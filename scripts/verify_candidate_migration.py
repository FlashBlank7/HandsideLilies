"""Rehearse database upgrades using exact frozen packages and synthetic data.

This is not an installer. No app entry point, GUI, credentials, private-data
directory, native windows, model, or network is used. Only a fixed allowlist of
data modules is loaded, under isolated Python package names. A successful report
proves this data rehearsal, not full application/installed rollback acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

from PyInstaller.archive.readers import CArchiveReader


ROOT = Path(__file__).resolve().parents[1]
BASE_MODULES = ("core.database", "core.data_migration", "core.themes",
                "core.productivity", "connectors.schema")
NEW_MODULES = ("core.papers", "core.shared_world")
MIGRATION_FAULTS = {
    7: ("message_revision_lookup", "message_revisions"),
    8: ("paper_annotation_lookup", "papers"),
    9: ("work_resumption_recent", "work_resumptions"),
    10: ("world_record_shelf", "world_records"),
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest().upper()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=lambda item: {"blobHex": item.hex()} if isinstance(item, bytes) else str(item))


def snapshot(path):
    """Include rowids and every table/index definition, not just row counts."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Database integrity failed")
        require(not db.execute("PRAGMA foreign_key_check").fetchall(), "Foreign key check failed")
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        tables = {}
        for kind, name, _, _ in schema:
            if kind != "table":
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            cursor = db.execute(f"SELECT rowid,* FROM {quoted} ORDER BY rowid")
            tables[name] = {"columns": [item[0] for item in cursor.description], "rows": cursor.fetchall()}
    return {"schema": schema, "tables": tables}


def snapshot_digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def assert_preserved(before, after):
    for row in before["schema"]:
        require(row in after["schema"], f"Original schema changed: {row[1]}")
    for name, value in before["tables"].items():
        current = after["tables"].get(name)
        require(current is not None and current["columns"] == value["columns"], f"Original columns changed: {name}")
        if name == "schema_migrations":
            require(all(row in current["rows"] for row in value["rows"]), "Original migration ledger changed")
        else:
            require(value == current, f"Original records changed: {name}")


def require_schema(value, version):
    versions = [row[1] for row in value["tables"]["schema_migrations"]["rows"]]
    require(versions == list(range(3, version + 1)), "Migration ledger is incomplete")
    for introduced, (_, table) in MIGRATION_FAULTS.items():
        require((table in value["tables"]) == (introduced <= version),
                f"Schema table does not match migration ledger: {table}")


def output_directory(path, *, create):
    target = Path(path).absolute()
    require(target.drive.casefold() == "f:" and target.parent == ROOT / "artifacts",
            "Rehearsal must use this project's F-drive artifacts directory")
    require(re.fullmatch(r"migration-v0357-[A-Za-z0-9-]{1,80}", target.name), "Invalid rehearsal identity")
    require(target.resolve(strict=False) == target, "Linked rehearsal directory is forbidden")
    if create:
        target.mkdir(exist_ok=False)
    else:
        require(target.is_dir(), "Parent rehearsal is missing")
    return target


@contextmanager
def frozen_data_modules(executable, expected_sha, version):
    executable = Path(executable).absolute()
    require(executable.resolve(strict=True) == executable, "Linked package is forbidden")
    require(re.fullmatch(r"[A-Fa-f0-9]{64}", expected_sha or ""), "Expected executable hash is required")
    require(digest(executable) == expected_sha.upper(), "Executable differs from pinned package")
    archive = CArchiveReader(str(executable))
    names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
    require(len(names) == 1, "Expected one frozen Python archive")
    pyz = archive.open_embedded_archive(names[0])
    prefix = "_lilies_data_rehearsal_" + uuid.uuid4().hex
    registered = []
    modules = {}
    fingerprints = {}
    try:
        for suffix in ("", "core", "connectors"):
            name = prefix + ("." + suffix if suffix else "")
            module = ModuleType(name)
            module.__package__ = name
            module.__path__ = []
            sys.modules[name] = module
            registered.append(name)
        exec(pyz.extract("lilies"), sys.modules[prefix].__dict__)
        require(sys.modules[prefix].__version__ == version, "Frozen version differs from required version")
        for suffix in BASE_MODULES + (NEW_MODULES if version == "0.3.57" else ()):
            code = pyz.extract("lilies." + suffix)
            name = prefix + "." + suffix
            module = ModuleType(name)
            module.__package__ = name.rsplit(".", 1)[0]
            module.__file__ = code.co_filename
            sys.modules[name] = module
            registered.append(name)
            exec(code, module.__dict__)
            modules[suffix] = module
            fingerprints[suffix] = hashlib.sha256(marshal.dumps(code)).hexdigest()
        yield modules, fingerprints
    finally:
        for name in reversed(registered):
            sys.modules.pop(name, None)
        require(digest(executable) == expected_sha.upper(), "Package changed during rehearsal")


def seed_legacy(modules, path):
    database = modules["core.database"].Database(path)
    modules["connectors.schema"].ensure_schema(path).close()
    p = modules["core.productivity"]
    now = [datetime(2026, 9, 1, 2, 0, tzinfo=UTC)]
    clock = lambda: now[0]
    conversation = database.ensure_conversation()
    with database.connection_session():
        for i in range(90):
            database.add_message(conversation, "user" if i % 2 == 0 else "assistant",
                                 f"模拟旧对话 {i}：论文 αβ 🕊，原文保留。", {"fixture": True})
        answer = database.add_message(conversation, "assistant", "模拟旧回答，可重新解释。")
        forgotten = database.add_message(conversation, "user", "模拟需要忘记的火星芝麻偏好")
        database.save_memory("称呼", "请叫我测试读者小林", partition_id="identity")
        database.save_memory("禁用偏好", "模拟禁用的青柠咖啡", enabled=False)
        database.save_memory("世界观", "白色纸箱是既定设定", category="Canon", canon_kind="canon")
        card = database.save_reading_card("variance = E[(X−μ)²]", "这是模拟的方差说明。", metadata={"sourceApp": "fixture-reader"})
        database.set_settings({"pet_drag_mode": "direct", "companion_enabled": False,
                               "fixture_unicode": "白纸・绳结 🕊", "fixture_permissions": {"mode": "cautious"}})
        database.upsert_desktop_item({"item_id": "fixture-desktop", "name": "模拟论文.pdf",
                                      "path": str(path.parent / "并不存在的文档.pdf"), "source": "manual", "kind": "file"})
        database.update_desktop_layout("fixture-desktop", x=203.5, y=157.25, hidden=True, pinned=True)
        database.create_desktop_layout("模拟研究布局")
        with database.connect() as db:
            fragment = db.execute("SELECT fragment_id FROM memory_fragments WHERE source_type='message' AND source_id=?", (forgotten,)).fetchone()[0]
        database.forget_memory_fragment(fragment)
    tasks = p.TaskService(database, now=clock)
    recurring = tasks.create("模拟每日读论文", category="project", due_at=now[0],
                             timezone_name="Asia/Tokyo", recurrence={"frequency": "daily", "interval": 1})
    done = tasks.complete(recurring["task_id"])
    pending = tasks.create("模拟未完成的消融实验", note="下一步核对表三", priority=3)
    archived = tasks.create("模拟已归档事项")
    tasks.archive(archived["task_id"])
    for index in range(3):
        tasks.complete(tasks.create(f"模拟日常 {index}", category="daily")["task_id"])
    focus = p.FocusService(database, now=clock)
    focused = focus.start(task_id=pending["task_id"], minutes=25)
    now[0] += timedelta(minutes=25)
    focus.finish(focused["session_id"])
    paused = focus.start(task_id=pending["task_id"], minutes=25)
    now[0] += timedelta(minutes=2)
    focus.pause(paused["session_id"])
    reading = p.ReadingSessionService(database, now=clock)
    read = reading.start(title="模拟旧论文", source="fixture")
    now[0] += timedelta(minutes=20)
    reading.finish(read["session_id"])
    reminder = p.ReminderScheduler(database, now=clock)
    reminder.create("模拟已提醒", now[0] - timedelta(minutes=1))
    reminder.claim_due()
    reminder.create("模拟明日继续", now[0] + timedelta(days=1), task_id=pending["task_id"], timezone_name="Asia/Tokyo")
    # Opaque synthetic bytes exercise BLOB preservation without Credential
    # Manager/DPAPI, real accounts, token access, decrypting or external writes.
    with database.connect() as db:
        db.execute("INSERT INTO connector_encrypted_content VALUES(?,?,?,?,?)",
                   ("fixture-content", "slack", b"\x00synthetic-ciphertext\xff", clock().isoformat(), clock().isoformat()))
        db.execute("INSERT INTO connector_policies VALUES(?,?,?,?,?,?,?,?)",
                   ("slack", "fixture-account", "necessary", "quiet", "metadata", "assist", "[]", clock().isoformat()))
    database.close()
    return {"conversation": conversation, "answer": answer, "forgottenFragment": fragment, "legacyCard": card,
            "task": recurring["task_id"], "occurrence": done["occurrence"]["occurrence_id"], "pendingTask": pending["task_id"]}


def assert_memory_privacy(database, fragment):
    with database.connect() as db:
        require(db.execute("SELECT forgotten FROM memory_fragments WHERE fragment_id=?", (fragment,)).fetchone()[0] == 1,
                "Forgotten memory became active")
        require(db.execute("SELECT COUNT(*) FROM memory_ngrams WHERE fragment_id=?", (fragment,)).fetchone()[0] == 0,
                "Forgotten memory was reindexed")
        require(db.execute("SELECT COUNT(*) FROM memory_ngrams n JOIN memory_fragments f ON n.fragment_id=f.fragment_id WHERE f.enabled=0 OR f.forgotten=1").fetchone()[0] == 0,
                "Disabled memory was reindexed")


def exercise_candidate(modules, database, ids):
    page = database.conversation_page(ids["conversation"], anchor=ids["answer"])
    require(page["messages"][page["anchorIndex"]]["message_id"] == ids["answer"], "Legacy history anchor failed")
    database.replace_assistant_reply(ids["answer"], "模拟旧回答，可重新解释。", "这是模拟重试后的解释。")
    require(database.reply_revisions(ids["answer"])[0]["content"] == "模拟旧回答，可重新解释。", "Old reply was lost")
    p = modules["core.productivity"]
    reading = p.ReadingSessionService(database)
    papers = modules["core.papers"].PaperService(database, reading)
    paper = papers.add("模拟本篇阅读", "https://example.org/fixture-paper")
    papers.update_position(paper["paper_id"], 8, "表三", "核对方差")
    papers.start(paper["paper_id"])
    card = database.save_reading_card("模拟新版摘录", "模拟解释", paper_context=papers.capture_context())
    papers.mark(paper["paper_id"], card, "question")
    world = modules["core.shared_world"].SharedWorldService(database)
    world.sync()
    entry = world.save_letter(ids["answer"], "这是模拟重试后的解释。")
    world.set_visibility(entry, "hidden")
    # Data-format fixture only: does not import the native restoration modules,
    # enumerate host windows or claim to test the work-resumption GUI.
    with database.connect() as db:
        db.execute("INSERT INTO work_resumptions VALUES(?,?,?,?,?,?,?,NULL)",
                   ("fixture-resumption", "下次接着读", ids["pendingTask"], "模拟未完成的消融实验", "先看表三",
                    '{"windows":[],"resources":[]}', datetime.now(UTC).isoformat()))
    return {"paper": paper["paper_id"], "card": card, "worldEntry": entry}


def migration_fault_class(database_type, version, *, crash=False):
    target_index = MIGRATION_FAULTS[version][0]
    class FaultDatabase(database_type):
        def _open_connection(self):
            db = super()._open_connection()
            def authorize(action, arg1, _arg2, _schema, _trigger):
                if action == sqlite3.SQLITE_CREATE_INDEX and arg1 == target_index:
                    if crash:
                        os._exit(87)  # Exact synthetic data-only child, no GUI.
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize)
            return db
    return FaultDatabase


def validate_failed_migration(path, baseline, version):
    current = snapshot(path)
    assert_preserved(baseline, current)
    require(MIGRATION_FAULTS[version][1] not in current["tables"], "Failed migration table survived rollback")
    require_schema(current, version - 1)


def verify_failed_backup(migration_module, source, directory):
    """A failed integrity check must never replace an existing good backup."""
    destination = directory / "backup-failure-preserved.db"
    migration_module.sqlite_backup(source, destination)
    before = digest(destination)
    original_integrity = migration_module.sqlite_integrity
    try:
        migration_module.sqlite_integrity = lambda _path: "synthetic-corruption"
        try:
            migration_module.sqlite_backup(source, destination)
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Invalid backup was accepted")
    finally:
        migration_module.sqlite_integrity = original_integrity
    require(digest(destination) == before and snapshot(destination) == snapshot(source),
            "Failed backup overwrote a good copy")
    require(not any(directory.glob(".backup-failure-preserved.db.*.tmp*")),
            "Failed backup left temporary database/sidecars behind")


def rehearse(legacy, candidate, directory, *, candidate_exe, candidate_sha):
    source = directory / "legacy.db"
    backup = directory / "pre-upgrade.db"
    upgraded = directory / "candidate.db"
    ids = seed_legacy(legacy, source)
    # Hold a connection open so the last synthetic write stays in WAL pages.
    keeper = sqlite3.connect(source)
    keeper.execute("PRAGMA wal_autocheckpoint=0")
    keeper.execute("PRAGMA journal_mode=WAL")
    try:
        writer = legacy["core.database"].Database(source)
        writer.add_message(ids["conversation"], "user", "模拟只在 WAL 里的最后一条消息")
        writer.close()
        require(source.with_name(source.name + "-wal").stat().st_size > 0, "WAL fixture is not live")
        baseline = snapshot(source)
        candidate["core.data_migration"].sqlite_backup(source, backup)
        require(snapshot(backup) == baseline, "Backup omitted committed WAL data")
    finally:
        keeper.close()
    require_schema(baseline, 6)
    backup_sha = digest(backup)
    copy_database = candidate["core.data_migration"].sqlite_backup
    verify_failed_backup(candidate["core.data_migration"], backup, directory)
    copy_database(backup, upgraded)
    database = candidate["core.database"].Database(upgraded)
    candidate["connectors.schema"].ensure_schema(upgraded).close()
    after_upgrade = snapshot(upgraded)
    assert_preserved(baseline, after_upgrade)
    require_schema(after_upgrade, 10)
    assert_memory_privacy(database, ids["forgottenFragment"])
    completed = candidate["core.productivity"].TaskService(database).complete(ids["task"], ids["occurrence"])
    require(completed["alreadyCompleted"] and snapshot(upgraded) == after_upgrade, "Upgrade replay rewarded old task twice")
    database.close()
    for _ in range(3):
        again = candidate["core.database"].Database(upgraded)
        again.close()
        require(snapshot(upgraded) == after_upgrade, "Repeated startup changed existing records")
    database = candidate["core.database"].Database(upgraded)
    exercise_candidate(candidate, database, ids)
    database.close()
    before_binary_rollback = snapshot(upgraded)
    old = legacy["core.database"].Database(upgraded)
    legacy["connectors.schema"].ensure_schema(upgraded).close()
    require(snapshot(upgraded) == before_binary_rollback, "Old data runtime changed new records")
    old.add_message(ids["conversation"], "user", "模拟换回旧版之后的新消息")
    old.close()
    after_old_write = snapshot(upgraded)
    again = candidate["core.database"].Database(upgraded)
    assert_memory_privacy(again, ids["forgottenFragment"])
    require(snapshot(upgraded) == after_old_write, "Re-upgrade changed records written after rollback")
    again.close()
    # Rehearse a snapshot fallback only into a new file; post-upgrade data is
    # retained separately. Never overwrite it with an older backup.
    retained = directory / "post-upgrade-retained.db"
    restored = directory / "snapshot-restored.db"
    copy_database(upgraded, retained)
    copy_database(backup, restored)
    require(snapshot(retained) == after_old_write, "Post-upgrade data was not retained")
    restored_db = legacy["core.database"].Database(restored)
    restored_db.close()
    require(snapshot(restored) == baseline, "Snapshot recovery differs from the old database")
    failures = []
    for version in MIGRATION_FAULTS:
        for mode in ("denied", "crash"):
            path = directory / f"{mode}-{version}.db"
            copy_database(backup, path)
            if mode == "denied":
                try:
                    migration_fault_class(candidate["core.database"].Database, version)(path)
                except sqlite3.DatabaseError:
                    pass
                else:
                    raise RuntimeError("Migration fault was not triggered")
            else:
                env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TEMP=str(directory), TMP=str(directory))
                child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "crash-worker",
                    "--candidate-exe", str(candidate_exe), "--candidate-sha", candidate_sha,
                    "--output-dir", str(directory), "--version", str(version)],
                    env=env, capture_output=True, timeout=30,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                require(child.returncode == 87, f"Crash fixture exited unexpectedly: {child.returncode}")
            validate_failed_migration(path, baseline, version)
            retry = candidate["core.database"].Database(path)
            retry.close()
            retried = snapshot(path)
            assert_preserved(baseline, retried)
            require_schema(retried, 10)
            failures.append({"version": version, "mode": mode, "oldDataPreserved": True, "transactionRolledBack": True, "retryPassed": True})
    require(snapshot(source) == baseline and digest(backup) == backup_sha, "Original fixture or backup changed")
    return {"schemaVersion": 1, "passed": True, "releaseReady": False,
            "scope": "Exact frozen data modules, synthetic records only; not application installation, UI or native performance acceptance",
            "legacySchema": 6, "candidateSchema": 10, "baselineTables": len(baseline["tables"]),
            "nonemptyBaselineTables": sum(bool(value["rows"]) for value in baseline["tables"].values()),
            "baselineDigest": snapshot_digest(baseline), "upgradedDigest": snapshot_digest(after_old_write),
            "committedWalBackedUp": True, "allLegacyRowsAndRowidsPreserved": True,
            "failedBackupPreservesGoodCopy": True, "failedBackupTemporaryFilesRemoved": True,
            "repeatedStartupStable": True, "memoryPrivacyPreserved": True, "noDuplicateGrowth": True,
            "newDataPreservedAcrossOldRuntime": True, "oldRuntimeWriteThenReupgrade": True,
            "backupRecoveryExact": True, "postUpgradeSnapshotRetained": True,
            "originalAndBackupUnchanged": True, "migrationFailures": failures,
            "testedAt": datetime.now(UTC).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rehearse", "crash-worker"))
    parser.add_argument("--legacy-exe", type=Path)
    parser.add_argument("--legacy-sha")
    parser.add_argument("--candidate-exe", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version", type=int, choices=tuple(MIGRATION_FAULTS))
    args = parser.parse_args()
    directory = output_directory(args.output_dir, create=args.action == "rehearse")
    with frozen_data_modules(args.candidate_exe, args.candidate_sha, "0.3.57") as (candidate, candidate_code):
        if args.action == "crash-worker":
            require(args.version in MIGRATION_FAULTS, "Crash version is required")
            path = directory / f"crash-{args.version}.db"
            require(path.is_file() and path.resolve() == path, "Crash fixture is missing or linked")
            migration_fault_class(candidate["core.database"].Database, args.version, crash=True)(path)
            raise RuntimeError("Crash fixture did not reach the target migration")
        require(args.legacy_exe is not None, "Legacy executable is required")
        with frozen_data_modules(args.legacy_exe, args.legacy_sha, "0.3.56") as (legacy, legacy_code):
            result = rehearse(legacy, candidate, directory, candidate_exe=args.candidate_exe, candidate_sha=args.candidate_sha)
    result.update(legacyExecutableSha256=args.legacy_sha.upper(), candidateExecutableSha256=args.candidate_sha.upper(),
                  legacyModuleFingerprints=legacy_code, candidateModuleFingerprints=candidate_code,
                  verifierSha256=digest(Path(__file__).resolve()))
    with (directory / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(encoded(result))


if __name__ == "__main__":
    main()
