"""One local installation, one owner password, expiring browser sessions."""

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path


def load_password():
    override = os.environ.get("PRICE_DESK_PASSWORD")
    if override is not None:
        if len(override) < 16:
            raise ValueError("PRICE_DESK_PASSWORD ต้องมีอย่างน้อย 16 ตัวอักษร")
        return override, None
    folder = Path.home() / ".config" / "price-action-desk"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / "password.txt"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        password = path.read_text(encoding="utf-8").strip()
        if len(password) < 16:
            raise ValueError("ไฟล์รหัสผ่านต้องมีอย่างน้อย 16 ตัวอักษร")
    else:
        password = secrets.token_urlsafe(24)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(password + "\n")
    return password, path


class Sessions:
    lifetime = 8 * 3600

    def __init__(self, password):
        self.password_digest = hashlib.sha256(password.encode()).digest()
        self.lock = threading.Lock()
        self.tokens = {}
        self.attempts = deque()

    def login(self, password):
        now = time.monotonic()
        with self.lock:
            while self.attempts and self.attempts[0] <= now - 60:
                self.attempts.popleft()
            if len(self.attempts) >= 6:
                return 429, None
            if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                       self.password_digest):
                self.attempts.append(now)
                return 401, None
            self.attempts.clear()
            self.tokens = {k: v for k, v in self.tokens.items() if v > now}
            token = secrets.token_urlsafe(32)
            self.tokens[token] = now + self.lifetime
            return 200, token

    def valid(self, token):
        with self.lock:
            return self.tokens.get(token, 0) > time.monotonic()

    def logout(self, token):
        with self.lock:
            self.tokens.pop(token, None)
