"""Ponto de entrada do AutomaizerOnePiece."""

from __future__ import annotations

import logging

from core.application import Application
from core.capture import enable_dpi_awareness
from ui.app_window import AppWindow


def main() -> None:
    enable_dpi_awareness()
    application = Application()
    try:
        window = AppWindow(application)
        application.start()
        window.mainloop()
    except Exception:
        logging.getLogger(__name__).exception("Erro não tratado na interface")
        raise
    finally:
        application.shutdown()


if __name__ == "__main__":
    main()
