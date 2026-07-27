"""CARRY-1 Stage-1 core measurements (pre-registered, measurement -> gate).

Two controls demanded by the Stage-0 audit before any build:

1. REGIME: extend funding history to ~2 years via startTime pagination
   (Stage 0 had 166 days = one hot window). Gate re-run per year and overall.
2. REAL COST: per-name all-in cost measured from the live order book
   (spread + depth walk for a $5k leg, x2 legs, x2 sides = round trip),
   not venue-level fantasy. Tier-2/3 names are where the carry clustered,
   and where the RV-1 research warned costs are highest.

Pre-registered gate (same doctrine as K3/RV-1, written before the data):
  PASS requires ALL of:
    a) median net >= 2x MEASURED per-name cost, overall and in each regime
       year with >= 10 trades (sign-stability across regimes),
    b) no single symbol contributes > 50% of trades,
    c) >= 4 trades/month overall,
    d) at theta in {2, 3} bp (the Stage-0 survivors).
  Anything less: CARRY-1 joins K3 and RV-1 in the archive. No engine is
  built on a fail. Negative results at equal prominence.
"""
import json
import sys
import time

sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")

import numpy as np
import pandas as pd
from k3 import data

from carry_stage0 import top_symbols, simulate_trades, HOLD_MAX_DAYS

YEARS = 2
LEG_USD = 5_000.0          # depth walk size per leg
THETAS_BP = [2, 3]
DEPTH_SNAPS = 3            # snapshots per name, spaced


def funding_history_paged(symbol, years=YEARS):
    """Paginate /fundingRate backward with endTime to cover `years`."""
    out = []
    end = None
    target_ms = years * 365 * 86400 * 1000
    got_ms = 0
    while got_ms < target_ms:
        params = {"symbol": symbol, "limit": 1000}
        if end is not None:
            params["endTime"] = end
        rows = data._get("/fundingRate", params, 25)
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        got_ms = int(time.time() * 1000) - int(rows[0]["fundingTime"])
        end = int(rows[0]["fundingTime"]) - 1
        if len(rows) < 1000:
            break
    if not out:
        return None
    df = pd.DataFrame({
        "t": pd.to_datetime([int(r["fundingTime"]) for r in out], unit="ms", utc=True),
        "bp": [float(r["fundingRate"]) * 1e4 for r in out],
    }).drop_duplicates("t").sort_values("t").reset_index(drop=True)
    return df if len(df) >= 100 else None


def depth_cost_bp(symbol, leg_usd=LEG_USD, snaps=DEPTH_SNAPS):
    """Measured round-trip cost (bp on notional) for a 2-leg carry:
    walk the book for leg_usd on both sides, x2 legs (perp+hedge assumed
    similar book -> we measure the perp book and double the perp share is
    NOT assumed; hedge leg on spot is usually tighter, so perp-book x2 is
    conservative). Spread + impact, both entry and exit."""
    walks = []
    for _ in range(snaps):
        try:
            j = data._get("/depth", {"symbol": symbol, "limit": 100}, 15)
            bids = [(float(p), float(q)) for p, q in j.get("bids", [])]
            asks = [(float(p), float(q)) for p, q in j.get("asks", [])]
            if not bids or not asks:
                continue
            mid = (bids[0][0] + asks[0][0]) / 2.0

            def walk(levels):
                filled = 0.0
                cost = 0.0
                for p, q in levels:
                    take = min(q * p, leg_usd - filled)
                    cost += take
                    filled += take
                    if filled >= leg_usd - 1e-9:
                        break
                if filled < leg_usd:
                    return None  # book too thin at 100 levels — disqualifying
                # avg fill price relative to mid
                qty_total = sum(min(q * p, leg_usd) / p for p, q in levels[:1])  # placeholder
                # recompute properly:
                filled2 = 0.0
                vwap_num = 0.0
                for p, q in levels:
                    take_usd = min(q * p, leg_usd - filled2)
                    qty = take_usd / p
                    vwap_num += qty * p
                    filled2 += take_usd
                    if filled2 >= leg_usd - 1e-9:
                        break
                vwap = vwap_num / (leg_usd / mid) if False else None
                # simpler: impact bp = weighted avg |price-mid|/mid
                rem = leg_usd
                impact = 0.0
                for p, q in levels:
                    take_usd = min(q * p, rem)
                    impact += abs(p - mid) / mid * take_usd
                    rem -= take_usd
                    if rem <= 1e-9:
                        break
                return impact / leg_usd * 1e4

            buy_bp = walk(asks)
            sell_bp = walk(bids)
            if buy_bp is None or sell_bp is None:
                return None
            spread_bp = (asks[0][0] - bids[0][0]) / mid * 1e4
            # one side one way ~ spread/2 + impact; round trip both legs:
            one_leg_rt = spread_bp + buy_bp + sell_bp
            walks.append(one_leg_rt)
        except Exception:
            continue
        time.sleep(1.0)
    if not walks:
        return None
    return float(np.median(walks)) * 2.0  # x2 legs (perp + hedge), conservative


def main():
    syms = top_symbols()
    print("universe:", syms, flush=True)

    print("\n-- measured book costs (bp round trip, both legs) --", flush=True)
    costs = {}
    for s in syms:
        c = depth_cost_bp(s)
        costs[s] = c
        print(f"  {s:14s} {c if c is not None else 'THIN/NA':>8}", flush=True)

    frames = {}
    for s in syms:
        try:
            df = funding_history_paged(s)
            if df is not None:
                frames[s] = df
        except Exception as e:
            print(f"  {s}: history failed: {e}", flush=True)

    span_days = {s: (df["t"].iloc[-1] - df["t"].iloc[0]).days for s, df in frames.items()}
    print(f"\nhistory depth (days): min={min(span_days.values())} "
          f"med={int(np.median(list(span_days.values())))} max={max(span_days.values())}")

    report = {"costs_bp": costs, "span_days": span_days, "theta": {}}

    for th in THETAS_BP:
        trades = []
        for s, df in frames.items():
            for t in simulate_trades(df, th):
                t["sym"] = s
                t["year"] = t["entry"][:4]
                trades.append(t)
        if not trades:
            continue
        df = pd.DataFrame(trades)
        df["cost_bp"] = df["sym"].map(costs)
        df = df.dropna(subset=["cost_bp"])
        df["net_bp"] = df["gross_bp"] - df["cost_bp"]
        med_net = df["net_bp"].median()
        med_cost2x = (2 * df["cost_bp"]).median()
        per_year = df.groupby("year").agg(
            n=("net_bp", "size"),
            med_net=("net_bp", "median"),
            pct_pos=("net_bp", lambda x: 100 * (x > 0).mean()),
        ).round(2)
        top_sym_share = df["sym"].value_counts(normalize=True).iloc[0]
        months = max(1.0, sum(span_days.get(s, 0) for s in df["sym"].unique()) / 30.44 / max(1, len(df["sym"].unique())))
        # simpler: overall months from min to max entry
        months = max(1.0, (pd.Timestamp(df["entry"].max()) - pd.Timestamp(df["entry"].min())).days / 30.44)
        gate = {
            "n": int(len(df)),
            "trades_per_month": round(len(df) / months, 1),
            "median_gross_bp": round(float(df["gross_bp"].median()), 2),
            "median_cost_bp": round(float(df["cost_bp"].median()), 2),
            "median_net_bp": round(float(med_net), 2),
            "median_2x_cost_bp": round(float(med_cost2x), 2),
            "clears_2x_median": bool(med_net >= med_cost2x),
            "pct_net_positive": round(float(100 * (df["net_bp"] > 0).mean()), 1),
            "top_symbol_share": round(float(top_sym_share), 2),
            "concentration_ok": bool(top_sym_share <= 0.50),
            "per_year": per_year.to_dict(),
        }
        yrs = df.groupby("year").filter(lambda g: len(g) >= 10).groupby("year")
        gate["sign_stable_years"] = bool(
            len(yrs) > 0 and all(g["net_bp"].median() >= (2 * g["cost_bp"]).median()
                                 for _, g in yrs)
        ) if len(df) else False
        gate["PASS"] = bool(
            gate["clears_2x_median"] and gate["concentration_ok"]
            and gate["trades_per_month"] >= 4 and gate["sign_stable_years"]
        )
        report["theta"][th] = gate
        print(f"\n=== theta={th}bp ===")
        print(json.dumps(gate, indent=2, default=str))

    with open("/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry/carry_stage1.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print("\nwrote carry_stage1.json")


if __name__ == "__main__":
    main()
