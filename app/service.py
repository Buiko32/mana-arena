"""Rules-to-storage adapters shared by API workflows."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .engine import calculate_standings
from .weekly_missions import WeeklyMissions


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


def _elo_order(row):
    return (-row['rating'], -row['wins'], row['name'].casefold(), row['id'])


def _expected_score(own, other):
    diff = other - own
    if own < 2650:
        diff = max(-400, min(400, diff))
    return 1 / (1 + 10 ** (diff / 400))


ELO_PLACEMENT_MATCHES = 30


def _elo_factor(row):
    return 40 if row['matches'] < ELO_PLACEMENT_MATCHES else 10 if row['peak_2400'] else 20


# Internal Elo inspired by FIDE K thresholds, not an official FIDE rating.
def calculate_global_elo(players: list[dict], matches: list[dict], *, on_game=None, before_game=None) -> list[dict]:
    rows = {p['id']: {**p, 'rating': 1500.0, 'matches': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'peak_2400': False} for p in players}
    for match in matches:
        if placements := match.get('placements'):
            ordered = [rows.get(pid) for pid in placements]
            if len(set(placements)) != len(placements) or not 3 <= len(ordered) <= 5 or any(row is None for row in ordered):
                continue
            if before_game:
                before_game(rows, match)
            updates = []
            for position, row in enumerate(ordered):
                opponents = ordered[:position] + ordered[position + 1:]
                expected = sum(_expected_score(row['rating'], other['rating']) for other in opponents)
                change = _elo_factor(row) * (len(ordered) - 1 - position - expected) / len(opponents)
                average = sum(other['rating'] for other in opponents) / len(opponents)
                updates.append((row, change, position == 0, average, opponents))
            for row, change, won, _, _ in updates:
                row['rating'] += change
                row['matches'] += 1
                row['wins' if won else 'losses'] += 1
                row['peak_2400'] |= row['rating'] >= 2400
            if on_game:
                on_game(rows, match, updates)
            continue
        if match.get('team_a') and match.get('team_b'):
            teams = [[rows.get(pid) for pid in match[key]] for key in ('team_a','team_b')]
            if any(not row for team in teams for row in team) or match.get('winner') not in ('a','b'):
                continue
            if before_game:
                before_game(rows, match)
            averages = [sum(row['rating'] for row in team)/len(team) for team in teams]
            updates = []
            for index, team in enumerate(teams):
                # The numerical handicap adjusts the opposing team's average;
                # every player is compared using their own pre-match rating.
                opponent_strength = averages[1-index] + 150 * (len(teams[1-index]) - len(team))
                won = match['winner'] == ('a' if index == 0 else 'b')
                opponents = teams[1-index] if on_game else None
                for row in team:
                    diff = opponent_strength - row['rating']
                    if row['rating'] < 2650:
                        diff = max(-400, min(400, diff))
                    expected_score = 1/(1+10**(diff/400))
                    k = _elo_factor(row)
                    updates.append((row, k*((1 if won else 0)-expected_score), won, opponent_strength, opponents))
            for row, change, won, _, _ in updates:
                row['rating'] += change
                row['matches'] += 1
                row['wins' if won else 'losses'] += 1
                row['peak_2400'] |= row['rating'] >= 2400
            if on_game:
                on_game(rows, match, updates)
            continue
        result = match.get('result')
        a, b = rows.get(match.get('p1')), rows.get(match.get('p2'))
        if result not in ('p1_win', 'p2_win', 'draw') or not a or not b or a is b or match.get('bye') or match.get('void'):
            continue
        if before_game:
            before_game(rows, match)
        if result == 'draw':
            continue
        score_a = {'p1_win': 1.0, 'p2_win': 0.0}[result]
        ra, rb = a['rating'], b['rating']
        a['rating'] += _elo_factor(a) * (score_a - _expected_score(ra, rb))
        b['rating'] += _elo_factor(b) * ((1 - score_a) - _expected_score(rb, ra))
        for row in (a, b):
            row['matches'] += 1
            row['peak_2400'] |= row['rating'] >= 2400
        if score_a == 1: a['wins'] += 1; b['losses'] += 1
        elif score_a == 0: b['wins'] += 1; a['losses'] += 1
        if on_game:
            on_game(rows, match, [
                (a, a['rating'] - ra, score_a == 1, rb, [b]),
                (b, b['rating'] - rb, score_a == 0, ra, [a]),
            ])
    result = sorted(rows.values(), key=_elo_order)
    for position, row in enumerate(result, 1):
        row['rating'] = round(row['rating'])
        row['rank'] = position
        del row['peak_2400']
    return result


def _elo_inputs(conn):
    import json
    players = [dict(row) for row in conn.execute('''
        SELECT id,name,active,photo,avatar_hue,avatar_color,created_at,
               CASE WHEN active=0 THEN updated_at END AS inactive_at
        FROM players
    ''')]
    # Replay by match date. Audit sequence breaks ties when records share a second.
    events = {}
    for row in conn.execute("SELECT id,detail_json,created_at FROM audit WHERE action IN ('match_result_recorded','friendly_result_recorded','team_friendly_result_recorded','ffa_friendly_result_recorded') ORDER BY id"):
        detail = json.loads(row['detail_json'])
        if detail.get('match_id'):
            events[detail['match_id']] = (row['created_at'], row['id'])
    matches = []
    for row in conn.execute("""
        WITH proposals AS (
            SELECT tournament_id, match_id, MAX(created_at) AS played_at FROM result_proposals
            WHERE tournament_id IS NOT NULL AND status='confirmed' GROUP BY tournament_id, match_id
        )
        SELECT m.id, m.data_json, t.created_at, p.played_at AS proposal_at
        FROM matches m JOIN tournaments t ON t.id=m.tournament_id
        LEFT JOIN proposals p ON p.tournament_id=m.tournament_id AND p.match_id=m.id
    """):
        match = json.loads(row['data_json'])
        event = events.get(row['id'])
        key = (row['proposal_at'] or (event[0] if event else row['created_at']), event[1] if event else 0, row['id'])
        matches.append((key, {**match, 'tournament_match': True}))
    for row in conn.execute("SELECT id,proposer_id,opponent_id,winner_id,created_at FROM result_proposals WHERE tournament_id IS NULL AND status='confirmed'"):
        match = {'p1':row['proposer_id'], 'p2':row['opponent_id'], 'result':'p1_win' if row['winner_id']==row['proposer_id'] else 'p2_win'}
        event = events.get(row['id'])
        key = (row['created_at'], event[1] if event else 0, row['id'])
        matches.append((key, match))
    for row in conn.execute("SELECT id,winning_team,created_at FROM team_results WHERE status='confirmed'"):
        members = list(conn.execute('SELECT player_id,team FROM team_result_players WHERE result_id=? ORDER BY player_id',(row['id'],)))
        match = {'team_a':[m['player_id'] for m in members if m['team']=='a'], 'team_b':[m['player_id'] for m in members if m['team']=='b'], 'winner':row['winning_team']}
        event=events.get(row['id'])
        key=(row['created_at'], event[1] if event else 0, row['id'])
        matches.append((key,match))
    for row in conn.execute("SELECT id,placements,created_at FROM ffa_results"):
        event = events.get(row['id'])
        key = (row['created_at'], event[1] if event else 0, row['id'])
        matches.append((key, {'placements': json.loads(row['placements'])}))
    matches.sort(key=lambda item: item[0])
    return players, [{**match, 'elo_date': key[0], 'elo_match_id': key[2]} for key, match in matches]


def _displayed_elo_delta(rating, change):
    return round(rating) - round(rating - change)


class ManaFragments:
    PARTICIPATION_REWARD = 1
    WIN_REWARD = 2
    STREAK_REWARDS = {3: 10, 5: 20}
    TOURNAMENT_REWARD = 30

    def __init__(self, conn, matches):
        self.weekly = WeeklyMissions()
        self.first = self.last = None
        self.balances = {
            champion: count * self.TOURNAMENT_REWARD
            for champion, count in conn.execute("""
                SELECT champion_id, COUNT(*) FROM tournaments
                WHERE champion_id IS NOT NULL AND
                  (status = 'completed' OR (status = 'archived' AND archived_from = 'completed'))
                GROUP BY champion_id
            """)
        }
        self.streaks = {}
        # Draws award participation even though Elo skips them.
        for match in matches:
            if match.get('result') != 'draw' or match.get('bye') or match.get('void'):
                continue
            first, second = match.get('p1'), match.get('p2')
            if first and second and first != second:
                for player_id in (first, second):
                    self.balances[player_id] = self.balance(player_id) + self.PARTICIPATION_REWARD

    def before_game(self, rows, match):
        timestamp = match['elo_date']
        active = [row for row in rows.values() if row['created_at'] <= timestamp
                  and (row['inactive_at'] is None or timestamp < row['inactive_at'])]
        self.first = min(active, key=_elo_order)['id'] if active else None
        self.last = max(active, key=_elo_order)['id'] if active else None
        if match.get('result') == 'draw':
            for player_id, opponent in ((match['p1'], match['p2']), (match['p2'], match['p1'])):
                self.weekly.record(timestamp, player_id, {opponent}, first=self.first,
                                   last=self.last, tournament=match.get('tournament_match', False))

    def record(self, match, updates):
        placements = match.get('placements', [])
        placement_rewards = {pid: len(placements) - index for index, pid in enumerate(placements)}
        for player, _, won, *details in updates:
            player_id = player['id']
            if 'elo_date' in match:
                self.weekly.record(
                    match['elo_date'], player_id, {row['id'] for row in details[-1]},
                    won=won, first=self.first, last=self.last, tournament=match.get('tournament_match', False),
                    team=bool(match.get('team_a')),
                    podium=len(placements) >= 4 and player_id in placements[:3],
                )
            earned = placement_rewards.get(player_id, self.PARTICIPATION_REWARD)
            if won:
                earned += self.WIN_REWARD
            self.balances[player_id] = self.balances.get(player_id, 0) + earned
            if match.get('tournament_match'):
                continue
            streak = self.streaks.get(player_id, 0) + 1 if won else 0
            self.streaks[player_id] = streak
            if streak in self.STREAK_REWARDS:
                self.balances[player_id] += self.STREAK_REWARDS[streak]

    def balance(self, player_id):
        return self.balances.get(player_id, 0) + self.weekly.balance(player_id)


def match_elo_deltas(conn, ids):
    players, matches = _elo_inputs(conn)
    deltas = {}

    def record_day_match(_rows, match, updates):
        if match["elo_match_id"] in ids:
            deltas[match["elo_match_id"]] = {
                row["id"]: _displayed_elo_delta(row["rating"], change)
                for row, change, *_ in updates
            }

    calculate_global_elo(players, matches, on_game=record_day_match)
    return deltas


def global_elo_from_db(conn, *, include_movement=False):
    players, matches = _elo_inputs(conn)
    if not include_movement:
        return [row for row in calculate_global_elo(players, matches) if row['active']]

    fragments = ManaFragments(conn, matches)
    rome = ZoneInfo('Europe/Rome')
    day = None
    opening = {}

    def standings(rows):
        active = sorted((row for row in rows if row['active']), key=_elo_order)
        return {row['id']: (rank, round(row['rating'])) for rank, row in enumerate(active, 1)}

    def before_game(rows, match):
        nonlocal day, opening
        fragments.before_game(rows, match)
        timestamp = datetime.fromisoformat(match['elo_date'])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        match_day = timestamp.astimezone(rome).date()
        if match_day != day:
            opening, day = standings(rows.values()), match_day

    def after_game(_rows, match, updates):
        fragments.record(match, updates)

    rows = calculate_global_elo(players, matches, before_game=before_game, on_game=after_game)
    active = [row for row in rows if row['active']]
    for rank, row in enumerate(active, 1):
        pid = row['id']
        row['mana_fragments'] = fragments.balance(pid)
        row['movement'] = None
        if day is not None:
            opening_rank, opening_rating = opening[pid]
            rank_change = opening_rank - rank
            rating_change = row['rating'] - opening_rating
            if rank_change or rating_change:
                row['movement'] = {'date': day.isoformat(), 'rank': rank_change, 'rating': rating_change}
    return active


def _opponent_profile(row):
    return {
        'name': row['name'],
        'photo_url': f"/photos/{row['photo']}" if row['photo'] else None,
        'avatar_hue': row['avatar_hue'],
        'avatar_color': row['avatar_color'],
    }


def elo_snapshot(conn, player_ids):
    ids = set(player_ids)
    players, matches = _elo_inputs(conn)
    fragments = ManaFragments(conn, matches)
    rows = calculate_global_elo(players, matches, before_game=fragments.before_game,
                                on_game=lambda _rows, match, updates: fragments.record(match, updates))
    active = [row for row in rows if row['active']]
    return {
        row['id']: {
            'id': row['id'], 'name': row['name'], 'rating': row['rating'], 'rank': rank,
            'mana_fragments': fragments.balance(row['id']),
            'mission_fragments': fragments.weekly.balance(row['id']),
            'photo_url': f"/photos/{row['photo']}" if row['photo'] else None,
            'avatar_hue': row['avatar_hue'], 'avatar_color': row['avatar_color'],
        }
        for rank, row in enumerate(active, 1) if row['id'] in ids
    }


def elo_changes(conn, before):
    after = elo_snapshot(conn, before)
    return [
        {**row, 'previous_rating': before[pid]['rating'], 'previous_rank': before[pid]['rank'],
         'mana_fragments_delta': row['mana_fragments'] - before[pid]['mana_fragments'],
         'mission_fragments_delta': row['mission_fragments'] - before[pid]['mission_fragments']}
        for pid, row in after.items()
    ]


def player_statistics(conn, player_id):
    players, matches = _elo_inputs(conn)
    active_ids = [player['id'] for player in players if player['active']]
    if player_id not in active_ids:
        return None

    fragments = ManaFragments(conn, matches)
    history = []
    recent_results = []
    head_to_head = {}
    streak = longest_streak = 0
    best_win = None
    best_gap = float('-inf')
    reached_2400 = False

    def after_game(rows, match, updates):
        nonlocal streak, longest_streak, best_win, best_gap, reached_2400
        player = rows[player_id]
        order = _elo_order(player)
        rank = 1 + sum(_elo_order(rows[pid]) < order for pid in active_ids if pid != player_id)
        rating = round(player['rating'])
        date = match['elo_date']
        own = next((update for update in updates if update[0] is player), None)
        if own or (history and (rating, rank) != (history[-1]['rating'], history[-1]['rank'])):
            history.append({'date': date, 'rating': rating, 'rank': rank})
        if own:
            fragments.record(match, [own])
            reached_2400 = player['peak_2400']
            _, change, won, opponent_rating, opponents = own
            profiles = [_opponent_profile(opponent) for opponent in opponents]
            if match.get('p1'):
                opponent = {**profiles[0], 'id': opponents[0]['id']}
                record = head_to_head.setdefault(opponent['id'], {
                    'opponent': opponent, 'wins': 0, 'losses': 0,
                })
                record['wins' if won else 'losses'] += 1
            placements = match.get('placements')
            recent_results.append({
                'result': 'win' if won else 'loss',
                'opponents': profiles,
                'date': date,
                'elo_delta': _displayed_elo_delta(player['rating'], change),
            })
            if placements:
                recent_results[-1].update(position=placements.index(player_id) + 1, field_size=len(placements))
            if len(recent_results) > 5:
                recent_results.pop(0)
            streak = streak + 1 if won else 0
            longest_streak = max(longest_streak, streak)
            gap = opponent_rating - (player['rating'] - change)
            if won and gap > best_gap:
                best_gap = gap
                best_win = {'date': date, 'opponents': [profile['name'] for profile in profiles],
                            'opponent_profiles': profiles, 'gap': round(gap), 'gain': round(change)}

    rows = calculate_global_elo(players, matches, before_game=fragments.before_game, on_game=after_game)
    active = [row for row in rows if row['active']]
    rank, player = next((rank, row) for rank, row in enumerate(active, 1) if row['id'] == player_id)
    return {
        'player': {**player, 'rank': rank, 'mana_fragments': fragments.balance(player_id)},
        'weekly_missions': fragments.weekly.current(player_id),
        'history': history,
        'placement': {
            'played': player['matches'], 'total': ELO_PLACEMENT_MATCHES,
            'remaining': max(0, ELO_PLACEMENT_MATCHES - player['matches']),
            'next_k': _elo_factor({**player, 'peak_2400': reached_2400}),
            'established_k': _elo_factor({'matches': ELO_PLACEMENT_MATCHES, 'peak_2400': reached_2400}),
        },
        'peak_rating': max(1500, max((point['rating'] for point in history), default=1500)),
        'best_rank': min((point['rank'] for point in history), default=rank),
        'win_streak': streak, 'longest_win_streak': longest_streak,
        'recent_form': [match['result'] for match in recent_results],
        'recent_results': recent_results, 'best_win': best_win,
        'head_to_head': sorted(head_to_head.values(), key=lambda record: (
            -(record['wins'] + record['losses']),
            record['opponent']['name'].casefold(), record['opponent']['id'],
        ))
    }
