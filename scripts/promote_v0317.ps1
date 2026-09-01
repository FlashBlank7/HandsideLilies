param(
    [string]$FormalRoot = 'F:\code\Lilies in the box',
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$CandidateRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ReleaseVersion = '0.3.17'
$PackagedSelfTest = Join-Path $CandidateRoot 'artifacts\packaged-self-test-v0317.json'
$PackagedCompactResource = Join-Path $CandidateRoot 'artifacts\packaged-compact-resource-v0317.json'
$CandidateDist = Join-Path $CandidateRoot 'dist\LiliesInTheBox'
$CandidateExe = Join-Path $CandidateDist 'LiliesInTheBox.exe'

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
    if ($sourceHash -ne $packagedHash) {
        throw "Packaged release resource is stale: $SourceRelative"
    }
}

if (-not (Test-Path -LiteralPath $CandidateExe -PathType Leaf)) {
    throw "Packaged candidate is missing: $CandidateExe"
}
$candidateExeHash = (Get-FileHash -LiteralPath $CandidateExe -Algorithm SHA256).Hash

$selfTest = Read-ReleaseJson $PackagedSelfTest
$compactResource = Read-ReleaseJson $PackagedCompactResource
if ([string]$selfTest.applicationVersion -cne $ReleaseVersion) {
    throw "Packaged self-test version mismatch: $($selfTest.applicationVersion)"
}
if ([string]$selfTest.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged self-test executable hash mismatch.'
}
foreach ($name in @(
    'qmlLoaded',
    'identityPassed',
    'desktopPetLoaded',
    'independentPetWindowLoaded',
    'petWindowAcceptsInput',
    'radialActionsAcceptInput',
    'timezoneDataPassed'
)) {
    Assert-JsonBoolean $selfTest $name $true 'selfTest'
}
Assert-JsonBoolean $selfTest 'hasBackslash' $false 'selfTest'
foreach ($name in @('chat', 'world', 'settings')) {
    Assert-JsonBoolean $selfTest.radialActionHitTests $name $true 'selfTest.radialActionHitTests'
}
if ([string]$compactResource.applicationVersion -cne $ReleaseVersion -or
    [string]$compactResource.mode -cne 'compact' -or
    [string]$compactResource.platform -cne 'offscreen' -or
    [string]$compactResource.backend -cne 'software') {
    throw 'Packaged compact resource report did not satisfy the v0.3.17 release gate.'
}
if ([string]$compactResource.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged compact resource executable hash mismatch.'
}
foreach ($name in @('responding', 'probeProcessStopped', 'passed')) {
    Assert-JsonBoolean $compactResource $name $true 'compactResource'
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

$exeBuiltAt = (Get-Item -LiteralPath $CandidateExe).LastWriteTimeUtc
$releaseGateNow = [DateTimeOffset]::UtcNow.UtcDateTime
foreach ($report in @($selfTest, $compactResource)) {
    $capturedAt = [DateTimeOffset]::Parse([string]$report.capturedAt).UtcDateTime
    if ($capturedAt -lt $exeBuiltAt.AddSeconds(-2)) {
        throw 'Release report predates the packaged executable.'
    }
    if ($capturedAt -gt $releaseGateNow.AddMinutes(5)) {
        throw 'Release report timestamp is implausibly far in the future.'
    }
}

Assert-MatchingReleaseFile 'qml\Main.qml' '_internal\qml\Main.qml'
Assert-MatchingReleaseFile 'qml\V03PetBody.qml' '_internal\qml\V03PetBody.qml'
Assert-MatchingReleaseFile 'qml\V03WorkPanel.qml' '_internal\qml\V03WorkPanel.qml'
Assert-MatchingReleaseFile 'qml\CompanionBubble.qml' '_internal\qml\CompanionBubble.qml'
Assert-MatchingReleaseFile 'themes\first-encounter\theme.json' '_internal\themes\first-encounter\theme.json'
$packagedTheme = Read-ReleaseJson (Join-Path $CandidateDist '_internal\themes\first-encounter\theme.json')
if ([string]$packagedTheme.version -cne $ReleaseVersion) {
    throw "Packaged theme version mismatch: $($packagedTheme.version)"
}

if ($ValidateOnly) {
    Write-Output "Lilies v$ReleaseVersion release gate passed. EXE SHA256: $candidateExeHash"
    return
}

$Engine = Join-Path $PSScriptRoot 'promote_v0313.ps1'
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
    throw "Promotion engine is missing: $Engine"
}

$AdditionalFiles = @(
    '.gitignore',
    'scripts\promote_v0313.ps1',
    'qml\CompanionBubble.qml',
    'qml\V03WorkPanel.qml',
    'scripts\promote_v0315.ps1',
    'scripts\promote_v0316.ps1',
    'scripts\verify_codex_subscription_smoke.py',
    'scripts\verify_focus_timer_aura.py',
    'tests\test_focus_timer_aura_qml.py',
    'src\lilies\core\companion.py',
    'src\lilies\companion_controller.py',
    'src\lilies\core\companion_runtime.py',
    'src\lilies\core\orchestration.py',
    'src\lilies\core\themes.py',
    'scripts\verify_companion_observer.py',
    'scripts\verify_companion_flow_ui.py',
    'scripts\verify_box_world_presentation.py',
    'scripts\verify_box_world_ui.py',
    'scripts\verify_pose_geometry_ui.py',
    'scripts\verify_transient_window_resources.py',
    'scripts\verify_packaged_compact_resources.ps1',
    'tests\test_companion.py',
    'tests\test_companion_controller.py',
    'tests\test_codex_subscription_v02.py',
    'tests\test_orchestration_v03.py',
    'tests\test_companion_flow_ui_offscreen.py',
    'tests\test_box_world_presentation_offscreen.py',
    'tests\test_box_world_ui_offscreen.py',
    'tests\test_pose_geometry_offscreen.py',
    'tests\test_pet_habitat_v03.py',
    'tests\test_compact_ui_offscreen.py',
    'tests\test_compact_hit_test.py',
    'tests\test_theme_socket.py',
    'tests\test_promotion_retry_contract.py',
    'themes\first-encounter\assets\lilith-pose-expansion-sheet-v1.png',
    'artifacts\packaged-compact-resource-v0317.json'
)

& $Engine `
    -FormalRoot $FormalRoot `
    -ReleaseVersion $ReleaseVersion `
    -PromotionScript 'scripts\promote_v0317.ps1' `
    -PackagedReport 'artifacts\packaged-self-test-v0317.json' `
    -AdditionalFiles $AdditionalFiles
