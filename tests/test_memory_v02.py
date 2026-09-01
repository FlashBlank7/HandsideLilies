from __future__ import annotations

import sqlite3

import pytest

from lilies.core.database import Database
from lilies.core.memory import MAX_RECALL_CHARS, MemoryService


def test_fixed_partitions_and_chinese_recall_include_dialogue_pair(tmp_path):
    database = Database(tmp_path / "lilies.db")
    service = MemoryService(database)
    conversation = database.ensure_conversation()
    database.add_message(conversation, "user", "我在做白色纸箱空间的桌面项目")
    database.add_message(conversation, "assistant", "我会把纸箱内部保持洁白。")

    assert [value["partition_id"] for value in service.partitions()] == [
        "identity",
        "relationship",
        "preferences",
        "projects",
        "research",
        "daily",
        "world-lore",
        "unfiled",
    ]
    result = service.recall(
        partition_ids=["unfiled"],
        query="白色纸箱空间",
        time_range="all",
        limit=6,
        turn_id="turn-one",
    )
    assert result["count"] >= 1
    assert "用户：" in result["snippets"][0]["content"]
    assert "莉莉丝：" in result["snippets"][0]["content"]


def test_recall_is_bounded_and_only_twice_per_turn(tmp_path):
    database = Database(tmp_path / "lilies.db")
    service = MemoryService(database)
    conversation = database.ensure_conversation()
    for index in range(12):
        database.add_message(conversation, "user", f"量子纸箱研究条目 {index} " + "很长的说明" * 100)

    first = service.recall(query="量子纸箱研究", limit=99, turn_id="bounded")
    second = service.recall(query="量子纸箱研究", limit=6, turn_id="bounded")
    third = service.recall(query="量子纸箱研究", limit=6, turn_id="bounded")

    assert first["count"] <= 6
    assert sum(len(value["content"]) for value in first["snippets"]) <= MAX_RECALL_CHARS
    assert not second["limitReached"]
    assert third["limitReached"]
    assert third["snippets"] == []


def test_identity_is_pinned_and_forget_keeps_raw_message_by_default(tmp_path):
    database = Database(tmp_path / "lilies.db")
    service = MemoryService(database)
    database.save_memory("称呼", "主人的名字：七秒", enabled=True)
    assert "七秒" in service.pinned_identity_context()

    conversation = database.ensure_conversation()
    message_id = database.add_message(conversation, "user", "记住独一无二的蓝色裂纹")
    fragment = next(
        value
        for value in database.memory_fragments("unfiled")
        if value["source_id"] == message_id
    )
    forgotten = service.forget(fragment["fragment_id"])
    assert forgotten == {"forgotten": True, "sourceDeleted": False}
    assert service.recall(query="蓝色裂纹", turn_id="after-forget")["snippets"] == []
    assert database.search_messages("蓝色裂纹")[0]["message_id"] == message_id


def test_world_lore_separates_canon_from_shared_story(tmp_path):
    database = Database(tmp_path / "lilies.db")
    canon = database.save_memory(
        "既定设定",
        "莉莉丝是唯一可能拥有感情的方舟",
        category="Canon",
        partition_id="world-lore",
        canon_kind="canon",
    )
    fragment = next(
        value
        for value in database.memory_fragments("world-lore")
        if value["source_id"] == canon
    )
    assert fragment["canon_kind"] == "canon"
    with pytest.raises(ValueError):
        database.classify_memory_fragment(
            fragment["fragment_id"],
            partition_id="daily",
            canon_kind="shared",
        )


def test_memory_map_can_move_a_fragment_without_rewriting_its_source(tmp_path):
    database = Database(tmp_path / "lilies.db")
    service = MemoryService(database)
    conversation = database.ensure_conversation()
    message_id = database.add_message(conversation, "user", "我正在整理一篇陶瓷裂纹论文")
    fragment = next(
        value for value in database.memory_fragments("unfiled") if value["source_id"] == message_id
    )

    assert service.move(fragment["fragment_id"], "research")
    moved = next(
        value
        for value in database.memory_fragments("research")
        if value["fragment_id"] == fragment["fragment_id"]
    )
    assert moved["content"] == "我正在整理一篇陶瓷裂纹论文"
    assert database.search_messages("陶瓷裂纹")[0]["message_id"] == message_id


def test_archival_fragment_and_partition_summary_update_are_atomic(tmp_path):
    database = Database(tmp_path / "atomic-archive.db")
    service = MemoryService(database)
    fragment_id = database.save_memory_fragment(
        source_type="companion-message",
        source_id="atomic",
        content="An atomic memory candidate.",
    )
    pending = database.memory_fragment(fragment_id)
    assert pending is not None
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_archive_summary BEFORE UPDATE ON memory_partitions
               WHEN NEW.partition_id='daily'
               BEGIN SELECT RAISE(ABORT, 'synthetic summary failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic summary failure"):
        service.apply_archival(
            fragment_id,
            {
                "partitionId": "daily",
                "summary": "must roll back",
                "keywords": [],
                "entities": [],
                "importance": 0.5,
                "canonKind": "none",
            },
            expected_partition_id="unfiled",
            expected_updated_at=str(pending["updated_at"]),
            fallback_partition_id="unfiled",
        )

    unchanged = database.memory_fragment(fragment_id)
    assert unchanged is not None
    assert unchanged["partition_id"] == "unfiled"
    assert unchanged["summary"] == ""


def test_recovery_memory_database_does_not_create_a_colon_memory_file():
    database = Database(":memory:")
    conversation = database.ensure_conversation()
    database.add_message(conversation, "user", "受限恢复仍可保留本次会话")
    assert database.search_messages("受限恢复")
    assert database.integrity_check() == "ok"
    database.close()
