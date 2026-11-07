"""Aviso no WhatsApp: configuração, pedidos aos dois serviços e envio em outra thread. Nada sai deste PC."""

import json
import urllib.error
import urllib.parse
from datetime import datetime

import pytest
from pydantic import ValidationError

from core.event_manager import EventManager, EventType
from whatsapp.notifier import (
    CALLMEBOT_URL,
    DeliveryState,
    WhatsAppNotifier,
    WhatsAppSettings,
    load_settings,
    save_settings,
)

CALLMEBOT_OK = (
    200,
    "<p>Message to: +5511912345678<p>Text to send: teste<p><b>Message queued.</b> You will receive it in a few seconds.",
)
# Resposta real do CallMeBot a uma chave errada: vem com 203, e não com um código de erro.
CALLMEBOT_WRONG_KEY = (
    203,
    '<p>Message to: +5500000000000<p>Text to send: teste<p style="color:red"><b>APIKey is invalid.</b> Please create a new '
    "one or contact support if you lost it.",
)
META_OK = (
    200,
    json.dumps(
        {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "5511912345678", "wa_id": "5511912345678"}],
            "messages": [{"id": "wamid.HBgM"}],
        }
    ),
)

CALLMEBOT_ON = WhatsAppSettings(enabled=True, phone="+55 11 91234-5678", callmebot_api_key="123456")
META_ON = WhatsAppSettings(
    enabled=True, service="meta", phone="+55 11 91234-5678", meta_token="EAAGtoken", meta_phone_number_id="109876543210"
)


class FakeService:
    """Responde no lugar do serviço, na ordem dada, e guarda os pedidos."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else CALLMEBOT_OK
        if isinstance(reply, Exception):
            raise reply
        return reply

    def query(self):
        return urllib.parse.parse_qs(urllib.parse.urlsplit(self.requests[-1].full_url).query)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr("whatsapp.notifier._RETRY_SECONDS", 0)


@pytest.fixture
def events():
    return EventManager()


def make_notifier(tmp_path, events, service, settings=CALLMEBOT_ON):
    notifier = WhatsAppNotifier(tmp_path / "whatsapp.json", events, service)
    notifier.save(settings)
    return notifier


def finished(notifier):
    """Espera a thread mandar o que está na fila."""
    notifier.close()
    return notifier.status


# --- Configuração -----------------------------------------------------------


def test_number_is_kept_with_only_the_plus_and_the_digits():
    assert WhatsAppSettings(phone=" +55 (11) 91234-5678 ").phone == "+5511912345678"


@pytest.mark.parametrize("phone", ["11 91234-5678", "5511912345678", "+55 11", "+55 11 9123-456a"])
def test_number_needs_the_plus_and_the_country_code(phone):
    with pytest.raises(ValidationError, match="código do país"):
        WhatsAppSettings(phone=phone)


def test_turning_on_needs_what_the_chosen_service_uses():
    with pytest.raises(ValidationError, match="preencha o seu número e a chave do CallMeBot"):
        WhatsAppSettings(enabled=True)
    with pytest.raises(ValidationError, match="preencha o token de acesso e a identificação do número de telefone"):
        WhatsAppSettings(enabled=True, service="meta", phone="+5511912345678")
    assert not WhatsAppSettings(service="meta").enabled  # desligado, pode ficar pela metade


def test_pasting_the_whole_reply_of_the_bot_is_caught():
    with pytest.raises(ValidationError, match="só letras e números"):
        WhatsAppSettings(callmebot_api_key="Your APIKEY is 123456")


def test_settings_survive_reopening(tmp_path):
    path = tmp_path / "data" / "whatsapp.json"
    save_settings(META_ON, path)
    assert load_settings(path) == (META_ON, None)


def test_without_the_file_the_notice_is_off(tmp_path):
    settings, warning = load_settings(tmp_path / "whatsapp.json")
    assert settings == WhatsAppSettings()
    assert not settings.enabled
    assert warning is None


def test_damaged_file_is_kept_aside_and_the_notice_is_off(tmp_path):
    path = tmp_path / "whatsapp.json"
    path.write_text("{quebrado", encoding="utf-8")

    settings, warning = load_settings(path)

    assert not settings.enabled
    assert "whatsapp.json inválido" in warning
    assert len(list(tmp_path.glob("whatsapp.invalid-*.json"))) == 1


# --- CallMeBot --------------------------------------------------------------


def test_callmebot_gets_the_number_the_text_and_the_key(tmp_path, events):
    service = FakeService(CALLMEBOT_OK)
    notifier = make_notifier(tmp_path, events, service)

    notifier.notify_token_found(datetime(2026, 11, 6, 21, 14))
    status = finished(notifier)

    assert status.state is DeliveryState.SENT
    assert not status.test
    assert status.detail == "Message queued. You will receive it in a few seconds."
    url = service.requests[0].full_url
    assert url.startswith(f"{CALLMEBOT_URL}?")
    assert "phone=%2B5511912345678" in url  # um + solto chegaria como espaço
    assert service.query() == {
        "phone": ["+5511912345678"],
        "text": ["AutomaizerOnePiece: o token de recompensa foi encontrado em 06/11/2026 às 21:14."],
        "apikey": ["123456"],
    }


def test_callmebot_wrong_key_says_what_to_check_and_is_not_retried(tmp_path, events):
    service = FakeService(CALLMEBOT_WRONG_KEY)
    notifier = make_notifier(tmp_path, events, service)

    notifier.send_test()
    status = finished(notifier)

    assert status.state is DeliveryState.FAILED
    assert status.test
    assert len(service.requests) == 1
    assert status.detail.startswith("A chave não confere com o número")
    assert status.detail.endswith(
        "Resposta do serviço: APIKey is invalid. Please create a new one or contact support if you lost it."
    )


# --- API oficial ------------------------------------------------------------


def test_meta_gets_the_template_for_the_number(tmp_path, events):
    service = FakeService(META_OK)
    notifier = make_notifier(tmp_path, events, service, META_ON)

    notifier.notify_token_found()
    status = finished(notifier)

    request = service.requests[0]
    assert status.state is DeliveryState.SENT
    assert request.full_url == "https://graph.facebook.com/v25.0/109876543210/messages"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer EAAGtoken"
    assert json.loads(request.data) == {
        "messaging_product": "whatsapp",
        "to": "5511912345678",
        "type": "template",
        "template": {"name": "hello_world", "language": {"code": "en_US"}},
    }


@pytest.mark.parametrize(
    ("code", "hint"),
    [(190, "O token expirou"), (131030, "O seu número não está na lista"), (132001, "Não há modelo aprovado")],
)
def test_meta_errors_say_what_to_fix(tmp_path, events, code, hint):
    body = {"error": {"message": f"(#{code}) Falhou", "code": code, "error_data": {"details": "Detalhes da Meta."}}}
    service = FakeService((400, json.dumps(body)))
    notifier = make_notifier(tmp_path, events, service, META_ON)

    notifier.send_test()
    status = finished(notifier)

    assert status.state is DeliveryState.FAILED
    assert status.detail.startswith(hint)
    assert status.detail.endswith(f"Resposta do serviço: (#{code}) Falhou Detalhes da Meta.")
    assert len(service.requests) == 1


# --- Envio ------------------------------------------------------------------


def test_no_internet_and_server_errors_are_retried(tmp_path, events):
    service = FakeService(urllib.error.URLError("getaddrinfo failed"), (502, "Bad Gateway"), CALLMEBOT_OK)
    notifier = make_notifier(tmp_path, events, service)

    notifier.send_test()

    assert finished(notifier).state is DeliveryState.SENT
    assert len(service.requests) == 3


def test_gives_up_after_the_last_attempt(tmp_path, events):
    service = FakeService(*[TimeoutError("timed out")] * 3)
    notifier = make_notifier(tmp_path, events, service)

    notifier.send_test()
    status = finished(notifier)

    assert status.state is DeliveryState.FAILED
    assert status.detail == "Sem resposta do serviço (timed out)."
    assert len(service.requests) == 3


def test_nothing_goes_with_the_notice_off(tmp_path, events):
    service = FakeService()
    notifier = make_notifier(tmp_path, events, service, CALLMEBOT_ON.model_copy(update={"enabled": False}))

    notifier.notify_token_found()

    assert finished(notifier).state is DeliveryState.NONE
    assert service.requests == []


def test_the_test_goes_even_with_the_notice_off(tmp_path, events):
    service = FakeService()
    notifier = make_notifier(tmp_path, events, service, CALLMEBOT_ON.model_copy(update={"enabled": False}))

    notifier.send_test()

    assert finished(notifier).state is DeliveryState.SENT
    assert service.query()["text"] == [
        "AutomaizerOnePiece: mensagem de teste. Quando o token for encontrado, o aviso chega aqui."
    ]


def test_incomplete_test_fails_right_away(tmp_path, events):
    service = FakeService()
    notifier = make_notifier(tmp_path, events, service, WhatsAppSettings(phone="+5511912345678"))

    notifier.send_test()

    assert notifier.status.state is DeliveryState.FAILED
    assert notifier.status.detail == "Preencha a chave do CallMeBot."
    assert service.requests == []


def test_each_result_becomes_an_event(tmp_path, events):
    notifier = make_notifier(tmp_path, events, FakeService(CALLMEBOT_OK, CALLMEBOT_WRONG_KEY))

    notifier.notify_token_found()
    notifier.send_test()
    finished(notifier)

    newest, oldest = events.recent(2)
    assert (oldest.type, oldest.message) == (EventType.WHATSAPP_SENT, "Mensagem do token enviada no WhatsApp")
    assert newest.type is EventType.WHATSAPP_FAILED
    assert newest.message.startswith("Mensagem de teste não enviada no WhatsApp: A chave não confere")
