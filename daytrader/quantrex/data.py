"""Fail-closed USD-M candle and quote validation."""

from __future__ import annotations

from datetime import datetime, timezone

from daytrader.market import Candle, MarketSnapshot

from .config import QuantrexConfig
from .contracts import canonical_hash


FIFTEEN_MINUTES_MS = 15 * 60 * 1000


class DataContractError(ValueError):
    pass


def parse_capture_ms(value: str) -> int:
    captured = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise DataContractError("captured_at must include timezone")
    return int(captured.astimezone(timezone.utc).timestamp() * 1000)


def validate_candles(candles: tuple[Candle, ...], observed_at_ms: int) -> None:
    if not candles:
        raise DataContractError("closed 15m candles required")
    previous_close = -1
    for candle in candles:
        if candle.close_time >= observed_at_ms:
            raise DataContractError("forming/current candle rejected")
        if candle.close_time <= previous_close:
            raise DataContractError("candles must be strictly chronological")
        if candle.close_time - candle.open_time + 1 != FIFTEEN_MINUTES_MS:
            raise DataContractError("only complete 15m candles accepted")
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            raise DataContractError("non-positive OHLC rejected")
        if candle.high < max(candle.open, candle.close) or candle.low > min(
            candle.open, candle.close
        ):
            raise DataContractError("invalid OHLC bounds")
        previous_close = candle.close_time


def validate_snapshot(
    snapshot: MarketSnapshot,
    config: QuantrexConfig,
    observed_at_ms: int,
) -> str:
    config.validate()
    if snapshot.symbol not in config.universe:
        raise DataContractError("symbol outside frozen USD-M universe")
    if snapshot.bid <= 0 or snapshot.ask <= snapshot.bid:
        raise DataContractError("valid bid/ask required")
    captured_ms = parse_capture_ms(snapshot.captured_at)
    age_ms = observed_at_ms - captured_ms
    if age_ms < 0 or age_ms > config.stale_after_seconds * 1000:
        raise DataContractError("quote/book feed stale")
    validate_candles(snapshot.trend, observed_at_ms)
    return canonical_hash(
        {
            "symbol": snapshot.symbol,
            "candles": [bar.to_dict() for bar in snapshot.trend],
            "bid": snapshot.bid,
            "ask": snapshot.ask,
            "mark": snapshot.mark,
            "captured_at": snapshot.captured_at,
        }
    )
