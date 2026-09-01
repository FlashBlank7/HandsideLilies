from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


MAX_COMMAND_LENGTH = 1000
MAX_OUTPUT_LENGTH = 8000


def run_terminal_command(command: str, timeout: int = 20) -> dict[str, Any]:
    """Run one explicitly confirmed, non-interactive PowerShell command."""

    clean = command.strip()
    if not clean:
        raise ValueError("command cannot be empty")
    if len(clean) > MAX_COMMAND_LENGTH:
        raise ValueError("command is too long")
    if "\x00" in clean:
        raise ValueError("command contains an invalid character")
    if os.name != "nt":
        raise OSError("terminal commands are currently available only on Windows")

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    prefix = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                prefix + clean,
            ],
            cwd=str(Path.home()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, min(int(timeout), 30)),
            creationflags=flags,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        truncated = len(stdout) > MAX_OUTPUT_LENGTH or len(stderr) > MAX_OUTPUT_LENGTH
        return {
            "command": clean,
            "exitCode": int(completed.returncode),
            "stdout": stdout[:MAX_OUTPUT_LENGTH],
            "stderr": stderr[:MAX_OUTPUT_LENGTH],
            "timedOut": False,
            "truncated": truncated,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return {
            "command": clean,
            "exitCode": None,
            "stdout": stdout[:MAX_OUTPUT_LENGTH],
            "stderr": stderr[:MAX_OUTPUT_LENGTH],
            "timedOut": True,
            "truncated": len(stdout) > MAX_OUTPUT_LENGTH or len(stderr) > MAX_OUTPUT_LENGTH,
        }
