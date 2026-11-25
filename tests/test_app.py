import http.client
import json
import threading
import unittest
from unittest.mock import patch

from app import DeskServer
from tradelens.desk import build_desk
from tradelens.market import load_market


class DeskHTTPTest(unittest.TestCase):
    def setUp(self):
        self.server = DeskServer(0, "local-test-password-example")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, data=None, **headers):
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
