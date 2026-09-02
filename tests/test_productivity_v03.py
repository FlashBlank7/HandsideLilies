# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from lilies.core.database import Database
from lilies.core.productivity import (
    BoxWorldService,
    EventOutbox,
    FocusService,
    GrowthEngine,
    NarrativeDirector,
    ReadingSessionService,
    ReminderScheduler,
    TaskService,
    WardrobeService,
)


@dataclass
class Clock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **changes: int) -> None:
        self.current += timedelta(**changes)


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 8, 29, 1, 0, tzinfo=UTC))


def services(tmp_path, clock: Clock):
    database = Database(tmp_path / "lilies.db")
    growth = GrowthEngine(database, now=clock, timezone_name="UTC")
    return database, growth, TaskService(database, growth=growth, now=clock)


def test_task_completion_is_idempotent_and_reopen_uses_compensation(tmp_path, clock) -> None:
    _database, growth, tasks = services(tmp_path, clock)
    task = tasks.create("整理书桌", category="daily")

    first = tasks.complete(task["task_id"])
    duplicate = tasks.complete(task["task_id"])

    assert first["alreadyCompleted"] is False
    assert duplicate["alreadyCompleted"] is True
    assert growth.status()["totalPoints"] == 10
    occurrence_id = first["occurrence"]["occurrence_id"]

    reopened = tasks.reopen(task["task_id"], occurrence_id)
    assert reopened["compensation"]["points"] == -10
    assert growth.status()["totalPoints"] == 0
    assert tasks.reopen(task["task_id"], occurrence_id)["alreadyOpen"] is True

    completed_again = tasks.complete(task["task_id"], occurrence_id)
    assert completed_again["occurrence"]["completion_version"] == 2
    assert growth.status()["totalPoints"] == 10
    history = growth.history()
    assert sorted(value["points"] for value in history) == [-10, 10, 10]
    assert len({value["event_id"] for value in history}) == 3


def test_daily_cap_and_per_activity_caps_are_deterministic(tmp_path, clock) -> None:
    _database, growth, tasks = services(tmp_path, clock)
    for index in range(7):
        task = tasks.create(f"事项 {index}")
        tasks.complete(task["task_id"])

    assert growth.status()["totalPoints"] == 60
    task_events = [value for value in growth.history() if value["event_kind"] == "task-complete"]
    assert len(task_events) == 7
    assert sorted(value["points"] for value in task_events) == [0, 10, 10, 10, 10, 10, 10]

    clock.advance(days=1)
    tomorrow = tasks.create("第二天的事项")
    tasks.complete(tomorrow["task_id"])
    assert growth.status()["totalPoints"] == 70


def test_compensated_task_does_not_count_toward_future_unlock(tmp_path, clock) -> None:
    _database, growth, tasks = services(tmp_path, clock)
    corrected = tasks.create("误勾选", category="daily")
    completion = tasks.complete(corrected["task_id"])
    tasks.reopen(corrected["task_id"], completion["occurrence"]["occurrence_id"])

    for title in ("洗杯子", "整理文件"):
        task = tasks.create(title, category="daily")
        tasks.complete(task["task_id"])
    assert "outfit:home-cardigan" not in {value["item_key"] for value in growth.unlocks()}

    third_real = tasks.create("清理桌面", category="daily")
    tasks.complete(third_real["task_id"])
    assert "outfit:home-cardigan" in {value["item_key"] for value in growth.unlocks()}


def test_focus_units_cap_at_four_per_day_and_three_sessions_unlock_workbench(
    tmp_path, clock
) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    focus = FocusService(database, growth=growth, now=clock)

    for _index in range(5):
        session = focus.start(minutes=25)
        clock.advance(minutes=25)
        focus.finish(session["session_id"])

    focus_units = [value for value in growth.history() if value["event_kind"] == "focus-unit"]
    assert len(focus_units) == 5
    assert sum(value["points"] for value in focus_units) == 32
    assert "outfit:focus-coat" in {value["item_key"] for value in growth.unlocks()}
    assert BoxWorldService(database).inspect("workbench")["unlocked"] is True

    final = focus.status(session["session_id"])
    assert final is not None and final["state"] == "finished"
    assert focus.finish(session["session_id"])["alreadyFinished"] is True
    assert sum(value["points"] for value in growth.history()) == 32


@pytest.mark.parametrize(
    ("minutes", "expected_units"),
    ((5, 0), (25, 1), (50, 2)),
)
def test_focus_finish_caps_long_timer_gap_at_planned_duration(
    tmp_path, clock, minutes: int, expected_units: int
) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    focus = FocusService(database, growth=growth, now=clock)
    session = focus.start(minutes=minutes)
    clock.advance(hours=8)

    first = focus.finish(session["session_id"])
    history_before_duplicate = growth.history()
    duplicate = focus.finish(session["session_id"])

    assert first["session"]["active_seconds"] == minutes * 60
    assert duplicate["alreadyFinished"] is True
    assert duplicate["growth"] == []
    assert growth.history() == history_before_duplicate
    units = [
        value for value in history_before_duplicate
        if value["event_kind"] == "focus-unit"
    ]
    assert len(units) == expected_units
    assert sum(value["points"] for value in units) == expected_units * 8


def test_focus_running_and_paused_sessions_restore_without_time_regression(
    tmp_path, clock
) -> None:
    database_path = tmp_path / "lilies.db"
    database = Database(database_path)
    growth = GrowthEngine(database, now=clock, timezone_name="UTC")
    focus = FocusService(database, growth=growth, now=clock)
    session = focus.start(minutes=50)
    clock.advance(minutes=10)

    restarted_database = Database(database_path)
    restarted_growth = GrowthEngine(
        restarted_database, now=clock, timezone_name="UTC"
    )
    restarted = FocusService(
        restarted_database, growth=restarted_growth, now=clock
    )
    running = restarted.status()
    assert running is not None
    assert running["session_id"] == session["session_id"]
    assert running["state"] == "running"
    assert running["live_active_seconds"] == 600

    paused = restarted.pause(session["session_id"])
    assert paused["active_seconds"] == 600
    clock.advance(hours=3)
    paused_after_restart = FocusService(
        Database(database_path), growth=restarted_growth, now=clock
    ).status()
    assert paused_after_restart is not None
    assert paused_after_restart["state"] == "paused"
    assert paused_after_restart["live_active_seconds"] == 600

    restarted.resume(session["session_id"])
    clock.advance(minutes=45)
    finished = restarted.finish(session["session_id"])
    assert finished["session"]["active_seconds"] == 3000
    assert len([
        value for value in restarted_growth.history()
        if value["event_kind"] == "focus-unit"
    ]) == 2


def test_focus_finish_caps_an_already_overdue_paused_session(
    tmp_path, clock
) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    focus = FocusService(database, growth=growth, now=clock)
    session = focus.start(minutes=25)
    clock.advance(minutes=30)
    paused = focus.pause(session["session_id"])
    assert paused["active_seconds"] == 1800

    finished = focus.finish(session["session_id"])

    assert finished["session"]["active_seconds"] == 1500
    units = [
        value for value in growth.history() if value["event_kind"] == "focus-unit"
    ]
    assert len(units) == 1
    assert units[0]["points"] == 8


def test_reading_completion_unlocks_wardrobe_world_and_narrative(tmp_path, clock) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    reading = ReadingSessionService(database, growth=growth, now=clock)
    session = reading.start(title="Attention Is All You Need", source="paper.pdf")
    clock.advance(minutes=20)

    result = reading.finish(session["session_id"])

    assert [value["points"] for value in result["growth"]] == [0, 6]
    assert growth.status()["totalPoints"] == 6
    wardrobe = WardrobeService(database)
    inventory = wardrobe.list()
    assert next(value for value in inventory["outfits"] if value["id"] == "reading-smock")["unlocked"]
    assert next(value for value in inventory["poses"] if value["id"] == "reading")["unlocked"]
    assert wardrobe.equip(outfit_id="reading-smock", pose_id="reading")["pose_id"] == "reading"
    with pytest.raises(PermissionError):
        wardrobe.equip(outfit_id="rest-nightdress", pose_id="resting")

    world = BoxWorldService(database)
    assert world.inspect("paper-shelf")["unlocked"] is True
    assert world.place("paper-shelf", x=0.2, y=0.8)["position"] == {"x": 0.2, "y": 0.8}
    narrative = NarrativeDirector(database, now=clock)
    pending = narrative.pending()
    assert any(value["event_key"] == "unlock:first-reading" for value in pending)
    acknowledged = narrative.acknowledge(pending[0]["narrative_id"])
    assert acknowledged["status"] == "acknowledged"
    assert narrative.replay(acknowledged["narrative_id"])["status"] == "pending"


def test_cancelled_reading_keeps_elapsed_time_but_never_rewards(tmp_path, clock) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    reading = ReadingSessionService(database, growth=growth, now=clock)
    session = reading.start(title="草稿")
    clock.advance(minutes=25)

    cancelled = reading.cancel(session["session_id"])

    assert cancelled["state"] == "cancelled"
    assert cancelled["active_seconds"] == 1500
    assert growth.status()["totalPoints"] == 0
    assert not any(value["event_kind"].startswith("reading") for value in growth.history())


def test_three_real_rests_unlock_without_adding_or_removing_points(tmp_path, clock) -> None:
    database, growth, _tasks = services(tmp_path, clock)
    focus = FocusService(database, growth=growth, now=clock)

    for _index in range(3):
        session = focus.start(minutes=5)
        clock.advance(minutes=5)
        focus.finish(session["session_id"], outcome="rest")

    assert growth.status()["totalPoints"] == 0
    assert "outfit:rest-nightdress" in {value["item_key"] for value in growth.unlocks()}
    assert "pose:resting" in {value["item_key"] for value in growth.unlocks()}


def test_recurring_task_keeps_local_wall_clock_across_dst(tmp_path, clock) -> None:
    pytest.importorskip("tzdata")
    _database, _growth, tasks = services(tmp_path, clock)
    task = tasks.create(
        "晨间整理",
        due_at="2027-03-13T09:00:00",
        timezone_name="America/New_York",
        recurrence={"frequency": "daily", "interval": 1},
    )

    completed = tasks.complete(task["task_id"])

    assert completed["task"]["status"] == "open"
    pending = next(value for value in completed["task"]["occurrences"] if value["status"] == "pending")
    # DST starts on March 14, so the UTC hour changes while 09:00 local stays stable.
    assert pending["scheduled_for"] == "2027-03-14T13:00:00+00:00"


def test_reminders_claim_each_schedule_once_and_advance_recurrence(tmp_path, clock) -> None:
    database, _growth, _tasks = services(tmp_path, clock)
    reminders = ReminderScheduler(database, now=clock)
    one_off = reminders.create("喝水", clock.current)
    daily = reminders.create(
        "查看日历", clock.current, recurrence={"frequency": "daily", "interval": 1}
    )

    first = reminders.claim_due(channel="bubble")
    assert {value["reminder_id"] for value in first} == {
        one_off["reminder_id"],
        daily["reminder_id"],
    }
    assert reminders.claim_due(channel="bubble") == []
    assert reminders.mark_delivery(first[0]["deliveryId"], delivered=True)["status"] == "delivered"
    assert next(value for value in reminders.list() if value["reminder_id"] == one_off["reminder_id"])["state"] == "completed"

    clock.advance(days=1)
    second = reminders.claim_due(channel="bubble")
    assert [value["reminder_id"] for value in second] == [daily["reminder_id"]]
    assert second[0]["scheduledFor"] != next(
        value["scheduledFor"] for value in first if value["reminder_id"] == daily["reminder_id"]
    )


@pytest.mark.parametrize("terminal_state", ("completed", "dismissed"))
def test_terminal_reminder_cannot_be_resurrected_by_snooze(
    tmp_path, clock, terminal_state: str
) -> None:
    database, _growth, _tasks = services(tmp_path, clock)
    reminders = ReminderScheduler(database, now=clock)
    reminder = reminders.create("只处理一次", clock.current)
    reminder_id = reminder["reminder_id"]

    if terminal_state == "completed":
        assert reminders.claim_due(channel="bubble")[0]["reminder_id"] == reminder_id
    else:
        reminders.dismiss(reminder_id)

    with pytest.raises(ValueError, match="only a pending reminder"):
        reminders.snooze(reminder_id, 10)
    saved = next(
        value for value in reminders.list() if value["reminder_id"] == reminder_id
    )
    assert saved["state"] == terminal_state
    assert saved["snoozed_until"] is None


def test_outbox_retries_committed_growth_events_without_duplication(tmp_path, clock) -> None:
    database, _growth, tasks = services(tmp_path, clock)
    task = tasks.create("提交实验记录")
    tasks.complete(task["task_id"])
    outbox = EventOutbox(database, now=clock)

    pending = outbox.pending()
    growth_event = next(value for value in pending if value["topic"] == "growth.recorded")
    assert growth_event["payload"]["points"] == 10
    assert outbox.failed(growth_event["outbox_id"], "temporary", retry_seconds=5)
    assert all(value["outbox_id"] != growth_event["outbox_id"] for value in outbox.pending())
    clock.advance(seconds=5)
    assert any(value["outbox_id"] == growth_event["outbox_id"] for value in outbox.pending())
    assert outbox.delivered(growth_event["outbox_id"])
    assert all(value["outbox_id"] != growth_event["outbox_id"] for value in outbox.pending())
