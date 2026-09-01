from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lilies.core import data_migration
from lilies.core.data_migration import (
    migration_status,
    prepare_private_data,
    sqlite_backup,
    sqlite_integrity,
    validate_startup_and_finalize,
)


def _legacy_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    try:
        database.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, content TEXT NOT NULL)")
        database.execute("INSERT INTO messages(content) VALUES (?)", ("我的名字是测试者",))
        database.commit()
    finally:
        database.close()


def test_sqlite_backup_migration_preserves_data_and_regenerates_token(tmp_path: Path) -> None:
    legacy = tmp_path / "local" / "Lilies in the box"
    destination = tmp_path / "project" / "private-data"
    _legacy_database(legacy / "lilies.db")
    (legacy / "codex-chat").mkdir()
    (legacy / "codex-chat" / "session.json").write_text("{}", "utf-8")
    (legacy / "socket-token.txt").write_text("old-secret", "utf-8")

    result = prepare_private_data(destination, legacy)

    assert result.status == "migrated"
    assert sqlite_integrity(destination / "lilies.db") == "ok"
    with sqlite3.connect(destination / "lilies.db") as database:
        assert database.execute("SELECT content FROM messages").fetchone()[0] == "我的名字是测试者"
    assert (destination / "codex-chat" / "session.json").is_file()
    assert not (destination / "socket-token.txt").exists()
    backup = Path(result.backup_directory)
    assert sqlite_integrity(backup / "lilies.db") == "ok"
    assert migration_status(destination)["status"] == "awaiting-restart-validation"
    assert legacy.is_dir()


def test_sqlite_backup_cleans_explicit_temp_main_wal_and_shm(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    _legacy_database(source)
    monkeypatch.setattr(data_migration.secrets, "token_hex", lambda _size: "cleanup")
    temporary = tmp_path / ".backup.db.cleanup.tmp"
    temporary_wal = temporary.with_name(f"{temporary.name}-wal")
    temporary_shm = temporary.with_name(f"{temporary.name}-shm")
    unrelated_sidecar = tmp_path / ".backup.db.unrelated.tmp-wal"
    unrelated_sidecar.write_bytes(b"keep")
    real_integrity = data_migration.sqlite_integrity

    def integrity_with_stale_sidecars(path: Path) -> str:
        result = real_integrity(path)
        temporary_wal.write_bytes(b"stale-wal")
        temporary_shm.write_bytes(b"stale-shm")
        return result

    monkeypatch.setattr(data_migration, "sqlite_integrity", integrity_with_stale_sidecars)

    sqlite_backup(source, destination)

    assert real_integrity(destination) == "ok"
    assert not temporary.exists()
    assert not temporary_wal.exists()
    assert not temporary_shm.exists()
    assert unrelated_sidecar.read_bytes() == b"keep"


def test_sqlite_backup_cleans_all_explicit_temp_files_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backup.db"
    _legacy_database(source)
    monkeypatch.setattr(data_migration.secrets, "token_hex", lambda _size: "failure")
    temporary = tmp_path / ".backup.db.failure.tmp"
    temporary_wal = temporary.with_name(f"{temporary.name}-wal")
    temporary_shm = temporary.with_name(f"{temporary.name}-shm")

    def failed_integrity(_path: Path) -> str:
        temporary_wal.write_bytes(b"stale-wal")
        temporary_shm.write_bytes(b"stale-shm")
        return "corrupt"

    monkeypatch.setattr(data_migration, "sqlite_integrity", failed_integrity)

    with pytest.raises(RuntimeError, match="SQLite 备份完整性检查失败"):
        sqlite_backup(source, destination)

    assert not destination.exists()
    assert not temporary.exists()
    assert not temporary_wal.exists()
    assert not temporary_shm.exists()


def test_migration_excludes_rebuildable_cache_but_preserves_user_data(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "local" / "Lilies in the box"
    destination = tmp_path / "project" / "private-data"
    _legacy_database(legacy / "lilies.db")
    (legacy / "cache" / "qmlcache").mkdir(parents=True)
    (legacy / "cache" / "qmlcache" / "root.qmlc").write_bytes(b"cache")
    nested = legacy / "Lilies in the box"
    (nested / "cache" / "qmlcache").mkdir(parents=True)
    (nested / "cache" / "qmlcache" / "nested.qmlc").write_bytes(b"cache")
    (nested / "user-state.json").write_text('{"keep": true}', "utf-8")
    (legacy / "codex-chat").mkdir()
    (legacy / "codex-chat" / "session.json").write_text("{}", "utf-8")
    (legacy / "documents").mkdir()
    (legacy / "documents" / "cache").write_text("user file", "utf-8")
    (legacy / "user-cache.txt").write_text("not a cache directory", "utf-8")

    result = prepare_private_data(destination, legacy)
    backup = Path(result.backup_directory)

    for migrated_root in (destination, backup):
        assert not (migrated_root / "cache").exists()
        assert not (migrated_root / "Lilies in the box" / "cache").exists()
        assert (migrated_root / "Lilies in the box" / "user-state.json").read_text(
            "utf-8"
        ) == '{"keep": true}'
        assert (migrated_root / "codex-chat" / "session.json").is_file()
        assert (migrated_root / "documents" / "cache").read_text("utf-8") == (
            "user file"
        )
        assert (migrated_root / "user-cache.txt").read_text("utf-8") == (
            "not a cache directory"
        )
    assert "cache" not in result.copied_items


def test_legacy_removal_requires_a_different_validated_session(
    tmp_path: Path, monkeypatch
) -> None:
    local = tmp_path / "local"
    legacy = local / "Lilies in the box"
    destination = tmp_path / "project" / "private-data"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    _legacy_database(legacy / "lilies.db")
    (legacy / "keep.txt").write_text("recoverable", "utf-8")
    prepare_private_data(destination, legacy)

    first = validate_startup_and_finalize(destination, legacy, "session-a")
    assert first["status"] == "awaiting-restart-validation"
    assert legacy.exists()

    # Re-validating within one process/session cannot satisfy restart safety.
    same = validate_startup_and_finalize(destination, legacy, "session-a")
    assert same["validatedSessions"] == 1
    assert legacy.exists()

    second = validate_startup_and_finalize(destination, legacy, "session-b")
    assert second["status"] == "legacy-removed"
    assert not legacy.exists()
    backup_dir = Path(migration_status(destination)["backupDirectory"])
    assert (backup_dir / "keep.txt").read_text("utf-8") == "recoverable"


def test_existing_f_database_is_never_overwritten(tmp_path: Path) -> None:
    legacy = tmp_path / "local" / "Lilies in the box"
    destination = tmp_path / "project" / "private-data"
    _legacy_database(legacy / "lilies.db")
    destination.mkdir(parents=True)
    _legacy_database(destination / "lilies.db")
    with sqlite3.connect(destination / "lilies.db") as database:
        database.execute("UPDATE messages SET content='F盘优先'")

    result = prepare_private_data(destination, legacy)

    assert result.status == "already-present"
    with sqlite3.connect(destination / "lilies.db") as database:
        assert database.execute("SELECT content FROM messages").fetchone()[0] == "F盘优先"
