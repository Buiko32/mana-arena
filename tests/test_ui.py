"""Serve and inspect the Italian, same-origin tournament interface."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_root_and_static_assets_are_served_as_local_web_assets(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        assert 'lang="it"' in page.text
        assert 'name="viewport"' in page.text
        for path, mime in (("/static/style.css", "text/css"), ("/static/app.js", "javascript")):
            asset = client.get(path)
            assert asset.status_code == 200
            assert mime in asset.headers["content-type"]


def test_public_and_admin_entry_points_are_labeled_and_keyboard_friendly(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/").text
    for marker in ('id="public-view"', 'id="admin-view"', 'aria-label="Navigazione principale"', 'id="login-form"', 'id="tournament-form"', 'id="players-list"'):
        assert marker in page
    assert "prefers-reduced-motion" in client.get("/static/style.css").text
    assert "visibilitychange" in client.get("/static/app.js").text
    assert "document.visibilityState" in client.get("/static/app.js").text


def test_static_interface_uses_only_local_assets_and_has_admin_workflow_controls(tmp_path):
    app = create_app(data_dir=tmp_path, admin_username="admin", admin_password="Valid-admin-passphrase-42", session_secret="ui-test-session-secret-at-least-32-bytes")
    with TestClient(app) as client:
        page = client.get("/").text
        js = client.get("/static/app.js").text
        css = client.get("/static/style.css").text
    assert "https://" not in page + js + css
    for marker in ("/api/public/tournaments", "/api/admin/players", "/api/admin/tournaments", "/api/admin/backup", "X-CSRF-Token", "createObjectURL"):
        assert marker in js
    for marker in ("result", "withdrawals", "rollback", "reset", "archive", "reopen", "audit", "export.csv", "restore", "password"):
        assert marker in js
    assert "bracket_slot" in js
    assert "single_elim" in js
