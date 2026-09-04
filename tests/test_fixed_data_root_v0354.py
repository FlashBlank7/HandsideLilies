from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import lilies.app as app_module
import lilies.backend as backend_module
import lilies.paths as paths_module
from lilies.backend import Backend
from lilies.paths import (
    DataRootPurpose,
    DataRootUnavailableError,
    configure_qt_cache_environment,
    data_root,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_normal_windows_data_root_rejects_external_environment_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = tmp_path / "untrusted-local-app-data" / "Lilies"
    monkeypatch.setenv("LILIES_DATA_DIR", str(attempted))

    with pytest.raises(DataRootUnavailableError) as raised:
        data_root()

    assert raised.value.path == attempted.resolve()
    assert "not permitted during normal startup" in str(raised.value)
    assert not attempted.exists()


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_backend_normal_mode_cannot_bypass_fixed_root_with_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted = tmp_path / "production-bypass"
    monkeypatch.setenv("LILIES_DATA_DIR", str(attempted))

    with pytest.raises(DataRootUnavailableError):
        Backend(smoke=False, force_compact=True)

    assert not attempted.exists()


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
@pytest.mark.parametrize("override", [False, True])
def test_normal_root_uses_canonical_fixed_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: bool,
) -> None:
    fixed = tmp_path / "fixed-private-data"
    monkeypatch.setattr(paths_module, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    if override:
        monkeypatch.setenv("LILIES_DATA_DIR", str(fixed).swapcase())
    else:
        monkeypatch.delenv("LILIES_DATA_DIR", raising=False)

    assert data_root() == fixed.resolve()
    assert fixed.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_matching_override_does_not_skip_legacy_migration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = tmp_path / "fixed-private-data"
    legacy = tmp_path / "legacy-private-data"
    calls: list[tuple[Path, Path]] = []

    class _StopAfterMigrationCheck(Exception):
        pass

    def prepare(destination: Path, source: Path) -> None:
        calls.append((destination, source))
        raise _StopAfterMigrationCheck

    monkeypatch.setattr(paths_module, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    monkeypatch.setattr(backend_module, "legacy_data_root", lambda: legacy)
    monkeypatch.setattr(backend_module, "prepare_private_data", prepare)
    monkeypatch.setenv("LILIES_DATA_DIR", str(fixed))

    with pytest.raises(_StopAfterMigrationCheck):
        Backend(smoke=False, force_compact=True)

    assert calls == [(fixed.resolve(), legacy)]
    assert not legacy.exists()


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_unavailable_fixed_root_does_not_fall_back_to_local_app_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = tmp_path / "unavailable-private-data"
    local_app_data = tmp_path / "must-stay-empty"
    attempted: list[Path] = []

    def reject_mkdir(path: Path, *_args: object, **_kwargs: object) -> None:
        attempted.append(path)
        raise PermissionError("test storage unavailable")

    monkeypatch.setattr(paths_module, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "mkdir", reject_mkdir)

    with pytest.raises(DataRootUnavailableError) as raised:
        data_root()

    assert raised.value.path == fixed.resolve()
    assert attempted == [fixed.resolve()]
    assert not local_app_data.exists()


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_fixed_root_redirect_to_system_drive_is_rejected_before_mkdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No filesystem operations are allowed in this test; use fixed drive
    # identities even when another developer's pytest temp directory is C:.
    fixed = Path(r"F:\Lilies-fixed-root-redirection-test")
    redirected = Path(r"C:\Lilies-root-redirection-test")
    original_resolve = Path.resolve
    mkdir_calls: list[Path] = []

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == fixed:
            return redirected
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(paths_module, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "mkdir", lambda path, **_kwargs: mkdir_calls.append(path))

    with pytest.raises(DataRootUnavailableError, match="dedicated volume"):
        data_root()

    assert mkdir_calls == []


@pytest.mark.parametrize("resolution", ["outside", "loop"])
def test_cache_redirect_is_rejected_before_any_directory_is_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resolution: str,
) -> None:
    fixed = tmp_path / "fixed-private-data"
    outside = tmp_path / "outside-private-data"
    cache_root = fixed / "cache"
    original_resolve = Path.resolve
    mkdir_calls: list[Path] = []

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path == cache_root or cache_root in path.parents:
            if resolution == "loop":
                raise RuntimeError("simulated junction loop")
            return outside / path.relative_to(cache_root)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "mkdir", lambda path, **_kwargs: mkdir_calls.append(path))

    with pytest.raises(DataRootUnavailableError, match="outside the dedicated data root"):
        configure_qt_cache_environment(fixed)

    assert mkdir_calls == []


def test_cache_root_resolution_failure_enters_data_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = tmp_path / "unreadable-private-data"

    def resolve(_path: Path, *_args: object, **_kwargs: object) -> Path:
        raise PermissionError("simulated volume failure")

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(DataRootUnavailableError, match="simulated volume failure") as raised:
        configure_qt_cache_environment(fixed)
    assert raised.value.path == fixed


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_explicit_test_and_diagnostic_purposes_keep_isolated_overrides_working(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for purpose, name in (
        (DataRootPurpose.TEST, "pytest-private"),
        (DataRootPurpose.DIAGNOSTIC, "self-test-private"),
        (DataRootPurpose.NATIVE_CAPTURE_HELPER, "capture-helper-private"),
    ):
        expected = tmp_path / name
        monkeypatch.setenv("LILIES_DATA_DIR", str(expected))
        assert data_root(purpose=purpose) == expected.resolve()
        assert expected.is_dir()

    with pytest.raises(TypeError, match="DataRootPurpose"):
        data_root(purpose="diagnostic")  # type: ignore[arg-type]


@pytest.mark.skipif(os.name != "nt", reason="the fixed F: boundary is Windows-only")
def test_normal_startup_enters_recovery_for_external_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RecoveryApplication:
        def setQuitOnLastWindowClosed(self, _enabled: bool) -> None:
            pass

        def setApplicationName(self, _name: str) -> None:
            pass

        def setOrganizationName(self, _name: str) -> None:
            pass

        def setApplicationVersion(self, _version: str) -> None:
            pass

        def setWindowIcon(self, _icon: object) -> None:
            pass

    attempted = tmp_path / "must-not-be-created"
    recovery: list[object] = []
    monkeypatch.setenv("LILIES_DATA_DIR", str(attempted))
    monkeypatch.setattr(app_module, "forward_to_existing_instance", lambda _args: False)
    monkeypatch.setattr(
        app_module,
        "QApplication",
        lambda _arguments: _RecoveryApplication(),
    )
    monkeypatch.setattr(app_module, "tray_icon", lambda: object())
    monkeypatch.setattr(
        app_module,
        "disable_qt_disk_caches_for_recovery",
        lambda: recovery.append("cache-disabled"),
    )
    monkeypatch.setattr(
        app_module,
        "restore_from_backup",
        lambda path: recovery.append(Path(path)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        "critical",
        lambda *_args: recovery.append("dialog"),
    )

    assert app_module.main(["--visual"]) == 3
    assert recovery[0] == "cache-disabled"
    assert "dialog" in recovery
    assert any(isinstance(item, Path) for item in recovery)
    assert not attempted.exists()


def test_every_nonproduction_override_is_explicit_at_its_startup_call_site() -> None:
    app_source = _source("src/lilies/app.py")
    backend_source = _source("src/lilies/backend.py")
    helper_source = _source("src/lilies/core/native_capture_helper.py")
    paths_source = _source("src/lilies/paths.py")

    ast.parse(app_source)
    ast.parse(backend_source)
    ast.parse(helper_source)
    ast.parse(paths_source)

    assert "DataRootPurpose.DIAGNOSTIC" in app_source
    assert "data_root(purpose=data_root_purpose)" in app_source
    assert "DataRootPurpose.DIAGNOSTIC" in backend_source
    assert "if smoke" in backend_source
    assert "DataRootPurpose.NATIVE_CAPTURE_HELPER" in helper_source
    assert "purpose: DataRootPurpose = DataRootPurpose.PRODUCTION" in paths_source
    assert "if not smoke and not os.environ.get(\"LILIES_DATA_DIR\")" not in backend_source
