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
    higher_trend: str | None = None
    trend_15m: str | None = None
    kill_zone: str | None = None
    funding_bp: float | None = None
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
        return data


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
        "target_basis": "TP1/TP2/TP3 = 1R/2R/3R net of modeled round-trip cost",
        "disclaimer": "Research/paper ticket only; no exchange order is created.",
    }


def evaluate_snapshot(
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
        live = (
            score >= config.signal_score
            and all(item[key] for key in ("higher", "trend", "alignment", "macd", "momentum", "adx", "vwap"))
            and volume_ok and controlled_breakout and execution and zone != "OFF_HOURS"
        )
        armed = score >= config.armed_score and core_bias and near_trigger and execution
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
    signal_id = f"{snapshot.symbol}:{current.close_time}:{side}:{state}"
    result = Signal(
        symbol=snapshot.symbol,
        state=state,
        side=side,
        score=score,
        action=action,
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
