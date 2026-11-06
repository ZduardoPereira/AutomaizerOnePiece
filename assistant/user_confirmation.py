"""Acompanha a espera pelo clique do usuário, que é percebido pela mudança da tela seguinte."""

from __future__ import annotations

from enum import Enum


class ConfirmationState(str, Enum):
    NONE = "NONE"
    WAITING = "WAITING"
    CONFIRMED = "CONFIRMED"


class ManualConfirmation:
    def __init__(self, reminder_seconds: float, max_reminders: int) -> None:
        self.reminder_seconds = reminder_seconds
        self.max_reminders = max_reminders
        self.state = ConfirmationState.NONE
        self._next_reminder_at = 0.0
        self._reminders_sent = 0

    def expect(self, now: float) -> None:
        self.state = ConfirmationState.WAITING
        self._next_reminder_at = now + self.reminder_seconds
        self._reminders_sent = 0

    def confirm(self) -> None:
        self.state = ConfirmationState.CONFIRMED

    def clear(self) -> None:
        self.state = ConfirmationState.NONE

    def reminder_due(self, now: float) -> bool:
        """Indica se é hora de avisar de novo: o usuário nem sempre está olhando para o jogo."""
        if self.state is not ConfirmationState.WAITING or self._reminders_sent >= self.max_reminders:
            return False
        if now < self._next_reminder_at:
            return False
        self._reminders_sent += 1
        self._next_reminder_at = now + self.reminder_seconds
        return True
