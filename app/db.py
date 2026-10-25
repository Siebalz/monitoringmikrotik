import sqlite3, threading, time, json
from .config import DB_PATH

_local = threading.local()

DEFAULT_SETTINGS = {
    "poll_interval": 30,        # detik
    "offline_after": 3,         # gagal berturut-turut sebelum dianggap DOWN
    "sustain": 3,               # poll berturut-turut sebelum alert threshold
    "cpu_limit": 90,
    "mem_limit": 90,
    "temp_limit": 75,
    "latency_limit": 300,       # ms
    "repeat_minutes": 0,        # ulang alert yg masih aktif (0 = mati)
    "notify_recovery": True,
    "notify_reboot": True,
    "metrics_retention_days": 60,
    "iface_retention_days": 7,
    "tg_enabled": True,
    "tg_token": "",
    "tg_chat_ids": "",
    # --- Network Latency (ping dari router ke internet) ---
    "latency_enabled": True,
    "latency_interval": 60,     # detik antar siklus ping
    "latency_gateway": True,    # ikut ping default gateway router
    "latency_targets": "Google:google.com,YouTube:youtube.com,Mobile Legends:mobilelegends.com,PUBG Mobile:pubgmobile.com,Instagram:instagram.com,Facebook:facebook.com",
    "latency_warn": 100,        # ms: di atas ini oranye
    "latency_bad": 200,         # ms: di atas ini merah
    "latency_retention_days": 7,
    # --- VPN ---
    "vpn_ip_start": 10,         # rentang IP statis untuk user VPN baru (oktet terakhir)
    "vpn_ip_end": 254,
    "vpn_nat_port_min": 20000,  # rentang port publik untuk port-forward
    "vpn_nat_port_max": 60000,
    "vpn_services": "Winbox:8291,API:8728,WebFig:80,HTTPS:2000",   # Label:port-di-client
    "vpn_monitor_address": "",  # sumber yang boleh akses API di router client (kosong = subnet VPN /24)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS routers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER NOT NULL DEFAULT 8728,
  username TEXT NOT NULL DEFAULT 'monitor',
  password_enc TEXT NOT NULL DEFAULT '',
  use_ssl INTEGER NOT NULL DEFAULT 0,
  group_name TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  wan_iface TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  alerts_enabled INTEGER NOT NULL DEFAULT 1,
  cpu_limit REAL, mem_limit REAL, temp_limit REAL, latency_limit REAL,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS router_state(
  router_id INTEGER PRIMARY KEY,
  online INTEGER NOT NULL DEFAULT 0,
  fail_count INTEGER NOT NULL DEFAULT 0,
  last_ok INTEGER, last_poll INTEGER, last_error TEXT DEFAULT '',
  down_since INTEGER,
  info TEXT DEFAULT '{}', latest TEXT DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS metrics(
  router_id INTEGER NOT NULL, ts INTEGER NOT NULL, online INTEGER NOT NULL,
  cpu REAL, mem_pct REAL, mem_used REAL, mem_total REAL, hdd_pct REAL,
  temp REAL, volt REAL, rx_bps REAL, tx_bps REAL, latency REAL,
  ppp INTEGER, dhcp INTEGER, wifi INTEGER, conn INTEGER
);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(router_id, ts);
CREATE INDEX IF NOT EXISTS ix_metrics_ts ON metrics(ts);
CREATE TABLE IF NOT EXISTS iface_metrics(
  router_id INTEGER NOT NULL, ts INTEGER NOT NULL, name TEXT NOT NULL,
  rx_bps REAL, tx_bps REAL
);
CREATE INDEX IF NOT EXISTS ix_iface ON iface_metrics(router_id, name, ts);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
  router_id INTEGER, router_name TEXT DEFAULT '',
  level TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events ON events(ts);
CREATE TABLE IF NOT EXISTS alert_state(
  router_id INTEGER NOT NULL, key TEXT NOT NULL, level TEXT NOT NULL,
  since INTEGER NOT NULL, last_sent INTEGER NOT NULL, message TEXT DEFAULT '',
  PRIMARY KEY(router_id, key)
);
CREATE TABLE IF NOT EXISTS watched_ifaces(
  router_id INTEGER NOT NULL, name TEXT NOT NULL, PRIMARY KEY(router_id, name)
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS latency(
  router_id INTEGER NOT NULL, ts INTEGER NOT NULL, target TEXT NOT NULL,
  ms REAL, loss REAL, ip TEXT NOT NULL DEFAULT '', err TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_latency ON latency(router_id, target, ts);
CREATE INDEX IF NOT EXISTS ix_latency_ts ON latency(ts);
CREATE TABLE IF NOT EXISTS vpn_servers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  host TEXT NOT NULL,            -- alamat untuk koneksi API (dari server ini)
  port INTEGER NOT NULL DEFAULT 8728,
  username TEXT NOT NULL,
  password_enc TEXT NOT NULL DEFAULT '',
  use_ssl INTEGER NOT NULL DEFAULT 0,
  public_ip TEXT NOT NULL DEFAULT '',   -- IP/domain publik: tujuan L2TP client & port-forward
  vpn_subnet TEXT NOT NULL DEFAULT '',  -- contoh "11.11.11."
  profile TEXT NOT NULL DEFAULT 'default',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS vpn_links(      -- user VPN yang otomatis didaftarkan ke monitoring
  server_id INTEGER NOT NULL, username TEXT NOT NULL, router_id INTEGER NOT NULL,
  PRIMARY KEY(server_id, username)
);
"""


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.c = c
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    try:                                   # migrasi database lama (sebelum kolom err ada)
        c.execute("ALTER TABLE latency ADD COLUMN err TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    c.commit()


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def ex(sql, args=()):
    c = conn()
    cur = c.execute(sql, args)
    c.commit()
    return cur


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    for r in conn().execute("SELECT key,value FROM settings"):
        if r["key"] in s:
            try:
                s[r["key"]] = json.loads(r["value"])
            except Exception:
                pass
    return s


def save_settings(d):
    c = conn()
    for k, v in d.items():
        if k in DEFAULT_SETTINGS:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (k, json.dumps(v)))
    c.commit()


def add_event(level, kind, message, router_id=None, router_name=""):
    ex("INSERT INTO events(ts,router_id,router_name,level,kind,message) VALUES(?,?,?,?,?,?)",
       (int(time.time()), router_id, router_name, level, kind, message))
