"""TJC (Trend Join Crypto) — faithful Pine v5 port, measured on real BTCUSDT 5m.

Pine rules (from the attached strategy), ported 1:1:
  - Trend: close > SMA200 of 60m (prior CLOSED 1h bar, non-repainting)
  - Levels (UTC): prevDay high/low; London window 07:00-08:00; NY 13:30-14:30
  - breakoutLevel = max(prevDayHigh, londonHigh-so-far, nyHigh-so-far)
  - stopRef = low of the window that set the level (fallback: current day low)
  - Entry (bar close, flat only): close > smaHTF AND close > prevDayHigh AND
    close > breakoutLevel AND close > stopRef AND in 00:00-23:30 UTC
  - Exit A (Fixed 1.5R): stop=stopRef, target=close+1.5*(close-stopRef)
  - Exit B (Trailing 2xATR14): ratchet trail, initial stop=stopRef
  - EOD flat 23:55 UTC (no overnight); commission 0.04%/side; slippage 1 tick
  - Conservative intrabar rule (K3 doctrine): if a bar's range spans both
    stop and target, the STOP is taken first.

Doctrine read: measurement, not endorsement. Report n, hit, mean/MEDIAN R
and net bp/trade, NW-t, profit factor, maxDD in R. Median is the bar.
"""
import json
import sys

sys.path.insert(0, "/Users/kokohtikeaung/Documents/kimi/workspace/kimi-k3")

import numpy as np
import pandas as pd
from k3 import data

SYM = "BTCUSDT"
BARS_5M = 50000          # ~5.7 months of 5m
COMMISSION = 0.0004      # 0.04% per side (Pine)
TICK = 0.10              # BTCUSDT tick size
LON = (7, 8)             # 07:00-08:00 UTC
NY = (13.5, 14.5)        # 13:30-14:30 UTC
ENTRY_END = (23, 30)     # entries allowed until 23:30
FLAT = (23, 55)          # EOD flat


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


def load():
    df = data.klines_history(SYM, "5m", BARS_5M)
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


def add_sma_htf(df):
    h = df.set_index("timestamp")["close"].resample("1h").last().dropna()
    sma = h.rolling(200).mean()
    # value available to a 5m bar = SMA of the last 1h bar closed BEFORE it
    sma_avail = sma.shift(1)
    idx = df["timestamp"].dt.floor("1h")
    df["sma_htf"] = idx.map(sma_avail).values
    return df


def simulate(df, tp_mode):
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    ts = df["timestamp"]
    day = (ts.values.astype("datetime64[s]").astype("datetime64[D]")).astype(int)
    hour = ts.dt.hour.values + ts.dt.minute.values / 60.0
    sma = df["sma_htf"].values

    # ATR14 on 5m
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = pd.Series(tr).rolling(14).mean().values
    atr = np.concatenate([[np.nan], atr])

    trades = []
    pos = None
    prev_hi = prev_lo = np.nan
    cur_hi = cur_lo = np.nan
    lon_hi = lon_lo = np.nan
    ny_hi = ny_lo = np.nan
    cur_day = -1

    for i in range(1, len(df)):
        if day[i] != cur_day:
            prev_hi, prev_lo = cur_hi, cur_lo
            cur_hi, cur_lo = h[i], l[i]
            lon_hi = lon_lo = ny_hi = ny_lo = np.nan
            cur_day = day[i]
        else:
            cur_hi = max(cur_hi, h[i])
            cur_lo = min(cur_lo, l[i])
        if LON[0] <= hour[i] < LON[1]:
            lon_hi = h[i] if np.isnan(lon_hi) else max(lon_hi, h[i])
            lon_lo = l[i] if np.isnan(lon_lo) else min(lon_lo, l[i])
        if NY[0] <= hour[i] < NY[1]:
            ny_hi = h[i] if np.isnan(ny_hi) else max(ny_hi, h[i])
            ny_lo = l[i] if np.isnan(ny_lo) else min(ny_lo, l[i])

        # EOD flat
        if pos is not None and hour[i] >= FLAT[0] + FLAT[1] / 60:
            px = c[i] * (1 - 0) - TICK
            trades.append(close_trade(pos, px, ts.iloc[i], "EOD"))
            pos = None
            continue

        if pos is not None:
            # trailing ratchet
            if tp_mode == "trail" and not np.isnan(atr[i]):
                cand = c[i] - 2.0 * atr[i]
                pos["trail"] = cand if pos["trail"] is None else max(pos["trail"], cand)
            stop = pos["stop"]
            if tp_mode == "trail" and pos["trail"] is not None:
                stop = max(stop, pos["trail"])
            # stop first (conservative), then target
            if l[i] <= stop:
                trades.append(close_trade(pos, stop - TICK, ts.iloc[i], "STOP"))
                pos = None
                continue
            if tp_mode == "fixed" and h[i] >= pos["target"]:
                trades.append(close_trade(pos, pos["target"] - TICK, ts.iloc[i], "TP"))
                pos = None
                continue
            continue

        # entry logic (flat only)
        if np.isnan(sma[i]) or np.isnan(prev_hi):
            continue
        level = prev_hi
        ref = prev_lo
        if not np.isnan(lon_hi) and lon_hi > level:
            level, ref = lon_hi, lon_lo
        if not np.isnan(ny_hi) and ny_hi > level:
            level, ref = ny_hi, ny_lo
        if np.isnan(ref):
            ref = cur_lo
        in_entry = hour[i] < ENTRY_END[0] + ENTRY_END[1] / 60
        if (c[i] > sma[i] and c[i] > prev_hi and c[i] > level
                and (c[i] - ref) > 0 and in_entry):
            entry = c[i] + TICK  # adverse slippage on entry
            pos = {"entry": entry, "stop": ref, "trail": None,
                   "target": entry + 1.5 * (entry - ref),
                   "t": ts.iloc[i], "risk": entry - ref}
    return trades


def close_trade(pos, exit_px, t, reason):
    gross = exit_px - pos["entry"]
    cost = COMMISSION * (pos["entry"] + exit_px)  # both sides
    net = gross - cost
    return {"t": str(t), "reason": reason, "entry": pos["entry"],
            "exit": exit_px, "net_bp": net / pos["entry"] * 1e4,
            "R": net / pos["risk"] if pos["risk"] > 0 else 0.0}


def report(trades, label):
    if len(trades) < 10:
        print(f"{label}: only {len(trades)} trades")
        return None
    df = pd.DataFrame(trades)
    r = df["R"].values
    bp = df["net_bp"].values
    eq = np.cumsum(r)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    gp = r[r > 0].sum()
    gl = -r[r < 0].sum()
    out = {
        "n": int(len(df)), "hit": round(float((r > 0).mean()), 3),
        "mean_R": round(float(r.mean()), 3), "median_R": round(float(np.median(r)), 3),
        "mean_bp": round(float(bp.mean()), 2), "median_bp": round(float(np.median(bp)), 2),
        "t_R": round(float(nw_t(r)), 2),
        "profit_factor": round(float(gp / gl), 2) if gl > 0 else None,
        "maxDD_R": round(dd, 1),
        "exits": df["reason"].value_counts().to_dict(),
    }
    print(f"\n=== {label} ===")
    for k, v in out.items():
        print(f"  {k}: {v}")
    return out


def main():
    df = load()
    print(f"data: {len(df)} 5m bars {df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}")
    df = add_sma_htf(df)
    res = {}
    res["fixed_1.5R"] = report(simulate(df, "fixed"), "TJC Fixed 1.5R (Pine default)")
    res["trail_2ATR"] = report(simulate(df, "trail"), "TJC Trailing 2xATR")
    with open("/Users/kokohtikeaung/Documents/kimi/workspace/product3-carry/tjc_backtest.json", "w") as f:
        json.dump(res, f, indent=2, default=str)
    print("\nwrote tjc_backtest.json")


if __name__ == "__main__":
    main()
