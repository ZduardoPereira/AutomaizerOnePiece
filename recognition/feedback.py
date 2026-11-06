"""Aprendizado com as respostas do usuário: exemplos rotulados e ajuste do limite de cada tela.

Cada resposta salva o quadro e os scores obtidos. Os limites são recalculados a partir de todos os
exemplos, sempre dentro de uma faixa segura; apagar um exemplo desfaz o efeito dele.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from core.config import RecognitionConfig
from core.state_machine import GAME_STATE_LABELS, GameState
from recognition.interface_detector import Detection, ScreenDetector, read_image

logger = logging.getLogger(__name__)

_JPEG_QUALITY = 92
CALIBRATION_MARGIN = 0.03
# O score de uma mesma tela varia com o fundo do jogo; subir o limite além disso faria o app deixar de
# reconhecê-la, possivelmente por causa de um único clique errado.
MAX_THRESHOLD_RAISE = 0.08


@dataclass(frozen=True)
class FeedbackSample:
    id: str
    label: GameState
    predicted: GameState
    confidence: float
    scores: dict[GameState, float]
    created_at: datetime
    fingerprint: str


@dataclass(frozen=True)
class Calibration:
    threshold: float
    conflict: bool


@dataclass(frozen=True)
class FeedbackOutcome:
    saved: bool
    message: str


def calibrate_threshold(
    base: float, positives: Iterable[float], negatives: Iterable[float], floor: float, ceiling: float
) -> Calibration:
    """Calcula o limite com o menor desvio do valor-base que respeite os exemplos.

    Um exemplo só tem efeito se o ajuste resolvê-lo: positivos abaixo do mínimo não seriam reconhecidos
    mesmo no mínimo, e negativos altos demais exigiriam subir o limite além do seguro. Nesses casos o
    limite não muda, de modo que uma marcação errada sobre uma tela evidente não causa dano.
    """
    negatives = list(negatives)
    upper = min(
        (score - CALIBRATION_MARGIN for score in positives if score - CALIBRATION_MARGIN >= floor), default=None
    )
    highest = base + MAX_THRESHOLD_RAISE if upper is None else min(base + MAX_THRESHOLD_RAISE, upper)

    threshold = base if upper is None else min(base, upper)
    raises = [score + CALIBRATION_MARGIN for score in negatives if score + CALIBRATION_MARGIN <= highest]
    if raises:
        threshold = max(threshold, max(raises))
    conflict = upper is not None and any(score + CALIBRATION_MARGIN > upper for score in negatives)
    return Calibration(min(max(threshold, floor), ceiling), conflict)


def calibrate_all(
    samples: Sequence[FeedbackSample],
    order: Sequence[GameState],
    base: dict[GameState, float],
    floor: float,
    ceiling: float,
) -> dict[GameState, Calibration]:
    """Calibra cada tela. Em um exemplo da tela X, só telas de prioridade maior que X devem falhar.

    As de prioridade menor podem aparecer junto sem causar erro, como as abas do lobby ao procurar
    partida; em um exemplo de tela desconhecida, nenhuma deve ser reconhecida.
    """
    priority = {state: index for index, state in enumerate(order)}
    calibrations = {}
    for state in order:
        positives = [sample.scores.get(state, 0.0) for sample in samples if sample.label is state]
        negatives = [
            sample.scores.get(state, 0.0)
            for sample in samples
            if sample.label is GameState.UNKNOWN
            or (sample.label in priority and priority[state] < priority[sample.label])
        ]
        calibrations[state] = calibrate_threshold(base[state], positives, negatives, floor, ceiling)
    return calibrations


def _sample_to_json(sample: FeedbackSample) -> dict[str, object]:
    return {
        "id": sample.id,
        "label": sample.label.value,
        "predicted": sample.predicted.value,
        "confidence": sample.confidence,
        "scores": {state.value: score for state, score in sample.scores.items()},
        "created_at": sample.created_at.isoformat(),
        "fingerprint": sample.fingerprint,
    }


def _sample_from_json(data: dict[str, object]) -> FeedbackSample:
    return FeedbackSample(
        id=str(data["id"]),
        label=GameState(data["label"]),
        predicted=GameState(data["predicted"]),
        confidence=float(data["confidence"]),
        scores={GameState(state): float(score) for state, score in data["scores"].items()},
        created_at=datetime.fromisoformat(str(data["created_at"])),
        fingerprint=str(data["fingerprint"]),
    )


class FeedbackManager:
    def __init__(self, directory: Path, detector: ScreenDetector, config: RecognitionConfig) -> None:
        self._directory = directory
        self._detector = detector
        self._floor = config.threshold_floor
        self._ceiling = config.threshold_ceiling
        self._last_frame_id: int | None = None
        self._conflicts: list[GameState] = []
        self._directory.mkdir(parents=True, exist_ok=True)
        self._samples = self._load_samples()
        self._calibrate()

    @property
    def count(self) -> int:
        return len(self._samples)

    def samples(self, label: GameState | None = None) -> list[FeedbackSample]:
        """Retorna os exemplos, do mais novo para o mais antigo."""
        samples = sorted(self._samples.values(), key=lambda sample: sample.created_at, reverse=True)
        return [sample for sample in samples if label is None or sample.label is label]

    def load_frame(self, sample_id: str) -> np.ndarray | None:
        return read_image(self._directory / f"{sample_id}.jpg")

    def add(self, frame: np.ndarray, frame_id: int, detection: Detection, label: GameState) -> FeedbackOutcome:
        """Salva um exemplo, recalibra os limites e descreve o efeito obtido."""
        if frame_id == self._last_frame_id:
            return FeedbackOutcome(False, "Este quadro já foi marcado.")

        # A análise contínua pode ter avaliado só a tela provável; o exemplo precisa de todas.
        scores = detection.scores()
        if set(scores) != set(self._detector.order):
            scores = self._detector.score(frame, all_scales=False).scores()

        now = datetime.now()
        # O relógio do Windows avança aos saltos, e dois exemplos com o mesmo nome se sobrescreveriam.
        while f"{now:%Y%m%d-%H%M%S-%f}" in self._samples:
            now += timedelta(microseconds=1)
        sample = FeedbackSample(
            id=f"{now:%Y%m%d-%H%M%S-%f}",
            label=label,
            predicted=detection.state,
            confidence=detection.confidence,
            scores=scores,
            created_at=now,
            fingerprint=self._detector.fingerprint,
        )
        encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        if not encoded:
            raise OSError("Não foi possível codificar o quadro")
        buffer.tofile(self._directory / f"{sample.id}.jpg")
        self._write_sidecar(sample)

        self._samples[sample.id] = sample
        self._last_frame_id = frame_id
        before = self._detector.thresholds
        self._calibrate()
        return FeedbackOutcome(True, self._describe(sample, before))

    def delete(self, sample_id: str) -> None:
        for suffix in (".jpg", ".json"):
            (self._directory / f"{sample_id}{suffix}").unlink(missing_ok=True)
        self._samples.pop(sample_id, None)
        self._calibrate()

    def rescore_outdated(self) -> int:
        """Reavalia exemplos feitos com outros recortes; seus scores não valem mais. Retorna quantos."""
        rescored = 0
        for sample in list(self._samples.values()):
            if sample.fingerprint == self._detector.fingerprint:
                continue
            frame = self.load_frame(sample.id)
            if frame is None:
                logger.warning("Exemplo sem imagem legível: %s", sample.id)
                continue
            updated = replace(
                sample, scores=self._detector.score(frame).scores(), fingerprint=self._detector.fingerprint
            )
            self._write_sidecar(updated)
            self._samples[sample.id] = updated
            rescored += 1
        if rescored:
            self._calibrate()
        return rescored

    def _load_samples(self) -> dict[str, FeedbackSample]:
        samples = {}
        for path in self._directory.glob("*.json"):
            try:
                sample = _sample_from_json(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                logger.warning("Exemplo ignorado por estar inválido: %s", path.name, exc_info=True)
                continue
            samples[sample.id] = sample
        return samples

    def _write_sidecar(self, sample: FeedbackSample) -> None:
        path = self._directory / f"{sample.id}.json"
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(_sample_to_json(sample), indent=2), encoding="utf-8")
        os.replace(temp_path, path)

    def _calibrate(self) -> None:
        current = [sample for sample in self._samples.values() if sample.fingerprint == self._detector.fingerprint]
        calibrations = calibrate_all(
            current, self._detector.order, self._detector.base_thresholds, self._floor, self._ceiling
        )
        self._detector.set_thresholds({state: calibration.threshold for state, calibration in calibrations.items()})
        self._conflicts = [state for state, calibration in calibrations.items() if calibration.conflict]

    def _describe(self, sample: FeedbackSample, before: dict[GameState, float]) -> str:
        after = self._detector.thresholds
        parts = [f"Salvo como {GAME_STATE_LABELS[sample.label]}."]
        for state, threshold in after.items():
            if round(threshold, 2) != round(before[state], 2):
                parts.append(f"Limite de {GAME_STATE_LABELS[state]}: {before[state]:.0%} → {threshold:.0%}.")

        score = sample.scores.get(sample.label)
        if score is not None and score < after[sample.label]:
            parts.append(f"Os recortes atuais ainda não reconhecem esta tela ({score:.0%}): ela precisa de um recorte novo.")
        predicted_score = sample.scores.get(sample.predicted)
        if (
            sample.predicted is not sample.label
            and predicted_score is not None
            and predicted_score >= after[sample.predicted]
        ):
            parts.append(
                f"Este erro pode se repetir: {GAME_STATE_LABELS[sample.predicted]} deu {predicted_score:.0%}, "
                f"alto demais para corrigir só com o limite. Confira a marcação; se estiver certa, os recortes "
                f"dessa tela precisam ser trocados."
            )
        if self._conflicts:
            names = ", ".join(GAME_STATE_LABELS[state] for state in self._conflicts)
            parts.append(f"Há exemplos em conflito em {names}: vale revisá-los.")
        return " ".join(parts)
