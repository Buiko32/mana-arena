"""Bounded, validated database-plus-photo backup archives."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 100 * 1024 * 1024
MAX_UNPACKED = 200 * 1024 * 1024
MAX_FILES = 5000
DB_MEMBER = "db/mana-arena.sqlite3"
SCHEMA_VERSION = 2
SCHEMA_COLUMNS = {
    "admin": {"id", "username", "salt", "password_hash", "revision"},
    "sessions": {"nonce", "username", "expires_at"},
    "players": {"id", "name", "photo", "active", "created_at", "updated_at"},
    "tournaments": {"id", "config_json", "frozen_config_json", "status", "revision", "warnings_json", "champion_id", "archived_from", "created_at", "updated_at"},
    "entrants": {"tournament_id", "player_id", "seed", "withdrawn"},
    "matches": {"id", "tournament_id", "round_no", "table_no", "data_json"},
    "withdrawals": {"tournament_id", "player_id", "effective_round", "created_at"},
    "audit": {"id", "tournament_id", "action", "detail_json", "created_at"},
}


def create_backup(database) -> bytes:
    database.db_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mana-backup-", dir=database.data_dir) as temporary:
        snap = Path(temporary) / "mana-arena.sqlite3"
        source = sqlite3.connect(database.path, timeout=15)
        target = sqlite3.connect(snap)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        payloads = {DB_MEMBER: snap.read_bytes()}
        snapshot = sqlite3.connect(snap)
        try:
            referenced = {row[0] for row in snapshot.execute("SELECT photo FROM players WHERE photo IS NOT NULL")}
        finally:
            snapshot.close()
        for filename in referenced:
            if not __import__("re").fullmatch(r"[0-9a-f]{32}\.jpg", filename):
                raise ValueError("Il database contiene un riferimento foto non valido.")
            photo = database.photos_dir / filename
            if not photo.is_file():
                raise ValueError("Manca una foto referenziata dal database.")
            payloads[f"photos/{filename}"] = photo.read_bytes()
        manifest = {"version": 1, "database": DB_MEMBER,
                    "files": [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                              for name, data in sorted(payloads.items())]}
        output = __import__("io").BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
            for name, data in payloads.items():
                archive.writestr(name, data)
        return output.getvalue()


def _safe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    path = PurePosixPath(name)
    unix_mode = info.external_attr >> 16
    allowed = name == "manifest.json" or name == DB_MEMBER or bool(__import__("re").fullmatch(r"photos/[0-9a-f]{32}\.jpg", name))
    return (allowed and not path.is_absolute() and "\\" not in name
            and all(part not in ("", ".", "..") for part in name.split("/"))
            and not (unix_mode and stat.S_ISLNK(unix_mode)) and not name.endswith("/"))


def _read_verified(blob: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(blob) > MAX_ARCHIVE:
        raise ValueError("Archivio troppo grande (massimo 100 MiB).")
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise ValueError("Archivio ZIP non valido.") from None
    with archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(infos) > MAX_FILES or len(names) != len(set(names)):
            raise ValueError("L'archivio contiene file duplicati o troppi elementi.")
        if any(not _safe_member(item) for item in infos):
            raise ValueError("L'archivio contiene percorsi non sicuri.")
        total = 0
        for item in infos:
            total += item.file_size
            if item.file_size > MAX_UNPACKED or (item.compress_size == 0 and item.file_size > 0) or (item.compress_size and item.file_size / item.compress_size > 200):
                raise ValueError("L'archivio contiene un elemento compresso in modo non sicuro.")
        if total > MAX_UNPACKED or "manifest.json" not in names:
            raise ValueError("Dimensione o manifest dell'archivio non valido.")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (ValueError, KeyError, UnicodeDecodeError):
            raise ValueError("Manifest dell'archivio non valido.") from None
        if manifest.get("version") != 1 or manifest.get("database") != DB_MEMBER or not isinstance(manifest.get("files"), list):
            raise ValueError("Versione del backup non supportata.")
        expected_paths = {item.get("path") for item in manifest["files"] if isinstance(item, dict)}
        if expected_paths != set(names) - {"manifest.json"} or DB_MEMBER not in expected_paths:
            raise ValueError("I file del backup non corrispondono al manifest.")
        contents = {}
        for item in manifest["files"]:
            name = item["path"]
            data = archive.read(name)
            if len(data) != item.get("size") or hashlib.sha256(data).hexdigest() != item.get("sha256"):
                raise ValueError("Il backup è danneggiato: verifica file non riuscita.")
            if name != DB_MEMBER and (not name.startswith("photos/") or len(PurePosixPath(name).parts) != 2):
                raise ValueError("Il backup contiene un percorso non previsto.")
            contents[name] = data
        return manifest, contents


def _validate_database(path: Path) -> None:
    check = sqlite3.connect(path)
    try:
        if check.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Versione schema del backup non supportata.")
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if integrity != "ok" or not set(SCHEMA_COLUMNS).issubset(tables):
            raise ValueError("Il database del backup non supera la verifica schema/integrità.")
        for table, expected in SCHEMA_COLUMNS.items():
            columns = {row[1] for row in check.execute(f"PRAGMA table_info({table})")}
            if columns != expected:
                raise ValueError(f"Schema del backup non compatibile: tabella {table}.")
        if check.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Il database del backup contiene riferimenti non validi.")
        admin_rows = check.execute("SELECT COUNT(*) FROM admin").fetchone()[0]
        admin = check.execute("SELECT username,salt,password_hash FROM admin WHERE id=1").fetchone()
        if admin_rows != 1 or admin is None or not admin[0] or len(admin[1] or b"") < 16 or len(admin[2] or b"") != 32:
            raise ValueError("Il backup non contiene un amministratore valido.")
    finally:
        check.close()


def restore_backup(database, blob: bytes, after_install=None) -> list[str]:
    _, files = _read_verified(blob)
    stage = Path(tempfile.mkdtemp(prefix=".restore-", dir=database.data_dir))
    stage_data = stage / "data"
    stage_db_dir = stage_data / "db"
    staged_db = stage_db_dir / "mana-arena.sqlite3"
    staged_photos = stage_data / "photos"
    stage_db_dir.mkdir(parents=True)
    staged_photos.mkdir()
    old_db = stage / "previous.sqlite3"
    old_photos = stage / "previous-photos"
    db_target = database.path
    photos_target = database.photos_dir
    moved_old_db = moved_old_photos = installed_db = installed_photos = False
    try:
        staged_db.write_bytes(files[DB_MEMBER])
        photo_names = []
        for name, data in files.items():
            if name == DB_MEMBER:
                continue
            filename = PurePosixPath(name).name
            if not __import__("re").fullmatch(r"[0-9a-f]{32}\.jpg", filename):
                raise ValueError("Nome foto nel backup non valido.")
            (staged_photos / filename).write_bytes(data)
            photo_names.append(filename)
        _validate_database(staged_db)
        staged_database = type(database)(stage_data)
        staged_database.initialize()
        with staged_database.transaction() as conn:
            conn.execute("DELETE FROM sessions")
        _validate_database(staged_db)
        check = sqlite3.connect(staged_db)
        try:
            referenced = {row[0] for row in check.execute("SELECT photo FROM players WHERE photo IS NOT NULL")}
        finally:
            check.close()
        archived = {PurePosixPath(name).name for name in files if name.startswith("photos/")}
        if any(not __import__("re").fullmatch(r"[0-9a-f]{32}\.jpg", filename) for filename in referenced) or referenced != archived:
            raise ValueError("Le foto del backup non corrispondono ai profili.")
        database.db_dir.mkdir(parents=True, exist_ok=True)
        if db_target.exists():
            os.replace(db_target, old_db)
            moved_old_db = True
        os.replace(staged_db, db_target)
        installed_db = True
        if photos_target.exists():
            os.replace(photos_target, old_photos)
            moved_old_photos = True
        os.replace(staged_photos, photos_target)
        installed_photos = True
        if after_install is not None:
            after_install()
        shutil.rmtree(old_photos, ignore_errors=True)
        old_db.unlink(missing_ok=True)
        return photo_names
    except Exception:
        if installed_photos:
            shutil.rmtree(photos_target, ignore_errors=True)
        if moved_old_photos and old_photos.exists():
            os.replace(old_photos, photos_target)
        if installed_db:
            db_target.unlink(missing_ok=True)
        Path(str(db_target) + "-wal").unlink(missing_ok=True)
        Path(str(db_target) + "-shm").unlink(missing_ok=True)
        if moved_old_db and old_db.exists():
            os.replace(old_db, db_target)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)

