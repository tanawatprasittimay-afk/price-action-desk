"""Launch the local Price Action Desk. Python 3.11+; no web dependencies."""

import argparse
import json
import socket
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tradelens.auth import Sessions, load_password
from tradelens.desk import build_desk, size_plan
from tradelens.importer import read_export
from tradelens.market import MarketUnavailable, load_market


STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_700_000
COOKIE = "price_desk_session"
ASSETS = {"/": ("index.html", "text/html"), "/static/app.js": ("app.js", "text/javascript"),
          "/static/chart.js": ("chart.js", "text/javascript"),
          "/static/styles.css": ("styles.css", "text/css")}


class DeskServer(ThreadingHTTPServer):
    def __init__(self, port, password):
        super().__init__(("127.0.0.1", port), Handler)
        self.sessions = Sessions(password)
        self.hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "PriceActionDesk"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_request(self, code="-", size="-"):
        # Do not log user-supplied query strings, cookies or uploaded exports.
        pass

    def reply(self, code, body, content_type="application/json", cookie=None):
        data = (json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
                if content_type == "application/json" else body)
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if cookie is not None:
            age = self.server.sessions.lifetime if cookie else 0
            self.send_header("Set-Cookie", f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}")
        self.end_headers()
        self.wfile.write(data)

    def token(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        return jar[COOKIE].value if COOKIE in jar else ""

    def local_request(self, write=False):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        if host not in self.server.hosts or (write and origin and origin != "http://" + host):
            self.reply(403, {"error": "อนุญาตเฉพาะคำขอจากแอปในเครื่อง"})
            return False
        return True

    def authenticated(self):
        if self.server.sessions.valid(self.token()):
            return True
        self.reply(401, {"error": "กรุณาเข้าสู่ระบบ"})
        return False

    def do_GET(self):
        if not self.local_request():
            return
        url = urlsplit(self.path)
        if url.path in ASSETS:
            filename, mime = ASSETS[url.path]
            self.reply(200, (STATIC / filename).read_bytes(), mime)
        elif url.path == "/api/session":
            self.reply(200, {"authenticated": self.server.sessions.valid(self.token())})
        elif url.path == "/api/desk":
            if not self.authenticated():
                return
            query = parse_qs(url.query)
            try:
                market = load_market(*(query.get(k, [default])[0]
                                       for k, default in [("symbol", "XAUUSD"), ("interval", "1h"), ("mode", "demo")]))
                self.reply(200, build_desk(market))
            except ValueError as error:
                self.reply(400, {"error": str(error)})
            except MarketUnavailable as error:
                self.reply(503, {"error": str(error)})
        elif url.path == "/favicon.ico":
            self.reply(204, b"", "image/x-icon")
        else:
            self.reply(404, {"error": "ไม่พบหน้านี้"})

    def do_POST(self):
        if not self.local_request(write=True):
            return
        path = urlsplit(self.path).path
        if path not in ("/api/login", "/api/logout", "/api/size", "/api/import"):
            self.reply(404, {"error": "ไม่พบคำสั่งนี้"})
            return
        if path != "/api/login" and not self.authenticated():
            return
        if self.headers.get_content_type() != "application/json":
            self.reply(415, {"error": "ต้องส่งข้อมูล JSON"})
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > (MAX_BODY if path == "/api/import" else 8192):
            self.reply(413, {"error": "คำขอมีขนาดใหญ่เกินกำหนด"})
            return
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("ข้อมูลต้องเป็น JSON object")
            if path == "/api/login":
                password = data.get("password")
                if not isinstance(password, str):
                    raise ValueError("กรุณาใส่รหัสผ่าน")
                code, token = self.server.sessions.login(password)
                if code == 200:
                    self.reply(200, {"authenticated": True}, cookie=token)
                else:
                    msg = "ลองใหม่ใน 1 นาที" if code == 429 else "รหัสผ่านไม่ถูกต้อง"
                    self.reply(code, {"error": msg})
            elif path == "/api/logout":
                self.server.sessions.logout(self.token())
                self.reply(200, {"authenticated": False}, cookie="")
            elif path == "/api/size":
                self.reply(200, size_plan(data))
            else:
                if not all(isinstance(data.get(k), str) for k in ("symbol", "interval", "timezone", "csv")):
                    raise ValueError("ข้อมูลสำหรับนำเข้าไฟล์ไม่ครบ")
                self.reply(200, build_desk(read_export(data["symbol"], data["interval"], data["timezone"], data["csv"])))
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            self.reply(400, {"error": str(error) or "รูปแบบข้อมูลไม่ถูกต้อง"})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open the local dashboard in a browser")
    args = parser.parse_args()
    password, path = load_password()
    with DeskServer(args.port, password) as httpd:
        print(f"Price Action Desk  http://127.0.0.1:{httpd.server_port}", flush=True)
        print(f"Password file  {path}" if path else "Password from PRICE_DESK_PASSWORD", flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{httpd.server_port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
