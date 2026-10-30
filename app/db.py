"""SQLite persistence for Mana Arena.

All mutable tournament records are stored as JSON at the engine boundary.  The
relational keys still enforce ownership and historical player references.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


MUTATION_LOCK = threading.RLock()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value, fallback=None):
    return json.loads(value) if value else (fallback if fallback is not None else {})


class Database:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.db_dir = self.data_dir / "db"
        self.photos_dir = self.data_dir / "photos"
        self.path = self.db_dir / "mana-arena.sqlite3"

    def initialize(self) -> None:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.photos_dir.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL, revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    nonce TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, photo TEXT, active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tournaments (
                    id TEXT PRIMARY KEY, config_json TEXT NOT NULL, frozen_config_json TEXT,
                    status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    warnings_json TEXT NOT NULL DEFAULT '[]', champion_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS entrants (
                    tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    player_id TEXT NOT NULL REFERENCES players(id), seed INTEGER NOT NULL,
                    withdrawn INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(tournament_id, player_id)
                );
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, tournament_id TEXT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    round_no INTEGER NOT NULL, table_no INTEGER NOT NULL, data_json TEXT NOT NULL,
                    UNIQUE(tournament_id, round_no, table_no)
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id TEXT REFERENCES tournaments(id) ON DELETE CASCADE,
                    action TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.db_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15, isolation_level="DEFERRED", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def health(self) -> bool:
        with self.transaction() as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise sqlite3.DatabaseError("SQLite quick_check did not return ok")
        return True

    @staticmethod
    def admin_row(conn):
        row = conn.execute("SELECT * FROM admin WHERE id=1").fetchone()
        return dict(row) if row else None

    @staticmethod
    def insert_admin(conn, username, salt, password_hash):
        conn.execute("INSERT INTO admin(id,username,salt,password_hash) VALUES(1,?,?,?)", (username, salt, password_hash))

    @staticmethod
    def public_player(row):
        return {"id": row["id"], "name": row["name"], "photo_url": f"/photos/{row['photo']}" if row["photo"] else None}

    @staticmethod
    def player(row):
        return {**Database.public_player(row), "active": bool(row["active"])}

    @staticmethod
    def tournament(row):
        return {"id": row["id"], "config": _load(row["config_json"]), "frozen_config": _load(row["frozen_config_json"]) if row["frozen_config_json"] else None,
                "status": row["status"], "revision": row["revision"], "warnings": _load(row["warnings_json"], []), "champion_id": row["champion_id"]}

    @staticmethod
    def get_tournament(conn, tid):
        row = conn.execute("SELECT * FROM tournaments WHERE id=?", (tid,)).fetchone()
        return Database.tournament(row) if row else None

    @staticmethod
    def get_entrants(conn, tid):
        rows = conn.execute("SELECT e.*,p.name,p.photo,p.active FROM entrants e JOIN players p ON p.id=e.player_id WHERE e.tournament_id=? ORDER BY e.seed,e.player_id", (tid,)).fetchall()
        return [{"id": r["player_id"], "seed": r["seed"], "withdrawn": bool(r["withdrawn"]), "name": r["name"], "photo_url": f"/photos/{r['photo']}" if r["photo"] else None} for r in rows]

    @staticmethod
    def get_matches(conn, tid):
        rows = conn.execute("SELECT id,data_json FROM matches WHERE tournament_id=? ORDER BY round_no,table_no", (tid,)).fetchall()
        return [{"id": r["id"], **_load(r["data_json"])} for r in rows]

    @staticmethod
    def save_matches(conn, tid, matches):
        conn.execute("DELETE FROM matches WHERE tournament_id=?", (tid,))
        import uuid
        for match in matches:
            row = dict(match)
            mid = row.pop("id", None) or str(uuid.uuid4())
            conn.execute("INSERT INTO matches(id,tournament_id,round_no,table_no,data_json) VALUES(?,?,?,?,?)",
                         (mid, tid, int(row["round"]), int(row.get("table", 0)), _dump(row)))

    @staticmethod
    def audit(conn, tid, action, detail=None):
        conn.execute("INSERT INTO audit(tournament_id,action,detail_json) VALUES(?,?,?)", (tid, action, _dump(detail or {})))

