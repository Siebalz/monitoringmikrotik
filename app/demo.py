"""Seed data demo (DEMO=1): beberapa router simulasi + riwayat 24 jam supaya grafik langsung terisi."""
import math, random, time
from . import db
from .security import enc

DEMO_ROUTERS = [
    ("Kantor Pusat", "demo-1", "Pusat"),
    ("Cabang Bandung", "demo-2", "Jawa Barat"),
    ("Gudang Cikarang", "demo-hot-3", "Jawa Barat"),
    ("Cabang Surabaya", "demo-4", "Jawa Timur"),
    ("Cabang Medan", "demo-flap-5", "Sumatera"),
    ("Cabang Makassar", "demo-down-6", "Sulawesi"),
    ("Warnet Dago", "demo-7", "Jawa Barat"),
    ("Cabang Bali", "demo-lag-8", "Bali & NTT"),
]


def seed():
    if db.q1("SELECT 1 FROM routers LIMIT 1"):
        return
    now = int(time.time())
    for name, host, grp in DEMO_ROUTERS:
        db.ex("INSERT INTO routers(name,host,port,username,password_enc,group_name,created_at) VALUES(?,?,?,?,?,?,?)",
              (name, host, 8728, "monitor", enc("demo"), grp, now))
    c = db.conn()
    for r in db.q("SELECT id,host FROM routers"):
        rid, host = r["id"], r["host"]
        rnd = random.Random(rid)
        base = 4 + (rid * 13 % 35)
        m, im = [], []
        for ts in range(now - 86400, now - 20, 30):
            day = (ts % 86400) / 86400
            wave = 0.55 + 0.45 * math.sin(2 * math.pi * (day - 0.3 - rid * 0.07))
            down = host.startswith("demo-down") or (host.startswith("demo-flap") and int(ts // 600) % 3 == 2)
            if down:
                m.append((rid, ts, 0) + (None,) * 14)
                continue
            cpu = max(1, min(100, 8 + wave * 25 + rid * 3 % 10 + rnd.uniform(-3, 4)))
            if host.startswith("demo-hot"):
                cpu = min(99, 82 + 14 * abs(math.sin(ts / 900)) + rnd.uniform(-2, 2))
            memp = 38 + (rid * 11 % 25) + 4 * math.sin(ts / 3000)
            mt = 256 * 1024 * 1024 if rid % 2 else 1024 ** 3
            rx = base * wave * 1e6 * rnd.uniform(0.7, 1.3)
            tx = rx * 0.22 * rnd.uniform(0.6, 1.4) * 3
            m.append((rid, ts, 1, cpu, memp, mt * memp / 100, mt, 12 + rid % 9,
                      41 + wave * 12 + rid % 6 + (28 if host.startswith("demo-hot") else 0), 24.0 + rnd.uniform(-.1, .1),
                      rx, tx, 12 + rid * 9 % 60 + rnd.uniform(0, 8) + (350 if host.startswith("demo-lag") else 0),
                      rid % 4, int(20 + wave * 40 + rid * 3 % 20), int(5 + wave * 25), int(2000 + wave * 6000 + rid * 100)))
            for n, k in (("ether1-WAN", 1.0), ("ether2-LAN", .8), ("bridge-lan", .9), ("wlan1", .3), ("vpn-ke-pusat", .02)):
                rr = base * wave * 1e6 * k * rnd.uniform(.7, 1.3)
                im.append((rid, ts, n, rr, rr * .25 * (3 if n == "ether1-WAN" else 1)))
        c.executemany("INSERT INTO metrics VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", m)
        c.executemany("INSERT INTO iface_metrics VALUES(?,?,?,?,?)", im)
        # tandai beberapa interface agar dipantau
        db.ex("INSERT OR IGNORE INTO watched_ifaces VALUES(?,?)", (rid, "ether1-WAN"))
    c.commit()
    db.add_event("info", "demo", "Mode demo aktif — router simulasi dibuat")
