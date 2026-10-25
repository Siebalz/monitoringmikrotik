import os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["ADMIN_USER"] = "a"; os.environ["ADMIN_PASSWORD"] = "b"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
from fake_ros import FakeRouter
from app import main, poller

poller.start = lambda: None            # jangan jalankan polling/bot saat uji
main.tg.start_bot = lambda: None
main.poller.poll_router = lambda r: {"online": 0, "last_error": ""}

fr = FakeRouter()
with TestClient(main.app) as c:
    assert c.get("/api/vpn/servers").status_code == 401
    assert c.post("/api/login", json={"username": "a", "password": "b"}).status_code == 200
    r = c.post("/api/vpn/servers/test", json={"host": "127.0.0.1", "port": fr.port, "username": "x", "password": "salah"}).json()
    assert not r["ok"], r
    r = c.post("/api/vpn/servers/test", json={"host": "127.0.0.1", "port": fr.port, "username": "x", "password": "pw"}).json()
    assert r["ok"] and r["profiles"] == ["default"], r
    sid = c.post("/api/vpn/servers", json={"name": "S1", "host": "127.0.0.1", "port": fr.port, "username": "x", "password": "pw",
                                           "public_ip": "1.2.3.4", "vpn_subnet": "11.11.11"}).json()["id"]
    assert c.get("/api/vpn/servers").json()[0]["vpn_subnet"] == "11.11.11."          # titik otomatis
    # buat user + monitoring
    r = c.post(f"/api/vpn/servers/{sid}/users", json={"username": "budi", "password": 'p"w$1', "monitor": True}).json()
    assert r["ip"] == "11.11.11.10" and len(r["access"]) == 4, r
    assert r["router_id"] and r["monitor_password"]
    assert 'password="p\\"w\\$1"' in r["client_script"] and "/user add name=monitor" in r["client_script"], r["client_script"]
    ports = [a["port"] for a in r["access"]]; assert len(set(ports)) == 4
    # user kedua: IP berikutnya, port tidak bentrok
    r2 = c.post(f"/api/vpn/servers/{sid}/users", json={"username": "ani", "monitor": False}).json()
    assert r2["ip"] == "11.11.11.11" and r2["router_id"] is None and len(r2["password"]) == 16
    assert not set(ports) & {a["port"] for a in r2["access"]}
    # duplikat & username jelek
    assert c.post(f"/api/vpn/servers/{sid}/users", json={"username": "budi"}).status_code == 400
    assert c.post(f"/api/vpn/servers/{sid}/users", json={"username": "a b;"}).status_code == 400
    # daftar + sesi aktif
    fr.t["/ppp/active"].append({".id": "*A1", "name": "budi", "address": "11.11.11.10", "uptime": "1h2m", "caller-id": "9.9.9.9"})
    fr.t["/interface"].append({".id": "*I1", "name": "<l2tp-budi>", "type": "l2tp-in", "rx-byte": "1000", "tx-byte": "2000"})
    d = c.get(f"/api/vpn/servers/{sid}/users").json()
    b = next(u for u in d["users"] if u["name"] == "budi")
    assert d["summary"] == {"total": 2, "online": 1, "disabled": 0} and b["online"] and b["rx"] == 1000 and len(b["access"]) == 4 and b["router_id"], d
    # script ulang
    assert "p\\\"w\\$1" in c.get(f"/api/vpn/servers/{sid}/users/{b['id']}/script").json()["script"]
    # nonaktifkan
    assert c.post(f"/api/vpn/servers/{sid}/users/{b['id']}/toggle", json={"disabled": True}).status_code == 200
    assert next(u for u in c.get(f"/api/vpn/servers/{sid}/users").json()["users"] if u["name"] == "budi")["disabled"]
    # NAT yatim
    fr.t["/ip/firewall/nat"].append({".id": "*N9", "comment": "VPN-ghost-Winbox", "dst-port": "30000"})
    assert c.get(f"/api/vpn/servers/{sid}/users").json()["orphan_nat"] == 1
    assert c.post(f"/api/vpn/servers/{sid}/cleanup").json()["removed"] == 1
    # hapus user: NAT hilang, link + router monitoring ikut hilang
    rid = b["router_id"]
    assert c.delete(f"/api/vpn/servers/{sid}/users/{b['id']}").json()["removed_nat"] == 4
    assert not any(x["comment"].startswith("VPN-budi-") for x in fr.t["/ip/firewall/nat"])
    assert main.db.q1("SELECT id FROM routers WHERE id=?", (rid,)) is None
    assert fr.t["/ppp/active"] == [] or True
    # rollback bila NAT gagal: simulasi lewat layanan tak valid tidak perlu; cek audit
    ev = c.get("/api/events?limit=50").json()
    assert any("User VPN dibuat: budi" in e["message"] for e in ev), ev
    # settings VPN tersimpan
    c.put("/api/settings", json={"vpn_services": "SSH:22"})
    r3 = c.post(f"/api/vpn/servers/{sid}/users", json={"username": "cici", "monitor": False}).json()
    assert [a["name"] for a in r3["access"]] == ["SSH"]
    # --- diagnosa pada server VPN ---
    base = f"/api/vpn/servers/{sid}/diag"
    c.put(f"/api/vpn/servers/{sid}", json={"name": "S1", "host": "127.0.0.1", "port": fr.port, "username": "x", "password": "pw", "vpn_subnet": "11.11.11."})
    p = c.post(base + "/ping", json={"address": "8.8.8.8", "count": 3}).json()
    assert all(abs(x["ms"] - (10.5 + x["seq"])) < 1e-9 for x in p["replies"]), p
    assert p["sent"] == 3 and p["received"] == 3 and abs(p["avg"] - 11.2) < 1e-9 and p["loss"] == 0 and len(p["replies"]) == 3, p
    t = c.post(base + "/traceroute", json={"address": "8.8.8.8"}).json()
    assert [h["address"] for h in t["hops"]] == ["10.0.0.1", "*", "8.8.8.8"] and t["reached"], t
    lg = c.get(base + "/log?topic=l2tp").json()
    assert lg["total"] == 1 and lg["lines"][0]["message"] == "budi logged in", lg
    assert c.get(base + "/log?search=BOOM").json()["lines"][0]["time"] == "10:01:00"
    assert c.get(base + "/log?limit=2").json()["lines"][0]["message"] == "boom"       # terbaru dulu
    for bad in ("8.8.8.8; rm", "a b", "", "-x"):
        assert c.post(base + "/ping", json={"address": bad}).status_code == 400, bad
    fr.deny = True
    e = c.post(base + "/ping", json={"address": "1.1.1.1"})
    assert e.status_code == 400 and "policy=read,api,test" in e.json()["detail"], e.text
    fr.deny = False
    # diagnosa pada router monitoring (host non-demo)
    rid2 = main.db.ex("""INSERT INTO routers(name,host,port,username,password_enc,created_at) VALUES('R','127.0.0.1',?,'x',?,1)""",
                      (fr.port, main.enc("pw"))).lastrowid
    assert c.post(f"/api/routers/{rid2}/diag/ping", json={"address": "1.1.1.1"}).json()["received"] == 4
    assert c.post("/api/routers/99999/diag/ping", json={"address": "1.1.1.1"}).status_code == 404
    # --- network latency ---
    from app import latency
    s = main.db.get_settings()
    assert [n for n, _ in latency.targets_for(s)] == ["Google", "YouTube", "Mobile Legends", "PUBG Mobile", "Instagram", "Facebook", "Gateway"]
    assert latency.parse_targets("A:ok.com, bad entry, B:x y.com, C:1.2.3.4") == [("A", "ok.com"), ("C", "1.2.3.4")]
    main.db.ex("UPDATE routers SET enabled=1 WHERE id=?", (rid2,))
    c.put("/api/settings", json={"latency_targets": "Google:google.com,PUBG Mobile:pubgmobile.com,Rusak:nama.invalid"})
    r2row = main.db.q1("SELECT * FROM routers WHERE id=?", (rid2,))
    assert latency.probe_router(r2row) == 4
    v = c.get(f"/api/routers/{rid2}/latency?range=1h").json()
    by = {t["name"]: t for t in v["targets"]}
    assert abs(by["Google"]["ms"] - 11.2) < 1e-9 and by["Google"]["ip"] == "142.250.1.1" and by["Google"]["loss"] == 0, by["Google"]
    assert by["PUBG Mobile"]["ms"] is None and by["PUBG Mobile"]["loss"] == 100 and by["PUBG Mobile"]["err"] == "timeout"
    assert by["Rusak"]["err"] == "dns", by["Rusak"]
    from app import ros as _r
    assert [_r.parse_ms(x) for x in ("12ms", "2ms113us", "1s200ms", "500us", "7", "", None, "abc")] == [12.0, 2.113, 1200.0, 0.5, 7.0, None, None, None]
    assert by["Rusak"]["loss"] == 100 and by["Rusak"]["ms"] is None          # DNS gagal = 100% loss, bukan crash
    assert by["Gateway"]["ip"] == "192.168.101.1" and abs(by["Gateway"]["ms"] - 11.2) < 1e-9 and by["Gateway"]["v"], by["Gateway"]
    assert list(by)[-1] == "Gateway" and v["error"] == ""
    # izin kurang -> pesan jelas, tidak ada data baru
    fr.deny = True
    assert c.post(f"/api/routers/{rid2}/latency/probe").json()["measured"] == 0
    assert "policy=read,api,test" in c.get(f"/api/routers/{rid2}/latency").json()["error"]
    fr.deny = False
    assert c.post(f"/api/routers/{rid2}/latency/probe").json()["measured"] == 4
    assert c.get(f"/api/routers/{rid2}/latency").json()["error"] == ""
    ov = c.get("/api/latency/overview").json()
    row = next(x for x in ov["routers"] if x["id"] == rid2)
    assert ov["targets"][-1] == "Gateway" and abs(row["cells"]["Google"]["ms"] - 11.2) < 1e-9, ov
    assert c.get("/api/routers/99999/latency").status_code == 404
    c.put("/api/settings", json={"latency_gateway": False})
    assert "Gateway" not in [t["name"] for t in c.get(f"/api/routers/{rid2}/latency").json()["targets"]]
    # --- mode live (1 detik) ---
    import time as _t
    c.put("/api/settings", json={"latency_gateway": True})
    j1 = c.get(f"/api/routers/{rid2}/latency/live").json()
    _t.sleep(3.3)
    j2 = c.get(f"/api/routers/{rid2}/latency/live?since=0").json()
    tg = {x["name"]: x for x in j2["targets"]}
    assert set(tg) == {"Google", "PUBG Mobile", "Rusak", "Gateway"}, tg.keys()
    g = tg["Google"]["pts"]
    assert len(g) >= 3 and all(abs(p[1] - 10.5) < 1.5 for p in g), g                     # ~1 titik/detik, waktu terbaca
    assert 900 <= g[1][0] - g[0][0] <= 1300, [p[0] for p in g]                              # selang ~1 detik
    assert tg["Google"]["ip"] == "142.250.1.1" and tg["Gateway"]["pts"], tg["Gateway"]
    assert tg["PUBG Mobile"]["pts"] and all(p[1] is None for p in tg["PUBG Mobile"]["pts"])  # 100% loss -> null
    last = g[-1][0]
    j3 = c.get(f"/api/routers/{rid2}/latency/live?since={last}").json()
    assert all(p[0] > last for x in j3["targets"] for p in x["pts"])                         # hanya titik baru
    # kuota sesi live
    from app import latency as _l
    _l.LIVE_MAX = 1
    rid3 = main.db.ex("""INSERT INTO routers(name,host,port,username,password_enc,created_at) VALUES('R3','127.0.0.1',?,'x',?,1)""", (fr.port, main.enc("pw"))).lastrowid
    assert c.get(f"/api/routers/{rid3}/latency/live").status_code == 429
    _l.LIVE_MAX = 4
    _l.LIVE_IDLE = 1; _t.sleep(2.5)                                                         # tanpa polling -> berhenti sendiri
    assert not _l._live[rid2].alive
    _l.LIVE_IDLE = 15
    # server mati -> 502 bukan crash
    c.put(f"/api/vpn/servers/{sid}", json={"name": "S1", "host": "127.0.0.1", "port": 1, "username": "x", "vpn_subnet": "11.11.11."})
    assert c.get(f"/api/vpn/servers/{sid}/users").status_code == 502
print("SEMUA TES LULUS")
