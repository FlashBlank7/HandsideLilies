from __future__ import annotations

import ctypes
import inspect
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

import lilies.core.native_capture_helper as helper
import lilies.core.activity as activity
import lilies.companion_controller as controller_module
from test_companion_capture_fallback_v0334 import _controller, _wait_for
from lilies.core.activity import ForegroundContext, CaptureStaging


@pytest.mark.parametrize("exit_code,reason", [
    (20, "native-helper-invalid-request"), (21, "native-print-failed"),
    (22, "encode-failed"), (23, "native-helper-frame-empty"),
    (24, "encode-storage-failed"), (0xC0000005, "native-helper-process-failed"),
])
def test_helper_exit_reasons_stay_content_free(tmp_path, monkeypatch, exit_code, reason):
    class Process:
        returncode = exit_code
        def __init__(self, *args, **kwargs): pass
        def poll(self): return self.returncode
    monkeypatch.setattr(helper, "native_capture_helper_available", lambda: True)
    monkeypatch.setattr(helper.subprocess, "Popen", Process)
    staging = CaptureStaging(tmp_path / "capture-staging", tmp_path / "library")
    with pytest.raises(helper.NativeCaptureHelperError) as result:
        helper.stage_window_capture_with_helper(staging, 1, 2)
    assert result.value.reason == reason
    assert not list(staging.root.glob("*.png"))


def test_unknown_helper_reason_cannot_become_persisted_content():
    error = helper.NativeCaptureHelperError("private content", reason="private content")
    assert error.reason == "native-print-failed"


def test_gpu_capture_requests_composited_client_area(monkeypatch):
    flags = []
    buffer = ctypes.create_string_buffer(8 * 8 * 4)
    class User32:
        def IsWindow(self, h): return True
        def IsWindowVisible(self, h): return True
        def GetWindowThreadProcessId(self, h, p): p._obj.value = 31; return 1
        def GetClientRect(self, h, p): p._obj.right = p._obj.bottom = 8; return True
        def PrintWindow(self, h, dc, flag): flags.append(flag); return True
    class Gdi32:
        def CreateCompatibleDC(self, h): return 1
        def CreateDIBSection(self, dc, info, usage, bits, section, offset):
            bits._obj.value = ctypes.addressof(buffer); return 2
        def SelectObject(self, dc, bitmap): return 3
        def DeleteObject(self, bitmap): return True
        def DeleteDC(self, dc): return True
    monkeypatch.setattr(activity, "_user32", User32)
    monkeypatch.setattr(activity, "_gdi32", Gdi32)
    monkeypatch.setattr(activity, "_window_is_explicitly_cloaked", lambda h: False)
    image = activity.capture_window_image_via_print_window(7, expected_process_id=31)
    image.close()
    assert flags == [3]  # PW_CLIENTONLY | PW_RENDERFULLCONTENT, exactly one HWND


def test_failed_capture_is_not_presented_as_ready_and_error_is_specific(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    context = ForegroundContext(43, process_id=9, process_name="wps.exe", window_class="WPS")
    controller = _controller(tmp_path, monkeypatch, context)
    controller._activity_enabled = True
    controller.activity.enabled = True
    controller.activity._last_reason = "allowed"
    monkeypatch.setattr(controller_module, "native_capture_helper_available", lambda: True)
    def fail(*args, **kwargs):
        raise helper.NativeCaptureHelperError("sensitive path omitted", reason="native-helper-frame-empty")
    monkeypatch.setattr(controller_module, "stage_window_capture_with_helper", fail)
    try:
        assert controller._start_generation(context, force=True, manual_capture=True)
        assert _wait_for(app, lambda: not controller.busy)
        assert not controller.runtime.calls and not controller.bubble
        status = controller.activityStatus
        assert status["lastCaptureReason"] == "native-helper-frame-empty"
        assert status["imageSubmitted"] is False
        assert status["compactStatusLabel"] == "陪伴 · 上次截图未成功"
        assert status["stateLabel"] == "上次截图未成功"
        assert status["captureRetrySeconds"] > 0
        assert "空白或受保护" in status["stateDetail"]
        controller.activity.paused = True
        assert controller.activityStatus["compactStatusLabel"] != "陪伴 · 上次截图未成功"
        persisted = repr(controller.database.get_setting("companion_last_capture_status"))
        assert "sensitive" not in persisted
    finally:
        controller.shutdown()
