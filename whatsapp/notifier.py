"""Aviso no WhatsApp quando o token de recompensa é encontrado.

Separado do resto do aplicativo, como o contador de cliques: só recebe o aviso do token e manda a mensagem em uma thread
própria, para que um serviço lento não atrase a interface. Dois serviços podem mandar:

- CallMeBot: grátis para uso pessoal, só com o número e a chave que o bot responde. Manda apenas para o número que pediu
  a chave. É um serviço pequeno, que às vezes fica sem vagas para números novos ou deixa de entregar por um tempo.
- API oficial do WhatsApp, da Meta: mais passos para configurar. O número de teste que a Meta cria não cobra e manda
  para até 5 números cadastrados. Fora de uma conversa aberta, só aceita modelos aprovados, como o hello_world, que toda
  conta nova já tem.

O número, a chave e o token ficam em data/whatsapp.json, fora do config.json, que vai para o git.
"""

from __future__ import annotations

import html
import http.client
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from core.event_manager import EventManager, EventType

logger = logging.getLogger(__name__)

CALLMEBOT = "callmebot"
META = "meta"
CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
META_URL = "https://graph.facebook.com/v25.0/{phone_number_id}/messages"

TOKEN_MESSAGE = "AutomaizerOnePiece: o token de recompensa foi encontrado em {when:%d/%m/%Y} às {when:%H:%M}."
TEST_MESSAGE = "AutomaizerOnePiece: mensagem de teste. Quando o token for encontrado, o aviso chega aqui."

_USER_AGENT = "AutomaizerOnePiece"
_TIMEOUT_SECONDS = 20
_ATTEMPTS = 3
_RETRY_SECONDS = 15.0
_CLOSE_SECONDS = 5.0
_MAX_BODY = 65_536
_MAX_DETAIL = 300
# O OneDrive pode segurar o arquivo por instantes.
_SAVE_ATTEMPTS = 5
_SAVE_RETRY_SECONDS = 0.2

# Vazio sempre vale: só o serviço escolhido precisa dos campos dele.
_FORMATS = {
    "callmebot_api_key": (r"[A-Za-z0-9]+", "a chave do CallMeBot tem só letras e números"),
    "meta_token": (r"[!-~]+", "o token não pode ter espaços nem acentos"),  # vai em um cabeçalho HTTP
    "meta_phone_number_id": (r"\d+", "a identificação do número de telefone tem só números"),
    "meta_template": (r"[a-z0-9_]+", "o nome do modelo tem só letras minúsculas, números e _"),
    "meta_language": (r"[A-Za-z]{2,3}(_[A-Za-z]{2,4})?", "informe o idioma como no modelo, por exemplo pt_BR"),
}

_CALLMEBOT_ECHO = ("Message to:", "Text to send:")
_CALLMEBOT_WRONG_KEY = "A chave não confere com o número: confira os dois, com o número no formato +55 11 91234-5678."
_META_HINTS = {
    190: "O token expirou ou está errado. O da página Configuração da API vale só 24 horas.",
    131030: "O seu número não está na lista do número de teste: cadastre-o no campo Para da Configuração da API.",
    132001: "Não há modelo aprovado com esse nome nesse idioma.",
}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} e {items[-1]}"


class WhatsAppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    service: Literal["callmebot", "meta"] = CALLMEBOT
    phone: str = ""  # o do usuário, que recebe a mensagem: + e o código do país
    callmebot_api_key: str = ""
    meta_token: str = ""
    meta_phone_number_id: str = ""  # o número que manda, como a Meta o identifica
    # O hello_world já vem aprovado em toda conta nova; um modelo em português pode substituí-lo.
    meta_template: str = "hello_world"
    meta_language: str = "en_US"

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        phone = re.sub(r"[\s().-]", "", value)
        if phone and not re.fullmatch(r"\+\d{8,15}", phone):
            raise ValueError("informe o número com + e o código do país, como +55 11 91234-5678")
        return phone

    @field_validator(*_FORMATS)
    @classmethod
    def _check_format(cls, value: str, info: ValidationInfo) -> str:
        value = value.strip()
        pattern, message = _FORMATS[info.field_name]
        if value and not re.fullmatch(pattern, value):
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def _check_complete(self) -> WhatsAppSettings:
        missing = self.missing()
        if self.enabled and missing:
            raise ValueError(f"para ligar o aviso, preencha {_join(missing)}")
        return self

    def missing(self) -> list[str]:
        """O que falta para o serviço escolhido conseguir mandar a mensagem."""
        if self.service == CALLMEBOT:
            fields = {"o seu número": self.phone, "a chave do CallMeBot": self.callmebot_api_key}
        else:
            fields = {
                "o seu número": self.phone,
                "o token de acesso": self.meta_token,
                "a identificação do número de telefone": self.meta_phone_number_id,
                "o nome do modelo": self.meta_template,
                "o idioma do modelo": self.meta_language,
            }
        return [name for name, value in fields.items() if not value]


class DeliveryState(str, Enum):
    NONE = "NONE"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WhatsAppStatus:
    state: DeliveryState = DeliveryState.NONE
    test: bool = False
    detail: str = ""  # a resposta do serviço ou o motivo da falha
    at: datetime | None = None


@dataclass(frozen=True)
class _Message:
    settings: WhatsAppSettings
    text: str
    test: bool


# Recebe o pedido e devolve o código e o corpo da resposta; os testes trocam por uma resposta pronta.
Transport = Callable[[urllib.request.Request], tuple[int, str]]


def load_settings(path: Path) -> tuple[WhatsAppSettings, str | None]:
    """Retorna (configuração, aviso). Um arquivo inválido é guardado com outro nome, pois tem o número do usuário."""
    try:
        return WhatsAppSettings.model_validate_json(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return WhatsAppSettings(), None
    except OSError as exc:
        logger.exception("Não foi possível ler %s", path)
        return WhatsAppSettings(), f"Não foi possível ler {path.name} ({exc}): aviso no WhatsApp desligado até reabrir o app."
    except ValueError as exc:
        backup = path.with_name(f"{path.stem}.invalid-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        os.replace(path, backup)
        summary = str(exc).splitlines()[0]
        return WhatsAppSettings(), f"{path.name} inválido ({summary}): aviso no WhatsApp desligado; original salvo em {backup.name}."


def save_settings(settings: WhatsAppSettings, path: Path) -> None:
    """Grava de forma atômica, para que uma falha no meio não corrompa o arquivo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
    for attempt in range(1, _SAVE_ATTEMPTS + 1):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == _SAVE_ATTEMPTS:
                raise
            time.sleep(_SAVE_RETRY_SECONDS)


def _open(request: urllib.request.Request) -> tuple[int, str]:
    """Faz o pedido e devolve o código e o corpo, inclusive o de um erro, que traz o motivo."""
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, response.read(_MAX_BODY).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read(_MAX_BODY).decode("utf-8", "replace")


def _plain_text(markup: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", markup)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())[:_MAX_DETAIL]


def _explain(hint: str, reply: str) -> str:
    return f"{hint} Resposta do serviço: {reply}" if hint else reply


def _callmebot_request(settings: WhatsAppSettings, text: str) -> urllib.request.Request:
    # urlencode troca o + do número por %2B; sem isso ele chegaria como espaço.
    query = urllib.parse.urlencode({"phone": settings.phone, "text": text, "apikey": settings.callmebot_api_key})
    return urllib.request.Request(f"{CALLMEBOT_URL}?{query}", headers={"User-Agent": _USER_AGENT})


def _callmebot_result(status: int, body: str) -> tuple[bool, str]:
    # O HTML repete o número e o texto antes do resultado. Só o 200 é sucesso: a chave errada volta com 203.
    parts = (_plain_text(part) for part in re.split(r"(?i)<p\b[^>]*>", body))
    reply = " ".join(part for part in parts if part and not part.startswith(_CALLMEBOT_ECHO)) or f"HTTP {status}"
    if status == 200:
        return True, reply
    return False, _explain(_CALLMEBOT_WRONG_KEY if "apikey is invalid" in reply.lower() else "", reply)


def _meta_request(settings: WhatsAppSettings) -> urllib.request.Request:
    body = {
        "messaging_product": "whatsapp",
        "to": settings.phone.removeprefix("+"),
        "type": "template",
        "template": {"name": settings.meta_template, "language": {"code": settings.meta_language}},
    }
    headers = {"Authorization": f"Bearer {settings.meta_token}", "Content-Type": "application/json", "User-Agent": _USER_AGENT}
    url = META_URL.format(phone_number_id=settings.meta_phone_number_id)
    return urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")


def _meta_result(status: int, body: str) -> tuple[bool, str]:
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return False, _plain_text(body) or f"HTTP {status}"
    if status == 200 and data.get("messages"):
        return True, "A Meta aceitou a mensagem."
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    error_data = error.get("error_data") if isinstance(error.get("error_data"), dict) else {}
    reply = " ".join(str(part) for part in (error.get("message"), error_data.get("details")) if part) or f"HTTP {status}"
    return False, _explain(_META_HINTS.get(error.get("code"), ""), reply)


class WhatsAppNotifier:
    def __init__(self, path: Path, events: EventManager, transport: Transport | None = None) -> None:
        self._path = path
        self._events = events
        self._transport = transport or _open
        self._settings, self.warning = load_settings(path)
        self._status = WhatsAppStatus()
        self._lock = threading.Lock()
        self._pending: queue.SimpleQueue[_Message | None] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None

    @property
    def settings(self) -> WhatsAppSettings:
        return self._settings

    @property
    def status(self) -> WhatsAppStatus:
        return self._status

    def save(self, settings: WhatsAppSettings) -> None:
        save_settings(settings, self._path)
        self._settings = settings

    def notify_token_found(self, detected_at: datetime | None = None) -> None:
        if self._settings.enabled:
            text = TOKEN_MESSAGE.format(when=detected_at or datetime.now())
            self._send_later(_Message(self._settings, text, test=False))

    def send_test(self) -> None:
        """Manda a mensagem de teste com o que está salvo, mesmo com o aviso desligado."""
        self._send_later(_Message(self._settings, TEST_MESSAGE, test=True))

    def close(self) -> None:
        """Dá um tempo para terminar o que está na fila, como o aviso de um token encontrado logo antes de fechar."""
        with self._lock:
            worker = self._worker
        if worker is not None and worker.is_alive():
            self._pending.put(None)
            worker.join(_CLOSE_SECONDS)

    def _send_later(self, message: _Message) -> None:
        missing = message.settings.missing()
        if missing:
            self._finish(message, False, f"Preencha {_join(missing)}.")
            return
        # Em outra thread: o serviço pode demorar, e quem chama é a interface.
        self._status = WhatsAppStatus(DeliveryState.SENDING, message.test, at=datetime.now())
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._send_pending, name="whatsapp", daemon=True)
                self._worker.start()
        self._pending.put(message)

    def _send_pending(self) -> None:
        while True:
            message = self._pending.get()
            if message is None:
                return
            try:
                sent, detail = self._deliver(message)
            except Exception:  # a thread precisa continuar para as próximas mensagens
                logger.exception("Falha inesperada ao mandar a mensagem do WhatsApp")
                sent, detail = False, "Erro inesperado; os detalhes estão no log."
            self._finish(message, sent, detail)

    def _deliver(self, message: _Message) -> tuple[bool, str]:
        settings = message.settings
        if settings.service == CALLMEBOT:
            request, read = _callmebot_request(settings, message.text), _callmebot_result
        else:
            request, read = _meta_request(settings), _meta_result
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                status, body = self._transport(request)
            except (OSError, http.client.HTTPException) as exc:  # sem internet, servidor fora do ar ou tempo esgotado
                sent, detail, retry = False, f"Sem resposta do serviço ({getattr(exc, 'reason', exc)}).", True
            else:
                sent, detail = read(status, body)
                retry = status >= 500
            if sent or not retry or attempt == _ATTEMPTS:
                break
            logger.warning("WhatsApp falhou (tentativa %d de %d): %s", attempt, _ATTEMPTS, detail)
            time.sleep(_RETRY_SECONDS)
        return sent, detail

    def _finish(self, message: _Message, sent: bool, detail: str) -> None:
        self._status = WhatsAppStatus(DeliveryState.SENT if sent else DeliveryState.FAILED, message.test, detail, datetime.now())
        kind = "de teste" if message.test else "do token"
        if sent:
            self._events.publish(EventType.WHATSAPP_SENT, f"Mensagem {kind} enviada no WhatsApp")
        else:
            self._events.publish(EventType.WHATSAPP_FAILED, f"Mensagem {kind} não enviada no WhatsApp: {detail}")
