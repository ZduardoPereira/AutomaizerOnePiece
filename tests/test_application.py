import json
import time
from datetime import datetime

import numpy as np
import pytest

from core.application import Application
from core.capture import CaptureState
from core.config import AppConfig, CaptureConfig, RecognitionConfig, SquadConfig, load_config
from core.event_manager import EventType
from core.recognition_service import Analysis
from core.state_machine import GameState
from database.models import MonitorControl
from recognition.interface_detector import Detection
from recognition.match_detector import MatchResult
from recognition.reward_detector import RewardEvidence, RewardState


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setattr("core.application.configure_logging", lambda *_args: None)
    app = make_application(tmp_path)
    yield app
    app.shutdown()


def make_application(path):
    return Application(
        config_path=path / "config.json",
        logs_dir=path / "logs",
        feedback_dir=path / "feedback",
        rewards_dir=path / "rewards",
        database_path=path / "monitor.db",
        notifications_path=path / "notifications.jsonl",
    )


def event_types(app):
    return [event.type for event in app.events.recent()]


def make_analysis(frame_id=1):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    return Analysis(frame, frame_id, Detection(GameState.UNKNOWN, 0.1, {}), datetime.now(), 1.0)


def test_start_and_stop_monitoring(application):
    application.start_monitoring()
    assert application.is_monitoring
    application.start_monitoring()
    assert event_types(application).count(EventType.MONITORING_STARTED) == 1

    application.stop_monitoring()
    assert not application.is_monitoring
    assert application.snapshot().state is CaptureState.STOPPED
    assert EventType.MONITORING_STOPPED in event_types(application)


def test_emergency_stop_stops_capture_and_is_always_recorded(application):
    application.emergency_stop()
    assert EventType.EMERGENCY_STOP in event_types(application)

    application.start_monitoring()
    application.emergency_stop()
    assert not application.is_monitoring
    assert event_types(application).count(EventType.EMERGENCY_STOP) == 2


def test_monitored_time_accumulates_only_while_running(application):
    assert application.monitored_seconds == 0
    application.start_monitoring()
    time.sleep(0.05)
    application.stop_monitoring()

    elapsed = application.monitored_seconds
    assert elapsed > 0
    time.sleep(0.05)
    assert application.monitored_seconds == elapsed


def test_update_config_persists_and_reaches_capture(application, tmp_path):
    config = AppConfig(capture=CaptureConfig(fps=9))
    application.update_config(config)

    assert application.config == config
    assert application.capture._config.fps == 9
    assert load_config(tmp_path / "config.json")[0] == config


def test_update_config_reaches_recognition(application):
    application.update_config(
        AppConfig(recognition=RecognitionConfig(fps=4, confirm_frames=5), squad=SquadConfig(confirm_frames=3))
    )

    assert application.recognition._config.fps == 4
    assert application.recognition._machine.confirm_frames == 5
    assert application.recognition._survival._config.confirm_frames == 3


def test_stop_also_stops_recognition(application):
    application.start_monitoring()
    thread = application.recognition._thread
    assert thread.is_alive()

    application.stop_monitoring()

    assert not thread.is_alive()
    assert application.recognition_snapshot().state is GameState.UNKNOWN


def test_feedback_is_saved_published_and_can_be_deleted(application):
    message = application.record_feedback(make_analysis(), GameState.UNKNOWN)

    assert "Salvo como Tela desconhecida" in message
    assert application.feedback.count == 1
    assert EventType.FEEDBACK_RECORDED in event_types(application)

    application.delete_feedback(application.feedback.samples()[0].id)
    assert application.feedback.count == 0


def test_repeated_feedback_is_not_published(application):
    application.record_feedback(make_analysis(), GameState.UNKNOWN)
    assert "já foi marcado" in application.record_feedback(make_analysis(), GameState.UNKNOWN)
    assert event_types(application).count(EventType.FEEDBACK_RECORDED) == 1


def test_feedback_write_failure_becomes_a_message(application, monkeypatch):
    def fail(*_args):
        raise OSError("disco cheio")

    monkeypatch.setattr(application.feedback, "add", fail)

    assert "disco cheio" in application.record_feedback(make_analysis(), GameState.LOBBY)


def test_missing_templates_warning_reaches_events(tmp_path, monkeypatch):
    monkeypatch.setattr("core.application.configure_logging", lambda *_args: None)

    app = Application(
        config_path=tmp_path / "config.json",
        logs_dir=tmp_path / "logs",
        templates_dir=tmp_path / "sem_recortes",
        feedback_dir=tmp_path / "feedback",
        rewards_dir=tmp_path / "rewards",
        database_path=tmp_path / "monitor.db",
        notifications_path=tmp_path / "notifications.jsonl",
    )

    messages = [event.message for event in app.events.recent(50, {EventType.RECOGNITION_WARNING})]
    app.shutdown()
    assert any("token" in message for message in messages)
    assert any("baús" in message for message in messages)
    assert any("cartão" in message for message in messages)
    assert any("Voltar ao Lobby" in message for message in messages)


def test_invalid_config_warning_reaches_events(tmp_path, monkeypatch):
    monkeypatch.setattr("core.application.configure_logging", lambda *_args: None)
    path = tmp_path / "config.json"
    path.write_text("{ quebrado", encoding="utf-8")

    app = make_application(tmp_path)

    assert EventType.CONFIG_WARNING in event_types(app)
    app.shutdown()


def test_reward_candidate_can_be_confirmed_dismissed_and_reset(application):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    evidence = RewardEvidence(0.9, 0.8, 0.85, False, True)
    application.rewards.observe(frame, evidence)
    application.rewards.interrupt()
    assert application.reward_status().candidate_path is not None

    application.dismiss_reward_candidate()
    assert application.reward_status().candidate_path is None

    application.rewards.observe(frame, evidence)
    application.rewards.interrupt()
    application.confirm_reward_candidate()
    assert application.reward_status().state is RewardState.STOPPED  # fora de partida, nada a aguardar

    application.reset_reward()
    assert application.reward_status().state is RewardState.NOT_FOUND


def test_turning_the_assistance_on_also_starts_monitoring(application):
    assert not application.assistant_status().active

    application.set_assistant_enabled(True)

    assert application.assistant_status().active
    assert application.is_monitoring


def test_turning_the_assistance_off_keeps_monitoring(application):
    application.set_assistant_enabled(True)

    application.set_assistant_enabled(False)

    assert not application.assistant_status().active
    assert application.is_monitoring


def test_assistance_comes_on_by_itself_when_the_app_opens(tmp_path, no_logging):
    # Era o defeito: a escolha não ficava guardada, e o app abria sem agir e sem dizer por quê.
    app = make_application(tmp_path)
    assert app.config.assistant.enabled is True

    app.start()

    assert app.assistant_status().active
    assert app.is_monitoring
    app.shutdown()


def test_assistance_turned_off_stays_off_after_reopening(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.start()
    app.set_assistant_enabled(False)
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.start()

    assert reopened.config.assistant.enabled is False
    assert not reopened.assistant_status().active
    assert not reopened.is_monitoring
    reopened.shutdown()


def test_assistance_turned_on_again_stays_on_after_reopening(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.set_assistant_enabled(False)
    app.set_assistant_enabled(True)
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.start()

    assert reopened.assistant_status().active
    reopened.shutdown()


def test_saving_other_settings_keeps_the_assistance_choice(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.set_assistant_enabled(False)

    app.set_press_escape(True)
    app.shutdown()

    saved = load_config(tmp_path / "config.json")[0].assistant
    assert saved.enabled is False
    assert saved.press_escape is True


def test_assistance_works_even_if_the_choice_cannot_be_saved(application, monkeypatch):
    def locked(*_args):
        raise PermissionError("com o OneDrive")

    monkeypatch.setattr("core.application.save_config", locked)

    application.set_assistant_enabled(True)
    assert application.assistant_status().active

    application.set_assistant_enabled(False)
    assert not application.assistant_status().active


def test_emergency_stop_also_stops_the_assistant(application):
    application.set_assistant_enabled(True)
    application.emergency_stop()
    assert not application.assistant_status().active
    assert not application.is_monitoring


def test_emergency_stop_is_not_undone_by_reopening(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.start()
    app.emergency_stop()
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.start()

    assert not reopened.assistant_status().active
    assert not reopened.is_monitoring
    reopened.shutdown()


def test_emergency_stop_works_even_if_the_choice_cannot_be_saved(application, monkeypatch):
    def locked(*_args):
        raise PermissionError("com o OneDrive")

    monkeypatch.setattr("core.application.save_config", locked)
    application.set_assistant_enabled(True)

    application.emergency_stop()

    assert not application.assistant_status().active
    assert not application.is_monitoring
    assert EventType.EMERGENCY_STOP in event_types(application)


def test_closing_twice_is_harmless(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.shutdown()
    app.shutdown()


def test_escape_option_starts_off_and_persists(application, tmp_path):
    assert application.config.assistant.press_escape is False

    application.set_press_escape(True)

    assert application.config.assistant.press_escape is True
    assert load_config(tmp_path / "config.json")[0].assistant.press_escape is True
    assert application.assistant._config.press_escape is True


TOKEN_ON_SCREEN = RewardEvidence(0.9, 0.8, 0.85, True, True)
FINISHED = "RECOMPENSA OBTIDA! Monitoramento finalizado."


@pytest.fixture
def no_logging(monkeypatch):
    monkeypatch.setattr("core.application.configure_logging", lambda *_args: None)


def find_token(app):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    for _ in range(app.config.rewards.confirm_frames):
        app.rewards.observe(frame, TOKEN_ON_SCREEN)


def finished_messages(app):
    return [event.message for event in app.events.recent(50, {EventType.MONITOR_FINISHED})]


def test_token_ends_monitoring_only_when_the_match_ends(application):
    application.set_assistant_enabled(True)
    find_token(application)

    application.process_shutdown()
    assert application.shutdown_manager.control.token_obtained
    assert application.is_monitoring
    assert not application.monitoring_finished

    application.rewards.on_match_over()
    application.process_shutdown()
    application.process_shutdown()

    assert application.monitoring_finished
    assert not application.is_monitoring
    assert not application.assistant_status().active
    assert finished_messages(application) == [FINISHED]


def test_finished_monitoring_tells_other_programs(application, tmp_path):
    find_token(application)
    application.rewards.on_match_over()
    application.process_shutdown()

    lines = (tmp_path / "notifications.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["type"] == "reward_obtained"
    assert record["message"] == FINISHED


def test_finished_monitoring_cannot_be_restarted(application):
    find_token(application)
    application.rewards.on_match_over()
    application.process_shutdown()

    application.start_monitoring()
    application.set_assistant_enabled(True)

    assert not application.is_monitoring
    assert not application.assistant_status().active


def test_finished_monitoring_survives_restart_without_a_new_notice(tmp_path, no_logging):
    app = make_application(tmp_path)
    find_token(app)
    app.rewards.on_match_over()
    app.process_shutdown()
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.process_shutdown()
    reopened.start_monitoring()

    assert reopened.monitoring_finished
    assert not reopened.is_monitoring
    assert finished_messages(reopened) == []
    reopened.shutdown()


def test_closing_while_waiting_for_the_match_end_finishes_later(tmp_path, no_logging):
    app = make_application(tmp_path)
    find_token(app)
    app.process_shutdown()
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.process_shutdown()
    assert not reopened.monitoring_finished
    assert reopened.reward_status().state is RewardState.WAITING_FOR_MATCH_END

    reopened.rewards.on_match_over()
    reopened.process_shutdown()

    assert reopened.monitoring_finished
    assert finished_messages(reopened) == [FINISHED]
    reopened.shutdown()


def test_closing_before_the_shutdown_was_applied_completes_it_on_restart(tmp_path, no_logging):
    app = make_application(tmp_path)
    find_token(app)
    app.rewards.on_match_over()
    app.shutdown()

    reopened = make_application(tmp_path)
    reopened.process_shutdown()

    assert reopened.monitoring_finished
    assert finished_messages(reopened) == [FINISHED]
    reopened.shutdown()


def test_token_confirmed_after_the_match_finishes_right_away(application):
    application.rewards.observe(np.zeros((20, 30, 3), dtype=np.uint8), RewardEvidence(0.9, 0.8, 0.85, False, True))
    application.rewards.interrupt()

    application.confirm_reward_candidate()
    application.process_shutdown()

    assert application.monitoring_finished


def test_erasing_the_token_allows_monitoring_again(application):
    find_token(application)
    application.rewards.on_match_over()
    application.process_shutdown()

    application.reset_reward()
    application.process_shutdown()
    application.start_monitoring()

    assert not application.monitoring_finished
    assert application.is_monitoring
    assert application.shutdown_manager.control == MonitorControl()


def test_token_is_marked_on_the_match_where_it_appeared(application):
    application.history.match_started()
    application.history.flush()
    find_token(application)
    application.process_shutdown()
    application.history.match_ended(MatchResult.VICTORY, 600)
    application.history.flush()

    assert application.history.recent_matches()[0].token_obtained


def test_events_reach_the_database(application):
    application.history.match_started()
    application.events.publish(EventType.MATCH_STARTED, "Partida iniciada")
    application.history.flush()

    match_id = application.history.current_match_id
    assert [event.description for event in application.history.events_of(match_id)] == ["Partida iniciada"]


def test_damaged_database_does_not_stop_the_app(tmp_path, no_logging):
    (tmp_path / "monitor.db").write_bytes(b"isto nao e um banco de dados" * 100)

    app = make_application(tmp_path)

    assert EventType.CONFIG_WARNING in event_types(app)
    assert list(tmp_path.glob("monitor.danificado-*.db"))
    app.shutdown()


def test_shutdown_can_run_twice(tmp_path, no_logging):
    app = make_application(tmp_path)
    app.shutdown()
    app.shutdown()
