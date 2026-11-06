"""Painel com cada integrante do esquadrão, usando o recorte do próprio jogo para mostrar quem é quem."""

from __future__ import annotations

import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image

from core.recognition_service import RecognitionSnapshot
from recognition.squad_detector import MemberStatus
from recognition.survival_detector import SquadMember
from ui import theme
from ui.formatting import fit_size, member_details_text, member_status_text

_MAX_MEMBERS = 4
_THUMBNAIL_SIZE = (204, 34)
_STATUS_COLORS = {
    MemberStatus.ALIVE: theme.GREEN,
    MemberStatus.KNOCKED: theme.AMBER,
    MemberStatus.ELIMINATED: theme.RED,
    MemberStatus.UNKNOWN: theme.TEXT_MUTED,
}


def _thumbnail(frame: np.ndarray, box: tuple[int, int, int, int]) -> ctk.CTkImage | None:
    left, top, width, height = box
    crop = frame[max(0, top) : top + height, max(0, left) : left + width]
    if crop.size == 0:
        return None
    fitted = fit_size(crop.shape[1], crop.shape[0], *_THUMBNAIL_SIZE)
    resized = cv2.resize(crop, fitted, interpolation=cv2.INTER_AREA)
    return ctk.CTkImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)), size=fitted)


class _MemberRow:
    def __init__(self, master: ctk.CTkBaseClass, row: int, blank: ctk.CTkImage) -> None:
        self._blank = blank
        # Um widget com imagem não pode voltar a image=None no CTkLabel; usa-se um pixel transparente.
        self._picture = ctk.CTkLabel(
            master, text="", image=blank, width=_THUMBNAIL_SIZE[0], text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"), anchor="w",
        )
        self._picture.grid(row=row, column=0, sticky="w", padx=(12, 8), pady=2)
        self._status = ctk.CTkLabel(
            master, text="", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"), anchor="w"
        )
        self._status.grid(row=row, column=1, sticky="w")
        self._details = ctk.CTkLabel(
            master, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), anchor="w"
        )
        self._details.grid(row=row, column=2, sticky="w", padx=(8, 12))

    def clear(self) -> None:
        self._picture.configure(image=self._blank, text="")
        self._status.configure(text="")
        self._details.configure(text="")

    def show(self, member: SquadMember, frame: np.ndarray | None, low_health_percent: int) -> None:
        image = None
        if not member.is_self and member.picture_box is not None and frame is not None:
            image = _thumbnail(frame, member.picture_box)
        if image is not None:
            self._picture.configure(image=image, text="")
        else:
            self._picture.configure(image=self._blank, text="Você" if member.is_self else "Colega")

        color = theme.TEXT_MUTED if member.left_match else _STATUS_COLORS[member.status]
        if member.needs_help(low_health_percent):
            color = theme.AMBER
        self._status.configure(text=member_status_text(member), text_color=color)
        self._details.configure(text=member_details_text(member, low_health_percent))


class SquadPanel(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkBaseClass) -> None:
        super().__init__(master, fg_color=theme.PANEL, corner_radius=10)
        self.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(
            self, text="Esquadrão", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 2))
        self._placeholder = ctk.CTkLabel(
            self, text="Aguardando uma partida.", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_BODY)
        )
        self._placeholder.grid(row=1, column=0, columnspan=3, sticky="w", padx=12)
        blank = ctk.CTkImage(Image.new("RGBA", (1, 1)), size=(1, 1))
        self._rows = [_MemberRow(self, index + 2, blank) for index in range(_MAX_MEMBERS)]
        self._shown: tuple | None = None

    def show(self, recognition: RecognitionSnapshot, low_health_percent: int) -> None:
        analysis = recognition.analysis
        key = (recognition.squad, analysis.frame_id if analysis is not None else None, low_health_percent)
        if key == self._shown:
            return
        self._shown = key

        members = recognition.squad.members[:_MAX_MEMBERS]
        if members:
            self._placeholder.grid_remove()
        else:
            self._placeholder.grid()
        frame = analysis.frame if analysis is not None else None
        for index, row in enumerate(self._rows):
            if index < len(members):
                row.show(members[index], frame, low_health_percent)
            else:
                row.clear()
