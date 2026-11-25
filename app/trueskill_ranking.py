from .service import _elo_inputs, calculate_global_elo
from trueskill import TrueSkill


def _result(match):
    if match.get("void") or match.get("bye"):
        return None
    if placements := match.get("placements"):
        return [[pid] for pid in placements], list(range(len(placements)))
    if match.get("team_a") and match.get("team_b"):
        winner = match.get("winner")
        if winner in ("a", "b"):
            return [match["team_a"], match["team_b"]], [winner != "a", winner != "b"]
    outcome = match.get("result")
    if outcome in ("p1_win", "p2_win", "draw"):
        return [[match.get("p1")], [match.get("p2")]], {
            "p1_win": [0, 1], "p2_win": [1, 0], "draw": [0, 0],
        }[outcome]
    return None


def trueskill_comparison(conn):
    players, matches = _elo_inputs(conn)
    elo_rows = [row for row in calculate_global_elo(players, matches) if row["active"]]
    model = TrueSkill()
    ratings = {player["id"]: model.create_rating() for player in players}
    counts = dict.fromkeys(ratings, 0)
    for match in matches:
        result = _result(match)
        if result is None:
            continue
        teams, ranks = result
        ids = [pid for team in teams for pid in team]
        if len(set(ids)) != len(ids) or any(pid not in ratings for pid in ids):
            continue
        updated = model.rate([[ratings[pid] for pid in team] for team in teams], ranks=ranks)
        for team, results in zip(teams, updated):
            for pid, rating in zip(team, results):
                ratings[pid] = rating
                counts[pid] += 1

    rows = []
    for elo_rank, player in enumerate(elo_rows, 1):
        pid = player["id"]
        rating = ratings[pid]
        rows.append({
            "id": pid, "name": player["name"], "elo": player["rating"], "elo_rank": elo_rank,
            "matches": counts[pid], "mu": rating.mu, "sigma": rating.sigma,
            "score": model.expose(rating),
        })
    rows.sort(key=lambda row: (not row["matches"], -row["score"], row["name"].casefold(), row["id"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank if row["matches"] else None
    return rows
