from __future__ import annotations

import json
import threading
import time
from io import StringIO
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

import lilies.core.model as model_module
from lilies.companion_controller import CompanionController
from lilies.core.companion import ContentCategory
from lilies.core.companion_runtime import LUNA_MODEL, TERRA_MODEL
from lilies.core.components import ComponentAction, ComponentRegistry, ConfirmationRequired
from lilies.core.database import Database
from lilies.core.model import GPT_MODEL_NAME, MODEL_NAME, ChatService, _BrokerTaskLease
from lilies.core.orchestration import (
    ModelTaskBroker,
    ModelTaskKind,
    ModelTaskState,
)
from lilies.core.permissions import PermissionBroker, Risk
from lilies.core.reading import prepare_reading_request
from lilies.core.selection import SelectionService


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true before timeout")


class RecordingBroker(ModelTaskBroker):
    def __init__(self) -> None:
        super().__init__()
        self.submissions: list[tuple[str, ModelTaskKind, dict[str, Any]]] = []

    def submit(self, model_id, kind, payload=None, **kwargs):
        normalized_kind = kind if isinstance(kind, ModelTaskKind) else ModelTaskKind(kind)
        self.submissions.append((str(model_id), normalized_kind, dict(payload or {})))
        return super().submit(model_id, kind, payload, **kwargs)


class _LocalWorkerStream:
    def __init__(self, lines: list[str], *, before_read=None) -> None:
        self._lines = iter(lines)
        self._before_read = before_read
        self._reads = 0

    def readline(self) -> str:
        self._reads += 1
        if self._before_read is not None:
            self._before_read(self._reads)
        return next(self._lines, "")


class _LocalWorkerProcess:
    def __init__(self, stdout: _LocalWorkerStream) -> None:
        self.stdin = StringIO()
        self.stdout = stdout


def test_execution_lease_waits_for_preempted_worker_to_release_model() -> None:
    broker = ModelTaskBroker()
    low_task = broker.submit(LUNA_MODEL, ModelTaskKind.MEMORY_ARCHIVE)
    low_aborted = threading.Event()
    low_lease = _BrokerTaskLease(
        broker,
        low_task.id,
        LUNA_MODEL,
        abort=low_aborted.set,
    )
    assert low_lease.acquire()

    high_task = broker.submit(LUNA_MODEL, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    high_acquired = threading.Event()
    high_lease = _BrokerTaskLease(broker, high_task.id, LUNA_MODEL)

    worker = threading.Thread(
        target=lambda: high_acquired.set() if high_lease.acquire() else None,
        daemon=True,
    )
    worker.start()
    assert low_aborted.wait(1)
    assert not high_acquired.wait(0.08)

    low_lease.close(result={"cancelled": True})
    assert high_acquired.wait(1)
    high_lease.close(result={"completed": True})
    worker.join(timeout=1)
    assert broker.get(low_task.id).state is ModelTaskState.CANCELLED
    assert broker.get(high_task.id).state is ModelTaskState.COMPLETED


def test_brokerless_execution_lease_still_aborts_its_own_call() -> None:
    local_cancel = threading.Event()
    aborted = threading.Event()
    lease = _BrokerTaskLease(
        None,
        None,
        LUNA_MODEL,
        abort=aborted.set,
        local_cancel=local_cancel,
    )
    assert lease.acquire()
    local_cancel.set()
    assert aborted.wait(1.0)
    lease.close(result={"cancelled": True})


def test_execution_lease_repeats_abort_until_cancelled_owner_closes() -> None:
    broker = ModelTaskBroker()
    task = broker.submit(LUNA_MODEL, ModelTaskKind.PROACTIVE)
    abort_count = 0
    abort_lock = threading.Lock()
    second_abort = threading.Event()

    def abort() -> None:
        nonlocal abort_count
        with abort_lock:
            abort_count += 1
            if abort_count >= 2:
                second_abort.set()

    lease = _BrokerTaskLease(broker, task.id, LUNA_MODEL, abort=abort)
    assert lease.acquire()
    broker.cancel(task.id, reason="test-cancel-after-acquire")
    assert second_abort.wait(1.0)
    lease.close(result={"cancelled": True})
    with abort_lock:
        count_after_close = abort_count
    time.sleep(0.16)
    with abort_lock:
        assert abort_count == count_after_close


def test_execution_lease_commit_linearizes_completion_against_cancel() -> None:
    broker = ModelTaskBroker()
    completed_task = broker.submit(LUNA_MODEL, ModelTaskKind.CONNECTOR_ASSIST)
    completed_lease = _BrokerTaskLease(
        broker, completed_task.id, LUNA_MODEL
    )
    assert completed_lease.acquire()
    assert completed_lease.commit(result={"ok": True}) is True
    broker.cancel(completed_task.id, reason="late-user-cancel")
    assert broker.get(completed_task.id).state is ModelTaskState.COMPLETED
    completed_lease.close()

    cancelled_task = broker.submit(LUNA_MODEL, ModelTaskKind.CONNECTOR_ASSIST)
    cancelled_lease = _BrokerTaskLease(
        broker, cancelled_task.id, LUNA_MODEL
    )
    assert cancelled_lease.acquire()
    broker.cancel(cancelled_task.id, reason="cancel-wins")
    assert cancelled_lease.commit(result={"ok": True}) is False
    assert broker.get(cancelled_task.id).state is ModelTaskState.CANCELLED
    cancelled_lease.close()


def test_closing_unacquired_queued_lease_cancels_ghost_task() -> None:
    broker = ModelTaskBroker()
    owner = broker.submit(LUNA_MODEL, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    queued = broker.submit(LUNA_MODEL, ModelTaskKind.PROACTIVE)
    lease = _BrokerTaskLease(broker, queued.id, LUNA_MODEL)

    lease.close(result={"completed": False})
    assert broker.get(queued.id).state is ModelTaskState.CANCELLED

    broker.finish(owner.id, result={"completed": True})
    replacement = broker.submit(LUNA_MODEL, ModelTaskKind.PROACTIVE)
    assert broker.get(replacement.id).state is ModelTaskState.RUNNING


def test_chat_submission_freezes_terra_provider_until_worker_commit(
    tmp_path, monkeypatch
) -> None:
    broker = RecordingBroker()
    chat = ChatService(Database(tmp_path / "provider-freeze.db"), model_broker=broker)

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"
        input_modalities: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, prompt, timeout, on_delta):
            self.calls += 1
            return "frozen-provider-reply"

        def abort(self) -> None:
            return None

        def stop(self) -> None:
            return None

    class DeferredThread:
        instance = None

        def __init__(self, *, target, args, **_kwargs) -> None:
            self.target = target
            self.args = args
            DeferredThread.instance = self

        def start(self) -> None:
            return None

    fake_gpt = FakeGpt()
    chat._gpt = fake_gpt
    with monkeypatch.context() as patch:
        patch.setattr(model_module.threading, "Thread", DeferredThread)
        chat.send("provider freeze regression")

    worker = DeferredThread.instance
    assert worker is not None
    fake_gpt.ready = False
    worker.target(*worker.args)

    model_id, kind, _payload = broker.submissions[-1]
    task = broker.get(worker.args[2])
    assert model_id == GPT_MODEL_NAME
    assert kind is ModelTaskKind.EXPLICIT_CHAT_REPLY
    assert fake_gpt.calls == 1
    assert task is not None and task.state is ModelTaskState.COMPLETED
    assert chat._last_metrics["model"] == GPT_MODEL_NAME
    assert chat.database.recent_messages(chat.conversation_id, 2)[-1]["content"] == (
        "frozen-provider-reply"
    )
    chat.shutdown()


def test_terra_chat_exposes_and_dispatches_reviewed_box_components(tmp_path) -> None:
    database = Database(tmp_path / "terra-component-tools.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    handler_calls: list[tuple[str, dict[str, Any]]] = []
    registry.register(
        ComponentAction(
            component_id="tasks",
            action_id="list",
            title="任务列表",
            description="读取任务",
            risk=Risk.READ,
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200}
                },
                "additionalProperties": False,
            },
            handler=lambda payload: handler_calls.append(("list", dict(payload)))
            or [{"id": "task-1", "title": "读论文"}],
        )
    )
    registry.register(
        ComponentAction(
            component_id="tasks",
            action_id="create",
            title="创建任务",
            description="创建本地任务",
            risk=Risk.MUTATE,
            parameters={
                "type": "object",
                "required": ["title"],
                "properties": {"title": {"type": "string", "minLength": 1}},
                "additionalProperties": False,
            },
            handler=lambda payload: handler_calls.append(("create", dict(payload)))
            or {"id": "task-2", "title": payload["title"]},
        )
    )

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"
        input_modalities: tuple[str, ...] = ()

        def __init__(self) -> None:
            self.namespaces: list[str] = []
            self.tool_results: list[dict[str, Any]] = []

        def complete(
            self,
            _prompt,
            timeout,
            on_delta,
            dynamic_tools,
            tool_handler,
        ):
            del timeout
            self.namespaces = [str(value.get("name", "")) for value in dynamic_tools]
            self.tool_results.append(
                tool_handler(
                    "tasks__list",
                    {"limit": 5},
                    {"namespace": "box"},
                )
            )
            self.tool_results.append(
                tool_handler(
                    "tasks__create",
                    {"title": "整理今天的论文笔记"},
                    {"namespace": "box"},
                )
            )
            on_delta("已经记下，也把现有任务看过了。")
            return "已经记下，也把现有任务看过了。"

        def abort(self) -> None:
            return None

        def stop(self) -> None:
            return None

    chat = ChatService(database)
    chat.bind_registry(registry)
    fake_gpt = FakeGpt()
    chat._gpt = fake_gpt
    confirmations: list[dict[str, Any]] = []
    invoked: list[tuple[str, str]] = []
    finished: list[str] = []
    chat.confirmationRequested.connect(
        lambda request: (
            confirmations.append(dict(request)),
            chat.resolve_confirmation(True),
        )
    )
    chat.componentInvoked.connect(
        lambda component, action, _result: invoked.append((component, action))
    )
    chat.responseFinished.connect(finished.append)

    chat._chat_worker(
        "请查看我的任务，并新建一个整理今天论文笔记的任务",
        None,
        None,
        GPT_MODEL_NAME,
    )

    assert fake_gpt.namespaces == ["memory", "box"]
    assert handler_calls == [
        ("list", {"limit": 5}),
        ("create", {"title": "整理今天的论文笔记"}),
    ]
    assert [result["result"] for result in fake_gpt.tool_results] == [
        [{"id": "task-1", "title": "读论文"}],
        {"id": "task-2", "title": "整理今天的论文笔记"},
    ]
    assert len(confirmations) == 1
    assert confirmations[0]["componentId"] == "tasks"
    assert confirmations[0]["actionId"] == "create"
    assert invoked == [("tasks", "list"), ("tasks", "create")]
    assert finished == ["已经记下，也把现有任务看过了。"]
    with database.connect() as connection:
        decisions = [
            str(row[0])
            for row in connection.execute(
                "SELECT decision FROM audit_log ORDER BY created_at"
            )
        ]
    assert decisions.count("confirm") == 1
    assert decisions.count("allow") == 2
    chat.shutdown()


def test_cancel_wins_before_deterministic_reply_commit_and_suppresses_publication(
    tmp_path,
) -> None:
    broker = ModelTaskBroker()
    chat = ChatService(Database(tmp_path / "commit-fence.db"), model_broker=broker)
    task = broker.submit(GPT_MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    entered = threading.Event()
    release = threading.Event()
    chunks: list[str] = []
    finished: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)
    chat._deterministic_memory_reply = lambda _text: ""

    def delayed_reply(_text: str) -> str:
        entered.set()
        release.wait(2)
        return "must-not-be-published"

    chat._deterministic_identity_reply = delayed_reply
    def cancel_after_reply_starts() -> None:
        assert entered.wait(1)
        broker.cancel(task.id, reason="cancel-wins-before-commit")
        release.set()

    canceller = threading.Thread(target=cancel_after_reply_starts, daemon=True)
    canceller.start()
    chat._chat_worker("commit regression", None, task.id, GPT_MODEL_NAME)
    canceller.join(timeout=1)

    assert not canceller.is_alive()
    assert broker.get(task.id).state is ModelTaskState.CANCELLED
    assert chunks == []
    assert finished == [""]
    assert all(
        value["content"] != "must-not-be-published"
        for value in chat.database.recent_messages(chat.conversation_id, 10)
    )
    chat.shutdown()


def test_cancelled_terra_completion_cannot_publish_or_persist_late_reply(
    tmp_path,
) -> None:
    broker = ModelTaskBroker()
    chat = ChatService(Database(tmp_path / "terra-commit-fence.db"), model_broker=broker)
    entered = threading.Event()
    release = threading.Event()

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"
        input_modalities: tuple[str, ...] = ()

        def complete(self, _prompt, timeout, on_delta):
            entered.set()
            release.wait(2)
            return "late-terra-reply"

        def abort(self) -> None:
            return None

        def stop(self) -> None:
            return None

    chat._gpt = FakeGpt()
    finished: list[str] = []
    chat.responseFinished.connect(finished.append)
    task = broker.submit(GPT_MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    def cancel_after_terra_starts() -> None:
        assert entered.wait(1)
        broker.cancel(task.id, reason="cancel-terra-before-return")
        release.set()

    canceller = threading.Thread(target=cancel_after_terra_starts, daemon=True)
    canceller.start()
    chat._chat_worker("late terra regression", None, task.id, GPT_MODEL_NAME)
    canceller.join(timeout=1)

    assert not canceller.is_alive()
    assert finished == [""]
    assert all(
        value["content"] != "late-terra-reply"
        for value in chat.database.recent_messages(chat.conversation_id, 10)
    )
    chat.shutdown()


def test_terra_exception_after_partial_suppresses_stale_final_and_persistence(
    tmp_path,
) -> None:
    broker = ModelTaskBroker()
    chat = ChatService(Database(tmp_path / "terra-partial-error.db"), model_broker=broker)

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"
        input_modalities: tuple[str, ...] = ()

        def complete(self, _prompt, timeout, on_delta):
            on_delta("provisional partial")
            raise RuntimeError("terra stream failed")

        def abort(self) -> None:
            return None

        def stop(self) -> None:
            return None

    chat._gpt = FakeGpt()
    chunks: list[str] = []
    finished: list[str] = []
    errors: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)
    chat.error.connect(errors.append)
    task = broker.submit(GPT_MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)

    chat._chat_worker("terra partial failure", None, task.id, GPT_MODEL_NAME)

    assert chunks == ["provisional partial"]
    assert finished == [""]
    assert errors and "terra stream failed" in errors[-1]
    assert all(
        value["content"] != "provisional partial"
        for value in chat.database.recent_messages(chat.conversation_id, 10)
    )
    chat.shutdown()


def _prepare_local_chat(chat, tmp_path, monkeypatch, process) -> None:
    for name in (
        "model.safetensors",
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
    ):
        (tmp_path / name).write_text("test", encoding="utf-8")
    monkeypatch.setattr(model_module, "MODEL_PATH", tmp_path)
    monkeypatch.setattr(model_module, "local_runtime", lambda: tmp_path / "python")
    monkeypatch.setattr(model_module, "worker_script", lambda: tmp_path / "worker.py")
    monkeypatch.setattr(chat, "_ensure_worker", lambda: process)


def test_local_done_commits_reply_then_publishes_metrics(
    tmp_path, monkeypatch
) -> None:
    chat = ChatService(Database(tmp_path / "local-done.db"))
    process = _LocalWorkerProcess(
        _LocalWorkerStream(
            [
                json.dumps({"type": "chunk", "text": "local reply"}) + "\n",
                json.dumps(
                    {
                        "type": "done",
                        "generatedTokens": 2,
                        "tokensPerSecond": 4.0,
                    }
                )
                + "\n",
            ]
        )
    )
    _prepare_local_chat(chat, tmp_path, monkeypatch, process)
    chat._last_metrics = {"previous": True}
    chunks: list[str] = []
    finished: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)

    chat._chat_worker("local done", None, None, MODEL_NAME)

    assert chunks == ["local reply"]
    assert finished == ["local reply"]
    assert chat._last_metrics == {"generatedTokens": 2, "tokensPerSecond": 4.0}
    assert chat.database.recent_messages(chat.conversation_id, 1)[-1]["content"] == (
        "local reply"
    )
    chat.shutdown()


def test_local_done_losing_broker_commit_publishes_neither_final_nor_metrics(
    tmp_path, monkeypatch
) -> None:
    broker = ModelTaskBroker()
    task = broker.submit(MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    chat = ChatService(Database(tmp_path / "local-done-cancelled.db"), model_broker=broker)

    def cancel_before_done(read_number: int) -> None:
        if read_number == 2:
            broker.cancel(task.id, reason="cancel-before-local-done-commit")

    process = _LocalWorkerProcess(
        _LocalWorkerStream(
            [
                json.dumps({"type": "chunk", "text": "buffered"}) + "\n",
                json.dumps({"type": "done", "generatedTokens": 1}) + "\n",
            ],
            before_read=cancel_before_done,
        )
    )
    _prepare_local_chat(chat, tmp_path, monkeypatch, process)
    chat._last_metrics = {"previous": True}
    chunks: list[str] = []
    finished: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)

    chat._chat_worker("local cancelled done", None, task.id, MODEL_NAME)

    assert broker.get(task.id).state is ModelTaskState.CANCELLED
    assert chunks == []
    assert finished == [""]
    assert chat._last_metrics == {"previous": True}
    chat.shutdown()


def test_local_error_does_not_publish_final_or_replace_metrics(
    tmp_path, monkeypatch
) -> None:
    chat = ChatService(Database(tmp_path / "local-error.db"))
    process = _LocalWorkerProcess(
        _LocalWorkerStream(
            [
                json.dumps({"type": "chunk", "text": "buffered"}) + "\n",
                json.dumps({"type": "error", "message": "worker failed"}) + "\n",
            ]
        )
    )
    _prepare_local_chat(chat, tmp_path, monkeypatch, process)
    chat._last_metrics = {"previous": True}
    chunks: list[str] = []
    finished: list[str] = []
    errors: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)
    chat.error.connect(errors.append)

    chat._chat_worker("local error", None, None, MODEL_NAME)

    assert chunks == []
    assert finished == [""]
    assert errors == ["worker failed"]
    assert chat._last_metrics == {"previous": True}
    assert all(
        value["content"] != "buffered"
        for value in chat.database.recent_messages(chat.conversation_id, 10)
    )
    chat.shutdown()


def test_local_cancel_does_not_release_buffered_prefix_or_stale_final(
    tmp_path, monkeypatch
) -> None:
    chat = ChatService(Database(tmp_path / "local-cancel.db"))

    def cancel_with_first_chunk(read_number: int) -> None:
        if read_number == 1:
            chat._cancel.set()

    process = _LocalWorkerProcess(
        _LocalWorkerStream(
            [json.dumps({"type": "chunk", "text": "buffered"}) + "\n"],
            before_read=cancel_with_first_chunk,
        )
    )
    _prepare_local_chat(chat, tmp_path, monkeypatch, process)
    chat._last_metrics = {"previous": True}
    chunks: list[str] = []
    finished: list[str] = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)

    chat._chat_worker("local cancel", None, None, MODEL_NAME)

    assert chunks == []
    assert finished == [""]
    assert chat._last_metrics == {"previous": True}
    chat.shutdown()


@pytest.mark.parametrize("stop_method", ["cancel", "shutdown"])
def test_cancel_and_shutdown_reject_and_wake_pending_confirmation(
    tmp_path, stop_method: str
) -> None:
    chat = ChatService(Database(tmp_path / f"confirm-{stop_method}.db"))

    class Registry:
        def __init__(self) -> None:
            self.calls: list[bool] = []

        def invoke(self, _component, _action, _payload, **kwargs):
            confirmed = bool(kwargs.get("confirmed"))
            self.calls.append(confirmed)
            if not confirmed:
                raise ConfirmationRequired(
                    "theme", "activate", Risk.MUTATE, "confirmation required"
                )
            return {"auditId": "confirmed", "result": {}}

    registry = Registry()
    chat.bind_registry(registry)
    errors: list[Exception] = []

    def invoke() -> None:
        try:
            chat._invoke_component("theme", "activate", {"renderer": "video"})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=invoke, daemon=True)
    worker.start()
    _wait_until(lambda: chat._pending_event is not None)
    started = time.monotonic()
    getattr(chat, stop_method)()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert time.monotonic() - started < 1
    assert registry.calls == [False]
    assert len(errors) == 1 and isinstance(errors[0], PermissionError)
    assert chat._pending_event is None
    if stop_method == "cancel":
        chat.shutdown()


def test_lease_cancel_between_deterministic_tool_steps_fences_side_effect(
    tmp_path, monkeypatch
) -> None:
    chat = ChatService(Database(tmp_path / "tool-fence.db"))

    class Lease:
        cancelled = False

    lease = Lease()

    class Registry:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def invoke(self, component, action, payload, **_kwargs):
            self.calls.append((component, action))
            if (component, action) == ("app-launcher", "search"):
                lease.cancelled = True
                return {
                    "auditId": "search",
                    "result": [{"name": "Demo", "path": "demo.lnk"}],
                }
            raise AssertionError("cancelled lease must not enter concrete open action")

    registry = Registry()
    chat.bind_registry(registry)
    monkeypatch.setattr(chat, "_application_launch_query", lambda _text: "Demo")

    with pytest.raises(PermissionError, match="request cancelled"):
        chat._route_simple_tool("tool fence regression", lease=lease)

    assert registry.calls == [("app-launcher", "search")]
    chat.shutdown()


def test_component_handler_is_not_entered_when_cancel_wins_commit_race(
    tmp_path, monkeypatch
) -> None:
    database = Database(tmp_path / "component-race-cancel.db")
    registry = ComponentRegistry(database, PermissionBroker(database))
    handler_calls: list[dict[str, Any]] = []
    registry.register(
        ComponentAction(
            "probe",
            "open",
            "open",
            "test launch",
            Risk.LAUNCH,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda payload: handler_calls.append(payload) or {"opened": True},
        )
    )
    broker = ModelTaskBroker()
    task = broker.submit(MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    lease = _BrokerTaskLease(broker, task.id, MODEL_NAME)
    assert lease.acquire()
    original_commit = lease.commit

    def cancel_at_linearization(*, result=None):
        broker.cancel(task.id, reason="cancel-at-handler-fence")
        return original_commit(result=result)

    monkeypatch.setattr(lease, "commit", cancel_at_linearization)
    chat = ChatService(database, model_broker=broker)
    chat.bind_registry(registry)
    try:
        with pytest.raises(PermissionError, match="request cancelled"):
            chat._invoke_component("probe", "open", {}, lease=lease)

        assert broker.get(task.id).state is ModelTaskState.CANCELLED
        assert handler_calls == []
    finally:
        lease.close()
        chat.shutdown()


def test_component_handler_runs_only_after_broker_commit_linearizes(
    tmp_path,
) -> None:
    database = Database(tmp_path / "component-race-commit.db")
    broker = ModelTaskBroker()
    task = broker.submit(MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    lease = _BrokerTaskLease(broker, task.id, MODEL_NAME)
    assert lease.acquire()
    states_seen: list[ModelTaskState] = []

    def handler(_payload):
        states_seen.append(broker.get(task.id).state)
        broker.cancel(task.id, reason="late-cancel-after-linearization")
        return {"opened": True}

    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(
        ComponentAction(
            "probe",
            "open",
            "open",
            "test launch",
            Risk.LAUNCH,
            {"type": "object", "properties": {}, "additionalProperties": False},
            handler,
        )
    )
    chat = ChatService(database, model_broker=broker)
    chat.bind_registry(registry)
    try:
        result = chat._invoke_component("probe", "open", {}, lease=lease)

        assert result["result"] == {"opened": True}
        assert states_seen == [ModelTaskState.COMPLETED]
        assert broker.get(task.id).state is ModelTaskState.COMPLETED
    finally:
        lease.close()
        chat.shutdown()


def test_brokerless_component_handler_remains_compatible(tmp_path) -> None:
    database = Database(tmp_path / "component-brokerless.db")
    calls: list[str] = []
    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(
        ComponentAction(
            "probe",
            "open",
            "open",
            "test launch",
            Risk.LAUNCH,
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _payload: calls.append("handler") or {"opened": True},
        )
    )
    lease = _BrokerTaskLease(None, None, MODEL_NAME)
    assert lease.acquire()
    chat = ChatService(database)
    chat.bind_registry(registry)
    try:
        result = chat._invoke_component("probe", "open", {}, lease=lease)

        assert result["result"] == {"opened": True}
        assert calls == ["handler"]
    finally:
        lease.close()
        chat.shutdown()


@pytest.mark.parametrize("approved", [False, True])
def test_component_confirmation_preflight_is_audited_without_duplicate_handler(
    tmp_path, approved: bool
) -> None:
    database = Database(tmp_path / f"component-confirm-{approved}.db")
    broker = ModelTaskBroker()
    task = broker.submit(MODEL_NAME, ModelTaskKind.EXPLICIT_CHAT_REPLY)
    lease = _BrokerTaskLease(broker, task.id, MODEL_NAME)
    assert lease.acquire()
    handler_calls: list[ModelTaskState] = []

    def handler(_payload):
        handler_calls.append(broker.get(task.id).state)
        return {"changed": True}

    registry = ComponentRegistry(database, PermissionBroker(database))
    registry.register(
        ComponentAction(
            "probe",
            "mutate",
            "mutate",
            "test mutation",
            Risk.MUTATE,
            {"type": "object", "properties": {}, "additionalProperties": False},
            handler,
        )
    )
    chat = ChatService(database, model_broker=broker)
    chat.bind_registry(registry)
    chat.confirmationRequested.connect(
        lambda _request: chat.resolve_confirmation(approved)
    )
    try:
        if approved:
            result = chat._invoke_component("probe", "mutate", {}, lease=lease)
            assert result["result"] == {"changed": True}
            assert handler_calls == [ModelTaskState.COMPLETED]
        else:
            with pytest.raises(PermissionError):
                chat._invoke_component("probe", "mutate", {}, lease=lease)
            assert handler_calls == []

        with database.connect() as connection:
            decisions = [
                row[0]
                for row in connection.execute(
                    "SELECT decision FROM audit_log ORDER BY created_at"
                )
            ]
        assert decisions.count("confirm") == 1
        assert decisions.count("allow") == int(approved)
        assert decisions.count("deny") == int(not approved)
    finally:
        lease.close()
        chat.shutdown()


def test_chat_preempts_terra_proactive_without_putting_message_in_payload(tmp_path) -> None:
    broker = RecordingBroker()
    proactive = broker.submit(TERRA_MODEL, ModelTaskKind.PROACTIVE, {"requestId": "old"})
    chat = ChatService(Database(tmp_path / "chat.db"), model_broker=broker)

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"
        input_modalities: tuple[str, ...] = ()

        def complete(self, prompt, timeout, on_delta):
            on_delta("收到。")
            return "收到。"

        def abort(self):
            return None

        def stop(self):
            return None

    chat._gpt = FakeGpt()
    secret_message = "broker-payload-must-not-contain-this-message"
    chat.send(secret_message)
    _wait_until(lambda: not chat._working)

    assert broker.get(proactive.id).state is ModelTaskState.CANCELLED
    model_id, kind, payload = broker.submissions[-1]
    assert model_id == TERRA_MODEL
    assert kind is ModelTaskKind.EXPLICIT_CHAT_REPLY
    assert secret_message not in json.dumps(payload, ensure_ascii=False)
    assert set(payload) == {"requestId", "conversationId", "hasImage"}
    chat.shutdown()


def test_selection_uses_luna_paper_priority_without_selected_text_in_payload(tmp_path) -> None:
    _app = QCoreApplication.instance() or QCoreApplication([])
    broker = RecordingBroker()
    archive = broker.submit(LUNA_MODEL, ModelTaskKind.MEMORY_ARCHIVE)
    service = SelectionService(
        Database(tmp_path / "selection.db"),
        active=False,
        model_broker=broker,
    )

    class FakeSubscription:
        ready = False
        available = False
        signed_in = False
        plan_type = ""

        def explain(self, source: str) -> str:
            return "解释"

        def reading_action(self, source: str, action: str, question: str = "") -> str:
            return "解释"

        def abort(self) -> None:
            return None

        def stop(self) -> None:
            return None

    service._subscription = FakeSubscription()
    selected_text = "private-selected-paper-sentence"
    service._start_request(prepare_reading_request(selected_text, "explain"), 10, 20)
    _wait_until(
        lambda: broker.submissions[-1][1] is ModelTaskKind.PAPER_SELECTION
        and broker.status(LUNA_MODEL)["models"][LUNA_MODEL]["active"] is None
    )

    assert broker.get(archive.id).state is ModelTaskState.CANCELLED
    model_id, kind, payload = broker.submissions[-1]
    assert model_id == LUNA_MODEL
    assert kind is ModelTaskKind.PAPER_SELECTION
    assert selected_text not in json.dumps(payload, ensure_ascii=False)
    assert set(payload) == {"requestId", "action", "sourceApplication"}
    service.shutdown()


def test_foreground_change_aborts_context_bound_companion_generation(tmp_path) -> None:
    _app = QCoreApplication.instance() or QCoreApplication([])
    broker = RecordingBroker()
    controller = CompanionController(
        Database(tmp_path / "companion.db"),
        tmp_path,
        active=False,
        status_sink=lambda _value: None,
        move_to_box=lambda _value: None,
        model_broker=broker,
    )
    controller.runtime.shutdown()

    class BlockingRuntime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.aborted: list[str] = []

        def generate(self, **_kwargs):
            self.started.set()
            self.release.wait(2)
            return {
                "summary": "不会显示",
                "detail": "不会显示",
                "model": LUNA_MODEL,
                "contextType": "application-signal",
            }

        def abort_model(self, model_id: str) -> None:
            self.aborted.append(model_id)
            self.release.set()

        def shutdown(self) -> None:
            self.release.set()

    runtime = BlockingRuntime()
    controller.runtime = runtime
    assert controller._start_generation(None, force=True)
    assert runtime.started.wait(1)

    cancelled_ids = broker.set_foreground_context("wps:paper-b")
    assert cancelled_ids
    _wait_until(lambda: bool(runtime.aborted))
    _wait_until(lambda: not controller._model_task_ids)

    _model, kind, payload = broker.submissions[-1]
    assert kind is ModelTaskKind.PROACTIVE
    assert set(payload) == {
        "requestId",
        "category",
        "hasCapture",
        "hasSource",
        "manualCapture",
        "anchorContinuation",
    }
    assert payload["manualCapture"] is False
    assert payload["anchorContinuation"] is False
    assert runtime.aborted == [LUNA_MODEL]
    assert all(broker.get(task_id).state is ModelTaskState.CANCELLED for task_id in cancelled_ids)
    controller.shutdown()


def test_cancelling_queued_companion_does_not_abort_explicit_model_owner(
    tmp_path,
) -> None:
    """Revoking a queued observation must not stop the active chat call."""

    _app = QCoreApplication.instance() or QCoreApplication([])
    broker = RecordingBroker()
    explicit = broker.submit(
        LUNA_MODEL,
        ModelTaskKind.EXPLICIT_CHAT_REPLY,
        {"requestId": "active-chat"},
    )
    assert explicit.state is ModelTaskState.RUNNING
    controller = CompanionController(
        Database(tmp_path / "queued-companion.db"),
        tmp_path,
        active=False,
        status_sink=lambda _value: None,
        move_to_box=lambda _value: None,
        model_broker=broker,
    )
    controller.runtime.shutdown()

    class QueuedRuntime:
        modality_status = {"checked": True, "imageModel": ""}

        def __init__(self) -> None:
            self.aborted: list[str] = []

        def generate(self, **_kwargs):
            raise AssertionError("queued companion must not enter the model")

        def abort_model(self, model_id: str) -> None:
            self.aborted.append(model_id)

        def shutdown(self) -> None:
            return None

    runtime = QueuedRuntime()
    controller.runtime = runtime
    try:
        assert controller._start_generation(None, force=True)
        _wait_until(
            lambda: bool(
                broker.status(LUNA_MODEL)["models"][LUNA_MODEL]["queued"]
            )
        )

        controller.setPaused(True)
        _wait_until(lambda: not controller._model_task_ids)

        assert runtime.aborted == []
        assert broker.get(explicit.id).state is ModelTaskState.RUNNING
    finally:
        broker.finish(explicit.id, result={"completed": True})
        controller.shutdown()


def test_companion_reply_and_archive_use_highest_and_lowest_luna_priorities(tmp_path) -> None:
    _app = QCoreApplication.instance() or QCoreApplication([])
    broker = RecordingBroker()
    controller = CompanionController(
        Database(tmp_path / "companion-priorities.db"),
        tmp_path,
        active=False,
        status_sink=lambda _value: None,
        move_to_box=lambda _value: None,
        model_broker=broker,
    )
    controller.runtime.shutdown()

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def reply(self, _bubble, _dialogue, _text):
            return "我听见了。"

        def propose_archive_one_pending(self):
            return {"proposal": True}

        def apply_archive_proposal(self, _proposal):
            return True

        def abort_model(self, _model_id):
            return None

        def shutdown(self):
            return None

    controller.runtime = Runtime()
    bubble = controller.engine.emit(
        category=ContentCategory.LORE,
        summary="盒中世界很安静。",
        force=True,
    )
    assert bubble is not None
    controller._bubble_object = bubble
    controller._bubble = {**bubble.to_mapping(), "busy": False}
    controller.reply(bubble.id, "这是不应进入任务元数据的回复")
    _wait_until(lambda: any(kind is ModelTaskKind.EXPLICIT_CHAT_REPLY for _, kind, _ in broker.submissions))
    _wait_until(lambda: not controller._model_task_ids)

    controller.activity.idle_provider = type(
        "IdleProvider", (), {"idle_seconds": lambda self: 60.0}
    )()
    controller._consider_archival()
    _wait_until(lambda: any(kind is ModelTaskKind.MEMORY_ARCHIVE for _, kind, _ in broker.submissions))
    _wait_until(lambda: not controller._model_task_ids)

    reply_payload = next(
        payload
        for _, kind, payload in broker.submissions
        if kind is ModelTaskKind.EXPLICIT_CHAT_REPLY
    )
    archive_payload = next(
        payload
        for _, kind, payload in broker.submissions
        if kind is ModelTaskKind.MEMORY_ARCHIVE
    )
    assert "不应进入" not in json.dumps(reply_payload, ensure_ascii=False)
    assert set(reply_payload) == {"requestId", "bubbleId"}
    assert set(archive_payload) == {"requestId"}
    controller.shutdown()


def test_cancelled_archive_proposal_is_not_applied_before_broker_commit(
    tmp_path, monkeypatch
) -> None:
    _app = QCoreApplication.instance() or QCoreApplication([])
    broker = ModelTaskBroker()
    controller = CompanionController(
        Database(tmp_path / "cancelled-archive.db"),
        tmp_path,
        active=False,
        status_sink=lambda _value: None,
        move_to_box=lambda _value: None,
        model_broker=broker,
    )
    controller.runtime.shutdown()
    commit_entered = threading.Event()
    release_commit = threading.Event()
    applied = threading.Event()
    original_commit = _BrokerTaskLease.commit

    def paused_commit(self, *, result=None):
        commit_entered.set()
        assert release_commit.wait(1.0)
        return original_commit(self, result=result)

    monkeypatch.setattr(_BrokerTaskLease, "commit", paused_commit)

    class Runtime:
        modality_status = {"checked": True, "imageModel": ""}

        def propose_archive_one_pending(self):
            return {"classification": "generated"}

        def apply_archive_proposal(self, _proposal):
            applied.set()
            return True

        def abort_model(self, _model_id):
            return None

        def shutdown(self):
            release_commit.set()

    controller.runtime = Runtime()
    controller.activity.idle_provider = type(
        "IdleProvider", (), {"idle_seconds": lambda self: 60.0}
    )()
    try:
        controller._consider_archival()
        assert commit_entered.wait(1.0)
        task_id = next(iter(controller._model_task_ids))
        broker.cancel(task_id, reason="cancel-after-propose-before-commit")
        release_commit.set()
        _wait_until(lambda: not controller._model_task_ids)

        assert broker.get(task_id).state is ModelTaskState.CANCELLED
        assert applied.is_set() is False
    finally:
        controller.shutdown()
