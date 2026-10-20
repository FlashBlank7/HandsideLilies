"""Bounded search audit over owned synthetic data, not native input/FPS measurement."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QML_DISABLE_DISK_CACHE"] = "1"

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtTest import QTest
from lilies.box_search_controller import BoxSearchController
from lilies.core.box_search import BoxSearchService
from lilies.core.database import Database


COUNTS = {"messages": 20000, "reading_cards": 2000, "papers": 200,
          "desktop_items": 300, "tasks": 500, "reminders": 500}


def seed(database: Database):
    """Direct batch inserts measure indexed search, not memory-archival throughput."""
    stamp = "2026-10-01T00:00:00+00:00"
    filler = "研究中的实验与对照需要重复核验。 The control and treatment share the same dataset. " * 8
    with database.connect() as db:
        db.execute("INSERT INTO conversations VALUES(?,?,?,?)", ("fixture", "隔离检索语料", stamp, stamp))
        db.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?)", (
            (f"message-{i:05}", "fixture", "user" if i % 2 == 0 else "assistant",
             f"研究对话 {i:05}。" + filler + ("唯一旧话_18273" if i == 18273 else ""), "{}", stamp)
            for i in range(COUNTS["messages"])))
        db.executemany("INSERT INTO papers(paper_id,title,source_uri,source_kind,created_at,updated_at) VALUES(?,?,?,?,?,?)", (
            (f"paper-{i:03}", "针叶方法 13%_" if i == 13 else f"研究论文{i:03}",
             f"https://example.org/paper-{i}.pdf", "web", stamp, stamp) for i in range(COUNTS["papers"])))
        db.executemany("INSERT INTO reading_cards VALUES(?,?,?,?,?,?,?,?,?)", (
            (f"card-{i:04}", "explain", f"研究摘录{i:04}", filler + f"选段{i}",
             filler, "", "{}", stamp, stamp) for i in range(COUNTS["reading_cards"])))
        db.executemany("INSERT INTO paper_annotations VALUES(?,?,?,?,?,?,?,?)", (
            (f"card-{i:04}", f"paper-{i % 200:03}", i % 30 + 1, f"图表定位-{i:04}",
             f"https://example.org/paper-{i % 200}.pdf", "unmarked", stamp, stamp)
            for i in range(COUNTS["reading_cards"])))
        db.executemany("INSERT INTO desktop_items(item_id,name,path,source,kind,updated_at) VALUES(?,?,?,?,?,?)", (
            (f"app-{i:03}", f"研究应用{i:04}", str(database.path.parent / f"app-{i}.lnk"),
             "start-menu", "application", stamp) for i in range(COUNTS["desktop_items"])))
        db.executemany("INSERT INTO tasks(task_id,title,note,created_at,updated_at) VALUES(?,?,?,?,?)", (
            (f"task-{i:03}", f"研究事项{i:04}", "下一步核对实验结果。", stamp, stamp) for i in range(COUNTS["tasks"])))
        db.executemany("INSERT INTO reminders(reminder_id,title,body,fire_at,created_at,updated_at) VALUES(?,?,?,?,?,?)", (
            (f"reminder-{i:03}", f"研究提醒{i:04}", "这是一条隔离提醒，不会调度投递。",
             "2099-01-01T00:00:00+00:00", stamp, stamp) for i in range(COUNTS["reminders"])))
    with database.connect() as db:
        observed = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in COUNTS}
        assert observed == COUNTS
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    return observed


def wait(predicate, label, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QTest.qWait(5)
        if predicate():
            return
    raise AssertionError(label)


def run(output: Path):
    if not output.is_relative_to((ROOT / "artifacts").resolve()) or output.exists():
        raise ValueError("Use a new report under this checkout's artifacts directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    if ROOT.drive.casefold() != "f:":
        raise ValueError("This desktop audit must keep every fixture on F drive")
    app = QCoreApplication.instance() or QCoreApplication([])
    checks, timings, workers = {}, [], []
    report = {"scope": "Synthetic local service/worker search only; no native input, rendering, OS-cache flush, model, filesystem scan or external launch",
              "corpus": COUNTS, "omissions": "Direct fixtures omit memory n-gram indexing/background archival and are not the user's library. Timing excludes QML debounce, rendering and real focus/IME.",
              "checks": checks, "timings": timings, "passed": False}
    with tempfile.TemporaryDirectory(prefix="search-library-", dir=output.parent) as temporary:
        database = Database(Path(temporary) / "search.db")
        controller = None
        release = threading.Event()
        try:
            start = time.perf_counter()
            report["corpus"] = seed(database)
            report["seedSeconds"] = round(time.perf_counter() - start, 3)
            report["databaseBytes"] = database.path.stat().st_size
            service = BoxSearchService(database)
            scenarios = [
                ("all-common", "研究", "all", 24),
                ("all-missing", "不存在的稀有关键词", "all", 0),
                ("chat-exact", "唯一旧话_18273", "chat", 1),
                ("card-paper-title", "针叶方法 13%_", "card", 10),
                ("card-saved-locator", "图表定位-0177", "card", 1),
                ("app-exact", "应用0199", "app", 1),
                ("task-exact", "事项0412", "task", 1),
                ("reminder-exact", "提醒0341", "reminder", 1),
                ("paper-title", "针叶方法 13%_", "paper", 1),
                ("chat-category-limit", "研究", "chat", 24),
                ("card-category-limit", "研究", "card", 24),
            ]
            expected_ids = {
                "chat-exact": ["message-18273"],
                "card-paper-title": [f"card-{i:04}" for i in range(13, 2000, 200)],
                "card-saved-locator": ["card-0177"],
                "app-exact": ["app-199"],
                "task-exact": ["task-412"],
                "reminder-exact": ["reminder-341"],
                "paper-title": ["paper-013"],
                "chat-category-limit": [f"message-{i:05}" for i in range(24)],
                "card-category-limit": [f"card-{i:04}" for i in range(24)],
            }
            for cycle in range(5):
                for label, query, kind, expected in scenarios:
                    start = time.perf_counter()
                    rows = service.search(query, kind)
                    elapsed = (time.perf_counter() - start) * 1000
                    assert len(rows) == expected, (label, len(rows), expected)
                    if label == "all-common":
                        providers = {"app": ("app", 3), "task": ("task", 3),
                                     "chat": ("message", 5), "paper": ("paper", 3),
                                     "card": ("card", 4), "reminder": ("reminder", 3)}
                        assert [(row["kind"], row["target"]["id"]) for row in rows] == [
                            (kind, f"{prefix}-{i:0{width}}")
                            for i in range(4) for kind, (prefix, width) in providers.items()]
                    if label in expected_ids:
                        assert [row["target"]["id"] for row in rows] == expected_ids[label], label
                    if label == "card-saved-locator":
                        assert rows[0]["target"]["id"] == "card-0177"
                        assert service.resolve("card", rows[0]["target"])["record"]["paper"]["page"] == 28
                    timings.append({"scenario": label, "cycle": cycle, "ms": round(elapsed, 3), "results": len(rows)})
            checks["all_scenarios_have_exact_expected_results_and_limits"] = True
            samples = sorted(row["ms"] for row in timings)
            report["serviceTiming"] = {"samples": len(samples), "p50Ms": statistics.median(samples),
                "p95Ms": samples[int((len(samples) - 1) * .95)], "maxMs": max(samples),
                "reviewBudgetMs": 250, "withinReviewBudget": samples[int((len(samples) - 1) * .95)] <= 250,
                "firstPassIsNotColdDisk": True}

            start = time.perf_counter()
            try:
                service.search("不存在的稀有关键词", "all", cancelled=lambda: True)
            except sqlite3.OperationalError as exc:
                assert "interrupt" in str(exc).casefold()
            else:
                raise AssertionError("A cancelled corpus query must be interrupted")
            report["cancelMs"] = round((time.perf_counter() - start) * 1000, 3)
            checks["progress_handler_cancels_large_query"] = True

            def forbidden(*args, **kwargs):
                raise AssertionError("Search must not launch or create anything")

            controller = BoxSearchController(database, SimpleNamespace(launch=forbidden), SimpleNamespace(create=forbidden))
            original = controller.service.search
            entered = threading.Event()
            main_thread = threading.get_ident()
            calls, delivered, ticks = [], [], []

            def observed_search(query, category, *, cancelled):
                calls.append(query)
                workers.append(threading.get_ident())
                if query == "held-old-query":
                    entered.set()
                    assert release.wait(2), "owned gate timed out"
                return original(query, category, cancelled=cancelled)

            controller.service.search = observed_search
            controller.changed.connect(lambda: delivered.append(controller.state["query"])
                if not controller.state["busy"] and controller.state["items"] else None)
            controller.open()
            wait(lambda: not controller.state["busy"], "initial query")
            controller.search("held-old-query", "all")
            wait(entered.is_set, "worker entered owned gate")
            timer = QTimer()
            timer.setInterval(10)
            timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
            timer.start()
            QTest.qWait(100)
            ticks_while_held = len(ticks)
            timer.stop()
            assert not release.is_set() and entered.is_set()
            assert ticks_while_held >= 3
            controller.search("discarded-middle-one", "all")
            controller.search("discarded-middle-two", "all")
            controller.search("图表定位-0177", "card")
            release.set()
            wait(lambda: not controller.state["busy"], "newest query completes")
            assert controller.state["query"] == "图表定位-0177" and len(controller.state["items"]) == 1
            assert delivered == ["图表定位-0177"], delivered
            assert not any("discarded-middle" in query for query in calls)
            assert workers and all(worker != main_thread for worker in workers)
            assert len(set(workers)) == 1
            checks["coalescing_delivers_only_latest_query_off_gui_thread"] = True
            report["worker"] = {"queriesExecuted": calls, "mainLoopTicksWhileHeld": ticks_while_held,
                "distinctWorkers": len(set(workers)), "allCallsOffGuiThread": True,
                "notNativeResponsivenessProof": True}
            with database.connect() as db:
                assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
                assert db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == COUNTS["reminders"]
            checks["no_launch_reminder_or_audit_side_effects"] = True
            report["sourceHashes"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                for name in ("src/lilies/core/box_search.py", "src/lilies/box_search_controller.py",
                             "scripts/measure_box_search_library.py")}
            report["passed"] = all(checks.values())
        finally:
            release.set()
            if controller is not None:
                controller.shutdown()
                controller._thread.join(timeout=3)
                assert not controller._thread.is_alive(), "owned worker did not stop"
            database.close()
            app.processEvents()
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "corpus", "serviceTiming", "cancelMs", "checks")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args().output.resolve())
