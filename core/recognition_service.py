"""Analisa os quadros capturados em uma thread própria, sem atrasar a captura nem a interface."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from assistant.guided_state_machine import GuidanceContext
from assistant.navigation_controller import NavigationController
from core.capture import CaptureService, CaptureSnapshot, CaptureState
from core.config import RecognitionConfig, SquadConfig
from core.event_manager import EventManager, EventType
from core.history_recorder import HistoryRecorder
from core.state_machine import GAME_STATE_LABELS, GameState, StateMachine, Transition
from recognition.feedback import FeedbackManager
from recognition.hud_reader import HudReader, HudTracker, MatchNumbers
from recognition.interface_detector import Detection, ScreenDetector
from recognition.match_detector import MatchStats, MatchTracker
from recognition.reward_detector import RewardDetector, RewardTracker
from recognition.squad_detector import SquadDetector
from recognition.survival_detector import SquadStatus, SurvivalTracker

logger = logging.getLogger(__name__)

_SQUAD_VISIBLE_ON = {GameState.IN_MATCH, GameState.ELIMINATED}
_OUTSIDE_MATCH = {GameState.LOBBY, GameState.MATCHMAKING, GameState.LOADING}
_MATCH_OVER = _OUTSIDE_MATCH | {GameState.MATCH_RESULT, GameState.DEFEAT}


@dataclass(frozen=True)
class Analysis:
    frame: np.ndarray
    frame_id: int
    detection: Detection
    analyzed_at: datetime
    duration_ms: float


@dataclass(frozen=True)
class RecognitionSnapshot:
    state: GameState = GameState.UNKNOWN
    analysis: Analysis | None = None
    matches: MatchStats = MatchStats()
    squad: SquadStatus = SquadStatus()
    numbers: MatchNumbers = MatchNumbers()


class RecognitionService:
    def __init__(
        self,
        config: RecognitionConfig,
        capture: CaptureService,
        events: EventManager,
        detector: ScreenDetector,
        feedback: FeedbackManager,
        squad_detector: SquadDetector,
        squad_config: SquadConfig,
        reward_detector: RewardDetector,
        rewards: RewardTracker,
        assistant: NavigationController,
        hud_reader: HudReader,
        history: HistoryRecorder,
    ) -> None:
        self._config = config
        self._capture = capture
        self._events = events
        self._detector = detector
        self._feedback = feedback
        self._squad_detector = squad_detector
        self._reward_detector = reward_detector
        self._rewards = rewards
        self._assistant = assistant
        self._hud_reader = hud_reader
        self._history = history
        self._hud = HudTracker()
        self._machine = StateMachine(config.confirm_frames, config.unknown_grace_seconds)
        self._matches = MatchTracker(events, history)
        self._survival = SurvivalTracker(squad_config, events)
        self._lock = threading.Lock()
        self._snapshot = RecognitionSnapshot()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> RecognitionSnapshot:
        with self._lock:
            return self._snapshot

    def update_config(self, config: RecognitionConfig, squad_config: SquadConfig) -> None:
        self._config = config
        self._machine.confirm_frames = config.confirm_frames
        self._machine.unknown_grace_seconds = config.unknown_grace_seconds
        self._survival.update_config(squad_config)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="recognition", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.error("A thread de reconhecimento não encerrou em %.1fs", timeout)
        self._thread = None

    def _run(self) -> None:
        try:
            self._rescore_feedback()
            self._loop()
        except Exception as exc:
            logger.exception("Falha fatal na thread de reconhecimento")
            self._events.publish(EventType.RECOGNITION_ERROR, f"Falha fatal no reconhecimento: {exc}")
        finally:
            self._forget_game()
            self._rewards.interrupt()

    def _rescore_feedback(self) -> None:
        rescored = self._feedback.rescore_outdated()
        if rescored:
            self._events.publish(EventType.RECOGNITION_WARNING, f"{rescored} exemplo(s) reavaliados com os recortes atuais")

    def _loop(self) -> None:
        last_frame_id: int | None = None
        last_error: str | None = None
        while not self._stop_event.is_set():
            started = time.monotonic()
            capture = self._capture.snapshot()
            if capture.state is CaptureState.SEARCHING:
                self._forget_game()
                self._rewards.on_match_over()
            elif capture.frame is not None and not capture.is_black and capture.frame_id != last_frame_id:
                last_frame_id = capture.frame_id
                try:
                    self._analyze(capture)
                    last_error = None
                except Exception as exc:
                    message = str(exc)
                    if message != last_error:
                        logger.exception("Falha ao analisar um quadro")
                        self._events.publish(EventType.RECOGNITION_ERROR, f"Falha ao analisar: {message}")
                    last_error = message
            self._stop_event.wait(max(0.02, 1.0 / self._config.fps - (time.monotonic() - started)))

    def _analyze(self, capture: CaptureSnapshot) -> None:
        started = time.monotonic()
        detection = self._detector.detect(capture.frame, self._machine.state)
        transition = self._machine.observe(detection.state, detection.confidence, started)
        self._publish_transition(transition)
        if transition is not None and transition.current is GameState.DEFEAT and self._matches.stats.in_match:
            # Antes de encerrar a partida, para o histórico guardar o esquadrão como eliminado.
            self._survival.mark_defeat()
            self._history.update_live(self._survival.status, self._hud.numbers)
        matches_before = self._matches.stats.identified
        self._matches.observe(transition, detection.is_visible(GameState.VICTORY), started)
        self._update_squad(capture.frame, detection, transition, matches_before)
        self._update_reward(capture.frame)
        self._update_assistant(capture, detection, started)
        duration_ms = (time.monotonic() - started) * 1000
        analysis = Analysis(capture.frame, capture.frame_id, detection, datetime.now(), duration_ms)
        with self._lock:
            self._snapshot = RecognitionSnapshot(
                self._machine.state, analysis, self._matches.stats, self._survival.status, self._hud.numbers
            )

    def _update_squad(
        self, frame: np.ndarray, detection: Detection, transition: Transition | None, matches_before: int
    ) -> None:
        current = transition.current if transition is not None else None
        new_match = self._matches.stats.identified != matches_before
        if new_match:
            self._hud.reset()
        if new_match or current in _OUTSIDE_MATCH:
            self._survival.reset()
        if current is GameState.DEFEAT:
            self._survival.mark_defeat()
        # O menu do jogo escurece o quadro e muda as cores das barras; o último estado confirmado é mantido.
        if detection.state in _SQUAD_VISIBLE_ON and not detection.is_visible(GameState.GAME_MENU):
            self._survival.observe(self._squad_detector.read(frame), detection.state)
            self._hud.observe(self._hud_reader.read(frame, detection))
            self._history.update_live(self._survival.status, self._hud.numbers)

    def _update_reward(self, frame: np.ndarray) -> None:
        state = self._machine.state
        if state in _MATCH_OVER:
            self._rewards.on_match_over()
        # Assistindo a um colega, o aviso seria da recompensa dele; por isso só vale durante a própria partida.
        elif state is GameState.IN_MATCH and self._rewards.is_searching:
            self._rewards.observe(frame, self._reward_detector.inspect(frame))

    def _update_assistant(self, capture: CaptureSnapshot, detection: Detection, now: float) -> None:
        window = capture.window
        context = GuidanceContext(
            state=self._machine.state,
            frame_state=detection.state,
            menu_open=detection.is_visible(GameState.GAME_MENU),
            condition=self._survival.status.condition,
            in_match=self._matches.stats.in_match,
            match_number=self._matches.stats.identified,
            token_obtained=self._rewards.status.token_obtained,
            foreground=window is not None and window.is_foreground,
            now=now,
        )
        self._assistant.update(capture.frame, context, window)

    def _forget_game(self) -> None:
        """Descarta o que se sabe sobre a tela atual; as estatísticas da sessão são mantidas."""
        self._publish_transition(self._machine.reset())
        self._matches.close(time.monotonic())
        self._survival.reset()
        self._assistant.forget_game()
        with self._lock:
            self._snapshot = replace(
                self._snapshot,
                state=self._machine.state,
                analysis=None,
                matches=self._matches.stats,
                squad=self._survival.status,
                numbers=self._hud.numbers,
            )

    def _publish_transition(self, transition: Transition | None) -> None:
        if transition is None:
            return
        message = f"{GAME_STATE_LABELS[transition.previous]} → {GAME_STATE_LABELS[transition.current]}"
        if transition.current is GameState.UNKNOWN:
            self._events.publish(EventType.SCREEN_CHANGED, message)
        else:
            self._events.publish(
                EventType.SCREEN_CHANGED, f"{message} ({transition.confidence:.0%})", transition.confidence
            )
