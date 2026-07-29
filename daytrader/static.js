/* CARRY-DAY dual runtime: Python API locally, direct Binance data on GitHub Pages. */
'use strict';

const $ = id => document.getElementById(id);
const money = n => Number(n || 0).toLocaleString(undefined, {
  style: 'currency', currency: 'USD', maximumFractionDigits: 2
});
const num = (n, d = 1) => n == null ? '—' : Number(n).toFixed(d);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));
const pnlClass = n => Number(n) > 0 ? 'good' : Number(n) < 0 ? 'bad' : '';
const FAPI = 'https://fapi.binance.com/fapi/v1';
const PAPER_KEY = 'carry_day_static_paper_v1';
const STATIC_MODE = location.hostname.endsWith('.github.io') ||
  location.protocol === 'file:' || new URLSearchParams(location.search).has('static');
const CFG = Object.freeze({
  universeSize: 12, minListingDays: 30, maxAbsChange: 25,
  breakoutLookback: 20, volumeLookback: 20, minVolumeRatio: 1.2,
  signalScore: 75, watchScore: 55, maxSpreadBp: 8,
  minAtrPct: 0.12, maxAtrPct: 3, maxEntryDriftAtr: 0.5,
  stopAtr: 1.25, minRewardCostMultiple: 3,
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
  const state = s.state || 'NONE';
  const change = Number(s.price_change_pct_24h || 0);
  return `<tr>
    <td><span class="symbol">${esc(s.symbol)}</span></td>
    <td><span class="state ${state.toLowerCase()}">${esc(state)}</span></td>
    <td><span class="bar"><i style="width:${Math.min(100, s.score || 0)}%"></i></span> ${s.score || 0}</td>
    <td>${num(s.rsi_5m, 1)}</td><td class="${Number(s.volume_ratio) >= 1.2 ? 'amber' : ''}">${num(s.volume_ratio, 2)}×</td>
    <td>${num(s.atr_pct, 2)}%</td><td>${num(s.spread_bp, 2)}bp</td>
    <td class="${pnlClass(change)}">${change >= 0 ? '+' : ''}${num(change, 2)}%</td>
  </tr>`;
}

function positionCard(p) {
  const pnl = Number(p.unrealized_pnl_usd || 0);
  return `<div class="position">
    <div class="pos-head"><b>${esc(p.symbol)}</b><span class="state ${String(p.side).toLowerCase()}">${esc(p.side)}</span><span class="pnl ${pnlClass(pnl)}">${pnl >= 0 ? '+' : ''}${money(pnl)}</span></div>
    <div class="pos-grid">
      <div>ENTRY<b>${num(p.entry, 6)}</b></div><div>MARK<b>${num(p.mark, 6)}</b></div><div>SIZE<b>${money(p.notional_usd)}</b></div>
      <div>STOP<b class="bad">${num(p.stop, 6)}</b></div><div>TP2<b class="good">${num(p.tp2, 6)}</b></div><div>RISK<b>${money(p.effective_risk_usd)}</b></div>
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
    '<tr><td colspan="8" class="empty">No market snapshots available</td></tr>';
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
    'All hard limits clear. New qualifying signals may open in the local paper ledger.' :
    `New entries blocked: ${(risk.blocked_by || []).join(', ')}.`;
  const fires = Number(s.fires || 0);
  if (!risk.can_open) {
    $('hero').innerHTML = 'SESSION <em class="bad">LOCKED</em>';
    $('heroSub').textContent = (risk.blocked_by || []).join(' · ');
  } else if (fires) {
    $('hero').innerHTML = `${fires} SETUP${fires === 1 ? '' : 'S'} <em>QUALIFIED</em>`;
    $('heroSub').textContent = 'Every signal and cost gate cleared. Simulation only—no order was sent.';
  } else {
    $('hero').innerHTML = 'SCANNING <em>SELECTIVELY</em>';
    $('heroSub').textContent = 'No setup clears every trend, breakout, volume, momentum, cost, and risk gate.';
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

function emaLast(values, period) {
  const alpha = 2 / (period + 1);
  let value = values[0];
  for (let i = 1; i < values.length; i++) value = alpha * values[i] + (1 - alpha) * value;
  return value;
}

function rsiLast(values, period = 14) {
  if (values.length <= period) return null;
  let gain = 0, loss = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    gain += Math.max(0, change); loss += Math.max(0, -change);
  }
  gain /= period; loss /= period;
  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    gain = (gain * (period - 1) + Math.max(0, change)) / period;
    loss = (loss * (period - 1) + Math.max(0, -change)) / period;
  }
  if (!loss) return gain ? 100 : 50;
  return 100 - 100 / (1 + gain / loss);
}

function atrLast(bars, period = 14) {
  if (bars.length <= period) return null;
  const ranges = bars.map((bar, i) => i === 0 ? bar.high - bar.low : Math.max(
    bar.high - bar.low, Math.abs(bar.high - bars[i - 1].close),
    Math.abs(bar.low - bars[i - 1].close)
  ));
  let value = ranges.slice(1, period + 1).reduce((a, b) => a + b, 0) / period;
  for (let i = period + 1; i < ranges.length; i++) value = (value * (period - 1) + ranges[i]) / period;
  return value;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
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
    symbol: item.symbol,
    quoteVolume: Number(item.quoteVolume || 0),
    change: Number(item.priceChangePercent || 0)
  })).sort((a, b) => b.quoteVolume - a.quoteVolume).slice(0, CFG.universeSize);
}

async function market(context) {
  const symbol = encodeURIComponent(context.symbol);
  const [primaryRows, trendRows, book, premium] = await Promise.all([
    jget(`/klines?symbol=${symbol}&interval=5m&limit=181`),
    jget(`/klines?symbol=${symbol}&interval=15m&limit=181`),
    jget(`/ticker/bookTicker?symbol=${symbol}`),
    jget(`/premiumIndex?symbol=${symbol}`)
  ]);
  const now = Date.now();
  return {
    ...context,
    primary: primaryRows.map(candle).filter(bar => bar.closeTime < now),
    trend: trendRows.map(candle).filter(bar => bar.closeTime < now),
    bid: Number(book.bidPrice || 0), ask: Number(book.askPrice || 0),
    mark: Number(premium.markPrice || 0),
    fundingBp: Number(premium.lastFundingRate || 0) * 10000
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

function makeTicket(signal, equity) {
  const direction = signal.side === 'LONG' ? 1 : -1;
  const stopDistance = signal.atr * CFG.stopAtr;
  const frictionPerUnit = signal.entry * signal.friction_bp / 10000;
  const effectiveRiskPerUnit = stopDistance + frictionPerUnit;
  const riskBudget = equity * CFG.riskPerTrade;
  const quantity = Math.max(0, Math.min(
    riskBudget / Math.max(effectiveRiskPerUnit, 1e-12),
    equity * CFG.maxNotional / Math.max(signal.entry, 1e-12)
  ));
  const notional = quantity * signal.entry;
  const cost = notional * signal.friction_bp / 10000;
  const target = netR => signal.entry + direction *
    (netR * effectiveRiskPerUnit + frictionPerUnit);
  return {
    mode: 'PAPER_ONLY', signal_id: signal.signal_id, symbol: signal.symbol,
    side: signal.side, entry: signal.entry,
    stop: signal.entry - direction * stopDistance,
    tp1: target(1), tp2: target(2), tp3: target(3),
    time_exit: new Date(Date.now() + CFG.maxHoldMinutes * 60000).toISOString(),
    quantity, notional_usd: notional, risk_budget_usd: riskBudget,
    effective_risk_usd: quantity * stopDistance + cost,
    estimated_round_trip_cost_usd: cost, friction_bp: signal.friction_bp,
    score: signal.score
  };
}

function evaluate(snapshot, equity) {
  const primary = snapshot.primary, trend = snapshot.trend;
  if (primary.length < 55 || trend.length < 55) return {
    symbol: snapshot.symbol, state: 'INSUFFICIENT', side: null, score: 0,
    price_change_pct_24h: snapshot.change, quote_volume_24h: snapshot.quoteVolume
  };
  const closes = primary.map(bar => bar.close), trendCloses = trend.map(bar => bar.close);
  const current = primary[primary.length - 1];
  const ema9 = emaLast(closes, 9), ema21 = emaLast(closes, 21);
  const trend20 = emaLast(trendCloses, 20), trend50 = emaLast(trendCloses, 50);
  const currentRsi = rsiLast(closes), currentAtr = atrLast(primary);
  const midpoint = snapshot.bid > 0 && snapshot.ask > snapshot.bid ?
    (snapshot.bid + snapshot.ask) / 2 : current.close;
  const spreadBp = snapshot.bid > 0 && snapshot.ask > snapshot.bid ?
    (snapshot.ask - snapshot.bid) / midpoint * 10000 : Infinity;
  const entry = snapshot.mark > 0 ? snapshot.mark : current.close;
  const atrPct = currentAtr / current.close * 100;
  const prior = primary.slice(-CFG.breakoutLookback - 1, -1);
  const high = Math.max(...prior.map(bar => bar.high));
  const low = Math.min(...prior.map(bar => bar.low));
  const volumeBase = median(primary.slice(-CFG.volumeLookback - 1, -1).map(bar => bar.quoteVolume));
  const volumeRatio = volumeBase > 0 ? current.quoteVolume / volumeBase : 0;
  const closeLocation = (current.close - current.low) / Math.max(current.high - current.low, 1e-12);
  const frictionBp = 2 * (CFG.feePerSideBp + CFG.slippagePerSideBp) +
    (Number.isFinite(spreadBp) ? spreadBp : CFG.maxSpreadBp * 10);
  const reward2Bp = 2 * currentAtr * CFG.stopAtr / entry * 10000;
  const driftAtr = Math.abs(entry - current.close) / Math.max(currentAtr, 1e-12);
  const definitions = {
    LONG: {
      trend: trendCloses.at(-1) > trend20 && trend20 > trend50,
      alignment: current.close > ema9 && ema9 > ema21,
      breakout: current.close > high,
      momentum: currentRsi >= 52 && currentRsi <= 78,
      candle: closeLocation >= 0.65, level: high
    },
    SHORT: {
      trend: trendCloses.at(-1) < trend20 && trend20 < trend50,
      alignment: current.close < ema9 && ema9 < ema21,
      breakout: current.close < low,
      momentum: currentRsi >= 22 && currentRsi <= 48,
      candle: closeLocation <= 0.35, level: low
    }
  };
  function scored(side) {
    const d = definitions[side];
    let score = 0;
    if (d.trend) score += 25;
    if (d.alignment) score += 15;
    if (d.breakout) score += 20;
    if (d.momentum) score += 10;
    if (volumeRatio >= 1.5) score += 15;
    else if (volumeRatio >= CFG.minVolumeRatio) score += 8;
    if (d.candle) score += 10;
    if (spreadBp <= CFG.maxSpreadBp) score += 5;
    return {side, score, d};
  }
  const choices = [scored('LONG'), scored('SHORT')].sort((a, b) => b.score - a.score);
  const choice = choices[0], d = choice.d;
  const gate = choice.score >= CFG.signalScore && d.trend && d.alignment && d.breakout &&
    d.momentum && volumeRatio >= CFG.minVolumeRatio && spreadBp <= CFG.maxSpreadBp &&
    atrPct >= CFG.minAtrPct && atrPct <= CFG.maxAtrPct &&
    reward2Bp >= CFG.minRewardCostMultiple * frictionBp && driftAtr <= CFG.maxEntryDriftAtr;
  const signal = {
    symbol: snapshot.symbol,
    state: gate ? choice.side : (choice.score >= CFG.watchScore ? 'WATCH' : 'NONE'),
    side: gate ? choice.side : null, score: choice.score,
    signal_id: gate ? `${snapshot.symbol}:${current.closeTime}:${choice.side}` : null,
    candle_close_time: current.closeTime, entry, spread_bp: Number.isFinite(spreadBp) ? spreadBp : null,
    friction_bp: frictionBp, atr: currentAtr, atr_pct: atrPct,
    rsi_5m: currentRsi, volume_ratio: volumeRatio, breakout_level: d.level,
    funding_bp: snapshot.fundingBp, price_change_pct_24h: snapshot.change,
    quote_volume_24h: snapshot.quoteVolume, ticket: null
  };
  if (gate) signal.ticket = makeTicket(signal, equity);
  return signal;
}

function newPaper() {
  const day = new Date().toISOString().slice(0, 10);
  return {
    version: 1, cash: CFG.startingEquity, equity: CFG.startingEquity,
    day, dayStartEquity: CFG.startingEquity, dailyPnl: 0, tradesToday: 0,
    positions: {}, history: [], seen: [], lastAction: null
  };
}

function loadPaper() {
  try {
    const value = JSON.parse(localStorage.getItem(PAPER_KEY));
    if (value && value.version === 1) return {...newPaper(), ...value, positions: value.positions || {}};
  } catch (_) {}
  return newPaper();
}

function savePaper(paper) {
  try { localStorage.setItem(PAPER_KEY, JSON.stringify(paper)); } catch (_) {}
}

function rollPaperDay(paper, now) {
  const day = now.toISOString().slice(0, 10);
  if (paper.day !== day) {
    paper.day = day; paper.dayStartEquity = paper.cash;
    paper.dailyPnl = 0; paper.tradesToday = 0;
  }
}

function riskState(paper) {
  const lossLimit = paper.dayStartEquity * CFG.maxDailyLoss;
  const blocked = [];
  if (paper.dailyPnl <= -lossLimit) blocked.push('daily loss limit reached');
  if (paper.tradesToday >= CFG.maxTrades) blocked.push('daily trade limit reached');
  if (Object.keys(paper.positions).length >= CFG.maxPositions) blocked.push('maximum open positions reached');
  return {
    can_open: !blocked.length, blocked_by: blocked,
    daily_loss_limit_usd: lossLimit, daily_realized_pnl_usd: paper.dailyPnl,
    trades_today: paper.tradesToday, max_trades_per_day: CFG.maxTrades,
    open_positions: Object.keys(paper.positions).length, max_open_positions: CFG.maxPositions
  };
}

function updatePaper(signals, prices, now) {
  const paper = loadPaper();
  rollPaperDay(paper, now);
  let unrealizedTotal = 0;
  for (const [symbol, position] of Object.entries(paper.positions)) {
    const price = Number(prices[symbol] || 0);
    if (!price) continue;
    const direction = position.side === 'LONG' ? 1 : -1;
    let reason = null, exit = price;
    if ((position.side === 'LONG' && price <= position.stop) ||
        (position.side === 'SHORT' && price >= position.stop)) reason = 'STOP';
    else if ((position.side === 'LONG' && price >= position.tp2) ||
             (position.side === 'SHORT' && price <= position.tp2)) {
      reason = 'TP2'; exit = position.tp2;
    } else if (now >= new Date(position.time_exit)) reason = 'TIME_EXIT';
    else if (position.opened_at.slice(0, 10) !== now.toISOString().slice(0, 10)) reason = 'UTC_DAY_FLAT';
    const gross = direction * (exit - position.entry) * position.quantity;
    if (reason) {
      const net = gross - position.estimated_round_trip_cost_usd;
      paper.cash += net; paper.dailyPnl += net;
      paper.history.unshift({...position, status: 'CLOSED', exit, closed_at: now.toISOString(),
        exit_reason: reason, gross_pnl_usd: gross, cost_usd: position.estimated_round_trip_cost_usd,
        net_pnl_usd: net});
      paper.history = paper.history.slice(0, 200);
      delete paper.positions[symbol];
      paper.lastAction = `CLOSE ${symbol} ${reason} ${net >= 0 ? '+' : ''}${net.toFixed(2)} USD`;
    } else {
      const liveGross = direction * (price - position.entry) * position.quantity;
      position.mark = price;
      position.unrealized_pnl_usd = liveGross - position.estimated_round_trip_cost_usd;
      unrealizedTotal += position.unrealized_pnl_usd;
    }
  }
  for (const signal of signals) {
    if (!signal.ticket || paper.seen.includes(signal.signal_id) || paper.positions[signal.symbol]) continue;
    const last = paper.history.find(item => item.symbol === signal.symbol);
    if (last && (now - new Date(last.closed_at)) / 60000 < CFG.cooldownMinutes) continue;
    if (!riskState(paper).can_open) break;
    const position = {...signal.ticket, status: 'OPEN', opened_at: now.toISOString(),
      mark: signal.entry, unrealized_pnl_usd: -signal.ticket.estimated_round_trip_cost_usd};
    paper.positions[signal.symbol] = position;
    paper.seen = [...paper.seen, signal.signal_id].slice(-500);
    paper.tradesToday += 1;
    paper.lastAction = `OPEN ${signal.side} ${signal.symbol} (browser paper)`;
  }
  paper.equity = paper.cash + unrealizedTotal;
  savePaper(paper);
  return {
    mode: 'PAPER_ONLY', cash_usd: paper.cash, equity_usd: paper.equity,
    starting_equity_usd: CFG.startingEquity, utc_day: paper.day,
    positions: Object.values(paper.positions), history: paper.history.slice(0, 20),
    last_action: paper.lastAction, risk: riskState(paper)
  };
}

async function scanStatic() {
  const started = performance.now();
  const contexts = await universe();
  const fetched = await mapLimited(contexts, 4, market);
  const snapshots = fetched.filter(item => item.value).map(item => item.value);
  const errors = fetched.filter(item => item.error).map(item => item.error);
  const paperBefore = loadPaper();
  const signals = snapshots.map(item => evaluate(item, paperBefore.equity || CFG.startingEquity));
  signals.sort((a, b) => {
    const af = ['LONG', 'SHORT'].includes(a.state) ? 0 : 1;
    const bf = ['LONG', 'SHORT'].includes(b.state) ? 0 : 1;
    return af - bf || b.score - a.score || a.symbol.localeCompare(b.symbol);
  });
  const prices = Object.fromEntries(snapshots.map(item => [item.symbol, item.mark || item.primary.at(-1).close]));
  const now = new Date();
  const paper = updatePaper(signals, prices, now);
  return {
    app: 'CARRY-DAY', mode: 'PAPER_ONLY', runtime: 'STATIC_GITHUB_PAGES',
    research_status: 'FAILED_INITIAL_GATE', status: snapshots.length ? 'LIVE' : 'DEGRADED',
    last_scan: now.toISOString(), scan_duration_seconds: ((performance.now() - started) / 1000).toFixed(2),
    universe: contexts.map(item => item.symbol), signals,
    fires: signals.filter(item => ['LONG', 'SHORT'].includes(item.state)).length,
    errors, paper
  };
}

async function refresh() {
  if (!STATIC_MODE) {
    try {
      const response = await fetch('/api/state', {cache: 'no-store'});
      if (response.ok) { render(await response.json()); return; }
    } catch (_) {}
  }
  if (lastStaticState && Date.now() - lastStaticScan < CFG.scanMs) {
    render(lastStaticState); return;
  }
  if (scanInFlight) return;
  scanInFlight = true;
  $('status').textContent = 'SCANNING';
  try {
    lastStaticState = await scanStatic();
    lastStaticScan = Date.now();
    render(lastStaticState);
  } catch (error) {
    $('status').textContent = 'DATA ERROR';
    $('dot').className = 'dot';
    $('hero').innerHTML = 'MARKET DATA <em class="bad">UNAVAILABLE</em>';
    $('heroSub').textContent = `Direct Binance request failed: ${String(error)}. Retry after checking regional/API access.`;
  } finally {
    scanInFlight = false;
  }
}

setInterval(refresh, 5000);
refresh();
