'use strict';
/* ---------- util ---------- */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const pad = n => String(n).padStart(2, '0');
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
const fmtTime = (ts, withDate) => { const d = new Date(ts * 1000); return (withDate ? `${d.getDate()} ${MON[d.getMonth()]} ` : '') + `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const fmtDT = ts => { const d = new Date(ts * 1000); return `${d.getDate()} ${MON[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
const ago = ts => { if (!ts) return '-'; const s = Math.max(0, Date.now() / 1000 - ts); if (s < 60) return Math.floor(s) + ' dtk lalu'; if (s < 3600) return Math.floor(s / 60) + ' mnt lalu'; if (s < 86400) return Math.floor(s / 3600) + ' jam lalu'; return Math.floor(s / 86400) + ' hari lalu'; };
const dur = s => { s = Math.floor(s || 0); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return d ? `${d}h ${h}j` : h ? `${h}j ${m}m` : `${m}m`; };
function fmtBps(v, digits = 1) { if (v == null || isNaN(v)) return '–'; const u = ['bps', 'Kbps', 'Mbps', 'Gbps']; let i = 0; while (v >= 1000 && i < 3) { v /= 1000; i++; } return (v >= 100 || i === 0 ? v.toFixed(0) : v.toFixed(digits)) + ' ' + u[i]; }
function fmtBytes(v) { if (v == null) return '–'; const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (v >= 1024 && i < 4) { v /= 1024; i++; } return v.toFixed(i > 1 ? 1 : 0) + ' ' + u[i]; }
const num = (v, d = 0) => v == null || isNaN(v) ? '–' : Number(v).toFixed(d);
const lvl = p => p == null ? '' : p >= 90 ? 'bad' : p >= 75 ? 'warn' : '';

function toast(msg, kind = '') {
  const t = document.createElement('div'); t.className = 'toast ' + kind; t.textContent = msg;
  $('#toasts').append(t); setTimeout(() => t.remove(), 4500);
}
async function api(path, opts = {}) {
  const o = { credentials: 'same-origin', headers: { 'content-type': 'application/json' }, method: opts.method || 'GET' };
  if (opts.body !== undefined) o.body = JSON.stringify(opts.body);
  const r = await fetch('/api' + path, o);
  if (r.status === 401 && path !== '/login') { showLogin(); throw new Error('Sesi berakhir'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

/* ---------- charts ---------- */
const charts = {};
function destroyCharts() { Object.values(charts).forEach(c => c.destroy()); for (const k in charts) delete charts[k]; }
const crosshair = {
  id: 'crosshair',
  afterDatasetsDraw(chart) {
    const { ctx, chartArea: a } = chart;
    const act = chart.tooltip && chart.tooltip.getActiveElements ? chart.tooltip.getActiveElements() : [];
    if (act.length) { ctx.save(); ctx.strokeStyle = css('--line2'); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(act[0].element.x, a.top); ctx.lineTo(act[0].element.x, a.bottom); ctx.stroke(); ctx.restore(); }
    const lim = chart.options.plugins.limitline && chart.options.plugins.limitline.value;
    if (lim != null) {
      const y = chart.scales.y.getPixelForValue(lim);
      if (y > a.top && y < a.bottom) { ctx.save(); ctx.strokeStyle = css('--bad'); ctx.globalAlpha = .7; ctx.setLineDash([5, 4]); ctx.beginPath(); ctx.moveTo(a.left, y); ctx.lineTo(a.right, y); ctx.stroke(); ctx.restore(); }
    }
  }
};
Chart.register(crosshair);
const UNITS = { bps: v => fmtBps(v), pct: v => num(v, 0) + '%', ms: v => num(v, 0) + ' ms', c: v => num(v, 0) + '°C', n: v => num(v, 0) };
function hexA(hex, a) { if (hex.startsWith('rgb')) return hex; const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${n >> 8 & 255},${n & 255},${a})`; }
function toPoints(t, v, bucket) {
  const out = [];
  for (let i = 0; i < t.length; i++) {
    if (i && t[i] - t[i - 1] > bucket * 2.5) out.push({ x: (t[i - 1] + bucket) * 1000, y: null });
    out.push({ x: t[i] * 1000, y: v[i] });
  }
  return out;
}
/* cfg: {t, bucket, range, series:[{label,v,color,fill}], unit, max, limit, stack} */
function lineChart(id, canvas, cfg) {
  const now = Date.now(), unit = UNITS[cfg.unit] || UNITS.n;
  const ds = cfg.series.map(s => ({
    label: s.label, data: toPoints(cfg.t, s.v, cfg.bucket), borderColor: css(s.color), borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 4,
    pointHoverBackgroundColor: css(s.color), pointHoverBorderColor: css('--panel'), pointHoverBorderWidth: 2, tension: .25, spanGaps: false,
    fill: s.fill === false ? false : 'origin', backgroundColor: hexA(css(s.color), cfg.series.length > 1 ? .10 : .14),
  }));
  const xmin = now - cfg.range * 1000;
  if (charts[id] && charts[id].canvas === canvas) {
    const c = charts[id]; c.data.datasets.forEach((d, i) => d.data = ds[i].data);
    c.options.scales.x.min = xmin; c.options.scales.x.max = now; c.options.plugins.limitline.value = cfg.limit; c.update('none'); return;
  }
  if (charts[id]) charts[id].destroy();
  const fmtX = v => fmtTime(v / 1000, cfg.range > 86400);
  charts[id] = new Chart(canvas, {
    type: 'line', data: { datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, parsing: false, normalized: true,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: { top: 2 } },
      scales: {
        x: { type: 'linear', min: xmin, max: now, grid: { color: css('--grid'), drawTicks: false }, border: { display: false },
          ticks: { color: css('--tx3'), maxTicksLimit: 6, maxRotation: 0, padding: 6, font: { size: 11 }, callback: fmtX } },
        y: { min: 0, max: cfg.max, grid: { color: css('--grid'), drawTicks: false }, border: { display: false },
          ticks: { color: css('--tx3'), maxTicksLimit: 5, padding: 8, font: { size: 11 }, callback: v => unit(v) }, grace: cfg.max ? 0 : '8%' },
      },
      plugins: {
        legend: { display: cfg.series.length > 1, align: 'start', labels: { color: css('--tx2'), usePointStyle: true, pointStyle: 'line', boxWidth: 18, boxHeight: 2, font: { size: 12 } } },
        limitline: { value: cfg.limit },
        tooltip: {
          backgroundColor: css('--raise'), titleColor: css('--tx'), bodyColor: css('--tx2'), borderColor: css('--line2'), borderWidth: 1, padding: 10, boxPadding: 4,
          usePointStyle: true, callbacks: {
            title: items => items.length ? fmtDT(items[0].parsed.x / 1000) : '',
            label: c => `${c.dataset.label}: ${c.parsed.y == null ? '–' : unit(c.parsed.y)}`,
          },
        },
      },
    },
  });
}
function spark(points, color) { // points [[ts,cpu,traffic]]
  const v = points.map(p => p[2]).filter(x => x != null);
  if (v.length < 2) return '<svg class="spark" viewBox="0 0 130 34"></svg>';
  const max = Math.max(...v, 1), W = 130, H = 34;
  const pts = points.filter(p => p[2] != null).map((p, i, a) => `${(i / (a.length - 1) * W).toFixed(1)},${(H - 3 - p[2] / max * (H - 6)).toFixed(1)}`);
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><polygon points="0,${H} ${pts.join(' ')} ${W},${H}" fill="${color}" opacity=".13"/><polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

/* ---------- state & routing ---------- */
let timer = null, refresh = null, curPage = '';
const S = { filter: { q: '', group: '', status: 'all' }, dashRange: '6h', range: '1h', iface: {} };
function setNav(p) { $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.p === p)); }
function showLogin() { $('#app').hidden = true; $('#login').hidden = false; if (timer) clearInterval(timer); }
async function route() {
  if (timer) clearInterval(timer); refresh = null; destroyCharts();
  const h = location.hash.replace(/^#/, '') || '/';
  const m = h.match(/^\/router\/(\d+)/);
  try {
    if (m) { setNav('routers'); await pageDetail(+m[1]); }
    else if (h === '/routers') { setNav('routers'); await pageRouters(); }
    else if (h === '/latency') { setNav('latency'); await pageLatency(); }
    else if (h === '/vpn') { setNav('vpn'); await pageVpn(); }
    else if (h === '/events') { setNav('events'); await pageEvents(); }
    else if (h === '/settings') { setNav('settings'); await pageSettings(); }
    else { setNav('dash'); await pageDash(); }
  } catch (e) { if (e.message !== 'Sesi berakhir') $('#main').innerHTML = `<div class="empty">Gagal memuat: ${esc(e.message)}</div>`; return; }
  if (refresh) timer = setInterval(() => refresh().catch(() => { }), 10000);
}
window.addEventListener('hashchange', route);

/* ---------- dashboard ---------- */
const statusOf = r => !r.enabled ? 'off' : !r.last_poll ? 'wait' : r.online ? 'on' : 'down';
function routerCard(r) {
  const st = statusOf(r), L = r.latest || {}, cls = st === 'down' ? 'down' : r.alerts.length && st === 'on' ? 'alert' : st === 'off' ? 'off' : '';
  const pill = st === 'on' ? '<span class="pill ok"><i class="dot"></i>Online</span>' : st === 'down' ? '<span class="pill bad"><i class="dot"></i>Down</span>' :
    st === 'off' ? '<span class="pill">Nonaktif</span>' : '<span class="pill">Menunggu…</span>';
  const meter = (k, v, txt) => `<div class="meter"><div class="k">${k}<b>${txt}</b></div><div class="bar"><i class="${lvl(v)}" style="width:${Math.min(100, v || 0)}%"></i></div></div>`;
  const body = st === 'on' ? `
    <div class="meters">${meter('CPU', L.cpu, num(L.cpu) + '%')}${meter('RAM', L.mem_pct, num(L.mem_pct) + '%')}
      <div class="meter"><div class="k">Latency<b>${num(L.latency)} ms</b></div><div class="bar"><i class="${L.latency > 300 ? 'bad' : L.latency > 150 ? 'warn' : ''}" style="width:${Math.min(100, (L.latency || 0) / 4)}%"></i></div></div></div>
    <div class="tr"><div class="rates"><span class="d">↓ ${fmtBps(L.rx_bps)}</span><span class="u">↑ ${fmtBps(L.tx_bps)}</span></div>${spark(r.spark, css('--s1'))}</div>
    <div class="foot">${L.temp != null ? `<span>${num(L.temp)}°C</span>` : ''}<span>Uptime ${dur(L.uptime)}</span>${L.dhcp != null ? `<span>${L.dhcp} klien</span>` : ''}${L.ppp ? `<span>${L.ppp} VPN</span>` : ''}</div>`
    : st === 'down' ? `<div class="err">${esc(r.last_error || 'Tidak merespon')}</div><div class="foot"><span>${r.last_ok ? 'Terakhir online ' + ago(r.last_ok) : 'Belum pernah terhubung'}</span></div>`
      : `<div class="foot"><span>${st === 'off' ? 'Polling dimatikan' : 'Menunggu data pertama'}</span></div>`;
  const al = r.alerts.length ? `<div class="foot">${r.alerts.map(a => `<span class="chip ${a.level === 'critical' ? 'bad' : 'warn'}">${esc(a.message)}</span>`).join('')}</div>` : '';
  return `<a class="rc ${cls}" href="#/router/${r.id}"><div class="top"><div style="min-width:0"><div class="nm">${esc(r.name)}</div><div class="ip">${esc(r.host)}${r.group_name ? ' · ' + esc(r.group_name) : ''}</div></div>${pill}</div>${body}${st === 'on' ? al : ''}</a>`;
}
function eventItem(e) {
  return `<li><i class="ico ${esc(e.level)}"></i><div><div class="t">${esc(e.router_name ? e.router_name + ': ' : '')}${esc(e.message)}</div><div class="m">${fmtDT(e.ts)}</div></div></li>`;
}
async function pageDash() {
  $('#main').innerHTML = `
  <div class="page-head"><div><h1>Dashboard</h1><div class="sub" id="dSub"></div></div><div class="row"><a class="btn primary" href="#/routers">Tambah router</a></div></div>
  <div class="kpis" id="kpis"></div>
  <div class="split">
    <section class="panel"><header><h2>Total traffic uplink semua router</h2><div class="seg" id="dRange">${['1h', '6h', '24h', '7d'].map(r => `<button data-r="${r}" class="${S.dashRange === r ? 'on' : ''}">${r}</button>`).join('')}</div></header><div class="cwrap tall"><canvas id="fleetC"></canvas></div></section>
    <section class="panel"><header><h2>Alert aktif</h2></header><ul class="list" id="alertList"></ul></section>
  </div>
  <div class="filters" id="filters"></div>
  <div class="cards" id="cards"></div>`;
  $('#dRange').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.dashRange = b.dataset.r; $$('#dRange button').forEach(x => x.classList.toggle('on', x === b)); loadFleet(); };
  let data;
  const loadFleet = async () => {
    const f = await api('/fleet/traffic?range=' + S.dashRange), sec = { '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 }[S.dashRange];
    lineChart('fleet', $('#fleetC'), { t: f.t, bucket: f.bucket, range: sec, unit: 'bps', series: [{ label: 'Download (masuk)', v: f.rx, color: '--s1' }, { label: 'Upload (keluar)', v: f.tx, color: '--s2' }] });
  };
  const drawCards = () => {
    const f = S.filter; let rs = data.routers.filter(r => (!f.q || (r.name + r.host + r.group_name).toLowerCase().includes(f.q.toLowerCase())) && (!f.group || r.group_name === f.group) &&
      (f.status === 'all' || (f.status === 'down' && statusOf(r) === 'down') || (f.status === 'on' && statusOf(r) === 'on') || (f.status === 'alert' && r.alerts.length)));
    const rank = r => ({ down: 0, on: r.alerts.length ? 1 : 2, wait: 3, off: 4 }[statusOf(r)]);
    rs.sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
    $('#cards').innerHTML = rs.map(routerCard).join('') || '<div class="empty panel" style="grid-column:1/-1">Belum ada router yang cocok. Tambahkan lewat menu Router.</div>';
  };
  const draw = async () => {
    data = await api('/overview'); const s = data.summary;
    $('#dSub').textContent = `Diperbarui ${new Date().toLocaleTimeString('id-ID').replace(/\./g, ':')} · polling tiap ${data.poll_interval} detik`;
    $('#kpis').innerHTML = `
      <div class="panel kpi"><div class="l">Router online</div><div class="v">${s.online}<small>/ ${s.enabled}</small></div><div class="s">${s.offline ? `<span class="pill bad"><i class="dot"></i>${s.offline} down</span>` : '<span class="pill ok"><i class="dot"></i>Semua normal</span>'}</div></div>
      <div class="panel kpi"><div class="l">Alert aktif</div><div class="v">${s.alerts}</div><div class="s">${s.alerts ? 'Perlu dicek' : 'Tidak ada'}</div></div>
      <div class="panel kpi"><div class="l">Download sekarang</div><div class="v" style="color:var(--s1)">${fmtBps(s.rx_bps)}</div><div class="s">Total uplink</div></div>
      <div class="panel kpi"><div class="l">Upload sekarang</div><div class="v" style="color:var(--s2)">${fmtBps(s.tx_bps)}</div><div class="s">Total uplink</div></div>
      <div class="panel kpi"><div class="l">Latency rata-rata</div><div class="v">${num(s.avg_latency)}<small>ms</small></div><div class="s">Server ke router</div></div>
      <div class="panel kpi"><div class="l">Klien DHCP</div><div class="v">${s.dhcp}</div><div class="s">${s.ppp} sesi VPN/PPP aktif</div></div>`;
    $('#alertList').innerHTML = data.alerts.length ? data.alerts.slice(0, 8).map(a => `<li><i class="ico ${a.level}"></i><div><div class="t"><a href="#/router/${a.router_id}"><b>${esc(a.router_name)}</b></a> — ${esc(a.message)}</div><div class="m">sejak ${fmtDT(a.since)}</div></div></li>`).join('')
      : `<li class="empty" style="display:block">Tidak ada alert aktif.</li>` ;
    const groups = [...new Set(data.routers.map(r => r.group_name).filter(Boolean))].sort();
    if (!$('#fq')) {
      $('#filters').innerHTML = `<input id="fq" placeholder="Cari nama, IP, atau grup" value="${esc(S.filter.q)}"><select id="fg"><option value="">Semua grup</option>${groups.map(g => `<option ${S.filter.group === g ? 'selected' : ''}>${esc(g)}</option>`).join('')}</select>
        <div class="seg" id="fs">${[['all', 'Semua'], ['on', 'Online'], ['down', 'Down'], ['alert', 'Ada alert']].map(([k, l]) => `<button data-s="${k}" class="${S.filter.status === k ? 'on' : ''}">${l}</button>`).join('')}</div>`;
      $('#fq').oninput = e => { S.filter.q = e.target.value; drawCards(); };
      $('#fg').onchange = e => { S.filter.group = e.target.value; drawCards(); };
      $('#fs').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.filter.status = b.dataset.s; $$('#fs button').forEach(x => x.classList.toggle('on', x === b)); drawCards(); };
    }
    drawCards();
  };
  await draw(); await loadFleet();
  refresh = async () => { await draw(); await loadFleet(); };
}

/* ---------- detail router ---------- */
const RANGES = { '1h': 3600, '3h': 10800, '6h': 21600, '24h': 86400, '7d': 604800, '30d': 2592000 };
async function pageDetail(id) {
  const [d0, settings] = await Promise.all([api('/routers/' + id), api('/settings')]);
  $('#main').innerHTML = `<div id="dHead"></div>
  <div class="row" style="justify-content:space-between;margin-bottom:12px"><div class="seg" id="rSeg">${Object.keys(RANGES).map(r => `<button data-r="${r}" class="${S.range === r ? 'on' : ''}">${r}</button>`).join('')}</div><div class="hint" id="rHint"></div></div>
  <div class="kpis" id="dK"></div>
  <section class="panel" style="margin-bottom:12px"><header><h2>Ketersediaan</h2><div class="stat-line" id="avail"></div></header><div style="height:12px"></div><div class="strip" id="strip"></div></section>
  <section class="panel" id="latPanel" style="margin-bottom:12px"><header><h2>Network Latency</h2><div class="row"><span class="hint" id="latHint"></span><button class="btn sm" id="latNow">Ukur sekarang</button></div></header><div id="latBody" class="lat-list"><div class="empty">Memuat…</div></div></section>
  <div id="chartGrid"></div>
  <div class="sect"><h2>Interface</h2><span class="hint">Klik bintang untuk memantau interface — alert dikirim saat link down.</span></div>
  <section class="panel"><div class="tbl-wrap"><table class="tbl" id="ifTbl"></table></div></section>
  <div class="grid2" style="margin-top:12px">
    <section class="panel" id="ifPanel"><header><h2 id="ifTitle">Traffic interface</h2><select id="ifSel" style="width:auto"></select></header><div class="cwrap"><canvas id="ifC"></canvas></div></section>
    <div class="cols2" style="grid-template-columns:1fr;gap:12px">
      <section class="panel"><header><h2>Info perangkat</h2></header><div class="pad"><dl class="kv" id="info"></dl></div></section>
      <section class="panel"><header><h2>Kejadian terakhir</h2></header><ul class="list" id="evs"></ul></section>
    </div></div>`;
  let det = d0, M = null, wanted = new Set(d0.watched);
  const rt = d0.router, lim = k => rt[k] != null ? rt[k] : settings[k];

  const panels = [
    { id: 'wan', title: 'Traffic uplink', unit: 'bps', series: [{ label: 'Download', k: 'rx', color: '--s1' }, { label: 'Upload', k: 'tx', color: '--s2' }], stat: m => `rata-rata <b>${fmtBps(m.summary.rx_avg)}</b> ↓ · puncak <b>${fmtBps(m.summary.rx_max)}</b>` },
    { id: 'cpu', title: 'CPU', unit: 'pct', max: 100, limit: lim('cpu_limit'), series: [{ label: 'CPU', k: 'cpu', color: '--s1' }], stat: m => `rata-rata <b>${num(m.summary.cpu_avg)}%</b> · puncak <b>${num(m.summary.cpu_max)}%</b>` },
    { id: 'mem', title: 'Memori', unit: 'pct', max: 100, limit: lim('mem_limit'), series: [{ label: 'RAM terpakai', k: 'mem', color: '--s3' }], stat: m => `rata-rata <b>${num(m.summary.mem_avg)}%</b> · puncak <b>${num(m.summary.mem_max)}%</b>` },
    { id: 'lat', title: 'Latency ke router', unit: 'ms', limit: lim('latency_limit'), series: [{ label: 'Latency', k: 'lat', color: '--s5' }], stat: m => `rata-rata <b>${num(m.summary.lat_avg)} ms</b> · puncak <b>${num(m.summary.lat_max)} ms</b>` },
    { id: 'temp', title: 'Suhu', unit: 'c', limit: lim('temp_limit'), series: [{ label: 'Suhu', k: 'temp', color: '--s2' }], stat: m => `puncak <b>${num(m.summary.temp_max)}°C</b>`, need: 'temp' },
    { id: 'cli', title: 'Klien & sesi', unit: 'n', series: [{ label: 'DHCP', k: 'dhcp', color: '--s1' }, { label: 'WiFi', k: 'wifi', color: '--s3' }, { label: 'VPN/PPP', k: 'ppp', color: '--s4' }], stat: () => '', need: ['dhcp', 'wifi', 'ppp'] },
    { id: 'conn', title: 'Koneksi firewall (conntrack)', unit: 'n', series: [{ label: 'Koneksi', k: 'conn', color: '--s5' }], stat: () => '', need: 'conn' },
  ];
  const hasData = (p, m) => !p.need || [].concat(p.need).some(k => m[k].some(v => v != null));
  function buildGrid() {
    const shown = panels.filter(p => hasData(p, M));
    $('#chartGrid').innerHTML = `<div class="grid2">${shown.map(p => `<section class="panel"><header><h2>${p.title}</h2><div class="stat-line" id="st_${p.id}"></div></header><div class="cwrap"><canvas id="c_${p.id}"></canvas></div></section>`).join('')}</div>`;
    return shown;
  }
  let shownIds = '';
  function drawCharts() {
    if (!M) return;
    const shown = panels.filter(p => hasData(p, M)), sig = shown.map(p => p.id).join();
    if (sig !== shownIds) { destroyCharts(); buildGrid(); shownIds = sig; }
    shown.forEach(p => {
      lineChart(p.id, $('#c_' + p.id), { t: M.t, bucket: M.bucket, range: M.range, unit: p.unit, max: p.max, limit: p.limit, series: p.series.map(s => ({ label: s.label, v: M[s.k], color: s.color })) });
      $('#st_' + p.id).innerHTML = p.stat(M);
    });
    const sm = M.summary;
    $('#avail').innerHTML = `<span>periode ini <b>${num(sm.avail, 2)}%</b></span><span>24j <b>${num(det.availability['24h'], 2)}%</b></span><span>7h <b>${num(det.availability['7d'], 2)}%</b></span><span>Total ↓ <b>${fmtBytes(sm.rx_bytes)}</b> ↑ <b>${fmtBytes(sm.tx_bytes)}</b></span>`;
    $('#strip').innerHTML = M.t.map((t, i) => `<i class="${M.online[i] === 0 ? 'bad' : ''}" title="${fmtTime(t, M.range > 86400)} — ${M.online[i] === 0 ? 'down' : 'online'}"></i>`).join('');
  }
  function drawStatic() {
    const r = det.router, st = det.state, L = det.latest, I = det.info, on = st.online;
    const stat = !r.enabled ? '<span class="pill">Nonaktif</span>' : on ? '<span class="pill ok"><i class="dot"></i>Online</span>' : '<span class="pill bad"><i class="dot"></i>Down</span>';
    $('#dHead').innerHTML = `<div class="page-head"><div><div class="row"><a class="btn ghost sm" href="#/">← Dashboard</a></div><div class="row" style="margin-top:6px"><h1>${esc(r.name)}</h1>${stat}</div>
      <div class="sub mono">${esc(r.host)}${r.group_name ? ' · ' + esc(r.group_name) : ''}${I.model ? ' · ' + esc(I.model) : ''}${I.version ? ' · RouterOS ' + esc(I.version) : ''}</div></div>
      <div class="row"><button class="btn" id="pollBtn">Cek sekarang</button><button class="btn" id="diagBtn">Diagnosa</button><button class="btn" id="editBtn">Edit</button></div></div>
      ${!on && st.last_poll ? `<div class="panel pad" style="border-color:var(--bad);margin-bottom:12px"><b style="color:var(--bad)">Router tidak merespon</b> — ${esc(st.last_error || '')}<div class="hint">Terakhir online ${ago(st.last_ok)} · gagal ${st.fail_count}x berturut-turut</div></div>` : ''}
      ${det.alerts.length ? `<div class="row" style="margin-bottom:12px">${det.alerts.map(a => `<span class="chip ${a.level === 'critical' ? 'bad' : 'warn'}">${esc(a.message)}</span>`).join('')}</div>` : ''}`;
    $('#pollBtn').onclick = async e => { e.target.disabled = true; try { await api(`/routers/${id}/poll`, { method: 'POST' }); await refresh(); } finally { e.target.disabled = false; } };
    $('#diagBtn').onclick = () => openDiag(`/routers/${id}/diag`, r.name);
    $('#editBtn').onclick = async () => { const rr = (await api('/routers')).find(x => x.id === id); openRouterForm(rr, () => route()); };
    const kp = (l, v, s, c) => `<div class="panel kpi"><div class="l">${l}</div><div class="v" ${c ? `style="color:${c}"` : ''}>${v}</div><div class="s">${s || ''}</div></div>`;
    $('#dK').innerHTML = on ? kp('CPU', num(L.cpu) + '<small>%</small>', `${I.cpu_count || ''} core · ${esc(I.arch || '')}`) + kp('Memori', num(L.mem_pct) + '<small>%</small>', `${fmtBytes(L.mem_used)} / ${fmtBytes(L.mem_total)}`) +
      kp('Latency', num(L.latency) + '<small>ms</small>') + kp('Download', fmtBps(L.rx_bps), `uplink ${esc((L.wan || []).join(', ') || '–')}${L.wan_auto ? ' (otomatis)' : ''}`, 'var(--s1)') + kp('Upload', fmtBps(L.tx_bps), '', 'var(--s2)') +
      kp('Uptime', dur(L.uptime), L.temp != null ? `${num(L.temp)}°C${L.volt != null ? ' · ' + num(L.volt, 1) + ' V' : ''}` : '') + kp('Klien', (L.dhcp ?? '–') + (L.wifi != null ? `<small>· ${L.wifi} wifi</small>` : ''), L.ppp != null ? `${L.ppp} sesi VPN/PPP` : '') : '';
    $('#info').innerHTML = [['Identity', I.identity], ['Model', I.model], ['Board', I.board], ['RouterOS', I.version], ['Firmware', I.firmware], ['CPU', [I.cpu_name, I.arch].filter(Boolean).join(' · ')], ['Serial', I.serial], ['Terakhir poll', st.last_poll ? ago(st.last_poll) : '-'], ['Catatan', r.notes]].filter(x => x[1]).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
    $('#evs').innerHTML = det.events.length ? det.events.map(eventItem).join('') : '<li class="empty" style="display:block">Belum ada kejadian.</li>';
    drawIfaces();
  }
  function drawIfaces() {
    const L = det.latest, ifs = (L.ifaces || []).filter(i => !i.disabled);
    ifs.sort((a, b) => (b.rx_bps || 0) + (b.tx_bps || 0) - (a.rx_bps || 0) - (a.tx_bps || 0));
    $('#ifTbl').innerHTML = `<thead><tr><th></th><th>Nama</th><th>Tipe</th><th>Status</th><th class="r">↓ Download</th><th class="r">↑ Upload</th><th class="r">Total ↓</th><th class="r">Total ↑</th></tr></thead><tbody>` +
      (ifs.map(i => `<tr><td><button class="star ${wanted.has(i.name) ? 'on' : ''}" data-n="${esc(i.name)}" title="Pantau interface ini" aria-label="Pantau ${esc(i.name)}">${wanted.has(i.name) ? '★' : '☆'}</button></td>
        <td><b>${esc(i.name)}</b>${i.comment ? `<div class="hint">${esc(i.comment)}</div>` : ''}</td><td class="dim">${esc(i.type)}</td>
        <td>${i.running ? '<span class="pill ok"><i class="dot"></i>Up</span>' : '<span class="pill bad"><i class="dot"></i>Down</span>'}</td>
        <td class="r num">${fmtBps(i.rx_bps)}</td><td class="r num">${fmtBps(i.tx_bps)}</td><td class="r num dim">${fmtBytes(i.rx)}</td><td class="r num dim">${fmtBytes(i.tx)}</td></tr>`).join('') || '<tr><td colspan="8" class="empty">Belum ada data interface.</td></tr>') + '</tbody>';
    const sel = $('#ifSel'), cur = S.iface[id] || (L.wan && L.wan[0]) || (ifs[0] && ifs[0].name);
    if (sel.options.length !== ifs.length) { sel.innerHTML = ifs.map(i => `<option ${i.name === cur ? 'selected' : ''}>${esc(i.name)}</option>`).join(''); }
    S.iface[id] = sel.value || cur;
  }
  $('#ifTbl').onclick = async e => {
    const b = e.target.closest('.star'); if (!b) return;
    const n = b.dataset.n; wanted.has(n) ? wanted.delete(n) : wanted.add(n);
    await api(`/routers/${id}/watched`, { method: 'PUT', body: { names: [...wanted] } }); drawIfaces(); toast(wanted.has(n) ? `Memantau ${n}` : `Berhenti memantau ${n}`, 'ok');
  };
  $('#ifSel').onchange = e => { S.iface[id] = e.target.value; loadIface(true); };
  async function loadIface(fresh) {
    const n = S.iface[id]; if (!n) return;
    const j = await api(`/routers/${id}/iface?name=${encodeURIComponent(n)}&range=${S.range}`);
    if (fresh && charts.iface) { charts.iface.destroy(); delete charts.iface; }
    $('#ifTitle').textContent = 'Traffic ' + n;
    lineChart('iface', $('#ifC'), { t: j.t, bucket: j.bucket, range: RANGES[S.range], unit: 'bps', series: [{ label: 'Download', v: j.rx, color: '--s1' }, { label: 'Upload', v: j.tx, color: '--s2' }] });
  }
  async function load() {
    [det, M] = await Promise.all([api('/routers/' + id), api(`/routers/${id}/metrics?range=${S.range}`)]);
    wanted = new Set(det.watched);
    $('#rHint').textContent = `Resolusi ${M.bucket} detik`;
    drawStatic(); drawCharts(); await loadIface(); await loadLat();
  }
  async function loadLat() {
    try {
      const j = await api(`/routers/${id}/latency?range=${S.range}`);
      $('#latHint').textContent = j.enabled ? `grafik ${S.range}` : 'dimatikan di Pengaturan';
      $('#latBody').innerHTML = (j.error ? `<div class="lat-err">${esc(j.error)}</div>` : '') + (j.targets.length ? latRows(j) : '<div class="empty">Belum ada target. Atur di Pengaturan → Network Latency.</div>');
    } catch (e) { $('#latBody').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  }
  $('#latNow').onclick = async e => { e.target.disabled = true; try { const j = await api(`/routers/${id}/latency/probe`, { method: 'POST' }); if (!j.measured && !j.error) toast('Tidak ada target terukur'); await loadLat(); } catch (x) { toast(x.message, 'bad'); } finally { e.target.disabled = false; } };
  async function drawChartsAsync() { M = await api(`/routers/${id}/metrics?range=${S.range}`); $('#rHint').textContent = `Resolusi ${M.bucket} detik`; drawCharts(); await loadIface(true); }
  // rebind (range change)
  $('#rSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.range = b.dataset.r; $$('#rSeg button').forEach(x => x.classList.toggle('on', x === b)); destroyCharts(); shownIds = ''; drawChartsAsync(); loadLat(); };
  await load();
  refresh = load;
}

/* ---------- router management ---------- */
const SETUP_CMD = `# Jalankan di terminal MikroTik (ganti subnet VPN & password)
/user group add name=monitor policy=read,api,test
/user add name=monitor group=monitor password=GANTI_PASSWORD_KUAT address=10.8.0.0/24
/ip service set api disabled=no address=10.8.0.0/24
/ip service set api-ssl disabled=yes
# Opsional: buka port API hanya dari VPN di firewall
/ip firewall filter add chain=input protocol=tcp dst-port=8728 src-address=10.8.0.0/24 action=accept comment="monitoring" place-before=0`;

function openModal(html) { const m = $('#modal'); m.innerHTML = html; m.hidden = false; m.onmousedown = e => { if (e.target === m) closeModal(); }; }
function closeModal() { $('#modal').hidden = true; $('#modal').innerHTML = ''; }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

function openRouterForm(r, done) {
  const e = !!r; r = r || { port: 8728, username: 'monitor', enabled: 1, alerts_enabled: 1, use_ssl: 0 };
  const v = k => esc(r[k] ?? '');
  openModal(`<form class="sheet" id="rf"><header><h2>${e ? 'Edit router' : 'Tambah router'}</h2><button type="button" class="btn ghost sm" id="rfX" aria-label="Tutup">✕</button></header>
  <div class="body">
    <label>Nama router<input name="name" value="${v('name')}" required placeholder="Cabang Bandung"></label>
    <label>Grup / lokasi<input name="group_name" value="${v('group_name')}" placeholder="Jawa Barat" list="grpList"></label>
    <label>IP VPN router<input name="host" value="${v('host')}" required placeholder="10.8.0.12" class="mono"></label>
    <label>Port API<input name="port" type="number" value="${v('port')}" class="mono"></label>
    <label>Username<input name="username" value="${v('username')}" required></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" placeholder="${e && r.has_password ? 'Kosongkan jika tidak diubah' : ''}"></label>
    <label class="chk full"><input type="checkbox" name="use_ssl" ${r.use_ssl ? 'checked' : ''}> Pakai API-SSL (port 8729)</label>
    <div class="full"><button type="button" class="btn" id="rfTest">Tes koneksi</button><div id="testOut" style="margin-top:10px"></div></div>
    <label class="full">Interface uplink (opsional)<input name="wan_iface" value="${v('wan_iface')}" placeholder="kosong = otomatis, contoh: ether1-WAN" class="mono"><span class="hint">Dipakai untuk grafik traffic utama. Pisahkan koma jika lebih dari satu.</span></label>
    <label class="full">Catatan<input name="notes" value="${v('notes')}"></label>
    <label class="chk"><input type="checkbox" name="enabled" ${r.enabled ? 'checked' : ''}> Aktif dipantau</label>
    <label class="chk"><input type="checkbox" name="alerts_enabled" ${r.alerts_enabled ? 'checked' : ''}> Kirim alert Telegram</label>
    <details class="full"><summary>Batas alert khusus router ini</summary><div class="body" style="padding:12px 0 0;grid-template-columns:repeat(4,1fr)">
      ${[['cpu_limit', 'CPU %'], ['mem_limit', 'RAM %'], ['temp_limit', 'Suhu °C'], ['latency_limit', 'Latency ms']].map(([k, l]) => `<label>${l}<input name="${k}" type="number" step="any" value="${v(k)}" placeholder="default"></label>`).join('')}</div></details>
  </div>
  <footer>${e ? '<button type="button" class="btn danger" id="rfDel" style="margin-right:auto">Hapus</button>' : ''}<button type="button" class="btn" id="rfCancel">Batal</button><button class="btn primary" type="submit">Simpan</button></footer></form>`);
  const f = $('#rf'), val = n => f.elements[n];
  $('#rfX').onclick = $('#rfCancel').onclick = closeModal;
  val('use_ssl').onchange = () => { if (+val('port').value === 8728 && val('use_ssl').checked) val('port').value = 8729; else if (+val('port').value === 8729 && !val('use_ssl').checked) val('port').value = 8728; };
  const payload = () => ({
    name: val('name').value, group_name: val('group_name').value, host: val('host').value, port: +val('port').value || 8728, username: val('username').value,
    password: val('password').value || null, use_ssl: val('use_ssl').checked, wan_iface: val('wan_iface').value, notes: val('notes').value,
    enabled: val('enabled').checked, alerts_enabled: val('alerts_enabled').checked,
    ...Object.fromEntries(['cpu_limit', 'mem_limit', 'temp_limit', 'latency_limit'].map(k => [k, val(k).value === '' ? null : +val(k).value])),
  });
  $('#rfTest').onclick = async ev => {
    const b = ev.target, out = $('#testOut'); b.disabled = true; out.innerHTML = '<span class="hint">Menghubungi router…</span>';
    try {
      const p = payload(), j = await api('/routers/test', { method: 'POST', body: { id: r.id || null, host: p.host, port: p.port, username: p.username, password: p.password, use_ssl: p.use_ssl } });
      out.innerHTML = j.ok ? `<div class="testres"><b style="color:var(--ok)">Terhubung</b> · ${esc(j.info.identity)} · ${esc(j.info.model)} · RouterOS ${esc(j.info.version)} · ${num(j.latency)} ms<div class="hint" style="margin-top:6px">Klik interface untuk dijadikan uplink:</div>${j.interfaces.map(n => `<span class="chip" data-wan="${esc(n)}">${esc(n)}</span>`).join('')}</div>` : `<div class="testres bad">${esc(j.error)}</div>`;
      $$('[data-wan]', out).forEach(c => c.onclick = () => { val('wan_iface').value = c.dataset.wan; });
    } catch (x) { out.innerHTML = `<div class="testres bad">${esc(x.message)}</div>`; } finally { b.disabled = false; }
  };
  if (e) $('#rfDel').onclick = async () => { if (!confirm(`Hapus router "${r.name}" beserta seluruh riwayatnya?`)) return; await api('/routers/' + r.id, { method: 'DELETE' }); closeModal(); toast('Router dihapus', 'ok'); location.hash = '#/routers'; done && done(); };
  f.onsubmit = async ev => {
    ev.preventDefault();
    try { await api(e ? '/routers/' + r.id : '/routers', { method: e ? 'PUT' : 'POST', body: payload() }); closeModal(); toast(e ? 'Perubahan disimpan' : 'Router ditambahkan', 'ok'); done && done(); }
    catch (x) { toast(x.message, 'bad'); }
  };
}
async function pageRouters() {
  $('#main').innerHTML = `<div class="page-head"><div><h1>Router</h1><div class="sub">Kelola daftar MikroTik yang dipantau lewat VPN.</div></div><div class="row"><button class="btn" id="guideBtn">Panduan setup MikroTik</button><button class="btn primary" id="addBtn">Tambah router</button></div></div>
  <section class="panel"><div class="tbl-wrap"><table class="tbl" id="rt"></table></div></section>`;
  const draw = async () => {
    const ov = await api('/overview');
    $('#rt').innerHTML = `<thead><tr><th>Nama</th><th>IP / Port</th><th>Grup</th><th>Status</th><th>Model</th><th>Terakhir online</th><th></th></tr></thead><tbody>${ov.routers.map(r => {
      const st = statusOf(r); return `<tr><td><a href="#/router/${r.id}"><b>${esc(r.name)}</b></a></td><td class="mono">${esc(r.host)}:${r.port}</td><td>${esc(r.group_name) || '<span class="dim">–</span>'}</td>
      <td>${st === 'on' ? '<span class="pill ok"><i class="dot"></i>Online</span>' : st === 'down' ? '<span class="pill bad"><i class="dot"></i>Down</span>' : st === 'off' ? '<span class="pill">Nonaktif</span>' : '<span class="pill">Menunggu</span>'}</td>
      <td class="dim">${esc(r.info.model || '–')}</td><td class="dim">${ago(r.last_ok)}</td><td class="r"><button class="btn sm" data-e="${r.id}">Edit</button></td></tr>`; }).join('') || '<tr><td colspan="7" class="empty">Belum ada router. Klik "Tambah router".</td></tr>'}</tbody>`;
    $$('[data-e]').forEach(b => b.onclick = async () => openRouterForm((await api('/routers')).find(x => x.id === +b.dataset.e), draw));
    $('#addBtn').onclick = () => openRouterForm(null, draw);
  };
  $('#guideBtn').onclick = () => { openModal(`<div class="sheet"><header><h2>Menyiapkan MikroTik</h2><button class="btn ghost sm" onclick="closeModal()">✕</button></header><div class="pad">
    <p class="muted" style="margin-top:0">Buat user khusus <b>read-only</b> dan batasi API hanya dari subnet VPN. Server monitoring harus bisa menjangkau IP VPN tiap router.</p><pre class="code" id="cmd">${esc(SETUP_CMD)}</pre>
    <button class="btn" id="cp">Salin perintah</button></div></div>`);
    $('#cp').onclick = () => navigator.clipboard.writeText(SETUP_CMD).then(() => toast('Perintah disalin', 'ok'), () => toast('Salin manual dari kotak di atas')); };
  await draw(); refresh = draw;
}

/* ---------- events ---------- */
async function pageEvents() {
  const rs = await api('/routers');
  $('#main').innerHTML = `<div class="page-head"><div><h1>Riwayat alert</h1><div class="sub">Semua kejadian: down, pulih, threshold, interface, reboot.</div></div></div>
  <div class="filters"><select id="evR"><option value="">Semua router</option>${rs.map(r => `<option value="${r.id}">${esc(r.name)}</option>`).join('')}</select>
  <select id="evL"><option value="">Semua level</option><option value="critical">Kritis</option><option value="warning">Peringatan</option><option value="ok">Pulih</option><option value="info">Info</option></select></div>
  <section class="panel"><div class="tbl-wrap"><table class="tbl" id="evT"></table></div></section>`;
  const draw = async () => {
    const qs = new URLSearchParams({ limit: 300 }); if ($('#evR').value) qs.set('router_id', $('#evR').value); if ($('#evL').value) qs.set('level', $('#evL').value);
    const ev = await api('/events?' + qs);
    $('#evT').innerHTML = `<thead><tr><th>Waktu</th><th>Router</th><th>Level</th><th>Pesan</th></tr></thead><tbody>${ev.map(e => `<tr><td class="mono dim" style="white-space:nowrap">${fmtDT(e.ts)}</td><td>${e.router_id ? `<a href="#/router/${e.router_id}">${esc(e.router_name)}</a>` : '–'}</td>
      <td><span class="pill ${e.level === 'critical' ? 'bad' : e.level === 'warning' ? 'warn' : e.level === 'ok' ? 'ok' : ''}">${{ critical: 'Kritis', warning: 'Peringatan', ok: 'Pulih', info: 'Info' }[e.level] || e.level}</span></td><td>${esc(e.message)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">Belum ada kejadian.</td></tr>'}</tbody>`;
  };
  $('#evR').onchange = $('#evL').onchange = draw; await draw(); refresh = draw;
}

/* ---------- settings ---------- */
async function pageSettings() {
  const s = await api('/settings');
  const fld = (k, l, hint, extra = '') => `<label>${l}<input name="${k}" type="number" step="any" value="${esc(s[k])}" ${extra}>${hint ? `<span class="hint">${hint}</span>` : ''}</label>`;
  $('#main').innerHTML = `<div class="page-head"><div><h1>Pengaturan</h1><div class="sub">Telegram, ambang alert, dan penyimpanan data.</div></div><button class="btn primary" id="save">Simpan pengaturan</button></div>
  <form id="sf" onsubmit="return false"><div class="grid2">
  <section class="panel"><header><h2>Notifikasi Telegram</h2></header><div class="pad" style="display:grid;gap:12px">
    <label class="chk"><input type="checkbox" name="tg_enabled" ${s.tg_enabled ? 'checked' : ''}> Aktifkan notifikasi Telegram</label>
    <label>Bot token<input name="tg_token" type="password" autocomplete="off" placeholder="${s.tg_token_set ? s.tg_token_hint + ' (tersimpan, isi untuk mengganti)' : '123456:ABC-DEF…'}" class="mono"></label>
    <label>Chat ID tujuan<input name="tg_chat_ids" value="${esc(s.tg_chat_ids)}" placeholder="123456789, -1001234567890" class="mono"><span class="hint">Boleh lebih dari satu (pisahkan koma) — user, grup, atau channel.</span></label>
    <div class="row"><button type="button" class="btn" id="tgTest">Kirim pesan tes</button>${s.tg_token_set ? '<button type="button" class="btn ghost danger" id="tgClear">Hapus token</button>' : ''}</div>
    <details><summary>Cara mendapatkan token & chat ID</summary><ol class="muted" style="padding-left:18px;margin:8px 0 0"><li>Chat <b>@BotFather</b> → <span class="mono">/newbot</span> → salin token.</li><li>Tempel token di atas lalu <b>Simpan</b>.</li><li>Kirim <span class="mono">/id</span> ke bot Anda (atau tambahkan bot ke grup lalu kirim <span class="mono">/id</span>). Bot membalas chat ID.</li><li>Isi chat ID, simpan, lalu klik <b>Kirim pesan tes</b>.</li><li>Perintah bot: <span class="mono">/status /routers /router nama /alerts</span></li></ol></details>
  </div></section>
  <section class="panel"><header><h2>Ambang alert (default semua router)</h2></header><div class="pad" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    ${fld('cpu_limit', 'CPU di atas (%)')}${fld('mem_limit', 'RAM di atas (%)')}${fld('temp_limit', 'Suhu di atas (°C)')}${fld('latency_limit', 'Latency di atas (ms)')}
    ${fld('offline_after', 'Dianggap DOWN setelah gagal (kali)', 'Gagal poll berturut-turut')}${fld('sustain', 'Threshold bertahan (poll)', 'Cegah alert karena lonjakan sesaat')}
    ${fld('repeat_minutes', 'Ulangi alert aktif tiap (menit)', '0 = tidak diulang')}
    <div style="display:grid;gap:8px;align-content:start"><label class="chk"><input type="checkbox" name="notify_recovery" ${s.notify_recovery ? 'checked' : ''}> Kirim notifikasi saat pulih</label><label class="chk"><input type="checkbox" name="notify_reboot" ${s.notify_reboot ? 'checked' : ''}> Kirim notifikasi saat router reboot</label></div>
  </div></section></div>
  <section class="panel"><header><h2>Network Latency</h2></header><div class="pad" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
    <div style="display:grid;gap:8px;align-content:start"><label class="chk"><input type="checkbox" name="latency_enabled" ${s.latency_enabled ? 'checked' : ''}> Aktifkan ping dari router</label><label class="chk"><input type="checkbox" name="latency_gateway" ${s.latency_gateway ? 'checked' : ''}> Ikut ping gateway router</label></div>
    ${fld('latency_interval', 'Interval (detik)', 'Minimal 20')}${fld('latency_warn', 'Oranye di atas (ms)')}${fld('latency_bad', 'Merah di atas (ms)')}${fld('latency_retention_days', 'Simpan riwayat (hari)')}
    <label style="grid-column:1/-1">Target ping<input name="latency_targets" value="${esc(s.latency_targets)}" class="mono"><span class="hint">Format <span class="mono">Nama:host</span>, pisahkan koma. Mobile Legends &amp; PUBG memakai domain situs resminya; ganti dengan IP/host server game yang Anda pakai bila perlu.</span></label></div></section>
  <section class="panel"><header><h2>VPN (user baru &amp; port-forward)</h2></header><div class="pad" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
    ${fld('vpn_ip_start', 'IP statis mulai (oktet terakhir)')}${fld('vpn_ip_end', 'IP statis sampai')}${fld('vpn_nat_port_min', 'Port publik minimum')}${fld('vpn_nat_port_max', 'Port publik maksimum')}
    <label>Layanan di-forward<input name="vpn_services" value="${esc(s.vpn_services)}" class="mono"><span class="hint">Format Label:port, pisahkan koma</span></label>
    <label>Sumber akses API monitoring<input name="vpn_monitor_address" value="${esc(s.vpn_monitor_address)}" class="mono" placeholder="kosong = subnet VPN /24"><span class="hint">Dipakai di script client (<span class="mono">address=</span>)</span></label></div></section>
  <section class="panel"><header><h2>Polling & penyimpanan</h2></header><div class="pad" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">
    ${fld('poll_interval', 'Interval polling (detik)', 'Minimal 10')}${fld('metrics_retention_days', 'Simpan metrik (hari)')}${fld('iface_retention_days', 'Simpan traffic interface (hari)')}</div></section></form>`;
  const collect = () => { const o = {}; $$('#sf input').forEach(i => { o[i.name] = i.type === 'checkbox' ? i.checked : i.value; }); return o; };
  $('#save').onclick = async () => { try { await api('/settings', { method: 'PUT', body: collect() }); toast('Pengaturan disimpan', 'ok'); route(); } catch (e) { toast(e.message, 'bad'); } };
  $('#tgTest').onclick = async () => { try { await api('/settings', { method: 'PUT', body: collect() }); const j = await api('/telegram/test', { method: 'POST' }); toast(j.ok ? 'Pesan tes terkirim ke Telegram' : 'Gagal: ' + j.error, j.ok ? 'ok' : 'bad'); } catch (e) { toast(e.message, 'bad'); } };
  const c = $('#tgClear'); if (c) c.onclick = async () => { if (confirm('Hapus token bot?')) { await api('/settings', { method: 'PUT', body: { tg_token_clear: true } }); route(); } };
}

/* ---------- VPN ---------- */
let vpnSid = null;
const VPN_SCRIPT_NOTE = 'Tempel di Terminal MikroTik client. Router client otomatis tersambung ke server VPN' ;
function copyBtn(sel, text) { $(sel).onclick = () => navigator.clipboard.writeText(text).then(() => toast('Disalin', 'ok'), () => toast('Salin manual dari kotak')); }

function openVpnServerForm(sv, done) {
  const e = !!sv; sv = sv || { port: 8728, use_ssl: 0, profile: 'default' };
  const v = k => esc(sv[k] ?? '');
  openModal(`<form class="sheet" id="vf"><header><h2>${e ? 'Edit server VPN' : 'Tambah server VPN'}</h2><button type="button" class="btn ghost sm" id="vfX">✕</button></header>
  <div class="body">
    <label>Nama<input name="name" value="${v('name')}" required placeholder="Server VPN utama"></label>
    <label>Host API<input name="host" value="${v('host')}" required placeholder="103.x.x.x" class="mono"><span class="hint">Alamat yang dipakai panel ini untuk konek ke RouterOS API.</span></label>
    <label>Port API<input name="port" type="number" value="${v('port')}" class="mono"></label>
    <label>Username<input name="username" value="${v('username')}" required></label>
    <label>Password<input name="password" type="password" autocomplete="new-password" placeholder="${e && sv.has_password ? 'Kosongkan jika tidak diubah' : ''}"></label>
    <label class="chk"><input type="checkbox" name="use_ssl" ${sv.use_ssl ? 'checked' : ''}> API-SSL (8729)</label>
    <label>IP / domain publik<input name="public_ip" value="${v('public_ip')}" class="mono" placeholder="sama dengan host"><span class="hint">Tujuan L2TP client &amp; alamat port-forward.</span></label>
    <label>Subnet VPN<input name="vpn_subnet" value="${v('vpn_subnet')}" required class="mono" placeholder="11.11.11."><span class="hint">3 oktet pertama + titik.</span></label>
    <label>Profile PPP default<input name="profile" value="${v('profile')}" class="mono"></label>
    <div class="full"><button type="button" class="btn" id="vfTest">Tes koneksi</button><div id="vfOut" style="margin-top:10px"></div></div>
  </div>
  <footer>${e ? '<button type="button" class="btn danger" id="vfDel" style="margin-right:auto">Hapus dari panel</button>' : ''}<button type="button" class="btn" id="vfCancel">Batal</button><button class="btn primary" type="submit">Simpan</button></footer></form>`);
  const f = $('#vf'), val = n => f.elements[n];
  $('#vfX').onclick = $('#vfCancel').onclick = closeModal;
  const payload = () => ({ name: val('name').value, host: val('host').value, port: +val('port').value || 8728, username: val('username').value,
    password: val('password').value || null, use_ssl: val('use_ssl').checked, public_ip: val('public_ip').value, vpn_subnet: val('vpn_subnet').value, profile: val('profile').value });
  $('#vfTest').onclick = async ev => {
    const b = ev.target, out = $('#vfOut'); b.disabled = true; out.innerHTML = '<span class="hint">Menghubungi router…</span>';
    try {
      const p = payload(), j = await api('/vpn/servers/test', { method: 'POST', body: { id: sv.id || null, host: p.host, port: p.port, username: p.username, password: p.password, use_ssl: p.use_ssl } });
      out.innerHTML = j.ok ? `<div class="testres"><b style="color:var(--ok)">Terhubung</b> · ${esc(j.identity)} · ${esc(j.board)} · RouterOS ${esc(j.version)}${j.l2tp_enabled === false ? '<div style="color:var(--warn)">L2TP server belum aktif di router ini.</div>' : ''}${j.profiles.length ? `<div class="hint" style="margin-top:6px">Profile PPP (klik untuk jadi default):</div>${j.profiles.map(n => `<span class="chip" data-p="${esc(n)}">${esc(n)}</span>`).join('')}` : ''}</div>` : `<div class="testres bad">${esc(j.error)}</div>`;
      $$('[data-p]', out).forEach(c => c.onclick = () => { val('profile').value = c.dataset.p; });
    } catch (x) { out.innerHTML = `<div class="testres bad">${esc(x.message)}</div>`; } finally { b.disabled = false; }
  };
  if (e) $('#vfDel').onclick = async () => { if (!confirm(`Hapus server "${sv.name}" dari panel? Router di lapangan tidak berubah.`)) return; await api('/vpn/servers/' + sv.id, { method: 'DELETE' }); closeModal(); vpnSid = null; done && done(); };
  f.onsubmit = async ev => { ev.preventDefault(); try { await api(e ? '/vpn/servers/' + sv.id : '/vpn/servers', { method: e ? 'PUT' : 'POST', body: payload() }); closeModal(); toast('Tersimpan', 'ok'); done && done(); } catch (x) { toast(x.message, 'bad'); } };
}

function showVpnResult(res) {
  openModal(`<div class="sheet"><header><h2>User VPN siap</h2><button class="btn ghost sm" onclick="closeModal()">✕</button></header><div class="pad">
    <p class="muted" style="margin-top:0">Simpan sekarang — password hanya ditampilkan di sini (bisa diambil lagi lewat tombol <b>Script</b>).</p>
    <table class="tbl"><tbody><tr><td class="dim">Username</td><td class="mono">${esc(res.username)}</td></tr><tr><td class="dim">Password</td><td class="mono">${esc(res.password)}</td></tr><tr><td class="dim">IP VPN</td><td class="mono">${esc(res.ip)}</td></tr>
    ${res.access.map(a => `<tr><td class="dim">${esc(a.name)}</td><td class="mono">${esc(a.host)}:${esc(a.port)}</td></tr>`).join('')}
    ${res.router_id ? `<tr><td class="dim">Monitoring</td><td><a href="#/router/${res.router_id}" onclick="closeModal()">Terdaftar otomatis</a> (menunggu client tersambung)</td></tr>` : ''}</tbody></table>
    <h3 style="margin:16px 0 6px;font-size:14px">Script untuk router client</h3><pre class="code" id="vcs">${esc(res.client_script)}</pre><button class="btn" id="vcp">Salin script</button></div></div>`);
  copyBtn('#vcp', res.client_script);
}

function openVpnUserForm(sid, profiles, defProfile, done) {
  openModal(`<form class="sheet" id="uf"><header><h2>Tambah user VPN</h2><button type="button" class="btn ghost sm" id="ufX">✕</button></header>
  <div class="body">
    <label>Username<input name="username" required pattern="[A-Za-z0-9_.@\\-]{1,40}" placeholder="budi_vpn" autocomplete="off"></label>
    <label>Password<input name="password" class="mono" placeholder="kosong = dibuat acak" autocomplete="off"></label>
    <label>Profile<select name="profile">${(profiles.length ? profiles : [defProfile]).map(p => `<option ${p === defProfile ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></label>
    <label>Grup monitoring<input name="group_name" placeholder="otomatis: VPN + nama server"></label>
    <label class="chk full"><input type="checkbox" name="monitor" checked> Daftarkan ke monitoring otomatis <span class="hint" style="margin-left:6px">(script client ikut membuat user read-only <b>monitor</b>)</span></label>
  </div><footer><button type="button" class="btn" id="ufCancel">Batal</button><button class="btn primary" type="submit">Buat user</button></footer></form>`);
  const f = $('#uf'); $('#ufX').onclick = $('#ufCancel').onclick = closeModal;
  f.onsubmit = async ev => {
    ev.preventDefault(); const b = f.querySelector('.primary'); b.disabled = true;
    try { const r = await api(`/vpn/servers/${sid}/users`, { method: 'POST', body: { username: f.username.value, password: f.password.value, profile: f.profile.value, monitor: f.monitor.checked, group_name: f.group_name.value } }); showVpnResult(r); done && done(); }
    catch (x) { toast(x.message, 'bad'); b.disabled = false; }
  };
}

async function pageVpn() {
  const servers = await api('/vpn/servers');
  if (!servers.length) {
    $('#main').innerHTML = `<div class="page-head"><div><h1>VPN</h1><div class="sub">Kelola user L2TP + port-forward di MikroTik server VPN.</div></div></div>
    <section class="panel"><div class="empty">Belum ada server VPN. <button class="btn primary" id="vAdd" style="margin-left:8px">Tambah server VPN</button></div></section>`;
    $('#vAdd').onclick = () => openVpnServerForm(null, route); return;
  }
  if (!servers.find(s => s.id === vpnSid)) vpnSid = servers[0].id;
  $('#main').innerHTML = `<div class="page-head"><div><h1>VPN</h1><div class="sub">User L2TP, port-forward otomatis, dan script client.</div></div>
    <div class="row"><select id="vSel">${servers.map(s => `<option value="${s.id}" ${s.id === vpnSid ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select>
    <button class="btn" id="vDiag">Diagnosa</button><button class="btn" id="vEdit">Server</button><button class="btn" id="vAddSrv">+ Server</button><button class="btn primary" id="vAddUser">Tambah user</button></div></div>
    <div id="vSum" class="kpis"></div><div class="filters"><input id="vQ" type="search" placeholder="Cari user / IP"></div>
    <section class="panel"><div class="tbl-wrap"><table class="tbl" id="vT"><tbody><tr><td class="empty">Memuat dari router…</td></tr></tbody></table></div></section>`;
  let data = null;
  const draw = () => {
    const q = $('#vQ').value.toLowerCase(), us = data.users.filter(u => !q || (u.name + u.address).toLowerCase().includes(q));
    $('#vSum').innerHTML = `<div class="panel kpi"><div class="l">User VPN</div><div class="v">${data.summary.total}</div></div><div class="panel kpi"><div class="l">Online</div><div class="v" style="color:var(--ok)">${data.summary.online}</div></div><div class="panel kpi"><div class="l">Nonaktif</div><div class="v">${data.summary.disabled}</div></div>
      ${data.orphan_nat ? `<div class="panel kpi"><div class="l">NAT yatim</div><div class="v" style="color:var(--warn)">${data.orphan_nat}</div><div class="s"><button class="btn sm" id="vClean">Bersihkan</button></div></div>` : ''}`;
    const c = $('#vClean'); if (c) c.onclick = async () => { const j = await api(`/vpn/servers/${vpnSid}/cleanup`, { method: 'POST' }); toast(`${j.removed} NAT rule dihapus`, 'ok'); load(); };
    $('#vT').innerHTML = `<thead><tr><th>User</th><th>Status</th><th>IP VPN</th><th>Akses (port publik)</th><th>RX / TX</th><th>Monitor</th><th></th></tr></thead><tbody>${us.map(u => `<tr>
      <td><b>${esc(u.name)}</b><div class="dim" style="font-size:12px">${esc(u.profile)}</div></td>
      <td>${u.disabled ? '<span class="pill">Nonaktif</span>' : u.online ? `<span class="pill ok"><i class="dot"></i>Online</span><div class="dim" style="font-size:12px">${esc(u.uptime || '')}</div>` : '<span class="pill warn">Offline</span>'}</td>
      <td class="mono">${esc(u.address) || '–'}</td>
      <td class="mono" style="font-size:12px">${u.access.map(a => `${esc(a.name)} <b>${esc(a.host)}:${esc(a.port)}</b>`).join('<br>') || '<span class="dim">–</span>'}</td>
      <td class="dim mono" style="font-size:12px">${u.rx == null ? '–' : fmtBytes(u.rx) + ' / ' + fmtBytes(u.tx)}</td>
      <td>${u.router_id ? `<a href="#/router/${u.router_id}">Lihat</a>` : '<span class="dim">–</span>'}</td>
      <td class="r" style="white-space:nowrap"><button class="btn sm" data-sc="${esc(u.id)}">Script</button> <button class="btn sm" data-tg="${esc(u.id)}" data-d="${u.disabled ? 0 : 1}">${u.disabled ? 'Aktifkan' : 'Nonaktifkan'}</button> <button class="btn sm danger" data-del="${esc(u.id)}" data-n="${esc(u.name)}" data-m="${u.router_id ? 1 : 0}">Hapus</button></td></tr>`).join('') || '<tr><td colspan="7" class="empty">Belum ada user.</td></tr>'}</tbody>`;
    $$('[data-sc]').forEach(b => b.onclick = async () => { try { const j = await api(`/vpn/servers/${vpnSid}/users/${encodeURIComponent(b.dataset.sc)}/script`); openModal(`<div class="sheet"><header><h2>Script client</h2><button class="btn ghost sm" onclick="closeModal()">✕</button></header><div class="pad"><pre class="code">${esc(j.script)}</pre><button class="btn" id="sp">Salin</button></div></div>`); copyBtn('#sp', j.script); } catch (x) { toast(x.message, 'bad'); } });
    $$('[data-tg]').forEach(b => b.onclick = async () => { try { await api(`/vpn/servers/${vpnSid}/users/${encodeURIComponent(b.dataset.tg)}/toggle`, { method: 'POST', body: { disabled: b.dataset.d === '1' } }); load(); } catch (x) { toast(x.message, 'bad'); } });
    $$('[data-del]').forEach(b => b.onclick = async () => {
      if (!confirm(`Hapus user VPN "${b.dataset.n}" beserta NAT rule-nya?` + (b.dataset.m === '1' ? '\nRouter ini juga dihapus dari monitoring.' : ''))) return;
      try { await api(`/vpn/servers/${vpnSid}/users/${encodeURIComponent(b.dataset.del)}`, { method: 'DELETE' }); toast('User dihapus', 'ok'); load(); } catch (x) { toast(x.message, 'bad'); } });
  };
  const load = async () => {
    try { data = await api(`/vpn/servers/${vpnSid}/users`); draw(); }
    catch (x) { $('#vT').innerHTML = `<tbody><tr><td class="empty">Gagal konek ke server VPN: ${esc(x.message)}</td></tr></tbody>`; $('#vSum').innerHTML = ''; }
  };
  $('#vSel').onchange = e => { vpnSid = +e.target.value; $('#vT').innerHTML = '<tbody><tr><td class="empty">Memuat…</td></tr></tbody>'; load(); };
  $('#vQ').oninput = () => data && draw();
  $('#vDiag').onclick = () => openDiag(`/vpn/servers/${vpnSid}/diag`, servers.find(s => s.id === vpnSid).name);
  $('#vEdit').onclick = () => openVpnServerForm(servers.find(s => s.id === vpnSid), route);
  $('#vAddSrv').onclick = () => openVpnServerForm(null, route);
  $('#vAddUser').onclick = () => data ? openVpnUserForm(vpnSid, data.profiles, data.server.profile, load) : toast('Tunggu data server termuat');
  await load(); refresh = load;
}

/* ---------- network latency (ala WiFiman) ---------- */
const latErr = (err, loss) => err === 'dns' ? 'DNS gagal' : (err === 'timeout' || loss >= 100) ? 'timeout' : err ? 'error' : '?';
const latColor = (ms, loss, w, b) => ms == null ? (loss >= 100 ? 'var(--bad)' : 'var(--tx2)') : ms >= b ? 'var(--bad)' : ms >= w ? 'var(--warn)' : 'var(--ok)';
const LAT_BRAND = { 'google': ['#4285f4', 'G'], 'youtube': ['#ff0033', '▶'], 'mobile legends': ['#233b8f', 'ML'], 'pubg mobile': ['#e8a317', 'P'], 'pubg': ['#e8a317', 'P'],
  'instagram': ['#d6249f', 'IG'], 'facebook': ['#1877f2', 'f'], 'x': ['#000', '𝕏'], 'tiktok': ['#111', '♪'], 'whatsapp': ['#25d366', 'W'], 'free fire': ['#f26b21', 'FF'] };
function latIcon(name) {
  if (name === 'Gateway') return '<span class="lat-ic" style="background:var(--raise);color:var(--tx2)"><svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2 12h16v5H2zM5 14.5h.01M8 14.5h.01M6 12V8M14 12V8M3.5 6a9 9 0 0 1 13 0"/></svg></span>';
  const b = LAT_BRAND[name.toLowerCase()] || ['#566470', name.slice(0, 1).toUpperCase()];
  return `<span class="lat-ic" style="background:${b[0]}">${esc(b[1])}</span>`;
}
function latSpark(t, v, color, bucket) {
  const W = 150, H = 34, pts = t.map((x, i) => [x, v[i]]).filter(p => p[1] != null);
  if (pts.length < 2) return `<svg class="lat-sp" viewBox="0 0 ${W} ${H}"></svg>`;
  const t0 = t[0], t1 = t[t.length - 1] || t0 + 1, mx = Math.max(...pts.map(p => p[1]), 1), mn = Math.min(...pts.map(p => p[1]));
  const sp = Math.max(mx - mn, mx * 0.25, 1), lo = Math.max(0, mn - sp * 0.15);
  let d = '', prev = null;
  pts.forEach(([x, y]) => { const px = ((x - t0) / Math.max(1, t1 - t0) * W).toFixed(1), py = (H - 3 - (y - lo) / (mx - lo || 1) * (H - 6)).toFixed(1); d += (prev !== null && x - prev <= bucket * 2.5 ? 'L' : 'M') + px + ',' + py; prev = x; });
  return `<svg class="lat-sp" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
}
function latRows(j) {
  return j.targets.map(t => {
    const col = latColor(t.ms, t.loss, j.warn, j.bad);
    const val = !t.fresh ? '<span class="dim">–</span>' : t.ms == null ? `<span style="color:var(--bad);font-size:15px">${latErr(t.err, t.loss)}</span>` : `${num(t.ms, t.ms < 10 ? 1 : 0)}<small> ms</small>`;
    return `<div class="lat-row" title="${esc(t.err ? 'Penyebab: ' + t.err + ' · ' : '')}rata-rata ${num(t.avg, 0)} ms · min ${num(t.min, 0)} · maks ${num(t.max, 0)}">${latIcon(t.name)}<div class="lat-n"><b>${esc(t.name)}</b><span class="mono dim">${esc(t.ip || t.host || '–')}</span></div>
      ${latSpark(t.t, t.v, 'var(--tx2)', j.bucket)}<div class="lat-v" style="color:${col}">${val}${t.fresh && t.loss > 0 && t.loss < 100 ? `<div class="lat-loss">loss ${num(t.loss, 0)}%</div>` : ''}</div></div>`;
  }).join('');
}
async function pageLatency() {
  $('#main').innerHTML = `<div class="page-head"><div><h1>Network Latency</h1><div class="sub">Ping dari tiap router ke internet &amp; gateway-nya — kualitas koneksi di setiap lokasi.</div></div><div class="hint" id="lHint"></div></div>
  <section class="panel"><div class="tbl-wrap"><table class="tbl lat-tbl" id="lT"></table></div></section>`;
  const draw = async () => {
    const j = await api('/latency/overview');
    $('#lHint').textContent = j.enabled ? `Hijau < ${j.warn} ms · oranye < ${j.bad} ms · merah di atasnya` : 'Pemantauan latency dimatikan (Pengaturan)';
    $('#lT').innerHTML = `<thead><tr><th>Router</th>${j.targets.map(t => `<th class="r">${esc(t)}</th>`).join('')}</tr></thead><tbody>${j.routers.filter(r => r.enabled).map(r => `<tr><td><a href="#/router/${r.id}"><b>${esc(r.name)}</b></a>${r.group_name ? `<div class="dim" style="font-size:12px">${esc(r.group_name)}</div>` : ''}${r.error ? `<div style="font-size:12px;color:var(--warn)" title="${esc(r.error)}">⚠ ${esc(r.error.slice(0, 60))}</div>` : ''}</td>
      ${j.targets.map(t => { const c = r.cells[t]; if (!r.online) return '<td class="r dim">–</td>'; if (!c) return '<td class="r dim">–</td>'; return `<td class="r mono" style="color:${latColor(c.ms, c.loss, j.warn, j.bad)};font-weight:600">${c.ms == null ? `<span title="${esc(c.err || '')}">${latErr(c.err, c.loss)}</span>` : num(c.ms, c.ms < 10 ? 1 : 0)}</td>`; }).join('')}</tr>`).join('') || `<tr><td colspan="${j.targets.length + 1}" class="empty">Belum ada router.</td></tr>`}</tbody>`;
  };
  await draw(); refresh = draw;
}

/* ---------- diagnosa: ping / traceroute / log ---------- */
function openDiag(base, title) {
  let tab = 'ping';
  openModal(`<div class="sheet" style="max-width:820px"><header><h2>Diagnosa · ${esc(title)}</h2><button class="btn ghost sm" onclick="closeModal()">✕</button></header>
  <div class="pad"><div class="seg" id="dgTabs" style="margin-bottom:12px"><button data-t="ping" class="on">Ping</button><button data-t="traceroute">Traceroute</button><button data-t="log">Log router</button></div>
  <div id="dgBody"></div></div></div>`);
  const body = $('#dgBody');
  const spin = t => `<div class="hint" style="margin-top:10px">${t}</div>`;
  const target = (extra) => `<form id="dgF" class="row" style="gap:8px;flex-wrap:wrap;align-items:end"><label style="flex:1;min-width:200px">Alamat tujuan<input name="addr" class="mono" placeholder="8.8.8.8 atau google.com" value="${esc(S.dgAddr || '')}" required autocomplete="off"></label>${extra}<button class="btn primary" type="submit">Jalankan</button></form>
    <div style="margin-top:8px">${['8.8.8.8', '1.1.1.1', 'google.com'].map(a => `<span class="chip" data-a="${a}">${a}</span>`).join('')}</div><div id="dgOut" style="margin-top:12px"></div>`;
  const chips = () => $$('[data-a]', body).forEach(c => c.onclick = () => { body.querySelector('[name=addr]').value = c.dataset.a; });
  const err = (out, x) => { out.innerHTML = `<div class="testres bad">${esc(x.message)}</div>`; };
  const view = () => {
    $$('#dgTabs button').forEach(b => b.classList.toggle('on', b.dataset.t === tab));
    if (tab === 'log') return viewLog();
    const isPing = tab === 'ping';
    body.innerHTML = target(isPing ? '<label style="width:90px">Jumlah<input name="count" type="number" min="1" max="10" value="4"></label>' : '');
    chips();
    $('#dgF').onsubmit = async ev => {
      ev.preventDefault(); const f = ev.target, out = $('#dgOut'), btn = f.querySelector('.primary'); S.dgAddr = f.addr.value.trim();
      btn.disabled = true; out.innerHTML = spin(isPing ? 'Mengirim ping dari router…' : 'Melacak rute dari router (bisa sampai ±30 detik)…');
      try {
        const j = await api(base + '/' + tab, { method: 'POST', body: isPing ? { address: S.dgAddr, count: +f.count.value || 4 } : { address: S.dgAddr } });
        out.innerHTML = isPing ? `<div class="stat-line" style="margin-bottom:8px"><span>terkirim <b>${j.sent}</b></span><span>diterima <b>${j.received}</b></span><span>loss <b style="color:${j.loss ? 'var(--bad)' : 'var(--ok)'}">${num(j.loss)}%</b></span><span>min/avg/max <b>${num(j.min, 1)} / ${num(j.avg, 1)} / ${num(j.max, 1)} ms</b></span></div>
          <table class="tbl"><thead><tr><th>#</th><th>Dari</th><th>Waktu</th><th>TTL</th></tr></thead><tbody>${j.replies.map(r => `<tr><td>${r.seq}</td><td class="mono">${esc(r.host)}</td><td>${r.ok ? (r.ms == null ? esc(r.raw || '?') : num(r.ms, 1) + ' ms') : `<span style="color:var(--bad)">${esc(r.status || 'timeout')}</span>`}</td><td>${esc(r.ttl || '–')}</td></tr>`).join('')}</tbody></table>`
          : `<div class="${j.reached ? '' : 'hint'}" style="margin-bottom:8px">${j.reached ? '<span class="pill ok">Tujuan tercapai</span>' : 'Tujuan belum tercapai dalam batas hop (mungkin ICMP diblokir di ujung).'}</div>
          <table class="tbl"><thead><tr><th>Hop</th><th>Alamat</th><th>Loss</th><th>Last</th><th>Avg</th><th>Best</th><th>Worst</th></tr></thead><tbody>${j.hops.map(h => `<tr><td>${h.hop}</td><td class="mono">${esc(h.address)}</td><td>${h.loss == null ? '–' : num(h.loss) + '%'}</td><td>${h.last == null ? '–' : num(h.last, 1)}</td><td>${h.avg == null ? '–' : num(h.avg, 1)}</td><td>${h.best == null ? '–' : num(h.best, 1)}</td><td>${h.worst == null ? '–' : num(h.worst, 1)}</td></tr>`).join('')}</tbody></table>`;
      } catch (x) { err(out, x); } finally { btn.disabled = false; }
    };
  };
  const viewLog = () => {
    body.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:end"><label style="width:170px">Topik<select id="lgT"><option value="">Semua</option></select></label><label style="flex:1;min-width:160px">Cari<input id="lgQ" type="search" placeholder="kata di pesan log"></label><label style="width:90px">Baris<select id="lgN"><option>100</option><option selected>200</option><option>500</option></select></label><button class="btn" id="lgR">Muat ulang</button></div><div id="dgOut" style="margin-top:12px"></div>`;
    let loaded = false;
    const load = async () => {
      const out = $('#dgOut'); out.innerHTML = spin('Mengambil log dari router…');
      try {
        const j = await api(`${base}/log?limit=${$('#lgN').value}&topic=${encodeURIComponent($('#lgT').value)}&search=${encodeURIComponent($('#lgQ').value)}`);
        if (!loaded) { loaded = true; const sel = $('#lgT'); j.topics.forEach(t => { const o = document.createElement('option'); o.textContent = t; sel.append(o); }); }
        const cls = t => /error|critical/.test(t) ? 'var(--bad)' : /warning/.test(t) ? 'var(--warn)' : '';
        out.innerHTML = `<div class="hint" style="margin-bottom:6px">${j.lines.length} dari ${j.total} baris cocok · terbaru di atas</div><div class="tbl-wrap" style="max-height:420px;overflow:auto"><table class="tbl"><tbody>${j.lines.map(l => `<tr><td class="mono dim" style="white-space:nowrap">${esc(l.time)}</td><td class="dim" style="font-size:12px">${esc(l.topics)}</td><td style="color:${cls(l.topics)}">${esc(l.message)}</td></tr>`).join('') || '<tr><td class="empty">Tidak ada baris.</td></tr>'}</tbody></table></div>`;
      } catch (x) { err(out, x); }
    };
    $('#lgR').onclick = load; $('#lgT').onchange = load; $('#lgN').onchange = load; $('#lgQ').onkeydown = e => { if (e.key === 'Enter') load(); };
    load();
  };
  $$('#dgTabs button').forEach(b => b.onclick = () => { tab = b.dataset.t; view(); });
  view();
}

/* ---------- boot ---------- */
$('#loginForm').onsubmit = async e => {
  e.preventDefault(); const f = e.target; $('#loginErr').textContent = '';
  try { await api('/login', { method: 'POST', body: { username: f.username.value, password: f.password.value } }); f.reset(); start(); }
  catch (x) { $('#loginErr').textContent = x.message; }
};
$('#logoutBtn').onclick = async () => { await api('/logout', { method: 'POST' }).catch(() => { }); showLogin(); };
$('#themeBtn').onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'), nx = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = nx; try { localStorage.setItem('theme', nx); } catch (e) { } route();
};
async function start() {
  try { const me = await api('/me'); if (!me.user) return showLogin(); } catch (e) { return showLogin(); }
  $('#login').hidden = true; $('#app').hidden = false; route();
}
start();
