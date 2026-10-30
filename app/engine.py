"""Pure tournament rules for Mana Arena.

The engine deliberately accepts and returns plain dictionaries so it can be
used by the API and persistence layers without importing either one.
"""

from __future__ import annotations

from datetime import date
STRUCTURES = {"swiss", "adapted", "league", "single_elim"}
TIEBREAKERS = {"omw", "wins", "game_diff"}
RESULTS = {"p1_win", "draw", "p2_win", "p1_forfeit", "p2_forfeit", "bye"}


def validate_config(config: dict) -> None:
    """Validate supported tournament settings, raising an Italian message."""
    if not isinstance(config, dict):
        raise ValueError("La configurazione del torneo non è valida.")
    structure = config.get("structure")
    if not isinstance(structure, str) or structure not in STRUCTURES:
        raise ValueError("La struttura del torneo non è riconosciuta.")
    if not isinstance(config.get("name"), str) or not config["name"].strip():
        raise ValueError("Inserisci il nome del torneo.")
    if not isinstance(config.get("description", ""), str):
        raise ValueError("La descrizione del torneo non è valida.")
    event_date = config.get("date")
    if event_date is not None:
        try:
            date.fromisoformat(event_date)
        except (TypeError, ValueError):
            raise ValueError("La data deve essere nel formato AAAA-MM-GG.") from None
    if not isinstance(config.get("game_format"), str) or not config["game_format"].strip():
        raise ValueError("Seleziona un formato di gioco.")
    if not isinstance(config.get("match_length"), int) or isinstance(config.get("match_length"), bool) or config["match_length"] not in (1, 3):
        raise ValueError("La durata dell'incontro deve essere BO1 o BO3.")
    if config.get("seeding") not in ("manual", "random"):
        raise ValueError("Il tipo di seeding non è valido.")

    rounds = config.get("rounds")
    if structure == "swiss":
        if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 32:
            raise ValueError("I turni svizzeri devono essere compresi tra 1 e 32.")
    elif rounds is not None and (not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 32):
        raise ValueError("Il numero di turni deve essere compreso tra 1 e 32.")

    threshold = config.get("loss_threshold")
    if structure == "adapted":
        if not isinstance(threshold, int) or isinstance(threshold, bool) or not 2 <= threshold <= 5:
            raise ValueError("La soglia di sconfitte deve essere compresa tra 2 e 5.")
    elif threshold is not None and (not isinstance(threshold, int) or isinstance(threshold, bool) or not 2 <= threshold <= 5):
        raise ValueError("La soglia di sconfitte deve essere compresa tra 2 e 5.")

    league_mode = config.get("league_mode")
    if structure == "league":
        if league_mode not in ("single", "double"):
            raise ValueError("Scegli sola andata o andata e ritorno.")
    elif league_mode is not None and league_mode not in ("single", "double"):
        raise ValueError("La modalità del campionato non è valida.")

    allow_draws = config.get("allow_draws")
    if not isinstance(allow_draws, bool):
        raise ValueError("L'impostazione delle patte non è valida.")
    if allow_draws and structure in {"adapted", "single_elim"}:
        raise ValueError("Questa struttura richiede un vincitore e non ammette patte concluse.")
    points = config.get("points")
    if not isinstance(points, dict) or set(points) != {"win", "draw", "loss", "bye"}:
        raise ValueError("Configura i punti per vittoria, patta, sconfitta e bye.")
    for key, value in points.items():
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10:
            raise ValueError("I punti devono essere numeri interi compresi tra 0 e 10.")
    if points["win"] <= points["loss"]:
        raise ValueError("I punti per la vittoria devono essere maggiori di quelli per la sconfitta.")
    if allow_draws and not points["loss"] <= points["draw"] <= points["win"]:
        raise ValueError("I punti per la patta devono essere compresi tra quelli per la sconfitta e la vittoria.")
    if not isinstance(config.get("tiebreakers"), list):
        raise ValueError("L'ordine degli spareggi non è valido.")
    tie = config["tiebreakers"]
    if any(not isinstance(item, str) for item in tie) or len(set(tie)) != len(tie) or any(item not in TIEBREAKERS for item in tie):
        raise ValueError("Gli spareggi devono essere scelti senza duplicati tra OMW, vittorie e differenza game.")


def _ordered_entrants(entrants: list[dict], config: dict) -> list[dict]:
    rows = [dict(p) for p in entrants]
    ids = [p.get("id") for p in rows]
    if any(not isinstance(pid, str) or not pid for pid in ids) or len(ids) != len(set(ids)):
        raise ValueError("I partecipanti devono avere identificativi univoci.")
    for i, person in enumerate(rows):
        if person.get("seed") is None:
            person["seed"] = i + 1
    # Stable, reproducible ordering from input data; callers persist the
    # random draw as seeds/order when seeding is random.
    rows.sort(key=lambda p: (p["seed"], p["id"]))
    return rows


def _is_finalized(match: dict) -> bool:
    return match.get("result") is not None


def _history_stats(config: dict, entrants: list[dict], matches: list[dict]) -> dict[str, dict]:
    stats = {p["id"]: {"points": 0, "played_points": 0, "played_count": 0, "wins": 0, "draws": 0, "losses": 0, "byes": 0, "game_diff": 0, "opponents": [], "bye_count": 0} for p in entrants}
    for match in matches:
        if match.get("result") == "void":
            continue
        p1, p2 = match.get("p1"), match.get("p2")
        if p1 not in stats:
            continue
        s1 = stats[p1]
        result = match.get("result")
        if match.get("bye") or result == "bye":
            s1["bye_count"] += 1
            s1["byes"] += 1
            if config["structure"] == "league":
                continue
            s1["points"] += config["points"]["bye"]
            continue
        if p2 not in stats or result is None:
            continue
        s2 = stats[p2]
        is_forfeit = result in ("p1_forfeit", "p2_forfeit")
        if not is_forfeit:
            s1["opponents"].append(p2)
            s2["opponents"].append(p1)
            s1["played_count"] += 1
            s2["played_count"] += 1
        g1, g2 = int(match.get("p1_games", 0) or 0), int(match.get("p2_games", 0) or 0)
        if result not in ("p1_forfeit", "p2_forfeit"):
            s1["game_diff"] += g1 - g2
            s2["game_diff"] += g2 - g1
        if result in ("p1_win", "p2_forfeit"):
            s1["points"] += config["points"]["win"]
            s2["points"] += config["points"]["loss"]
            if not is_forfeit:
                s1["played_points"] += config["points"]["win"]
                s2["played_points"] += config["points"]["loss"]
            if result == "p1_win":
                s1["wins"] += 1
            s2["losses"] += 1
        elif result in ("p2_win", "p1_forfeit"):
            s2["points"] += config["points"]["win"]
            s1["points"] += config["points"]["loss"]
            if not is_forfeit:
                s2["played_points"] += config["points"]["win"]
                s1["played_points"] += config["points"]["loss"]
            if result == "p2_win":
                s2["wins"] += 1
            s1["losses"] += 1
        elif result == "draw":
            s1["points"] += config["points"]["draw"]
            s2["points"] += config["points"]["draw"]
            s1["played_points"] += config["points"]["draw"]
            s2["played_points"] += config["points"]["draw"]
            s1["draws"] += 1
            s2["draws"] += 1
    return stats


def _standing_order(config: dict, entrants: list[dict], matches: list[dict]) -> list[dict]:
    stats = _history_stats(config, entrants, matches)
    points_pct = {}
    for pid, stat in stats.items():
        points_pct[pid] = _match_points_pct(stat["played_points"], stat["played_count"], config)
    for pid, stat in stats.items():
        opponent_pcts = [points_pct[opp] for opp in stat["opponents"] if opp in points_pct]
        stat["omw"] = sum(opponent_pcts) / len(opponent_pcts) if opponent_pcts else 0.0
    def key(person: dict) -> tuple:
        stat = stats[person["id"]]
        values = [stat["points"]]
        for tie in config["tiebreakers"]:
            values.append(stat[tie])
        values.append(-(person.get("seed") if person.get("seed") is not None else 10**9))
        return tuple(values)
    ordered = sorted(entrants, key=key, reverse=True)
    return [{"entrant": p, **stats[p["id"]]} for p in ordered]


def _match_points_pct(points: int, played_count: int, config: dict) -> float:
    if played_count <= 0:
        return 0.0
    # The draw value is attainable only when draws are enabled; when enabled,
    # validation already constrains it to be no greater than the win value.
    maximum = config["points"]["win"]
    if maximum == 0:
        return 0.0
    return min(1.0, points / (played_count * maximum))


def _match_template(round_number: int, table: int, p1: str, p2: str | None, bye: bool = False) -> dict:
    return {"round": round_number, "table": table, "p1": p1, "p2": p2, "bye": bye}


def _has_played_pair(matches: list[dict]) -> set[frozenset[str]]:
    return {frozenset((m["p1"], m["p2"])) for m in matches if m.get("p1") is not None and m.get("p2") is not None}


def _pair_swiss(config: dict, field: list[dict], matches: list[dict], pair_field: list[dict] | None = None) -> tuple[list[tuple[str, str]], list[str]]:
    order = _standing_order(config, field, matches)
    stats = {row["entrant"]["id"]: row for row in order}
    prior_pairs = _has_played_pair(matches)
    pair_ids = {person["id"] for person in (pair_field if pair_field is not None else field)}
    remaining = [row["entrant"]["id"] for row in order if row["entrant"]["id"] in pair_ids]
    max_nodes = 40000
    nodes = 0

    def candidates(pid: str, pool: list[str]) -> list[str]:
        a = stats[pid]
        return sorted(pool, key=lambda other: (
            frozenset((pid, other)) in prior_pairs,
            abs(a["losses"] - stats[other]["losses"]) if config["structure"] == "adapted" else 0,
            abs(a["points"] - stats[other]["points"]),
            -stats[other]["points"],
            stats[other]["entrant"].get("seed", 10**9),
            other,
        ))

    # First seek a complete matching with no rematches. The bounded search is
    # fast on realistic fields and cannot grow without limit on pathological
    # history; a complete deterministic greedy fallback always follows.
    def search(pool: list[str]) -> list[tuple[str, str]] | None:
        nonlocal nodes
        if not pool:
            return []
        nodes += 1
        if nodes > max_nodes:
            return None
        # Minimum-remaining-values reduces branching for constrained graphs.
        first = min(pool, key=lambda pid: (sum(frozenset((pid, q)) not in prior_pairs for q in pool if q != pid), order.index(next(r for r in order if r["entrant"]["id"] == pid))))
        rest = [pid for pid in pool if pid != first]
        for other in candidates(first, rest):
            if frozenset((first, other)) in prior_pairs:
                continue
            answer = search([pid for pid in rest if pid != other])
            if answer is not None:
                return [(first, other), *answer]
        return None

    pairs = search(remaining)
    warnings = []
    if pairs is None:
        warnings.append("Rivincita inevitabile o ricerca limitata: pairing completo con compromessi applicati.")
        pool = remaining[:]
        pairs = []
        while len(pool) >= 2:
            first = pool.pop(0)
            other = min(pool, key=lambda pid: (
                frozenset((first, pid)) in prior_pairs,
                abs(stats[first]["losses"] - stats[pid]["losses"]) if config["structure"] == "adapted" else 0,
                abs(stats[first]["points"] - stats[pid]["points"]),
                stats[pid]["entrant"].get("seed", 10**9), pid,
            ))
            pool.remove(other)
            pairs.append((first, other))
    return pairs, warnings


def _select_bye(config: dict, field: list[dict], matches: list[dict], active: list[dict]) -> dict:
    stats = _history_stats(config, field, matches)
    standings = {r["entrant"]["id"]: r for r in _standing_order(config, field, matches)}
    return min(active, key=lambda p: (
        stats[p["id"]]["bye_count"],
        standings[p["id"]]["points"],
        -int(p.get("seed") or 10**9),
        p["id"],
    ))


def make_round(config: dict, entrants: list[dict], matches: list[dict], round_number: int) -> dict:
    """Create deterministic pairings for a Swiss, adapted, or elimination round."""
    validate_config(config)
    if not isinstance(round_number, int) or round_number < 1:
        raise ValueError("Il numero del turno non è valido.")
    field = _ordered_entrants(entrants, config)
    if not field:
        return {"matches": [], "warnings": []}
    prior = [m for m in matches if m.get("round", 0) < round_number]
    if any(m.get("round") == round_number for m in matches):
        return {"matches": [], "warnings": []}
    if config["structure"] == "single_elim":
        return _make_elimination_round(config, field, prior, round_number)
    if config["structure"] not in ("swiss", "adapted"):
        raise ValueError("Questa struttura usa un calendario iniziale, non turni svizzeri.")
    stats = _history_stats(config, field, prior)
    active = [p for p in field if not p.get("withdrawn") and not (
        config["structure"] == "adapted" and stats[p["id"]]["losses"] >= config["loss_threshold"]
    )]
    if config["structure"] == "swiss" and round_number > config["rounds"]:
        return {"matches": [], "warnings": []}
    if config["structure"] == "adapted" and len(active) <= 1:
        return {"matches": [], "warnings": []}
    if not active:
        return {"matches": [], "warnings": []}
    warnings = []
    bye_person = None
    if len(active) % 2:
        bye_person = _select_bye(config, field, prior, active)
        active = [p for p in active if p["id"] != bye_person["id"]]
    pairs, pairing_warnings = _pair_swiss(config, field, prior, pair_field=active)
    warnings.extend(pairing_warnings)
    round_matches = [_match_template(round_number, i + 1, a, b) for i, (a, b) in enumerate(pairs)]
    if bye_person:
        round_matches.append(_match_template(round_number, len(round_matches) + 1, bye_person["id"], None, True))
    return {"matches": round_matches, "warnings": warnings}


def _winner(match: dict) -> str | None:
    result = match.get("result")
    if result == "bye":
        return match.get("p1")
    if result in ("p1_win", "p2_forfeit"):
        return match.get("p1")
    if result in ("p2_win", "p1_forfeit"):
        return match.get("p2")
    return None


def _make_elimination_round(config: dict, field: list[dict], prior: list[dict], round_number: int) -> dict:
    if round_number == 1:
        return make_schedule(config, field)
    previous_rounds = [m.get("round", 0) for m in prior]
    if not previous_rounds:
        return {"matches": [], "warnings": []}
    last_round = max(previous_rounds)
    if last_round != round_number - 1:
        return {"matches": [], "warnings": []}
    previous = [m for m in prior if m.get("round") == last_round]
    if any(not _is_finalized(match) for match in previous):
        return {"matches": [], "warnings": []}
    slot_count = max((m.get("bracket_slot", -1) for m in previous), default=-1) + 1
    if slot_count <= 0 or slot_count % 2:
        return {"matches": [], "warnings": []}
    slots = [match.get("bracket_slot") for match in previous]
    if len(slots) != slot_count or set(slots) != set(range(slot_count)):
        return {"matches": [], "warnings": []}
    winners: list[str | None] = [None] * slot_count
    for match in previous:
        slot = match.get("bracket_slot")
        if not isinstance(slot, int) or not 0 <= slot < slot_count:
            return {"matches": [], "warnings": []}
        winner = _winner(match)
        winners[slot] = winner
    by_id = {p["id"]: p for p in field}
    result = []
    for slot in range(slot_count // 2):
        left, right = winners[slot * 2], winners[slot * 2 + 1]
        if left is None and right is None:
            result.append(_void_match(round_number, slot, slot))
            continue
        if left is None or right is None:
            survivor = left or right
            if by_id[survivor].get("withdrawn"):
                result.append(_void_match(round_number, slot, slot))
            else:
                result.append(_finalize_bye(config, round_number, slot, slot, survivor))
            continue
        left_withdrawn = bool(by_id[left].get("withdrawn"))
        right_withdrawn = bool(by_id[right].get("withdrawn"))
        if left_withdrawn and right_withdrawn:
            result.append(_void_match(round_number, slot, slot))
        elif left_withdrawn:
            result.append(_finalize_forfeit(config, round_number, slot, slot, left, right, "p1_forfeit"))
        elif right_withdrawn:
            result.append(_finalize_forfeit(config, round_number, slot, slot, left, right, "p2_forfeit"))
        else:
            result.append({**_match_template(round_number, slot + 1, left, right), "bracket_slot": slot})
    return {"matches": result, "warnings": []}


def _seed_order(size: int) -> list[int]:
    bracket = [1, 2]
    current = 2
    while current < size:
        bracket = [seed for position in bracket for seed in (position, current * 2 + 1 - position)]
        current *= 2
    return bracket


def _void_match(round_number: int, table: int, bracket_slot: int) -> dict:
    return {
        "round": round_number, "table": table + 1, "p1": None, "p2": None,
        "bye": False, "result": "void", "p1_games": 0, "p2_games": 0,
        "p1_points": 0, "p2_points": 0, "p1_wins": 0, "p2_wins": 0,
        "p1_draws": 0, "p2_draws": 0, "p1_losses": 0, "p2_losses": 0,
        "bracket_slot": bracket_slot,
    }


def _finalize_bye(config: dict, round_number: int, table: int, bracket_slot: int, player_id: str) -> dict:
    match = _match_template(round_number, table + 1, player_id, None, True)
    return {**apply_result(config, match, {"result": "bye", "p1_games": 0, "p2_games": 0}), "bracket_slot": bracket_slot}


def _finalize_forfeit(config: dict, round_number: int, table: int, bracket_slot: int, p1: str, p2: str, outcome: str) -> dict:
    match = _match_template(round_number, table + 1, p1, p2)
    return {**apply_result(config, match, {"result": outcome, "p1_games": 0, "p2_games": 0}), "bracket_slot": bracket_slot}


def make_schedule(config: dict, entrants: list[dict]) -> dict:
    """Create a league calendar or first round of a single-elimination bracket."""
    validate_config(config)
    field = _ordered_entrants(entrants, config)
    if config["structure"] == "league":
        return _league_schedule(config, field)
    if config["structure"] == "single_elim":
        return _elimination_schedule(config, field)
    if config["structure"] in ("swiss", "adapted"):
        return make_round(config, field, [], 1)
    return {"matches": [], "warnings": []}


def _league_schedule(config: dict, field: list[dict]) -> dict:
    if len(field) < 2:
        return {"matches": [], "warnings": []}
    ids: list[str | None] = [p["id"] for p in field]
    if len(ids) % 2:
        ids.append(None)
    count = len(ids)
    rounds = count - 1
    schedule = []
    for round_index in range(rounds):
        rotation = ids[:]
        for table in range(count // 2):
            a, b = rotation[table], rotation[count - 1 - table]
            if a is None or b is None:
                player = b or a
                if player is not None:
                    schedule.append({**_match_template(round_index + 1, table + 1, player, None, True), "league_bye": True})
            else:
                # Alternate home ordering, preserving a stable schedule.
                if round_index % 2:
                    a, b = b, a
                schedule.append(_match_template(round_index + 1, table + 1, a, b))
        ids = [ids[0], ids[-1], *ids[1:-1]]
    if config["league_mode"] == "double":
        extra = []
        for m in schedule:
            swapped = dict(m)
            if not m.get("bye"):
                swapped["p1"], swapped["p2"] = m["p2"], m["p1"]
            swapped["round"] = m["round"] + rounds
            extra.append(swapped)
        schedule.extend(extra)
    return {"matches": schedule, "warnings": []}


def _elimination_schedule(config: dict, field: list[dict]) -> dict:
    if len(field) < 2:
        return {"matches": [], "warnings": []}
    # Fill a standard seed-ordered binary bracket. Later stages retain each
    # match's slot, so winners stay on their original branch.
    size = 1 << (len(field) - 1).bit_length()
    by_seed = {rank: person for rank, person in enumerate(field, start=1)}
    seed_order = _seed_order(size)
    result = []
    for slot in range(size // 2):
        p1 = by_seed.get(seed_order[slot * 2])
        p2 = by_seed.get(seed_order[slot * 2 + 1])
        p1_id = p1["id"] if p1 else None
        p2_id = p2["id"] if p2 else None
        if p1_id is None and p2_id is None:
            match = _void_match(1, slot, slot)
        elif p1_id is None or p2_id is None:
            survivor = p1 or p2
            if survivor.get("withdrawn"):
                match = _void_match(1, slot, slot)
            else:
                match = _finalize_bye(config, 1, slot, slot, survivor["id"])
        elif p1.get("withdrawn") and p2.get("withdrawn"):
            match = _void_match(1, slot, slot)
        elif p1.get("withdrawn"):
            match = _finalize_forfeit(config, 1, slot, slot, p1_id, p2_id, "p1_forfeit")
        elif p2.get("withdrawn"):
            match = _finalize_forfeit(config, 1, slot, slot, p1_id, p2_id, "p2_forfeit")
        else:
            match = {**_match_template(1, slot + 1, p1_id, p2_id), "bracket_slot": slot}
        result.append(match)
    return {"matches": result, "warnings": []}


def apply_result(config: dict, match: dict, result: dict) -> dict:
    """Validate and finalize one match with canonical points/loss/game fields."""
    validate_config(config)
    if not isinstance(match, dict) or not isinstance(result, dict):
        raise ValueError("Il risultato dell'incontro non è valido.")
    p1, p2 = match.get("p1"), match.get("p2")
    is_bye = bool(match.get("bye"))
    outcome = result.get("result")
    if not isinstance(p1, str) or (not is_bye and not isinstance(p2, str)) or (is_bye and p2 is not None):
        raise ValueError("I partecipanti dell'incontro non sono validi.")
    if is_bye:
        if outcome != "bye":
            raise ValueError("Un bye deve essere registrato come bye.")
        if any(result.get(key, 0) != 0 for key in ("p1_games", "p2_games")):
            raise ValueError("Un bye non registra game giocati.")
        bye_points = 0 if config["structure"] == "league" else config["points"]["bye"]
        return {**match, "result": "bye", "p1_games": 0, "p2_games": 0,
                "p1_points": bye_points, "p2_points": 0, "p1_wins": 0, "p2_wins": 0,
                "p1_draws": 0, "p2_draws": 0, "p1_losses": 0, "p2_losses": 0}
    if not isinstance(outcome, str) or outcome not in RESULTS - {"bye"}:
        raise ValueError("Seleziona un risultato valido.")
    g1, g2 = result.get("p1_games"), result.get("p2_games")
    if not isinstance(g1, int) or isinstance(g1, bool) or not isinstance(g2, int) or isinstance(g2, bool) or g1 < 0 or g2 < 0:
        raise ValueError("Il numero di game deve essere un intero non negativo.")
    if outcome in ("p1_forfeit", "p2_forfeit"):
        if g1 != 0 or g2 != 0:
            raise ValueError("Un forfait non registra game giocati.")
    elif outcome == "draw":
        if not config["allow_draws"]:
            raise ValueError("Le patte non sono ammesse in questo torneo.")
        if g1 != g2 or g1 > 1:
            raise ValueError("Una patta BO3 può terminare solo sul punteggio 0-0 o 1-1.")
        if config["structure"] in ("adapted", "single_elim"):
            raise ValueError("Questo incontro deve avere un vincitore.")
    else:
        if config["match_length"] == 1:
            valid_score = (g1, g2) in ((1, 0), (0, 1))
        else:
            valid_score = (g1, g2) in ((2, 0), (2, 1), (0, 2), (1, 2))
        if not valid_score:
            raise ValueError("Il punteggio non è possibile per la durata dell'incontro.")
        if outcome in ("p1_win", "p1_forfeit") and outcome == "p1_win" and g1 <= g2:
            raise ValueError("Il punteggio non corrisponde alla vittoria del giocatore 1.")
        if outcome in ("p2_win", "p2_forfeit") and outcome == "p2_win" and g2 <= g1:
            raise ValueError("Il punteggio non corrisponde alla vittoria del giocatore 2.")
    if config["structure"] == "single_elim" and outcome == "draw":
        raise ValueError("L'eliminazione diretta richiede un vincitore.")
    p1_won = outcome in ("p1_win", "p2_forfeit")
    p2_won = outcome in ("p2_win", "p1_forfeit")
    is_draw = outcome == "draw"
    p1_points = config["points"]["draw"] if is_draw else config["points"]["win"] if p1_won else config["points"]["loss"]
    p2_points = config["points"]["draw"] if is_draw else config["points"]["win"] if p2_won else config["points"]["loss"]
    if outcome in ("p1_forfeit", "p2_forfeit"):
        target = 1 if config["match_length"] == 1 else 2
        g1, g2 = (0, target) if outcome == "p1_forfeit" else (target, 0)
    return {**match, "result": outcome, "p1_games": g1, "p2_games": g2,
            "p1_points": p1_points, "p2_points": p2_points,
            "p1_wins": int(p1_won and outcome not in ("p1_forfeit", "p2_forfeit")),
            "p2_wins": int(p2_won and outcome not in ("p1_forfeit", "p2_forfeit")),
            "p1_draws": int(is_draw), "p2_draws": int(is_draw),
            "p1_losses": int(p2_won), "p2_losses": int(p1_won)}


def calculate_standings(config: dict, entrants: list[dict], matches: list[dict]) -> list[dict]:
    """Calculate deterministic points standings, including inactive entrants.

    In single elimination, the champion is the winner propagated through the
    bracket's results and slots; rank 1 in this points-based table is not a
    champion signal.
    """
    validate_config(config)
    field = _ordered_entrants(entrants, config)
    stats = _history_stats(config, field, [m for m in matches if _is_finalized(m)])
    ordered = _standing_order(config, field, [m for m in matches if _is_finalized(m)])
    rows = []
    for rank, entry in enumerate(ordered, start=1):
        entrant = entry["entrant"]
        s = stats[entrant["id"]]
        rows.append({
            "player_id": entrant["id"], "seed": entrant.get("seed"),
            "points": s["points"], "wins": s["wins"], "draws": s["draws"],
            "losses": s["losses"], "byes": s["byes"], "game_diff": s["game_diff"],
            "omw": entry["omw"], "rank": rank,
            "eliminated": bool(entrant.get("withdrawn")) or (
                config["structure"] == "adapted" and s["losses"] >= config["loss_threshold"]
            ),
        })
    return rows
