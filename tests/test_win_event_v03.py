from __future__ import annotations

from lilies.core import win_event
from lilies.core.win_event import (
    EVENT_OBJECT_LOCATIONCHANGE,
    EVENT_SYSTEM_FOREGROUND,
    WinEventHub,
    WinEventKind,
)


def test_native_callback_payload_is_queued_until_owner_dispatches() -> None:
    hub = WinEventHub()
    received = []
    hub.subscribe(received.append)

    assert hub.publish_native(EVENT_SYSTEM_FOREGROUND, 42) is True
    assert received == []
    assert hub.pending_count == 1

    assert hub.dispatch_pending() == 1
    assert received[0].kind is WinEventKind.FOREGROUND
    assert received[0].hwnd == 42


def test_location_events_coalesce_and_non_window_children_are_ignored() -> None:
    hub = WinEventHub()

    assert hub.publish_native(EVENT_OBJECT_LOCATIONCHANGE, 7, native_time_ms=1)
    assert hub.publish_native(EVENT_OBJECT_LOCATIONCHANGE, 7, native_time_ms=2)
    assert not hub.publish_native(
        EVENT_OBJECT_LOCATIONCHANGE, 7, object_id=-4, child_id=0
    )

    events = hub.drain()
    assert len(events) == 1
    assert events[0].native_time_ms == 2


def test_one_bad_subscriber_does_not_starve_other_services() -> None:
    hub = WinEventHub()
    received = []

    def broken(_event) -> None:
        raise RuntimeError("Dock model was deleted")

    hub.subscribe(broken)
    hub.subscribe(received.append)
    hub.publish_native(EVENT_SYSTEM_FOREGROUND, 5)

    assert hub.dispatch_pending() == 1
    assert [event.hwnd for event in received] == [5]


def test_start_is_a_noop_when_native_hooks_are_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(win_event.os, "name", "posix")
    hub = WinEventHub()

    assert hub.available is False
    assert hub.start() is False
    assert hub.running is False

