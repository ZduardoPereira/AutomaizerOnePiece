"""Estados do jogo e confirmação das mudanças de tela ao longo de capturas consecutivas."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GameState(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOBBY = "LOBBY"
    MATCHMAKING = "MATCHMAKING"
    LOADING = "LOADING"
    IN_MATCH = "IN_MATCH"
    ELIMINATED = "ELIMINATED"
    GAME_MENU = "GAME_MENU"
    VICTORY = "VICTORY"
    DEFEAT = "DEFEAT"
    MATCH_RESULT = "MATCH_RESULT"
    REWARD_CLAIM = "REWARD_CLAIM"


GAME_STATE_LABELS = {
    GameState.UNKNOWN: "Tela desconhecida",
    GameState.LOBBY: "Lobby",
    GameState.MATCHMAKING: "Procurando partida",
    GameState.LOADING: "Carregando",
    GameState.IN_MATCH: "Partida em andamento",
    GameState.ELIMINATED: "Jogador eliminado",
    GameState.GAME_MENU: "Menu do jogo",
    GameState.VICTORY: "Vitória",
    GameState.DEFEAT: "Derrota",
    GameState.MATCH_RESULT: "Resultado da partida",
    GameState.REWARD_CLAIM: "Recompensa para resgatar",
}


@dataclass(frozen=True)
class Transition:
    previous: GameState
    current: GameState
    confidence: float


class StateMachine:
    """Só aceita uma nova tela depois de vê-la em capturas consecutivas.

    Quadros não reconhecidos são comuns dentro de um estado conhecido, por exemplo com o mapa aberto
    durante a partida; por isso o estado só passa a UNKNOWN após um período de tolerância.
    """

    def __init__(self, confirm_frames: int, unknown_grace_seconds: float) -> None:
        self.confirm_frames = confirm_frames
        self.unknown_grace_seconds = unknown_grace_seconds
        self.state = GameState.UNKNOWN
        self.confidence = 0.0
        self._pending: GameState | None = None
        self._pending_count = 0
        self._state_seen_at = 0.0

    def observe(self, candidate: GameState, confidence: float, now: float) -> Transition | None:
        if candidate is self.state:
            self._clear_pending()
            self._state_seen_at = now
            self.confidence = confidence
            return None

        if candidate is GameState.UNKNOWN:
            self._clear_pending()
            if now - self._state_seen_at < self.unknown_grace_seconds:
                return None
            return self._enter(candidate, confidence, now)

        if candidate is self._pending:
            self._pending_count += 1
        else:
            self._pending = candidate
            self._pending_count = 1
        if self._pending_count < self.confirm_frames:
            return None
        return self._enter(candidate, confidence, now)

    def reset(self) -> Transition | None:
        """Volta ao estado inicial, por exemplo quando o jogo é fechado."""
        self._clear_pending()
        if self.state is GameState.UNKNOWN:
            return None
        return self._enter(GameState.UNKNOWN, 0.0, 0.0)

    def _enter(self, state: GameState, confidence: float, now: float) -> Transition:
        transition = Transition(self.state, state, confidence)
        self.state = state
        self.confidence = confidence
        self._state_seen_at = now
        self._clear_pending()
        return transition

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_count = 0
