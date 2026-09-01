from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication

import lilies.companion_controller as companion_controller_module
from lilies.companion_controller import CompanionController
from lilies.core.activity import (
    CaptureEncodeError,
    CaptureStorageError,
    ForegroundContext,
    LowInformationCapture,
    ProtectedCaptureContent,
    StagedCapture,
)
from lilies.core.database import Database


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
        has_image = kwargs.get("image_path") is not None
        return {
            "summary": "只说有证据的事情。",
            "detail": "网页像素需要单独授权。",
            "model": "capture-policy-test",
            "contextType": (
                "active-window-image" if has_image else "application-signal"
            ),
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


@pytest.mark.parametrize(
    ("process_name", "browser_authorized", "expect_capture"),
    [
        ("chrome.exe", False, False),
        ("msedge.exe", True, False),
        ("firefox.exe", True, False),
        ("wps.exe", False, True),
    ],
)
def test_v0334_browser_pixels_stay_paused_but_allowed_bubbles_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_name: str,
    browser_authorized: bool,
    expect_capture: bool,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(
        42,
        process_id=8,
        process_name=process_name,
        window_class="ordinary-window",
        title="ordinary safe title",
    )
    controller = _controller(tmp_path, monkeypatch, context)
    grab_calls: list[int] = []

    def grab(hwnd: int) -> Image.Image:
        grab_calls.append(int(hwnd))
        return Image.effect_noise((96, 72), 32).convert("RGB")

    monkeypatch.setattr(companion_controller_module, "capture_window_image", grab)
    if browser_authorized:
        controller.authorizeBrowserSingleCapture(True)
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: bool(controller.bubble) and not controller.busy)
        assert grab_calls == ([42] if expect_capture else [])
        assert controller.runtime.calls
        assert bool(controller.runtime.calls[0]["image_path"]) is expect_capture
        assert controller.bubble["contextType"] == (
            "active-window-image" if expect_capture else "application-signal"
        )
        if process_name in {"chrome.exe", "msedge.exe", "firefox.exe"}:
            assert controller.activityStatus["browserSingleCaptureEnabled"] is False
            assert controller.activityStatus["lastCaptureOutcome"] == "skipped"
            assert (
                controller.activityStatus["lastCaptureReason"]
                == "browser-capture-paused"
            )
            assert controller.activityStatus["lastContextType"] == "application-signal"
    finally:
        controller.shutdown()


def test_legacy_database_does_not_inherit_global_consent_for_browser_pixels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = ForegroundContext(51, process_name="firefox.exe")
    seed = Database(tmp_path / "lilies.db")
    seed.set_setting("browser_single_capture_authorized", True)
    seed.close()
    controller = _controller(tmp_path, monkeypatch, context)
    try:
        assert controller.activityStatus["smartObservationEnabled"] is True
        assert controller.activityStatus["browserSingleCaptureEnabled"] is False
        assert (
            controller.database.get_setting(
                "browser_single_capture_authorized", False
            )
            is False
        )
    finally:
        controller.shutdown()


@pytest.mark.parametrize(
    "title",
    ["ordinary article", "Sign in to continue", "Private Browsing"],
)
def test_all_browser_surface_kinds_share_the_v0334_pixel_pause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    title: str,
) -> None:
    context = ForegroundContext(
        52,
        process_name="msedge.exe",
        window_class="Chrome_WidgetWin_1",
        title=title,
    )
    controller = _controller(tmp_path, monkeypatch, context)
    try:
        assert controller._capture_policy(context) == (
            False,
            "browser-capture-paused",
        )
    finally:
        controller.shutdown()


def test_native_grab_failure_uses_content_free_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(55, process_name="wps.exe", title="paper")
    controller = _controller(tmp_path, monkeypatch, context)

    def fail_grab(_hwnd: int):
        raise OSError(r"private native detail C:\secret\paper.png")

    monkeypatch.setattr(companion_controller_module, "capture_window_image", fail_grab)
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert controller.bubble == {}
        assert controller.runtime.calls == []
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "failed"
        assert status["lastCaptureReason"] == "native-grab-failed"
        assert status["imageSubmitted"] is False
        persisted = repr(
            controller.database.get_setting("companion_last_capture_status", {})
        )
        assert "secret" not in persisted
        assert "paper.png" not in persisted
        assert controller._generation_attempt_not_before > time.monotonic()
    finally:
        controller.shutdown()


@pytest.mark.parametrize(
    ("error_type", "expected_reason"),
    [
        (ProtectedCaptureContent, "protected-black"),
        (LowInformationCapture, "low-information"),
        (CaptureStorageError, "encode-storage-failed"),
        (CaptureEncodeError, "encode-failed"),
    ],
)
def test_encode_failure_classes_have_fixed_content_free_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[RuntimeError],
    expected_reason: str,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(56, process_name="wps.exe", title="paper")
    controller = _controller(tmp_path, monkeypatch, context)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.effect_noise((96, 72), 32).convert("RGB"),
    )

    def fail_encode(_hwnd, _image, *, cancelled):
        assert not cancelled()
        raise error_type(r"private encoder detail F:\secret\capture.png")

    monkeypatch.setattr(controller.capture_staging, "stage_image", fail_encode)
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert controller.bubble == {}
        assert controller.runtime.calls == []
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "failed"
        assert status["lastCaptureReason"] == expected_reason
        assert status["imageSubmitted"] is False
        persisted = repr(
            controller.database.get_setting("companion_last_capture_status", {})
        )
        assert "secret" not in persisted
        assert "capture.png" not in persisted
        assert controller._generation_attempt_not_before > time.monotonic()
    finally:
        controller.shutdown()


def test_browser_authorize_true_fails_closed_and_cleans_legacy_inflight_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = ForegroundContext(
        61,
        process_id=9,
        process_name="brave.exe",
        window_class="Chrome_WidgetWin_1",
        title="safe page",
    )
    controller = _controller(tmp_path, monkeypatch, context)
    staged_path = tmp_path / "capture-staging" / "legacy-browser.png"
    staged_path.parent.mkdir(parents=True, exist_ok=True)
    staged_path.write_bytes(b"synthetic")
    staged = StagedCapture(staged_path, tmp_path / "capture-library")
    cancelled = threading.Event()
    controller._browser_single_capture = True
    controller._active_generation_token = 3
    controller._active_generation_has_capture = True
    controller._generation_cancel_event = cancelled
    controller._busy = True
    controller._set_inflight_capture(staged)
    try:
        controller.authorizeBrowserSingleCapture(True)
        assert controller.activityStatus["browserSingleCaptureEnabled"] is False
        assert controller.database.get_setting(
            "browser_single_capture_authorized", True
        ) is False
        assert cancelled.is_set()
        assert controller.busy is False
        assert controller.activityStatus["lastCaptureOutcome"] == "cancelled"
        assert (
            controller.activityStatus["lastCaptureReason"]
            == "browser-authorization-revoked"
        )
        assert controller.activityStatus["imageSubmitted"] is False
        assert controller.runtime.calls == []
        assert controller.bubble == {}
        assert not staged_path.exists()
    finally:
        controller.shutdown()


def test_browser_capture_ui_truthfully_reports_v0334_pause() -> None:
    qml = (Path(__file__).parents[1] / "qml" / "Main.qml").read_text("utf-8")
    assert 'objectName: "companionBrowserSingleCaptureButton"' in qml
    assert 'objectName: "companionBrowserCaptureWarning"' in qml
    assert 'text: "浏览器像素观察暂不开放"' in qml
    assert "普通网页、登录页与隐私窗口都不会进入像素截图路径" in qml
    assert "browserSingleCaptureConfirm.open()" not in qml
    assert "authorizeBrowserSingleCapture(true)" not in qml
