# Orchestration only. The caller verifies one exact candidate before entering;
# file recovery uses the shared per-target transaction lock. Never uses the
# installed legacy image's unchecked --restore implementation.

function Invoke-LiliesCheckedUninstall {
    param([string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [string]$RecoveryRoot, [scriptblock]$ValidateRecovery,
          $ShortcutPlan=$null, [scriptblock]$Checkpoint={})
    if ($null -eq $ValidateRecovery) { throw 'An exact recovery package verifier is required.' }
    $recoveryDirectory = Get-LiliesPhysicalPath $RecoveryRoot
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot ([guid]::NewGuid().ToString('N'))
    foreach ($other in @($paths.allowedBase, $paths.journalBase)) {
        if ($recoveryDirectory -ieq $other -or
            $recoveryDirectory.StartsWith($other + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $other.StartsWith($recoveryDirectory + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Recovery package must be independent of program and journal directories.'
        }
    }
    # PowerShell uses dynamic scope: Stop-LiliesForUpgrade also declares
    # ValidateRecovery. Use a distinct name inside its callback so the callback
    # invokes the caller verifier, not recursively invokes itself.
    $verifyUninstallRecovery = $ValidateRecovery
    & $verifyUninstallRecovery $recoveryDirectory | Out-Null
    $recoverySnapshot = Get-LiliesTreeSnapshot $recoveryDirectory
    $targetExecutable = Join-Path $paths.target 'LiliesInTheBox.exe'
    $recoveryExecutable = Join-Path $recoveryDirectory 'LiliesInTheBox.exe'
    if (-not (Test-Path -LiteralPath $recoveryExecutable -PathType Leaf)) { throw 'Recovery executable is missing.' }
    $parameters = @{Target=$paths.target; AllowedBase=$paths.allowedBase; JournalRoot=$paths.journalBase}
    $transaction = New-LiliesUninstallTransaction @parameters -ShortcutPlan $ShortcutPlan
    Write-Information "Prepared uninstall $($transaction.transactionId). Recovery journal: $($transaction.journal)" -InformationAction Continue
    return Complete-LiliesUninstallTransaction @parameters -TransactionId $transaction.transactionId `
        -BeforeRemove {
            param($lockedPaths)
            # Complete holds the exclusive target lock and has already
            # snapshotted both files and links before stopping the old main.
            Stop-LiliesForUpgrade -TargetExecutable $targetExecutable -RecoveryExecutable $recoveryExecutable `
                -ValidateRecovery {
                    & $verifyUninstallRecovery $recoveryDirectory | Out-Null
                    Assert-LiliesTreeMatches $recoveryDirectory $recoverySnapshot
                }
            $related = @($lockedPaths.target, $lockedPaths.previous, $lockedPaths.stage, $lockedPaths.failed) |
                ForEach-Object { Join-Path $_ 'LiliesInTheBox.exe' }
            Assert-LiliesImagesQuiescent @($related + $recoveryExecutable)
        } -BeforeRollback {
            param($lockedPaths)
            # A late/unknown process blocks file rollback rather than moving
            # its files. This guard is read-only and never guesses a PID.
            $related = @($lockedPaths.target, $lockedPaths.previous, $lockedPaths.stage, $lockedPaths.failed) |
                ForEach-Object { Join-Path $_ 'LiliesInTheBox.exe' }
            Assert-LiliesImagesQuiescent @($related + $recoveryExecutable)
        } -Checkpoint $Checkpoint
}
