"""Únicas ações do aplicativo fora dele: levar o cursor até um botão e apertar Esc.

Este módulo não tem função de clique, de propósito.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from core.capture import is_window_foreground

logger = logging.getLogger(__name__)

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_VK_ESCAPE = 0x1B
_SCAN_ESCAPE = 0x01
_KEY_HOLD_SECONDS = 0.04


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputUnion(ctypes.Union):
    # A união precisa incluir a entrada de mouse para que a estrutura tenha o tamanho que o Windows espera.
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT


class CursorCornerStop(Exception):
    """O PyAutoGUI recusou o movimento com o mouse em um canto da tela: é o gesto de emergência dele."""


def _send_key(flags: int) -> bool:
    event = _Input(_INPUT_KEYBOARD)
    event.union.ki = _KeyboardInput(_VK_ESCAPE, _SCAN_ESCAPE, flags, 0, 0)
    return _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1


class MouseGuidance:
    def move_to(self, x: int, y: int, duration: float) -> None:
        import pyautogui  # só carregado se o assistente for usado

        pyautogui.PAUSE = 0
        try:
            pyautogui.moveTo(x, y, duration=duration)
        except pyautogui.FailSafeException as exc:
            raise CursorCornerStop from exc

    def press_escape(self, hwnd: int) -> bool:
        """Aperta Esc se o jogo estiver em primeiro plano; em outra janela, a tecla iria para o lugar errado."""
        if not is_window_foreground(hwnd):
            logger.info("Esc não enviado: o jogo não está em primeiro plano")
            return False
        pressed = _send_key(0)
        time.sleep(_KEY_HOLD_SECONDS)
        released = _send_key(_KEYEVENTF_KEYUP)
        logger.info("Esc enviado ao jogo (%s)", "ok" if pressed and released else "falhou")
        return pressed and released
