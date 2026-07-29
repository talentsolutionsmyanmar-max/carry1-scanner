#!/usr/bin/env python3
"""Conservative historical screen for the CARRY-DAY signal gate.

This is a research aid, not a validator. It uses current symbol selection,
assumed historical spread, and Binance klines (which do not contain queue or
book-impact data). If stop and target are both touched in one bar, stop wins.
"""

from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if __package__ in {None, ""}:
    sys.path.insert(0, str(ROOT.parent))
    from daytrader.config import Config
    from daytrader.engine import evaluate_snapshot
    from daytrader.market import BinanceFuturesClient, Candle, MarketSnapshot
else:
    from .config import Config
    from .engine import evaluate_snapshot
    from .market import BinanceFuturesClient, Candle, MarketSnapshot


FIVE_MINUTES_MS = 5 * 60 * 1000
FIFTEEN_MINUTES_MS = 15 * 60 * 1000
ONE_HOUR_MS = 60 * 60 * 1000


def aggregate_bars(
    bars: tuple[Candle, ...], bucket_ms: int, expected_count: int
) -> tuple[Candle, ...]:
    buckets: dict[int, list[Candle]] = {}
    for bar in bars:
        key = bar.open_time // bucket_ms
        buckets.setdefault(key, []).append(bar)
    out = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda item: item.open_time)
        if len(group) != expected_count:
            continue
        if group[-1].close_time - group[0].open_time + 1 < bucket_ms:
            continue
        out.append(
            Candle(
                open_time=group[0].open_time,
                close_time=group[-1].close_time,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
                quote_volume=sum(item.quote_volume for item in group),
                trades=sum(item.trades for item in group),
            )
        )
    return tuple(out)


def simulate_symbol(
    symbol: str,
    bars: tuple[Candle, ...],
    config: Config,
    assumed_spread_bp: float,
) -> list[dict]:
    trend_bars = aggregate_bars(bars, FIFTEEN_MINUTES_MS, 3)
    higher_bars = aggregate_bars(bars, ONE_HOUR_MS, 12)
    trend_closes = [bar.close_time for bar in trend_bars]
    higher_closes = [bar.close_time for bar in higher_bars]
    trades = []
    i = max(config.candle_limit, 201 * 12)
    max_hold_bars = max(1, config.max_hold_minutes // 5)
    while i < len(bars) - 1:
        bar = bars[i]
        trend_end = bisect.bisect_right(trend_closes, bar.close_time)
        higher_end = bisect.bisect_right(higher_closes, bar.close_time)
        if trend_end < 55 or higher_end < 201:
            i += 1
            continue
        half_spread = assumed_spread_bp / 20_000.0
        snapshot = MarketSnapshot(
            symbol=symbol,
            primary=bars[max(0, i - config.candle_limit + 1) : i + 1],
            trend=trend_bars[max(0, trend_end - config.candle_limit) : trend_end],
            higher=higher_bars[max(0, higher_end - config.candle_limit) : higher_end],
            bid=bar.close * (1.0 - half_spread),
            ask=bar.close * (1.0 + half_spread),
            mark=bar.close,
            funding_bp=0.0,
            price_change_pct_24h=0.0,
            quote_volume_24h=0.0,
            captured_at=datetime.fromtimestamp(
                bar.close_time / 1000, tz=timezone.utc
            ).isoformat(),
        )
        signal = evaluate_snapshot(
            snapshot,
            config,
            equity_usd=config.starting_equity_usd,
            now=datetime.fromtimestamp(bar.close_time / 1000, tz=timezone.utc),
        )
        if signal.state != "LIVE" or not signal.ticket:
            i += 1
            continue

        ticket = signal.ticket
        stop = float(ticket["stop"])
        target = float(ticket["tp2"])
        entry = float(ticket["entry"])
        direction = 1.0 if signal.side == "LONG" else -1.0
        exit_price = bars[min(i + max_hold_bars, len(bars) - 1)].close
        reason = "TIME_EXIT"
        exit_index = min(i + max_hold_bars, len(bars) - 1)
        for j in range(i + 1, min(len(bars), i + max_hold_bars + 1)):
            future = bars[j]
            stop_hit = (
                future.low <= stop if signal.side == "LONG" else future.high >= stop
            )
            target_hit = (
                future.high >= target
                if signal.side == "LONG"
                else future.low <= target
            )
            if stop_hit:
                exit_price = stop
                reason = "STOP"
                exit_index = j
                break
            if target_hit:
                exit_price = target
                reason = "TP2"
                exit_index = j
                break

        quantity = float(ticket["quantity"])
        gross_usd = direction * (exit_price - entry) * quantity
        net_usd = gross_usd - float(ticket["estimated_round_trip_cost_usd"])
        effective_risk = max(float(ticket["effective_risk_usd"]), 1e-9)
        trades.append(
            {
                "symbol": symbol,
                "side": signal.side,
                "entry_time": bar.close_time,
                "exit_time": bars[exit_index].close_time,
                "entry": entry,
                "exit": exit_price,
                "exit_reason": reason,
                "score": signal.score,
                "net_usd": round(net_usd, 2),
                "net_r": round(net_usd / effective_risk, 4),
            }
        )
        i = exit_index + 1
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "median_net_r": None,
            "mean_net_r": None,
            "profit_factor": None,
            "max_drawdown_r": None,
        }
    values = [float(item["net_r"]) for item in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "trades": len(trades),
        "win_rate_pct": round(100.0 * len(wins) / len(trades), 2),
        "median_net_r": round(statistics.median(values), 4),
        "mean_net_r": round(statistics.mean(values), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_r": round(max_drawdown, 3),
        "total_net_r": round(sum(values), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT", help="comma-separated"
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--assumed-spread-bp", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = Config.from_env()
    client = BinanceFuturesClient(config)
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - args.days * 86_400_000
    all_trades = []
    per_symbol = {}
    for symbol in [item.strip().upper() for item in args.symbols.split(",") if item.strip()]:
        print(f"fetching {symbol} {args.days}d of 5m bars…", file=sys.stderr)
        bars = client.historical_klines(
            symbol, config.primary_interval, start_ms, now_ms
        )
        trades = simulate_symbol(symbol, bars, config, args.assumed_spread_bp)
        all_trades.extend(trades)
        per_symbol[symbol] = {
            "bars": len(bars),
            "summary": summarize(trades),
        }
    all_trades.sort(key=lambda item: item["entry_time"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": args.days,
        "symbols": list(per_symbol),
        "assumed_spread_bp": args.assumed_spread_bp,
        "config": config.public_dict(),
        "per_symbol": per_symbol,
        "combined": summarize(all_trades),
        "limitations": [
            "current symbol selection can introduce survivorship bias",
            "klines do not contain historical order-book impact or queue position",
            "spread is assumed and fees/slippage are configurable estimates",
            "when stop and target occur in one candle, stop is counted first",
            "the live paper governor's portfolio trade caps and cooldown are not simulated",
            "this report is exploratory and not out-of-sample validation",
        ],
        "trades": all_trades,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2))
        print(f"wrote {args.output}", file=sys.stderr)
        print(
            json.dumps(
                {
                    "generated_at": report["generated_at"],
                    "period_days": report["period_days"],
                    "per_symbol": report["per_symbol"],
                    "combined": report["combined"],
                    "limitations": report["limitations"],
                },
                indent=2,
            )
        )
    else:
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
