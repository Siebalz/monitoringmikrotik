import os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["ADMIN_USER"] = "admin"; os.environ["ADMIN_PASSWORD"] = "adminpass"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastapi.testclient import TestClient
from app import main, poller, telegram as tg, db
poller.start = lambda: None; main.latency.start = lambda: None; tg.start_bot = lambda: None

sent = []
tg.send_async = lambda text, **kw: sent.append((text, kw.get("chat_ids")))
bot = []
tg.send = lambda text, settings=None, chat_ids=None, silent=False: (bot.append((text, chat_ids)), (True, None))[1]

def mk(name, host, grp="G"):
    return db.ex("INSERT INTO routers(name,host,port,username,password_enc,group_name,created_at) VALUES(?,?,?,?,?,?,?)",
                 (name, host, 8728, "monitor", main.enc("x"), grp, int(time.time()))).lastrowid

with TestClient(main.app) as adm:
    assert adm.post("/api/login", json={"username": "admin", "password": "adminpass"}).json()["role"] == "admin"
    r1, r2, r3 = mk("Toko A", "11.11.11.10"), mk("Toko B", "11.11.11.11"), mk("Kantor C", "11.11.11.12")
    for rid, on in ((r1, 1), (r2, 1), (r3, 0)):
        db.ex("INSERT INTO router_state(router_id,online,fail_count,last_error,info,latest) VALUES(?,?,0,'connect ke 11.11.11.12 gagal','{}','{}')", (rid, on))
    db.ex("INSERT INTO events(ts,level,kind,message,router_id,router_name) VALUES(?,?,?,?,?,?)", (int(time.time()), "critical", "offline", "Router tidak merespon", r1, "Toko A"))
    db.ex("INSERT INTO events(ts,level,kind,message,router_id,router_name) VALUES(?,?,?,?,?,?)", (int(time.time()), "critical", "offline", "Router tidak merespon", r2, "Toko B"))

    # ---- admin membuat klien
    assert adm.post("/api/users", json={"username": "x", "router_ids": []}).status_code == 400          # username pendek
    assert adm.post("/api/users", json={"username": "admin"}).status_code == 400                         # bentrok admin
    assert adm.post("/api/users", json={"username": "budi", "password": "short"}).status_code == 400
    assert adm.post("/api/users", json={"username": "budi", "router_ids": [999]}).status_code == 400
    assert adm.post("/api/users", json={"username": "budi", "tg_chat_ids": "abc"}).status_code == 400
    r = adm.post("/api/users", json={"username": "budi", "display_name": "Pak Budi", "password": "rahasia123", "tg_chat_ids": "111222,333444", "notify": "all", "router_ids": [r1]}).json()
    uid_budi = r["id"]
    gen = adm.post("/api/users", json={"username": "Sari", "router_ids": [r2, r3], "notify": "down", "tg_chat_ids": "555666"}).json()
    assert len(gen["password"]) == 14                                                                    # password acak bila kosong
    assert adm.post("/api/users", json={"username": "BUDI"}).status_code == 400                          # unik, tak peka huruf besar
    assert db.q1("SELECT password_hash FROM users WHERE id=?", (uid_budi,))["password_hash"].startswith("scrypt$")
    assert "rahasia123" not in str(db.q("SELECT * FROM users"))

    # ---- klien login & lingkup
    with TestClient(main.app) as c:
        assert c.post("/api/login", json={"username": "budi", "password": "salah"}).status_code == 401
        assert c.post("/api/login", json={"username": "tidakada", "password": "x"}).status_code == 401
        assert c.post("/api/login", json={"username": "budi", "password": "rahasia123"}).json()["role"] == "client"
        me = c.get("/api/me").json(); assert me["role"] == "client" and me["name"] == "Pak Budi", me
        ov = c.get("/api/overview").json()
        assert [x["name"] for x in ov["routers"]] == ["Toko A"] and ov["summary"]["total"] == 1, ov["summary"]
        x = ov["routers"][0]
        assert x["host"] == "" and "username" not in x and "has_password" not in x and x["last_error"] == "", x
        assert len(ov["events"]) == 1 and ov["events"][0]["router_id"] == r1
        d = c.get(f"/api/routers/{r1}").json()
        assert d["router"]["host"] == "" and d["state"]["last_error"] == ""
        assert c.get(f"/api/routers/{r2}").status_code == 404                                            # router orang lain
        for path in (f"/api/routers/{r2}/metrics", f"/api/routers/{r2}/iface?name=e", f"/api/routers/{r2}/latency"):
            assert c.get(path).status_code == 404, path
        assert c.get(f"/api/routers/{r1}/metrics").status_code == 200 and c.get(f"/api/routers/{r1}/latency").status_code == 200
        ev = c.get("/api/events").json(); assert {e["router_id"] for e in ev} == {r1}
        assert c.get(f"/api/events?router_id={r2}").json() == []
        lo = c.get("/api/latency/overview").json(); assert [x["id"] for x in lo["routers"]] == [r1]
        assert c.get("/api/fleet/traffic").status_code == 200
        assert set(c.get("/api/thresholds").json()) == {"cpu_limit", "mem_limit", "temp_limit", "latency_limit"}
        assert [x["name"] for x in c.get("/api/my/routers").json()] == ["Toko A"] and len(adm.get("/api/my/routers").json()) == 3
        # ---- semua yang admin-only harus 403
        for m, path, body in [("get", "/api/routers", None), ("get", "/api/settings", None), ("put", "/api/settings", {"tg_token": "x"}),
                              ("get", "/api/vpn/servers", None), ("get", "/api/users", None), ("post", "/api/users", {"username": "hack1"}),
                              ("put", f"/api/routers/{r1}", {"name": "x"}), ("delete", f"/api/routers/{r1}", None),
                              ("post", f"/api/routers/{r1}/poll", None), ("post", f"/api/routers/{r1}/diag/ping", {"address": "1.1.1.1"}),
                              ("get", f"/api/routers/{r1}/diag/log", None), ("post", f"/api/routers/{r1}/latency/probe", None),
                              ("get", f"/api/routers/{r1}/latency/live", None), ("put", f"/api/routers/{r1}/watched", {"names": []}),
                              ("post", "/api/telegram/test", {}), ("delete", f"/api/users/{uid_budi}", None)]:
            resp = getattr(c, m)(path, **({"json": body} if body is not None else {}))
            assert resp.status_code == 403, (m, path, resp.status_code)
        assert db.q1("SELECT id FROM routers WHERE id=?", (r1,))                                          # tidak terhapus

        # ---- profil & password sendiri
        assert c.put("/api/me/profile", json={"tg_chat_ids": "x1", "notify": "all"}).status_code == 400
        assert c.put("/api/me/profile", json={"tg_chat_ids": "111222", "notify": "down"}).status_code == 200
        assert c.get("/api/me/profile").json()["notify"] == "down"
        assert c.put("/api/me/password", json={"current": "salah", "new": "passbaru123"}).status_code == 400
        assert c.put("/api/me/password", json={"current": "rahasia123", "new": "pendek"}).status_code == 400
        assert c.put("/api/me/password", json={"current": "rahasia123", "new": "passbaru123"}).status_code == 200
        assert adm.get("/api/me/profile").status_code == 403                                              # admin bukan klien
        with TestClient(main.app) as c2:
            assert c2.post("/api/login", json={"username": "budi", "password": "rahasia123"}).status_code == 401
            assert c2.post("/api/login", json={"username": "budi", "password": "passbaru123"}).status_code == 200

        # ---- dinonaktifkan admin -> sesi langsung mati
        u = adm.get("/api/users").json(); ub = next(x for x in u if x["username"] == "budi")
        assert ub["router_ids"] == [r1]
        adm.put(f"/api/users/{uid_budi}", json={"username": "budi", "enabled": False, "router_ids": [r1], "notify": "down", "tg_chat_ids": "111222"})
        assert c.get("/api/overview").status_code == 401
        with TestClient(main.app) as c3:
            assert c3.post("/api/login", json={"username": "budi", "password": "passbaru123"}).status_code == 401
        adm.put(f"/api/users/{uid_budi}", json={"username": "budi", "enabled": True, "router_ids": [r1], "notify": "all", "tg_chat_ids": "111222,333444", "password": "resetbaru99"})
        with TestClient(main.app) as c4:
            assert c4.post("/api/login", json={"username": "budi", "password": "resetbaru99"}).status_code == 200

    # ---- notifikasi Telegram per klien
    s = db.get_settings(); s["tg_chat_ids"] = "999"
    ra = db.q1("SELECT * FROM routers WHERE id=?", (r1,)); rb = db.q1("SELECT * FROM routers WHERE id=?", (r2,)); rc = db.q1("SELECT * FROM routers WHERE id=?", (r3,))
    txt = "🔴 <b>ROUTER DOWN</b>\n<b>Toko A</b> (11.11.11.10)\nTidak merespon sejak X\n<i>connect ke 11.11.11.10:8728 gagal</i>"
    sent.clear(); tg.notify(ra, "offline", txt, s)
    assert len(sent) == 2 and sent[0][1] is None and "11.11.11.10" in sent[0][0]                         # admin dapat lengkap
    ct, ids = sent[1]; assert ids == ["111222", "333444"] and "11.11.11.10" not in ct and "connect ke" not in ct and "Toko A" in ct, sent
    sent.clear(); tg.notify(ra, "cpu", "cpu tinggi", s); assert len(sent) == 2                           # 'all' menerima cpu
    sent.clear(); tg.notify(rb, "cpu", "cpu tinggi", s); assert len(sent) == 1                           # Sari 'down': tak dapat cpu
    sent.clear(); tg.notify(rb, "offline", txt, s); assert len(sent) == 2 and sent[1][1] == ["555666"]
    sent.clear(); tg.notify(rc, "offline:x", txt, s); assert sent[1][1] == ["555666"]
    adm.put(f"/api/users/{gen['id']}", json={"username": "Sari", "router_ids": [r2], "notify": "off", "tg_chat_ids": "555666"})
    sent.clear(); tg.notify(rb, "offline", txt, s); assert len(sent) == 1                                 # off = tak ada kiriman
    s["tg_chat_ids"] = "111222"                                                                          # admin = chat yang sama dg klien -> tak dobel
    sent.clear(); tg.notify(ra, "offline", txt, s); assert len(sent) == 2 and sent[1][1] == ["333444"]
    s["tg_chat_ids"] = "999"
    # tombol tes notifikasi admin
    assert adm.post(f"/api/users/{uid_budi}/test-notify").json()["ok"] is True and bot[-1][1] == ["111222", "333444"]

    # ---- bot Telegram: klien hanya melihat routernya, tanpa host
    db.save_settings({"tg_chat_ids": "999"}) if hasattr(db, "save_settings") else None
    bot.clear(); tg._handle({"chat": {"id": 111222}, "text": "/routers"})
    assert "Toko A" in bot[-1][0] and "Toko B" not in bot[-1][0] and "Kantor C" not in bot[-1][0], bot[-1]
    bot.clear(); tg._handle({"chat": {"id": 111222}, "text": "/router Toko B"}); assert "tidak ditemukan" in bot[-1][0]
    bot.clear(); tg._handle({"chat": {"id": 111222}, "text": "/status"}); assert "11.11.11" not in bot[-1][0] and "Toko B" not in bot[-1][0]
    bot.clear(); tg._handle({"chat": {"id": 424242}, "text": "/routers"}); assert "belum terdaftar" in bot[-1][0] and "424242" in bot[-1][0]
    bot.clear(); tg._handle({"chat": {"id": 999}, "text": "/routers"}); assert all(n in bot[-1][0] for n in ("Toko A", "Toko B", "Kantor C"))   # admin lihat semua

    # ---- hapus router membersihkan penautan; hapus klien
    adm.delete(f"/api/routers/{r1}")
    assert db.q("SELECT * FROM user_routers WHERE router_id=?", (r1,)) == []
    assert adm.delete(f"/api/users/{uid_budi}").status_code == 200 and adm.get("/api/users").json()[0]["username"] == "Sari"
    # sesi lama admin (sebelum ada kolom role) tetap dianggap admin
    with TestClient(main.app) as legacy:
        pass
print("TES PORTAL KLIEN LULUS")
