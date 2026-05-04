from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, delete

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent, LineupSegment, PlayerImpact, TrackedTeam
from app.parser import parse_game_url, parse_game_log_url
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
            "box_obpr": weighted_avg(games, "box_obpr", "minutes"),
            "box_dbpr": weighted_avg(games, "box_dbpr", "minutes"),
            "obpr": weighted_avg(games, "obpr", "minutes"),
            "dbpr": weighted_avg(games, "dbpr", "minutes"),
            "bpr": weighted_avg(games, "bpr", "minutes"),
            "on_off_eff": on_off_eff,
            "off_off_eff": off_off_eff,
            "on_def_eff": on_def_eff,
            "off_def_eff": off_def_eff,
            "net_on": on_off_eff - on_def_eff,
            "net_off": off_off_eff - off_def_eff,
            "net_onoff": (on_off_eff - on_def_eff) - (off_off_eff - off_def_eff),
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
    summary = build_player_season_summary(stats, impacts)
    summary_row = summary[0] if summary else None
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
            "summary": summary_row,
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
