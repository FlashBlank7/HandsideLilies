"""Translate explicit task-editor fields, without model inference or side effects."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def task_changes(value: Mapping[str, Any], *, default_timezone: str = "UTC") -> dict[str, Any]:
    allowed = {"title", "note", "category", "priority", "dueAt", "dueLocal", "timezone", "recurrence"}
    if set(value) - allowed:
        raise ValueError("任务包含不支持的设置")
    changes = {key: value[key] for key in ("title", "note", "category", "recurrence") if key in value}
    if "priority" in value:
        priority = value["priority"]
        changes["priority"] = {"low": 0, "normal": 1, "high": 2, "critical": 3}.get(str(priority), priority)
    zone_name = str(value.get("timezone") or default_timezone)
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("时区没有找到，请使用 Asia/Shanghai 等时区名称") from exc
    if "timezone" in value:
        changes["timezone_name"] = zone_name
    if "dueAt" in value and "dueLocal" in value:
        raise ValueError("截止时间重复，请只填写一种")
    if "dueAt" in value:
        changes["due_at"] = value["dueAt"] or None
    if "dueLocal" in value:
        text = str(value["dueLocal"]).strip()
        if not text:
            changes["due_at"] = None
        else:
            try:
                local = datetime.strptime(text, "%Y-%m-%d %H:%M")
            except ValueError as exc:
                raise ValueError("时间请写成 2026-10-04 18:00，留空则不设截止时间") from exc
            aware = local.replace(tzinfo=zone)
            if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local:
                raise ValueError("夏令时切换跳过了这个时间，请另选一个时间")
            if local.replace(tzinfo=zone, fold=0).utcoffset() != local.replace(tzinfo=zone, fold=1).utcoffset():
                raise ValueError("夏令时切换让这个时间出现两次，请另选一个明确时间")
            changes["due_at"] = aware.isoformat()
    return changes
