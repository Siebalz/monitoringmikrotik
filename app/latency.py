"""Network Latency ala WiFiman: tiap router mem-ping Google, YouTube, game, sosmed, dan gateway-nya sendiri.

Ping dijalankan DARI router (lewat RouterOS API), jadi angkanya = kualitas internet di lokasi router itu.
Catatan: Mobile Legends / PUBG dipantau lewat domain situs resminya (bukan IP server game, yang
bervariasi per region). Ganti target di Pengaturan bila Anda tahu host/IP server game yang dipakai.
"""
import re, time, math, random, threading, logging
from concurrent.futures import ThreadPoolExecutor
from . import db, ros
from .security import dec
from .diag import ADDR_RE

log = logging.getLogger("latency")
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_errors = {}          # router_id -> pesan error terakhir (mis. izin kurang)
_err_lock = threading.Lock()


def parse_targets(s):
    """'Nama:host,Nama2:host2' -> [(nama, host)] (host tak valid dibuang)."""
    out = []
    for part in (s or "").split(","):
        name, _, host = part.strip().partition(":")
        name, host = name.strip(), host.strip()
        if name and ADDR_RE.match(host):
            out.append((name[:40], host))
    return out


def targets_for(s):
    t = parse_targets(s["latency_targets"])
    if s["latency_gateway"]:
        t.append(("Gateway", "@gateway"))
    return t


def get_error(rid):
    return _errors.get(rid, "")


# ------------------------------------------------------------------ probe nyata
def _gateway(c):
    try:
        rows, _ = c.call("/ip/route/print", queries=["?dst-address=0.0.0.0/0"], proplist=["gateway", "disabled", "active", "distance"])
    except ros.RosError:
        return None
    rows = [r for r in rows if r.get("disabled") != "true" and r.get("active", "true") != "false"]
    rows.sort(key=lambda r: int(r.get("distance", 1) or 1))
    for r in rows:
        g = (r.get("gateway") or "").split("%")[0].strip()
        if IP_RE.match(g):
            return g
    return None


def _ping(c, host):
    """Return (ms|None, loss%, ip, err). err: '' | 'dns' | 'timeout' | teks error lain.
    RosError dilempar ulang hanya untuk masalah izin/koneksi."""
    try:
        rows, _ = c.call("/ping", {"address": host, "count": "3", "interval": "200ms"})
    except ros.RosError as e:
        m = str(e)
        if "not enough permissions" in m.lower() or "Timeout" in m or "ditutup" in m:
            raise
        return None, 100.0, "", ("dns" if re.search(r"dns|no such host|resolve|no address", m, re.I) else m[:120])
    ip = next((r["host"] for r in rows if IP_RE.match(r.get("host", ""))), "")
    last = rows[-1] if rows else {}
    try:
        loss = float(last.get("packet-loss", 100))
    except ValueError:
        loss = 100.0
    if loss >= 100:
        # tanpa balasan sama sekali; nama host yang gagal di-resolve biasanya tidak punya IP
        status = next((r.get("status", "") for r in rows if r.get("status")), "")
        return None, 100.0, ip, ("dns" if (not ip and not IP_RE.match(host) and re.search(r"dns|resolv|no such", status, re.I)) else "timeout")
    ms = ros.parse_ms(last.get("avg-rtt"))
    if ms is None:                         # fallback: rata-rata dari balasan individual
        vals = [x for x in (ros.parse_ms(r.get("time")) for r in rows) if x is not None]
        ms = sum(vals) / len(vals) if vals else None
    return ms, loss, ip, ("" if ms is not None else "format waktu tak dikenali: " + str(last.get("avg-rtt")))


def probe_router(r, s=None):
    """Satu siklus ping dari satu router; simpan ke DB. Return jumlah target terukur."""
    s = s or db.get_settings()
    tg = targets_for(s)
    if not tg:
        return 0
    now = int(time.time())
    rid = r["id"]
    res = []
    try:
        if r["host"].startswith("demo"):
            res = [(n, *_mock(r, n, h)) for n, h in tg]
        else:
            c = ros.connect(r["host"], r["port"], r["use_ssl"], r["username"], dec(r["password_enc"]), timeout=15 + 3 * len(tg))
            try:
                gw = None
                for n, h in tg:
                    if h == "@gateway":
                        gw = gw or _gateway(c)
                        if not gw:
                            continue                # gateway berupa interface (PPPoE/LTE): lewati
                        h = gw
                    res.append((n, *_ping(c, h)))
            finally:
                c.close()
        with _err_lock:
            _errors.pop(rid, None)
    except ros.RosError as e:
        msg = str(e)
        with _err_lock:
            _errors[rid] = ("Izin user API kurang untuk ping. Jalankan di router: /user group set monitor policy=read,api,test"
                            if "not enough permissions" in msg.lower() else msg)
        return 0
    if res:
        c2 = db.conn()
        c2.executemany("INSERT INTO latency(router_id,ts,target,ms,loss,ip,err) VALUES(?,?,?,?,?,?,?)",
                       [(rid, now, n, ms, loss, ip, err) for n, ms, loss, ip, err in res])
        c2.commit()
    return len(res)


def _mock(r, name, host):
    base = {"Google": 20, "YouTube": 22, "Mobile Legends": 48, "PUBG Mobile": 62, "Instagram": 30, "Facebook": 12, "X": 14, "Gateway": 3}
    rid, now = r["id"], time.time()
    rnd = random.Random(hash((rid, name, int(now // 20))) & 0xFFFFFFFF)
    if r["host"].startswith("demo-down"):
        raise ros.RosError("down")
    b = base.get(name, 35) + rid * 3 % 9
    if r["host"].startswith("demo-lag") and name != "Gateway":
        b += 150
    ms = max(1.0, b * (1 + 0.25 * math.sin(now / 300 + rid + len(name))) + rnd.uniform(-2, 6))
    loss = 100.0 if (r["host"].startswith("demo-flap") and name == "PUBG Mobile" and int(now // 300) % 2) else 0.0
    ip = "192.168.101.1" if name == "Gateway" else f"{(hash(name) % 200) + 20}.{(hash(name) >> 8) % 250}.{rid}.{(hash(name) >> 16) % 250 + 1}"
    return (None if loss >= 100 else ms), loss, ip, ("timeout" if loss >= 100 else "")


def seed_demo():
    """Isi riwayat 1 jam supaya grafik demo langsung terlihat."""
    s = db.get_settings()
    if db.q1("SELECT 1 FROM latency LIMIT 1"):
        return
    now = int(time.time())
    c = db.conn()
    for r in db.q("SELECT * FROM routers WHERE host LIKE 'demo%'"):
        if r["host"].startswith("demo-down"):
            continue
        rows = []
        for ts in range(now - 3600, now - 30, 60):
            for n, h in targets_for(s):
                rnd = random.Random(hash((r["id"], n, ts)) & 0xFFFFFFFF)
                base = {"Google": 20, "YouTube": 22, "Mobile Legends": 48, "PUBG Mobile": 62, "Instagram": 30, "Facebook": 12, "Gateway": 3}.get(n, 35) + r["id"] * 3 % 9
                if r["host"].startswith("demo-lag") and n != "Gateway":
                    base += 150
                rows.append((r["id"], ts, n, max(1, base * (1 + 0.25 * math.sin(ts / 300 + r["id"] + len(n))) + rnd.uniform(-2, 6)), 0.0, "", ""))
        c.executemany("INSERT INTO latency(router_id,ts,target,ms,loss,ip,err) VALUES(?,?,?,?,?,?,?)", rows)
    c.commit()


# ------------------------------------------------------------------ scheduler
def _safe(r):
    try:
        probe_router(r)
    except Exception:
        log.exception("latency %s", r.get("name"))


def probe_all():
    rs = db.q("""SELECT r.* FROM routers r JOIN router_state s ON s.router_id=r.id WHERE r.enabled=1 AND s.online=1""")
    if rs:
        with ThreadPoolExecutor(max_workers=min(16, len(rs))) as ex:
            list(ex.map(_safe, rs))


def cleanup():
    s = db.get_settings()
    db.ex("DELETE FROM latency WHERE ts < ?", (int(time.time()) - int(s["latency_retention_days"]) * 86400,))


def loop():
    last_clean = 0
    time.sleep(8)                                   # beri waktu poller pertama mengisi status online
    while True:
        t0 = time.time()
        s = db.get_settings()
        try:
            if s["latency_enabled"]:
                probe_all()
            if t0 - last_clean > 3600:
                cleanup()
                last_clean = t0
        except Exception:
            log.exception("latency loop")
        time.sleep(max(1.0, max(20, int(s["latency_interval"])) - (time.time() - t0)))


def start():
    threading.Thread(target=loop, daemon=True, name="latency").start()
