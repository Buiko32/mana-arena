"""Admin bootstrap, password hashing, signed sessions, and login throttling."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict

ITERATIONS = 600_000
PLACEHOLDERS = {
    "password", "password123456", "changeme", "changeme1234", "change-me",
    "change-me-please", "admin", "admin123456", "admin-password",
    "your-password", "your-password-here", "your_admin_password",
    "replace-me", "replace-with-a-long-random-secret", "secret",
    "123456789012", "qwerty123456",
}


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or secrets.token_bytes(16)
    return salt, hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)


def valid_bootstrap_password(value: str | None) -> bool:
    return bool(value and len(value) >= 12 and value.strip().lower() not in PLACEHOLDERS and value.lower() not in {"password1234"})


class AuthManager:
    def __init__(self, secret: str, username: str, secure: bool = False, limit: int = 5):
        if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
            raise ValueError("SESSION_SECRET deve contenere almeno 32 byte.")
        self.secret = secret.encode("utf-8")
        self.username = username
        self.secure = secure
        self.limit = max(1, limit)
        self.failures: OrderedDict[str, tuple[int, float]] = OrderedDict()
        self.revoked: set[str] = set()
        self.epoch = 0
        self.lock = threading.Lock()

    def create_session(self, username: str) -> tuple[str, str, str, int]:
        csrf = secrets.token_urlsafe(24)
        payload = {"sub": username, "exp": int(time.time()) + 8 * 60 * 60, "csrf": csrf, "epoch": self.epoch, "nonce": secrets.token_urlsafe(18)}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
        sig = hmac.new(self.secret, body, hashlib.sha256).digest()
        token = body.decode() + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        return token, csrf, payload["nonce"], payload["exp"]

    def verify_session(self, token: str | None) -> dict | None:
        if not token or token in self.revoked:
            return None
        try:
            body_text, sig_text = token.split(".", 1)
            body, sig = body_text.encode(), base64.urlsafe_b64decode(sig_text + "=" * (-len(sig_text) % 4))
            expected = hmac.new(self.secret, body, hashlib.sha256).digest()
            if not hmac.compare_digest(sig, expected):
                return None
            payload = json.loads(base64.urlsafe_b64decode(body_text + "=" * (-len(body_text) % 4)))
            if payload.get("sub") != self.username or payload.get("epoch") != self.epoch or payload.get("exp", 0) < time.time():
                return None
            return payload
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def revoke(self, token: str | None) -> None:
        if token:
            self.revoked.add(token)
            if len(self.revoked) > 4096:
                self.revoked.clear()
                self.epoch += 1

    def revoke_all(self) -> None:
        self.epoch += 1
        self.revoked.clear()

    def allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            count, blocked_until = self.failures.get(key, (0, 0.0))
            return now >= blocked_until

    def failed(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            count, _ = self.failures.get(key, (0, 0.0))
            count += 1
            delay = min(300, 2 ** max(0, count - self.limit)) if count >= self.limit else 0
            self.failures[key] = (count, now + delay if delay else 0.0)
            self.failures.move_to_end(key)
            while len(self.failures) > 2048:
                self.failures.popitem(last=False)

    def succeeded(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)

