"""Share the desk inside a signed-in Tailscale network while this process runs."""

import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading

from app import DeskServer, tailnet_origin
from tradelens.auth import load_password


def find_cli():
    cli = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if not os.access(cli, os.X_OK):
        raise ValueError("ติดตั้งและเปิด Tailscale บน Mac ก่อน: https://tailscale.com/download/mac")
    return cli


def status_json(cli, args):
    try:
        result = subprocess.run(
            [cli, *args], capture_output=True, text=True, timeout=12,
            env={**os.environ, "TAILSCALE_BE_CLI": "1"},
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Tailscale ยังไม่พร้อม เปิด Tailscale บน Mac อนุญาตการเพิ่ม VPN ลงชื่อเข้าใช้ แล้วเปิดตัวเชื่อมอีกครั้ง") from error
    if "Failed to save preferences" in result.stdout + result.stderr:
        raise ValueError("เปิด Tailscale แล้วทำขั้นตอนอนุญาต VPN ในการตั้งค่า macOS ให้เสร็จก่อน")
    # Some signed-out client versions return a nonzero status *and* valid JSON.
    if result.returncode and not (args[0] == "status" and result.stdout.lstrip().startswith("{")):
        raise ValueError("อ่านสถานะ Tailscale ไม่สำเร็จ เปิดแอป Tailscale บน Mac แล้วลองอีกครั้ง")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise ValueError("Tailscale ส่งข้อมูลสถานะไม่ครบ ลองเปิดแอปและเชื่อมต่ออีกครั้ง") from exc
    if value is None and args[0] == "serve":
        return {}
    if not isinstance(value, dict):
        raise ValueError("อ่านสถานะเครือข่ายจาก Tailscale ไม่ได้")
    return value


def occupied_ports(config):
    """Tailscale can hold foreground web routes and Funnel alongside background TCP."""
    ports = {int(port) for port in config.get("TCP", {})}
    for field in ("Web", "AllowFunnel"):
        ports.update(int(authority.rsplit(":", 1)[-1]) for authority in config.get(field, {}))
    for field in ("Foreground", "Services"):
        for nested in config.get(field, {}).values():
            ports.update(occupied_ports(nested))
    return ports


def choose_port(config):
    used = occupied_ports(config)
    for port in range(18765, 18865):
        if port not in used:
            with socket.socket() as probe:
                try:
                    probe.bind(("", port))
                except OSError:
                    continue
                return port
    raise ValueError("พอร์ตสำหรับ Price Action Desk ถูกใช้งานครบแล้ว")


def serve(cli, origin, port):
    password, _ = load_password()
    # Reserving the actual backend socket here avoids adopting someone else's
    # localhost service. The OS chooses an available loopback port.
    with DeskServer(0, password, proxy_origin=origin) as backend:
        worker = threading.Thread(target=backend.serve_forever, daemon=True)
        worker.start()
        try:
            target = f"http://127.0.0.1:{backend.server_port}"
            # Foreground mode owns a temporary Tailscale session. A shutdown
            # removes this specific route without editing persistent Serve routes.
            print("เปิด Tailscale บน iPhone ด้วยบัญชีในเครือข่ายเดียวกัน", flush=True)
            print("เมื่อ Tailscale แจ้งว่าพร้อม เปิด Safari ที่", origin, flush=True)
            print("เปิดหน้าต่างนี้ไว้ขณะใช้งาน • กด Control-C เพื่อหยุด", flush=True)
            with subprocess.Popen([cli, "serve", f"--https={port}", target],
                                  env={**os.environ, "TAILSCALE_BE_CLI": "1"}) as process:
                try:
                    return process.wait()
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
        finally:
            backend.shutdown()
            worker.join()


def main():
    if os.name != "posix":
        raise ValueError("เปิดตัวเชื่อมนี้บน Mac")
    import fcntl
    cli = find_cli()
    status = status_json(cli, ["status", "--json", "--peers=false"])
    if status.get("BackendState") != "Running":
        raise ValueError("เปิด Tailscale และลงชื่อเข้าใช้บน Mac ก่อน จากนั้นเปิดตัวเชื่อม iPhone อีกครั้ง")
    hostname = (status.get("Self") or {}).get("DNSName", "").rstrip(".").lower()
    if not hostname:
        raise ValueError("ไม่พบชื่อ Mac ใน Tailscale ตรวจการเชื่อมต่อและ MagicDNS")
    # Validate before any local or network configuration changes.
    tailnet_origin(f"https://{hostname}")
    folder = Path.home() / ".config" / "price-action-desk"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / "phone-vpn.lock").open("a+", encoding="utf-8") as owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            owner.seek(0)
            print("ตัวเชื่อมเปิดอยู่แล้ว ดูสถานะในหน้าต่างเดิม", owner.read().strip())
            return 0
        config = status_json(cli, ["serve", "status", "--json"])
        port = choose_port(config)
        origin = tailnet_origin(f"https://{hostname}:{port}")
        owner.seek(0)
        owner.truncate()
        owner.write(origin + "\n")
        owner.flush()
        try:
            return serve(cli, origin, port)
        finally:
            owner.seek(0)
            owner.truncate()


if __name__ == "__main__":
    # Closing a Terminal or stopping the parent should release its child route.
    def stop_session(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGHUP, stop_session)
    signal.signal(signal.SIGTERM, stop_session)
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("ปิดตัวเชื่อม iPhone แล้ว")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
