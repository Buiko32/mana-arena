"""Public history of played matches, grouped by the client in local time."""
import json

from .service import match_elo_deltas


def month_matches(conn, start, end):
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM players")}
    games = []
    participants = {}

    def add(match_id, stamp, label, left, right, winner, kind="friendly", score=None, match_format="duel"):
        participants[match_id] = (len(games), (left, right))
        games.append({"at": stamp.replace(" ", "T") + "Z", "label": label, "kind": kind,
                      "sides": [[names.get(p, "Giocatore") for p in side] for side in (left, right)],
                      "player_ids": [left, right], "winner": winner, "score": score, "format": match_format})

    for row in conn.execute("""
        WITH recorded AS (
            SELECT tournament_id, json_extract(detail_json,'$.match_id') AS mid, MAX(created_at) AS stamp
            FROM audit WHERE action='match_result_recorded' GROUP BY tournament_id, mid
        )
        SELECT m.id, m.data_json, t.config_json, m.round_no, COALESCE(p.stamp,r.stamp,t.created_at) AS stamp
        FROM matches m LEFT JOIN recorded r ON m.id=r.mid AND m.tournament_id=r.tournament_id
        LEFT JOIN (SELECT tournament_id,match_id,MAX(created_at) AS stamp FROM result_proposals WHERE status='confirmed' GROUP BY tournament_id,match_id) p ON p.tournament_id=m.tournament_id AND p.match_id=m.id
        JOIN tournaments t ON t.id=m.tournament_id WHERE COALESCE(p.stamp,r.stamp,t.created_at)>=? AND COALESCE(p.stamp,r.stamp,t.created_at)<?
    """, (start, end)):
        m = json.loads(row["data_json"])
        outcome = m.get("result")
        if outcome in ("p1_win", "p2_win", "draw") and m.get("p2") and not m.get("void"):
            label = f'{json.loads(row["config_json"])["name"]} · Turno {row["round_no"]}'
            score = [m.get("p1_games"), m.get("p2_games")]
            if m.get("score_unknown") or any(value is None for value in score):
                score = None
            add(row["id"], row["stamp"], label, [m["p1"]], [m["p2"]], {"p1_win": 0, "p2_win": 1}.get(outcome),
                kind="tournament", score=score)

    for r in conn.execute("""SELECT * FROM result_proposals WHERE status='confirmed'
            AND tournament_id IS NULL AND created_at>=? AND created_at<?""", (start, end)):
        add(r["id"], r["created_at"], "Amichevole", [r["proposer_id"]], [r["opponent_id"]],
            0 if r["winner_id"] == r["proposer_id"] else 1)

    teams = {}
    for r in conn.execute("""SELECT r.id,r.created_at,r.winning_team,p.team,p.player_id
            FROM team_results r JOIN team_result_players p ON p.result_id=r.id
            WHERE r.status='confirmed' AND r.created_at>=? AND r.created_at<?
            ORDER BY r.id,p.team,p.player_id""", (start, end)):
        stamp, winner, sides = teams.setdefault(r["id"], (r["created_at"], r["winning_team"], {"a": [], "b": []}))
        sides[r["team"]].append(r["player_id"])
    for match_id, (stamp, winner, sides) in teams.items():
        add(match_id, stamp, "Amichevole", sides["a"], sides["b"], 0 if winner == "a" else 1, match_format="team")
    for row in conn.execute("SELECT * FROM ffa_results WHERE created_at>=? AND created_at<?", (start, end)):
        sides = [[pid] for pid in json.loads(row["placements"])]
        participants[row["id"]] = (len(games), sides)
        games.append({"at": row["created_at"].replace(" ", "T") + "Z", "label": "Amichevole · Tutti contro tutti",
                      "kind": "ffa", "sides": [[names.get(pid, "Giocatore")] for (pid,) in sides],
                      "player_ids": sides, "winner": 0, "score": None, "format": "ffa"})
    if participants:
        deltas = match_elo_deltas(conn, participants)
        for match_id, (index, sides) in participants.items():
            changes = deltas.get(match_id, {})
            games[index]["elo_deltas"] = [[changes.get(pid, 0) for pid in side] for side in sides]
    return sorted(games, key=lambda game: game["at"], reverse=True)
