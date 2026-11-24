"""Rules-to-storage adapters shared by API workflows."""
from __future__ import annotations

from .engine import calculate_standings


def champion_id(config: dict, entrants: list[dict], matches: list[dict]) -> str | None:
    structure = config["structure"]
    active_ids = {person["id"] for person in entrants if not person.get("withdrawn")}
    if not active_ids:
        return None
    finalized = [m for m in matches if m.get("result") is not None]
    if structure == "single_elim":
        if not finalized:
            return None
        last_round = max(m["round"] for m in finalized)
        final_matches = [m for m in finalized if m["round"] == last_round]
        winners = []
        for match in final_matches:
            if match.get("result") == "bye":
                winners.append(match.get("p1"))
            elif match.get("result") in ("p1_win", "p2_forfeit"):
                winners.append(match.get("p1"))
            elif match.get("result") in ("p2_win", "p1_forfeit"):
                winners.append(match.get("p2"))
        winners = [winner for winner in winners if winner in active_ids]
        return winners[0] if len(winners) == 1 else None
    if structure == "adapted":
        rows = calculate_standings(config, entrants, finalized)
        remaining = [row for row in rows if not row["eliminated"]]
        return remaining[0]["player_id"] if len(remaining) == 1 else None
    if structure == "swiss":
        rows = calculate_standings(config, entrants, finalized)
        return next((row["player_id"] for row in rows if row["player_id"] in active_ids), None)
    return None


def public_rounds(matches: list[dict]) -> list[dict]:
    by_round = {}
    for match in matches:
        item = {key: value for key, value in match.items() if key != "id"}
        by_round.setdefault(match["round"], []).append({"id": match["id"], **item})
    return [{"round": number, "matches": rows} for number, rows in sorted(by_round.items())]


def safe_csv(value) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


# Internal Elo inspired by FIDE K thresholds, not an official FIDE rating.
def calculate_global_elo(players: list[dict], matches: list[dict]) -> list[dict]:
    rows = {p['id']: {**p, 'rating': 1500.0, 'matches': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'peak_2400': False} for p in players}
    for match in matches:
        if match.get('team_a') and match.get('team_b'):
            teams = [[rows.get(pid) for pid in match[key]] for key in ('team_a','team_b')]
            if any(not row for team in teams for row in team) or match.get('winner') not in ('a','b'):
                continue
            averages = [sum(row['rating'] for row in team)/len(team) for team in teams]
            updates = []
            for index, team in enumerate(teams):
                diff = max(-400, min(400, averages[1-index]-averages[index])) if averages[index] < 2650 else averages[1-index]-averages[index]
                expected_score = 1/(1+10**(diff/400))
                won = match['winner'] == ('a' if index == 0 else 'b')
                for row in team:
                    k = 40 if row['matches'] < 30 else 10 if row['peak_2400'] else 20
                    updates.append((row, k*((1 if won else 0)-expected_score), won))
            for row, change, won in updates:
                row['rating'] += change
                row['matches'] += 1
                row['wins' if won else 'losses'] += 1
                row['peak_2400'] |= row['rating'] >= 2400
            continue
        result = match.get('result')
        a, b = rows.get(match.get('p1')), rows.get(match.get('p2'))
        if result not in ('p1_win', 'p2_win') or not a or not b or a is b or match.get('bye') or match.get('void'):
            continue
        score_a = {'p1_win': 1.0, 'p2_win': 0.0}[result]
        ra, rb = a['rating'], b['rating']
        def expected(own, other):
            diff = other - own
            if own < 2650:
                diff = max(-400, min(400, diff))
            return 1 / (1 + 10 ** (diff / 400))
        def factor(row):
            return 40 if row['matches'] < 30 else 10 if row['peak_2400'] else 20
        a['rating'] += factor(a) * (score_a - expected(ra, rb))
        b['rating'] += factor(b) * ((1 - score_a) - expected(rb, ra))
        for row in (a, b):
            row['matches'] += 1
            row['peak_2400'] |= row['rating'] >= 2400
        if score_a == 1: a['wins'] += 1; b['losses'] += 1
        elif score_a == 0: b['wins'] += 1; a['losses'] += 1
    result = sorted(rows.values(), key=lambda row: (-row['rating'], -row['wins'], row['name'].casefold(), row['id']))
    for position, row in enumerate(result, 1):
        row['rating'] = round(row['rating'])
        row['rank'] = position
        del row['peak_2400']
    return result


def global_elo_from_db(conn):
    import json
    players = [dict(row) for row in conn.execute('SELECT id,name,active,photo,avatar_hue,avatar_color FROM players')]
    # The audit id is the stable order in which results were recorded; use the latest
    # matching event after a rollback. Old backups without events have a deterministic fallback.
    events = {}
    for row in conn.execute("SELECT id,detail_json FROM audit WHERE action IN ('match_result_recorded','friendly_result_recorded','team_friendly_result_recorded') ORDER BY id"):
        detail = json.loads(row['detail_json'])
        if detail.get('match_id'):
            events[detail['match_id']] = row['id']
    matches = []
    for row in conn.execute('SELECT m.id,m.data_json,m.round_no,m.table_no,t.created_at,t.id AS tid FROM matches m JOIN tournaments t ON t.id=m.tournament_id'):
        match = json.loads(row['data_json'])
        event = events.get(row['id'])
        key = (1, event) if event is not None else (0, row['created_at'], row['tid'], row['round_no'], row['table_no'], row['id'])
        matches.append((key, match))
    for row in conn.execute("SELECT id,proposer_id,opponent_id,winner_id,created_at FROM result_proposals WHERE tournament_id IS NULL AND status='confirmed'"):
        match = {'p1':row['proposer_id'], 'p2':row['opponent_id'], 'result':'p1_win' if row['winner_id']==row['proposer_id'] else 'p2_win'}
        event = events.get(row['id'])
        key = (1, event) if event is not None else (0, row['created_at'], '', 0, 0, row['id'])
        matches.append((key, match))
    for row in conn.execute("SELECT id,winning_team,created_at FROM team_results WHERE status='confirmed'"):
        members = list(conn.execute('SELECT player_id,team FROM team_result_players WHERE result_id=? ORDER BY player_id',(row['id'],)))
        match = {'team_a':[m['player_id'] for m in members if m['team']=='a'], 'team_b':[m['player_id'] for m in members if m['team']=='b'], 'winner':row['winning_team']}
        event=events.get(row['id'])
        key=(1,event) if event is not None else (0,row['created_at'],'',0,0,row['id'])
        matches.append((key,match))
    matches.sort(key=lambda item: item[0])
    return [row for row in calculate_global_elo(players, [item[1] for item in matches]) if row['active']]
