"""RouterOS API palsu (cukup untuk uji fitur VPN): login, ppp secret/active, nat, interface, profile."""
import socket, threading, itertools

def enc_len(n):
    if n < 0x80: return bytes([n])
    if n < 0x4000: return (n | 0x8000).to_bytes(2, "big")
    return (n | 0xC00000).to_bytes(3, "big")

def sentence(words):
    return b"".join(enc_len(len(w.encode())) + w.encode() for w in words) + b"\x00"

class FakeRouter:
    def __init__(self):
        self.deny = False
        self.ids = itertools.count(1)
        self.t = {"/ppp/secret": [], "/ip/firewall/nat": [], "/ppp/active": [], "/ip/hotspot/active": [], "/interface": [], "/ip/route": [{".id": "*R1", "dst-address": "0.0.0.0/0", "gateway": "192.168.101.1", "active": "true", "disabled": "false", "distance": "1"}], "/ppp/profile": [{".id": "*p1", "name": "default"}]}
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(8); self.port = self.srv.getsockname()[1]
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try: c, _ = self.srv.accept()
            except OSError: return
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def rd(self, c):
        words = []
        while True:
            b = c.recv(1)
            if not b: return None
            n = b[0]
            if n >= 0x80:
                n = ((n & 0x3F) << 8) | c.recv(1)[0] if n & 0xC0 == 0x80 else n
            if n == 0: return words
            data = b""
            while len(data) < n: data += c.recv(n - len(data))
            words.append(data.decode())

    def serve(self, c):
        while True:
            s = self.rd(c)
            if s is None: break
            cmd, args, qs = s[0], {}, []
            for w in s[1:]:
                if w.startswith("="): k, _, v = w[1:].partition("="); args[k] = v
                elif w.startswith("?"): qs.append(w[1:])
            out = self.handle(cmd, args, qs)
            for row in out.get("rows", []):
                c.sendall(sentence(["!re"] + [f"={k}={v}" for k, v in row.items()]))
            if "trap" in out: c.sendall(sentence(["!trap", "=message=" + out["trap"]]))
            c.sendall(sentence(["!done"])); 
        c.close()

    def handle(self, cmd, a, qs):
        if cmd == "/login": return {} if a.get("password") == "pw" else {"trap": "invalid user name or password"}
        if self.deny and cmd in ("/ping", "/tool/traceroute"): return {"trap": "not enough permissions (9)"}
        if cmd == "/ping":
            if a["address"].endswith(".invalid"): return {"trap": "failure: dns name does not exist"}
            if a["address"] == "pubgmobile.com": return {"rows": [{"status": "timeout", "sent": "3", "received": "0", "packet-loss": "100"}]}
            n = int(a["count"]); ipa = a["address"] if a["address"][0].isdigit() else "142.250.1.1"
            rows = [{"seq": str(i), "host": ipa, "time": f"{10+i}ms500us", "ttl": "57", "size": "56",
                                          "sent": str(i + 1), "received": str(i + 1), "packet-loss": "0",
                                          "min-rtt": "10ms100us", "avg-rtt": "11ms200us", "max-rtt": f"{10+i}ms900us"} for i in range(n)]
            return {"rows": rows}
        if cmd == "/tool/traceroute":
            cyc = [{"address": "10.0.0.1", "loss": "0%", "sent": "1", "last": "1ms", "avg": "1ms", "best": "1ms", "worst": "2ms"},
                   {"address": "", "loss": "100%", "sent": "1"},
                   {"address": a["address"], "loss": "0%", "sent": "1", "last": "9ms", "avg": "9ms", "best": "9ms", "worst": "9ms"}]
            return {"rows": cyc + cyc}
        if cmd == "/log/print":
            return {"rows": [{"time": "10:00:01", "topics": "system,info", "message": "login ok"},
                             {"time": "10:00:05", "topics": "l2tp,ppp,info", "message": "budi logged in"},
                             {"time": "10:01:00", "topics": "system,error", "message": "boom"}]}
        if cmd == "/system/identity/print": return {"rows": [{"name": "fake-router"}]}
        if cmd == "/interface/l2tp-server/server/print": return {"rows": [{"enabled": "true"}]}
        if cmd == "/system/resource/print": return {"rows": [{"version": "7.15", "board-name": "fake"}]}
        for base in self.t:
            if cmd.startswith(base + "/"):
                op, rows = cmd[len(base) + 1:], self.t[base]
                if op == "print":
                    sel = rows
                    for q in qs:
                        k, _, v = q.partition("="); sel = [r for r in sel if r.get(k) == v]
                    return {"rows": [dict(r) for r in sel]}
                if op == "add":
                    if base == "/ppp/secret" and any(r["name"] == a["name"] for r in rows): return {"trap": "failure: already have such entry"}
                    if a.get("fail-me"): return {"trap": "boom"}
                    r = {".id": f"*{next(self.ids):X}", **a}; rows.append(r)
                    if base == "/ppp/secret": r["disabled"] = "false"
                    return {}
                if op == "remove": self.t[base][:] = [r for r in rows if r[".id"] != a[".id"]]; return {}
                if op in ("disable", "enable"):
                    for r in rows:
                        if r[".id"] == a[".id"]: r["disabled"] = "true" if op == "disable" else "false"
                    return {}
        return {"trap": "no such command"}
