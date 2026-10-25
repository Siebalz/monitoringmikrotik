import json, time, threading, logging, hmac, os, re
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import db, poller, ros, telegram as tg, demo, vpn, diag, latency
from .config import ADMIN_USER, ADMIN_PASSWORD, SECRET_KEY, DEMO, COOKIE_SECURE, APP_VERSION
from .security import enc, dec, hash_password, verify_password, _DUMMY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@asynccontextmanager
async def lifespan(app):
    db.init()
    if DEMO:
        demo.seed()
        latency.seed_demo()
    if ADMIN_PASSWORD == "admin":
        logging.warning("ADMIN_PASSWORD masih default 'admin' — segera ganti di .env!")
    poller.start()
    latency.start()
    tg.start_bot()
    yield


app = FastAPI(title="MikroTik Monitor", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=14 * 86400, same_site="lax", https_only=COOKIE_SECURE)
class NoCacheStatic(StaticFiles):
    """File statis selalu divalidasi ulang (ETag) agar update app.js/style.css langsung terpakai, tanpa Ctrl+Shift+R."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")


def _ctx(request: Request):
    """Identitas sesi. Admin = akun .env (akses penuh); client = akun portal klien (baca-saja, hanya router miliknya)."""
    u = request.session.get("u")
    if not u:
        raise HTTPException(401, "Belum login")
    if (request.session.get("role") or "admin") == "client":
        row = db.q1("SELECT id, username, display_name, enabled FROM users WHERE id=? AND role='client'", (request.session.get("uid"),))
        if not row or not row["enabled"]:          # dinonaktifkan/dihapus -> langsung tertendang
            request.session.clear()
            raise HTTPException(401, "Akun tidak aktif")
        return {"role": "client", "uid": row["id"], "username": row["username"], "name": row["display_name"] or row["username"]}
    return {"role": "admin", "uid": None, "username": u, "name": u}


def auth(request: Request):
    """Default semua endpoint: HANYA admin."""
    if _ctx(request)["role"] != "admin":
        raise HTTPException(403, "Hanya untuk admin")


def auth_any(request: Request):
    """Admin atau klien (dipakai khusus endpoint baca-saja yang disaring per klien)."""
    return _ctx(request)


def scope(request: Request):
    """None = admin (semua router); list id router untuk klien."""
    c = _ctx(request)
    if c["role"] == "admin":
        return None
    return [r["router_id"] for r in db.q("SELECT router_id FROM user_routers WHERE user_id=?", (c["uid"],))]


def _check(request: Request, rid: int):
    sc = scope(request)
    if sc is not None and rid not in sc:
        raise HTTPException(404, "Router tidak ditemukan")      # 404, bukan 403: tidak membocorkan keberadaan router lain


def _in(ids):
    return "(" + (",".join("?" * len(ids)) or "NULL") + ")"


_CLIENT_HIDE = ("host", "port", "username", "use_ssl", "has_password", "notes", "wan_iface")


def _client_view(r):
    """Sembunyikan detail koneksi/internal dari klien."""
    r = dict(r)
    for k in _CLIENT_HIDE:
        r.pop(k, None)
    r["host"] = ""
    if "last_error" in r:
        r["last_error"] = ""
    return r


_fails = {}


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    ip = request.client.host if request.client else "?"
    f = _fails.get(ip, (0, 0))
    if f[0] >= 5 and time.time() - f[1] < 60:
        raise HTTPException(429, "Terlalu banyak percobaan. Coba lagi 1 menit.")
    uname = body.username.strip()
    role, uid = None, None
    if hmac.compare_digest(uname.encode(), ADMIN_USER.encode()) & hmac.compare_digest(body.password.encode(), ADMIN_PASSWORD.encode()):
        role = "admin"
    else:
        u = db.q1("SELECT * FROM users WHERE username=? AND role='client'", (uname,))
        good = verify_password(body.password, u["password_hash"] if u else _DUMMY)      # waktu sama walau user tak ada
        if u and good and u["enabled"]:
            role, uid = "client", u["id"]
    if not role:
        _fails[ip] = (f[0] + 1 if time.time() - f[1] < 60 else 1, time.time())
        raise HTTPException(401, "Username atau password salah")
    _fails.pop(ip, None)
    request.session.clear()
    request.session["u"], request.session["role"] = uname, role
    if uid:
        request.session["uid"] = uid
        db.ex("UPDATE users SET last_login=? WHERE id=?", (int(time.time()), uid))
    return {"ok": True, "role": role}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    brand = db.get_settings()["brand_name"]
    try:
        c = _ctx(request)
    except HTTPException:
        return {"user": None, "demo": DEMO, "brand": brand, "version": APP_VERSION}
    return {"user": c["username"], "name": c["name"], "role": c["role"], "demo": DEMO, "brand": brand, "version": APP_VERSION}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"), headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": APP_VERSION}


# ------------------------------------------------------------------ helpers
RANGES = {"1h": 3600, "3h": 10800, "6h": 21600, "24h": 86400, "7d": 604800, "30d": 2592000}


def _range(r):
    sec = RANGES.get(r, 3600)
    bucket = max(30, -(-sec // 300 // 30) * 30)
    return sec, bucket


def _router_public(r):
    r = dict(r)
    r["has_password"] = bool(r.pop("password_enc", ""))
    return r


def _load(rid):
    r = db.q1("SELECT * FROM routers WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404, "Router tidak ditemukan")
    return r


# ------------------------------------------------------------------ overview
@app.get("/api/overview", dependencies=[Depends(auth_any)])
def overview(request: Request):
    sc = scope(request)
    now = int(time.time())
    rows = db.q("""SELECT r.*, s.online, s.fail_count, s.last_ok, s.last_poll, s.last_error, s.down_since, s.info, s.latest
                   FROM routers r LEFT JOIN router_state s ON s.router_id=r.id ORDER BY r.name""")
    if sc is not None:
        rows = [r for r in rows if r["id"] in sc]
    alerts = db.q("""SELECT a.router_id, a.key, a.level, a.since, a.message, r.name router_name
                     FROM alert_state a JOIN routers r ON r.id=a.router_id ORDER BY a.level='critical' DESC, a.since DESC""")
    if sc is not None:
        alerts = [a for a in alerts if a["router_id"] in sc]
    by_r = {}
    for a in alerts:
        by_r.setdefault(a["router_id"], []).append(a)
    spark = {}
    for m in db.q("SELECT router_id, ts, cpu, rx_bps, tx_bps FROM metrics WHERE ts > ? ORDER BY ts", (now - 3600,)):
        spark.setdefault(m["router_id"], []).append([m["ts"], m["cpu"], (m["rx_bps"] or 0) + (m["tx_bps"] or 0) if m["rx_bps"] is not None else None])
    out = []
    tot = {"rx": 0.0, "tx": 0.0, "clients": 0, "ppp": 0}
    for r in rows:
        lt = json.loads(r.pop("latest") or "{}")
        info = json.loads(r.pop("info") or "{}")
        lt.pop("ifaces", None)
        r = _router_public(r)
        if sc is not None:
            r = _client_view(r)
        r.update(latest=lt, info=info, alerts=by_r.get(r["id"], []), spark=spark.get(r["id"], []))
        if r["enabled"] and r["online"]:
            tot["rx"] += lt.get("rx_bps") or 0
            tot["tx"] += lt.get("tx_bps") or 0
            tot["clients"] += (lt.get("dhcp") or 0)
            tot["ppp"] += (lt.get("ppp") or 0)
        out.append(r)
    en = [r for r in out if r["enabled"]]
    lats = [r["latest"].get("latency") for r in en if r["online"] and r["latest"].get("latency") is not None]
    summary = {
        "total": len(out), "enabled": len(en),
        "online": sum(1 for r in en if r["online"]), "offline": sum(1 for r in en if not r["online"] and r["last_poll"]),
        "alerts": len(alerts), "rx_bps": tot["rx"], "tx_bps": tot["tx"], "dhcp": tot["clients"], "ppp": tot["ppp"],
        "avg_latency": sum(lats) / len(lats) if lats else None,
    }
    ev = db.q("SELECT * FROM events ORDER BY id DESC LIMIT 10") if sc is None else \
        db.q(f"SELECT * FROM events WHERE router_id IN {_in(sc)} ORDER BY id DESC LIMIT 10", sc)
    return {"summary": summary, "routers": out, "alerts": alerts, "events": ev, "now": now,
            "poll_interval": db.get_settings()["poll_interval"]}


@app.get("/api/fleet/traffic", dependencies=[Depends(auth_any)])
def fleet_traffic(request: Request, range: str = "6h"):
    sc = scope(request)
    sec, b = _range(range)
    b = max(b, 60)
    flt, args = ("", []) if sc is None else (f" AND router_id IN {_in(sc)}", list(sc))
    rows = db.q(f"""SELECT t, SUM(rx) rx, SUM(tx) tx, COUNT(*) n FROM (
                     SELECT router_id, (ts/?)*? t, AVG(rx_bps) rx, AVG(tx_bps) tx FROM metrics
                     WHERE ts>=? AND online=1{flt} GROUP BY router_id, t) GROUP BY t ORDER BY t""", [b, b, int(time.time()) - sec] + args)
    return {"bucket": b, "t": [r["t"] for r in rows], "rx": [r["rx"] for r in rows], "tx": [r["tx"] for r in rows]}


# ------------------------------------------------------------------ router detail
@app.get("/api/routers/{rid}", dependencies=[Depends(auth_any)])
def router_detail(rid: int, request: Request):
    _check(request, rid)
    r = _load(rid)
    s = db.q1("SELECT * FROM router_state WHERE router_id=?", (rid,)) or {}
    now = int(time.time())
    av = {}
    for k, sec in (("24h", 86400), ("7d", 604800), ("30d", 2592000)):
        a = db.q1("SELECT AVG(online)*100 v, COUNT(*) n FROM metrics WHERE router_id=? AND ts>=?", (rid, now - sec))
        av[k] = a["v"] if a and a["n"] else None
    watched = [x["name"] for x in db.q("SELECT name FROM watched_ifaces WHERE router_id=?", (rid,))]
    alerts = db.q("SELECT * FROM alert_state WHERE router_id=? ORDER BY since DESC", (rid,))
    events = db.q("SELECT * FROM events WHERE router_id=? ORDER BY id DESC LIMIT 15", (rid,))
    res = {"router": _router_public(r),
            "state": {k: s.get(k) for k in ("online", "fail_count", "last_ok", "last_poll", "last_error", "down_since")},
            "info": json.loads(s.get("info") or "{}"), "latest": json.loads(s.get("latest") or "{}"),
            "availability": av, "watched": watched, "alerts": alerts, "events": events, "now": now}
    if scope(request) is not None:
        res["router"] = _client_view(res["router"])
        res["state"]["last_error"] = ""
        res["watched"] = []
    return res


@app.get("/api/routers/{rid}/metrics", dependencies=[Depends(auth_any)])
def router_metrics(rid: int, request: Request, range: str = "1h"):
    _check(request, rid)
    _load(rid)
    sec, b = _range(range)
    since = int(time.time()) - sec
    rows = db.q("""SELECT (ts/:b)*:b t, AVG(cpu) cpu, AVG(mem_pct) mem, AVG(temp) temp, AVG(volt) volt, AVG(rx_bps) rx, AVG(tx_bps) tx,
                   AVG(latency) lat, AVG(ppp) ppp, AVG(dhcp) dhcp, AVG(wifi) wifi, AVG(conn) conn, MIN(online) online
                   FROM metrics WHERE router_id=:r AND ts>=:s GROUP BY t ORDER BY t""".replace(":b", str(b)).replace(":r", str(rid)).replace(":s", str(since)))
    summ = db.q1("""SELECT AVG(cpu) cpu_avg, MAX(cpu) cpu_max, AVG(mem_pct) mem_avg, MAX(mem_pct) mem_max, MAX(temp) temp_max,
                    AVG(latency) lat_avg, MAX(latency) lat_max, AVG(rx_bps) rx_avg, MAX(rx_bps) rx_max, AVG(tx_bps) tx_avg,
                    MAX(tx_bps) tx_max, AVG(online)*100 avail FROM metrics WHERE router_id=? AND ts>=?""", (rid, since))
    summ["rx_bytes"] = (summ["rx_avg"] or 0) * sec / 8
    summ["tx_bytes"] = (summ["tx_avg"] or 0) * sec / 8
    cols = ["cpu", "mem", "temp", "volt", "rx", "tx", "lat", "ppp", "dhcp", "wifi", "conn", "online"]
    d = {"bucket": b, "range": sec, "t": [r["t"] for r in rows], "summary": summ}
    for c in cols:
        d[c] = [r[c] for r in rows]
    return d


@app.get("/api/routers/{rid}/iface", dependencies=[Depends(auth_any)])
def router_iface(rid: int, request: Request, name: str, range: str = "1h"):
    _check(request, rid)
    _load(rid)
    sec, b = _range(range)
    rows = db.q("""SELECT (ts/?)*? t, AVG(rx_bps) rx, AVG(tx_bps) tx FROM iface_metrics
                   WHERE router_id=? AND name=? AND ts>=? GROUP BY t ORDER BY t""", (b, b, rid, name, int(time.time()) - sec))
    return {"bucket": b, "t": [r["t"] for r in rows], "rx": [r["rx"] for r in rows], "tx": [r["tx"] for r in rows]}


class WatchIn(BaseModel):
    names: list[str]


@app.put("/api/routers/{rid}/watched", dependencies=[Depends(auth)])
def set_watched(rid: int, body: WatchIn):
    _load(rid)
    db.ex("DELETE FROM watched_ifaces WHERE router_id=?", (rid,))
    for n in set(body.names):
        db.ex("INSERT INTO watched_ifaces VALUES(?,?)", (rid, n))
    return {"ok": True}


@app.post("/api/routers/{rid}/poll", dependencies=[Depends(auth)])
def poll_now(rid: int):
    r = _load(rid)
    st = poller.poll_router(r)
    return {"online": st["online"], "error": st["last_error"]}


# ------------------------------------------------------------------ router CRUD
class RouterIn(BaseModel):
    name: str
    host: str
    port: int = 8728
    username: str = "monitor"
    password: Optional[str] = None
    use_ssl: bool = False
    group_name: str = ""
    notes: str = ""
    wan_iface: str = ""
    enabled: bool = True
    alerts_enabled: bool = True
    cpu_limit: Optional[float] = None
    mem_limit: Optional[float] = None
    temp_limit: Optional[float] = None
    latency_limit: Optional[float] = None


@app.get("/api/routers", dependencies=[Depends(auth)])
def list_routers():
    return [_router_public(r) for r in db.q("SELECT * FROM routers ORDER BY name")]


@app.post("/api/routers", dependencies=[Depends(auth)])
def create_router(b: RouterIn):
    cur = db.ex("""INSERT INTO routers(name,host,port,username,password_enc,use_ssl,group_name,notes,wan_iface,enabled,alerts_enabled,
                   cpu_limit,mem_limit,temp_limit,latency_limit,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (b.name.strip(), b.host.strip(), b.port, b.username, enc(b.password or ""), int(b.use_ssl), b.group_name.strip(),
                 b.notes, b.wan_iface.strip(), int(b.enabled), int(b.alerts_enabled), b.cpu_limit, b.mem_limit, b.temp_limit,
                 b.latency_limit, int(time.time())))
    rid = cur.lastrowid
    threading.Thread(target=lambda: poller.poll_router(_load(rid)), daemon=True).start()
    return {"id": rid}


@app.put("/api/routers/{rid}", dependencies=[Depends(auth)])
def update_router(rid: int, b: RouterIn):
    old = _load(rid)
    pw = enc(b.password) if b.password else old["password_enc"]
    db.ex("""UPDATE routers SET name=?,host=?,port=?,username=?,password_enc=?,use_ssl=?,group_name=?,notes=?,wan_iface=?,enabled=?,
             alerts_enabled=?,cpu_limit=?,mem_limit=?,temp_limit=?,latency_limit=? WHERE id=?""",
          (b.name.strip(), b.host.strip(), b.port, b.username, pw, int(b.use_ssl), b.group_name.strip(), b.notes, b.wan_iface.strip(),
           int(b.enabled), int(b.alerts_enabled), b.cpu_limit, b.mem_limit, b.temp_limit, b.latency_limit, rid))
    if not b.enabled:
        db.ex("DELETE FROM alert_state WHERE router_id=?", (rid,))
    threading.Thread(target=lambda: poller.poll_router(_load(rid)), daemon=True).start()
    return {"ok": True}


@app.delete("/api/routers/{rid}", dependencies=[Depends(auth)])
def delete_router(rid: int):
    _load(rid)
    for t in ("routers", "router_state", "metrics", "iface_metrics", "alert_state", "watched_ifaces"):
        db.ex(f"DELETE FROM {t} WHERE {'id' if t == 'routers' else 'router_id'}=?", (rid,))
    db.ex("DELETE FROM user_routers WHERE router_id=?", (rid,))
    return {"ok": True}


class TestIn(BaseModel):
    id: Optional[int] = None
    host: str
    port: int = 8728
    username: str = "monitor"
    password: Optional[str] = None
    use_ssl: bool = False


@app.post("/api/routers/test", dependencies=[Depends(auth)])
def test_router(b: TestIn):
    pw = b.password or ""
    if not pw and b.id:
        pw = dec(_load(b.id)["password_enc"])
    r = {"id": 0, "name": "test", "host": b.host.strip(), "port": b.port, "username": b.username, "use_ssl": int(b.use_ssl)}
    try:
        d = ros.mock_fetch(r, pw) if r["host"].startswith("demo") else ros.fetch(r, pw)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "latency": d["latency"], "info": d["info"], "interfaces": [i["name"] for i in d["ifaces"]],
            "uptime": d["uptime"]}


# ------------------------------------------------------------------ events
@app.get("/api/events", dependencies=[Depends(auth_any)])
def events(request: Request, router_id: Optional[int] = None, level: Optional[str] = None, limit: int = 200):
    sc = scope(request)
    sql, a = "SELECT * FROM events WHERE 1=1", []
    if sc is not None:                                   # klien: hanya router miliknya, tanpa log audit admin
        sql += f" AND router_id IN {_in(sc)} AND kind!='audit'"
        a += list(sc)
    if router_id:
        sql += " AND router_id=?"
        a.append(router_id)
    if level:
        sql += " AND level=?"
        a.append(level)
    sql += " ORDER BY id DESC LIMIT ?"
    a.append(min(limit, 1000))
    return db.q(sql, a)


# ------------------------------------------------------------------ settings
def _settings_public():
    s = db.get_settings()
    tok = s.pop("tg_token", "")
    s["tg_token_set"] = bool(tok)
    s["tg_token_hint"] = ("•••• " + tok[-4:]) if tok else ""
    return s


@app.get("/api/settings", dependencies=[Depends(auth)])
def get_settings():
    return _settings_public()


@app.put("/api/settings", dependencies=[Depends(auth)])
def put_settings(body: dict):
    clean = {}
    for k, v in body.items():
        if k not in db.DEFAULT_SETTINGS:
            continue
        if k == "tg_token":
            if not v:
                continue
            v = str(v).strip()
        elif isinstance(db.DEFAULT_SETTINGS[k], bool):
            v = bool(v)
        elif isinstance(db.DEFAULT_SETTINGS[k], (int, float)):
            try:
                v = float(v)
                v = int(v) if v == int(v) else v
            except (TypeError, ValueError):
                continue
        else:
            v = str(v).strip()
        clean[k] = v
    if body.get("tg_token_clear"):
        clean["tg_token"] = ""
    db.save_settings(clean)
    return _settings_public()


@app.post("/api/telegram/test", dependencies=[Depends(auth)])
def telegram_test():
    ok, err = tg.send("✅ <b>Tes notifikasi</b>\nMikroTik Monitor terhubung ke Telegram.\n🕒 " + tg.fmt_ts())
    return {"ok": ok, "error": err}


# ------------------------------------------------------------------ VPN server
class VpnServerIn(BaseModel):
    name: str
    host: str
    port: int = 8728
    username: str
    password: Optional[str] = None
    use_ssl: bool = False
    public_ip: str = ""
    vpn_subnet: str = ""
    profile: str = "default"


def _vpn_server(sid):
    r = db.q1("SELECT * FROM vpn_servers WHERE id=?", (sid,))
    if not r:
        raise HTTPException(404, "Server VPN tidak ditemukan")
    return r


def _vpn_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except vpn.VpnError as e:
        raise HTTPException(400, str(e))
    except ros.RosError as e:
        raise HTTPException(502, str(e))


def _norm_subnet(s):
    s = (s or "").strip()
    return s if not s or s.endswith(".") else s + "."


def _audit(request, msg):
    db.add_event("info", "audit", f"[{request.session.get('u', '?')}] {msg}")


@app.get("/api/vpn/servers", dependencies=[Depends(auth)])
def vpn_servers():
    return [_router_public(r) for r in db.q("SELECT * FROM vpn_servers ORDER BY name")]


@app.post("/api/vpn/servers", dependencies=[Depends(auth)])
def vpn_server_add(b: VpnServerIn, request: Request):
    cur = db.ex("""INSERT INTO vpn_servers(name,host,port,username,password_enc,use_ssl,public_ip,vpn_subnet,profile,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (b.name.strip(), b.host.strip(), b.port, b.username.strip(), enc(b.password or ""), int(b.use_ssl),
                 b.public_ip.strip() or b.host.strip(), _norm_subnet(b.vpn_subnet), b.profile.strip() or "default", int(time.time())))
    _audit(request, f"Server VPN ditambahkan: {b.name}")
    return {"id": cur.lastrowid}


@app.put("/api/vpn/servers/{sid}", dependencies=[Depends(auth)])
def vpn_server_update(sid: int, b: VpnServerIn, request: Request):
    old = _vpn_server(sid)
    pw = enc(b.password) if b.password else old["password_enc"]
    db.ex("""UPDATE vpn_servers SET name=?,host=?,port=?,username=?,password_enc=?,use_ssl=?,public_ip=?,vpn_subnet=?,profile=? WHERE id=?""",
          (b.name.strip(), b.host.strip(), b.port, b.username.strip(), pw, int(b.use_ssl), b.public_ip.strip() or b.host.strip(),
           _norm_subnet(b.vpn_subnet), b.profile.strip() or "default", sid))
    _audit(request, f"Server VPN diubah: {b.name}")
    return {"ok": True}


@app.delete("/api/vpn/servers/{sid}", dependencies=[Depends(auth)])
def vpn_server_delete(sid: int, request: Request):
    s = _vpn_server(sid)
    db.ex("DELETE FROM vpn_servers WHERE id=?", (sid,))
    db.ex("DELETE FROM vpn_links WHERE server_id=?", (sid,))
    _audit(request, f"Server VPN dihapus dari panel: {s['name']} (router di MikroTik tidak disentuh)")
    return {"ok": True}


class VpnTestIn(BaseModel):
    id: Optional[int] = None
    host: str
    port: int = 8728
    username: str
    password: Optional[str] = None
    use_ssl: bool = False


@app.post("/api/vpn/servers/test", dependencies=[Depends(auth)])
def vpn_server_test(b: VpnTestIn):
    pw = b.password or (dec(_vpn_server(b.id)["password_enc"]) if b.id else "")
    try:
        c = ros.connect(b.host.strip(), b.port, b.use_ssl, b.username, pw, timeout=10)
        try:
            res = c.call("/system/resource/print")[0][0]
            ident = c.call("/system/identity/print")[0][0].get("name", "")
            try:
                profs = [p.get("name") for p in c.call("/ppp/profile/print", proplist=["name"])[0]]
                l2tp = c.call("/interface/l2tp-server/server/print")[0]
                l2tp_on = bool(l2tp and l2tp[0].get("enabled") == "true")
            except ros.RosError:
                profs, l2tp_on = [], None
        finally:
            c.close()
    except ros.RosError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "identity": ident, "version": res.get("version", ""), "board": res.get("board-name", ""),
            "profiles": profs, "l2tp_enabled": l2tp_on}


@app.get("/api/vpn/servers/{sid}/users", dependencies=[Depends(auth)])
def vpn_users(sid: int):
    srv = _vpn_server(sid)
    d = _vpn_call(vpn.list_users, srv)
    d["server"] = _router_public(srv)
    return d


class VpnUserIn(BaseModel):
    username: str
    password: str = ""
    profile: str = ""
    monitor: bool = True
    group_name: str = ""


@app.post("/api/vpn/servers/{sid}/users", dependencies=[Depends(auth)])
def vpn_user_add(sid: int, b: VpnUserIn, request: Request):
    srv = _vpn_server(sid)
    s = db.get_settings()
    res = _vpn_call(vpn.create_user, srv, b.username.strip(), b.password, b.profile.strip(),
                    vpn.parse_services(s["vpn_services"]), s, b.monitor, b.group_name.strip())
    _audit(request, f"User VPN dibuat: {res['username']} ({res['ip']}) di {srv['name']}" + (" + dipantau" if res["router_id"] else ""))
    if res["router_id"]:
        rid = res["router_id"]
        threading.Thread(target=lambda: poller.poll_router(_load(rid)), daemon=True).start()
    return res


@app.delete("/api/vpn/servers/{sid}/users/{secret_id}", dependencies=[Depends(auth)])
def vpn_user_delete(sid: int, secret_id: str, request: Request, remove_monitor: bool = True):
    srv = _vpn_server(sid)
    res = _vpn_call(vpn.delete_user, srv, secret_id)
    lk = db.q1("SELECT router_id FROM vpn_links WHERE server_id=? AND username=?", (sid, res["username"]))
    if lk:
        db.ex("DELETE FROM vpn_links WHERE server_id=? AND username=?", (sid, res["username"]))
        if remove_monitor:
            for t in ("routers", "router_state", "metrics", "iface_metrics", "alert_state", "watched_ifaces"):
                db.ex(f"DELETE FROM {t} WHERE {'id' if t == 'routers' else 'router_id'}=?", (lk["router_id"],))
    _audit(request, f"User VPN dihapus: {res['username']} dari {srv['name']} ({res['removed_nat']} NAT rule dibersihkan)")
    return res


class ToggleIn(BaseModel):
    disabled: bool


@app.post("/api/vpn/servers/{sid}/users/{secret_id}/toggle", dependencies=[Depends(auth)])
def vpn_user_toggle(sid: int, secret_id: str, b: ToggleIn, request: Request):
    srv = _vpn_server(sid)
    name = _vpn_call(vpn.set_disabled, srv, secret_id, b.disabled)
    _audit(request, f"User VPN {'dinonaktifkan' if b.disabled else 'diaktifkan'}: {name}")
    return {"ok": True}


@app.get("/api/vpn/servers/{sid}/users/{secret_id}/script", dependencies=[Depends(auth)])
def vpn_user_script(sid: int, secret_id: str):
    srv = _vpn_server(sid)
    return {"script": _vpn_call(vpn.get_script, srv, secret_id, db.get_settings())}


@app.post("/api/vpn/servers/{sid}/cleanup", dependencies=[Depends(auth)])
def vpn_cleanup(sid: int, request: Request):
    srv = _vpn_server(sid)
    n = _vpn_call(vpn.cleanup_orphans, srv)
    _audit(request, f"Pembersihan NAT yatim di {srv['name']}: {n} rule")
    return {"removed": n}


# ------------------------------------------------------------------ diagnosa (ping / traceroute / log)
def _diag_run(conn, fn, *a, **kw):
    try:
        return fn(conn, *a, **kw)
    except diag.DiagError as e:
        raise HTTPException(400, str(e))
    except ros.RosError as e:
        raise HTTPException(502, str(e))


def _conn_router(r):
    if r["host"].startswith("demo"):
        raise HTTPException(400, "Mode demo: alat diagnosa tidak tersedia untuk router simulasi.")
    return lambda timeout=15: ros.connect(r["host"], r["port"], r["use_ssl"], r["username"], dec(r["password_enc"]), timeout=timeout)


def _conn_vpn(s):
    return lambda timeout=15: ros.connect(s["host"], s["port"], s["use_ssl"], s["username"], dec(s["password_enc"]), timeout=timeout)


class PingIn(BaseModel):
    address: str
    count: int = 4
    size: Optional[int] = None


class TraceIn(BaseModel):
    address: str
    max_hops: int = 20


def _diag_routes(prefix, getter, label):
    """Daftarkan 3 endpoint diagnosa untuk satu jenis target (router monitoring / server VPN)."""
    def mk(kind):
        def deps(tid):
            return _conn_router(getter(tid)) if label == "router" else _conn_vpn(getter(tid))
        return deps

    @app.post(prefix + "/{tid}/diag/ping", dependencies=[Depends(auth)], name=prefix + "_ping")
    def _ping(tid: int, b: PingIn, request: Request):
        res = _diag_run(mk("ping")(tid), diag.ping, b.address, b.count, b.size)
        _audit(request, f"Ping {res['address']} dari {label} #{tid}: {res['received']}/{res['sent']}")
        return res

    @app.post(prefix + "/{tid}/diag/traceroute", dependencies=[Depends(auth)], name=prefix + "_trace")
    def _trace(tid: int, b: TraceIn, request: Request):
        res = _diag_run(mk("trace")(tid), diag.traceroute, b.address, b.max_hops)
        _audit(request, f"Traceroute {res['address']} dari {label} #{tid}: {len(res['hops'])} hop")
        return res

    @app.get(prefix + "/{tid}/diag/log", dependencies=[Depends(auth)], name=prefix + "_log")
    def _log(tid: int, limit: int = 200, topic: str = "", search: str = ""):
        return _diag_run(mk("log")(tid), diag.log, limit, topic, search)


_diag_routes("/api/routers", _load, "router")
_diag_routes("/api/vpn/servers", _vpn_server, "server VPN")


# ------------------------------------------------------------------ network latency
def _lat_view(rid, rng):
    s = db.get_settings()
    sec, b = _range(rng)
    since = int(time.time()) - sec
    tg = latency.targets_for(s)
    rows = db.q("""SELECT target, (ts/?)*? t, AVG(ms) ms, MAX(loss) loss FROM latency
                   WHERE router_id=? AND ts>=? GROUP BY target, t ORDER BY t""", (b, b, rid, since))
    ser = {}
    for r in rows:
        ser.setdefault(r["target"], []).append(r)
    out = []
    for name, host in tg:
        pts = ser.get(name, [])
        last = db.q1("SELECT ts, ms, loss, ip, err FROM latency WHERE router_id=? AND target=? ORDER BY ts DESC LIMIT 1", (rid, name))
        vals = [p["ms"] for p in pts if p["ms"] is not None]
        fresh = bool(last) and int(time.time()) - last["ts"] < max(180, 3 * int(s["latency_interval"]))
        out.append({"name": name, "host": "" if host == "@gateway" else host, "ip": (last or {}).get("ip", ""),
                    "err": last["err"] if last and fresh else "", "ms": last["ms"] if last and fresh else None, "loss": last["loss"] if last and fresh else None, "fresh": fresh,
                    "ts": last["ts"] if last else None,
                    "avg": sum(vals) / len(vals) if vals else None, "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                    "t": [p["t"] for p in pts], "v": [p["ms"] for p in pts]})
    return {"targets": out, "bucket": b, "warn": s["latency_warn"], "bad": s["latency_bad"], "enabled": s["latency_enabled"],
            "error": latency.get_error(rid)}


@app.get("/api/routers/{rid}/latency", dependencies=[Depends(auth_any)])
def router_latency(rid: int, request: Request, range: str = "1h"):
    _check(request, rid)
    _load(rid)
    v = _lat_view(rid, range)
    if scope(request) is not None:
        v["error"] = ""
    return v


@app.post("/api/routers/{rid}/latency/probe", dependencies=[Depends(auth)])
def router_latency_probe(rid: int):
    r = _load(rid)
    n = latency.probe_router(r)
    return {"measured": n, "error": latency.get_error(rid)}


@app.get("/api/latency/overview", dependencies=[Depends(auth_any)])
def latency_overview(request: Request):
    sc = scope(request)
    s = db.get_settings()
    tg = [n for n, _ in latency.targets_for(s)]
    now = int(time.time())
    rows = db.q("""SELECT l.router_id, l.target, l.ms, l.loss, l.ts, l.err FROM latency l
                   JOIN (SELECT router_id, target, MAX(ts) m FROM latency WHERE ts>=? GROUP BY router_id, target) x
                   ON x.router_id=l.router_id AND x.target=l.target AND x.m=l.ts""", (now - 900,))
    cell = {}
    for r in rows:
        cell.setdefault(r["router_id"], {})[r["target"]] = {"ms": r["ms"], "loss": r["loss"], "ts": r["ts"], "err": r["err"]}
    rs = db.q("""SELECT r.id, r.name, r.group_name, r.enabled, COALESCE(s.online,0) online FROM routers r
                 LEFT JOIN router_state s ON s.router_id=r.id ORDER BY r.name""")
    for r in rs:
        r["cells"] = cell.get(r["id"], {})
        r["error"] = latency.get_error(r["id"]) if sc is None else ""
    if sc is not None:
        rs = [r for r in rs if r["id"] in sc]
    return {"targets": tg, "routers": rs, "warn": s["latency_warn"], "bad": s["latency_bad"], "enabled": s["latency_enabled"], "now": now}


@app.get("/api/routers/{rid}/latency/live", dependencies=[Depends(auth)])
def router_latency_live(rid: int, since: int = 0):
    r = _load(rid)
    try:
        ss = latency.get_live(r)
    except latency.LiveBusy as e:
        raise HTTPException(429, str(e))
    st = db.get_settings()
    return {"targets": ss.snapshot(since), "error": ss.err, "warn": st["latency_warn"], "bad": st["latency_bad"], "now": int(time.time() * 1000)}


# ------------------------------------------------------------------ akun klien (portal)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{3,40}$")
CHAT_RE = re.compile(r"^-?\d{5,20}$")
NOTIFY_OPTS = ("all", "down", "off")


def _norm_chats(s):
    ids = [x.strip() for x in (s or "").replace(";", ",").split(",") if x.strip()]
    for i in ids:
        if not CHAT_RE.match(i):
            raise HTTPException(400, f"Chat ID Telegram tidak valid: '{i}' (hanya angka, boleh diawali minus)")
    return ",".join(dict.fromkeys(ids))


def _user_row(u):
    ids = [r["router_id"] for r in db.q("SELECT router_id FROM user_routers WHERE user_id=?", (u["id"],))]
    return {"id": u["id"], "username": u["username"], "display_name": u["display_name"], "enabled": bool(u["enabled"]),
            "tg_chat_ids": u["tg_chat_ids"], "notify": u["notify"], "last_login": u["last_login"], "created_at": u["created_at"],
            "router_ids": ids}


class UserIn(BaseModel):
    username: str
    display_name: str = ""
    password: Optional[str] = None
    enabled: bool = True
    tg_chat_ids: str = ""
    notify: str = "all"
    router_ids: list[int] = []


def _check_user_in(b: UserIn):
    if b.notify not in NOTIFY_OPTS:
        raise HTTPException(400, "Pilihan notifikasi tidak valid")
    ids = list(dict.fromkeys(b.router_ids))
    if ids:
        have = {r["id"] for r in db.q(f"SELECT id FROM routers WHERE id IN {_in(ids)}", ids)}
        if set(ids) - have:
            raise HTTPException(400, "Ada router yang tidak ditemukan")
    return ids, _norm_chats(b.tg_chat_ids)


def _set_links(uid, ids):
    db.ex("DELETE FROM user_routers WHERE user_id=?", (uid,))
    if ids:
        c = db.conn()
        c.executemany("INSERT INTO user_routers(user_id,router_id) VALUES(?,?)", [(uid, i) for i in ids])
        c.commit()


@app.get("/api/users", dependencies=[Depends(auth)])
def users_list():
    return [_user_row(u) for u in db.q("SELECT * FROM users WHERE role='client' ORDER BY username")]


@app.post("/api/users", dependencies=[Depends(auth)])
def users_add(b: UserIn, request: Request):
    uname = b.username.strip()
    if not USERNAME_RE.match(uname):
        raise HTTPException(400, "Username 3–40 karakter: huruf, angka, titik, minus, underscore, @")
    if uname.lower() == ADMIN_USER.lower() or db.q1("SELECT 1 FROM users WHERE username=?", (uname,)):
        raise HTTPException(400, "Username sudah dipakai")
    if b.password is not None and b.password != "" and len(b.password) < 8:
        raise HTTPException(400, "Password minimal 8 karakter")
    ids, chats = _check_user_in(b)
    pw = b.password or vpn.gen_password(14)
    cur = db.ex("""INSERT INTO users(username,display_name,password_hash,role,enabled,tg_chat_ids,notify,created_at)
                   VALUES(?,?,?,'client',?,?,?,?)""",
                (uname, b.display_name.strip()[:80], hash_password(pw), int(b.enabled), chats, b.notify, int(time.time())))
    _set_links(cur.lastrowid, ids)
    _audit(request, f"Akun klien dibuat: {uname} ({len(ids)} router)")
    return {"id": cur.lastrowid, "username": uname, "password": pw}


@app.put("/api/users/{uid}", dependencies=[Depends(auth)])
def users_update(uid: int, b: UserIn, request: Request):
    u = db.q1("SELECT * FROM users WHERE id=? AND role='client'", (uid,))
    if not u:
        raise HTTPException(404, "Akun tidak ditemukan")
    uname = b.username.strip()
    if not USERNAME_RE.match(uname):
        raise HTTPException(400, "Username tidak valid")
    if uname.lower() == ADMIN_USER.lower() or db.q1("SELECT 1 FROM users WHERE username=? AND id!=?", (uname, uid)):
        raise HTTPException(400, "Username sudah dipakai")
    ids, chats = _check_user_in(b)
    pw_new = None
    if b.password:
        if len(b.password) < 8:
            raise HTTPException(400, "Password minimal 8 karakter")
        pw_new = b.password
    db.ex("UPDATE users SET username=?, display_name=?, enabled=?, tg_chat_ids=?, notify=? WHERE id=?",
          (uname, b.display_name.strip()[:80], int(b.enabled), chats, b.notify, uid))
    if pw_new:
        db.ex("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw_new), uid))
    _set_links(uid, ids)
    _audit(request, f"Akun klien diubah: {uname}" + (" (password direset)" if pw_new else "") + ("" if b.enabled else " (dinonaktifkan)"))
    return {"ok": True}


@app.delete("/api/users/{uid}", dependencies=[Depends(auth)])
def users_delete(uid: int, request: Request):
    u = db.q1("SELECT * FROM users WHERE id=? AND role='client'", (uid,))
    if not u:
        raise HTTPException(404, "Akun tidak ditemukan")
    db.ex("DELETE FROM user_routers WHERE user_id=?", (uid,))
    db.ex("DELETE FROM users WHERE id=?", (uid,))
    _audit(request, f"Akun klien dihapus: {u['username']}")
    return {"ok": True}


@app.post("/api/users/{uid}/test-notify", dependencies=[Depends(auth)])
def users_test_notify(uid: int):
    u = db.q1("SELECT * FROM users WHERE id=? AND role='client'", (uid,))
    if not u:
        raise HTTPException(404, "Akun tidak ditemukan")
    ids = [x for x in u["tg_chat_ids"].split(",") if x]
    if not ids:
        raise HTTPException(400, "Akun ini belum mengisi Chat ID Telegram")
    ok, err = tg.send(f"✅ <b>Tes notifikasi</b>\nHalo {tg.esc(u['display_name'] or u['username'])}, notifikasi untuk router Anda sudah aktif.", chat_ids=ids)
    return {"ok": ok, "error": err}


# ---- klien: profil sendiri
def _client_only(request: Request):
    c = _ctx(request)
    if c["role"] != "client":
        raise HTTPException(403, "Hanya untuk akun klien (admin diatur lewat .env)")
    return c


class ProfileIn(BaseModel):
    tg_chat_ids: str = ""
    notify: str = "all"


class PasswordIn(BaseModel):
    current: str
    new: str


@app.get("/api/me/profile")
def my_profile(request: Request):
    c = _client_only(request)
    u = db.q1("SELECT * FROM users WHERE id=?", (c["uid"],))
    s = db.get_settings()
    return {"username": u["username"], "display_name": u["display_name"], "tg_chat_ids": u["tg_chat_ids"], "notify": u["notify"],
            "telegram_ready": bool(s["tg_enabled"] and s["tg_token"])}


@app.put("/api/me/profile")
def my_profile_save(b: ProfileIn, request: Request):
    c = _client_only(request)
    if b.notify not in NOTIFY_OPTS:
        raise HTTPException(400, "Pilihan notifikasi tidak valid")
    db.ex("UPDATE users SET tg_chat_ids=?, notify=? WHERE id=?", (_norm_chats(b.tg_chat_ids), b.notify, c["uid"]))
    return {"ok": True}


@app.put("/api/me/password")
def my_password(b: PasswordIn, request: Request):
    c = _client_only(request)
    u = db.q1("SELECT * FROM users WHERE id=?", (c["uid"],))
    if not verify_password(b.current, u["password_hash"]):
        raise HTTPException(400, "Password saat ini salah")
    if len(b.new) < 8:
        raise HTTPException(400, "Password baru minimal 8 karakter")
    db.ex("UPDATE users SET password_hash=? WHERE id=?", (hash_password(b.new), c["uid"]))
    return {"ok": True}


@app.post("/api/me/test-notify")
def my_test_notify(request: Request):
    c = _client_only(request)
    u = db.q1("SELECT * FROM users WHERE id=?", (c["uid"],))
    ids = [x for x in u["tg_chat_ids"].split(",") if x]
    if not ids:
        raise HTTPException(400, "Isi dan simpan Chat ID Telegram dulu")
    ok, err = tg.send("✅ <b>Tes notifikasi</b>\nNotifikasi untuk router Anda sudah aktif.", chat_ids=ids)
    return {"ok": ok, "error": err}


# ------------------------------------------------------------------ endpoint kecil yang aman untuk klien
@app.get("/api/thresholds", dependencies=[Depends(auth_any)])
def thresholds():
    s = db.get_settings()
    return {k: s[k] for k in ("cpu_limit", "mem_limit", "temp_limit", "latency_limit")}


@app.get("/api/my/routers", dependencies=[Depends(auth_any)])
def my_routers(request: Request):
    sc = scope(request)
    rows = db.q("SELECT id, name, group_name FROM routers ORDER BY name")
    return rows if sc is None else [r for r in rows if r["id"] in sc]
