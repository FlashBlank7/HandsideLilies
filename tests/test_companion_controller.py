from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QTimer
# These timer-only tests share a process with QML lifecycle tests in the
# release suite.  Start the GUI-capable application class from the outset;
# Qt cannot upgrade an existing QCoreApplication later in the same process.
from PySide6.QtGui import QGuiApplication as QCoreApplication

import lilies.companion_controller as companion_controller_module
from lilies.companion_controller import CompanionController
from lilies.core.activity import ForegroundContext, StagedCapture
from lilies.core.companion import (
    BubbleSource,
    ContentCategory,
    summaries_are_near_duplicates,
)
from lilies.core.companion_runtime import LUNA_MODEL
from lilies.core.content import ContentItem
from lilies.core.database import Database
from lilies.core.orchestration import ModelTaskBroker, ModelTaskKind, ModelTaskState


class _Idle:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def idle_seconds(self) -> float:
        return self.seconds


def _wait_for(app: QCoreApplication, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def test_default_generation_sends_only_coarse_application_category(
    tmp_path, monkeypatch
) -> None:
    """Document titles and window classes must stay out of subscription prompts."""

    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            return {
                "summary": "只知道你正在处理文档。",
                "detail": "没有读取标题或正文。",
                "model": "fake-subscription",
                "contextType": "application-signal",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    context = ForegroundContext(
        42,
        process_id=7,
        process_name="wps.exe",
        window_class="SecretDocumentWindow",
        title="unpublished-paper-title.docx",
    )
    controller.activity.update_foreground(context)
    try:
        assert controller._start_generation(context, force=True) is True
        assert _wait_for(app, lambda: bool(generated) and not controller.busy)
        metadata = dict(generated[0]["context_metadata"])
        assert metadata == {
            "applicationCategory": "文档工作",
            "fullScreen": False,
            "inputScope": "application-category-only",
        }
        serialized = repr(generated[0])
        assert "unpublished-paper-title" not in serialized
        assert "SecretDocumentWindow" not in serialized
        assert "wps.exe" not in serialized
    finally:
        controller.shutdown()
    assert app is not None


def test_previous_momentum_is_only_weak_context_not_current_scene_fact(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            return {
                "summary": "当前是文档工作。",
                "detail": "旧主题没有覆盖当前场景。",
                "model": "test",
                "contextType": "application-signal",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    context = ForegroundContext(42, process_name="wps.exe")
    controller.activity.update_foreground(context)
    controller.momentum.current = "论文阅读"
    try:
        assert controller._start_generation(context, force=True)
        assert _wait_for(app, lambda: bool(generated) and not controller.busy)
        assert generated[0]["scene_label"] == "文档工作"
        assert generated[0]["context_metadata"] == {
            "applicationCategory": "文档工作",
            "fullScreen": False,
            "inputScope": "application-category-only",
            "weakMomentumTopic": "论文阅读",
        }
    finally:
        controller.shutdown()
    assert app is not None


def test_smart_capture_rechecks_same_hwnd_after_capture(tmp_path, monkeypatch) -> None:
    """A same-HWND switch to payment content must discard the staged image."""

    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna"}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            return {"summary": "unexpected", "detail": "unexpected"}

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    initial = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Research article",
    )
    payment = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Payment checkout",
    )
    reads = iter((initial, payment))
    controller.reader = lambda _hwnd: next(reads)
    controller._smart_observation = True
    controller.activity.update_foreground(initial)
    stage_calls: list[int] = []

    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (32, 32), "white"),
    )

    def stage(hwnd: int, _capture) -> StagedCapture:
        stage_calls.append(int(hwnd))
        capture_path = tmp_path / "capture-staging" / "capture-test.png"
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(b"synthetic")
        return StagedCapture(capture_path, tmp_path / "capture-library")

    monkeypatch.setattr(controller.capture_staging, "stage", stage)
    try:
        # Automatic generation has already passed the timing gate in
        # ``_consider``; this test isolates the post-capture identity recheck.
        assert controller._start_generation(initial, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert stage_calls == []
        assert generated == []
        assert controller.busy is False
        assert controller.activityStatus["state"] == "payment-window"
    finally:
        controller.shutdown()
    assert app is not None


def test_regular_heartbeat_does_not_poll_title_for_same_hwnd(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.activity.update_foreground(
        ForegroundContext(42, process_id=8, process_name="wps.exe", title="Paper")
    )
    reader_calls: list[int] = []
    controller.reader = lambda hwnd: (
        reader_calls.append(int(hwnd))
        or ForegroundContext(42, process_id=8, process_name="wps.exe", title="Paper")
    )
    controller.activity.set_paused(True)
    try:
        controller._consider()
        assert reader_calls == []
    finally:
        controller.shutdown()
    assert app is not None


def test_capture_preflight_title_change_resets_stability_without_grab(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    clock = [10.0]
    controller.activity.clock = lambda: clock[0]
    initial = ForegroundContext(
        42, process_id=8, process_name="wps.exe", window_class="WPS", title="Paper A"
    )
    changed_document = ForegroundContext(
        42, process_id=8, process_name="wps.exe", window_class="WPS", title="Paper B"
    )
    controller.activity.update_foreground(initial)
    controller.activity.enabled = True
    clock[0] = 130.0
    controller.reader = lambda _hwnd: changed_document
    controller._smart_observation = True
    controller.runtime.modality_status = {
        "checked": True,
        "imageModel": "luna",
        "error": "",
    }

    def grab_must_not_run(_hwnd):
        raise AssertionError("changed document must restabilize before capture")

    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", grab_must_not_run
    )
    try:
        assert controller._start_generation(initial, force=False) is False
        current = controller.activity.current_context
        assert current is not None
        assert current.changed_at == 130.0
        assert controller.activity.consider_observation(now=249.0).reason == "window-not-stable"
        assert controller.activityStatus["lastCaptureOutcome"] == "skipped"
        assert controller.activityStatus["lastCaptureReason"] == "window-content-changed"
        assert not (tmp_path / "capture-staging").exists()
    finally:
        controller.shutdown()
    assert app is not None


def test_capture_preflight_title_becoming_empty_resets_without_grab(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    clock = [10.0]
    controller.activity.clock = lambda: clock[0]
    initial = ForegroundContext(
        42, process_id=8, process_name="wps.exe", window_class="WPS", title="Paper A"
    )
    untitled = ForegroundContext(
        42, process_id=8, process_name="wps.exe", window_class="WPS", title=""
    )
    controller.activity.update_foreground(initial)
    controller.activity.enabled = True
    clock[0] = 130.0
    controller.reader = lambda _hwnd: untitled
    controller._smart_observation = True
    controller.runtime.modality_status = {
        "checked": True,
        "imageModel": "luna",
        "error": "",
    }

    def grab_must_not_run(_hwnd):
        raise AssertionError("untitled replacement must restabilize before capture")

    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", grab_must_not_run
    )
    try:
        assert controller._start_generation(initial, force=False) is False
        assert controller.activity.current_context is not None
        assert controller.activity.current_context.changed_at == 130.0
        assert controller.activityStatus["lastCaptureOutcome"] == "skipped"
        assert controller.activityStatus["lastCaptureReason"] == "window-content-changed"
    finally:
        controller.shutdown()
    assert app is not None


def test_authorized_image_observation_cleans_staging_and_reaches_native_ack(
    tmp_path, monkeypatch
) -> None:
    """Authorized capture completes the full offscreen presentation contract."""

    app = QCoreApplication.instance() or QCoreApplication([])
    generated_paths: list[Path] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **kwargs):
            image_path = Path(kwargs["image_path"])
            assert image_path.is_file()
            generated_paths.append(image_path)
            return {
                "summary": "你在安静地读一页论文。",
                "detail": "先把这一段读完就好。",
                "model": LUNA_MODEL,
                "contextType": "active-window-image",
                "imageGrounded": True,
                "evidenceConfidence": "high",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Research article",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    capture_path = tmp_path / "capture-staging" / "authorized.png"
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.effect_noise((64, 64), 32).convert("RGB"),
    )

    def stage(_hwnd: int, _capture) -> StagedCapture:
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(b"synthetic-png")
        return StagedCapture(capture_path, tmp_path / "capture-library")

    monkeypatch.setattr(controller.capture_staging, "stage", stage)
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: bool(controller.bubble) and not controller.busy)
        assert generated_paths == [capture_path]
        assert not capture_path.exists()
        assert controller.bubble["hasCapture"] is True
        assert controller.bubble["contextType"] == "active-window-image"
        pending_status = controller.activityStatus
        assert pending_status["lastCapturePresentationOutcome"] == "pending"
        assert pending_status["lastCapturePresentationReason"] == "awaiting-presentation"
        bubble_id = str(controller.bubble["id"])
        assert controller.ackPresented(bubble_id, True, True, 1) is True
        assert controller.deliveryStatus["state"] == "presented"
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePixelsUsed"] is True
        assert status["lastCaptureModel"] == LUNA_MODEL
        assert status["lastCaptureEvidenceConfidence"] == "high"
        assert status["lastCapturePresentationOutcome"] == "shown"
        assert status["lastCapturePresentationReason"] == "window-exposed"
        session = controller.database.proactive_session(
            controller._bubble_object.session_id
        )
        assert session is not None
        assert session["generation"] == {
            "schemaVersion": 1,
            "contextType": "active-window-image",
            "imageGrounded": True,
            "model": LUNA_MODEL,
            "evidenceConfidence": "high",
        }
    finally:
        controller.shutdown()
    assert app is not None


def test_explicit_one_shot_observation_uses_pixels_and_prefers_philosophy(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            assert Path(kwargs["image_path"]).is_file()
            return {
                "summary": "左侧留白把两段文字分成彼此可见的岛。",
                "detail": "留白没有内容，却参与决定什么会被看作一个整体。",
                "model": "one-shot-image-test",
                "contextType": "active-window-image",
                "imageGrounded": True,
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.effect_noise((96, 72), 32).convert("RGB"),
    )
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    try:
        assert controller.requestScreenNow() is True
        assert _wait_for(app, lambda: bool(controller.bubble) and not controller.busy)
        assert len(generated) == 1
        assert generated[0]["category"] is ContentCategory.PHILOSOPHY
        assert generated[0]["image_path"] is not None
        assert controller.bubble["category"] == ContentCategory.PHILOSOPHY.value
        assert controller.bubble["contextType"] == "active-window-image"
        assert controller.activityStatus["lastCaptureOutcome"] == "used"
        assert not list((tmp_path / "capture-staging").glob("capture-*.png"))
    finally:
        controller.shutdown()
    assert app is not None


def test_explicit_one_shot_observation_refuses_browser_pixels(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(
        88,
        process_id=12,
        process_name="chrome.exe",
        window_class="Chrome_WidgetWin_1",
        title="Research page",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 88,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    controller.runtime.modality_status = {
        "checked": True,
        "imageModel": "luna",
        "error": "",
    }
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: (_ for _ in ()).throw(
            AssertionError("browser pixels must not be captured")
        ),
    )
    try:
        assert controller.requestScreenNow() is False
        assert controller.bubble == {}
        assert controller.busy is False
        assert "浏览器像素观察暂不开放" in controller.activityStatus["requestFeedback"]
        assert controller.activityStatus["lastCaptureOutcome"] == "skipped"
        assert controller.activityStatus["lastCaptureReason"] in {
            "browser-capture-not-authorized",
            "browser-capture-paused",
        }
    finally:
        controller.shutdown()
    assert app is not None


def test_explicit_one_shot_observation_requires_authorization_and_image_model(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    probes: list[bool] = []
    try:
        assert controller.requestScreenNow() is False
        assert "先阅读说明并授权" in controller.activityStatus["requestFeedback"]

        controller._smart_observation = True
        controller.runtime.modality_status = {
            "checked": True,
            "imageModel": "",
            "error": "synthetic unavailable",
        }
        monkeypatch.setattr(
            controller, "_probe_modalities", lambda: probes.append(True)
        )
        assert controller.requestScreenNow() is False
        assert probes == [True]
        assert "图像能力" in controller.activityStatus["requestFeedback"]
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


def test_uniform_capture_stays_quiet_without_calling_text_runtime(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            raise AssertionError("capture failure must not fall back to text generation")

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (320, 240), "white"),
    )
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    try:
        started_at = time.monotonic()
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert generated == []
        assert controller.bubble == {}
        assert controller.activityStatus["lastCaptureOutcome"] == "failed"
        assert controller.activityStatus["lastCaptureReason"] == "low-information"
        assert controller.activityStatus["imageSubmitted"] is False
        assert controller._generation_attempt_not_before >= started_at + 29.0
    finally:
        controller.shutdown()
    assert app is not None


def test_native_capture_failure_stays_quiet_without_calling_text_runtime(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    generate_calls: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **kwargs):
            generate_calls.append(dict(kwargs))
            raise AssertionError("native capture failure must stay content-free")

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    def fail_capture(_hwnd: int):
        raise OSError("synthetic native capture failure")

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", fail_capture
    )
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    try:
        started_at = time.monotonic()
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert generate_calls == []
        assert controller.bubble == {}
        assert controller.activityStatus["lastCaptureOutcome"] == "failed"
        assert controller.activityStatus["lastCaptureReason"] == "native-grab-failed"
        assert controller.activityStatus["imageSubmitted"] is False
        assert controller._generation_attempt_not_before >= started_at + 29.0
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("force", [False, True])
def test_image_quality_skip_is_quiet_and_does_not_spend_observation_gate(
    tmp_path, force: bool
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    feedback_before = controller.activityStatus["requestFeedback"]
    observation_before = controller.activity._last_observation_at
    try:
        started_at = time.monotonic()
        controller._accept_generation(
            {
                "result": {
                    "summary": "",
                    "detail": "",
                    "model": "fake-image-model",
                    "contextType": "active-window-image",
                    "imageGrounded": False,
                    "skip": True,
                    "skipReason": "image-low-confidence",
                    "retryAfterSeconds": 30.0,
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "document work",
                "capture": None,
                "force": force,
                "generationToken": 0,
            }
        )
        assert controller.bubble == {}
        assert controller.activity._last_observation_at == observation_before
        assert controller._generation_attempt_not_before >= started_at + 29.0
        if force:
            assert "没有弹出气泡" in controller.activityStatus["requestFeedback"]
            assert controller.activityStatus["requestFeedbackKind"] == "quiet"
        else:
            assert controller.activityStatus["requestFeedback"] == feedback_before
    finally:
        controller.shutdown()
    assert app is not None


def test_image_quality_skip_records_specific_content_free_capture_reason(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    generate_calls: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **kwargs):
            generate_calls.append(dict(kwargs))
            assert Path(kwargs["image_path"]).is_file()
            return {
                "summary": "",
                "detail": "",
                "model": "fake-image-model",
                "contextType": "active-window-image",
                "imageGrounded": False,
                "skip": True,
                "skipReason": "image-low-confidence",
                "retryAfterSeconds": 30.0,
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.effect_noise((96, 72), 32).convert("RGB"),
    )
    context = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPS",
        title="Paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy)
        assert len(generate_calls) == 1
        assert controller.bubble == {}
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "failed"
        assert status["lastCaptureReason"] == "image-low-confidence"
        assert status["lastCaptureReasonLabel"] == "图像模型没有足够的画面把握"
        assert status["captureAttempted"] is True
        assert status["imageSubmitted"] is True
        assert status["imageResponseAccepted"] is False
        assert not list((tmp_path / "capture-staging").glob("capture-*.png"))
    finally:
        controller.shutdown()
    assert app is not None


def test_revoking_capture_authorization_cancels_and_fences_late_result(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    capture_path = tmp_path / "capture-staging" / "inflight.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(b"synthetic")
    capture = StagedCapture(capture_path, tmp_path / "capture-library")
    try:
        controller._smart_observation = True
        controller._busy = True
        controller._active_generation_token = 7
        controller._generation_cancel_event = threading.Event()
        controller._set_inflight_capture(capture)
        controller.authorizeSmartObservation(False)
        assert not capture_path.exists()
        assert controller.busy is False
        assert controller.activityStatus["lastCaptureOutcome"] == "cancelled"
        assert controller.activityStatus["lastCaptureReason"] == "authorization-revoked"

        controller._accept_generation(
            {
                "generationToken": 7,
                "result": {"summary": "late", "detail": "late"},
                "category": ContentCategory.LORE,
                "sceneLabel": "late",
                "force": False,
            }
        )
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


def test_image_philosophy_quality_failure_keeps_truthful_capture_receipt(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        token = controller._begin_capture_diagnostic()
        controller._record_capture_outcome(
            "submitted",
            "model-request-starting",
            attempt_token=token,
            model=LUNA_MODEL,
        )
        controller._record_capture_outcome(
            "failed",
            "philosophy-quality-invalid",
            attempt_token=token,
            model="vision-quality-test",
            evidence_confidence="high",
        )
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "failed"
        assert status["imageSubmitted"] is True
        assert status["lastCapturePixelsUsed"] is False
        assert status["lastCaptureModel"] == LUNA_MODEL
        assert status["lastCaptureEvidenceConfidence"] == "none"
        assert status["lastCaptureReasonLabel"] == (
            "画面已理解，但表达没有通过哲思质量检查"
        )
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("model_completed", [False, True])
def test_capture_cancellation_preserves_submitted_or_used_pixel_truth(
    tmp_path, model_completed: bool
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        token = controller._begin_capture_diagnostic()
        controller._record_capture_outcome(
            "submitted",
            "model-request-starting",
            attempt_token=token,
            model=LUNA_MODEL,
        )
        if model_completed:
            controller._record_capture_outcome(
                "used",
                "image-model-completed",
                attempt_token=token,
                model=LUNA_MODEL,
                evidence_confidence="high",
            )
        controller._active_generation_token = 91
        controller._generation_cancel_event = threading.Event()
        controller._active_generation_has_capture = True
        controller._active_generation_capture_diagnostic_token = token
        controller._busy = True

        assert controller._cancel_active_generation(
            "foreground-context-changed",
            capture_reason="request-cancelled",
        ) is True
        status = controller.activityStatus
        assert status["imageSubmitted"] is True
        assert status["lastCaptureModel"] == LUNA_MODEL
        assert status["lastCapturePresentationOutcome"] == "cancelled"
        assert status["lastCapturePresentationReason"] == "generation-cancelled"
        if model_completed:
            assert status["lastCaptureOutcome"] == "used"
            assert status["lastCapturePixelsUsed"] is True
            assert status["lastCaptureEvidenceConfidence"] == "high"
        else:
            assert status["lastCaptureOutcome"] == "cancelled"
            assert status["lastCapturePixelsUsed"] is False
            assert status["lastCaptureEvidenceConfidence"] == "none"

        # The cancellation advances the diagnostic fence only after saving
        # the terminal receipt. A late worker must not rewrite it.
        snapshot = database.get_setting("companion_last_capture_status", {})
        controller._record_capture_outcome(
            "failed", "model-error", attempt_token=token, model=LUNA_MODEL
        )
        assert database.get_setting("companion_last_capture_status", {}) == snapshot
    finally:
        controller.shutdown()
    assert app is not None


def test_image_bubble_ack_timeout_keeps_pixel_use_and_records_unread(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    capture_path = tmp_path / "capture-staging" / "presentation.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(b"synthetic-image")
    capture = StagedCapture(capture_path, tmp_path / "capture-library")
    try:
        token = controller._begin_capture_diagnostic()
        controller._record_capture_outcome(
            "used",
            "image-model-completed",
            attempt_token=token,
            model=LUNA_MODEL,
            evidence_confidence="medium",
        )
        controller._smart_observation = True
        controller._active_generation_token = 17
        controller._active_generation_has_capture = True
        controller._active_generation_capture_diagnostic_token = token
        controller._busy = True
        controller._accept_generation(
            {
                "generationToken": 17,
                "captureDiagnosticToken": token,
                "capture": capture,
                "result": {
                    "summary": "留白让边界变得可见。",
                    "detail": "留白参与定义整体，也留下一个问题：缺席是否同样在组织注意？",
                    "model": LUNA_MODEL,
                    "contextType": "active-window-image",
                    "imageGrounded": True,
                    "evidenceConfidence": "medium",
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "document work",
                "force": True,
            }
        )
        assert controller.activityStatus["lastCapturePresentationOutcome"] == "pending"
        controller._presentation_ack_timed_out()
        status = controller.activityStatus
        assert controller.bubble == {}
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePixelsUsed"] is True
        assert status["lastCapturePresentationOutcome"] == "unread"
        assert status["lastCapturePresentationReason"] == "presentation-ack-timeout"
    finally:
        controller.shutdown()
    assert app is not None


def test_capture_diagnostic_write_failure_cannot_block_ack_or_ttl(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        token = controller._begin_capture_diagnostic()
        controller._record_capture_outcome(
            "used",
            "image-model-completed",
            attempt_token=token,
            model=LUNA_MODEL,
            evidence_confidence="medium",
        )
        controller._bubble = {
            "id": "diagnostic-write-failure",
            "visible": True,
            "contextType": "active-window-image",
            "expiresAt": "",
            "deliveryState": "waiting-present-ack",
        }
        controller._bubble_capture_diagnostic_token = token
        controller._presentation_ack_pending = True
        original_set_setting = database.set_setting

        def fail_capture_diagnostic(key, value):
            if key == "companion_last_capture_status":
                raise OSError("synthetic full disk")
            return original_set_setting(key, value)

        monkeypatch.setattr(database, "set_setting", fail_capture_diagnostic)
        assert controller.ackPresented(
            "diagnostic-write-failure", True, True, 1
        ) is True
        assert controller.deliveryStatus["state"] == "presented"
        assert controller._bubble_expiry_timer.isActive() is True
        assert controller.activityStatus["lastCapturePresentationOutcome"] == "shown"
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize(
    ("persisted", "requested", "action"),
    ((False, True, "开启"), (True, False, "撤销")),
)
def test_authorize_smart_observation_write_failure_is_atomic_and_visible(
    tmp_path, monkeypatch, persisted: bool, requested: bool, action: str
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    database.set_setting("smart_observation_authorized", persisted)
    statuses: list[str] = []
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
    )
    changed_count = 0
    cancel_event: threading.Event | None = None
    if persisted and not requested:
        # A failed revoke must not apply any revoke side effect either.
        controller._busy = True
        controller._active_generation_token = 7
        cancel_event = threading.Event()
        controller._generation_cancel_event = cancel_event

    def on_changed() -> None:
        nonlocal changed_count
        changed_count += 1

    controller.changed.connect(on_changed)
    monkeypatch.setattr(
        database,
        "set_setting",
        lambda _key, _value: (_ for _ in ()).throw(OSError("disk full")),
    )
    try:
        controller.authorizeSmartObservation(requested)

        assert controller.activityStatus["smartObservationEnabled"] is persisted
        assert database.get_setting("smart_observation_authorized", None) is persisted
        assert controller.activityStatus["requestFeedbackKind"] == "warning"
        assert f"授权{action}保存失败" in controller.activityStatus["requestFeedback"]
        assert statuses and f"授权{action}保存失败" in statuses[-1]
        assert changed_count == 1
        if cancel_event is not None:
            assert controller.busy is True
            assert controller._active_generation_token == 7
            assert not cancel_event.is_set()
    finally:
        controller.shutdown()
    assert app is not None


def test_successful_capture_authorization_revoke_releases_retained_capture(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    database.set_setting("smart_observation_authorized", True)
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    capture_path = tmp_path / "capture-staging" / "retained.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(b"synthetic")
    capture = StagedCapture(capture_path, tmp_path / "capture-library")
    capture.retain_in_memory()
    controller._capture = capture
    controller._bubble = {"hasCapture": True}
    bubble_changed_count = 0

    def on_bubble_changed() -> None:
        nonlocal bubble_changed_count
        bubble_changed_count += 1

    controller.bubbleChanged.connect(on_bubble_changed)
    try:
        controller.authorizeSmartObservation(False)

        assert database.get_setting("smart_observation_authorized", None) is False
        assert controller.activityStatus["smartObservationEnabled"] is False
        assert controller._capture is None
        assert capture._image_bytes is None
        assert controller.bubble["hasCapture"] is False
        assert bubble_changed_count == 1
    finally:
        controller.shutdown()
    assert app is not None


def test_slow_capture_encoding_does_not_block_qt_timer(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    encoding_started = threading.Event()
    release_encoding = threading.Event()

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **_kwargs):
            return {
                "summary": "编码没有挡住桌面。",
                "detail": "计时器仍在前进。",
                "model": "fake-image-model",
                "contextType": "active-window-image",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (128, 128), "white"),
    )
    context = ForegroundContext(42, process_id=8, process_name="wps.exe", title="Paper")
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True

    def slow_stage(_hwnd, _image, *, cancelled):
        encoding_started.set()
        assert release_encoding.wait(2.0)
        if cancelled():
            raise companion_controller_module.CaptureCancelled("cancelled")
        path = tmp_path / "capture-staging" / "slow.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
        return StagedCapture(path, tmp_path / "capture-library")

    monkeypatch.setattr(controller.capture_staging, "stage_image", slow_stage)
    ticks: list[int] = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(len(ticks) + 1))
    timer.start()
    try:
        started_at = time.monotonic()
        assert controller._start_generation(context, force=False) is True
        assert time.monotonic() - started_at < 0.25
        assert encoding_started.wait(0.5)
        assert _wait_for(app, lambda: len(ticks) >= 3, timeout=0.5)
        assert release_encoding.is_set() is False
        release_encoding.set()
        assert _wait_for(app, lambda: bool(controller.bubble), timeout=2.0)
    finally:
        release_encoding.set()
        timer.stop()
        controller.shutdown()
    assert app is not None


def test_raw_capture_is_closed_before_blocking_model_call(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    raw_closed = threading.Event()
    model_started = threading.Event()
    release_model = threading.Event()

    class RawCapture:
        def close(self) -> None:
            raw_closed.set()

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def generate(self, **_kwargs):
            assert raw_closed.is_set()
            model_started.set()
            release_model.wait(2.0)
            return {
                "summary": "raw pixels released",
                "detail": "only the bounded staged PNG remains",
                "model": "fake-image-model",
                "contextType": "active-window-image",
            }

        def abort_model(self, _model_id: str) -> None:
            release_model.set()

        def shutdown(self) -> None:
            release_model.set()

    context = ForegroundContext(42, process_id=8, process_name="wps.exe", title="Paper")
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.runtime.shutdown()
    controller.runtime = Runtime()
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", lambda _hwnd: RawCapture()
    )

    def stage(_hwnd, _image, *, cancelled):
        assert not cancelled()
        path = tmp_path / "capture-staging" / "raw-release.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
        return StagedCapture(path, tmp_path / "capture-library")

    monkeypatch.setattr(controller.capture_staging, "stage_image", stage)
    try:
        assert controller._start_generation(context, force=False) is True
        assert model_started.wait(1.0)
        assert raw_closed.is_set()
        controller.setPaused(True)
        assert _wait_for(app, lambda: not controller.busy)
    finally:
        release_model.set()
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize(
    "replacement_title",
    ["Payment checkout", ""],
    ids=["sensitive-title", "empty-title"],
)
def test_queued_screen_observation_rechecks_live_title_before_model_call(
    tmp_path, monkeypatch, replacement_title
) -> None:
    """A queued capture must be revalidated after its model lease is acquired."""

    app = QCoreApplication.instance() or QCoreApplication([])

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)
            self.generate_calls = 0

        def generate(self, **_kwargs):
            self.generate_calls += 1
            return {
                "summary": "must not be generated",
                "detail": "the capture context is no longer safe",
                "model": "fake-image-model",
                "contextType": "active-window-image",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (64, 64), "white"),
    )
    broker = ModelTaskBroker()
    owner = broker.submit(
        LUNA_MODEL,
        ModelTaskKind.EXPLICIT_CHAT_REPLY,
        {"requestId": "blocking-owner"},
        context_bound=False,
    )
    initial = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Research article",
    )
    replacement = ForegroundContext(
        42,
        process_id=8,
        process_name="wps.exe",
        window_class="WPSWindow",
        title=replacement_title,
    )
    live_context = [initial]
    encoded_context_read = threading.Event()

    def read_context(_hwnd: int) -> ForegroundContext:
        # Capture the return value before waking the test so changing the
        # mutable reader source cannot affect the completed encoding check.
        current = live_context[0]
        # Capture acquisition now runs in the worker and therefore adds its
        # own before/after identity reads.  Wake only on the post-encoding
        # fence: the staged file already exists at that point.
        if staged_path.is_file():
            encoded_context_read.set()
        return current

    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=broker,
        foreground_provider=lambda: 42,
    )
    controller.reader = read_context
    controller.activity.update_foreground(initial)
    controller._smart_observation = True
    staged_path = tmp_path / "capture-staging" / "queued-recheck.png"

    def stage_image(_hwnd, _image, *, cancelled):
        assert not cancelled()
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_bytes(b"synthetic-png")
        return StagedCapture(staged_path, tmp_path / "capture-library")

    monkeypatch.setattr(controller.capture_staging, "stage_image", stage_image)
    try:
        assert controller._start_generation(initial, force=False) is True
        assert encoded_context_read.wait(1.0)
        assert _wait_for(app, staged_path.is_file)
        assert controller.activityStatus["lastCaptureOutcome"] == "staged"
        assert controller.activityStatus["captureAttempted"] is True
        assert controller.activityStatus["imageSubmitted"] is False
        queued = broker.status(LUNA_MODEL)["models"][LUNA_MODEL]["queued"]
        observation_task_id = next(
            item["id"]
            for item in queued
            if item["kind"] == ModelTaskKind.SCREEN_UNDERSTANDING.value
        )

        # No foreground event/update is delivered. Only the live reader sees
        # the same HWND replace its safe title before broker promotion.
        live_context[0] = replacement
        broker.finish(owner.id, result={"completed": True})

        assert _wait_for(
            app,
            lambda: not controller.busy and not staged_path.exists(),
            timeout=2.0,
        )
        assert controller.runtime.generate_calls == 0
        observation_task = broker.get(observation_task_id)
        assert observation_task is not None
        assert observation_task.state is ModelTaskState.CANCELLED
        assert (
            observation_task.cancel_reason
            == "capture-context-changed-before-model"
        )
        assert controller.activityStatus["lastCaptureOutcome"] == "cancelled"
        assert (
            controller.activityStatus["lastCaptureReason"]
            == "capture-context-changed-before-model"
        )
    finally:
        owner_task = broker.get(owner.id)
        if owner_task is not None and owner_task.state is ModelTaskState.RUNNING:
            broker.finish(owner.id, result={"completed": True})
        controller.shutdown()
    assert app is not None


def test_broker_cancel_during_post_acquire_recheck_never_enters_image_model(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    acquired = threading.Event()
    cancelled_in_reader = threading.Event()

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)
            self.generate_calls = 0

        def generate(self, **_kwargs):
            self.generate_calls += 1
            raise AssertionError("cancelled capture must not enter the model")

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.effect_noise((64, 64), 32).convert("RGB"),
    )
    original_acquire = companion_controller_module._BrokerTaskLease.acquire

    def acquire_and_mark(lease) -> bool:
        result = original_acquire(lease)
        if result:
            acquired.set()
        return result

    monkeypatch.setattr(
        companion_controller_module._BrokerTaskLease,
        "acquire",
        acquire_and_mark,
    )
    broker = ModelTaskBroker()
    context = ForegroundContext(
        91,
        process_id=12,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Safe paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=broker,
        foreground_provider=lambda: 91,
    )

    def reader(_hwnd: int) -> ForegroundContext:
        if acquired.is_set() and not cancelled_in_reader.is_set():
            active = broker.status(LUNA_MODEL)["models"][LUNA_MODEL]["active"]
            assert active is not None
            broker.cancel(active["id"], reason="cancel-during-final-reader")
            cancelled_in_reader.set()
        return context

    controller.reader = reader
    controller.activity.update_foreground(context)
    controller._smart_observation = True
    try:
        assert controller._start_generation(context, force=False) is True
        assert _wait_for(app, lambda: not controller.busy, timeout=2.0)
        assert acquired.is_set()
        assert cancelled_in_reader.is_set()
        assert controller.runtime.generate_calls == 0
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


def test_cancelling_during_encoding_removes_partial_staging(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    encoding_started = threading.Event()
    partial_path = tmp_path / "capture-staging" / "capture-partial.png"

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna", "error": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            self.modality_status = dict(type(self).modality_status)

        def generate(self, **_kwargs):
            raise AssertionError("cancelled capture must not reach the model")

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    monkeypatch.setattr(
        companion_controller_module,
        "capture_window_image",
        lambda _hwnd: Image.new("RGB", (128, 128), "white"),
    )
    context = ForegroundContext(42, process_id=8, process_name="wps.exe", title="Paper")
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller.activity.update_foreground(context)
    controller._smart_observation = True

    def cancellable_stage(_hwnd, _image, *, cancelled):
        partial_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path.write_bytes(b"partial")
        encoding_started.set()
        while not cancelled():
            time.sleep(0.005)
        partial_path.unlink(missing_ok=True)
        raise companion_controller_module.CaptureCancelled("cancelled")

    monkeypatch.setattr(controller.capture_staging, "stage_image", cancellable_stage)
    try:
        assert controller._start_generation(context, force=False) is True
        assert encoding_started.wait(0.5)
        controller.setActivityEnabled(False)
        assert _wait_for(
            app,
            lambda: (
                not partial_path.exists()
                and controller.activityStatus["lastCaptureOutcome"] == "cancelled"
            ),
            timeout=2.0,
        )
        assert controller.busy is False
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("failure_point", ["provider", "reader"])
def test_activity_scheduler_survives_a_failing_startup_foreground_probe(
    tmp_path, monkeypatch, failure_point
) -> None:
    """A transient User32 race must not create an enabled-but-dead service."""

    app = QCoreApplication.instance() or QCoreApplication([])
    calls = {"provider": 0, "reader": 0}

    class Reader:
        def __call__(self, hwnd: int) -> ForegroundContext:
            calls["reader"] += 1
            if failure_point == "reader" and calls["reader"] == 1:
                raise RuntimeError("synthetic startup reader race")
            return ForegroundContext(int(hwnd), process_name="wps.exe")

    monkeypatch.setattr(
        companion_controller_module,
        "Win32ForegroundContextReader",
        lambda: Reader(),
    )

    def foreground() -> int:
        calls["provider"] += 1
        if failure_point == "provider" and calls["provider"] == 1:
            raise OSError("synthetic startup foreground race")
        return 77

    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=True,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        unified_event_hub=object(),
        foreground_provider=foreground,
    )
    try:
        assert controller.activityStatus["configuredEnabled"] is True
        assert controller.activity.enabled is True
        assert controller._timer.isActive() is True
        assert controller._timer.interval() == 1500
        assert controller.activity.current_context is None
        controller._last_foreground_reconcile_at = 0.0
        controller._consider()
        assert controller.activity.current_context is not None
        assert controller.activity.current_context.hwnd == 77
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("failure_point", ["provider", "reader"])
def test_activity_scheduler_survives_a_failing_reenable_foreground_probe(
    tmp_path, monkeypatch, failure_point
) -> None:
    """Re-enabling must stay recoverable when the first foreground read races."""

    app = QCoreApplication.instance() or QCoreApplication([])
    calls = {"provider": 0, "reader": 0}

    class Reader:
        def __call__(self, hwnd: int) -> ForegroundContext:
            calls["reader"] += 1
            if failure_point == "reader" and calls["reader"] == 1:
                raise RuntimeError("synthetic re-enable reader race")
            return ForegroundContext(int(hwnd), process_name="wps.exe")

    monkeypatch.setattr(
        companion_controller_module,
        "Win32ForegroundContextReader",
        lambda: Reader(),
    )

    def foreground() -> int:
        calls["provider"] += 1
        if failure_point == "provider" and calls["provider"] == 1:
            raise OSError("synthetic re-enable foreground race")
        return 78

    database = Database(tmp_path / "lilies.db")
    database.set_setting("activity_context_enabled", False)
    controller = CompanionController(
        database,
        tmp_path,
        active=True,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        unified_event_hub=object(),
        foreground_provider=foreground,
    )
    try:
        assert controller._timer.isActive() is False
        controller.setActivityEnabled(True)
        assert controller.activityStatus["configuredEnabled"] is True
        assert controller.activity.enabled is True
        assert controller._timer.isActive() is True
        assert controller._timer.interval() == 1500
        assert controller.activity.current_context is None
        controller._last_foreground_reconcile_at = 0.0
        controller._consider()
        assert controller.activity.current_context is not None
        assert controller.activity.current_context.hwnd == 78
    finally:
        controller.shutdown()
    assert app is not None


def test_shutdown_fences_a_queued_generation_result(tmp_path, monkeypatch) -> None:
    """A worker result queued before shutdown must not revive UI or storage."""

    app = QCoreApplication.instance() or QCoreApplication([])
    returned = threading.Event()

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate(self, **_kwargs):
            returned.set()
            return {
                "summary": "synthetic late result",
                "detail": "synthetic only",
                "model": "fake",
                "contextType": "application-signal",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=ModelTaskBroker(),
        foreground_provider=lambda: 0,
    )
    assert controller._start_generation(None, force=True) is True
    assert returned.wait(2.0)
    deadline = time.monotonic() + 2.0
    while controller._worker_threads and time.monotonic() < deadline:
        time.sleep(0.01)
    assert controller._worker_threads == set()
    assert controller.bubble == {}

    controller.shutdown()
    for _ in range(4):
        app.processEvents()
    assert controller.bubble == {}
    with database.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM proactive_sessions"
        ).fetchone()[0]
    assert count == 0


def test_shutdown_blocks_an_authorization_single_shot_refresh(
    tmp_path, monkeypatch
) -> None:
    """The uncancellable Qt singleShot must become inert after shutdown."""

    app = QCoreApplication.instance() or QCoreApplication([])
    refresh_started = threading.Event()

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    controller._active = True
    controller.refresh_source_component = (
        lambda *_args: refresh_started.set() or {"providerId": "synthetic"}
    )
    controller.authorizeOnlineContent(True)
    controller.shutdown()
    for _ in range(4):
        app.processEvents()
    assert controller._active is False
    assert refresh_started.is_set() is False
    assert controller._worker_threads == set()


def test_foreground_change_cancels_only_context_bound_companion_tasks(
    tmp_path,
) -> None:
    broker = ModelTaskBroker()
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=broker,
        foreground_provider=lambda: 0,
    )
    explicit_id = controller._submit_model_task(
        "luna-mid",
        ModelTaskKind.EXPLICIT_CHAT_REPLY,
        {},
        context_bound=False,
        ttl_seconds=30,
    )
    archive_id = controller._submit_model_task(
        "luna-mid",
        ModelTaskKind.MEMORY_ARCHIVE,
        {},
        context_bound=False,
        ttl_seconds=30,
    )
    contextual_id = controller._submit_model_task(
        "luna-mid",
        ModelTaskKind.PROACTIVE,
        {},
        context_bound=True,
        ttl_seconds=30,
    )
    try:
        controller.updateForegroundContext(
            ForegroundContext(501, process_name="wps.exe", title="Synthetic")
        )
        assert broker.get(contextual_id).state is ModelTaskState.CANCELLED
        assert broker.get(explicit_id).terminal is False
        assert broker.get(archive_id).terminal is False
    finally:
        controller.shutdown()


def test_generation_worker_start_failure_rolls_back_busy_and_broker(
    tmp_path, monkeypatch
) -> None:
    broker = ModelTaskBroker()
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=broker,
        foreground_provider=lambda: 0,
    )

    def fail_start(_thread) -> None:
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    try:
        assert controller._start_generation(None, force=True) is False
        assert controller._busy is False
        assert controller._model_task_ids == set()
        assert broker.status("luna-mid")["models"]["luna-mid"]["active"] is None
        assert broker.status("luna-mid")["models"]["luna-mid"]["queued"] == []
    finally:
        controller.shutdown()


def test_companion_controller_is_private_and_network_off_by_default(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    statuses: list[str] = []
    moved: list[dict] = []
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=moved.append,
    )
    try:
        status = controller.activityStatus
        assert controller._activity_enabled is True
        assert controller.preferences["frequency"] == "balanced"
        assert status["stateLabel"] == "尚未启动"
        assert status["lastContextLabel"] == "尚未发送"
        assert status["configuredEnabled"] is True
        assert status["observationModeShort"] == "感知中 · 不截图"
        assert status["observationModeLabel"] == "应用感知已开启 · 不截图"
        assert "截图必须另行明确授权" in status["observationModeDetail"]
        assert status["smartObservationEnabled"] is False
        assert status["onlineContentEnabled"] is False
        assert controller.content.fetcher is None
        assert not (tmp_path / "capture-staging").exists()

        controller.setFrequency("quiet", 45, 6)
        controller.setMix(70, 30, 45)
        controller.setCategoryWeight("吐槽", 0)
        assert controller.preferences["frequency"] == "quiet"
        assert controller.preferences["interestWeight"] == 70
        assert controller.preferences["categoryWeights"]["吐槽"] == 0

        policy = controller.setPolicy("game.exe", "signal")
        assert policy == {"application": "game.exe", "policy": "signal"}
        assert controller.activityStatus["applicationPolicies"]["game.exe"] == "signal"

        controller.addCustomSource("研究组", "https://example.org/feed.xml")
        custom = next(value for value in controller.sources if value["custom"])
        assert custom["label"] == "研究组"
        assert controller.content.fetcher is None
        controller.removeCustomSource(custom["id"])
        assert not any(value["custom"] for value in controller.sources)

        controller.addCustomSource("不安全", "http://127.0.0.1/feed")
        assert statuses[-1] == "不能订阅本机或局域网地址"
    finally:
        controller.shutdown()
    assert app is not None


def test_application_policy_list_is_title_free_reversible_and_fail_closed(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    secret_title = r"Embargoed result C:\Users\Alice\paper-draft.pdf"
    secret_window_class = "SecretPaperEditor"
    try:
        controller.activity.update_foreground(
            ForegroundContext(
                88,
                process_id=880,
                process_name="wps.exe",
                window_class=secret_window_class,
                title=secret_title,
                scene_label="unpublished-project",
            )
        )
        assert controller.applicationPolicies == []

        assert controller.setPolicy("wps.exe", "blocked") == {
            "application": "wps.exe",
            "policy": "blocked",
        }
        policies = controller.applicationPolicies
        assert policies == [
            {
                "application": "wps.exe",
                "policy": "blocked",
                "policyLabel": "静默",
                "safetyLocked": False,
            }
        ]
        serialized = repr(policies)
        assert secret_title not in serialized
        assert "paper-draft" not in serialized
        assert secret_window_class not in serialized
        assert "unpublished-project" not in serialized

        assert controller.setPolicy("wps.exe", "bubble")["policy"] == "bubble"
        assert controller.applicationPolicies[0]["policyLabel"] == "允许气泡"
        assert controller.setPolicy("wps.exe", "default") == {
            "application": "wps.exe",
            "policy": "default",
        }
        assert controller.applicationPolicies == []
        assert database.get_setting("activity_application_policies", None) == {}

        # Even a direct slot call cannot weaken an immutable password-manager
        # default.  The UI receives a locked, truthful effective policy.
        assert controller.setPolicy("Bitwarden.exe", "bubble") == {
            "application": "bitwarden.exe",
            "policy": "blocked",
        }
        locked = controller.applicationPolicies[0]
        assert locked["policy"] == "blocked"
        assert locked["safetyLocked"] is True
        assert controller.activity.guard.evaluate(
            ForegroundContext(89, process_name="bitwarden.exe")
        ).reason == "password-manager"
        controller.setPolicy("bitwarden.exe", "default")
        assert controller.applicationPolicies == []
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("policy", ["signal", "blocked"])
def test_tightening_current_application_policy_immediately_cancels_and_cleans(
    tmp_path, policy
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    context = ForegroundContext(
        88,
        process_id=880,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Paper",
    )
    controller.activity.update_foreground(context)
    inflight_path = tmp_path / "capture-staging" / "policy-inflight.png"
    retained_path = tmp_path / "capture-staging" / "policy-retained.png"
    inflight_path.parent.mkdir(parents=True, exist_ok=True)
    inflight_path.write_bytes(b"inflight")
    retained_path.write_bytes(b"retained")
    cancelled = threading.Event()
    controller._generation_serial = 9
    controller._active_generation_token = 9
    controller._generation_cancel_event = cancelled
    controller._active_generation_has_capture = True
    controller._busy = True
    controller._set_inflight_capture(
        StagedCapture(inflight_path, tmp_path / "capture-library")
    )
    controller._capture = StagedCapture(
        retained_path, tmp_path / "capture-library"
    )
    controller._bubble = {"id": "visible", "visible": True, "busy": False}
    controller._bubble_interacted = True
    try:
        assert controller.setPolicy("wps.exe", policy) == {
            "application": "wps.exe",
            "policy": policy,
        }
        assert cancelled.is_set()
        assert controller.busy is False
        assert controller.bubble == {}
        assert controller._capture is None
        assert controller._inflight_capture is None
        assert not inflight_path.exists()
        assert not retained_path.exists()
        assert controller.activityStatus["lastCaptureOutcome"] == "cancelled"
        assert controller.activityStatus["lastCaptureReason"] == "privacy-suppressed"
        allowed, _reason = controller._capture_policy(context)
        assert allowed is False
    finally:
        controller.shutdown()
    assert app is not None


def test_modality_probe_defers_while_generation_busy_and_blocks_generation(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    probe_calls: list[str] = []
    probe_entered = threading.Event()
    release_probe = threading.Event()

    def probe_modalities():
        probe_calls.append("probe")
        probe_entered.set()
        assert release_probe.wait(2.0)
        return {"checked": True, "imageModel": "luna", "error": ""}

    monkeypatch.setattr(controller.runtime, "probe_modalities", probe_modalities)
    controller._smart_observation = True
    try:
        controller._busy = True
        controller._probe_modalities()
        assert probe_calls == []
        assert controller._probe_busy is False
        assert controller._modality_retry_timer.isActive()
        assert controller.retrySmartObservationProbe() is False

        controller._busy = False
        controller._modality_retry_timer.stop()
        assert controller.retrySmartObservationProbe() is True
        assert probe_entered.wait(1.0)
        assert controller._probe_busy is True
        assert controller._start_generation(None, force=True) is False
        release_probe.set()
        assert _wait_for(app, lambda: not controller._probe_busy)
        assert probe_calls == ["probe"]
    finally:
        release_probe.set()
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize(
    "skip_reason",
    [
        "text-result-invalid",
        "text-visual-claim",
        "philosophy-quality-invalid",
        "source-metadata-repeated",
        "source-metadata-unavailable",
        "subjective-generation-failed",
        "subjective-model-unavailable",
        "image-anchor-unrelated",
        "image-generation-failed",
        "image-model-unavailable",
        "image-circuit-open",
    ],
)
def test_runtime_truth_skips_stay_quiet_without_spending_success_gate(
    tmp_path, skip_reason
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    controller._generation_serial = 11
    controller._active_generation_token = 11
    controller._generation_cancel_event = threading.Event()
    controller._busy = True
    before_observation = controller.activity._last_observation_at
    started_at = time.monotonic()
    try:
        controller._accept_generation(
            {
                "result": {
                    "summary": "",
                    "detail": "",
                    "model": "subscription-test",
                    "contextType": "application-signal",
                    "skip": True,
                    "skipReason": skip_reason,
                    "retryAfterSeconds": 1800,
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "文档工作",
                "force": False,
                "generationToken": 11,
            }
        )
        assert controller.busy is False
        assert controller.bubble == {}
        assert controller.activity._last_observation_at == before_observation
        assert controller._generation_attempt_not_before >= started_at + 1799
    finally:
        controller.shutdown()
    assert app is not None


def test_observation_mode_feedback_tracks_pause_authorization_and_fallback(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.setPaused(True)
        paused = controller.activityStatus
        assert paused["observationModeShort"] == "感知已暂停"
        assert "暂停期间不会截图" in paused["observationModeDetail"]

        controller.setPaused(False)
        monkeypatch.setattr(controller, "_probe_modalities", lambda: None)
        controller.authorizeSmartObservation(True)
        authorized = controller.activityStatus
        assert authorized["smartObservationEnabled"] is True
        assert authorized["observationModeShort"] == "屏幕观察已授权"
        assert "尝试一次非浏览器活动窗口截图" in authorized["observationModeDetail"]
        assert "浏览器像素观察在 v0.3.36 暂不开放" in authorized["observationModeDetail"]

        controller.runtime.modality_status.update({"checked": True, "imageModel": ""})
        fallback = controller.activityStatus
        assert fallback["observationModeShort"] == "感知中 · 不截图"
        assert fallback["observationModeLabel"] == "应用感知已开启 · 当前不截图"
        assert "只使用应用类别与自然停顿" in fallback["observationModeDetail"]

        controller.setActivityEnabled(False)
        disabled = controller.activityStatus
        assert disabled["configuredEnabled"] is False
        assert disabled["observationModeShort"] == "感知已关闭"
        assert disabled["observationModeDetail"] == "不会触发主动陪伴或截图"
        assert not (tmp_path / "capture-staging").exists()
    finally:
        controller.shutdown()
    assert app is not None


def test_activity_status_reports_persisted_emission_gate_instead_of_false_allowed(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.setFrequency("off", 0, 0)
        disabled = controller.activityStatus
        assert disabled["gate"]["reason"] == "frequency-off"
        assert disabled["stateLabel"] == "主动陪伴频率已关闭"
        assert "选择安静、平衡、活泼或自定义" in disabled["stateDetail"]

        controller.setFrequency("balanced", 25, 12)
        controller.snooze(60)
        snoozed = controller.activityStatus
        assert snoozed["gate"]["reason"] == "snoozed"
        assert snoozed["stateLabel"] == "主动陪伴已暂停一会儿"
        assert snoozed["gate"]["remainingSeconds"] > 3500
    finally:
        controller.shutdown()
    assert app is not None


def test_activity_status_combines_all_earliest_automatic_waits(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    monotonic_now = [1_000.0]
    controller.activity.clock = lambda: monotonic_now[0]
    controller.activity.start()
    controller.activity.update_foreground(
        ForegroundContext(62, process_name="wps.exe", title="Paper")
    )
    monotonic_now[0] = 1_040.0
    controller.activity._last_observation_at = 900.0
    controller.activity.consider_observation()
    controller.engine.gate._last_emitted = datetime.now(UTC) - timedelta(seconds=300)
    try:
        status = controller.activityStatus
        opportunity = status["automaticOpportunity"]
        assert opportunity["available"] is True
        assert opportunity["blockReason"] == ""
        assert opportunity["stableRemainingSeconds"] == 80.0
        assert opportunity["activityCooldownRemainingSeconds"] == 1360.0
        assert 1198.0 <= opportunity["gateRemainingSeconds"] <= 1200.0
        assert opportunity["waitSeconds"] == 1360.0

        controller.activity._last_observation_at = float("-inf")
        opportunity = controller.activityStatus["automaticOpportunity"]
        assert opportunity["activityCooldownRemainingSeconds"] == 0.0
        assert opportunity["waitSeconds"] == opportunity["gateRemainingSeconds"]

        controller.engine.gate._last_emitted = None
        opportunity = controller.activityStatus["automaticOpportunity"]
        assert opportunity["waitSeconds"] == 80.0

        controller.setPaused(True)
        paused = controller.activityStatus["automaticOpportunity"]
        assert paused["available"] is False
        assert paused["blockReason"] == "paused"

        controller.setPaused(False)
        controller.setFrequency("off", 0, 0)
        disabled = controller.activityStatus["automaticOpportunity"]
        assert disabled["available"] is False
        assert disabled["blockReason"] == "frequency-off"
    finally:
        controller.shutdown()
    assert app is not None


def test_source_categories_select_their_own_ranked_item_and_fail_visibly_without_one(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    now = datetime.now(UTC)
    controller._content_items = [
        ContentItem.create(
            category=ContentCategory.RESEARCH,
            title="Highly ranked paper",
            summary="AI biology",
            source="arXiv",
            published_at=now,
            url="https://example.test/research",
            topics=("AI",),
        ),
        ContentItem.create(
            category=ContentCategory.NEWS,
            title="A dated news item",
            summary="desktop",
            source="NASA",
            published_at=now,
            url="https://example.test/news",
            topics=("desktop",),
        ),
    ]
    try:
        news = controller._choose_content("AI biology", ContentCategory.NEWS)
        assert news is not None
        assert news.category is ContentCategory.NEWS

        controller._content_items = []
        assert controller.requestCategory("新闻") is False
        assert statuses
        assert "没有带来源和日期的可用内容" in statuses[-1]
        assert controller.busy is False
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize(
    ("skip_reason", "feedback_fragment"),
    [
        ("subjective-model-unavailable", "订阅模型暂不可用"),
        ("subjective-generation-failed", "模型没有生成出可用内容"),
        ("source-metadata-unavailable", "没有经过验证的来源与日期"),
        ("source-metadata-repeated", "来源近期已经展示过"),
    ],
)
def test_manual_content_free_skip_reports_accurate_state_without_bubble(
    tmp_path, skip_reason, feedback_fragment
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller._busy = True
        controller._accept_generation(
            {
                "result": {
                    "summary": "",
                    "detail": "",
                    "model": "subscription-test",
                    "contextType": "application-signal",
                    "skip": True,
                    "skipReason": skip_reason,
                    "degraded": True,
                    "retryAfterSeconds": 60,
                },
                "category": ContentCategory.LORE,
                "sceneLabel": "论文阅读",
                "force": True,
            }
        )
        status = controller.activityStatus
        assert controller.bubble == {}
        assert status["requestFeedbackKind"] == "quiet"
        assert feedback_fragment in status["requestFeedback"]
    finally:
        controller.shutdown()
    assert app is not None


def test_request_now_rejects_while_generation_is_busy(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
    )
    try:
        def unexpected_generation(*_args, **_kwargs) -> bool:
            raise AssertionError("busy requestNow must not start another generation")

        monkeypatch.setattr(controller, "_start_generation", unexpected_generation)
        controller._busy = True

        assert controller.requestNow() is False
        assert statuses
    finally:
        controller.shutdown()
    assert app is not None


def test_request_now_forces_generation_without_consulting_idle_gate(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        context = ForegroundContext(42, process_name="wps.exe", title="Paper")
        controller.activity.update_foreground(context)
        calls: list[tuple[ForegroundContext | None, bool]] = []

        def idle_gate_must_not_run(*_args, **_kwargs):
            raise AssertionError("requestNow must bypass the idle observation gate")

        def record_generation(
            current: ForegroundContext | None, *, force: bool
        ) -> bool:
            calls.append((current, force))
            return True

        monkeypatch.setattr(
            type(controller.activity), "consider_observation", idle_gate_must_not_run
        )
        monkeypatch.setattr(controller, "_start_generation", record_generation)

        assert controller.requestNow() is True
        assert calls == [(controller.activity.current_context, True)]
        assert statuses
    finally:
        controller.shutdown()
    assert app is not None


def test_manual_scene_generation_is_always_text_only(
    tmp_path, monkeypatch
) -> None:
    """The explicit scene action is useful, but is never a screenshot test."""

    app = QCoreApplication.instance() or QCoreApplication([])
    generated: list[dict[str, object]] = []

    class Runtime:
        modality_status = {"checked": True, "imageModel": "luna"}

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate(self, **kwargs):
            generated.append(dict(kwargs))
            return {
                "summary": "只使用应用类别回应。",
                "detail": "没有读取屏幕内容。",
                "model": "fake-subscription",
                "contextType": "application-signal",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    monkeypatch.setattr(companion_controller_module, "CompanionRuntime", Runtime)
    context = ForegroundContext(
        42,
        process_id=7,
        process_name="wps.exe",
        window_class="WpsFrame",
        title="Paper",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 42,
    )
    controller.reader = lambda _hwnd: context
    controller._smart_observation = True
    controller.activity.update_foreground(context)

    def capture_must_not_run(*_args, **_kwargs):
        raise AssertionError("manual scene generation must never capture pixels")

    monkeypatch.setattr(
        companion_controller_module, "capture_window_image", capture_must_not_run
    )
    try:
        assert controller._start_generation(context, force=True) is True
        assert _wait_for(app, lambda: bool(generated) and not controller.busy)
        assert generated[0]["image_path"] is None
        assert generated[0]["context_metadata"] == {
            "applicationCategory": "文档工作",
            "fullScreen": False,
            "inputScope": "application-category-only",
        }
    finally:
        controller.shutdown()
    assert app is not None


def test_request_now_respects_pause_and_sensitive_scene(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        def unexpected_generation(*_args, **_kwargs) -> bool:
            raise AssertionError("quiet or sensitive scenes must not start generation")

        monkeypatch.setattr(controller, "_start_generation", unexpected_generation)
        controller.activity.update_foreground(
            ForegroundContext(42, process_name="wps.exe", title="Paper")
        )
        controller.activity.set_paused(True)
        assert controller.requestNow() is False

        controller.activity.set_paused(False)
        controller.activity.update_foreground(
            ForegroundContext(43, process_name="1password.exe", title="Vault")
        )
        assert controller.requestNow() is False

        controller.activity.update_foreground(
            ForegroundContext(
                44,
                process_name="game.exe",
                title="Full screen game",
                full_screen=True,
                is_game=True,
            )
        )
        assert controller.requestNow() is False
        assert statuses
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize(
    ("live_context", "expected_reason"),
    [
        (
            ForegroundContext(91, process_name="1password.exe", title="Vault"),
            "password-manager",
        ),
        (
            ForegroundContext(92, process_name="teams.exe", title="Meeting"),
            "meeting",
        ),
        (
            ForegroundContext(
                93,
                process_name="game.exe",
                title="Full screen game",
                full_screen=True,
                is_game=True,
            ),
            "signals-only",
        ),
    ],
)
def test_manual_request_reconciles_stale_safe_cache_before_generation(
    tmp_path, monkeypatch, live_context, expected_reason
) -> None:
    """A stale WPS cache must never authorize a newly-sensitive foreground."""

    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    reads: list[int] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: live_context.hwnd,
    )
    controller.activity.update_foreground(
        ForegroundContext(80, process_name="wps.exe", title="Paper")
    )
    controller.reader = lambda hwnd: reads.append(int(hwnd)) or live_context
    monkeypatch.setattr(
        controller,
        "_start_generation",
        lambda *_args, **_kwargs: pytest.fail("sensitive foreground started generation"),
    )
    try:
        assert controller.requestNow() is False
        assert reads == [live_context.hwnd]
        assert controller.activityStatus["state"] == expected_reason
        assert controller.activityStatus["requestFeedbackKind"] == "quiet"
        assert "没有打扰你" in controller.activityStatus["requestFeedback"]
        assert statuses
    finally:
        controller.shutdown()
    assert app is not None


def test_manual_request_ignores_lilies_own_settings_window_and_keeps_external_context(
    tmp_path, monkeypatch
) -> None:
    """Clicking the settings control must not replace the last safe work context."""

    app = QCoreApplication.instance() or QCoreApplication([])
    own_context = ForegroundContext(
        102,
        process_name="LiliesInTheBox.exe",
        window_class="QtWindow",
        title="Lilies settings",
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: own_context.hwnd,
    )
    safe = ForegroundContext(101, process_name="wps.exe", title="Paper")
    controller.activity.update_foreground(safe)
    cached_safe = controller.activity.current_context
    controller.reader = lambda _hwnd: own_context
    calls: list[tuple[ForegroundContext | None, bool]] = []
    monkeypatch.setattr(
        controller,
        "_start_generation",
        lambda context, *, force: calls.append((context, force)) or True,
    )
    try:
        assert controller.requestNow() is True
        assert calls == [(cached_safe, True)]
        assert controller.activity.current_context is cached_safe
    finally:
        controller.shutdown()
    assert app is not None


def test_generation_result_rechecks_live_sensitive_foreground_before_bubble(
    tmp_path,
) -> None:
    """A safe request cannot finish into a password window and revive a bubble."""

    app = QCoreApplication.instance() or QCoreApplication([])
    live = ForegroundContext(112, process_name="1password.exe", title="Vault")
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: live.hwnd,
    )
    controller.activity.update_foreground(
        ForegroundContext(111, process_name="wps.exe", title="Paper")
    )
    controller.reader = lambda _hwnd: live
    controller._busy = True
    try:
        controller._accept_generation(
            {
                "result": {
                    "summary": "这句话不应出现。",
                    "detail": "synthetic",
                    "model": "local-safe-fallback",
                    "contextType": "application-signal",
                },
                "category": ContentCategory.LORE,
                "sceneLabel": "文档工作",
                "force": True,
            }
        )
        assert controller.bubble == {}
        assert controller.activityStatus["state"] == "password-manager"
        assert controller.activityStatus["requestFeedbackKind"] == "quiet"
        assert "没有弹出气泡" in controller.activityStatus["requestFeedback"]
    finally:
        controller.shutdown()
    assert app is not None


def test_generation_completion_cannot_revive_bubble_after_pause(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.activity.update_foreground(
            ForegroundContext(42, process_name="wps.exe", title="Paper")
        )
        controller.activity.set_paused(True)
        controller._busy = True
        controller._accept_generation(
            {
                "result": {"summary": "should stay hidden", "detail": "hidden"},
                "category": ContentCategory.LORE,
                "sceneLabel": "Paper",
                "force": True,
            }
        )
        assert controller.busy is False
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


def test_missed_foreground_event_is_reconciled_and_emits_speech_bubble(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    contexts = {
        10: ForegroundContext(
            10,
            process_id=1,
            process_name="LiliesInTheBox.exe",
            title="Settings",
        ),
        20: ForegroundContext(
            20,
            process_id=2,
            process_name="wps.exe",
            title="Paper C:\\Users\\Alice\\private.pdf",
            scene_label="论文阅读",
        ),
    }
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 20,
    )
    controller.runtime.shutdown()

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def generate(self, **_kwargs):
            return {
                "summary": "你在这篇论文前停了一会儿。",
                "detail": "我没有读取正文，只知道当前应用适合阅读。",
                "model": "subscription-reconcile-test",
                "contextType": "application-signal",
            }

        def abort_model(self, _model_id: str) -> None:
            pass

        def shutdown(self) -> None:
            pass

    controller.runtime = Runtime()
    controller.reader = lambda hwnd: contexts[int(hwnd)]
    controller.activity.idle_provider = _Idle(10.0)
    controller.activity.stable_seconds = 0.0
    controller.activity.cooldown_seconds = 0.0
    controller.activity.start()
    controller.activity.update_foreground(contexts[10])
    try:
        controller._consider()
        assert _wait_for(app, lambda: bool(controller.bubble.get("visible")))
        assert controller.activity.current_context is not None
        assert controller.activity.current_context.hwnd == 20
        assert controller.activity.current_context.process_name == "wps.exe"
        assert controller.bubble["summary"] == "你在这篇论文前停了一会儿。"
        assert controller.bubble["contextType"] == "application-signal"
        assert "private.pdf" not in controller.activity.context_identity
    finally:
        controller.shutdown()


def test_generation_from_old_foreground_cannot_appear_in_new_context(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.activity.update_foreground(
            ForegroundContext(30, process_id=3, process_name="wps.exe")
        )
        old_identity = controller.activity.context_identity
        controller.activity.update_foreground(
            ForegroundContext(40, process_id=4, process_name="browser.exe")
        )
        controller._busy = True
        controller._accept_generation(
            {
                "result": {"summary": "old", "detail": "old"},
                "category": ContentCategory.LORE,
                "sceneLabel": "旧窗口",
                "force": False,
                "contextIdentity": old_identity,
            }
        )
        assert controller.busy is False
        assert controller.bubble == {}
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("replacement_title", ["Another safe paper", ""])
@pytest.mark.parametrize("forced", [False, True], ids=["automatic", "manual"])
def test_capture_result_is_discarded_when_same_hwnd_title_changes_before_accept(
    tmp_path, replacement_title, forced
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    initial = ForegroundContext(
        77,
        process_id=9,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Original paper",
    )
    replacement = ForegroundContext(
        77,
        process_id=9,
        process_name="wps.exe",
        window_class="WPSWindow",
        title=replacement_title,
    )
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 77,
    )
    controller.reader = lambda _hwnd: replacement
    controller.activity.update_foreground(initial)
    controller._smart_observation = True
    controller._generation_serial = 12
    controller._active_generation_token = 12
    controller._generation_cancel_event = threading.Event()
    controller._active_generation_model_id = LUNA_MODEL
    capture_diagnostic_token = controller._begin_capture_diagnostic()
    controller._record_capture_outcome(
        "used",
        "image-model-completed",
        attempt_token=capture_diagnostic_token,
        model=LUNA_MODEL,
        evidence_confidence="medium",
    )
    controller._active_generation_has_capture = True
    controller._active_generation_capture_diagnostic_token = (
        capture_diagnostic_token
    )
    controller._busy = True
    capture_path = tmp_path / "capture-staging" / "late-result.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(b"png")
    capture = StagedCapture(capture_path, tmp_path / "capture-library")
    try:
        controller._accept_generation(
            {
                "result": {
                    "summary": "belongs to the original paper",
                    "detail": "must never appear over the replacement",
                    "model": LUNA_MODEL,
                    "contextType": "active-window-image",
                },
                "category": ContentCategory.SCIENCE,
                "sceneLabel": "论文阅读",
                "capture": capture,
                "force": forced,
                "contextIdentity": controller.activity.context_identity,
                "generationToken": 12,
                "captureDiagnosticToken": capture_diagnostic_token,
            }
        )
        assert controller.bubble == {}
        assert controller.busy is False
        assert not capture_path.exists()
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePixelsUsed"] is True
        assert status["lastCaptureReason"] == "image-model-completed"
        assert status["lastCapturePresentationOutcome"] == "quiet"
        assert (
            status["lastCapturePresentationReason"]
            == "capture-context-changed-before-presentation"
        )
    finally:
        controller.shutdown()
    assert app is not None


def test_sensitive_and_fullscreen_contexts_cancel_and_hide_immediately(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    cancellations: list[str] = []
    monkeypatch.setattr(controller, "_cancel_model_tasks", cancellations.append)
    try:
        controller._busy = True
        controller._bubble = {"id": "visible", "visible": True}
        controller.updateForegroundContext(
            ForegroundContext(50, process_name="1password.exe", title="Vault")
        )
        assert controller.bubble == {}
        assert controller.activityStatus["state"] == "password-manager"
        assert controller.activityStatus["stateLabel"] == "密码应用，保持安静"
        assert any("password-manager" in value for value in cancellations)

        controller._bubble = {"id": "late", "visible": True}
        controller.updateForegroundContext(
            ForegroundContext(
                60,
                process_name="game.exe",
                full_screen=True,
                is_game=True,
            )
        )
        assert controller.bubble == {}
        assert controller.activityStatus["state"] == "signals-only"
        assert controller.activityStatus["stateLabel"] == "当前应用只使用场景信号"
        assert any("signals-only" in value for value in cancellations)

        controller.activity.mark_observation_sent("application-signal")
        assert controller.activityStatus["lastContextLabel"] == "应用级信号（未截图）"
    finally:
        controller._busy = False
        controller.shutdown()
    assert app is not None


def test_safe_title_change_does_not_unlock_an_inflight_explicit_reply(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        model_broker=ModelTaskBroker(),
    )
    controller.runtime.shutdown()
    entered = threading.Event()
    release = threading.Event()

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self) -> None:
            self.reply_calls = 0

        def reply(self, _bubble, _dialogue, _text):
            self.reply_calls += 1
            entered.set()
            release.wait(2)
            return "只应有这一条回答。"

        def abort_model(self, _model_id):
            pass

        def shutdown(self):
            release.set()

    runtime = Runtime()
    controller.runtime = runtime
    first = ForegroundContext(
        88,
        process_id=21,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Paper A",
    )
    second = ForegroundContext(
        88,
        process_id=21,
        process_name="wps.exe",
        window_class="WPSWindow",
        title="Paper B",
    )
    controller.activity.update_foreground(first)
    bubble = controller.engine.emit(
        category=ContentCategory.LORE,
        summary="先说一句。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": False}
    try:
        controller.reply(bubble.id, "第一条回复")
        assert entered.wait(1)
        controller.updateForegroundContext(second)
        assert controller.busy is True
        assert controller.bubble["busy"] is True

        controller.reply(bubble.id, "不应排入第二条")
        assert runtime.reply_calls == 1

        release.set()
        assert _wait_for(app, lambda: not controller.busy, timeout=2.0)
        assert runtime.reply_calls == 1
    finally:
        release.set()
        controller.shutdown()
    assert app is not None


def _seed_visible_bubble(
    controller: CompanionController, *, lifetime_seconds: float = 30.0
) -> None:
    now = datetime.now(UTC)
    controller._bubble = {
        "id": f"test-bubble-{time.monotonic_ns()}",
        "visible": True,
        "summary": "synthetic",
        "createdAt": now.isoformat(),
        "expiresAt": (now + timedelta(seconds=lifetime_seconds)).isoformat(),
        "busy": False,
    }
    controller._schedule_bubble_expiry()
    controller.bubbleChanged.emit()


def _seed_pending_capture_bubble(
    controller: CompanionController, tmp_path: Path
) -> tuple[int, str, StagedCapture]:
    _seed_visible_bubble(controller)
    bubble_id = str(controller.bubble["id"])
    token = controller._begin_capture_diagnostic()
    controller._record_capture_outcome(
        "used",
        "image-model-completed",
        attempt_token=token,
        model=LUNA_MODEL,
        evidence_confidence="medium",
    )
    controller._record_capture_presentation(
        "pending",
        "awaiting-presentation",
        attempt_token=token,
        session_id="pending-capture-session",
        bubble_id=bubble_id,
    )
    capture_path = tmp_path / "capture-staging" / f"{bubble_id}.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(b"synthetic-capture")
    capture = StagedCapture(capture_path, tmp_path / "capture-library")
    capture.retain_in_memory()
    controller._capture = capture
    controller._bubble.update(
        {
            "contextType": "active-window-image",
            "hasCapture": True,
            "deliveryState": "waiting-present-ack",
        }
    )
    controller._bubble_capture_diagnostic_token = token
    controller._presentation_ack_pending = True
    controller._delivery_record = {
        "schemaVersion": 2,
        "sessionId": "pending-capture-session",
        "bubbleId": bubble_id,
        "state": "waiting-present-ack",
        "reason": "generated",
        "generatedAt": datetime.now(UTC).isoformat(),
        "presentedAt": "",
        "expiresAt": "",
        "unread": False,
        "unreadSince": "",
        "redeliveryCount": 0,
        "lastRedeliveryAt": "",
    }
    return token, bubble_id, capture


def test_capture_bubble_privacy_pause_safe_resume_then_ack(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        _token, bubble_id, _capture = _seed_pending_capture_bubble(
            controller, tmp_path
        )
        controller.setPresentationSuppressed(True)
        status = controller.activityStatus
        assert controller.bubble["deliveryState"] == "suppressed"
        assert status["lastCapturePresentationOutcome"] == "pending"
        assert status["lastCapturePresentationReason"] == "privacy-suppressed"

        controller.setPresentationSuppressed(False)
        status = controller.activityStatus
        assert controller.bubble["deliveryState"] == "waiting-present-ack"
        assert status["lastCapturePresentationOutcome"] == "pending"
        assert status["lastCapturePresentationReason"] == "awaiting-presentation"
        assert controller.ackPresented(bubble_id, True, True, 1) is True
        assert controller.activityStatus["lastCapturePresentationOutcome"] == "shown"

        controller.dismissExplicit()
        assert controller.bubble == {}
        assert controller.activityStatus["lastCapturePresentationOutcome"] == "shown"
    finally:
        controller.shutdown()
    assert app is not None


def test_capture_bubble_unsafe_resume_becomes_unread_and_releases_pixels(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 99,
    )
    try:
        _token, _bubble_id, capture = _seed_pending_capture_bubble(
            controller, tmp_path
        )
        controller.setPresentationSuppressed(True)
        controller.reader = lambda _hwnd: ForegroundContext(
            99, process_name="1password.exe", title="Vault"
        )
        controller.setPresentationSuppressed(False)
        status = controller.activityStatus
        assert controller.bubble == {}
        assert controller.deliveryStatus["state"] == "unread"
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePresentationOutcome"] == "unread"
        assert status["lastCapturePresentationReason"] == "unsafe-resume"
        assert capture._image_bytes is None
    finally:
        controller.shutdown()
    assert app is not None


def test_presentation_suppression_blocks_start_and_discards_late_generation(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    context = ForegroundContext(71, process_id=7, process_name="wps.exe")
    controller.activity.update_foreground(context)
    before_count = int(controller.engine.gate.snapshot()["countToday"])
    try:
        controller.setPresentationSuppressed(False)
        controller._busy = True
        controller._active_generation_token = 41
        controller.setPresentationSuppressed(True)

        assert controller.presentationSuppressed is True
        assert controller.busy is False
        assert controller._active_generation_token == 0
        assert controller._start_generation(context, force=False) is False
        assert controller.requestNow() is False

        # Even if a non-cooperative worker finishes after suppression has
        # lifted, its invalidated token cannot create, persist, or count a
        # bubble.
        controller.setPresentationSuppressed(False)
        controller._accept_generation(
            {
                "generationToken": 41,
                "result": {"summary": "late", "detail": "late"},
                "category": ContentCategory.LORE,
                "sceneLabel": "synthetic",
                "force": False,
            }
        )
        assert controller.bubble == {}
        assert int(controller.engine.gate.snapshot()["countToday"]) == before_count
        with database.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM proactive_sessions").fetchone()[0] == 0

        starts: list[tuple[ForegroundContext | None, bool]] = []
        monkeypatch.setattr(
            controller,
            "_start_generation",
            lambda current, *, force: starts.append((current, force)) or True,
        )
        assert controller.requestNow() is True
        assert len(starts) == 1
        assert starts[0][1] is True
        assert starts[0][0] is not None
        assert starts[0][0].hwnd == context.hwnd
        assert starts[0][0].process_name == context.process_name
    finally:
        controller.shutdown()
    assert app is not None


def test_legacy_privacy_dismiss_preserves_bubble_across_suppression(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.setPresentationSuppressed(False)
        _seed_visible_bubble(controller)
        bubble_id = controller.bubble["id"]

        # Mirrors Backend's current order: ambient dismiss first, QML's
        # dockSuppressed binding immediately afterwards.
        controller.dismiss()
        assert controller.bubble["id"] == bubble_id
        controller.setPresentationSuppressed(True)
        app.processEvents()
        app.processEvents()
        assert controller.bubble["id"] == bubble_id

        controller.setPresentationSuppressed(False)
        assert controller.bubble["id"] == bubble_id
        assert controller.bubble["deliveryState"] == "waiting-present-ack"
        assert controller.bubble["expiresAt"] == ""
        assert controller.deliveryStatus["ackPending"] is True
        assert controller.ackPresented(bubble_id, True, True, 1) is True
        assert controller.deliveryStatus["state"] == "presented"

        # Hidden time is not presentation time.  Resume requires a fresh
        # native exposure ACK, and only that ACK restarts the remaining TTL.
        controller.setPresentationSuppressed(True)
        controller._bubble_ttl_remaining_seconds = 0.08
        assert controller.bubble["expiresAt"] == ""
        _wait_for(app, lambda: False, timeout=0.16)
        assert controller.bubble["id"] == bubble_id
        controller.setPresentationSuppressed(False)
        assert controller.bubble["deliveryState"] == "waiting-present-ack"
        _wait_for(app, lambda: False, timeout=0.16)
        assert controller.bubble["id"] == bubble_id
        assert controller.ackPresented(bubble_id, True, True, 2) is True
        assert _wait_for(app, lambda: controller.bubble == {}, timeout=0.8)
        assert controller.deliveryStatus["state"] == "unread"
        assert controller.deliveryStatus["unreadCount"] == 1
    finally:
        controller.shutdown()
    assert app is not None


def test_explicit_close_snooze_and_mute_clear_immediately(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.setPresentationSuppressed(False)

        _seed_visible_bubble(controller)
        controller.dismissExplicit()
        assert controller.bubble == {}
        assert controller.deliveryStatus["unreadCount"] == 0

        _seed_visible_bubble(controller)
        controller.snooze(60)
        assert controller.bubble == {}
        assert controller.deliveryStatus["unreadCount"] == 0

        _seed_visible_bubble(controller)
        controller.activity.update_foreground(
            ForegroundContext(81, process_id=8, process_name="wps.exe")
        )
        controller.muteCurrentApp()
        assert controller.bubble == {}
        assert controller.deliveryStatus["state"] == "dismissed"
        assert controller.deliveryStatus["unreadCount"] == 0
    finally:
        controller.shutdown()
    assert app is not None


@pytest.mark.parametrize("action", ["dismiss", "snooze"])
def test_explicit_bubble_actions_invalidate_late_replacement_generation(
    tmp_path, action: str
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    bubble = controller.engine.emit(
        category=ContentCategory.PHILOSOPHY,
        summary="原来的问题仍然留在这里。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": False}
    controller._active_generation_token = 73
    controller._busy = True
    try:
        if action == "dismiss":
            controller.dismissExplicit()
        else:
            controller.snooze(60)

        assert controller._active_generation_token == 0
        assert controller.busy is False
        assert controller.bubble == {}

        controller._accept_generation(
            {
                "generationToken": 73,
                "result": {
                    "summary": "这是一条已经失效的迟到结果。",
                    "detail": "它不应该重新弹出。",
                    "model": "late-test",
                    "contextType": "application-signal",
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "文档工作",
                "force": True,
            }
        )
        assert controller.bubble == {}
        assert controller.busy is False
    finally:
        controller.shutdown()
    assert app is not None


def test_another_preserves_the_visible_bubble_category(tmp_path, monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    bubble = controller.engine.emit(
        category=ContentCategory.PHILOSOPHY,
        summary="先从这个问题继续想。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": False}
    requested: list[ContentCategory | None] = []

    def start() -> bool:
        requested.append(controller._requested_category)
        controller._requested_category = None
        return True

    monkeypatch.setattr(controller, "_start_manual_generation", start)
    try:
        controller.another(bubble.id)
        assert requested == [ContentCategory.PHILOSOPHY]
        assert controller.deliveryStatus["unreadCount"] == 0
    finally:
        controller.shutdown()
    assert app is not None


def test_category_choice_counts_as_interaction_when_replacement_cannot_start(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    bubble = controller.engine.emit(
        category=ContentCategory.SCIENCE,
        summary="一条等待选择的气泡。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": False}
    monkeypatch.setattr(controller, "_start_manual_generation", lambda: False)
    try:
        assert controller.requestCategory(ContentCategory.PHILOSOPHY.value) is False
        assert controller.deliveryStatus["state"] == "interacted"
        assert controller.deliveryStatus["unreadCount"] == 0
    finally:
        controller.shutdown()
    assert app is not None


def test_reply_empty_result_preserves_original_bubble_and_clears_busy(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    statuses: list[str] = []
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
    )
    bubble = controller.engine.emit(
        category=ContentCategory.PHILOSOPHY,
        summary="原来的哲思不能被失败回复覆盖。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": True}
    controller._busy = True
    try:
        controller._accept_reply({"bubbleId": bubble.id, "answer": "   "})
        assert controller.busy is False
        assert controller.bubble["busy"] is False
        assert controller.bubble["summary"] == bubble.summary
        assert controller.bubble["error"] == "这次回复没有生成成功，可以稍后再试。"
        assert statuses and "空内容" in statuses[-1]
    finally:
        controller.shutdown()
    assert app is not None


def test_reply_success_is_labelled_as_dialogue_and_source_as_context(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    source = BubbleSource(
        "Example Journal", "https://example.test/article", datetime.now(UTC)
    )
    bubble = controller.engine.emit(
        category=ContentCategory.SCIENCE,
        summary="一条有来源的起始内容。",
        source=source,
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "visible": True, "busy": True}
    controller.engine.reply(bubble.id, "你为什么这样想？")
    controller._busy = True
    try:
        controller._accept_reply(
            {"bubbleId": bubble.id, "answer": "因为边界本身也会改变观察。"}
        )
        assert controller.busy is False
        assert controller.bubble["category"] == "继续对话"
        assert controller.bubble["sceneLabel"] == "短会话"
        assert controller.bubble["sourceRole"] == "context"
        assert controller.bubble["source"]["name"] == "Example Journal"
        assert controller.bubble["error"] == ""
    finally:
        controller.shutdown()
    assert app is not None


def test_bubble_ttl_runs_when_activity_observation_is_disabled(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
    )
    try:
        controller.setPresentationSuppressed(False)
        _seed_visible_bubble(controller, lifetime_seconds=0.08)
        controller.setActivityEnabled(False)

        assert controller._timer.isActive() is False
        assert controller._bubble_expiry_timer.isActive() is True
        assert _wait_for(app, lambda: controller.bubble == {}, timeout=1.0)
        assert controller.deliveryStatus["state"] == "unread"
        assert controller.deliveryStatus["unreadCount"] == 1
    finally:
        controller.shutdown()
    assert app is not None


def test_companion_bubble_qml_syncs_suppression_and_uses_explicit_close() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "qml" / "CompanionBubble.qml"
    ).read_text(encoding="utf-8")

    assert "Component.onCompleted: syncPresentationSuppression()" in source
    assert "onSuppressedChanged: syncPresentationSuppression()" in source
    assert "controller.setPresentationSuppressed(Boolean(suppressed))" in source
    assert source.count("bubbleWindow.dismissExplicitly()") == 2


def _accept_delivery_test_bubble(
    controller: CompanionController, *, summary: str = "synthetic delivery prose"
) -> str:
    controller._busy = True
    controller._accept_generation(
        {
            "result": {
                "summary": summary,
                "detail": summary + " detail",
                "model": "local-safe-fallback",
                "contextType": "application-signal",
            },
            "category": ContentCategory.LORE,
            "sceneLabel": "synthetic scene",
            "force": True,
        }
    )
    return str(controller.bubble["id"])


def test_delivery_waits_for_ack_and_memory_failure_cannot_swallow_bubble(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    statuses: list[str] = []
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    notifications: list[dict[str, object]] = []
    controller.bubbleChanged.connect(
        lambda: notifications.append(controller.bubble)
    )

    def fail_memory(_bubble) -> None:
        raise OSError("private memory failure detail")

    monkeypatch.setattr(controller, "_remember_observation_if_needed", fail_memory)
    try:
        bubble_id = _accept_delivery_test_bubble(
            controller, summary="prose-must-not-enter-delivery-status"
        )
        assert notifications
        assert notifications[0]["id"] == bubble_id
        assert controller.bubble["expiresAt"] == ""
        assert controller.bubble["deliveryState"] == "waiting-present-ack"
        assert controller.deliveryStatus["ackPending"] is True
        assert controller._bubble_expiry_timer.isActive() is False
        assert statuses and "OSError" in statuses[-1]
        assert "private memory failure detail" not in statuses[-1]

        # A declarative visibility request is insufficient; actual native
        # exposure starts the presentation lifetime.
        assert controller.ackPresented(bubble_id, True, False, 1) is False
        assert controller.bubble["expiresAt"] == ""
        assert controller.ackPresented(bubble_id, True, True, 2) is True
        assert controller.deliveryStatus["state"] == "presented"
        assert controller.deliveryStatus["ackPending"] is False
        assert controller._bubble_expiry_timer.isActive() is True

        journal = database.get_setting("companion_delivery_status", {})
        assert set(journal) == {
            "schemaVersion",
            "sessionId",
            "bubbleId",
            "state",
            "reason",
            "generatedAt",
            "presentedAt",
            "expiresAt",
            "unread",
            "unreadSince",
            "redeliveryCount",
            "lastRedeliveryAt",
        }
        serialized = json.dumps(journal, ensure_ascii=False)
        assert "prose-must-not-enter-delivery-status" not in serialized
        assert "detail" not in serialized.casefold()
    finally:
        controller.shutdown()
    assert app is not None


def test_missing_ack_becomes_unread_and_explicit_reopen_marks_read_on_interaction(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        bubble_id = _accept_delivery_test_bubble(controller)
        controller._presentation_ack_timed_out()
        assert controller.bubble == {}
        assert controller.deliveryStatus["state"] == "unread"
        assert controller.deliveryStatus["unreadCount"] == 1

        # Later global suppression edges must not turn an unread item back
        # into an unsolicited bubble; only the explicit reopen action may.
        controller.setPresentationSuppressed(True)
        controller.setPresentationSuppressed(False)
        assert controller.bubble == {}
        assert controller.deliveryStatus["state"] == "unread"

        assert controller.reopenUnread() is True
        assert controller.bubble["id"] == bubble_id
        assert controller.deliveryStatus["state"] == "waiting-present-ack"
        # Merely re-presenting does not claim that the user read it.
        assert controller.ackPresented(bubble_id, True, True, 1) is True
        assert controller.deliveryStatus["unreadCount"] == 1
        controller.acknowledgeInteraction(bubble_id, "detail")
        assert controller.deliveryStatus["state"] == "interacted"
        assert controller.deliveryStatus["unreadCount"] == 0
    finally:
        controller.shutdown()
    assert app is not None


def test_unfinished_delivery_survives_restart_as_content_free_unread(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    first = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    bubble_id = _accept_delivery_test_bubble(
        first, summary="persisted only in proactive session"
    )
    first.shutdown()

    restored = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert restored.bubble == {}
        assert restored.deliveryStatus["state"] == "unread"
        assert restored.deliveryStatus["reason"] == "process-restarted-before-read"
        assert restored.deliveryStatus["unreadCount"] == 1
        assert restored.reopenUnread() is True
        assert restored.bubble["id"] == bubble_id

        journal = database.get_setting("companion_delivery_status", {})
        serialized = json.dumps(journal, ensure_ascii=False)
        assert "persisted only in proactive session" not in serialized
        assert "summary" not in serialized.casefold()
    finally:
        restored.shutdown()
    assert app is not None


def test_category_weights_drive_smooth_deterministic_rotation(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        mapping = controller.preferences_model.to_mapping()
        mapping["categoryWeights"] = {
            category.value: 0 for category in ContentCategory
        }
        mapping["categoryWeights"].update({"科普": 25, "哲思": 75})
        controller._update_preferences(mapping)
        chosen = [controller._choose_category("论文阅读")[0] for _ in range(40)]
        assert chosen.count(ContentCategory.SCIENCE) == 10
        assert chosen.count(ContentCategory.PHILOSOPHY) == 30
        assert set(chosen) == {
            ContentCategory.SCIENCE,
            ContentCategory.PHILOSOPHY,
        }
        # Smooth rotation must not emit the three-weight category in one
        # monolithic block.
        longest_philosophy_run = max(
            len(run)
            for run in "".join(
                "P" if item is ContentCategory.PHILOSOPHY else "S"
                for item in chosen
            ).split("S")
        )
        assert longest_philosophy_run <= 3
    finally:
        controller.shutdown()
    assert app is not None


def test_custom_frequency_draft_survives_presets_and_restart(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    first = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        first.setFrequency("custom", 7, 2)
        assert first.preferences["minimumMinutes"] == 7
        assert first.preferences["dailyLimit"] == 2
        first.setFrequency("quiet", 45, 6)
        assert first.preferences["frequency"] == "quiet"
        assert first.preferences["customMinimumMinutes"] == 7
        assert first.preferences["customDailyLimit"] == 2
    finally:
        first.shutdown()

    restored = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert restored.preferences["frequency"] == "quiet"
        assert restored.preferences["customMinimumMinutes"] == 7
        assert restored.preferences["customDailyLimit"] == 2
        restored.setFrequency("custom", 1, 99)
        assert restored.preferences["minimumMinutes"] == 5
        assert restored.preferences["dailyLimit"] == 50
    finally:
        restored.shutdown()

    assert database.get_setting("companion_custom_frequency", {}) == {
        "minimumMinutes": 5,
        "dailyLimit": 50,
    }
    clamped = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert clamped.preferences["frequency"] == "custom"
        assert clamped.preferences["minimumMinutes"] == 5
        assert clamped.preferences["dailyLimit"] == 50
        assert clamped.preferences["customMinimumMinutes"] == 5
        assert clamped.preferences["customDailyLimit"] == 50
    finally:
        clamped.shutdown()
    assert app is not None


def test_recent_summary_persists_and_duplicate_rejection_has_independent_backoff(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    old_summary = "右侧那条细线几乎没有声势，却把等待变成了可以看见的距离。"
    database.save_proactive_session(
        session_id="old-session",
        bubble={
            "id": "old-bubble",
            "category": "哲思",
            "summary": old_summary,
            "detail": "",
            "sceneLabel": "论文阅读",
            "createdAt": datetime.now(UTC).isoformat(),
        },
    )
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert list(controller._recent_summary_snippets) == [old_summary]
        before_count = controller.engine.gate.state(datetime.now(UTC))["countToday"]
        controller._accept_generation(
            {
                "result": {
                    "summary": "右侧的那条细线没有声势，却让等待成了看得见的距离。",
                    "detail": "近似改写也不应再次出现。",
                    "model": "test",
                    "contextType": "application-signal",
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "论文阅读",
                "force": False,
                "generationToken": 0,
            }
        )
        assert controller.bubble == {}
        assert controller.activityStatus["lastContextType"] == "duplicate-suppressed"
        assert controller._generation_attempt_not_before > time.monotonic()
        assert controller._start_generation(None, force=False) is False
        assert controller.engine.gate.state(datetime.now(UTC))["countToday"] == before_count
        assert database.recent_proactive_summaries(12) == [old_summary]
    finally:
        controller.shutdown()
    assert app is not None


def test_detail_only_near_duplicate_is_suppressed_after_restart(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database_path = tmp_path / "lilies.db"
    old_summary = "A narrow progress line divides the window."
    new_summary = "The unnamed weather waits inside a paper box."
    repeated_detail = " ".join(
        [
            "The progress indicator turns unfinished work into a measurable distance",
            "while the person remains still and the interface divides time into visible steps",
            "leaving the same question about whether movement belongs to the number or its observer",
        ]
        * 3
    )
    assert not summaries_are_near_duplicates(old_summary, new_summary)
    assert summaries_are_near_duplicates(
        f"{old_summary}\n{repeated_detail}",
        f"{new_summary}\n{repeated_detail}",
    )

    Database(database_path).save_proactive_session(
        session_id="old-session",
        bubble={
            "id": "old-bubble",
            "category": "philosophy",
            "summary": old_summary,
            "detail": repeated_detail,
            "createdAt": datetime.now(UTC).isoformat(),
        },
    )
    database = Database(database_path)
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert list(controller._recent_summary_snippets) == [old_summary]
        assert list(controller._recent_prose_snippets) == [
            f"{old_summary}\n{repeated_detail}"
        ]
        controller._accept_generation(
            {
                "result": {
                    "summary": new_summary,
                    "detail": repeated_detail,
                    "model": "test",
                    "contextType": "application-signal",
                },
                "category": ContentCategory.PHILOSOPHY,
                "sceneLabel": "test scene",
                "force": False,
                "generationToken": 0,
            }
        )

        assert controller.bubble == {}
        assert controller.activityStatus["lastContextType"] == "duplicate-suppressed"
        assert database.recent_proactive_summaries(12) == [old_summary]
        assert database.recent_proactive_prose(12) == [
            {"summary": old_summary, "detail": repeated_detail}
        ]
    finally:
        controller.shutdown()
    assert app is not None


def test_duplicate_retry_reaches_runtime_with_one_variation_and_force_does_not_count_daily(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    generated: list[dict[str, object]] = []

    def generate(**kwargs):
        generated.append(dict(kwargs))
        return {
            "summary": "第二次从另一种尺度看见了边界。",
            "detail": "这是一个新的观察角度。",
            "model": "variation-test",
            "contextType": "application-signal",
        }

    controller.runtime.generate = generate
    before_count = controller.engine.gate.state(datetime.now(UTC))["countToday"]
    try:
        assert controller._start_generation(
            None, force=True, duplicate_retry=1
        ) is True
        assert _wait_for(app, lambda: bool(generated) and not controller.busy)
        assert generated[0]["variation_nonce"] == 1
        assert controller.engine.gate.state(datetime.now(UTC))["countToday"] == before_count
    finally:
        controller.shutdown()
    assert app is not None


def test_generation_forwards_saved_interest_hints_and_mix_to_runtime(tmp_path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    generated: list[dict[str, object]] = []

    def generate(**kwargs):
        generated.append(dict(kwargs))
        return {
            "summary": "同一个问题再次回来，是答案没变，还是提问的人已经不同？",
            "detail": "问题保持原样，提问者却会随着时间改变。",
            "model": "interest-forwarding-test",
            "contextType": "application-signal",
        }

    controller.runtime.generate = generate
    controller.setInterests("生物学, 认识论")
    controller.setMix(70, 30, 30)
    controller._requested_category = ContentCategory.PHILOSOPHY
    try:
        assert controller._start_generation(None, force=True) is True
        assert _wait_for(app, lambda: bool(generated) and not controller.busy)
        assert generated[0]["interest_hints"] == ["生物学", "认识论"]
        assert generated[0]["interest_weight"] == 70
        assert generated[0]["scene_weight"] == 30
    finally:
        controller.shutdown()
    assert app is not None


def test_manual_duplicate_schedules_exactly_one_retry_without_daily_charge(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    old_summary = "细线没有催促，只把等待变成了看得见的距离。"
    controller._recent_summary_snippets.append(old_summary)
    retry_calls: list[tuple[bool, int]] = []

    def start_retry(_context, *, force: bool, duplicate_retry: int = 0) -> bool:
        retry_calls.append((bool(force), int(duplicate_retry)))
        controller._requested_category = None
        return True

    controller._start_generation = start_retry
    before_count = controller.engine.gate.state(datetime.now(UTC))["countToday"]
    payload = {
        "result": {
            "summary": old_summary,
            "detail": "近似改写应触发一次重试。",
            "model": "test",
            "contextType": "application-signal",
        },
        "category": ContentCategory.PHILOSOPHY,
        "sceneLabel": "论文阅读",
        "force": True,
        "generationToken": 0,
    }
    try:
        controller._accept_generation({**payload, "duplicateRetry": 0})
        assert _wait_for(app, lambda: retry_calls == [(True, 1)])
        controller._accept_generation({**payload, "duplicateRetry": 1})
        app.processEvents()
        assert retry_calls == [(True, 1)]
        assert controller.bubble == {}
        assert controller.engine.gate.state(datetime.now(UTC))["countToday"] == before_count
        assert database.recent_proactive_summaries(12) == []
    finally:
        controller.shutdown()
    assert app is not None
