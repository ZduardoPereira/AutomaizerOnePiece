import sys
import types
from pathlib import Path

import cv2
import numpy as np
import pytest

from assistant import mouse_guidance
from assistant.guided_state_machine import ButtonMatch, GuidanceContext, GuidanceStep, NavigationButton as B
from assistant.mouse_guidance import CursorCornerStop, MouseGuidance
from assistant.navigation_controller import ButtonLocator, NavigationController
from core.capture import WindowInfo
from core.config import AssistantConfig
from core.event_manager import EventManager, EventType
from core.paths import TEMPLATES_DIR
from core.state_machine import GameState as S
from recognition.interface_detector import read_image
from recognition.survival_detector import SquadCondition as C
from ui.guidance_overlay import hint_position
from assistant.navigation_controller import GuidanceHint

SCREENS_DIR = Path(__file__).parent / "fixtures" / "screens"
WINDOW = WindowInfo(77, "Fortnite", "jogo.exe", 1920, 10, 1920, 1080, False, True)
FRAME = np.zeros((1080, 1920, 3), dtype=np.uint8)


class FakeLocator:
    def __init__(self):
        self.visible = {}
        self.threshold = None

    def locate(self, _frame, button):
        return self.visible.get(button)

    def update_threshold(self, threshold):
        self.threshold = threshold


class FakeMouse:
    def __init__(self):
        self.moves = []
        self.escapes = []
        self.move_error = None
        self.escape_works = True

    def move_to(self, x, y, duration):
        if self.move_error is not None:
            raise self.move_error
        self.moves.append((x, y, duration))

    def press_escape(self, hwnd):
        self.escapes.append(hwnd)
        return self.escape_works


class FakeOutbox:
    def __init__(self):
        self.sent = []

    def publish(self, kind, message, button=None):
        self.sent.append((kind, message, button))


def context(**changes):
    values = {
        "state": S.IN_MATCH,
        "frame_state": S.IN_MATCH,
        "menu_open": False,
        "condition": C.SQUAD_ACTIVE,
        "in_match": False,
        "match_number": 1,
        "token_obtained": False,
        "foreground": True,
        "now": 0.0,
    }
    return GuidanceContext(**{**values, **changes})


LOBBY = {"state": S.LOBBY, "frame_state": S.LOBBY}


@pytest.fixture
def harness():
    events = EventManager()
    locator = FakeLocator()
    mouse = FakeMouse()
    config = AssistantConfig(press_escape=True, escape_delay_seconds=3, cursor_move_seconds=0.1)
    controller = NavigationController(config, events, locator, mouse, FakeOutbox())
    return controller, events, locator, mouse


def notifications(events):
    return [event.message for event in events.recent(50, {EventType.ASSISTANT_NOTIFY})][::-1]


def to_lobby_step(controller, locator):
    controller.update(FRAME, context(), WINDOW)
    locator.visible = {B.PLAY: ButtonMatch(B.PLAY, 0.97, 260, 913)}
    controller.update(FRAME, context(**LOBBY), WINDOW)


def test_starts_off_and_does_nothing_until_activated(harness):
    controller, events, locator, mouse = harness
    assert not controller.status().active

    locator.visible = {B.PLAY: ButtonMatch(B.PLAY, 0.97, 260, 913)}
    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE), WINDOW)
    controller.update(FRAME, context(**LOBBY), WINDOW)

    assert mouse.moves == []
    assert mouse.escapes == []
    assert notifications(events) == []


def test_cursor_goes_to_the_button_in_screen_coordinates(harness):
    controller, events, locator, mouse = harness
    controller.activate()

    to_lobby_step(controller, locator)

    # A janela do jogo pode estar em outro monitor.
    assert mouse.moves == [(1920 + 260, 10 + 913, 0.1)]
    assert notifications(events) == ["Clique em Jogar para iniciar outra partida"]


def test_status_reports_what_the_page_needs(harness):
    controller, _events, locator, _mouse = harness
    controller.activate()
    to_lobby_step(controller, locator)

    status = controller.status()

    assert status.active
    assert status.step is GuidanceStep.CLICK_PLAY
    assert status.next_action == "Clique em Jogar para iniciar outra partida"
    assert status.button is B.PLAY
    assert status.button_score == 0.97
    assert status.hint.text == status.next_action
    assert (status.hint.x, status.hint.y) == (2180, 923)


def test_escape_goes_to_the_game_window(harness):
    controller, _events, _locator, mouse = harness
    controller.activate()
    last_alive = context(condition=C.LAST_SQUAD_MEMBER_ALIVE)

    controller.update(FRAME, last_alive, WINDOW)
    assert mouse.escapes == []

    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE, now=3.0), WINDOW)
    assert mouse.escapes == [77]


def test_refused_escape_asks_the_user(harness):
    controller, events, _locator, mouse = harness
    mouse.escape_works = False
    controller.activate()

    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE), WINDOW)
    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE, now=3.0), WINDOW)

    assert "Não consegui abrir o menu. Aperte Esc." in notifications(events)


def test_stop_ends_everything_and_hides_the_hint(harness):
    controller, events, locator, mouse = harness
    controller.activate()
    to_lobby_step(controller, locator)
    assert controller.status().hint is not None

    controller.stop()

    status = controller.status()
    assert not status.active
    assert status.step is GuidanceStep.IDLE
    assert status.hint is None
    assert "Assistência desligada" in [event.message for event in events.recent(20)]

    controller.update(FRAME, context(**LOBBY), WINDOW)
    assert len(mouse.moves) == 1


def test_mouse_in_a_corner_turns_the_assistance_off_and_says_so(harness):
    controller, events, locator, mouse = harness
    controller.activate()
    mouse.move_error = CursorCornerStop()

    to_lobby_step(controller, locator)

    assert not controller.status().active
    assert "canto da tela" in events.recent(1, {EventType.ASSISTANT_STATE_CHANGED})[0].message
    # Sem o aviso, a assistência pararia em silêncio no meio da partida.
    assert "canto da tela" in notifications(events)[-1]


def test_other_cursor_failure_warns_and_keeps_the_assistance_on(harness):
    controller, events, locator, mouse = harness
    controller.activate()
    mouse.move_error = RuntimeError("falha qualquer")

    to_lobby_step(controller, locator)

    assert controller.status().active
    assert notifications(events)[-1] == "Não consegui levar o mouse até o botão."


def test_game_closed_forgets_the_step_but_stays_active(harness):
    controller, _events, locator, _mouse = harness
    controller.activate()
    to_lobby_step(controller, locator)

    controller.forget_game()

    assert controller.status().active
    assert controller.status().step is GuidanceStep.IDLE


def test_update_config_reaches_escape_option_and_locator(harness):
    controller, _events, locator, mouse = harness
    controller.update_config(AssistantConfig(press_escape=False, button_threshold=0.9))
    controller.activate()

    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE), WINDOW)
    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE, now=10.0), WINDOW)

    assert mouse.escapes == []
    assert locator.threshold == 0.9


def test_escape_is_refused_outside_the_game(monkeypatch):
    sent = []
    monkeypatch.setattr(mouse_guidance, "is_window_foreground", lambda _hwnd: False)
    monkeypatch.setattr(mouse_guidance, "_send_key", lambda flags: sent.append(flags) or True)

    assert MouseGuidance().press_escape(123) is False
    assert sent == []


def test_escape_presses_and_releases_the_key(monkeypatch):
    sent = []
    monkeypatch.setattr(mouse_guidance, "is_window_foreground", lambda _hwnd: True)
    monkeypatch.setattr(mouse_guidance, "_send_key", lambda flags: sent.append(flags) or True)

    assert MouseGuidance().press_escape(123) is True
    assert sent == [0, mouse_guidance._KEYEVENTF_KEYUP]


def test_mouse_guidance_has_no_way_to_click():
    public = {name for name in dir(MouseGuidance) if not name.startswith("_")}
    assert public == {"move_to", "press_escape"}
    assert "click" not in Path(mouse_guidance.__file__).read_text(encoding="utf-8").replace("clique", "").lower()


def test_pyautogui_corner_refusal_becomes_our_own_stop(monkeypatch):
    class FailSafe(Exception):
        pass

    def refuse(_x, _y, duration):
        raise FailSafe

    fake = types.SimpleNamespace(PAUSE=0.1, moveTo=refuse, FailSafeException=FailSafe)
    monkeypatch.setitem(sys.modules, "pyautogui", fake)

    with pytest.raises(CursorCornerStop):
        MouseGuidance().move_to(10, 20, 0.2)


def test_move_uses_pyautogui_without_extra_pause(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(PAUSE=0.1, moveTo=lambda x, y, duration: calls.append((x, y, duration)))
    monkeypatch.setitem(sys.modules, "pyautogui", fake)

    MouseGuidance().move_to(10, 20, 0.2)

    assert calls == [(10, 20, 0.2)]
    assert fake.PAUSE == 0


@pytest.mark.parametrize(
    ("side", "expected"),
    [("left", (1000 - 200 - 84, 500 - 20)), ("right", (1000 + 84, 500 - 20)), ("below", (1000 - 100, 500 + 28))],
)
def test_hint_stays_beside_the_button(side, expected):
    assert hint_position(GuidanceHint("x", 1000, 500, side), 200, 40) == expected


def test_missing_button_templates_are_reported(tmp_path):
    assert len(ButtonLocator(tmp_path, 0.85, 540).warnings) == 4
    assert ButtonLocator(TEMPLATES_DIR, 0.85, 540).warnings == []
    assert ButtonLocator(tmp_path, 0.85, 540).locate(FRAME, B.PLAY) is None


EXPECTED_BUTTONS = {
    B.MENU_TAB: ("eliminated_spectator_social_panel", (1678, 71)),
    B.LEAVE_MATCH: ("eliminated_spectator_menu_panel", (1562, 174)),
    B.PLAY: ("lobby_idle", (260, 913)),
    B.PLAY_AGAIN: ("result_victory", (1580, 653)),
}
needs_prints = pytest.mark.skipif(
    not (SCREENS_DIR / "lobby_idle.png").exists(), reason="Prints do jogo indisponíveis em tests/fixtures/screens"
)


@pytest.fixture(scope="module")
def locator():
    return ButtonLocator(TEMPLATES_DIR, AssistantConfig().button_threshold, 540)


@needs_prints
@pytest.mark.parametrize("button", EXPECTED_BUTTONS)
def test_finds_each_button_where_the_game_shows_it(locator, button):
    name, (x, y) = EXPECTED_BUTTONS[button]
    match = locator.locate(read_image(SCREENS_DIR / f"{name}.png"), button)
    assert match is not None
    assert abs(match.center_x - x) <= 4
    assert abs(match.center_y - y) <= 4


@needs_prints
@pytest.mark.parametrize("button", EXPECTED_BUTTONS)
@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440)], ids=["720p", "1440p"])
def test_finds_buttons_in_other_resolutions(locator, button, size):
    name, (x, y) = EXPECTED_BUTTONS[button]
    frame = read_image(SCREENS_DIR / f"{name}.png")
    interpolation = cv2.INTER_AREA if size[1] < 1080 else cv2.INTER_CUBIC
    match = locator.locate(cv2.resize(frame, size, interpolation=interpolation), button)
    assert match is not None
    assert abs(match.center_x * 1080 / size[1] - x) <= 6
    assert abs(match.center_y * 1080 / size[1] - y) <= 6


@needs_prints
@pytest.mark.parametrize("button", EXPECTED_BUTTONS)
def test_buttons_are_not_found_on_other_screens(locator, button):
    source = EXPECTED_BUTTONS[button][0]
    for path in SCREENS_DIR.glob("*.png"):
        if path.stem != source:
            assert locator.locate(read_image(path), button) is None, path.stem


def outbox_of(controller):
    return controller._outbox.sent


def written_notifications(controller):
    return [message for kind, message, _button in outbox_of(controller) if kind != "click_done"]


PLAY_REQUEST = ("click_required", "Clique em Jogar para iniciar outra partida", "PLAY")


def test_the_request_to_click_and_its_reminder_go_to_the_file(harness):
    controller, events, locator, mouse = harness
    controller.activate()

    to_lobby_step(controller, locator)
    controller.update(FRAME, context(**LOBBY, now=20.0), WINDOW)

    assert len(mouse.moves) == 1
    assert outbox_of(controller) == [PLAY_REQUEST, PLAY_REQUEST]
    assert written_notifications(controller) == notifications(events)


def test_the_file_gets_the_click_after_the_user_makes_it(harness):
    controller, _events, locator, _mouse = harness
    controller.activate()
    to_lobby_step(controller, locator)

    controller.update(FRAME, context(state=S.MATCHMAKING, frame_state=S.MATCHMAKING, now=1.0), WINDOW)

    assert outbox_of(controller) == [PLAY_REQUEST, ("click_done", "Clique feito: Jogar", "PLAY")]
    assert controller.status().step is GuidanceStep.IDLE


def test_informative_notice_goes_to_the_outbox_without_a_button(harness):
    controller, _events, _locator, _mouse = harness
    controller.activate()

    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE), WINDOW)

    assert outbox_of(controller) == [("info", "Você é o último vivo. O menu abre em 3 s.", None)]


def test_failed_escape_notice_also_goes_to_the_outbox(harness):
    controller, events, _locator, mouse = harness
    mouse.escape_works = False
    controller.activate()

    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE), WINDOW)
    controller.update(FRAME, context(condition=C.LAST_SQUAD_MEMBER_ALIVE, now=3.0), WINDOW)

    assert outbox_of(controller)[-1] == ("info", "Não consegui abrir o menu. Aperte Esc.", None)
    assert written_notifications(controller) == notifications(events)
