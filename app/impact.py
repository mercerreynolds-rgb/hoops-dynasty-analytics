from __future__ import annotations

import re
from collections import defaultdict

from app.config import MY_TEAM


def safe_div(n: float, d: float) -> float:
    return n / d if d else 0.0


def clock_to_seconds(half: str | None, clock: str | None) -> int | None:
    if not clock or ":" not in clock:
        return None
    m, s = clock.split(":")
    remaining = int(m) * 60 + int(s)
    # first/second half both start at 20:00; for segment ordering within half we only need remaining
    return remaining


def parse_lineup(description: str) -> list[str]:
    # "PG - Randy Cope (fresh) | SG - Ralph Louie (fresh)"
    players = []
    for part in description.split("|"):
        part = part.strip()
        m = re.match(r"^(PG|SG|SF|PF|C)\s+-\s+(.+?)(?:\s+\(|$)", part, flags=re.I)
        if m:
            players.append(m.group(2).strip())
    return players


def parse_substitution(description: str) -> tuple[list[str], list[str]]:
    # "Norman Tobar (PG), Leonard Woods (SG) | Randy Cope, Ralph Louie"
    if "|" not in description:
        return [], []
    incoming_raw, outgoing_raw = description.split("|", 1)

    incoming = []
    for piece in incoming_raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        piece = re.sub(r"\s+\([A-Z]{1,2}\)$", "", piece).strip()
        if piece:
            incoming.append(piece)

    outgoing = []
    for piece in outgoing_raw.split(","):
        piece = piece.strip()
        if piece:
            outgoing.append(piece)

    return incoming, outgoing


def score_tuple(score: str | None) -> tuple[int, int] | None:
    if not score or "-" not in score:
        return None
    a, b = score.split("-", 1)
    if a.isdigit() and b.isdigit():
        return int(a), int(b)
    return None


def infer_points_scored(prev_score: tuple[int, int] | None, new_score: tuple[int, int] | None, team: str | None, teams: list[str]) -> tuple[str | None, int]:
    if not prev_score or not new_score or len(teams) < 2:
        return None, 0
    d0 = new_score[0] - prev_score[0]
    d1 = new_score[1] - prev_score[1]
    if d0 > 0:
        return teams[0], d0
    if d1 > 0:
        return teams[1], d1
    return None, 0


def classify_possession_end(event_type: str, desc: str, scoring_team: str | None, rebound_team: str | None, offense_team: str | None) -> bool:
    d = desc.lower()

    if event_type in {"turnover", "turnover_steal"}:
        return True

    if event_type == "made_field_goal":
        # And-1s are usually marked as foul event in this parser if text includes fouled.
        # A plain made FG ends possession.
        return True

    if event_type == "missed_free_throw":
        # Don't end until rebound. Final FT miss with defensive board ends on rebound.
        return False

    if event_type == "made_free_throw":
        # Most made FT lines are sequences; final made FT generally changes possession.
        # We approximate by ending if next event isn't another same-clock FT; handled in loop.
        return False

    if event_type == "rebound":
        # Defensive rebound ends possession; offensive rebound continues it.
        if rebound_team and offense_team and rebound_team != offense_team:
            return True
        return False

    return False


def calculate_game_impacts(game_id: int, stats: list, events: list, teams: list[str]) -> tuple[list[dict], list[dict]]:
    """
    Builds lineup segments and ECSU on/off player impact.

    This is a practical v1 possession model:
    - Tracks lineups from Lineup and Subs events
    - Points are inferred from score changes
    - Possessions end on turnovers, made FGs, and defensive rebounds
    - Offensive rebounds extend the same possession
    - Free throw trips end when the sequence score changes and the next possession begins;
      this v1 treats FT-only trips conservatively via the next terminal event.
    """
    my_team = MY_TEAM
    current_lineups: dict[str, list[str]] = {t: [] for t in teams}
    last_score = (0, 0)
    offense_team = None

    # Running segment state per my team lineup only
    segment_number = 0
    active_segment = None
    segments = []

    def close_segment(end_clock=None):
        nonlocal active_segment
        if active_segment:
            active_segment["end_clock"] = end_clock
            segments.append(active_segment)
            active_segment = None

    def open_segment(event):
        nonlocal active_segment, segment_number
        lineup = current_lineups.get(my_team, [])
        if len(lineup) == 5:
            segment_number += 1
            active_segment = {
                "game_id": game_id,
                "segment_number": segment_number,
                "half": getattr(event, "half", None),
                "start_clock": getattr(event, "clock", None),
                "end_clock": None,
                "team": my_team,
                "lineup": ", ".join(lineup),
                "points_for": 0,
                "points_against": 0,
                "possessions_for": 0,
                "possessions_against": 0,
            }

    def reset_segment(event):
        close_segment(getattr(event, "clock", None))
        open_segment(event)

    for ev in events:
        et = ev.event_type
        team = ev.team
        desc = ev.description or ""

        if et == "lineup" and team in current_lineups:
            lineup = parse_lineup(desc)
            if lineup:
                current_lineups[team] = lineup
                if team == my_team:
                    reset_segment(ev)
            continue

        if et == "substitution" and team in current_lineups:
            incoming, outgoing = parse_substitution(desc)
            lineup = current_lineups.get(team, []).copy()
            for outp in outgoing:
                if outp in lineup:
                    lineup.remove(outp)
            for inp in incoming:
                if inp and inp not in lineup and len(lineup) < 5:
                    lineup.append(inp)
            current_lineups[team] = lineup
            if team == my_team:
                reset_segment(ev)
            continue

        if not active_segment:
            open_segment(ev)

        new_score = score_tuple(ev.score)
        scoring_team, pts = infer_points_scored(last_score, new_score, team, teams)
        if pts and active_segment:
            if scoring_team == my_team:
                active_segment["points_for"] += pts
            else:
                active_segment["points_against"] += pts

        # possession team inference
        if team in teams and et not in {"rebound", "block", "foul"}:
            offense_team = team

        possession_end = False
        if et in {"turnover", "turnover_steal"}:
            possession_end = True
            end_team = team
        elif et == "made_field_goal":
            possession_end = True
            end_team = scoring_team or team
        elif et == "rebound":
            rebound_team = team
            if offense_team and rebound_team and rebound_team != offense_team:
                possession_end = True
                end_team = offense_team
            else:
                end_team = None
        else:
            end_team = None

        if possession_end and active_segment and end_team:
            if end_team == my_team:
                active_segment["possessions_for"] += 1
            else:
                active_segment["possessions_against"] += 1
            # change possession
            if len(teams) == 2:
                offense_team = teams[1] if end_team == teams[0] else teams[0]

        if new_score:
            last_score = new_score

    close_segment(None)

    # Build per-player on/off from my team box score players
    my_players = [s.player for s in stats if s.team == my_team]
    impacts = []

    total_pf = sum(s["points_for"] for s in segments)
    total_pa = sum(s["points_against"] for s in segments)
    total_poss_for = sum(s["possessions_for"] for s in segments)
    total_poss_against = sum(s["possessions_against"] for s in segments)

    for player in my_players:
        on = [s for s in segments if player in [p.strip() for p in s["lineup"].split(",")]]
        off = [s for s in segments if player not in [p.strip() for p in s["lineup"].split(",")]]

        on_pf = sum(s["points_for"] for s in on)
        on_pa = sum(s["points_against"] for s in on)
        on_poss_for = sum(s["possessions_for"] for s in on)
        on_poss_against = sum(s["possessions_against"] for s in on)

        off_pf = sum(s["points_for"] for s in off)
        off_pa = sum(s["points_against"] for s in off)
        off_poss_for = sum(s["possessions_for"] for s in off)
        off_poss_against = sum(s["possessions_against"] for s in off)

        on_off_eff = safe_div(on_pf, on_poss_for) * 100
        off_off_eff = safe_div(off_pf, off_poss_for) * 100
        on_def_eff = safe_div(on_pa, on_poss_against) * 100
        off_def_eff = safe_div(off_pa, off_poss_against) * 100

        impacts.append({
            "game_id": game_id,
            "player": player,
            "team": my_team,
            "on_possessions_for": on_poss_for,
            "on_points_for": on_pf,
            "on_possessions_against": on_poss_against,
            "on_points_against": on_pa,
            "off_possessions_for": off_poss_for,
            "off_points_for": off_pf,
            "off_possessions_against": off_poss_against,
            "off_points_against": off_pa,
            "on_off_eff": round(on_off_eff, 3),
            "off_off_eff": round(off_off_eff, 3),
            "on_def_eff": round(on_def_eff, 3),
            "off_def_eff": round(off_def_eff, 3),
            # scale impact down to BPR-sized numbers: 10 efficiency points = 1 rating point.
            "off_impact": round((on_off_eff - off_off_eff) / 10, 3),
            "def_impact": round((off_def_eff - on_def_eff) / 10, 3),
        })

    return segments, impacts
