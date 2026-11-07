"""Reconhecimento sobre prints reais do jogo.

Os prints ficam fora do controle de versão por mostrarem nomes de outros jogadores; sem eles, estes
testes são ignorados. Os "real_*" são quadros salvos pelo próprio aplicativo durante uma partida.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import RecognitionConfig, SquadConfig
from core.event_manager import EventManager
from core.paths import TEMPLATES_DIR
from core.state_machine import GameState as S
from core.state_machine import StateMachine
from recognition.interface_detector import ScreenDetector, read_image
from recognition.match_detector import MatchStats, MatchTracker
from recognition.squad_detector import MemberStatus, SquadDetector
from recognition.survival_detector import SquadCondition as C
from recognition.survival_detector import build_status

SCREENS_DIR = Path(__file__).parent / "fixtures" / "screens"
EXPECTED = {
    "lobby_idle": S.LOBBY,
    "matchmaking_lobby": S.MATCHMAKING,
    "matchmaking_requeue": S.MATCHMAKING,
    "loading_connecting": S.LOADING,
    "in_match_warmup": S.IN_MATCH,
    "in_match_teammate_knocked": S.IN_MATCH,
    "in_match_teammate_eliminated_timer": S.IN_MATCH,
    "in_match_teammate_eliminated_card": S.IN_MATCH,
    "in_match_self_knocked": S.IN_MATCH,
    "in_match_last_alive_timer": S.IN_MATCH,
    "in_match_last_alive_card": S.IN_MATCH,
    "in_match_solo": S.IN_MATCH,
    "in_match_solo_bus": S.IN_MATCH,
    "in_match_item_found_elemental": S.IN_MATCH,
    "eliminated_killcam": S.ELIMINATED,
    "eliminated_spectator": S.ELIMINATED,
    "eliminated_spectator_social_panel": S.ELIMINATED,
    "eliminated_spectator_menu_panel": S.ELIMINATED,
    "defeat_placement": S.DEFEAT,
    "victory_banner": S.VICTORY,
    "result_victory": S.MATCH_RESULT,
    "real_matchmaking": S.MATCHMAKING,
    "real_in_match_warmup": S.IN_MATCH,
    "real_eliminated_spectator": S.ELIMINATED,
    "reward_claim_treasure": S.REWARD_CLAIM,
    "reward_claim_quest": S.REWARD_CLAIM,
    "reward_claim_battle_pass": S.REWARD_CLAIM,
}

pytestmark = pytest.mark.skipif(
    not all((SCREENS_DIR / f"{name}.png").exists() for name in EXPECTED),
    reason="Prints do jogo indisponíveis em tests/fixtures/screens",
)


@pytest.fixture(scope="module")
def frames():
    return {name: read_image(SCREENS_DIR / f"{name}.png") for name in EXPECTED}


def new_detector():
    return ScreenDetector(RecognitionConfig(), TEMPLATES_DIR)


@pytest.fixture(scope="module")
def detector():
    return new_detector()


@pytest.mark.parametrize("name", EXPECTED)
def test_each_print_is_recognized(frames, detector, name):
    assert detector.detect(frames[name]).state is EXPECTED[name]


@pytest.mark.parametrize("name", EXPECTED)
def test_being_in_a_match_does_not_hide_other_screens(frames, detector, name):
    """Com a partida como tela provável, a análise é mais rápida, mas precisa perceber qualquer mudança."""
    assert detector.detect(frames[name], S.IN_MATCH).state is EXPECTED[name]


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440)], ids=["720p", "1440p"])
def test_other_resolutions(frames, detector, name, size):
    interpolation = cv2.INTER_AREA if size[1] < 1080 else cv2.INTER_CUBIC
    assert detector.detect(cv2.resize(frames[name], size, interpolation=interpolation)).state is EXPECTED[name]


@pytest.mark.parametrize("name", EXPECTED)
@pytest.mark.parametrize(("gain", "offset"), [(0.75, 0), (1.15, 15)], ids=["escuro", "claro"])
def test_brightness_changes(frames, detector, name, gain, offset):
    adjusted = cv2.convertScaleAbs(frames[name], alpha=gain, beta=offset)
    assert detector.detect(adjusted).state is EXPECTED[name]


@pytest.mark.parametrize("name", EXPECTED)
def test_saved_example_quality_keeps_recognition(frames, detector, name):
    _, buffer = cv2.imencode(".jpg", frames[name], [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert detector.detect(cv2.imdecode(buffer, cv2.IMREAD_COLOR)).state is EXPECTED[name]


@pytest.mark.parametrize("name", ["lobby_idle", "in_match_warmup", "in_match_solo", "eliminated_spectator", "victory_banner"])
@pytest.mark.parametrize("scale", [0.9, 1.1])
def test_larger_or_smaller_interface_is_found(frames, name, scale):
    frame = frames[name]
    height, width = frame.shape[:2]
    resized = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
    canvas = np.zeros_like(frame)
    copy_height = min(height, resized.shape[0])
    copy_width = min(width, resized.shape[1])
    canvas[:copy_height, :copy_width] = resized[:copy_height, :copy_width]

    assert new_detector().detect(canvas).state is EXPECTED[name]


def test_margin_between_right_and_wrong_screens(frames, detector):
    """Protege a folga em relação ao limite: um recorte ruim costuma falhar aqui antes de falhar no jogo."""
    rules = RecognitionConfig().screens
    for name, expected in EXPECTED.items():
        detection = detector.score(frames[name])
        assert detection.screens[expected].score >= rules[expected].threshold + 0.03, name
        priority = detector.order.index(expected)
        for state in detector.order[:priority]:
            # O menu pode estar aberto sobre o modo espectador, sem que isso seja um erro.
            if state is S.GAME_MENU:
                continue
            assert detection.screens[state].score <= rules[state].threshold - 0.05, f"{name}: {state}"


def test_all_three_counter_icons_are_not_required(frames, detector):
    """O modo solo não tem o ícone de assistências, e foi isso que quebrou a primeira versão do recorte."""
    assert detector.detect(frames["in_match_solo"]).screens[S.IN_MATCH].score >= 0.9
    assert detector.detect(frames["in_match_solo_bus"]).screens[S.IN_MATCH].score >= 0.9


@pytest.mark.parametrize("name", ["eliminated_spectator_social_panel", "eliminated_spectator_menu_panel"])
def test_open_menu_is_noticed_over_the_spectator(frames, detector, name):
    detection = detector.detect(frames[name])
    assert detection.state is S.ELIMINATED
    assert detection.is_visible(S.GAME_MENU)


@pytest.mark.parametrize("name", [name for name in EXPECTED if "panel" not in name])
def test_menu_is_not_seen_where_it_is_closed(frames, detector, name):
    assert not detector.detect(frames[name]).is_visible(S.GAME_MENU)


@pytest.mark.parametrize(
    ("name", "expected_teammates", "condition", "active"),
    [
        ("in_match_warmup", 3, C.SQUAD_ACTIVE, 4),
        ("in_match_teammate_knocked", 3, C.SQUAD_ACTIVE, 4),
        ("in_match_teammate_eliminated_timer", 3, C.SQUAD_ACTIVE, 3),
        ("in_match_teammate_eliminated_card", 3, C.SQUAD_ACTIVE, 3),
        ("in_match_self_knocked", 3, C.SQUAD_ACTIVE, 4),
        ("in_match_last_alive_timer", 3, C.LAST_SQUAD_MEMBER_ALIVE, 1),
        ("in_match_last_alive_card", 3, C.LAST_SQUAD_MEMBER_ALIVE, 1),
        ("eliminated_killcam", 3, C.SQUAD_ACTIVE, 3),
        ("eliminated_spectator", 3, C.SQUAD_ACTIVE, 3),
        ("real_in_match_warmup", 3, C.SQUAD_ACTIVE, 4),
        ("real_eliminated_spectator", 3, C.SQUAD_ACTIVE, 3),
        ("in_match_solo", 0, C.NO_SQUAD, 1),
        ("in_match_item_found_elemental", 3, C.SQUAD_ACTIVE, 2),
    ],
)
def test_squad_condition_from_real_prints(frames, name, expected_teammates, condition, active):
    reading = SquadDetector(SquadConfig(), TEMPLATES_DIR).read(frames[name])

    status = build_status(reading, EXPECTED[name], expected_teammates)

    assert status.condition is condition
    assert status.active_count == active
    assert len(status.members) == expected_teammates + 1


def test_knocked_teammate_and_knocked_self_are_told_apart(frames):
    squad = SquadDetector(SquadConfig(), TEMPLATES_DIR)

    teammate = build_status(squad.read(frames["in_match_teammate_knocked"]), S.IN_MATCH, 3)
    assert teammate.knocked_teammates == 1
    assert teammate.self_member.status is MemberStatus.ALIVE

    you = build_status(squad.read(frames["in_match_self_knocked"]), S.IN_MATCH, 3)
    assert you.knocked_teammates == 0
    assert you.self_member.status is MemberStatus.KNOCKED


def test_full_session_in_game_order(frames):
    detector = new_detector()
    machine = StateMachine(confirm_frames=2, unknown_grace_seconds=8.0)
    tracker = MatchTracker(EventManager())
    sequence = [
        "lobby_idle",
        "matchmaking_lobby",
        "loading_connecting",
        "in_match_warmup",
        "in_match_teammate_knocked",
        "in_match_self_knocked",
        "eliminated_killcam",
        "eliminated_spectator",
        "eliminated_spectator_menu_panel",
        "in_match_last_alive_timer",
        "victory_banner",
        "result_victory",
        "matchmaking_requeue",
        "loading_connecting",
        "in_match_warmup",
        "eliminated_spectator",
        "reward_claim_quest",
        "reward_claim_battle_pass",
        "lobby_idle",
        "matchmaking_lobby",
        "loading_connecting",
        "in_match_solo_bus",
        "in_match_solo",
        "defeat_placement",
        "result_victory",
        "lobby_idle",
    ]
    visited = []
    now = 0.0
    for name in sequence:
        for _ in range(2):
            now += 0.5
            detection = detector.detect(frames[name])
            transition = machine.observe(detection.state, detection.confidence, now)
            tracker.observe(transition, detection.is_visible(S.VICTORY), now)
            if transition is not None:
                visited.append(transition.current)

    assert visited == [
        S.LOBBY,
        S.MATCHMAKING,
        S.LOADING,
        S.IN_MATCH,
        S.ELIMINATED,
        S.IN_MATCH,
        S.VICTORY,
        S.MATCH_RESULT,
        S.MATCHMAKING,
        S.LOADING,
        S.IN_MATCH,
        S.ELIMINATED,
        S.REWARD_CLAIM,
        S.LOBBY,
        S.MATCHMAKING,
        S.LOADING,
        S.IN_MATCH,
        S.DEFEAT,
        S.MATCH_RESULT,
        S.LOBBY,
    ]
    assert tracker.stats == MatchStats(identified=3, victories=1, defeats=2, in_match=False)
