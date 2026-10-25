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
