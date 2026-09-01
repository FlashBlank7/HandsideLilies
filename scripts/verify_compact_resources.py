from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import configure_quick_window_resource_lifecycle
from lilies.backend import Backend
from lilies.paths import qml_path


EXPECTED_QML_WINDOW_COUNT = 16
EXPECTED_NAMED_QML_WINDOWS = (
    "boxWorldSceneWindow",
    "chatWindow",
    "companionBubbleWindow",
    "focusDiversionBubbleWindow",
    "paperDock",
    "petWindow",
    "selectionBubble",
    "selectionQuestion",
    "v03AllWindowsDrawer",
    "v03ConnectorSetup",
    "v03FocusTimerAura",
    "v03PaperFoldDock",
    "v03WindowPreviewShelf",
    "v03WorkPanel",
    "windowShelf",
)


def evaluate_window_resource_lifecycle(root: QObject) -> dict[str, object]:
    """Configure every QML window, then enforce the fixed release contract."""

    lifecycle = configure_quick_window_resource_lifecycle(root)
    windows = tuple(lifecycle.windows)
    named_windows = sorted(
        str(window.objectName()) for window in windows if window.objectName()
    )
    persistent_hints = [
        {
            "objectName": str(window.objectName() or ""),
            "persistentGraphics": bool(window.isPersistentGraphics()),
            "persistentSceneGraph": bool(window.isPersistentSceneGraph()),
        }
        for window in windows
    ]
    persistent_hints_disabled = all(
        not value["persistentGraphics"] and not value["persistentSceneGraph"]
        for value in persistent_hints
    )
    passed = (
        len(windows) == EXPECTED_QML_WINDOW_COUNT
        and named_windows == list(EXPECTED_NAMED_QML_WINDOWS)
        and persistent_hints_disabled
    )
    return {
        "windowCount": len(windows),
        "expectedWindowCount": EXPECTED_QML_WINDOW_COUNT,
        "namedWindows": named_windows,
        "expectedNamedWindows": list(EXPECTED_NAMED_QML_WINDOWS),
        "persistentHints": persistent_hints,
        "persistentHintsDisabled": persistent_hints_disabled,
        "passed": passed,
    }


def compact_resource_report_passed(
    compact_unloaded: bool,
    cold_multimedia_unloaded: bool,
    visual_round_trip: bool,
    visual_surface_recovery: bool,
    window_resource_lifecycle: dict[str, object],
) -> bool:
    return all(
        (
            compact_unloaded,
            cold_multimedia_unloaded,
            visual_round_trip,
            visual_surface_recovery,
            bool(window_resource_lifecycle.get("passed")),
        )
    )


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _current_process_thread_count() -> int | None:
    """Count only this diagnostic process's threads; inspect no window state."""

    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        return None
    entry = _ThreadEntry32()
    entry.dwSize = ctypes.sizeof(entry)
    kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ThreadEntry32),
    ]
    kernel32.Thread32Next.restype = wintypes.BOOL
    count = 0
    process_id = os.getpid()
    try:
        more = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while more:
            if int(entry.th32OwnerProcessID) == process_id:
                count += 1
            more = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    return count


def _memory_snapshot() -> dict[str, float | int | None]:
    """Return current-process counters without adding a runtime dependency."""

    if os.name != "nt":
        return {"workingSetMiB": None, "privateMiB": None, "threads": None}
    counters = _ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    process = kernel32.GetCurrentProcess()
    get_memory = kernel32.K32GetProcessMemoryInfo
    get_memory.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    get_memory.restype = wintypes.BOOL
    ok = get_memory(
        process, ctypes.byref(counters), counters.cb
    )
    if not ok:
        return {"workingSetMiB": None, "privateMiB": None, "threads": None}

    # These counters cover only this offscreen diagnostic process.
    handle_count = wintypes.DWORD()
    kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    kernel32.GetProcessHandleCount(process, ctypes.byref(handle_count))
    return {
        "workingSetMiB": round(counters.WorkingSetSize / 1048576, 2),
        "privateMiB": round(counters.PrivateUsage / 1048576, 2),
        "processHandles": int(handle_count.value),
        "threads": _current_process_thread_count(),
    }


def _module_loaded(name: str) -> bool:
    if os.name != "nt":
        return False
    return bool(ctypes.windll.kernel32.GetModuleHandleW(name))


def _settle(milliseconds: int = 180) -> None:
    QTest.qWait(milliseconds)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    QApplication.processEvents()


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    temporary = tempfile.TemporaryDirectory(prefix="lilies-compact-resources-")
    os.environ["LILIES_DATA_DIR"] = temporary.name

    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")
    root = engine.rootObjects()[0]
    window_resource_lifecycle = evaluate_window_resource_lifecycle(root)

    def snapshot(label: str) -> dict[str, object]:
        scene = root.findChild(QQuickItem, "desktopScene")
        player = root.findChild(QObject, "desktopCinematicPlayer")
        multimedia_modules = {
            name: _module_loaded(name)
            for name in (
                "Qt6Multimedia.dll",
                "Qt6MultimediaQuick.dll",
                "ffmpegmediaplugin.dll",
                "avcodec-61.dll",
            )
        }
        return {
            "label": label,
            "mode": str(backend.shellMode),
            "renderer": str(backend.renderer),
            "desktopVisible": root.isVisible(),
            "sceneLoaded": bool(root.property("desktopSceneLoaded")),
            "sceneExists": scene is not None,
            "videoLoaded": bool(root.property("desktopVideoLoaded")),
            "playerExists": player is not None,
            "playerState": str(root.property("desktopVideoPlaybackState")),
            "multimediaModules": multimedia_modules,
            "memory": _memory_snapshot(),
        }

    states: dict[str, dict[str, object]] = {}
    backend.setRenderer("scene2d")
    backend.setShellMode("compact")
    _settle()
    states["compactCold"] = snapshot("compactCold")

    backend.setShellMode("visual")
    _settle(420)
    states["visualScene"] = snapshot("visualScene")
    root.hide()
    _settle(80)
    visual_surface_hidden = not root.isVisible()
    invoked_surface_recovery = bool(
        QMetaObject.invokeMethod(root, "ensureDesktopSurface")
    )
    _settle(180)
    states["visualSceneRecovered"] = snapshot("visualSceneRecovered")
    visual_surface_recovery = (
        visual_surface_hidden
        and invoked_surface_recovery
        and states["visualSceneRecovered"]["desktopVisible"]
        and states["visualSceneRecovered"]["sceneLoaded"]
        and states["visualSceneRecovered"]["sceneExists"]
    )
    backend.setShellMode("compact")
    _settle(420)
    states["compactAfterScene"] = snapshot("compactAfterScene")

    backend.setRenderer("video")
    _settle()
    states["compactVideoCold"] = snapshot("compactVideoCold")
    backend.setShellMode("visual")
    _settle(500)
    states["visualVideo"] = snapshot("visualVideo")
    backend.setShellMode("compact")
    # Give the platform decoder worker pool time to retire after its QML
    # MediaPlayer owner has been destroyed.  The DLL may remain mapped in Qt's
    # plugin cache, but it must not keep a player object alive.
    _settle(2200)
    states["compactAfterVideo"] = snapshot("compactAfterVideo")

    compact_keys = ("compactCold", "compactAfterScene", "compactVideoCold", "compactAfterVideo")
    compact_unloaded = all(
        not states[key]["desktopVisible"]
        and not states[key]["sceneLoaded"]
        and not states[key]["sceneExists"]
        and not states[key]["videoLoaded"]
        and not states[key]["playerExists"]
        and states[key]["playerState"] == "unloaded"
        for key in compact_keys
    )
    cold_multimedia_unloaded = not any(
        states["compactVideoCold"]["multimediaModules"].values()
    )
    visual_round_trip = (
        states["visualScene"]["desktopVisible"]
        and states["visualScene"]["sceneLoaded"]
        and states["visualScene"]["sceneExists"]
        and not states["visualScene"]["videoLoaded"]
        and states["visualVideo"]["desktopVisible"]
        and not states["visualVideo"]["sceneLoaded"]
        and states["visualVideo"]["videoLoaded"]
        and states["visualVideo"]["playerExists"]
        and states["visualVideo"]["playerState"] in {"playing", "paused", "stopped"}
    )

    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "compactUnloaded": compact_unloaded,
        "coldMultimediaUnloaded": cold_multimedia_unloaded,
        "visualRoundTrip": visual_round_trip,
        "visualSurfaceRecovery": visual_surface_recovery,
        "windowResourceLifecycle": window_resource_lifecycle,
        "states": states,
        "passed": compact_resource_report_passed(
            compact_unloaded,
            cold_multimedia_unloaded,
            visual_round_trip,
            visual_surface_recovery,
            window_resource_lifecycle,
        ),
    }
    report_path = PROJECT_ROOT / "artifacts" / "compact-resource-lifecycle.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({**report, "report": str(report_path)}, ensure_ascii=False))

    backend.shutdown()
    temporary.cleanup()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
