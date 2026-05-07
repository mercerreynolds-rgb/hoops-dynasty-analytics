from __future__ import annotations

from typing import Optional
from sqlmodel import SQLModel, Field


class Game(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_url: str
    wis_game_id: Optional[str] = None
    game_date: Optional[str] = None
    away_team: Optional[str] = None
    home_team: Optional[str] = None
    away_score: Optional[int] = None
    home_score: Optional[int] = None
    away_coach: Optional[str] = None
    home_coach: Optional[str] = None


class PlayerGameStat(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    team: str
    role: str
    pos: str
    player: str

    minutes: int = 0
    fgm: int = 0
    fga: int = 0
    fg3m: int = 0
    fg3a: int = 0
    ftm: int = 0
    fta: int = 0
    orb: int = 0
    reb: int = 0
    ast: int = 0
    tov: int = 0
    stl: int = 0
    blk: int = 0
    pf: int = 0
    pts: int = 0

    box_obpr: float = 0.0
    box_dbpr: float = 0.0
    obpr: float = 0.0
    dbpr: float = 0.0
    bpr: float = 0.0


class PlayByPlayEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    event_number: int
    half: Optional[str] = None
    clock: Optional[str] = None
    team: Optional[str] = None
    description: str
    score: Optional[str] = None
    event_type: str = "other"
class LineupSegment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    segment_number: int
    half: Optional[str] = None
    start_clock: Optional[str] = None
    end_clock: Optional[str] = None
    team: str
    lineup: str
    points_for: int = 0
    points_against: int = 0
    possessions_for: int = 0
    possessions_against: int = 0


class PlayerImpact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    game_id: int = Field(index=True)
    player: str
    team: str

    on_possessions_for: int = 0
    on_points_for: int = 0
    on_possessions_against: int = 0
    on_points_against: int = 0

    off_possessions_for: int = 0
    off_points_for: int = 0
    off_possessions_against: int = 0
    off_points_against: int = 0

    on_off_eff: float = 0.0
    off_off_eff: float = 0.0
    on_def_eff: float = 0.0
    off_def_eff: float = 0.0

    off_impact: float = 0.0
    def_impact: float = 0.0
class TrackedTeam(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    team: str = Field(index=True)
    world: str = Field(index=True)
    game_log_url: Optional[str] = None
class PlayerRatingSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    player_id: Optional[str] = Field(default=None, index=True)
    tid: Optional[str] = Field(default=None, index=True)
    player: Optional[str] = Field(default=None, index=True)
    team: Optional[str] = Field(default=None, index=True)
    source_url: str

    season: Optional[str] = None
    snapshot_type: str

    athleticism: int = 0
    speed: int = 0
    rebounding: int = 0
    defense: int = 0
    shot_blocking: int = 0
    low_post: int = 0
    perimeter: int = 0
    ball_handling: int = 0
    passing: int = 0
    work_ethic: int = 0
    stamina: int = 0
    durability: str = ""
    free_throw: str = ""
    overall: int = 0

    athleticism_color: str = ""
    speed_color: str = ""
    rebounding_color: str = ""
    defense_color: str = ""
    shot_blocking_color: str = ""
    low_post_color: str = ""
    perimeter_color: str = ""
    ball_handling_color: str = ""
    passing_color: str = ""
    work_ethic_color: str = ""
    stamina_color: str = ""
    durability_color: str = ""
    free_throw_color: str = ""


class PlayerPotentialColorOverride(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    player_id: str = Field(index=True)
    rating_key: str = Field(index=True)
    color: str = ""
