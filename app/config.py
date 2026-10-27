import os, hashlib, base64, secrets

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "monitor.db")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
TZ_NAME = os.environ.get("TZ", "Asia/Jakarta")
DEMO = os.environ.get("DEMO", "0") == "1"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"   # 1 bila diakses lewat HTTPS (reverse proxy)


def _secret():
    s = os.environ.get("SECRET_KEY")
    if s:
        return s
    p = os.path.join(DATA_DIR, "secret.key")
    if os.path.exists(p):
        return open(p).read().strip()
    s = secrets.token_hex(32)
    with open(p, "w") as f:
        f.write(s)
    os.chmod(p, 0o600)
    return s


SECRET_KEY = _secret()
FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(("fernet:" + SECRET_KEY).encode()).digest())

APP_VERSION = "2026.10.26-sesi"   # tampil di sidebar & /healthz untuk memastikan versi yang berjalan
