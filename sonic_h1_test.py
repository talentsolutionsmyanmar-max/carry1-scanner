"""Sonictraders close-location hypothesis — measured on our own panel.

His method claim (public channel, 2026-07-27):
  "A candle's wick is the argument. The close is the verdict."
  A shooting star is a VALID short signal only if the confirmation candle
  CLOSES in the BOTTOM THIRD of the shooting star's range. Middle-third
  close = pause, not reversal ("shorts who entered here get run over").

Doctrine: this is a measurement, not an endorsement. Pre-registered read:
  - shooting star on 1h bars: upper wick >= 2x body, body in lower half
  - confirmation = next bar's close location within the star's range
  - buckets: bottom third (his VALID) / middle third / top third
  - forward return +4h and +8h from confirmation close, pooled across
    20 USDT perps, ~11 months (cached k3 panel)
  - report n, mean, MEDIAN, NW-t per bucket. If bottom-third doesn't beat
    middle/third with significance, the claim is folklore on crypto 1h.
"""
import sys

sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")

import numpy as np
import pandas as pd
from k3 import data
from carry_stage0 import top_symbols  # reuse live top-20


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


def main():
    syms = top_symbols()
    events = []
    for s in syms:
        try:
            df = data.klines_history(s, "1h", 8000)
        except Exception:
            continue
        if len(df) < 3000:
            continue
        o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
        body = np.abs(c - o)
        rng = h - l
        rng[rng == 0] = np.nan
        upper = h - np.maximum(o, c)
        # shooting star: upper wick >= 2x body, body in lower half of range
        star = (upper >= 2 * np.maximum(body, 1e-12)) & ((np.maximum(o, c) - l) / rng <= 0.5)
        for i in np.where(star)[0]:
            if i + 9 >= len(df):
                continue
            r = h[i] - l[i]
            if r <= 0 or np.isnan(r):
                continue
            loc = (c[i + 1] - l[i]) / r  # confirmation close location in star range
            fwd4 = (c[i + 5] / c[i + 1] - 1.0) * 1e4
            fwd8 = (c[i + 9] / c[i + 1] - 1.0) * 1e4
            events.append((s, loc, fwd4, fwd8))
    df = pd.DataFrame(events, columns=["sym", "loc", "fwd4", "fwd8"])
    print(f"events: {len(df)} shooting stars with confirmation bar")

    buckets = {
        "bottom third (his VALID)": df[(df["loc"] >= 0) & (df["loc"] < 1 / 3)],
        "middle third (his pause)": df[(df["loc"] >= 1 / 3) & (df["loc"] < 2 / 3)],
        "top third": df[(df["loc"] >= 2 / 3) & (df["loc"] <= 1.0)],
        "outside range": df[(df["loc"] < 0) | (df["loc"] > 1)],
    }
    out = {}
    for label, sub in buckets.items():
        if len(sub) < 30:
            print(f"{label}: n={len(sub)} (too few)")
            continue
        row = {}
        for hz in ("fwd4", "fwd8"):
            x = sub[hz].values
            row[hz] = {"n": int(len(x)), "mean": round(float(np.mean(x)), 2),
                       "median": round(float(np.median(x)), 2),
                       "t": round(float(nw_t(x)), 2)}
        out[label] = row
        print(f"{label:28s} n={len(sub):>5} | 4h: mean {row['fwd4']['mean']:>7.2f} med {row['fwd4']['median']:>7.2f} t {row['fwd4']['t']:>5.2f}"
              f" | 8h: mean {row['fwd8']['mean']:>7.2f} med {row['fwd8']['median']:>7.2f} t {row['fwd8']['t']:>5.2f}")

    # decisive contrast: bottom vs middle at 4h (his exact claim)
    b = buckets["bottom third (his VALID)"]["fwd4"].values
    m = buckets["middle third (his pause)"]["fwd4"].values
    if len(b) > 30 and len(m) > 30:
        print(f"\nCONTRAST bottom-minus-middle 4h: {b.mean() - m.mean():.2f}bp mean "
              f"(his claim: bottom MORE negative)")
        print(f"bottom median {np.median(b):.2f}bp vs middle median {np.median(m):.2f}bp")

    import json
    with open("/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry/sonic_h1_test.json", "w") as f:
        json.dump(out, f, indent=2)
    print("\nwrote sonic_h1_test.json")


if __name__ == "__main__":
    main()
