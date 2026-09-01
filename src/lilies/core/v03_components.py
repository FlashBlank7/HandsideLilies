from __future__ import annotations

"""Bounded v0.3 component surface.

The model can inspect and propose work through these actions, but this module
intentionally contains no Calendar commit, Slack send, credential, arbitrary
HTTP, or shell action.  Connector commits remain confirmation-UI-only.
"""

from dataclasses import dataclass
from typing import Any

from .components import ComponentAction, ComponentRegistry
from .permissions import Risk


EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
ID_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 128}
RECURRENCE_SCHEMA = {
    "type": "object",
    "required": ["frequency"],
    "properties": {
        "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "interval": {"type": "integer", "minimum": 1, "maximum": 365},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class V03Services:
    tasks: Any
    focus: Any
    reading: Any
    reminders: Any
    growth: Any
    wardrobe: Any
    box_world: Any
    window_catalog: Any
    pet_habitat: Any
    calendar: Any | None = None
    slack: Any | None = None
    box_world_enter: Any | None = None


def _register(
    registry: ComponentRegistry,
    component: str,
    action: str,
    title: str,
    description: str,
    risk: Risk,
    schema: dict[str, Any],
    handler: Any,
    *,
    audit_projector: Any | None = None,
) -> None:
    registry.register(
        ComponentAction(
            component,
            action,
            title,
            description,
            risk,
            schema,
            handler,
            audit_projector,
        )
    )


def _connector_audit_projection(kind: str, value: Any) -> dict[str, Any]:
    """Keep connector audit useful without copying external/user content."""

    if kind == "error":
        return {
            "redacted": True,
            "kind": "error",
            "type": type(value).__name__,
        }
    item = dict(value) if isinstance(value, dict) else {}
    event_id = str(item.get("eventId", item.get("remoteId", "")))[:128]
    if kind == "payload":
        change = item.get("change")
        return {
            "redacted": True,
            "eventId": event_id,
            "changedFields": sorted(str(key)[:80] for key in change)
            if isinstance(change, dict)
            else (["text"] if "text" in item else []),
            "textLength": len(str(item.get("text", ""))) if "text" in item else 0,
        }
    return {
        "redacted": True,
        "proposalId": str(item.get("proposalId", item.get("id", "")))[:128],
        "provider": str(item.get("provider", item.get("connector", "")))[:40],
        "action": str(item.get("action", ""))[:80],
        "status": str(item.get("status", ""))[:40],
        "changedFields": list(item.get("changedFields", []))[:20],
    }


def _proposal_receipt(value: Any) -> dict[str, Any]:
    """Model-safe receipt; full before/after remains exclusive to focused UI."""

    proposal = dict(value) if isinstance(value, dict) else {}
    after = proposal.get("after")
    changed: list[str] = []
    if isinstance(after, dict):
        changes = after.get("changes") if isinstance(after.get("changes"), dict) else after
        changed = sorted(str(key)[:80] for key in changes)[:20]
    return {
        "proposalId": str(proposal.get("proposalId", proposal.get("id", "")))[:128],
        "provider": str(proposal.get("connector", ""))[:40],
        "action": str(proposal.get("action", ""))[:80],
        "status": str(proposal.get("status", "pending"))[:40],
        "changedFields": changed,
        "requiresUserConfirmation": True,
    }


def _session_schema(*, include_outcome: bool = False) -> dict[str, Any]:
    properties: dict[str, Any] = {"sessionId": ID_SCHEMA}
    if include_outcome:
        properties["outcome"] = {"type": "string", "enum": ["focused", "rest"]}
    return {
        "type": "object",
        "required": ["sessionId"],
        "properties": properties,
        "additionalProperties": False,
    }


def register_v03_components(
    registry: ComponentRegistry,
    services: V03Services,
) -> ComponentRegistry:
    """Register deterministic productivity, habitat and connector proposals."""

    task_create = {
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 240},
            "note": {"type": "string", "maxLength": 10_000},
            "category": {"type": "string", "maxLength": 80},
            "priority": {"type": "integer", "minimum": 0, "maximum": 3},
            "dueAt": {"type": "string", "format": "date-time"},
            "timezone": {"type": "string", "minLength": 1, "maxLength": 80},
            "recurrence": RECURRENCE_SCHEMA,
        },
        "additionalProperties": False,
    }

    def create_task(payload: dict[str, Any]) -> Any:
        return services.tasks.create(
            str(payload["title"]),
            note=str(payload.get("note", "")),
            category=str(payload.get("category", "inbox")),
            priority=int(payload.get("priority", 1)),
            due_at=payload.get("dueAt"),
            timezone_name=str(payload.get("timezone", "UTC")),
            recurrence=payload.get("recurrence"),
        )

    _register(
        registry, "tasks", "list", "任务收件箱", "读取本地任务和任务实例。", Risk.READ,
        {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["", "open", "completed", "archived"]},
                "includeArchived": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
        lambda p: services.tasks.list(
            status=str(p.get("status", "")),
            include_archived=bool(p.get("includeArchived", False)),
            limit=int(p.get("limit", 100)),
        ),
    )
    _register(registry, "tasks", "create", "创建任务", "在本地收件箱创建任务；外部事项不会自动转入。", Risk.MUTATE, task_create, create_task)

    task_changes = dict(task_create["properties"])
    task_changes.pop("title")
    task_changes["title"] = {"type": "string", "minLength": 1, "maxLength": 240}
    _register(
        registry, "tasks", "update", "更新任务", "更新一个明确任务的字段。", Risk.MUTATE,
        {
            "type": "object", "required": ["taskId", "changes"],
            "properties": {
                "taskId": ID_SCHEMA,
                "changes": {"type": "object", "properties": task_changes, "additionalProperties": False},
            },
            "additionalProperties": False,
        },
        lambda p: services.tasks.update(
            str(p["taskId"]),
            **{
                {"dueAt": "due_at", "timezone": "timezone_name"}.get(key, key): value
                for key, value in dict(p["changes"]).items()
            },
        ),
    )
    task_instance_schema = {
        "type": "object", "required": ["taskId"],
        "properties": {"taskId": ID_SCHEMA, "occurrenceId": ID_SCHEMA},
        "additionalProperties": False,
    }
    _register(registry, "tasks", "complete", "完成任务", "完成一次任务实例并由确定性账本结算共鸣。", Risk.MUTATE, task_instance_schema, lambda p: services.tasks.complete(str(p["taskId"]), p.get("occurrenceId")))
    _register(registry, "tasks", "reopen", "重开任务", "重开已完成实例并写入补偿事件，不删除历史。", Risk.MUTATE, task_instance_schema, lambda p: services.tasks.reopen(str(p["taskId"]), p.get("occurrenceId")))
    _register(registry, "tasks", "archive", "归档任务", "把明确任务从活动收件箱归档。", Risk.MUTATE, {"type": "object", "required": ["taskId"], "properties": {"taskId": ID_SCHEMA}, "additionalProperties": False}, lambda p: services.tasks.archive(str(p["taskId"])))

    status_schema = {"type": "object", "properties": {"sessionId": ID_SCHEMA}, "additionalProperties": False}
    _register(registry, "focus", "status", "专注状态", "读取当前或指定专注会话。", Risk.READ, status_schema, lambda p: services.focus.status(p.get("sessionId")))
    _register(
        registry, "focus", "start", "开始专注", "开始 5 至 180 分钟的显式专注会话。", Risk.MUTATE,
        {"type": "object", "properties": {"taskId": ID_SCHEMA, "minutes": {"type": "integer", "minimum": 5, "maximum": 180}}, "additionalProperties": False},
        lambda p: services.focus.start(task_id=p.get("taskId"), minutes=int(p.get("minutes", 25))),
    )
    _register(registry, "focus", "pause", "暂停专注", "暂停专注计时。", Risk.MUTATE, _session_schema(), lambda p: services.focus.pause(str(p["sessionId"])))
    _register(registry, "focus", "resume", "继续专注", "继续已暂停的专注会话。", Risk.MUTATE, _session_schema(), lambda p: services.focus.resume(str(p["sessionId"])))
    _register(registry, "focus", "finish", "完成专注", "结束专注或主动休息并结算确定性事件。", Risk.MUTATE, _session_schema(include_outcome=True), lambda p: services.focus.finish(str(p["sessionId"]), outcome=str(p.get("outcome", "focused"))))
    _register(registry, "focus", "cancel", "取消专注", "取消会话且不扣除共鸣。", Risk.MUTATE, _session_schema(), lambda p: services.focus.cancel(str(p["sessionId"])))

    _register(registry, "reading", "status", "阅读状态", "读取当前或指定论文阅读会话。", Risk.READ, status_schema, lambda p: services.reading.status(p.get("sessionId")))
    _register(
        registry, "reading", "start", "开始论文阅读", "显式开始一次论文阅读会话。", Risk.MUTATE,
        {"type": "object", "properties": {"title": {"type": "string", "maxLength": 300}, "source": {"type": "string", "maxLength": 1000}}, "additionalProperties": False},
        lambda p: services.reading.start(title=str(p.get("title", "")), source=str(p.get("source", ""))),
    )
    _register(registry, "reading", "finish", "完成论文阅读", "结束阅读并按有效时长结算共鸣。", Risk.MUTATE, _session_schema(), lambda p: services.reading.finish(str(p["sessionId"])))

    _register(
        registry, "reminders", "list", "提醒列表", "读取本地确定性提醒。", Risk.READ,
        {"type": "object", "properties": {"state": {"type": "string", "enum": ["", "pending", "dismissed", "completed"]}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False},
        lambda p: services.reminders.list(state=str(p.get("state", "")), limit=int(p.get("limit", 100))),
    )
    _register(
        registry, "reminders", "create", "创建提醒", "创建不依赖模型的本地提醒。", Risk.MUTATE,
        {
            "type": "object", "required": ["title", "fireAt"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "body": {"type": "string", "maxLength": 4000},
                "fireAt": {"type": "string", "format": "date-time"},
                "taskId": ID_SCHEMA,
                "timezone": {"type": "string", "minLength": 1, "maxLength": 80},
                "recurrence": RECURRENCE_SCHEMA,
            },
            "additionalProperties": False,
        },
        lambda p: services.reminders.create(str(p["title"]), str(p["fireAt"]), body=str(p.get("body", "")), task_id=p.get("taskId"), timezone_name=str(p.get("timezone", "UTC")), recurrence=p.get("recurrence")),
    )
    reminder_id = {"type": "object", "required": ["reminderId"], "properties": {"reminderId": ID_SCHEMA}, "additionalProperties": False}
    _register(registry, "reminders", "snooze", "稍后提醒", "把提醒推迟一段明确时间。", Risk.MUTATE, {"type": "object", "required": ["reminderId"], "properties": {"reminderId": ID_SCHEMA, "minutes": {"type": "integer", "minimum": 1, "maximum": 10080}}, "additionalProperties": False}, lambda p: services.reminders.snooze(str(p["reminderId"]), int(p.get("minutes", 10))))
    _register(registry, "reminders", "dismiss", "忽略提醒", "关闭本次本地提醒。", Risk.MUTATE, reminder_id, lambda p: services.reminders.dismiss(str(p["reminderId"])))
    _register(registry, "reminders", "delete", "删除提醒", "永久删除一个提醒及其投递记录。", Risk.DESTRUCTIVE, reminder_id, lambda p: {"deleted": services.reminders.delete(str(p["reminderId"]))})

    _register(registry, "growth", "status", "共鸣进度", "读取精确共鸣值、阶段和下一阶段。", Risk.READ, EMPTY_SCHEMA, lambda _p: services.growth.status())
    _register(registry, "growth", "history", "共鸣账本", "读取不可变共鸣事件；模型不能授予积分。", Risk.READ, {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "additionalProperties": False}, lambda p: services.growth.history(int(p.get("limit", 100))))
    _register(registry, "growth", "unlocks", "成长解锁", "读取确定性解锁项目。", Risk.READ, {"type": "object", "properties": {"kind": {"type": "string", "maxLength": 40}}, "additionalProperties": False}, lambda p: services.growth.unlocks(str(p.get("kind", ""))))
    _register(registry, "wardrobe", "list", "衣橱", "读取服装、姿态兼容关系和当前装扮。", Risk.READ, EMPTY_SCHEMA, lambda _p: services.wardrobe.list())
    _register(registry, "wardrobe", "equip", "更换装扮", "装备已经解锁且兼容的服装或姿态。", Risk.MUTATE, {"type": "object", "properties": {"outfitId": ID_SCHEMA, "poseId": ID_SCHEMA}, "additionalProperties": False}, lambda p: services.wardrobe.equip(outfit_id=p.get("outfitId"), pose_id=p.get("poseId")))
    _register(registry, "box-world", "status", "盒中世界", "读取盒中陈设与解锁状态。", Risk.READ, EMPTY_SCHEMA, lambda _p: services.box_world.status())
    _register(registry, "box-world", "inspect", "查看陈设", "查看一个盒中对象。", Risk.READ, {"type": "object", "required": ["objectId"], "properties": {"objectId": ID_SCHEMA}, "additionalProperties": False}, lambda p: services.box_world.inspect(str(p["objectId"])))
    _register(
        registry,
        "box-world",
        "enter",
        "进入盒中世界",
        "打开盒中空间，不改变系统权限。",
        Risk.MUTATE,
        EMPTY_SCHEMA,
        lambda _p: (
            services.box_world_enter()
            if callable(services.box_world_enter)
            else services.box_world.enter()
        ),
    )

    _register(registry, "pet-habitat", "status", "桌宠栖息状态", "读取莉莉丝当前宿主、姿态与浮层策略。", Risk.READ, EMPTY_SCHEMA, lambda _p: services.pet_habitat.status())
    _register(registry, "pet-habitat", "set-mode", "桌宠浮层模式", "切换始终置顶或普通窗口层级。", Risk.MUTATE, {"type": "object", "required": ["mode"], "properties": {"mode": {"type": "string", "enum": ["always", "normal"]}}, "additionalProperties": False}, lambda p: services.pet_habitat.set_floating_mode(str(p["mode"])) or services.pet_habitat.status())
    _register(registry, "pet-habitat", "detach", "脱离窗口", "从当前前台窗口的自动栖息点手动脱离。", Risk.MUTATE, EMPTY_SCHEMA, lambda _p: {"detached": services.pet_habitat.detach(), "status": services.pet_habitat.status()})

    if services.calendar is not None:
        _register_connector_components(registry, "calendar", services.calendar)
    if services.slack is not None:
        _register_connector_components(registry, "slack", services.slack)
    return registry


def _register_connector_components(
    registry: ComponentRegistry,
    provider: str,
    connector: Any,
) -> None:
    """Expose reads and proposals only; commit/send methods stay private."""

    if provider == "calendar":
        _register(registry, provider, "status", "日历状态", "读取本地连接与最后同步状态。", Risk.READ, EMPTY_SCHEMA, lambda _p: connector.status())
        _register(registry, provider, "calendars", "日历列表", "读取已授权日历的本地元数据。", Risk.READ, EMPTY_SCHEMA, lambda _p: connector.calendars())
        _register(registry, provider, "upcoming", "近期日程", "读取滚动窗口内的近期日程元数据；正文只在用户点选单项后进入隔离协助流程。", Risk.READ, {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}, lambda p: connector.metadata_items(limit=int(p.get("limit", 30))))
        _register(registry, provider, "refresh", "刷新日历", "按已授权只读范围刷新缓存。", Risk.READ, EMPTY_SCHEMA, lambda _p: connector.refresh())
        _register(registry, provider, "open-event", "打开日程", "在系统浏览器打开一个已缓存日程。", Risk.LAUNCH, {"type": "object", "required": ["eventId"], "properties": {"eventId": ID_SCHEMA}, "additionalProperties": False}, lambda p: connector.open_event(str(p["eventId"])))
        proposal_schema = {"type": "object", "required": ["change"], "properties": {"change": {"type": "object"}}, "additionalProperties": False}
        _register(registry, provider, "propose-create", "提议创建日程", "生成可审阅差异，不直接写入 Google。", Risk.MUTATE, proposal_schema, lambda p: _proposal_receipt(connector.propose_create(dict(p["change"]))), audit_projector=_connector_audit_projection)
        _register(registry, provider, "propose-update", "提议修改日程", "基于 ETag 生成可审阅差异，不直接提交；模型只收到脱敏回执。", Risk.MUTATE, {"type": "object", "required": ["eventId", "change"], "properties": {"eventId": ID_SCHEMA, "change": {"type": "object"}}, "additionalProperties": False}, lambda p: _proposal_receipt(connector.propose_update(str(p["eventId"]), dict(p["change"]))), audit_projector=_connector_audit_projection)
        return

    _register(registry, provider, "status", "Slack 状态", "读取本地连接、断线与授权状态。", Risk.READ, EMPTY_SCHEMA, lambda _p: connector.status())
    _register(registry, provider, "inbox", "Slack 信笺匣", "读取已过滤项目的元数据；正文不会自动进入模型。", Risk.READ, {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False}, lambda p: connector.metadata_items(limit=int(p.get("limit", 30))))
    _register(registry, provider, "open-message", "打开 Slack 消息", "返回可定位项目的元数据；正文只由用户点选后的隔离协助流程读取。", Risk.LAUNCH, {"type": "object", "required": ["eventId"], "properties": {"eventId": ID_SCHEMA}, "additionalProperties": False}, lambda p: next((item for item in connector.metadata_items(limit=100) if item.get("id") == str(p["eventId"])), {}))
    _register(registry, provider, "draft-reply", "拟写 Slack 回复", "为一个单项消息生成草稿，不发送；审计不保存正文。", Risk.MUTATE, {"type": "object", "required": ["eventId", "text"], "properties": {"eventId": ID_SCHEMA, "text": {"type": "string", "minLength": 1, "maxLength": 4000}}, "additionalProperties": False}, lambda p: {"eventId": str(p["eventId"]), "draftLength": len(str(p["text"])), "sent": False}, audit_projector=_connector_audit_projection)
    _register(registry, provider, "propose-reply", "提议 Slack 回复", "生成可编辑确认提案，不调用 chat.postMessage；模型只收到脱敏回执。", Risk.MUTATE, {"type": "object", "required": ["eventId", "text"], "properties": {"eventId": ID_SCHEMA, "text": {"type": "string", "minLength": 1, "maxLength": 4000}}, "additionalProperties": False}, lambda p: _proposal_receipt(connector.propose_reply(str(p["eventId"]), str(p["text"]))), audit_projector=_connector_audit_projection)


__all__ = ["V03Services", "register_v03_components"]
