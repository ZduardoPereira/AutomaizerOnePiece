"""Página de recompensas: token detectado, evidência, data e situação do encerramento."""

from __future__ import annotations

from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import cv2
from PIL import Image

from core.application import Application
from recognition.interface_detector import read_image
from ui import theme
from ui.formatting import fit_size, reward_closing_text, reward_found_text
from ui.widgets import Page, StatTile


class RewardsPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Recompensas", "Token de recompensa do evento")
        self.grid_rowconfigure(1, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # Tamanho fixado pelo grid: sem isso, a imagem exibida faria a área crescer.
        self._picture_area = ctk.CTkFrame(body, fg_color=theme.PREVIEW_BACKGROUND, corner_radius=10)
        self._picture_area.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._picture_area.grid_propagate(False)
        self._blank = ctk.CTkImage(Image.new("RGBA", (1, 1)), size=(1, 1))
        self._picture = ctk.CTkLabel(self._picture_area, text="", image=self._blank, text_color=theme.TEXT_MUTED)
        self._picture.place(relx=0.5, rely=0.5, anchor="center")

        side = ctk.CTkFrame(body, fg_color="transparent")
        side.grid(row=0, column=1, sticky="ns")
        side.grid_columnconfigure(0, minsize=300)
        self._found = StatTile(side, "Token detectado", value_wraplength=270)
        self._found.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._date = StatTile(side, "Data de obtenção", value_wraplength=270)
        self._date.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self._closing = StatTile(side, "Status de encerramento", value_wraplength=270)
        self._closing.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        self._review = ctk.CTkFrame(side, fg_color=theme.PANEL, corner_radius=10)
        self._review.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._review.grid_columnconfigure((0, 1), weight=1, uniform="buttons")
        self._review_text = ctk.CTkLabel(
            self._review, text="", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"),
            wraplength=270, justify="left",
        )
        self._review_text.grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 6))
        ctk.CTkButton(
            self._review, text="É o token", height=30, fg_color=theme.GREEN, hover_color=theme.GREEN_HOVER,
            text_color=theme.ON_GOLD, command=self._accept_candidate,
        ).grid(row=1, column=0, sticky="ew", padx=(12, 4), pady=(0, 10))
        ctk.CTkButton(
            self._review, text="Não é", height=30, fg_color=theme.RED, hover_color=theme.RED_HOVER,
            text_color="#FFFFFF", command=application.dismiss_reward_candidate,
        ).grid(row=1, column=1, sticky="ew", padx=(4, 12), pady=(0, 10))

        self._erase = ctk.CTkButton(
            side, text="Apagar registro do token", height=30, fg_color=theme.PANEL, hover_color=theme.PANEL_HOVER,
            text_color=theme.TEXT, command=self._confirm_reset,
        )
        self._erase.grid(row=4, column=0, sticky="ew")

        self._shown_key: tuple | None = None

    def refresh(self) -> None:
        status = self.application.reward_status()
        found, hint = reward_found_text(status)
        color = theme.GREEN if status.token_obtained else theme.AMBER if status.candidate_path else theme.TEXT
        # Nesta página, a pergunta sobre o quadro já aparece logo abaixo.
        self._found.set(found, hint if status.token_obtained else "", color)
        self._date.set(f"{status.detected_at:%d/%m/%Y %H:%M:%S}" if status.detected_at else "—")
        self._closing.set(reward_closing_text(status, self.application.monitoring_finished))

        if status.candidate_path is not None and not status.token_obtained:
            self._review_text.configure(text=f"Este quadro mostra o token? O app teve {status.candidate_confidence:.0%} de certeza.")
            self._review.grid()
        else:
            self._review.grid_remove()
        if status.token_obtained:
            self._erase.grid()
        else:
            self._erase.grid_remove()

        path = status.evidence_path if status.evidence_path is not None else status.candidate_path
        self._show_picture(path)

    def _show_picture(self, path: Path | None) -> None:
        area_width = self._picture_area.winfo_width()
        area_height = self._picture_area.winfo_height()
        key = (path, area_width, area_height)
        if key == self._shown_key or area_width <= 1 or area_height <= 1:
            return
        self._shown_key = key

        frame = read_image(path) if path is not None else None
        if frame is None:
            self._picture.configure(image=self._blank, text="Nenhuma evidência registrada.")
            return
        width, height = fit_size(frame.shape[1], frame.shape[0], area_width - 8, area_height - 8)
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        image = ctk.CTkImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)), size=(width / scaling, height / scaling))
        self._picture.configure(image=image, text="")

    def _accept_candidate(self) -> None:
        self.application.confirm_reward_candidate()

    def _confirm_reset(self) -> None:
        erase = messagebox.askyesno(
            "Apagar registro",
            "O registro e a imagem do token serão apagados, e o aplicativo voltará a procurá-lo. Continuar?",
            parent=self,
        )
        if erase:
            self.application.reset_reward()
