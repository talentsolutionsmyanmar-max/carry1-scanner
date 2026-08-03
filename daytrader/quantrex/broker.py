"""Persistent deterministic paper broker for Quantrex strategy books."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from daytrader.market import Candle

from .config import QuantrexConfig
from .contracts import Book, LedgerEvent, RiskDecision, Side, SignalEvent, canonical_hash
from .replay import ReplayLedger
from .risk import RiskKernel, RiskState


@dataclass
class ExitLeg:
    name: str
    target: float
    original_fraction: float
    quantity: float
    status: str = "OPEN"
    fill_price: float | None = None
    fill_time: int | None = None


@dataclass
class PaperPosition:
    idempotency_key: str
    strategy_version: str
    book: str
    symbol: str
    side: str
    entry: float
    stop: float
    original_quantity: float
    remaining_quantity: float
    risk_usd: float
    modeled_cost_usd: float
    recognized_cost_usd: float
    opened_at: int
    time_exit: int
    legs: list[ExitLeg] = field(default_factory=list)
    status: str = "OPEN"
    realized_pnl_usd: float = 0.0


class PaperBroker:
    """One-position broker whose persisted event stream is the authority."""

    def __init__(self, config: QuantrexConfig, risk: RiskKernel | None = None):
        config.validate()
        self.config = config
        self.risk = risk or RiskKernel(config)
        self.ledger = ReplayLedger(config.schema_version)
        self.position: PaperPosition | None = None

    def open(self, signal: SignalEvent, decision: RiskDecision, timestamp: int) -> PaperPosition:
        if self.position and self.position.status == "OPEN":
            raise ValueError("one open position total in Quantrex v1")
        if not decision.accepted:
            raise ValueError("rejected decision cannot open a paper position")
        self.risk.register_intent(signal, decision)
        risk_distance = abs(signal.entry_quote - signal.stop)
        modeled_cost = decision.estimated_costs.round_trip_usd
        cost_per_unit = modeled_cost / decision.quantity if decision.quantity > 0 else 0.0
        legs: list[ExitLeg] = []
        if signal.book is Book.QSR and signal.strategy_version == self.config.qsr_version:
            for name, multiple, fraction in self.config.qsr_exit_legs:
                distance = multiple * (risk_distance + cost_per_unit) + cost_per_unit
                target = signal.entry_quote + distance if signal.side is Side.LONG else signal.entry_quote - distance
                legs.append(ExitLeg(name, target, fraction, decision.quantity * fraction))
        else:
            legs.append(ExitLeg("TP", signal.target, 1.0, decision.quantity))
        self.position = PaperPosition(
            idempotency_key=signal.idempotency_key,
            strategy_version=signal.strategy_version,
            book=signal.book.value,
            symbol=signal.symbol,
            side=signal.side.value,
            entry=signal.entry_quote,
            stop=signal.stop,
            original_quantity=decision.quantity,
            remaining_quantity=decision.quantity,
            risk_usd=decision.risk_usd,
            modeled_cost_usd=modeled_cost,
            recognized_cost_usd=0.0,
            opened_at=timestamp,
            time_exit=signal.time_exit,
            legs=legs,
        )
        self.ledger.append("INTENT", signal.idempotency_key, timestamp, {"signal": signal.idempotency_key})
        self.ledger.append("FILL_ENTRY", signal.idempotency_key, timestamp, {"price": signal.entry_quote, "quantity": decision.quantity})
        return self.position

    def _pnl(self, price: float, quantity: float) -> float:
        assert self.position
        direction = 1.0 if self.position.side == Side.LONG.value else -1.0
        return (price - self.position.entry) * direction * quantity

    def _fill(self, leg: ExitLeg, price: float, timestamp: int, event_type: str) -> None:
        assert self.position
        leg.status = "FILLED"
        leg.fill_price = price
        leg.fill_time = timestamp
        quantity = min(leg.quantity, self.position.remaining_quantity)
        self.position.remaining_quantity = max(0.0, self.position.remaining_quantity - quantity)
        cost = self.position.modeled_cost_usd * quantity / self.position.original_quantity
        pnl = self._pnl(price, quantity) - cost
        self.position.recognized_cost_usd += cost
        self.position.realized_pnl_usd += pnl
        self.ledger.append(event_type, self.position.idempotency_key, timestamp, {"leg": leg.name, "price": price, "quantity": quantity, "modeled_cost_usd": cost, "net_pnl_usd": pnl})

    def process_bar(self, bar: Candle) -> tuple[str, ...]:
        """Apply gap/stop before targets; a stop-target collision always stops first."""
        position = self.position
        if not position or position.status != "OPEN":
            return ()
        long = position.side == Side.LONG.value
        stop_hit = bar.low <= position.stop if long else bar.high >= position.stop
        open_through_stop = bar.open <= position.stop if long else bar.open >= position.stop
        if stop_hit:
            price = bar.open if open_through_stop else position.stop
            remaining = position.remaining_quantity
            if remaining > 0:
                stop_leg = ExitLeg("SL", position.stop, remaining / position.original_quantity, remaining)
                self._fill(stop_leg, price, bar.close_time, "FILL_STOP")
            for leg in position.legs:
                if leg.status == "OPEN":
                    leg.status = "CANCELLED_BY_STOP"
                    self.ledger.append("NO_FILL", position.idempotency_key, bar.close_time, {"leg": leg.name, "reason": "STOP_FIRST"})
            self._close("STOPPED", bar.close_time)
            return ("SL",)
        filled: list[str] = []
        for leg in position.legs:
            if leg.status != "OPEN":
                continue
            hit = bar.high >= leg.target if long else bar.low <= leg.target
            if hit:
                self._fill(leg, leg.target, bar.close_time, "FILL_TARGET")
                filled.append(leg.name)
        if position.remaining_quantity <= 1e-12:
            self._close("TARGETS_COMPLETE", bar.close_time)
        elif bar.close_time >= position.time_exit:
            remaining = position.remaining_quantity
            time_leg = ExitLeg("TIME", bar.close, remaining / position.original_quantity, remaining)
            self._fill(time_leg, bar.close, bar.close_time, "FILL_TIME_EXIT")
            for leg in position.legs:
                if leg.status == "OPEN":
                    leg.status = "CANCELLED_BY_TIME"
                    self.ledger.append("NO_FILL", position.idempotency_key, bar.close_time, {"leg": leg.name, "reason": "TIME_EXIT"})
            self._close("TIME_EXIT", bar.close_time)
            filled.append("TIME")
        return tuple(filled)

    def _close(self, status: str, timestamp: int) -> None:
        assert self.position
        self.position.status = status
        self.risk.state.open_risk_usd = 0.0
        self.risk.state.equity_usd += self.position.realized_pnl_usd
        if self.position.realized_pnl_usd < 0:
            self.risk.state.consecutive_losses += 1
        else:
            self.risk.state.consecutive_losses = 0
        self.ledger.append("POSITION_CLOSED", self.position.idempotency_key, timestamp, {"status": status, "pnl_usd": self.position.realized_pnl_usd})

    def snapshot(self) -> dict:
        payload = {
            "schema_version": self.config.schema_version,
            "risk": {
                **asdict(self.risk.state),
                "seen_intents": sorted(self.risk.state.seen_intents),
            },
            "position": asdict(self.position) if self.position else None,
            "events": [event.to_dict() for event in self.ledger.events],
            "ledger_hash": self.ledger.output_hash,
        }
        payload["snapshot_hash"] = canonical_hash(payload)
        return payload

    def save(self, path: Path) -> None:
        payload = self.snapshot()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    @classmethod
    def load(cls, config: QuantrexConfig, path: Path) -> "PaperBroker":
        payload = json.loads(path.read_text(encoding="utf-8"))
        claimed_snapshot_hash = payload.pop("snapshot_hash", None)
        if claimed_snapshot_hash != canonical_hash(payload):
            raise ValueError("paper snapshot hash mismatch")
        if payload.get("schema_version") != config.schema_version:
            raise ValueError("paper snapshot schema mismatch")
        risk_payload = dict(payload["risk"])
        risk_payload["seen_intents"] = set(risk_payload.get("seen_intents", ()))
        broker = cls(config, RiskKernel(config, RiskState(**risk_payload)))
        event_rows = payload.get("events", [])
        broker.ledger.events = [LedgerEvent(**row) for row in event_rows]
        broker.ledger.keys = {
            event.idempotency_key for event in broker.ledger.events if event.event_type == "INTENT"
        }
        if not broker.ledger.reconcile() or broker.ledger.output_hash != payload.get("ledger_hash"):
            broker.risk.state.reconciliation_ok = False
            raise ValueError("paper ledger reconciliation mismatch")
        position_payload = payload.get("position")
        if position_payload:
            position_payload = dict(position_payload)
            position_payload["legs"] = [ExitLeg(**leg) for leg in position_payload.get("legs", [])]
            broker.position = PaperPosition(**position_payload)
            expected_remaining = sum(
                leg.quantity for leg in broker.position.legs if leg.status == "OPEN"
            )
            if abs(expected_remaining - broker.position.remaining_quantity) > 1e-9:
                broker.risk.state.reconciliation_ok = False
                raise ValueError("paper position quantity mismatch")
        return broker
