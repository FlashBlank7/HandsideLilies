from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from lilies.companion_controller import (
    _UNREAD_REDELIVERY_LIMIT,
    _UNREAD_RETENTION_SECONDS,
    CompanionController,
)
from lilies.core.companion import ContentCategory
from lilies.core.companion_runtime import CompanionRuntime
from lilies.core.database import Database
from lilies.core.memory import MemoryService


def _app() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _controller(database: Database, root: Path) -> CompanionController:
    _app()
    return CompanionController(
        database,
        root,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )


def _seed_session(database: Database, *, session_id: str = "retained-session") -> dict:
    created = datetime.now(UTC)
    bubble = {
        "id": f"{session_id}-bubble",
        "category": ContentCategory.PHILOSOPHY.value,
        "summary": "A retained synthetic summary.",
        "detail": "A retained synthetic detail.",
        "sceneLabel": "测试场景",
        "createdAt": created.isoformat(),
    }
    database.save_proactive_session(session_id=session_id, bubble=bubble)
    return bubble


def _seed_unread(
    database: Database,
    bubble: dict,
    *,
    session_id: str = "retained-session",
    unread_since: datetime | None = None,
    redelivery_count: int = 0,
) -> None:
    when = unread_since or datetime.now(UTC)
    database.set_setting(
        "companion_delivery_status",
        {
            "schemaVersion": 2,
            "sessionId": session_id,
            "bubbleId": bubble["id"],
            "state": "unread",
            "reason": "expired-without-interaction",
            "generatedAt": bubble["createdAt"],
            "presentedAt": bubble["createdAt"],
            "expiresAt": "",
            "unread": True,
            "unreadSince": when.isoformat(),
            "redeliveryCount": redelivery_count,
            "lastRedeliveryAt": "",
        },
    )


def test_legacy_capture_unavailable_is_migrated_and_backfilled(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting(
        "companion_last_capture_status",
        {
            "outcome": "failed",
            "reason": "capture-unavailable",
            "at": "2026-01-02T03:04:05+00:00",
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["lastCaptureReason"] == "legacy-failure-unknown"
        assert status["lastCaptureReasonLabel"] == "旧版截图失败记录（具体阶段未知）"
        assert status["captureAttempted"] is True
        assert status["imageSubmitted"] is False
        assert status["imageResponseAccepted"] is False
        persisted = database.get_setting("companion_last_capture_status", {})
        assert persisted["reason"] == "legacy-failure-unknown"
        assert persisted["captureAttempted"] is True
        assert persisted["imageSubmitted"] is False
        assert persisted["imageResponseAccepted"] is False
    finally:
        controller.shutdown()


def test_unknown_legacy_capture_failure_discards_private_reason(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting(
        "companion_last_capture_status",
        {
            "outcome": "failed",
            "reason": r"decoder failed at F:\private\paper.png",
            "captureAttempted": None,
            "imageSubmitted": None,
            "imageResponseAccepted": None,
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["lastCaptureReason"] == "legacy-failure-unknown"
        assert status["lastCaptureReasonLabel"] == "旧版截图失败记录（具体阶段未知）"
        persisted = repr(database.get_setting("companion_last_capture_status", {}))
        assert "private" not in persisted
        assert "paper.png" not in persisted
    finally:
        controller.shutdown()


def test_contradictory_capture_receipt_is_normalized_without_inventing_evidence(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting(
        "companion_last_capture_status",
        {
            "schemaVersion": 3,
            "outcome": "failed",
            "reason": "image-result-invalid",
            "at": "2026-01-02T03:04:05+00:00",
            "captureAttempted": False,
            "imageSubmitted": True,
            "imageResponseAccepted": True,
            "pixelsUsed": True,
            "model": "untrusted-model-name",
            "evidenceConfidence": "high",
            "presentationOutcome": "shown",
            "presentationReason": "window-exposed",
            "presentationAt": "2026-01-02T03:04:06+00:00",
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["captureAttempted"] is True
        assert status["imageSubmitted"] is True
        assert status["imageResponseAccepted"] is False
        assert status["lastCapturePixelsUsed"] is False
        assert status["lastCaptureModel"] == ""
        assert status["lastCaptureEvidenceConfidence"] == "none"
        assert status["lastCapturePresentationOutcome"] == "quiet"
        assert status["lastCapturePresentationReason"] == "quality-rejected"
        persisted = database.get_setting("companion_last_capture_status", {})
        assert persisted["imageResponseAccepted"] is False
        assert persisted["pixelsUsed"] is False
        assert persisted["model"] == ""
        assert persisted["evidenceConfidence"] == "none"
        assert persisted["presentationOutcome"] == "quiet"
        assert persisted["presentationReason"] == "quality-rejected"
    finally:
        controller.shutdown()


def test_pending_capture_presentation_is_resolved_on_restart(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting(
        "companion_last_capture_status",
        {
            "schemaVersion": 3,
            "outcome": "used",
            "reason": "image-model-completed",
            "at": "2026-01-02T03:04:05+00:00",
            "captureAttempted": True,
            "imageSubmitted": True,
            "imageResponseAccepted": True,
            "pixelsUsed": True,
            "model": "gpt-5.6-luna",
            "evidenceConfidence": "medium",
            "presentationOutcome": "pending",
            "presentationReason": "awaiting-presentation",
            "presentationAt": "2026-01-02T03:04:06+00:00",
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePixelsUsed"] is True
        assert status["lastCapturePresentationOutcome"] == "cancelled"
        assert status["lastCapturePresentationReason"] == "generation-cancelled"
    finally:
        controller.shutdown()


def test_pending_capture_restart_links_only_to_matching_unread_delivery(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    matched = _seed_session(database, session_id="capture-matched")
    _seed_unread(database, matched, session_id="capture-matched")
    old_presentation_at = "2026-01-02T03:04:06+00:00"
    database.set_setting(
        "companion_last_capture_status",
        {
            "schemaVersion": 4,
            "outcome": "used",
            "reason": "image-model-completed",
            "at": "2026-01-02T03:04:05+00:00",
            "captureAttempted": True,
            "imageSubmitted": True,
            "imageResponseAccepted": True,
            "pixelsUsed": True,
            "model": "gpt-5.6-luna",
            "evidenceConfidence": "medium",
            "presentationOutcome": "pending",
            "presentationReason": "awaiting-presentation",
            "presentationAt": old_presentation_at,
            "sessionId": "capture-matched",
            "bubbleId": matched["id"],
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["lastCaptureOutcome"] == "used"
        assert status["lastCapturePresentationOutcome"] == "unread"
        assert status["lastCapturePresentationReason"] == (
            "process-restarted-before-presentation"
        )
        assert status["lastCapturePresentationAt"] != old_presentation_at
    finally:
        controller.shutdown()


def test_old_unread_delivery_cannot_claim_a_different_pending_capture(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    old_bubble = _seed_session(database, session_id="old-unread")
    _seed_unread(database, old_bubble, session_id="old-unread")
    new_bubble = _seed_session(database, session_id="new-capture")
    database.set_setting(
        "companion_last_capture_status",
        {
            "schemaVersion": 4,
            "outcome": "used",
            "reason": "image-model-completed",
            "captureAttempted": True,
            "imageSubmitted": True,
            "imageResponseAccepted": True,
            "pixelsUsed": True,
            "model": "gpt-5.6-terra",
            "evidenceConfidence": "high",
            "presentationOutcome": "pending",
            "presentationReason": "awaiting-presentation",
            "sessionId": "new-capture",
            "bubbleId": new_bubble["id"],
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert controller.deliveryStatus["unreadCount"] == 1
        assert status["lastCapturePixelsUsed"] is True
        assert status["lastCapturePresentationOutcome"] == "cancelled"
        assert status["lastCapturePresentationReason"] == "generation-cancelled"
    finally:
        controller.shutdown()


def test_shown_capture_receipt_stays_shown_across_restart(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting(
        "companion_last_capture_status",
        {
            "schemaVersion": 4,
            "outcome": "used",
            "reason": "image-model-completed",
            "captureAttempted": True,
            "imageSubmitted": True,
            "imageResponseAccepted": True,
            "pixelsUsed": True,
            "model": "gpt-5.6-luna",
            "evidenceConfidence": "high",
            "presentationOutcome": "shown",
            "presentationReason": "window-exposed",
            "presentationAt": "2026-01-02T03:04:06+00:00",
            "sessionId": "shown-session",
            "bubbleId": "shown-bubble",
        },
    )
    controller = _controller(database, tmp_path)
    try:
        status = controller.activityStatus
        assert status["lastCapturePresentationOutcome"] == "shown"
        assert status["lastCapturePresentationReason"] == "window-exposed"
        assert status["lastCapturePresentationAt"] == (
            "2026-01-02T03:04:06+00:00"
        )
    finally:
        controller.shutdown()


def test_delivery_writer_rejects_arbitrary_reason_without_persisting_it(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    controller = _controller(database, tmp_path)
    private_reason = r"unexpected F:\private\paper.png"
    try:
        before = dict(controller.deliveryStatus)
        with pytest.raises(ValueError, match="unknown companion delivery reason"):
            controller._set_delivery_state("idle", private_reason)

        assert controller.deliveryStatus == before
        persisted = repr(database.get_setting("companion_delivery_status", {}))
        assert private_reason not in persisted
        assert "paper.png" not in persisted
    finally:
        controller.shutdown()


def test_malformed_delivery_journal_pairs_state_and_unread_on_restore(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).isoformat()
    cases = (
        ("unread", False, "unread", True),
        ("dismissed", True, "dismissed", False),
    )
    for index, (stored_state, stored_unread, expected_state, expected_unread) in enumerate(
        cases
    ):
        database = Database(tmp_path / f"paired-{index}.db")
        session_id = f"paired-session-{index}"
        bubble = _seed_session(database, session_id=session_id)
        database.set_setting(
            "companion_delivery_status",
            {
                "schemaVersion": 1,
                "sessionId": session_id,
                "bubbleId": bubble["id"],
                "state": stored_state,
                "reason": "legacy-mismatch",
                "generatedAt": bubble["createdAt"],
                "presentedAt": bubble["createdAt"],
                "unread": stored_unread,
                "unreadSince": now,
                "redeliveryCount": 1,
                "lastRedeliveryAt": now,
            },
        )

        controller = _controller(database, tmp_path)
        try:
            assert controller.deliveryStatus["state"] == expected_state
            assert controller.deliveryStatus["unreadCount"] == int(expected_unread)
            persisted = database.get_setting("companion_delivery_status", {})
            assert persisted["state"] == expected_state
            assert persisted["unread"] is expected_unread
            assert (persisted["state"] == "unread") is persisted["unread"]
            assert bool(persisted["unreadSince"]) is expected_unread
            assert persisted["redeliveryCount"] == (1 if expected_unread else 0)
            assert bool(persisted["lastRedeliveryAt"]) is expected_unread
            assert persisted["reason"] == "legacy-reason-unknown"
            assert "legacy-mismatch" not in repr(persisted)
        finally:
            controller.shutdown()


def test_unread_delivery_without_identifiers_is_archived_on_restore(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    now = datetime.now(UTC).isoformat()
    database.set_setting(
        "companion_delivery_status",
        {
            "schemaVersion": 1,
            "sessionId": "",
            "bubbleId": "",
            "state": "unread",
            "reason": "legacy-missing-identifiers",
            "generatedAt": now,
            "presentedAt": now,
            "unread": True,
            "unreadSince": now,
            "redeliveryCount": 1,
            "lastRedeliveryAt": now,
        },
    )

    controller = _controller(database, tmp_path)
    try:
        assert controller.deliveryStatus["state"] == "expired"
        assert controller.deliveryStatus["reason"] == "unread-session-missing"
        assert controller.deliveryStatus["unreadCount"] == 0
        persisted = database.get_setting("companion_delivery_status", {})
        assert persisted["state"] == "expired"
        assert persisted["reason"] == "unread-session-missing"
        assert persisted["unread"] is False
        assert persisted["unreadSince"] == ""
        assert persisted["redeliveryCount"] == 0
        assert persisted["lastRedeliveryAt"] == ""
    finally:
        controller.shutdown()


def test_missing_unread_session_self_heals_without_blocking_forever(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    now = datetime.now(UTC).isoformat()
    database.set_setting(
        "companion_delivery_status",
        {
            "schemaVersion": 1,
            "sessionId": "missing-session",
            "bubbleId": "missing-bubble",
            "state": "unread",
            "reason": "presentation-ack-timeout",
            "generatedAt": now,
            "presentedAt": now,
            "unread": True,
        },
    )
    controller = _controller(database, tmp_path)
    try:
        assert controller.deliveryStatus["unreadCount"] == 0
        assert controller.deliveryStatus["state"] == "expired"
        assert controller.deliveryStatus["reason"] == "unread-session-missing"
        assert controller._prune_unread_delivery() is False
    finally:
        controller.shutdown()


def test_unread_retention_expiry_unblocks_but_keeps_history(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    bubble = _seed_session(database)
    _seed_unread(
        database,
        bubble,
        unread_since=datetime.now(UTC)
        - timedelta(seconds=_UNREAD_RETENTION_SECONDS + 1),
    )
    controller = _controller(database, tmp_path)
    try:
        assert controller.deliveryStatus["unreadCount"] == 0
        assert controller.deliveryStatus["reason"] == "unread-retention-expired"
        assert database.proactive_session("retained-session") is not None
    finally:
        controller.shutdown()


def test_only_automatic_reopen_consumes_budget_and_explicit_mark_read_keeps_history(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    bubble = _seed_session(database)
    _seed_unread(database, bubble)
    controller = _controller(database, tmp_path)
    try:
        assert controller.reopenUnread() is True
        assert controller._delivery_record["redeliveryCount"] == 0
        controller._clear_bubble(
            reason="expired-without-interaction", preserve_unread=True
        )

        assert controller._reopen_unread(automatic=True) is True
        assert controller._delivery_record["redeliveryCount"] == 1
        controller._clear_bubble(
            reason="expired-without-interaction", preserve_unread=True
        )

        assert controller._reopen_unread(automatic=True) is True
        assert controller._delivery_record["redeliveryCount"] == _UNREAD_REDELIVERY_LIMIT
        controller._clear_bubble(
            reason="expired-without-interaction", preserve_unread=True
        )
        assert controller._prune_unread_delivery() is True
        assert controller.deliveryStatus["unreadCount"] == 0
        assert controller.deliveryStatus["reason"] == "unread-redelivery-exhausted"
        assert database.proactive_session("retained-session") is not None

        _seed_unread(database, bubble)
    finally:
        controller.shutdown()

    marked = _controller(database, tmp_path)
    try:
        assert marked.markUnreadRead() is True
        assert marked.deliveryStatus["unreadCount"] == 0
        assert marked.deliveryStatus["reason"] == "explicit-mark-read"
        assert database.proactive_session("retained-session") is not None
    finally:
        marked.shutdown()


class _FakeClient:
    def __init__(self, model: str, *, error: str = "") -> None:
        self.model = model
        self.error = error
        self.ready = True
        self.calls = 0

    def complete(self, _prompt: str, **_kwargs) -> str:
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return (
            '{"anchor":"","evidenceConfidence":"none",'
            '"summary":"A distinct model-generated observation.",'
            '"detail":"A distinct model-generated detail."}'
        )

    def abort(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_proactive_circuits_are_separate_by_model_and_modality(tmp_path: Path) -> None:
    database = Database(tmp_path / "lilies.db")
    runtime = CompanionRuntime(tmp_path, MemoryService(database))
    luna = _FakeClient("luna-test")
    terra = _FakeClient("terra-test", error="synthetic vision failure")
    runtime.luna = luna
    runtime.terra = terra
    runtime.image_model = "terra"
    runtime.modality_status = {
        "checked": True,
        "luna": ["text"],
        "terra": ["text", "image"],
        "imageModel": "terra",
        "error": "",
    }
    try:
        failed_image = runtime.generate(
            category=ContentCategory.PHILOSOPHY,
            scene_label="论文阅读",
            image_path=tmp_path / "synthetic.png",
        )
        assert failed_image["degraded"] is True
        assert failed_image["circuit"] == "terra-test:image"
        assert terra.calls == 1

        text_result = runtime.generate(
            category=ContentCategory.SCIENCE,
            scene_label="论文阅读",
        )
        assert text_result["degraded"] is False
        assert text_result["circuit"] == "luna-test:text"
        assert luna.calls == 1

        second_image = runtime.generate(
            category=ContentCategory.PHILOSOPHY,
            scene_label="论文阅读",
            image_path=tmp_path / "synthetic.png",
        )
        assert second_image["degraded"] is True
        assert second_image["retryAfterSeconds"] > 0
        assert terra.calls == 1
    finally:
        runtime.shutdown()


def test_subjective_failure_stays_quiet_and_does_not_spend_success_gate(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    controller = _controller(database, tmp_path)
    before_count = controller.engine.gate.state(datetime.now(UTC))["countToday"]
    before_cooldown = controller.activity.status()["cooldownRemainingSeconds"]
    payload = {
        "result": {
            "summary": "",
            "detail": "",
            "model": "subscription-test",
            "contextType": "application-signal",
            "skip": True,
            "skipReason": "subjective-generation-failed",
            "degraded": True,
            "retryAfterSeconds": 30,
        },
        "category": ContentCategory.PHILOSOPHY,
        "sceneLabel": "论文阅读",
        "generationToken": 0,
    }
    try:
        start = time.monotonic()
        controller._busy = True
        controller._accept_generation({**payload, "force": False})
        assert controller.bubble == {}
        assert controller.engine.gate.state(datetime.now(UTC))["countToday"] == before_count
        assert controller.activity.status()["cooldownRemainingSeconds"] == before_cooldown
        assert start + 29 <= controller._generation_attempt_not_before <= start + 32

        controller._busy = True
        controller._accept_generation({**payload, "force": True})
        assert controller.bubble == {}
        assert "没有使用固定文案" in controller.activityStatus["requestFeedback"]
        assert controller.engine.gate.state(datetime.now(UTC))["countToday"] == before_count
        assert controller.activity.status()["cooldownRemainingSeconds"] == before_cooldown
    finally:
        controller.shutdown()
