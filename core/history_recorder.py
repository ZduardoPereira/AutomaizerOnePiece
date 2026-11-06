"""Grava no banco as partidas, os integrantes e os eventos, sem interromper o monitoramento se falhar."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime

from core.event_manager import AppEvent
from database.database import Database
from database.models import EventRecord, MatchRecord, MatchSummary, MatchTotals, SquadMemberRecord
from database.repositories import EventRepository, MatchRepository, SquadRepository
from recognition.hud_reader import MatchNumbers
from recognition.match_detector import MatchResult
from recognition.survival_detector import SquadStatus

logger = logging.getLogger(__name__)


class HistoryRecorder:
    """As gravações entram em uma fila e rodam em uma thread própria, na ordem em que foram pedidas.

    Uma gravação já levou 6 s com o jogo carregando uma partida, e a análise do jogo, que guia o mouse, não pode
    esperar o disco.
    """

    def __init__(self, database: Database) -> None:
        self._matches = MatchRepository(database)
        self._squad = SquadRepository(database)
        self._events = EventRepository(database)
        self._match_id: int | None = None
        self._live_squad = SquadStatus()
        self._live_numbers = MatchNumbers()
        self._saved_statuses: tuple[str, ...] = ()
        self._version = 0
        self._totals = MatchTotals()
        self._safely(lambda: self._matches.close_unfinished(self._now()))
        self._refresh_totals()
        self._pending: queue.SimpleQueue[Callable[[], None] | None] = queue.SimpleQueue()
        self._worker = threading.Thread(target=self._run_pending, name="history", daemon=True)
        self._worker.start()

    @property
    def current_match_id(self) -> int | None:
        return self._match_id

    @property
    def version(self) -> int:
        """Muda quando uma partida termina, para a interface saber quando recarregar a lista."""
        return self._version

    @property
    def totals(self) -> MatchTotals:
        return self._totals

    def match_started(self) -> None:
        self._submit(self._start_match)

    def update_live(self, squad: SquadStatus, numbers: MatchNumbers) -> None:
        """Guarda o estado atual, que será o resultado final quando a partida terminar."""
        self._submit(lambda: self._save_live(squad, numbers))

    def match_ended(self, result: MatchResult, duration_seconds: int) -> None:
        self._submit(lambda: self._finish_match(result, duration_seconds))

    def mark_token_obtained(self, match_id: int | None) -> None:
        if match_id is not None:
            self._submit(lambda: self._mark_token(match_id))

    def record_event(self, event: AppEvent) -> None:
        record = EventRecord(event.type.value, event.message, event.created_at.isoformat(timespec="seconds"), event.confidence)
        self._submit(lambda: self._safely(lambda: self._events.add(self._match_id, record)))

    def recent_matches(self, limit: int = 100) -> list[MatchRecord]:
        return self._safely(lambda: self._matches.recent(limit)) or []

    def squad_of(self, match_id: int) -> list[SquadMemberRecord]:
        return self._safely(lambda: self._squad.for_match(match_id)) or []

    def events_of(self, match_id: int) -> list[EventRecord]:
        return self._safely(lambda: self._events.for_match(match_id)) or []

    def flush(self, timeout: float = 20.0) -> bool:
        """Espera as gravações já pedidas terminarem."""
        done = threading.Event()
        self._pending.put(done.set)
        return done.wait(timeout)

    def close(self, timeout: float = 5.0) -> None:
        self._pending.put(None)
        self._worker.join(timeout)
        if self._worker.is_alive():
            logger.error("As gravações do histórico não terminaram em %.1fs", timeout)

    def _submit(self, job: Callable[[], None]) -> None:
        self._pending.put(job)

    def _run_pending(self) -> None:
        while True:
            job = self._pending.get()
            if job is None:
                return
            try:
                job()
            except Exception:
                logger.exception("Falha ao gravar o histórico")

    def _start_match(self) -> None:
        self._match_id = self._safely(lambda: self._matches.create(self._now()))
        self._live_squad = SquadStatus()
        self._live_numbers = MatchNumbers()
        self._saved_statuses = ()
        self._refresh_totals()

    def _save_live(self, squad: SquadStatus, numbers: MatchNumbers) -> None:
        self._live_squad = squad
        self._live_numbers = numbers
        match_id = self._match_id
        statuses = tuple(member.status.value for member in squad.members)
        if match_id is None or statuses == self._saved_statuses or not statuses:
            return
        self._saved_statuses = statuses
        self._safely(lambda: self._squad.save(match_id, statuses, self._now()))

    def _finish_match(self, result: MatchResult, duration_seconds: int) -> None:
        match_id = self._match_id
        self._match_id = None
        squad = self._live_squad
        numbers = self._live_numbers
        if match_id is None:
            return
        you = squad.self_member
        summary = MatchSummary(
            self._now(),
            duration_seconds,
            result.value,
            you.status.value if you is not None else None,
            squad.condition.value,
            numbers.kills,
            numbers.assists,
            numbers.chests_opened,
            numbers.players_left,
        )
        self._safely(lambda: self._matches.finish(match_id, summary))
        self._refresh_totals()
        self._version += 1

    def _mark_token(self, match_id: int) -> None:
        self._safely(lambda: self._matches.mark_token_obtained(match_id))
        self._version += 1

    def _refresh_totals(self) -> None:
        totals = self._safely(self._matches.totals)
        if totals is not None:
            self._totals = totals

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _safely(action: Callable[[], object]):
        # O banco pode estar travado por instantes, e a partida não deixa de ser acompanhada por isso.
        try:
            return action()
        except sqlite3.Error:
            logger.exception("Falha ao gravar no banco de dados")
            return None
