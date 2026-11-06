import json
import os

import pytest
from pydantic import ValidationError

from core.config import AppConfig, CaptureConfig, NotificationConfig, RewardConfig, load_config, save_config


def test_missing_file_creates_defaults(tmp_path):
    path = tmp_path / "config.json"
    config, warning = load_config(path)
    assert warning is None
    assert config == AppConfig()
    assert path.exists()


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig(capture=CaptureConfig(fps=12, process_names=["jogo.exe"]))
    save_config(config, path)
    assert load_config(path) == (config, None)


@pytest.mark.parametrize("content", ["{ quebrado", json.dumps({"capture": {"fps": -1}}), json.dumps({"desconhecido": 1})])
def test_invalid_file_is_preserved_and_defaults_restored(tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content, encoding="utf-8")

    config, warning = load_config(path)

    assert config == AppConfig()
    assert warning is not None
    backups = list(tmp_path.glob("config.json.invalid-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == content
    assert load_config(path) == (AppConfig(), None)


@pytest.mark.parametrize("values", [{"fps": 0}, {"fps": 31}, {"window_poll_seconds": 0.1}, {"process_names": [" ", ""]}])
def test_capture_rejects_out_of_range_values(values):
    with pytest.raises(ValidationError):
        CaptureConfig(**values)


def test_name_lists_are_trimmed():
    assert CaptureConfig(window_titles=[" Fortnite ", ""]).window_titles == ["Fortnite"]


@pytest.mark.parametrize(
    "values",
    [{"scales": []}, {"scales": [0]}, {"search_region": [0, 0, 1]}, {"search_region": [0.5, 0, 0.2, 1]}, {"confirm_frames": 1}],
)
def test_reward_config_rejects_invalid_values(values):
    with pytest.raises(ValidationError):
        RewardConfig(**values)


def test_missing_new_sections_use_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"capture": {"fps": 7}}), encoding="utf-8")
    config, warning = load_config(path)
    assert warning is None
    assert config.capture.fps == 7
    assert config.rewards == RewardConfig()


def test_notifications_default_to_the_file_only():
    config = NotificationConfig()
    assert config.file_enabled
    assert config.webhook_url == ""


@pytest.mark.parametrize("url", ["ftp://servidor", "servidor.com", "c:/pasta"])
def test_webhook_must_be_a_web_address(url):
    with pytest.raises(ValidationError):
        NotificationConfig(webhook_url=url)


def test_webhook_address_is_trimmed_and_can_be_empty():
    assert NotificationConfig(webhook_url="  https://exemplo.com/tick ").webhook_url == "https://exemplo.com/tick"
    assert NotificationConfig(webhook_url="   ").webhook_url == ""


@pytest.mark.parametrize("seconds", [0.5, 61])
def test_card_time_has_limits(seconds):
    with pytest.raises(ValidationError):
        NotificationConfig(card_seconds=seconds)


def test_assistance_comes_on_and_old_files_get_it_too(tmp_path):
    assert AppConfig().assistant.enabled is True

    path = tmp_path / "config.json"
    path.write_text('{"assistant": {"press_escape": true}}', encoding="utf-8")
    config, warning = load_config(path)

    assert warning is None
    assert config.assistant.enabled is True
    assert config.assistant.press_escape is True


def test_saving_waits_for_a_file_held_for_a_moment(tmp_path, monkeypatch):
    monkeypatch.setattr("core.config._SAVE_RETRY_SECONDS", 0.0)
    real_replace = os.replace
    failures = [PermissionError("com o OneDrive"), PermissionError("com o OneDrive")]

    def held_replace(source, target):
        if failures:
            raise failures.pop()
        real_replace(source, target)

    monkeypatch.setattr("core.config.os.replace", held_replace)
    path = tmp_path / "config.json"

    save_config(AppConfig(), path)

    assert failures == []
    assert path.exists()


def test_saving_gives_up_if_the_file_stays_held(tmp_path, monkeypatch):
    monkeypatch.setattr("core.config._SAVE_RETRY_SECONDS", 0.0)

    def held_replace(_source, _target):
        raise PermissionError("com o OneDrive")

    monkeypatch.setattr("core.config.os.replace", held_replace)

    with pytest.raises(PermissionError):
        save_config(AppConfig(), tmp_path / "config.json")
