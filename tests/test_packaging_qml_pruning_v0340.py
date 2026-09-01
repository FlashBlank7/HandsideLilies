from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import PySide6


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOK = PROJECT_ROOT / "packaging" / "hooks" / "hook-PySide6.QtQml.py"
SPEC = PROJECT_ROOT / "LiliesInTheBox.spec"


def _required_modules() -> tuple[str, ...]:
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "_REQUIRED_QML_MODULES"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        return tuple(str(item) for item in value)
    raise AssertionError("QML allowlist was not found")


def test_spec_uses_lilies_selective_qtqml_hook() -> None:
    source = SPEC.read_text(encoding="utf-8")

    assert "packaging' / 'hooks'" in source
    assert HOOK.is_file()


def test_spec_prunes_broad_qt_plugins_that_reintroduce_forbidden_dlls() -> None:
    source = SPEC.read_text(encoding="utf-8")

    assert "keep_lilies_runtime_binary" in source
    assert "pyside6/plugins/qmltooling/" in source
    assert "pyside6/plugins/imageformats/qpdf.dll" in source
    assert "pyside6/qt6pdf.dll" in source
    assert "pyside6/qt6quick3dutils.dll" in source
    assert "a.binaries = [" in source
    assert "if keep_lilies_runtime_binary(entry)" in source


def test_qml_hook_covers_every_application_import_family() -> None:
    modules = set(_required_modules())

    assert {
        "QtQuick",
        "QtQml",
        "QtMultimedia",
        "QtQuick/Controls",
        "QtQuick/Layouts",
        "QtQuick/Window",
        "QtQuick/Dialogs",
    }.issubset(modules)
    assert "QtQuick/Templates" in modules
    assert "QtQuick/Controls/Windows" in modules
    assert "QtQuick/Controls/Basic" in modules


def test_qml_hook_matches_qmlimportscanner_dependency_closure() -> None:
    package_root = Path(PySide6.__file__).resolve().parent
    scanner = package_root / (
        "qmlimportscanner.exe" if sys.platform == "win32" else "qmlimportscanner"
    )
    completed = subprocess.run(
        [
            str(scanner),
            "-rootPath",
            str(PROJECT_ROOT / "qml"),
            "-importPath",
            str(package_root / "qml"),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    scanned = {
        str(item["relativePath"]).replace("\\", "/")
        for item in json.loads(completed.stdout)
        if item.get("relativePath")
    }

    assert set(_required_modules()) == scanned


def test_qml_hook_excludes_large_unimported_feature_families() -> None:
    modules = _required_modules()
    forbidden = (
        "QtWebEngine",
        "QtQuick3D",
        "QtCharts",
        "QtGraphs",
        "QtDataVisualization",
        "QtLocation",
        "QtPdf",
        "QtWebSockets",
    )

    assert all(
        not module.startswith(prefix)
        for module in modules
        for prefix in forbidden
    )


def test_hook_fails_closed_when_a_required_module_is_missing() -> None:
    source = HOOK.read_text(encoding="utf-8")

    assert 'raise RuntimeError(f"Required QML module is missing:' in source
    assert 'raise RuntimeError(\n                f"Required QML module could not be collected:' in source
