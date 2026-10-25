"""Pengambil data dari RouterOS API (blocking; dipanggil lewat thread pool)."""
from __future__ import annotations

import re
import socket
import ssl
import time
from typing import Any

_UPTIME_RE = re.compile(r"(\d+)([wdhms])")
_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_uptime(text: str | int | None) -> int:
    if text is None:
        return 0
    if isinstance(text, int):
        return text
    return sum(int(n) * _UNITS[u] for n, u in _UPTIME_RE.findall(str(text)))


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def tcp_latency(host: str, port: int, timeout: float) -> float:
    """Waktu jabat-tangan TCP ke port API (ms) - ukuran latensi lewat tunnel VPN."""
    t0 = time.perf_counter()
    with socket.create_connection((host, port), timeout=timeout):
        pass
    return (time.perf_counter() - t0) * 1000.0


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def collect(router: dict, password: str, timeout: float = 6.0) -> dict:
    """Kembalikan snapshot dict. Raise Exception bila gagal terhubung."""
    import librouteros
    from librouteros.exceptions import TrapError  # noqa: F401

    host, port = router["host"], int(router["port"])
    latency = tcp_latency(host, port, timeout)

    kwargs: dict[str, Any] = dict(
        host=host, port=port, username=router["username"], password=password, timeout=timeout
    )
    if router.get("use_ssl"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("ADH:@SECLEVEL=0")  # API-SSL MikroTik pakai anonymous DH bila tanpa sertifikat
        kwargs["ssl_wrapper"] = ctx.wrap_socket
    api = librouteros.connect(**kwargs)
    try:
        res = list(api.path("system", "resource"))[0]
        identity = _safe(lambda: list(api.path("system", "identity"))[0].get("name", ""), "") or router["name"]

        # health: v6 = satu baris; v7 = daftar name/value
        temp = volt = None
        health = _safe(lambda: list(api.path("system", "health")), []) or []
        for row in health:
            if "name" in row:
                n = row.get("name", "")
                if n in ("temperature", "cpu-temperature", "board-temperature1") and temp is None:
                    temp = _num(row.get("value"))
                elif n == "voltage" and volt is None:
                    volt = _num(row.get("value"))
            else:
                temp = temp if temp is not None else _num(row.get("temperature") or row.get("cpu-temperature"))
                volt = volt if volt is not None else _num(row.get("voltage"))

        ifaces = []
        for r in api.path("interface"):
            ifaces.append(
                {
                    "name": r.get("name", ""),
                    "type": r.get("type", ""),
                    "running": bool(r.get("running", False)),
                    "disabled": bool(r.get("disabled", False)),
                    "rx": int(r.get("rx-byte", 0) or 0),
                    "tx": int(r.get("tx-byte", 0) or 0),
                    "comment": r.get("comment", ""),
                }
            )

        ppp = _safe(lambda: sum(1 for _ in api.path("ppp", "active")), None)
        dhcp = _safe(
            lambda: sum(1 for r in api.path("ip", "dhcp-server", "lease") if r.get("status") == "bound"), None
        )
        wifi = _safe(lambda: sum(1 for _ in api.path("interface", "wireless", "registration-table")), None)
        if wifi is None:  # RouterOS 7 paket wifi baru
            wifi = _safe(lambda: sum(1 for _ in api.path("interface", "wifi", "registration-table")), None)

        # rute default aktif -> petunjuk uplink
        gw_iface = None

        def _default_gw():
            for r in api.path("ip", "route"):
                if r.get("dst-address") == "0.0.0.0/0" and r.get("active") and not r.get("disabled"):
                    return r.get("vrf-interface") or r.get("immediate-gw") or None
            return None

        gw_iface = _safe(_default_gw)

        total_mem = int(res.get("total-memory", 0) or 0)
        free_mem = int(res.get("free-memory", 0) or 0)
        total_hdd = int(res.get("total-hdd-space", 0) or 0)
        free_hdd = int(res.get("free-hdd-space", 0) or 0)

        return {
            "identity": identity,
            "board": res.get("board-name", ""),
            "version": res.get("version", ""),
            "arch": res.get("architecture-name", ""),
            "cpu_count": int(res.get("cpu-count", 1) or 1),
            "uptime": parse_uptime(res.get("uptime")),
            "cpu": _num(res.get("cpu-load"), 0.0),
            "mem_total": total_mem,
            "mem_free": free_mem,
            "disk_total": total_hdd,
            "disk_free": free_hdd,
            "temp": temp,
            "volt": volt,
            "latency": latency,
            "ifaces": ifaces,
            "ppp": ppp,
            "dhcp": dhcp,
            "wifi": wifi,
            "gw_hint": gw_iface,
            "ts": time.time(),
        }
    finally:
        try:
            api.close()
        except Exception:
            pass


def test_connection(router: dict, password: str, timeout: float = 6.0) -> dict:
    snap = collect(router, password, timeout)
    return {
        "identity": snap["identity"],
        "board": snap["board"],
        "version": snap["version"],
        "uptime": snap["uptime"],
        "latency": round(snap["latency"], 1),
        "interfaces": len(snap["ifaces"]),
    }
