"""Configurações do aplicativo, validadas com Pydantic e persistidas em config.json."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.state_machine import GameState


# O OneDrive pode segurar o arquivo por instantes.
_SAVE_ATTEMPTS = 5
_SAVE_RETRY_SECONDS = 0.2


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CaptureConfig(_Section):
    fps: float = Field(default=3.0, gt=0, le=30)
    window_poll_seconds: float = Field(default=2.0, ge=0.5, le=30)
    # O MSS captura a área da tela: com o jogo atrás de outra janela, o quadro mostraria essa outra janela.
    capture_only_when_foreground: bool = True
    black_frame_threshold: float = Field(default=4.0, ge=0, le=255)
    process_names: list[str] = Field(default_factory=lambda: ["FortniteClient-Win64-Shipping.exe"])
    window_titles: list[str] = Field(default_factory=lambda: ["Fortnite"])

    @field_validator("process_names", "window_titles")
    @classmethod
    def _clean_names(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if not cleaned:
            raise ValueError("informe ao menos um valor")
        return cleaned


class ScreenRule(_Section):
    folder: str = Field(min_length=1)  # subpasta de assets/templates
    threshold: float = Field(default=0.75, ge=0.3, le=0.99)
    min_matches: int = Field(default=1, ge=1)


def _default_screens() -> dict[GameState, ScreenRule]:
    # A ordem é a prioridade: a tela de resultado de uma vitória também mostra o banner de vitória, a
    # de procurar partida também mostra as abas do lobby, e o menu pode abrir sobre o modo espectador.
    return {
        GameState.MATCH_RESULT: ScreenRule(folder="result", min_matches=2),
        GameState.VICTORY: ScreenRule(folder="victory"),
        GameState.DEFEAT: ScreenRule(folder="defeat", min_matches=2),
        GameState.ELIMINATED: ScreenRule(folder="eliminated"),
        GameState.GAME_MENU: ScreenRule(folder="menu", threshold=0.85, min_matches=2),
        GameState.MATCHMAKING: ScreenRule(folder="matchmaking"),
        GameState.LOADING: ScreenRule(folder="loading"),
        GameState.LOBBY: ScreenRule(folder="lobby"),
        # Os ícones da partida são pequenos e por isso se parecem mais com outras regiões do quadro.
        GameState.IN_MATCH: ScreenRule(folder="in_match", threshold=0.80, min_matches=2),
    }


class RecognitionConfig(_Section):
    fps: float = Field(default=2.0, gt=0, le=15)
    confirm_frames: int = Field(default=2, ge=1, le=30)
    unknown_grace_seconds: float = Field(default=8.0, ge=0, le=300)
    working_height: int = Field(default=540, ge=240, le=2160)
    scales: list[float] = Field(default_factory=lambda: [1.0, 0.9, 1.1])
    threshold_floor: float = Field(default=0.62, ge=0.3, le=0.99)
    threshold_ceiling: float = Field(default=0.97, ge=0.3, le=0.99)
    screens: dict[GameState, ScreenRule] = Field(default_factory=_default_screens)

    @field_validator("scales")
    @classmethod
    def _check_scales(cls, values: list[float]) -> list[float]:
        if not values or any(value <= 0 for value in values):
            raise ValueError("informe ao menos uma escala positiva")
        return values

    @field_validator("screens")
    @classmethod
    def _check_screens(cls, screens: dict[GameState, ScreenRule]) -> dict[GameState, ScreenRule]:
        if not screens:
            raise ValueError("informe ao menos uma tela")
        if GameState.UNKNOWN in screens:
            raise ValueError("UNKNOWN é o resultado de nenhuma tela reconhecida e não tem recortes")
        return screens

    @model_validator(mode="after")
    def _check_threshold_range(self) -> RecognitionConfig:
        if self.threshold_floor >= self.threshold_ceiling:
            raise ValueError("threshold_floor deve ser menor que threshold_ceiling")
        return self


class SquadConfig(_Section):
    confirm_frames: int = Field(default=2, ge=1, le=30)
    # Uma linha que some pode ser só um item passando na frente; um colega só é dado como fora da
    # partida depois de mais capturas seguidas.
    missing_row_confirm_frames: int = Field(default=8, ge=1, le=120)
    low_health_percent: int = Field(default=35, ge=1, le=99)
    card_threshold: float = Field(default=0.75, ge=0.3, le=0.99)


class RewardConfig(_Section):
    reward_type: str = Field(default="event_token", min_length=1)
    folder: str = Field(default="rewards/event_token", min_length=1)  # subpasta de assets/templates
    icon_threshold: float = Field(default=0.70, ge=0.3, le=0.99)
    name_threshold: float = Field(default=0.65, ge=0.3, le=0.99)
    # A mensagem é a mesma para qualquer item, e outras telas chegam a 67% nesse recorte.
    message_threshold: float = Field(default=0.75, ge=0.3, le=0.99)
    candidate_threshold: float = Field(default=0.55, ge=0.3, le=0.99)
    confirm_frames: int = Field(default=3, ge=2, le=30)
    max_missed_frames: int = Field(default=2, ge=0, le=30)
    # 1.0 é o tamanho medido em 1080p com o HUD padrão, e o aviso do token pode ser até 20% maior que o de
    # um item comum. Passos de 4%: com 4% de diferença a nota já cai para cerca de 70%, e com 2% fica em 85%.
    scales: list[float] = Field(default_factory=lambda: [0.96, 1.0, 1.04, 1.08, 1.12, 1.16, 1.2, 1.24])
    search_region: list[float] = Field(default_factory=lambda: [0.12, 0.0, 0.88, 0.62])  # esquerda, topo, direita, base
    # O aviso sozinho ocupa 5,4% da região com dourado; acima disso, o filtro só o deixaria passar com ajuda
    # de outras coisas na tela, como o chapéu laranja de uma skin.
    gold_gate: float = Field(default=0.025, ge=0.0, le=1.0)

    @field_validator("scales")
    @classmethod
    def _check_scales(cls, values: list[float]) -> list[float]:
        if not values or any(value <= 0 for value in values):
            raise ValueError("informe ao menos uma escala positiva")
        return values

    @field_validator("search_region")
    @classmethod
    def _check_region(cls, values: list[float]) -> list[float]:
        if len(values) != 4 or not (0 <= values[0] < values[2] <= 1 and 0 <= values[1] < values[3] <= 1):
            raise ValueError("informe esquerda, topo, direita e base, de 0 a 1")
        return values


class AssistantConfig(_Section):
    enabled: bool = True  # o botão de ligar e desligar a assistência; a escolha vale também na próxima abertura
    press_escape: bool = False  # enviar uma tecla ao jogo é uma escolha do usuário
    escape_delay_seconds: float = Field(default=3.0, ge=0, le=30)
    menu_timeout_seconds: float = Field(default=6.0, ge=1, le=60)
    button_threshold: float = Field(default=0.85, ge=0.5, le=0.99)
    reminder_seconds: float = Field(default=20.0, ge=5, le=300)
    max_reminders: int = Field(default=3, ge=0, le=20)
    cursor_move_seconds: float = Field(default=0.2, ge=0, le=2)


class NotificationConfig(_Section):
    file_enabled: bool = True
    webhook_url: str = ""
    webhook_secret: str = ""
    card_seconds: float = Field(default=6.0, ge=1, le=60)

    @field_validator("webhook_url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("informe um endereço http:// ou https://")
        return value


class UiConfig(_Section):
    appearance_mode: Literal["dark", "light", "system"] = "dark"
    preview_fps: float = Field(default=4.0, gt=0, le=15)


class LoggingConfig(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_bytes: int = Field(default=2_000_000, ge=10_000)
    backup_count: int = Field(default=5, ge=0, le=50)


class AppConfig(_Section):
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    recognition: RecognitionConfig = Field(default_factory=RecognitionConfig)
    squad: SquadConfig = Field(default_factory=SquadConfig)
    rewards: RewardConfig = Field(default_factory=RewardConfig)
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    ui: UiConfig = Field(default_factory=UiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def save_config(config: AppConfig, path: Path) -> None:
    """Grava de forma atômica, para que uma falha no meio não corrompa o arquivo."""
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
    for attempt in range(1, _SAVE_ATTEMPTS + 1):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt == _SAVE_ATTEMPTS:
                raise
            time.sleep(_SAVE_RETRY_SECONDS)


def load_config(path: Path) -> tuple[AppConfig, str | None]:
    """Carrega a configuração. Retorna (config, aviso); o aviso existe se o arquivo era inválido.

    Um arquivo inválido é preservado com outro nome em vez de sobrescrito, pois pode conter
    ajustes do usuário.
    """
    if not path.exists():
        config = AppConfig()
        save_config(config, path)
        return config, None

    try:
        return AppConfig.model_validate(json.loads(path.read_text(encoding="utf-8"))), None
    except (ValueError, ValidationError) as exc:
        backup_path = path.with_name(f"{path.name}.invalid-{datetime.now():%Y%m%d-%H%M%S}")
        os.replace(path, backup_path)
        config = AppConfig()
        save_config(config, path)
        summary = str(exc).splitlines()[0]
        return config, f"config.json inválido ({summary}). Padrões restaurados; original salvo em {backup_path.name}."
