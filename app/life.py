"""Persistent, idempotent life-counter session records."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

router = APIRouter()
MAX_BODY = 1024 * 1024


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=160)


class Player(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    members: list[Member] = Field(min_length=1, max_length=6)
    initial_life: int = Field(ge=-100000, le=100000)
    final_life: int = Field(ge=-100000, le=100000)


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: datetime
    undo: bool = False
    player: str = Field(min_length=1, max_length=160)
    delta: int = Field(ge=-100000, le=100000)
    life: int = Field(ge=-100000, le=100000)


class ResultTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["friendly", "tournament"]
    proposer_id: str = Field(min_length=1, max_length=100)
    tournament_id: str | None = Field(default=None, max_length=100)
    match_id: str | None = Field(default=None, max_length=100)


class Game(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["duel", "team", "ffa"]
    session_id: UUID
    started_at: datetime
    ended_at: datetime
    starting_life: int = Field(ge=-100000, le=100000)
    players: list[Player] = Field(min_length=2, max_length=6)
    changes: list[Change] = Field(max_length=5000)
    winner: str | None = Field(default=None, max_length=160)
    result_target: ResultTarget | None = None


def _validate(game: Game) -> None:
    names = [p.name for p in game.players]
    if any(not n.strip() for n in names) or len(set(names)) != len(names):
        raise ValueError("I nomi dei lati devono essere distinti e non vuoti.")
    if any(not m.name.strip() for p in game.players for m in p.members):
        raise ValueError("I nomi dei giocatori non possono essere vuoti.")
    if game.format in ("duel", "team") and len(game.players) != 2:
        raise ValueError("Sono richiesti due lati.")
    if game.format != "team" and any(len(p.members) != 1 for p in game.players):
        raise ValueError("Ogni contatore deve appartenere a un giocatore.")
    profile_ids = [m.id for p in game.players for m in p.members if m.id is not None]
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("Un profilo può partecipare una sola volta.")
    if game.winner is not None and game.winner not in names:
        raise ValueError("Il vincitore deve essere uno dei lati.")
    if game.starting_life != game.players[0].initial_life:
        raise ValueError("La vita iniziale non corrisponde al primo lato.")
    if any(t.tzinfo is None or t.utcoffset() is None for t in [game.started_at, game.ended_at, *(c.at for c in game.changes)]):
        raise ValueError("Le date devono includere il fuso orario.")
    if game.ended_at < game.started_at:
        raise ValueError("La fine precede l'inizio.")
    totals = {p.name: p.initial_life for p in game.players}
    previous = game.started_at
    for change in game.changes:
        if change.player not in totals or change.at < previous or change.at > game.ended_at:
            raise ValueError("Cronologia non valida.")
        totals[change.player] += change.delta
        if totals[change.player] != change.life or not change.delta:
            raise ValueError("Variazione di vita incoerente.")
        previous = change.at
    if any(totals[p.name] != p.final_life for p in game.players):
        raise ValueError("Vita finale incoerente con la cronologia.")


def _path(data_dir: str | Path | None = None) -> Path:
    return Path(os.environ.get("LIFE_STATS_DIR") or (Path(data_dir or os.environ.get("DATA_DIR", "/data")) / "life")) / "life-stats.sqlite3"


def _statistics(game: Game):
    active = {p.name: [] for p in game.players}
    for change in game.changes:
        deltas = active[change.player]
        if change.undo:
            if not deltas or deltas.pop() != -change.delta:
                raise ValueError("La correzione deve annullare l’ultima variazione del lato.")
        else:
            deltas.append(change.delta)
    return {
        name: {"gained": sum(d for d in deltas if d > 0),
               "lost": -sum(d for d in deltas if d < 0), "changes": len(deltas)}
        for name, deltas in active.items()
    }


def _save(game: Game, data_dir: str | Path | None = None) -> bool:
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = game.model_dump(mode="json")
    if data["result_target"] is None:
        del data["result_target"]
    # The complete timeline is retained; these aggregates make downstream analysis easier.
    data["statistics"] = _statistics(game)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS games (session_id TEXT PRIMARY KEY, ended_at TEXT NOT NULL, payload TEXT NOT NULL)")
        existing = conn.execute("SELECT payload FROM games WHERE session_id = ?", (str(game.session_id),)).fetchone()
        if existing:
            if existing[0] != payload:
                raise HTTPException(409, "Questa sessione è già stata salvata con dati diversi.")
            return True
        try:
            conn.execute("INSERT INTO games VALUES (?, ?, ?)", (str(game.session_id), game.ended_at.isoformat(), payload))
        except sqlite3.IntegrityError:
            existing = conn.execute("SELECT payload FROM games WHERE session_id = ?", (str(game.session_id),)).fetchone()
            if not existing or existing[0] != payload:
                raise HTTPException(409, "Questa sessione è già stata salvata con dati diversi.")
            return True
    return False


@router.post("/api/life/games")
async def save_life_game(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "È richiesto application/json.")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BODY:
            raise HTTPException(413, "Sessione troppo grande.")
    try:
        game = Game.model_validate_json(bytes(chunks))
        _validate(game)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Dati della sessione non validi: " + str(exc)[:300]) from exc
    try:
        db = getattr(request.app.state, "db", None)
        duplicate = await run_in_threadpool(_save, game, getattr(db, "data_dir", None))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Archivio statistiche non disponibile.") from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "Archivio statistiche non disponibile.") from exc
    from .life_results import submit_result
    if game.result_target and db:
        result = await run_in_threadpool(submit_result, db, game, request.cookies.get('mana_player_session', ''), request.headers.get('X-Player-CSRF', ''))
    else:
        result = {"result_status": "stats_only"}
    return {"saved": True, "duplicate": duplicate, **result}
