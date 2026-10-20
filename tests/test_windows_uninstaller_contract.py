"""Release-entry wiring. File/process behavior is exercised in companion tests."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / 'scripts' / name).read_text('utf-8')


def test_gate_precedes_all_shortcut_process_and_transaction_actions():
    script = source('uninstall_windows.ps1')
    gate = script.index("throw 'v0.3.57 uninstall is not released:")
    for action in ('$ownership = New-LiliesRemovalShortcutPlan', '$uninstalled = Invoke-LiliesCheckedUninstall'):
        assert gate < script.index(action)
    assert '[switch]$Force' not in script
    assert 'Remove-Item' not in script and 'Stop-Process' not in script
    assert '& $TargetExe --restore' not in script
    assert 'SilentlyContinue' not in script


def test_exact_recovery_package_is_reverified_before_shutdown_and_restore():
    script, helper = source('uninstall_windows.ps1'), source('uninstall_startup.ps1')
    assert 'check-candidate --root $ProjectRoot --build-id $BuildId' in script
    assert 'Get-LiliesProgramFileHash $executable' in script
    assert "'dist\\' + $BuildId + '\\LiliesInTheBox'" in script
    assert script.index('Assert-LiliesUninstallCandidate $SourceRoot') < script.index("throw 'v0.3.57 uninstall is not released:")
    assert '-ValidateRecovery { param($directory); Assert-LiliesUninstallCandidate $directory }' in script
    assert 'Stop-LiliesForUpgrade' in helper and '-BeforeRemove' in helper
    assert '& $verifyUninstallRecovery $recoveryDirectory' in helper
    assert 'Assert-LiliesTreeMatches $recoveryDirectory $recoverySnapshot' in helper
    assert helper.index('$transaction = New-LiliesUninstallTransaction') < helper.index('Stop-LiliesForUpgrade -TargetExecutable')


def test_explicit_file_recovery_is_independent_and_read_only_about_processes():
    script = source('uninstall_windows.ps1')
    recovery = script.split('if ($RecoverTransactionId) {', 1)[1].split('if ($RollbackCommitted)', 1)[0]
    assert '-BeforeRestore { Assert-LiliesImagesQuiescent $recoveryExecutables }' in recovery
    assert 'Restore-LiliesUninstallTransaction' in recovery
    assert 'Start-Process' not in recovery and 'Stop-Lilies' not in recovery
    assert script.index('if ($RecoverTransactionId)') < script.index('if ($BuildId -cnotmatch')
    assert "$JournalRoot = 'F:\\code\\Lilies in the box\\private-data\\install-transactions'" in script


def test_unfinished_permanent_cleanup_is_refused_without_claiming_data_deleted():
    script = source('uninstall_windows.ps1')
    assert script.index("if ($RemoveData) { throw 'Permanent data cleanup is not released.") < script.index('Assert-LiliesUninstallCandidate $SourceRoot')
    assert 'Personal data was preserved; this is not permanent cleanup.' in script
    for name in ('uninstall_windows.ps1', 'uninstall_transaction.ps1', 'uninstall_startup.ps1'):
        helper = source(name)
        assert 'Remove-Item' not in helper and '[IO.Directory]::Delete' not in helper


def test_install_and_uninstall_entrypoints_do_not_cross_apply_plans():
    installer = source('install_transaction.ps1')
    assert "if ($manifest.schemaVersion -eq 3) { throw 'Use the explicit uninstall transaction entrypoint.' }" in installer
    assert "if ($manifest.schemaVersion -eq 3) { throw 'Use the explicit uninstall recovery entrypoint.' }" in installer
    removal = source('uninstall_transaction.ps1')
    assert "$manifest.operation -cne 'uninstall'" in removal
    assert 'Assert-LiliesNoOtherPendingTransaction $paths' in removal
