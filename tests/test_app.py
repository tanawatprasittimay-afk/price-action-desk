import http.client
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import DeskServer
from tradelens.desk import build_desk
from tradelens.market import load_market


class DeskHTTPTest(unittest.TestCase):
    def setUp(self):
        self.server = self.start_server()
        self.client_context = None

    def start_server(self, **options):
        server = DeskServer(0, "local-test-password-example", **options)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def request(self, method, path, data=None, **headers):
        if self.client_context:
            client = http.client.HTTPSConnection("127.0.0.1", self.server.server_port, context=self.client_context)
        else:
            client = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            client.request(method, path, json.dumps(data) if data is not None else None,
                           headers={"Content-Type": "application/json", **headers})
            response = client.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            client.close()

    def test_browser_session_protects_prices_risk_and_revokes(self):
        self.assertEqual(self.request("GET", "/api/desk")[0], 401)
        self.assertEqual(self.request("POST", "/api/size", {})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password": "bad"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password": "local-test-password-example"},
                                      Origin="https://another.example")[0], 403)
        _, headers, _ = self.request("POST", "/api/login", {"password": "local-test-password-example"})
        cookie = {"Cookie": headers["Set-Cookie"].split(";")[0]}
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        status, _, data = self.request("GET", "/api/desk?mode=demo", **cookie)
        self.assertEqual(status, 200)
        self.assertEqual(data["market"]["symbol"], "XAUUSD")
        status, _, size = self.request("POST", "/api/size", dict(equity=10000, risk_pct=1,
            entry=2050, stop=2045, reward_risk=2, leverage=100), **cookie)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(size["net_rr"], 2)
        self.assertLessEqual(size["estimated_loss"], 100)
        self.assertEqual(self.request("POST", "/api/logout", {}, **cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/desk", **cookie)[0], 401)

    def test_only_local_http_or_explicit_private_https(self):
        for options in ({"bind":"0.0.0.0"}, {"bind":"192.168.1.20"},
                        {"bind":"8.8.8.8"}, {"name":"invalid.example"},
                        {"proxy_origin":"http://test.example.ts.net"}, {"proxy_origin":"https://example.test"},
                        {"proxy_origin":"https://desk.internal.ts.net/path"}, {"tls_cert":"missing-key"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                DeskServer(0, "test-password", **options)

    def test_tailnet_proxy_origin_with_either_host_style(self):
        origin = "https://desk.private.ts.net:8443"
        self.server = self.start_server(proxy_origin=origin)
        for host in (f"127.0.0.1:{self.server.server_port}", "desk.private.ts.net:8443"):
            with self.subTest(host=host):
                status, headers, _ = self.request("POST", "/api/login", {"password":"local-test-password-example"},
                                                  Host=host, Origin=origin)
                self.assertEqual(status, 200)
                self.assertIn("; Secure", headers["Set-Cookie"])
                cookie = headers["Set-Cookie"].split(";")[0]
                self.assertTrue(self.request("GET", "/api/session", Host=host, Cookie=cookie)[2]["authenticated"])
                status, _, _ = self.request("POST", "/api/logout", {}, Host=host, Origin=origin, Cookie=cookie)
                self.assertEqual(status, 200)
        self.assertEqual(self.request("POST", "/api/login", {}, Origin="http://localhost")[0], 403)
        self.assertEqual(self.request("GET", "/api/session", Host="other.private.ts.net")[0], 403)

    @unittest.skipUnless(shutil.which("openssl"), "requires openssl to create a throwaway TLS fixture")
    def test_https_login_with_ip_or_local_hostname(self):
        with tempfile.TemporaryDirectory() as tmp:
            cert, key, config = (Path(tmp) / name for name in ("cert.pem","key.pem","request.cnf"))
            config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\n[dn]\n"
                              "[ext]\nsubjectAltName=IP:127.0.0.1,DNS:desk.local\n", encoding="ascii")
            subprocess.run(["openssl","req","-x509","-newkey","rsa:2048","-nodes","-days","1",
                            "-subj","/CN=desk.local","-config",str(config),"-keyout",str(key),"-out",str(cert)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.server = self.start_server(name="desk.local", tls_cert=cert, tls_key=key)
            self.client_context = ssl.create_default_context(cafile=str(cert))
        for name in ("127.0.0.1","desk.local"):
            host = f"{name}:{self.server.server_port}"
            status, headers, _ = self.request("POST", "/api/login", {"password":"local-test-password-example"},
                                              Host=host, Origin=f"https://{host}")
            self.assertEqual(status, 200)
            self.assertIn("; Secure", headers["Set-Cookie"])
            status, _, data = self.request("GET", "/api/desk?mode=demo", Host=host,
                                           Cookie=headers["Set-Cookie"].split(";")[0])
            self.assertEqual(status, 200)
            self.assertEqual(data["market"]["mode"], "demo")

    def test_repeated_wrong_password_limited(self):
        for _ in range(6):
            self.assertEqual(self.request("POST", "/api/login", {"password":"bad"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password":"bad"})[0], 429)

    def test_stale_and_session_closed_disable_plans(self):
        fixture = load_market("XAUUSD", "1h", "demo")
        for stale, closed in [(True,False), (False,True)]:
            market = dict(fixture, mode="live", stale=stale, session_closed=closed, warning="old")
            with patch("tradelens.desk.analyze", return_value={"signal":"BUY","plan":{"entry":123},"reasons":[]}):
                result = build_desk(market)["analysis"]
                self.assertEqual(result["signal"], "WAIT")
                self.assertIsNone(result["plan"])
