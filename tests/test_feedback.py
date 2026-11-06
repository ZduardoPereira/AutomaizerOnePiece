import json
from datetime import datetime

import cv2
import pytest

from core.state_machine import GameState as S
from recognition.feedback import CALIBRATION_MARGIN, FeedbackManager, FeedbackSample, calibrate_all, calibrate_threshold
from recognition.interface_detector import ScreenDetector
from tests.helpers import blank_frame, paste, pattern, recognition_config, write_template

FLOOR = 0.62
CEILING = 0.97
LOBBY_PATCH = pattern(1, 240, 120)
LOADING_PATCH = pattern(2, 240, 120)


def calibrate(base=0.75, positives=(), negatives=()):
    return calibrate_threshold(base, positives, negatives, FLOOR, CEILING)


def sample(label, predicted=None, **scores):
    return FeedbackSample(
        id=f"{label.value}-{id(scores)}",
        label=label,
        predicted=predicted or label,
        confidence=0.5,
        scores={S[name]: score for name, score in scores.items()},
        created_at=datetime.now(),
        fingerprint="x",
    )


def test_without_examples_keeps_base():
    calibration = calibrate()
    assert calibration.threshold == 0.75
    assert not calibration.conflict


def test_examples_inside_the_safe_window_keep_base():
    assert calibrate(positives=[0.9], negatives=[0.5]).threshold == 0.75


def test_positive_example_lowers_threshold_just_enough():
    assert calibrate(positives=[0.72, 0.9]).threshold == pytest.approx(0.72 - CALIBRATION_MARGIN)


def test_negative_example_raises_threshold_just_enough():
    assert calibrate(negatives=[0.78, 0.5]).threshold == pytest.approx(0.78 + CALIBRATION_MARGIN)


def test_single_wrong_click_on_an_obvious_screen_changes_nothing():
    calibration = calibrate(negatives=[0.95])
    assert calibration.threshold == 0.75
    assert not calibration.conflict


def test_negatives_too_high_do_not_block_the_ones_that_can_be_fixed():
    assert calibrate(negatives=[0.95, 0.78]).threshold == pytest.approx(0.78 + CALIBRATION_MARGIN)


def test_threshold_stays_between_opposing_examples():
    threshold = calibrate(positives=[0.85], negatives=[0.77]).threshold
    assert 0.77 < threshold < 0.85


def test_conflict_is_flagged_and_keeps_recognizing_confirmed_screens():
    calibration = calibrate(positives=[0.8], negatives=[0.82])
    assert calibration.conflict
    assert calibration.threshold == 0.75


def test_threshold_respects_ceiling():
    assert calibrate(base=0.96, negatives=[0.95]).threshold == CEILING


def test_positive_below_floor_does_not_lower_threshold_in_vain():
    assert calibrate(positives=[0.41]).threshold == 0.75
    assert calibrate(positives=[0.63]).threshold == 0.75


def test_positive_near_floor_lowers_threshold_to_it():
    assert calibrate(positives=[0.66]).threshold == pytest.approx(0.66 - CALIBRATION_MARGIN)


ORDER = [S.MATCH_RESULT, S.VICTORY, S.MATCHMAKING, S.LOBBY, S.IN_MATCH]
BASE = {state: 0.75 for state in ORDER}


def thresholds(*samples):
    return {state: item.threshold for state, item in calibrate_all(samples, ORDER, BASE, FLOOR, CEILING).items()}


def test_lower_priority_screen_may_appear_together():
    # Procurando partida também mostra as abas do lobby, sem que isso seja um erro do lobby.
    result = thresholds(sample(S.MATCHMAKING, MATCHMAKING=0.9, LOBBY=0.95))
    assert result[S.LOBBY] == 0.75


def test_higher_priority_screen_counts_as_false_positive():
    result = thresholds(sample(S.LOBBY, S.MATCHMAKING, MATCHMAKING=0.78, LOBBY=0.95))
    assert result[S.MATCHMAKING] == pytest.approx(0.78 + CALIBRATION_MARGIN)


def test_unknown_example_is_negative_for_every_screen():
    result = thresholds(sample(S.UNKNOWN, S.IN_MATCH, IN_MATCH=0.78, LOBBY=0.5))
    assert result[S.IN_MATCH] == pytest.approx(0.78 + CALIBRATION_MARGIN)
    assert result[S.LOBBY] == 0.75


@pytest.fixture
def harness(tmp_path):
    templates = tmp_path / "templates"
    write_template(templates, "loading", "a", LOADING_PATCH)
    write_template(templates, "lobby", "a", LOBBY_PATCH)
    config = recognition_config(LOADING={"folder": "loading"}, LOBBY={"folder": "lobby"})
    detector = ScreenDetector(config, templates)
    manager = FeedbackManager(tmp_path / "feedback", detector, config)
    return manager, detector, config, tmp_path


def degraded_frame(strength=0.5):
    """Quadro em que o recorte aparece encoberto, como após uma mudança visual do jogo."""
    noise = pattern(50, 240, 120)
    blended = cv2.addWeighted(LOBBY_PATCH, 1 - strength, noise, strength, 0)
    return paste(blank_frame(), blended, 400, 300)


def test_positive_feedback_makes_a_variation_recognizable(harness):
    manager, detector, _config, _path = harness
    frame = degraded_frame()
    before = detector.detect(frame)
    assert before.state is S.UNKNOWN
    assert FLOOR < before.screens[S.LOBBY].score < 0.75

    outcome = manager.add(frame, 1, before, S.LOBBY)

    assert outcome.saved
    assert "Limite de Lobby" in outcome.message
    assert detector.detect(frame).state is S.LOBBY


def test_negative_feedback_stops_a_false_positive(harness):
    manager, detector, _config, _path = harness
    frame = degraded_frame(0.47)
    before = detector.detect(frame)
    assert before.state is S.LOBBY
    assert before.screens[S.LOBBY].score < 0.8

    outcome = manager.add(frame, 1, before, S.UNKNOWN)

    assert "pode se repetir" not in outcome.message
    assert detector.detect(frame).state is S.UNKNOWN
    assert detector.detect(paste(blank_frame(), LOBBY_PATCH, 100, 100)).state is S.LOBBY


def test_wrong_click_on_an_obvious_screen_is_reported_and_causes_no_damage(harness):
    manager, detector, _config, _path = harness
    frame = degraded_frame(0.1)

    outcome = manager.add(frame, 1, detector.detect(frame), S.UNKNOWN)

    assert detector.thresholds[S.LOBBY] == 0.75
    assert detector.detect(frame).state is S.LOBBY
    assert "pode se repetir" in outcome.message


def test_deleting_an_example_undoes_its_effect(harness):
    manager, detector, _config, _path = harness
    frame = degraded_frame()
    manager.add(frame, 1, detector.detect(frame), S.LOBBY)
    assert detector.detect(frame).state is S.LOBBY

    manager.delete(manager.samples()[0].id)

    assert manager.count == 0
    assert detector.thresholds[S.LOBBY] == 0.75
    assert detector.detect(frame).state is S.UNKNOWN


def test_same_frame_is_not_saved_twice(harness):
    manager, detector, _config, _path = harness
    frame = paste(blank_frame(), LOBBY_PATCH, 100, 100)
    detection = detector.detect(frame)

    manager.add(frame, 7, detection, S.LOBBY)
    repeated = manager.add(frame, 7, detection, S.LOBBY)
    assert not repeated.saved
    assert "já foi marcado" in repeated.message
    assert manager.count == 1

    manager.add(frame, 8, detection, S.LOBBY)
    assert manager.count == 2


def test_example_gets_all_scores_even_after_a_partial_analysis(tmp_path):
    templates = tmp_path / "templates"
    write_template(templates, "lobby", "a", LOBBY_PATCH)
    write_template(templates, "in_match", "a", LOADING_PATCH)
    config = recognition_config(LOBBY={"folder": "lobby"}, IN_MATCH={"folder": "in_match"})
    detector = ScreenDetector(config, templates)
    manager = FeedbackManager(tmp_path / "feedback", detector, config)
    frame = paste(paste(blank_frame(), LOADING_PATCH, 100, 100), LOBBY_PATCH, 800, 500)
    partial = detector.detect(frame, S.IN_MATCH)
    assert list(partial.screens) == [S.IN_MATCH]

    outcome = manager.add(frame, 1, partial, S.LOBBY)

    assert manager.samples()[0].scores[S.LOBBY] > 0.9
    assert manager.samples()[0].predicted is S.IN_MATCH
    assert "recorte novo" not in outcome.message


def test_screen_the_templates_cannot_see_is_reported(harness):
    manager, detector, _config, _path = harness
    frame = paste(blank_frame(), pattern(9, 240, 120), 100, 100)

    outcome = manager.add(frame, 1, detector.detect(frame), S.LOBBY)

    assert "recorte novo" in outcome.message
    assert detector.thresholds[S.LOBBY] == 0.75


def test_conflicting_examples_are_reported(harness):
    manager, detector, _config, _path = harness
    frame = degraded_frame(0.3)
    detection = detector.detect(frame)
    manager.add(frame, 1, detection, S.LOBBY)

    outcome = manager.add(frame, 2, detection, S.UNKNOWN)

    assert "conflito" in outcome.message


def test_examples_survive_restart(harness):
    manager, detector, config, path = harness
    frame = degraded_frame()
    manager.add(frame, 1, detector.detect(frame), S.LOBBY)
    learned = detector.thresholds[S.LOBBY]

    new_detector = ScreenDetector(config, path / "templates")
    new_manager = FeedbackManager(path / "feedback", new_detector, config)

    assert new_manager.count == 1
    assert new_manager.samples()[0].label is S.LOBBY
    assert new_detector.thresholds[S.LOBBY] == learned
    assert new_manager.load_frame(new_manager.samples()[0].id).shape == frame.shape


def test_invalid_example_file_is_ignored(harness):
    manager, detector, config, path = harness
    (path / "feedback" / "quebrado.json").write_text("{ nao e json", encoding="utf-8")
    (path / "feedback" / "incompleto.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")

    assert FeedbackManager(path / "feedback", detector, config).count == 0


def test_samples_are_newest_first_and_filterable(harness):
    manager, detector, _config, _path = harness
    frame = blank_frame()
    detection = detector.detect(frame)
    manager.add(frame, 1, detection, S.LOBBY)
    manager.add(frame, 2, detection, S.UNKNOWN)

    assert [item.label for item in manager.samples()] == [S.UNKNOWN, S.LOBBY]
    assert [item.label for item in manager.samples(S.LOBBY)] == [S.LOBBY]


def test_examples_are_rescored_when_templates_change(harness):
    manager, detector, config, path = harness
    frame = degraded_frame()
    manager.add(frame, 1, detector.detect(frame), S.LOBBY)
    old_score = manager.samples()[0].scores[S.LOBBY]

    # Um recorte novo, igual ao que aparece no quadro, passa a reconhecer a variação com folga.
    write_template(path / "templates", "lobby", "novo", cv2.cvtColor(frame[300:420, 400:640], cv2.COLOR_BGR2GRAY))
    new_detector = ScreenDetector(config, path / "templates")
    new_manager = FeedbackManager(path / "feedback", new_detector, config)

    assert new_detector.thresholds[S.LOBBY] == 0.75  # exemplos desatualizados não contam
    assert new_manager.rescore_outdated() == 1
    assert new_manager.samples()[0].scores[S.LOBBY] > old_score
    assert new_detector.thresholds[S.LOBBY] == 0.75
    assert new_manager.rescore_outdated() == 0
