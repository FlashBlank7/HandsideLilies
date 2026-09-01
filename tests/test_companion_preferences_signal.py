from __future__ import annotations

from PySide6.QtTest import QSignalSpy

from lilies.companion_controller import CompanionController
from lilies.core.companion import ContentCategory
from lilies.core.database import Database


def test_preferences_signal_is_distinct_and_only_emits_for_real_changes(
    tmp_path,
) -> None:
    controller = CompanionController(
        Database(tmp_path / "lilies.db"),
        tmp_path,
        active=False,
        status_sink=lambda _message: None,
        move_to_box=lambda _payload: None,
        foreground_provider=lambda: 0,
    )
    preference_spy = QSignalSpy(controller.preferencesChanged)
    state_spy = QSignalSpy(controller.changed)

    try:
        property_index = controller.metaObject().indexOfProperty("preferences")
        property_meta = controller.metaObject().property(property_index)
        assert bytes(property_meta.notifySignal().name()) == b"preferencesChanged"

        controller._consider()
        assert state_spy.count() >= 1
        assert preference_spy.count() == 0

        controller.setFrequency("quiet", 45, 6)
        assert controller.preferences["frequency"] == "quiet"
        controller.setFrequency("quiet", 45, 6)
        assert preference_spy.count() == 1

        controller.setMix(70, 30, 45)
        assert controller.preferences["interestWeight"] == 70
        controller.setMix(70, 30, 45)
        assert preference_spy.count() == 2

        controller.setCategoryWeight(ContentCategory.SCIENCE.value, 25)
        assert controller.preferences["categoryWeights"][
            ContentCategory.SCIENCE.value
        ] == 25
        controller.setCategoryWeight(ContentCategory.SCIENCE.value, 25)
        assert preference_spy.count() == 3

        controller.setInterests("biology, astronomy, biology")
        assert controller.preferences["interests"] == ["biology", "astronomy"]
        controller.setInterests("biology, astronomy")
        assert preference_spy.count() == 4

        controller.setScreenMemoryMode("all")
        assert controller.preferences["screenMemoryMode"] == "all"
        controller.setScreenMemoryMode("all")
        assert preference_spy.count() == 5
    finally:
        controller.shutdown()
