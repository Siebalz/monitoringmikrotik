import os, sys, tempfile, time
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["ADMIN_USER"] = "a"; os.environ["ADMIN_PASSWORD"] = "b"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient
from fake_ros import FakeRouter
from app import main, poller, cloudflare, hotspot

poller.start = lambda: None
hotspot.start = lambda: None
main.tg.start_bot = lambda: None
main.poller.poll_router = lambda r: {"online": 0, "last_error": ""}

fr = FakeRouter()

# ---- Cloudflare palsu (tanpa jaringan)
dns, cf_calls = {}, []
def fake_cf(method, path, token, body=None, params=None):
    cf_calls.append((method, path))
    if token != "tok":
        raise cloudflare.CfError("Invalid API Token (1000)")
    if path.startswith("/zones/") and path.split("/")[2] != "Z1":
        raise cloudflare.CfError("Could not route to zone (7003)")
    if path == "/zones/Z1":
        return {"name": "example.com"}
    if path == "/accounts/A1/cfd_tunnel/T1" and method == "GET":
        return {"name": "pantau", "status": "healthy", "remote_config": True}
    if path == "/accounts/A1/cfd_tunnel/T1/configurations":
        assert body["config"]["ingress"][0]["hostname"] == "*.pantau.example.com" and body["config"]["ingress"][-1] == {"service": "http_status:404"}
        return {}
    if path == "/zones/Z1/dns_records" and method == "GET":
        return [r for r in dns.values() if r["name"] == params["name"]]
    if path == "/zones/Z1/dns_records" and method == "POST":
        assert body["type"] == "CNAME" and body["proxied"] is True and body["content"] == "T1.cfargotunnel.com", body
        rid = f"r{len(dns)+1}"; dns[rid] = {"id": rid, **body}; return dns[rid]
    if path.startswith("/zones/Z1/dns_records/") and method == "DELETE":
        dns.pop(path.rsplit("/", 1)[1], None); return {}
    raise AssertionError((method, path))
cloudflare._request = fake_cf

def login(c, u, p, host="testserver"):
    return c.post("/api/login", json={"username": u, "password": p}, headers={"host": host})

with TestClient(main.app) as c:
    assert login(c, "a", "b").status_code == 200
    # router + akun hotspot
    rid = main.db.ex("INSERT INTO routers(name,host,port,username,password_enc,created_at) VALUES('Warnet','127.0.0.1',?,'monitor',?,1)",
                     (fr.port, main.enc("pw"))).lastrowid
    rid2 = main.db.ex("INSERT INTO routers(name,host,port,username,password_enc,created_at) VALUES('Lain','127.0.0.1',?,'monitor',?,1)",
                      (fr.port, main.enc("pw"))).lastrowid
    # akun monitoring (read-only) tidak bisa menulis -> pesan jelas
    main.db.ex("UPDATE routers SET hs_enabled=1 WHERE id=?", (rid,))
    fr.ro = True
    e = c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "1jam"})
    assert e.status_code == 400 and "hak tulis" in e.json()["detail"], e.text
    t = c.post(f"/api/hotspot/routers/{rid}/test").json() if False else c.post(f"/api/hotspot/routers/{rid}/test")
    assert t.status_code == 400, t.text
    fr.ro = False
    assert c.put(f"/api/hotspot/routers/{rid}/config", json={"hs_enabled": True, "hs_user": "pantau-hs", "hs_password": "pw", "hs_dns": "http://wifi.toko.lan/"}).status_code == 200
    r = c.post(f"/api/hotspot/routers/{rid}/test").json()
    assert r["ok"] and r["hotspot_found"] and r["servers"] == ["hotspot1"] and fr.t["/ip/hotspot/user"] == [], r   # user uji dihapus lagi
    hr = c.get("/api/hotspot/routers").json()
    assert [x["name"] for x in hr] == ["Lain", "Warnet"] and next(x for x in hr if x["id"] == rid)["hs_dns"] == "wifi.toko.lan" and next(x for x in hr if x["id"] == rid)["hs_has_password"]

    # ---- profile
    bad = c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "x;y"})
    assert bad.status_code == 400
    assert c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "1jam", "rate_limit": "2M/5M", "price": 2000, "validity": "1h"}).status_code == 200
    assert c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "1hari", "price": 5000, "validity": "1d", "shared_users": "2"}).status_code == 200
    assert c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "1jam"}).status_code == 400            # duplikat
    assert c.post(f"/api/hotspot/routers/{rid}/profiles", json={"name": "z", "validity": "sehari"}).status_code == 400
    pj = c.get(f"/api/hotspot/routers/{rid}/profiles").json()
    p1 = next(p for p in pj["profiles"] if p["name"] == "1jam")
    assert p1["price"] == 2000 and p1["validity"] == "1h" and p1["rate_limit"] == "2M/5M" and pj["servers"] == ["hotspot1"], pj
    assert c.put(f"/api/hotspot/routers/{rid}/profiles/{p1['id']}", json={"name": "1jam", "rate_limit": "3M/6M", "price": 2500, "validity": "1h"}).status_code == 200
    assert next(p for p in c.get(f"/api/hotspot/routers/{rid}/profiles").json()["profiles"] if p["name"] == "1jam")["price"] == 2500

    # ---- generate voucher
    g = c.post(f"/api/hotspot/routers/{rid}/vouchers", json={"profile": "1jam", "qty": 5, "mode": "vc", "length": 6, "charset": "num", "prefix": "W", "note": "pagi"})
    assert g.status_code == 200, g.text
    g = g.json()
    assert g["count"] == 5 and g["price"] == 2500 and all(v["username"] == v["password"] and v["username"].startswith("W") and len(v["username"]) == 7 for v in g["vouchers"]), g
    assert len({v["username"] for v in g["vouchers"]}) == 5 and len(fr.t["/ip/hotspot/user"]) == 5
    assert fr.t["/ip/hotspot/user"][0]["comment"].startswith("pantau:" + g["batch"]) and "pagi" in fr.t["/ip/hotspot/user"][0]["comment"]
    g2 = c.post(f"/api/hotspot/routers/{rid}/vouchers", json={"profile": "1hari", "qty": 3, "mode": "up", "charset": "alnum", "limit_mb": 100}).json()
    assert all(v["username"] != v["password"] for v in g2["vouchers"]) and g2["price"] == 5000
    assert any(u.get("limit-bytes-total") == str(100 * 1024 * 1024) for u in fr.t["/ip/hotspot/user"])
    for bad in ({"profile": "tidak-ada"}, {"profile": "1jam", "qty": 0}, {"profile": "1jam", "qty": 501}, {"profile": "1jam", "length": 2},
                {"profile": "1jam", "prefix": "a b"}, {"profile": "1jam", "mode": "zzz"}, {"profile": "1jam", "limit_uptime": "x"}):
        assert c.post(f"/api/hotspot/routers/{rid}/vouchers", json=bad).status_code == 400, bad
    # rollback bila gagal di tengah jalan
    n0 = len(fr.t["/ip/hotspot/user"])
    assert c.post(f"/api/hotspot/routers/{rid}/vouchers", json={"profile": "1jam", "qty": 4, "prefix": "boom"}).status_code == 400
    assert len(fr.t["/ip/hotspot/user"]) == n0, "voucher setengah jadi harus dibersihkan"
    assert len(c.get(f"/api/hotspot/routers/{rid}/batches").json()) == 2
    bv = c.get(f"/api/hotspot/routers/{rid}/batches/{g['batch']}").json()
    assert len(bv["vouchers"]) == 5 and bv["router_dns"] == "wifi.toko.lan"
    assert c.get(f"/api/hotspot/routers/{rid}/batches/nope").status_code == 404

    # ---- terjual: terdeteksi saat dipakai, masa aktif dihitung sejak pertama dipakai
    codes = [v["username"] for v in g["vouchers"]]
    for u in fr.t["/ip/hotspot/user"]:
        if u["name"] in codes[:2]:
            u["uptime"], u["bytes-in"] = "5m", "1000"
    fr.t["/ip/hotspot/active"].append({".id": "*A1", "user": codes[0], "address": "10.5.50.2", "mac-address": "AA:BB"})
    s = c.post(f"/api/hotspot/routers/{rid}/sync").json()
    assert s == {"used": 2, "expired": 0}, s
    assert c.post(f"/api/hotspot/routers/{rid}/sync").json() == {"used": 0, "expired": 0}               # idempoten
    us = c.get(f"/api/hotspot/routers/{rid}/users").json()
    row = next(u for u in us["users"] if u["name"] == codes[0])
    assert row["online"] and row["address"] == "10.5.50.2" and row["voucher"] and row["used_at"] and row["expires_at"] - row["used_at"] == 3600, row
    assert us["online"] == 1 and us["total"] == 8 and us["users"][0]["name"] == codes[0]                   # online di atas
    assert [u["name"] for u in c.get(f"/api/hotspot/routers/{rid}/users?q={codes[3]}").json()["users"]] == [codes[3]]
    # kedaluwarsa: user dihapus dari router + sesi diputus, penjualan tetap tercatat
    main.db.ex("UPDATE hs_vouchers SET expires_at=? WHERE username=?", (int(time.time()) - 5, codes[0]))
    s = c.post(f"/api/hotspot/routers/{rid}/sync").json()
    assert s["expired"] == 1 and not any(u["name"] == codes[0] for u in fr.t["/ip/hotspot/user"]) and fr.t["/ip/hotspot/active"] == [], s
    sales = c.get("/api/hotspot/sales?days=7").json()
    assert sales["count"] == 2 and sales["amount"] == 5000 and sales["stock_count"] == 6, sales          # 2 terjual (2500), 3 belum + 3 up
    assert sales["by_profile"] == [{"profile": "1jam", "count": 2, "amount": 5000}] and sum(d["count"] for d in sales["by_day"]) == 2 and len(sales["by_day"]) == 7
    assert c.get(f"/api/hotspot/sales?rid={rid}&days=7").json()["count"] == 2
    assert c.get(f"/api/hotspot/sales?rid={rid2}").status_code == 404                                   # router tanpa hotspot aktif
    # hapus voucher: yang belum dipakai hilang dari catatan, yang sudah terjual tetap
    assert c.post(f"/api/hotspot/routers/{rid}/users/remove", json={"names": [codes[1], codes[4]]}).json()["removed"] == 2
    assert main.db.q1("SELECT 1 FROM hs_vouchers WHERE username=?", (codes[4],)) is None
    assert main.db.q1("SELECT removed FROM hs_vouchers WHERE username=?", (codes[1],))["removed"] == 1
    assert c.get(f"/api/hotspot/sales?days=7").json()["count"] == 2
    # aksi user: disable / kick / remove
    uid = next(u for u in fr.t["/ip/hotspot/user"] if u["name"] == codes[2])[".id"]
    assert c.post(f"/api/hotspot/routers/{rid}/users/{uid}/action", json={"action": "disable"}).status_code == 200
    assert next(u for u in fr.t["/ip/hotspot/user"] if u["name"] == codes[2])["disabled"] == "true"
    assert c.post(f"/api/hotspot/routers/{rid}/users/{uid}/action", json={"action": "bukan"}).status_code == 400
    # profile yang masih dipakai tidak bisa dihapus
    assert c.delete(f"/api/hotspot/routers/{rid}/profiles/{p1['id']}").status_code == 400

    # ---- halaman login hotspot
    lp = c.get(f"/api/hotspot/routers/{rid}/loginpage?title=Warnet%20<b>X</b>&color=red&mode=voucher").json()["html"]
    assert "$(link-login-only)" in lp and "$(if chap-id)" in lp and "hexMD5" in lp and "<b>X</b>" not in lp and "&lt;b&gt;X&lt;/b&gt;" in lp
    assert "#2563eb" in lp and "solid red" not in lp and "background:red" not in lp, "warna tidak valid harus diganti default"
    assert "document.login.password.value=document.login.username.value" in lp
    up = c.get(f"/api/hotspot/routers/{rid}/loginpage?mode=userpass&color=%23ff0000").json()["html"]
    assert 'type="password"' in up and "#ff0000" in up and "password.value=document.login.username" not in up
    assert "$(x)" not in hotspot.login_html(title="$(x)")

    # ---- akses klien: hanya router miliknya + hanya bila diberi izin
    cu = c.post("/api/users", json={"username": "toko_maju", "display_name": "Toko Maju", "password": "rahasia123", "router_ids": [rid], "hotspot_access": False}).json()
    c2 = TestClient(main.app)
    assert login(c2, "toko_maju", "rahasia123").status_code == 200
    assert c2.get("/api/me").json()["hotspot"] is False
    assert c2.get("/api/hotspot/routers").json() == []
    assert c2.get(f"/api/hotspot/routers/{rid}/profiles").status_code == 404
    assert c2.get("/api/hotspot/sales").status_code == 404
    c.put(f"/api/users/{cu['id']}", json={"username": "toko_maju", "display_name": "Toko Maju", "router_ids": [rid], "hotspot_access": True})
    assert c2.get("/api/me").json()["hotspot"] is True
    mine = c2.get("/api/hotspot/routers").json()
    assert [x["id"] for x in mine] == [rid] and "host" not in mine[0] and "hs_user" not in mine[0], mine
    assert c2.get(f"/api/hotspot/routers/{rid}/profiles").status_code == 200
    assert c2.get(f"/api/hotspot/routers/{rid2}/profiles").status_code == 404                           # bukan miliknya
    mn = c2.post(f"/api/hotspot/routers/{rid}/vouchers", json={"profile": "1jam", "qty": 2})
    assert mn.status_code == 200 and fr.t["/ip/hotspot/user"][-1]["name"] == mn.json()["vouchers"][-1]["username"]
    assert c2.put(f"/api/hotspot/routers/{rid}/config", json={"hs_enabled": False}).status_code == 403   # konfigurasi akun router = admin
    assert c2.post(f"/api/hotspot/routers/{rid}/test").status_code == 403
    assert c2.get("/api/settings").status_code == 403 and c2.post("/api/cloudflare/test").status_code == 403
    assert c2.get("/api/hotspot/sales?days=7").json()["stock_count"] >= 2
    main.db.ex("UPDATE routers SET hs_enabled=0 WHERE id=?", (rid,))                                       # admin matikan hotspot di router
    assert c2.get(f"/api/hotspot/routers/{rid}/profiles").status_code == 404
    main.db.ex("UPDATE routers SET hs_enabled=1 WHERE id=?", (rid,))

    # ---- Cloudflare: pengaturan, tes, ingress
    s = c.put("/api/settings", json={"cf_enabled": True, "cf_token": "tok", "cf_account_id": "A1", "cf_zone_id": "Z1", "cf_tunnel_id": "T1",
                                    "cf_base_domain": "https://Pantau.Example.com/"}).json()
    assert s["cf_token_set"] is True and "cf_token" not in s and s["cf_base_domain"] == "pantau.example.com", s
    raw = main.db.q1("SELECT value FROM settings WHERE key='cf_token'")["value"]
    assert "tok" not in raw.replace("tokf", "") or raw.count("gAAAA") == 1, "token harus tersimpan terenkripsi"
    assert "gAAAA" in raw
    c.put("/api/settings", json={"cf_token": ""})                                                          # kosong = pertahankan
    assert c.get("/api/settings").json()["cf_token_set"] is True
    t = c.post("/api/cloudflare/test").json()
    assert t["zone"] == "example.com" and t["tunnel"] == "pantau" and t["status"] == "healthy", t
    assert c.post("/api/cloudflare/apply").json()["ok"]
    c.put("/api/settings", json={"cf_zone_id": "Z9"})
    assert c.post("/api/cloudflare/test").status_code == 400
    c.put("/api/settings", json={"cf_zone_id": "Z1"})

    # ---- publish subdomain per klien
    assert c.post(f"/api/users/{cu['id']}/publish", json={"subdomain": "Bad_Name"}).status_code == 400
    assert c.post(f"/api/users/{cu['id']}/publish", json={"subdomain": "admin"}).status_code == 400        # dicadangkan
    pb = c.post(f"/api/users/{cu['id']}/publish", json={"subdomain": "tokomaju"}).json()
    assert pb["url"] == "https://tokomaju.pantau.example.com" and len(dns) == 1 and dns["r1"]["name"] == "tokomaju.pantau.example.com", pb
    cu2 = c.post("/api/users", json={"username": "toko_b", "password": "rahasia456", "router_ids": []}).json()
    assert c.post(f"/api/users/{cu2['id']}/publish", json={"subdomain": "tokomaju"}).status_code == 400    # sudah dipakai
    ul = {u["username"]: u for u in c.get("/api/users").json()}
    assert ul["toko_maju"]["subdomain"] == "tokomaju" and ul["toko_maju"]["url"] == pb["url"]
    dns["r9"] = {"id": "r9", "name": "orang.pantau.example.com", "type": "A", "content": "1.2.3.4"}
    assert c.post(f"/api/users/{cu2['id']}/publish", json={"subdomain": "orang"}).status_code == 400       # tidak menimpa record bukan-tunnel

    # ---- login per host: portal klien hanya menerima klien itu
    H = "tokomaju.pantau.example.com"
    c3 = TestClient(main.app)
    assert login(c3, "a", "b", H).status_code == 401, "admin tidak boleh login lewat subdomain klien"
    assert login(c3, "toko_b", "rahasia456", H).status_code == 401, "klien lain tidak boleh login di subdomain ini"
    assert login(c3, "toko_maju", "salah", H).status_code == 401
    assert login(c3, "toko_maju", "rahasia123", H).status_code == 200
    me = c3.get("/api/me", headers={"host": H}).json()
    assert me["brand"] == "Toko Maju" and me["role"] == "client", me
    assert TestClient(main.app).get("/api/me", headers={"host": "belum.pantau.example.com"}).json()["brand"] != "Toko Maju"
    assert login(TestClient(main.app), "toko_maju", "rahasia123", "belum.pantau.example.com").status_code == 401   # subdomain tak dikenal
    assert login(TestClient(main.app), "a", "b", "testserver").status_code == 200                          # host utama tetap normal
    assert login(TestClient(main.app), "toko_maju", "rahasia123", "testserver").status_code == 200
    # batas percobaan memakai IP asli dari Cloudflare, bukan IP cloudflared
    for _ in range(5):
        TestClient(main.app).post("/api/login", json={"username": "x", "password": "y"}, headers={"host": H, "cf-connecting-ip": "9.9.9.9"})
    assert TestClient(main.app).post("/api/login", json={"username": "toko_maju", "password": "rahasia123"}, headers={"host": H, "cf-connecting-ip": "9.9.9.9"}).status_code == 429
    main.poller  # (batas percobaan per IP asli: pengunjung lain di subdomain yang sama tidak ikut terkunci)
    assert TestClient(main.app).post("/api/login", json={"username": "toko_maju", "password": "rahasia123"}, headers={"host": H, "cf-connecting-ip": "8.8.4.4"}).status_code == 200
    # unpublish + hapus akun membersihkan DNS
    assert c.delete(f"/api/users/{cu['id']}/publish").json()["ok"] and "r1" not in dns
    c.post(f"/api/users/{cu['id']}/publish", json={"subdomain": "tokomaju"})
    assert "r2" in dns or any(r["name"] == "tokomaju.pantau.example.com" for r in dns.values())
    c.delete(f"/api/users/{cu['id']}")
    assert not any(r["name"] == "tokomaju.pantau.example.com" for r in dns.values())
    # hapus router membersihkan voucher
    c.delete(f"/api/routers/{rid}")
    assert main.db.q1("SELECT 1 FROM hs_vouchers WHERE router_id=?", (rid,)) is None
print("TES HOTSPOT & CLOUDFLARE LULUS")
