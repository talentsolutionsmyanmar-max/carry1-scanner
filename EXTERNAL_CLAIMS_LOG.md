# External Claims Log — every brought-in strategy/guru claim, measured

*Standing protocol: faithful port, real data, median not mean, measured
costs, negatives at equal prominence. Newest first. Scripts + JSON in this
directory.*

## 2026-07-27 — TJC "Trend Join Crypto" (Pine v5, session breakout, BTCUSDT 5m)

Faithful 1:1 port: SMA200(60m) trend filter, London 07-08 / NY 13:30-14:30 UTC
session levels, breakout = max(prevDayHigh, session highs), stop = window low,
fixed 1.5R or trailing 2×ATR, EOD flat 23:55, commission 0.04%/side + 1 tick,
conservative stop-first intrabar. Data: 50,000 real 5m bars, 2026-02-04 ..
2026-07-27 (`tjc_backtest.py` / `.json`).

| variant | trades | hit | mean R | **median R** | t | PF | verdict |
|---|---|---|---|---|---|---|---|
| Fixed 1.5R (default) | 66 | 50% | +0.027 | **−0.011** | 0.44 | 1.15 | DEAD — coin flip after costs |
| Trailing 2×ATR | 300 | 26% | −0.068 | **−0.072** | **−3.29** | 0.48 | significantly NEGATIVE |

Structural finding: in Fixed mode the exits were 57 EOD / 5 STOP / **4 TP**
in 5.7 months — the 1.5R ladder almost never resolves; the strategy is de
facto "hold to EOD". Same decorative-ladder signature as K3's own ledger
study. TJC is 5m time-series momentum — the family K3 closed; the closure
generalizes. **REJECTED, both modes.**

## 2026-07-27 — Jose Donato (cryexc) delta-spike archetype, 20 perps 1h

Proxy: signed bar delta = (2×taker_buy − volume)×close, 142,235 bar-events,
~11 months (`delta_spike_test.py` / `.json`). His thesis: big buy prints
continue up, big sell prints continue down.

| event | n | 4h mean | 4h med | 8h mean | 8h med | t(8h) |
|---|---|---|---|---|---|---|
| buy z≥2 | 2,998 | +15.2 | **−9.8** | +50.0 | −2.0 | 2.41 |
| buy p99 | 1,741 | +37.2 | −3.6 | +70.4 | **+10.5** | 2.44 |
| sell z≤−2 | 4,022 | −6.3 | 0.0 | −4.4 | −1.9 | −0.28 |
| sell p99 | 1,790 | +8.5 | **+7.8** | +8.9 | −0.8 | 0.29 |

Mean effect at 8h after extreme buys is real (t≈2.4) but: median ≤ 0 except
one marginal bucket (+10.5bp vs ~11bp one-way cost = no margin), and the
**sell side breaks the thesis** (extreme sells also drift UP, +7.8bp median
4h). Extreme-volume bars mark local extremes in both directions — a
volatility/beta signature, not directional order-flow edge. **REJECTED as a
signal module; archived as context feature only.**

LIQ-CLUSTER events: no free historical liquidation data exists (Binance
forceOrders is realtime-only). Data gap stated; not tested.

## 2026-07-27 — @sonictraders close-location candle rule

19,018 events, 20 perps 1h (`sonic_h1_test.py`). His "VALID short" bucket
(bottom-third confirmation close) goes **UP +8.9bp at 4h (t=2.1)** — claim
fails **sign-flipped** on crypto 1h. Inverted edge still doesn't clear
one-way cost. REJECTED. (Session discipline + invalidation framing kept.)

## Standing conclusion (5 external claims measured, 0 survivors)

Sonic's candle rule (inverted), TJC fixed (dead), TJC trail (negative),
delta spikes (tail, asymmetric-broken), plus the three internal tracks
(K3, RV-1, CARRY-1). Every directional claim brought to this desk fails the
same bar: **the typical trade does not pay its own friction.** The scanner
that only fires with 2× margin remains the only honest product of this
research program.
