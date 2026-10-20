# Four fixed Windows-managed entrypoints, coordinated by install_transaction.
# Durable old/new bytes and retained temporary files live in its F-drive
# journal. No shortcut is executed, no registry values or other links touched.

function Get-LiliesBytesHash([byte[]]$Bytes) {
    $hash = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hash.ComputeHash($Bytes))).Replace('-', '') }
    finally { $hash.Dispose() }
}

function Get-LiliesShortcutPath([string]$Root, [string]$Role) {
    $names = @{
        primary = 'Lilies in the box.lnk'
        desktop = 'Lilies in the box - Desktop.lnk'
        pet = 'Lilies in the box - Pet only.lnk'
        startup = 'Startup\Lilies in the box.lnk'
    }
    if ($Role -cnotin @('primary', 'desktop', 'pet', 'startup')) { throw 'Unknown Lilies shortcut role.' }
    $base = Get-LiliesPhysicalPath $Root
    $path = Get-LiliesPhysicalPath (Join-Path $base $names[$Role])
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $path) -PathType Container)) {
        throw 'The Windows shortcut parent directory must already exist.'
    }
    if ((Test-Path -LiteralPath $path) -and -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw 'A shortcut destination is not an ordinary file.'
    }
    return $path
}

function ConvertTo-LiliesShortcutSnapshot([byte[]]$Bytes) {
    if ($null -eq $Bytes -or $Bytes.Length -lt 1 -or $Bytes.Length -gt 262144) {
        throw 'Shortcut payload must be between 1 byte and 256 KiB.'
    }
    return [pscustomobject]@{
        length = $Bytes.Length; sha256 = Get-LiliesBytesHash $Bytes
        data = [Convert]::ToBase64String($Bytes)
    }
}

function Read-LiliesShortcutBytes([string]$Path) {
    Get-LiliesPhysicalPath $Path | Out-Null
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or $item.Length -lt 1 -or $item.Length -gt 262144) {
        throw 'Existing shortcut has an unsupported size or type; preserve it.'
    }
    return ,([IO.File]::ReadAllBytes($Path))
}

function ConvertFrom-LiliesShortcutSnapshot($Value) {
    if ($null -eq $Value -or $Value.data -isnot [string] -or $Value.data.Length -gt 349528 -or
        $Value.sha256 -cnotmatch '^[A-F0-9]{64}$' -or $Value.length -lt 1 -or $Value.length -gt 262144) {
        throw 'Invalid shortcut backup metadata.'
    }
    [byte[]]$bytes = [Convert]::FromBase64String($Value.data)
    if ($bytes.Length -ne $Value.length -or (Get-LiliesBytesHash $bytes) -cne $Value.sha256) {
        throw 'Shortcut backup integrity check failed.'
    }
    return ,$bytes
}

function New-LiliesShortcutPlan([string]$ShortcutRoot, [hashtable]$Payloads) {
    if ($Payloads.Count -lt 1 -or $Payloads.Count -gt 4) { throw 'Expected one to four named Lilies shortcuts.' }
    $entries = [Collections.Generic.List[object]]::new()
    foreach ($role in @($Payloads.Keys | Sort-Object)) {
        $path = Get-LiliesShortcutPath $ShortcutRoot $role
        if ($Payloads[$role] -isnot [byte[]]) { throw 'Shortcut payloads must be bytes, not a command or path.' }
        $before = if (Test-Path -LiteralPath $path) {
            ConvertTo-LiliesShortcutSnapshot (Read-LiliesShortcutBytes $path)
        } else { $null }
        $entries.Add([pscustomobject]@{
            role = $role; before = $before
            after = ConvertTo-LiliesShortcutSnapshot $Payloads[$role]
        })
    }
    return [pscustomobject]@{ schemaVersion = 1; root = (Get-LiliesPhysicalPath $ShortcutRoot); entries = $entries.ToArray() }
}

function Assert-LiliesShortcutPlan($Plan, $Paths) {
    if ($null -eq $Plan) { return }
    if ($Plan.schemaVersion -ne 1 -or @($Plan.entries).Count -lt 1 -or @($Plan.entries).Count -gt 4) {
        throw 'Invalid shortcut plan.'
    }
    $root = Get-LiliesPhysicalPath $Plan.root
    foreach ($other in @($Paths.allowedBase, $Paths.journalBase)) {
        if ($root -ieq $other -or $root.StartsWith($other + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $other.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Shortcut, program and journal directories must not overlap.'
        }
    }
    $seen = @{}
    foreach ($entry in $Plan.entries) {
        Get-LiliesShortcutPath $root $entry.role | Out-Null
        if ($seen.ContainsKey($entry.role)) { throw 'Duplicate shortcut role.' }
        $seen[$entry.role] = $true
        ConvertFrom-LiliesShortcutSnapshot $entry.after | Out-Null
        if ($null -ne $entry.before) { ConvertFrom-LiliesShortcutSnapshot $entry.before | Out-Null }
    }
}

function Get-LiliesShortcutState($Plan, $Entry) {
    $path = Get-LiliesShortcutPath $Plan.root $Entry.role
    if (-not (Test-Path -LiteralPath $path)) {
        if ($null -eq $Entry.before) { return 'before' }
        throw 'A previous shortcut disappeared; no file was overwritten.'
    }
    $hash = Get-LiliesBytesHash (Read-LiliesShortcutBytes $path)
    if ($null -ne $Entry.before -and $hash -ceq $Entry.before.sha256) { return 'before' }
    if ($hash -ceq $Entry.after.sha256) { return 'after' }
    throw 'A shortcut changed outside this transaction; preserve it for review.'
}

function Assert-LiliesShortcutState($Plan, [string]$Expected = '') {
    if ($null -eq $Plan) { return }
    foreach ($entry in $Plan.entries) {
        $state = Get-LiliesShortcutState $Plan $entry
        # Identical old/new bytes satisfy either endpoint without a rewrite.
        $identical = $null -ne $entry.before -and $entry.before.sha256 -ceq $entry.after.sha256
        if ($Expected -and $state -cne $Expected -and -not $identical) { throw 'Shortcut state does not match this transaction phase.' }
    }
}

function Get-LiliesShortcutTemp($Paths, $Plan, $Entry, [string]$Purpose) {
    if ($Purpose -cnotin @('apply', 'restore', 'remove', 'replaced')) { throw 'Invalid shortcut temporary role.' }
    $parent = Split-Path -Parent (Get-LiliesShortcutPath $Plan.root $Entry.role)
    return Get-LiliesPhysicalPath (Join-Path $parent ('.lilies-' + $Paths.id + '-' + $Entry.role + '-' + $Purpose + '.pending'))
}

function Assert-LiliesDisplacedShortcutBytes($Entry, [string]$Purpose, [string]$Hash) {
    if ($Purpose -cnotin @('replaced', 'remove')) { return }
    $known = @($Entry.after.sha256)
    if ($Purpose -ceq 'replaced' -and $null -ne $Entry.before) { $known += $Entry.before.sha256 }
    if ($Hash -cnotin $known) { throw 'Displaced shortcut contains an external change; preserve all files for review.' }
}

function Assert-LiliesDisplacedShortcuts($Paths, $Plan) {
    if ($null -eq $Plan) { return }
    foreach ($entry in $Plan.entries) {
        foreach ($purpose in @('replaced', 'remove')) {
            $path = Get-LiliesShortcutTemp $Paths $Plan $entry $purpose
            if (-not (Test-Path -LiteralPath $path)) { continue }
            # Atomic replacement/removal may race an external writer. A
            # transaction-owned *name* does not prove ownership of its bytes.
            # Stop before any program/link recovery intent if displaced bytes
            # are not one of the expected endpoints. Keep them in place.
            $hash = Get-LiliesBytesHash (Read-LiliesShortcutBytes $path)
            Assert-LiliesDisplacedShortcutBytes $entry $purpose $hash
        }
    }
}

function Preserve-LiliesShortcutTemp($Paths, $Plan, $Entry, [string]$Purpose) {
    $path = Get-LiliesShortcutTemp $Paths $Plan $Entry $Purpose
    if (-not (Test-Path -LiteralPath $path)) { return }
    # This is an exact transaction-owned temporary name, never a user's .lnk.
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer -or $item.Length -gt 262144) { throw 'Unexpected shortcut temporary file; preserve it in place.' }
    [byte[]]$bytes = [IO.File]::ReadAllBytes($path)
    $hash = Get-LiliesBytesHash $bytes
    # Recheck the actual bytes here, not only at recovery entry: another
    # writer may have changed a displaced file since the earlier preflight.
    Assert-LiliesDisplacedShortcutBytes $Entry $Purpose $hash
    $retained = Get-LiliesPhysicalPath (Join-Path $Paths.journal ('s-' + $Entry.role + '-' + $Purpose + '-' + $hash.Substring(0, 16) + '.bin'))
    if (-not (Test-Path -LiteralPath $retained)) {
        $pending = Get-LiliesPhysicalPath (Join-Path $Paths.journal ('.pending-' + [guid]::NewGuid().ToString('N')))
        $stream = [IO.File]::Open($pending, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
        finally { $stream.Dispose() }
        [IO.File]::Move($pending, $retained)
    }
    if ((Get-LiliesProgramFileHash $retained) -cne $hash -or (Get-LiliesProgramFileHash $path) -cne $hash) {
        throw 'Shortcut temporary retention failed; both files were preserved.'
    }
    # Original shortcut bytes are already immutable in the F-drive manifest;
    # even partial temporaries are retained there before this exact removal.
    Get-LiliesPhysicalPath $path | Out-Null
    [IO.File]::Delete($path)
}

function Set-LiliesShortcutSnapshot($Paths, $Plan, $Entry, $Snapshot, [string]$Purpose, [string]$ExpectedState, [scriptblock]$Checkpoint = {}) {
    Assert-LiliesDisplacedShortcuts $Paths $Plan
    if ((Get-LiliesShortcutState $Plan $Entry) -cne $ExpectedState) { throw 'Shortcut changed before its atomic write.' }
    $path = Get-LiliesShortcutPath $Plan.root $Entry.role
    $temp = Get-LiliesShortcutTemp $Paths $Plan $Entry $Purpose
    Preserve-LiliesShortcutTemp $Paths $Plan $Entry $Purpose
    [byte[]]$bytes = ConvertFrom-LiliesShortcutSnapshot $Snapshot
    $stream = [IO.File]::Open($temp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    if ((Get-LiliesProgramFileHash $temp) -cne $Snapshot.sha256 -or (Get-LiliesShortcutState $Plan $Entry) -cne $ExpectedState) {
        throw 'Shortcut changed while preparing its write; preserve all files.'
    }
    if (Test-Path -LiteralPath $path) {
        # Windows PowerShell converts a null string argument to an empty path.
        # Use a real sibling backup instead, preserving even the exact bytes
        # displaced by ReplaceFile if another writer raced our preflight.
        Preserve-LiliesShortcutTemp $Paths $Plan $Entry 'replaced'
        $backup = Get-LiliesShortcutTemp $Paths $Plan $Entry 'replaced'
        & $Checkpoint ('before-shortcut-swap-' + $Entry.role) $Paths | Out-Null
        [IO.File]::Replace($temp, $path, $backup)
        & $Checkpoint ('after-shortcut-swap-' + $Entry.role) $Paths | Out-Null
        $expectedHash = if ($ExpectedState -ceq 'before') { $Entry.before.sha256 } else { $Entry.after.sha256 }
        if ((Get-LiliesProgramFileHash $backup) -cne $expectedHash) {
            throw 'Shortcut changed during replacement; displaced bytes are retained.'
        }
        Preserve-LiliesShortcutTemp $Paths $Plan $Entry 'replaced'
    }
    else {
        & $Checkpoint ('before-shortcut-swap-' + $Entry.role) $Paths | Out-Null
        [IO.File]::Move($temp, $path)
        & $Checkpoint ('after-shortcut-swap-' + $Entry.role) $Paths | Out-Null
    }
}

function Set-LiliesShortcutsAfter($Paths, $Plan, [scriptblock]$Checkpoint = {}) {
    if ($null -eq $Plan) { return }
    Assert-LiliesShortcutPlan $Plan $Paths
    Assert-LiliesDisplacedShortcuts $Paths $Plan
    Assert-LiliesShortcutState $Plan 'before'
    foreach ($entry in $Plan.entries) {
        & $Checkpoint ('before-shortcut-' + $entry.role) $Paths | Out-Null
        if ($null -eq $entry.before -or $entry.before.sha256 -cne $entry.after.sha256) {
            Set-LiliesShortcutSnapshot $Paths $Plan $entry $entry.after 'apply' 'before' $Checkpoint
        }
        & $Checkpoint ('after-shortcut-' + $entry.role) $Paths | Out-Null
    }
    Assert-LiliesShortcutState $Plan 'after'
}

function Restore-LiliesShortcuts($Paths, $Plan, [scriptblock]$Checkpoint = {}) {
    if ($null -eq $Plan) { return }
    Assert-LiliesShortcutPlan $Plan $Paths
    Assert-LiliesDisplacedShortcuts $Paths $Plan
    Assert-LiliesShortcutState $Plan
    foreach ($entry in $Plan.entries) {
        if ((Get-LiliesShortcutState $Plan $entry) -ceq 'after') {
            if ($null -ne $entry.before) {
                Set-LiliesShortcutSnapshot $Paths $Plan $entry $entry.before 'restore' 'after' $Checkpoint
            } else {
                $path = Get-LiliesShortcutPath $Plan.root $entry.role
                Preserve-LiliesShortcutTemp $Paths $Plan $entry 'remove'
                $retained = Get-LiliesShortcutTemp $Paths $Plan $entry 'remove'
                if ((Get-LiliesShortcutState $Plan $entry) -cne 'after') { throw 'Shortcut changed before removal.' }
                & $Checkpoint ('before-shortcut-remove-' + $entry.role) $Paths | Out-Null
                [IO.File]::Move($path, $retained)
                & $Checkpoint ('after-shortcut-remove-' + $entry.role) $Paths | Out-Null
                if ((Get-LiliesProgramFileHash $retained) -cne $entry.after.sha256) {
                    throw 'Shortcut changed during removal; displaced bytes are retained.'
                }
            }
        }
        & $Checkpoint ('after-shortcut-restore-' + $entry.role) $Paths | Out-Null
        foreach ($purpose in @('apply', 'restore', 'remove', 'replaced')) { Preserve-LiliesShortcutTemp $Paths $Plan $entry $purpose }
    }
    Assert-LiliesShortcutState $Plan 'before'
}

function New-LiliesShortcutPayloads([string]$Target, [string]$StagingDirectory, [bool]$IncludeStartup) {
    $targetPath = Get-LiliesPhysicalPath $Target
    $staging = Get-LiliesPhysicalPath $StagingDirectory
    if ([IO.Path]::GetPathRoot($staging) -ine 'F:\' -or (Test-Path -LiteralPath $staging)) {
        throw 'Shortcut generation needs a fresh F-drive staging directory.'
    }
    [IO.Directory]::CreateDirectory($staging) | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $payloads = @{}
    $arguments = @{ primary = ''; desktop = '--visual'; pet = '--compact' }
    try {
        foreach ($role in @('primary', 'desktop', 'pet')) {
            $path = Join-Path $staging ($role + '.lnk')
            $shortcut = $shell.CreateShortcut($path)
            try {
                $shortcut.TargetPath = Join-Path $targetPath 'LiliesInTheBox.exe'
                $shortcut.Arguments = $arguments[$role]
                $shortcut.WorkingDirectory = $targetPath
                $shortcut.IconLocation = (Join-Path $targetPath '_internal\assets\icons\lilies-app.ico') + ',0'
                $shortcut.Description = 'Lilies in the box'
                $shortcut.Save()
            } finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) | Out-Null }
            $payloads[$role] = Read-LiliesShortcutBytes $path
        }
    } finally { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null }
    if ($IncludeStartup) { $payloads['startup'] = $payloads['primary'] }
    return $payloads
}

function New-LiliesManagedShortcutPlan([string]$Target, [string]$ShortcutRoot, [string]$StagingDirectory, [bool]$StartAtLogin) {
    $startup = Get-LiliesShortcutPath $ShortcutRoot 'startup'
    $includeStartup = $StartAtLogin -or (Test-Path -LiteralPath $startup -PathType Leaf)
    $payloads = New-LiliesShortcutPayloads $Target $StagingDirectory $includeStartup
    return New-LiliesShortcutPlan $ShortcutRoot $payloads
}
