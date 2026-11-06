"""Registros guardados no banco. Datas ficam como texto ISO 8601."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchRecord:
    id: int
    started_at: str
    ended_at: str | None
    duration_seconds: int | None
    result: str | None
    player_status: str | None
    squad_status: str | None
    token_obtained: bool
    kills: int
    assists: int
    chests_opened: int | None
    players_left: int | None


@dataclass(frozen=True)
class MatchSummary:
    """O que só se sabe quando a partida termina."""

    ended_at: str
    duration_seconds: int
    result: str
    player_status: str | None
    squad_status: str | None
    kills: int
    assists: int
    chests_opened: int | None
    players_left: int | None


@dataclass(frozen=True)
class MatchTotals:
    matches: int = 0
    victories: int = 0
    defeats: int = 0
    kills: int = 0
    assists: int = 0
    chests_opened: int = 0


@dataclass(frozen=True)
class SquadMemberRecord:
    player_number: int
    status: str
    updated_at: str


@dataclass(frozen=True)
class EventRecord:
    event_type: str
    description: str
    created_at: str
    confidence: float | None = None


@dataclass(frozen=True)
class RewardRecord:
    reward_type: str
    status: str
    confidence: float = 0.0
    detected_at: str | None = None
    evidence_path: str | None = None
    confirmed_by_user: bool = False
    candidate_path: str | None = None
    candidate_confidence: float = 0.0
    match_id: int | None = None


@dataclass(frozen=True)
class MonitorControl:
    token_obtained: bool = False
    shutdown_pending: bool = False
    monitor_stopped: bool = False
