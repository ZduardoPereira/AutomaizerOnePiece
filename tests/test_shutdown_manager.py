import sqlite3

import pytest

from core.event_manager import EventManager, EventType
from core.shutdown_manager import FINISHED_MESSAGE, ShutdownManager
from database.database import Database
from database.models import MonitorControl
from database.repositories import MonitorControlRepository


@pytest.fixture
def harness(tmp_path):
    database = Database(tmp_path / "monitor.db")
    repository = MonitorControlRepository(database)
    events = EventManager()
    yield ShutdownManager(repository, events), repository, events
    database.close()


def finished(events):
    return [event.message for event in events.recent(50, {EventType.MONITOR_FINISHED})]


def test_starts_allowing_monitoring(harness):
    manager, _repository, _events = harness
    assert manager.can_monitor
    assert manager.control == MonitorControl()
    assert not manager.complete_if_pending()


def test_match_end_without_token_changes_nothing(harness):
    manager, _repository, events = harness
    manager.on_match_over()
    assert not manager.complete_if_pending()
    assert manager.can_monitor
    assert finished(events) == []


def test_token_keeps_monitoring_the_current_match(harness):
    manager, repository, _events = harness
    manager.on_token_obtained()
    assert manager.control == MonitorControl(token_obtained=True)
    assert repository.load() == manager.control
    assert manager.can_monitor
    assert not manager.complete_if_pending()


def test_match_end_after_the_token_finishes_once(harness):
    manager, repository, events = harness
    manager.on_token_obtained()
    manager.on_match_over()
    assert repository.load() == MonitorControl(True, True, False)

    assert manager.complete_if_pending()
    assert not manager.complete_if_pending()

    assert repository.load() == MonitorControl(True, False, True)
    assert not manager.can_monitor
    assert finished(events) == [FINISHED_MESSAGE]


def test_finished_message_is_the_specified_one():
    assert FINISHED_MESSAGE == "RECOMPENSA OBTIDA! Monitoramento finalizado."


def test_match_end_after_finishing_does_not_notify_again(harness):
    manager, _repository, events = harness
    manager.on_token_obtained()
    manager.on_match_over()
    manager.complete_if_pending()

    manager.on_match_over()

    assert not manager.complete_if_pending()
    assert len(finished(events)) == 1


def test_state_survives_reopening(harness):
    manager, repository, events = harness
    manager.on_token_obtained()
    manager.on_match_over()
    manager.complete_if_pending()

    reopened = ShutdownManager(repository, EventManager())

    assert not reopened.can_monitor
    assert reopened.control == MonitorControl(True, False, True)
    assert len(finished(events)) == 1


def test_pending_shutdown_is_completed_after_reopening(harness):
    manager, repository, _events = harness
    manager.on_token_obtained()
    manager.on_match_over()

    new_events = EventManager()
    reopened = ShutdownManager(repository, new_events)

    assert reopened.complete_if_pending()
    assert finished(new_events) == [FINISHED_MESSAGE]


def test_reset_allows_monitoring_again(harness):
    manager, repository, _events = harness
    manager.on_token_obtained()
    manager.on_match_over()
    manager.complete_if_pending()

    manager.reset()

    assert manager.can_monitor
    assert repository.load() == MonitorControl()


def test_database_failure_does_not_block_the_shutdown():
    class BrokenRepository:
        def load(self):
            return MonitorControl()

        def save(self, _control):
            raise sqlite3.OperationalError("banco travado")

    events = EventManager()
    manager = ShutdownManager(BrokenRepository(), events)

    manager.on_token_obtained()
    manager.on_match_over()

    assert manager.complete_if_pending()
    assert not manager.can_monitor
    assert finished(events) == [FINISHED_MESSAGE]
