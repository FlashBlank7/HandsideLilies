from __future__ import annotations

import pytest

from lilies.core.hotkeys import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, parse_hotkey


def test_parse_desktop_peek_hotkey() -> None:
    value = parse_hotkey("Ctrl + Alt + d")
    assert value.display == "Ctrl+Alt+D"
    assert value.virtual_key == ord("D")
    assert value.modifiers == MOD_CONTROL | MOD_ALT | MOD_NOREPEAT


def test_parse_function_hotkey_and_reject_unmodified_key() -> None:
    value = parse_hotkey("Shift+F12")
    assert value.display == "Shift+F12"
    assert value.modifiers == MOD_SHIFT | MOD_NOREPEAT
    with pytest.raises(ValueError):
        parse_hotkey("D")
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+Space")
