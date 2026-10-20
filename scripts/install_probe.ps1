# One read-only, stdlib-only health verifier. No shell, window, log file or
# process-name termination. Its held Process object is the only kill authority.

function Get-LiliesProbePython([string]$Python) {
    $executable = Get-LiliesPhysicalPath $Python
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf) -or
        (Split-Path -Leaf $executable) -ine 'python.exe') { throw 'A local CPython interpreter is required for startup health.' }
    $parent = Split-Path -Parent $executable
    if ((Split-Path -Leaf $parent) -ieq 'Scripts') {
        $config = Get-LiliesPhysicalPath (Join-Path (Split-Path -Parent $parent) 'pyvenv.cfg')
        if (-not (Test-Path -LiteralPath $config -PathType Leaf) -or (Get-Item -LiteralPath $config).Length -gt 16384) {
            throw 'Virtual-environment interpreter identity is unavailable.'
        }
        $settings = [regex]::Matches((Get-Content -LiteralPath $config -Raw -Encoding UTF8), '(?m)^home[ \t]*=[ \t]*(.+)$')
        if ($settings.Count -ne 1) { throw 'Virtual-environment base interpreter is ambiguous.' }
        $pythonHome = Get-LiliesPhysicalPath $settings[0].Groups[1].Value.Trim()
        $base = Get-LiliesPhysicalPath (Join-Path $pythonHome 'python.exe')
        if ($base -ieq $executable -or (Split-Path -Leaf $pythonHome) -ieq 'Scripts' -or
            -not (Test-Path -LiteralPath $base -PathType Leaf)) { throw 'Virtual-environment base interpreter cannot be verified.' }
        # Windows venv python.exe is a redirector that can leave a real Python
        # child behind if only the redirector is stopped. The fixed health
        # verifier needs no venv/site packages; run its base interpreter.
        return $base
    }
    return $executable
}

function ConvertTo-LiliesWindowsArgument([string]$Value) {
    if ($Value.Contains([string][char]0)) { throw 'A process argument cannot contain NUL.' }
    # Windows CRT quoting, not PowerShell/cmd escaping. Double backslashes
    # before quotes and at the closing quote; even an empty argument survives.
    $quoted = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
    return '"' + $quoted + '"'
}

function Invoke-LiliesHealthProbe {
    param([hashtable]$Owner, [string]$Python, [string[]]$Arguments,
          [ValidateRange(100, 60000)][int]$TimeoutMilliseconds=35000)
    if ($null -eq $Owner -or ($Owner.ContainsKey('healthProbeQuiescent') -and -not $Owner.healthProbeQuiescent)) {
        throw 'A previous health verifier has an unknown exit state; retain its held identity before retrying.'
    }
    $pythonPath = Get-LiliesProbePython $Python
    $probe = New-LiliesLaunchState $pythonPath
    $Owner.healthProbe = $probe
    $Owner.healthProbeQuiescent = $false
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $process.StartInfo.FileName = $pythonPath
    $process.StartInfo.WorkingDirectory = Get-LiliesPhysicalPath $PSScriptRoot
    $process.StartInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-LiliesWindowsArgument $_ }) -join ' ')
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.CreateNoWindow = $true
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $probe.process = $process
    $output = [IO.MemoryStream]::new()
    $channels = @()
    $timer = [Diagnostics.Stopwatch]::StartNew()
    try {
        $probe.attempted = $true
        try {
            if (-not $process.Start()) { throw 'No process was returned.' }
        } catch { throw 'Health verifier could not be started; startup outcome must be checked.' }
        $probe.handle = $process.Handle
        $probe.processId = $process.Id
        if (-not $process.HasExited) {
            $probe.createdFileTime = $process.StartTime.ToFileTimeUtc()
            if ($probe.handle -eq [IntPtr]::Zero -or [IO.Path]::GetFullPath($process.Path) -ine $pythonPath) {
                throw 'Health verifier identity is unavailable.'
            }
            $probe.identityReady = $true
        }
        $process.StandardInput.Close()
        $channels = @(
            @{stream=$process.StandardOutput.BaseStream; buffer=[byte[]]::new(4096); task=$null; done=$false; capture=$true},
            @{stream=$process.StandardError.BaseStream; buffer=[byte[]]::new(4096); task=$null; done=$false; capture=$false}
        )
        $bytes = 0
        do {
            if ($timer.ElapsedMilliseconds -ge $TimeoutMilliseconds) { throw 'Startup health verifier timed out.' }
            foreach ($channel in $channels) {
                if ($channel.done) { continue }
                if ($null -eq $channel.task) { $channel.task = $channel.stream.ReadAsync($channel.buffer, 0, $channel.buffer.Length) }
                if (-not $channel.task.IsCompleted) { continue }
                try { $count = $channel.task.GetAwaiter().GetResult() }
                catch { throw 'Health verifier pipe read failed.' }
                $channel.task = $null
                if ($count -eq 0) { $channel.done=$true; continue }
                $bytes += $count
                if ($bytes -gt 65536) { throw 'Health verifier exceeded the output limit.' }
                if ($channel.capture) { $output.Write($channel.buffer, 0, $count) }
            }
            $process.Refresh()
            $finished = $process.HasExited -and $channels[0].done -and $channels[1].done
            if (-not $finished) { [Threading.Thread]::Sleep(10) }
        } while (-not $finished)
        try { $text = [Text.UTF8Encoding]::new($false, $true).GetString($output.ToArray()) }
        catch { throw 'Health verifier output is not valid UTF-8.' }
        return [pscustomobject]@{output=$text; exitCode=$process.ExitCode}
    } finally {
        $quiet = $false
        try {
            Stop-LiliesOwnedChild $probe
            $quiet = $probe.stopped
            # The fixed verifier never spawns workers. Do not block disposing
            # an unresolved read, or claim cleanup if a pipe remains held.
            foreach ($channel in $channels) {
                if ($null -ne $channel.task -and -not $channel.task.IsCompleted) {
                    try { if (-not $channel.task.Wait(1000)) { $quiet=$false } }
                    catch { if (-not $channel.task.IsCompleted) { $quiet=$false } }
                }
            }
        } catch { $quiet=$false }
        $output.Dispose()
        if ($quiet) {
            foreach ($channel in $channels) { $channel.stream.Dispose() }
            Close-LiliesLaunchHandle $probe
            $Owner.healthProbeQuiescent = $true
        } else {
            # Preserve the held object for explicit handling. A failed stop
            # must prevent application file rollback; never look up a new PID.
            $Owner.healthProbeQuiescent = $false
            throw 'Health verifier exit was not verified; preserve program files.'
        }
    }
}
