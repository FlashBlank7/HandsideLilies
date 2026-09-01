from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine


ROOT = Path(__file__).resolve().parents[1]


def snapshot(item: object) -> dict[str, object]:
    return {
        "pose": str(item.property("resolvedPose")),
        "context": str(item.property("contextKind")),
        "highMotion": bool(item.property("requiresHighMotion")),
    }


def set_map(item: object, name: str, value: dict[str, object]) -> None:
    if not item.setProperty(name, value):
        raise RuntimeError(f"failed to set {name}")
    QGuiApplication.processEvents()


def main() -> int:
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    engine = QQmlEngine()
    component = QQmlComponent(
        engine,
        QUrl.fromLocalFile(str(ROOT / "qml" / "V03PetPoseResolver.qml")),
    )
    if component.isError():
        raise RuntimeError("; ".join(error.toString() for error in component.errors()))
    item = component.create()
    if item is None:
        raise RuntimeError("pose resolver did not instantiate")

    item.setProperty("equippedPose", "resting")
    QGuiApplication.processEvents()
    outcome: dict[str, object] = {"default": snapshot(item)}

    set_map(item, "focusStatus", {"active": True, "paused": False, "state": "running"})
    outcome["focus"] = snapshot(item)

    set_map(item, "readingStatus", {"active": True, "paused": False})
    outcome["reading"] = snapshot(item)

    set_map(item, "companionBubble", {"visible": True, "id": "bubble-1"})
    outcome["companion"] = snapshot(item)

    set_map(item, "selectionBubble", {"visible": True})
    outcome["selection"] = snapshot(item)

    item.setProperty("chatOpen", True)
    QGuiApplication.processEvents()
    outcome["chat"] = snapshot(item)

    item.setProperty("chatOpen", False)
    set_map(item, "selectionBubble", {"visible": False})
    set_map(item, "companionBubble", {"visible": False})
    set_map(item, "readingStatus", {"active": False, "paused": False})
    set_map(item, "focusStatus", {"active": False, "paused": False, "state": "idle"})

    habitat_cases = {
        "edge": "edge-peek",
        "edgeLive": "edge-peek-live",
        "topSpaceListen": "listening-live",
        "title": "title-sit",
        "perch": "perch-top",
        "unknown": "future-untrusted-pose",
    }
    outcome["habitat"] = {}
    for label, pose in habitat_cases.items():
        set_map(item, "habitatState", {"attached": True, "pose": pose})
        outcome["habitat"][label] = snapshot(item)

    set_map(item, "habitatState", {"attached": False, "pose": ""})
    set_map(item, "focusStatus", {"active": True, "paused": True, "state": "paused"})
    outcome["pausedFocus"] = snapshot(item)

    expected = {
        "default": {"pose": "resting", "context": "equipped", "highMotion": False},
        "focus": {"pose": "focus-watch", "context": "focus", "highMotion": False},
        "reading": {"pose": "reading", "context": "reading", "highMotion": False},
        "companion": {"pose": "presenting", "context": "companion", "highMotion": False},
        "selection": {"pose": "reading", "context": "selection", "highMotion": False},
        "chat": {"pose": "listening-live", "context": "chat", "highMotion": False},
        "pausedFocus": {"pose": "resting", "context": "equipped", "highMotion": False},
    }
    expected_habitat = {
        "edge": "edge-peek-live",
        "edgeLive": "edge-peek-live",
        "topSpaceListen": "listening-live",
        "title": "title-sit",
        "perch": "perch-prone",
        "unknown": "resting",
    }
    passed = all(outcome[key] == value for key, value in expected.items())
    passed = passed and all(
        outcome["habitat"][key]
        == {"pose": pose, "context": "habitat", "highMotion": False}
        for key, pose in expected_habitat.items()
    )
    outcome["platform"] = str(application.platformName())
    outcome["passed"] = bool(passed and outcome["platform"] == "offscreen")
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    item.deleteLater()
    engine.deleteLater()
    QGuiApplication.processEvents()
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
