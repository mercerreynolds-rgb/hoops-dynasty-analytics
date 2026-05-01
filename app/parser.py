from __future__ import annotations

import re
from dataclasses import dataclass

import requests
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.whatifsports.com/hd/",
    }
    r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    r.raise_for_status()

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
            return rows[:2]

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


def parse_boxscore(lines: list[str]) -> list[dict]:
    rows = []
    current_team = None
    current_role = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if is_record_header(line):
            current_team = re.sub(r"\s+\(\d+-\d+,\s*\d+-\d+\)$", "", line).strip()
            current_role = None
            i += 1
            continue

        if line == "STARTERS":
            current_role = "starter"
            i += 1
            # skip headers until first pos
            continue

        if line == "BENCH":
            current_role = "bench"
            i += 1
            continue

        if line == "Totals":
            current_role = None
            i += 15
            continue

        # Cell-by-cell player rows:
        # pos, player, min, fg, fg3, ft, orb, reb, ast, to, stl, blk, pf, pts
        if (
            current_team
            and current_role
            and re.match(r"^(c|pf|sf|sg|pg)$", line, flags=re.I)
            and i + 13 < len(lines)
            and re.match(r"^\d+$", lines[i + 2])
            and re.match(r"^\d+-\d+$", lines[i + 3])
            and re.match(r"^\d+-\d+$", lines[i + 4])
            and re.match(r"^\d+-\d+$", lines[i + 5])
        ):
            row = {
                "team": current_team,
                "role": current_role,
                "pos": lines[i].lower(),
                "player": lines[i + 1],
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
