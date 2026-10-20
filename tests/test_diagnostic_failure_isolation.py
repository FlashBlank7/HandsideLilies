"""Diagnostic failure paths must never recover the real shell or show dialogs."""
import os
from types import SimpleNamespace

import pytest

from lilies import app
from lilies.paths import DataRootUnavailableError


@pytest.mark.parametrize("mode", ["smoke", "self-test", "windows-startup-probe", "benchmark", "compact"])
@pytest.mark.parametrize("failure", ["cache", "backend-data", "backend-socket"])
def test_diagnostic_failures_return_without_native_recovery_or_dialog(tmp_path, monkeypatch, mode, failure):
    root = tmp_path / "synthetic-data"
    monkeypatch.setenv("LILIES_DATA_DIR", str(root))
    for name in ("QT_QPA_PLATFORM", "QSG_RHI_BACKEND", "QT_QUICK_BACKEND", "QT_QUICK_CONTROLS_STYLE", "QT_QPA_OFFSCREEN_SIZE"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(app, "forward_to_existing_instance", lambda _: False)
    monkeypatch.setattr(app, "data_root", lambda **_: root)

    observed = []

    def effect(name):
        def record(*_a, **_k):
            if mode != "compact":
                pytest.fail("diagnostic failure reached real recovery or a modal dialog")
            observed.append(name)
        return record

    monkeypatch.setattr(app, "restore_from_backup", effect("restore"))
    monkeypatch.setattr(app, "QMessageBox", SimpleNamespace(critical=effect("critical"), warning=effect("warning")))
    monkeypatch.setattr(app, "reserve_primary_endpoint_or_forward", lambda *_a, **_k: (SimpleNamespace(close=lambda: None), False))
    monkeypatch.setattr(app, "QQuickWindow", SimpleNamespace(setDefaultAlphaBuffer=lambda _: None))
    fake_app = SimpleNamespace(**{method: (lambda *args: None) for method in (
        "setQuitOnLastWindowClosed", "setApplicationName", "setOrganizationName", "setApplicationVersion", "setWindowIcon")})
    monkeypatch.setattr(app, "QApplication", lambda _: fake_app)
    monkeypatch.setattr(app, "tray_icon", lambda: None)

    def cache(_root):
        if failure == "cache":
            raise DataRootUnavailableError(root, "synthetic unavailable storage")
        return {"passed": True}

    def backend(**kwargs):
        assert kwargs["smoke"] == (mode != "compact")
        if failure == "backend-data":
            raise DataRootUnavailableError(root, "synthetic database unavailable")
        raise OSError("synthetic diagnostic socket unavailable")

    monkeypatch.setattr(app, "configure_qt_cache_environment", cache)
    monkeypatch.setattr(app, "disable_qt_disk_caches_for_recovery", lambda: None)
    monkeypatch.setattr(app, "Backend", backend)
    args = ["--" + mode]
    if mode not in {"smoke", "compact"}:
        args.append(str(tmp_path / "result.json"))
    assert app.main(args) == (4 if failure == "backend-socket" else 3)
    assert observed == ((["warning"] if failure == "backend-socket" else ["restore", "critical"]) if mode == "compact" else [])
    assert not root.exists()  # No actual app, storage or native API was used.
