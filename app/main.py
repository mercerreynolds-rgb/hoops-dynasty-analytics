from __future__ import annotations

import os
import secrets
from fastapi import Depends, FastAPI, Form, Request, HTTPException, status, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlmodel import Session, select, delete

from app.database import get_session, init_db
from app.models import Game, PlayerGameStat, PlayByPlayEvent, LineupSegment, PlayerImpact, PlayerRatingSnapshot, TrackedTeam, PlayerProjectionProfile
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


PROJECTION_RATINGS = [
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
PROJECTION_COLORS = ["green", "blue", "black", "yellow", "red"]
BASE_GROWTH = {"green": 36, "blue": 22, "black": 13, "yellow": 3, "red": 1}
MINUTES_TIER = {
    "green": {"label": "Very High / 4-year starter", "minutes": "3000+", "multiplier": 1.10},
    "blue": {"label": "High / multi-year starter", "minutes": "2400-2999", "multiplier": 1.05},
    "black": {"label": "Average / rotation-starter", "minutes": "1800-2399", "multiplier": 1.00},
    "yellow": {"label": "Low / role player", "minutes": "1000-1799", "multiplier": 0.92},
    "red": {"label": "Very Low / bench", "minutes": "<1000", "multiplier": 0.82},
}


def projection_rating_value(snapshot, key: str) -> int:
    try:
        return int(getattr(snapshot, key, 0) or 0)
    except Exception:
        return 0


def projection_season_int(snapshot) -> int:
    try:
        return int(snapshot.season or 0)
    except Exception:
        return 0


def get_projection_baseline(rows):
    if not rows:
        return None
    valid = [r for r in rows if projection_season_int(r) > 0]
    if not valid:
        return rows[0]
    min_season = min(projection_season_int(r) for r in valid)
    season_rows = [r for r in valid if projection_season_int(r) == min_season]
    for r in season_rows:
        if (r.snapshot_type or "").strip().lower() == "season start":
            return r
    return season_rows[0]


def we_multiplier(we: int) -> float:
    if we >= 90:
        return 1.12
    if we >= 80:
        return 1.08
    if we >= 70:
        return 1.04
    if we >= 60:
        return 1.00
    if we >= 50:
        return 0.96
    return 0.90


def seasons_multiplier(seasons: int) -> float:
    if seasons >= 5:
        return 1.10
    if seasons == 4:
        return 1.00
    if seasons == 3:
        return 0.82
    if seasons == 2:
        return 0.60
    return 0.35


def cap_growth(start_rating: int, growth: float) -> float:
    return max(0, min(float(growth), 100 - start_rating))


def get_or_create_projection_profile(session: Session, baseline):
    profile = session.exec(
        select(PlayerProjectionProfile).where(PlayerProjectionProfile.player_id == baseline.player_id)
    ).first()
    if profile:
        profile.player = baseline.player or profile.player
        profile.team = baseline.team or profile.team
        session.add(profile)
        session.commit()
        return profile

    profile = PlayerProjectionProfile(
        player_id=baseline.player_id or "",
        player=baseline.player or "",
        team=baseline.team or "",
        pos="",
        seasons_in_program=4,
        development_tier="black",
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def projection_color_for(profile, key: str) -> str:
    if key == "work_ethic":
        return "black"
    color = (getattr(profile, f"{key}_color", "black") or "black").lower()
    return color if color in PROJECTION_COLORS else "black"


def project_player(profile, baseline):
    we = projection_rating_value(baseline, "work_ethic")
    minutes_info = MINUTES_TIER.get(profile.development_tier, MINUTES_TIER["black"])
    minutes_mult = minutes_info["multiplier"]
    season_mult = seasons_multiplier(profile.seasons_in_program)
    we_mult = we_multiplier(we)

    rows = []
    start_ovr = 0
    projected_ovr = 0

    for key, label, include in PROJECTION_RATINGS:
        start_value = projection_rating_value(baseline, key)
        if key == "work_ethic":
            color = "black"
            base_growth = 20
            raw_growth = base_growth * season_mult * minutes_mult
        else:
            color = projection_color_for(profile, key)
            base_growth = BASE_GROWTH[color]
            raw_growth = base_growth * season_mult * minutes_mult * we_mult

        growth = cap_growth(start_value, raw_growth)
        projected = min(100, start_value + growth)

        if include:
            start_ovr += start_value
            projected_ovr += projected

        rows.append({
            "key": key,
            "label": label,
            "start": start_value,
            "color": color,
            "base_growth": base_growth,
            "growth": growth,
            "projected": projected,
            "include": include,
        })

    return {
        "rows": rows,
        "start_ovr": start_ovr,
        "projected_ovr": projected_ovr,
        "growth_ovr": projected_ovr - start_ovr,
        "we": we,
        "we_multiplier": we_mult,
        "seasons_multiplier": season_mult,
        "minutes_multiplier": minutes_mult,
        "minutes_info": minutes_info,
    }



PROJECTION_ROLE_WEIGHTS = {
    "Lead Guard": {"speed": 1.0, "perimeter": 1.1, "ball_handling": 1.4, "passing": 1.4, "defense": 0.8, "stamina": 0.5},
    "Scoring Guard": {"speed": 0.9, "perimeter": 1.5, "ball_handling": 1.1, "passing": 0.6, "defense": 0.7, "stamina": 0.5},
    "Two-Way Wing": {"athleticism": 1.0, "speed": 0.8, "defense": 1.3, "perimeter": 1.0, "ball_handling": 0.7, "passing": 0.6, "stamina": 0.5},
    "Stretch Forward": {"athleticism": 0.8, "rebounding": 0.8, "defense": 0.9, "low_post": 0.7, "perimeter": 1.3, "stamina": 0.5},
    "Interior Big": {"athleticism": 0.8, "rebounding": 1.4, "defense": 1.0, "shot_blocking": 1.2, "low_post": 1.2, "stamina": 0.5},
    "Defensive Stopper": {"athleticism": 1.0, "speed": 0.9, "defense": 1.6, "shot_blocking": 0.7, "stamina": 0.6},
}


def projected_rating_map(projection):
    return {r["key"]: r["projected"] for r in projection["rows"]}


def score_projected_role(projection, weights):
    ratings = projected_rating_map(projection)
    total_weight = sum(weights.values()) or 1
    return sum(ratings.get(key, 0) * weight for key, weight in weights.items()) / total_weight


def projected_role_scores(projection):
    scores = [{"role": role, "score": score_projected_role(projection, weights)} for role, weights in PROJECTION_ROLE_WEIGHTS.items()]
    scores.sort(key=lambda r: r["score"], reverse=True)
    return scores


def projection_flag(player_row):
    growth = player_row["growth_ovr"]
    projected = player_row["projected_ovr"]
    tier = player_row["profile"].development_tier
    if projected >= 760:
        return "Star ceiling"
    if growth >= 120:
        return "Huge development upside"
    if tier in {"green", "blue"} and projected < 650:
        return "Minutes investment risk"
    if projected >= 700:
        return "Strong rotation piece"
    return "Depth / monitor"


def get_projection_players(session: Session, team: str = ""):
    snapshots = session.exec(select(PlayerRatingSnapshot)).all()
    grouped = {}
    for s in snapshots:
        if team and (s.team or "") != team:
            continue
        if not s.player_id:
            continue
        grouped.setdefault(s.player_id, []).append(s)

    players = []
    for player_id, rows in grouped.items():
        baseline = get_projection_baseline(rows)
        if not baseline:
            continue
        profile = get_or_create_projection_profile(session, baseline)
        projection = project_player(profile, baseline)
        role_scores = projected_role_scores(projection)
        row = {
            "player_id": player_id,
            "player": baseline.player,
            "team": baseline.team,
            "baseline": baseline,
            "profile": profile,
            "start_ovr": projection["start_ovr"],
            "projected_ovr": projection["projected_ovr"],
            "growth_ovr": projection["growth_ovr"],
            "best_role": role_scores[0]["role"] if role_scores else "",
            "best_role_score": role_scores[0]["score"] if role_scores else 0,
        }
        row["flag"] = projection_flag(row)
        players.append(row)
    players.sort(key=lambda r: r["projected_ovr"], reverse=True)
    return players


@app.get("/projections", response_class=HTMLResponse)
def projections_board(request: Request, team: str = "", sort: str = "projected", dev_tier: str = "", session: Session = Depends(get_session)):
    try:
        teams = get_tracked_team_options()
    except Exception:
        teams = []
    players = get_projection_players(session, team=team)

    if dev_tier:
        players = [p for p in players if p["profile"].development_tier == dev_tier]

    if sort == "growth":
        players.sort(key=lambda p: p["growth_ovr"], reverse=True)
    elif sort == "start":
        players.sort(key=lambda p: p["start_ovr"], reverse=True)
    elif sort == "role":
        players.sort(key=lambda p: p["best_role_score"], reverse=True)
    else:
        players.sort(key=lambda p: p["projected_ovr"], reverse=True)

    top_projected = players[:5]
    top_growth = sorted(players, key=lambda p: p["growth_ovr"], reverse=True)[:5]
    role_counts = {}
    for p in players:
        role_counts[p["best_role"]] = role_counts.get(p["best_role"], 0) + 1
    role_summary = [{"role": k, "count": v} for k, v in sorted(role_counts.items(), key=lambda x: x[0])]

    return templates.TemplateResponse(
        "projections.html",
        {
            "request": request,
            "players": players,
            "teams": teams,
            "team": team,
            "sort": sort,
            "dev_tier": dev_tier,
            "minutes_tier": MINUTES_TIER,
            "top_projected": top_projected,
            "top_growth": top_growth,
            "role_summary": role_summary,
        },
    )


@app.get("/projections/{player_id}", response_class=HTMLResponse)
def projection_detail(player_id: str, request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(PlayerRatingSnapshot).where(PlayerRatingSnapshot.player_id == player_id)).all()
    baseline = get_projection_baseline(rows)
    if not baseline:
        return templates.TemplateResponse("projection_detail.html", {"request": request, "baseline": None, "player_id": player_id})
    profile = get_or_create_projection_profile(session, baseline)
    projection = project_player(profile, baseline)
    return templates.TemplateResponse(
        "projection_detail.html",
        {
            "request": request,
            "player_id": player_id,
            "player": baseline.player,
            "baseline": baseline,
            "profile": profile,
            "projection": projection,
            "role_scores": projected_role_scores(projection),
            "ratings": PROJECTION_RATINGS,
            "colors": PROJECTION_COLORS,
            "minutes_tier": MINUTES_TIER,
            "scenario_4": project_player(type("ScenarioProfile", (), {**profile.__dict__, "seasons_in_program": 4})(), baseline),
            "scenario_5": project_player(type("ScenarioProfile", (), {**profile.__dict__, "seasons_in_program": 5})(), baseline),
        },
    )


@app.post("/projections/{player_id}")
async def save_projection_profile(player_id: str, request: Request, session: Session = Depends(get_session)):
    rows = session.exec(select(PlayerRatingSnapshot).where(PlayerRatingSnapshot.player_id == player_id)).all()
    baseline = get_projection_baseline(rows)
    if not baseline:
        return RedirectResponse("/projections", status_code=303)

    profile = get_or_create_projection_profile(session, baseline)
    form = await request.form()

    try:
        profile.seasons_in_program = int(form.get("seasons_in_program", profile.seasons_in_program))
    except Exception:
        pass

    dev = str(form.get("development_tier", profile.development_tier)).lower()
    if dev in PROJECTION_COLORS:
        profile.development_tier = dev

    profile.pos = str(form.get("pos", profile.pos or "")).strip()

    for key, _label, _include in PROJECTION_RATINGS:
        if key == "work_ethic":
            continue
        color = str(form.get(f"{key}_color", getattr(profile, f"{key}_color", "black"))).lower()
        if color in PROJECTION_COLORS:
            setattr(profile, f"{key}_color", color)

    session.add(profile)
    session.commit()
    return RedirectResponse(f"/projections/{player_id}", status_code=303)


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
