"""Bounded, read-only WM_NULL response sampling of one identified process.

No Qt/Lilies imports, socket, tokens, titles, screen/input, stack reading,
focus/window mutation or target termination. Only this fixed no-op message is
sent. A sent-message acknowledgement is NOT queued-input or presented-frame
latency, and the probe can briefly wake an otherwise idle receiving thread.

https://learn.microsoft.com/en-us/windows/win32/winmsg/wm-null
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeoutw
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import UTC, datetime
import json
import importlib.util
import os
from pathlib import Path
import time

_helper_spec = importlib.util.spec_from_file_location(
    "_lilies_response_identity", Path(__file__).with_name("verify_install_health.py"),
)
_helper = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helper)
HealthError, WindowsProcess = _helper.HealthError, _helper.WindowsProcess


ROOT = Path(__file__).resolve().parents[1]
MAX_WINDOWS = 128
MAX_THREADS = 8
TIMEOUT_MS = 75


class WindowQueries:
    def __init__(self, process):
        self.process = process
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.api.EnumWindows.argtypes = [self.callback_type, wintypes.LPARAM]
        self.api.EnumWindows.restype = wintypes.BOOL
        self.api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.api.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.api.IsWindowVisible.argtypes = [wintypes.HWND]
        self.api.IsWindowVisible.restype = wintypes.BOOL
        self.api.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
        ]
        self.api.SendMessageTimeoutW.restype = ctypes.c_ssize_t

    def owner(self, hwnd):
        pid = wintypes.DWORD()
        tid = int(self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
        return int(pid.value), tid

    def targets(self):
        self.process.check_alive()
        rows = []

        def collect(hwnd, _unused):
            pid, tid = self.owner(hwnd)
            if pid == self.process.pid and tid:
                rows.append((int(hwnd), tid, bool(self.api.IsWindowVisible(hwnd))))
            return len(rows) <= MAX_WINDOWS

        callback = self.callback_type(collect)
        if not self.api.EnumWindows(callback, 0):
            raise HealthError("window-enumeration-incomplete")
        # One representative per owning thread, preferring a visible surface.
        # Sorting contains only numeric HWND/thread identities, never titles.
        grouped = {}
        for hwnd, tid, visible in sorted(rows, key=lambda r: (not r[2], r[0])):
            grouped.setdefault(tid, (hwnd, tid, visible))
        if len(grouped) > MAX_THREADS:
            raise HealthError("too-many-window-threads")
        return list(grouped.values())

    def ping(self, target):
        hwnd, tid, _visible = target
        self.process.check_alive()
        if hwnd in (0, 0xffff) or self.owner(hwnd) != (self.process.pid, tid):
            return {"status": "target-changed", "queryMs": None}
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        started = time.perf_counter()
        ok = self.api.SendMessageTimeoutW(
            hwnd, 0, 0, 0, 0x0001 | 0x0002 | 0x0020, TIMEOUT_MS, ctypes.byref(result),
        )
        elapsed = (time.perf_counter() - started) * 1000
        error = ctypes.get_last_error()
        # Distinguish the function's success from WM_NULL's *zero* LRESULT.
        status = "acknowledged" if ok else (
            "timeout" if error == 1460 else "denied" if error == 5 else "failed-unknown"
        )
        self.process.check_alive()
        if self.owner(hwnd) != (self.process.pid, tid):
            status = "target-changed"
        return {"status": status, "queryMs": round(elapsed, 4), "error": int(error) if not ok else 0}


def sample(queries, count, interval, *, clock=time.perf_counter, sleep=time.sleep):
    if type(count) is not int or not 1 <= count <= 40 or not 1 <= interval <= 5:
        raise ValueError("invalid-sample-bounds")
    started = clock()
    deadline = started + 50.0
    rows = []
    stop = "sample-count"
    for index in range(count):
        if clock() >= deadline:
            stop = "observation-deadline"
            break
        row_start = clock()
        targets = queries.targets()
        enumerated = clock()
        probes = []
        for target in targets:
            if clock() >= deadline:
                stop = "observation-deadline"
                break
            check_start = clock()
            result = queries.ping(target)
            probes.append({
                "threadId": target[1], "visible": target[2], **result,
                "wholeCheckMs": round((clock() - check_start) * 1000, 4),
            })
        rows.append({
            "elapsedMs": round((row_start - started) * 1000, 4),
            "enumerationMs": round((enumerated - row_start) * 1000, 4),
            "targets": probes,
        })
        if stop != "sample-count":
            break
        if index + 1 < count:
            # No catch-up burst after a slow call; time limits bound the next
            # call, not every synchronous Windows metadata operation itself.
            sleep(min(interval, max(0, deadline - clock())))
    return {"stopReason": stop, "durationMs": round((clock() - started) * 1000, 4), "samples": rows}


def output_path(value):
    target = Path(value)
    if not target.is_absolute():
        raise ValueError("absolute-artifact-path-required")
    expected = ROOT / "artifacts"
    if os.name != "nt" or ROOT.drive.casefold() != "f:":
        raise ValueError("F-artifact-root-required")
    if expected.resolve() != expected or target.parent.resolve() != expected:
        raise ValueError("artifact-parent-mismatch")
    if not target.name.startswith("window-response-") or target.suffix != ".json":
        raise ValueError("artifact-name-invalid")
    if target.exists() or target.is_symlink():
        raise FileExistsError("artifact-already-exists")
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--created", required=True, type=int, help="exact Windows creation FILETIME")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.pid == os.getpid() or not 1 <= args.samples <= 40 or not 1 <= args.interval <= 5:
        parser.error("external process and bounded samples/interval required")
    target = output_path(args.output)
    process = WindowsProcess(args.pid, args.executable, args.created)
    try:
        measurements = sample(WindowQueries(process), args.samples, args.interval)
    finally:
        # Close only our query handle. Never terminate the observed process.
        process.close()
    report = {
        "schemaVersion": 1, "capturedAt": datetime.now(UTC).isoformat(),
        "pid": args.pid, "processCreatedFiletime": args.created,
        "requestedSamples": args.samples, "intervalSeconds": args.interval,
        "message": "WM_NULL", "timeoutMs": TIMEOUT_MS, **measurements,
        "scope": "Sent-message acknowledgements only. Not queued mouse/keyboard input, drag latency, compositor/presented FPS, workload classification or long-run acceptance. Briefly wakes the selected thread. No titles, pixels, private data or target mutation.",
    }
    with target.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=True, indent=2)
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
