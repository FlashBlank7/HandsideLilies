from __future__ import annotations

import os

import pytest

from lilies.core.terminal import run_terminal_command


@pytest.mark.skipif(os.name != "nt", reason="v0.1 terminal backend is Windows-only")
def test_terminal_command_is_noninteractive_and_captures_output():
    result = run_terminal_command("Write-Output lilies-terminal-test", timeout=5)
    assert result["exitCode"] == 0
    assert result["stdout"] == "lilies-terminal-test"
    assert result["timedOut"] is False


def test_terminal_command_rejects_empty_and_oversized_input():
    with pytest.raises(ValueError):
        run_terminal_command("   ")
    with pytest.raises(ValueError):
        run_terminal_command("x" * 1001)
