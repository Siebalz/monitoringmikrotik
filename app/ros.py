"""Klien RouterOS API (port 8728 / 8729 TLS) ringan tanpa dependensi + simulator demo."""
import socket, ssl, time, re, hashlib, binascii, math, random, threading


class RosError(Exception):
    pass


class RosClient:
    def __init__(self, host, port=8728, use_ssl=False, timeout=20.0):
        self.host, self.port, self.use_ssl = host, int(port), use_ssl
        self.deadline = time.time() + timeout
        self.latency_ms = None
        t0 = time.time()
        try:
            s = socket.create_connection((host, self.port), timeout=min(6.0, timeout))
        except OSError as e:
            raise RosError(f"Tidak bisa konek ke {host}:{self.port} ({e.__class__.__name__}: {e})")
        self.latency_ms = (time.time() - t0) * 1000
        if use_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            try:
                ctx.set_ciphers("ALL:@SECLEVEL=0")  # RouterOS lama/ADH
            except ssl.SSLError:
                pass
            s = ctx.wrap_socket(s, server_hostname=host)
        self.sock = s

    # --- low level -------------------------------------------------------
    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            left = self.deadline - time.time()
            if left <= 0:
                raise RosError("Timeout menunggu respon router")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(n - len(buf))
            except (socket.timeout, TimeoutError):
                raise RosError("Timeout menunggu respon router")
            if not chunk:
                raise RosError("Koneksi ditutup oleh router")
            buf += chunk
        return buf

    @staticmethod
    def _enc_len(n):
        if n < 0x80:
            return bytes([n])
        if n < 0x4000:
            return (n | 0x8000).to_bytes(2, "big")
        if n < 0x200000:
            return (n | 0xC00000).to_bytes(3, "big")
        if n < 0x10000000:
            return (n | 0xE0000000).to_bytes(4, "big")
        return b"\xf0" + n.to_bytes(4, "big")

    def _read_len(self):
        c = self._recv(1)[0]
        if c < 0x80:
            return c
        if c & 0xC0 == 0x80:
            return ((c & 0x3F) << 8) | self._recv(1)[0]
        if c & 0xE0 == 0xC0:
            b = self._recv(2)
            return ((c & 0x1F) << 16) | (b[0] << 8) | b[1]
        if c & 0xF0 == 0xE0:
            b = self._recv(3)
            return ((c & 0x0F) << 24) | (b[0] << 16) | (b[1] << 8) | b[2]
        if c == 0xF0:
            return int.from_bytes(self._recv(4), "big")
        raise RosError("Format panjang kata tidak valid")

    def _write_sentence(self, words):
        out = b""
        for w in words:
            wb = w.encode("utf-8")
            out += self._enc_len(len(wb)) + wb
        out += b"\x00"
        self.sock.settimeout(max(0.5, self.deadline - time.time()))
        self.sock.sendall(out)

    def _read_sentence(self):
        words = []
        while True:
            n = self._read_len()
            if n == 0:
                return words
            words.append(self._recv(n).decode("utf-8", "replace"))

    # --- api -------------------------------------------------------------
    def call(self, command, attrs=None, queries=None, proplist=None):
        """Return (rows, done_attrs). Raise RosError pada !trap/!fatal."""
        words = [command]
        for k, v in (attrs or {}).items():
            words.append(f"={k}={v}")
        for qy in (queries or []):
            words.append(qy)
        if proplist:
            words.append(".proplist=" + ",".join(proplist))
        self._write_sentence(words)
        rows, done, err = [], {}, None
        while True:
            s = self._read_sentence()
            if not s:
                continue
            kind, rest = s[0], s[1:]
            d = {}
            for w in rest:
                if w.startswith("=") and "=" in w[1:]:
                    k, _, v = w[1:].partition("=")
                    d[k] = v
            if kind == "!re":
                rows.append(d)
            elif kind == "!done":
                done = d
                break
            elif kind == "!trap":
                err = d.get("message", "trap")
            elif kind == "!fatal":
                raise RosError("Fatal: " + " ".join(rest))
        if err:
            raise RosError(err)
        return rows, done

    def login(self, user, password):
        rows, done = self.call("/login", {"name": user, "password": password})
        if "ret" in done:  # RouterOS < 6.43 (challenge)
            ch = binascii.unhexlify(done["ret"])
            h = hashlib.md5(b"\x00" + password.encode() + ch).hexdigest()
            self.call("/login", {"name": user, "response": "00" + h})

    def count(self, command, queries=None):
        _, done = self.call(command, {"count-only": ""}, queries=queries)
        return int(done.get("ret", 0))

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|us|s)")


def parse_ms(v):
    """Format waktu RouterOS ('12ms', '2ms113us', '1s200ms', '500us', '12') -> milidetik float; None bila tak terbaca."""
    if v is None or v == "":
        return None
    v = str(v).strip()
    try:
        return float(v)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d+):(\d+):(\d+(?:\.\d+)?)", v)           # 00:00:00.016
    if m:
        return (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) * 1000.0
    parts = _TIME_RE.findall(v)
    if not parts:
        return None
    return sum(float(n) * {"s": 1000.0, "ms": 1.0, "us": 0.001}[u] for n, u in parts)


def connect(host, port, use_ssl, username, password, timeout=15.0):
    """Buka koneksi API yang sudah login (untuk operasi tulis: VPN server). Pemanggil wajib close()."""
    c = RosClient(host, port, bool(use_ssl), timeout=timeout)
    try:
        c.login(username, password)
    except RosError as e:
        c.close()
        raise RosError("Login gagal: " + str(e))
    return c


# ------------------------------------------------------------------------
_UP = re.compile(r"(?:(\d+)w)?(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")


def parse_uptime(s):
    m = _UP.fullmatch(s or "")
    if not m:
        return 0
    w, d, h, mi, se = [int(x) if x else 0 for x in m.groups()]
    return ((w * 7 + d) * 24 + h) * 3600 + mi * 60 + se


def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


_caps = {}  # (host, key) -> bool  (fitur yg tidak didukung router)


def _try(c, host, key, fn):
    if _caps.get((host, key)) is False:
        return None
    try:
        v = fn()
        _caps[(host, key)] = True
        return v
    except RosError as e:
        msg = str(e).lower()
        if "no such command" in msg or "unknown" in msg or "not found" in msg:
            _caps[(host, key)] = False
        return None


def fetch(router, password):
    """Ambil semua metrik dari 1 router. Raise RosError bila gagal."""
    c = RosClient(router["host"], router["port"], bool(router["use_ssl"]))
    try:
        try:
            c.login(router["username"], password)
        except RosError as e:
            raise RosError("Login gagal: " + str(e))
        host = router["host"]
        res, _ = c.call("/system/resource/print")
        res = res[0] if res else {}
        ident, _ = c.call("/system/identity/print")
        ident = ident[0].get("name", "") if ident else ""
        rb = _try(c, host, "rb", lambda: c.call("/system/routerboard/print")[0])
        rb = rb[0] if rb else {}
        health = _try(c, host, "health", lambda: c.call("/system/health/print")[0]) or []
        temp = volt = None
        for h in health:
            if "name" in h and "value" in h:      # RouterOS v7
                n = h["name"]
                if n in ("temperature", "cpu-temperature", "board-temperature1") and temp is None:
                    temp = _f(h["value"])
                elif n in ("voltage",):
                    volt = _f(h["value"])
            else:                                  # v6
                temp = _f(h.get("temperature"), _f(h.get("cpu-temperature")))
                volt = _f(h.get("voltage"))
        ifs, _ = c.call("/interface/print", proplist=["name", "type", "running", "disabled", "rx-byte", "tx-byte", "comment"])
        ifaces = [{
            "name": i.get("name", ""), "type": i.get("type", ""),
            "running": i.get("running") == "true", "disabled": i.get("disabled") == "true",
            "rx": int(i.get("rx-byte", 0) or 0), "tx": int(i.get("tx-byte", 0) or 0),
            "comment": i.get("comment", ""),
        } for i in ifs]
        ppp = _try(c, host, "ppp", lambda: c.count("/ppp/active/print"))
        dhcp = _try(c, host, "dhcp", lambda: c.count("/ip/dhcp-server/lease/print", ["?status=bound"]))
        wifi = None
        for key, path in (("wifi7", "/interface/wifi/registration-table/print"),
                          ("wifi2", "/interface/wifiwave2/registration-table/print"),
                          ("wifi6", "/interface/wireless/registration-table/print")):
            n = _try(c, host, key, lambda p=path: c.count(p))
            if n is not None:
                wifi = (wifi or 0) + n
        conn_n = _try(c, host, "conn", lambda: int(c.call("/ip/firewall/connection/tracking/print")[0][0].get("total-entries", 0)))
        mt = int(res.get("total-memory", 0) or 0)
        mf = int(res.get("free-memory", 0) or 0)
        ht = int(res.get("total-hdd-space", 0) or 0)
        hf = int(res.get("free-hdd-space", 0) or 0)
        return {
            "latency": c.latency_ms,
            "cpu": _f(res.get("cpu-load"), 0),
            "mem_total": mt, "mem_used": mt - mf,
            "mem_pct": ((mt - mf) / mt * 100) if mt else None,
            "hdd_pct": ((ht - hf) / ht * 100) if ht else None,
            "temp": temp, "volt": volt,
            "uptime": parse_uptime(res.get("uptime", "")),
            "ifaces": ifaces, "ppp": ppp, "dhcp": dhcp, "wifi": wifi, "conn": conn_n,
            "info": {
                "identity": ident,
                "version": res.get("version", ""),
                "board": res.get("board-name", ""),
                "model": rb.get("model", "") or res.get("board-name", ""),
                "arch": res.get("architecture-name", ""),
                "cpu_name": res.get("cpu", ""),
                "cpu_count": res.get("cpu-count", ""),
                "serial": rb.get("serial-number", ""),
                "firmware": rb.get("current-firmware", ""),
            },
        }
    finally:
        c.close()


# ------------------------------------------------------------------------
# Simulator untuk DEMO=1 (host berawalan "demo")
_mock_cnt = {}
_mock_lock = threading.Lock()
_BOOT = time.time()


def mock_fetch(router, password):
    rid = router["id"]
    host = router["host"]
    now = time.time()
    rnd = random.Random(rid * 7919 + int(now // 30))
    if host.startswith("demo-down"):
        raise RosError("Tidak bisa konek ke %s:8728 (TimeoutError: timed out)" % host)
    # router yg sesekali putus
    if host.startswith("demo-flap") and int(now // 600) % 3 == 2:
        raise RosError("Timeout menunggu respon router")
    day = (now % 86400) / 86400
    wave = 0.55 + 0.45 * math.sin(2 * math.pi * (day - 0.3 - rid * 0.07))
    base = 4 + (rid * 13 % 35)  # Mbps
    cpu = max(1, min(100, 8 + wave * 25 + rid * 3 % 10 + rnd.uniform(-3, 4)))
    if host.startswith("demo-hot"):
        cpu = min(99, 82 + 14 * abs(math.sin(now / 900)) + rnd.uniform(-2, 2))
    mem_total = 256 * 1024 * 1024 if rid % 2 else 1024 * 1024 * 1024
    mem_pct = 38 + (rid * 11 % 25) + 4 * math.sin(now / 3000)
    names = [("ether1-WAN", "ether"), ("ether2-LAN", "ether"), ("ether3", "ether"), ("bridge-lan", "bridge"),
             ("wlan1", "wlan"), ("vpn-ke-pusat", "l2tp-out")]
    rates = {"ether1-WAN": base * wave * 1e6 * 1.0, "ether2-LAN": base * wave * 0.8e6, "ether3": base * 0.1e6 * wave,
             "bridge-lan": base * wave * 0.9e6, "wlan1": base * wave * 0.3e6, "vpn-ke-pusat": 0.12e6 + 0.05e6 * wave}
    ifaces = []
    with _mock_lock:
        for n, t in names:
            key = (rid, n)
            prev_t, rx, tx = _mock_cnt.get(key, (now, rid * 1e9, rid * 3e8))
            dt = max(0, now - prev_t)
            r = rates[n] * rnd.uniform(0.7, 1.3)
            rx += r / 8 * dt * 1.0
            tx += r * 0.22 / 8 * dt * rnd.uniform(0.6, 1.4) * (3 if n == "ether1-WAN" else 1)
            _mock_cnt[key] = (now, rx, tx)
            running = not (n == "ether3" and host.startswith("demo-flap"))
            ifaces.append({"name": n, "type": t, "running": running, "disabled": False,
                           "rx": int(rx), "tx": int(tx), "comment": "Uplink ISP" if n == "ether1-WAN" else ""})
    return {
        "latency": 12 + rid * 9 % 60 + rnd.uniform(0, 8) + (350 if host.startswith("demo-lag") else 0),
        "cpu": cpu, "mem_total": mem_total, "mem_used": mem_total * mem_pct / 100, "mem_pct": mem_pct,
        "hdd_pct": 12 + rid % 9,
        "temp": 41 + wave * 12 + rid % 6 + (28 if host.startswith("demo-hot") else 0),
        "volt": 24.0 + rnd.uniform(-0.1, 0.1),
        "uptime": int(now - _BOOT) + rid * 86400 * 3,
        "ifaces": ifaces,
        "ppp": rid % 4, "dhcp": int(20 + wave * 40 + rid * 3 % 20), "wifi": int(5 + wave * 25), "conn": int(2000 + wave * 6000 + rid * 100),
        "info": {"identity": router["name"], "version": "7.15.2 (stable)", "board": "RB4011iGS+" if rid % 2 else "hAP ax3",
                 "model": "RB4011iGS+5HacQ2HnD" if rid % 2 else "C53UiG+5HPaxD2HPaxD", "arch": "arm64" if rid % 2 else "arm",
                 "cpu_name": "ARM64", "cpu_count": "4", "serial": "HEX%06d" % (rid * 7331), "firmware": "7.15"},
    }
