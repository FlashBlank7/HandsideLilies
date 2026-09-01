from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlparse

from .database import Database
from .desktop import DesktopIndex
from .permissions import PermissionBroker, Risk
from .shell import ShellController
from .themes import ThemeManifest
from .windows import activate_window, list_windows, open_settings, open_web_url


class ConfirmationRequired(PermissionError):
    def __init__(self, component_id: str, action_id: str, risk: Risk, reason: str, audit_id: str = "") -> None:
        super().__init__(reason)
        self.component_id = component_id
        self.action_id = action_id
        self.risk = risk
        self.reason = reason
        self.audit_id = audit_id


def _validate_schema_value(schema: dict[str, Any], value: Any, path: str) -> None:
    """Validate the deliberately small, recursively safe schema subset we expose.

    Component schemas are both a UI contract and the last boundary before a
    model-originated payload reaches a handler.  Keeping this implementation
    local avoids accepting partially validated nested objects just because a
    full JSON-schema dependency is not installed.
    """
    python_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    expected = schema.get("type")
    expected_type = python_types.get(expected)
    wrong_integer = expected == "integer" and isinstance(value, bool)
    wrong_number = expected == "number" and isinstance(value, bool)
    if expected_type and (not isinstance(value, expected_type) or wrong_integer or wrong_number):
        raise ValueError(f"parameter {path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"parameter {path} is outside the allowed values")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"missing required parameter at {path}: " + ", ".join(missing))
        if schema.get("additionalProperties") is False:
            extra = [name for name in value if name not in properties]
            if extra:
                raise ValueError(f"unknown parameter at {path}: " + ", ".join(extra))
        for name, child in value.items():
            definition = properties.get(name)
            if isinstance(definition, dict):
                _validate_schema_value(definition, child, f"{path}.{name}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise ValueError(f"parameter {path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise ValueError(f"parameter {path} has too many items")
        if schema.get("uniqueItems"):
            fingerprints = [repr(item) for item in value]
            if len(set(fingerprints)) != len(fingerprints):
                raise ValueError(f"parameter {path} must contain unique items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                _validate_schema_value(item_schema, child, f"{path}[{index}]")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"parameter {path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"parameter {path} is above maximum")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            raise ValueError(f"parameter {path} is shorter than allowed")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise ValueError(f"parameter {path} is longer than allowed")
        if "pattern" in schema and re.search(str(schema["pattern"]), value) is None:
            raise ValueError(f"parameter {path} does not match the required pattern")
        value_format = schema.get("format")
        try:
            if value_format == "date":
                date.fromisoformat(value)
            elif value_format == "date-time":
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            elif value_format == "uri":
                parsed = urlparse(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError
        except ValueError as exc:
            raise ValueError(f"parameter {path} must be a valid {value_format}") from exc


def validate_payload(schema: dict[str, Any], payload: dict[str, Any]) -> None:
    """Validate the recursive JSON-schema subset exposed by component actions."""
    _validate_schema_value(schema, payload, "payload")


@dataclass(frozen=True)
class ComponentAction:
    component_id: str
    action_id: str
    title: str
    description: str
    risk: Risk
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    audit_projector: Callable[[str, Any], Any] | None = None

    @property
    def tool_name(self) -> str:
        return f"{self.component_id}__{self.action_id}".replace("-", "_")

    def public(self) -> dict[str, Any]:
        return {
            "componentId": self.component_id,
            "actionId": self.action_id,
            "title": self.title,
            "description": self.description,
            "risk": self.risk.value,
            "parameters": self.parameters,
            "toolName": self.tool_name,
        }

    def tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class V02ComponentBindings:
    desktop_peek_status: Callable[[], Any]
    desktop_peek_toggle: Callable[[], Any]
    desktop_peek_restore: Callable[[], Any]
    activity_status: Callable[[], Any]
    activity_set_policy: Callable[[str, str], Any]
    companion_preferences: Callable[[], Any]
    companion_reply: Callable[[str, str], Any]
    companion_another: Callable[[str], Any]
    companion_snooze: Callable[[int], Any]
    memory_partitions: Callable[[], Any]
    memory_recall: Callable[[dict[str, Any]], Any]
    memory_reindex: Callable[[], Any]
    memory_forget: Callable[[str, bool], Any]
    content_sources: Callable[[], Any]
    content_refresh: Callable[[str, str, int], Any]


class ComponentRegistry:
    def __init__(self, database: Database, permissions: PermissionBroker) -> None:
        self.database = database
        self.permissions = permissions
        self._actions: dict[tuple[str, str], ComponentAction] = {}

    def register(self, action: ComponentAction) -> None:
        key = (action.component_id, action.action_id)
        if key in self._actions:
            raise KeyError(f"duplicate component action: {key}")
        self._actions[key] = action

    def list(self) -> list[dict[str, Any]]:
        return [value.public() for value in self._actions.values()]

    def tools(self) -> list[dict[str, Any]]:
        return [value.tool_schema() for value in self._actions.values()]

    def by_tool_name(self, name: str) -> ComponentAction:
        for value in self._actions.values():
            if value.tool_name == name:
                return value
        raise KeyError(f"unregistered tool: {name}")

    def invoke(
        self,
        component_id: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
        *,
        origin: str = "ui",
        confirmed: bool = False,
    ) -> Any:
        value = self._actions.get((component_id, action_id))
        if value is None:
            raise KeyError(f"unsupported component action: {component_id}.{action_id}")
        clean_payload = payload if isinstance(payload, dict) else {}

        def audit_projection(kind: str, raw: Any) -> Any:
            if value.audit_projector is None:
                return raw
            try:
                return value.audit_projector(kind, raw)
            except Exception:
                # Audit must never become a second content store merely
                # because a custom projection failed.
                return {"redacted": True, "kind": kind, "projectionFailed": True}

        try:
            validate_payload(value.parameters, clean_payload)
        except ValueError:
            self.database.audit(
                origin,
                component_id,
                action_id,
                value.risk.value,
                "reject",
                audit_projection("payload", clean_payload),
            )
            raise
        decision = self.permissions.check(component_id, action_id, value.risk, confirmed)
        audit_id = self.database.audit(
            origin,
            component_id,
            action_id,
            value.risk.value,
            "allow" if decision.allowed else "confirm",
            audit_projection("payload", clean_payload),
        )
        if not decision.allowed:
            raise ConfirmationRequired(component_id, action_id, value.risk, decision.reason, audit_id)
        try:
            result = value.handler(clean_payload)
        except Exception as exc:
            self.database.complete_audit(
                audit_id, error=audit_projection("error", exc)
            )
            raise
        self.database.complete_audit(
            audit_id, result=audit_projection("result", result)
        )
        return {"auditId": audit_id, "result": result}


def string_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": [name],
        "properties": {name: {"type": "string", "description": description}},
        "additionalProperties": False,
    }


def build_registry(
    database: Database,
    permissions: PermissionBroker,
    desktop: DesktopIndex,
    shell: ShellController,
    theme: ThemeManifest,
    model_status: Callable[[], dict[str, Any]],
    window_list: Callable[[], list[dict[str, Any]]] | None = None,
    window_activate: Callable[[int], bool] | None = None,
) -> ComponentRegistry:
    registry = ComponentRegistry(database, permissions)

    registry.register(
        ComponentAction(
            "theme", "status", "主题状态", "读取当前主题和渲染方式。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: {"theme": theme.public(), "renderer": database.get_setting("theme_renderer", theme.default_renderer)},
        )
    )

    def activate_theme(payload: dict[str, Any]) -> dict[str, Any]:
        renderer = str(payload.get("renderer", theme.default_renderer))
        if renderer not in theme.renderers:
            raise ValueError("renderer is not supported by the active theme")
        database.set_setting("theme_renderer", renderer)
        return {"themeId": theme.theme_id, "renderer": renderer}

    registry.register(
        ComponentAction(
            "theme", "activate", "切换主题渲染", "在实时纸雕和电影循环之间切换。", Risk.MUTATE,
            {
                "type": "object",
                "required": ["renderer"],
                "properties": {"renderer": {"type": "string", "enum": list(theme.renderers)}},
                "additionalProperties": False,
            },
            activate_theme,
        )
    )
    registry.register(
        ComponentAction(
            "desktop-icons", "list", "桌面图标", "读取 Lilies 虚拟桌面中的文件与快捷方式。", Risk.READ,
            {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False},
            lambda p: desktop.items(str(p.get("query", "")), limit=40),
        )
    )

    def update_desktop_item(payload: dict[str, Any]) -> dict[str, Any]:
        item_id = str(payload["itemId"])
        known = next(
            (value for value in database.desktop_items(include_hidden=True) if value["item_id"] == item_id),
            None,
        )
        if known is None:
            raise KeyError("desktop item is not indexed")
        changes: dict[str, Any] = {}
        mapping = {"x": "x", "y": "y", "group": "group_name", "pinned": "pinned", "hidden": "hidden"}
        for source, target in mapping.items():
            if source in payload:
                changes[target] = int(payload[source]) if source in {"pinned", "hidden"} else payload[source]
        database.update_desktop_layout(item_id, **changes)
        return {"itemId": item_id, "updated": sorted(changes)}

    registry.register(
        ComponentAction(
            "desktop-icons", "arrange", "编排桌面图标", "修改当前虚拟布局中的位置、分组、固定或隐藏状态。", Risk.MUTATE,
            {
                "type": "object", "required": ["itemId"],
                "properties": {
                    "itemId": {"type": "string"},
                    "x": {"type": "number", "minimum": 0, "maximum": 10000},
                    "y": {"type": "number", "minimum": 0, "maximum": 10000},
                    "group": {"type": "string"},
                    "pinned": {"type": "boolean"},
                    "hidden": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            update_desktop_item,
        )
    )
    registry.register(
        ComponentAction(
            "desktop-icons", "layouts", "桌面布局方案", "列出可切换的虚拟桌面布局方案。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: database.desktop_layouts(),
        )
    )

    def activate_layout(payload: dict[str, Any]) -> dict[str, Any]:
        layout_id = str(payload["layoutId"])
        if not database.activate_desktop_layout(layout_id):
            raise KeyError("desktop layout does not exist")
        return {"layoutId": layout_id}

    registry.register(
        ComponentAction(
            "desktop-icons", "activate-layout", "切换桌面布局", "切换到一个已经存在的虚拟桌面布局。", Risk.MUTATE,
            string_schema("layoutId", "布局标识"), activate_layout,
        )
    )
    registry.register(
        ComponentAction(
            "desktop-icons", "open", "打开桌面项目", "打开已被 Lilies 索引的文件、文件夹或快捷方式。", Risk.LAUNCH,
            string_schema("path", "已索引项目的完整路径"),
            lambda p: desktop.launch(str(p["path"])) or {"opened": str(p["path"])},
        )
    )
    registry.register(
        ComponentAction(
            "app-launcher", "search", "搜索应用", "搜索桌面和开始菜单中的应用。", Risk.READ,
            string_schema("query", "应用名称的一部分"),
            lambda p: desktop.applications(str(p["query"]), limit=12),
        )
    )
    registry.register(
        ComponentAction(
            "app-launcher", "open", "启动应用", "启动 Lilies 应用库中的程序。", Risk.LAUNCH,
            string_schema("path", "已索引应用的完整路径"),
            lambda p: desktop.launch(str(p["path"])) or {"opened": str(p["path"])},
        )
    )
    registry.register(
        ComponentAction(
            "filesystem", "search", "查找文件或文件夹", "按明确路径或在用户内容目录中查找文件与文件夹。", Risk.READ,
            string_schema("query", "文件名、文件夹名或完整路径"),
            lambda p: desktop.resources(str(p["query"]), limit=12),
        )
    )
    registry.register(
        ComponentAction(
            "filesystem", "open", "打开文件或文件夹", "打开已经由 Lilies 找到并登记的文件或文件夹。", Risk.LAUNCH,
            string_schema("path", "已登记文件或文件夹的完整路径"),
            lambda p: desktop.launch(str(p["path"])) or {"opened": str(p["path"])},
        )
    )
    registry.register(
        ComponentAction(
            "web", "open", "打开网页", "使用系统默认浏览器打开明确的 HTTP 或 HTTPS 地址。", Risk.LAUNCH,
            {
                "type": "object", "required": ["url"],
                "properties": {"url": {"type": "string", "minLength": 1, "maxLength": 2048}},
                "additionalProperties": False,
            },
            lambda p: open_web_url(str(p["url"])) or {"opened": str(p["url"])},
        )
    )
    # v0.3 deliberately exposes no arbitrary terminal/shell component.  The
    # model may only invoke bounded, registered application actions below.
    registry.register(
        ComponentAction(
            "window-manager", "list", "窗口列表", "读取当前可切换的顶层窗口。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: (window_list or list_windows)(),
        )
    )
    registry.register(
        ComponentAction(
            "window-manager", "activate", "切换窗口", "激活指定的现有窗口。", Risk.LAUNCH,
            {
                "type": "object", "required": ["handle"],
                "properties": {"handle": {"type": "integer"}}, "additionalProperties": False,
            },
            lambda p: {"activated": (window_activate or activate_window)(int(p["handle"]))},
        )
    )
    registry.register(
        ComponentAction(
            "shell-mode", "status", "外壳状态", "读取当前桌面外壳模式和恢复快捷键。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: shell.status(),
        )
    )

    def switch_shell(payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload["mode"])
        if mode == "visual":
            shell.enter_visual()
        else:
            shell.enter_compact()
        return shell.status()

    registry.register(
        ComponentAction(
            "shell-mode", "switch", "切换桌面形态", "在完整 Lilies 桌面和紧凑盒子之间切换。", Risk.MUTATE,
            {
                "type": "object", "required": ["mode"],
                "properties": {"mode": {"type": "string", "enum": ["visual", "compact"]}},
                "additionalProperties": False,
            },
            switch_shell,
        )
    )
    registry.register(
        ComponentAction(
            "shell-mode", "system-settings", "系统设置", "打开 Windows 网络、声音、通知或显示设置。", Risk.LAUNCH,
            {
                "type": "object", "required": ["page"],
                "properties": {"page": {"type": "string", "enum": ["network", "sound", "notifications", "display"]}},
                "additionalProperties": False,
            },
            lambda p: open_settings("ms-settings:" + str(p["page"])) or {"opened": p["page"]},
        )
    )
    registry.register(
        ComponentAction(
            "memory", "list", "记忆卡片", "读取已启用的长期记忆卡片。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: database.memory_cards(enabled_only=True),
        )
    )
    registry.register(
        ComponentAction(
            "memory", "remember", "保存记忆", "把用户明确要求记住的信息保存为可审阅卡片。", Risk.MUTATE,
            {
                "type": "object", "required": ["title", "content"],
                "properties": {"title": {"type": "string"}, "content": {"type": "string"}, "category": {"type": "string"}},
                "additionalProperties": False,
            },
            lambda p: {"memoryId": database.save_memory(str(p["title"]), str(p["content"]), str(p.get("category", "事实")))},
        )
    )
    registry.register(
        ComponentAction(
            "reading-cards", "search", "搜索论文卡片", "在用户主动保存的论文划词卡片中搜索。", Risk.READ,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 400},
                    "kind": {"type": "string", "enum": ["", "explain", "translate", "term", "ask"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            lambda p: database.reading_cards(
                str(p.get("query", "")),
                str(p.get("kind", "")),
                int(p.get("limit", 40)),
            ),
        )
    )
    registry.register(
        ComponentAction(
            "reading-cards", "save", "保存论文卡片", "把用户明确要求保留的原文与当前回答写入本地论文卡片。", Risk.MUTATE,
            {
                "type": "object",
                "required": ["sourceText", "answer"],
                "properties": {
                    "sourceText": {"type": "string", "minLength": 1, "maxLength": 5000},
                    "answer": {"type": "string", "minLength": 1, "maxLength": 5000},
                    "kind": {"type": "string", "enum": ["explain", "translate", "term", "ask"]},
                    "question": {"type": "string", "maxLength": 1200},
                    "title": {"type": "string", "maxLength": 120},
                },
                "additionalProperties": False,
            },
            lambda p: {
                "cardId": database.save_reading_card(
                    str(p["sourceText"]),
                    str(p["answer"]),
                    kind=str(p.get("kind", "explain")),
                    question=str(p.get("question", "")),
                    title=str(p.get("title", "")),
                    metadata={"origin": "component"},
                )
            },
        )
    )
    registry.register(
        ComponentAction(
            "reading-cards", "delete", "删除论文卡片", "永久删除一张已经保存的论文卡片。", Risk.DESTRUCTIVE,
            string_schema("cardId", "论文卡片标识"),
            lambda p: {"deleted": database.delete_reading_card(str(p["cardId"]))},
        )
    )
    registry.register(
        ComponentAction(
            "model-status", "read", "模型状态", "读取电脑中现有的莉莉丝 0.5B 模型状态。", Risk.READ,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _p: model_status(),
        )
    )
    return registry


def register_v02_components(
    registry: ComponentRegistry,
    bindings: V02ComponentBindings,
) -> ComponentRegistry:
    """Register the v0.2 public socket/model actions against app-owned services."""

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register(
        ComponentAction(
            "desktop-peek", "status", "桌面往返状态", "读取当前是否正在临时查看桌面。",
            Risk.READ, empty, lambda _p: bindings.desktop_peek_status(),
        )
    )
    registry.register(
        ComponentAction(
            "desktop-peek", "toggle", "看桌面／返回工作", "成对收起并恢复本次由 Lilies 处理的窗口。",
            Risk.MUTATE, empty, lambda _p: bindings.desktop_peek_toggle(),
        )
    )
    registry.register(
        ComponentAction(
            "desktop-peek", "restore", "恢复工作窗口", "恢复仍可验证且仍被最小化的窗口事务。",
            Risk.MUTATE, empty, lambda _p: bindings.desktop_peek_restore(),
        )
    )
    registry.register(
        ComponentAction(
            "activity-context", "status", "情境感知状态", "读取脱敏后的感知状态，不读取按键或鼠标内容。",
            Risk.READ, empty, lambda _p: bindings.activity_status(),
        )
    )
    registry.register(
        ComponentAction(
            "activity-context", "set-policy", "应用感知策略", "为一个明确应用设置禁止、仅信号、允许观察或允许气泡。",
            Risk.MUTATE,
            {
                "type": "object", "required": ["application", "policy"],
                "properties": {
                    "application": {"type": "string", "minLength": 1, "maxLength": 260},
                    "policy": {"type": "string", "enum": ["blocked", "signal", "observe", "bubble"]},
                },
                "additionalProperties": False,
            },
            lambda p: bindings.activity_set_policy(str(p["application"]), str(p["policy"])),
        )
    )
    registry.register(
        ComponentAction(
            "companion", "preferences", "陪伴偏好", "读取陪伴频率、类别和兴趣／场景权重。",
            Risk.READ, empty, lambda _p: bindings.companion_preferences(),
        )
    )
    registry.register(
        ComponentAction(
            "companion", "reply", "回复气泡", "在当前独立气泡短会话中回复莉莉丝。",
            Risk.MUTATE,
            {
                "type": "object", "required": ["bubbleId", "text"],
                "properties": {
                    "bubbleId": {"type": "string", "minLength": 1, "maxLength": 160},
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                },
                "additionalProperties": False,
            },
            lambda p: bindings.companion_reply(str(p["bubbleId"]), str(p["text"])),
        )
    )
    registry.register(
        ComponentAction(
            "companion", "another", "换一个", "生成一个不重复刚才内容的新气泡。",
            Risk.MUTATE,
            {
                "type": "object",
                "properties": {"bubbleId": {"type": "string", "maxLength": 160}},
                "additionalProperties": False,
            },
            lambda p: bindings.companion_another(str(p.get("bubbleId", ""))),
        )
    )
    registry.register(
        ComponentAction(
            "companion", "snooze", "暂停陪伴", "在指定分钟内暂停主动气泡。",
            Risk.MUTATE,
            {
                "type": "object",
                "properties": {"minutes": {"type": "integer", "minimum": 1, "maximum": 1440}},
                "additionalProperties": False,
            },
            lambda p: {"until": str(bindings.companion_snooze(int(p.get("minutes", 60))))},
        )
    )
    registry.register(
        ComponentAction(
            "memory", "partitions", "记忆分区", "读取固定记忆分区的目录与摘要。",
            Risk.READ, empty, lambda _p: bindings.memory_partitions(),
        )
    )
    registry.register(
        ComponentAction(
            "memory", "recall", "查阅记忆", "在长度与次数限制内只读查阅少量本地记忆片段。",
            Risk.READ,
            {
                "type": "object", "required": ["partitionIds", "query", "timeRange", "limit"],
                "properties": {
                    "partitionIds": {"type": "array"},
                    "query": {"type": "string", "maxLength": 2000},
                    "timeRange": {},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 6},
                },
                "additionalProperties": False,
            },
            lambda p: bindings.memory_recall(p),
        )
    )
    registry.register(
        ComponentAction(
            "memory", "reindex", "重建记忆索引", "从仍保留的本地来源重新生成检索索引。",
            Risk.MUTATE, empty, lambda _p: bindings.memory_reindex(),
        )
    )
    registry.register(
        ComponentAction(
            "memory", "forget", "忘记片段", "从检索索引排除片段，但保留原对话。",
            Risk.MUTATE, string_schema("fragmentId", "记忆片段标识"),
            lambda p: bindings.memory_forget(str(p["fragmentId"]), False),
        )
    )
    registry.register(
        ComponentAction(
            "memory", "delete-source", "忘记并删除原对话", "永久删除记忆片段所关联的原始对话或卡片。",
            Risk.DESTRUCTIVE, string_schema("fragmentId", "记忆片段标识"),
            lambda p: bindings.memory_forget(str(p["fragmentId"]), True),
        )
    )
    registry.register(
        ComponentAction(
            "content", "sources", "内容来源", "读取当前研究与新闻来源及联网准备状态。",
            Risk.READ, empty, lambda _p: bindings.content_sources(),
        )
    )
    registry.register(
        ComponentAction(
            "content", "refresh", "刷新内容来源", "按已授权状态刷新一个来源，只缓存元数据与短摘要。",
            Risk.READ,
            {
                "type": "object", "required": ["providerId"],
                "properties": {
                    "providerId": {"type": "string", "minLength": 1, "maxLength": 80},
                    "query": {"type": "string", "maxLength": 300},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "additionalProperties": False,
            },
            lambda p: bindings.content_refresh(
                str(p["providerId"]), str(p.get("query", "")), int(p.get("limit", 10))
            ),
        )
    )
    return registry
