"""Resolução de caminhos, tanto em desenvolvimento quanto no executável PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path

_IS_FROZEN = getattr(sys, "frozen", False)

# Arquivos graváveis ficam ao lado do executável; recursos empacotados ficam em _MEIPASS.
APP_DIR = Path(sys.executable).resolve().parent if _IS_FROZEN else Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))

CONFIG_PATH = APP_DIR / "config.json"
LOGS_DIR = APP_DIR / "logs"
DATA_DIR = APP_DIR / "data"
FEEDBACK_DIR = DATA_DIR / "feedback"
REWARDS_DIR = DATA_DIR / "rewards"
DATABASE_PATH = DATA_DIR / "monitor.db"
NOTIFICATIONS_PATH = DATA_DIR / "notifications.jsonl"
ASSETS_DIR = RESOURCE_DIR / "assets"
TEMPLATES_DIR = ASSETS_DIR / "templates"
