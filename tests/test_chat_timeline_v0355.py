from PySide6.QtCore import QModelIndex, QObject
from PySide6.QtTest import QSignalSpy, QTest

from lilies.chat_timeline import ChatTimeline
from lilies.app import CompactPointerEventFilter


def test_stream_updates_only_tail_and_coalesces_a_thousand_chunks():
    model = ChatTimeline()
    history = [{"role": "assistant", "content": "旧消息" * 200} for _ in range(100)]
    model.reset_messages(history)
    model.begin_turn("继续")
    changes = QSignalSpy(model.dataChanged)
    resets = QSignalSpy(model.modelReset)
    for _ in range(1000):
        model.append_chunk("字")
    assert changes.count() == 0
    QTest.qWait(60)
    assert changes.count() == 1
    assert changes.at(0)[0].row() == 101
    assert changes.at(0)[1].row() == 101
    assert resets.count() == 0
    assert model.data(model.index(0), model.TEXT) == history[0]["content"]
    assert model.data(model.index(101), model.TEXT) == "字" * 1000
    assert model.rowCount(model.index(0)) == 0
    assert model.data(QModelIndex(), model.TEXT) is None


def test_final_authoritative_text_and_new_conversation_drop_queued_output():
    model = ChatTimeline()
    model.begin_turn("你好")
    model.append_chunk("草稿")
    model.finish("你好。")
    assert model.data(model.index(1), model.TEXT) == "你好。"
    model.append_chunk("旧的待处理文字")
    model.reset_messages()
    QTest.qWait(50)
    assert model.rowCount() == 0
    model.begin_turn("新话题")
    model.finish("从这里开始。")
    assert model.data(model.index(1), model.TEXT) == "从这里开始。"


def test_direct_drag_suspends_hit_walk_but_keeps_pointer_capture_without_cache():
    root = QObject()
    root.setProperty("nativeDragAwaitingThreshold", False)
    bridge = CompactPointerEventFilter(root)
    events = []

    class Observer:
        def suspend_drag_observers(self): events.append("pause")
        def resume_drag_observers(self): events.append("resume")

    bridge._native_drag_filter = Observer()
    bridge.beginDragProxyGesture()
    assert bridge._direct_character_drag_active
    assert not bridge._native_drag_filter_suspended
    bridge._resume_native_drag_observers()
    assert events == ["pause"]
    bridge.endDragProxyGesture()
    assert not bridge._direct_character_drag_active
    assert events == ["pause", "resume"]
    bridge.endDragProxyGesture()
    assert events == ["pause", "resume"]
    bridge.close_drag_diagnostics_writer()


def test_native_press_does_not_claim_direct_observer_ownership():
    root = QObject()
    root.setProperty("nativeDragAwaitingThreshold", True)
    bridge = CompactPointerEventFilter(root)
    bridge.beginDragProxyGesture()
    assert not bridge._direct_character_drag_active
    bridge.endDragProxyGesture()
    bridge.close_drag_diagnostics_writer()
