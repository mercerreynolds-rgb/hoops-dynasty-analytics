from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from app.wis_client import wis_get
from bs4 import BeautifulSoup


LAST_FETCH_DEBUG = {
    "url": "",
    "final_url": "",
    "status_code": None,
    "html_length": 0,
    "text_preview": "",
    "contains_final": False,
    "contains_time_team_play_score": False,
    "contains_split_pbp_header": False,
    "contains_boxscore_title": False,
}


@dataclass
class ParsedGame:
    source_url: str
    wis_game_id: str | None
    summary_rows: list[dict]
    boxscore_rows: list[dict]
    pbp_rows: list[dict]
    debug: dict


def fetch_html(url: str) -> str:
    # Use authenticated WIS client when WIS_COOKIE or WIS_USERNAME/WIS_PASSWORD
    # are configured; otherwise it behaves like a normal browser-like GET.
    r = wis_get(url)

    LAST_FETCH_DEBUG["url"] = url
    LAST_FETCH_DEBUG["final_url"] = str(r.url)
    LAST_FETCH_DEBUG["status_code"] = r.status_code
    LAST_FETCH_DEBUG["html_length"] = len(r.text)
    return r.text


def clean_lines_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()
        if line:
            lines.append(line)

    joined = "\n".join(lines)
    LAST_FETCH_DEBUG["text_preview"] = joined[:3000]
    LAST_FETCH_DEBUG["contains_final"] = "Final" in joined
    LAST_FETCH_DEBUG["contains_time_team_play_score"] = "Time Team Play Score" in joined
    LAST_FETCH_DEBUG["contains_split_pbp_header"] = contains_sequence(lines, ["Time", "Team", "Play", "Score"])
    LAST_FETCH_DEBUG["contains_boxscore_title"] = "Game Boxscore" in joined or "Boxscore" in joined
    print("FETCH DEBUG:", LAST_FETCH_DEBUG, flush=True)

    return lines


def contains_sequence(lines: list[str], seq: list[str]) -> bool:
    n = len(seq)
    return any(lines[i:i+n] == seq for i in range(0, max(len(lines) - n + 1, 0)))


def extract_wis_game_id(url: str) -> str | None:
    m = re.search(r"[?&]gid=(\d+)", url)
    return m.group(1) if m else None


def is_record_header(line: str) -> bool:
    return bool(re.match(r"^.+\(\d+-\d+,\s*\d+-\d+\)$", line))



def parse_team_coaches(lines: list[str], team_names: list[str]) -> dict[str, str | None]:
    coaches = {team: None for team in team_names}
    final_idx = lines.index("Final") if "Final" in lines else min(len(lines), 80)
    header = lines[:final_idx]

    for team in team_names:
        for i, line in enumerate(header):
            if line == team and i + 1 < len(header):
                candidate = header[i + 1]
                if not candidate.startswith("#") and candidate not in team_names:
                    coaches[team] = candidate
                break

    return coaches

def parse_scoreboard(lines: list[str]) -> list[dict]:
    date = None
    for i, line in enumerate(lines):
        if line == "Final" and i + 2 < len(lines) and lines[i + 1] == "-":
            if re.match(r"\d{1,2}/\d{1,2}/\d{4}", lines[i + 2]):
                date = lines[i + 2]
                break

    rows = []
    # Pattern from fetched text:
    # Final - date 88 1 2 F #6 E. Conn 42 46 88 #16 Whitworth 54 44 98 98
    for i in range(len(lines) - 4):
        if re.match(r"^#?\d*", lines[i]) is not None:
            m = re.match(r"^(#\d+\s+)?(?P<team>.+)$", lines[i])
            if (
                m
                and i + 3 < len(lines)
                and lines[i + 1].isdigit()
                and lines[i + 2].isdigit()
                and lines[i + 3].isdigit()
            ):
                team = m.group("team").strip()
                # Avoid grabbing player stat rows or junk.
                if (
                    team
                    and not team.isdigit()
                    and not team.lower() in {"min", "fgm-a", "points"}
                    and not re.match(r"^(c|pf|sf|sg|pg)$", team, flags=re.I)
                    and len(rows) < 2
                    and (team.startswith("#") or any(ch.isalpha() for ch in team))
                ):
                    # Strip ranking if present.
                    team = re.sub(r"^#\d+\s+", "", team)
                    if i > 0 and lines[i-1] == "F":
                        pass
                    rows.append({
                        "date": date,
                        "team": team,
                        "half1": int(lines[i + 1]),
                        "half2": int(lines[i + 2]),
                        "final": int(lines[i + 3]),
                    })
        if len(rows) >= 2:
            rows = rows[:2]
    coaches = parse_team_coaches(lines, [r["team"] for r in rows])
    for r in rows:
        r["coach"] = coaches.get(r["team"])
    return rows

    # More targeted fallback after F marker.
    try:
        f_idx = lines.index("F")
        i = f_idx + 1
        while i < len(lines) - 3 and len(rows) < 2:
            if lines[i].startswith("#") and lines[i+1].isdigit() and lines[i+2].isdigit() and lines[i+3].isdigit():
                rows.append({
                    "date": date,
                    "team": re.sub(r"^#\d+\s+", "", lines[i]),
                    "half1": int(lines[i+1]),
                    "half2": int(lines[i+2]),
                    "final": int(lines[i+3]),
                })
                i += 4
            else:
                i += 1
    except ValueError:
        pass

    return rows[:2]



COMPACT_PLAYER_ROW_RE = re.compile(
    r"^(?P<pos>c|pf|sf|sg|pg)\s*"
    r"(?P<player>.+?)\s+"
    r"(?P<min>\d+)\s+"
    r"(?P<fg>\d+-\d+)\s+"
    r"(?P<fg3>\d+-\d+)\s+"
    r"(?P<ft>\d+-\d+)\s+"
    r"(?P<orb>\d+)\s+"
    r"(?P<reb>\d+)\s+"
    r"(?P<ast>\d+)\s+"
    r"(?P<to>\d+)\s+"
    r"(?P<stl>\d+)\s+"
    r"(?P<blk>\d+)\s+"
    r"(?P<pf>\d+)\s+"
    r"(?P<pts>\d+)$",
    flags=re.I,
)


def parse_compact_player_stat_line(line: str, team: str, role: str) -> dict | None:
    m = COMPACT_PLAYER_ROW_RE.match(line)
    if not m:
        return None

    fgm, fga = m["fg"].split("-")
    fg3m, fg3a = m["fg3"].split("-")
    ftm, fta = m["ft"].split("-")

    return {
        "team": team,
        "role": role,
        "pos": m["pos"].lower(),
        "player": m["player"].strip(),
        "minutes": int(m["min"]),
        "fgm": int(fgm),
        "fga": int(fga),
        "fg3m": int(fg3m),
        "fg3a": int(fg3a),
        "ftm": int(ftm),
        "fta": int(fta),
        "orb": int(m["orb"]),
        "reb": int(m["reb"]),
        "ast": int(m["ast"]),
        "tov": int(m["to"]),
        "stl": int(m["stl"]),
        "blk": int(m["blk"]),
        "pf": int(m["pf"]),
        "pts": int(m["pts"]),
    }


def parse_boxscore(lines: list[str]) -> list[dict]:
    """
    Hybrid WIS box-score parser.

    WhatIfSports can render player stats in two different text shapes:
      1) compact rows:
         c Norman Brown 18 4-10 0-0 ...
      2) cell-by-cell rows:
         c
         Norman Brown
         18
         4-10
         ...

    This supports both, which fixes Redlands/Knight pages that return compact rows.
    """
    rows = []
    current_team = None
    current_role = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Team header examples:
        # Redlands (9-0)
        # E. Connecticut St. (22-6, 15-1)
        team_header = re.match(r"^(.+?)\s+\(\d+-\d+(?:,\s*\d+-\d+)?\)$", line)
        if team_header:
            current_team = team_header.group(1).strip()
            current_role = None
            i += 1
            continue

        if line.startswith("STARTERS"):
            current_role = "starter"
            i += 1
            continue

        if line.startswith("BENCH"):
            current_role = "bench"
            i += 1
            continue

        if line.startswith("Totals"):
            current_role = None
            i += 1
            continue

        # Format 1: compact player row.
        if current_team and current_role:
            compact = parse_compact_player_stat_line(line, current_team, current_role)
            if compact:
                rows.append(compact)
                i += 1
                continue

        # Format 2: cell-by-cell player row.
        if (
            current_team
            and current_role
            and re.match(r"^(c|pf|sf|sg|pg)$", line, flags=re.I)
            and i + 13 < len(lines)
            and re.match(r"^\d+$", lines[i + 2].strip())
            and re.match(r"^\d+-\d+$", lines[i + 3].strip())
            and re.match(r"^\d+-\d+$", lines[i + 4].strip())
            and re.match(r"^\d+-\d+$", lines[i + 5].strip())
        ):
            row = {
                "team": current_team,
                "role": current_role,
                "pos": lines[i].strip().lower(),
                "player": lines[i + 1].strip(),
                "minutes": int(lines[i + 2]),
                "fgm": int(lines[i + 3].split("-")[0]),
                "fga": int(lines[i + 3].split("-")[1]),
                "fg3m": int(lines[i + 4].split("-")[0]),
                "fg3a": int(lines[i + 4].split("-")[1]),
                "ftm": int(lines[i + 5].split("-")[0]),
                "fta": int(lines[i + 5].split("-")[1]),
                "orb": int(lines[i + 6]),
                "reb": int(lines[i + 7]),
                "ast": int(lines[i + 8]),
                "tov": int(lines[i + 9]),
                "stl": int(lines[i + 10]),
                "blk": int(lines[i + 11]),
                "pf": int(lines[i + 12]),
                "pts": int(lines[i + 13]),
            }
            rows.append(row)
            i += 14
            continue

        i += 1

    print("BOX PARSER DEBUG:", {"rows": len(rows), "teams": sorted(set(r["team"] for r in rows))}, flush=True)
    return rows


def classify_event(description: str) -> str:
    d = description.lower()
    if d.startswith("subs ") or d == "subs" or "subs " in d:
        return "substitution"
    if "lineup " in d or d.startswith("lineup"):
        return "lineup"
    if "wins the tip" in d:
        return "tipoff"
    if "free throw" in d and ("hits" in d or "makes" in d):
        return "made_free_throw"
    if "free throw" in d and ("misses" in d or "clanks" in d):
        return "missed_free_throw"
    if any(w in d for w in ["hits", "drills", "buries", "knocks in", "scores on", "tips it in", "lays it in"]):
        return "made_field_goal"
    if any(w in d for w in ["misses", "clanks", "comes up short", "can't connect"]):
        return "missed_field_goal"
    if "rebound" in d or "grabs the board" in d or "pulls down" in d:
        return "rebound"
    if "foul" in d or "fouled" in d or "called for" in d or "hacked" in d:
        return "foul"
    if "steal" in d or "picks off" in d or "intercepted" in d:
        return "turnover_steal"
    if "throws a bad pass" in d or "out of bounds" in d:
        return "turnover"
    if "swats" in d or "block" in d:
        return "block"
    return "other"


def find_pbp_start(lines: list[str]) -> int | None:
    for i in range(len(lines) - 3):
        if lines[i:i+4] == ["Time", "Team", "Play", "Score"]:
            return i + 4
    for i, line in enumerate(lines):
        if line == "Time Team Play Score":
            return i + 1
    return None


def parse_lineup_block(lines: list[str], i: int, half: str, event_number: int) -> tuple[dict, int]:
    # Expected:
    # Lineup, Team, PG -, Name, (fresh), SG -, Name...
    team = lines[i + 1] if i + 1 < len(lines) else None
    details = []
    j = i + 2
    while j + 1 < len(lines):
        if re.match(r"^\d{1,2}:\d{2}$", lines[j]) or lines[j] in {"Lineup", "Subs", "Game Plan", "1st Half", "2nd Half"}:
            break
        if re.match(r"^(PG|SG|SF|PF|C)\s*-$", lines[j], flags=re.I):
            pos = lines[j].replace("-", "").strip()
            player = lines[j + 1] if j + 1 < len(lines) else ""
            status = lines[j + 2] if j + 2 < len(lines) and lines[j + 2].startswith("(") else ""
            details.append(f"{pos} - {player} {status}".strip())
            j += 3 if status else 2
            continue
        j += 1

    return {
        "event_number": event_number,
        "half": half,
        "clock": None,
        "team": team,
        "description": " | ".join(details),
        "score": None,
        "event_type": "lineup",
    }, j


def parse_subs_block(lines: list[str], i: int, half: str, event_number: int) -> tuple[dict, int]:
    team = lines[i + 1] if i + 1 < len(lines) else None
    details = []
    j = i + 2
    while j < len(lines):
        if re.match(r"^\d{1,2}:\d{2}$", lines[j]) or lines[j] in {"Lineup", "Subs", "Game Plan", "1st Half", "2nd Half"}:
            break
        details.append(lines[j])
        j += 1
    return {
        "event_number": event_number,
        "half": half,
        "clock": None,
        "team": team,
        "description": " | ".join(details),
        "score": None,
        "event_type": "substitution",
    }, j


def parse_play_by_play(lines: list[str], known_teams: list[str]) -> list[dict]:
    rows = []
    start = find_pbp_start(lines)
    if start is None:
        return rows

    half = "1st Half"
    i = start

    while i < len(lines):
        line = lines[i]

        if line in {"1st Half", "2nd Half"}:
            half = line
            i += 1
            continue

        if line == "Lineup":
            event, i = parse_lineup_block(lines, i, half, len(rows) + 1)
            rows.append(event)
            continue

        if line == "Subs":
            event, i = parse_subs_block(lines, i, half, len(rows) + 1)
            rows.append(event)
            continue

        if line == "Game Plan":
            team = lines[i + 1] if i + 1 < len(lines) else None
            desc = lines[i + 2] if i + 2 < len(lines) else ""
            rows.append({
                "event_number": len(rows) + 1,
                "half": half,
                "clock": None,
                "team": team,
                "description": desc,
                "score": None,
                "event_type": "game_plan",
            })
            i += 3
            continue

        # Timed event is cell-by-cell: time, team, play, score.
        if re.match(r"^\d{1,2}:\d{2}$", line) and i + 3 < len(lines):
            clock = lines[i]
            team = lines[i + 1]
            desc = lines[i + 2]
            score = lines[i + 3] if re.match(r"^\d+-\d+$", lines[i + 3]) else None
            rows.append({
                "event_number": len(rows) + 1,
                "half": half,
                "clock": clock,
                "team": team,
                "description": desc,
                "score": score,
                "event_type": classify_event(desc),
            })
            i += 4 if score else 3
            continue

        i += 1

    return rows


def parse_game_url(url: str) -> ParsedGame:
    html = fetch_html(url)
    lines = clean_lines_from_html(html)

    summary = parse_scoreboard(lines)
    teams = [r["team"] for r in summary]
    boxscore = parse_boxscore(lines)
    if not teams:
        teams = sorted(set(row["team"] for row in boxscore))
    pbp = parse_play_by_play(lines, teams)

    print("PARSE COUNTS:", {"summary": len(summary), "box": len(boxscore), "pbp": len(pbp)}, flush=True)

    return ParsedGame(
        source_url=url,
        wis_game_id=extract_wis_game_id(url),
        summary_rows=summary,
        boxscore_rows=boxscore,
        pbp_rows=pbp,
        debug=dict(LAST_FETCH_DEBUG),
    )
def parse_game_log_url(url: str) -> dict:
    """
    Parse a WIS TeamProfile/GameLog.aspx page and return unique box score URLs.

    Returns:
      {
        "source_url": url,
        "team": detected team name if available,
        "world": detected world if available,
        "game_urls": [...]
      }
    """
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text_lines = clean_lines_from_html(html)

    # Heuristic detection from page title/text.
    joined = "\n".join(text_lines[:200])
    team = None
    world = None

    # Known tracked teams/worlds.
    known = [
        ("E. Connecticut St.", "Phelan"),
        ("CSU, Eastbay", "Tarkanian"),
        ("Redlands", "Knight"),
    ]
    for t, w in known:
        if t in joined:
            team = t
        if w in joined:
            world = w

    game_ids = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "GameResults/BoxScore.aspx" in href and "gid=" in href:
            m = re.search(r"gid=(\d+)", href)
            if m:
                game_ids.append(m.group(1))

    # Fallback: regex scan entire HTML.
    for gid in re.findall(r"BoxScore\.aspx\?gid=(\d+)", html):
        game_ids.append(gid)

    # Deduplicate while preserving order.
    seen = set()
    unique_ids = []
    for gid in game_ids:
        if gid not in seen:
            seen.add(gid)
            unique_ids.append(gid)

    game_urls = [
        f"https://www.whatifsports.com/hd/GameResults/BoxScore.aspx?gid={gid}&tab=boxscore"
        for gid in unique_ids
    ]

    return {
        "source_url": url,
        "team": team,
        "world": world,
        "game_urls": game_urls,
    }
def _to_int(value: str) -> int:
    try:
        return int(value)
    except Exception:
        return 0



def _to_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0



def _to_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0



def parse_ratings_history_url(url: str) -> dict:
    """
    Hard-anchored, color-aware parser for WIS HD RatingsHistory.aspx.

    Uses PlayerProfile endpoint because it has the stable RatingsHistory text and OVR structure.
    """
    original_url = url
    url = normalize_ratings_history_url(url)
    html = fetch_html(url)
    lines = clean_lines_from_html(html)
    soup = BeautifulSoup(html, "lxml")

    pid_match = re.search(r"[?&]pid=(\d+)", url)
    tid_match = re.search(r"[?&]tid=(\d+)", url)
    player_id = pid_match.group(1) if pid_match else None
    tid = tid_match.group(1) if tid_match else None

    player = None
    team = None
    class_tokens = {"Fr.", "So.", "Jr.", "Sr.", "Fr", "So", "Jr", "Sr"}
    nav_words = {"Ratings", "Statistics", "Game Log", "Awards", "Ratings History", "More", "Show More"}

    for i in range(0, min(len(lines) - 2, 120)):
        if lines[i + 1] in class_tokens:
            candidate = lines[i].strip()
            if candidate and candidate not in nav_words and not candidate.startswith("#"):
                player = candidate
                break

    if not player and lines:
        m = re.search(r" - ([^-]+?) - Ratings History", lines[0])
        if m:
            player = m.group(1).strip()

    for i in range(0, min(len(lines) - 1, 50)):
        if lines[i].startswith("#") and i + 1 < len(lines):
            candidate = lines[i + 1].strip()
            if candidate and candidate not in nav_words:
                team = candidate
                break

    header_variants = [
        ["Sn.", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT", "OVR"],
        ["Sn.", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PER", "BH", "P", "WE", "ST", "DU", "FT", "OVR"],
        ["Sn", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT", "OVR"],
        ["Season", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT", "OVR"],

        # PlayerHistory endpoint often omits OVR.
        ["Sn.", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT"],
        ["Sn.", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PER", "BH", "P", "WE", "ST", "DU", "FT"],
        ["Sn", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT"],
        ["Season", "Type", "A", "SPD", "REB", "DE", "BLK", "LP", "PE", "BH", "P", "WE", "ST", "DU", "FT"],
    ]

    header_idx = None
    for seq in header_variants:
        n = len(seq)
        for i in range(0, max(len(lines) - n + 1, 0)):
            if lines[i:i + n] == seq:
                header_idx = i + n
                break
        if header_idx is not None:
            break

    if header_idx is None:
        for i in range(0, len(lines) - 5):
            if lines[i] in {"Sn.", "Sn", "Season"} and lines[i + 1] == "Type":
                window = lines[i:i + 25]
                if "OVR" in window:
                    header_idx = i + window.index("OVR") + 1
                    break
                if "FT" in window:
                    header_idx = i + window.index("FT") + 1
                    break

    rows = []

    def _to_int_local(value: str) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return 0

    def add_row(parts, colors=None):
        # Supported shapes:
        # with OVR:    season, type, A, SPD, REB, DE, BLK, LP, PE, BH, P, WE, ST, DU, FT, OVR
        # without OVR: season, type, A, SPD, REB, DE, BLK, LP, PE, BH, P, WE, ST, DU, FT
        if len(parts) < 15:
            return False

        season = str(parts[0]).strip()
        typ = str(parts[1]).strip()
        vals = [str(v).strip() for v in parts[2:]]

        if not re.match(r"^\d+$", season):
            return False
        if len(vals) < 13 or not re.match(r"^\d+$", vals[0]):
            return False

        color_vals = colors or [""] * 13
        color_vals = (color_vals + [""] * 13)[:13]

        # WIS OVR equals the sum of visible numeric ratings excluding FT.
        # If endpoint omits OVR, calculate it.
        calculated_overall = sum(_to_int_local(v) for v in vals[:12])
        overall_value = _to_int_local(vals[13]) if len(vals) >= 14 else calculated_overall

        row = {
            "season": season,
            "snapshot_type": typ,
            "athleticism": _to_int_local(vals[0]),
            "speed": _to_int_local(vals[1]),
            "rebounding": _to_int_local(vals[2]),
            "defense": _to_int_local(vals[3]),
            "shot_blocking": _to_int_local(vals[4]),
            "low_post": _to_int_local(vals[5]),
            "perimeter": _to_int_local(vals[6]),
            "ball_handling": _to_int_local(vals[7]),
            "passing": _to_int_local(vals[8]),
            "work_ethic": _to_int_local(vals[9]),
            "stamina": _to_int_local(vals[10]),
            "durability": vals[11],
            "free_throw": vals[12],
            "overall": overall_value,
            "athleticism_color": color_vals[0],
            "speed_color": color_vals[1],
            "rebounding_color": color_vals[2],
            "defense_color": color_vals[3],
            "shot_blocking_color": color_vals[4],
            "low_post_color": color_vals[5],
            "perimeter_color": color_vals[6],
            "ball_handling_color": color_vals[7],
            "passing_color": color_vals[8],
            "work_ethic_color": color_vals[9],
            "stamina_color": color_vals[10],
            "durability_color": color_vals[11],
            "free_throw_color": color_vals[12],
        }
        rows.append(row)
        return True

    # First try soup table parsing to capture colors.
    header_names = {"Sn.", "Sn", "Season"}
    for table in soup.find_all("table"):
        table_rows = table.find_all("tr")
        for tr_idx, tr in enumerate(table_rows):
            cells = tr.find_all(["th", "td"])
            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            if len(texts) >= 16 and texts[0] in header_names and texts[1] == "Type" and "OVR" in texts:
                for row_tr in table_rows[tr_idx + 1:]:
                    row_cells = row_tr.find_all(["td", "th"])
                    row_texts = [" ".join(c.get_text(" ", strip=True).split()) for c in row_cells]
                    if len(row_texts) < 15:
                        continue
                    color_vals = [normalize_rating_color(c) for c in row_cells[2:15]]
                    add_row(row_texts[:16] if len(row_texts) >= 16 else row_texts[:15], color_vals)
                break
        if rows:
            break

    # HTML row fallback: find rows whose first two cells look like Season + Type.
    # This captures actual potential_* classes even if the header matching failed.
    if not rows:
        for tr in soup.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
            if len(texts) < 15:
                continue
            if not re.match(r"^\d+$", texts[0]):
                continue
            if texts[1] not in {"Current", "Season Start", "Season End", "Recruiting", "Signed"}:
                continue
            color_vals = [normalize_rating_color(c) for c in cells[2:15]]
            add_row(texts[:16] if len(texts) >= 16 else texts[:15], color_vals)

    # Cell-by-cell text fallback.
    if not rows and header_idx is not None:
        i = header_idx
        while i + 14 < len(lines):
            if not re.match(r"^\d+$", lines[i]):
                i += 1
                continue

            if add_row(lines[i:i + 16]):
                i += 16
                continue

            if add_row(lines[i:i + 15]):
                i += 15
                continue

            if i + 16 < len(lines) and lines[i + 1] == "Season" and lines[i + 2] in {"Start", "End"}:
                candidate = [lines[i], "Season " + lines[i + 2]] + lines[i + 3:i + 17]
                if add_row(candidate):
                    i += 17
                    continue
            i += 1

    rows = apply_detected_colors_to_rating_rows(rows, html)

    detected_color_cells = 0
    for _row in rows:
        detected_color_cells += sum(
            1 for _k, _v in _row.items()
            if _k.endswith("_color") and _v
        )

    print("RATINGS PARSER DEBUG:", {
        "url": url,
        "original_url": original_url if "original_url" in locals() else url,
        "player_id": player_id,
        "tid": tid,
        "player": player,
        "team": team,
        "header_idx": header_idx,
        "rows": len(rows),
        "first_row": rows[0] if rows else None,
        "detected_color_cells": detected_color_cells if "detected_color_cells" in locals() else 0,
    }, flush=True)

    return {
        "source_url": url,
        "player_id": player_id,
        "tid": tid,
        "player": player,
        "team": team,
        "rows": rows,
        "debug_preview": "\\n".join(lines[:150]),
    }

def normalize_rating_color(cell) -> str:
    """
    Extract WIS potential color from a rating cell.

    WIS uses classes like:
      potential_veryhigh -> green
      potential_high -> blue
      potential_average -> black
      potential_low -> yellow
      potential_verylow -> red

    The class can be on the td itself or on a nested span/font.
    """
    if cell is None:
        return ""

    chunks = []

    def collect(node):
        if not hasattr(node, "attrs"):
            return
        for key, value in node.attrs.items():
            if isinstance(value, list):
                chunks.extend(str(v) for v in value)
            else:
                chunks.append(str(value))

    collect(cell)
    for nested in cell.find_all(True):
        collect(nested)

    raw = " ".join(chunks).lower()

    # Important: check verylow before low, veryhigh before high.
    if "potential_veryhigh" in raw:
        return "green"
    if "potential_verylow" in raw:
        return "red"
    if "potential_high" in raw:
        return "blue"
    if "potential_average" in raw:
        return "black"
    if "potential_low" in raw:
        return "yellow"

    # Generic fallback.
    if "veryhigh" in raw:
        return "green"
    if "verylow" in raw:
        return "red"
    if "high" in raw:
        return "blue"
    if "average" in raw:
        return "black"
    if "low" in raw:
        return "yellow"

    for color in ["green", "blue", "yellow", "red", "black"]:
        if color in raw:
            return color

    if "#008000" in raw or "0,128,0" in raw or "#00ff00" in raw:
        return "green"
    if "#0000ff" in raw or "0,0,255" in raw:
        return "blue"
    if "#ffff00" in raw or "255,255,0" in raw:
        return "yellow"
    if "#ff0000" in raw or "255,0,0" in raw:
        return "red"
    if "#000000" in raw or "0,0,0" in raw:
        return "black"

    return ""



def extract_potential_colors_from_html_rows(html: str) -> dict:
    """
    Extract potential colors directly from raw RatingsHistory HTML rows.

    This handles rows like:
      <td class="center potential_average">67</td>

    Returns map:
      (season, type) -> [A, SPD, REB, DE, BLK, LP, PE, BH, P, WE, ST, DU, FT colors]
    """
    soup = BeautifulSoup(html, "lxml")
    color_map = {}

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 16:
            continue

        texts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        season = texts[0]
        row_type = texts[1]

        if not re.match(r"^\d+$", season):
            continue
        if row_type not in {"Current", "Season Start", "Season End", "Recruiting", "Signed"}:
            continue

        colors = [normalize_rating_color(c) for c in cells[2:15]]
        color_map[(season, row_type)] = colors

    return color_map


def apply_detected_colors_to_rating_rows(rows: list[dict], html: str) -> list[dict]:
    """
    After text parsing succeeds, attach actual WIS potential colors from raw HTML rows.
    """
    color_map = extract_potential_colors_from_html_rows(html)

    field_names = [
        "athleticism_color",
        "speed_color",
        "rebounding_color",
        "defense_color",
        "shot_blocking_color",
        "low_post_color",
        "perimeter_color",
        "ball_handling_color",
        "passing_color",
        "work_ethic_color",
        "stamina_color",
        "durability_color",
        "free_throw_color",
    ]

    for row in rows:
        key = (row.get("season"), row.get("snapshot_type"))
        colors = color_map.get(key)
        if not colors:
            continue

        for field, color in zip(field_names, colors):
            if color:
                row[field] = color

    return rows



def normalize_ratings_history_url(url: str) -> str:
    """
    Normalize to the stable PlayerProfile RatingsHistory endpoint.

    PlayerProfile includes the stable text/OVR structure. We parse colors from its
    potential_* classes when present and fall back to inferred colors when needed.
    """
    pid_match = re.search(r"[?&]pid=(\d+)", url)
    tid_match = re.search(r"[?&]tid=(\d+)", url)
    if not pid_match:
        return url

    pid = pid_match.group(1)
    tid = tid_match.group(1) if tid_match else None

    if tid:
        return f"https://www.whatifsports.com/hd/PlayerProfile/RatingsHistory.aspx?tid={tid}&pid={pid}"
    return f"https://www.whatifsports.com/hd/PlayerProfile/RatingsHistory.aspx?pid={pid}"


def parse_team_ratings_url(url: str) -> dict:
    """
    Parse a TeamProfile/Ratings.aspx page and find each player's RatingsHistory URL.

    v26: aggressive player-id detection. WIS sometimes puts pid in hrefs,
    onclick handlers, JS fragments, or profile links that are not obvious anchors.
    """
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    lines = clean_lines_from_html(html)

    tid_match = re.search(r"[?&]tid=(\d+)", url)
    tid = tid_match.group(1) if tid_match else None

    players_by_pid = {}

    def add_pid(pid: str, name: str = ""):
        if not pid:
            return
        if pid in players_by_pid:
            if name and (not players_by_pid[pid]["player"] or players_by_pid[pid]["player"] == pid):
                players_by_pid[pid]["player"] = name
            return

        players_by_pid[pid] = {
            "player": name or pid,
            "player_id": pid,
            "tid": tid,
            "ratings_history_url": f"https://www.whatifsports.com/hd/PlayerProfile/RatingsHistory.aspx?tid={tid}&pid={pid}",
            "profile_url": f"https://www.whatifsports.com/hd/PlayerProfile/Default.aspx?tid={tid}&pid={pid}",
        }

    # 1) Anchor hrefs.
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = " ".join(a.get_text(" ", strip=True).split())
        m = re.search(r"pid=(\d+)", href)
        if m:
            add_pid(m.group(1), text)

    # 2) onclick / data attrs / full HTML regex.
    for pid in re.findall(r"pid=(\d+)", html):
        add_pid(pid)

    # 3) Common PlayerProfile URL variants.
    for pid in re.findall(r"PlayerProfile[^\\\"'>]*?(\d{6,})", html):
        add_pid(pid)

    # 4) If names are near player id links but anchor text was blank, try title attrs.
    for tag in soup.find_all(attrs=True):
        raw = " ".join([str(v) for v in tag.attrs.values()])
        m = re.search(r"pid=(\d+)", raw)
        if m:
            name = " ".join(tag.get_text(" ", strip=True).split())
            add_pid(m.group(1), name)

    players = list(players_by_pid.values())

    # Remove obvious non-player duplicates if any.
    players = [p for p in players if p["player_id"] and p["player_id"].isdigit()]
    players.sort(key=lambda p: p["player"])

    print("TEAM RATINGS PARSER DEBUG:", {
        "url": url,
        "tid": tid,
        "players": len(players),
        "sample": players[:5],
        "preview": "\\n".join(lines[:80]),
    }, flush=True)

    return {"source_url": url, "tid": tid, "players": players}
