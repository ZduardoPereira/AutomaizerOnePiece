from pathlib import Path

import cv2
import numpy as np
import pytest

from core.state_machine import GameState
from recognition.interface_detector import ScreenDetector, read_image
from tests.helpers import blank_frame, paste, pattern, recognition_config, write_template

LOBBY_PATCH = pattern(1, 240, 120)
LOADING_PATCH = pattern(2, 240, 120)
EXTRA_PATCH = pattern(3, 240, 120)


@pytest.fixture
def templates(tmp_path):
    write_template(tmp_path, "lobby", "a", LOBBY_PATCH)
    write_template(tmp_path, "loading", "a", LOADING_PATCH)
    return tmp_path


def make_detector(directory, **rules):
    rules = rules or {"LOADING": {"folder": "loading"}, "LOBBY": {"folder": "lobby"}}
    return ScreenDetector(recognition_config(**rules), directory)


def test_read_image_handles_accented_paths_and_missing_files(tmp_path):
    folder = tmp_path / "ação"
    write_template(folder, "tela", "recorte", LOBBY_PATCH)
    assert read_image(folder / "tela" / "recorte.png").shape[:2] == LOBBY_PATCH.shape
    assert read_image(folder / "nao_existe.png") is None


@pytest.mark.parametrize("position", [(0, 0), (900, 400), (1680, 960)])
def test_finds_template_anywhere_in_the_frame(templates, position):
    frame = paste(blank_frame(), LOBBY_PATCH, *position)
    detection = make_detector(templates).detect(frame)

    assert detection.state is GameState.LOBBY
    assert detection.confidence > 0.95
    match = detection.screens[GameState.LOBBY].matches[0]
    assert abs(match.left - position[0]) <= 2
    assert abs(match.top - position[1]) <= 2
    assert abs(match.width - 240) <= 2


def test_unknown_when_nothing_matches(templates):
    detection = make_detector(templates).detect(paste(blank_frame(), pattern(9, 240, 120), 300, 300))
    assert detection.state is GameState.UNKNOWN
    assert detection.confidence < 0.75
    assert not detection.is_visible(GameState.LOBBY)


@pytest.mark.parametrize(
    "frame",
    [
        np.zeros((1080, 1920, 3), dtype=np.uint8),
        np.full((1080, 1920, 3), 255, dtype=np.uint8),
        np.tile(np.arange(1920, dtype=np.float32)[None, :, None] / 1920 * 255, (1080, 1, 3)).astype(np.uint8),
        np.random.default_rng(0).integers(0, 255, (1080, 1920, 3), dtype=np.uint8),
    ],
    ids=["preto", "branco", "gradiente", "ruido"],
)
def test_frames_without_interface_are_unknown(templates, frame):
    assert make_detector(templates).detect(frame).state is GameState.UNKNOWN


def test_priority_decides_when_screens_overlap(templates):
    frame = paste(paste(blank_frame(), LOBBY_PATCH, 100, 100), LOADING_PATCH, 800, 500)
    detection = make_detector(templates).detect(frame)

    assert detection.state is GameState.LOADING
    assert detection.is_visible(GameState.LOBBY)


def test_min_matches_requires_distinct_templates(tmp_path):
    write_template(tmp_path, "result", "a", LOBBY_PATCH)
    write_template(tmp_path, "result", "b", EXTRA_PATCH)
    detector = make_detector(tmp_path, MATCH_RESULT={"folder": "result", "min_matches": 2})

    only_one = paste(blank_frame(), LOBBY_PATCH, 100, 100)
    assert detector.detect(only_one).state is GameState.UNKNOWN

    both = paste(only_one.copy(), EXTRA_PATCH, 800, 500)
    assert detector.detect(both).state is GameState.MATCH_RESULT


@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440), (3840, 2160)])
def test_resolution_does_not_matter(templates, size):
    frame = paste(blank_frame(), LOBBY_PATCH, 600, 300)
    resized = cv2.resize(frame, size, interpolation=cv2.INTER_NEAREST)
    assert make_detector(templates).detect(resized).state is GameState.LOBBY


def test_wider_aspect_ratio_keeps_working(templates):
    frame = paste(blank_frame(width=2560), LOBBY_PATCH, 2200, 300)
    assert make_detector(templates).detect(frame).state is GameState.LOBBY


def test_alternative_scale_is_found_and_remembered(templates):
    smaller = cv2.resize(LOBBY_PATCH, None, fx=0.9, fy=0.9, interpolation=cv2.INTER_NEAREST)
    frame = paste(blank_frame(), smaller, 500, 500)
    detector = make_detector(templates)

    first = detector.detect(frame)
    assert first.state is GameState.LOBBY
    assert first.screens[GameState.LOBBY].matches[0].scale == 0.9

    second = detector.detect(frame)
    assert second.state is GameState.LOBBY
    assert second.screens[GameState.LOBBY].matches[0].scale == 0.9


def test_alternative_scales_are_probed_only_periodically(templates, monkeypatch):
    detector = make_detector(templates)
    calls = []
    original = detector._evaluate

    def spy(working, factor, *, all_scales):
        calls.append(all_scales)
        return original(working, factor, all_scales=all_scales)

    monkeypatch.setattr(detector, "_evaluate", spy)
    frame = blank_frame()
    for _ in range(12):
        detector.detect(frame)

    assert calls.count(True) == 2  # no 1º e no 11º quadro desconhecido
    assert calls.count(False) == 12


def test_score_does_not_change_remembered_scale(templates):
    smaller = cv2.resize(LOBBY_PATCH, None, fx=0.9, fy=0.9, interpolation=cv2.INTER_NEAREST)
    detector = make_detector(templates)
    assert detector.score(paste(blank_frame(), smaller, 500, 500)).state is GameState.LOBBY

    normal = paste(blank_frame(), LOBBY_PATCH, 500, 500)
    assert detector.detect(normal).screens[GameState.LOBBY].matches[0].scale == 1.0


def in_match_detector(directory):
    write_template(directory, "in_match", "a", EXTRA_PATCH)
    return make_detector(
        directory, LOADING={"folder": "loading"}, LOBBY={"folder": "lobby"}, IN_MATCH={"folder": "in_match"}
    )


def test_likely_in_match_checks_only_its_own_templates(templates, monkeypatch):
    detector = in_match_detector(templates)
    frame = paste(blank_frame(), EXTRA_PATCH, 500, 500)
    names = []
    original = detector._match

    def spy(working, factor, template, scales):
        names.append(template.name)
        return original(working, factor, template, scales)

    monkeypatch.setattr(detector, "_match", spy)

    detection = detector.detect(frame, GameState.IN_MATCH)

    assert detection.state is GameState.IN_MATCH
    assert list(detection.screens) == [GameState.IN_MATCH]
    assert len(names) == 1


def test_likely_screen_near_its_threshold_still_checks_the_others(templates):
    detector = in_match_detector(templates)
    frame = paste(blank_frame(), EXTRA_PATCH, 500, 500)
    score = detector.detect(frame).screens[GameState.IN_MATCH].score
    detector.set_thresholds({GameState.IN_MATCH: score - 0.02})

    detection = detector.detect(frame, GameState.IN_MATCH)

    assert detection.state is GameState.IN_MATCH
    assert len(detection.screens) == 3


def test_likely_screen_falls_back_to_all_screens(templates):
    detector = in_match_detector(templates)
    detection = detector.detect(paste(blank_frame(), LOBBY_PATCH, 500, 500), GameState.IN_MATCH)
    assert detection.state is GameState.LOBBY
    assert len(detection.screens) == 3


def test_other_likely_screens_evaluate_everything(templates):
    # Procurar partida aparece junto com as abas do lobby, então o lobby não pode dispensar as demais.
    detector = in_match_detector(templates)
    frame = paste(paste(blank_frame(), LOBBY_PATCH, 100, 100), LOADING_PATCH, 800, 500)
    assert detector.detect(frame, GameState.LOBBY).state is GameState.LOADING


def test_thresholds_can_be_changed_and_fall_back_to_base(templates):
    detector = make_detector(templates)
    frame = paste(blank_frame(), LOBBY_PATCH, 300, 300)

    detector.set_thresholds({GameState.LOBBY: 0.99})
    detector.set_thresholds({GameState.LOBBY: 1.0})
    assert detector.thresholds[GameState.LOBBY] == 1.0
    assert detector.thresholds[GameState.LOADING] == 0.75

    detector.set_thresholds({})
    assert detector.detect(frame).state is GameState.LOBBY


def test_warns_about_missing_and_insufficient_templates(tmp_path):
    write_template(tmp_path, "result", "a", LOBBY_PATCH)
    detector = make_detector(
        tmp_path,
        MATCH_RESULT={"folder": "result", "min_matches": 2},
        LOBBY={"folder": "pasta_vazia"},
    )
    assert len(detector.warnings) == 2
    assert detector.detect(blank_frame()).state is GameState.UNKNOWN


def test_unreadable_template_is_reported_and_skipped(tmp_path):
    write_template(tmp_path, "lobby", "bom", LOBBY_PATCH)
    (tmp_path / "lobby" / "ruim.png").write_bytes(b"isto nao e uma imagem")
    detector = make_detector(tmp_path, LOBBY={"folder": "lobby"})

    assert any("ruim.png" in warning for warning in detector.warnings)
    assert detector.detect(paste(blank_frame(), LOBBY_PATCH, 10, 10)).state is GameState.LOBBY


def test_fingerprint_changes_with_templates_and_rules(templates):
    original = make_detector(templates).fingerprint
    assert make_detector(templates).fingerprint == original

    write_template(templates, "lobby", "novo", EXTRA_PATCH)
    with_template = make_detector(templates).fingerprint
    assert with_template != original

    rules = {"LOADING": {"folder": "loading"}, "LOBBY": {"folder": "lobby", "min_matches": 2}}
    assert make_detector(templates, **rules).fingerprint != with_template


def test_real_template_folders_exist_for_every_default_screen():
    from core.config import RecognitionConfig
    from core.paths import TEMPLATES_DIR

    assert ScreenDetector(RecognitionConfig(), Path(TEMPLATES_DIR)).warnings == []
