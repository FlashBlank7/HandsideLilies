"""Deterministic local reminders for cached Google Calendar events.

The connector owns network synchronization.  This module only consumes the
already cached rolling window and mirrors future event starts into Lilies'
local :class:`ReminderScheduler`.  The persistent fingerprint map makes the
operation safe to replay after every incremental sync and after a restart.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.database import Database
from ..core.productivity import ReminderScheduler


_STATE_SETTING = "connector_calendar_local_reminders_v1"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_start(value: Mapping[str, Any]) -> datetime | None:
    raw = str(value.get("occurredAt", "")).strip()
    if not raw:
        return None
    timezone_name = str(value.get("timeZone", "")).strip()
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
            try:
                zone = ZoneInfo(timezone_name) if timezone_name else datetime.now().astimezone().tzinfo
            except ZoneInfoNotFoundError:
                zone = datetime.now().astimezone().tzinfo
            return datetime.combine(day, time(hour=9), tzinfo=zone).astimezone(UTC)
        normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            try:
                zone = ZoneInfo(timezone_name) if timezone_name else datetime.now().astimezone().tzinfo
            except ZoneInfoNotFoundError:
                zone = datetime.now().astimezone().tzinfo
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _lead_minutes(value: Mapping[str, Any], default: int) -> int:
    reminders = value.get("reminders")
    if not isinstance(reminders, Mapping):
        return default
    overrides = reminders.get("overrides")
    if not isinstance(overrides, list):
        return default
    candidates: list[int] = []
    for item in overrides:
        if not isinstance(item, Mapping) or str(item.get("method", "")) != "popup":
            continue
        try:
            minutes = int(item.get("minutes", default))
        except (TypeError, ValueError):
            continue
        if 0 <= minutes <= 40_320:
            candidates.append(minutes)
    return min(candidates) if candidates else default


def _fingerprint(remote_id: str, start: datetime, lead: int) -> str:
    payload = json.dumps(
        [remote_id, start.isoformat(), int(lead)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CalendarReminderBridge:
    """Replay-safe projection from cached events to local reminders.

    No Google request is made here.  Event bodies are never copied into a
    reminder; at the metadata retention tier the title remains deliberately
    generic because the cached item contains no summary.
    """

    def __init__(
        self,
        database: Database,
        reminders: ReminderScheduler,
        *,
        now: Callable[[], datetime] | None = None,
        default_lead_minutes: int = 10,
    ) -> None:
        self.database = database
        self.reminders = reminders
        self.now = now or _utc_now
        self.default_lead_minutes = max(0, min(int(default_lead_minutes), 40_320))
        self._sanitize_tracked_reminders()

    def _tracked_state(self) -> dict[str, dict[str, Any]]:
        raw_state = self.database.get_setting(_STATE_SETTING, {})
        if not isinstance(raw_state, dict):
            return {}
        return {
            str(remote_id): dict(value)
            for remote_id, value in raw_state.items()
            if isinstance(value, dict)
        }

    def _sanitize_tracked_reminders(self) -> None:
        """Remove titles copied by pre-release builds from the plaintext DB."""

        reminder_ids = [
            str(value.get("reminderId", ""))
            for value in self._tracked_state().values()
            if value.get("reminderId")
        ]
        if not reminder_ids:
            return
        placeholders = ",".join("?" for _ in reminder_ids)
        with self.database.connect() as db:
            db.execute(
                f"""UPDATE reminders SET title=?,body=?,updated_at=?
                    WHERE reminder_id IN ({placeholders})""",
                (
                    "日程即将开始",
                    "来自 Google Calendar 的本地提醒。打开日程可查看完整内容。",
                    _utc_now().isoformat(),
                    *reminder_ids,
                ),
            )

    def clear(self) -> dict[str, int]:
        """Cancel every connector-derived reminder and forget its mapping."""

        state = self._tracked_state()
        removed = 0
        for value in state.values():
            reminder_id = str(value.get("reminderId", ""))
            if reminder_id and self.reminders.delete(reminder_id):
                removed += 1
        self.database.set_setting(_STATE_SETTING, {})
        return {"remindersDeleted": removed, "trackedCleared": len(state)}

    def reconcile(self, items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
        state = self._tracked_state()
        created = 0
        replaced = 0
        cancelled = 0
        retained_ids: set[str] = set()

        for raw_item in items:
            item = dict(raw_item)
            remote_id = str(item.get("id", item.get("remoteId", ""))).strip()
            if not remote_id:
                continue
            retained_ids.add(remote_id)
            existing = state.get(remote_id)
            existing = dict(existing) if isinstance(existing, dict) else {}
            if str(item.get("state", "")).casefold() == "cancelled":
                if existing.get("reminderId"):
                    self.reminders.delete(str(existing["reminderId"]))
                    cancelled += 1
                state.pop(remote_id, None)
                continue

            start = _parse_start(item)
            if start is None or start <= current - timedelta(days=1):
                state.pop(remote_id, None)
                continue
            lead = _lead_minutes(item, self.default_lead_minutes)
            fire_at = start - timedelta(minutes=lead)
            if fire_at <= current < start:
                fire_at = current
            if start <= current:
                state.pop(remote_id, None)
                continue
            # ReminderScheduler is intentionally plaintext and therefore never
            # receives a Calendar title, even at an encrypted retention tier.
            # A future delivery UI may decrypt the selected item ephemerally.
            title = "日程即将开始"
            fingerprint = _fingerprint(remote_id, start, lead)
            if existing.get("fingerprint") == fingerprint and existing.get("reminderId"):
                continue
            old_id = str(existing.get("reminderId", ""))
            if old_id:
                self.reminders.delete(old_id)
                replaced += 1
            saved = self.reminders.create(
                title,
                fire_at,
                body="来自 Google Calendar 的本地提醒。打开日程可查看完整内容。",
                timezone_name="UTC",
            )
            state[remote_id] = {
                "reminderId": str(saved["reminder_id"]),
                "fingerprint": fingerprint,
                "startAt": start.isoformat(),
            }
            created += 1

        # Entries older than the rolling window cannot fire again.  Future
        # entries absent from a partial incremental response are intentionally
        # retained; only an explicit cancelled item removes them immediately.
        for remote_id, raw in list(state.items()):
            entry = dict(raw) if isinstance(raw, dict) else {}
            try:
                start = datetime.fromisoformat(str(entry.get("startAt", "")))
                if start.tzinfo is None:
                    start = start.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                state.pop(remote_id, None)
                continue
            if start.astimezone(UTC) <= current - timedelta(days=1):
                state.pop(remote_id, None)

        self.database.set_setting(_STATE_SETTING, state)
        return {
            "created": created,
            "replaced": replaced,
            "cancelled": cancelled,
            "tracked": len(state),
        }


__all__ = ["CalendarReminderBridge"]
