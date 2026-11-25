"""Start a password-protected HTTPS desk on the Mac's private LAN interface."""

import http.client
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import sys

from app import PRIVATE_LANS
from tradelens.mobile_tls import ensure_certificates

PORT = 8766
CONFIG = Path.home() / ".config" / "price-action-desk"


def mac_address():
    route = subprocess.check_output(["/sbin/route", "-n", "get", "default"], text=True)
    adapter = next((s.split(":", 1)[1].strip() for s in route.splitlines()
                    if s.strip().startswith("interface:")), None)
    if not adapter:
        raise ValueError("ไม่พบเครือข่ายในบ้าน กรุณาเชื่อมต่อ Wi-Fi หรือสาย LAN")
    ip = subprocess.check_output(["/usr/sbin/ipconfig", "getifaddr", adapter], text=True).strip()
    if not any(ipaddress.IPv4Address(ip) in net for net in PRIVATE_LANS):
        raise ValueError("ต้องใช้ IP ภายในบ้านบน Wi-Fi หรือ Ethernet")
    name = subprocess.check_output(["/usr/sbin/scutil", "--get", "LocalHostName"], text=True).strip()
    return ip, name.lower() + ".local"


def already_running(ip, public_ca):
    # The CA is the DER file intended for the phone. Also verify host and app identity.
    tls = ssl.create_default_context(cadata=public_ca.read_bytes())
    conn = http.client.HTTPSConnection(ip, PORT, context=tls, timeout=2)
    try:
        conn.request("GET", "/api/session")
        result = conn.getresponse()
        if (result.status == 200 and result.getheader("Server", "").startswith("PriceActionDesk")
                and isinstance(json.loads(result.read(4096)).get("authenticated"), bool)):
            return True
        raise RuntimeError("พอร์ตนี้มีบริการอื่นใช้งานอยู่")
    except ConnectionRefusedError:
        return False
    finally:
        conn.close()


def shareable_certificate(public_ca):
    # The folder shown in Finder contains only the public AirDrop attachment.
    folder = CONFIG / "iphone-share"
    folder.mkdir(mode=0o700, exist_ok=True)
    public = folder / "Price Action Desk LAN Root.cer"
    data = public_ca.read_bytes()
    if not public.is_file() or public.read_bytes() != data:
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as out:
            out.write(data)
        os.replace(out.name, public)
    return public


def main():
    ip, hostname = mac_address()
    cert, key, public_ca = ensure_certificates(CONFIG, ip, hostname)
    attachment = shareable_certificate(public_ca)
    print(f"เปิด Safari บน iPhone ที่ใช้ Wi-Fi บ้านเดียวกับ Mac\nhttps://{hostname}:{PORT}\nhttps://{ip}:{PORT}", flush=True)
    print(f"ครั้งแรก ส่งเฉพาะใบรับรองสาธารณะนี้ด้วย AirDrop แล้วติดตั้งและเปิดความเชื่อถือบน iPhone\n{attachment}", flush=True)
    if already_running(ip, public_ca):
        print("Price Action Desk เปิดให้ iPhone ใช้งานอยู่แล้ว", flush=True)
        return
    args = [sys.executable, str(Path(__file__).resolve().with_name("app.py")), "--bind", ip,
            "--port", str(PORT), "--name", hostname, "--tls-cert", str(cert), "--tls-key", str(key)]
    os.execv(sys.executable, args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"เปิดโหมด iPhone ไม่สำเร็จ ({error})", file=sys.stderr)
        sys.exit(1)
