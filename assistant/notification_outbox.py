"""Conta para fora do aplicativo cada notificação mostrada e cada clique feito: um arquivo que outros programas leem e,
se configurado, um webhook.

O arquivo é o contrato com outros programas. Ele só recebe linhas novas, uma por registro, em JSON.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from core.config import NotificationConfig

logger = logging.getLogger(__name__)

CLICK_REQUIRED = "click_required"  # notificação que pede um clique; cada lembrete gera outro registro
CLICK_DONE = "click_done"  # o usuário deu o clique pedido, percebido pela mudança da tela
INFO = "info"
REWARD_OBTAINED = "reward_obtained"

# O OneDrive pode segurar o arquivo por instantes, e um registro perdido deixaria a contagem errada.
_WRITE_ATTEMPTS = 5
_WRITE_RETRY_SECONDS = 0.2
_WEBHOOK_TIMEOUT_SECONDS = 3
_WEBHOOK_ATTEMPTS = 3
_WEBHOOK_RETRY_SECONDS = 1.0
_SECRET_HEADER = "X-Automaizer-Secret"


class NotificationOutbox:
    def __init__(self, config: NotificationConfig, path: Path) -> None:
        self._config = config
        self._path = path
        self._lock = threading.Lock()
        self._pending: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None

    @property
    def path(self) -> Path:
        return self._path

    def update_config(self, config: NotificationConfig) -> None:
        self._config = config

    def publish(self, kind: str, message: str, button: str | None = None) -> None:
        record = {
            "id": uuid.uuid4().hex,
            "type": kind,
            "button": button,
            "message": message,
            "created_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "app": "AutomaizerOnePiece",
        }
        if self._config.file_enabled:
            self._append(record)
        if self._config.webhook_url:
            self._send_later(record)

    def close(self) -> None:
        worker = self._worker
        if worker is not None:
            self._pending.put(None)
            worker.join(_WEBHOOK_TIMEOUT_SECONDS)

    def _append(self, record: dict[str, object]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        for attempt in range(1, _WRITE_ATTEMPTS + 1):
            try:
                with self._lock:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    # Uma única escrita por linha, para quem lê nunca encontrar uma linha pela metade seguida de outra.
                    with self._path.open("a", encoding="utf-8", newline="\n") as file:
                        file.write(line)
                return
            except PermissionError:
                if attempt == _WRITE_ATTEMPTS:
                    logger.exception("O arquivo %s continua travado: registro perdido", self._path)
                else:
                    time.sleep(_WRITE_RETRY_SECONDS)
            except OSError:
                logger.exception("Não foi possível gravar o registro em %s", self._path)
                return

    def _send_later(self, record: dict[str, object]) -> None:
        # Em outra thread: um servidor lento não pode atrasar a análise do jogo.
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._send_pending, name="webhook", daemon=True)
                self._worker.start()
        self._pending.put(json.dumps(record, ensure_ascii=False).encode("utf-8"))

    def _send_pending(self) -> None:
        while True:
            body = self._pending.get()
            if body is None:
                return
            self._send(body)

    def _send(self, body: bytes) -> None:
        config = self._config
        headers = {"Content-Type": "application/json"}
        if config.webhook_secret:
            headers[_SECRET_HEADER] = config.webhook_secret
        request = urllib.request.Request(config.webhook_url, data=body, headers=headers, method="POST")
        for attempt in range(1, _WEBHOOK_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=_WEBHOOK_TIMEOUT_SECONDS):
                    return
            except (urllib.error.URLError, OSError, ValueError) as exc:
                logger.warning("Webhook falhou (tentativa %d de %d): %s", attempt, _WEBHOOK_ATTEMPTS, exc)
                if attempt < _WEBHOOK_ATTEMPTS:
                    time.sleep(_WEBHOOK_RETRY_SECONDS)
