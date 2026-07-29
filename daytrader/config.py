"""Environment-backed configuration for the intraday scanner."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """All defaults are deliberately conservative and paper-only."""

    api_base: str = "https://fapi.binance.com"
    universe_size: int = 12
    min_listing_days: int = 30
    max_abs_24h_change_pct: float = 25.0
    scan_interval_seconds: int = 60
    universe_refresh_seconds: int = 3600
    request_timeout_seconds: int = 12
    request_retries: int = 3
    max_workers: int = 4

    primary_interval: str = "5m"
    trend_interval: str = "15m"
    candle_limit: int = 180
    breakout_lookback: int = 20
    volume_lookback: int = 20
    min_volume_ratio: float = 1.20
    signal_score: int = 75
    watch_score: int = 55
    long_rsi_min: float = 52.0
    long_rsi_max: float = 78.0
    short_rsi_min: float = 22.0
    short_rsi_max: float = 48.0
    max_spread_bp: float = 8.0
    min_atr_pct: float = 0.12
    max_atr_pct: float = 3.00
    max_entry_drift_atr: float = 0.50
    stop_atr: float = 1.25
    min_reward_cost_multiple: float = 3.0

    starting_equity_usd: float = 10_000.0
    risk_per_trade_pct: float = 0.25
    max_daily_loss_pct: float = 1.00
    max_open_positions: int = 2
    max_trades_per_day: int = 4
    max_notional_pct: float = 50.0
    max_hold_minutes: int = 180
    cooldown_minutes: int = 30
    fee_per_side_bp: float = 5.0
    slippage_per_side_bp: float = 2.0
    auto_paper: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_base=os.environ.get("BINANCE_FUTURES_BASE", cls.api_base),
            universe_size=_env_int("DAY_UNIVERSE_SIZE", cls.universe_size),
            min_listing_days=_env_int("DAY_MIN_LISTING_DAYS", cls.min_listing_days),
            scan_interval_seconds=_env_int("DAY_SCAN_SECONDS", cls.scan_interval_seconds),
            max_workers=_env_int("DAY_MAX_WORKERS", cls.max_workers),
            starting_equity_usd=_env_float("DAY_STARTING_EQUITY", cls.starting_equity_usd),
            risk_per_trade_pct=_env_float("DAY_RISK_PER_TRADE_PCT", cls.risk_per_trade_pct),
            max_daily_loss_pct=_env_float("DAY_MAX_DAILY_LOSS_PCT", cls.max_daily_loss_pct),
            max_open_positions=_env_int("DAY_MAX_OPEN_POSITIONS", cls.max_open_positions),
            max_trades_per_day=_env_int("DAY_MAX_TRADES", cls.max_trades_per_day),
            max_hold_minutes=_env_int("DAY_MAX_HOLD_MINUTES", cls.max_hold_minutes),
            fee_per_side_bp=_env_float("DAY_FEE_PER_SIDE_BP", cls.fee_per_side_bp),
            slippage_per_side_bp=_env_float(
                "DAY_SLIPPAGE_PER_SIDE_BP", cls.slippage_per_side_bp
            ),
            auto_paper=_env_bool("DAY_AUTO_PAPER", cls.auto_paper),
        )

    @property
    def risk_per_trade_fraction(self) -> float:
        return self.risk_per_trade_pct / 100.0

    @property
    def max_daily_loss_fraction(self) -> float:
        return self.max_daily_loss_pct / 100.0

    @property
    def max_notional_fraction(self) -> float:
        return self.max_notional_pct / 100.0

    @property
    def fixed_round_trip_cost_bp(self) -> float:
        return 2.0 * (self.fee_per_side_bp + self.slippage_per_side_bp)

    def public_dict(self) -> dict:
        visible = asdict(self)
        visible.pop("api_base", None)
        visible.pop("request_retries", None)
        visible.pop("request_timeout_seconds", None)
        return visible
