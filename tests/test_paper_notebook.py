from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from lilies.core.database import Database
from lilies.core.papers import PaperService, paper_source
from lilies.core.productivity import ReadingSessionService
from lilies.core.reading import prepare_reading_request
from lilies.core.selection import SelectionService


def notebook(tmp_path):
    database = Database(tmp_path / "papers.db")
    reading = ReadingSessionService(database)
    papers = PaperService(database, reading)
    return database, reading, papers


def test_migration_preserves_legacy_cards_and_is_idempotent(tmp_path):
    database, _, papers = notebook(tmp_path)
    legacy = database.save_reading_card("旧划词", "旧解释")
    paper = papers.add("对照实验", "https://example.org/paper.pdf")
    papers.update_position(paper["paper_id"], 7, "消融实验", "检查表三的变量")
    again = Database(database.path)
    restored = PaperService(again, ReadingSessionService(again))
    assert restored.selected()["last_page"] == 7
    assert restored.selected()["next_step"] == "检查表三的变量"
    assert restored.card_page(None)["items"][0]["card_id"] == legacy
    assert restored.card_page(paper["paper_id"])["total"] == 0
    with again.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=8").fetchone()[0] == 1
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_only_explicit_running_paper_session_attaches_selections(tmp_path):
    _, reading, papers = notebook(tmp_path)
    first = papers.add("第一篇", "https://example.org/first.pdf")
    second = papers.add("第二篇", "https://example.org/second.pdf")
    assert papers.capture_context() == {}
    session = papers.start(first["paper_id"])
    assert papers.capture_context()["paperId"] == first["paper_id"]
    with pytest.raises(ValueError):
        papers.select(second["paper_id"])
    reading.pause(session["session_id"])
    assert papers.capture_context() == {}
    reading.resume(session["session_id"])
    assert papers.capture_context()["paperId"] == first["paper_id"]
    reading.finish(session["session_id"])
    reading.start(title="普通阅读计时")
    assert papers.capture_context() == {}


@pytest.mark.parametrize("source", ["powershell:whoami", "shell:startup", "javascript:alert(1)",
    "file://server/share/a.pdf", r"\\server\share\a.pdf", "https://name:secret@example.org/a",
    "https://example.org:bad/a", "https://example.org\\@evil.test/a", "https://exa mple.org/a", "", "relative.pdf"])
def test_unsafe_or_ambiguous_sources_are_rejected(source):
    with pytest.raises(ValueError):
        paper_source(source)


@pytest.mark.parametrize("page", [0, 100001, -1, True, 1.2, "3", None])
def test_page_validation_does_not_silently_truncate(tmp_path, page):
    _, _, papers = notebook(tmp_path)
    paper = papers.add("页码", "https://example.org/a")
    with pytest.raises(ValueError):
        papers.update_position(paper["paper_id"], page)
    assert papers.selected()["last_page"] == 1


def test_snapshot_links_cards_to_old_page_without_inventing_exact_reader_support(tmp_path):
    database, _, papers = notebook(tmp_path)
    paper = papers.add("方法", "https://example.org/a.pdf#page=99")
    papers.update_position(paper["paper_id"], 3, "定义")
    papers.start(paper["paper_id"])
    snapshot = papers.capture_context()
    papers.update_position(paper["paper_id"], 12, "实验")
    card = database.save_reading_card("x", "y", paper_context=snapshot)
    target = papers.open_target(paper["paper_id"], card)
    assert target["page"] == 3 and target["url"].endswith("#page=3")
    assert target["exactPageGuaranteed"] is False
    assert papers.cards(paper["paper_id"])[0]["locator"] == "定义"
    assert papers.card_page(None)["items"][0]["paper_title"] == "方法"


def test_question_is_user_authored_review_excludes_unmarked_and_checks_owner(tmp_path):
    database, _, papers = notebook(tmp_path)
    paper = papers.add("方法", "https://example.org/a")
    other = papers.add("另一篇", "https://example.org/b")
    question = papers.question(paper["paper_id"], "为什么只比较均值？")
    row = papers.cards(paper["paper_id"])[0]
    assert row["answer"] == "" and row["mark"] == "question"
    assert json.loads(row["metadata_json"])["generated"] is False
    assert papers.summary(paper["paper_id"])["markedCount"] == 1
    with pytest.raises(ValueError):
        papers.mark(other["paper_id"], question, "citation")
    with pytest.raises(ValueError):
        papers.open_target(other["paper_id"], question)
    for mark in ("understood", "citation", "unmarked"):
        papers.mark(paper["paper_id"], question, mark)
        assert papers.summary(paper["paper_id"])["markedCount"] == (0 if mark == "unmarked" else 1)
    assert not papers.card_page(paper["paper_id"], marked_only=True)["items"]
    assert database.delete_reading_card(question)
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_annotations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_fragments WHERE source_id=?", (question,)).fetchone()[0] == 0


def test_stale_source_fails_atomically_and_provenance_cannot_be_reassigned(tmp_path):
    database, reading, papers = notebook(tmp_path)
    first = papers.add("第一篇", "https://example.org/a")
    session = papers.start(first["paper_id"])
    old = papers.capture_context()
    card = database.save_reading_card("x", "y", paper_context=old)
    reading.finish(session["session_id"])
    second = papers.add("第二篇", "https://example.org/b")
    papers.start(second["paper_id"])
    with pytest.raises(ValueError):
        database.save_reading_card("updated", "answer", card_id=card, paper_context=papers.capture_context())
    assert database.reading_cards()[0]["source_text"] == "x"
    assert papers.card_page(first["paper_id"])["total"] == 1
    assert papers.card_page(second["paper_id"])["total"] == 0
    with pytest.raises(ValueError):
        database.save_reading_card("stale", "answer", paper_context={**old, "sourceUri": "https://example.org/changed"})
    assert database.reading_cards(query="stale") == []


def test_pagination_search_and_counts_cover_more_than_old_300_limit(tmp_path):
    database, _, papers = notebook(tmp_path)
    paper = papers.add("增长 5%", "https://example.org/a")
    # Batch synthetic data avoids writing hundreds of independent transactions.
    with database.connect() as db:
        for index in range(325):
            card = f"card-{index:03}"
            db.execute("INSERT INTO reading_cards VALUES(?,?,?,?,?,?,?,?,?)", (card, "explain", card, "原文", "答案", "", "{}", "2026-10-03", "2026-10-03"))
            db.execute("INSERT INTO paper_annotations VALUES(?,?,?,?,?,?,?,?)", (card, paper["paper_id"], 1, "", paper["source_uri"], "question" if index < 310 else "unmarked", "2026-10-03", "2026-10-03"))
        for index in range(110):
            db.execute("INSERT INTO papers(paper_id,title,source_uri,source_kind,created_at,updated_at) VALUES(?,?,?,?,?,?)", (str(index), f"论文{index}", f"https://example.org/{index}", "web", "2026-10-03", "2026-10-03"))
    assert papers.summary(paper["paper_id"])["markedCount"] == 310
    found = set()
    offset = 0
    while True:
        page = papers.card_page(paper["paper_id"], marked_only=True, offset=offset)
        found.update(card["card_id"] for card in page["items"])
        if not page["hasMore"]:
            break
        offset += page["limit"]
    assert len(found) == 310
    assert papers.card_page(None, query="5%")["total"] == 325
    assert papers.library_page("5%")["total"] == 1
    assert papers.library_page("_")["total"] == 0
    assert papers.library_page(offset=100)["total"] == 111
    assert len(papers.library_page(offset=100)["items"]) == 11


def test_selection_async_and_followup_keep_original_source_but_no_paper_model_context(tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    database, reading, papers = notebook(tmp_path)
    paper = papers.add("不能进入模型的论文标题", "https://example.org/private-title")
    papers.update_position(paper["paper_id"], 4, "原来的位置")
    session = papers.start(paper["paper_id"])
    pending, sent = [], []

    class DeferredThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            pending.append(self.target)

    class Model:
        def explain(self, text):
            sent.append(text)
            return "简单解释"
        def reading_action(self, source, action, question):
            sent.append((source, action, question))
            return "术语：相关性"
        def stop(self):
            pass

    monkeypatch.setattr("lilies.core.selection.threading.Thread", DeferredThread)
    selection = SelectionService(database, active=False)
    selection._subscription = Model()
    selection.paper_context_provider = papers.capture_context
    selection._source_app = "wpspdf.exe"
    selection._last_text = "correlation is not causation"
    selection._start_request(prepare_reading_request(selection._last_text), 40, 80)
    papers.update_position(paper["paper_id"], 9, "新的位置")
    selection._source_app = "another.exe"
    pending.pop(0)()
    app.processEvents()
    preview = selection.prepare_card_save()
    saved = selection.confirm_card_save(preview["id"], "paper")
    assert papers.cards(paper["paper_id"])[0]["page"] == 4
    assert database.reading_cards()[0]["metadata"]["sourceApp"] == "wpspdf.exe"
    reading.finish(session["session_id"])
    papers.add("新的论文", "https://example.org/b")
    selection.request_action("term")
    pending.pop(0)()
    app.processEvents()
    assert papers.card_page(paper["paper_id"])["total"] == 1
    assert not selection.bubble["autoSaved"]
    preview = selection.prepare_card_save()
    selection.confirm_card_save(preview["id"], "paper")
    assert papers.card_page(paper["paper_id"])["total"] == 2
    assert all(card["page"] == 4 for card in papers.cards(paper["paper_id"]))
    assert sent == ["correlation is not causation", ("correlation is not causation", "term", "")]
    assert saved
    selection.shutdown()


def test_backend_paper_open_is_explicit_and_refresh_does_not_launch(tmp_path, monkeypatch):
    from lilies.backend import Backend
    from lilies import backend as backend_module

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    opened = []
    monkeypatch.setattr(backend_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    backend = Backend(smoke=True, force_compact=True)
    try:
        assert backend.paperAdd("测试论文", "https://example.org/paper.pdf")["ok"]
        paper_id = backend.paperState["current"]["paper_id"]
        assert backend.paperSavePosition(paper_id, 8, "讨论", "看局限")["ok"]
        assert backend.paperStart(paper_id)["ok"]
        backend.refreshReadingCards()
        assert opened == []
        assert backend.paperOpenSource(paper_id)["ok"]
        assert opened == ["https://example.org/paper.pdf#page=8"]
        backend.readingFinish()
        assert not backend.paperState["active"]
        assert backend.paperState["current"]["next_step"] == "看局限"
    finally:
        backend.shutdown()


@pytest.mark.parametrize("origin", ["search", "world"])
def test_only_successful_source_navigation_announces_new_paper_view(tmp_path, monkeypatch, origin):
    from lilies.backend import Backend

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    navigated, presented = [], []
    backend.paperRecordOpened.connect(navigated.append)
    backend.openReadingDeskRequested.connect(lambda: presented.append(True))

    def route(paper_id):
        if origin == "search":
            backend._route_box_search("paper", {"record": {"paper_id": paper_id}})
        else:
            backend._open_shared_source({"kind": "paper", "id": paper_id})

    try:
        first = backend.papers.add("第一篇", "https://example.org/first.pdf")["paper_id"]
        second = backend.papers.add("第二篇", "https://example.org/second.pdf")["paper_id"]
        backend.paperBrowseCards(True, False, "旧筛选", 40)
        backend.refreshReadingCards()
        backend.openReadingDesk()
        assert navigated == []
        presented.clear()
        view = dict(backend._paper_card_view)
        route("missing")
        assert navigated == presented == []
        assert backend.papers.selected()["paper_id"] == second
        assert backend._paper_card_view == view
        backend.paperStart(second)
        route(first)
        assert navigated == presented == []
        assert backend.readingStatus["paperId"] == second
        assert backend._paper_card_view == view
        backend.readingFinish()
        route(first)
        assert navigated == [first] and presented == [True]
        assert backend.papers.selected()["paper_id"] == first
        assert not backend.readingStatus["active"]
        route(first)
        assert navigated == [first, first] and presented == [True, True]
    finally:
        backend.shutdown()


def test_local_pdf_link_and_card_deletion_never_edit_original(tmp_path):
    database, _, papers = notebook(tmp_path)
    # This is only a path/immutability fixture, not a document being parsed.
    pdf = tmp_path / "论文 空格.pdf"
    pdf.write_bytes(b"fixture-never-read-as-document")
    original = pdf.read_bytes()
    paper = papers.add("本机论文", pdf.as_uri())
    assert papers.add("同一份", str(pdf))["paper_id"] == paper["paper_id"]
    question = papers.question(paper["paper_id"], "问题")
    target = papers.open_target(paper["paper_id"], question)
    assert target["url"] == pdf.as_uri() + "#page=1"
    database.delete_reading_card(question)
    assert pdf.read_bytes() == original
    pdf.unlink()
    with pytest.raises(ValueError):
        papers.open_target(paper["paper_id"])


def test_superseded_response_cannot_relabel_new_selection(tmp_path, monkeypatch):
    database, _, papers = notebook(tmp_path)
    paper = papers.add("测试", "https://example.org/paper")
    papers.start(paper["paper_id"])

    class NoThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass

    monkeypatch.setattr("lilies.core.selection.threading.Thread", NoThread)
    selection = SelectionService(database, active=False)
    selection.paper_context_provider = papers.capture_context
    selection._start_request(prepare_reading_request("第一页"), 0, 0)
    old_id = selection._request_id
    papers.update_position(paper["paper_id"], 8)
    selection._start_request(prepare_reading_request("第八页"), 0, 0)
    new_id = selection._request_id
    selection._finish_request(old_id, "迟到的解释", "")
    assert selection._current_answer == ""
    assert old_id not in selection._pending_provenance
    selection._finish_request(new_id, "现在的解释", "")
    preview = selection.prepare_card_save()
    selection.confirm_card_save(preview["id"], "paper")
    assert papers.cards(paper["paper_id"])[0]["page"] == 8
    selection.shutdown()


def test_paper_migration_failure_rolls_back_and_can_retry():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at TEXT)")
    connection.execute("CREATE TABLE reading_cards(card_id TEXT PRIMARY KEY)")
    connection.commit()

    class FailIndex:
        def execute(self, sql, *args):
            if sql.startswith("CREATE INDEX paper_annotation_lookup"):
                raise sqlite3.OperationalError("injected failure")
            return connection.execute(sql, *args)
        def commit(self):
            connection.commit()
        def rollback(self):
            connection.rollback()

    with pytest.raises(sqlite3.OperationalError):
        Database._apply_paper_notebook_migration(FailIndex())
    assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='papers'").fetchone()
    assert not connection.execute("SELECT 1 FROM schema_migrations WHERE version=8").fetchone()
    Database._apply_paper_notebook_migration(connection)
    assert connection.execute("SELECT 1 FROM schema_migrations WHERE version=8").fetchone()
    connection.close()
