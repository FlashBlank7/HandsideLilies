from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# These must be selected before constructing QApplication.  The 150% scale is
# deliberate: this verifier is a regression harness for the Windows DPI case
# that previously clipped the last body line against the action row.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import QObject, QPoint, QPointF, Property, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_ROOT = PROJECT_ROOT / "qml"
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "bubble-audit"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class MockCompanionController(QObject):
    bubbleChanged = Signal()
    busyChanged = Signal()

    def __init__(self, bubble: dict[str, Any]) -> None:
        super().__init__()
        self._bubble = dict(bubble)
        self._busy = False
        self.calls: list[list[Any]] = []

    @Property("QVariantMap", notify=bubbleChanged)
    def bubble(self) -> dict[str, Any]:
        return dict(self._bubble)

    def set_bubble(self, value: dict[str, Any]) -> None:
        self._bubble = dict(value)
        self.bubbleChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, value: bool) -> None:
        self._busy = bool(value)
        self.busyChanged.emit()

    def _record(self, name: str, *values: Any) -> None:
        self.calls.append([name, *values])

    @Slot(str)
    def another(self, bubble_id: str) -> None:
        self._record("another", bubble_id)

    @Slot(str, str)
    def reply(self, bubble_id: str, text: str) -> None:
        self._record("reply", bubble_id, text)

    @Slot(str)
    def requestCategory(self, category: str) -> None:
        self._record("category", category)

    @Slot(str, int, int)
    def setFrequency(self, mode: str, minutes: int, count: int) -> None:
        self._record("frequency", mode, minutes, count)

    @Slot(int)
    def snooze(self, minutes: int) -> None:
        self._record("snooze", minutes)

    @Slot()
    def muteCurrentApp(self) -> None:
        self._record("mute")

    @Slot()
    def openSource(self) -> None:
        self._record("source")

    @Slot()
    def saveMoment(self) -> None:
        self._record("save")

    @Slot()
    def moveToBox(self) -> None:
        self._record("box")

    @Slot()
    def dismiss(self) -> None:
        self._record("dismiss")


class MockFocusBackend(QObject):
    focusDiversionChanged = Signal()

    def __init__(self, bubble: dict[str, Any]) -> None:
        super().__init__()
        self._bubble = dict(bubble)
        self.calls: list[list[str]] = []

    @Property("QVariantMap", notify=focusDiversionChanged)
    def focusDiversion(self) -> dict[str, Any]:
        return dict(self._bubble)

    def set_bubble(self, value: dict[str, Any]) -> None:
        self._bubble = dict(value)
        self.focusDiversionChanged.emit()

    @Slot(str, str)
    def focusDiversionAction(self, action: str, session_id: str) -> None:
        self.calls.append([action, session_id])


def load_windows_ui_fonts(app: QApplication) -> dict[str, Any]:
    """Load real Windows UI fonts instead of relying on offscreen fallback."""

    fonts_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    loaded: list[dict[str, Any]] = []
    families: list[str] = []
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        candidate = fonts_root / filename
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        registered = (
            list(QFontDatabase.applicationFontFamilies(font_id)) if font_id >= 0 else []
        )
        loaded.append(
            {"path": str(candidate), "id": font_id, "families": registered}
        )
        for family in registered:
            if family not in families:
                families.append(family)

    preferred = next(
        (family for family in families if "YaHei UI" in family),
        next((family for family in families if "YaHei" in family), "Microsoft YaHei UI"),
    )
    app_font = QFont(preferred)
    app_font.setPointSizeF(10.0)
    app.setFont(app_font)
    return {
        "loaded": loaded,
        "families": families,
        "applicationFamily": app.font().family(),
    }


def component_errors(component: QQmlComponent) -> str:
    return "\n".join(error.toString() for error in component.errors())


def create_window(
    engine: QQmlEngine,
    filename: str,
    initial_properties: dict[str, Any],
) -> tuple[QQmlComponent, QQuickWindow]:
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_ROOT / filename)))
    if component.status() == QQmlComponent.Status.Error:
        raise RuntimeError(component_errors(component))
    window = component.createWithInitialProperties(initial_properties)
    if window is None:
        raise RuntimeError(component_errors(component) or f"{filename} did not load")
    if not isinstance(window, QQuickWindow):
        raise RuntimeError(f"{filename} must remain an independent QQuickWindow")
    return component, window


def settle(app: QApplication, milliseconds: int = 55) -> None:
    app.processEvents()
    QTest.qWait(milliseconds)
    app.processEvents()


def named_item(window: QQuickWindow, name: str) -> QQuickItem:
    item = window.findChild(QQuickItem, name)
    if item is None:
        raise RuntimeError(f"missing QML item: {name}")
    return item


def click(window: QQuickWindow, item: QQuickItem) -> None:
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(round(center.x()), round(center.y())),
    )


def scene_rect(item: QQuickItem) -> list[float]:
    top_left = item.mapToScene(QPointF(0, 0))
    return [
        round(top_left.x(), 2),
        round(top_left.y(), 2),
        round(item.width(), 2),
        round(item.height(), 2),
    ]


def control_metrics(window: QQuickWindow, names: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in names:
        item = named_item(window, name)
        result[name] = {
            "visible": item.isVisible(),
            "width": round(item.width(), 2),
            "height": round(item.height(), 2),
            "implicitWidth": round(float(item.property("implicitWidth") or 0), 2),
            "implicitHeight": round(float(item.property("implicitHeight") or 0), 2),
            "text": str(item.property("text") or ""),
        }
    return result


def controls_not_compressed(metrics: dict[str, Any]) -> bool:
    return all(
        not value["visible"]
        or (
            value["width"] + 0.75 >= value["implicitWidth"]
            and value["height"] + 0.75 >= value["implicitHeight"]
        )
        for value in metrics.values()
    )


def nonempty_visible_text(metrics: dict[str, Any]) -> bool:
    return all(
        not value["visible"] or bool(str(value["text"]).strip())
        for value in metrics.values()
    )


def visible_text_contains_cjk(metrics: dict[str, Any]) -> bool:
    return all(
        not value["visible"]
        or any("\u3400" <= character <= "\u9fff" for character in value["text"])
        for value in metrics.values()
    )


def placement_case(
    app: QApplication,
    window: QQuickWindow,
    accent_name: str,
    *,
    side: str,
) -> dict[str, Any]:
    geometry = window.screen().geometry()
    margin = float(window.property("screenMargin"))
    gap = float(window.property("sideGap"))
    usable_subject_width = max(
        18.0,
        min(96.0, geometry.width() - window.width() - gap - margin * 2 - 10),
    )
    if side == "left":
        subject_left = geometry.x() + margin + 4
        subject_right = subject_left + usable_subject_width
    else:
        subject_right = geometry.x() + geometry.width() - margin - 4
        subject_left = subject_right - usable_subject_width
    center_y = geometry.y() + geometry.height() * 0.52
    window.setProperty("subjectLeft", subject_left)
    window.setProperty("subjectRight", subject_right)
    window.setProperty("subjectCenterY", center_y)
    settle(app, 35)

    placed_right = bool(window.property("placeOnRight"))
    accent = named_item(window, accent_name)
    accent_rect = scene_rect(accent)
    screen_left = geometry.x() + margin
    screen_right = geometry.x() + geometry.width() - margin
    side_gap_held = (
        window.x() >= subject_right + gap - 1
        if placed_right
        else window.x() + window.width() <= subject_left - gap + 1
    )
    return {
        "requestedSide": side,
        "placedRight": placed_right,
        "subject": [round(subject_left, 2), round(subject_right, 2), round(center_y, 2)],
        "window": [window.x(), window.y(), window.width(), window.height()],
        "sideGapHeld": side_gap_held,
        "screenBoundsHeld": (
            window.x() >= screen_left - 1
            and window.x() + window.width() <= screen_right + 1
            and window.y() >= geometry.y() + margin - 1
            and window.y() + window.height()
            <= geometry.y() + geometry.height() - margin + 1
        ),
        "verticallyCentered": abs(window.y() + window.height() / 2 - center_y) <= 1,
        "accentFacesSubject": (
            accent_rect[0] <= 1 if placed_right
            else accent_rect[0] + accent_rect[2] >= window.width() - 1
        ),
    }


def save_window(app: QApplication, window: QQuickWindow, filename: str) -> dict[str, Any]:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    settle(app, 90)
    image = window.grabWindow()
    destination = ARTIFACT_ROOT / filename
    saved = not image.isNull() and image.save(str(destination))
    return {
        "path": str(destination),
        "saved": bool(saved),
        "logicalSize": [window.width(), window.height()],
        "pixelSize": [image.width(), image.height()],
        "devicePixelRatio": float(window.devicePixelRatio()),
    }


def main() -> int:
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    font_result = load_windows_ui_fonts(app)

    summary = (
        "离屏高 DPI 验证：这段中文正文需要自然换行，最后一行不能被操作按钮挤住；"
        "即使来源和场景标签同时出现，也要保留舒展的阅读空间。"
    )
    detail = (
        "展开只改变阅读范围，不应请求键盘焦点。只有明确点击回复以后，窗口才临时接受焦点，"
        "并把输入焦点交给回复框；发送结束后应立即恢复不抢焦点的工具窗口语义。"
    )
    companion_data: dict[str, Any] = {
        "id": "bubble-dpi-audit",
        "category": "科普",
        "summary": summary,
        "detail": detail,
        "source": {
            "name": "本地验证来源",
            "publishedAt": "2026-08-29T00:00:00+09:00",
            "url": "https://example.invalid/audit",
        },
        "sourceRole": "context",
        "sceneLabel": "高 DPI 桌面",
        "hasCapture": True,
        "visible": True,
        "busy": False,
    }
    focus_data: dict[str, Any] = {
        "sessionId": "focus-dpi-audit",
        "visible": True,
        "title": "专注轻提醒",
        "text": "刚才的专注还在。要回去、把这段算作休息，还是结束专注？",
    }
    companion_controller = MockCompanionController(companion_data)
    focus_backend = MockFocusBackend(focus_data)
    engine = QQmlEngine()
    companion_component, companion = create_window(
        engine, "CompanionBubble.qml", {"controller": companion_controller}
    )
    focus_component, focus = create_window(
        engine, "FocusDiversionBubble.qml", {"appBackend": focus_backend}
    )
    # Keep the components alive for the same lifetime as their instances.
    _components = (companion_component, focus_component)
    settle(app, 240)

    companion_reply_input = named_item(companion, "companionReplyInput")
    default_focus = {
        "companionDoesNotAcceptFocus": bool(
            companion.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "companionReplyHasFocus": companion_reply_input.hasActiveFocus(),
        "focusDiversionDoesNotAcceptFocus": bool(
            focus.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
    }

    companion_left = placement_case(
        app, companion, "companionFacingAccent", side="left"
    )
    companion_left_screenshot = save_window(
        app, companion, "companion-collapsed-left-subject-150.png"
    )
    companion_right = placement_case(
        app, companion, "companionFacingAccent", side="right"
    )
    companion_right_screenshot = save_window(
        app, companion, "companion-collapsed-right-subject-150.png"
    )

    companion_controls = control_metrics(
        companion,
        [
            "companionMenuButton",
            "companionCloseButton",
            "companionAnotherButton",
            "companionDetailButton",
            "companionReplyButton",
        ],
    )
    companion_labels = control_metrics(
        companion,
        [
            "companionCategoryLabel",
            "companionSceneLabel",
            "companionSourceLabel",
            "companionBodyText",
            "companionAnotherButton",
            "companionDetailButton",
            "companionReplyButton",
        ],
    )
    companion_body = named_item(companion, "companionBodyText")
    companion_scroll = named_item(companion, "companionBodyScroll")
    companion_action_row = named_item(companion, "companionActionRow")
    body_rect = scene_rect(companion_scroll)
    actions_rect = scene_rect(companion_action_row)
    collapsed_layout = {
        "controlsNotCompressed": controls_not_compressed(companion_controls),
        "visibleLabelsNonempty": nonempty_visible_text(companion_labels),
        "visibleLabelsContainCjk": visible_text_contains_cjk(companion_labels),
        "bodyContentHeight": round(float(companion_body.property("contentHeight") or 0), 2),
        "bodyRequiredHeight": round(
            max(
                companion_body.implicitHeight(),
                float(companion_body.property("contentHeight") or 0)
                + float(companion_body.property("topPadding") or 0)
                + float(companion_body.property("bottomPadding") or 0),
            ),
            2,
        ),
        "bodyViewportHeight": round(
            float(companion_scroll.property("availableHeight") or companion_scroll.height()), 2
        ),
        "bodyFits": max(
            companion_body.implicitHeight(),
            float(companion_body.property("contentHeight") or 0)
            + float(companion_body.property("topPadding") or 0)
            + float(companion_body.property("bottomPadding") or 0),
        ) <= float(companion_scroll.property("availableHeight") or companion_scroll.height()) + 1,
        "bodyDoesNotOverlapActions": body_rect[1] + body_rect[3] <= actions_rect[1] + 0.75,
        "labels": {name: value["text"] for name, value in companion_labels.items()},
        "controls": companion_controls,
    }

    busy_data = dict(companion_data)
    busy_data["busy"] = True
    companion_controller.set_bubble(busy_data)
    settle(app, 240)
    busy_label = named_item(companion, "companionBusyLabel")
    busy_scroll = named_item(companion, "companionBodyScroll")
    busy_body = named_item(companion, "companionBodyText")
    busy_required_height = max(
        busy_body.implicitHeight(),
        float(busy_body.property("contentHeight") or 0)
        + float(busy_body.property("topPadding") or 0)
        + float(busy_body.property("bottomPadding") or 0),
    )
    busy_layout = {
        "effectiveBusy": bool(companion.property("effectiveBusy")),
        "labelVisible": busy_label.isVisible(),
        "label": str(busy_label.property("text") or ""),
        "bodyFits": busy_required_height
        <= float(busy_scroll.property("availableHeight") or busy_scroll.height()) + 1,
    }
    companion_controller.set_bubble(companion_data)
    companion_controller.set_busy(True)
    settle(app, 240)
    controller_busy = {
        "effectiveBusy": bool(companion.property("effectiveBusy")),
        "payloadBusy": bool(companion_data["busy"]),
        "labelVisible": named_item(companion, "companionBusyLabel").isVisible(),
        "actionsDisabled": all(
            not bool(named_item(companion, name).property("enabled"))
            for name in (
                "companionMenuButton",
                "companionAnotherButton",
                "companionDetailButton",
                "companionReplyButton",
            )
        ),
        "closeEnabled": bool(
            named_item(companion, "companionCloseButton").property("enabled")
        ),
    }
    companion_controller.set_busy(False)
    settle(app, 90)

    initial_side = bool(companion.property("placeOnRight"))
    detail_button = named_item(companion, "companionDetailButton")
    click(companion, detail_button)
    settle(app, 240)
    expanded_body_text = str(
        named_item(companion, "companionBodyText").property("text") or ""
    )
    expanded_semantics = {
        "expanded": bool(companion.property("expanded")),
        "detailPinned": bool(companion.property("detailPinned")),
        "sideStayedStable": bool(companion.property("placeOnRight")) is initial_side,
        "doesNotAcceptFocus": bool(
            companion.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "replyHasFocus": companion_reply_input.hasActiveFocus(),
    }
    expanded_screenshot = save_window(
        app, companion, "companion-expanded-no-focus-150.png"
    )
    click(companion, detail_button)
    settle(app, 240)
    expanded_semantics["collapsedAgain"] = not bool(companion.property("expanded"))

    reply_button = named_item(companion, "companionReplyButton")
    click(companion, reply_button)
    settle(app, 260)
    reply_controls = control_metrics(
        companion,
        ["companionReplyInput", "companionSendButton"],
    )
    reply_semantics = {
        "replying": bool(companion.property("replying")),
        "expanded": bool(companion.property("expanded")),
        "acceptsFocusTemporarily": not bool(
            companion.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "inputHasFocus": companion_reply_input.hasActiveFocus(),
        "sideStayedStable": bool(companion.property("placeOnRight")) is initial_side,
        "controlsNotCompressed": controls_not_compressed(reply_controls),
    }
    reply_screenshot = save_window(app, companion, "companion-reply-focus-150.png")
    companion_reply_input.setProperty("text", "收到，请继续。")
    click(companion, named_item(companion, "companionSendButton"))
    settle(app, 90)
    reply_semantics.update(
        {
            "replyCall": companion_controller.calls[-1]
            if companion_controller.calls
            else [],
            "closedAfterSend": not bool(companion.property("replying")),
            "focusReleasedAfterSend": not companion_reply_input.hasActiveFocus(),
            "doesNotAcceptFocusAfterSend": bool(
                companion.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
            ),
        }
    )

    concise_reply = dict(companion_data)
    concise_reply.update({"summary": "这是新的简短回复。", "detail": "这是新的简短回复。"})
    companion_controller.set_bubble(concise_reply)
    settle(app, 90)
    pinned_detail_button = named_item(companion, "companionDetailButton")
    pinned_without_detail = {
        "hasDetail": bool(companion.property("hasDetail")),
        "detailPinned": bool(companion.property("detailPinned")),
        "expanded": bool(companion.property("expanded")),
        "buttonVisible": pinned_detail_button.isVisible(),
        "buttonEnabled": bool(pinned_detail_button.property("enabled")),
        "buttonText": str(pinned_detail_button.property("text") or ""),
    }
    click(companion, pinned_detail_button)
    settle(app, 90)
    pinned_without_detail.update(
        {
            "collapsedAfterClick": not bool(companion.property("expanded")),
            "buttonHiddenAfterClick": not pinned_detail_button.isVisible(),
        }
    )

    same_length_summary = "甲乙丙丁戊己庚辛"
    same_length_detail = "天地玄黄宇宙洪荒"
    same_length_data = dict(companion_data)
    same_length_data.update(
        {
            "id": "same-length-detail",
            "summary": same_length_summary,
            "detail": same_length_detail,
            "source": {},
            "sceneLabel": "",
        }
    )
    companion_controller.set_bubble(same_length_data)
    settle(app, 90)
    distinct_detail = {
        "sameLength": len(same_length_summary) == len(same_length_detail),
        "hasDetail": bool(companion.property("hasDetail")),
        "buttonVisible": named_item(companion, "companionDetailButton").isVisible(),
    }

    focus_left = placement_case(
        app, focus, "focusDiversionFacingAccent", side="left"
    )
    focus_left_screenshot = save_window(
        app, focus, "focus-diversion-left-subject-150.png"
    )
    focus_right = placement_case(
        app, focus, "focusDiversionFacingAccent", side="right"
    )
    focus_right_screenshot = save_window(
        app, focus, "focus-diversion-right-subject-150.png"
    )
    focus_controls = control_metrics(
        focus,
        [
            "focusDiversionCloseButton",
            "focusDiversionReturnButton",
            "focusDiversionRestButton",
            "focusDiversionFinishButton",
        ],
    )
    focus_labels = control_metrics(
        focus,
        [
            "focusDiversionTitleLabel",
            "focusDiversionBodyLabel",
            "focusDiversionReturnButton",
            "focusDiversionRestButton",
            "focusDiversionFinishButton",
        ],
    )
    focus_body = named_item(focus, "focusDiversionBodyLabel")
    focus_scroll = named_item(focus, "focusDiversionBodyScroll")
    focus_actions = named_item(focus, "focusDiversionActionGrid")
    focus_body_rect = scene_rect(focus_scroll)
    focus_action_rect = scene_rect(focus_actions)
    focus_layout = {
        "controlsNotCompressed": controls_not_compressed(focus_controls),
        "visibleLabelsNonempty": nonempty_visible_text(focus_labels),
        "visibleLabelsContainCjk": visible_text_contains_cjk(focus_labels),
        "bodyFits": focus_body.implicitHeight()
        <= float(focus_scroll.property("availableHeight") or focus_scroll.height()) + 1,
        "bodyDoesNotOverlapActions": focus_body_rect[1] + focus_body_rect[3]
        <= focus_action_rect[1] + 0.75,
        "columns": int(focus_actions.property("columns")),
        "labels": {name: value["text"] for name, value in focus_labels.items()},
        "controls": focus_controls,
    }

    for object_name in (
        "focusDiversionReturnButton",
        "focusDiversionRestButton",
        "focusDiversionFinishButton",
        "focusDiversionCloseButton",
    ):
        click(focus, named_item(focus, object_name))
        settle(app, 25)
    focus_actions_result = {
        "calls": list(focus_backend.calls),
        "doesNotAcceptFocusAfterClicks": bool(
            focus.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
    }

    companion.setProperty("suppressed", True)
    focus.setProperty("suppressed", True)
    settle(app, 40)
    suppression = {
        "companionHidden": not companion.isVisible(),
        "focusHidden": not focus.isVisible(),
    }
    companion.setProperty("suppressed", False)
    focus.setProperty("suppressed", False)
    settle(app, 40)
    suppression.update(
        companionRestored=companion.isVisible(),
        focusRestored=focus.isVisible(),
    )

    screenshots = {
        "companionLeft": companion_left_screenshot,
        "companionRight": companion_right_screenshot,
        "companionExpanded": expanded_screenshot,
        "companionReply": reply_screenshot,
        "focusLeft": focus_left_screenshot,
        "focusRight": focus_right_screenshot,
    }
    placement_results = [companion_left, companion_right, focus_left, focus_right]
    passed = all(
        (
            os.name != "nt" or bool(font_result["loaded"]),
            default_focus["companionDoesNotAcceptFocus"],
            not default_focus["companionReplyHasFocus"],
            default_focus["focusDiversionDoesNotAcceptFocus"],
            all(case["sideGapHeld"] for case in placement_results),
            all(case["screenBoundsHeld"] for case in placement_results),
            all(case["verticallyCentered"] for case in placement_results),
            all(case["accentFacesSubject"] for case in placement_results),
            companion_left["placedRight"],
            not companion_right["placedRight"],
            focus_left["placedRight"],
            not focus_right["placedRight"],
            collapsed_layout["controlsNotCompressed"],
            collapsed_layout["visibleLabelsNonempty"],
            collapsed_layout["visibleLabelsContainCjk"],
            collapsed_layout["bodyFits"],
            collapsed_layout["bodyDoesNotOverlapActions"],
            busy_layout == {
                "effectiveBusy": True,
                "labelVisible": True,
                "label": "正在整理……",
                "bodyFits": True,
            },
            controller_busy
            == {
                "effectiveBusy": True,
                "payloadBusy": False,
                "labelVisible": True,
                "actionsDisabled": True,
                "closeEnabled": True,
            },
            expanded_semantics["expanded"],
            expanded_semantics["detailPinned"],
            expanded_semantics["sideStayedStable"],
            expanded_semantics["doesNotAcceptFocus"],
            not expanded_semantics["replyHasFocus"],
            expanded_semantics["collapsedAgain"],
            expanded_body_text == summary + "\n\n" + detail,
            reply_semantics["replying"],
            reply_semantics["expanded"],
            reply_semantics["acceptsFocusTemporarily"],
            reply_semantics["inputHasFocus"],
            reply_semantics["sideStayedStable"],
            reply_semantics["controlsNotCompressed"],
            reply_semantics["replyCall"]
            == ["reply", "bubble-dpi-audit", "收到，请继续。"],
            reply_semantics["closedAfterSend"],
            reply_semantics["focusReleasedAfterSend"],
            reply_semantics["doesNotAcceptFocusAfterSend"],
            pinned_without_detail
            == {
                "hasDetail": False,
                "detailPinned": True,
                "expanded": True,
                "buttonVisible": True,
                "buttonEnabled": True,
                "buttonText": "简单点",
                "collapsedAfterClick": True,
                "buttonHiddenAfterClick": True,
            },
            distinct_detail["sameLength"],
            distinct_detail["hasDetail"],
            distinct_detail["buttonVisible"],
            focus_layout["controlsNotCompressed"],
            focus_layout["visibleLabelsNonempty"],
            focus_layout["visibleLabelsContainCjk"],
            focus_layout["bodyFits"],
            focus_layout["bodyDoesNotOverlapActions"],
            focus_actions_result["calls"]
            == [
                ["return", "focus-dpi-audit"],
                ["rest", "focus-dpi-audit"],
                ["finish", "focus-dpi-audit"],
                ["dismiss", "focus-dpi-audit"],
            ],
            focus_actions_result["doesNotAcceptFocusAfterClicks"],
            all(suppression.values()),
            all(value["saved"] for value in screenshots.values()),
            all(value["devicePixelRatio"] >= 1.49 for value in screenshots.values()),
        )
    )

    outcome = {
        "passed": passed,
        "scaleFactor": os.environ.get("QT_SCALE_FACTOR"),
        "font": font_result,
        "defaultFocus": default_focus,
        "placement": {
            "companionLeft": companion_left,
            "companionRight": companion_right,
            "focusLeft": focus_left,
            "focusRight": focus_right,
        },
        "collapsedLayout": collapsed_layout,
        "busyLayout": busy_layout,
        "controllerBusy": controller_busy,
        "expandedSemantics": expanded_semantics,
        "expandedBodyText": expanded_body_text,
        "replySemantics": reply_semantics,
        "pinnedWithoutDistinctDetail": pinned_without_detail,
        "distinctSameLengthDetail": distinct_detail,
        "focusLayout": focus_layout,
        "focusActions": focus_actions_result,
        "suppression": suppression,
        "screenshots": screenshots,
    }
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))

    companion.setVisible(False)
    focus.setVisible(False)
    companion.deleteLater()
    focus.deleteLater()
    engine.deleteLater()
    settle(app, 15)
    del _components
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
