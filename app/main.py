from __future__ import annotations

import os
import secrets
from fastapi import Depends, FastAPI, Form, Request, HTTPException, status, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlmodel import Session, select, delete

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent, LineupSegment, PlayerImpact, PlayerRatingSnapshot, TrackedTeam
from app.parser import parse_game_url, parse_ratings_history_url, parse_game_log_url, parse_team_ratings_url
from app.ratings import calculate_box_ratings
from app.wis_client import wis_auth_status, get_wis_session
from app.impact import calculate_game_impacts
from app.config import TRACKED_TEAMS, DEFAULT_TEAM, DEFAULT_WORLD, SYNC_TARGETS, RATINGS_SYNC_TARGETS, SYNC_TARGETS, RATINGS_SYNC_TARGETS


security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    expected_username = os.getenv("APP_USERNAME")
    expected_password = os.getenv("APP_PASSWORD")

    # Fail closed if env vars are missing.
    if not expected_username or not expected_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="App login is not configured.",
            headers={"WWW-Authenticate": "Basic"},
        )

    username_ok = secrets.compare_digest(credentials.username, expected_username)
    password_ok = secrets.compare_digest(credentials.password, expected_password)

    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid login.",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username

app = FastAPI(title="Hoops Dynasty Analytics", dependencies=[Depends(require_auth)])
templates = Jinja2Templates(directory="app/templates")

LAST_RATINGS_SYNC_RESULT = []


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




def get_sync_targets():
    targets = []
    for target in SYNC_TARGETS:
        url = os.getenv(target["env"], "").strip() or target.get("default_url", "")
        targets.append({
            **target,
            "url": url,
            "configured": bool(url),
        })
    return targets


def sync_game_log_url(game_log_url: str, session: Session) -> dict:
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
            print("SYNC IMPORT ERROR:", url, exc, flush=True)

    return {
        "source": game_log_url,
        "found": len(parsed["game_urls"]),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    games = session.exec(select(Game).order_by(Game.id.desc())).all()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "games": games, "teams": get_tracked_team_options(), "sync_targets": get_sync_targets(), "ratings_sync_targets": RATINGS_SYNC_TARGETS, "wis_auth": wis_auth_status()},
    )


@app.post("/import")
def import_game(url: str = Form(...), session: Session = Depends(get_session)):
    game, created = save_parsed_game(url, session)
    return RedirectResponse(f"/games/{game.id}", status_code=303)







def import_ratings_history_url(url: str, session: Session, player_name: str = "", team: str = "") -> dict:
    parsed = parse_ratings_history_url(url)
    name = player_name.strip() or parsed.get("player") or parsed.get("player_id") or "Unknown Player"
    team_name = team.strip() or parsed.get("team") or ""

    if parsed.get("player_id"):
        existing = session.exec(
            select(PlayerRatingSnapshot).where(PlayerRatingSnapshot.player_id == parsed["player_id"])
        ).all()
        for row in existing:
            session.delete(row)
        session.commit()

    imported = 0
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
        imported += 1

    session.commit()

    return {
        "url": url,
        "player": name,
        "player_id": parsed.get("player_id"),
        "rows": imported,
    }


def sync_team_ratings_url(ratings_url: str, team: str, session: Session) -> dict:
    parsed = parse_team_ratings_url(ratings_url)

    imported_players = 0
    imported_rows = 0
    errors = 0

    for player in parsed["players"]:
        try:
            result = import_ratings_history_url(
                player["ratings_history_url"],
                session,
                player_name=player.get("player") or "",
                team=team,
            )

            if result["rows"] > 0:
                imported_players += 1
                imported_rows += result["rows"]

        except Exception as exc:
            errors += 1
            print("RATINGS SYNC ERROR:", player, exc, flush=True)

    return {
        "source": ratings_url,
        "players_found": len(parsed["players"]),
        "imported_players": imported_players,
        "imported_rows": imported_rows,
        "errors": errors,
    }


@app.get("/sync-diagnostics", response_class=HTMLResponse)
def sync_diagnostics(request: Request):
    checks = {
        "parse_team_ratings_url_loaded": callable(parse_team_ratings_url),
        "parse_ratings_history_url_loaded": callable(parse_ratings_history_url),
        "wis_auth_mode": wis_auth_status().get("mode"),
    }
    return templates.TemplateResponse(
        "sync_diagnostics.html",
        {"request": request, "checks": checks},
    )

@app.post("/sync-ratings-all")
def sync_ratings_all(session: Session = Depends(get_session)):
    global LAST_RATINGS_SYNC_RESULT
    results = []
    for target in RATINGS_SYNC_TARGETS:
        try:
            result = sync_team_ratings_url(target["url"], target["team"], session)
            result["label"] = target["label"]
            results.append(result)
        except Exception as exc:
            results.append({
                "label": target["label"],
                "players_found": 0,
                "imported_players": 0,
                "imported_rows": 0,
                "errors": 1,
                "error": str(exc),
            })

    LAST_RATINGS_SYNC_RESULT = results
    print("SYNC RATINGS ALL RESULT:", results, flush=True)
    return RedirectResponse("/ratings", status_code=303)


@app.post("/sync-ratings-team")
def sync_ratings_team(ratings_url: str = Form(...), team: str = Form(""), session: Session = Depends(get_session)):
    global LAST_RATINGS_SYNC_RESULT
    result = sync_team_ratings_url(ratings_url, team, session)
    LAST_RATINGS_SYNC_RESULT = [result]
    print("SYNC RATINGS TEAM RESULT:", result, flush=True)
    return RedirectResponse("/ratings", status_code=303)

@app.post("/sync-all")
def sync_all(session: Session = Depends(get_session)):
    results = []
    for target in get_sync_targets():
        if not target["configured"]:
            results.append({
                "source": target["label"],
                "found": 0,
                "imported": 0,
                "skipped": 0,
                "errors": 1,
                "note": f"Missing env var {target['env']}",
            })
            continue

        result = sync_game_log_url(target["url"], session)
        result["source"] = target["label"]
        results.append(result)

    print("SYNC ALL RESULT:", results, flush=True)
    return RedirectResponse("/diagnostics", status_code=303)


@app.post("/sync-team")
def sync_team(game_log_url: str = Form(...), session: Session = Depends(get_session)):
    result = sync_game_log_url(game_log_url, session)
    print("SYNC TEAM RESULT:", result, flush=True)
    return RedirectResponse("/diagnostics", status_code=303)


@app.get("/sync", response_class=HTMLResponse)
def sync_page(request: Request):
    return templates.TemplateResponse(
        "sync.html",
        {
            "request": request,
            "sync_targets": get_sync_targets(),
            "ratings_sync_targets": RATINGS_SYNC_TARGETS,
            "wis_auth": wis_auth_status(),
        },
    )


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


def expected_bpr_from_role_score(role_score_value: float) -> float:
    """
    Transparent first-pass mapping from role score to expected BPR.

    Role scores are roughly 0-100. BPR in this app is currently on a larger
    single-game-style scale, so this intentionally compresses ratings into a
    conservative expected impact range.

    Tune later after enough player-seasons:
      Expected BPR = (BestRoleScore - 50) / 4
    """
    return (role_score_value - 50.0) / 4.0


def decision_label(impact_gap: float) -> str:
    if impact_gap >= 6:
        return "Major Overperformer"
    if impact_gap >= 3:
        return "Overperformer"
    if impact_gap <= -6:
        return "Major Underperformer"
    if impact_gap <= -3:
        return "Underperformer"
    return "As Expected"


def build_rating_lookup(session: Session):
    snapshots = session.exec(select(PlayerRatingSnapshot)).all()
    grouped = {}
    for snap in snapshots:
        if not snap.player:
            continue
        key = snap.player.strip().lower()
        grouped.setdefault(key, []).append(snap)

    lookup = {}
    for key, rows in grouped.items():
        summary = build_rating_summary(rows)
        if summary:
            lookup[key] = {
                "rows": rows,
                "summary": summary,
            }
    return lookup


def build_decision_engine_rows(session: Session, team: str, human_only: bool = False):
    stats = session.exec(select(PlayerGameStat).where(PlayerGameStat.team == team)).all()
    impacts = session.exec(select(PlayerImpact).where(PlayerImpact.team == team)).all()

    if human_only:
        human_game_ids = set()
        for g in session.exec(select(Game)).all():
            if g.away_team == team:
                opp_coach = (g.home_coach or "").strip()
                if opp_coach and opp_coach != "Sim AI":
                    human_game_ids.add(g.id)
            elif g.home_team == team:
                opp_coach = (g.away_coach or "").strip()
                if opp_coach and opp_coach != "Sim AI":
                    human_game_ids.add(g.id)

        stats = [s for s in stats if s.game_id in human_game_ids]
        impacts = [i for i in impacts if i.game_id in human_game_ids]

    perf_rows = build_player_season_summary(stats, impacts)
    ratings_lookup = build_rating_lookup(session)

    rows = []
    for perf in perf_rows:
        player_key = perf["player"].strip().lower()
        rating_obj = ratings_lookup.get(player_key)
        if not rating_obj:
            rows.append({
                **perf,
                "has_ratings": False,
                "current_ovr": None,
                "ovr_growth": None,
                "best_role": "No ratings imported",
                "role_score": 0.0,
                "expected_bpr": 0.0,
                "impact_gap": 0.0,
                "decision": "Import ratings",
            })
            continue

        rating_summary = rating_obj["summary"]
        best_role = rating_summary["best_role"] or {"role": "", "score": 0}
        expected_bpr = expected_bpr_from_role_score(best_role["score"])
        actual_bpr = perf["bpr"]
        impact_gap = actual_bpr - expected_bpr

        rows.append({
            **perf,
            "has_ratings": True,
            "current_ovr": rating_summary["current"].overall,
            "ovr_growth": rating_summary["growth"]["overall"],
            "best_role": best_role["role"],
            "role_score": best_role["score"],
            "expected_bpr": expected_bpr,
            "impact_gap": impact_gap,
            "decision": decision_label(impact_gap),
        })

    rows.sort(key=lambda r: r["impact_gap"], reverse=True)
    return rows



def world_for_team(team: str, fallback: str = "") -> str:
    try:
        for t in get_tracked_team_options():
            if t.get("team") == team:
                return t.get("world") or fallback
    except Exception:
        pass
    return fallback

@app.get("/decision", response_class=HTMLResponse)
def decision_dashboard(
    request: Request,
    team: str = DEFAULT_TEAM if "DEFAULT_TEAM" in globals() else "E. Connecticut St.",
    world: str = DEFAULT_WORLD if "DEFAULT_WORLD" in globals() else "Phelan",
    filter_mode: str = "human",
    session: Session = Depends(get_session),
):
    world = world_for_team(team, world)

    try:
        teams = get_tracked_team_options()
    except Exception:
        teams = [{"team": team, "world": world}]

    # Count available human/all games for this team.
    team_games = [
        g for g in session.exec(select(Game)).all()
        if g.away_team == team or g.home_team == team
    ]
    human_game_ids = set()
    for g in team_games:
        if g.away_team == team:
            opp_coach = (g.home_coach or "").strip()
        else:
            opp_coach = (g.away_coach or "").strip()
        if opp_coach and opp_coach != "Sim AI":
            human_game_ids.add(g.id)

    requested_filter_mode = filter_mode
    if filter_mode not in {"human", "all"}:
        filter_mode = "human"

    # Smart fallback: if there are no human games, use all games.
    fallback_to_all = False
    if filter_mode == "human" and len(human_game_ids) == 0:
        filter_mode = "all"
        fallback_to_all = True

    human_only = filter_mode == "human"

    rows = build_decision_engine_rows(session, team, human_only=human_only)
    missing_count = sum(1 for r in rows if not r["has_ratings"])
    over_count = sum(1 for r in rows if r["decision"] in {"Overperformer", "Major Overperformer"})
    under_count = sum(1 for r in rows if r["decision"] in {"Underperformer", "Major Underperformer"})

    return templates.TemplateResponse(
        "decision.html",
        {
            "request": request,
            "team": team,
            "world": world,
            "teams": teams,
            "rows": rows,
            "missing_count": missing_count,
            "over_count": over_count,
            "under_count": under_count,
            "human_only": human_only,
            "filter_mode": filter_mode,
            "requested_filter_mode": requested_filter_mode,
            "fallback_to_all": fallback_to_all,
            "team_games_count": len(team_games),
            "human_games_count": len(human_game_ids),
            "all_games_count": len(team_games),
        },
    )


COLOR_EXPECTED_GROWTH = {"green": 30, "blue": 20, "black": 10, "yellow": 5, "red": 0, "": 0}
WE_EXPECTED_GROWTH = 20

RATING_META = [
    ("athleticism", "A", True),
    ("speed", "SPD", True),
    ("rebounding", "REB", True),
    ("defense", "DE", True),
    ("shot_blocking", "BLK", True),
    ("low_post", "LP", True),
    ("perimeter", "PE", True),
    ("ball_handling", "BH", True),
    ("passing", "P", True),
    ("work_ethic", "WE", True),
    ("stamina", "ST", True),
    ("durability", "DU", True),
    ("free_throw", "FT", False),
]


FT_GRADE_VALUE = {
    "A+": 100, "A": 95, "A-": 90,
    "B+": 85, "B": 80, "B-": 75,
    "C+": 70, "C": 65, "C-": 60,
    "D+": 55, "D": 50, "D-": 45,
    "F": 35,
}


def rating_numeric_value(snapshot, key: str) -> float:
    raw = getattr(snapshot, key, 0)

    if key == "free_throw":
        if raw is None:
            return 0
        raw_str = str(raw).strip().upper()
        if raw_str in FT_GRADE_VALUE:
            return FT_GRADE_VALUE[raw_str]
        try:
            return float(raw_str)
        except Exception:
            return 0

    try:
        return float(raw or 0)
    except Exception:
        return 0



def infer_color_from_growth(growth: int) -> str:
    if growth >= 30:
        return "green"
    if growth >= 20:
        return "blue"
    if growth >= 10:
        return "black"
    if growth >= 5:
        return "yellow"
    return "red"


def adjusted_expected_growth(color: str, start_value: int, work_ethic: int, rating_key: str) -> float:
    # WE is special in Hoops Dynasty:
    # - always black
    # - not capped by the color-growth system
    # - growth appears driven mostly by playing time
    # - for now, assume average +20 career growth, with low-start WE slightly slower
    if rating_key == "work_ethic":
        if start_value < 40:
            return max(0, WE_EXPECTED_GROWTH - 4)
        if start_value < 55:
            return max(0, WE_EXPECTED_GROWTH - 2)
        if start_value >= 80:
            return WE_EXPECTED_GROWTH + 2
        return WE_EXPECTED_GROWTH

    base = COLOR_EXPECTED_GROWTH.get((color or "").lower(), 0)
    we_adj = 0
    if work_ethic >= 80:
        we_adj = 4
    elif work_ethic >= 70:
        we_adj = 2
    elif work_ethic < 40:
        we_adj = -3
    elif work_ethic < 55:
        we_adj = -1

    # Non-WE ratings are capped at 100.
    return max(0, min(100 - start_value, base + we_adj))


def rating_value(snapshot, key: str):
    return rating_numeric_value(snapshot, key)


def rating_color(snapshot, key: str):
    return (getattr(snapshot, f"{key}_color", "") or "").lower().strip()


def build_potential_summary(rows):
    summary = build_rating_summary(rows)
    if not summary:
        return None

    current = summary["current"]
    start = summary["start"]
    start_we = rating_value(start, "work_ethic")

    rating_rows = []
    projected_total = 0
    current_total = 0
    remaining_total = 0
    color_source_counts = {"detected": 0, "inferred": 0}

    for key, label, include_in_total in RATING_META:
        start_val = rating_value(start, key)
        current_val = rating_value(current, key)
        actual_growth = current_val - start_val
        detected_color = rating_color(start, key)

        if key == "work_ethic":
            color = "black"
            color_source = "fixed"
        elif detected_color:
            color = detected_color
            color_source = "detected"
            color_source_counts["detected"] += 1
        else:
            color = infer_color_from_growth(actual_growth)
            color_source = "inferred"
            color_source_counts["inferred"] += 1

        expected_growth = adjusted_expected_growth(color, start_val, start_we, key)

        if key == "work_ethic":
            # WE has no color cap. Do not clamp expected peak to 100 here.
            expected_peak = round(start_val + expected_growth, 1)
        else:
            expected_peak = min(100, round(start_val + expected_growth, 1))

        remaining = max(0, expected_peak - current_val)
        outlier_flag = color == "green" and key in {"perimeter", "low_post"} and start_we >= 55

        if include_in_total:
            projected_total += expected_peak
            current_total += current_val
            remaining_total += remaining

        rating_rows.append({
            "key": key,
            "label": label,
            "start": start_val,
            "current": current_val,
            "start_display": getattr(start, key, start_val),
            "current_display": getattr(current, key, current_val),
            "growth": actual_growth,
            "color": color,
            "color_source": color_source,
            "expected_growth": expected_growth,
            "expected_peak": expected_peak,
            "remaining": remaining,
            "include_in_total": include_in_total,
            "outlier_flag": outlier_flag,
        })

    projected_proxy = type("ProjectedSnapshot", (), {})()
    for r in rating_rows:
        setattr(projected_proxy, r["key"], r["expected_peak"])

    role_scores = []
    for role, weights in ROLE_WEIGHTS.items():
        current_score = role_score(current, weights)
        projected_score = role_score(projected_proxy, weights)
        role_scores.append({
            "role": role, "current_score": current_score,
            "projected_score": projected_score,
            "projected_gain": projected_score - current_score,
        })
    role_scores.sort(key=lambda r: r["projected_score"], reverse=True)

    included_count = sum(1 for r in rating_rows if r.get("include_in_total"))
    avg_remaining = remaining_total / included_count if included_count else 0
    projected_ovr = min(1000, round((current.overall or 0) + avg_remaining * 10, 1))

    return {
        "summary": summary, "current": current, "start": start,
        "baseline_note": summary.get("baseline_note", ""),
        "rating_rows": rating_rows, "role_scores": role_scores,
        "best_projected_role": role_scores[0] if role_scores else None,
        "current_total": current_total, "projected_total": projected_total,
        "remaining_total": remaining_total, "projected_ovr": projected_ovr,
        "color_source_counts": color_source_counts,
        "has_green_offense_outlier": any(r["outlier_flag"] for r in rating_rows),
    }


def build_all_potential_rows(session: Session, team: str = ""):
    snapshots = session.exec(select(PlayerRatingSnapshot)).all()
    grouped = {}
    for snap in snapshots:
        if team and (snap.team or "") != team:
            continue
        key = (snap.player or "Unknown Player", snap.player_id or "", snap.team or "")
        grouped.setdefault(key, []).append(snap)

    rows = []
    for (player, player_id, snap_team), snap_rows in grouped.items():
        potential = build_potential_summary(snap_rows)
        if not potential:
            continue
        best_role = potential["best_projected_role"] or {"role": "", "projected_score": 0, "projected_gain": 0}
        rows.append({
            "player": player, "player_id": player_id, "team": snap_team,
            "current_ovr": potential["current"].overall,
            "projected_ovr": potential["projected_ovr"],
            "remaining_total": potential["remaining_total"],
            "best_projected_role": best_role["role"],
            "projected_role_score": best_role["projected_score"],
            "projected_role_gain": best_role["projected_gain"],
            "baseline_note": potential["baseline_note"],
            "has_green_offense_outlier": potential["has_green_offense_outlier"],
            "detected_colors": potential["color_source_counts"]["detected"],
            "inferred_colors": potential["color_source_counts"]["inferred"],
        })

    rows.sort(key=lambda r: (r["remaining_total"], r["projected_role_score"]), reverse=True)
    return rows

@app.get("/potential", response_class=HTMLResponse)
def potential_dashboard(request: Request, team: str = "", session: Session = Depends(get_session)):
    try:
        teams = get_tracked_team_options()
    except Exception:
        teams = []
    rows = build_all_potential_rows(session, team=team)
    return templates.TemplateResponse(
        "potential.html",
        {"request": request, "rows": rows, "teams": teams, "team": team},
    )


@app.get("/potential/{player_id}", response_class=HTMLResponse)
def potential_player_detail(player_id: str, request: Request, session: Session = Depends(get_session)):
    rows = session.exec(
        select(PlayerRatingSnapshot).where(PlayerRatingSnapshot.player_id == player_id)
    ).all()
    rows = sorted(rows, key=lambda r: (int(r.season or 0), r.id or 0), reverse=True)
    potential = build_potential_summary(rows)
    return templates.TemplateResponse(
        "potential_detail.html",
        {"request": request, "rows": rows, "potential": potential, "player": rows[0].player if rows else player_id, "player_id": player_id},
    )



@app.post("/ratings/import")
def import_ratings_history(
    url: str = Form(...),
    player_name: str = Form(""),
    team: str = Form(""),
    session: Session = Depends(get_session),
):
    result = import_ratings_history_url(url, session, player_name=player_name, team=team)
    print("RATINGS IMPORT RESULT:", result, flush=True)
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
            "last_sync_result": LAST_RATINGS_SYNC_RESULT,
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
    team: str = DEFAULT_TEAM if "DEFAULT_TEAM" in globals() else "E. Connecticut St.",
    world: str = DEFAULT_WORLD if "DEFAULT_WORLD" in globals() else "Phelan",
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(PlayerGameStat)
        .where(PlayerGameStat.team == team)
        .order_by(PlayerGameStat.bpr.desc())
    ).all()

    try:
        teams = get_tracked_team_options()
    except Exception:
        teams = [{"team": team, "world": world}]

    return templates.TemplateResponse(
        "players.html",
        {
            "request": request,
            "rows": rows,
            "team": team,
            "world": world,
            "teams": teams,
        },
    )
