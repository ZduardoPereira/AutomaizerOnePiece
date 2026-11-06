import threading
import time

import numpy as np
import pytest

from core.capture import CaptureSnapshot, CaptureState
from core.config import RecognitionConfig, RewardConfig, SquadConfig
from core.event_manager import EventManager, EventType
from core.recognition_service import RecognitionService
from core.state_machine import GameState
from database.database import Database
from database.repositories import RewardRepository
from recognition.hud_reader import HudNumbers, MatchNumbers
from recognition.interface_detector import Detection, ScreenResult
from recognition.match_detector import MatchResult, MatchStats
from recognition.reward_detector import RewardEvidence, RewardState, RewardStatus, RewardTracker
from recognition.squad_detector import MemberReading, MemberStatus, SquadReading
from recognition.survival_detector import SquadCondition


def make_detection(state, confidence=0.9, victory=False, menu=False):
    screens = {}
    if state is not GameState.UNKNOWN:
        screens[state] = ScreenResult(state, confidence, 0.75, ())
    if victory:
        screens[GameState.VICTORY] = ScreenResult(GameState.VICTORY, 0.9, 0.75, ())
    if menu:
        screens[GameState.GAME_MENU] = ScreenResult(GameState.GAME_MENU, 0.9, 0.75, ())
    return Detection(state, confidence, screens)


class FakeCapture:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = CaptureSnapshot()

    def snapshot(self):
        with self.lock:
            return self.current

    def show_frame(self, value=1, **changes):
        with self.lock:
            frame = np.full((4, 4, 3), value, dtype=np.uint8)
            self.current = CaptureSnapshot(
                state=CaptureState.CAPTURING, frame=frame, frame_id=self.current.frame_id + 1, **changes
            )

    def set_state(self, state):
        with self.lock:
            self.current = CaptureSnapshot(state=state, frame_id=self.current.frame_id)


class FakeDetector:
    """Devolve a detecção programada e conta os quadros analisados."""

    def __init__(self):
        self.detection = make_detection(GameState.UNKNOWN, 0.1)
        self.error = None
        self.calls = 0

    def detect(self, _frame, _likely=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.detection


class FakeSquadDetector:
    """Devolve a leitura programada e conta quantas vezes o HUD foi lido."""

    def __init__(self):
        self.reading = SquadReading()
        self.calls = 0

    def read(self, _frame):
        self.calls += 1
        return self.reading


class FakeRewardDetector:
    """Devolve a evidência programada e conta quantos quadros foram examinados."""

    def __init__(self):
        self.evidence = RewardEvidence()
        self.calls = 0

    def inspect(self, _frame):
        self.calls += 1
        return self.evidence


class FakeAssistant:
    """Guarda o que o assistente receberia a cada quadro."""

    def __init__(self):
        self.contexts = []
        self.forgotten = 0

    def update(self, _frame, context, _window):
        self.contexts.append(context)

    def forget_game(self):
        self.forgotten += 1


class NoRewards:
    """Substitui o registro do token nos testes que não tratam dele."""

    is_searching = False
    status = RewardStatus()

    def observe(self, _frame, _evidence):
        raise AssertionError("não deveria examinar o token")

    def on_match_over(self):
        pass

    def interrupt(self):
        pass


class FakeHudReader:
    def __init__(self):
        self.numbers = HudNumbers()
        self.calls = 0

    def read(self, _frame, _detection):
        self.calls += 1
        return self.numbers


class FakeHistory:
    """Guarda o que seria gravado no banco."""

    def __init__(self):
        self.started = 0
        self.ended = []
        self.squad = None
        self.numbers = None

    def match_started(self):
        self.started += 1
        self.squad = self.numbers = None

    def match_ended(self, result, _duration):
        self.ended.append((result, self.squad, self.numbers))

    def update_live(self, squad, numbers):
        self.squad = squad
        self.numbers = numbers


class FakeFeedback:
    def __init__(self, rescored=0):
        self.rescored = rescored

    def rescore_outdated(self):
        return self.rescored


def make_service(
    config,
    capture,
    events,
    detector,
    squad_detector=None,
    feedback=None,
    reward_detector=None,
    rewards=None,
    assistant=None,
    hud_reader=None,
    history=None,
):
    return RecognitionService(
        config,
        capture,
        events,
        detector,
        feedback or FakeFeedback(),
        squad_detector or FakeSquadDetector(),
        SquadConfig(),
        reward_detector or FakeRewardDetector(),
        rewards or NoRewards(),
        assistant or FakeAssistant(),
        hud_reader or FakeHudReader(),
        history or FakeHistory(),
    )


@pytest.fixture
def harness():
    capture = FakeCapture()
    detector = FakeDetector()
    events = EventManager()
    config = RecognitionConfig(fps=15, confirm_frames=2, unknown_grace_seconds=0.0)
    service = make_service(config, capture, events, detector)
    service.start()
    yield service, capture, detector, events
    service.stop()


@pytest.fixture
def squad_harness():
    capture = FakeCapture()
    detector = FakeDetector()
    squad = FakeSquadDetector()
    config = RecognitionConfig(fps=15, confirm_frames=1, unknown_grace_seconds=0.0)
    service = make_service(config, capture, EventManager(), detector, squad)
    service.start()
    yield service, capture, detector, squad
    service.stop()


@pytest.fixture
def reward_harness(tmp_path):
    capture = FakeCapture()
    detector = FakeDetector()
    reward_detector = FakeRewardDetector()
    events = EventManager()
    repository = RewardRepository(Database(tmp_path / "monitor.db"))
    tracker = RewardTracker(RewardConfig(confirm_frames=2), events, tmp_path / "rewards", repository)
    config = RecognitionConfig(fps=15, confirm_frames=1, unknown_grace_seconds=30.0)
    service = make_service(config, capture, events, detector, reward_detector=reward_detector, rewards=tracker)
    service.start()
    yield service, capture, detector, reward_detector, tracker
    service.stop()


TOKEN_ON_SCREEN = RewardEvidence(0.9, 0.8, 0.85, True, True)


FULL_SQUAD = SquadReading(
    (MemberReading(MemberStatus.ALIVE, 1.0, 1.0), MemberReading(MemberStatus.ALIVE, 1.0, 1.0)),
    MemberReading(MemberStatus.ALIVE, 1.0, 1.0),
)


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    pytest.fail("condição não atendida a tempo")


def feed_until(capture, condition):
    """Fornece quadros novos, como a captura faz, até a condição ser atendida."""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        capture.show_frame()
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("condição não atendida a tempo")


def test_recognizes_and_publishes_confirmed_screen(harness):
    service, capture, detector, events = harness
    detector.detection = make_detection(GameState.LOBBY, 0.93)

    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)

    assert "Lobby" in events.recent(1, {EventType.SCREEN_CHANGED})[0].message
    assert "93%" in events.recent(1, {EventType.SCREEN_CHANGED})[0].message
    analysis = service.snapshot().analysis
    assert analysis.detection.state is GameState.LOBBY
    assert analysis.frame is not None


def test_same_frame_is_analyzed_only_once(harness):
    _service, capture, detector, _events = harness
    capture.show_frame()
    wait_for(lambda: detector.calls == 1)
    time.sleep(0.25)
    assert detector.calls == 1


def test_black_frames_are_skipped_and_state_is_kept(harness):
    service, capture, detector, _events = harness
    detector.detection = make_detection(GameState.LOADING)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOADING)

    calls = detector.calls
    detector.detection = make_detection(GameState.UNKNOWN, 0.0)
    capture.show_frame(0, is_black=True)
    time.sleep(0.2)

    assert detector.calls == calls
    assert service.snapshot().state is GameState.LOADING


def test_paused_capture_keeps_last_analysis_for_feedback(harness):
    service, capture, detector, _events = harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)

    capture.set_state(CaptureState.BACKGROUND)
    time.sleep(0.15)

    snapshot = service.snapshot()
    assert snapshot.state is GameState.IN_MATCH
    assert snapshot.analysis is not None


def test_game_closed_resets_state_and_closes_match(harness):
    service, capture, detector, events = harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().matches.in_match)

    capture.set_state(CaptureState.SEARCHING)
    wait_for(lambda: service.snapshot().state is GameState.UNKNOWN)

    snapshot = service.snapshot()
    assert snapshot.analysis is None
    assert snapshot.matches == MatchStats(identified=1, victories=0, defeats=0, in_match=False)
    assert len(events.recent(10, {EventType.MATCH_ENDED})) == 1


def test_full_match_updates_statistics(harness):
    service, capture, detector, _events = harness
    for state in (GameState.LOADING, GameState.IN_MATCH):
        detector.detection = make_detection(state)
        feed_until(capture, lambda s=state: service.snapshot().state is s)

    detector.detection = make_detection(GameState.MATCH_RESULT, victory=True)
    feed_until(capture, lambda: service.snapshot().state is GameState.MATCH_RESULT)

    assert service.snapshot().matches == MatchStats(identified=1, victories=1, defeats=0, in_match=False)


def test_analysis_errors_are_reported_once_and_recovered(harness):
    service, capture, detector, events = harness
    detector.error = ValueError("quadro inválido")
    feed_until(capture, lambda: detector.calls >= 3)
    assert len(events.recent(10, {EventType.RECOGNITION_ERROR})) == 1

    detector.error = None
    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)


def test_stop_forgets_screen_but_keeps_session_statistics(harness):
    service, capture, detector, _events = harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().matches.in_match)

    service.stop()

    snapshot = service.snapshot()
    assert snapshot.state is GameState.UNKNOWN
    assert snapshot.analysis is None
    assert snapshot.matches.identified == 1
    assert not snapshot.matches.in_match


def test_restart_continues_analyzing(harness):
    service, capture, detector, _events = harness
    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)
    service.stop()
    assert service.snapshot().state is GameState.UNKNOWN

    service.start()
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)


def test_update_config_applies_to_running_machine(harness):
    service, capture, detector, _events = harness
    service.update_config(RecognitionConfig(fps=15, confirm_frames=1, unknown_grace_seconds=0.0), SquadConfig())
    detector.detection = make_detection(GameState.LOBBY)
    capture.show_frame()
    wait_for(lambda: service.snapshot().state is GameState.LOBBY)


def test_squad_is_read_during_the_match(squad_harness):
    service, capture, detector, squad = squad_harness
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)

    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.SQUAD_ACTIVE)

    assert service.snapshot().squad.active_count == 3


def test_squad_is_not_read_outside_the_match(squad_harness):
    service, capture, detector, squad = squad_harness
    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)
    assert squad.calls == 0


def test_menu_open_keeps_last_squad_status(squad_harness):
    service, capture, detector, squad = squad_harness
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().squad.active_count == 3)

    calls = squad.calls
    squad.reading = SquadReading()
    detector.detection = make_detection(GameState.ELIMINATED, menu=True)
    feed_until(capture, lambda: service.snapshot().state is GameState.ELIMINATED)

    assert squad.calls == calls
    assert service.snapshot().squad.active_count == 3


def test_defeat_screen_eliminates_the_squad(squad_harness):
    service, capture, detector, squad = squad_harness
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().squad.active_count == 3)

    detector.detection = make_detection(GameState.DEFEAT)
    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.SQUAD_ELIMINATED)

    assert service.snapshot().matches.defeats == 1


def test_leaving_the_match_forgets_the_squad(squad_harness):
    service, capture, detector, squad = squad_harness
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().squad.active_count == 3)

    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.UNKNOWN)


def test_new_match_starts_with_a_clean_squad(squad_harness):
    service, capture, detector, squad = squad_harness
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().squad.active_count == 3)

    detector.detection = make_detection(GameState.LOADING)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOADING)

    squad.reading = SquadReading((), MemberReading(MemberStatus.ALIVE, 1.0, 0.0))
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.NO_SQUAD)


def test_token_is_confirmed_during_the_match(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)

    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)

    assert tracker.status.state is RewardState.WAITING_FOR_MATCH_END


def test_banner_covering_the_hud_is_still_examined(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)

    # O quadro não é reconhecido, mas a partida segue valendo durante a tolerância.
    detector.detection = make_detection(GameState.UNKNOWN, 0.2)
    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)


@pytest.mark.parametrize("state", [GameState.LOBBY, GameState.ELIMINATED, GameState.VICTORY, GameState.MATCH_RESULT])
def test_token_is_not_searched_outside_the_players_own_match(reward_harness, state):
    service, capture, detector, reward_detector, tracker = reward_harness
    reward_detector.evidence = TOKEN_ON_SCREEN
    detector.detection = make_detection(state)

    feed_until(capture, lambda: service.snapshot().state is state)

    assert reward_detector.calls == 0
    assert not tracker.status.token_obtained


def test_end_of_match_after_token_reaches_stopped(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)
    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)

    detector.detection = make_detection(GameState.MATCH_RESULT)
    feed_until(capture, lambda: tracker.status.state is RewardState.STOPPED)


def test_token_is_not_examined_again_after_confirmed(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)
    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)

    calls = reward_detector.calls
    feed_until(capture, lambda: detector.calls > calls + 5)
    assert reward_detector.calls == calls


def test_stopping_the_monitor_does_not_finish_the_wait(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)
    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)

    service.stop()

    assert tracker.status.state is RewardState.WAITING_FOR_MATCH_END


def test_closing_the_game_ends_the_wait(reward_harness):
    service, capture, detector, reward_detector, tracker = reward_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)
    reward_detector.evidence = TOKEN_ON_SCREEN
    feed_until(capture, lambda: tracker.status.token_obtained)

    capture.set_state(CaptureState.SEARCHING)
    wait_for(lambda: tracker.status.state is RewardState.STOPPED)


def assistant_service():
    capture = FakeCapture()
    detector = FakeDetector()
    squad = FakeSquadDetector()
    assistant = FakeAssistant()
    config = RecognitionConfig(fps=15, confirm_frames=1, unknown_grace_seconds=0.0)
    service = make_service(config, capture, EventManager(), detector, squad, assistant=assistant)
    return service, capture, detector, squad, assistant


def test_assistant_receives_screen_squad_and_match_on_each_analysis():
    service, capture, detector, squad, assistant = assistant_service()
    squad.reading = FULL_SQUAD
    detector.detection = make_detection(GameState.IN_MATCH)
    service.start()
    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.SQUAD_ACTIVE and assistant.contexts)
    wait_for(lambda: assistant.contexts[-1].condition is SquadCondition.SQUAD_ACTIVE)
    service.stop()

    context = assistant.contexts[-1]
    assert context.state is GameState.IN_MATCH
    assert context.frame_state is GameState.IN_MATCH
    assert context.match_number == 1
    assert context.token_obtained is False
    assert context.menu_open is False


def test_assistant_knows_when_the_menu_is_open():
    service, capture, detector, _squad, assistant = assistant_service()
    detector.detection = make_detection(GameState.ELIMINATED, menu=True)
    service.start()
    feed_until(capture, lambda: bool(assistant.contexts))
    service.stop()
    assert assistant.contexts[-1].menu_open is True


def test_assistant_forgets_the_step_when_the_game_closes():
    service, capture, _detector, _squad, assistant = assistant_service()
    service.start()
    capture.set_state(CaptureState.SEARCHING)
    wait_for(lambda: assistant.forgotten >= 1)
    service.stop()


def test_rescored_examples_are_announced():
    events = EventManager()
    service = make_service(RecognitionConfig(), FakeCapture(), events, FakeDetector(), feedback=FakeFeedback(rescored=3))
    service.start()
    wait_for(lambda: bool(events.recent(5, {EventType.RECOGNITION_WARNING})))
    service.stop()
    assert "3 exemplo(s)" in events.recent(5, {EventType.RECOGNITION_WARNING})[0].message


@pytest.fixture
def hud_harness():
    capture = FakeCapture()
    detector = FakeDetector()
    squad = FakeSquadDetector()
    hud_reader = FakeHudReader()
    history = FakeHistory()
    config = RecognitionConfig(fps=15, confirm_frames=1, unknown_grace_seconds=0.0)
    service = make_service(config, capture, EventManager(), detector, squad, hud_reader=hud_reader, history=history)
    service.start()
    yield service, capture, detector, squad, hud_reader, history
    service.stop()


def test_hud_numbers_reach_the_snapshot(hud_harness):
    service, capture, detector, _squad, hud_reader, _history = hud_harness
    hud_reader.numbers = HudNumbers(2, 3, 13, 382)
    detector.detection = make_detection(GameState.IN_MATCH)

    feed_until(capture, lambda: service.snapshot().numbers.kills == 2)

    numbers = service.snapshot().numbers
    assert (numbers.assists, numbers.players_left) == (3, 13)
    assert numbers.chests_opened == 0


def test_hud_is_not_read_with_the_menu_open_or_outside_a_match(hud_harness):
    service, capture, detector, _squad, hud_reader, _history = hud_harness
    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)
    assert hud_reader.calls == 0

    detector.detection = make_detection(GameState.IN_MATCH, menu=True)
    feed_until(capture, lambda: service.snapshot().state is GameState.IN_MATCH)
    capture.show_frame()
    time.sleep(0.2)
    assert hud_reader.calls == 0


def test_numbers_stay_after_the_match_and_reset_on_the_next_one(hud_harness):
    service, capture, detector, _squad, hud_reader, _history = hud_harness
    hud_reader.numbers = HudNumbers(kills=4)
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().numbers.kills == 4)

    detector.detection = make_detection(GameState.LOBBY)
    feed_until(capture, lambda: service.snapshot().state is GameState.LOBBY)
    assert service.snapshot().numbers.kills == 4

    hud_reader.numbers = HudNumbers()
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().matches.identified == 2)
    feed_until(capture, lambda: service.snapshot().numbers == MatchNumbers())


def test_history_receives_the_final_state_of_a_defeat(hud_harness):
    service, capture, detector, squad, hud_reader, history = hud_harness
    squad.reading = FULL_SQUAD
    hud_reader.numbers = HudNumbers(kills=1, assists=2, players_left=20)
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: service.snapshot().numbers.kills == 1)
    assert history.started == 1

    detector.detection = make_detection(GameState.DEFEAT)
    feed_until(capture, lambda: history.ended)

    result, final_squad, numbers = history.ended[0]
    assert result is MatchResult.DEFEAT
    assert final_squad.condition is SquadCondition.SQUAD_ELIMINATED
    assert final_squad.self_member.status is MemberStatus.ELIMINATED
    assert numbers.kills == 1


def test_defeat_seen_without_a_match_in_progress_still_marks_the_squad(hud_harness):
    service, capture, detector, _squad, _hud_reader, history = hud_harness
    detector.detection = make_detection(GameState.DEFEAT)

    feed_until(capture, lambda: service.snapshot().squad.condition is SquadCondition.NO_SQUAD)

    assert history.started == 1
    assert [item[0] for item in history.ended] == [MatchResult.DEFEAT]


def test_closing_the_game_ends_the_match_in_the_history(hud_harness):
    _service, capture, detector, _squad, _hud_reader, history = hud_harness
    detector.detection = make_detection(GameState.IN_MATCH)
    feed_until(capture, lambda: history.started == 1)

    capture.set_state(CaptureState.SEARCHING)

    wait_for(lambda: len(history.ended) == 1)
    assert history.ended[0][0] is MatchResult.UNDETERMINED


def test_screen_changes_carry_their_confidence(harness):
    _service, capture, detector, events = harness
    detector.detection = make_detection(GameState.LOBBY, 0.93)
    feed_until(capture, lambda: events.recent(1, {EventType.SCREEN_CHANGED}))

    assert events.recent(1, {EventType.SCREEN_CHANGED})[0].confidence == pytest.approx(0.93)
