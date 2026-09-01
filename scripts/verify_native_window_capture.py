from __future__ import annotations

"""Content-free probe for Lilies' HWND capture plumbing on Windows.

The probe owns every pixel source it touches.  Its tiny native window is
created with no-activate/tool-window styles and is placed wholly beyond the
virtual desktop before it is shown.  No image is saved and the JSON result is
limited to four booleans/codes that cannot contain window or user content.
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lilies.core.activity import capture_window_image_via_print  # noqa: E402


_PROBE_WIDTH = 96
_PROBE_HEIGHT = 72
_OUTPUT_KEYS = (
    "callSucceeded",
    "sizeValid",
    "evidenceValid",
    "reasonCode",
)


def _result(
    *,
    call_succeeded: bool = False,
    size_valid: bool = False,
    evidence_valid: bool = False,
    reason_code: str,
) -> dict[str, bool | str]:
    return {
        "callSucceeded": bool(call_succeeded),
        "sizeValid": bool(size_valid),
        "evidenceValid": bool(evidence_valid),
        "reasonCode": str(reason_code),
    }


def _evidence_valid(image: Image.Image) -> bool:
    """Recognize the deterministic two-colour probe without exposing pixels."""

    if image.size != (_PROBE_WIDTH, _PROBE_HEIGHT):
        return False
    sample = image.convert("RGB")
    left = sample.crop((0, 0, _PROBE_WIDTH // 2, _PROBE_HEIGHT))
    right = sample.crop((_PROBE_WIDTH // 2, 0, _PROBE_WIDTH, _PROBE_HEIGHT))
    try:
        return left.getextrema() == (
            (255, 255),
            (0, 0),
            (0, 0),
        ) and right.getextrema() == (
            (0, 0),
            (0, 0),
            (255, 255),
        )
    finally:
        left.close()
        right.close()
        sample.close()


def _run_windows_probe(helper_exe: Path | None = None) -> dict[str, bool | str]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    wndproc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", wndproc_type),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class PAINTSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hdc", wintypes.HDC),
            ("fErase", wintypes.BOOL),
            ("rcPaint", wintypes.RECT),
            ("fRestore", wintypes.BOOL),
            ("fIncUpdate", wintypes.BOOL),
            ("rgbReserved", ctypes.c_byte * 32),
        ]

    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.UpdateWindow.argtypes = [wintypes.HWND]
    user32.UpdateWindow.restype = wintypes.BOOL
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = wintypes.HDC
    user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.restype = wintypes.BOOL
    user32.FillRect.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.HBRUSH,
    ]
    user32.FillRect.restype = ctypes.c_int
    gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
    gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    WM_PAINT = 0x000F
    WM_ERASEBKGND = 0x0014
    WM_PRINT = 0x0317
    WM_PRINTCLIENT = 0x0318
    CS_OWNDC = 0x0020
    WS_POPUP = 0x80000000
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    SW_HIDE = 0
    SW_SHOWNOACTIVATE = 4
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    PM_REMOVE = 0x0001

    def paint_pattern(dc: int) -> None:
        if not dc:
            return
        left = wintypes.RECT(0, 0, _PROBE_WIDTH // 2, _PROBE_HEIGHT)
        right = wintypes.RECT(
            _PROBE_WIDTH // 2,
            0,
            _PROBE_WIDTH,
            _PROBE_HEIGHT,
        )
        red = gdi32.CreateSolidBrush(0x000000FF)
        blue = gdi32.CreateSolidBrush(0x00FF0000)
        try:
            if red:
                user32.FillRect(dc, ctypes.byref(left), red)
            if blue:
                user32.FillRect(dc, ctypes.byref(right), blue)
        finally:
            if red:
                gdi32.DeleteObject(red)
            if blue:
                gdi32.DeleteObject(blue)

    @wndproc_type
    def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_ERASEBKGND:
            paint_pattern(int(wparam))
            return 1
        if message in {WM_PRINT, WM_PRINTCLIENT}:
            paint_pattern(int(wparam))
            return 1
        if message == WM_PAINT:
            paint = PAINTSTRUCT()
            dc = user32.BeginPaint(hwnd, ctypes.byref(paint))
            try:
                paint_pattern(int(dc or 0))
            finally:
                user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        return int(user32.DefWindowProcW(hwnd, message, wparam, lparam))

    instance = kernel32.GetModuleHandleW(None)
    class_name = f"LiliesNativeCaptureProbe_{os.getpid()}"
    window_class = WNDCLASSW(
        CS_OWNDC,
        window_proc,
        0,
        0,
        instance,
        None,
        None,
        None,
        None,
        class_name,
    )
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        return _result(reason_code="window-create-failed")

    hwnd = None
    try:
        # A generous gap keeps the complete popup outside every monitor in the
        # virtual desktop, including its one-pixel edge and any DPI rounding.
        x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)) - _PROBE_WIDTH - 512
        y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)) - _PROBE_HEIGHT - 512
        hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            class_name,
            "",
            WS_POPUP,
            x,
            y,
            _PROBE_WIDTH,
            _PROBE_HEIGHT,
            None,
            None,
            instance,
            None,
        )
        if not hwnd:
            return _result(reason_code="window-create-failed")
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(hwnd)

        try:
            if helper_exe is None:
                image = capture_window_image_via_print(
                    int(hwnd),
                    expected_process_id=os.getpid(),
                    timeout_ms=750,
                )
            else:
                helper_path = Path(helper_exe).resolve()
                if not helper_path.is_file():
                    return _result(reason_code="native-call-failed")
                with tempfile.TemporaryDirectory(
                    prefix="lilies-native-capture-probe-"
                ) as temporary_root:
                    data_directory = Path(temporary_root)
                    destination = (
                        data_directory
                        / "capture-staging"
                        / f"capture-{uuid.uuid4().hex}.png"
                    )
                    child_environment = dict(os.environ)
                    child_environment["LILIES_DATA_DIR"] = str(data_directory)
                    child_environment["PYTHONUTF8"] = "1"
                    process = subprocess.Popen(
                        [
                            str(helper_path),
                            "--native-capture-helper",
                            str(int(hwnd)),
                            str(os.getpid()),
                            str(destination),
                            "--native-capture-max-edge",
                            "1600",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=child_environment,
                        creationflags=int(
                            getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        ),
                    )
                    deadline = time.monotonic() + 8.0
                    message = wintypes.MSG()
                    while process.poll() is None and time.monotonic() < deadline:
                        while user32.PeekMessageW(
                            ctypes.byref(message), None, 0, 0, PM_REMOVE
                        ):
                            user32.TranslateMessage(ctypes.byref(message))
                            user32.DispatchMessageW(ctypes.byref(message))
                        time.sleep(0.005)
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=1.0)
                        return _result(reason_code="native-call-failed")
                    if process.returncode != 0 or not destination.is_file():
                        return _result(reason_code="native-call-failed")
                    with Image.open(destination) as captured:
                        captured.load()
                        image = captured.copy()
        except (OSError, RuntimeError, TypeError, ValueError):
            return _result(reason_code="native-call-failed")
        try:
            size_valid = image.size == (_PROBE_WIDTH, _PROBE_HEIGHT)
            evidence_valid = bool(size_valid and _evidence_valid(image))
            return _result(
                call_succeeded=True,
                size_valid=size_valid,
                evidence_valid=evidence_valid,
                reason_code=(
                    "ok"
                    if evidence_valid
                    else ("no-visual-evidence" if size_valid else "invalid-size")
                ),
            )
        finally:
            image.close()
    finally:
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
            user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, instance)


def run_probe(helper_exe: Path | None = None) -> dict[str, bool | str]:
    if os.name != "nt":
        return _result(reason_code="unsupported-platform")
    try:
        outcome = _run_windows_probe(helper_exe)
    except Exception:
        outcome = _result(reason_code="native-call-failed")
    # Keep future edits from accidentally widening the public diagnostic.
    return {key: outcome[key] for key in _OUTPUT_KEYS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--helper-exe", type=Path)
    options = parser.parse_args(argv)
    print(
        json.dumps(
            run_probe(options.helper_exe),
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
