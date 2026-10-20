"""Explicit card provenance; models and reading windows are synthetic."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication

from lilies.core.database import Database
from lilies.core.papers import PaperService
from lilies.core.productivity import ReadingSessionService
from lilies.core.reading import prepare_reading_request
from lilies.core.selection import SelectionService


@pytest.fixture
def selection_case(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "selection.db")
    papers = PaperService(database, ReadingSessionService(database))
    paper = papers.add("正在读的 A 论文", "https://example.org/a.pdf")
    papers.update_position(paper["paper_id"], 4, "方法部分")
    papers.start(paper["paper_id"])

    class NoThread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass

    monkeypatch.setattr("lilies.core.selection.threading.Thread", NoThread)
    service = SelectionService(database, active=False)
    service.paper_context_provider = papers.capture_context
    # An eligible reader is NOT proof that its text belongs to the active paper.
    service._source_app = "notepad.exe"
    yield service, papers, database, paper, app
    service.shutdown()


def answer(service, action="explain", source="别处选中的文字"):
    service._start_request(prepare_reading_request(source, action), 20, 40)
    service._finish_request(service._request_id, "只解释这一段。", "")


def test_plain_save_does_not_invent_active_paper_provenance(selection_case):
    service, papers, database, paper, _ = selection_case
    answer(service)
    saved = service.save_current_card()
    assert saved and len(database.reading_cards()) == 1
    assert papers.card_page(paper["paper_id"])["total"] == 0


def test_term_action_never_auto_attaches_to_unconfirmed_paper(selection_case):
    service, papers, database, paper, _ = selection_case
    answer(service, "term")
    assert papers.card_page(paper["paper_id"])["total"] == 0
    assert not database.reading_cards(), "With a paper candidate, ask before saving."
    assert service.bubble["canSave"]


def test_preview_is_read_only_and_confirmation_freezes_original_page(selection_case):
    service, papers, database, paper, _ = selection_case
    answer(service)
    preview = service.prepare_card_save()
    assert preview["paperTitle"] == paper["title"]
    assert preview["page"] == 4 and preview["locator"] == "方法部分"
    assert not database.reading_cards()
    papers.update_position(paper["paper_id"], 9, "新的页码")
    # This is a copy, not a writable authority to alter the stored offer.
    preview["page"] = 100
    preview["sourceUri"] = "https://example.org/wrong.pdf"
    card = service.confirm_card_save(preview["id"], "paper")
    row = papers.cards(paper["paper_id"])[0]
    assert row["card_id"] == card and row["page"] == 4 and row["locator"] == "方法部分"
    assert database.reading_cards()[0]["metadata"]["paperAssociation"] == "user-confirmed"
    assert database.reading_cards()[0]["metadata"]["pageOrigin"] == "user"
    assert service.bubble["paperAssociation"] == "user-confirmed"
    assert not service.bubble["autoSaved"]
    with pytest.raises(ValueError):
        service.confirm_card_save(preview["id"], "paper")
    assert len(database.reading_cards()) == 1


def test_independent_choice_ignores_active_paper(selection_case):
    service, papers, database, paper, _ = selection_case
    answer(service)
    preview = service.prepare_card_save()
    saved = service.confirm_card_save(preview["id"], "independent")
    assert saved and not papers.cards(paper["paper_id"])
    assert database.reading_cards()[0]["metadata"]["paperAssociation"] == "independent"
    assert service.bubble["paperAssociation"] == "independent"


@pytest.mark.parametrize("change", ["cancel", "dismiss", "privacy", "supersede", "expire", "shutdown", "drag"])
def test_cancelled_or_stale_offer_never_writes(selection_case, monkeypatch, change):
    service, _, database, _, _ = selection_case
    answer(service)
    preview = service.prepare_card_save()
    if change == "cancel": service.cancel_card_save(preview["id"])
    if change == "dismiss": service.dismiss()
    if change == "privacy": service.set_privacy_suppressed(True)
    if change == "supersede": answer(service, source="新选区")
    if change == "expire":
        expired = service._save_offer["expiresAt"]
        monkeypatch.setattr("lilies.core.selection.time.monotonic", lambda: expired)
    if change == "shutdown": service.shutdown()
    if change == "drag": service.set_interaction_suspended(True)
    with pytest.raises(ValueError): service.confirm_card_save(preview["id"], "paper")
    assert not database.reading_cards()


@pytest.mark.parametrize("destination", ["another-paper", "", [], None, 1])
def test_invalid_destination_does_not_write(selection_case, destination):
    service, _, database, _, _ = selection_case
    answer(service)
    preview = service.prepare_card_save()
    with pytest.raises(ValueError): service.confirm_card_save(preview["id"], destination)
    assert not database.reading_cards()


def test_old_cancel_cannot_retire_new_offer(selection_case):
    service, _, database, _, _ = selection_case
    answer(service)
    old = service.prepare_card_save()
    new = service.prepare_card_save()
    service.cancel_card_save(old["id"])
    with pytest.raises(ValueError): service.confirm_card_save(old["id"], "paper")
    assert service.confirm_card_save(new["id"], "paper")
    assert len(database.reading_cards()) == 1


@pytest.mark.parametrize("change", ["remove-paper", "change-source"])
def test_stale_paper_fails_without_partial_card_or_memory(selection_case, change):
    service, _, database, paper, _ = selection_case
    answer(service)
    preview = service.prepare_card_save()
    with database.connect() as db:
        if change == "remove-paper": db.execute("DELETE FROM papers WHERE paper_id=?", (paper["paper_id"],))
        else: db.execute("UPDATE papers SET source_uri=? WHERE paper_id=?", ("https://example.org/new.pdf", paper["paper_id"]))
    with pytest.raises(ValueError): service.confirm_card_save(preview["id"], "paper")
    assert not database.reading_cards()
    with database.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memory_fragments WHERE source_type='reading-card'").fetchone()[0] == 0
    # The result is still available for an explicitly independent save.
    assert service.save_current_card()


def test_no_paper_cannot_be_forged_by_destination_choice(selection_case):
    service, _, database, _, _ = selection_case
    service.paper_context_provider = lambda: {}
    answer(service)
    preview = service.prepare_card_save()
    assert not preview["hasPaper"]
    with pytest.raises(ValueError): service.confirm_card_save(preview["id"], "paper")
    assert not database.reading_cards()
    assert service.confirm_card_save(preview["id"], "independent")


def test_error_busy_and_invisible_results_cannot_prepare_or_quick_save(selection_case):
    service, _, database, _, _ = selection_case
    for field, value in [("busy", True), ("error", True), ("visible", False)]:
        answer(service)
        service._bubble[field] = value
        with pytest.raises(ValueError): service.prepare_card_save()
        assert service.save_current_card() == ""
    assert not database.reading_cards()


def test_shutdown_cannot_prepare_a_new_offer(selection_case):
    service, _, database, _, _ = selection_case
    answer(service)
    service.shutdown()
    with pytest.raises(ValueError): service.prepare_card_save()
    assert not service.save_current_card() and not database.reading_cards()
