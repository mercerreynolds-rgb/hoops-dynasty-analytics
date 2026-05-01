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
