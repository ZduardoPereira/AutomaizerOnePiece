"""Página do aviso no WhatsApp: o serviço, o número, um teste e o resultado do último envio."""

from __future__ import annotations

import logging
import webbrowser

import customtkinter as ctk
from pydantic import ValidationError

from core.application import Application
from ui import theme
from ui.widgets import Page, StatTile
from whatsapp.notifier import CALLMEBOT, META, DeliveryState, WhatsAppSettings

logger = logging.getLogger(__name__)

_SERVICES = {"CallMeBot (grátis)": CALLMEBOT, "API oficial do WhatsApp (Meta)": META}
_SERVICE_NAMES = {service: name for name, service in _SERVICES.items()}
_SERVICE_PAGES = {
    CALLMEBOT: "https://www.callmebot.com/blog/free-api-whatsapp-messages/",
    META: "https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started",
}
_HELP = {
    CALLMEBOT: (
        "Para pegar a chave, uma vez só: abra a página do CallMeBot, salve nos contatos o número do bot que aparece lá e "
        "mande para ele, do seu WhatsApp: I allow callmebot to send me messages. A resposta traz a chave (APIKEY). Ele só "
        "manda mensagens para o número que pediu a chave. Se a página disser que o bot está cheio, tente de novo em alguns "
        "dias ou use a API oficial."
    ),
    META: (
        "Crie um app com o WhatsApp em developers.facebook.com. Na Configuração da API ficam a identificação do número de "
        "teste e um token que vale 24 horas, e é lá que o seu número entra no campo Para. O hello_world chega em inglês; "
        "o README explica como criar um modelo em português e um token que não expira."
    ),
}


def _error_text(error: ValidationError) -> str:
    messages = (item["msg"].removeprefix("Value error, ") for item in error.errors())
    return "\n".join(message[:1].upper() + message[1:] for message in messages)


class WhatsAppPage(Page):
    def __init__(self, master: ctk.CTkBaseClass, application: Application) -> None:
        super().__init__(master, application, "WhatsApp", "Mensagem no seu WhatsApp quando o token for encontrado")

        self._form = ctk.CTkFrame(self, fg_color=theme.PANEL, corner_radius=10)
        self._form.grid(row=1, column=0, sticky="ew")
        self._form.grid_columnconfigure(1, weight=1)
        self._row = 0
        self._service_widgets: dict[str, list[ctk.CTkBaseClass]] = {CALLMEBOT: [], META: []}

        self._enabled = ctk.CTkSwitch(
            self._form, text="Avisar no WhatsApp quando o token for encontrado", text_color=theme.TEXT,
            progress_color=theme.GOLD,
        )
        self._enabled.grid(row=self._row, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 4))
        self._row += 1
        ctk.CTkLabel(self._form, text="Serviço", text_color=theme.TEXT).grid(row=self._row, column=0, sticky="w", padx=14, pady=4)
        self._service = ctk.CTkOptionMenu(
            self._form, values=list(_SERVICES), fg_color=theme.PANEL_HOVER, button_color=theme.GOLD,
            button_hover_color=theme.GOLD_HOVER, text_color=theme.TEXT, command=lambda _name: self._show_service(),
        )
        self._service.grid(row=self._row, column=1, sticky="w", padx=14, pady=4)
        self._row += 1
        self._phone = self._entry("Seu número, com + e o código do país", placeholder="+55 11 91234-5678")
        self._api_key = self._entry("Chave do CallMeBot (APIKEY)", CALLMEBOT, placeholder="123456")
        self._token = self._entry("Token de acesso", META, secret=True)
        self._phone_number_id = self._entry("Identificação do número de telefone", META)
        self._template = self._entry("Nome do modelo", META)
        self._language = self._entry("Idioma do modelo", META)
        self._help = ctk.CTkLabel(
            self._form, text="", text_color=theme.TEXT_MUTED, font=ctk.CTkFont(size=theme.FONT_SMALL), wraplength=820,
            justify="left",
        )
        self._help.grid(row=self._row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 14))

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", pady=12)
        actions.grid_columnconfigure(3, weight=1)
        ctk.CTkButton(
            actions, text="Salvar", fg_color=theme.GOLD, hover_color=theme.GOLD_HOVER, text_color=theme.ON_GOLD, command=self._save
        ).grid(row=0, column=0, padx=(0, 8))
        self._test = ctk.CTkButton(
            actions, text="Enviar teste", fg_color=theme.PANEL, hover_color=theme.PANEL_HOVER, text_color=theme.TEXT,
            command=self._send_test,
        )
        self._test.grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            actions, text="Abrir página do serviço", fg_color=theme.PANEL, hover_color=theme.PANEL_HOVER, text_color=theme.TEXT,
            command=lambda: webbrowser.open(_SERVICE_PAGES[_SERVICES[self._service.get()]]),
        ).grid(row=0, column=2, padx=(0, 12))
        self._status = ctk.CTkLabel(
            actions, text="", justify="left", anchor="w", wraplength=480, font=ctk.CTkFont(size=theme.FONT_BODY)
        )
        self._status.grid(row=0, column=3, sticky="ew")

        tiles = ctk.CTkFrame(self, fg_color="transparent")
        tiles.grid(row=3, column=0, sticky="ew")
        tiles.grid_columnconfigure((0, 1), weight=1, uniform="tiles")
        self._notice = StatTile(tiles, "Aviso", value_wraplength=400)
        self._notice.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._last = StatTile(tiles, "Último envio", value_wraplength=400)
        self._last.grid(row=0, column=1, sticky="nsew")

        self._load_form(application.whatsapp_settings())

    def refresh(self) -> None:
        settings = self.application.whatsapp_settings()
        if not settings.enabled:
            self._notice.set("Desligado", "Nenhuma mensagem é enviada quando o token for encontrado.")
        elif self.application.reward_status().token_obtained:
            self._notice.set(
                "Ligado", "O token já foi encontrado: o próximo aviso sai depois de apagar o registro em Recompensas.", theme.AMBER
            )
        else:
            self._notice.set("Ligado", f"{_SERVICE_NAMES[settings.service]}, para {settings.phone}", theme.GOLD)

        status = self.application.whatsapp_status()
        kind = "Mensagem de teste" if status.test else "Aviso do token"
        if status.state is DeliveryState.NONE:
            self._last.set("—", "Nenhuma mensagem desde que o app abriu")
        elif status.state is DeliveryState.SENDING:
            self._last.set("Enviando...", kind)
        elif status.state is DeliveryState.SENT:
            self._last.set(f"Enviada às {status.at:%H:%M:%S}", f"{kind}. {status.detail}", theme.GREEN)
        else:
            self._last.set(f"Falhou às {status.at:%H:%M:%S}", f"{kind}. {status.detail}", theme.RED)

        state = "disabled" if status.state is DeliveryState.SENDING else "normal"
        if self._test.cget("state") != state:
            self._test.configure(state=state)

    def _entry(
        self, label: str, service: str | None = None, placeholder: str | None = None, secret: bool = False
    ) -> ctk.CTkEntry:
        label_widget = ctk.CTkLabel(self._form, text=label, text_color=theme.TEXT)
        label_widget.grid(row=self._row, column=0, sticky="w", padx=14, pady=4)
        entry = ctk.CTkEntry(self._form, text_color=theme.TEXT, placeholder_text=placeholder, show="•" if secret else "")
        entry.grid(row=self._row, column=1, sticky="ew", padx=14, pady=4)
        self._row += 1
        if service is not None:
            self._service_widgets[service] += [label_widget, entry]
        return entry

    def _show_service(self) -> None:
        service = _SERVICES[self._service.get()]
        for name, widgets in self._service_widgets.items():
            for widget in widgets:
                if name == service:
                    widget.grid()
                else:
                    widget.grid_remove()
        self._help.configure(text=_HELP[service])

    def _load_form(self, settings: WhatsAppSettings) -> None:
        if settings.enabled:
            self._enabled.select()
        else:
            self._enabled.deselect()
        self._service.set(_SERVICE_NAMES[settings.service])
        values = {
            self._phone: settings.phone,
            self._api_key: settings.callmebot_api_key,
            self._token: settings.meta_token,
            self._phone_number_id: settings.meta_phone_number_id,
            self._template: settings.meta_template,
            self._language: settings.meta_language,
        }
        for entry, value in values.items():
            # Um campo vazio fica como está: apagá-lo ou inserir texto vazio sumiria com a dica dele.
            if value:
                entry.delete(0, "end")
                entry.insert(0, value)
        self._show_service()

    def _read_form(self) -> WhatsAppSettings | None:
        try:
            return WhatsAppSettings(
                enabled=bool(self._enabled.get()),
                service=_SERVICES[self._service.get()],
                phone=self._phone.get(),
                callmebot_api_key=self._api_key.get(),
                meta_token=self._token.get(),
                meta_phone_number_id=self._phone_number_id.get(),
                meta_template=self._template.get(),
                meta_language=self._language.get(),
            )
        except ValidationError as error:
            self._show_status(_error_text(error), theme.RED)
            return None

    def _save(self) -> bool:
        settings = self._read_form()
        if settings is None:
            return False
        try:
            self.application.save_whatsapp_settings(settings)
        except OSError as error:
            logger.exception("Falha ao salvar a configuração do WhatsApp")
            self._show_status(f"Não foi possível salvar: {error}", theme.RED)
            return False
        self._load_form(settings)  # mostra o número como ficou guardado
        self._show_status("Salvo.", theme.GREEN)
        return True

    def _send_test(self) -> None:
        # Salva antes, para o teste usar o que está na tela e nada ficar sem salvar.
        if self._save():
            self.application.send_whatsapp_test()

    def _show_status(self, text: str, color: tuple[str, str]) -> None:
        self._status.configure(text=text, text_color=color)
