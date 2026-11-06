"""Encerramento do monitoramento depois do token: a regra persiste mesmo se o aplicativo for fechado."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace

from core.event_manager import EventManager, EventType
from database.models import MonitorControl
from database.repositories import MonitorControlRepository

logger = logging.getLogger(__name__)

FINISHED_MESSAGE = "RECOMPENSA OBTIDA! Monitoramento finalizado."


class ShutdownManager:
    def __init__(self, repository: MonitorControlRepository, events: EventManager) -> None:
        self._repository = repository
        self._events = events
        self._control = repository.load()

    @property
    def control(self) -> MonitorControl:
        return self._control

    @property
    def can_monitor(self) -> bool:
        return not self._control.monitor_stopped

    def on_token_obtained(self) -> None:
        if not self._control.token_obtained:
            self._update(token_obtained=True)

    def on_match_over(self) -> None:
        """Com o token obtido, a partida atual era a última: nenhum novo ciclo deve começar."""
        if self._control.token_obtained and not self._control.monitor_stopped and not self._control.shutdown_pending:
            self._update(shutdown_pending=True)

    def complete_if_pending(self) -> bool:
        """Conclui o encerramento pendente, inclusive um interrompido por o aplicativo ter sido fechado."""
        if not self._control.shutdown_pending:
            return False
        self._update(shutdown_pending=False, monitor_stopped=True)
        self._events.publish(EventType.MONITOR_FINISHED, FINISHED_MESSAGE)
        return True

    def reset(self) -> None:
        self._control = MonitorControl()
        self._save()

    def _update(self, **changes: bool) -> None:
        self._control = replace(self._control, **changes)
        self._save()

    def _save(self) -> None:
        # Uma falha ao gravar não pode impedir o encerramento; a próxima gravação leva o estado completo.
        try:
            self._repository.save(self._control)
        except sqlite3.Error:
            logger.exception("Não foi possível gravar o controle do monitoramento")
