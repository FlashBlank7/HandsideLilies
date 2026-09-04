from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import (
    QMetaObject,
    QPoint,
    QPointF,
    QObject,
    QTimer,
    Qt,
    QUrl,
    Slot,
)
from PySide6.QtGui import QFontDatabase, QWheelEvent, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import (
    CompactHitTestFilter,
    CompactPointerEventFilter,
    configure_quick_window_resource_lifecycle,
)
from lilies.backend import Backend
from lilies.core.activity import ForegroundContext
from lilies.paths import qml_path


class OffscreenBackend(Backend):
    """Keep synthetic drag verification away from the real system cursor."""

    def __init__(self, *args, **kwargs) -> None:
        self.offscreen_cursor: dict[str, int] | None = None
        self.offscreen_work_areas: list[dict[str, int | float | str]] | None = None
        self.offscreen_box_layout_saves: list[dict[str, float]] = []
        super().__init__(*args, **kwargs)

    @Slot(result="QVariantMap")
    def cursorPosition(self) -> dict[str, int]:
        if self.offscreen_cursor is not None:
            return dict(self.offscreen_cursor)
        return super().cursorPosition()

    @Slot(float, float, result="QVariantMap")
    def screenWorkAreaAt(self, x: float, y: float) -> dict[str, object]:
        if not self.offscreen_work_areas:
            return super().screenWorkAreaAt(x, y)

        point_x = float(x)
        point_y = float(y)

        def distance_squared(area: dict[str, int | float | str]) -> float:
            left = float(area["left"])
            top = float(area["top"])
            right = float(area["right"])
            bottom = float(area["bottom"])
            nearest_x = max(left, min(point_x, right))
            nearest_y = max(top, min(point_y, bottom))
            return (point_x - nearest_x) ** 2 + (point_y - nearest_y) ** 2

        containing = [
            area
            for area in self.offscreen_work_areas
            if float(area["left"]) <= point_x < float(area["right"])
            and float(area["top"]) <= point_y < float(area["bottom"])
        ]
        selected = containing[0] if containing else min(
            self.offscreen_work_areas, key=distance_squared
        )
        return dict(selected)

    @Slot(float, float, float)
    def saveBoxLayout(self, x: float, y: float, size: float) -> None:
        self.offscreen_box_layout_saves.append(
            {"x": float(x), "y": float(y), "size": float(size)}
        )
        super().saveBoxLayout(x, y, size)


def load_windows_ui_fonts() -> None:
    """Give the offscreen platform the same CJK fonts as the real desktop."""

    for candidate in (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
    ):
        if candidate.is_file():
            QFontDatabase.addApplicationFont(str(candidate))


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-ui-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication([])
    load_windows_ui_fonts()
    backend = OffscreenBackend(smoke=True, force_compact=True)
    # This verifier supplies its own deterministic foreground/habitat records.
    # Stop the real catalogue pump so a delayed host-window event cannot
    # replace one synthetic profile halfway through the six-case sequence.
    backend._v03_timer.stop()
    engine = QQmlApplicationEngine()
    qml_warnings: list[str] = []
    engine.warnings.connect(
        lambda values: qml_warnings.extend(
            str(value.toString()) for value in values
        )
    )
    engine.rootContext().setContextProperty("backend", backend)
    # Production app.py always publishes the packaged startup-probe flag.
    # Main.qml hides every real surface if that context property is absent,
    # so the offscreen UI verifier must explicitly select normal rendering.
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        backend.shutdown()
        QApplication.processEvents()
        temporary.cleanup()
        detail = " | ".join(qml_warnings[-8:])
        raise RuntimeError(
            "Main.qml failed to load" + (f": {detail}" if detail else "")
        )
    root = engine.rootObjects()[0]
    resource_lifecycle = configure_quick_window_resource_lifecycle(root)
    if len(resource_lifecycle.windows) != 16:
        raise RuntimeError(
            f"unexpected QML window count: {len(resource_lifecycle.windows)}"
        )
    pet_window = root.findChild(QQuickWindow, "petWindow")
    pet_body = root.findChild(QQuickItem, "compactLilith")
    accessory_box = root.findChild(QQuickItem, "compactAccessoryBox")
    compact_window = root.findChild(QQuickItem, "desktopPet")
    work_panel = root.findChild(QQuickWindow, "v03WorkPanel")
    box_world_scene = root.findChild(QQuickWindow, "boxWorldSceneWindow")
    chat_window = root.findChild(QQuickWindow, "chatWindow")
    connector_setup = root.findChild(QQuickWindow, "v03ConnectorSetup")
    selection_question = root.findChild(QQuickWindow, "selectionQuestion")
    companion_bubble = root.findChild(QQuickWindow, "companionBubbleWindow")
    focus_timer = root.findChild(QQuickWindow, "v03FocusTimerAura")
    paper_dock = root.findChild(QQuickWindow, "v03PaperFoldDock")
    pose_artwork_frame = root.findChild(QQuickItem, "petPoseArtworkFrame")
    figure_frame = root.findChild(QQuickItem, "petFigureFrame")
    pet_descendants = tuple(
        CompactHitTestFilter._visual_descendants(pet_window.contentItem())
    ) if pet_window is not None else ()
    chat_action = next(
        (item for item in pet_descendants if item.objectName() == "desktopPetAction_chat"),
        None,
    )
    awareness_label = next(
        (
            item
            for item in pet_descendants
            if item.objectName() == "desktopPetAwarenessLabel_chat"
        ),
        None,
    )
    desktop_discovery_label = next(
        (
            item
            for item in pet_descendants
            if item.objectName() == "compactDesktopDiscoveryLabel"
        ),
        None,
    )
    desktop_discovery_card = root.findChild(
        QQuickItem, "compactDesktopDiscoveryCard"
    )
    desktop_shell_toggle = root.findChild(
        QQuickItem, "compactDesktopShellToggle"
    )
    desktop_mode_tab = root.findChild(
        QQuickItem, "desktopPetDesktopModeTab"
    )
    companion_request_button = root.findChild(
        QQuickItem, "companionRequestNowButton"
    )
    if (
        pet_window is None
        or pet_body is None
        or accessory_box is None
        or compact_window is None
        or work_panel is None
        or box_world_scene is None
        or chat_window is None
        or connector_setup is None
        or selection_question is None
        or companion_bubble is None
        or focus_timer is None
        or paper_dock is None
        or pose_artwork_frame is None
        or figure_frame is None
        or chat_action is None
        or awareness_label is None
        or desktop_discovery_label is None
        or desktop_discovery_card is None
        or desktop_shell_toggle is None
        or desktop_mode_tab is None
        or companion_request_button is None
    ):
        raise RuntimeError("independent pet window failed to load")
    outcome: dict[str, object] = {}
    hit_test = CompactHitTestFilter(
        pet_window,
        backend,
        native_window_id=int(pet_window.winId()),
    )
    pointer_event_filter = CompactPointerEventFilter(pet_window)
    visible_action_gate_wait: dict[str, object] = {}

    # The coordinate helper is the startup parser used by petWindow.x/y.
    # JavaScript's truthiness must never turn the valid primary-screen origin
    # (0, 0), or a negative secondary-screen origin, into the fallback.
    finite_zero = float(root.finiteCoordinate(0.0, 777.0))
    finite_negative = float(root.finiteCoordinate(-840.0, 777.0))
    outcome["startupCoordinateParsing"] = {
        "helper": [finite_zero, finite_negative],
        "passed": (
            finite_zero == 0.0
            and finite_negative == -840.0
        ),
    }
    backend.set_desktop_window_handle(int(root.winId()))
    backend.enter_initial_mode()
    initial_size = float(root.property("compactBoxSize"))

    def sample_breath_start() -> None:
        outcome["breathScaleYStart"] = float(root.property("compactPetBreathScaleY"))
        outcome["animationBudget"] = {
            "idleLowPower": bool(pet_body.property("lowPower")),
            "idleTargetFps": int(pet_body.property("targetFps")),
            "idleHighMotion": bool(compact_window.property("highMotion")),
        }

    def item_center(item: QQuickItem) -> QPoint:
        scene_point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
        return QPoint(round(scene_point.x()), round(scene_point.y()))

    def pump_pet_frame(wait_ms: int = 0) -> None:
        """Advance one deterministic compact-pet frame on the offscreen QPA.

        A real exposed Windows surface continuously receives compositor frame
        requests.  Qt's offscreen platform is allowed to coalesce those
        requests after a top-level sibling was shown or hidden; ordinary
        ``processEvents()`` then advances the animation clock without
        necessarily polishing/swapping the Quick scene.  ``grabWindow()`` is
        the public synchronous render path and keeps this verifier testing the
        rendered target instead of a parked first interpolation sample.
        """

        pet_window.requestUpdate()
        if wait_ms > 0:
            QTest.qWait(wait_ms)
        QApplication.processEvents()
        pet_window.grabWindow()
        QApplication.processEvents()

    def verify_direct_desktop_mode_tab() -> None:
        """The shell-form control is always available outside the feature slots."""

        start_point = item_center(desktop_mode_tab)
        initial = {
            "mode": str(backend.shellMode),
            "visible": desktop_mode_tab.isVisible(),
            "label": str(
                next(
                    (
                        item.property("text")
                        for item in desktop_mode_tab.childItems()
                        if item.metaObject().className().endswith("Text")
                    ),
                    "",
                )
            ),
            "nativeHit": hit_test.accepts_point(start_point.x(), start_point.y()),
        }
        QTest.mouseClick(
            pet_window, Qt.MouseButton.LeftButton, pos=start_point
        )
        QTest.qWait(100)
        QApplication.processEvents()
        expanded_point = item_center(desktop_mode_tab)
        expanded = {
            "mode": str(backend.shellMode),
            "desktopVisible": root.isVisible(),
            "petVisible": pet_window.isVisible(),
            "tabVisible": desktop_mode_tab.isVisible(),
            "nativeHit": hit_test.accepts_point(
                expanded_point.x(), expanded_point.y()
            ),
        }
        QTest.mouseClick(
            pet_window, Qt.MouseButton.LeftButton, pos=expanded_point
        )
        QTest.qWait(100)
        QApplication.processEvents()
        collapsed = {
            "mode": str(backend.shellMode),
            "desktopHidden": not root.isVisible(),
            "petVisible": pet_window.isVisible(),
            "tabVisible": desktop_mode_tab.isVisible(),
        }
        outcome["directDesktopModeTab"] = {
            "initial": initial,
            "expanded": expanded,
            "collapsed": collapsed,
            "passed": bool(
                initial["mode"] == "compact"
                and initial["visible"]
                and initial["nativeHit"]
                and expanded["mode"] == "visual"
                and expanded["desktopVisible"]
                and expanded["petVisible"]
                and expanded["tabVisible"]
                and expanded["nativeHit"]
                and collapsed["mode"] == "compact"
                and collapsed["desktopHidden"]
                and collapsed["petVisible"]
                and collapsed["tabVisible"]
            ),
        }

    def click_lilith() -> None:
        open_action_menu()
        figure_point = QPoint(
            int(float(pet_window.property("compactCharacterLeft"))
                + float(pet_window.property("compactCharacterWidth")) / 2),
            int(float(pet_window.property("compactCharacterTop"))
                + float(pet_window.property("compactCharacterHeight")) / 2),
        )
        size_before = float(root.property("compactBoxSize"))
        packet_sizes = []
        for _ in range(8):
            wheel = QWheelEvent(
                QPointF(figure_point),
                QPointF(pet_window.mapToGlobal(figure_point)),
                QPoint(0, 0),
                QPoint(0, 15),
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.ScrollUpdate,
                False,
            )
            QApplication.sendEvent(pet_window, wheel)
            packet_sizes.append(float(root.property("compactBoxSize")))
        outcome["highResolutionWheel"] = {
            "sizeBefore": size_before,
            "packetSizes": packet_sizes,
            "passed": (
                all(abs(size - size_before) < 0.01 for size in packet_sizes[:7])
                and 0 < packet_sizes[-1] - size_before <= 12.01
            ),
        }

    def find_action(action_id: str) -> QQuickItem | None:
        action = next(
            (
                item
                for item in CompactHitTestFilter._visual_descendants(pet_window.contentItem())
                if item.objectName() == f"desktopPetAction_{action_id}"
            ),
            None,
        )
        if action is None:
            action = pet_window.findChild(
                QQuickItem, f"desktopPetAction_{action_id}"
            )
        return action

    def click_action(action_id: str) -> bool:
        action = find_action(action_id)
        if action is None:
            return False
        # QTest can otherwise inject the click in the same event turn that
        # starts the 640 ms radial animation.  A person cannot click an action
        # before it is drawn; wait for the production input gate so repeated
        # open/close cycles exercise the real visible target instead of an
        # invisible delegate at its collapsed origin.
        action_deadline = time.monotonic() + 0.9
        while (
            (
                not action.isVisible()
                or not bool(pet_window.property("compactActionsInteractive"))
            )
            and time.monotonic() < action_deadline
        ):
            pump_pet_frame(20)
        point = item_center(action)
        outcome[f"{action_id}Point"] = [point.x(), point.y()]
        outcome[f"{action_id}Geometry"] = [action.x(), action.y(), action.width(), action.height()]
        action_visible = action.isVisible()
        action_interactive = bool(
            pet_window.property("compactActionsInteractive")
        )
        native_hit = hit_test.accepts_point(point.x(), point.y())
        QTest.mouseClick(pet_window, Qt.MouseButton.LeftButton, pos=point)
        QApplication.processEvents()
        action_clicks = outcome.setdefault("radialActionClicks", {})
        history = action_clicks.setdefault(action_id, [])
        history.append(
            {
                "visible": action_visible,
                "interactive": action_interactive,
                "nativeHit": native_hit,
                "inputDispatched": True,
            }
        )
        return bool(action_visible and action_interactive and native_hit)

    def open_action_menu() -> None:
        if not bool(root.property("compactExpanded")):
            # A preceding action starts the 640 ms collapse animation.  QTest
            # can inject the next synthetic tap in the very same render turn,
            # while a person necessarily arrives after at least one painted
            # frame.  Wait for the old radial hit islands to finish collapsing
            # before reopening so this probe measures the production target,
            # not an impossible same-frame overlap.
            collapse_deadline = time.monotonic() + 0.9
            while (
                float(compact_window.property("orbitProgress") or 0.0) > 0.01
                and time.monotonic() < collapse_deadline
            ):
                pump_pet_frame(20)
            QTest.mouseClick(
                pet_window,
                Qt.MouseButton.LeftButton,
                pos=item_center(accessory_box),
            )
            pump_pet_frame()

    def verify_chat_click() -> None:
        outcome["actionsVisibleForClicks"] = bool(root.property("compactActionsVisible"))
        default_awareness = str(awareness_label.property("text"))
        default_description = str(chat_action.property("visibleDescription"))
        default_visible = bool(awareness_label.isVisible())
        awareness_screenshot = (
            PROJECT_ROOT / "artifacts" / "companion-awareness-menu.png"
        )
        pet_window.grabWindow().save(str(awareness_screenshot))
        backend.companion.setPaused(True)
        QApplication.processEvents()
        paused_awareness = str(awareness_label.property("text"))
        backend.companion.setPaused(False)
        QApplication.processEvents()
        restored_awareness = str(awareness_label.property("text"))
        outcome["companionAwarenessOnPet"] = {
            "default": default_awareness,
            "paused": paused_awareness,
            "restored": restored_awareness,
            "visibleWhenMenuOpen": default_visible,
            "description": default_description,
            "screenshot": str(awareness_screenshot),
            "passed": (
                # The compact line now reports the live companion state.  A
                # smoke backend legitimately starts at "尚未启动", while
                # resuming the controller advances it to "等待停顿".  Verify
                # the semantic transition instead of requiring the old,
                # static observation-mode copy on all three frames.
                default_awareness.startswith("陪伴 · ")
                and paused_awareness == "陪伴 · 已暂停"
                and restored_awareness.startswith("陪伴 · ")
                and restored_awareness != paused_awareness
                and default_visible
                and "应用感知已开启 · 不截图" in default_description
                and "截图必须另行明确授权" in default_description
                and "选择「陪伴」" in default_description
            ),
        }
        click_action("chat")
        outcome["chatClickWorked"] = bool(backend.chatOpen)
        outcome["menuClosedAfterChat"] = not bool(root.property("compactExpanded"))
        outcome["menuInputClosedAfterChat"] = (
            not bool(pet_window.property("compactActionsInteractive"))
            and not chat_action.isVisible()
        )
        restore_cycles: list[dict[str, object]] = []
        # A retained Qt.Tool used to pass this probe once and then remain
        # minimized on a later click.  Exercise three consecutive cycles in
        # the same native window so the release gate covers that intermittent
        # "clicked but nothing appeared" failure.
        for cycle_index in range(3):
            chat_window.showMinimized()
            # The offscreen QPA applies showMinimized() asynchronously too.
            # Let the synthetic precondition settle before asking the app to
            # restore it, or a late event from the verifier's own minimize can
            # race past a successful showNormal() and create a false failure.
            minimize_deadline = time.monotonic() + 1.0
            minimize_settled_checks = 0
            while time.monotonic() < minimize_deadline:
                if chat_window.visibility() == QWindow.Visibility.Minimized:
                    minimize_settled_checks += 1
                    if minimize_settled_checks >= 5:
                        break
                else:
                    minimize_settled_checks = 0
                QTest.qWait(25)
                QApplication.processEvents()
            minimized_before_reopen = minimize_settled_checks >= 5
            # The first presentation above is still an actual radial-menu
            # click.  From here isolate the retained-window recovery state
            # machine from a second menu animation/hit-test: invoke the same
            # public QML presentation toggle used by the desktop surface.
            presentation_invoked = bool(
                QMetaObject.invokeMethod(root, "toggleChatPresentation")
            )
            QApplication.processEvents()
            # Native Qt.Tool restoration is asynchronous on Windows.  Wait for
            # the QML recovery probe to finish its stable-window checks instead
            # of accepting a transient Windowed state between retry turns.
            # The extra headroom keeps a busy software-rendered suite from
            # exhausting the verifier deadline before the bounded probe does.
            restore_deadline = time.monotonic() + 2.5
            restore_started = time.monotonic()
            settled_checks = 0
            minimized_after = True
            recovery_armed_after = True
            recovery_trace: list[dict[str, object]] = []
            previous_recovery_state: tuple[bool, bool, int] | None = None
            while time.monotonic() < restore_deadline:
                minimized_after = (
                    chat_window.visibility() == QWindow.Visibility.Minimized
                )
                recovery_armed_after = bool(
                    chat_window.property("presentationRecoveryArmed")
                )
                qml_stable_checks = int(
                    chat_window.property("presentationStableChecks") or 0
                )
                recovery_state = (
                    minimized_after,
                    recovery_armed_after,
                    qml_stable_checks,
                )
                if recovery_state != previous_recovery_state:
                    recovery_trace.append(
                        {
                            "elapsedMs": round(
                                (time.monotonic() - restore_started) * 1000,
                                1,
                            ),
                            "minimized": minimized_after,
                            "armed": recovery_armed_after,
                            "qmlStableChecks": qml_stable_checks,
                        }
                    )
                    previous_recovery_state = recovery_state
                if not minimized_after and not recovery_armed_after:
                    settled_checks += 1
                    if settled_checks >= 5:
                        break
                else:
                    settled_checks = 0
                QTest.qWait(25)
                QApplication.processEvents()
            cycle_result = {
                "cycle": cycle_index + 1,
                "minimizedBefore": minimized_before_reopen,
                "minimizeSettledChecks": minimize_settled_checks,
                "invokeSucceeded": presentation_invoked,
                "visibleAfter": chat_window.isVisible(),
                "minimizedAfter": minimized_after,
                "recoveryArmedAfter": recovery_armed_after,
                "settledChecks": settled_checks,
                "recoveryTrace": recovery_trace,
                "page": int(chat_window.property("page")),
            }
            cycle_result["passed"] = bool(
                cycle_result["minimizedBefore"]
                and cycle_result["invokeSucceeded"]
                and cycle_result["visibleAfter"]
                and not cycle_result["minimizedAfter"]
                and not cycle_result["recoveryArmedAfter"]
                and cycle_result["settledChecks"] >= 5
                and cycle_result["page"] == 0
            )
            restore_cycles.append(cycle_result)
        outcome["chatRestoredAfterMinimize"] = {
            "cycles": restore_cycles,
            "passed": all(bool(value["passed"]) for value in restore_cycles),
        }
        backend.setChatOpen(False)
        QApplication.processEvents()

    def verify_visible_action_input_gate() -> None:
        point = item_center(chat_action)
        animation_budget = dict(outcome.get("animationBudget") or {})
        animation_budget.update(
            activeLowPower=bool(pet_body.property("lowPower")),
            activeTargetFps=int(pet_body.property("targetFps")),
            activeHighMotion=bool(compact_window.property("highMotion")),
        )
        animation_budget["passed"] = bool(
            animation_budget.get("idleLowPower")
            and animation_budget.get("idleTargetFps") == 15
            and not animation_budget.get("idleHighMotion")
            and not animation_budget.get("activeLowPower")
            and animation_budget.get("activeTargetFps") == 60
            and animation_budget.get("activeHighMotion")
        )
        outcome["animationBudget"] = animation_budget
        outcome["visibleActionInputGate"] = {
            "visible": chat_action.isVisible(),
            "interactive": bool(
                pet_window.property("compactActionsInteractive")
            ),
            "orbitProgress": float(
                compact_window.property("orbitProgress") or 0.0
            ),
            "nativeHit": hit_test.accepts_point(point.x(), point.y()),
            "waitSatisfied": bool(
                visible_action_gate_wait.get("satisfied", False)
            ),
            "waitAttempts": int(
                visible_action_gate_wait.get("attempts", 0)
            ),
            "waitElapsedMs": round(
                max(
                    0.0,
                    (
                        time.monotonic()
                        - float(
                            visible_action_gate_wait.get(
                                "startedAt", time.monotonic()
                            )
                        )
                    )
                    * 1000,
                ),
                1,
            ),
        }
        outcome["visibleActionInputGate"]["passed"] = bool(
            outcome["visibleActionInputGate"]["visible"]
            and outcome["visibleActionInputGate"]["interactive"]
            and outcome["visibleActionInputGate"]["nativeHit"]
            and outcome["visibleActionInputGate"]["waitSatisfied"]
        )

    def verify_settings_click() -> None:
        outcome["desktopDiscoveryInRadialMenu"] = {
            "label": str(desktop_discovery_label.property("text")),
            "visible": bool(desktop_discovery_label.isVisible()),
            "shellModeBefore": str(backend.shellMode),
            "actionIds": [
                str(value.get("action", "")) for value in backend.quickActions
            ],
        }
        click_action("settings")
        QApplication.processEvents()
        settings_screenshot = (
            PROJECT_ROOT / "artifacts" / "desktop-discovery-settings.png"
        )
        chat_window.grabWindow().save(str(settings_screenshot))
        outcome["settingsClickWorked"] = bool(backend.chatOpen)
        outcome["menuClosedAfterSettings"] = not bool(root.property("compactExpanded"))
        outcome["desktopDiscoveryInSettings"] = {
            "cardVisible": bool(desktop_discovery_card.isVisible()),
            "buttonVisible": bool(desktop_shell_toggle.isVisible()),
            "buttonText": str(desktop_shell_toggle.property("text")),
            "shellModeAfterOpening": str(backend.shellMode),
            "didNotAutoSwitch": str(backend.shellMode) == "compact",
            "radialHintHiddenAfterOpen": not bool(
                desktop_discovery_label.isVisible()
            ),
            "screenshot": str(settings_screenshot),
        }

        # Exercise the actual settings control in both directions.  Merely
        # finding the button does not prove that a compact pet can expand the
        # full desktop, nor that the same still-open settings surface can
        # immediately return to the transparent pet.
        settings_page_before = int(chat_window.property("page"))
        QTest.mouseClick(
            chat_window,
            Qt.MouseButton.LeftButton,
            pos=item_center(desktop_shell_toggle),
        )
        QTest.qWait(80)
        QApplication.processEvents()
        outcome["settingsDesktopExpand"] = {
            "pageBefore": settings_page_before,
            "pageAfter": int(chat_window.property("page")),
            "mode": str(backend.shellMode),
            "desktopVisible": root.isVisible(),
            "petVisible": pet_window.isVisible(),
            "settingsVisible": chat_window.isVisible(),
            "buttonText": str(desktop_shell_toggle.property("text")),
        }
        outcome["settingsDesktopExpand"]["passed"] = bool(
            outcome["settingsDesktopExpand"]["pageBefore"] == 4
            and outcome["settingsDesktopExpand"]["pageAfter"] == 4
            and outcome["settingsDesktopExpand"]["mode"] == "visual"
            and outcome["settingsDesktopExpand"]["desktopVisible"]
            and outcome["settingsDesktopExpand"]["petVisible"]
            and outcome["settingsDesktopExpand"]["settingsVisible"]
            and outcome["settingsDesktopExpand"]["buttonText"]
                == "收成透明桌宠"
        )

        QTest.mouseClick(
            chat_window,
            Qt.MouseButton.LeftButton,
            pos=item_center(desktop_shell_toggle),
        )
        QTest.qWait(80)
        QApplication.processEvents()
        outcome["settingsDesktopCollapse"] = {
            "page": int(chat_window.property("page")),
            "mode": str(backend.shellMode),
            "desktopHidden": not root.isVisible(),
            "petVisible": pet_window.isVisible(),
            "settingsVisible": chat_window.isVisible(),
            "buttonText": str(desktop_shell_toggle.property("text")),
        }
        outcome["settingsDesktopCollapse"]["passed"] = bool(
            outcome["settingsDesktopCollapse"]["page"] == 4
            and outcome["settingsDesktopCollapse"]["mode"] == "compact"
            and outcome["settingsDesktopCollapse"]["desktopHidden"]
            and outcome["settingsDesktopCollapse"]["petVisible"]
            and outcome["settingsDesktopCollapse"]["settingsVisible"]
            and outcome["settingsDesktopCollapse"]["buttonText"]
                == "展开莉莉丝桌面"
        )

        # The function library owns only three optional slots.  A fourth row
        # must be disabled before QML can optimistically toggle its local
        # checked state while the backend rejects the request.
        action_ids = ("companion", "work", "lilies-desktop", "focus")

        def function_checkbox(action_id: str) -> QQuickItem | None:
            return next(
                (
                    item
                    for item in CompactHitTestFilter._visual_descendants(
                        chat_window.contentItem()
                    )
                    if item.objectName() == f"functionLibraryPin_{action_id}"
                ),
                None,
            )

        choices: list[dict[str, object]] = []
        # Start from a deterministic empty optional library.  Each selection
        # rebuilds the QVariantList-backed Repeater, so let the replacement
        # delegates finish one short event turn before locating and clicking
        # the next row; otherwise a stale scene position can hit its neighbour
        # under software rendering.
        backend.clearQuickActions()
        QTest.qWait(80)
        QApplication.processEvents()
        core_before = [
            str(value.get("action", "")) for value in backend.quickActions[:3]
        ]
        for action_id in action_ids[:3]:
            QTest.qWait(80)
            QApplication.processEvents()
            checkbox = function_checkbox(action_id)
            if checkbox is None:
                choices.append({"action": action_id, "found": False})
                continue
            QTest.mouseClick(
                chat_window,
                Qt.MouseButton.LeftButton,
                pos=item_center(checkbox),
            )
            QTest.qWait(80)
            QApplication.processEvents()
            checkbox = function_checkbox(action_id)
            selected_ids = [
                str(value.get("action", ""))
                for value in backend.quickActions[3:]
            ]
            choices.append(
                {
                    "action": action_id,
                    "found": checkbox is not None,
                    "checked": bool(checkbox and checkbox.property("checked")),
                    "backendSelected": action_id in selected_ids,
                    "selectedIds": selected_ids,
                }
            )
        fourth = function_checkbox(action_ids[3])
        selected_before = [
            str(value.get("action", "")) for value in backend.quickActions[3:]
        ]
        fourth_before = bool(fourth and fourth.property("checked"))
        fourth_enabled = bool(fourth and fourth.isEnabled())
        if fourth is not None:
            QTest.mouseClick(
                chat_window,
                Qt.MouseButton.LeftButton,
                pos=item_center(fourth),
            )
            QApplication.processEvents()
        fourth = function_checkbox(action_ids[3])
        selected_after = [
            str(value.get("action", "")) for value in backend.quickActions[3:]
        ]
        core_after = [
            str(value.get("action", "")) for value in backend.quickActions[:3]
        ]
        outcome["functionLibraryChoiceCap"] = {
            "coreBefore": core_before,
            "coreAfter": core_after,
            "choices": choices,
            "selectedBeforeFourth": selected_before,
            "selectedAfterFourth": selected_after,
            "fourthEnabled": fourth_enabled,
            "fourthCheckedBefore": fourth_before,
            "fourthCheckedAfter": bool(fourth and fourth.property("checked")),
            "passed": (
                core_before == ["chat", "world", "settings"]
                and core_after == core_before
                and len(selected_before) == 3
                and selected_after == selected_before
                and all(
                    bool(value.get("found"))
                    and bool(value.get("checked"))
                    and bool(value.get("backendSelected"))
                    for value in choices
                )
                and not fourth_enabled
                and not fourth_before
                and not bool(fourth and fourth.property("checked"))
            ),
        }
        pinned_desktop_action = find_action("lilies-desktop")
        outcome["pinnedDesktopActionLoaded"] = {
            "backendSelected": "lilies-desktop" in selected_after,
            "qmlDelegateFound": pinned_desktop_action is not None,
            "repeaterCount": int(root.property("compactActionCount")),
        }
        outcome["pinnedDesktopActionLoaded"]["passed"] = bool(
            outcome["pinnedDesktopActionLoaded"]["backendSelected"]
            and outcome["pinnedDesktopActionLoaded"]["qmlDelegateFound"]
            and outcome["pinnedDesktopActionLoaded"]["repeaterCount"] == 6
        )
        backend.setChatOpen(False)
        QApplication.processEvents()

    def begin_radial_desktop_click() -> None:
        compact_window.setProperty("expanded", True)
        QApplication.processEvents()

    def click_radial_desktop() -> None:
        action = find_action("lilies-desktop")
        if action is None:
            outcome["radialDesktopExpand"] = {"clicked": False}
            return
        point = item_center(action)
        action_visible = action.isVisible()
        native_hit = hit_test.accepts_point(point.x(), point.y())
        outcome["radialDesktopExpand"] = {
            "clicked": click_action("lilies-desktop"),
            "actionVisible": action_visible,
            "nativeHit": native_hit,
        }

    def verify_radial_desktop_expand() -> None:
        result = dict(outcome.get("radialDesktopExpand") or {})
        result.update(
            mode=str(backend.shellMode),
            desktopVisible=root.isVisible(),
            petVisible=pet_window.isVisible(),
            menuClosed=not bool(root.property("compactExpanded")),
        )
        result["passed"] = bool(
            result.get("clicked")
            and result.get("actionVisible")
            and result.get("nativeHit")
            and result["mode"] == "visual"
            and result["desktopVisible"]
            and result["petVisible"]
            and result["menuClosed"]
        )
        outcome["radialDesktopExpand"] = result

    def click_radial_desktop_collapse() -> None:
        action = find_action("lilies-desktop")
        if action is None:
            outcome["radialDesktopCollapse"] = {"clicked": False}
            return
        point = item_center(action)
        action_visible = action.isVisible()
        native_hit = hit_test.accepts_point(point.x(), point.y())
        outcome["radialDesktopCollapse"] = {
            "clicked": click_action("lilies-desktop"),
            "actionVisible": action_visible,
            "nativeHit": native_hit,
        }

    def verify_radial_desktop_collapse() -> None:
        result = dict(outcome.get("radialDesktopCollapse") or {})
        result.update(
            mode=str(backend.shellMode),
            desktopHidden=not root.isVisible(),
            petVisible=pet_window.isVisible(),
            menuClosed=not bool(root.property("compactExpanded")),
        )
        result["passed"] = bool(
            result.get("clicked")
            and result.get("actionVisible")
            and result.get("nativeHit")
            and result["mode"] == "compact"
            and result["desktopHidden"]
            and result["petVisible"]
            and result["menuClosed"]
        )
        outcome["radialDesktopCollapse"] = result

    def verify_world_click() -> None:
        click_action("world")
        outcome["compactModeDuringWorldClick"] = str(backend.shellMode) == "compact"
        outcome["worldSceneVisible"] = bool(box_world_scene.isVisible())
        outcome["worldSceneOpen"] = bool(backend.boxWorldSceneOpen)
        outcome["worldClickWorked"] = (
            outcome["worldSceneOpen"] is True
            and outcome["worldSceneVisible"] is True
            and int(box_world_scene.property("presentationCount") or 0) >= 1
        )
        outcome["menuClosedAfterWorld"] = not bool(root.property("compactExpanded"))
        backend.setBoxWorldSceneOpen(False)
        QApplication.processEvents()

    def begin_direct_world_entry() -> None:
        # Exercise the backend route independently of the radial menu.  The
        # full paper-diorama scene, not merely a management tab, must restore
        # from its hidden first frame.
        backend.setBoxWorldSceneOpen(False)
        QApplication.processEvents()
        backend.enterBoxWorld()

    def verify_direct_world_entry() -> None:
        outcome["directWorldSceneVisible"] = bool(box_world_scene.isVisible())
        outcome["directWorldEntryWorked"] = (
            bool(backend.boxWorldSceneOpen)
            and outcome["directWorldSceneVisible"] is True
        )
        backend.setBoxWorldSceneOpen(False)
        QApplication.processEvents()

    def begin_component_world_entry() -> None:
        backend.setBoxWorldSceneOpen(False)
        backend.registry.invoke(
            "box-world", "enter", {}, origin="offscreen-ui", confirmed=True
        )

    def verify_component_world_entry() -> None:
        outcome["componentWorldSceneVisible"] = bool(box_world_scene.isVisible())
        outcome["componentWorldEntryWorked"] = (
            bool(backend.boxWorldSceneOpen)
            and outcome["componentWorldSceneVisible"] is True
        )
        backend.setBoxWorldSceneOpen(False)
        QApplication.processEvents()

    def verify_character_click() -> None:
        point = QPoint(
            int(float(pet_window.property("compactCharacterLeft"))
                + float(pet_window.property("compactCharacterWidth")) / 2),
            int(float(pet_window.property("compactCharacterTop"))
                + float(pet_window.property("compactCharacterHeight")) * 0.45),
        )
        outcome["characterClickPoint"] = [point.x(), point.y()]
        QTest.mouseClick(pet_window, Qt.MouseButton.LeftButton, pos=point)
        QApplication.processEvents()
        outcome["characterClickWorked"] = not bool(root.property("compactExpanded"))

    def verify_character_drag_threshold() -> None:
        compact_window.setProperty("expanded", False)
        backend.clearPetInteractionLocks()
        pet_window.cancelPositionAnimations()
        QApplication.processEvents()
        point = QPoint(
            int(float(pet_window.property("compactCharacterLeft"))
                + float(pet_window.property("compactCharacterWidth")) / 2),
            int(float(pet_window.property("compactCharacterTop"))
                + float(pet_window.property("compactCharacterHeight")) * 0.45),
        )
        start_x = int(pet_window.x())
        start_y = int(pet_window.y())
        # Keep click-threshold verification independent from any attached
        # target left by earlier profile tests.
        backend.detachPetHabitat(start_x, start_y)
        QApplication.processEvents()
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}

        pet_body.beginPointer(float(point.x()), float(point.y()))
        backend.offscreen_cursor = {"x": global_start.x() + 3, "y": global_start.y()}
        pet_body.movePointer(float(point.x() + 3), float(point.y()), True)
        pet_body.endPointer()
        QApplication.processEvents()
        outcome["threePixelJitterRemainsClick"] = (
            bool(root.property("compactExpanded"))
            and not bool(pet_window.property("manualDragActive"))
            and int(pet_window.x()) == start_x
            and int(pet_window.y()) == start_y
        )
        compact_window.setProperty("expanded", False)

        # The threshold is radial pointer travel, not the sum of the two
        # axes.  A common small diagonal tremor (3, 2) remains below 4 px and
        # must therefore retain click semantics without nudging the window.
        diagonal_start_x = int(pet_window.x())
        diagonal_start_y = int(pet_window.y())
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}
        pet_body.beginPointer(float(point.x()), float(point.y()))
        backend.offscreen_cursor = {
            "x": global_start.x() + 3,
            "y": global_start.y() + 2,
        }
        pet_body.movePointer(float(point.x() + 3), float(point.y() + 2), True)
        pet_body.endPointer()
        QApplication.processEvents()
        outcome["diagonalSubFourPixelJitterRemainsClick"] = (
            bool(root.property("compactExpanded"))
            and not bool(pet_window.property("manualDragActive"))
            and int(pet_window.x()) == diagonal_start_x
            and int(pet_window.y()) == diagonal_start_y
        )
        compact_window.setProperty("expanded", False)

        # The release-side window-position fallback uses the same radial
        # threshold.  Simulate a tiny compositor/window drift without a QML
        # move signal; (3, 2) must still complete as a click.
        fallback_start_x = int(pet_window.x())
        fallback_start_y = int(pet_window.y())
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}
        pet_body.beginPointer(float(point.x()), float(point.y()))
        pet_window.setX(fallback_start_x + 3)
        pet_window.setY(fallback_start_y + 2)
        pet_body.endPointer()
        QApplication.processEvents()
        outcome["diagonalWindowDriftRemainsClick"] = (
            bool(root.property("compactExpanded"))
            and not bool(pet_window.property("manualDragActive"))
            and int(pet_window.x()) == fallback_start_x + 3
            and int(pet_window.y()) == fallback_start_y + 2
        )
        compact_window.setProperty("expanded", False)

        # A real habitat change can arrive while the mouse is held.  It must
        # remain deferred until a click release clears manualDragActive.
        # Earlier presentation checks may intentionally leave the offscreen
        # test window beyond the synthetic monitor.  Normalize it first so
        # this probe measures deferral rather than the unrelated safety clamp
        # performed by applyHabitatState().
        screen_geometry = pet_window.screen().availableGeometry()
        minimum_x = int(screen_geometry.left())
        maximum_x = int(screen_geometry.right() + 1 - pet_window.width())
        minimum_y = int(screen_geometry.top())
        maximum_y = int(screen_geometry.bottom() + 1 - pet_window.height())
        pet_window.cancelPositionAnimations()
        pet_window.setProperty("geometryClampActive", True)
        pet_window.setX(max(minimum_x, min(int(pet_window.x()), maximum_x)))
        pet_window.setY(max(minimum_y, min(int(pet_window.y()), maximum_y)))
        pet_window.setProperty("geometryClampActive", False)
        QApplication.processEvents()
        replay_start_x = int(pet_window.x())
        replay_start_y = int(pet_window.y())
        habitat_dx = 32 if replay_start_x + 32 <= maximum_x else -32
        target_x = replay_start_x + habitat_dx
        attached_state = dict(backend._habitat_status)
        attached_state.update({
            "attached": True,
            "visible": True,
            "state": "normal",
            "profile": "desktop",
            "pose": "",
            "x": replay_start_x,
            "y": replay_start_y,
        })
        backend._habitat_status = attached_state
        backend.habitatChanged.emit()
        QApplication.processEvents()
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}
        pet_body.beginPointer(float(point.x()), float(point.y()))
        attached_state = dict(attached_state)
        attached_state["x"] = target_x
        backend._habitat_status = attached_state
        backend.habitatChanged.emit()
        QApplication.processEvents()
        habitat_change_deferred = int(pet_window.x()) == replay_start_x
        pet_body.endPointer()
        QTest.qWait(340)
        outcome["deferredHabitatReplayDiagnostics"] = {
            "start": [replay_start_x, replay_start_y],
            "target": [target_x, replay_start_y],
            "actual": [int(pet_window.x()), int(pet_window.y())],
            "deferred": habitat_change_deferred,
            "attached": bool(backend._habitat_status.get("attached")),
            "manualDragActive": bool(pet_window.property("manualDragActive")),
            "dragMoved": bool(pet_window.property("dragMoved")),
        }
        outcome["deferredHabitatReplayedAfterClick"] = (
            habitat_change_deferred
            and abs(int(pet_window.x()) - target_x) <= 2
            and int(pet_window.y()) == replay_start_y
        )
        compact_window.setProperty("expanded", False)

        drag_start_x = int(pet_window.x())
        drag_start_y = int(pet_window.y())
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}
        pet_body.beginPointer(float(point.x()), float(point.y()))
        backend.offscreen_cursor = {"x": global_start.x() + 5, "y": global_start.y()}
        pet_body.movePointer(float(point.x() + 5), float(point.y()), True)
        pet_window.followPointerFrame()
        QApplication.processEvents()
        five_pixel_drag_active = bool(pet_window.property("dragMoved"))
        pet_body.endPointer()
        QTest.qWait(80)
        outcome["fivePixelMovementStartsDrag"] = (
            five_pixel_drag_active
            and not bool(root.property("compactExpanded"))
            and not bool(pet_window.property("manualDragActive"))
        )
        outcome["realDragDoesNotReplayAttachment"] = (
            not bool(backend._habitat_status.get("attached"))
            and abs(int(pet_window.x()) - (drag_start_x + 5)) <= 2
            and int(pet_window.y()) == drag_start_y
        )
        backend.offscreen_cursor = None

    def verify_character_drag() -> None:
        point = QPoint(
            int(float(pet_window.property("compactCharacterLeft"))
                + float(pet_window.property("compactCharacterWidth")) / 2),
            int(float(pet_window.property("compactCharacterTop"))
                + float(pet_window.property("compactCharacterHeight")) * 0.45),
        )
        start_x = int(pet_window.x())
        start_y = int(pet_window.y())
        screen_geometry = pet_window.screen().geometry()
        right_room = screen_geometry.right() + 1 - (start_x + int(pet_window.width()))
        left_room = start_x - screen_geometry.left()
        drag_direction = 1 if right_room >= 44 or right_room >= left_room else -1
        outcome["dragStartPoint"] = [point.x(), point.y()]
        outcome["dragDirection"] = drag_direction
        outcome["menuClosedBeforeDrag"] = not bool(root.property("compactExpanded"))
        global_start = pet_window.mapToGlobal(point)
        backend.offscreen_cursor = {"x": global_start.x(), "y": global_start.y()}

        # Production app.py installs this bridge on petWindow. Install it only
        # for the real QTest gesture here so earlier direct helper calls remain
        # intentionally independent from a native event stream.
        pet_window.installEventFilter(pointer_event_filter)
        QTest.mousePress(pet_window, Qt.MouseButton.LeftButton, pos=point)
        QApplication.processEvents()
        outcome["manualDragActiveAfterPress"] = bool(
            pet_window.property("manualDragActive")
        )

        backend.offscreen_cursor = {
            "x": global_start.x() + drag_direction * 20,
            "y": global_start.y(),
        }
        QTest.mouseMove(
            pet_window, point + QPoint(drag_direction * 20, 0), delay=20
        )
        pet_window.followPointerFrame()
        QApplication.processEvents()
        outcome["dragFirstDelta"] = [
            int(pet_window.x()) - start_x,
            int(pet_window.y()) - start_y,
        ]
        # The window followed the first movement, so this same held-local point
        # is now 20 px farther right in global space.  Sending it again models
        # the real captured-pointer stream: the cursor reaches +40 globally
        # while remaining at the same grab offset inside the moving window.
        backend.offscreen_cursor = {
            "x": global_start.x() + drag_direction * 40,
            "y": global_start.y(),
        }
        QTest.mouseMove(
            pet_window, point + QPoint(drag_direction * 20, 0), delay=20
        )
        pet_window.followPointerFrame()
        QApplication.processEvents()
        immediate_dx = int(pet_window.x()) - start_x
        immediate_dy = int(pet_window.y()) - start_y
        outcome["dragImmediateDelta"] = [immediate_dx, immediate_dy]
        outcome["manualDragActiveDuringMove"] = bool(
            pet_window.property("manualDragActive")
        )
        outcome["dragFollowedImmediately"] = (
            abs(outcome["dragFirstDelta"][0] - drag_direction * 20) <= 4
            and abs(outcome["dragFirstDelta"][1]) <= 3
            and abs(immediate_dx - drag_direction * 40) <= 6
            and abs(immediate_dy) <= 3
        )

        # The window has followed the pointer by 40 px, so the original local
        # point now represents the same global cursor position for release.
        QTest.mouseRelease(pet_window, Qt.MouseButton.LeftButton, pos=point)
        QTest.qWait(80)
        outcome["manualDragActiveAfterRelease"] = bool(
            pet_window.property("manualDragActive")
        )
        outcome["dragReleaseDelta"] = [
            int(pet_window.x()) - start_x,
            int(pet_window.y()) - start_y,
        ]
        outcome["dragMenuStayedClosed"] = not bool(root.property("compactExpanded"))
        outcome["breathScaleYDuringDrag"] = float(
            root.property("compactPetBreathScaleY")
        )
        pet_window.removeEventFilter(pointer_event_filter)
        backend.offscreen_cursor = None

    def verify_cross_monitor_and_high_dpi_drag() -> None:
        original_size = float(root.property("compactBoxSize"))
        original_preferred_size = float(root.property("preferredCompactBoxSize"))
        original_x = int(pet_window.x())
        original_y = int(pet_window.y())
        regular_work_areas = [
            {
                "left": -960,
                "top": 0,
                "right": 0,
                "bottom": 700,
                "width": 960,
                "height": 700,
                "name": "synthetic-left",
                "devicePixelRatio": 1.5,
            },
            {
                "left": 0,
                "top": 0,
                "right": 960,
                "bottom": 540,
                "width": 960,
                "height": 540,
                "name": "synthetic-200pct",
                "devicePixelRatio": 2.0,
            },
        ]
        backend.offscreen_work_areas = regular_work_areas

        # Model the queued startup constraint after a saved layout has placed
        # the pet on the negative-coordinate display.  Work-area lookup must
        # retain that screen instead of snapping the window onto the primary.
        root.setProperty("preferredCompactBoxSize", 184.0)
        root.setProperty("compactBoxSize", 184.0)
        pet_window.setX(-840)
        pet_window.setY(24)
        root.constrainCompactPet(False)
        QApplication.processEvents()
        outcome["startupSafetyConstraint"] = {
            "position": [float(pet_window.x()), float(pet_window.y())],
            "passed": (
                pet_window.x() < 0.0
                and pet_window.x() >= -952.0
                and pet_window.x() + pet_window.width() <= -8.0
                and pet_window.y() >= 8.0
                and pet_window.y() + pet_window.height() <= 692.0
            ),
        }

        # A 1080p monitor at 200% scaling exposes about 960x540 logical px.
        # The entire 3.5x3.3 pet window must remain inside that work area.
        root.setProperty("preferredCompactBoxSize", 320.0)
        root.setProperty("compactBoxSize", 320.0)
        pet_window.setX(220)
        pet_window.setY(120)
        root.constrainCompactPet(False)
        QApplication.processEvents()
        fitted_size = float(root.property("compactBoxSize"))
        outcome["highDpiCompactFit"] = {
            "size": fitted_size,
            "geometry": [
                int(pet_window.x()),
                int(pet_window.y()),
                int(pet_window.width()),
                int(pet_window.height()),
            ],
            "passed": (
                110.0 <= fitted_size <= (540.0 - 16.0) / 3.30 + 0.01
                and pet_window.x() >= 8
                and pet_window.y() >= 8
                and pet_window.x() + pet_window.width() <= 952
                and pet_window.y() + pet_window.height() <= 532
            ),
        }

        # Reproduce the v0.3.8 regression exactly: the normal 184px pet crosses
        # onto a 960x540 logical screen.  The fit changes its effective size,
        # but the normalized point held by the user must remain under the
        # cursor instead of the oversized geometry collapsing to (0, 0).
        root.setProperty("preferredCompactBoxSize", 184.0)
        root.setProperty("compactBoxSize", 184.0)
        pet_window.setX(-820)
        pet_window.setY(40)
        pet_window.setProperty("dragGrabOffsetX", 140.0)
        pet_window.setProperty("dragGrabOffsetY", 100.0)
        pet_window.setProperty("dragStartCursorX", -680.0)
        pet_window.setProperty("dragStartCursorY", 140.0)
        pet_window.setProperty("dragMoved", True)
        pet_window.setProperty("manualDragActive", True)
        backend.offscreen_cursor = {"x": 450, "y": 100}
        pet_window.followGlobalPointer()
        QApplication.processEvents()
        crossed_size = float(root.property("compactBoxSize"))
        expected_grab_offset = [
            140.0 * crossed_size / 184.0,
            100.0 * crossed_size / 184.0,
        ]
        actual_grab_offset = [
            450.0 - float(pet_window.x()),
            100.0 - float(pet_window.y()),
        ]
        outcome["defaultSizeCrossMonitorDrag"] = {
            "size": crossed_size,
            "position": [float(pet_window.x()), float(pet_window.y())],
            "expectedGrabOffset": expected_grab_offset,
            "actualGrabOffset": actual_grab_offset,
            "passed": (
                110.0 <= crossed_size < 184.0
                and float(pet_window.x()) > 250.0
                and float(pet_window.y()) > 2.0
                and abs(actual_grab_offset[0] - expected_grab_offset[0]) <= 2.0
                and abs(actual_grab_offset[1] - expected_grab_offset[1]) <= 2.0
                and pet_window.x() + pet_window.width() <= 960.01
                and pet_window.y() + pet_window.height() <= 540.01
            ),
        }

        # Drag from a negative-coordinate monitor to the monitor on the right.
        # Pointer-based screen selection must not clamp the window to its old
        # QScreen before Qt has reassigned the native window.
        root.setProperty("preferredCompactBoxSize", 110.0)
        root.setProperty("compactBoxSize", 110.0)
        pet_window.setX(-900)
        pet_window.setY(90)
        pet_window.setProperty("dragGrabOffsetX", 90.0)
        pet_window.setProperty("dragGrabOffsetY", 70.0)
        pet_window.setProperty("dragStartCursorX", -810.0)
        pet_window.setProperty("dragStartCursorY", 160.0)
        pet_window.setProperty("dragMoved", True)
        pet_window.setProperty("manualDragActive", True)
        backend.offscreen_cursor = {"x": 310, "y": 190}
        pet_window.followGlobalPointer()
        QApplication.processEvents()
        outcome["crossMonitorDrag"] = {
            "position": [int(pet_window.x()), int(pet_window.y())],
            "passed": (
                abs(pet_window.x() - 220) <= 2
                and abs(pet_window.y() - 120) <= 2
                and 0 <= pet_window.x() < 960
                and 0 <= pet_window.y() < 540
            ),
        }

        # Detaching from an edge-peek pose must leave the whole pet clickable.
        pet_window.setProperty("manualDragActive", False)
        pet_window.setX(-100)
        pet_window.setY(88)
        root.constrainCompactPet(False)
        QApplication.processEvents()
        outcome["edgeDetachReclamped"] = {
            "position": [int(pet_window.x()), int(pet_window.y())],
            "passed": (
                pet_window.x() >= 8
                and pet_window.y() >= 8
                and pet_window.x() + pet_window.width() <= 952
                and pet_window.y() + pet_window.height() <= 532
            ),
        }

        # A pathological logical work area may be too small even for 110px.
        # Rendering can temporarily dip below that floor, while persistence
        # continues to store the normal preferred size.
        backend.offscreen_work_areas = [
            {
                "left": 0,
                "top": 0,
                "right": 300,
                "bottom": 260,
                "width": 300,
                "height": 260,
                "name": "synthetic-extreme-dpi",
                "devicePixelRatio": 3.0,
            }
        ]
        root.setProperty("preferredCompactBoxSize", 184.0)
        root.setProperty("compactBoxSize", 184.0)
        pet_window.setX(40)
        pet_window.setY(20)
        root.constrainCompactPet(False)
        QApplication.processEvents()
        emergency_size = float(root.property("compactBoxSize"))
        emergency_preferred = float(root.property("preferredCompactBoxSize"))
        saves_before_emergency_persist = len(backend.offscreen_box_layout_saves)
        root.persistCompactLayout()
        emergency_persist_writes = (
            len(backend.offscreen_box_layout_saves)
            - saves_before_emergency_persist
        )
        emergency_saved_layout = dict(backend.boxLayout())
        outcome["emergencyLogicalScreenFit"] = {
            "effectiveSize": emergency_size,
            "preferredSize": emergency_preferred,
            "savedSize": float(emergency_saved_layout["size"]),
            "geometry": [
                float(pet_window.x()),
                float(pet_window.y()),
                float(pet_window.width()),
                float(pet_window.height()),
            ],
            "persistWrites": emergency_persist_writes,
            "passed": (
                48.0 <= emergency_size < 110.0
                and emergency_preferred == 184.0
                and float(emergency_saved_layout["size"]) >= 110.0
                and emergency_persist_writes == 1
                and pet_window.x() >= 8.0
                and pet_window.y() >= 8.0
                and pet_window.x() + pet_window.width() <= 292.01
                and pet_window.y() + pet_window.height() <= 252.01
                and abs(backend.pet_habitat.pet_width - emergency_size * 3.50) <= 0.01
                and abs(backend.pet_habitat.pet_height - emergency_size * 3.30) <= 0.01
            ),
        }

        # Model several DragHandler translation events followed by its release
        # callback.  Movement is render-only; the release owns exactly one
        # compact_box_layout write.
        backend.offscreen_work_areas = regular_work_areas
        root.setProperty("preferredCompactBoxSize", 140.0)
        root.setProperty("compactBoxSize", 140.0)
        pet_window.setX(120)
        pet_window.setY(20)
        root.constrainCompactPet(False)
        saves_before_resize_drag = len(backend.offscreen_box_layout_saves)
        resize_sizes: list[float] = []
        for delta in (3.0, 4.0, -2.0, 5.0):
            root.resizeCompactPet(delta, False)
            resize_sizes.append(float(root.property("compactBoxSize")))
        resize_move_writes = (
            len(backend.offscreen_box_layout_saves) - saves_before_resize_drag
        )
        root.persistCompactLayout()
        resize_release_writes = (
            len(backend.offscreen_box_layout_saves)
            - saves_before_resize_drag
            - resize_move_writes
        )
        final_preferred_size = float(root.property("preferredCompactBoxSize"))
        last_resize_save = dict(backend.offscreen_box_layout_saves[-1])
        outcome["resizeDragPersistence"] = {
            "sizes": resize_sizes,
            "moveWrites": resize_move_writes,
            "releaseWrites": resize_release_writes,
            "savedSize": last_resize_save["size"],
            "preferredSize": final_preferred_size,
            "passed": (
                resize_move_writes == 0
                and resize_release_writes == 1
                and resize_sizes[-1] != resize_sizes[0]
                and abs(last_resize_save["size"] - final_preferred_size) <= 0.01
            ),
        }

        backend.offscreen_cursor = None
        backend.offscreen_work_areas = None
        root.setProperty("preferredCompactBoxSize", original_preferred_size)
        root.setProperty("compactBoxSize", original_size)
        pet_window.setX(original_x)
        pet_window.setY(original_y)
        backend.saveBoxLayout(original_x, original_y, original_preferred_size)
        backend.setCompactPetEffectiveSize(original_size)
        QApplication.processEvents()

    def show_companion_bubble() -> None:
        outcome["compactModeDuringCompanionBubble"] = str(backend.shellMode) == "compact"
        backend.companion._bubble = {
            "id": "offscreen-companion-bubble",
            "category": "科普",
            "summary": (
                "离屏验证：莉莉丝仍能在紧凑模式下递来一张短笺。"
                "正文需要自然换行，末行不能被按钮或滚动条挤住。"
            ),
            "detail": (
                "这条内容由验证脚本直接注入，不调用模型、截图或在线内容源。"
                "展开后应当保留舒展的行距，并允许阅读更长的补充说明。"
            ),
            "source": {},
            "actions": [],
            "sceneLabel": "紧凑模式",
            "createdAt": "2026-08-29T00:00:00Z",
            "expiresAt": "",
            "visible": True,
            "busy": False,
        }
        backend.companion.bubbleChanged.emit()
        QApplication.processEvents()

    original_companion_generate = backend.companion.runtime.generate
    original_companion_foreground = backend.companion._foreground_provider
    companion_generation_calls = {"count": 0}

    def generate_offscreen_companion(**kwargs: object) -> dict[str, str]:
        """Return distinct deterministic prose for each real UI request.

        The production controller deliberately suppresses a summary that is
        too close to a recently accepted bubble.  Reusing one fixed stub for
        both the chat-page button and the radial action therefore tested the
        duplicate guard, not the second button.  Keep this transport local
        and deterministic while modelling the runtime contract: a new UI
        request receives a new angle, and a duplicate retry (nonce > 0)
        receives a deliberately different angle again.
        """

        companion_generation_calls["count"] += 1
        call_index = int(companion_generation_calls["count"])
        variation_nonce = max(0, int(kwargs.get("variation_nonce") or 0))
        if variation_nonce > 0:
            summary = (
                "离屏验证：重复检测让真实按钮换了观察角度，"
                "莉莉丝从窗边递来另一枚纸星。"
            )
            detail = "重试请求携带了变化编号，并且没有复用上一张短笺。"
        elif call_index == 1:
            summary = "离屏验证：莉莉丝从真实按钮递来一张短笺。"
            detail = "这次点击经过完整的 QML 与控制器链路。"
        else:
            summary = (
                "离屏验证：径向菜单的真实按钮已经抵达，"
                "她把一句新的观察轻轻系在红绳上。"
            )
            detail = "第二个入口独立生成内容，因此不会被近似重复保护拦下。"
        return {
            "summary": summary,
            "detail": detail,
            "model": "gpt-5.6-luna",
            "contextType": "application-signal",
        }

    def begin_manual_companion_ui_click() -> None:
        # Exercise the actual QML Button -> requestNow -> async controller ->
        # CompanionBubble chain. The prose transport is replaced with a local
        # deterministic result so this verification never logs in, connects,
        # captures a window or reads the real foreground application.
        backend.companion.runtime.generate = generate_offscreen_companion
        backend.companion._foreground_provider = lambda: 0
        backend.companion.dismissExplicit()
        backend.companion.setPaused(False)
        backend.companion.setActivityEnabled(True)
        chat_window.setProperty("page", 3)
        backend.setChatOpen(True)
        chat_window.show()
        QApplication.processEvents()
        outcome["manualCompanionButton"] = {
            "visible": bool(companion_request_button.isVisible()),
            "enabled": bool(companion_request_button.isEnabled()),
            "clicked": True,
        }
        QTest.mouseClick(
            chat_window,
            Qt.MouseButton.LeftButton,
            pos=item_center(companion_request_button),
        )
        QApplication.processEvents()

    def verify_manual_companion_ui_click() -> None:
        result = dict(outcome.get("manualCompanionButton") or {})
        result.update(
            bubbleVisible=bool(companion_bubble.isVisible()),
            summary=str(companion_bubble.property("summaryText")),
            busy=bool(backend.companion.busy),
        )
        result["passed"] = bool(
            result.get("visible")
            and result.get("enabled")
            and result["bubbleVisible"]
            and "真实按钮" in result["summary"]
            and not result["busy"]
        )
        outcome["manualCompanionButton"] = result
        backend.companion.dismissExplicit()
        backend.setChatOpen(False)
        QApplication.processEvents()

    def begin_radial_companion_click() -> None:
        # The character/accessory opening gesture is already covered above.
        # Open the menu deterministically here so this phase isolates the
        # optional companion action's real MouseArea and requestNow route.
        compact_window.setProperty("expanded", True)
        QApplication.processEvents()

    def click_radial_companion() -> None:
        outcome["radialCompanionAction"] = {
            "clicked": click_action("companion")
        }

    def verify_radial_companion_click() -> None:
        result = dict(outcome.get("radialCompanionAction") or {})
        result.update(
            bubbleVisible=bool(companion_bubble.isVisible()),
            summary=str(companion_bubble.property("summaryText")),
            menuClosed=not bool(root.property("compactExpanded")),
            busy=bool(backend.companion.busy),
        )
        result["passed"] = bool(
            result.get("clicked")
            and result["bubbleVisible"]
            and "真实按钮" in result["summary"]
            and result["menuClosed"]
            and not result["busy"]
        )
        outcome["radialCompanionAction"] = result
        # A legitimate request failure opens the Companion chat page as its
        # product fallback.  Preserve that failure in the result above, then
        # clear the fixture-owned chat state so the later habitat transition
        # cannot be misreported as an artwork regression as well.
        backend.setChatOpen(False)
        # Keep the three selected optional actions alive through the real
        # radial click above; only reset the fixture after that route has been
        # exercised. Clearing them in the settings phase made this verifier
        # look for a delegate it had deliberately removed itself.
        backend.clearQuickActions()
        QApplication.processEvents()
        remaining_radial_actions = sorted(
            item.objectName().removeprefix("desktopPetAction_")
            for item in CompactHitTestFilter._visual_descendants(
                pet_window.contentItem()
            )
            if item.objectName().startswith("desktopPetAction_")
        )
        outcome["quickActionsCleared"] = {
            "backendActionIds": [
                str(value.get("action", "")) for value in backend.quickActions
            ],
            "qmlActionIds": remaining_radial_actions,
            "repeaterCount": int(root.property("compactActionCount")),
        }
        outcome["quickActionsCleared"]["passed"] = bool(
            outcome["quickActionsCleared"]["backendActionIds"]
            == ["chat", "world", "settings"]
            and remaining_radial_actions == ["chat", "settings", "world"]
            and outcome["quickActionsCleared"]["repeaterCount"] == 3
        )
        backend.companion.runtime.generate = original_companion_generate
        backend.companion._foreground_provider = original_companion_foreground
        backend.companion.dismissExplicit()
        QApplication.processEvents()

    def verify_companion_bubble() -> None:
        outcome["companionBubbleVisible"] = bool(companion_bubble.isVisible())
        outcome["companionBubbleSummary"] = str(
            companion_bubble.property("summaryText")
        )
        bubble_image = companion_bubble.grabWindow()
        bubble_screenshot = PROJECT_ROOT / "artifacts" / "companion-bubble-layout.png"
        bubble_screenshot.parent.mkdir(parents=True, exist_ok=True)
        bubble_image.save(str(bubble_screenshot))
        outcome["companionBubbleLayout"] = {
            "width": int(companion_bubble.width()),
            "height": int(companion_bubble.height()),
            "expanded": bool(companion_bubble.property("expanded")),
            "screenshot": str(bubble_screenshot),
        }
        backend.companion.dismissExplicit()
        QApplication.processEvents()
        outcome["companionBubbleDismissed"] = not bool(companion_bubble.isVisible())

    def verify_focus_timer() -> None:
        backend.focusStart(5)
        QTest.qWait(90)
        flags = focus_timer.flags()
        focus_image = focus_timer.grabWindow()
        focus_screenshot = PROJECT_ROOT / "artifacts" / "focus-timer-aura.png"
        focus_screenshot.parent.mkdir(parents=True, exist_ok=True)
        focus_image.save(str(focus_screenshot))
        outcome["focusTimerRunning"] = {
            "visible": bool(focus_timer.isVisible()),
            "state": str(focus_timer.property("visualState")),
            "time": str(focus_timer.property("timeText")),
            "doesNotAcceptFocus": bool(flags & Qt.WindowType.WindowDoesNotAcceptFocus),
            "transparentForInput": bool(flags & Qt.WindowType.WindowTransparentForInput),
            "screenshot": str(focus_screenshot),
        }
        backend.focusPause()
        QTest.qWait(45)
        outcome["focusTimerPaused"] = str(focus_timer.property("visualState")) == "paused"
        backend.focusResume()
        backend.focusFinish()
        QTest.qWait(45)
        outcome["focusTimerCompleted"] = (
            str(focus_timer.property("visualState")) == "finished"
            and bool(focus_timer.isVisible())
        )

    def verify_habitat_visual_adaptation() -> None:
        backend.pet_habitat.stable_seconds = 0
        work_area = {"left": 0, "top": 0, "right": 1920, "bottom": 1040}
        cases = (
            ("offscreen", {"left": -920, "top": 240, "right": 1080, "bottom": 850}, 32, False,
             "offscreen-window-edge", "edge-peek-live", "", False),
            ("micro", {"left": 100, "top": 600, "right": 350, "bottom": 780}, 32, False,
             "micro-window-edge", "edge-peek-live", "", False),
            ("small", {"left": 180, "top": 240, "right": 620, "bottom": 560}, 32, False,
             "small-title", "title-sit", "poseTitleSit", False),
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
        profile_results: dict[str, object] = {}
        for index, (
            name,
            rect,
            title_bar_height,
            maximized,
            expected_profile,
            expected_pose,
            expected_artwork_key,
            expected_mirror,
        ) in enumerate(cases):
            backend.pet_habitat.update_foreground(
                {
                    "handle": 987650 + index,
                    "rect": rect,
                    "workArea": work_area,
                    "visible": True,
                    "minimized": False,
                    "maximized": maximized,
                    "dpi": 96,
                    "titleBarHeight": title_bar_height,
                }
            )
            backend._habitat_status = backend.pet_habitat.status()
            backend.habitatChanged.emit()
            presentation_transition_started = bool(
                pet_body.property("poseTransitionRunning")
            )
            # Anchor equality is a settled-state invariant.  During the first
            # 280 ms the new habitat blend intentionally interpolates from the
            # prior contact point, so sampling at the old 45 ms mark would
            # mistake the smooth transition itself for anchor drift.
            QTest.qWait(330)
            pump_pet_frame()
            representation_deadline = time.monotonic() + 1.2
            while time.monotonic() < representation_deadline:
                pending_uses_artwork = bool(
                    pet_body.property("usesPoseArtwork")
                )
                pending_frame = (
                    pose_artwork_frame
                    if pending_uses_artwork
                    else figure_frame
                )
                if (
                    not bool(pet_body.property("poseTransitionRunning"))
                    and pending_frame.isVisible()
                ):
                    break
                pump_pet_frame(20)

            state = dict(backend._habitat_status)
            expected_scale = float(state["characterScale"])
            rendered_anchor_norm_x = float(
                pet_body.property("renderedAnchorNormX")
            )
            rendered_anchor_norm_y = float(
                pet_body.property("renderedAnchorNormY")
            )
            rendered_contact_x = float(pet_body.property("renderedContactX"))
            rendered_contact_y = float(pet_body.property("renderedContactY"))
            expected_anchor_x = float(pet_body.width()) * rendered_anchor_norm_x
            expected_anchor_y = float(pet_body.height()) * rendered_anchor_norm_y
            uses_pose_artwork = bool(pet_body.property("usesPoseArtwork"))
            active_frame = pose_artwork_frame if uses_pose_artwork else figure_frame
            actual_anchor_x = float(active_frame.x()) + float(
                active_frame.width()
            ) * rendered_contact_x
            actual_anchor_y = float(active_frame.y()) + float(
                active_frame.height()
            ) * rendered_contact_y
            profile = str(pet_body.property("habitatProfile"))
            pose = str(pet_body.property("pose"))
            artwork_key = str(pet_body.property("poseArtworkKey"))
            scale = float(pet_body.property("habitatCharacterScale"))
            mirrored = bool(pet_body.property("habitatMirror"))
            representation_visible = bool(active_frame.isVisible())
            anchor_error = [
                round(actual_anchor_x - expected_anchor_x, 3),
                round(actual_anchor_y - expected_anchor_y, 3),
            ]
            profile_results[name] = {
                "profile": profile,
                "pose": pose,
                "artworkKey": artwork_key,
                "artworkSource": str(
                    pet_body.property("poseArtworkSource") or ""
                ),
                "displayedArtworkSource": str(
                    pose_artwork_frame.property("displayedSource") or ""
                ),
                "artworkBlend": round(
                    float(pet_body.property("renderedArtworkBlend") or 0.0), 4
                ),
                "artworkBlendSyncCount": int(
                    pet_body.property("artworkBlendSyncCount") or 0
                ),
                "interactionSnap": bool(
                    pet_body.property("interactionSnap")
                ),
                "representation": "artwork" if uses_pose_artwork else "layered",
                "scale": scale,
                "mirrored": mirrored,
                "presentationTransitionStarted": presentation_transition_started,
                "representationVisible": representation_visible,
                "anchorError": anchor_error,
                "passed": (
                    profile == expected_profile
                    and pose == expected_pose
                    and artwork_key == expected_artwork_key
                    and uses_pose_artwork is (expected_artwork_key != "")
                    and abs(scale - expected_scale) < 0.001
                    and mirrored is expected_mirror
                    and representation_visible
                    and abs(anchor_error[0]) <= 1.0
                    and abs(anchor_error[1]) <= 1.0
                ),
            }

        outcome["habitatProfiles"] = profile_results
        maximized_result = dict(profile_results["maximized"])
        outcome["habitatVisualProfile"] = maximized_result["profile"]
        outcome["habitatPose"] = maximized_result["pose"]
        outcome["habitatCharacterScale"] = maximized_result["scale"]
        outcome["habitatMirrored"] = maximized_result["mirrored"]
        outcome["habitatArtworkVisible"] = bool(pose_artwork_frame.isVisible())
        outcome["habitatAnchorError"] = maximized_result["anchorError"]
        outcome["habitatVisualAdaptationWorked"] = all(
            bool(dict(value).get("passed")) for value in profile_results.values()
        ) and bool(dict(profile_results["top-space"])["presentationTransitionStarted"])

        # Re-enter the verified top-space artwork pose before removing its
        # host.  The maximized profile above is intentionally procedural now,
        # so detaching directly from it would only exercise a layered-to-
        # layered transition and silently drop bitmap cross-fade coverage.
        backend.pet_habitat.update_foreground(
            {
                "handle": 987699,
                "rect": {"left": 180, "top": 8, "right": 1180, "bottom": 780},
                "workArea": work_area,
                "visible": True,
                "minimized": False,
                "maximized": False,
                "dpi": 96,
                "titleBarHeight": 32,
            }
        )
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        artwork_ready_deadline = time.monotonic() + 1.8
        artwork_ready = False
        while time.monotonic() < artwork_ready_deadline:
            artwork_ready = bool(
                pet_body.property("usesPoseArtwork")
                and pose_artwork_frame.isVisible()
                and not bool(pet_body.property("poseTransitionRunning"))
                and float(pet_body.property("renderedArtworkBlend")) >= 0.999
            )
            if artwork_ready:
                break
            pump_pet_frame(20)

        # Removing an artwork host used to switch to the layered outfit in a
        # single frame.  Both representations should overlap briefly, while
        # the retained bitmap and deterministic aspect ratio keep the visual
        # contact point stable.
        backend.pet_habitat.update_foreground(None)
        backend._habitat_status = backend.pet_habitat.status()
        backend.habitatChanged.emit()
        transition_started = bool(pet_body.property("poseTransitionRunning"))
        middle_blend = float(pet_body.property("renderedArtworkBlend"))
        middle_habitat_blend = float(pet_body.property("renderedHabitatBlend"))
        middle_visible = False
        # A source/mirror cross-fade may finish on the same event turn that
        # starts the detach blend.  Poll for the real overlap frame instead of
        # assuming it must occur at one fixed 70 ms sample.
        for _ in range(14):
            pump_pet_frame(20)
            middle_blend = float(pet_body.property("renderedArtworkBlend"))
            middle_habitat_blend = float(pet_body.property("renderedHabitatBlend"))
            middle_visible = pose_artwork_frame.isVisible() and figure_frame.isVisible()
            if 0.0 < middle_blend < 1.0 and middle_visible:
                break
        pump_pet_frame(300)
        outcome["habitatDetachTransition"] = {
            "artworkReadyBeforeDetach": artwork_ready,
            "started": transition_started,
            "middleArtworkBlend": round(middle_blend, 4),
            "middleHabitatBlend": round(middle_habitat_blend, 4),
            "bothFramesVisibleMidway": middle_visible,
            "finishedArtworkBlend": round(
                float(pet_body.property("renderedArtworkBlend")), 4
            ),
            "passed": (
                artwork_ready
                and transition_started
                and 0.0 < middle_blend < 1.0
                and 0.0 < middle_habitat_blend < 1.0
                and middle_visible
                and float(pet_body.property("renderedArtworkBlend")) <= 0.001
                and not pose_artwork_frame.isVisible()
                and figure_frame.isVisible()
            ),
        }

    def verify_suppressed_overlay_lifecycle() -> None:
        backend.setChatOpen(True)
        backend.setWorkPanelOpen(True)
        backend.setBoxWorldSceneOpen(True)
        connector_setup.setProperty("requestedVisible", True)
        backend._selection_bubble = {
            "visible": True,
            "text": "仅用于离屏静默验证",
            "busy": False,
            "error": False,
            "x": 500,
            "y": 300,
        }
        backend.selectionChanged.emit()
        selection_question.setProperty("requested", True)
        compact_window.setProperty("expanded", True)
        pet_window.setProperty("manualDragActive", True)
        backend.setPetInteractionLocked(True)
        QTest.qWait(80)
        outcome["suppressionBefore"] = {
            "pet": pet_window.isVisible(),
            "chat": chat_window.isVisible(),
            "work": work_panel.isVisible(),
            "world": box_world_scene.isVisible(),
            "connector": connector_setup.isVisible(),
            # Pointer-critical movement deliberately removes auxiliary Quick
            # windows from the compositor.  The drafted follow-up survives
            # that temporary hide, while the privacy transition below owns
            # the later cancellation.
            "questionHiddenByDrag": not selection_question.isVisible(),
            "questionRequestPreserved": bool(
                selection_question.property("requested")
            ),
            "menu": bool(compact_window.property("expanded")),
            "drag": bool(pet_window.property("manualDragActive")),
            "interactionLocked": bool(backend._pet_interaction_locked),
        }

        backend.pet_habitat.set_presence("silent")
        backend._sync_habitat_state(force_cleanup=True)
        backend.applicationActivationRequested.emit("show")
        QTest.qWait(80)
        outcome["suppressionDuring"] = {
            "petHidden": not pet_window.isVisible(),
            "activationStayedHidden": not pet_window.isVisible(),
            "chatHidden": not chat_window.isVisible(),
            "workHidden": not work_panel.isVisible(),
            "worldHidden": not box_world_scene.isVisible(),
            "connectorHidden": not connector_setup.isVisible(),
            "questionHidden": not selection_question.isVisible(),
            "questionCancelled": not bool(selection_question.property("requested")),
            "menuCollapsed": not bool(compact_window.property("expanded")),
            "dragCancelled": not bool(pet_window.property("manualDragActive")),
            "interactionUnlocked": not bool(backend._pet_interaction_locked),
        }

        backend.pet_habitat.set_presence("normal")
        backend._sync_habitat_state()
        QTest.qWait(80)
        outcome["suppressionAfter"] = {
            "petRestored": pet_window.isVisible(),
            "chatRestored": chat_window.isVisible(),
            "workRestored": work_panel.isVisible(),
            "worldRestored": box_world_scene.isVisible(),
            "connectorRestored": connector_setup.isVisible(),
            "questionStayedClosed": not selection_question.isVisible(),
            "menuStayedCollapsed": not bool(compact_window.property("expanded")),
            "interactionStayedUnlocked": not bool(backend._pet_interaction_locked),
        }
        backend.setChatOpen(False)
        backend.setWorkPanelOpen(False)
        backend.setBoxWorldSceneOpen(False)
        connector_setup.setProperty("requestedVisible", False)
        backend.dismissSelectionBubble()
        QApplication.processEvents()

    def verify_desktop_scene_lifecycle() -> None:
        """Exercise shell/presence visibility and renderer unloading offscreen."""

        def snapshot() -> dict[str, object]:
            desktop_scene = root.findChild(QQuickItem, "desktopScene")
            desktop_scene_lilith = root.findChild(QQuickItem, "desktopSceneLilith")
            cinematic_player = root.findChild(QObject, "desktopCinematicPlayer")
            return {
                "mode": str(backend.shellMode),
                "desktop": root.isVisible(),
                "renderer": str(backend.renderer),
                "scene": desktop_scene is not None and desktop_scene.isVisible(),
                "sceneLoaded": bool(root.property("desktopSceneLoaded")),
                "sceneLilith": (
                    desktop_scene_lilith is not None
                    and desktop_scene_lilith.isVisible()
                ),
                "sceneLilithSource": (
                    str(desktop_scene_lilith.property("source"))
                    if desktop_scene_lilith is not None
                    else ""
                ),
                "videoLoaded": bool(root.property("desktopVideoLoaded")),
                "videoPlayerExists": cinematic_player is not None,
                "videoPlaybackState": str(
                    root.property("desktopVideoPlaybackState")
                ),
                "pet": pet_window.isVisible(),
                "dock": paper_dock.isVisible(),
                "dockRaised": bool(paper_dock.property("raised")),
                "sceneActive": bool(backend.sceneActive),
            }

        states: dict[str, dict[str, object]] = {}
        backend.pet_habitat.set_presence("normal")
        backend._sync_habitat_state()
        backend.setRenderer("scene2d")
        backend.setShellMode("compact")
        QTest.qWait(60)
        states["compactNormal"] = snapshot()

        backend.setShellMode("visual")
        QTest.qWait(60)
        states["visualNormal"] = snapshot()

        # The same-window full-screen path is the real regression: no second
        # foreground event is involved, only a debounced native catalogue edge.
        backend._apply_foreground_context(
            ForegroundContext(
                9701,
                process_id=197,
                process_name="game.exe",
                window_class="UnityWndClass",
                full_screen=True,
                is_game=True,
            )
        )
        backend.pet_habitat.update_foreground(
            {
                "handle": 9701,
                "visible": True,
                "minimized": False,
                "fullScreen": True,
                "rect": [0, 0, 1280, 720],
                "workArea": [0, 0, 1280, 680],
            }
        )
        backend._sync_habitat_state(force_cleanup=True)
        QTest.qWait(60)
        states["visualSilent"] = snapshot()

        windowed_context = ForegroundContext(
            9701,
            process_id=197,
            process_name="game.exe",
            window_class="UnityWndClass",
            full_screen=False,
            is_game=True,
        )
        original_reader = backend.companion.reader
        backend.companion.reader = lambda _hwnd: windowed_context
        try:
            windowed_record = {
                "handle": 9701,
                "active": True,
                "visible": True,
                "minimized": False,
                "fullScreen": False,
                "rect": [80, 60, 1120, 660],
                "workArea": [0, 0, 1280, 680],
            }
            # Match Backend._on_window_catalog's production ordering: update
            # geometry first, then reconcile the event-derived presence bits.
            backend.pet_habitat.update_foreground(windowed_record)
            backend._reconcile_presence_from_catalog(windowed_record)
        finally:
            backend.companion.reader = original_reader
        QTest.qWait(60)
        states["visualRestored"] = snapshot()

        # A covered visual desktop may have left sceneActive false.  Compact
        # mode must reactivate the independent character immediately rather
        # than waiting on the now-hidden desktop timer.
        backend._scene_active = False
        backend.sceneActiveChanged.emit()
        backend.setShellMode("compact")
        QTest.qWait(60)
        states["compactAfterCoveredVisual"] = snapshot()

        # The video branch used to decode and start playing during compact
        # cold start because Component.onCompleted omitted the shell-mode
        # guard.  Renderer changes while compact must not construct either
        # the player or the 2D 4K scene; visual restores it synchronously.
        backend.setRenderer("video")
        QTest.qWait(80)
        states["compactVideo"] = snapshot()
        backend.setShellMode("visual")
        QTest.qWait(220)
        states["visualVideo"] = snapshot()
        backend.setShellMode("compact")
        QTest.qWait(120)
        states["compactVideoRestored"] = snapshot()
        backend.setRenderer("scene2d")

        outcome["desktopSceneLifecycle"] = {
            "states": states,
            "passed": (
                states["compactNormal"]["desktop"] is False
                and states["compactNormal"]["sceneLoaded"] is False
                and states["compactNormal"]["videoLoaded"] is False
                and states["compactNormal"]["pet"] is True
                and states["compactNormal"]["dock"] is False
                and states["visualNormal"]["desktop"] is True
                and states["visualNormal"]["scene"] is True
                and states["visualNormal"]["sceneLoaded"] is True
                and states["visualNormal"]["videoLoaded"] is False
                and states["visualNormal"]["sceneLilith"] is True
                and bool(states["visualNormal"]["sceneLilithSource"])
                and states["visualNormal"]["pet"] is True
                and states["visualNormal"]["dock"] is True
                and states["visualSilent"]["desktop"] is True
                and states["visualSilent"]["pet"] is False
                and states["visualSilent"]["dock"] is False
                and states["visualRestored"]["desktop"] is True
                and states["visualRestored"]["sceneLilith"] is True
                and states["visualRestored"]["pet"] is True
                and states["visualRestored"]["dock"] is True
                and states["visualRestored"]["dockRaised"] is False
                and states["compactAfterCoveredVisual"]["desktop"] is False
                and states["compactAfterCoveredVisual"]["pet"] is True
                and states["compactAfterCoveredVisual"]["dock"] is False
                and states["compactAfterCoveredVisual"]["sceneActive"] is True
                and states["compactAfterCoveredVisual"]["sceneLoaded"] is False
                and states["compactVideo"]["sceneLoaded"] is False
                and states["compactVideo"]["videoLoaded"] is False
                and states["compactVideo"]["videoPlayerExists"] is False
                and states["compactVideo"]["videoPlaybackState"] == "unloaded"
                and states["visualVideo"]["desktop"] is True
                and states["visualVideo"]["sceneLoaded"] is False
                and states["visualVideo"]["videoLoaded"] is True
                and states["visualVideo"]["videoPlayerExists"] is True
                and states["compactVideoRestored"]["desktop"] is False
                and states["compactVideoRestored"]["sceneLoaded"] is False
                and states["compactVideoRestored"]["videoLoaded"] is False
                and states["compactVideoRestored"]["videoPlayerExists"] is False
                and states["compactVideoRestored"]["videoPlaybackState"] == "unloaded"
            ),
        }

    def verify() -> None:
        image = pet_window.grabWindow()
        screenshot = PROJECT_ROOT / "artifacts" / "compact-ui-clicked.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(screenshot))
        backend.saveBoxLayout(123, 234, 196)
        backend.saveComponentLayout("chat", 0.42, -0.33, 1.18)
        backend.saveAccessoryBoxLayout(0.58, 0.49, 0.46)
        saved_box = dict(backend.boxLayout())
        saved_action = dict(backend.componentLayout("chat", -25))
        saved_accessory = dict(backend.accessoryBoxLayout())
        outcome.update(
            {
                "expandedAfterCharacterClick": bool(root.property("compactExpanded")),
                "actionCount": int(root.property("compactActionCount")),
                "actionsVisible": bool(root.property("compactActionsVisible")),
                "characterAsset": str(backend.assetUrl("desktopPet")),
                "wheelResizeWorked": float(root.property("compactBoxSize")) > initial_size,
                "resizeHandleExists": root.findChild(QObject, "desktopPetResizeHandle") is not None,
                "breathingAnimated": max(
                    float(outcome.get("breathScaleYStart", 0.0)),
                    float(outcome.get("breathScaleYDuringDrag", 0.0)),
                    float(root.property("compactPetBreathScaleY")),
                ) - min(
                    float(outcome.get("breathScaleYStart", 0.0)),
                    float(outcome.get("breathScaleYDuringDrag", 0.0)),
                    float(root.property("compactPetBreathScaleY")),
                ) > 0.0002,
                "breathScaleYEnd": float(root.property("compactPetBreathScaleY")),
                "layoutPersistence": {
                    "box": saved_box == {"x": 123.0, "y": 234.0, "size": 196.0},
                    "action": saved_action == {"dx": 0.42, "dy": -0.33, "scale": 1.18},
                    "accessory": saved_accessory == {"dx": 0.58, "dy": 0.49, "scale": 0.46},
                },
                "screenshot": str(screenshot),
            }
        )
        report = PROJECT_ROOT / "artifacts" / "compact-ui-verification.json"
        report.write_text(json.dumps(outcome, ensure_ascii=False, indent=2), "utf-8")
        backend.shutdown()
        app.quit()

    # Chain each step from the completion of the previous one.  Loading the
    # first high-resolution pose can legitimately delay the Qt render thread;
    # a collection of absolute timers would then all become overdue and run
    # re-entrantly inside QApplication.processEvents().  That did not model a
    # human interaction and intermittently collapsed the menu between press
    # and release.
    def step_start() -> None:
        verify_direct_desktop_mode_tab()
        sample_breath_start()
        QTimer.singleShot(550, step_open_initial_menu)

    def step_open_initial_menu() -> None:
        click_lilith()
        started_at = time.monotonic()
        visible_action_gate_wait.update(
            startedAt=started_at,
            # The first high-resolution pose upload can occupy the software
            # render thread for over a second on a busy suite worker.  Keep a
            # bounded six-second outer-event-loop wait; the runtime's 0.18
            # interaction threshold remains untouched.
            deadline=started_at + 6.0,
            attempts=0,
            satisfied=False,
        )
        QTimer.singleShot(20, step_wait_for_visible_action_gate)

    def step_wait_for_visible_action_gate() -> None:
        visible_action_gate_wait["attempts"] = int(
            visible_action_gate_wait.get("attempts", 0)
        ) + 1
        if bool(pet_window.property("compactActionsInteractive")):
            visible_action_gate_wait["satisfied"] = True
            verify_visible_action_input_gate()
            QTimer.singleShot(720, step_chat)
            return
        if time.monotonic() < float(visible_action_gate_wait["deadline"]):
            # QTest's offscreen QPA advances its animation driver while
            # qWait() pumps posted render events.  Keep each pump short and
            # return to the outer event loop between probes, so this remains a
            # bounded asynchronous condition wait rather than a fixed sleep.
            # Unlike a real exposed Windows surface, the offscreen QPA may
            # stop scheduling scene-graph frames once the window is otherwise
            # idle.  Explicitly request the next frame so the 640 ms orbit is
            # measured rather than left parked at its first interpolation
            # sample.
            pump_pet_frame(20)
            QTimer.singleShot(40, step_wait_for_visible_action_gate)
            return
        verify_visible_action_input_gate()
        QTimer.singleShot(720, step_chat)

    def step_chat() -> None:
        verify_chat_click()
        QTimer.singleShot(180, step_open_settings_menu)

    def step_open_settings_menu() -> None:
        open_action_menu()
        QTimer.singleShot(750, step_settings)

    def step_settings() -> None:
        verify_settings_click()
        QTimer.singleShot(180, step_open_radial_desktop)

    def step_open_radial_desktop() -> None:
        begin_radial_desktop_click()
        QTimer.singleShot(750, step_click_radial_desktop)

    def step_click_radial_desktop() -> None:
        click_radial_desktop()
        QTimer.singleShot(180, step_verify_radial_desktop_expand)

    def step_verify_radial_desktop_expand() -> None:
        verify_radial_desktop_expand()
        begin_radial_desktop_click()
        QTimer.singleShot(750, step_click_radial_desktop_collapse)

    def step_click_radial_desktop_collapse() -> None:
        click_radial_desktop_collapse()
        QTimer.singleShot(180, step_verify_radial_desktop_collapse)

    def step_verify_radial_desktop_collapse() -> None:
        verify_radial_desktop_collapse()
        QTimer.singleShot(180, step_open_world_menu)

    def step_open_world_menu() -> None:
        open_action_menu()
        QTimer.singleShot(750, step_world)

    def step_world() -> None:
        verify_world_click()
        QTimer.singleShot(180, step_direct_world)

    def step_direct_world() -> None:
        begin_direct_world_entry()
        QTimer.singleShot(100, step_verify_direct_world)

    def step_verify_direct_world() -> None:
        verify_direct_world_entry()
        QTimer.singleShot(100, step_component_world)

    def step_component_world() -> None:
        begin_component_world_entry()
        QTimer.singleShot(100, step_verify_component_world)

    def step_verify_component_world() -> None:
        verify_component_world_entry()
        QTimer.singleShot(180, step_open_character_menu)

    def step_open_character_menu() -> None:
        open_action_menu()
        QTimer.singleShot(750, step_character_click)

    def step_character_click() -> None:
        verify_character_click()
        QTimer.singleShot(300, step_character_drag_threshold)

    def step_character_drag_threshold() -> None:
        verify_character_drag_threshold()
        QTimer.singleShot(300, step_character_drag)

    def step_character_drag() -> None:
        verify_character_drag()
        QTimer.singleShot(300, step_cross_monitor_drag)

    def step_cross_monitor_drag() -> None:
        verify_cross_monitor_and_high_dpi_drag()
        QTimer.singleShot(300, step_manual_companion)

    def step_manual_companion() -> None:
        begin_manual_companion_ui_click()
        QTimer.singleShot(500, step_verify_manual_companion)

    def step_verify_manual_companion() -> None:
        verify_manual_companion_ui_click()
        QTimer.singleShot(200, step_open_radial_companion)

    def step_open_radial_companion() -> None:
        begin_radial_companion_click()
        QTimer.singleShot(750, step_click_radial_companion)

    def step_click_radial_companion() -> None:
        click_radial_companion()
        QTimer.singleShot(500, step_verify_radial_companion)

    def step_verify_radial_companion() -> None:
        verify_radial_companion_click()
        QTimer.singleShot(200, step_show_companion)

    def step_show_companion() -> None:
        show_companion_bubble()
        QTimer.singleShot(200, step_verify_companion)

    def step_verify_companion() -> None:
        verify_companion_bubble()
        QTimer.singleShot(200, step_focus)

    def step_focus() -> None:
        verify_focus_timer()
        QTimer.singleShot(300, step_habitat)

    def step_habitat() -> None:
        verify_habitat_visual_adaptation()
        QTimer.singleShot(250, step_suppression)

    def step_suppression() -> None:
        verify_suppressed_overlay_lifecycle()
        QTimer.singleShot(250, step_desktop_scene)

    def step_desktop_scene() -> None:
        verify_desktop_scene_lifecycle()
        QTimer.singleShot(300, verify)

    # Let the 220/280 ms initial pose blend finish before measuring the quiet
    # cadence.  The next steps then prove that opening the menu restores the
    # active cadence.
    QTimer.singleShot(420, step_start)
    app.exec()
    temporary.cleanup()
    passed = (
        outcome.get("startupCoordinateParsing", {}).get("passed") is True
        and outcome.get("startupSafetyConstraint", {}).get("passed") is True
        and outcome.get("directDesktopModeTab", {}).get("passed") is True
        and outcome.get("expandedAfterCharacterClick") is False
        and int(outcome.get("actionCount", 0)) == 3
        and outcome.get("actionsVisibleForClicks") is True
        and outcome.get("visibleActionInputGate", {}).get("passed") is True
        and all(
            any(
                bool(event.get("visible"))
                and bool(event.get("interactive"))
                and bool(event.get("nativeHit"))
                and bool(event.get("inputDispatched"))
                for event in dict(
                    outcome.get("radialActionClicks", {})
                ).get(action_id, [])
            )
            for action_id in ("chat", "world", "settings", "lilies-desktop")
        )
        and outcome.get("companionAwarenessOnPet", {}).get("passed") is True
        and outcome.get("wheelResizeWorked") is True
        and outcome.get("highResolutionWheel", {}).get("passed") is True
        and outcome.get("resizeHandleExists") is True
        and outcome.get("breathingAnimated") is True
        and outcome.get("animationBudget", {}).get("passed") is True
        and outcome.get("chatClickWorked") is True
        and outcome.get("menuClosedAfterChat") is True
        and outcome.get("menuInputClosedAfterChat") is True
        and outcome.get("chatRestoredAfterMinimize", {}).get("passed") is True
        and outcome.get("settingsClickWorked") is True
        and outcome.get("menuClosedAfterSettings") is True
        and outcome.get("desktopDiscoveryInRadialMenu", {}).get("label")
            == "完整桌面入口"
        and outcome.get("desktopDiscoveryInRadialMenu", {}).get("visible") is True
        and outcome.get("desktopDiscoveryInRadialMenu", {}).get("shellModeBefore")
            == "compact"
        and outcome.get("desktopDiscoveryInRadialMenu", {}).get("actionIds")
            == ["chat", "world", "settings"]
        and outcome.get("desktopDiscoveryInSettings", {}).get("cardVisible") is True
        and outcome.get("desktopDiscoveryInSettings", {}).get("buttonVisible") is True
        and outcome.get("desktopDiscoveryInSettings", {}).get("buttonText")
            == "展开莉莉丝桌面"
        and outcome.get("desktopDiscoveryInSettings", {}).get("didNotAutoSwitch") is True
        and outcome.get("desktopDiscoveryInSettings", {}).get(
            "radialHintHiddenAfterOpen"
        ) is True
        and outcome.get("settingsDesktopExpand", {}).get("passed") is True
        and outcome.get("settingsDesktopCollapse", {}).get("passed") is True
        and outcome.get("functionLibraryChoiceCap", {}).get("passed") is True
        and outcome.get("pinnedDesktopActionLoaded", {}).get("passed") is True
        and outcome.get("radialDesktopExpand", {}).get("passed") is True
        and outcome.get("radialDesktopCollapse", {}).get("passed") is True
        and outcome.get("worldClickWorked") is True
        and outcome.get("menuClosedAfterWorld") is True
        and outcome.get("compactModeDuringWorldClick") is True
        and outcome.get("worldSceneVisible") is True
        and outcome.get("worldSceneOpen") is True
        and outcome.get("directWorldEntryWorked") is True
        and outcome.get("directWorldSceneVisible") is True
        and outcome.get("componentWorldEntryWorked") is True
        and outcome.get("componentWorldSceneVisible") is True
        and outcome.get("characterClickWorked") is True
        and outcome.get("threePixelJitterRemainsClick") is True
        and outcome.get("diagonalSubFourPixelJitterRemainsClick") is True
        and outcome.get("diagonalWindowDriftRemainsClick") is True
        and outcome.get("deferredHabitatReplayedAfterClick") is True
        and outcome.get("fivePixelMovementStartsDrag") is True
        and outcome.get("realDragDoesNotReplayAttachment") is True
        and outcome.get("menuClosedBeforeDrag") is True
        and outcome.get("manualDragActiveAfterPress") is True
        and outcome.get("manualDragActiveDuringMove") is True
        and outcome.get("dragFollowedImmediately") is True
        and outcome.get("manualDragActiveAfterRelease") is False
        and outcome.get("dragMenuStayedClosed") is True
        and outcome.get("highDpiCompactFit", {}).get("passed") is True
        and outcome.get("defaultSizeCrossMonitorDrag", {}).get("passed") is True
        and outcome.get("crossMonitorDrag", {}).get("passed") is True
        and outcome.get("edgeDetachReclamped", {}).get("passed") is True
        and outcome.get("emergencyLogicalScreenFit", {}).get("passed") is True
        and outcome.get("resizeDragPersistence", {}).get("passed") is True
        and outcome.get("manualCompanionButton", {}).get("passed") is True
        and outcome.get("radialCompanionAction", {}).get("passed") is True
        and outcome.get("quickActionsCleared", {}).get("passed") is True
        and outcome.get("compactModeDuringCompanionBubble") is True
        and outcome.get("companionBubbleVisible") is True
        and outcome.get("companionBubbleDismissed") is True
        and int(outcome.get("companionBubbleLayout", {}).get("width", 0)) >= 320
        and int(outcome.get("companionBubbleLayout", {}).get("height", 0)) >= 184
        and outcome.get("focusTimerRunning", {}).get("visible") is True
        and outcome.get("focusTimerRunning", {}).get("state") == "running"
        and outcome.get("focusTimerRunning", {}).get("doesNotAcceptFocus") is True
        and outcome.get("focusTimerRunning", {}).get("transparentForInput") is True
        and outcome.get("focusTimerPaused") is True
        and outcome.get("focusTimerCompleted") is True
        and outcome.get("habitatVisualAdaptationWorked") is True
        and outcome.get("habitatDetachTransition", {}).get("passed") is True
        and all(outcome.get("suppressionBefore", {}).values())
        and all(outcome.get("suppressionDuring", {}).values())
        and all(outcome.get("suppressionAfter", {}).values())
        and outcome.get("desktopSceneLifecycle", {}).get("passed") is True
        and all(outcome.get("layoutPersistence", {}).values())
    )
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
