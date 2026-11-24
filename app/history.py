"""Public history of played matches, grouped by the client in local time."""
import json


def month_matches(conn, start, end):
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM players")}
    games = []
    def add(stamp, label, left, right, winner):
        games.append({"at": stamp.replace(" ", "T") + "Z", "label": label,
                      "sides": [[names.get(p, "Giocatore") for p in side] for side in (left, right)],
                      "winner": winner})

    for row in conn.execute("""
        WITH recorded AS (
            SELECT tournament_id, json_extract(detail_json,'$.match_id') AS mid, MAX(created_at) AS stamp
            FROM audit WHERE action='match_result_recorded' GROUP BY tournament_id, mid
        )
        SELECT m.data_json, t.config_json, m.round_no, COALESCE(p.stamp,r.stamp,t.created_at) AS stamp
        FROM matches m LEFT JOIN recorded r ON m.id=r.mid AND m.tournament_id=r.tournament_id
        LEFT JOIN (SELECT tournament_id,match_id,MAX(created_at) AS stamp FROM result_proposals WHERE status='confirmed' GROUP BY tournament_id,match_id) p ON p.tournament_id=m.tournament_id AND p.match_id=m.id
        JOIN tournaments t ON t.id=m.tournament_id WHERE COALESCE(p.stamp,r.stamp,t.created_at)>=? AND COALESCE(p.stamp,r.stamp,t.created_at)<?
    """, (start, end)):
        m = json.loads(row["data_json"])
        outcome = m.get("result")
        if outcome in ("p1_win", "p2_win", "draw") and m.get("p2") and not m.get("void"):
            label = f'{json.loads(row["config_json"])["name"]} · Turno {row["round_no"]}'
            add(row["stamp"], label, [m["p1"]], [m["p2"]], {"p1_win": 0, "p2_win": 1}.get(outcome))

    for r in conn.execute("""SELECT * FROM result_proposals WHERE status='confirmed'
            AND tournament_id IS NULL AND created_at>=? AND created_at<?""", (start, end)):
        add(r["created_at"], "Amichevole", [r["proposer_id"]], [r["opponent_id"]],
            0 if r["winner_id"] == r["proposer_id"] else 1)

    teams = {}
    for r in conn.execute("""SELECT r.id,r.created_at,r.winning_team,p.team,p.player_id
            FROM team_results r JOIN team_result_players p ON p.result_id=r.id
            WHERE r.status='confirmed' AND r.created_at>=? AND r.created_at<?
            ORDER BY r.id,p.team,p.player_id""", (start, end)):
        stamp, winner, sides = teams.setdefault(r["id"], (r["created_at"], r["winning_team"], {"a": [], "b": []}))
        sides[r["team"]].append(r["player_id"])
    for stamp, winner, sides in teams.values():
        add(stamp, "Amichevole", sides["a"], sides["b"], 0 if winner == "a" else 1)
    return sorted(games, key=lambda game: game["at"], reverse=True)
