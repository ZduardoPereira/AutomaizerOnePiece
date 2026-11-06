import threading

from core.event_manager import EventManager, EventType


def test_subscribers_only_run_on_dispatch():
    events = EventManager()
    received = []
    events.subscribe(received.append)

    events.publish(EventType.CONFIG_UPDATED, "a")
    assert received == []

    assert events.dispatch_pending() == 1
    assert [event.message for event in received] == ["a"]
    assert events.dispatch_pending() == 0


def test_failing_subscriber_does_not_block_others():
    events = EventManager()
    received = []

    def fail(_event):
        raise RuntimeError("falha")

    events.subscribe(fail)
    events.subscribe(received.append)
    events.publish(EventType.CONFIG_UPDATED, "a")

    assert events.dispatch_pending() == 1
    assert len(received) == 1


def test_recent_is_newest_first_filtered_and_bounded():
    events = EventManager(history_size=3)
    for index in range(5):
        events.publish(EventType.CONFIG_UPDATED, str(index))
    events.publish(EventType.FORTNITE_LOST, "perdido")

    assert [event.message for event in events.recent()] == ["perdido", "4", "3"]
    assert [event.message for event in events.recent(limit=1)] == ["perdido"]
    assert [event.message for event in events.recent(types={EventType.CONFIG_UPDATED})] == ["4", "3"]


def test_publish_from_other_threads_delivers_everything():
    events = EventManager()
    received = []
    events.subscribe(received.append)
    threads = [
        threading.Thread(target=lambda: [events.publish(EventType.CONFIG_UPDATED, "x") for _ in range(100)])
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events.dispatch_pending()
    assert len(received) == 500


def test_sink_runs_immediately_on_the_publishing_thread():
    events = EventManager()
    received = []
    events.add_sink(received.append)

    events.publish(EventType.CONFIG_UPDATED, "a", 0.9)

    assert [(event.message, event.confidence) for event in received] == [("a", 0.9)]
    assert events.dispatch_pending() == 1


def test_failing_sink_does_not_stop_the_event():
    events = EventManager()
    received = []

    def fail(_event):
        raise RuntimeError("falha")

    events.add_sink(fail)
    events.add_sink(received.append)
    events.subscribe(received.append)

    events.publish(EventType.CONFIG_UPDATED, "a")
    events.dispatch_pending()

    assert len(received) == 2
    assert events.recent()[0].message == "a"
