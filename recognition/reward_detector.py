"""Reconhecimento do token de recompensa do evento.

Um ícone parecido não basta. A recompensa só é confirmada com o nome dela na tela e mais uma evidência
alinhada a ele, em capturas seguidas. Se o reconhecimento ficar pela metade, o melhor quadro é guardado
para o usuário decidir, e nada é dado como obtido sem essa resposta.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock

import cv2
import numpy as np

from core.config import RewardConfig
from core.event_manager import EventManager, EventType
from database.models import RewardRecord
from database.repositories import RewardRepository
from recognition.interface_detector import REFERENCE_HEIGHT, read_image

logger = logging.getLogger(__name__)

_ICON_PREFIX = "icone"
_MESSAGE_PREFIX = "mensagem"
_NAME_PREFIX = "nome"
_MAX_CENTER_OFFSET = 0.05  # o aviso é centralizado; em relação à largura da região de busca
_GOLD_LOW = np.array((10, 110, 130), np.uint8)
_GOLD_HIGH = np.array((32, 255, 255), np.uint8)
_GATE_SIZE = (192, 108)
_CANDIDATE_FILE = "candidato.png"


class RewardState(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    WAITING_FOR_MATCH_END = "WAITING_FOR_MATCH_END"
    STOPPED = "STOPPED"


_OBTAINED = {RewardState.CONFIRMED, RewardState.WAITING_FOR_MATCH_END, RewardState.STOPPED}


@dataclass(frozen=True)
class RewardEvidence:
    icon: float = 0.0
    message: float = 0.0
    name: float = 0.0
    sufficient: bool = False  # o bastante para contar como uma das capturas de confirmação
    suspicious: bool = False  # algo parecido apareceu, mas não basta

    @property
    def confidence(self) -> float:
        return (self.name + max(self.icon, self.message)) / 2


@dataclass(frozen=True)
class RewardStatus:
    state: RewardState = RewardState.NOT_FOUND
    reward_type: str = "event_token"
    confidence: float = 0.0
    detected_at: datetime | None = None
    evidence_path: Path | None = None
    confirmed_by_user: bool = False
    candidate_path: Path | None = None
    candidate_confidence: float = 0.0
    match_id: int | None = None

    @property
    def token_obtained(self) -> bool:
        return self.state in _OBTAINED


@dataclass(frozen=True)
class _Template:
    variants: dict[float, np.ndarray]


@dataclass(frozen=True)
class _Found:
    score: float = 0.0
    center_x: float = 0.0
    top: float = 0.0


class RewardDetector:
    """Compara o quadro com os recortes de assets/templates, separados pelo prefixo do nome do arquivo."""

    def __init__(self, config: RewardConfig, templates_dir: Path, working_height: int) -> None:
        self._config = config
        self._working_height = working_height
        folder = templates_dir / config.folder
        self._icons = self._load(folder, _ICON_PREFIX)
        self._messages = self._load(folder, _MESSAGE_PREFIX)
        self._names = self._load(folder, _NAME_PREFIX)
        self.warnings: list[str] = []
        if not self._names or not (self._icons or self._messages):
            self.warnings.append(f"O token não será reconhecido: faltam recortes em {folder}.")

    def inspect(self, frame: np.ndarray) -> RewardEvidence:
        if not self._has_gold(frame):
            return RewardEvidence()
        area = self._search_area(frame)
        icon = self._best(area, self._icons)
        message = self._best(area, self._messages)
        name = self._best(area, self._names)

        supported = (
            name.score >= self._config.name_threshold
            and (
                self._supports(icon, name, area, self._config.icon_threshold)
                or self._supports(message, name, area, self._config.message_threshold)
            )
        )
        suspicious = name.score >= self._config.candidate_threshold or icon.score >= self._config.icon_threshold
        return RewardEvidence(icon.score, message.score, name.score, supported, suspicious or supported)

    def _load(self, folder: Path, prefix: str) -> list[_Template]:
        templates = []
        for path in sorted(folder.glob(f"{prefix}*.png")):
            image = read_image(path, cv2.IMREAD_GRAYSCALE)
            if image is not None:
                templates.append(_Template(self._variants(image)))
        return templates

    def _variants(self, image: np.ndarray) -> dict[float, np.ndarray]:
        variants = {}
        for scale in self._config.scales:
            factor = self._working_height / REFERENCE_HEIGHT * scale
            size = (round(image.shape[1] * factor), round(image.shape[0] * factor))
            variants[scale] = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        return variants

    def _has_gold(self, frame: np.ndarray) -> bool:
        """Teste barato que evita a busca em quase todos os quadros: o aviso do token é muito dourado."""
        small = cv2.resize(frame, _GATE_SIZE, interpolation=cv2.INTER_AREA)
        left, top, right, bottom = self._config.search_region
        area = small[int(top * _GATE_SIZE[1]) : int(bottom * _GATE_SIZE[1]), int(left * _GATE_SIZE[0]) : int(right * _GATE_SIZE[0])]
        mask = cv2.inRange(cv2.cvtColor(area, cv2.COLOR_BGR2HSV), _GOLD_LOW, _GOLD_HIGH)
        return np.count_nonzero(mask) / mask.size >= self._config.gold_gate

    def _search_area(self, frame: np.ndarray) -> np.ndarray:
        factor = self._working_height / frame.shape[0]
        gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
        height, width = gray.shape
        left, top, right, bottom = self._config.search_region
        return gray[int(top * height) : int(bottom * height), int(left * width) : int(right * width)]

    @staticmethod
    def _best(area: np.ndarray, templates: list[_Template]) -> _Found:
        best = _Found()
        for template in templates:
            for image in template.variants.values():
                if image.shape[0] > area.shape[0] or image.shape[1] > area.shape[1]:
                    continue
                result = cv2.matchTemplate(area, image, cv2.TM_CCOEFF_NORMED)
                np.nan_to_num(result, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
                _, score, _, (x, y) = cv2.minMaxLoc(result)
                if score > best.score:
                    best = _Found(float(score), x + image.shape[1] / 2, float(y))
        return best

    @staticmethod
    def _supports(other: _Found, name: _Found, area: np.ndarray, threshold: float) -> bool:
        """Confere se a outra evidência está acima do nome e centralizada com ele, como na tela real."""
        centered = abs(other.center_x - name.center_x) <= _MAX_CENTER_OFFSET * area.shape[1]
        return other.score >= threshold and centered and other.top < name.top


class RewardTracker:
    """Confirma o token em capturas seguidas e mantém o registro entre execuções, sem duplicá-lo."""

    def __init__(
        self,
        config: RewardConfig,
        events: EventManager,
        directory: Path,
        repository: RewardRepository,
        current_match_id: Callable[[], int | None] = lambda: None,
    ) -> None:
        self._config = config
        self._events = events
        self._directory = directory
        self._repository = repository
        self._current_match_id = current_match_id
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._hits = 0
        self._missed = 0
        self._best_frame: np.ndarray | None = None
        self._best_confidence = 0.0
        self._tracked_match_id: int | None = None
        self._status = self._load()

    @property
    def status(self) -> RewardStatus:
        with self._lock:
            return self._status

    @property
    def is_searching(self) -> bool:
        return not self.status.token_obtained

    def observe(self, frame: np.ndarray, evidence: RewardEvidence) -> None:
        """Registra um quadro analisado durante a partida."""
        with self._lock:
            if self._status.token_obtained:
                return
            if not evidence.suspicious:
                self._on_nothing_seen()
                return

            self._missed = 0
            if self._status.state is RewardState.NOT_FOUND:
                self._status = replace(self._status, state=RewardState.CANDIDATE)
                self._tracked_match_id = self._current_match_id()
                self._events.publish(EventType.REWARD_CANDIDATE, "Possível token de recompensa na tela")
            if evidence.confidence > self._best_confidence:
                self._best_confidence = evidence.confidence
                self._best_frame = frame
            if evidence.sufficient:
                self._hits += 1
            if self._hits >= self._config.confirm_frames:
                self._confirm_automatically()

    def on_match_over(self) -> None:
        with self._lock:
            self._finish_candidate()
            if self._status.state is RewardState.WAITING_FOR_MATCH_END:
                self._status = replace(self._status, state=RewardState.STOPPED)
                self._save()
                self._events.publish(EventType.REWARD_STOPPED, "Partida encerrada com o token obtido")

    def interrupt(self) -> None:
        """O monitoramento parou: o que estava em confirmação não tem mais como ser concluído."""
        with self._lock:
            self._finish_candidate()

    def confirm_candidate(self, in_match: bool) -> None:
        """O usuário reconheceu o token no quadro guardado."""
        with self._lock:
            candidate = self._status.candidate_path
            if candidate is None or self._status.token_obtained:
                return
            now = datetime.now()
            evidence = self._directory / f"token_{now:%Y%m%d-%H%M%S}.png"
            os.replace(candidate, evidence)
            self._record_token(now, self._status.candidate_confidence, evidence, True, in_match, self._status.match_id)

    def dismiss_candidate(self) -> None:
        with self._lock:
            self._delete(self._status.candidate_path)
            self._status = replace(self._status, candidate_path=None, candidate_confidence=0.0, match_id=None)
            self._save()

    def reset(self) -> None:
        """Apaga o registro e as imagens, para um token novo ou após uma confirmação indevida."""
        with self._lock:
            self._delete(self._status.evidence_path)
            self._delete(self._status.candidate_path)
            self._forget_saved_record()
            self._clear_tracking()
            self._status = RewardStatus(reward_type=self._config.reward_type)
            self._events.publish(EventType.REWARD_RESET, "Registro do token apagado")

    def _on_nothing_seen(self) -> None:
        if self._status.state is not RewardState.CANDIDATE:
            return
        self._missed += 1
        if self._missed > self._config.max_missed_frames:
            self._finish_candidate()

    def _confirm_automatically(self) -> None:
        now = datetime.now()
        evidence = self._directory / f"token_{now:%Y%m%d-%H%M%S}.png"
        self._write_image(evidence, self._best_frame)
        self._delete(self._status.candidate_path)
        self._status = replace(self._status, candidate_path=None, candidate_confidence=0.0)
        self._record_token(now, self._best_confidence, evidence, False, True, self._tracked_match_id)

    def _record_token(
        self, now: datetime, confidence: float, evidence: Path, by_user: bool, in_match: bool, match_id: int | None
    ) -> None:
        state = RewardState.WAITING_FOR_MATCH_END if in_match else RewardState.STOPPED
        self._status = RewardStatus(state, self._config.reward_type, confidence, now, evidence, by_user, match_id=match_id)
        self._clear_tracking()
        self._save()
        self._events.publish(EventType.REWARD_CONFIRMED, f"Token de recompensa confirmado ({confidence:.0%})", confidence)
        if in_match:
            self._events.publish(EventType.REWARD_WAITING_MATCH_END, "Aguardando o fim da partida atual")

    def _finish_candidate(self) -> None:
        """Encerra a confirmação em andamento; se ela não se completou, guarda o melhor quadro para revisão."""
        if self._status.state is not RewardState.CANDIDATE:
            return
        frame, confidence, match_id = self._best_frame, self._best_confidence, self._tracked_match_id
        self._clear_tracking()
        self._status = replace(self._status, state=RewardState.NOT_FOUND)
        if frame is not None and confidence > self._status.candidate_confidence:
            path = self._directory / _CANDIDATE_FILE
            self._write_image(path, frame)
            self._status = replace(self._status, candidate_path=path, candidate_confidence=confidence, match_id=match_id)
            self._save()
            self._events.publish(EventType.REWARD_REVIEW_NEEDED, "Veja em Recompensas se este quadro mostra o token")

    def _clear_tracking(self) -> None:
        self._hits = 0
        self._missed = 0
        self._best_frame = None
        self._best_confidence = 0.0
        self._tracked_match_id = None

    @staticmethod
    def _write_image(path: Path, frame: np.ndarray) -> None:
        encoded, buffer = cv2.imencode(".png", frame)
        if not encoded:
            raise OSError(f"Não foi possível codificar {path.name}")
        buffer.tofile(path)

    @staticmethod
    def _delete(path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)

    def _save(self) -> None:
        status = self._status
        record = RewardRecord(
            status.reward_type,
            status.state.value,
            status.confidence,
            status.detected_at.isoformat() if status.detected_at else None,
            status.evidence_path.name if status.evidence_path else None,
            status.confirmed_by_user,
            status.candidate_path.name if status.candidate_path else None,
            status.candidate_confidence,
            status.match_id,
        )
        try:
            self._repository.save(record)
        except sqlite3.Error:
            logger.exception("Não foi possível gravar o registro do token")

    def _forget_saved_record(self) -> None:
        try:
            self._repository.delete(self._config.reward_type)
        except sqlite3.Error:
            logger.exception("Não foi possível apagar o registro do token")

    def _load(self) -> RewardStatus:
        default = RewardStatus(reward_type=self._config.reward_type)
        record = self._repository.load(self._config.reward_type)
        if record is None:
            return default
        try:
            state = RewardState(record.status)
            detected_at = datetime.fromisoformat(record.detected_at) if record.detected_at else None
        except ValueError:
            logger.warning("Registro do token ignorado por estar inválido: %s", record, exc_info=True)
            return default
        status = RewardStatus(
            state,
            record.reward_type,
            record.confidence,
            detected_at,
            self._existing(record.evidence_path),
            record.confirmed_by_user,
            self._existing(record.candidate_path),
            record.candidate_confidence,
            record.match_id,
        )
        # Um estado transitório só fica salvo se o aplicativo tiver sido fechado no meio dele.
        if status.state is RewardState.CONFIRMED:
            return replace(status, state=RewardState.WAITING_FOR_MATCH_END)
        if status.state is RewardState.CANDIDATE:
            return replace(status, state=RewardState.NOT_FOUND)
        return status

    def _existing(self, name: str | None) -> Path | None:
        if not name:
            return None
        path = self._directory / name
        return path if path.exists() else None
