"""Interpreta as leituras do HUD: quem do esquadrão segue ativo e quando o usuário é o último vivo.

As condições se referem somente ao esquadrão; o último integrante vivo não é o último sobrevivente da partida.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from core.config import SquadConfig
from core.event_manager import EventManager, EventType
from core.state_machine import GameState
from recognition.squad_detector import MemberReading, MemberStatus, SquadReading

_ACTIVE = {MemberStatus.ALIVE, MemberStatus.KNOCKED}


class SquadCondition(str, Enum):
    UNKNOWN = "UNKNOWN"
    NO_SQUAD = "NO_SQUAD"
    SQUAD_ACTIVE = "SQUAD_ACTIVE"
    LAST_SQUAD_MEMBER_STANDING = "LAST_SQUAD_MEMBER_STANDING"  # os demais estão caídos, e ainda podem voltar
    LAST_SQUAD_MEMBER_ALIVE = "LAST_SQUAD_MEMBER_ALIVE"
    SQUAD_ELIMINATED = "SQUAD_ELIMINATED"


@dataclass(frozen=True)
class SquadMember:
    is_self: bool
    status: MemberStatus
    health: float | None = None
    shield: float | None = None
    reboot_seconds: int | None = None
    card_collected: bool = False
    left_match: bool = False
    box: tuple[int, int, int, int] | None = None
    picture_box: tuple[int, int, int, int] | None = None

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE

    def needs_help(self, low_health_percent: int) -> bool:
        """Indica um colega que depende de alguém: caído, com o cartão no chão ou com pouca vida."""
        if self.is_self or self.left_match:
            return False
        if self.status is MemberStatus.KNOCKED or self.reboot_seconds is not None or self.card_collected:
            return True
        return self.status is MemberStatus.ALIVE and self.health is not None and self.health * 100 <= low_health_percent


@dataclass(frozen=True)
class SquadStatus:
    condition: SquadCondition = SquadCondition.UNKNOWN
    members: tuple[SquadMember, ...] = ()

    @property
    def self_member(self) -> SquadMember | None:
        return next((member for member in self.members if member.is_self), None)

    @property
    def teammates(self) -> tuple[SquadMember, ...]:
        return tuple(member for member in self.members if not member.is_self)

    @property
    def active_count(self) -> int:
        return sum(member.active for member in self.members)

    @property
    def knocked_teammates(self) -> int:
        return sum(member.status is MemberStatus.KNOCKED for member in self.teammates)

    @property
    def active_teammates(self) -> int:
        return sum(member.active for member in self.teammates)

    @property
    def visible_teammates(self) -> int:
        return sum(not member.left_match for member in self.teammates)

    @property
    def collected_cards(self) -> int:
        return sum(member.card_collected for member in self.teammates)


def _member(reading: MemberReading, is_self: bool = False) -> SquadMember:
    return SquadMember(
        is_self,
        reading.status,
        reading.health,
        reading.shield,
        reading.reboot_seconds,
        reading.card_collected,
        box=reading.box,
        picture_box=reading.picture_box,
    )


def _split_members(reading: SquadReading, state: GameState) -> tuple[SquadMember, list[SquadMember]]:
    if state is not GameState.ELIMINATED:
        hud = reading.hud
        # Sem o HUD principal, como no ônibus, o jogador segue na partida: só não há o que medir.
        you = _member(hud, is_self=True) if hud is not None else SquadMember(True, MemberStatus.ALIVE)
        return you, [_member(row) for row in reading.rows]

    # Eliminado, o usuário passa a ser uma linha da lista, e o HUD principal mostra o colega observado.
    rows = list(reading.rows)
    own_row = next((row for row in rows if row.status is MemberStatus.ELIMINATED), None)
    if own_row is not None:
        rows.remove(own_row)
        you = _member(own_row, is_self=True)
    else:
        you = SquadMember(True, MemberStatus.ELIMINATED, health=0.0, shield=0.0)
    teammates = [_member(row) for row in rows]
    if reading.hud is not None:
        teammates.append(_member(reading.hud))
    return you, teammates


def build_status(reading: SquadReading, state: GameState, expected_teammates: int) -> SquadStatus:
    """Monta o estado do esquadrão; um colega que já apareceu e sumiu da lista saiu da partida."""
    you, teammates = _split_members(reading, state)
    missing = max(0, expected_teammates - len(teammates))
    teammates += [SquadMember(False, MemberStatus.ELIMINATED, left_match=True)] * missing
    members = (you, *teammates)

    if not teammates:
        condition = SquadCondition.NO_SQUAD
    elif any(member.status is MemberStatus.ALIVE for member in teammates):
        condition = SquadCondition.SQUAD_ACTIVE
    elif any(member.status is MemberStatus.KNOCKED for member in teammates):
        standing = you.status is MemberStatus.ALIVE
        condition = SquadCondition.LAST_SQUAD_MEMBER_STANDING if standing else SquadCondition.SQUAD_ACTIVE
    elif you.active:
        condition = SquadCondition.LAST_SQUAD_MEMBER_ALIVE
    else:
        condition = SquadCondition.SQUAD_ELIMINATED
    return SquadStatus(condition, members)


def _summary(status: SquadStatus) -> tuple:
    """O que precisa ser confirmado antes de ser aceito; vida e escudo mudam livremente."""
    you = status.self_member
    teammates = Counter(
        (member.status, member.reboot_seconds is not None, member.card_collected, member.left_match)
        for member in status.teammates
    )
    return status.condition, you.status if you else None, tuple(sorted(teammates.items(), key=repr))


class SurvivalTracker:
    def __init__(self, config: SquadConfig, events: EventManager) -> None:
        self._config = config
        self._events = events
        self._status = SquadStatus()
        self._expected_teammates = 0
        self._pending: tuple | None = None
        self._pending_count = 0

    @property
    def status(self) -> SquadStatus:
        return self._status

    def update_config(self, config: SquadConfig) -> None:
        self._config = config

    def reset(self) -> None:
        self._status = SquadStatus()
        self._expected_teammates = 0
        self._clear_pending()

    def observe(self, reading: SquadReading, state: GameState) -> None:
        if not reading.visible or self._is_incomplete(reading, state):
            self._clear_pending()
            return

        candidate = build_status(reading, state, self._expected_teammates)
        summary = _summary(candidate)
        if summary == _summary(self._status):
            self._clear_pending()
            self._status = candidate
            return

        if summary == self._pending:
            self._pending_count += 1
        else:
            self._pending = summary
            self._pending_count = 1
        row_vanished = candidate.visible_teammates < self._status.visible_teammates
        required = self._config.missing_row_confirm_frames if row_vanished else self._config.confirm_frames
        if self._pending_count >= required:
            self._commit(candidate)

    @staticmethod
    def _is_incomplete(reading: SquadReading, state: GameState) -> bool:
        """Na passagem para o modo espectador, o usuário já entrou na lista, mas o HUD do colega observado
        ainda não apareceu; esse colega pareceria ter saído da partida."""
        own_row_listed = any(row.status is MemberStatus.ELIMINATED for row in reading.rows)
        return state is GameState.ELIMINATED and reading.hud is None and own_row_listed

    def mark_defeat(self) -> None:
        """A tela de colocação só aparece quando não há mais volta para o esquadrão."""
        you = SquadMember(True, MemberStatus.ELIMINATED, health=0.0, shield=0.0)
        teammates = [SquadMember(False, MemberStatus.ELIMINATED, left_match=True)] * self._expected_teammates
        condition = SquadCondition.SQUAD_ELIMINATED if teammates else SquadCondition.NO_SQUAD
        self._commit(SquadStatus(condition, (you, *teammates)))

    def _commit(self, new: SquadStatus) -> None:
        old = self._status
        self._status = new
        self._expected_teammates = max(self._expected_teammates, len(new.teammates))
        self._clear_pending()
        self._announce(old, new)

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_count = 0

    def _announce(self, old: SquadStatus, new: SquadStatus) -> None:
        if old.members:
            self._announce_teammates(old, new)
        old_you = old.self_member
        new_you = new.self_member
        if new_you is not None and new_you.status is MemberStatus.KNOCKED and (
            old_you is None or old_you.status is not MemberStatus.KNOCKED
        ):
            self._events.publish(EventType.SELF_KNOCKED, "Você caiu: precisa de ajuda")
        if new.condition is not old.condition:
            self._announce_condition(new)

    def _announce_teammates(self, old: SquadStatus, new: SquadStatus) -> None:
        knocked_change = new.knocked_teammates - old.knocked_teammates
        if knocked_change == 1:
            self._events.publish(EventType.TEAMMATE_KNOCKED, "Colega caído: precisa de ajuda")
        elif knocked_change > 1:
            self._events.publish(EventType.TEAMMATE_KNOCKED, f"{knocked_change} colegas caídos: precisam de ajuda")
        elif knocked_change < 0 and new.active_teammates == old.active_teammates:
            self._events.publish(EventType.TEAMMATE_RECOVERED, "Colega caído está de pé novamente")

        if new.active_teammates < old.active_teammates:
            timers = [member.reboot_seconds for member in new.teammates if member.reboot_seconds is not None]
            detail = f": cartão no chão por {max(timers)} s" if timers else ""
            self._events.publish(EventType.TEAMMATE_ELIMINATED, f"Colega eliminado{detail}")
        elif new.active_teammates > old.active_teammates and len(new.teammates) == len(old.teammates):
            self._events.publish(EventType.TEAMMATE_RETURNED, "Colega de volta à partida")

        if new.collected_cards > old.collected_cards:
            self._events.publish(EventType.REBOOT_CARD_COLLECTED, "Cartão do colega coletado: leve à van de reinicialização")

    def _announce_condition(self, new: SquadStatus) -> None:
        messages = {
            SquadCondition.NO_SQUAD: "Partida sem esquadrão",
            SquadCondition.SQUAD_ACTIVE: f"Esquadrão ativo: {new.active_count} de {len(new.members)}",
            SquadCondition.LAST_SQUAD_MEMBER_STANDING: "Você é o último de pé: os demais colegas estão fora de combate",
            SquadCondition.LAST_SQUAD_MEMBER_ALIVE: "Você é o último integrante vivo do esquadrão",
            SquadCondition.SQUAD_ELIMINATED: "Esquadrão eliminado",
        }
        self._events.publish(EventType.SQUAD_CONDITION_CHANGED, messages[new.condition])
