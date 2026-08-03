/* CARRY-DAY dual runtime: Python API locally, direct Binance data on GitHub Pages. */
'use strict';

const $ = id => document.getElementById(id);
const money = n => Number(n || 0).toLocaleString(undefined, {
  style: 'currency', currency: 'USD', maximumFractionDigits: 2
});
const num = (n, d = 1) => n == null ? '—' : Number(n).toFixed(d);
const signedPct = (n, d = 2) => n == null ? '—' : `${Number(n) >= 0 ? '+' : ''}${Number(n).toFixed(d)}%`;
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
const compactUsd = n => n == null ? '—' : Number(n).toLocaleString(undefined, {
  style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 1
});
const API_BASE = 'https://fapi.binance.com';
const FAPI = API_BASE + '/fapi/v1';
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
  liquiditySweepLookback: 8, swingLeftBars: 2, swingRightBars: 2,
  sweepBufferAtr: 0.03, mssBufferAtr: 0.02, displacementBodyRatio: 1.20,
  fvgMinAtr: 0.08, fvgEntryMaxDistanceAtr: 0.75,
  liquiditySignalScore: 85, liquidityArmedScore: 80, liquidityStopMinAtr: 0.75,
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
  const verdict = String(s.derivatives_verdict || 'UNAVAILABLE');
  const playbook = s.playbook === 'LIQUIDITY_MSS_FVG' ? 'B · LIQ/MSS' : 'A · MOMENTUM';
  return `<tr>
    <td><span class="symbol">${esc(s.symbol)}</span></td>
    <td><span class="state ${state.toLowerCase()}">${esc(state.replace('_', ' '))}</span></td>
    <td><span class="playbook-mini ${s.playbook === 'LIQUIDITY_MSS_FVG' ? 'liquidity' : ''}">${esc(playbook)}</span></td>
    <td class="${s.side === 'LONG' ? 'good' : s.side === 'SHORT' ? 'bad' : ''}">${esc(s.side || '—')}</td>
    <td><span class="bar"><i style="width:${Math.min(100, s.score || 0)}%"></i></span> ${s.score || 0}</td>
    <td>${num(s.rsi_5m, 1)} / ${num(s.adx_5m, 1)}</td>
    <td class="${Number(s.volume_ratio) >= 1.2 ? 'amber' : ''}">${num(s.volume_ratio, 2)}×</td>
    <td>${num(s.friction_stop_pct, 1)}%</td><td>${num(s.spread_bp, 2)}bp</td>
    <td><span class="deriv-pill ${verdict.toLowerCase()}">${esc(verdict)}</span></td>
    <td class="${pnlClass(change)}">${change >= 0 ? '+' : ''}${num(change, 2)}%</td>
  </tr>`;
}

function playbookPanel(signal) {
  const liquidity = signal.playbook === 'LIQUIDITY_MSS_FVG';
  const confirmation = String(signal.playbook_confirmation || 'SINGLE').replaceAll('_', ' ');
  const fields = liquidity ? [
    ['LIQUIDITY', signal.liquidity_level_name || 'PENDING'],
    ['LEVEL / SWEEP', `${price(signal.liquidity_level)} / ${price(signal.sweep_price)}`],
    ['MSS LEVEL', price(signal.mss_level)],
    ['DISPLACEMENT', signal.displacement_ratio == null ? '—' : `${num(signal.displacement_ratio, 2)}× body`],
    ['FVG RANGE', signal.fvg_low == null ? 'PENDING' : `${price(signal.fvg_low)} – ${price(signal.fvg_high)}`],
    ['50% ENTRY', price(signal.fvg_mid)],
    ['DEALING RANGE', `${esc(signal.premium_discount || 'UNKNOWN')} · mid ${price(signal.dealing_range_mid)}`]
  ] : [
    ['TRIGGER', 'CLOSED 5M BREAKOUT'],
    ['BREAKOUT LEVEL', price(signal.breakout_level)],
    ['ENTRY LOGIC', signal.state === 'LIVE' ? 'MARK AFTER CLOSE' : 'STOP TRIGGER'],
    ['HTF FILTER', `${esc(signal.higher_trend || 'MIXED')} / ${esc(signal.trend_15m || 'MIXED')}`],
    ['MOMENTUM', `RSI ${num(signal.rsi_5m, 1)} · ADX ${num(signal.adx_5m, 1)}`],
    ['VOLUME', `${num(signal.volume_ratio, 2)}× MEDIAN`],
    ['SESSION', signal.kill_zone || '—']
  ];
  return `<section class="playbook-context ${liquidity ? 'liquidity' : ''}" aria-label="Selected strategy playbook evidence">
    <div class="playbook-head">
      <div><span class="playbook-kicker">${esc(signal.playbook_label || 'PLAYBOOK A · MOMENTUM')}</span><small>${liquidity ? 'SWEEP → MSS → DISPLACEMENT → FVG RETEST' : 'REGIME → TREND → MOMENTUM BREAKOUT'}</small></div>
      <span class="playbook-confirmation">${esc(confirmation)}</span>
    </div>
    <div class="playbook-grid">${fields.map(([label, value]) => `<div><span>${esc(label)}</span><b>${value}</b></div>`).join('')}</div>
  </section>`;
}

function derivativesPanel(signal) {
  const verdict = String(signal.derivatives_verdict || 'UNAVAILABLE');
  const reasons = (signal.derivatives_reasons || []).slice(0, 3)
    .map(item => `<li>${esc(item)}</li>`).join('');
  const risk = String(signal.leverage_risk || 'UNKNOWN').replaceAll('_', ' ');
  return `<section class="derivatives" aria-label="Derivatives positioning context">
    <div class="derivatives-head">
      <div><span class="derivatives-kicker">DERIVATIVES CONTEXT</span><small>BINANCE PUBLIC 5M FEEDS · NOT A LIQUIDATION MAP</small></div>
      <span class="deriv-pill ${verdict.toLowerCase()}">${esc(verdict)}</span>
    </div>
    <div class="derivatives-grid">
      <div><span>OPEN INTEREST</span><b>${compactUsd(signal.open_interest_usd)}</b></div>
      <div><span>OI · 15M</span><b class="${pnlClass(signal.open_interest_change_15m_pct)}">${signedPct(signal.open_interest_change_15m_pct)}</b></div>
      <div><span>OI · 1H</span><b class="${pnlClass(signal.open_interest_change_1h_pct)}">${signedPct(signal.open_interest_change_1h_pct)}</b></div>
      <div><span>TAKER BUY / SELL</span><b>${num(signal.taker_buy_sell_ratio_15m, 2)}×</b></div>
      <div><span>LONG / SHORT ACCTS</span><b>${num(signal.long_short_account_ratio, 2)}×</b></div>
      <div><span>LEVERAGE RISK</span><b>${esc(risk)}</b></div>
    </div>
    <ul class="derivatives-notes">${reasons}</ul>
  </section>`;
}

function ticketCard(signal) {
  const t = signal.ticket;
  const sideClass = signal.side === 'LONG' ? 'good' : 'bad';
  const evidence = (signal.reasons || []).map(item => `<span class="evidence good-evidence">✓ ${esc(item)}</span>`).join('');
  const blocks = (signal.blocked_by || []).slice(0, 4).map(item => `<span class="evidence block-evidence">• ${esc(item)}</span>`).join('');
  const expiry = t.entry_valid_until ? new Date(t.entry_valid_until).toISOString().slice(11, 16) + ' UTC' : '—';
  return `<article class="ticket ${String(signal.state).toLowerCase()}">
    <div class="ticket-head">
      <div><span class="state ${String(signal.state).toLowerCase()}">${esc(signal.state)}</span><span class="ticket-symbol">${esc(signal.symbol)}</span><span class="ticket-side ${sideClass}">${esc(signal.side)}</span><span class="playbook-mini ${signal.playbook === 'LIQUIDITY_MSS_FVG' ? 'liquidity' : ''}">${esc(signal.playbook === 'LIQUIDITY_MSS_FVG' ? 'B · LIQ/MSS' : 'A · MOMENTUM')}</span></div>
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
    ${playbookPanel(signal)}
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
    ${derivativesPanel(signal)}
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

function quantrexCard(c) {
  const side = c.side === 'LONG' ? 'good' : 'bad';
  const blocks = (c.blocked_by || []).map(item => esc(item)).join(' · ');
  const timeExit = c.time_exit ? new Date(Number(c.time_exit)).toISOString().slice(11, 16) + ' UTC' : '—';
  return `<article class="quantrex-card">
    <h3><span class="state ${String(c.state).toLowerCase()}">${esc(c.state)}</span>${esc(c.symbol)} <span class="${side}">${esc(c.side)}</span><small>${esc(c.book)}</small></h3>
    <div class="quantrex-levels">
      <div><span>ENTRY · NEXT QUOTE</span><b>${price(c.entry)}</b></div>
      <div class="sl"><span>STOP LOSS</span><b>${price(c.stop)}</b></div>
      <div class="tp"><span>TAKE PROFIT 1 · 50% · NET 1R</span><b>${price(c.tp1)}</b></div>
      <div class="tp"><span>TAKE PROFIT 2 · 30% · NET 1.5R</span><b>${price(c.tp2)}</b></div>
      <div class="tp"><span>TAKE PROFIT 3 · 20% · NET 2R</span><b>${price(c.tp3)}</b></div>
      <div><span>KILL ZONE</span><b>${esc(c.kill_zone)}</b></div>
      <div><span>TIME EXIT</span><b>${timeExit}</b></div>
      <div><span>RISK / SIZE</span><b>${money(c.risk_usd)} · ${num(c.quantity, 6)}</b></div>
      <div><span>ROUND-TRIP COST</span><b>${money((c.costs || {}).round_trip_usd)}</b></div>
    </div>
    <div class="quantrex-blocks">${blocks ? `BLOCKED · ${blocks}` : 'SHADOW ORDER READY · physically unable to submit'}</div>
    <div class="quantrex-chart" id="quantrexChart-${esc(c.idempotency_key).replace(/[^a-zA-Z0-9_-]/g, '-')}"><div class="chart-fallback">Loading completed 15-minute candles…</div></div>
  </article>`;
}

function renderNativeQuantrexChart(container, candidate, sessions, runs) {
  const bars = candidate.chart_bars || [];
  const width = Math.max(container.clientWidth || 640, 320);
  const height = 218;
  const pad = { top: 15, right: 70, bottom: 20, left: 8 };
  const levels = [candidate.entry, candidate.stop, candidate.tp1, candidate.tp2, candidate.tp3].filter(value => value !== null && value !== undefined && Number.isFinite(Number(value))).map(Number);
  const prices = bars.flatMap(bar => [Number(bar.high), Number(bar.low)]).concat(levels);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const range = Math.max(high - low, Math.abs(high) * 0.0001, 1e-8);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const x = index => pad.left + ((index + .5) / bars.length) * plotWidth;
  const y = value => pad.top + ((high - Number(value)) / range) * plotHeight;
  const candleWidth = Math.max(1, Math.min(6, plotWidth / bars.length * .62));
  const candles = bars.map((bar, index) => {
    const open = Number(bar.open), close = Number(bar.close), color = close >= open ? '#32d49a' : '#ff657a';
    const bodyTop = Math.min(y(open), y(close));
    const bodyHeight = Math.max(1, Math.abs(y(open) - y(close)));
    return `<line x1="${x(index)}" y1="${y(bar.high)}" x2="${x(index)}" y2="${y(bar.low)}" stroke="${color}" stroke-width="1"/><rect x="${x(index) - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" fill="${color}"/>`;
  }).join('');
  const priceLines = [
    ['ENTRY', candidate.entry, '#eef4ff'], ['SL', candidate.stop, '#ff657a'],
    ['TP1', candidate.tp1, '#f7bf55'], ['TP2', candidate.tp2, '#32d49a'], ['TP3', candidate.tp3, '#3dd9eb'],
  ].filter(([, value]) => value !== null && value !== undefined && Number.isFinite(Number(value))).map(([label, value, color]) => `<line x1="${pad.left}" y1="${y(value)}" x2="${width - pad.right}" y2="${y(value)}" stroke="${color}" stroke-width="1" stroke-dasharray="4 4"/><text x="${width - pad.right + 5}" y="${y(value) + 3}" fill="${color}" font-size="8" font-family="monospace">${label} ${price(value)}</text>`).join('');
  container.innerHTML = `<div class="quantrex-session-strip" aria-label="Kill Zone session shading">${runs.map(run => `<i class="${run.name}" style="width:${run.count / sessions.length * 100}%"></i>`).join('')}</div><svg class="quantrex-chart-canvas" viewBox="0 0 ${width} ${height}" role="img" aria-label="Completed 15-minute candlestick chart with entry, Stop Loss, and Take Profit levels" preserveAspectRatio="none"><line x1="${pad.left}" y1="${pad.top + plotHeight}" x2="${width - pad.right}" y2="${pad.top + plotHeight}" stroke="#202b3f"/>${candles}${priceLines}</svg>`;
}

function renderQuantrexChart(candidate) {
  const id = `quantrexChart-${String(candidate.idempotency_key).replace(/[^a-zA-Z0-9_-]/g, '-')}`;
  const container = $(id);
  const lib = window.LightweightCharts;
  if (!container || !(candidate.chart_bars || []).length) {
    if (container) container.innerHTML = '<div class="chart-fallback">Completed candles unavailable. Trading logic remains fail-closed.</div>';
    return;
  }
  const sessions = (candidate.chart_bars || []).map(bar => {
    const hour = new Date(Number(bar.time) * 1000).getUTCHours();
    return hour >= 7 && hour < 11 ? 'london' : hour >= 13 && hour < 17 ? 'new-york' : 'outside';
  });
  const runs = sessions.reduce((items, name) => {
    const last = items[items.length - 1];
    if (last && last.name === name) last.count += 1;
    else items.push({ name, count: 1 });
    return items;
  }, []);
  if (!lib) {
    renderNativeQuantrexChart(container, candidate, sessions, runs);
    return;
  }
  container.innerHTML = `<div class="quantrex-session-strip" aria-label="Kill Zone session shading">${runs.map(run => `<i class="${run.name}" style="width:${run.count / sessions.length * 100}%"></i>`).join('')}</div><div class="quantrex-chart-canvas"></div>`;
  const canvasHost = container.querySelector('.quantrex-chart-canvas');
  const chart = lib.createChart(canvasHost, {
    width: container.clientWidth,
    height: 218,
    layout: { background: { type: 'solid', color: '#080e18' }, textColor: '#7e8da8' },
    grid: { vertLines: { color: '#151e2c' }, horzLines: { color: '#151e2c' } },
    rightPriceScale: { borderColor: '#202b3f' },
    timeScale: { borderColor: '#202b3f', timeVisible: true, secondsVisible: false },
  });
  const candles = chart.addSeries(lib.CandlestickSeries, {
    upColor: '#32d49a', downColor: '#ff657a', borderVisible: false,
    wickUpColor: '#32d49a', wickDownColor: '#ff657a', priceLineVisible: false,
  });
  candles.setData(candidate.chart_bars);
  const levels = [
    ['ENTRY', candidate.entry, '#eef4ff', 0],
    ['SL · STOP LOSS', candidate.stop, '#ff657a', 2],
    ['TP1 · 50%', candidate.tp1, '#f7bf55', 2],
    ['TP2 · 30%', candidate.tp2, '#32d49a', 2],
    ['TP3 · 20%', candidate.tp3, '#3dd9eb', 2],
  ];
  levels.filter(([, value]) => value !== null && value !== undefined && Number.isFinite(Number(value))).forEach(([title, value, color, lineStyle]) => {
    candles.createPriceLine({ price: Number(value), color, lineWidth: 1, lineStyle, axisLabelVisible: true, title });
  });
  chart.timeScale().fitContent();
  const resize = new ResizeObserver(entries => entries.forEach(entry => chart.applyOptions({ width: entry.contentRect.width })));
  resize.observe(container);
}

function quantrexMarketCard(book) {
  const candidate = (book.candidates || [])[0];
  if (candidate) return quantrexCard(candidate);
  return `<article class="quantrex-card">
    <h3><span class="state watch">${esc(book.status || 'NO SIGNAL')}</span>${esc(book.symbol)}<small>FLAT CONTROL</small></h3>
    <div class="quantrex-blocks">No versioned QSR-1 or Breakout candidate on the latest completed 15-minute bar. Position remains zero.</div>
    <div class="quantrex-chart" id="quantrexChart-market-${esc(book.symbol)}"><div class="chart-fallback">Loading completed 15-minute candles…</div></div>
    <div class="quantrex-session-key">KILL ZONE SHADING · CYAN LONDON 07–11 · BLUE NEW YORK 13–17 UTC</div>
  </article>`;
}

function renderQuantrexMarketChart(book) {
  if ((book.candidates || []).length) {
    book.candidates.forEach(renderQuantrexChart);
    return;
  }
  renderQuantrexChart({
    idempotency_key: `market-${book.symbol}`,
    chart_bars: book.chart_bars || [],
    entry: null, stop: null, tp1: null, tp2: null, tp3: null,
  });
}

let latestQuantrex = null;

function renderQuantrex(q) {
  if (!q) return;
  latestQuantrex = q;
  $('quantrexStatus').textContent = `${q.status || 'UNKNOWN'} · ${q.no_submit === false ? 'UNSAFE' : 'NO_SUBMIT'}`;
  $('quantrexStatus').className = `count ${q.no_submit === false ? 'bad' : 'good'}`;
  const forward = q.forward_paper || {};
  const killActive = Boolean((((forward.broker || {}).risk || {}).kill_switch));
  $('quantrexForward').textContent = `${forward.mode || 'STARTING'} · SIGNALS ${forward.signals || 0} · FILLS ${forward.fills || 0} · KILL ${killActive ? 'ACTIVE' : 'CLEAR'}`;
  const books = q.books || [];
  $('quantrexBooks').innerHTML = books.length ? books.map(quantrexMarketCard).join('') :
    '<div class="quantrex-empty">Waiting for validated completed 15-minute USD-M bars.</div>';
  books.forEach(renderQuantrexMarketChart);
  const sortMode = ($('quantrexSort') || {}).value || 'volume';
  const filterMode = ($('quantrexFilter') || {}).value || 'all';
  const sortFields = { volume: 'quote_volume_24h', change: 'price_change_pct_24h', spread: 'spread_bp', oi: 'open_interest_usd', oi15: 'open_interest_change_15m_pct', oi1h: 'open_interest_change_1h_pct', taker: 'taker_buy_sell_ratio_15m', funding: 'funding_bp' };
  const source = [...(q.discovery || [])];
  const volumes = source.map(item => Number(item.quote_volume_24h || 0)).sort((a, b) => a - b);
  const medianVolume = volumes.length ? volumes[Math.floor(volumes.length / 2)] : 0;
  const filtered = source.filter(item => {
    if (filterMode === 'high_volume') return Number(item.quote_volume_24h || 0) >= medianVolume;
    if (filterMode === 'oi_spike') return Math.abs(Number(item.open_interest_change_1h_pct || 0)) >= 1;
    if (filterMode === 'big_movers') return Math.abs(Number(item.price_change_pct_24h || 0)) >= 5;
    if (filterMode === 'high_funding') return Math.abs(Number(item.funding_bp || 0)) >= 3;
    return true;
  });
  const discovery = filtered.sort((a, b) => {
    if (sortMode === 'pair') return String(a.symbol).localeCompare(String(b.symbol));
    if (sortMode === 'book') return Number(Boolean(b.scored_book)) - Number(Boolean(a.scored_book)) || String(a.symbol).localeCompare(String(b.symbol));
    const field = sortFields[sortMode] || 'quote_volume_24h';
    return Number(b[field] || 0) - Number(a[field] || 0);
  });
  $('quantrexDiscovery').innerHTML = discovery.length ? discovery.map((item, index) => `<tr>
    <td>${index + 1}</td><td class="symbol">${esc(item.symbol)}</td><td class="${pnlClass(Number(item.price_change_pct_24h || 0))}">${num(item.price_change_pct_24h, 2)}%</td><td>${money(item.quote_volume_24h)}</td>
    <td>${num(item.spread_bp, 2)}bp</td><td>${money(item.open_interest_usd)}</td>
    <td class="${pnlClass(Number(item.open_interest_change_15m_pct || 0))}">${num(item.open_interest_change_15m_pct, 2)}%</td>
    <td class="${pnlClass(Number(item.open_interest_change_1h_pct || 0))}">${num(item.open_interest_change_1h_pct, 2)}%</td>
    <td>${num(item.taker_buy_sell_ratio_15m, 2)}×</td><td>${num(item.funding_bp, 2)}bp</td>
    <td>${item.scored_book ? '<span class="state live">SCORED V0</span>' : '<span class="state watch">DISCOVERY</span>'}</td>
  </tr>`).join('') : '<tr><td colspan="11" class="empty">No discovery snapshots available</td></tr>';
}

if ($('quantrexSort')) $('quantrexSort').addEventListener('change', () => latestQuantrex && renderQuantrex(latestQuantrex));
if ($('quantrexFilter')) $('quantrexFilter').addEventListener('change', () => latestQuantrex && renderQuantrex(latestQuantrex));

function render(s) {
  const paper = s.paper || {};
  const risk = paper.risk || {};
  const signals = s.signals || [];
  const positions = paper.positions || [];
  const history = paper.history || [];
  const live = signals.filter(item => item.state === 'LIVE');
  const armed = signals.filter(item => item.state === 'ARMED');
  // Signals arrive ranked LIVE → ARMED → WATCH, then by confluence score.
  // Keep the desk decisive: one strongest actionable plan, never a menu of trades.
  const tickets = signals.filter(item => item.ticket).slice(0, 1);
  const strongest = tickets[0] || null;
  renderQuantrex(s.quantrex);
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
    '<tr><td colspan="11" class="empty">No market snapshots available</td></tr>';
  $('ticketCount').textContent = strongest ?
    `STRONGEST · ${strongest.symbol} · ${strongest.playbook === 'LIQUIDITY_MSS_FVG' ? 'PLAYBOOK B' : 'PLAYBOOK A'} · ${strongest.state} · ${strongest.score}` :
    'NO ACTIONABLE TICKET';
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
    $('hero').innerHTML = 'STRONGEST SETUP <em>LIVE</em>';
    $('heroSub').textContent = `${strongest.symbol} ${strongest.side} · ${strongest.playbook_label} · ${strongest.score}/100 · derivatives ${String(strongest.derivatives_verdict || 'unavailable').toLowerCase()}. Every trigger, execution-cost, context, and risk gate cleared. Paper simulation only.`;
  } else if (armed.length) {
    $('hero').innerHTML = 'STRONGEST SETUP <em class="amber">ARMED</em>';
    $('heroSub').textContent = `${strongest.symbol} ${strongest.side} · ${strongest.playbook_label} · ${strongest.score}/100 · derivatives ${String(strongest.derivatives_verdict || 'unavailable').toLowerCase()}. Do not enter before the displayed trigger; every unresolved gate remains visible.`;
  } else {
    $('hero').innerHTML = 'STAND <em>DOWN</em>';
    $('heroSub').textContent = 'No setup currently has coherent multi-timeframe confluence. Waiting is the trade.';
  }
}

async function jget(path) {
  const base = path.startsWith('/futures/') ? API_BASE : FAPI;
  const response = await fetch(base + path, {cache: 'no-store'});
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response.json();
}

async function optionalJson(path) {
  try { return await jget(path); }
  catch (_) { return null; }
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

function confirmedSwings(bars, left = 2, right = 2) {
  const highs = [], lows = [];
  if (left < 1 || right < 1) throw new Error('swing confirmation needs bars on both sides');
  for (let index = left; index < bars.length - right; index++) {
    const bar = bars[index], neighbors = [
      ...bars.slice(index - left, index), ...bars.slice(index + 1, index + right + 1)
    ];
    if (neighbors.every(item => bar.high > item.high)) highs.push({
      index, confirmationIndex: index + right, price: bar.high, time: bar.closeTime
    });
    if (neighbors.every(item => bar.low < item.low)) lows.push({
      index, confirmationIndex: index + right, price: bar.low, time: bar.closeTime
    });
  }
  return {highs, lows};
}

function findFairValueGaps(bars, side, startIndex, atrValue, minGapAtr = 0.08) {
  const gaps = [], minimum = Math.max(0, atrValue * minGapAtr);
  for (let index = Math.max(2, startIndex); index < bars.length; index++) {
    const first = bars[index - 2], third = bars[index];
    const low = side === 'LONG' ? first.high : third.high;
    const high = side === 'LONG' ? third.low : first.low;
    if (high - low < minimum) continue;
    const mid = (low + high) / 2, later = bars.slice(index + 1);
    const fullyFilled = side === 'LONG' ? later.some(bar => bar.low <= low) : later.some(bar => bar.high >= high);
    let retestIndex = null;
    for (let offset = 0; offset < later.length; offset++) {
      const bar = later[offset];
      const touched = side === 'LONG' ? bar.low <= mid && bar.close > low : bar.high >= mid && bar.close < high;
      if (touched) { retestIndex = index + 1 + offset; break; }
    }
    if (!fullyFilled) gaps.push({index, low, high, mid, retestIndex, sizeAtr: (high - low) / Math.max(atrValue, 1e-12)});
  }
  return gaps;
}

function sessionLevels(primary, asOfMs, side) {
  const available = primary.filter(bar => bar.closeTime < asOfMs);
  if (!available.length) return [];
  const asOf = new Date(asOfMs), today = asOf.toISOString().slice(0, 10), levels = [];
  const dates = [...new Set(available.map(bar => new Date(bar.openTime).toISOString().slice(0, 10)).filter(day => day < today))].sort();
  if (dates.length) {
    const prior = available.filter(bar => new Date(bar.openTime).toISOString().slice(0, 10) === dates.at(-1));
    const value = side === 'LONG' ? Math.min(...prior.map(bar => bar.low)) : Math.max(...prior.map(bar => bar.high));
    levels.push([side === 'LONG' ? 'PREVIOUS DAY LOW' : 'PREVIOUS DAY HIGH', value]);
  }
  if (asOf.getUTCHours() >= 4) {
    const asia = available.filter(bar => {
      const date = new Date(bar.openTime);
      return date.toISOString().slice(0, 10) === today && date.getUTCHours() < 4;
    });
    if (asia.length) {
      const value = side === 'LONG' ? Math.min(...asia.map(bar => bar.low)) : Math.max(...asia.map(bar => bar.high));
      levels.push([side === 'LONG' ? 'ASIA LOW' : 'ASIA HIGH', value]);
    }
  }
  return levels;
}

function liquiditySweeps(primary, trend, side, atrValue) {
  const primarySwings = confirmedSwings(primary, CFG.swingLeftBars, CFG.swingRightBars);
  const trendSwings = confirmedSwings(trend, CFG.swingLeftBars, CFG.swingRightBars);
  const key = side === 'LONG' ? 'lows' : 'highs', found = [];
  const start = Math.max(1, primary.length - CFG.liquiditySweepLookback), buffer = atrValue * CFG.sweepBufferAtr;
  for (let index = start; index < primary.length; index++) {
    const bar = primary[index], levels = [];
    const primaryKnown = primarySwings[key].filter(pivot => pivot.confirmationIndex < index);
    if (primaryKnown.length) levels.push([`5M SWING ${side === 'LONG' ? 'LOW' : 'HIGH'}`, primaryKnown.at(-1).price]);
    const trendKnown = trendSwings[key].filter(pivot => trend[pivot.confirmationIndex].closeTime < bar.openTime);
    if (trendKnown.length) levels.push([`15M SWING ${side === 'LONG' ? 'LOW' : 'HIGH'}`, trendKnown.at(-1).price]);
    levels.push(...sessionLevels(primary, bar.openTime, side));
    const swept = levels.filter(([, level]) => side === 'LONG' ?
      bar.low <= level - buffer && bar.close > level && bar.open >= level - buffer :
      bar.high >= level + buffer && bar.close < level && bar.open <= level + buffer
    );
    if (swept.length) {
      swept.sort((a, b) => Math.abs(bar.close - a[1]) - Math.abs(bar.close - b[1]));
      found.push({index, name: swept[0][0], level: swept[0][1], extreme: side === 'LONG' ? bar.low : bar.high});
    }
  }
  return found;
}

function dealingRange(trend, asOfMs) {
  const eligible = trend.filter(bar => bar.closeTime < asOfMs);
  if (eligible.length < 8) return null;
  const swings = confirmedSwings(eligible, CFG.swingLeftBars, CFG.swingRightBars);
  let high, low;
  if (swings.highs.length && swings.lows.length) {
    high = swings.highs.at(-1).price; low = swings.lows.at(-1).price;
  } else {
    const window = eligible.slice(-20);
    high = Math.max(...window.map(bar => bar.high)); low = Math.min(...window.map(bar => bar.low));
  }
  return high > low ? {low, high, mid: (low + high) / 2} : null;
}

function marketStructureShift(primary, side, sweepIndex, atrValue) {
  const swings = confirmedSwings(primary, CFG.swingLeftBars, CFG.swingRightBars);
  const key = side === 'LONG' ? 'highs' : 'lows';
  const known = swings[key].filter(pivot => pivot.confirmationIndex < sweepIndex);
  const context = primary.slice(Math.max(0, sweepIndex - 12), sweepIndex);
  if (!known.length && !context.length) return null;
  const level = known.length ? known.at(-1).price : side === 'LONG' ?
    Math.max(...context.map(bar => bar.high)) : Math.min(...context.map(bar => bar.low));
  const buffer = atrValue * CFG.mssBufferAtr;
  for (let index = sweepIndex + 1; index < primary.length; index++) {
    const bar = primary[index], bodies = primary.slice(Math.max(0, index - 20), index).map(item => Math.abs(item.close - item.open));
    const bodyRatio = Math.abs(bar.close - bar.open) / Math.max(median(bodies), 1e-12);
    const location = (bar.close - bar.low) / Math.max(bar.high - bar.low, 1e-12);
    const breakOk = side === 'LONG' ? bar.close >= level + buffer : bar.close <= level - buffer;
    const displacement = bodyRatio >= CFG.displacementBodyRatio && (side === 'LONG' ? location >= 0.70 : location <= 0.30);
    if (breakOk && displacement) return {index, level, bodyRatio};
  }
  return null;
}

function derivativesContext(snapshot, side, priceChange15mPct) {
  const oi15m = snapshot.oiChange15mPct, oi1h = snapshot.oiChange1hPct;
  const taker = snapshot.takerBuySellRatio15m, accounts = snapshot.longShortAccountRatio;
  const funding = Number(snapshot.fundingBp || 0);
  const available = [oi15m, taker, accounts].every(value => value != null);
  const supports = [], conflicts = [];
  if (side === 'LONG') {
    if (taker != null && taker >= 1.08) supports.push(`15m taker buying ${taker.toFixed(2)}× sell volume`);
    else if (taker != null && taker <= 0.92) conflicts.push(`15m taker selling dominates at ${taker.toFixed(2)}×`);
    if (oi15m != null && oi15m >= 0.25 && priceChange15mPct >= 0.10) supports.push(`OI +${oi15m.toFixed(2)}% expands with rising 15m price`);
    else if (oi15m != null && oi15m >= 0.25 && priceChange15mPct <= -0.10) conflicts.push(`OI +${oi15m.toFixed(2)}% expands while 15m price falls`);
    if (accounts != null && accounts <= 0.67 && funding <= -3) supports.push('short crowding leaves upside squeeze fuel');
    else if (accounts != null && accounts >= 1.50 && funding >= 3) conflicts.push('long accounts and positive funding are crowded');
  } else {
    if (taker != null && taker <= 0.92) supports.push(`15m taker selling dominates at ${taker.toFixed(2)}×`);
    else if (taker != null && taker >= 1.08) conflicts.push(`15m taker buying ${taker.toFixed(2)}× sell volume`);
    if (oi15m != null && oi15m >= 0.25 && priceChange15mPct <= -0.10) supports.push(`OI +${oi15m.toFixed(2)}% expands with falling 15m price`);
    else if (oi15m != null && oi15m >= 0.25 && priceChange15mPct >= 0.10) conflicts.push(`OI +${oi15m.toFixed(2)}% expands while 15m price rises`);
    if (accounts != null && accounts >= 1.50 && funding >= 3) supports.push('long crowding leaves downside liquidation risk');
    else if (accounts != null && accounts <= 0.67 && funding <= -3) conflicts.push('short accounts and negative funding are crowded');
  }
  let leverageRisk = 'UNKNOWN';
  if (accounts != null && accounts >= 1.50 && funding >= 3) leverageRisk = 'LONGS_CROWDED';
  else if (accounts != null && accounts <= 0.67 && funding <= -3) leverageRisk = 'SHORTS_CROWDED';
  else if (oi15m != null && oi15m <= -0.50) leverageRisk = 'DELEVERAGING';
  else if (oi15m != null && oi15m >= 0.50) leverageRisk = 'LEVERAGE_BUILDING';
  else if (available) leverageRisk = 'BALANCED';
  let verdict, reasons;
  if (!available) { verdict = 'UNAVAILABLE'; reasons = ['one or more public derivatives feeds are unavailable']; }
  else if (conflicts.length >= 2) { verdict = 'CONFLICTS'; reasons = [...conflicts, ...supports]; }
  else if (supports.length >= 2 && !conflicts.length) { verdict = 'SUPPORTS'; reasons = supports; }
  else { verdict = 'NEUTRAL'; reasons = [...conflicts, ...supports]; if (!reasons.length) reasons.push('positioning is mixed; no two-factor edge'); }
  return {verdict, available, leverageRisk, reasons, oi15m, oi1h, taker, accounts};
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
  const [primaryRows, trendRows, higherRows, book, premium, oiRows, takerRows, accountRows] = await Promise.all([
    jget(`/klines?symbol=${symbol}&interval=5m&limit=221`),
    jget(`/klines?symbol=${symbol}&interval=15m&limit=221`),
    jget(`/klines?symbol=${symbol}&interval=1h&limit=221`),
    jget(`/ticker/bookTicker?symbol=${symbol}`), jget(`/premiumIndex?symbol=${symbol}`),
    optionalJson(`/futures/data/openInterestHist?symbol=${symbol}&period=5m&limit=13`),
    optionalJson(`/futures/data/takerlongshortRatio?symbol=${symbol}&period=5m&limit=3`),
    optionalJson(`/futures/data/globalLongShortAccountRatio?symbol=${symbol}&period=5m&limit=1`)
  ]);
  const now = Date.now();
  const oi = Array.isArray(oiRows) ? oiRows : [];
  const oiValues = oi.map(row => Number(row.sumOpenInterest || 0));
  const pctChange = (latest, earlier) => latest > 0 && earlier > 0 ? (latest / earlier - 1) * 100 : null;
  const taker = Array.isArray(takerRows) ? takerRows : [];
  const buyVolume = taker.reduce((sum, row) => sum + Number(row.buyVol || 0), 0);
  const sellVolume = taker.reduce((sum, row) => sum + Number(row.sellVol || 0), 0);
  const accounts = Array.isArray(accountRows) ? accountRows : [];
  return {
    ...context,
    primary: primaryRows.map(candle).filter(bar => bar.closeTime < now),
    trend: trendRows.map(candle).filter(bar => bar.closeTime < now),
    higher: higherRows.map(candle).filter(bar => bar.closeTime < now),
    bid: Number(book.bidPrice || 0), ask: Number(book.askPrice || 0),
    mark: Number(premium.markPrice || 0), fundingBp: Number(premium.lastFundingRate || 0) * 10000,
    openInterestUsd: oi.length ? Number(oi.at(-1).sumOpenInterestValue || 0) : null,
    oiChange15mPct: oiValues.length >= 4 ? pctChange(oiValues.at(-1), oiValues.at(-4)) : null,
    oiChange1hPct: oiValues.length >= 13 ? pctChange(oiValues.at(-1), oiValues.at(-13)) : null,
    takerBuySellRatio15m: sellVolume > 0 ? buyVolume / sellVolume : null,
    longShortAccountRatio: accounts.length ? Number(accounts.at(-1).longShortRatio || 0) : null
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
    friction_stop_pct: signal.friction_stop_pct, score: signal.score, confidence: signal.confidence,
    playbook: signal.playbook, playbook_confirmation: signal.playbook_confirmation,
    target_basis: signal.playbook === 'LIQUIDITY_MSS_FVG' ?
      'FVG retest entry; TP1/TP2/TP3 are minimum net 1R/2R/3R' :
      'Breakout entry; TP1/TP2/TP3 are net 1R/2R/3R'
  };
}

function evaluateMomentum(snapshot, equity, now = new Date()) {
  const {primary, trend, higher} = snapshot;
  if (primary.length < 55 || trend.length < 55 || higher.length < 201) return {
    symbol: snapshot.symbol, state: 'INSUFFICIENT', side: null, score: 0,
    action: 'WAIT — indicator warm-up', price_change_pct_24h: snapshot.change,
    quote_volume_24h: snapshot.quoteVolume, blocked_by: ['Need 55×5m, 55×15m, and 201×1h closed candles']
  };
  const closes = primary.map(bar => bar.close), trendCloses = trend.map(bar => bar.close);
  const higherCloses = higher.map(bar => bar.close), current = primary.at(-1);
  const priceChange15mPct = primary.length >= 4 && primary.at(-4).close > 0 ?
    (current.close / primary.at(-4).close - 1) * 100 : 0;
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
    const d = definitions[side], derivatives = derivativesContext(snapshot, side, priceChange15mPct);
    const plannedEntry = d.breakout ? mark : d.trigger;
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
    const derivativesClear = derivatives.verdict !== 'CONFLICTS';
    if (!derivativesClear) blocks.push('derivatives context conflicts on at least two independent factors');
    const live = score >= CFG.signalScore && ['higher', 'trend', 'alignment', 'macd', 'momentum', 'adx', 'vwap'].every(key => d[key]) &&
      volumeOk && controlledBreakout && execution && derivativesClear && zone !== 'OFF_HOURS';
    const armed = score >= CFG.armedScore && coreBias && nearTrigger && execution && derivativesClear;
    return {...d, side, score: Math.min(100, score), entry: plannedEntry, stop, stopBp, frictionStopPct, reasons, blocks, live, armed, derivatives};
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
    playbook: 'MOMENTUM_BREAKOUT', playbook_label: 'PLAYBOOK A · MOMENTUM',
    playbook_confirmation: 'SINGLE',
    signal_id: `${snapshot.symbol}:${current.closeTime}:MOMENTUM_BREAKOUT:${choice.side}:${state}`,
    candle_close_time: current.closeTime, entry: choice.entry, entry_trigger: choice.trigger, stop: choice.stop,
    spread_bp: Number.isFinite(spreadBp) ? spreadBp : null, friction_bp: frictionBp,
    friction_stop_pct: choice.frictionStopPct, stop_distance_bp: choice.stopBp,
    atr: currentAtr, atr_pct: atrPct, rsi_5m: currentRsi, stoch_rsi_5m: currentStoch,
    macd_hist_5m: macd.hist, adx_5m: currentAdx, vwap, bollinger_position: bbPosition,
    volume_ratio: volumeRatio, breakout_level: choice.level,
    higher_trend: choice.higher ? choice.trendName : 'MIXED', trend_15m: choice.trend ? choice.trendName : 'MIXED',
    kill_zone: zone, funding_bp: snapshot.fundingBp, price_change_pct_24h: snapshot.change,
    derivatives_verdict: choice.derivatives.verdict, derivatives_available: choice.derivatives.available,
    open_interest_usd: snapshot.openInterestUsd, open_interest_change_15m_pct: snapshot.oiChange15mPct,
    open_interest_change_1h_pct: snapshot.oiChange1hPct,
    taker_buy_sell_ratio_15m: snapshot.takerBuySellRatio15m,
    long_short_account_ratio: snapshot.longShortAccountRatio,
    leverage_risk: choice.derivatives.leverageRisk, derivatives_reasons: choice.derivatives.reasons,
    quote_volume_24h: snapshot.quoteVolume, expires_at: expires.toISOString(), invalidation,
    reasons: choice.reasons, blocked_by: choice.blocks, ticket: null
  };
  if (['LIVE', 'ARMED'].includes(state)) signal.ticket = makeTicket(signal, equity, now);
  return signal;
}

function evaluateLiquidity(snapshot, equity, now = new Date()) {
  const {primary, trend, higher} = snapshot;
  if (primary.length < 55 || trend.length < 55 || higher.length < 201) return {
    symbol: snapshot.symbol, state: 'INSUFFICIENT', side: null, score: 0,
    action: 'WAIT — liquidity playbook warm-up', playbook: 'LIQUIDITY_MSS_FVG',
    playbook_label: 'PLAYBOOK B · LIQUIDITY MSS/FVG', playbook_confirmation: 'SINGLE',
    price_change_pct_24h: snapshot.change, quote_volume_24h: snapshot.quoteVolume,
    blocked_by: ['Need 55×5m, 55×15m, and 201×1h closed candles']
  };
  const current = primary.at(-1), closes = primary.map(bar => bar.close);
  const trendCloses = trend.map(bar => bar.close), higherCloses = higher.map(bar => bar.close);
  const currentAtr = atrLast(primary), currentRsi = rsiLast(closes), currentStoch = stochRsiLast(closes);
  const currentAdx = adxLast(primary), macd = macdLast(closes);
  const higher50 = emaLast(higherCloses, 50), higher200 = emaLast(higherCloses, 200);
  const trend20 = emaLast(trendCloses, 20), trend50 = emaLast(trendCloses, 50);
  const vwap = sessionVwap(primary), bb = closes.slice(-20);
  const bbMid = bb.reduce((sum, value) => sum + value, 0) / bb.length;
  const bbStd = Math.sqrt(bb.reduce((sum, value) => sum + (value - bbMid) ** 2, 0) / bb.length);
  const bbPosition = (current.close - bbMid) / Math.max(2 * bbStd, 1e-12);
  const volumeBase = median(primary.slice(-CFG.volumeLookback - 1, -1).map(bar => bar.quoteVolume));
  const volumeRatio = volumeBase > 0 ? current.quoteVolume / volumeBase : 0;
  const midpoint = snapshot.bid > 0 && snapshot.ask > snapshot.bid ? (snapshot.bid + snapshot.ask) / 2 : current.close;
  const spreadBp = snapshot.bid > 0 && snapshot.ask > snapshot.bid ? (snapshot.ask - snapshot.bid) / midpoint * 10000 : Infinity;
  const mark = snapshot.mark > 0 ? snapshot.mark : current.close, atrPct = currentAtr / current.close * 100;
  const frictionBp = 2 * (CFG.feePerSideBp + CFG.slippagePerSideBp) + (Number.isFinite(spreadBp) ? spreadBp : CFG.maxSpreadBp * 10);
  const driftAtr = Math.abs(mark - current.close) / Math.max(currentAtr, 1e-12), zone = killZone(now);
  const priceChange15mPct = primary.at(-4).close > 0 ? (current.close / primary.at(-4).close - 1) * 100 : 0;

  function candidate(side) {
    const direction = side === 'LONG' ? 1 : -1;
    const htfBias = side === 'LONG' ? higherCloses.at(-1) > higher50 && higher50 > higher200 :
      higherCloses.at(-1) < higher50 && higher50 < higher200;
    const trendBias = side === 'LONG' ? trendCloses.at(-1) > trend20 && trend20 > trend50 :
      trendCloses.at(-1) < trend20 && trend20 < trend50;
    const derivatives = derivativesContext(snapshot, side, priceChange15mPct);
    const sweeps = liquiditySweeps(primary, trend, side, currentAtr);
    let selected = sweeps.length ? sweeps.at(-1) : null, selectedMss = null, selectedFvg = null;
    for (let offset = sweeps.length - 1; offset >= 0; offset--) {
      const sweep = sweeps[offset], mss = marketStructureShift(primary, side, sweep.index, currentAtr);
      if (!mss) continue;
      const gaps = findFairValueGaps(primary, side, mss.index, currentAtr, CFG.fvgMinAtr);
      if (gaps.length) {
        selected = sweep; selectedMss = mss;
        const currentRetests = gaps.filter(gap => gap.retestIndex === primary.length - 1);
        selectedFvg = (currentRetests.length ? currentRetests : gaps).at(-1);
        break;
      }
      if (selected === sweep) selectedMss = mss;
    }
    const sweepOk = Boolean(selected), mssOk = Boolean(selectedMss), fvgOk = Boolean(selectedFvg && selectedMss);
    const entry = selectedFvg ? selectedFvg.mid : current.close;
    const range = dealingRange(trend, selected ? primary[selected.index].openTime : current.openTime);
    const locationOk = range ? (side === 'LONG' ? entry <= range.mid : entry >= range.mid) : false;
    const premiumDiscount = !range ? 'UNKNOWN' : entry < range.mid ? 'DISCOUNT' : entry > range.mid ? 'PREMIUM' : 'EQUILIBRIUM';
    const sweepExtreme = selected ? selected.extreme : current.close - direction * currentAtr;
    const stop = side === 'LONG' ?
      Math.min(sweepExtreme - 0.1 * currentAtr, entry - CFG.liquidityStopMinAtr * currentAtr) :
      Math.max(sweepExtreme + 0.1 * currentAtr, entry + CFG.liquidityStopMinAtr * currentAtr);
    const stopDistance = Math.abs(entry - stop), stopAtr = stopDistance / Math.max(currentAtr, 1e-12);
    const stopBp = stopDistance / Math.max(entry, 1e-12) * 10000;
    const frictionStopPct = frictionBp / Math.max(stopBp, 1e-12) * 100;
    const execution = spreadBp <= CFG.maxSpreadBp && atrPct >= CFG.minAtrPct && atrPct <= CFG.maxAtrPct &&
      driftAtr <= CFG.maxEntryDriftAtr && stopAtr <= CFG.maxStopAtr && frictionStopPct <= CFG.maxFrictionStopPct &&
      2 * stopBp >= CFG.minRewardCostMultiple * frictionBp;
    const sequenceOk = mssOk && fvgOk;
    const score = (htfBias ? 20 : 0) + (locationOk ? 15 : 0) + (sweepOk ? 20 : 0) +
      (mssOk ? 20 : 0) + (sequenceOk ? 15 : 0) + (execution ? 10 : 0);
    const derivativesClear = derivatives.verdict !== 'CONFLICTS';
    const retestIndex = selectedFvg ? selectedFvg.retestIndex : null;
    const currentRetest = retestIndex === primary.length - 1;
    const pendingRetest = selectedFvg && retestIndex == null && Math.abs(mark - entry) <= CFG.fvgEntryMaxDistanceAtr * currentAtr;
    const critical = htfBias && locationOk && sweepOk && mssOk && fvgOk && execution;
    const live = score >= CFG.liquiditySignalScore && critical && currentRetest && derivativesClear && zone !== 'OFF_HOURS';
    const armed = score >= CFG.liquidityArmedScore && critical && pendingRetest && derivativesClear;
    const reasons = [], blocks = [];
    (htfBias ? reasons : blocks).push(`1h EMA50/200 structure ${htfBias ? 'aligned' : 'not aligned'}`);
    if (!range) blocks.push('15m dealing range unavailable');
    else if (locationOk) reasons.push(`entry in ${premiumDiscount.toLowerCase()} half of 15m dealing range`);
    else blocks.push(`entry is in ${premiumDiscount.toLowerCase()}, wrong side of range`);
    if (selected) reasons.push(`${selected.name.toLowerCase()} swept and reclaimed on a closed 5m candle`);
    else blocks.push('no closed 5m liquidity sweep in the last eight bars');
    if (selectedMss) reasons.push(`MSS closed through ${price(selectedMss.level)} with ${selectedMss.bodyRatio.toFixed(2)}× displacement`);
    else if (selected) blocks.push('post-sweep market structure shift with displacement is pending');
    if (selectedFvg) reasons.push(`unfilled FVG ${price(selectedFvg.low)}–${price(selectedFvg.high)}`);
    else if (selectedMss) blocks.push(`FVG at least ${CFG.fvgMinAtr.toFixed(2)} ATR is pending`);
    if (execution) reasons.push(`friction ${frictionStopPct.toFixed(1)}% of structural stop`);
    else {
      if (spreadBp > CFG.maxSpreadBp) blocks.push(`spread ${spreadBp.toFixed(1)}bp above ${CFG.maxSpreadBp}bp`);
      if (atrPct < CFG.minAtrPct || atrPct > CFG.maxAtrPct) blocks.push(`ATR ${atrPct.toFixed(2)}% outside volatility band`);
      if (driftAtr > CFG.maxEntryDriftAtr) blocks.push(`mark drift ${driftAtr.toFixed(2)} ATR from signal close`);
      if (stopAtr > CFG.maxStopAtr) blocks.push(`sweep stop ${stopAtr.toFixed(2)} ATR is too wide`);
      if (frictionStopPct > CFG.maxFrictionStopPct) blocks.push(`friction consumes ${frictionStopPct.toFixed(1)}% of stop`);
    }
    if (selectedFvg && retestIndex != null && !currentRetest) blocks.push('FVG midpoint traded on an earlier candle; setup is consumed');
    else if (selectedFvg && !currentRetest && !pendingRetest) blocks.push(`FVG midpoint is more than ${CFG.fvgEntryMaxDistanceAtr.toFixed(2)} ATR from mark`);
    if (!derivativesClear) blocks.push('derivatives context conflicts on at least two independent factors');
    if (zone === 'OFF_HOURS') blocks.push('outside Asia/London/New York kill zone; LIVE disabled');
    return {side, score: Math.min(100, score), entry, stop, stopBp, frictionStopPct, higher: htfBias,
      trend: trendBias, selected, mss: selectedMss, fvg: selectedFvg, rangeMid: range?.mid ?? null,
      premiumDiscount, reasons, blocks, live, armed, derivatives};
  }

  const choice = [candidate('LONG'), candidate('SHORT')].sort((a, b) =>
    (a.live ? 0 : a.armed ? 1 : 2) - (b.live ? 0 : b.armed ? 1 : 2) || b.score - a.score
  )[0];
  const state = choice.live ? 'LIVE' : choice.armed ? 'ARMED' : choice.score >= CFG.watchScore ? 'WATCH' : 'STAND_DOWN';
  const expires = new Date(now.getTime() + CFG.entryExpiryMinutes * 60000);
  const confidence = choice.score >= 90 ? 'A' : choice.score >= 80 ? 'A−' : choice.score >= 70 ? 'B' : 'C';
  const action = state === 'LIVE' ? `ENTER ${choice.side} NOW — closed 5m FVG midpoint retest confirmed` :
    state === 'ARMED' ? `${choice.side} LIMIT ${price(choice.entry)} — 50% FVG retest; cancel by ${expires.toISOString().slice(11, 16)} UTC` :
    state === 'WATCH' ? `WAIT — ${choice.side.toLowerCase()} liquidity sequence incomplete or consumed` :
    'STAND DOWN — no valid liquidity sweep/MSS/FVG sequence';
  const selected = choice.selected, mss = choice.mss, fvg = choice.fvg;
  const invalidationLevel = selected ? selected.extreme : choice.stop;
  const invalidation = `Cancel before entry if 5m closes ${choice.side === 'LONG' ? 'below' : 'above'} ${price(invalidationLevel)}, the FVG fully fills, spread exceeds ${CFG.maxSpreadBp}bp, or the ticket expires. After entry, the hard stop is final.`;
  const signal = {
    symbol: snapshot.symbol, state, side: choice.side, score: choice.score, action, confidence,
    playbook: 'LIQUIDITY_MSS_FVG', playbook_label: 'PLAYBOOK B · LIQUIDITY MSS/FVG', playbook_confirmation: 'SINGLE',
    signal_id: `${snapshot.symbol}:${current.closeTime}:LIQUIDITY_MSS_FVG:${choice.side}:${state}`,
    candle_close_time: current.closeTime, entry: choice.entry, entry_trigger: choice.entry, stop: choice.stop,
    spread_bp: Number.isFinite(spreadBp) ? spreadBp : null, friction_bp: frictionBp,
    friction_stop_pct: choice.frictionStopPct, stop_distance_bp: choice.stopBp,
    atr: currentAtr, atr_pct: atrPct, rsi_5m: currentRsi, stoch_rsi_5m: currentStoch,
    macd_hist_5m: macd.hist, adx_5m: currentAdx, vwap, bollinger_position: bbPosition,
    volume_ratio: volumeRatio, breakout_level: mss?.level ?? null,
    liquidity_level_name: selected?.name ?? null, liquidity_level: selected?.level ?? null,
    sweep_price: selected?.extreme ?? null, mss_level: mss?.level ?? null,
    displacement_ratio: mss?.bodyRatio ?? null, fvg_low: fvg?.low ?? null,
    fvg_high: fvg?.high ?? null, fvg_mid: fvg?.mid ?? null,
    dealing_range_mid: choice.rangeMid, premium_discount: choice.premiumDiscount,
    higher_trend: choice.higher ? (choice.side === 'LONG' ? 'BULLISH' : 'BEARISH') : 'MIXED',
    trend_15m: choice.trend ? (choice.side === 'LONG' ? 'BULLISH' : 'BEARISH') : 'MIXED',
    kill_zone: zone, funding_bp: snapshot.fundingBp, price_change_pct_24h: snapshot.change,
    derivatives_verdict: choice.derivatives.verdict, derivatives_available: choice.derivatives.available,
    open_interest_usd: snapshot.openInterestUsd, open_interest_change_15m_pct: snapshot.oiChange15mPct,
    open_interest_change_1h_pct: snapshot.oiChange1hPct,
    taker_buy_sell_ratio_15m: snapshot.takerBuySellRatio15m,
    long_short_account_ratio: snapshot.longShortAccountRatio,
    leverage_risk: choice.derivatives.leverageRisk, derivatives_reasons: choice.derivatives.reasons,
    quote_volume_24h: snapshot.quoteVolume, expires_at: expires.toISOString(), invalidation,
    reasons: choice.reasons, blocked_by: choice.blocks, ticket: null
  };
  if (['LIVE', 'ARMED'].includes(state)) signal.ticket = makeTicket(signal, equity, now);
  return signal;
}

function evaluate(snapshot, equity, now = new Date()) {
  const momentum = evaluateMomentum(snapshot, equity, now), liquidity = evaluateLiquidity(snapshot, equity, now);
  const rank = {LIVE: 0, ARMED: 1, WATCH: 2, STAND_DOWN: 3, INSUFFICIENT: 4};
  const actionable = [momentum, liquidity].filter(item => ['LIVE', 'ARMED'].includes(item.state));
  if (actionable.length === 2 && actionable[0].side !== actionable[1].side) {
    const base = [...actionable].sort((a, b) => rank[a.state] - rank[b.state] || b.score - a.score)[0];
    return {...base, state: 'WATCH', action: 'WAIT — Playbook A and Playbook B disagree on direction',
      playbook_confirmation: 'CONFLICT', signal_id: `${snapshot.symbol}:${base.candle_close_time}:PLAYBOOK_CONFLICT:WATCH`,
      blocked_by: [...(base.blocked_by || []), 'opposite actionable playbooks; arbiter vetoed the ticket'], ticket: null};
  }
  let chosen = [momentum, liquidity].sort((a, b) => rank[a.state] - rank[b.state] || b.score - a.score ||
    (a.playbook === 'LIQUIDITY_MSS_FVG' ? -1 : 1))[0];
  if (actionable.length === 2 && actionable[0].side === actionable[1].side) {
    chosen = {...chosen, playbook_confirmation: 'DUAL_CONFIRMATION',
      reasons: [...(chosen.reasons || []), 'independent playbooks confirm the same direction']};
    chosen.ticket = makeTicket(chosen, equity, now);
  }
  return chosen;
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
  // The desk makes one decision per sweep: only the strongest ranked LIVE
  // signal may enter the paper ledger, even if several markets qualify.
  const signal = signals.find(item => item.state === 'LIVE' && item.ticket);
  if (signal && !paper.seen.includes(signal.signal_id) && !paper.positions[signal.symbol]) {
    const last = paper.history.find(item => item.symbol === signal.symbol);
    const coolingDown = last && (now - new Date(last.closed_at)) / 60000 < CFG.cooldownMinutes;
    if (!coolingDown && riskState(paper).can_open) {
      const position = {...signal.ticket, status: 'OPEN', opened_at: now.toISOString(), mark: signal.entry, unrealized_pnl_usd: -signal.ticket.estimated_round_trip_cost_usd};
      paper.positions[signal.symbol] = position; paper.seen = [...paper.seen, signal.signal_id].slice(-500);
      paper.tradesToday += 1; paper.lastAction = `OPEN ${signal.side} ${signal.symbol} (browser paper)`;
    }
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
    research_status: 'UNVALIDATED_DUAL_PLAYBOOK_AFTER_FAILED_V1', status: snapshots.length ? 'LIVE' : 'DEGRADED',
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
