'use strict';
/* ---------- Hotspot / Mikhmon online ---------- */
const rp = n => 'Rp ' + Number(n || 0).toLocaleString('id-ID');
const HS = { rid: null, tab: 'voucher', routers: [] };
try { HS.rid = +localStorage.getItem('hs_rid') || null; } catch (e) { }

function hsDownload(name, text, type = 'text/plain') {
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([text], { type: type + ';charset=utf-8' })); a.download = name;
  document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
const hsBase = () => `/hotspot/routers/${HS.rid}`;
const hsRouter = () => HS.routers.find(r => r.id === HS.rid);

async function pageHotspot() {
  HS.routers = await api('/hotspot/routers');
  const admin = S.role === 'admin';
  if (!HS.routers.length) {
    $('#main').innerHTML = `<div class="page-head"><div><h1>Hotspot</h1></div></div><div class="empty panel">${admin ? 'Belum ada router.' : 'Belum ada router hotspot yang diaktifkan untuk akun Anda. Hubungi penyedia layanan.'}</div>`; return;
  }
  if (!HS.routers.some(r => r.id === HS.rid)) HS.rid = (HS.routers.find(r => r.hs_enabled) || HS.routers[0]).id;
  try { localStorage.setItem('hs_rid', HS.rid); } catch (e) { }
  const r = hsRouter();
  const tabs = [['voucher', 'Voucher'], ['users', 'User'], ['profiles', 'Profile'], ['sales', 'Laporan'], ['login', 'Halaman login']].concat(admin ? [['config', 'Pengaturan']] : []);
  if (!r.hs_enabled && HS.tab !== 'config') HS.tab = 'config';
  $('#main').innerHTML = `<div class="page-head"><div><h1>Hotspot</h1><div class="sub">Voucher, user, profile dan laporan penjualan hotspot MikroTik.</div></div>
    <div class="row"><select id="hsRouter" style="width:auto">${HS.routers.map(x => `<option value="${x.id}" ${x.id === HS.rid ? 'selected' : ''}>${esc(x.name)}${x.hs_enabled ? '' : ' (belum aktif)'}</option>`).join('')}</select></div></div>
    <div class="tabs" id="hsTabs">${tabs.map(([k, v]) => `<button class="tab ${HS.tab === k ? 'on' : ''}" data-t="${k}" ${!r.hs_enabled && k !== 'config' ? 'disabled' : ''}>${v}</button>`).join('')}</div>
    <div id="hsBody"></div>`;
  $('#hsRouter').onchange = e => { HS.rid = +e.target.value; HS.tab = 'voucher'; pageHotspot(); };
  $$('#hsTabs .tab').forEach(b => b.onclick = () => { HS.tab = b.dataset.t; pageHotspot(); });
  const fn = { voucher: hsVoucher, users: hsUsers, profiles: hsProfiles, sales: hsSales, login: hsLogin, config: hsConfig }[HS.tab];
  const body = $('#hsBody');
  try { await fn(body); } catch (e) { if (document.body.contains(body)) body.innerHTML = `<div class="lat-err">${esc(e.message)}</div>`; }   // abaikan bila halaman sudah berpindah
}

/* ---------- Voucher ---------- */
async function hsVoucher(el) {
  const [pj, batches] = await Promise.all([api(hsBase() + '/profiles'), api(hsBase() + '/batches')]);
  const profs = pj.profiles;
  el.innerHTML = `<section class="panel" style="margin-bottom:12px"><header><h2>Buat voucher</h2></header>
  ${profs.length ? '' : '<div class="lat-err" style="margin:12px">Belum ada profile. Buat dulu di tab Profile (harga, masa aktif, kecepatan).</div>'}
  <form id="vf" class="pad" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px">
    <label>Profile<select name="profile" required>${profs.map(p => `<option value="${esc(p.name)}">${esc(p.name)} · ${rp(p.price)}${p.validity ? ' · ' + esc(p.validity) : ''}</option>`).join('')}</select></label>
    <label>Jumlah<input name="qty" type="number" min="1" max="500" value="10" required></label>
    <label>Tipe<select name="mode"><option value="vc">Kode saja (user = password)</option><option value="up">User + password</option></select></label>
    <label>Panjang kode<input name="length" type="number" min="4" max="12" value="6"></label>
    <label>Karakter<select name="charset"><option value="num">Angka</option><option value="low">Huruf kecil + angka</option><option value="alnum">Huruf besar + angka</option></select></label>
    <label>Prefix<input name="prefix" maxlength="8" placeholder="opsional, mis. WF" class="mono"></label>
    <label>Server hotspot<select name="server"><option value="all">Semua</option>${pj.servers.map(s => `<option>${esc(s)}</option>`).join('')}</select></label>
    <label>Catatan batch<input name="note" maxlength="40" placeholder="mis. Pagi, Event"></label>
    <label>Batas waktu pakai<input name="limit_uptime" class="mono" placeholder="opsional: 2h, 1d"><span class="hint">Total waktu online (uptime), bukan masa aktif.</span></label>
    <label>Kuota (MB)<input name="limit_mb" type="number" min="0" value="0"><span class="hint">0 = tanpa batas</span></label>
    <div style="align-self:end"><button class="btn primary" type="submit" ${profs.length ? '' : 'disabled'}>Buat voucher</button></div>
  </form></section>
  <section class="panel"><header><h2>Batch voucher</h2></header><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Batch</th><th>Profile</th><th>Harga</th><th>Dibuat</th><th>Jumlah</th><th>Terpakai</th><th></th></tr></thead><tbody>
  ${batches.map(b => `<tr><td class="mono" style="font-size:12px">${esc(b.batch)}</td><td>${esc(b.profile)}</td><td>${rp(b.price)}</td><td class="dim">${fmtDT(b.created_at)}${b.created_by ? '<div style="font-size:12px">' + esc(b.created_by) + '</div>' : ''}</td>
    <td>${b.total}</td><td>${b.used} <span class="dim">(${b.total ? Math.round(100 * b.used / b.total) : 0}%)</span></td>
    <td class="r" style="white-space:nowrap"><button class="btn sm" data-p="${esc(b.batch)}">Cetak</button> <button class="btn sm" data-c="${esc(b.batch)}">CSV</button> <button class="btn sm danger" data-d="${esc(b.batch)}">Hapus sisa</button></td></tr>`).join('') || '<tr><td colspan="7" class="empty">Belum ada voucher dibuat lewat Pantau.</td></tr>'}</tbody></table></div></section>`;
  $('#vf').onsubmit = async ev => {
    ev.preventDefault(); const f = ev.target, btn = f.querySelector('button[type=submit]'); btn.disabled = true; btn.textContent = 'Membuat…';
    const body = { profile: f.profile.value, qty: +f.qty.value, mode: f.mode.value, length: +f.length.value, charset: f.charset.value, prefix: f.prefix.value,
      server: f.server.value, note: f.note.value, limit_uptime: f.limit_uptime.value.trim(), limit_mb: +f.limit_mb.value || 0 };
    try { const j = await api(hsBase() + '/vouchers', { method: 'POST', body }); toast(`${j.count} voucher dibuat`, 'ok'); hsVoucherResult(j, body.mode); pageHotspot(); }
    catch (x) { toast(x.message, 'bad'); btn.disabled = false; btn.textContent = 'Buat voucher'; }
  };
  const loadBatch = async b => { const j = await api(`${hsBase()}/batches/${encodeURIComponent(b)}`); return j; };
  $$('[data-p]', el).forEach(b => b.onclick = async () => { try { const j = await loadBatch(b.dataset.p); hsPrint(j.vouchers.filter(v => !v.removed), j); } catch (x) { toast(x.message, 'bad'); } });
  $$('[data-c]', el).forEach(b => b.onclick = async () => { try { const j = await loadBatch(b.dataset.c); hsCsv(j.vouchers, b.dataset.c); } catch (x) { toast(x.message, 'bad'); } });
  $$('[data-d]', el).forEach(b => b.onclick = async () => {
    try {
      const j = await loadBatch(b.dataset.d), left = j.vouchers.filter(v => !v.used_at && !v.removed).map(v => v.username);
      if (!left.length) return toast('Tidak ada voucher yang belum dipakai di batch ini');
      if (!confirm(`Hapus ${left.length} voucher yang belum dipakai dari router?`)) return;
      await api(hsBase() + '/users/remove', { method: 'POST', body: { names: left } }); toast('Voucher dihapus', 'ok'); pageHotspot();
    } catch (x) { toast(x.message, 'bad'); }
  });
}
function hsCsv(vs, name) {
  hsDownload(`voucher-${name}.csv`, 'username,password,profile,harga\n' + vs.map(v => [v.username, v.password, v.profile, v.price].map(x => `"${String(x ?? '').replace(/"/g, '""')}"`).join(',')).join('\n'), 'text/csv');
}
function hsVoucherResult(j, mode) {
  const ctx = { router_dns: j.router_dns, brand: document.title };
  const vs = j.vouchers.map(v => ({ ...v, profile: j.profile, price: j.price, validity: j.validity }));
  openModal(`<div class="sheet" style="max-width:640px"><header><h2>${j.count} voucher dibuat</h2><button class="btn ghost sm" onclick="closeModal()">✕</button></header><div class="pad">
    <p class="muted" style="margin-top:0">Profile <b>${esc(j.profile)}</b> · ${rp(j.price)}${j.validity ? ' · masa aktif ' + esc(j.validity) : ''} · batch <span class="mono">${esc(j.batch)}</span></p>
    <pre class="code" style="max-height:260px;overflow:auto">${esc(vs.map(v => mode === 'up' ? `${v.username}  ${v.password}` : v.username).join('\n'))}</pre>
    <div class="row"><button class="btn primary" id="vrP">Cetak</button><button class="btn" id="vrC">Unduh CSV</button><button class="btn" id="vrK">Salin</button></div></div></div>`);
  $('#vrP').onclick = () => hsPrint(vs, ctx); $('#vrC').onclick = () => hsCsv(vs, j.batch);
  copyBtn('#vrK', vs.map(v => mode === 'up' ? `${v.username} ${v.password}` : v.username).join('\n'));
}
function hsPrint(vs, ctx) {
  if (!vs.length) return toast('Tidak ada voucher untuk dicetak');
  const up = vs.some(v => v.username !== v.password), brand = esc(ctx.brand || ''), url = ctx.router_dns ? esc(ctx.router_dns) : '';
  const cards = vs.map(v => `<div class="vc"><div class="vc-h"><b>${brand}</b><span>${rp(v.price)}</span></div>
    ${up ? `<div class="vc-r"><small>User</small><code>${esc(v.username)}</code></div><div class="vc-r"><small>Password</small><code>${esc(v.password)}</code></div>` : `<div class="vc-r"><small>Kode voucher</small><code class="big">${esc(v.username)}</code></div>`}
    <div class="vc-f">${esc(v.profile)}${v.validity ? ' · ' + esc(v.validity) : ''}${url ? ' · ' + url : ''}</div></div>`).join('');
  let pa = $('#printArea'); if (!pa) { pa = document.createElement('div'); pa.id = 'printArea'; document.body.append(pa); }
  pa.innerHTML = `<div class="vc-grid">${cards}</div>`;
  document.body.classList.add('printing'); closeModal();
  setTimeout(() => { window.print(); document.body.classList.remove('printing'); pa.innerHTML = ''; }, 150);
}

/* ---------- User ---------- */
async function hsUsers(el) {
  el.innerHTML = `<section class="panel"><header><h2>User hotspot <span class="hint" id="huC"></span></h2><div class="row"><input id="huQ" placeholder="Cari nama / profile / catatan" style="min-width:220px"><button class="btn sm" id="huR">Muat ulang</button></div></header>
  <div class="tbl-wrap"><table class="tbl" id="huT"></table></div></section>`;
  const load = async () => {
    const j = await api(hsBase() + '/users?q=' + encodeURIComponent($('#huQ').value));
    $('#huC').textContent = `· ${j.total} user, ${j.online} online` + (j.truncated ? ' (menampilkan 1000 pertama)' : '');
    $('#huT').innerHTML = `<thead><tr><th>User</th><th>Profile</th><th>Uptime</th><th>Traffic</th><th>Status</th><th></th></tr></thead><tbody>${j.users.map(u => `<tr>
      <td><span class="mono">${esc(u.name)}</span>${u.comment ? `<div class="dim" style="font-size:12px">${esc(u.comment)}</div>` : ''}</td><td>${esc(u.profile)}</td><td class="mono">${esc(u.uptime || '0s')}</td>
      <td class="dim mono" style="font-size:12px">${fmtBytes(u.bytes_in)} / ${fmtBytes(u.bytes_out)}</td>
      <td>${u.disabled ? '<span class="pill warn">Nonaktif</span>' : u.online ? `<span class="pill ok">Online</span><div class="dim mono" style="font-size:12px">${esc(u.address)}</div>` : u.used_at ? '<span class="pill">Terpakai</span>' : '<span class="pill">Belum dipakai</span>'}${u.expires_at ? `<div class="dim" style="font-size:12px">habis ${fmtDT(u.expires_at)}</div>` : ''}</td>
      <td class="r" style="white-space:nowrap">${u.online ? `<button class="btn sm" data-a="kick" data-i="${esc(u.id)}">Putus</button> ` : ''}<button class="btn sm" data-a="${u.disabled ? 'enable' : 'disable'}" data-i="${esc(u.id)}">${u.disabled ? 'Aktifkan' : 'Nonaktifkan'}</button> <button class="btn sm danger" data-a="remove" data-i="${esc(u.id)}" data-n="${esc(u.name)}">Hapus</button></td></tr>`).join('') || '<tr><td colspan="6" class="empty">Tidak ada user.</td></tr>'}</tbody>`;
    $$('[data-a]', el).forEach(b => b.onclick = async () => {
      if (b.dataset.a === 'remove' && !confirm(`Hapus user "${b.dataset.n}"?`)) return;
      try { await api(`${hsBase()}/users/${encodeURIComponent(b.dataset.i)}/action`, { method: 'POST', body: { action: b.dataset.a } }); toast('Berhasil', 'ok'); load(); } catch (x) { toast(x.message, 'bad'); }
    });
  };
  let tm; $('#huQ').oninput = () => { clearTimeout(tm); tm = setTimeout(() => load().catch(x => toast(x.message, 'bad')), 350); };
  $('#huR').onclick = () => load().catch(x => toast(x.message, 'bad'));
  await load();
}

/* ---------- Profile ---------- */
function hsProfileForm(p) {
  const e = !!p; p = p || { shared_users: '1', price: 0 };
  openModal(`<form class="sheet" id="pf2"><header><h2>${e ? 'Edit profile' : 'Profile baru'}</h2><button type="button" class="btn ghost sm" id="pX">✕</button></header><div class="body">
    <label>Nama profile<input name="name" value="${esc(p.name || '')}" required maxlength="40" placeholder="1jam"></label>
    <label>Harga (Rp)<input name="price" type="number" min="0" value="${p.price || 0}"></label>
    <label>Masa aktif<input name="validity" class="mono" value="${esc(p.validity || '')}" placeholder="1d, 3h, 30m"><span class="hint">Dihitung sejak pertama dipakai; voucher dihapus otomatis saat habis. Kosong = tanpa batas.</span></label>
    <label>Kecepatan (rate limit)<input name="rate_limit" class="mono" value="${esc(p.rate_limit || '')}" placeholder="2M/5M (upload/download)"></label>
    <label>Perangkat per voucher<input name="shared_users" type="number" min="1" max="99" value="${esc(p.shared_users || '1')}"></label>
    <label>Session timeout<input name="session_timeout" class="mono" value="${esc(p.session_timeout && p.session_timeout !== '0s' ? p.session_timeout : '')}" placeholder="opsional"></label>
    <label>Idle timeout<input name="idle_timeout" class="mono" value="${esc(p.idle_timeout && p.idle_timeout !== 'none' ? p.idle_timeout : '')}" placeholder="opsional, mis. 10m"></label>
  </div><footer><button type="button" class="btn" id="pC">Batal</button><button class="btn primary" type="submit">Simpan</button></footer></form>`);
  $('#pX').onclick = $('#pC').onclick = closeModal;
  $('#pf2').onsubmit = async ev => {
    ev.preventDefault(); const f = ev.target;
    const body = { name: f.name.value.trim(), price: +f.price.value || 0, validity: f.validity.value.trim(), rate_limit: f.rate_limit.value.trim(), shared_users: f.shared_users.value, session_timeout: f.session_timeout.value.trim(), idle_timeout: f.idle_timeout.value.trim() };
    try { await api(e ? `${hsBase()}/profiles/${encodeURIComponent(p.id)}` : hsBase() + '/profiles', { method: e ? 'PUT' : 'POST', body }); closeModal(); toast('Profile disimpan', 'ok'); pageHotspot(); } catch (x) { toast(x.message, 'bad'); }
  };
}
async function hsProfiles(el) {
  const pj = await api(hsBase() + '/profiles');
  el.innerHTML = `<section class="panel"><header><h2>Profile</h2><button class="btn primary sm" id="hpA">Profile baru</button></header><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Nama</th><th>Harga</th><th>Masa aktif</th><th>Kecepatan</th><th>Perangkat</th><th></th></tr></thead><tbody>
  ${pj.profiles.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${rp(p.price)}</td><td class="mono">${esc(p.validity) || '<span class="dim">–</span>'}</td><td class="mono">${esc(p.rate_limit) || '<span class="dim">tanpa batas</span>'}</td><td>${esc(p.shared_users || '1')}</td>
  <td class="r" style="white-space:nowrap"><button class="btn sm" data-e="${esc(p.id)}">Edit</button>${p.name === 'default' ? '' : ` <button class="btn sm danger" data-d="${esc(p.id)}" data-n="${esc(p.name)}">Hapus</button>`}</td></tr>`).join('')}</tbody></table></div></section>
  <p class="hint" style="margin:8px 4px">Harga dan masa aktif disimpan di Pantau (dipakai laporan penjualan dan penghapusan otomatis). Kecepatan dan perangkat disimpan di router.</p>`;
  $('#hpA').onclick = () => hsProfileForm(null);
  $$('[data-e]', el).forEach(b => b.onclick = () => hsProfileForm(pj.profiles.find(x => x.id === b.dataset.e)));
  $$('[data-d]', el).forEach(b => b.onclick = async () => { if (!confirm(`Hapus profile "${b.dataset.n}"?`)) return; try { await api(`${hsBase()}/profiles/${encodeURIComponent(b.dataset.d)}`, { method: 'DELETE' }); toast('Dihapus', 'ok'); pageHotspot(); } catch (x) { toast(x.message, 'bad'); } });
}

/* ---------- Laporan ---------- */
async function hsSales(el) {
  const days = +(HS.days || 30), scope = HS.all ? 'all' : String(HS.rid);
  const j = await api(`/hotspot/sales?rid=${scope}&days=${days}`);
  const max = Math.max(1, ...j.by_day.map(d => d.amount));
  el.innerHTML = `<div class="row" style="margin-bottom:12px"><select id="sD" style="width:auto">${[7, 30, 90, 365].map(d => `<option value="${d}" ${d === days ? 'selected' : ''}>${d} hari terakhir</option>`).join('')}</select>
    ${HS.routers.length > 1 ? `<label class="chk"><input type="checkbox" id="sA" ${HS.all ? 'checked' : ''}> Gabungkan semua router</label>` : ''}<button class="btn sm" id="sS">Sinkron sekarang</button></div>
  <div class="cards3"><div class="panel pad"><div class="muted">Omzet</div><div style="font:700 26px var(--mono)">${rp(j.amount)}</div></div>
  <div class="panel pad"><div class="muted">Voucher terjual</div><div style="font:700 26px var(--mono)">${j.count}</div></div>
  <div class="panel pad"><div class="muted">Stok belum terpakai</div><div style="font:700 26px var(--mono)">${j.stock_count}</div><div class="dim">senilai ${rp(j.stock_value)}</div></div></div>
  <section class="panel" style="margin-top:12px"><header><h2>Per hari</h2><span class="hint">${esc(j.from)} → ${esc(j.to)}</span></header><div class="pad"><div class="bars">${j.by_day.map(d => `<div class="bar" title="${esc(d.date)}: ${rp(d.amount)} (${d.count} voucher)"><i style="height:${Math.max(d.amount ? 3 : 0, Math.round(100 * d.amount / max))}%"></i></div>`).join('')}</div></div></section>
  <section class="panel" style="margin-top:12px"><header><h2>Per profile</h2></header><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Profile</th><th>Terjual</th><th>Omzet</th></tr></thead><tbody>${j.by_profile.map(p => `<tr><td>${esc(p.profile)}</td><td>${p.count}</td><td>${rp(p.amount)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty">Belum ada penjualan pada periode ini.</td></tr>'}</tbody></table></div></section>
  <p class="hint" style="margin:8px 4px">Voucher dihitung terjual saat pertama kali terlihat dipakai (Pantau memeriksa tiap ±1 menit). Voucher yang dihapus langsung dari router sebelum dipakai ikut dihitung terjual; hapus lewat Pantau agar tidak dihitung.</p>`;
  $('#sD').onchange = e => { HS.days = +e.target.value; pageHotspot(); };
  const a = $('#sA'); if (a) a.onchange = () => { HS.all = a.checked; pageHotspot(); };
  $('#sS').onclick = async () => { try { const s = await api(hsBase() + '/sync', { method: 'POST' }); toast(`Sinkron: ${s.used} terpakai baru, ${s.expired} kedaluwarsa`, 'ok'); pageHotspot(); } catch (x) { toast(x.message, 'bad'); } };
}

/* ---------- Halaman login hotspot ---------- */
async function hsLogin(el) {
  const st = HS.lp || (HS.lp = { title: (hsRouter().name || 'WiFi') + ' WiFi', subtitle: 'Masukkan kode voucher Anda', color: '#2563eb', mode: 'voucher', footer: '' });
  el.innerHTML = `<div class="two"><section class="panel"><header><h2>Desain halaman login</h2></header><form id="lf" class="pad" style="display:grid;gap:12px">
    <label>Judul<input name="title" value="${esc(st.title)}" maxlength="60"></label>
    <label>Keterangan<input name="subtitle" value="${esc(st.subtitle)}" maxlength="100"></label>
    <label>Warna<input name="color" type="color" value="${esc(st.color)}" style="width:72px;height:38px;padding:2px"></label>
    <label>Jenis login<select name="mode"><option value="voucher" ${st.mode === 'voucher' ? 'selected' : ''}>Kode voucher saja</option><option value="userpass" ${st.mode === 'userpass' ? 'selected' : ''}>Username + password</option></select></label>
    <label>Teks bawah<input name="footer" value="${esc(st.footer)}" maxlength="160" placeholder="mis. Info: 0812-xxxx"></label>
    <div class="row"><button class="btn primary" type="button" id="lD">Unduh login.html</button><button class="btn" type="button" id="lC">Salin kode</button></div>
    <p class="hint">Pasang di router: Winbox → <b>Files</b> → buka folder <span class="mono">hotspot</span> → ganti <span class="mono">login.html</span> dengan file ini (unggah/drag). Cadangkan file lama dulu. Kode voucher mode "kode saja" cocok untuk voucher bertipe user = password.</p></form></section>
  <section class="panel"><header><h2>Pratinjau</h2></header><div class="pad"><iframe id="lP" sandbox="" style="width:100%;height:440px;border:1px solid var(--line);border-radius:10px;background:#fff"></iframe></div></section></div>`;
  let html = '';
  const refresh = async () => {
    const f = $('#lf'); Object.assign(st, { title: f.title.value, subtitle: f.subtitle.value, color: f.color.value, mode: f.mode.value, footer: f.footer.value });
    const q = new URLSearchParams(st).toString();
    html = (await api(`${hsBase()}/loginpage?${q}`)).html;
    $('#lP').srcdoc = html.replace(/\$\(if chap-id\)[\s\S]*?\$\(endif\)/g, '').replace(/\$\(if error\)[\s\S]*?\$\(endif\)/g, '').replace(/\$\(if chap-id\)/g, '').replace(/\$\(endif\)/g, '').replace(/\sonSubmit="[^"]*"/i, '').replace(/\$\(link-login-only\)/g, '#').replace(/\$\([a-z-]+\)/g, '');
  };
  let tm; $('#lf').oninput = () => { clearTimeout(tm); tm = setTimeout(() => refresh().catch(x => toast(x.message, 'bad')), 300); };
  $('#lD').onclick = () => hsDownload('login.html', html, 'text/html');
  $('#lC').onclick = () => navigator.clipboard.writeText(html).then(() => toast('Disalin', 'ok'), () => toast('Salin manual dari file unduhan'));
  await refresh();
}

/* ---------- Pengaturan (admin) ---------- */
async function hsConfig(el) {
  const r = hsRouter();
  el.innerHTML = `<section class="panel" style="max-width:720px"><header><h2>Akun hotspot untuk router ini</h2></header><form id="cf" class="pad" style="display:grid;gap:12px">
    <label class="chk"><input type="checkbox" name="en" ${r.hs_enabled ? 'checked' : ''}> Aktifkan Mikhmon online untuk router <b>${esc(r.name)}</b></label>
    <label>Username akun hotspot<input name="u" class="mono" value="${esc(r.hs_user || '')}" placeholder="pantau-hs" autocomplete="off"><span class="hint">Kosong = pakai akun monitoring (biasanya read-only, tidak bisa membuat voucher).</span></label>
    <label>Password<input name="p" type="password" class="mono" autocomplete="new-password" placeholder="${r.hs_has_password ? 'kosongkan bila tidak diubah' : ''}"></label>
    <label>DNS name hotspot<input name="dns" class="mono" value="${esc(r.hs_dns || '')}" placeholder="wifi.toko.lan (tercetak di voucher)"></label>
    <div class="row"><button class="btn primary" type="submit">Simpan</button><button class="btn" type="button" id="cT">Tes koneksi &amp; hak tulis</button></div>
    <div id="cR"></div>
    <div><div style="font-weight:600;margin-bottom:6px">Buat akun di router (Terminal MikroTik)</div>
    <pre class="code">/user group add name=pantau-hotspot policy=read,write,api,test
/user add name=pantau-hs group=pantau-hotspot password=GANTI_PASSWORD_KUAT address=${esc(r.host && /^\d/.test(r.host) ? r.host.replace(/\.\d+$/, '.0/24') : '10.8.0.0/24')}</pre>
    <p class="hint">Ganti <span class="mono">address=</span> dengan alamat/subnet asal koneksi Pantau ke router. Akun ini hanya dipakai Pantau untuk voucher, user, dan profile hotspot. Tes koneksi membuat lalu menghapus satu user sementara untuk memastikan hak tulis.</p></div></form></section>`;
  $('#cf').onsubmit = async ev => {
    ev.preventDefault(); const f = ev.target;
    try { await api(`/hotspot/routers/${HS.rid}/config`, { method: 'PUT', body: { hs_enabled: f.en.checked, hs_user: f.u.value, hs_password: f.p.value || null, hs_dns: f.dns.value } }); toast('Tersimpan', 'ok'); pageHotspot(); } catch (x) { toast(x.message, 'bad'); }
  };
  $('#cT').onclick = async () => {
    $('#cR').innerHTML = '<span class="dim">Menguji…</span>';
    try { const j = await api(`/hotspot/routers/${HS.rid}/test`, { method: 'POST' }); $('#cR').innerHTML = `<div class="pill ok" style="display:inline-block">Berhasil · hak tulis OK · ${j.users} user${j.hotspot_found ? ' · server: ' + esc(j.servers.join(', ')) : ''}</div>${j.hotspot_found ? '' : '<div class="lat-err">Server hotspot belum ada di router ini (/ip hotspot setup).</div>'}`; }
    catch (x) { $('#cR').innerHTML = `<div class="lat-err">${esc(x.message)}</div>`; }
  };
}
