from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_companion_bubbles_high_dpi_layout_placement_and_focus() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_SCALE_FACTOR"] = "1.5"
    environment["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_companion_bubbles.py")],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])

    assert outcome["passed"] is True
    assert outcome["scaleFactor"] == "1.5"
    if os.name == "nt":
        assert outcome["font"]["loaded"]
        assert any(
            "Microsoft YaHei" in family
            for family in outcome["font"]["families"]
        )

    assert outcome["defaultFocus"] == {
        "companionDoesNotAcceptFocus": True,
        "companionReplyHasFocus": False,
        "focusDiversionDoesNotAcceptFocus": True,
    }

    placement = outcome["placement"]
    assert placement["companionLeft"]["placedRight"] is True
    assert placement["companionRight"]["placedRight"] is False
    assert placement["focusLeft"]["placedRight"] is True
    assert placement["focusRight"]["placedRight"] is False
    for case in placement.values():
        assert case["sideGapHeld"] is True
        assert case["screenBoundsHeld"] is True
        assert case["verticallyCentered"] is True
        assert case["accentFacesSubject"] is True

    collapsed = outcome["collapsedLayout"]
    assert collapsed["controlsNotCompressed"] is True
    assert collapsed["visibleLabelsNonempty"] is True
    assert collapsed["visibleLabelsContainCjk"] is True
    assert collapsed["bodyFits"] is True
    assert collapsed["bodyDoesNotOverlapActions"] is True
    assert collapsed["labels"] == {
        "companionAnotherButton": "换一个",
        "companionBodyText": (
            "离屏高 DPI 验证：这段中文正文需要自然换行，最后一行不能被操作按钮挤住；"
            "即使来源和场景标签同时出现，也要保留舒展的阅读空间。"
        ),
        "companionCategoryLabel": "科普",
        "companionDetailButton": "详细点",
        "companionReplyButton": "回复",
        "companionSceneLabel": "· 高 DPI 桌面",
        "companionSourceLabel": "起始来源 · 本地验证来源 · 2026-08-29",
    }
    assert outcome["busyLayout"] == {
        "bodyFits": True,
        "effectiveBusy": True,
        "label": "正在整理……",
        "labelVisible": True,
    }
    assert outcome["controllerBusy"] == {
        "actionsDisabled": True,
        "closeEnabled": True,
        "effectiveBusy": True,
        "labelVisible": True,
        "payloadBusy": False,
    }

    assert outcome["expandedSemantics"] == {
        "collapsedAgain": True,
        "detailPinned": True,
        "doesNotAcceptFocus": True,
        "expanded": True,
        "replyHasFocus": False,
        "sideStayedStable": True,
    }
    assert outcome["expandedBodyText"] == (
        "离屏高 DPI 验证：这段中文正文需要自然换行，最后一行不能被操作按钮挤住；"
        "即使来源和场景标签同时出现，也要保留舒展的阅读空间。"
        "\n\n"
        "展开只改变阅读范围，不应请求键盘焦点。只有明确点击回复以后，窗口才临时接受焦点，"
        "并把输入焦点交给回复框；发送结束后应立即恢复不抢焦点的工具窗口语义。"
    )
    reply = outcome["replySemantics"]
    assert reply == {
        "acceptsFocusTemporarily": True,
        "closedAfterSend": True,
        "controlsNotCompressed": True,
        "doesNotAcceptFocusAfterSend": True,
        "expanded": True,
        "focusReleasedAfterSend": True,
        "inputHasFocus": True,
        "replyCall": ["reply", "bubble-dpi-audit", "收到，请继续。"],
        "replying": True,
        "sideStayedStable": True,
    }
    assert outcome["pinnedWithoutDistinctDetail"] == {
        "buttonEnabled": True,
        "buttonHiddenAfterClick": True,
        "buttonText": "简单点",
        "buttonVisible": True,
        "collapsedAfterClick": True,
        "detailPinned": True,
        "expanded": True,
        "hasDetail": False,
    }
    assert outcome["distinctSameLengthDetail"] == {
        "buttonVisible": True,
        "hasDetail": True,
        "sameLength": True,
    }

    focus_layout = outcome["focusLayout"]
    assert focus_layout["controlsNotCompressed"] is True
    assert focus_layout["visibleLabelsNonempty"] is True
    assert focus_layout["visibleLabelsContainCjk"] is True
    assert focus_layout["bodyFits"] is True
    assert focus_layout["bodyDoesNotOverlapActions"] is True
    assert focus_layout["labels"] == {
        "focusDiversionBodyLabel": (
            "刚才的专注还在。要回去、把这段算作休息，还是结束专注？"
        ),
        "focusDiversionFinishButton": "结束专注",
        "focusDiversionRestButton": "这是休息",
        "focusDiversionReturnButton": "回到刚才的工作",
        "focusDiversionTitleLabel": "专注轻提醒",
    }
    assert outcome["focusActions"] == {
        "calls": [
            ["return", "focus-dpi-audit"],
            ["rest", "focus-dpi-audit"],
            ["finish", "focus-dpi-audit"],
            ["dismiss", "focus-dpi-audit"],
        ],
        "doesNotAcceptFocusAfterClicks": True,
    }

    for screenshot in outcome["screenshots"].values():
        assert screenshot["saved"] is True
        assert screenshot["devicePixelRatio"] >= 1.49
        assert screenshot["pixelSize"][0] > screenshot["logicalSize"][0]
        assert screenshot["pixelSize"][1] > screenshot["logicalSize"][1]
        assert Path(screenshot["path"]).is_file()


@pytest.mark.parametrize("scale", ["1", "1.5", "2"])
def test_companion_bubble_short_long_reply_matrix(scale: str) -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["QSG_RHI_BACKEND"] = "software"
    environment["QT_SCALE_FACTOR"] = scale
    environment["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
    environment["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "verify_companion_bubble_matrix.py"),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=40,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    outcome = json.loads(completed.stdout.strip().splitlines()[-1])

    assert outcome["passed"] is True
    assert outcome["scaleFactor"] == scale
    assert outcome["devicePixelRatio"] == pytest.approx(float(scale), abs=0.02)
    assert set(outcome["cases"]) == {"short", "medium", "long"}

    for name, case in outcome["cases"].items():
        assert case["passed"] is True, name
        assert case["initialFocus"] == {
            "doesNotAcceptFocus": True,
            "replyInputHasFocus": False,
        }
        assert case["buttons"]["notCompressed"] is True
        assert case["buttons"]["visibleTargetsAtLeast40"] is True
        assert case["buttons"]["visibleTargetsInsideWindow"] is True
        assert case["collapsed"]["positiveViewport"] is True
        assert case["collapsed"]["doesNotOverlapActions"] is True
        assert case["collapsed"]["bodyInsideWindow"] is True
        assert case["collapsed"]["actionsInsideWindow"] is True
        assert case["anotherHit"] is True
        assert case["passiveFocusHeld"] is True
        assert case["replyOpen"] == {
            "acceptsFocusTemporarily": True,
            "controlsInsideWindow": True,
            "controlsNotCompressed": True,
            "expanded": True,
            "inputHasFocus": True,
            "replying": True,
            "sendTargetAtLeast40": True,
        }
        assert case["replyClosed"]["closed"] is True
        assert case["replyClosed"]["focusReleased"] is True
        assert case["replyClosed"]["doesNotAcceptFocusAgain"] is True
        assert case["replyClosed"]["replyCall"][0] == "reply"
        assert case["replyClosed"]["submitMethod"] == (
            "enter" if name == "medium" else "button"
        )
        assert case["expanded"]["contentMatchesExpected"] is True
        assert all(screenshot["saved"] for screenshot in case["screenshots"])
        assert all(
            screenshot["devicePixelRatio"]
            == pytest.approx(float(scale), abs=0.02)
            for screenshot in case["screenshots"]
        )
        assert all(
            Path(screenshot["path"]).is_file()
            for screenshot in case["screenshots"]
        )

    assert outcome["cases"]["short"]["collapsed"]["fits"] is True
    assert outcome["cases"]["short"]["collapsed"]["textTruncated"] is False
    assert outcome["cases"]["short"]["expanded"]["buttonVisible"] is False
    assert outcome["cases"]["medium"]["collapsed"]["fits"] is True
    assert outcome["cases"]["medium"]["collapsed"]["textTruncated"] is False
    assert outcome["cases"]["medium"]["expanded"]["buttonVisible"] is True
    assert outcome["cases"]["medium"]["expanded"]["body"]["fits"] is True
    assert outcome["cases"]["long"]["collapsed"]["fits"] is True
    assert outcome["cases"]["long"]["collapsed"]["textTruncated"] is True
    assert (
        outcome["cases"]["long"]["collapsed"]["displayEndsWithEllipsis"]
        is True
    )
    assert outcome["cases"]["long"]["expanded"]["buttonVisible"] is True
    assert outcome["cases"]["long"]["expanded"]["body"]["fits"] is False
    assert (
        outcome["cases"]["long"]["expanded"]["overflowMatchesExpectation"]
        is True
    )
    assert (
        outcome["cases"]["long"]["expanded"]["overflowScrollBarValid"]
        is True
    )
    long_scroll_bar = outcome["cases"]["long"]["expanded"]["body"][
        "verticalScrollBar"
    ]
    assert long_scroll_bar["visible"] is True
    assert long_scroll_bar["positiveSize"] is True
    assert long_scroll_bar["insideWindow"] is True
    assert long_scroll_bar["insideBodyViewport"] is True
    assert long_scroll_bar["inRightHalf"] is True
    assert long_scroll_bar["anchoredToRight"] is True
