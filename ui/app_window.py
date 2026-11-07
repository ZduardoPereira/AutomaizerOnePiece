"""Janela principal: menu lateral, troca de páginas e ciclo de atualização da interface."""

from __future__ import annotations

import logging

import customtkinter as ctk

from assistant.notifier import WindowsNotifier
from core.application import Application
from core.event_manager import AppEvent, EventType
from ui import theme
from ui.assistant import AssistantPage
from ui.click_counter import ClickCounterPage
from ui.dashboard import DashboardPage
from ui.formatting import assistance_state_text, fortnite_status
from ui.guidance_overlay import GuidanceOverlay, NotificationCards
from ui.history import HistoryPage
from ui.monitoring import MonitoringPage
from ui.rewards import RewardsPage
from ui.settings import SettingsPage
from ui.whatsapp import WhatsAppPage
from ui.widgets import Page

logger = logging.getLogger(__name__)

_TICK_MS = 100
_PAGES: dict[str, type[Page]] = {
    "Dashboard": DashboardPage,
    "Monitoramento": MonitoringPage,
    "Assistente": AssistantPage,
    "Recompensas": RewardsPage,
    "Histórico": HistoryPage,
    "Cliques": ClickCounterPage,
    "WhatsApp": WhatsAppPage,
    "Configurações": SettingsPage,
}


class AppWindow(ctk.CTk):
    def __init__(self, application: Application) -> None:
        ctk.set_appearance_mode(application.config.ui.appearance_mode)
        super().__init__(fg_color=theme.BACKGROUND)
        self.application = application
        self.title("AutomaizerOnePiece")
        self.geometry("1180x800")
        self.minsize(960, 700)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._build_sidebar()

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=20, pady=18)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)
        self._pages = {name: page_class(content, application) for name, page_class in _PAGES.items()}
        self._current_page = next(iter(self._pages))
        self.show_page(self._current_page)

        self._overlay = GuidanceOverlay(self)
        self._cards = NotificationCards(self, application.config.notifications.card_seconds)
        self._notifier = WindowsNotifier(self.winfo_id(), "AutomaizerOnePiece")
        application.events.subscribe(self._on_event)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_job = self.after(_TICK_MS, self._tick)

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=theme.SIDEBAR)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(sidebar, text="AUTOMAIZER", text_color=theme.TEXT, font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=20, pady=(22, 0)
        )
        ctk.CTkLabel(sidebar, text="ONE PIECE", text_color=theme.GOLD, font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=1, column=0, sticky="w", padx=20, pady=(0, 20)
        )

        for row, name in enumerate(_PAGES, start=2):
            button = ctk.CTkButton(
                sidebar,
                text=name,
                anchor="w",
                height=38,
                fg_color="transparent",
                hover_color=theme.PANEL_HOVER,
                text_color=theme.TEXT,
                command=lambda page=name: self.show_page(page),
            )
            button.grid(row=row, column=0, sticky="ew", padx=12, pady=2)
            self._nav_buttons[name] = button

        sidebar.grid_rowconfigure(20, weight=1)
        assistance = ctk.CTkFrame(sidebar, fg_color=theme.PANEL, corner_radius=10)
        assistance.grid(row=21, column=0, sticky="ew", padx=12, pady=(0, 12))
        self._assistance = ctk.CTkSwitch(
            assistance, text="Assistência", text_color=theme.TEXT, progress_color=theme.GOLD,
            font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold"), command=self._toggle_assistance,
        )
        self._assistance.pack(anchor="w", padx=12, pady=(10, 2))
        self._assistance_state = ctk.CTkLabel(
            assistance, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=170, justify="left"
        )
        self._assistance_state.pack(anchor="w", padx=12, pady=(0, 8))
        self._status = ctk.CTkLabel(
            sidebar, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=190, justify="left"
        )
        self._status.grid(row=22, column=0, sticky="w", padx=20, pady=(0, 8))
        self._toggle = ctk.CTkButton(
            sidebar, text="", height=38, fg_color=theme.GOLD, hover_color=theme.GOLD_HOVER, text_color=theme.ON_GOLD,
            text_color_disabled=theme.ON_GOLD, command=self._toggle_monitoring,
        )
        self._toggle.grid(row=23, column=0, sticky="ew", padx=12, pady=(0, 8))
        ctk.CTkButton(
            sidebar, text="PARADA DE EMERGÊNCIA", height=44, fg_color=theme.RED, hover_color=theme.RED_HOVER,
            text_color="#FFFFFF", font=ctk.CTkFont(size=13, weight="bold"), command=self.application.emergency_stop,
        ).grid(row=24, column=0, sticky="ew", padx=12, pady=(0, 16))
        self._refresh_sidebar()

    def show_page(self, name: str) -> None:
        self._pages[self._current_page].grid_forget()
        self._nav_buttons[self._current_page].configure(fg_color="transparent", text_color=theme.TEXT)
        self._current_page = name
        self._pages[name].grid(row=0, column=0, sticky="nsew")
        self._nav_buttons[name].configure(fg_color=theme.PANEL_HOVER, text_color=theme.GOLD)
        self._pages[name].refresh()

    def _toggle_monitoring(self) -> None:
        if self.application.is_monitoring:
            self.application.stop_monitoring()
        else:
            self.application.start_monitoring()
        self._refresh_sidebar()

    def _toggle_assistance(self) -> None:
        self.application.set_assistant_enabled(bool(self._assistance.get()))
        self._refresh_sidebar()

    def _refresh_assistance(self) -> None:
        status = self.application.assistant_status()
        finished = self.application.monitoring_finished
        # O estado pode mudar por fora do botão: parada de emergência, mouse em um canto da tela ou token obtido.
        if bool(self._assistance.get()) != status.active:
            self._assistance.configure(state="normal")
            self._assistance.select() if status.active else self._assistance.deselect()
        switch_state = "disabled" if finished else "normal"
        if self._assistance.cget("state") != switch_state:
            self._assistance.configure(state=switch_state)
        text = assistance_state_text(status, self.application.is_monitoring, finished)
        if self._assistance_state.cget("text") != text:
            self._assistance_state.configure(text=text)

    def _refresh_sidebar(self) -> None:
        self._refresh_assistance()
        if self.application.monitoring_finished:
            label = "Monitoramento finalizado"
        else:
            label = "Parar monitoramento" if self.application.is_monitoring else "Iniciar monitoramento"
        if self._toggle.cget("text") != label:
            self._toggle.configure(text=label, state="disabled" if self.application.monitoring_finished else "normal")
        status = fortnite_status(self.application.snapshot(), self.application.recognition_snapshot().state)
        if self._status.cget("text") != status:
            self._status.configure(text=status)

    def _on_event(self, event: AppEvent) -> None:
        if event.type is EventType.CONFIG_UPDATED:
            ctk.set_appearance_mode(self.application.config.ui.appearance_mode)
            self._cards.seconds = self.application.config.notifications.card_seconds
        elif event.type in (EventType.ASSISTANT_NOTIFY, EventType.MONITOR_FINISHED):
            self._cards.show(event.message)
            self._notifier.notify(event.message)

    def _tick(self) -> None:
        try:
            self.application.process_shutdown()
            self.application.events.dispatch_pending()
            self._refresh_sidebar()
            self._overlay.show(self.application.assistant_status().hint)
            self._pages[self._current_page].refresh()
        except Exception:
            logger.exception("Falha ao atualizar a interface")
        finally:
            self._tick_job = self.after(_TICK_MS, self._tick)

    def _on_close(self) -> None:
        self.after_cancel(self._tick_job)
        self._cards.close()
        self._notifier.close()
        self.application.shutdown()
        self.destroy()

    def report_callback_exception(self, exc: type[BaseException], val: BaseException, tb: object) -> None:
        logger.error("Erro em callback da interface", exc_info=(exc, val, tb))
