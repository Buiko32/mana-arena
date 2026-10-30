#!/usr/bin/env python3
"""Exercise the built image using Docker volumes and the public HTTP API."""

from __future__ import annotations

import argparse
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


def exercise_api(base: str, expected_player: str | None) -> str:
    # A request with no session must not mutate the player list.
    status, _ = request(base, "/api/admin/players", method="POST", body={"name": "Anonymous forbidden"})
    if status not in (401, 403):
        raise RuntimeError(f"anonymous player creation was not denied (HTTP {status})")

    opener, csrf = login(base)
    if expected_player is None:
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
        return str(player["id"])

    status, players = request(base, "/api/admin/players", opener=opener)
    if status != 200 or not isinstance(players, list):
        raise RuntimeError(f"admin player list failed: HTTP {status}: {players}")
    if not any(player.get("id") == expected_player and player.get("name") == "CI Persisted Player" for player in players):
        raise RuntimeError("created player was not present after container restart")
    return expected_player


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    suffix = uuid.uuid4().hex[:10]
    container = f"mana-arena-smoke-{suffix}"
    db_volume = f"mana-arena-smoke-db-{suffix}"
    photos_volume = f"mana-arena-smoke-photos-{suffix}"
    host_port = ""
    try:
        docker("volume", "create", db_volume)
        docker("volume", "create", photos_volume)
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
            args.image,
        )
        if not run.stdout.strip():
            raise RuntimeError("docker run did not return a container id")
        port_result = docker("port", container, "3388/tcp")
        host_port = port_result.stdout.strip().rsplit(":", 1)[-1]
        base = f"http://127.0.0.1:{host_port}"

        wait_for_health(base, container)
        player_id = exercise_api(base, None)
        docker("restart", container)
        wait_for_health(base, container)
        exercise_api(base, player_id)
        print("Container smoke passed: health, anonymous write denied, and player persisted across restart.")
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
