from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, delete

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent, LineupSegment, PlayerImpact
from app.parser import parse_game_url
from app.ratings import calculate_box_ratings
from app.impact import calculate_game_impacts
from app.config import MY_TEAM, WORLD

app = FastAPI(title="Hoops Dynasty Analytics")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()


def weighted_avg(rows, value_attr, weight_attr):
    total_weight = sum(getattr(r, weight_attr, 0) or 0 for r in rows)
    if not total_weight:
        return 0.0
    return sum((getattr(r, value_attr, 0) or 0) * (getattr(r, weight_attr, 0) or 0) for r in rows) / total_weight


def rate(n, d):
    return (n / d * 100) if d else 0.0


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
        stl = sum(g.stl or 0 for g in games)
        blk = sum(g.blk or 0 for g in games)

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
            "stl": stl,
            "blk": blk,
            "box_obpr": weighted_avg(games, "box_obpr", "minutes"),
            "box_dbpr": weighted_avg(games, "box_dbpr", "minutes"),
            "obpr": weighted_avg(games, "obpr", "minutes"),
            "dbpr": weighted_avg(games, "dbpr", "minutes"),
            "bpr": weighted_avg(games, "bpr", "minutes"),
            "on_off_eff": on_off_eff,
            "off_off_eff": off_off_eff,
            "on_def_eff": on_def_eff,
            "off_def_eff": off_def_eff,
            "off_impact": (on_off_eff - off_off_eff) / 10,
            "def_impact": (off_def_eff - on_def_eff) / 10,
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
        off_eff = rate(g["pf"], g["poss_for"])
        def_eff = rate(g["pa"], g["poss_against"])
        g["off_eff"] = off_eff
        g["def_eff"] = def_eff
        g["net_eff"] = off_eff - def_eff
        rows.append(g)

    rows.sort(key=lambda r: (r["poss_for"], r["net_eff"]), reverse=True)
    return rows


def rebuild_impacts_for_all_games(session: Session):
    games = session.exec(select(Game).order_by(Game.id)).all()

    session.exec(delete(PlayerImpact))
    session.exec(delete(LineupSegment))
    session.commit()

    rebuilt = []
    for game in games:
        stats = session.exec(
            select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)
        ).all()
        events = session.exec(
            select(PlayByPlayEvent)
            .where(PlayByPlayEvent.game_id == game.id)
            .order_by(PlayByPlayEvent.event_number)
        ).all()

        teams = [t for t in [game.away_team, game.home_team] if t]
        if not teams:
            teams = sorted(set(s.team for s in stats))

        segments, impacts = calculate_game_impacts(game.id, stats, events, teams)

        for seg in segments:
            session.add(LineupSegment(**seg))

        impact_by_player = {}
        for imp in impacts:
            impact_by_player[imp["player"]] = imp
            session.add(PlayerImpact(**imp))

        for stat in stats:
            if stat.team == MY_TEAM and stat.player in impact_by_player:
                imp = impact_by_player[stat.player]
                stat.obpr = round(stat.box_obpr + imp["off_impact"], 3)
                stat.dbpr = round(stat.box_dbpr + imp["def_impact"], 3)
                stat.bpr = round(stat.obpr + stat.dbpr, 3)
                session.add(stat)

        rebuilt.append({
            "game_id": game.id,
            "stats": len(stats),
            "events": len(events),
            "teams": teams,
            "segments": len(segments),
            "impacts": len(impacts),
        })

    session.commit()
    return rebuilt


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id.desc())).all()
    top_players = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == MY_TEAM)
        .order_by(PlayerGameStat.bpr.desc())
        .limit(20)
    ).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "games": games, "top_players": top_players, "my_team": MY_TEAM, "world": WORLD},
    )


@app.post("/import")
def import_game(url: str = Form(...), session: Session = Depends(get_session)):
    parsed = parse_game_url(url)

    print("DEBUG SUMMARY:", parsed.summary_rows, flush=True)
    print("DEBUG BOX:", len(parsed.boxscore_rows), flush=True)
    print("DEBUG PBP:", len(parsed.pbp_rows), flush=True)
    if hasattr(parsed, "debug"):
        print("DEBUG FETCH PREVIEW:", parsed.debug, flush=True)

    existing = None
    if parsed.wis_game_id:
        existing = session.exec(
            select(Game).where(Game.wis_game_id == parsed.wis_game_id)
        ).first()

    if existing:
        existing_stats = session.exec(
            select(PlayerGameStat).where(PlayerGameStat.game_id == existing.id)
        ).all()
        existing_events = session.exec(
            select(PlayByPlayEvent).where(PlayByPlayEvent.game_id == existing.id)
        ).all()

        if existing_stats and existing_events:
            return RedirectResponse(f"/games/{existing.id}", status_code=303)

        session.exec(delete(PlayerGameStat).where(PlayerGameStat.game_id == existing.id))
        session.exec(delete(PlayByPlayEvent).where(PlayByPlayEvent.game_id == existing.id))
        session.delete(existing)
        session.commit()

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
    )
    session.add(game)
    session.commit()
    session.refresh(game)

    for row in parsed.boxscore_rows:
        ratings = calculate_box_ratings(row)
        stat = PlayerGameStat(game_id=game.id, **row, **ratings)
        session.add(stat)

    for row in parsed.pbp_rows:
        event = PlayByPlayEvent(game_id=game.id, **row)
        session.add(event)

    session.commit()

    stats = session.exec(
        select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)
    ).all()
    events = session.exec(
        select(PlayByPlayEvent)
        .where(PlayByPlayEvent.game_id == game.id)
        .order_by(PlayByPlayEvent.event_number)
    ).all()
    teams = [t for t in [game.away_team, game.home_team] if t]
    if not teams:
        teams = sorted(set(s.team for s in stats))
    segments, impacts = calculate_game_impacts(game.id, stats, events, teams)

    for seg in segments:
        session.add(LineupSegment(**seg))

    impact_by_player = {}
    for imp in impacts:
        impact_by_player[imp["player"]] = imp
        session.add(PlayerImpact(**imp))

    for stat in stats:
        if stat.team == MY_TEAM and stat.player in impact_by_player:
            imp = impact_by_player[stat.player]
            stat.obpr = round(stat.box_obpr + imp["off_impact"], 3)
            stat.dbpr = round(stat.box_dbpr + imp["def_impact"], 3)
            stat.bpr = round(stat.obpr + stat.dbpr, 3)
            session.add(stat)

    session.commit()
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@app.post("/admin/reset")
def reset_database(session: Session = Depends(get_session)):
    session.exec(delete(PlayerImpact))
    session.exec(delete(LineupSegment))
    session.exec(delete(PlayerGameStat))
    session.exec(delete(PlayByPlayEvent))
    session.exec(delete(Game))
    session.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/diagnostics", response_class=HTMLResponse)
def diagnostics(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id)).all()
    stats = session.exec(select(PlayerGameStat)).all()
    my_stats = session.exec(select(PlayerGameStat).where(PlayerGameStat.team == MY_TEAM)).all()
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
            "my_team": MY_TEAM,
            "world": WORLD,
            "games": games,
            "stats_count": len(stats),
            "my_stats_count": len(my_stats),
            "events_count": len(events),
            "impacts_count": len(impacts),
            "segments_count": len(segments),
            "teams": teams,
            "player_teams": player_teams,
        },
    )


@app.post("/admin/rebuild-impacts")
def rebuild_impacts(session: Session = Depends(get_session)):
    rebuilt = rebuild_impacts_for_all_games(session)
    print("REBUILT IMPACTS:", rebuilt, flush=True)
    return RedirectResponse("/diagnostics", status_code=303)


@app.get("/season", response_class=HTMLResponse)
def season_dashboard(request: Request, session: Session = Depends(get_session)):
    stats = session.exec(
        select(PlayerGameStat).where(PlayerGameStat.team == MY_TEAM)
    ).all()
    impacts = session.exec(
        select(PlayerImpact).where(PlayerImpact.team == MY_TEAM)
    ).all()
    segments = session.exec(
        select(LineupSegment).where(LineupSegment.team == MY_TEAM)
    ).all()
    games = session.exec(select(Game).order_by(Game.id)).all()

    if stats and (not impacts or not segments):
        rebuild_impacts_for_all_games(session)
        impacts = session.exec(
            select(PlayerImpact).where(PlayerImpact.team == MY_TEAM)
        ).all()
        segments = session.exec(
            select(LineupSegment).where(LineupSegment.team == MY_TEAM)
        ).all()

    player_rows = build_player_season_summary(stats, impacts)
    lineup_rows = build_lineup_summary(segments)

    return templates.TemplateResponse(
        "season.html",
        {
            "request": request,
            "my_team": MY_TEAM,
            "world": WORLD,
            "games": games,
            "player_rows": player_rows,
            "lineup_rows": lineup_rows,
        },
    )


@app.get("/season/players/{player_name}", response_class=HTMLResponse)
def player_detail(player_name: str, request: Request, session: Session = Depends(get_session)):
    stats = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == MY_TEAM)
        .where(PlayerGameStat.player == player_name)
        .order_by(PlayerGameStat.game_id)
    ).all()
    impacts = session.exec(
        select(PlayerImpact)
        .where(PlayerImpact.team == MY_TEAM)
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
            "my_team": MY_TEAM,
            "world": WORLD,
            "player": player_name,
            "stats": stats,
            "impact_by_game": impact_by_game,
            "game_by_id": game_by_id,
            "summary": summary_row,
        },
    )


@app.get("/games/{game_id}", response_class=HTMLResponse)
def game_detail(
    request: Request, game_id: int, session: Session = Depends(get_session)
):
    game = session.get(Game, game_id)
    stats = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.game_id == game_id)
        .where(PlayerGameStat.team == MY_TEAM)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()
    impacts = session.exec(
        select(PlayerImpact)
        .where(PlayerImpact.game_id == game_id)
        .order_by(PlayerImpact.off_impact.desc())
    ).all()
    segments = session.exec(
        select(LineupSegment)
        .where(LineupSegment.game_id == game_id)
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
            "my_team": MY_TEAM,
        },
    )


@app.get("/players", response_class=HTMLResponse)
def players(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == MY_TEAM)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()
    return templates.TemplateResponse(
        "players.html",
        {"request": request, "rows": rows},
    )
