"""Measure synthetic worker -> Qt delivery, never native input/display latency.

No Backend/ChatService initializer runs, so this harness opens no database,
socket, model bridge, observer or native window. Payloads are synthetic; reports
contain counts/timings only. Run explicitly, not as part of application startup.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QSG_RHI_BACKEND"] = "software"
os.environ["QT_QUICK_BACKEND"] = "software"
os.environ["QML_DISABLE_DISK_CACHE"] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot
from PySide6.QtTest import QTest
from lilies.backend import Backend
from lilies.chat_timeline import ChatTimeline
from lilies.core.model import ChatService
from lilies.chat_delivery import ChatDelivery


class Marker(QObject):
    reached = Signal()


class Receiver(Backend):
    def __init__(self):
        # Only the real stream callback/timeline and inherited Qt signals.
        QObject.__init__(self)
        self._chat_timeline = ChatTimeline(self)
        self.calls = 0
        self.thread_ids = set()
        self.marker_at = None

    def _on_chat_chunk(self, value):
        self.calls += 1
        self.thread_ids.add(threading.get_ident())
        super()._on_chat_chunk(value)

    @Slot()
    def mark(self):
        self.marker_at = time.perf_counter()


def measure(count, paused, *, coalesced):
    source = ChatService.__new__(ChatService)
    QObject.__init__(source)
    marker = Marker()
    target = Receiver()
    target._chat_timeline.begin_turn("合成问题")
    target._chat_timeline.set_stream_paused(paused)
    delivery = ChatDelivery(target._on_chat_chunk, target) if coalesced else None
    if delivery:
        source.chunk.connect(delivery.enqueue, Qt.ConnectionType.DirectConnection)
    else:
        source.chunk.connect(target._on_chat_chunk)
    marker.reached.connect(target.mark)
    worker_identity = []

    def produce():
        worker_identity.append(threading.get_ident())
        for _ in range(count):
            source.chunk.emit("字")
        marker.reached.emit()

    worker = threading.Thread(target=produce, daemon=True)
    before = time.perf_counter()
    worker.start()
    # Deliberately queue a burst before pumping. This represents a stress
    # bound, not measured model chunk frequency or a natural desktop stall.
    worker.join(timeout=5)
    assert not worker.is_alive(), "owned producer did not finish"
    queued_at = time.perf_counter()
    assert target.calls == 0 and target.marker_at is None, "callback escaped Qt GUI ownership"
    deadline = queued_at + 10
    while target.marker_at is None and time.perf_counter() < deadline:
        QCoreApplication.processEvents()
    assert target.marker_at is not None
    assert target.calls == (1 if coalesced and count else count)
    assert target.thread_ids == ({threading.get_ident()} if count else set())
    assert worker_identity != [threading.get_ident()]
    assert target._chat_timeline.plain_text().endswith("字" * count)
    result = {
        "chunks": count, "pointerPresentationPaused": paused,
        "queuedGuiCallbacks": target.calls,
        "producerMs": round((queued_at - before) * 1000, 4),
        "burstDrainToMarkerMs": round((target.marker_at - queued_at) * 1000, 4),
        "allTextRetained": True,
    }
    target._chat_timeline.reset_messages()
    if delivery:
        delivery.close()
    source.deleteLater()
    marker.deleteLater()
    target.deleteLater()
    QTest.qWait(1)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coalesced", action="store_true")
    args = parser.parse_args()
    app = QCoreApplication.instance() or QCoreApplication([])
    rows = [measure(count, paused, coalesced=args.coalesced) for paused in (False, True) for count in (192, 1000, 8000)]
    report = {
        "scope": "synthetic queued worker burst; no native input, pixels, model or private data",
        "measuresPresentedFrames": False, "measuresInputQueueDelay": False,
        "coalesced": args.coalesced,
        "rows": rows,
    }
    output = ROOT / "artifacts" / ("chat-signal-delivery-after.json" if args.coalesced else "chat-signal-delivery-before.json")
    if output.exists():
        raise FileExistsError(f"Preserve previous measurement: {output}")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    app.processEvents()


if __name__ == "__main__":
    main()
