"""Synthetic GUI-thread stream tests; no model, disk, socket or native input."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QSignalSpy, QTest

from lilies.backend import Backend
from lilies.chat_timeline import ChatTimeline
from lilies.chat_delivery import ChatDelivery


class LegacyCopySentinel(str):
    copied_characters = 0

    def __add__(self, other):
        type(self).copied_characters += len(self)
        return type(self)(super().__add__(other))


@pytest.fixture
def backend():
    # Deliberately do not call Backend.__init__: this harness needs only its
    # actual chat slots and Qt signals, never any system/native service.
    value = Backend.__new__(Backend)
    QObject.__init__(value)
    value._chat_timeline = ChatTimeline(value)
    value._chat_timeline.updated.connect(value.chatChanged)
    value._chat_delivery = ChatDelivery(value._on_chat_chunk, value)
    value._chat_busy = False
    value._chat_retrying = False
    value._chat_retry_error = ""
    value._conversation_page = {"messages": []}
    value._reply_revisions = []
    # Present solely to reproduce accidental legacy whole-history copying.
    value._chat_text = LegacyCopySentinel("")
    value.chat = SimpleNamespace(
        send=lambda text: True, conversation_id="fixture", retry_last=lambda: True
    )
    value.database = SimpleNamespace(
        reply_revisions=lambda _: [],
        conversation_page=lambda _: value._conversation_page,
    )
    yield value
    value._chat_delivery.close()
    value._chat_timeline.reset_messages()
    value.deleteLater()
    QTest.qWait(1)


def test_stream_does_not_copy_previous_history_per_delta(backend):
    old = "已经读过的那篇论文。" * 20_000
    backend._chat_timeline.reset_messages([{"role": "assistant", "content": old}])
    backend._chat_timeline.begin_turn("继续")
    backend._chat_text = LegacyCopySentinel(old)
    LegacyCopySentinel.copied_characters = 0
    changes = QSignalSpy(backend._chat_timeline.dataChanged)
    for _ in range(1000):
        backend._on_chat_chunk("新的句子。")
    assert LegacyCopySentinel.copied_characters == 0
    assert changes.count() == 0  # Still coalesced by the existing tail timer.
    backend._chat_timeline.flush()
    assert changes.count() == 1 and changes.at(0)[0].row() == 2
    assert backend.chatText == "莉莉丝：" + old + "\n\n你：继续\n\n莉莉丝：" + "新的句子。" * 1000


def test_plain_snapshot_includes_pending_unicode_without_flushing(backend):
    assert backend.sendMessage("  你好 🌱  ")
    notifications = QSignalSpy(backend.chatChanged)
    backend._on_chat_chunk("好，")
    backend._on_chat_chunk("继续读。\n<不是富文本>")
    before = notifications.count()
    first = backend.chatText
    assert first == "你：你好 🌱\n\n莉莉丝：好，继续读。\n<不是富文本>"
    assert notifications.count() == before  # Getter must not emit or flush.
    assert backend.chatText is first  # Unchanged readers reuse the snapshot.
    backend._chat_timeline.flush()
    assert backend.chatText == first


def test_retry_snapshot_and_history_switch_do_not_keep_old_reply_or_pending_tail(backend):
    page = {"messages": [
        {"role": "user", "content": "上次的问题", "message_id": "u"},
        {"role": "assistant", "content": "旧答案", "message_id": "a"},
    ]}
    backend._display_chat_page(page)
    backend.retryLastMessage()
    backend._on_chat_chunk("新的回答")
    assert backend.chatText == "你：上次的问题\n\n莉莉丝：新的回答"
    # The failed/cancelled retry path restores the saved authoritative page.
    backend._display_chat_page(page)
    QTest.qWait(45)
    assert backend.chatText == "你：上次的问题\n\n莉莉丝：旧答案"
    backend._display_chat_page({"messages": [{"role": "user", "content": "另一篇", "message_id": "b"}]})
    assert backend.chatText == "你：另一篇"


def test_final_reply_and_error_snapshot_match_visible_timeline(backend):
    backend.sendMessage("继续")
    backend._on_chat_chunk("尚未确认的草稿")
    assert "草稿" in backend.chatText
    backend._chat_timeline.finish("最终文字")
    assert backend.chatText == "你：继续\n\n莉莉丝：最终文字"
    backend._on_chat_error("连接中断")
    assert backend.chatText == "你：继续\n\n莉莉丝：最终文字\n连接中断"


def test_snapshot_cache_is_invalidated_by_each_visible_mutation():
    model = ChatTimeline()
    assert model.plain_text() == ""
    model.begin_turn("问题")
    assert model.plain_text() == "你：问题\n\n莉莉丝："
    model.append_chunk("回答")
    assert model.plain_text() == "你：问题\n\n莉莉丝：回答"
    model.finish("修正")
    assert model.plain_text() == "你：问题\n\n莉莉丝：修正"
    model.begin_retry()
    assert model.plain_text() == "你：问题\n\n莉莉丝："
    model.reset_messages()
    assert model.plain_text() == ""
