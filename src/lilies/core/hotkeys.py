from __future__ import annotations

import ctypes
import os
import re
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


@dataclass(frozen=True)
class ParsedHotkey:
    modifiers: int
    virtual_key: int
    display: str


def parse_hotkey(value: str) -> ParsedHotkey:
    parts = [part.strip().casefold() for part in re.split(r"\s*\+\s*", value) if part.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少需要一个修饰键和一个按键")
    modifiers = 0
    display_modifiers: list[str] = []
    aliases = {
        "ctrl": (MOD_CONTROL, "Ctrl"),
        "control": (MOD_CONTROL, "Ctrl"),
        "alt": (MOD_ALT, "Alt"),
        "shift": (MOD_SHIFT, "Shift"),
        "win": (MOD_WIN, "Win"),
        "windows": (MOD_WIN, "Win"),
    }
    key_parts: list[str] = []
    for part in parts:
        if part in aliases:
            bit, label = aliases[part]
            if not modifiers & bit:
                modifiers |= bit
                display_modifiers.append(label)
        else:
            key_parts.append(part)
    if not modifiers or len(key_parts) != 1:
        raise ValueError("快捷键格式应类似 Ctrl+Alt+D")
    key = key_parts[0]
    if len(key) == 1 and (key.isascii() and key.isalnum()):
        virtual_key = ord(key.upper())
        display_key = key.upper()
    elif re.fullmatch(r"f(?:[1-9]|1[0-9]|2[0-4])", key):
        number = int(key[1:])
        virtual_key = 0x6F + number
        display_key = f"F{number}"
    else:
        raise ValueError("主按键只支持字母、数字或 F1–F24")
    return ParsedHotkey(modifiers | MOD_NOREPEAT, virtual_key, "+".join([*display_modifiers, display_key]))


class GlobalHotkey:
    """Register one Windows hotkey on a private message-loop thread."""

    def __init__(self, callback: Callable[[], None], hotkey_id: int = 0x4C44) -> None:
        self.callback = callback
        self.hotkey_id = hotkey_id
        self.thread: threading.Thread | None = None
        self.thread_id = 0
        self.sequence = ""
        self.registered = False
        self.error = ""
        self._ready = threading.Event()

    def start(self, sequence: str) -> bool:
        parsed = parse_hotkey(sequence)
        self.stop()
        self._ready = threading.Event()
        self.sequence = parsed.display
        self.error = ""

        if os.name != "nt":
            self.error = "global hotkeys are only available on Windows"
            return False

        def run() -> None:
            user32 = ctypes.windll.user32
            self.thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
            self.registered = bool(
                user32.RegisterHotKey(
                    None,
                    self.hotkey_id,
                    parsed.modifiers,
                    parsed.virtual_key,
                )
            )
            if not self.registered:
                self.error = f"快捷键 {parsed.display} 已被其他程序占用"
                self._ready.set()
                return
            self._ready.set()
            message = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                    if message.message == WM_HOTKEY and int(message.wParam) == self.hotkey_id:
                        self.callback()
            finally:
                user32.UnregisterHotKey(None, self.hotkey_id)
                self.registered = False

        self.thread = threading.Thread(target=run, name="lilies-desktop-peek-hotkey", daemon=True)
        self.thread.start()
        self._ready.wait(timeout=2)
        return self.registered

    def stop(self) -> None:
        thread = self.thread
        if os.name == "nt" and self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.thread = None
        self.thread_id = 0
        self.registered = False


__all__ = ["GlobalHotkey", "ParsedHotkey", "parse_hotkey"]
