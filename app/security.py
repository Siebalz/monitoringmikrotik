import os, hmac, base64, hashlib
from cryptography.fernet import Fernet
from .config import FERNET_KEY

_f = Fernet(FERNET_KEY)


def enc(s: str) -> str:
    return _f.encrypt((s or "").encode()).decode()


def dec(s: str) -> str:
    try:
        return _f.decrypt((s or "").encode()).decode()
    except Exception:
        return ""


# ---- password akun klien: scrypt + salt acak, format "scrypt$n$r$p$salt$hash"
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(h).decode())


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


_DUMMY = hash_password("dummy-untuk-menyamakan-waktu")
