"""Página do contador de cliques, integrado do Projeto click no mouse."""

from __future__ import annotations

from tkinter import messagebox

import customtkinter as ctk

from click_counter.counter import CountedClick
from core.application import Application
from ui import theme
from ui.widgets import Page, StatTile

_EXPLANATION = (
    "Soma 1 a cada clique que o assistente pediu e você deu, como Jogar, Resgatar ou Voltar ao Lobby. Pedidos de clique, "
    "lembretes e avisos não contam. A contagem lê o arquivo data/notifications.jsonl, continua a do Projeto click no mouse "
    "e roda com o app aberto, monitorando ou não. Zerar não faz nenhum clique antigo ser contado de novo."
)
_EMPTY_TEXT = "Os cliques contados desde que o app abriu aparecem aqui."


class ClickCounterPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Cliques", "Contador de cliques feitos, integrado do Projeto click no mouse")
        self.grid_rowconfigure(2, weight=1)

        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew")
        tiles.grid_columnconfigure((0, 1), weight=1, uniform="tiles")
        self._total = StatTile(tiles, "Cliques contados", value_wraplength=400)
        self._total.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._last = StatTile(tiles, "Último clique", value_wraplength=400)
        self._last.grid(row=0, column=1, sticky="nsew")

        self._recent = ctk.CTkTextbox(self, fg_color=theme.PANEL, text_color=theme.TEXT, wrap="word", state="disabled")
        self._recent.grid(row=2, column=0, sticky="nsew", pady=12)
        self._shown_recent: tuple[CountedClick, ...] | None = None

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.grid_columnconfigure(1, weight=1)
        self._reset = ctk.CTkButton(
            bottom, text="Zerar contagem", height=30, fg_color=theme.PANEL, hover_color=theme.PANEL_HOVER,
            text_color=theme.TEXT, command=self._confirm_reset,
        )
        self._reset.grid(row=0, column=0, sticky="nw", padx=(0, 14))
        ctk.CTkLabel(
            bottom, text=_EXPLANATION, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL),
            wraplength=680, justify="left",
        ).grid(row=0, column=1, sticky="w")

    def refresh(self) -> None:
        count = self.application.click_count()
        if not count.ready:
            self._total.set("—", "Lendo a contagem salva...")
        elif not self.application.config.notifications.file_enabled:
            self._total.set(
                str(count.total), "A gravação em arquivo está desligada em Configurações: nada novo é contado.", theme.AMBER
            )
        else:
            self._total.set(str(count.total), "", theme.GOLD)

        if count.recent:
            last = count.recent[0]
            self._last.set(last.message, f"{last.created_at:%d/%m/%Y %H:%M:%S}")
        else:
            self._last.set("—", "Nenhum clique desde que o app abriu")
        self._show_recent(count.recent)

        state = "normal" if count.ready else "disabled"
        if self._reset.cget("state") != state:
            self._reset.configure(state=state)

    def _show_recent(self, recent: tuple[CountedClick, ...]) -> None:
        if recent == self._shown_recent:
            return
        self._shown_recent = recent
        lines = [f"{click.created_at:%H:%M:%S}  {click.message}" for click in recent]
        self._recent.configure(state="normal")
        self._recent.delete("1.0", "end")
        self._recent.insert("1.0", "\n".join(lines) or _EMPTY_TEXT)
        self._recent.configure(state="disabled")

    def _confirm_reset(self) -> None:
        reset = messagebox.askyesno(
            "Zerar contagem",
            "O total de cliques volta a 0. Os cliques já contados não serão contados de novo. Continuar?",
            parent=self,
        )
        if reset:
            self.application.reset_click_count()
