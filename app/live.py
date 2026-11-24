"""Short-lived spectator snapshots for the single Mana Arena server process."""
from dataclasses import dataclass
from math import exp, log
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from .access import COOKIE
from .life import Member
from .service import _elo_inputs, calculate_global_elo

router = APIRouter()
LIVE_TIMEOUT = 90


class Side(BaseModel):
    members: list[Member] = Field(min_length=1, max_length=6)
    initial: int = Field(ge=1, le=999)
    life: int = Field(ge=-100000, le=100000)


class Snapshot(BaseModel):
    format: Literal["duel", "team", "ffa"]
    kind: Literal["friendly", "tournament"]
    sides: list[Side] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def check_sides(self):
        if self.format != "ffa" and len(self.sides) != 2:
            raise ValueError("Sono richiesti due lati.")
        if self.kind == "tournament" and self.format != "duel":
            raise ValueError("Il torneo richiede un duello.")
        if self.format != "team" and any(len(side.members) != 1 for side in self.sides):
            raise ValueError("È richiesto un giocatore per lato.")
        ids = [member.id for side in self.sides for member in side.members if member.id]
        if len(set(ids)) != len(ids):
            raise ValueError("I profili devono essere distinti.")
        return self


def duel_strength(conn, snapshot):
    if snapshot.format != "duel":
        return None
    a, b = [side.members[0].id for side in snapshot.sides]
    if not a or not b:
        return None

    players, matches = _elo_inputs(conn)
    ratings = {row["id"]: row["rating"] for row in calculate_global_elo(players, matches)}
    if a not in ratings or b not in ratings:
        return None

    wins = [0, 0]
    for match in matches:
        pair = (match.get("p1"), match.get("p2"))
        outcome = match.get("result")
        if pair not in ((a, b), (b, a)) or match.get("bye") or match.get("void"):
            continue
        if outcome in ("p1_win", "p2_win"):
            winner = pair[0 if outcome == "p1_win" else 1]
            wins[0 if winner == a else 1] += 1

    elo = max(-800, min(800, ratings[a] - ratings[b])) * log(10) / 400
    return elo + 0.6 * log((wins[0] + 4) / (wins[1] + 4))


def estimate(snapshot, strength):
    if strength is None:
        return None
    a, b = snapshot.sides
    # Compare remaining fractions. The floor keeps estimates finite near zero.
    life_log_odds = log(max(0.01, a.life / a.initial) / max(0.01, b.life / b.initial))
    log_odds = strength + 1.5 * life_log_odds
    percent = max(1, min(99, round(100 / (1 + exp(-log_odds)))))
    return [percent, 100 - percent]


@dataclass
class Entry:
    owner: str
    snapshot: Snapshot
    strength: float | None
    expires: float


class LiveGames:
    def __init__(self):
        self.lock = Lock()
        self.entries = {}

    def prune(self):
        now = monotonic()
        for key in [key for key, entry in self.entries.items() if entry.expires <= now]:
            del self.entries[key]


def publisher(request):
    if request.headers.get("X-Mana-Live") != "1":
        raise HTTPException(403)
    return request.cookies[COOKIE]


@router.get("/api/public/live")
def live_games(request: Request):
    live = request.app.state.live_games
    with live.lock:
        live.prune()
        return [
            {"id": key, **entry.snapshot.model_dump(),
             "prediction": estimate(entry.snapshot, entry.strength)}
            for key, entry in live.entries.items()
        ]


@router.put("/api/life/live/{game_id}", status_code=204)
def update_live(game_id: UUID, snapshot: Snapshot, request: Request):
    owner = publisher(request)
    live = request.app.state.live_games
    with live.lock:
        live.prune()
        entry = live.entries.get(game_id)
        if entry and entry.owner != owner:
            raise HTTPException(404)
        if not entry and len(live.entries) >= 128:
            raise HTTPException(429)
        if entry and (entry.snapshot.format, entry.snapshot.kind,
                      [side.members for side in entry.snapshot.sides]) == (
                          snapshot.format, snapshot.kind, [side.members for side in snapshot.sides]):
            strength = entry.strength
        elif snapshot.format == "duel" and all(side.members[0].id for side in snapshot.sides):
            with request.app.state.db.transaction() as conn:
                strength = duel_strength(conn, snapshot)
        else:
            strength = None
        live.entries[game_id] = Entry(owner, snapshot, strength, monotonic() + LIVE_TIMEOUT)
    return Response(status_code=204)


@router.delete("/api/life/live/{game_id}", status_code=204)
def stop_live(game_id: UUID, request: Request):
    owner = publisher(request)
    live = request.app.state.live_games
    with live.lock:
        entry = live.entries.get(game_id)
        if entry and entry.owner != owner:
            raise HTTPException(404)
        live.entries.pop(game_id, None)
    return Response(status_code=204)
