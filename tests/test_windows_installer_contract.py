from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_installer_rejects_wrong_exe_version_before_any_mutation() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")

    call = "Assert-ReleaseExecutable $SourceExe"
    assert "function Assert-ReleaseExecutable" in installer
    assert "$ExpectedFileVersion = '0.3.57.0'" in installer
    assert "$ExpectedProductVersion = '0.3.57'" in installer
    assert ".VersionInfo" in installer
    assert "FileVersion" in installer
    assert "ProductVersion" in installer
    assert "Remove-Item" not in installer
    assert installer.index(call) < installer.index("$transaction = New-LiliesInstallTransaction")
    assert installer.index(call) < installer.index("$shortcutPlan = New-LiliesManagedShortcutPlan")
    assert installer.index(call) < installer.index("$manifest = [ordered]")


def test_installer_stops_verified_main_then_restores_under_transaction_lock() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    helper = (PROJECT_ROOT / "scripts" / "install_startup.ps1").read_text("utf-8")
    shutdown = helper.split("function Stop-LiliesForUpgrade", 1)[1].split("function Restore-LiliesAfterStartupFailure", 1)[0]
    activate = "$installed = Complete-LiliesInstallTransaction"
    assert installer.index(activate) < installer.index("-BeforeActivate") < installer.index("Stop-LiliesForUpgrade")
    assert "Assert-LiliesTreeMatches $paths.stage" in installer
    assert installer.index("if ($NoLaunch) { Assert-LiliesImagesQuiescent") < installer.index("$transaction = New-LiliesInstallTransaction")
    assert shutdown.index("Get-LiliesExistingProcessRole") < shutdown.index("Stop-LiliesOwnedChild $lease")
    assert shutdown.index("Stop-LiliesOwnedChild $lease") < shutdown.index("Wait-LiliesImageExit $TargetExecutable") < shutdown.index("Start-LiliesOwnedChild $recovery")
    assert shutdown.index("Start-LiliesOwnedChild $recovery") < shutdown.index("$recovery.process.WaitForExit(20000)")
    assert "Stop-Process" not in installer and "Wait-Process" not in installer
    assert "SilentlyContinue" not in installer
    assert "& $TargetExe --restore" not in installer


def test_primary_and_startup_shortcuts_do_not_force_compact_mode() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    shortcuts = (PROJECT_ROOT / "scripts" / "install_shortcuts.ps1").read_text("utf-8")
    assert "primary = ''" in shortcuts
    assert "$StartAtLogin -or (Test-Path -LiteralPath $startup -PathType Leaf)" in shortcuts
    assert "$payloads['startup'] = $payloads['primary']" in shortcuts
    assert "Copy-Item" not in installer
    assert "Start-Process -FilePath $TargetExe -ArgumentList '--compact'" not in installer
    assert "Start-Process -FilePath $TargetExe" not in installer
    assert "Start-LiliesOwnedChild $launch" in installer


def test_installer_has_a_separate_explicit_compact_only_shortcut() -> None:
    shortcuts = (PROJECT_ROOT / "scripts" / "install_shortcuts.ps1").read_text("utf-8")
    assert "Lilies in the box - Pet only.lnk" in shortcuts
    assert "pet = '--compact'" in shortcuts
    assert shortcuts.count("'--compact'") == 1


def test_installer_has_a_separate_explicit_full_desktop_shortcut() -> None:
    shortcuts = (PROJECT_ROOT / "scripts" / "install_shortcuts.ps1").read_text("utf-8")
    uninstaller = (PROJECT_ROOT / "scripts" / "uninstall_windows.ps1").read_text("utf-8")

    assert "Lilies in the box - Desktop.lnk" in shortcuts
    assert "desktop = '--visual'" in shortcuts
    assert shortcuts.count("'--visual'") == 1
    assert "New-LiliesRemovalShortcutPlan" in uninstaller
    removal = (PROJECT_ROOT / "scripts" / "uninstall_transaction.ps1").read_text("utf-8")
    assert "Get-LiliesShortcutPath $ShortcutRoot $role" in removal
    assert "desktop='--visual'" in removal and "pet='--compact'" in removal


def test_candidate_installer_stays_closed_until_real_release_acceptance() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    gate = "throw 'v0.3.57 installation is not released:"
    assert installer.index(gate) < installer.index("$transaction = New-LiliesInstallTransaction")
    assert installer.index(gate) < installer.index("Stop-LiliesForUpgrade")
    assert installer.index(gate) < installer.index("$shortcutPlan = New-LiliesManagedShortcutPlan")
    assert "[switch]$Force" not in installer


def test_candidate_source_is_exact_side_by_side_build_not_legacy_dist() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    assert "[string]$BuildId" in installer
    assert "'dist\\' + $BuildId + '\\LiliesInTheBox'" in installer
    assert "'dist\\LiliesInTheBox'" not in installer
    assert "check-candidate --root $ProjectRoot --build-id $BuildId" in installer
    assert "& $VerifierPython -B" in installer
    assert installer.index("if ($RecoverTransactionId)") < installer.index("if ($BuildId -cnotmatch")
    assert installer.index("check-candidate --root") < installer.index("$transaction = New-LiliesInstallTransaction")
    assert installer.index("check-candidate --root") < installer.index("throw 'v0.3.57 installation is not released:")
    for field in ("buildId = $BuildId", "sourceDigest = $candidateIdentity.sourceDigest", "executableSha256 = $candidateIdentity.executableSha256"):
        assert field in installer


def test_recovery_is_explicit_does_not_require_candidate_and_keeps_user_data() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    recovery = installer.split("if ($RecoverTransactionId) {", 1)[1].split("if ($RollbackCommitted)", 1)[0]
    assert "Get-LiliesTransactionPaths" in recovery
    assert "Assert-LiliesImagesQuiescent $recoveryExecutables" in recovery
    assert "-BeforeRestore" in recovery
    assert "Restore-LiliesInstallTransaction" in recovery
    assert "Stop-Process" not in recovery
    assert "Start-Process" not in recovery
    assert installer.index("if ($RecoverTransactionId)") < installer.index("Assert-ReleaseExecutable $SourceExe")
    assert "$JournalRoot = 'F:\\code\\Lilies in the box\\private-data\\install-transactions'" in installer
    helper = (PROJECT_ROOT / "scripts" / "install_transaction.ps1").read_text("utf-8")
    assert "Remove-Item" not in helper
    assert "[IO.File]::Delete" not in helper
    assert "[IO.Directory]::Delete" not in helper


def test_shortcuts_are_prepared_then_committed_inside_the_program_transaction() -> None:
    installer = (PROJECT_ROOT / "scripts" / "install_windows.ps1").read_text("utf-8")
    prepare = "$shortcutPlan = New-LiliesManagedShortcutPlan"
    assert installer.index(prepare) < installer.index("$transaction = New-LiliesInstallTransaction")
    assert "-ShortcutPlan $shortcutPlan" in installer
    assert "if (-not $NoShortcuts)" in installer
    assert "CreateShortcut" not in installer and ".Save()" not in installer
    helper = (PROJECT_ROOT / "scripts" / "install_transaction.ps1").read_text("utf-8")
    activate = helper.split("function Complete-LiliesInstallTransaction", 1)[1]
    assert activate.index("Set-LiliesShortcutsAfter") < activate.index("& $AfterActivate")
    assert activate.index("& $AfterActivate") < activate.index("Write-LiliesInstallEvent $paths 'committed'")
    assert activate.index("& $BeforeRollback") < activate.index("Restore-LiliesInstallUnlocked")
