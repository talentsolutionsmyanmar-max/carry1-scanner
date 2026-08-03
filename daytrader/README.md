# CARRY-DAY — intraday scanner and paper trader

This is the new day-trading application inside the CARRY-1 research repo. It
does not modify or relabel the rejected multi-day funding-carry strategy.

Live static version:
[`https://talentsolutionsmyanmar-max.github.io/carry1-scanner/daytrader/`](https://talentsolutionsmyanmar-max.github.io/carry1-scanner/daytrader/)

The GitHub Pages build runs directly in the browser and persists its paper
ledger in `localStorage`. It does not require the Python server or an API key.

## Two independent playbooks, one decision

The scanner evaluates both playbooks on closed Binance candles, then a single
arbiter emits at most one ticket per symbol and the desk displays only the
strongest ticket across the universe:

- **Playbook A — Momentum Breakout:** the existing 1h regime, 15m trend, 5m
  EMA/momentum/volume/VWAP stack and controlled 20-bar breakout.
- **Playbook B — Liquidity MSS/FVG:** an objective ICT/SMC-inspired sequence:
  a known liquidity level is swept and reclaimed, price closes through a
  confirmed minor swing with displacement, an unfilled three-candle fair value
  gap remains, and entry is planned at its 50% midpoint in the correct half of
  the 15m dealing range.

Confirmed swing pivots require two closed bars on both sides. A sweep can use a
previous-day high/low, a completed Asia-session high/low, or a previously
confirmed 5m/15m swing. Displacement requires a body at least 1.2× the prior
20-bar median with a directional close. The FVG must be at least 0.08 ATR and
must not be fully filled. These definitions are deliberately mechanical and
non-repainting; the engine does not guess discretionary order blocks.

If both playbooks are actionable in opposite directions, the arbiter vetoes
the ticket. If they agree, the selected ticket is labeled `DUAL_CONFIRMATION`
without adding points. Each playbook retains its own 100-point score so
derivatives inputs never inflate technical confluence.

The automatic paper ledger follows the same rule: per scan, only the strongest
ranked `LIVE` signal is eligible to open. It never opens every qualifying market
as a basket.

## Derivatives context

Each symbol also reads Binance's public 5-minute derivatives feeds and reports:

- open-interest value plus 15-minute and one-hour change;
- aggregated 15-minute taker buy/sell volume ratio;
- global long/short account ratio and current funding;
- a transparent `SUPPORTS`, `NEUTRAL`, `CONFLICTS`, or `UNAVAILABLE` verdict;
- a leverage-risk label such as `LONGS_CROWDED`, `SHORTS_CROWDED`,
  `LEVERAGE_BUILDING`, or `DELEVERAGING`.

This is deliberately not presented as CoinGlass data or as a liquidation
heatmap. CoinGlass's liquidation-map model requires a paid API key, which must
never be embedded in a public GitHub Pages bundle. The browser instead uses
auditable exchange-native positioning proxies. These inputs add no points to
the technical confluence score. Two independent conflicts veto `LIVE` and
`ARMED`; missing auxiliary feeds remain visible as `UNAVAILABLE` and do not
silently invent a value.

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

A Playbook A `LIVE` ticket requires all of:

1. Closed 1-hour EMA50/EMA200 regime alignment.
2. Closed 15-minute EMA20/EMA50 trend alignment.
3. Closed 5-minute EMA9/EMA21 alignment and 20-bar breakout.
4. MACD(12,26,9), RSI(14), Stoch RSI, and ADX(14) confirmation.
5. Price on the correct side of session VWAP; Bollinger position is reported.
6. Quote volume at least 1.2× the prior 20-bar median.
7. Spread, ATR, mark drift, and structural-stop width inside configured limits.
8. Modeled round-trip friction below 25% of the stop distance.
9. Asia, London, or New York UTC kill zone.
10. No two-factor conflict from OI, taker flow, funding, and account crowding.
11. Confluence score of at least 80/100.

A Playbook B `LIVE` ticket requires all of:

1. Closed 1-hour EMA50/EMA200 structure aligned with the trade direction.
2. Entry in discount for a long or premium for a short, using a closed 15m
   dealing range.
3. A recent closed 5m wick through known liquidity and close back across it.
4. A later 5m close through a pre-sweep confirmed swing (MSS).
5. MSS displacement body at least 1.2× its prior 20-bar median and a directional
   close in the outer 30% of the candle.
6. An unfilled three-candle FVG at least 0.08 ATR.
7. A current closed-candle retest of the FVG midpoint; `ARMED` means the retest
   remains pending and nearby.
8. Spread, ATR, mark drift, swept-extreme stop, modeled friction, session, and
   derivatives-conflict gates all clear.
9. Liquidity-playbook score of at least 85/100.

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
- structural swing/swept-extreme stop, target ladder at a minimum true
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
[`RESEARCH.md`](RESEARCH.md). It failed. Both the multi-indicator momentum
playbook and the new liquidity/MSS/FVG playbook are unvalidated hypotheses;
they do not erase that result, and live exchange execution must remain disabled.

## Quantrex v0 paper and shadow desk

The server dashboard also exposes a separate, versioned Quantrex research
panel for BTCUSDT, ETHUSDT, and SOLUSDT USD-M perpetuals. It evaluates QSR-1
DAY QSR1_V1 and Breakout v0 from completed 15-minute bars. QSR1_V1 shows the
next-quote entry, hard stop, Take Profit 1 (50% at net 1R), Take Profit 2 (30%
at net 1.5R), Take Profit 3 (20% at net 2R), session/kill zone, time exit, risk,
modeled cost, and every fail-closed blocker. Breakout remains a single net-1.5R
exit so the adversarial benchmark is not silently changed.

The shared Quantrex lifecycle lives in `daytrader/quantrex/`. Its venue is
`SHADOW_NO_SUBMIT`: the code has no credential loader, authenticated HTTP
client, order endpoint, or runtime enable switch. Calling `submit()` always
raises `SubmissionDisabled`. Public market quotes may be compared with would-be
orders, but this version cannot place a funded exchange order.

Alongside the scored v0 cohort, the dashboard maintains a read-only top-12
USD-M discovery table ranked by Binance 24-hour quote volume. Discovery symbols
show 24-hour change, spread, open interest, short-horizon OI changes, taker
buy/sell ratio, and funding, and may be sorted by volume, OI, or funding. They
are explicitly labeled `DISCOVERY` and do not enter the scored
QSR/Breakout book until a new universe version is frozen.

Historical promotion metrics are evaluated separately by
`daytrader.quantrex.research`. It creates anchored, chronologically purged
walk-forward windows plus an explicitly untouched final OOS window, reports a
deterministic bootstrap interval, profit factor, drawdown, tail loss, turnover,
costs, and symbol/session concentration, and returns `NO_GO` whenever a frozen
gate or required stress/integrity input is missing. It consumes Quantrex trade
rows; it does not relabel the legacy exploratory CARRY-DAY backtest as QSR-1
evidence.

`daytrader.quantrex.forward.ForwardPaperRunner` is the durable forward-paper
coordinator. It journals every versioned signal and risk rejection, processes
each completed bar once, persists fills and reconciliation hashes, and opens a
paper intent only when exactly one accepted candidate exists and no Quantrex
position is already open. Simultaneous accepted candidates fail closed rather
than relying on an unfrozen priority rule. The runner contains no exchange
client and reports `FORWARD_PAPER_NO_SUBMIT`.
Start the server with `--quantrex-kill-switch` to persistently block every new
Quantrex paper intent; the dashboard exposes the resulting `KILL ACTIVE`
state. Clearing it is intentionally not exposed through the unauthenticated
HTTP dashboard and must be an explicit local operator action through the
runner before a restart.
