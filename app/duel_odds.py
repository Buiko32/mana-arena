"""Conservative duel probabilities for the initial Mana betting rollout."""
from collections import defaultdict, deque
from datetime import datetime, timezone
from math import exp, log, log1p

from .service import calculate_global_elo

MODEL_VERSION = "duel-conservative-1"
HALF_LIFE_DAYS = 90
ELO_SCALE = log(10) / 400


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def elo_log_odds(rating_gap):
    return clamp(rating_gap, -800, 800) * ELO_SCALE


def logistic(log_odds):
    return 1 / (1 + exp(-log_odds))


def prediction(log_odds):
    percent = round(100 * logistic(log_odds))
    return [percent, 100 - percent]


def prematch_strength(elo_gap, matches_a, matches_b, form_a, form_b, direct):
    # Temper Elo, especially when either player's history is sparse.
    evidence = min(matches_a, matches_b)
    elo_weight = 0.5 * evidence / (evidence + 20)
    return elo_weight * elo_log_odds(elo_gap) + 0.25 * (form_a - form_b) + 0.25 * direct


def live_adjustment(initial_a, life_a, initial_b, life_b):
    # A life lead is informative but cannot establish a won game on its own.
    a = max(0, life_a) / initial_a
    b = max(0, life_b) / initial_b
    base = clamp(0.8 * (log1p(4 * a) - log1p(4 * b)), -1.3, 1.3)
    life_gap = a - b
    # Require a substantial relative lead before rewarding proximity to lethal.
    lead = clamp((abs(life_gap) - 0.25) / 0.65, 0, 1)
    if life_gap > 0:
        return base + 0.7 * lead * clamp((4 - life_b) / 3, 0, 1)
    return base - 0.7 * lead * clamp((4 - life_a) / 3, 0, 1)


def historical_evidence(players, matches, *, now=None):
    """Replay official Elo and measure duel results against pre-match expectations."""
    now = now or datetime.now(timezone.utc)
    recent = defaultdict(lambda: deque(maxlen=10))
    direct = defaultdict(lambda: (0.0, 0.0))

    def observe(rows, match):
        if match.get("team_a") or match.get("placements"):
            return
        a, b = sorted((match["p1"], match["p2"]))
        outcome = match["result"]
        score = 0.5 if outcome == "draw" else float(
            a == match["p1" if outcome == "p1_win" else "p2"]
        )
        expected = logistic(elo_log_odds(rows[a]["rating"] - rows[b]["rating"]))
        residual = score - expected
        recent[a].append(residual)
        recent[b].append(-residual)
        played = datetime.fromisoformat(match["elo_date"])
        if played.tzinfo is None:
            played = played.replace(tzinfo=timezone.utc)
        age_days = max(0, (now - played).total_seconds() / 86400)
        weight = 2 ** (-age_days / HALF_LIFE_DAYS)
        total, count = direct[a, b]
        direct[a, b] = total + weight * residual, count + weight

    ranking = {row["id"]: row for row in calculate_global_elo(players, matches, before_game=observe)}
    form = {pid: sum(values) / (len(values) + 6) for pid, values in recent.items()}
    head_to_head = {pair: total / (count + 8) for pair, (total, count) in direct.items()}
    return ranking, form, head_to_head


def duel_strength(ids, evidence):
    a, b = ids
    ranking, form, direct = evidence
    if a not in ranking or b not in ranking:
        return None
    pair = tuple(sorted(ids))
    head_to_head = direct.get(pair, 0) * (1 if a == pair[0] else -1)
    return prematch_strength(
        ranking[a]["rating"] - ranking[b]["rating"],
        ranking[a]["matches"], ranking[b]["matches"],
        form.get(a, 0), form.get(b, 0), head_to_head,
    )
