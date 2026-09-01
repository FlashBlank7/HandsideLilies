from __future__ import annotations

"""Exercise Lilies-owned transient QQuickWindows without observing the desktop.

The verifier uses a smoke Backend, a temporary database and synthetic bubble
data.  It deliberately does not enumerate native windows or capture pixels.
Run it in a fresh process so graphics-driver worker pools can be measured from
a cold start, for example with either the software or d3d11 Qt Quick backend.
"""

import gc
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.paths import qml_path
from verify_compact_resources import _memory_snapshot


def _settle(milliseconds: int = 650) -> None:
    QTest.qWait(milliseconds)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    QApplication.processEvents()


def _synthetic_bubble(controller: Any, serial: int) -> None:
    now = datetime.now(UTC)
    controller._bubble = {
        "id": f"synthetic-resource-{serial}",
        "visible": True,
        "category": "science",
        "summary": "Synthetic resource lifecycle card. No screen content.",
        "detail": "Synthetic detail only; no model or network request.",
        "source": {},
        "actions": [],
        "sceneLabel": "synthetic",
        "createdAt": now.isoformat(),
        "expiresAt": (now + timedelta(minutes=4)).isoformat(),
    }
    controller.bubbleChanged.emit()


def _delta(last: dict[str, Any], first: dict[str, Any], key: str) -> float | None:
    left = last.get(key)
    right = first.get(key)
    if left is None or right is None:
        return None
    return round(float(left) - float(right), 2)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Offscreen+D3D11 has no presenting swap chain on this Qt build and grows
    # staging memory even while an untouched pet window merely breathes.  Use
    # the deterministic software scene graph by default; D3D11 remains an
    # explicit diagnostic opt-in, never a release gate.
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")
    temporary = tempfile.TemporaryDirectory(prefix="lilies-transient-resources-")
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
    desktop = engine.rootObjects()[0]
    pet = desktop.findChild(QQuickWindow, "petWindow")
    panel = desktop.findChild(QQuickWindow, "v03WorkPanel")
    bubble = desktop.findChild(QQuickWindow, "companionBubbleWindow")
    if not all((pet, panel, bubble)):
        raise RuntimeError("expected Lilies windows were not created")

    states: list[dict[str, Any]] = []

    def snapshot(label: str) -> None:
        counters = _memory_snapshot()
        counters.update(
            {
                "label": label,
                "petVisible": pet.isVisible(),
                "panelVisible": panel.isVisible(),
                "bubbleVisible": bubble.isVisible(),
            }
        )
        states.append(counters)

    try:
        backend.setShellMode("compact")
        backend.setWorkPanelOpen(False)
        backend.companion.dismiss()
        _settle(900)
        snapshot("cold")

        for serial in range(3):
            backend.openWorkPanelSection("work")
            _settle()
            snapshot(f"panel-show-{serial + 1}")
            backend.setWorkPanelOpen(False)
            _settle(1200)
            snapshot(f"panel-hide-{serial + 1}")

        for serial in range(3):
            _synthetic_bubble(backend.companion, serial)
            _settle()
            snapshot(f"bubble-show-{serial + 1}")
            backend.companion.dismiss()
            _settle(1200)
            snapshot(f"bubble-hide-{serial + 1}")

        backend.openWorkPanelSection("world")
        _synthetic_bubble(backend.companion, 9)
        _settle()
        snapshot("both-show")
        backend.setWorkPanelOpen(False)
        backend.companion.dismiss()
        _settle(1600)
        snapshot("both-hide")

        # This is diagnostic only: compare Qt's explicit scene-graph release
        # with ordinary hiding, then prove both windows still reopen.
        panel.releaseResources()
        bubble.releaseResources()
        _settle(2400)
        snapshot("after-releaseResources")
        backend.openWorkPanelSection("work")
        _synthetic_bubble(backend.companion, 10)
        _settle()
        snapshot("reopen-after-release")
        reopened = panel.isVisible() and bubble.isVisible()
        backend.setWorkPanelOpen(False)
        backend.companion.dismiss()
        _settle(1400)
        snapshot("final-hide")

        hidden = [state for state in states if "hide" in str(state["label"])]
        plateau_start = next(
            state for state in states if state["label"] == "panel-hide-1"
        )
        final = states[-1]
        thread_values = [int(state["threads"]) for state in hidden if state["threads"] is not None]
        handle_values = [
            int(state["processHandles"])
            for state in hidden
            if state["processHandles"] is not None
        ]
        thread_span = max(thread_values) - min(thread_values) if thread_values else None
        handle_span = max(handle_values) - min(handle_values) if handle_values else None
        resource_plateau = (
            (thread_span is None or thread_span <= 4)
            and (handle_span is None or handle_span <= 12)
            and (
                _delta(final, plateau_start, "privateMiB") is None
                or _delta(final, plateau_start, "privateMiB") <= 32.0
            )
        )
        all_hidden = all(
            not state["panelVisible"] and not state["bubbleVisible"]
            for state in hidden
        )
        report = {
            "passed": bool(resource_plateau and all_hidden and reopened),
            "platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "quickBackend": os.environ.get("QT_QUICK_BACKEND", "default"),
            "rhiBackend": os.environ.get("QSG_RHI_BACKEND", "default"),
            "resourcePlateau": resource_plateau,
            "allHiddenAfterClose": all_hidden,
            "reopenedAfterRelease": reopened,
            "hiddenThreadSpan": thread_span,
            "hiddenHandleSpan": handle_span,
            "privateGrowthAfterFirstPanelMiB": _delta(
                final, plateau_start, "privateMiB"
            ),
            "states": states,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    finally:
        backend.shutdown()
        engine.deleteLater()
        _settle(300)
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
