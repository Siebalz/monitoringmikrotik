"""Mikhmon online: voucher, user & profile hotspot MikroTik lewat RouterOS API, plus laporan penjualan.

Data router (user/profile) tetap di router; Pantau menyimpan voucher yang dibuatnya (tabel hs_vouchers)
beserta harga/masa-aktif per profile (hs_profile_meta) sebagai dasar laporan penjualan.

Definisi "terjual": voucher pertama kali terlihat dipakai (uptime/traffic > 0) atau hilang dari router
sesudah dibuat (kedaluwarsa). Masa aktif (validity) dihitung sejak pertama dipakai dan ditegakkan oleh
sinkron berkala Pantau (sync_router), bukan oleh script di router.
"""
import re, secrets, time, html, logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import ros, db
from .config import TZ_NAME
from .security import dec

log = logging.getLogger("hotspot")

PROFILE_RE = re.compile(r"^[A-Za-z0-9_.@ -]{1,40}$")
TIME_RE = re.compile(r"^(\d+[wdhms])+$")          # 30m, 1d, 1w2d, 2h30m
RATE_RE = re.compile(r"^[0-9A-Za-z/ ._-]{0,60}$")
PREFIX_RE = re.compile(r"^[A-Za-z0-9]{0,8}$")
USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,40}$")
CHARSETS = {"num": "0123456789", "low": "abcdefghjkmnpqrstuvwxyz23456789", "alnum": "ABCDEFGHJKMNPQRSTUVWXYZ23456789"}
MAX_QTY = 500


class HsError(Exception):
    pass


# ------------------------------------------------------------------ koneksi
def has_creds(r):
    return bool((r.get("hs_user") and r.get("hs_password_enc")) or (r.get("username") and r.get("password_enc")))


def connect(r, timeout=20.0):
    """Pakai akun hotspot khusus bila diisi; jika tidak, akun monitoring (biasanya read-only -> gagal saat menulis)."""
    if r.get("hs_user") and r.get("hs_password_enc"):
        user, pw = r["hs_user"], dec(r["hs_password_enc"])
    else:
        user, pw = r["username"], dec(r["password_enc"])
    return ros.connect(r["host"], r["port"], r["use_ssl"], user, pw, timeout=timeout)


def _wrap(e):
    msg = str(e)
    if "not enough permissions" in msg or "permission" in msg:
        return HsError("Akun router tidak punya hak tulis hotspot. Isi akun Hotspot (policy read,write,api) di Pengaturan Hotspot.")
    return HsError(msg)


def _secs(t):
    return ros.parse_uptime(t) if t and TIME_RE.match(t) else 0


# ------------------------------------------------------------------ profile
def _meta(rid):
    return {m["profile"]: m for m in db.q("SELECT * FROM hs_profile_meta WHERE router_id=?", (rid,))}


def list_profiles(r):
    c = connect(r)
    try:
        rows, _ = c.call("/ip/hotspot/user/profile/print")
        servers = [s.get("name") for s in _safe(c, "/ip/hotspot/print")]
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    meta = _meta(r["id"])
    out = []
    for p in rows:
        m = meta.get(p.get("name"), {})
        out.append({"id": p.get(".id"), "name": p.get("name", ""), "rate_limit": p.get("rate-limit", ""),
                    "shared_users": p.get("shared-users", ""), "session_timeout": p.get("session-timeout", ""),
                    "idle_timeout": p.get("idle-timeout", ""), "price": m.get("price", 0), "validity": m.get("validity", "")})
    out.sort(key=lambda x: x["name"].lower())
    return {"profiles": out, "servers": [s for s in servers if s]}


def _safe(c, cmd):
    try:
        return c.call(cmd)[0]
    except ros.RosError:
        return []


def _check_profile(b):
    name = (b.get("name") or "").strip()
    if not PROFILE_RE.match(name):
        raise HsError("Nama profile 1–40 karakter: huruf, angka, spasi, titik, minus, underscore.")
    rate = (b.get("rate_limit") or "").strip()
    if not RATE_RE.match(rate):
        raise HsError("Rate limit tidak valid (contoh 2M/5M).")
    shared = str(b.get("shared_users") or "1").strip()
    if not shared.isdigit() or not 1 <= int(shared) <= 99:
        raise HsError("Shared users harus 1–99.")
    for k, lbl in (("session_timeout", "Session timeout"), ("idle_timeout", "Idle timeout"), ("validity", "Masa aktif")):
        v = (b.get(k) or "").strip()
        if v and not TIME_RE.match(v):
            raise HsError(f"{lbl} tidak valid (contoh 30m, 2h, 1d, 1w).")
    try:
        price = int(b.get("price") or 0)
    except (TypeError, ValueError):
        raise HsError("Harga harus angka.")
    if price < 0:
        raise HsError("Harga tidak boleh negatif.")
    return name, rate, shared, price


def save_profile(r, b, pid=None):
    name, rate, shared, price = _check_profile(b)
    attrs = {"name": name, "shared-users": shared, "rate-limit": rate,
             "session-timeout": (b.get("session_timeout") or "").strip() or "0s",
             "idle-timeout": (b.get("idle_timeout") or "").strip() or "none"}
    c = connect(r)
    try:
        if pid:
            old, _ = c.call("/ip/hotspot/user/profile/print", queries=[f"?.id={pid}"])
            if not old:
                raise HsError("Profile tidak ditemukan.")
            c.call("/ip/hotspot/user/profile/set", {".id": pid, **attrs})
            if old[0].get("name") != name:
                db.ex("DELETE FROM hs_profile_meta WHERE router_id=? AND profile=?", (r["id"], old[0].get("name")))
        else:
            if any(p.get("name") == name for p in c.call("/ip/hotspot/user/profile/print")[0]):
                raise HsError(f"Profile '{name}' sudah ada.")
            c.call("/ip/hotspot/user/profile/add", attrs)
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    db.ex("""INSERT INTO hs_profile_meta(router_id,profile,price,validity) VALUES(?,?,?,?)
             ON CONFLICT(router_id,profile) DO UPDATE SET price=excluded.price, validity=excluded.validity""",
          (r["id"], name, price, (b.get("validity") or "").strip()))
    return name


def delete_profile(r, pid):
    c = connect(r)
    try:
        row, _ = c.call("/ip/hotspot/user/profile/print", queries=[f"?.id={pid}"])
        if not row:
            raise HsError("Profile tidak ditemukan.")
        name = row[0].get("name")
        if name == "default":
            raise HsError("Profile 'default' tidak boleh dihapus.")
        if c.call("/ip/hotspot/user/print", queries=[f"?profile={name}"])[0]:
            raise HsError(f"Profile '{name}' masih dipakai user. Hapus user/voucher-nya dulu.")
        c.call("/ip/hotspot/user/profile/remove", {".id": pid})
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    db.ex("DELETE FROM hs_profile_meta WHERE router_id=? AND profile=?", (r["id"], name))
    return name


# ------------------------------------------------------------------ user
def list_users(r, search="", limit=1000):
    c = connect(r)
    try:
        users, _ = c.call("/ip/hotspot/user/print")
        active, _ = c.call("/ip/hotspot/active/print")
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    act = {a.get("user"): a for a in active}
    vch = {v["username"]: v for v in db.q("SELECT * FROM hs_vouchers WHERE router_id=? AND removed=0", (r["id"],))}
    s = (search or "").strip().lower()
    out = []
    for u in users:
        n = u.get("name", "")
        if n == "default-trial" or (s and s not in n.lower() and s not in (u.get("comment", "") or "").lower() and s not in (u.get("profile", "") or "").lower()):
            continue
        a, v = act.get(n), vch.get(n)
        out.append({"id": u.get(".id"), "name": n, "profile": u.get("profile", ""), "uptime": u.get("uptime", ""),
                    "bytes_in": int(u.get("bytes-in", 0) or 0), "bytes_out": int(u.get("bytes-out", 0) or 0),
                    "comment": u.get("comment", ""), "disabled": u.get("disabled") == "true",
                    "limit_uptime": u.get("limit-uptime", ""), "online": bool(a), "address": a.get("address") if a else "",
                    "mac": a.get("mac-address") if a else "", "voucher": bool(v),
                    "used_at": v["used_at"] if v else None, "expires_at": v["expires_at"] if v else None})
    out.sort(key=lambda x: (not x["online"], x["name"].lower()))
    total = len(out)
    return {"users": out[:limit], "total": total, "online": len(active), "truncated": total > limit}


def user_action(r, uid, action):
    """action: remove | enable | disable | kick (putuskan sesi aktif)."""
    c = connect(r)
    try:
        row, _ = c.call("/ip/hotspot/user/print", queries=[f"?.id={uid}"])
        if not row:
            raise HsError("User tidak ditemukan.")
        name = row[0].get("name")
        if action == "remove":
            c.call("/ip/hotspot/user/remove", {".id": uid})
            _drop_session(c, name)
            _mark_removed(r["id"], [name])
        elif action in ("enable", "disable"):
            c.call("/ip/hotspot/user/" + action, {".id": uid})
            if action == "disable":
                _drop_session(c, name)
        elif action == "kick":
            _drop_session(c, name)
        else:
            raise HsError("Aksi tidak dikenal.")
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    return name


def _drop_session(c, name):
    try:
        for a in c.call("/ip/hotspot/active/print", queries=[f"?user={name}"])[0]:
            c.call("/ip/hotspot/active/remove", {".id": a[".id"]})
    except ros.RosError:
        pass


def _mark_removed(rid, names):
    """Voucher yang sudah terjual tetap tercatat (removed=1); yang belum pernah dipakai dihapus dari catatan."""
    for n in names:
        db.ex("DELETE FROM hs_vouchers WHERE router_id=? AND username=? AND used_at IS NULL", (rid, n))
        db.ex("UPDATE hs_vouchers SET removed=1 WHERE router_id=? AND username=?", (rid, n))


def remove_users(r, names):
    names = [n for n in dict.fromkeys(names) if n][:1000]
    c = connect(r, timeout=30 + len(names) * 0.3)
    n_ok = 0
    try:
        rows, _ = c.call("/ip/hotspot/user/print", proplist=[".id", "name"])
        byname = {u.get("name"): u.get(".id") for u in rows}
        for n in names:
            if n in byname:
                c.call("/ip/hotspot/user/remove", {".id": byname[n]})
                _drop_session(c, n)
                n_ok += 1
        _mark_removed(r["id"], names)
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    return n_ok


# ------------------------------------------------------------------ voucher
def _code(length, kind, prefix=""):
    alpha = CHARSETS[kind]
    return prefix + "".join(secrets.choice(alpha) for _ in range(length))


def generate(r, b, created_by=""):
    prof = (b.get("profile") or "").strip()
    qty = int(b.get("qty") or 0)
    mode = b.get("mode") or "vc"
    kind = b.get("charset") or "num"
    length = int(b.get("length") or 6)
    prefix = (b.get("prefix") or "").strip()
    server = (b.get("server") or "all").strip()
    lim_up = (b.get("limit_uptime") or "").strip()
    note = re.sub(r"[^\w .-]", "", (b.get("note") or "").strip())[:40]
    if not 1 <= qty <= MAX_QTY:
        raise HsError(f"Jumlah voucher 1–{MAX_QTY}.")
    if mode not in ("vc", "up") or kind not in CHARSETS:
        raise HsError("Mode atau jenis karakter tidak valid.")
    if not 4 <= length <= 12:
        raise HsError("Panjang kode 4–12 karakter.")
    if not PREFIX_RE.match(prefix):
        raise HsError("Prefix maksimal 8 huruf/angka.")
    if lim_up and not TIME_RE.match(lim_up):
        raise HsError("Batas uptime tidak valid (contoh 2h, 1d).")
    try:
        lim_mb = int(b.get("limit_mb") or 0)
    except (TypeError, ValueError):
        raise HsError("Kuota harus angka (MB).")
    if kind == "num" and length - len(prefix) < 4 and qty > 1000:
        raise HsError("Kode terlalu pendek untuk jumlah sebanyak itu.")
    batch = datetime.now(ZoneInfo(TZ_NAME)).strftime("%y%m%d-%H%M") + "-" + secrets.token_hex(2)
    comment = f"pantau:{batch}" + (f" {note}" if note else "")
    c = connect(r, timeout=30 + qty * 0.4)
    made = []
    try:
        profs = {p.get("name") for p in c.call("/ip/hotspot/user/profile/print", proplist=["name"])[0]}
        if prof not in profs:
            raise HsError(f"Profile '{prof}' tidak ada di router.")
        have = {u.get("name") for u in c.call("/ip/hotspot/user/print", proplist=["name"])[0]}
        m = _meta(r["id"]).get(prof, {})
        price, validity = int(m.get("price", 0)), m.get("validity", "")
        rows, tries = [], 0
        while len(rows) < qty:
            tries += 1
            if tries > qty * 50 + 200:
                raise HsError("Tidak bisa membuat kode unik. Perpanjang kode atau ganti jenis karakter.")
            name = _code(length, kind, prefix)
            if name in have:
                continue
            have.add(name)
            rows.append((name, name if mode == "vc" else _code(length, kind)))
        for name, pw in rows:
            attrs = {"name": name, "password": pw, "profile": prof, "comment": comment}
            if server and server != "all":
                attrs["server"] = server
            if lim_up:
                attrs["limit-uptime"] = lim_up
            if lim_mb > 0:
                attrs["limit-bytes-total"] = str(lim_mb * 1024 * 1024)
            c.call("/ip/hotspot/user/add", attrs)
            made.append(name)
    except ros.RosError as e:
        _rollback(c, made)
        raise _wrap(e)
    except HsError:
        _rollback(c, made)
        raise
    finally:
        c.close()
    now = int(time.time())
    cx = db.conn()
    cx.executemany("""INSERT OR REPLACE INTO hs_vouchers(router_id,batch,username,password,profile,price,validity,created_by,created_at)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   [(r["id"], batch, n, pw, prof, price, validity, created_by, now) for n, pw in rows])
    cx.commit()
    return {"batch": batch, "count": len(rows), "profile": prof, "price": price, "validity": validity,
            "vouchers": [{"username": n, "password": pw} for n, pw in rows]}


def _rollback(c, names):
    try:
        if not names:
            return
        ids = {u.get("name"): u.get(".id") for u in c.call("/ip/hotspot/user/print", proplist=[".id", "name"])[0]}
        for n in names:
            if n in ids:
                c.call("/ip/hotspot/user/remove", {".id": ids[n]})
    except ros.RosError:
        pass


def list_batches(rid):
    return db.q("""SELECT batch, profile, price, validity, created_by, MIN(created_at) created_at, COUNT(*) total,
                          SUM(used_at IS NOT NULL) used, SUM(removed) removed
                   FROM hs_vouchers WHERE router_id=? GROUP BY batch ORDER BY created_at DESC LIMIT 100""", (rid,))


def batch_vouchers(rid, batch):
    return db.q("""SELECT username, password, profile, price, validity, used_at, removed FROM hs_vouchers
                   WHERE router_id=? AND batch=? ORDER BY id""", (rid, batch))


# ------------------------------------------------------------------ sinkron (terjual & kedaluwarsa)
def sync_router(r):
    """Tandai voucher yang sudah dipakai, hapus yang kedaluwarsa. Mengembalikan ringkasan; error koneksi dilempar sebagai HsError."""
    if not db.q1("SELECT 1 FROM hs_vouchers WHERE router_id=? AND removed=0 LIMIT 1", (r["id"],)):
        return {"used": 0, "expired": 0}
    c = connect(r)
    used = expired = 0
    now = int(time.time())
    try:
        users, _ = c.call("/ip/hotspot/user/print", proplist=[".id", "name", "uptime", "bytes-in", "bytes-out"])
        byname = {u.get("name"): u for u in users}
        for v in db.q("SELECT * FROM hs_vouchers WHERE router_id=? AND removed=0", (r["id"],)):
            u = byname.get(v["username"])
            if u is None:                                  # hilang dari router
                if v["used_at"] is None:                   # tidak pernah terlihat dipakai: hapus manual/habis -> anggap terpakai
                    db.ex("UPDATE hs_vouchers SET used_at=?, removed=1 WHERE id=?", (now, v["id"]))
                    used += 1
                else:
                    db.ex("UPDATE hs_vouchers SET removed=1 WHERE id=?", (v["id"],))
                continue
            is_used = (u.get("uptime") or "0s") not in ("0s", "") or int(u.get("bytes-in", 0) or 0) > 0 or int(u.get("bytes-out", 0) or 0) > 0
            if v["used_at"] is None and is_used:
                sec = _secs(v["validity"])
                db.ex("UPDATE hs_vouchers SET used_at=?, expires_at=? WHERE id=?", (now, now + sec if sec else None, v["id"]))
                used += 1
                v["used_at"], v["expires_at"] = now, (now + sec if sec else None)
            if v["expires_at"] and v["expires_at"] <= now:
                _drop_session(c, v["username"])
                c.call("/ip/hotspot/user/remove", {".id": u[".id"]})
                db.ex("UPDATE hs_vouchers SET removed=1 WHERE id=?", (v["id"],))
                expired += 1
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    return {"used": used, "expired": expired}


def sync_all():
    rows = db.q("SELECT * FROM routers WHERE hs_enabled=1")
    def one(r):
        try:
            sync_router(r)
        except Exception as e:
            log.debug("sync hotspot %s gagal: %s", r["name"], e)
    if rows:
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(one, rows))


def loop():
    while True:
        try:
            sync_all()
        except Exception:
            log.exception("loop hotspot")
        time.sleep(max(30, int(db.get_settings().get("hs_sync_interval") or 60)))


def start():
    import threading
    threading.Thread(target=loop, daemon=True, name="hotspot").start()


# ------------------------------------------------------------------ uji koneksi akun hotspot
def test(r):
    """Login, cek hotspot ada, dan uji hak tulis dengan membuat lalu menghapus satu user sementara."""
    c = connect(r, timeout=15)
    try:
        servers = [s.get("name") for s in _safe(c, "/ip/hotspot/print")]
        n_users = len(c.call("/ip/hotspot/user/print", proplist=["name"])[0])
        tmp = "pantau-test-" + secrets.token_hex(3)
        try:
            c.call("/ip/hotspot/user/add", {"name": tmp, "password": tmp, "comment": "uji hak tulis Pantau"})
        except ros.RosError as e:
            raise _wrap(e)
        rows, _ = c.call("/ip/hotspot/user/print", queries=[f"?name={tmp}"])
        for x in rows:
            c.call("/ip/hotspot/user/remove", {".id": x[".id"]})
    except ros.RosError as e:
        raise _wrap(e)
    finally:
        c.close()
    return {"ok": True, "servers": [s for s in servers if s], "users": n_users, "hotspot_found": bool(servers)}


# ------------------------------------------------------------------ laporan penjualan
def sales(router_ids, days=30):
    days = max(1, min(int(days or 30), 366))
    tz = ZoneInfo(TZ_NAME)
    today = datetime.now(tz).date()
    start = today - timedelta(days=days - 1)
    t0 = int(datetime(start.year, start.month, start.day, tzinfo=tz).timestamp())
    ids = list(router_ids)
    ph = "(" + (",".join("?" * len(ids)) or "NULL") + ")"
    used = db.q(f"SELECT router_id, profile, price, used_at, username FROM hs_vouchers WHERE router_id IN {ph} AND used_at>=? ORDER BY used_at", ids + [t0])
    stock = db.q(f"SELECT COUNT(*) n, COALESCE(SUM(price),0) v FROM hs_vouchers WHERE router_id IN {ph} AND used_at IS NULL AND removed=0", ids)[0]
    by_day = {(start + timedelta(days=i)).isoformat(): [0, 0] for i in range(days)}
    by_prof = {}
    for v in used:
        d = datetime.fromtimestamp(v["used_at"], tz).date().isoformat()
        if d in by_day:
            by_day[d][0] += 1
            by_day[d][1] += v["price"]
        p = by_prof.setdefault(v["profile"], [0, 0])
        p[0] += 1
        p[1] += v["price"]
    return {"days": days, "from": start.isoformat(), "to": today.isoformat(),
            "count": len(used), "amount": sum(v["price"] for v in used),
            "stock_count": stock["n"], "stock_value": stock["v"],
            "by_day": [{"date": d, "count": c, "amount": a} for d, (c, a) in by_day.items()],
            "by_profile": sorted(({"profile": k, "count": c, "amount": a} for k, (c, a) in by_prof.items()), key=lambda x: -x["amount"])}


# ------------------------------------------------------------------ halaman login hotspot (login.html)
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def login_html(title="WiFi Voucher", subtitle="Masukkan kode voucher Anda", color="#2563eb", mode="voucher", footer=""):
    """Template login.html MikroTik. mode 'voucher' = satu kolom kode (username=password), 'userpass' = dua kolom."""
    def clean(s, n):
        return html.escape(str(s or "").replace("$(", "").replace("$", "")[:n])
    color = color if COLOR_RE.match(color or "") else "#2563eb"
    title, subtitle, footer = clean(title, 60), clean(subtitle, 100), clean(footer, 160)
    if mode == "userpass":
        fields = ('<input name="username" type="text" placeholder="Username" autocomplete="off" required>'
                  '<input name="password" type="password" placeholder="Password" autocomplete="off" required>')
        fill = ""
    else:
        fields = ('<input name="username" type="text" placeholder="Kode voucher" autocomplete="off" autocapitalize="off" required>'
                  '<input name="password" type="hidden" value="">')
        fill = "document.login.password.value=document.login.username.value;"
    return f"""<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="pragma" content="no-cache"><meta http-equiv="expires" content="-1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(160deg,{color},#0f172a);font-family:system-ui,Segoe UI,Roboto,sans-serif;color:#0f172a}}
.card{{width:min(380px,92vw);background:#fff;border-radius:16px;padding:28px 24px;box-shadow:0 20px 50px rgba(0,0,0,.35)}}
h1{{margin:0 0 4px;font-size:22px}}p{{margin:0 0 18px;color:#475569;font-size:14px}}
input{{width:100%;padding:13px 14px;margin:0 0 10px;border:1px solid #cbd5e1;border-radius:10px;font-size:16px}}
input:focus{{outline:2px solid {color};border-color:transparent}}
button{{width:100%;padding:13px;border:0;border-radius:10px;background:{color};color:#fff;font-size:16px;font-weight:600;cursor:pointer}}
.err{{margin:0 0 12px;padding:10px 12px;border-radius:8px;background:#fee2e2;color:#991b1b;font-size:14px}}
.foot{{margin-top:16px;text-align:center;font-size:12px;color:#64748b}}
</style></head><body>
$(if chap-id)
<form name="sendin" action="$(link-login-only)" method="post">
<input type="hidden" name="username"><input type="hidden" name="password">
<input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true"></form>
<script src="/md5.js"></script>
<script>function doLogin(){{document.sendin.username.value=document.login.username.value;document.sendin.password.value=hexMD5('$(chap-id)'+document.login.password.value+'$(chap-challenge)');document.sendin.submit();return false;}}</script>
$(endif)
<div class="card">
<h1>{title}</h1><p>{subtitle}</p>
$(if error)<div class="err">$(error)</div>$(endif)
<form name="login" action="$(link-login-only)" method="post" onSubmit="{fill}$(if chap-id)return doLogin();$(endif)">
<input type="hidden" name="dst" value="$(link-orig)"><input type="hidden" name="popup" value="true">
{fields}
<button type="submit">Hubungkan</button>
</form>
<div class="foot">{footer}</div>
</div>
</body></html>
"""
