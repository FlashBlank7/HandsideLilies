from __future__ import annotations

from lilies.core.focus_diversion import (
    FocusDiversionMonitor,
    is_entertainment_process,
)


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def running_monitor(clock: Clock) -> FocusDiversionMonitor:
    monitor = FocusDiversionMonitor(clock=clock)
    monitor.set_focus("focus-1", "running")
    return monitor


def test_requires_45_seconds_of_one_stable_entertainment_context() -> None:
    clock = Clock()
    monitor = running_monitor(clock)
    monitor.update_foreground("game:1", "game.exe", entertainment=True)

    clock.value = 44.9
    assert monitor.tick() is None
    clock.value = 45.0
    reminder = monitor.tick()

    assert reminder is not None
    assert reminder.delivery == "bubble"
    assert reminder.actions == ("return", "rest", "finish")
    clock.value = 700.0
    assert monitor.tick() is None  # one uninterrupted visit never nags twice


def test_new_visit_obeys_ten_minute_cooldown() -> None:
    clock = Clock()
    monitor = running_monitor(clock)
    monitor.update_foreground("game:1", "game.exe", entertainment=True)
    clock.value = 45.0
    assert monitor.tick() is not None

    clock.value = 50.0
    monitor.update_foreground("work:1", "wps.exe", entertainment=False)
    monitor.update_foreground("game:2", "game.exe", entertainment=True)
    clock.value = 100.0
    assert monitor.tick() is None

    clock.value = 650.0
    assert monitor.tick() is not None


def test_deferred_sensitive_delivery_waits_and_fullscreen_uses_notification() -> None:
    clock = Clock()
    monitor = running_monitor(clock)
    monitor.update_foreground(
        "game:1",
        "game.exe",
        entertainment=True,
        full_screen_game=True,
        defer_reminder=True,
    )
    clock.value = 60.0
    assert monitor.tick() is None

    monitor.update_foreground(
        "game:1",
        "game.exe",
        entertainment=True,
        full_screen_game=True,
        defer_reminder=False,
    )
    reminder = monitor.tick()
    assert reminder is not None
    assert reminder.delivery == "windows-notification"


def test_pausing_focus_resets_stability_window() -> None:
    clock = Clock()
    monitor = running_monitor(clock)
    monitor.update_foreground("game:1", "game.exe", entertainment=True)
    clock.value = 30.0
    monitor.set_focus("focus-1", "paused")
    clock.value = 100.0
    monitor.set_focus("focus-1", "running")
    clock.value = 144.0
    assert monitor.tick() is None
    clock.value = 145.0
    assert monitor.tick() is not None


def test_entertainment_classifier_is_conservative_and_title_free() -> None:
    assert is_entertainment_process(r"C:\Games\Steam.exe") is True
    assert is_entertainment_process("unknown.exe", is_game=True) is True
    assert is_entertainment_process("wps.exe") is False
