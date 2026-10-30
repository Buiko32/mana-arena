"""End-to-end persistence, workflow, and security contract for Mana Arena."""

from __future__ import annotations

import io
import json
import hashlib
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app


PASSWORD = "A-Long-Admin-Passphrase-42"
SECRET = "test-only-session-secret-that-is-at-least-32-bytes-long"


@pytest.fixture
def app_factory(tmp_path):
    def make(path=None):
        return create_app(
            data_dir=path or tmp_path,
            admin_username="organizer",
            admin_password=PASSWORD,
            session_secret=SECRET,
            cookie_secure=False,
            login_limit=3,
        )

    return make


@pytest.fixture
def client(app_factory):
    with TestClient(app_factory()) as value:
        yield value


def login(client, password=PASSWORD):
    response = client.post("/api/admin/login", json={"username": "organizer", "password": password})
    return response


def admin_headers(client):
    response = login(client)
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def player(client, headers, name):
    response = client.post("/api/admin/players", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def config(name="Serata Mana", **overrides):
    value = {
        "name": name,
        "date": "2026-10-30",
        "description": "",
        "structure": "swiss",
        "game_format": "Modern",
        "match_length": 1,
        "rounds": 2,
        "loss_threshold": None,
        "league_mode": None,
        "seeding": "manual",
        "allow_draws": False,
        "points": {"win": 3, "draw": 1, "loss": 0, "bye": 3},
        "tiebreakers": ["omw", "wins", "game_diff"],
    }
    value.update(overrides)
    return value


def tournament_with_players(client, headers, count=2, cfg=None):
    people = [player(client, headers, f"Giocatore {i}") for i in range(count)]
    created = client.post("/api/admin/tournaments", json={"config": cfg or config()}, headers=headers)
    assert created.status_code == 201, created.text
    tid = created.json()["id"]
    added = client.post(
        f"/api/admin/tournaments/{tid}/entrants",
        json={"player_ids": [p["id"] for p in people]}, headers=headers,
    )
    assert added.status_code == 200, added.text
    return tid, people


def start(client, tid, headers):
    return client.post(f"/api/admin/tournaments/{tid}/start", json={}, headers=headers)


def test_initialization_reopen_persists_players_and_tournaments(app_factory):
    with TestClient(app_factory()) as first:
        headers = admin_headers(first)
        p = player(first, headers, "Persistente")
        created = first.post("/api/admin/tournaments", json={"config": config()}, headers=headers)
        assert created.status_code == 201
        tid = created.json()["id"]
    with TestClient(app_factory()) as reopened:
        headers = admin_headers(reopened)
        assert any(row["id"] == p["id"] for row in reopened.get("/api/admin/players", headers=headers).json())
        assert any(row["id"] == tid for row in reopened.get("/api/admin/tournaments", headers=headers).json())


def test_bootstrap_rejects_missing_or_placeholder_credentials(tmp_path):
    missing = create_app(data_dir=tmp_path / "missing", admin_username="organizer", admin_password=None, session_secret=SECRET)
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        with TestClient(missing):
            pass
    weak_secret = create_app(data_dir=tmp_path / "secret", admin_username="organizer", admin_password=PASSWORD, session_secret="short")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        with TestClient(weak_secret):
            pass


def test_health_public_list_detail_standings_and_rounds(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    headers = admin_headers(client)
    tid, _ = tournament_with_players(client, headers)
    assert client.get("/api/public/tournaments").json() == []
    assert client.get(f"/api/public/tournaments/{tid}").status_code == 404
    assert start(client, tid, headers).status_code == 200
    assert client.get("/api/public/tournaments").json()[0]["id"] == tid
    detail = client.get(f"/api/public/tournaments/{tid}").json()
    assert detail["status"] == "active"
    assert len(detail["entrants"]) == 2
    assert client.get(f"/api/public/tournaments/{tid}/standings").status_code == 200
    assert client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).status_code == 200
    assert client.get(f"/api/public/tournaments/{tid}/rounds").json()
    assert "audit" not in json.dumps(detail).lower()


def test_admin_bootstrap_login_logout_and_password_change(client):
    assert client.get("/api/admin/players").status_code == 401
    logged_in = login(client)
    assert logged_in.status_code == 200
    assert logged_in.json()["csrf_token"]
    assert "httponly" in logged_in.headers["set-cookie"].lower()
    assert "samesite=lax" in logged_in.headers["set-cookie"].lower()
    headers = {"X-CSRF-Token": logged_in.json()["csrf_token"]}
    changed = client.post("/api/admin/password", json={"current_password": PASSWORD, "new_password": "Another-Long-Password-53"}, headers=headers)
    assert changed.status_code == 200
    assert client.get("/api/admin/players").status_code == 401
    assert login(client, "Another-Long-Password-53").status_code == 200
    csrf = client.cookies.get("mana_csrf")
    assert client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/admin/players").status_code == 401


def test_public_read_only_and_admin_writes_require_session_and_csrf(client):
    assert client.post("/api/admin/players", json={"name": "Intruso"}).status_code == 401
    headers = admin_headers(client)
    assert client.post("/api/admin/players", json={"name": "No CSRF"}).status_code == 403
    assert client.post("/api/admin/players", json={"name": "OK"}, headers=headers).status_code == 201
    assert client.patch("/api/public/tournaments/qualcosa", json={}).status_code in (404, 405)


def test_login_throttling_is_progressive_and_user_safe(client):
    for attempt in range(3):
        assert login(client, "wrong-password-value").status_code == 401
    limited = login(client, "wrong-password-value")
    assert limited.status_code == 429
    assert "organizer" not in limited.text.lower()


def test_player_crud_soft_delete_and_photo_validation(client):
    headers = admin_headers(client)
    created = player(client, headers, "Ada")
    assert client.patch(f"/api/admin/players/{created['id']}", json={"name": "Ada Lovelace"}, headers=headers).json()["name"] == "Ada Lovelace"
    image = Image.new("RGB", (24, 24), (210, 120, 10))
    data = io.BytesIO()
    image.save(data, format="PNG")
    uploaded = client.post(
        f"/api/admin/players/{created['id']}/photo",
        files={"file": ("profile.png", data.getvalue(), "image/png")}, headers=headers,
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["photo_url"].startswith("/photos/")
    bad = client.post(
        f"/api/admin/players/{created['id']}/photo",
        files={"file": ("profile.svg", b"<svg/>", "image/svg+xml")}, headers=headers,
    )
    assert bad.status_code == 422
    tid, _ = tournament_with_players(client, headers)
    assert client.post(f"/api/admin/tournaments/{tid}/entrants", json={"player_ids": [created["id"]]}, headers=headers).status_code == 200
    referenced = client.delete(f"/api/admin/players/{created['id']}", headers=headers)
    assert referenced.status_code == 409
    assert client.delete(f"/api/admin/players/{tid}-missing", headers=headers).status_code == 404
    unreferenced = player(client, headers, "Da rimuovere")
    assert client.delete(f"/api/admin/players/{unreferenced['id']}", headers=headers).status_code == 200
    assert all(row["id"] != unreferenced["id"] for row in client.get("/api/admin/players", headers=headers).json())


def test_draft_config_rejects_incompatible_settings(client):
    headers = admin_headers(client)
    invalid = client.post("/api/admin/tournaments", json={"config": config(allow_draws=True, structure="single_elim")}, headers=headers)
    assert invalid.status_code == 422
    assert client.get("/api/admin/tournaments", headers=headers).json() == []


def test_draft_tournament_create_and_edit(client):
    headers = admin_headers(client)
    created = client.post("/api/admin/tournaments", json={"config": config()}, headers=headers)
    assert created.status_code == 201
    tid = created.json()["id"]
    edited = client.put(f"/api/admin/tournaments/{tid}/config", json={"config": config("Nuovo nome")}, headers=headers)
    assert edited.status_code == 200
    assert edited.json()["config"]["name"] == "Nuovo nome"


def test_start_freezes_config_and_requires_confirmed_full_rollback(client):
    headers = admin_headers(client)
    tid, _ = tournament_with_players(client, headers)
    assert start(client, tid, headers).status_code == 200
    assert client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).status_code == 200
    blocked = client.put(f"/api/admin/tournaments/{tid}/config", json={"config": config("Modificato")}, headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["requires_confirmation"] is True
    assert blocked.json()["detail"]["affected_rounds"] == [1]
    rolled = client.put(f"/api/admin/tournaments/{tid}/config?confirm_rollback=true", json={"config": config("Modificato")}, headers=headers)
    assert rolled.status_code == 200
    assert rolled.json()["status"] == "draft"
    assert rolled.json()["config"]["name"] == "Modificato"


def test_result_and_round_persistence_and_duplicate_guard(client):
    headers = admin_headers(client)
    tid, people = tournament_with_players(client, headers, 4, config(rounds=2))
    assert start(client, tid, headers).status_code == 200
    round1 = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    assert len(round1["matches"]) == 2
    match = next(m for m in round1["matches"] if not m["bye"])
    result = client.put(
        f"/api/admin/tournaments/{tid}/matches/{match['id']}/result",
        json={"result": "p1_win", "p1_games": 1, "p2_games": 0}, headers=headers,
    )
    assert result.status_code == 200
    assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", json={"result": "p2_win", "p1_games": 0, "p2_games": 1}, headers=headers).status_code == 409
    other = next(m for m in round1["matches"] if m["id"] != match["id"])
    assert client.put(f"/api/admin/tournaments/{tid}/matches/{other['id']}/result", json={"result": "p1_win", "p1_games": 1, "p2_games": 0}, headers=headers).status_code == 200
    round2 = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers)
    assert round2.status_code == 200
    assert round2.json()["round"] == 2
    assert client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).status_code == 409
    persisted = client.get(f"/api/public/tournaments/{tid}/rounds").json()
    assert len(persisted) == 2


def test_withdrawal_audit_and_reset_preserve_audit(client):
    headers = admin_headers(client)
    tid, people = tournament_with_players(client, headers)
    assert start(client, tid, headers).status_code == 200
    response = client.post(f"/api/admin/tournaments/{tid}/withdrawals", json={"player_id": people[0]["id"]}, headers=headers)
    assert response.status_code == 200
    assert response.json()["entrant"]["withdrawn"] is True
    audit = client.get(f"/api/admin/tournaments/{tid}/audit", headers=headers)
    assert audit.status_code == 200 and len(audit.json()) >= 1
    reset = client.post(f"/api/admin/tournaments/{tid}/reset", json={"confirm": True}, headers=headers)
    assert reset.status_code == 200 and reset.json()["status"] == "draft"
    assert client.get(f"/api/admin/tournaments/{tid}/audit", headers=headers).json()


def test_rollback_preview_and_result_correction_invalidation(client):
    headers = admin_headers(client)
    tid, _ = tournament_with_players(client, headers, 4, config(rounds=2))
    start(client, tid, headers)
    round1 = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    for match in round1["matches"]:
        assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", json={"result": "p1_win", "p1_games": 1, "p2_games": 0}, headers=headers).status_code == 200
    round2 = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    match = next(m for m in round1["matches"] if not m["bye"])
    preview = client.post(f"/api/admin/tournaments/{tid}/rollback", json={"match_id": match["id"]}, headers=headers)
    assert preview.status_code == 409
    assert preview.json()["detail"]["requires_confirmation"] is True
    assert preview.json()["detail"]["affected_rounds"] == [1, 2]
    changed = client.post(f"/api/admin/tournaments/{tid}/rollback", json={"match_id": match["id"], "confirm": True}, headers=headers)
    assert changed.status_code == 200
    assert client.get(f"/api/public/tournaments/{tid}/rounds").json() == []


def test_csv_export_formula_safe_and_filename_controlled(client):
    headers = admin_headers(client)
    person = player(client, headers, "=HYPERLINK(\"bad\")")
    tid, _ = tournament_with_players(client, headers, 2)
    assert client.post(f"/api/admin/tournaments/{tid}/entrants", json={"player_ids": [person["id"]]}, headers=headers).status_code == 200
    start(client, tid, headers)
    exported = client.get(f"/api/admin/tournaments/{tid}/export.csv?kind=entrants", headers=headers)
    assert exported.status_code == 200
    assert 'attachment; filename="mana-arena-' in exported.headers["content-disposition"]
    assert "'=HYPERLINK" in exported.text


def test_backup_restore_includes_database_and_photos(client, tmp_path):
    headers = admin_headers(client)
    person = player(client, headers, "Backup")
    image = Image.new("RGB", (16, 16), (20, 30, 40))
    stream = io.BytesIO()
    image.save(stream, format="WEBP")
    uploaded = client.post(f"/api/admin/players/{person['id']}/photo", files={"file": ("p.webp", stream.getvalue(), "image/webp")}, headers=headers)
    assert uploaded.status_code == 200
    backup = client.get("/api/admin/backup", headers=headers)
    assert backup.status_code == 200
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["version"] == 1 and any(name.startswith("photos/") for name in archive.namelist())
    other = tmp_path / "restored"
    other.mkdir()
    app = create_app(data_dir=other, admin_username="organizer", admin_password=PASSWORD, session_secret=SECRET, cookie_secure=False)
    with TestClient(app) as restored:
        restored_headers = admin_headers(restored)
        response = restored.post("/api/admin/restore?confirm=true", files={"file": ("backup.zip", backup.content, "application/zip")}, headers=restored_headers)
        assert response.status_code == 200
        assert any(row["name"] == "Backup" for row in restored.get("/api/admin/players", headers=admin_headers(restored)).json())
        assert (other / "photos").exists()


def test_restore_rejects_traversal_and_requires_explicit_confirmation(client):
    headers = admin_headers(client)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("../escape", b"bad")
    no_confirmation = client.post("/api/admin/restore", files={"file": ("bad.zip", archive_bytes.getvalue(), "application/zip")}, headers=headers)
    assert no_confirmation.status_code == 422
    confirmed = client.post("/api/admin/restore?confirm=true", files={"file": ("bad.zip", archive_bytes.getvalue(), "application/zip")}, headers=headers)
    assert confirmed.status_code == 422


def test_league_rollback_preserves_round_robin_schedule(client):
    headers = admin_headers(client)
    cfg = config(structure="league", rounds=None, league_mode="single")
    tid, _ = tournament_with_players(client, headers, 4, cfg)
    started = start(client, tid, headers)
    calendar = started.json()["matches"]
    assert len(calendar) == 6
    for match in [row for row in calendar if row["round"] == 1]:
        assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", json={"result":"p1_win","p1_games":1,"p2_games":0}, headers=headers).status_code == 200
    player_id = started.json()["entrants"][0]["id"]
    withdrawn = client.post(f"/api/admin/tournaments/{tid}/withdrawals", json={"player_id":player_id}, headers=headers)
    assert withdrawn.status_code == 200 and withdrawn.json()["effective_round"] == 2
    dependent = next(row for row in calendar if row["round"] == 2)
    preview = client.post(f"/api/admin/tournaments/{tid}/rollback", json={"match_id":dependent["id"]}, headers=headers)
    assert preview.status_code == 409
    assert client.post(f"/api/admin/tournaments/{tid}/rollback", json={"match_id":dependent["id"],"confirm":True}, headers=headers).status_code == 200
    after = client.get(f"/api/admin/tournaments/{tid}", headers=headers).json()["matches"]
    assert len(after) == len(calendar)
    assert all(row["result"] == "p1_win" for row in after if row["round"] == 1)
    assert all(row.get("result") is None for row in after if row["round"] >= 2)
    state = client.get(f"/api/admin/tournaments/{tid}", headers=headers).json()
    assert next(row for row in state["entrants"] if row["id"] == player_id)["withdrawn"] is False


def test_rollback_restores_withdrawal_state_and_swiss_regeneration(client):
    headers = admin_headers(client)
    tid, people = tournament_with_players(client, headers, 4, config(rounds=3))
    start(client, tid, headers)
    first = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    for match in first["matches"]:
        assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", json={"result":"p1_win","p1_games":1,"p2_games":0}, headers=headers).status_code == 200
    second = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    withdrawn_id = second["matches"][0]["p1"]
    outcome = client.post(f"/api/admin/tournaments/{tid}/withdrawals", json={"player_id":withdrawn_id}, headers=headers)
    assert outcome.status_code == 200
    assert outcome.json()["effective_round"] == 2
    target = first["matches"][0]
    assert client.post(f"/api/admin/tournaments/{tid}/rollback", json={"match_id":target["id"],"confirm":True}, headers=headers).status_code == 200
    entrants = client.get(f"/api/admin/tournaments/{tid}", headers=headers).json()["entrants"]
    assert next(row for row in entrants if row["id"] == withdrawn_id)["withdrawn"] is False
    regenerated = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers)
    assert regenerated.status_code == 200 and regenerated.json()["round"] == 1
    assert any(withdrawn_id in (match["p1"],match["p2"]) for match in regenerated.json()["matches"])


def test_swiss_withdrawal_uses_next_round_when_no_round_is_open(client):
    headers = admin_headers(client)
    tid, people = tournament_with_players(client, headers, 4, config(rounds=3))
    start(client, tid, headers)
    first = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    for match in first["matches"]:
        assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", json={"result":"p1_win","p1_games":1,"p2_games":0}, headers=headers).status_code == 200
    response = client.post(f"/api/admin/tournaments/{tid}/withdrawals", json={"player_id":people[0]["id"]}, headers=headers)
    assert response.status_code == 200 and response.json()["effective_round"] == 2
    next_round = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).json()
    assert all(people[0]["id"] not in (match["p1"],match["p2"]) for match in next_round["matches"])


def _zip_with_database(database_bytes):
    members = {"db/mana-arena.sqlite3": database_bytes}
    manifest = {"version":1,"database":"db/mana-arena.sqlite3","files":[{"path":name,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()} for name,data in members.items()]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer,"w",compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json",json.dumps(manifest))
        for name,data in members.items(): archive.writestr(name,data)
    return buffer.getvalue()


def _incomplete_schema_backup(tmp_path):
    db_path = tmp_path / "incomplete.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        for table in ("admin","players","tournaments","entrants","matches","audit"):
            conn.execute(f"CREATE TABLE {table}(placeholder TEXT)")
        conn.execute("PRAGMA user_version=2")
        conn.commit()
    finally:
        conn.close()
    return _zip_with_database(db_path.read_bytes())


def test_restore_rejects_integral_but_incomplete_schema_and_preserves_session(client, tmp_path):
    headers = admin_headers(client)
    player(client, headers, "Dato da preservare")
    bad_archive = _incomplete_schema_backup(tmp_path)
    response = client.post("/api/admin/restore?confirm=true", files={"file":("invalid-schema.zip",bad_archive,"application/zip")}, headers=headers)
    assert response.status_code == 422
    assert client.get("/api/admin/session").status_code == 200
    assert any(row["name"] == "Dato da preservare" for row in client.get("/api/admin/players",headers=headers).json())


def test_restore_rolls_back_files_if_post_validation_activation_fails(client, monkeypatch):
    import app.main as main_module
    headers = admin_headers(client)
    player(client, headers, "Dati precedenti")
    snapshot = client.get("/api/admin/backup").content
    player(client, headers, "Solo nel DB attuale")
    def fail_initialize():
        raise RuntimeError("errore attivazione staging simulato")
    monkeypatch.setattr(client.app.state.db, "initialize", fail_initialize)
    response = client.post("/api/admin/restore?confirm=true", files={"file":("restore.zip",snapshot,"application/zip")}, headers=headers)
    assert response.status_code == 422
    assert client.get("/api/admin/session").status_code == 200
    names = {row["name"] for row in client.get("/api/admin/players",headers=headers).json()}
    assert {"Dati precedenti","Solo nel DB attuale"}.issubset(names)


def test_archive_reopen_restores_completed_state_without_champion(client):
    headers = admin_headers(client)
    cfg = config(structure="single_elim", rounds=None, loss_threshold=None)
    tid, people = tournament_with_players(client, headers, 4, cfg)
    start(client, tid, headers)
    first = client.get(f"/api/admin/tournaments/{tid}", headers=headers).json()["matches"]
    assert len(first) == 2
    for person in people:
        assert client.post(f"/api/admin/tournaments/{tid}/withdrawals",json={"player_id":person["id"]},headers=headers).status_code == 200
    second = client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers)
    assert second.status_code == 200
    completed = client.get(f"/api/admin/tournaments/{tid}",headers=headers).json()
    assert completed["status"] == "completed" and completed["champion_id"] is None
    archived = client.post(f"/api/admin/tournaments/{tid}/archive",headers=headers)
    assert archived.status_code == 200 and archived.json()["status"] == "archived"
    reopened = client.post(f"/api/admin/tournaments/{tid}/reopen",headers=headers)
    assert reopened.status_code == 200 and reopened.json()["status"] == "completed"


def test_completed_and_archived_config_requires_explicit_reopen_then_rollback(client):
    headers = admin_headers(client)
    tid, _ = tournament_with_players(client, headers, 2, config(rounds=1))
    start(client, tid, headers)
    round1 = client.post(f"/api/admin/tournaments/{tid}/rounds/next",json={},headers=headers).json()
    match=round1["matches"][0]
    assert client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result",json={"result":"p1_win","p1_games":1,"p2_games":0},headers=headers).status_code == 200
    assert client.get(f"/api/admin/tournaments/{tid}",headers=headers).json()["status"] == "completed"
    changed = {"config":config("Modificato",rounds=1)}
    assert client.post(f"/api/admin/tournaments/{tid}/archive",headers=headers).json()["status"] == "archived"
    direct = client.put(f"/api/admin/tournaments/{tid}/config?confirm_rollback=true",json=changed,headers=headers)
    assert direct.status_code == 409 and direct.json()["detail"]["requires_reopen"] is True
    assert client.post(f"/api/admin/tournaments/{tid}/reopen",headers=headers).json()["status"] == "completed"
    archived_confirm = client.put(f"/api/admin/tournaments/{tid}/config?confirm_rollback=true",json=changed,headers=headers)
    assert archived_confirm.status_code == 409 and archived_confirm.json()["detail"]["requires_reopen"] is True
    assert client.post(f"/api/admin/tournaments/{tid}/reopen",headers=headers).json()["status"] == "active"
    assert client.put(f"/api/admin/tournaments/{tid}/config",json=changed,headers=headers).status_code == 409
    assert client.put(f"/api/admin/tournaments/{tid}/config?confirm_rollback=true",json=changed,headers=headers).status_code == 200

