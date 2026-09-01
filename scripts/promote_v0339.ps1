param(
    [string]$FormalRoot = 'F:\code\Lilies in the box',
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$CandidateRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ReleaseVersion = '0.3.39'
$FileVersion = '0.3.39.0'
$PackagedSelfTest = Join-Path $CandidateRoot 'artifacts\packaged-self-test-v0339.json'
$PackagedCompactResource = Join-Path $CandidateRoot 'artifacts\packaged-compact-resource-v0339.json'
$PackagedWindowsStartup = Join-Path $CandidateRoot 'artifacts\packaged-windows-startup-v0339.json'
$PoseClickMaskReport = Join-Path $CandidateRoot 'artifacts\pose-click-mask-v0339.json'
$CandidateDist = Join-Path $CandidateRoot 'dist\LiliesInTheBox'
$CandidateInternal = Join-Path $CandidateDist '_internal'
$CandidateExe = Join-Path $CandidateDist 'LiliesInTheBox.exe'
$Python = Join-Path $CandidateRoot '.venv\Scripts\python.exe'
$NativeCaptureProbe = Join-Path $CandidateRoot 'scripts\verify_native_window_capture.py'
$HistoricalV0338 = Join-Path $CandidateRoot 'scripts\promote_v0338.ps1'
$HistoricalV0338Sha256 = '71222a2c88b4d54578e03cffd3298f3ddb8d3c3e064c2b29c97d69aa1b790692'

if (-not (Test-Path -LiteralPath $HistoricalV0338 -PathType Leaf)) {
    throw "Historical v0.3.38 promotion wrapper is missing: $HistoricalV0338"
}
$historicalV0338Actual = (
    Get-FileHash -LiteralPath $HistoricalV0338 -Algorithm SHA256
).Hash.ToLowerInvariant()
if ($historicalV0338Actual -cne $HistoricalV0338Sha256) {
    throw "Historical v0.3.38 promotion wrapper changed: $historicalV0338Actual"
}

function Read-ReleaseJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Release report is missing: $Path"
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw "Release report is not valid JSON: $Path"
    }
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

function Assert-QmlWarningEvidence([object]$Report, [string]$Context) {
    Assert-JsonBoolean $Report 'qmlWarningsPassed' $true $Context
    if ((Get-RequiredJsonInteger $Report 'qmlWarningCount' $Context) -ne 0) {
        throw "$Context.qmlWarningCount must be the integer zero."
    }
    $warningsProperty = $Report.PSObject.Properties['qmlWarnings']
    if ($null -eq $warningsProperty -or
        $warningsProperty.Value -isnot [System.Array]) {
        throw "$Context.qmlWarnings must be a JSON array."
    }
    if (@($warningsProperty.Value).Count -ne 0) {
        throw "$Context.qmlWarnings must be empty."
    }
}

function Assert-PackagedFootprintReport(
    [object]$Report,
    [string]$ExpectedDistRoot
) {
    $actual = Get-PackagedDistFootprint $ExpectedDistRoot
    $reportedTotalBytes = Get-RequiredJsonInteger `
        $Report 'distTotalBytes' 'compactResource'
    if ($reportedTotalBytes -le 0 -or
        $reportedTotalBytes -ne [long]$actual.totalBytes) {
        throw 'compactResource.distTotalBytes must equal the current packaged dist size.'
    }
    $reportedFileCount = Get-RequiredJsonInteger `
        $Report 'distFileCount' 'compactResource'
    if ($reportedFileCount -le 0 -or
        $reportedFileCount -ne [long]$actual.fileCount) {
        throw 'compactResource.distFileCount must equal the current packaged dist file count.'
    }

    $scanProperty = $Report.PSObject.Properties['forbiddenQtResources']
    if ($null -eq $scanProperty -or $null -eq $scanProperty.Value) {
        throw 'compactResource.forbiddenQtResources is missing.'
    }
    $scan = $scanProperty.Value
    Assert-JsonBoolean $scan 'scanned' $true 'compactResource.forbiddenQtResources'
    Assert-JsonBoolean $scan 'passed' $true 'compactResource.forbiddenQtResources'

    $scanRootProperty = $scan.PSObject.Properties['scanRoot']
    if ($null -eq $scanRootProperty -or
        $scanRootProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$scanRootProperty.Value)) {
        throw 'compactResource.forbiddenQtResources.scanRoot must be a JSON string.'
    }
    $reportedScanRoot = [IO.Path]::GetFullPath([string]$scanRootProperty.Value).
        TrimEnd([IO.Path]::DirectorySeparatorChar).
        TrimEnd([IO.Path]::AltDirectorySeparatorChar)
    if (-not $reportedScanRoot.Equals(
        [string]$actual.distRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'compactResource.forbiddenQtResources.scanRoot must identify the candidate dist.'
    }

    $scannedItemCount = Get-RequiredJsonInteger `
        $scan 'scannedItemCount' 'compactResource.forbiddenQtResources'
    if ($scannedItemCount -lt $reportedFileCount -or
        $scannedItemCount -ne [long]$actual.itemCount) {
        throw 'compactResource.forbiddenQtResources.scannedItemCount is stale.'
    }

    $familiesProperty = $scan.PSObject.Properties['families']
    if ($null -eq $familiesProperty -or
        $familiesProperty.Value -isnot [System.Array]) {
        throw 'compactResource.forbiddenQtResources.families must be a JSON array.'
    }
    $families = @($familiesProperty.Value)
    $expectedFamilies = @($actual.forbiddenFamilies)
    if ($families.Count -ne $expectedFamilies.Count) {
        throw 'compactResource.forbiddenQtResources.families must contain all eight families.'
    }
    for ($index = 0; $index -lt $expectedFamilies.Count; $index++) {
        if ($families[$index] -isnot [string] -or
            [string]$families[$index] -cne [string]$expectedFamilies[$index]) {
            throw 'compactResource.forbiddenQtResources.families is not the required allowlist complement.'
        }
    }

    if ((Get-RequiredJsonInteger `
        $scan 'matchCount' 'compactResource.forbiddenQtResources') -ne 0) {
        throw 'compactResource.forbiddenQtResources.matchCount must be the integer zero.'
    }
    $matchesProperty = $scan.PSObject.Properties['matches']
    if ($null -eq $matchesProperty -or
        $matchesProperty.Value -isnot [System.Array]) {
        throw 'compactResource.forbiddenQtResources.matches must be a JSON array.'
    }
    if (@($matchesProperty.Value).Count -ne 0) {
        throw 'compactResource.forbiddenQtResources.matches must be empty.'
    }
    if (@($actual.forbiddenMatches).Count -ne 0) {
        $paths = @(
            $actual.forbiddenMatches |
                Select-Object -First 8 -ExpandProperty relativePath
        )
        throw "Candidate dist contains forbidden Qt resources: $($paths -join ', ')"
    }
}

function Assert-VersionInfoString(
    [object]$VersionInfo,
    [string]$PropertyName,
    [string]$Expected
) {
    $property = $VersionInfo.PSObject.Properties[$PropertyName]
    if ($null -eq $property -or [string]$property.Value -cne $Expected) {
        throw "EXE VERSIONINFO $PropertyName mismatch: $($property.Value)"
    }
}

function Assert-MatchingReleaseFile([string]$SourceRelative, [string]$PackagedRelative) {
    $source = Join-Path $CandidateRoot $SourceRelative
    $packaged = Join-Path $CandidateDist $PackagedRelative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Candidate source is missing: $source"
    }
    if (-not (Test-Path -LiteralPath $packaged -PathType Leaf)) {
        throw "Packaged release resource is missing: $packaged"
    }
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $packagedHash = (Get-FileHash -LiteralPath $packaged -Algorithm SHA256).Hash
    if ($sourceHash -cne $packagedHash) {
        throw "Packaged release resource is stale: $SourceRelative"
    }
}

function Assert-FormalReleaseFile([string]$RelativePath) {
    $candidate = Join-Path $CandidateRoot $RelativePath
    $formal = Join-Path $FormalRoot $RelativePath
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Candidate release file is missing: $candidate"
    }
    if (-not (Test-Path -LiteralPath $formal -PathType Leaf)) {
        throw "Formal release file is missing after promotion: $formal"
    }
    $candidateHash = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
    $formalHash = (Get-FileHash -LiteralPath $formal -Algorithm SHA256).Hash
    if ($candidateHash -cne $formalHash) {
        throw "Formal release file is stale after promotion: $RelativePath"
    }
}

function Assert-CandidateReleaseFile([string]$RelativePath) {
    $candidate = [IO.Path]::GetFullPath((Join-Path $CandidateRoot $RelativePath))
    $prefix = $CandidateRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Candidate release file escaped the candidate root: $RelativePath"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Candidate release file is missing: $candidate"
    }
}

function Assert-ReleaseArtifactFreshness(
    [string]$Path,
    [string[]]$RequiredInputs,
    [string]$Context
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Context report is missing: $Path"
    }
    $newestInput = [DateTime]::MinValue
    foreach ($inputPath in $RequiredInputs) {
        if (-not (Test-Path -LiteralPath $inputPath -PathType Leaf)) {
            throw "$Context input is missing: $inputPath"
        }
        $inputTime = (Get-Item -LiteralPath $inputPath).LastWriteTimeUtc
        if ($inputTime -gt $newestInput) {
            $newestInput = $inputTime
        }
    }
    $reportTime = (Get-Item -LiteralPath $Path).LastWriteTimeUtc
    if ($reportTime -lt $newestInput.AddSeconds(-2)) {
        throw "$Context report predates an input or the packaged executable."
    }
    if ($reportTime -gt [DateTime]::UtcNow.AddMinutes(5)) {
        throw "$Context report timestamp is implausibly far in the future."
    }
}

function Assert-PoseClickMaskReport(
    [object]$Report,
    [string]$ExpectedApplicationVersion,
    [string]$ExpectedExecutableSha256,
    [string]$ExpectedResourceRoot
) {
    if ((Get-RequiredJsonInteger $Report 'schemaVersion' 'poseClickMask') -ne 1) {
        throw 'poseClickMask.schemaVersion must be the integer 1.'
    }
    Assert-JsonString $Report 'applicationVersion' $ExpectedApplicationVersion 'poseClickMask'
    Assert-JsonString $Report 'executableSha256' $ExpectedExecutableSha256 'poseClickMask'
    $resourceRootProperty = $Report.PSObject.Properties['resourceRoot']
    if ($null -eq $resourceRootProperty -or
        $resourceRootProperty.Value -isnot [string]) {
        throw 'poseClickMask.resourceRoot must identify the packaged _internal directory.'
    }
    $reportedResourceRoot = [IO.Path]::GetFullPath([string]$resourceRootProperty.Value)
    $expectedResourceRoot = [IO.Path]::GetFullPath($ExpectedResourceRoot)
    if (-not $reportedResourceRoot.Equals(
        $expectedResourceRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'poseClickMask.resourceRoot must identify the packaged _internal directory.'
    }
    Assert-JsonString $Report 'platform' 'offscreen' 'poseClickMask'
    Assert-JsonBoolean $Report 'passed' $true 'poseClickMask'

    $posesProperty = $Report.PSObject.Properties['poses']
    if ($null -eq $posesProperty -or $null -eq $posesProperty.Value) {
        throw 'poseClickMask.poses is missing.'
    }
    foreach ($poseId in @(
        'edge-peek-live',
        'focus-watch',
        'listening-live',
        'perch-prone',
        'title-sit'
    )) {
        $poseProperty = $posesProperty.Value.PSObject.Properties[$poseId]
        if ($null -eq $poseProperty -or $null -eq $poseProperty.Value) {
            throw "poseClickMask.poses.$poseId is missing."
        }
        $pose = $poseProperty.Value
        $context = "poseClickMask.poses.$poseId"
        Assert-JsonBoolean $pose 'declaredHit' $true $context
        Assert-JsonBoolean $pose 'passed' $true $context
        $cornersProperty = $pose.PSObject.Properties['transparentCornerHits']
        if ($null -eq $cornersProperty) {
            throw "$context.transparentCornerHits is missing."
        }
        $corners = @($cornersProperty.Value)
        if ($corners.Count -ne 4) {
            throw "$context.transparentCornerHits must contain four booleans."
        }
        foreach ($corner in $corners) {
            if ($corner -isnot [bool] -or [bool]$corner) {
                throw "$context.transparentCornerHits must contain only false JSON booleans."
            }
        }
    }

    foreach ($sectionName in @('mirror', 'transition', 'loadFailure', 'rapidSwitch')) {
        $sectionProperty = $Report.PSObject.Properties[$sectionName]
        if ($null -eq $sectionProperty -or $null -eq $sectionProperty.Value) {
            throw "poseClickMask.$sectionName is missing."
        }
        Assert-JsonBoolean $sectionProperty.Value 'passed' $true "poseClickMask.$sectionName"
    }
    Assert-JsonBoolean $Report.mirror 'displayedMirror' $true 'poseClickMask.mirror'
    Assert-JsonBoolean $Report.loadFailure 'targetLoadFailed' $true 'poseClickMask.loadFailure'
    Assert-JsonBoolean $Report.rapidSwitch 'transitionActive' $false 'poseClickMask.rapidSwitch'
}

function Assert-NativeCaptureDiagnostic([object]$Report) {
    $expectedNames = @('callSucceeded', 'sizeValid', 'evidenceValid', 'reasonCode')
    $actualNames = @($Report.PSObject.Properties | ForEach-Object { $_.Name })
    if ($actualNames.Count -ne $expectedNames.Count -or
        @($expectedNames | Where-Object { $_ -notin $actualNames }).Count -ne 0) {
        throw 'nativeCapture diagnostic must contain exactly the four content-free fields.'
    }
    foreach ($name in @('callSucceeded', 'sizeValid', 'evidenceValid')) {
        Assert-JsonBoolean $Report $name $true 'nativeCapture'
    }
    Assert-JsonString $Report 'reasonCode' 'ok' 'nativeCapture'
}

function Invoke-NativeCaptureDiagnostic(
    [string]$PythonPath,
    [string]$ProbePath,
    [string]$HelperExePath,
    [int]$TimeoutMilliseconds = 15000
) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Native capture diagnostic Python is missing: $PythonPath"
    }
    if (-not (Test-Path -LiteralPath $ProbePath -PathType Leaf)) {
        throw "Native capture diagnostic script is missing: $ProbePath"
    }
    if (-not (Test-Path -LiteralPath $HelperExePath -PathType Leaf)) {
        throw "Native capture helper executable is missing: $HelperExePath"
    }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $PythonPath
    $startInfo.Arguments = (
        '"' + $ProbePath.Replace('"', '\"') + '" --helper-exe "' +
        $HelperExePath.Replace('"', '\"') + '"'
    )
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'Native capture diagnostic process did not start.'
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $process.Kill()
            $process.WaitForExit()
            throw "Native capture diagnostic timed out after $TimeoutMilliseconds ms."
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            throw "Native capture diagnostic failed with exit code $($process.ExitCode)."
        }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            throw 'Native capture diagnostic wrote unexpected stderr output.'
        }
        try {
            $report = $stdout | ConvertFrom-Json
        } catch {
            throw 'Native capture diagnostic did not return valid JSON.'
        }
        Assert-NativeCaptureDiagnostic $report
        return $report
    } finally {
        $process.Dispose()
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

function Assert-QtCacheRoutingReport(
    [object]$Report,
    [string]$Context
) {
    $routingProperty = $Report.PSObject.Properties['qtCacheRouting']
    if ($null -eq $routingProperty -or $null -eq $routingProperty.Value) {
        throw "$Context.qtCacheRouting is missing."
    }
    $routing = $routingProperty.Value
    foreach ($name in @(
        'pathsWithinDataRoot',
        'environmentApplied',
        'qtShaderDiskCacheDisabled',
        'passed'
    )) {
        Assert-JsonBoolean $routing $name $true "$Context.qtCacheRouting"
    }

    $dataRootProperty = $routing.PSObject.Properties['dataRoot']
    if ($null -eq $dataRootProperty -or
        $dataRootProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$dataRootProperty.Value)) {
        throw "$Context.qtCacheRouting.dataRoot must be a non-empty string."
    }
    $resolvedRoot = [IO.Path]::GetFullPath([string]$dataRootProperty.Value).TrimEnd('\')
    foreach ($name in @(
        'cacheRoot',
        'qmlDiskCachePath',
        'rhiPipelineCacheLoadPath',
        'rhiPipelineCacheSavePath'
    )) {
        $pathProperty = $routing.PSObject.Properties[$name]
        if ($null -eq $pathProperty -or
            $pathProperty.Value -isnot [string] -or
            [string]::IsNullOrWhiteSpace([string]$pathProperty.Value)) {
            throw "$Context.qtCacheRouting.$name must be a non-empty string."
        }
        $resolvedPath = [IO.Path]::GetFullPath([string]$pathProperty.Value)
        if (-not $resolvedPath.StartsWith(
            $resolvedRoot + '\',
            [StringComparison]::OrdinalIgnoreCase
        )) {
            throw "$Context.qtCacheRouting.$name escaped dataRoot: $resolvedPath"
        }
    }
    $pipelineLoad = [IO.Path]::GetFullPath(
        [string]$routing.rhiPipelineCacheLoadPath
    )
    $pipelineSave = [IO.Path]::GetFullPath(
        [string]$routing.rhiPipelineCacheSavePath
    )
    if (-not $pipelineLoad.Equals(
        $pipelineSave,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "$Context.qtCacheRouting RHI load/save paths must match."
    }
}

if (-not (Test-Path -LiteralPath $CandidateExe -PathType Leaf)) {
    throw "Packaged candidate is missing: $CandidateExe"
}
$candidateExeItem = Get-Item -LiteralPath $CandidateExe
$candidateExeHash = (Get-FileHash -LiteralPath $CandidateExe -Algorithm SHA256).Hash
$candidateVersionInfo = $candidateExeItem.VersionInfo
foreach ($expectation in @(
    @('FileVersion', $FileVersion),
    @('ProductVersion', $ReleaseVersion),
    @('CompanyName', 'Lilies in the box'),
    @('FileDescription', 'Lilies in the box desktop companion'),
    @('InternalName', 'LiliesInTheBox'),
    @('OriginalFilename', 'LiliesInTheBox.exe'),
    @('ProductName', 'Lilies in the box')
)) {
    Assert-VersionInfoString $candidateVersionInfo $expectation[0] $expectation[1]
}

$selfTest = Read-ReleaseJson $PackagedSelfTest
$compactResource = Read-ReleaseJson $PackagedCompactResource
$windowsStartup = Read-ReleaseJson $PackagedWindowsStartup
$poseClickMask = Read-ReleaseJson $PoseClickMaskReport
Assert-PoseClickMaskReport `
    $poseClickMask `
    $ReleaseVersion `
    $candidateExeHash `
    $CandidateInternal
Assert-ReleaseArtifactFreshness `
    $PoseClickMaskReport `
    @(
        $CandidateExe,
        (Join-Path $CandidateInternal 'qml\V03PetBody.qml'),
        (Join-Path $CandidateInternal 'qml\V03PetPoseResolver.qml'),
        (Join-Path $CandidateRoot 'scripts\verify_pose_click_masks.py'),
        (Join-Path $CandidateInternal 'themes\first-encounter\theme.json'),
        (Join-Path $CandidateInternal 'themes\first-encounter\assets\lilith-pose-perch-prone-v1.png'),
        (Join-Path $CandidateInternal 'themes\first-encounter\assets\lilith-pose-title-sit-v1.png'),
        (Join-Path $CandidateInternal 'themes\first-encounter\assets\lilith-pose-edge-peek-v1.png'),
        (Join-Path $CandidateInternal 'themes\first-encounter\assets\lilith-pose-listening-live-v1.png'),
        (Join-Path $CandidateInternal 'themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png')
    ) `
    'poseClickMask'
$nativeCaptureDiagnostic = Invoke-NativeCaptureDiagnostic `
    $Python `
    $NativeCaptureProbe `
    $CandidateExe
if ((Get-RequiredJsonInteger $selfTest 'schemaVersion' 'selfTest') -ne 1) {
    throw 'selfTest.schemaVersion must be the integer 1.'
}
Assert-JsonBoolean $selfTest 'passed' $true 'selfTest'
Assert-JsonString $selfTest 'applicationVersion' $ReleaseVersion 'selfTest'
Assert-JsonString $selfTest 'executableSha256' $candidateExeHash 'selfTest'
Assert-QmlWarningEvidence $selfTest 'selfTest'
foreach ($name in @(
    'qmlLoaded',
    'identityPassed',
    'desktopPetLoaded',
    'independentPetWindowLoaded',
    'petWindowAcceptsInput',
    'radialActionsAcceptInput',
    'boxWorldPresentationPassed',
    'syntheticProactiveBubbleVisible',
    'nativeSystemMovePathPresent',
    'dragFallbackVerified',
    'compactStartupPassed',
    'timezoneDataPassed'
)) {
    Assert-JsonBoolean $selfTest $name $true 'selfTest'
}
Assert-JsonBoolean $selfTest 'hasBackslash' $false 'selfTest'
Assert-JsonBoolean $selfTest 'nativeSystemMoveRuntimeVerified' $false 'selfTest'
foreach ($name in @('chat', 'world', 'settings')) {
    Assert-JsonBoolean $selfTest.radialActionHitTests $name $true 'selfTest.radialActionHitTests'
}
if ([string]$selfTest.diagnosticPlatform -cne 'offscreen') {
    throw 'Packaged self-test must run on the offscreen platform.'
}
Assert-FocusTimerAnimationReport $selfTest
Assert-QtCacheRoutingReport $selfTest 'selfTest'

if ($null -eq $selfTest.PSObject.Properties['compactStartup']) {
    throw 'selfTest.compactStartup is missing.'
}
Assert-JsonBoolean $selfTest.compactStartup 'passed' $true 'selfTest.compactStartup'
if ([string]$selfTest.compactStartup.shellMode -cne 'compact') {
    throw 'selfTest.compactStartup.shellMode must be compact.'
}
if ($null -eq $selfTest.compactStartup.PSObject.Properties['quickWindows']) {
    throw 'selfTest.compactStartup.quickWindows is missing.'
}
$quickWindows = $selfTest.compactStartup.quickWindows
foreach ($name in @('expectedWindowCount', 'windowCount')) {
    if ((Get-RequiredJsonInteger $quickWindows $name 'selfTest.compactStartup.quickWindows') -ne 16) {
        throw "selfTest.compactStartup.quickWindows.$name must be the integer 16."
    }
}
Assert-JsonBoolean $quickWindows 'persistentHintsDisabled' $true 'selfTest.compactStartup.quickWindows'
Assert-JsonBoolean $quickWindows 'passed' $true 'selfTest.compactStartup.quickWindows'
$persistentHints = @($quickWindows.persistentHints)
if ($persistentHints.Count -ne 16) {
    throw 'selfTest.compactStartup.quickWindows.persistentHints must contain 16 windows.'
}
foreach ($hint in $persistentHints) {
    if ([string]::IsNullOrWhiteSpace([string]$hint.objectName)) {
        throw 'A packaged quick-window persistence entry has no objectName.'
    }
    Assert-JsonBoolean $hint 'persistentGraphics' $false 'selfTest.compactStartup.quickWindows.persistentHints'
    Assert-JsonBoolean $hint 'persistentSceneGraph' $false 'selfTest.compactStartup.quickWindows.persistentHints'
}
if ($null -eq $selfTest.compactStartup.PSObject.Properties['compactIdle']) {
    throw 'selfTest.compactStartup.compactIdle is missing.'
}
$compactIdle = $selfTest.compactStartup.compactIdle
Assert-JsonBoolean $compactIdle 'lowPower' $true 'selfTest.compactStartup.compactIdle'
Assert-JsonBoolean $compactIdle 'passed' $true 'selfTest.compactStartup.compactIdle'
if ((Get-RequiredJsonInteger $compactIdle 'targetFps' 'selfTest.compactStartup.compactIdle') -ne 15) {
    throw 'selfTest.compactStartup.compactIdle.targetFps must be the integer 15.'
}
if ($null -eq $selfTest.compactStartup.PSObject.Properties['sceneLoaders']) {
    throw 'selfTest.compactStartup.sceneLoaders is missing.'
}
$sceneLoaders = $selfTest.compactStartup.sceneLoaders
Assert-JsonBoolean $sceneLoaders 'scene2dLoaded' $false 'selfTest.compactStartup.sceneLoaders'
Assert-JsonBoolean $sceneLoaders 'videoLoaded' $false 'selfTest.compactStartup.sceneLoaders'
Assert-JsonBoolean $sceneLoaders 'passed' $true 'selfTest.compactStartup.sceneLoaders'
if ([string]$sceneLoaders.videoPlaybackState -cne 'unloaded') {
    throw 'selfTest.compactStartup.sceneLoaders.videoPlaybackState must be unloaded.'
}

if ($null -eq $selfTest.PSObject.Properties['boxWorldPresentation']) {
    throw 'selfTest.boxWorldPresentation is missing.'
}
foreach ($name in @(
    'backendOpen',
    'windowFound',
    'windowVisible',
    'windowExposed',
    'requestedVisible',
    'stageFound',
    'stageVisible'
)) {
    Assert-JsonBoolean $selfTest.boxWorldPresentation $name $true 'selfTest.boxWorldPresentation'
}
if ((Get-RequiredJsonInteger $selfTest.boxWorldPresentation 'presentationCount' 'selfTest.boxWorldPresentation') -lt 1) {
    throw 'selfTest.boxWorldPresentation.presentationCount must be positive.'
}
if ($null -eq $selfTest.PSObject.Properties['syntheticProactiveBubble']) {
    throw 'selfTest.syntheticProactiveBubble is missing.'
}
foreach ($name in @('windowFound', 'windowVisible', 'windowExposed', 'bodyFound', 'bodyVisible')) {
    Assert-JsonBoolean $selfTest.syntheticProactiveBubble $name $true 'selfTest.syntheticProactiveBubble'
}
if ((Get-RequiredJsonInteger $selfTest.syntheticProactiveBubble 'presentationRevision' 'selfTest.syntheticProactiveBubble') -lt 1 -or
    [string]$selfTest.syntheticProactiveBubble.summary -cne 'packaged proactive bubble probe') {
    throw 'Packaged self-test proactive bubble presentation mismatch.'
}
if ($null -eq $selfTest.PSObject.Properties['dragProbe']) {
    throw 'selfTest.dragProbe is missing.'
}
foreach ($name in @('previewMode', 'nativeSystemMovePathPresent', 'fallbackMovedWindow')) {
    Assert-JsonBoolean $selfTest.dragProbe $name $true 'selfTest.dragProbe'
}
foreach ($name in @('nativeSystemMoveAttempted', 'nativeSystemMoveActive', 'nativeSystemMoveRuntimeVerified')) {
    Assert-JsonBoolean $selfTest.dragProbe $name $false 'selfTest.dragProbe'
}

if ([int]$compactResource.schemaVersion -ne 1 -or
    [string]$compactResource.applicationVersion -cne $ReleaseVersion -or
    [string]$compactResource.mode -cne 'compact' -or
    [string]$compactResource.platform -cne 'offscreen' -or
    [string]$compactResource.backend -cne 'software') {
    throw 'Packaged compact resource report did not satisfy the v0.3.39 release gate.'
}
if ([string]$compactResource.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged compact resource executable hash mismatch.'
}
Assert-PackagedFootprintReport $compactResource $CandidateDist
foreach ($name in @('responding', 'probeProcessStopped', 'passed')) {
    Assert-JsonBoolean $compactResource $name $true 'compactResource'
}
if ($null -eq $compactResource.PSObject.Properties['cleanup']) {
    throw 'compactResource.cleanup is missing.'
}
Assert-JsonBoolean $compactResource.cleanup 'succeeded' $true 'compactResource.cleanup'
$cleanupAttempts = Get-RequiredJsonInteger $compactResource.cleanup 'attempts' 'compactResource.cleanup'
if ($cleanupAttempts -lt 0 -or $cleanupAttempts -gt 5) {
    throw 'compactResource.cleanup.attempts must be an integer from zero through five.'
}
$cleanupErrorsProperty = $compactResource.cleanup.PSObject.Properties['errors']
if ($null -eq $cleanupErrorsProperty) {
    throw 'compactResource.cleanup.errors is missing.'
}
$cleanupErrors = @($cleanupErrorsProperty.Value)
if ($cleanupErrors.Count -ne 0) {
    throw "Compact resource cleanup reported errors: $($cleanupErrors -join '; ')"
}
if ($null -eq $compactResource.PSObject.Properties['modules']) {
    throw 'compactResource.modules is missing.'
}
foreach ($name in @(
    'Qt6Multimedia.dll',
    'Qt6MultimediaQuick.dll',
    'ffmpegmediaplugin.dll',
    'avcodec-61.dll'
)) {
    Assert-JsonBoolean $compactResource.modules $name $false 'compactResource.modules'
}
$unexpectedLoadedModule = @(
    $compactResource.modules.PSObject.Properties |
        Where-Object { $_.Value -isnot [bool] -or [bool]$_.Value }
)
if ($unexpectedLoadedModule.Count -ne 0) {
    throw "Compact release has invalid or loaded modules: $($unexpectedLoadedModule.Name -join ', ')"
}

if ([int]$windowsStartup.schemaVersion -ne 1 -or
    [string]$windowsStartup.applicationVersion -cne $ReleaseVersion -or
    [string]$windowsStartup.probeKind -cne 'hidden-qwindows-cold-start' -or
    [string]$windowsStartup.diagnosticPlatform -cne 'windows') {
    throw 'Packaged Windows startup report did not satisfy the v0.3.39 release gate.'
}
if ([string]$windowsStartup.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged Windows startup executable hash mismatch.'
}
foreach ($name in @(
    'qmlLoaded',
    'nativeWindowCreated',
    'nativeWindowIdCached',
    'nativeHitTestDispatched',
    'nativeRadialWorldHit',
    'nativeDesktopModeTabHit',
    'nativeTransparentCornerPass',
    'rootNoActivateStyle',
    'petNoActivateStyle',
    'eventLoopResponsive',
    'passed'
)) {
    Assert-JsonBoolean $windowsStartup $name $true 'windowsStartup'
}
Assert-JsonBoolean $windowsStartup 'trayPublished' $false 'windowsStartup'
if ((Get-RequiredJsonInteger $windowsStartup 'nativeDesktopModeTabHitResult' 'windowsStartup') -ne 1) {
    throw 'windowsStartup.nativeDesktopModeTabHitResult must be HTCLIENT (1).'
}
if ((Get-RequiredJsonInteger $windowsStartup 'visibleQuickWindowCount' 'windowsStartup') -ne 0) {
    throw 'windowsStartup.visibleQuickWindowCount must be the integer zero.'
}
if ((Get-RequiredJsonInteger $windowsStartup 'quickWindowCount' 'windowsStartup') -lt 1 -or
    @($windowsStartup.visibleQuickWindows).Count -ne 0) {
    throw 'Windows startup probe did not create only hidden QML windows.'
}
$nativeHitTestCount = Get-RequiredJsonInteger $windowsStartup 'nativeHitTestCount' 'windowsStartup'
$nativeDispatchCount = Get-RequiredJsonInteger $windowsStartup 'nativeDispatchCount' 'windowsStartup'
$eventLoopTicks = Get-RequiredJsonInteger $windowsStartup 'eventLoopTicks' 'windowsStartup'
if ($nativeHitTestCount -lt 1 -or $nativeDispatchCount -lt $nativeHitTestCount -or
    $eventLoopTicks -lt 4) {
    throw 'Windows startup probe did not exercise a responsive native event loop.'
}
if (-not [string]::IsNullOrEmpty([string]$windowsStartup.dispatchError)) {
    throw "Windows startup native dispatch failed: $($windowsStartup.dispatchError)"
}
Assert-QtCacheRoutingReport $windowsStartup 'windowsStartup'

$exeBuiltAt = $candidateExeItem.LastWriteTimeUtc
$releaseGateNow = [DateTimeOffset]::UtcNow.UtcDateTime
foreach ($report in @($selfTest, $compactResource, $windowsStartup, $poseClickMask)) {
    $capturedAtProperty = $report.PSObject.Properties['capturedAt']
    if ($null -eq $capturedAtProperty -or
        $capturedAtProperty.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$capturedAtProperty.Value)) {
        throw 'Release report capturedAt must be a non-empty JSON string.'
    }
    try {
        $capturedAt = [DateTimeOffset]::Parse(
            [string]$capturedAtProperty.Value
        ).UtcDateTime
    } catch {
        throw 'Release report capturedAt is not a valid timestamp.'
    }
    if ($capturedAt -lt $exeBuiltAt.AddSeconds(-2)) {
        throw 'Release report predates the packaged executable.'
    }
    if ($capturedAt -gt $releaseGateNow.AddMinutes(5)) {
        throw 'Release report timestamp is implausibly far in the future.'
    }
}

foreach ($mapping in @(
    @('qml\Main.qml', '_internal\qml\Main.qml'),
    @('qml\V03PetBody.qml', '_internal\qml\V03PetBody.qml'),
    @('qml\V03PetPoseResolver.qml', '_internal\qml\V03PetPoseResolver.qml'),
    @('qml\V03WorkPanel.qml', '_internal\qml\V03WorkPanel.qml'),
    @('qml\CompanionBubble.qml', '_internal\qml\CompanionBubble.qml'),
    @('qml\V03FocusTimerAura.qml', '_internal\qml\V03FocusTimerAura.qml'),
    @('qml\V03BoxWorldScene.qml', '_internal\qml\V03BoxWorldScene.qml'),
    @('themes\first-encounter\theme.json', '_internal\themes\first-encounter\theme.json'),
    @('themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png', '_internal\themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png')
)) {
    Assert-MatchingReleaseFile $mapping[0] $mapping[1]
}
$packagedTheme = Read-ReleaseJson (Join-Path $CandidateDist '_internal\themes\first-encounter\theme.json')
if ([string]$packagedTheme.version -cne $ReleaseVersion) {
    throw "Packaged theme version mismatch: $($packagedTheme.version)"
}

$Engine = Join-Path $PSScriptRoot 'promote_v0313.ps1'
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
    throw "Promotion engine is missing: $Engine"
}

$AdditionalFiles = @(
    '.gitignore',
    'LiliesInTheBox.spec',
    'main.py',
    'pyproject.toml',
    'README.md',
    'uv.lock',
    'docs\pose-asset-gate.md',
    'packaging\windows_version_info.txt',
    'scripts\build_windows.ps1',
    'scripts\install_windows.ps1',
    'scripts\promote_v0313.ps1',
    'scripts\promote_v0315.ps1',
    'scripts\promote_v0316.ps1',
    'scripts\promote_v0317.ps1',
    'scripts\promote_v0318.ps1',
    'scripts\promote_v0319.ps1',
    'scripts\promote_v0320.ps1',
    'scripts\promote_v0321.ps1',
    'scripts\promote_v0322.ps1',
    'scripts\promote_v0323.ps1',
    'scripts\promote_v0324.ps1',
    'scripts\promote_v0325.ps1',
    'scripts\promote_v0326.ps1',
    'scripts\promote_v0327.ps1',
    'scripts\promote_v0328.ps1',
    'scripts\promote_v0329.ps1',
    'scripts\promote_v0330.ps1',
    'scripts\promote_v0331.ps1',
    'scripts\promote_v0332.ps1',
    'scripts\promote_v0333.ps1',
    'scripts\promote_v0334.ps1',
    'scripts\promote_v0335.ps1',
    'scripts\promote_v0336.ps1',
    'scripts\promote_v0337.ps1',
    'scripts\promote_v0338.ps1',
    'scripts\promote_v0339.ps1',
    'qml\Main.qml',
    'qml\CompanionBubble.qml',
    'qml\FocusDiversionBubble.qml',
    'qml\V03ConnectorSetup.qml',
    'qml\V03Dock.qml',
    'qml\V03BoxWorldScene.qml',
    'qml\V03FocusTimerAura.qml',
    'qml\V03PetBody.qml',
    'qml\V03PetPoseResolver.qml',
    'qml\V03WorkPanel.qml',
    'src\lilies\app.py',
    'src\lilies\paths.py',
    'src\lilies\__init__.py',
    'src\lilies\__main__.py',
    'src\lilies\backend.py',
    'src\lilies\companion_controller.py',
    'src\lilies\connectors\slack_socket.py',
    'src\lilies\connectors\slack_websocket.py',
    'src\lilies\core\companion.py',
    'src\lilies\core\activity.py',
    'src\lilies\core\companion_delivery.py',
    'src\lilies\core\native_capture_helper.py',
    'src\lilies\core\companion_runtime.py',
    'src\lilies\core\codex_subscription.py',
    'src\lilies\core\data_migration.py',
    'src\lilies\core\database.py',
    'src\lilies\core\focus_diversion.py',
    'src\lilies\core\memory.py',
    'src\lilies\core\connector_assist.py',
    'src\lilies\core\orchestration.py',
    'src\lilies\core\model.py',
    'src\lilies\core\selection.py',
    'src\lilies\core\pet_habitat.py',
    'src\lilies\core\productivity.py',
    'src\lilies\core\shell.py',
    'src\lilies\core\socket_server.py',
    'src\lilies\core\themes.py',
    'src\lilies_in_the_box.egg-info\PKG-INFO',
    'themes\first-encounter\theme.json',
    'scripts\verify_box_world_presentation.py',
    'scripts\verify_box_world_click_path.py',
    'scripts\verify_box_world_scene.py',
    'scripts\verify_box_world_ui.py',
    'scripts\verify_codex_subscription_smoke.py',
    'scripts\verify_entry_actions.py',
    'scripts\verify_main_qml_ui.py',
    'scripts\verify_dock_ui.py',
    'scripts\verify_drag_runtime_v0325.py',
    'scripts\verify_companion_flow_ui.py',
    'scripts\verify_companion_frequency_draft.py',
    'scripts\verify_companion_presentation_gate.py',
    'scripts\verify_companion_bubbles.py',
    'scripts\verify_companion_bubble_matrix.py',
    'scripts\verify_companion_observer.py',
    'scripts\verify_compact_resources.py',
    'scripts\verify_compact_ui.py',
    'scripts\verify_cross_dpi_layout.py',
    'scripts\verify_focus_timer_aura.py',
    'scripts\verify_habitat_ui.py',
    'scripts\verify_focus_main_integration.py',
    'scripts\verify_desktop_surface_replay_v0328.py',
    'scripts\inspect_desktop_surface.py',
    'scripts\verify_packaged_windows_startup.ps1',
    'scripts\verify_packaged_compact_resources.ps1',
    'scripts\verify_pose_assets.py',
    'scripts\verify_pet_pose_resolver.py',
    'scripts\verify_pose_click_masks.py',
    'scripts\verify_pose_geometry_ui.py',
    'scripts\verify_pose_outfit_policy_ui.py',
    'scripts\verify_procedural_habitat_poses.py',
    'scripts\verify_selection_ui.py',
    'scripts\verify_transient_window_resources.py',
    'scripts\verify_native_window_capture.py',
    'tests\conftest.py',
    'tests\test_backend_v03_contract.py',
    'tests\test_activity_context.py',
    'tests\test_browser_capture_safety_v0332.py',
    'tests\test_box_world_presentation_offscreen.py',
    'tests\test_box_world_click_path_offscreen.py',
    'tests\test_box_world_scene_offscreen.py',
    'tests\test_box_world_ui_offscreen.py',
    'tests\test_codex_subscription_v02.py',
    'tests\test_compact_hit_test.py',
    'tests\test_compact_animation_budget_v0320.py',
    'tests\test_compact_resource_lifecycle.py',
    'tests\test_compact_ui_offscreen.py',
    'tests\test_companion.py',
    'tests\test_atomic_companion_frequency.py',
    'tests\test_companion_controller.py',
    'tests\test_companion_preferences_signal.py',
    'tests\test_companion_frequency_projection_hardening.py',
    'tests\test_companion_resilience_v0333.py',
    'tests\test_companion_frequency_draft_v0332.py',
    'tests\test_companion_native_presentation.py',
    'tests\test_companion_discoverability_qml.py',
    'tests\test_companion_presentation_gate_qml.py',
    'tests\test_companion_persistence.py',
    'tests\test_connector_assist_service_v03.py',
    'tests\test_desktop_native_presentation_v0328.py',
    'tests\test_desktop_surface_replay_v0328.py',
    'tests\test_companion_flow_ui_offscreen.py',
    'tests\test_companion_bubbles_offscreen.py',
    'tests\test_data_migration.py',
    'tests\test_cross_dpi_layout_v0312.py',
    'tests\test_dock_ui_offscreen.py',
    'tests\test_entry_actions_offscreen.py',
    'tests\test_drag_follow_contract_v0328.py',
    'tests\test_focus_timer_aura_qml.py',
    'tests\test_focus_diversion_v03.py',
    'tests\test_focus_main_integration_qml.py',
    'tests\test_habitat_ui_offscreen.py',
    'tests\test_habitat_pose_variants_v0322.py',
    'tests\test_orchestration_v03.py',
    'tests\test_model_broker_wiring_v03.py',
    'tests\test_main_qml_ui_v0333.py',
    'tests\test_memory_v02.py',
    'tests\test_native_drag_press_contract_v0320.py',
    'tests\test_drag_runtime_v0325.py',
    'tests\test_outfit_asset_gate.py',
    'tests\test_optional_habitat_pose_gate_v0326.py',
    'tests\test_pose_asset_gate.py',
    'tests\test_pose_click_mask_qml_v0329.py',
    'tests\test_procedural_habitat_poses_v0326.py',
    'tests\test_packaged_user_flow_self_test_contract.py',
    'tests\test_packaged_footprint_gate_v0340.py',
    'tests\test_pet_habitat_v03.py',
    'tests\test_pet_pose_resolver_v0332.py',
    'tests\test_pose_geometry_offscreen.py',
    'tests\test_pose_outfit_policy_offscreen.py',
    'tests\test_promotion_retry_contract.py',
    'tests\test_process_entrypoints.py',
    'tests\test_single_instance_activation.py',
    'tests\test_quick_window_resource_lifecycle.py',
    'tests\test_release_probe_cleanup_contract.py',
    'tests\test_release_focus_gate_v0329.py',
    'tests\test_release_focus_gate_v0330.py',
    'tests\test_release_focus_gate_v0331.py',
    'tests\test_release_focus_gate_v0332.py',
    'tests\test_release_focus_gate_v0333.py',
    'tests\test_release_qt_cache_gate_v0331.py',
    'tests\test_release_qt_cache_gate_v0332.py',
    'tests\test_release_qt_cache_gate_v0333.py',
    'tests\test_runtime_snapshot_socket.py',
    'tests\test_runtime_recovery_hardening_v0335.py',
    'tests\test_shell_recovery.py',
    'tests\test_slack_socket_v03.py',
    'tests\test_slack_websocket_transport.py',
    'tests\test_selection.py',
    'tests\test_theme_socket.py',
    'tests\test_version_alignment_v0318.py',
    'tests\test_version_alignment_v0319.py',
    'tests\test_version_alignment_v0320.py',
    'tests\test_version_alignment_v0321.py',
    'tests\test_version_alignment_v0322.py',
    'tests\test_version_alignment_v0323.py',
    'tests\test_version_alignment_v0324.py',
    'tests\test_version_alignment_v0325.py',
    'tests\test_version_alignment_v0326.py',
    'tests\test_version_alignment_v0327.py',
    'tests\test_version_alignment_v0328.py',
    'tests\test_version_alignment_v0329.py',
    'tests\test_version_alignment_v0330.py',
    'tests\test_version_alignment_v0331.py',
    'tests\test_version_alignment_v0332.py',
    'tests\test_version_alignment_v0333.py',
    'tests\test_qt_cache_routing_v0331.py',
    'tests\test_qt_cache_routing_v0332.py',
    'tests\test_windows_startup_probe_contract.py',
    'tests\test_windows_installer_contract.py',
    'tests\test_wardrobe_manifest_contract.py',
    'tests\test_windows_version_resource_contract.py',
    'tests\test_native_window_capture_probe_v0334.py',
    'tests\test_native_capture_helper_v0334.py',
    'tests\test_companion_capture_fallback_v0334.py',
    'tests\test_release_focus_gate_v0334.py',
    'tests\test_release_qt_cache_gate_v0334.py',
    'tests\test_version_alignment_v0334.py',
    'tests\test_version_alignment_v0335.py',
    'tests\test_version_alignment_v0336.py',
    'tests\test_version_alignment_v0337.py',
    'tests\test_version_alignment_v0338.py',
    'tests\test_version_alignment_v0339.py',
    'themes\first-encounter\assets\lilith-pose-expansion-sheet-v1.png',
    'themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v1-rgb.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v2-rgb.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v1.md',
    'art-reference\generated-v0.3\lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png',
    'art-reference\generated-v0.3\README.md',
    'artifacts\pose-click-mask-v0329.json',
    'artifacts\pose-click-mask-v0330.json',
    'artifacts\pose-click-mask-v0331.json',
    'artifacts\pose-click-mask-v0332.json',
    'artifacts\pose-click-mask-v0334.json',
    'artifacts\pose-asset-gate.json',
    'artifacts\procedural-habitat-pose-gate.json',
    'artifacts\habitat-pose-coverage.json',
    'artifacts\packaged-compact-resource-v0330.json',
    'artifacts\packaged-windows-startup-v0330.json',
    'artifacts\packaged-compact-resource-v0331.json',
    'artifacts\packaged-windows-startup-v0331.json',
    'artifacts\packaged-compact-resource-v0332.json',
    'artifacts\packaged-windows-startup-v0332.json',
    'artifacts\packaged-compact-resource-v0334.json',
    'artifacts\packaged-windows-startup-v0334.json',
    'artifacts\pose-click-mask-v0335.json',
    'artifacts\packaged-self-test-v0335.json',
    'artifacts\packaged-compact-resource-v0335.json',
    'artifacts\packaged-windows-startup-v0335.json',
    'artifacts\pose-click-mask-v0336.json',
    'artifacts\packaged-self-test-v0336.json',
    'artifacts\packaged-compact-resource-v0336.json',
    'artifacts\packaged-windows-startup-v0336.json',
    'artifacts\pose-click-mask-v0337.json',
    'artifacts\packaged-self-test-v0337.json',
    'artifacts\packaged-compact-resource-v0337.json',
    'artifacts\packaged-windows-startup-v0337.json',
    'artifacts\pose-click-mask-v0338.json',
    'artifacts\packaged-self-test-v0338.json',
    'artifacts\packaged-compact-resource-v0338.json',
    'artifacts\packaged-windows-startup-v0338.json',
    'artifacts\pose-click-mask-v0339.json',
    'artifacts\packaged-self-test-v0339.json',
    'artifacts\packaged-compact-resource-v0339.json',
    'artifacts\packaged-windows-startup-v0339.json'
)

# ValidateOnly and formal promotion intentionally share every candidate-side
# gate.  In particular, an entry cannot hide in AdditionalFiles until the
# mutating promotion engine happens to enumerate it.
foreach ($relativePath in $AdditionalFiles) {
    Assert-CandidateReleaseFile $relativePath
}

if ($ValidateOnly) {
    Write-Output "Lilies v$ReleaseVersion release gate passed. EXE SHA256: $candidateExeHash; native capture diagnostic: $($nativeCaptureDiagnostic.reasonCode)"
    return
}

& $Engine `
    -FormalRoot $FormalRoot `
    -ReleaseVersion $ReleaseVersion `
    -PromotionScript 'scripts\promote_v0339.ps1' `
    -PackagedReport 'artifacts\packaged-self-test-v0339.json' `
    -AdditionalFiles $AdditionalFiles

# Keep an explicit v0.3.39 checksum fence for every source, gate and artwork
# contract changed in this release.  The baked-checkerboard draft is copied
# only under art-reference; no runtime theme asset points to it.
foreach ($relativePath in @(
    'pyproject.toml',
    'uv.lock',
    'README.md',
    'docs\pose-asset-gate.md',
    'packaging\windows_version_info.txt',
    'scripts\build_windows.ps1',
    'scripts\install_windows.ps1',
    'scripts\promote_v0329.ps1',
    'scripts\promote_v0330.ps1',
    'scripts\promote_v0331.ps1',
    'scripts\promote_v0332.ps1',
    'scripts\promote_v0333.ps1',
    'scripts\promote_v0334.ps1',
    'scripts\promote_v0335.ps1',
    'scripts\promote_v0336.ps1',
    'scripts\promote_v0337.ps1',
    'scripts\promote_v0338.ps1',
    'scripts\promote_v0339.ps1',
    'scripts\verify_codex_subscription_smoke.py',
    'scripts\verify_packaged_compact_resources.ps1',
    'scripts\verify_packaged_windows_startup.ps1',
    'scripts\verify_native_window_capture.py',
    'src\lilies\__init__.py',
    'src\lilies\core\codex_subscription.py',
    'src\lilies_in_the_box.egg-info\PKG-INFO',
    'themes\first-encounter\theme.json',
    'src\lilies\app.py',
    'src\lilies\paths.py',
    'src\lilies\companion_controller.py',
    'src\lilies\core\activity.py',
    'src\lilies\core\companion_delivery.py',
    'src\lilies\core\native_capture_helper.py',
    'src\lilies\core\companion.py',
    'src\lilies\core\companion_runtime.py',
    'src\lilies\core\data_migration.py',
    'src\lilies\core\database.py',
    'src\lilies\core\orchestration.py',
    'src\lilies\core\shell.py',
    'src\lilies\core\socket_server.py',
    'src\lilies\core\themes.py',
    'qml\Main.qml',
    'qml\CompanionBubble.qml',
    'qml\FocusDiversionBubble.qml',
    'qml\V03ConnectorSetup.qml',
    'qml\V03Dock.qml',
    'qml\V03FocusTimerAura.qml',
    'qml\V03PetBody.qml',
    'qml\V03PetPoseResolver.qml',
    'qml\V03WorkPanel.qml',
    'src\lilies\backend.py',
    'src\lilies\connectors\slack_socket.py',
    'src\lilies\connectors\slack_websocket.py',
    'src\lilies\core\focus_diversion.py',
    'src\lilies\core\pet_habitat.py',
    'scripts\verify_box_world_click_path.py',
    'scripts\verify_entry_actions.py',
    'scripts\verify_main_qml_ui.py',
    'scripts\verify_dock_ui.py',
    'scripts\verify_companion_frequency_draft.py',
    'scripts\verify_companion_presentation_gate.py',
    'scripts\verify_companion_flow_ui.py',
    'scripts\verify_companion_bubbles.py',
    'scripts\verify_companion_bubble_matrix.py',
    'scripts\verify_compact_ui.py',
    'scripts\verify_cross_dpi_layout.py',
    'scripts\verify_drag_runtime_v0325.py',
    'scripts\verify_focus_timer_aura.py',
    'scripts\verify_habitat_ui.py',
    'scripts\verify_pet_pose_resolver.py',
    'scripts\verify_pose_assets.py',
    'scripts\verify_pose_click_masks.py',
    'scripts\verify_pose_geometry_ui.py',
    'scripts\verify_pose_outfit_policy_ui.py',
    'scripts\verify_procedural_habitat_poses.py',
    'tests\conftest.py',
    'tests\test_compact_ui_offscreen.py',
    'tests\test_backend_v03_contract.py',
    'tests\test_activity_context.py',
    'tests\test_browser_capture_safety_v0332.py',
    'tests\test_box_world_click_path_offscreen.py',
    'tests\test_companion.py',
    'tests\test_atomic_companion_frequency.py',
    'tests\test_companion_bubbles_offscreen.py',
    'tests\test_companion_controller.py',
    'tests\test_companion_preferences_signal.py',
    'tests\test_companion_frequency_projection_hardening.py',
    'tests\test_companion_resilience_v0333.py',
    'tests\test_companion_frequency_draft_v0332.py',
    'tests\test_companion_discoverability_qml.py',
    'tests\test_companion_flow_ui_offscreen.py',
    'tests\test_companion_native_presentation.py',
    'tests\test_companion_presentation_gate_qml.py',
    'tests\test_companion_persistence.py',
    'tests\test_cross_dpi_layout_v0312.py',
    'tests\test_data_migration.py',
    'tests\test_dock_ui_offscreen.py',
    'tests\test_entry_actions_offscreen.py',
    'tests\test_drag_runtime_v0325.py',
    'tests\test_focus_diversion_v03.py',
    'tests\test_focus_timer_aura_qml.py',
    'tests\test_focus_main_integration_qml.py',
    'tests\test_habitat_ui_offscreen.py',
    'tests\test_habitat_pose_variants_v0322.py',
    'tests\test_main_qml_ui_v0333.py',
    'tests\test_outfit_asset_gate.py',
    'tests\test_optional_habitat_pose_gate_v0326.py',
    'tests\test_packaged_user_flow_self_test_contract.py',
    'tests\test_packaged_footprint_gate_v0340.py',
    'tests\test_pet_habitat_v03.py',
    'tests\test_pet_pose_resolver_v0332.py',
    'tests\test_pose_asset_gate.py',
    'tests\test_pose_click_mask_qml_v0329.py',
    'tests\test_pose_geometry_offscreen.py',
    'tests\test_pose_outfit_policy_offscreen.py',
    'tests\test_procedural_habitat_poses_v0326.py',
    'tests\test_release_focus_gate_v0329.py',
    'tests\test_release_focus_gate_v0330.py',
    'tests\test_release_focus_gate_v0331.py',
    'tests\test_release_focus_gate_v0332.py',
    'tests\test_release_focus_gate_v0333.py',
    'tests\test_release_qt_cache_gate_v0331.py',
    'tests\test_release_qt_cache_gate_v0332.py',
    'tests\test_release_qt_cache_gate_v0333.py',
    'tests\test_runtime_snapshot_socket.py',
    'tests\test_runtime_recovery_hardening_v0335.py',
    'tests\test_shell_recovery.py',
    'tests\test_slack_socket_v03.py',
    'tests\test_slack_websocket_transport.py',
    'tests\test_theme_socket.py',
    'tests\test_promotion_retry_contract.py',
    'tests\test_version_alignment_v0328.py',
    'tests\test_version_alignment_v0329.py',
    'tests\test_version_alignment_v0330.py',
    'tests\test_version_alignment_v0331.py',
    'tests\test_version_alignment_v0332.py',
    'tests\test_version_alignment_v0333.py',
    'tests\test_qt_cache_routing_v0331.py',
    'tests\test_qt_cache_routing_v0332.py',
    'tests\test_windows_startup_probe_contract.py',
    'tests\test_windows_installer_contract.py',
    'tests\test_windows_version_resource_contract.py',
    'tests\test_native_window_capture_probe_v0334.py',
    'tests\test_native_capture_helper_v0334.py',
    'tests\test_companion_capture_fallback_v0334.py',
    'tests\test_release_focus_gate_v0334.py',
    'tests\test_release_qt_cache_gate_v0334.py',
    'tests\test_version_alignment_v0334.py',
    'tests\test_version_alignment_v0335.py',
    'tests\test_version_alignment_v0336.py',
    'tests\test_version_alignment_v0337.py',
    'tests\test_version_alignment_v0338.py',
    'tests\test_version_alignment_v0339.py',
    'artifacts\pose-click-mask-v0330.json',
    'artifacts\pose-click-mask-v0331.json',
    'artifacts\pose-click-mask-v0332.json',
    'artifacts\pose-click-mask-v0334.json',
    'artifacts\pose-click-mask-v0335.json',
    'artifacts\packaged-self-test-v0335.json',
    'artifacts\packaged-compact-resource-v0335.json',
    'artifacts\packaged-windows-startup-v0335.json',
    'artifacts\pose-click-mask-v0336.json',
    'artifacts\packaged-self-test-v0336.json',
    'artifacts\packaged-compact-resource-v0336.json',
    'artifacts\packaged-windows-startup-v0336.json',
    'artifacts\pose-click-mask-v0337.json',
    'artifacts\packaged-self-test-v0337.json',
    'artifacts\packaged-compact-resource-v0337.json',
    'artifacts\packaged-windows-startup-v0337.json',
    'artifacts\pose-click-mask-v0338.json',
    'artifacts\packaged-self-test-v0338.json',
    'artifacts\packaged-compact-resource-v0338.json',
    'artifacts\packaged-windows-startup-v0338.json',
    'artifacts\pose-click-mask-v0339.json',
    'artifacts\packaged-self-test-v0339.json',
    'artifacts\packaged-compact-resource-v0339.json',
    'artifacts\packaged-windows-startup-v0339.json',
    'artifacts\pose-asset-gate.json',
    'artifacts\procedural-habitat-pose-gate.json',
    'artifacts\habitat-pose-coverage.json',
    'art-reference\generated-v0.3\README.md',
    'art-reference\generated-v0.3\lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png'
)) {
    Assert-FormalReleaseFile $relativePath
}
