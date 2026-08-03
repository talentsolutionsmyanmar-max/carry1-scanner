"""Versioned, deterministic event contracts shared by every Quantrex book."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Book(str, Enum):
    QSR = "QSR_1_DAY"
    BREAKOUT = "BREAKOUT_V0"
    MATCHED_RANDOM = "MATCHED_RANDOM"
    NO_TRADE = "NO_TRADE"


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class CostEstimate:
    fee_usd: float
    spread_usd: float
    impact_usd: float
    slippage_usd: float
    funding_usd: float = 0.0
    latency_usd: float = 0.0
    missed_fill_usd: float = 0.0

    @property
    def round_trip_usd(self) -> float:
        return sum(asdict(self).values())


@dataclass(frozen=True)
class SignalEvent:
    schema_version: str
    strategy_version: str
    book: Book
    symbol: str
    side: Side
    signal_close_time: int
    reference_level: str
    entry_quote: float
    stop: float
    target: float
    time_exit: int
    atr: float
    session_arm: str
    evidence: tuple[str, ...] = ()

    @property
    def idempotency_key(self) -> str:
        return ":".join(
            (
                self.strategy_version,
                self.symbol,
                self.reference_level,
                str(self.signal_close_time),
            )
        )


@dataclass(frozen=True)
class RiskDecision:
    schema_version: str
    idempotency_key: str
    accepted: bool
    reasons: tuple[str, ...]
    equity_usd: float
    risk_usd: float
    quantity: float
    estimated_costs: CostEstimate


@dataclass(frozen=True)
class LedgerEvent:
    schema_version: str
    sequence: int
    event_type: str
    idempotency_key: str
    timestamp: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
