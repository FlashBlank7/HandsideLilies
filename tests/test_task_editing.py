from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lilies.core.database import Database
from lilies.core.productivity import TaskService, GrowthEngine
from lilies.core.task_editing import task_changes


def test_editor_converts_local_time_and_retains_explicit_fields():
    changes = task_changes({
        "title": "读完消融实验", "priority": "high", "note": "比较表 3",
        "dueLocal": "2026-10-04 18:00", "timezone": "Asia/Tokyo",
        "recurrence": {"frequency": "weekly", "interval": 2},
    })
    assert changes["priority"] == 2
    assert changes["due_at"] == "2026-10-04T18:00:00+09:00"
    assert changes["recurrence"]["interval"] == 2
    assert changes["timezone_name"] == "Asia/Tokyo"
    assert task_changes({"dueLocal": ""}) == {"due_at": None}
    assert task_changes({"title": "改名字"}) == {"title": "改名字"}


@pytest.mark.parametrize("value", [
    {"dueLocal": "明天"},
    {"dueLocal": "2026-02-30 12:00"},
    {"dueLocal": "2026-10-04 25:00"},
    {"dueLocal": "2026-03-08 02:30", "timezone": "America/New_York"},
    {"dueLocal": "2026-11-01 01:30", "timezone": "America/New_York"},
    {"timezone": "invalid-zone"},
    {"dueAt": "2026-10-04T18:00Z", "dueLocal": "2026-10-04 18:00"},
    {"points": 1000},
])
def test_invalid_and_ambiguous_edits_are_rejected(value):
    with pytest.raises(ValueError):
        task_changes(value)


def test_recurring_completion_undo_and_archive_are_instance_scoped(tmp_path):
    database = Database(tmp_path / "tasks.db")
    now = lambda: datetime(2026, 10, 3, 10, tzinfo=UTC)
    growth = GrowthEngine(database, now=now)
    tasks = TaskService(database, growth=growth, now=now)
    task = tasks.create("读论文", recurrence={"frequency": "daily"})
    task_id = task["task_id"]
    occurrence = task["occurrences"][0]["occurrence_id"]
    first = tasks.complete(task_id, occurrence)
    assert first["growth"]["points"] == 10
    assert tasks.complete(task_id, occurrence)["alreadyCompleted"]
    assert growth.status()["totalPoints"] == 10
    tasks.reopen(task_id, occurrence)
    assert tasks.reopen(task_id, occurrence)["alreadyOpen"]
    assert growth.status()["totalPoints"] == 0
    pending = tasks.get(task_id)["occurrences"]
    assert len(pending) == 2 and all(row["status"] == "pending" for row in pending)
    tasks.archive(task_id)
    restored = tasks.restore(task_id)
    assert restored["status"] == "open" and not restored["archived"]
    assert tasks.restore(task_id) == restored
    assert growth.status()["totalPoints"] == 0
    assert restored["occurrences"] == pending


def test_archived_completed_task_stays_completed_on_restore(tmp_path):
    tasks = TaskService(Database(tmp_path / "tasks.db"))
    task = tasks.create("完成的事")
    tasks.complete(task["task_id"])
    tasks.archive(task["task_id"])
    assert tasks.restore(task["task_id"])["status"] == "completed"


def test_backend_edit_reopen_and_task_focus_keep_real_identity(tmp_path, monkeypatch):
    from lilies.backend import Backend

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    try:
        created = backend.tasksCreate({"title": "待修改", "category": "research"})
        assert created["ok"]
        task = created["task"]
        task_id = task["id"]
        saved = backend.tasksUpdate(task_id, {
            "title": "对照实验", "dueLocal": "2026-10-04 18:00", "timezone": "Asia/Tokyo",
            "recurrence": {"frequency": "weekly", "interval": 1}, "priority": "high",
        })
        assert saved["ok"] and saved["task"]["dueLocal"] == "2026-10-04 18:00"
        assert backend.focusStartTask(task_id, 25)["ok"]
        assert backend.focusStatus["taskId"] == task_id
        assert backend.focusStatus["taskTitle"] == "对照实验"
        assert 0 < backend.focusStatus["remainingSeconds"] <= 1500
        backend.focusCancel()
        occurrence_id = saved["task"]["occurrenceId"]
        assert backend.tasksComplete(task_id, occurrence_id)["ok"]
        assert backend.tasksComplete(task_id, occurrence_id)["ok"]
        assert backend.growthStatus["points"] == 10
        assert backend.tasksReopen(task_id, occurrence_id)["ok"]
        assert backend.growthStatus["points"] == 0
        assert not backend.tasksUpdate(task_id, {"dueLocal": "随便写的时间"})["ok"]
        assert backend.tasks.get(task_id)["title"] == "对照实验"
        assert backend.tasksSetArchived(task_id, True)["ok"]
        assert len(backend.archivedTaskItems) == 1 and not backend.taskItems
        assert not backend.focusStartTask(task_id, 25)["ok"]
        assert backend.tasksSetArchived(task_id, False)["ok"]
        assert backend.taskItems[0]["id"] == task_id
    finally:
        backend.shutdown()
