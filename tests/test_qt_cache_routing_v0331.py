from __future__ import annotations

import ast
import os
from pathlib import Path, PureWindowsPath

from lilies.paths import (
    WINDOWS_PRIVATE_DATA_ROOT,
    DataRootUnavailableError,
    configure_qt_cache_environment,
    disable_qt_disk_caches_for_recovery,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def test_qt_cache_configuration_overrides_untrusted_external_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = tmp_path / "private-data"
    hostile_root = Path(r"C:\Users\attacker\AppData\Local\qt-cache")
    monkeypatch.setenv("QML_DISK_CACHE_PATH", str(hostile_root / "qml"))
    monkeypatch.setenv(
        "QSG_RHI_PIPELINE_CACHE_LOAD", str(hostile_root / "load.bin")
    )
    monkeypatch.setenv(
        "QSG_RHI_PIPELINE_CACHE_SAVE", str(hostile_root / "save.bin")
    )
    monkeypatch.delenv("QT_DISABLE_SHADER_DISK_CACHE", raising=False)

    routing = configure_qt_cache_environment(data_root)

    expected_qml = data_root.resolve() / "cache" / "qmlcache"
    expected_pipeline = (
        data_root.resolve() / "cache" / "qt-rhi-pipeline-cache.bin"
    )
    assert os.environ["QML_DISK_CACHE_PATH"] == str(expected_qml)
    assert os.environ["QSG_RHI_PIPELINE_CACHE_LOAD"] == str(expected_pipeline)
    assert os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"] == str(expected_pipeline)
    assert hostile_root not in (
        Path(os.environ["QML_DISK_CACHE_PATH"]),
        Path(os.environ["QSG_RHI_PIPELINE_CACHE_LOAD"]),
        Path(os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"]),
    )
    assert routing["environmentApplied"] is True
    assert os.environ["QT_DISABLE_SHADER_DISK_CACHE"] == "1"
    assert routing["qtShaderDiskCacheDisabled"] is True
    assert routing["pathsWithinDataRoot"] is True
    assert routing["passed"] is True


def test_qt_cache_directory_and_pipeline_files_stay_under_data_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root = (tmp_path / "dedicated-f-data").resolve()
    monkeypatch.delenv("QML_DISK_CACHE_PATH", raising=False)
    monkeypatch.delenv("QSG_RHI_PIPELINE_CACHE_LOAD", raising=False)
    monkeypatch.delenv("QSG_RHI_PIPELINE_CACHE_SAVE", raising=False)

    routing = configure_qt_cache_environment(data_root)

    routed_paths = (
        Path(str(routing["cacheRoot"])),
        Path(str(routing["qmlDiskCachePath"])),
        Path(str(routing["rhiPipelineCacheLoadPath"])),
        Path(str(routing["rhiPipelineCacheSavePath"])),
    )
    assert all(_is_within(path, data_root) for path in routed_paths)
    assert Path(str(routing["qmlDiskCachePath"])).is_dir()
    assert routing["rhiPipelineCacheLoadPath"] == routing[
        "rhiPipelineCacheSavePath"
    ]
    assert PureWindowsPath(str(WINDOWS_PRIVATE_DATA_ROOT)).drive.upper() == "F:"


def test_qt_cache_configuration_fails_closed_when_cache_root_is_not_a_directory(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "private-data"
    data_root.mkdir()
    (data_root / "cache").write_text("not a directory", encoding="utf-8")

    try:
        configure_qt_cache_environment(data_root)
    except DataRootUnavailableError as exc:
        assert exc.path == data_root.resolve()
    else:
        raise AssertionError("an invalid cache root must stop Qt startup")


def test_recovery_disables_the_rhi_disk_cache_with_qt_supported_name(
    monkeypatch,
) -> None:
    correct_name = "QSG_RHI_DISABLE_DISK_CACHE"
    misleading_names = (
        "QSG_RHI_DISABLE_SHADER_DISK_CACHE",
        "QSG_RHI_PIPELINE_CACHE_DISABLE",
    )
    monkeypatch.delenv(correct_name, raising=False)
    for misleading_name in misleading_names:
        monkeypatch.delenv(misleading_name, raising=False)
    for inherited_name in (
        "QML_FORCE_DISK_CACHE",
        "QML_DISK_CACHE_PATH",
        "QSG_RHI_PIPELINE_CACHE_LOAD",
        "QSG_RHI_PIPELINE_CACHE_SAVE",
    ):
        monkeypatch.setenv(inherited_name, r"C:\untrusted-cache")

    disable_qt_disk_caches_for_recovery()

    assert os.environ["QML_DISABLE_DISK_CACHE"] == "1"
    assert os.environ["QT_DISABLE_SHADER_DISK_CACHE"] == "1"
    assert os.environ[correct_name] == "1"
    assert all(name not in os.environ for name in misleading_names)
    assert "QML_FORCE_DISK_CACHE" not in os.environ
    assert "QML_DISK_CACHE_PATH" not in os.environ
    assert "QSG_RHI_PIPELINE_CACHE_LOAD" not in os.environ
    assert "QSG_RHI_PIPELINE_CACHE_SAVE" not in os.environ
    paths_source = _read("src/lilies/paths.py")
    assert f'os.environ["{correct_name}"] = "1"' in paths_source
    assert all(name not in paths_source for name in misleading_names)


def test_qt_cache_routing_runs_before_qapplication_and_qml_engine() -> None:
    tree = ast.parse(_read("src/lilies/app.py"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "main"
    )

    calls: dict[str, list[int]] = {
        "configure_qt_cache_environment": [],
        "QApplication": [],
        "QQmlApplicationEngine": [],
    }
    for node in ast.walk(main):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        else:
            continue
        if name in calls:
            calls[name].append(node.lineno)

    assert all(calls.values()), calls
    configured_at = min(calls["configure_qt_cache_environment"])
    assert configured_at < min(calls["QApplication"])
    assert configured_at < min(calls["QQmlApplicationEngine"])


def test_windows_startup_wrapper_rejects_files_and_cache_paths_in_localappdata() -> None:
    source = _read("scripts/verify_packaged_windows_startup.ps1")
    sentinel = (
        "$localAppDataSentinel = Join-Path $diagnosticRoot "
        "'local-app-data-sentinel'"
    )
    create = "[IO.Directory]::CreateDirectory($localAppDataSentinel)"
    assign = "$env:LOCALAPPDATA = $localAppDataSentinel"
    launch = "Start-Process -FilePath $Executable"
    enumerate_sentinel = (
        "Get-ChildItem -LiteralPath $localAppDataSentinel -Force -Recurse"
    )
    reject_files = "if ($localAppDataFiles.Count -ne 0 -or"
    reject_cache_paths = "$liliesOrQtCacheEntries.Count -ne 0)"
    restore = "$env:LOCALAPPDATA = $oldLocalAppData"

    for contract in (
        sentinel,
        create,
        "artifacts\\windows-startup-probes",
        "$oldLocalAppData = $env:LOCALAPPDATA",
        assign,
        launch,
        enumerate_sentinel,
        "$localAppDataFiles = @(",
        "$liliesOrQtCacheEntries = @(",
        "'.qmlc'",
        "qmlcache|qtpipelinecache",
        reject_files,
        reject_cache_paths,
        "Packaged startup wrote a file or Lilies/Qt cache path outside LILIES_DATA_DIR via LOCALAPPDATA",
        "Remove-Item Env:LOCALAPPDATA",
        restore,
    ):
        assert contract in source

    assert source.index(sentinel) < source.index(create)
    assert source.index(create) < source.index(assign) < source.index(launch)
    assert source.index(launch) < source.index(enumerate_sentinel)
    assert source.index(enumerate_sentinel) < source.index(reject_files)
    assert source.index(reject_files) < source.index("} finally {")


def test_compact_resource_probe_keeps_its_diagnostic_cache_on_project_drive() -> None:
    source = _read("scripts/verify_packaged_compact_resources.ps1")
    runtime = source[source.index("$ProjectRoot =") :]
    assert "artifacts\\compact-resource-probes" in runtime
    assert "[IO.Directory]::CreateDirectory($temporaryRoot)" in runtime
    assert "[IO.Path]::GetTempPath()" not in runtime
    assert "$env:LILIES_DATA_DIR = $diagnosticDataRoot" in runtime
    assert "Remove-ExactDiagnosticDirectoryWithRetry" in runtime
