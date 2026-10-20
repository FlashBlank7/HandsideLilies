import math
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from lilies.backend import Backend
from lilies.core.quiet_reactions import QuietReactionGate, completed_task_identity


def test_gate_is_bounded_transient_and_discards_instead_of_queuing():
    now = [10.0]
    gate = QuietReactionGate(clock=lambda: now[0])
    assert gate.accept("a:1", 10, allowed=True) == {"sequence": 1, "kind": "task-completed"}
    now[0] += 3
    assert gate.accept("a:1", 13, allowed=True) is None
    assert gate.accept("a:2", 13, allowed=True)["sequence"] == 2
    assert gate.accept("b:1", 13, allowed=True) is None
    now[0] += 3
    assert gate.accept("b:1", 16, allowed=True) is None
    assert gate.accept("private:1", 16, allowed=False) is None
    assert gate.accept("private:1", 16, allowed=True) is None
    assert gate.accept("stale:1", 10, allowed=True) is None
    assert gate.accept("invalid", math.nan, allowed=True) is None
    for i in range(300): gate.accept(f"ignored:{i}", 16, allowed=False)
    assert len(gate._seen) == 256
    assert QuietReactionGate(clock=lambda: now[0]).sequence == 0


@pytest.mark.parametrize("result", [None, {}, {"alreadyCompleted": True}, {"alreadyCompleted": False, "occurrence": {}},
    {"alreadyCompleted": False, "occurrence": "invalid"}, {"alreadyCompleted": False, "occurrence": ["invalid"]},
    {"alreadyCompleted": False, "occurrence": {"occurrence_id": "a", "status": "pending", "completion_version": 1}},
    {"alreadyCompleted": False, "occurrence": {"occurrence_id": "a", "status": "completed", "completion_version": True}}])
def test_only_committed_task_results_are_eligible(result):
    assert not completed_task_identity(result)


def test_backend_ui_and_registered_worker_completion_without_replay_or_prose(tmp_path, monkeypatch):
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    backend._v03_timer.stop(); backend._productivity_timer.stop()
    notices, signals = [], []
    backend.reminderDue.connect(lambda *args: notices.append(args))
    backend.petReactionChanged.connect(lambda: signals.append((backend.petReaction, threading.get_ident())))
    try:
        task = backend.tasks.create("不要把这句私密标题交给动画")
        backend.tasksComplete(task["task_id"])
        assert signals[-1][0] == {"sequence": 1, "kind": "task-completed"}
        backend.tasksComplete(task["task_id"])
        assert len(signals) == 1 and not notices
        # A completed worker action returns an audited wrapper, not a raw task result.
        task2 = backend.tasks.create("worker")
        backend._quiet_reaction_gate._last_at = -math.inf
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(backend.registry.invoke("tasks", "complete",
            {"taskId": task2["task_id"]}, origin="test", confirmed=True)))
        worker.start(); worker.join(timeout=4)
        assert not worker.is_alive() and outcomes
        for _ in range(100):
            QCoreApplication.processEvents()
            if len(signals) == 2: break
            time.sleep(0.005)
        assert len(signals) == 2 and signals[-1][1] == threading.get_ident()
        assert signals[-1][0]["sequence"] == 2
        task3 = backend.tasks.create("suppressed")
        backend._companion_suppressed_status = True
        backend.tasksComplete(task3["task_id"])
        backend._companion_suppressed_status = False
        backend.tasksComplete(task3["task_id"])
        assert len(signals) == 2
    finally:
        backend.shutdown(); QCoreApplication.processEvents()
