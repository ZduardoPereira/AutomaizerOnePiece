"""Leitura dos números do HUD: abates, assistências, jogadores restantes e a missão de baús.

Os números ficam ao lado de ícones já localizados pelo reconhecimento da partida, e cada dígito é comparado
com modelos tirados do jogo. Os modelos com canal alfa guardam só o desenho e o contorno escuro do caractere,
então o cenário atrás deles não interfere. Uma leitura só é aceita depois de se repetir, pois um objeto passando
atrás do número pode confundir um quadro isolado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.state_machine import GameState
from recognition.interface_detector import REFERENCE_HEIGHT, Detection, read_image

_SKULL = "hud_caveira"
_PERSON = "hud_pessoa"
_MIN_ICON_SCORE = 0.8
# Medidas em pixels de um quadro com 1080 de altura.
_ROW_LEFT_SPAN = 84  # dos números até o ícone, à esquerda dele
_ROW_ABOVE = 2
_ROW_BELOW = 36
_ICON_GAP = 2
_ASSIST_ROW_OFFSET = 38  # da caveira até a linha de assistências, abaixo dela
_MIN_GAP_WITH_ASSISTS = 60  # sem a linha de assistências, como no modo solo, a pessoa fica logo abaixo da caveira
_QUEST_SEARCH = (0, 0, 640, 320)  # esquerda, topo, direita, base
_QUEST_ANCHOR_SCORE = 0.65
_QUEST_NEAR_X = 8  # a largura do número desloca o texto da missão
_QUEST_NEAR_Y = 3
_QUEST_SEARCH_EVERY = 4  # procurar em toda a região é o passo caro, e a missão pode nem estar na tela
_QUEST_LEFT_SPAN = 120
_QUEST_ROW_HEIGHT = 29
_OVERLAP_LIMIT = 0.45
_COUNTER_THRESHOLD = 0.75
_QUEST_THRESHOLD = 0.70
_COUNTER_MAX_GAP = 8
_QUEST_MAX_GAP = 6
_COUNTER_MAX_RIGHT_MARGIN = 16
_QUEST_PATTERN = re.compile(r"^(\d+)/(\d+)$")
# Mudanças improváveis só são aceitas após muitas repetições: costumam ser erro de leitura.
_MAX_NORMAL_JUMP = 10
_LONG_CONFIRM_FRAMES = 6
_CHARACTER_FILES = {"barra": "/"}
_GLYPH_LEVEL = 128


def _read_shape(path: Path) -> tuple[np.ndarray, np.ndarray | None] | None:
    image = read_image(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    if image.ndim == 2:
        return image, None
    if image.shape[2] == 4:
        return image[:, :, 0], image[:, :, 3]
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), None


def _correlate(gray: np.ndarray, image: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    result = cv2.matchTemplate(gray, image, cv2.TM_CCOEFF_NORMED, mask=mask)
    # Regiões uniformes geram divisão por zero, e com máscara o resultado pode sair do intervalo.
    np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
    result[result > 1.0] = 0.0
    return result


@dataclass(frozen=True)
class _Glyph:
    character: str
    image: np.ndarray
    mask: np.ndarray | None
    left: int  # onde o desenho começa e termina, sem a margem do contorno
    right: int


def _load_glyph(path: Path) -> _Glyph | None:
    shape = _read_shape(path)
    if shape is None:
        return None
    image, mask = shape
    left, right = 0, image.shape[1]
    if mask is not None:
        columns = np.nonzero(((image >= _GLYPH_LEVEL) & (mask > 0)).any(axis=0))[0]
        if columns.size == 0:
            return None
        left, right = int(columns[0]), int(columns[-1]) + 1
    return _Glyph(_CHARACTER_FILES.get(path.stem, path.stem), image, mask, left, right)


class DigitReader:
    def __init__(self, folder: Path, threshold: float, max_gap: int) -> None:
        self._threshold = threshold
        self._max_gap = max_gap
        glyphs = (_load_glyph(path) for path in sorted(folder.glob("*.png")))
        self._glyphs = [glyph for glyph in glyphs if glyph is not None]

    @property
    def available(self) -> bool:
        return bool(self._glyphs)

    def read(self, gray: np.ndarray) -> tuple[str, int]:
        """Retorna o texto lido e a posição horizontal onde ele termina."""
        candidates = []
        for glyph in self._glyphs:
            if glyph.image.shape[0] > gray.shape[0] or glyph.image.shape[1] > gray.shape[1]:
                continue
            result = _correlate(gray, glyph.image, glyph.mask)
            for y, x in zip(*np.nonzero(result >= self._threshold)):
                candidates.append((float(result[y, x]), int(x) + glyph.left, int(x) + glyph.right, glyph.character))

        accepted: list[tuple[float, int, int, str]] = []
        for candidate in sorted(candidates, reverse=True):
            _, x0, x1, _ = candidate
            if not any(min(x1, b1) - max(x0, b0) > _OVERLAP_LIMIT * min(x1 - x0, b1 - b0) for _, b0, b1, _ in accepted):
                accepted.append(candidate)
        if not accepted:
            return "", 0

        # Só vale o que está colado: um desenho do cenário mais à esquerda não faz parte do número.
        accepted.sort(key=lambda item: item[1], reverse=True)
        chain = [accepted[0]]
        for item in accepted[1:]:
            if chain[-1][1] - item[2] > self._max_gap:
                break
            chain.append(item)
        return "".join(item[3] for item in reversed(chain)), chain[0][2]


@dataclass(frozen=True)
class HudNumbers:
    kills: int | None = None
    assists: int | None = None
    players_left: int | None = None
    chest_progress: int | None = None


class HudReader:
    def __init__(self, templates_dir: Path) -> None:
        folder = templates_dir / "hud"
        self._digits = DigitReader(folder / "digitos", _COUNTER_THRESHOLD, _COUNTER_MAX_GAP)
        self._quest_digits = DigitReader(folder / "digitos_missao", _QUEST_THRESHOLD, _QUEST_MAX_GAP)
        self._anchor = _read_shape(folder / "missao_baus.png")
        self._anchor_position: tuple[int, int] | None = None
        self._frames_without_search = _QUEST_SEARCH_EVERY
        self.warnings: list[str] = []
        if not self._digits.available:
            self.warnings.append(f"Abates e jogadores restantes não serão lidos: faltam dígitos em {folder}.")
        if self._anchor is None or not self._quest_digits.available:
            self.warnings.append(f"Os baús não serão contados: faltam recortes da missão em {folder}.")

    def read(self, frame: np.ndarray, detection: Detection) -> HudNumbers:
        scale = frame.shape[0] / REFERENCE_HEIGHT
        result = detection.screens.get(GameState.IN_MATCH)
        icons = {match.name: match for match in result.matches} if result is not None else {}
        person = self._usable(icons.get(_PERSON))
        players_left = self._read_counter(frame, scale, person.left, person.top) if person is not None else None
        # Eliminado, o HUD pode mostrar os números do colega observado; só o total de jogadores continua valendo.
        if detection.state is GameState.ELIMINATED:
            return HudNumbers(players_left=players_left)

        skull = self._usable(icons.get(_SKULL))
        kills = assists = None
        if skull is not None:
            kills = self._read_counter(frame, scale, skull.left, skull.top)
            if person is not None and person.top - skull.top >= _MIN_GAP_WITH_ASSISTS * scale:
                assists = self._read_counter(frame, scale, skull.left, skull.top + round(_ASSIST_ROW_OFFSET * scale))
        return HudNumbers(kills, assists, players_left, self._read_chest_progress(frame, scale))

    @staticmethod
    def _usable(match):
        return match if match is not None and match.score >= _MIN_ICON_SCORE else None

    @staticmethod
    def _crop(frame: np.ndarray, scale: float, left: float, top: float, right: float, bottom: float) -> np.ndarray:
        """Recorta em pixels de 1080p e devolve em tons de cinza nessa mesma escala."""
        y0, y1 = max(0, round(top * scale)), round(bottom * scale)
        x0, x1 = max(0, round(left * scale)), round(right * scale)
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if abs(scale - 1.0) > 0.002 and gray.size:
            interpolation = cv2.INTER_AREA if scale > 1 else cv2.INTER_CUBIC
            gray = cv2.resize(gray, None, fx=1 / scale, fy=1 / scale, interpolation=interpolation)
        return gray

    def _read_counter(self, frame: np.ndarray, scale: float, icon_left: int, icon_top: int) -> int | None:
        left = icon_left / scale
        top = icon_top / scale
        gray = self._crop(frame, scale, left - _ROW_LEFT_SPAN, top - _ROW_ABOVE, left - _ICON_GAP, top + _ROW_BELOW)
        if gray.size == 0:
            return None
        text, end = self._digits.read(gray)
        if not text.isdigit() or gray.shape[1] - end > _COUNTER_MAX_RIGHT_MARGIN:
            return None
        return int(text)

    def _read_chest_progress(self, frame: np.ndarray, scale: float) -> int | None:
        if self._anchor is None:
            return None
        left, top, right, bottom = _QUEST_SEARCH
        area = self._crop(frame, scale, left, top, right, bottom)
        position = self._find_anchor(area)
        if position is None:
            return None

        x, y = position
        row = area[y : y + _QUEST_ROW_HEIGHT, max(0, x - _QUEST_LEFT_SPAN) : x]
        if row.size == 0:
            return None
        match = _QUEST_PATTERN.match(self._quest_digits.read(row)[0])
        if match is None or int(match.group(1)) > int(match.group(2)):
            return None
        return int(match.group(1))

    def _find_anchor(self, area: np.ndarray) -> tuple[int, int] | None:
        if self._anchor_position is not None:
            self._anchor_position = self._search_near(area, self._anchor_position)
        if self._anchor_position is None:
            self._frames_without_search += 1
            if self._frames_without_search >= _QUEST_SEARCH_EVERY:
                self._frames_without_search = 0
                self._anchor_position = self._search(area, 0, 0)
        return self._anchor_position

    def _search_near(self, area: np.ndarray, position: tuple[int, int]) -> tuple[int, int] | None:
        x, y = position
        height, width = self._anchor[0].shape
        x0, y0 = max(0, x - _QUEST_NEAR_X), max(0, y - _QUEST_NEAR_Y)
        return self._search(area[y0 : y + height + _QUEST_NEAR_Y, x0 : x + width + _QUEST_NEAR_X], x0, y0)

    def _search(self, area: np.ndarray, offset_x: int, offset_y: int) -> tuple[int, int] | None:
        image, mask = self._anchor
        if image.shape[0] > area.shape[0] or image.shape[1] > area.shape[1]:
            return None
        _, score, _, (x, y) = cv2.minMaxLoc(_correlate(area, image, mask))
        return (x + offset_x, y + offset_y) if score >= _QUEST_ANCHOR_SCORE else None


class _ConfirmedValue:
    """Aceita um valor quando ele se repete. Uma mudança improvável precisa se repetir por mais tempo."""

    def __init__(self, confirm_frames: int, only_grows: bool) -> None:
        self._confirm_frames = confirm_frames
        self._only_grows = only_grows
        self.value: int | None = None
        self._pending: int | None = None
        self._count = 0

    def observe(self, reading: int | None) -> None:
        if reading is None or reading == self.value:
            self._pending = None
            self._count = 0
            return
        self._count = self._count + 1 if reading == self._pending else 1
        self._pending = reading
        if self._count >= (_LONG_CONFIRM_FRAMES if self._is_unlikely(reading) else self._confirm_frames):
            self.value = reading
            self._pending = None
            self._count = 0

    def _is_unlikely(self, reading: int) -> bool:
        if self.value is None:
            return False
        return abs(reading - self.value) > _MAX_NORMAL_JUMP or (self._only_grows and reading < self.value)


@dataclass(frozen=True)
class MatchNumbers:
    kills: int = 0
    assists: int = 0
    players_left: int | None = None
    chests_opened: int | None = None  # None enquanto a missão de baús não aparece na tela


class HudTracker:
    def __init__(self, confirm_frames: int = 2) -> None:
        self._confirm_frames = confirm_frames
        self.reset()

    def reset(self) -> None:
        self._kills = _ConfirmedValue(self._confirm_frames, only_grows=True)
        self._assists = _ConfirmedValue(self._confirm_frames, only_grows=True)
        self._players_left = _ConfirmedValue(self._confirm_frames, only_grows=False)
        self._chests = _ConfirmedValue(self._confirm_frames, only_grows=True)
        self._chests_at_start: int | None = None

    @property
    def numbers(self) -> MatchNumbers:
        chests = self._chests.value
        opened = chests - self._chests_at_start if chests is not None and self._chests_at_start is not None else None
        return MatchNumbers(self._kills.value or 0, self._assists.value or 0, self._players_left.value, opened)

    def observe(self, reading: HudNumbers) -> None:
        self._kills.observe(reading.kills)
        self._assists.observe(reading.assists)
        self._players_left.observe(reading.players_left)
        self._observe_chests(reading.chest_progress)

    def _observe_chests(self, progress: int | None) -> None:
        before = self._chests.value
        self._chests.observe(progress)
        after = self._chests.value
        if after is None or after == before:
            return
        if self._chests_at_start is None or after < self._chests_at_start:
            self._chests_at_start = after
        elif before is not None and after - before > _MAX_NORMAL_JUMP:
            # Um salto desses não são baús, e sim a leitura anterior que estava errada: a contagem continua daqui.
            self._chests_at_start += after - before
