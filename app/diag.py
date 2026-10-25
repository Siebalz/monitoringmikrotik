"""Alat diagnosa jarak jauh: ping / traceroute dari router + log router (lewat RouterOS API)."""
import re
from . import ros

ADDR_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-:]{0,251}[A-Za-z0-9])?$")   # IPv4/IPv6/hostname


class DiagError(Exception):
    pass


def _addr(a):
    a = (a or "").strip()
    if not ADDR_RE.match(a):
        raise DiagError("Alamat tidak valid. Isi IP atau nama host (tanpa spasi/karakter khusus).")
    return a


def _friendly(e):
    m = str(e)
    if "not enough permissions" in m.lower():
        return DiagError("Izin user API kurang: ping/traceroute butuh policy 'test' dan log butuh 'read'. "
                         "Jalankan di router: /user group set monitor policy=read,api,test")
    return DiagError(m)


_num = ros.parse_ms


def ping(conn, address, count=4, size=None):
    address = _addr(address)
    count = max(1, min(int(count), 10))
    attrs = {"address": address, "count": str(count)}
    if size:
        attrs["size"] = str(max(28, min(int(size), 1500)))
    c = conn(timeout=count + 12)
    try:
        rows, _ = c.call("/ping", attrs)
    except ros.RosError as e:
        raise _friendly(e)
    finally:
        c.close()
    replies = []
    for r in rows:
        t = r.get("time")
        replies.append({"seq": int(r.get("seq", len(replies)) or 0), "ok": t is not None and not r.get("status"),
                        "ms": _num(t), "raw": t, "ttl": r.get("ttl"), "size": r.get("size"), "host": r.get("host", address),
                        "status": r.get("status", "")})
    last = rows[-1] if rows else {}
    sent = int(last.get("sent", len(rows)) or 0)
    recv = int(last.get("received", sum(1 for x in replies if x["ok"])) or 0)
    return {"address": address, "replies": replies, "sent": sent, "received": recv,
            "loss": float(last.get("packet-loss", (100 * (sent - recv) / sent) if sent else 100) or 0),
            "min": _num(last.get("min-rtt")), "avg": _num(last.get("avg-rtt")), "max": _num(last.get("max-rtt"))}


def traceroute(conn, address, max_hops=20):
    address = _addr(address)
    c = conn(timeout=60)
    try:
        rows, _ = c.call("/tool/traceroute", {"address": address, "count": "2", "max-hops": str(max(1, min(int(max_hops), 30))),
                                              "timeout": "1s"})
    except ros.RosError as e:
        raise _friendly(e)
    finally:
        c.close()
    # RouterOS mengirim ulang seluruh tabel hop tiap update -> ambil siklus terakhir.
    if rows and ".section" in rows[0]:
        last = {}
        for r in rows:
            last[r[".section"]] = r
        cycle = [last[k] for k in sorted(last, key=lambda x: int(x))]
    else:
        first = rows[0].get("address", "") if rows else ""
        starts = [i for i, r in enumerate(rows) if r.get("address", "") == first]
        cycle = rows[starts[-1]:] if starts else rows
    hops = []
    for i, r in enumerate(cycle, 1):
        a = r.get("address", "")
        hops.append({"hop": i, "address": a if a and a != "0.0.0.0" else "*", "loss": _num(r.get("loss")),
                     "sent": r.get("sent"), "last": _num(r.get("last")), "avg": _num(r.get("avg")),
                     "best": _num(r.get("best")), "worst": _num(r.get("worst"))})
    return {"address": address, "hops": hops, "reached": bool(hops) and hops[-1]["address"] == address}


def log(conn, limit=200, topic="", search=""):
    c = conn(timeout=20)
    try:
        rows, _ = c.call("/log/print", proplist=["time", "topics", "message"])
    except ros.RosError as e:
        raise _friendly(e)
    finally:
        c.close()
    topic, search = topic.strip().lower(), search.strip().lower()
    out = [{"time": r.get("time", ""), "topics": r.get("topics", ""), "message": r.get("message", "")} for r in rows
           if (not topic or topic in r.get("topics", "").lower()) and (not search or search in r.get("message", "").lower())]
    total = len(out)
    out = out[-max(1, min(int(limit), 1000)):][::-1]          # terbaru di atas
    return {"lines": out, "total": total, "topics": sorted({t for r in rows for t in r.get("topics", "").split(",") if t})}
