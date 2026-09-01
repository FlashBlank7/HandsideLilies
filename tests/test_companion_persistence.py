from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lilies.core.companion import ContentCategory
from lilies.core.content import CacheEntry, ContentItem
from lilies.core.content_cache import DatabaseContentCache
from lilies.core.database import Database
from lilies.core.memory import MemoryService


def test_proactive_session_reply_is_persisted_and_indexed(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    bubble = {
        "id": "bubble-1",
        "category": "科普",
        "summary": "一条很短的说明",
        "detail": "更详细的解释",
        "source": None,
        "sceneLabel": "论文阅读",
        "createdAt": datetime.now(UTC).isoformat(),
    }
    database.save_proactive_session(session_id="session-1", bubble=bubble)
    database.add_proactive_message("session-1", "user", "这个和我的项目有关")
    database.add_proactive_message("session-1", "assistant", "我把关联点收好了")

    saved = database.proactive_session("session-1")

    assert saved is not None
    assert saved["summary"] == bubble["summary"]
    assert [value["role"] for value in saved["messages"]] == ["user", "assistant"]
    assert any(
        value["source_type"] == "companion-message"
        for value in database.memory_fragments("unfiled")
    )

    fragment = next(
        value
        for value in database.memory_fragments("unfiled")
        if value["source_type"] == "companion-message" and value["role"] == "user"
    )
    service = MemoryService(database)
    assert service.forget(fragment["fragment_id"], False)["sourceDeleted"] is False
    assert database.proactive_session("session-1") is not None
    assert service.forget(fragment["fragment_id"], True)["sourceDeleted"] is True
    assert database.proactive_session("session-1") is None
    assert not any(
        value["source_type"] == "companion-message"
        for value in database.memory_fragments(include_forgotten=True)
    )


def test_recent_proactive_prose_survives_restart_and_keeps_summary_api(tmp_path) -> None:
    database_path = tmp_path / "lilies.db"
    database = Database(database_path)
    records = [
        ("session-1", "bubble-1", "First summary", "First generated detail"),
        ("session-2", "bubble-2", "Second summary", "Second generated detail"),
    ]
    for session_id, bubble_id, summary, detail in records:
        database.save_proactive_session(
            session_id=session_id,
            bubble={
                "id": bubble_id,
                "category": "philosophy",
                "summary": summary,
                "detail": detail,
                "createdAt": datetime.now(UTC).isoformat(),
            },
        )

    expected_summaries = [record[2] for record in records]
    expected_prose = [
        {"summary": record[2], "detail": record[3]} for record in records
    ]
    assert database.recent_proactive_summaries(12) == expected_summaries
    assert database.recent_proactive_prose(12) == expected_prose

    restarted = Database(database_path)
    assert restarted.recent_proactive_summaries(12) == expected_summaries
    assert restarted.recent_proactive_prose(12) == expected_prose


def test_proactive_generation_receipt_is_linked_and_strictly_content_safe(
    tmp_path,
) -> None:
    database = Database(tmp_path / "lilies.db")
    database.save_proactive_session(
        session_id="session-image",
        bubble={
            "id": "bubble-image",
            "category": "哲思",
            "summary": "一块留白把边界照得更清楚。",
            "detail": "边界依赖空白才可见。",
            "createdAt": datetime.now(UTC).isoformat(),
        },
    )
    database.save_proactive_generation(
        "session-image",
        {
            "contextType": "active-window-image",
            "imageGrounded": True,
            "model": "gpt-5.6-terra",
            "evidenceConfidence": "HIGH",
            # These fields must never cross the persistence allowlist.
            "windowTitle": "private paper title",
            "hwnd": 42,
            "imagePath": "F:/private/capture.png",
            "pixels": "base64-private",
            "anchor": "private captured text",
        },
    )

    saved = database.proactive_session("session-image")
    assert saved is not None
    assert saved["generation"] == {
        "schemaVersion": 1,
        "contextType": "active-window-image",
        "imageGrounded": True,
        "model": "gpt-5.6-terra",
        "evidenceConfidence": "high",
    }
    with database.connect() as db:
        raw = str(
            db.execute(
                "SELECT generation_json FROM proactive_sessions "
                "WHERE session_id='session-image'"
            ).fetchone()["generation_json"]
        )
    assert "private" not in raw
    assert "capture.png" not in raw
    assert "base64" not in raw

    database.save_proactive_session(
        session_id="session-untrusted-model",
        bubble={
            "id": "bubble-untrusted-model",
            "category": "哲思",
            "summary": "另一条合成测试。",
            "createdAt": datetime.now(UTC).isoformat(),
        },
        generation={
            "contextType": "active-window-image",
            "imageGrounded": True,
            "model": "terra-test\nmodel",
            "evidenceConfidence": "high",
        },
    )
    untrusted = database.proactive_session("session-untrusted-model")
    assert untrusted is not None
    assert untrusted["generation"]["model"] == ""

    with database.connect() as db:
        db.execute(
            "UPDATE proactive_sessions SET generation_json='[]' "
            "WHERE session_id='session-untrusted-model'"
        )
    malformed = database.proactive_session("session-untrusted-model")
    assert malformed is not None
    assert malformed["generation"] == {}

    database.save_proactive_generation(
        "session-image",
        {
            "contextType": "active-window-image",
            "imageGrounded": True,
            "model": "gpt-5.6-terra",
            "evidenceConfidence": "high",
        },
    )
    with pytest.raises(RuntimeError, match="不可覆盖"):
        database.save_proactive_generation(
            "session-image",
            {
                "contextType": "application-signal",
                "imageGrounded": False,
                "model": "gpt-5.6-luna",
                "evidenceConfidence": "none",
            },
        )

    database.save_proactive_session(
        session_id="session-nonboolean-grounding",
        bubble={
            "id": "bubble-nonboolean-grounding",
            "category": "哲思",
            "summary": "布尔类型也必须经过校验。",
            "createdAt": datetime.now(UTC).isoformat(),
        },
        generation={
            "contextType": "active-window-image",
            "imageGrounded": "false",
            "model": "gpt-5.6-luna",
            "evidenceConfidence": "high",
        },
    )
    nonboolean = database.proactive_session("session-nonboolean-grounding")
    assert nonboolean is not None
    assert nonboolean["generation"]["imageGrounded"] is False
    assert nonboolean["generation"]["evidenceConfidence"] == "none"


def test_content_cache_round_trip_keeps_metadata_only(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    cache = DatabaseContentCache(database)
    now = datetime.now(UTC)
    item = ContentItem.create(
        category=ContentCategory.RESEARCH,
        title="A result",
        summary="Only the short abstract metadata is cached.",
        source="arXiv",
        published_at=now,
        url="https://arxiv.org/abs/1234.5678",
        topics=("ai",),
        stable_id="paper-1",
        fetched_at=now,
    )

    cache.put("query-key", CacheEntry((item,), now))
    restored = cache.get("query-key")

    assert restored is not None
    assert restored.items[0].id == "paper-1"
    assert restored.items[0].source == "arXiv"
    assert restored.items[0].published_at == now


def test_desktop_peek_log_records_only_result_metadata(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    event_id = database.log_desktop_peek("toggle", {"minimized": 3, "active": True})
    with database.connect() as connection:
        row = connection.execute(
            "SELECT action,result_json FROM desktop_peek_log WHERE event_id=?", (event_id,)
        ).fetchone()
    assert row["action"] == "toggle"
    assert "minimized" in row["result_json"]
