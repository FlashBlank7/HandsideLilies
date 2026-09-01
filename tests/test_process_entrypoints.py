from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = (
    pytest.param(PROJECT_ROOT / "main.py", None, id="pyinstaller-main"),
    pytest.param(
        PROJECT_ROOT / "src" / "lilies" / "__main__.py",
        "lilies",
        id="python-module-main",
    ),
)


def test_pyinstaller_spec_uses_tested_process_entrypoint() -> None:
    spec = (PROJECT_ROOT / "LiliesInTheBox.spec").read_text("utf-8")

    assert "Analysis(\n    ['main.py']," in spec


def _execute_entrypoint(path: Path, *, module_name: str, package: str | None) -> None:
    namespace = {
        "__name__": module_name,
        "__package__": package,
        "__file__": str(path),
    }
    exec(compile(path.read_bytes(), str(path), "exec"), namespace)


@pytest.mark.parametrize(("path", "package"), ENTRYPOINTS)
def test_importing_entrypoint_does_not_start_application(
    path: Path,
    package: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    fake_app = ModuleType("lilies.app")
    fake_app.main = lambda: calls.append(None) or 23
    monkeypatch.setitem(sys.modules, "lilies.app", fake_app)

    _execute_entrypoint(
        path,
        module_name="lilies.entrypoint_import_probe",
        package=package,
    )

    assert calls == []


@pytest.mark.parametrize(("path", "package"), ENTRYPOINTS)
def test_executed_entrypoint_propagates_main_return_code(
    path: Path,
    package: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    fake_app = ModuleType("lilies.app")
    fake_app.main = lambda: calls.append(None) or 23
    monkeypatch.setitem(sys.modules, "lilies.app", fake_app)

    with pytest.raises(SystemExit) as exit_info:
        _execute_entrypoint(path, module_name="__main__", package=package)

    assert exit_info.value.code == 23
    assert calls == [None]
