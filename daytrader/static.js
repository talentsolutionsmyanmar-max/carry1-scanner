/* CARRY-DAY dual runtime: Python API locally, direct Binance data on GitHub Pages. */
'use strict';

const $ = id => document.getElementById(id);
const money = n => Number(n || 0).toLocaleString(undefined, {
  style: 'currency', currency: 'USD', maximumFractionDigits: 2
});
const num = (n, d = 1) => n == null ? '—' : Number(n).toFixed(d);
const price = n => {
  if (n == null) return '—';
  const value = Number(n);
  const digits = value >= 10000 ? 1 : value >= 100 ? 2 : value >= 1 ? 4 : value >= 0.01 ? 6 : 8;
  return value.toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits});
};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const pnlClass = n => Number(n) > 0 ? 'good' : Number(n) < 0 ? 'bad' : '';
const FAPI = 'https://fapi.binance.com/fapi/v1';
const PAPER_KEY = 'carry_day_static_paper_v2';
const STATIC_MODE = location.hostname.endsWith('.github.io') ||
  location.protocol === 'file:' || new URLSearchParams(location.search).has('static');
const CFG = Object.freeze({
  universeSize: 12, minListingDays: 30, maxAbsChange: 25,
  breakoutLookback: 20, volumeLookback: 20, minVolumeRatio: 1.2,
  signalScore: 80, armedScore: 65, watchScore: 50, maxSpreadBp: 8,
  minAtrPct: 0.12, maxAtrPct: 3, maxEntryDriftAtr: 0.5,
  stopAtr: 1.25, maxStopAtr: 4, maxFrictionStopPct: 25, minAdx: 18,
  minRewardCostMultiple: 3, entryExpiryMinutes: 20,
  startingEquity: 10000, riskPerTrade: 0.0025, maxDailyLoss: 0.01,
  maxPositions: 2, maxTrades: 4, maxNotional: 0.5,
  maxHoldMinutes: 180, cooldownMinutes: 30,
  feePerSideBp: 5, slippagePerSideBp: 2, scanMs: 60000
});
const STABLE = new Set(['USDT', 'USDC', 'FDUSD', 'TUSD', 'USDP', 'DAI', 'BUSD']);
let lastStaticState = null;
let lastStaticScan = 0;
let scanInFlight = false;

function relative(iso) {
  if (!iso) return 'not scanned';
  const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000));
  return seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds / 60)}m ago`;
}

function signalRow(s) {
  const state = s.state || 'STAND_DOWN';
  const change = Number(s.price_change_pct_24h || 0);
  return `<tr>
    <td><span class="symbol">${esc(s.symbol)}</span></td>
    <td><span class="state ${state.toLowerCase()}">${esc(state.replace('_', ' '))}</span></td>
    <td class="${s.side === 'LONG' ? 'good' : s.side === 'SHORT' ? 'bad' : ''}">${esc(s.side || '—')}</td>
    <td><span class="bar"><i style="width:${Math.min(100, s.score || 0)}%"></i></span> ${s.score || 0}</td>
    <td>${num(s.rsi_5m, 1)} / ${num(s.adx_5m, 1)}</td>
    <td class="${Number(s.volume_ratio) >= 1.2 ? 'amber' : ''}">${num(s.volume_ratio, 2)}×</td>
    <td>${num(s.friction_stop_pct, 1)}%</td><td>${num(s.spread_bp, 2)}bp</td>
    <td class="${pnlClass(change)}">${change >= 0 ? '+' : ''}${num(change, 2)}%</td>
  </tr>`;
}

function ticketCard(signal) {
  const t = signal.ticket;
  const sideClass = signal.side === 'LONG' ? 'good' : 'bad';
  const evidence = (signal.reasons || []).map(item => `<span class="evidence good-evidence">✓ ${esc(item)}</span>`).join('');
  const blocks = (signal.blocked_by || []).slice(0, 4).map(item => `<span class="evidence block-evidence">• ${esc(item)}</span>`).join('');
  const expiry = t.entry_valid_until ? new Date(t.entry_valid_until).toISOString().slice(11, 16) + ' UTC' : '—';
  return `<article class="ticket ${String(signal.state).toLowerCase()}">
    <div class="ticket-head">
      <div><span class="state ${String(signal.state).toLowerCase()}">${esc(signal.state)}</span><span class="ticket-symbol">${esc(signal.symbol)}</span><span class="ticket-side ${sideClass}">${esc(signal.side)}</span></div>
      <div class="grade">${esc(signal.confidence)} <b>${signal.score}</b><small>CONFLUENCE</small></div>
    </div>
    <div class="ticket-action">${esc(signal.action)}</div>
    <div class="levels">
      <div class="entry"><span>ENTRY</span><b>${price(t.entry)}</b></div>
      <div class="stop"><span>STOP LOSS</span><b>${price(t.stop)}</b></div>
      <div><span>TP1 · NET 1R</span><b>${price(t.tp1)}</b></div>
      <div><span>TP2 · NET 2R</span><b>${price(t.tp2)}</b></div>
      <div><span>TP3 · NET 3R</span><b>${price(t.tp3)}</b></div>
    </div>
    <div class="indicator-grid">
      <span>1H REGIME<b>${esc(signal.higher_trend)}</b></span>
      <span>15M TREND<b>${esc(signal.trend_15m)}</b></span>
      <span>RSI14<b>${num(signal.rsi_5m, 1)}</b></span>
      <span>STOCH RSI<b>${num(signal.stoch_rsi_5m, 1)}</b></span>
      <span>MACD HIST<b>${num(signal.macd_hist_5m, 6)}</b></span>
      <span>ADX14<b>${num(signal.adx_5m, 1)}</b></span>
      <span>VWAP<b>${price(signal.vwap)}</b></span>
      <span>BB POSITION<b>${num(signal.bollinger_position, 2)}σ</b></span>
      <span>ATR14<b>${num(signal.atr_pct, 2)}%</b></span>
      <span>VOLUME<b>${num(signal.volume_ratio, 2)}×</b></span>
      <span>FUNDING<b>${num(signal.funding_bp, 2)}bp</b></span>
      <span>SESSION<b>${esc(signal.kill_zone)}</b></span>
    </div>
    <div class="ticket-meta">
      <span>RISK <b>${money(t.risk_budget_usd)} · 0.25%</b></span>
      <span>SIZE <b>${money(t.notional_usd)}</b></span>
      <span>STOP <b>${num(signal.stop_distance_bp, 1)}bp</b></span>
      <span>FRICTION/STOP <b>${num(t.friction_stop_pct, 1)}%</b></span>
      <span>ENTRY EXPIRY <b>${expiry}</b></span>
      <span>TIME EXIT <b>${new Date(t.time_exit).toISOString().slice(11, 16)} UTC</b></span>
    </div>
    <div class="evidence-list">${evidence}${blocks}</div>
    <div class="invalidation"><b>INVALIDATION</b> ${esc(t.invalidation)}</div>
  </article>`;
}

function positionCard(p) {
  const pnl = Number(p.unrealized_pnl_usd || 0);
  return `<div class="position">
    <div class="pos-head"><b>${esc(p.symbol)}</b><span class="state ${String(p.side).toLowerCase()}">${esc(p.side)}</span><span class="pnl ${pnlClass(pnl)}">${pnl >= 0 ? '+' : ''}${money(pnl)}</span></div>
    <div class="pos-grid">
      <div>ENTRY<b>${price(p.entry)}</b></div><div>MARK<b>${price(p.mark)}</b></div><div>SIZE<b>${money(p.notional_usd)}</b></div>
      <div>STOP<b class="bad">${price(p.stop)}</b></div><div>TP2<b class="good">${price(p.tp2)}</b></div><div>RISK<b>${money(p.effective_risk_usd)}</b></div>
    </div>
  </div>`;
}

function tradeRow(t) {
  const pnl = Number(t.net_pnl_usd || 0);
  return `<div class="trade"><b>${esc(t.symbol)} · ${esc(t.side)} · ${esc(t.exit_reason)}</b><span>${new Date(t.closed_at).toISOString().slice(5, 16).replace('T', ' ')}</span><div class="pnl ${pnlClass(pnl)}">${pnl >= 0 ? '+' : ''}${money(pnl)}</div></div>`;
}

function render(s) {
  const paper = s.paper || {};
  const risk = paper.risk || {};
  const signals = s.signals || [];
  const positions = paper.positions || [];
  const history = paper.history || [];
  const live = signals.filter(item => item.state === 'LIVE');
  const armed = signals.filter(item => item.state === 'ARMED');
  const tickets = signals.filter(item => item.ticket).slice(0, 4);
  $('status').textContent = s.status || 'UNKNOWN';
  $('dot').className = 'dot ' + (s.status === 'LIVE' ? 'live' : '');
  $('equity').textContent = money(paper.equity_usd);
  $('cash').textContent = `cash ${money(paper.cash_usd)}`;
  const daily = Number(risk.daily_realized_pnl_usd || 0);
  $('dailyPnl').textContent = (daily >= 0 ? '+' : '') + money(daily);
  $('dailyPnl').className = 'v ' + pnlClass(daily);
  $('lossLimit').textContent = `stop −${money(risk.daily_loss_limit_usd)}`;
  $('trades').textContent = `${risk.trades_today || 0} / ${risk.max_trades_per_day || 0}`;
  $('positions').textContent = `${risk.open_positions || 0} / ${risk.max_open_positions || 0}`;
  $('positionCap').textContent = 'concurrent paper positions';
  $('scanTime').textContent = `${relative(s.last_scan)} · ${s.scan_duration_seconds || 0}s sweep`;
  $('signals').innerHTML = signals.length ? signals.map(signalRow).join('') :
    '<tr><td colspan="9" class="empty">No market snapshots available</td></tr>';
  $('ticketCount').textContent = `${live.length} LIVE · ${armed.length} ARMED`;
  $('tickets').innerHTML = tickets.length ? tickets.map(ticketCard).join('') :
    '<div class="empty ticket-empty">No candidate currently has enough coherent confluence for an actionable ticket. Stand down is a valid signal.</div>';
  $('openCount').textContent = String(positions.length);
  $('openPositions').innerHTML = positions.length ? positions.map(positionCard).join('') :
    '<div class="empty">No open positions</div>';
  $('history').innerHTML = history.length ? history.map(tradeRow).join('') :
    '<div class="empty">No completed trades</div>';
  const used = Math.max(0, -daily) / Math.max(Number(risk.daily_loss_limit_usd || 1), 1) * 100;
  $('riskUsed').textContent = `${Math.min(100, used).toFixed(0)}%`;
  $('riskBar').style.width = `${Math.min(100, used)}%`;
  $('riskState').textContent = risk.can_open ? 'ARMED' : 'LOCKED';
  $('riskState').className = 'count ' + (risk.can_open ? 'good' : 'bad');
  $('riskNote').textContent = risk.can_open ?
    'Hard portfolio limits are clear. Only LIVE tickets can enter the browser paper ledger.' :
    `New entries blocked: ${(risk.blocked_by || []).join(', ')}.`;
  if (!risk.can_open) {
    $('hero').innerHTML = 'SESSION <em class="bad">LOCKED</em>';
    $('heroSub').textContent = (risk.blocked_by || []).join(' · ');
  } else if (live.length) {
    $('hero').innerHTML = `${live.length} <em>LIVE</em> · ${armed.length} ARMED`;
    $('heroSub').textContent = 'LIVE cleared every indicator, trigger, kill-zone, execution-cost, and risk gate. Paper simulation only.';
  } else if (armed.length) {
    $('hero').innerHTML = `${armed.length} SETUP${armed.length === 1 ? '' : 'S'} <em class="amber">ARMED</em>`;
    $('heroSub').textContent = 'Conditional entry plans are ready below. Do not enter before their trigger; unresolved gates remain visible.';
  } else {
    $('hero').innerHTML = 'STAND <em>DOWN</em>';
    $('heroSub').textContent = 'No setup currently has coherent multi-timeframe confluence. Waiting is the trade.';
  }
}

async function jget(path) {
  const response = await fetch(FAPI + path, {cache: 'no-store'});
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json();
}

function candle(row) {
  return {
    openTime: +row[0], closeTime: +row[6], open: +row[1], high: +row[2],
    low: +row[3], close: +row[4], volume: +row[5], quoteVolume: +row[7], trades: +row[8]
  };
}

function emaSeries(values, period) {
  if (!values.length) return [];
  const alpha = 2 / (period + 1), out = [values[0]];
  for (let i = 1; i < values.length; i++) out.push(alpha * values[i] + (1 - alpha) * out.at(-1));
  return out;
}
const emaLast = (values, period) => emaSeries(values, period).at(-1);

function rsiSeries(values, period = 14) {
  const out = Array(values.length).fill(null);
  if (values.length <= period) return out;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    gain += Math.max(0, change); loss += Math.max(0, -change);
  }
  gain /= period; loss /= period;
  const value = () => loss ? 100 - 100 / (1 + gain / loss) : gain ? 100 : 50;
  out[period] = value();
  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    gain = (gain * (period - 1) + Math.max(0, change)) / period;
    loss = (loss * (period - 1) + Math.max(0, -change)) / period;
    out[i] = value();
  }
  return out;
}
const rsiLast = (values, period = 14) => rsiSeries(values, period).at(-1);

function atrLast(bars, period = 14) {
  if (bars.length <= period) return null;
  const ranges = bars.map((bar, i) => i === 0 ? bar.high - bar.low : Math.max(
    bar.high - bar.low, Math.abs(bar.high - bars[i - 1].close), Math.abs(bar.low - bars[i - 1].close)
  ));
  let value = ranges.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  for (let i = period + 1; i < ranges.length; i++) value = (value * (period - 1) + ranges[i]) / period;
  return value;
}

function macdLast(values) {
  const fast = emaSeries(values, 12), slow = emaSeries(values, 26);
  const line = fast.map((value, i) => value - slow[i]);
  const signal = emaSeries(line, 9);
  return {line: line.at(-1), signal: signal.at(-1), hist: line.at(-1) - signal.at(-1)};
}

function stochRsiLast(values, period = 14) {
  const series = rsiSeries(values, period).filter(value => value != null);
  if (series.length < period) return null;
  const window = series.slice(-period), floor = Math.min(...window), ceiling = Math.max(...window);
  return ceiling === floor ? 50 : (window.at(-1) - floor) / (ceiling - floor) * 100;
}

function adxLast(bars, period = 14) {
  if (bars.length < period * 2 + 1) return null;
  const tr = [], plus = [], minus = [];
  for (let i = 1; i < bars.length; i++) {
    const current = bars[i], previous = bars[i - 1];
    const up = current.high - previous.high, down = previous.low - current.low;
    plus.push(up > down && up > 0 ? up : 0);
    minus.push(down > up && down > 0 ? down : 0);
    tr.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
  }
  const dx = [];
  for (let end = period; end <= tr.length; end++) {
    const total = tr.slice(end - period, end).reduce((a, b) => a + b, 0);
    if (!total) continue;
    const p = 100 * plus.slice(end - period, end).reduce((a, b) => a + b, 0) / total;
    const m = 100 * minus.slice(end - period, end).reduce((a, b) => a + b, 0) / total;
    dx.push(100 * Math.abs(p - m) / Math.max(p + m, 1e-12));
  }
  return dx.length >= period ? dx.slice(-period).reduce((a, b) => a + b, 0) / period : null;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function sessionVwap(bars) {
  const day = new Date(bars.at(-1).openTime).toISOString().slice(0, 10);
  const session = bars.filter(bar => new Date(bar.openTime).toISOString().slice(0, 10) === day);
  const volume = session.reduce((sum, bar) => sum + bar.volume, 0);
  return volume ? session.reduce((sum, bar) => sum + (bar.high + bar.low + bar.close) / 3 * bar.volume, 0) / volume : bars.at(-1).close;
}

function killZone(date) {
  const hour = date.getUTCHours();
  if (hour < 4) return 'ASIA';
  if (hour >= 8 && hour < 12) return 'LONDON';
  if (hour >= 12 && hour < 16) return 'NEW_YORK';
  return 'OFF_HOURS';
}

async function universe() {
  const [tickers, exchange] = await Promise.all([jget('/ticker/24hr'), jget('/exchangeInfo')]);
  const now = Date.now();
  const eligible = new Map(exchange.symbols.filter(item =>
    item.status === 'TRADING' && item.contractType === 'PERPETUAL' &&
    item.quoteAsset === 'USDT' && !STABLE.has(item.baseAsset) &&
    now - Number(item.onboardDate || 0) >= CFG.minListingDays * 86400000
  ).map(item => [item.symbol, item]));
  return tickers.filter(item => eligible.has(item.symbol) &&
    Math.abs(Number(item.priceChangePercent || 0)) <= CFG.maxAbsChange
  ).map(item => ({
    symbol: item.symbol, quoteVolume: Number(item.quoteVolume || 0), change: Number(item.priceChangePercent || 0)
  })).sort((a, b) => b.quoteVolume - a.quoteVolume).slice(0, CFG.universeSize);
}

async function market(context) {
  const symbol = encodeURIComponent(context.symbol);
  const [primaryRows, trendRows, higherRows, book, premium] = await Promise.all([
    jget(`/klines?symbol=${symbol}&interval=5m&limit=221`),
    jget(`/klines?symbol=${symbol}&interval=15m&limit=221`),
    jget(`/klines?symbol=${symbol}&interval=1h&limit=221`),
    jget(`/ticker/bookTicker?symbol=${symbol}`), jget(`/premiumIndex?symbol=${symbol}`)
  ]);
  const now = Date.now();
  return {
    ...context,
    primary: primaryRows.map(candle).filter(bar => bar.closeTime < now),
    trend: trendRows.map(candle).filter(bar => bar.closeTime < now),
    higher: higherRows.map(candle).filter(bar => bar.closeTime < now),
    bid: Number(book.bidPrice || 0), ask: Number(book.askPrice || 0),
    mark: Number(premium.markPrice || 0), fundingBp: Number(premium.lastFundingRate || 0) * 10000
  };
}

async function mapLimited(items, limit, fn) {
  const results = new Array(items.length);
  let cursor = 0;
  async function worker() {
    while (cursor < items.length) {
      const index = cursor++;
      try { results[index] = {value: await fn(items[index])}; }
      catch (error) { results[index] = {error: String(error)}; }
    }
  }
  await Promise.all(Array.from({length: Math.min(limit, items.length)}, worker));
  return results;
}

function makeTicket(signal, equity, now) {
  const direction = signal.side === 'LONG' ? 1 : -1;
  const stopDistance = Math.abs(signal.entry - signal.stop);
  const frictionPerUnit = signal.entry * signal.friction_bp / 10000;
  const effectiveRiskPerUnit = stopDistance + frictionPerUnit;
  const riskBudget = equity * CFG.riskPerTrade;
  const quantity = Math.max(0, Math.min(
    riskBudget / Math.max(effectiveRiskPerUnit, 1e-12),
    equity * CFG.maxNotional / Math.max(signal.entry, 1e-12)
  ));
  const notional = quantity * signal.entry, cost = notional * signal.friction_bp / 10000;
  const target = netR => signal.entry + direction * (netR * effectiveRiskPerUnit + frictionPerUnit);
  return {
    mode: 'PAPER_ONLY', status: signal.state,
    activation: signal.state === 'LIVE' ? 'ENTER_NOW' : 'CONDITIONAL_TRIGGER',
    signal_id: signal.signal_id, symbol: signal.symbol, side: signal.side, action: signal.action,
    created_at: now.toISOString(), entry_valid_until: signal.expires_at,
    entry: signal.entry, stop: signal.stop, tp1: target(1), tp2: target(2), tp3: target(3),
    time_exit: new Date(now.getTime() + CFG.maxHoldMinutes * 60000).toISOString(),
    invalidation: signal.invalidation, quantity, notional_usd: notional,
    risk_budget_usd: riskBudget, effective_risk_usd: quantity * stopDistance + cost,
    estimated_round_trip_cost_usd: cost, friction_bp: signal.friction_bp,
    friction_stop_pct: signal.friction_stop_pct, score: signal.score, confidence: signal.confidence
  };
}

function evaluate(snapshot, equity, now = new Date()) {
  const {primary, trend, higher} = snapshot;
  if (primary.length < 55 || trend.length < 55 || higher.length < 201) return {
    symbol: snapshot.symbol, state: 'INSUFFICIENT', side: null, score: 0,
    action: 'WAIT — indicator warm-up', price_change_pct_24h: snapshot.change,
    quote_volume_24h: snapshot.quoteVolume, blocked_by: ['Need 55×5m, 55×15m, and 201×1h closed candles']
  };
  const closes = primary.map(bar => bar.close), trendCloses = trend.map(bar => bar.close);
  const higherCloses = higher.map(bar => bar.close), current = primary.at(-1);
  const ema9 = emaLast(closes, 9), ema21 = emaLast(closes, 21);
  const trend20 = emaLast(trendCloses, 20), trend50 = emaLast(trendCloses, 50);
  const higher50 = emaLast(higherCloses, 50), higher200 = emaLast(higherCloses, 200);
  const currentRsi = rsiLast(closes), currentStoch = stochRsiLast(closes);
  const currentAtr = atrLast(primary), currentAdx = adxLast(primary), macd = macdLast(closes);
  const vwap = sessionVwap(primary), bb = closes.slice(-20);
  const bbMid = bb.reduce((a, b) => a + b, 0) / bb.length;
  const bbStd = Math.sqrt(bb.reduce((sum, value) => sum + (value - bbMid) ** 2, 0) / bb.length);
  const bbPosition = (current.close - bbMid) / Math.max(2 * bbStd, 1e-12);
  const midpoint = snapshot.bid > 0 && snapshot.ask > snapshot.bid ? (snapshot.bid + snapshot.ask) / 2 : current.close;
  const spreadBp = snapshot.bid > 0 && snapshot.ask > snapshot.bid ? (snapshot.ask - snapshot.bid) / midpoint * 10000 : Infinity;
  const mark = snapshot.mark > 0 ? snapshot.mark : current.close, atrPct = currentAtr / current.close * 100;
  const prior = primary.slice(-CFG.breakoutLookback - 1, -1);
  const high = Math.max(...prior.map(bar => bar.high)), low = Math.min(...prior.map(bar => bar.low));
  const volumeBase = median(primary.slice(-CFG.volumeLookback - 1, -1).map(bar => bar.quoteVolume));
  const volumeRatio = volumeBase > 0 ? current.quoteVolume / volumeBase : 0;
  const closeLocation = (current.close - current.low) / Math.max(current.high - current.low, 1e-12);
  const frictionBp = 2 * (CFG.feePerSideBp + CFG.slippagePerSideBp) + (Number.isFinite(spreadBp) ? spreadBp : CFG.maxSpreadBp * 10);
  const driftAtr = Math.abs(mark - current.close) / Math.max(currentAtr, 1e-12), zone = killZone(now);
  const buffer = Math.max(currentAtr * 0.05, mark * Math.min(spreadBp, CFG.maxSpreadBp) / 20000);
  const swingLow = Math.min(...primary.slice(-8, -1).map(bar => bar.low));
  const swingHigh = Math.max(...primary.slice(-8, -1).map(bar => bar.high));
  const definitions = {
    LONG: {
      higher: higherCloses.at(-1) > higher50 && higher50 > higher200,
      trend: trendCloses.at(-1) > trend20 && trend20 > trend50,
      alignment: current.close > ema9 && ema9 > ema21, macd: macd.hist > 0,
      momentum: currentRsi >= 52 && currentRsi <= 78 && currentStoch >= 55,
      adx: currentAdx >= CFG.minAdx, vwap: current.close > vwap,
      breakout: current.close > high, candle: closeLocation >= 0.60,
      level: high, trigger: high + buffer, trendName: 'BULLISH'
    },
    SHORT: {
      higher: higherCloses.at(-1) < higher50 && higher50 < higher200,
      trend: trendCloses.at(-1) < trend20 && trend20 < trend50,
      alignment: current.close < ema9 && ema9 < ema21, macd: macd.hist < 0,
      momentum: currentRsi >= 22 && currentRsi <= 48 && currentStoch <= 45,
      adx: currentAdx >= CFG.minAdx, vwap: current.close < vwap,
      breakout: current.close < low, candle: closeLocation <= 0.40,
      level: low, trigger: low - buffer, trendName: 'BEARISH'
    }
  };
  function scored(side) {
    const d = definitions[side], plannedEntry = d.breakout ? mark : d.trigger;
    const stop = side === 'LONG' ?
      Math.min(plannedEntry - CFG.stopAtr * currentAtr, swingLow - 0.1 * currentAtr) :
      Math.max(plannedEntry + CFG.stopAtr * currentAtr, swingHigh + 0.1 * currentAtr);
    const stopDistance = Math.abs(plannedEntry - stop), stopAtr = stopDistance / Math.max(currentAtr, 1e-12);
    const stopBp = stopDistance / plannedEntry * 10000, frictionStopPct = frictionBp / Math.max(stopBp, 1e-12) * 100;
    const execution = spreadBp <= CFG.maxSpreadBp && atrPct >= CFG.minAtrPct && atrPct <= CFG.maxAtrPct &&
      driftAtr <= CFG.maxEntryDriftAtr && stopAtr <= CFG.maxStopAtr && frictionStopPct <= CFG.maxFrictionStopPct &&
      2 * stopBp >= CFG.minRewardCostMultiple * frictionBp;
    const volumeOk = volumeRatio >= CFG.minVolumeRatio, controlledBreakout = d.breakout && d.candle;
    const weighted = [['higher', 15], ['trend', 15], ['alignment', 10], ['macd', 10], ['momentum', 10], ['adx', 10], ['vwap', 10]];
    let score = weighted.reduce((sum, [key, weight]) => sum + (d[key] ? weight : 0), 0) +
      (volumeOk ? 10 : 0) + (controlledBreakout ? 10 : 0) + (execution ? 10 : 0);
    const labels = {
      higher: '1h EMA50/200 regime', trend: '15m EMA20/50 trend', alignment: '5m EMA9/21 alignment',
      macd: 'MACD 12/26/9 histogram', momentum: 'RSI14 + Stoch RSI momentum',
      adx: `ADX14 strength ${currentAdx.toFixed(1)}`, vwap: 'session VWAP position'
    };
    const reasons = [], blocks = [];
    weighted.forEach(([key]) => (d[key] ? reasons : blocks).push(`${labels[key]} ${d[key] ? 'aligned' : 'not confirmed'}`));
    (volumeOk ? reasons : blocks).push(volumeOk ? `quote volume ${volumeRatio.toFixed(2)}× 20-bar median` : `volume ${volumeRatio.toFixed(2)}× below ${CFG.minVolumeRatio.toFixed(2)}× gate`);
    (controlledBreakout ? reasons : blocks).push(controlledBreakout ? `closed 5m breakout with directional close (${Math.round(closeLocation * 100)}%)` : 'closed 5m breakout/close-location trigger pending');
    if (execution) reasons.push(`friction ${frictionStopPct.toFixed(1)}% of stop distance`);
    else {
      if (spreadBp > CFG.maxSpreadBp) blocks.push(`spread ${spreadBp.toFixed(1)}bp above ${CFG.maxSpreadBp}bp`);
      if (atrPct < CFG.minAtrPct || atrPct > CFG.maxAtrPct) blocks.push(`ATR ${atrPct.toFixed(2)}% outside volatility band`);
      if (driftAtr > CFG.maxEntryDriftAtr) blocks.push(`mark drift ${driftAtr.toFixed(2)} ATR from signal close`);
      if (stopAtr > CFG.maxStopAtr) blocks.push(`structural stop ${stopAtr.toFixed(2)} ATR is too wide`);
      if (frictionStopPct > CFG.maxFrictionStopPct) blocks.push(`friction consumes ${frictionStopPct.toFixed(1)}% of stop`);
    }
    const nearTrigger = Math.abs(current.close - d.level) <= 1.5 * currentAtr || d.breakout;
    const coreBias = ['higher', 'trend', 'alignment'].filter(key => d[key]).length >= 2;
    const live = score >= CFG.signalScore && ['higher', 'trend', 'alignment', 'macd', 'momentum', 'adx', 'vwap'].every(key => d[key]) &&
      volumeOk && controlledBreakout && execution && zone !== 'OFF_HOURS';
    const armed = score >= CFG.armedScore && coreBias && nearTrigger && execution;
    return {...d, side, score: Math.min(100, score), entry: plannedEntry, stop, stopBp, frictionStopPct, reasons, blocks, live, armed};
  }
  const choice = [scored('LONG'), scored('SHORT')].sort((a, b) => b.score - a.score)[0];
  const state = choice.live ? 'LIVE' : choice.armed ? 'ARMED' : choice.score >= CFG.watchScore ? 'WATCH' : 'STAND_DOWN';
  const expires = new Date(now.getTime() + CFG.entryExpiryMinutes * 60000);
  const confidence = choice.score >= 90 ? 'A' : choice.score >= 80 ? 'A−' : choice.score >= 70 ? 'B' : 'C';
  const action = state === 'LIVE' ? `ENTER ${choice.side} NOW — closed-candle trigger confirmed` :
    state === 'ARMED' ? `${choice.side === 'LONG' ? 'BUY STOP' : 'SELL STOP'} ${price(choice.entry)} — cancel if not triggered by ${expires.toISOString().slice(11, 16)} UTC` :
    state === 'WATCH' ? `WAIT — ${choice.side.toLowerCase()} bias, confluence incomplete` : 'STAND DOWN — no coherent directional edge';
  const invalidation = `Cancel before entry if 5m closes ${choice.side === 'LONG' ? 'below' : 'above'} ${price(choice.level)}, spread exceeds ${CFG.maxSpreadBp}bp, or the ticket expires. After entry, the hard stop is final.`;
  if (zone === 'OFF_HOURS') choice.blocks.push('outside Asia/London/New York kill zone; LIVE disabled');
  const signal = {
    symbol: snapshot.symbol, state, side: choice.side, score: choice.score, action, confidence,
    signal_id: `${snapshot.symbol}:${current.closeTime}:${choice.side}:${state}`,
    candle_close_time: current.closeTime, entry: choice.entry, entry_trigger: choice.trigger, stop: choice.stop,
    spread_bp: Number.isFinite(spreadBp) ? spreadBp : null, friction_bp: frictionBp,
    friction_stop_pct: choice.frictionStopPct, stop_distance_bp: choice.stopBp,
    atr: currentAtr, atr_pct: atrPct, rsi_5m: currentRsi, stoch_rsi_5m: currentStoch,
    macd_hist_5m: macd.hist, adx_5m: currentAdx, vwap, bollinger_position: bbPosition,
    volume_ratio: volumeRatio, breakout_level: choice.level,
    higher_trend: choice.higher ? choice.trendName : 'MIXED', trend_15m: choice.trend ? choice.trendName : 'MIXED',
    kill_zone: zone, funding_bp: snapshot.fundingBp, price_change_pct_24h: snapshot.change,
    quote_volume_24h: snapshot.quoteVolume, expires_at: expires.toISOString(), invalidation,
    reasons: choice.reasons, blocked_by: choice.blocks, ticket: null
  };
  if (['LIVE', 'ARMED'].includes(state)) signal.ticket = makeTicket(signal, equity, now);
  return signal;
}

function newPaper() {
  const day = new Date().toISOString().slice(0, 10);
  return {
    version: 2, cash: CFG.startingEquity, equity: CFG.startingEquity,
    day, dayStartEquity: CFG.startingEquity, dailyPnl: 0, tradesToday: 0,
    positions: {}, history: [], seen: [], lastAction: null
  };
}

function loadPaper() {
  try {
    const value = JSON.parse(localStorage.getItem(PAPER_KEY));
    if (value && value.version === 2) return {...newPaper(), ...value, positions: value.positions || {}};
  } catch (_) {}
  return newPaper();
}
function savePaper(paper) { try { localStorage.setItem(PAPER_KEY, JSON.stringify(paper)); } catch (_) {} }
function rollPaperDay(paper, now) {
  const day = now.toISOString().slice(0, 10);
  if (paper.day !== day) { paper.day = day; paper.dayStartEquity = paper.cash; paper.dailyPnl = 0; paper.tradesToday = 0; }
}
function riskState(paper) {
  const lossLimit = paper.dayStartEquity * CFG.maxDailyLoss, blocked = [];
  if (paper.dailyPnl <= -lossLimit) blocked.push('daily loss limit reached');
  if (paper.tradesToday >= CFG.maxTrades) blocked.push('daily trade limit reached');
  if (Object.keys(paper.positions).length >= CFG.maxPositions) blocked.push('maximum open positions reached');
  return {
    can_open: !blocked.length, blocked_by: blocked, daily_loss_limit_usd: lossLimit,
    daily_realized_pnl_usd: paper.dailyPnl, trades_today: paper.tradesToday,
    max_trades_per_day: CFG.maxTrades, open_positions: Object.keys(paper.positions).length,
    max_open_positions: CFG.maxPositions
  };
}

function updatePaper(signals, prices, now) {
  const paper = loadPaper();
  rollPaperDay(paper, now);
  let unrealizedTotal = 0;
  for (const [symbol, position] of Object.entries(paper.positions)) {
    const mark = Number(prices[symbol] || 0);
    if (!mark) continue;
    const direction = position.side === 'LONG' ? 1 : -1;
    let reason = null, exit = mark;
    if ((position.side === 'LONG' && mark <= position.stop) || (position.side === 'SHORT' && mark >= position.stop)) reason = 'STOP';
    else if ((position.side === 'LONG' && mark >= position.tp2) || (position.side === 'SHORT' && mark <= position.tp2)) { reason = 'TP2'; exit = position.tp2; }
    else if (now >= new Date(position.time_exit)) reason = 'TIME_EXIT';
    else if (position.opened_at.slice(0, 10) !== now.toISOString().slice(0, 10)) reason = 'UTC_DAY_FLAT';
    const gross = direction * (exit - position.entry) * position.quantity;
    if (reason) {
      const net = gross - position.estimated_round_trip_cost_usd;
      paper.cash += net; paper.dailyPnl += net;
      paper.history.unshift({...position, status: 'CLOSED', exit, closed_at: now.toISOString(), exit_reason: reason, gross_pnl_usd: gross, cost_usd: position.estimated_round_trip_cost_usd, net_pnl_usd: net});
      paper.history = paper.history.slice(0, 200); delete paper.positions[symbol];
      paper.lastAction = `CLOSE ${symbol} ${reason} ${net >= 0 ? '+' : ''}${net.toFixed(2)} USD`;
    } else {
      position.mark = mark;
      position.unrealized_pnl_usd = direction * (mark - position.entry) * position.quantity - position.estimated_round_trip_cost_usd;
      unrealizedTotal += position.unrealized_pnl_usd;
    }
  }
  for (const signal of signals) {
    if (signal.state !== 'LIVE' || !signal.ticket || paper.seen.includes(signal.signal_id) || paper.positions[signal.symbol]) continue;
    const last = paper.history.find(item => item.symbol === signal.symbol);
    if (last && (now - new Date(last.closed_at)) / 60000 < CFG.cooldownMinutes) continue;
    if (!riskState(paper).can_open) break;
    const position = {...signal.ticket, status: 'OPEN', opened_at: now.toISOString(), mark: signal.entry, unrealized_pnl_usd: -signal.ticket.estimated_round_trip_cost_usd};
    paper.positions[signal.symbol] = position; paper.seen = [...paper.seen, signal.signal_id].slice(-500);
    paper.tradesToday += 1; paper.lastAction = `OPEN ${signal.side} ${signal.symbol} (browser paper)`;
  }
  paper.equity = paper.cash + unrealizedTotal; savePaper(paper);
  return {
    mode: 'PAPER_ONLY', cash_usd: paper.cash, equity_usd: paper.equity,
    starting_equity_usd: CFG.startingEquity, utc_day: paper.day,
    positions: Object.values(paper.positions), history: paper.history.slice(0, 20),
    last_action: paper.lastAction, risk: riskState(paper)
  };
}

async function scanStatic() {
  const started = performance.now(), contexts = await universe();
  const fetched = await mapLimited(contexts, 4, market);
  const snapshots = fetched.filter(item => item.value).map(item => item.value);
  const errors = fetched.filter(item => item.error).map(item => item.error);
  const paperBefore = loadPaper(), now = new Date();
  const signals = snapshots.map(item => evaluate(item, paperBefore.equity || CFG.startingEquity, now));
  const rank = {LIVE: 0, ARMED: 1, WATCH: 2, STAND_DOWN: 3, INSUFFICIENT: 4};
  signals.sort((a, b) => (rank[a.state] ?? 9) - (rank[b.state] ?? 9) || b.score - a.score || a.symbol.localeCompare(b.symbol));
  const prices = Object.fromEntries(snapshots.map(item => [item.symbol, item.mark || item.primary.at(-1).close]));
  const paper = updatePaper(signals, prices, now);
  return {
    app: 'CARRY-DAY', mode: 'PAPER_ONLY', runtime: 'STATIC_GITHUB_PAGES',
    research_status: 'UNVALIDATED_V2_AFTER_FAILED_V1', status: snapshots.length ? 'LIVE' : 'DEGRADED',
    last_scan: now.toISOString(), scan_duration_seconds: ((performance.now() - started) / 1000).toFixed(2),
    universe: contexts.map(item => item.symbol), signals,
    fires: signals.filter(item => item.state === 'LIVE').length, errors, paper
  };
}

async function refresh() {
  if (!STATIC_MODE) {
    try {
      const response = await fetch('/api/state', {cache: 'no-store'});
      if (response.ok) { render(await response.json()); return; }
    } catch (_) {}
  }
  if (lastStaticState && Date.now() - lastStaticScan < CFG.scanMs) { render(lastStaticState); return; }
  if (scanInFlight) return;
  scanInFlight = true; $('status').textContent = 'SCANNING';
  try {
    lastStaticState = await scanStatic(); lastStaticScan = Date.now(); render(lastStaticState);
  } catch (error) {
    $('status').textContent = 'DATA ERROR'; $('dot').className = 'dot';
    $('hero').innerHTML = 'MARKET DATA <em class="bad">UNAVAILABLE</em>';
    $('heroSub').textContent = `Direct Binance request failed: ${String(error)}. Retry after checking regional/API access.`;
  } finally { scanInFlight = false; }
}

setInterval(refresh, 5000);
refresh();
