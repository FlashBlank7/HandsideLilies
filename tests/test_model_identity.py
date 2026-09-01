from __future__ import annotations

from lilies.core.model import ChatService, PROMPT_REVISION, SYSTEM_PROMPT
from lilies.core.database import Database


def test_identity_critical_greetings_are_deterministic():
    expected = "……你好。我是莉莉丝。"
    for greeting in ("你好", "哈喽", "嗨！", "Hello", "晚上好"):
        assert ChatService._deterministic_identity_reply(greeting) == expected


def test_identity_question_never_assigns_lilith_name_to_user():
    reply = ChatService._deterministic_identity_reply("你是谁？")
    assert reply.startswith("我是莉莉丝。")
    assert "不会擅自给你取名" in reply


def test_small_model_identity_inversions_are_sanitized():
    assert ChatService._correct_identity_address("很高兴遇见你，莉莉丝。") == "……你好。我是莉莉丝。"
    assert ChatService._correct_identity_address("\\\n莉莉丝：你好。") == "你好。"
    assert "你叫莉莉丝" not in ChatService._correct_identity_address("你叫莉莉丝，我记住了。")


def test_identity_prompt_revision_forces_fresh_history_boundary():
    assert PROMPT_REVISION >= 3
    assert "正在与你对话的人" in SYSTEM_PROMPT
    assert "不是莉莉丝" in SYSTEM_PROMPT


def test_memory_candidates_stay_disabled_and_reviewed_preferences_use_recall(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)
    assert chat._propose_memory_candidate("我喜欢安静的白色界面")
    cards = database.memory_cards()
    assert len(cards) == 1
    assert cards[0]["enabled"] == 0
    prompt = chat._messages("继续")
    assert "安静的白色界面" not in prompt[0]["content"]
    database.save_memory(
        cards[0]["title"], cards[0]["content"], cards[0]["category"], cards[0]["memory_id"], True
    )
    assert "安静的白色界面" not in chat._messages("继续")[0]["content"]
    recalled = chat.memory.recall(
        partition_ids=["preferences"],
        query="安静的白色界面",
        time_range="all",
        limit=6,
        turn_id="reviewed-preference",
    )
    assert "安静的白色界面" in recalled["snippets"][0]["content"]
    database.delete_memory(cards[0]["memory_id"])
    assert "安静的白色界面" not in chat._messages("继续")[0]["content"]
    chat.shutdown()


def test_enabled_name_memory_is_used_for_greeting_and_user_identity(tmp_path):
    database = Database(tmp_path / "lilies.db")
    database.save_memory("称呼", "主人的名字：七秒", "事实", enabled=True)
    chat = ChatService(database)
    assert chat._deterministic_memory_reply("你好") == "……你好，七秒。我是莉莉丝。"
    assert chat._deterministic_memory_reply("你知道我是谁吗？") == "……知道。你是七秒，是打开盒子、发现我的人。"
    assert chat._deterministic_memory_reply("你还记得我吗？") == "记得。你希望我称呼你为七秒。"
    assert "必须直接采用" in chat._messages("我们继续")[0]["content"]
    chat.shutdown()


def test_disabled_name_memory_is_not_used(tmp_path):
    database = Database(tmp_path / "lilies.db")
    database.save_memory("称呼", "我叫七秒", "事实", enabled=False)
    chat = ChatService(database)
    assert chat._deterministic_memory_reply("你知道我是谁吗？") == ""
    chat.shutdown()


def test_gpt_prompt_injects_pinned_identity_and_local_history(tmp_path):
    database = Database(tmp_path / "lilies.db")
    database.save_memory("称呼", "主人的名字：七秒", "事实", enabled=True)
    chat = ChatService(database)
    database.add_message(chat.conversation_id, "user", "我在读一篇生物学论文")
    database.add_message(chat.conversation_id, "assistant", "我记住了。")
    database.add_message(chat.conversation_id, "user", "继续聊刚才的内容")

    prompt = chat._gpt_prompt("继续聊刚才的内容")

    assert "主人的名字：七秒" in prompt
    assert "我在读一篇生物学论文" in prompt
    assert "继续聊刚才的内容" in prompt
    assert "只输出莉莉丝" in prompt
    chat.shutdown()


def test_gpt_is_primary_chat_path_and_streams_reply(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class FakeGpt:
        ready = True
        running = False
        plan_type = "pro"

        def complete(self, prompt, timeout, on_delta):
            assert "聊聊今天" in prompt
            on_delta("今天很安静。")
            return "今天很安静。"

        def abort(self):
            pass

        def stop(self):
            pass

    chat._gpt = FakeGpt()
    chunks = []
    finished = []
    chat.chunk.connect(chunks.append)
    chat.responseFinished.connect(finished.append)
    database.add_message(chat.conversation_id, "user", "聊聊今天")

    chat._chat_worker("聊聊今天")

    assert chunks == ["今天很安静。"]
    assert finished == ["今天很安静。"]
    assert chat._last_metrics["model"] == "gpt-5.6-terra"
    assert database.recent_messages(chat.conversation_id, 2)[-1]["content"] == "今天很安静。"
    chat.shutdown()


def test_rule_router_uses_only_registered_component_calls(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, component_id, action_id, payload, **kwargs):
            self.calls.append((component_id, action_id, payload, kwargs.get("origin")))
            if (component_id, action_id) == ("desktop-icons", "list"):
                return {"auditId": "1234567890", "result": [{"name": "画板"}]}
            return {"auditId": "1234567890", "result": {}}

    registry = Registry()
    chat.bind_registry(registry)
    assert "画板" in chat._route_simple_tool("桌面有什么")
    assert registry.calls == [("desktop-icons", "list", {"query": ""}, "model")]
    chat.shutdown()


def test_natural_language_app_request_launches_registered_application(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, component_id, action_id, payload, **kwargs):
            self.calls.append((component_id, action_id, payload, kwargs.get("origin")))
            if (component_id, action_id) == ("app-launcher", "search"):
                return {
                    "auditId": "1234567890",
                    "result": [{"name": "WPS Office", "path": r"C:\Apps\WPS Office.lnk"}],
                }
            return {"auditId": "1234567890", "result": {"opened": payload.get("path")}}

    registry = Registry()
    chat.bind_registry(registry)
    for request in ("打开WPS", "我要打开 wps", "帮我启动一下 WPS", "把 WPS 给我打开"):
        registry.calls.clear()
        assert chat._route_simple_tool(request) == "已打开 WPS Office"
        assert registry.calls == [
            ("app-launcher", "search", {"query": "WPS" if "WPS" in request else "wps"}, "model"),
            ("app-launcher", "open", {"path": r"C:\Apps\WPS Office.lnk"}, "model"),
        ]
    chat.shutdown()


def test_ambiguous_application_request_asks_for_full_name(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, component_id, action_id, payload, **kwargs):
            self.calls.append((component_id, action_id))
            return {
                "auditId": "1234567890",
                "result": [
                    {"name": "Office Alpha", "path": r"C:\Apps\Alpha.lnk"},
                    {"name": "Office Beta", "path": r"C:\Apps\Beta.lnk"},
                ],
            }

    registry = Registry()
    chat.bind_registry(registry)
    reply = chat._route_simple_tool("打开 Office")
    assert "多个可能的应用" in reply
    assert registry.calls == [("app-launcher", "search")]
    chat.shutdown()


def test_file_request_falls_back_to_registered_filesystem_component(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, component_id, action_id, payload, **kwargs):
            self.calls.append((component_id, action_id, payload))
            if (component_id, action_id) == ("app-launcher", "search"):
                result = []
            elif (component_id, action_id) == ("filesystem", "search"):
                result = [{"name": "研究报告", "path": r"C:\Docs\研究报告.pdf", "kind": "file"}]
            else:
                result = {"opened": payload.get("path")}
            return {"auditId": "1234567890", "result": result}

    registry = Registry()
    chat.bind_registry(registry)
    assert chat._route_simple_tool("打开研究报告.pdf") == "已打开文件 研究报告"
    assert registry.calls == [
        ("app-launcher", "search", {"query": "研究报告.pdf"}),
        ("filesystem", "search", {"query": "研究报告.pdf"}),
        ("filesystem", "open", {"path": r"C:\Docs\研究报告.pdf"}),
    ]
    chat.shutdown()


def test_web_and_terminal_requests_require_explicit_syntax():
    assert ChatService._website_request("打开 GitHub") == "https://github.com/"
    assert ChatService._website_request("访问 example.com") == "https://example.com"
    assert ChatService._website_request("上网搜索 Lilies in the box").startswith("https://www.bing.com/search?q=")
    assert ChatService._website_request("打开 WPS") == ""
    assert ChatService._terminal_command("执行命令：Get-Date") == "Get-Date"
    assert ChatService._terminal_command("PowerShell: whoami") == "whoami"
    assert ChatService._terminal_command("帮我运行 ipconfig") == ""


def test_explicit_terminal_request_is_refused_without_invoking_a_component(tmp_path):
    database = Database(tmp_path / "lilies.db")
    chat = ChatService(database)

    class Registry:
        def __init__(self):
            self.calls = []

        def invoke(self, component_id, action_id, payload, **kwargs):
            self.calls.append((component_id, action_id, payload))
            return {
                "auditId": "1234567890",
                "result": {
                    "exitCode": 0, "stdout": "lilies", "stderr": "",
                    "timedOut": False, "truncated": False,
                },
            }

    registry = Registry()
    chat.bind_registry(registry)
    reply = chat._route_simple_tool("执行命令：Write-Output lilies")
    assert "不提供任意终端命令" in reply
    assert "已登记" in reply
    assert registry.calls == []
    chat.shutdown()
