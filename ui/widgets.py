"""Componentes visuais compartilhados entre as páginas."""

from __future__ import annotations

from collections.abc import Sequence

import customtkinter as ctk

from core.application import Application
from core.event_manager import AppEvent
from ui import theme


class Page(ctk.CTkFrame):
    """Página do menu lateral. refresh() é chamado periodicamente enquanto ela está visível."""

    def __init__(self, master: ctk.CTkBaseClass, application: Application, title: str, subtitle: str) -> None:
        super().__init__(master, fg_color="transparent")
        self.application = application
        self.grid_columnconfigure(0, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header, text=title, text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_TITLE, weight="bold")
        ).pack(anchor="w")
        ctk.CTkLabel(header, text=subtitle, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_BODY)).pack(
            anchor="w"
        )

    def refresh(self) -> None:
        """Atualiza os dados exibidos; páginas sem dados dinâmicos não precisam sobrescrever."""


class StatTile(ctk.CTkFrame):
    def __init__(
        self, master: ctk.CTkBaseClass, label: str, value: str = "—", hint: str = "", value_wraplength: int = 150
    ) -> None:
        super().__init__(master, fg_color=theme.PANEL, corner_radius=10)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=label, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL)).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 0)
        )
        self._value = ctk.CTkLabel(
            self,
            text=value,
            text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.FONT_VALUE, weight="bold"),
            wraplength=value_wraplength,
            justify="left",
        )
        self._value.grid(row=1, column=0, sticky="w", padx=14)
        self._hint = ctk.CTkLabel(
            self, text=hint, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=value_wraplength, justify="left"
        )
        self._hint.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))
        self._shown = (value, hint, theme.TEXT)

    def set(self, value: str, hint: str = "", color: tuple[str, str] = theme.TEXT) -> None:
        # Reconfigurar um widget tem custo de redesenho; só o faz se algo mudou.
        if (value, hint, color) == self._shown:
            return
        self._shown = (value, hint, color)
        self._value.configure(text=value, text_color=color)
        self._hint.configure(text=hint)


class EventLog(ctk.CTkTextbox):
    """Lista de eventos somente leitura, do mais novo para o mais antigo."""

    def __init__(self, master: ctk.CTkBaseClass, empty_text: str, **kwargs: object) -> None:
        super().__init__(master, fg_color=theme.PANEL, text_color=theme.TEXT, wrap="word", state="disabled", **kwargs)
        self._empty_text = empty_text
        self._shown: list[AppEvent] | None = None

    def show(self, events: Sequence[AppEvent]) -> None:
        events = list(events)
        if events == self._shown:
            return
        self._shown = events
        lines = [f"{event.created_at:%H:%M:%S}  {event.message}" for event in events]
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", "\n".join(lines) or self._empty_text)
        self.configure(state="disabled")


class StagePanel(ctk.CTkFrame):
    """Informa o que a página exibirá quando a etapa correspondente for implementada."""

    def __init__(self, master: ctk.CTkBaseClass, stage: str, items: Sequence[str]) -> None:
        super().__init__(master, fg_color=theme.PANEL, corner_radius=10)
        ctk.CTkLabel(
            self, text=stage, text_color=theme.GOLD, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold")
        ).pack(anchor="w", padx=18, pady=(14, 6))
        for item in items:
            ctk.CTkLabel(
                self, text=f"•  {item}", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY), justify="left"
            ).pack(anchor="w", padx=18)
        ctk.CTkFrame(self, fg_color="transparent", height=12).pack()
