from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import ctypes

from assistant.notification_outbox import CLICK_REQUIRED
from core.state_machine import GameState

logger = logging.getLogger(__name__)

# Considera a notificação que realmente pede um clique (CLICK_REQUIRED).
COUNTED_TYPES = frozenset({CLICK_REQUIRED})
POLL_SECONDS = 0.5
MAX_IDS = 1000  # ids guardados para que a mesma linha nunca conte duas vezes
RECENT_CLICKS = 20

# Eventos de mouse da WinAPI
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

# Códigos de teclas virtuais (W, A, S, D, Espaço, Tecla 1)
VK_W = 0x57
VK_A = 0x41
VK_S = 0x53
VK_D = 0x44
VK_SPACE = 0x20
VK_1 = 0x31
KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002

user32 = ctypes.WinDLL("user32", use_last_error=True)


@dataclass(frozen=True)
class CountedClick:
    message: str
    button: str | None
    created_at: datetime


@dataclass(frozen=True)
class ClickCount:
    ready: bool = False  # o estado salvo já foi lido; antes disso, o total ainda não é conhecido
    total: int = 0
    recent: tuple[CountedClick, ...] = ()  # cliques desta execução, do mais novo para o mais antigo


def _read_state(path: Path) -> dict:
    """Devolve o estado salvo, ou {} se não houver. Arquivo travado gera OSError."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError("o conteúdo não é um objeto")
        return state
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        backup = path.with_name(f"{path.stem}.danificado-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        logger.error("Estado do contador inválido em %s (%s): guardado como %s, e a contagem recomeça", path, exc, backup.name)
        os.replace(path, backup)
        return {}


def _write_state(path: Path, state: dict) -> None:
    """Grava de forma atômica: quem lê nunca vê um arquivo pela metade."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(state, file)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _end_of_last_complete_line(path: Path) -> int:
    """Posição logo após o último \\n, para não pegar uma linha ainda sendo escrita."""
    chunk_size = 4096
    with path.open("rb") as file:
        position = file.seek(0, os.SEEK_END)
        while position > 0:
            start = max(0, position - chunk_size)
            file.seek(start)
            newline = file.read(position - start).rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            position = start
    return 0


def _created_at(value: object) -> datetime:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else datetime.now()
    except ValueError:
        return datetime.now()


def execute_triple_click() -> None:
    """Executa 3 cliques esquerdos consecutivos do mouse."""
    for i in range(3):
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        if i < 2:
            time.sleep(0.1)


def press_key(vk_code: int, duration: float) -> None:
    """Pressiona e segura uma tecla específica por um tempo determinado."""
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYDOWN, 0)
    time.sleep(duration)
    user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)


class ClickCounter:
    def __init__(
        self,
        notifications_path: Path,
        state_path: Path,
        poll_seconds: float = POLL_SECONDS,
        game_state: Callable[[], GameState] = lambda: GameState.UNKNOWN,
        espera_clique_segundos: float = 2.0,
        idade_maxima_segundos: float = 60.0,
        clicar_de_verdade: bool = True,
    ) -> None:
        self._path = Path(os.path.abspath(notifications_path))
        self._state_path = state_path
        self._poll_seconds = poll_seconds
        self._game_state = game_state
        self._espera_clique = espera_clique_segundos
        self._idade_maxima = idade_maxima_segundos
        self._clicar_de_verdade = clicar_de_verdade

        self._lock = threading.Lock()
        self._loaded = False
        self._position: int | None = None  # None: o início da leitura ainda não foi definido
        self._total = 0
        self._ids: deque[str] = deque(maxlen=MAX_IDS)
        self._recent: deque[CountedClick] = deque(maxlen=RECENT_CLICKS)
        self._dirty = False
        self._count = ClickCount()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._wasd_thread: threading.Thread | None = None

    @property
    def count(self) -> ClickCount:
        return self._count

    @property
    def game_state(self) -> GameState:
        return self._game_state()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        
        # Thread principal de monitoramento de notificações e cliques associados
        self._thread = threading.Thread(target=self._run, name="click-counter", daemon=True)
        self._thread.start()

        # Thread secundária para a sequência aleatória de WASD / Espaço / Cliques / Tecla 1 (somente em IN_MATCH)
        self._wasd_thread = threading.Thread(target=self._wasd_sequence_loop, name="wasd-random-actions", daemon=True)
        self._wasd_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None
        
        wasd_thread = self._wasd_thread
        if wasd_thread is not None:
            wasd_thread.join(timeout)
        self._wasd_thread = None

        self._poll_safely()

    def reset(self) -> None:
        """Zera o total. A posição e os ids continuam, então nenhum clique antigo é contado de novo."""
        with self._lock:
            if not self._loaded:
                return
            self._total = 0
            self._dirty = True
            self._save()
            self._publish()

    def poll(self) -> None:
        """Um ciclo: lê as linhas completas novas, salva o estado e publica o total."""
        with self._lock:
            if not self._loaded and not self._load():
                return
            if self._position is None:
                self._find_start()
            if self._position is not None:
                self._read_new_lines()
            if self._dirty:
                self._save()
            self._publish()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._poll_safely()
            self._stop_event.wait(self._poll_seconds)

    def _wasd_sequence_loop(self) -> None:
        """Gerencia as ações automáticas durante o estado IN_MATCH."""
        keys = [VK_W, VK_A, VK_S, VK_D, VK_SPACE, "CLICK"]
        
        last_match_state = False
        match_start_time = 0.0
        tecla_um_pressionada = False

        while not self._stop_event.is_set():
            current_state = self.game_state

            if current_state == GameState.IN_MATCH:
                # Detecta o exato momento em que entrou na partida para contar os 120 segundos
                if not last_match_state:
                    match_start_time = time.time()
                    tecla_um_pressionada = False
                    last_match_state = True
                    logger.info("[Match Loop] Partida iniciada (IN_MATCH detectado). Contando 120s para a tecla 1.")

                # Verifica se já passaram 120 segundos desde o início da partida para apertar a tecla 1
                if not tecla_um_pressionada and (time.time() - match_start_time >= 120.0):
                    logger.info("[Match Loop] 120 segundos passados em IN_MATCH. Pressionando a tecla 1.")
                    if self._clicar_de_verdade:
                        press_key(VK_1, 0.3)
                    tecla_um_pressionada = True

                # Executa a escolha aleatória entre W, A, S, D, Espaço ou Clique Triplo
                chosen_action = random.choice(keys)

                try:
                    if chosen_action == "CLICK":
                        logger.info("[Match Loop] Executando triplo clique esquerdo aleatório em IN_MATCH.")
                        if self._clicar_de_verdade:
                            execute_triple_click()
                    else:
                        # Define a duração conforme a tecla escolhida (W = 30s, A/S/D/Espaço = 15s ou aleatório curto para espaço)
                        if chosen_action == VK_W:
                            duration = 30.0
                            key_name = 'W (30s)'
                        elif chosen_action in (VK_A, VK_S, VK_D):
                            duration = 15.0
                            key_name = {VK_A: 'A', VK_S: 'S', VK_D: 'D'}.get(chosen_action) + ' (15s)'
                        else:  # VK_SPACE
                            duration = 1.0  # Espaço por um toque rápido ou ajuste desejado
                            key_name = 'ESPAÇO'

                        logger.info("[Match Loop] Pressionando tecla %s em IN_MATCH.", key_name)
                        if self._clicar_de_verdade:
                            press_key(chosen_action, duration)
                except Exception:
                    logger.exception("Erro ao executar ação aleatória em IN_MATCH.")

                # Pequeno intervalo aleatório entre os ciclos de ações
                sleep_time = random.uniform(2.0, 5.0)
                self._stop_event.wait(sleep_time)
            else:
                if last_match_state:
                    logger.info("[Match Loop] Estado IN_MATCH encerrado.")
                last_match_state = False
                self._stop_event.wait(1.0)

    def _poll_safely(self) -> None:
        try:
            self.poll()
        except Exception:
            logger.exception("Falha no contador de cliques")

    def _load(self) -> bool:
        try:
            saved = _read_state(self._state_path)
        except OSError:
            return False
        self._total = int(saved.get("total_ticks", 0))
        self._ids.extend(saved.get("ids", []))
        if _same_path(saved.get("path", ""), self._path):
            self._position = saved.get("position")
        self._loaded = True
        return True

    def _find_start(self) -> None:
        """Na primeira execução, ou com outro arquivo, os cliques que já estavam nele não contam."""
        try:
            self._position = _end_of_last_complete_line(self._path)
        except FileNotFoundError:
            self._position = 0
        except OSError:
            return  # Travado: a posição continua indefinida e é buscada no próximo ciclo.
        self._dirty = True
        logger.info("Contador de cliques: primeira leitura de %s, a partir do byte %d", self._path, self._position)

    def _read_new_lines(self) -> None:
        try:
            with self._path.open("rb") as file:
                size = file.seek(0, os.SEEK_END)
                if size < self._position:
                    logger.info("O arquivo de notificações ficou menor que a posição salva: lendo do início")
                    self._position = 0
                    self._dirty = True
                file.seek(self._position)
                data = file.read()
        except OSError:
            return  # Inexistente ou travado: tenta de novo no próximo ciclo.

        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            return
        for line in data[:last_newline].split(b"\n"):
            self._process_line(line)
        self._position += last_newline + 1
        self._dirty = True

    def _process_line(self, line: bytes) -> None:
        try:
            record = json.loads(line.decode("utf-8-sig"))
            if not isinstance(record, dict):
                raise ValueError("não é um objeto JSON")
        except ValueError as exc:
            logger.warning("Linha inválida ignorada pelo contador (%s): %r", exc, line[:200])
            return

        if record.get("type") not in COUNTED_TYPES:
            return
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            logger.warning("Notificação sem id ignorada pelo contador: %r", line[:200])
            return
        if record_id in self._ids:
            return

        self._ids.append(record_id)
        self._total += 1
        message = str(record.get("message") or "Clique necessário")
        button = record.get("button") if isinstance(record.get("button"), str) else None
        created_at = _created_at(record.get("created_at"))
        self._recent.appendleft(CountedClick(message, button, created_at))
        logger.info("Notificação contada: %s", message)

        # Dispara a lógica do triplo clique após a notificação chegar
        self._handle_notification_click(created_at)

    def _handle_notification_click(self, created_at: datetime) -> None:
        # Valida a idade máxima da notificação
        now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
        age = (now - created_at).total_seconds()
        if age > self._idade_maxima:
            logger.info("Notificação muito antiga (%.1f s). Ignorando triplo clique automático.", age)
            return

        # Aguarda o tempo configurado (ex: 2 segundos) antes de clicar
        if self._espera_clique > 0:
            logger.info("Aguardando %.1f segundos para efetuar o triplo clique da notificação...", self._espera_clique)
            time.sleep(self._espera_clique)

        if self._clicar_de_verdade:
            logger.info("Executando triplo clique esquerdo após notificação.")
            execute_triple_click()
        else:
            logger.info("[Simulação] Triplo clique da notificação ignorado (clicar_de_verdade = False).")

    def _save(self) -> None:
        state = {"path": str(self._path), "position": self._position, "total_ticks": self._total, "ids": list(self._ids)}
        try:
            _write_state(self._state_path, state)
        except OSError:
            return  # Continua pendente e é salvo no próximo ciclo.
        self._dirty = False

    def _publish(self) -> None:
        self._count = ClickCount(True, self._total, tuple(self._recent))