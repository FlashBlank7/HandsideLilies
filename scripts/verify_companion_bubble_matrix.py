from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Every run is an isolated, software-rendered process.  The caller selects the
# scale before QApplication is created so no real desktop surface is touched.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_SCALE_FACTOR", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlEngine

from verify_companion_bubbles import (
    MockCompanionController,
    click,
    control_metrics,
    controls_not_compressed,
    create_window,
    load_windows_ui_fonts,
    named_item,
    save_window,
    scene_rect,
    settle,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def item_inside_window(window: QQuickWindow, item: QQuickItem) -> bool:
    x, y, width, height = scene_rect(item)
    return (
        width > 0
        and height > 0
        and x >= -0.75
        and y >= -0.75
        and x + width <= window.width() + 0.75
        and y + height <= window.height() + 0.75
    )


def body_metrics(window: QQuickWindow) -> dict[str, Any]:
    body = named_item(window, "companionBodyText")
    scroll = named_item(window, "companionBodyScroll")
    vertical_bar = named_item(window, "companionBodyScrollBar")
    action_row = named_item(window, "companionActionRow")
    body_rect = scene_rect(scroll)
    vertical_bar_rect = scene_rect(vertical_bar)
    action_rect = scene_rect(action_row)
    required = max(
        body.implicitHeight(),
        float(body.property("contentHeight") or 0)
        + float(body.property("topPadding") or 0)
        + float(body.property("bottomPadding") or 0),
    )
    viewport = float(scroll.property("availableHeight") or scroll.height())
    vertical_bar_right = vertical_bar_rect[0] + vertical_bar_rect[2]
    body_right = body_rect[0] + body_rect[2]
    vertical_bar_bottom = vertical_bar_rect[1] + vertical_bar_rect[3]
    body_bottom = body_rect[1] + body_rect[3]
    return {
        "requiredHeight": round(required, 2),
        "viewportHeight": round(viewport, 2),
        "fits": required <= viewport + 1,
        "positiveViewport": viewport >= 40,
        "doesNotOverlapActions": body_rect[1] + body_rect[3]
        <= action_rect[1] + 0.75,
        "bodyInsideWindow": item_inside_window(window, scroll),
        "actionsInsideWindow": item_inside_window(window, action_row),
        "verticalScrollBar": {
            "visible": vertical_bar.isVisible(),
            "rect": vertical_bar_rect,
            "positiveSize": vertical_bar_rect[2] > 0
            and vertical_bar_rect[3] > 0,
            "insideWindow": item_inside_window(window, vertical_bar),
            "insideBodyViewport": (
                vertical_bar_rect[0] >= body_rect[0] - 0.75
                and vertical_bar_rect[1] >= body_rect[1] - 0.75
                and vertical_bar_right <= body_right + 0.75
                and vertical_bar_bottom <= body_bottom + 0.75
            ),
            "inRightHalf": (
                vertical_bar_rect[0] + vertical_bar_rect[2] / 2
                >= body_rect[0] + body_rect[2] / 2
            ),
            "anchoredToRight": abs(vertical_bar_right - body_right) <= 0.75,
        },
    }


def button_metrics(window: QQuickWindow) -> dict[str, Any]:
    names = [
        "companionMenuButton",
        "companionCloseButton",
        "companionAnotherButton",
        "companionDetailButton",
        "companionReplyButton",
    ]
    metrics = control_metrics(window, names)
    return {
        "controls": metrics,
        "notCompressed": controls_not_compressed(metrics),
        "visibleTargetsAtLeast40": all(
            not value["visible"]
            or (value["width"] >= 39.5 and value["height"] >= 39.5)
            for value in metrics.values()
        ),
        "visibleTargetsInsideWindow": all(
            not value["visible"] or item_inside_window(window, named_item(window, name))
            for name, value in metrics.items()
        ),
    }


def exercise_case(
    app: QApplication,
    window: QQuickWindow,
    controller: MockCompanionController,
    *,
    name: str,
    bubble: dict[str, Any],
    expect_detail: bool,
    expect_collapsed_fit: bool,
    expect_truncated: bool,
    expect_expanded_overflow: bool,
    scale_tag: str,
) -> dict[str, Any]:
    controller.calls.clear()
    controller.set_bubble(bubble)
    settle(app, 240)

    reply_input = named_item(window, "companionReplyInput")
    detail_button = named_item(window, "companionDetailButton")
    another_button = named_item(window, "companionAnotherButton")
    reply_button = named_item(window, "companionReplyButton")

    initial_focus = {
        "doesNotAcceptFocus": bool(
            window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "replyInputHasFocus": reply_input.hasActiveFocus(),
    }
    collapsed = body_metrics(window)
    collapsed.update(
        {
            "textTruncated": bool(window.property("collapsedTextTruncated")),
            "displayText": str(window.property("collapsedDisplayText") or ""),
            "displayEndsWithEllipsis": str(
                window.property("collapsedDisplayText") or ""
            ).endswith("…"),
        }
    )
    buttons = button_metrics(window)
    collapsed_screenshot = save_window(
        app, window, f"companion-matrix-{scale_tag}-{name}-collapsed.png"
    )

    click(window, another_button)
    settle(app, 45)
    another_hit = controller.calls == [["another", bubble["id"]]]
    passive_focus_held = bool(
        window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
    ) and not reply_input.hasActiveFocus()

    expanded: dict[str, Any] = {
        "expected": expect_detail,
        "buttonVisible": detail_button.isVisible(),
    }
    expanded_screenshot: dict[str, Any] | None = None
    if expect_detail:
        click(window, detail_button)
        settle(app, 240)
        expanded_body = body_metrics(window)
        expanded_text = str(
            named_item(window, "companionBodyText").property("text") or ""
        )
        summary = str(bubble.get("summary") or "").strip()
        detail = str(bubble.get("detail") or "").strip() or summary
        expected_expanded_text = (
            detail if summary in detail else summary + "\n\n" + detail
        )
        expanded_scroll_bar = expanded_body["verticalScrollBar"]
        overflow_scroll_bar_valid = not expect_expanded_overflow or all(
            (
                expanded_scroll_bar["visible"],
                expanded_scroll_bar["positiveSize"],
                expanded_scroll_bar["insideWindow"],
                expanded_scroll_bar["insideBodyViewport"],
                expanded_scroll_bar["inRightHalf"],
                expanded_scroll_bar["anchoredToRight"],
            )
        )
        expanded.update(
            {
                "expanded": bool(window.property("expanded")),
                "detailPinned": bool(window.property("detailPinned")),
                "doesNotAcceptFocus": bool(
                    window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
                ),
                "replyInputHasFocus": reply_input.hasActiveFocus(),
                "contentMatchesExpected": expanded_text == expected_expanded_text,
                "body": expanded_body,
                "overflowMatchesExpectation": (
                    not expanded_body["fits"]
                ) is expect_expanded_overflow,
                "overflowScrollBarValid": overflow_scroll_bar_valid,
            }
        )
        expanded_screenshot = save_window(
            app, window, f"companion-matrix-{scale_tag}-{name}-expanded.png"
        )
        click(window, detail_button)
        settle(app, 240)
        expanded["collapsedAgain"] = not bool(window.property("expanded"))
    else:
        expanded.update(
            {
                "expanded": bool(window.property("expanded")),
                "detailPinned": bool(window.property("detailPinned")),
                "doesNotAcceptFocus": bool(
                    window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
                ),
                "replyInputHasFocus": reply_input.hasActiveFocus(),
                "contentMatchesExpected": True,
                "collapsedAgain": True,
                "overflowMatchesExpectation": True,
                "overflowScrollBarValid": True,
            }
        )

    click(window, reply_button)
    settle(app, 260)
    send_button = named_item(window, "companionSendButton")
    reply_controls = control_metrics(
        window, ["companionReplyInput", "companionSendButton"]
    )
    reply_open = {
        "replying": bool(window.property("replying")),
        "expanded": bool(window.property("expanded")),
        "acceptsFocusTemporarily": not bool(
            window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
        "inputHasFocus": reply_input.hasActiveFocus(),
        "controlsNotCompressed": controls_not_compressed(reply_controls),
        "controlsInsideWindow": item_inside_window(window, reply_input)
        and item_inside_window(window, send_button),
        "sendTargetAtLeast40": send_button.width() >= 39.5
        and send_button.height() >= 39.5,
    }
    reply_screenshot = save_window(
        app, window, f"companion-matrix-{scale_tag}-{name}-reply.png"
    )
    reply_text = f"{name}：收到。"
    reply_input.setProperty("text", reply_text)
    submit_method = "enter" if name == "medium" else "button"
    if submit_method == "enter":
        QTest.keyClick(window, Qt.Key.Key_Return)
    else:
        click(window, send_button)
    settle(app, 100)
    reply_closed = {
        "replyCall": controller.calls[-1] if controller.calls else [],
        "submitMethod": submit_method,
        "closed": not bool(window.property("replying")),
        "focusReleased": not reply_input.hasActiveFocus(),
        "doesNotAcceptFocusAgain": bool(
            window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus
        ),
    }

    screenshots = [collapsed_screenshot, reply_screenshot]
    if expanded_screenshot is not None:
        screenshots.append(expanded_screenshot)
    passed = all(
        (
            initial_focus == {
                "doesNotAcceptFocus": True,
                "replyInputHasFocus": False,
            },
            collapsed["fits"] is expect_collapsed_fit,
            collapsed["textTruncated"] is expect_truncated,
            collapsed["displayEndsWithEllipsis"] is expect_truncated,
            collapsed["positiveViewport"],
            collapsed["doesNotOverlapActions"],
            collapsed["bodyInsideWindow"],
            collapsed["actionsInsideWindow"],
            buttons["notCompressed"],
            buttons["visibleTargetsAtLeast40"],
            buttons["visibleTargetsInsideWindow"],
            another_hit,
            passive_focus_held,
            expanded["buttonVisible"] is expect_detail,
            expanded["expanded"] is expect_detail,
            expanded["detailPinned"] is expect_detail,
            expanded["doesNotAcceptFocus"],
            not expanded["replyInputHasFocus"],
            expanded["contentMatchesExpected"],
            expanded["collapsedAgain"],
            expanded["overflowMatchesExpectation"],
            expanded["overflowScrollBarValid"],
            reply_open["replying"],
            reply_open["expanded"],
            reply_open["acceptsFocusTemporarily"],
            reply_open["inputHasFocus"],
            reply_open["controlsNotCompressed"],
            reply_open["controlsInsideWindow"],
            reply_open["sendTargetAtLeast40"],
            reply_closed
            == {
                "replyCall": ["reply", bubble["id"], reply_text],
                "submitMethod": submit_method,
                "closed": True,
                "focusReleased": True,
                "doesNotAcceptFocusAgain": True,
            },
            all(screenshot["saved"] for screenshot in screenshots),
        )
    )
    return {
        "passed": passed,
        "window": [window.width(), window.height()],
        "initialFocus": initial_focus,
        "collapsed": collapsed,
        "buttons": buttons,
        "anotherHit": another_hit,
        "passiveFocusHeld": passive_focus_held,
        "expanded": expanded,
        "replyOpen": reply_open,
        "replyClosed": reply_closed,
        "screenshots": screenshots,
    }


def main() -> int:
    QQuickWindow.setDefaultAlphaBuffer(True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    font = load_windows_ui_fonts(app)

    short_summary = "我在这里。"
    medium_summary = (
        "刚才那段阅读像是在给一个复杂问题慢慢拆线。先休息一下眼睛，"
        "回来时再从最后那个定义继续，也不会丢掉思路。"
    )
    long_summary = (
        "这是一段用于验证折叠上限的中文说明。气泡不会把操作按钮挤出边界，"
        "也不会因为内容很多就覆盖回复入口。"
    ) * 7
    long_detail = (
        "展开后仍然保留有限尺寸，超出的正文交给阅读区滚动。标题、来源、"
        "快速操作与回复入口都应该留在稳定的位置。"
    ) * 28

    base = {
        "category": "科普",
        "source": {
            "name": "本地离屏验证",
            "publishedAt": "2026-08-30T00:00:00+09:00",
            "url": "https://example.invalid/companion-matrix",
        },
        "sceneLabel": "论文阅读",
        "hasCapture": False,
        "visible": True,
        "busy": False,
    }
    cases = [
        (
            "short",
            dict(base, id="matrix-short", summary=short_summary, detail=short_summary),
            False,
            True,
            False,
            False,
        ),
        (
            "medium",
            dict(
                base,
                id="matrix-medium",
                summary=medium_summary,
                detail=medium_summary + "如果你愿意，我也可以把这一点再讲细一点。",
            ),
            True,
            True,
            False,
            False,
        ),
        (
            "long",
            dict(
                base,
                id="matrix-long",
                summary=long_summary,
                detail=long_detail,
            ),
            True,
            True,
            True,
            True,
        ),
    ]

    controller = MockCompanionController(cases[0][1])
    engine = QQmlEngine()
    component, window = create_window(
        engine, "CompanionBubble.qml", {"controller": controller}
    )
    _keep_alive = component
    settle(app, 240)

    scale_value = float(os.environ.get("QT_SCALE_FACTOR", "1"))
    scale_tag = str(scale_value).replace(".", "p")
    results: dict[str, Any] = {}
    for (
        name,
        bubble,
        expect_detail,
        collapsed_fit,
        expect_truncated,
        expanded_overflow,
    ) in cases:
        results[name] = exercise_case(
            app,
            window,
            controller,
            name=name,
            bubble=bubble,
            expect_detail=expect_detail,
            expect_collapsed_fit=collapsed_fit,
            expect_truncated=expect_truncated,
            expect_expanded_overflow=expanded_overflow,
            scale_tag=scale_tag,
        )

    outcome = {
        "passed": all(case["passed"] for case in results.values()),
        "scaleFactor": os.environ.get("QT_SCALE_FACTOR"),
        "devicePixelRatio": float(window.devicePixelRatio()),
        "screen": [window.screen().geometry().width(), window.screen().geometry().height()],
        "font": font,
        "cases": results,
    }
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))

    window.setVisible(False)
    window.deleteLater()
    engine.deleteLater()
    settle(app, 20)
    del _keep_alive
    return 0 if outcome["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
