"""Contador de cliques, integrado do Projeto click no mouse. Usa só pastas temporárias."""

import ctypes
import json
import logging
import time
from ctypes import wintypes

import pytest

from click_counter.counter import MAX_IDS, RECENT_CLICKS, ClickCounter


def line(id_="a", type_="click_done", **extra):
    data = {
        "id": id_,
        "type": type_,
        "button": "PLAY",
        "message": "Clique feito: Jogar",
        "created_at": "2026-11-05T21:14:03.512-03:00",
        "app": "AutomaizerOnePiece",
    }
    data.update(extra)
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


class Files:
    def __init__(self, directory):
        self.notifications = directory / "notifications.jsonl"
        self.state = directory / "click_counter.json"

    def counter(self, path=None):
        return ClickCounter(path or self.notifications, self.state)

    def append(self, data):
        with self.notifications.open("ab") as file:
            file.write(data)

    def saved(self):
        return json.loads(self.state.read_text(encoding="utf-8"))


@pytest.fixture
def files(tmp_path):
    return Files(tmp_path)


@pytest.fixture
def counter(files):
    """Contador que já fez a primeira leitura, com o arquivo de notificações ainda inexistente."""
    counter = files.counter()
    counter.poll()
    return counter


class exclusive_lock:
    """Abre o arquivo sem compartilhamento, como um programa que o trava de verdade."""

    def __init__(self, path):
        self.path = str(path)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    def __enter__(self):
        self.handle = self.kernel32.CreateFileW(self.path, 0x80000000, 0, None, 3, 0x80, None)
        if self.handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), "não foi possível travar", self.path)

    def __exit__(self, *_):
        self.kernel32.CloseHandle(self.handle)


# --- Primeira leitura -------------------------------------------------------


def test_count_is_unknown_before_the_first_reading(files):
    assert files.counter().count.ready is False


def test_existing_file_starts_at_the_end(files):
    files.append(line("old"))
    counter = files.counter()
    counter.poll()
    assert counter.count.ready
    assert counter.count.total == 0

    files.append(line("new"))
    counter.poll()
    assert counter.count.total == 1
    assert files.saved()["total_ticks"] == 1


def test_missing_file_starts_at_zero_and_counts_the_first_click(files, counter):
    assert files.saved()["position"] == 0
    files.append(line("first"))
    counter.poll()
    assert counter.count.total == 1


def test_line_being_written_at_startup_is_not_lost(files):
    full = line("partial")
    files.append(line("old") + full[:10])
    counter = files.counter()
    counter.poll()

    files.append(full[10:])
    counter.poll()

    assert counter.count.total == 1


def test_locked_file_does_not_become_position_zero(files):
    files.append(line("old"))
    counter = files.counter()
    with exclusive_lock(files.notifications):
        counter.poll()
    counter.poll()

    assert counter.count.total == 0
    assert files.saved()["position"] == files.notifications.stat().st_size


def test_another_file_starts_at_its_end_and_keeps_the_total(files, counter, tmp_path):
    files.append(line("a"))
    counter.poll()

    other = tmp_path / "other.jsonl"
    other.write_bytes(line("old-in-other"))
    moved = files.counter(other)
    moved.poll()

    assert moved.count.total == 1
    assert files.saved()["position"] == other.stat().st_size


def test_previous_project_state_is_continued(files):
    # O state.json do Projeto click no mouse tem o mesmo formato e pode ser copiado como está.
    files.append(line("already-counted"))
    position = files.notifications.stat().st_size
    files.state.write_text(
        json.dumps({"path": str(files.notifications), "position": position, "total_ticks": 67, "ids": ["already-counted"]}),
        encoding="utf-8",
    )
    files.append(line("new"))

    counter = files.counter()
    counter.poll()

    assert counter.count.total == 68


# --- Leitura ----------------------------------------------------------------


def test_only_the_clicks_done_count(files, counter):
    for kind in ("click_required", "info", "reward_obtained"):
        files.append(line(f"id-{kind}", kind))
    files.append(line("id-done"))
    counter.poll()

    assert counter.count.total == 1


def test_same_id_does_not_count_twice(files, counter):
    files.append(line("a") + line("a"))
    counter.poll()
    files.append(line("a"))
    counter.poll()

    assert counter.count.total == 1


def test_incomplete_line_waits_for_the_newline(files, counter):
    full = line("açaí", message="Clique feito: Aba de configurações do menu")
    cut = full.index("ç".encode()) + 1  # no meio de um caractere UTF-8
    files.append(full[:cut])
    counter.poll()
    assert counter.count.total == 0

    files.append(full[cut:])
    counter.poll()
    assert counter.count.total == 1
    assert files.saved()["position"] == len(full)


def test_invalid_lines_are_logged_and_skipped(files, counter, caplog):
    bad = b"{quebrado\n" + b"[1, 2]\n" + b"\xff\xfe\n" + b"\n" + b'{"type": "click_done"}\n'
    files.append(bad + line("good"))

    with caplog.at_level(logging.WARNING, logger="click_counter.counter"):
        counter.poll()

    assert len(caplog.records) == 5
    assert counter.count.total == 1


def test_locked_file_is_read_on_the_next_cycle(files, counter):
    files.append(line("a"))
    with exclusive_lock(files.notifications):
        counter.poll()
    assert counter.count.total == 0

    counter.poll()
    assert counter.count.total == 1


def test_shrunk_file_goes_back_to_the_start(files, counter):
    files.append(line("a") + line("b"))
    counter.poll()
    files.notifications.write_bytes(line("c"))
    counter.poll()

    assert counter.count.total == 3
    assert files.saved()["position"] == files.notifications.stat().st_size


def test_keeps_only_the_last_ids(files, counter):
    files.append(b"".join(line(f"id{i}") for i in range(MAX_IDS + 1)))
    counter.poll()
    assert len(files.saved()["ids"]) == MAX_IDS

    files.append(line(f"id{MAX_IDS}"))  # ainda guardado
    files.append(line("id0"))  # já saiu da lista
    counter.poll()
    assert counter.count.total == MAX_IDS + 2


def test_recent_clicks_come_newest_first_up_to_the_limit(files, counter):
    files.append(b"".join(line(f"id{i}", message=f"Clique {i}") for i in range(RECENT_CLICKS + 5)))
    counter.poll()

    recent = counter.count.recent
    assert len(recent) == RECENT_CLICKS
    assert recent[0].message == f"Clique {RECENT_CLICKS + 4}"
    assert recent[0].button == "PLAY"
    assert f"{recent[0].created_at:%H:%M:%S}" == "21:14:03"


# --- Reinício ---------------------------------------------------------------


def test_restart_keeps_total_and_position(files, counter):
    files.append(line("a"))
    counter.poll()

    reopened = files.counter()
    reopened.poll()

    assert reopened.count.total == 1


def test_click_written_while_closed_counts_on_restart(files, counter):
    files.append(line("while-closed"))

    reopened = files.counter()
    reopened.poll()

    assert reopened.count.total == 1


def test_id_is_remembered_across_restart(files, counter):
    files.append(line("a"))
    counter.poll()
    files.append(line("a"))

    reopened = files.counter()
    reopened.poll()

    assert reopened.count.total == 1


def test_same_path_in_another_case_is_not_a_first_run(files, counter):
    files.append(line("a"))
    counter.poll()
    files.append(line("while-closed"))

    reopened = files.counter(str(files.notifications).upper())
    reopened.poll()

    assert reopened.count.total == 2


def test_failed_save_is_retried_without_losing_the_count(files, counter):
    files.append(line("a"))
    with exclusive_lock(files.state):
        counter.poll()
    assert counter.count.total == 1

    counter.poll()
    assert files.saved()["total_ticks"] == 1


def test_locked_state_at_startup_waits_instead_of_starting_from_zero(files, counter):
    files.append(line("a"))
    counter.poll()

    reopened = files.counter()
    with exclusive_lock(files.state):
        reopened.poll()
        assert reopened.count.ready is False
    reopened.poll()

    assert reopened.count.total == 1


def test_damaged_state_is_kept_aside_and_counting_restarts(files, tmp_path):
    files.state.write_text("{quebrado", encoding="utf-8")

    counter = files.counter()
    counter.poll()

    assert counter.count.ready
    assert counter.count.total == 0
    assert len(list(tmp_path.glob("click_counter.danificado-*.json"))) == 1


# --- Zerar ------------------------------------------------------------------


def test_reset_keeps_position_and_ids(files, counter):
    files.append(line("a"))
    counter.poll()

    counter.reset()
    assert counter.count.total == 0
    assert files.saved()["total_ticks"] == 0

    files.append(line("a"))  # id já contado continua sem valer
    counter.poll()
    assert counter.count.total == 0
    files.append(line("b"))
    counter.poll()
    assert counter.count.total == 1


def test_reset_before_reading_the_state_changes_nothing(files):
    files.state.write_text(
        json.dumps({"path": str(files.notifications), "position": 0, "total_ticks": 5, "ids": []}), encoding="utf-8"
    )
    counter = files.counter()

    counter.reset()
    counter.poll()

    assert counter.count.total == 5


# --- Thread -----------------------------------------------------------------


def test_thread_counts_while_running_and_stop_reads_the_last_click(files):
    counter = ClickCounter(files.notifications, files.state, poll_seconds=0.02)
    counter.start()
    deadline = time.monotonic() + 5
    while not counter.count.ready and time.monotonic() < deadline:
        time.sleep(0.01)
    assert counter.count.ready

    files.append(line("a"))
    counter.stop()

    assert counter.count.total == 1
    assert files.saved()["total_ticks"] == 1
