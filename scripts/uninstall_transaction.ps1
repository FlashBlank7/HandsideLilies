# Recoverable removal of one exact program tree and owned, fixed-role links.
# Requires install_transaction.ps1. Shares its per-target lock and journal so
# an interrupted install/uninstall must be recovered before another can start.
# No shell/process operations, user-data deletion or recursive deletion here.

function Assert-LiliesRemovalPlan($Plan, $Paths) {
    Assert-LiliesShortcutPlan $Plan $Paths
    if ($null -eq $Plan) { return }
    foreach ($entry in $Plan.entries) {
        # Invert the install endpoint: after = original link; before = absent.
        if ($null -ne $entry.before) { throw 'Uninstall links may only transition from recorded bytes to absence.' }
    }
}

function New-LiliesRemovalShortcutPlan([string]$Target, [string]$ShortcutRoot) {
    $executable = Get-LiliesPhysicalPath (Join-Path $Target 'LiliesInTheBox.exe')
    $shortcutDirectory = Get-LiliesPhysicalPath $ShortcutRoot
    if (-not (Test-Path -LiteralPath $shortcutDirectory)) {
        return [pscustomobject]@{plan=$null; skippedRoles=@()}
    }
    if (-not (Test-Path -LiteralPath $shortcutDirectory -PathType Container)) { throw 'Shortcut root is not a directory.' }
    $entries = [Collections.Generic.List[object]]::new()
    $skipped = [Collections.Generic.List[string]]::new()
    $arguments = @{ primary=''; desktop='--visual'; pet='--compact'; startup='' }
    $shell = New-Object -ComObject WScript.Shell
    try {
        foreach ($role in @('primary', 'desktop', 'pet', 'startup')) {
            # Missing Startup is not permission to recreate it, nor an error
            # when there is no startup shortcut to remove.
            if ($role -ceq 'startup') {
                $startupDirectory = Get-LiliesPhysicalPath (Join-Path $shortcutDirectory 'Startup')
                if (-not (Test-Path -LiteralPath $startupDirectory)) { continue }
            }
            $path = Get-LiliesShortcutPath $ShortcutRoot $role
            if (-not (Test-Path -LiteralPath $path)) { continue }
            $snapshot = ConvertTo-LiliesShortcutSnapshot (Read-LiliesShortcutBytes $path)
            $shortcut = $shell.CreateShortcut($path)
            try {
                # A familiar name alone does not authorize deleting a user-
                # customized link. Read COM metadata only; never Save/execute.
                $matches = -not [string]::IsNullOrWhiteSpace($shortcut.TargetPath) -and
                    [IO.Path]::GetFullPath($shortcut.TargetPath) -ieq $executable -and
                    [string]$shortcut.Arguments -ceq $arguments[$role]
            } finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) | Out-Null }
            if ((Get-LiliesBytesHash (Read-LiliesShortcutBytes $path)) -cne $snapshot.sha256) {
                throw 'Shortcut changed during ownership inspection; no uninstall was prepared.'
            }
            if (-not $matches) { $skipped.Add($role); continue }
            $entries.Add([pscustomobject]@{role=$role; before=$null; after=$snapshot})
        }
    } finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null }
    $plan = if ($entries.Count) {
        [pscustomobject]@{schemaVersion=1; root=(Get-LiliesPhysicalPath $ShortcutRoot); entries=$entries.ToArray()}
    } else { $null }
    return [pscustomobject]@{plan=$plan; skippedRoles=$skipped.ToArray()}
}

function Read-LiliesUninstallManifest($Paths) {
    $manifest = Read-LiliesInstallManifest $Paths
    if ($manifest.schemaVersion -ne 3 -or $manifest.operation -cne 'uninstall') {
        throw 'This journal is not an uninstall transaction.'
    }
    Assert-LiliesRemovalPlan $manifest.shortcuts $Paths
    foreach ($unexpected in @($Paths.stage, $Paths.failed)) {
        Get-LiliesPhysicalPath $unexpected | Out-Null
        if (Test-Path -LiteralPath $unexpected) { throw 'Unexpected tree occupies an uninstall transaction path.' }
    }
    return $manifest
}

function New-LiliesUninstallTransaction {
    param([string]$Target, [string]$AllowedBase, [string]$JournalRoot, $ShortcutPlan=$null)
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot ([guid]::NewGuid().ToString('N'))
    $lock = Open-LiliesInstallLock $paths
    try {
        Assert-LiliesNoOtherPendingTransaction $paths
        Assert-LiliesRemovalPlan $ShortcutPlan $paths
        Assert-LiliesShortcutState $ShortcutPlan 'after'
        $snapshot = Get-LiliesTreeSnapshot $paths.target
        if (-not (Test-Path -LiteralPath (Join-Path $paths.target 'LiliesInTheBox.exe') -PathType Leaf)) {
            throw 'No installed executable was found; preserve this directory for review.'
        }
        foreach ($reserved in @($paths.stage, $paths.previous, $paths.failed, $paths.journal)) {
            Get-LiliesPhysicalPath $reserved | Out-Null
            if (Test-Path -LiteralPath $reserved) { throw 'Uninstall transaction destination already exists.' }
        }
        [IO.Directory]::CreateDirectory($paths.journal) | Out-Null
        Write-LiliesExclusiveJson (Join-Path $paths.journal 'manifest.json') ([ordered]@{
            schemaVersion=3; operation='uninstall'; transactionId=$paths.id
            target=$paths.target; hadPrevious=$true; oldSnapshot=$snapshot; newSnapshot=$null
            shortcuts=$ShortcutPlan
        })
        Write-LiliesInstallEvent $paths 'prepared'
        return [pscustomobject]@{transactionId=$paths.id; state='prepared'; journal=$paths.journal; retained=$paths.previous}
    } finally { $lock.Dispose() }
}

function Assert-LiliesUninstallEndpoints($Paths, $Manifest) {
    # Repeat after a process/recovery guard too. That guard can take time;
    # another writer must not introduce reserved trees unnoticed meanwhile.
    foreach ($unexpected in @($Paths.stage, $Paths.failed)) {
        Get-LiliesPhysicalPath $unexpected | Out-Null
        if (Test-Path -LiteralPath $unexpected) { throw 'Unexpected tree occupies an uninstall transaction path.' }
    }
    Assert-LiliesDisplacedShortcuts $Paths $Manifest.shortcuts
    Assert-LiliesShortcutState $Manifest.shortcuts
    $targetExists = Test-Path -LiteralPath $Paths.target
    $retainedExists = Test-Path -LiteralPath $Paths.previous
    if ($targetExists -eq $retainedExists) { throw 'Uninstall program state is missing or ambiguous; preserve all files.' }
    if ($targetExists) { Assert-LiliesTreeMatches $Paths.target $Manifest.oldSnapshot }
    else { Assert-LiliesTreeMatches $Paths.previous $Manifest.oldSnapshot }
}

function Restore-LiliesRemovedLinks($Paths, $Plan, [scriptblock]$Checkpoint={}) {
    if ($null -eq $Plan) { return }
    Assert-LiliesDisplacedShortcuts $Paths $Plan
    Assert-LiliesShortcutState $Plan
    foreach ($entry in $Plan.entries) {
        if ((Get-LiliesShortcutState $Plan $entry) -ceq 'before') {
            Set-LiliesShortcutSnapshot $Paths $Plan $entry $entry.after 'apply' 'before' $Checkpoint
        }
        & $Checkpoint ('after-uninstall-link-restore-' + $entry.role) $Paths | Out-Null
        foreach ($purpose in @('apply', 'restore', 'remove', 'replaced')) {
            Preserve-LiliesShortcutTemp $Paths $Plan $entry $purpose
        }
    }
    Assert-LiliesShortcutState $Plan 'after'
}

function Restore-LiliesUninstallUnlocked($Paths, $Manifest, [scriptblock]$Checkpoint={}) {
    Assert-LiliesUninstallEndpoints $Paths $Manifest
    Write-LiliesInstallEvent $Paths 'recovering'
    if (Test-Path -LiteralPath $Paths.previous) {
        Move-LiliesProgramDirectory $Paths.previous $Paths.target $Paths
        & $Checkpoint 'after-uninstall-program-restore' $Paths | Out-Null
    }
    Restore-LiliesRemovedLinks $Paths $Manifest.shortcuts $Checkpoint
    Assert-LiliesTreeMatches $Paths.target $Manifest.oldSnapshot
    Assert-LiliesDisplacedShortcuts $Paths $Manifest.shortcuts
    Assert-LiliesShortcutState $Manifest.shortcuts 'after'
    Write-LiliesInstallEvent $Paths 'restored'
    return [pscustomobject]@{transactionId=$Paths.id; state='restored'; target=$Paths.target}
}

function Complete-LiliesUninstallTransaction {
    param([string]$TransactionId, [string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [scriptblock]$BeforeRemove, [scriptblock]$BeforeRollback, [scriptblock]$Checkpoint={})
    if ($null -eq $BeforeRemove -or $null -eq $BeforeRollback) { throw 'Uninstall requires recovery and quiescence guards.' }
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $TransactionId
    $lock = Open-LiliesInstallLock $paths
    try {
        Assert-LiliesNoOtherPendingTransaction $paths
        $manifest = Read-LiliesUninstallManifest $paths
        $states = Get-LiliesInstallStates $paths
        if ('restored' -in $states) { throw 'This uninstall was already restored.' }
        Assert-LiliesUninstallEndpoints $paths $manifest
        if ('committed' -in $states -and 'recovering' -notin $states) {
            if (Test-Path -LiteralPath $paths.target) { throw 'The uninstall target is occupied again.' }
            Assert-LiliesShortcutState $manifest.shortcuts 'before'
            return [pscustomobject]@{transactionId=$paths.id; state='uninstalled'; retained=$paths.previous; alreadyCommitted=$true}
        }
        if ('prepared' -notin $states -or 'moving-old' -in $states -or 'recovering' -in $states) {
            throw 'Interrupted uninstall requires explicit recovery, not replay.'
        }
        Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot
        Assert-LiliesShortcutState $manifest.shortcuts 'after'
        # Unknown process/recovery status never enters file rollback. Nothing
        # has been moved yet; leave the prepared transaction intact.
        & $BeforeRemove $paths | Out-Null
        Assert-LiliesUninstallEndpoints $paths $manifest
        Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot
        Assert-LiliesShortcutState $manifest.shortcuts 'after'
        try {
            & $Checkpoint 'before-uninstall-program-move' $paths | Out-Null
            Write-LiliesInstallEvent $paths 'moving-old'
            Move-LiliesProgramDirectory $paths.target $paths.previous $paths
            & $Checkpoint 'after-uninstall-program-move' $paths | Out-Null
            Write-LiliesInstallEvent $paths 'old-moved'
            Restore-LiliesShortcuts $paths $manifest.shortcuts $Checkpoint
            if ($null -ne $manifest.shortcuts) { Write-LiliesInstallEvent $paths 'shortcuts-applied' }
            & $Checkpoint 'before-uninstall-commit' $paths | Out-Null
            Assert-LiliesUninstallEndpoints $paths $manifest
            if (Test-Path -LiteralPath $paths.target) { throw 'The uninstall target was recreated.' }
            Assert-LiliesShortcutState $manifest.shortcuts 'before'
            Write-LiliesInstallEvent $paths 'committed'
        } catch {
            $failure = $_.Exception
            try {
                & $BeforeRollback $paths | Out-Null
                Restore-LiliesUninstallUnlocked $paths $manifest | Out-Null
            } catch {
                throw [AggregateException]::new('Uninstall and recovery both failed; all remaining files and journal are retained.', [Exception[]]@($failure, $_.Exception))
            }
            throw $failure
        }
        return [pscustomobject]@{transactionId=$paths.id; state='uninstalled'; retained=$paths.previous; alreadyCommitted=$false}
    } finally { $lock.Dispose() }
}

function Restore-LiliesUninstallTransaction {
    param([string]$TransactionId, [string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [switch]$RollbackCommitted, [scriptblock]$BeforeRestore, [scriptblock]$Checkpoint={})
    if ($null -eq $BeforeRestore) { throw 'Uninstall recovery requires a read-only quiescence guard.' }
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $TransactionId
    $lock = Open-LiliesInstallLock $paths
    try {
        & $BeforeRestore $paths | Out-Null
        Assert-LiliesNoOtherPendingTransaction $paths
        if (Test-LiliesUnpreparedJournal $paths) {
            return [pscustomobject]@{transactionId=$paths.id; state='not-prepared'; target=$paths.target}
        }
        $manifest = Read-LiliesUninstallManifest $paths
        $states = Get-LiliesInstallStates $paths
        if ('committed' -in $states -and -not $RollbackCommitted) { throw 'Completed uninstall requires explicit rollback.' }
        if ('restored' -in $states) {
            Assert-LiliesUninstallEndpoints $paths $manifest
            Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot
            Assert-LiliesShortcutState $manifest.shortcuts 'after'
            return [pscustomobject]@{transactionId=$paths.id; state='restored'; alreadyRestored=$true}
        }
        return Restore-LiliesUninstallUnlocked $paths $manifest $Checkpoint
    } finally { $lock.Dispose() }
}
