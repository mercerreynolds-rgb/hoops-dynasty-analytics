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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id.desc())).all()
    top_players = session.exec(
        select(PlayerGameStat).where(PlayerGameStat.team == MY_TEAM).order_by(PlayerGameStat.bpr.desc()).limit(20)
    ).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "games": games, "top_players": top_players, "my_team": MY_TEAM, "world": WORLD},
    )


@app.post("/import")
def import_game(url: str = Form(...), session: Session = Depends(get_session)):
    parsed = parse_game_url(url)

    existing = None
    if parsed.wis_game_id:
        existing = session.exec(
            select(Game).where(Game.wis_game_id == parsed.wis_game_id)
        ).first()

    # If a previous parser version created a blank shell for this game,
    # remove it and re-import with the current parser.
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

    # Build true ECSU on/off impact after raw stats/events are saved.
    stats = session.exec(
        select(PlayerGameStat).where(PlayerGameStat.game_id == game.id)
    ).all()
    events = session.exec(
        select(PlayByPlayEvent)
        .where(PlayByPlayEvent.game_id == game.id)
        .order_by(PlayByPlayEvent.event_number)
    ).all()
    teams = [t for t in [game.away_team, game.home_team] if t]
    segments, impacts = calculate_game_impacts(game.id, stats, events, teams)

    for seg in segments:
        session.add(LineupSegment(**seg))

    impact_by_player = {}
    for imp in impacts:
        impact_by_player[imp["player"]] = imp
        session.add(PlayerImpact(**imp))

    # Blend box ratings with lineup impact for MY_TEAM only.
    for stat in stats:
        if stat.team == MY_TEAM and stat.player in impact_by_player:
            imp = impact_by_player[stat.player]
            stat.obpr = round(stat.box_obpr + imp["off_impact"], 3)
            stat.dbpr = round(stat.box_dbpr + imp["def_impact"], 3)
            stat.bpr = round(stat.obpr + stat.dbpr, 3)
            session.add(stat)

    session.commit()
    return RedirectResponse(f"/games/{game.id}", status_code=303)


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
        {"request": request, "game": game, "stats": stats, "events": events[:200], "impacts": impacts, "segments": segments, "my_team": MY_TEAM},
    )



@app.post("/admin/reset")
def reset_database(session: Session = Depends(get_session)):
    session.exec(delete(PlayerImpact))
    session.exec(delete(LineupSegment))
    session.exec(delete(PlayerGameStat))
    session.exec(delete(PlayByPlayEvent))
    session.exec(delete(Game))
    session.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/players", response_class=HTMLResponse)
def players(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(PlayerGameStat).where(PlayerGameStat.team == MY_TEAM).order_by(PlayerGameStat.bpr.desc())).all()
    return templates.TemplateResponse(
        "players.html",
        {"request": request, "rows": rows},
    )
