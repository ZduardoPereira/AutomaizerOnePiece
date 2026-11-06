"""Edição das configurações, com validação pelo modelo Pydantic antes de salvar."""

from __future__ import annotations

import logging

import customtkinter as ctk
from pydantic import ValidationError

from core.application import Application
from core.config import AppConfig
from ui import theme
from ui.widgets import Page

logger = logging.getLogger(__name__)


def _split_list(text: str) -> list[str]:
    return text.split(",")


def _number(text: str) -> str:
    # O Pydantic converte o texto; aqui apenas se aceita a vírgula decimal.
    return text.strip().replace(",", ".")


def format_validation_error(error: ValidationError) -> str:
    return "\n".join(f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors())


class SettingsPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "Configurações", "Alterações valem assim que forem salvas")
        self.grid_rowconfigure(1, weight=1)

        self._form = ctk.CTkScrollableFrame(self, fg_color=theme.PANEL, corner_radius=10)
        self._form.grid(row=1, column=0, sticky="nsew")
        self._form.grid_columnconfigure(1, weight=1)
        self._row = 0

        self._section("Captura")
        self._fps = self._entry("Quadros por segundo (até 30)")
        self._poll = self._entry("Intervalo de busca da janela (s)")
        self._black = self._entry("Limite de quadro preto (0 a 255)")
        self._processes = self._entry("Processos do jogo (separados por vírgula)")
        self._titles = self._entry("Títulos da janela (separados por vírgula)")
        self._foreground = ctk.CTkSwitch(self._form, text="Capturar somente com o jogo em primeiro plano", text_color=theme.TEXT)
        self._place_full_width(self._foreground)

        self._section("Reconhecimento")
        self._recognition_fps = self._entry("Análises por segundo (até 15)")
        self._confirm_frames = self._entry("Capturas seguidas para confirmar uma tela")
        self._unknown_grace = self._entry("Tolerância a telas desconhecidas (s)")

        self._section("Esquadrão")
        self._squad_confirm = self._entry("Capturas seguidas para confirmar uma mudança")
        self._low_health = self._entry("Vida baixa de um colega (%)")

        self._section("Assistente")
        self._escape_delay = self._entry("Espera antes de apertar Esc (s)")
        self._reminder = self._entry("Lembrar do clique a cada (s)")

        self._section("Notificações")
        self._card_seconds = self._entry("Tempo do cartão na tela (s)")
        self._notification_file = ctk.CTkSwitch(self._form, text="Gravar em arquivo, para outros programas lerem", text_color=theme.TEXT)
        self._place_full_width(self._notification_file)
        file_path = ctk.CTkLabel(
            self._form, text=str(application.outbox.path), text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=820, justify="left"
        )
        self._place_full_width(file_path)
        self._webhook_url = self._entry("Webhook: endereço para enviar as notificações")
        self._webhook_secret = self._entry("Webhook: senha enviada no cabeçalho")

        self._section("Interface")
        self._appearance = self._option("Tema", ["dark", "light", "system"])
        self._preview_fps = self._entry("Atualizações por segundo da visualização (até 15)")

        self._section("Logs")
        self._log_level = self._option("Nível de detalhe", ["DEBUG", "INFO", "WARNING", "ERROR"])

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        actions.grid_columnconfigure(2, weight=1)
        ctk.CTkButton(
            actions, text="Salvar", fg_color=theme.GOLD, hover_color=theme.GOLD_HOVER, text_color=theme.ON_GOLD, command=self._save
        ).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(
            actions, text="Restaurar padrões", fg_color=theme.PANEL, hover_color=theme.PANEL_HOVER, text_color=theme.TEXT,
            command=lambda: self._load_form(AppConfig()),
        ).grid(row=0, column=1, padx=(0, 12))
        self._status = ctk.CTkLabel(
            actions, text="", justify="left", anchor="w", wraplength=560, font=ctk.CTkFont(size=theme.FONT_BODY)
        )
        self._status.grid(row=0, column=2, sticky="ew")

        self._load_form(application.config)

    def _section(self, title: str) -> None:
        ctk.CTkLabel(
            self._form, text=title, text_color=theme.GOLD, font=ctk.CTkFont(size=theme.FONT_BODY, weight="bold")
        ).grid(row=self._row, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 4))
        self._row += 1

    def _label(self, text: str) -> None:
        ctk.CTkLabel(self._form, text=text, text_color=theme.TEXT).grid(row=self._row, column=0, sticky="w", padx=14, pady=4)

    def _entry(self, label: str) -> ctk.CTkEntry:
        self._label(label)
        entry = ctk.CTkEntry(self._form, text_color=theme.TEXT)
        entry.grid(row=self._row, column=1, sticky="ew", padx=14, pady=4)
        self._row += 1
        return entry

    def _option(self, label: str, values: list[str]) -> ctk.CTkOptionMenu:
        self._label(label)
        menu = ctk.CTkOptionMenu(
            self._form, values=values, fg_color=theme.PANEL_HOVER, button_color=theme.GOLD,
            button_hover_color=theme.GOLD_HOVER, text_color=theme.TEXT,
        )
        menu.grid(row=self._row, column=1, sticky="w", padx=14, pady=4)
        self._row += 1
        return menu

    def _place_full_width(self, widget: ctk.CTkBaseClass) -> None:
        widget.grid(row=self._row, column=0, columnspan=2, sticky="w", padx=14, pady=4)
        self._row += 1

    def _load_form(self, config: AppConfig) -> None:
        values = {
            self._fps: config.capture.fps,
            self._poll: config.capture.window_poll_seconds,
            self._black: config.capture.black_frame_threshold,
            self._processes: ", ".join(config.capture.process_names),
            self._titles: ", ".join(config.capture.window_titles),
            self._recognition_fps: config.recognition.fps,
            self._confirm_frames: config.recognition.confirm_frames,
            self._unknown_grace: config.recognition.unknown_grace_seconds,
            self._squad_confirm: config.squad.confirm_frames,
            self._low_health: config.squad.low_health_percent,
            self._escape_delay: config.assistant.escape_delay_seconds,
            self._reminder: config.assistant.reminder_seconds,
            self._card_seconds: config.notifications.card_seconds,
            self._webhook_url: config.notifications.webhook_url,
            self._webhook_secret: config.notifications.webhook_secret,
            self._preview_fps: config.ui.preview_fps,
        }
        for entry, value in values.items():
            entry.delete(0, "end")
            entry.insert(0, str(value))
        if config.capture.capture_only_when_foreground:
            self._foreground.select()
        else:
            self._foreground.deselect()
        if config.notifications.file_enabled:
            self._notification_file.select()
        else:
            self._notification_file.deselect()
        self._appearance.set(config.ui.appearance_mode)
        self._log_level.set(config.logging.level)
        self._show_status("", theme.TEXT)

    def _show_status(self, text: str, color: tuple[str, str]) -> None:
        self._status.configure(text=text, text_color=color)

    def _save(self) -> None:
        current = self.application.config
        try:
            config = AppConfig.model_validate(
                {
                    "capture": {
                        "fps": _number(self._fps.get()),
                        "window_poll_seconds": _number(self._poll.get()),
                        "capture_only_when_foreground": bool(self._foreground.get()),
                        "black_frame_threshold": _number(self._black.get()),
                        "process_names": _split_list(self._processes.get()),
                        "window_titles": _split_list(self._titles.get()),
                    },
                    "recognition": {
                        **current.recognition.model_dump(mode="json"),
                        "fps": _number(self._recognition_fps.get()),
                        "confirm_frames": _number(self._confirm_frames.get()),
                        "unknown_grace_seconds": _number(self._unknown_grace.get()),
                    },
                    "squad": {
                        **current.squad.model_dump(mode="json"),
                        "confirm_frames": _number(self._squad_confirm.get()),
                        "low_health_percent": _number(self._low_health.get()),
                    },
                    "rewards": current.rewards.model_dump(mode="json"),
                    "assistant": {
                        **current.assistant.model_dump(mode="json"),
                        "escape_delay_seconds": _number(self._escape_delay.get()),
                        "reminder_seconds": _number(self._reminder.get()),
                    },
                    "notifications": {
                        "file_enabled": bool(self._notification_file.get()),
                        "card_seconds": _number(self._card_seconds.get()),
                        "webhook_url": self._webhook_url.get(),
                        "webhook_secret": self._webhook_secret.get(),
                    },
                    "ui": {"appearance_mode": self._appearance.get(), "preview_fps": _number(self._preview_fps.get())},
                    "logging": {
                        "level": self._log_level.get(),
                        "max_bytes": current.logging.max_bytes,
                        "backup_count": current.logging.backup_count,
                    },
                }
            )
        except ValidationError as error:
            self._show_status(format_validation_error(error), theme.RED)
            return

        try:
            self.application.update_config(config)
        except OSError as error:
            logger.exception("Falha ao salvar as configurações")
            self._show_status(f"Não foi possível salvar: {error}", theme.RED)
            return
        self._show_status("Configurações salvas.", theme.GREEN)
