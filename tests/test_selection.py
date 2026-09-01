from __future__ import annotations

import json
from pathlib import Path

from lilies.core.codex_subscription import (
    CodexSubscriptionClient,
    chatgpt_auth_summary,
    find_codex_cli,
    reading_action_prompt,
    selection_prompt,
)
from lilies.core.database import Database
from lilies.core.reading import READING_ACTIONS, prepare_reading_request
from lilies.core.selection import READING_PROCESSES, SelectionService


def test_selection_prompt_is_explicitly_context_free() -> None:
    prompt = selection_prompt("Dropout randomly masks activations.")
    assert "Dropout randomly masks activations." in prompt
    assert "完全独立" in prompt
    assert "不要调用工具" in prompt
    assert "不要引用或猜测任何对话历史" in prompt


def test_every_selection_action_has_an_isolated_prompt() -> None:
    source = "The model minimizes L = L_task + lambda R."
    for action in READING_ACTIONS:
        question = "What does R mean here?" if action == "ask" else ""
        request = prepare_reading_request(source, action, question)
        prompt = reading_action_prompt(request)
        assert source in prompt
        assert "完全独立" in prompt
        assert "上次回答" in prompt
        assert "长期记忆" in prompt
        assert "不要调用工具" in prompt
        if action == "ask":
            assert question in prompt
            assert "<question>" in prompt
        else:
            assert "<question>" not in prompt


def test_follow_up_prompt_contains_only_this_question_and_source() -> None:
    prompt = selection_prompt("Ablation removes one component.", "ask", "Why do this?")
    assert "Ablation removes one component." in prompt
    assert "Why do this?" in prompt
    assert "<question>" in prompt
    assert "只根据原文回答本次问题" in prompt


def test_selection_actions_save_only_when_requested_except_terms(tmp_path: Path, monkeypatch) -> None:
    class ImmediateThread:
        def __init__(self, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    class FakeSubscription:
        ready = False
        available = False
        signed_in = False
        plan_type = ""

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        def reading_action(self, source: str, action: str, question: str = "") -> str:
            self.calls.append((source, action, question))
            return f"{action} answer"

        def stop(self) -> None:
            return None

    monkeypatch.setattr("lilies.core.selection.threading.Thread", ImmediateThread)
    database = Database(tmp_path / "lilies.db")
    service = SelectionService(database, active=False)
    fake = FakeSubscription()
    service._subscription = fake
    service._last_text = "Dropout randomly masks activations."
    service._bubble = {"visible": True, "x": 40, "y": 80, "busy": False, "error": False}

    service.request_action("translate")
    assert fake.calls[-1] == ("Dropout randomly masks activations.", "translate", "")
    assert service.bubble["action"] == "translate"
    assert service.bubble["ephemeral"] is True
    assert service.bubble["canSave"] is True
    assert database.reading_cards() == []

    saved_id = service.save_current_card()
    assert saved_id
    assert service.save_current_card() == saved_id
    assert database.reading_cards()[0]["kind"] == "translate"

    service.request_action("term")
    assert service.bubble["autoSaved"] is True
    assert service.bubble["savedCardId"]
    assert len(database.reading_cards()) == 2

    service.request_action("ask", "Why is it useful?")
    assert fake.calls[-1] == (
        "Dropout randomly masks activations.",
        "ask",
        "Why is it useful?",
    )
    assert service.bubble["question"] == "Why is it useful?"
    assert len(database.reading_cards()) == 2


def test_wps_reading_processes_are_monitored() -> None:
    assert {"wps.exe", "wpspdf.exe"} <= READING_PROCESSES


def test_chatgpt_auth_summary_never_returns_tokens(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "secret"}}),
        encoding="utf-8",
    )
    summary = chatgpt_auth_summary(tmp_path)
    assert summary == {"signedIn": True, "mode": "chatgpt"}
    assert "secret" not in repr(summary)


def test_find_codex_cli_uses_desktop_runtime_from_config(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime" / "codex.exe"
    runtime.parent.mkdir()
    runtime.write_bytes(b"test")
    (tmp_path / "config.toml").write_text(
        f"CODEX_CLI_PATH = '{runtime}'\n", encoding="utf-8"
    )
    monkeypatch.delenv("LILIES_CODEX_CLI", raising=False)
    assert find_codex_cli(home=tmp_path, local_app_data=tmp_path / "unused") == runtime


def test_turn_uses_completed_agent_message_as_authoritative(tmp_path: Path) -> None:
    client = CodexSubscriptionClient(tmp_path, executable=tmp_path / "missing.exe")
    client._pending.extend(
        [
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "delta": "流式草稿",
                },
            },
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread",
                    "turnId": "turn",
                    "item": {"type": "agentMessage", "text": "最终解释。"},
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread",
                    "turn": {"id": "turn", "status": "completed"},
                },
            },
        ]
    )
    assert (
        client._wait_for_turn_locked("thread", timeout=1, turn_id="turn")
        == "最终解释。"
    )
