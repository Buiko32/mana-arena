"""Short-lived spectator snapshots for the single Mana Arena server process."""
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from math import exp, log
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from .access import COOKIE
from .life import Member
from .service import _elo_inputs, calculate_global_elo, global_elo_from_db
from .live_bets import public_market, settle_mana_bets, tournament_market

router = APIRouter()
LIVE_TIMEOUT = 90


class Side(BaseModel):
    members: list[Member] = Field(min_length=1, max_length=6)
    initial: int = Field(ge=1, le=999)
    life: int = Field(ge=-100000, le=100000)


class Snapshot(BaseModel):
    format: Literal["duel", "team", "ffa"]
    kind: Literal["friendly", "tournament"]
    sides: list[Side] = Field(min_length=2, max_length=5)
    tournament_id: str | None = Field(default=None, max_length=80)
    match_id: str | None = Field(default=None, max_length=100)

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
    bettor_eligible: bool = False


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
        eligible = False
        if snapshot.format == "duel" and strength is not None:
            ids = [side.members[0].id for side in snapshot.sides]
            with request.app.state.db.transaction() as conn:
                session = viewer_session(conn, request)
                eligible = bool(session and session["player_id"] in ids)
                existing = conn.execute("SELECT p1,p2 FROM mana_bets WHERE market=? LIMIT 1",
                                        (f"live:{game_id}",)).fetchone()
                if existing and (snapshot.kind != "friendly" or set(ids) != {existing["p1"], existing["p2"]}):
                    raise HTTPException(409, "I giocatori della partita non possono cambiare.")
                if eligible and snapshot.kind == "tournament" and snapshot.match_id and snapshot.tournament_id:
                    match = tournament_market(conn, snapshot.tournament_id, snapshot.match_id)
                    if match and market_open(match) and set(ids) == {match.get("p1"), match.get("p2")}:
                        conn.execute("INSERT OR IGNORE INTO live_tournament_markets VALUES (?,?)",
                                     (snapshot.match_id, str(game_id)))
        live.entries[game_id] = Entry(owner, snapshot, strength, monotonic() + LIVE_TIMEOUT, eligible)
    return Response(status_code=204)


@router.delete("/api/life/live/{game_id}", status_code=204)
def stop_live(game_id: UUID, request: Request, cancel: bool = False):
    owner = publisher(request)
    live = request.app.state.live_games
    with live.lock:
        entry = live.entries.get(game_id)
        if entry and entry.owner != owner:
            raise HTTPException(404)
        if cancel and entry and entry.bettor_eligible:
            snapshot = entry.snapshot
            market = (tournament_market_id(snapshot.tournament_id, snapshot.match_id)
                      if snapshot.kind == "tournament" else f"live:{game_id}")
            with request.app.state.db.transaction() as conn:
                session = viewer_session(conn, request)
                if session and session["player_id"] in [side.members[0].id for side in snapshot.sides]:
                    settle_mana_bets(conn)
                    if snapshot.kind == "tournament":
                        removed = conn.execute("DELETE FROM live_tournament_markets WHERE match_id=? AND session_id=?",
                                               (snapshot.match_id, str(game_id))).rowcount
                        if not removed:
                            live.entries.pop(game_id, None)
                            return Response(status_code=204)
                    conn.execute("UPDATE mana_bets SET status='refunded' WHERE market=? AND status='pending'", (market,))
        live.entries.pop(game_id, None)
    return Response(status_code=204)


def viewer_session(conn, request):
    token = request.cookies.get("mana_player_session", "")
    return conn.execute("""
        SELECT s.player_id, s.csrf FROM player_sessions s JOIN players p ON p.id=s.player_id
        WHERE s.token_hash=? AND s.expires_at>strftime('%s','now') AND p.active=1
    """, (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()


def tournament_market_id(tournament, match):
    return f"tournament:{tournament}:{match}"


def market_open(match):
    return match["status"] == "active" and not any(match.get(flag) for flag in ("result", "void", "bye"))


def prematch_prediction(ids, ranking):
    ratings = [ranking[pid]["rating"] for pid in ids]
    difference = max(-800, min(800, ratings[0] - ratings[1]))
    percent = max(1, min(99, round(100 / (1 + exp(-difference * log(10) / 400)))))
    return [percent, 100 - percent]


def market_for_entry(conn, key, entry, viewer, ranking):
    if not entry.bettor_eligible:
        return None
    snapshot = entry.snapshot
    ids = [side.members[0].id for side in snapshot.sides]
    prediction = estimate(snapshot, entry.strength)
    if not prediction or any(pid not in ranking for pid in ids):
        return None
    if snapshot.kind == "tournament":
        if not snapshot.tournament_id or not snapshot.match_id:
            return None
        match = tournament_market(conn, snapshot.tournament_id, snapshot.match_id)
        binding = conn.execute("SELECT session_id FROM live_tournament_markets WHERE match_id=?",
                               (snapshot.match_id,)).fetchone()
        if not match or not market_open(match) or not binding or binding["session_id"] != str(key):
            return None
        ordered = [match.get("p1"), match.get("p2")]
        if set(ordered) != set(ids):
            return None
        if ordered != ids:
            prediction = prediction[::-1]
        market_id = tournament_market_id(snapshot.tournament_id, snapshot.match_id)
        market = public_market(conn, market_id, ordered, prediction, viewer)
    else:
        market = public_market(conn, f"live:{key}", ids, prediction, viewer)
        if conn.execute("SELECT 1 FROM life_result_links WHERE session_id=?", (str(key),)).fetchone():
            return None
    return market


@router.get("/api/public/live-markets")
def live_markets(request: Request, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    live = request.app.state.live_games
    with live.lock:
        live.prune()
        entries = list(live.entries.items())
    with request.app.state.db.transaction() as conn:
        session = viewer_session(conn, request)
        viewer = session["player_id"] if session else None
        ranking = {row["id"]: row for row in global_elo_from_db(conn, include_movement=True)}
        active = {}
        for key, entry in entries:
            market = market_for_entry(conn, key, entry, viewer, ranking)
            if market:
                active[str(key)] = market
        started = {row["match_id"] for row in conn.execute("SELECT match_id FROM live_tournament_markets")}
        scheduled = []
        for row in conn.execute("""
            SELECT m.id, m.tournament_id, m.round_no, m.data_json, t.config_json FROM matches m
            JOIN tournaments t ON t.id=m.tournament_id WHERE t.status='active'
            ORDER BY t.created_at, m.round_no, m.table_no
        """):
            match = json.loads(row["data_json"])
            ids = [match.get("p1"), match.get("p2")]
            if match.get("result") or match.get("void") or match.get("bye") or len(set(ids)) != 2 or any(pid not in ranking for pid in ids):
                continue
            market_id = tournament_market_id(row["tournament_id"], row["id"])
            if row["id"] in started:
                continue
            market = public_market(conn, market_id, ids, prematch_prediction(ids, ranking), viewer)
            market["title"] = f'{json.loads(row["config_json"])["name"]} · Turno {row["round_no"]}'
            market["names"] = [ranking[pid]["name"] for pid in ids]
            scheduled.append(market)
        tickets = [dict(row) for row in conn.execute("""
            SELECT b.market, b.selection, b.amount, b.multiplier, b.status, p.name
            FROM mana_bets b LEFT JOIN players p ON p.id=b.selection
            WHERE b.bettor=? ORDER BY b.id DESC LIMIT 20
        """, (viewer,))]
        return {"active": active, "scheduled": scheduled, "tickets": tickets,
                "viewer": {"balance": ranking[viewer]["mana_fragments"], "csrf": session["csrf"]}
                if viewer in ranking else None}


class BetRequest(BaseModel):
    market: str = Field(max_length=220)
    selection: str = Field(max_length=100)
    amount: int = Field(strict=True, ge=5, multiple_of=5)
    multiplier: int = Field(strict=True, ge=105, le=300)


@router.post("/api/player/live-bets", status_code=201)
def place_bet(bet: BetRequest, request: Request):
    live = request.app.state.live_games
    with live.lock, request.app.state.db.transaction() as conn:
        session = viewer_session(conn, request)
        if not session or not secrets.compare_digest(request.headers.get("X-Player-CSRF", ""), session["csrf"]):
            raise HTTPException(403, "Accedi per puntare.")
        player = session["player_id"]
        live.prune()
        ranking = {row["id"]: row for row in global_elo_from_db(conn, include_movement=True)}
        market = None
        parts = bet.market.split(":")
        if len(parts) == 3 and parts[0] == "tournament":
            _, tournament_id, match_id = parts
            match = tournament_market(conn, tournament_id, match_id)
            ids = [match.get("p1"), match.get("p2")] if match else []
            if match and market_open(match) and len(set(ids)) == 2 and all(pid in ranking for pid in ids):
                binding = conn.execute("SELECT session_id FROM live_tournament_markets WHERE match_id=?", (match_id,)).fetchone()
                if binding:
                    key = UUID(binding["session_id"])
                    entry = live.entries.get(key)
                    if entry and (entry.snapshot.tournament_id, entry.snapshot.match_id) == (tournament_id, match_id):
                        market = market_for_entry(conn, key, entry, player, ranking)
                else:
                    market = public_market(conn, bet.market, ids, prematch_prediction(ids, ranking), player)
        elif len(parts) == 2 and parts[0] == "live":
            _, match_id = parts
            tournament_id = None
            try:
                key = UUID(match_id)
            except ValueError:
                raise HTTPException(404, "Partita non disponibile.") from None
            entry = live.entries.get(key)
            if entry and entry.snapshot.kind == "friendly":
                market = market_for_entry(conn, key, entry, player, ranking)
        if not market:
            raise HTTPException(409, "Le puntate per questa partita sono chiuse.")
        if market["participant"]:
            raise HTTPException(403, "Non puoi puntare sulla tua partita.")
        if market["own"]:
            raise HTTPException(409, "Hai già puntato su questa partita.")
        if bet.selection not in market["players"]:
            raise HTTPException(422, "Seleziona un giocatore della partita.")
        multiplier = market["quotes"][market["players"].index(bet.selection)]
        if bet.multiplier != multiplier:
            raise HTTPException(409, "La quota è cambiata. Controllala e conferma di nuovo.")
        if bet.amount > ranking[player]["mana_fragments"]:
            raise HTTPException(409, "Saldo Frammenti Mana insufficiente.")
        conn.execute("""
            INSERT INTO mana_bets(market,match_id,tournament_id,p1,p2,bettor,selection,amount,multiplier,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (bet.market, match_id, tournament_id, *market["players"], player, bet.selection,
              bet.amount, multiplier, int(datetime.now(timezone.utc).timestamp())))
        return {"amount": bet.amount, "multiplier": multiplier, "return": bet.amount * multiplier // 100}
