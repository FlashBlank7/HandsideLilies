from __future__ import annotations

"""End-to-end, non-capturing verification for default proactive companionship.

The verifier runs Qt on the offscreen platform, publishes a synthetic
foreground WinEvent and advances deterministic stability/idle clocks.  It
does not enumerate native windows, read the system cursor, capture pixels,
use the network, inspect an API key or download/start any model.
"""

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
for value in (SRC_ROOT, SCRIPTS_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QMetaObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.app import CompactHitTestFilter
from lilies.core.activity import ForegroundContext
from lilies.core.win_event import WinEvent, WinEventKind
from lilies.paths import qml_path
from verify_compact_ui import OffscreenBackend, load_windows_ui_fonts


class Clock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value


class Idle:
    def __init__(self, seconds: float = 0.0) -> None:
        self.seconds = float(seconds)

    def idle_seconds(self) -> float:
        return self.seconds


class SyntheticAvailableModel:
    model = "synthetic-subscription"
    ready = True

    def complete(self, *_args, **_kwargs):
        return json.dumps(
            {
                "anchor": "",
                "evidenceConfidence": "none",
                "summary": "窗口安静下来以后，问题的边缘反而更容易被看见。",
                "detail": "这是应用类别信号生成的合成验证文本；没有读取窗口正文。",
            },
            ensure_ascii=False,
        )

    def abort(self) -> None:
        pass

    def stop(self) -> None:
        pass


def descendants(window: QQuickWindow) -> tuple[QQuickItem, ...]:
    return tuple(CompactHitTestFilter._visual_descendants(window.contentItem()))


def find_pet_item(window: QQuickWindow, object_name: str) -> QQuickItem | None:
    return next(
        (item for item in descendants(window) if item.objectName() == object_name),
        None,
    )


def find_window_item(window: QQuickWindow, object_name: str) -> QQuickItem | None:
    return next(
        (
            item
            for item in CompactHitTestFilter._visual_descendants(
                window.contentItem()
            )
            if item.objectName() == object_name
        ),
        None,
    )


def wait_for(app: QApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def click_item(window: QQuickWindow, item: QQuickItem) -> None:
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        pos=QPoint(round(point.x()), round(point.y())),
    )


def main() -> int:
    temp_parent = PROJECT_ROOT / ".test-tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(
        prefix="companion-flow-ui-", dir=temp_parent
    )
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    backend = OffscreenBackend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")
    root = engine.rootObjects()[0]
    pet_window = root.findChild(QQuickWindow, "petWindow")
    compact_window = root.findChild(QQuickItem, "desktopPet")
    chat_window = root.findChild(QQuickWindow, "chatWindow")
    bubble_window = root.findChild(QQuickWindow, "companionBubbleWindow")
    companion_page = root.findChild(QQuickItem, "companionSettingsPage")
    companion_nav = root.findChild(QQuickItem, "chatPageCompanionButton")
    activity_switch = root.findChild(QQuickItem, "companionActivityEnabledSwitch")
    delivery_status_label = root.findChild(
        QQuickItem, "companionDeliveryStatusLabel"
    )
    reopen_unread_button = root.findChild(
        QQuickItem, "companionReopenUnreadButton"
    )
    delivery_privacy_hint = root.findChild(
        QQuickItem, "companionDeliveryPrivacyHint"
    )
    application_policy_group = root.findChild(
        QQuickItem, "companionApplicationPolicyGroup"
    )
    application_policy_empty = root.findChild(
        QQuickItem, "companionApplicationPolicyEmpty"
    )
    library_open = find_window_item(chat_window, "functionLibraryOpen_companion")
    library_status = find_window_item(chat_window, "functionLibraryStatus_companion")
    if any(
        value is None
        for value in (
            pet_window,
            compact_window,
            chat_window,
            bubble_window,
            companion_page,
            companion_nav,
            activity_switch,
            delivery_status_label,
            reopen_unread_button,
            delivery_privacy_hint,
            application_policy_group,
            application_policy_empty,
            library_open,
            library_status,
        )
    ):
        raise RuntimeError("companion UI entry contract failed to load")

    companion = backend.companion
    clock = Clock()
    idle = Idle()
    synthetic_hwnd = 7301
    synthetic_context = ForegroundContext(
        synthetic_hwnd,
        process_id=73,
        process_name="wps.exe",
        window_class="KingsoftWriter",
        title=r"Paper C:\Users\Alice\private.pdf",
        scene_label="论文阅读",
    )
    original_reader = companion.reader
    original_foreground = companion._foreground_provider
    original_luna = companion.runtime.luna
    original_active = companion._active
    companion.reader = lambda hwnd: (
        synthetic_context
        if int(hwnd) == synthetic_hwnd
        else ForegroundContext(int(hwnd or 0))
    )
    companion._foreground_provider = lambda: synthetic_hwnd
    companion.activity.clock = clock
    companion.activity.idle_provider = idle
    companion.runtime.luna = SyntheticAvailableModel()
    original_luna.stop()
    # Smoke backends deliberately keep background services inactive. Enable
    # only this controller after every native provider has been replaced with
    # a synthetic one, then stop its wall-clock timer so the verifier advances
    # the state machine explicitly.
    companion._active = True
    companion.setActivityEnabled(True)
    scheduler_started = companion._timer.isActive()
    scheduler_interval_ms = companion._timer.interval()
    companion._timer.stop()

    try:
        defaults = companion.activityStatus
        core_actions = [str(value["action"]) for value in backend.quickActions]
        chat_action = find_pet_item(pet_window, "desktopPetAction_chat")
        companion_catalog = next(
            (
                dict(value)
                for value in backend.functionCatalog
                if str(value.get("action", "")) == "companion"
            ),
            {},
        )

        # The permanent chat action exposes companionship even before the user
        # chooses the dedicated optional radial shortcut.
        backend.setChatOpen(True)
        wait_for(app, lambda: bool(chat_window.isVisible()))
        chat_entry = {
            "coreActions": core_actions,
            "chatOpened": bool(backend.chatOpen),
            "radialActionLoaded": chat_action is not None,
            "pageBeforeCompanion": int(chat_window.property("page")),
            "companionButtonVisible": bool(companion_nav.isVisible()),
            "catalogEntry": companion_catalog,
        }
        click_item(chat_window, companion_nav)
        app.processEvents()
        chat_entry.update(
            {
                "pageAfterCompanion": int(chat_window.property("page")),
                "settingsPageVisible": bool(companion_page.isVisible()),
                "activitySwitchVisible": bool(activity_switch.isVisible()),
                "activitySwitchChecked": bool(activity_switch.property("checked")),
                "noApiKeyCopy": "不需要 API Key"
                in str(companion.activityStatus["observationModeDetail"]),
            }
        )

        # A bubble's "mute this app" action must have a visible, title-free
        # undo path.  Exercise the shipped delegates and their real onClicked
        # handlers; only the synthetic process identity may reach the row.
        policy_empty_initial = bool(application_policy_empty.isVisible())
        companion.setPolicy("wps.exe", "blocked")
        policy_row_created = wait_for(
            app,
            lambda: find_window_item(
                chat_window, "companionApplicationPolicyRow_0"
            ) is not None,
        )
        policy_identity = find_window_item(
            chat_window, "companionApplicationPolicyIdentity_0"
        )
        policy_value = find_window_item(
            chat_window, "companionApplicationPolicyValue_0"
        )
        policy_allow = find_window_item(
            chat_window, "companionApplicationPolicyAllow_0"
        )
        policy_identity_text = (
            str(policy_identity.property("text") or "")
            if policy_identity is not None else ""
        )
        policy_value_text = (
            str(policy_value.property("text") or "")
            if policy_value is not None else ""
        )
        policy_allow_enabled = bool(
            policy_allow is not None and policy_allow.isEnabled()
        )
        policy_row_text = " | ".join(
            value for value in (policy_identity_text, policy_value_text) if value
        )
        allow_invoked = bool(
            policy_allow is not None
            and QMetaObject.invokeMethod(policy_allow, "click")
        )
        allow_applied = wait_for(
            app,
            lambda: bool(companion.applicationPolicies)
            and companion.applicationPolicies[0]["policy"] == "bubble",
        )
        policy_value_after_allow = find_window_item(
            chat_window, "companionApplicationPolicyValue_0"
        )
        policy_value_after_allow_text = (
            str(policy_value_after_allow.property("text") or "")
            if policy_value_after_allow is not None else ""
        )
        policy_reset_after_allow = find_window_item(
            chat_window, "companionApplicationPolicyReset_0"
        )
        reset_invoked = bool(
            policy_reset_after_allow is not None
            and QMetaObject.invokeMethod(policy_reset_after_allow, "click")
        )
        reset_applied = wait_for(
            app,
            lambda: companion.applicationPolicies == []
            and bool(application_policy_empty.isVisible()),
        )

        companion.setPolicy("bitwarden.exe", "bubble")
        sensitive_created = wait_for(
            app,
            lambda: bool(companion.applicationPolicies)
            and companion.applicationPolicies[0]["application"]
            == "bitwarden.exe",
        )
        sensitive_allow = find_window_item(
            chat_window, "companionApplicationPolicyAllow_0"
        )
        sensitive_value = find_window_item(
            chat_window, "companionApplicationPolicyValue_0"
        )
        policy_ui = {
            "emptyInitially": policy_empty_initial,
            "rowCreated": policy_row_created,
            "identity": policy_identity_text,
            "initialPolicy": policy_value_text,
            "rowText": policy_row_text,
            "allowButtonEnabled": policy_allow_enabled,
            "allowInvoked": allow_invoked,
            "allowApplied": allow_applied,
            "policyAfterAllow": policy_value_after_allow_text,
            "resetInvoked": reset_invoked,
            "resetApplied": reset_applied,
            "sensitiveCreated": sensitive_created,
            "sensitivePolicy": (
                str(sensitive_value.property("text") or "")
                if sensitive_value is not None else ""
            ),
            "sensitiveAllowEnabled": bool(
                sensitive_allow is not None and sensitive_allow.isEnabled()
            ),
            "leakedTitleOrContent": any(
                token in policy_row_text
                for token in ("Paper", "Alice", "private.pdf", "KingsoftWriter")
            ),
        }
        companion.setPolicy("bitwarden.exe", "default")
        # Keep the later emission timeline independent from this UI probe even
        # if a Qt minor version does not expose AbstractButton.click() through
        # QMetaObject and the verifier records that contract as failed.
        companion.setPolicy("wps.exe", "default")
        chat_window.setProperty("page", 4)
        wait_for(
            app,
            lambda: int(chat_window.property("page")) == 4
            and bool(library_open.isVisible()),
        )
        QTest.qWait(80)
        settings_library_entry = {
            "openVisible": bool(library_open.isVisible()),
            "openEnabled": bool(library_open.isEnabled()),
            "openPosition": [
                round(library_open.mapToScene(QPointF(0, 0)).x()),
                round(library_open.mapToScene(QPointF(0, 0)).y()),
                round(library_open.width()),
                round(library_open.height()),
            ],
            "windowSize": [chat_window.width(), chat_window.height()],
            "statusVisible": bool(library_status.isVisible()),
            "statusText": str(library_status.property("text") or ""),
        }
        open_left, open_top, open_width, open_height = settings_library_entry[
            "openPosition"
        ]
        settings_library_entry["openInsideWindow"] = bool(
            open_left >= 0
            and open_top >= 0
            and open_left + open_width <= chat_window.width()
            and open_top + open_height <= chat_window.height()
        )
        click_item(chat_window, library_open)
        wait_for(app, lambda: int(chat_window.property("page")) == 3)
        settings_library_entry["pageAfterOpen"] = int(
            chat_window.property("page")
        )

        # The dedicated optional action must route to exactly the same page.
        backend.setChatOpen(False)
        app.processEvents()
        backend.setQuickActionPinned("companion", True)
        optional_loaded = wait_for(
            app,
            lambda: find_pet_item(
                pet_window, "desktopPetAction_companion"
            ) is not None,
        )
        optional_entry = {
            "pinned": "companion"
            in [str(value["action"]) for value in backend.quickActions],
            "radialActionLoaded": optional_loaded,
            "catalogSelected": bool(
                next(
                    value
                    for value in backend.functionCatalog
                    if str(value.get("action", "")) == "companion"
                ).get("selected")
            ),
        }
        backend.setChatOpen(False)
        app.processEvents()

        # Publish through the production in-memory hub subscription. No native
        # foreground APIs are touched by this verifier.
        queued = backend.win_event_hub.publish(
            WinEvent(WinEventKind.FOREGROUND, synthetic_hwnd, 0)
        )
        dispatched = backend.win_event_hub.dispatch_pending()
        event_context = companion.activity.current_context
        after_event = companion.activityStatus

        # Exercise the real defaults: two-minute stable window, then a 6–60s
        # natural pause. These calls are deterministic and do not shorten the
        # product thresholds for the test.
        idle.seconds = 10.0
        companion._consider()
        initial_gate = companion.activityStatus
        clock.value += 119.0
        companion._consider()
        before_stable = companion.activityStatus
        clock.value += 1.0
        idle.seconds = 5.0
        companion._consider()
        before_pause = companion.activityStatus
        idle.seconds = 10.0
        companion._timer.start()
        scheduler_active_before_emission = companion._timer.isActive()
        emitted = wait_for(app, lambda: bool(companion.bubble.get("visible")))
        presentation_acknowledged = wait_for(
            app,
            lambda: (
                companion.deliveryStatus.get("state") == "presented"
                and bool(companion.bubble.get("expiresAt"))
            ),
        )
        companion._timer.stop()
        bubble = companion.bubble
        emitted_status = companion.activityStatus
        bubble_visible = bool(bubble_window.isVisible())
        bubble_flags = bubble_window.flags()
        first_presentation_revision = int(
            bubble_window.property("presentationRevision") or 0
        )
        replacement = dict(bubble)
        replacement["id"] = str(bubble["id"]) + "-replacement"
        replacement["summary"] = "同一窗口里的下一张短笺。"
        companion._bubble = replacement
        companion.bubbleChanged.emit()
        app.processEvents()
        replacement_presentation_revision = int(
            bubble_window.property("presentationRevision") or 0
        )
        bubble_duration_seconds = round(
            (
                datetime.fromisoformat(str(bubble["expiresAt"]))
                - datetime.fromisoformat(
                    str(companion._delivery_record["presentedAt"])
                )
            ).total_seconds()
        )

        companion.dismiss()
        clock.value += 1.0
        companion._consider()
        cooldown = companion.activityStatus
        delivery_ui_updated = wait_for(
            app,
            lambda: (
                "1 条未读" in str(delivery_status_label.property("text") or "")
                and bool(reopen_unread_button.isEnabled())
            ),
        )

        # Reveal the radial delegate just enough to evaluate the permanent
        # compact status line; menu animation/clicks have their own suite.
        compact_window.setProperty("expanded", True)
        wait_for(
            app,
            lambda: any(
                item.objectName() == "desktopPetAwarenessLabel_chat"
                and bool(str(item.property("text") or ""))
                for item in descendants(pet_window)
            ),
        )
        awareness = next(
            (
                item
                for item in descendants(pet_window)
                if item.objectName() == "desktopPetAwarenessLabel_chat"
                and bool(str(item.property("text") or ""))
            ),
            None,
        )
        outcome = {
            "passed": False,
            "defaults": {
                "activityConfigured": defaults["configuredEnabled"],
                "smartObservationAuthorized": defaults["smartObservationEnabled"],
                "onlineContentAuthorized": defaults["onlineContentEnabled"],
                "frequency": companion.preferences["frequency"],
                "stableSeconds": after_event["requiredStableSeconds"],
                "pauseRange": [
                    after_event["naturalPauseMinimumSeconds"],
                    after_event["naturalPauseMaximumSeconds"],
                ],
                "captureStagingCreated": (
                    Path(temporary.name) / "capture-staging"
                ).exists(),
            },
            "entries": {
                "chat": chat_entry,
                "settingsLibrary": settings_library_entry,
                "optional": optional_entry,
            },
            "applicationPoliciesUi": policy_ui,
            "event": {
                "queued": queued,
                "dispatched": dispatched,
                "contextProcess": event_context.process_name if event_context else "",
                "contextHandle": event_context.hwnd if event_context else 0,
                "stateAfterEvent": after_event["state"],
            },
            "timeline": {
                "initial": initial_gate["state"],
                "at119Seconds": before_stable["state"],
                "activeAt120Seconds": before_pause["state"],
                "emitted": emitted,
                "bubbleWindowVisible": bubble_visible,
                "bubbleModel": bubble.get("model", ""),
                "contextType": bubble.get("contextType", ""),
                "bubbleDurationSeconds": bubble_duration_seconds,
                "schedulerStartedAtEnable": scheduler_started,
                "schedulerIntervalMs": scheduler_interval_ms,
                "schedulerActiveBeforeEmission": scheduler_active_before_emission,
                "staysOnTop": bool(
                    bubble_flags & Qt.WindowType.WindowStaysOnTopHint
                ),
                "doesNotAcceptFocus": bool(
                    bubble_flags & Qt.WindowType.WindowDoesNotAcceptFocus
                ),
                "firstPresentationRevision": first_presentation_revision,
                "replacementPresentationRevision": replacement_presentation_revision,
                "afterEmission": emitted_status["state"],
                "afterDismissAndTick": cooldown["state"],
                "cooldownLabel": cooldown["compactStatusLabel"],
            },
            "petAwareness": {
                "exists": awareness is not None,
                "text": str(awareness.property("text") or "") if awareness else "",
            },
            "deliveryUi": {
                "updated": delivery_ui_updated,
                "status": str(delivery_status_label.property("text") or ""),
                "reopenText": str(reopen_unread_button.property("text") or ""),
                "reopenEnabled": bool(reopen_unread_button.isEnabled()),
                "privacyHint": str(delivery_privacy_hint.property("text") or ""),
            },
        }
        outcome["passed"] = bool(
            outcome["defaults"]["activityConfigured"]
            and outcome["defaults"]["smartObservationAuthorized"] is False
            and outcome["defaults"]["onlineContentAuthorized"] is False
            and outcome["defaults"]["frequency"] == "balanced"
            and outcome["defaults"]["stableSeconds"] == 120.0
            and outcome["defaults"]["pauseRange"] == [6.0, 60.0]
            and outcome["defaults"]["captureStagingCreated"] is False
            and chat_entry["coreActions"] == ["chat", "world", "settings"]
            and chat_entry["radialActionLoaded"]
            and chat_entry["chatOpened"]
            and chat_entry["pageBeforeCompanion"] == 0
            and chat_entry["companionButtonVisible"]
            and chat_entry["pageAfterCompanion"] == 3
            and chat_entry["settingsPageVisible"]
            and chat_entry["activitySwitchVisible"]
            and chat_entry["activitySwitchChecked"]
            and chat_entry["noApiKeyCopy"]
            and policy_ui["emptyInitially"]
            and policy_ui["rowCreated"]
            and policy_ui["identity"] == "wps.exe"
            and policy_ui["initialPolicy"] == "静默"
            and policy_ui["allowButtonEnabled"]
            and policy_ui["allowInvoked"]
            and policy_ui["allowApplied"]
            and policy_ui["policyAfterAllow"] == "允许气泡"
            and policy_ui["resetInvoked"]
            and policy_ui["resetApplied"]
            and policy_ui["sensitiveCreated"]
            and policy_ui["sensitivePolicy"] == "静默"
            and not policy_ui["sensitiveAllowEnabled"]
            and not policy_ui["leakedTitleOrContent"]
            and settings_library_entry["openVisible"]
            and settings_library_entry["openEnabled"]
            and settings_library_entry["openInsideWindow"]
            and settings_library_entry["statusVisible"]
            and settings_library_entry["statusText"].startswith("陪伴 · ")
            and settings_library_entry["pageAfterOpen"] == 3
            and companion_catalog.get("label") == "生活陪伴与屏幕观察"
            and optional_entry
            == {
                "pinned": True,
                "radialActionLoaded": True,
                "catalogSelected": True,
            }
            and queued
            and dispatched == 1
            and outcome["event"]["contextProcess"] == "wps.exe"
            and outcome["event"]["contextHandle"] == synthetic_hwnd
            and outcome["event"]["stateAfterEvent"] == "stabilizing"
            and initial_gate["state"] == "window-not-stable"
            and before_stable["state"] == "window-not-stable"
            and before_pause["state"] == "user-active"
            and emitted
            and bubble_visible
            and presentation_acknowledged
            and bubble.get("model") == "synthetic-subscription"
            and bubble.get("contextType") == "application-signal"
            and bubble_duration_seconds == 240
            and scheduler_started
            and scheduler_interval_ms == 1500
            and scheduler_active_before_emission
            and bool(bubble_flags & Qt.WindowType.WindowStaysOnTopHint)
            and bool(bubble_flags & Qt.WindowType.WindowDoesNotAcceptFocus)
            and first_presentation_revision >= 1
            and replacement_presentation_revision > first_presentation_revision
            and emitted_status["generationMode"] == "subscription"
            and cooldown["state"] == "cooldown"
            and cooldown["compactStatusLabel"] == "陪伴 · 安静间隔"
            and awareness is not None
            and str(awareness.property("text") or "") == "陪伴 · 1 条未读"
            and outcome["deliveryUi"]["updated"]
            and outcome["deliveryUi"]["status"].startswith("投递 · 1 条未读")
            and outcome["deliveryUi"]["reopenText"] == "重新显示未读"
            and outcome["deliveryUi"]["reopenEnabled"]
            and "不显示气泡正文或窗口标题" in outcome["deliveryUi"]["privacyHint"]
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        return 0 if outcome["passed"] else 1
    finally:
        companion.reader = original_reader
        companion._foreground_provider = original_foreground
        companion.runtime.luna = original_luna
        companion._active = original_active
        backend.shutdown()
        engine.deleteLater()
        app.processEvents()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
