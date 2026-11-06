"""Avisos sobre o jogo: não recebem cliques, não tiram o foco e ficam fora da captura de tela do aplicativo."""

from __future__ import annotations

import ctypes
import tkinter as tk
from ctypes import wintypes

import mss

from assistant.navigation_controller import GuidanceHint

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_LWA_ALPHA = 0x02
_OPACITY = 240
_MARGIN = 28
_HIDDEN_POSITION = -20000
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_WDA_EXCLUDEFROMCAPTURE = 0x00000011
_ARROWS = {"left": "{text}  ▶", "right": "◀  {text}", "below": "▲  {text}"}
# O cartão fica à direita, abaixo do minimapa, onde não cobre os elementos que o jogador mais usa.
_CARD_TOP = 300
_CARD_RIGHT_MARGIN = 30

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL


def hint_position(hint: GuidanceHint, width: int, height: int) -> tuple[int, int]:
    """Calcula o canto do aviso para que ele fique ao lado do botão, sem cobri-lo."""
    if hint.side == "left":
        return hint.x - width - 3 * _MARGIN, hint.y - height // 2
    if hint.side == "right":
        return hint.x + 3 * _MARGIN, hint.y - height // 2
    return hint.x - width // 2, hint.y + _MARGIN


class _FloatingWindow:
    """Janela sempre visível que o usuário atravessa com o mouse e que o Windows nunca ativa."""

    def __init__(self, master: tk.Misc) -> None:
        self.window = tk.Toplevel(master)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.geometry(f"+{_HIDDEN_POSITION}+{_HIDDEN_POSITION}")

    def finish_setup(self) -> None:
        """Deve ser chamado depois de colocar o conteúdo na janela."""
        self.window.deiconify()
        self.window.update_idletasks()
        self._hwnd = _user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
        style = _user32.GetWindowLongPtrW(self._hwnd, _GWL_EXSTYLE)
        _user32.SetWindowLongPtrW(
            self._hwnd, _GWL_EXSTYLE, style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW
        )
        _user32.SetLayeredWindowAttributes(self._hwnd, 0, _OPACITY, _LWA_ALPHA)
        # A captura do aplicativo não pode ver o próprio aviso: ele cobriria os recortes que identificam a tela.
        _user32.SetWindowDisplayAffinity(self._hwnd, _WDA_EXCLUDEFROMCAPTURE)

    def place(self, x: int, y: int) -> None:
        # Direto pelo Windows: o geometry do Tk não reposiciona esta janela e poderia ativá-la, tirando o foco do jogo.
        _user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, x, y, 0, 0, _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW)

    def hide(self) -> None:
        self.place(_HIDDEN_POSITION, _HIDDEN_POSITION)


class GuidanceOverlay(_FloatingWindow):
    """Aviso ao lado do botão que deve ser clicado."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self._label = tk.Label(
            self.window, text="", font=("Segoe UI", 15, "bold"), bg="#F2B632", fg="#0B1B2B", padx=16, pady=9
        )
        self._label.pack()
        self.finish_setup()
        self._shown: GuidanceHint | None = None

    def show(self, hint: GuidanceHint | None) -> None:
        if hint == self._shown:
            return
        self._shown = hint
        if hint is None:
            self.hide()
            return
        self._label.configure(text=_ARROWS[hint.side].format(text=hint.text))
        self.window.update_idletasks()
        self.place(*hint_position(hint, self.window.winfo_reqwidth(), self.window.winfo_reqheight()))


class _NotificationCard(_FloatingWindow):
    def __init__(self, master: tk.Misc, monitor: dict[str, int]) -> None:
        super().__init__(master)
        self._monitor = monitor
        frame = tk.Frame(self.window, bg="#0B1B2B", highlightbackground="#F2B632", highlightthickness=3)
        frame.pack()
        tk.Label(
            frame, text="AutomaizerOnePiece", font=("Segoe UI", 10, "bold"), bg="#0B1B2B", fg="#F2B632", anchor="w", padx=16
        ).pack(fill="x", pady=(8, 0))
        self._message = tk.Label(
            frame, text="", font=("Segoe UI", 15, "bold"), bg="#0B1B2B", fg="#FFFFFF", anchor="w", padx=16, wraplength=420, justify="left"
        )
        self._message.pack(fill="x", pady=(0, 10))
        self.finish_setup()

    def show(self, message: str) -> None:
        self._message.configure(text=message)
        self.window.update_idletasks()
        x = self._monitor["left"] + self._monitor["width"] - self.window.winfo_reqwidth() - _CARD_RIGHT_MARGIN
        self.place(x, self._monitor["top"] + _CARD_TOP)


class NotificationCards:
    """Notificação do próprio aplicativo, uma em cada monitor.

    O Windows segura as notificações dele enquanto um jogo ocupa a tela, então esta é a garantia de ver o aviso.
    """

    def __init__(self, master: tk.Misc, seconds: float) -> None:
        self._master = master
        self.seconds = seconds
        with mss.MSS() as screen:
            monitors = screen.monitors[1:]
        self._cards = [_NotificationCard(master, monitor) for monitor in monitors]
        self._hide_job: str | None = None

    def show(self, message: str) -> None:
        for card in self._cards:
            card.show(message)
        if self._hide_job is not None:
            self._master.after_cancel(self._hide_job)
        self._hide_job = self._master.after(round(self.seconds * 1000), self._hide)

    def close(self) -> None:
        if self._hide_job is not None:
            self._master.after_cancel(self._hide_job)
            self._hide_job = None

    def _hide(self) -> None:
        self._hide_job = None
        for card in self._cards:
            card.hide()
