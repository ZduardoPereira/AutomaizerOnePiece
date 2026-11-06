"""Acompanha o ciclo de cada partida a partir das mudanças de tela confirmadas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.event_manager import EventManager, EventType
from core.state_machine import GameState, Transition

_PLAYING = {GameState.IN_MATCH, GameState.ELIMINATED, GameState.VICTORY, GameState.DEFEAT}
# Procurar partida não encerra a partida atual: a tela só vem depois do resultado, e seu recorte
# "Cancelar" pode aparecer em caixas de diálogo durante o jogo.
_ENDING = {GameState.MATCH_RESULT, GameState.DEFEAT, GameState.LOBBY, GameState.LOADING}


class MatchResult(str, Enum):
    VICTORY = "VICTORY"
    DEFEAT = "DEFEAT"
    UNDETERMINED = "UNDETERMINED"


_RESULT_LABELS = {
    MatchResult.VICTORY: "vitória",
    MatchResult.DEFEAT: "derrota",
    MatchResult.UNDETERMINED: "resultado indeterminado",
}


@dataclass(frozen=True)
class MatchStats:
    identified: int = 0
    victories: int = 0
    defeats: int = 0
    in_match: bool = False


class MatchListener(Protocol):
    def match_started(self) -> None: ...

    def match_ended(self, result: MatchResult, duration_seconds: int) -> None: ...


class MatchTracker:
    def __init__(self, events: EventManager, listener: MatchListener | None = None) -> None:
        self._events = events
        self._listener = listener
        self._stats = MatchStats()
        self._started_at = 0.0
        self._victory = False
        self._eliminated = False

    @property
    def stats(self) -> MatchStats:
        return self._stats

    def observe(self, transition: Transition | None, victory_visible: bool, now: float) -> None:
        if transition is None:
            return
        current = transition.current

        if current in _PLAYING and not self._stats.in_match:
            self._start(now)

        if self._stats.in_match:
            if current is GameState.ELIMINATED:
                self._eliminated = True
                self._events.publish(EventType.PLAYER_ELIMINATED, "Jogador eliminado")
            elif current is GameState.IN_MATCH and self._eliminated:
                self._eliminated = False
                self._events.publish(EventType.PLAYER_RETURNED, "Jogador de volta à partida")
            elif current is GameState.VICTORY or (current is GameState.MATCH_RESULT and victory_visible):
                self._mark_victory()

            if current in _ENDING:
                reached_result = current in {GameState.MATCH_RESULT, GameState.DEFEAT}
                self._end(now, reached_result)

    def close(self, now: float) -> None:
        """Encerra a partida em aberto quando não há mais como acompanhá-la."""
        if self._stats.in_match:
            self._end(now, reached_result=False)

    def _start(self, now: float) -> None:
        self._started_at = now
        self._victory = False
        self._eliminated = False
        self._stats = MatchStats(self._stats.identified + 1, self._stats.victories, self._stats.defeats, True)
        # Primeiro o registro da partida, para o evento já ficar ligado a ela.
        if self._listener is not None:
            self._listener.match_started()
        self._events.publish(EventType.MATCH_STARTED, "Partida iniciada")

    def _mark_victory(self) -> None:
        if not self._victory:
            self._victory = True
            self._events.publish(EventType.VICTORY_DETECTED, "Vitória Royale detectada")

    def _end(self, now: float, reached_result: bool) -> None:
        if self._victory:
            result = MatchResult.VICTORY
        elif reached_result or self._eliminated:
            result = MatchResult.DEFEAT
        else:
            result = MatchResult.UNDETERMINED
        self._stats = MatchStats(
            self._stats.identified,
            self._stats.victories + (result is MatchResult.VICTORY),
            self._stats.defeats + (result is MatchResult.DEFEAT),
            False,
        )
        duration = max(0, int(now - self._started_at))
        minutes, seconds = divmod(duration, 60)
        self._events.publish(
            EventType.MATCH_ENDED, f"Partida encerrada: {_RESULT_LABELS[result]} ({minutes:02d}:{seconds:02d})"
        )
        if self._listener is not None:
            self._listener.match_ended(result, duration)
