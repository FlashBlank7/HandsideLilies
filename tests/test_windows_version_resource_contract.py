from __future__ import annotations

import ast
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    StringFileInfo,
    VSVersionInfo,
    VarFileInfo,
    load_version_info_from_text_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "LiliesInTheBox.spec"
VERSION_RESOURCE = PROJECT_ROOT / "packaging" / "windows_version_info.txt"


def _version_tuple(ms: int, ls: int) -> tuple[int, int, int, int]:
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def test_windows_version_resource_is_valid_and_complete() -> None:
    version_info = load_version_info_from_text_file(str(VERSION_RESOURCE))
    assert isinstance(version_info, VSVersionInfo)

    fixed = version_info.ffi
    assert _version_tuple(fixed.fileVersionMS, fixed.fileVersionLS) == (0, 3, 48, 0)
    assert _version_tuple(fixed.productVersionMS, fixed.productVersionLS) == (
        0,
        3,
        48,
        0,
    )
    assert fixed.fileOS == 0x40004
    assert fixed.fileType == 0x1

    string_info = next(
        child for child in version_info.kids if isinstance(child, StringFileInfo)
    )
    assert len(string_info.kids) == 1
    table = string_info.kids[0]
    assert table.name == "040904B0"
    strings = {entry.name: entry.val for entry in table.kids}
    assert strings == {
        "CompanyName": "Lilies in the box",
        "FileDescription": "Lilies in the box desktop companion",
        "FileVersion": "0.3.48.0",
        "InternalName": "LiliesInTheBox",
        "LegalCopyright": "Copyright (c) 2026 Lilies in the box",
        "OriginalFilename": "LiliesInTheBox.exe",
        "ProductName": "Lilies in the box",
        "ProductVersion": "0.3.48",
    }

    variable_info = next(
        child for child in version_info.kids if isinstance(child, VarFileInfo)
    )
    assert len(variable_info.kids) == 1
    assert variable_info.kids[0].name == "Translation"
    assert variable_info.kids[0].kids == [0x0409, 1200]

    # Exercise the same serializer used by PyInstaller before touching an EXE,
    # then parse it again to catch malformed lengths, padding or code-page data.
    raw = version_info.toRaw()
    assert raw
    round_trip = VSVersionInfo()
    round_trip.fromRaw(raw)
    assert round_trip.toRaw() == raw


def test_pyinstaller_spec_embeds_the_version_resource() -> None:
    source = SPEC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SPEC_PATH))
    exe_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "EXE"
    )
    keywords = {keyword.arg: keyword.value for keyword in exe_call.keywords}
    version_keyword = keywords.get("version")
    assert isinstance(version_keyword, ast.Call)
    assert isinstance(version_keyword.func, ast.Name)
    assert version_keyword.func.id == "str"
    assert len(version_keyword.args) == 1
    assert isinstance(version_keyword.args[0], ast.Name)
    assert version_keyword.args[0].id == "version_resource"

    assert "Path(SPECPATH) / 'packaging' / 'windows_version_info.txt'" in source
    assert "if not version_resource.is_file():" in source
    assert "raise FileNotFoundError" in source
