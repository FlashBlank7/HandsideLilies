from __future__ import annotations

from datetime import UTC, datetime
import time

import pytest
from PySide6.QtGui import QGuiApplication

from lilies.companion_controller import CompanionController
from lilies.core.awareness import bubble_evidence, capture_evidence
from lilies.core.database import Database
from lilies.core.selection import SelectionService
from lilies.core.companion import ContentCategory


def wait(app, predicate):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        app.processEvents()
        if predicate(): return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("payload,kind", [
    ({}, "none"),
    ({"contextType": "bogus", "summary": "I am watching your screen"}, "unknown"),
    ({"contextType": "application-signal"}, "application"),
    ({"contextType": "application-signal", "summary": "我看到你在第三页"}, "application"),
    ({"contextType": "active-window-image", "imageGrounded": False}, "unknown"),
    ({"contextType": "active-window-image", "imageGrounded": True}, "window-image"),
    ({"contextType": "retained-image-anchor"}, "image-anchor"),
    ({"contextType": "active-window-image", "imageGrounded": True, "replyBasis": "dialogue"}, "dialogue"),
    ({"contextType": "active-window-image", "imageGrounded": True, "replyBasis": "image-anchor"}, "image-anchor"),
    ({"contextType": "application-signal", "source": {"name": "Journal", "url": "https://example.org"}}, "source"),
])
def test_receipt_uses_structured_evidence_never_prose(payload, kind):
    assert bubble_evidence(payload)["kind"] == kind


def test_replayed_bubble_never_claims_fresh_observation():
    value = bubble_evidence({"contextType": "active-window-image", "imageGrounded": True,
        "restoredUnread": True, "createdAt": "2026-10-03T00:00:00Z"})
    assert value["kind"] == "history" and value["originalKind"] == "window-image"
    assert "没有重新观察" in value["detail"]
    assert value["at"] == "2026-10-03T00:00:00Z"
    assert bubble_evidence({"restoredUnread": True})["originalKind"] == "unknown"


@pytest.mark.parametrize("status,kind", [
    ({"outcome": "never"}, "none"),
    ({"captureAttempted": True}, "none"),
    ({"pixelsUsed": True}, "none"),
    ({"imageSubmitted": True, "outcome": "cancelled"}, "image-submitted"),
    ({"imageSubmitted": True, "imageResponseAccepted": False}, "image-submitted"),
    ({"imageSubmitted": True, "imageResponseAccepted": True, "pixelsUsed": True}, "window-image"),
])
def test_capture_submission_not_confused_with_permission_or_local_capture(status, kind):
    assert capture_evidence(status)["kind"] == kind


def controller(tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    database = Database(tmp_path / "lilies.db")
    value = CompanionController(database, tmp_path, active=False, status_sink=lambda text: None,
        move_to_box=lambda data: None, foreground_provider=lambda: 0)
    return app, database, value


@pytest.mark.parametrize("generation,expected", [
    ({}, "unknown"),
    ({"contextType": "application-signal"}, "application"),
    ({"contextType": "active-window-image", "imageGrounded": True, "evidenceConfidence": "high"}, "window-image"),
    ({"contextType": "retained-image-anchor"}, "image-anchor"),
])
def test_unread_rehydrate_uses_saved_receipt_without_rehydrating_pixels_or_anchors(tmp_path, generation, expected):
    _, database, value = controller(tmp_path)
    try:
        database.save_proactive_session(session_id="session", bubble={"id": "b", "category": ContentCategory.SCIENCE.value,
            "summary": "旧内容", "detail": "之前生成的说明", "createdAt": datetime.now(UTC).isoformat()}, generation=generation)
        value._delivery_record = {"sessionId": "session", "bubbleId": "b"}
        assert value._load_unread_bubble()
        assert value.bubble["awareness"]["kind"] == "history"
        assert value.bubble["awareness"]["originalKind"] == expected
        assert not value.bubble["hasCapture"] and not value._bubble_evidence
    finally: value.shutdown()


def test_reply_replaces_basis_but_failed_and_stale_replies_keep_original(tmp_path):
    _, _, value = controller(tmp_path)
    try:
        bubble = value.engine.emit(category=ContentCategory.SCIENCE, summary="当时的说明", detail="只供测试", force=True)
        assert bubble
        value._bubble_object = bubble
        value._bubble = {**bubble.to_mapping(), "contextType": "active-window-image", "imageGrounded": True}
        value._active_reply_serial = 2
        value._accept_reply({"replySerial": 1, "bubbleId": bubble.id, "answer": "old", "replyBasis": "dialogue"})
        assert value.bubble["awareness"]["kind"] == "window-image"
        value._accept_reply({"replySerial": 2, "bubbleId": bubble.id, "error": "failed"})
        assert value.bubble["awareness"]["kind"] == "window-image"
        value._active_reply_serial = 3
        value._accept_reply({"replySerial": 3, "bubbleId": bubble.id, "answer": "沿用刚才那条文字线索。", "replyBasis": "image-anchor"})
        assert value.bubble["awareness"]["kind"] == "image-anchor"
        assert value.bubble["contextType"] == "active-window-image"  # original provenance not overwritten
    finally: value.shutdown()


class TextOnlyModel:
    ready = True
    def __init__(self): self.calls = []
    def explain(self, text):
        self.calls.append(text)
        return "这里比较的是方差，不是均值。"
    def reading_action(self, source, action, question):
        self.calls.append((source, action, question))
        return "样本量会影响方差估计的稳定性。"
    def stop(self): pass


def test_manual_text_does_not_enable_monitoring_or_attach_paper_context(tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    database = Database(tmp_path / "selection.db")
    database.set_setting("selection_monitor_enabled", False)
    selection = SelectionService(database, active=False)
    model = TextOnlyModel()
    selection._subscription = model
    selection.paper_context_provider = lambda: (_ for _ in ()).throw(AssertionError("manual paste must not inherit the current paper"))
    try:
        selection.explain_provided_text("同均值，不同方差。", 10, 20)
        assert wait(app, lambda: not selection.bubble["busy"])
        assert model.calls == ["同均值，不同方差。"]
        assert selection.bubble["awareness"]["kind"] == "selected-text"
        assert selection.bubble["sourceApp"] == "用户提供"
        assert selection.bubble["paperTitle"] == ""
        assert not database.get_setting("selection_monitor_enabled", True)
        assert not database.reading_cards()
        selection.request_action("ask", "这和样本量有关吗？")
        assert wait(app, lambda: not selection.bubble["busy"])
        assert model.calls[-1] == ("同均值，不同方差。", "ask", "这和样本量有关吗？")
        assert selection.bubble["sourceApp"] == "用户提供" and not selection.bubble["paperTitle"]
        selection._bubble["busy"] = True
        with pytest.raises(ValueError): selection.explain_provided_text("别抢占上一段", 10, 20)
        selection._bubble["busy"] = False
        with pytest.raises(ValueError): selection.explain_provided_text("字" * 5001, 10, 20)
    finally: selection.shutdown()
