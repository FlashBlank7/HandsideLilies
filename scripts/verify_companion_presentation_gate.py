from __future__ import annotations

"""Isolated offscreen probe for the companion presentation gate.

Qt Quick engines are deliberately process-scoped here.  Repeatedly creating
and destroying a QQuickWindow inside the long-lived pytest GUI application can
leave deferred scene-graph work behind on Windows, which made this otherwise
small contract test order-dependent.
"""

import json
import os
import sys
from pathlib import Path


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    Property,
    QMetaObject,
    QUrl,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_FILE = PROJECT_ROOT / "qml" / "CompanionBubble.qml"


class PresentationController(QObject):
    bubbleChanged = Signal()
    busyChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.suppression_calls: list[bool] = []
        self.explicit_dismissals = 0

    @Property("QVariantMap", notify=bubbleChanged)
    def bubble(self) -> dict[str, object]:
        # Never expose a native window during this regression probe.
        return {"id": "synthetic", "visible": False, "summary": "synthetic"}

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return False

    @Slot(bool)
    def setPresentationSuppressed(self, value: bool) -> None:
        self.suppression_calls.append(bool(value))

    @Slot()
    def dismissExplicit(self) -> None:
        self.explicit_dismissals += 1


def settle(app: QApplication) -> None:
    app.processEvents()
    QTest.qWait(5)
    app.processEvents()


def flush_deferred_deletes(app: QApplication) -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def main() -> int:
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    engine = QQmlEngine()
    controller = PresentationController()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_FILE)))
    if component.status() == QQmlComponent.Status.Error:
        raise RuntimeError("\n".join(error.toString() for error in component.errors()))

    window = component.createWithInitialProperties(
        {"controller": controller, "suppressed": False}
    )
    if window is None:
        raise RuntimeError(
            "\n".join(error.toString() for error in component.errors())
            or "CompanionBubble.qml did not load"
        )
    if not isinstance(window, QQuickWindow):
        raise RuntimeError("CompanionBubble.qml must remain a QQuickWindow")

    settle(app)
    initial_hidden = not window.isVisible()
    window.setProperty("suppressed", True)
    settle(app)
    window.setProperty("suppressed", False)
    settle(app)

    invoked = QMetaObject.invokeMethod(
        window, "dismissExplicitly", Qt.ConnectionType.DirectConnection
    )
    settle(app)
    report = {
        "passed": bool(
            controller.suppression_calls == [False, True, False]
            and initial_hidden
            and invoked
            and controller.explicit_dismissals == 1
        ),
        "suppressionCalls": controller.suppression_calls,
        "initialHidden": initial_hidden,
        "dismissInvoked": bool(invoked),
        "explicitDismissals": controller.explicit_dismissals,
    }
    # CompanionBubble owns Qt.callLater work and short size animations.  Keep
    # every dependency alive until those queues settle, then destroy the root,
    # component, engine and controller in dependency order.  processEvents()
    # alone does not guarantee delivery of DeferredDelete events.
    window.setVisible(False)
    window.close()
    window.releaseResources()
    QTest.qWait(220)
    app.processEvents()
    window.deleteLater()
    flush_deferred_deletes(app)
    component.deleteLater()
    flush_deferred_deletes(app)
    engine.clearComponentCache()
    engine.collectGarbage()
    engine.deleteLater()
    flush_deferred_deletes(app)
    controller.deleteLater()
    flush_deferred_deletes(app)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
