from pathlib import Path

import cv2
import pytest

from core.config import SquadConfig
from core.paths import TEMPLATES_DIR
from recognition.interface_detector import read_image
from recognition.squad_detector import MemberStatus as M
from recognition.squad_detector import SquadDetector, SquadReading
from tests.helpers import (
    BLUE_WALL,
    GRASS,
    HEALTH_TIP,
    ROW_TOPS,
    SHIELD,
    blank_frame,
    draw_alive_row,
    draw_hud,
    draw_knocked_row,
    draw_name_diamond,
    draw_timer_row,
    filled_frame,
)

SCREENS_DIR = Path(__file__).parent / "fixtures" / "screens"


@pytest.fixture(scope="module")
def detector():
    return SquadDetector(SquadConfig(), TEMPLATES_DIR)


def test_empty_frame_has_no_squad(detector):
    reading = detector.read(blank_frame())
    assert reading == SquadReading()
    assert not reading.visible


def test_reads_each_state_and_keeps_order(detector):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[0], health=0.5, shield=0.75)
    draw_knocked_row(frame, ROW_TOPS[1], health=0.6)
    draw_timer_row(frame, ROW_TOPS[2], seconds=90)
    draw_hud(frame, health=0.66, shield=0.5)

    rows = detector.read(frame).rows

    assert [row.status for row in rows] == [M.ALIVE, M.KNOCKED, M.ELIMINATED]
    assert rows[0].health == pytest.approx(0.5, abs=0.02)
    assert rows[0].shield == pytest.approx(0.75, abs=0.02)
    assert rows[1].health == pytest.approx(0.6, abs=0.02)
    assert rows[2].reboot_seconds == pytest.approx(90, abs=2)


def test_reads_main_hud(detector):
    frame = blank_frame()
    draw_hud(frame, health=0.66, shield=0.5)

    hud = detector.read(frame).hud

    assert hud.status is M.ALIVE
    assert hud.health == pytest.approx(0.66, abs=0.02)
    assert hud.shield == pytest.approx(0.5, abs=0.02)


def test_knocked_main_hud(detector):
    frame = blank_frame()
    draw_hud(frame, health=0.98, knocked=True)
    assert detector.read(frame).hud.status is M.KNOCKED


def test_hud_and_rows_are_not_confused(detector):
    frame = blank_frame()
    draw_hud(frame, health=1.0, shield=1.0)
    reading = detector.read(frame)
    assert reading.rows == ()
    assert reading.hud is not None


@pytest.mark.parametrize("background", [GRASS, BLUE_WALL], ids=["grama", "parede_azul"])
def test_same_colored_background_does_not_hide_bars(detector, background):
    frame = filled_frame(background)
    draw_alive_row(frame, ROW_TOPS[0], health=0.4, shield=0.5)
    draw_timer_row(frame, ROW_TOPS[1], seconds=60)
    draw_hud(frame, health=0.66, shield=0.3)

    reading = detector.read(frame)

    assert [row.status for row in reading.rows] == [M.ALIVE, M.ELIMINATED]
    assert reading.rows[0].health == pytest.approx(0.4, abs=0.02)
    assert reading.rows[1].reboot_seconds == pytest.approx(60, abs=2)
    assert reading.hud.health == pytest.approx(0.66, abs=0.02)


@pytest.mark.parametrize("color", [SHIELD, HEALTH_TIP], ids=["azul", "amarelo"])
def test_colored_diamond_next_to_the_name_is_not_a_row(detector, color):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[0])
    draw_name_diamond(frame, ROW_TOPS[0], color)

    assert len(detector.read(frame).rows) == 1


def test_low_health_is_still_a_living_teammate(detector):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[0], health=0.05)

    rows = detector.read(frame).rows

    assert [row.status for row in rows] == [M.ALIVE]
    assert rows[0].health == pytest.approx(0.05, abs=0.02)


def test_bars_out_of_line_are_scenery(detector):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[0])
    draw_hud(frame)
    frame[700:710, 300:400] = frame[ROW_TOPS[0] + 10, 100]

    assert len(detector.read(frame).rows) == 1


@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440), (3840, 2160)])
def test_resolution_does_not_matter(detector, size):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[1], health=0.5, shield=1.0)
    draw_knocked_row(frame, ROW_TOPS[2])
    draw_hud(frame, health=0.66)
    resized = cv2.resize(frame, size, interpolation=cv2.INTER_NEAREST)

    reading = detector.read(resized)

    assert [row.status for row in reading.rows] == [M.ALIVE, M.KNOCKED]
    assert reading.rows[0].health == pytest.approx(0.5, abs=0.03)
    assert reading.hud.health == pytest.approx(0.66, abs=0.03)


def test_box_covers_the_row_in_frame_coordinates(detector):
    frame = blank_frame()
    draw_alive_row(frame, ROW_TOPS[0])
    left, top, width, height = detector.read(frame).rows[0].box
    assert left < 20
    assert top == ROW_TOPS[0] - 24
    assert left + width > 280
    assert top + height > ROW_TOPS[0] + 18


def test_hud_picture_shows_the_name_above_the_bars(detector):
    frame = blank_frame()
    draw_hud(frame)
    left, top, width, height = detector.read(frame).hud.picture_box
    assert top + height <= 984
    assert top < 940 < top + height
    assert left < 60 < left + width


def test_missing_card_template_is_reported(tmp_path):
    assert SquadDetector(SquadConfig(), tmp_path).warnings
    assert SquadDetector(SquadConfig(), TEMPLATES_DIR).warnings == []


# (vida, escudo) medidos, com o que o jogo mostra na tela quando há número visível.
ALIVE_100_100 = (M.ALIVE, 1.0, 1.0)
EXPECTED_READINGS = {
    "in_match_warmup": ([(M.ALIVE, 1.0, 0.0)] * 3, (M.ALIVE, 1.0, 0.0)),
    "in_match_teammate_knocked": ([(M.ALIVE, 1.0, 0.8), (M.ALIVE, 0.83, 1.0), (M.KNOCKED, None, None)], (M.ALIVE, 1.0, 0.70)),
    "in_match_teammate_eliminated_timer": ([ALIVE_100_100, (M.ALIVE, 0.40, 1.0), (M.ELIMINATED, None, None)], ALIVE_100_100),
    "in_match_teammate_eliminated_card": ([ALIVE_100_100, (M.ALIVE, 0.58, 1.0), (M.ELIMINATED, None, None)], ALIVE_100_100),
    "in_match_self_knocked": ([ALIVE_100_100, (M.ALIVE, 1.0, 0.21), ALIVE_100_100], (M.KNOCKED, 0.98, None)),
    "in_match_last_alive_timer": ([(M.ELIMINATED, None, None)], (M.ALIVE, 1.0, 0.50)),
    "in_match_last_alive_card": ([(M.ELIMINATED, None, None)], (M.ALIVE, 0.98, 0.80)),
    "in_match_solo": ([], (M.ALIVE, 0.66, 0.0)),
    "in_match_solo_bus": ([], None),
    "in_match_item_found_elemental": ([(M.ELIMINATED, None, None), (M.ELIMINATED, None, None), (M.ALIVE, 0.83, 1.0)], ALIVE_100_100),
    "eliminated_killcam": ([ALIVE_100_100, ALIVE_100_100, (M.ALIVE, 0.50, 0.75)], None),
    "eliminated_spectator": ([(M.ELIMINATED, None, None), (M.ALIVE, 1.0, 0.21), ALIVE_100_100], ALIVE_100_100),
    "real_in_match_warmup": ([(M.ALIVE, 1.0, 0.0)] * 3, (M.ALIVE, 1.0, 0.0)),
    "real_eliminated_spectator": ([(M.ELIMINATED, None, None), (M.ALIVE, 1.0, 1.0), (M.ALIVE, 0.48, 0.75)], (M.ALIVE, 1.0, 0.61)),
}
TIMERS = {
    "in_match_teammate_eliminated_timer": [114],
    "in_match_last_alive_timer": [113],
    "eliminated_spectator": [111],
    "real_eliminated_spectator": [109],
    "in_match_item_found_elemental": [60, 55],
}
COLLECTED_CARDS = {"in_match_teammate_eliminated_card", "in_match_last_alive_card"}

needs_prints = pytest.mark.skipif(
    not all((SCREENS_DIR / f"{name}.png").exists() for name in EXPECTED_READINGS),
    reason="Prints do jogo indisponíveis em tests/fixtures/screens",
)


def assert_member(actual, expected):
    status, health, shield = expected
    assert actual.status is status
    if health is not None:
        assert actual.health == pytest.approx(health, abs=0.04)
    if shield is not None:
        assert actual.shield == pytest.approx(shield, abs=0.04)


@needs_prints
@pytest.mark.parametrize("name", EXPECTED_READINGS)
def test_reads_real_prints_like_the_game_shows(detector, name):
    rows, hud = EXPECTED_READINGS[name]
    reading = detector.read(read_image(SCREENS_DIR / f"{name}.png"))

    assert len(reading.rows) == len(rows)
    for actual, expected in zip(reading.rows, rows):
        assert_member(actual, expected)
    if hud is None:
        assert reading.hud is None
    else:
        assert_member(reading.hud, hud)


@needs_prints
@pytest.mark.parametrize("name", TIMERS)
def test_timer_bar_matches_the_seconds_on_screen(detector, name):
    reading = detector.read(read_image(SCREENS_DIR / f"{name}.png"))
    timers = [row.reboot_seconds for row in reading.rows if row.reboot_seconds is not None]
    assert timers == [pytest.approx(seconds, abs=2) for seconds in TIMERS[name]]


@needs_prints
@pytest.mark.parametrize("name", COLLECTED_CARDS)
def test_collected_card_is_recognized(detector, name):
    reading = detector.read(read_image(SCREENS_DIR / f"{name}.png"))
    assert [row.card_collected for row in reading.rows].count(True) == 1


@needs_prints
@pytest.mark.parametrize("name", sorted(set(EXPECTED_READINGS) - COLLECTED_CARDS))
def test_no_false_collected_card(detector, name):
    reading = detector.read(read_image(SCREENS_DIR / f"{name}.png"))
    assert not any(row.card_collected for row in reading.rows)


@needs_prints
@pytest.mark.parametrize("name", ["in_match_teammate_knocked", "in_match_teammate_eliminated_timer", "in_match_solo"])
@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440)], ids=["720p", "1440p"])
def test_real_prints_in_other_resolutions(detector, name, size):
    rows, hud = EXPECTED_READINGS[name]
    frame = read_image(SCREENS_DIR / f"{name}.png")
    interpolation = cv2.INTER_AREA if size[1] < 1080 else cv2.INTER_CUBIC

    reading = detector.read(cv2.resize(frame, size, interpolation=interpolation))

    assert [row.status for row in reading.rows] == [row[0] for row in rows]
    assert reading.hud.status is hud[0]
