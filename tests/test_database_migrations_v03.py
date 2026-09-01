# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3

import pytest

import lilies.core.database as database_module
from lilies.core.database import Database


EXPECTED_TABLES = {
    "tasks",
    "task_occurrences",
    "focus_sessions",
    "reading_sessions",
    "reminders",
    "reminder_deliveries",
    "growth_events",
    "growth_state",
    "unlocks",
    "character_loadout",
    "world_state",
    "narrative_events",
    "event_outbox",
}


def test_productivity_migration_is_versioned_reentrant_and_seeded(tmp_path) -> None:
    path = tmp_path / "lilies.db"
    first = Database(path)
    with first.connect() as db:
        names = {
            str(value["name"])
            for value in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        migration = db.execute("SELECT * FROM schema_migrations WHERE version=3").fetchone()
        loadout = db.execute("SELECT * FROM character_loadout WHERE loadout_id='default'").fetchone()
    assert EXPECTED_TABLES <= names
    assert migration is not None and migration["name"] == "v0.3-productivity-growth"
    assert loadout is not None
    assert (loadout["outfit_id"], loadout["pose_id"]) == ("first-encounter", "idle-prayer")
    first.close()

    second = Database(path)
    with second.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=3").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM unlocks").fetchone()[0] == 2
    assert second.integrity_check() == "ok"


def test_failed_versioned_migration_rolls_back_every_statement(tmp_path, monkeypatch) -> None:
    path = tmp_path / "broken.db"
    with monkeypatch.context() as context:
        context.setattr(
            database_module,
            "_PRODUCTIVITY_SCHEMA",
            (
                "CREATE TABLE migration_probe(value TEXT)",
                "THIS IS NOT VALID SQLITE",
            ),
        )
        with pytest.raises(sqlite3.OperationalError):
            Database(path)

    connection = sqlite3.connect(path)
    try:
        tables = {
            str(value[0])
            for value in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "migration_probe" not in tables
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        connection.close()

    # The same legacy database can be opened normally once the migration code is healthy.
    recovered = Database(path)
    with recovered.connect() as db:
        assert db.execute("SELECT name FROM schema_migrations WHERE version=3").fetchone()[0] == "v0.3-productivity-growth"


def test_connector_audit_privacy_migration_redacts_legacy_content(tmp_path) -> None:
    path = tmp_path / "legacy-audit.db"
    database = Database(path)
    with database.connect() as db:
        db.execute("DELETE FROM schema_migrations WHERE version=5")
        db.execute(
            """INSERT INTO audit_log
               (audit_id,origin,component_id,action_id,risk,decision,payload_json,
                result_json,error_json,completed_at,created_at)
               VALUES('legacy','model','calendar','propose-update','mutate','allow',
                      ?,?,NULL,?,?)""",
            (
                '{"summary":"private old title"}',
                '{"before":{"summary":"private remote title"}}',
                database_module.utc_now(),
                database_module.utc_now(),
            ),
        )
    database.close()

    reopened = Database(path)
    with reopened.connect() as db:
        row = db.execute(
            "SELECT payload_json,result_json FROM audit_log WHERE audit_id='legacy'"
        ).fetchone()
        migration = db.execute(
            "SELECT name FROM schema_migrations WHERE version=5"
        ).fetchone()
    assert "private" not in row["payload_json"] + row["result_json"]
    assert migration["name"] == "v0.3-connector-audit-privacy"


def test_proactive_generation_provenance_migrates_legacy_session_table(
    tmp_path,
) -> None:
    path = tmp_path / "legacy-proactive.db"
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """CREATE TABLE proactive_sessions (
                session_id TEXT PRIMARY KEY,
                bubble_id TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                summary TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                source_json TEXT NOT NULL DEFAULT '{}',
                scene_label TEXT NOT NULL DEFAULT '',
                moved_to_box INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(path)
    with database.connect() as db:
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(proactive_sessions)").fetchall()
        }
        migration = db.execute(
            "SELECT name FROM schema_migrations WHERE version=6"
        ).fetchone()
    assert "generation_json" in columns
    assert migration is not None
    assert migration["name"] == "v0.3-proactive-generation-provenance"

    reopened = Database(path)
    with reopened.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=6"
            ).fetchone()[0]
            == 1
        )
