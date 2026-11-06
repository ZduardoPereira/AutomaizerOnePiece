import logging
import sqlite3
import threading
import time

import pytest

from core.event_manager import AppEvent, EventManager, EventType
from core.history_recorder import HistoryRecorder
from database.database import Database
from database.models import EventRecord
from database.repositories import MatchRepository
from recognition.hud_reader import MatchNumbers
from recognition.match_detector import MatchResult, MatchTracker
from recognition.squad_detector import MemberStatus as M
from recognition.survival_detector import SquadCondition as C
from recognition.survival_detector import SquadMember, SquadStatus


def squad(*teammates, condition=C.SQUAD_ACTIVE, you=M.ALIVE):
    members = (SquadMember(True, you), *(SquadMember(False, status) for status in teammates))
    return SquadStatus(condition, members)


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "monitor.db")
    yield db
    db.close()


class FlushingRecorder(HistoryRecorder):
    """Espera cada gravação terminar, para o teste conferir o resultado na linha seguinte."""

    def _submit(self, job):
        super()._submit(job)
        self.flush()


@pytest.fixture
def recorder(database):
    recorder = FlushingRecorder(database)
    yield recorder
    recorder.close()


def latest(recorder):
    return recorder.recent_matches()[0]


def test_match_is_created_at_start_and_completed_at_the_end(recorder):
    recorder.match_started()
    match_id = recorder.current_match_id
    assert latest(recorder).result is None

    recorder.update_live(squad(M.ALIVE, M.ELIMINATED), MatchNumbers(4, 2, 11, 3))
    recorder.match_ended(MatchResult.VICTORY, 900)

    match = latest(recorder)
    assert match.id == match_id
    assert match.result == "VICTORY"
    assert match.duration_seconds == 900
    assert match.player_status == "ALIVE"
    assert match.squad_status == "SQUAD_ACTIVE"
    assert (match.kills, match.assists, match.players_left, match.chests_opened) == (4, 2, 11, 3)
    assert recorder.current_match_id is None


def test_numbers_of_a_match_do_not_leak_into_the_next(recorder):
    recorder.match_started()
    recorder.update_live(squad(M.ALIVE), MatchNumbers(9, 9, 9, 9))
    recorder.match_ended(MatchResult.DEFEAT, 60)

    recorder.match_started()
    recorder.match_ended(MatchResult.UNDETERMINED, 10)

    match = latest(recorder)
    assert (match.kills, match.assists, match.players_left, match.chests_opened) == (0, 0, None, None)
    assert match.player_status is None


def test_squad_members_follow_the_match(recorder):
    recorder.match_started()
    match_id = recorder.current_match_id

    recorder.update_live(squad(M.ALIVE, M.ALIVE), MatchNumbers())
    recorder.update_live(squad(M.KNOCKED, M.ALIVE), MatchNumbers())

    members = recorder.squad_of(match_id)
    assert [(member.player_number, member.status) for member in members] == [(1, "ALIVE"), (2, "KNOCKED"), (3, "ALIVE")]


def test_squad_is_only_written_when_it_changes(recorder, monkeypatch):
    recorder.match_started()
    saves = []
    monkeypatch.setattr(recorder._squad, "save", lambda *args: saves.append(args))

    for _ in range(5):
        recorder.update_live(squad(M.ALIVE), MatchNumbers())
    recorder.update_live(squad(M.KNOCKED), MatchNumbers())

    assert len(saves) == 2


def test_nothing_is_written_outside_a_match(recorder, database):
    recorder.update_live(squad(M.ALIVE), MatchNumbers(1))
    recorder.match_ended(MatchResult.VICTORY, 10)

    assert recorder.recent_matches() == []
    assert database.query("SELECT id FROM squad_members") == []


def test_events_are_linked_to_the_current_match(recorder):
    recorder.record_event(AppEvent(EventType.MONITORING_STARTED, "Monitoramento iniciado"))
    recorder.match_started()
    match_id = recorder.current_match_id
    recorder.record_event(AppEvent(EventType.SCREEN_CHANGED, "Partida (90%)", confidence=0.9))
    recorder.match_ended(MatchResult.DEFEAT, 5)
    recorder.record_event(AppEvent(EventType.MONITORING_STOPPED, "Monitoramento interrompido"))

    events = recorder.events_of(match_id)

    assert [(event.event_type, event.description, event.confidence) for event in events] == [
        ("SCREEN_CHANGED", "Partida (90%)", 0.9)
    ]
    assert isinstance(events[0], EventRecord)


def test_match_tracker_events_stay_inside_the_match(recorder):
    events = EventManager()
    events.add_sink(recorder.record_event)
    tracker = MatchTracker(events, recorder)
    tracker._start(0.0)
    match_id = recorder.current_match_id

    tracker._end(30.0, reached_result=True)

    descriptions = [event.description for event in recorder.events_of(match_id)]
    assert descriptions == ["Partida iniciada", "Partida encerrada: derrota (00:30)"]
    assert latest(recorder).result == "DEFEAT"
    assert latest(recorder).duration_seconds == 30


def test_totals_and_version_follow_the_matches(recorder):
    assert recorder.totals.matches == 0
    version = recorder.version

    recorder.match_started()
    assert recorder.totals.matches == 1
    recorder.update_live(squad(M.ALIVE), MatchNumbers(kills=2))
    recorder.match_ended(MatchResult.VICTORY, 10)

    assert recorder.version != version
    assert recorder.totals.victories == 1
    assert recorder.totals.kills == 2


def test_token_mark_uses_the_match_informed(recorder):
    recorder.match_started()
    match_id = recorder.current_match_id
    recorder.match_ended(MatchResult.DEFEAT, 5)
    version = recorder.version

    recorder.mark_token_obtained(match_id)

    assert latest(recorder).token_obtained
    assert recorder.version != version


def test_token_mark_without_a_match_is_ignored(recorder):
    recorder.mark_token_obtained(None)
    assert recorder.recent_matches() == []


def test_match_left_open_by_a_closed_app_is_closed_on_opening(database):
    open_match = MatchRepository(database).create("2026-11-05T10:00:00")

    recorder = FlushingRecorder(database)

    match = latest(recorder)
    assert match.id == open_match
    assert match.result == "UNDETERMINED"
    assert recorder.totals.matches == 1


def test_database_failure_never_reaches_the_monitoring(tmp_path):
    database = Database(tmp_path / "monitor.db")
    recorder = FlushingRecorder(database)
    recorder.match_started()
    match_id = recorder.current_match_id
    database.close()  # qualquer acesso agora falha

    recorder.update_live(squad(M.ALIVE), MatchNumbers(1))
    recorder.record_event(AppEvent(EventType.SCREEN_CHANGED, "Partida"))
    recorder.mark_token_obtained(match_id)
    recorder.match_ended(MatchResult.VICTORY, 10)
    recorder.match_started()

    assert recorder.current_match_id is None
    assert recorder.recent_matches() == []
    assert recorder.squad_of(match_id) == []
    assert recorder.events_of(match_id) == []


def test_only_database_errors_are_silenced(recorder, monkeypatch):
    def fail(*_args):
        raise sqlite3.OperationalError("travado")

    monkeypatch.setattr(recorder._matches, "create", fail)
    recorder.match_started()
    assert recorder.current_match_id is None

    def bug(*_args):
        raise KeyError("erro de programação")

    monkeypatch.setattr(recorder._matches, "create", bug)
    recorder.match_started()

    # A fila não pode parar por causa de uma gravação: as seguintes continuam.
    monkeypatch.undo()
    recorder.match_started()
    assert recorder.current_match_id is not None


def test_programming_error_in_a_write_is_logged(recorder, monkeypatch, caplog):
    def bug(*_args):
        raise KeyError("erro de programação")

    monkeypatch.setattr(recorder._matches, "create", bug)

    with caplog.at_level(logging.ERROR):
        recorder.match_started()

    assert "Falha ao gravar o histórico" in caplog.text


def test_slow_disk_does_not_hold_the_game_analysis(database, monkeypatch):
    recorder = HistoryRecorder(database)
    release = threading.Event()
    real_create = recorder._matches.create

    def slow_create(*args):
        release.wait(10)
        return real_create(*args)

    monkeypatch.setattr(recorder._matches, "create", slow_create)

    started = time.monotonic()
    recorder.match_started()
    recorder.record_event(AppEvent(EventType.SCREEN_CHANGED, "Partida"))
    recorder.update_live(squad(M.ALIVE), MatchNumbers(1))
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert recorder.current_match_id is None

    release.set()
    assert recorder.flush()
    match_id = recorder.current_match_id
    assert match_id is not None
    # A ordem é mantida: o evento pedido depois do início da partida fica ligado a ela.
    assert [event.description for event in recorder.events_of(match_id)] == ["Partida"]
    recorder.close()


def test_closing_finishes_what_was_requested(database):
    recorder = HistoryRecorder(database)
    recorder.match_started()
    recorder.match_ended(MatchResult.VICTORY, 10)

    recorder.close()

    assert latest(recorder).result == "VICTORY"
