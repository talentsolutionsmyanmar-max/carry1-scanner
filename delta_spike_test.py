"""Jose Donato alert archetypes — measured on our own 1h panel.

cryexc_alerts fires two public event types we CAN reconstruct from klines:
  DELTA SPIKE: large signed taker flow on one bar (his: >$30M net).
  Our proxy: signed delta = 2*taker_buy - volume (base) x close (quote),
  per 1h bar, 20 USDT perps, ~11 months (cached).

  (LIQ CLUSTER: historical liquidation prints are not available from any
  free public API — Binance forceOrders is realtime-only. Stated as a data
  gap, not silently skipped.)

Pre-registered read:
  spike_long  = bar delta z >= +2 (z vs rolling 720h) OR delta > rolling p99
  spike_short = z <= -2 (his RED prints)
  forward +4h / +8h close-to-close returns vs ALL-BARS baseline.
  His implied thesis: buy-spikes continue UP, sell-spikes continue DOWN.
  Report n, mean, MEDIAN, NW-t. Median is the bar. Negatives published.
"""
import json
import sys

sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")

import numpy as np
import pandas as pd
from k3 import data
from carry_stage0 import top_symbols


def nw_t(x, lag=5):
    x = np.asarray(x, float)
    n = len(x)
    if n < lag + 10:
        return np.nan
    m = x.mean()
    u = x - m
    var = u @ u / n
    for l in range(1, lag + 1):
        var += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l] / n)
    return m / np.sqrt(var / n) if var > 0 else np.nan


def st(x):
    x = np.asarray(x, float)
    if len(x) < 30:
        return None
    return {"n": int(len(x)), "mean": round(float(np.mean(x)), 2),
            "median": round(float(np.median(x)), 2), "t": round(float(nw_t(x)), 2)}


def main():
    syms = top_symbols()
    ev = {"buy_z2": [], "sell_z2": [], "buy_p99": [], "sell_p99": [], "all": []}
    for s in syms:
        try:
            df = data.klines_history(s, "1h", 8000)
        except Exception:
            continue
        if len(df) < 3000:
            continue
        c = df["close"].values
        v = df["volume"].values
        tb = df["taker_buy"].values
        delta = (2 * tb - v) * c  # signed quote delta per bar
        ds = pd.Series(delta, index=df["timestamp"])
        mu = ds.rolling(720, min_periods=200).mean()
        sd = ds.rolling(720, min_periods=200).std()
        z = ((ds - mu) / sd.replace(0, np.nan)).values
        p99_hi = ds.rolling(720, min_periods=200).quantile(0.99).values
        p99_lo = ds.rolling(720, min_periods=200).quantile(0.01).values
        for i in range(200, len(df) - 9):
            if np.isnan(z[i]):
                continue
            f4 = (c[i + 4] / c[i] - 1.0) * 1e4
            f8 = (c[i + 8] / c[i] - 1.0) * 1e4
            ev["all"].append((f4, f8))
            if z[i] >= 2:
                ev["buy_z2"].append((f4, f8))
            elif z[i] <= -2:
                ev["sell_z2"].append((f4, f8))
            if not np.isnan(p99_hi[i]) and delta[i] >= p99_hi[i]:
                ev["buy_p99"].append((f4, f8))
            elif not np.isnan(p99_lo[i]) and delta[i] <= p99_lo[i]:
                ev["sell_p99"].append((f4, f8))

    print(f"panel events: all-bars n={len(ev['all'])}")
    out = {}
    for k in ("buy_z2", "sell_z2", "buy_p99", "sell_p99", "all"):
        f4 = [x[0] for x in ev[k]]
        f8 = [x[1] for x in ev[k]]
        out[k] = {"4h": st(f4), "8h": st(f8)}
        if out[k]["4h"]:
            a, b = out[k]["4h"], out[k]["8h"]
            print(f"{k:10s} n={a['n']:>6} | 4h mean {a['mean']:>7.2f} med {a['median']:>7.2f} t {a['t']:>5.2f}"
                  f" | 8h mean {b['mean']:>7.2f} med {b['median']:>7.2f} t {b['t']:>5.2f}")

    print("\nHis thesis (buy spikes -> UP): 4h contrast buy_z2 minus all-bars mean ="
          f" {out['buy_z2']['4h']['mean'] - out['all']['4h']['mean']:.2f}bp"
          if out["buy_z2"]["4h"] and out["all"]["4h"] else "insufficient")
    with open("/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry/delta_spike_test.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote delta_spike_test.json")


if __name__ == "__main__":
    main()
