"""Frozen Quantrex v0 risk and cost gate."""

from __future__ import annotations

import math
from dataclasses import asdict
from dataclasses import dataclass, field

from .config import QuantrexConfig
from .contracts import CostEstimate, RiskDecision, SignalEvent


@dataclass
class RiskState:
    equity_usd: float = 10_000.0
    day_start_equity_usd: float = 10_000.0
    open_risk_usd: float = 0.0
    consecutive_losses: int = 0
    entries_by_book: dict[str, int] = field(default_factory=dict)
    kill_switch: bool = False
    reconciliation_ok: bool = True
    seen_intents: set[str] = field(default_factory=set)
    current_utc_day: str | None = None


class RiskKernel:
    def __init__(self, config: QuantrexConfig, state: RiskState | None = None):
        config.validate()
        self.config = config
        self.state = state or RiskState(
            equity_usd=config.starting_equity_usd,
            day_start_equity_usd=config.starting_equity_usd,
        )

    def roll_utc_day(self, utc_day: str) -> None:
        """Reset day-scoped counters; persistent kill/reconciliation halts remain."""
        if self.state.current_utc_day == utc_day:
            return
        self.state.current_utc_day = utc_day
        self.state.day_start_equity_usd = self.state.equity_usd
        self.state.entries_by_book = {}

    def set_kill_switch(self, active: bool) -> None:
        self.state.kill_switch = bool(active)

    def decide(
        self,
        signal: SignalEvent,
        costs: CostEstimate,
        *,
        depth_available: bool,
        feed_age_seconds: float,
    ) -> RiskDecision:
        reasons: list[str] = []
        stop_distance = abs(signal.entry_quote - signal.stop)
        target_distance = abs(signal.target - signal.entry_quote)
        risk_budget = self.state.equity_usd * self.config.risk_per_trade_fraction
        provisional_quantity = risk_budget / stop_distance if stop_distance > 0 else 0.0
        cost_per_unit = costs.round_trip_usd / provisional_quantity if provisional_quantity > 0 else float("inf")
        quantity = risk_budget / (stop_distance + cost_per_unit) if stop_distance > 0 and math.isfinite(cost_per_unit) else 0.0
        scale = quantity / provisional_quantity if provisional_quantity > 0 else 0.0
        scaled_costs = CostEstimate(**{name: value * scale for name, value in asdict(costs).items()})
        if self.state.kill_switch:
            reasons.append("manual kill switch active")
        if not self.state.reconciliation_ok:
            reasons.append("ledger reconciliation mismatch")
        if signal.idempotency_key in self.state.seen_intents:
            reasons.append("duplicate intent")
        daily_loss = self.state.day_start_equity_usd - self.state.equity_usd
        if daily_loss >= self.state.day_start_equity_usd * self.config.max_daily_loss_fraction:
            reasons.append("daily loss halt requires manual restart")
        if self.state.consecutive_losses >= self.config.max_consecutive_losses:
            reasons.append("three-loss halt requires manual restart")
        if self.state.entries_by_book.get(signal.book.value, 0) >= self.config.max_entries_per_book_day:
            reasons.append("book daily entry cap reached")
        max_open = self.state.equity_usd * self.config.max_aggregate_risk_fraction
        if self.state.open_risk_usd + risk_budget > max_open + 1e-9:
            reasons.append("aggregate open-risk cap reached")
        if feed_age_seconds > self.config.stale_after_seconds:
            reasons.append("quote/book feed stale")
        if not depth_available:
            reasons.append("required depth unavailable")
        if stop_distance <= 0:
            reasons.append("invalid stop distance")
        elif cost_per_unit > stop_distance * self.config.max_cost_stop_fraction:
            reasons.append("round-trip cost exceeds 25% of stop distance")
        if target_distance < cost_per_unit * self.config.min_target_cost_multiple:
            reasons.append("net target below 3x round-trip cost")
        return RiskDecision(
            schema_version=self.config.schema_version,
            idempotency_key=signal.idempotency_key,
            accepted=not reasons,
            reasons=tuple(reasons),
            equity_usd=self.state.equity_usd,
            risk_usd=risk_budget,
            quantity=quantity,
            estimated_costs=scaled_costs,
        )

    def register_intent(self, signal: SignalEvent, decision: RiskDecision) -> None:
        if not decision.accepted:
            raise ValueError("rejected decision cannot register intent")
        if signal.idempotency_key in self.state.seen_intents:
            self.state.reconciliation_ok = False
            raise ValueError("duplicate intent")
        self.state.seen_intents.add(signal.idempotency_key)
        self.state.open_risk_usd += decision.risk_usd
        book = signal.book.value
        self.state.entries_by_book[book] = self.state.entries_by_book.get(book, 0) + 1
