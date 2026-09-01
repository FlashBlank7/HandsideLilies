from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# This verifier must never fall through to the caller's desktop platform.
# It deliberately exercises a native-window QML component in an offscreen,
# software-rendered process only.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_FILE = PROJECT_ROOT / "qml" / "V03FocusTimerAura.qml"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _component_errors(component: QQmlComponent) -> str:
    return "\n".join(error.toString() for error in component.errors())


def load_windows_ui_fonts() -> None:
    """Give the offscreen platform the same CJK fonts as the real desktop."""

    for candidate in (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
    ):
        if candidate.is_file():
            QFontDatabase.addApplicationFont(str(candidate))


def main() -> int:
    # The production shell is a QApplication (Qt Widgets owns the tray,
    # menus, prompts and default UI font).  A bare QGuiApplication can leave
    # the offscreen text engine without the same fallback font and produce
    # tofu squares even though the installed UI is healthy.
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_FILE)))
    if component.status() == QQmlComponent.Status.Error:
        raise RuntimeError(_component_errors(component))
    aura = component.create()
    if aura is None:
        raise RuntimeError(_component_errors(component) or "focus aura did not load")
    if not isinstance(aura, QQuickWindow):
        raise RuntimeError("focus aura root must remain an independent QQuickWindow")

    # The module forces Qt's offscreen platform before constructing the app,
    # so presentation can be enabled here without touching the real desktop.
    # This also exercises the production visibility gate instead of a hidden
    # diagnostic-only motion path.
    aura.setProperty("presentationEnabled", True)

    def settle(milliseconds: int = 35) -> None:
        app.processEvents()
        QTest.qWait(milliseconds)
        app.processEvents()

    def set_focus(value: dict[str, object]) -> None:
        if not aura.setProperty("focusInfo", value):
            raise RuntimeError("could not set focusInfo")
        settle()

    def set_presence(state: str) -> None:
        if not aura.setProperty("presenceInfo", {"state": state}):
            raise RuntimeError("could not set presenceInfo")
        settle()

    def set_transition(
        sequence: int,
        kind: str,
        *,
        elapsed_seconds: int,
        duration_seconds: int = 300,
    ) -> None:
        if not aura.setProperty(
            "focusTransition",
            {
                "sequence": sequence,
                "kind": kind,
                "sessionId": f"focus-{sequence}",
                "elapsedSeconds": elapsed_seconds,
                "durationSeconds": duration_seconds,
                "occurredAt": f"2026-08-29T00:00:0{sequence}+00:00",
            },
        ):
            raise RuntimeError("could not set focusTransition")
        settle()

    set_presence("normal")
    aura_surface = aura.findChild(QQuickItem, "focusTimerAuraSurface")
    state_text_item = aura.findChild(QQuickItem, "focusTimerStateLabel")
    time_text_item = aura.findChild(QQuickItem, "focusTimerTimeText")
    used_time_item = aura.findChild(QQuickItem, "focusTimerUsedTimeText")
    if any(
        value is None
        for value in (aura_surface, state_text_item, time_text_item, used_time_item)
    ):
        raise RuntimeError("focus timer surface or time labels are missing")

    idle_ticks = int(aura.property("motionTickCount"))
    settle(100)
    idle = {
        "shouldShow": bool(aura.property("shouldShow")),
        "visible": aura.isVisible(),
        "targetFps": int(aura.property("targetFps")),
        "motionStopped": int(aura.property("motionTickCount")) == idle_ticks,
    }

    # Exercise logical work areas rather than physical screen pixels.  The
    # last two cases model a very high Windows scale factor and a pathological
    # emergency work area; neither may leave the native tool window outside
    # its monitor or turn the duration ring into an unscaled solid disc.
    scaling: dict[str, dict[str, object]] = {}
    for label, area in (
        ("standard", {"left": 0, "top": 0, "width": 1920, "height": 1040}),
        ("negativePortrait", {"left": -1280, "top": -40, "width": 520, "height": 760}),
        ("highDpiEmergency", {"left": 200, "top": 100, "width": 96, "height": 80}),
        ("microEmergency", {"left": -48, "top": 20, "width": 40, "height": 32}),
    ):
        aura.setProperty("placementArea", area)
        aura.setProperty("anchorX", area["left"] + area["width"] / 2)
        aura.setProperty("anchorY", area["top"] + area["height"] / 2)
        aura.setProperty("subjectLeft", area["left"] + area["width"] * 0.40)
        aura.setProperty("subjectRight", area["left"] + area["width"] * 0.60)
        aura.setProperty("subjectCenterY", area["top"] + area["height"] * 0.55)
        settle()
        right = area["left"] + area["width"]
        bottom = area["top"] + area["height"]
        expected_extent = max(
            1,
            min(
                176,
                area["width"] - 16 if area["width"] > 16 else area["width"],
                area["height"] - 16 if area["height"] > 16 else area["height"],
            ),
        )
        scale = float(aura.property("dialScale"))
        surface_width = float(aura_surface.property("width"))
        ring_inset = float(aura.property("ringInset"))
        ring_stroke = float(aura.property("ringStroke"))
        ring_radius = max(1.0, surface_width / 2.0 - ring_inset)
        scaling[label] = {
            "extent": [aura.width(), aura.height()],
            "expectedExtent": expected_extent,
            "insideWorkArea": (
                aura.x() >= area["left"] - 1
                and aura.y() >= area["top"] - 1
                and aura.x() + aura.width() <= right + 1
                and aura.y() + aura.height() <= bottom + 1
            ),
            "surfaceInsideWindow": 0 < surface_width <= aura.width() + 0.01,
            "scaledRingReadable": (
                ring_stroke <= ring_radius * 1.25
                and ring_inset <= max(2.0, surface_width / 2.0)
                and 0 < scale <= 1
            ),
            "dialScale": scale,
            "ringInset": ring_inset,
            "ringStroke": ring_stroke,
        }

    # Restore a conventional logical monitor before the state/animation
    # checks below, which intentionally assert the full 176 px master size.
    aura.setProperty(
        "placementArea", {"left": 0, "top": 0, "width": 800, "height": 600}
    )
    aura.setProperty("anchorY", -1000.0)
    aura.setProperty("subjectLeft", 220.0)
    aura.setProperty("subjectRight", 340.0)
    aura.setProperty("subjectCenterY", 310.0)
    settle()
    placed_right = bool(aura.property("placeOnRight"))
    placement = {
        "usesSide": bool(aura.property("useSidePlacement")),
        "sideGapHeld": (
            aura.x() >= float(aura.property("subjectRight")) + float(aura.property("sideGap")) - 1
            if placed_right
            else aura.x() + aura.width()
            <= float(aura.property("subjectLeft")) - float(aura.property("sideGap")) + 1
        ),
        "verticalCenterHeld": abs(
            aura.y() + aura.height() / 2 - float(aura.property("subjectCenterY"))
        ) <= 1,
    }
    set_focus(
        {
            "sessionId": "focus-running",
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 59,
            "durationMinutes": 5,
        }
    )
    minute_before = {
        "state": str(aura.property("visualState")),
        "time": str(aura.property("timeText")),
        "used": str(aura.property("usedTimeText")),
        "remainingSeconds": int(aura.property("remainingSeconds")),
        "progressTarget": float(aura.property("progressTarget")),
    }
    # Freeze exactly on the minute boundary, then resume one second later.
    # This catches off-by-one formatting and a stale duration arc without
    # waiting a real minute in the verifier.
    set_focus(
        {
            "sessionId": "focus-running",
            "active": True,
            "paused": True,
            "state": "paused",
            "elapsedSeconds": 60,
            "durationMinutes": 5,
        }
    )
    settle(210)
    minute_paused = {
        "state": str(aura.property("visualState")),
        "time": str(aura.property("timeText")),
        "used": str(aura.property("usedTimeText")),
        "remainingSeconds": int(aura.property("remainingSeconds")),
        "progressTarget": float(aura.property("progressTarget")),
        "motionStopped": not bool(aura.property("breathing")),
    }
    breath_before = float(aura.property("breath"))
    orbit_before = float(aura.property("orbitAngle"))
    progress_head_before = float(aura.property("progressHeadAngle"))
    set_focus(
        {
            "sessionId": "focus-running",
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 61,
            "durationMinutes": 5,
        }
    )
    settle(140)
    running = {
        "state": str(aura.property("visualState")),
        "label": str(aura.property("stateLabel")),
        "remainingLabel": str(aura.property("remainingLabel")),
        "time": str(aura.property("timeText")),
        "elapsedText": str(aura.property("elapsedText")),
        "durationText": str(aura.property("durationText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "visibleRemainingLabel": str(state_text_item.property("text")),
        "visibleUsedTimeText": str(used_time_item.property("text")),
        "elapsedSeconds": int(aura.property("elapsedSeconds")),
        "remainingSeconds": int(aura.property("remainingSeconds")),
        "progressTarget": float(aura.property("progressTarget")),
        "animatedProgress": float(aura.property("animatedProgress")),
        "breathing": bool(aura.property("breathing")),
        "breathMoved": abs(float(aura.property("breath")) - breath_before) > 0.001,
        "orbitMoved": abs(float(aura.property("orbitAngle")) - orbit_before) > 1.0,
        "progressHeadMoved": (
            float(aura.property("progressHeadAngle")) - progress_head_before > 0.1
        ),
        "shouldShow": bool(aura.property("shouldShow")),
    }
    minute_boundary = {
        "before": minute_before,
        "paused": minute_paused,
        "resumed": {
            "state": running["state"],
            "time": running["time"],
            "used": running["usedTimeText"],
            "remainingSeconds": running["remainingSeconds"],
            "progressTarget": running["progressTarget"],
        },
    }

    # The session-identity change above now owns a bounded 880 ms explicit
    # acknowledgement.  Let that finite pulse finish before measuring the
    # long-lived steady-state budgets; otherwise its deliberate temporary
    # 60 FPS presentation would be misreported as the low-power cadence.
    settle(760)
    # Verify both steady-state animation budgets in the same offscreen process.
    # The low-power path remains alive but generates far fewer visual updates.
    aura.setProperty("lowPower", False)
    full_motion_before = int(aura.property("motionTickCount"))
    settle(280)
    full_motion_ticks = int(aura.property("motionTickCount")) - full_motion_before
    aura.setProperty("lowPower", True)
    low_motion_before = int(aura.property("motionTickCount"))
    settle(300)
    low_motion_ticks = int(aura.property("motionTickCount")) - low_motion_before
    low_power = {
        "targetFps": int(aura.property("targetFps")),
        "fullMotionTicks": full_motion_ticks,
        "lowMotionTicks": low_motion_ticks,
        "stillAnimated": low_motion_ticks > 0,
        "reducedCadence": full_motion_ticks > low_motion_ticks * 2,
    }
    aura.setProperty("lowPower", False)

    hidden_motion_before = int(aura.property("motionTickCount"))
    aura.setProperty("presentationEnabled", False)
    settle(120)
    hidden_active = {
        "visible": aura.isVisible(),
        "canAnimate": bool(aura.property("canAnimate")),
        "targetFps": int(aura.property("targetFps")),
        "motionStopped": int(aura.property("motionTickCount"))
        == hidden_motion_before,
        "startPulseCleared": abs(float(aura.property("startPulse"))) < 0.001,
    }
    aura.setProperty("presentationEnabled", True)
    settle()

    # QML receives device-independent work-area sizes after Windows DPI
    # scaling. The time hierarchy must fit every supported 144-208 DIP dial.
    text_scaling: dict[str, dict[str, object]] = {}
    for label, extent in (("compact", 144), ("standard", 176), ("large", 208)):
        aura.setProperty("preferredExtent", extent)
        settle()
        text_scaling[label] = {
            "extent": [aura.width(), aura.height()],
            "remaining": str(state_text_item.property("text")),
            "used": str(used_time_item.property("text")),
            "usedImplicitWidth": float(used_time_item.property("implicitWidth")),
            "usedWidth": float(used_time_item.property("width")),
            "stateFits": float(state_text_item.property("implicitWidth"))
            <= float(aura_surface.property("width")) - 8,
            "usedFits": float(used_time_item.property("implicitWidth"))
            <= float(used_time_item.property("width")) + 1,
        }
    aura.setProperty("preferredExtent", 176)
    settle()

    # Let the 920ms duration-arc interpolation settle. The red knot must land
    # on the exact endpoint of 61/300 rather than following the independent
    # nine-second activity sweep.
    settle(900)
    progress_endpoint = {
        "angle": float(aura.property("progressHeadAngle")),
        "expectedAngle": 61 / 300 * 360,
    }
    screenshot_path = PROJECT_ROOT / "artifacts" / "focus-timer-aura-running.png"
    settle(90)
    screenshot = aura.grabWindow()
    screenshot_saved = not screenshot.isNull() and screenshot.save(str(screenshot_path))
    settle()

    set_focus(
        {
            "active": True,
            "paused": True,
            "state": "paused",
            "elapsedSeconds": 65,
            "durationMinutes": 5,
        }
    )
    # The short 180ms visual handoff lands on the final active second before
    # the paused state becomes visually stationary.
    settle(210)
    paused_orbit = float(aura.property("orbitAngle"))
    paused_head = float(aura.property("progressHeadAngle"))
    settle(140)
    paused = {
        "state": str(aura.property("visualState")),
        "label": str(aura.property("stateLabel")),
        "remainingLabel": str(aura.property("remainingLabel")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "breathing": bool(aura.property("breathing")),
        "orbitStopped": abs(float(aura.property("orbitAngle")) - paused_orbit) < 0.5,
        "progressHeadStopped": (
            abs(float(aura.property("progressHeadAngle")) - paused_head) < 0.05
        ),
        "shouldShow": bool(aura.property("shouldShow")),
    }


    # Resuming does not lose the accumulated duration position. A one-second
    # backend update advances both the countdown and its endpoint while the
    # independent activity sweep starts moving again.
    set_focus(
        {
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 66,
            "durationMinutes": 5,
        }
    )
    resumed_orbit = float(aura.property("orbitAngle"))
    resumed_head = float(aura.property("progressHeadAngle"))
    settle(180)
    resumed = {
        "state": str(aura.property("visualState")),
        "remainingLabel": str(aura.property("remainingLabel")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "orbitMoved": abs(float(aura.property("orbitAngle")) - resumed_orbit) > 1.0,
        "progressHeadAdvanced": (
            float(aura.property("progressHeadAngle")) - resumed_head > 0.05
        ),
    }

    # Boundary immediately before the deterministic backend completion tick.
    set_focus(
        {
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 299,
            "durationMinutes": 5,
        }
    )
    settle(950)
    near_deadline = {
        "time": str(aura.property("timeText")),
        "remainingSeconds": int(aura.property("remainingSeconds")),
        "progressTarget": float(aura.property("progressTarget")),
        "progressHeadAngle": float(aura.property("progressHeadAngle")),
    }

    set_focus(
        {
            "active": False,
            "paused": False,
            "state": "finished",
            "elapsedSeconds": 300,
            "durationMinutes": 5,
        }
    )
    set_transition(1, "completed", elapsed_seconds=300)
    completed = {
        "state": str(aura.property("visualState")),
        "label": str(aura.property("stateLabel")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "completionVisible": bool(aura.property("completionVisible")),
        "shouldShow": bool(aura.property("shouldShow")),
    }

    # A new session must not reuse the old completed ring and visibly rewind.
    # Only a non-time-bearing start affordance may animate here; the duration
    # arc itself must already show the exact zero progress of the new session.
    set_focus(
        {
            "sessionId": "focus-new",
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    set_transition(2, "started", elapsed_seconds=0)
    settle(55)
    new_session_start = {
        "state": str(aura.property("visualState")),
        "time": str(aura.property("timeText")),
        "progressTarget": float(aura.property("progressTarget")),
        "animatedProgress": float(aura.property("animatedProgress")),
        "startPulseActive": float(aura.property("startPulse")) > 0.01,
    }

    # Pausing immediately after the explicit start acknowledgement must stop
    # that finite pulse in the same settled frame.  A paused clock is a still
    # clock: it must not retain a hidden 60 FPS render budget or keep scaling
    # the paper surface for the remainder of the 880 ms start animation.
    set_focus(
        {
            "sessionId": "focus-new",
            "active": True,
            "paused": True,
            "state": "paused",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    immediate_pause_scale = float(aura_surface.property("scale"))
    immediate_pause_ticks = int(aura.property("motionTickCount"))
    settle(180)
    immediate_pause_after_scale = float(aura_surface.property("scale"))
    immediate_pause = {
        "state": str(aura.property("visualState")),
        "breathing": bool(aura.property("breathing")),
        "startPulseCleared": abs(float(aura.property("startPulse"))) < 0.001,
        "startAcknowledgementStopped": not bool(
            aura.property("startAcknowledgementActive")
        ),
        "targetFps": int(aura.property("targetFps")),
        "motionStopped": int(aura.property("motionTickCount"))
        == immediate_pause_ticks,
        "surfaceScaleStopped": abs(
            immediate_pause_after_scale - immediate_pause_scale
        )
        < 0.0005,
    }

    # Cancelling during the finite start ripple must atomically hand over to
    # the ending state instead of overlaying two incompatible animations.
    set_focus(
        {
            "sessionId": "focus-new",
            "active": False,
            "paused": False,
            "state": "cancelled",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    set_transition(3, "cancelled", elapsed_seconds=0)
    quick_cancel = {
        "state": str(aura.property("visualState")),
        "startPulseCleared": abs(float(aura.property("startPulse"))) < 0.001,
        "completionPulseCleared": abs(float(aura.property("completionPulse"))) < 0.001,
    }

    set_focus(
        {
            "sessionId": "focus-finished",
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 65,
            "durationMinutes": 5,
        }
    )
    set_focus(
        {
            "active": False,
            "paused": False,
            "state": "finished",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    set_transition(4, "finished", elapsed_seconds=65)
    finished = {
        "state": str(aura.property("visualState")),
        "label": str(aura.property("stateLabel")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "completionVisible": bool(aura.property("completionVisible")),
        "progress": float(aura.property("endingProgress")),
    }

    set_focus(
        {
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 40,
            "durationMinutes": 5,
        }
    )
    set_focus(
        {
            "active": False,
            "paused": False,
            "state": "cancelled",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    set_transition(5, "cancelled", elapsed_seconds=40)
    cancelled = {
        "state": str(aura.property("visualState")),
        "label": str(aura.property("stateLabel")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "completionVisible": bool(aura.property("completionVisible")),
        "progress": float(aura.property("endingProgress")),
    }

    set_focus(
        {
            "sessionId": "focus-after-cancel",
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 0,
            "durationMinutes": 5,
        }
    )
    set_transition(6, "started", elapsed_seconds=0)
    settle(55)
    after_cancel_start = {
        "state": str(aura.property("visualState")),
        "completionVisible": bool(aura.property("completionVisible")),
        "completionPulseCleared": abs(float(aura.property("completionPulse"))) < 0.001,
        "progressReset": float(aura.property("animatedProgress")) < 0.001,
    }

    set_focus(
        {
            "active": True,
            "paused": False,
            "state": "running",
            "elapsedSeconds": 75,
            "durationMinutes": 5,
        }
    )
    set_presence("silent")
    silent = {
        "state": str(aura.property("visualState")),
        "suppressed": bool(aura.property("suppressed")),
        "shouldShow": bool(aura.property("shouldShow")),
        "visible": aura.isVisible(),
    }

    # Restored sessions are not new sessions.  Construct fresh QML windows
    # with initial running/paused state to exercise the same ordering used at
    # application startup.  Neither may replay the start acknowledgement;
    # the running session resumes only its ordinary low-power clock, while a
    # paused session remains completely still.
    restored_sessions: dict[str, dict[str, object]] = {}
    restored_windows: list[QQuickWindow] = []
    # Keep each QQmlComponent alive with its created object.  Some PySide
    # builds otherwise release the first object when the loop-local component
    # wrapper is reassigned, which turns deterministic cleanup into a noisy
    # "Internal C++ object already deleted" error after the report is written.
    restored_components: list[QQmlComponent] = []
    for label, restored_paused in (("running", False), ("paused", True)):
        restored_component = QQmlComponent(
            engine, QUrl.fromLocalFile(str(QML_FILE))
        )
        restored_components.append(restored_component)
        if restored_component.status() == QQmlComponent.Status.Error:
            raise RuntimeError(_component_errors(restored_component))
        restored = restored_component.createWithInitialProperties(
            {
                "focusInfo": {
                    "sessionId": f"focus-restored-{label}",
                    "active": True,
                    "paused": restored_paused,
                    "state": "paused" if restored_paused else "running",
                    "elapsedSeconds": 120,
                    "durationMinutes": 5,
                },
                "presenceInfo": {"state": "normal"},
                "presentationEnabled": True,
                "lowPower": True,
                "placementArea": {
                    "left": 0,
                    "top": 0,
                    "width": 800,
                    "height": 600,
                },
            }
        )
        if restored is None or not isinstance(restored, QQuickWindow):
            raise RuntimeError(
                _component_errors(restored_component)
                or f"restored {label} focus aura did not load"
            )
        restored_windows.append(restored)
        settle(80)
        pulse_before = float(restored.property("startPulse"))
        ticks_before = int(restored.property("motionTickCount"))
        settle(180)
        restored_sessions[label] = {
            "state": str(restored.property("visualState")),
            "visible": restored.isVisible(),
            "startPulseBefore": pulse_before,
            "startPulseAfter": float(restored.property("startPulse")),
            "startAcknowledgementStopped": not bool(
                restored.property("startAcknowledgementActive")
            ),
            "targetFps": int(restored.property("targetFps")),
            "motionTicks": int(restored.property("motionTickCount"))
            - ticks_before,
        }

    flags = aura.flags()
    outcome = {
        "idle": idle,
        "minuteBoundary": minute_boundary,
        "running": running,
        "lowPower": low_power,
        "hiddenActive": hidden_active,
        "textScaling": text_scaling,
        "progressEndpoint": progress_endpoint,
        "runningScreenshot": {
            "path": str(screenshot_path),
            "saved": bool(screenshot_saved),
            "size": [screenshot.width(), screenshot.height()],
        },
        "paused": paused,
        "resumed": resumed,
        "nearDeadline": near_deadline,
        "completed": completed,
        "newSessionStart": new_session_start,
        "immediatePause": immediate_pause,
        "quickCancel": quick_cancel,
        "finished": finished,
        "cancelled": cancelled,
        "afterCancelStart": after_cancel_start,
        "placement": placement,
        "scaling": scaling,
        "silent": silent,
        "restoredSessions": restored_sessions,
        "window": {
            "visibleDuringVerification": aura.isVisible(),
            "doesNotAcceptFocus": bool(
                flags & Qt.WindowType.WindowDoesNotAcceptFocus
            ),
            "transparentForInput": bool(
                flags & Qt.WindowType.WindowTransparentForInput
            ),
            "independent": aura.transientParent() is None,
            "ringExists": aura.findChild(QObject, "focusTimerProgressRing") is not None,
            "timeLabelExists": aura.findChild(QObject, "focusTimerTimeText") is not None,
            "usedTimeLabelExists": (
                aura.findChild(QObject, "focusTimerUsedTimeText") is not None
            ),
            "orbitKnotExists": aura.findChild(QObject, "focusTimerOrbitKnot") is not None,
            "activitySweepExists": (
                aura.findChild(QObject, "focusTimerActivitySweep") is not None
            ),
            "startWaveExists": (
                aura.findChild(QObject, "focusTimerStartWave") is not None
            ),
        },
    }
    passed = bool(
        idle["shouldShow"] is False
        and idle["visible"] is False
        and idle["targetFps"] == 0
        and idle["motionStopped"]
        and running["state"] == "running"
        and minute_boundary["before"]["time"] == "04:01"
        and minute_boundary["before"]["used"] == "已用 00:59 / 05:00"
        and minute_boundary["before"]["remainingSeconds"] == 241
        and abs(minute_boundary["before"]["progressTarget"] - 59 / 300) < 1e-6
        and minute_boundary["paused"]["state"] == "paused"
        and minute_boundary["paused"]["time"] == "04:00"
        and minute_boundary["paused"]["used"] == "已用 01:00 / 05:00"
        and minute_boundary["paused"]["remainingSeconds"] == 240
        and minute_boundary["paused"]["motionStopped"]
        and abs(minute_boundary["paused"]["progressTarget"] - 60 / 300) < 1e-6
        and minute_boundary["resumed"]["state"] == "running"
        and minute_boundary["resumed"]["time"] == "03:59"
        and minute_boundary["resumed"]["used"] == "已用 01:01 / 05:00"
        and minute_boundary["resumed"]["remainingSeconds"] == 239
        and abs(minute_boundary["resumed"]["progressTarget"] - 61 / 300) < 1e-6
        and running["time"] == "03:59"
        and running["remainingLabel"] == "专注中 · 剩余"
        and running["usedTimeText"] == "已用 01:01 / 05:00"
        and running["breathing"]
        and running["breathMoved"]
        and running["orbitMoved"]
        and running["progressHeadMoved"]
        and low_power["targetFps"] == 15
        and low_power["stillAnimated"]
        and low_power["reducedCadence"]
        and not hidden_active["visible"]
        and not hidden_active["canAnimate"]
        and hidden_active["targetFps"] == 0
        and hidden_active["motionStopped"]
        and hidden_active["startPulseCleared"]
        and all(
            value["remaining"] == "专注中 · 剩余"
            and value["used"] == "已用 01:01 / 05:00"
            and bool(value["stateFits"])
            and bool(value["usedFits"])
            for value in text_scaling.values()
        )
        and abs(progress_endpoint["angle"] - progress_endpoint["expectedAngle"]) < 0.1
        and bool(screenshot_saved)
        and screenshot.width() == aura.width()
        and screenshot.height() == aura.height()
        and paused["state"] == "paused"
        and paused["remainingLabel"] == "已暂停 · 剩余"
        and paused["usedTimeText"] == "已用 01:05 / 05:00"
        and paused["orbitStopped"]
        and paused["progressHeadStopped"]
        and resumed["orbitMoved"]
        and resumed["progressHeadAdvanced"]
        and resumed["remainingLabel"] == "专注中 · 剩余"
        and resumed["usedTimeText"] == "已用 01:06 / 05:00"
        and near_deadline["time"] == "00:01"
        and near_deadline["remainingSeconds"] == 1
        and abs(near_deadline["progressHeadAngle"] - 299 / 300 * 360) < 0.1
        and completed["state"] == "completed"
        and completed["time"] == "00:00"
        and completed["usedTimeText"] == "已用 05:00 / 05:00"
        and new_session_start["state"] == "running"
        and new_session_start["time"] == "05:00"
        and new_session_start["progressTarget"] == 0.0
        and new_session_start["animatedProgress"] < 0.001
        and new_session_start["startPulseActive"]
        and immediate_pause["state"] == "paused"
        and not immediate_pause["breathing"]
        and immediate_pause["startPulseCleared"]
        and immediate_pause["startAcknowledgementStopped"]
        and immediate_pause["targetFps"] == 0
        and immediate_pause["motionStopped"]
        and immediate_pause["surfaceScaleStopped"]
        and quick_cancel["state"] == "cancelled"
        and quick_cancel["startPulseCleared"]
        and quick_cancel["completionPulseCleared"]
        and finished["usedTimeText"] == "已用 01:05 / 05:00"
        and cancelled["usedTimeText"] == "已用 00:40 / 05:00"
        and after_cancel_start["state"] == "running"
        and not after_cancel_start["completionVisible"]
        and after_cancel_start["completionPulseCleared"]
        and after_cancel_start["progressReset"]
        and all(
            abs(float(value["extent"][0]) - float(value["expectedExtent"])) <= 1
            and abs(float(value["extent"][1]) - float(value["expectedExtent"])) <= 1
            and bool(value["insideWorkArea"])
            and bool(value["surfaceInsideWindow"])
            and bool(value["scaledRingReadable"])
            for value in scaling.values()
        )
        and silent["shouldShow"] is False
        and restored_sessions["running"]["state"] == "running"
        and restored_sessions["running"]["visible"]
        and abs(float(restored_sessions["running"]["startPulseBefore"])) < 0.001
        and abs(float(restored_sessions["running"]["startPulseAfter"])) < 0.001
        and restored_sessions["running"]["startAcknowledgementStopped"]
        and restored_sessions["running"]["targetFps"] == 15
        and int(restored_sessions["running"]["motionTicks"]) > 0
        and restored_sessions["paused"]["state"] == "paused"
        and restored_sessions["paused"]["visible"]
        and abs(float(restored_sessions["paused"]["startPulseBefore"])) < 0.001
        and abs(float(restored_sessions["paused"]["startPulseAfter"])) < 0.001
        and restored_sessions["paused"]["startAcknowledgementStopped"]
        and restored_sessions["paused"]["targetFps"] == 0
        and int(restored_sessions["paused"]["motionTicks"]) == 0
        and outcome["window"]["doesNotAcceptFocus"]
        and outcome["window"]["transparentForInput"]
        and outcome["window"]["ringExists"]
        and outcome["window"]["timeLabelExists"]
        and outcome["window"]["usedTimeLabelExists"]
        and outcome["window"]["orbitKnotExists"]
        and outcome["window"]["activitySweepExists"]
        and outcome["window"]["startWaveExists"]
    )
    outcome["offscreenPlatform"] = os.environ["QT_QPA_PLATFORM"]
    outcome["passed"] = passed
    report_path = PROJECT_ROOT / "artifacts" / "focus-timer-aura-audit.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    outcome["report"] = str(report_path)
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))

    for restored in restored_windows:
        restored.deleteLater()
    aura.deleteLater()
    engine.deleteLater()
    app.processEvents()
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
