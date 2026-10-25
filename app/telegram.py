import time, threading, html, logging, datetime, re
import requests
from zoneinfo import ZoneInfo
from . import db
from .config import TZ_NAME

log = logging.getLogger("tg")
TZ = ZoneInfo(TZ_NAME)


def fmt_ts(ts=None):
    return datetime.datetime.fromtimestamp(ts or time.time(), TZ).strftime("%d %b %Y %H:%M:%S")


def fmt_dur(sec):
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    if d:
        return f"{d}h {h}j {m}m"
    if h:
        return f"{h}j {m}m"
    if m:
        return f"{m}m {s}d"
    return f"{s}d"


def esc(s):
    return html.escape(str(s))


def _chat_ids(s):
    return [x.strip() for x in (s.get("tg_chat_ids") or "").replace(";", ",").split(",") if x.strip()]


def send(text, settings=None, chat_ids=None, silent=False):
    """Kirim pesan HTML ke semua chat id. Return (ok, error)."""
    s = settings or db.get_settings()
    if not s.get("tg_enabled") or not s.get("tg_token"):
        return False, "Telegram belum diaktifkan / token kosong"
    ids = chat_ids or _chat_ids(s)
    if not ids:
        return False, "Chat ID kosong"
    err = None
    ok_any = False
    for cid in ids:
        try:
            r = requests.post(f"https://api.telegram.org/bot{s['tg_token']}/sendMessage",
                              json={"chat_id": cid, "text": text[:4000], "parse_mode": "HTML",
                                    "disable_web_page_preview": True, "disable_notification": silent}, timeout=15)
            j = r.json()
            if j.get("ok"):
                ok_any = True
            else:
                err = j.get("description", "gagal")
        except Exception as e:
            err = str(e)
    return ok_any, err


def send_async(text, **kw):
    threading.Thread(target=send, args=(text,), kwargs=kw, daemon=True).start()


def _client_text(text, host):
    """Versi pesan untuk klien: tanpa IP/host dan tanpa detail error teknis."""
    t = re.sub(r"\n<i>.*?</i>", "", text, flags=re.S)
    return t.replace(f" ({esc(host)})", "").replace(esc(host), "router")


def client_chat_ids(rid, kind):
    rows = db.q("""SELECT u.tg_chat_ids, u.notify FROM users u JOIN user_routers ur ON ur.user_id=u.id
                   WHERE ur.router_id=? AND u.role='client' AND u.enabled=1 AND u.tg_chat_ids!='' AND u.notify!='off'""", (rid,))
    ids = []
    for u in rows:
        if u["notify"] == "down" and not kind.startswith("offline"):
            continue                     # 'down' = hanya router mati/hidup kembali
        ids += [x for x in u["tg_chat_ids"].split(",") if x]
    return list(dict.fromkeys(ids))


def notify(r, kind, text, settings):
    """Kirim ke admin; plus ke klien pemilik router ini (sesuai preferensi mereka), dengan teks yang sudah disaring."""
    send_async(text, settings=settings)
    admin = set(_chat_ids(settings))
    ids = [i for i in client_chat_ids(r["id"], kind) if i not in admin]
    if ids:
        send_async(_client_text(text, r["host"]), settings=settings, chat_ids=ids)


# ---------------- bot command listener (long polling) ----------------
def _status_text(scope=None):
    rows = db.q("""SELECT r.id,r.name,r.host,r.enabled,s.online,s.latest FROM routers r
                   LEFT JOIN router_state s ON s.router_id=r.id ORDER BY r.name""")
    if scope is not None:
        rows = [r for r in rows if r["id"] in scope]
    on = sum(1 for r in rows if r["online"])
    off = [r for r in rows if r["enabled"] and not r["online"]]
    al = db.q("SELECT a.*, r.name rname FROM alert_state a JOIN routers r ON r.id=a.router_id ORDER BY a.since")
    if scope is not None:
        al = [a for a in al if a["router_id"] in scope]
    t = f"📊 <b>Ringkasan jaringan</b>\n🟢 Online: <b>{on}</b> / {len(rows)}\n🔴 Down: <b>{len(off)}</b>\n⚠️ Alert aktif: <b>{len(al)}</b>\n"
    if off:
        t += "\n<b>Router DOWN:</b>\n" + "\n".join(f"• {esc(r['name'])}" + (f" ({esc(r['host'])})" if scope is None else "") for r in off)
    if al:
        t += "\n\n<b>Alert aktif:</b>\n" + "\n".join(f"• {esc(a['rname'])}: {esc(a['message'])}" for a in al[:15])
    return t + f"\n\n🕒 {fmt_ts()}"


def _routers_text(scope=None):
    import json
    rows = db.q("""SELECT r.id,r.name,r.host,r.enabled,s.online,s.latest FROM routers r
                   LEFT JOIN router_state s ON s.router_id=r.id ORDER BY r.name""")
    if scope is not None:
        rows = [r for r in rows if r["id"] in scope]
    lines = []
    for r in rows:
        lt = json.loads(r["latest"] or "{}")
        if not r["enabled"]:
            lines.append(f"⚪ {esc(r['name'])} — nonaktif")
        elif r["online"]:
            lines.append(f"🟢 <b>{esc(r['name'])}</b> — CPU {lt.get('cpu', 0):.0f}% · RAM {lt.get('mem_pct') or 0:.0f}% · {lt.get('latency') or 0:.0f} ms")
        else:
            lines.append(f"🔴 <b>{esc(r['name'])}</b> — DOWN")
    return "📡 <b>Daftar router</b>\n" + ("\n".join(lines) or "Belum ada router.")


def _router_text(name, scope=None):
    import json
    rs = db.q("""SELECT r.*, s.online, s.latest, s.info FROM routers r LEFT JOIN router_state s ON s.router_id=r.id
                 WHERE lower(r.name) LIKE ? ORDER BY r.name""", (f"%{name.lower()}%",))
    if scope is not None:
        rs = [x for x in rs if x["id"] in scope]
    r = rs[0] if rs else None
    if not r:
        return "Router tidak ditemukan."
    if scope is not None:
        r = dict(r, host="")
    lt, info = json.loads(r["latest"] or "{}"), json.loads(r["info"] or "{}")
    if not r["online"]:
        return f"🔴 <b>{esc(r['name'])}</b>" + (f" ({esc(r['host'])})" if r["host"] else "") + " sedang DOWN"
    return (f"🟢 <b>{esc(r['name'])}</b>" + (f" ({esc(r['host'])})" if r["host"] else "") + f"\n{esc(info.get('model', ''))} · RouterOS {esc(info.get('version', ''))}\n"
            f"Uptime: {fmt_dur(lt.get('uptime', 0))}\nCPU: {lt.get('cpu', 0):.0f}% · RAM: {lt.get('mem_pct') or 0:.0f}%"
            + (f" · Suhu: {lt['temp']:.0f}°C" if lt.get("temp") is not None else "")
            + f"\nLatency: {lt.get('latency') or 0:.0f} ms\n⬇️ {lt.get('rx_bps') or 0 :.0f} bps · ⬆️ {lt.get('tx_bps') or 0:.0f} bps")


HELP = ("🤖 <b>Bot monitoring MikroTik</b>\n/status — ringkasan\n/routers — daftar router\n"
        "/router &lt;nama&gt; — detail satu router\n/alerts — alert aktif\n/id — tampilkan chat id")


def _handle(msg):
    s = db.get_settings()
    chat = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()
    if not text.startswith("/"):
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd == "/id":
        send(f"Chat ID: <code>{chat}</code>", s, [chat])
        return
    scope = None                                   # None = admin (semua router)
    if chat not in _chat_ids(s):
        us = db.q("SELECT id FROM users WHERE role='client' AND enabled=1 AND (',' || tg_chat_ids || ',') LIKE ?", (f"%,{chat},%",))
        if not us:
            send("⛔ Chat ini belum terdaftar.\nChat ID Anda: <code>%s</code>\nMasukkan ID ini di menu <b>Akun saya</b> pada portal, atau berikan ke admin." % chat, s, [chat])
            return
        scope = [x["router_id"] for u in us for x in db.q("SELECT router_id FROM user_routers WHERE user_id=?", (u["id"],))]
    if cmd in ("/start", "/help"):
        send(HELP, s, [chat])
    elif cmd in ("/status", "/alerts"):
        send(_status_text(scope), s, [chat])
    elif cmd == "/routers":
        send(_routers_text(scope), s, [chat])
    elif cmd == "/router" and arg:
        send(_router_text(arg.strip(), scope), s, [chat])
    else:
        send(HELP, s, [chat])


def bot_loop():
    offset = None
    last_tok = None
    while True:
        try:
            s = db.get_settings()
            tok = s.get("tg_token")
            if not tok or not s.get("tg_enabled"):
                time.sleep(10)
                continue
            if tok != last_tok:
                offset, last_tok = None, tok
            r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates",
                             params={"timeout": 25, "offset": offset, "allowed_updates": '["message"]'}, timeout=35)
            j = r.json()
            if not j.get("ok"):
                time.sleep(15)
                continue
            for u in j.get("result", []):
                offset = u["update_id"] + 1
                if "message" in u:
                    try:
                        _handle(u["message"])
                    except Exception:
                        log.exception("tg handle")
        except Exception as e:
            log.warning("tg loop: %s", e)
            time.sleep(10)


def start_bot():
    threading.Thread(target=bot_loop, daemon=True, name="tg-bot").start()
