"""Local, bounded search. Text is data; it never becomes a command or tool name."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any


KINDS = {"app": "应用", "task": "任务", "chat": "对话", "paper": "论文", "card": "阅读卡", "reminder": "提醒"}
# One joined snapshot is used for searching AND revalidation. Current reading
# progress/marks are deliberately excluded: a saved card keeps its old location.
CARD_TABLE = """(SELECT c.*, a.paper_id AS saved_paper_id, a.page AS saved_page,
    a.locator AS saved_locator, a.source_uri AS saved_source_uri, p.title AS saved_paper_title
    FROM reading_cards c LEFT JOIN paper_annotations a ON c.card_id=a.card_id
    LEFT JOIN papers p ON p.paper_id=a.paper_id)"""
SPECS = {
    "app": ("desktop_items", "item_id", "name", "kind='application'", "name"),
    "task": ("tasks", "task_id", "title", "archived=0 AND status='open'", "title || ' ' || note"),
    "chat": ("messages", "message_id", "content", "role IN ('user','assistant')", "content"),
    "paper": ("papers", "paper_id", "title", "1=1", "title || ' ' || locator || ' ' || next_step"),
    "card": (CARD_TABLE, "card_id", "title", "1=1", "title || ' ' || source_text || ' ' || answer || ' ' || question || ' ' || COALESCE(saved_paper_title,'') || ' ' || COALESCE(saved_locator,'')"),
    "reminder": ("reminders", "reminder_id", "title", "state='pending'", "title || ' ' || body"),
}


def fingerprint(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def reminder_draft(query: str) -> dict | None:
    # Deliberately a visible, small grammar, not a general natural-language executor.
    match = re.fullmatch(r"提醒\s+(\d{1,5})\s*(分钟|小时|天)后\s+(.+)", query)
    if not match:
        return None
    minutes = int(match[1]) * {"分钟": 1, "小时": 60, "天": 1440}[match[2]]
    title = match[3].strip()
    if not 1 <= minutes <= 10080 or not title or len(title) > 200:
        raise ValueError("提醒支持 1 分钟至 7 天，内容最多 200 字")
    return {"minutes": minutes, "title": title}


def excerpt(value: str, query: str, limit: int = 150) -> str:
    value = " ".join(str(value).split())
    at = value.casefold().find(query.casefold())
    start = max(0, at - 28)
    return ("…" if start else "") + value[start:start + limit] + ("…" if len(value) > start + limit else "")


class BoxSearchService:
    def __init__(self, database):
        self.database = database

    def search(self, query: str, category: str = "all", *, cancelled=lambda: False) -> list[dict[str, Any]]:
        query = str(query).strip()
        if len(query) > 200:
            raise ValueError("搜索词最多 200 字")
        if category != "all" and category not in KINDS:
            raise ValueError("未知搜索分类")
        if not query:
            return []
        draft = reminder_draft(query) if category in {"all", "reminder"} else None
        if draft:
            return [{"kind": "new-reminder", "title": draft["title"], "subtitle": f"{draft['minutes']} 分钟后 · 先预览，不会直接创建",
                     "target": draft}]
        needle = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        groups = []
        deadline = time.monotonic() + 1.5
        with self.database.connect() as db:
            db.execute("PRAGMA busy_timeout=200")
            db.set_progress_handler(lambda: int(cancelled() or time.monotonic() > deadline), 1000)
            try:
                for kind in KINDS if category == "all" else (category,):
                    table, key, title, where, field = SPECS[kind]
                    # All identifiers are fixed above. LIMIT bounds both UI allocation and returned text.
                    recent = "created_at" if kind == "chat" else "updated_at"
                    rows = db.execute(f"SELECT * FROM {table} WHERE {where} AND ({field}) LIKE ? ESCAPE '\\' "
                        f"ORDER BY CASE WHEN {title}=? COLLATE NOCASE THEN 0 ELSE 1 END, {recent} DESC, {key} LIMIT ?",
                        (needle, query, 4 if category == "all" else 24)).fetchall()
                    group = []
                    for raw in rows:
                        row = dict(raw)
                        subtitle = KINDS[kind]
                        if kind == "chat": subtitle += " · " + ("你" if row["role"] == "user" else "莉莉丝") + " · " + row["created_at"][:10]
                        if kind == "app": subtitle += " · " + {"start-menu": "开始菜单", "desktop": "桌面", "custom": "自选目录"}.get(row.get("source"), "应用库") + " · 打开"
                        if kind == "task": subtitle += " · 查看后开始专注"
                        if kind == "card":
                            if row["saved_paper_title"] is not None:
                                subtitle += " · " + excerpt(row["saved_paper_title"], query, 70) + f" · 第 {row['saved_page']} 页"
                            subtitle += " · " + excerpt(row["source_text"], query, 65)
                        if kind == "reminder": subtitle += " · " + row["fire_at"]
                        group.append({"kind": kind, "title": excerpt(row[title], query), "subtitle": subtitle,
                            "hint": str(row["path"]) if kind == "app" else subtitle,
                            "target": {"id": row[key], "version": fingerprint(row)}})
                    groups.append(group)
            finally:
                db.set_progress_handler(None, 0)
        # Interleave providers so a large app library never hides matching papers/chat.
        return [group[index] for index in range(24) for group in groups if index < len(group)][:24]

    def resolve(self, kind: str, target: dict) -> dict:
        if kind == "new-reminder":
            minutes, title = target["minutes"], target["title"]
            due = datetime.now(UTC) + timedelta(minutes=minutes)
            return {"kind": kind, "title": title, "dueAt": due.isoformat(), "timezone": "UTC",
                "detail": "提醒时间：" + due.astimezone().strftime("%Y-%m-%d %H:%M %Z"), "action": "确认创建提醒"}
        if kind not in SPECS:
            raise ValueError("不支持这个操作")
        table, key, title, where, _ = SPECS[kind]
        with self.database.connect() as db:
            db.execute("PRAGMA busy_timeout=200")
            row = db.execute(f"SELECT * FROM {table} WHERE {key}=? AND {where}", (target["id"],)).fetchone()
            if row is None or fingerprint(dict(row)) != target["version"]:
                raise ValueError("这条内容已经变化或删除，请重新搜索")
            result = dict(row)
            if kind == "card":
                source = {key: result.pop("saved_" + key) for key in
                          ("paper_id", "page", "locator", "source_uri", "paper_title")}
                result["paper"] = source if source["paper_id"] and source["paper_title"] is not None else {}
        detail = str(result.get("note", result.get("body", "")))
        action = {"app": "打开应用", "task": "开始 25 分钟专注", "chat": "回到这段对话",
                  "paper": "打开论文记录", "card": "", "reminder": ""}[kind]
        if kind == "card":
            p = result["paper"]
            source_label = (f"{p['paper_title']} · 第 {p['page']} 页\n{p['locator']}\n"
                            f"原文：{p['source_uri']}\n页码是保存时的位置，能否自动跳页取决于阅读器。\n\n"
                            if p else "未关联论文\n\n")
            action = "回到原文" if p else ""
            detail = source_label + "原文\n" + result["source_text"] + "\n\n莉莉丝的说明\n" + result["answer"]
            if result["question"]: detail += "\n\n追问\n" + result["question"]
        if kind == "reminder": detail = result["fire_at"] + "\n" + detail
        return {"kind": kind, "title": result[title], "detail": detail[:40000], "action": action,
                "record": result, "version": target["version"]}
