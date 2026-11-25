import concurrent.futures
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import unittest

from unittest.mock import patch

if sys.platform == "darwin":
    from tradelens.mobile_tls import ensure_certificates


@unittest.skipUnless(sys.platform == "darwin", "uses macOS bundled openssl and file locks")
class MobileCertificateTests(unittest.TestCase):
    def test_private_ca_trusts_server_for_both_safari_addresses(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent)
            address = "192.168.9.11"
            hostname = "desk-test.local"
            cert, key, public_root = ensure_certificates(folder, address, hostname)
            self.assertEqual(0o600, key.stat().st_mode & 0o777)
            self.assertEqual(0o600, (key.parent / "root.key").stat().st_mode & 0o777)
            self.assertEqual(0o700, key.parent.stat().st_mode & 0o777)
            self.assertEqual(public_root.suffix, ".cer")
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(cert, key)
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(cadata=public_root.read_bytes())

            def server_exchange(sock):
                with server_ctx.wrap_socket(sock, server_side=True) as secure:
                    secure.sendall(b"ok")

            for name in (hostname, address):
                with self.subTest(name=name):
                    upstream, downstream = socket.socketpair()
                    upstream.settimeout(5)
                    downstream.settimeout(5)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        send = pool.submit(server_exchange, upstream)
                        with client_ctx.wrap_socket(downstream, server_hostname=name) as client:
                            self.assertEqual(b"ok", client.recv(2))
                        send.result(timeout=6)

    def test_reuses_ca_across_renewal_concurrent_starts_and_dhcp(self):
        with tempfile.TemporaryDirectory() as parent:
            folder = Path(parent)
            def create():
                cert, _, public = ensure_certificates(folder, "10.0.0.55", "Mini-Mac.local")
                return cert, cert.read_bytes(), public.read_bytes()
            begin = threading.Barrier(3)
            def simultaneous():
                begin.wait(timeout=4)
                return create()
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                calls = [pool.submit(simultaneous) for _ in range(3)]
                results = [call.result(timeout=15) for call in calls]
            cert, first, root = results[0]
            self.assertEqual(results, [results[0]] * 3)
            new, _, same_root = ensure_certificates(folder, "10.0.1.8", "mini-mac.local")
            self.assertNotEqual(cert, new)
            self.assertEqual(root, same_root.read_bytes())
            self.assertEqual(first, cert.read_bytes())
            with patch("tradelens.mobile_tls._RENEW_DAYS", 81):
                renewed, contents, root_afterwards = create()
            self.assertEqual(cert, renewed)
            self.assertNotEqual(first, contents)
            self.assertEqual(root, root_afterwards)

    def test_requires_local_name_and_private_address(self):
        with tempfile.TemporaryDirectory() as parent:
            for ip, host in (("192.168.1.10", "example.com"), ("203.0.113.15", "mini.local")):
                with self.assertRaises(ValueError):
                    ensure_certificates(parent, ip, host)
            self.assertEqual(list(Path(parent).iterdir()), [])
