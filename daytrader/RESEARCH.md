# CARRY-DAY — initial diagnostic record

*Run 2026-07-29. This is an in-sample diagnostic, not validation.*

> **V2 status:** The dashboard now reports a broader multi-timeframe confluence
> model and complete conditional trade tickets. That V2 logic was created after
> observing the failed result below, so this sample cannot validate it. V2 must
> remain paper-only until it passes a newly pre-registered out-of-sample and
> forward-paper protocol with realistic execution data.

## Question

Does the first intraday gate show enough basic promise to justify anything
beyond a paper-data collection app?

The screen used closed 5-minute breakouts with volume and RSI confirmation,
aligned to a closed 15-minute EMA20/50 trend. The simulator charged 5bp fee
plus 2bp slippage per side and assumed an additional 2bp historical spread.
If stop and target appeared inside the same five-minute bar, it counted the
stop first.

Command:

```bash
python3 daytrader/backtest.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --days 30 \
  --assumed-spread-bp 2
```

## First result

The first implementation's raw price-based target ladder generated 213 trades
and failed badly:

| Market | Trades | Win rate | Profit factor | Total net R |
|---|---:|---:|---:|---:|
| BTCUSDT | 42 | 33.33% | 0.475 | -14.133 |
| ETHUSDT | 71 | 26.76% | 0.329 | -34.051 |
| SOLUSDT | 100 | 31.00% | 0.427 | -39.568 |
| **Combined** | **213** | **30.05%** | **0.402** | **-87.752** |

The diagnostic also exposed a specification error: a target labeled “2R” was
2× the ATR stop distance before costs, not 2× effective risk after costs. That
made winners materially smaller than their label. The ticket builder was
corrected so 1R/2R/3R are now net of modeled round-trip cost.

The same sample was then rerun with the corrected target semantics:

| Market | Trades | Win rate | Profit factor | Total net R |
|---|---:|---:|---:|---:|
| BTCUSDT | 40 | 20.00% | 0.385 | -18.156 |
| ETHUSDT | 71 | 16.90% | 0.293 | -40.766 |
| SOLUSDT | 91 | 27.47% | 0.585 | -27.379 |
| **Combined** | **202** | **22.28%** | **0.437** | **-86.301** |

The correction fixed the ticket specification but did not rescue the
hypothesis.

## Decision

**FAIL. CARRY-DAY is not a validated strategy and makes no profitability
claim.** The shipped application is useful only as:

1. a live, fail-closed scanner;
2. a persistent paper ledger for forward data collection;
3. a backtest harness for developing and rejecting later hypotheses.

There is no live-order code path. Do not add one unless a separately
pre-registered strategy passes out-of-sample, walk-forward, regime, and
forward-paper gates with realistic execution costs.

## Known limitations

- Thirty days is a short, single-regime sample.
- Current-symbol selection creates survivorship bias.
- Klines do not contain historical spread, book impact, or queue position.
- The backtest does not simulate the live paper governor's portfolio-level
  trade cap or same-symbol cooldown.
- The three markets are correlated, so combined trade count overstates
  independent evidence.
- Strategy changes made after observing this sample require new untouched
  out-of-sample data; this sample cannot validate them.
