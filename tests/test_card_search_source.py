"""Search saved paper provenance; no network, native input or real reader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lilies.core.box_search import BoxSearchService
from lilies.core.database import Database
from lilies.core.papers import PaperService, paper_source
from lilies.core.productivity import ReadingSessionService


def paper_card(database):
    papers = PaperService(database, ReadingSessionService(database))
    paper = papers.add("对照研究 7%_ 专属标题", "https://example.org/study.pdf")
    papers.update_position(paper["paper_id"], 3, "消融实验 · 表二")
    papers.start(paper["paper_id"])
    card_id = database.save_reading_card("Sample variance differs.", "两组波动不同。",
        title="方差摘录", paper_context=papers.capture_context())
    papers.update_position(paper["paper_id"], 18, "当前读到讨论", "检查局限")
    return papers, paper, card_id


@pytest.fixture
def saved(tmp_path):
    database = Database(tmp_path / "cards.db")
    papers, paper, card_id = paper_card(database)
    yield database, BoxSearchService(database), papers, paper, card_id
    database.close()


@pytest.mark.parametrize("query", ["专属标题", "7%_", "消融实验", "表二"])
def test_card_search_matches_paper_title_and_saved_locator(saved, query):
    _, service, _, paper, card_id = saved
    found = service.search(query, "card")
    assert len(found) == 1 and found[0]["target"]["id"] == card_id
    assert paper["title"] in found[0]["subtitle"]
    assert "第 3 页" in found[0]["subtitle"]
    assert service.search("当前读到讨论", "card") == []


def test_source_action_discloses_original_target_and_saved_page(saved):
    _, service, _, paper, _ = saved
    target = service.search("方差摘录", "card")[0]["target"]
    preview = service.resolve("card", target)
    assert preview["action"] == "回到原文"
    assert paper["source_uri"] in preview["detail"]
    assert "第 3 页" in preview["detail"] and "第 18 页" not in preview["detail"]
    assert "阅读器" in preview["detail"]


@pytest.mark.parametrize("change", ["page", "locator", "source_uri", "paper_title", "association"])
def test_source_changes_invalidate_existing_search_tokens(saved, change):
    database, service, _, paper, card_id = saved
    target = service.search("方差摘录", "card")[0]["target"]
    with database.connect() as db:
        if change == "paper_title":
            db.execute("UPDATE papers SET title='另一份标题' WHERE paper_id=?", (paper["paper_id"],))
        elif change == "association":
            db.execute("DELETE FROM paper_annotations WHERE card_id=?", (card_id,))
        else:
            value = {"page": 8, "locator": "不同位置", "source_uri": "https://example.org/other.pdf"}[change]
            db.execute(f"UPDATE paper_annotations SET {change}=? WHERE card_id=?", (value, card_id))
    with pytest.raises(ValueError, match="变化或删除"):
        service.resolve("card", target)


def test_current_reading_progress_and_marks_do_not_rewrite_saved_source(saved):
    _, service, papers, paper, card_id = saved
    target = service.search("方差摘录", "card")[0]["target"]
    papers.update_position(paper["paper_id"], 22, "下一章")
    papers.mark(paper["paper_id"], card_id, "understood")
    assert service.resolve("card", target)["record"]["paper"]["page"] == 3


def test_final_target_read_rejects_provenance_change_after_resolution(saved):
    database, service, papers, paper, card_id = saved
    target = service.search("方差摘录", "card")[0]["target"]
    source = service.resolve("card", target)["record"]["paper"]
    with database.connect() as db:
        db.execute("UPDATE paper_annotations SET page=4 WHERE card_id=?", (card_id,))
    with pytest.raises(ValueError, match="出处已经变化"):
        papers.open_target(paper["paper_id"], card_id, expected_source=source)


def test_pdf_named_link_cannot_resolve_to_an_executable(tmp_path, monkeypatch):
    alias = tmp_path / "looks-like-paper.pdf"
    executable = tmp_path / "not-a-reader.exe"
    alias.write_bytes(b"owned fixture")
    executable.write_bytes(b"not executable; resolution fixture only")
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs:
                        executable if path == alias else original_resolve(path, *args, **kwargs))
    with pytest.raises(ValueError, match="PDF"):
        paper_source(str(alias))


def test_paper_metadata_progress_does_not_replace_original_card_uri(saved):
    database, service, papers, paper, card_id = saved
    target = service.search("方差摘录", "card")[0]["target"]
    with database.connect() as db:
        db.execute("UPDATE papers SET source_uri='https://example.org/replacement.pdf' WHERE paper_id=?", (paper["paper_id"],))
    preview = service.resolve("card", target)
    source = papers.open_target(paper["paper_id"], card_id, expected_source=preview["record"]["paper"])
    assert source["url"] == "https://example.org/study.pdf#page=3"


def test_joined_card_results_stay_bounded_and_leave_standalone_cards_read_only(saved):
    database, service, _, paper, _ = saved
    with database.connection_session():
        for index in range(30):
            database.save_reading_card(f"摘录 {index}", "解释", paper_context={
                "paperId": paper["paper_id"], "sourceUri": paper["source_uri"], "page": 4})
        independent = database.save_reading_card("无来源的独立词条", "不猜测出处")
    assert len(service.search("专属标题", "card")) == 24
    assert sum(row["kind"] == "card" for row in service.search("专属标题")) == 4
    row = service.search("无来源的独立词条", "card")[0]
    assert row["target"]["id"] == independent
    assert service.resolve("card", row["target"])["action"] == ""


@pytest.mark.parametrize("mutation", ["none", "delete", "change-source"])
def test_gui_route_revalidates_then_opens_only_saved_source(tmp_path, monkeypatch, mutation):
    from lilies.backend import Backend
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    backend = Backend(smoke=True, force_compact=True)
    try:
        _, paper, card_id = paper_card(backend.database)
        service = backend.boxSearch.service
        target = service.search("方差摘录", "card")[0]["target"]
        preview = service.resolve("card", target)
        assert not opened
        if mutation == "delete":
            backend.database.delete_reading_card(card_id)
        elif mutation == "change-source":
            with backend.database.connect() as db:
                db.execute("UPDATE paper_annotations SET source_uri='https://example.org/swapped.pdf' WHERE card_id=?", (card_id,))
        # This is the queued GUI delivery after worker-side confirmation.
        backend._route_box_search("card", preview)
        if mutation == "none":
            assert opened == ["https://example.org/study.pdf#page=3"]
            assert backend.papers.selected()["last_page"] == 18
            with backend.database.connect() as db:
                rows = db.execute("SELECT * FROM audit_log WHERE action_id='open-card-source'").fetchall()
            assert len(rows) == 1
            assert rows[0]["risk"] == "launch" and rows[0]["completed_at"]
            assert json.loads(rows[0]["result_json"])["accepted"] is True
        else:
            assert opened == [] and backend.boxSearch.state["error"]
        assert backend.readingStatus["paperId"] == paper["paper_id"]
    finally:
        backend.shutdown()


@pytest.mark.parametrize("failure", ["audit-before", "audit-after", "reader-false", "reader-error"])
def test_source_open_audit_and_reader_failures_do_not_repeat_effect(tmp_path, monkeypatch, failure):
    import sqlite3
    from lilies.backend import Backend
    from PySide6.QtGui import QDesktopServices

    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    backend = Backend(smoke=True, force_compact=True)
    opened = []

    def opener(url):
        opened.append(url.toString())
        if failure == "reader-error":
            raise OSError("fixture reader failure")
        return failure != "reader-false"

    def audit_failure(*args, **kwargs):
        raise sqlite3.OperationalError("fixture database busy")

    monkeypatch.setattr(QDesktopServices, "openUrl", opener)
    try:
        paper_card(backend.database)
        service = backend.boxSearch.service
        preview = service.resolve("card", service.search("方差摘录", "card")[0]["target"])
        if failure == "audit-before":
            monkeypatch.setattr(backend.database, "audit", audit_failure)
        if failure == "audit-after":
            monkeypatch.setattr(backend.database, "complete_audit", audit_failure)
        backend._route_box_search("card", preview)
        assert len(opened) == (0 if failure == "audit-before" else 1)
        if failure == "audit-after":
            assert not backend.boxSearch.state["error"]  # Success must not invite another launch.
        else:
            assert backend.boxSearch.state["error"]
        if failure.startswith("reader-"):
            with backend.database.connect() as db:
                row = db.execute("SELECT * FROM audit_log WHERE action_id='open-card-source'").fetchone()
            assert row["completed_at"] and json.loads(row["error_json"])["type"] == "SourceOpenFailed"
    finally:
        backend.shutdown()
