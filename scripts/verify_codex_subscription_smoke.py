from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from lilies.core.codex_subscription import CodexSubscriptionClient
from lilies.core.memory import MemoryService


class DiagnosticClient(CodexSubscriptionClient):
    """Expose only non-secret auth shape while exercising the real bridge."""

    account_type = ""
    plan_type_seen = ""
    thread_starts = 0
    dynamic_thread_starts = 0
    capability_rejections = 0

    def _rpc_locked(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        if method == "thread/start":
            self.thread_starts += 1
            if params.get("dynamicTools"):
                self.dynamic_thread_starts += 1
        try:
            result = super()._rpc_locked(method, params, timeout)
        except RuntimeError as exc:
            if "requires experimentalApi capability" in str(exc):
                self.capability_rejections += 1
            raise
        if method == "account/read":
            account = result.get("account") or {}
            self.account_type = str(account.get("type", ""))
            self.plan_type_seen = str(account.get("planType", ""))
        return result


def main() -> int:
    client = DiagnosticClient(Path(".codex-subscription-smoke-v0349"), max_output_chars=200)
    tool_spec = MemoryService.dynamic_tool_spec()
    nonce = secrets.token_hex(8)
    result: dict[str, Any] = {
        "ok": False,
        "accountType": "",
        "planType": "",
        "reply": "",
        "errorType": "",
        "error": "",
        "threadStarts": 0,
        "dynamicThreadStarts": 0,
        "capabilityRejections": 0,
        "toolCalls": 0,
        "toolShapeOk": False,
    }
    tool_calls = 0
    tool_shape_ok = False

    def tool_handler(
        name: str, arguments: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, str]:
        nonlocal tool_calls, tool_shape_ok
        tool_calls += 1
        tool_shape_ok = bool(
            name == "recall"
            and context.get("namespace") == "memory"
            and arguments.get("query") == nonce
            and arguments.get("partitionIds") == []
            and arguments.get("timeRange") == "all"
            and arguments.get("limit") == 1
        )
        if not tool_shape_ok:
            raise RuntimeError("production memory.recall namespace shape mismatch")
        return {"nonce": nonce}

    try:
        result["reply"] = client.complete(
            (
                "Call memory.recall exactly once with partitionIds=[], query='"
                + nonce
                + "', timeRange='all', limit=1. Read the nonce from that tool result, "
                "then reply with exactly CAPABILITY_OK:<nonce> and nothing else."
            ),
            timeout=90,
            dynamic_tools=[tool_spec],
            tool_handler=tool_handler,
        )
        result["ok"] = (
            result["reply"].strip() == f"CAPABILITY_OK:{nonce}"
            and client.thread_starts == 1
            and client.dynamic_thread_starts == 1
            and client.capability_rejections == 0
            and tool_calls == 1
            and tool_shape_ok
        )
    except Exception as exc:  # diagnostic boundary
        result["errorType"] = type(exc).__name__
        result["error"] = str(exc)[:500]
    finally:
        result["accountType"] = client.account_type
        result["planType"] = client.plan_type_seen
        result["threadStarts"] = client.thread_starts
        result["dynamicThreadStarts"] = client.dynamic_thread_starts
        result["capabilityRejections"] = client.capability_rejections
        result["toolCalls"] = tool_calls
        result["toolShapeOk"] = tool_shape_ok
        client.stop()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
