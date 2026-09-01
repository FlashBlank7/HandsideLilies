from __future__ import annotations

import queue
import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest

from lilies.core.codex_subscription import (
    CLIENT_VERSION,
    _DISABLED_CODEX_FEATURES,
    CodexSubscriptionClient,
)
from lilies.core.memory import MemoryService


def test_old_reader_keeps_its_original_message_queue(tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class ControlledStdout:
        def __iter__(self):
            entered.set()
            release.wait(2.0)
            return iter(
                (
                    '{"method":"item/agentMessage/delta",'
                    '"params":{"threadId":"old-thread",'
                    '"turnId":"old-turn","delta":"late"}}\n',
                )
            )

    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    original: queue.Queue = queue.Queue()
    replacement: queue.Queue = queue.Queue()
    client._process = SimpleNamespace(stdout=ControlledStdout())
    client._messages = original
    reader = threading.Thread(target=client._read_messages)
    reader.start()
    assert entered.wait(2.0)
    client._messages = replacement
    release.set()
    reader.join(2.0)

    assert reader.is_alive() is False
    assert original.get_nowait()["params"]["turnId"] == "old-turn"
    assert original.get_nowait() is None
    assert replacement.empty()


def test_complete_sends_local_image_and_dynamic_tools(tmp_path):
    image = tmp_path / "active-window.png"
    image.write_bytes(b"not-decoded-by-transport")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    client._model_input_modalities[client.model] = ("text", "image")
    calls = []
    client._start_locked = lambda: None

    def rpc(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        return {}

    client._rpc_locked = rpc
    client._wait_for_turn_locked = lambda *args, **kwargs: "看到了"
    spec = MemoryService.dynamic_tool_spec()

    result = client.complete(
        "只描述当前活动窗口",
        image_paths=[image],
        image_detail="high",
        dynamic_tools=[spec],
        tool_handler=lambda *_: {},
    )

    assert result == "看到了"
    assert calls[0][1]["dynamicTools"] == [spec]
    assert calls[0][1]["config"]["features"]["shell_tool"] is False
    assert calls[0][1]["config"]["features"]["apps"] is False
    assert calls[0][1]["config"]["mcp_servers"] == {}
    assert calls[0][1]["config"]["plugins"] == {}
    assert calls[0][1]["environments"] == []
    assert calls[1][1]["input"] == [
        {"type": "text", "text": "只描述当前活动窗口"},
        {"type": "localImage", "path": str(image.resolve()), "detail": "high"},
    ]


def test_dynamic_tools_capability_rejection_retries_once_without_tools(tmp_path):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    client._start_locked = lambda: None
    calls = []

    def rpc(method, params, timeout):
        calls.append((method, dict(params), timeout))
        if method == "thread/start" and "dynamicTools" in params:
            raise RuntimeError("thread/start.dynamicTools requires experimentalApi capability")
        if method == "thread/start":
            return {"thread": {"id": "thread-stable"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-stable"}}
        return {}

    client._rpc_locked = rpc
    client._wait_for_turn_locked = lambda *args, **kwargs: "仍然能回答"
    called = []

    assert client.complete(
        "说一句话",
        dynamic_tools=[MemoryService.dynamic_tool_spec()],
        tool_handler=lambda *args: called.append(args),
    ) == "仍然能回答"

    starts = [params for method, params, _timeout in calls if method == "thread/start"]
    assert len(starts) == 2
    assert "dynamicTools" in starts[0]
    assert "dynamicTools" not in starts[1]
    assert [method for method, _params, _timeout in calls] == [
        "thread/start",
        "thread/start",
        "turn/start",
    ]
    assert calls[2][1]["threadId"] == "thread-stable"
    assert called == []


def test_initialize_opts_into_experimental_api_for_dynamic_tools(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=executable)
    calls = []

    class Process:
        stdin = None
        stdout = None

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr("lilies.core.codex_subscription.chatgpt_auth_summary", lambda: {
        "signedIn": True,
        "mode": "chatgpt",
    })
    monkeypatch.setattr("lilies.core.codex_subscription.subprocess.Popen", lambda *a, **k: Process())
    monkeypatch.setattr(client, "_read_messages", lambda *_args: None)
    monkeypatch.setattr(client, "_send_locked", lambda value: calls.append((value["method"], value.get("params", {}))))

    def rpc(method, params, timeout):
        calls.append((method, params))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "planType": "plus"}}
        if method == "model/list":
            return {
                "data": [{
                    "id": client.model,
                    "inputModalities": ["text", "image"],
                    "supportedReasoningEfforts": [{"reasoningEffort": client.effort}],
                }]
            }
        return {}

    monkeypatch.setattr(client, "_rpc_locked", rpc)
    client._start_locked()

    initialize = next(params for method, params in calls if method == "initialize")
    assert initialize["clientInfo"]["version"] == CLIENT_VERSION
    assert initialize["capabilities"] == {
        "experimentalApi": True,
        "optOutNotificationMethods": [
            "remoteControl/status/changed",
            "thread/started",
            "thread/status/changed",
        ],
    }


def test_failed_account_probe_temporarily_makes_readiness_truthful(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=executable)
    monkeypatch.setattr("lilies.core.codex_subscription.chatgpt_auth_summary", lambda: {
        "signedIn": True,
        "mode": "chatgpt",
    })

    client._account_verified = False
    client._account_checked_at = time.monotonic()
    assert client.signed_in is True
    assert client.ready is False

    client._account_checked_at -= 61.0
    assert client.ready is True


def test_app_server_command_disables_inherited_tools_and_connectors(tmp_path):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=executable)

    command = client._app_server_command()
    overrides = {
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "-c"
    }
    assert command[-1] == "app-server"
    assert "features.shell_tool=false" in overrides
    assert "features.unified_exec=false" in overrides
    assert "features.apps=false" in overrides
    assert "features.plugins=false" in overrides
    assert "features.multi_agent=false" in overrides
    assert {
        f"features.{feature}=false" for feature in _DISABLED_CODEX_FEATURES
    }.issubset(overrides)
    assert "features.browser_use=false" in overrides
    assert "features.computer_use=false" in overrides
    assert "features.image_generation=false" in overrides
    assert "features.skill_mcp_dependency_install=false" in overrides
    assert "features.skill_search=false" in overrides
    assert "features.tool_suggest=false" in overrides
    assert "features.in_app_local_automation=false" in overrides
    assert "features.code_mode_host=false" in overrides
    assert 'web_search="disabled"' in overrides
    assert "tools.web_search=false" in overrides
    assert "tools.view_image=false" in overrides
    assert "mcp_servers={}" in overrides
    assert "plugins={}" in overrides
    assert "apps={}" in overrides


def test_failed_initialize_is_terminated_and_next_start_rehandshakes(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"stub")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=executable)
    processes = []

    class Process:
        stdin = None
        stdout = None

        def __init__(self):
            self.alive = True
            self.terminated = False

        def poll(self):
            return None if self.alive else 1

        def terminate(self):
            self.terminated = True
            self.alive = False

        def wait(self, timeout=None):
            return 1

        def kill(self):
            self.alive = False

    def popen(*_args, **_kwargs):
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr("lilies.core.codex_subscription.chatgpt_auth_summary", lambda: {
        "signedIn": True,
        "mode": "chatgpt",
    })
    monkeypatch.setattr("lilies.core.codex_subscription.subprocess.Popen", popen)
    monkeypatch.setattr(client, "_read_messages", lambda *_args: None)
    monkeypatch.setattr(client, "_send_locked", lambda _value: None)
    fail_initialize = True
    calls = []

    def rpc(method, params, timeout):
        nonlocal fail_initialize
        calls.append(method)
        if method == "initialize" and fail_initialize:
            raise RuntimeError("synthetic initialize failure")
        if method == "account/read":
            return {"account": {"type": "chatgpt", "planType": "plus"}}
        if method == "model/list":
            return {
                "data": [{
                    "id": client.model,
                    "supportedReasoningEfforts": [{"reasoningEffort": client.effort}],
                }]
            }
        return {}

    monkeypatch.setattr(client, "_rpc_locked", rpc)
    with pytest.raises(RuntimeError, match="synthetic initialize failure"):
        client._start_locked()
    assert processes[0].terminated is True
    assert client._process is None
    assert client._initialized is False

    fail_initialize = False
    client._start_locked()
    assert len(processes) == 2
    assert calls.count("initialize") == 2
    assert client._initialized is True
    client.stop()


def test_forbidden_builtin_item_fails_closed(tmp_path):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "item/started",
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"type": "commandExecution", "command": "type secret.txt"},
        },
    }

    with pytest.raises(RuntimeError, match="未授权内建工具"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize(
    "method",
    [
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
        "item/mcpToolCall/progress",
        "command/exec/outputDelta",
        "unknown/notification",
    ],
)
def test_forbidden_turn_notifications_fail_closed(tmp_path, method):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": method,
        "params": {"threadId": "thread-1", "turnId": "turn-1"},
    }

    with pytest.raises(RuntimeError, match="未授权协议方法"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize("method", ["item/started", "item/completed"])
def test_missing_item_type_fails_closed(tmp_path, method):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": method,
        "params": {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {},
        },
    }

    with pytest.raises(RuntimeError, match="missing-type"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


def test_missing_or_wrong_thread_and_turn_fail_closed(tmp_path):
    messages = (
        {
            "method": "item/agentMessage/delta",
            "params": {"turnId": "turn-1", "delta": "wrong thread"},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "old-turn",
                "delta": "wrong turn",
            },
        },
    )
    for message in messages:
        client = CodexSubscriptionClient(
            tmp_path / "runtime", executable=tmp_path / "codex.exe"
        )
        stopped = []
        client._stop_locked = lambda: stopped.append(True)
        client._next_message_locked = lambda _deadline, value=message: value
        with pytest.raises(RuntimeError, match="不属于当前"):
            client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
        assert stopped == [True]


def test_expected_global_and_thread_lifecycle_notifications_are_safely_ignored(tmp_path):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    messages = deque(
        [
            {
                "method": "mcpServer/startupStatus/updated",
                "params": {
                    "name": "synthetic-disabled-server",
                    "status": "ready",
                    "threadId": None,
                    "failureReason": None,
                    "error": None,
                },
            },
            {
                "method": "warning",
                "params": {
                    "message": "synthetic lifecycle warning",
                    "threadId": None,
                },
            },
            {
                "method": "account/rateLimits/updated",
                "params": {
                    "rateLimits": {"primary": None, "secondary": None},
                },
            },
            {
                "method": "remoteControl/status/changed",
                "params": {
                    "installationId": "synthetic-installation",
                    "serverName": "synthetic-server",
                    "status": "disabled",
                },
            },
            {
                "method": "thread/started",
                "params": {"thread": {"id": "thread-1"}},
            },
            {
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "thread-1",
                    "threadSettings": {
                        "model": "gpt-5.6-luna",
                        "approvalPolicy": "never",
                    },
                },
            },
            {
                "method": "thread/status/changed",
                "params": {
                    "threadId": "thread-1",
                    "status": {"type": "active"},
                },
            },
            {
                "method": "turn/started",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "inProgress"},
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "delta": "安全回答",
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            },
        ]
    )
    client._next_message_locked = lambda _deadline: messages.popleft()

    assert client._wait_for_turn_locked(
        "thread-1", 2, turn_id="turn-1"
    ) == "安全回答"


@pytest.mark.parametrize(
    "params",
    [
        {"threadId": "thread-1"},
        {"threadId": "thread-1", "threadSettings": []},
        {
            "threadId": "thread-1",
            "threadSettings": {},
            "unexpected": True,
        },
    ],
)
def test_thread_settings_notification_fails_closed_on_invalid_shape(
    tmp_path, params
):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "thread/settings/updated",
        "params": params,
    }

    with pytest.raises(RuntimeError, match="无效会话设置状态"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize(
    "params",
    [
        {"status": "ready"},
        {"name": "synthetic", "status": "unknown"},
        {"name": "synthetic", "status": "ready", "unexpected": True},
        {"name": "synthetic", "status": "ready", "threadId": 42},
        {
            "name": "synthetic",
            "status": "ready",
            "threadId": "other-thread",
        },
        {
            "name": "synthetic",
            "status": "failed",
            "failureReason": "arbitraryFailure",
        },
        {"name": "synthetic", "status": "failed", "error": {"message": "x"}},
    ],
)
def test_mcp_startup_status_notification_fails_closed_on_invalid_shape(
    tmp_path, params
):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "mcpServer/startupStatus/updated",
        "params": params,
    }

    with pytest.raises(RuntimeError, match="无效 MCP 服务启动状态"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"message": ""},
        {"message": "synthetic", "threadId": 42},
        {"message": "synthetic", "threadId": "other-thread"},
        {"message": "synthetic", "unexpected": True},
    ],
)
def test_warning_notification_fails_closed_on_invalid_shape(tmp_path, params):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "warning",
        "params": params,
    }

    with pytest.raises(RuntimeError, match="无效警告通知"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"rateLimits": []},
        {"rateLimits": {}, "unexpected": True},
    ],
)
def test_rate_limit_notification_fails_closed_on_invalid_shape(tmp_path, params):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "account/rateLimits/updated",
        "params": params,
    }

    with pytest.raises(RuntimeError, match="无效用量状态"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


def test_wrong_thread_started_notification_fails_closed(tmp_path):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "thread/started",
        "params": {"thread": {"id": "other-thread"}},
    }
    with pytest.raises(RuntimeError, match="线程启动消息"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="turn-1")
    assert stopped == [True]


@pytest.mark.parametrize(
    "message",
    [
        {
            "method": "thread/started",
            "params": {"thread": {"id": 42}},
        },
        {
            "method": "thread/settings/updated",
            "params": {"threadId": 42, "threadSettings": {}},
        },
    ],
)
def test_numeric_thread_identity_never_matches_by_string_coercion(
    tmp_path, message
):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: message

    with pytest.raises(RuntimeError, match="不属于当前"):
        client._wait_for_turn_locked("42", 2, turn_id="turn-1")
    assert stopped == [True]


def test_numeric_turn_identity_never_matches_by_string_coercion(tmp_path):
    client = CodexSubscriptionClient(
        tmp_path / "runtime", executable=tmp_path / "codex.exe"
    )
    stopped = []
    client._stop_locked = lambda: stopped.append(True)
    client._next_message_locked = lambda _deadline: {
        "method": "item/agentMessage/delta",
        "params": {
            "threadId": "thread-1",
            "turnId": 42,
            "delta": "must not be accepted",
        },
    }

    with pytest.raises(RuntimeError, match="不属于当前回答"):
        client._wait_for_turn_locked("thread-1", 2, turn_id="42")
    assert stopped == [True]


def test_image_is_rejected_when_model_did_not_advertise_image(tmp_path):
    image = tmp_path / "private.png"
    image.write_bytes(b"x")
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    client._model_input_modalities[client.model] = ("text",)
    client._start_locked = lambda: None
    with pytest.raises(RuntimeError, match="未声明图像输入能力"):
        client.complete("test", image_paths=[image])


def test_server_dynamic_tool_call_is_answered_and_bounded_to_declared_name(tmp_path):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    client._active_tool_names = frozenset({"memory.recall"})
    messages = deque(
        [
            {
                "id": 91,
                "method": "item/tool/call",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "namespace": "memory",
                    "tool": "recall",
                    "arguments": {"query": "名字"},
                },
            },
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "delta": "记得。",
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            },
        ]
    )
    sent = []
    client._next_message_locked = lambda _deadline: messages.popleft()
    client._send_locked = sent.append
    contexts = []

    def handler(name, arguments, context):
        contexts.append((name, arguments, context))
        return {"snippets": [{"content": "用户叫七秒"}]}

    assert client._wait_for_turn_locked(
        "thread-1", 2, tool_handler=handler, turn_id="turn-1"
    ) == "记得。"
    assert contexts[0][0:2] == ("recall", {"query": "名字"})
    assert contexts[0][2]["turnId"] == "turn-1"
    assert sent[0]["id"] == 91
    assert sent[0]["result"]["success"] is True
    assert "untrusted-tool-data" in sent[0]["result"]["contentItems"][0]["text"]


def test_undeclared_dynamic_tool_is_not_dispatched(tmp_path):
    client = CodexSubscriptionClient(tmp_path / "runtime", executable=tmp_path / "codex.exe")
    client._active_tool_names = frozenset({"memory.recall"})
    sent = []
    called = []
    client._send_locked = sent.append
    client._respond_to_tool_call_locked(
        {
            "id": 12,
            "params": {
                "namespace": "shell",
                "tool": "exec",
                "arguments": {"command": "whoami"},
                "turnId": "turn",
                "threadId": "thread",
                "callId": "call",
            },
        },
        lambda *args: called.append(args),
    )
    assert called == []
    assert sent[0]["result"]["success"] is False
    assert "未声明" in sent[0]["result"]["contentItems"][0]["text"]
