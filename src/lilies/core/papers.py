"""Explicit paper notebooks: provenance and progress, never implicit document reading."""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit, unquote

from .database import Database, utc_now
from .productivity import ReadingSessionService

MARKS = {"unmarked": "未整理", "understood": "已经明白", "question": "还没明白", "citation": "值得引用"}


def _needle(value: str) -> str:
    return "%" + str(value).strip()[:1000].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _page(total: int, offset: int, limit: int) -> dict[str, Any]:
    size = max(1, min(100, int(limit)))
    start = min(max(0, int(offset)), ((total - 1) // size) * size if total else 0)
    return {"total": total, "offset": start, "limit": size, "hasMore": start + size < total}


def paper_source(value: str) -> tuple[str, str]:
    """Accept explicit local PDFs and ordinary web links, not shell/custom URLs."""
    source = str(value).strip()
    if not source or len(source) > 4096 or any(ord(c) < 32 for c in source):
        raise ValueError("请选择本地 PDF，或填写论文网页地址")
    parts = urlsplit(source)
    if parts.scheme in {"http", "https"}:
        if not parts.hostname or parts.username or parts.password or "\\" in source or any(c.isspace() for c in source):
            raise ValueError("论文网址不应包含登录凭据")
        _ = parts.port  # validate malformed ports before a future browser launch
        # Page is maintained as an explicit notebook field, not guessed from URLs.
        return urlunsplit(parts._replace(fragment="")), "web"
    if parts.scheme == "file":
        if parts.netloc:
            raise ValueError("请使用本机 PDF，不支持网络共享路径")
        source = unquote(parts.path)
        if re.match(r"^/[A-Za-z]:/", source):
            source = source[1:]
    elif parts.scheme and not re.match(r"^[A-Za-z]:[\\/]", source):
        raise ValueError("仅支持本机 PDF 和 HTTP/HTTPS 论文链接")
    if source.startswith(("\\\\", "//")):
        raise ValueError("请使用本机 PDF，不支持网络共享路径")
    path = Path(source)
    if not path.is_absolute() or path.suffix.casefold() != ".pdf":
        raise ValueError("没有找到这个本机 PDF 文件")
    resolved_path = path.resolve()
    resolved = str(resolved_path)
    if resolved.startswith(("\\\\", "//")):
        raise ValueError("请使用本机 PDF，不支持网络共享路径")
    # A PDF-looking link must not turn into an executable/custom target after
    # canonicalization. Check the actual path before handing it to the OS.
    if resolved_path.suffix.casefold() != ".pdf" or not resolved_path.is_file():
        raise ValueError("没有找到这个本机 PDF 文件")
    return resolved, "file"


class PaperService:
    def __init__(self, database: Database, reading: ReadingSessionService):
        self.database = database
        self.reading = reading

    def get(self, paper_id: str) -> dict[str, Any] | None:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
        return dict(row) if row else None

    def list(self, query: str = "") -> list[dict[str, Any]]:
        return self.library_page(query)["items"]

    def library_page(self, query: str = "", *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        with self.database.connect() as db:
            total = db.execute("SELECT COUNT(*) FROM papers WHERE title LIKE ? ESCAPE '\\'", (_needle(query),)).fetchone()[0]
            page = _page(total, offset, limit)
            items = [dict(row) for row in db.execute("""SELECT p.*,
                (SELECT COUNT(*) FROM paper_annotations a WHERE a.paper_id=p.paper_id) AS card_count
                FROM papers p WHERE title LIKE ? ESCAPE '\\'
                ORDER BY updated_at DESC,paper_id LIMIT ? OFFSET ?""",
                (_needle(query), page["limit"], page["offset"])).fetchall()]
        return {**page, "items": items, "query": str(query)}

    def selected(self) -> dict[str, Any] | None:
        return self.get(str(self.database.get_setting("selected_paper_id", "")))

    def _check_switch(self, paper_id: str = "") -> None:
        session = self.reading.status()
        if session and str(session.get("source", "")) != "paper:" + paper_id:
            raise ValueError("先结束当前阅读，再切换到另一篇论文")

    def add(self, title: str, source: str) -> dict[str, Any]:
        uri, kind = paper_source(source)
        clean_title = str(title).strip()[:300]
        if not clean_title:
            clean_title = Path(uri).stem if kind == "file" else urlsplit(uri).hostname or "论文"
        now = utc_now()
        with self.database.connect() as db:
            # Windows file identities are case-insensitive, URL paths are not.
            clause = "source_uri=? COLLATE NOCASE" if kind == "file" and os.name == "nt" else "source_uri=?"
            existing = db.execute("SELECT * FROM papers WHERE " + clause, (uri,)).fetchone()
            self._check_switch(str(existing["paper_id"]) if existing else "")
            if existing:
                paper_id = str(existing["paper_id"])
            else:
                paper_id = uuid.uuid4().hex
                db.execute("INSERT INTO papers(paper_id,title,source_uri,source_kind,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                           (paper_id, clean_title, uri, kind, now, now))
        self.select(paper_id)
        return self.get(paper_id) or {}

    def select(self, paper_id: str) -> None:
        if self.get(paper_id) is None:
            raise ValueError("这篇论文记录已不存在")
        self._check_switch(paper_id)
        self.database.set_setting("selected_paper_id", paper_id)

    def update_position(self, paper_id: str, page: int, locator: str = "", next_step: str | None = None) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100000:
            raise ValueError("页码应在 1 到 100000 之间")
        with self.database.connect() as db:
            current = db.execute("SELECT * FROM papers WHERE paper_id=?", (paper_id,)).fetchone()
            if current is None:
                raise ValueError("这篇论文记录已不存在")
            db.execute("UPDATE papers SET last_page=?,locator=?,next_step=?,updated_at=? WHERE paper_id=?",
                       (int(page), str(locator).strip()[:200], current["next_step"] if next_step is None else str(next_step).strip()[:2000], utc_now(), paper_id))

    def start(self, paper_id: str) -> dict[str, Any]:
        self.select(paper_id)
        if self.reading.status():
            raise ValueError("这篇论文已经在阅读中")
        paper = self.get(paper_id)
        assert paper is not None
        return self.reading.start(title=paper["title"], source="paper:" + paper_id)

    def capture_context(self) -> dict[str, Any]:
        """A selected library row alone is NOT authorization to attach selections."""
        session = self.reading.status()
        source = str((session or {}).get("source", ""))
        if not source.startswith("paper:") or (session or {}).get("state") != "running":
            return {}
        paper = self.get(source[6:])
        if not paper:
            return {}
        return {"paperId": paper["paper_id"], "paperTitle": paper["title"], "sourceUri": paper["source_uri"],
                "page": paper["last_page"], "locator": paper["locator"], "pageOrigin": "user"}

    def cards(self, paper_id: str, *, marked_only: bool = False) -> list[dict[str, Any]]:
        return self.card_page(paper_id, marked_only=marked_only)["items"]

    def card_page(self, paper_id: str | None, *, marked_only: bool = False, query: str = "",
                  offset: int = 0, limit: int = 40) -> dict[str, Any]:
        # None means all cards, including legacy unassociated selections. An
        # empty paper id means no selected paper, never an implicit global list.
        clauses, parameters = [], []
        if paper_id is not None:
            clauses.append("a.paper_id=?")
            parameters.append(paper_id)
        if marked_only:
            clauses.append("a.mark!='unmarked'")
        if str(query).strip():
            clauses.append("(c.title LIKE ? ESCAPE '\\' OR c.source_text LIKE ? ESCAPE '\\' OR c.answer LIKE ? ESCAPE '\\' OR c.question LIKE ? ESCAPE '\\' OR p.title LIKE ? ESCAPE '\\')")
            parameters.extend([_needle(query)] * 5)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        tables = " FROM reading_cards c LEFT JOIN paper_annotations a ON c.card_id=a.card_id LEFT JOIN papers p ON p.paper_id=a.paper_id"
        with self.database.connect() as db:
            total = db.execute("SELECT COUNT(*)" + tables + where, parameters).fetchone()[0]
            page = _page(total, offset, limit)
            rows = db.execute("SELECT c.*,a.paper_id,a.page,a.locator,a.source_uri,a.mark,p.title AS paper_title" + tables + where +
                " ORDER BY c.created_at DESC,c.card_id LIMIT ? OFFSET ?", (*parameters, page["limit"], page["offset"])).fetchall()
        return {**page, "items": [{**dict(row), "markLabel": MARKS.get(row["mark"], "独立划词")} for row in rows]}

    def mark(self, paper_id: str, card_id: str, mark: str) -> None:
        if mark not in MARKS:
            raise ValueError("未知的阅读标记")
        with self.database.connect() as db:
            changed = db.execute("UPDATE paper_annotations SET mark=?,updated_at=? WHERE card_id=? AND paper_id=?",
                                 (mark, utc_now(), card_id, paper_id))
            if not changed.rowcount:
                raise ValueError("这张卡片不属于当前论文")

    def question(self, paper_id: str, question: str) -> str:
        paper = self.get(paper_id)
        text = str(question).strip()
        if not paper or not text or len(text) > 4000:
            raise ValueError("请填写一个 4000 字以内的问题")
        return self.database.save_reading_card(text, "", kind="question-note", title=text[:72],
            metadata={"author": "user", "generated": False},
            paper_context={"paperId": paper_id, "sourceUri": paper["source_uri"], "page": paper["last_page"], "locator": paper["locator"]})

    def summary(self, paper_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            counts = {row["mark"]: row["n"] for row in db.execute(
                "SELECT mark,COUNT(*) AS n FROM paper_annotations WHERE paper_id=? GROUP BY mark", (paper_id,)).fetchall()}
            latest = db.execute("""SELECT c.title,c.source_text,a.page,a.mark FROM paper_annotations a
                JOIN reading_cards c ON a.card_id=c.card_id WHERE a.paper_id=?
                ORDER BY c.created_at DESC,c.card_id LIMIT 1""", (paper_id,)).fetchone()
        return {"groups": [{"mark": mark, "label": MARKS[mark], "count": counts.get(mark, 0)}
                           for mark in ("question", "understood", "citation")],
                "markedCount": sum(n for mark, n in counts.items() if mark != "unmarked"),
                "questionCount": counts.get("question", 0), "totalCount": sum(counts.values()),
                "latest": dict(latest) if latest else {}, "generated": False}

    def open_target(self, paper_id: str, card_id: str = "", *, expected_source: dict | None = None) -> dict[str, Any]:
        paper = self.get(paper_id)
        if not paper:
            raise ValueError("这篇论文记录已不存在")
        page = paper["last_page"]
        uri = paper["source_uri"]
        if card_id:
            with self.database.connect() as db:
                card = db.execute("SELECT * FROM paper_annotations WHERE card_id=? AND paper_id=?", (card_id, paper_id)).fetchone()
            if card is None:
                raise ValueError("这张卡片不属于当前论文")
            if expected_source is not None and any(card[key] != expected_source.get(key)
                    for key in ("paper_id", "page", "locator", "source_uri")):
                raise ValueError("卡片出处已经变化，请重新查看后再打开")
            page, uri = int(card["page"]), str(card["source_uri"])
        source, kind = paper_source(uri)  # revalidate before a user-triggered launch
        url = Path(source).as_uri() if kind == "file" else source
        pdf_link = urlsplit(url).path.casefold().endswith(".pdf")
        return {"url": url + (f"#page={page}" if pdf_link else ""), "page": page,
                "exactPageGuaranteed": False, "note": f"原文第 {page} 页；是否自动跳页取决于阅读器"}
