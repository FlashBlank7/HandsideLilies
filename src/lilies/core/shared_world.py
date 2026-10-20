"""Source-linked keepsakes. No invented history, copied private prose or model tools."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from .database import utc_now

SHELVES = {"papers": "纸页架", "letters": "小信笺", "projects": "工作台"}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_id(shelf: str, source_id: str, version: str = "") -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, "lilies-world:" + shelf + ":" + source_id + ":" + version).hex


class SharedWorldService:
    def __init__(self, database): self.database = database

    def sync(self) -> None:
        """Import references only, on explicit browse/refresh. Tombstones never resurrect."""
        now = utc_now()
        with self.database.connect() as db:
            db.create_function("world_record_id", 2, record_id)
            try:
                db.execute("""INSERT OR IGNORE INTO world_records
                    (record_id,shelf,source_id,occurred_at,saved_at,updated_at)
                    SELECT world_record_id('papers',p.paper_id),'papers',p.paper_id,MIN(r.started_at),?,?
                    FROM papers p JOIN reading_sessions r ON r.source='paper:' || p.paper_id
                    WHERE r.state IN ('running','paused','finished') GROUP BY p.paper_id""", (now, now))
                db.execute("""INSERT OR IGNORE INTO world_records
                    (record_id,shelf,source_id,occurred_at,saved_at,updated_at)
                    SELECT world_record_id('projects',o.occurrence_id),'projects',o.occurrence_id,o.completed_at,?,?
                    FROM task_occurrences o JOIN tasks t ON t.task_id=o.task_id
                    WHERE o.status='completed' AND o.completed_at IS NOT NULL AND t.category IN ('project','项目')""", (now, now))
            finally:
                db.create_function("world_record_id", 2, None)

    def save_letter(self, message_id: str, expected_text: str) -> str:
        # The visible row's text is a compare-and-swap guard, never new content to store.
        if not expected_text.strip() or len(expected_text) > 100000:
            raise ValueError("请选择一条已保存的对话")
        with self.database.connect() as db:
            row = db.execute("SELECT role,content,created_at FROM messages WHERE message_id=?", (message_id,)).fetchone()
            if row is None or row["role"] not in {"user", "assistant"} or row["content"] != expected_text:
                raise ValueError("这句话已变化或尚未保存，请重新打开对话后再收好")
            version = content_hash(row["content"])
            identifier = record_id("letters", message_id, version)
            now = utc_now()
            db.execute("""INSERT INTO world_records
                (record_id,shelf,source_id,source_hash,occurred_at,saved_at,updated_at)
                VALUES(?,'letters',?,?,?,?,?) ON CONFLICT(record_id) DO UPDATE SET visibility='visible',updated_at=excluded.updated_at""",
                (identifier, message_id, version, row["created_at"], now, now))
        return identifier

    def page(self, shelf="papers", hidden=False, offset=0, *, limit=20):
        if shelf not in SHELVES: raise ValueError("未知陈设")
        size = max(1, min(50, int(limit)))
        visibility = "hidden" if hidden else "visible"
        with self.database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM world_records WHERE shelf=? AND visibility=?", (shelf, visibility)).fetchone()[0]
            start = min(max(0, int(offset)), max(0, ((total - 1) // size) * size))
            entries = db.execute("SELECT * FROM world_records WHERE shelf=? AND visibility=? ORDER BY occurred_at DESC,record_id LIMIT ? OFFSET ?",
                (shelf, visibility, size, start)).fetchall()
            items = []
            for row in entries:
                data = self._resolve(db, dict(row))
                items.append({key: data[key] for key in ("id", "title", "label", "occurredAt", "savedAt", "available", "visibility", "shelf")})
            counts = {name: db.execute("SELECT COUNT(*) FROM world_records WHERE shelf=? AND visibility='visible'", (name,)).fetchone()[0] for name in SHELVES}
        return {"items": items, "shelf": shelf, "hidden": hidden, "total": total, "offset": start,
                "hasMore": start + size < total, "limit": size, "counts": counts}

    def detail(self, identifier: str):
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM world_records WHERE record_id=? AND visibility!='removed'", (identifier,)).fetchone()
            if row is None: raise ValueError("这份记录已移除")
            return self._resolve(db, dict(row))

    def _resolve(self, db, link: dict) -> dict[str, Any]:
        result = {"id": link["record_id"], "shelf": link["shelf"], "visibility": link["visibility"],
            "title": "原记录已不在这里", "label": "来源不可用", "body": "原内容已删除或无法核对，这里不保留另一份副本。",
            "occurredAt": link["occurred_at"], "savedAt": link["saved_at"], "available": False,
            "sourceId": link["source_id"], "route": {}, "sourceVersion": ""}
        source_id = link["source_id"]
        if link["shelf"] == "papers":
            paper = db.execute("SELECT * FROM papers WHERE paper_id=?", (source_id,)).fetchone()
            if paper:
                marks = db.execute("SELECT mark,COUNT(*) AS n FROM paper_annotations WHERE paper_id=? GROUP BY mark", (source_id,)).fetchall()
                label = {"understood": "已明白", "question": "还没明白", "citation": "值得引用", "unmarked": "未整理"}
                counts = " · ".join(f"{label[r['mark']]} {r['n']}" for r in marks) or "尚未收好阅读卡"
                result.update(title=paper["title"], label="本篇阅读记录", available=True,
                    body=f"原文：{paper['source_uri']}\n\n当前记录：第 {paper['last_page']} 页\n{paper['locator']}\n\n{counts}\n\n下一步\n{paper['next_step'] or '还没写下下一步'}\n\n开启过阅读不代表读完整篇。页码由你标记。",
                    route={"kind": "paper", "id": source_id}, sourceVersion=paper["updated_at"])
        elif link["shelf"] == "projects":
            row = db.execute("""SELECT o.*,t.title,t.note,t.status AS task_status,t.updated_at AS task_updated
                FROM task_occurrences o JOIN tasks t ON t.task_id=o.task_id WHERE o.occurrence_id=?""", (source_id,)).fetchone()
            if row:
                current = "本次事项已完成" if row["status"] == "completed" else "这次完成后来已撤销"
                result.update(title=row["title"], label=current, available=True,
                    body=f"当时标记完成：{link['occurred_at']}\n当前状态：{current}\n\n任务当前说明\n{row['note'] or '没有附加说明'}\n\n任务来源：{row['task_id']}\n实例：{source_id}\n\n这是一个项目事项，不代表整个项目都已完成。",
                    sourceVersion=row["task_updated"] + row["updated_at"])
        else:
            row = db.execute("SELECT * FROM messages WHERE message_id=? AND role IN ('user','assistant')", (source_id,)).fetchone()
            if row:
                text = row["content"]
                original = content_hash(text) == link["source_hash"]
                if not original:
                    # Reuse existing retry history, never copy the saved prose to another table.
                    text = next((r["content"] for r in db.execute("SELECT content FROM message_revisions WHERE message_id=? ORDER BY revision_id DESC", (source_id,))
                        if content_hash(r["content"]) == link["source_hash"]), "")
                if text:
                    speaker = "你" if row["role"] == "user" else "莉莉丝"
                    result.update(title=" ".join(text.split())[:80], label=speaker + ("的一句话" if original else "当时的回答 · 原对话已更新"),
                        body=text, available=True, sourceVersion=link["source_hash"],
                        route={"kind": "chat", "id": source_id, "conversationId": row["conversation_id"]},
                        note="这是主动保存的对话。故事和猜想不会因此变成正式设定。")
        return result

    def set_visibility(self, identifier: str, value: str):
        if value not in {"visible", "hidden", "removed"}: raise ValueError("未知显示方式")
        with self.database.connect() as db:
            row = db.execute("SELECT visibility FROM world_records WHERE record_id=?", (identifier,)).fetchone()
            if row is None or row["visibility"] == "removed": raise ValueError("这份记录已移除")
            db.execute("UPDATE world_records SET visibility=?,updated_at=? WHERE record_id=?", (value, utc_now(), identifier))
        # Removal retains only a reference tombstone so automatic sync cannot re-add it.
        # Original papers/messages/tasks, growth, memory and Canon are never changed.
