from __future__ import annotations

import sqlite3
import threading

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from lilies.companion_controller import CompanionController
from lilies.core.database import Database


def _install_second_setting_failure(database: Database) -> None:
    with database.connect() as connection:
        connection.execute(
            """CREATE TRIGGER fail_companion_preferences_update
               BEFORE UPDATE ON settings
               WHEN NEW.key='companion_preferences'
               BEGIN
                 SELECT RAISE(ABORT, 'synthetic second setting failure');
               END"""
        )


def test_set_settings_rolls_back_every_key_when_later_upsert_fails(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    original_custom = {"minimumMinutes": 25, "dailyLimit": 12}
    original_preferences = {
        "frequency": "balanced",
        "minimumMinutes": 25,
        "dailyLimit": 12,
    }
    database.set_settings(
        {
            "companion_custom_frequency": original_custom,
            "companion_preferences": original_preferences,
        }
    )
    _install_second_setting_failure(database)

    with pytest.raises(sqlite3.IntegrityError, match="second setting failure"):
        database.set_settings(
            {
                "companion_custom_frequency": {
                    "minimumMinutes": 7,
                    "dailyLimit": 2,
                },
                "companion_preferences": {
                    "frequency": "custom",
                    "minimumMinutes": 7,
                    "dailyLimit": 2,
                },
            }
        )

    assert database.get_setting("companion_custom_frequency") == original_custom
    assert database.get_setting("companion_preferences") == original_preferences


def test_set_settings_serializes_every_value_before_touching_database(tmp_path) -> None:
    database = Database(tmp_path / "lilies.db")
    database.set_setting("first", {"state": "old"})

    with pytest.raises(TypeError):
        database.set_settings(
            {
                "first": {"state": "new"},
                "second": object(),
            }
        )

    assert database.get_setting("first") == {"state": "old"}
    assert database.get_setting("second", None) is None


def test_custom_frequency_database_failure_does_not_publish_partial_state(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    statuses: list[str] = []
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=statuses.append,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    try:
        assert controller.setFrequency("custom", 25, 12) is True
        original_preferences = dict(controller.preferences)
        original_model = controller.preferences_model
        original_engine_preferences = controller.engine.preferences
        original_gate = controller.engine.gate.config
        original_cooldown = controller.activity.cooldown_seconds
        original_momentum = controller.momentum
        original_scores = dict(controller._category_smooth_scores)
        original_custom_row = database.get_setting("companion_custom_frequency")
        original_preference_row = database.get_setting("companion_preferences")
        _install_second_setting_failure(database)
        state_spy = QSignalSpy(controller.changed)
        preference_spy = QSignalSpy(controller.preferencesChanged)

        assert controller.setFrequency("custom", 7, 2) is False

        assert controller.preferences == original_preferences
        assert controller.preferences_model is original_model
        assert controller.engine.preferences is original_engine_preferences
        assert controller.engine.gate.config is original_gate
        assert controller.activity.cooldown_seconds == original_cooldown
        assert controller.momentum is original_momentum
        assert controller._category_smooth_scores == original_scores
        assert database.get_setting("companion_custom_frequency") == original_custom_row
        assert database.get_setting("companion_preferences") == original_preference_row
        assert state_spy.count() == 0
        assert preference_spy.count() == 0
        assert statuses[-1] == "主动陪伴频率保存失败，请稍后重试"
    finally:
        controller.shutdown()
    assert app is not None


def test_screen_memory_write_failure_does_not_publish_uncommitted_mode(
    tmp_path, monkeypatch
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    state_spy = QSignalSpy(controller.changed)
    preference_spy = QSignalSpy(controller.preferencesChanged)
    original_mode = controller.preferences["screenMemoryMode"]
    original_set_setting = database.set_setting

    def fail_memory_mode_write(key, value):
        if key == "screen_observation_memory":
            raise sqlite3.OperationalError("synthetic memory preference failure")
        original_set_setting(key, value)

    monkeypatch.setattr(database, "set_setting", fail_memory_mode_write)
    try:
        with pytest.raises(sqlite3.OperationalError, match="memory preference failure"):
            controller.setScreenMemoryMode("all")
        assert controller.preferences["screenMemoryMode"] == original_mode
        assert database.get_setting("screen_observation_memory", original_mode) == original_mode
        assert state_spy.count() == 0
        assert preference_spy.count() == 0
    finally:
        controller.shutdown()
    assert app is not None


def test_frequency_write_rejects_non_owner_thread_without_side_effects(
    tmp_path,
) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    database = Database(tmp_path / "lilies.db")
    controller = CompanionController(
        database,
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    state_spy = QSignalSpy(controller.changed)
    preference_spy = QSignalSpy(controller.preferencesChanged)
    before_preferences = dict(controller.preferences)
    before_active_row = database.get_setting("companion_preferences", None)
    before_custom_row = database.get_setting("companion_custom_frequency", None)
    results: list[bool] = []

    worker = threading.Thread(
        target=lambda: results.append(controller.setFrequency("custom", 7, 2)),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=5)
    try:
        assert not worker.is_alive()
        assert results == [False]
        assert controller.preferences == before_preferences
        assert database.get_setting("companion_preferences", None) == before_active_row
        assert database.get_setting("companion_custom_frequency", None) == before_custom_row
        assert state_spy.count() == 0
        assert preference_spy.count() == 0
    finally:
        controller.shutdown()
    assert app is not None
