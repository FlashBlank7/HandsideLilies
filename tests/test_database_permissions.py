from __future__ import annotations

import pytest

from lilies.core.database import Database
from lilies.core.permissions import PermissionBroker, PermissionMode, Risk


def test_memory_cards_are_reviewable(tmp_path):
    database = Database(tmp_path / "lilies.db")
    memory_id = database.save_memory("称呼", "用户希望被称作盒子的继承者")
    assert database.memory_cards(enabled_only=True)[0]["memory_id"] == memory_id
    database.save_memory("称呼", "已修改", memory_id=memory_id, enabled=False)
    assert database.memory_cards(enabled_only=True) == []
    database.delete_memory(memory_id)
    assert database.memory_cards() == []


def test_chat_history_is_local_and_searchable(tmp_path):
    database = Database(tmp_path / "lilies.db")
    conversation = database.ensure_conversation()
    database.add_message(conversation, "user", "记住纸箱里的白色空间")
    database.add_message(conversation, "assistant", "我会记得。")
    results = database.search_messages("白色空间")
    assert len(results) == 1
    assert results[0]["conversation_id"] == conversation
    assert database.search_messages("不存在的句子") == []


def test_permission_modes(tmp_path):
    database = Database(tmp_path / "lilies.db")
    broker = PermissionBroker(database)
    assert broker.check("theme", "status", Risk.READ).allowed
    assert broker.check("theme", "activate", Risk.MUTATE).needs_confirmation
    broker.set_mode(PermissionMode.CAUTIOUS)
    assert broker.check("app-launcher", "open", Risk.LAUNCH).needs_confirmation
    broker.set_mode(PermissionMode.TRUSTED)
    database.set_setting("trusted_allowlist", ["theme.activate"])
    assert broker.check("theme", "activate", Risk.MUTATE).allowed
    assert broker.check("files", "delete", Risk.DESTRUCTIVE).needs_confirmation


def test_desktop_layout_does_not_change_path(tmp_path):
    database = Database(tmp_path / "lilies.db")
    source = tmp_path / "Example.txt"
    source.write_text("untouched", "utf-8")
    database.upsert_desktop_item({"name": "Example", "path": str(source), "source": "desktop", "kind": "file"})
    item = database.desktop_items()[0]
    database.update_desktop_layout(item["item_id"], x=800, y=500, group_name="测试")
    assert source.read_text("utf-8") == "untouched"
    assert database.desktop_items()[0]["path"] == str(source)


def test_desktop_layout_schemes_are_independent(tmp_path):
    database = Database(tmp_path / "lilies.db")
    source = tmp_path / "Example.txt"
    source.write_text("untouched", "utf-8")
    database.upsert_desktop_item({"name": "Example", "path": str(source), "source": "desktop", "kind": "file"})
    item_id = database.desktop_items()[0]["item_id"]
    database.update_desktop_layout(item_id, x=120, y=220, group_name="工作")
    second = database.create_desktop_layout("专注")
    database.update_desktop_layout(item_id, x=720, y=420, hidden=1)
    assert database.desktop_items() == []
    assert database.activate_desktop_layout("default")
    original = database.desktop_items()[0]
    assert (original["x"], original["y"], original["group_name"], original["hidden"]) == (120, 220, "工作", 0)
    assert source.read_text("utf-8") == "untouched"
    assert database.activate_desktop_layout(second)
    assert database.desktop_items(include_hidden=True)[0]["hidden"] == 1
