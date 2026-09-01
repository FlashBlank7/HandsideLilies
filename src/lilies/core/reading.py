from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


READING_ACTIONS = ("explain", "translate", "term", "ask")
READING_ACTION_LABELS = {
    "explain": "简释",
    "translate": "翻译",
    "term": "术语卡",
    "ask": "追问",
}


@dataclass(frozen=True, slots=True)
class ReadingRequest:
    """One isolated operation over one selection.

    A request intentionally contains no conversation id, memory id, or previous
    response.  Keeping that boundary in the domain object makes it harder for a
    UI caller to accidentally turn paper selection into a stateful chat.
    """

    source_text: str
    action: str = "explain"
    question: str = ""


def prepare_reading_request(
    source_text: str,
    action: str = "explain",
    question: str = "",
) -> ReadingRequest:
    source = str(source_text).strip()
    kind = str(action).strip().casefold()
    current_question = str(question).strip()
    if not source:
        raise ValueError("没有可处理的划词原文")
    if kind not in READING_ACTIONS:
        raise ValueError(f"不支持的论文划词动作：{action}")
    if kind == "ask" and not current_question:
        raise ValueError("追问需要填写本次问题")
    if kind != "ask":
        current_question = ""
    return ReadingRequest(source[:5000], kind, current_question[:1200])


def reading_card_title(request: ReadingRequest, answer: str = "") -> str:
    """Build a compact, stable title without asking the model for more work."""

    if request.action == "term" and answer.strip():
        first_line = answer.strip().splitlines()[0]
        first_line = re.sub(r"^(?:术语|概念)\s*[：:]\s*", "", first_line).strip()
        if first_line:
            return first_line[:72]
    compact = re.sub(r"\s+", " ", request.source_text).strip()
    return compact[:72] + ("…" if len(compact) > 72 else "")


def reading_bubble_metadata(
    request: ReadingRequest,
    answer: str = "",
    *,
    source_app: str = "",
    saved_card_id: str = "",
    busy: bool = False,
    error: bool = False,
) -> dict[str, Any]:
    """Return QML-friendly metadata shared by every reading bubble action."""

    preview = re.sub(r"\s+", " ", request.source_text).strip()
    return {
        "action": request.action,
        "actionLabel": READING_ACTION_LABELS[request.action],
        "availableActions": list(READING_ACTIONS),
        "sourceText": request.source_text,
        "sourcePreview": preview[:180] + ("…" if len(preview) > 180 else ""),
        "sourceLength": len(request.source_text),
        "sourceApp": source_app,
        "question": request.question,
        "answerLength": len(answer),
        "canSave": bool(answer.strip()) and not busy and not error and not saved_card_id,
        "savedCardId": saved_card_id,
        "autoSaved": request.action == "term" and bool(saved_card_id),
        "ephemeral": True,
    }
