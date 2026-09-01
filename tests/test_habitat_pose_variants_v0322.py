from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from lilies.core.pet_habitat import (
    HOST_HABITAT_STRATEGIES,
    choose_habitat_candidate,
)
from lilies.core.window_catalog import WindowRect


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORK = WindowRect(0, 0, 1920, 1040)


def candidate(
    rect: WindowRect,
    *,
    title_bar_height: float = 32,
    maximized: bool = False,
    previous=None,
):
    return choose_habitat_candidate(
        rect,
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=title_bar_height,
        maximized=maximized,
        previous_profile=previous.profile if previous else "",
        previous_size_class=previous.size_class if previous else "",
        previous_side=previous.side if previous else "",
        previous_pose_variant=previous.pose_variant if previous else "",
    )


def test_host_geometry_has_six_explicit_presentation_strategies() -> None:
    cases = {
        "tiny": candidate(WindowRect(180, 240, 500, 450)),
        "small": candidate(WindowRect(180, 240, 580, 510)),
        "medium": candidate(WindowRect(180, 240, 1020, 720)),
        "large": candidate(WindowRect(80, 220, 1800, 1000)),
        "maximized": candidate(WORK, maximized=True),
        "edge": candidate(
            WindowRect(180, 240, 1180, 850), title_bar_height=18
        ),
    }

    assert tuple(cases) == HOST_HABITAT_STRATEGIES
    for expected_strategy, result in cases.items():
        assert result.strategy == expected_strategy
        assert result.to_dict()["habitatStrategy"] == expected_strategy

    assert cases["tiny"].pose_variant == "micro-corner-grip"
    assert cases["small"].pose_variant == "compact-title-curl"
    assert cases["medium"].pose_variant == "window-perch-tucked"
    assert cases["large"].pose_variant == "wide-window-sprawl"
    assert cases["maximized"].pose_variant == "screen-edge-watch"
    assert cases["edge"].pose_variant == "caption-side-lean"


@pytest.mark.parametrize(
    ("initial_rect", "inside_rect", "outside_rect", "variant", "replacement"),
    (
        (
            WindowRect(180, 240, 580, 510),
            WindowRect(180, 240, 620, 560),
            WindowRect(180, 240, 650, 590),
            "compact-title-curl",
            "title-sit-balance",
        ),
        (
            WindowRect(180, 240, 1020, 720),
            WindowRect(180, 240, 1110, 770),
            WindowRect(180, 240, 1150, 800),
            "window-perch-tucked",
            "window-perch",
        ),
        (
            WindowRect(60, 220, 1860, 980),
            WindowRect(110, 220, 1810, 980),
            WindowRect(130, 220, 1800, 980),
            "panoramic-prone",
            "wide-window-sprawl",
        ),
    ),
)
def test_variant_thresholds_have_resize_hysteresis(
    initial_rect: WindowRect,
    inside_rect: WindowRect,
    outside_rect: WindowRect,
    variant: str,
    replacement: str,
) -> None:
    initial = candidate(initial_rect)
    inside = candidate(inside_rect, previous=initial)
    outside = candidate(outside_rect, previous=inside)

    assert initial.pose_variant == variant
    assert inside.pose_variant == variant
    assert outside.pose_variant == replacement


def test_shipped_habitat_variant_manifest_is_bounded_and_complete() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "themes" / "first-encounter" / "theme.json").read_text(
            encoding="utf-8"
        )
    )
    character = manifest["character"]
    variants = character["habitatPoseVariants"]
    declared_poses = set(character["poses"])
    required_variants = {
        "desktop-prayer",
        "pointer-safe-rest",
        "micro-corner-grip",
        "compact-title-curl",
        "title-sit-balance",
        "portrait-title-watch",
        "window-perch-tucked",
        "window-perch",
        "wide-window-sprawl",
        "panoramic-prone",
        "screen-edge-watch",
        "cautious-return",
        "caption-side-lean",
        "edge-listen",
    }

    assert required_variants <= set(variants)
    assert {variants[name]["strategy"] for name in required_variants} >= set(
        HOST_HABITAT_STRATEGIES
    )
    for name in required_variants:
        definition = variants[name]
        assert definition["strategy"] in {*HOST_HABITAT_STRATEGIES, "desktop"}
        assert definition["basePose"] in declared_poses
        assert 0.40 <= float(definition["heightFactor"]) <= 1.0
        layered = definition["layered"]
        assert -82.0 <= float(layered["bodyRotation"]) <= 82.0
        assert -18.0 <= float(layered["headRotation"]) <= 18.0
        assert -12.0 <= float(layered["torsoRotation"]) <= 12.0
        assert -12.0 <= float(layered["skirtRotation"]) <= 12.0
        for offset_name in ("headOffset", "torsoOffset", "skirtOffset"):
            values = layered[offset_name]
            assert len(values) == 2
            assert all(-0.12 <= float(value) <= 0.12 for value in values)
        artwork = definition["artwork"]
        assert -2.0 <= float(artwork["rotationBias"]) <= 2.0
        assert 0.30 <= float(artwork["motionGain"]) <= 1.30

    assert {
        definition.get("optionalArtworkAsset")
        for definition in variants.values()
        if definition.get("optionalArtworkAsset")
    } == {
        "poseEdgeLeanV1",
        "poseMicroCornerGripV1",
        "poseWindowProneV2",
        "poseWindowDangleV1",
        "poseWideWindowSprawlV1",
    }
    assert all(
        definition.get("optionalArtworkEnabled") is False
        for definition in variants.values()
        if definition.get("optionalArtworkAsset")
    )
    assert variants["micro-corner-grip"]["optionalArtworkAsset"] == (
        "poseMicroCornerGripV1"
    )
    assert {
        variants[name]["optionalArtworkAsset"]
        for name in ("window-perch-tucked", "window-perch")
    } == {"poseWindowDangleV1"}
    assert all(
        variants[name]["proceduralLayeredFallback"] is True
        for name in (
            "micro-corner-grip",
            "compact-title-curl",
            "window-perch-tucked",
            "window-perch",
            "wide-window-sprawl",
            "panoramic-prone",
            "screen-edge-watch",
            "cautious-return",
            "caption-side-lean",
        )
    )
    assert {
        variants[name]["optionalArtworkAsset"]
        for name in ("wide-window-sprawl", "panoramic-prone")
    } == {"poseWideWindowSprawlV1"}
    assert "optionalArtworkAsset" not in variants["edge-listen"]
    assert "large-edge-lean" not in variants


def test_qml_variants_transform_layers_around_contact_and_fail_open_assets() -> None:
    source = (PROJECT_ROOT / "qml" / "V03PetBody.qml").read_text(
        encoding="utf-8"
    )

    assert "character.habitatPoseVariants" in source
    assert "habitatLayout.habitatStrategy" in source
    assert 'objectName: "petLayeredProfileRotation"' in source
    assert "root.renderedContactX" in source
    assert "root.renderedContactY" in source
    assert source.count("mirror: root.habitatMirror") >= 3
    assert "layeredSupportCordPoint" in source
    assert "figureFrame.mapToItem" in source
    assert "habitatVariantDefinition.optionalArtworkAsset" in source
    assert "Boolean(habitatVariantDefinition.optionalArtworkEnabled)" in source
    assert 'String(appBackend.assetUrl(optionalKey)) !== ""' in source
    assert "habitatVariantDefinition.proceduralLayeredFallback" in source
    assert 'case "title-curl"' in source
    assert 'case "perch-tuck"' in source
    assert 'case "perch-drift"' in source


def test_layered_outfit_variants_keep_contact_and_rope_inside_offscreen() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["PYTHONUTF8"] = "1"
    probe = r'''
import json
import math
import sys
from pathlib import Path

root = Path.cwd()
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "scripts"))

from PySide6.QtCore import QPointF, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.core.pet_habitat import PetHabitatController
from verify_habitat_ui import HARNESS, HabitatHarnessBackend

QQuickWindow.setDefaultAlphaBuffer(True)
app = QApplication([])
backend = HabitatHarnessBackend()
backend._wardrobe_state = {"current": {"outfit_id": "home-cardigan"}}
engine = QQmlApplicationEngine()
engine.rootContext().setContextProperty("backend", backend)
engine.loadData(HARNESS, QUrl.fromLocalFile(str(root / "qml" / "HabitatHarness.qml")))
if not engine.rootObjects():
    raise RuntimeError("habitat variant harness failed to load")
window = engine.rootObjects()[0]
pet = window.findChild(QQuickItem, "habitatPet")
frame = window.findChild(QQuickItem, "petFigureFrame")
if pet is None or frame is None:
    raise RuntimeError("layered habitat items are missing")

habitat = PetHabitatController(stable_seconds=0, pet_width=150, pet_height=250)
work = {"left": 0, "top": 0, "right": 1920, "bottom": 1040}
cases = (
    ("small", {"left": 180, "top": 240, "right": 580, "bottom": 510}, 32, False),
    ("medium", {"left": 180, "top": 240, "right": 1020, "bottom": 720}, 32, False),
    ("large", {"left": 60, "top": 220, "right": 1860, "bottom": 980}, 32, False),
    ("maximized", work, 32, True),
)
results = {}
for index, (name, rect, title_height, maximized) in enumerate(cases):
    habitat.update_foreground({
        "handle": 88000 + index,
        "rect": rect,
        "workArea": work,
        "visible": True,
        "minimized": False,
        "maximized": maximized,
        "dpi": 96,
        "titleBarHeight": title_height,
    })
    backend.set_habitat_state(habitat.status())
    QTest.qWait(360)
    state = dict(backend.habitatState)
    contact = frame.mapToItem(
        pet,
        QPointF(
            float(frame.width()) * float(pet.property("renderedContactX")),
            float(frame.height()) * float(pet.property("renderedContactY")),
        ),
    )
    expected_x = float(pet.width()) * float(pet.property("renderedAnchorNormX"))
    expected_y = float(pet.height()) * float(pet.property("renderedAnchorNormY"))
    cord = pet.property("supportCordPoint")
    bounds = pet.property("layeredFigureBounds")
    finite = all(math.isfinite(value) for value in (
        float(contact.x()), float(contact.y()), float(cord.x()), float(cord.y()),
        float(bounds.x()), float(bounds.y()), float(bounds.width()), float(bounds.height()),
    ))
    inside = (
        float(bounds.x()) >= -0.1
        and float(bounds.y()) >= -0.1
        and float(bounds.x()) + float(bounds.width()) <= float(pet.width()) + 0.1
        and float(bounds.y()) + float(bounds.height()) <= float(pet.height()) + 0.1
        and 0.0 <= float(cord.x()) <= float(pet.width())
        and 0.0 <= float(cord.y()) <= float(pet.height())
    )
    results[name] = {
        "strategy": str(pet.property("habitatStrategy")),
        "variant": str(pet.property("habitatPoseVariant")),
        "rotation": float(pet.property("renderedLayeredBodyRotation")),
        "usesArtwork": bool(pet.property("usesPoseArtwork")),
        "anchorError": max(abs(float(contact.x()) - expected_x), abs(float(contact.y()) - expected_y)),
        "finite": finite,
        "inside": inside,
    }

expected = {"small": "small", "medium": "medium", "large": "large", "maximized": "maximized"}
passed = all(
    value["strategy"] == expected[name]
    and value["usesArtwork"] is False
    and value["anchorError"] <= 0.15
    and value["finite"]
    and value["inside"]
    for name, value in results.items()
)
passed = passed and abs(results["small"]["rotation"] - results["large"]["rotation"]) >= 40.0
print(json.dumps({"passed": passed, "results": results}, ensure_ascii=False))
raise SystemExit(0 if passed else 1)
'''
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["passed"] is True
