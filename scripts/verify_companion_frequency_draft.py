from __future__ import annotations

"""Offscreen regression for the custom companion frequency editor."""

import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for value in (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts"):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")

from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.companion_controller import CompanionController
from lilies.core.database import Database
from lilies.paths import qml_path
from verify_compact_ui import OffscreenBackend


_DIGIT_KEYS = {
    "0": Qt.Key.Key_0,
    "1": Qt.Key.Key_1,
    "2": Qt.Key.Key_2,
    "3": Qt.Key.Key_3,
    "4": Qt.Key.Key_4,
    "5": Qt.Key.Key_5,
    "6": Qt.Key.Key_6,
    "7": Qt.Key.Key_7,
    "8": Qt.Key.Key_8,
    "9": Qt.Key.Key_9,
}


def _type_spinbox_text(
    window: QQuickWindow,
    spinbox: QQuickItem,
    text: str,
    *,
    after_key: Callable[[str, QQuickItem], None] | None = None,
) -> QQuickItem:
    editor = spinbox.property("contentItem")
    if not isinstance(editor, QQuickItem):
        raise RuntimeError("editable SpinBox has no text editor")
    editor.forceActiveFocus()
    QApplication.processEvents()
    if not bool(editor.property("activeFocus")):
        raise RuntimeError("could not focus editable SpinBox")
    QTest.keyClick(
        window,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier,
    )
    for character in str(text):
        QTest.keyClick(window, _DIGIT_KEYS[character])
        QApplication.processEvents()
        if after_key is not None:
            after_key(character, editor)
    QApplication.processEvents()
    return editor


def _type_text_field(
    window: QQuickWindow, field: QQuickItem, text: str
) -> None:
    field.forceActiveFocus()
    QApplication.processEvents()
    if not bool(field.property("activeFocus")):
        raise RuntimeError("could not focus text field")
    QTest.keyClick(
        window,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.ControlModifier,
    )
    for character in str(text):
        key = (
            Qt.Key.Key_Comma
            if character == ","
            else getattr(Qt.Key, f"Key_{character.upper()}")
        )
        QTest.keyClick(window, key)
        QApplication.processEvents()


def main() -> int:
    temp_parent = PROJECT_ROOT / ".test-tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(
        prefix="companion-frequency-draft-", dir=temp_parent
    )
    os.environ["LILIES_DATA_DIR"] = temporary.name
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
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
    chat_window = root.findChild(QQuickWindow, "chatWindow")
    scroll = root.findChild(QQuickItem, "companionSettingsPage")
    frequency = root.findChild(QQuickItem, "companionFrequencyDraft")
    interest_weight = root.findChild(QQuickItem, "companionInterestWeight")
    scene_weight = root.findChild(QQuickItem, "companionSceneWeight")
    minutes = root.findChild(QQuickItem, "customCompanionMinutesDraft")
    daily = root.findChild(QQuickItem, "customCompanionDailyDraft")
    apply_button = root.findChild(QQuickItem, "applyCustomCompanionFrequency")
    restore_button = root.findChild(QQuickItem, "restoreSavedCompanionFrequency")
    force_apply_button = root.findChild(
        QQuickItem, "forceApplyCustomCompanionFrequency"
    )
    conflict_notice = root.findChild(QQuickItem, "companionFrequencyConflictNotice")
    interests = root.findChild(QQuickItem, "companionInterestsDraft")
    screen_button = root.findChild(QQuickItem, "companionRequestScreenNowButton")
    screen_timer = root.findChild(QObject, "companionScreenObservationDelay")
    if any(
        item is None
        for item in (
            scroll,
            chat_window,
            frequency,
            interest_weight,
            scene_weight,
            minutes,
            daily,
            apply_button,
            restore_button,
            force_apply_button,
            conflict_notice,
            interests,
            screen_button,
            screen_timer,
        )
    ):
        raise RuntimeError("custom frequency controls are missing")

    try:
        chat_window.setProperty("page", 3)
        backend.setChatOpen(True)
        chat_window.show()
        chat_window.requestActivate()
        app.processEvents()
        assert backend.companion.preferences["frequency"] == "balanced"

        # Zero is an intentional way to disable either ranking signal, not a
        # missing value that should be replaced by the visual defaults.
        backend.companion.setMix(0, 0, 30)
        app.processEvents()
        zero_mix = {
            "interestSlider": int(round(float(interest_weight.property("value")))),
            "sceneSlider": int(round(float(scene_weight.property("value")))),
            "interestPreference": backend.companion.preferences["interestWeight"],
            "scenePreference": backend.companion.preferences["sceneWeight"],
        }

        # A change made outside this settings page (the speech-bubble menu's
        # “减少频率” action follows this exact path) must refresh an untouched
        # draft even though the settings ScrollView was created at startup.
        backend.companion.setFrequency("quiet", 45, 6)
        app.processEvents()
        after_external_change = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "controlMode": str(frequency.property("currentValue")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "backendMode": backend.companion.preferences["frequency"],
        }

        # Follow the real ComboBox activation path, then type an out-of-range
        # value into the editable line editor.  Before Return the SpinBox value
        # is intentionally still the saved draft; raw text and focus must be
        # protected from both an external bubble action and heartbeat signals.
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        minutes_editor = _type_spinbox_text(chat_window, minutes, "222")
        typed_before_commit = {
            "text": str(minutes_editor.property("text")),
            "value": int(minutes.property("value")),
            "draft": int(scroll.property("customMinutesDraft")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
        }
        backend.companion.setFrequency("lively", 10, 30)
        # The committed preference update above is allowed to defer a sync
        # while the user is editing. Clear that marker so the following
        # broad state heartbeats prove they no longer reach this QML handler.
        scroll.setProperty("frequencyDraftSyncPending", False)
        for _ in range(6):
            # This is the broad state signal emitted by the 1.5 second
            # activity heartbeat. It must not be the preferences notifier.
            backend.companion.changed.emit()
            app.processEvents()
        during_edit = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "text": str(minutes_editor.property("text")),
            "minutes": int(scroll.property("customMinutesDraft")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
            "heartbeatSyncPending": bool(
                scroll.property("frequencyDraftSyncPending")
            ),
            "backendMode": backend.companion.preferences["frequency"],
        }
        QTest.keyClick(chat_window, Qt.Key.Key_Return)
        app.processEvents()

        # Return clamps minutes to the upper bound.  Moving focus away from a
        # typed daily value clamps it to the lower bound as well.
        daily_editor = _type_spinbox_text(chat_window, daily, "0")
        minutes_editor.forceActiveFocus()
        app.processEvents()
        after_boundary_commit = {
            "minutesText": str(minutes_editor.property("text")),
            "minutes": int(minutes.property("value")),
            "minutesDraft": int(scroll.property("customMinutesDraft")),
            "dailyText": str(daily_editor.property("text")),
            "daily": int(daily.property("value")),
            "dailyDraft": int(scroll.property("customDailyDraft")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
        }

        # Cancel/restore reads the latest committed backend value, rather than
        # a stale baseline captured when the process first opened.
        restore_button.forceActiveFocus()
        app.processEvents()
        if not QMetaObject.invokeMethod(
            restore_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not invoke custom frequency restore")
        app.processEvents()
        after_restore = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "controlMode": str(frequency.property("currentValue")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "backendMode": backend.companion.preferences["frequency"],
        }

        # Valid typed values also use the real editors.  They remain drafts
        # until the explicit Apply button is invoked.
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        incremental_typing = []

        def record_incremental_typing(_character, editor):
            # Heartbeats between individual keystrokes must not cause the
            # SpinBox's lower bound to replace an incomplete value such as the
            # leading "3" in "37".
            backend.companion.changed.emit()
            app.processEvents()
            incremental_typing.append(
                {
                    "text": str(editor.property("text")),
                    "value": int(minutes.property("value")),
                    "draft": int(scroll.property("customMinutesDraft")),
                    "editing": bool(scroll.property("frequencyDraftEditing")),
                }
            )

        _type_spinbox_text(
            chat_window,
            minutes,
            "37",
            after_key=record_incremental_typing,
        )
        QTest.keyClick(chat_window, Qt.Key.Key_Return)
        daily_editor = _type_spinbox_text(chat_window, daily, "9")
        app.processEvents()
        before_direct_apply = {
            "text": str(daily_editor.property("text")),
            "value": int(daily.property("value")),
            "draft": int(scroll.property("customDailyDraft")),
            "activeFocus": bool(daily_editor.property("activeFocus")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
        }
        if not QMetaObject.invokeMethod(
            apply_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not invoke custom frequency apply")
        app.processEvents()
        after_apply = dict(backend.companion.preferences)
        after_apply_draft = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
        }

        # Construct a fresh controller over the same database to exercise the
        # exact restart-loading path, not merely the live QML property map.
        restored_controller = CompanionController(
            Database(Path(temporary.name) / "lilies.db"),
            Path(temporary.name),
            active=False,
            status_sink=lambda _message: None,
            move_to_box=lambda _payload: None,
            foreground_provider=lambda: 0,
        )
        try:
            after_restart = {
                "frequency": restored_controller.preferences["frequency"],
                "minimumMinutes": restored_controller.preferences["minimumMinutes"],
                "dailyLimit": restored_controller.preferences["dailyLimit"],
                "customMinimumMinutes": restored_controller.preferences[
                    "customMinimumMinutes"
                ],
                "customDailyLimit": restored_controller.preferences[
                    "customDailyLimit"
                ],
            }
        finally:
            restored_controller.shutdown()

        # Qt retains a hidden TextInput's activeFocus on some platforms.  The
        # draft lock is therefore tied to the settings page's visibility: an
        # untouched editor may defer an external change while visible, but
        # closing the page must reconcile that pending committed value.
        minutes_editor.forceActiveFocus()
        app.processEvents()
        backend.companion.setFrequency("quiet", 45, 6)
        app.processEvents()
        before_settings_close = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
        }
        backend.setChatOpen(False)
        app.processEvents()
        after_settings_close = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
        }

        # A deliberately uncommitted text draft belongs to the user, not to
        # the periodic runtime state. Closing and reopening settings must keep
        # that text until Apply or Restore is explicitly chosen.
        backend.setChatOpen(True)
        chat_window.show()
        app.processEvents()
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        dirty_editor = _type_spinbox_text(chat_window, minutes, "44")
        backend.companion.changed.emit()
        app.processEvents()
        backend.setChatOpen(False)
        app.processEvents()
        dirty_after_close = {
            "text": str(dirty_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
            "mode": str(scroll.property("frequencyDraft")),
        }
        backend.setChatOpen(True)
        chat_window.show()
        app.processEvents()
        dirty_after_reopen = {
            "text": str(dirty_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "mode": str(scroll.property("frequencyDraft")),
        }
        if not QMetaObject.invokeMethod(
            restore_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not restore reopened frequency draft")
        app.processEvents()
        restored_after_dirty_close = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "controlMode": str(frequency.property("currentValue")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "backendMode": backend.companion.preferences["frequency"],
        }

        # Direct Apply owns parsing and boundary enforcement even while the
        # daily editor still has focus. Leading zeroes are accepted as normal
        # decimal input; a too-small interval becomes the documented floor.
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        _type_spinbox_text(chat_window, minutes, "2")
        daily_editor = _type_spinbox_text(chat_window, daily, "0004")
        if not QMetaObject.invokeMethod(
            apply_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not apply boundary frequency draft")
        app.processEvents()
        after_direct_boundary_apply = {
            "frequency": backend.companion.preferences["frequency"],
            "minutes": backend.companion.preferences["minimumMinutes"],
            "daily": backend.companion.preferences["dailyLimit"],
            "minutesText": str(minutes.property("contentItem").property("text")),
            "dailyText": str(daily_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
        }

        # A preset is an immediate durable action. If its database write
        # fails, the ComboBox must return to the committed value instead of
        # displaying a selection that never reached disk.
        database = backend.companion.database
        original_set_setting = database.set_setting

        def fail_preset_write(key, value):
            if key == "companion_preferences":
                raise OSError("synthetic preset frequency write failure")
            return original_set_setting(key, value)

        database.set_setting = fail_preset_write
        try:
            frequency.setProperty("currentIndex", 1)
            frequency.activated.emit(1)
            app.processEvents()
        finally:
            database.set_setting = original_set_setting
        after_failed_preset = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "controlMode": str(frequency.property("currentValue")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "error": str(scroll.property("frequencyDraftError")),
            "backendMode": backend.companion.preferences["frequency"],
            "backendMinutes": backend.companion.preferences["minimumMinutes"],
            "backendDaily": backend.companion.preferences["dailyLimit"],
        }

        # A failed custom write follows the opposite recovery policy: keep
        # the user's parsed draft visible and dirty so Apply can be retried;
        # Restore remains an explicit way back to the durable values.
        original_set_settings = database.set_settings

        def fail_custom_write(_values):
            raise OSError("synthetic custom frequency write failure")

        database.set_settings = fail_custom_write
        try:
            _type_spinbox_text(chat_window, minutes, "33")
            QTest.keyClick(chat_window, Qt.Key.Key_Return)
            failed_daily_editor = _type_spinbox_text(chat_window, daily, "8")
            if not QMetaObject.invokeMethod(
                apply_button, "clicked", Qt.ConnectionType.DirectConnection
            ):
                raise RuntimeError("could not invoke failing custom frequency apply")
            app.processEvents()
        finally:
            database.set_settings = original_set_settings
        after_failed_custom = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "minutes": int(minutes.property("value")),
            "dailyText": str(failed_daily_editor.property("text")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "error": str(scroll.property("frequencyDraftError")),
            "backendMode": backend.companion.preferences["frequency"],
            "backendMinutes": backend.companion.preferences["minimumMinutes"],
            "backendDaily": backend.companion.preferences["dailyLimit"],
        }
        if not QMetaObject.invokeMethod(
            restore_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not restore after failed custom frequency apply")
        app.processEvents()
        after_failed_custom_restore = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "error": str(scroll.property("frequencyDraftError")),
        }

        # A preferencesChanged notification is broader than frequency. Keep a
        # dirty frequency draft conflict-free when only the mix changes.
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        _type_spinbox_text(chat_window, minutes, "41")
        QTest.keyClick(chat_window, Qt.Key.Key_Return)
        conflict_daily_editor = _type_spinbox_text(chat_window, daily, "7")
        backend.companion.setMix(61, 39, 30)
        app.processEvents()
        after_non_frequency_preference = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "minutes": int(minutes.property("value")),
            "dailyText": str(conflict_daily_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
            "interestWeight": backend.companion.preferences["interestWeight"],
        }

        # An actual external frequency commit must preserve the draft, expose
        # recovery/override choices, and make ordinary Apply a no-op.
        backend.companion.setFrequency("quiet", 45, 6)
        app.processEvents()
        after_external_conflict = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "minutes": int(minutes.property("value")),
            "dailyText": str(conflict_daily_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
            "noticeVisible": bool(conflict_notice.property("visible")),
            "restoreText": str(restore_button.property("text")),
            "forceVisible": bool(force_apply_button.property("visible")),
            "forceEnabled": bool(force_apply_button.property("enabled")),
        }
        if not QMetaObject.invokeMethod(
            apply_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not invoke conflict-blocked frequency apply")
        app.processEvents()
        after_blocked_apply = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "minutes": int(minutes.property("value")),
            "dailyText": str(conflict_daily_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "backendMode": backend.companion.preferences["frequency"],
            "backendMinutes": backend.companion.preferences["minimumMinutes"],
            "backendDaily": backend.companion.preferences["dailyLimit"],
            "error": str(scroll.property("frequencyDraftError")),
        }
        if not QMetaObject.invokeMethod(
            restore_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not restore latest conflicting frequency")
        app.processEvents()
        after_conflict_restore = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
            "restoreText": str(restore_button.property("text")),
        }

        # A second conflict is resolved only through the independent explicit
        # override action. Success establishes the draft as the new baseline.
        frequency.setProperty("currentIndex", 4)
        frequency.activated.emit(4)
        app.processEvents()
        _type_spinbox_text(chat_window, minutes, "42")
        QTest.keyClick(chat_window, Qt.Key.Key_Return)
        _type_spinbox_text(chat_window, daily, "8")
        backend.companion.setFrequency("lively", 10, 30)
        app.processEvents()
        if not QMetaObject.invokeMethod(
            force_apply_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not explicitly override frequency conflict")
        app.processEvents()
        after_forced_apply = {
            "draftMode": str(scroll.property("frequencyDraft")),
            "baselineMode": str(scroll.property("frequencyBaseline")),
            "minutes": int(minutes.property("value")),
            "daily": int(daily.property("value")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "backendMode": backend.companion.preferences["frequency"],
            "backendMinutes": backend.companion.preferences["minimumMinutes"],
            "backendDaily": backend.companion.preferences["dailyLimit"],
            "error": str(scroll.property("frequencyDraftError")),
        }

        # Reverse the conflict ordering covered above: an external commit can
        # land after focus but before the user's first keystroke.  The first
        # edit must promote the pending generation to a conflict, and ordinary
        # Apply must leave the newer backend value untouched.
        late_editor = minutes.property("contentItem")
        if not isinstance(late_editor, QQuickItem):
            raise RuntimeError("custom minutes editor is missing")
        late_editor.forceActiveFocus()
        app.processEvents()
        backend.companion.setFrequency("quiet", 45, 6)
        app.processEvents()
        after_external_before_typing = {
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "editing": bool(scroll.property("frequencyDraftEditing")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "backendMode": backend.companion.preferences["frequency"],
        }
        _type_spinbox_text(chat_window, minutes, "43")
        app.processEvents()
        after_late_typing = {
            "text": str(late_editor.property("text")),
            "dirty": bool(scroll.property("frequencyDraftDirty")),
            "pending": bool(scroll.property("frequencyDraftSyncPending")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "backendMode": backend.companion.preferences["frequency"],
        }
        if not QMetaObject.invokeMethod(
            apply_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not invoke reverse-order conflict apply")
        app.processEvents()
        after_late_typing_blocked_apply = {
            "draftText": str(late_editor.property("text")),
            "conflict": bool(scroll.property("frequencyDraftConflict")),
            "backendMode": backend.companion.preferences["frequency"],
            "backendMinutes": backend.companion.preferences["minimumMinutes"],
            "backendDaily": backend.companion.preferences["dailyLimit"],
            "noticeVisible": bool(conflict_notice.property("visible")),
            "forceVisible": bool(force_apply_button.property("visible")),
            "sameError": str(scroll.property("frequencyDraftError"))
            == after_blocked_apply["error"],
        }
        if not QMetaObject.invokeMethod(
            restore_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not restore reverse-order conflict")
        app.processEvents()

        # Aggregate preferencesChanged is also emitted for unrelated sliders.
        # A real TextField edit remains the user's draft until it is committed.
        _type_text_field(chat_window, interests, "draft,topic")
        typed_interest = str(interests.property("text"))
        backend.companion.changed.emit()
        app.processEvents()
        interest_after_heartbeat = str(interests.property("text"))
        backend.companion.setMix(62, 38, 31)
        app.processEvents()
        interest_after_unrelated_preference = str(interests.property("text"))
        interest_dirty_after_unrelated_preference = bool(
            interests.property("draftDirty")
        )
        mix_after_unrelated_preference = [
            backend.companion.preferences["interestWeight"],
            backend.companion.preferences["sceneWeight"],
            backend.companion.preferences["momentumHalfLifeMinutes"],
        ]
        QTest.keyClick(chat_window, Qt.Key.Key_Return)
        app.processEvents()
        committed_interests = list(backend.companion.preferences["interests"])
        canonical_interest_text = str(interests.property("text")) == "draft，topic"
        late_editor.forceActiveFocus()
        app.processEvents()
        backend.companion.setInterests("external,clean")
        app.processEvents()
        interest_draft_protection = {
            "typed": typed_interest,
            "afterHeartbeat": interest_after_heartbeat,
            "afterUnrelatedPreference": interest_after_unrelated_preference,
            "dirtyAfterUnrelatedPreference": interest_dirty_after_unrelated_preference,
            "mixAfterUnrelatedPreference": mix_after_unrelated_preference,
            "savedInterests": committed_interests,
            "dirtyAfterCommit": bool(interests.property("draftDirty")),
            "canonicalAfterCommit": canonical_interest_text,
            "cleanExternalRefill": str(interests.property("text"))
            == "external，clean",
            "savedAfterExternalRefill": list(
                backend.companion.preferences["interests"]
            ),
        }

        # Exercise the real QML click handler.  Reopening settings inside the
        # foreground-restoration grace period cancels the stale one-shot, so it
        # cannot dispatch after the UI becomes visible again.
        original_smart_observation = backend.companion._smart_observation
        original_modality_status = dict(backend.companion.runtime.modality_status)
        original_reconcile = backend.companion._reconcile_foreground_for_bubble
        reconcile_calls: list[bool] = []
        backend.companion._smart_observation = True
        backend.companion.runtime.modality_status = {
            "checked": True,
            "imageModel": "offscreen-test-image",
            "error": "",
        }
        backend.companion._reconcile_foreground_for_bubble = lambda: (
            reconcile_calls.append(True) or None,
            False,
            "no-foreground-window",
        )
        backend.companion.changed.emit()
        chat_window.setProperty("page", 3)
        backend.setChatOpen(True)
        chat_window.show()
        app.processEvents()
        feedback_before_screen_click = str(
            backend.companion.activityStatus.get("requestFeedback", "")
        )
        screen_button_enabled = bool(screen_button.property("enabled"))
        if not QMetaObject.invokeMethod(
            screen_button, "clicked", Qt.ConnectionType.DirectConnection
        ):
            raise RuntimeError("could not invoke one-shot screen observation")
        app.processEvents()
        screen_after_click = {
            "chatOpen": bool(backend.chatOpen),
            "visible": bool(chat_window.isVisible()),
            "timerRunning": bool(screen_timer.property("running")),
        }
        QTest.qWait(100)
        backend.setChatOpen(True)
        chat_window.show()
        app.processEvents()
        screen_after_reopen = {
            "chatOpen": bool(backend.chatOpen),
            "visible": bool(chat_window.isVisible()),
            "timerRunning": bool(screen_timer.property("running")),
        }
        QTest.qWait(320)
        app.processEvents()
        screen_reopen_cancellation = {
            "buttonEnabled": screen_button_enabled,
            "afterClick": screen_after_click,
            "afterReopen": screen_after_reopen,
            "requestCallsAfterDeadline": len(reconcile_calls),
            "feedbackUnchanged": str(
                backend.companion.activityStatus.get("requestFeedback", "")
            )
            == feedback_before_screen_click,
        }
        backend.companion._reconcile_foreground_for_bubble = original_reconcile
        backend.companion.runtime.modality_status = original_modality_status
        backend.companion._smart_observation = original_smart_observation
        backend.companion.changed.emit()
        app.processEvents()

        report = {
            "passed": zero_mix
            == {
                "interestSlider": 0,
                "sceneSlider": 0,
                "interestPreference": 0,
                "scenePreference": 0,
            }
            and after_external_change
            == {
                "draftMode": "quiet",
                "baselineMode": "quiet",
                "controlMode": "quiet",
                "dirty": False,
                "backendMode": "quiet",
            }
            and typed_before_commit
            == {
                "text": "222",
                "value": 25,
                "draft": 25,
                "dirty": True,
                "editing": True,
            }
            and during_edit
            == {
                "draftMode": "custom",
                "text": "222",
                "minutes": 25,
                "dirty": True,
                "editing": True,
                "heartbeatSyncPending": False,
                "backendMode": "lively",
            }
            and after_boundary_commit
            == {
                "minutesText": "180",
                "minutes": 180,
                "minutesDraft": 180,
                "dailyText": "1",
                "daily": 1,
                "dailyDraft": 1,
                "dirty": True,
            }
            and after_restore
            == {
                "draftMode": "lively",
                "baselineMode": "lively",
                "controlMode": "lively",
                "minutes": 25,
                "daily": 12,
                "dirty": False,
                "backendMode": "lively",
            }
            and incremental_typing
            == [
                {"text": "3", "value": 25, "draft": 25, "editing": True},
                {"text": "37", "value": 25, "draft": 25, "editing": True},
            ]
            and before_direct_apply
            == {
                "text": "9",
                "value": 12,
                "draft": 12,
                "activeFocus": True,
                "editing": True,
            }
            and after_apply["frequency"] == "custom"
            and after_apply["minimumMinutes"] == 37
            and after_apply["dailyLimit"] == 9
            and after_apply_draft
            == {
                "draftMode": "custom",
                "baselineMode": "custom",
                "minutes": 37,
                "daily": 9,
                "dirty": False,
            }
            and after_restart
            == {
                "frequency": "custom",
                "minimumMinutes": 37,
                "dailyLimit": 9,
                "customMinimumMinutes": 37,
                "customDailyLimit": 9,
            }
            and before_settings_close
            == {
                "draftMode": "custom",
                "dirty": False,
                "editing": True,
                "pending": True,
                "backendMode": "quiet",
            }
            and after_settings_close
            == {
                "draftMode": "quiet",
                "baselineMode": "quiet",
                "dirty": False,
                "editing": False,
                "pending": False,
                "backendMode": "quiet",
            }
            and dirty_after_close
            == {
                "text": "44",
                "dirty": True,
                "editing": False,
                "mode": "custom",
            }
            and dirty_after_reopen
            == {"text": "44", "dirty": True, "mode": "custom"}
            and restored_after_dirty_close
            == {
                "draftMode": "quiet",
                "controlMode": "quiet",
                "minutes": 37,
                "daily": 9,
                "dirty": False,
                "backendMode": "quiet",
            }
            and after_direct_boundary_apply
            == {
                "frequency": "custom",
                "minutes": 5,
                "daily": 4,
                "minutesText": "5",
                "dailyText": "4",
                "dirty": False,
            }
            and after_failed_preset
            == {
                "draftMode": "custom",
                "controlMode": "custom",
                "dirty": False,
                "error": "保存失败，已恢复上一次设置",
                "backendMode": "custom",
                "backendMinutes": 5,
                "backendDaily": 4,
            }
            and after_failed_custom
            == {
                "draftMode": "custom",
                "minutes": 33,
                "dailyText": "8",
                "daily": 8,
                "dirty": True,
                "error": "保存失败，修改仍保留；可以重试或恢复已保存",
                "backendMode": "custom",
                "backendMinutes": 5,
                "backendDaily": 4,
            }
            and after_failed_custom_restore
            == {
                "draftMode": "custom",
                "minutes": 5,
                "daily": 4,
                "dirty": False,
                "error": "",
            }
            and after_non_frequency_preference
            == {
                "draftMode": "custom",
                "minutes": 41,
                "dailyText": "7",
                "dirty": True,
                "conflict": False,
                "pending": False,
                "backendMode": "custom",
                "interestWeight": 61,
            }
            and after_external_conflict
            == {
                "draftMode": "custom",
                "minutes": 41,
                "dailyText": "7",
                "dirty": True,
                "conflict": True,
                "pending": True,
                "backendMode": "quiet",
                "noticeVisible": True,
                "restoreText": "恢复最新",
                "forceVisible": True,
                "forceEnabled": True,
            }
            and after_blocked_apply
            == {
                "draftMode": "custom",
                "minutes": 41,
                "dailyText": "7",
                "dirty": True,
                "conflict": True,
                "backendMode": "quiet",
                "backendMinutes": 45,
                "backendDaily": 6,
                "error": "未应用：频率已在其他位置更新；请恢复最新设置，或选择“仍然应用”确认覆盖",
            }
            and after_conflict_restore
            == {
                "draftMode": "quiet",
                "baselineMode": "quiet",
                "minutes": 5,
                "daily": 4,
                "dirty": False,
                "conflict": False,
                "pending": False,
                "backendMode": "quiet",
                "restoreText": "恢复已保存",
            }
            and after_forced_apply
            == {
                "draftMode": "custom",
                "baselineMode": "custom",
                "minutes": 42,
                "daily": 8,
                "dirty": False,
                "conflict": False,
                "pending": False,
                "backendMode": "custom",
                "backendMinutes": 42,
                "backendDaily": 8,
                "error": "",
            }
            and after_external_before_typing
            == {
                "dirty": False,
                "editing": True,
                "pending": True,
                "conflict": False,
                "backendMode": "quiet",
            }
            and after_late_typing
            == {
                "text": "43",
                "dirty": True,
                "pending": True,
                "conflict": True,
                "backendMode": "quiet",
            }
            and after_late_typing_blocked_apply
            == {
                "draftText": "43",
                "conflict": True,
                "backendMode": "quiet",
                "backendMinutes": 45,
                "backendDaily": 6,
                "noticeVisible": True,
                "forceVisible": True,
                "sameError": True,
            }
            and interest_draft_protection
            == {
                "typed": "draft,topic",
                "afterHeartbeat": "draft,topic",
                "afterUnrelatedPreference": "draft,topic",
                "dirtyAfterUnrelatedPreference": True,
                "mixAfterUnrelatedPreference": [62, 38, 31],
                "savedInterests": ["draft", "topic"],
                "dirtyAfterCommit": False,
                "canonicalAfterCommit": True,
                "cleanExternalRefill": True,
                "savedAfterExternalRefill": ["external", "clean"],
            }
            and screen_reopen_cancellation
            == {
                "buttonEnabled": True,
                "afterClick": {
                    "chatOpen": False,
                    "visible": False,
                    "timerRunning": True,
                },
                "afterReopen": {
                    "chatOpen": True,
                    "visible": True,
                    "timerRunning": False,
                },
                "requestCallsAfterDeadline": 0,
                "feedbackUnchanged": True,
            },
            "zeroMix": zero_mix,
            "afterExternalChange": after_external_change,
            "typedBeforeCommit": typed_before_commit,
            "duringEdit": during_edit,
            "afterBoundaryCommit": after_boundary_commit,
            "afterRestore": after_restore,
            "incrementalTyping": incremental_typing,
            "beforeDirectApply": before_direct_apply,
            "afterApply": {
                "frequency": after_apply["frequency"],
                "minimumMinutes": after_apply["minimumMinutes"],
                "dailyLimit": after_apply["dailyLimit"],
            },
            "afterApplyDraft": after_apply_draft,
            "afterRestart": after_restart,
            "beforeSettingsClose": before_settings_close,
            "afterSettingsClose": after_settings_close,
            "dirtyAfterClose": dirty_after_close,
            "dirtyAfterReopen": dirty_after_reopen,
            "restoredAfterDirtyClose": restored_after_dirty_close,
            "afterDirectBoundaryApply": after_direct_boundary_apply,
            "afterFailedPreset": after_failed_preset,
            "afterFailedCustom": after_failed_custom,
            "afterFailedCustomRestore": after_failed_custom_restore,
            "afterNonFrequencyPreference": after_non_frequency_preference,
            "afterExternalConflict": after_external_conflict,
            "afterBlockedApply": after_blocked_apply,
            "afterConflictRestore": after_conflict_restore,
            "afterForcedApply": after_forced_apply,
            "afterExternalBeforeTyping": after_external_before_typing,
            "afterLateTyping": after_late_typing,
            "afterLateTypingBlockedApply": after_late_typing_blocked_apply,
            "interestDraftProtection": interest_draft_protection,
            "screenReopenCancellation": screen_reopen_cancellation,
        }
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    finally:
        backend.shutdown()
        engine.deleteLater()
        app.processEvents()
        temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
