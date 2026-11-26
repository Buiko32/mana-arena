from datetime import datetime, timedelta, timezone

from app.db import Database
from app.service import elo_changes, elo_snapshot, global_elo_from_db, player_statistics
from app.weekly_missions import WeeklyMissions, assigned_missions, week_start


def test_assignments_are_personal_stable_and_pay_at_most_sixty():
    week = week_start(datetime(2026, 11, 29, 23, tzinfo=timezone.utc))
    assert week.isoformat() == '2026-11-30'
    assignments = [assigned_missions(str(pid), week) for pid in range(20)]
    assert len(set(assignments)) > 1
    assert all(sum(m.reward for m in missions) == 60 for missions in assignments)
    assert assignments[0] == assigned_missions('0', week)
    assert all(group != assigned_missions(str(pid), week + timedelta(days=7))
               for pid, group in enumerate(assignments))


def test_progress_rolls_over_at_rome_midnight_and_rewards_are_capped():
    tracker = WeeklyMissions()
    before = '2026-12-06 22:59:59'
    week = week_start(datetime.fromisoformat(before).replace(tzinfo=timezone.utc))
    player = next(str(pid) for pid in range(100)
                  if any(m.key == 'play_5' for m in assigned_missions(str(pid), week)))
    for _ in range(15):
        tracker.record(before, player, {'a'}, won=True)
    visible = tracker.current(player, now=datetime(2026, 12, 6, 22, tzinfo=timezone.utc))
    assert next(m for m in visible if m['key'] == 'play_5')['progress'] == 5
    earned = tracker.balance(player)
    assert 10 <= earned <= 60
    assert all(m['progress'] == 0 for m in tracker.current(
        player, now=datetime(2026, 12, 6, 23, tzinfo=timezone.utc)))
    assert tracker.balance(player) == earned


def test_fifth_match_includes_both_missions_in_summary_and_balance(tmp_path, monkeypatch):
    now = datetime(2026, 12, 1, 12, tzinfo=timezone.utc)
    week = week_start(now)
    player_id = next(f'p{pid}' for pid in range(1000)
                     if {'play_5', 'win_5'} <= {m.key for m in assigned_missions(f'p{pid}', week)})
    opponents = [f'o{number}' for number in range(5)]
    db = Database(tmp_path)
    db.initialize()
    current = WeeklyMissions.current
    monkeypatch.setattr(WeeklyMissions, 'current', lambda self, pid: current(self, pid, now=now))
    with db.transaction() as conn:
        conn.executemany("INSERT INTO players (id, name, created_at) VALUES (?, ?, '2026-11-30 00:00:00')",
                         [(pid, pid) for pid in [player_id, *opponents]])

        def finish(number):
            conn.execute("""
                INSERT INTO result_proposals (id, proposer_id, opponent_id, winner_id, status, created_at)
                VALUES (?, ?, ?, ?, 'confirmed', ?)
            """, (str(number), player_id, opponents[number], player_id, f'2026-12-01 11:0{number}:00'))

        for number in range(4):
            finish(number)
        before = elo_snapshot(conn, [player_id])
        finish(4)
        result, = elo_changes(conn, before)
        assert result['mission_fragments_delta'] == 30
        assert result['mana_fragments_delta'] == 53  # two missions, participation, win, five-win streak
        ranking = {row['id']: row for row in global_elo_from_db(conn, include_movement=True)}
        profile = player_statistics(conn, player_id)
        assert profile['player']['mana_fragments'] == ranking[player_id]['mana_fragments'] == result['mana_fragments']
        assert {m['key'] for m in profile['weekly_missions'] if m['completed']} >= {'play_5', 'win_5'}
