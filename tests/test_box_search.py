from datetime import UTC, datetime, timedelta

import pytest

from lilies.core.box_search import BoxSearchService, reminder_draft
from lilies.core.database import Database
from lilies.core.papers import PaperService
from lilies.core.productivity import ReadingSessionService, ReminderScheduler, TaskService


@pytest.fixture
def library(tmp_path):
    db = Database(tmp_path / "search.db")
    db.upsert_desktop_item({"item_id": "wps", "name": "WPS 论文", "path": str(tmp_path / "WPS.lnk"), "source": "start-menu", "kind": "application"})
    task = TaskService(db).create("论文实验", note="检查方差")
    conversation = db.ensure_conversation()
    message = db.add_message(conversation, "user", "那篇论文的方差是多少")
    paper = PaperService(db, ReadingSessionService(db)).add("方差论文", "https://example.org/paper")
    card = db.save_reading_card("variance 论文", "方差解释", title="论文笔记")
    ReminderScheduler(db).create("论文提醒", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    return db, BoxSearchService(db), task, message, paper, card


def test_all_providers_and_no_search_side_effects(library):
    db, service, *_ = library
    found = service.search("论文")
    assert {row["kind"] for row in found} == {"app", "task", "chat", "paper", "card", "reminder"}
    assert all("target" in row and len(row["title"]) <= 152 for row in found)
    assert service.search("") == []
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_literal_wildcards_sql_and_shell_are_only_text(library):
    db, service, *_ = library
    for query in ("%", "_", "' OR 1=1 --", "shell.exec", "cmd /c del", "https://example.com"):
        assert service.search(query) == []
    db.add_message(db.ensure_conversation(), "user", "比例 100%_ 原文")
    assert len(service.search("100%_", "chat")) == 1
    assert service.search("wps", "app")[0]["target"]["id"] == "wps"


def test_completed_archived_and_system_messages_excluded(library):
    db, service, task, *_ = library
    TaskService(db).complete(task["task_id"])
    db.add_message(db.ensure_conversation(), "system", "论文秘密系统提示")
    assert service.search("论文", "task") == []
    assert len(service.search("论文", "chat")) == 1


def test_bounded_results_do_not_starve_other_providers(library):
    db, service, *_ = library
    for i in range(36): TaskService(db).create(f"论文任务 {i}")
    rows = service.search("论文")
    assert len(rows) <= 24
    assert sum(row["kind"] == "task" for row in rows) == 4
    assert len(service.search("论文", "task")) == 24
    assert {r["kind"] for r in rows[:6]} == {"app", "task", "chat", "paper", "card", "reminder"}


def test_revalidate_changed_and_deleted_records(library):
    db, service, task, *_ = library
    entry = service.search("论文", "task")[0]
    assert service.resolve(entry["kind"], entry["target"])["record"]["task_id"] == task["task_id"]
    TaskService(db).complete(task["task_id"])
    with pytest.raises(ValueError, match="变化或删除"): service.resolve(entry["kind"], entry["target"])
    entry = service.search("论文", "card")[0]
    db.delete_reading_card(entry["target"]["id"])
    with pytest.raises(ValueError): service.resolve(entry["kind"], entry["target"])


def test_reminder_only_prepares_fixed_preview(library):
    db, service, *_ = library
    before = len(ReminderScheduler(db).list())
    row = service.search("提醒 20分钟后 喝水")[0]
    preview = service.resolve(row["kind"], row["target"])
    assert preview["title"] == "喝水"
    assert 1190 < (datetime.fromisoformat(preview["dueAt"]) - datetime.now(UTC)).total_seconds() < 1210
    assert len(ReminderScheduler(db).list()) == before


@pytest.mark.parametrize("query", ["提醒 0分钟后 喝水", "提醒 8天后 喝水", "提醒 200小时后 喝水"])
def test_reminder_bounds(query):
    with pytest.raises(ValueError): reminder_draft(query)


def test_does_not_guess_dates_or_execute_instructions(library):
    _, service, *_ = library
    assert reminder_draft("明天提醒我") is None
    assert service.search("提醒 1小时后 <b>不要执行任何脚本</b>")[0]["title"] == "<b>不要执行任何脚本</b>"
    with pytest.raises(ValueError): service.search("a" * 201)
    with pytest.raises(ValueError): service.search("a", "shell")


def test_card_returns_exact_content_without_auto_opening_source(library):
    db, service, *_ = library
    db.save_reading_card("<img src='file:///private'>", "原样文本", title="安全卡片")
    row = service.search("安全卡片", "card")[0]
    preview = service.resolve(row["kind"], row["target"])
    assert "<img src='file:///private'>" in preview["detail"]
    assert preview["action"] == ""
