"""Notificação do Windows com som, para avisar quem não está olhando para o jogo."""

from __future__ import annotations

import ctypes
import logging
import winsound
from ctypes import wintypes

logger = logging.getLogger(__name__)

_NIM_ADD = 0
_NIM_MODIFY = 1
_NIM_DELETE = 2
_NIF_ICON = 0x02
_NIF_TIP = 0x04
_NIF_INFO = 0x10
_NIIF_INFO = 0x01
_NIIF_NOSOUND = 0x10
_IDI_APPLICATION = 32512
_ICON_ID = 1  # fixo: o Windows cria um registro novo para cada identificador diferente
_TITLE_LENGTH = 64
_MESSAGE_LENGTH = 256


class _NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * _MESSAGE_LENGTH),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * _TITLE_LENGTH),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NotifyIconData)]
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
_user32.LoadIconW.restype = wintypes.HICON


class WindowsNotifier:
    """Deve ser usado na thread da interface, dona da janela informada."""

    def __init__(self, hwnd: int, title: str) -> None:
        self._title = title[: _TITLE_LENGTH - 1]
        self._data = _NotifyIconData()
        self._data.cbSize = ctypes.sizeof(_NotifyIconData)
        self._data.hWnd = hwnd
        self._data.uID = _ICON_ID
        self._data.uFlags = _NIF_ICON | _NIF_TIP
        self._data.hIcon = _user32.LoadIconW(None, ctypes.cast(_IDI_APPLICATION, wintypes.LPCWSTR))
        self._data.szTip = self._title
        self._added = bool(_shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(self._data)))
        if not self._added:
            logger.warning("Não foi possível registrar o ícone de notificação")

    def notify(self, message: str) -> bool:
        self._play_sound()
        if not self._added:
            return False
        self._data.uFlags = _NIF_INFO
        self._data.dwInfoFlags = _NIIF_INFO | _NIIF_NOSOUND  # o som já foi tocado, e não depende do modo de jogo
        self._data.szInfoTitle = self._title
        self._data.szInfo = message[: _MESSAGE_LENGTH - 1]
        shown = bool(_shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(self._data)))
        if not shown:
            logger.warning("Não foi possível mostrar a notificação")
        return shown

    def close(self) -> None:
        if self._added:
            _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self._data))
            self._added = False

    @staticmethod
    def _play_sound() -> None:
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except RuntimeError:
            logger.warning("Não foi possível tocar o som do aviso", exc_info=True)
