"""Regression coverage for the shared device access gate."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


ADMIN_PASSWORD = "A-Long-Admin-Passphrase-42"
SESSION_SECRET = "test-only-session-secret-that-is-at-least-32-bytes-long"
SHARED_PASSWORD = "Shared-access-passphrase-42"


def make_app(tmp_path, monkeypatch, password=SHARED_PASSWORD):
    monkeypatch.setenv("SHARED_ACCESS_PASSWORD", password)
    return create_app(
        data_dir=tmp_path,
        admin_username="organizer",
        admin_password=ADMIN_PASSWORD,
        session_secret=SESSION_SECRET,
        cookie_secure=False,
    )


def authenticate(client, password=SHARED_PASSWORD):
    response = client.post("/api/access/login", json={"password": password})
    assert response.status_code == 200, response.text
    assert response.cookies.get("mana_device")
    assert client.cookies.get("mana_device")
    return response


def test_shared_gate_blocks_pages_and_apis_until_device_login(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for path in ("/", "/profilo", "/segnavita"):
            response = client.get(path)
            assert response.status_code == 401, path
            assert "text/html" in response.headers["content-type"], path
        assert client.get("/api/public/tournaments").status_code == 401
        assert client.get("/api/admin/players").status_code == 401

        rejected = client.post("/api/access/login", json={"password": "wrong password"})
        assert rejected.status_code == 401
        authenticate(client)

        assert client.get("/").status_code == 200
        assert client.get("/segnavita").status_code == 200
        assert client.get("/api/public/tournaments").status_code == 200


def test_shared_login_cookie_flags_and_device_expiry(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_ACCESS_PASSWORD", SHARED_PASSWORD)
    app = create_app(
        data_dir=tmp_path,
        admin_username="organizer",
        admin_password=ADMIN_PASSWORD,
        session_secret=SESSION_SECRET,
        cookie_secure=True,
    )
    with TestClient(app, base_url="https://testserver") as client:
        response = authenticate(client)
        set_cookie = response.headers["set-cookie"].lower()
        assert "httponly" in set_cookie
        assert "secure" in set_cookie
        assert "samesite=lax" in set_cookie
        assert "max-age=7776000" in set_cookie

        assert client.get("/").status_code == 200
        with sqlite3.connect(app.state.access.path) as conn:
            conn.execute("UPDATE devices SET expires_at=0")
        assert client.get("/").status_code == 401


def test_shared_login_is_throttled_and_missing_password_fails_closed(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/api/access/login", json={"password": "incorrect"}).status_code == 401
        assert client.post("/api/access/login", json={"password": SHARED_PASSWORD}).status_code == 429

    monkeypatch.delenv("SHARED_ACCESS_PASSWORD", raising=False)
    missing = create_app(
        data_dir=tmp_path / "missing-password",
        admin_username="organizer",
        admin_password=ADMIN_PASSWORD,
        session_secret=SESSION_SECRET,
        cookie_secure=False,
    )
    with pytest.raises(RuntimeError, match="SHARED_ACCESS_PASSWORD"):
        with TestClient(missing):
            pass


def test_device_cookie_survives_app_restart_and_password_rotation_revokes_it(tmp_path, monkeypatch):
    first_app = make_app(tmp_path, monkeypatch)
    with TestClient(first_app) as first:
        authenticate(first)
        cookie = first.cookies.get("mana_device")
        assert cookie

    restarted_app = make_app(tmp_path, monkeypatch)
    with TestClient(restarted_app) as restarted:
        restarted.cookies.set("mana_device", cookie, domain="testserver.local", path="/")
        assert restarted.get("/").status_code == 200

    rotated_app = make_app(tmp_path, monkeypatch, "Rotated-access-passphrase-93")
    with TestClient(rotated_app) as rotated:
        rotated.cookies.set("mana_device", cookie, domain="testserver.local", path="/")
        assert rotated.get("/").status_code == 401
        authenticate(rotated, "Rotated-access-passphrase-93")
        assert rotated.get("/").status_code == 200


def test_admin_can_revoke_a_device(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as owner, TestClient(app) as device:
        device.headers["User-Agent"] = "revocation-target-device"
        authenticate(device)
        assert device.get("/").status_code == 200

        # Admin authentication is separate from the shared device gate.
        assert owner.get("/api/admin/devices").status_code == 401
        assert owner.post(
            "/api/admin/login",
            json={"username": "organizer", "password": ADMIN_PASSWORD},
        ).status_code == 401
        authenticate(owner)
        login = owner.post(
            "/api/admin/login",
            json={"username": "organizer", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200, login.text
        csrf = login.json()["csrf_token"]
        devices = owner.get("/api/admin/devices")
        assert devices.status_code == 200, devices.text
        target = next(row for row in devices.json() if row.get("label") == "revocation-target-device")
        device_id = target["id"]

        revoked = owner.delete(
            f"/api/admin/devices/{device_id}",
            headers={"X-CSRF-Token": csrf},
        )
        assert revoked.status_code == 200, revoked.text
        assert device.get("/").status_code == 401
