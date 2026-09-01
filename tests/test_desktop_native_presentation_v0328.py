from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication, QWindow
from PySide6.QtTest import QTest

from lilies.app import DesktopSurfacePresentationProbe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _SyntheticDesktopWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.visible = True
        self.exposed = False
        self.window_visibility = QWindow.Visibility.Windowed
        self.show_normal_calls = 0
        self.hide_calls = 0
        self.show_calls = 0

    def isVisible(self) -> bool:
        return self.visible

    def isExposed(self) -> bool:
        return self.exposed

    def visibility(self) -> QWindow.Visibility:
        return self.window_visibility

    def showNormal(self) -> None:
        self.show_normal_calls += 1
        self.visible = True
        self.window_visibility = QWindow.Visibility.Windowed

    def hide(self) -> None:
        self.hide_calls += 1
        self.visible = False
        self.window_visibility = QWindow.Visibility.Hidden

    def show(self) -> None:
        self.show_calls += 1
        self.visible = True
        self.window_visibility = QWindow.Visibility.Windowed


def test_desktop_probe_is_bounded_non_activating_and_cancelable() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    parent = QObject()
    window = _SyntheticDesktopWindow()
    probe = DesktopSurfacePresentationProbe(window, parent)
    probe._RETRY_DELAYS_MS = (0, 10, 20, 35)

    probe.requestPresentation()
    QTest.qWait(70)
    app.processEvents()
    assert probe.success_count == 0
    assert probe.attempt_count == 4
    assert probe.show_normal_count == 1
    assert probe.remap_count == 1
    assert window.show_normal_calls == 1
    assert window.hide_calls == 1
    assert window.show_calls == 1

    # Every scheduled callback has now expired.  Waiting longer cannot turn
    # the finite verifier into a background polling loop.
    QTest.qWait(80)
    assert probe.attempt_count == 4
    assert window.show_normal_calls == 1
    assert window.hide_calls == 1
    assert window.show_calls == 1

    attempts_before_cancel = probe.attempt_count
    probe._RETRY_DELAYS_MS = (25,)
    probe.requestPresentation()
    probe.cancelPending()
    QTest.qWait(45)
    assert probe.attempt_count == attempts_before_cancel

    window.visible = True
    window.exposed = True
    window.window_visibility = QWindow.Visibility.Minimized
    probe._RETRY_DELAYS_MS = (0, 12)
    probe.requestPresentation()
    QTest.qWait(60)
    assert probe.success_count == 1
    assert probe.last_evidence["passed"] is True
    assert window.show_normal_calls == 2
    assert window.window_visibility == QWindow.Visibility.Windowed


def test_cancel_between_remap_hide_and_show_cannot_strand_desktop_hidden() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    window = _SyntheticDesktopWindow()
    probe = DesktopSurfacePresentationProbe(window, QObject())
    probe._request_serial = 41
    probe._show_normal_used = True

    probe._probe(41, 2)
    assert window.visible is False
    assert probe._remap_hidden is True

    probe.cancelPending(True)
    assert window.visible is True
    assert probe._remap_hidden is False
    assert window.show_calls == 1

    # Compact/intentional-hide cancellation must invalidate the already
    # queued zero-shot without showing the desktop again.
    window.visible = True
    window.window_visibility = QWindow.Visibility.Windowed
    probe._request_serial = 52
    probe._show_normal_used = True
    probe._remap_used = False
    probe._probe(52, 2)
    assert window.visible is False
    probe.cancelPending(False)
    QTest.qWait(20)
    assert window.visible is False
    assert window.show_calls == 1


def test_qwindows_unknown_worker_layer_waits_for_later_native_evidence() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    window = _SyntheticDesktopWindow()
    window.exposed = True
    probe = DesktopSurfacePresentationProbe(window, QObject())
    probe._request_serial = 7
    evidence = [
        {"available": True, "passed": None, "workerLayerOk": None},
        {"available": True, "passed": True, "workerLayerOk": True},
    ]
    probe._native_evidence = lambda: evidence.pop(0)

    probe._probe(7, 0)
    assert probe.success_count == 0
    assert probe.show_normal_count == 0
    assert probe.remap_count == 0
    assert probe.last_evidence["nativePending"] is True

    probe._probe(7, 1)
    assert probe.success_count == 1
    assert probe.last_evidence["degraded"] is False


def test_qwindows_unknown_dwm_cloak_state_is_also_a_bounded_wait() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    window = _SyntheticDesktopWindow()
    window.exposed = True
    probe = DesktopSurfacePresentationProbe(window, QObject())
    probe._request_serial = 9
    probe._native_evidence = lambda: {
        "available": True,
        "passed": None,
        "cloaked": None,
        "workerLayerOk": True,
    }

    probe._probe(9, 0)
    assert probe.success_count == 0
    assert probe.show_normal_count == 0
    assert probe.remap_count == 0
    assert probe.last_evidence["nativePending"] is True

    combine = DesktopSurfacePresentationProbe._combine_native_presentation_state
    assert combine(
        pid_matches=True,
        visible=True,
        iconic=False,
        cloaked=None,
        worker_layer_ok=True,
    ) is None
    assert combine(
        pid_matches=True,
        visible=True,
        iconic=False,
        cloaked=False,
        worker_layer_ok=True,
    ) is True
    assert combine(
        pid_matches=True,
        visible=True,
        iconic=False,
        cloaked=None,
        worker_layer_ok=False,
    ) is False


def test_permanently_unknown_native_state_degrades_only_on_final_attempt() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    window = _SyntheticDesktopWindow()
    window.exposed = True
    probe = DesktopSurfacePresentationProbe(window, QObject())
    probe._RETRY_DELAYS_MS = (0, 10)
    probe._request_serial = 11
    probe._native_evidence = lambda: {"available": True, "passed": None}

    probe._probe(11, 0)
    assert probe.success_count == 0
    assert probe.last_evidence["degraded"] is False
    probe._probe(11, 1)
    assert probe.success_count == 1
    assert probe.last_evidence["degraded"] is True


def test_workerw_layer_evidence_is_shell_owned_intersecting_and_pid_safe() -> None:
    target = {
        "handle": 100,
        "pid": 700,
        "class": "QtWindow",
        "visible": True,
        "iconic": False,
        "cloaked": False,
        "rect": (0, 0, 1920, 1080),
    }
    shell_layer = {
        "handle": 200,
        "pid": 800,
        "class": "WorkerW",
        "visible": True,
        "iconic": False,
        "cloaked": False,
        "rect": (0, 0, 1920, 1080),
    }
    foreign_worker = dict(shell_layer, handle=201, pid=999)

    evaluate = DesktopSurfacePresentationProbe._worker_layer_is_valid
    arguments = {
        "target_handle": 100,
        "target_pid": 700,
        "shell_pid": 800,
        "target_rect": (0, 0, 1920, 1080),
    }
    assert evaluate(rows=[target, foreign_worker, shell_layer], **arguments) is True
    assert evaluate(rows=[shell_layer, target], **arguments) is False
    assert evaluate(rows=[target, foreign_worker], **arguments) is None

    # A recycled HWND and unavailable/irrelevant wallpaper rows can never
    # manufacture affirmative z-order evidence.
    recycled_target = dict(target, pid=701)
    assert evaluate(rows=[recycled_target, shell_layer], **arguments) is False
    assert (
        evaluate(rows=[target, dict(shell_layer, iconic=True)], **arguments) is None
    )
    assert (
        evaluate(rows=[target, dict(shell_layer, cloaked=True)], **arguments) is None
    )
    unknown_shell = dict(shell_layer, cloaked=None)
    assert evaluate(rows=[target, unknown_shell], **arguments) is None
    assert (
        evaluate(rows=[unknown_shell, target, shell_layer], **arguments) is None
    )
    assert (
        evaluate(rows=[target, shell_layer, unknown_shell], **arguments) is True
    )
    assert (
        evaluate(
            rows=[target, dict(shell_layer, rect=(2500, 0, 3000, 800))],
            **arguments,
        )
        is None
    )


def test_desktop_probe_and_qml_pending_replay_contract_are_non_invasive() -> None:
    app_source = (PROJECT_ROOT / "src" / "lilies" / "app.py").read_text("utf-8")
    main_source = (PROJECT_ROOT / "qml" / "Main.qml").read_text("utf-8")
    probe_source = app_source.split(
        "class DesktopSurfacePresentationProbe", 1
    )[1].split("def configure_quick_window_resource_lifecycle", 1)[0]

    assert "_RETRY_DELAYS_MS" in probe_source
    assert "isExposed()" in probe_source
    assert "QWindow.Visibility.Minimized" in probe_source
    assert "DwmGetWindowAttribute" in probe_source
    assert "GetShellWindow" in probe_source
    assert "EnumWindows" in probe_source
    assert "requestActivate" not in probe_source
    assert "SetWindowPos" not in probe_source
    assert "SetParent" not in probe_source
    assert "FindWindow" not in probe_source
    assert "GetWindowText" not in probe_source

    assert "DesktopSurfacePresentationProbe(root_window, app)" in app_source
    assert '"nativeDesktopPresentationController", desktop_presentation_probe' in app_source
    assert "app._lilies_desktop_presentation_probe" in app_source
    assert "property bool desktopPresentationPending: false" in main_source
    assert "function queueCurrentSurfacePresentation()" in main_source
    assert "function replayPendingSurfacePresentation()" in main_source
    assert "function probeDesktopSurfaceHealth()" in main_source
    assert 'objectName: "desktopPresentationHealthTimer"' in main_source
    assert "onTriggered: desktop.probeDesktopSurfaceHealth()" in main_source
    assert "function onHabitatChanged()" in main_source
    assert "desktop.desktopPresentationPending" in main_source
    assert "Qt.callLater(desktop.replayPendingSurfacePresentation)" in main_source
    assert "Qt.WindowDoesNotAcceptFocus" in main_source.split(
        "property real sceneBreath", 1
    )[0]
    assert "desktop.queueCurrentSurfacePresentation()" in main_source.split(
        "function onHabitatChanged()", 1
    )[1].split("}", 1)[0]
