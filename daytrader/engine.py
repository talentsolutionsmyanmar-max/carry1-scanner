"""Multi-timeframe intraday decision engine and risk-sized paper tickets.

The engine deliberately separates an actionable conditional plan (ARMED) from a
closed-candle setup that has cleared every gate (LIVE). It never places orders.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

from .config import Config
from .market import Candle, MarketSnapshot


def ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = [max(0.0, values[i] - values[i - 1]) for i in range(1, len(values))]
    losses = [max(0.0, values[i - 1] - values[i]) for i in range(1, len(values))]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value() -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    out[period] = value()
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = value()
    return out


def atr(candles: tuple[Candle, ...], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return out
    ranges: list[float] = []
    for i, candle in enumerate(candles):
        prev_close = candles[i - 1].close if i else candle.close
        ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - prev_close),
                abs(candle.low - prev_close),
            )
        )
    current = sum(ranges[1 : period + 1]) / period
    out[period] = current
    for i in range(period + 1, len(candles)):
        current = (current * (period - 1) + ranges[i]) / period
        out[i] = current
    return out


def macd(values: list[float]) -> tuple[float, float, float]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    line = [a - b for a, b in zip(fast, slow)]
    signal = ema(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def stoch_rsi(values: list[float], period: int = 14) -> float | None:
    series = [value for value in rsi(values, period) if value is not None]
    if len(series) < period:
        return None
    window = series[-period:]
    floor, ceiling = min(window), max(window)
    return 50.0 if ceiling == floor else (window[-1] - floor) / (ceiling - floor) * 100.0


def adx(candles: tuple[Candle, ...], period: int = 14) -> float | None:
    if len(candles) < period * 2 + 1:
        return None
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, len(candles)):
        current, previous = candles[i], candles[i - 1]
        up = current.high - previous.high
        down = previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    dx: list[float] = []
    for end in range(period, len(true_ranges) + 1):
        tr = sum(true_ranges[end - period : end])
        if tr <= 0:
            continue
        plus = 100.0 * sum(plus_dm[end - period : end]) / tr
        minus = 100.0 * sum(minus_dm[end - period : end]) / tr
        dx.append(100.0 * abs(plus - minus) / max(plus + minus, 1e-12))
    return statistics.mean(dx[-period:]) if len(dx) >= period else None


def session_vwap(candles: tuple[Candle, ...]) -> float:
    session = datetime.fromtimestamp(candles[-1].open_time / 1000, timezone.utc).date()
    bars = [
        bar
        for bar in candles
        if datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).date() == session
    ]
    volume = sum(bar.volume for bar in bars)
    if volume <= 0:
        return candles[-1].close
    return sum(((bar.high + bar.low + bar.close) / 3.0) * bar.volume for bar in bars) / volume


def kill_zone(now: datetime) -> str:
    hour = now.astimezone(timezone.utc).hour
    if 0 <= hour < 4:
        return "ASIA"
    if 8 <= hour < 12:
        return "LONDON"
    if 12 <= hour < 16:
        return "NEW_YORK"
    return "OFF_HOURS"


@dataclass(frozen=True)
class Signal:
    symbol: str
    state: str
    side: str | None
    score: int
    action: str
    playbook: str = "MOMENTUM_BREAKOUT"
    playbook_label: str = "PLAYBOOK A · MOMENTUM"
    playbook_confirmation: str = "SINGLE"
    confidence: str = "—"
    signal_id: str | None = None
    candle_close_time: int | None = None
    entry: float | None = None
    entry_trigger: float | None = None
    stop: float | None = None
    spread_bp: float | None = None
    friction_bp: float | None = None
    friction_stop_pct: float | None = None
    stop_distance_bp: float | None = None
    atr: float | None = None
    atr_pct: float | None = None
    rsi_5m: float | None = None
    stoch_rsi_5m: float | None = None
    macd_hist_5m: float | None = None
    adx_5m: float | None = None
    vwap: float | None = None
    bollinger_position: float | None = None
    volume_ratio: float | None = None
    breakout_level: float | None = None
    liquidity_level_name: str | None = None
    liquidity_level: float | None = None
    sweep_price: float | None = None
    mss_level: float | None = None
    displacement_ratio: float | None = None
    fvg_low: float | None = None
    fvg_high: float | None = None
    fvg_mid: float | None = None
    dealing_range_mid: float | None = None
    premium_discount: str | None = None
    higher_trend: str | None = None
    trend_15m: str | None = None
    kill_zone: str | None = None
    funding_bp: float | None = None
    derivatives_verdict: str = "UNAVAILABLE"
    derivatives_available: bool = False
    open_interest_usd: float | None = None
    open_interest_change_15m_pct: float | None = None
    open_interest_change_1h_pct: float | None = None
    taker_buy_sell_ratio_15m: float | None = None
    long_short_account_ratio: float | None = None
    leverage_risk: str = "UNKNOWN"
    derivatives_reasons: tuple[str, ...] = ()
    price_change_pct_24h: float | None = None
    quote_volume_24h: float | None = None
    expires_at: str | None = None
    invalidation: str | None = None
    reasons: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    ticket: dict | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        data["blocked_by"] = list(self.blocked_by)
        data["derivatives_reasons"] = list(self.derivatives_reasons)
        return data


def derivatives_context(
    snapshot: MarketSnapshot,
    side: str,
    price_change_15m_pct: float,
) -> dict:
    """Classify derivatives positioning without claiming liquidation levels.

    The verdict is deliberately separate from the 100-point technical score.
    Only an explicit two-factor conflict can veto an actionable ticket.
    """
    oi_15m = snapshot.open_interest_change_15m_pct
    oi_1h = snapshot.open_interest_change_1h_pct
    taker = snapshot.taker_buy_sell_ratio_15m
    accounts = snapshot.long_short_account_ratio
    funding = snapshot.funding_bp
    available = all(value is not None for value in (oi_15m, taker, accounts))
    supports: list[str] = []
    conflicts: list[str] = []

    if side == "LONG":
        if taker is not None and taker >= 1.08:
            supports.append(f"15m taker buying {taker:.2f}× sell volume")
        elif taker is not None and taker <= 0.92:
            conflicts.append(f"15m taker selling dominates at {taker:.2f}×")
        if oi_15m is not None and oi_15m >= 0.25 and price_change_15m_pct >= 0.10:
            supports.append(f"OI +{oi_15m:.2f}% expands with rising 15m price")
        elif oi_15m is not None and oi_15m >= 0.25 and price_change_15m_pct <= -0.10:
            conflicts.append(f"OI +{oi_15m:.2f}% expands while 15m price falls")
        if accounts is not None and accounts <= 0.67 and funding <= -3.0:
            supports.append("short crowding leaves upside squeeze fuel")
        elif accounts is not None and accounts >= 1.50 and funding >= 3.0:
            conflicts.append("long accounts and positive funding are crowded")
    else:
        if taker is not None and taker <= 0.92:
            supports.append(f"15m taker selling dominates at {taker:.2f}×")
        elif taker is not None and taker >= 1.08:
            conflicts.append(f"15m taker buying {taker:.2f}× sell volume")
        if oi_15m is not None and oi_15m >= 0.25 and price_change_15m_pct <= -0.10:
            supports.append(f"OI +{oi_15m:.2f}% expands with falling 15m price")
        elif oi_15m is not None and oi_15m >= 0.25 and price_change_15m_pct >= 0.10:
            conflicts.append(f"OI +{oi_15m:.2f}% expands while 15m price rises")
        if accounts is not None and accounts >= 1.50 and funding >= 3.0:
            supports.append("long crowding leaves downside liquidation risk")
        elif accounts is not None and accounts <= 0.67 and funding <= -3.0:
            conflicts.append("short accounts and negative funding are crowded")

    if accounts is not None and accounts >= 1.50 and funding >= 3.0:
        leverage_risk = "LONGS_CROWDED"
    elif accounts is not None and accounts <= 0.67 and funding <= -3.0:
        leverage_risk = "SHORTS_CROWDED"
    elif oi_15m is not None and oi_15m <= -0.50:
        leverage_risk = "DELEVERAGING"
    elif oi_15m is not None and oi_15m >= 0.50:
        leverage_risk = "LEVERAGE_BUILDING"
    elif available:
        leverage_risk = "BALANCED"
    else:
        leverage_risk = "UNKNOWN"

    if not available:
        verdict = "UNAVAILABLE"
        reasons = ["one or more public derivatives feeds are unavailable"]
    elif len(conflicts) >= 2:
        verdict = "CONFLICTS"
        reasons = conflicts + supports
    elif len(supports) >= 2 and not conflicts:
        verdict = "SUPPORTS"
        reasons = supports
    else:
        verdict = "NEUTRAL"
        reasons = conflicts + supports or ["positioning is mixed; no two-factor edge"]
    return {
        "verdict": verdict,
        "available": available,
        "leverage_risk": leverage_risk,
        "reasons": tuple(reasons),
        "oi_15m": oi_15m,
        "oi_1h": oi_1h,
        "taker": taker,
        "accounts": accounts,
        "open_interest_usd": snapshot.open_interest_usd,
    }


def _round_price(value: float) -> float:
    if value >= 10_000:
        digits = 1
    elif value >= 100:
        digits = 2
    elif value >= 1:
        digits = 4
    elif value >= 0.01:
        digits = 6
    else:
        digits = 8
    return round(value, digits)


def build_ticket(
    signal: Signal,
    config: Config,
    equity_usd: float,
    now: datetime | None = None,
) -> dict:
    if signal.side not in {"LONG", "SHORT"} or not signal.entry or not signal.atr:
        raise ValueError("a directional signal with entry and ATR is required")
    now = now or datetime.now(timezone.utc)
    direction = 1.0 if signal.side == "LONG" else -1.0
    entry = signal.entry
    stop = signal.stop if signal.stop is not None else entry - direction * signal.atr * config.stop_atr
    stop_distance = abs(entry - stop)
    friction_bp = signal.friction_bp or config.fixed_round_trip_cost_bp
    friction_per_unit = entry * friction_bp / 10_000.0
    effective_risk_per_unit = stop_distance + friction_per_unit
    risk_budget = equity_usd * config.risk_per_trade_fraction
    qty_by_risk = risk_budget / max(effective_risk_per_unit, 1e-12)
    qty_by_notional = equity_usd * config.max_notional_fraction / max(entry, 1e-12)
    quantity = max(0.0, min(qty_by_risk, qty_by_notional))
    notional = quantity * entry
    estimated_cost = notional * friction_bp / 10_000.0
    effective_risk = quantity * stop_distance + estimated_cost

    def target_for(net_r: float) -> float:
        target_move = net_r * effective_risk_per_unit + friction_per_unit
        return entry + direction * target_move

    return {
        "mode": "PAPER_ONLY",
        "status": signal.state,
        "activation": "ENTER_NOW" if signal.state == "LIVE" else "CONDITIONAL_TRIGGER",
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "side": signal.side,
        "action": signal.action,
        "created_at": now.isoformat(),
        "entry_valid_until": signal.expires_at,
        "entry": _round_price(entry),
        "stop": _round_price(stop),
        "tp1": _round_price(target_for(1.0)),
        "tp2": _round_price(target_for(2.0)),
        "tp3": _round_price(target_for(3.0)),
        "time_exit": (now + timedelta(minutes=config.max_hold_minutes)).isoformat(),
        "invalidation": signal.invalidation,
        "quantity": quantity,
        "notional_usd": round(notional, 2),
        "risk_budget_usd": round(risk_budget, 2),
        "effective_risk_usd": round(effective_risk, 2),
        "estimated_round_trip_cost_usd": round(estimated_cost, 2),
        "friction_bp": round(friction_bp, 2),
        "friction_stop_pct": signal.friction_stop_pct,
        "score": signal.score,
        "confidence": signal.confidence,
        "playbook": signal.playbook,
        "playbook_confirmation": signal.playbook_confirmation,
        "target_basis": (
            "FVG retest entry; TP1/TP2/TP3 = minimum 1R/2R/3R net of modeled round-trip cost"
            if signal.playbook == "LIQUIDITY_MSS_FVG"
            else "breakout entry; TP1/TP2/TP3 = 1R/2R/3R net of modeled round-trip cost"
        ),
        "disclaimer": "Research/paper ticket only; no exchange order is created.",
    }


def confirmed_swings(
    candles: tuple[Candle, ...],
    left: int = 2,
    right: int = 2,
) -> dict[str, list[dict[str, float | int]]]:
    """Return pivots only after `right` closed bars confirm them.

    The confirmation index is carried with every pivot so callers evaluating an
    older candle can exclude information that was not yet knowable.
    """
    highs: list[dict[str, float | int]] = []
    lows: list[dict[str, float | int]] = []
    if left < 1 or right < 1:
        raise ValueError("swing confirmation requires at least one bar on each side")
    for index in range(left, len(candles) - right):
        bar = candles[index]
        left_bars = candles[index - left : index]
        right_bars = candles[index + 1 : index + right + 1]
        if all(bar.high > item.high for item in (*left_bars, *right_bars)):
            highs.append(
                {
                    "index": index,
                    "confirmation_index": index + right,
                    "price": bar.high,
                    "time": bar.close_time,
                }
            )
        if all(bar.low < item.low for item in (*left_bars, *right_bars)):
            lows.append(
                {
                    "index": index,
                    "confirmation_index": index + right,
                    "price": bar.low,
                    "time": bar.close_time,
                }
            )
    return {"highs": highs, "lows": lows}


def find_fair_value_gaps(
    candles: tuple[Candle, ...],
    side: str,
    start_index: int,
    atr_value: float,
    min_gap_atr: float = 0.08,
) -> list[dict[str, float | int | None]]:
    """Find still-open three-candle imbalances at or after `start_index`.

    A bullish gap is fully filled only when a later low reaches its lower edge;
    a bearish gap is filled when a later high reaches its upper edge. Midpoint
    touches are reported separately so a setup cannot be re-used after entry.
    """
    if side not in {"LONG", "SHORT"}:
        raise ValueError("side must be LONG or SHORT")
    minimum = max(0.0, atr_value * min_gap_atr)
    gaps: list[dict[str, float | int | None]] = []
    for index in range(max(2, start_index), len(candles)):
        first, third = candles[index - 2], candles[index]
        if side == "LONG":
            low, high = first.high, third.low
        else:
            low, high = third.high, first.low
        if high - low < minimum:
            continue
        midpoint = (low + high) / 2.0
        later = candles[index + 1 :]
        if side == "LONG":
            fully_filled = any(bar.low <= low for bar in later)
            retest = next(
                (
                    index + 1 + offset
                    for offset, bar in enumerate(later)
                    if bar.low <= midpoint and bar.close > low
                ),
                None,
            )
        else:
            fully_filled = any(bar.high >= high for bar in later)
            retest = next(
                (
                    index + 1 + offset
                    for offset, bar in enumerate(later)
                    if bar.high >= midpoint and bar.close < high
                ),
                None,
            )
        if not fully_filled:
            gaps.append(
                {
                    "index": index,
                    "low": low,
                    "high": high,
                    "mid": midpoint,
                    "retest_index": retest,
                    "size_atr": (high - low) / max(atr_value, 1e-12),
                }
            )
    return gaps


def _session_levels(
    primary: tuple[Candle, ...],
    as_of_ms: int,
    side: str,
) -> list[tuple[str, float]]:
    available = [bar for bar in primary if bar.close_time < as_of_ms]
    if not available:
        return []
    as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)
    levels: list[tuple[str, float]] = []
    dates = sorted(
        {
            datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).date()
            for bar in available
            if datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).date()
            < as_of.date()
        }
    )
    if dates:
        previous_date = dates[-1]
        previous = [
            bar
            for bar in available
            if datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).date()
            == previous_date
        ]
        value = min(bar.low for bar in previous) if side == "LONG" else max(bar.high for bar in previous)
        levels.append(("PREVIOUS DAY LOW" if side == "LONG" else "PREVIOUS DAY HIGH", value))
    if as_of.hour >= 4:
        asia = [
            bar
            for bar in available
            if datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).date() == as_of.date()
            and 0 <= datetime.fromtimestamp(bar.open_time / 1000, timezone.utc).hour < 4
        ]
        if asia:
            value = min(bar.low for bar in asia) if side == "LONG" else max(bar.high for bar in asia)
            levels.append(("ASIA LOW" if side == "LONG" else "ASIA HIGH", value))
    return levels


def _liquidity_sweeps(
    primary: tuple[Candle, ...],
    trend: tuple[Candle, ...],
    side: str,
    atr_value: float,
    config: Config,
) -> list[dict[str, float | int | str]]:
    primary_swings = confirmed_swings(primary, config.swing_left_bars, config.swing_right_bars)
    trend_swings = confirmed_swings(trend, config.swing_left_bars, config.swing_right_bars)
    swing_key = "lows" if side == "LONG" else "highs"
    start = max(1, len(primary) - config.liquidity_sweep_lookback)
    found: list[dict[str, float | int | str]] = []
    buffer = atr_value * config.sweep_buffer_atr
    for index in range(start, len(primary)):
        candle = primary[index]
        levels: list[tuple[str, float]] = []
        primary_known = [
            pivot for pivot in primary_swings[swing_key]
            if int(pivot["confirmation_index"]) < index
        ]
        if primary_known:
            levels.append((f"5M SWING {'LOW' if side == 'LONG' else 'HIGH'}", float(primary_known[-1]["price"])))
        trend_known = [
            pivot for pivot in trend_swings[swing_key]
            if trend[int(pivot["confirmation_index"])].close_time < candle.open_time
        ]
        if trend_known:
            levels.append((f"15M SWING {'LOW' if side == 'LONG' else 'HIGH'}", float(trend_known[-1]["price"])))
        levels.extend(_session_levels(primary, candle.open_time, side))
        swept: list[tuple[str, float]] = []
        for name, level in levels:
            if side == "LONG":
                valid = candle.low <= level - buffer and candle.close > level and candle.open >= level - buffer
            else:
                valid = candle.high >= level + buffer and candle.close < level and candle.open <= level + buffer
            if valid:
                swept.append((name, level))
        if swept:
            name, level = min(swept, key=lambda item: abs(candle.close - item[1]))
            found.append(
                {
                    "index": index,
                    "name": name,
                    "level": level,
                    "extreme": candle.low if side == "LONG" else candle.high,
                }
            )
    return found


def _dealing_range(
    trend: tuple[Candle, ...],
    as_of_ms: int,
    config: Config,
) -> tuple[float, float, float] | None:
    eligible = tuple(bar for bar in trend if bar.close_time < as_of_ms)
    if len(eligible) < 8:
        return None
    swings = confirmed_swings(eligible, config.swing_left_bars, config.swing_right_bars)
    if swings["highs"] and swings["lows"]:
        high = float(swings["highs"][-1]["price"])
        low = float(swings["lows"][-1]["price"])
    else:
        window = eligible[-20:]
        high, low = max(bar.high for bar in window), min(bar.low for bar in window)
    if high <= low:
        return None
    return low, high, (low + high) / 2.0


def _market_structure_shift(
    primary: tuple[Candle, ...],
    side: str,
    sweep_index: int,
    atr_value: float,
    config: Config,
) -> dict[str, float | int] | None:
    swings = confirmed_swings(primary, config.swing_left_bars, config.swing_right_bars)
    key = "highs" if side == "LONG" else "lows"
    known = [
        pivot for pivot in swings[key]
        if int(pivot["confirmation_index"]) < sweep_index
    ]
    if known:
        level = float(known[-1]["price"])
    else:
        context = primary[max(0, sweep_index - 12) : sweep_index]
        if not context:
            return None
        level = max(bar.high for bar in context) if side == "LONG" else min(bar.low for bar in context)
    buffer = atr_value * config.mss_buffer_atr
    for index in range(sweep_index + 1, len(primary)):
        bar = primary[index]
        prior_bodies = [abs(item.close - item.open) for item in primary[max(0, index - 20) : index]]
        body_base = statistics.median(prior_bodies) if prior_bodies else 0.0
        body_ratio = abs(bar.close - bar.open) / max(body_base, 1e-12)
        location = (bar.close - bar.low) / max(bar.high - bar.low, 1e-12)
        break_ok = bar.close >= level + buffer if side == "LONG" else bar.close <= level - buffer
        displacement_ok = body_ratio >= config.displacement_body_ratio and (
            location >= 0.70 if side == "LONG" else location <= 0.30
        )
        if break_ok and displacement_ok:
            return {"index": index, "level": level, "body_ratio": body_ratio}
    return None


def _evaluate_momentum_snapshot(
    snapshot: MarketSnapshot,
    config: Config,
    equity_usd: float | None = None,
    now: datetime | None = None,
) -> Signal:
    primary, trend, higher = snapshot.primary, snapshot.trend, snapshot.higher
    required_primary = max(55, config.breakout_lookback + 2, config.volume_lookback + 2)
    if len(primary) < required_primary or len(trend) < 55 or len(higher) < 201:
        return Signal(
            symbol=snapshot.symbol,
            state="INSUFFICIENT",
            side=None,
            score=0,
            action="WAIT — indicator warm-up",
            funding_bp=snapshot.funding_bp,
            open_interest_usd=snapshot.open_interest_usd,
            open_interest_change_15m_pct=snapshot.open_interest_change_15m_pct,
            open_interest_change_1h_pct=snapshot.open_interest_change_1h_pct,
            taker_buy_sell_ratio_15m=snapshot.taker_buy_sell_ratio_15m,
            long_short_account_ratio=snapshot.long_short_account_ratio,
            price_change_pct_24h=snapshot.price_change_pct_24h,
            quote_volume_24h=snapshot.quote_volume_24h,
            blocked_by=(
                f"need {required_primary} closed 5m, 55 closed 15m, and 201 closed 1h candles",
            ),
        )

    now = now or datetime.now(timezone.utc)
    closes = [bar.close for bar in primary]
    trend_closes = [bar.close for bar in trend]
    higher_closes = [bar.close for bar in higher]
    current = primary[-1]
    price_change_15m_pct = (
        (current.close / primary[-4].close - 1.0) * 100.0
        if len(primary) >= 4 and primary[-4].close > 0
        else 0.0
    )
    current_rsi = rsi(closes, 14)[-1]
    current_atr = atr(primary, 14)[-1]
    current_adx = adx(primary, 14)
    current_stoch = stoch_rsi(closes, 14)
    if None in {current_rsi, current_atr, current_adx, current_stoch} or current.close <= 0:
        raise ValueError("indicator calculation did not warm up")
    assert current_rsi is not None and current_atr is not None
    assert current_adx is not None and current_stoch is not None

    ema9, ema21 = ema(closes, 9)[-1], ema(closes, 21)[-1]
    trend20, trend50 = ema(trend_closes, 20)[-1], ema(trend_closes, 50)[-1]
    higher50, higher200 = ema(higher_closes, 50)[-1], ema(higher_closes, 200)[-1]
    _, _, macd_hist = macd(closes)
    vwap = session_vwap(primary)
    bb_window = closes[-20:]
    bb_mid = statistics.mean(bb_window)
    bb_std = statistics.pstdev(bb_window)
    bb_position = (current.close - bb_mid) / max(2.0 * bb_std, 1e-12)

    bid, ask = snapshot.bid, snapshot.ask
    midpoint = (bid + ask) / 2.0 if bid > 0 and ask > bid else current.close
    spread_bp = (ask - bid) / midpoint * 10_000.0 if bid > 0 and ask > bid else math.inf
    mark = snapshot.mark if snapshot.mark > 0 else current.close
    atr_pct = current_atr / current.close * 100.0
    previous = primary[-config.breakout_lookback - 1 : -1]
    breakout_high = max(bar.high for bar in previous)
    breakout_low = min(bar.low for bar in previous)
    volumes = [bar.quote_volume for bar in primary[-config.volume_lookback - 1 : -1]]
    baseline_volume = statistics.median(volumes)
    volume_ratio = current.quote_volume / baseline_volume if baseline_volume > 0 else 0.0
    candle_range = max(current.high - current.low, 1e-12)
    close_location = (current.close - current.low) / candle_range
    friction_bp = config.fixed_round_trip_cost_bp + (
        spread_bp if math.isfinite(spread_bp) else config.max_spread_bp * 10.0
    )
    drift_atr = abs(mark - current.close) / max(current_atr, 1e-12)
    zone = kill_zone(now)
    buffer = max(current_atr * 0.05, mark * min(spread_bp, config.max_spread_bp) / 20_000.0)
    swing_low = min(bar.low for bar in primary[-8:-1])
    swing_high = max(bar.high for bar in primary[-8:-1])

    definitions = {
        "LONG": {
            "higher": higher_closes[-1] > higher50 > higher200,
            "trend": trend_closes[-1] > trend20 > trend50,
            "alignment": current.close > ema9 > ema21,
            "macd": macd_hist > 0,
            "momentum": config.long_rsi_min <= current_rsi <= config.long_rsi_max and current_stoch >= 55,
            "adx": current_adx >= config.min_adx,
            "vwap": current.close > vwap,
            "breakout": current.close > breakout_high,
            "candle": close_location >= 0.60,
            "level": breakout_high,
            "trigger": breakout_high + buffer,
            "trend_name": "BULLISH",
        },
        "SHORT": {
            "higher": higher_closes[-1] < higher50 < higher200,
            "trend": trend_closes[-1] < trend20 < trend50,
            "alignment": current.close < ema9 < ema21,
            "macd": macd_hist < 0,
            "momentum": config.short_rsi_min <= current_rsi <= config.short_rsi_max and current_stoch <= 45,
            "adx": current_adx >= config.min_adx,
            "vwap": current.close < vwap,
            "breakout": current.close < breakout_low,
            "candle": close_location <= 0.40,
            "level": breakout_low,
            "trigger": breakout_low - buffer,
            "trend_name": "BEARISH",
        },
    }

    def scored(side: str) -> dict:
        item = definitions[side]
        derivatives = derivatives_context(snapshot, side, price_change_15m_pct)
        direction = 1.0 if side == "LONG" else -1.0
        live_entry = mark
        planned_entry = live_entry if item["breakout"] else item["trigger"]
        structural_stop = (
            min(planned_entry - config.stop_atr * current_atr, swing_low - 0.10 * current_atr)
            if side == "LONG"
            else max(planned_entry + config.stop_atr * current_atr, swing_high + 0.10 * current_atr)
        )
        stop_distance = abs(planned_entry - structural_stop)
        stop_atr = stop_distance / max(current_atr, 1e-12)
        stop_bp = stop_distance / planned_entry * 10_000.0
        friction_stop_pct = friction_bp / max(stop_bp, 1e-12) * 100.0
        execution = (
            spread_bp <= config.max_spread_bp
            and config.min_atr_pct <= atr_pct <= config.max_atr_pct
            and drift_atr <= config.max_entry_drift_atr
            and stop_atr <= config.max_stop_atr
            and friction_stop_pct <= config.max_friction_stop_pct
            and 2.0 * stop_bp >= config.min_reward_cost_multiple * friction_bp
        )
        volume_ok = volume_ratio >= config.min_volume_ratio
        controlled_breakout = bool(item["breakout"] and item["candle"])
        weights = (
            ("higher", 15), ("trend", 15), ("alignment", 10), ("macd", 10),
            ("momentum", 10), ("adx", 10), ("vwap", 10),
        )
        score = sum(weight for name, weight in weights if item[name])
        score += 10 if volume_ok else 0
        score += 10 if controlled_breakout else 0
        score += 10 if execution else 0
        evidence: list[str] = []
        blocks: list[str] = []
        labels = {
            "higher": "1h EMA50/200 regime",
            "trend": "15m EMA20/50 trend",
            "alignment": "5m EMA9/21 alignment",
            "macd": "MACD 12/26/9 histogram",
            "momentum": "RSI14 + Stoch RSI momentum",
            "adx": f"ADX14 strength {current_adx:.1f}",
            "vwap": "session VWAP position",
        }
        for name, _ in weights:
            (evidence if item[name] else blocks).append(
                labels[name] + (" aligned" if item[name] else " not confirmed")
            )
        if volume_ok:
            evidence.append(f"quote volume {volume_ratio:.2f}× 20-bar median")
        else:
            blocks.append(f"volume {volume_ratio:.2f}× below {config.min_volume_ratio:.2f}× gate")
        if controlled_breakout:
            evidence.append(f"closed 5m breakout with directional close ({close_location:.0%})")
        else:
            blocks.append("closed 5m breakout/close-location trigger pending")
        if execution:
            evidence.append(f"friction {friction_stop_pct:.1f}% of stop distance")
        else:
            if spread_bp > config.max_spread_bp:
                blocks.append(f"spread {spread_bp:.1f}bp above {config.max_spread_bp:.1f}bp")
            if not config.min_atr_pct <= atr_pct <= config.max_atr_pct:
                blocks.append(f"ATR {atr_pct:.2f}% outside volatility band")
            if drift_atr > config.max_entry_drift_atr:
                blocks.append(f"mark drift {drift_atr:.2f} ATR from signal close")
            if stop_atr > config.max_stop_atr:
                blocks.append(f"structural stop {stop_atr:.2f} ATR is too wide")
            if friction_stop_pct > config.max_friction_stop_pct:
                blocks.append(f"friction consumes {friction_stop_pct:.1f}% of stop")
        near_trigger = abs(current.close - item["level"]) <= 1.50 * current_atr or item["breakout"]
        core_bias = sum(bool(item[key]) for key in ("higher", "trend", "alignment")) >= 2
        derivatives_clear = derivatives["verdict"] != "CONFLICTS"
        if not derivatives_clear:
            blocks.append("derivatives context conflicts on at least two independent factors")
        live = (
            score >= config.signal_score
            and all(item[key] for key in ("higher", "trend", "alignment", "macd", "momentum", "adx", "vwap"))
            and volume_ok and controlled_breakout and execution and derivatives_clear
            and zone != "OFF_HOURS"
        )
        armed = (
            score >= config.armed_score and core_bias and near_trigger
            and execution and derivatives_clear
        )
        return {
            **item,
            "side": side,
            "score": min(100, score),
            "entry": planned_entry,
            "stop": structural_stop,
            "stop_bp": stop_bp,
            "friction_stop_pct": friction_stop_pct,
            "evidence": evidence,
            "blocks": blocks,
            "live": live,
            "armed": armed,
            "direction": direction,
            "derivatives": derivatives,
        }

    choices = sorted((scored("LONG"), scored("SHORT")), key=lambda item: item["score"], reverse=True)
    choice = choices[0]
    score = int(choice["score"])
    if choice["live"]:
        state = "LIVE"
    elif choice["armed"]:
        state = "ARMED"
    elif score >= config.watch_score:
        state = "WATCH"
    else:
        state = "STAND_DOWN"
    side = str(choice["side"])
    expiry = now + timedelta(minutes=config.entry_expiry_minutes)
    confidence = "A" if score >= 90 else "A−" if score >= 80 else "B" if score >= 70 else "C"
    entry = float(choice["entry"])
    if state == "LIVE":
        action = f"ENTER {side} NOW — closed-candle trigger confirmed"
    elif state == "ARMED":
        verb = "BUY STOP" if side == "LONG" else "SELL STOP"
        action = f"{verb} {_round_price(entry)} — cancel if not triggered by {expiry:%H:%M} UTC"
    elif state == "WATCH":
        action = f"WAIT — {side.lower()} bias, confluence incomplete"
    else:
        action = "STAND DOWN — no coherent directional edge"
    invalidation = (
        f"Cancel before entry if 5m closes {'below' if side == 'LONG' else 'above'} "
        f"{_round_price(float(choice['level']))}, spread exceeds {config.max_spread_bp:.0f}bp, "
        "or the ticket expires. After entry, the hard stop is final."
    )
    blocks = list(choice["blocks"])
    if zone == "OFF_HOURS":
        blocks.append("outside Asia/London/New York kill zone; LIVE disabled")
    signal_id = f"{snapshot.symbol}:{current.close_time}:MOMENTUM_BREAKOUT:{side}:{state}"
    result = Signal(
        symbol=snapshot.symbol,
        state=state,
        side=side,
        score=score,
        action=action,
        playbook="MOMENTUM_BREAKOUT",
        playbook_label="PLAYBOOK A · MOMENTUM",
        playbook_confirmation="SINGLE",
        confidence=confidence,
        signal_id=signal_id,
        candle_close_time=current.close_time,
        entry=entry,
        entry_trigger=float(choice["trigger"]),
        stop=float(choice["stop"]),
        spread_bp=round(spread_bp, 3) if math.isfinite(spread_bp) else None,
        friction_bp=round(friction_bp, 3),
        friction_stop_pct=round(float(choice["friction_stop_pct"]), 2),
        stop_distance_bp=round(float(choice["stop_bp"]), 2),
        atr=current_atr,
        atr_pct=round(atr_pct, 3),
        rsi_5m=round(current_rsi, 2),
        stoch_rsi_5m=round(current_stoch, 2),
        macd_hist_5m=macd_hist,
        adx_5m=round(current_adx, 2),
        vwap=_round_price(vwap),
        bollinger_position=round(bb_position, 3),
        volume_ratio=round(volume_ratio, 2),
        breakout_level=_round_price(float(choice["level"])),
        higher_trend=str(choice["trend_name"]) if choice["higher"] else "MIXED",
        trend_15m=str(choice["trend_name"]) if choice["trend"] else "MIXED",
        kill_zone=zone,
        funding_bp=round(snapshot.funding_bp, 3),
        derivatives_verdict=str(choice["derivatives"]["verdict"]),
        derivatives_available=bool(choice["derivatives"]["available"]),
        open_interest_usd=snapshot.open_interest_usd,
        open_interest_change_15m_pct=snapshot.open_interest_change_15m_pct,
        open_interest_change_1h_pct=snapshot.open_interest_change_1h_pct,
        taker_buy_sell_ratio_15m=snapshot.taker_buy_sell_ratio_15m,
        long_short_account_ratio=snapshot.long_short_account_ratio,
        leverage_risk=str(choice["derivatives"]["leverage_risk"]),
        derivatives_reasons=tuple(choice["derivatives"]["reasons"]),
        price_change_pct_24h=round(snapshot.price_change_pct_24h, 2),
        quote_volume_24h=round(snapshot.quote_volume_24h, 2),
        expires_at=expiry.isoformat(),
        invalidation=invalidation,
        reasons=tuple(choice["evidence"]),
        blocked_by=tuple(blocks),
    )
    if state in {"LIVE", "ARMED"}:
        result = replace(
            result,
            ticket=build_ticket(result, config, equity_usd or config.starting_equity_usd, now=now),
        )
    return result


def _evaluate_liquidity_snapshot(
    snapshot: MarketSnapshot,
    config: Config,
    equity_usd: float | None = None,
    now: datetime | None = None,
) -> Signal:
    """Evaluate Playbook B with objective, closed-candle ICT/SMC-inspired rules."""
    primary, trend, higher = snapshot.primary, snapshot.trend, snapshot.higher
    if len(primary) < 55 or len(trend) < 55 or len(higher) < 201:
        return Signal(
            symbol=snapshot.symbol,
            state="INSUFFICIENT",
            side=None,
            score=0,
            action="WAIT — liquidity playbook warm-up",
            playbook="LIQUIDITY_MSS_FVG",
            playbook_label="PLAYBOOK B · LIQUIDITY MSS/FVG",
            funding_bp=snapshot.funding_bp,
            price_change_pct_24h=snapshot.price_change_pct_24h,
            quote_volume_24h=snapshot.quote_volume_24h,
            blocked_by=("need 55 closed 5m, 55 closed 15m, and 201 closed 1h candles",),
        )

    now = now or datetime.now(timezone.utc)
    current = primary[-1]
    closes = [bar.close for bar in primary]
    trend_closes = [bar.close for bar in trend]
    higher_closes = [bar.close for bar in higher]
    current_atr = atr(primary, 14)[-1]
    current_rsi = rsi(closes, 14)[-1]
    current_stoch = stoch_rsi(closes, 14)
    current_adx = adx(primary, 14)
    if None in {current_atr, current_rsi, current_stoch, current_adx} or current.close <= 0:
        raise ValueError("liquidity playbook indicators did not warm up")
    assert current_atr is not None and current_rsi is not None
    assert current_stoch is not None and current_adx is not None

    higher50, higher200 = ema(higher_closes, 50)[-1], ema(higher_closes, 200)[-1]
    trend20, trend50 = ema(trend_closes, 20)[-1], ema(trend_closes, 50)[-1]
    _, _, macd_hist = macd(closes)
    vwap = session_vwap(primary)
    bb_window = closes[-20:]
    bb_mid = statistics.mean(bb_window)
    bb_std = statistics.pstdev(bb_window)
    bb_position = (current.close - bb_mid) / max(2.0 * bb_std, 1e-12)
    volume_base = statistics.median(
        bar.quote_volume for bar in primary[-config.volume_lookback - 1 : -1]
    )
    volume_ratio = current.quote_volume / volume_base if volume_base > 0 else 0.0
    bid, ask = snapshot.bid, snapshot.ask
    midpoint = (bid + ask) / 2.0 if bid > 0 and ask > bid else current.close
    spread_bp = (ask - bid) / midpoint * 10_000.0 if bid > 0 and ask > bid else math.inf
    mark = snapshot.mark if snapshot.mark > 0 else current.close
    atr_pct = current_atr / current.close * 100.0
    friction_bp = config.fixed_round_trip_cost_bp + (
        spread_bp if math.isfinite(spread_bp) else config.max_spread_bp * 10.0
    )
    drift_atr = abs(mark - current.close) / max(current_atr, 1e-12)
    zone = kill_zone(now)
    price_change_15m_pct = (
        (current.close / primary[-4].close - 1.0) * 100.0
        if primary[-4].close > 0
        else 0.0
    )

    def candidate(side: str) -> dict:
        direction = 1.0 if side == "LONG" else -1.0
        htf_bias = (
            higher_closes[-1] > higher50 > higher200
            if side == "LONG"
            else higher_closes[-1] < higher50 < higher200
        )
        trend_bias = (
            trend_closes[-1] > trend20 > trend50
            if side == "LONG"
            else trend_closes[-1] < trend20 < trend50
        )
        derivatives = derivatives_context(snapshot, side, price_change_15m_pct)
        sweeps = _liquidity_sweeps(primary, trend, side, current_atr, config)
        selected = sweeps[-1] if sweeps else None
        selected_mss: dict[str, float | int] | None = None
        selected_fvg: dict[str, float | int | None] | None = None
        for sweep in reversed(sweeps):
            mss = _market_structure_shift(
                primary, side, int(sweep["index"]), current_atr, config
            )
            if mss is None:
                continue
            gaps = find_fair_value_gaps(
                primary,
                side,
                int(mss["index"]),
                current_atr,
                config.fvg_min_atr,
            )
            if gaps:
                selected = sweep
                selected_mss = mss
                # Prefer the most recent unfilled imbalance; a current-bar retest
                # outranks a merely pending gap.
                current_retests = [gap for gap in gaps if gap["retest_index"] == len(primary) - 1]
                selected_fvg = (current_retests or gaps)[-1]
                break
            if selected is sweep:
                selected_mss = mss

        sweep_ok = selected is not None
        mss_ok = selected_mss is not None
        fvg_ok = selected_fvg is not None and mss_ok
        entry = float(selected_fvg["mid"]) if selected_fvg else current.close
        range_values = _dealing_range(
            trend,
            primary[int(selected["index"])].open_time if selected else current.open_time,
            config,
        )
        range_mid = range_values[2] if range_values else None
        if range_mid is None:
            location_ok = False
            premium_discount = "UNKNOWN"
        else:
            location_ok = entry <= range_mid if side == "LONG" else entry >= range_mid
            premium_discount = (
                "DISCOUNT" if entry < range_mid else "PREMIUM" if entry > range_mid else "EQUILIBRIUM"
            )

        sweep_extreme = float(selected["extreme"]) if selected else current.close - direction * current_atr
        stop = (
            min(sweep_extreme - 0.10 * current_atr, entry - config.liquidity_stop_min_atr * current_atr)
            if side == "LONG"
            else max(sweep_extreme + 0.10 * current_atr, entry + config.liquidity_stop_min_atr * current_atr)
        )
        stop_distance = abs(entry - stop)
        stop_atr = stop_distance / max(current_atr, 1e-12)
        stop_bp = stop_distance / max(entry, 1e-12) * 10_000.0
        friction_stop_pct = friction_bp / max(stop_bp, 1e-12) * 100.0
        execution = (
            spread_bp <= config.max_spread_bp
            and config.min_atr_pct <= atr_pct <= config.max_atr_pct
            and drift_atr <= config.max_entry_drift_atr
            and stop_atr <= config.max_stop_atr
            and friction_stop_pct <= config.max_friction_stop_pct
            and 2.0 * stop_bp >= config.min_reward_cost_multiple * friction_bp
        )
        sequence_ok = mss_ok and fvg_ok
        score = (
            (20 if htf_bias else 0)
            + (15 if location_ok else 0)
            + (20 if sweep_ok else 0)
            + (20 if mss_ok else 0)
            + (15 if sequence_ok else 0)
            + (10 if execution else 0)
        )
        derivatives_clear = derivatives["verdict"] != "CONFLICTS"
        retest_index = int(selected_fvg["retest_index"]) if selected_fvg and selected_fvg["retest_index"] is not None else None
        current_retest = retest_index == len(primary) - 1
        pending_retest = retest_index is None and abs(mark - entry) <= (
            config.fvg_entry_max_distance_atr * current_atr
        )
        critical = htf_bias and location_ok and sweep_ok and mss_ok and fvg_ok and execution
        live = (
            score >= config.liquidity_signal_score
            and critical
            and current_retest
            and derivatives_clear
            and zone != "OFF_HOURS"
        )
        armed = (
            score >= config.liquidity_armed_score
            and critical
            and pending_retest
            and derivatives_clear
        )

        evidence: list[str] = []
        blocks: list[str] = []
        (evidence if htf_bias else blocks).append(
            f"1h EMA50/200 structure {'aligned' if htf_bias else 'not aligned'}"
        )
        if range_mid is None:
            blocks.append("15m dealing range unavailable")
        elif location_ok:
            evidence.append(f"entry in {premium_discount.lower()} half of 15m dealing range")
        else:
            blocks.append(f"entry is in {premium_discount.lower()}, wrong side of range")
        if selected:
            evidence.append(
                f"{selected['name'].lower()} swept and reclaimed on a closed 5m candle"
            )
        else:
            blocks.append("no closed 5m liquidity sweep in the last eight bars")
        if selected_mss:
            evidence.append(
                f"MSS closed through {_round_price(float(selected_mss['level']))} with "
                f"{float(selected_mss['body_ratio']):.2f}× displacement"
            )
        elif selected:
            blocks.append("post-sweep market structure shift with displacement is pending")
        if selected_fvg:
            evidence.append(
                f"unfilled FVG {_round_price(float(selected_fvg['low']))}–"
                f"{_round_price(float(selected_fvg['high']))}"
            )
        elif selected_mss:
            blocks.append(f"FVG at least {config.fvg_min_atr:.2f} ATR is pending")
        if execution:
            evidence.append(f"friction {friction_stop_pct:.1f}% of structural stop")
        else:
            if spread_bp > config.max_spread_bp:
                blocks.append(f"spread {spread_bp:.1f}bp above {config.max_spread_bp:.1f}bp")
            if not config.min_atr_pct <= atr_pct <= config.max_atr_pct:
                blocks.append(f"ATR {atr_pct:.2f}% outside volatility band")
            if drift_atr > config.max_entry_drift_atr:
                blocks.append(f"mark drift {drift_atr:.2f} ATR from signal close")
            if stop_atr > config.max_stop_atr:
                blocks.append(f"sweep stop {stop_atr:.2f} ATR is too wide")
            if friction_stop_pct > config.max_friction_stop_pct:
                blocks.append(f"friction consumes {friction_stop_pct:.1f}% of stop")
        if selected_fvg and retest_index is not None and not current_retest:
            blocks.append("FVG midpoint traded on an earlier candle; setup is consumed")
        elif selected_fvg and not current_retest and not pending_retest:
            blocks.append(
                f"FVG midpoint is more than {config.fvg_entry_max_distance_atr:.2f} ATR from mark"
            )
        if not derivatives_clear:
            blocks.append("derivatives context conflicts on at least two independent factors")
        if zone == "OFF_HOURS":
            blocks.append("outside Asia/London/New York kill zone; LIVE disabled")
        return {
            "side": side,
            "score": min(100, score),
            "entry": entry,
            "stop": stop,
            "stop_bp": stop_bp,
            "friction_stop_pct": friction_stop_pct,
            "higher": htf_bias,
            "trend": trend_bias,
            "selected": selected,
            "mss": selected_mss,
            "fvg": selected_fvg,
            "range_mid": range_mid,
            "premium_discount": premium_discount,
            "evidence": evidence,
            "blocks": blocks,
            "live": live,
            "armed": armed,
            "derivatives": derivatives,
        }

    choices = sorted(
        (candidate("LONG"), candidate("SHORT")),
        key=lambda item: (
            0 if item["live"] else 1 if item["armed"] else 2,
            -int(item["score"]),
        ),
    )
    choice = choices[0]
    score = int(choice["score"])
    state = (
        "LIVE" if choice["live"]
        else "ARMED" if choice["armed"]
        else "WATCH" if score >= config.watch_score
        else "STAND_DOWN"
    )
    side = str(choice["side"])
    entry = float(choice["entry"])
    expiry = now + timedelta(minutes=config.entry_expiry_minutes)
    confidence = "A" if score >= 90 else "A−" if score >= 80 else "B" if score >= 70 else "C"
    if state == "LIVE":
        action = f"ENTER {side} NOW — closed 5m FVG midpoint retest confirmed"
    elif state == "ARMED":
        action = (
            f"{side} LIMIT {_round_price(entry)} — 50% FVG retest; cancel by "
            f"{expiry:%H:%M} UTC"
        )
    elif state == "WATCH":
        action = f"WAIT — {side.lower()} liquidity sequence incomplete or consumed"
    else:
        action = "STAND DOWN — no valid liquidity sweep/MSS/FVG sequence"
    selected = choice["selected"]
    mss = choice["mss"]
    fvg = choice["fvg"]
    invalidation_level = float(selected["extreme"]) if selected else float(choice["stop"])
    invalidation = (
        f"Cancel before entry if 5m closes {'below' if side == 'LONG' else 'above'} "
        f"{_round_price(invalidation_level)}, the FVG fully fills, spread exceeds "
        f"{config.max_spread_bp:.0f}bp, or the ticket expires. After entry, the hard stop is final."
    )
    result = Signal(
        symbol=snapshot.symbol,
        state=state,
        side=side,
        score=score,
        action=action,
        playbook="LIQUIDITY_MSS_FVG",
        playbook_label="PLAYBOOK B · LIQUIDITY MSS/FVG",
        playbook_confirmation="SINGLE",
        confidence=confidence,
        signal_id=f"{snapshot.symbol}:{current.close_time}:LIQUIDITY_MSS_FVG:{side}:{state}",
        candle_close_time=current.close_time,
        entry=entry,
        entry_trigger=entry,
        stop=float(choice["stop"]),
        spread_bp=round(spread_bp, 3) if math.isfinite(spread_bp) else None,
        friction_bp=round(friction_bp, 3),
        friction_stop_pct=round(float(choice["friction_stop_pct"]), 2),
        stop_distance_bp=round(float(choice["stop_bp"]), 2),
        atr=current_atr,
        atr_pct=round(atr_pct, 3),
        rsi_5m=round(current_rsi, 2),
        stoch_rsi_5m=round(current_stoch, 2),
        macd_hist_5m=macd_hist,
        adx_5m=round(current_adx, 2),
        vwap=_round_price(vwap),
        bollinger_position=round(bb_position, 3),
        volume_ratio=round(volume_ratio, 2),
        breakout_level=_round_price(float(mss["level"])) if mss else None,
        liquidity_level_name=str(selected["name"]) if selected else None,
        liquidity_level=_round_price(float(selected["level"])) if selected else None,
        sweep_price=_round_price(float(selected["extreme"])) if selected else None,
        mss_level=_round_price(float(mss["level"])) if mss else None,
        displacement_ratio=round(float(mss["body_ratio"]), 2) if mss else None,
        fvg_low=_round_price(float(fvg["low"])) if fvg else None,
        fvg_high=_round_price(float(fvg["high"])) if fvg else None,
        fvg_mid=_round_price(float(fvg["mid"])) if fvg else None,
        dealing_range_mid=_round_price(float(choice["range_mid"])) if choice["range_mid"] is not None else None,
        premium_discount=str(choice["premium_discount"]),
        higher_trend="BULLISH" if choice["higher"] and side == "LONG" else "BEARISH" if choice["higher"] else "MIXED",
        trend_15m="BULLISH" if choice["trend"] and side == "LONG" else "BEARISH" if choice["trend"] else "MIXED",
        kill_zone=zone,
        funding_bp=round(snapshot.funding_bp, 3),
        derivatives_verdict=str(choice["derivatives"]["verdict"]),
        derivatives_available=bool(choice["derivatives"]["available"]),
        open_interest_usd=snapshot.open_interest_usd,
        open_interest_change_15m_pct=snapshot.open_interest_change_15m_pct,
        open_interest_change_1h_pct=snapshot.open_interest_change_1h_pct,
        taker_buy_sell_ratio_15m=snapshot.taker_buy_sell_ratio_15m,
        long_short_account_ratio=snapshot.long_short_account_ratio,
        leverage_risk=str(choice["derivatives"]["leverage_risk"]),
        derivatives_reasons=tuple(choice["derivatives"]["reasons"]),
        price_change_pct_24h=round(snapshot.price_change_pct_24h, 2),
        quote_volume_24h=round(snapshot.quote_volume_24h, 2),
        expires_at=expiry.isoformat(),
        invalidation=invalidation,
        reasons=tuple(choice["evidence"]),
        blocked_by=tuple(choice["blocks"]),
    )
    if state in {"LIVE", "ARMED"}:
        result = replace(
            result,
            ticket=build_ticket(result, config, equity_usd or config.starting_equity_usd, now=now),
        )
    return result


def evaluate_snapshot(
    snapshot: MarketSnapshot,
    config: Config,
    equity_usd: float | None = None,
    now: datetime | None = None,
) -> Signal:
    """Run both independent playbooks and emit one fail-closed decision."""
    now = now or datetime.now(timezone.utc)
    momentum = _evaluate_momentum_snapshot(snapshot, config, equity_usd, now)
    liquidity = _evaluate_liquidity_snapshot(snapshot, config, equity_usd, now)
    rank = {"LIVE": 0, "ARMED": 1, "WATCH": 2, "STAND_DOWN": 3, "INSUFFICIENT": 4}
    actionable = [item for item in (momentum, liquidity) if item.state in {"LIVE", "ARMED"}]
    if len(actionable) == 2 and actionable[0].side != actionable[1].side:
        base = min(actionable, key=lambda item: (rank[item.state], -item.score))
        return replace(
            base,
            state="WATCH",
            action="WAIT — Playbook A and Playbook B disagree on direction",
            playbook_confirmation="CONFLICT",
            signal_id=f"{snapshot.symbol}:{base.candle_close_time}:PLAYBOOK_CONFLICT:WATCH",
            blocked_by=base.blocked_by + ("opposite actionable playbooks; arbiter vetoed the ticket",),
            ticket=None,
        )
    chosen = min(
        (momentum, liquidity),
        key=lambda item: (
            rank.get(item.state, 9),
            -item.score,
            0 if item.playbook == "LIQUIDITY_MSS_FVG" else 1,
        ),
    )
    if len(actionable) == 2 and actionable[0].side == actionable[1].side:
        chosen = replace(
            chosen,
            playbook_confirmation="DUAL_CONFIRMATION",
            reasons=chosen.reasons + ("independent playbooks confirm the same direction",),
        )
        chosen = replace(
            chosen,
            ticket=build_ticket(
                chosen, config, equity_usd or config.starting_equity_usd, now=now
            ),
        )
    return chosen
