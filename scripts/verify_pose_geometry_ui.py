from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QJSValue, QQmlApplicationEngine, QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


POSE_IDS = ("reading", "presenting", "box-support", "resting")
VIEWPORTS = (
    ("extreme-narrow", 144, 640),
    ("extreme-short", 640, 144),
    ("tiny", 144, 128),
    ("emergency-compact", 168, 158.4),
    ("standard-compact", 385, 363),
    ("large-compact", 1120, 1056),
)


class GeometryBackend(QObject):
    habitatChanged = Signal()
    wardrobeChanged = Signal()

    def __init__(self, manifest: dict[str, object], theme_root: Path) -> None:
        super().__init__()
        self._manifest = dict(manifest)
        self._asset_urls = {
            str(key): QUrl.fromLocalFile(str((theme_root / str(value)).resolve()))
            for key, value in dict(manifest.get("assets", {})).items()
        }

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, object]:
        return {}

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, object]:
        return dict(self._manifest)

    @Property("QVariantMap", notify=wardrobeChanged)
    def wardrobeState(self) -> dict[str, object]:
        return {"current": {"outfit_id": "first-encounter"}}

    @Slot(str, result=QUrl)
    def assetUrl(self, key: str) -> QUrl:
        return self._asset_urls.get(str(key), QUrl())


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    id: geometryWindow
    objectName: "poseGeometryWindow"
    width: 385
    height: 363
    // The platform is forced to offscreen by the verifier, so this drives
    // normal QQuick polish/layout without mapping a desktop window.
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "geometryReading"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "reading"
        paused: true
        visible: false
    }
    V03PetBody {
        objectName: "geometryPresenting"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "presenting"
        paused: true
        visible: false
    }
    V03PetBody {
        objectName: "geometryBoxSupport"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "box-support"
        paused: true
        visible: false
    }
    V03PetBody {
        objectName: "geometryResting"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "resting"
        paused: true
        visible: false
    }
    V03PetBody {
        objectName: "geometryTransition"
        anchors.fill: parent
        appBackend: backend
        characterHeight: height * 2.0 / 3.0
        pose: "reading"
        paused: true
        visible: false
    }
}
"""


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def _qml_bool(item: QObject, expression_source: str) -> bool:
    context = QQmlEngine.contextForObject(item)
    expression = QQmlExpression(context, item, expression_source)
    result = expression.evaluate()
    if expression.hasError():
        raise RuntimeError(expression.error().toString())
    # PySide 6.8+ returns (value, isUndefined) from QQmlExpression.evaluate;
    # older releases return the value directly.
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, QJSValue):
        return result.toBool()
    return bool(result)


def _point(value: object) -> tuple[float, float]:
    return float(value.x()), float(value.y())


def _frame(pet: QObject) -> QQuickItem:
    result = pet.findChild(QQuickItem, "petPoseArtworkFrame")
    if result is None:
        raise RuntimeError("pose artwork frame did not load")
    return result


def _pose_result(
    pet: QQuickItem,
    frame: QQuickItem,
    sprite: dict[str, object],
    viewport_width: float,
    viewport_height: float,
) -> dict[str, object]:
    left = float(frame.x())
    top = float(frame.y())
    width = float(frame.width())
    height = float(frame.height())
    ratio = float(pet.property("poseArtworkAspectRatio"))
    hit_rect = pet.property("poseArtworkHitRect")
    hit_left = left + float(hit_rect.x()) * width
    hit_top = top + float(hit_rect.y()) * height
    hit_right = hit_left + float(hit_rect.width()) * width
    hit_bottom = hit_top + float(hit_rect.height()) * height
    cord_x, cord_y = _point(pet.property("supportCordPoint"))
    cord_anchor = [float(value) for value in sprite["cordAnchor"]]
    expected_cord = [left + width * cord_anchor[0], top + height * cord_anchor[1]]
    mask_center_x = (hit_left + hit_right) / 2.0
    mask_center_y = (hit_top + hit_bottom) / 2.0
    center_hit = _qml_bool(
        pet,
        f"containsCharacterPoint({mask_center_x:.9f}, {mask_center_y:.9f})",
    )
    transparent_corner_hit = _qml_bool(
        pet,
        f"containsCharacterPoint({left:.9f}, {top:.9f})",
    )
    outside_hit = _qml_bool(
        pet,
        f"containsCharacterPoint({left - 2.0:.9f}, {top - 2.0:.9f})",
    )
    geometry_finite = _finite(
        left,
        top,
        width,
        height,
        ratio,
        hit_left,
        hit_top,
        hit_right,
        hit_bottom,
        cord_x,
        cord_y,
    )
    frame_inside = (
        left >= -0.1
        and top >= -0.1
        and left + width <= viewport_width + 0.1
        and top + height <= viewport_height + 0.1
    )
    hit_inside = (
        hit_left >= left - 0.1
        and hit_top >= top - 0.1
        and hit_right <= left + width + 0.1
        and hit_bottom <= top + height + 0.1
    )
    cord_inside_mask = (
        hit_left - 0.1 <= cord_x <= hit_right + 0.1
        and hit_top - 0.1 <= cord_y <= hit_bottom + 0.1
    )
    exact_aspect = abs(width / max(0.001, height) - ratio) <= 0.001
    cord_error = max(abs(cord_x - expected_cord[0]), abs(cord_y - expected_cord[1]))
    passed = (
        geometry_finite
        and width > 0
        and height > 0
        and frame_inside
        and hit_inside
        and cord_inside_mask
        and exact_aspect
        and cord_error <= 0.05
        and center_hit
        and not transparent_corner_hit
        and not outside_hit
    )
    return {
        "frame": [round(left, 4), round(top, 4), round(width, 4), round(height, 4)],
        "frameInside": frame_inside,
        "exactAspect": exact_aspect,
        "hitBounds": [
            round(hit_left, 4),
            round(hit_top, 4),
            round(hit_right, 4),
            round(hit_bottom, 4),
        ],
        "hitInsideFrame": hit_inside,
        "maskCenterHit": center_hit,
        "transparentCornerHit": transparent_corner_hit,
        "outsideHit": outside_hit,
        "cord": [round(cord_x, 4), round(cord_y, 4)],
        "cordInsideMask": cord_inside_mask,
        "cordAnchorError": round(cord_error, 4),
        "passed": passed,
    }


def main() -> int:
    manifest_path = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sprites = dict(
        manifest["character"]["poseArtworkSpecs"]["poseExpansionSheet"]["sprites"]
    )
    app = QApplication([])
    QQuickWindow.setDefaultAlphaBuffer(True)
    backend = GeometryBackend(manifest, manifest_path.parent)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(
        HARNESS,
        QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "PoseGeometryHarness.qml")),
    )
    if not engine.rootObjects():
        raise RuntimeError("pose geometry QML harness failed to load")
    window = engine.rootObjects()[0]
    object_names = {
        "reading": "geometryReading",
        "presenting": "geometryPresenting",
        "box-support": "geometryBoxSupport",
        "resting": "geometryResting",
    }
    pets: dict[str, QQuickItem] = {}
    frames: dict[str, QQuickItem] = {}
    for pose_id, object_name in object_names.items():
        pet = window.findChild(QQuickItem, object_name)
        if pet is None:
            raise RuntimeError(f"pose item did not load: {pose_id}")
        pets[pose_id] = pet
        frames[pose_id] = _frame(pet)

    # V03PetBody now keeps its layered fallback authoritative until an
    # asynchronous artwork slot reports Image.Ready.  Geometry/mask sampling
    # must therefore wait for the same committed presentation that a visible
    # runtime window would use, rather than inspecting an eight-millisecond
    # loading frame.
    for _ready_sample in range(100):
        app.processEvents()
        if all(bool(frame.property("hasReadyPresentation")) for frame in frames.values()) \
                and all(not bool(pet.property("poseTransitionRunning")) for pet in pets.values()):
            break
        QTest.qWait(10)
    if not all(bool(frame.property("hasReadyPresentation")) for frame in frames.values()):
        raise RuntimeError("pose artwork buffers did not become ready")

    viewport_results: list[dict[str, object]] = []
    for name, width, height in VIEWPORTS:
        window.setWidth(round(width))
        window.setHeight(round(height))
        app.processEvents()
        QTest.qWait(8)
        poses = {
            pose_id: _pose_result(
                pets[pose_id], frames[pose_id], dict(sprites[pose_id]),
                float(window.width()), float(window.height()),
            )
            for pose_id in POSE_IDS
        }
        viewport_results.append(
            {
                "name": name,
                "size": [float(window.width()), float(window.height())],
                "poses": poses,
                "passed": all(bool(result["passed"]) for result in poses.values()),
            }
        )

    # Exercise the animated pose swap separately at the production compact
    # aspect ratio.  The instant endpoint remains pinned while ratio, anchor,
    # mask and clip cross-fade to their next manifest definitions.
    window.setWidth(385)
    window.setHeight(363)
    transition_pet = window.findChild(QQuickItem, "geometryTransition")
    if transition_pet is None:
        raise RuntimeError("transition pose item did not load")
    transition_frame = _frame(transition_pet)
    transition_pet.setProperty("visible", True)
    # The window was resized through every viewport above.  At fractional DPI
    # Qt can deliver the final polish one frame after a nominal 280 ms
    # Behavior duration, which made the first pose swap measure the tail of a
    # prior layout animation as an apparent 10-14 px "instant" cord jump.
    # Wait for the public transition contract, not for an equal wall-clock
    # guess, before taking the baseline.
    for _settle_sample in range(24):
        QTest.qWait(20)
        app.processEvents()
        if not bool(transition_pet.property("poseTransitionRunning")):
            break
    if bool(transition_pet.property("poseTransitionRunning")):
        QTest.qWait(80)
        app.processEvents()
    transitions: list[dict[str, object]] = []
    for pose_id in POSE_IDS[1:]:
        before = _point(transition_pet.property("supportCordPoint"))
        transition_pet.setProperty("pose", pose_id)
        # Measure an actual synchronous discontinuity before letting the Qt
        # animation driver advance.  ``processEvents()`` can consume several
        # frames when the full test suite is busy (especially at fractional
        # DPI), which previously mislabelled 15-25 px of legitimate animated
        # travel as an "instant" jump.  The timed loop below still measures
        # that first rendered frame in ``maxCordStep``.
        immediate = _point(transition_pet.property("supportCordPoint"))
        instant_jump = math.hypot(immediate[0] - before[0], immediate[1] - before[1])
        previous = immediate
        max_step = 0.0
        in_bounds = True
        finite = True
        for _sample in range(18):
            QTest.qWait(18)
            app.processEvents()
            current = _point(transition_pet.property("supportCordPoint"))
            max_step = max(max_step, math.hypot(current[0] - previous[0], current[1] - previous[1]))
            previous = current
            values = (
                float(transition_frame.x()),
                float(transition_frame.y()),
                float(transition_frame.width()),
                float(transition_frame.height()),
                current[0],
                current[1],
            )
            finite = finite and _finite(*values)
            in_bounds = in_bounds and (
                values[0] >= -0.1
                and values[1] >= -0.1
                and values[0] + values[2] <= float(window.width()) + 0.1
                and values[1] + values[3] <= float(window.height()) + 0.1
            )
        sprite = dict(sprites[pose_id])
        settled = _pose_result(
            transition_pet,
            transition_frame,
            sprite,
            float(window.width()),
            float(window.height()),
        )
        passed = (
            instant_jump <= 1.5
            and max_step <= 60.0
            and finite
            and in_bounds
            and settled["passed"]
            and not bool(transition_pet.property("poseTransitionRunning"))
        )
        transitions.append(
            {
                "toPose": pose_id,
                "instantCordJump": round(instant_jump, 4),
                "maxCordStep": round(max_step, 4),
                "finite": finite,
                "frameStayedInside": in_bounds,
                "settled": settled,
                "passed": passed,
            }
        )

    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "qtScaleFactor": os.environ.get("QT_SCALE_FACTOR", "1"),
        "devicePixelRatio": float(window.devicePixelRatio()),
        "viewports": viewport_results,
        "transitions": transitions,
        "passed": (
            all(bool(result["passed"]) for result in viewport_results)
            and all(bool(result["passed"]) for result in transitions)
        ),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    window.setPersistentGraphics(False)
    window.setPersistentSceneGraph(False)
    window.hide()
    window.close()
    window.releaseResources()
    window.deleteLater()
    engine.clearComponentCache()
    engine.collectGarbage()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
