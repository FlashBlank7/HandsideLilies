"""Exact saved-card source opening via production QML; all openers intercepted."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from verify_paper_resume_ui import ROOT, Notebook, wait, load_windows_ui_fonts
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output.resolve()
    if not output.is_relative_to((ROOT / "artifacts").resolve()) or output.exists():
        raise ValueError("Use a new report under this checkout's artifacts directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    load_windows_ui_fonts()
    app.setFont(QFont("Microsoft YaHei UI", 10))
    temporary = tempfile.TemporaryDirectory(prefix="card-source-", dir=output.parent)
    checks, warnings, models, opened, observations = {}, [], [], [], {}
    ui, error = None, ""
    try:
        os.environ["LILIES_DATA_DIR"] = temporary.name
        os.environ["QML_DISABLE_DISK_CACHE"] = "1"
        with patch.object(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True):
            ui = Notebook(warnings, models)
            backend = ui.backend
            service, database = backend.papers, backend.database
            window = ui.root.findChild(QQuickWindow, "boxSearchWindow")
            controller = backend.boxSearch
            web = service.add("方差对照研究", "https://example.org/variance.pdf")
            pdf = Path(temporary.name) / "本机 论文.pdf"
            pdf.write_bytes(b"owned path fixture; not parsed as a document")
            local = service.add("本机离线研究", str(pdf))
            service.update_position(local["paper_id"], 5, "本机原文")
            local_card = service.question(local["paper_id"], "本机问题")
            service.update_position(web["paper_id"], 3, "消融实验 · 图二")
            session = service.start(web["paper_id"])
            card_id = database.save_reading_card("Unequal sample variance.", "先比较波动，而不只是均值。",
                title="方差摘录", paper_context=service.capture_context())
            database.save_reading_card("独立词条", "没有给定出处，不能猜测。")
            service.update_position(web["paper_id"], 18, "讨论", "核对局限")
            backend.refreshReadingCards()

            def preview(query):
                if not controller.state["open"]:
                    backend.openBoxSearch()
                    wait(window.isVisible, "search shown")
                QTest.qWait(100)
                window.setProperty("category", "card")
                field = ui.find("boxSearchQuery", window)
                field.setProperty("text", "")
                field.setProperty("text", query)
                field.forceActiveFocus()
                wait(lambda: not controller.state["busy"] and controller.state["query"] == query, "card search")
                assert len(controller.state["items"]) == 1, controller.state
                QTest.keyClick(window, Qt.Key_Return)
                wait(lambda: controller.state["preview"].get("kind") == "card", "card preview")

            def confirm():
                ui.click("boxSearchConfirm", window)
                wait(lambda: not controller.state["busy"], "source confirmation")

            preview("方差对照研究")
            detail = controller.state["preview"]["detail"]
            checks["paper_title_search_shows_card_original_page_and_target"] = (
                "第 3 页" in detail and "第 18 页" not in detail
                and "https://example.org/variance.pdf" in detail
                and controller.state["preview"]["action"] == "回到原文")
            checks["preview_never_opens_source_or_requests_model"] = not opened and all(m.calls == 0 for m in models)
            for width in (520, 360):
                window.resize(width, 610)
                QTest.qWait(160)
                assert window.grabWindow().save(str(output.with_name(output.stem + f"-{width}.png")))
            window.resize(520, 610)
            confirm()
            controller.confirm()  # A second submission of the consumed preview is inert.
            QTest.qWait(100)
            checks["one_confirm_opens_saved_page_once_not_latest_progress"] = (
                opened == ["https://example.org/variance.pdf#page=3"] and not controller.state["open"]
                and service.selected()["last_page"] == 18
                and backend.reading_sessions.status()["session_id"] == session["session_id"])

            preview("消融实验")
            with database.connect() as db:
                db.execute("UPDATE paper_annotations SET source_uri='https://example.org/replaced.pdf' WHERE card_id=?", (card_id,))
            confirm()
            checks["changed_source_after_preview_is_rejected"] = (
                len(opened) == 1 and bool(controller.state["error"]) and not controller.state["preview"])
            with database.connect() as db:
                db.execute("UPDATE paper_annotations SET source_uri=? WHERE card_id=?", (web["source_uri"], card_id))

            preview("独立词条")
            assert not ui.find("boxSearchConfirm", window).isVisible()
            QTest.keyClick(window, Qt.Key_Return)
            QTest.qWait(100)
            checks["standalone_card_keeps_read_only_preview"] = len(opened) == 1 and not controller.state["preview"]["action"]

            preview("本机离线研究")
            confirm()
            observations["localSource"] = {"opened": opened[-1], "expected": pdf.as_uri() + "#page=5",
                "selectedPaperUnchanged": service.selected()["paper_id"] == web["paper_id"]}
            checks["local_pdf_opens_explicit_saved_page_without_switching_session"] = (
                QUrl(opened[-1]) == QUrl(pdf.as_uri() + "#page=5") and len(opened) == 2
                and service.selected()["paper_id"] == web["paper_id"])
            preview("本机离线研究")
            # Rename ONLY the synthetic file inside this owned fixture.
            moved = pdf.with_name("已移动的测试论文.pdf")
            assert pdf.is_relative_to(Path(temporary.name)) and moved.is_relative_to(Path(temporary.name))
            pdf.rename(moved)
            confirm()
            checks["moved_file_is_not_guessed_or_replaced"] = len(opened) == 2 and bool(controller.state["error"])
            preview("本机离线研究")
            database.delete_reading_card(local_card)
            confirm()
            checks["deleted_card_after_preview_never_opens"] = len(opened) == 2 and bool(controller.state["error"])

            # Deliberately corrupt only the fixture to check the launcher boundary.
            with database.connect() as db:
                db.execute("UPDATE paper_annotations SET source_uri='javascript:alert(1)' WHERE card_id=?", (card_id,))
            preview("方差对照研究")
            confirm()
            checks["unsafe_uri_is_rejected_without_shell_or_model"] = (
                len(opened) == 2 and bool(controller.state["error"]) and all(m.calls == 0 for m in models))
            with database.connect() as db:
                audits = db.execute("SELECT risk,completed_at,result_json FROM audit_log WHERE action_id='open-card-source'").fetchall()
            checks["only_explicit_valid_launches_have_completed_audit"] = (
                len(audits) == 2 and all(row["risk"] == "launch" and row["completed_at"]
                    and json.loads(row["result_json"])["accepted"] for row in audits))
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        if ui is not None:
            ui.close()
        temporary.cleanup()
        checks["no_qml_warnings_including_teardown"] = not warnings
        passed = bool(checks) and all(checks.values()) and not error
        output.write_text(json.dumps({"passed": passed, "checks": checks, "observations": observations, "warnings": warnings, "error": error,
            "scope": "Production Main.qml, owned offscreen inputs/renders, disposable F records/files, intercepted reader opens, no live model/account"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": passed, "checks": checks, "error": error}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
