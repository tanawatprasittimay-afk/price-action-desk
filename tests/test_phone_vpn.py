import io
import os
import socket
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import phone_vpn


@unittest.skipUnless(os.name == "posix", "macOS Finder/Tailscale helper")
class PhoneVPNTest(unittest.TestCase):
    def test_ports_reserved_in_background_foreground_and_funnel(self):
        config = {
            "TCP": {"18765": {"HTTPS": True}},
            "Web": {"desk.a.ts.net:18766": {}},
            "Foreground": {"session": {"TCP": {"18767": {"HTTP": True}}}},
            "AllowFunnel": {"desk.a.ts.net:18768": True},
            "Services": {"svc:chart": {"TCP": {"18769": {}}}},
        }
        self.assertEqual(phone_vpn.occupied_ports(config), set(range(18765, 18770)))
        first = phone_vpn.choose_port(config)
        self.assertGreaterEqual(first, 18770)
        with socket.socket() as existing:
            existing.bind(("127.0.0.1", first))
            self.assertGreater(phone_vpn.choose_port(config), first)

    def test_signed_out_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as tmp, patch("phone_vpn.find_cli", return_value="tailscale"), \
             patch("phone_vpn.status_json", return_value={"BackendState": "NeedsLogin"}) as status, \
             patch("phone_vpn.Path.home", return_value=Path(tmp)), patch("phone_vpn.serve") as serve:
            with self.assertRaisesRegex(ValueError, "ลงชื่อเข้าใช้"):
                phone_vpn.main()
            self.assertEqual(list(Path(tmp).iterdir()), [])
            serve.assert_not_called()
            status.assert_called_once()

    def test_private_serve_reaches_authenticated_loopback_app(self):
        # A disposable CLI stand-in inspects the same local endpoint Tailscale
        # would proxy. Live-device VPN connectivity requires a real signed-in phone.
        origin = "https://mac.example.ts.net:18765"
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "fake-tailscale"
            launcher.write_text(
                "#!/usr/bin/env python3\n"
                "import sys,json,urllib.request,os\n"
                "assert sys.argv[1:3] == ['serve','--https=18765']\n"
                "assert os.environ['TAILSCALE_BE_CLI']=='1'\n"
                "target=sys.argv[3]\n"
                "assert target.startswith('http://127.0.0.1:')\n"
                "headers={'Content-Type':'application/json','Origin':'" + origin + "','Host':'mac.example.ts.net:18765'}\n"
                "data=json.dumps({'password':'test-password-local-only'}).encode()\n"
                "req=urllib.request.Request(target+'/api/login',data=data,headers=headers)\n"
                "with urllib.request.urlopen(req,timeout=3) as r:\n"
                " assert json.load(r)['authenticated'] and 'Secure' in r.headers['Set-Cookie']\n"
            )
            launcher.chmod(0o700)
            with patch("phone_vpn.load_password", return_value=("test-password-local-only", None)), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(phone_vpn.serve(str(launcher), origin, 18765), 0)

    def test_rerun_does_not_register_second_route(self):
        with tempfile.TemporaryDirectory() as tmp, patch("phone_vpn.find_cli", return_value="tailscale"), \
             patch("phone_vpn.Path.home", return_value=Path(tmp)), redirect_stdout(io.StringIO()):
            state = {"BackendState": "Running", "Self": {"DNSName": "mac.example.ts.net."}}
            with patch("phone_vpn.status_json", side_effect=[state, {}, state]), \
                 patch("phone_vpn.serve", side_effect=lambda *args: phone_vpn.main()) as serve:
                self.assertEqual(phone_vpn.main(), 0)
                serve.assert_called_once()
