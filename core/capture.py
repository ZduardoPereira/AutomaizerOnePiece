"""Localização da janela do Fortnite e captura da sua área em uma thread dedicada.

A janela é apenas observada pelas APIs públicas do Windows e pelo MSS (captura da tela); o processo
do jogo é aberto somente com permissão para consultar o nome do executável.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import PureWindowsPath
from typing import Protocol

import mss
import numpy as np

from core.config import CaptureConfig
from core.event_manager import EventManager, EventType

logger = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.InternalGetWindowText.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.InternalGetWindowText.restype = ctypes.c_int
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetClientRect.restype = wintypes.BOOL
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.ClientToScreen.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


def enable_dpi_awareness() -> None:
    """Faz o Windows informar coordenadas físicas, para que a região capturada seja exata com escala de tela."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        logger.warning("Não foi possível ativar o reconhecimento de DPI", exc_info=True)


class CaptureState(str, Enum):
    STOPPED = "STOPPED"
    SEARCHING = "SEARCHING"
    MINIMIZED = "MINIMIZED"
    BACKGROUND = "BACKGROUND"
    CAPTURING = "CAPTURING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process_name: str | None
    left: int
    top: int
    width: int
    height: int
    is_minimized: bool
    is_foreground: bool


@dataclass(frozen=True)
class CaptureSnapshot:
    state: CaptureState = CaptureState.STOPPED
    window: WindowInfo | None = None
    frame: np.ndarray | None = None  # BGR, somente leitura
    frame_id: int = 0
    captured_at: datetime | None = None
    is_black: bool = False
    fps: float = 0.0
    error: str | None = None


def is_fortnite_window(
    process_name: str | None,
    title: str,
    process_names: Iterable[str],
    window_titles: Iterable[str],
) -> bool:
    """Decide se uma janela é o jogo.

    O nome do processo é o critério principal; o título só é usado se o processo não puder ser
    consultado, pois uma pasta aberta no Explorer também pode se chamar "Fortnite".
    """
    if process_name is not None:
        return process_name.casefold() in {name.casefold() for name in process_names}
    return title.casefold() in {name.casefold() for name in window_titles}


def is_black_frame(frame: np.ndarray, threshold: float) -> bool:
    """Indica um quadro quase todo preto: tela de transição ou jogo em tela cheia exclusiva."""
    return float(frame[::8, ::8].mean()) <= threshold


def is_window_foreground(hwnd: int) -> bool:
    return _user32.GetForegroundWindow() == hwnd


def _window_title(hwnd: int) -> str:
    # GetWindowText envia uma mensagem às janelas do próprio processo e travaria esta thread enquanto
    # a interface aguarda o seu encerramento; InternalGetWindowText não envia mensagens.
    buffer = ctypes.create_unicode_buffer(512)
    _user32.InternalGetWindowText(hwnd, buffer, len(buffer))
    return buffer.value


def _process_name(hwnd: int) -> str | None:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return PureWindowsPath(buffer.value).name
    finally:
        _kernel32.CloseHandle(handle)


class WindowFinder:
    """Localiza a janela do jogo pelas APIs do Windows."""

    def __init__(self, process_names: Iterable[str], window_titles: Iterable[str]) -> None:
        self._process_names = list(process_names)
        self._window_titles = list(window_titles)

    def find(self) -> WindowInfo | None:
        found: list[int] = []

        def visit(hwnd: int, _lparam: int) -> bool:
            if _user32.IsWindowVisible(hwnd):
                title = _window_title(hwnd)
                if title and is_fortnite_window(_process_name(hwnd), title, self._process_names, self._window_titles):
                    found.append(hwnd)
                    return False
            return True

        _user32.EnumWindows(_WNDENUMPROC(visit), 0)
        return self.describe(found[0]) if found else None

    def describe(self, hwnd: int) -> WindowInfo | None:
        """Lê o estado atual de uma janela conhecida, ou None se ela não existe mais."""
        if not _user32.IsWindow(hwnd):
            return None
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not _user32.GetClientRect(hwnd, ctypes.byref(rect)) or not _user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        return WindowInfo(
            hwnd=hwnd,
            title=_window_title(hwnd),
            process_name=_process_name(hwnd),
            left=origin.x,
            top=origin.y,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
            is_minimized=bool(_user32.IsIconic(hwnd)),
            is_foreground=_user32.GetForegroundWindow() == hwnd,
        )


class Grabber(Protocol):
    def grab(self, left: int, top: int, width: int, height: int) -> np.ndarray: ...

    def close(self) -> None: ...


class MssGrabber:
    """Captura uma região da tela. Deve ser criado na thread que o utiliza, por exigência do MSS."""

    def __init__(self) -> None:
        self._sct = mss.MSS()

    def grab(self, left: int, top: int, width: int, height: int) -> np.ndarray:
        shot = self._sct.grab({"left": left, "top": top, "width": width, "height": height})
        return np.asarray(shot)[:, :, :3].copy()

    def close(self) -> None:
        self._sct.close()


FinderFactory = Callable[[CaptureConfig], WindowFinder]


def _default_finder(config: CaptureConfig) -> WindowFinder:
    return WindowFinder(config.process_names, config.window_titles)


class CaptureService:
    """Mantém o quadro mais recente do jogo; os demais módulos o consultam por snapshot()."""

    def __init__(
        self,
        config: CaptureConfig,
        events: EventManager,
        finder_factory: FinderFactory = _default_finder,
        grabber_factory: Callable[[], Grabber] = MssGrabber,
    ) -> None:
        self._config = config
        self._events = events
        self._finder_factory = finder_factory
        self._grabber_factory = grabber_factory
        self._lock = threading.Lock()
        self._snapshot = CaptureSnapshot()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> CaptureSnapshot:
        with self._lock:
            return self._snapshot

    def update_config(self, config: CaptureConfig) -> None:
        self._config = config

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.error("A thread de captura não encerrou em %.1fs", timeout)
        self._thread = None

    def _publish(self, **changes: object) -> None:
        with self._lock:
            previous = self._snapshot
            current = replace(previous, **changes)
            self._snapshot = current
        if current.state != previous.state:
            self._events.publish(
                EventType.CAPTURE_STATE_CHANGED, f"Captura: {previous.state.value} → {current.state.value}"
            )
        if previous.window is None and current.window is not None:
            info = current.window
            self._events.publish(EventType.FORTNITE_DETECTED, f"Fortnite detectado ({info.width}x{info.height})")
        elif previous.window is not None and current.window is None and current.state is not CaptureState.STOPPED:
            self._events.publish(EventType.FORTNITE_LOST, "Janela do Fortnite não encontrada")

    def _publish_without_frame(self, state: CaptureState, window: WindowInfo | None, error: str | None = None) -> None:
        self._publish(state=state, window=window, frame=None, is_black=False, fps=0.0, error=error)

    def _run(self) -> None:
        grabber: Grabber | None = None
        try:
            grabber = self._grabber_factory()
            self._loop(grabber)
        except Exception as exc:
            logger.exception("Falha fatal na thread de captura")
            self._events.publish(EventType.CAPTURE_ERROR, f"Falha fatal na captura: {exc}")
        finally:
            if grabber is not None:
                grabber.close()
            self._publish_without_frame(CaptureState.STOPPED, None)

    def _loop(self, grabber: Grabber) -> None:
        hwnd: int | None = None
        next_search = 0.0
        last_frame_at: float | None = None
        last_error: str | None = None
        self._publish_without_frame(CaptureState.SEARCHING, None)

        while not self._stop_event.is_set():
            config = self._config
            tick_started = time.monotonic()
            finder = self._finder_factory(config)

            window = finder.describe(hwnd) if hwnd is not None else None
            if window is None and tick_started >= next_search:
                window = finder.find()
                next_search = tick_started + config.window_poll_seconds
            hwnd = window.hwnd if window is not None else None

            if window is None:
                self._publish_without_frame(CaptureState.SEARCHING, None)
            elif window.is_minimized or window.width <= 0 or window.height <= 0:
                self._publish_without_frame(CaptureState.MINIMIZED, window)
            elif config.capture_only_when_foreground and not window.is_foreground:
                self._publish_without_frame(CaptureState.BACKGROUND, window)
            else:
                try:
                    frame = grabber.grab(window.left, window.top, window.width, window.height)
                except Exception as exc:
                    message = str(exc)
                    self._publish_without_frame(CaptureState.ERROR, window, message)
                    if message != last_error:
                        logger.warning("Falha ao capturar quadro", exc_info=True)
                        self._events.publish(EventType.CAPTURE_ERROR, f"Falha ao capturar: {message}")
                    last_error = message
                else:
                    last_error = None
                    frame.setflags(write=False)
                    now = time.monotonic()
                    fps = 1.0 / (now - last_frame_at) if last_frame_at is not None and now > last_frame_at else 0.0
                    last_frame_at = now
                    self._publish(
                        state=CaptureState.CAPTURING,
                        window=window,
                        frame=frame,
                        frame_id=self.snapshot().frame_id + 1,
                        captured_at=datetime.now(),
                        is_black=is_black_frame(frame, config.black_frame_threshold),
                        fps=fps,
                        error=None,
                    )
                    self._stop_event.wait(max(0.0, 1.0 / config.fps - (time.monotonic() - tick_started)))
                    continue

            # Sem quadro neste ciclo: a medida de FPS recomeça no próximo quadro capturado.
            last_frame_at = None
            self._stop_event.wait(0.25)
