from __future__ import annotations

import threading

import pytest
from PySide6.QtTest import QTest

from lilies.chat_timeline import ChatTimeline
from lilies.core.database import Database
from lilies.core.model import ChatService


def wait_finished(chat):
    for _ in range(300):
        if not chat._working:
            return
        QTest.qWait(10)
    raise AssertionError("chat worker did not finish")


def test_history_anchor_and_pages_cover_long_chat_without_duplicates(tmp_path):
    database = Database(tmp_path / "chat.db")
    conversation = database.ensure_conversation()
    with database.connection_session():
        ids = [database.add_message(conversation, "user" if i % 2 == 0 else "assistant", f"第 {i} 句") for i in range(235)]
    page = database.conversation_page(conversation, anchor=ids[106])
    assert len(page["messages"]) == 80
    assert page["messages"][page["anchorIndex"]]["message_id"] == ids[106]
    assert page["hasOlder"] and page["hasNewer"]
    latest = database.conversation_page(conversation)
    collected = [m["message_id"] for m in latest["messages"]]
    while latest["hasOlder"]:
        latest = database.conversation_page(conversation, before=latest["before"])
        collected = [m["message_id"] for m in latest["messages"]] + collected
    assert collected == ids
    collected = [m["message_id"] for m in latest["messages"]]
    while latest["hasNewer"]:
        latest = database.conversation_page(conversation, after=latest["after"])
        collected += [m["message_id"] for m in latest["messages"]]
    assert collected == ids
    other = database.ensure_conversation()
    with pytest.raises(KeyError):
        database.conversation_page(other, anchor=ids[106])
    with pytest.raises(KeyError):
        database.conversation_page("not-a-real-chat")
    assert database.conversation_page(other)["messages"] == []


def test_reply_revision_atomic_and_does_not_revive_forgotten_memory(tmp_path):
    path = tmp_path / "chat.db"
    database = Database(path)
    conversation = database.ensure_conversation()
    user = database.add_message(conversation, "user", "解释一下")
    answer = database.add_message(conversation, "assistant", "不合适的旧答案")
    with database.connect() as db:
        fragment_id = db.execute("SELECT fragment_id FROM memory_fragments WHERE source_id=?", (answer,)).fetchone()[0]
    database.forget_memory_fragment(fragment_id)
    database.replace_assistant_reply(answer, "不合适的旧答案", "更准确的新答案")
    assert len(database.recent_messages(conversation)) == 2
    assert database.recent_messages(conversation)[0]["message_id"] == user
    assert database.reply_revisions(answer)[0]["content"] == "不合适的旧答案"
    assert database.memory_fragment(fragment_id)["forgotten"]
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memory_ngrams WHERE fragment_id=?", (fragment_id,)).fetchone()[0] == 0
    with pytest.raises(ValueError):
        database.replace_assistant_reply(answer, "不合适的旧答案", "过期的并发答案")
    assert len(database.reply_revisions(answer)) == 1
    database.close()
    reopened = Database(path)
    assert reopened.recent_messages(conversation)[-1]["content"] == "更准确的新答案"
    assert len(reopened.reply_revisions(answer)) == 1
    assert reopened.integrity_check() == "ok"
    with reopened.connect() as db:
        db.execute("DELETE FROM messages WHERE message_id=?", (answer,))
    assert reopened.reply_revisions(answer) == []


class FakeModel:
    ready = True
    running = False
    plan_type = "test"

    def __init__(self, complete):
        self.complete = complete

    def abort(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def chat(tmp_path):
    service = ChatService(Database(tmp_path / "chat.db"))
    yield service
    service.shutdown()


def test_retry_replaces_answer_excludes_old_context_and_rejects_action_tools(chat, monkeypatch):
    database = chat.database
    database.add_message(chat.conversation_id, "user", "打开 WPS")
    answer = database.add_message(chat.conversation_id, "assistant", "旧回答不应进入提示词")
    calls = []
    monkeypatch.setattr(chat, "_route_simple_tool", lambda *a, **k: calls.append(a) or "不能执行")

    def complete(prompt, **kwargs):
        assert "旧回答不应进入提示词" not in prompt
        assert prompt.count('"content": "打开 WPS"') == 1
        assert "不再次执行任何组件动作" in prompt
        assert all(spec.get("namespace") != "box" for spec in kwargs["dynamic_tools"])
        with pytest.raises(PermissionError):
            kwargs["tool_handler"]("tasks_create", {"title": "不应该创建"}, {"namespace": "box"})
        kwargs["on_delta"]("新的回答。")
        return "新的回答。"

    chat._gpt = FakeModel(complete)
    assert chat.retry_last()
    wait_finished(chat)
    assert calls == []
    assert [m["content"] for m in database.recent_messages(chat.conversation_id)] == ["打开 WPS", "新的回答。"]
    assert len(database.reply_revisions(answer)) == 1
    assert chat._retry_state is None


@pytest.mark.parametrize("failure", ["cancel", "exception"])
def test_interrupted_retry_keeps_old_reply_and_no_draft_revision(chat, failure):
    database = chat.database
    database.add_message(chat.conversation_id, "user", "再想想这件事")
    answer = database.add_message(chat.conversation_id, "assistant", "原来的回答")

    def complete(prompt, **kwargs):
        kwargs["on_delta"]("不能保存的半句")
        if failure == "cancel":
            chat._cancel.set()
            return "不能保存的半句"
        raise RuntimeError("断网")

    chat._gpt = FakeModel(complete)
    assert chat.retry_last()
    wait_finished(chat)
    assert database.recent_messages(chat.conversation_id)[-1]["content"] == "原来的回答"
    assert database.reply_revisions(answer) == []


def test_failed_first_response_retries_same_user_turn_and_busy_cannot_switch(chat):
    database = chat.database
    user = database.add_message(chat.conversation_id, "user", "第一次没有答出来")
    entered = threading.Event()
    release = threading.Event()

    def complete(prompt, **kwargs):
        entered.set()
        assert release.wait(3)
        return "这次答出来了"

    chat._gpt = FakeModel(complete)
    try:
        assert chat.retry_last()
        assert entered.wait(2)
        assert not chat.retry_last()
        with pytest.raises(RuntimeError):
            chat.open_conversation(database.ensure_conversation())
    finally:
        release.set()
    wait_finished(chat)
    rows = database.recent_messages(chat.conversation_id)
    assert len(rows) == 2 and rows[0]["message_id"] == user
    assert rows[-1]["content"] == "这次答出来了"


def test_open_history_validates_before_changing_active_conversation(chat):
    database = chat.database
    old = database.ensure_conversation()
    message = database.add_message(old, "user", "去年那篇论文")
    previous = chat.conversation_id
    with pytest.raises(KeyError):
        chat.open_conversation(old, "missing")
    assert chat.conversation_id == previous
    assert chat.open_conversation(old, message)["anchorIndex"] == 0
    assert database.get_setting("active_conversation") == old


def test_retry_of_unpersisted_image_requires_reselection_without_touching_turn(chat):
    database = chat.database
    database.add_message(chat.conversation_id, "user", "解释这张图", {"hasImage": True})
    answer = database.add_message(chat.conversation_id, "assistant", "之前的图片解释")
    with pytest.raises(ValueError, match="重新选择图片"):
        chat.retry_last()
    assert not chat._working
    assert database.recent_messages(chat.conversation_id)[-1]["content"] == "之前的图片解释"
    assert not database.reply_revisions(answer)


def test_retry_timeline_does_not_duplicate_user_or_leave_queued_chunks():
    timeline = ChatTimeline()
    original = [{"role": "user", "content": "问题"}, {"role": "assistant", "content": "旧答案"}]
    timeline.reset_messages(original)
    timeline.begin_retry()
    timeline.append_chunk("新答案")
    timeline.flush()
    assert timeline.rowCount() == 2
    assert timeline.data(timeline.index(1), timeline.TEXT) == "新答案"
    timeline.append_chunk("过期的尾巴")
    timeline.reset_messages(original)
    QTest.qWait(40)
    assert timeline.data(timeline.index(1), timeline.TEXT) == "旧答案"
    timeline.reset_messages(original[:1])
    timeline.begin_retry()
    assert timeline.rowCount() == 2
