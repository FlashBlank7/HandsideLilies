from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from lilies.app import CompactHitTestFilter, parse_args


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_native_filter_requires_a_pre_resolved_immutable_window_id() -> None:
    signature = inspect.signature(CompactHitTestFilter)
    native_window_id = signature.parameters["native_window_id"]

    assert native_window_id.kind is inspect.Parameter.KEYWORD_ONLY
    assert native_window_id.default is inspect.Parameter.empty


def test_native_filter_owns_no_window_creation_or_presentation_call() -> None:
    # The native callback may call helpers on this class. Audit the whole class
    # rather than only nativeEventFilter(), so moving a forbidden call into a
    # helper cannot silently bypass the release contract.
    tree = ast.parse(textwrap.dedent(inspect.getsource(CompactHitTestFilter)))
    forbidden = {
        "create",
        "destroy",
        "hide",
        "lower",
        "raise_",
        "requestActivate",
        "setVisible",
        "show",
        "showFullScreen",
        "showMaximized",
        "showMinimized",
        "showNormal",
        "winId",
    }
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert calls.isdisjoint(forbidden), sorted(calls & forbidden)


def test_hidden_windows_startup_probe_is_a_private_cli_mode() -> None:
    parsed = parse_args(["--windows-startup-probe", "probe.json"])

    assert parsed.windows_startup_probe == "probe.json"
    assert parsed.smoke is False
    assert parsed.self_test is None


def test_windows_probe_uses_qwindows_without_publishing_surfaces() -> None:
    app_source = _read("src/lilies/app.py")
    qml_source = _read("qml/Main.qml")

    assert 'os.environ["QT_QPA_PLATFORM"] = "windows"' in app_source
    assert '"diagnosticWindowProbe", bool(args.windows_startup_probe)' in app_source
    assert "if not args.windows_startup_probe:\n        tray.show()" in app_source
    assert "SendMessageW" in app_source
    assert "CompactHitTestFilter.WM_NCHITTEST" in app_source
    assert '"nativeHitTestDispatched": hit_test_dispatched' in app_source
    assert '"nativeRadialWorldHit": native_radial_world_hit' in app_source
    assert 'item.objectName() == "desktopPetDesktopModeTab"' in app_source
    assert '"nativeDesktopModeTabHit": native_desktop_mode_tab_hit' in app_source
    assert (
        '"nativeDesktopModeTabHitResult": probe_state['
        in app_source
    )
    assert '"rootNoActivateStyle": root_no_activate_style' in app_source
    assert '"petNoActivateStyle": pet_no_activate_style' in app_source
    assert (
        '"nativeTransparentCornerPass": native_transparent_corner_pass'
        in app_source
    )
    assert '"visibleQuickWindowCount": len(visible_windows)' in app_source
    assert 'visible: !diagnosticWindowProbe && backend.shellMode !== "compact"' in qml_source
    assert (
        "visible: !diagnosticWindowProbe && !desktop.petPresenceSuppressed"
        in qml_source
    )
    assert 'petPresenceState === "silent"' in qml_source
    assert 'petPresenceState === "blocked"' in qml_source


def test_packaging_build_hard_runs_the_hidden_qwindows_probe() -> None:
    build = _read("scripts/build_windows.ps1")
    probe = _read("scripts/verify_packaged_windows_startup.ps1")

    assert "verify_packaged_windows_startup.ps1" in build
    assert "-WindowStyle Hidden -PassThru" in probe
    assert "--windows-startup-probe" in probe
    assert "packaged-windows-startup-v0342.json" in probe
    assert "$env:LILIES_DATA_DIR = $diagnosticRoot" in probe
    assert "Stop-Process -Id $process.Id -Force" in probe
    assert "Get-Process -Name" not in probe
    for field in (
        "qmlLoaded",
        "nativeWindowCreated",
        "nativeWindowIdCached",
        "nativeHitTestDispatched",
        "nativeRadialWorldHit",
        "nativeDesktopModeTabHit",
        "nativeDesktopModeTabHitResult",
        "nativeTransparentCornerPass",
        "rootNoActivateStyle",
        "petNoActivateStyle",
        "eventLoopResponsive",
        "trayPublished",
        "visibleQuickWindowCount",
        "passed",
    ):
        assert field in probe


def test_v0342_release_gate_requires_native_desktop_mode_tab_hit() -> None:
    promotion = _read("scripts/promote_v0342.ps1")
    probe = _read("scripts/verify_packaged_windows_startup.ps1")

    for source in (promotion, probe):
        assert "nativeDesktopModeTabHit" in source
        assert "nativeDesktopModeTabHitResult" in source
        assert "HTCLIENT (1)" in source
    assert (
        "Get-RequiredJsonInteger $windowsStartup "
        "'nativeDesktopModeTabHitResult' 'windowsStartup'"
        in promotion
    )
    assert (
        "Get-RequiredJsonInteger $report 'nativeDesktopModeTabHitResult'"
        in probe
    )
