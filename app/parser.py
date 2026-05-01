from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup


@dataclass
class ParsedGame:
    source_url: str
    wis_game_id: str | None
    summary_rows: list[dict]
    boxscore_rows: list[dict]
    pbp_rows: list[dict]


def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/122 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
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
    return lines


def extract_wis_game_id(url: str) -> str | None:
    m = re.search(r"[?&]gid=(\d+)", url)
    return m.group(1) if m else None


def split_made_attempt(value: str) -> tuple[int, int]:
    made, att = value.split("-")
    return int(made), int(att)


def looks_like_player_stat_line(line: str) -> bool:
    # WIS text extraction often returns player rows like:
    #   cGeorge Nicoll 26 5-9 ...
    # with no space between position and player name.
    return bool(
        re.match(
            r"^(c|pf|sf|sg|pg)\s*.+?\s+\d+\s+\d+-\d+\s+\d+-\d+\s+\d+-\d+\s+",
            line,
            flags=re.I,
        )
    )


def parse_player_stat_line(line: str, team: str, role: str) -> dict:
    pattern = re.compile(
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
    m = pattern.match(line)
    if not m:
        raise ValueError(f"Could not parse player stat line: {line}")

    fgm, fga = split_made_attempt(m["fg"])
    fg3m, fg3a = split_made_attempt(m["fg3"])
    ftm, fta = split_made_attempt(m["ft"])

    return {
        "team": team,
        "role": role,
        "pos": m["pos"].lower(),
        "player": m["player"],
        "minutes": int(m["min"]),
        "fgm": fgm,
        "fga": fga,
        "fg3m": fg3m,
        "fg3a": fg3a,
        "ftm": ftm,
        "fta": fta,
        "orb": int(m["orb"]),
        "reb": int(m["reb"]),
        "ast": int(m["ast"]),
        "tov": int(m["to"]),
        "stl": int(m["stl"]),
        "blk": int(m["blk"]),
        "pf": int(m["pf"]),
        "pts": int(m["pts"]),
    }


def parse_scoreboard(lines: list[str]) -> list[dict]:
    text = "\n".join(lines)
    date_match = re.search(r"Final -\s*\n?(\d{1,2}/\d{1,2}/\d{4})", text)
    date = date_match.group(1) if date_match else None

    rows = []
    for line in lines:
        m = re.match(
            r"^(#\d+\s+)?(?P<team>.+?)\s+(?P<h1>\d+)\s+(?P<h2>\d+)\s+(?P<final>\d+)$",
            line,
        )
        if not m:
            continue
        team = m.group("team").strip()
        if len(team) > 2 and not team.isdigit():
            rows.append(
                {
                    "date": date,
                    "team": team,
                    "half1": int(m.group("h1")),
                    "half2": int(m.group("h2")),
                    "final": int(m.group("final")),
                }
            )

    return rows[:2]


def parse_boxscore(lines: list[str]) -> list[dict]:
    rows = []
    current_team = None
    current_role = None

    for line in lines:
        team_header = re.match(r"^(.+?)\s+\(\d+-\d+,\s*\d+-\d+\)$", line)
        if team_header:
            current_team = team_header.group(1).strip()
            current_role = None
            continue

        if line.strip().startswith("STARTERS"):
            current_role = "starter"
            continue

        if line.strip().startswith("BENCH"):
            current_role = "bench"
            continue

        if current_team and current_role and looks_like_player_stat_line(line):
            rows.append(parse_player_stat_line(line, current_team, current_role))

        if line.startswith("Totals "):
            current_role = None

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


def is_time_line(line: str) -> bool:
    return bool(re.match(r"^\d{1,2}:\d{2}\s+", line))


def split_team_from_description(body: str, known_teams: list[str]) -> tuple[str | None, str]:
    for team in sorted(known_teams, key=len, reverse=True):
        if body.startswith(team):
            return team, body[len(team):].strip()
    return None, body


def parse_pbp_event(line: str, half: str | None, known_teams: list[str]) -> dict | None:
    if not is_time_line(line):
        return None

    m = re.match(
        r"^(?P<time>\d{1,2}:\d{2})\s+(?P<body>.+?)\s+(?P<score>\d+-\d+)$",
        line,
    )

    if m:
        body = m.group("body").strip()
        team, description = split_team_from_description(body, known_teams)
        return {
            "half": half,
            "clock": m.group("time"),
            "team": team,
            "description": description,
            "score": m.group("score"),
            "event_type": classify_event(description),
        }

    raw_time = line[:5]
    body = line[6:].strip()
    team, description = split_team_from_description(body, known_teams)
    return {
        "half": half,
        "clock": raw_time,
        "team": team,
        "description": description,
        "score": None,
        "event_type": classify_event(description),
    }


def parse_play_by_play(lines: list[str], known_teams: list[str]) -> list[dict]:
    rows = []
    in_pbp = False
    half = "1st Half"

    i = 0
    while i < len(lines):
        line = lines[i]

        if line == "Time Team Play Score":
            in_pbp = True
            i += 1
            continue

        if not in_pbp:
            i += 1
            continue

        if line == "1st Half":
            half = "1st Half"
            i += 1
            continue
        if line == "2nd Half":
            half = "2nd Half"
            i += 1
            continue

        # Capture lineup headers and the following five player lines.
        if line.startswith("Lineup "):
            team = line.replace("Lineup ", "", 1).strip()
            details = []
            j = i + 1
            while j < len(lines) and not is_time_line(lines[j]) and not lines[j].startswith(("Lineup ", "Subs ", "Game Plan ")):
                if " - " in lines[j]:
                    details.append(lines[j])
                j += 1

            rows.append({
                "event_number": len(rows) + 1,
                "half": half,
                "clock": None,
                "team": team,
                "description": " | ".join(details) if details else line,
                "score": None,
                "event_type": "lineup",
            })
            i = j
            continue

        # Capture substitution blocks and the following in/out player lines.
        if line.startswith("Subs "):
            team = line.replace("Subs ", "", 1).strip()
            details = []
            j = i + 1
            while j < len(lines) and not is_time_line(lines[j]) and not lines[j].startswith(("Lineup ", "Subs ", "Game Plan ")):
                details.append(lines[j])
                j += 1

            rows.append({
                "event_number": len(rows) + 1,
                "half": half,
                "clock": None,
                "team": team,
                "description": " | ".join(details) if details else line,
                "score": None,
                "event_type": "substitution",
            })
            i = j
            continue

        # Capture game plans too, useful for later model context.
        if line.startswith("Game Plan "):
            body = line.replace("Game Plan ", "", 1).strip()
            team, description = split_team_from_description(body, known_teams)
            rows.append({
                "event_number": len(rows) + 1,
                "half": half,
                "clock": None,
                "team": team,
                "description": description,
                "score": None,
                "event_type": "game_plan",
            })
            i += 1
            continue

        event = parse_pbp_event(line, half, known_teams)
        if event:
            event["event_number"] = len(rows) + 1
            rows.append(event)

        i += 1

    return rows


def parse_game_url(url: str) -> ParsedGame:
    html = fetch_html(url)
    lines = clean_lines_from_html(html)

    summary = parse_scoreboard(lines)
    teams = [r["team"] for r in summary]
    boxscore = parse_boxscore(lines)
    pbp = parse_play_by_play(lines, teams)

    return ParsedGame(
        source_url=url,
        wis_game_id=extract_wis_game_id(url),
        summary_rows=summary,
        boxscore_rows=boxscore,
        pbp_rows=pbp,
    )
