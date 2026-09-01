from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from lilies.core.activity import capture_window_image_via_print_window


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = PROJECT_ROOT / "scripts" / "verify_native_window_capture.py"


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("verify_native_window_capture", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_capture_probe_reports_only_content_free_truth() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROBE_PATH)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert completed.stderr == ""
    outcome = json.loads(completed.stdout)
    assert set(outcome) == {
        "callSucceeded",
        "sizeValid",
        "evidenceValid",
        "reasonCode",
    }
    assert type(outcome["callSucceeded"]) is bool
    assert type(outcome["sizeValid"]) is bool
    assert type(outcome["evidenceValid"]) is bool
    assert outcome["reasonCode"] in {
        "unsupported-platform",
        "window-create-failed",
        "native-call-failed",
        "invalid-size",
        "no-visual-evidence",
        "ok",
    }
    if os.name == "nt":
        assert outcome["callSucceeded"] is True
        assert outcome["sizeValid"] is True
        assert outcome["evidenceValid"] is True
        assert outcome["reasonCode"] == "ok"
    assert outcome["evidenceValid"] is (outcome["reasonCode"] == "ok")
    if not outcome["sizeValid"]:
        assert outcome["evidenceValid"] is False


def test_native_capture_probe_evidence_classifier_is_deterministic() -> None:
    probe = _load_probe_module()
    uniform = Image.new("RGB", (96, 72), "black")
    pattern = Image.new("RGB", (96, 72), "red")
    ImageDraw.Draw(pattern).rectangle((48, 0, 95, 71), fill="blue")
    wrong_colours = Image.new("RGB", (96, 72), "green")
    ImageDraw.Draw(wrong_colours).rectangle((48, 0, 95, 71), fill="yellow")
    reversed_pattern = Image.new("RGB", (96, 72), "blue")
    ImageDraw.Draw(reversed_pattern).rectangle((48, 0, 95, 71), fill="red")
    corrupted_pattern = pattern.copy()
    corrupted_pattern.putpixel((10, 10), (254, 0, 0))
    try:
        assert probe._evidence_valid(uniform) is False
        assert probe._evidence_valid(pattern) is True
        assert probe._evidence_valid(wrong_colours) is False
        assert probe._evidence_valid(reversed_pattern) is False
        assert probe._evidence_valid(corrupted_pattern) is False
    finally:
        uniform.close()
        pattern.close()
        wrong_colours.close()
        reversed_pattern.close()
        corrupted_pattern.close()


def test_native_capture_probe_has_static_privacy_guards() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")
    assert "WS_EX_NOACTIVATE" in source
    assert "SW_SHOWNOACTIVATE" in source
    assert "SM_XVIRTUALSCREEN" in source
    assert "SM_YVIRTUALSCREEN" in source
    assert "capture_window_image_via_print" in source
    assert "timeout_ms=750" in source
    assert "expected_process_id=os.getpid()" in source
    assert "ShowWindow.argtypes" in source
    assert "UpdateWindow.argtypes" in source
    assert "GetSystemMetrics.argtypes" in source
    assert "GetForegroundWindow" not in source
    assert "QScreen" not in source
    assert "ImageGrab.grab" not in source
    assert "all_screens" not in source
    assert "bbox=" not in source
    assert ".save(" not in source
    assert '"--helper-exe"' in source
    assert '"--native-capture-helper"' in source
    assert "subprocess.Popen(" in source
    assert "CREATE_NO_WINDOW" in source
    assert 'child_environment["LILIES_DATA_DIR"]' in source
    assert "stdout=subprocess.DEVNULL" in source
    assert "stderr=subprocess.DEVNULL" in source
    assert "SetForegroundWindow" not in source

    probe = _load_probe_module()
    backend = inspect.getsource(probe.capture_window_image_via_print)
    assert "SendMessageTimeoutW" in backend
    assert "WM_PRINT" in backend
    assert "timeout" in backend
    assert "expected_process_id" in backend
    # One definition plus one fence immediately before and after WM_PRINT.
    assert backend.count("validate_target()") == 3
    assert "IsWindow(" in backend
    assert "IsWindowVisible(" in backend
    assert "GetWindowThreadProcessId(" in backend
    assert "PRF_CHECKVISIBLE" in backend
    assert "PRF_OWNED" not in backend
    assert "SMTO_ERRORONEXIT" in backend
    assert "12_000_000" in backend
    assert "_window_is_explicitly_cloaked" in backend
    assert "ImageGrab.grab(" not in backend
    assert "GetForegroundWindow" not in backend
    assert "BitBlt" not in backend
    assert "QScreen" not in backend

    cross_process_backend = inspect.getsource(capture_window_image_via_print_window)
    assert "PrintWindow(" in cross_process_backend
    assert "PW_CLIENTONLY" in cross_process_backend
    assert "PW_RENDERFULLCONTENT" not in cross_process_backend
    assert cross_process_backend.count("validate_target()") == 3
    assert "expected_process_id" in cross_process_backend
    assert "IsWindowVisible(" in cross_process_backend
    assert "12_000_000" in cross_process_backend
    assert "BitBlt" not in cross_process_backend
    assert "QScreen" not in cross_process_backend
