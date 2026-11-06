import json
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from assistant.notification_outbox import CLICK_DONE, INFO, REWARD_OBTAINED, NotificationOutbox
from core.config import NotificationConfig


def lines_of(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def path(tmp_path):
    return tmp_path / "pasta" / "notifications.jsonl"


@pytest.fixture
def receiver():
    """Servidor local que guarda o que recebe; responde com o código da lista, depois 200."""
    received = []
    codes = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((json.loads(body), dict(self.headers)))
            self.send_response(codes.pop(0) if codes else 200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/tick", received, codes
    server.shutdown()
    server.server_close()


def wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("condição não atendida a tempo")


def test_each_notification_is_one_json_line(path):
    outbox = NotificationOutbox(NotificationConfig(), path)

    outbox.publish(CLICK_DONE, "Clique feito: Jogar", "PLAY")
    outbox.publish(REWARD_OBTAINED, "Monitoramento finalizado")
    outbox.publish(INFO, "Você é o último vivo")

    records = lines_of(path)
    assert [record["type"] for record in records] == [CLICK_DONE, REWARD_OBTAINED, INFO]
    assert records[0]["message"] == "Clique feito: Jogar"
    assert records[0]["button"] == "PLAY"
    assert records[2]["button"] is None
    assert records[0]["app"] == "AutomaizerOnePiece"


def test_records_have_unique_ids_and_a_date_with_timezone(path):
    outbox = NotificationOutbox(NotificationConfig(), path)
    outbox.publish(INFO, "a")
    outbox.publish(INFO, "a")

    first, second = lines_of(path)
    assert first["id"] != second["id"]
    assert datetime.fromisoformat(first["created_at"]).tzinfo is not None


def test_accents_are_kept_readable_in_the_file(path):
    NotificationOutbox(NotificationConfig(), path).publish(INFO, "Você é o último")
    assert "Você é o último" in path.read_text(encoding="utf-8")


def test_file_only_grows(path):
    NotificationOutbox(NotificationConfig(), path).publish(INFO, "primeira")
    NotificationOutbox(NotificationConfig(), path).publish(INFO, "segunda")
    assert [record["message"] for record in lines_of(path)] == ["primeira", "segunda"]


def test_lines_end_in_a_single_newline(path):
    outbox = NotificationOutbox(NotificationConfig(), path)
    outbox.publish(INFO, "a")
    assert path.read_bytes().endswith(b"}\n")


def test_file_can_be_turned_off(path):
    NotificationOutbox(NotificationConfig(file_enabled=False), path).publish(INFO, "a")
    assert not path.exists()


def test_config_update_applies_to_the_next_notification(path):
    outbox = NotificationOutbox(NotificationConfig(), path)
    outbox.update_config(NotificationConfig(file_enabled=False))
    outbox.publish(INFO, "a")
    assert not path.exists()


def test_write_failure_does_not_reach_the_monitoring(tmp_path):
    blocked = tmp_path / "arquivo"
    blocked.write_text("não é uma pasta", encoding="utf-8")

    NotificationOutbox(NotificationConfig(), blocked / "notifications.jsonl").publish(INFO, "a")


def test_file_held_for_a_moment_does_not_lose_the_record(path, monkeypatch):
    monkeypatch.setattr("assistant.notification_outbox._WRITE_RETRY_SECONDS", 0.0)
    real_open = type(path).open
    failures = [PermissionError("com o OneDrive"), PermissionError("com o OneDrive")]

    def held_open(self, *args, **kwargs):
        if failures:
            raise failures.pop()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", held_open)

    NotificationOutbox(NotificationConfig(), path).publish(INFO, "a")

    assert failures == []
    monkeypatch.undo()
    assert [record["message"] for record in lines_of(path)] == ["a"]


def test_file_held_for_too_long_does_not_reach_the_monitoring(path, monkeypatch):
    monkeypatch.setattr("assistant.notification_outbox._WRITE_RETRY_SECONDS", 0.0)

    def held_open(self, *args, **kwargs):
        raise PermissionError("com o OneDrive")

    monkeypatch.setattr(type(path), "open", held_open)

    NotificationOutbox(NotificationConfig(), path).publish(INFO, "a")


def test_simultaneous_notifications_do_not_mix_lines(path):
    outbox = NotificationOutbox(NotificationConfig(), path)
    threads = [
        threading.Thread(target=lambda: [outbox.publish(INFO, "x" * 200) for _ in range(50)]) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(lines_of(path)) == 200


def test_webhook_receives_the_same_record(path, receiver):
    url, received, _codes = receiver
    outbox = NotificationOutbox(NotificationConfig(webhook_url=url), path)

    outbox.publish(CLICK_DONE, "Clique feito: Jogar", "PLAY")

    wait_for(lambda: received)
    outbox.close()
    assert received[0][0] == lines_of(path)[0]


def test_webhook_sends_the_secret_only_when_configured(path, receiver):
    url, received, _codes = receiver
    outbox = NotificationOutbox(NotificationConfig(webhook_url=url), path)
    outbox.publish(INFO, "sem senha")
    wait_for(lambda: len(received) == 1)
    assert "X-Automaizer-Secret" not in received[0][1]

    outbox.update_config(NotificationConfig(webhook_url=url, webhook_secret="abc"))
    outbox.publish(INFO, "com senha")
    wait_for(lambda: len(received) == 2)
    outbox.close()
    assert received[1][1]["X-Automaizer-Secret"] == "abc"


def test_webhook_works_without_the_file(path, receiver):
    url, received, _codes = receiver
    outbox = NotificationOutbox(NotificationConfig(file_enabled=False, webhook_url=url), path)

    outbox.publish(INFO, "a")

    wait_for(lambda: received)
    outbox.close()
    assert not path.exists()


def test_webhook_is_retried_after_a_server_error(path, receiver, monkeypatch):
    monkeypatch.setattr("assistant.notification_outbox._WEBHOOK_RETRY_SECONDS", 0.01)
    url, received, codes = receiver
    codes.append(500)
    outbox = NotificationOutbox(NotificationConfig(webhook_url=url), path)

    outbox.publish(INFO, "a")

    wait_for(lambda: len(received) == 2)
    outbox.close()
    assert received[0][0]["id"] == received[1][0]["id"]


def test_unreachable_webhook_does_not_block_the_notification(path, monkeypatch):
    monkeypatch.setattr("assistant.notification_outbox._WEBHOOK_RETRY_SECONDS", 0.01)
    outbox = NotificationOutbox(NotificationConfig(webhook_url="http://127.0.0.1:9/tick"), path)

    started = time.monotonic()
    outbox.publish(INFO, "a")
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert len(lines_of(path)) == 1
    outbox.close()


def test_close_without_webhook_is_harmless(path):
    outbox = NotificationOutbox(NotificationConfig(), path)
    outbox.close()
    outbox.close()


def test_webhook_can_be_used_again_after_closing(path, receiver):
    url, received, _codes = receiver
    outbox = NotificationOutbox(NotificationConfig(webhook_url=url), path)
    outbox.publish(INFO, "a")
    wait_for(lambda: len(received) == 1)
    outbox.close()

    outbox.publish(INFO, "b")

    wait_for(lambda: len(received) == 2)
    outbox.close()
