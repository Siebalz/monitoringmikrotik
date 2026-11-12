"""Publish portal Pantau lewat Cloudflare Tunnel: satu tunnel, satu subdomain per klien.

Alur: tunnel (dibuat di dashboard Zero Trust, dijalankan container cloudflared) menerima *.base_domain dan
meneruskan ke web Pantau. Tiap klien diberi record DNS CNAME  nama.base_domain -> <tunnel-id>.cfargotunnel.com
(proxied). Pantau mengenali klien dari nama host (lihat tenant_for_host di main.py).

Token API butuh izin: Zone:DNS:Edit (zone terkait) dan Account:Cloudflare Tunnel:Edit.
"""
import re
import requests
from . import db
from .security import dec

API = "https://api.cloudflare.com/client/v4"
SUB_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?$")
RESERVED = {"www", "admin", "api", "mail", "ftp", "ns1", "ns2", "static", "app", "portal", "login", "vpn", "monitor", "pantau"}


class CfError(Exception):
    pass


def cfg():
    s = db.get_settings()
    return {k: s[k] for k in ("cf_enabled", "cf_account_id", "cf_zone_id", "cf_tunnel_id", "cf_base_domain", "cf_service")} | \
           {"token": dec(s["cf_token"]) if s["cf_token"] else ""}


def ready(c=None):
    c = c or cfg()
    return bool(c["cf_enabled"] and c["token"] and c["cf_zone_id"] and c["cf_tunnel_id"] and c["cf_base_domain"])


def _request(method, path, token, body=None, params=None):
    try:
        r = requests.request(method, API + path, json=body, params=params, timeout=20,
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    except requests.RequestException as e:
        raise CfError(f"Tidak bisa menghubungi Cloudflare: {e.__class__.__name__}")
    try:
        j = r.json()
    except ValueError:
        raise CfError(f"Respon Cloudflare tidak valid (HTTP {r.status_code})")
    if not j.get("success"):
        errs = "; ".join(f"{e.get('message')} ({e.get('code')})" for e in j.get("errors", [])) or f"HTTP {r.status_code}"
        raise CfError(errs)
    return j.get("result")


def norm_base(d):
    return re.sub(r"^https?://", "", (d or "").strip().lower()).strip("/. ")


def host_of(sub, c=None):
    c = c or cfg()
    return f"{sub}.{norm_base(c['cf_base_domain'])}"


def check_sub(sub):
    sub = (sub or "").strip().lower()
    if not SUB_RE.match(sub):
        raise CfError("Subdomain 1–30 karakter: huruf kecil, angka, minus (tidak di awal/akhir).")
    if sub in RESERVED:
        raise CfError(f"Subdomain '{sub}' dicadangkan, pilih nama lain.")
    return sub


def test(c=None):
    c = c or cfg()
    if not (c["token"] and c["cf_zone_id"] and c["cf_account_id"] and c["cf_tunnel_id"] and c["cf_base_domain"]):
        raise CfError("Lengkapi token, Account ID, Zone ID, Tunnel ID, dan domain dasar dulu.")
    zone = _request("GET", f"/zones/{c['cf_zone_id']}", c["token"])
    base = norm_base(c["cf_base_domain"])
    zname = zone.get("name", "")
    if not (base == zname or base.endswith("." + zname)):
        raise CfError(f"Domain dasar '{base}' bukan bagian dari zone '{zname}'.")
    tun = _request("GET", f"/accounts/{c['cf_account_id']}/cfd_tunnel/{c['cf_tunnel_id']}", c["token"])
    return {"zone": zname, "tunnel": tun.get("name"), "status": tun.get("status"), "remote_config": tun.get("remote_config")}


def apply_ingress(c=None):
    """Atur tunnel (remotely-managed) agar *.domain dan domain itu sendiri menuju web Pantau."""
    c = c or cfg()
    base = norm_base(c["cf_base_domain"])
    ingress = [{"hostname": f"*.{base}", "service": c["cf_service"]},
               {"hostname": base, "service": c["cf_service"]},
               {"service": "http_status:404"}]
    _request("PUT", f"/accounts/{c['cf_account_id']}/cfd_tunnel/{c['cf_tunnel_id']}/configurations", c["token"],
             {"config": {"ingress": ingress}})
    return ingress


def _find_record(name, c):
    rows = _request("GET", f"/zones/{c['cf_zone_id']}/dns_records", c["token"], params={"name": name})
    return rows[0] if rows else None


def publish(sub, c=None):
    """Buat/ubah CNAME proxied untuk subdomain; return (hostname, record_id)."""
    c = c or cfg()
    sub = check_sub(sub)
    name = host_of(sub, c)
    body = {"type": "CNAME", "name": name, "content": f"{c['cf_tunnel_id']}.cfargotunnel.com", "proxied": True, "comment": "Pantau portal klien"}
    ex = _find_record(name, c)
    if ex:
        if ex.get("type") != "CNAME" or not str(ex.get("content", "")).endswith(".cfargotunnel.com"):
            raise CfError(f"Record DNS '{name}' sudah ada dan bukan milik tunnel. Pilih subdomain lain atau hapus record itu di Cloudflare.")
        rec = _request("PUT", f"/zones/{c['cf_zone_id']}/dns_records/{ex['id']}", c["token"], body)
    else:
        rec = _request("POST", f"/zones/{c['cf_zone_id']}/dns_records", c["token"], body)
    return name, rec["id"]


def unpublish(sub, record_id="", c=None):
    c = c or cfg()
    name = host_of(sub, c)
    rid = record_id
    if not rid:
        ex = _find_record(name, c)
        rid = ex["id"] if ex else ""
    if rid:
        try:
            _request("DELETE", f"/zones/{c['cf_zone_id']}/dns_records/{rid}", c["token"])
        except CfError as e:
            if "not found" not in str(e).lower() and "81044" not in str(e):
                raise
    return name
