#!/usr/bin/env python3
"""Exercise the built image using Docker volumes and the public HTTP API."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


PASSWORD = "CI-only-admin-password-42"
SESSION_SECRET = "ci-only-session-secret-with-more-than-32-bytes"
PNG_FIXTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADUlEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["docker", *args], text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def request(base: str, path: str, *, opener=None, method: str = "GET", body=None, headers=None):
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Content-Type": "application/json"} if payload is not None else {}
    request_headers.update(headers or {})
    req = urllib.request.Request(base + path, data=payload, headers=request_headers, method=method)
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=5) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        return 0, str(error.reason)
    except OSError as error:
        # urllib can expose socket resets/timeouts directly while reading the response.
        # Return a retryable connection failure so wait_for_health can poll again.
        return 0, str(error)


def upload_photo(base: str, player_id: str, opener, csrf: str):
    boundary = f"ManaArenaSmoke{uuid.uuid4().hex}"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="smoke.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("ascii") + PNG_FIXTURE + f"\r\n--{boundary}--\r\n".encode("ascii")
    req = urllib.request.Request(
        base + f"/api/admin/players/{player_id}/photo",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-CSRF-Token": csrf},
        method="POST",
    )
    try:
        with opener.open(req, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        return 0, str(reason)


def read_public_photo(base: str, photo_url: str) -> bytes:
    req = urllib.request.Request(base + photo_url, method="GET")
    try:
        with urllib.request.build_opener().open(req, timeout=10) as response:
            photo = response.read()
            if response.status != 200 or not photo.startswith(b"\xff\xd8") or not photo.endswith(b"\xff\xd9"):
                raise RuntimeError("public player photo was not a complete JPEG")
            return photo
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"public photo request failed: HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"public photo request failed: {error.reason}") from error
    except OSError as error:
        raise RuntimeError(f"public photo request failed: {error}") from error


def wait_for_health(base: str, container: str) -> None:
    deadline = time.monotonic() + 90
    last = "not yet responding"
    while time.monotonic() < deadline:
        inspect = docker("inspect", "--format", "{{.State.Running}}", container, check=False)
        if inspect.returncode == 0 and inspect.stdout.strip() != "true":
            logs = docker("logs", container, check=False)
            raise RuntimeError(f"container exited before becoming healthy:\n{logs.stdout}\n{logs.stderr}")
        status, payload = request(base, "/healthz")
        if status == 200 and payload == {"status": "ok"}:
            return
        last = f"HTTP {status}: {payload}"
        time.sleep(2)
    logs = docker("logs", container, check=False)
    raise RuntimeError(f"container did not become healthy ({last}):\n{logs.stdout}\n{logs.stderr}")


def login(base: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, payload = request(
        base,
        "/api/admin/login",
        opener=opener,
        method="POST",
        body={"username": "ci-organizer", "password": PASSWORD},
    )
    if status != 200 or not isinstance(payload, dict) or not payload.get("csrf_token"):
        raise RuntimeError(f"admin login failed: HTTP {status}: {payload}")
    return opener, payload["csrf_token"]


def assert_anonymous_write_denied(base: str) -> None:
    # A request with no session must not mutate the player list.
    status, _ = request(base, "/api/admin/players", method="POST", body={"name": "Anonymous forbidden"})
    if status not in (401, 403):
        raise RuntimeError(f"anonymous player creation was not denied (HTTP {status})")


def create_player_and_photo(base: str) -> tuple[str, str, str]:
    assert_anonymous_write_denied(base)
    opener, csrf = login(base)
    status, player = request(
        base,
        "/api/admin/players",
        opener=opener,
        method="POST",
        body={"name": "CI Persisted Player"},
        headers={"X-CSRF-Token": csrf},
    )
    if status != 201 or not isinstance(player, dict) or not player.get("id"):
        raise RuntimeError(f"authenticated player creation failed: HTTP {status}: {player}")
    player_id = str(player["id"])

    status, upload = upload_photo(base, player_id, opener, csrf)
    if status != 200 or not isinstance(upload, dict) or not isinstance(upload.get("photo_url"), str):
        raise RuntimeError(f"authenticated photo upload failed: HTTP {status}: {upload}")
    photo_url = upload["photo_url"]
    if not photo_url.startswith("/photos/"):
        raise RuntimeError(f"photo upload returned an unexpected URL: {photo_url}")

    status, players = request(base, "/api/admin/players", opener=opener)
    if status != 200 or not isinstance(players, list):
        raise RuntimeError(f"admin player list failed: HTTP {status}: {players}")
    profile = next((row for row in players if row.get("id") == player_id), None)
    if not profile or profile.get("name") != "CI Persisted Player" or profile.get("photo_url") != photo_url:
        raise RuntimeError("created player profile did not retain the uploaded photo reference")
    photo = read_public_photo(base, photo_url)
    digest = hashlib.sha256(photo).hexdigest()
    return player_id, photo_url, digest


def verify_persisted_player_and_photo(base: str, player_id: str, photo_url: str, expected_digest: str) -> None:
    assert_anonymous_write_denied(base)
    opener, _ = login(base)
    status, players = request(base, "/api/admin/players", opener=opener)
    if status != 200 or not isinstance(players, list):
        raise RuntimeError(f"admin player list failed after restart: HTTP {status}: {players}")
    profile = next((row for row in players if row.get("id") == player_id), None)
    if not profile or profile.get("name") != "CI Persisted Player" or profile.get("photo_url") != photo_url:
        raise RuntimeError("player or photo reference did not persist across container restart")
    photo = read_public_photo(base, photo_url)
    if hashlib.sha256(photo).hexdigest() != expected_digest:
        raise RuntimeError("stored player photo content changed across container restart")


def start_container(image: str, container: str, db_volume: str, photos_volume: str) -> str:
    """Start a container and return the freshly published local HTTP endpoint."""
    run = docker(
        "run", "--detach", "--name", container,
        "--publish", "127.0.0.1::3388",
        "--env", "DATA_DIR=/data",
        "--env", "ADMIN_USERNAME=ci-organizer",
        "--env", f"ADMIN_PASSWORD={PASSWORD}",
        "--env", f"SESSION_SECRET={SESSION_SECRET}",
        "--env", "COOKIE_SECURE=false",
        "--volume", f"{db_volume}:/data/db",
        "--volume", f"{photos_volume}:/data/photos",
        image,
    )
    if not run.stdout.strip():
        raise RuntimeError("docker run did not return a container id")

    port_result = docker("port", container, "3388/tcp")
    mapping = port_result.stdout.strip().splitlines()
    if not mapping:
        raise RuntimeError(f"Docker did not publish container port 3388/tcp: {port_result.stderr}")
    host_port = mapping[0].rsplit(":", 1)[-1]
    if not host_port.isdigit():
        raise RuntimeError(f"Docker returned an invalid published port: {mapping[0]}")
    return f"http://127.0.0.1:{host_port}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:10]
    container = f"mana-arena-smoke-{suffix}"
    db_volume = f"mana-arena-smoke-db-{suffix}"
    photos_volume = f"mana-arena-smoke-photos-{suffix}"
    try:
        docker("volume", "create", db_volume)
        docker("volume", "create", photos_volume)

        base = start_container(args.image, container, db_volume, photos_volume)
        wait_for_health(base, container)
        player_id, photo_url, photo_digest = create_player_and_photo(base)

        # Recreate the container instead of restarting it in place. The named
        # volumes preserve the database and photos, while Docker allocates a
        # fresh ephemeral host port that must be queried again.
        docker("rm", "--force", container)
        base = start_container(args.image, container, db_volume, photos_volume)
        wait_for_health(base, container)
        verify_persisted_player_and_photo(base, player_id, photo_url, photo_digest)
        print("Container smoke passed: health, anonymous write denied, and player/photo persisted across restart.")
        return 0
    except Exception as error:
        print(f"Container smoke failed: {error}", file=sys.stderr)
        return 1
    finally:
        docker("rm", "--force", container, check=False)
        docker("volume", "rm", "--force", db_volume, check=False)
        docker("volume", "rm", "--force", photos_volume, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
