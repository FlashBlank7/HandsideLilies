"""Diagnostic startup owns an isolated F-drive lifetime, not system temp/user data."""
from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from lilies import app, paths
from lilies.paths import DataRootPurpose, DataRootUnavailableError


@pytest.mark.skipif(os.name != "nt", reason="fixed F-drive startup boundary")
@pytest.mark.parametrize("mode", ["smoke", "self-test", "windows-startup-probe", "benchmark"])
@pytest.mark.parametrize("previous", [None, "   "])
def test_default_diagnostic_temp_has_explicit_parent_and_restores_environment(
    tmp_path, monkeypatch, mode, previous,
):
    fixed = tmp_path / "fixed-private-data"
    monkeypatch.setattr(paths, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    if previous is None:
        monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("LILIES_DATA_DIR", previous)
    original_temporary = tempfile.TemporaryDirectory
    attempted_parents = []
    allocated = []

    def allocate(*args, **kwargs):
        attempted_parents.append(kwargs.get("dir"))
        # The pre-fix code uses system temp. Keep even this red test on its
        # owned F fixture rather than reproducing the unwanted system write.
        actual_kwargs = dict(kwargs)
        actual_kwargs.setdefault("dir", tmp_path)
        owned = original_temporary(*args, **actual_kwargs)
        allocated.append(Path(owned.name))
        return owned

    monkeypatch.setattr(tempfile, "TemporaryDirectory", allocate)
    roots = []

    def fail_cache(root):
        roots.append(root)
        raise DataRootUnavailableError(root, "synthetic cache refusal")

    def forbidden(*_a, **_k):
        pytest.fail("failed diagnostic must not create Qt, recover the shell or forward")

    monkeypatch.setattr(app, "configure_qt_cache_environment", fail_cache)
    monkeypatch.setattr(app, "QApplication", forbidden)
    monkeypatch.setattr(app, "restore_from_backup", forbidden)
    monkeypatch.setattr(app, "request_existing_instance", forbidden)
    args = ["--" + mode]
    if mode != "smoke":
        args.append(str(tmp_path / "result.json"))
    assert app.main(args) == 3
    assert attempted_parents == [fixed.resolve() / "diagnostics"]
    assert roots == allocated
    assert len(allocated) == 1
    assert not allocated[0].exists()
    assert os.environ.get("LILIES_DATA_DIR") == previous


@pytest.mark.skipif(os.name != "nt", reason="fixed F-drive startup boundary")
@pytest.mark.parametrize("override", ["fixed", "ancestor", "redirect", "system-drive", "missing"])
def test_diagnostic_root_rejects_real_or_system_data_before_writing(
    tmp_path, monkeypatch, override,
):
    fixed = tmp_path / "fixed-private-data"
    monkeypatch.setattr(paths, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    requested = {
        "fixed": fixed,
        "ancestor": fixed.parent,
        "redirect": tmp_path / "redirected-diagnostic",
        "system-drive": Path(r"C:\Lilies-diagnostic-must-not-create"),
    }.get(override)
    if requested is None:
        monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("LILIES_DATA_DIR", str(requested))
    resolve = Path.resolve
    if override == "redirect":
        monkeypatch.setattr(Path, "resolve", lambda p, *a, **k: fixed.resolve() if p == requested else resolve(p, *a, **k))
    mkdir_calls = []
    monkeypatch.setattr(Path, "mkdir", lambda p, **kw: mkdir_calls.append(p))
    # Avoid actually testing the availability of any real C directory.
    monkeypatch.setattr(Path, "is_dir", lambda p: True)
    monkeypatch.setattr(os, "access", lambda *a: True)
    with pytest.raises(DataRootUnavailableError):
        paths.data_root(purpose=DataRootPurpose.DIAGNOSTIC)
    assert mkdir_calls == []


@pytest.mark.skipif(os.name != "nt", reason="fixed F-drive startup boundary")
@pytest.mark.parametrize("outcome", ["success", "nonzero", "error"])
@pytest.mark.parametrize("explicit", [False, True])
def test_main_scopes_owned_data_and_never_cleans_caller_data(tmp_path, monkeypatch, outcome, explicit):
    fixed = tmp_path / "fixed-private-data"
    fixed.mkdir()
    real_sentinel = fixed / "do-not-touch.bin"
    real_sentinel.write_bytes(b"synthetic user data")
    monkeypatch.setattr(paths, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    caller_root = tmp_path / "caller-owned"
    if explicit:
        monkeypatch.setenv("LILIES_DATA_DIR", str(caller_root))
    else:
        monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    used = []

    def run(args, argv, *, diagnostic_run):
        assert args.self_test and diagnostic_run
        root = paths.data_root(purpose=DataRootPurpose.DIAGNOSTIC)
        used.append(root)
        (root / "probe.bin").write_bytes(b"synthetic probe data")
        if outcome == "error":
            raise RuntimeError("synthetic engine exception")
        return 0 if outcome == "success" else 4

    monkeypatch.setattr(app, "_run_application", run)
    args = ["--self-test", str(tmp_path / "result.json")]
    if outcome == "error":
        with pytest.raises(RuntimeError, match="synthetic engine"):
            app.main(args)
    else:
        assert app.main(args) == (0 if outcome == "success" else 4)
    assert len(used) == 1
    assert os.environ.get("LILIES_DATA_DIR") == (str(caller_root) if explicit else None)
    assert used[0].exists() is explicit
    if explicit:
        assert (caller_root / "probe.bin").read_bytes() == b"synthetic probe data"
    else:
        assert used[0].parent == fixed / "diagnostics"
    assert real_sentinel.read_bytes() == b"synthetic user data"


@pytest.mark.skipif(os.name != "nt", reason="fixed F-drive startup boundary")
@pytest.mark.parametrize("failure", ["fixed-redirect", "parent-redirect", "parent-loop", "mkdir", "allocate"])
def test_failed_default_allocation_does_not_enter_application_or_fall_back(tmp_path, monkeypatch, failure):
    fixed = tmp_path / "fixed-private-data"
    monkeypatch.setattr(paths, "WINDOWS_PRIVATE_DATA_ROOT", fixed)
    monkeypatch.delenv("LILIES_DATA_DIR", raising=False)
    resolve = Path.resolve

    def resolve_path(p, *a, **k):
        if p == fixed and failure == "fixed-redirect":
            return Path(r"C:\Lilies-must-not-create")
        if p == fixed / "diagnostics":
            if failure == "parent-redirect":
                return tmp_path / "must-not-create"
            if failure == "parent-loop":
                raise RuntimeError("synthetic junction loop")
        return resolve(p, *a, **k)

    mkdir_calls = []

    def mkdir(p, **_kw):
        mkdir_calls.append(p)
        if failure == "mkdir":
            raise PermissionError("synthetic read-only volume")

    attempts = []

    def allocate(**kw):
        attempts.append(kw)
        raise OSError("synthetic full volume")

    def forbidden(*_a, **_k):
        pytest.fail("failed allocation must return before app or recovery")

    monkeypatch.setattr(Path, "resolve", resolve_path)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(tempfile, "TemporaryDirectory", allocate)
    monkeypatch.setattr(app, "_run_application", forbidden)
    monkeypatch.setattr(app, "restore_from_backup", forbidden)
    assert app.main(["--self-test", str(tmp_path / "result.json")]) == 3
    assert os.environ.get("LILIES_DATA_DIR") is None
    assert mkdir_calls == ([fixed / "diagnostics"] if failure in {"mkdir", "allocate"} else [])
    assert len(attempts) == (1 if failure == "allocate" else 0)
    if attempts:
        assert attempts[0]["dir"] == fixed / "diagnostics"
    assert not fixed.exists()


def test_normal_launcher_does_not_allocate_diagnostic_data(monkeypatch):
    monkeypatch.setattr(app, "forward_to_existing_instance", lambda args: False)

    def forbidden():
        pytest.fail("normal launcher must not allocate or clean diagnostic data")

    calls = []
    monkeypatch.setattr(app, "diagnostic_data_environment", forbidden)
    monkeypatch.setattr(app, "_run_application", lambda args, argv, **kw: calls.append((args.compact, kw)) or 17)
    assert app.main(["--compact"]) == 17
    assert calls == [(True, {"diagnostic_run": False})]


@pytest.mark.skipif(os.name != "nt", reason="fixed F-drive/native hidden startup")
@pytest.mark.parametrize("mode", ["self-test", "windows-startup-probe"])
def test_real_startup_without_override_cleans_owned_data(tmp_path, mode, record_property):
    repo = Path(__file__).resolve().parents[1]
    fixed = tmp_path / "synthetic-private"
    system_temp = tmp_path / "system-temp-sentinel"
    local_app_data = tmp_path / "local-app-data-sentinel"
    system_temp.mkdir()
    local_app_data.mkdir()
    report_path = tmp_path / (mode + ".json")
    environment = dict(os.environ, PYTHONPATH=str(repo / "src"), PYTHONUTF8="1",
                       PYTHONDONTWRITEBYTECODE="1", TEMP=str(system_temp), TMP=str(system_temp),
                       LOCALAPPDATA=str(local_app_data))
    for name in ("LILIES_DATA_DIR", "QML_DISABLE_DISK_CACHE", "QML_FORCE_DISK_CACHE"):
        environment.pop(name, None)
    code = """
import json, os, sys
from pathlib import Path
from lilies import app, paths
paths.WINDOWS_PRIVATE_DATA_ROOT = Path(sys.argv[1])
code = app.main(['--' + sys.argv[2], sys.argv[3]])
print(json.dumps({'exit': code, 'overrideRestored': 'LILIES_DATA_DIR' not in os.environ}))
raise SystemExit(code)
"""
    process = subprocess.run(
        [sys.executable, "-B", "-c", code, str(fixed), mode, str(report_path)],
        cwd=repo, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert process.returncode == 0, process.stdout + "\n" + process.stderr
    returned = json.loads(process.stdout.strip().splitlines()[-1])
    assert returned == {"exit": 0, "overrideRestored": True}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    cache = report["qtCacheRouting"]
    allocated = Path(cache["dataRoot"])
    assert allocated.parent == fixed / "diagnostics"
    assert allocated.name.startswith("lilies-diagnostic-")
    assert cache["passed"] is True
    for name in ("qmlDiskCachePath", "rhiPipelineCacheLoadPath", "rhiPipelineCacheSavePath"):
        assert Path(cache[name]).is_relative_to(allocated)
    if mode == "windows-startup-probe":
        assert report["diagnosticPlatform"] == "windows"
        assert report["quickWindowCount"] > 0
        assert report["visibleQuickWindowCount"] == 0
        assert report["visibleQuickWindows"] == []
        assert report["trayPublished"] is False
    else:
        assert report["diagnosticPlatform"] == "offscreen"
        assert report["qmlWarningCount"] == 0
    record_property("report_path", str(report_path))
    # Check after process termination as well as app.main's scoped return:
    # late Qt teardown must not recreate a deleted cache or data directory.
    parent = fixed / "diagnostics"
    assert parent.is_dir() and list(parent.iterdir()) == []
    assert not any(p.is_file() for p in fixed.rglob("*"))
    assert list(system_temp.iterdir()) == []
    assert not any(p.is_file() for p in local_app_data.rglob("*"))
