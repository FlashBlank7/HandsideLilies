# -*- coding: utf-8 -*-
from __future__ import annotations

import calendar
import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import Database
from .themes import ThemeManifest


NowProvider = Callable[[], datetime]

STAGES: tuple[tuple[str, int], ...] = (
    ("初遇", 0),
    ("熟悉", 100),
    ("信赖", 300),
    ("亲近", 700),
    ("相伴", 1200),
)

OUTFIT_CATALOG: tuple[dict[str, Any], ...] = (
    {"id": "first-encounter", "name": "初遇裂纹裙"},
    {"id": "summer-cotton-dress", "name": "清凉棉质连衣裙"},
    {"id": "home-cardigan", "name": "家居开衫"},
    {"id": "reading-smock", "name": "阅读罩衫"},
    {"id": "focus-coat", "name": "专注外套"},
    {"id": "rest-nightdress", "name": "休息睡裙"},
)

POSE_CATALOG: tuple[dict[str, str], ...] = (
    {"id": "idle-prayer", "name": "抱拳祈祷"},
    {"id": "perch-top", "name": "窗口栖息"},
    {"id": "edge-peek", "name": "屏幕探身"},
    {"id": "listening", "name": "倾听"},
    {"id": "reading", "name": "论文阅读"},
    {"id": "presenting", "name": "展示说明"},
    {"id": "box-support", "name": "红绳托盒"},
    {"id": "resting", "name": "休息"},
)

WORLD_CATALOG: dict[str, tuple[str, str]] = {
    "box-core": ("room", "莉莉丝的盒子"),
    "paper-shelf": ("furniture", "纸页架"),
    "workbench": ("furniture", "工作台"),
    "living-corner": ("room", "生活角"),
    "letter-rack": ("furniture", "信笺架"),
    "rest-cushion": ("furniture", "休息软垫"),
}

WORLD_UNLOCK_HINTS: dict[str, str] = {
    "box-core": "初始空间",
    "paper-shelf": "完成一次完整的论文阅读",
    "workbench": "完成三段专注",
    "living-corner": "完成三件日常事项",
    "letter-rack": "完成一次 Slack 整理",
    "rest-cushion": "完成三次主动休息",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _now(provider: NowProvider) -> datetime:
    return _utc(provider())


def _now_iso(provider: NowProvider) -> str:
    return _now(provider).isoformat()


def _clean(value: Any, *, maximum: int, field: str, required: bool = False) -> str:
    result = " ".join(str(value or "").replace("\x00", " ").split())
    if required and not result:
        raise ValueError(f"{field} cannot be empty")
    return result[:maximum]


def _timezone(name: str) -> tzinfo:
    if str(name or "UTC").casefold() in {"utc", "etc/utc", "z"}:
        return UTC
    try:
        return ZoneInfo(str(name or "UTC"))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def _instant(value: datetime | str, timezone_name: str = "UTC") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            raise ValueError("time cannot be empty")
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"invalid ISO time: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone(timezone_name))
    return parsed.astimezone(UTC)


def _iso(value: datetime | str, timezone_name: str = "UTC") -> str:
    return _instant(value, timezone_name).isoformat()


def _json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _decode_json(value: Any, default: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return default


def _row(row: sqlite3.Row | None, *, json_fields: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        if field in result:
            result[field.removesuffix("_json")] = _decode_json(result.pop(field), {})
    return result


def _recurrence(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    frequency = str(value.get("frequency", "")).casefold()
    if frequency not in {"daily", "weekly", "monthly"}:
        raise ValueError("recurrence frequency must be daily, weekly or monthly")
    try:
        interval = int(value.get("interval", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("recurrence interval must be an integer") from exc
    if not 1 <= interval <= 365:
        raise ValueError("recurrence interval must be between 1 and 365")
    return {"frequency": frequency, "interval": interval}


def _next_occurrence(
    current: datetime | str,
    recurrence: Mapping[str, Any],
    timezone_name: str,
) -> datetime:
    rule = _recurrence(recurrence)
    if not rule:
        raise ValueError("a recurrence rule is required")
    zone = _timezone(timezone_name)
    local = _instant(current, timezone_name).astimezone(zone)
    interval = int(rule["interval"])
    if rule["frequency"] == "daily":
        result = local + timedelta(days=interval)
    elif rule["frequency"] == "weekly":
        result = local + timedelta(weeks=interval)
    else:
        month_index = local.year * 12 + local.month - 1 + interval
        year, zero_month = divmod(month_index, 12)
        month = zero_month + 1
        day = min(local.day, calendar.monthrange(year, month)[1])
        result = local.replace(year=year, month=month, day=day)
    return result.astimezone(UTC)


def _elapsed(started_at: str | None, current: datetime) -> int:
    if not started_at:
        return 0
    return max(0, int((current - _instant(started_at)).total_seconds()))


class GrowthEngine:
    """Append-only, deterministic progression. No public arbitrary grant API."""

    DAILY_POINT_CAP = 60
    _RULE_LIMITS = {"focus-unit": 4, "reading-unit": 3, "slack-cleanup": 3}

    def __init__(
        self,
        database: Database,
        *,
        now: NowProvider | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))
        configured = timezone_name or database.get_setting("growth_timezone", "UTC")
        self.timezone_name = str(configured)
        _timezone(self.timezone_name)

    def status(self) -> dict[str, Any]:
        with self.database.connect() as db:
            state = db.execute("SELECT * FROM growth_state WHERE state_id='default'").fetchone()
        assert state is not None
        total = int(state["total_points"])
        stage = str(state["stage"])
        index = next((i for i, item in enumerate(STAGES) if item[0] == stage), 0)
        current_floor = STAGES[index][1]
        next_stage = STAGES[index + 1] if index + 1 < len(STAGES) else None
        if next_stage is None:
            progress = 1.0
            remaining = 0
        else:
            span = next_stage[1] - current_floor
            progress = min(1.0, max(0.0, (total - current_floor) / span))
            remaining = max(0, next_stage[1] - total)
        return {
            "totalPoints": total,
            "stage": stage,
            "stageFloor": current_floor,
            "nextStage": next_stage[0] if next_stage else "",
            "nextAt": next_stage[1] if next_stage else None,
            "remaining": remaining,
            "progress": progress,
            "updatedAt": str(state["updated_at"]),
        }

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(0, min(int(limit), 500))
        if bounded == 0:
            return []
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM growth_events ORDER BY occurred_at DESC,event_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return [self._event_mapping(value) for value in rows]

    def unlocks(self, kind: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM unlocks"
        parameters: tuple[Any, ...] = ()
        if kind:
            query += " WHERE item_kind=?"
            parameters = (str(kind),)
        query += " ORDER BY unlocked_at,item_key"
        with self.database.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [self._unlock_mapping(value) for value in rows]

    def record_slack_cleanup(
        self,
        source_id: str,
        *,
        handled_count: int = 0,
        reply_sent: bool = False,
    ) -> dict[str, Any] | None:
        if int(handled_count) < 3 and not reply_sent:
            return None
        current = _now(self.now)
        with self.database.connect() as db:
            return self._record(
                db,
                idempotency_key=f"slack:{source_id}",
                event_kind="slack-cleanup",
                source_kind="slack-session",
                source_id=str(source_id),
                requested_points=5,
                metadata={"handledCount": max(0, int(handled_count)), "replySent": bool(reply_sent)},
                occurred_at=current,
            )

    def record_rest(self, source_id: str, active_seconds: int) -> dict[str, Any] | None:
        if int(active_seconds) < 300:
            return None
        current = _now(self.now)
        with self.database.connect() as db:
            return self._record(
                db,
                idempotency_key=f"rest:{source_id}",
                event_kind="rest-session",
                source_kind="focus-session",
                source_id=str(source_id),
                requested_points=0,
                metadata={"activeSeconds": int(active_seconds)},
                occurred_at=current,
            )

    def _record_task_completion(
        self,
        db: sqlite3.Connection,
        *,
        occurrence_id: str,
        completion_version: int,
        category: str,
        occurred_at: datetime,
    ) -> dict[str, Any]:
        return self._record(
            db,
            idempotency_key=f"task:{occurrence_id}:v{completion_version}",
            event_kind="task-complete",
            source_kind="task-occurrence",
            source_id=occurrence_id,
            requested_points=10,
            metadata={"category": category, "completionVersion": completion_version},
            occurred_at=occurred_at,
        )

    def _record_focus_completion(
        self,
        db: sqlite3.Connection,
        *,
        session_id: str,
        active_seconds: int,
        planned_seconds: int,
        occurred_at: datetime,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if active_seconds >= min(planned_seconds, 1500):
            results.append(
                self._record(
                    db,
                    idempotency_key=f"focus-session:{session_id}",
                    event_kind="focus-session",
                    source_kind="focus-session",
                    source_id=session_id,
                    requested_points=0,
                    metadata={"activeSeconds": active_seconds, "plannedSeconds": planned_seconds},
                    occurred_at=occurred_at,
                )
            )
        for unit in range(active_seconds // 1500):
            results.append(
                self._record(
                    db,
                    idempotency_key=f"focus:{session_id}:unit:{unit + 1}",
                    event_kind="focus-unit",
                    source_kind="focus-session",
                    source_id=session_id,
                    requested_points=8,
                    metadata={"unit": unit + 1, "activeSeconds": active_seconds},
                    occurred_at=occurred_at,
                )
            )
        return results

    def _record_reading_completion(
        self,
        db: sqlite3.Connection,
        *,
        session_id: str,
        active_seconds: int,
        occurred_at: datetime,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if active_seconds >= 1200:
            results.append(
                self._record(
                    db,
                    idempotency_key=f"reading-session:{session_id}",
                    event_kind="reading-session",
                    source_kind="reading-session",
                    source_id=session_id,
                    requested_points=0,
                    metadata={"activeSeconds": active_seconds},
                    occurred_at=occurred_at,
                )
            )
        for unit in range(active_seconds // 1200):
            results.append(
                self._record(
                    db,
                    idempotency_key=f"reading:{session_id}:unit:{unit + 1}",
                    event_kind="reading-unit",
                    source_kind="reading-session",
                    source_id=session_id,
                    requested_points=6,
                    metadata={"unit": unit + 1, "activeSeconds": active_seconds},
                    occurred_at=occurred_at,
                )
            )
        return results

    def _compensate_key(
        self,
        db: sqlite3.Connection,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> dict[str, Any] | None:
        target = db.execute(
            "SELECT * FROM growth_events WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if target is None:
            return None
        existing = db.execute(
            "SELECT * FROM growth_events WHERE compensation_for=?", (target["event_id"],)
        ).fetchone()
        if existing is not None:
            return self._event_mapping(existing)
        result = self._record(
            db,
            idempotency_key=f"compensate:{target['event_id']}",
            event_kind=f"{target['event_kind']}-compensation",
            source_kind=str(target["source_kind"]),
            source_id=str(target["source_id"]),
            requested_points=-int(target["points"]),
            metadata={"reason": "source-reopened"},
            occurred_at=occurred_at,
            compensation_for=str(target["event_id"]),
            apply_caps=False,
        )
        return result

    def _record(
        self,
        db: sqlite3.Connection,
        *,
        idempotency_key: str,
        event_kind: str,
        source_kind: str,
        source_id: str,
        requested_points: int,
        metadata: Mapping[str, Any],
        occurred_at: datetime,
        compensation_for: str | None = None,
        apply_caps: bool = True,
    ) -> dict[str, Any]:
        existing = db.execute(
            "SELECT * FROM growth_events WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing is not None:
            result = self._event_mapping(existing)
            result["created"] = False
            return result

        points = int(requested_points)
        if apply_caps and points > 0:
            points = self._capped_points(db, event_kind, points, occurred_at)
        event_id = uuid.uuid4().hex
        current_iso = occurred_at.isoformat()
        db.execute(
            """INSERT INTO growth_events
               (event_id,idempotency_key,event_kind,source_kind,source_id,points,requested_points,
                compensation_for,metadata_json,occurred_at,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                event_id,
                idempotency_key,
                event_kind,
                source_kind,
                str(source_id),
                points,
                int(requested_points),
                compensation_for,
                json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True),
                current_iso,
                _now_iso(self.now),
            ),
        )
        old_stage, new_stage = self._refresh_projection(db, occurred_at)
        self._evaluate_unlocks(db, event_id, occurred_at)
        if new_stage != old_stage:
            self._create_narrative(
                db,
                event_key=f"stage:{new_stage}",
                kind="stage",
                title=f"共鸣抵达「{new_stage}」",
                body="莉莉丝与你共同经历的事情，被安静地留在了盒中。",
                source_event_id=event_id,
                occurred_at=occurred_at,
            )
        self._outbox(
            db,
            idempotency_key=f"growth:{event_id}",
            topic="growth.recorded",
            payload={"eventId": event_id, "kind": event_kind, "points": points},
            occurred_at=occurred_at,
        )
        saved = db.execute("SELECT * FROM growth_events WHERE event_id=?", (event_id,)).fetchone()
        assert saved is not None
        result = self._event_mapping(saved)
        result["created"] = True
        return result

    def _capped_points(
        self,
        db: sqlite3.Connection,
        event_kind: str,
        requested: int,
        occurred_at: datetime,
    ) -> int:
        zone = _timezone(self.timezone_name)
        local = occurred_at.astimezone(zone)
        start = datetime(local.year, local.month, local.day, tzinfo=zone).astimezone(UTC)
        end = (datetime(local.year, local.month, local.day, tzinfo=zone) + timedelta(days=1)).astimezone(UTC)
        if event_kind in self._RULE_LIMITS:
            count = db.execute(
                """SELECT COUNT(*) FROM growth_events
                   WHERE event_kind=? AND compensation_for IS NULL
                     AND occurred_at>=? AND occurred_at<?""",
                (event_kind, start.isoformat(), end.isoformat()),
            ).fetchone()[0]
            if int(count) >= self._RULE_LIMITS[event_kind]:
                return 0
        awarded = db.execute(
            """SELECT COALESCE(SUM(CASE WHEN points>0 THEN points ELSE 0 END),0)
               FROM growth_events WHERE occurred_at>=? AND occurred_at<?""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()[0]
        return max(0, min(requested, self.DAILY_POINT_CAP - int(awarded)))

    def _refresh_projection(
        self, db: sqlite3.Connection, occurred_at: datetime
    ) -> tuple[str, str]:
        state = db.execute("SELECT stage FROM growth_state WHERE state_id='default'").fetchone()
        old_stage = str(state["stage"]) if state else STAGES[0][0]
        total = max(0, int(db.execute("SELECT COALESCE(SUM(points),0) FROM growth_events").fetchone()[0]))
        points_stage = STAGES[0][0]
        for stage, threshold in STAGES:
            if total >= threshold:
                points_stage = stage
        old_index = next((i for i, item in enumerate(STAGES) if item[0] == old_stage), 0)
        new_index = next((i for i, item in enumerate(STAGES) if item[0] == points_stage), 0)
        # A correction can reduce the exact score, but never erases a reached
        # relationship stage or its narrative/unlocks.
        new_stage = STAGES[max(old_index, new_index)][0]
        db.execute(
            "UPDATE growth_state SET total_points=?,stage=?,updated_at=? WHERE state_id='default'",
            (total, new_stage, occurred_at.isoformat()),
        )
        return old_stage, new_stage

    def _evaluate_unlocks(
        self, db: sqlite3.Connection, source_event_id: str, occurred_at: datetime
    ) -> None:
        counts = {
            str(row["event_kind"]): int(row["amount"])
            for row in db.execute(
                """SELECT original.event_kind,COUNT(*) amount FROM growth_events original
                   WHERE original.compensation_for IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM growth_events correction
                       WHERE correction.compensation_for=original.event_id
                     )
                   GROUP BY original.event_kind"""
            ).fetchall()
        }
        daily_tasks = 0
        for value in db.execute(
            """SELECT original.metadata_json FROM growth_events original
               WHERE original.event_kind='task-complete' AND original.compensation_for IS NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM growth_events correction
                   WHERE correction.compensation_for=original.event_id
                 )"""
        ).fetchall():
            category = str(_decode_json(value["metadata_json"], {}).get("category", "")).casefold()
            if category in {"daily", "日常", "生活", "life"}:
                daily_tasks += 1
        state = db.execute("SELECT stage FROM growth_state WHERE state_id='default'").fetchone()
        stage = str(state["stage"]) if state else "初遇"
        rules: tuple[tuple[bool, str, tuple[tuple[str, str], ...], str], ...] = (
            (
                counts.get("reading-session", 0) >= 1,
                "first-reading",
                (("outfit", "reading-smock"), ("pose", "reading"), ("world", "paper-shelf")),
                "第一次完整的论文阅读，被收进了纸页架。",
            ),
            (
                counts.get("focus-session", 0) >= 3,
                "three-focus",
                (("outfit", "focus-coat"), ("world", "workbench")),
                "三段专注让盒中多出了一张安静的工作台。",
            ),
            (
                daily_tasks >= 3,
                "three-daily-tasks",
                (("outfit", "home-cardigan"), ("world", "living-corner")),
                "寻常的小事也有了可以安放的生活角。",
            ),
            (
                counts.get("slack-cleanup", 0) >= 1,
                "first-slack-cleanup",
                (("world", "letter-rack"), ("pose", "presenting")),
                "整理过的消息化作了盒中的信笺架。",
            ),
            (
                counts.get("rest-session", 0) >= 3,
                "three-rests",
                (("outfit", "rest-nightdress"), ("pose", "resting"), ("world", "rest-cushion")),
                "三次主动休息，让盒子记住了柔软的形状。",
            ),
            (
                next(i for i, item in enumerate(STAGES) if item[0] == stage) >= 1,
                "stage-familiar",
                (("outfit", "summer-cotton-dress"),),
                "熟悉之后，莉莉丝愿意换上更轻盈的白裙。",
            ),
        )
        for eligible, rule_id, items, narrative in rules:
            if not eligible:
                continue
            created_any = False
            for item_kind, item_id in items:
                created_any |= self._unlock(
                    db,
                    item_kind=item_kind,
                    item_id=item_id,
                    reason=rule_id,
                    source_event_id=source_event_id,
                    occurred_at=occurred_at,
                )
            if created_any:
                self._create_narrative(
                    db,
                    event_key=f"unlock:{rule_id}",
                    kind="unlock",
                    title="盒中有了新的变化",
                    body=narrative,
                    source_event_id=source_event_id,
                    occurred_at=occurred_at,
                )

    def _unlock(
        self,
        db: sqlite3.Connection,
        *,
        item_kind: str,
        item_id: str,
        reason: str,
        source_event_id: str,
        occurred_at: datetime,
    ) -> bool:
        item_key = f"{item_kind}:{item_id}"
        unlock_id = uuid.uuid4().hex
        cursor = db.execute(
            """INSERT OR IGNORE INTO unlocks
               (unlock_id,item_key,item_kind,reason,source_event_id,unlocked_at,metadata_json)
               VALUES(?,?,?,?,?,?,'{}')""",
            (unlock_id, item_key, item_kind, reason, source_event_id, occurred_at.isoformat()),
        )
        if not cursor.rowcount:
            return False
        if item_kind == "world":
            object_kind, display_name = WORLD_CATALOG[item_id]
            db.execute(
                """INSERT INTO world_state
                   (object_id,object_kind,display_name,unlocked,placed,position_json,state_json,source_event_id,updated_at)
                   VALUES(?,?,?,1,0,'{}','{}',?,?)
                   ON CONFLICT(object_id) DO UPDATE SET unlocked=1,source_event_id=excluded.source_event_id,
                     updated_at=excluded.updated_at""",
                (item_id, object_kind, display_name, source_event_id, occurred_at.isoformat()),
            )
        self._outbox(
            db,
            idempotency_key=f"unlock:{item_key}",
            topic="growth.unlocked",
            payload={"itemKind": item_kind, "itemId": item_id, "reason": reason},
            occurred_at=occurred_at,
        )
        return True

    def _create_narrative(
        self,
        db: sqlite3.Connection,
        *,
        event_key: str,
        kind: str,
        title: str,
        body: str,
        source_event_id: str,
        occurred_at: datetime,
    ) -> None:
        narrative_id = uuid.uuid4().hex
        cursor = db.execute(
            """INSERT OR IGNORE INTO narrative_events
               (narrative_id,event_key,kind,title,body,status,source_event_id,created_at)
               VALUES(?,?,?,?,?,'pending',?,?)""",
            (narrative_id, event_key, kind, title, body, source_event_id, occurred_at.isoformat()),
        )
        if cursor.rowcount:
            self._outbox(
                db,
                idempotency_key=f"narrative:{event_key}",
                topic="narrative.created",
                payload={"narrativeId": narrative_id, "eventKey": event_key},
                occurred_at=occurred_at,
            )

    @staticmethod
    def _outbox(
        db: sqlite3.Connection,
        *,
        idempotency_key: str,
        topic: str,
        payload: Mapping[str, Any],
        occurred_at: datetime,
    ) -> None:
        db.execute(
            """INSERT OR IGNORE INTO event_outbox
               (outbox_id,idempotency_key,topic,payload_json,state,attempts,available_at,created_at)
               VALUES(?,?,?,?,'pending',0,?,?)""",
            (
                uuid.uuid4().hex,
                idempotency_key,
                topic,
                json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
                occurred_at.isoformat(),
                occurred_at.isoformat(),
            ),
        )

    @staticmethod
    def _event_mapping(value: sqlite3.Row) -> dict[str, Any]:
        result = dict(value)
        result["metadata"] = _decode_json(result.pop("metadata_json"), {})
        return result

    @staticmethod
    def _unlock_mapping(value: sqlite3.Row) -> dict[str, Any]:
        result = dict(value)
        result["metadata"] = _decode_json(result.pop("metadata_json"), {})
        return result


class TaskService:
    def __init__(
        self,
        database: Database,
        *,
        growth: GrowthEngine | None = None,
        now: NowProvider | None = None,
    ) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))
        self.growth = growth or GrowthEngine(database, now=self.now)

    def create(
        self,
        title: str,
        *,
        note: str = "",
        category: str = "inbox",
        priority: int = 1,
        due_at: datetime | str | None = None,
        timezone_name: str = "UTC",
        recurrence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_title = _clean(title, maximum=240, field="title", required=True)
        clean_note = _clean(note, maximum=10_000, field="note")
        clean_category = _clean(category, maximum=80, field="category") or "inbox"
        safe_priority = int(priority)
        if not 0 <= safe_priority <= 3:
            raise ValueError("priority must be between 0 and 3")
        zone_name = str(timezone_name or "UTC")
        _timezone(zone_name)
        rule = _recurrence(recurrence)
        current = _now(self.now)
        due_iso = _iso(due_at, zone_name) if due_at is not None else None
        scheduled_for = due_iso or current.isoformat()
        task_id = uuid.uuid4().hex
        occurrence_id = uuid.uuid4().hex
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO tasks
                   (task_id,title,note,category,status,priority,due_at,timezone,recurrence_json,
                    archived,completed_at,created_at,updated_at)
                   VALUES(?,?,?,?,'open',?,?,?,?,0,NULL,?,?)""",
                (
                    task_id,
                    clean_title,
                    clean_note,
                    clean_category,
                    safe_priority,
                    due_iso,
                    zone_name,
                    json.dumps(rule, ensure_ascii=False, sort_keys=True),
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            db.execute(
                """INSERT INTO task_occurrences
                   (occurrence_id,task_id,scheduled_for,status,completion_version,completed_at,created_at,updated_at)
                   VALUES(?,?,?,'pending',0,NULL,?,?)""",
                (occurrence_id, task_id, scheduled_for, current.isoformat(), current.isoformat()),
            )
            saved = self._task(db, task_id)
        assert saved is not None
        return saved

    def list(
        self,
        *,
        status: str = "",
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        bounded = max(0, min(int(limit), 500))
        if bounded == 0:
            return []
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_archived:
            clauses.append("archived=0")
        if status:
            if status not in {"open", "completed", "archived"}:
                raise ValueError("unknown task status")
            clauses.append("status=?")
            parameters.append(status)
        query = "SELECT task_id FROM tasks"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END,due_at,priority DESC,created_at LIMIT ?"
        parameters.append(bounded)
        with self.database.connect() as db:
            ids = [str(value["task_id"]) for value in db.execute(query, parameters).fetchall()]
            return [value for task_id in ids if (value := self._task(db, task_id)) is not None]

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.database.connect() as db:
            return self._task(db, task_id)

    def update(self, task_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"title", "note", "category", "priority", "due_at", "timezone_name", "recurrence"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported task fields: {', '.join(sorted(unknown))}")
        current = _now(self.now)
        with self.database.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            values: dict[str, Any] = {}
            if "title" in changes:
                values["title"] = _clean(changes["title"], maximum=240, field="title", required=True)
            if "note" in changes:
                values["note"] = _clean(changes["note"], maximum=10_000, field="note")
            if "category" in changes:
                values["category"] = _clean(changes["category"], maximum=80, field="category") or "inbox"
            if "priority" in changes:
                priority = int(changes["priority"])
                if not 0 <= priority <= 3:
                    raise ValueError("priority must be between 0 and 3")
                values["priority"] = priority
            zone_name = str(changes.get("timezone_name", task["timezone"]) or "UTC")
            _timezone(zone_name)
            if "timezone_name" in changes:
                values["timezone"] = zone_name
            if "recurrence" in changes:
                values["recurrence_json"] = json.dumps(
                    _recurrence(changes["recurrence"]), ensure_ascii=False, sort_keys=True
                )
            if "due_at" in changes:
                values["due_at"] = (
                    _iso(changes["due_at"], zone_name) if changes["due_at"] is not None else None
                )
            if values:
                values["updated_at"] = current.isoformat()
                assignments = ",".join(f"{field}=?" for field in values)
                db.execute(
                    f"UPDATE tasks SET {assignments} WHERE task_id=?",
                    (*values.values(), task_id),
                )
            if "due_at" in values and values["due_at"] is not None:
                pending = db.execute(
                    """SELECT occurrence_id FROM task_occurrences
                       WHERE task_id=? AND status='pending' ORDER BY scheduled_for LIMIT 1""",
                    (task_id,),
                ).fetchone()
                if pending:
                    db.execute(
                        "UPDATE task_occurrences SET scheduled_for=?,updated_at=? WHERE occurrence_id=?",
                        (values["due_at"], current.isoformat(), pending["occurrence_id"]),
                    )
            saved = self._task(db, task_id)
        assert saved is not None
        return saved

    def complete(self, task_id: str, occurrence_id: str | None = None) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if occurrence_id:
                occurrence = db.execute(
                    "SELECT * FROM task_occurrences WHERE occurrence_id=? AND task_id=?",
                    (occurrence_id, task_id),
                ).fetchone()
            else:
                occurrence = db.execute(
                    """SELECT * FROM task_occurrences WHERE task_id=? AND status='pending'
                       ORDER BY scheduled_for LIMIT 1""",
                    (task_id,),
                ).fetchone()
                if occurrence is None:
                    occurrence = db.execute(
                        """SELECT * FROM task_occurrences WHERE task_id=? AND status='completed'
                           ORDER BY completed_at DESC LIMIT 1""",
                        (task_id,),
                    ).fetchone()
            if occurrence is None:
                raise KeyError("task occurrence does not exist")
            if occurrence["status"] == "completed":
                saved = self._task(db, task_id)
                assert saved is not None
                return {"task": saved, "occurrence": dict(occurrence), "alreadyCompleted": True, "growth": None}
            version = int(occurrence["completion_version"]) + 1
            db.execute(
                """UPDATE task_occurrences SET status='completed',completion_version=?,completed_at=?,updated_at=?
                   WHERE occurrence_id=?""",
                (version, current.isoformat(), current.isoformat(), occurrence["occurrence_id"]),
            )
            rule = _decode_json(task["recurrence_json"], {})
            if rule:
                next_at = _next_occurrence(occurrence["scheduled_for"], rule, task["timezone"])
                db.execute(
                    """INSERT OR IGNORE INTO task_occurrences
                       (occurrence_id,task_id,scheduled_for,status,completion_version,completed_at,created_at,updated_at)
                       VALUES(?,?,?,'pending',0,NULL,?,?)""",
                    (uuid.uuid4().hex, task_id, next_at.isoformat(), current.isoformat(), current.isoformat()),
                )
                db.execute(
                    "UPDATE tasks SET status='open',due_at=?,completed_at=NULL,updated_at=? WHERE task_id=?",
                    (next_at.isoformat(), current.isoformat(), task_id),
                )
            else:
                db.execute(
                    "UPDATE tasks SET status='completed',completed_at=?,updated_at=? WHERE task_id=?",
                    (current.isoformat(), current.isoformat(), task_id),
                )
            growth = self.growth._record_task_completion(
                db,
                occurrence_id=str(occurrence["occurrence_id"]),
                completion_version=version,
                category=str(task["category"]),
                occurred_at=current,
            )
            saved = self._task(db, task_id)
            completed = db.execute(
                "SELECT * FROM task_occurrences WHERE occurrence_id=?", (occurrence["occurrence_id"],)
            ).fetchone()
        assert saved is not None and completed is not None
        return {"task": saved, "occurrence": dict(completed), "alreadyCompleted": False, "growth": growth}

    def reopen(self, task_id: str, occurrence_id: str | None = None) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError(f"unknown task: {task_id}")
            if occurrence_id:
                occurrence = db.execute(
                    "SELECT * FROM task_occurrences WHERE occurrence_id=? AND task_id=?",
                    (occurrence_id, task_id),
                ).fetchone()
            else:
                occurrence = db.execute(
                    """SELECT * FROM task_occurrences WHERE task_id=? AND status='completed'
                       ORDER BY completed_at DESC LIMIT 1""",
                    (task_id,),
                ).fetchone()
            if occurrence is None:
                raise KeyError("completed task occurrence does not exist")
            if occurrence["status"] != "completed":
                saved = self._task(db, task_id)
                assert saved is not None
                return {"task": saved, "occurrence": dict(occurrence), "alreadyOpen": True, "compensation": None}
            db.execute(
                """UPDATE task_occurrences SET status='pending',completed_at=NULL,updated_at=?
                   WHERE occurrence_id=?""",
                (current.isoformat(), occurrence["occurrence_id"]),
            )
            db.execute(
                "UPDATE tasks SET status='open',archived=0,completed_at=NULL,updated_at=? WHERE task_id=?",
                (current.isoformat(), task_id),
            )
            compensation = self.growth._compensate_key(
                db,
                f"task:{occurrence['occurrence_id']}:v{occurrence['completion_version']}",
                current,
            )
            saved = self._task(db, task_id)
            reopened = db.execute(
                "SELECT * FROM task_occurrences WHERE occurrence_id=?", (occurrence["occurrence_id"],)
            ).fetchone()
        assert saved is not None and reopened is not None
        return {"task": saved, "occurrence": dict(reopened), "alreadyOpen": False, "compensation": compensation}

    def archive(self, task_id: str) -> dict[str, Any]:
        current = _now_iso(self.now)
        with self.database.connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET status='archived',archived=1,updated_at=? WHERE task_id=?",
                (current, task_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"unknown task: {task_id}")
            saved = self._task(db, task_id)
        assert saved is not None
        return saved

    @staticmethod
    def _task(db: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
        task = db.execute("SELECT * FROM tasks WHERE task_id=?", (str(task_id),)).fetchone()
        mapped = _row(task, json_fields=("recurrence_json",))
        if mapped is None:
            return None
        mapped["archived"] = bool(mapped["archived"])
        mapped["occurrences"] = [
            dict(value)
            for value in db.execute(
                "SELECT * FROM task_occurrences WHERE task_id=? ORDER BY scheduled_for",
                (str(task_id),),
            ).fetchall()
        ]
        return mapped


class _TimedSessionService:
    table = ""

    def __init__(self, database: Database, *, growth: GrowthEngine | None = None, now: NowProvider | None = None) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))
        self.growth = growth or GrowthEngine(database, now=self.now)

    def status(self, session_id: str | None = None) -> dict[str, Any] | None:
        with self.database.connect() as db:
            if session_id:
                value = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
            else:
                value = db.execute(
                    f"SELECT * FROM {self.table} WHERE state IN ('running','paused') ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
        if value is None:
            return None
        result = dict(value)
        if result["state"] == "running":
            result["live_active_seconds"] = int(result["active_seconds"]) + _elapsed(
                result["last_resumed_at"], _now(self.now)
            )
        else:
            result["live_active_seconds"] = int(result["active_seconds"])
        return result

    def pause(self, session_id: str, *, idle_seconds: int = 0) -> dict[str, Any]:
        return self._stop_clock(session_id, "paused", idle_seconds=idle_seconds)

    def resume(self, session_id: str) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == "running":
                return dict(value)
            if value["state"] != "paused":
                raise ValueError("only a paused session can resume")
            db.execute(
                f"UPDATE {self.table} SET state='running',last_resumed_at=?,paused_at=NULL,updated_at=? WHERE session_id=?",
                (current.isoformat(), current.isoformat(), session_id),
            )
            saved = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def cancel(self, session_id: str) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == "cancelled":
                return dict(value)
            if value["state"] == "finished":
                raise ValueError("a finished session cannot be cancelled")
            active = int(value["active_seconds"])
            if value["state"] == "running":
                active += _elapsed(value["last_resumed_at"], current)
            db.execute(
                f"""UPDATE {self.table} SET state='cancelled',active_seconds=?,last_resumed_at=NULL,
                    paused_at=NULL,ended_at=?,updated_at=? WHERE session_id=?""",
                (active, current.isoformat(), current.isoformat(), session_id),
            )
            saved = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def _stop_clock(self, session_id: str, next_state: str, *, idle_seconds: int = 0) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == next_state:
                return dict(value)
            if value["state"] != "running":
                raise ValueError("session is not running")
            elapsed = _elapsed(value["last_resumed_at"], current)
            idle = max(0, min(int(idle_seconds), elapsed))
            active = int(value["active_seconds"]) + elapsed - idle
            total_idle = int(value["idle_seconds"]) + idle if "idle_seconds" in value.keys() else 0
            assignments = "state=?,active_seconds=?,paused_at=?,last_resumed_at=NULL,updated_at=?"
            parameters: list[Any] = [next_state, active, current.isoformat(), current.isoformat()]
            if "idle_seconds" in value.keys():
                assignments += ",idle_seconds=?"
                parameters.append(total_idle)
            parameters.append(session_id)
            db.execute(f"UPDATE {self.table} SET {assignments} WHERE session_id=?", parameters)
            saved = db.execute(f"SELECT * FROM {self.table} WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)


class FocusService(_TimedSessionService):
    table = "focus_sessions"

    def start(self, *, task_id: str | None = None, minutes: int = 25) -> dict[str, Any]:
        planned = int(minutes) * 60
        if not 300 <= planned <= 10_800:
            raise ValueError("focus duration must be between 5 and 180 minutes")
        current = _now(self.now)
        session_id = uuid.uuid4().hex
        with self.database.connect() as db:
            if db.execute("SELECT 1 FROM focus_sessions WHERE state IN ('running','paused')").fetchone():
                raise RuntimeError("a focus session is already active")
            if task_id and not db.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise KeyError(f"unknown task: {task_id}")
            db.execute(
                """INSERT INTO focus_sessions
                   (session_id,task_id,state,planned_seconds,active_seconds,idle_seconds,outcome,
                    started_at,last_resumed_at,paused_at,ended_at,created_at,updated_at)
                   VALUES(?,?,'running',?,0,0,'focused',?,?,NULL,NULL,?,?)""",
                (
                    session_id,
                    task_id,
                    planned,
                    current.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            saved = db.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def finish(
        self,
        session_id: str,
        *,
        idle_seconds: int = 0,
        outcome: str = "focused",
    ) -> dict[str, Any]:
        if outcome not in {"focused", "rest"}:
            raise ValueError("outcome must be focused or rest")
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == "finished":
                return {"session": dict(value), "growth": [], "alreadyFinished": True}
            if value["state"] == "cancelled":
                raise ValueError("a cancelled session cannot finish")
            active = int(value["active_seconds"])
            total_idle = int(value["idle_seconds"])
            if value["state"] == "running":
                elapsed = _elapsed(value["last_resumed_at"], current)
                idle = max(0, min(int(idle_seconds), elapsed))
                active += elapsed - idle
                total_idle += idle
            planned = max(0, int(value["planned_seconds"]))
            active = max(0, min(active, planned))
            db.execute(
                """UPDATE focus_sessions SET state='finished',active_seconds=?,idle_seconds=?,outcome=?,
                   last_resumed_at=NULL,paused_at=NULL,ended_at=?,updated_at=? WHERE session_id=?""",
                (active, total_idle, outcome, current.isoformat(), current.isoformat(), session_id),
            )
            if outcome == "rest":
                growth = []
                if active >= 300:
                    growth.append(
                        self.growth._record(
                            db,
                            idempotency_key=f"rest:{session_id}",
                            event_kind="rest-session",
                            source_kind="focus-session",
                            source_id=session_id,
                            requested_points=0,
                            metadata={"activeSeconds": active},
                            occurred_at=current,
                        )
                    )
            else:
                growth = self.growth._record_focus_completion(
                    db,
                    session_id=session_id,
                    active_seconds=active,
                    planned_seconds=planned,
                    occurred_at=current,
                )
            saved = db.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return {"session": dict(saved), "growth": growth, "alreadyFinished": False}

    def cancel(self, session_id: str, *, idle_seconds: int = 0) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == "cancelled":
                return dict(value)
            if value["state"] == "finished":
                raise ValueError("a finished session cannot be cancelled")
            active = int(value["active_seconds"])
            total_idle = int(value["idle_seconds"])
            if value["state"] == "running":
                elapsed = _elapsed(value["last_resumed_at"], current)
                idle = max(0, min(int(idle_seconds), elapsed))
                active += elapsed - idle
                total_idle += idle
            db.execute(
                """UPDATE focus_sessions SET state='cancelled',active_seconds=?,idle_seconds=?,
                   last_resumed_at=NULL,paused_at=NULL,ended_at=?,updated_at=? WHERE session_id=?""",
                (active, total_idle, current.isoformat(), current.isoformat(), session_id),
            )
            saved = db.execute("SELECT * FROM focus_sessions WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)


class ReadingSessionService(_TimedSessionService):
    table = "reading_sessions"

    def start(self, *, title: str = "", source: str = "") -> dict[str, Any]:
        current = _now(self.now)
        session_id = uuid.uuid4().hex
        with self.database.connect() as db:
            if db.execute("SELECT 1 FROM reading_sessions WHERE state IN ('running','paused')").fetchone():
                raise RuntimeError("a reading session is already active")
            db.execute(
                """INSERT INTO reading_sessions
                   (session_id,title,source,state,active_seconds,started_at,last_resumed_at,
                    paused_at,ended_at,created_at,updated_at)
                   VALUES(?,?,?,'running',0,?,?,NULL,NULL,?,?)""",
                (
                    session_id,
                    _clean(title, maximum=300, field="title"),
                    _clean(source, maximum=1000, field="source"),
                    current.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            saved = db.execute("SELECT * FROM reading_sessions WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def finish(self, session_id: str) -> dict[str, Any]:
        current = _now(self.now)
        with self.database.connect() as db:
            value = db.execute("SELECT * FROM reading_sessions WHERE session_id=?", (session_id,)).fetchone()
            if value is None:
                raise KeyError(f"unknown session: {session_id}")
            if value["state"] == "finished":
                return {"session": dict(value), "growth": [], "alreadyFinished": True}
            if value["state"] == "cancelled":
                raise ValueError("a cancelled session cannot finish")
            active = int(value["active_seconds"])
            if value["state"] == "running":
                active += _elapsed(value["last_resumed_at"], current)
            db.execute(
                """UPDATE reading_sessions SET state='finished',active_seconds=?,last_resumed_at=NULL,
                   paused_at=NULL,ended_at=?,updated_at=? WHERE session_id=?""",
                (active, current.isoformat(), current.isoformat(), session_id),
            )
            growth = self.growth._record_reading_completion(
                db, session_id=session_id, active_seconds=active, occurred_at=current
            )
            saved = db.execute("SELECT * FROM reading_sessions WHERE session_id=?", (session_id,)).fetchone()
        assert saved is not None
        return {"session": dict(saved), "growth": growth, "alreadyFinished": False}


class ReminderScheduler:
    def __init__(self, database: Database, *, now: NowProvider | None = None) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))

    def create(
        self,
        title: str,
        fire_at: datetime | str,
        *,
        body: str = "",
        task_id: str | None = None,
        timezone_name: str = "UTC",
        recurrence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_title = _clean(title, maximum=240, field="title", required=True)
        zone_name = str(timezone_name or "UTC")
        _timezone(zone_name)
        fire_iso = _iso(fire_at, zone_name)
        rule = _recurrence(recurrence)
        current = _now_iso(self.now)
        reminder_id = uuid.uuid4().hex
        with self.database.connect() as db:
            if task_id and not db.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
                raise KeyError(f"unknown task: {task_id}")
            db.execute(
                """INSERT INTO reminders
                   (reminder_id,task_id,title,body,fire_at,timezone,recurrence_json,state,
                    snoozed_until,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,'pending',NULL,?,?)""",
                (
                    reminder_id,
                    task_id,
                    clean_title,
                    _clean(body, maximum=4000, field="body"),
                    fire_iso,
                    zone_name,
                    json.dumps(rule, ensure_ascii=False, sort_keys=True),
                    current,
                    current,
                ),
            )
            saved = db.execute("SELECT * FROM reminders WHERE reminder_id=?", (reminder_id,)).fetchone()
        assert saved is not None
        return self._mapping(saved)

    def list(self, *, state: str = "", limit: int = 200) -> list[dict[str, Any]]:
        bounded = max(0, min(int(limit), 500))
        if bounded == 0:
            return []
        query = "SELECT * FROM reminders"
        parameters: list[Any] = []
        if state:
            if state not in {"pending", "dismissed", "completed"}:
                raise ValueError("unknown reminder state")
            query += " WHERE state=?"
            parameters.append(state)
        query += " ORDER BY COALESCE(snoozed_until,fire_at),created_at LIMIT ?"
        parameters.append(bounded)
        with self.database.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        return [self._mapping(value) for value in rows]

    def claim_due(self, *, channel: str = "bubble", limit: int = 50) -> list[dict[str, Any]]:
        current = _now(self.now)
        bounded = max(0, min(int(limit), 200))
        clean_channel = _clean(channel, maximum=40, field="channel", required=True)
        if bounded == 0:
            return []
        claimed: list[dict[str, Any]] = []
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT * FROM reminders WHERE state='pending'
                   AND COALESCE(snoozed_until,fire_at)<=?
                   ORDER BY COALESCE(snoozed_until,fire_at),created_at LIMIT ?""",
                (current.isoformat(), bounded),
            ).fetchall()
            for value in rows:
                scheduled_for = str(value["snoozed_until"] or value["fire_at"])
                delivery_id = uuid.uuid4().hex
                cursor = db.execute(
                    """INSERT OR IGNORE INTO reminder_deliveries
                       (delivery_id,reminder_id,scheduled_for,channel,status,delivered_at,error,created_at,updated_at)
                       VALUES(?,?,?,?,'claimed',NULL,'',?,?)""",
                    (
                        delivery_id,
                        value["reminder_id"],
                        scheduled_for,
                        clean_channel,
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )
                if not cursor.rowcount:
                    continue
                rule = _decode_json(value["recurrence_json"], {})
                if rule:
                    next_at = _next_occurrence(value["fire_at"], rule, value["timezone"])
                    while next_at <= current:
                        next_at = _next_occurrence(next_at, rule, value["timezone"])
                    db.execute(
                        "UPDATE reminders SET fire_at=?,snoozed_until=NULL,updated_at=? WHERE reminder_id=?",
                        (next_at.isoformat(), current.isoformat(), value["reminder_id"]),
                    )
                else:
                    db.execute(
                        "UPDATE reminders SET state='completed',snoozed_until=NULL,updated_at=? WHERE reminder_id=?",
                        (current.isoformat(), value["reminder_id"]),
                    )
                mapped = self._mapping(value)
                mapped.update({"deliveryId": delivery_id, "scheduledFor": scheduled_for, "channel": clean_channel})
                claimed.append(mapped)
        return claimed

    def mark_delivery(self, delivery_id: str, *, delivered: bool, error: str = "") -> dict[str, Any]:
        current = _now_iso(self.now)
        state = "delivered" if delivered else "failed"
        with self.database.connect() as db:
            cursor = db.execute(
                """UPDATE reminder_deliveries SET status=?,delivered_at=?,error=?,updated_at=?
                   WHERE delivery_id=?""",
                (state, current if delivered else None, _clean(error, maximum=1000, field="error"), current, delivery_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"unknown delivery: {delivery_id}")
            saved = db.execute("SELECT * FROM reminder_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def snooze(self, reminder_id: str, minutes: int = 10) -> dict[str, Any]:
        duration = int(minutes)
        if not 1 <= duration <= 7 * 24 * 60:
            raise ValueError("snooze must be between 1 minute and 7 days")
        current = _now(self.now)
        until = current + timedelta(minutes=duration)
        with self.database.connect() as db:
            existing = db.execute(
                "SELECT state FROM reminders WHERE reminder_id=?", (reminder_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(f"unknown reminder: {reminder_id}")
            if str(existing["state"]) != "pending":
                raise ValueError("only a pending reminder can be snoozed")
            cursor = db.execute(
                """UPDATE reminders SET state='pending',snoozed_until=?,updated_at=?
                   WHERE reminder_id=? AND state='pending'""",
                (until.isoformat(), current.isoformat(), reminder_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"unknown reminder: {reminder_id}")
            saved = db.execute("SELECT * FROM reminders WHERE reminder_id=?", (reminder_id,)).fetchone()
        assert saved is not None
        return self._mapping(saved)

    def dismiss(self, reminder_id: str) -> dict[str, Any]:
        return self._set_state(reminder_id, "dismissed")

    def delete(self, reminder_id: str) -> bool:
        with self.database.connect() as db:
            return bool(db.execute("DELETE FROM reminders WHERE reminder_id=?", (reminder_id,)).rowcount)

    def _set_state(self, reminder_id: str, state: str) -> dict[str, Any]:
        with self.database.connect() as db:
            cursor = db.execute(
                "UPDATE reminders SET state=?,snoozed_until=NULL,updated_at=? WHERE reminder_id=?",
                (state, _now_iso(self.now), reminder_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"unknown reminder: {reminder_id}")
            saved = db.execute("SELECT * FROM reminders WHERE reminder_id=?", (reminder_id,)).fetchone()
        assert saved is not None
        return self._mapping(saved)

    @staticmethod
    def _mapping(value: sqlite3.Row) -> dict[str, Any]:
        result = _row(value, json_fields=("recurrence_json",))
        assert result is not None
        return result


class WardrobeService:
    def __init__(self, database: Database, theme: ThemeManifest | None = None) -> None:
        self.database = database
        if theme is None:
            # Source, packaged and test builds all resolve the same active
            # theme through paths.theme_root.  Import lazily to keep this core
            # module independent from application startup.
            from ..paths import theme_root

            theme = ThemeManifest.load(theme_root() / "theme.json")
        self.theme = theme
        declared_outfits = {
            str(value) for value in self.theme.character.get("outfits", [])
        }
        declared_poses = {
            str(value) for value in self.theme.character.get("poses", [])
        }
        catalog_outfits = {str(value["id"]) for value in OUTFIT_CATALOG}
        catalog_poses = {str(value["id"]) for value in POSE_CATALOG}
        if not catalog_outfits <= declared_outfits:
            raise ValueError("wardrobe catalog contains outfits absent from the active theme")
        if not catalog_poses <= declared_poses:
            raise ValueError("wardrobe catalog contains poses absent from the active theme")
        self._compatible_poses = {
            outfit_id: tuple(
                pose_id
                for pose_id in (str(value["id"]) for value in POSE_CATALOG)
                if self.theme.pose_accepts_outfit(pose_id, outfit_id)
            )
            for outfit_id in catalog_outfits
        }

    def _public_poses(self, outfit_id: str) -> list[str] | str:
        poses = self._compatible_poses.get(str(outfit_id), ())
        all_poses = tuple(str(value["id"]) for value in POSE_CATALOG)
        return "*" if poses == all_poses else list(poses)

    def list(self) -> dict[str, Any]:
        with self.database.connect() as db:
            unlocked = {str(value["item_key"]) for value in db.execute("SELECT item_key FROM unlocks").fetchall()}
            current = db.execute("SELECT * FROM character_loadout WHERE loadout_id='default'").fetchone()
        current_value = dict(current) if current else {}
        current_outfit = str(current_value.get("outfit_id", ""))
        current_pose = str(current_value.get("pose_id", ""))
        return {
            "outfits": [
                {
                    **value,
                    "poses": self._public_poses(str(value["id"])),
                    "unlocked": f"outfit:{value['id']}" in unlocked,
                    "equipped": value["id"] == current_outfit,
                }
                for value in OUTFIT_CATALOG
            ],
            "poses": [
                {
                    **value,
                    "unlocked": f"pose:{value['id']}" in unlocked,
                    "equipped": value["id"] == current_pose,
                }
                for value in POSE_CATALOG
            ],
            "current": current_value,
        }

    def equip(self, *, outfit_id: str | None = None, pose_id: str | None = None) -> dict[str, Any]:
        current_time = datetime.now(UTC).isoformat()
        with self.database.connect() as db:
            current = db.execute("SELECT * FROM character_loadout WHERE loadout_id='default'").fetchone()
            assert current is not None
            outfit = str(outfit_id or current["outfit_id"])
            pose = str(pose_id or current["pose_id"])
            for kind, item_id in (("outfit", outfit), ("pose", pose)):
                if not db.execute("SELECT 1 FROM unlocks WHERE item_key=?", (f"{kind}:{item_id}",)).fetchone():
                    raise PermissionError(f"{kind} is not unlocked: {item_id}")
            outfit_info = next((value for value in OUTFIT_CATALOG if value["id"] == outfit), None)
            pose_info = next((value for value in POSE_CATALOG if value["id"] == pose), None)
            if outfit_info is None or pose_info is None:
                raise KeyError("unknown outfit or pose")
            compatible = self._compatible_poses.get(outfit, ())
            if pose not in compatible:
                raise ValueError(f"{outfit} is not compatible with {pose}")
            db.execute(
                "UPDATE character_loadout SET outfit_id=?,pose_id=?,updated_at=? WHERE loadout_id='default'",
                (outfit, pose, current_time),
            )
            saved = db.execute("SELECT * FROM character_loadout WHERE loadout_id='default'").fetchone()
        assert saved is not None
        return dict(saved)


class BoxWorldService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self) -> dict[str, Any]:
        with self.database.connect() as db:
            rows = db.execute("SELECT * FROM world_state").fetchall()
        saved = {
            str(value["object_id"]): self._mapping(value)
            for value in rows
        }
        objects: list[dict[str, Any]] = []
        catalog_order = {object_id: index for index, object_id in enumerate(WORLD_CATALOG)}
        for object_id, (object_kind, display_name) in WORLD_CATALOG.items():
            value = saved.pop(object_id, None) or {
                "object_id": object_id,
                "object_kind": object_kind,
                "display_name": display_name,
                "unlocked": False,
                "placed": False,
                "position": {},
                "state": {},
            }
            value["unlockHint"] = WORLD_UNLOCK_HINTS.get(object_id, "继续积累共鸣")
            objects.append(value)
        # Preserve forward-compatible rows created by a future catalog or an
        # extension instead of hiding them merely because this build does not
        # yet know their display metadata.
        objects.extend(saved.values())
        objects.sort(
            key=lambda value: (
                not bool(value.get("placed")),
                not bool(value.get("unlocked")),
                catalog_order.get(str(value.get("object_id", "")), len(catalog_order)),
                str(value.get("object_id", "")),
            )
        )
        unlocked_count = sum(bool(value.get("unlocked")) for value in objects)
        placed_count = sum(bool(value.get("placed")) for value in objects)
        return {
            "entered": bool(self.database.get_setting("box_world_entered", False)),
            "name": "莉莉丝的盒中空间",
            "objects": objects,
            "totalCount": len(objects),
            "unlockedCount": unlocked_count,
            "placedCount": placed_count,
            "availableCount": sum(
                bool(value.get("unlocked")) and not bool(value.get("placed"))
                for value in objects
            ),
        }

    def enter(self) -> dict[str, Any]:
        self.database.set_setting("box_world_entered", True)
        return self.status()

    def inspect(self, object_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            value = db.execute("SELECT * FROM world_state WHERE object_id=?", (object_id,)).fetchone()
        if value is None:
            if object_id not in WORLD_CATALOG:
                raise KeyError(f"unknown world object: {object_id}")
            kind, name = WORLD_CATALOG[object_id]
            return {
                "object_id": object_id,
                "object_kind": kind,
                "display_name": name,
                "unlocked": False,
                "placed": False,
                "unlockHint": WORLD_UNLOCK_HINTS.get(object_id, "继续积累共鸣"),
            }
        return self._mapping(value)

    def place(self, object_id: str, *, x: float = 0.5, y: float = 0.5) -> dict[str, Any]:
        safe_x = min(1.0, max(0.0, float(x)))
        safe_y = min(1.0, max(0.0, float(y)))
        with self.database.connect() as db:
            value = db.execute("SELECT * FROM world_state WHERE object_id=?", (object_id,)).fetchone()
            if value is None or not value["unlocked"]:
                raise PermissionError(f"world object is not unlocked: {object_id}")
            db.execute(
                "UPDATE world_state SET placed=1,position_json=?,updated_at=? WHERE object_id=?",
                (json.dumps({"x": safe_x, "y": safe_y}), datetime.now(UTC).isoformat(), object_id),
            )
            saved = db.execute("SELECT * FROM world_state WHERE object_id=?", (object_id,)).fetchone()
        assert saved is not None
        return self._mapping(saved)

    @staticmethod
    def _mapping(value: sqlite3.Row) -> dict[str, Any]:
        result = _row(value, json_fields=("position_json", "state_json"))
        assert result is not None
        result["unlocked"] = bool(result["unlocked"])
        result["placed"] = bool(result["placed"])
        result["unlockHint"] = WORLD_UNLOCK_HINTS.get(
            str(result.get("object_id", "")), "继续积累共鸣"
        )
        return result


class NarrativeDirector:
    def __init__(self, database: Database, *, now: NowProvider | None = None) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))

    def pending(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM narrative_events WHERE status='pending' ORDER BY created_at LIMIT ?",
                (max(0, min(int(limit), 100)),),
            ).fetchall()
        return [dict(value) for value in rows]

    def acknowledge(self, narrative_id: str) -> dict[str, Any]:
        current = _now_iso(self.now)
        with self.database.connect() as db:
            cursor = db.execute(
                """UPDATE narrative_events SET status='acknowledged',acknowledged_at=?
                   WHERE narrative_id=?""",
                (current, narrative_id),
            )
            if not cursor.rowcount:
                raise KeyError(f"unknown narrative: {narrative_id}")
            saved = db.execute("SELECT * FROM narrative_events WHERE narrative_id=?", (narrative_id,)).fetchone()
        assert saved is not None
        return dict(saved)

    def replay(self, narrative_id: str) -> dict[str, Any]:
        current = _now_iso(self.now)
        with self.database.connect() as db:
            original = db.execute("SELECT * FROM narrative_events WHERE narrative_id=?", (narrative_id,)).fetchone()
            if original is None:
                raise KeyError(f"unknown narrative: {narrative_id}")
            replay_id = uuid.uuid4().hex
            db.execute(
                """INSERT INTO narrative_events
                   (narrative_id,event_key,kind,title,body,status,source_event_id,created_at,acknowledged_at)
                   VALUES(?,?,?,?,?,'pending',?,?,NULL)""",
                (
                    replay_id,
                    f"{original['event_key']}:replay:{replay_id}",
                    "replay",
                    original["title"],
                    original["body"],
                    original["source_event_id"],
                    current,
                ),
            )
            saved = db.execute("SELECT * FROM narrative_events WHERE narrative_id=?", (replay_id,)).fetchone()
        assert saved is not None
        return dict(saved)


class EventOutbox:
    """Reliable boundary between committed domain changes and UI delivery."""

    def __init__(self, database: Database, *, now: NowProvider | None = None) -> None:
        self.database = database
        self.now = now or (lambda: datetime.now(UTC))

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        current = _now_iso(self.now)
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT * FROM event_outbox WHERE state IN ('pending','failed') AND available_at<=?
                   ORDER BY created_at LIMIT ?""",
                (current, max(0, min(int(limit), 500))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for value in rows:
            mapped = dict(value)
            mapped["payload"] = _decode_json(mapped.pop("payload_json"), {})
            result.append(mapped)
        return result

    def delivered(self, outbox_id: str) -> bool:
        with self.database.connect() as db:
            return bool(
                db.execute(
                    """UPDATE event_outbox SET state='delivered',attempts=attempts+1,delivered_at=?,last_error=''
                       WHERE outbox_id=?""",
                    (_now_iso(self.now), outbox_id),
                ).rowcount
            )

    def failed(self, outbox_id: str, error: str, retry_seconds: int = 30) -> bool:
        delay = max(1, min(int(retry_seconds), 86_400))
        available = _now(self.now) + timedelta(seconds=delay)
        with self.database.connect() as db:
            return bool(
                db.execute(
                    """UPDATE event_outbox SET state='failed',attempts=attempts+1,available_at=?,last_error=?
                       WHERE outbox_id=?""",
                    (available.isoformat(), _clean(error, maximum=1000, field="error"), outbox_id),
                ).rowcount
            )
