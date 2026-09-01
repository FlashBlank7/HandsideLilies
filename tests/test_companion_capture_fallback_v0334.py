from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication

import lilies.companion_controller as companion_controller_module
from lilies.companion_controller import CompanionController
from lilies.core.activity import (
    ForegroundContext,
    ProtectedCaptureContent,
    StagedCapture,
)
from lilies.core.database import Database
from lilies.core.native_capture_helper import NativeCaptureHelperError


def _wait_for(app: QCoreApplication, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


class _Runtime:
    modality_status = {"checked": True, "imageModel": "luna", "error": ""}

    def __init__(self, *_args, **_kwargs) -> None:
        self.modality_status = dict(type(self).modality_status)
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs):
        self.calls.append(dict(kwargs))
        assert kwargs["image_path"] is not None
        assert Path(kwargs["image_path"]).is_file()
        return {
            "summary": "Synthetic image-grounded result.",
            "detail": "Synthetic detail.",
            "model": "fallback-image-test",
            "contextType": "active-window-image",
            "imageGrounded": True,
        }

    def abort_model(self, _model_id: str) -> None:
        pass

    def shutdown(self) -> None:
        pass


def _controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context: ForegroundContext,
) -> CompanionController:
    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", _Runtime)
    database = Database(tmp_path / "lilies.db")
    database.set_setting("smart_observation_authorized", True)
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: context.hwnd,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    return controller


def _fail_primary_grab(_hwnd: int):
    raise OSError("synthetic primary capture failure")


def test_primary_grab_failure_uses_helper_image_runtime_and_has_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Synthetic document",
    )
    controller = _controller(tmp_path, monkeypatch, context)
    helper_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", _fail_primary_grab
    )
    monkeypatch.setattr(
        companion_controller_module, "native_capture_helper_available", lambda: True
    )

    def helper(staging, hwnd, process_id, *, cancelled):
        assert not cancelled()
        helper_calls.append((int(hwnd), int(process_id)))
        path = staging.root / ("capture-" + "b" * 32 + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic bounded PNG")
        return StagedCapture(path, staging.library_root)

    monkeypatch.setattr(
        companion_controller_module, "stage_window_capture_with_helper", helper
    )
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: bool(controller.bubble) and not controller.busy)
        assert helper_calls == [(42, 8)]
        assert len(controller.runtime.calls) == 1
        assert controller.runtime.calls[0]["image_path"] is not None
        assert controller.bubble["hasCapture"] is True
        assert controller.bubble["contextType"] == "active-window-image"
        assert controller.activityStatus["lastCaptureOutcome"] == "used"
    finally:
        controller.shutdown()


def test_helper_failure_stays_quiet_without_model_and_uses_fixed_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(
        43,
        process_id=9,
        process_name="wps.exe",
        window_class="WPS",
        title="Synthetic document",
    )
    controller = _controller(tmp_path, monkeypatch, context)
    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", _fail_primary_grab
    )
    monkeypatch.setattr(
        companion_controller_module, "native_capture_helper_available", lambda: True
    )

    def fail_helper(*_args, **_kwargs):
        raise NativeCaptureHelperError(r"private helper detail C:\secret\capture.png")

    monkeypatch.setattr(
        companion_controller_module, "stage_window_capture_with_helper", fail_helper
    )
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert controller.runtime.calls == []
        assert controller.bubble == {}
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "failed"
        assert status["lastCaptureReason"] == "native-print-failed"
        assert status["imageSubmitted"] is False
        persisted = repr(
            controller.database.get_setting("companion_last_capture_status", {})
        )
        assert "secret" not in persisted
        assert "capture.png" not in persisted
    finally:
        controller.shutdown()


def test_protected_black_frame_never_tries_alternate_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(
        45,
        process_id=12,
        process_name="wps.exe",
        window_class="WPS",
        title="Synthetic protected document",
    )
    controller = _controller(tmp_path, monkeypatch, context)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (64, 48), "black"),
    )
    monkeypatch.setattr(
        companion_controller_module, "native_capture_helper_available", lambda: True
    )

    def protected(*_args, **_kwargs):
        raise ProtectedCaptureContent("synthetic protected frame")

    def helper_must_not_run(*_args, **_kwargs):
        raise AssertionError("protected content must not try an alternate capture")

    monkeypatch.setattr(controller.capture_staging, "stage_image", protected)
    monkeypatch.setattr(
        companion_controller_module,
        "stage_window_capture_with_helper",
        helper_must_not_run,
    )
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert controller.runtime.calls == []
        assert controller.bubble == {}
        assert controller.activityStatus["lastCaptureReason"] == "protected-black"
    finally:
        controller.shutdown()


def test_foreground_identity_change_prevents_helper_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    initial = ForegroundContext(
        44,
        process_id=10,
        process_name="wps.exe",
        window_class="WPS",
        title="Synthetic document",
    )
    changed = ForegroundContext(
        44,
        process_id=11,
        process_name="other.exe",
        window_class="Other",
        title="Replacement window",
    )
    controller = _controller(tmp_path, monkeypatch, initial)
    reads = iter((initial, changed))
    controller.reader = lambda _hwnd: next(reads)
    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", _fail_primary_grab
    )
    monkeypatch.setattr(
        companion_controller_module, "native_capture_helper_available", lambda: True
    )

    def helper_must_not_run(*_args, **_kwargs):
        raise AssertionError("changed foreground identity must prevent helper launch")

    monkeypatch.setattr(
        companion_controller_module,
        "stage_window_capture_with_helper",
        helper_must_not_run,
    )
    try:
        assert controller._start_generation(initial, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert controller.runtime.calls == []
        assert controller.bubble == {}
        assert controller.activityStatus["lastCaptureOutcome"] == "discarded"
        assert controller.activityStatus["lastCaptureReason"] == "foreground-changed"
    finally:
        controller.shutdown()
