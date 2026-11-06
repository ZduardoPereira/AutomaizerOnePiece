import sys
import threading
import time
import tkinter
from pathlib import Path

import numpy as np
import pytest

from core.capture import CaptureService, CaptureState, WindowFinder, WindowInfo, is_black_frame, is_fortnite_window
from core.config import CaptureConfig
from core.event_manager import EventManager, EventType

PROCESSES = ["FortniteClient-Win64-Shipping.exe"]
TITLES = ["Fortnite"]


def make_window(**changes):
    values = {
        "hwnd": 1,
        "title": "Fortnite",
        "process_name": PROCESSES[0],
        "left": 10,
        "top": 20,
        "width": 64,
        "height": 48,
        "is_minimized": False,
        "is_foreground": True,
    }
    return WindowInfo(**{**values, **changes})


class FakeFinder:
    def __init__(self, window=None):
        self.window = window

    def find(self):
        return self.window

    def describe(self, _hwnd):
        return self.window


class FakeGrabber:
    def __init__(self, value=128):
        self.value = value
        self.regions = []
        self.error = None
        self.closed = False

    def grab(self, left, top, width, height):
        if self.error is not None:
            raise self.error
        self.regions.append((left, top, width, height))
        return np.full((height, width, 3), self.value, dtype=np.uint8)

    def close(self):
        self.closed = True


@pytest.fixture
def harness():
    finder = FakeFinder()
    grabber = FakeGrabber()
    events = EventManager()
    config = CaptureConfig(fps=30, window_poll_seconds=0.5)
    service = CaptureService(config, events, finder_factory=lambda _config: finder, grabber_factory=lambda: grabber)
    yield service, finder, grabber, events
    service.stop()


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.01)
    pytest.fail("condição não atendida a tempo")


@pytest.mark.parametrize(
    ("process", "title", "expected"),
    [
        (PROCESSES[0], "qualquer título", True),
        (PROCESSES[0].upper(), "", True),
        ("explorer.exe", "Fortnite", False),
        ("chrome.exe", "Fortnite - YouTube", False),
        (None, "fortnite", True),
        (None, "Fortnite - YouTube", False),
    ],
)
def test_is_fortnite_window(process, title, expected):
    assert is_fortnite_window(process, title, PROCESSES, TITLES) is expected


def test_is_black_frame():
    assert is_black_frame(np.zeros((64, 64, 3), dtype=np.uint8), 4.0)
    assert not is_black_frame(np.full((64, 64, 3), 200, dtype=np.uint8), 4.0)


def test_window_finder_does_not_match_nonexistent_process():
    assert WindowFinder(["processo-que-nao-existe.exe"], ["titulo-que-nao-existe"]).find() is None


def test_captures_client_area_of_found_window(harness):
    service, finder, grabber, _events = harness
    finder.window = make_window()
    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)

    snapshot = service.snapshot()
    assert grabber.regions[0] == (10, 20, 64, 48)
    assert snapshot.frame.shape == (48, 64, 3)
    assert not snapshot.frame.flags.writeable
    assert snapshot.frame_id >= 1
    assert not snapshot.is_black


def test_searches_until_window_appears_and_reports_loss(harness):
    service, finder, _grabber, events = harness
    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.SEARCHING)
    assert service.snapshot().frame is None

    finder.window = make_window()
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)

    finder.window = None
    wait_for(lambda: service.snapshot().state is CaptureState.SEARCHING)
    assert service.snapshot().frame is None

    types = [event.type for event in events.recent(50)]
    assert EventType.FORTNITE_DETECTED in types
    assert EventType.FORTNITE_LOST in types


@pytest.mark.parametrize(
    ("window", "state"),
    [
        (make_window(is_foreground=False), CaptureState.BACKGROUND),
        (make_window(is_minimized=True), CaptureState.MINIMIZED),
        (make_window(width=0), CaptureState.MINIMIZED),
    ],
)
def test_does_not_grab_when_game_is_not_visible(harness, window, state):
    service, finder, grabber, _events = harness
    finder.window = window
    service.start()
    wait_for(lambda: service.snapshot().state is state)
    assert grabber.regions == []
    assert service.snapshot().frame is None


def test_background_capture_can_be_enabled_at_runtime(harness):
    service, finder, _grabber, _events = harness
    finder.window = make_window(is_foreground=False)
    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.BACKGROUND)

    service.update_config(CaptureConfig(capture_only_when_foreground=False))
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)


def test_recovers_from_grab_error_and_reports_it_once(harness):
    service, finder, grabber, events = harness
    finder.window = make_window()
    grabber.error = OSError("falha de captura")
    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.ERROR)
    time.sleep(0.6)
    assert service.snapshot().error == "falha de captura"
    assert len(events.recent(50, {EventType.CAPTURE_ERROR})) == 1

    grabber.error = None
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)
    assert service.snapshot().error is None


def test_stop_releases_resources_and_can_restart(harness):
    service, finder, grabber, events = harness
    finder.window = make_window()
    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)

    service.stop()
    assert not service.is_running
    assert grabber.closed
    assert service.snapshot().state is CaptureState.STOPPED
    assert service.snapshot().frame is None
    assert not events.recent(50, {EventType.FORTNITE_LOST})

    service.start()
    wait_for(lambda: service.snapshot().state is CaptureState.CAPTURING)


def test_grabber_creation_failure_is_reported_without_crashing():
    def broken_factory():
        raise OSError("sem tela")

    events = EventManager()
    service = CaptureService(CaptureConfig(), events, grabber_factory=broken_factory)
    service.start()
    wait_for(lambda: not service.is_running)

    assert service.snapshot().state is CaptureState.STOPPED
    assert len(events.recent(10, {EventType.CAPTURE_ERROR})) == 1


def test_window_lookup_does_not_depend_on_ui_thread_message_loop():
    # A interface fica sem processar mensagens enquanto aguarda a thread de captura em stop().
    root = tkinter.Tk()
    root.title("janela-do-proprio-processo")
    root.update()
    results = []
    finder = WindowFinder([Path(sys.executable).name], ["janela-do-proprio-processo"])
    worker = threading.Thread(target=lambda: results.append(finder.find()))
    try:
        worker.start()
        worker.join(2.0)
        assert not worker.is_alive()
        assert results[0] is not None
    finally:
        root.destroy()
        worker.join()
