import {makeChart, candleTime} from './chart.js';

const $ = id => document.getElementById(id);
const put = (id, value) => { $(id).textContent = value ?? '—'; };
const instruments = {XAUUSD:['XAU / USD','ทองคำ','Au'], EURUSD:['EUR / USD','ยูโร / ดอลลาร์สหรัฐ','€'], GBPUSD:['GBP / USD','ปอนด์ / ดอลลาร์สหรัฐ','£']};
const biasText = {BUY:'พิจารณาฝั่งซื้อ', SELL:'พิจารณาฝั่งขาย', WAIT:'รอจังหวะ'};
const shapes = {
  'Flat bar':'ราคาเปิดและปิดคงที่ ไม่มีช่วงแกว่ง', 'Doji':'โดจิ · ราคาเปิดใกล้ราคาปิด',
  'Bullish engulfing':'แท่งกลืนขาขึ้น', 'Bearish engulfing':'แท่งกลืนขาลง',
  'Bullish pin bar':'พินบาร์ขาขึ้น · ไส้ล่างยาว', 'Bearish pin bar':'พินบาร์ขาลง · ไส้บนยาว',
  'Inside bar':'แท่งในกรอบแท่งก่อน', 'Bullish breakout':'ปิดทะลุกรอบบน', 'Bearish breakout':'ปิดหลุดกรอบล่าง',
  'Up candle':'แท่งบวก', 'Down candle':'แท่งลบ'
};
let configuredSymbol = null;
let loggedIn = false, interval = '1h', mode = 'demo', current = null, dataEpoch = 0, riskEpoch = 0;
let aborter = null, polling = null, baseline = null, chartHistory = new Map();
const symbol = () => $('symbol').value;
const precision = () => symbol() === 'XAUUSD' ? 2 : 5;
const num = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString('en-US', {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
const price = value => num(value, precision());
const compact = (value, digits = 2) => Number.isFinite(value) ? value.toLocaleString('en-US', {maximumFractionDigits:digits}) : '—';
const tone = sig => sig === 'BUY' ? 'positive' : sig === 'SELL' ? 'negative' : 'waiting';
function el(tag, content, className) {
  const node = document.createElement(tag);
  node.textContent = content ?? '';
  if (className) node.className = className;
  return node;
}
function labelSignal(sig) { return Object.hasOwn(biasText, sig) ? sig : 'WAIT'; }
function note(message = '') {
  put('notice', message); $('notice').hidden = !message;
}
function storageGet(key) { try { return localStorage.getItem(key); } catch { return null; } }
function storeValue(key, value) { try { localStorage.setItem(key, value); return true; } catch { return false; } }
let alertsOn = storageGet('pad:alerts') === '1';

async function api(path, {body, signal, publicAccess = false} = {}) {
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', cache:'no-store',
    headers:body === undefined ? {} : {'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body), signal});
  let data;
  try { data = await response.json(); } catch { throw new Error('อ่านคำตอบไม่ได้ กรุณาลองใหม่อีกครั้ง'); }
  if (response.status === 401 && !publicAccess) {
    showLogin('เซสชันหมดอายุ กรุณาเข้าสู่ระบบอีกครั้ง');
    throw new Error('เซสชันหมดอายุ');
  }
  if (!response.ok) throw new Error(data.error || 'โหลดข้อมูลไม่สำเร็จ (' + response.status + ')');
  return data;
}
function clearRisk() {
  riskEpoch++; $('size-submit').disabled = false; $('size-result').hidden = true; put('size-error','');
}
function placeCalculator(connected) {
  const container = connected ? document.querySelector('.lower-grid') : $('independent-risk');
  if ($('money').parentElement !== container) container.append($('money'));
}
function clearSelection(resetFields = true) {
  dataEpoch++; aborter?.abort(); aborter = null;
  current = null; baseline = null; chartHistory.clear(); $('data-view').hidden = true;
  clearRisk(); placeCalculator(false);
  if (resetFields) {
    const f = $('size-form').elements;
    f.entry.value = ''; f.stop.value = '';
  }
}
function showLogin(message = '') {
  loggedIn = false; clearSelection(); clearInterval(polling); polling = null;
  $('startup').hidden = $('desk').hidden = $('logout').hidden = $('nav').hidden = true;
  $('login').hidden = false; $('password').value = ''; put('login-error', message);
  $('csv-file').value = ''; $('password').focus();
}
function configureSource() {
  $('import-form').hidden = mode !== 'import'; $('refresh').hidden = mode === 'import';
  for (const id of ['interval','mode']) {
    const selected = id === 'mode' ? mode : interval;
    $(id).querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(selected === b.dataset.value)));
  }
  const ticker = symbol(); const f = $('size-form').elements;
  $('size-symbol').value = instruments[ticker][0];
  if (configuredSymbol !== ticker) {
    f.contract_size.value = ticker === 'XAUUSD' ? '100' : '100000';
    configuredSymbol = ticker;
  }
  updateAlerts();
}
function showDesk() {
  loggedIn = true; $('login').hidden = $('startup').hidden = true;
  $('desk').hidden = $('nav').hidden = $('logout').hidden = false;
  configureSource(); refresh();
  clearInterval(polling);
  polling = setInterval(() => { if (loggedIn && mode === 'live' && !aborter) refresh(true); }, 60000);
}
$('login-form').addEventListener('submit', async e => {
  e.preventDefault(); $('login-submit').disabled = true; put('login-error','');
  try { await api('/api/login', {body:{password:$('password').value}, publicAccess:true}); $('password').value = ''; showDesk(); }
  catch (err) { put('login-error', err.message); }
  finally { $('login-submit').disabled = false; }
});
$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  try { await api('/api/logout', {body:{}}); showLogin(); } catch (err) { if (loggedIn) note(err.message); }
  finally { $('logout').disabled = false; }
});

const paintChart = makeChart($('chart'), $('chart-data'), $('chart-window'), (time, hovering) => {
  const row = chartHistory.get(time);
  if (!row) { put('inspect-label','รูปแบบแท่ง'); put('inspect-detail','—'); return; }
  put('inspect-label', (hovering ? 'แท่งที่เลือก' : 'แท่งล่าสุด') + ' · ' + (shapes[row.pattern] || row.pattern));
  const latest = current?.analysis?.time === time;
  const signal = latest ? current.analysis.signal : row.signal;
  const reason = hovering ? (latest ? current.analysis.reasons : row.reasons)?.at(-1) : '';
  put('inspect-detail', labelSignal(signal) + ' · RSI ' + num(row.rsi) + ' · MACD ' + num(row.macd, precision()) + (reason ? ' · ' + reason : ''));
});
function sourceText(m) {
  if (m.mode === 'demo') return 'ข้อมูลสาธิต';
  if (m.mode === 'import') return 'ย้อนหลังจากไฟล์';
  if (m.stale) return 'ข้อมูลตลาดล่าช้า';
  return m.session_closed ? 'ตลาดปิด' : 'ข้อมูลตลาดจริง';
}
function renderLevels(levels) {
  const key = $('level-key'); key.replaceChildren();
  for (const [name, title] of [['support','แนวรับล่าสุด'], ['resistance','แนวต้านล่าสุด']]) {
    const z = levels?.[name]?.[0];
    key.append(el('span', z ? title + ' ' + price(z.low) + '–' + price(z.high) + ' · สวิง ' + z.touches + ' ครั้ง' : title + ' · ยังไม่ยืนยัน', name));
  }
  if (levels?.at_price?.length) key.append(el('span','ราคาอยู่ในโซนสวิง','waiting'));
}
function renderSignal(a, m) {
  const signal = labelSignal(a.signal); put('signal', signal); $('signal').className = tone(signal);
  put('signal-meaning',biasText[signal]); put('pattern-now',(shapes[a.pattern] ?? a.pattern ?? '—') + (shapes[a.pattern] ? ' · ' + a.pattern : ''));
  $('reasons').replaceChildren(...(a.reasons || []).map(r => el('li',r)));
  $('plan').hidden = !a.plan || (m.mode === 'live' && (m.stale || m.session_closed));
  if (!$('plan').hidden) for (const field of ['entry','stop','target']) put('plan-' + field, price(a.plan[field]));
  put('last-candle','แท่งปิด ณ ' + candleTime(m.as_of) + ' น. กรุงเทพฯ');
  const idx = a.indicators || {};
  put('rsi',num(idx.rsi)); put('macd',num(idx.macd,precision())); put('macd-trigger','เส้นสัญญาณ ' + num(idx.signal,precision()));
  put('hist',num(idx.histogram,precision())); put('atr',num(idx.atr,precision()));
  $('hist').className = idx.histogram > 0 ? 'positive' : idx.histogram < 0 ? 'negative' : '';
  const log = $('pattern-log'); log.replaceChildren();
  for (const row of (a.history || []).slice(-8).reverse()) {
    const tr = el('tr'); tr.append(el('td',candleTime(row.time)), el('td',shapes[row.pattern] || row.pattern), el('td',labelSignal(row.signal),'tag ' + tone(row.signal)));
    log.append(tr);
  }
}
function renderTest(test, market) {
  put('test-source',sourceText(market));
  const count = market.candles.length;
  put('test-summary','ทดสอบ ' + count.toLocaleString('en-US') + ' แท่ง · ' + candleTime(market.candles[0].time) + ' – ' + candleTime(market.as_of) + ' (กรุงเทพฯ)');
  const metrics = [
    ['ชนะจากรายการที่ปิด', test.trades ? num(test.win_rate,1) + '%' : '—', 'ชนะ ' + test.wins + ' / แพ้ ' + test.losses + ' / เสมอ ' + (test.breakeven || 0)],
    ['ผลสะสมสุทธิ',num(test.net_r) + ' R','1R คือความเสี่ยงที่วางไว้ต่อครั้ง'],
    ['การลดลงสูงสุด',num(test.max_drawdown_r) + ' R','จากจุดสูงสุดของผลสะสม'],
    ['รายการที่ปิด',String(test.trades),'รอผล ' + test.unresolved + ' · ข้าม ' + test.skipped]
  ];
  $('test-metrics').replaceChildren(...metrics.map(([label,value,desc]) => {
    const box = el('div'); box.append(el('span',label),el('strong',value),el('p',desc)); return box;
  }));
  put('test-assumptions',(market.mode === 'import' ? 'แบบจำลองใช้ราคาในไฟล์เป็นราคากลาง หากเป็นกราฟ Bid ของ MT5 ผลอาจต่างจากการจับคู่จริง · ' : '') + 'จำลองเข้าที่ราคาเปิดแท่งถัดไป · สเปรดเต็ม ' + compact(test.spread_bps) + ' bps · ค่าธรรมเนียม ' + compact(test.fee_bps) + ' และสลิปเพจ ' + compact(test.slippage_bps) + ' bps ต่อข้าง (1 bps = 0.01%) · ผลย้อนหลังไม่รับประกันกำไรในอนาคต');
  const rows = (test.results || []).slice(-10).reverse(); $('test-empty').hidden = rows.length > 0;
  $('test-log').replaceChildren(...rows.map(row => {
    const tr = el('tr'); tr.append(el('td',candleTime(row.entry_time)),el('td',row.side,tone(row.side)),el('td',price(row.entry)),el('td',price(row.exit)),el('td',num(row.r),row.r > 0 ? 'positive' : row.r < 0 ? 'negative' : '')); return tr;
  }));
}
function render(data) {
  const {market:m,analysis:a,backtest:b} = data;
  if (!m?.candles?.length || !a || !b || m.symbol !== symbol() || m.interval !== interval || m.mode !== mode) throw new Error('ได้รับชุดข้อมูลไม่ตรงกับตัวเลือก กรุณาลองใหม่');
  current = data; placeCalculator(true); $('data-view').hidden = false;
  const latest = m.candles.at(-1), previous = m.candles.at(-2), [name,detail,icon] = instruments[m.symbol];
  put('pair-name',name); put('pair-detail',detail); put('coin-icon',icon); $('coin-icon').className = 'coin' + (m.symbol === 'XAUUSD' ? '' : ' fx');
  put('source-badge',sourceText(m)); $('source-badge').className = 'badge ' + (m.mode === 'live' && !m.stale ? 'live' : 'demo');
  put('latest-price',price(latest.close)); put('chart-interval',interval.toUpperCase());
  if (previous) {
    const d = (latest.close / previous.close - 1) * 100;
    put('bar-change',(d > 0 ? '+' : '') + num(d,2) + '% จากแท่งก่อน'); $('bar-change').className = 'change ' + (d > 0 ? 'positive' : d < 0 ? 'negative' : '');
  } else put('bar-change','');
  chartHistory = new Map((a.history || []).map(v => [v.time,v]));
  renderSignal(a,m); renderLevels(a.levels); paintChart(m.candles,a.levels,m.symbol,m.interval); renderTest(b,m);
  put('market-source','แหล่งที่มา ' + m.source + ' · เวลาเปิดแท่งแสดงตามกรุงเทพฯ' + (m.mode === 'live' ? ' · ราคาอ้างอิงอาจต่างจากราคาโบรกเกอร์' : ''));
  note(m.warning || (m.mode === 'demo' ? 'โหมดสาธิตใช้ราคาจำลอง สลับเป็นตลาดจริงหรือนำเข้าไฟล์เมื่อต้องการวิเคราะห์ราคาตลาด' : ''));
  processAlert(data);
}
async function refresh(background = false) {
  if (!loggedIn || mode === 'import') return;
  const epoch = ++dataEpoch; aborter?.abort();
  const controller = new AbortController(); aborter = controller; $('refresh').disabled = true;
  if (!background) note('กำลังอ่านแท่งราคา…');
  try {
    const params = new URLSearchParams({symbol:symbol(),interval,mode});
    const data = await api('/api/desk?' + params,{signal:controller.signal});
    if (epoch === dataEpoch && loggedIn) render(data);
  } catch (err) {
    if (epoch !== dataEpoch || err.name === 'AbortError' || !loggedIn) return;
    baseline = null; current = null; $('data-view').hidden = true; placeCalculator(false); note(err.message);
  } finally {
    if (epoch === dataEpoch) { aborter = null; $('refresh').disabled = false; }
  }
}
function changeSelection() {
  clearSelection(); configureSource();
  if (mode === 'import') note('เลือกไฟล์ให้ตรงกับสินทรัพย์และกรอบเวลาที่เลือก แล้วกดอ่านไฟล์ย้อนหลัง');
  else refresh();
}
$('symbol').addEventListener('change',changeSelection);
for (const id of ['mode','interval']) {
  $(id).addEventListener('click', e => {
    const button = e.target.closest('button[data-value]');
    if (!button || button.getAttribute('aria-pressed') === 'true') return;
    if (id === 'mode') mode = button.dataset.value; else interval = button.dataset.value;
    changeSelection();
  });
}
$('refresh').addEventListener('click', () => refresh());
$('import-form').addEventListener('submit',async e => {
  e.preventDefault(); if (mode !== 'import') return;
  const file = $('csv-file').files[0]; if (!file) return;
  clearSelection(); const epoch = dataEpoch;
  if (file.size > 1000000) { note('ไฟล์เกิน 1 MB กรุณาส่งออกช่วงที่สั้นลง'); return; }
  $('import-submit').disabled = true; note('กำลังอ่านไฟล์ ' + file.name + '…');
  const controller = new AbortController(); aborter = controller;
  try {
    const buffer = await file.arrayBuffer();
    const prefix = new Uint8Array(buffer,0,Math.min(2,buffer.byteLength));
    const encoding = prefix[0] === 0xff && prefix[1] === 0xfe ? 'utf-16le' : prefix[0] === 0xfe && prefix[1] === 0xff ? 'utf-16be' : 'utf-8';
    const csv = new TextDecoder(encoding).decode(buffer);
    if (epoch !== dataEpoch) return;
    const data = await api('/api/import',{body:{symbol:symbol(),interval,timezone:$('csv-zone').value.trim(),csv},signal:controller.signal});
    if (epoch === dataEpoch && loggedIn) render(data);
  } catch (err) { if (epoch === dataEpoch && loggedIn) note(err.message); }
  finally { if (epoch === dataEpoch) aborter = null; $('import-submit').disabled = false; }
});
for (const id of ['csv-file','csv-zone']) $(id).addEventListener('change', () => { if (mode === 'import') { clearSelection(); note('กดอ่านไฟล์ย้อนหลังเพื่อใช้ข้อมูลที่เลือก'); } });

$('size-form').addEventListener('input',clearRisk);
$('apply-plan').addEventListener('click', () => {
  if (!current?.analysis?.plan) return;
  const form = $('size-form').elements, plan = current.analysis.plan;
  form.entry.value = String(plan.entry); form.stop.value = String(plan.stop); form.reward_risk.value = String(plan.rr);
  clearRisk(); $('money').scrollIntoView({block:'start'}); form.equity.focus({preventScroll:true});
});
$('size-form').addEventListener('submit',async e => {
  e.preventDefault(); clearRisk(); const epoch = riskEpoch;
  const values = {symbol:symbol()};
  for (const [k,v] of new FormData(e.currentTarget)) values[k] = Number(v);
  if (values.entry === values.stop) { put('size-error','ราคาเข้าและจุดตัดขาดทุนต้องต่างกัน'); return; }
  if (['entry','stop','equity','risk_pct','reward_risk','contract_size','lot_step','min_lot'].some(k => !(values[k] > 0))) {
    put('size-error','กรอกทุน ความเสี่ยง ราคา และขนาดสัญญาให้มากกว่าศูนย์'); return;
  }
  $('size-submit').disabled = true;
  try {
    const r = await api('/api/size',{body:values});
    if (epoch !== riskEpoch || !loggedIn) return;
    const side = values.stop < values.entry ? 'BUY' : 'SELL';
    put('size-units',side + ' · ' + compact(r.lots,8) + ' lot');
    const rows = [
      ['หน่วยอ้างอิง', compact(r.units,8)],['วงเงินที่ยอมเสี่ยง',num(r.risk_budget) + ' USD'],
      ['ขาดทุนประมาณการเมื่อชน Stop',num(r.estimated_loss) + ' USD'],['มาร์จินประมาณการ',num(r.margin) + ' USD'],
      ['ราคาเป้าหมายหลังเผื่อต้นทุน',price(r.target)],['กำไรประมาณการเมื่อถึงเป้า',num(r.estimated_reward) + ' USD']
    ];
    if (!r.lots) rows.push(['ขนาดต่ำกว่า lot ขั้นต่ำ','ทุนหรือวงเงินเสี่ยงไม่พอ']);
    else if (r.limited_by_capital) rows.push(['ข้อจำกัดขนาด','จำกัดตามมาร์จินที่มี']);
    $('size-facts').replaceChildren(...rows.map(([term,val]) => {
      const div = el('div'); div.append(el('dt',term),el('dd',val)); return div;
    }));
    $('size-result').hidden = false;
  } catch (err) { if (epoch === riskEpoch && loggedIn) put('size-error',err.message); }
  finally { if (epoch === riskEpoch) $('size-submit').disabled = false; }
});

const allowedNotifications = () => window.isSecureContext && 'Notification' in window;
function updateAlerts(message = '') {
  const permitted = allowedNotifications() && Notification.permission === 'granted';
  const active = alertsOn && permitted;
  $('alerts').setAttribute('aria-pressed',String(active));
  put('alert-label',active ? 'ปิดแจ้งเตือน' : 'เปิดแจ้งเตือน');
  if (!message) {
    if (active) message = mode === 'live' ? 'ติดตามแท่งใหม่ขณะเปิดหน้านี้' : 'พร้อมทำงานเมื่อเลือกตลาดจริง';
    else if (!allowedNotifications()) message = 'เบราว์เซอร์นี้ใช้การแจ้งเตือนไม่ได้';
    else message = 'เฉพาะแท่งตลาดจริงขณะเปิดหน้านี้';
  }
  put('alert-status',message);
}
$('alerts').addEventListener('click',async () => {
  if (alertsOn && allowedNotifications() && Notification.permission === 'granted') {
    alertsOn = false; storeValue('pad:alerts','0'); updateAlerts(); return;
  }
  if (!allowedNotifications()) { updateAlerts('ต้องใช้เบราว์เซอร์ที่รองรับบน localhost หรือ HTTPS'); return; }
  try {
    const permission = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission;
    if (permission !== 'granted') { alertsOn = false; updateAlerts('เปิดสิทธิ์แจ้งเตือนของเว็บไซต์ในเบราว์เซอร์'); return; }
    if (!storeValue('pad:alerts','1')) { updateAlerts('เปิดพื้นที่จัดเก็บของไซต์เพื่อป้องกันแจ้งซ้ำ'); return; }
    alertsOn = true; baseline = current?.market?.mode === 'live' && !current.market.stale && !current.market.session_closed ? current.analysis.time : null;
    updateAlerts();
  } catch { updateAlerts('เบราว์เซอร์นี้เปิดการแจ้งเตือนไม่สำเร็จ'); }
});
function processAlert({market:m,analysis:a}) {
  if (mode !== 'live' || m.stale || m.session_closed || !Number.isFinite(a.time)) { baseline = null; return; }
  const prior = baseline;
  if (prior !== null && a.time < prior) { baseline = null; return; }
  baseline = a.time;
  const frameSeconds = {'15m':900,'1h':3600,'4h':14400,'1d':86400}[interval];
  if (prior === null || a.time !== prior + frameSeconds || !alertsOn || !allowedNotifications() ||
      Notification.permission !== 'granted' || !['BUY','SELL'].includes(a.signal) || !a.plan) return;
  const key = 'pad:notified:' + symbol() + ':' + interval;
  const heading = a.signal + ' · ' + instruments[symbol()][0] + ' · ' + interval;
  const body = a.pattern + ' · จุดเข้า ' + price(a.plan.entry) + ' · Stop ' + price(a.plan.stop) + ' · ' + candleTime(m.as_of) + ' น.';
  const notify = () => {
    if (!loggedIn || current?.analysis !== a || !alertsOn || Number(storageGet(key)) >= a.time) return;
    if (!storeValue(key,String(a.time))) { updateAlerts('ไม่สามารถบันทึกสถานะแจ้งเตือนได้'); return; }
    try { new Notification(heading,{body,tag:key + ':' + a.time}); }
    catch { updateAlerts('อุปกรณ์นี้ไม่สามารถแสดงการแจ้งเตือนได้'); }
  };
  if (navigator.locks) navigator.locks.request(key,notify).catch(() => updateAlerts('ระบบแจ้งเตือนยังไม่พร้อม'));
  else notify();
}
window.addEventListener('storage',e => {
  if (e.key === 'pad:alerts') { alertsOn = e.newValue === '1'; updateAlerts(); }
});
async function start() {
  try {
    const session = await api('/api/session',{publicAccess:true});
    if (session.authenticated) showDesk(); else showLogin();
  } catch (err) { showLogin('เชื่อมต่อแอปไม่สำเร็จ · ' + err.message); }
}
start();
