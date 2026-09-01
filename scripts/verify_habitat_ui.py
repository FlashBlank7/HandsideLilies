from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

# Never let this native-window verifier fall through to the user's desktop.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QObject, QPointF, Property, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.core.pet_habitat import PetHabitatController
from verify_pose_assets import validate_pose_assets


class HabitatHarnessBackend(QObject):
    habitatChanged = Signal()
    wardrobeChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        manifest_path = PROJECT_ROOT / "themes" / "first-encounter" / "theme.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._manifest = manifest
        theme_root = manifest_path.parent
        self._asset_urls = {
            str(key): QUrl.fromLocalFile(str(path))
            for key, value in dict(manifest.get("assets", {})).items()
            if (path := (theme_root / value).resolve()).is_file()
        }
        self._habitat_state: dict[str, object] = {}
        self._wardrobe_state = {"current": {"outfit_id": "first-encounter"}}

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, object]:
        return dict(self._habitat_state)

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, object]:
        return dict(self._manifest)

    @Property("QVariantMap", notify=wardrobeChanged)
    def wardrobeState(self) -> dict[str, object]:
        return dict(self._wardrobe_state)

    @Slot(str, result=QUrl)
    def assetUrl(self, key: str) -> QUrl:
        return self._asset_urls.get(str(key), QUrl())

    def set_habitat_state(self, value: dict[str, object]) -> None:
        self._habitat_state = dict(value)
        self.habitatChanged.emit()


HARNESS = b"""
import QtQuick
import QtQuick.Window

Window {
    width: 700
    height: 660
    visible: true
    color: "transparent"

    V03PetBody {
        objectName: "habitatPet"
        anchors.fill: parent
        appBackend: backend
        characterHeight: 400
        pose: {
            var habitatPose = String(backend.habitatState.pose || "")
            if (backend.habitatState.attached && habitatPose.indexOf("edge-peek") === 0)
                return "edge-peek-live"
            if (backend.habitatState.attached && habitatPose === "title-sit")
                return "title-sit"
            if (backend.habitatState.attached && habitatPose === "perch-top")
                return "perch-prone"
            return "idle-prayer"
        }
    }
}
"""


def main() -> int:
    asset_gate = validate_pose_assets(
        PROJECT_ROOT / "themes" / "first-encounter" / "theme.json",
        PROJECT_ROOT / "qml" / "V03PetBody.qml",
    )
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    backend = HabitatHarnessBackend()
    habitat = PetHabitatController(
        stable_seconds=0,
        pet_width=150,
        pet_height=250,
    )
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.loadData(
        HARNESS,
        QUrl.fromLocalFile(str(PROJECT_ROOT / "qml" / "HabitatHarness.qml")),
    )
    if not engine.rootObjects():
        raise RuntimeError("habitat QML harness failed to load")
    window = engine.rootObjects()[0]
    pet = window.findChild(QQuickItem, "habitatPet")
    frame = window.findChild(QQuickItem, "petPoseArtworkFrame")
    artwork_image = window.findChild(QQuickItem, "petPoseArtworkImage")
    outgoing = window.findChild(QQuickItem, "petPoseArtworkOutgoingImage")
    profile_scale = window.findChild(QObject, "petPoseProfileScale")
    profile_rotation = window.findChild(QObject, "petPoseProfileRotation")
    if (
        pet is None
        or frame is None
        or artwork_image is None
        or outgoing is None
        or profile_scale is None
        or profile_rotation is None
    ):
        raise RuntimeError("habitat pose layers failed to load")

    work_area = {"left": 0, "top": 0, "right": 1920, "bottom": 1040}
    cases = (
        ("offscreen", {"left": -820, "top": 220, "right": 180, "bottom": 780}, 32, False,
         "offscreen-window-edge", "edge-peek-live", "", False),
        ("micro", {"left": 180, "top": 240, "right": 500, "bottom": 450}, 32, False,
         "micro-window-edge", "edge-peek-live", "", False),
        ("small", {"left": 180, "top": 240, "right": 620, "bottom": 560}, 32, False,
         "small-title", "title-sit", "poseTitleSit", False),
        ("portrait", {"left": 300, "top": 160, "right": 980, "bottom": 960}, 32, False,
         "portrait-title", "title-sit", "poseTitleSit", False),
        ("medium", {"left": 180, "top": 240, "right": 1180, "bottom": 850}, 32, False,
         "medium-perch", "perch-prone", "", False),
        ("large", {"left": 80, "top": 260, "right": 1800, "bottom": 1000}, 32, False,
         "large-perch", "perch-prone", "", False),
        ("top-space", {"left": 180, "top": 8, "right": 1180, "bottom": 780}, 32, False,
         "top-space-listen", "edge-peek-live", "poseListeningLive", False),
        ("narrow", {"left": 180, "top": 240, "right": 1180, "bottom": 850}, 18, False,
         "narrow-caption-edge", "edge-peek-live", "", False),
        ("maximized", work_area, 32, True,
         "maximized-edge", "edge-peek-live", "", True),
    )
    expected_presentations = {
        "offscreen-window-edge": ("cautious-return", "cautious-peek", 4.6, 0.70),
        "micro-window-edge": ("micro-corner-grip", "corner-grip", 2.9, 0.74),
        "small-title": ("title-sit-balance", "title-balance", 3.3, 1.0),
        "portrait-title": ("portrait-title-watch", "portrait-listen", 4.7, 1.0),
        "medium-perch": ("window-perch", "perch-breathe", 4.1, 1.0),
        "large-perch": ("wide-window-sprawl", "perch-stretch", 5.0, 1.0),
        "top-space-listen": ("edge-listen", "edge-listen", 4.2, 0.72),
        "narrow-caption-edge": ("caption-side-lean", "caption-lean", 3.8, 0.78),
        "maximized-edge": ("screen-edge-watch", "screen-watch", 5.2, 0.72),
    }
    alpha_assets: dict[str, object] = {}
    for asset_key in (
        "posePerchProne",
        "poseTitleSit",
        "poseEdgePeek",
        "poseListeningLive",
    ):
        asset_path = Path(backend.assetUrl(asset_key).toLocalFile())
        image = QImage(str(asset_path))
        sample = image.scaled(128, 128).convertToFormat(QImage.Format_RGBA8888)
        rgba = bytes(sample.bits()) if not sample.isNull() else b""
        alpha_values = rgba[3::4]
        alpha_assets[asset_key] = {
            "exists": asset_path.is_file(),
            "hasAlphaChannel": image.hasAlphaChannel(),
            "minAlpha": min(alpha_values) if alpha_values else 255,
            "passed": (
                asset_path.is_file()
                and image.hasAlphaChannel()
                and bool(alpha_values)
                and min(alpha_values) < 255
            ),
        }
    results: dict[str, object] = {}
    for index, (
        name,
        rect,
        title_bar_height,
        maximized,
        expected_profile,
        expected_pose,
        expected_artwork,
        expected_mirror,
    ) in enumerate(cases):
        previous_frame_height = float(frame.height())
        habitat.update_foreground(
            {
                "handle": 70000 + index,
                "rect": rect,
                "workArea": work_area,
                "visible": True,
                "minimized": False,
                "maximized": maximized,
                "dpi": 96,
                "titleBarHeight": title_bar_height,
            }
        )
        backend.set_habitat_state(habitat.status())
        immediate_height_jump = float(frame.height()) - previous_frame_height
        transition_started = bool(pet.property("poseTransitionRunning"))
        QTest.qWait(70)

        rendered_anchor_x = float(pet.width()) * float(
            pet.property("renderedAnchorNormX")
        )
        rendered_anchor_y = float(pet.height()) * float(
            pet.property("renderedAnchorNormY")
        )
        rendered_contact_x = float(pet.property("renderedContactX"))
        rendered_contact_y = float(pet.property("renderedContactY"))
        mid_error = [
            float(frame.x()) + float(frame.width()) * rendered_contact_x
            - rendered_anchor_x,
            float(frame.y()) + float(frame.height()) * rendered_contact_y
            - rendered_anchor_y,
        ]
        outgoing_visible = (
            str(frame.property("outgoingSource") or "") != ""
            and float(frame.property("presentationProgress")) < 0.999
        )
        QTest.qWait(270)

        state = dict(backend.habitatState)
        target_anchor_x = float(pet.width()) * float(state["anchorNormX"])
        target_anchor_y = float(pet.height()) * float(state["anchorNormY"])
        final_error = [
            float(frame.x()) + float(frame.width())
            * float(pet.property("renderedContactX"))
            - target_anchor_x,
            float(frame.y()) + float(frame.height())
            * float(pet.property("renderedContactY"))
            - target_anchor_y,
        ]
        profile = str(pet.property("habitatProfile"))
        pose = str(pet.property("pose"))
        artwork = str(pet.property("poseArtworkKey"))
        mirror = bool(pet.property("habitatMirror"))
        pose_variant = str(pet.property("habitatPoseVariant"))
        motion_style = str(pet.property("habitatMotionStyle"))
        motion_period = float(pet.property("habitatMotionPeriod"))
        peek_fraction = float(pet.property("habitatPeekFraction"))
        expected_variant, expected_motion, expected_period, expected_peek = (
            expected_presentations[expected_profile]
        )

        # Freeze the deterministic off-screen scene and sample two points of
        # the profile motion.  Scale and rotation are performed around the
        # declared artwork contact, so that point must remain coincident with
        # the host anchor at every phase—not merely when the animation rests.
        pet.setProperty("paused", True)
        motion_samples: list[dict[str, object]] = []
        max_transformed_anchor_error = 0.0
        max_transformed_frame_overflow = 0.0
        max_cord_endpoint_drift = 0.0
        max_public_bounds_drift = 0.0
        baseline_cord: tuple[float, float] | None = None
        baseline_bounds: tuple[float, float, float, float] | None = None
        for phase in (0.0, math.pi / 2.0):
            pet.setProperty("habitatMotionPhase", phase)
            QTest.qWait(2)
            mapped_contact = frame.mapToItem(
                pet,
                QPointF(
                    float(frame.width()) * float(pet.property("renderedContactX")),
                    float(frame.height()) * float(pet.property("renderedContactY")),
                ),
            )
            phase_anchor_x = float(pet.width()) * float(
                pet.property("renderedAnchorNormX")
            )
            phase_anchor_y = float(pet.height()) * float(
                pet.property("renderedAnchorNormY")
            )
            transformed_error = [
                float(mapped_contact.x()) - phase_anchor_x,
                float(mapped_contact.y()) - phase_anchor_y,
            ]
            max_transformed_anchor_error = max(
                max_transformed_anchor_error,
                *(abs(value) for value in transformed_error),
            )
            mapped_corners = (
                frame.mapToItem(pet, QPointF(0.0, 0.0)),
                frame.mapToItem(pet, QPointF(float(frame.width()), 0.0)),
                frame.mapToItem(pet, QPointF(0.0, float(frame.height()))),
                frame.mapToItem(
                    pet, QPointF(float(frame.width()), float(frame.height()))
                ),
            )
            frame_overflow = max(
                0.0,
                *(-float(point.x()) for point in mapped_corners),
                *(-float(point.y()) for point in mapped_corners),
                *(float(point.x()) - float(pet.width()) for point in mapped_corners),
                *(float(point.y()) - float(pet.height()) for point in mapped_corners),
            )
            max_transformed_frame_overflow = max(
                max_transformed_frame_overflow, frame_overflow
            )
            cord_start = pet.property("cordStart")
            cord = (float(cord_start.x()), float(cord_start.y()))
            bounds = (
                float(pet.property("figureLeft")),
                float(pet.property("figureTop")),
                float(pet.property("figureWidth")),
                float(pet.property("figureHeight")),
            )
            if baseline_cord is None:
                baseline_cord = cord
                baseline_bounds = bounds
            else:
                max_cord_endpoint_drift = max(
                    max_cord_endpoint_drift,
                    abs(cord[0] - baseline_cord[0]),
                    abs(cord[1] - baseline_cord[1]),
                )
                assert baseline_bounds is not None
                max_public_bounds_drift = max(
                    max_public_bounds_drift,
                    *(abs(value - base) for value, base in zip(bounds, baseline_bounds)),
                )
            motion_samples.append(
                {
                    "phase": round(phase, 4),
                    "rotation": round(float(profile_rotation.property("angle")), 4),
                    "scaleX": round(float(profile_scale.property("xScale")), 4),
                    "scaleY": round(float(profile_scale.property("yScale")), 4),
                    "frameOverflow": round(frame_overflow, 4),
                    "cordStart": [round(value, 4) for value in cord],
                    "figureBounds": [round(value, 4) for value in bounds],
                    "anchorError": [round(value, 4) for value in transformed_error],
                }
            )
        pet.setProperty("paused", False)
        motion_changed = any(
            abs(float(motion_samples[1][key]) - float(motion_samples[0][key])) > 0.0005
            for key in ("rotation", "scaleX", "scaleY")
        )
        settled = not bool(pet.property("poseTransitionRunning"))
        passed = (
            profile == expected_profile
            and pose == expected_pose
            and artwork == expected_artwork
            and mirror is expected_mirror
            and pose_variant == expected_variant
            and motion_style == expected_motion
            and abs(motion_period - expected_period) <= 0.001
            and abs(peek_fraction - expected_peek) <= 0.001
            and motion_changed
            and all(
                abs(float(sample[axis]) - 1.0) <= 0.000001
                for sample in motion_samples
                for axis in ("scaleX", "scaleY")
            )
            and max_transformed_anchor_error <= 0.05
            and max_transformed_frame_overflow <= 0.05
            and max_cord_endpoint_drift <= 0.05
            and max_public_bounds_drift <= 0.05
            and max(abs(value) for value in mid_error) <= 1.5
            and max(abs(value) for value in final_error) <= 1.5
            and (index == 0 or abs(immediate_height_jump) <= 3.0)
            and settled
        )
        results[name] = {
            "profile": profile,
            "pose": pose,
            "artwork": artwork,
            "mirror": mirror,
            "poseVariant": pose_variant,
            "motionStyle": motion_style,
            "motionPeriod": round(motion_period, 3),
            "peekFraction": round(peek_fraction, 4),
            "motionSamples": motion_samples,
            "motionChanged": motion_changed,
            "maxTransformedAnchorError": round(max_transformed_anchor_error, 4),
            "maxTransformedFrameOverflow": round(max_transformed_frame_overflow, 4),
            "maxCordEndpointDrift": round(max_cord_endpoint_drift, 4),
            "maxPublicBoundsDrift": round(max_public_bounds_drift, 4),
            "settledFrameSize": [round(float(frame.width()), 3), round(float(frame.height()), 3)],
            "transitionStarted": transition_started,
            "outgoingVisibleMidTransition": outgoing_visible,
            "immediateHeightJump": round(immediate_height_jump, 3),
            "midAnchorError": [round(value, 3) for value in mid_error],
            "finalAnchorError": [round(value, 3) for value in final_error],
            "settled": settled,
            "passed": passed,
        }

    # The wardrobe poses share one audited sheet, but each must keep its own
    # non-destructive clip, aspect ratio, hit bounds and cord anchor at runtime.
    backend.set_habitat_state({})
    QTest.qWait(330)
    sprite_definitions = dict(
        backend.themeManifest["character"]["poseArtworkSpecs"]
        ["poseExpansionSheet"]["sprites"]
    )
    sheet_path = Path(backend.assetUrl("poseExpansionSheet").toLocalFile()).resolve()
    sprite_runtime: dict[str, object] = {}
    previous_cord: tuple[float, float] | None = None
    for pose_id in ("reading", "presenting", "box-support", "resting"):
        before_point = pet.property("supportCordPoint")
        before_cord = (float(before_point.x()), float(before_point.y()))
        pet.setProperty("pose", pose_id)
        # Read synchronously.  qWait(1) is allowed to oversleep by an entire
        # render frame on a loaded Windows worker, which measures legitimate
        # animation progress and mislabels it as an instantaneous snap.
        immediate_point = pet.property("supportCordPoint")
        immediate_cord = (float(immediate_point.x()), float(immediate_point.y()))
        instant_cord_jump = math.hypot(
            immediate_cord[0] - before_cord[0],
            immediate_cord[1] - before_cord[1],
        )
        presentation_started = False
        for _presentation_sample in range(60):
            app.processEvents()
            presentation_started = bool(frame.property("transitionActive"))
            if presentation_started:
                break
            QTest.qWait(5)
        transition_started = presentation_started or bool(
            pet.property("poseTransitionRunning")
        )
        outgoing_visible = (
            str(frame.property("outgoingSource") or "") != ""
            and float(frame.property("presentationProgress")) < 0.999
        )
        sampled_point = pet.property("supportCordPoint")
        previous_cord = (float(sampled_point.x()), float(sampled_point.y()))
        max_cord_step = 0.0
        max_cord_speed = 0.0
        max_sample_gap_ms = 0.0
        previous_sample_at = time.perf_counter()
        # Sample close to a 60 Hz frame.  A 22 ms polling gap could span
        # two rendered frames under CI scheduling and report a false >60 px
        # single-step jump even though the 220 ms transition was continuous.
        for _sample in range(16):
            QTest.qWait(18)
            sampled_at = time.perf_counter()
            sample_gap_ms = max(0.001, (sampled_at - previous_sample_at) * 1000.0)
            max_sample_gap_ms = max(max_sample_gap_ms, sample_gap_ms)
            previous_sample_at = sampled_at
            point = pet.property("supportCordPoint")
            current_cord = (float(point.x()), float(point.y()))
            if previous_cord is not None:
                cord_step = math.hypot(
                    current_cord[0] - previous_cord[0],
                    current_cord[1] - previous_cord[1],
                )
                max_cord_step = max(
                    max_cord_step,
                    cord_step,
                )
                max_cord_speed = max(max_cord_speed, cord_step / sample_gap_ms)
            previous_cord = current_cord
        # QTest.qWait() keeps processing the Qt event loop, but a loaded CI
        # worker may still deschedule this process for more than one nominal
        # sample interval.  Judge velocity against the measured wall-clock gap
        # instead of treating skipped frames as an animation discontinuity.
        # The independent synchronous ``instant_cord_jump`` fence still catches
        # a property-binding snap before the transition clock advances.
        allowed_cord_speed = 5.0
        allowed_cord_step = allowed_cord_speed * max_sample_gap_ms
        definition = dict(sprite_definitions[pose_id])
        expected_rect = [float(value) for value in definition["sourceRect"]]
        clip = pet.property("poseArtworkClipRect")
        image_clip = frame.property("displayedClipRect")
        actual_rect = [float(clip.x()), float(clip.y()), float(clip.width()), float(clip.height())]
        image_rect = [
            float(image_clip.x()), float(image_clip.y()),
            float(image_clip.width()), float(image_clip.height()),
        ]
        source = frame.property("displayedSource")
        source_path = Path(source.toLocalFile()).resolve() if isinstance(source, QUrl) else Path()
        ratio = float(pet.property("poseArtworkAspectRatio"))
        expected_ratio = expected_rect[2] / expected_rect[3]
        cord = pet.property("supportCordPoint")
        cord_inside = (
            float(frame.x()) <= float(cord.x()) <= float(frame.x()) + float(frame.width())
            and float(frame.y()) <= float(cord.y()) <= float(frame.y()) + float(frame.height())
        )
        settled = not bool(pet.property("poseTransitionRunning"))
        pose_passed = (
            str(pet.property("poseArtworkKey")) == "poseExpansionSheet"
            and str(pet.property("poseArtworkSpriteId")) == pose_id
            and all(abs(a - b) <= 0.01 for a, b in zip(actual_rect, expected_rect))
            and all(abs(a - b) <= 0.01 for a, b in zip(image_rect, expected_rect))
            and abs(ratio - expected_ratio) <= 1e-6
            and source_path == sheet_path
            and bool(pet.property("usesPoseArtwork"))
            and transition_started
            and (pose_id == "reading" or outgoing_visible)
            and instant_cord_jump <= 1.5
            and max_cord_speed <= allowed_cord_speed
            and cord_inside
            and settled
        )
        sprite_runtime[pose_id] = {
            "sourceRect": actual_rect,
            "aspectRatio": round(ratio, 6),
            "transitionStarted": transition_started,
            "outgoingVisibleMidTransition": outgoing_visible,
            "maxCordStep": round(max_cord_step, 4),
            "maxCordSpeedPxPerMs": round(max_cord_speed, 4),
            "maxSampleGapMs": round(max_sample_gap_ms, 4),
            "allowedCordStep": round(allowed_cord_step, 4),
            "instantCordJump": round(instant_cord_jump, 4),
            "cordInsideFrame": cord_inside,
            "settled": settled,
            "passed": pose_passed,
        }

    profile_artwork = {
        str(dict(value)["profile"]): str(dict(value)["artwork"])
        for value in results.values()
    }
    unique_artwork = sorted(
        artwork for artwork in set(profile_artwork.values()) if artwork
    )
    procedural_profiles = sorted(
        profile for profile, artwork in profile_artwork.items() if not artwork
    )
    profile_variants = {
        str(dict(value)["profile"]): str(dict(value)["poseVariant"])
        for value in results.values()
    }
    profile_motions = {
        str(dict(value)["profile"]): str(dict(value)["motionStyle"])
        for value in results.values()
    }
    constrained_profiles = (
        "micro-window-edge",
        "offscreen-window-edge",
        "narrow-caption-edge",
        "maximized-edge",
    )
    constrained_signatures = {
        (
            profile_variants[profile],
            profile_motions[profile],
            float(dict(next(
                value for value in results.values()
                if str(dict(value)["profile"]) == profile
            ))["peekFraction"]),
        )
        for profile in constrained_profiles
    }
    # Do not accept unique labels as proof of variety.  The sampled transform,
    # mirror state, frame size and reveal amount must produce a distinct visual
    # presentation for every geometry class.
    visual_signatures = {
        (
            str(dict(value)["artwork"]),
            bool(dict(value)["mirror"]),
            tuple(dict(value)["settledFrameSize"]),
            float(dict(value)["peekFraction"]),
            tuple(
                (
                    float(dict(sample)["rotation"]),
                    float(dict(sample)["scaleX"]),
                    float(dict(sample)["scaleY"]),
                )
                for sample in dict(value)["motionSamples"]
            ),
        )
        for value in results.values()
    }
    coverage = {
        "windowClasses": len(results),
        "profiles": profile_artwork,
        "uniqueArtwork": unique_artwork,
        "uniqueArtworkCount": len(unique_artwork),
        "runtimeVariants": profile_variants,
        "motionStyles": profile_motions,
        "uniqueRuntimeVariantCount": len(set(profile_variants.values())),
        "uniqueVisualSignatureCount": len(visual_signatures),
        "visualSignaturesDistinct": len(visual_signatures) == len(results),
        "constrainedProfilesDistinct": len(constrained_signatures) == 4,
        "contactStableDuringMotion": all(
            float(dict(value)["maxTransformedAnchorError"]) <= 0.05
            for value in results.values()
        ),
        "wholeArtworkScaleDisabled": all(
            abs(float(sample[axis]) - 1.0) <= 0.000001
            for value in results.values()
            for sample in dict(value)["motionSamples"]
            for axis in ("scaleX", "scaleY")
        ),
        "transformedFramesInsidePetWindow": all(
            float(dict(value)["maxTransformedFrameOverflow"]) <= 0.05
            for value in results.values()
        ),
        "cordStableDuringMotion": all(
            float(dict(value)["maxCordEndpointDrift"]) <= 0.05
            for value in results.values()
        ),
        "publicBoundsStableDuringMotion": all(
            float(dict(value)["maxPublicBoundsDrift"]) <= 0.05
            for value in results.values()
        ),
        "proceduralLayeredProfiles": procedural_profiles,
        "microUsesVerifiedFallback": False,
        "microUsesProceduralLayeredFallback": (
            profile_artwork.get("micro-window-edge") == ""
        ),
        "mediumUsesProceduralLayeredFallback": (
            profile_artwork.get("medium-perch") == ""
        ),
        "passed": (
            len(results) == 9
            and len(unique_artwork) == 2
            and procedural_profiles == [
                "large-perch",
                "maximized-edge",
                "medium-perch",
                "micro-window-edge",
                "narrow-caption-edge",
                "offscreen-window-edge",
            ]
            and len(set(profile_variants.values())) == 9
            and len(visual_signatures) == len(results)
            and len(constrained_signatures) == 4
        ),
    }
    passed = (
        bool(asset_gate["passed"])
        and all(bool(dict(value)["passed"]) for value in alpha_assets.values())
        and
        all(bool(dict(value)["passed"]) for value in results.values())
        and bool(coverage["passed"])
        and bool(dict(results["top-space"])["transitionStarted"])
        and bool(dict(results["maximized"])["transitionStarted"])
        and all(bool(dict(value)["passed"]) for value in sprite_runtime.values())
    )
    report = {
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "assetGate": asset_gate,
        "transparentAssets": alpha_assets,
        "profiles": results,
        "poseCoverage": coverage,
        "spritePoses": sprite_runtime,
        "passed": passed,
    }
    report_path = PROJECT_ROOT / "artifacts" / "habitat-pose-coverage.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report["report"] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    app.quit()
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
