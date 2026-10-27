import json, re, time, threading, logging
from concurrent.futures import ThreadPoolExecutor
from . import db, ros, telegram as tg
from .security import dec
from .config import DEMO

log = logging.getLogger("poller")

_prev = {}        # (rid, iface) -> (ts, rx, tx)
_prev_uptime = {}  # rid -> uptime
_cnt = {}         # (rid, key) -> consecutive count
_lock = threading.Lock()

DYNAMIC_TYPES = {"l2tp-in", "sstp-in", "ovpn-in", "pptp-in", "pppoe-in", "wg-peer"}
WAN_TYPES = {"ether", "pppoe-out", "lte", "wlan", "wifi", "l2tp-out", "sstp-out", "ovpn-out", "wg", "wireguard", "sfp", "vlan"}
EMOJI = {"critical": "🔴", "warning": "🟠", "ok": "🟢", "info": "ℹ️"}


def _sess_kinds(s):
    return tuple(k for k in ("pppoe", "hotspot", "vpn") if s.get(f"sess_{k}", "off") != "off")


def _fetch(router, s=None):
    kinds = _sess_kinds(s or db.get_settings())
    if router["host"].startswith("demo") or (DEMO and router["host"].startswith("demo")):
        return ros.mock_fetch(router, "", kinds)
    return ros.fetch(router, dec(router["password_enc"]), kinds)


# ------------------------------------------------------------------ alerts
def _raise_or_clear(r, key, cond, level, fire_msg, clear_msg, tg_fire, tg_clear, sustain=1, hold=False, settings=None):
    """Mesin alert: dedup, sustain, hysteresis (hold), repeat, recovery."""
    s = settings
    rid = r["id"]
    now = int(time.time())
    row = db.q1("SELECT * FROM alert_state WHERE router_id=? AND key=?", (rid, key))
    ck = (rid, key)
    if cond:
        _cnt[ck] = _cnt.get(ck, 0) + 1
        if not row and _cnt[ck] >= sustain:
            db.ex("INSERT OR REPLACE INTO alert_state(router_id,key,level,since,last_sent,message) VALUES(?,?,?,?,?,?)",
                  (rid, key, level, now, now, fire_msg))
            db.add_event(level, key.split(":")[0], fire_msg, rid, r["name"])
            tg.notify(r, key, tg_fire, s)
        elif row and s["repeat_minutes"] and now - row["last_sent"] >= s["repeat_minutes"] * 60:
            db.ex("UPDATE alert_state SET last_sent=? WHERE router_id=? AND key=?", (now, rid, key))
            tg.notify(r, key, "🔁 <b>Masih aktif</b>\n" + tg_fire, s)
    else:
        _cnt[ck] = 0
        if row and not hold:
            db.ex("DELETE FROM alert_state WHERE router_id=? AND key=?", (rid, key))
            dur = tg.fmt_dur(now - row["since"])
            db.add_event("ok", key.split(":")[0] + "_ok", f"{clear_msg} (selama {dur})", rid, r["name"])
            if s["notify_recovery"]:
                tg.notify(r, key, tg_clear.replace("{dur}", dur), s)


def _clear_all(r):
    db.ex("DELETE FROM alert_state WHERE router_id=? AND key!='offline'", (r["id"],))


def evaluate(r, st, d, s, info_changed_reboot):
    """st: router_state dict (setelah update), d: data poll (None jika gagal)."""
    name, host = tg.esc(r["name"]), tg.esc(r["host"])
    head = f"<b>{name}</b> ({host})"
    if not r["alerts_enabled"]:
        db.ex("DELETE FROM alert_state WHERE router_id=?", (r["id"],))
        return
    # ---- offline
    is_down = (not st["online"]) and st["fail_count"] >= s["offline_after"]
    since = tg.fmt_ts(st["down_since"]) if st.get("down_since") else tg.fmt_ts()
    _raise_or_clear(
        r, "offline", is_down, "critical",
        f"Router tidak merespon ({st['fail_count']}x gagal)",
        "Router kembali online",
        f"🔴 <b>ROUTER DOWN</b>\n{head}\nTidak merespon sejak {since}\n<i>{tg.esc(st.get('last_error', ''))}</i>",
        f"🟢 <b>ROUTER ONLINE KEMBALI</b>\n{head}\nSempat down selama {{dur}}",
        sustain=1, settings=s)
    if not st["online"] or d is None:
        return
    # ---- reboot
    if info_changed_reboot and s["notify_reboot"]:
        db.add_event("info", "reboot", "Router reboot (uptime reset)", r["id"], r["name"])
        tg.notify(r, "reboot", f"🔄 <b>ROUTER REBOOT</b>\n{head}\nUptime sekarang {tg.fmt_dur(d['uptime'])}", s)

    def lim(k):
        v = r.get(k)
        return float(v) if v is not None else float(s[k])

    sus = max(1, int(s["sustain"]))
    cpu, memp, temp, lat = d["cpu"], d.get("mem_pct"), d.get("temp"), d.get("latency")
    L = lim("cpu_limit")
    _raise_or_clear(r, "cpu", cpu is not None and cpu >= L, "warning", f"CPU tinggi {cpu:.0f}% (batas {L:.0f}%)",
                    "CPU kembali normal", f"🟠 <b>CPU TINGGI</b>\n{head}\nCPU {cpu:.0f}% (batas {L:.0f}%)",
                    f"🟢 <b>CPU normal</b>\n{head}\nCPU {cpu:.0f}%", sus, hold=cpu is not None and cpu >= L - 5, settings=s)
    L = lim("mem_limit")
    if memp is not None:
        _raise_or_clear(r, "mem", memp >= L, "warning", f"RAM tinggi {memp:.0f}% (batas {L:.0f}%)",
                        "RAM kembali normal", f"🟠 <b>RAM TINGGI</b>\n{head}\nRAM {memp:.0f}% (batas {L:.0f}%)",
                        f"🟢 <b>RAM normal</b>\n{head}\nRAM {memp:.0f}%", sus, hold=memp >= L - 5, settings=s)
    L = lim("temp_limit")
    if temp is not None:
        _raise_or_clear(r, "temp", temp >= L, "warning", f"Suhu tinggi {temp:.0f}°C (batas {L:.0f}°C)",
                        "Suhu kembali normal", f"🌡️ <b>SUHU TINGGI</b>\n{head}\nSuhu {temp:.0f}°C (batas {L:.0f}°C)",
                        f"🟢 <b>Suhu normal</b>\n{head}\nSuhu {temp:.0f}°C", sus, hold=temp >= L - 3, settings=s)
    L = lim("latency_limit")
    if lat is not None:
        _raise_or_clear(r, "latency", lat >= L, "warning", f"Latency tinggi {lat:.0f} ms (batas {L:.0f} ms)",
                        "Latency kembali normal", f"🐢 <b>LATENCY TINGGI</b>\n{head}\n{lat:.0f} ms (batas {L:.0f} ms)",
                        f"🟢 <b>Latency normal</b>\n{head}\n{lat:.0f} ms", sus, hold=lat >= L * 0.85, settings=s)
    # ---- interface yang dipantau
    watched = {x["name"] for x in db.q("SELECT name FROM watched_ifaces WHERE router_id=?", (r["id"],))}
    cur = {i["name"]: i for i in d["ifaces"]}
    for w in watched:
        i = cur.get(w)
        down = (i is None) or (not i["running"] and not i["disabled"])
        _raise_or_clear(r, f"iface:{w}", down, "critical", f"Interface {w} DOWN", f"Interface {w} UP kembali",
                        f"🔌 <b>INTERFACE DOWN</b>\n{head}\nInterface <b>{tg.esc(w)}</b> link down",
                        f"🟢 <b>Interface UP</b>\n{head}\n<b>{tg.esc(w)}</b> kembali up (down {{dur}})", 2, settings=s)
    # bersihkan alert iface yg sudah tidak dipantau
    for a in db.q("SELECT key FROM alert_state WHERE router_id=? AND key LIKE 'iface:%'", (r["id"],)):
        if a["key"][6:] not in watched:
            db.ex("DELETE FROM alert_state WHERE router_id=? AND key=?", (r["id"], a["key"]))


# ------------------------------------------------------------------ sesi PPPoE / Hotspot / VPN
import fnmatch

_SESS_LABEL = {"pppoe": ("🟢", "🔴", "PPPoE"), "hotspot": ("📶", "📴", "Hotspot"), "vpn": ("🔐", "🔓", "VPN")}


def _patterns(s):
    return [p.strip().lower() for p in re.split(r"[,\n;]+", s.get("sess_watch") or "") if p.strip()]


def _wanted(mode, name, pats):
    if mode == "all":
        return True
    return mode == "watch" and any(fnmatch.fnmatchcase(name.lower(), p) for p in pats)


def process_sessions(r, d, s, now=None):
    """Bandingkan sesi aktif dengan poll sebelumnya -> event + notifikasi login/putus.
    - poll pertama (per router & jenis) hanya mencatat baseline, tanpa notifikasi
    - putus baru diumumkan setelah hilang `sess_grace_polls` poll berturut-turut (anti-flapping)
    - banyak sesi berubah sekaligus diringkas jadi 1 pesan (mis. uplink/OLT bermasalah)"""
    now = int(now or time.time())
    rid = r["id"]
    sess = (d or {}).get("sessions") or {}
    head = f"<b>{tg.esc(r['name'])}</b> ({tg.esc(r['host'])})"
    grace = max(1, int(s["sess_grace_polls"]))
    mass = max(2, int(s["sess_mass_drop"]))
    pats = _patterns(s)
    notify_on = bool(r["alerts_enabled"])
    for kind in ("pppoe", "hotspot", "vpn"):
        mode = s.get(f"sess_{kind}", "off")
        if mode == "off":                                        # dimatikan: buang catatan lama supaya tak muncul 'putus' palsu saat diaktifkan lagi
            if db.q1("SELECT 1 FROM sess_seed WHERE router_id=? AND kind=?", (rid, kind)):
                db.ex("DELETE FROM sessions WHERE router_id=? AND kind=?", (rid, kind))
                db.ex("DELETE FROM sess_seed WHERE router_id=? AND kind=?", (rid, kind))
            continue
        cur = sess.get(kind)
        if cur is None:                                          # gagal ambil data -> jangan diff
            continue
        seeded = bool(db.q1("SELECT 1 FROM sess_seed WHERE router_id=? AND kind=?", (rid, kind)))
        key = (lambda x: f"{x['name']}|{x['caller']}") if kind == "hotspot" else (lambda x: x["name"])
        curmap = {key(x): x for x in cur}
        prev = {x["skey"]: x for x in db.q("SELECT * FROM sessions WHERE router_id=? AND kind=?", (rid, kind))}
        ups, downs = [], []
        c = db.conn()
        for k, x in curmap.items():
            if k in prev:
                c.execute("UPDATE sessions SET address=?, caller=?, uptime=?, last_seen=?, miss=0 WHERE router_id=? AND kind=? AND skey=?",
                          (x["address"], x["caller"], x["uptime"], now, rid, kind, k))
            else:
                first = now - ros.parse_dur(x["uptime"]) if x["uptime"] else now
                c.execute("INSERT OR REPLACE INTO sessions(router_id,kind,skey,name,address,caller,uptime,first_seen,last_seen,miss) VALUES(?,?,?,?,?,?,?,?,?,0)",
                          (rid, kind, k, x["name"], x["address"], x["caller"], x["uptime"], first, now))
                ups.append(x)
        for k, p in prev.items():
            if k in curmap:
                continue
            if p["miss"] + 1 >= grace:
                c.execute("DELETE FROM sessions WHERE router_id=? AND kind=? AND skey=?", (rid, kind, k))
                downs.append(p)
            else:
                c.execute("UPDATE sessions SET miss=miss+1 WHERE router_id=? AND kind=? AND skey=?", (rid, kind, k))
        c.execute("INSERT OR IGNORE INTO sess_seed(router_id,kind) VALUES(?,?)", (rid, kind))
        c.commit()
        if not seeded:
            continue                                             # baseline pertama: tanpa notifikasi
        ups = [x for x in ups if _wanted(mode, x["name"], pats)] if s["sess_notify_login"] else []
        downs = [x for x in downs if _wanted(mode, x["name"], pats)]
        icon_up, icon_dn, label = _SESS_LABEL[kind]
        for lst, is_up in ((ups, True), (downs, False)):
            if not lst:
                continue
            verb = "tersambung" if is_up else "putus"
            if len(lst) >= mass:
                names = ", ".join(x["name"] for x in lst[:10]) + (f" (+{len(lst) - 10} lainnya)" if len(lst) > 10 else "")
                db.add_event("warning" if not is_up else "info", "session", f"{len(lst)} sesi {label} {verb} bersamaan: {names}", rid, r["name"])
                if notify_on:
                    ttl = f"{icon_up} <b>{len(lst)} sesi {label} tersambung kembali</b>" if is_up else f"⚠️ <b>{len(lst)} sesi {label} putus bersamaan</b>"
                    note = "" if is_up else "\nKemungkinan gangguan uplink/perangkat di router ini."
                    tg.notify(r, "session", f"{ttl}\n{head}\n<code>{tg.esc(names)}</code>{note}", s)
                continue
            for x in lst:
                if is_up:
                    msg = f"{label} {x['name']} tersambung" + (f" ({x['address']})" if x["address"] else "")
                    text = f"{icon_up} <b>{label} tersambung</b>\n{head}\nUser: <code>{tg.esc(x['name'])}</code>" + (f"\nIP: {tg.esc(x['address'])}" if x["address"] else "")
                else:
                    dur = tg.fmt_dur(max(0, now - x["first_seen"]))
                    msg = f"{label} {x['name']} putus (online {dur})"
                    text = f"{icon_dn} <b>{label} putus</b>\n{head}\nUser: <code>{tg.esc(x['name'])}</code>\nOnline selama {dur}" + (f"\nIP terakhir: {tg.esc(x['address'])}" if x["address"] else "")
                db.add_event("info" if is_up else "warning", "session", msg, rid, r["name"])
                if notify_on:
                    tg.notify(r, "session", text, s)


# ------------------------------------------------------------------ polling
def poll_router(r, s=None):
    s = s or db.get_settings()
    rid = r["id"]
    now = int(time.time())
    d, err = None, ""
    try:
        d = _fetch(r, s)
    except Exception as e:
        err = str(e)[:300]
    old = db.q1("SELECT * FROM router_state WHERE router_id=?", (rid,)) or {}
    fail = 0 if d else int(old.get("fail_count") or 0) + 1
    reboot = False
    st = {"router_id": rid, "online": 1 if d else 0, "fail_count": fail, "last_poll": now,
          "last_ok": now if d else old.get("last_ok"), "last_error": err,
          "down_since": None if d else (old.get("down_since") or now)}

    if d:
        # --- hitung rate per interface
        rates, total_rx, total_tx = {}, 0.0, 0.0
        for i in d["ifaces"]:
            k = (rid, i["name"])
            p = _prev.get(k)
            rx_bps = tx_bps = None
            if p and now > p[0] and i["rx"] >= p[1] and i["tx"] >= p[2]:
                dt = now - p[0]
                rx_bps, tx_bps = (i["rx"] - p[1]) * 8 / dt, (i["tx"] - p[2]) * 8 / dt
            _prev[k] = (now, i["rx"], i["tx"])
            rates[i["name"]] = (rx_bps, tx_bps)
            i["rx_bps"], i["tx_bps"] = rx_bps, tx_bps
        # --- pilih interface uplink/WAN
        wan = [x.strip() for x in (r.get("wan_iface") or "").split(",") if x.strip()]
        auto = False
        if not wan:
            cands = [i for i in d["ifaces"] if i["type"] in WAN_TYPES and not i["disabled"] and i["type"] != "vlan"]
            if cands:
                wan = [max(cands, key=lambda i: i["rx"] + i["tx"])["name"]]
                auto = True
        for w in wan:
            a, b = rates.get(w, (None, None))
            total_rx += a or 0
            total_tx += b or 0
        have_rate = any(rates.get(w, (None, None))[0] is not None for w in wan)
        rx_bps = total_rx if have_rate else None
        tx_bps = total_tx if have_rate else None
        # --- reboot?
        pu = _prev_uptime.get(rid)
        if pu is not None and d["uptime"] + 10 < pu:
            reboot = True
        _prev_uptime[rid] = d["uptime"]
        latest = {k: d[k] for k in ("cpu", "mem_pct", "mem_used", "mem_total", "hdd_pct", "temp", "volt", "latency",
                                     "uptime", "ppp", "dhcp", "wifi", "conn")}
        latest.update(rx_bps=rx_bps, tx_bps=tx_bps, wan=wan, wan_auto=auto, ifaces=d["ifaces"])
        st["info"] = json.dumps(d["info"])
        st["latest"] = json.dumps(latest)
        db.ex("""INSERT INTO metrics(router_id,ts,online,cpu,mem_pct,mem_used,mem_total,hdd_pct,temp,volt,rx_bps,tx_bps,latency,ppp,dhcp,wifi,conn)
                 VALUES(?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (rid, now, d["cpu"], d["mem_pct"], d["mem_used"], d["mem_total"], d["hdd_pct"], d["temp"], d["volt"],
               rx_bps, tx_bps, d["latency"], d["ppp"], d["dhcp"], d["wifi"], d["conn"]))
        rows = [(rid, now, i["name"], i["rx_bps"], i["tx_bps"]) for i in d["ifaces"]
                if not i["disabled"] and i["type"] not in DYNAMIC_TYPES and i["rx_bps"] is not None]
        if rows:
            c = db.conn()
            c.executemany("INSERT INTO iface_metrics(router_id,ts,name,rx_bps,tx_bps) VALUES(?,?,?,?,?)", rows)
            c.commit()
    else:
        db.ex("INSERT INTO metrics(router_id,ts,online) VALUES(?,?,0)", (rid, now))
        st["info"] = old.get("info") or "{}"
        st["latest"] = old.get("latest") or "{}"
    db.ex("""INSERT INTO router_state(router_id,online,fail_count,last_ok,last_poll,last_error,down_since,info,latest)
             VALUES(:router_id,:online,:fail_count,:last_ok,:last_poll,:last_error,:down_since,:info,:latest)
             ON CONFLICT(router_id) DO UPDATE SET online=:online,fail_count=:fail_count,last_ok=:last_ok,last_poll=:last_poll,
             last_error=:last_error,down_since=:down_since,info=:info,latest=:latest""", st)
    try:
        evaluate(r, st, d, s, reboot)
    except Exception:
        log.exception("evaluate %s", r["name"])
    if d:
        try:
            process_sessions(r, d, s, now)
        except Exception:
            log.exception("sessions %s", r["name"])
    return st


def _safe(r):
    try:
        poll_router(r)
    except Exception:
        log.exception("poll %s", r.get("name"))


def poll_all():
    routers = db.q("SELECT * FROM routers WHERE enabled=1")
    if routers:
        with ThreadPoolExecutor(max_workers=min(32, len(routers))) as ex:
            list(ex.map(_safe, routers))


def cleanup():
    s = db.get_settings()
    now = int(time.time())
    db.ex("DELETE FROM metrics WHERE ts < ?", (now - int(s["metrics_retention_days"]) * 86400,))
    db.ex("DELETE FROM iface_metrics WHERE ts < ?", (now - int(s["iface_retention_days"]) * 86400,))
    db.ex("DELETE FROM events WHERE ts < ?", (now - 180 * 86400,))


def loop():
    last_clean = 0
    while True:
        t0 = time.time()
        try:
            poll_all()
            if t0 - last_clean > 3600:
                cleanup()
                last_clean = t0
        except Exception:
            log.exception("poll loop")
        iv = max(10, int(db.get_settings()["poll_interval"]))
        time.sleep(max(1.0, iv - (time.time() - t0)))


def start():
    threading.Thread(target=loop, daemon=True, name="poller").start()
