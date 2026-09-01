from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

# This is a full Main.qml integration check, but it must never create a real
# desktop surface or use the hardware renderer on the developer's machine.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.paths import qml_path


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-focus-main-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    # Drive foreground/presence transitions explicitly. The productivity
    # methods still publish their real signals synchronously.
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    focus_now = [datetime(2026, 8, 30, 0, 0, tzinfo=UTC)]
    backend.focus.now = lambda: focus_now[0]

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(
        lambda values: warnings.extend(str(value.toString()) for value in values)
    )
    engine.rootContext().setContextProperty("backend", backend)
    # Main.qml deliberately fails closed when the packaged startup-probe
    # context flag is absent.  The real app always publishes this property;
    # mirror that contract so this verifier exercises the normal compact pet
    # instead of an intentionally hidden diagnostic surface.
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        backend.shutdown()
        temporary.cleanup()
        raise RuntimeError("Main.qml failed to load: " + " | ".join(warnings[-8:]))

    root = engine.rootObjects()[0]
    aura = root.findChild(QQuickWindow, "v03FocusTimerAura")
    work_panel = root.findChild(QQuickWindow, "v03WorkPanel")
    focus_start_button = root.findChild(QQuickItem, "focusStartButton")
    focus_minutes_input = root.findChild(QQuickItem, "focusMinutesInput")
    pet = root.findChild(QQuickItem, "compactLilith")
    artwork_frame = root.findChild(QQuickItem, "petPoseArtworkFrame")
    presence_label = root.findChild(QQuickItem, "petPresenceStatusLabel")
    if (
        aura is None
        or work_panel is None
        or focus_start_button is None
        or focus_minutes_input is None
        or pet is None
        or artwork_frame is None
        or presence_label is None
    ):
        backend.shutdown()
        temporary.cleanup()
        raise RuntimeError(
            "focus aura, real start controls, compact Lilith, pose artwork frame, or presence label was not constructed"
        )

    def settle(milliseconds: int = 90) -> None:
        app.processEvents()
        QTest.qWait(milliseconds)
        app.processEvents()

    original_loadout = dict(backend.wardrobeState.get("current") or {})
    # Exercise the production button rather than calling Backend.focusStart
    # directly.  This catches a dead hit target or a broken WorkPanel.invoke
    # bridge while proving that a low-power compact pet still acknowledges the
    # explicit click immediately.
    backend.openWorkPanelSection("work")
    settle(140)
    focus_minutes_input.setProperty("value", 5)
    app.processEvents()
    start_center = focus_start_button.mapToScene(
        QPointF(focus_start_button.width() / 2, focus_start_button.height() / 2)
    )
    button_visible_before_click = bool(focus_start_button.isVisible())
    motion_ticks_before_click = int(aura.property("motionTickCount"))
    orbit_before_click = float(aura.property("orbitAngle"))
    QTest.mouseClick(
        work_panel,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(start_center.x()), round(start_center.y())),
    )
    app.processEvents()
    active_from_real_click = bool(backend.focusStatus.get("active", False))
    # Closing the interactive panel returns the compact surface to low power
    # while the bounded acknowledgement is still in flight.  The pulse must
    # survive that budget transition rather than disappearing with the panel.
    backend.setWorkPanelOpen(False)
    settle(150)
    explicit_start = {
        "buttonVisible": button_visible_before_click,
        "activeFromClick": active_from_real_click,
        "auraVisible": bool(aura.isVisible()),
        "startPulseActive": float(aura.property("startPulse")) > 0.01,
        "temporaryFps": int(aura.property("targetFps")),
        "motionTicksAdvanced": int(aura.property("motionTickCount"))
        > motion_ticks_before_click,
        "orbitAdvanced": abs(float(aura.property("orbitAngle")) - orbit_before_click)
        > 0.1,
    }
    # The focus pose handoff briefly keeps the pet on its interactive budget.
    # Once it settles, the same acknowledgement must remain visible while the
    # recurring motion clock has already returned to low power.
    settle(270)
    low_power_handoff = {
        "lowPower": bool(aura.property("lowPower")),
        "startPulseStillActive": float(aura.property("startPulse")) > 0.01,
    }
    # Let the finite focus-pose handoff settle so the aura is measured at its
    # normal idle animation budget rather than the temporary interaction rate.
    settle(560)
    running = {
        "visible": aura.isVisible(),
        "pose": str(pet.property("pose")),
        "state": str(aura.property("visualState")),
        "time": str(aura.property("timeText")),
        "remainingLabel": str(aura.property("remainingLabel")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "targetFps": int(aura.property("targetFps")),
        "startPulseCleared": abs(float(aura.property("startPulse"))) < 0.001,
    }

    # Advance the service's real clock and publish through the same backend
    # signal used by the production one-second timer. This proves the QML
    # countdown is not merely a static start-state mock.
    focus_now[0] += timedelta(seconds=1)
    backend.refreshProductivity()
    settle(120)
    progressed = {
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
        "progressTarget": float(aura.property("progressTarget")),
    }

    root.setProperty("compactBoxSize", 110.0)
    settle()
    small = {
        "preferredExtent": float(aura.property("preferredExtent")),
        "extent": [aura.width(), aura.height()],
        "figureHeight": float(pet.property("figureHeight")),
        "petExtent": [pet.width(), pet.height()],
        "characterHeight": float(pet.property("characterHeight")),
        "poseArtworkFrame": [artwork_frame.width(), artwork_frame.height()],
        "renderedArtworkBlend": float(pet.property("renderedArtworkBlend")),
        "renderedArtworkRatio": float(pet.property("renderedPoseArtworkRatio")),
    }
    root.setProperty("compactBoxSize", 320.0)
    settle()
    large = {
        "preferredExtent": float(aura.property("preferredExtent")),
        "extent": [aura.width(), aura.height()],
        "figureHeight": float(pet.property("figureHeight")),
        "petExtent": [pet.width(), pet.height()],
        "characterHeight": float(pet.property("characterHeight")),
        "poseArtworkFrame": [artwork_frame.width(), artwork_frame.height()],
        "renderedArtworkBlend": float(pet.property("renderedArtworkBlend")),
        "renderedArtworkRatio": float(pet.property("renderedPoseArtworkRatio")),
    }

    backend.setChatOpen(True)
    settle()
    chat_pose = str(pet.property("pose"))
    backend.setChatOpen(False)
    settle()
    post_chat_pose = str(pet.property("pose"))

    backend.focusPause()
    settle()
    paused = {
        "visible": aura.isVisible(),
        "pose": str(pet.property("pose")),
        "state": str(aura.property("visualState")),
        "remainingLabel": str(aura.property("remainingLabel")),
        "targetFps": int(aura.property("targetFps")),
    }
    paused_before = dict(backend.focusStatus)
    backend.pet_habitat.set_presence("silent")
    backend._sync_habitat_state()
    settle()
    paused_silent_presence = str(presence_label.property("text"))
    focus_now[0] += timedelta(seconds=2)
    backend.refreshProductivity()
    settle(120)
    paused_after = dict(backend.focusStatus)
    paused_silent_continuity = {
        "sessionSame": paused_before.get("sessionId") == paused_after.get("sessionId"),
        "elapsedBefore": int(paused_before.get("elapsedSeconds", -1)),
        "elapsedAfter": int(paused_after.get("elapsedSeconds", -1)),
        "auraVisible": aura.isVisible(),
        "presenceText": str(presence_label.property("text")),
    }
    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle()
    backend.focusResume()
    settle()
    resumed_pose = str(pet.property("pose"))

    backend.pet_habitat.set_presence("silent")
    backend._sync_habitat_state()
    settle()
    silent_before = dict(backend.focusStatus)
    focus_now[0] += timedelta(seconds=2)
    backend.refreshProductivity()
    settle(120)
    silent_after = dict(backend.focusStatus)
    silent_running = {
        "visible": aura.isVisible(),
        "suppressed": bool(aura.property("suppressed")),
        "presenceText": str(presence_label.property("text")),
        "rootPresenceText": str(root.property("petPresenceStatusText")),
        "sessionSame": silent_before.get("sessionId") == silent_after.get("sessionId"),
        "elapsedBefore": int(silent_before.get("elapsedSeconds", -1)),
        "elapsedAfter": int(silent_after.get("elapsedSeconds", -1)),
    }
    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle()
    restored_running = {
        "visible": aura.isVisible(),
        "suppressed": bool(aura.property("suppressed")),
        "pose": str(pet.property("pose")),
        "presenceText": str(presence_label.property("text")),
        "time": str(aura.property("timeText")),
        "usedTimeText": str(aura.property("usedTimeText")),
    }

    backend.pet_habitat.set_presence("blocked")
    backend._sync_habitat_state()
    settle()
    blocked_running = {
        "visible": aura.isVisible(),
        "suppressed": bool(aura.property("suppressed")),
        "presenceText": str(presence_label.property("text")),
    }
    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle()

    # Completion dwell is paused while privacy/full-screen suppression hides
    # the independent window, then starts only after the normal state returns.
    backend.pet_habitat.set_presence("silent")
    backend._sync_habitat_state()
    backend.focusFinish()
    settle(2200)
    hidden_completion = {
        "visible": aura.isVisible(),
        "completionVisible": bool(aura.property("completionVisible")),
        "state": str(aura.property("visualState")),
        "presenceText": str(presence_label.property("text")),
    }
    backend.pet_habitat.set_presence("normal")
    backend._sync_habitat_state()
    settle(120)
    restored_completion = {
        "visible": aura.isVisible(),
        "completionVisible": bool(aura.property("completionVisible")),
        "state": str(aura.property("visualState")),
    }

    final_loadout = dict(backend.wardrobeState.get("current") or {})
    focus_qml_warnings = [
        value for value in warnings if "V03FocusTimerAura.qml" in value
    ]
    outcome = {
        "explicitStart": explicit_start,
        "lowPowerHandoff": low_power_handoff,
        "running": running,
        "progressed": progressed,
        "small": small,
        "large": large,
        "chatPose": chat_pose,
        "postChatPose": post_chat_pose,
        "paused": paused,
        "pausedSilentPresence": paused_silent_presence,
        "pausedSilentContinuity": paused_silent_continuity,
        "resumedPose": resumed_pose,
        "silentRunning": silent_running,
        "restoredRunning": restored_running,
        "blockedRunning": blocked_running,
        "hiddenCompletion": hidden_completion,
        "restoredCompletion": restored_completion,
        "loadoutUnchanged": final_loadout == original_loadout,
        "qmlWarningCount": len(warnings),
        "focusQmlWarningCount": len(focus_qml_warnings),
    }
    outcome["passed"] = bool(
        explicit_start["buttonVisible"]
        and explicit_start["activeFromClick"]
        and explicit_start["auraVisible"]
        and explicit_start["startPulseActive"]
        and explicit_start["temporaryFps"] == 60
        and explicit_start["motionTicksAdvanced"]
        and explicit_start["orbitAdvanced"]
        and low_power_handoff["lowPower"]
        and low_power_handoff["startPulseStillActive"]
        and running["visible"]
        and running["pose"] == "focus-watch"
        and running["state"] == "running"
        and running["remainingLabel"] == "专注中 · 剩余"
        and running["usedTimeText"] == "已用 00:00 / 05:00"
        and running["targetFps"] == 15
        and running["startPulseCleared"]
        and progressed["time"] == "04:59"
        and progressed["usedTimeText"] == "已用 00:01 / 05:00"
        and abs(progressed["progressTarget"] - 1 / 300) < 0.000001
        and abs(small["preferredExtent"] - 144.0) <= 1.0
        and small["extent"] == [144, 144]
        and abs(large["preferredExtent"] - 208.0) <= 1.0
        and large["extent"] == [208, 208]
        and small["figureHeight"] > 0.0
        and large["figureHeight"] > small["figureHeight"] * 2.0
        and chat_pose == "listening-live"
        and post_chat_pose == "focus-watch"
        and paused["visible"]
        and paused["pose"] != "focus-watch"
        and paused["state"] == "paused"
        and paused["remainingLabel"] == "已暂停 · 剩余"
        and paused["targetFps"] == 0
        and paused_silent_presence
        == "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来，专注仍保持暂停"
        and paused_silent_continuity["sessionSame"]
        and paused_silent_continuity["elapsedBefore"] == 1
        and paused_silent_continuity["elapsedAfter"] == 1
        and not paused_silent_continuity["auraVisible"]
        and paused_silent_continuity["presenceText"] == paused_silent_presence
        and resumed_pose == "focus-watch"
        and not silent_running["visible"]
        and silent_running["suppressed"]
        and silent_running["sessionSame"]
        and silent_running["elapsedBefore"] == 1
        and silent_running["elapsedAfter"] == 3
        and silent_running["presenceText"]
        == "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来，专注计时仍在后台继续"
        and silent_running["rootPresenceText"] == silent_running["presenceText"]
        and restored_running["visible"]
        and not restored_running["suppressed"]
        and restored_running["pose"] == "focus-watch"
        and restored_running["presenceText"] == "当前 · 莉莉丝正在桌面安静停驻"
        and restored_running["time"] == "04:57"
        and restored_running["usedTimeText"] == "已用 00:03 / 05:00"
        and not blocked_running["visible"]
        and blocked_running["suppressed"]
        and blocked_running["presenceText"]
        == "当前 · 受保护或敏感界面中暂时隐藏；离开后莉莉丝会自动回来，专注计时仍在后台继续"
        and not hidden_completion["visible"]
        and hidden_completion["completionVisible"]
        and hidden_completion["presenceText"]
        == "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来"
        and restored_completion["visible"]
        and restored_completion["completionVisible"]
        and outcome["loadoutUnchanged"]
        and outcome["focusQmlWarningCount"] == 0
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
