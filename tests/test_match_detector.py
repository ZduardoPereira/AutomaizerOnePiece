import pytest

from core.event_manager import EventManager, EventType
from core.state_machine import GameState as S
from core.state_machine import Transition
from recognition.match_detector import MatchStats, MatchTracker


@pytest.fixture
def harness():
    events = EventManager()
    return MatchTracker(events), events


def play(tracker, *states, victory_visible=()):
    previous = S.UNKNOWN
    for now, state in enumerate(states):
        tracker.observe(Transition(previous, state, 0.9), state in victory_visible, float(now * 60))
        previous = state


def messages(events, event_type):
    return [event.message for event in events.recent(50, {event_type})][::-1]


def test_no_transition_changes_nothing():
    tracker = MatchTracker(EventManager())
    tracker.observe(None, True, 0.0)
    assert tracker.stats == MatchStats()


def test_menus_do_not_start_a_match():
    tracker = MatchTracker(EventManager())
    play(tracker, S.LOBBY, S.MATCHMAKING, S.LOADING)
    assert tracker.stats == MatchStats()


def test_victory_flow(harness):
    tracker, events = harness
    play(tracker, S.LOBBY, S.MATCHMAKING, S.LOADING, S.IN_MATCH, S.VICTORY, S.MATCH_RESULT)
    assert tracker.stats == MatchStats(identified=1, victories=1, defeats=0, in_match=False)
    assert "vitória" in messages(events, EventType.MATCH_ENDED)[0]


def test_victory_seen_only_on_result_screen_counts(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.MATCH_RESULT, victory_visible={S.MATCH_RESULT})
    assert tracker.stats.victories == 1


def test_result_screen_without_banner_is_a_defeat(harness):
    tracker, events = harness
    play(tracker, S.IN_MATCH, S.ELIMINATED, S.MATCH_RESULT)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=1, in_match=False)
    assert "derrota" in messages(events, EventType.MATCH_ENDED)[0]


def test_defeat_screen_ends_the_match_right_away(harness):
    tracker, events = harness
    play(tracker, S.IN_MATCH, S.DEFEAT)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=1, in_match=False)
    assert "derrota" in messages(events, EventType.MATCH_ENDED)[0]


def test_result_screen_after_defeat_is_not_counted_again(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.DEFEAT, S.MATCH_RESULT, S.LOBBY)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=1, in_match=False)


def test_defeat_screen_seen_after_starting_late_counts(harness):
    tracker, _events = harness
    play(tracker, S.DEFEAT)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=1, in_match=False)


def test_menu_during_match_does_not_end_it(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.GAME_MENU, S.IN_MATCH)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=0, in_match=True)


def test_leaving_after_elimination_is_a_defeat(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.ELIMINATED, S.LOBBY)
    assert tracker.stats.defeats == 1


def test_leaving_alive_is_undetermined(harness):
    tracker, events = harness
    play(tracker, S.IN_MATCH, S.LOBBY)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=0, in_match=False)
    assert "indeterminado" in messages(events, EventType.MATCH_ENDED)[0]


def test_reboot_continues_the_same_match(harness):
    tracker, events = harness
    play(tracker, S.IN_MATCH, S.ELIMINATED, S.IN_MATCH, S.VICTORY, S.MATCH_RESULT)
    assert tracker.stats == MatchStats(identified=1, victories=1, defeats=0, in_match=False)
    assert len(messages(events, EventType.PLAYER_RETURNED)) == 1


def test_returning_after_reboot_and_leaving_is_not_a_defeat(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.ELIMINATED, S.IN_MATCH, S.LOBBY)
    assert tracker.stats.defeats == 0


def test_unknown_screen_does_not_end_the_match(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.UNKNOWN, S.IN_MATCH)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=0, in_match=True)


def test_cancel_dialog_during_match_does_not_end_it(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.MATCHMAKING, S.IN_MATCH)
    assert tracker.stats == MatchStats(identified=1, victories=0, defeats=0, in_match=True)


def test_result_screen_without_a_match_is_not_counted(harness):
    tracker, _events = harness
    play(tracker, S.MATCH_RESULT, S.LOBBY)
    assert tracker.stats == MatchStats()


def test_two_matches_in_sequence(harness):
    tracker, _events = harness
    play(tracker, S.IN_MATCH, S.VICTORY, S.MATCH_RESULT, S.MATCHMAKING, S.LOADING, S.IN_MATCH, S.ELIMINATED, S.MATCH_RESULT)
    assert tracker.stats == MatchStats(identified=2, victories=1, defeats=1, in_match=False)


def test_close_ends_open_match_once(harness):
    tracker, events = harness
    play(tracker, S.IN_MATCH)
    tracker.close(10.0)
    tracker.close(20.0)
    assert not tracker.stats.in_match
    assert len(messages(events, EventType.MATCH_ENDED)) == 1
