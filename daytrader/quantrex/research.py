"""Deterministic historical evaluation and fail-closed promotion gates.

This module evaluates already-simulated Quantrex trade rows. It deliberately
does not reuse the legacy CARRY-DAY backtest or claim that kline-only results
contain historical spread, depth, funding, or queue truth.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class EvaluationWindow:
    name: str
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    embargo_ms: int
    untouched: bool = False


def anchored_purged_windows(
    timestamps: Sequence[int],
    *,
    validation_folds: int = 3,
    untouched_fraction: float = 0.20,
    embargo_ms: int = 2 * 60 * 60 * 1000,
) -> tuple[EvaluationWindow, ...]:
    """Create chronological anchored folds plus one untouched final window."""
    if validation_folds < 1:
        raise ValueError("validation_folds must be positive")
    ordered = sorted(set(int(value) for value in timestamps))
    if len(ordered) < (validation_folds + 2) * 2:
        raise ValueError("insufficient timestamps for purged walk-forward")
    untouched_size = max(1, math.ceil(len(ordered) * untouched_fraction))
    development = ordered[:-untouched_size]
    untouched = ordered[-untouched_size:]
    block = max(1, len(development) // (validation_folds + 1))
    windows: list[EvaluationWindow] = []
    for index in range(validation_folds):
        test_start_index = block * (index + 1)
        test_end_index = block * (index + 2) if index + 1 < validation_folds else len(development)
        test = development[test_start_index:test_end_index]
        if not test:
            continue
        train_candidates = [value for value in development[:test_start_index] if value < test[0] - embargo_ms]
        if not train_candidates:
            raise ValueError("embargo removes entire anchored training window")
        windows.append(
            EvaluationWindow(
                name=f"WF{index + 1}",
                train_start=development[0],
                train_end=train_candidates[-1],
                test_start=test[0],
                test_end=test[-1],
                embargo_ms=embargo_ms,
            )
        )
    untouched_train = [value for value in development if value < untouched[0] - embargo_ms]
    if not untouched_train:
        raise ValueError("embargo removes final OOS training history")
    windows.append(
        EvaluationWindow(
            name="UNTOUCHED_OOS",
            train_start=development[0],
            train_end=untouched_train[-1],
            test_start=untouched[0],
            test_end=untouched[-1],
            embargo_ms=embargo_ms,
            untouched=True,
        )
    )
    return tuple(windows)


def bootstrap_mean_ci(values: Sequence[float], *, samples: int = 2_000, seed: int = 0) -> tuple[float, float] | None:
    if not values:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    return means[int(samples * 0.025)], means[min(samples - 1, int(samples * 0.975))]


def _max_drawdown(values: Iterable[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def summarize_trade_rows(rows: Sequence[dict], *, bootstrap_samples: int = 2_000) -> dict:
    ordered = sorted(rows, key=lambda row: (int(row["entry_time"]), str(row.get("symbol", ""))))
    values = [float(row["net_r"]) for row in ordered]
    bps = [float(row.get("net_bps", 0.0)) for row in ordered]
    wins = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    ci = bootstrap_mean_ci(values, samples=bootstrap_samples)
    group_pnl: dict[str, float] = {}
    for row in ordered:
        key = f"{row.get('symbol', 'UNKNOWN')}:{row.get('session', 'UNKNOWN')}"
        group_pnl[key] = group_pnl.get(key, 0.0) + float(row["net_r"])
    positive_total = sum(max(0.0, value) for value in group_pnl.values())
    concentration = max((max(0.0, value) / positive_total for value in group_pnl.values()), default=0.0) if positive_total else 0.0
    return {
        "opportunities": len(ordered),
        "expectancy_net_r": statistics.fmean(values) if values else None,
        "expectancy_net_bps": statistics.fmean(bps) if bps else None,
        "bootstrap_95pct_net_r": list(ci) if ci else None,
        "profit_factor": wins / losses if losses else (None if not wins else math.inf),
        "max_drawdown_r": _max_drawdown(values),
        "tail_loss_r_p05": sorted(values)[max(0, math.ceil(len(values) * 0.05) - 1)] if values else None,
        "turnover_usd": sum(float(row.get("turnover_usd", 0.0)) for row in ordered),
        "modeled_cost_usd": sum(float(row.get("modeled_cost_usd", 0.0)) for row in ordered),
        "max_symbol_session_profit_concentration": concentration,
        "volatility_regimes": len({str(row.get("volatility_regime", "UNKNOWN")) for row in ordered}),
    }


def promotion_verdict(
    oos: dict,
    *,
    doubled_slippage_expectancy_r: float | None,
    delayed_entry_expectancy_r: float | None,
    integrity_defects: dict[str, int],
) -> dict:
    """Apply the frozen historical gate; missing evidence is a failed check."""
    checks = {
        "at_least_150_oos_opportunities": int(oos.get("opportunities") or 0) >= 150,
        "at_least_three_volatility_regimes": int(oos.get("volatility_regimes") or 0) >= 3,
        "positive_net_expectancy": (oos.get("expectancy_net_r") or 0.0) > 0,
        "bootstrap_interval_reported": oos.get("bootstrap_95pct_net_r") is not None,
        "profit_factor_at_least_1_15": oos.get("profit_factor") is not None and float(oos["profit_factor"]) >= 1.15,
        "max_drawdown_no_worse_than_8r": oos.get("max_drawdown_r") is not None and float(oos["max_drawdown_r"]) <= 8.0,
        "profit_concentration_at_most_50pct": oos.get("max_symbol_session_profit_concentration") is not None and float(oos["max_symbol_session_profit_concentration"]) <= 0.50,
        "doubled_slippage_non_negative": doubled_slippage_expectancy_r is not None and doubled_slippage_expectancy_r >= 0,
        "one_bar_delay_non_negative": delayed_entry_expectancy_r is not None and delayed_entry_expectancy_r >= 0,
        "zero_integrity_defects": bool(integrity_defects) and all(int(value) == 0 for value in integrity_defects.values()),
    }
    return {"verdict": "GO_FORWARD_PAPER" if all(checks.values()) else "NO_GO", "checks": checks}

