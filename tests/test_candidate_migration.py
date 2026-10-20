import copy
import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lilies.core.database import Database
from lilies.core import data_migration


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("candidate_migration", ROOT / "scripts/verify_candidate_migration.py")
rehearsal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rehearsal)


class OldSchemaFixture(Database):
    """A unit-test layout only; the CLI uses actual frozen legacy modules."""
    _apply_chat_revision_migration = staticmethod(lambda db: None)
    _apply_paper_notebook_migration = staticmethod(lambda db: None)
    _apply_work_resumption_migration = staticmethod(lambda db: None)
    _apply_shared_world_migration = staticmethod(lambda db: None)


@pytest.mark.parametrize("version", [7, 8, 9, 10])
def test_real_sql_denial_rolls_back_target_transaction_and_keeps_old_rows(tmp_path, version):
    path = tmp_path / "fixture.db"
    old = OldSchemaFixture(path)
    conversation = old.ensure_conversation()
    old.add_message(conversation, "user", "旧记录：不能丢失 α 🕊")
    old.close()
    before = rehearsal.snapshot(path)
    with pytest.raises(sqlite3.DatabaseError):
        rehearsal.migration_fault_class(Database, version)(path)
    rehearsal.validate_failed_migration(path, before, version)
    upgraded = Database(path)
    upgraded.close()
    rehearsal.assert_preserved(before, rehearsal.snapshot(path))


@pytest.mark.parametrize("kind", ["content", "rowid", "column", "table", "index"])
def test_preservation_gate_rejects_changes_even_with_same_record_count(tmp_path, kind):
    db = Database(tmp_path / "sample.db")
    db.add_message(db.ensure_conversation(), "user", "唯一记录")
    before = rehearsal.snapshot(db.path)
    after = copy.deepcopy(before)
    if kind in {"content", "rowid"}:
        row = list(after["tables"]["messages"]["rows"][0])
        row[0 if kind == "rowid" else 4] = "modified"
        after["tables"]["messages"]["rows"][0] = tuple(row)
    elif kind == "column":
        after["tables"]["messages"]["columns"][-1] = "wrong_column"
    elif kind == "table":
        del after["tables"]["messages"]
    else:
        after["schema"] = [row for row in after["schema"] if row[1] != "messages_conversation_created"]
    with pytest.raises(RuntimeError):
        rehearsal.assert_preserved(before, after)


def test_snapshot_checks_foreign_keys_and_keeps_binary_values(tmp_path):
    path = tmp_path / "unicode 白纸.db"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(id INTEGER REFERENCES parent(id), value BLOB);")
        db.execute("INSERT INTO child VALUES(?,?)", (42, b"\x00fixture\xff"))
    with pytest.raises(RuntimeError, match="Foreign key"):
        rehearsal.snapshot(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO parent VALUES(42)")
    result = rehearsal.snapshot(path)
    assert result["tables"]["child"]["rows"][0][2] == b"\x00fixture\xff"
    assert "blobHex" in rehearsal.encoded(result)


def test_backup_integrity_failure_preserves_good_copy_and_cleans_temp(tmp_path):
    source = tmp_path / "source.db"
    database = Database(source)
    database.add_message(database.ensure_conversation(), "user", "备份保留这句话")
    database.close()
    rehearsal.verify_failed_backup(data_migration, source, tmp_path)


@pytest.mark.parametrize("kind", ["missing-ledger", "missing-table", "premature-table"])
def test_schema_gate_never_accepts_partial_migration_as_retry_success(tmp_path, kind):
    database = Database(tmp_path / "schema.db")
    value = rehearsal.snapshot(database.path)
    if kind == "missing-ledger":
        value["tables"]["schema_migrations"]["rows"] = [row for row in value["tables"]["schema_migrations"]["rows"] if row[1] != 8]
    elif kind == "missing-table":
        del value["tables"]["papers"]
    else:
        value["tables"]["schema_migrations"]["rows"] = [row for row in value["tables"]["schema_migrations"]["rows"] if row[1] <= 6]
    with pytest.raises(RuntimeError):
        rehearsal.require_schema(value, 6 if kind == "premature-table" else 10)


def test_output_is_new_f_drive_diagnostic_directory_only(tmp_path, monkeypatch):
    monkeypatch.setattr(rehearsal, "ROOT", tmp_path)
    (tmp_path / "artifacts").mkdir()
    target = tmp_path / "artifacts/migration-v0357-unit"
    assert rehearsal.output_directory(target, create=True) == target
    with pytest.raises(FileExistsError):
        rehearsal.output_directory(target, create=True)
    for bad in (tmp_path / "private-data/migration-v0357-unit", tmp_path / "artifacts/wrong", Path("C:/artifacts/migration-v0357-unit")):
        with pytest.raises(RuntimeError):
            rehearsal.output_directory(bad, create=True)


def test_frozen_loader_only_loads_named_data_modules_and_cleans_up(tmp_path, monkeypatch):
    executable = tmp_path / "synthetic.exe"
    executable.write_bytes(b"synthetic fixture, never executed")
    requested = []
    def extract(name):
        requested.append(name)
        return compile('__version__ = "0.3.57"' if name == "lilies" else 'fixture = True', name, "exec")
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
                              open_embedded_archive=lambda name: SimpleNamespace(extract=extract))
    monkeypatch.setattr(rehearsal, "CArchiveReader", lambda _: archive)
    names_before = set(sys.modules)
    with rehearsal.frozen_data_modules(executable, rehearsal.digest(executable), "0.3.57") as (modules, hashes):
        assert set(modules) == set(rehearsal.BASE_MODULES + rehearsal.NEW_MODULES)
        assert set(hashes) == set(modules)
        assert all(module.fixture for module in modules.values())
    assert not any(name.startswith("_lilies_data_rehearsal_") for name in set(sys.modules) - names_before)
    assert set(requested) == {"lilies", *("lilies." + suffix for suffix in rehearsal.BASE_MODULES + rehearsal.NEW_MODULES)}
    requested.clear()
    with pytest.raises(RuntimeError, match="pinned package"):
        with rehearsal.frozen_data_modules(executable, "0" * 64, "0.3.57"):
            pytest.fail("must not enter")
    assert not requested
    with pytest.raises(RuntimeError, match="Frozen version"):
        with rehearsal.frozen_data_modules(executable, rehearsal.digest(executable), "0.3.56"):
            pytest.fail("must not enter")
    assert not any(name.startswith("_lilies_data_rehearsal_") for name in set(sys.modules) - names_before)
