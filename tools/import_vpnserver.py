#!/usr/bin/env python3
"""Impor daftar router dari aplikasi PHP lama (vpnserver/data/mikrotiks.json) sebagai Server VPN.

Pakai:  python tools/import_vpnserver.py /path/ke/vpnserver/data
Butuh file mikrotiks.json dan .enckey di folder tersebut. Aman dijalankan ulang (duplikat dilewati).
"""
import sys, os, json, base64, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from app import db
from app.security import enc


def php_decrypt(value, key):
    if not value.startswith("enc:"):
        return value                       # data lama yang masih plaintext
    raw = base64.b64decode(value[4:])
    iv, ct = raw[:16], raw[16:]
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    pt = d.update(ct) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return (u.update(pt) + u.finalize()).decode()


def main(folder):
    key = bytes.fromhex(open(os.path.join(folder, ".enckey")).read().strip())
    items = json.load(open(os.path.join(folder, "mikrotiks.json")))
    db.init()
    n = 0
    for it in items:
        name = it.get("label") or it["host"]
        if db.q1("SELECT id FROM vpn_servers WHERE host=? AND port=? AND username=?", (it["host"], int(it.get("port") or 8728), it["user"])):
            print(f"lewati (sudah ada): {name}")
            continue
        sub = it.get("vpn_subnet") or ""
        db.ex("""INSERT INTO vpn_servers(name,host,port,username,password_enc,use_ssl,public_ip,vpn_subnet,profile,created_at)
                 VALUES(?,?,?,?,?,0,?,?,?,?)""",
              (name, it["host"], int(it.get("port") or 8728), it["user"], enc(php_decrypt(it.get("pass", ""), key)),
               it.get("public_ip") or it["host"], sub if sub.endswith(".") or not sub else sub + ".", "default", int(time.time())))
        n += 1
        print(f"diimpor: {name}")
    print(f"selesai, {n} server VPN ditambahkan.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
