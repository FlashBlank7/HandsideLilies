from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from lilies.core.database import Database
from lilies.core.productivity import TaskService
from lilies.core.work_resumption import WorkResumptionService, resource


class Windows:
    def __init__(self):
        self.identities = {h: {"handle": h, "processId": h + 100, "processStarted": h + 1000,
            "processName": "reader.exe", "executableHash": "e" * 64, "className": "Reader",
            "titleHash": str(h) * 64} for h in [1, 2, 3]}
        self.placements = {h: {"flags": 0, "showCmd": 3 if h == 2 else 1, "minPosition": [0, 0],
            "maxPosition": [0, 0], "normalPosition": [100, 100, 900, 600], "device": [0, 0, 1920, 1080]} for h in [1, 2, 3]}
        self.minimized = {3}
        self.handles = [1, 2, 3]
        self.restored = []
        self.fail = False

    def list_handles(self): return list(self.handles)
    def identity(self, handle): return copy.deepcopy(self.identities.get(handle))
    def placement(self, handle): return copy.deepcopy(self.placements.get(handle))
    def is_minimized(self, handle): return handle in self.minimized
    def restore(self, handle, placement):
        if self.fail:
            raise OSError("blocked by OS")
        self.restored.append((handle, placement))
        return True
    def minimize(self, *args): raise AssertionError("Saving/restoring must never minimize")
    def foreground(self): raise AssertionError("No focus theft")
    def set_foreground(self, *args): raise AssertionError("No focus theft")


def setup(tmp_path):
    db = Database(tmp_path / "bookmarks.db")
    api, opened, layout, now = Windows(), [], ["display-1@150"], [100.0]
    tasks = TaskService(db)
    service = WorkResumptionService(db, api=api, opener=lambda item: opened.append(item["source"]) or True,
        layout=lambda: layout[0], task_get=tasks.get, clock=lambda: now[0])
    return db, service, api, opened, layout, now, tasks


def save(service, **kwargs):
    capture = service.capture()
    return service.save(capture["id"], title="先去开会", next_step="回来检查表三的方差",
        window_ids=[row["id"] for row in capture["windows"]], **kwargs)


def test_save_has_no_effects_and_restart_never_replays(tmp_path):
    db, service, api, opened, layout, now, tasks = setup(tmp_path)
    task = tasks.create("继续论文")
    record = save(service, task_id=task["task_id"], sources=["https://example.org/a#figure3"])
    assert not api.restored and not opened
    again = WorkResumptionService(Database(db.path), api=api, opener=lambda _: False,
        layout=lambda: layout[0], task_get=tasks.get)
    listing = again.list()
    assert listing["items"][0]["next_step"] == "回来检查表三的方差"
    assert listing["items"][0]["task_title"] == "继续论文"
    assert not api.restored and not opened
    preview = again.preview(record["id"])
    assert len(preview["windows"]) == 2  # pre-existing minimized stays out
    assert preview["resources"][0]["source"].endswith("#figure3")
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=9").fetchone()[0] == 1
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_only_selected_preview_items_execute_once(tmp_path):
    db, service, api, opened, _, _, _ = setup(tmp_path)
    record = save(service, sources=["https://example.org/a", "https://example.org/b"])
    preview = service.preview(record["id"])
    assert not opened and not api.restored
    window, link = preview["windows"][1], preview["resources"][0]
    result = service.confirm(preview["token"], window_ids=[window["id"]], resource_ids=[link["id"]])
    assert result["requested"] == 2
    assert api.restored == [(2, api.placements[2])]
    assert opened == ["https://example.org/a"]
    with pytest.raises(ValueError):
        service.confirm(preview["token"], window_ids=[], resource_ids=[])
    with db.connect() as connection:
        run = connection.execute("SELECT * FROM work_resumption_runs").fetchone()
        audit = connection.execute("SELECT * FROM audit_log WHERE component_id='work-resumption'").fetchone()
        assert run["finished_at"] and audit["completed_at"]
        assert "https://" not in json.dumps(dict(audit))
        assert "reader.exe" not in run["result_json"]


@pytest.mark.parametrize("change", ["closed", "other-desktop", "reuse", "title", "process", "display"])
def test_changed_window_is_skipped_after_preview(tmp_path, change):
    _, service, api, opened, layout, _, _ = setup(tmp_path)
    record = save(service)
    preview = service.preview(record["id"])
    if change in {"closed", "other-desktop"}: api.handles.remove(1)
    elif change == "reuse": api.identities[1]["processId"] += 1
    elif change == "title": api.identities[1]["titleHash"] = "changed"
    elif change == "process": api.identities[1]["processStarted"] += 1
    else: layout[0] = "different-DPI"
    result = service.confirm(preview["token"], window_ids=[preview["windows"][0]["id"]], resource_ids=[])
    assert result["skipped"] == 1 and result["requested"] == 0
    assert not api.restored and not opened


def test_new_windows_and_manually_restored_unselected_windows_untouched(tmp_path):
    _, service, api, _, _, _, _ = setup(tmp_path)
    record = save(service)
    api.handles.append(99)
    api.minimized.clear()
    preview = service.preview(record["id"])
    service.confirm(preview["token"], window_ids=[preview["windows"][0]["id"]], resource_ids=[])
    assert [item[0] for item in api.restored] == [1]


def test_save_revalidates_window_and_uses_confirmation_position(tmp_path):
    _, service, api, _, _, _, _ = setup(tmp_path)
    capture = service.capture()
    api.placements[1]["normalPosition"] = [300, 200, 1200, 800]
    record = service.save(capture["id"], title="论文", next_step="读表三", window_ids=[capture["windows"][0]["id"]])
    preview = service.preview(record["id"])
    service.confirm(preview["token"], window_ids=[preview["windows"][0]["id"]], resource_ids=[])
    assert api.restored[0][1]["normalPosition"] == [300, 200, 1200, 800]
    capture = service.capture()
    api.handles.clear()
    with pytest.raises(ValueError, match="窗口或显示器"):
        service.save(capture["id"], title="论文", next_step="表四", window_ids=[capture["windows"][0]["id"]])


def test_document_revalidation_and_failed_item_do_not_hide_other_outcomes(tmp_path):
    _, service, api, opened, _, _, _ = setup(tmp_path)
    document = tmp_path / "实验记录.txt"
    document.write_text("before", encoding="utf-8")
    record = save(service, sources=[str(document), "https://example.org/a"])
    preview = service.preview(record["id"])
    document.write_text("changed after preview", encoding="utf-8")
    api.fail = True
    result = service.confirm(preview["token"], window_ids=[preview["windows"][0]["id"]], resource_ids=[r["id"] for r in preview["resources"]])
    assert (result["requested"], result["skipped"], result["failed"]) == (1, 1, 1)
    assert opened == ["https://example.org/a"]
    assert document.read_text(encoding="utf-8") == "changed after preview"


def test_unavailable_items_cannot_be_forged_or_unblocked_in_returned_preview(tmp_path):
    _, service, api, _, _, now, _ = setup(tmp_path)
    record = save(service)
    api.handles.remove(1)
    preview = service.preview(record["id"])
    preview["windows"][0]["available"] = True
    with pytest.raises(ValueError):
        service.confirm(preview["token"], window_ids=[preview["windows"][0]["id"]], resource_ids=[])
    with pytest.raises(ValueError):
        service.confirm(preview["token"], window_ids=["9999"], resource_ids=[])
    now[0] += 121
    with pytest.raises(ValueError, match="过期"):
        service.confirm(preview["token"], window_ids=[], resource_ids=[])
    assert not api.restored


def test_modified_file_before_preview_is_disclosed_and_can_be_selected(tmp_path):
    _, service, _, opened, _, _, _ = setup(tmp_path)
    document = tmp_path / "working-notes.txt"
    document.write_text("old", encoding="utf-8")
    record = save(service, sources=[str(document)])
    document.write_text("newly saved work", encoding="utf-8")
    preview = service.preview(record["id"])
    item = preview["resources"][0]
    assert item["available"] and "更新" in item["note"]
    result = service.confirm(preview["token"], window_ids=[], resource_ids=[item["id"]])
    assert result["requested"] == 1 and opened == [str(document)]


def test_window_titles_are_transient_only_and_plain_user_choice(tmp_path):
    db, service, _, _, _, _, _ = setup(tmp_path)
    title = "private discussion <b>not rich text</b>"
    service.window_label = lambda handle: title
    capture = service.capture()
    assert capture["windows"][0]["label"] == title
    record = service.save(capture["id"], title="工作", next_step="继续", window_ids=[capture["windows"][0]["id"]])
    preview = service.preview(record["id"])
    assert preview["windows"][0]["label"] == title
    with db.connect() as connection:
        saved = connection.execute("SELECT payload_json FROM work_resumptions").fetchone()[0]
        assert title not in saved and "private discussion" not in saved


@pytest.mark.parametrize("value", ["powershell:whoami", "shell:startup", "javascript:alert(1)", "file://server/share/a.txt",
    r"\\server\share\a.txt", "https://name:password@example.org/", "https://example.org:bad/", "https://example.org/a b",
    "https://example.org/\\anything", "a.txt", r"F:\data\a.txt:secret", "cmd.exe"])
def test_resources_reject_commands_credentials_and_network_paths(value):
    with pytest.raises((ValueError, OSError)):
        resource(value)


@pytest.mark.parametrize("extension", [".exe", ".lnk", ".ps1", ".cmd", ".url", ".docm", ".hta", ".html", ".svg"])
def test_executable_or_active_files_are_not_bookmarks(tmp_path, extension):
    path = tmp_path / ("not-a-document" + extension)
    path.write_bytes(b"anything")
    with pytest.raises(ValueError): resource(str(path))


def test_folder_file_urls_and_unicode_are_explicit_and_bounded(tmp_path):
    _, service, _, _, _, _, _ = setup(tmp_path)
    path = tmp_path / "记录 #1.txt"
    path.write_text("原文", encoding="utf-8")
    assert resource(path.as_uri())["source"] == str(path.resolve())
    assert resource(str(tmp_path))["kind"] == "folder"
    record = save(service, sources=[path.as_uri()])
    assert service.preview(record["id"])["resources"][0]["label"] == "记录 #1.txt"
    capture = service.capture()
    with pytest.raises(ValueError):
        service.save(capture["id"], title="x", next_step="y", sources=["https://example.org/" + str(i) for i in range(13)])


def test_capture_cancel_expiry_and_deleted_preview_have_no_effect(tmp_path):
    _, service, api, opened, _, now, _ = setup(tmp_path)
    capture = service.capture()
    now[0] += 601
    with pytest.raises(ValueError): service.save(capture["id"], title="a", next_step="b", window_ids=[capture["windows"][0]["id"]])
    record = save(service)
    preview = service.preview(record["id"])
    service.delete(record["id"])
    with pytest.raises(ValueError): service.confirm(preview["token"], window_ids=[], resource_ids=[])
    assert service.list()["total"] == 0
    record = save(service)
    preview = service.preview(record["id"])
    service.cancel()
    with pytest.raises(ValueError): service.confirm(preview["token"], window_ids=[], resource_ids=[])
    assert not api.restored and not opened


def test_expired_window_snapshot_does_not_prevent_saving_text_only(tmp_path):
    _, service, api, _, _, now, _ = setup(tmp_path)
    capture = service.capture()
    now[0] += 601
    service.save(capture["id"], title="只留下一步", next_step="继续思考", window_ids=[])
    assert service.list()["total"] == 1 and not api.restored


def test_empty_invalid_arrays_are_not_silently_accepted(tmp_path):
    _, service, _, _, _, _, _ = setup(tmp_path)
    capture = service.capture()
    for field in ("window_ids", "sources"):
        for value in (False, {}, ""):
            with pytest.raises(ValueError):
                service.save(capture["id"], title="a", next_step="b", **{field: value})
    assert service.list()["total"] == 0


def test_shutdown_skips_effects_and_interrupted_run_is_not_replayed(tmp_path):
    db, service, api, opened, _, _, _ = setup(tmp_path)
    record = save(service, sources=["https://example.org"])
    preview = service.preview(record["id"])
    service.should_continue = lambda: False
    result = service.confirm(preview["token"], window_ids=[r["id"] for r in preview["windows"]], resource_ids=[r["id"] for r in preview["resources"]])
    assert result["skipped"] == 3 and not api.restored and not opened
    with db.connect() as connection:
        connection.execute("INSERT INTO work_resumption_runs VALUES('interrupted',?,'before',NULL,NULL)", (record["id"],))
    Database(db.path)
    assert not api.restored and not opened


def test_migration_nine_rolls_back_atomically():
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,name TEXT,applied_at TEXT)")
    def authorize(action, arg1, arg2, *_):
        if action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "work_resumption_runs": return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    connection.set_authorizer(authorize)
    with pytest.raises(sqlite3.DatabaseError): Database._apply_work_resumption_migration(connection)
    connection.set_authorizer(None)
    assert not connection.execute("SELECT name FROM sqlite_master WHERE name='work_resumptions'").fetchone()
    assert not connection.execute("SELECT * FROM schema_migrations").fetchone()
    Database._apply_work_resumption_migration(connection)
    assert connection.execute("SELECT version FROM schema_migrations").fetchone()[0] == 9
