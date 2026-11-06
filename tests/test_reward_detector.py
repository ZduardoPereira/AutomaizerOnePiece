import sqlite3
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import RecognitionConfig, RewardConfig
from core.event_manager import EventManager, EventType
from core.paths import TEMPLATES_DIR
from database.database import Database
from database.models import RewardRecord
from database.repositories import RewardRepository
from recognition.interface_detector import read_image
from recognition.reward_detector import RewardDetector, RewardEvidence, RewardState, RewardTracker
from tests.helpers import blank_frame

SCREENS_DIR = Path(__file__).parent / "fixtures" / "screens"
TOKEN_DIR = TEMPLATES_DIR / "rewards" / "event_token"
WORKING_HEIGHT = RecognitionConfig().working_height
GOLD = (20, 150, 220)


@pytest.fixture(scope="module")
def detector():
    return RewardDetector(RewardConfig(), TEMPLATES_DIR, WORKING_HEIGHT)


def gold_frame():
    return np.full((1080, 1920, 3), GOLD, dtype=np.uint8)


def paste_centered(frame, name, top, center_x=960):
    image = read_image(TOKEN_DIR / f"{name}.png")
    left = center_x - image.shape[1] // 2
    frame[top : top + image.shape[0], left : left + image.shape[1]] = image
    return frame


def full_banner(frame=None):
    frame = gold_frame() if frame is None else frame
    paste_centered(frame, "icone_token", 130)
    paste_centered(frame, "mensagem_voce_encontrou", 300)
    paste_centered(frame, "nome_acesso_a_loja_pirata", 360)
    return frame


def test_ordinary_frame_has_no_evidence(detector):
    assert detector.inspect(blank_frame()) == RewardEvidence()


def test_full_banner_is_sufficient(detector):
    evidence = detector.inspect(full_banner())
    assert evidence.sufficient
    assert evidence.suspicious
    assert evidence.icon > 0.9
    assert evidence.message > 0.9
    assert evidence.name > 0.9
    assert evidence.confidence > 0.9


def test_icon_alone_is_not_enough(detector):
    evidence = detector.inspect(paste_centered(gold_frame(), "icone_token", 130))
    assert evidence.icon > 0.9
    assert evidence.suspicious
    assert not evidence.sufficient


def test_name_alone_is_not_enough(detector):
    evidence = detector.inspect(paste_centered(gold_frame(), "nome_acesso_a_loja_pirata", 360))
    assert evidence.name > 0.9
    assert evidence.suspicious
    assert not evidence.sufficient


def test_generic_message_with_icon_but_without_the_name_is_not_enough(detector):
    frame = paste_centered(gold_frame(), "icone_token", 130)
    paste_centered(frame, "mensagem_voce_encontrou", 300)
    assert not detector.inspect(frame).sufficient


def test_name_with_one_more_evidence_is_enough(detector):
    # O ícone do jogo pode estar animado; a mensagem também serve de segunda evidência.
    frame = paste_centered(gold_frame(), "mensagem_voce_encontrou", 300)
    paste_centered(frame, "nome_acesso_a_loja_pirata", 360)
    assert detector.inspect(frame).sufficient


def test_evidence_out_of_alignment_is_not_enough(detector):
    frame = paste_centered(gold_frame(), "icone_token", 130, center_x=500)
    paste_centered(frame, "nome_acesso_a_loja_pirata", 360, center_x=1300)
    evidence = detector.inspect(frame)
    assert evidence.icon > 0.9
    assert evidence.name > 0.9
    assert not evidence.sufficient


def test_evidence_in_the_wrong_order_is_not_enough(detector):
    frame = paste_centered(gold_frame(), "nome_acesso_a_loja_pirata", 130)
    paste_centered(frame, "icone_token", 300)
    assert not detector.inspect(frame).sufficient


def test_name_with_only_a_weak_generic_message_is_not_enough():
    # Outras telas chegam a 67% no recorte da mensagem, abaixo do limite exigido dela.
    config = RewardConfig(message_threshold=0.99)
    strict = RewardDetector(config, TEMPLATES_DIR, WORKING_HEIGHT)
    frame = paste_centered(gold_frame(), "mensagem_voce_encontrou", 300)
    paste_centered(frame, "nome_acesso_a_loja_pirata", 360)
    frame = cv2.GaussianBlur(frame, (0, 0), 2.5)
    assert not strict.inspect(frame).sufficient


def test_frame_without_gold_skips_the_search(detector, monkeypatch):
    calls = []
    monkeypatch.setattr(RewardDetector, "_best", staticmethod(lambda *_args: calls.append(1)))
    assert detector.inspect(blank_frame()) == RewardEvidence()
    assert calls == []


@pytest.mark.parametrize("size", [(1280, 720), (2560, 1440)])
def test_resolution_does_not_matter(detector, size):
    interpolation = cv2.INTER_AREA if size[1] < 1080 else cv2.INTER_CUBIC
    assert detector.inspect(cv2.resize(full_banner(), size, interpolation=interpolation)).sufficient


def test_missing_templates_are_reported(tmp_path):
    assert RewardDetector(RewardConfig(), tmp_path, WORKING_HEIGHT).warnings
    assert RewardDetector(RewardConfig(), TEMPLATES_DIR, WORKING_HEIGHT).warnings == []


needs_print = pytest.mark.skipif(
    not (SCREENS_DIR / "reward_token_stream.png").exists(), reason="Print do token indisponível em tests/fixtures/screens"
)


@pytest.fixture(scope="module")
def token_print():
    return read_image(SCREENS_DIR / "reward_token_stream.png")


def banner_over(base, token, scale, sharpen=0.0):
    """Cola o aviso do print em um quadro de 1080p, no tamanho que ele pode ter com outra escala de HUD."""
    big = cv2.resize(token[85:330, 180:1215], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    if sharpen:
        blurred = cv2.GaussianBlur(big, (0, 0), 1.5 * scale)
        big = cv2.addWeighted(big, 1 + sharpen, blurred, -sharpen, 0)
    frame = cv2.resize(base, (1920, 1080))
    height, width = big.shape[:2]
    left = (1920 - width) // 2
    if left < 0:
        big = big[:, -left : width + left]
        width = big.shape[1]
        left = 0
    top = 118
    height = min(height, 1080 - top)
    frame[top : top + height, left : left + width] = big[:height]
    return frame


@needs_print
def test_recognizes_the_original_print(detector, token_print):
    evidence = detector.inspect(token_print)
    assert evidence.sufficient
    assert evidence.confidence > 0.9


@needs_print
@pytest.mark.parametrize("base_name", ["real_in_match_warmup", "in_match_solo", "in_match_teammate_knocked"])
@pytest.mark.parametrize("scale", [1.15, 1.20, 1.25, 1.30, 1.38])
@pytest.mark.parametrize("sharpen", [0.0, 1.0], ids=["como_no_video", "mais_nitido"])
def test_recognizes_the_banner_in_other_sizes(detector, token_print, base_name, scale, sharpen):
    """Do tamanho medido na tela do usuário até o tamanho proporcional ao do vídeo."""
    base = read_image(SCREENS_DIR / f"{base_name}.png")
    assert detector.inspect(banner_over(base, token_print, scale, sharpen)).sufficient


@pytest.fixture(scope="module")
def common_item():
    """Print real da tela do usuário: o mesmo aviso do token, mas de um item comum."""
    return cv2.resize(read_image(SCREENS_DIR / "in_match_item_found_elemental.png"), (1920, 1080))


def token_on_real_banner(common_item, token_print, ratio, sharpen=0.0):
    """Troca, no aviso real do usuário, o nome e o ícone do item pelos do token."""
    frame = common_item.copy()
    block = cv2.resize(token_print[237:294, 588:818], None, fx=ratio, fy=ratio, interpolation=cv2.INTER_CUBIC)
    if sharpen:
        blurred = cv2.GaussianBlur(block, (0, 0), 1.5)
        block = cv2.addWeighted(block, 1 + sharpen, blurred, -sharpen, 0)
    height, width = block.shape[:2]
    left = 965 - width // 2
    frame[300 : 300 + height, left : left + width] = block
    icon = cv2.resize(token_print[92:168, 662:736], None, fx=1.45, fy=1.45, interpolation=cv2.INTER_CUBIC)
    icon_height, icon_width = icon.shape[:2]
    frame[130 : 130 + icon_height, 960 - icon_width // 2 : 960 - icon_width // 2 + icon_width] = icon
    return frame


@needs_print
@pytest.mark.parametrize("ratio", [1.15, 1.20, 1.25, 1.30, 1.38])
@pytest.mark.parametrize("sharpen", [0.0, 1.0, 2.0], ids=["como_no_video", "nitido", "muito_nitido"])
def test_recognizes_the_token_in_the_users_real_banner(detector, common_item, token_print, ratio, sharpen):
    evidence = detector.inspect(token_on_real_banner(common_item, token_print, ratio, sharpen))
    assert evidence.sufficient
    assert evidence.name > 0.8
    assert evidence.message > 0.85


@needs_print
def test_common_item_with_the_same_banner_is_not_the_token(detector, common_item):
    evidence = detector.inspect(common_item)
    assert evidence.message > 0.85  # o aviso foi encontrado, e a busca de fato rodou
    assert evidence.name < RewardConfig().candidate_threshold
    assert evidence.icon < RewardConfig().icon_threshold
    assert not evidence.sufficient
    assert not evidence.suspicious


@needs_print
@pytest.mark.parametrize("background", [(90, 90, 90), (80, 40, 20), (200, 200, 200)], ids=["cinza", "azul", "claro"])
def test_color_filter_lets_the_banner_through_without_help_from_the_scenery(detector, common_item, background):
    # No print há um chapéu laranja que soma dourado; o filtro não pode depender dele.
    frame = np.full((1080, 1920, 3), background, dtype=np.uint8)
    frame[240:400, 760:1170] = common_item[240:400, 760:1170]
    assert detector._has_gold(frame)


@needs_print
@pytest.mark.parametrize(
    "path",
    [path for path in sorted(SCREENS_DIR.glob("*.png")) if "reward" not in path.name],
    ids=lambda path: path.stem,
)
def test_other_screens_are_not_mistaken_for_the_token(detector, path):
    evidence = detector.inspect(read_image(path))
    assert not evidence.sufficient
    assert not evidence.suspicious


SUFFICIENT = RewardEvidence(0.9, 0.8, 0.85, True, True)
SUSPICIOUS = RewardEvidence(0.8, 0.3, 0.4, False, True)
NOTHING = RewardEvidence()


def frame_with(value):
    return np.full((20, 30, 3), value, dtype=np.uint8)


def repository_at(path):
    return RewardRepository(Database(path.parent / "monitor.db"))


def make_tracker(config, events, path, **kwargs):
    """Cada chamada abre o banco de novo, como ao reabrir o aplicativo."""
    return RewardTracker(config, events, path, repository_at(path), **kwargs)


@pytest.fixture
def harness(tmp_path):
    events = EventManager()
    config = RewardConfig(confirm_frames=3, max_missed_frames=2)
    return make_tracker(config, events, tmp_path / "rewards"), events, config, tmp_path / "rewards"


def types_of(events):
    return [event.type for event in events.recent(50)][::-1]


def feed(tracker, *evidences):
    for index, evidence in enumerate(evidences):
        tracker.observe(frame_with(index + 1), evidence)


def test_starts_not_found(harness):
    tracker, _events, _config, _path = harness
    status = tracker.status
    assert status.state is RewardState.NOT_FOUND
    assert not status.token_obtained
    assert tracker.is_searching


def test_first_evidence_becomes_candidate(harness):
    tracker, events, _config, _path = harness
    feed(tracker, SUFFICIENT)
    assert tracker.status.state is RewardState.CANDIDATE
    assert not tracker.status.token_obtained
    assert types_of(events) == [EventType.REWARD_CANDIDATE]


def test_confirmed_after_consecutive_captures_and_waits_for_match_end(harness):
    tracker, events, _config, path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)

    status = tracker.status
    assert status.state is RewardState.WAITING_FOR_MATCH_END
    assert status.token_obtained
    assert not tracker.is_searching
    assert not status.confirmed_by_user
    assert status.confidence == pytest.approx(SUFFICIENT.confidence)
    assert status.detected_at is not None
    assert status.evidence_path.exists()
    assert read_image(status.evidence_path).shape == (20, 30, 3)
    assert repository_at(path).load("event_token").status == RewardState.WAITING_FOR_MATCH_END.value
    assert types_of(events) == [
        EventType.REWARD_CANDIDATE,
        EventType.REWARD_CONFIRMED,
        EventType.REWARD_WAITING_MATCH_END,
    ]


def test_evidence_is_the_best_frame_seen(harness):
    tracker, _events, _config, _path = harness
    better = RewardEvidence(0.99, 0.9, 0.99, True, True)
    tracker.observe(frame_with(10), SUFFICIENT)
    tracker.observe(frame_with(200), better)
    tracker.observe(frame_with(30), SUFFICIENT)
    assert read_image(tracker.status.evidence_path)[0, 0, 0] == 200


def test_similar_icon_never_confirms_by_itself(harness):
    tracker, _events, _config, _path = harness
    feed(tracker, *[SUSPICIOUS] * 30)
    assert tracker.status.state is RewardState.CANDIDATE
    assert not tracker.status.token_obtained


def test_short_gaps_are_tolerated(harness):
    tracker, _events, _config, _path = harness
    feed(tracker, SUFFICIENT, NOTHING, NOTHING, SUFFICIENT, NOTHING, SUFFICIENT)
    assert tracker.status.token_obtained


def test_long_gap_starts_over_and_keeps_the_frame_for_review(harness):
    tracker, events, _config, _path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, NOTHING, NOTHING, NOTHING)

    status = tracker.status
    assert status.state is RewardState.NOT_FOUND
    assert not status.token_obtained
    assert status.candidate_path.exists()
    assert status.candidate_confidence == pytest.approx(SUFFICIENT.confidence)
    assert EventType.REWARD_REVIEW_NEEDED in types_of(events)

    feed(tracker, SUFFICIENT, SUFFICIENT)
    assert not tracker.status.token_obtained


def test_keeps_only_the_best_candidate(harness):
    tracker, _events, _config, path = harness
    tracker.observe(frame_with(10), SUFFICIENT)
    feed(tracker, NOTHING, NOTHING, NOTHING)
    assert read_image(tracker.status.candidate_path)[0, 0, 0] == 10

    tracker.observe(frame_with(50), SUSPICIOUS)
    feed(tracker, NOTHING, NOTHING, NOTHING)
    assert read_image(tracker.status.candidate_path)[0, 0, 0] == 10
    assert len(list(path.glob("*.png"))) == 1


def test_no_duplicates_after_confirmation(harness):
    tracker, events, _config, path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    confirmed_at = tracker.status.detected_at

    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT, SUFFICIENT)

    assert tracker.status.detected_at == confirmed_at
    assert types_of(events).count(EventType.REWARD_CONFIRMED) == 1
    assert len(list(path.glob("token_*.png"))) == 1


def test_match_over_stops_and_persists(harness):
    tracker, events, config, path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)

    tracker.on_match_over()
    tracker.on_match_over()

    assert tracker.status.state is RewardState.STOPPED
    assert tracker.status.token_obtained
    assert types_of(events).count(EventType.REWARD_STOPPED) == 1
    assert make_tracker(config, EventManager(), path).status.state is RewardState.STOPPED


def test_match_over_without_token_keeps_searching_and_saves_candidate(harness):
    tracker, _events, _config, _path = harness
    feed(tracker, SUSPICIOUS)

    tracker.on_match_over()

    assert tracker.status.state is RewardState.NOT_FOUND
    assert tracker.status.candidate_path is not None
    assert tracker.is_searching


def test_stopping_the_monitor_keeps_waiting_for_the_match_end(harness):
    tracker, _events, _config, _path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    tracker.interrupt()
    assert tracker.status.state is RewardState.WAITING_FOR_MATCH_END


def test_interrupt_ends_a_confirmation_in_progress(harness):
    tracker, _events, _config, _path = harness
    feed(tracker, SUFFICIENT)
    tracker.interrupt()
    assert tracker.status.state is RewardState.NOT_FOUND
    assert tracker.status.candidate_path is not None


def test_token_survives_restart_without_duplicating(harness):
    tracker, events, config, path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    original = tracker.status

    new_events = EventManager()
    reopened = make_tracker(config, new_events, path)
    feed(reopened, SUFFICIENT, SUFFICIENT, SUFFICIENT)

    assert reopened.status == original
    assert new_events.recent() == []
    assert len(list(path.glob("token_*.png"))) == 1


def test_candidate_survives_restart(harness):
    tracker, _events, config, path = harness
    feed(tracker, SUFFICIENT, NOTHING, NOTHING, NOTHING)
    assert make_tracker(config, EventManager(), path).status.candidate_path is not None


@pytest.mark.parametrize(
    ("in_match", "state"), [(True, RewardState.WAITING_FOR_MATCH_END), (False, RewardState.STOPPED)]
)
def test_user_can_confirm_the_candidate(harness, in_match, state):
    tracker, events, _config, path = harness
    feed(tracker, SUFFICIENT, NOTHING, NOTHING, NOTHING)

    tracker.confirm_candidate(in_match)

    status = tracker.status
    assert status.state is state
    assert status.token_obtained
    assert status.confirmed_by_user
    assert status.evidence_path.exists()
    assert status.candidate_path is None
    assert not (path / "candidato.png").exists()
    assert EventType.REWARD_CONFIRMED in types_of(events)


def test_confirm_without_candidate_does_nothing(harness):
    tracker, _events, _config, _path = harness
    tracker.confirm_candidate(True)
    assert tracker.status.state is RewardState.NOT_FOUND


def test_user_can_dismiss_the_candidate(harness):
    tracker, _events, _config, path = harness
    feed(tracker, SUFFICIENT, NOTHING, NOTHING, NOTHING)

    tracker.dismiss_candidate()

    assert tracker.status.candidate_path is None
    assert tracker.status.candidate_confidence == 0.0
    assert not (path / "candidato.png").exists()
    assert not tracker.status.token_obtained


def test_automatic_confirmation_replaces_an_old_candidate(harness):
    tracker, _events, _config, path = harness
    feed(tracker, SUFFICIENT, NOTHING, NOTHING, NOTHING)
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    assert tracker.status.candidate_path is None
    assert not (path / "candidato.png").exists()


def test_reset_erases_everything_and_searches_again(harness):
    tracker, events, _config, path = harness
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)

    tracker.reset()

    assert tracker.status.state is RewardState.NOT_FOUND
    assert tracker.is_searching
    assert list(path.iterdir()) == []
    assert EventType.REWARD_RESET in types_of(events)
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    assert tracker.status.token_obtained


def test_transitional_states_on_disk_are_normalized(tmp_path):
    config = RewardConfig()
    path = tmp_path / "rewards"
    tracker = make_tracker(config, EventManager(), path)
    feed(tracker, SUFFICIENT, SUFFICIENT, SUFFICIENT)
    repository = repository_at(path)
    record = repository.load(config.reward_type)

    repository.save(replace(record, status="CONFIRMED"))
    assert make_tracker(config, EventManager(), path).status.state is RewardState.WAITING_FOR_MATCH_END

    repository.save(replace(record, status="CANDIDATE"))
    assert make_tracker(config, EventManager(), path).status.state is RewardState.NOT_FOUND


@pytest.mark.parametrize(
    "record",
    [RewardRecord("event_token", "NAO_EXISTE"), RewardRecord("event_token", "STOPPED", detected_at="ontem")],
)
def test_invalid_record_is_ignored(tmp_path, record):
    path = tmp_path / "rewards"
    repository_at(path).save(record)
    assert make_tracker(RewardConfig(), EventManager(), path).status.state is RewardState.NOT_FOUND


def test_token_remembers_the_match_where_it_appeared(tmp_path):
    path = tmp_path / "rewards"
    match_id = Database(tmp_path / "monitor.db").execute("INSERT INTO matches (started_at) VALUES ('2026-11-05')")
    tracker = make_tracker(RewardConfig(confirm_frames=2), EventManager(), path, current_match_id=lambda: match_id)

    feed(tracker, SUFFICIENT, SUFFICIENT)

    assert tracker.status.match_id == match_id
    assert repository_at(path).load("event_token").match_id == match_id
    assert make_tracker(RewardConfig(), EventManager(), path).status.match_id == match_id


def test_candidate_keeps_its_match_after_that_match_ended(tmp_path):
    path = tmp_path / "rewards"
    match_id = Database(tmp_path / "monitor.db").execute("INSERT INTO matches (started_at) VALUES ('2026-11-05')")
    current = [match_id]
    tracker = make_tracker(RewardConfig(), EventManager(), path, current_match_id=lambda: current[0])
    feed(tracker, SUSPICIOUS)

    current[0] = None
    tracker.on_match_over()
    assert tracker.status.match_id == match_id

    tracker.confirm_candidate(in_match=False)
    assert tracker.status.match_id == match_id
    assert repository_at(path).load("event_token").match_id == match_id


def test_dismissing_the_candidate_forgets_its_match(tmp_path):
    path = tmp_path / "rewards"
    match_id = Database(tmp_path / "monitor.db").execute("INSERT INTO matches (started_at) VALUES ('2026-11-05')")
    tracker = make_tracker(RewardConfig(), EventManager(), path, current_match_id=lambda: match_id)
    feed(tracker, SUSPICIOUS)
    tracker.interrupt()

    tracker.dismiss_candidate()

    assert tracker.status.match_id is None


def test_database_failure_does_not_lose_the_token_in_memory(tmp_path):
    class BrokenRepository:
        def load(self, _reward_type):
            return None

        def save(self, _record):
            raise sqlite3.OperationalError("banco travado")

        def delete(self, _reward_type):
            raise sqlite3.OperationalError("banco travado")

    events = EventManager()
    tracker = RewardTracker(RewardConfig(confirm_frames=2), events, tmp_path / "rewards", BrokenRepository())

    feed(tracker, SUFFICIENT, SUFFICIENT)
    assert tracker.status.token_obtained
    assert EventType.REWARD_CONFIRMED in types_of(events)

    tracker.reset()
    assert not tracker.status.token_obtained
