"""Launch the local Price Action Desk. Python 3.11+; no web dependencies."""

import argparse
import ipaddress
import json
import re
import socket
import ssl
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
          "/static/styles.css": ("styles.css", "text/css"),
          "/desk.webmanifest": ("desk.webmanifest", "application/manifest+json"),
          "/static/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
          "/static/desk-icon.png": ("desk-icon.png", "image/png")}


LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
PRIVATE_LANS = tuple(ipaddress.IPv4Network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def tailnet_origin(value):
    # Only the exact HTTPS Tailscale Serve hostname is intended as a browser entry point.
    match = re.fullmatch(rf"https://((?:{LABEL}\.)+ts\.net)(?::([1-9][0-9]{{0,4}}))?", value.lower())
    if match is None or len(match[1]) > 253:
        raise ValueError("--proxy-origin requires an exact https://hostname.ts.net[:port] origin")
    port = int(match[2] or 443)
    if port > 65535:
        raise ValueError("invalid proxy origin port")
    return "https://" + match[1] + (f":{port}" if port != 443 else "")


class DeskServer(ThreadingHTTPServer):
    def __init__(self, port, password, *, bind="127.0.0.1", name=None, tls_cert=None, tls_key=None,
                 proxy_origin=None):
        address = ipaddress.IPv4Address(bind)
        if not address.is_loopback and not any(address in net for net in PRIVATE_LANS):
            raise ValueError("--bind must be a loopback or private LAN IPv4 address")
        if bool(tls_cert) != bool(tls_key):
            raise ValueError("--tls-cert and --tls-key must be supplied together")
        if not address.is_loopback and not tls_cert:
            raise ValueError("a LAN listener requires a TLS certificate and key")
        if name and not re.fullmatch(rf"{LABEL}\.local", name.lower()):
            raise ValueError("--name must be the Mac's exact .local hostname")
        if proxy_origin and (not address.is_loopback or tls_cert or name):
            raise ValueError("--proxy-origin requires a dedicated plain HTTP loopback listener")

        self.proxy_origin = tailnet_origin(proxy_origin) if proxy_origin else None
        self.tls_context = None
        if tls_cert:
            self.tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.tls_context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.tls_context.load_cert_chain(tls_cert, tls_key)
        self.secure_cookie = bool(self.proxy_origin or self.tls_context)
        super().__init__((str(address), port), Handler)
        self.sessions = Sessions(password)
        names = {str(address)}
        if address.is_loopback:
            names.add("localhost")
        if name:
            names.add(name.lower())
        scheme = "https" if self.tls_context else "http"
        default_port = 443 if self.tls_context else 80
        suffix = "" if self.server_port == default_port else f":{self.server_port}"
        self.url = self.proxy_origin or f"{scheme}://{name or address}{suffix}"
        self.origins = {}
        for hostname in names:
            origin = self.proxy_origin or f"{scheme}://{hostname}{suffix}"
            self.origins[f"{hostname}:{self.server_port}"] = origin
            if self.server_port == default_port:
                self.origins[hostname] = origin
        if self.proxy_origin:
            public = urlsplit(self.proxy_origin)
            self.origins[public.netloc] = self.proxy_origin
            if public.port is None:
                self.origins[f"{public.hostname}:443"] = self.proxy_origin
        # No forwarded header defines a trusted host or origin. Tailscale Serve may
        # retain the public Host or rewrite it to the configured loopback backend.

    def get_request(self):
        conn, addr = super().get_request()
        if self.tls_context:
            # A slow TLS handshake must not block accept() for other devices.
            conn = self.tls_context.wrap_socket(conn, server_side=True, do_handshake_on_connect=False)
        return conn, addr


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
        charset = "; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""
        self.send_header("Content-Type", content_type + charset)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if cookie is not None:
            age = self.server.sessions.lifetime if cookie else 0
            secure = "; Secure" if self.server.secure_cookie else ""
            self.send_header("Set-Cookie", f"{COOKIE}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}{secure}")
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
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        expected = self.server.origins.get(hosts[0].lower()) if len(hosts) == 1 else None
        if expected is None or (write and origins != [expected] and (origins or self.server.proxy_origin)):
            self.reply(403, {"error": "กรุณาเปิดแอปจากที่อยู่ที่ตั้งค่าไว้"})
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
    parser.add_argument("--bind", default="127.0.0.1", help="Loopback or exact private LAN IPv4 address")
    parser.add_argument("--name", help="Mac .local hostname on the direct listener")
    parser.add_argument("--tls-cert", help="PEM certificate chain for the private HTTPS listener")
    parser.add_argument("--tls-key", help="PEM private key for the private HTTPS listener")
    parser.add_argument("--proxy-origin", help="Exact external HTTPS origin for a loopback-only Tailscale Serve backend")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in a browser")
    args = parser.parse_args()
    password, path = load_password()
    try:
        server = DeskServer(args.port, password, bind=args.bind, name=args.name,
                            tls_cert=args.tls_cert, tls_key=args.tls_key, proxy_origin=args.proxy_origin)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    with server as httpd:
        print(f"Price Action Desk  {httpd.url}", flush=True)
        print(f"Password file  {path}" if path else "Password from PRICE_DESK_PASSWORD", flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(httpd.url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
