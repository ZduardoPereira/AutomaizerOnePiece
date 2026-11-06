"""Leitura dos números do HUD.

Os testes sobre prints reais são ignorados quando os prints não estão disponíveis, pois eles ficam fora do
controle de versão por mostrarem nomes de outros jogadores.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import RecognitionConfig
from core.paths import TEMPLATES_DIR
from core.state_machine import GameState
from recognition.hud_reader import _QUEST_MAX_GAP, _QUEST_THRESHOLD, DigitReader, HudNumbers, HudReader, HudTracker, MatchNumbers
from recognition.interface_detector import Detection, ScreenDetector, ScreenResult, TemplateMatch, read_image
from tests.helpers import blank_frame

SCREENS_DIR = Path(__file__).parent / "fixtures" / "screens"
DIGITS_DIR = TEMPLATES_DIR / "hud" / "digitos"
QUEST_DIGITS_DIR = TEMPLATES_DIR / "hud" / "digitos_missao"

EXPECTED = {
    "in_match_warmup": HudNumbers(0, 0, 80),
    "in_match_teammate_knocked": HudNumbers(7, 16, 17),
    "in_match_teammate_eliminated_timer": HudNumbers(7, 16, 16),
    "in_match_teammate_eliminated_card": HudNumbers(11, 20, 7),
    "in_match_self_knocked": HudNumbers(0, 1, 36, 382),
    "in_match_last_alive_timer": HudNumbers(0, 1, 10, 387),
    "in_match_last_alive_card": HudNumbers(0, 1, 5, 390),
    "in_match_solo": HudNumbers(0, None, 64),
    "in_match_solo_bus": HudNumbers(0, None, 80),
    "in_match_item_found_elemental": HudNumbers(2, 3, 13),
    "real_in_match_warmup": HudNumbers(0, 0, 76),
    "eliminated_killcam": HudNumbers(players_left=15),
    "eliminated_spectator": HudNumbers(players_left=35),
    "real_eliminated_spectator": HudNumbers(players_left=15),
}
WITHOUT_HUD = ["lobby_idle", "loading_connecting", "matchmaking_lobby", "defeat_placement", "result_victory", "victory_banner"]


@pytest.fixture(scope="module")
def detector():
    return ScreenDetector(RecognitionConfig(), TEMPLATES_DIR)


def load_screen(name):
    path = SCREENS_DIR / f"{name}.png"
    if not path.exists():
        pytest.skip(f"print indisponível: {name}")
    return read_image(path)


def read_print(name, detector, height=None):
    frame = load_screen(name)
    if height is not None:
        width = round(frame.shape[1] * height / frame.shape[0])
        interpolation = cv2.INTER_AREA if height < frame.shape[0] else cv2.INTER_CUBIC
        frame = cv2.resize(frame, (width, height), interpolation=interpolation)
    return HudReader(TEMPLATES_DIR).read(frame, detector.score(frame))


def test_templates_are_available():
    assert HudReader(TEMPLATES_DIR).warnings == []


def test_missing_templates_are_reported(tmp_path):
    warnings = HudReader(tmp_path).warnings
    assert any("Abates" in warning for warning in warnings)
    assert any("baús" in warning for warning in warnings)


def test_reader_without_templates_reads_nothing(tmp_path):
    frame = blank_frame()
    assert HudReader(tmp_path).read(frame, Detection(GameState.IN_MATCH, 0.9, {})) == HudNumbers()


@pytest.mark.parametrize(("name", "expected"), EXPECTED.items())
def test_numbers_on_real_screens(name, expected, detector):
    assert read_print(name, detector) == expected


@pytest.mark.parametrize("height", [720, 1440])
@pytest.mark.parametrize(("name", "expected"), EXPECTED.items())
def test_numbers_on_other_resolutions(name, expected, height, detector):
    assert read_print(name, detector, height) == expected


@pytest.mark.parametrize("name", WITHOUT_HUD)
def test_screens_without_hud_have_no_numbers(name, detector):
    assert read_print(name, detector) == HudNumbers()


def test_quest_digits_read_another_players_video():
    """Nenhum dos dígitos deste número veio da imagem, exceto o 4: ela prova que os modelos valem fora dos prints."""
    frame = load_screen("reward_token_stream")
    row = cv2.cvtColor(frame[46:70, 40:96], cv2.COLOR_BGR2GRAY)
    row = cv2.resize(row, None, fx=407 / 192, fy=407 / 192, interpolation=cv2.INTER_CUBIC)
    assert DigitReader(QUEST_DIGITS_DIR, _QUEST_THRESHOLD, _QUEST_MAX_GAP).read(row)[0] == "146/250"


def test_quest_is_found_again_after_it_moves(detector):
    """A largura do número e as outras missões deslocam a linha entre uma leitura e outra."""
    reader = HudReader(TEMPLATES_DIR)
    frame = load_screen("in_match_last_alive_timer")
    detection = detector.score(frame)
    assert reader.read(frame, detection).chest_progress == 387

    moved = np.zeros_like(frame)
    moved[60:] = frame[:-60]
    readings = [reader.read(moved, detection).chest_progress for _ in range(5)]

    assert readings[-1] == 387


def test_quest_search_is_skipped_on_most_frames_while_it_is_not_on_screen(monkeypatch):
    reader = HudReader(TEMPLATES_DIR)
    calls = []
    original = reader._search
    monkeypatch.setattr(reader, "_search", lambda *args: calls.append(args) or original(*args))

    frame = blank_frame()
    for _ in range(8):
        reader.read(frame, Detection(GameState.IN_MATCH, 0.9, {}))

    assert len(calls) == 2


def icon_detection(left, skull_top, person_top, state=GameState.IN_MATCH, score=0.95):
    matches = (
        TemplateMatch("hud_caveira", score, left, skull_top, 30, 30, 1.0),
        TemplateMatch("hud_pessoa", score, left, person_top, 30, 30, 1.0),
    )
    return Detection(state, 0.9, {GameState.IN_MATCH: ScreenResult(GameState.IN_MATCH, score, 0.8, matches)})


def counter_frame(values, left=1584):
    """Desenha os números com os próprios modelos, alinhados à direita dos ícones."""
    frame = np.full((1080, 1920, 3), 60, dtype=np.uint8)
    for top, text in values.items():
        right = left - 4
        for character in reversed(text):
            digit = read_image(DIGITS_DIR / f"{character}.png", cv2.IMREAD_GRAYSCALE)
            x = right - digit.shape[1]
            frame[top + 4 : top + 4 + digit.shape[0], x : right] = cv2.cvtColor(digit, cv2.COLOR_GRAY2BGR)
            right = x - 1
    return frame


def test_counters_follow_the_icons_with_assists():
    frame = counter_frame({33: "12", 71: "5", 112: "47"})
    numbers = HudReader(TEMPLATES_DIR).read(frame, icon_detection(1584, 33, 112))
    assert (numbers.kills, numbers.assists, numbers.players_left) == (12, 5, 47)


def test_solo_has_no_assists_row():
    frame = counter_frame({33: "3", 70: "64"})
    numbers = HudReader(TEMPLATES_DIR).read(frame, icon_detection(1584, 33, 70))
    assert (numbers.kills, numbers.assists, numbers.players_left) == (3, None, 64)


def test_uncertain_icons_are_ignored():
    frame = counter_frame({33: "12", 71: "5", 112: "47"})
    numbers = HudReader(TEMPLATES_DIR).read(frame, icon_detection(1584, 33, 112, score=0.6))
    assert numbers == HudNumbers()


def test_eliminated_only_trusts_the_players_left():
    frame = counter_frame({33: "12", 71: "5", 112: "47"})
    numbers = HudReader(TEMPLATES_DIR).read(frame, icon_detection(1584, 33, 112, state=GameState.ELIMINATED))
    assert numbers == HudNumbers(players_left=47)


def test_number_far_from_the_icon_is_not_a_counter():
    frame = counter_frame({33: "12"}, left=1520)
    numbers = HudReader(TEMPLATES_DIR).read(frame, icon_detection(1584, 33, 112))
    assert numbers.kills is None


def test_empty_region_reads_no_number():
    reader = DigitReader(DIGITS_DIR, 0.75, 8)
    assert reader.read(np.full((38, 82), 90, dtype=np.uint8)) == ("", 0)


def test_digit_reader_without_files_reads_nothing(tmp_path):
    reader = DigitReader(tmp_path, 0.75, 8)
    assert not reader.available
    assert reader.read(np.zeros((30, 30), dtype=np.uint8)) == ("", 0)


def track(tracker, *readings):
    for reading in readings:
        tracker.observe(reading)
    return tracker.numbers


def test_tracker_starts_empty():
    assert HudTracker().numbers == MatchNumbers(0, 0, None, None)


def test_value_needs_to_repeat():
    tracker = HudTracker(confirm_frames=2)
    assert track(tracker, HudNumbers(kills=3)).kills == 0
    assert track(tracker, HudNumbers(kills=3)).kills == 3


def test_isolated_misreading_is_ignored():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(kills=1), HudNumbers(kills=1))
    assert track(tracker, HudNumbers(kills=7), HudNumbers(kills=1), HudNumbers(kills=7)).kills == 1


def test_missing_reading_keeps_the_last_value():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(kills=2), HudNumbers(kills=2))
    assert track(tracker, HudNumbers(), HudNumbers()).kills == 2


def test_reading_interrupted_by_a_gap_starts_over():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(kills=2), HudNumbers(kills=2))
    assert track(tracker, HudNumbers(kills=3), HudNumbers(), HudNumbers(kills=3)).kills == 2


def test_big_jump_needs_many_repetitions():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(kills=1), HudNumbers(kills=1))
    assert track(tracker, *[HudNumbers(kills=18)] * 5).kills == 1
    assert track(tracker, HudNumbers(kills=18)).kills == 18


def test_kills_only_go_down_after_many_repetitions():
    """Uma leitura errada que passou da confirmação não pode ficar para sempre."""
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(kills=7), HudNumbers(kills=7))
    assert track(tracker, *[HudNumbers(kills=1)] * 5).kills == 7
    assert track(tracker, HudNumbers(kills=1)).kills == 1


def test_players_left_can_go_up_and_down():
    tracker = HudTracker(confirm_frames=2)
    assert track(tracker, HudNumbers(players_left=70), HudNumbers(players_left=70)).players_left == 70
    assert track(tracker, HudNumbers(players_left=76), HudNumbers(players_left=76)).players_left == 76
    assert track(tracker, HudNumbers(players_left=70), HudNumbers(players_left=70)).players_left == 70


def test_chests_count_from_the_start_of_the_match():
    tracker = HudTracker(confirm_frames=2)
    assert track(tracker, HudNumbers(chest_progress=382), HudNumbers(chest_progress=382)).chests_opened == 0
    assert track(tracker, HudNumbers(chest_progress=383), HudNumbers(chest_progress=383)).chests_opened == 1
    assert track(tracker, HudNumbers(chest_progress=390), HudNumbers(chest_progress=390)).chests_opened == 8


def test_chests_are_unknown_until_the_quest_is_seen():
    tracker = HudTracker(confirm_frames=2)
    assert track(tracker, HudNumbers(kills=1), HudNumbers(kills=1)).chests_opened is None


def test_chests_survive_the_quest_being_hidden_for_a_while():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(chest_progress=100), HudNumbers(chest_progress=100))
    track(tracker, HudNumbers(), HudNumbers(), HudNumbers())
    assert track(tracker, HudNumbers(chest_progress=102), HudNumbers(chest_progress=102)).chests_opened == 2


def test_big_jump_in_chests_does_not_add_to_the_count():
    """O início da partida leu 302 no lugar de 382: esses 80 não são baús abertos."""
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(chest_progress=302), HudNumbers(chest_progress=302))
    track(tracker, HudNumbers(chest_progress=304), HudNumbers(chest_progress=304))

    assert track(tracker, *[HudNumbers(chest_progress=384)] * 6).chests_opened == 2
    assert track(tracker, HudNumbers(chest_progress=385), HudNumbers(chest_progress=385)).chests_opened == 3


def test_chests_recover_from_a_start_read_too_high():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(chest_progress=392), HudNumbers(chest_progress=392))

    assert track(tracker, *[HudNumbers(chest_progress=382)] * 6).chests_opened == 0
    assert track(tracker, HudNumbers(chest_progress=383), HudNumbers(chest_progress=383)).chests_opened == 1


def test_reset_starts_a_new_match():
    tracker = HudTracker(confirm_frames=2)
    track(tracker, HudNumbers(4, 2, 30, 100), HudNumbers(4, 2, 30, 100), HudNumbers(4, 2, 30, 103), HudNumbers(4, 2, 30, 103))

    tracker.reset()

    assert tracker.numbers == MatchNumbers()
    assert track(tracker, HudNumbers(chest_progress=103), HudNumbers(chest_progress=103)).chests_opened == 0


def test_glyph_without_a_drawing_is_skipped(tmp_path):
    empty = np.zeros((20, 12, 4), dtype=np.uint8)
    cv2.imencode(".png", empty)[1].tofile(tmp_path / "7.png")
    assert not DigitReader(tmp_path, 0.7, 6).available
