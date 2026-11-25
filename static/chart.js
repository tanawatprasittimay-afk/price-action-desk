const dateFormatter = new Intl.DateTimeFormat('th-TH', {day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bangkok'});

export function candleTime(seconds) {
  return Number.isFinite(seconds) ? dateFormatter.format(new Date(seconds * 1000)) : '—';
}

export function makeChart(canvas, readout, windowLabel, inspect) {
  const ctx = canvas.getContext('2d');
  let prices = [], levels = {}, symbol = '', interval = '', active = null, geometry = null;
  const digits = () => symbol === 'XAUUSD' ? 2 : 5;
  const fmt = n => n.toLocaleString('en-US', {minimumFractionDigits: digits(), maximumFractionDigits: digits()});

  function draw() {
    if (!ctx) return;
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height || !prices.length) return;
    const density = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = Math.round(width * density);
    canvas.height = Math.round(height * density);
    ctx.setTransform(density, 0, 0, density, 0, 0);
    const left = 4, right = width - (width < 400 ? 56 : 72), top = 9, bottom = height - 24;
    const bars = prices.slice(-Math.max(12, Math.min(95, Math.floor((right - left) / 8))));
    const step = (right - left) / bars.length;
    geometry = {bars, left, right, step};
    const low = Math.min(...bars.map(b => b.low)), high = Math.max(...bars.map(b => b.high));
    const range = Math.max(high - low, Math.abs(high) * .0001, 1e-9);
    const nearest = [['support', '#67dcb4'], ['resistance', '#f0c777']].map(([name, color]) => ({zone: levels[name]?.[0], color}));
    const visibleLevels = nearest.filter(({zone}) => zone && zone.low >= low - range * .3 && zone.high <= high + range * .3);
    const min = Math.min(low, ...visibleLevels.map(v => v.zone.low)) - range * .07;
    const max = Math.max(high, ...visibleLevels.map(v => v.zone.high)) + range * .07;
    const y = price => top + (max - price) / (max - min) * (bottom - top);
    const x = i => left + step * (i + .5);

    function line(x1, y1, x2, y2, color, dash = []) {
      ctx.beginPath(); ctx.strokeStyle = color; ctx.setLineDash(dash);
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.font = '10px system-ui, sans-serif'; ctx.textBaseline = 'middle'; ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const value = min + (max - min) * i / 4, py = y(value);
      line(left, py, right, py, '#344139');
      ctx.fillStyle = '#adbaaf'; ctx.textAlign = 'left'; ctx.fillText(fmt(value), right + 5, py);
    }
    const spacing = Math.max(1, Math.floor(bars.length / (width < 400 ? 3 : 5)));
    for (let i = 0; i < bars.length; i += spacing) {
      const px = x(i);
      line(px, top, px, bottom, '#2b3830', [2, 4]);
      const dt = new Date(bars[i].time * 1000);
      const daily = interval === '1d' || interval === '4h';
      const opt = daily ? {day:'numeric',month:'short'} : {hour:'2-digit',minute:'2-digit'};
      ctx.textAlign = i === 0 ? 'left' : 'center'; ctx.fillStyle = '#a8b5aa';
      ctx.fillText(dt.toLocaleString('th-TH', {...opt, timeZone:'Asia/Bangkok'}), px, bottom + 14);
    }
    visibleLevels.forEach(({zone,color}) => {
      ctx.fillStyle = color + '14'; ctx.fillRect(left, y(zone.high), right - left, Math.max(2,y(zone.low) - y(zone.high)));
      line(left, y(zone.center), right, y(zone.center), color, [6,4]);
    });
    bars.forEach((bar, i) => {
      const px = x(i), color = bar.close >= bar.open ? '#67dcb4' : '#ed948a';
      line(px, y(bar.high), px, y(bar.low), color);
      ctx.fillStyle = color;
      const bodyWidth = Math.max(2, Math.min(8, step * .64));
      ctx.fillRect(px - bodyWidth / 2, y(Math.max(bar.open, bar.close)), bodyWidth, Math.max(1.3, Math.abs(y(bar.close) - y(bar.open))));
    });
    if (active !== null) active = Math.max(0, Math.min(bars.length - 1, active));
    const bar = bars[active ?? (bars.length - 1)];
    if (active !== null) {
      line(x(active), top, x(active), bottom, '#e5eedf', [3,3]);
      line(left, y(bar.close), right, y(bar.close), '#b6c7b4', [3,4]);
      ctx.fillStyle = '#e5eedf'; ctx.beginPath(); ctx.arc(x(active), y(bar.close), 3, 0, Math.PI * 2); ctx.fill();
    }
    readout.textContent = (active === null ? '' : candleTime(bar.time) + ' · ') + ['O','H','L','C'].map((k,i) => k + ' ' + fmt(bar[['open','high','low','close'][i]])).join('   ');
    canvas.setAttribute('aria-label', 'กราฟ ' + symbol + ' ' + interval + ' ' + candleTime(bar.time) + ' กรุงเทพฯ ' + readout.textContent + ' ใช้ปุ่มลูกศรเพื่ออ่านแท่งถัดไป');
    inspect(bar.time, active !== null);
    windowLabel.textContent = bars.length + ' แท่ง · เวลากรุงเทพฯ';
  }
  canvas.addEventListener('pointermove', e => {
    if (!geometry) return;
    const xx = e.clientX - canvas.getBoundingClientRect().left;
    active = Math.floor((xx - geometry.left) / geometry.step); draw();
  });
  canvas.addEventListener('pointerleave', () => { active = null; draw(); });
  canvas.addEventListener('blur', () => { active = null; draw(); });
  canvas.addEventListener('keydown', e => {
    if (!geometry || !['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) return;
    e.preventDefault();
    const last = geometry.bars.length - 1;
    active = e.key === 'Home' ? 0 : e.key === 'End' ? last : (active ?? last) + (e.key === 'ArrowLeft' ? -1 : 1); draw();
  });
  new ResizeObserver(draw).observe(canvas.parentElement);
  return (candles, zones, ticker, frame) => { prices = candles; levels = zones ?? {}; symbol = ticker; interval = frame; active = null; draw(); };
}
