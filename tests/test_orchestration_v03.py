from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from lilies.core.orchestration import (
    IntentArbiter,
    IntentState,
    MODEL_TASK_PRIORITIES,
    ModelTaskBroker,
    ModelTaskKind,
    ModelTaskState,
    PetIntentKind,
    PresenceSignals,
    PresenceState,
    PresenceStateMachine,
)


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_presence_privacy_precedence_and_reset() -> None:
    clock = Clock(10.0)
    presence = PresenceStateMachine(clock=clock)

    assert presence.status()["state"] == PresenceState.NORMAL.value
    clock.value = 11.0
    silent = presence.update(fullscreenGame=True)
    assert silent.state is PresenceState.SILENT
    assert silent.reasons == ("fullscreen-game",)

    clock.value = 12.0
    blocked = presence.update(meeting=True, uac=True)
    assert blocked.state is PresenceState.BLOCKED
    assert blocked.reasons == ("meeting", "uac")
    assert presence.status()["signals"]["fullscreenGame"] is True

    clock.value = 13.0
    normal = presence.update(PresenceSignals())
    assert normal.state is PresenceState.NORMAL
    assert normal.generation == 3
    assert normal.changed_at == 13.0


@pytest.mark.parametrize(
    "signal",
    ["sensitive", "meeting", "remoteDesktop", "locked", "uac"],
)
def test_every_sensitive_presence_signal_is_blocked(signal: str) -> None:
    presence = PresenceStateMachine()
    snapshot = presence.update({signal: True})
    assert snapshot.state is PresenceState.BLOCKED


def test_presence_rejects_unknown_or_non_boolean_signals() -> None:
    presence = PresenceStateMachine()
    with pytest.raises(ValueError, match="unknown presence signal"):
        presence.update(keylogger=True)
    with pytest.raises(TypeError, match="must be bool"):
        presence.update(meeting=1)


def test_intent_arbiter_accepts_only_fixed_bounded_events() -> None:
    arbiter = IntentArbiter()
    event = arbiter.submit(
        PetIntentKind.READ_PAPER,
        {"durationMs": 12_000, "intensity": 0.4, "loop": False},
        source="reading",
    )

    assert event.state is IntentState.RUNNING
    assert event.payload == {
        "durationMs": 12_000,
        "intensity": 0.4,
        "loop": False,
    }

    with pytest.raises(ValueError, match="unknown pet intent"):
        arbiter.submit("run-arbitrary-animation", {})
    with pytest.raises(ValueError, match="forbidden"):
        arbiter.submit("prayer", {"x": 400})
    with pytest.raises(ValueError, match="forbidden"):
        arbiter.submit("show-bubble", {"script": "Qt.quit()"})
    with pytest.raises(ValueError, match="forbidden"):
        arbiter.submit("show-bubble", {"meta": {"command": "rm -rf /"}})
    with pytest.raises(ValueError, match="between"):
        arbiter.submit("prayer", {"durationMs": 900_000})
    with pytest.raises(ValueError, match="safe identifier"):
        arbiter.submit("show-bubble", {"bubbleId": "../../../token"})


def test_intent_priority_preempts_and_then_promotes_the_queue() -> None:
    arbiter = IntentArbiter()
    prayer = arbiter.submit("prayer")
    rest = arbiter.submit("rest")
    bubble = arbiter.submit(
        "show-bubble", {"bubbleId": "task.done", "category": "任务"}
    )

    assert arbiter.get(prayer.id).state is IntentState.CANCELLED
    assert arbiter.get(rest.id).state is IntentState.QUEUED
    assert bubble.state is IntentState.RUNNING

    arbiter.finish(bubble.id)
    assert arbiter.get(rest.id).state is IntentState.RUNNING


def test_model_priorities_are_fixed_in_the_required_order() -> None:
    ordered = [
        ModelTaskKind.EXPLICIT_CHAT_REPLY,
        ModelTaskKind.PAPER_SELECTION,
        ModelTaskKind.CONNECTOR_ASSIST,
        ModelTaskKind.PROACTIVE,
        ModelTaskKind.SCREEN_UNDERSTANDING,
        ModelTaskKind.MEMORY_ARCHIVE,
    ]
    assert [MODEL_TASK_PRIORITIES[item] for item in ordered] == [
        600,
        500,
        400,
        300,
        200,
        100,
    ]


def test_higher_priority_model_task_preempts_running_lower_priority() -> None:
    broker = ModelTaskBroker()
    archive = broker.submit("luna", "memory-archive")
    explicit = broker.submit("luna", "explicit-chat-reply")

    cancelled = broker.get(archive.id)
    assert cancelled.state is ModelTaskState.CANCELLED
    assert cancelled.cancel_reason == f"preempted-by:{explicit.id}"
    assert broker.cancellation_event(archive.id).is_set()
    assert explicit.state is ModelTaskState.RUNNING
    assert broker.status("luna")["models"]["luna"]["active"]["id"] == explicit.id


def test_each_model_has_one_running_task_and_its_own_queue() -> None:
    broker = ModelTaskBroker()
    first = broker.submit("luna", "explicit-chat-reply")
    queued = broker.submit("luna", "paper-selection")
    other_model = broker.submit("terra", "paper-selection")

    assert first.state is ModelTaskState.RUNNING
    assert queued.state is ModelTaskState.QUEUED
    assert other_model.state is ModelTaskState.RUNNING
    status = broker.status()
    assert status["models"]["luna"]["active"]["id"] == first.id
    assert status["models"]["terra"]["active"]["id"] == other_model.id

    broker.finish(first.id, result={"text": "完成"})
    assert broker.get(first.id).result == {"text": "完成"}
    assert broker.get(queued.id).state is ModelTaskState.RUNNING
    assert broker.get(other_model.id).state is ModelTaskState.RUNNING


def test_foreground_generation_cancels_only_context_bound_tasks() -> None:
    broker = ModelTaskBroker()
    explicit = broker.submit("luna", "explicit-chat-reply")
    contextual = broker.submit("luna", "proactive")
    archive = broker.submit("luna", "memory-archive")
    screen = broker.submit("terra", "screen-understanding")

    cancelled = set(broker.set_foreground_context("wps:paper-a"))

    assert cancelled == {contextual.id, screen.id}
    assert broker.get(contextual.id).cancel_reason == "foreground-context-changed"
    assert broker.get(screen.id).state is ModelTaskState.CANCELLED
    assert broker.get(explicit.id).state is ModelTaskState.RUNNING
    assert broker.get(archive.id).state is ModelTaskState.QUEUED
    assert broker.status()["contextGeneration"] == 1
    assert broker.set_foreground_context("wps:paper-a") == ()


def test_expired_jobs_never_start_and_next_valid_job_is_promoted() -> None:
    clock = Clock(100.0)
    broker = ModelTaskBroker(clock=clock)
    active = broker.submit("local-0.5b", "explicit-chat-reply")
    expired_later = broker.submit(
        "local-0.5b", "paper-selection", expires_at=105.0
    )
    valid = broker.submit("local-0.5b", "connector-assist")

    clock.value = 106.0
    broker.finish(active.id)

    assert broker.get(expired_later.id).state is ModelTaskState.CANCELLED
    assert broker.get(expired_later.id).cancel_reason == "expired"
    assert broker.get(valid.id).state is ModelTaskState.RUNNING


def test_concurrent_submissions_still_start_only_one_task_per_model() -> None:
    broker = ModelTaskBroker()

    def submit_archive(index: int):
        return broker.submit("luna", "memory-archive", {"index": index})

    with ThreadPoolExecutor(max_workers=8) as executor:
        tasks = list(executor.map(submit_archive, range(32)))

    status = broker.status("luna")["models"]["luna"]
    assert status["active"] is not None
    assert len(status["queued"]) == 31
    assert sum(task.state is ModelTaskState.RUNNING for task in map(broker.get, (t.id for t in tasks))) == 1


def test_terminal_model_task_diagnostics_are_bounded() -> None:
    broker = ModelTaskBroker()
    recent_id = ""
    for index in range(1000):
        task = broker.submit("luna", ModelTaskKind.MEMORY_ARCHIVE, {"index": index})
        broker.finish(task.id)
        recent_id = task.id

    assert broker.get(recent_id).state is ModelTaskState.COMPLETED
    assert len(broker._tasks) <= broker._TERMINAL_HISTORY_LIMIT
    assert len(broker._cancel_events) <= broker._TERMINAL_HISTORY_LIMIT
    assert len(broker._order) <= broker._TERMINAL_HISTORY_LIMIT
    assert len(broker._terminal_ids) <= broker._TERMINAL_HISTORY_LIMIT
    assert broker.status()["models"] == {}
