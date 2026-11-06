"""Visualização do que está sendo analisado, confiança, transições e respostas Certo/Errado."""

from __future__ import annotations

import time

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

from core.application import Application
from core.capture import CaptureSnapshot
from core.event_manager import EventType
from core.recognition_service import Analysis, RecognitionSnapshot
from core.state_machine import GAME_STATE_LABELS, GameState
from recognition.interface_detector import Detection
from recognition.squad_detector import MemberStatus
from recognition.survival_detector import SquadStatus
from ui import theme
from ui.feedback_review import FeedbackReviewWindow
from ui.formatting import capture_hint, fit_size, fortnite_status
from ui.widgets import EventLog, Page, StatTile

_TRANSITION_EVENTS = {
    EventType.SCREEN_CHANGED,
    EventType.FORTNITE_DETECTED,
    EventType.FORTNITE_LOST,
    EventType.TEAMMATE_KNOCKED,
    EventType.TEAMMATE_RECOVERED,
    EventType.TEAMMATE_ELIMINATED,
    EventType.TEAMMATE_RETURNED,
    EventType.SQUAD_CONDITION_CHANGED,
    EventType.REWARD_CONFIRMED,
    EventType.ASSISTANT_NOTIFY,
}
_STATE_BY_LABEL = {label: state for state, label in GAME_STATE_LABELS.items()}
_MATCH_BOX_COLOR = (50, 182, 242)  # dourado do tema, em BGR
_MEMBER_BOX_COLORS = {
    MemberStatus.ALIVE: (176, 194, 43),
    MemberStatus.KNOCKED: (58, 145, 240),
    MemberStatus.ELIMINATED: (47, 67, 216),
    MemberStatus.UNKNOWN: (184, 165, 143),
}


def _draw_matches(image: np.ndarray, detection: Detection, ratio: float) -> None:
    """Marca sobre a visualização os recortes que levaram a este reconhecimento."""
    result = detection.screens.get(detection.state)
    if result is None:
        return
    for match in result.matches:
        if match.score >= result.threshold:
            top_left = (round(match.left * ratio), round(match.top * ratio))
            bottom_right = (round((match.left + match.width) * ratio), round((match.top + match.height) * ratio))
            cv2.rectangle(image, top_left, bottom_right, _MATCH_BOX_COLOR, 2)


def _draw_squad(image: np.ndarray, squad: SquadStatus, ratio: float) -> None:
    """Marca as linhas do esquadrão com a cor do estado lido, para conferir a leitura com o jogo."""
    for member in squad.members:
        if member.box is not None:
            left, top, width, height = (round(value * ratio) for value in member.box)
            cv2.rectangle(image, (left, top), (left + width, top + height), _MEMBER_BOX_COLORS[member.status], 1)


class _FeedbackPanel(ctk.CTkFrame):
    """Recebe a resposta do usuário sobre o quadro exibido, e não sobre o mais recente: é o que ele vê."""

    _WRONG_PROMPT = "Errado, era..."

    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, fg_color=theme.PANEL, corner_radius=10)
        self._application = application
        self._analysis: Analysis | None = None
        self._review_window: FeedbackReviewWindow | None = None
        self.grid_columnconfigure((0, 1), weight=1, uniform="buttons")

        ctk.CTkLabel(
            self, text="Este quadro está certo?", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL)
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
        self._verdict = ctk.CTkLabel(
            self, text="—", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold")
        )
        self._verdict.grid(row=1, column=0, columnspan=2, sticky="w", padx=12)

        self._right = ctk.CTkButton(
            self, text="Certo", height=30, fg_color=theme.GREEN, hover_color=theme.GREEN_HOVER,
            text_color=theme.ON_GOLD, state="disabled", command=self._mark_right,
        )
        self._right.grid(row=2, column=0, sticky="ew", padx=(12, 4), pady=(4, 4))
        self._wrong = ctk.CTkOptionMenu(
            self, values=list(GAME_STATE_LABELS.values()), height=30, fg_color=theme.RED, button_color=theme.RED,
            button_hover_color=theme.RED_HOVER, text_color="#FFFFFF", dynamic_resizing=False, state="disabled",
            command=self._mark_wrong,
        )
        self._wrong.set(self._WRONG_PROMPT)
        self._wrong.grid(row=2, column=1, sticky="ew", padx=(4, 12), pady=(4, 4))

        self._status = ctk.CTkLabel(
            self, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=270, justify="left"
        )
        self._status.grid(row=3, column=0, columnspan=2, sticky="w", padx=12)
        self._review = ctk.CTkButton(
            self, text="", height=26, fg_color="transparent", hover_color=theme.PANEL_HOVER, text_color=theme.GOLD,
            command=self._open_review,
        )
        self._review.grid(row=4, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
        self._shown_count = -1
        self.refresh()

    def show(self, analysis: Analysis | None) -> None:
        if analysis is self._analysis:
            return
        had_analysis = self._analysis is not None
        self._analysis = analysis
        if analysis is None:
            self._verdict.configure(text="—")
            self._right.configure(state="disabled")
            self._wrong.configure(state="disabled")
            return
        detection = analysis.detection
        self._verdict.configure(text=f"{GAME_STATE_LABELS[detection.state]} ({detection.confidence:.0%})")
        if not had_analysis:
            self._right.configure(state="normal")
            self._wrong.configure(state="normal")

    def refresh(self) -> None:
        count = self._application.feedback.count
        if count != self._shown_count:
            self._shown_count = count
            self._review.configure(text=f"Revisar exemplos salvos ({count})")

    def _mark_right(self) -> None:
        if self._analysis is not None:
            self._record(self._analysis.detection.state)

    def _mark_wrong(self, value: str) -> None:
        self._wrong.set(self._WRONG_PROMPT)
        self._record(_STATE_BY_LABEL[value])

    def _record(self, label: GameState) -> None:
        if self._analysis is not None:
            self._status.configure(text=self._application.record_feedback(self._analysis, label))
            self.refresh()

    def _open_review(self) -> None:
        if self._review_window is not None and self._review_window.winfo_exists():
            self._review_window.lift()
            self._review_window.focus()
            return
        self._review_window = FeedbackReviewWindow(self.winfo_toplevel(), self._application)


class MonitoringPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Monitoramento", "O que o aplicativo está vendo e reconhecendo agora")
        self.grid_rowconfigure(1, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Tamanho fixado pelo grid: sem isso, a imagem exibida faria a área crescer a cada quadro.
        self._preview_area = ctk.CTkFrame(body, fg_color=theme.PREVIEW_BACKGROUND, corner_radius=10)
        self._preview_area.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._preview_area.grid_propagate(False)
        # Um widget com imagem não pode voltar a image=None no CTkLabel; usa-se um pixel transparente.
        self._blank = ctk.CTkImage(Image.new("RGBA", (1, 1)), size=(1, 1))
        self._preview = ctk.CTkLabel(self._preview_area, text="", image=self._blank, text_color=theme.TEXT_MUTED)
        self._preview.place(relx=0.5, rely=0.5, anchor="center")

        side = ctk.CTkFrame(body, fg_color="transparent", width=300)
        side.grid(row=0, column=1, sticky="ns")
        side.grid_columnconfigure((0, 1), weight=1, uniform="tiles")
        side.grid_rowconfigure(3, weight=1)
        self._state = StatTile(side, "Estado reconhecido", value_wraplength=270)
        self._state.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._confidence = StatTile(side, "Confiança")
        self._confidence.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(0, 8))
        self._frame_info = StatTile(side, "Quadro")
        self._frame_info.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=(0, 8))
        self._feedback = _FeedbackPanel(side, application)
        self._feedback.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self._transitions = EventLog(side, "Nenhuma transição registrada.", width=300, height=80)
        self._transitions.grid(row=3, column=0, columnspan=2, sticky="nsew")

        self._hint = ctk.CTkLabel(
            self, text="", text_color=theme.AMBER, font=ctk.CTkFont(size=theme.FONT_BODY), anchor="w"
        )
        self._hint.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self._shown_frame_id: int | None = None
        self._next_preview_at = 0.0

    def refresh(self) -> None:
        capture = self.application.snapshot()
        recognition = self.application.recognition_snapshot()
        self._state.set(fortnite_status(capture, recognition.state))
        self._update_details(capture, recognition)
        self._feedback.refresh()
        self._transitions.show(self.application.events.recent(40, _TRANSITION_EVENTS))
        hint = capture_hint(capture)
        if hint != self._hint.cget("text"):
            self._hint.configure(text=hint)
        self._update_preview(capture, recognition)

    def _update_details(self, capture: CaptureSnapshot, recognition: RecognitionSnapshot) -> None:
        analysis = recognition.analysis
        if analysis is None:
            self._confidence.set("—")
            self._frame_info.set("—")
            return
        detection = analysis.detection
        result = detection.screens.get(detection.state)
        limit = f"limite {result.threshold:.0%}" if result is not None else "abaixo dos limites"
        self._confidence.set(f"{detection.confidence:.0%}", limit)
        height, width = analysis.frame.shape[:2]
        self._frame_info.set(f"{width}x{height}", f"análise {analysis.duration_ms:.0f} ms")

    def _update_preview(self, capture: CaptureSnapshot, recognition: RecognitionSnapshot) -> None:
        analysis = recognition.analysis
        frame = analysis.frame if analysis is not None else capture.frame
        if frame is None:
            status = fortnite_status(capture, recognition.state)
            if self._shown_frame_id is not None or self._preview.cget("text") != status:
                self._shown_frame_id = None
                self._preview.configure(image=self._blank, text=status)
            self._feedback.show(None)
            return

        frame_id = analysis.frame_id if analysis is not None else capture.frame_id
        now = time.monotonic()
        if frame_id == self._shown_frame_id or now < self._next_preview_at:
            return
        self._next_preview_at = now + 1.0 / self.application.config.ui.preview_fps

        area_width = self._preview_area.winfo_width()
        area_height = self._preview_area.winfo_height()
        if area_width <= 1 or area_height <= 1:
            return

        frame_height, frame_width = frame.shape[:2]
        width, height = fit_size(frame_width, frame_height, area_width - 8, area_height - 8)
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        if analysis is not None:
            _draw_matches(resized, analysis.detection, width / frame_width)
            _draw_squad(resized, recognition.squad, width / frame_width)
        image = Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB))
        # winfo_* fornece pixels físicos, enquanto o CTkImage espera unidades lógicas.
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        self._preview.configure(image=ctk.CTkImage(image, size=(width / scaling, height / scaling)), text="")
        self._shown_frame_id = frame_id
        self._feedback.show(analysis)
