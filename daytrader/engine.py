"""Closed-candle intraday signal engine and risk-sized paper tickets."""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass
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
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

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
    ranges = []
    for i, candle in enumerate(candles):
        if i == 0:
            ranges.append(candle.high - candle.low)
        else:
            prev_close = candles[i - 1].close
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


@dataclass(frozen=True)
class Signal:
    symbol: str
    state: str
    side: str | None
    score: int
    signal_id: str | None
    candle_close_time: int | None
    entry: float | None
    spread_bp: float | None
    friction_bp: float | None
    atr: float | None
    atr_pct: float | None
    rsi_5m: float | None
    volume_ratio: float | None
    breakout_level: float | None
    funding_bp: float | None
    price_change_pct_24h: float | None
    quote_volume_24h: float | None
    reasons: tuple[str, ...]
    blocked_by: tuple[str, ...]
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
    stop_distance = signal.atr * config.stop_atr
    entry = signal.entry
    stop = entry - direction * stop_distance
    friction_bp = signal.friction_bp or config.fixed_round_trip_cost_bp
    friction_per_unit = entry * friction_bp / 10_000.0
    effective_risk_per_unit = stop_distance + friction_per_unit
    risk_budget = equity_usd * config.risk_per_trade_fraction
    qty_by_risk = risk_budget / max(effective_risk_per_unit, 1e-12)
    qty_by_notional = (
        equity_usd * config.max_notional_fraction / max(entry, 1e-12)
    )
    quantity = max(0.0, min(qty_by_risk, qty_by_notional))
    notional = quantity * entry
    estimated_cost = notional * friction_bp / 10_000.0
    effective_risk = quantity * stop_distance + estimated_cost

    def target_for(net_r: float) -> float:
        # Net profit/unit = target move - modeled round-trip friction.
        # Solve for a true multiple of effective stop risk after that friction.
        target_move = net_r * effective_risk_per_unit + friction_per_unit
        return entry + direction * target_move

    return {
        "mode": "PAPER_ONLY",
        "signal_id": signal.signal_id,
        "symbol": signal.symbol,
        "side": signal.side,
        "opened_at": now.isoformat(),
        "entry": _round_price(entry),
        "stop": _round_price(stop),
        "tp1": _round_price(target_for(1.0)),
        "tp2": _round_price(target_for(2.0)),
        "tp3": _round_price(target_for(3.0)),
        "time_exit": (now + timedelta(minutes=config.max_hold_minutes)).isoformat(),
        "quantity": quantity,
        "notional_usd": round(notional, 2),
        "risk_budget_usd": round(risk_budget, 2),
        "effective_risk_usd": round(effective_risk, 2),
        "estimated_round_trip_cost_usd": round(estimated_cost, 2),
        "friction_bp": round(friction_bp, 2),
        "score": signal.score,
        "target_basis": "1R/2R/3R net of modeled round-trip cost",
        "disclaimer": "Simulation ticket; no exchange order is created.",
    }


def evaluate_snapshot(
    snapshot: MarketSnapshot,
    config: Config,
    equity_usd: float | None = None,
    now: datetime | None = None,
) -> Signal:
    primary = snapshot.primary
    trend = snapshot.trend
    required = max(55, config.breakout_lookback + 2, config.volume_lookback + 2)
    if len(primary) < required or len(trend) < 55:
        return Signal(
            symbol=snapshot.symbol,
            state="INSUFFICIENT_DATA",
            side=None,
            score=0,
            signal_id=None,
            candle_close_time=None,
            entry=None,
            spread_bp=None,
            friction_bp=None,
            atr=None,
            atr_pct=None,
            rsi_5m=None,
            volume_ratio=None,
            breakout_level=None,
            funding_bp=snapshot.funding_bp,
            price_change_pct_24h=snapshot.price_change_pct_24h,
            quote_volume_24h=snapshot.quote_volume_24h,
            reasons=(),
            blocked_by=(f"need {required} closed 5m and 55 closed 15m candles",),
        )

    closes = [bar.close for bar in primary]
    trend_closes = [bar.close for bar in trend]
    ema9 = ema(closes, 9)[-1]
    ema21 = ema(closes, 21)[-1]
    trend20 = ema(trend_closes, 20)[-1]
    trend50 = ema(trend_closes, 50)[-1]
    current = primary[-1]
    current_rsi = rsi(closes, 14)[-1]
    current_atr = atr(primary, 14)[-1]
    if current_rsi is None or current_atr is None or current.close <= 0:
        raise ValueError("indicator calculation did not warm up")

    bid, ask = snapshot.bid, snapshot.ask
    midpoint = (bid + ask) / 2.0 if bid > 0 and ask > bid else current.close
    spread_bp = (
        (ask - bid) / midpoint * 10_000.0 if bid > 0 and ask > bid else math.inf
    )
    entry = snapshot.mark if snapshot.mark > 0 else current.close
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
    reward2_bp = 2.0 * current_atr * config.stop_atr / entry * 10_000.0
    entry_drift_atr = abs(entry - current.close) / max(current_atr, 1e-12)

    directional = {
        "LONG": {
            "trend": trend_closes[-1] > trend20 > trend50,
            "alignment": current.close > ema9 > ema21,
            "breakout": current.close > breakout_high,
            "momentum": config.long_rsi_min <= current_rsi <= config.long_rsi_max,
            "candle": close_location >= 0.65,
            "level": breakout_high,
        },
        "SHORT": {
            "trend": trend_closes[-1] < trend20 < trend50,
            "alignment": current.close < ema9 < ema21,
            "breakout": current.close < breakout_low,
            "momentum": config.short_rsi_min <= current_rsi <= config.short_rsi_max,
            "candle": close_location <= 0.35,
            "level": breakout_low,
        },
    }

    def score_for(side: str) -> tuple[int, list[str], list[str]]:
        checks = directional[side]
        score = 0
        reasons: list[str] = []
        blocks: list[str] = []
        if checks["trend"]:
            score += 25
            reasons.append("15m EMA20/50 trend aligned")
        else:
            blocks.append("15m trend not aligned")
        if checks["alignment"]:
            score += 15
            reasons.append("5m EMA9/21 aligned")
        else:
            blocks.append("5m EMA alignment missing")
        if checks["breakout"]:
            score += 20
            reasons.append(f"closed-bar {config.breakout_lookback}-bar breakout")
        else:
            blocks.append("no closed-bar breakout")
        if checks["momentum"]:
            score += 10
            reasons.append(f"RSI confirmation {current_rsi:.1f}")
        else:
            blocks.append(f"RSI {current_rsi:.1f} outside confirmation band")
        if volume_ratio >= 1.5:
            score += 15
            reasons.append(f"volume {volume_ratio:.2f}x median")
        elif volume_ratio >= config.min_volume_ratio:
            score += 8
            reasons.append(f"volume {volume_ratio:.2f}x median")
        else:
            blocks.append(f"volume {volume_ratio:.2f}x below gate")
        if checks["candle"]:
            score += 10
            reasons.append("breakout candle closed with directional control")
        else:
            blocks.append("weak candle close location")
        if spread_bp <= config.max_spread_bp:
            score += 5
        else:
            blocks.append(f"spread {spread_bp:.1f}bp above limit")
        if not (config.min_atr_pct <= atr_pct <= config.max_atr_pct):
            blocks.append(f"ATR {atr_pct:.2f}% outside volatility band")
        if reward2_bp < config.min_reward_cost_multiple * friction_bp:
            blocks.append(
                f"2R move {reward2_bp:.1f}bp < "
                f"{config.min_reward_cost_multiple:.0f}x modeled cost"
            )
        if entry_drift_atr > config.max_entry_drift_atr:
            blocks.append(
                f"mark drift {entry_drift_atr:.2f} ATR from signal close"
            )
        return score, reasons, blocks

    choices = {}
    for side in ("LONG", "SHORT"):
        choices[side] = score_for(side)
    side = max(choices, key=lambda item: choices[item][0])
    score, reasons, blocks = choices[side]
    hard_checks = directional[side]
    gate = (
        score >= config.signal_score
        and all(hard_checks[key] for key in ("trend", "alignment", "breakout", "momentum"))
        and volume_ratio >= config.min_volume_ratio
        and spread_bp <= config.max_spread_bp
        and config.min_atr_pct <= atr_pct <= config.max_atr_pct
        and reward2_bp >= config.min_reward_cost_multiple * friction_bp
        and entry_drift_atr <= config.max_entry_drift_atr
    )
    state = side if gate else ("WATCH" if score >= config.watch_score else "NONE")
    signal_id = f"{snapshot.symbol}:{current.close_time}:{side}" if gate else None
    result = Signal(
        symbol=snapshot.symbol,
        state=state,
        side=side if gate else None,
        score=score,
        signal_id=signal_id,
        candle_close_time=current.close_time,
        entry=entry,
        spread_bp=round(spread_bp, 3) if math.isfinite(spread_bp) else None,
        friction_bp=round(friction_bp, 3),
        atr=current_atr,
        atr_pct=round(atr_pct, 3),
        rsi_5m=round(current_rsi, 2),
        volume_ratio=round(volume_ratio, 2),
        breakout_level=directional[side]["level"],
        funding_bp=round(snapshot.funding_bp, 3),
        price_change_pct_24h=round(snapshot.price_change_pct_24h, 2),
        quote_volume_24h=round(snapshot.quote_volume_24h, 2),
        reasons=tuple(reasons),
        blocked_by=tuple(blocks),
    )
    if gate:
        ticket = build_ticket(
            result,
            config,
            equity_usd or config.starting_equity_usd,
            now=now,
        )
        result = Signal(**{**asdict(result), "ticket": ticket})
    return result
