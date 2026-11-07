from dataclasses import replace

import pytest

from assistant.guided_state_machine import (
    ButtonMatch,
    ClickDone,
    GuidanceContext,
    GuidanceStep,
    GuidedStateMachine,
    MoveCursor,
    NavigationButton as B,
    Notify,
    PressEscape,
)
from assistant.user_confirmation import ConfirmationState
from core.config import AssistantConfig
from core.state_machine import GameState as S
from recognition.survival_detector import SquadCondition as C

ACTIVE_CONFIG = AssistantConfig(press_escape=True, escape_delay_seconds=3, menu_timeout_seconds=6, reminder_seconds=20, max_reminders=2)


def match(button, x=100, y=200):
    return ButtonMatch(button, 0.95, x, y)


class Scene:
    """Monta o que a tela mostra a cada passo: tela, botões visíveis e condição do esquadrão."""

    def __init__(self, config=ACTIVE_CONFIG):
        self.machine = GuidedStateMachine(config)
        self.context = GuidanceContext(S.IN_MATCH, S.IN_MATCH, False, C.SQUAD_ACTIVE, True, 1, False, True, 0.0)
        self.visible = set()
        self.positions = {}  # botão: (x, y), quando não for a posição padrão

    def set(self, buttons=None, **changes):
        self.context = replace(self.context, **changes)
        if buttons is not None:
            self.visible = set(buttons)
        return self

    def tick(self, seconds=0.5):
        self.context = replace(self.context, now=self.context.now + seconds)
        return self.machine.update(
            self.context, lambda b: match(b, *self.positions.get(b, ())) if b in self.visible else None
        )

    @property
    def step(self):
        return self.machine.step


def of_type(commands, kind):
    return [command for command in commands if isinstance(command, kind)]


def messages(commands):
    return [command.message for command in of_type(commands, Notify)]


def moves(commands):
    return [command.match.button for command in of_type(commands, MoveCursor)]


def clicks_done(commands):
    return [command.button for command in of_type(commands, ClickDone)]


def in_game_menu(scene, buttons):
    return scene.set(buttons, state=S.GAME_MENU, frame_state=S.GAME_MENU, menu_open=True)


def last_alive(scene):
    return scene.set(condition=C.LAST_SQUAD_MEMBER_ALIVE)


def to_waiting_for_menu(scene):
    last_alive(scene).tick()
    return scene.tick(3.0)


def test_nothing_happens_while_the_squad_is_active():
    scene = Scene()
    assert scene.tick() == []
    assert scene.step is GuidanceStep.IDLE


@pytest.mark.parametrize("condition", [C.LAST_SQUAD_MEMBER_STANDING, C.SQUAD_ACTIVE, C.NO_SQUAD, C.UNKNOWN])
def test_only_the_last_one_alive_starts_leaving(condition):
    # Colegas caídos ainda podem ser levantados, e solo não tem esquadrão.
    scene = Scene().set(condition=condition)
    assert scene.tick() == []
    assert scene.step is GuidanceStep.IDLE


def test_last_alive_warns_before_pressing_escape():
    scene = last_alive(Scene())

    first = scene.tick()
    assert scene.step is GuidanceStep.COUNTDOWN
    assert messages(first) == ["Você é o último vivo. O menu abre em 3 s."]
    assert not of_type(first, PressEscape)

    assert not of_type(scene.tick(1.0), PressEscape)


def test_escape_is_pressed_once_after_the_delay():
    scene = last_alive(Scene())
    scene.tick()

    pressed = scene.tick(3.0)
    assert len(of_type(pressed, PressEscape)) == 1
    assert scene.step is GuidanceStep.WAITING_FOR_MENU

    for _ in range(30):
        assert not of_type(scene.tick(1.0), PressEscape)


def test_escape_is_off_by_default_and_the_user_is_asked_instead():
    assert AssistantConfig().press_escape is False
    scene = last_alive(Scene(AssistantConfig()))

    commands = scene.tick()

    assert scene.step is GuidanceStep.WAITING_FOR_MENU
    assert messages(commands) == ["Você é o último vivo. Aperte Esc para abrir o menu."]
    for _ in range(40):
        assert not of_type(scene.tick(1.0), PressEscape)


def test_escape_is_not_pressed_with_the_game_in_the_background():
    scene = last_alive(Scene())
    scene.tick()
    scene.set(foreground=False)

    commands = scene.tick(3.0)

    assert not of_type(commands, PressEscape)
    assert "primeiro plano" in messages(commands)[0]
    assert scene.step is GuidanceStep.WAITING_FOR_MENU


def test_teammate_coming_back_cancels_before_the_key_and_can_start_again():
    scene = last_alive(Scene())
    scene.tick()

    scene.set(condition=C.SQUAD_ACTIVE)
    assert not of_type(scene.tick(3.0), PressEscape)
    assert scene.step is GuidanceStep.IDLE

    last_alive(scene)
    scene.tick()
    assert scene.step is GuidanceStep.COUNTDOWN


def test_full_way_to_the_lobby_waits_for_each_manual_click():
    scene = Scene()
    to_waiting_for_menu(scene)

    in_game_menu(scene, {B.MENU_TAB})
    opened = scene.tick()
    assert scene.step is GuidanceStep.CLICK_MENU_TAB
    assert moves(opened) == [B.MENU_TAB]
    assert messages(opened) == ["Clique para abrir as configurações"]
    assert scene.machine.confirmation.state is ConfirmationState.WAITING

    # Sem o clique, nada avança e o cursor não é movido de novo.
    assert moves(scene.tick()) == []
    assert scene.step is GuidanceStep.CLICK_MENU_TAB

    in_game_menu(scene, {B.LEAVE_MATCH})
    leaving = scene.tick()
    assert scene.step is GuidanceStep.CLICK_LEAVE_MATCH
    assert moves(leaving) == [B.LEAVE_MATCH]
    assert messages(leaving) == ["Clique para retornar ao lobby"]
    assert scene.machine.confirmation.state is ConfirmationState.WAITING

    scene.set(set(), state=S.LOADING, frame_state=S.LOADING, menu_open=False)
    scene.tick()
    assert scene.step is GuidanceStep.WAITING_FOR_LOBBY
    assert scene.machine.confirmation.state is ConfirmationState.CONFIRMED

    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    lobby = scene.tick()
    assert scene.step is GuidanceStep.CLICK_PLAY
    assert moves(lobby) == [B.PLAY]
    assert messages(lobby) == ["Clique em Jogar para iniciar outra partida"]

    scene.set(set(), state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    scene.tick()
    assert scene.step is GuidanceStep.IDLE
    assert scene.machine.confirmation.state is ConfirmationState.CONFIRMED


def test_no_command_ever_clicks():
    # O assistente só conhece avisar, mover o cursor e apertar Esc.
    assert Command_types() == {Notify, MoveCursor, PressEscape, ClickDone}


def Command_types():
    from typing import get_args

    from assistant.guided_state_machine import Command

    return set(get_args(Command))


def test_menu_already_on_the_right_tab_skips_a_step():
    scene = Scene()
    to_waiting_for_menu(scene)

    in_game_menu(scene, {B.LEAVE_MATCH, B.MENU_TAB})
    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_LEAVE_MATCH
    assert moves(commands) == [B.LEAVE_MATCH]


def test_opening_the_menu_by_hand_during_the_countdown_skips_the_key():
    scene = last_alive(Scene())
    scene.tick()

    in_game_menu(scene, {B.MENU_TAB})
    commands = scene.tick()

    assert not of_type(commands, PressEscape)
    assert scene.step is GuidanceStep.CLICK_MENU_TAB


def test_button_not_recognized_means_no_mouse_movement():
    scene = Scene()
    to_waiting_for_menu(scene)

    in_game_menu(scene, set())
    commands = scene.tick()

    assert moves(commands) == []
    assert scene.step is GuidanceStep.WAITING_FOR_MENU
    assert "não será movido" in scene.machine.message


def test_cursor_waits_for_the_game_to_be_in_the_foreground():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.MENU_TAB}).set(foreground=False)

    away = scene.tick()
    assert moves(away) == []
    assert messages(away) == ["Clique para abrir as configurações"]

    scene.set(foreground=True)
    assert moves(scene.tick()) == [B.MENU_TAB]
    assert moves(scene.tick()) == []


def test_cursor_is_never_moved_during_gameplay():
    # O movimento giraria a câmera do personagem.
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.MENU_TAB}).set(foreground=False)
    scene.tick()

    scene.set(frame_state=S.IN_MATCH, menu_open=False, foreground=True)
    assert moves(scene.tick()) == []


def test_closing_the_menu_interrupts_and_does_not_insist_in_the_same_match():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.MENU_TAB})
    scene.tick()

    scene.set(set(), state=S.IN_MATCH, frame_state=S.IN_MATCH, menu_open=False)
    for _ in range(3):
        scene.tick()
    assert scene.step is GuidanceStep.IDLE
    assert "interrompida" in scene.machine.message

    for _ in range(20):
        commands = scene.tick(1.0)
        assert not of_type(commands, PressEscape)
        assert scene.step is GuidanceStep.IDLE


def test_one_frame_without_the_menu_does_not_interrupt():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.MENU_TAB})
    scene.tick()

    scene.set(menu_open=False)
    scene.tick()
    in_game_menu(scene, {B.MENU_TAB})
    scene.tick()

    assert scene.step is GuidanceStep.CLICK_MENU_TAB


def test_next_match_can_start_leaving_again():
    scene = Scene()
    to_waiting_for_menu(scene)
    scene.set(condition=C.SQUAD_ACTIVE, match_number=2)
    scene.tick()
    assert scene.step is GuidanceStep.IDLE

    last_alive(scene)
    scene.tick()
    assert scene.step is GuidanceStep.COUNTDOWN


def test_menu_that_does_not_open_asks_the_user_without_pressing_again():
    scene = Scene()
    to_waiting_for_menu(scene)

    silent = scene.tick(5.0)
    assert messages(silent) == []

    commands = scene.tick(1.5)
    assert not of_type(commands, PressEscape)
    assert messages(commands) == ["O menu não abriu sozinho. Aperte Esc para continuar."]
    assert scene.machine.confirmation.state is ConfirmationState.WAITING


def test_going_back_to_another_tab_returns_to_the_tab_step():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.LEAVE_MATCH})
    scene.tick()

    in_game_menu(scene, {B.MENU_TAB})
    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_MENU_TAB
    assert moves(commands) == [B.MENU_TAB]


def test_transition_frames_after_leaving_count_as_the_click():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.LEAVE_MATCH})
    scene.tick()

    scene.set(set(), state=S.GAME_MENU, frame_state=S.UNKNOWN, menu_open=False)
    for _ in range(3):
        scene.tick()

    assert scene.step is GuidanceStep.WAITING_FOR_LOBBY


def test_lobby_never_appearing_ends_the_guidance():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.LEAVE_MATCH})
    scene.tick()
    scene.set(set(), state=S.LOADING, frame_state=S.LOADING, menu_open=False)
    scene.tick()

    scene.tick(121.0)

    assert scene.step is GuidanceStep.IDLE


@pytest.mark.parametrize("final_state", [S.VICTORY, S.DEFEAT, S.ELIMINATED, S.IN_MATCH])
def test_result_screen_points_to_play_again_after_any_ending(final_state):
    scene = Scene().set(state=final_state, frame_state=final_state)
    scene.tick()

    scene.set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT)
    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_PLAY_AGAIN
    assert moves(commands) == [B.PLAY_AGAIN]
    assert messages(commands) == ["Clique em Jogar novamente"]
    assert scene.machine.confirmation.state is ConfirmationState.WAITING


def after_a_match():
    scene = Scene()
    scene.tick()
    return scene


def test_play_again_clicked_finishes_and_does_not_repeat():
    scene = after_a_match().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT)
    scene.tick()

    scene.set(set(), state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    scene.tick()

    assert scene.step is GuidanceStep.IDLE
    assert scene.machine.confirmation.state is ConfirmationState.CONFIRMED
    assert scene.tick() == []


def test_result_screen_points_to_play_again_even_without_having_seen_the_match():
    scene = Scene().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT, in_match=False)
    assert moves(scene.tick()) == [B.PLAY_AGAIN]


def test_going_to_the_lobby_from_the_result_points_to_play():
    scene = after_a_match().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT)
    scene.tick()

    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_PLAY
    assert moves(commands) == [B.PLAY]


def test_lobby_points_to_play_as_soon_as_the_app_sees_it():
    # O primeiro lobby depois de abrir o app também conta: não é preciso ter passado por uma partida.
    scene = Scene().set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY, in_match=False)

    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_PLAY
    assert moves(commands) == [B.PLAY]
    assert messages(commands) == ["Clique em Jogar para iniciar outra partida"]


def test_coming_back_to_the_lobby_points_to_play_again():
    scene = Scene().set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY, in_match=False)
    scene.tick()

    # O usuário entrou na fila e cancelou.
    scene.set(state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    scene.tick()
    scene.set(state=S.LOBBY, frame_state=S.LOBBY)

    assert moves(scene.tick()) == [B.PLAY]


def test_lobby_without_the_button_on_screen_keeps_looking_without_moving():
    scene = Scene().set(set(), state=S.LOBBY, frame_state=S.LOBBY, in_match=False)

    assert scene.tick() == []
    assert "não será movido" in scene.machine.message

    scene.set({B.PLAY})
    assert moves(scene.tick()) == [B.PLAY]


def test_search_message_does_not_outlive_the_screen():
    scene = Scene().set(set(), state=S.LOBBY, frame_state=S.LOBBY, in_match=False)
    scene.tick()

    scene.set(state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    scene.tick()

    assert scene.machine.message == ""


def open_menu_by_hand(scene, buttons, **changes):
    """O menu visível por dois quadros, como a tela de reconhecimento informa."""
    in_game_menu(scene, buttons).set(**changes)
    return scene.tick() + scene.tick()


def test_opening_the_menu_during_the_match_starts_the_way_to_a_new_match():
    scene = Scene()

    commands = open_menu_by_hand(scene, {B.MENU_TAB})

    assert scene.step is GuidanceStep.CLICK_MENU_TAB
    assert moves(commands) == [B.MENU_TAB]
    assert messages(commands) == ["Clique para abrir as configurações"]
    assert not of_type(commands, PressEscape)


def test_menu_seen_in_a_single_frame_does_not_move_the_cursor():
    # Um reconhecimento errado com o jogo na tela giraria a câmera.
    scene = Scene()
    in_game_menu(scene, {B.MENU_TAB})

    assert scene.tick() == []
    assert scene.step is GuidanceStep.IDLE


def test_menu_over_the_spectator_mode_also_starts_the_way():
    # Sobre o modo espectador, a tela reconhecida continua sendo a de eliminado.
    scene = Scene().set(state=S.ELIMINATED, frame_state=S.ELIMINATED)

    commands = open_menu_by_hand(scene, {B.LEAVE_MATCH}, state=S.ELIMINATED, frame_state=S.ELIMINATED)

    assert scene.step is GuidanceStep.CLICK_LEAVE_MATCH
    assert moves(commands) == [B.LEAVE_MATCH]


def test_closing_the_menu_while_spectating_is_not_a_click():
    scene = Scene().set(state=S.ELIMINATED, frame_state=S.ELIMINATED)
    open_menu_by_hand(scene, {B.LEAVE_MATCH}, state=S.ELIMINATED, frame_state=S.ELIMINATED)

    scene.set(set(), menu_open=False)
    done = []
    for _ in range(5):
        done += clicks_done(scene.tick())

    assert done == []
    assert scene.step is GuidanceStep.IDLE
    assert "interrompida" in scene.machine.message


def test_menu_outside_a_match_starts_nothing():
    # No lobby, o mesmo painel abre, mas não há partida de onde voltar.
    scene = Scene().set(in_match=False)

    assert open_menu_by_hand(scene, {B.MENU_TAB}, in_match=False) == []
    assert scene.step is GuidanceStep.IDLE


def test_opening_the_menu_again_starts_over_each_time():
    scene = Scene()
    open_menu_by_hand(scene, {B.MENU_TAB})
    scene.set(set(), state=S.IN_MATCH, frame_state=S.IN_MATCH, menu_open=False)
    for _ in range(3):
        scene.tick()
    assert scene.step is GuidanceStep.IDLE

    assert moves(open_menu_by_hand(scene, {B.MENU_TAB})) == [B.MENU_TAB]


def test_menu_opened_by_hand_when_alone_dispenses_the_key_in_that_match():
    scene = last_alive(Scene()).set(state=S.GAME_MENU, frame_state=S.GAME_MENU, menu_open=True)
    scene.visible = {B.MENU_TAB}
    scene.tick()
    scene.tick()
    assert scene.step is GuidanceStep.CLICK_MENU_TAB

    scene.set(set(), state=S.IN_MATCH, frame_state=S.IN_MATCH, menu_open=False)
    for _ in range(30):
        assert not of_type(scene.tick(1.0), PressEscape)


def test_escape_is_never_pressed_twice_in_the_same_match():
    scene = Scene()
    to_waiting_for_menu(scene)

    # O colega voltou depois da tecla e caiu de novo: o aviso volta, mas a tecla não.
    scene.set(condition=C.SQUAD_ACTIVE)
    scene.tick()
    last_alive(scene)
    commands = scene.tick()
    assert messages(commands) == ["Você é o último vivo. Aperte Esc para abrir o menu."]

    for _ in range(20):
        assert not of_type(scene.tick(1.0), PressEscape)


def test_escape_can_be_pressed_again_in_the_next_match():
    scene = Scene()
    to_waiting_for_menu(scene)
    scene.set(condition=C.SQUAD_ACTIVE, match_number=2)
    scene.tick()

    last_alive(scene)
    scene.tick()

    assert of_type(scene.tick(3.0), PressEscape)


def test_cursor_moves_only_once_per_lobby():
    scene = Scene()
    scene.tick()
    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    assert moves(scene.tick()) == [B.PLAY]
    for _ in range(10):
        assert moves(scene.tick()) == []


def test_token_obtained_stops_suggesting_new_matches():
    scene = Scene()
    scene.tick()
    scene.set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT, token_obtained=True)

    commands = scene.tick()

    assert moves(commands) == []
    assert messages(commands) == ["Token obtido: nenhuma nova partida será sugerida."]
    assert scene.step is GuidanceStep.IDLE
    # O aviso não pode se repetir a cada análise enquanto a tela de resultado estiver aberta.
    for _ in range(10):
        assert scene.tick() == []


def test_reminders_repeat_the_message_up_to_the_limit():
    scene = Scene()
    scene.tick()
    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    scene.tick()

    notified = [messages(scene.tick(20.0)) for _ in range(5)]

    assert notified[:2] == [["Clique em Jogar para iniciar outra partida"]] * 2
    assert notified[2:] == [[], [], []]


def test_confirmation_is_cleared_when_the_next_match_starts():
    scene = after_a_match().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT)
    scene.tick()
    scene.set(set(), state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    scene.tick()
    assert scene.machine.message == "Clique confirmado"

    scene.set(state=S.IN_MATCH, frame_state=S.IN_MATCH)
    scene.tick()

    assert scene.machine.message == ""
    assert scene.machine.confirmation.state is ConfirmationState.NONE


def test_reset_forgets_everything():
    scene = Scene()
    to_waiting_for_menu(scene)
    scene.machine.reset()
    assert scene.step is GuidanceStep.IDLE
    assert scene.machine.confirmation.state is ConfirmationState.NONE


def buttons_of(commands):
    return [command.button for command in of_type(commands, Notify)]


def test_click_steps_name_their_button():
    scene = Scene()
    scene.tick()
    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)

    assert buttons_of(scene.tick()) == [B.PLAY]


def test_reminder_keeps_the_button():
    scene = Scene()
    scene.tick()
    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    scene.tick()

    assert buttons_of(scene.tick(20.0)) == [B.PLAY]


def test_notices_that_ask_for_no_click_have_no_button():
    scene = Scene()

    assert buttons_of(last_alive(scene).tick()) == [None]


def test_a_full_way_to_the_lobby_asks_for_each_click_once():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.MENU_TAB})
    clicks = buttons_of(scene.tick())
    scene.set({B.LEAVE_MATCH})
    clicks += buttons_of(scene.tick())
    scene.tick()
    scene.set(set(), state=S.LOADING, frame_state=S.LOADING, menu_open=False)
    clicks += buttons_of(scene.tick())
    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    clicks += buttons_of(scene.tick())
    scene.tick()

    assert clicks == [B.MENU_TAB, B.LEAVE_MATCH, B.PLAY]


def test_a_click_is_only_reported_after_the_screen_changes():
    scene = Scene()
    in_game_menu(scene, {B.MENU_TAB})
    done = clicks_done(scene.tick()) + clicks_done(scene.tick())
    for _ in range(5):
        done += clicks_done(scene.tick())
    assert done == []

    in_game_menu(scene, {B.LEAVE_MATCH})
    assert clicks_done(scene.tick()) == [B.MENU_TAB]
    for _ in range(5):
        assert clicks_done(scene.tick()) == []

    scene.set(set(), state=S.LOADING, frame_state=S.LOADING, menu_open=False)
    assert clicks_done(scene.tick()) == [B.LEAVE_MATCH]

    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    assert clicks_done(scene.tick()) == []
    for _ in range(5):
        assert clicks_done(scene.tick()) == []

    scene.set(set(), state=S.MATCHMAKING, frame_state=S.MATCHMAKING)
    assert clicks_done(scene.tick()) == [B.PLAY]


def test_play_again_is_reported_when_the_queue_starts():
    scene = Scene().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT, in_match=False)
    scene.tick()
    assert clicks_done(scene.tick()) == []

    scene.set(set(), state=S.MATCHMAKING, frame_state=S.MATCHMAKING)

    assert clicks_done(scene.tick()) == [B.PLAY_AGAIN]


@pytest.mark.parametrize("next_state", [S.LOADING, S.LOBBY])
def test_leaving_the_result_by_another_button_is_not_play_again(next_state):
    # Retornar ao lobby passa pelo carregamento; Jogar novamente vai direto para a fila.
    scene = Scene().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT, in_match=False)
    scene.tick()

    scene.set(set(), state=next_state, frame_state=next_state)

    assert clicks_done(scene.tick()) == []


def test_unanswered_screens_never_report_a_click():
    scene = Scene().set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY, in_match=False)
    done = []
    for _ in range(100):
        done += clicks_done(scene.tick(1.0))
    assert done == []


REWARD = {"state": S.REWARD_CLAIM, "frame_state": S.REWARD_CLAIM, "in_match": False}


def reward_screen(buttons):
    return Scene().set(buttons, **REWARD)


def test_single_reward_points_to_claim():
    scene = reward_screen({B.CLAIM})

    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_CLAIM
    assert moves(commands) == [B.CLAIM]
    assert messages(commands) == ["Clique em Resgatar para receber a recompensa"]
    assert buttons_of(commands) == [B.CLAIM]
    assert scene.machine.confirmation.state is ConfirmationState.WAITING


def test_more_than_one_reward_points_to_claim_all():
    # O texto de Resgatar tudo começa com Resgatar, então os dois podem ser encontrados no mesmo botão.
    scene = reward_screen({B.CLAIM, B.CLAIM_ALL})

    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_CLAIM_ALL
    assert moves(commands) == [B.CLAIM_ALL]
    assert messages(commands) == ["Clique em Resgatar tudo para receber as recompensas"]


def test_reward_without_the_button_on_screen_keeps_looking_without_moving():
    scene = reward_screen(set())

    assert scene.tick() == []
    assert "não será movido" in scene.machine.message

    scene.set({B.CLAIM})
    assert moves(scene.tick()) == [B.CLAIM]


def test_cursor_moves_only_once_per_reward():
    scene = reward_screen({B.CLAIM})
    scene.tick()

    for _ in range(10):
        commands = scene.tick()
        assert moves(commands) == []
        assert clicks_done(commands) == []
    assert scene.step is GuidanceStep.CLICK_CLAIM


def test_claiming_the_last_reward_leads_to_play():
    scene = reward_screen({B.CLAIM_ALL})
    scene.tick()

    scene.set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY)
    commands = scene.tick()

    assert clicks_done(commands) == [B.CLAIM_ALL]
    assert scene.step is GuidanceStep.CLICK_PLAY
    assert moves(commands) == [B.PLAY]


def test_next_reward_after_the_animation_is_offered_again():
    scene = reward_screen({B.CLAIM})
    scene.tick()

    # Entre uma recompensa e a seguinte, o botão some por alguns quadros, ainda com a tela de recompensa.
    scene.set(set(), frame_state=S.UNKNOWN)
    assert clicks_done(scene.tick() + scene.tick()) == []
    assert scene.step is GuidanceStep.CLICK_CLAIM

    assert clicks_done(scene.tick()) == [B.CLAIM]
    assert scene.step is GuidanceStep.IDLE

    scene.set({B.CLAIM}, frame_state=S.REWARD_CLAIM)
    commands = scene.tick()
    assert scene.step is GuidanceStep.CLICK_CLAIM
    assert moves(commands) == [B.CLAIM]


def test_button_flickering_for_a_frame_is_not_a_click():
    scene = reward_screen({B.CLAIM})
    scene.tick()

    scene.set(set())
    scene.tick()
    scene.set({B.CLAIM})
    scene.tick()
    scene.set(set())
    scene.tick()
    scene.set({B.CLAIM})

    assert clicks_done(scene.tick()) == []
    assert scene.step is GuidanceStep.CLICK_CLAIM


def test_reward_replaced_without_a_pause_moves_the_cursor_again():
    # Uma recompensa sem Equipar mostra Resgatar no centro; a seguinte, com Equipar, à direita.
    scene = reward_screen({B.CLAIM})
    scene.positions = {B.CLAIM: (960, 986)}
    scene.tick()

    scene.positions = {B.CLAIM: (1128, 986)}
    commands = scene.tick()

    assert clicks_done(commands) == [B.CLAIM]
    assert moves(commands) == [B.CLAIM]
    assert scene.machine.button.center_x == 1128


def test_claim_all_appearing_after_a_single_claim_is_another_reward():
    scene = reward_screen({B.CLAIM})
    scene.tick()

    scene.set({B.CLAIM_ALL})
    commands = scene.tick()

    assert clicks_done(commands) == [B.CLAIM]
    assert scene.step is GuidanceStep.CLICK_CLAIM_ALL
    assert moves(commands) == [B.CLAIM_ALL]


def test_small_shifts_of_the_same_button_are_the_same_reward():
    scene = reward_screen({B.CLAIM})
    scene.positions = {B.CLAIM: (1128, 986)}
    scene.tick()

    scene.positions = {B.CLAIM: (1130, 984)}

    assert scene.tick() == []


def test_rewards_after_leaving_the_match_come_before_play():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.LEAVE_MATCH})
    scene.tick()
    scene.set(set(), state=S.LOADING, frame_state=S.LOADING, menu_open=False)
    scene.tick()
    assert scene.step is GuidanceStep.WAITING_FOR_LOBBY

    scene.set({B.CLAIM}, **REWARD)
    commands = scene.tick()

    assert scene.step is GuidanceStep.CLICK_CLAIM
    assert moves(commands) == [B.CLAIM]


def test_reward_right_after_leaving_counts_as_the_click_on_leave():
    scene = Scene()
    to_waiting_for_menu(scene)
    in_game_menu(scene, {B.LEAVE_MATCH})
    scene.tick()

    scene.set({B.CLAIM}, **REWARD, menu_open=False)
    commands = scene.tick()

    assert clicks_done(commands) == [B.LEAVE_MATCH]
    assert scene.step is GuidanceStep.CLICK_CLAIM


def test_reward_opening_over_the_lobby_comes_before_play():
    scene = Scene().set({B.PLAY}, state=S.LOBBY, frame_state=S.LOBBY, in_match=False)
    scene.tick()

    scene.set({B.CLAIM}, **REWARD)
    commands = scene.tick()

    assert clicks_done(commands) == []
    assert scene.step is GuidanceStep.CLICK_CLAIM
    assert moves(commands) == [B.CLAIM]
    assert "interrompida" not in scene.machine.message


def test_reward_after_the_result_is_not_play_again():
    scene = Scene().set({B.PLAY_AGAIN}, state=S.MATCH_RESULT, frame_state=S.MATCH_RESULT, in_match=False)
    scene.tick()

    scene.set({B.CLAIM_ALL}, **REWARD)
    commands = scene.tick()

    assert clicks_done(commands) == []
    assert scene.step is GuidanceStep.CLICK_CLAIM_ALL
