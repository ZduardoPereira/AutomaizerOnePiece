"""Página do Guided Assistant: próxima ação, botão identificado e opção do Esc."""

from __future__ import annotations

import customtkinter as ctk

from assistant.user_confirmation import ConfirmationState
from core.application import Application
from ui import theme
from ui.formatting import assistant_action_text, assistant_button_text, assistant_confirmation_text
from ui.widgets import Page, StatTile

_EXPLANATION = (
    "Com a chave Assistência ligada, na lateral, o app guia a próxima partida. No lobby, ele leva o cursor até Jogar. "
    "Se você abrir o menu durante a partida, ele leva o cursor até a aba de configurações e depois até Voltar ao Lobby. "
    "Quando só sobrar você, ele avisa e abre o mesmo caminho. No fim da partida, ele leva o cursor até Jogar novamente. "
    "Em cada passo, você recebe uma notificação com som e um aviso ao lado do botão.\n\n"
    "O clique é sempre seu. O cursor só se move com um menu na tela, nunca durante o jogo. "
    "Para desligar sem usar o app, leve o mouse a um canto da tela."
)
_FINISHED_TEXT = "Token obtido: monitoramento finalizado"
_ESCAPE_NOTE = (
    "Esta é a única tecla que o app envia ao jogo, uma vez por partida. O Fortnite não permite automação, então pode "
    "haver risco para a conta, e a tecla pode ser ignorada pelo jogo. Com a opção desligada, o app pede que você abra o menu."
)


class AssistantPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Assistente", "Orientação de navegação pelos menus do jogo")

        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew")
        tiles.grid_columnconfigure(0, weight=2, uniform="tiles")
        tiles.grid_columnconfigure((1, 2), weight=1, uniform="tiles")
        self._action = StatTile(tiles, "Próxima ação recomendada", value_wraplength=420)
        self._action.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._button = StatTile(tiles, "Botão identificado", value_wraplength=190)
        self._button.grid(row=0, column=1, sticky="nsew", padx=(0, 8))
        self._confirmation = StatTile(tiles, "Confirmação manual", value_wraplength=190)
        self._confirmation.grid(row=0, column=2, sticky="nsew")

        escape_panel = ctk.CTkFrame(self, fg_color=theme.PANEL, corner_radius=10)
        escape_panel.grid(row=2, column=0, sticky="ew", pady=12)
        self._escape = ctk.CTkSwitch(
            escape_panel, text="Apertar Esc por mim quando eu for o último vivo", text_color=theme.TEXT,
            font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"), command=self._toggle_escape,
        )
        self._escape.pack(anchor="w", padx=14, pady=(12, 4))
        if application.config.assistant.press_escape:
            self._escape.select()
        ctk.CTkLabel(
            escape_panel, text=_ESCAPE_NOTE, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL),
            wraplength=860, justify="left",
        ).pack(anchor="w", padx=14, pady=(0, 12))

        ctk.CTkLabel(
            self, text=_EXPLANATION, text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_BODY),
            wraplength=880, justify="left",
        ).grid(row=3, column=0, sticky="w")

    def refresh(self) -> None:
        status = self.application.assistant_status()
        finished = self.application.monitoring_finished
        color = theme.GOLD if status.active else theme.TEXT_MUTED
        self._action.set(_FINISHED_TEXT if finished else assistant_action_text(status), "", color)
        self._button.set(*assistant_button_text(status))
        waiting = status.confirmation is ConfirmationState.WAITING
        self._confirmation.set(assistant_confirmation_text(status), "", theme.AMBER if waiting else theme.TEXT)

    def _toggle_escape(self) -> None:
        self.application.set_press_escape(bool(self._escape.get()))
