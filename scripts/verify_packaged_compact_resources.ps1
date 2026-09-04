param(
    [string]$Executable,
    [string]$SelfTestReport,
    [string]$ReportPath,
    [ValidateRange(3, 30)]
    [int]$SettleSeconds = 8
)

$ErrorActionPreference = 'Stop'

function Get-LiveProcessResourceSnapshot {
    param(
        [Parameter(Mandatory)]
        [object]$Process,
        [Parameter(Mandatory)]
        [ValidateRange(1, 2147483647)]
        [int]$ExpectedProcessId
    )

    $null = $Process.Refresh()
    if ($Process.HasExited -or [int]$Process.Id -ne $ExpectedProcessId) {
        throw 'Compact resource sampling requires the live diagnostic process.'
    }
    # Process properties are lazy and Refresh/termination invalidates them.
    # Materialize values, never retain the Process object as report evidence.
    $workingSetBytes = [long]$Process.WorkingSet64
    $privateBytes = [long]$Process.PrivateMemorySize64
    $threadCount = [int]$Process.Threads.Count
    $handleCount = [int]$Process.HandleCount
    $responding = [bool]$Process.Responding
    $moduleNames = [string[]]@(
        $Process.Modules | ForEach-Object { [string]$_.ModuleName }
    )
    $null = $Process.Refresh()
    if ($Process.HasExited) {
        throw 'The diagnostic process exited during resource sampling.'
    }
    if ($workingSetBytes -le 0 -or $privateBytes -le 0 -or
        $threadCount -le 0 -or $handleCount -le 0 -or
        $moduleNames.Count -le 0) {
        throw 'Live compact resource metrics and module enumeration must be positive.'
    }
    return [pscustomobject]@{
        processId = $ExpectedProcessId
        workingSetBytes = $workingSetBytes
        privateBytes = $privateBytes
        threads = $threadCount
        handles = $handleCount
        responding = $responding
        moduleNames = $moduleNames
        sampledWhileAlive = $true
        capturedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
}

function Remove-ExactDiagnosticDirectoryWithRetry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,
        [Parameter(Mandatory)]
        [string]$TemporaryRoot,
        [ValidateRange(2, 5)]
        [int]$MaxAttempts = 5,
        [ValidateRange(10, 2000)]
        [int]$InitialDelayMilliseconds = 80,
        [scriptblock]$DeleteDirectory = {
            param([string]$Target)
            [IO.Directory]::Delete($Target, $true)
        }
    )

    $resolvedRoot = [IO.Path]::GetFullPath($TemporaryRoot).TrimEnd('\')
    $resolvedPath = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $resolvedParent = [IO.Path]::GetFullPath(
        (Split-Path -Parent $resolvedPath)
    ).TrimEnd('\')
    $leaf = Split-Path -Leaf $resolvedPath
    if ($resolvedParent -cne $resolvedRoot -or
        $leaf -notlike 'lilies-packaged-resource-*') {
        throw [InvalidOperationException]::new(
            "Refusing diagnostic cleanup outside the exact temporary directory: $resolvedPath"
        )
    }

    $attemptFailures = [System.Collections.Generic.List[System.Exception]]::new()
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            return [pscustomobject]@{
                removed = $true
                attempts = $attempt - 1
            }
        }
        if (-not (Test-Path -LiteralPath $resolvedPath -PathType Container)) {
            $attemptFailures.Add([InvalidOperationException]::new(
                "Refusing to delete a non-directory diagnostic path: $resolvedPath"
            ))
            throw [AggregateException]::new(
                'Diagnostic cleanup found an unexpected path type.',
                $attemptFailures.ToArray()
            )
        }
        try {
            & $DeleteDirectory $resolvedPath
            if (Test-Path -LiteralPath $resolvedPath) {
                throw [IO.IOException]::new(
                    "Diagnostic directory still exists after delete attempt $attempt."
                )
            }
            return [pscustomobject]@{
                removed = $true
                attempts = $attempt
            }
        } catch [System.UnauthorizedAccessException] {
            $attemptFailures.Add($_.Exception)
        } catch [System.IO.IOException] {
            $attemptFailures.Add($_.Exception)
        } catch {
            $attemptFailures.Add($_.Exception)
            throw [AggregateException]::new(
                'Diagnostic cleanup failed with a non-transient error.',
                $attemptFailures.ToArray()
            )
        }

        if ($attempt -lt $MaxAttempts) {
            $delay = [Math]::Min(
                1000,
                $InitialDelayMilliseconds * [Math]::Pow(2, $attempt - 1)
            )
            Start-Sleep -Milliseconds ([int]$delay)
        }
    }

    throw [AggregateException]::new(
        "Diagnostic cleanup failed after $MaxAttempts attempts.",
        $attemptFailures.ToArray()
    )
}

function Assert-JsonBoolean(
    [object]$Object,
    [string]$PropertyName,
    [bool]$Expected,
    [string]$Context
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or $property.Value -isnot [bool] -or
        [bool]$property.Value -ne $Expected) {
        throw "$Context.$PropertyName must be the JSON boolean $Expected."
    }
}

function Get-RequiredJsonInteger(
    [object]$Object,
    [string]$PropertyName,
    [string]$Context
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or
        ($property.Value -isnot [int] -and $property.Value -isnot [long])) {
        throw "$Context.$PropertyName must be a JSON integer."
    }
    return [long]$property.Value
}

function Get-RequiredJsonNumber(
    [object]$Object,
    [string]$PropertyName,
    [string]$Context
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    $value = if ($null -ne $property) { $property.Value } else { $null }
    if ($null -eq $property -or
        ($value -isnot [System.Byte] -and $value -isnot [System.SByte] -and
            $value -isnot [System.Int16] -and $value -isnot [System.UInt16] -and
            $value -isnot [System.Int32] -and $value -isnot [System.UInt32] -and
            $value -isnot [System.Int64] -and $value -isnot [System.UInt64] -and
            $value -isnot [System.Single] -and $value -isnot [System.Double] -and
            $value -isnot [System.Decimal])) {
        throw "$Context.$PropertyName must be a JSON number."
    }
    return [double]$value
}

function Assert-JsonString(
    [object]$Object,
    [string]$PropertyName,
    [string]$Expected,
    [string]$Context
) {
    $property = $Object.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or $property.Value -isnot [string] -or
        [string]$property.Value -cne $Expected) {
        throw "$Context.$PropertyName must be '$Expected'."
    }
}

function Assert-FocusTimerAnimationReport([object]$SelfTest) {
    Assert-JsonBoolean $SelfTest 'focusTimerAnimationPassed' $true 'selfTest'
    $animationProperty = $SelfTest.PSObject.Properties['focusTimerAnimation']
    if ($null -eq $animationProperty -or $null -eq $animationProperty.Value) {
        throw 'selfTest.focusTimerAnimation is missing.'
    }
    $animation = $animationProperty.Value
    foreach ($name in @(
        'windowFound',
        'surfaceFound',
        'sequencesStrictlyIncreasing',
        'passed'
    )) {
        Assert-JsonBoolean $animation $name $true 'selfTest.focusTimerAnimation'
    }

    $expectedTransitions = [ordered]@{
        started = 'started'
        paused = 'paused'
        resumed = 'resumed'
        finished = 'finished'
    }
    $sequences = [System.Collections.Generic.List[long]]::new()
    foreach ($stageName in $expectedTransitions.Keys) {
        $stageProperty = $animation.PSObject.Properties[$stageName]
        if ($null -eq $stageProperty -or $null -eq $stageProperty.Value) {
            throw "selfTest.focusTimerAnimation.$stageName is missing."
        }
        $stage = $stageProperty.Value
        $context = "selfTest.focusTimerAnimation.$stageName"
        Assert-JsonBoolean $stage 'passed' $true $context
        Assert-JsonString $stage 'transitionKind' $expectedTransitions[$stageName] $context
        $sequence = Get-RequiredJsonInteger $stage 'sequence' $context
        if ($sequence -le 0) {
            throw "$context.sequence must be positive."
        }
        if ($sequences.Count -gt 0 -and $sequence -le $sequences[$sequences.Count - 1]) {
            throw 'Focus transition sequences must be strictly increasing.'
        }
        $sequences.Add($sequence)
    }

    $reportedProperty = $animation.PSObject.Properties['transitionSequences']
    if ($null -eq $reportedProperty) {
        throw 'selfTest.focusTimerAnimation.transitionSequences is missing.'
    }
    $reported = @($reportedProperty.Value)
    if ($reported.Count -ne 4) {
        throw 'selfTest.focusTimerAnimation.transitionSequences must contain four integers.'
    }
    for ($index = 0; $index -lt 4; $index++) {
        if (($reported[$index] -isnot [int] -and
                $reported[$index] -isnot [long]) -or
            [long]$reported[$index] -ne $sequences[$index]) {
            throw 'selfTest.focusTimerAnimation.transitionSequences must match the four stages.'
        }
    }

    $started = $animation.started
    Assert-JsonString $started 'backendState' 'running' 'selfTest.focusTimerAnimation.started'
    Assert-JsonString $started 'visualState' 'running' 'selfTest.focusTimerAnimation.started'
    foreach ($name in @('windowVisible', 'windowExposed', 'startAcknowledgementActive')) {
        Assert-JsonBoolean $started $name $true 'selfTest.focusTimerAnimation.started'
    }
    if ((Get-RequiredJsonNumber $started 'startPulse' 'selfTest.focusTimerAnimation.started') -le 0.01 -or
        (Get-RequiredJsonInteger $started 'targetFps' 'selfTest.focusTimerAnimation.started') -ne 60 -or
        (Get-RequiredJsonInteger $started 'motionTickAfter' 'selfTest.focusTimerAnimation.started') -le
            (Get-RequiredJsonInteger $started 'motionTickBefore' 'selfTest.focusTimerAnimation.started')) {
        throw 'The focus start stage did not prove its visible 60 FPS acknowledgement.'
    }

    $paused = $animation.paused
    Assert-JsonString $paused 'backendState' 'paused' 'selfTest.focusTimerAnimation.paused'
    Assert-JsonString $paused 'visualState' 'paused' 'selfTest.focusTimerAnimation.paused'
    foreach ($name in @('breathing', 'startAcknowledgementActive')) {
        Assert-JsonBoolean $paused $name $false 'selfTest.focusTimerAnimation.paused'
    }
    if ([Math]::Abs((Get-RequiredJsonNumber $paused 'startPulse' 'selfTest.focusTimerAnimation.paused')) -ge 0.001 -or
        (Get-RequiredJsonInteger $paused 'targetFps' 'selfTest.focusTimerAnimation.paused') -ne 0 -or
        (Get-RequiredJsonInteger $paused 'motionTickAfter' 'selfTest.focusTimerAnimation.paused') -ne
            (Get-RequiredJsonInteger $paused 'motionTickBefore' 'selfTest.focusTimerAnimation.paused') -or
        [Math]::Abs(
            (Get-RequiredJsonNumber $paused 'surfaceScaleAfter' 'selfTest.focusTimerAnimation.paused') -
            (Get-RequiredJsonNumber $paused 'surfaceScaleBefore' 'selfTest.focusTimerAnimation.paused')
        ) -ge 0.0005) {
        throw 'The focus pause stage did not prove that motion was frozen.'
    }

    $resumed = $animation.resumed
    Assert-JsonString $resumed 'backendState' 'running' 'selfTest.focusTimerAnimation.resumed'
    Assert-JsonString $resumed 'visualState' 'running' 'selfTest.focusTimerAnimation.resumed'
    Assert-JsonBoolean $resumed 'breathing' $true 'selfTest.focusTimerAnimation.resumed'
    Assert-JsonBoolean $resumed 'startAcknowledgementActive' $false 'selfTest.focusTimerAnimation.resumed'
    $resumedFps = Get-RequiredJsonInteger $resumed 'targetFps' 'selfTest.focusTimerAnimation.resumed'
    if ([Math]::Abs((Get-RequiredJsonNumber $resumed 'startPulse' 'selfTest.focusTimerAnimation.resumed')) -ge 0.001 -or
        $resumedFps -notin @(15, 60) -or
        (Get-RequiredJsonInteger $resumed 'motionTickAfter' 'selfTest.focusTimerAnimation.resumed') -le
            (Get-RequiredJsonInteger $resumed 'motionTickBefore' 'selfTest.focusTimerAnimation.resumed')) {
        throw 'The focus resume stage replayed start or did not resume motion.'
    }

    $finishedStage = $animation.finished
    Assert-JsonBoolean $finishedStage 'backendActive' $false 'selfTest.focusTimerAnimation.finished'
    Assert-JsonString $finishedStage 'visualState' 'finished' 'selfTest.focusTimerAnimation.finished'
    foreach ($name in @('completionVisible', 'windowVisible', 'windowExposed')) {
        Assert-JsonBoolean $finishedStage $name $true 'selfTest.focusTimerAnimation.finished'
    }
    if ([Math]::Abs((Get-RequiredJsonNumber $finishedStage 'startPulse' 'selfTest.focusTimerAnimation.finished')) -ge 0.001) {
        throw 'The focus finish stage retained a stale start pulse.'
    }
}

function Get-PackagedDistFootprint([string]$DistRoot) {
    $resolvedRoot = [IO.Path]::GetFullPath($DistRoot).
        TrimEnd([IO.Path]::DirectorySeparatorChar).
        TrimEnd([IO.Path]::AltDirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
        throw "Packaged dist root is missing: $resolvedRoot"
    }

    $forbiddenPatterns = [ordered]@{
        QtWebEngine = '(?i)Qt(?:6)?WebEngine'
        QtQuick3D = '(?i)Qt(?:6)?Quick3D'
        QtCharts = '(?i)Qt(?:6)?Charts'
        QtGraphs = '(?i)Qt(?:6)?Graphs'
        QtDataVisualization = '(?i)Qt(?:6)?DataVisualization'
        QtLocation = '(?i)Qt(?:6)?Location'
        QtPdf = '(?i)Qt(?:6)?Pdf'
        QtWebSockets = '(?i)Qt(?:6)?WebSockets'
    }
    $rootPrefix = $resolvedRoot + [IO.Path]::DirectorySeparatorChar
    $items = @(
        Get-ChildItem -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction Stop
    )
    $fileCount = 0
    [long]$totalBytes = 0
    $forbiddenMatches = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $items) {
        $itemPath = [IO.Path]::GetFullPath([string]$item.FullName)
        if (-not $itemPath.StartsWith(
            $rootPrefix,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "Packaged dist scan escaped its root: $itemPath"
        }
        $relativePath = $itemPath.Substring($rootPrefix.Length)
        if (-not $item.PSIsContainer) {
            $fileCount++
            $totalBytes += [long]$item.Length
        }
        foreach ($family in $forbiddenPatterns.Keys) {
            if ($relativePath -match $forbiddenPatterns[$family]) {
                $forbiddenMatches.Add([pscustomobject]@{
                    family = [string]$family
                    relativePath = $relativePath
                    itemType = if ($item.PSIsContainer) { 'directory' } else { 'file' }
                })
            }
        }
    }
    if ($fileCount -le 0 -or $totalBytes -le 0) {
        throw 'Packaged dist footprint must contain at least one non-empty file.'
    }

    return [pscustomobject]@{
        distRoot = $resolvedRoot
        itemCount = $items.Count
        fileCount = $fileCount
        totalBytes = $totalBytes
        forbiddenFamilies = [object[]]@($forbiddenPatterns.Keys)
        forbiddenMatches = [object[]]@($forbiddenMatches)
    }
}

$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ExpectedApplicationVersion = '0.3.54'
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $ProjectRoot 'dist\LiliesInTheBox\LiliesInTheBox.exe'
}
if ([string]::IsNullOrWhiteSpace($SelfTestReport)) {
    $SelfTestReport = Join-Path $ProjectRoot 'artifacts\packaged-self-test-v0354.json'
}
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
    $ReportPath = Join-Path $ProjectRoot 'artifacts\packaged-compact-resource-v0354.json'
}

$Executable = [IO.Path]::GetFullPath($Executable)
$SelfTestReport = [IO.Path]::GetFullPath($SelfTestReport)
$ReportPath = [IO.Path]::GetFullPath($ReportPath)
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "Packaged executable is missing: $Executable"
}
if (-not (Test-Path -LiteralPath $SelfTestReport -PathType Leaf)) {
    throw "Packaged self-test report is missing: $SelfTestReport"
}
$selfTest = Get-Content -LiteralPath $SelfTestReport -Raw -Encoding UTF8 |
    ConvertFrom-Json
if (-not $selfTest.qmlLoaded -or -not $selfTest.identityPassed -or
    [string]::IsNullOrWhiteSpace([string]$selfTest.applicationVersion)) {
    throw 'The packaged self-test has not passed; refusing the resource probe.'
}
if ([string]$selfTest.applicationVersion -cne $ExpectedApplicationVersion) {
    throw "Packaged self-test version mismatch: $($selfTest.applicationVersion)"
}
Assert-FocusTimerAnimationReport $selfTest
$executableSha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
if ([string]$selfTest.executableSha256 -cne $executableSha256) {
    throw 'The packaged self-test belongs to a different executable.'
}
$distFootprint = Get-PackagedDistFootprint (Split-Path -Parent $Executable)

$oldPlatform = $env:QT_QPA_PLATFORM
$oldRhi = $env:QSG_RHI_BACKEND
$oldQuick = $env:QT_QUICK_BACKEND
$oldHold = $env:LILIES_SMOKE_HOLD
$oldDataDir = $env:LILIES_DATA_DIR
$temporaryRoot = [IO.Path]::GetFullPath(
    (Join-Path $ProjectRoot 'artifacts\compact-resource-probes')
).TrimEnd('\')
[IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
$diagnosticDataRoot = [IO.Path]::GetFullPath((Join-Path $temporaryRoot (
    'lilies-packaged-resource-' + [Guid]::NewGuid().ToString('N')
)))
if ([IO.Path]::GetFullPath((Split-Path -Parent $diagnosticDataRoot)).TrimEnd('\') -cne
    $temporaryRoot -or (Split-Path -Leaf $diagnosticDataRoot) -notlike
    'lilies-packaged-resource-*') {
    throw "Refusing an unsafe diagnostic data path: $diagnosticDataRoot"
}
[IO.Directory]::CreateDirectory($diagnosticDataRoot) | Out-Null
$process = $null
$resourceSnapshot = $null
$loadedModules = @{}
$probeStopped = $false
$probeFailure = $null
$cleanupResult = $null
$cleanupSucceeded = $false
$cleanupFailures = [System.Collections.Generic.List[System.Exception]]::new()
try {
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:QSG_RHI_BACKEND = 'software'
    $env:QT_QUICK_BACKEND = 'software'
    $env:LILIES_SMOKE_HOLD = '1'
    $env:LILIES_DATA_DIR = $diagnosticDataRoot
    $process = Start-Process -FilePath $Executable `
        -ArgumentList @('--smoke', '--compact') `
        -WindowStyle Hidden -PassThru

    Start-Sleep -Seconds $SettleSeconds
    $process.Refresh()
    if ($process.HasExited) {
        throw "Packaged compact probe exited early with code $($process.ExitCode)."
    }
    $sample = Get-Process -Id $process.Id -ErrorAction Stop
    $resourceSnapshot = Get-LiveProcessResourceSnapshot `
        -Process $sample -ExpectedProcessId $process.Id
    foreach ($name in @(
        'Qt6Multimedia.dll',
        'Qt6MultimediaQuick.dll',
        'ffmpegmediaplugin.dll',
        'avcodec-61.dll'
    )) {
        $loadedModules[$name] = $resourceSnapshot.moduleNames -contains $name
    }
} catch {
    $probeFailure = $_.Exception
} finally {
    try {
        if ($null -ne $process) {
            $process.Refresh()
            if (-not $process.HasExited) {
                # This is the exact diagnostic child created above. It owns no
                # user data or desktop shell state and is never discovered by name.
                Stop-Process -Id $process.Id -Force -ErrorAction Stop
                $process.WaitForExit(5000) | Out-Null
                $process.Refresh()
                if (-not $process.HasExited) {
                    throw [IO.IOException]::new(
                        "Diagnostic process $($process.Id) did not stop within five seconds."
                    )
                }
            }
            $probeStopped = $process.HasExited
        }
    } catch {
        $cleanupFailures.Add($_.Exception)
    } finally {
        # Restore the caller's process environment even if child termination
        # failed; the release probe must never leak diagnostic policy.
        try {
            $env:QT_QPA_PLATFORM = $oldPlatform
            $env:QSG_RHI_BACKEND = $oldRhi
            $env:QT_QUICK_BACKEND = $oldQuick
            $env:LILIES_SMOKE_HOLD = $oldHold
            $env:LILIES_DATA_DIR = $oldDataDir
        } catch {
            $cleanupFailures.Add($_.Exception)
        }
    }
    try {
        $cleanupResult = Remove-ExactDiagnosticDirectoryWithRetry `
            -Path $diagnosticDataRoot `
            -TemporaryRoot $temporaryRoot
    } catch {
        if ($_.Exception -is [AggregateException]) {
            foreach ($inner in $_.Exception.InnerExceptions) {
                $cleanupFailures.Add($inner)
            }
        } else {
            $cleanupFailures.Add($_.Exception)
        }
    }
    $cleanupSucceeded = [bool](
        $cleanupFailures.Count -eq 0 -and
        $null -ne $cleanupResult -and
        $cleanupResult.removed -and
        -not (Test-Path -LiteralPath $diagnosticDataRoot)
    )
}

if ($null -ne $probeFailure) {
    $allFailures = [System.Collections.Generic.List[System.Exception]]::new()
    $allFailures.Add($probeFailure)
    foreach ($cleanupFailure in $cleanupFailures) {
        $allFailures.Add($cleanupFailure)
    }
    throw [AggregateException]::new(
        'The packaged compact resource probe failed.',
        $allFailures.ToArray()
    )
}

if ($null -eq $resourceSnapshot -or -not $resourceSnapshot.sampledWhileAlive) {
    throw 'The packaged compact process could not be sampled.'
}
$responding = [bool]$resourceSnapshot.responding
$multimediaLoaded = @($loadedModules.GetEnumerator() | Where-Object { $_.Value })
$report = [ordered]@{
    schemaVersion = 1
    applicationVersion = [string]$selfTest.applicationVersion
    executableSha256 = $executableSha256
    mode = 'compact'
    platform = 'offscreen'
    backend = 'software'
    distTotalBytes = [long]$distFootprint.totalBytes
    distFileCount = [int]$distFootprint.fileCount
    forbiddenQtResources = [ordered]@{
        scanRoot = [string]$distFootprint.distRoot
        scanned = $true
        scannedItemCount = [int]$distFootprint.itemCount
        families = [object[]]@($distFootprint.forbiddenFamilies)
        matchCount = [int]$distFootprint.forbiddenMatches.Count
        matches = [object[]]@($distFootprint.forbiddenMatches)
        passed = $distFootprint.forbiddenMatches.Count -eq 0
    }
    settleSeconds = $SettleSeconds
    workingSetMiB = [Math]::Round($resourceSnapshot.workingSetBytes / 1MB, 2)
    privateMiB = [Math]::Round($resourceSnapshot.privateBytes / 1MB, 2)
    threads = [int]$resourceSnapshot.threads
    handles = [int]$resourceSnapshot.handles
    sampledWhileAlive = [bool]$resourceSnapshot.sampledWhileAlive
    sampledProcessId = [int]$resourceSnapshot.processId
    resourceSampledAt = [string]$resourceSnapshot.capturedAt
    responding = $responding
    modules = $loadedModules
    probeProcessStopped = $probeStopped
    cleanup = [ordered]@{
        succeeded = $cleanupSucceeded
        attempts = if ($null -ne $cleanupResult) {
            [int]$cleanupResult.attempts
        } else {
            0
        }
        errors = @($cleanupFailures | ForEach-Object { $_.Message })
    }
    passed = [bool](
        $resourceSnapshot.sampledWhileAlive -and
        $responding -and $probeStopped -and $cleanupSucceeded -and
        $multimediaLoaded.Count -eq 0 -and
        $distFootprint.forbiddenMatches.Count -eq 0
    )
    capturedAt = [DateTimeOffset]::UtcNow.ToString('o')
}
$parent = Split-Path -Parent $ReportPath
[IO.Directory]::CreateDirectory($parent) | Out-Null
[IO.File]::WriteAllText(
    $ReportPath,
    ($report | ConvertTo-Json -Depth 5),
    [Text.UTF8Encoding]::new($false)
)
$report | ConvertTo-Json -Depth 5
if (-not $report.passed) {
    exit 1
}
