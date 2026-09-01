from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path


def emit(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    if os.name == "nt":
        try:
            import ctypes

            BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS
            )
        except OSError:
            pass
    model_path = Path(sys.argv[1]).resolve()
    if not (model_path / "model.safetensors").is_file():
        emit({"type": "error", "message": "本地 0.5B 模型文件不完整"})
        return 3
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

        emit({"type": "status", "message": "正在载入本机 Qwen2.5 0.5B…"})
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        tokenizer.truncation_side = "left"
        model = AutoModelForCausalLM.from_pretrained(
            str(model_path),
            local_files_only=True,
            torch_dtype=torch.float32,
        )
        model.eval()
        # Leave scheduling headroom for the 60 FPS desktop compositor.
        torch.set_num_threads(max(2, min(4, (os.cpu_count() or 8) // 3)))
        torch.set_num_interop_threads(1)
        emit({"type": "ready", "model": "Qwen2.5-0.5B-Instruct", "device": "cpu"})
    except Exception as exc:
        emit({"type": "error", "message": f"0.5B 模型载入失败：{exc}"})
        return 4

    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            emit({"type": "error", "message": "无效的模型请求"})
            continue
        if request.get("type") == "shutdown":
            return 0
        if request.get("type") != "chat":
            emit({"type": "error", "message": "未知模型请求"})
            continue
        try:
            messages = request.get("messages") or []
            prompt = "".join(
                f"<|im_start|>{str(message.get('role', 'user'))}\n{str(message.get('content', ''))}<|im_end|>\n"
                for message in messages
            ) + "<|im_start|>assistant\n"
            context_window = max(512, min(8192, int(request.get("context", 8192))))
            inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=context_window)
            streamer = TextIteratorStreamer(
                tokenizer,
                skip_prompt=True,
                skip_special_tokens=True,
                timeout=180,
            )
            generation = threading.Thread(
                target=model.generate,
                kwargs={
                    **inputs,
                    "streamer": streamer,
                    "max_new_tokens": int(request.get("maxNewTokens", 192)),
                    "do_sample": True,
                    "temperature": float(request.get("temperature", 0.70)),
                    "top_p": float(request.get("topP", 0.86)),
                    "repetition_penalty": 1.08,
                    "pad_token_id": tokenizer.eos_token_id,
                },
                daemon=True,
            )
            started = time.perf_counter()
            first_chunk_at = 0.0
            output_parts: list[str] = []
            generation.start()
            for text in streamer:
                if text:
                    if not first_chunk_at:
                        first_chunk_at = time.perf_counter()
                    output_parts.append(text)
                    emit({"type": "chunk", "text": text})
            generation.join(timeout=5)
            elapsed = max(0.001, time.perf_counter() - started)
            generated_tokens = len(tokenizer.encode("".join(output_parts), add_special_tokens=False))
            emit({
                "type": "done",
                "generatedTokens": generated_tokens,
                "generationSeconds": round(elapsed, 4),
                "firstTokenSeconds": round(first_chunk_at - started, 4) if first_chunk_at else None,
                "tokensPerSecond": round(generated_tokens / elapsed, 3),
            })
        except Exception as exc:
            emit({"type": "error", "message": f"生成失败：{exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
