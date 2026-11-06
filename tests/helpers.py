"""Imagens sintéticas para testar o reconhecimento sem depender de prints do jogo."""

from pathlib import Path

import cv2
import numpy as np

from core.config import RecognitionConfig, ScreenRule
from core.state_machine import GameState


def pattern(seed: int, width: int, height: int) -> np.ndarray:
    """Textura aleatória com detalhes grandes o bastante para sobreviver à redução do quadro."""
    generator = np.random.default_rng(seed)
    coarse = generator.integers(0, 255, (height // 8 + 1, width // 8 + 1), dtype=np.uint8)
    return cv2.resize(coarse, (width, height), interpolation=cv2.INTER_NEAREST)


def blank_frame(width: int = 1920, height: int = 1080) -> np.ndarray:
    return np.full((height, width, 3), 90, dtype=np.uint8)


def paste(frame: np.ndarray, gray: np.ndarray, left: int, top: int) -> np.ndarray:
    frame[top : top + gray.shape[0], left : left + gray.shape[1]] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return frame


def write_template(directory: Path, folder: str, name: str, gray: np.ndarray) -> None:
    (directory / folder).mkdir(parents=True, exist_ok=True)
    ok, buffer = cv2.imencode(".png", gray)
    assert ok
    buffer.tofile(directory / folder / f"{name}.png")


def recognition_config(**rules: dict) -> RecognitionConfig:
    """Cria a configuração a partir de {nome do estado: campos da regra}, na ordem de prioridade."""
    return RecognitionConfig(screens={GameState[name]: ScreenRule(**fields) for name, fields in rules.items()})


def _bgr(hue: int, saturation: int, value: int) -> tuple[int, int, int]:
    pixel = np.uint8([[[hue, saturation, value]]])
    return tuple(int(channel) for channel in cv2.cvtColor(pixel, cv2.COLOR_HSV2BGR)[0, 0])


# Cores medidas no jogo, em HSV do OpenCV.
HEALTH = _bgr(55, 188, 205)
HEALTH_TIP = _bgr(30, 230, 255)
SHIELD = _bgr(100, 250, 235)
TIMER = _bgr(98, 255, 255)
KNOCKED = _bgr(172, 220, 235)
GRASS = _bgr(38, 250, 165)
BLUE_WALL = _bgr(106, 160, 160)

BAR_LEFT = 67
ROW_TOPS = (776, 832, 888)
ROW_BAR_WIDTH = 212
HUD_BAR_WIDTH = 343
HUD_HEALTH_TOP = 984
HUD_SHIELD_TOP = 956


def filled_frame(color: tuple[int, int, int], width: int = 1920, height: int = 1080) -> np.ndarray:
    return np.full((height, width, 3), color, dtype=np.uint8)


def _bar(frame: np.ndarray, top: int, height: int, width: int, color: tuple[int, int, int]) -> None:
    frame[top : top + height, BAR_LEFT : BAR_LEFT + width] = color


def _health_bar(frame: np.ndarray, top: int, height: int, full_width: int, fraction: float) -> None:
    width = round(full_width * fraction)
    tip = min(4, width)
    _bar(frame, top, height, width - tip, HEALTH)
    frame[top : top + height, BAR_LEFT + width - tip : BAR_LEFT + width] = HEALTH_TIP


def draw_alive_row(frame: np.ndarray, top: int, health: float = 1.0, shield: float = 0.0) -> None:
    if shield > 0:
        _bar(frame, top, 6, round(ROW_BAR_WIDTH * shield) - 3, SHIELD)
    _health_bar(frame, top + 8, 10, ROW_BAR_WIDTH, health)


def draw_knocked_row(frame: np.ndarray, top: int, health: float = 1.0) -> None:
    _bar(frame, top + 8, 10, round(ROW_BAR_WIDTH * health), KNOCKED)


def draw_timer_row(frame: np.ndarray, top: int, seconds: int) -> None:
    _bar(frame, top, 18, round(ROW_BAR_WIDTH * seconds / 120), TIMER)


def draw_name_diamond(frame: np.ndarray, top: int, color: tuple[int, int, int]) -> None:
    frame[top - 20 : top - 10, BAR_LEFT : BAR_LEFT + 10] = color


def draw_hud(frame: np.ndarray, health: float = 1.0, shield: float = 0.0, knocked: bool = False) -> None:
    if knocked:
        _bar(frame, HUD_HEALTH_TOP, 40, round(HUD_BAR_WIDTH * health), KNOCKED)
        return
    if shield > 0:
        _bar(frame, HUD_SHIELD_TOP, 24, round(HUD_BAR_WIDTH * shield) - 3, SHIELD)
    _health_bar(frame, HUD_HEALTH_TOP, 40, HUD_BAR_WIDTH, health)
