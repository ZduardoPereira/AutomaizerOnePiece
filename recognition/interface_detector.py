"""Reconhecimento de telas por comparação visual com recortes de referência (templates).

Cada recorte é procurado no quadro inteiro, de modo que a posição dos elementos não precisa ser
conhecida. O quadro é reduzido a uma altura fixa antes da comparação, o que torna a resolução do jogo
indiferente; escalas alternativas cobrem interfaces maiores ou menores que a dos recortes.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.config import RecognitionConfig
from core.state_machine import GAME_STATE_LABELS, GameState

logger = logging.getLogger(__name__)

# Altura dos prints de onde os recortes são extraídos.
REFERENCE_HEIGHT = 1080
# Com a tela desconhecida, as escalas alternativas são testadas só a cada tantos quadros, pois
# multiplicam o custo da análise.
_SCALE_PROBE_INTERVAL = 10
_MIN_TEMPLATE_SIZE = 4
# Com os contadores da partida visíveis, nenhuma outra tela está aberta. Basta conferi-los, o que reduz
# muito o custo da análise justamente enquanto o jogo mais precisa do processador.
_EXCLUSIVE_SCREENS = {GameState.IN_MATCH}
# O atalho dispensa a prioridade entre as telas, então só vale com folga sobre o limite. Perto dele, outra
# tela pode estar aberta: o resultado da partida chega a 74% nesses ícones.
_EXCLUSIVE_MARGIN = 0.08


def read_image(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """Lê uma imagem; cv2.imread não abre caminhos com acentos no Windows."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, flags) if data.size else None


@dataclass(frozen=True)
class TemplateMatch:
    name: str
    score: float
    left: int
    top: int
    width: int
    height: int
    scale: float


@dataclass(frozen=True)
class ScreenResult:
    state: GameState
    score: float
    threshold: float
    matches: tuple[TemplateMatch, ...]  # do maior para o menor score

    @property
    def matched(self) -> bool:
        return self.score >= self.threshold


@dataclass(frozen=True)
class Detection:
    state: GameState
    confidence: float
    screens: dict[GameState, ScreenResult]

    def is_visible(self, state: GameState) -> bool:
        result = self.screens.get(state)
        return result is not None and result.matched

    def scores(self) -> dict[GameState, float]:
        return {state: result.score for state, result in self.screens.items()}


@dataclass(frozen=True)
class _Template:
    name: str
    variants: dict[float, np.ndarray]


class ScreenDetector:
    def __init__(self, config: RecognitionConfig, templates_dir: Path) -> None:
        self._working_height = config.working_height
        self._scales = list(config.scales)
        self.order = list(config.screens)
        self._min_matches = {state: rule.min_matches for state, rule in config.screens.items()}
        self.base_thresholds = {state: rule.threshold for state, rule in config.screens.items()}
        self._thresholds = dict(self.base_thresholds)
        self._preferred_scale = {state: self._scales[0] for state in self.order}
        self._unknown_frames = 0
        self.warnings: list[str] = []

        digest = hashlib.sha1(f"{self._working_height}|{self._scales}|{self._min_matches}".encode())
        self._templates = {
            state: self._load_group(state, templates_dir / rule.folder, digest)
            for state, rule in config.screens.items()
        }
        self.fingerprint = digest.hexdigest()

    @property
    def thresholds(self) -> dict[GameState, float]:
        return dict(self._thresholds)

    def set_thresholds(self, thresholds: dict[GameState, float]) -> None:
        self._thresholds = {**self.base_thresholds, **thresholds}

    def detect(self, frame: np.ndarray, likely: GameState | None = None) -> Detection:
        """Reconhece a tela. Com a tela provável informada, o resultado pode trazer só o score dela."""
        working, factor = self._to_working(frame)
        if likely in _EXCLUSIVE_SCREENS and likely in self._templates:
            result = self._evaluate_screen(likely, working, factor, [self._preferred_scale[likely]])
            if result.score >= result.threshold + _EXCLUSIVE_MARGIN:
                self._unknown_frames = 0
                return Detection(likely, result.score, {likely: result})

        detection = self._evaluate(working, factor, all_scales=False)
        if detection.state is not GameState.UNKNOWN or len(self._scales) == 1:
            self._unknown_frames = 0
            return detection

        self._unknown_frames += 1
        if self._unknown_frames % _SCALE_PROBE_INTERVAL != 1:
            return detection
        probe = self._evaluate(working, factor, all_scales=True)
        for state, result in probe.screens.items():
            if result.matched and result.matches:
                self._preferred_scale[state] = result.matches[0].scale
        return probe

    def score(self, frame: np.ndarray, all_scales: bool = True) -> Detection:
        """Avalia todas as telas em um quadro isolado, sem afetar a análise contínua."""
        working, factor = self._to_working(frame)
        return self._evaluate(working, factor, all_scales=all_scales)

    def _load_group(self, state: GameState, folder: Path, digest: "hashlib._Hash") -> list[_Template]:
        templates = []
        for path in sorted(folder.glob("*.png")):
            image = read_image(path, cv2.IMREAD_GRAYSCALE)
            if image is None:
                self.warnings.append(f"Recorte ilegível: {path.name}")
                continue
            digest.update(f"{state.value}/{path.name}".encode())
            digest.update(image.tobytes())
            variants = self._build_variants(image)
            if variants:
                templates.append(_Template(path.stem, variants))
            else:
                self.warnings.append(f"Recorte pequeno demais: {path.name}")

        label = GAME_STATE_LABELS[state]
        if len(templates) < self._min_matches[state]:
            self.warnings.append(
                f"{label} não será reconhecida: exige {self._min_matches[state]} recorte(s), "
                f"mas {folder} tem {len(templates)}."
            )
        return templates

    def _build_variants(self, image: np.ndarray) -> dict[float, np.ndarray]:
        variants = {}
        for scale in self._scales:
            factor = self._working_height / REFERENCE_HEIGHT * scale
            width = round(image.shape[1] * factor)
            height = round(image.shape[0] * factor)
            if min(width, height) >= _MIN_TEMPLATE_SIZE:
                variants[scale] = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
        return variants

    def _to_working(self, frame: np.ndarray) -> tuple[np.ndarray, float]:
        factor = self._working_height / frame.shape[0]
        interpolation = cv2.INTER_AREA if factor < 1 else cv2.INTER_LINEAR
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, None, fx=factor, fy=factor, interpolation=interpolation), factor

    def _evaluate_screen(self, state: GameState, working: np.ndarray, factor: float, scales: list[float]) -> ScreenResult:
        matches = sorted(
            (self._match(working, factor, template, scales) for template in self._templates[state]),
            key=lambda match: match.score,
            reverse=True,
        )
        required = self._min_matches[state]
        score = matches[required - 1].score if len(matches) >= required else 0.0
        return ScreenResult(state, score, self._thresholds[state], tuple(matches))

    def _evaluate(self, working: np.ndarray, factor: float, *, all_scales: bool) -> Detection:
        screens: dict[GameState, ScreenResult] = {}
        winner: ScreenResult | None = None
        for state in self.order:
            scales = self._scales if all_scales else [self._preferred_scale[state]]
            result = self._evaluate_screen(state, working, factor, scales)
            screens[state] = result
            if winner is None and result.matched:
                winner = result

        if winner is not None:
            return Detection(winner.state, winner.score, screens)
        best_score = max((result.score for result in screens.values()), default=0.0)
        return Detection(GameState.UNKNOWN, best_score, screens)

    @staticmethod
    def _match(working: np.ndarray, factor: float, template: _Template, scales: list[float]) -> TemplateMatch:
        best = TemplateMatch(template.name, 0.0, 0, 0, 0, 0, scales[0])
        for scale in scales:
            image = template.variants.get(scale)
            if image is None or image.shape[0] > working.shape[0] or image.shape[1] > working.shape[1]:
                continue
            result = cv2.matchTemplate(working, image, cv2.TM_CCOEFF_NORMED)
            # Regiões de cor uniforme geram divisão por zero na correlação.
            np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            _, score, _, (x, y) = cv2.minMaxLoc(result)
            if score > best.score:
                best = TemplateMatch(
                    template.name,
                    min(float(score), 1.0),
                    round(x / factor),
                    round(y / factor),
                    round(image.shape[1] / factor),
                    round(image.shape[0] / factor),
                    scale,
                )
        return best
