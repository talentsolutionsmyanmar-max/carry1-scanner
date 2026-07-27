# CARRY-1 — Honest Signal Scanner

**Live dashboard (no install):** open `web/index.html` via any static host — it talks to Binance directly from your browser.

- jsDelivr (always current `main`):
  `https://cdn.jsdelivr.net/gh/talentsolutionsmyanmar-max/carry1-scanner@main/web/index.html`
- Local full version (live persistence rebuild + shared alert log):
  ```bash
  cd carrydash && python3 server.py --port 7100   # → http://localhost:7100/
  ```

## What it is

A 24/7 scanner over the top-20 USDT perps that fires a **full trade ticket**
(Entry / TP1 / TP2 / TP3 / SL1 / SL2 / hard flat) only when a pre-registered
inequality clears:

```
FIRE  ⇔  funding ≥ θ 3bp   AND   persistence-median ≥ 2 × measured book cost
```

- **Funding** — last realized print (bp/epoch), live from `/premiumIndex`.
- **Persistence** — median 5-day gross carry at that theta from the name's own
  funding history (`web/persist.json`; the local server rebuilds it 6-hourly).
- **Cost** — real order-book walk (spread + impact on both legs, round trip),
  measured live, not venue fee tables.
- **Silence is the honest output.** The gate is fail-closed: unknown
  persistence or unknown cost ⇒ no fire.

## Why the gate is so strict — the research record

Three systematic tracks were built and tested to pre-registered gates, and all
three closed on the data (median, not mean; measured costs; negatives published):

| track | construction | result |
|---|---|---|
| K3 | time-series momentum/reversion, 5m/15m/1h | signal-to-friction ratio ~3× short, invariant across horizons — **closed** |
| RV-1 | cross-sectional 4h loser reversal | mean real (t≈3.3) but **median 4.5bp vs 20bp bar** — closed |
| CARRY-1 | funding-rate carry, conditional entry | median net **16.8bp vs 77.9bp bar** on measured costs — closed as a strategy |

The structural finding: the edge-to-friction ratio is *anticorrelated* with
book quality. Funding is large exactly on the names where entering/exiting
costs the entire edge (AKE 38.9bp, ESPORTS 57.7bp measured round trip vs
BTC 0.06bp). See `RESEARCH.md` for the full measurement tables.

So this scanner is the product: it watches for the rare dislocations where the
inequality actually clears with margin, and stays silent otherwise.

## Layout

```
web/index.html      standalone dashboard — browser ↔ Binance direct (CORS-open)
web/persist.json    persistence table (median/p75 gross carry per name/theta)
carrydash/          local server build (server.py, watchdog.py, richer UI)
carry_stage0.py     Stage-0 measurement: conditional carry vs venue costs
carry_stage1.py     Stage-1 controls: 2y regime attempt + measured book costs
carry_stage0.json / carry_stage1.json   the data behind the gate decision
RESEARCH.md         full research record and gate decision
```

## Doctrine

Rules written before the data. Median, not mean. Measured costs, not venue
fantasy. Breakeven is not validation. Negative results at equal prominence.
Live execution stays gated: paper accounting with full fees only.
