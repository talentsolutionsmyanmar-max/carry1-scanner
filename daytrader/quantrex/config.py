"""Frozen Quantrex paper-system v0 parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuantrexConfig:
    schema_version: str = "quantrex-events-v1.0.0"
    qsr_v0_version: str = "qsr-day-v0.1.0"
    qsr_version: str = "QSR1_V1"
    breakout_version: str = "breakout-v0.1.0"
    random_version: str = "matched-random-v0.1.0"
    flat_version: str = "no-trade-v0.1.0"
    universe: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    interval: str = "15m"
    starting_equity_usd: float = 10_000.0
    risk_per_trade_fraction: float = 0.001
    max_aggregate_risk_fraction: float = 0.001
    max_daily_loss_fraction: float = 0.004
    max_consecutive_losses: int = 3
    max_entries_per_book_day: int = 3
    stale_after_seconds: float = 2.0
    atr_period: int = 14
    sweep_atr: float = 0.05
    stop_buffer_atr: float = 0.25
    min_stop_atr: float = 0.75
    max_stop_atr: float = 2.0
    target_r: float = 1.5
    qsr_exit_legs: tuple[tuple[str, float, float], ...] = (
        ("TP1", 1.0, 0.50),
        ("TP2", 1.5, 0.30),
        ("TP3", 2.0, 0.20),
    )
    qsr_hold_bars: int = 8
    breakout_hold_bars: int = 16
    breakout_lookback: int = 20
    qsr_body_lookback: int = 20
    qsr_outer_close_fraction: float = 0.25
    breakout_regime_ema: int = 48
    breakout_regime_slope_bars: int = 6
    context_adx_period: int = 14
    context_adx_block: float = 25.0
    context_ema_period: int = 20
    max_cost_stop_fraction: float = 0.25
    min_target_cost_multiple: float = 3.0
    sessions_utc: tuple[tuple[int, int], ...] = ((7, 11), (13, 17))

    def validate(self) -> None:
        if self.universe != ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            raise ValueError("Quantrex v0 universe is frozen")
        if self.risk_per_trade_fraction != 0.001:
            raise ValueError("Quantrex v0 risk is frozen at 0.10%")
        if self.max_aggregate_risk_fraction != 0.001:
            raise ValueError("Quantrex v0 permits one 0.10% risk position")
        if sum(fraction for _, _, fraction in self.qsr_exit_legs) != 1.0:
            raise ValueError("QSR1_V1 exit fractions must total 100%")
