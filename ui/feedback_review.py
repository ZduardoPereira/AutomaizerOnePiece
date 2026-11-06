"""Janela para revisar e apagar os exemplos salvos pelas respostas Certo e Errado."""

from __future__ import annotations

import customtkinter as ctk
import cv2
from PIL import Image

from core.application import Application
from core.state_machine import GAME_STATE_LABELS, GameState
from recognition.feedback import FeedbackSample
from ui import theme
from ui.formatting import fit_size

_ALL_LABELS = "Todas as telas"
_STATE_BY_LABEL = {label: state for state, label in GAME_STATE_LABELS.items()}
_MAX_ROWS = 50
_THUMBNAIL_SIZE = (192, 108)


class FeedbackReviewWindow(ctk.CTkToplevel):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, fg_color=theme.BACKGROUND)
        self._application = application
        self.title("Exemplos salvos")
        self.geometry("760x560")
        self.minsize(560, 360)
        self.transient(master)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        header.grid_columnconfigure(1, weight=1)
        self._filter = ctk.CTkOptionMenu(
            header,
            values=[_ALL_LABELS, *GAME_STATE_LABELS.values()],
            fg_color=theme.PANEL_HOVER,
            button_color=theme.GOLD,
            button_hover_color=theme.GOLD_HOVER,
            text_color=theme.TEXT,
            command=lambda _value: self._reload(),
        )
        self._filter.grid(row=0, column=0, sticky="w")
        self._summary = ctk.CTkLabel(header, text="", text_color=theme.TEXT_MUTED, anchor="e")
        self._summary.grid(row=0, column=1, sticky="ew")

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self._list.grid_columnconfigure(0, weight=1)
        self._reload()
        # O CTkToplevel termina de se configurar depois de aberto e pode ficar atrás da janela principal.
        self.after(200, self.lift)

    def _reload(self) -> None:
        for row in self._list.winfo_children():
            row.destroy()
        selected = self._filter.get()
        samples = self._application.feedback.samples(None if selected == _ALL_LABELS else _STATE_BY_LABEL[selected])
        for index, sample in enumerate(samples[:_MAX_ROWS]):
            self._add_row(index, sample)
        self._summary.configure(text=self._summary_text(len(samples)))

    @staticmethod
    def _summary_text(total: int) -> str:
        if total > _MAX_ROWS:
            return f"Mostrando os {_MAX_ROWS} mais recentes de {total}"
        return f"{total} exemplo(s)"

    def _add_row(self, index: int, sample: FeedbackSample) -> None:
        row = ctk.CTkFrame(self._list, fg_color=theme.PANEL, corner_radius=10)
        row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
        row.grid_columnconfigure(1, weight=1)

        preview = ctk.CTkLabel(row, text="Imagem indisponível", text_color=theme.TEXT_MUTED, width=_THUMBNAIL_SIZE[0])
        thumbnail = self._thumbnail(sample.id)
        if thumbnail is not None:
            preview.configure(image=thumbnail, text="")
        preview.grid(row=0, column=0, padx=10, pady=10)

        predicted = GAME_STATE_LABELS[sample.predicted]
        details = (
            f"Marcado como: {GAME_STATE_LABELS[sample.label]}\n"
            f"O app disse: {predicted} ({sample.confidence:.0%})\n"
            f"{sample.created_at:%d/%m/%Y %H:%M:%S}"
        )
        ctk.CTkLabel(row, text=details, text_color=theme.TEXT, justify="left", anchor="w").grid(
            row=0, column=1, sticky="ew", padx=4
        )
        ctk.CTkButton(
            row,
            text="Apagar",
            width=80,
            fg_color=theme.RED,
            hover_color=theme.RED_HOVER,
            text_color="#FFFFFF",
            command=lambda: self._delete(sample, row),
        ).grid(row=0, column=2, padx=12)

    def _thumbnail(self, sample_id: str) -> ctk.CTkImage | None:
        frame = self._application.feedback.load_frame(sample_id)
        if frame is None:
            return None
        width, height = fit_size(frame.shape[1], frame.shape[0], *_THUMBNAIL_SIZE)
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        return ctk.CTkImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)), size=(width, height))

    def _delete(self, sample: FeedbackSample, row: ctk.CTkFrame) -> None:
        self._application.delete_feedback(sample.id)
        row.destroy()
        selected = self._filter.get()
        label = None if selected == _ALL_LABELS else _STATE_BY_LABEL[selected]
        self._summary.configure(text=self._summary_text(len(self._application.feedback.samples(label))))
