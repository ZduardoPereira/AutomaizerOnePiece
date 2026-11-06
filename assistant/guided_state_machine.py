"""Sequência de orientação: o que o usuário deve clicar a seguir e quando o clique aconteceu.

A sequência para uma nova partida começa sempre que o lobby aparece, o usuário abre o menu durante a partida,
a partida termina ou só resta o usuário no esquadrão.

Esta classe só decide. Ela nunca clica, nunca inicia partidas e nunca move o personagem: o cursor só é
movido com um menu na tela, porque durante o jogo o movimento do mouse giraria a câmera. A única tecla
enviada é o Esc que abre o menu, uma vez por partida, e só se o usuário tiver ligado essa opção.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from assistant.user_confirmation import ConfirmationState, ManualConfirmation
from core.config import AssistantConfig
from core.state_machine import GameState
from recognition.survival_detector import SquadCondition

_QUEUEING = {GameState.MATCHMAKING, GameState.LOADING}
# Sem o menu, estas telas mostram que a partida continua; qualquer outra pode ser a transição para o lobby.
_MATCH_GOES_ON = {GameState.IN_MATCH, GameState.ELIMINATED}
# O menu fechado por um quadro pode ser só uma animação; por mais quadros, foi o usuário que fechou.
_MENU_GONE_FRAMES = 3
# O cursor vai até o menu assim que ele abre, então um quadro isolado não basta: com o jogo na tela, o
# movimento giraria a câmera. Sobre o modo espectador a tela segue como eliminado, por isso a conta é à parte.
_MENU_SEEN_FRAMES = 2
_LOBBY_TIMEOUT_SECONDS = 120.0


class NavigationButton(str, Enum):
    MENU_TAB = "MENU_TAB"
    LEAVE_MATCH = "LEAVE_MATCH"
    PLAY = "PLAY"
    PLAY_AGAIN = "PLAY_AGAIN"


class GuidanceStep(str, Enum):
    IDLE = "IDLE"
    COUNTDOWN = "COUNTDOWN"
    WAITING_FOR_MENU = "WAITING_FOR_MENU"
    CLICK_MENU_TAB = "CLICK_MENU_TAB"
    CLICK_LEAVE_MATCH = "CLICK_LEAVE_MATCH"
    WAITING_FOR_LOBBY = "WAITING_FOR_LOBBY"
    CLICK_PLAY = "CLICK_PLAY"
    CLICK_PLAY_AGAIN = "CLICK_PLAY_AGAIN"


CLICK_MESSAGES = {
    GuidanceStep.CLICK_MENU_TAB: "Clique para abrir as configurações",
    GuidanceStep.CLICK_LEAVE_MATCH: "Clique para retornar ao lobby",
    GuidanceStep.CLICK_PLAY: "Clique em Jogar para iniciar outra partida",
    GuidanceStep.CLICK_PLAY_AGAIN: "Clique em Jogar novamente",
}
BUTTON_LABELS = {
    NavigationButton.MENU_TAB: "Aba de configurações do menu",
    NavigationButton.LEAVE_MATCH: "Voltar ao Lobby",
    NavigationButton.PLAY: "Jogar",
    NavigationButton.PLAY_AGAIN: "Jogar novamente",
}


@dataclass(frozen=True)
class ButtonMatch:
    button: NavigationButton
    score: float
    center_x: int  # em pixels do quadro
    center_y: int


@dataclass(frozen=True)
class GuidanceContext:
    state: GameState  # tela confirmada
    frame_state: GameState  # tela reconhecida neste quadro
    menu_open: bool
    condition: SquadCondition
    in_match: bool
    match_number: int
    token_obtained: bool
    foreground: bool
    now: float


@dataclass(frozen=True)
class Notify:
    message: str
    button: NavigationButton | None = None  # o botão que o usuário deve clicar; sem ele, é só um aviso


@dataclass(frozen=True)
class MoveCursor:
    match: ButtonMatch


@dataclass(frozen=True)
class PressEscape:
    pass


@dataclass(frozen=True)
class ClickDone:
    """A tela mudou: o usuário deu o clique que estava sendo pedido."""

    button: NavigationButton


Command = Notify | MoveCursor | PressEscape | ClickDone
Locate = Callable[[NavigationButton], ButtonMatch | None]


class GuidedStateMachine:
    def __init__(self, config: AssistantConfig) -> None:
        self._config = config
        self.confirmation = ManualConfirmation(config.reminder_seconds, config.max_reminders)
        self._handlers = {
            GuidanceStep.IDLE: self._idle,
            GuidanceStep.COUNTDOWN: self._countdown,
            GuidanceStep.WAITING_FOR_MENU: self._waiting_for_menu,
            GuidanceStep.CLICK_MENU_TAB: self._click_menu_tab,
            GuidanceStep.CLICK_LEAVE_MATCH: self._click_leave_match,
            GuidanceStep.WAITING_FOR_LOBBY: self._waiting_for_lobby,
            GuidanceStep.CLICK_PLAY: self._click_play,
            GuidanceStep.CLICK_PLAY_AGAIN: self._click_play_again,
        }
        self.reset()

    def reset(self) -> None:
        """Esquece a orientação em andamento e o que já foi feito nas partidas anteriores."""
        self.step = GuidanceStep.IDLE
        self.message = ""
        self.button: ButtonMatch | None = None
        self.confirmation.clear()
        self._step_started_at = 0.0
        self._handled_match: int | None = None
        self._escape_match: int | None = None
        self._cursor_target: ButtonMatch | None = None
        self._menu_gone_frames = 0
        self._menu_seen_frames = 0
        self._menu_warning_sent = False
        self._searching = False
        self._token_notice_sent = False
        self._commands: list[Command] = []

    def update_config(self, config: AssistantConfig) -> None:
        self._config = config
        self.confirmation.reminder_seconds = config.reminder_seconds
        self.confirmation.max_reminders = config.max_reminders

    def update(self, context: GuidanceContext, locate: Locate) -> list[Command]:
        self._commands = []
        self._menu_seen_frames = self._menu_seen_frames + 1 if context.menu_open else 0
        self._handlers[self.step](context, locate)
        self._move_cursor_if_allowed(context)
        if self.confirmation.reminder_due(context.now):
            self._commands.append(Notify(self.message, self.button.button if self.button else None))
        return self._commands

    def _idle(self, context: GuidanceContext, locate: Locate) -> None:
        if context.state is GameState.IN_MATCH and self.confirmation.state is ConfirmationState.CONFIRMED:
            self.confirmation.clear()
            self.message = ""
        if self._searching:
            self._searching = False
            self.message = ""
        last_alive = context.condition is SquadCondition.LAST_SQUAD_MEMBER_ALIVE
        playing = context.state is GameState.IN_MATCH and context.frame_state is GameState.IN_MATCH
        if context.in_match and self._menu_seen_frames >= _MENU_SEEN_FRAMES:
            if last_alive:
                self._handled_match = context.match_number  # já está no menu: dispensa o Esc nesta partida
            self._advance_in_menu(context, locate)
        elif playing and last_alive and self._handled_match != context.match_number:
            self._handled_match = context.match_number
            self._start_leaving(context)
        elif context.state is GameState.MATCH_RESULT:
            self._offer_new_match(GuidanceStep.CLICK_PLAY_AGAIN, NavigationButton.PLAY_AGAIN, context, locate)
        elif context.state is GameState.LOBBY:
            self._offer_new_match(GuidanceStep.CLICK_PLAY, NavigationButton.PLAY, context, locate)

    def _start_leaving(self, context: GuidanceContext) -> None:
        if self._escape_match == context.match_number:
            self._ask_for_escape(context, "Você é o último vivo. Aperte Esc para abrir o menu.")
        elif self._config.press_escape:
            self._enter(GuidanceStep.COUNTDOWN, context)
            self._say(f"Você é o último vivo. O menu abre em {self._config.escape_delay_seconds:.0f} s.")
        else:
            self._ask_for_escape(context, "Você é o último vivo. Aperte Esc para abrir o menu.")

    def _ask_for_escape(self, context: GuidanceContext, message: str) -> None:
        self._enter(GuidanceStep.WAITING_FOR_MENU, context)
        self._menu_warning_sent = True
        self._say(message)
        self.confirmation.expect(context.now)

    def _countdown(self, context: GuidanceContext, locate: Locate) -> None:
        if context.menu_open:
            self._advance_in_menu(context, locate)
        elif not self._still_last_alive(context):
            self._give_up_on_leaving()
        elif context.now - self._step_started_at >= self._config.escape_delay_seconds:
            if context.foreground:
                self._escape_match = context.match_number
                self._commands.append(PressEscape())
                self._enter(GuidanceStep.WAITING_FOR_MENU, context)
                self.message = "Abrindo o menu..."
            else:
                self._ask_for_escape(context, "O jogo não está em primeiro plano. Aperte Esc para abrir o menu.")

    def _waiting_for_menu(self, context: GuidanceContext, locate: Locate) -> None:
        if context.menu_open:
            self._advance_in_menu(context, locate)
        elif not self._still_last_alive(context):
            self._give_up_on_leaving()
        elif not self._menu_warning_sent and context.now - self._step_started_at >= self._config.menu_timeout_seconds:
            # Não aperta de novo: se o menu só estivesse demorando, uma segunda tecla o fecharia.
            self._ask_for_escape(context, "O menu não abriu sozinho. Aperte Esc para continuar.")

    @staticmethod
    def _still_last_alive(context: GuidanceContext) -> bool:
        return context.state is GameState.IN_MATCH and context.condition is SquadCondition.LAST_SQUAD_MEMBER_ALIVE

    def _give_up_on_leaving(self) -> None:
        """Um colega voltou, ou a partida mudou: a saída pode voltar a valer se ele cair de novo."""
        self._handled_match = None
        self._finish("")

    def _advance_in_menu(self, context: GuidanceContext, locate: Locate) -> None:
        leave = locate(NavigationButton.LEAVE_MATCH)
        if leave is not None:
            self._enter_click(GuidanceStep.CLICK_LEAVE_MATCH, leave, context)
            return
        tab = locate(NavigationButton.MENU_TAB)
        if tab is not None:
            self._enter_click(GuidanceStep.CLICK_MENU_TAB, tab, context)
            return
        self._searching = True
        self.message = "Menu aberto, mas o botão não foi identificado. O mouse não será movido."

    def _click_menu_tab(self, context: GuidanceContext, locate: Locate) -> None:
        if not self._menu_still_open(context):
            return
        leave = locate(NavigationButton.LEAVE_MATCH)
        if leave is not None:
            self._confirm_click()
            self._enter_click(GuidanceStep.CLICK_LEAVE_MATCH, leave, context)

    def _click_leave_match(self, context: GuidanceContext, locate: Locate) -> None:
        if context.state is GameState.LOBBY:
            self._confirm_click()
            self._finish("Clique confirmado")
            self._idle(context, locate)
        elif context.state in _QUEUEING:
            self._wait_for_lobby(context)
        elif context.menu_open:
            self._menu_gone_frames = 0
            if locate(NavigationButton.LEAVE_MATCH) is None:
                self._advance_in_menu(context, locate)
        elif context.frame_state in _MATCH_GOES_ON:
            self._menu_still_open(context)
        else:
            self._menu_gone_frames += 1
            if self._menu_gone_frames >= _MENU_GONE_FRAMES:
                self._wait_for_lobby(context)

    def _wait_for_lobby(self, context: GuidanceContext) -> None:
        self._confirm_click()
        self._enter(GuidanceStep.WAITING_FOR_LOBBY, context)
        self.message = "Voltando ao lobby..."

    def _waiting_for_lobby(self, context: GuidanceContext, locate: Locate) -> None:
        if context.state in (GameState.LOBBY, GameState.MATCH_RESULT):
            self._finish("")
            self._idle(context, locate)
        elif context.state is GameState.IN_MATCH and context.frame_state is GameState.IN_MATCH:
            self._finish("Orientação interrompida: a partida continuou.")
        elif context.now - self._step_started_at >= _LOBBY_TIMEOUT_SECONDS:
            self._finish("O lobby não apareceu: orientação encerrada.")

    def _offer_new_match(
        self, step: GuidanceStep, button: NavigationButton, context: GuidanceContext, locate: Locate
    ) -> None:
        if context.token_obtained:
            if not self._token_notice_sent:
                self._token_notice_sent = True
                self._say("Token obtido: nenhuma nova partida será sugerida.")
            return
        match = locate(button)
        if match is None:
            self._searching = True
            self.message = f"Procurando o botão {BUTTON_LABELS[button]}. O mouse não será movido."
            return
        self._enter_click(step, match, context)

    def _click_play(self, context: GuidanceContext, locate: Locate) -> None:
        if context.state in _QUEUEING or context.state is GameState.IN_MATCH:
            self._confirm_click()
            self._finish("Clique confirmado")
        elif context.state is not GameState.LOBBY:
            self._finish("A tela mudou: orientação interrompida.")

    def _click_play_again(self, context: GuidanceContext, locate: Locate) -> None:
        if context.state is GameState.MATCHMAKING or context.state is GameState.IN_MATCH:
            self._confirm_click()
            self._finish("Clique confirmado")
        elif context.state is GameState.LOBBY or context.state is GameState.LOADING:
            # Jogar novamente vai direto para a fila: daqui, o usuário escolheu Retornar ao lobby.
            self._finish("")
            self._idle(context, locate)
        elif context.state is not GameState.MATCH_RESULT:
            self._finish("A tela mudou: orientação interrompida.")

    def _menu_still_open(self, context: GuidanceContext) -> bool:
        if context.menu_open:
            self._menu_gone_frames = 0
            return True
        self._menu_gone_frames += 1
        if self._menu_gone_frames >= _MENU_GONE_FRAMES:
            self._finish("O menu foi fechado: orientação interrompida.")
        return False

    def _enter(self, step: GuidanceStep, context: GuidanceContext) -> None:
        self.step = step
        self._searching = False
        self.button = None
        self._cursor_target = None
        self._step_started_at = context.now
        self._menu_gone_frames = 0
        self._menu_warning_sent = False
        if self.confirmation.state is ConfirmationState.WAITING:
            self.confirmation.clear()

    def _enter_click(self, step: GuidanceStep, match: ButtonMatch, context: GuidanceContext) -> None:
        self._enter(step, context)
        self.button = match
        self._cursor_target = match
        self.message = CLICK_MESSAGES[step]
        self._commands.append(Notify(self.message, match.button))
        self.confirmation.expect(context.now)

    def _confirm_click(self) -> None:
        self.confirmation.confirm()
        if self.button is not None:
            self._commands.append(ClickDone(self.button.button))

    def _finish(self, message: str) -> None:
        self.step = GuidanceStep.IDLE
        self._searching = False
        self.button = None
        self._cursor_target = None
        self.message = message
        if self.confirmation.state is ConfirmationState.WAITING:
            self.confirmation.clear()

    def _say(self, message: str) -> None:
        self.message = message
        self._commands.append(Notify(message))

    def _move_cursor_if_allowed(self, context: GuidanceContext) -> None:
        """Move uma vez por passo, para não disputar o mouse com o usuário, e nunca com o jogo em primeiro plano
        de ação: fora de um menu, o movimento giraria a câmera do personagem."""
        if self._cursor_target is None or not context.foreground:
            return
        if context.frame_state is GameState.IN_MATCH and not context.menu_open:
            return
        self._commands.append(MoveCursor(self._cursor_target))
        self._cursor_target = None
