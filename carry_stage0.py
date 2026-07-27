"""CARRY-1 Stage-0 arithmetic — funding-rate carry, conditional entry.

Structure that neither closed track tested: income from a PAYMENT STREAM
(funding), not price prediction. Short the perp, hedge the delta away
(spot or offsetting perp), collect funding while it prints positive.

Pre-registered measurement protocol (measurement, NOT a validation study):
  - Universe: top-20 USDT perps by 24h quote volume (live snapshot; the
    point-in-time caveat from RV-1 applies and is restated in the report).
  - History: up to 1000 funding prints per symbol (~333d at 8h epochs).
  - SIGNAL: at funding epoch i, realized rate_i >= theta bp (rate_i is known
    at the epoch boundary; predicted rate is published ~1min ahead live).
  - POSITION: short perp + delta hedge, entered at epoch i boundary.
  - HOLD: collect epochs i+1..j while rate > 0; exit at the first epoch
    whose rate <= 0 (not collected); hard exit after 5 calendar days.
  - GROSS carry = sum of collected prints (bp on notional).
  - NET = gross - venue round trip (both legs taker: Binance 22bp, MEXC 10bp).
  - GATE (pre-registered): some theta with MEDIAN net >= 2x cost AND
    >= 4 trades/month across the universe. Median, not mean. Breakeven is
    not validation.

Known omissions, stated honestly:
  - Basis risk: spot-perp basis can move against the hedge during the hold.
    Not in funding data; needs mark-vs-index series (Stage 2 if gated in).
  - Funding-flip slippage: live exits use the predicted print; realized can
    differ by a fraction of a bp. Small vs the 2x bar, flagged anyway.
  - Universe is a live snapshot, not point-in-time (same caveat as RV-1).

Doctrine: measurement only; negative results at equal prominence.
"""
import json
import sys

sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")

import numpy as np
import pandas as pd
from k3 import data
from k3.config import STABLE_LIKE

TOP_N = 20
HOLD_MAX_DAYS = 5.0
THETAS_BP = [1, 2, 3, 5, 7, 10, 15]
COSTS = {"binance": 22.0, "mexc": 10.0}


def top_symbols(n=TOP_N):
    ticks = data._get("/ticker/24hr")
    info = {s["symbol"]: s for s in data._get("/exchangeInfo").get("symbols", [])}
    rows = []
    for t in ticks:
        sym = t.get("symbol", "")
        meta = info.get(sym)
        if not meta or meta.get("status") != "TRADING" or meta.get("contractType") != "PERPETUAL":
            continue
        if not sym.endswith("USDT") or meta.get("baseAsset") in STABLE_LIKE:
            continue
        rows.append((sym, float(t.get("quoteVolume") or 0)))
    rows.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in rows[:n]]


def funding_frame(symbol):
    rows = data._get("/fundingRate", {"symbol": symbol, "limit": 1000}, 20)
    if not isinstance(rows, list) or len(rows) < 100:
        return None
    df = pd.DataFrame({
        "t": pd.to_datetime([int(r["fundingTime"]) for r in rows], unit="ms", utc=True),
        "bp": [float(r["fundingRate"]) * 1e4 for r in rows],
    }).drop_duplicates("t").sort_values("t").reset_index(drop=True)
    return df


def simulate_trades(df, theta_bp):
    """Walk the print series; emit one trade per signal, non-overlapping."""
    t = df["t"].values
    r = df["bp"].values
    trades = []
    i = 0
    n = len(df)
    while i < n - 1:
        if r[i] >= theta_bp:
            gross = 0.0
            epochs = 0
            j = i + 1
            entry_t = pd.Timestamp(t[i])
            while j < n:
                dt_days = (pd.Timestamp(t[j]) - entry_t).total_seconds() / 86400.0
                if dt_days > HOLD_MAX_DAYS or r[j] <= 0:
                    break
                gross += r[j]
                epochs += 1
                j += 1
            trades.append({
                "entry": str(entry_t),
                "gross_bp": gross,
                "epochs": epochs,
                "days": round((pd.Timestamp(t[min(j, n - 1)]) - entry_t).total_seconds() / 86400.0, 2),
            })
            i = max(j, i + 1)  # non-overlapping
        else:
            i += 1
    return trades


def main():
    syms = top_symbols()
    print("universe:", syms, flush=True)
    frames = {}
    for s in syms:
        try:
            df = funding_frame(s)
            if df is not None:
                frames[s] = df
        except Exception as e:
            print(f"  {s}: funding fetch failed: {e}")
    if not frames:
        print("no funding data"); return

    span = {s: (df["t"].iloc[0], df["t"].iloc[-1], len(df)) for s, df in frames.items()}
    days = max((v[1] - v[0]).total_seconds() / 86400.0 for v in span.values())
    months = days / 30.44
    print(f"history: up to {days:.0f} days ({months:.1f} months), "
          f"{len(frames)} symbols", flush=True)

    # baseline stats: unconditional funding distribution
    allbp = np.concatenate([df["bp"].values for df in frames.values()])
    print(f"\nunconditional 8h funding (bp): mean={allbp.mean():.2f} "
          f"median={np.median(allbp):.2f} p75={np.percentile(allbp, 75):.2f} "
          f"p90={np.percentile(allbp, 90):.2f} p99={np.percentile(allbp, 99):.2f} "
          f"%>0={100 * (allbp > 0).mean():.1f}%")

    results = {}
    print("\n=== CONDITIONAL CARRY (per trade, bp on notional) ===")
    hdr = f"{'theta':>6} {'trades':>7} {'tr/mo':>6} {'medEpo':>7} {'medGross':>9} {'meanGross':>10}"
    for v, c in COSTS.items():
        hdr += f" | {v}: medNet {c:.0f}c 2x? "
    print(hdr)
    for th in THETAS_BP:
        trades = []
        for s, df in frames.items():
            trades.extend(simulate_trades(df, th))
        if len(trades) < 20:
            print(f"{th:>6} {len(trades):>7}  (too few)")
            continue
        g = np.array([t["gross_bp"] for t in trades])
        ep = np.array([t["epochs"] for t in trades])
        row = {
            "theta_bp": th,
            "trades": len(trades),
            "trades_per_month": round(len(trades) / months, 1),
            "median_epochs": float(np.median(ep)),
            "median_gross_bp": round(float(np.median(g)), 2),
            "mean_gross_bp": round(float(g.mean()), 2),
            "p75_gross_bp": round(float(np.percentile(g, 75)), 2),
            "venues": {},
        }
        line = f"{th:>6} {len(trades):>7} {len(trades)/months:>6.1f} {np.median(ep):>7.1f} {np.median(g):>9.2f} {g.mean():>10.2f}"
        for v, c in COSTS.items():
            net = g - c
            med_net = float(np.median(net))
            ok = "PASS" if med_net >= 2 * c else "fail"
            row["venues"][v] = {
                "median_net_bp": round(med_net, 2),
                "pct_positive": round(float(100 * (net > 0).mean()), 1),
                "gate_2x": ok,
            }
            line += f" | {med_net:>8.2f} {ok:>4}"
        print(line)
        results[f"theta_{th}"] = row

    with open("/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry/carry_stage0.json", "w") as f:
        json.dump({"universe": syms, "months": months, "results": results,
                   "unconditional": {"mean": float(allbp.mean()), "median": float(np.median(allbp)),
                                     "p75": float(np.percentile(allbp, 75)),
                                     "p90": float(np.percentile(allbp, 90)),
                                     "pct_pos": float(100 * (allbp > 0).mean())}}, f, indent=2)
    print("\nwrote product3-carry/carry_stage0.json")


if __name__ == "__main__":
    main()
