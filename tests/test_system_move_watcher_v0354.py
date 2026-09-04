from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Qt

from lilies.app import CompactPointerEventFilter, _SystemMoveWatcher
from lilies.windows_drag_proxy import WindowRect


class _MoveRoot(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.completions: list[int] = []

    def startSystemMove(self) -> bool:
        return True

    def finishNativeSystemMove(self, serial: int) -> bool:
        self.completions.append(serial)
        return True

    def devicePixelRatio(self) -> float:
        return 1.5

    def width(self) -> int:
        return 420

    def height(self) -> int:
        return 396

    def screen(self):
        return None


def _watched_controller(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    root = _MoveRoot()
    controller = CompactPointerEventFilter(root)
    # Zero starts no real hook thread and never creates a desktop window.
    watcher = _SystemMoveWatcher(0)
    tick = {"value": 100}
    monkeypatch.setattr(watcher, "_tick_count32", lambda: tick["value"])
    controller._native_drag_filter = SimpleNamespace(native_window_id=4321)
    controller._system_move_end_watcher = watcher
    monkeypatch.setattr(
        controller, "_native_window_rect", lambda _hwnd: WindowRect(0, 0, 420, 396)
    )
    monkeypatch.setattr(controller, "_left_button_is_down", lambda: True)
    watcher.moveStartedTagged.connect(
        controller._on_system_move_started, Qt.ConnectionType.QueuedConnection
    )
    watcher.moveEndedTagged.connect(
        controller._on_system_move_ended, Qt.ConnectionType.QueuedConnection
    )
    return app, root, controller, watcher, tick


def _deliver_pair(watcher: _SystemMoveWatcher, start: int, end: int) -> None:
    watcher._observe_native_event(watcher._EVENT_SYSTEM_MOVESIZESTART, 4321, start)
    watcher._observe_native_event(watcher._EVENT_SYSTEM_MOVESIZEEND, 4321, end)


def _drain(app: QCoreApplication) -> None:
    # Tagged hook signals and the completion singleShot are separate turns.
    app.processEvents()
    app.processEvents()


def test_old_delayed_start_and_end_cannot_finish_new_held_root_drag(monkeypatch):
    app, root, controller, watcher, tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(1)
    controller.acknowledgeSystemMoveFinished(1)
    tick["value"] = 200
    assert controller.tryStartSystemMove(2)
    second_session = controller._active_system_move_session_id

    # These events were generated for N, but delivered after N+1 owns the
    # unchanged HWND. Their generation must not be taken from callback time.
    _deliver_pair(watcher, 110, 120)
    _drain(app)
    controller._poll_system_move_release()
    _drain(app)
    assert controller._active_system_move_session_id == second_session
    assert not controller._system_move_entered
    assert root.completions == []

    _deliver_pair(watcher, 210, 220)
    _drain(app)
    assert root.completions == [2]
    assert not controller.native_system_move_active
    controller.close_drag_diagnostics_writer()


@pytest.mark.parametrize(
    ("armed", "event", "accepted"),
    [
        (100, 99, False),
        (100, 100, False),
        (100, 101, True),
        (0xFFFFFFF0, 0xFFFFFFEF, False),
        (0xFFFFFFF0, 0, True),
        (0xFFFFFFF0, 0x10, True),
        (0x10, 0xFFFFFFF0, False),
        (0, 0x80000000, False),
        (None, 100, False),
    ],
)
def test_event_tick_fence_is_wrap_safe_and_rejects_ambiguity(armed, event, accepted):
    assert _SystemMoveWatcher._event_is_after_arm(event, armed) is accepted


def test_same_tick_callbacks_leave_stationary_click_to_release_watchdog(monkeypatch):
    app, root, controller, watcher, _tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(10)
    _deliver_pair(watcher, 100, 100)
    _drain(app)
    assert not controller._system_move_entered
    assert controller.native_system_move_active
    assert root.completions == []

    monkeypatch.setattr(controller, "_left_button_is_down", lambda: False)
    controller._poll_system_move_release()
    _drain(app)
    assert root.completions == [10]
    assert not controller.systemMoveHadMotion(10)
    controller.close_drag_diagnostics_writer()


@pytest.mark.parametrize("swapped", [False, True])
def test_later_valid_end_finishes_ambiguous_start_without_watchdog(monkeypatch, swapped):
    app, root, controller, watcher, _tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(11)
    queried_keys: list[int] = []
    primary_vk = controller._VK_RBUTTON if swapped else controller._VK_LBUTTON

    def button_state(key):
        queried_keys.append(key)
        # The secondary button is held; only the logical primary is released.
        return 0 if key == primary_vk else 0x8000

    monkeypatch.setattr(
        controller, "_win32_user32",
        lambda: SimpleNamespace(GetSystemMetrics=lambda _metric: int(swapped),
                                GetAsyncKeyState=button_state),
    )
    monkeypatch.setattr(
        controller, "_left_button_is_down",
        lambda: CompactPointerEventFilter._left_button_is_down(controller),
    )
    _deliver_pair(watcher, 100, 120)
    _drain(app)
    assert not controller._system_move_entered  # Do not invent a START receipt.
    assert root.completions == [11]
    assert queried_keys == [primary_vk]
    assert controller._completion_watchdog_polls == 0
    assert controller._completion_queued_by == "win-event-move-end"
    controller.close_drag_diagnostics_writer()


def test_ambiguous_start_with_valid_end_never_finishes_held_primary(monkeypatch):
    app, root, controller, watcher, _tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(12)
    _deliver_pair(watcher, 100, 120)
    _drain(app)
    assert not controller._system_move_entered
    assert controller.native_system_move_active
    assert root.completions == []
    monkeypatch.setattr(controller, "_left_button_is_down", lambda: False)
    watcher._observe_native_event(watcher._EVENT_SYSTEM_MOVESIZEEND, 4321, 130)
    _drain(app)
    assert root.completions == [12]
    assert controller._completion_watchdog_polls == 0
    controller.close_drag_diagnostics_writer()


def test_ambiguous_start_end_requires_tag_and_readable_button_state(monkeypatch):
    app, root, controller, watcher, _tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(13)
    monkeypatch.setattr(controller, "_left_button_is_down", lambda: False)
    controller._on_system_move_ended(4321, 0)
    _drain(app)
    assert root.completions == []

    def unavailable():
        raise OSError("button state unavailable")

    monkeypatch.setattr(controller, "_left_button_is_down", unavailable)
    watcher._observe_native_event(watcher._EVENT_SYSTEM_MOVESIZEEND, 4321, 120)
    _drain(app)
    assert root.completions == []
    assert controller.native_system_move_active
    controller.acknowledgeSystemMoveFinished(13)
    controller.close_drag_diagnostics_writer()


def test_queued_accepted_events_do_not_cross_cancel_and_rearm(monkeypatch):
    app, root, controller, watcher, tick = _watched_controller(monkeypatch)
    assert controller.tryStartSystemMove(20)
    # Accepted on the hook thread before cancellation, delivered to Qt later.
    _deliver_pair(watcher, 110, 120)
    controller.acknowledgeSystemMoveFinished(20)
    tick["value"] = 200
    assert controller.tryStartSystemMove(21)
    _drain(app)
    assert controller._active_system_move_serial == 21
    assert root.completions == []
    controller.acknowledgeSystemMoveFinished(21)
    _deliver_pair(watcher, 210, 220)
    _drain(app)
    assert root.completions == []
    controller.close_drag_diagnostics_writer()


def test_valid_move_events_survive_tick_counter_wrap(monkeypatch):
    app, root, controller, watcher, tick = _watched_controller(monkeypatch)
    tick["value"] = 0xFFFFFFF0
    assert controller.tryStartSystemMove(30)
    _deliver_pair(watcher, 0xFFFFFFE0, 0xFFFFFFE8)
    _drain(app)
    assert root.completions == []
    _deliver_pair(watcher, 0x00000000, 0x00000010)
    _drain(app)
    assert root.completions == [30]
    controller.close_drag_diagnostics_writer()


def test_missing_windows_clock_uses_watchdog_without_tagging_events(monkeypatch):
    app, root, controller, watcher, tick = _watched_controller(monkeypatch)
    tick["value"] = None
    assert controller.tryStartSystemMove(40)
    _deliver_pair(watcher, 110, 120)
    _drain(app)
    assert root.completions == []
    assert not controller._system_move_entered
    monkeypatch.setattr(controller, "_left_button_is_down", lambda: False)
    controller._poll_system_move_release()
    _drain(app)
    assert root.completions == [40]
    controller.close_drag_diagnostics_writer()


def test_generation_zero_keeps_probe_counts_but_never_emits_lifecycle(monkeypatch):
    watcher = _SystemMoveWatcher(0)
    monkeypatch.setattr(watcher, "_tick_count32", lambda: 100)
    tagged: list[tuple[int, int]] = []
    watcher.moveStartedTagged.connect(lambda hwnd, serial: tagged.append((hwnd, serial)))
    watcher.moveEndedTagged.connect(lambda hwnd, serial: tagged.append((hwnd, serial)))
    watcher.set_window_id(4321)
    _deliver_pair(watcher, 110, 120)
    assert watcher.event_counts == (1, 1)
    assert tagged == []
    watcher.close()
    _deliver_pair(watcher, 130, 140)
    assert watcher.event_counts == (1, 1)
