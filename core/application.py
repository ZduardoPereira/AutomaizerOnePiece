"""Orquestra configuração, eventos, captura e reconhecimento; é a única fachada usada pela interface."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from assistant.mouse_guidance import MouseGuidance
from assistant.navigation_controller import AssistantStatus, ButtonLocator, NavigationController
from assistant.notification_outbox import REWARD_OBTAINED, NotificationOutbox
from core.capture import CaptureService, CaptureSnapshot
from core.config import AppConfig, load_config, save_config
from core.event_manager import EventManager, EventType
from core.history_recorder import HistoryRecorder
from core.logging_setup import configure_logging
from core.paths import (
    CONFIG_PATH,
    DATABASE_PATH,
    FEEDBACK_DIR,
    LOGS_DIR,
    NOTIFICATIONS_PATH,
    REWARDS_DIR,
    TEMPLATES_DIR,
)
from core.recognition_service import Analysis, RecognitionService, RecognitionSnapshot
from core.shutdown_manager import FINISHED_MESSAGE, ShutdownManager
from core.state_machine import GameState
from database.database import Database
from database.repositories import MonitorControlRepository, RewardRepository
from recognition.feedback import FeedbackManager
from recognition.hud_reader import HudReader
from recognition.interface_detector import ScreenDetector
from recognition.reward_detector import RewardDetector, RewardState, RewardStatus, RewardTracker
from recognition.squad_detector import SquadDetector

logger = logging.getLogger(__name__)


class Application:
    def __init__(
        self,
        config_path: Path = CONFIG_PATH,
        logs_dir: Path = LOGS_DIR,
        templates_dir: Path = TEMPLATES_DIR,
        feedback_dir: Path = FEEDBACK_DIR,
        rewards_dir: Path = REWARDS_DIR,
        database_path: Path = DATABASE_PATH,
        notifications_path: Path = NOTIFICATIONS_PATH,
    ) -> None:
        self._config_path = config_path
        self._logs_dir = logs_dir
        self.config, config_warning = load_config(config_path)
        configure_logging(self.config.logging, logs_dir)

        self.events = EventManager()
        self.database = Database(database_path)
        if self.database.warning:
            self.events.publish(EventType.CONFIG_WARNING, self.database.warning)
        self.history = HistoryRecorder(self.database)
        self.events.add_sink(self.history.record_event)
        self.shutdown_manager = ShutdownManager(MonitorControlRepository(self.database), self.events)
        self.outbox = NotificationOutbox(self.config.notifications, notifications_path)
        if config_warning:
            self.events.publish(EventType.CONFIG_WARNING, config_warning)
        self.capture = CaptureService(self.config.capture, self.events)

        detector = ScreenDetector(self.config.recognition, templates_dir)
        for warning in detector.warnings:
            self.events.publish(EventType.RECOGNITION_WARNING, warning)
        squad_detector = SquadDetector(self.config.squad, templates_dir)
        for warning in squad_detector.warnings:
            self.events.publish(EventType.RECOGNITION_WARNING, warning)
        reward_detector = RewardDetector(self.config.rewards, templates_dir, self.config.recognition.working_height)
        for warning in reward_detector.warnings:
            self.events.publish(EventType.RECOGNITION_WARNING, warning)
        self.rewards = RewardTracker(
            self.config.rewards,
            self.events,
            rewards_dir,
            RewardRepository(self.database),
            lambda: self.history.current_match_id,
        )
        locator = ButtonLocator(templates_dir, self.config.assistant.button_threshold, self.config.recognition.working_height)
        for warning in locator.warnings:
            self.events.publish(EventType.RECOGNITION_WARNING, warning)
        self.assistant = NavigationController(self.config.assistant, self.events, locator, MouseGuidance(), self.outbox)
        hud_reader = HudReader(templates_dir)
        for warning in hud_reader.warnings:
            self.events.publish(EventType.RECOGNITION_WARNING, warning)
        self.feedback = FeedbackManager(feedback_dir, detector, self.config.recognition)
        self.recognition = RecognitionService(
            self.config.recognition,
            self.capture,
            self.events,
            detector,
            self.feedback,
            squad_detector,
            self.config.squad,
            reward_detector,
            self.rewards,
            self.assistant,
            hud_reader,
            self.history,
        )

        self._lock = threading.Lock()
        self._monitored_seconds = 0.0
        self._monitoring_since: float | None = None
        self._closed = False

    def start(self) -> None:
        """Ao abrir, a assistência volta como foi deixada."""
        if self.config.assistant.enabled:
            self.set_assistant_enabled(True)

    @property
    def is_monitoring(self) -> bool:
        return self.capture.is_running

    @property
    def monitored_seconds(self) -> float:
        # A thread de captura pode ter encerrado sozinha após uma falha fatal.
        if not self.is_monitoring:
            self._close_monitoring_period()
        with self._lock:
            running = time.monotonic() - self._monitoring_since if self._monitoring_since is not None else 0.0
            return self._monitored_seconds + running

    def snapshot(self) -> CaptureSnapshot:
        return self.capture.snapshot()

    def recognition_snapshot(self) -> RecognitionSnapshot:
        return self.recognition.snapshot()

    def reward_status(self) -> RewardStatus:
        return self.rewards.status

    @property
    def monitoring_finished(self) -> bool:
        return not self.shutdown_manager.can_monitor

    def process_shutdown(self) -> None:
        """Aplica a regra do token. A interface chama isto sempre, pois quem para as threads não pode ser uma delas."""
        status = self.rewards.status
        if status.token_obtained and not self.shutdown_manager.control.token_obtained:
            self.shutdown_manager.on_token_obtained()
            self.history.mark_token_obtained(status.match_id)
        if status.state is RewardState.STOPPED:
            self.shutdown_manager.on_match_over()
        if self.shutdown_manager.complete_if_pending():
            self.assistant.stop()
            self._stop_all()
            self.outbox.publish(REWARD_OBTAINED, FINISHED_MESSAGE)

    def confirm_reward_candidate(self) -> None:
        self.rewards.confirm_candidate(self.recognition_snapshot().matches.in_match)

    def dismiss_reward_candidate(self) -> None:
        self.rewards.dismiss_candidate()

    def reset_reward(self) -> None:
        self.rewards.reset()
        self.shutdown_manager.reset()

    def assistant_status(self) -> AssistantStatus:
        return self.assistant.status()

    def set_assistant_enabled(self, enabled: bool) -> None:
        """Liga ou desliga o mouse e as notificações. Ligar também inicia o monitoramento, sem o qual ela não vê o jogo."""
        if enabled and self.monitoring_finished:
            return
        if enabled:
            self.assistant.activate()
            self.start_monitoring()
        else:
            self.assistant.stop()
        self._remember_assistant_choice(enabled)

    def set_press_escape(self, enabled: bool) -> None:
        config = self.config.model_copy(deep=True)
        config.assistant.press_escape = enabled
        self.update_config(config)

    def start_monitoring(self) -> None:
        if self.is_monitoring or self.monitoring_finished:
            return
        self._close_monitoring_period()
        self.capture.start()
        self.recognition.start()
        with self._lock:
            self._monitoring_since = time.monotonic()
        self.events.publish(EventType.MONITORING_STARTED, "Monitoramento iniciado")

    def stop_monitoring(self) -> None:
        if self._stop_all():
            self.events.publish(EventType.MONITORING_STOPPED, "Monitoramento interrompido")

    def emergency_stop(self) -> None:
        """Interrompe imediatamente toda atividade, esteja ela em andamento ou não."""
        self.assistant.stop()
        self._stop_all()
        self.events.publish(EventType.EMERGENCY_STOP, "Parada de emergência acionada")
        # Por último: não poder gravar a escolha não pode atrapalhar a parada.
        self._remember_assistant_choice(False)

    def record_feedback(self, analysis: Analysis, label: GameState) -> str:
        """Registra a resposta do usuário sobre um quadro e retorna o resultado para exibição."""
        try:
            outcome = self.feedback.add(analysis.frame, analysis.frame_id, analysis.detection, label)
        except OSError as exc:
            logger.exception("Falha ao salvar o exemplo")
            return f"Não foi possível salvar o exemplo: {exc}"
        if outcome.saved:
            self.events.publish(EventType.FEEDBACK_RECORDED, outcome.message)
        return outcome.message

    def delete_feedback(self, sample_id: str) -> None:
        self.feedback.delete(sample_id)
        self.events.publish(EventType.FEEDBACK_RECORDED, "Exemplo removido; limites recalculados")

    def update_config(self, config: AppConfig) -> None:
        save_config(config, self._config_path)
        self.config = config
        configure_logging(config.logging, self._logs_dir)
        self.capture.update_config(config.capture)
        self.recognition.update_config(config.recognition, config.squad)
        self.assistant.update_config(config.assistant)
        self.outbox.update_config(config.notifications)
        self.events.publish(EventType.CONFIG_UPDATED, "Configurações salvas")

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.assistant.stop()
        self._stop_all()
        self.outbox.close()
        self.history.close()
        self.database.close()
        logger.info("Aplicativo encerrado")

    def _remember_assistant_choice(self, enabled: bool) -> None:
        if self.config.assistant.enabled is enabled:
            return
        config = self.config.model_copy(deep=True)
        config.assistant.enabled = enabled
        try:
            self.update_config(config)
        except OSError:
            logger.exception("Não foi possível guardar a escolha da assistência")

    def _stop_all(self) -> bool:
        was_running = self.is_monitoring
        self.recognition.stop()
        self.capture.stop()
        self._close_monitoring_period()
        return was_running

    def _close_monitoring_period(self) -> None:
        with self._lock:
            if self._monitoring_since is not None:
                self._monitored_seconds += time.monotonic() - self._monitoring_since
                self._monitoring_since = None
