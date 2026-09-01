from __future__ import annotations

from datetime import UTC, datetime, timedelta

from PySide6.QtWidgets import QApplication
import pytest

from lilies.backend import Backend
from lilies.core.database import Database
from lilies.core.productivity import BoxWorldService, WardrobeService


def test_box_world_status_is_a_complete_progress_catalog(tmp_path) -> None:
    database = Database(tmp_path / "world-catalog.db")
    world = BoxWorldService(database)

    status = world.status()

    assert status["entered"] is False
    assert status["totalCount"] == 6
    assert status["unlockedCount"] == 1
    assert status["placedCount"] == 1
    assert status["availableCount"] == 0
    assert status["objects"][0]["object_id"] == "box-core"
    assert {value["object_id"] for value in status["objects"]} == {
        "box-core",
        "paper-shelf",
        "workbench",
        "living-corner",
        "letter-rack",
        "rest-cushion",
    }
    assert all(value["unlockHint"] for value in status["objects"])
    assert world.inspect("paper-shelf")["unlockHint"] == "完成一次完整的论文阅读"
    with pytest.raises(PermissionError):
        world.place("paper-shelf")


def test_wardrobe_marks_the_current_outfit_and_pose(tmp_path) -> None:
    inventory = WardrobeService(Database(tmp_path / "wardrobe-current.db")).list()

    equipped_outfits = [value["id"] for value in inventory["outfits"] if value["equipped"]]
    equipped_poses = [value["id"] for value in inventory["poses"] if value["equipped"]]
    assert equipped_outfits == ["first-encounter"]
    assert equipped_poses == ["idle-prayer"]


def test_backend_wires_world_growth_and_wardrobe_without_switching_shell(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("LILIES_DATA_DIR", str(tmp_path / "private-data"))
    app = QApplication.instance() or QApplication([])
    backend = Backend(smoke=True, force_compact=True)
    try:
        methods = {
            backend.metaObject().method(index).name().data().decode("utf-8")
            for index in range(backend.metaObject().methodCount())
        }
        assert "boxWorldPlace" in methods

        status = backend.boxWorldStatus
        assert status["totalCount"] == 6
        assert status["growth"] == {
            "points": 0,
            "stage": "初遇",
            "nextStage": "熟悉",
            "remaining": 100,
            "progress": 0.0,
        }
        assert status["wardrobe"]["outfitName"] == "初遇裂纹裙"
        assert status["wardrobe"]["poseName"] == "抱拳祈祷"

        shell_mode = backend.shellMode
        backend.enterBoxWorld()
        backend.boxWorldPlace("paper-shelf")
        paper_shelf = next(
            value
            for value in backend.boxWorldStatus["objects"]
            if value["object_id"] == "paper-shelf"
        )
        assert paper_shelf["unlocked"] is False
        assert paper_shelf["placed"] is False

        current = [datetime(2026, 8, 30, 1, 0, tzinfo=UTC)]
        now = lambda: current[0]
        backend.growth.now = now
        backend.reading_sessions.now = now
        reading = backend.reading_sessions.start(title="盒中测试", source="paper.pdf")
        current[0] += timedelta(minutes=20)
        backend.reading_sessions.finish(str(reading["session_id"]))
        backend.boxWorldPlace("paper-shelf")
        placed_shelf = next(
            value
            for value in backend.boxWorldStatus["objects"]
            if value["object_id"] == "paper-shelf"
        )
        assert placed_shelf["unlocked"] is True
        assert placed_shelf["placed"] is True
        assert placed_shelf["position"] == {"x": 0.5, "y": 0.5}
        assert backend.shellMode == shell_mode == "compact"
    finally:
        backend.shutdown()
        app.processEvents()
