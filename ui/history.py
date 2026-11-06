"""Partidas anteriores: duração, resultado, números, integrantes do esquadrão e eventos identificados."""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

from core.application import Application
from core.event_manager import EventType
from database.models import MatchRecord
from ui import theme
from ui.formatting import MEMBER_STATUS_BY_VALUE, format_duration, match_numbers_text, match_result_text
from ui.widgets import Page

_RESULT_COLORS = {"VICTORY": theme.GREEN, "DEFEAT": theme.RED}
_MAX_MATCHES = 100
_HIDDEN_EVENTS = {EventType.SCREEN_CHANGED.value}  # ficam no banco, mas encheriam a lista de cada partida


class HistoryPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Histórico", "Partidas anteriores")
        self.grid_rowconfigure(1, weight=1)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, uniform="history")
        body.grid_columnconfigure(1, weight=2, uniform="history")
        body.grid_rowconfigure(0, weight=1)

        self._list = ctk.CTkScrollableFrame(body, fg_color=theme.PANEL, corner_radius=10)
        self._list.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._list.grid_columnconfigure(0, weight=1)

        details = ctk.CTkFrame(body, fg_color=theme.PANEL, corner_radius=10)
        details.grid(row=0, column=1, sticky="nsew")
        details.grid_columnconfigure(0, weight=1)
        details.grid_rowconfigure(2, weight=1)
        self._title = ctk.CTkLabel(
            details, text="Escolha uma partida", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"), anchor="w"
        )
        self._title.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
        self._squad = ctk.CTkLabel(
            details, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), justify="left", anchor="w", wraplength=320
        )
        self._squad.grid(row=1, column=0, sticky="ew", padx=14)
        self._events = ctk.CTkTextbox(details, fg_color="transparent", text_color=theme.TEXT, wrap="word", state="disabled")
        self._events.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))

        self._shown_version: int | None = None

    def refresh(self) -> None:
        version = self.application.history.version
        if version != self._shown_version:
            self._shown_version = version
            self._reload()

    def _reload(self) -> None:
        for row in self._list.winfo_children():
            row.destroy()
        matches = self.application.history.recent_matches(_MAX_MATCHES)
        if not matches:
            ctk.CTkLabel(self._list, text="Nenhuma partida registrada ainda.", text_color=theme.TEXT_MUTED).grid(
                row=0, column=0, sticky="w", padx=8, pady=8
            )
            return
        for index, match in enumerate(matches):
            self._add_row(index, match)

    def _add_row(self, index: int, match: MatchRecord) -> None:
        started = datetime.fromisoformat(match.started_at)
        duration = format_duration(match.duration_seconds) if match.duration_seconds is not None else "—"
        row = ctk.CTkButton(
            self._list,
            text=f"{started:%d/%m %H:%M}   {duration}   {match_result_text(match.result):<14}   {match_numbers_text(match)}",
            anchor="w",
            height=32,
            fg_color="transparent",
            hover_color=theme.PANEL_HOVER,
            text_color=_RESULT_COLORS.get(match.result, theme.TEXT),
            command=lambda: self._show_details(match),
        )
        row.grid(row=index, column=0, sticky="ew", padx=2, pady=1)

    def _show_details(self, match: MatchRecord) -> None:
        started = datetime.fromisoformat(match.started_at)
        token = "  •  Token obtido" if match.token_obtained else ""
        self._title.configure(text=f"{started:%d/%m/%Y %H:%M}  •  {match_result_text(match.result)}{token}")

        members = self.application.history.squad_of(match.id)
        lines = []
        for member in members:
            who = "Você" if member.player_number == 1 else f"Colega {member.player_number - 1}"
            lines.append(f"{who}: {MEMBER_STATUS_BY_VALUE.get(member.status, member.status)}")
        self._squad.configure(text="\n".join(lines) or "Sem dados do esquadrão.")

        events = [event for event in self.application.history.events_of(match.id) if event.event_type not in _HIDDEN_EVENTS]
        text = "\n".join(f"{datetime.fromisoformat(event.created_at):%H:%M:%S}  {event.description}" for event in events)
        self._events.configure(state="normal")
        self._events.delete("1.0", "end")
        self._events.insert("1.0", text or "Nenhum evento registrado.")
        self._events.configure(state="disabled")
