"""Personal weekly missions replayed from finalized results."""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from itertools import combinations
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
WEEKLY_LIMIT = 60
# Earlier results remain outside the mission economy.
START_DATE = date(2026, 11, 26)


@dataclass(frozen=True)
class Mission:
    key: str
    title: str
    description: str
    reward: int
    target: int = 1


CATALOG = (
    Mission('first', 'Assalto al trono', 'Gioca contro il primo in classifica.', 15),
    Mission('last', 'Duello in coda', 'Gioca contro l’ultimo in classifica.', 5),
    Mission('tournament', 'Ingresso nell’arena', 'Completa almeno una partita di torneo.', 5),
    Mission('play_5', 'Cinque scintille', 'Completa 5 partite.', 10, 5),
    Mission('play_10', 'Maratoneta', 'Completa 10 partite.', 25, 10),
    Mission('win_3', "Non c'è due senza tre", 'Vinci contro 3 avversari diversi.', 15, 3),
    Mission('win_5', 'Cinquina arcana', 'Vinci contro 5 avversari diversi.', 20, 5),
    Mission('days', 'Tre giorni di magia', 'Completa almeno una partita in 3 giorni diversi.', 5, 3),
    Mission('opponents', 'Non ho paura di nessuno', 'Gioca contro 4 avversari diversi.', 10, 4),
    Mission('team', 'Squadra che vince non si cambia', 'Vinci una partita a squadre.', 10),
    Mission('podium', 'Caos su carte', 'Arriva tra i primi 3 in un tutti contro tutti con almeno 4 giocatori.', 10),
)


@lru_cache(maxsize=1)
def mission_sets():
    return tuple(group for size in range(1, len(CATALOG) + 1)
                 for group in combinations(CATALOG, size)
                 if sum(mission.reward for mission in group) == WEEKLY_LIMIT)


def local_date(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ROME).date()


def monday(day):
    return day - timedelta(days=day.weekday())


def week_start(value):
    return monday(local_date(value))


def assigned_missions(player_id, week):
    # Alternating pools ensure even a repeated random draw changes on Monday.
    parity = (week.toordinal() // 7) % 2
    groups = mission_sets()[parity::2]
    digest = sha256(f'weekly-missions:{player_id}:{week.isoformat()}'.encode()).digest()
    return groups[int.from_bytes(digest, 'big') % len(groups)]


@dataclass
class Progress:
    matches: int = 0
    days: set = field(default_factory=set)
    opponents: set = field(default_factory=set)
    defeated: set = field(default_factory=set)
    milestones: set = field(default_factory=set)

    def value(self, key):
        if key in ('play_5', 'play_10'):
            return self.matches
        if key in ('win_3', 'win_5'):
            return len(self.defeated)
        if key in ('days', 'opponents'):
            return len(getattr(self, key))
        return int(key in self.milestones)


class WeeklyMissions:
    def __init__(self):
        self.progress = {}
        self.balances = {}

    def record(self, timestamp, player_id, opponents, *, won=False, first=None, last=None,
               tournament=False, team=False, podium=False):
        day = local_date(datetime.fromisoformat(timestamp))
        if day < START_DATE:
            return
        week = monday(day)
        progress = self.progress.setdefault((player_id, week), Progress())
        missions = assigned_missions(player_id, week)
        incomplete = [mission for mission in missions if progress.value(mission.key) < mission.target]
        progress.matches += 1
        progress.days.add(day)
        progress.opponents.update(opponents)
        if won:
            progress.defeated.update(opponents)
        milestones = {'first': first in opponents, 'last': last in opponents, 'tournament': tournament,
                      'team': team and won, 'podium': podium}
        progress.milestones.update(key for key, achieved in milestones.items() if achieved)
        reward = sum(m.reward for m in incomplete if progress.value(m.key) >= m.target)
        self.balances[player_id] = self.balance(player_id) + reward

    def balance(self, player_id):
        return self.balances.get(player_id, 0)

    def current(self, player_id, *, now=None):
        week = week_start(now or datetime.now(timezone.utc))
        progress = self.progress.get((player_id, week), Progress())
        return [
            {'key': m.key, 'title': m.title, 'description': m.description, 'reward': m.reward,
             'target': m.target, 'progress': min(progress.value(m.key), m.target),
             'completed': progress.value(m.key) >= m.target,
             'tier': 'gold' if m.reward >= 20 else 'silver' if m.reward >= 10 else 'bronze'}
            for m in assigned_missions(player_id, week)
        ]
