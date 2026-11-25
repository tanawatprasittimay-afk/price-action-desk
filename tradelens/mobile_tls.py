"""Issue private LAN HTTPS credentials with macOS's bundled LibreSSL."""

import fcntl
import hashlib
import ipaddress
import os
import re
import secrets
import ssl
import subprocess
import tempfile
from pathlib import Path


_OPENSSL = "/usr/bin/openssl"
_PRIVATE_V4 = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_LOCAL_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.local")
_CA_DAYS = 3650
_LEAF_DAYS = 80
_RENEW_DAYS = 10


def _openssl(*args, check=True):
    return subprocess.run([_OPENSSL, *map(str, args)], check=check, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)


def _private_key(path, work):
    if path.exists():
        path.chmod(0o600)
        return
    key = work / "new.key"
    _openssl("genrsa", "-out", key, "2048")
    key.chmod(0o600)
    os.replace(key, path)


def _valid_for(path, days):
    return path.is_file() and _openssl("x509", "-in", path, "-checkend", days * 86400,
                                        "-noout", check=False).returncode == 0


def _ensure_ca(folder, work):
    cert, key = folder / "root.pem", folder / "root.key"
    if cert.exists() and not key.is_file():
        raise RuntimeError("ไม่พบกุญแจของใบรับรองหลักที่เคยติดตั้ง ให้กู้คืนไฟล์ root.key จากข้อมูลสำรอง")
    _private_key(key, work)
    if cert.exists():
        public_key = _openssl("pkey", "-in", key, "-pubout").stdout
        if _openssl("x509", "-in", cert, "-pubkey", "-noout").stdout != public_key:
            raise RuntimeError("กุญแจกับใบรับรองหลักไม่ตรงกัน ตรวจไฟล์ root.key และ root.pem")
    if not _valid_for(cert, _LEAF_DAYS + 1):
        config = work / "root.cnf"
        config.write_text(
            "[req]\ndistinguished_name=dn\n[dn]\n[ca]\nbasicConstraints=critical,CA:TRUE,pathlen:0\n"
            "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n", encoding="ascii")
        issued = work / "root.pem"
        _openssl("req", "-new", "-x509", "-sha256", "-days", _CA_DAYS, "-key", key,
                 "-subj", "/CN=Price Action Desk LAN Root", "-config", config,
                 "-extensions", "ca", "-out", issued)
        os.replace(issued, cert)
    public = folder / "root.cer"
    der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
    if not public.is_file() or public.read_bytes() != der:
        exported = work / "root.cer"
        exported.write_bytes(der)
        os.replace(exported, public)
    return cert, key, public


def ensure_certificates(config_dir, ip, hostname):
    """Return server PEM, private key and AirDrop-ready public root DER paths.

    Reuse the authority when a DHCP change requires a new leaf. Distinct file
    paths for distinct SANs also let two launchers use the results concurrently.
    """
    address = ipaddress.IPv4Address(ip)
    if not any(address in network for network in _PRIVATE_V4):
        raise ValueError("ต้องใช้ IPv4 ของเครือข่ายส่วนตัว")
    host = str(hostname).lower().rstrip(".")
    if not _LOCAL_NAME.fullmatch(host):
        raise ValueError("ชื่อเครื่องต้องเป็นชื่อ Bonjour ที่ลงท้ายด้วย .local")

    folder = Path(config_dir) / "mobile-tls"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    identity = hashlib.sha256(f"{host}/{address}".encode()).hexdigest()[:24]
    cert, key = folder / f"server-{identity}.pem", folder / "server.key"
    with (folder / ".issuance.lock").open("ab") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with tempfile.TemporaryDirectory(prefix=".issuing-", dir=folder) as partial:
            work = Path(partial)
            ca, ca_key, public = _ensure_ca(folder, work)
            _private_key(key, work)
            if _valid_for(cert, _RENEW_DAYS):
                trusted = _openssl("verify", "-purpose", "sslserver", "-CAfile", ca, cert,
                                   check=False).returncode == 0
                if trusted:
                    try:
                        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
                        return cert, key, public
                    except ssl.SSLError:
                        pass  # Reissue after restoring a mismatched server key.
            request, extensions, issued = work / "server.csr", work / "server.cnf", work / "server.pem"
            extensions.write_text(
                "[server]\nbasicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                f"subjectAltName=DNS:{host},IP:{address}\nsubjectKeyIdentifier=hash\n"
                "authorityKeyIdentifier=keyid\n", encoding="ascii")
            _openssl("req", "-new", "-sha256", "-key", key, "-subj", f"/CN={host}", "-out", request)
            _openssl("x509", "-req", "-sha256", "-in", request, "-CA", ca, "-CAkey", ca_key,
                     "-set_serial", "0x01" + secrets.token_hex(15), "-days", _LEAF_DAYS,
                     "-extfile", extensions, "-extensions", "server", "-out", issued)
            _openssl("verify", "-purpose", "sslserver", "-CAfile", ca, issued)
            os.replace(issued, cert)
            return cert, key, public
