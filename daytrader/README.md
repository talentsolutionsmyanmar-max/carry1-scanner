# CARRY-DAY — intraday scanner and paper trader

This is the new day-trading application inside the CARRY-1 research repo. It
does not modify or relabel the rejected multi-day funding-carry strategy.

Live static version:
[`https://talentsolutionsmyanmar-max.github.io/carry1-scanner/`](https://talentsolutionsmyanmar-max.github.io/carry1-scanner/)

The GitHub Pages build runs directly in the browser and persists its paper
ledger in `localStorage`. It does not require the Python server or an API key.

## Run

```bash
cd daytrader
python3 server.py
# http://127.0.0.1:7200
```

No API key is required. The application reads only public Binance USD-M Futures
market data and contains no exchange-order implementation.

Run one scan without opening a paper position:

```bash
python3 server.py --once --no-auto-paper
```

Run the tests:

```bash
python3 -m unittest discover -s daytrader/tests -v
```

## Ticket states and signal gate

The dashboard shows exactly one complete plan: the strongest `LIVE` ticket, or
the highest-scoring `ARMED` ticket when no setup is live. The remaining markets
stay visible in the comparison board without competing ticket cards. The plan
includes side, entry trigger, structural stop, TP1/TP2/TP3, risk-sized quantity,
cost estimate, expiry, time exit, evidence, unresolved gates, and invalidation.
The state controls whether that plan is actionable:

- `LIVE`: every confluence, trigger, session, execution, and risk gate cleared;
- `ARMED`: conditional stop-entry plan; do not enter before its displayed trigger;
- `WATCH`: directional context exists but no actionable ticket;
- `STAND DOWN`: no coherent edge.

A `LIVE` ticket requires all of:

1. Closed 1-hour EMA50/EMA200 regime alignment.
2. Closed 15-minute EMA20/EMA50 trend alignment.
3. Closed 5-minute EMA9/EMA21 alignment and 20-bar breakout.
4. MACD(12,26,9), RSI(14), Stoch RSI, and ADX(14) confirmation.
5. Price on the correct side of session VWAP; Bollinger position is reported.
6. Quote volume at least 1.2× the prior 20-bar median.
7. Spread, ATR, mark drift, and structural-stop width inside configured limits.
8. Modeled round-trip friction below 25% of the stop distance.
9. Asia, London, or New York UTC kill zone.
10. Confluence score of at least 80/100.

The last, still-forming candle is discarded. Missing or invalid market data
fails closed.

## Risk governor

Defaults:

- paper mode only;
- $10,000 starting paper equity;
- 0.25% risk budget per trade;
- 1.00% daily realized-loss stop;
- two simultaneous positions;
- four new trades per UTC day;
- a 30-minute same-symbol cooldown after exit;
- notional capped at 50% of paper equity per trade;
- structural swing stop with a 1.25 ATR minimum, target ladder at true
  1R/2R/3R after modeled costs;
- all paper positions close at 2R, after three hours, or on UTC-day rollover;
- configurable fees and slippage are charged to every completed paper trade.

Paper state is persisted to `daytrader/.paper_state.json`.

## Configuration

Environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `DAY_STARTING_EQUITY` | `10000` | Initial paper equity (USD) |
| `DAY_RISK_PER_TRADE_PCT` | `0.25` | Risk budget as percent of equity |
| `DAY_MAX_DAILY_LOSS_PCT` | `1.00` | UTC daily realized-loss stop |
| `DAY_MAX_OPEN_POSITIONS` | `2` | Concurrent paper positions |
| `DAY_MAX_TRADES` | `4` | New paper positions per UTC day |
| `DAY_MAX_HOLD_MINUTES` | `180` | Time stop |
| `DAY_UNIVERSE_SIZE` | `12` | Liquid USDT perpetuals scanned |
| `DAY_SCAN_SECONDS` | `60` | Full sweep interval |
| `DAY_FEE_PER_SIDE_BP` | `5` | Modeled fee per side |
| `DAY_SLIPPAGE_PER_SIDE_BP` | `2` | Modeled slippage per side |
| `DAY_AUTO_PAPER` | `true` | Automatically open qualifying paper tickets |

## Important limitation

This is an experiment, not a profitability claim or financial advice. The
paper ledger uses sampled mark prices rather than an exchange-grade event
stream, so fills can differ materially from real trading. Validate the
strategy over out-of-sample data and a long forward paper period before
considering any separate execution system.

The initial in-sample diagnostic is published in
[`RESEARCH.md`](RESEARCH.md). It failed. This multi-indicator V2 is a new,
unvalidated hypothesis; it does not erase that result, and live execution must
remain disabled.
