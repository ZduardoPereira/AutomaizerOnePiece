"""Conexão com o SQLite e criação das tabelas."""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

_DAMAGED_FILE_CODES = {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    result TEXT,
    player_status TEXT,
    squad_status TEXT,
    token_obtained INTEGER NOT NULL DEFAULT 0,
    kills INTEGER NOT NULL DEFAULT 0,
    assists INTEGER NOT NULL DEFAULT 0,
    chests_opened INTEGER,
    players_left INTEGER
);
CREATE INDEX IF NOT EXISTS idx_matches_started_at ON matches(started_at);
CREATE INDEX IF NOT EXISTS idx_matches_result ON matches(result);

CREATE TABLE IF NOT EXISTS squad_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (match_id, player_number)
);

CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    reward_type TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    detected_at TEXT,
    evidence_path TEXT,
    confirmed_by_user INTEGER NOT NULL DEFAULT 0,
    candidate_path TEXT,
    candidate_confidence REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rewards_match_id ON rewards(match_id);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER REFERENCES matches(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    confidence REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_match_id ON events(match_id);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at);

CREATE TABLE IF NOT EXISTS monitor_control (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token_obtained INTEGER NOT NULL DEFAULT 0,
    shutdown_pending INTEGER NOT NULL DEFAULT 0,
    monitor_stopped INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO monitor_control (id) VALUES (1);
"""


class Database:
    """Uma conexão compartilhada entre as threads, protegida por uma trava."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.warning: str | None = None
        try:
            self._connection = self._open(path)
        except sqlite3.DatabaseError as exc:
            if exc.sqlite_errorcode not in _DAMAGED_FILE_CODES:
                raise
            logger.exception("Banco de dados danificado: %s", path)
            backup = path.with_name(f"{path.stem}.danificado-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
            os.replace(path, backup)
            self.warning = f"O banco de dados estava danificado e foi guardado como {backup.name}. Um novo foi criado."
            self._connection = self._open(path)

    @staticmethod
    def _open(path: Path) -> sqlite3.Connection:
        # A pasta do projeto pode estar sincronizada pelo OneDrive, que segura os arquivos por instantes.
        connection = sqlite3.connect(path, timeout=15, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # Sem os arquivos extras do modo WAL, e sem criar e apagar o diário a cada gravação.
            connection.execute("PRAGMA journal_mode = TRUNCATE")
            connection.executescript(_SCHEMA)
            connection.commit()
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> int:
        """Executa uma gravação e retorna o id da última linha inserida."""
        with self._lock:
            cursor = self._connection.execute(sql, parameters)
            self._connection.commit()
            return int(cursor.lastrowid or 0)

    def query(self, sql: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, parameters).fetchall()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
