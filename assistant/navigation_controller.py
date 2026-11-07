"""Liga a sequência de orientação à tela: localiza os botões, leva o cursor e dispara os avisos."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from assistant.guided_state_machine import (
    BUTTON_LABELS,
    CLICK_MESSAGES,
    ButtonMatch,
    ClickDone,
    GuidanceContext,
    GuidanceStep,
    GuidedStateMachine,
    MoveCursor,
    NavigationButton,
    Notify,
    PressEscape,
)
from assistant.mouse_guidance import CursorCornerStop, MouseGuidance
from assistant.notification_outbox import CLICK_DONE, CLICK_REQUIRED, INFO, NotificationOutbox
from assistant.user_confirmation import ConfirmationState
from core.capture import WindowInfo
from core.config import AssistantConfig
from core.event_manager import EventManager, EventType
from recognition.interface_detector import REFERENCE_HEIGHT, read_image

logger = logging.getLogger(__name__)

_TEMPLATE_FILES = {
    NavigationButton.MENU_TAB: "aba_menu.png",
    NavigationButton.LEAVE_MATCH: "voltar_ao_lobby.png",
    NavigationButton.PLAY: "jogar.png",
    NavigationButton.PLAY_AGAIN: "jogar_novamente.png",
    NavigationButton.CLAIM: "resgatar.png",
    NavigationButton.CLAIM_ALL: "resgatar_tudo.png",
}
# De que lado do botão o aviso aparece, sem cobrir o botão nem os recortes que identificam a tela.
_HINT_SIDES = {
    NavigationButton.MENU_TAB: "below",
    NavigationButton.LEAVE_MATCH: "left",
    NavigationButton.PLAY: "right",
    NavigationButton.PLAY_AGAIN: "left",
    # Largo e no rodapé: ao lado, o aviso cobriria o texto do botão; ao lado de Resgatar fica Equipar.
    NavigationButton.CLAIM: "above",
    NavigationButton.CLAIM_ALL: "above",
}


_CORNER_STOP_MESSAGE = "Mouse em um canto da tela: assistência desligada."


@dataclass(frozen=True)
class GuidanceHint:
    text: str
    x: int  # em pixels da tela
    y: int
    side: str


@dataclass(frozen=True)
class AssistantStatus:
    active: bool = False
    step: GuidanceStep = GuidanceStep.IDLE
    next_action: str = ""
    button: NavigationButton | None = None
    button_score: float = 0.0
    confirmation: ConfirmationState = ConfirmationState.NONE
    hint: GuidanceHint | None = None


class ButtonLocator:
    def __init__(self, templates_dir: Path, threshold: float, working_height: int) -> None:
        self._threshold = threshold
        self._working_height = working_height
        self.warnings: list[str] = []
        self._templates: dict[NavigationButton, np.ndarray] = {}
        for button, name in _TEMPLATE_FILES.items():
            image = read_image(templates_dir / "navigation" / name, cv2.IMREAD_GRAYSCALE)
            if image is None:
                self.warnings.append(f"Recorte do botão {BUTTON_LABELS[button]} não encontrado: {name}")
                continue
            factor = working_height / REFERENCE_HEIGHT
            self._templates[button] = cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)

    def update_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def locate(self, frame: np.ndarray, button: NavigationButton) -> ButtonMatch | None:
        """Retorna o botão se ele estiver na tela com confiança; sem isso, o mouse não é movido."""
        template = self._templates.get(button)
        if template is None:
            return None
        factor = self._working_height / frame.shape[0]
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
            return None
        result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        _, score, _, (x, y) = cv2.minMaxLoc(result)
        if score < self._threshold:
            return None
        return ButtonMatch(
            button,
            float(score),
            round((x + template.shape[1] / 2) / factor),
            round((y + template.shape[0] / 2) / factor),
        )


class NavigationController:
    def __init__(
        self,
        config: AssistantConfig,
        events: EventManager,
        locator: ButtonLocator,
        mouse: MouseGuidance,
        outbox: NotificationOutbox,
    ) -> None:
        self._config = config
        self._events = events
        self._locator = locator
        self._mouse = mouse
        self._outbox = outbox
        self._machine = GuidedStateMachine(config)
        self._lock = Lock()
        self._active = False
        self._window: WindowInfo | None = None

    def status(self) -> AssistantStatus:
        with self._lock:
            machine = self._machine
            button = machine.button
            return AssistantStatus(
                self._active,
                machine.step,
                machine.message,
                button.button if button is not None else None,
                button.score if button is not None else 0.0,
                machine.confirmation.state,
                self._hint(),
            )

    def update_config(self, config: AssistantConfig) -> None:
        with self._lock:
            self._config = config
            self._machine.update_config(config)
            self._locator.update_threshold(config.button_threshold)

    def activate(self) -> None:
        with self._lock:
            if self._active:
                return
            self._active = True
            self._machine.reset()
        self._events.publish(EventType.ASSISTANT_STATE_CHANGED, "Assistência ligada")

    def stop(self, reason: str = "Assistência desligada") -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._machine.reset()
        self._events.publish(EventType.ASSISTANT_STATE_CHANGED, reason)

    def forget_game(self) -> None:
        """O jogo foi fechado ou o monitoramento parou: o que estava em andamento não vale mais."""
        with self._lock:
            self._machine.reset()
            self._window = None

    def update(self, frame: np.ndarray, context: GuidanceContext, window: WindowInfo | None) -> None:
        with self._lock:
            if not self._active:
                return
            self._window = window
            commands = self._machine.update(context, lambda button: self._locator.locate(frame, button))
            duration = self._config.cursor_move_seconds

        # Fora da trava: o movimento do cursor é demorado, e a interface consulta o estado o tempo todo.
        for command in commands:
            if isinstance(command, Notify):
                self._notify(command)
            elif isinstance(command, MoveCursor):
                self._move_cursor(command.match, window, duration)
            elif isinstance(command, PressEscape):
                self._press_escape(window)
            elif isinstance(command, ClickDone):
                self._outbox.publish(CLICK_DONE, f"Clique feito: {BUTTON_LABELS[command.button]}", command.button.value)

    def _notify(self, command: Notify) -> None:
        self._events.publish(EventType.ASSISTANT_NOTIFY, command.message)
        # Toda notificação mostrada vai para o arquivo, inclusive o pedido de clique e os lembretes dele.
        if command.button is None:
            self._outbox.publish(INFO, command.message)
        else:
            self._outbox.publish(CLICK_REQUIRED, command.message, command.button.value)

    def _move_cursor(self, match: ButtonMatch, window: WindowInfo | None, duration: float) -> None:
        left, top = (window.left, window.top) if window is not None else (0, 0)
        try:
            self._mouse.move_to(left + match.center_x, top + match.center_y, duration)
        except CursorCornerStop:
            self.stop(_CORNER_STOP_MESSAGE)
            self._notify(Notify(_CORNER_STOP_MESSAGE))
        except Exception:
            logger.exception("Não foi possível mover o cursor")
            self._notify(Notify("Não consegui levar o mouse até o botão."))

    def _press_escape(self, window: WindowInfo | None) -> None:
        if window is None or not self._mouse.press_escape(window.hwnd):
            self._notify(Notify("Não consegui abrir o menu. Aperte Esc."))

    def _hint(self) -> GuidanceHint | None:
        machine = self._machine
        button = machine.button
        if not self._active or button is None or machine.step not in CLICK_MESSAGES:
            return None
        left, top = (self._window.left, self._window.top) if self._window is not None else (0, 0)
        return GuidanceHint(machine.message, left + button.center_x, top + button.center_y, _HINT_SIDES[button.button])
