"""Manajemen VPN L2TP di MikroTik server: PPP secret + NAT port-forward + script client.

Port dari vpn_service.php (vpnserver) dengan beberapa perbaikan:
- port NAT dipilih bebas-tabrakan (cek NAT yang sudah ada)
- escape script RouterOS lengkap (\\ " $ ?)
- pembersihan NAT yatim hanya lewat aksi eksplisit (bukan tiap GET)
- opsional: user VPN langsung didaftarkan ke monitoring
"""
import re, secrets, string, random, time
from . import ros, db
from .security import enc, dec

USER_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,40}$")
PREFIX = "VPN-"


class VpnError(Exception):
    pass


# ------------------------------------------------------------------ helpers
def connect(server):
    return ros.connect(server["host"], server["port"], server["use_ssl"], server["username"], dec(server["password_enc"]))


def parse_services(s):
    out = []
    for part in (s or "").split(","):
        if ":" in part:
            n, _, p = part.strip().rpartition(":")
            if n.strip() and p.strip().isdigit():
                out.append({"name": re.sub(r"[^A-Za-z0-9_]", "", n) or "svc", "port": int(p)})
    return out


def gen_password(n=16):
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


def ros_esc(s):
    """Escape string untuk literal "..." di script RouterOS."""
    for a, b in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"), ("?", "\\?")):
        s = s.replace(a, b)
    return s


def subnet_cidr(prefix):
    p = (prefix or "").strip().rstrip(".")
    return f"{p}.0/24" if p.count(".") == 2 else ""


def client_script(server, username, password, monitor_pw=None, monitor_addr=""):
    conn = re.sub(r"[^a-zA-Z0-9_-]", "", username) + "-vpn"
    lines = [f'/interface l2tp-client add name={conn} connect-to={server["public_ip"] or server["host"]} '
             f'user="{ros_esc(username)}" password="{ros_esc(password)}" disabled=no']
    if monitor_pw:
        addr = monitor_addr or subnet_cidr(server["vpn_subnet"])
        a = f" address={addr}" if addr else ""
        lines += [
            "# --- akses monitoring (read-only) untuk server Pantau ---",
            ':do { /user group add name=monitor policy=read,api,test } on-error={}',
            f'/user add name=monitor group=monitor password="{ros_esc(monitor_pw)}"{a}',
            f"/ip service set api disabled=no{a}",
        ]
    return "\n".join(lines)


# ------------------------------------------------------------------ operasi
def _nat_rows(c):
    rows, _ = c.call("/ip/firewall/nat/print", proplist=[".id", "comment", "dst-port", "to-addresses", "chain"])
    return rows


def _free_ip(secrets_rows, subnet, start, end):
    used = {s.get("remote-address") for s in secrets_rows if s.get("remote-address")}
    for i in range(int(start), int(end) + 1):
        if f"{subnet}{i}" not in used:
            return f"{subnet}{i}"
    return None


def _pick_ports(nat_rows, n, lo, hi):
    used = set()
    for r in nat_rows:
        for p in (r.get("dst-port") or "").split(","):
            if p.strip().isdigit():
                used.add(int(p))
    for _ in range(300):
        base = random.randint(int(lo), int(hi) - n)
        if all((base + i) not in used for i in range(n)):
            return [base + i for i in range(n)]
    raise VpnError("Tidak menemukan port publik kosong pada rentang yang diatur.")


def list_users(server):
    """Gabungkan secret + sesi aktif + traffic + NAT. Return dict."""
    c = connect(server)
    try:
        sec, _ = c.call("/ppp/secret/print")
        act, _ = c.call("/ppp/active/print")
        nat = _nat_rows(c)
        try:
            ifs, _ = c.call("/interface/print", proplist=["name", "type", "rx-byte", "tx-byte"])
        except ros.RosError:
            ifs = []
        try:
            profs = [p.get("name") for p in c.call("/ppp/profile/print", proplist=["name"])[0]]
        except ros.RosError:
            profs = ["default"]
    finally:
        c.close()
    active = {a.get("name"): a for a in act}
    traffic = {}
    for i in ifs:
        n = (i.get("name") or "").strip("<>")
        n = re.sub(r"^(l2tp|sstp|ovpn|pptp|pppoe)-", "", n)
        if n:
            traffic[n] = (int(i.get("rx-byte", 0) or 0), int(i.get("tx-byte", 0) or 0))
    pub = server["public_ip"] or server["host"]
    names = {s.get("name") for s in sec}
    links = {r["username"]: r["router_id"] for r in db.q("SELECT username, router_id FROM vpn_links WHERE server_id=?", (server["id"],))}
    users = []
    for s in sec:
        n = s.get("name", "")
        a = active.get(n)
        pre = f"{PREFIX}{n}-"
        access = [{"name": r["comment"][len(pre):], "host": pub, "port": r.get("dst-port", "-")}
                  for r in nat if (r.get("comment") or "").startswith(pre)]
        rx, tx = traffic.get(n, (None, None))
        users.append({
            "id": s.get(".id"), "name": n, "profile": s.get("profile", ""), "service": s.get("service", ""),
            "address": s.get("remote-address", ""), "disabled": s.get("disabled") == "true", "comment": s.get("comment", ""),
            "online": bool(a), "uptime": a.get("uptime") if a else None, "caller": a.get("caller-id") if a else None,
            "rx": rx if a else None, "tx": tx if a else None, "access": access, "router_id": links.get(n),
        })
    orphans = [r for r in nat if (r.get("comment") or "").startswith(PREFIX) and not _owner(r["comment"], names)]
    users.sort(key=lambda u: (not u["online"], u["name"].lower()))
    return {"users": users, "profiles": profs, "orphan_nat": len(orphans),
            "summary": {"total": len(users), "online": sum(1 for u in users if u["online"]),
                        "disabled": sum(1 for u in users if u["disabled"])}}


def _owner(comment, names):
    rest = comment[len(PREFIX):]
    return any(rest.startswith(n + "-") for n in names)


def create_user(server, username, password, profile, services, s, monitor=False, group=""):
    if not USER_RE.match(username or ""):
        raise VpnError("Username hanya boleh huruf, angka, titik, minus, underscore, @ (maks 40 karakter).")
    password = password or gen_password()
    subnet = server["vpn_subnet"]
    if not subnet:
        raise VpnError("Subnet VPN server belum diisi (contoh 11.11.11.).")
    pub = server["public_ip"] or server["host"]
    c = connect(server)
    try:
        sec, _ = c.call("/ppp/secret/print", proplist=["name", "remote-address"])
        if any(x.get("name") == username for x in sec):
            raise VpnError(f"User '{username}' sudah ada.")
        ip = _free_ip(sec, subnet, s["vpn_ip_start"], s["vpn_ip_end"])
        if not ip:
            raise VpnError("Alokasi IP statis pada subnet ini sudah habis.")
        nat = _nat_rows(c)
        ports = _pick_ports(nat, len(services), s["vpn_nat_port_min"], s["vpn_nat_port_max"]) if services else []
        c.call("/ppp/secret/add", {"name": username, "password": password, "profile": profile or server["profile"] or "default",
                                  "service": "l2tp", "remote-address": ip})
        access = []
        try:
            for svc, port in zip(services, ports):
                c.call("/ip/firewall/nat/add", {
                    "chain": "dstnat", "action": "dst-nat", "dst-address": pub, "protocol": "tcp",
                    "dst-port": str(port), "to-addresses": ip, "to-ports": str(svc["port"]),
                    "comment": f"{PREFIX}{username}-{svc['name']}"})
                access.append({"name": svc["name"], "host": pub, "port": port})
        except ros.RosError:
            _rollback(c, username)     # jangan tinggalkan setengah jadi
            raise
    finally:
        c.close()
    res = {"ok": True, "username": username, "password": password, "ip": ip, "access": access, "router_id": None, "monitor_password": None}
    if monitor:
        mpw = gen_password()
        cur = db.ex("""INSERT INTO routers(name,host,port,username,password_enc,use_ssl,group_name,notes,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (username, ip, 8728, "monitor", enc(mpw), 0,
                     group or f"VPN {server['name']}", f"Dibuat dari VPN {server['name']}", int(time.time())))
        res["router_id"], res["monitor_password"] = cur.lastrowid, mpw
        db.ex("INSERT OR REPLACE INTO vpn_links VALUES(?,?,?)", (server["id"], username, cur.lastrowid))
    res["client_script"] = client_script(server, username, password, res["monitor_password"], s["vpn_monitor_address"])
    return res


def _rollback(c, username):
    try:
        for r in _nat_rows(c):
            if (r.get("comment") or "").startswith(f"{PREFIX}{username}-"):
                c.call("/ip/firewall/nat/remove", {".id": r[".id"]})
        sec, _ = c.call("/ppp/secret/print", queries=[f"?name={username}"])
        for x in sec:
            c.call("/ppp/secret/remove", {".id": x[".id"]})
    except ros.RosError:
        pass


def delete_user(server, secret_id):
    c = connect(server)
    try:
        sec, _ = c.call("/ppp/secret/print", queries=[f"?.id={secret_id}"])
        if not sec:
            raise VpnError("User tidak ditemukan.")
        name = sec[0]["name"]
        c.call("/ppp/secret/remove", {".id": secret_id})
        act, _ = c.call("/ppp/active/print", queries=[f"?name={name}"])
        for a in act:
            try:
                c.call("/ppp/active/remove", {".id": a[".id"]})   # putuskan sesi yang masih tersambung
            except ros.RosError:
                pass
        removed = 0
        for r in _nat_rows(c):
            if (r.get("comment") or "").startswith(f"{PREFIX}{name}-"):
                c.call("/ip/firewall/nat/remove", {".id": r[".id"]})
                removed += 1
    finally:
        c.close()
    return {"username": name, "removed_nat": removed}


def set_disabled(server, secret_id, disabled):
    c = connect(server)
    try:
        sec, _ = c.call("/ppp/secret/print", queries=[f"?.id={secret_id}"])
        if not sec:
            raise VpnError("User tidak ditemukan.")
        c.call("/ppp/secret/" + ("disable" if disabled else "enable"), {".id": secret_id})
        if disabled:
            for a in c.call("/ppp/active/print", queries=[f"?name={sec[0]['name']}"])[0]:
                try:
                    c.call("/ppp/active/remove", {".id": a[".id"]})
                except ros.RosError:
                    pass
        return sec[0]["name"]
    finally:
        c.close()


def cleanup_orphans(server):
    c = connect(server)
    try:
        names = {x.get("name") for x in c.call("/ppp/secret/print", proplist=["name"])[0]}
        n = 0
        for r in _nat_rows(c):
            cm = r.get("comment") or ""
            if cm.startswith(PREFIX) and not _owner(cm, names):
                c.call("/ip/firewall/nat/remove", {".id": r[".id"]})
                n += 1
        return n
    finally:
        c.close()


def get_script(server, secret_id, s):
    c = connect(server)
    try:
        sec, _ = c.call("/ppp/secret/print", queries=[f"?.id={secret_id}"])
    finally:
        c.close()
    if not sec:
        raise VpnError("User tidak ditemukan.")
    u = sec[0]
    mpw = None
    lk = db.q1("SELECT router_id FROM vpn_links WHERE server_id=? AND username=?", (server["id"], u["name"]))
    if lk:
        r = db.q1("SELECT password_enc FROM routers WHERE id=?", (lk["router_id"],))
        mpw = dec(r["password_enc"]) if r else None
    return client_script(server, u["name"], u.get("password", ""), mpw, s["vpn_monitor_address"])
