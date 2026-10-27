import os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["ADMIN_PASSWORD"] = "x"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db, poller, ros, telegram as tg
from fake_ros import FakeRouter

db.init()
sent = []
tg.send_async = lambda text, **kw: sent.append((text, kw.get("chat_ids")))
rid = db.ex("INSERT INTO routers(name,host,port,username,password_enc,created_at) VALUES('POP Satu','10.0.0.1',8728,'m','x',1)").lastrowid
R = lambda: db.q1("SELECT * FROM routers WHERE id=?", (rid,))
S = lambda **o: {**db.get_settings(), **o}
P = lambda name, ip="10.1.1.1", up="5m": {"name": name, "address": ip, "caller": "AA:BB", "uptime": up, "service": "pppoe"}
D = lambda kind, rows: {"sessions": {kind: rows}}
evs = lambda: [e["message"] for e in db.q("SELECT message FROM events WHERE kind='session' ORDER BY id")]
t = 1_000_000

# parser durasi
assert ros.parse_dur("1w2d3h4m5s") == 604800 + 2 * 86400 + 3 * 3600 + 240 + 5 and ros.parse_dur("45s") == 45 and ros.parse_dur("") == 0 and ros.parse_dur("3h20m") == 12000

# --- mode off: tidak ada apa-apa
s = S(sess_pppoe="off")
poller.process_sessions(R(), D("pppoe", [P("a")]), s, t); assert db.q("SELECT * FROM sessions") == [] and sent == []

# --- baseline pertama: catat tanpa notifikasi
s = S(sess_pppoe="all", sess_grace_polls=2, sess_mass_drop=5)
poller.process_sessions(R(), D("pppoe", [P("andi"), P("budi"), P("citra")]), s, t)
assert sent == [] and len(db.q("SELECT * FROM sessions")) == 3 and evs() == []

# --- login baru
poller.process_sessions(R(), D("pppoe", [P("andi"), P("budi"), P("citra"), P("dewi", "10.1.1.9", "20s")]), s, t + 30)
assert len(sent) == 1 and "tersambung" in sent[0][0] and "dewi" in sent[0][0] and "10.1.1.9" in sent[0][0], sent
assert evs() == ["PPPoE dewi tersambung (10.1.1.9)"]

# --- putus butuh 2 poll berturut-turut (anti-flapping)
sent.clear()
poller.process_sessions(R(), D("pppoe", [P("andi"), P("citra"), P("dewi")]), s, t + 60)          # budi hilang 1x
assert sent == [], "belum boleh dinyatakan putus"
poller.process_sessions(R(), D("pppoe", [P("andi"), P("budi"), P("citra"), P("dewi")]), s, t + 90)  # budi balik: flap diabaikan
assert sent == [] and len(db.q("SELECT * FROM sessions WHERE miss>0")) == 0
poller.process_sessions(R(), D("pppoe", [P("andi"), P("citra"), P("dewi")]), s, t + 120)
poller.process_sessions(R(), D("pppoe", [P("andi"), P("citra"), P("dewi")]), s, t + 150)        # hilang 2x -> putus
assert len(sent) == 1 and "putus" in sent[0][0] and "budi" in sent[0][0] and "Online selama" in sent[0][0], sent
assert "PPPoE budi putus (online" in evs()[-1]

# --- gagal ambil data (None) tidak boleh dianggap semua putus
sent.clear()
before = db.q("SELECT COUNT(*) n FROM sessions")[0]["n"]
for i in range(4):
    poller.process_sessions(R(), {"sessions": {"pppoe": None}}, s, t + 200 + i)
    poller.process_sessions(R(), {}, s, t + 210 + i)
assert sent == [] and db.q("SELECT COUNT(*) n FROM sessions")[0]["n"] == before

# --- banyak putus bersamaan -> 1 ringkasan
for k in ("sessions", "sess_seed"): db.ex(f"DELETE FROM {k}")
poller.process_sessions(R(), D("pppoe", [P("seed")]), s, t + 290)                                  # baseline baru
users = [P(f"u{i}") for i in range(8)]
poller.process_sessions(R(), D("pppoe", users), s, t + 300)
poller.process_sessions(R(), D("pppoe", users), s, t + 301); poller.process_sessions(R(), D("pppoe", users), s, t + 302)
sent.clear()
poller.process_sessions(R(), D("pppoe", []), s, t + 330); poller.process_sessions(R(), D("pppoe", []), s, t + 360)
assert len(sent) == 1 and "8 sesi PPPoE putus bersamaan" in sent[0][0] and "uplink" in sent[0][0] and "u7" in sent[0][0], sent
sent.clear()
poller.process_sessions(R(), D("pppoe", users), s, t + 390)                                       # semua balik -> 1 ringkasan
assert len(sent) == 1 and "tersambung kembali" in sent[0][0], sent

# --- mode watch + wildcard (tak peka huruf besar)
for k in ("sessions", "sess_seed"): db.ex(f"DELETE FROM {k}")
sent.clear()
s = S(sess_pppoe="watch", sess_watch="VIP-*\nbudi, andi", sess_grace_polls=1)
poller.process_sessions(R(), D("pppoe", [P("x")]), s, t + 500)
poller.process_sessions(R(), D("pppoe", [P("x"), P("vip-toko"), P("biasa"), P("Budi")]), s, t + 530)
msgs = " ".join(m for m, _ in sent)
assert "vip-toko" in msgs and "Budi" in msgs and "biasa" not in msgs, msgs
sent.clear(); poller.process_sessions(R(), D("pppoe", [P("x")]), s, t + 560)
assert len(sent) == 2 and not any("biasa" in m for m, _ in sent)                                    # 'biasa' putus tapi tak dipantau
assert "biasa" not in " ".join(evs())

# --- login dimatikan: hanya putus yang diberi tahu
for k in ("sessions", "sess_seed"): db.ex(f"DELETE FROM {k}")
sent.clear(); s = S(sess_pppoe="all", sess_notify_login=False, sess_grace_polls=1)
poller.process_sessions(R(), D("pppoe", [P("a")]), s, t + 600); poller.process_sessions(R(), D("pppoe", [P("a"), P("b")]), s, t + 630)
assert sent == []
poller.process_sessions(R(), D("pppoe", [P("a")]), s, t + 660); assert len(sent) == 1 and "putus" in sent[0][0]

# --- hotspot: kunci per user+MAC (satu user, dua perangkat), dan jenis lain tidak tercampur
for k in ("sessions", "sess_seed"): db.ex(f"DELETE FROM {k}")
sent.clear(); s = S(sess_hotspot="all", sess_pppoe="off", sess_grace_polls=1)
H = lambda n, mac: {"name": n, "address": "172.16.0.5", "caller": mac, "uptime": "1m", "service": "hotspot"}
poller.process_sessions(R(), D("hotspot", [H("tamu1", "AA:01")]), s, t + 700)
poller.process_sessions(R(), D("hotspot", [H("tamu1", "AA:01"), H("tamu1", "AA:02")]), s, t + 730)
assert len(sent) == 1 and "Hotspot tersambung" in sent[0][0]

# --- router alerts dimatikan: event tetap tercatat, Telegram diam
for k in ("sessions", "sess_seed"): db.ex(f"DELETE FROM {k}")
db.ex("UPDATE routers SET alerts_enabled=0 WHERE id=?", (rid,)); sent.clear(); n0 = len(evs())
s = S(sess_pppoe="all", sess_grace_polls=1)
poller.process_sessions(R(), D("pppoe", [P("a")]), s, t + 800); poller.process_sessions(R(), D("pppoe", [P("a"), P("b")]), s, t + 830)
assert sent == [] and len(evs()) == n0 + 1
db.ex("UPDATE routers SET alerts_enabled=1 WHERE id=?", (rid,))

# --- dimatikan lalu diaktifkan lagi: tak boleh banjir notifikasi palsu
poller.process_sessions(R(), D("pppoe", [P("a"), P("b")]), S(sess_pppoe="off"), t + 900)
assert db.q("SELECT * FROM sessions") == []
sent.clear(); poller.process_sessions(R(), D("pppoe", [P("z1"), P("z2")]), S(sess_pppoe="all"), t + 930); assert sent == []

# --- pengambilan nyata dari RouterOS (fake): pppoe vs vpn terpisah, hotspot, dan fitur tak tersedia
fr = FakeRouter()
fr.t["/ppp/active"] += [{".id": "*1", "name": "andi", "service": "pppoe", "address": "10.9.0.2", "caller-id": "AA:11", "uptime": "1h"},
                        {".id": "*2", "name": "vpn-x", "service": "l2tp", "address": "11.11.11.5", "caller-id": "9.9.9.9", "uptime": "5m"}]
fr.t["/ip/hotspot/active"] += [{".id": "*3", "user": "tamu9", "address": "172.16.0.9", "mac-address": "AA:99", "uptime": "2m"}]
c = ros.connect("127.0.0.1", fr.port, False, "x", "pw")
got = ros.fetch_sessions(c, "h1", ("pppoe", "vpn", "hotspot")); c.close()
assert [x["name"] for x in got["pppoe"]] == ["andi"] and [x["name"] for x in got["vpn"]] == ["vpn-x"] and got["hotspot"][0]["caller"] == "AA:99", got
c = ros.connect("127.0.0.1", fr.port, False, "x", "pw")
assert ros.fetch_sessions(c, "h2", ("pppoe",)) == {"pppoe": [{"name": "andi", "address": "10.9.0.2", "caller": "AA:11", "uptime": "1h", "service": "pppoe"}]}
fr.t.pop("/ip/hotspot/active")                                                                       # paket hotspot tidak terpasang
got = ros.fetch_sessions(c, "h3", ("hotspot", "pppoe")); c.close()
assert got["hotspot"] is None and got["pppoe"] is not None, got
print("TES SESI LULUS")
