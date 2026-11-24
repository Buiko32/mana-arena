"""FastAPI application for Mana Arena."""
from __future__ import annotations

import asyncio
import csv
import hashlib
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
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from .access import COOKIE as DEVICE_COOKIE, LIFETIME, DeviceAccess
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError

from datetime import datetime, timezone, timedelta
from .life import router as life_router
from .history import month_matches
from .tournament_results import _advance_completed_round, _bump, _finalize_if_done, _persist_match_rows, record_tournament_winner
from . import engine
from .auth import AuthManager, hash_password, valid_bootstrap_password
from .backup import create_backup, restore_backup
from .db import Database, MUTATION_LOCK, _dump
from .service import public_rounds, safe_csv, global_elo_from_db, player_statistics, elo_snapshot, elo_changes

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


def _set_champion(conn, tid, champ):
    conn.execute("UPDATE tournaments SET champion_id=? WHERE id=?", (champ, tid))


def create_app(*, data_dir=None, admin_username=None, admin_password=None, session_secret=None,
               cookie_secure=None, login_limit=5, allow_admin_result_override=False):
    """Create an isolated app; environment validation occurs in the lifespan."""
    data_dir = Path(data_dir or os.getenv("DATA_DIR", "/data"))
    username = admin_username if admin_username is not None else os.getenv("ADMIN_USERNAME", "admin")
    password = admin_password if admin_password is not None else os.getenv("ADMIN_PASSWORD")
    secret = session_secret if session_secret is not None else os.getenv("SESSION_SECRET")
    secure = cookie_secure if cookie_secure is not None else os.getenv("COOKIE_SECURE", "true").strip().lower() not in ("0", "false", "no")
    db = Database(data_dir)
    photo_slots = asyncio.Semaphore(2)

    @asynccontextmanager
    async def lifespan(application):
        if not username or len(username) > 80:
            raise RuntimeError("ADMIN_USERNAME non è valido.")
        if not secret or len(secret.encode("utf-8")) < 32:
            raise RuntimeError("SESSION_SECRET deve contenere almeno 32 byte.")
        shared_password = os.getenv("SHARED_ACCESS_PASSWORD")
        if not valid_bootstrap_password(shared_password) or len(shared_password.encode()) > 1024:
            raise RuntimeError("SHARED_ACCESS_PASSWORD deve avere almeno 12 caratteri, massimo 1024 byte e non essere un segnaposto.")
        if shared_password in (password, secret):
            raise RuntimeError("La password condivisa deve essere diversa dai segreti amministrativi.")
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
                _, shared_hash = hash_password(shared_password, admin["salt"])
                if secrets.compare_digest(shared_hash, admin["password_hash"]):
                    raise RuntimeError("La password condivisa deve essere diversa dalla password amministratore.")
                admin_name = admin["username"]
                if admin_name != username:
                    raise RuntimeError("ADMIN_USERNAME deve corrispondere all'amministratore già configurato.")
        application.state.access = DeviceAccess(db.db_dir, shared_password, secret)
        application.state.db = db
        application.state.auth = AuthManager(secret, admin_name, bool(secure), login_limit)
        yield
        application.state.auth.revoke_all()

    application = FastAPI(title="Mana Arena", version="1.0.0", lifespan=lifespan)
    application.state.db = db
    application.include_router(life_router)

    @application.middleware("http")
    async def require_device_access(request: Request, call_next):
        path = request.url.path
        public = (path in ("/healthz", "/life-worker.js") and request.method == "GET") or (
            path == "/api/access/login" and request.method == "POST"
        )
        if not public and not await run_in_threadpool(
            request.app.state.access.authorized, request.cookies.get(DEVICE_COOKIE)
        ):
            navigation = request.method == "GET" and (
                path in ("/", "/segnavita", "/profilo") or path.startswith("/profilo/")
                or request.headers.get("sec-fetch-mode") == "navigate"
            )
            if navigation and not path.startswith("/api/"):
                response = FileResponse(Path(__file__).with_name("access.html"), media_type="text/html", status_code=401)
                response.headers["X-Frame-Options"] = "DENY"
            else:
                response = JSONResponse({"detail": {"message": "Accesso a Mana Arena richiesto."}}, status_code=401)
        else:
            response = await call_next(request)
        if "no-store" not in response.headers.get("Cache-Control", ""):
            response.headers["Cache-Control"] = "private, no-store"
        response.headers.add_vary_header("Cookie")
        return response

    @application.post("/api/access/login")
    async def access_login(request: Request, response: Response):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            _detail(415, "Invia la password in formato JSON.")
        key = ("shared-access", request.client.host if request.client else "unknown")
        auth = request.app.state.auth
        if not auth.allowed(key):
            _detail(429, "Troppi tentativi. Riprova più tardi.")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 8192:
                _detail(413, "Richiesta troppo grande.")
            data.extend(chunk)
        try:
            body = json.loads(data)
        except (ValueError, RecursionError):
            _detail(422, "Richiesta non valida.")
        access = request.app.state.access
        if not isinstance(body, dict) or not access.accepts(body.get("password")):
            auth.failed(key)
            _detail(401, "Password non corretta.")
        auth.succeeded(key)
        token = await run_in_threadpool(access.register, request.headers.get("user-agent", ""))
        response.set_cookie(DEVICE_COOKIE, token, max_age=LIFETIME, httponly=True, secure=secure, samesite="lax", path="/")
        return {"ok": True}

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

    @application.get("/api/admin/devices")
    def devices(request: Request, session=Depends(admin_session)):
        return request.app.state.access.devices()

    @application.delete("/api/admin/devices/{device_id}")
    def revoke_device(device_id: str, request: Request, session=Depends(admin_write)):
        if not request.app.state.access.revoke(device_id):
            _detail(404, "Dispositivo non trovato.")
        return {"ok": True}

    def player_session(request: Request):
        import hashlib
        token = request.cookies.get('mana_player_session', '')
        digest = hashlib.sha256(token.encode()).hexdigest()
        with db.transaction() as conn:
            row = conn.execute("SELECT s.*,p.name FROM player_sessions s JOIN players p ON p.id=s.player_id WHERE s.token_hash=? AND s.expires_at>strftime('%s','now') AND p.active=1", (digest,)).fetchone()
        if not row: _detail(401, 'Accedi come giocatore.')
        return dict(row)

    def player_write(request: Request, session=Depends(player_session)):
        if not secrets.compare_digest(request.headers.get('X-Player-CSRF', ''), session['csrf']):
            _detail(403, 'Token di sicurezza non valido.')
        return session

    @application.post('/api/admin/players/{player_id}/credential')
    def player_credential(player_id: str, session=Depends(admin_write)):
        password = secrets.token_urlsafe(12)
        salt, digest = hash_password(password)
        with MUTATION_LOCK, db.transaction() as conn:
            if not conn.execute('SELECT 1 FROM players WHERE id=? AND active=1', (player_id,)).fetchone(): _detail(404, 'Giocatore non trovato.')
            conn.execute('INSERT OR REPLACE INTO player_credentials VALUES(?,?,?)', (player_id, salt, digest))
            conn.execute('DELETE FROM player_sessions WHERE player_id=?', (player_id,))
        return {'password': password}

    @application.post('/api/player/login')
    async def player_login(request: Request, response: Response):
        import hashlib, time
        body = await _json(request); pid = body.get('player_id')
        if not isinstance(pid, str) or not pid: _detail(422, 'Seleziona un giocatore.')
        token = secrets.token_urlsafe(32); csrf = secrets.token_urlsafe(24)
        with MUTATION_LOCK, db.transaction() as conn:
            row = conn.execute('SELECT id,name FROM players WHERE id=? AND active=1', (pid,)).fetchone()
            if not row: _detail(404, 'Giocatore non trovato.')
            conn.execute("DELETE FROM player_sessions WHERE expires_at < strftime('%s','now')")
            conn.execute('INSERT INTO player_sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), pid, csrf, int(time.time())+30*86400))
        response.set_cookie('mana_player_session', token, httponly=True, secure=secure, samesite='lax', max_age=30*86400, path='/')
        return {'player_id':pid, 'name':row['name'], 'csrf_token':csrf}

    @application.get('/api/player/session')
    def player_session_status(session=Depends(player_session)):
        return {'player_id':session['player_id'], 'name':session['name'], 'csrf_token':session['csrf']}

    @application.post('/api/player/logout')
    def player_logout(response: Response, session=Depends(player_write)):
        with db.transaction() as conn: conn.execute('DELETE FROM player_sessions WHERE token_hash=?', (session['token_hash'],))
        response.delete_cookie('mana_player_session', path='/')
        return {'ok':True}

    @application.get('/api/player/results')
    def player_results(session=Depends(player_session)):
        with db.transaction() as conn:
            rows = conn.execute("SELECT r.*,a.name AS proposer_name,b.name AS opponent_name,w.name AS winner_name FROM result_proposals r JOIN players a ON a.id=r.proposer_id JOIN players b ON b.id=r.opponent_id JOIN players w ON w.id=r.winner_id WHERE (r.proposer_id=? OR r.opponent_id=?) AND (r.status='pending' OR r.id IN (SELECT id FROM result_proposals WHERE (proposer_id=? OR opponent_id=?) AND status='confirmed' ORDER BY created_at DESC LIMIT 100)) ORDER BY r.created_at DESC", (session['player_id'],session['player_id'],session['player_id'],session['player_id'])).fetchall()
            return [dict(row) for row in rows]

    @application.get('/api/player/team-results')
    def list_team_results(session=Depends(player_session)):
        with db.transaction() as conn:
            rows=conn.execute("SELECT r.* FROM team_results r JOIN team_result_players me ON me.result_id=r.id WHERE me.player_id=? AND r.status='pending' ORDER BY r.created_at DESC",(session['player_id'],)).fetchall()
            result=[]
            for row in rows:
                members=[dict(m) for m in conn.execute("SELECT m.player_id,m.team,m.confirmed,p.name,p.photo,p.avatar_hue,p.avatar_color FROM team_result_players m JOIN players p ON p.id=m.player_id WHERE m.result_id=? ORDER BY m.team,p.name",(row['id'],))]
                result.append({**dict(row),'players':members})
            return result

    @application.post('/api/player/team-results',status_code=201)
    async def create_team_result(request: Request, session=Depends(player_write)):
        body=await _json(request); a=body.get('team_a'); b=body.get('team_b'); winner=body.get('winner')
        if not isinstance(a,list) or not isinstance(b,list) or not 1 <= len(a) <= 3 or not 1 <= len(b) <= 3 or not all(isinstance(x,str) for x in a+b) or len(set(a+b))!=len(a+b) or session['player_id'] not in a or winner not in ('a','b'):
            _detail(422,'Scegli da 1 a 3 giocatori distinti per parte e indica chi ha vinto.')
        with MUTATION_LOCK, db.transaction() as conn:
            if any(not conn.execute('SELECT 1 FROM players WHERE id=? AND active=1',(pid,)).fetchone() for pid in a+b): _detail(422,'Seleziona giocatori attivi.')
            before = elo_snapshot(conn, a + b)
            rid=str(uuid.uuid4())
            conn.execute("INSERT INTO team_results(id,proposer_id,winning_team,status) VALUES(?,?,?,'confirmed')",(rid,session['player_id'],winner))
            conn.executemany('INSERT INTO team_result_players(result_id,player_id,team) VALUES(?,?,?)',[(rid,pid,team) for team,items in (('a',a),('b',b)) for pid in items])
            Database.audit(conn, None, 'team_friendly_result_recorded', {'match_id': rid})
            return {'id':rid, 'elo_changes':elo_changes(conn, before)}

    @application.post('/api/player/team-results/{result_id}/confirm')
    def confirm_team_result(result_id: str, session=Depends(player_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            row=conn.execute('SELECT * FROM team_results WHERE id=?',(result_id,)).fetchone()
            if not row: _detail(404,'Proposta non trovata.')
            member=conn.execute("SELECT * FROM team_result_players WHERE result_id=? AND player_id=?",(result_id,session['player_id'])).fetchone()
            if not member or member['team']!='b': _detail(403,'Solo gli avversari possono confermare.')
            if row['status']!='pending' or member['confirmed']: _detail(409,'Proposta già gestita.')
            before = None
            if conn.execute("SELECT COUNT(*) FROM team_result_players WHERE result_id=? AND team='b' AND confirmed=0", (result_id,)).fetchone()[0] == 1:
                ids = [row['player_id'] for row in conn.execute('SELECT player_id FROM team_result_players WHERE result_id=?', (result_id,))]
                before = elo_snapshot(conn, ids)
            conn.execute('UPDATE team_result_players SET confirmed=1 WHERE result_id=? AND player_id=?',(result_id,session['player_id']))
            if not conn.execute("SELECT 1 FROM team_result_players WHERE result_id=? AND team='b' AND confirmed=0",(result_id,)).fetchone():
                conn.execute("UPDATE team_results SET status='confirmed' WHERE id=?",(result_id,))
                Database.audit(conn,None,'team_friendly_result_recorded',{'match_id':result_id})
                return {'ok':True, 'elo_changes':elo_changes(conn, before)}
        return {'ok':True}

    @application.post('/api/player/team-results/{result_id}/reject')
    def reject_team_result(result_id: str, session=Depends(player_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            row=conn.execute('SELECT * FROM team_results WHERE id=?',(result_id,)).fetchone()
            if not row: _detail(404,'Proposta non trovata.')
            member=conn.execute('SELECT team FROM team_result_players WHERE result_id=? AND player_id=?',(result_id,session['player_id'])).fetchone()
            if not member or (member['team']!='b' and session['player_id']!=row['proposer_id']): _detail(403,'Operazione non consentita.')
            if row['status']!='pending': _detail(409,'Proposta già gestita.')
            conn.execute("UPDATE team_results SET status='rejected' WHERE id=?",(result_id,))
        return {'ok':True}

    @application.get('/api/player/matches')
    def player_matches(session=Depends(player_session)):
        with db.transaction() as conn:
            rows = conn.execute("SELECT id, frozen_config_json, config_json FROM tournaments WHERE status='active'").fetchall()
            result = []
            for row in rows:
                matches = Database.get_matches(conn, row['id'])
                current, revealed = reveal_state(conn, row['id'], matches, session['player_id'])
                for match in matches:
                    if match['round'] == current and session['player_id'] in (match.get('p1'), match.get('p2')) and not match.get('result') and not match.get('void') and not match.get('bye'):
                        result.append({**match, 'revealed': revealed, 'tournament': {'id': row['id'], 'config': json.loads(row['frozen_config_json'] or row['config_json'])}})
            return result

    @application.post('/api/player/results', status_code=201)
    async def register_player_result(request: Request, session=Depends(player_write)):
        body=await _json(request); player=session['player_id']; tid=body.get('tournament_id'); mid=body.get('match_id'); winner=body.get('winner_id')
        with MUTATION_LOCK, db.transaction() as conn:
            if tid:
                tournament=_row_tournament(conn,tid)
                if tournament['status']!='active': _detail(409,'Il torneo non è attivo.')
                matches = Database.get_matches(conn,tid)
                current, _ = reveal_state(conn,tid,matches,player)
                match=next((m for m in matches if m['id']==mid and m['round']==current),None)
                if not match or player not in (match.get('p1'),match.get('p2')) or match.get('bye') or match.get('result') or match.get('void'): _detail(409,'Incontro non disponibile.')
                opponent=match['p2'] if match['p1']==player else match['p1']
                if not opponent: _detail(409,'Incontro non disponibile.')
            else:
                opponent=body.get('opponent_id'); tid=None; mid=None
                if not opponent or opponent==player or not conn.execute('SELECT 1 FROM players WHERE id=? AND active=1',(opponent,)).fetchone(): _detail(422,'Seleziona un avversario valido.')
            if winner not in (player,opponent): _detail(422,'Seleziona il vincitore.')
            before = elo_snapshot(conn, (player, opponent))
            rid=str(uuid.uuid4())
            conn.execute("INSERT INTO result_proposals(id,proposer_id,opponent_id,tournament_id,match_id,winner_id,status) VALUES(?,?,?,?,?,?,'confirmed')",(rid,player,opponent,tid,mid,winner))
            if tid:
                conn.execute("UPDATE result_proposals SET status='invalidated' WHERE tournament_id=? AND match_id=? AND status='pending'", (tid, mid))
                record_tournament_winner(conn, tournament, matches, match, winner)
            else:
                Database.audit(conn, None, 'friendly_result_recorded', {'match_id': rid})
            return {'id':rid, 'elo_changes':elo_changes(conn, before)}

    @application.post('/api/player/results/{result_id}/confirm')
    def confirm_player_result(result_id: str, session=Depends(player_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            proposal=conn.execute('SELECT * FROM result_proposals WHERE id=?',(result_id,)).fetchone()
            if not proposal: _detail(404,'Proposta non trovata.')
            if proposal['opponent_id']!=session['player_id']: _detail(403,'Solo l’avversario può confermare.')
            if proposal['status']!='pending': _detail(409,'Proposta già gestita.')
            before = elo_snapshot(conn, (proposal['proposer_id'], proposal['opponent_id']))
            if proposal['tournament_id']:
                tid=proposal['tournament_id']; tournament=_row_tournament(conn,tid)
                if tournament['status']!='active': _detail(409,'Il torneo non è attivo.')
                matches=Database.get_matches(conn,tid); match=next((m for m in matches if m['id']==proposal['match_id']),None)
                if not match or match.get('result') or match.get('void') or set((match.get('p1'),match.get('p2')))!=set((proposal['proposer_id'],proposal['opponent_id'])): _detail(409,'L’incontro non è più disponibile.')
                record_tournament_winner(conn, tournament, matches, match, proposal['winner_id'])
            if not proposal['tournament_id']:
                Database.audit(conn,None,'friendly_result_recorded',{'match_id':result_id})
            conn.execute("UPDATE result_proposals SET status='confirmed' WHERE id=?",(result_id,))
            return {'ok':True, 'elo_changes':elo_changes(conn, before)}

    @application.post('/api/player/results/{result_id}/reject')
    def reject_player_result(result_id: str, session=Depends(player_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            row=conn.execute('SELECT * FROM result_proposals WHERE id=?',(result_id,)).fetchone()
            if not row: _detail(404,'Proposta non trovata.')
            if session['player_id'] not in (row['opponent_id'],row['proposer_id']): _detail(403,'Operazione non consentita.')
            if row['status']!='pending': _detail(409,'Proposta già gestita.')
            conn.execute("UPDATE result_proposals SET status='rejected' WHERE id=?",(result_id,))
        return {'ok':True}

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
            html = index.read_text(encoding="utf-8")
            for asset in ("mana-arena-mark.svg", "style.css", "taste/motion.css", "taste/motion.js", "brackets.js", "reveal.js", "player-avatar.js", "result-celebration.js", "app.js", "player-results.js", "history.js", "admin-friendlies.js"):
                version = hashlib.sha256((index.parent / asset).read_bytes()).hexdigest()[:12]
                html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={version}"')
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        return HTMLResponse("<!doctype html><html lang='it'><meta charset='utf-8'><title>Mana Arena</title><body><main><h1>Mana Arena</h1><p>Interfaccia in preparazione.</p></main></body></html>")

    @application.get("/profilo", response_class=HTMLResponse)
    @application.get("/profilo/{player_id}", response_class=HTMLResponse)
    def profile_page(player_id: str = ""):
        folder = Path(__file__).parent / "static"
        html = (folder / "profile.html").read_text(encoding="utf-8")
        for asset in ("mana-arena-mark.svg", "style.css", "player-avatar.js", "player-profile.js"):
            version = hashlib.sha256((folder / asset).read_bytes()).hexdigest()[:12]
            html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={version}"')
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    life_assets = ("mana-arena-mark.svg", "style.css", "life.css", "player-avatar.js", "result-celebration.js", "life.js", "life-entry.js")

    def offline_shell():
        folder = Path(__file__).parent / "static"
        html = (folder / "life.html").read_text(encoding="utf-8")
        urls = []
        for name in life_assets:
            version = hashlib.sha256((folder / name).read_bytes()).hexdigest()[:12]
            url = f"/static/{name}?v={version}"
            html = html.replace(f'/static/{name}"', f'{url}"')
            urls.append(url)
        return html, urls

    @application.get("/segnavita", response_class=HTMLResponse)
    def life_page():
        html, _ = offline_shell()
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @application.get("/life-worker.js")
    def life_worker():
        html, urls = offline_shell()
        template = (Path(__file__).parent / "static/life-worker.js").read_text(encoding="utf-8")
        revision = hashlib.sha256((html + template).encode()).hexdigest()[:16]
        script = template.replace("__CACHE_NAME__", json.dumps("mana-life-shell-" + revision))
        script = script.replace("__ASSETS__", json.dumps(urls))
        return Response(script, media_type="application/javascript", headers={"Cache-Control": "no-cache"})

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
            if request.app.state.access.accepts(new):
                _detail(422, "Scegli una password amministratore diversa dalla password condivisa.")
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
            used = {row[0] for row in conn.execute("SELECT avatar_hue FROM players WHERE active=1")}
            palette = [215, 0, 120, 40, 280, 175, 325, 75, 250, 20, 150, 300]
            hue = next((value for value in palette + list(range(360)) if value not in used), 215)
            conn.execute("INSERT INTO players(id,name,avatar_hue) VALUES(?,?,?)", (pid, name.strip(), hue))
            row = conn.execute("SELECT * FROM players WHERE id=?", (pid,)).fetchone()
        return Database.player(row)

    @application.patch("/api/admin/players/{player_id}")
    async def edit_player(player_id: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        name = body.get("name")
        hue = body.get("avatar_hue")
        if not body or any(key not in ("name", "avatar_hue") for key in body):
            _detail(422, "Modifica non valida.")
        if "name" in body and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 80):
            _detail(422, "Il nome del giocatore è obbligatorio e deve contenere al massimo 80 caratteri.")
        if "avatar_hue" in body and (type(hue) is not int or not 0 <= hue <= 360):
            _detail(422, "Il colore deve essere compreso tra 0 e 360.")
        with MUTATION_LOCK, db.transaction() as conn:
            row = conn.execute("SELECT * FROM players WHERE id=? AND active=1", (player_id,)).fetchone()
            if not row: _detail(404, "Giocatore non trovato.")
            conn.execute("UPDATE players SET name=?,avatar_hue=?,avatar_color=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (name.strip() if "name" in body else row["name"], hue if "avatar_hue" in body else row["avatar_hue"], None if "avatar_hue" in body else row["avatar_color"], player_id))
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

    async def save_player_photo(player_id: str, file: UploadFile):
        raw = await file.read(MAX_PHOTO + 1)
        await file.close()
        if len(raw) > MAX_PHOTO:
            _detail(413, "La foto non può superare 5 MiB.")
        if not raw:
            _detail(422, "Seleziona una foto JPEG, PNG o WebP valida.")
        def encode_photo():
            try:
                image = Image.open(io.BytesIO(raw))
                if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > MAX_PHOTO_PIXELS or max(image.size) > 6000:
                    _detail(422, "La foto deve essere JPEG, PNG o WebP con dimensioni contenute.")
                image.load()
                image = ImageOps.exif_transpose(image).convert("RGB")
                output = io.BytesIO(); image.save(output, format="JPEG", quality=88, optimize=True)
                return output.getvalue()
            except (UnidentifiedImageError, OSError, ValueError):
                _detail(422, "Il file caricato non è un'immagine valida.")
        async with photo_slots:
            encoded = await asyncio.to_thread(encode_photo)
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

    @application.post("/api/admin/players/{player_id}/photo")
    async def upload_photo(player_id: str, file: UploadFile = File(...), session=Depends(admin_write)):
        return await save_player_photo(player_id, file)

    @application.get('/api/player/profile')
    def get_player_profile(session=Depends(player_session)):
        with db.transaction() as conn:
            row = conn.execute('SELECT * FROM players WHERE id=? AND active=1', (session['player_id'],)).fetchone()
            if not row: _detail(404, 'Giocatore non trovato.')
            return Database.public_player(row)

    @application.patch('/api/player/profile')
    async def update_player_profile(request: Request, session=Depends(player_write)):
        body = await _json(request)
        color = body.get('avatar_color')
        if set(body) != {'avatar_color'} or not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            _detail(422, 'Inserisci un colore esadecimale valido, ad esempio #FF8800.')
        with MUTATION_LOCK, db.transaction() as conn:
            conn.execute('UPDATE players SET avatar_color=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND active=1', (color.lower(), session['player_id']))
            row = conn.execute('SELECT * FROM players WHERE id=? AND active=1', (session['player_id'],)).fetchone()
            if not row: _detail(404, 'Giocatore non trovato.')
            return Database.public_player(row)

    @application.post('/api/player/profile/photo')
    async def update_player_photo(file: UploadFile = File(...), session=Depends(player_write)):
        return await save_player_photo(session['player_id'], file)

    @application.delete('/api/player/profile/photo')
    def delete_player_photo(session=Depends(player_write)):
        with MUTATION_LOCK:
            with db.transaction() as conn:
                row = conn.execute('SELECT photo FROM players WHERE id=? AND active=1', (session['player_id'],)).fetchone()
                if not row: _detail(404, 'Giocatore non trovato.')
                conn.execute('UPDATE players SET photo=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?', (session['player_id'],))
            if row['photo']:
                (db.photos_dir / row['photo']).unlink(missing_ok=True)
        return {'ok': True}

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
                conn.execute("UPDATE result_proposals SET status='invalidated' WHERE tournament_id=?", (tid,))
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

    @application.post("/api/admin/tournaments/{tid}/seed-from-ranking")
    def seed_from_ranking(tid: str, session=Depends(admin_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] != "draft": _detail(409, "I seed possono essere aggiornati solo in bozza.")
            entrants = Database.get_entrants(conn, tid)
            ranks = {row['id']: index for index, row in enumerate(global_elo_from_db(conn))}
            ordered = sorted(entrants, key=lambda p: (ranks.get(p['id'], 10**9), p['seed'], p['id']))
            for index, player in enumerate(ordered, 1):
                conn.execute("UPDATE entrants SET seed=? WHERE tournament_id=? AND player_id=?", (index, tid, player['id']))
            cfg = dict(tournament["config"]); cfg["seeding"] = "manual"
            Database.audit(conn, tid, "seeds_from_global_ranking", {"player_ids": [p['id'] for p in ordered]})
            _bump(conn, tid, config=cfg)
            return {"entrants": Database.get_entrants(conn, tid)}

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
        if not allow_admin_result_override: _detail(403, "Il risultato deve essere registrato da un partecipante.")
        body = await _json(request)
        if set(body) - {"result", "p1_games", "p2_games"}: _detail(422, "Il corpo del risultato contiene campi non supportati.")
        if body.get("result") not in ("p1_win", "p2_win"): _detail(422, "Seleziona uno dei due vincitori.")
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
            before = elo_snapshot(conn, (found["p1"], found["p2"]))
            matches[matches.index(found)] = finalized
            _persist_match_rows(conn, tid, matches)
            _bump(conn, tid)
            Database.audit(conn, tid, "match_result_recorded", {"match_id": match_id, "round": found["round"], "result": finalized["result"]})
            _advance_completed_round(conn, tournament, entrants, matches)
            return {"id": match_id, **finalized, "status": Database.get_tournament(conn, tid)["status"], "elo_changes": elo_changes(conn, before)}

    @application.post("/api/admin/tournaments/{tid}/withdrawals")
    async def withdraw(tid: str, request: Request, session=Depends(admin_write)):
        if not allow_admin_result_override: _detail(403, "I ritiri automatici non sono disponibili. Il risultato deve essere registrato da un partecipante.")
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
            _advance_completed_round(conn, tournament, refreshed, matches)
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
                        match.pop("void", None)
                        match.pop("score_unknown", None)
                    if match["round"] >= target["round"] and match.get("result") is None and match.get("p2"):
                        left_out = active_withdrawals.get(match["p1"], 10**9) <= match["round"]
                        right_out = active_withdrawals.get(match["p2"], 10**9) <= match["round"]
                        if left_out ^ right_out:
                            match = {**match, "result": "void", "void": True}
                        elif left_out and right_out:
                            match = {**match, "result": "void", "p1_games": 0, "p2_games": 0,
                                     "p1_points": 0, "p2_points": 0, "p1_wins": 0, "p2_wins": 0,
                                     "p1_draws": 0, "p2_draws": 0, "p1_losses": 0, "p2_losses": 0}
                    kept.append(match)
            else:
                kept = [m for m in matches if m["round"] < target["round"]]
            for invalidated_match in invalidated:
                conn.execute("UPDATE result_proposals SET status='invalidated' WHERE tournament_id=? AND match_id=?", (tid, invalidated_match["id"]))
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
            conn.execute("UPDATE result_proposals SET status='invalidated' WHERE tournament_id=?", (tid,))
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

    @application.get("/api/admin/reset-data/preview")
    def reset_data_preview(session=Depends(admin_session)):
        with db.transaction() as conn:
            return {"tournaments": conn.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0],
                    "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
                    "matches": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
                    "photos": conn.execute("SELECT COUNT(*) FROM players WHERE photo IS NOT NULL").fetchone()[0]}

    @application.post("/api/admin/reset-data")
    async def reset_data(request: Request, session=Depends(admin_write)):
        body = await _json(request)
        scope = body.get("scope")
        if scope not in ("results", "tournaments", "all") or body.get("confirmation") != "ELIMINA":
            _detail(422, "Scegli i dati e scrivi ELIMINA per confermare.")
        failures = []
        with MUTATION_LOCK:
            with db.transaction() as conn:
                photos = [row[0] for row in conn.execute("SELECT photo FROM players WHERE photo IS NOT NULL")] if scope == "all" else []
                if scope == "all" and db.photos_dir.exists():
                    # Include orphaned files owned by the app, but never arbitrary paths.
                    photos = sorted(set(photos) | {p.name for p in db.photos_dir.iterdir() if re.fullmatch(r"[a-f0-9]{32}\.jpg", p.name)})
                if scope == "results":
                    conn.execute("DELETE FROM result_proposals")
                    conn.execute("DELETE FROM team_results")
                    conn.execute("DELETE FROM matches")
                    conn.execute("DELETE FROM withdrawals")
                    conn.execute("UPDATE entrants SET withdrawn=0")
                    conn.execute("DELETE FROM audit WHERE action IN ('match_result_recorded','friendly_result_recorded','team_friendly_result_recorded','match_rollback','tournament_completed','player_withdrew')")
                    conn.execute("UPDATE tournaments SET status='draft', archived_from=NULL, frozen_config_json=NULL, champion_id=NULL, warnings_json='[]', revision=revision+1, updated_at=CURRENT_TIMESTAMP")
                else:
                    conn.execute("DELETE FROM tournaments")
                    if scope == "all":
                        conn.execute("DELETE FROM result_proposals")
                        conn.execute("DELETE FROM team_results")
                        conn.execute("DELETE FROM players")
            for name in photos:
                if not re.fullmatch(r"[a-f0-9]{32}\.jpg", name):
                    failures.append(name); continue
                try:
                    (db.photos_dir / name).unlink(missing_ok=True)
                except OSError:
                    failures.append(name)
        return {"ok": True, "scope": scope, "photos_not_deleted": len(failures)}

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

    @application.get("/api/public/history")
    def public_history(start: datetime, end: datetime):
        if not start.tzinfo or not end.tzinfo or not timedelta(0) < end - start <= timedelta(days=32):
            _detail(422, "Intervallo del calendario non valido.")
        bounds = [v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") for v in (start, end)]
        with db.transaction() as conn:
            return month_matches(conn, *bounds)

    @application.get('/api/admin/friendlies')
    def admin_friendlies(start: datetime, end: datetime, session=Depends(admin_session)):
        with db.transaction() as conn:
            if start.tzinfo is None or end.tzinfo is None or end <= start or end - start > timedelta(days=32): _detail(422, 'Intervallo non valido.')
            bounds = tuple(d.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S') for d in (start, end))
            names = {r['id']: r['name'] for r in conn.execute('SELECT id,name FROM players')}
            games = []
            for row in conn.execute("SELECT * FROM result_proposals WHERE tournament_id IS NULL AND status IN ('confirmed','pending') AND created_at>=? AND created_at<?", bounds):
                games.append({'id':row['id'], 'kind':'single', 'status':row['status'], 'at':row['created_at'].replace(' ','T')+'Z',
                    'sides':[[{'id':pid,'name':names.get(pid,'Giocatore')}] for pid in (row['proposer_id'],row['opponent_id'])],
                    'winner': 'a' if row['winner_id']==row['proposer_id'] else 'b'})
            for row in conn.execute("SELECT * FROM team_results WHERE status IN ('confirmed','pending') AND created_at>=? AND created_at<?", bounds):
                members = list(conn.execute('SELECT player_id,team FROM team_result_players WHERE result_id=? ORDER BY player_id',(row['id'],)))
                games.append({'id':row['id'], 'kind':'team', 'status':row['status'], 'at':row['created_at'].replace(' ','T')+'Z',
                    'sides':[[{'id':m['player_id'],'name':names.get(m['player_id'],'Giocatore')} for m in members if m['team']==side] for side in ('a','b')], 'winner':row['winning_team']})
            return sorted(games, key=lambda g:(g['at'],g['id']), reverse=True)

    @application.patch('/api/admin/friendlies/{kind}/{result_id}')
    async def admin_edit_friendly(kind: str, result_id: str, request: Request, session=Depends(admin_write)):
        body = await _json(request)
        try:
            stamp = datetime.fromisoformat(body['at'].replace('Z','+00:00'))
            if stamp.tzinfo is None:
                raise ValueError()
            stamp = stamp.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            _detail(422, 'Data e ora non valide.')
        if kind not in ('single','team'):
            _detail(404, 'Amichevole non trovata.')
        with MUTATION_LOCK, db.transaction() as conn:
            if kind == 'single':
                row = conn.execute("SELECT * FROM result_proposals WHERE id=? AND tournament_id IS NULL AND status='confirmed'",(result_id,)).fetchone()
                if not row: _detail(404, 'Amichevole non trovata.')
                winner = body.get('winner_id')
                if winner not in (row['proposer_id'],row['opponent_id']): _detail(422, 'Vincitore non valido.')
                conn.execute('UPDATE result_proposals SET created_at=?,winner_id=? WHERE id=?',(stamp,winner,result_id))
            else:
                row = conn.execute("SELECT * FROM team_results WHERE id=? AND status='confirmed'",(result_id,)).fetchone()
                if not row: _detail(404, 'Amichevole non trovata.')
                winner = body.get('winner')
                if winner not in ('a','b'): _detail(422, 'Squadra vincitrice non valida.')
                conn.execute('UPDATE team_results SET created_at=?,winning_team=? WHERE id=?',(stamp,winner,result_id))
            Database.audit(conn,None,'friendly_admin_updated',{'match_id':result_id,'kind':kind})
        return {'ok':True}

    @application.delete('/api/admin/friendlies/{kind}/{result_id}')
    def admin_delete_friendly(kind: str, result_id: str, session=Depends(admin_write)):
        if kind not in ('single','team'): _detail(404, 'Amichevole non trovata.')
        with MUTATION_LOCK, db.transaction() as conn:
            if kind == 'single':
                deleted = conn.execute("DELETE FROM result_proposals WHERE id=? AND tournament_id IS NULL AND status IN ('confirmed','pending')",(result_id,)).rowcount
            else:
                deleted = conn.execute("DELETE FROM team_results WHERE id=? AND status IN ('confirmed','pending')",(result_id,)).rowcount
            if not deleted: _detail(404, 'Amichevole non trovata.')
            Database.audit(conn,None,'friendly_admin_deleted',{'match_id':result_id,'kind':kind})
        return {'ok':True}

    def ranking_player(row, rank):
        return {"rank": rank, "id": row["id"], "name": row["name"], "rating": row["rating"],
                "matches": row["matches"], "wins": row["wins"], "draws": row["draws"], "losses": row["losses"],
                "photo_url": f"/photos/{row['photo']}" if row.get("photo") else None,
                "avatar_hue": row["avatar_hue"], "avatar_color": row["avatar_color"]}

    @application.get("/api/public/ranking")
    def public_ranking():
        with db.transaction() as conn:
            rows = global_elo_from_db(conn, include_movement=True)
            return [{**ranking_player(row, rank), "movement": row["movement"]}
                    for rank, row in enumerate(rows, 1)]

    @application.get("/api/public/players/{player_id}/statistics")
    def public_player_statistics(player_id: str):
        with db.transaction() as conn:
            stats = player_statistics(conn, player_id)
            if stats is None:
                _detail(404, "Profilo non disponibile.")
            stats["player"] = ranking_player(stats["player"], stats["player"]["rank"])
            return stats

    def reveal_state(conn, tid, matches, pid):
        pending = [m['round'] for m in matches if not m.get('result') and not m.get('void')]
        if not matches: return None, True
        current = min(pending) if pending else max(m['round'] for m in matches)
        round_matches = [m for m in matches if m['round'] == current]
        data = [[m['id'], m.get('p1'), m.get('p2')] for m in round_matches]
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        row = conn.execute('SELECT fingerprint FROM round_reveals WHERE tournament_id=? AND player_id=? AND round_no=?', (tid,pid,current)).fetchone() if pid else None
        return current, bool(row and row['fingerprint'] == fingerprint)

    @application.post('/api/player/tournaments/{tid}/reveal')
    def reveal_round(tid: str, session=Depends(player_write)):
        with MUTATION_LOCK, db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament['status'] not in ('active', 'completed'): _detail(409, 'Nessun turno disponibile.')
            matches = Database.get_matches(conn, tid)
            current, _ = reveal_state(conn, tid, matches, session['player_id'])
            if current is None: _detail(409, 'Nessun incontro disponibile.')
            match = next((m for m in matches if m['round']==current and session['player_id'] in (m.get('p1'),m.get('p2'))),None)
            round_matches = [m for m in matches if m['round']==current]
            data = [[m['id'], m.get('p1'), m.get('p2')] for m in round_matches]
            fingerprint = hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
            conn.execute('INSERT OR REPLACE INTO round_reveals VALUES(?,?,?,?)',(tid,session['player_id'],current,fingerprint))
            other = (match.get('p2') if match.get('p1')==session['player_id'] else match.get('p1')) if match else None
            person = conn.execute('SELECT * FROM players WHERE id=?',(other,)).fetchone() if other else None
            return {'round':current,'opponent':Database.public_player(person) if person else None,'bye':bool(match and match.get('bye')),'unpaired':match is None}

    @application.get("/api/public/tournaments")
    def public_tournaments(request: Request):
        with db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tournaments WHERE status IN ('active','completed') ORDER BY created_at DESC,id").fetchall()
            token_hash = hashlib.sha256(request.cookies.get('mana_player_session', '').encode()).hexdigest()
            active = conn.execute("SELECT player_id FROM player_sessions WHERE token_hash=? AND expires_at>strftime('%s','now')", (token_hash,)).fetchone()
            result = []
            player_id = active['player_id'] if active else None
            for row in rows:
                matches = Database.get_matches(conn, row['id'])
                current, revealed = reveal_state(conn, row['id'], matches, player_id)
                personal = None
                if player_id and current is not None and row['status'] == 'active':
                    match = next((m for m in matches if m['round'] == current and player_id in (m.get('p1'), m.get('p2'))), None)
                    if match:
                        personal = {'round': current}
                        if revealed:
                            opponent = match.get('p2') if match.get('p1') == player_id else match.get('p1')
                            personal.update(
                                table=match.get('table'), opponent_id=opponent, bye=bool(match.get('bye')),
                                completed=bool(match.get('result') or match.get('void')),
                                match_id=match['id'],
                            )
                result.append({"id": row["id"], "config": json.loads(row["frozen_config_json"] or row["config_json"]),
                               "status": row["status"], "champion_id": None, "my_match": personal,
                               "reveal_locked": current is not None and not revealed, "reveal_available": current is not None})
            return result

    def public_detail(tid, request: Request):
        with db.transaction() as conn:
            tournament = _row_tournament(conn, tid)
            if tournament["status"] not in ("active", "completed"):
                _detail(404, "Torneo non trovato.")
            entrants = Database.get_entrants(conn, tid)
            matches = Database.get_matches(conn, tid)
            cfg = tournament["frozen_config"] or tournament["config"]
            token = request.cookies.get('mana_player_session', '')
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            active = conn.execute("SELECT player_id FROM player_sessions WHERE token_hash=? AND expires_at>strftime('%s','now')", (token_hash,)).fetchone()
            current, revealed = reveal_state(conn, tid, matches, active['player_id'] if active else None)
            shown = matches if current is None else [m for m in matches if m['round'] < current or (revealed and m['round'] == current)]
            public_entrants = [{"id": p["id"], "name": p["name"], "photo_url": p["photo_url"], "avatar_hue": p["avatar_hue"], "avatar_color": p["avatar_color"], "seed": p["seed"], "withdrawn": p["withdrawn"]} for p in entrants]
            return {"id": tid, "config": cfg, "status": tournament["status"], "entrants": public_entrants,
                    "matches": shown, "rounds": public_rounds(shown), "current_round": current, "reveal_available": current is not None, "reveal_locked": current is not None and not revealed, "standings": engine.calculate_standings(cfg, entrants, shown),
                    "champion_id": tournament["champion_id"] if revealed else None, "warnings": tournament["warnings"] if revealed else []}

    @application.get("/api/public/tournaments/{tid}")
    def public_tournament(tid: str, request: Request):
        return public_detail(tid, request)

    @application.get("/api/public/tournaments/{tid}/standings")
    def public_standings(tid: str, request: Request):
        return public_detail(tid, request)["standings"]

    @application.get("/api/public/tournaments/{tid}/rounds")
    def public_tournament_rounds(tid: str, request: Request):
        return public_detail(tid, request)["rounds"]

    return application


app = create_app()

