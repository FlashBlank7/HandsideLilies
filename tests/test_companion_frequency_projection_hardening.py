from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from lilies.companion_controller import CompanionController
from lilies.core.database import Database


def _controller(database: Database, data_directory) -> CompanionController:
    return CompanionController(
        database,
        data_directory,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )


def _raw_setting(database: Database, key: str, default=None):
    with database.connect() as connection:
        row = connection.execute(
            "SELECT value_json FROM settings WHERE key=?", (key,)
        ).fetchone()
    return default if row is None else json.loads(row["value_json"])


def _active_custom(minutes: int, daily: int) -> dict[str, object]:
    return {
        "frequency": "custom",
        "minimumMinutes": minutes,
        "dailyLimit": daily,
    }


def test_committed_preferences_emit_once_without_a_post_commit_database_read(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    database.set_setting("companion_interests", ["orchids"])
    controller = _controller(database, tmp_path)
    original_get_setting = database.get_setting
    original_set_settings = database.set_settings
    committed = False
    rejected_reads: list[str] = []

    def reject_reads_after_commit(key, *_args, **_kwargs):
        rejected_reads.append(str(key))
        raise sqlite3.OperationalError("synthetic post-commit read failure")

    def commit_then_reject_reads(values):
        nonlocal committed
        original_set_settings(values)
        if "companion_preferences" in values:
            committed = True
            monkeypatch.setattr(database, "get_setting", reject_reads_after_commit)

    monkeypatch.setattr(database, "set_settings", commit_then_reject_reads)
    state_spy = QSignalSpy(controller.changed)
    preference_spy = QSignalSpy(controller.preferencesChanged)
    signal_snapshots: list[dict[str, object]] = []
    controller.preferencesChanged.connect(
        lambda: signal_snapshots.append(controller.preferences)
    )
    try:
        assert controller.setFrequency("custom", 7, 2) is True

        assert committed is True
        assert state_spy.count() == 1
        assert preference_spy.count() == 1
        assert len(signal_snapshots) == 1
        assert signal_snapshots[0]["frequency"] == "custom"
        assert signal_snapshots[0]["minimumMinutes"] == 7
        assert signal_snapshots[0]["dailyLimit"] == 2
        assert signal_snapshots[0]["customMinimumMinutes"] == 7
        assert signal_snapshots[0]["customDailyLimit"] == 2
        # The public property is the same detached in-memory generation and
        # remains readable while every legacy get_setting call is poisoned.
        snapshot = controller.preferences
        assert snapshot["interests"] == ["orchids"]
        snapshot["interests"].append("mutated outside")
        assert controller.preferences["interests"] == ["orchids"]
        assert rejected_reads == []
    finally:
        monkeypatch.setattr(database, "get_setting", original_get_setting)
        monkeypatch.setattr(database, "set_settings", original_set_settings)
        controller.shutdown()

    assert original_get_setting("companion_preferences")["frequency"] == "custom"
    assert original_get_setting("companion_custom_frequency") == {
        "minimumMinutes": 7,
        "dailyLimit": 2,
    }
    assert app is not None


def test_startup_backfills_missing_remembered_custom_on_one_projection_read(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    active = {**_active_custom(7, 2), "futureOption": "preserved"}
    database.set_setting("companion_preferences", active)
    original_get_setting = database.get_setting
    original_reconcile_settings = database.reconcile_settings
    reconciliation_calls: list[tuple[str, ...]] = []

    def reject_split_projection_read(key, default=None):
        if key in {"companion_preferences", "companion_custom_frequency"}:
            raise AssertionError("frequency projections must use one transaction")
        return original_get_setting(key, default)

    def track_reconciliation(keys, reconcile):
        reconciliation_calls.append(keys)
        return original_reconcile_settings(keys, reconcile)

    monkeypatch.setattr(database, "get_setting", reject_split_projection_read)
    monkeypatch.setattr(database, "reconcile_settings", track_reconciliation)
    first = _controller(database, tmp_path)
    try:
        assert reconciliation_calls == [
            ("companion_preferences", "companion_custom_frequency")
        ]
        assert first.preferences["frequency"] == "custom"
        assert first.preferences["customMinimumMinutes"] == 7
        assert first.preferences["customDailyLimit"] == 2
        assert _raw_setting(database, "companion_custom_frequency") == {
            "minimumMinutes": 7,
            "dailyLimit": 2,
        }
        assert _raw_setting(database, "companion_preferences") == active
        assert first.setFrequency("quiet", 45, 6) is True
    finally:
        first.shutdown()

    restored = _controller(database, tmp_path)
    try:
        assert len(reconciliation_calls) == 2
        assert restored.preferences["frequency"] == "quiet"
        assert restored.preferences["customMinimumMinutes"] == 7
        assert restored.preferences["customDailyLimit"] == 2
    finally:
        restored.shutdown()
    assert app is not None


def test_startup_active_custom_overwrites_a_stale_remembered_projection(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    database.set_settings(
        {
            "companion_preferences": _active_custom(9, 3),
            "companion_custom_frequency": {
                "minimumMinutes": 66,
                "dailyLimit": 11,
            },
        }
    )

    first = _controller(database, tmp_path)
    try:
        assert first.preferences["minimumMinutes"] == 9
        assert first.preferences["dailyLimit"] == 3
        assert first.preferences["customMinimumMinutes"] == 9
        assert first.preferences["customDailyLimit"] == 3
        assert database.get_setting("companion_custom_frequency") == {
            "minimumMinutes": 9,
            "dailyLimit": 3,
        }
        assert first.setFrequency("lively", 10, 30) is True
    finally:
        first.shutdown()

    restored = _controller(database, tmp_path)
    try:
        assert restored.preferences["frequency"] == "lively"
        assert restored.preferences["customMinimumMinutes"] == 9
        assert restored.preferences["customDailyLimit"] == 3
    finally:
        restored.shutdown()
    assert app is not None


def test_startup_custom_backfill_failure_leaves_the_active_projection_untouched(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    active = _active_custom(8, 4)
    database.set_setting("companion_preferences", active)
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_remembered_custom_insert
               BEFORE INSERT ON settings
               WHEN NEW.key='companion_custom_frequency'
               BEGIN
                 SELECT RAISE(ABORT, 'synthetic startup repair failure');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="startup repair failure"):
        _controller(database, tmp_path)

    assert database.get_setting("companion_preferences") == active
    assert database.get_setting("companion_custom_frequency", None) is None
    assert app is not None


def test_reconcile_settings_reads_and_repairs_through_one_connection(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting("companion_preferences", _active_custom(6, 1))
    original_connect = database.connect
    connection_count = 0

    @contextmanager
    def counting_connect():
        nonlocal connection_count
        connection_count += 1
        with original_connect() as connection:
            yield connection

    monkeypatch.setattr(database, "connect", counting_connect)
    result = database.reconcile_settings(
        ("companion_preferences", "companion_custom_frequency"),
        lambda rows: {
            "companion_custom_frequency": {
                "minimumMinutes": rows["companion_preferences"][
                    "minimumMinutes"
                ],
                "dailyLimit": rows["companion_preferences"]["dailyLimit"],
            }
        },
    )

    assert connection_count == 1
    assert result["companion_custom_frequency"] == {
        "minimumMinutes": 6,
        "dailyLimit": 1,
    }
    monkeypatch.setattr(database, "connect", original_connect)
    assert database.get_setting("companion_custom_frequency") == {
        "minimumMinutes": 6,
        "dailyLimit": 1,
    }
