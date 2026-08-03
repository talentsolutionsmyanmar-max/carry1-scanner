"""Stateless public-market evaluation for the Quantrex dashboard."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone

from daytrader.market import MarketSnapshot

from .config import QuantrexConfig
from .contracts import CostEstimate, Side, SignalEvent
from .data import DataContractError, parse_capture_ms, validate_snapshot
from .risk import RiskKernel, RiskState
from .shadow import NoSubmitVenue
from .strategies import breakout_signal, qsr_signal


def _depth_at_size(signal: SignalEvent, snapshot: MarketSnapshot, quantity: float) -> tuple[bool, float]:
    levels = snapshot.ask_depth if signal.side is Side.LONG else snapshot.bid_depth
    remaining = quantity
    notional = 0.0
    for price, available in levels:
        filled = min(remaining, available)
        notional += filled * price
        remaining -= filled
        if remaining <= 1e-12:
            break
    if remaining > 1e-12 or quantity <= 0:
        return False, 0.0
    vwap = notional / quantity
    reference = snapshot.ask if signal.side is Side.LONG else snapshot.bid
    impact = max(0.0, (vwap - reference) * quantity) if signal.side is Side.LONG else max(0.0, (reference - vwap) * quantity)
    return True, impact


def _costs(signal: SignalEvent, snapshot: MarketSnapshot, equity: float) -> tuple[CostEstimate, bool]:
    stop_distance = max(abs(signal.entry_quote - signal.stop), 1e-12)
    quantity = equity * 0.001 / stop_distance
    notional = quantity * signal.entry_quote
    depth_available, impact_usd = _depth_at_size(signal, snapshot, quantity)
    return CostEstimate(
        fee_usd=notional * 0.001,  # frozen primary taker: 5bp per side
        spread_usd=(snapshot.ask - snapshot.bid) * quantity,
        impact_usd=impact_usd,
        slippage_usd=notional * 0.0004,  # conservative 2bp per side placeholder
        funding_usd=0.0,
        latency_usd=0.0,
        missed_fill_usd=0.0,
    ), depth_available


def _candidate(
    signal: SignalEvent,
    snapshot: MarketSnapshot,
    config: QuantrexConfig,
    *,
    depth_available: bool | None,
    observed_at_ms: int,
    equity_usd: float,
) -> dict:
    costs, measured_depth_available = _costs(signal, snapshot, equity_usd)
    if depth_available is None:
        depth_available = measured_depth_available
    provisional_quantity = (
        config.starting_equity_usd * config.risk_per_trade_fraction
        / max(abs(signal.entry_quote - signal.stop), 1e-12)
    )
    cost_per_unit = costs.round_trip_usd / provisional_quantity
    risk_distance = abs(signal.entry_quote - signal.stop)
    target_distance = config.target_r * (risk_distance + cost_per_unit) + cost_per_unit
    target = (
        signal.entry_quote + target_distance
        if signal.side is Side.LONG
        else signal.entry_quote - target_distance
    )
    signal = replace(signal, target=target)
    feed_age = max(0.0, (observed_at_ms - parse_capture_ms(snapshot.captured_at)) / 1000)
    decision = RiskKernel(
        config,
        RiskState(equity_usd=equity_usd, day_start_equity_usd=equity_usd),
    ).decide(
        signal,
        costs,
        depth_available=depth_available,
        feed_age_seconds=feed_age,
    )
    shadow = NoSubmitVenue().prepare(signal, decision) if decision.accepted else None
    direction = 1.0 if signal.side is Side.LONG else -1.0
    if signal.book.value == "QSR_1_DAY" and signal.strategy_version == config.qsr_version:
        targets = {
            name.lower(): signal.entry_quote + direction * (multiple * (risk_distance + cost_per_unit) + cost_per_unit)
            for name, multiple, _fraction in config.qsr_exit_legs
        }
        exit_plan = [
            {"name": name, "r": multiple, "fraction": fraction, "target": targets[name.lower()]}
            for name, multiple, fraction in config.qsr_exit_legs
        ]
    else:
        targets = {"tp1": signal.target, "tp2": None, "tp3": None}
        exit_plan = [{"name": "TP", "r": config.target_r, "fraction": 1.0, "target": signal.target}]
    return {
        "book": signal.book.value,
        "strategy_version": signal.strategy_version,
        "symbol": signal.symbol,
        "side": signal.side.value,
        "state": "SHADOW_READY" if decision.accepted else "BLOCKED",
        "entry": signal.entry_quote,
        "stop": signal.stop,
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],
        "exit_plan": exit_plan,
        "time_exit": signal.time_exit,
        "kill_zone": signal.session_arm,
        "reference_level": signal.reference_level,
        "risk_usd": decision.risk_usd,
        "quantity": decision.quantity,
        "costs": asdict(costs) | {"round_trip_usd": costs.round_trip_usd},
        "blocked_by": list(decision.reasons),
        "evidence": list(signal.evidence),
        "idempotency_key": signal.idempotency_key,
        "shadow_order": asdict(shadow) if shadow else None,
        "signal_event": {
            **asdict(signal),
            "book": signal.book.value,
            "side": signal.side.value,
        },
        "depth_available": depth_available,
        "feed_age_seconds": feed_age,
        "chart_bars": [
            {
                "time": bar.close_time // 1000,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
            }
            for bar in snapshot.trend[-96:]
        ],
    }


def evaluate_public_snapshot(
    snapshot: MarketSnapshot,
    config: QuantrexConfig,
    *,
    observed_at: datetime | None = None,
    depth_available: bool | None = None,
    equity_usd: float | None = None,
) -> dict:
    observed_at = observed_at or datetime.now(timezone.utc)
    observed_at_ms = int(observed_at.timestamp() * 1000)
    equity_usd = config.starting_equity_usd if equity_usd is None else float(equity_usd)
    try:
        input_hash = validate_snapshot(snapshot, config, observed_at_ms)
    except DataContractError as exc:
        return {
            "symbol": snapshot.symbol,
            "status": "DATA_BLOCKED",
            "blocked_by": [str(exc)],
            "candidates": [],
        }
    chart_bars = [
        {
            "time": bar.close_time // 1000,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
        }
        for bar in snapshot.trend[-96:]
    ]
    signals = [
        qsr_signal(
            snapshot.symbol,
            snapshot.trend,
            snapshot.higher,
            snapshot.bid,
            snapshot.ask,
            config,
        ),
        breakout_signal(
            snapshot.symbol,
            snapshot.trend,
            snapshot.higher,
            snapshot.bid,
            snapshot.ask,
            config,
        ),
    ]
    candidates = [
        _candidate(
            signal,
            snapshot,
            config,
            depth_available=depth_available,
            observed_at_ms=observed_at_ms,
            equity_usd=equity_usd,
        )
        for signal in signals
        if signal is not None
    ]
    return {
        "symbol": snapshot.symbol,
        "status": "CANDIDATE" if candidates else "NO_SIGNAL",
        "input_hash": input_hash,
        "chart_bars": chart_bars,
        "candidates": candidates,
        "flat_control": {
            "book": "NO_TRADE",
            "position": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
        },
    }
