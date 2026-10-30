# Mana Arena Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Mana Arena, an Italian web app for running customizable 1v1 Magic tournaments on a UGREEN DXP2800 NAS.

**Architecture:** One Python FastAPI process serves a responsive static HTML/CSS/JS frontend and a SQLite-backed API. Tournament rules live in a pure Python engine; the service persists tournaments, players, matches, audit events and photos. Docker Compose pulls the public Linux amd64 image from GHCR.

**Tech Stack:** Python, FastAPI, SQLite, HTML, CSS, JavaScript, pytest, Docker Compose, GitHub Actions, GitHub Container Registry.

**Spec:** `docs/superpowers/specs/2026-10-30-magic-tornei-design.md`

## Global Constraints

- One FastAPI service and SQLite, with writes serialized through transactions.
- Interface and user-facing validation messages are in Italian; public endpoints are read-only.
- Tournament structures: `swiss`, `adapted`, `league`, `single_elim`; every match is 1v1.
- Magic labels are independent from structure: Standard, Modern, Commander, Pauper, or custom text; Commander multiplayer is out of scope.
- Match length is BO1 or BO3. Adapted Swiss defaults to BO1 and permits BO3.
- A tournament has 2–64 entrants; Swiss rounds are configurable from 1–32; adapted elimination threshold is configurable from 2–5.
- Win, draw, loss, and bye points are configurable from 0–10; win must exceed loss and an enabled draw score must lie between them. Preset “Come Excel” is adapted, BO1, threshold 2, win 1, draw disabled, loss 0, bye 1.
- Tiebreak order is points, selected order of OMW/wins/game_diff, and seed as the final criterion in every configuration.
- Seeding is manual or random; a tournament’s rule configuration is frozen at start and changes after start require an explicit full rollback.
- One admin is bootstrapped from environment credentials; no public registration. Admin changes require authentication, CSRF protection, and rate limiting.
- Container target is Linux amd64, app/container port 3388, persistent mounts `/data/db` and `/data/photos`.
- Public repository is `Buiko32/mana-arena`; public image is `ghcr.io/buiko32/mana-arena:latest`.
- Compose pulls the published image with `image:` and contains no local `build:` instruction.
- Do not commit secrets, real player data, or player photos.

## Review Focus

1. **Invalid or incompatible tournament settings:** the service must reject unknown structures, out-of-range values, duplicate tiebreakers, and draw settings incompatible with a winner-required format. Pin in `tests/test_engine.py::test_validate_config_rejects_incompatible_settings` and `tests/test_api.py::test_draft_config_rejects_incompatible_settings`.
2. **Odd Swiss field and repeated bye pressure:** every round must have legal pairings; a bye does not create a played win or loss, and an eligible player with fewer previous byes is preferred. Pin in `tests/test_engine.py::test_odd_field_bye_is_fair_and_not_a_played_win`.
3. **No legal no-rematch pairing:** the engine must return a complete legal fallback or a visible warning when a rematch is unavoidable; it must never silently omit an entrant. Pin in `tests/test_engine.py::test_pairing_reports_unavoidable_rematch`.
4. **Unauthorized or forged write:** public GETs remain available, but missing/invalid session, missing CSRF token, and rate-limited login cannot change data. Pin in `tests/test_api.py::test_public_read_only_and_admin_writes_require_session_and_csrf`.
5. **Changing rules after rounds exist:** a config edit must fail unless a full rollback is explicitly confirmed; confirmed rollback must invalidate dependent matches and write an audit event. Pin in `tests/test_api.py::test_active_config_change_requires_confirmed_full_rollback`.

---

## File Map

- `app/engine.py`: pure tournament configuration validation, schedules, pairings, match scoring and standings.
- `app/db.py`: SQLite schema, initialization, transactions, repositories, audit events, exports, backup/restore primitives.
- `app/auth.py`: password hashing, login throttling, signed session and CSRF helpers.
- `app/main.py`: FastAPI app, public/admin routes, static serving and request validation.
- `app/static/index.html`, `style.css`, `app.js`: Italian responsive single-page interface.
- `tests/test_engine.py`, `tests/test_api.py`, `tests/test_ui.py`, `tests/test_packaging.py`: unit, API, UI smoke and deployment contract tests.
- `Dockerfile`, `docker-compose.yaml`, `.env.example`, `.github/workflows/release.yml`, `README.md`: amd64 container, NAS deployment, secrets template, GHCR publishing and operations guide.

## Shared Engine Interfaces

Use plain JSON-compatible dictionaries at the engine boundary. Keep IDs as strings; do not let SQLite rows or FastAPI request objects leak into the engine.

`config` keys:

```python
{
    "name": str,
    "date": str | None,                 # ISO date or null
    "description": str,
    "structure": "swiss" | "adapted" | "league" | "single_elim",
    "game_format": str,                 # named format or custom label
    "match_length": 1 | 3,              # BO1 or BO3
    "rounds": int | None,                # Swiss: 1..32
    "loss_threshold": int | None,       # adapted: 2..5
    "league_mode": "single" | "double" | None,
    "seeding": "manual" | "random",
    "random_seed": int | None,           # internal: generated/stored by server for random seeding
    "allow_draws": bool,
    "points": {"win": int, "draw": int, "loss": int, "bye": int},
    "tiebreakers": list[str],           # ordered subset of omw/wins/game_diff
}
```

Seed is always appended as final tie-break. Entrants are `[{"id": str, "seed": int | None}]`. Completed match records use `{"round": int, "table": int, "p1": str, "p2": str | None, "bye": bool, "result": "p1_win" | "draw" | "p2_win" | "p1_forfeit" | "p2_forfeit" | None, "p1_games": int, "p2_games": int}`. A bye is represented with `p2: None`, `bye: True`, and a finalized bye result; it is not a played win.

- `validate_config(config: dict) -> None`: raises `ValueError` with an Italian, user-safe explanation for invalid/incompatible configuration.
- `make_round(config: dict, entrants: list[dict], matches: list[dict], round_number: int) -> dict`: returns `{"matches": list[dict], "warnings": list[str]}`; every match has `round`, `table`, `p1`, `p2`, and `bye`.
- `apply_result(config: dict, match: dict, result: dict) -> dict`: validates an outcome and returns the finalized match record including per-player points, loss increments and game counts.
- `calculate_standings(config: dict, entrants: list[dict], matches: list[dict]) -> list[dict]`: returns ordered rows with `player_id`, `seed`, `points`, `wins`, `draws`, `losses`, `byes`, `game_diff`, `omw`, `rank`, and `eliminated`.
- `make_schedule(config: dict, entrants: list[dict]) -> dict`: creates the initial round-robin or single-elimination schedule; Swiss structures use `make_round` after each completed round.

Admin API uses JSON for configs/results and multipart form data for photos. Public/admin route names and response shapes are kept stable for the frontend task.

Before Task 1, initialize Git in this current project directory if it is not already a repository, use `main` as the default branch, and commit the approved spec and this plan. This avoids a new worktree and keeps subsequent task commits reviewable.

## Task 1: Tournament Engine

**Files:**
- Create: `app/__init__.py`
- Create: `app/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: no application/database imports.
- Produces: shared engine interfaces above for Tasks 2 and 3.

- [ ] **Step 1: Write failing config and score tests.** Add named tests for allowed presets/custom values, incompatible structures, score bounds 0–10, Swiss rounds 1–32, adapted threshold 2–5, BO1/BO3, and draw compatibility. Assert the preset “Come Excel” is BO1 adapted with threshold 2 and points `{win: 1, loss: 0, bye: 1}` with draws disabled.
- [ ] **Step 2: Run the focused tests and confirm failure.** Run `pytest tests/test_engine.py -q`; expected: collection or assertion failures because engine functions are not implemented.
- [ ] **Step 3: Implement `validate_config(config: dict) -> None` in `app/engine.py`.** Enforce exact ranges and cross-field compatibility; include deterministic validation order and Italian error messages.
- [ ] **Step 4: Run config tests and confirm pass.** Run `pytest tests/test_engine.py -q`; expected: all config tests pass.
- [ ] **Step 5: Write failing schedule/pairing tests.** Cover Swiss score grouping and no rematches when possible; an unavoidable rematch warning; odd entrant bye fairness/no played result; adapted threshold elimination and a final with a rematch if required; deterministic seed/random input; single/double round-robin; single-elimination byes.
- [ ] **Step 6: Run those tests and confirm failure.** Run `pytest tests/test_engine.py -q`; expected: failures for missing schedule, pairing, and standings functions.
- [ ] **Step 7: Implement `make_round`, `make_schedule`, `apply_result`, and `calculate_standings` in `app/engine.py`.** Use deterministic tie resolution from the supplied seed/order; report relaxed pairing constraints in `warnings`; account for configurable points, forfeits, byes, selected tiebreakers and final seed ordering.
- [ ] **Step 8: Run engine tests and confirm pass.** Run `pytest tests/test_engine.py -q`; expected: all engine tests pass, including all five engine edge groups in Review Focus.
- [ ] **Step 9: Commit the engine task.** Commit only `app/__init__.py`, `app/engine.py`, and `tests/test_engine.py` after the full engine suite passes.

## Task 2: SQLite, Authentication, and API

**Files:**
- Create: `app/db.py`
- Create: `app/auth.py`
- Create: `app/main.py`
- Create: `requirements.txt`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `validate_config`, `make_round`, `make_schedule`, `apply_result`, and `calculate_standings` from Task 1.
- Produces: `GET /healthz`; same-origin public API `GET /api/public/tournaments`, `GET /api/public/tournaments/{id}`, `GET /api/public/tournaments/{id}/standings`, `GET /api/public/tournaments/{id}/rounds`; authenticated admin API rooted at `/api/admin/` for login/logout, players/photos, tournament CRUD/config, start, round generation, match results, withdrawals, rollback, CSV export, backup, and restore.

- [ ] **Step 1: Write failing SQLite/API tests.** Add named tests for initialization/reopen persistence, public list/detail/standings/rounds, admin bootstrap/login/logout, unauthenticated write rejection, CSRF enforcement, login throttling, player/photo validation, draft tournament create/edit, start freezes config, result and round persistence, withdrawal/audit, confirmed rollback invalidation, CSV export, and consistent database-plus-photo backup/restore.
- [ ] **Step 2: Run focused tests and confirm failure.** Run `pytest tests/test_api.py -q`; expected: import or assertion failures before the app exists.
- [ ] **Step 3: Implement database primitives in `app/db.py`.** Create schema and explicit transaction helpers for admin, players, tournaments/config snapshot, entrants, rounds/matches, audit, and media metadata. Use SQLite backup API and package photos with a versioned manifest; never copy a live database file as a backup.
- [ ] **Step 4: Implement `app/auth.py`.** Bootstrap exactly one admin from required environment values; hash passwords with PBKDF2-HMAC-SHA256 and random salt; issue signed `HttpOnly`/`SameSite` session cookies; enforce CSRF for mutating routes and progressive login throttling. Do not provide a default password or registration endpoint.
- [ ] **Step 5: Implement API routes in `app/main.py`.** Public routes are GET-only. Admin routes verify session and CSRF, validate input, call the pure engine, commit each logical operation transactionally, and append audit events. Config updates after start fail unless the caller explicitly confirms full rollback; return the affected-round summary before confirmation.
- [ ] **Step 6: Run API tests and confirm pass.** Run `pytest tests/test_api.py -q`; expected: all persistence, security, rules, rollback, export, and backup tests pass.
- [ ] **Step 7: Commit the API task.** Commit only API/auth/database source and API tests after the focused suite passes.

## Task 3: Responsive Italian Web Interface

**Files:**
- Create: `app/static/index.html`
- Create: `app/static/style.css`
- Create: `app/static/app.js`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: public and admin JSON routes from Task 2; same-origin cookies and CSRF token flow.
- Produces: public tournament listing/details, responsive standings/rounds views, and admin workflows for login, players/photos, custom settings, registration/seeding, start, pairings/results, withdrawal, rollback, CSV and backup/restore.

- [ ] **Step 1: Write failing UI smoke tests.** Assert the app serves the Italian document and static assets, has labeled public navigation and admin forms, includes responsive viewport styles, supports `prefers-reduced-motion`, and polls public state only while the page is visible.
- [ ] **Step 2: Run UI tests and confirm failure.** Run `pytest tests/test_ui.py -q`; expected: failed route/asset assertions because frontend files are missing.
- [ ] **Step 3: Implement the public views in `index.html`, `style.css`, and `app.js`.** Show active/completed tournaments, format/config summary, participant photos, rounds, match results, byes and selected tiebreak standings; refresh public data every 5 seconds only when visible.
- [ ] **Step 4: Implement admin screens and flows.** Build custom tournament settings with “Come Excel” preset and validation; make configuration, draw compatibility, Swiss rounds, threshold, point values, league mode, seeding, and tiebreak order visible before start. Require a clear impact review and explicit confirmation for full rollback. Add accessible photo, CSV, backup and restore controls.
- [ ] **Step 5: Run UI tests and confirm pass.** Run `pytest tests/test_ui.py -q`; expected: all smoke/accessibility contract tests pass and UI uses only the documented API.
- [ ] **Step 6: Commit the UI task.** Commit only the static UI assets and UI tests after the smoke suite passes.

## Task 4: Docker, GHCR Publishing, and Operations Docs

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yaml`
- Create: `.env.example`
- Create: `.github/workflows/release.yml`
- Create: `README.md`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: `app.main:app`, health endpoint, app environment-variable names, and persistent paths from Tasks 1–3.
- Produces: public amd64 image `ghcr.io/buiko32/mana-arena:latest`; Compose pulls this image and maps host port 3388 to container port 3388 with `/data/db` and `/data/photos` persistent volumes.

- [ ] **Step 1: Write failing packaging contract tests.** Assert Compose has `image: ghcr.io/buiko32/mana-arena:latest`, port `3388:3388`, both persistent mounts, health check and no `build:`; assert `.env.example` has placeholders only; assert workflow targets amd64 and grants only required package permissions.
- [ ] **Step 2: Run packaging tests and confirm failure.** Run `pytest tests/test_packaging.py -q`; expected: missing-file or contract failures.
- [ ] **Step 3: Add a minimal amd64 Docker image.** Install only runtime requirements, run as an unprivileged user, expose 3388, mount persistent paths, and provide a health endpoint that checks process/database readiness without revealing secrets.
- [ ] **Step 4: Add Compose and environment template.** Compose uses `image:` only, pulls the published image, maps 3388:3388, persists database/photos, and never contains credentials. `.env.example` documents required first-admin values with unusable placeholders.
- [ ] **Step 5: Add GitHub Actions workflow.** On pushes to `main` and version tags, run `pytest`, build `linux/amd64`, and publish `ghcr.io/buiko32/mana-arena:latest` (plus immutable commit/version tags) using `GITHUB_TOKEN` with `contents: read` and `packages: write`. Document the one-time GHCR package visibility setting as public.
- [ ] **Step 6: Document deployment and recovery.** README covers initial admin setup, public read-only behavior, Compose pull/start/update, NAS path permissions, local-network access, externally managed HTTPS, health status, CSV, and coherent backup/restore of both database and photos. State that installation on the NAS is not verified until performed there.
- [ ] **Step 7: Run final checks.** Run `pytest -q` and `docker buildx build --platform linux/amd64 --load -t mana-arena:check .` when Docker is available; then run the container health check and `docker compose config`. Expected: tests pass, image reports healthy, Compose resolves the public image and persistent mounts. If Docker is unavailable, record the skipped runtime check without claiming it passed.
- [ ] **Step 8: Commit the deployment/docs task.** Commit packaging, workflow, README and deployment tests after all available checks pass.
- [ ] **Step 9: Create and publish the approved public GitHub repository.** Create `Buiko32/mana-arena` as public, push the source and workflow, and confirm GHCR package visibility is public. Then verify the published `latest` tag can be pulled by Compose without registry login.
