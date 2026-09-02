param(
    [string]$Executable,
    [string]$ReportPath,
    [ValidateRange(5, 60)]
    [int]$TimeoutSeconds = 25
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $ProjectRoot 'dist\LiliesInTheBox\LiliesInTheBox.exe'
}
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $ProjectRoot 'artifacts\packaged-windows-startup-v0353.json'
}
$Executable = [IO.Path]::GetFullPath($Executable)
$ReportPath = [IO.Path]::GetFullPath($ReportPath)
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged executable is missing: $Executable"
}

function Assert-JsonBoolean(
    [object]$Object,
    [string]$PropertyName,
    [bool]$Expected
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or $property.Value -isnot [bool] -or
        [bool]$property.Value -ne $Expected) {
        throw "Windows startup report.$PropertyName must be the JSON boolean $Expected."
    }
}

function Get-RequiredJsonInteger(
    [object]$Object,
    [string]$PropertyName
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or
        ($property.Value -isnot [int] -and $property.Value -isnot [long])) {
        throw "Windows startup report.$PropertyName must be a JSON integer."
    }
    return [long]$property.Value
}

function Remove-ExactProbeDirectory([string]$Path, [string]$TemporaryRoot) {
    $resolvedRoot = [IO.Path]::GetFullPath($TemporaryRoot).TrimEnd('\')
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ([IO.Path]::GetFullPath((Split-Path -Parent $resolvedPath)).TrimEnd('\') -cne
        $resolvedRoot -or
        (Split-Path -Leaf $resolvedPath) -notlike 'lilies-windows-startup-probe-*') {
        throw "Refusing startup-probe cleanup outside its exact temporary directory: $resolvedPath"
    }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            return
        }
        if (-not (Test-Path -LiteralPath $resolvedPath -PathType Container)) {
            throw "Refusing to delete an unexpected startup-probe path type: $resolvedPath"
        }
        try {
            [IO.Directory]::Delete($resolvedPath, $true)
        } catch [System.IO.IOException] {
            if ($attempt -eq 5) { throw }
        } catch [System.UnauthorizedAccessException] {
            if ($attempt -eq 5) { throw }
        }
        if (Test-Path -LiteralPath $resolvedPath) {
            Start-Sleep -Milliseconds ([int](80 * [Math]::Pow(2, $attempt - 1)))
        }
    }
}

$reportParent = Split-Path -Parent $ReportPath
[IO.Directory]::CreateDirectory($reportParent) | Out-Null
if (Test-Path -LiteralPath $ReportPath) {
    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw "Refusing to replace a non-file startup report path: $ReportPath"
    }
    [IO.File]::Delete($ReportPath)
}

$temporaryRoot = [IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot 'artifacts\windows-startup-probes')
).TrimEnd('\')
[IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
$diagnosticRoot = [IO.Path]::GetFullPath((Join-Path $temporaryRoot (
    'lilies-windows-startup-probe-' + [Guid]::NewGuid().ToString('N')
)))
[IO.Directory]::CreateDirectory($diagnosticRoot) | Out-Null
$localAppDataSentinel = Join-Path $diagnosticRoot 'local-app-data-sentinel'
[IO.Directory]::CreateDirectory($localAppDataSentinel) | Out-Null
$oldDataRoot = $env:LILIES_DATA_DIR
$oldLocalAppData = $env:LOCALAPPDATA
$process = $null
$timedOut = $false
try {
    $env:LILIES_DATA_DIR = $diagnosticRoot
    $env:LOCALAPPDATA = $localAppDataSentinel
    $quotedReport = '"' + $ReportPath + '"'
    $process = Start-Process -FilePath $Executable `
        -ArgumentList @('--windows-startup-probe', $quotedReport) `
        -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $timedOut = $true
        throw "Hidden qwindows startup probe timed out after $TimeoutSeconds seconds."
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) {
        throw "Hidden qwindows startup probe exited with code $($process.ExitCode)."
    }
    $localAppDataEntries = @(
        Get-ChildItem -LiteralPath $localAppDataSentinel -Force -Recurse
    )
    # User32/Shell may materialize empty Microsoft\Windows\Caches directories
    # when an icon is resolved. Those directories contain no Lilies data. Fail
    # on every file, every Lilies-named entry and every recognizable Qt cache
    # path instead of misclassifying empty OS-owned directory scaffolding.
    $localAppDataFiles = @(
        $localAppDataEntries | Where-Object { -not $_.PSIsContainer }
    )
    $liliesOrQtCacheEntries = @(
        $localAppDataEntries | Where-Object {
            $_.Name -match '(?i)lilies([ -]?in[ -]?the[ -]?box)?' -or
            $_.Name -match '(?i)^(qmlcache|qtpipelinecache.*|qt-rhi-pipeline-cache.*|shadercache)$' -or
            $_.Extension -ieq '.qmlc'
        }
    )
    if ($localAppDataFiles.Count -ne 0 -or
        $liliesOrQtCacheEntries.Count -ne 0) {
        $localAppDataLeaks = @(
            $localAppDataFiles
            $liliesOrQtCacheEntries
        ) | Sort-Object FullName -Unique
        $leakSummary = ($localAppDataLeaks | ForEach-Object FullName) -join ', '
        throw "Packaged startup wrote a file or Lilies/Qt cache path outside LILIES_DATA_DIR via LOCALAPPDATA: $leakSummary"
    }
} finally {
    if ($null -ne $process) {
        $process.Refresh()
        if (-not $process.HasExited) {
            # This is the exact diagnostic child created above; it never owns
            # the real shell or user data and is safe to terminate on timeout.
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
    }
    $env:LILIES_DATA_DIR = $oldDataRoot
    if ($null -eq $oldLocalAppData) {
        Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue
    } else {
        $env:LOCALAPPDATA = $oldLocalAppData
    }
    Remove-ExactProbeDirectory -Path $diagnosticRoot -TemporaryRoot $temporaryRoot
}

if ($timedOut -or -not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
    throw "Hidden qwindows startup report is missing: $ReportPath"
}
$report = Get-Content -LiteralPath $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
$executableItem = Get-Item -LiteralPath $Executable
$executableHash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
if ([int]$report.schemaVersion -ne 1) {
    throw 'Windows startup report schemaVersion must be 1.'
}
if ([string]$report.applicationVersion -cne
    [string]$executableItem.VersionInfo.ProductVersion) {
    throw 'Windows startup report application version does not match the executable.'
}
if ([string]$report.executableSha256 -cne $executableHash) {
    throw 'Windows startup report executable hash does not match the executable.'
}
if ([string]$report.probeKind -cne 'hidden-qwindows-cold-start' -or
    [string]$report.diagnosticPlatform -cne 'windows') {
    throw 'Windows startup report did not use the hidden qwindows probe.'
}
foreach ($name in @(
    'qmlLoaded',
    'nativeWindowCreated',
    'nativeWindowIdCached',
    'nativeHitTestDispatched',
    'nativeRadialWorldHit',
    'nativeDesktopModeTabHit',
    'nativeTransparentCornerPass',
    'systemMoveWatcherReady',
    'systemMoveWatcherWindowMatches',
    'systemMoveWatcherEventsObserved',
    'rootNoActivateStyle',
    'petNoActivateStyle',
    'eventLoopResponsive',
    'passed'
)) {
    Assert-JsonBoolean $report $name $true
}
Assert-JsonBoolean $report 'trayPublished' $false
if ((Get-RequiredJsonInteger $report 'nativeDesktopModeTabHitResult') -ne 1) {
    throw 'Windows startup report.nativeDesktopModeTabHitResult must be HTCLIENT (1).'
}
if ([int]$report.nativeHitTestCount -lt 1 -or
    [int]$report.nativeDispatchCount -lt [int]$report.nativeHitTestCount) {
    throw 'Windows startup probe did not traverse the native event filter.'
}
if ((Get-RequiredJsonInteger $report 'systemMoveWatcherStartCount') -lt 1 -or
    (Get-RequiredJsonInteger $report 'systemMoveWatcherEndCount') -lt 1) {
    throw 'Windows startup probe did not traverse the native move watcher.'
}
if ([int]$report.eventLoopTicks -lt 4) {
    throw 'Windows startup probe did not demonstrate a responsive Qt event loop.'
}
if ([int]$report.quickWindowCount -lt 1 -or
    [int]$report.visibleQuickWindowCount -ne 0 -or
    @($report.visibleQuickWindows).Count -ne 0) {
    throw 'Windows startup probe published a visible QML window.'
}
if (-not [string]::IsNullOrEmpty([string]$report.dispatchError)) {
    throw "Windows startup native dispatch failed: $($report.dispatchError)"
}
$qtCacheRouting = $report.PSObject.Properties['qtCacheRouting'].Value
if ($null -eq $qtCacheRouting) {
    throw 'Windows startup report.qtCacheRouting is missing.'
}
Assert-JsonBoolean $qtCacheRouting 'pathsWithinDataRoot' $true
Assert-JsonBoolean $qtCacheRouting 'environmentApplied' $true
Assert-JsonBoolean $qtCacheRouting 'qtShaderDiskCacheDisabled' $true
Assert-JsonBoolean $qtCacheRouting 'passed' $true
$expectedDataRoot = [IO.Path]::GetFullPath($diagnosticRoot).TrimEnd('\')
if ([IO.Path]::GetFullPath([string]$qtCacheRouting.dataRoot).TrimEnd('\') -cne
    $expectedDataRoot) {
    throw 'Qt cache routing dataRoot does not match the isolated diagnostic root.'
}
foreach ($cachePath in @(
    [string]$qtCacheRouting.cacheRoot,
    [string]$qtCacheRouting.qmlDiskCachePath,
    [string]$qtCacheRouting.rhiPipelineCacheLoadPath,
    [string]$qtCacheRouting.rhiPipelineCacheSavePath
)) {
    $resolvedCachePath = [IO.Path]::GetFullPath($cachePath)
    if (-not $resolvedCachePath.StartsWith(
        $expectedDataRoot + '\',
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Qt cache path escaped the isolated diagnostic root: $resolvedCachePath"
    }
}

$report | ConvertTo-Json -Depth 5
