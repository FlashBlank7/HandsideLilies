"""Real queued Qt delivery with owned workers and F-drive synthetic data."""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal
from PySide6.QtTest import QSignalSpy, QTest

from lilies.backend import Backend
from lilies.chat_delivery import ChatDelivery


class Source(QObject):
    chunk = Signal(str)


def run_worker(callback):
    errors = []

    def run():
        try:
            callback()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "owned worker did not terminate"
    return errors


def pump_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def channel():
    source = Source()
    delivered = []
    delivery = ChatDelivery(lambda text: delivered.append((threading.get_ident(), text)))
    source.chunk.connect(delivery.enqueue, Qt.ConnectionType.DirectConnection)
    yield source, delivery, delivered
    delivery.close()
    QCoreApplication.processEvents()
    source.deleteLater()
    delivery.deleteLater()
    QTest.qWait(1)


@pytest.mark.parametrize("count", [1, 192, 8000])
def test_one_queued_gui_wake_for_burst_and_exact_unicode_order(channel, count):
    source, delivery, delivered = channel
    notifications = QSignalSpy(delivery._ready)
    parts = [f"{i}:🌱中\n" for i in range(count)]
    assert not run_worker(lambda: [source.chunk.emit(part) for part in parts])
    assert not delivered
    assert notifications.count() == 1
    pump_until(lambda: bool(delivered))
    assert delivered == [(threading.get_ident(), "".join(parts))]
    assert delivery._parts == [] and not delivery._notified


def test_same_thread_producer_is_also_deferred_without_timer(channel):
    source, delivery, delivered = channel
    source.chunk.emit("第一字")
    assert delivered == []
    pump_until(lambda: bool(delivered))
    assert delivered == [(threading.get_ident(), "第一字")]


def test_flush_before_error_or_finish_invalidates_stale_wake_not_later_batch(channel):
    source, delivery, delivered = channel
    source.chunk.emit("先前半句")
    stale_ticket = delivery._ticket
    delivery.flush()
    source.chunk.emit("下一段")
    assert delivery._ticket != stale_ticket
    delivery._drain(stale_ticket)
    assert delivered == [(threading.get_ident(), "先前半句")]
    pump_until(lambda: len(delivered) == 2)
    QCoreApplication.processEvents()
    assert [text for _, text in delivered] == ["先前半句", "下一段"]


def test_callback_can_enqueue_without_lock_deadlock_or_order_inversion():
    parts = []

    def receive(text):
        parts.append(text)
        if text == "先":
            delivery.enqueue("后")

    delivery = ChatDelivery(receive)
    try:
        delivery.enqueue("先")
        pump_until(lambda: len(parts) == 2)
        assert parts == ["先", "后"]
    finally:
        delivery.close()


def test_sparse_output_does_not_wait_for_size_threshold(channel):
    source, _, delivered = channel
    assert not run_worker(lambda: source.chunk.emit("首字"))
    pump_until(lambda: len(delivered) == 1)
    assert not run_worker(lambda: source.chunk.emit("末字"))
    pump_until(lambda: len(delivered) == 2)
    assert [text for _, text in delivered] == ["首字", "末字"]


def test_empty_deltas_and_repeated_flush_do_not_publish_phantom_output(channel):
    source, delivery, delivered = channel
    notifications = QSignalSpy(delivery._ready)
    assert not run_worker(lambda: [source.chunk.emit("") for _ in range(100)])
    delivery.flush()
    delivery.flush()
    QCoreApplication.processEvents()
    assert delivered == [] and notifications.count() == 0


def test_producer_can_enqueue_next_batch_while_gui_delivers_current():
    first_entered = threading.Event()
    second_queued = threading.Event()
    seen, errors = [], []

    def receive(text):
        seen.append(text)
        if text == "第一段":
            first_entered.set()
            if not second_queued.wait(timeout=2):
                errors.append("delivery held the producer lock")

    delivery = ChatDelivery(receive)

    def produce():
        delivery.enqueue("第一段")
        if not first_entered.wait(timeout=2):
            errors.append("GUI never delivered first batch")
            return
        delivery.enqueue("第二段")
        second_queued.set()

    worker = threading.Thread(target=produce)
    try:
        worker.start()
        pump_until(lambda: len(seen) == 2 or bool(errors))
        worker.join(timeout=3)
        assert not worker.is_alive() and not errors
        assert seen == ["第一段", "第二段"]
    finally:
        first_entered.set()
        second_queued.set()
        worker.join(timeout=3)
        delivery.close()


def test_close_discards_only_pending_presentation_and_ignores_late_producer(channel):
    source, delivery, delivered = channel
    source.chunk.emit("已经交付")
    delivery.flush()
    source.chunk.emit("关闭时尚未交付")
    delivery.close()
    delivery.close()
    assert not run_worker(lambda: [source.chunk.emit("迟到") for _ in range(8000)])
    delivery.flush()
    QCoreApplication.processEvents()
    assert [text for _, text in delivered] == ["已经交付"]
    assert not delivery._parts and not delivery._notified


@pytest.mark.parametrize("method", ["flush", "close"])
def test_worker_cannot_force_gui_delivery_or_close(channel, method):
    source, delivery, delivered = channel
    source.chunk.emit("原内容")
    errors = run_worker(getattr(delivery, method))
    assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    assert delivered == []
    delivery.flush()
    assert [text for _, text in delivered] == ["原内容"]


def test_concurrent_producers_keep_each_sequence_without_loss(channel):
    source, _, delivered = channel
    gate = threading.Barrier(3)

    def produce(letter):
        gate.wait(timeout=3)
        for index in range(1000):
            source.chunk.emit(f"{letter}{index},")

    workers = [threading.Thread(target=produce, args=(letter,)) for letter in "AB"]
    for worker in workers:
        worker.start()
    gate.wait(timeout=3)
    for worker in workers:
        worker.join(timeout=5)
        assert not worker.is_alive()
    pump_until(lambda: bool(delivered))
    text = "".join(part for _, part in delivered)
    rows = text.rstrip(",").split(",")
    assert len(rows) == 2000
    for letter in "AB":
        assert [row for row in rows if row.startswith(letter)] == [f"{letter}{i}" for i in range(1000)]


def test_backend_merges_worker_burst_before_gui_callbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    backend._chat_timeline.begin_turn("合成问题")
    calls = []
    append = backend._chat_timeline.append_chunk

    def counted(value):
        calls.append((threading.get_ident(), len(value)))
        append(value)

    monkeypatch.setattr(backend._chat_timeline, "append_chunk", counted)
    worker = threading.Thread(target=lambda: [backend.chat.chunk.emit("字") for _ in range(8000)])
    try:
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert calls == []
        QCoreApplication.processEvents()
        assert calls == [(threading.get_ident(), 8000)]
        assert backend.chatText.endswith("字" * 8000)
    finally:
        worker.join(timeout=5)
        backend.shutdown()
        QCoreApplication.processEvents()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_backend_terminal_event_drains_prefix_without_tail_after_final(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    backend._v03_timer.stop()
    backend._productivity_timer.stop()
    model = backend._chat_timeline
    model.begin_turn("合成问题")
    backend._chat_busy = True
    backend.setPetInteractionLock("character", True)
    backend.database.add_message(backend.chat.conversation_id, "user", "合成问题")
    final_id = backend.database.add_message(backend.chat.conversation_id, "assistant", "最终回答") if outcome == "success" else ""

    def produce():
        for _ in range(1000):
            backend.chat.chunk.emit("临时")
        if outcome == "error":
            backend.chat.error.emit("模拟中断")
        backend.chat.responseFinished.emit("最终回答" if outcome == "success" else "")

    try:
        assert not run_worker(produce)
        pump_until(lambda: not backend.chatBusy)
        if outcome == "success":
            expected = "最终回答"
            assert model.data(model.index(1), model.MESSAGE_ID) == final_id
        else:
            expected = "临时" * 1000 + ("\n模拟中断" if outcome == "error" else "")
            assert len(backend.database.recent_messages(backend.chat.conversation_id)) == 1
        assert model.data(model.index(1), model.TEXT) == expected
        backend.clearPetInteractionLocks()
        QTest.qWait(80)
        assert model.data(model.index(1), model.TEXT) == expected
    finally:
        backend.shutdown()
        QCoreApplication.processEvents()
