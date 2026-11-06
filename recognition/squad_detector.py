"""Leitura do esquadrão no HUD a partir da cor e do formato das barras.

Nomes e skins mudam a cada partida, mas as barras seguem sempre o mesmo padrão. Elas são procuradas em uma
região ampla do canto inferior esquerdo, e o que as separa do cenário é a cor exata, a altura e o
alinhamento à esquerda entre si, não uma posição fixa no quadro.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from core.config import SquadConfig
from recognition.interface_detector import REFERENCE_HEIGHT, read_image

# Medidas em pixels de um quadro com 1080 de altura.
_ZONE = (0, 690, 440, 1045)  # esquerda, topo, direita, base
_ROW_BAR_WIDTH = 212
_HUD_BAR_WIDTH = 343
_ALIGNMENT_TOLERANCE = 5
_ROW_TOLERANCE = 4
_HEALTH_OFFSET = 8  # da barra de escudo até a de vida, dentro de uma linha
_CARD_ROW_OFFSET = 4  # do topo do ícone do cartão até o topo das barras da linha
_CARD_MIN_DISTANCE = 214  # do início das barras até o ícone do cartão
_CARD_MAX_DISTANCE = 234
_ROW_BOX = (-53, -24, 290, 48)  # avatar, nome, barras e ícones, em relação ao início das barras
_HUD_BOX = (-27, -30, 378, 74)
_HUD_NAME_BOX = (-27, -57, 240, 22)  # o nome fica acima das barras
_MIN_FILL = 0.6
_MIN_BAR_WIDTH = 14  # o losango colorido ao lado do nome é mais estreito que isso
_STRIP_KERNEL = np.ones((3, 5), np.uint8)
_HEALTH_MIN_HUE = 40  # abaixo disso é a ponta amarela da barra de vida

# A barra de um colega eliminado esvazia até o cartão de reinicialização expirar.
REBOOT_WINDOW_SECONDS = 120

# Faixas HSV estreitas de propósito: a grama tem saturação maior e brilho menor que a barra de vida, e
# paredes azuis têm saturação e brilho menores que as barras de escudo.
_HEALTH_COLORS = (((40, 150, 175), (74, 240, 255)), ((24, 170, 235), (38, 255, 255)))  # o segundo é a ponta amarela
_SHIELD_COLORS = (((88, 170, 190), (108, 255, 255)),)
_KNOCKED_COLORS = (((160, 170, 110), (179, 255, 255)), ((0, 170, 110), (4, 255, 255)))


class MemberStatus(str, Enum):
    ALIVE = "ALIVE"
    KNOCKED = "KNOCKED"
    ELIMINATED = "ELIMINATED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MemberReading:
    status: MemberStatus
    health: float | None = None
    shield: float | None = None
    reboot_seconds: int | None = None  # tempo restante para pegar o cartão de reinicialização
    card_collected: bool = False
    box: tuple[int, int, int, int] | None = None  # esquerda, topo, largura, altura no quadro
    picture_box: tuple[int, int, int, int] | None = None  # o que mostra quem é este integrante


@dataclass(frozen=True)
class SquadReading:
    rows: tuple[MemberReading, ...] = ()  # lista de colegas, de cima para baixo
    hud: MemberReading | None = None  # HUD principal: o próprio jogador ou o colega observado

    @property
    def visible(self) -> bool:
        return bool(self.rows) or self.hud is not None


@dataclass(frozen=True)
class _Strip:
    x: int
    y: int
    width: int
    height: int


def _find_strips(hsv: np.ndarray, colors: tuple, min_height: int, max_height: int) -> list[_Strip]:
    mask = np.zeros(hsv.shape[:2], np.uint8)
    for low, high in colors:
        mask |= cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _STRIP_KERNEL)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
    strips = []
    for index in range(1, count):
        x, y, width, height, area = stats[index]
        if min_height <= height <= max_height and width > height and area / (width * height) >= _MIN_FILL:
            strips.append(_Strip(int(x), int(y), int(width), int(height)))
    return strips


def _wide_enough(strips: list[_Strip]) -> list[_Strip]:
    return [strip for strip in strips if strip.width >= _MIN_BAR_WIDTH]


def _health_strips(hsv: np.ndarray, min_height: int, max_height: int) -> list[_Strip]:
    """Barra quase vazia é estreita como o losango do nome, mas só ela tem verde e ponta amarela juntos."""

    def is_bar(strip: _Strip) -> bool:
        if strip.width >= _MIN_BAR_WIDTH:
            return True
        hues = hsv[strip.y : strip.y + strip.height, strip.x : strip.x + strip.width, 0]
        fill_share = float(np.mean(hues >= _HEALTH_MIN_HUE))
        return 0.2 <= fill_share <= 0.85

    return [strip for strip in _find_strips(hsv, _HEALTH_COLORS, min_height, max_height) if is_bar(strip)]


def _common_left_edge(strips: list[_Strip]) -> int | None:
    """Retorna onde a maioria das barras começa; empates ficam com o grupo de barras mais largas."""
    if not strips:
        return None

    def aligned_with(strip: _Strip) -> list[_Strip]:
        return [other for other in strips if abs(other.x - strip.x) <= _ALIGNMENT_TOLERANCE]

    def support(strip: _Strip) -> tuple[int, int]:
        group = aligned_with(strip)
        return len(group), sum(other.width for other in group)

    return min(other.x for other in aligned_with(max(strips, key=support)))


def _fraction(strip: _Strip, x0: int, full_width: int, extra: int = 0) -> float:
    """Mede a partir do início comum: os primeiros pixels de uma barra podem não atingir a faixa de cor."""
    return min(1.0, (strip.x + strip.width + extra - x0) / full_width)


class SquadDetector:
    def __init__(self, config: SquadConfig, templates_dir: Path) -> None:
        self._card_threshold = config.card_threshold
        self._card = read_image(templates_dir / "squad" / "cartao_coletado.png", cv2.IMREAD_GRAYSCALE)
        self.warnings: list[str] = []
        if self._card is None:
            self.warnings.append("Recorte do cartão não encontrado: colegas com o cartão coletado contam como fora da partida.")

    def read(self, frame: np.ndarray) -> SquadReading:
        scale = frame.shape[0] / REFERENCE_HEIGHT
        left, top, right, bottom = (round(value * scale) for value in _ZONE)
        zone = frame[top:bottom, left : min(right, frame.shape[1])]
        if abs(scale - 1.0) > 0.002:
            zone = cv2.resize(zone, None, fx=1 / scale, fy=1 / scale, interpolation=cv2.INTER_LINEAR)
        hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)

        health = _health_strips(hsv, 7, 14)
        knocked = _wide_enough(_find_strips(hsv, _KNOCKED_COLORS, 7, 14))
        shields = _wide_enough(_find_strips(hsv, _SHIELD_COLORS, 4, 9))
        timers = _wide_enough(_find_strips(hsv, _SHIELD_COLORS, 14, 21))
        hud_health = _health_strips(hsv, 30, 46)
        hud_knocked = _wide_enough(_find_strips(hsv, _KNOCKED_COLORS, 30, 46))
        hud_shields = _wide_enough(_find_strips(hsv, _SHIELD_COLORS, 22, 30))

        all_strips = health + knocked + shields + timers + hud_health + hud_knocked + hud_shields
        x0 = _common_left_edge(all_strips)
        if x0 is None:
            return self._without_bars(zone, scale, top)

        def aligned(strips: list[_Strip]) -> list[_Strip]:
            return [strip for strip in strips if 0 <= strip.x - x0 <= 2 * _ALIGNMENT_TOLERANCE]

        def to_frame(box: tuple[int, int, int, int], bar_top: int) -> tuple[int, int, int, int]:
            box_left, box_top, width, height = box
            return (
                max(0, round((x0 + box_left) * scale)),
                round((bar_top + box_top) * scale) + top,
                round(width * scale),
                round(height * scale),
            )

        rows = self._assemble_rows(
            aligned(health), aligned(knocked), aligned(shields), aligned(timers), self._find_cards(zone, x0)
        )
        readings = tuple(
            self._read_row(parts, x0, to_frame(_ROW_BOX, row_top)) for row_top, parts in sorted(rows.items())
        )
        hud = self._read_hud(aligned(hud_health), aligned(hud_knocked), aligned(hud_shields), x0, to_frame)
        return SquadReading(readings, hud)

    def _without_bars(self, zone: np.ndarray, scale: float, top: int) -> SquadReading:
        """Um colega eliminado com o cartão já coletado não tem barras, só o ícone do cartão."""
        cards = self._find_cards(zone, None)

        def card_box(card_top: int, card_x: int) -> tuple[int, int, int, int]:
            return (
                max(0, round((card_x - _CARD_MIN_DISTANCE - 6 + _ROW_BOX[0]) * scale)),
                round((card_top + _ROW_BOX[1]) * scale) + top,
                round(_ROW_BOX[2] * scale),
                round(_ROW_BOX[3] * scale),
            )

        rows = tuple(
            MemberReading(
                MemberStatus.ELIMINATED,
                card_collected=True,
                box=card_box(card_top, card_x),
                picture_box=card_box(card_top, card_x),
            )
            for card_top, card_x in sorted(cards.items())
        )
        return SquadReading(rows)

    def _find_cards(self, zone: np.ndarray, x0: int | None) -> dict[int, int]:
        """Localiza ícones de cartão coletado; retorna {topo da linha: posição horizontal do ícone}."""
        if self._card is None:
            return {}
        gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(gray, self._card, cv2.TM_CCOEFF_NORMED)
        np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        cards: dict[int, tuple[float, int]] = {}
        for y, x in zip(*np.nonzero(result >= self._card_threshold)):
            if x0 is not None and not _CARD_MIN_DISTANCE <= x - x0 <= _CARD_MAX_DISTANCE:
                continue
            row_top = int(y) + _CARD_ROW_OFFSET
            score = float(result[y, x])
            nearby = next((key for key in cards if abs(key - row_top) <= self._card.shape[0]), None)
            if nearby is None:
                cards[row_top] = (score, int(x))
            elif score > cards[nearby][0]:
                del cards[nearby]
                cards[row_top] = (score, int(x))
        return {row_top: x for row_top, (_score, x) in cards.items()}

    @staticmethod
    def _assemble_rows(
        health: list[_Strip],
        knocked: list[_Strip],
        shields: list[_Strip],
        timers: list[_Strip],
        cards: dict[int, int],
    ) -> dict[int, dict[str, _Strip | bool]]:
        rows: dict[int, dict[str, _Strip | bool]] = {}

        def add(row_top: int, kind: str, value: _Strip | bool) -> None:
            key = next((existing for existing in rows if abs(existing - row_top) <= _ROW_TOLERANCE), row_top)
            rows.setdefault(key, {})[kind] = value

        for strip in timers:
            add(strip.y, "timer", strip)
        for strip in shields:
            add(strip.y, "shield", strip)
        for strip in health:
            add(strip.y - _HEALTH_OFFSET, "health", strip)
        for strip in knocked:
            add(strip.y - _HEALTH_OFFSET, "knocked", strip)
        for row_top in cards:
            add(row_top, "card", True)
        return rows

    @staticmethod
    def _read_row(parts: dict[str, _Strip | bool], x0: int, box: tuple[int, int, int, int]) -> MemberReading:
        if "timer" in parts:
            remaining = _fraction(parts["timer"], x0, _ROW_BAR_WIDTH)
            seconds = int(remaining * REBOOT_WINDOW_SECONDS)
            return MemberReading(MemberStatus.ELIMINATED, reboot_seconds=seconds, box=box, picture_box=box)
        if "knocked" in parts:
            health = _fraction(parts["knocked"], x0, _ROW_BAR_WIDTH)
            return MemberReading(MemberStatus.KNOCKED, health=health, box=box, picture_box=box)
        if "health" in parts or "shield" in parts:
            health = _fraction(parts["health"], x0, _ROW_BAR_WIDTH) if "health" in parts else None
            # A ponta branca da barra de escudo não entra na faixa de cor.
            shield = _fraction(parts["shield"], x0, _ROW_BAR_WIDTH, extra=3) if "shield" in parts else 0.0
            return MemberReading(MemberStatus.ALIVE, health=health, shield=shield, box=box, picture_box=box)
        return MemberReading(MemberStatus.ELIMINATED, card_collected=True, box=box, picture_box=box)

    @staticmethod
    def _read_hud(
        health: list[_Strip], knocked: list[_Strip], shields: list[_Strip], x0: int, to_frame
    ) -> MemberReading | None:
        if knocked:
            bar = knocked[0]
            return MemberReading(
                MemberStatus.KNOCKED,
                _fraction(bar, x0, _HUD_BAR_WIDTH),
                0.0,
                box=to_frame(_HUD_BOX, bar.y),
                picture_box=to_frame(_HUD_NAME_BOX, bar.y),
            )
        if health:
            bar = health[0]
            shield = _fraction(shields[0], x0, _HUD_BAR_WIDTH, extra=3) if shields else 0.0
            return MemberReading(
                MemberStatus.ALIVE,
                _fraction(bar, x0, _HUD_BAR_WIDTH),
                shield,
                box=to_frame(_HUD_BOX, bar.y),
                picture_box=to_frame(_HUD_NAME_BOX, bar.y),
            )
        return None
