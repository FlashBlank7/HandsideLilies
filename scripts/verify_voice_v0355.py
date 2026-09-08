"""Optional real bridge check using synthetic text only, never private history."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lilies.core.database import Database
from lilies.core.model import ChatService


def main():
    with tempfile.TemporaryDirectory(prefix="lilies-voice-") as temporary:
        chat = ChatService(Database(Path(temporary) / "voice.db"))
        try:
            if not chat._gpt.ready:
                print(json.dumps({"tested": False, "reason": "subscription-unavailable"}))
                return 0
            for text in ("哈喽，今天不聊正事。", "为什么我总觉得存下论文，就好像已经读过一点了？来点有趣的想法，不要讲大道理。"):
                answer = chat._gpt.complete(chat._gpt_prompt(text), timeout=60)
                print(json.dumps({"tested": True, "syntheticInput": text,
                                  "model": chat._gpt.model, "reply": answer}, ensure_ascii=False), flush=True)
            return 0
        except RuntimeError:
            # Availability is not a writing-quality result. Do not publish a
            # canned reply or inspect/alter the official login to pass a probe.
            print(json.dumps({"tested": False, "reason": "subscription-account-or-transport-unavailable"}))
            return 0
        finally:
            chat.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
