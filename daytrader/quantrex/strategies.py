"""Frozen QSR-1 DAY, Breakout v0, and null-control signal plug-ins."""

from __future__ import annotations

import hashlib
import statistics
from datetime import datetime, timezone

from daytrader.engine import adx, atr, ema
from daytrader.market import Candle

from .config import QuantrexConfig
from .contracts import Book, Side, SignalEvent


BAR_MS = 15 * 60 * 1000


def _session(close_time: int, config: QuantrexConfig) -> str | None:
    hour = datetime.fromtimestamp(close_time / 1000, timezone.utc).hour
    labels = ("LONDON", "NEW_YORK")
    for label, (start, end) in zip(labels, config.sessions_utc):
        if start <= hour < end:
            return label
    return None


def _shifted_atr(bars: tuple[Candle, ...], config: QuantrexConfig) -> float | None:
    values = atr(bars, config.atr_period)
    return values[-2] if len(values) >= 2 else None


def _previous_day_levels(bars: tuple[Candle, ...]) -> tuple[float, float] | None:
    signal_day = datetime.fromtimestamp(bars[-1].close_time / 1000, timezone.utc).date()
    prior = [
        bar
        for bar in bars[:-1]
        if datetime.fromtimestamp(bar.close_time / 1000, timezone.utc).date() < signal_day
    ]
    if not prior:
        return None
    previous_day = max(
        datetime.fromtimestamp(bar.close_time / 1000, timezone.utc).date()
        for bar in prior
    )
    completed = [
        bar
        for bar in prior
        if datetime.fromtimestamp(bar.close_time / 1000, timezone.utc).date()
        == previous_day
    ]
    return max(bar.high for bar in completed), min(bar.low for bar in completed)


def _context_blocks(
    side: Side, hourly: tuple[Candle, ...], config: QuantrexConfig
) -> bool:
    if len(hourly) < max(config.context_ema_period + 2, config.context_adx_period * 2 + 1):
        return True
    strength = adx(hourly, config.context_adx_period)
    averages = ema([bar.close for bar in hourly], config.context_ema_period)
    if strength is None or len(averages) < 2:
        return True
    slope = averages[-1] - averages[-2]
    price = hourly[-1].close
    if side is Side.LONG:
        return strength >= config.context_adx_block and slope < 0 and price < averages[-1]
    return strength >= config.context_adx_block and slope > 0 and price > averages[-1]


def _signal(
    *,
    config: QuantrexConfig,
    version: str,
    book: Book,
    symbol: str,
    side: Side,
    current: Candle,
    reference: str,
    entry: float,
    stop: float,
    atr_value: float,
    hold_bars: int,
    session: str,
    evidence: tuple[str, ...],
    estimated_cost_per_unit: float = 0.0,
) -> SignalEvent:
    risk_per_unit = abs(entry - stop)
    target_distance = config.target_r * risk_per_unit + estimated_cost_per_unit
    target = entry + target_distance if side is Side.LONG else entry - target_distance
    return SignalEvent(
        schema_version=config.schema_version,
        strategy_version=version,
        book=book,
        symbol=symbol,
        side=side,
        signal_close_time=current.close_time,
        reference_level=reference,
        entry_quote=entry,
        stop=stop,
        target=target,
        time_exit=current.close_time + hold_bars * BAR_MS,
        atr=atr_value,
        session_arm=session,
        evidence=evidence,
    )


def qsr_signal(
    symbol: str,
    bars: tuple[Candle, ...],
    hourly: tuple[Candle, ...],
    bid: float,
    ask: float,
    config: QuantrexConfig,
    *,
    strategy_version: str | None = None,
) -> SignalEvent | None:
    required = max(config.qsr_body_lookback + 2, config.atr_period + 3)
    if len(bars) < required:
        return None
    session = _session(bars[-1].close_time, config)
    levels = _previous_day_levels(bars)
    atr_value = _shifted_atr(bars, config)
    if session is None or levels is None or not atr_value or atr_value <= 0:
        return None
    current = bars[-1]
    previous_high, previous_low = levels
    body = abs(current.close - current.open)
    prior_bodies = [abs(bar.close - bar.open) for bar in bars[-21:-1]]
    if body < statistics.median(prior_bodies):
        return None
    bar_range = max(current.high - current.low, 1e-12)
    location = (current.close - current.low) / bar_range
    long_sweep = (
        previous_low - current.low >= config.sweep_atr * atr_value
        and current.close > previous_low
        and location >= 1.0 - config.qsr_outer_close_fraction
    )
    short_sweep = (
        current.high - previous_high >= config.sweep_atr * atr_value
        and current.close < previous_high
        and location <= config.qsr_outer_close_fraction
    )
    if long_sweep and not _context_blocks(Side.LONG, hourly, config):
        stop = current.low - config.stop_buffer_atr * atr_value
        distance = ask - stop
        if config.min_stop_atr * atr_value <= distance <= config.max_stop_atr * atr_value:
            return _signal(
                config=config,
                version=strategy_version or config.qsr_version,
                book=Book.QSR,
                symbol=symbol,
                side=Side.LONG,
                current=current,
                reference=f"PDL:{previous_low:.12g}",
                entry=ask,
                stop=stop,
                atr_value=atr_value,
                hold_bars=config.qsr_hold_bars,
                session=session,
                evidence=("previous completed UTC-day low swept and reclaimed", "body gate passed"),
            )
    if short_sweep and not _context_blocks(Side.SHORT, hourly, config):
        stop = current.high + config.stop_buffer_atr * atr_value
        distance = stop - bid
        if config.min_stop_atr * atr_value <= distance <= config.max_stop_atr * atr_value:
            return _signal(
                config=config,
                version=strategy_version or config.qsr_version,
                book=Book.QSR,
                symbol=symbol,
                side=Side.SHORT,
                current=current,
                reference=f"PDH:{previous_high:.12g}",
                entry=bid,
                stop=stop,
                atr_value=atr_value,
                hold_bars=config.qsr_hold_bars,
                session=session,
                evidence=("previous completed UTC-day high swept and reclaimed", "body gate passed"),
            )
    return None


def qsr_v0_signal(
    symbol: str,
    bars: tuple[Candle, ...],
    hourly: tuple[Candle, ...],
    bid: float,
    ask: float,
    config: QuantrexConfig,
) -> SignalEvent | None:
    """Frozen single-exit reference book retained beside QSR1_V1."""
    return qsr_signal(
        symbol,
        bars,
        hourly,
        bid,
        ask,
        config,
        strategy_version=config.qsr_v0_version,
    )


def breakout_signal(
    symbol: str,
    bars: tuple[Candle, ...],
    hourly: tuple[Candle, ...],
    bid: float,
    ask: float,
    config: QuantrexConfig,
) -> SignalEvent | None:
    if len(bars) < config.breakout_lookback + config.atr_period + 3:
        return None
    if len(hourly) < config.breakout_regime_ema + config.breakout_regime_slope_bars + 1:
        return None
    session = _session(bars[-1].close_time, config)
    atr_value = _shifted_atr(bars, config)
    if session is None or not atr_value or atr_value <= 0:
        return None
    current = bars[-1]
    prior = bars[-config.breakout_lookback - 1 : -1]
    ceiling = max(bar.high for bar in prior)
    floor = min(bar.low for bar in prior)
    averages = ema([bar.close for bar in hourly], config.breakout_regime_ema)
    slope = averages[-1] - averages[-1 - config.breakout_regime_slope_bars]
    if slope > 0 and current.close - ceiling >= config.sweep_atr * atr_value:
        return _signal(
            config=config,
            version=config.breakout_version,
            book=Book.BREAKOUT,
            symbol=symbol,
            side=Side.LONG,
            current=current,
            reference=f"HIGH20:{ceiling:.12g}",
            entry=ask,
            stop=ask - atr_value,
            atr_value=atr_value,
            hold_bars=config.breakout_hold_bars,
            session=session,
            evidence=("20-bar close breakout", "completed 1h EMA48 regime up"),
        )
    if slope < 0 and floor - current.close >= config.sweep_atr * atr_value:
        return _signal(
            config=config,
            version=config.breakout_version,
            book=Book.BREAKOUT,
            symbol=symbol,
            side=Side.SHORT,
            current=current,
            reference=f"LOW20:{floor:.12g}",
            entry=bid,
            stop=bid + atr_value,
            atr_value=atr_value,
            hold_bars=config.breakout_hold_bars,
            session=session,
            evidence=("20-bar close breakout", "completed 1h EMA48 regime down"),
        )
    return None


def matched_random_signal(
    template: SignalEvent,
    eligible_bars: tuple[Candle, ...],
    bid: float,
    ask: float,
    config: QuantrexConfig,
) -> SignalEvent | None:
    eligible = [bar for bar in eligible_bars if _session(bar.close_time, config)]
    if not eligible:
        return None
    digest = hashlib.sha256(template.idempotency_key.encode()).digest()
    current = eligible[int.from_bytes(digest[:8], "big") % len(eligible)]
    side = Side.LONG if digest[8] % 2 == 0 else Side.SHORT
    entry = ask if side is Side.LONG else bid
    stop = entry - template.atr if side is Side.LONG else entry + template.atr
    return _signal(
        config=config,
        version=config.random_version,
        book=Book.MATCHED_RANDOM,
        symbol=template.symbol,
        side=side,
        current=current,
        reference=f"RANDOM:{template.signal_close_time}",
        entry=entry,
        stop=stop,
        atr_value=template.atr,
        hold_bars=config.qsr_hold_bars,
        session=_session(current.close_time, config) or "NONE",
        evidence=("deterministic matched random timing null",),
    )
