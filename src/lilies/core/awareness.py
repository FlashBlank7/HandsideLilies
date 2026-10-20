"""Content-free, application-owned input receipts; never inferred from prose.

An enabled permission is not evidence of observation. These labels describe the
inputs of a particular response, not the user's current desktop or model belief.
"""
from __future__ import annotations

from typing import Any, Mapping


_BASIS = {
    "none": ("本次没有观察", "没有把窗口画面或选中文字交给模型。开启感知或授权截图，不代表已经看过屏幕。"),
    "unknown": ("依据未记录", "这条内容没有可核对的输入记录，不能据此判断莉莉丝看过什么。"),
    "application": ("只知道应用", "这条气泡只有粗粒度应用类别，没有窗口标题、正文或截图。也可能参考你设置的兴趣和主动查阅的记忆；并不代表看过正在读的内容。"),
    "window-image": ("本条用过截图", "生成这条气泡时用过一次活动窗口截图，不是持续观看，也不代表知道现在的画面。图片暂存会在请求结束后清理。"),
    "image-anchor": ("沿用上次线索", "这次沿用上一条气泡保留的文字线索，没有重新发送截图，也不能发现新的画面细节。"),
    "dialogue": ("沿用这段对话", "这次依据气泡内的对话和你刚写的话，没有重新读取窗口或截图；需要时仍可能查阅已保存的记忆。"),
    "source": ("依据来源摘要", "这条内容依据所列来源的标题、日期和短摘要，也可能结合应用类别或兴趣；不是读取你屏幕上的文章，更不表示已读来源全文。"),
    "selected-text": ("你选的文字", "只处理这次交来的文字和本次问题，不读取整篇文档，不带聊天历史或记忆。保存后的卡片另有本地记忆索引。"),
    "image-submitted": ("图像请求已发起", "程序已发起带画面的请求，但还没有可用解读；这不保证服务端已收到，也不能把等待、失败或取消当成已经理解了画面。"),
}


def evidence(kind: str, *, at: str = "") -> dict[str, str]:
    key = kind if kind in _BASIS else "unknown"
    label, detail = _BASIS[key]
    return {"kind": key, "label": label, "detail": detail, "at": str(at)[:40]}


def bubble_evidence(bubble: Mapping[str, Any]) -> dict[str, str]:
    if not bubble:
        return evidence("none")
    context = bubble.get("contextType")
    if bubble.get("replyBasis") in {"image-anchor", "dialogue"}:
        kind = str(bubble["replyBasis"])
    elif context == "active-window-image" and bubble.get("imageGrounded") is True:
        kind = "window-image"
    elif context == "retained-image-anchor":
        kind = "image-anchor"
    elif context == "application-signal":
        source = bubble.get("source")
        kind = "source" if isinstance(source, Mapping) and source.get("url") and source.get("name") else "application"
    else:
        kind = "unknown"
    value = evidence(kind, at=str(bubble.get("basisAt") or bubble.get("createdAt", "")))
    if bubble.get("restoredUnread") or bubble.get("redisplayed"):
        return {**value, "kind": "history", "originalKind": kind, "label": "之前的气泡",
            "detail": "这是重新显示的旧内容，本次没有重新观察。原来依据：" + value["label"] + "。" + value["detail"]}
    return value


def capture_evidence(diagnostic: Mapping[str, Any]) -> dict[str, str]:
    # Pixels obtained locally are NOT a claim that the model saw them. An
    # accepted response is tracked separately from successful screen capture.
    if diagnostic.get("imageSubmitted") is True:
        kind = "window-image" if diagnostic.get("imageResponseAccepted") is True and diagnostic.get("pixelsUsed") is True else "image-submitted"
    else:
        kind = "none"
    return evidence(kind, at=str(diagnostic.get("at", "")))
