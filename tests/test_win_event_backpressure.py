"""Interleaved geometry must not fill the GUI queue or evict lifecycle edges."""
import threading
import random

import pytest

from lilies.core.win_event import WinEvent, WinEventHub, WinEventKind


def event(kind, hwnd, tick=0):
    return WinEvent(kind, hwnd, 0, native_time_ms=tick)


def test_interleaved_window_motion_retains_only_latest_geometry_per_window():
    hub = WinEventHub()
    hub.publish(event(WinEventKind.FOREGROUND, 3))
    for tick in range(10_000):
        hub.publish(event(WinEventKind.LOCATION_CHANGE, 10 + tick % 2, tick))
    received = []
    hub.subscribe(received.append)
    assert hub.pending_count == 3
    assert hub.dispatch_pending() == 3
    assert [(e.kind, e.hwnd, e.native_time_ms) for e in received] == [
        (WinEventKind.FOREGROUND, 3, 0),
        (WinEventKind.LOCATION_CHANGE, 10, 9998),
        (WinEventKind.LOCATION_CHANGE, 11, 9999),
    ]


def test_unique_motion_pressure_does_not_discard_foreground_or_host_destroy():
    hub = WinEventHub(queue_limit=32)
    foreground = event(WinEventKind.FOREGROUND, 3)
    destroy = event(WinEventKind.DESTROY, 8)
    hub.publish(foreground)
    hub.publish(destroy)
    for tick in range(200):
        hub.publish(event(WinEventKind.LOCATION_CHANGE, 1000 + tick, tick))
    assert hub.pending_count == 32
    events = hub.drain()
    assert events[:2] == [foreground, destroy]
    assert [e.native_time_ms for e in events[2:]] == list(range(170, 200))


@pytest.mark.parametrize("barrier", [kind for kind in WinEventKind if kind is not WinEventKind.LOCATION_CHANGE])
def test_geometry_never_crosses_lifecycle_or_foreground_boundary(barrier):
    hub = WinEventHub()
    before = event(WinEventKind.LOCATION_CHANGE, 10, 1)
    edge = event(barrier, 10, 2)
    after = event(WinEventKind.LOCATION_CHANGE, 10, 3)
    for item in (before, edge, after):
        hub.publish(item)
    assert hub.drain() == [before, edge, after]


def test_geometry_replacement_uses_latest_order_not_first_seen_order():
    hub = WinEventHub()
    for hwnd, tick in ((10, 1), (11, 2), (10, 3), (12, 4), (11, 5)):
        hub.publish(event(WinEventKind.LOCATION_CHANGE, hwnd, tick))
    assert [(e.hwnd, e.native_time_ms) for e in hub.drain()] == [(10, 3), (12, 4), (11, 5)]


def test_reused_handle_with_other_native_thread_is_not_coalesced():
    hub = WinEventHub()
    first = WinEvent(WinEventKind.LOCATION_CHANGE, 10, 0, thread_id=1)
    second = WinEvent(WinEventKind.LOCATION_CHANGE, 10, 0, thread_id=2)
    hub.publish(first)
    hub.publish(second)
    assert hub.drain() == [first, second]


def test_partial_drain_removes_indexes_without_erasing_a_later_segment():
    hub = WinEventHub()
    before = event(WinEventKind.LOCATION_CHANGE, 10, 1)
    edge = event(WinEventKind.FOREGROUND, 11, 2)
    after = event(WinEventKind.LOCATION_CHANGE, 10, 3)
    for item in (before, edge, after):
        hub.publish(item)
    assert hub.drain(1) == [before]
    newest = event(WinEventKind.LOCATION_CHANGE, 10, 4)
    hub.publish(newest)
    assert hub.drain() == [edge, newest]
    assert not hub._location_keys and not hub._queued_locations
    hub.publish(after)
    assert hub.drain() == [after]


def test_all_critical_overflow_stays_bounded_and_geometry_cannot_evict_it():
    hub = WinEventHub(queue_limit=32)
    edges = [event(WinEventKind.FOREGROUND, tick + 1, tick) for tick in range(40)]
    for item in edges:
        hub.publish(item)
    for tick in range(100):
        hub.publish(event(WinEventKind.LOCATION_CHANGE, 100 + tick, tick))
    assert hub.pending_count == 32
    assert hub.drain() == edges[-32:]
    assert not hub._location_keys and not hub._queued_locations


def test_concurrent_producers_keep_latest_each_without_inline_callbacks():
    hub = WinEventHub(queue_limit=32)
    callback_threads = []
    received = []
    gate = threading.Barrier(3)

    def listener(item):
        callback_threads.append(threading.get_ident())
        received.append(item)

    def producer(hwnd):
        gate.wait(timeout=3)
        for tick in range(2000):
            hub.publish(event(WinEventKind.LOCATION_CHANGE, hwnd, tick))

    hub.subscribe(listener)
    workers = [threading.Thread(target=producer, args=(handle,)) for handle in (10, 11)]
    for worker in workers:
        worker.start()
    gate.wait(timeout=3)
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert received == []
    assert hub.dispatch_pending() == 2
    assert sorted((e.hwnd, e.native_time_ms) for e in received) == [(10, 1999), (11, 1999)]
    assert callback_threads == [threading.get_ident()] * 2


def test_listener_can_publish_next_batch_without_deadlock_or_event_loss():
    hub = WinEventHub()
    first = event(WinEventKind.FOREGROUND, 10)
    second = event(WinEventKind.LOCATION_CHANGE, 10, 1)
    received = []

    def listener(item):
        received.append(item)
        if item is first:
            hub.publish(second)

    hub.subscribe(listener)
    hub.publish(first)
    assert hub.dispatch_pending() == 1
    assert received == [first]
    assert hub.dispatch_pending() == 1
    assert received == [first, second]


def test_geometry_storm_still_delivers_privacy_transition_to_real_backend(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from lilies.backend import Backend
    from lilies.core.activity import ForegroundContext
    from lilies.core.orchestration import PresenceState

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    read_on = []
    try:
        backend._v03_timer.stop()

        def read(hwnd):
            read_on.append(threading.get_ident())
            return ForegroundContext(hwnd, process_id=123, is_protected=True)

        monkeypatch.setattr(backend.companion, "reader", read)
        hub = backend.win_event_hub
        hub.publish(event(WinEventKind.FOREGROUND, 6001))
        for index in range(3000):
            hub.publish(event(WinEventKind.LOCATION_CHANGE, 10_000 + index, index))
        assert hub.pending_count == 2048
        assert hub.dispatch_pending(limit=4096) == 2048
        assert read_on == [threading.get_ident()]
        assert backend._presence_context_handle == 6001
        assert backend.presence.snapshot().state is PresenceState.BLOCKED
        assert backend.selection.capture_policy(6001) is False
    finally:
        backend.shutdown()
        QCoreApplication.instance().processEvents()


def test_bounded_indexes_match_simple_reference_across_mixed_partial_drains():
    hub = WinEventHub(queue_limit=32)
    reference = []
    rng = random.Random(20261005)
    kinds = list(WinEventKind)
    for tick in range(10_000):
        if rng.randrange(9) == 0:
            limit = rng.randrange(12)
            assert hub.drain(limit) == reference[:limit]
            del reference[:limit]
        else:
            kind = WinEventKind.LOCATION_CHANGE if rng.randrange(5) else rng.choice(kinds)
            new = WinEvent(kind, rng.randrange(1, 8), 0, thread_id=rng.randrange(1, 3), native_time_ms=tick)
            if kind is WinEventKind.LOCATION_CHANGE:
                for index in range(len(reference) - 1, -1, -1):
                    old = reference[index]
                    if old.kind is not WinEventKind.LOCATION_CHANGE:
                        break
                    if (old.hwnd, old.thread_id) == (new.hwnd, new.thread_id):
                        reference.pop(index)
                        break
            reference.append(new)
            if len(reference) > 32:
                remove = next((i for i, e in enumerate(reference) if e.kind is WinEventKind.LOCATION_CHANGE), 0)
                reference.pop(remove)
            hub.publish(new)
        assert hub.pending_count == len(reference)
        assert len(hub._location_keys) <= len(hub._queued_locations) <= hub.pending_count <= 32
    assert hub.drain() == reference
    assert not hub._location_keys and not hub._queued_locations
