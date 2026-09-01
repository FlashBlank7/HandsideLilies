from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from lilies.connectors.calendar_reminders import CalendarReminderBridge
from lilies.core.database import Database
from lilies.core.productivity import ReminderScheduler


def test_calendar_reminders_are_replay_safe_and_replace_changed_events(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    bridge = CalendarReminderBridge(database, reminders, now=lambda: now)
    event = {
        "id": "event-1",
        "occurredAt": (now + timedelta(hours=2)).isoformat(),
        "summary": "组会",
        "state": "confirmed",
    }

    assert bridge.reconcile([event])["created"] == 1
    assert bridge.reconcile([event])["created"] == 0
    assert len(reminders.list()) == 1
    assert reminders.list()[0]["title"] == "日程即将开始"
    assert "组会" not in str(reminders.list()[0])

    changed = {**event, "occurredAt": (now + timedelta(hours=3)).isoformat()}
    result = bridge.reconcile([changed])
    assert result["created"] == 1
    assert result["replaced"] == 1
    assert len(reminders.list()) == 1


def test_calendar_metadata_reminder_does_not_invent_or_store_event_body(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    bridge = CalendarReminderBridge(database, reminders, now=lambda: now)
    bridge.reconcile(
        [
            {
                "id": "metadata-only",
                "occurredAt": (now + timedelta(hours=1)).isoformat(),
                "state": "confirmed",
            }
        ]
    )

    saved = reminders.list()[0]
    assert saved["title"] == "日程即将开始"
    assert "metadata-only" not in saved["body"]


def test_calendar_cancelled_event_removes_pending_local_reminder(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    bridge = CalendarReminderBridge(database, reminders, now=lambda: now)
    bridge.reconcile(
        [{"id": "event-1", "occurredAt": (now + timedelta(hours=2)).isoformat()}]
    )

    result = bridge.reconcile(
        [
            {
                "id": "event-1",
                "occurredAt": (now + timedelta(hours=2)).isoformat(),
                "state": "cancelled",
            }
        ]
    )
    assert result["cancelled"] == 1
    assert reminders.list() == []


def test_calendar_popup_override_controls_local_fire_time(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    bridge = CalendarReminderBridge(database, reminders, now=lambda: now)
    start = now + timedelta(hours=2)
    bridge.reconcile(
        [
            {
                "id": "event-1",
                "occurredAt": start.isoformat(),
                "reminders": {"overrides": [{"method": "popup", "minutes": 30}]},
            }
        ]
    )

    assert reminders.list()[0]["fire_at"] == (start - timedelta(minutes=30)).isoformat()


def test_calendar_clear_removes_connector_reminders_and_mapping(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    bridge = CalendarReminderBridge(database, reminders, now=lambda: now)
    bridge.reconcile(
        [{"id": "event-private", "occurredAt": (now + timedelta(hours=2)).isoformat()}]
    )

    result = bridge.clear()

    assert result == {"remindersDeleted": 1, "trackedCleared": 1}
    assert reminders.list() == []
    assert database.get_setting("connector_calendar_local_reminders_v1", {}) == {}


def test_bridge_sanitizes_titles_left_by_an_older_build(tmp_path: Path) -> None:
    now = datetime(2026, 8, 29, 3, 0, tzinfo=UTC)
    database = Database(tmp_path / "lilies.db")
    reminders = ReminderScheduler(database, now=lambda: now)
    saved = reminders.create(
        "日程 · private legacy title",
        now + timedelta(hours=1),
        body="legacy content",
    )
    database.set_setting(
        "connector_calendar_local_reminders_v1",
        {
            "event-old": {
                "reminderId": saved["reminder_id"],
                "fingerprint": "old",
                "startAt": (now + timedelta(hours=1)).isoformat(),
            }
        },
    )

    CalendarReminderBridge(database, reminders, now=lambda: now)

    sanitized = reminders.list()[0]
    assert sanitized["title"] == "日程即将开始"
    assert "private legacy title" not in str(sanitized)
    assert "legacy content" not in str(sanitized)
