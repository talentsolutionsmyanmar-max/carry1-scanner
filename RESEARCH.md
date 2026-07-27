# CARRY-1 — Funding-Rate Carry: Research Record and Gate Decision

*2026-07-27. Third systematic track tested under the same doctrine:
rules written before the data, median not mean, measured costs not
venue fantasy, negatives at equal prominence.*

## Structure

Income from a payment stream, not price prediction: short the perp,
hedge the delta, collect funding while it prints positive. Signal:
realized 8h/4h funding >= theta at an epoch boundary. Hold while
funding > 0, hard exit at 5 days.

## Measurements (scripts: carry_stage0.py, carry_stage1.py; data in .json)

Stage 0 (166d, top-20 perps, venue-level costs): theta=2/3bp passed the
2x-median bar on MEXC fantasy costs (net 31.7 / 45.8bp vs 20bp bar).

Stage 1 controls, demanded before any build:

1. **Measured book costs** (live order-book walks, $5k legs, both legs,
   round trip, 3 snapshots): the carry exists exactly where the books are
   thin. AKE 38.9bp, ESPORTS 57.7bp, DEXE 35.4bp, DIA 49.0bp measured —
   vs BTC 0.06bp, ETH 0.20bp. The RV-1 thin-name warning was correct and
   is now measured fact.
2. **Regime**: exchange funding history serves only ~37-166 days
   (median 124d) — a single hot window (2026-05..07). No multi-regime
   sample exists from this venue; sign-stability cannot be established.

## Gate result (pre-registered: median net >= 2x measured cost, sign-stable
across regimes, <=50% single-symbol concentration, >=4 trades/month)

| theta | n | med gross | med measured cost | med net | 2x bar | verdict |
|---|---|---|---|---|---|---|
| 2bp | 38 | 41.7bp | 39.0bp | 4.2bp | 77.9bp | **FAIL** |
| 3bp | 29 | 55.8bp | 39.0bp | 16.8bp | 77.9bp | **FAIL** |

**CARRY-1 is REJECTED at its gate.** The edge-to-friction ratio is
anticorrelated with book quality: funding is large precisely on the names
where entering and exiting costs the entire edge. Same wall as K3
(time-series, ~3x short, ratio invariant across horizons) and RV-1
(cross-sectional, ~4x short on the median). Three constructions, one
structural result: at retail-accessible friction, the market prices its
own anomalies.

## What ships instead, and why it is not a strategy claim

A live scanner (`carrydash/`) that monitors the universe 24/7 and FIRES
only when the pre-registered inequality clears with the full 2x margin:
predicted/last funding >= theta AND historical persistence median at that
theta >= 2x the name's live-measured book cost. On current measurements
it will be silent nearly all the time — silence is the honest output.
When it fires (extreme dislocation, funding >= ~5bp with persistence and
cheap-enough books), the opportunity is real and measured. The dashboard
also carries the day-trading context panel (kill-zone clock, funding
board, per-name cost meter, cross-sectional dispersion) for discretionary
use. No always-on signal is claimed anywhere. Live execution remains
gated: paper accounting with full fees only.
