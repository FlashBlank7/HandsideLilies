from lilies.core.codex_subscription import selection_prompt
from lilies.core.database import Database
from lilies.core.model import ChatService, IDENTITY_EXAMPLES
from lilies.core.voice import LILITH_VOICE


def test_subscription_uses_real_history_not_calibration_dialogue(tmp_path):
    chat = ChatService(Database(tmp_path / "voice.db"))
    try:
        prompt = chat._gpt_prompt("哈喽")
        assert LILITH_VOICE in prompt
        assert '"content": "哈喽"' in prompt
        for message in IDENTITY_EXAMPLES:
            assert message["content"] not in prompt
        assert "不能据此编造成功" in prompt
        assert "不是莉莉丝" in prompt
    finally:
        chat.shutdown()


def test_subscription_greeting_is_generated_not_a_fixed_identity_line(tmp_path):
    chat = ChatService(Database(tmp_path / "greeting.db"))
    calls = []

    class Gpt:
        ready = True
        running = False
        plan_type = "pro"
        def complete(self, prompt, timeout, on_delta):
            calls.append(prompt)
            on_delta("嗯，我在。")
            return "嗯，我在。"
        def stop(self): pass
        def abort(self): pass

    chat._gpt = Gpt()
    finished = []
    chat.responseFinished.connect(finished.append)
    try:
        chat.database.add_message(chat.conversation_id, "user", "哈喽")
        chat._chat_worker("哈喽")
        assert len(calls) == 1
        assert finished == ["嗯，我在。"]
    finally:
        chat.shutdown()


def test_paper_voice_does_not_weaken_selection_privacy():
    prompt = selection_prompt("causal inference")
    assert LILITH_VOICE in prompt
    assert "不要调用工具，不要读取文件，不要搜索网络" in prompt
    assert "完全独立" in prompt
    assert "长期记忆" in prompt
