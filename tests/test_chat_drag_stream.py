"""Held-pointer stream checks: synthetic Qt events, no model/native input/data."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QSignalSpy, QTest

from lilies.backend import Backend
from lilies.chat_timeline import ChatTimeline


def wait_until(predicate):
    # Positive timer delivery is scheduler-dependent, not a frame-rate test.
    for _ in range(100):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), "owned Qt event did not settle"


@pytest.fixture
def backend():
    value = Backend.__new__(Backend)
    QObject.__init__(value)
    value._chat_timeline = ChatTimeline(value)
    value._chat_timeline.updated.connect(value.chatChanged)
    value._pet_interaction_lock_reasons = set()
    value._pet_interaction_locked = False
    value._pet_pointer_critical_locked = False
    value._pet_pointer_paused_timers = {}
    value._pet_pointer_input_pulse_suspended = False
    value._pet_cursor_sample = None
    value._pet_interaction_grace_until = 0.0
    value._companion_interaction_suspended = False
    value._window_catalog_refresh_running = False
    value._shutdown_in_progress = False
    value._shutdown_complete = False
    value._recordRuntimeHeartbeat = lambda: None
    value.selection = SimpleNamespace(set_interaction_suspended=lambda _: None)
    value.companion = SimpleNamespace(set_interaction_suspended=lambda _: None)
    value.input_pulse = SimpleNamespace(set_interaction_suspended=lambda _: None)
    value._chat_timeline.begin_turn("继续读这一段")
    yield value
    value._chat_timeline.reset_messages()
    value.deleteLater()
    QTest.qWait(1)


@pytest.mark.parametrize("reason", ["character", "resize", "accessory-box"])
def test_held_pointer_stops_periodic_tail_layout_without_dropping_unicode(backend, reason):
    model = backend._chat_timeline
    changes = QSignalSpy(model.dataChanged)
    backend._on_chat_chunk("按下前已经排队。")
    backend.setPetInteractionLock(reason, True)
    parts = ["中文", "🌱", "\n", "<不是标记>", "后续"]
    for part in parts:
        backend._on_chat_chunk(part)
        QTest.qWait(45)
    # Reproduce actual event-loop timer firings, not a tight loop that never
    # lets the existing 33 ms coalescer fire in the first place.
    assert changes.count() == 0, "stream layout still wakes during held pointer"
    assert not model._timer.isActive()
    assert model.data(model.index(1), model.TEXT) == ""
    expected = "按下前已经排队。" + "".join(parts)
    assert backend.chatText.endswith(expected)
    backend.setPetInteractionLock(reason, False)
    assert changes.count() == 0  # Release does not synchronously lay out text.
    wait_until(lambda: changes.count() > 0)
    assert changes.count() == 1
    assert model.data(model.index(1), model.TEXT) == expected


def test_nested_owners_and_regrab_do_not_thaw_old_pending_update(backend):
    model = backend._chat_timeline
    changes = QSignalSpy(model.dataChanged)
    backend.setPetInteractionLock("menu", True)
    backend.setPetInteractionLock("character", True)
    backend.setPetInteractionLock("resize", True)
    backend._on_chat_chunk("保留的文字")
    backend.setPetInteractionLock("character", False)
    QTest.qWait(50)
    assert changes.count() == 0
    backend.setPetInteractionLock("resize", False)
    backend.setPetInteractionLock("character", True)
    model.flush()  # A timeout already posted before the new press.
    QTest.qWait(50)
    assert changes.count() == 0
    backend.clearPetInteractionLocks()
    wait_until(lambda: changes.count() > 0)
    assert changes.count() == 1
    assert model.data(model.index(1), model.TEXT) == "保留的文字"


@pytest.mark.parametrize("reason", ["menu", "companion-unread", "desktop-mode-tab"])
def test_non_pointer_owners_keep_normal_streaming(backend, reason):
    backend.setPetInteractionLock(reason, True)
    backend._on_chat_chunk("仍然逐字显示")
    wait_until(lambda: not backend._chat_timeline._timer.isActive())
    assert backend._chat_timeline.data(backend._chat_timeline.index(1), ChatTimeline.TEXT) == "仍然逐字显示"


def test_final_commit_is_immediate_and_does_not_replay_deferred_draft(backend):
    model = backend._chat_timeline
    backend.setPetInteractionLock("character", True)
    backend._on_chat_chunk("临时草稿")
    model.finish("最终回答")
    model.bind_message_ids([
        {"role": "user", "content": "继续读这一段", "message_id": "user"},
        {"role": "assistant", "content": "最终回答", "message_id": "answer"},
    ])
    assert model.data(model.index(1), model.TEXT) == "最终回答"
    assert model.data(model.index(1), model.MESSAGE_ID) == "answer"
    changes = QSignalSpy(model.dataChanged)
    backend.setPetInteractionLock("character", False)
    QTest.qWait(60)
    assert changes.count() == 0
    assert "草稿" not in backend.chatText


def test_history_reset_discards_held_pending_text_without_thawing_stream(backend):
    model = backend._chat_timeline
    backend.setPetInteractionLock("character", True)
    backend._on_chat_chunk("不应串到下一段")
    model.reset_messages([{"role": "user", "content": "新问题"}])
    model.begin_retry()
    backend._on_chat_chunk("新回答")
    QTest.qWait(60)
    assert model.data(model.index(1), model.TEXT) == ""
    assert backend.chatText == "你：新问题\n\n莉莉丝：新回答"
    backend.clearPetInteractionLocks()
    wait_until(lambda: not model._timer.isActive())
    assert model.data(model.index(1), model.TEXT) == "新回答"


def test_shutdown_release_cannot_restart_pending_stream_timer(backend):
    backend.setPetInteractionLock("character", True)
    backend._on_chat_chunk("关闭中的待显示文字")
    backend._shutdown_in_progress = True
    backend.clearPetInteractionLocks()
    QTest.qWait(60)
    assert not backend._chat_timeline._timer.isActive()
    assert backend._chat_timeline.data(backend._chat_timeline.index(1), ChatTimeline.TEXT) == ""
