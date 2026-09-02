from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_installer_rejects_wrong_exe_version_before_any_mutation() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")

    call = "Assert-ReleaseExecutable $SourceExe"
    assert "function Assert-ReleaseExecutable" in installer
    assert "$ExpectedFileVersion = '0.3.49.0'" in installer
    assert "$ExpectedProductVersion = '0.3.49'" in installer
    assert ".VersionInfo" in installer
    assert "FileVersion" in installer
    assert "ProductVersion" in installer
    assert installer.index(call) < installer.index("Remove-Item")
    assert installer.index(call) < installer.index("Copy-Item")
    assert installer.index(call) < installer.index("install-manifest.json")


def test_installer_waits_for_source_restore_before_stopping_installed_processes() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")

    start = "Start-Process -FilePath $SourceExe"
    wait = "$restoreProcess.WaitForExit(20000)"
    enumerate_installed = "$installedProcesses = @(Get-Process"
    remove_target = "Remove-Item -LiteralPath $Target -Recurse -Force"
    assert "-WindowStyle Hidden -PassThru" in installer
    assert "Stop-Process -Id $restoreProcess.Id -Force" in installer
    assert installer.index(start) < installer.index(wait)
    assert installer.index(wait) < installer.index(enumerate_installed)
    assert installer.index(enumerate_installed) < installer.index(remove_target)
    assert "& $TargetExe --restore" not in installer


def test_primary_and_startup_shortcuts_do_not_force_compact_mode() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")

    assert "$shortcut.Arguments = ''" in installer
    assert (
        "if ($StartAtLogin -or (Test-Path -LiteralPath $startup -PathType Leaf))"
        in installer
    )
    assert "Copy-Item -LiteralPath $shortcutPath -Destination $startup -Force" in installer
    assert "Start-Process -FilePath $TargetExe -ArgumentList '--compact'" not in installer
    assert "Start-Process -FilePath $TargetExe\n" in installer


def test_installer_has_a_separate_explicit_compact_only_shortcut() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")

    assert "Lilies in the box - Pet only.lnk" in installer
    assert "$petShortcut.Arguments = '--compact'" in installer
    assert installer.count("Arguments = '--compact'") == 1


def test_installer_has_a_separate_explicit_full_desktop_shortcut() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    uninstaller = (PROJECT_ROOT / "scripts" / "uninstall_windows.ps1").read_text("utf-8")

    assert "Lilies in the box - Desktop.lnk" in installer
    assert "$desktopShortcut.Arguments = '--visual'" in installer
    assert installer.count("Arguments = '--visual'") == 1
    assert "Lilies in the box - Desktop.lnk" in uninstaller
    assert "Lilies in the box - Pet only.lnk" in uninstaller
