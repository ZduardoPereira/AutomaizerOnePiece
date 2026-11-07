"""Barramento de eventos entre as threads de trabalho e a interface.

Qualquer thread pode publicar; os assinantes são chamados apenas em dispatch_pending(), que a
interface executa em sua própria thread, pois o Tkinter não é seguro entre threads.
"""

from __future__ import annotations

import logging
import queue
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from threading import Lock

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    MONITORING_STARTED = "MONITORING_STARTED"
    MONITORING_STOPPED = "MONITORING_STOPPED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    FORTNITE_DETECTED = "FORTNITE_DETECTED"
    FORTNITE_LOST = "FORTNITE_LOST"
    CAPTURE_STATE_CHANGED = "CAPTURE_STATE_CHANGED"
    CAPTURE_ERROR = "CAPTURE_ERROR"
    SCREEN_CHANGED = "SCREEN_CHANGED"
    RECOGNITION_ERROR = "RECOGNITION_ERROR"
    RECOGNITION_WARNING = "RECOGNITION_WARNING"
    MATCH_STARTED = "MATCH_STARTED"
    PLAYER_ELIMINATED = "PLAYER_ELIMINATED"
    PLAYER_RETURNED = "PLAYER_RETURNED"
    VICTORY_DETECTED = "VICTORY_DETECTED"
    MATCH_ENDED = "MATCH_ENDED"
    TEAMMATE_KNOCKED = "TEAMMATE_KNOCKED"
    TEAMMATE_RECOVERED = "TEAMMATE_RECOVERED"
    TEAMMATE_ELIMINATED = "TEAMMATE_ELIMINATED"
    TEAMMATE_RETURNED = "TEAMMATE_RETURNED"
    REBOOT_CARD_COLLECTED = "REBOOT_CARD_COLLECTED"
    SELF_KNOCKED = "SELF_KNOCKED"
    SQUAD_CONDITION_CHANGED = "SQUAD_CONDITION_CHANGED"
    REWARD_CANDIDATE = "REWARD_CANDIDATE"
    REWARD_REVIEW_NEEDED = "REWARD_REVIEW_NEEDED"
    REWARD_CONFIRMED = "REWARD_CONFIRMED"
    REWARD_WAITING_MATCH_END = "REWARD_WAITING_MATCH_END"
    REWARD_STOPPED = "REWARD_STOPPED"
    REWARD_RESET = "REWARD_RESET"
    MONITOR_FINISHED = "MONITOR_FINISHED"
    WHATSAPP_SENT = "WHATSAPP_SENT"
    WHATSAPP_FAILED = "WHATSAPP_FAILED"
    ASSISTANT_NOTIFY = "ASSISTANT_NOTIFY"
    ASSISTANT_STATE_CHANGED = "ASSISTANT_STATE_CHANGED"
    FEEDBACK_RECORDED = "FEEDBACK_RECORDED"
    CONFIG_UPDATED = "CONFIG_UPDATED"
    CONFIG_WARNING = "CONFIG_WARNING"


@dataclass(frozen=True)
class AppEvent:
    type: EventType
    message: str
    created_at: datetime = field(default_factory=datetime.now)
    confidence: float | None = None


Subscriber = Callable[[AppEvent], None]


class EventManager:
    def __init__(self, history_size: int = 200) -> None:
        self._pending: queue.SimpleQueue[AppEvent] = queue.SimpleQueue()
        self._history: deque[AppEvent] = deque(maxlen=history_size)
        self._lock = Lock()
        self._subscribers: list[Subscriber] = []
        self._sinks: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def add_sink(self, callback: Subscriber) -> None:
        """Registra quem recebe o evento na hora, na thread que o publicou, como o banco de dados."""
        with self._lock:
            self._sinks.append(callback)

    def publish(self, event_type: EventType, message: str, confidence: float | None = None) -> None:
        event = AppEvent(event_type, message, confidence=confidence)
        with self._lock:
            self._history.append(event)
            sinks = list(self._sinks)
        self._pending.put(event)
        logger.info("%s: %s", event_type.value, message)
        for sink in sinks:
            try:
                sink(event)
            except Exception:
                logger.exception("Falha ao entregar %s", event_type.value)

    def dispatch_pending(self) -> int:
        """Entrega os eventos pendentes e retorna quantos foram entregues."""
        with self._lock:
            subscribers = list(self._subscribers)
        delivered = 0
        while True:
            try:
                event = self._pending.get_nowait()
            except queue.Empty:
                return delivered
            delivered += 1
            for callback in subscribers:
                try:
                    callback(event)
                except Exception:
                    logger.exception("Falha em um assinante ao tratar %s", event.type.value)

    def recent(self, limit: int = 20, types: set[EventType] | None = None) -> list[AppEvent]:
        """Retorna os eventos mais recentes, do mais novo para o mais antigo."""
        with self._lock:
            events = list(self._history)
        if types is not None:
            events = [event for event in events if event.type in types]
        return events[::-1][:limit]
