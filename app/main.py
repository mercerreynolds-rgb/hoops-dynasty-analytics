from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, delete

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent, LineupSegment, PlayerImpact, PlayerRatingSnapshot, TrackedTeam
from app.parser import parse_game_url, parse_ratings_history_url, parse_game_log_url
from app.ratings import calculate_box_ratings
from app.impact import calculate_game_impacts
from app.config import TRACKED_TEAMS, DEFAULT_TEAM, DEFAULT_WORLD

app = FastAPI(title="Hoops Dynasty Analytics")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()


def get_tracked_team_options():
    return TRACKED_TEAMS


def normalize_team(team: str | None) -> str:
    return (team or "").strip()


def weighted_avg(rows, value_attr, weight_attr):
    total_weight = sum(getattr(r, weight_attr, 0) or 0 for r in rows)
    if not total_weight:
        return 0.0
    return sum((getattr(r, value_attr, 0) or 0) * (getattr(r, weight_attr, 0) or 0) for r in rows) / total_weight


def rate(n, d):
    return (n / d * 100) if d else 0.0


def opponent_coach_for_team(game: Game, team: str) -> str | None:
    if game.away_team == team:
        return game.home_coach
    if game.home_team == team:
        return game.away_coach
    return None


def opponent_control_type(game: Game, team: str) -> str:
    return "Sim AI" if (opponent_coach_for_team(game, team) or "").strip() == "Sim AI" else "Human"


def is_team_win(game: Game, team: str) -> bool:
    if game.away_team == team:
        return (game.away_score or 0) > (game.home_score or 0)
    if game.home_team == team:
        return (game.home_score or 0) > (game.away_score or 0)
    return False


def build_player_season_summary(stats, impacts):
    impact_by_key = {(i.game_id, i.player): i for i in impacts}
    grouped = {}
    for s in stats:
        grouped.setdefault(s.player, []).append(s)

    rows = []
    for player, games in grouped.items():
        player_impacts = [impact_by_key[(g.game_id, player)] for g in games if (g.game_id, player) in impact_by_key]
        gp = len(games)
        minutes = sum(g.minutes or 0 for g in games)
        pts = sum(g.pts or 0 for g in games)
        reb = sum(g.reb or 0 for g in games)
        ast = sum(g.ast or 0 for g in games)
        tov = sum(g.tov or 0 for g in games)

        on_pf = sum(i.on_points_for for i in player_impacts)
        on_pa = sum(i.on_points_against for i in player_impacts)
        on_poss_for = sum(i.on_possessions_for for i in player_impacts)
        on_poss_against = sum(i.on_possessions_against for i in player_impacts)
        off_pf = sum(i.off_points_for for i in player_impacts)
        off_pa = sum(i.off_points_against for i in player_impacts)
        off_poss_for = sum(i.off_possessions_for for i in player_impacts)
        off_poss_against = sum(i.off_possessions_against for i in player_impacts)

        on_off_eff = rate(on_pf, on_poss_for)
        off_off_eff = rate(off_pf, off_poss_for)
        on_def_eff = rate(on_pa, on_poss_against)
        off_def_eff = rate(off_pa, off_poss_against)

        fga = sum(g.fga or 0 for g in games)
        fta = sum(g.fta or 0 for g in games)
        poss_used = sum((g.fga or 0) + 0.44 * (g.fta or 0) + (g.tov or 0) for g in games)
        team_poss_proxy = on_poss_for + off_poss_for
        usage = rate(poss_used, team_poss_proxy)
        ts_pct = (pts / (2 * (fga + 0.44 * fta)) * 100) if (fga + 0.44 * fta) else 0.0
        net_on = on_off_eff - on_def_eff
        net_off = off_off_eff - off_def_eff
        net_onoff = net_on - net_off

        # Simple role tagging. These are intentionally transparent and tunable.
        role_tags = []
        if usage >= 24 and ts_pct >= 55:
            role_tags.append("Scorer")
        elif usage >= 24:
            role_tags.append("High Usage")
        if gp and (ast / gp) >= 3.0:
            role_tags.append("Creator")
        if on_def_eff and on_def_eff <= 95:
            role_tags.append("Stopper")
        if net_onoff >= 10:
            role_tags.append("Glue/Impact")
        if usage < 14 and net_onoff >= 5:
            role_tags.append("Low-Usage Plus")
        if tov and poss_used and (tov / poss_used) >= 0.22:
            role_tags.append("TO Risk")
        if not role_tags:
            role_tags.append("Balanced")

        rows.append({
            "player": player,
            "gp": gp,
            "minutes": minutes,
            "mpg": minutes / gp if gp else 0,
            "pts": pts,
            "ppg": pts / gp if gp else 0,
            "reb": reb,
            "rpg": reb / gp if gp else 0,
            "ast": ast,
            "apg": ast / gp if gp else 0,
            "tov": tov,
            "poss_used": poss_used,
            "usage": usage,
            "ts_pct": ts_pct,
            "on_ortg": on_off_eff,
            "on_drtg": on_def_eff,
            "on_net": net_on,
            "off_net": net_off,
            "net_onoff": net_onoff,
            "role": ", ".join(role_tags),
            "box_obpr": weighted_avg(games, "box_obpr", "minutes"),
            "box_dbpr": weighted_avg(games, "box_dbpr", "minutes"),
            "obpr": weighted_avg(games, "obpr", "minutes"),
            "dbpr": weighted_avg(games, "dbpr", "minutes"),
            "bpr": weighted_avg(games, "bpr", "minutes"),
            "on_off_eff": on_off_eff,
            "off_off_eff": off_off_eff,
            "on_def_eff": on_def_eff,
            "off_def_eff": off_def_eff,
            "net_on": net_on,
            "net_off": net_off,
            "on_poss": on_poss_for,
            "off_poss": off_poss_for,
        })

    rows.sort(key=lambda r: r["bpr"], reverse=True)
    return rows


def build_lineup_summary(segments):
    grouped = {}
    for s in segments:
        key = s.lineup
        g = grouped.setdefault(key, {
            "lineup": key,
            "segments": 0,
            "pf": 0,
            "pa": 0,
            "poss_for": 0,
            "poss_against": 0,
        })
        g["segments"] += 1
        g["pf"] += s.points_for or 0
        g["pa"] += s.points_against or 0
        g["poss_for"] += s.possessions_for or 0
        g["poss_against"] += s.possessions_against or 0

    rows = []
    for g in grouped.values():
        g["off_eff"] = rate(g["pf"], g["poss_for"])
        g["def_eff"] = rate(g["pa"], g["poss_against"])
        g["net_eff"] = g["off_eff"] - g["def_eff"]
        rows.append(g)

    rows.sort(key=lambda r: (r["poss_for"], r["net_eff"]), reverse=True)
    return rows


def avg_bpr_for_games(player_stats, game_ids):
    rows = [s for s in player_stats if s.game_id in game_ids]
    return weighted_avg(rows, "bpr", "minutes") if rows else 0.0


def build_form_and_split_rows(stats, games, team):
    my_games = [g for g in games if g.away_team == team or g.home_team == team]
    my_games_sorted = sorted(my_games, key=lambda g: g.id)

    last5_ids = {g.id for g in my_games_sorted[-5:]}
    win_ids = {g.id for g in my_games if is_team_win(g, team)}
    loss_ids = {g.id for g in my_games if g.id not in win_ids}
    human_ids = {g.id for g in my_games if opponent_control_type(g, team) == "Human"}
    sim_ids = {g.id for g in my_games if opponent_control_type(g, team) == "Sim AI"}

    grouped = {}
    for s in stats:
        grouped.setdefault(s.player, []).append(s)

    rows = []
    for player, player_stats in grouped.items():
        season_bpr = weighted_avg(player_stats, "bpr", "minutes")
        last5_bpr = avg_bpr_for_games(player_stats, last5_ids)
        wins_bpr = avg_bpr_for_games(player_stats, win_ids)
        losses_bpr = avg_bpr_for_games(player_stats, loss_ids)
        human_bpr = avg_bpr_for_games(player_stats, human_ids)
        sim_bpr = avg_bpr_for_games(player_stats, sim_ids)

        rows.append({
            "player": player,
            "season_bpr": season_bpr,
            "last5_bpr": last5_bpr,
            "trend": last5_bpr - season_bpr,
            "wins_bpr": wins_bpr,
            "losses_bpr": losses_bpr,
            "win_loss_gap": wins_bpr - losses_bpr,
            "human_bpr": human_bpr,
            "sim_bpr": sim_bpr,
            "human_sim_gap": human_bpr - sim_bpr,
        })

    rows.sort(key=lambda r: r["trend"], reverse=True)
    return rows


def build_opponent_control_summary(stats, games, team):
    my_games = [g for g in games if g.away_team == team or g.home_team == team]
    human_ids = {g.id for g in my_games if opponent_control_type(g, team) == "Human"}
    sim_ids = {g.id for g in my_games if opponent_control_type(g, team) == "Sim AI"}

    human_rows = [s for s in stats if s.game_id in human_ids]
    sim_rows = [s for s in stats if s.game_id in sim_ids]

    return {
        "human_games": len(human_ids),
        "sim_games": len(sim_ids),
        "human_bpr": weighted_avg(human_rows, "bpr", "minutes") if human_rows else 0.0,
        "sim_bpr": weighted_avg(sim_rows, "bpr", "minutes") if sim_rows else 0.0,
    }


def save_parsed_game(url: str, session: Session) -> tuple[Game, bool]:
    parsed = parse_game_url(url)

    existing = None
    if parsed.wis_game_id:
        existing = session.exec(
            select(Game).where(Game.wis_game_id == parsed.wis_game_id)
        ).first()

    if existing:
        return existing, False

    away = parsed.summary_rows[0] if len(parsed.summary_rows) > 0 else {}
    home = parsed.summary_rows[1] if len(parsed.summary_rows) > 1 else {}

    game = Game(
        source_url=url,
        wis_game_id=parsed.wis_game_id,
        game_date=away.get("date") or home.get("date"),
        away_team=away.get("team"),
        home_team=home.get("team"),
        away_score=away.get("final"),
        home_score=home.get("final"),
        away_coach=away.get("coach"),
        home_coach=home.get("coach"),
    )
    session.add(game)
    session.commit()
    session.refresh(game)

    for row in parsed.boxscore_rows:
        ratings = calculate_box_ratings(row)
        session.add(PlayerGameStat(game_id=game.id, **row, **ratings))

    for row in parsed.pbp_rows:
        session.add(PlayByPlayEvent(game_id=game.id, **row))

    session.commit()

    stats = session.exec(select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)).all()
    events = session.exec(
        select(PlayByPlayEvent)
        .where(PlayByPlayEvent.game_id == game.id)
        .order_by(PlayByPlayEvent.event_number)
    ).all()
    teams = [t for t in [game.away_team, game.home_team] if t] or sorted(set(s.team for s in stats))

    # Build impacts for any tracked team appearing in the game.
    for tracked in TRACKED_TEAMS:
        team = tracked["team"]
        if team not in teams:
            continue
        segments, impacts = calculate_game_impacts_for_team(game.id, stats, events, teams, team)
        for seg in segments:
            session.add(LineupSegment(**seg))

        impact_by_player = {}
        for imp in impacts:
            impact_by_player[imp["player"]] = imp
            session.add(PlayerImpact(**imp))

        for stat in stats:
            if stat.team == team and stat.player in impact_by_player:
                imp = impact_by_player[stat.player]
                stat.obpr = round(stat.box_obpr + imp["off_impact"], 3)
                stat.dbpr = round(stat.box_dbpr + imp["def_impact"], 3)
                stat.bpr = round(stat.obpr + stat.dbpr, 3)
                session.add(stat)

    session.commit()
    return game, True


def calculate_game_impacts_for_team(game_id, stats, events, teams, team):
    # Temporarily monkeypatch by using the existing engine semantics.
    # The engine originally used MY_TEAM from app.config; for v11 we adapt its output by filtering.
    # To keep the package stable, compute through existing function for default team when possible;
    # otherwise temporarily use a simple team-specific copy by setting app.impact.MY_TEAM.
    import app.impact as impact_mod
    old_team = impact_mod.MY_TEAM
    impact_mod.MY_TEAM = team
    try:
        return calculate_game_impacts(game_id, stats, events, teams)
    finally:
        impact_mod.MY_TEAM = old_team


def rebuild_impacts_for_all_games(session: Session):
    games = session.exec(select(Game).order_by(Game.id)).all()
    session.exec(delete(PlayerImpact))
    session.exec(delete(LineupSegment))
    session.commit()

    for game in games:
        stats = session.exec(select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)).all()
        events = session.exec(
            select(PlayByPlayEvent)
            .where(PlayByPlayEvent.game_id == game.id)
            .order_by(PlayByPlayEvent.event_number)
        ).all()
        teams = [t for t in [game.away_team, game.home_team] if t] or sorted(set(s.team for s in stats))

        for tracked in TRACKED_TEAMS:
            team = tracked["team"]
            if team not in teams:
                continue
            segments, impacts = calculate_game_impacts_for_team(game.id, stats, events, teams, team)

            for seg in segments:
                session.add(LineupSegment(**seg))

            impact_by_player = {}
            for imp in impacts:
                impact_by_player[imp["player"]] = imp
                session.add(PlayerImpact(**imp))

            for stat in stats:
                if stat.team == team and stat.player in impact_by_player:
                    imp = impact_by_player[stat.player]
                    stat.obpr = round(stat.box_obpr + imp["off_impact"], 3)
                    stat.dbpr = round(stat.box_dbpr + imp["def_impact"], 3)
                    stat.bpr = round(stat.obpr + stat.dbpr, 3)
                    session.add(stat)

    session.commit()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id.desc())).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "games": games, "teams": get_tracked_team_options()},
    )


@app.post("/import")
def import_game(url: str = Form(...), session: Session = Depends(get_session)):
    game, created = save_parsed_game(url, session)
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@app.post("/bulk-import")
def bulk_import(game_log_url: str = Form(...), session: Session = Depends(get_session)):
    parsed = parse_game_log_url(game_log_url)
    imported = 0
    skipped = 0
    errors = 0

    for url in parsed["game_urls"]:
        try:
            _game, created = save_parsed_game(url, session)
            if created:
                imported += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            print("BULK IMPORT ERROR:", url, exc, flush=True)

    print("BULK IMPORT RESULT:", {"imported": imported, "skipped": skipped, "errors": errors}, flush=True)
    return RedirectResponse("/diagnostics", status_code=303)


@app.post("/admin/reset")
def reset_database(session: Session = Depends(get_session)):
    session.exec(delete(PlayerImpact))
    session.exec(delete(LineupSegment))
    session.exec(delete(PlayerGameStat))
    session.exec(delete(PlayByPlayEvent))
    session.exec(delete(Game))
    session.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/admin/rebuild-impacts")
def rebuild_impacts(session: Session = Depends(get_session)):
    rebuild_impacts_for_all_games(session)
    return RedirectResponse("/diagnostics", status_code=303)


@app.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id)).all()
    stats = session.exec(select(PlayerGameStat)).all()
    events = session.exec(select(PlayByPlayEvent)).all()
    impacts = session.exec(select(PlayerImpact)).all()
    segments = session.exec(select(LineupSegment)).all()
    rating_snapshots = session.exec(select(PlayerRatingSnapshot)).all()

    teams = sorted(set(s.team for s in stats))
    player_teams = {}
    for s in stats:
        player_teams.setdefault(s.team, 0)
        player_teams[s.team] += 1

    return templates.TemplateResponse(
        "diagnostics.html",
        {
            "request": request,
            "games": games,
            "stats_count": len(stats),
            "events_count": len(events),
            "impacts_count": len(impacts),
            "segments_count": len(segments),
            "rating_snapshots_count": len(rating_snapshots),
            "teams": teams,
            "player_teams": player_teams,
            "tracked_teams": get_tracked_team_options(),
        },
    )


@app.get("/season", response_class=HTMLResponse)
def season_dashboard(
    request: Request,
    team: str = DEFAULT_TEAM,
    world: str = DEFAULT_WORLD,
    session: Session = Depends(get_session),
):
    stats = session.exec(select(PlayerGameStat).where(PlayerGameStat.team == team)).all()
    impacts = session.exec(select(PlayerImpact).where(PlayerImpact.team == team)).all()
    segments = session.exec(select(LineupSegment).where(LineupSegment.team == team)).all()
    games = session.exec(select(Game).order_by(Game.id)).all()
    team_games = [g for g in games if g.away_team == team or g.home_team == team]

    if stats and (not impacts or not segments):
        rebuild_impacts_for_all_games(session)
        impacts = session.exec(select(PlayerImpact).where(PlayerImpact.team == team)).all()
        segments = session.exec(select(LineupSegment).where(LineupSegment.team == team)).all()

    player_rows = build_player_season_summary(stats, impacts)
    lineup_rows = build_lineup_summary(segments)
    lineup_rows_20 = [l for l in lineup_rows if l["poss_for"] >= 20]
    lineup_rows_20.sort(key=lambda r: r["net_eff"], reverse=True)
    form_rows = build_form_and_split_rows(stats, team_games, team)
    control_summary = build_opponent_control_summary(stats, team_games, team)

    return templates.TemplateResponse(
        "season.html",
        {
            "request": request,
            "team": team,
            "world": world,
            "teams": get_tracked_team_options(),
            "games": team_games,
            "player_rows": player_rows,
            "lineup_rows_20": lineup_rows_20,
            "form_rows": form_rows,
            "control_summary": control_summary,
            "min_lineup_possessions": 20,
        },
    )



@app.get("/season/players/{player_name}", response_class=HTMLResponse)
def player_detail(
    player_name: str,
    request: Request,
    team: str = DEFAULT_TEAM,
    world: str = DEFAULT_WORLD,
    session: Session = Depends(get_session),
):
    stats = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == team)
        .where(PlayerGameStat.player == player_name)
        .order_by(PlayerGameStat.game_id)
    ).all()

    impacts = session.exec(
        select(PlayerImpact)
        .where(PlayerImpact.team == team)
        .where(PlayerImpact.player == player_name)
        .order_by(PlayerImpact.game_id)
    ).all()

    games = session.exec(select(Game).order_by(Game.id)).all()
    game_by_id = {g.id: g for g in games}
    summary_rows = build_player_season_summary(stats, impacts)
    summary = summary_rows[0] if summary_rows else None
    impact_by_game = {i.game_id: i for i in impacts}

    return templates.TemplateResponse(
        "player_detail.html",
        {
            "request": request,
            "team": team,
            "world": world,
            "player": player_name,
            "stats": stats,
            "impact_by_game": impact_by_game,
            "game_by_id": game_by_id,
            "summary": summary,
        },
    )


@app.get("/games/{game_id}", response_class=HTMLResponse)
def game_detail(request: Request, game_id: int, session: Session = Depends(get_session)):
    game = session.get(Game, game_id)
    if not game:
        return RedirectResponse("/", status_code=303)

    # Prefer whichever tracked team is in this game.
    tracked_team_names = [t["team"] for t in TRACKED_TEAMS]
    team = game.away_team if game.away_team in tracked_team_names else game.home_team

    stats = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.game_id == game_id)
        .where(PlayerGameStat.team == team)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()
    impacts = session.exec(
        select(PlayerImpact)
        .where(PlayerImpact.game_id == game_id)
        .where(PlayerImpact.team == team)
        .order_by(PlayerImpact.off_impact.desc())
    ).all()
    segments = session.exec(
        select(LineupSegment)
        .where(LineupSegment.game_id == game_id)
        .where(LineupSegment.team == team)
        .order_by(LineupSegment.segment_number)
    ).all()
    events = session.exec(
        select(PlayByPlayEvent)
        .where(PlayByPlayEvent.game_id == game_id)
        .order_by(PlayByPlayEvent.event_number)
    ).all()

    return templates.TemplateResponse(
        "game_detail.html",
        {
            "request": request,
            "game": game,
            "stats": stats,
            "events": events[:200],
            "impacts": impacts,
            "segments": segments,
            "my_team": team,
        },
    )



RATING_KEYS = [
    "athleticism", "speed", "rebounding", "defense", "shot_blocking",
    "low_post", "perimeter", "ball_handling", "passing", "work_ethic", "stamina"
]

ROLE_WEIGHTS = {
    "PG Creator": {
        "speed": 0.18, "ball_handling": 0.22, "passing": 0.22, "perimeter": 0.14,
        "athleticism": 0.10, "defense": 0.10, "stamina": 0.04,
    },
    "Perimeter Scorer": {
        "perimeter": 0.28, "speed": 0.16, "ball_handling": 0.16,
        "athleticism": 0.14, "passing": 0.08, "defense": 0.08, "stamina": 0.10,
    },
    "3&D Wing": {
        "defense": 0.24, "athleticism": 0.18, "speed": 0.14,
        "perimeter": 0.18, "rebounding": 0.10, "stamina": 0.08, "passing": 0.08,
    },
    "Post Scorer": {
        "low_post": 0.32, "athleticism": 0.16, "rebounding": 0.14,
        "defense": 0.10, "shot_blocking": 0.10, "stamina": 0.10, "passing": 0.08,
    },
    "Rim Protector": {
        "shot_blocking": 0.28, "defense": 0.22, "rebounding": 0.20,
        "athleticism": 0.14, "stamina": 0.08, "speed": 0.08,
    },
    "Rebounding Big": {
        "rebounding": 0.34, "athleticism": 0.16, "defense": 0.16,
        "shot_blocking": 0.14, "low_post": 0.10, "stamina": 0.10,
    },
}


def role_score(snapshot, weights):
    return sum((getattr(snapshot, key, 0) or 0) * weight for key, weight in weights.items())


def snapshot_to_dict(snapshot):
    return {key: getattr(snapshot, key, 0) or 0 for key in RATING_KEYS}


def build_rating_summary(rows):
    if not rows:
        return None

    def season_int(row):
        try:
            return int(row.season or 0)
        except Exception:
            return 0

    # Current row: explicitly Current if available, otherwise newest season/highest id row.
    current = next((r for r in rows if r.snapshot_type == "Current"), None)
    if current is None:
        current = sorted(rows, key=lambda r: (season_int(r), r.id or 0), reverse=True)[0]

    # Correct baseline:
    # lowest numbered season + "Season Start"
    # NOT "Season End" from that same season.
    positive_seasons = [season_int(r) for r in rows if season_int(r) > 0]
    min_season = min(positive_seasons) if positive_seasons else season_int(current)
    min_season_rows = [r for r in rows if season_int(r) == min_season]

    start = next((r for r in min_season_rows if r.snapshot_type == "Season Start"), None)

    # Fall back to Recruiting/Signed if available for that same lowest season.
    if start is None:
        start = next((r for r in min_season_rows if r.snapshot_type in {"Recruiting", "Signed"}), None)

    # Last fallback: earliest row in the lowest season.
    if start is None and min_season_rows:
        start = sorted(min_season_rows, key=lambda r: r.id or 0)[0]

    # Absolute fallback.
    if start is None:
        start = rows[-1]

    current_season = season_int(current)
    current_season_rows = [r for r in rows if season_int(r) == current_season]
    season_start = next((r for r in current_season_rows if r.snapshot_type == "Season Start"), start)

    growth = {}
    for key in RATING_KEYS + ["overall"]:
        growth[key] = (getattr(current, key, 0) or 0) - (getattr(start, key, 0) or 0)

    role_scores = []
    for role, weights in ROLE_WEIGHTS.items():
        role_scores.append({
            "role": role,
            "score": role_score(current, weights),
        })
    role_scores.sort(key=lambda r: r["score"], reverse=True)

    return {
        "current": current,
        "start": start,
        "season_start": season_start,
        "growth": growth,
        "role_scores": role_scores,
        "best_role": role_scores[0] if role_scores else None,
        "baseline_note": f"{start.season} {start.snapshot_type}",
    }


@app.post("/ratings/import")
def import_ratings_history(
    url: str = Form(...),
    player_name: str = Form(""),
    team: str = Form(""),
    session: Session = Depends(get_session),
):
    parsed = parse_ratings_history_url(url)
    print("RATINGS IMPORT RESULT:", {"rows": len(parsed.get("rows", [])), "player": parsed.get("player"), "pid": parsed.get("player_id")}, flush=True)
    name = player_name.strip() or parsed.get("player") or parsed.get("player_id") or "Unknown Player"
    team_name = team.strip() or parsed.get("team") or ""

    # Remove previous snapshots for this pid/url combo to avoid duplicates.
    if parsed.get("player_id"):
        existing = session.exec(
            select(PlayerRatingSnapshot).where(PlayerRatingSnapshot.player_id == parsed["player_id"])
        ).all()
        for row in existing:
            session.delete(row)
        session.commit()

    for row in parsed["rows"]:
        snap = PlayerRatingSnapshot(
            source_url=url,
            player_id=parsed.get("player_id"),
            tid=parsed.get("tid"),
            player=name,
            team=team_name,
            **row,
        )
        session.add(snap)

    session.commit()
    return RedirectResponse("/ratings", status_code=303)


@app.get("/ratings", response_class=HTMLResponse)
def ratings_dashboard(request: Request, session: Session = Depends(get_session)):
    snapshots = session.exec(select(PlayerRatingSnapshot)).all()

    players = {}
    for snap in snapshots:
        key = (snap.player or "Unknown Player", snap.player_id or "", snap.team or "")
        players.setdefault(key, []).append(snap)

    player_rows = []
    for (player, player_id, team), rows in players.items():
        rows = sorted(rows, key=lambda r: (int(r.season or 0), r.id or 0), reverse=True)
        summary = build_rating_summary(rows)
        if not summary:
            continue
        current = summary["current"]
        player_rows.append({
            "player": player,
            "player_id": player_id,
            "team": team,
            "current_ovr": current.overall,
            "growth_ovr": summary["growth"]["overall"],
            "best_role": summary["best_role"]["role"] if summary["best_role"] else "",
            "best_role_score": summary["best_role"]["score"] if summary["best_role"] else 0,
            "snapshots": len(rows),
        })

    player_rows.sort(key=lambda r: r["current_ovr"], reverse=True)

    return templates.TemplateResponse(
        "ratings.html",
        {
            "request": request,
            "player_rows": player_rows,
        },
    )


@app.get("/ratings/{player_id}", response_class=HTMLResponse)
def rating_player_detail(player_id: str, request: Request, session: Session = Depends(get_session)):
    rows = session.exec(
        select(PlayerRatingSnapshot)
        .where(PlayerRatingSnapshot.player_id == player_id)
    ).all()
    rows = sorted(rows, key=lambda r: (int(r.season or 0), r.id or 0), reverse=True)
    summary = build_rating_summary(rows)

    return templates.TemplateResponse(
        "rating_detail.html",
        {
            "request": request,
            "rows": rows,
            "summary": summary,
            "player": rows[0].player if rows else player_id,
            "player_id": player_id,
            "rating_keys": RATING_KEYS,
        },
    )


@app.get("/players", response_class=HTMLResponse)
def players(
    request: Request,
    team: str = DEFAULT_TEAM,
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == team)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()
    return templates.TemplateResponse("players.html", {"request": request, "rows": rows})
