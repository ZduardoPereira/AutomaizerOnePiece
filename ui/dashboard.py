"""Página inicial: visão geral do monitoramento e do esquadrão."""

from __future__ import annotations

import customtkinter as ctk

from core.application import Application
from core.capture import CaptureState
from core.state_machine import GameState
from recognition.survival_detector import SquadCondition
from ui import theme
from ui.formatting import (
    SQUAD_CONDITION_LABELS,
    alive_count_text,
    capture_hint,
    format_duration,
    fortnite_status,
    last_survivor_text,
    reward_found_text,
    squad_help_text,
)
from ui.squad_panel import SquadPanel
from ui.widgets import EventLog, Page, StatTile

_COLUMNS = 5
_STATE_COLORS = {
    CaptureState.ERROR: theme.RED,
    CaptureState.SEARCHING: theme.AMBER,
    CaptureState.MINIMIZED: theme.AMBER,
    CaptureState.BACKGROUND: theme.AMBER,
}
_CONDITION_COLORS = {
    SquadCondition.SQUAD_ACTIVE: theme.GREEN,
    SquadCondition.LAST_SQUAD_MEMBER_STANDING: theme.AMBER,
    SquadCondition.LAST_SQUAD_MEMBER_ALIVE: theme.AMBER,
    SquadCondition.SQUAD_ELIMINATED: theme.RED,
}
_CHESTS_HIDDEN_HINT = "Acompanhe a missão de baús no jogo"


class DashboardPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Dashboard", "Visão geral do monitoramento")
        self.grid_rowconfigure(2, weight=1)

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew")
        for column in range(_COLUMNS):
            grid.grid_columnconfigure(column, weight=1, uniform="tiles")

        self._state = StatTile(grid, "Estado do Fortnite")
        self._squad = StatTile(grid, "Estado do esquadrão")
        self._alive = StatTile(grid, "Integrantes vivos")
        self._last_survivor = StatTile(grid, "Último sobrevivente")
        self._time = StatTile(grid, "Tempo monitorado")
        self._matches = StatTile(grid, "Partidas identificadas", "0")
        self._victories = StatTile(grid, "Vitórias", "0")
        self._defeats = StatTile(grid, "Derrotas", "0")
        self._capture = StatTile(grid, "Captura")
        self._token = StatTile(grid, "Status do token")
        self._kills = StatTile(grid, "Abates")
        self._assists = StatTile(grid, "Assistências")
        self._players = StatTile(grid, "Jogadores restantes")
        self._chests = StatTile(grid, "Baús abertos")
        tiles = [
            self._state,
            self._squad,
            self._alive,
            self._last_survivor,
            self._token,
            self._time,
            self._matches,
            self._victories,
            self._defeats,
            self._capture,
            self._kills,
            self._assists,
            self._players,
            self._chests,
        ]
        for index, tile in enumerate(tiles):
            tile.grid(row=index // _COLUMNS, column=index % _COLUMNS, sticky="nsew", padx=4, pady=4)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, sticky="nsew", padx=4, pady=(10, 0))
        bottom.grid_columnconfigure(0, weight=5, uniform="bottom")
        bottom.grid_columnconfigure(1, weight=4, uniform="bottom")
        bottom.grid_rowconfigure(0, weight=1)

        self._squad_panel = SquadPanel(bottom)
        self._squad_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        events_frame = ctk.CTkFrame(bottom, fg_color="transparent")
        events_frame.grid(row=0, column=1, sticky="nsew")
        events_frame.grid_columnconfigure(0, weight=1)
        events_frame.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            events_frame, text="Últimos eventos", text_color=theme.TEXT, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._events = EventLog(events_frame, "Nenhum evento registrado.", height=120)
        self._events.grid(row=1, column=0, sticky="nsew")

    def refresh(self) -> None:
        capture = self.application.snapshot()
        recognition = self.application.recognition_snapshot()
        low_health = self.application.config.squad.low_health_percent

        color = _STATE_COLORS.get(capture.state, theme.TEXT)
        if capture.state is CaptureState.CAPTURING:
            color = theme.AMBER if recognition.state is GameState.UNKNOWN else theme.GREEN
        self._state.set(fortnite_status(capture, recognition.state), capture_hint(capture), color)

        squad = recognition.squad
        self._squad.set(
            SQUAD_CONDITION_LABELS[squad.condition],
            squad_help_text(squad, low_health),
            _CONDITION_COLORS.get(squad.condition, theme.TEXT),
        )
        self._alive.set(*alive_count_text(squad))
        self._last_survivor.set(*last_survivor_text(squad))
        self._squad_panel.show(recognition, low_health)

        reward = self.application.reward_status()
        token_color = theme.GREEN if reward.token_obtained else theme.AMBER if reward.candidate_path else theme.TEXT
        self._token.set(*reward_found_text(reward), token_color)

        self._time.set(format_duration(self.application.monitored_seconds))
        if capture.state is CaptureState.CAPTURING and capture.window is not None:
            self._capture.set(f"{capture.fps:.1f} FPS", f"{capture.window.width}x{capture.window.height}")
        else:
            self._capture.set("—")
        stats = recognition.matches
        totals = self.application.history.totals
        self._matches.set(str(totals.matches), f"Nesta sessão: {stats.identified}")
        self._victories.set(str(totals.victories), f"Nesta sessão: {stats.victories}")
        self._defeats.set(str(totals.defeats), f"Nesta sessão: {stats.defeats}")

        numbers = recognition.numbers
        self._kills.set(str(numbers.kills), f"Total: {totals.kills}")
        self._assists.set(str(numbers.assists), f"Total: {totals.assists}")
        self._players.set("—" if numbers.players_left is None else str(numbers.players_left))
        if numbers.chests_opened is None:
            self._chests.set("—", _CHESTS_HIDDEN_HINT)
        else:
            self._chests.set(str(numbers.chests_opened), f"Total: {totals.chests_opened}")
        self._events.show(self.application.events.recent(30))
