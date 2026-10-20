# Startup helpers used only below install_windows' unconditional release gate.
# Keep the exact Process object/handle; never terminate by a reusable PID alone.
. (Join-Path $PSScriptRoot 'install_probe.ps1')

function New-LiliesLaunchState([string]$Executable) {
    return @{
        executable = Get-LiliesPhysicalPath $Executable
        attempted = $false; process = $null; handle = [IntPtr]::Zero
        identityReady = $false; createdFileTime = [long]0; processId = 0
        stopped = $false; health = $null; healthProbe = $null; healthProbeQuiescent = $true
    }
}

function Start-LiliesOwnedChild([hashtable]$State, [string[]]$Arguments = @()) {
    if ($State.attempted) { throw 'A startup attempt cannot be replayed.' }
    $State.attempted = $true
    $parameters = @{
        FilePath = $State.executable; WorkingDirectory = (Split-Path -Parent $State.executable)
        WindowStyle = 'Hidden'; PassThru = $true; ErrorAction = 'Stop'
    }
    if ($Arguments.Count) { $parameters.ArgumentList = $Arguments }
    $State.process = Start-Process @parameters
    # Capture the object before querying metadata: a failed query cannot be
    # treated as proof that no child was created.
    $State.handle = $State.process.Handle
    $State.processId = $State.process.Id
    # A fast recovery-only child may have completed before metadata queries.
    # Its held Process object still supplies exit status; it cannot pass the
    # main app's health check because identityReady remains false.
    if ($State.process.HasExited) { return }
    $State.createdFileTime = $State.process.StartTime.ToFileTimeUtc()
    if ([IO.Path]::GetFullPath($State.process.Path) -ine $State.executable -or
        $State.handle -eq [IntPtr]::Zero) { throw 'Started child identity could not be verified.' }
    $State.identityReady = $true
}

function Assert-LiliesStartupHealth {
    param([hashtable]$State, [string]$Python, [string]$Verifier,
          [ValidateRange(100, 60000)][int]$TimeoutMilliseconds=35000)
    $State.health = $null
    if (-not $State.identityReady) { throw 'No verified startup child exists.' }
    if (-not (Test-LiliesHeldIdentity $State)) { throw 'The new Lilies process exited before its health check.' }
    $verifierPath = Get-LiliesPhysicalPath $Verifier
    $reply = Invoke-LiliesHealthProbe -Owner $State -Python $Python -TimeoutMilliseconds $TimeoutMilliseconds `
        -Arguments @('-I', '-S', '-B', '-X', 'utf8', $verifierPath, '--pid', [string]$State.processId,
                     '--executable', $State.executable, '--created-filetime', [string]$State.createdFileTime)
    $verifierExitCode = $reply.exitCode
    # Windows PowerShell unwraps a one-element JSON array. Reject the outer
    # shape before conversion so [valid-looking-health] is not an object.
    if ($reply.output -isnot [string] -or -not $reply.output.Trim().StartsWith('{') -or
        -not $reply.output.Trim().EndsWith('}')) { throw 'Startup health must return one JSON object.' }
    try { $health = $reply.output | ConvertFrom-Json -ErrorAction Stop }
    catch { throw 'Startup health did not return a valid result.' }
    foreach ($field in @('schemaVersion', 'processId', 'stableMilliseconds', 'elapsedMilliseconds', 'healthySamples')) {
        $value = $health.$field
        if ($value -isnot [int] -and $value -isnot [long]) { throw 'Startup health returned an invalid numeric field.' }
    }
    if ($verifierExitCode -ne 0 -or $health.passed -isnot [bool] -or -not $health.passed -or
        $health.schemaVersion -ne 1 -or $health.processId -ne $State.processId -or
        $health.stableMilliseconds -lt 3000 -or $health.stableMilliseconds -gt 60000 -or
        $health.elapsedMilliseconds -lt $health.stableMilliseconds -or $health.elapsedMilliseconds -gt 60000 -or
        $health.healthySamples -lt 2 -or $health.healthySamples -gt 1000) {
        throw 'The new Lilies process did not pass authenticated Qt startup health.'
    }
    if (-not (Test-LiliesHeldIdentity $State)) { throw 'The new Lilies process exited after its health check.' }
    # Persist only typed, content-free fields, never extra stdout properties.
    $State.health = [ordered]@{
        schemaVersion=1; passed=$true; processId=$health.processId
        stableMilliseconds=$health.stableMilliseconds; elapsedMilliseconds=$health.elapsedMilliseconds
        healthySamples=$health.healthySamples
        scope='Owned-process authenticated Qt startup heartbeat; not visual or performance acceptance'
    }
}

function Stop-LiliesOwnedChild([hashtable]$State) {
    if (-not $State.attempted -or $State.stopped) { return }
    if ($null -eq $State.process) { throw 'Startup outcome is unknown; do not restore files beneath a possible child.' }
    $State.process.Refresh()
    if (-not $State.process.HasExited) {
        if (-not $State.identityReady -or $State.handle -eq [IntPtr]::Zero -or
            $State.process.Handle -ne $State.handle -or $State.process.Id -ne $State.processId -or
            $State.process.StartTime.ToFileTimeUtc() -ne $State.createdFileTime -or
            [IO.Path]::GetFullPath($State.process.Path) -ine $State.executable) {
            throw 'Startup child identity changed or is unavailable; no process was terminated.'
        }
        try { $State.process.Kill() }
        catch {
            # A verified child can finish between the alive check and Kill.
            # Only the held object's exited state makes that race benign.
            $State.process.Refresh()
            if (-not $State.process.HasExited) { throw }
        }
        if (-not $State.process.WaitForExit(10000)) { throw 'The owned startup child has not exited; preserve program files.' }
    }
    $State.stopped = $true
}

function Close-LiliesLaunchHandle([hashtable]$State) {
    if ($null -ne $State.process) { $State.process.Dispose() }
    $State.handle = [IntPtr]::Zero
}

function Test-LiliesHeldIdentity([hashtable]$State) {
    $State.process.Refresh()
    if ($State.process.HasExited) { return $false }
    if (-not $State.identityReady -or $State.handle -eq [IntPtr]::Zero -or
        $State.process.Handle -ne $State.handle -or $State.process.Id -ne $State.processId -or
        $State.process.StartTime.ToFileTimeUtc() -ne $State.createdFileTime -or
        [IO.Path]::GetFullPath($State.process.Path) -ine $State.executable) {
        throw 'Held process identity is unavailable or changed; preserve all program files.'
    }
    return (-not $State.process.HasExited)
}

function Get-LiliesImageLeases([string]$Executable) {
    $executablePath = Get-LiliesPhysicalPath $Executable
    $name = [IO.Path]::GetFileNameWithoutExtension($executablePath)
    $processes = @()
    $leases = [Collections.Generic.List[hashtable]]::new()
    $completed = $false
    try {
        try { $processes = @(Get-Process -Name $name -ErrorAction Stop) }
        catch {
            if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenName,*') { return }
            throw 'Cannot enumerate related image processes; preserve files for explicit recovery.'
        }
        if ($processes.Count -gt 32) { throw 'Too many same-name processes to verify safely.' }
        foreach ($process in $processes) {
            # Acquire a real handle before path/time queries. No subsequent
            # termination may look up this PID again and hit a replacement.
            $handle = $process.Handle
            if ($handle -eq [IntPtr]::Zero) { throw 'An image process handle is unavailable.' }
            $process.Refresh()
            if ($process.HasExited) { continue }
            if ([string]::IsNullOrWhiteSpace($process.Path)) { throw 'An image process path is unavailable.' }
            if ([IO.Path]::GetFullPath($process.Path) -ine $executablePath) { continue }
            $lease = New-LiliesLaunchState $executablePath
            $lease.process = $process
            $lease.handle = $handle
            $lease.processId = $process.Id
            $lease.createdFileTime = $process.StartTime.ToFileTimeUtc()
            $lease.identityReady = $true
            $lease.attempted = $true
            if (Test-LiliesHeldIdentity $lease) { $leases.Add($lease) }
        }
        $completed = $true
        $leases.ToArray()
    } finally {
        foreach ($process in $processes) {
            $retained = $false
            if ($completed) {
                foreach ($lease in $leases) {
                    if ([object]::ReferenceEquals($lease.process, $process)) { $retained = $true; break }
                }
            }
            if (-not $retained) { $process.Dispose() }
        }
    }
}

function Assert-LiliesImagesQuiescent([string[]]$Executables) {
    foreach ($executable in $Executables) {
        $leases = @(Get-LiliesImageLeases $executable)
        try {
            if ($leases.Count) { throw 'Close related Lilies processes before file recovery; no process was stopped.' }
        } finally {
            foreach ($lease in $leases) { Close-LiliesLaunchHandle $lease }
        }
    }
}

function Get-LiliesExistingProcessRole([hashtable]$State) {
    if (-not (Test-LiliesHeldIdentity $State)) { return 'exited' }
    # CommandLine is used only transiently to separate the main application
    # from its watchdog. CIM is NOT termination authority. Match its creation
    # time and path back to the held process on both sides of the query.
    # Access denied/missing metadata refuses upgrade; never guess "main".
    try {
        $records = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $State.processId) `
            -Property ProcessId,ExecutablePath,CreationDate,CommandLine -OperationTimeoutSec 5 -ErrorAction Stop)
    } catch { throw 'Cannot identify the existing Lilies role; close it explicitly before upgrading.' }
    if (-not (Test-LiliesHeldIdentity $State)) { return 'exited' }
    if ($records.Count -ne 1) { throw 'Existing Lilies process metadata is missing or ambiguous.' }
    $record = $records[0]
    if (($record.ProcessId -isnot [int] -and $record.ProcessId -isnot [long] -and $record.ProcessId -isnot [uint32]) -or
        $record.ProcessId -ne $State.processId -or $record.CreationDate -isnot [DateTime] -or
        [string]::IsNullOrWhiteSpace($record.ExecutablePath) -or
        [IO.Path]::GetFullPath($record.ExecutablePath) -ine $State.executable -or
        $record.CommandLine -isnot [string] -or [string]::IsNullOrWhiteSpace($record.CommandLine)) {
        throw 'Existing Lilies process metadata does not match the held process.'
    }
    # CIM datetime has microsecond resolution; FILETIME uses 100ns ticks.
    # Integer remainders avoid loss of precision from double division.
    $cimTime = $record.CreationDate.ToFileTimeUtc()
    $heldTime = $State.createdFileTime
    if (($cimTime - ($cimTime % [long]10)) -ne ($heldTime - ($heldTime % [long]10))) {
        throw 'Existing Lilies creation identity changed; no process was stopped.'
    }
    $command = [regex]::Match($record.CommandLine, '\A\s*(?:"(?<image>[^"]+)"|(?<image>[^\s"]+))(?=\s|\z)(?<args>[\s\S]*)\z')
    if (-not $command.Success -or
        [IO.Path]::GetFullPath($command.Groups['image'].Value) -ine $State.executable) {
        throw 'Existing Lilies command identity is not recognized.'
    }
    $arguments = $command.Groups['args'].Value.Trim()
    if ($arguments -cin @('', '--compact', '--visual', '--login-shell')) { $role = 'main' }
    elseif ($arguments -ceq '--restore' -or $arguments -cmatch '^--watchdog(?:\s|$)') { $role = 'passive-recovery' }
    else { throw 'Unrecognized Lilies process role; leave diagnostic and helper processes untouched.' }
    if (-not (Test-LiliesHeldIdentity $State)) { return 'exited' }
    return $role
}

function Wait-LiliesImageExit([string]$Executable, [ValidateRange(0, 60)][int]$TimeoutSeconds = 15) {
    # Watchdogs must finish their own shell/peek restoration and release DLLs.
    # Never kill an unowned same-name process merely to force file recovery.
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $leases = @(Get-LiliesImageLeases $Executable)
        try { if (-not $leases.Count) { return } }
        finally { foreach ($lease in $leases) { Close-LiliesLaunchHandle $lease } }
        if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) { throw 'Related image processes remain; preserve files for explicit recovery.' }
        Start-Sleep -Milliseconds 200
    } while ($true)
}

function Stop-LiliesForUpgrade {
    param([string]$TargetExecutable, [string]$RecoveryExecutable, [scriptblock]$ValidateRecovery, [switch]$NoLaunch)
    if ($null -eq $ValidateRecovery) { throw 'A staged payload verifier is required before shutdown.' }
    Assert-LiliesImagesQuiescent @($RecoveryExecutable)
    & $ValidateRecovery | Out-Null
    $restoreRequired = Test-Path -LiteralPath $TargetExecutable -PathType Leaf
    $leases = @(Get-LiliesImageLeases $TargetExecutable)
    try {
        if ($NoLaunch -and $leases.Count) { throw 'No-launch upgrade cannot interrupt a running companion.' }
        $restoreRequired = $restoreRequired -or $leases.Count -gt 0
        # Validate the entire set before stopping any main process.
        foreach ($lease in $leases) { $lease.role = Get-LiliesExistingProcessRole $lease }
        foreach ($lease in $leases) {
            if (-not (Test-LiliesHeldIdentity $lease)) { $lease.role = 'exited' }
        }
        foreach ($lease in $leases) {
            if ($lease.role -eq 'main') { Stop-LiliesOwnedChild $lease }
        }
    } finally { foreach ($lease in $leases) { Close-LiliesLaunchHandle $lease } }
    # The old main can no longer re-hide restored chrome. Watchdogs/recovery
    # helpers must finish naturally; never terminate them to unlock files.
    Wait-LiliesImageExit $TargetExecutable
    if ($restoreRequired) {
        & $ValidateRecovery | Out-Null
        $recovery = New-LiliesLaunchState $RecoveryExecutable
        try {
            Start-LiliesOwnedChild $recovery @('--restore')
            if (-not $recovery.process.WaitForExit(20000)) { throw 'Pre-upgrade shell restoration timed out.' }
            $recovery.process.Refresh()
            if ($recovery.process.ExitCode -ne 0) { throw 'Pre-upgrade shell restoration was not verified.' }
        } finally {
            try { Stop-LiliesOwnedChild $recovery }
            finally { Close-LiliesLaunchHandle $recovery }
        }
    }
    Assert-LiliesImagesQuiescent @($TargetExecutable, $RecoveryExecutable)
}

function Restore-LiliesAfterStartupFailure([hashtable]$State) {
    if (-not $State.attempted) { return }
    Stop-LiliesOwnedChild $State
    Wait-LiliesImageExit $State.executable
    if (-not $State.healthProbeQuiescent) { throw 'Health verifier quiescence is unknown; preserve program files for explicit recovery.' }
    # Recover Explorer from the still-intact new image before any directory
    # move. Failure or unknown processes leave the pending transaction intact.
    $recovery = New-LiliesLaunchState $State.executable
    try {
        Start-LiliesOwnedChild $recovery @('--restore')
        if (-not $recovery.process.WaitForExit(20000)) { throw 'Startup-failure shell restoration timed out.' }
        $recovery.process.Refresh()
        if ($recovery.process.ExitCode -ne 0) { throw 'Startup-failure shell restoration failed.' }
    } finally {
        try { Stop-LiliesOwnedChild $recovery }
        finally { Close-LiliesLaunchHandle $recovery }
    }
    Wait-LiliesImageExit $State.executable
}
