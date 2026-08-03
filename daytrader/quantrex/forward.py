"""Persistent forward-paper coordinator over public Quantrex evaluations."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from daytrader.market import MarketSnapshot

from .broker import PaperBroker
from .config import QuantrexConfig
from .contracts import Book, CostEstimate, Side, SignalEvent


class ForwardPaperRunner:
    """Record every candidate/skip and open only an unambiguous accepted intent.

    The runner has no venue client. It consumes the same public evaluations as
    the dashboard and persists the deterministic paper broker after every new
    signal or bar transition.
    """

    def __init__(self, config: QuantrexConfig, state_path: Path):
        self.config = config
        self.state_path = state_path
        self.broker = PaperBroker.load(config, state_path) if state_path.exists() else PaperBroker(config)
        self.seen_signals = {
            event.idempotency_key
            for event in self.broker.ledger.events
            if event.event_type == "SIGNAL"
        }
        self.processed_bars = {
            (event.payload.get("symbol"), int(event.payload.get("close_time", 0)))
            for event in self.broker.ledger.events
            if event.event_type == "BAR_PROCESSED"
        }

    @staticmethod
    def _signal(row: dict) -> SignalEvent:
        payload = dict(row["signal_event"])
        payload["book"] = Book(payload["book"])
        payload["side"] = Side(payload["side"])
        payload["evidence"] = tuple(payload.get("evidence", ()))
        return SignalEvent(**payload)

    @staticmethod
    def _costs(row: dict) -> CostEstimate:
        payload = dict(row["costs"])
        payload.pop("round_trip_usd", None)
        return CostEstimate(**payload)

    def consume(self, evaluations: list[dict], snapshots: dict[str, MarketSnapshot]) -> dict:
        changed = False
        position = self.broker.position
        if position and position.status == "OPEN" and position.symbol in snapshots:
            bar = snapshots[position.symbol].trend[-1]
            bar_key = (position.symbol, bar.close_time)
            if bar_key not in self.processed_bars and bar.close_time > position.opened_at:
                self.broker.process_bar(bar)
                self.broker.ledger.append(
                    "BAR_PROCESSED",
                    position.idempotency_key,
                    bar.close_time,
                    {"symbol": position.symbol, "close_time": bar.close_time},
                )
                self.processed_bars.add(bar_key)
                changed = True

        new_rows: list[dict] = []
        for evaluation in evaluations:
            for row in evaluation.get("candidates", []):
                key = str(row["idempotency_key"])
                if key in self.seen_signals:
                    continue
                signal = self._signal(row)
                self.broker.ledger.append(
                    "SIGNAL",
                    key,
                    signal.signal_close_time,
                    {"book": signal.book.value, "symbol": signal.symbol, "state": row["state"]},
                )
                self.seen_signals.add(key)
                new_rows.append(row)
                changed = True

        accepted: list[tuple[dict, SignalEvent, object]] = []
        for row in new_rows:
            signal = self._signal(row)
            signal_day = datetime.fromtimestamp(signal.signal_close_time / 1000, timezone.utc).date().isoformat()
            self.broker.risk.roll_utc_day(signal_day)
            decision = self.broker.risk.decide(
                signal,
                self._costs(row),
                depth_available=bool(row.get("depth_available")),
                feed_age_seconds=float(row.get("feed_age_seconds", float("inf"))),
            )
            self.broker.ledger.append(
                "RISK_DECISION",
                signal.idempotency_key,
                signal.signal_close_time,
                {"accepted": decision.accepted, "reasons": list(decision.reasons)},
            )
            if decision.accepted:
                accepted.append((row, signal, decision))

        if len(accepted) == 1 and not (self.broker.position and self.broker.position.status == "OPEN"):
            _row, signal, decision = accepted[0]
            self.broker.open(signal, decision, signal.signal_close_time + 1)
            changed = True
        elif len(accepted) > 1:
            for _row, signal, _decision in accepted:
                self.broker.ledger.append(
                    "NO_FILL",
                    signal.idempotency_key,
                    signal.signal_close_time + 1,
                    {"reason": "AMBIGUOUS_SIMULTANEOUS_CANDIDATES"},
                )
            changed = True

        if changed:
            self.broker.save(self.state_path)
        return self.snapshot()

    def snapshot(self) -> dict:
        broker = self.broker.snapshot()
        events = broker["events"]
        return {
            "mode": "FORWARD_PAPER_NO_SUBMIT",
            "no_submit": True,
            "signals": sum(event["event_type"] == "SIGNAL" for event in events),
            "risk_rejections": sum(event["event_type"] == "RISK_DECISION" and not event["payload"].get("accepted") for event in events),
            "fills": sum(event["event_type"].startswith("FILL_") for event in events),
            "ledger_hash": broker["ledger_hash"],
            "snapshot_hash": broker["snapshot_hash"],
            "broker": broker,
        }

    def set_kill_switch(self, active: bool) -> dict:
        self.broker.risk.set_kill_switch(active)
        self.broker.ledger.append(
            "KILL_SWITCH",
            "operator-kill-switch",
            int(datetime.now(timezone.utc).timestamp() * 1000),
            {"active": bool(active)},
        )
        self.broker.save(self.state_path)
        return self.snapshot()
