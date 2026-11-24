"""Persistent browser credentials, kept outside tournament backups."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager

COOKIE = "mana_device"
LIFETIME = 90 * 86400


class DeviceAccess:
    def __init__(self, directory, password, secret):
        self.password_digest = hashlib.sha256(password.encode()).digest()
        self.path = directory / "access.sqlite3"
        generation = hmac.digest(secret.encode(), b"mana-device-v1\0" + self.password_digest, "sha256")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY, generation BLOB NOT NULL)")
            conn.execute("""CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, digest BLOB UNIQUE NOT NULL, label TEXT NOT NULL,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)""")
            previous = conn.execute("SELECT generation FROM configuration WHERE id=1").fetchone()
            if not previous or previous[0] != generation:
                conn.execute("DELETE FROM devices")
                conn.execute("INSERT OR REPLACE INTO configuration VALUES (1, ?)", (generation,))

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            conn.row_factory = sqlite3.Row
            yield conn

    def accepts(self, password):
        return isinstance(password, str) and hmac.compare_digest(
            hashlib.sha256(password.encode("utf-8", errors="surrogatepass")).digest(), self.password_digest
        )

    def register(self, label):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connection() as conn:
            conn.execute("DELETE FROM devices WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO devices VALUES (?, ?, ?, ?, ?)", (
                secrets.token_hex(16), hashlib.sha256(token.encode()).digest(),
                label[:200] or "Browser sconosciuto", now, now + LIFETIME,
            ))
        return token

    def authorized(self, token):
        if not token or len(token) != 43 or not token.isascii():
            return False
        with self.connection() as conn:
            return conn.execute("SELECT 1 FROM devices WHERE digest=? AND expires_at>?", (
                hashlib.sha256(token.encode()).digest(), int(time.time()),
            )).fetchone() is not None

    def devices(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT id,label,created_at,expires_at FROM devices WHERE expires_at>? ORDER BY created_at DESC",
                (int(time.time()),),
            )]

    def revoke(self, device_id):
        with self.connection() as conn:
            return conn.execute("DELETE FROM devices WHERE id=?", (device_id,)).rowcount
