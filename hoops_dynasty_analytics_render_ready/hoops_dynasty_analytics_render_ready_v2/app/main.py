from __future__ import annotations

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent
from app.parser import parse_game_url
from app.ratings import calculate_box_ratings

app = FastAPI(title="Hoops Dynasty Analytics")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id.desc())).all()
    top_players = session.exec(
        select(PlayerGameStat).order_by(PlayerGameStat.bpr.desc()).limit(20)
    ).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "games": games, "top_players": top_players},
    )


@app.post("/import")
def import_game(url: str = Form(...), session: Session = Depends(get_session)):
    parsed = parse_game_url(url)

    existing = None
    if parsed.wis_game_id:
        existing = session.exec(
            select(Game).where(Game.wis_game_id == parsed.wis_game_id)
        ).first()

    if existing:
        return RedirectResponse(f"/games/{existing.id}", status_code=303)

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
    return RedirectResponse(f"/games/{game.id}", status_code=303)


@app.get("/games/{game_id}", response_class=HTMLResponse)
def game_detail(
    request: Request, game_id: int, session: Session = Depends(get_session)
):
    game = session.get(Game, game_id)
    stats = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.game_id == game_id)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()
    events = session.exec(
        select(PlayByPlayEvent)
        .where(PlayByPlayEvent.game_id == game_id)
        .order_by(PlayByPlayEvent.event_number)
    ).all()
    return templates.TemplateResponse(
        "game_detail.html",
        {"request": request, "game": game, "stats": stats, "events": events[:200]},
    )


@app.get("/players", response_class=HTMLResponse)
def players(request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(PlayerGameStat).order_by(PlayerGameStat.bpr.desc())).all()
    return templates.TemplateResponse(
        "players.html",
        {"request": request, "rows": rows},
    )
