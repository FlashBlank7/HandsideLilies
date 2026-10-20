# Program files and optional four-entry shortcut plan. No process control,
# shell state, credentials, database migration or recursive deletion here.
# The caller must quiesce the exact installed app before activation/recovery.
. (Join-Path $PSScriptRoot 'install_shortcuts.ps1')

function Get-LiliesPhysicalPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) {
        throw 'An absolute install path is required.'
    }
    # Only ordinary drive-letter paths; UNC/device/ADS and Win32 alias names
    # must not make the validated destination resolve to a different tree.
    if ($Path -notmatch '^[A-Za-z]:[\\/]' -or $Path.Substring(2).Contains(':')) {
        throw 'Only ordinary local drive paths are supported.'
    }
    # .NET Framework may strip these aliases in GetFullPath, so inspect the
    # original spelling before canonicalization as well.
    foreach ($segment in $Path.Replace('/', '\').Substring(3).Split('\')) {
        if ($segment -match '[. ]$') { throw 'Ambiguous trailing-dot or trailing-space path.' }
    }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\')) { throw 'A drive root is not an install path.' }
    $ancestor = $full
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked install paths are forbidden: $ancestor"
            }
        }
        $next = Split-Path -Parent $ancestor
        if ($next -eq $ancestor) { break }
        $ancestor = $next
    }
    return $full
}

function Get-LiliesStringHash([string]$Value) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Value)))).Replace('-', '') }
    finally { $hash.Dispose() }
}

function Get-LiliesProgramFileHash([string]$Path) {
    # Avoid profile/module-path-dependent Get-FileHash availability in a
    # Windows PowerShell child launched from a different PowerShell version.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($stream))).Replace('-', '') }
    finally { $hash.Dispose(); $stream.Dispose() }
}

function Get-LiliesTransactionPaths([string]$Target, [string]$AllowedBase, [string]$JournalRoot, [string]$Id) {
    $targetPath = Get-LiliesPhysicalPath $Target
    $basePath = Get-LiliesPhysicalPath $AllowedBase
    $journalBase = Get-LiliesPhysicalPath $JournalRoot
    if ((Split-Path -Parent $targetPath) -ine $basePath) { throw 'Install target must be an immediate child of the allowed program directory.' }
    if ((Split-Path -Leaf $targetPath) -like '.lilies-*') { throw 'Reserved installation target name.' }
    if ([IO.Path]::GetPathRoot($journalBase) -ine 'F:\') { throw 'Install journals must stay on F drive.' }
    foreach ($left in @($targetPath, $basePath)) {
        if ($journalBase -ieq $left -or $journalBase.StartsWith($left + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $left.StartsWith($journalBase + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Program directories and journal storage must not overlap.'
        }
    }
    if ($Id -cnotmatch '^[a-f0-9]{32}$') { throw 'Invalid install transaction identity.' }
    $key = Get-LiliesStringHash $targetPath.ToLowerInvariant()
    $journalParent = Join-Path $journalBase $key
    return [pscustomobject]@{
        id = $Id; target = $targetPath; allowedBase = $basePath; journalBase = $journalBase
        journalParent = $journalParent; journal = (Join-Path $journalParent $Id)
        stage = (Join-Path $basePath ('.lilies-stage-' + $Id))
        previous = (Join-Path $basePath ('.lilies-previous-' + $Id))
        failed = (Join-Path $basePath ('.lilies-failed-' + $Id))
    }
}

function Open-LiliesInstallLock($Paths) {
    Get-LiliesPhysicalPath $Paths.journalParent | Out-Null
    [IO.Directory]::CreateDirectory($Paths.journalParent) | Out-Null
    $lockPath = Join-Path $Paths.journalParent 'transaction.lock'
    Get-LiliesPhysicalPath $lockPath | Out-Null
    # Windows releases the handle on process death. A retained lock file is not
    # treated as proof of a live owner and never requires deleting a stale PID.
    return [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
}

function Write-LiliesExclusiveJson([string]$Path, $Value) {
    Get-LiliesPhysicalPath $Path | Out-Null
    if (Test-Path -LiteralPath $Path) { throw 'An immutable install record already exists.' }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 12 -Compress))
    # Only fully flushed records enter the manifest/event namespace. A process
    # death during Write leaves a .pending file, not a truncated final event.
    # Retain that partial file; recovery ignores it and checks actual trees.
    $pending = Join-Path (Split-Path -Parent $Path) ('.pending-' + [guid]::NewGuid().ToString('N'))
    Get-LiliesPhysicalPath $pending | Out-Null
    $stream = [IO.File]::Open($pending, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    [IO.File]::Move($pending, $Path)
}

function Write-LiliesInstallEvent($Paths, [string]$State) {
    Write-LiliesExclusiveJson (Join-Path $Paths.journal ('event-' + [guid]::NewGuid().ToString('N') + '.json')) ([ordered]@{
        schemaVersion = 1; transactionId = $Paths.id; state = $State
        at = [DateTimeOffset]::UtcNow.ToString('o')
    })
}

function Get-LiliesInstallStates($Paths) {
    $states = [Collections.Generic.List[string]]::new()
    foreach ($file in @(Get-ChildItem -LiteralPath $Paths.journal -File -Filter 'event-*.json' -Force)) {
        Get-LiliesPhysicalPath $file.FullName | Out-Null
        $event = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($event.schemaVersion -ne 1 -or $event.transactionId -cne $Paths.id -or
            $event.state -cnotin @('prepared', 'moving-old', 'old-moved', 'moving-new', 'activated', 'shortcuts-applied', 'committed', 'recovering', 'quarantined', 'restored')) {
            throw 'Invalid install event. Preserve all directories for recovery.'
        }
        $states.Add([string]$event.state)
    }
    return ,$states.ToArray()
}

function Assert-LiliesNoOtherPendingTransaction($Paths) {
    # All callers hold this target's exclusive lock. A committed transaction
    # is no longer terminal once an explicit rollback has started: death in
    # that rollback must be recovered before any other transaction can act.
    foreach ($directory in @(Get-ChildItem -LiteralPath $Paths.journalParent -Directory -Force)) {
        if ($directory.Name -ceq $Paths.id) { continue }
        $prior = Get-LiliesTransactionPaths $Paths.target $Paths.allowedBase $Paths.journalBase $directory.Name
        if (Test-LiliesUnpreparedJournal $prior) { continue }
        Read-LiliesInstallManifest $prior | Out-Null
        $states = Get-LiliesInstallStates $prior
        $terminal = ('restored' -in $states) -or (
            'committed' -in $states -and 'recovering' -notin $states
        )
        if (-not $terminal) {
            throw "An interrupted install must be recovered first: $($prior.id)"
        }
    }
}

function Get-LiliesTreeSnapshot([string]$Root) {
    $rootPath = Get-LiliesPhysicalPath $Root
    if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) { throw "Missing program directory: $rootPath" }
    $prefix = $rootPath + '\'
    $queue = [Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($rootPath)
    $files = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    $directories = [Collections.Generic.List[string]]::new()
    while ($queue.Count) {
        foreach ($item in @(Get-ChildItem -LiteralPath $queue.Dequeue() -Force -ErrorAction Stop)) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked program entry is forbidden: $($item.Name)" }
            $absolute = [IO.Path]::GetFullPath($item.FullName)
            if (-not $absolute.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Program inventory escaped its root.' }
            $relative = $absolute.Substring($prefix.Length)
            if ($item.PSIsContainer) {
                $directories.Add($relative); $queue.Enqueue($absolute)
            } else {
                $files.Add($relative, [pscustomobject]@{
                    path = $relative; length = [long]$item.Length
                    sha256 = Get-LiliesProgramFileHash $absolute
                })
            }
        }
    }
    [string[]]$keys = @($files.Keys)
    [Array]::Sort($keys, [StringComparer]::Ordinal)
    [string[]]$dirs = $directories.ToArray()
    [Array]::Sort($dirs, [StringComparer]::Ordinal)
    return [pscustomobject]@{ files = [object[]]@($keys | ForEach-Object { $files[$_] }); directories = [string[]]$dirs }
}

function Test-LiliesTreeMatches([string]$Root, $Snapshot) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return $false }
    $current = Get-LiliesTreeSnapshot $Root
    return ($current | ConvertTo-Json -Depth 8 -Compress) -ceq ($Snapshot | ConvertTo-Json -Depth 8 -Compress)
}

function Assert-LiliesTreeMatches([string]$Root, $Snapshot) {
    if (-not (Test-LiliesTreeMatches $Root $Snapshot)) { throw "Program files changed or are incomplete: $Root" }
}

function Read-LiliesInstallManifest($Paths) {
    Get-LiliesPhysicalPath $Paths.journal | Out-Null
    $path = Join-Path $Paths.journal 'manifest.json'
    Get-LiliesPhysicalPath $path | Out-Null
    $manifest = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    $isUninstall = $manifest.schemaVersion -eq 3 -and $manifest.operation -ceq 'uninstall'
    if ($manifest.schemaVersion -notin @(1, 2, 3) -or $manifest.transactionId -cne $Paths.id -or
        $manifest.target -ine $Paths.target -or $manifest.hadPrevious -isnot [bool] -or
        (-not $isUninstall -and $null -eq $manifest.newSnapshot) -or
        ($manifest.hadPrevious -and $null -eq $manifest.oldSnapshot)) {
        throw 'Invalid or mismatched install manifest. No directories have been moved.'
    }
    if ($manifest.schemaVersion -eq 3 -and (-not $isUninstall -or -not $manifest.hadPrevious -or
        $null -ne $manifest.newSnapshot)) { throw 'Invalid uninstall transaction manifest.' }
    if ($manifest.schemaVersion -eq 2 -and $null -eq $manifest.shortcuts) { throw 'Shortcut plan is missing from this transaction.' }
    if ($manifest.schemaVersion -eq 1 -and $null -ne $manifest.shortcuts) { throw 'Legacy transaction cannot include shortcut effects.' }
    Assert-LiliesShortcutPlan $manifest.shortcuts $Paths
    return $manifest
}

function Test-LiliesOwnedTarget($Paths) {
    $marker = Join-Path $Paths.target '.lilies-install-transaction.json'
    if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) { return $false }
    Get-LiliesPhysicalPath $marker | Out-Null
    try { $value = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $false }
    return $value.transactionId -ceq $Paths.id -and $value.target -ieq $Paths.target -and $value.schemaVersion -eq 1
}

function Test-LiliesUnpreparedJournal($Paths) {
    Get-LiliesPhysicalPath $Paths.journal | Out-Null
    if (-not (Test-Path -LiteralPath $Paths.journal -PathType Container)) { return $false }
    # The immutable manifest is flushed before stage creation or any rename.
    # An empty journal/partial pending record can be left by process death at
    # that boundary. Ignore it only when no managed program tree or final event
    # exists. Missing/corrupt manifests after preparation still fail closed.
    if (Test-Path -LiteralPath (Join-Path $Paths.journal 'manifest.json')) { return $false }
    foreach ($managed in @($Paths.stage, $Paths.previous, $Paths.failed)) {
        Get-LiliesPhysicalPath $managed | Out-Null
        if (Test-Path -LiteralPath $managed) { return $false }
    }
    if (Test-LiliesOwnedTarget $Paths) { return $false }
    foreach ($entry in @(Get-ChildItem -LiteralPath $Paths.journal -Force -ErrorAction Stop)) {
        Get-LiliesPhysicalPath $entry.FullName | Out-Null
        if ($entry.PSIsContainer -or $entry.Name -cnotmatch '^\.pending-[a-f0-9]{32}$') { return $false }
    }
    return $true
}

function Move-LiliesProgramDirectory([string]$From, [string]$To, $Paths) {
    $source = Get-LiliesPhysicalPath $From
    $destination = Get-LiliesPhysicalPath $To
    $owned = @($Paths.target, $Paths.stage, $Paths.previous, $Paths.failed)
    if ($source -notin $owned -or $destination -notin $owned -or
        (Split-Path -Parent $source) -ine $Paths.allowedBase -or
        (Split-Path -Parent $destination) -ine $Paths.allowedBase -or
        (Test-Path -LiteralPath $destination)) { throw 'Refusing an unsafe or overwriting program directory move.' }
    # Reject nested junctions too, before moving any tree. No recursive delete.
    Get-LiliesTreeSnapshot $source | Out-Null
    [IO.Directory]::Move($source, $destination)
}

function Restore-LiliesInstallUnlocked($Paths, $Manifest, [scriptblock]$Checkpoint = {}) {
    Assert-LiliesDisplacedShortcuts $Paths $Manifest.shortcuts
    Assert-LiliesShortcutState $Manifest.shortcuts
    # Reject a stale rollback (or damaged backup) BEFORE publishing recovery
    # intent. Otherwise a harmless wrong-order request leaves a false pending
    # owner that blocks recovery of the newer installation it never owned.
    if (Test-Path -LiteralPath $Paths.previous) {
        if (-not $Manifest.hadPrevious) { throw 'Unexpected previous directory; manual inspection is required.' }
        Assert-LiliesTreeMatches $Paths.previous $Manifest.oldSnapshot
        if (Test-Path -LiteralPath $Paths.target) {
            if (-not (Test-LiliesOwnedTarget $Paths)) { throw 'Target no longer belongs to this transaction; it was not replaced.' }
            if (Test-Path -LiteralPath $Paths.failed) { throw 'Recovery quarantine already exists; preserve all files for inspection.' }
        }
    } elseif ($Manifest.hadPrevious) {
        Assert-LiliesTreeMatches $Paths.target $Manifest.oldSnapshot
    } elseif (Test-Path -LiteralPath $Paths.target) {
        if (-not (Test-LiliesOwnedTarget $Paths)) { throw 'Unexpected fresh-install target; it was not replaced.' }
        if (Test-Path -LiteralPath $Paths.failed) { throw 'Recovery quarantine already exists; preserve all files for inspection.' }
    }
    Write-LiliesInstallEvent $Paths 'recovering'
    Restore-LiliesShortcuts $Paths $Manifest.shortcuts $Checkpoint
    if (Test-Path -LiteralPath $Paths.previous) {
        if (-not $Manifest.hadPrevious) { throw 'Unexpected previous directory; manual inspection is required.' }
        Assert-LiliesTreeMatches $Paths.previous $Manifest.oldSnapshot
        if (Test-Path -LiteralPath $Paths.target) {
            if (-not (Test-LiliesOwnedTarget $Paths)) { throw 'Target no longer belongs to this transaction; it was not replaced.' }
            Move-LiliesProgramDirectory $Paths.target $Paths.failed $Paths
            & $Checkpoint 'after-quarantine' $Paths | Out-Null
            Write-LiliesInstallEvent $Paths 'quarantined'
        }
        Assert-LiliesTreeMatches $Paths.previous $Manifest.oldSnapshot
        Move-LiliesProgramDirectory $Paths.previous $Paths.target $Paths
        & $Checkpoint 'after-old-restore' $Paths | Out-Null
        Assert-LiliesTreeMatches $Paths.target $Manifest.oldSnapshot
    } elseif ($Manifest.hadPrevious) {
        # Before old-move or after a completed restore with an interrupted log.
        Assert-LiliesTreeMatches $Paths.target $Manifest.oldSnapshot
    } elseif (Test-Path -LiteralPath $Paths.target) {
        if (-not (Test-LiliesOwnedTarget $Paths)) { throw 'Unexpected fresh-install target; it was not replaced.' }
        Move-LiliesProgramDirectory $Paths.target $Paths.failed $Paths
        & $Checkpoint 'after-quarantine' $Paths | Out-Null
        Write-LiliesInstallEvent $Paths 'quarantined'
    }
    Write-LiliesInstallEvent $Paths 'restored'
    return [pscustomobject]@{ transactionId = $Paths.id; state = 'restored'; target = $Paths.target; retainedStage = $Paths.stage; retainedFailed = $Paths.failed }
}

function New-LiliesInstallTransaction {
    param([string]$SourceRoot, [string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [string]$InstallManifestJson, $ShortcutPlan = $null, [scriptblock]$ValidatePayload = {}, [scriptblock]$Checkpoint = {})
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot ([guid]::NewGuid().ToString('N'))
    $source = Get-LiliesPhysicalPath $SourceRoot
    if ($source -ieq $paths.allowedBase -or $source.StartsWith($paths.allowedBase + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $paths.allowedBase.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Install source and target directory must not overlap.' }
    $lock = Open-LiliesInstallLock $paths
    try {
        Assert-LiliesNoOtherPendingTransaction $paths
        Assert-LiliesShortcutPlan $ShortcutPlan $paths
        Assert-LiliesShortcutState $ShortcutPlan 'before'
        $sourceSnapshot = Get-LiliesTreeSnapshot $source
        if (@($sourceSnapshot.files).Count -eq 0) { throw 'Cannot stage an empty release.' }
        if (@($sourceSnapshot.files | Where-Object { $_.path -in @('install-manifest.json', '.lilies-install-transaction.json') }).Count) {
            throw 'Source contains installation-owned metadata.'
        }
        & $ValidatePayload $source | Out-Null
        $hadPrevious = Test-Path -LiteralPath $paths.target
        $oldSnapshot = if ($hadPrevious) { Get-LiliesTreeSnapshot $paths.target } else { $null }
        foreach ($reserved in @($paths.stage, $paths.previous, $paths.failed, $paths.journal)) {
            Get-LiliesPhysicalPath $reserved | Out-Null
            if (Test-Path -LiteralPath $reserved) { throw 'Transaction destination already exists.' }
        }
        # Immutable intent exists before creating a program staging directory.
        $marker = [ordered]@{ schemaVersion = 1; transactionId = $paths.id; target = $paths.target }
        $markerJson = $marker | ConvertTo-Json -Depth 5 -Compress
        $newFiles = [Collections.Generic.List[object]]::new()
        foreach ($file in $sourceSnapshot.files) { $newFiles.Add($file) }
        $extra = [ordered]@{ '.lilies-install-transaction.json' = $markerJson }
        if ($InstallManifestJson) { $extra['install-manifest.json'] = $InstallManifestJson }
        foreach ($name in $extra.Keys) {
            $newFiles.Add([pscustomobject]@{ path = $name; length = [long][Text.Encoding]::UTF8.GetByteCount($extra[$name]); sha256 = (Get-LiliesStringHash $extra[$name]) })
        }
        # Match the deterministic ordinal ordering used by live inventories.
        $byName = @{}
        foreach ($file in $newFiles) { $byName[$file.path] = $file }
        [string[]]$names = @($byName.Keys); [Array]::Sort($names, [StringComparer]::Ordinal)
        $newSnapshot = [pscustomobject]@{ files = [object[]]@($names | ForEach-Object { $byName[$_] }); directories = $sourceSnapshot.directories }
        [IO.Directory]::CreateDirectory($paths.journal) | Out-Null
        $manifest = [ordered]@{ schemaVersion = 1; transactionId = $paths.id; target = $paths.target; hadPrevious = [bool]$hadPrevious; oldSnapshot = $oldSnapshot; newSnapshot = $newSnapshot }
        if ($null -ne $ShortcutPlan) { $manifest.schemaVersion = 2; $manifest['shortcuts'] = $ShortcutPlan }
        & $Checkpoint 'before-manifest' $paths | Out-Null
        Write-LiliesExclusiveJson (Join-Path $paths.journal 'manifest.json') $manifest
        try {
            [IO.Directory]::CreateDirectory($paths.stage) | Out-Null
            & $Checkpoint 'stage-created' $paths | Out-Null
            foreach ($relative in $sourceSnapshot.directories) { [IO.Directory]::CreateDirectory((Join-Path $paths.stage $relative)) | Out-Null }
            foreach ($file in $sourceSnapshot.files) {
                & $Checkpoint 'copy-file' $paths | Out-Null
                [IO.File]::Copy((Join-Path $source $file.path), (Join-Path $paths.stage $file.path), $false)
            }
            Assert-LiliesTreeMatches $source $sourceSnapshot
            Assert-LiliesTreeMatches $paths.stage $sourceSnapshot
            foreach ($name in $extra.Keys) {
                [IO.File]::WriteAllText((Join-Path $paths.stage $name), [string]$extra[$name], [Text.UTF8Encoding]::new($false))
            }
            & $Checkpoint 'stage-copied' $paths | Out-Null
            Assert-LiliesTreeMatches $paths.stage $newSnapshot
            & $ValidatePayload $paths.stage | Out-Null
            Write-LiliesInstallEvent $paths 'prepared'
        } catch {
            # No old files have moved yet. Retain incomplete staged files and
            # the immutable intent for diagnosis; no cleanup can delete data.
            Write-LiliesInstallEvent $paths 'restored'
            throw
        }
        return [pscustomobject]@{ transactionId = $paths.id; state = 'prepared'; journal = $paths.journal; stage = $paths.stage; previous = $paths.previous }
    } finally { $lock.Dispose() }
}

function Complete-LiliesInstallTransaction {
    param([string]$TransactionId, [string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [scriptblock]$AfterActivate = {}, [scriptblock]$BeforeRollback = {}, [scriptblock]$Checkpoint = {},
          [scriptblock]$BeforeActivate = {})
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $TransactionId
    $lock = Open-LiliesInstallLock $paths
    try {
        Assert-LiliesNoOtherPendingTransaction $paths
        $manifest = Read-LiliesInstallManifest $paths
        if ($manifest.schemaVersion -eq 3) { throw 'Use the explicit uninstall transaction entrypoint.' }
        $states = Get-LiliesInstallStates $paths
        if ('restored' -in $states) { throw 'This transaction was already restored.' }
        Assert-LiliesDisplacedShortcuts $paths $manifest.shortcuts
        if ('committed' -in $states) {
            Assert-LiliesTreeMatches $paths.target $manifest.newSnapshot
            Assert-LiliesShortcutState $manifest.shortcuts 'after'
            return [pscustomobject]@{ transactionId = $paths.id; state = 'committed'; previous = $paths.previous; alreadyCommitted = $true }
        }
        if ('prepared' -notin $states -or 'moving-old' -in $states -or 'moving-new' -in $states -or 'recovering' -in $states) { throw 'Interrupted activation requires explicit recovery, not replay.' }
        Assert-LiliesTreeMatches $paths.stage $manifest.newSnapshot
        Assert-LiliesShortcutState $manifest.shortcuts 'before'
        if ($manifest.hadPrevious) { Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot }
        elseif (Test-Path -LiteralPath $paths.target) { throw 'The previously absent target now exists.' }
        # Process quiescence runs under the target lock, but OUTSIDE the file
        # rollback catch: an unknown running child must leave stage/old intact.
        & $BeforeActivate $paths | Out-Null
        Assert-LiliesTreeMatches $paths.stage $manifest.newSnapshot
        Assert-LiliesShortcutState $manifest.shortcuts 'before'
        if ($manifest.hadPrevious) { Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot }
        elseif (Test-Path -LiteralPath $paths.target) { throw 'The previously absent target now exists.' }
        try {
            & $Checkpoint 'before-old-move' $paths | Out-Null
            if ($manifest.hadPrevious) {
                Write-LiliesInstallEvent $paths 'moving-old'
                Move-LiliesProgramDirectory $paths.target $paths.previous $paths
                & $Checkpoint 'after-old-move' $paths | Out-Null
                Write-LiliesInstallEvent $paths 'old-moved'
            }
            Write-LiliesInstallEvent $paths 'moving-new'
            Move-LiliesProgramDirectory $paths.stage $paths.target $paths
            & $Checkpoint 'after-new-move' $paths | Out-Null
            Write-LiliesInstallEvent $paths 'activated'
            Assert-LiliesTreeMatches $paths.target $manifest.newSnapshot
            Set-LiliesShortcutsAfter $paths $manifest.shortcuts $Checkpoint
            if ($null -ne $manifest.shortcuts) { Write-LiliesInstallEvent $paths 'shortcuts-applied' }
            & $AfterActivate | Out-Null
            & $Checkpoint 'before-commit' $paths | Out-Null
            Assert-LiliesDisplacedShortcuts $paths $manifest.shortcuts
            Assert-LiliesTreeMatches $paths.target $manifest.newSnapshot
            Assert-LiliesShortcutState $manifest.shortcuts 'after'
            Write-LiliesInstallEvent $paths 'committed'
        } catch {
            $activationFailure = $_.Exception
            try {
                & $BeforeRollback | Out-Null
                Restore-LiliesInstallUnlocked $paths $manifest | Out-Null
            }
            catch { throw [AggregateException]::new('Install and recovery both failed; all directories and journal are retained.', [Exception[]]@($activationFailure, $_.Exception)) }
            throw $activationFailure
        }
        return [pscustomobject]@{ transactionId = $paths.id; state = 'committed'; previous = $paths.previous; alreadyCommitted = $false }
    } finally { $lock.Dispose() }
}

function Restore-LiliesInstallTransaction {
    param([string]$TransactionId, [string]$Target, [string]$AllowedBase, [string]$JournalRoot,
          [switch]$RollbackCommitted, [scriptblock]$Checkpoint = {}, [scriptblock]$BeforeRestore = {})
    $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $TransactionId
    $lock = Open-LiliesInstallLock $paths
    try {
        & $BeforeRestore $paths | Out-Null
        if (Test-LiliesUnpreparedJournal $paths) {
            return [pscustomobject]@{ transactionId = $paths.id; state = 'not-prepared'; target = $paths.target }
        }
        Assert-LiliesNoOtherPendingTransaction $paths
        $manifest = Read-LiliesInstallManifest $paths
        if ($manifest.schemaVersion -eq 3) { throw 'Use the explicit uninstall recovery entrypoint.' }
        $states = Get-LiliesInstallStates $paths
        if ('committed' -in $states -and -not $RollbackCommitted) { throw 'Completed installation requires an explicit rollback request.' }
        if ('restored' -in $states) {
            Assert-LiliesDisplacedShortcuts $paths $manifest.shortcuts
            Assert-LiliesShortcutState $manifest.shortcuts 'before'
            if ($manifest.hadPrevious) { Assert-LiliesTreeMatches $paths.target $manifest.oldSnapshot }
            elseif (Test-Path -LiteralPath $paths.target) { throw 'A different installation now occupies the target.' }
            return [pscustomobject]@{ transactionId = $paths.id; state = 'restored'; alreadyRestored = $true }
        }
        return Restore-LiliesInstallUnlocked $paths $manifest $Checkpoint
    } finally { $lock.Dispose() }
}
