"""FastAPI application for Mana Arena."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError

from . import engine
from .auth import AuthManager, hash_password, valid_bootstrap_password
from .backup import create_backup, restore_backup
from .db import Database, MUTATION_LOCK, _dump
from .service import champion_id, public_rounds, safe_csv

MAX_PHOTO = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000
MAX_RESTORE = 100 * 1024 * 1024
SESSION_COOKIE = "mana_session"
CSRF_COOKIE = "mana_csrf"
CSRF_HEADER = "X-CSRF-Token"


def _detail(status: int, message: str, **extra):
    raise HTTPException(status_code=status, detail={"message": message, **extra})


async def _json(request: Request) -> dict:
    try:
        value = await request.json()
    except Exception:
        _detail(422, "Il corpo JSON non è valido.")
    if not isinstance(value, dict):
        _detail(422, "Il corpo della richiesta deve essere un oggetto JSON.")
    return value


def _cfg(value: dict) -> dict:
    if isinstance(value, dict) and (
        len(str(value.get("name", ""))) > 120
        or len(str(value.get("game_format", ""))) > 80
        or len(str(value.get("description", ""))) > 4000
    ):
        _detail(422, "Nome, formato o descrizione superano la lunghezza consentita.")
    try:
        engine.validate_config(value)
    except ValueError as exc:
        _detail(422, str(exc))
    return value


def _row_tournament(conn, tid):
    item = Database.get_tournament(conn, tid)
    if not item:
        _detail(404, "Torneo non trovato.")
    return item


def _bump(conn, tid, status=None, config=None, frozen=None, warnings=None, champ_marker=False):
    fields = ["revision=revision+1", "updated_at=CURRENT_TIMESTAMP"]
    params = []
    if status is not None:
        fields.append("status=?"); params.append(status)
    if config is not None:
        fields.append("config_json=?"); params.append(_dump(config))
    if frozen is not None:
        fields.append("frozen_config_json=?"); params.append(_dump(frozen) if frozen else None)
    if warnings is not None:
        fields.append("warnings_json=?"); params.append(_dump(warnings))
    if champ_marker:
        fields.append("champion_id=NULL")
    params.append(tid)
    conn.execute(f"UPDATE tournaments SET {', '.join(fields)} WHERE id=?", params)


def _persist_match_rows(conn, tid, matches):
    Database.save_matches(conn, tid, matches)


def _set_champion(conn, tid, champ):
    conn.execute("UPDATE tournaments SET champion_id=? WHERE id=?", (champ, tid))


def _finalize_if_done(conn, tournament, entrants, matches):
    if tournament["status"] != "active":
        return tournament
    cfg = tournament["frozen_config"] or tournament["config"]
    final_round = max((m["round"] for m in matches), default=0)
    latest = [m for m in matches if m["round"] == final_round]
    completed = False
    champ = None
    if cfg["structure"] == "swiss":
        completed = final_round >= cfg["rounds"] and bool(latest) and all(m.get("result") for m in latest)
        if completed:
            champ = champion_id(cfg, entrants, matches)
    elif cfg["structure"] == "adapted":
        standings = engine.calculate_standings(cfg, entrants, matches)
        survivors = [row for row in standings if not row["eliminated"]]
        completed = len(survivors) <= 1 and bool(latest) and all(m.get("result") for m in latest)
        champ = survivors[0]["player_id"] if len(survivors) == 1 else None
    elif cfg["structure"] == "single_elim":
        completed = len(latest) == 1 and bool(latest) and all(m.get("result") for m in latest)
        if completed:
            champ = champion_id(cfg, entrants, matches)
    else:
        completed = bool(matches) and all(m.get("result") for m in matches)
        if completed:
            rows = engine.calculate_standings(cfg, entrants, matches)
            champ = rows[0]["player_id"] if rows else None
    if completed:
        conn.execute("UPDATE tournaments SET status='completed',champion_id=? WHERE id=?", (champ, tournament["id"]))
        Database.audit(conn, tournament["id"], "tournament_completed", {"champion_id": champ})
        tournament["status"] = "completed"
        tournament["champion_id"] = champ
    return tournament


def create_app(*, data_dir=None, admin_username=None, admin_password=None, session_secret=None,
               cookie_secure=None, login_limit=5):
    """Create an isolated app; environment validation occurs in the lifespan."""
    data_dir = Path(data_dir or os.getenv("DATA_DIR", "/data"))
    username = admin_username if admin_username is not None else os.getenv("ADMIN_USERNAME", "admin")
    password = admin_password if admin_password is not None else os.getenv("ADMIN_PASSWORD")
    secret = session_secret if session_secret is not None else os.getenv("SESSION_SECRET")
    secure = cookie_secure if cookie_secure is not None else os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
    db = Database(data_dir)

    @asynccontextmanager
    async def lifespan(application):
        if not username or len(username) > 80:
            raise RuntimeError("ADMIN_USERNAME non è valido.")
        if not secret or len(secret.encode("utf-8")) < 32:
            raise RuntimeError("SESSION_SECRET deve contenere almeno 32 byte.")
        db.initialize()
        with MUTATION_LOCK, db.transaction() as conn:
            admin = Database.admin_row(conn)
            if admin is None:
                if not valid_bootstrap_password(password):
                    raise RuntimeError("ADMIN_PASSWORD deve essere impostata e contenere almeno 12 caratteri non segnaposto.")
                salt, digest = hash_password(password)
                Database.insert_admin(conn, username, salt, digest)
                admin_name = username
            else:
                admin_name = admin["username"]
                if admin_name != username:
                    raise RuntimeError("ADMIN_USERNAME deve corrispondere all'amministratore già configurato.")
        application.state.db = db
        application.state.auth = AuthManager(secret, admin_name, bool(secure), login_limit)
        yield
        application.state.auth.revoke_all()

    application = FastAPI(title="Mana Arena", version="1.0.0", lifespan=lifespan)
    application.state.db = db

    def admin_session(request: Request):
        auth: AuthManager = request.app.state.auth
        token = request.cookies.get(SESSION_COOKIE)
        payload = auth.verify_session(token)
        if not payload:
            _detail(401, "Accesso amministratore richiesto.")
        with db.transaction() as conn:
            exists = conn.execute("SELECT 1 FROM sessions WHERE nonce=? AND username=? AND expires_at>=strftime('%s','now')", (payload["nonce"], payload["sub"])).fetchone()
        if not exists:
            _detail(401, "Sessione amministratore scaduta o revocata.")
        return payload

    def admin_write(request: Request, session=Depends(admin_session)):
        token = request.headers.get(CSRF_HEADER)
        cookie = request.cookies.get(CSRF_COOKIE)
        if not token or not cookie or token != session["csrf"] or cookie != session["csrf"]:
            _detail(403, "Token di sicurezza CSRF mancante o non valido.")
        return session

    @application.get("/healthz")
    def health():
        try:
            db.health()
        except Exception:
            _detail(503, "Database non disponibile.")
        return {"status": "ok"}

    @application.get("/")
    def home():
        index = Path(__file__).parent / "static" / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html; charset=utf-8")
        return HTMLResponse("<!doctype html><html lang='it'><meta charset='utf-8'><title>Mana Arena</title><body><main><h1>Mana Arena</h1><p>Interfaccia in preparazione.</p></main></body></html>")

    @application.get("/photos/{filename}")
    def photo(filename: str):
        if not re.fullmatch(r"[0-9a-f]{32}\.jpg", filename):
            _detail(404, "Foto non trovata.")
        path = db.photos_dir / filename
        if not path.is_file():
            _detail(404, "Foto non trovata.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    static_dir = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=static_dir, check_dir=False), name="static")

    @application.post("/api/admin/login")
    async def login(request: Request, response: Response):
        body = await _json(request)
        user = body.get("username", "")
        passwd = body.get("password", "")
        if not isinstance(user, str) or not isinstance(passwd, str) or len(user) > 80 or len(passwd) > 1024:
            _detail(422, "Credenziali non valide.")
        key = f"{request.client.host if request.client else 'unknown'}:{user.casefold()}"
        auth: AuthManager = request.app.state.auth
        if not auth.allowed(key):
            _detail(429, "Troppi tentativi. Riprova tra poco.")
        with MUTATION_LOCK, db.transaction() as conn:
            admin = Database.admin_row(conn)
        valid = False
        if admin and secrets.compare_digest(user, admin["username"]):
            _, digest = hash_password(passwd, admin["salt"])
            valid = secrets.compare_digest(digest, admin["password_hash"])
        if not valid:
            auth.failed(key)
            _detail(401, "Credenziali non valide.")
        auth.succeeded(key)
        token, csrf, nonce, expires_at = auth.create_session(user)
        with MUTATION_LOCK, db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < strftime('%s','now')")
            conn.execute("INSERT INTO sessions(nonce,username,expires_at) VALUES(?,?,?)", (nonce, user, expires_at))
        response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=auth.secure, samesite="lax", max_age=28800, path="/")
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=auth.secure, samesite="lax", max_age=28800, path="/")
        return {"username": user, "csrf_token": csrf, "expires_in": 28800}

    @application.post("/api/admin/logout")
    def logout(request: Request, session=Depends(admin_write)):
        token = request.cookies.get(SESSION_COOKIE)
        request.app.state.auth.revoke(token)
        with MUTATION_LOCK, db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE nonce=?", (session["nonce"],))
        response = Response(content=json.dumps({"ok": True}), media_type="application/json")
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @application.get("/api/admin/session")
    def session_status(session=Depends(admin_session)):
        return {"username": session["sub"], "csrf_token": session["csrf"], "expires_at": session["exp"]}

    @application.post("/api/admin/password")
    async def change_password(request: Request, session=Depends(admin_write)):
        body = await _json(request)
        current, new = body.get("current_password"), body.get("new_password")
        if not isinstance(current, str) or not isinstance(new, str) or len(new) > 1024 or not valid_bootstrap_password(new):
            _detail(422, "La nuova password deve contenere almeno 12 caratteri e non essere un segnaposto.")
        with MUTATION_LOCK, db.transaction() as conn:
            admin = Database.admin_row(conn)
            _, digest = hash_password(current, admin["salt"])
            if not secrets.compare_digest(digest, admin["password_hash"]):
                _detail(401, "Password attuale non valida.")
            salt, digest = hash_password(new)
            conn.execute("UPDATE admin SET salt=?,password_hash=?,revision=revision+1 WHERE id=1", (salt, digest))
            conn.execute("DELETE FROM sessions")
        request.app.state.auth.revoke_all()
        response = Response(content=json.dumps({"ok": True}), media_type="application/json")
        response.delete_cookie(SESSION_COOKIE, path="/"); response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @application.get("/api/admin/players")
    def admin_players(session=Depends(admin_session)):
        with db.transaction() as conn:
            rows = conn.execute("SELECT * FROM players WHERE active=1 ORDER BY name COLLATE NOCASE,id").fetchall()
            return [Database.player(row) for row in rows]

    @application.post("/api/admin/players", status_code=201)
    async def create_player(request: Request, session=Depends(admin_write)):
        body = await _json(request); name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            _detail(422, "Il nome del giocatore è obbligatorio e deve contenere al massimo 80 caratteri.")
        pid = str(uuid.uuid4())
        with MUTATION_LOCK, db.transaction() as conn:
            conn.execute("INSERT INTO players(id,name) VALUES(?,?)", (pid, name.strip()))
            row = conn.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
        return Database.player(row)

    @application.patch("/api/admin/players/{player_id}")
    async def edit_player(player_id: str, request: Request, session=Depends(admin_write)):
        body = await _json(request); name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            _detail(422, "Il nome del giocatore è obbligatorio e deve contenere al massimo 80 caratteri.")
        with MUTATION_LOCK, db.transaction() as conn:
            row = conn.execute("SELECT * FROM players WHERE id=? AND active=1", (player_id,)).fetchone()
            if not row: _detail(404, "Giocatore non trovato.")
            conn.execute("UPDATE players SET name=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name.strip(), player_id))
            row = conn.execute("SELECT * FROM players WHERE id=?", (player_id,)).fetchone()
        return Database.player(row)

    @application.delete("/api/admin/players/{player_id}")
    def delete_player(player_id: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            row = conn.execute("SELECT * FROM players WHERE id=? AND active=1", (player_id,)).fetchone()
            if not row: _detail(404, "Giocatore non trovato.")
            if conn.execute("SELECT 1 FROM entrants WHERE player_id=? LIMIT 1", (player_id,)).fetchone():
                _detail(409, "Il giocatore è già iscritto a uno o più tornei e non può essere eliminato.")
            old_photo = row["photo"]
            conn.execute("UPDATE players SET active=0,photo=NULL WHERE id=?", (player_id,))
        if old_photo:
            (db.photos_dir / old_photo).unlink(missing_ok=True)
        return {"ok": True}

    @application.post("/api/admin/players/{player_id}/photo")
    async def upload_photo(player_id: str, file: UploadFile = File(...), session=Depends(admin_write)):
        raw = await file.read(MAX_PHOTO + 1)
        await file.close()
        if len(raw) > MAX_PHOTO:
            _detail(413, "La foto non può superare 5 MiB.")
        if not raw:
            _detail(422, "Seleziona una foto JPEG, PNG o WebP valida.")
        try:
            image = Image.open(io.BytesIO(raw))
            if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > MAX_PHOTO_PIXELS or max(image.size) > 6000:
                _detail(422, "La foto deve essere JPEG, PNG o WebP con dimensioni contenute.")
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            output = io.BytesIO(); image.save(output, format="JPEG", quality=88, optimize=True)
            encoded = output.getvalue()
        except (UnidentifiedImageError, OSError, ValueError):
            _detail(422, "Il file caricato non è un'immagine valida.")
        new_name = secrets.token_hex(16) + ".jpg"
        db.photos_dir.mkdir(parents=True, exist_ok=True)
        new_path = db.photos_dir / new_name
        try:
            with MUTATION_LOCK:
                new_path.write_bytes(encoded)
                with db.transaction() as conn:
                    row = conn.execute("SELECT photo FROM players WHERE id=? AND active=1", (player_id,)).fetchone()
                    if not row: _detail(404, "Giocatore non trovato.")
                    old_name = row["photo"]
                    conn.execute("UPDATE players SET photo=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_name, player_id))
                if old_name and old_name != new_name:
                    (db.photos_dir / old_name).unlink(missing_ok=True)
        except Exception:
            new_path.unlink(missing_ok=True)
            raise
        return {"photo_url": f"/photos/{new_name}"}

    @application.get("/api/admin/tournaments")
    def admin_tournaments(session=Depends(admin_session)):
        with db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tournaments ORDER BY created_at DESC,id").fetchall()
            return [Database.tournament(row) for row in rows]

    @application.post("/api/admin/tournaments", status_code=201)
    async def create_tournament(request: Request, session=Depends(admin_write)):
        body = await _json(request); cfg = _cfg(body.get("config"))
        tid = str(uuid.uuid4())
        with MUTATION_LOCK, db.transaction() as conn:
            conn.execute("INSERT INTO tournaments(id,config_json,status) VALUES(?,?,'draft')", (tid, _dump(cfg)))
            Database.audit(conn, tid, "tournament_created", {"name": cfg["name"]})
            return Database.get_tournament(conn, tid)

    @application.get("/api/admin/tournaments/{tid}")
    def admin_tournament(tid: str, session=Depends(admin_session)):
        with db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            entrants = Database.get_entrants(conn, tid)
            matches = Database.get_matches(conn, tid)
            cfg = tournament["frozen_config"] or tournament["config"]
            return {**tournament, "entrants": entrants, "matches": matches,
                    "rounds": public_rounds(matches),
                    "standings": engine.calculate_standings(cfg, entrants, matches)}

    @application.put("/api/admin/tournaments/{tid}/config")
    async def update_config(tid: str, request: Request, confirm_rollback: bool = False, session=Depends(admin_write)):
        body = await _json(request); cfg = _cfg(body.get("config"))
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] in ("completed", "archived"):
                matches = Database.get_matches(conn, tid)
                _detail(409, "Riapri esplicitamente il torneo prima di confermare il rollback.", requires_reopen=True, requires_confirmation=bool(confirm_rollback),
                        affected_rounds=sorted({m["round"] for m in matches}), affected_results=sum(bool(m.get("result")) for m in matches))
            matches = Database.get_matches(conn, tid)
            if tournament["status"] != "draft" and not confirm_rollback:
                _detail(409, "La configurazione è congelata; conferma il rollback completo.", requires_confirmation=True,
                        affected_rounds=sorted({m["round"] for m in matches}), affected_results=sum(bool(m.get("result")) for m in matches))
            if confirm_rollback and tournament["status"] != "draft":
                conn.execute("DELETE FROM matches WHERE tournament_id=?", (tid,))
                conn.execute("UPDATE entrants SET withdrawn=0 WHERE tournament_id=?", (tid,))
                conn.execute("DELETE FROM withdrawals WHERE tournament_id=?", (tid,))
                Database.audit(conn, tid, "full_rollback_for_config", {"rounds": sorted({m["round"] for m in matches}), "results": sum(bool(m.get("result")) for m in matches)})
            _bump(conn, tid, status="draft", config=cfg, frozen={})
            conn.execute("UPDATE tournaments SET frozen_config_json=NULL,champion_id=NULL WHERE id=?", (tid,))
            Database.audit(conn, tid, "config_updated", {"name": cfg["name"]})
            return Database.get_tournament(conn, tid)

    @application.delete("/api/admin/tournaments/{tid}")
    def delete_tournament(tid: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft":
                _detail(409, "Solo i tornei in bozza possono essere eliminati.")
            conn.execute("DELETE FROM tournaments WHERE id=?", (tid,))
        return {"ok": True}

    @application.post("/api/admin/tournaments/{tid}/entrants")
    async def add_entrants(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request); ids = body.get("player_ids")
        if not isinstance(ids, list) or len(ids) > 64 or len(set(ids)) != len(ids) or any(not isinstance(x, str) for x in ids):
            _detail(422, "Seleziona un elenco valido di giocatori senza duplicati.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft": _detail(409, "Le iscrizioni sono chiuse dopo l'avvio.")
            current = Database.get_entrants(conn, tid)
            if len(current) + len(ids) > 64: _detail(422, "Un torneo può avere al massimo 64 partecipanti.")
            for pid in ids:
                player_row = conn.execute("SELECT id FROM players WHERE id=? AND active=1", (pid,)).fetchone()
                if not player_row: _detail(422, "Uno dei giocatori selezionati non esiste o non è disponibile.")
                if any(p["id"] == pid for p in current): _detail(409, "Il giocatore è già iscritto al torneo.")
                seed = max((p["seed"] for p in current), default=0) + 1
                conn.execute("INSERT INTO entrants(tournament_id,player_id,seed) VALUES(?,?,?)", (tid, pid, seed))
                current.append({"id": pid, "seed": seed})
            Database.audit(conn, tid, "entrants_added", {"player_ids": ids})
            _bump(conn, tid)
            return Database.get_entrants(conn, tid)

    @application.put("/api/admin/tournaments/{tid}/entrants/{player_id}")
    async def seed_entrant(tid: str, player_id: str, request: Request, session=Depends(admin_write)):
        body = await _json(request); seed = body.get("seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 1: _detail(422, "La testa di serie deve essere un intero positivo.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft": _detail(409, "Le teste di serie sono congelate dopo l'avvio.")
            row = conn.execute("SELECT 1 FROM entrants WHERE tournament_id=? AND player_id=?", (tid, player_id)).fetchone()
            if not row: _detail(404, "Iscrizione non trovata.")
            count = conn.execute("SELECT COUNT(*) FROM entrants WHERE tournament_id=?", (tid,)).fetchone()[0]
            if seed > max(1, count): _detail(422, "La testa di serie deve rientrare nel numero degli iscritti.")
            current = conn.execute("SELECT seed FROM entrants WHERE tournament_id=? AND player_id=?", (tid, player_id)).fetchone()[0]
            other = conn.execute("SELECT player_id FROM entrants WHERE tournament_id=? AND seed=? AND player_id<>?", (tid, seed, player_id)).fetchone()
            conn.execute("UPDATE entrants SET seed=? WHERE tournament_id=? AND player_id=?", (seed, tid, player_id))
            if other:
                conn.execute("UPDATE entrants SET seed=? WHERE tournament_id=? AND player_id=?", (current, tid, other["player_id"]))
            Database.audit(conn, tid, "entrant_seed_changed", {"player_id": player_id, "seed": seed})
            _bump(conn, tid)
            return Database.get_entrants(conn, tid)

    @application.delete("/api/admin/tournaments/{tid}/entrants/{player_id}")
    def remove_entrant(tid: str, player_id: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft": _detail(409, "Le iscrizioni sono congelate dopo l'avvio.")
            cur = conn.execute("DELETE FROM entrants WHERE tournament_id=? AND player_id=?", (tid, player_id))
            if not cur.rowcount: _detail(404, "Iscrizione non trovata.")
            Database.audit(conn, tid, "entrant_removed", {"player_id": player_id}); _bump(conn, tid)
            return Database.get_entrants(conn, tid)

    @application.post("/api/admin/tournaments/{tid}/start")
    async def start_tournament(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft": _detail(409, "Il torneo non è più una bozza.")
            entrants = Database.get_entrants(conn, tid)
            if not 2 <= len(entrants) <= 64: _detail(422, "Il torneo deve avere da 2 a 64 partecipanti.")
            cfg = dict(tournament["config"])
            if cfg["seeding"] == "random":
                import random
                seed_value = secrets.randbelow(2**31 - 1) + 1
                ordered = list(entrants); random.Random(seed_value).shuffle(ordered)
                for index, person in enumerate(ordered, 1):
                    conn.execute("UPDATE entrants SET seed=? WHERE tournament_id=? AND player_id=?", (index, tid, person["id"]))
                cfg["random_seed"] = seed_value
                entrants = Database.get_entrants(conn, tid)
            engine.validate_config(cfg)
            schedule = {"matches": [], "warnings": []}
            if cfg["structure"] in ("league", "single_elim"):
                schedule = engine.make_schedule(cfg, entrants)
                rows = []
                for match in schedule["matches"]:
                    if match.get("bye") and match.get("result") is None:
                        match = engine.apply_result(cfg, match, {"result": "bye", "p1_games": 0, "p2_games": 0})
                    rows.append(match)
                schedule["matches"] = rows
                _persist_match_rows(conn, tid, rows)
            conn.execute("UPDATE tournaments SET status='active',frozen_config_json=?,warnings_json=?,champion_id=NULL,revision=revision+1 WHERE id=?",
                         (_dump(cfg), _dump(schedule["warnings"]), tid))
            Database.audit(conn, tid, "tournament_started", {"random_seed": cfg.get("random_seed"), "entrant_count": len(entrants)})
            current = Database.get_tournament(conn, tid)
            _finalize_if_done(conn, current, entrants, schedule["matches"])
            return {**Database.get_tournament(conn, tid), "entrants": Database.get_entrants(conn, tid), "matches": Database.get_matches(conn, tid)}

    @application.post("/api/admin/tournaments/{tid}/rounds/next")
    async def next_round(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "active": _detail(409, "Il torneo non è attivo.")
            cfg = tournament["frozen_config"] or tournament["config"]
            if cfg["structure"] == "league": _detail(409, "Il calendario del campionato viene creato all'avvio.")
            matches = Database.get_matches(conn, tid)
            latest_round = max((m["round"] for m in matches), default=0)
            latest = [m for m in matches if m["round"] == latest_round]
            if latest and any(m.get("result") is None for m in latest):
                _detail(409, "Completa tutti gli incontri del turno corrente prima di crearne un altro.", round=latest_round)
            entrants = Database.get_entrants(conn, tid)
            number = latest_round + 1
            schedule = engine.make_round(cfg, entrants, matches, number)
            if not schedule["matches"]:
                _detail(409, "Non è possibile creare un altro turno.")
            new = []
            for match in schedule["matches"]:
                if match.get("bye") and match.get("result") is None:
                    match = engine.apply_result(cfg, match, {"result": "bye", "p1_games": 0, "p2_games": 0})
                new.append(match)
            matches.extend(new)
            _persist_match_rows(conn, tid, matches)
            warnings = tournament["warnings"] + schedule["warnings"]
            _bump(conn, tid, warnings=warnings)
            Database.audit(conn, tid, "round_created", {"round": number, "matches": len(new), "warnings": schedule["warnings"]})
            _finalize_if_done(conn, tournament, entrants, matches)
            return {"round": number, "matches": [m for m in Database.get_matches(conn, tid) if m["round"] == number], "warnings": schedule["warnings"], "revision": Database.get_tournament(conn, tid)["revision"]}

    @application.put("/api/admin/tournaments/{tid}/matches/{match_id}/result")
    async def set_result(tid: str, match_id: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        if set(body) - {"result", "p1_games", "p2_games"}: _detail(422, "Il corpo del risultato contiene campi non supportati.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "active": _detail(409, "Il torneo non è attivo.")
            cfg = tournament["frozen_config"] or tournament["config"]
            matches = Database.get_matches(conn, tid)
            found = next((m for m in matches if m["id"] == match_id), None)
            if not found: _detail(404, "Incontro non trovato.")
            if found.get("result") is not None: _detail(409, "L'incontro è già stato registrato. Usa il rollback per correggerlo.")
            entrants = Database.get_entrants(conn, tid)
            try:
                finalized = engine.apply_result(cfg, found, body)
            except ValueError as exc:
                _detail(422, str(exc))
            matches[matches.index(found)] = finalized
            _persist_match_rows(conn, tid, matches)
            _bump(conn, tid)
            Database.audit(conn, tid, "match_result_recorded", {"match_id": match_id, "round": found["round"], "result": finalized["result"]})
            _finalize_if_done(conn, tournament, entrants, matches)
            return {"id": match_id, **finalized, "status": Database.get_tournament(conn, tid)["status"]}

    @application.post("/api/admin/tournaments/{tid}/withdrawals")
    async def withdraw(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request); pid = body.get("player_id")
        if not isinstance(pid, str): _detail(422, "Seleziona un partecipante.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "active": _detail(409, "Il ritiro è disponibile durante un torneo attivo.")
            cfg = tournament["frozen_config"] or tournament["config"]
            entrants = Database.get_entrants(conn, tid)
            entrant = next((p for p in entrants if p["id"] == pid), None)
            if not entrant: _detail(404, "Partecipante non trovato.")
            if entrant["withdrawn"]: _detail(409, "Il partecipante risulta già ritirato.")
            matches = Database.get_matches(conn, tid)
            open_for_player = [m for m in matches if m.get("result") is None and pid in (m.get("p1"), m.get("p2"))]
            if cfg["structure"] == "league":
                effective_round = min((m["round"] for m in open_for_player), default=max((m["round"] for m in matches), default=0) + 1)
            else:
                latest_round = max((m["round"] for m in matches), default=0)
                latest_open = any(m.get("round") == latest_round and m.get("result") is None for m in matches)
                effective_round = latest_round if latest_round and latest_open else latest_round + 1
            conn.execute("INSERT INTO withdrawals(tournament_id,player_id,effective_round) VALUES(?,?,?)", (tid, pid, effective_round))
            conn.execute("UPDATE entrants SET withdrawn=1 WHERE tournament_id=? AND player_id=?", (tid, pid))
            for index, match in enumerate(matches):
                if match.get("round", 0) < effective_round or match.get("result") is not None or pid not in (match.get("p1"), match.get("p2")) or not match.get("p2"):
                    continue
                other = match["p2"] if match["p1"] == pid else match["p1"]
                outcome = "p1_forfeit" if match["p1"] == pid else "p2_forfeit"
                try:
                    matches[index] = engine.apply_result(cfg, match, {"result": outcome, "p1_games": 0, "p2_games": 0})
                except ValueError:
                    continue
                if cfg["structure"] != "league":
                    break
            if cfg["structure"] == "league":
                for index, match in enumerate(matches):
                    if match.get("round", 0) >= effective_round and match.get("result") is None and pid in (match.get("p1"), match.get("p2")) and match.get("p2"):
                        outcome = "p1_forfeit" if match["p1"] == pid else "p2_forfeit"
                        matches[index] = engine.apply_result(cfg, match, {"result": outcome, "p1_games": 0, "p2_games": 0})
            _persist_match_rows(conn, tid, matches)
            _bump(conn, tid)
            Database.audit(conn, tid, "player_withdrew", {"player_id": pid, "effective_round": effective_round})
            refreshed = Database.get_entrants(conn, tid)
            _finalize_if_done(conn, tournament, refreshed, matches)
            return {"entrant": next(p for p in refreshed if p["id"] == pid), "effective_round": effective_round, "matches": matches}

    @application.post("/api/admin/tournaments/{tid}/rollback")
    async def rollback(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request); match_id = body.get("match_id"); confirm = body.get("confirm") is True
        if not isinstance(match_id, str): _detail(422, "Seleziona l'incontro da correggere.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            matches = Database.get_matches(conn, tid)
            target = next((m for m in matches if m["id"] == match_id), None)
            if not target: _detail(404, "Incontro non trovato.")
            affected_rounds = sorted({m["round"] for m in matches if m["round"] >= target["round"]})
            invalidated = [m for m in matches if m["round"] >= target["round"]]
            summary = {"affected_rounds": affected_rounds, "affected_results": sum(bool(m.get("result")) for m in invalidated), "match_ids": [m["id"] for m in invalidated]}
            if not confirm:
                _detail(409, "Conferma il rollback dei risultati e degli abbinamenti dipendenti.", requires_confirmation=True, **summary)
            cfg = tournament["frozen_config"] or tournament["config"]
            withdrawals = Database.get_withdrawals(conn, tid)
            conn.execute("DELETE FROM withdrawals WHERE tournament_id=? AND effective_round>=?", (tid, target["round"]))
            withdrawals = [row for row in withdrawals if row["effective_round"] < target["round"]]
            active_withdrawals = {row["player_id"]: row["effective_round"] for row in withdrawals}
            conn.execute("UPDATE entrants SET withdrawn=0 WHERE tournament_id=?", (tid,))
            for player_id in active_withdrawals:
                conn.execute("UPDATE entrants SET withdrawn=1 WHERE tournament_id=? AND player_id=?", (tid, player_id))
            if cfg["structure"] == "league":
                kept = []
                for match in matches:
                    if match["round"] >= target["round"] and match.get("result") is not None and match.get("result") != "bye":
                        match = {**match, "result": None, "p1_games": 0, "p2_games": 0,
                                 "p1_points": 0, "p2_points": 0, "p1_wins": 0, "p2_wins": 0,
                                 "p1_draws": 0, "p2_draws": 0, "p1_losses": 0, "p2_losses": 0}
                    if match["round"] >= target["round"] and match.get("result") is None and match.get("p2"):
                        left_out = active_withdrawals.get(match["p1"], 10**9) <= match["round"]
                        right_out = active_withdrawals.get(match["p2"], 10**9) <= match["round"]
                        if left_out ^ right_out:
                            result = "p1_forfeit" if left_out else "p2_forfeit"
                            match = engine.apply_result(cfg, match, {"result": result, "p1_games": 0, "p2_games": 0})
                        elif left_out and right_out:
                            match = {**match, "result": "void", "p1_games": 0, "p2_games": 0,
                                     "p1_points": 0, "p2_points": 0, "p1_wins": 0, "p2_wins": 0,
                                     "p1_draws": 0, "p2_draws": 0, "p1_losses": 0, "p2_losses": 0}
                    kept.append(match)
            else:
                kept = [m for m in matches if m["round"] < target["round"]]
            _persist_match_rows(conn, tid, kept)
            conn.execute("UPDATE tournaments SET status='active',champion_id=NULL WHERE id=?", (tid,))
            Database.audit(conn, tid, "match_rollback", summary); _bump(conn, tid)
            return {"ok": True, "status": "active", "remaining_matches": kept, **summary}

    @application.post("/api/admin/tournaments/{tid}/reset")
    async def reset_tournament(tid: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        if body.get("confirm") is not True: _detail(422, "Conferma esplicita il ripristino della bozza.")
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            matches = Database.get_matches(conn, tid)
            conn.execute("DELETE FROM matches WHERE tournament_id=?", (tid,))
            conn.execute("UPDATE entrants SET withdrawn=0 WHERE tournament_id=?", (tid,))
            conn.execute("DELETE FROM withdrawals WHERE tournament_id=?", (tid,))
            conn.execute("UPDATE tournaments SET status='draft',frozen_config_json=NULL,champion_id=NULL WHERE id=?", (tid,))
            Database.audit(conn, tid, "tournament_reset", {"rounds": sorted({m["round"] for m in matches}), "results": sum(bool(m.get("result")) for m in matches)})
            _bump(conn, tid)
            return Database.get_tournament(conn, tid)

    @application.post("/api/admin/tournaments/{tid}/archive")
    def archive_tournament(tid: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] == "archived": _detail(409, "Il torneo è già archiviato.")
            conn.execute("UPDATE tournaments SET archived_from=status,status='archived',revision=revision+1 WHERE id=?", (tid,))
            Database.audit(conn, tid, "tournament_archived", {"previous_status": tournament["status"]})
            return Database.get_tournament(conn, tid)

    @application.post("/api/admin/tournaments/{tid}/reopen")
    def reopen_tournament(tid: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] == "archived":
                status = tournament["archived_from"] or ("active" if tournament["frozen_config"] else "draft")
                conn.execute("UPDATE tournaments SET status=?,archived_from=NULL,revision=revision+1 WHERE id=?", (status, tid))
                Database.audit(conn, tid, "tournament_reopened", {"status": status})
            elif tournament["status"] == "completed":
                status = "active"
                conn.execute("UPDATE tournaments SET status='active',revision=revision+1 WHERE id=?", (tid,))
                Database.audit(conn, tid, "completed_tournament_reopened_for_edit", {"previous_champion_id": tournament["champion_id"]})
            else:
                _detail(409, "Riapri un torneo archiviato o concluso.")
            return Database.get_tournament(conn, tid)

    @application.get("/api/admin/tournaments/{tid}/audit")
    def audit_log(tid: str, session=Depends(admin_session)):
        with db.transaction() as conn:
            _row_tournament(conn, tid)
            rows = conn.execute("SELECT id,action,detail_json,created_at FROM audit WHERE tournament_id=? ORDER BY id", (tid,)).fetchall()
            return [{"id": row["id"], "action": row["action"], "detail": json.loads(row["detail_json"]), "created_at": row["created_at"]} for row in rows]

    @application.get("/api/admin/tournaments/{tid}/export.csv")
    def export_csv(tid: str, kind: str = "standings", session=Depends(admin_session)):
        with db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            entrants = Database.get_entrants(conn, tid)
            matches = Database.get_matches(conn, tid)
            cfg = tournament["frozen_config"] or tournament["config"]
            out = io.StringIO(newline="")
            writer = csv.writer(out, lineterminator="\r\n")
            by_id = {row["id"]: row for row in entrants}
            if kind == "entrants":
                writer.writerow(["seed", "giocatore", "ritirato"])
                for row in entrants: writer.writerow([row["seed"], safe_csv(row["name"]), "sì" if row["withdrawn"] else "no"])
            elif kind == "matches":
                writer.writerow(["turno", "tavolo", "giocatore 1", "giocatore 2", "risultato", "game 1", "game 2"])
                for match in matches: writer.writerow([match["round"], match.get("table"), safe_csv(by_id.get(match.get("p1"), {}).get("name", "")), safe_csv(by_id.get(match.get("p2"), {}).get("name", "")), match.get("result") or "in attesa", match.get("p1_games", 0), match.get("p2_games", 0)])
            elif kind == "standings":
                writer.writerow(["posizione", "seed", "giocatore", "punti", "vittorie", "patte", "sconfitte", "bye", "differenza game", "OMW"])
                for row in engine.calculate_standings(cfg, entrants, matches):
                    person = by_id[row["player_id"]]
                    writer.writerow([row["rank"], row["seed"], safe_csv(person["name"]), row["points"], row["wins"], row["draws"], row["losses"], row["byes"], row["game_diff"], f"{row['omw']:.4f}"])
            else:
                _detail(422, "Tipo di esportazione non valido. Usa entrants, matches o standings.")
            content = "\ufeff" + out.getvalue()
        return Response(content=content, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="mana-arena-{kind}.csv"'})

    @application.get("/api/admin/backup")
    def backup(session=Depends(admin_session)):
        with MUTATION_LOCK:
            blob = create_backup(db)
        return Response(blob, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="mana-arena-backup.zip"', "Cache-Control": "no-store"})

    @application.post("/api/admin/restore")
    async def restore(request: Request, file: UploadFile = File(...), confirm: bool = False, session=Depends(admin_write)):
        if not confirm: _detail(422, "Conferma esplicitamente il ripristino con confirm=true.")
        blob = await file.read(MAX_RESTORE + 1); await file.close()
        if len(blob) > MAX_RESTORE: _detail(413, "Il backup non può superare 100 MiB.")
        with MUTATION_LOCK:
            try:
                def activate_restored_database():
                    db.initialize()
                    with db.transaction() as conn:
                        conn.execute("DELETE FROM sessions")
                restored_photos = restore_backup(db, blob, after_install=activate_restored_database)
            except ValueError as exc:
                _detail(422, str(exc))
            except Exception:
                _detail(422, "Non è stato possibile ripristinare il backup.")
            request.app.state.auth.revoke_all()
        response = Response(content=json.dumps({"ok": True, "restored_photos": len(restored_photos)}), media_type="application/json")
        response.delete_cookie(SESSION_COOKIE, path="/"); response.delete_cookie(CSRF_COOKIE, path="/")
        return response

    @application.get("/api/public/tournaments")
    def public_tournaments():
        with db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tournaments WHERE status IN ('active','completed') ORDER BY created_at DESC,id").fetchall()
            return [{"id": row["id"], "config": json.loads(row["frozen_config_json"] or row["config_json"]), "status": row["status"], "champion_id": row["champion_id"]} for row in rows]

    def public_detail(tid):
        with db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] not in ("active", "completed"):
                _detail(404, "Torneo non trovato.")
            entrants = Database.get_entrants(conn, tid)
            matches = Database.get_matches(conn, tid)
            cfg = tournament["frozen_config"] or tournament["config"]
            return {"id": tid, "config": cfg, "status": tournament["status"], "entrants": [Database.public_player({"id": p["id"], "name": p["name"], "photo": p["photo_url"].rsplit("/",1)[-1] if p["photo_url"] else None}) for p in entrants],
                    "matches": matches, "rounds": public_rounds(matches), "standings": engine.calculate_standings(cfg, entrants, matches),
                    "champion_id": tournament["champion_id"], "warnings": tournament["warnings"]}

    @application.get("/api/public/tournaments/{tid}")
    def public_tournament(tid: str):
        return public_detail(tid)

    @application.get("/api/public/tournaments/{tid}/standings")
    def public_standings(tid: str):
        return public_detail(tid)["standings"]

    @application.get("/api/public/tournaments/{tid}/rounds")
    def public_tournament_rounds(tid: str):
        return public_detail(tid)["rounds"]

    return application


app = create_app()

