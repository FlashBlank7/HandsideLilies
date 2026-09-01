from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# This exercises the real Main.qml/backend surface state machine but must not
# publish a native desktop window on the developer's workstation.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.paths import qml_path


class SyntheticDesktopPresentationController(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.request_count = 0
        self.cancel_count = 0
        self.cancel_recover_values: list[bool] = []

    @Slot(result=int)
    def requestPresentation(self) -> int:
        self.request_count += 1
        return self.request_count

    @Slot(bool)
    def cancelPending(self, recover_remap: bool = True) -> None:
        self.cancel_count += 1
        self.cancel_recover_values.append(bool(recover_remap))


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-desktop-replay-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(
        lambda values: warnings.extend(str(value.toString()) for value in values)
    )
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        backend.shutdown()
        temporary.cleanup()
        raise RuntimeError("Main.qml failed to load: " + " | ".join(warnings[-8:]))

    root = engine.rootObjects()[0]
    pet = root.findChild(QQuickWindow, "petWindow")
    health_timer = root.findChild(QObject, "desktopPresentationHealthTimer")
    if pet is None or health_timer is None:
        backend.shutdown()
        temporary.cleanup()
        raise RuntimeError("petWindow was not constructed")
    controller = SyntheticDesktopPresentationController()
    root.setProperty("nativeDesktopPresentationController", controller)
    backend.set_desktop_window_handle(int(root.winId()))
    backend.enter_initial_mode()

    def settle(milliseconds: int = 90) -> None:
        app.processEvents()
        QTest.qWait(milliseconds)
        app.processEvents()

    settle()
    initial = {
        "mode": str(backend.shellMode),
        "desktopHidden": not root.isVisible(),
        "petVisible": pet.isVisible(),
    }

    backend.pet_habitat.set_presence("silent")
    backend._sync_habitat_state(force_cleanup=True)
    settle()
    backend.toggleDesktopShell()
    # Coalesce several equivalent show requests while privacy suppression is
    # active; none may dispatch the native probe yet.
    backend.applicationActivationRequested.emit("show")
    backend.applicationActivationRequested.emit("visual")
    settle(120)
    suppressed_visual = {
        "mode": str(backend.shellMode),
        "dockSuppressed": bool(backend.dockSuppressed),
        "pending": bool(root.property("desktopPresentationPending")),
        "replays": int(root.property("desktopPresentationReplayCount")),
        "nativeRequests": controller.request_count,
        "petHidden": not pet.isVisible(),
    }

    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle(180)
    restored_visual = {
        "mode": str(backend.shellMode),
        "pending": bool(root.property("desktopPresentationPending")),
        "replays": int(root.property("desktopPresentationReplayCount")),
        "nativeRequests": controller.request_count,
        "desktopVisible": root.isVisible(),
        "desktopExposed": root.isExposed(),
        "petVisible": pet.isVisible(),
        "floatMode": str(backend.petFloatMode),
    }
    backend.habitatChanged.emit()
    backend.habitatChanged.emit()
    settle(100)
    duplicate_safe = {
        "replays": int(root.property("desktopPresentationReplayCount")),
        "nativeRequests": controller.request_count,
    }

    # The production five-second timer is shortened only inside this
    # offscreen fixture.  It must dispatch bounded read-only health probes in
    # visual mode and stop immediately in compact mode.
    health_before = controller.request_count
    health_timer.setProperty("interval", 60)
    settle(190)
    health_after = controller.request_count
    visual_health_probe = {
        "timerRunning": bool(health_timer.property("running")),
        "before": health_before,
        "after": health_after,
        "advanced": health_after > health_before,
    }

    # A suppressed plain Show request in compact mode targets the pet, not the
    # hidden full-desktop window, and is also replayed exactly once.
    backend.setShellMode("compact")
    settle()
    compact_health_count = controller.request_count
    settle(150)
    compact_health_probe = {
        "timerStopped": not bool(health_timer.property("running")),
        "nativeRequests": compact_health_count,
        "requestsStable": controller.request_count == compact_health_count,
    }
    backend.pet_habitat.set_presence("silent")
    backend._sync_habitat_state(force_cleanup=True)
    backend.applicationActivationRequested.emit("show")
    settle()
    compact_pending = bool(root.property("desktopPresentationPending"))
    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle(140)
    restored_compact = {
        "mode": str(backend.shellMode),
        "pendingWasSet": compact_pending,
        "pending": bool(root.property("desktopPresentationPending")),
        "replays": int(root.property("desktopPresentationReplayCount")),
        "nativeRequests": controller.request_count,
        "desktopHidden": not root.isVisible(),
        "petVisible": pet.isVisible(),
    }

    invocation_warnings = [
        warning
        for warning in warnings
        if "cancelPending" in warning
        or "Insufficient arguments" in warning
    ]
    outcome = {
        "initial": initial,
        "suppressedVisual": suppressed_visual,
        "restoredVisual": restored_visual,
        "duplicateSafe": duplicate_safe,
        "visualHealthProbe": visual_health_probe,
        "compactHealthProbe": compact_health_probe,
        "restoredCompact": restored_compact,
        "qmlWarningCount": len(warnings),
        "qmlInvocationWarnings": invocation_warnings,
        "nativeCancels": controller.cancel_count,
        "nativeCancelRecoverValues": list(controller.cancel_recover_values),
    }
    outcome["passed"] = bool(
        initial == {"mode": "compact", "desktopHidden": True, "petVisible": True}
        and suppressed_visual["mode"] == "visual"
        and suppressed_visual["dockSuppressed"]
        and suppressed_visual["pending"]
        and suppressed_visual["replays"] == 0
        and suppressed_visual["nativeRequests"] == 0
        and suppressed_visual["petHidden"]
        and restored_visual["mode"] == "visual"
        and not restored_visual["pending"]
        and restored_visual["replays"] == 1
        and restored_visual["nativeRequests"] == 1
        and restored_visual["desktopVisible"]
        and restored_visual["desktopExposed"]
        and restored_visual["petVisible"]
        and restored_visual["floatMode"] == "always"
        and duplicate_safe == {"replays": 1, "nativeRequests": 1}
        and visual_health_probe["timerRunning"]
        and visual_health_probe["advanced"]
        and compact_health_probe["timerStopped"]
        and compact_health_probe["requestsStable"]
        and restored_compact["mode"] == "compact"
        and restored_compact["pendingWasSet"]
        and not restored_compact["pending"]
        and restored_compact["replays"] == 2
        and restored_compact["nativeRequests"] == compact_health_count
        and restored_compact["desktopHidden"]
        and restored_compact["petVisible"]
        and not invocation_warnings
        and controller.cancel_count >= 2
        and True in controller.cancel_recover_values
        and False in controller.cancel_recover_values
    )

    for window in root.findChildren(QQuickWindow):
        window.setVisible(False)
    if isinstance(root, QQuickWindow):
        root.setVisible(False)
    app.processEvents()
    backend.shutdown()
    app.processEvents()
    temporary.cleanup()
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
