"""Acesso às tabelas: cada repositório cuida de uma delas."""

from __future__ import annotations

from collections.abc import Iterable
from sqlite3 import Row

from database.database import Database
from database.models import (
    EventRecord,
    MatchRecord,
    MatchSummary,
    MatchTotals,
    MonitorControl,
    RewardRecord,
    SquadMemberRecord,
)

_UNDETERMINED = "UNDETERMINED"


def _match_from_row(row: Row) -> MatchRecord:
    return MatchRecord(
        row["id"],
        row["started_at"],
        row["ended_at"],
        row["duration_seconds"],
        row["result"],
        row["player_status"],
        row["squad_status"],
        bool(row["token_obtained"]),
        row["kills"],
        row["assists"],
        row["chests_opened"],
        row["players_left"],
    )


class MatchRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def create(self, started_at: str) -> int:
        return self._database.execute("INSERT INTO matches (started_at) VALUES (?)", (started_at,))

    def finish(self, match_id: int, summary: MatchSummary) -> None:
        self._database.execute(
            """
            UPDATE matches
            SET ended_at = ?, duration_seconds = ?, result = ?, player_status = ?, squad_status = ?,
                kills = ?, assists = ?, chests_opened = ?, players_left = ?
            WHERE id = ?
            """,
            (
                summary.ended_at,
                summary.duration_seconds,
                summary.result,
                summary.player_status,
                summary.squad_status,
                summary.kills,
                summary.assists,
                summary.chests_opened,
                summary.players_left,
                match_id,
            ),
        )

    def mark_token_obtained(self, match_id: int) -> None:
        self._database.execute("UPDATE matches SET token_obtained = 1 WHERE id = ?", (match_id,))

    def close_unfinished(self, ended_at: str) -> int:
        """Encerra partidas que ficaram abertas porque o aplicativo foi fechado no meio delas."""
        rows = self._database.query("SELECT id FROM matches WHERE ended_at IS NULL")
        for row in rows:
            self._database.execute(
                "UPDATE matches SET ended_at = ?, result = ? WHERE id = ?", (ended_at, _UNDETERMINED, row["id"])
            )
        return len(rows)

    def recent(self, limit: int = 100) -> list[MatchRecord]:
        rows = self._database.query("SELECT * FROM matches ORDER BY id DESC LIMIT ?", (limit,))
        return [_match_from_row(row) for row in rows]

    def totals(self) -> MatchTotals:
        row = self._database.query(
            """
            SELECT COUNT(*) AS matches,
                   COALESCE(SUM(result = 'VICTORY'), 0) AS victories,
                   COALESCE(SUM(result = 'DEFEAT'), 0) AS defeats,
                   COALESCE(SUM(kills), 0) AS kills,
                   COALESCE(SUM(assists), 0) AS assists,
                   COALESCE(SUM(chests_opened), 0) AS chests_opened
            FROM matches
            """
        )[0]
        return MatchTotals(row["matches"], row["victories"], row["defeats"], row["kills"], row["assists"], row["chests_opened"])


class SquadRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, match_id: int, statuses: Iterable[str], updated_at: str) -> None:
        for player_number, status in enumerate(statuses, start=1):
            self._database.execute(
                """
                INSERT INTO squad_members (match_id, player_number, status, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT (match_id, player_number) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
                """,
                (match_id, player_number, status, updated_at),
            )

    def for_match(self, match_id: int) -> list[SquadMemberRecord]:
        rows = self._database.query(
            "SELECT player_number, status, updated_at FROM squad_members WHERE match_id = ? ORDER BY player_number", (match_id,)
        )
        return [SquadMemberRecord(row["player_number"], row["status"], row["updated_at"]) for row in rows]


class EventRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, match_id: int | None, event: EventRecord) -> None:
        self._database.execute(
            "INSERT INTO events (match_id, event_type, description, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
            (match_id, event.event_type, event.description, event.confidence, event.created_at),
        )

    def for_match(self, match_id: int) -> list[EventRecord]:
        rows = self._database.query(
            "SELECT event_type, description, confidence, created_at FROM events WHERE match_id = ? ORDER BY id", (match_id,)
        )
        return [EventRecord(row["event_type"], row["description"], row["created_at"], row["confidence"]) for row in rows]


class RewardRepository:
    """Cada tipo de recompensa tem uma única linha, o que evita registros duplicados."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def load(self, reward_type: str) -> RewardRecord | None:
        rows = self._database.query("SELECT * FROM rewards WHERE reward_type = ?", (reward_type,))
        if not rows:
            return None
        row = rows[0]
        return RewardRecord(
            row["reward_type"],
            row["status"],
            row["confidence"],
            row["detected_at"],
            row["evidence_path"],
            bool(row["confirmed_by_user"]),
            row["candidate_path"],
            row["candidate_confidence"],
            row["match_id"],
        )

    def save(self, record: RewardRecord) -> None:
        self._database.execute(
            """
            INSERT INTO rewards (
                reward_type, status, confidence, detected_at, evidence_path, confirmed_by_user,
                candidate_path, candidate_confidence, match_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (reward_type) DO UPDATE SET
                status = excluded.status,
                confidence = excluded.confidence,
                detected_at = excluded.detected_at,
                evidence_path = excluded.evidence_path,
                confirmed_by_user = excluded.confirmed_by_user,
                candidate_path = excluded.candidate_path,
                candidate_confidence = excluded.candidate_confidence,
                match_id = excluded.match_id
            """,
            (
                record.reward_type,
                record.status,
                record.confidence,
                record.detected_at,
                record.evidence_path,
                int(record.confirmed_by_user),
                record.candidate_path,
                record.candidate_confidence,
                record.match_id,
            ),
        )

    def delete(self, reward_type: str) -> None:
        self._database.execute("DELETE FROM rewards WHERE reward_type = ?", (reward_type,))


class MonitorControlRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def load(self) -> MonitorControl:
        row = self._database.query("SELECT token_obtained, shutdown_pending, monitor_stopped FROM monitor_control WHERE id = 1")[0]
        return MonitorControl(bool(row["token_obtained"]), bool(row["shutdown_pending"]), bool(row["monitor_stopped"]))

    def save(self, control: MonitorControl) -> None:
        self._database.execute(
            "UPDATE monitor_control SET token_obtained = ?, shutdown_pending = ?, monitor_stopped = ? WHERE id = 1",
            (int(control.token_obtained), int(control.shutdown_pending), int(control.monitor_stopped)),
        )
