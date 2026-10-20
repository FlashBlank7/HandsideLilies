from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from lilies.app import CompactPointerEventFilter, parse_args
from lilies.drag_timing import DragTimingProbe, _Distribution


class Clock:
    def __init__(self):
        self.now = 10.0
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_timing_requires_explicit_process_option_and_is_not_persistent():
    assert not parse_args([]).drag_timing
    assert parse_args(["--compact", "--drag-timing"]).drag_timing
    assert not parse_args(["--compact"]).drag_timing


def test_disabled_probe_does_not_read_clock_or_collect():
    clock = Clock()
    probe = DragTimingProbe(clock=clock)
    probe.begin()
    probe.pointer_delivered(1)
    probe.pointer_pulled(1, available=True)
    probe.submission_finished(probe.submission_started(), succeeded=True)
    probe.cancel()
    result = probe.finish()
    assert clock.calls == 0
    assert result["state"] == "disabled"
    assert result["observations"] == 0
    assert result["bridgeDeliveryToConsumption"]["meanMs"] is None
    assert result["setPositionCall"]["maxMs"] is None


def test_coalesced_latest_sample_is_not_claimed_to_measure_hardware_or_pixels():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    probe.pointer_delivered(10)
    clock.advance(0.002)
    probe.pointer_delivered(11)
    clock.advance(0.003)
    probe.pointer_pulled(11, available=True)
    clock.advance(0.007)
    probe.pointer_pulled(11, available=False)
    probe.pointer_pulled(11, available=True)  # Bad consumer must not double-count.
    result = probe.finish()
    assert result["pointerEvents"] == 2
    assert result["unconsumedOrCoalescedEvents"] == 1
    assert result["bridgeDeliveryToConsumption"]["count"] == 1
    assert result["bridgeDeliveryToConsumption"]["meanMs"] == 3.0
    assert result["bridgePullGap"]["maxMs"] == 7.0
    assert result["measuresPresentedFrames"] is False
    assert result["measuresInputQueueDelay"] is False
    assert result["setPositionCall"]["meanMs"] is None


def test_consumption_cannot_use_previous_gesture_or_unknown_sample():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    probe.pointer_delivered(10)
    probe.finish()
    probe.begin()
    probe.pointer_pulled(10, available=True)
    probe.pointer_delivered(11)
    probe.pointer_pulled(12, available=True)
    assert probe.finish()["bridgeDeliveryToConsumption"]["count"] == 0


def test_submissions_count_errors_and_measure_call_start_gaps():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    first = probe.submission_started()
    clock.advance(0.002)
    probe.submission_finished(first, succeeded=True)
    clock.advance(0.004)
    failed = probe.submission_started()
    clock.advance(0.001)
    probe.submission_finished(failed, succeeded=False)
    clock.advance(0.003)
    final = probe.submission_started()
    clock.advance(0.003)
    probe.submission_finished(final, succeeded=True)
    result = probe.finish()
    assert result["setPositionCall"]["count"] == 3
    assert result["setPositionCall"]["meanMs"] == 2.0
    assert result["setPositionCall"]["maxMs"] == 3.0
    assert result["failedSubmissions"] == 1
    assert result["submissionStarts"] == 3
    assert result["submissionsWithoutTimedReturn"] == 0
    assert result["setPositionStartGap"]["count"] == 2
    assert result["setPositionStartGap"]["meanMs"] == 5.0
    assert result["setPositionStartGap"]["maxMs"] == 6.0


@pytest.mark.parametrize("new_gesture", [False, True])
def test_nested_terminal_or_new_press_cannot_receive_late_submission(new_gesture):
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    token = probe.submission_started()
    probe.finish()
    if new_gesture:
        probe.begin()
    clock.advance(0.005)
    probe.submission_finished(token, succeeded=True)
    assert probe.finish()["setPositionCall"]["count"] == 0


def test_nested_geometry_calls_keep_start_order_not_return_order():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    outer = probe.submission_started()
    clock.advance(0.005)
    nested = probe.submission_started()
    clock.advance(0.002)
    probe.submission_finished(nested, succeeded=True)
    clock.advance(0.003)
    probe.submission_finished(outer, succeeded=True)
    last = probe.submission_started()
    probe.submission_finished(last, succeeded=True)
    report = probe.finish()
    assert report["setPositionStartGap"]["count"] == 2
    assert report["setPositionStartGap"]["meanMs"] == 5.0
    assert report["setPositionCall"]["maxMs"] == 10.0
    assert report["submissionsWithoutTimedReturn"] == 0


def test_timeout_inside_set_position_retains_an_explicit_unfinished_sample():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    token = probe.submission_started()
    clock.advance(31.0)
    probe.submission_finished(token, succeeded=True)
    report = probe.finish()
    assert report["state"] == "gesture-time-limit"
    assert report["submissionStarts"] == 1
    assert report["submissionsWithoutTimedReturn"] == 1
    assert report["setPositionCall"]["maxMs"] is None


@pytest.mark.parametrize("action", ["pointer", "pull", "submission", "finish"])
def test_gesture_timeout_bounds_every_recording_entry(action):
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    clock.advance(30.0)
    if action == "pointer":
        probe.pointer_delivered(1)
    elif action == "pull":
        probe.pointer_pulled(1, available=True)
    elif action == "submission":
        assert probe.submission_started() is None
    else:
        assert probe.finish()["state"] == "gesture-time-limit"
        return
    assert not probe.active
    probe.begin()  # Duplicate begin in a held expired gesture cannot re-arm.
    assert not probe.active
    assert probe.finish()["state"] == "gesture-time-limit"


def test_gesture_count_and_ten_minute_session_cannot_restart_themselves():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    for index in range(32):
        probe.begin()
        probe.begin()
        assert probe.active
        assert probe.finish()["gestureNumber"] == index + 1
    probe.begin()
    assert not probe.active
    assert probe.finish()["state"] == "gesture-limit"
    clock.advance(600.0)
    probe.begin()
    assert probe.finish()["state"] == "session-expired"


def test_session_expiry_during_a_gesture_is_not_a_zero_latency_sample():
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    clock.advance(590.0)
    probe.begin()
    probe.pointer_delivered(1)
    clock.advance(10.0)
    probe.pointer_pulled(1, available=True)
    report = probe.finish()
    assert report["state"] == "session-expired"
    assert report["bridgeDeliveryToConsumption"]["meanMs"] is None


def test_observation_limit_freezes_counts_and_storage_is_constant(monkeypatch):
    monkeypatch.setattr(DragTimingProbe, "MAX_OBSERVATIONS", 2000)
    clock = Clock()
    probe = DragTimingProbe(enabled=True, clock=clock)
    probe.begin()
    for index in range(5000):
        probe.pointer_delivered(index + 1)
        probe.pointer_pulled(index + 1, available=True)
    report = probe.finish()
    assert report["state"] == "observation-limit"
    assert report["observations"] == 2000
    assert report["pointerEvents"] == 1000
    assert report["bridgeDeliveryToConsumption"]["count"] == 1000
    assert len(json.dumps(report)) < 4000
    # No raw history retained, even before serialization.
    assert set(probe._delivery_to_consumption.__dict__) == {
        "count", "total", "maximum", "buckets"
    }
    assert len(probe._delivery_to_consumption.buckets) == 13


@pytest.mark.parametrize("value", [-0.1, float("nan"), float("inf")])
def test_invalid_durations_are_missing_not_reported_as_zero(value):
    distribution = _Distribution()
    distribution.add(value)
    assert distribution.snapshot()["count"] == 0
    assert distribution.snapshot()["maxMs"] is None


def test_histogram_is_disjoint_upper_inclusive_and_preserves_empty_values():
    distribution = _Distribution()
    for value in (0.0, 0.00025, 0.00025001, 0.001, 2.0):
        distribution.add(value)
    result = distribution.snapshot()
    assert sum(bucket["count"] for bucket in result["buckets"]) == 5
    assert result["buckets"][0] == {"upperInclusiveMs": 0.25, "count": 2}
    assert result["buckets"][1]["count"] == 1
    assert result["buckets"][-1] == {"upperInclusiveMs": None, "count": 1}
    assert result["maxMs"] == 2000.0


class Root(QObject):
    """No native window, cursor query, input delivery or process hooks."""

    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.positions = []
        self.fail_position = False

    def setPosition(self, point):
        self.clock.advance(0.002)
        if self.fail_position:
            raise RuntimeError("synthetic platform refusal")
        self.positions.append(QPoint(point))

    def width(self):
        return 320

    def height(self):
        return 300

    def screen(self):
        return None

    def devicePixelRatio(self):
        return 1.5


def pointer_event(kind=QEvent.Type.MouseMove):
    return QMouseEvent(
        kind, QPointF(30, 40), QPointF(450, 550),
        Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )


@pytest.fixture
def bridge():
    clock = Clock()
    root = Root(clock)
    controller = CompactPointerEventFilter(root, drag_timing_enabled=True)
    controller._drag_timing = DragTimingProbe(enabled=True, clock=clock)
    try:
        yield clock, root, controller
    finally:
        controller.close_drag_diagnostics_writer()


def press_character(root, controller):
    assert not controller.eventFilter(root, pointer_event(QEvent.Type.MouseButtonPress))
    root.setProperty("manualDragActive", True)
    controller.beginDragProxyGesture()


def test_bridge_records_only_held_samples_and_geometry_calls_without_writing(bridge):
    clock, root, controller = bridge
    assert not controller.eventFilter(root, pointer_event())  # Ordinary hover.
    press_character(root, controller)
    assert controller.eventFilter(root, pointer_event())
    clock.advance(0.004)
    latest = controller.takeLatestPointerEvent(0)
    assert latest == {"available": True, "serial": 2, "x": 450.0, "y": 550.0}
    assert controller.moveWindowForDrag(-100.0, 250.0)
    assert controller.moveWindowForDrag(-100.0, 250.0)  # Deduplication unchanged.
    assert controller._direct_move_commits == 1
    assert not controller._drag_diagnostics_timer.isActive()
    assert not controller._last_drag_diagnostics
    controller.completeGestureDiagnostics(1, True, False, False)
    report = controller.dragDiagnosticsSnapshot()["directTiming"]
    assert report["bridgeDeliveryToConsumption"]["meanMs"] == 4.0
    assert report["setPositionCall"]["meanMs"] == 2.0
    assert report["setPositionCall"]["count"] == 1
    assert report["pointerEvents"] == 1
    assert controller._diagnostics_writer is None
    text = json.dumps(report)
    for forbidden in ('"x"', '"y"', '"title"', '"text"', '"processId"', '"fps"'):
        assert forbidden not in text


def test_bridge_final_release_is_observed_but_still_reaches_qml(bridge):
    clock, root, controller = bridge
    press_character(root, controller)
    assert not controller.eventFilter(root, pointer_event(QEvent.Type.MouseButtonRelease))
    clock.advance(0.003)
    controller.takeLatestPointerEvent(0)
    controller.completeGestureDiagnostics(1, False, False, False)
    assert controller.dragDiagnosticsSnapshot()["directTiming"][
        "bridgeDeliveryToConsumption"
    ]["meanMs"] == 3.0


def test_bridge_move_failure_and_nonfinite_target_are_not_successes(bridge):
    _clock, root, controller = bridge
    press_character(root, controller)
    root.fail_position = True
    assert not controller.moveWindowForDrag(0.0, 0.0)
    assert not controller.moveWindowForDrag(float("nan"), 0.0)
    controller.completeGestureDiagnostics(1, True, False, False)
    report = controller.dragDiagnosticsSnapshot()["directTiming"]
    assert report["failedSubmissions"] == 1
    assert report["setPositionCall"]["count"] == 1
    assert report["setPositionStartGap"]["count"] == 0
    assert controller._direct_move_commits == 0


def test_platform_reentry_does_not_attribute_old_return_to_new_gesture(bridge):
    clock, root, controller = bridge
    press_character(root, controller)
    old_report = []

    def nested_set_position(_point):
        controller.completeGestureDiagnostics(1, True, False, False)
        old_report.append(controller.dragDiagnosticsSnapshot()["directTiming"])
        controller.endDragProxyGesture()
        press_character(root, controller)
        clock.advance(0.010)

    root.setPosition = nested_set_position
    assert controller.moveWindowForDrag(10.0, 20.0)
    controller.completeGestureDiagnostics(2, False, False, False)
    new_report = controller.dragDiagnosticsSnapshot()["directTiming"]
    assert old_report[0]["submissionStarts"] == 1
    assert old_report[0]["submissionsWithoutTimedReturn"] == 1
    assert new_report["gestureNumber"] == 2
    assert new_report["submissionStarts"] == 0
    assert new_report["setPositionCall"]["count"] == 0


def test_default_bridge_never_timestamps_deliveries_or_formats_distributions():
    clock = Clock()
    root = Root(clock)
    controller = CompactPointerEventFilter(root)
    controller._drag_timing._clock = lambda: pytest.fail("disabled clock called")
    controller._drag_timing.finish = lambda: pytest.fail("disabled snapshot formatted")
    try:
        press_character(root, controller)
        controller.eventFilter(root, pointer_event())
        controller.takeLatestPointerEvent(0)
        controller.moveWindowForDrag(10.0, 20.0)
        controller.completeGestureDiagnostics(1, True, False, False)
        assert controller.dragDiagnosticsSnapshot()["directTiming"] is None
    finally:
        controller.close_drag_diagnostics_writer()


def test_bridge_terminal_or_close_stops_measurement_and_next_press_is_fresh(bridge):
    _clock, root, controller = bridge
    press_character(root, controller)
    controller.eventFilter(root, pointer_event())
    controller.endDragProxyGesture()
    controller.eventFilter(root, pointer_event())
    assert not controller._drag_timing.active
    press_character(root, controller)
    assert controller._drag_timing.active
    controller.completeGestureDiagnostics(2, False, False, False)
    report = controller.dragDiagnosticsSnapshot()["directTiming"]
    assert report["gestureNumber"] == 2
    assert report["pointerEvents"] == 0
    press_character(root, controller)
    controller.close_drag_diagnostics_writer()
    assert not controller._drag_timing.active
    controller.beginDragProxyGesture()
    assert not controller._drag_timing.active


def test_native_move_stream_is_not_relabelled_as_direct_latency(bridge):
    _clock, root, controller = bridge
    press_character(root, controller)
    controller._active_system_move_serial = 1
    assert controller.eventFilter(root, pointer_event())
    controller.completeGestureDiagnostics(1, True, True, True)
    report = controller.dragDiagnosticsSnapshot()
    assert report["mode"] == "native"
    assert report["directTiming"]["pointerEvents"] == 0
    assert report["directTiming"]["setPositionCall"]["meanMs"] is None


def test_finished_metrics_use_existing_deferred_writer_only(tmp_path):
    clock = Clock()
    root = Root(clock)
    path = tmp_path / "runtime" / "pet-drag-latest.json"
    controller = CompactPointerEventFilter(root, diagnostics_path=path, drag_timing_enabled=True)
    controller._drag_timing = DragTimingProbe(enabled=True, clock=clock)
    try:
        press_character(root, controller)
        controller.eventFilter(root, pointer_event())
        controller.takeLatestPointerEvent(0)
        controller.moveWindowForDrag(100.0, 200.0)
        assert not path.exists()
        controller.completeGestureDiagnostics(1, True, False, False)
        assert not path.exists()
        assert controller._drag_diagnostics_timer.isActive()
        assert controller.wait_for_drag_diagnostics_write(1.0)
        result = json.loads(path.read_text("utf-8"))
        assert result["directTiming"]["setPositionCall"]["count"] == 1
        assert result["schemaVersion"] == 4  # Additive, independently versioned block.
    finally:
        controller.close_drag_diagnostics_writer()
