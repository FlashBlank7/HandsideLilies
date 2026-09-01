param(
    [string]$FormalRoot = 'F:\code\Lilies in the box',
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'
$CandidateRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ReleaseVersion = '0.3.22'
$FileVersion = '0.3.22.0'
$PackagedSelfTest = Join-Path $CandidateRoot 'artifacts\packaged-self-test-v0322.json'
$PackagedCompactResource = Join-Path $CandidateRoot 'artifacts\packaged-compact-resource-v0322.json'
$PackagedWindowsStartup = Join-Path $CandidateRoot 'artifacts\packaged-windows-startup-v0322.json'
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
    $countProperty = $quickWindows.PSObject.Properties[$name]
    if ($null -eq $countProperty -or
        ($countProperty.Value -isnot [int] -and
            $countProperty.Value -isnot [long]) -or
        [long]$countProperty.Value -ne 16) {
        throw "selfTest.compactStartup.quickWindows.$name must be the integer 16."
    }
}
Assert-JsonBoolean $quickWindows 'persistentHintsDisabled' $true `
    'selfTest.compactStartup.quickWindows'
Assert-JsonBoolean $quickWindows 'passed' $true `
    'selfTest.compactStartup.quickWindows'
$persistentHints = @($quickWindows.persistentHints)
if ($persistentHints.Count -ne 16) {
    throw 'selfTest.compactStartup.quickWindows.persistentHints must contain 16 windows.'
}
foreach ($hint in $persistentHints) {
    if ([string]::IsNullOrWhiteSpace([string]$hint.objectName)) {
        throw 'A packaged quick-window persistence entry has no objectName.'
    }
    Assert-JsonBoolean $hint 'persistentGraphics' $false `
        'selfTest.compactStartup.quickWindows.persistentHints'
    Assert-JsonBoolean $hint 'persistentSceneGraph' $false `
        'selfTest.compactStartup.quickWindows.persistentHints'
}
if ($null -eq $selfTest.compactStartup.PSObject.Properties['compactIdle']) {
    throw 'selfTest.compactStartup.compactIdle is missing.'
}
$compactIdle = $selfTest.compactStartup.compactIdle
Assert-JsonBoolean $compactIdle 'lowPower' $true `
    'selfTest.compactStartup.compactIdle'
Assert-JsonBoolean $compactIdle 'passed' $true `
    'selfTest.compactStartup.compactIdle'
$targetFps = $compactIdle.PSObject.Properties['targetFps']
if ($null -eq $targetFps -or
    ($targetFps.Value -isnot [int] -and $targetFps.Value -isnot [long]) -or
    [long]$targetFps.Value -ne 15) {
    throw 'selfTest.compactStartup.compactIdle.targetFps must be the integer 15.'
}
if ($null -eq $selfTest.compactStartup.PSObject.Properties['sceneLoaders']) {
    throw 'selfTest.compactStartup.sceneLoaders is missing.'
}
$sceneLoaders = $selfTest.compactStartup.sceneLoaders
Assert-JsonBoolean $sceneLoaders 'scene2dLoaded' $false `
    'selfTest.compactStartup.sceneLoaders'
Assert-JsonBoolean $sceneLoaders 'videoLoaded' $false `
    'selfTest.compactStartup.sceneLoaders'
Assert-JsonBoolean $sceneLoaders 'passed' $true `
    'selfTest.compactStartup.sceneLoaders'
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
$worldPresentationCount = $selfTest.boxWorldPresentation.PSObject.Properties[
    'presentationCount'
]
if ($null -eq $worldPresentationCount -or
    ($worldPresentationCount.Value -isnot [int] -and
        $worldPresentationCount.Value -isnot [long]) -or
    [long]$worldPresentationCount.Value -lt 1) {
    throw 'selfTest.boxWorldPresentation.presentationCount must be a positive integer.'
}
if ($null -eq $selfTest.PSObject.Properties['syntheticProactiveBubble']) {
    throw 'selfTest.syntheticProactiveBubble is missing.'
}
foreach ($name in @(
    'windowFound',
    'windowVisible',
    'windowExposed',
    'bodyFound',
    'bodyVisible'
)) {
    Assert-JsonBoolean $selfTest.syntheticProactiveBubble $name $true 'selfTest.syntheticProactiveBubble'
}
$bubblePresentationRevision = $selfTest.syntheticProactiveBubble.PSObject.Properties[
    'presentationRevision'
]
if ($null -eq $bubblePresentationRevision -or
    ($bubblePresentationRevision.Value -isnot [int] -and
        $bubblePresentationRevision.Value -isnot [long]) -or
    [long]$bubblePresentationRevision.Value -lt 1) {
    throw 'selfTest.syntheticProactiveBubble.presentationRevision must be a positive integer.'
}
if ([string]$selfTest.syntheticProactiveBubble.summary -cne
    'packaged proactive bubble probe') {
    throw 'Packaged self-test proactive bubble content mismatch.'
}
if ($null -eq $selfTest.PSObject.Properties['dragProbe']) {
    throw 'selfTest.dragProbe is missing.'
}
foreach ($name in @(
    'previewMode',
    'nativeSystemMovePathPresent',
    'fallbackMovedWindow'
)) {
    Assert-JsonBoolean $selfTest.dragProbe $name $true 'selfTest.dragProbe'
}
foreach ($name in @(
    'nativeSystemMoveAttempted',
    'nativeSystemMoveActive',
    'nativeSystemMoveRuntimeVerified'
)) {
    Assert-JsonBoolean $selfTest.dragProbe $name $false 'selfTest.dragProbe'
}
if ([int]$compactResource.schemaVersion -ne 1 -or
    [string]$compactResource.applicationVersion -cne $ReleaseVersion -or
    [string]$compactResource.mode -cne 'compact' -or
    [string]$compactResource.platform -cne 'offscreen' -or
    [string]$compactResource.backend -cne 'software') {
    throw 'Packaged compact resource report did not satisfy the v0.3.22 release gate.'
}
if ([string]$compactResource.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged compact resource executable hash mismatch.'
}
foreach ($name in @('responding', 'probeProcessStopped', 'passed')) {
    Assert-JsonBoolean $compactResource $name $true 'compactResource'
}
if ($null -eq $compactResource.PSObject.Properties['cleanup']) {
    throw 'compactResource.cleanup is missing.'
}
Assert-JsonBoolean $compactResource.cleanup 'succeeded' $true 'compactResource.cleanup'
$cleanupAttempts = $compactResource.cleanup.PSObject.Properties['attempts']
if ($null -eq $cleanupAttempts -or
    ($cleanupAttempts.Value -isnot [int] -and $cleanupAttempts.Value -isnot [long]) -or
    [long]$cleanupAttempts.Value -lt 0 -or [long]$cleanupAttempts.Value -gt 5) {
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
    throw 'Packaged Windows startup report did not satisfy the v0.3.22 release gate.'
}
if ([string]$windowsStartup.executableSha256 -cne $candidateExeHash) {
    throw 'Packaged Windows startup executable hash mismatch.'
}
foreach ($name in @(
    'qmlLoaded',
    'nativeWindowCreated',
    'nativeWindowIdCached',
    'nativeHitTestDispatched',
    'eventLoopResponsive',
    'passed'
)) {
    Assert-JsonBoolean $windowsStartup $name $true 'windowsStartup'
}
Assert-JsonBoolean $windowsStartup 'trayPublished' $false 'windowsStartup'
$visibleQuickWindowCount = $windowsStartup.PSObject.Properties['visibleQuickWindowCount']
if ($null -eq $visibleQuickWindowCount -or
    ($visibleQuickWindowCount.Value -isnot [int] -and
        $visibleQuickWindowCount.Value -isnot [long]) -or
    [long]$visibleQuickWindowCount.Value -ne 0) {
    throw 'windowsStartup.visibleQuickWindowCount must be the integer zero.'
}
$quickWindowCount = $windowsStartup.PSObject.Properties['quickWindowCount']
if ($null -eq $quickWindowCount -or
    ($quickWindowCount.Value -isnot [int] -and
        $quickWindowCount.Value -isnot [long]) -or
    [long]$quickWindowCount.Value -lt 1 -or
    @($windowsStartup.visibleQuickWindows).Count -ne 0) {
    throw 'Windows startup probe did not create only hidden QML windows.'
}
$nativeHitTestCount = $windowsStartup.PSObject.Properties['nativeHitTestCount']
$nativeDispatchCount = $windowsStartup.PSObject.Properties['nativeDispatchCount']
$eventLoopTicks = $windowsStartup.PSObject.Properties['eventLoopTicks']
if ($null -eq $nativeHitTestCount -or $null -eq $nativeDispatchCount -or
    $null -eq $eventLoopTicks -or
    [long]$nativeHitTestCount.Value -lt 1 -or
    [long]$nativeDispatchCount.Value -lt [long]$nativeHitTestCount.Value -or
    [long]$eventLoopTicks.Value -lt 4) {
    throw 'Windows startup probe did not exercise a responsive native event loop.'
}
if (-not [string]::IsNullOrEmpty([string]$windowsStartup.dispatchError)) {
    throw "Windows startup native dispatch failed: $($windowsStartup.dispatchError)"
}

$exeBuiltAt = $candidateExeItem.LastWriteTimeUtc
$releaseGateNow = [DateTimeOffset]::UtcNow.UtcDateTime
foreach ($report in @($selfTest, $compactResource, $windowsStartup)) {
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
Assert-MatchingReleaseFile 'qml\V03FocusTimerAura.qml' '_internal\qml\V03FocusTimerAura.qml'
Assert-MatchingReleaseFile 'qml\V03BoxWorldScene.qml' '_internal\qml\V03BoxWorldScene.qml'
Assert-MatchingReleaseFile 'themes\first-encounter\theme.json' '_internal\themes\first-encounter\theme.json'
Assert-MatchingReleaseFile 'themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png' '_internal\themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png'
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
    'LiliesInTheBox.spec',
    'main.py',
    'scripts\build_windows.ps1',
    'packaging\windows_version_info.txt',
    'scripts\promote_v0313.ps1',
    'scripts\promote_v0315.ps1',
    'scripts\promote_v0316.ps1',
    'scripts\promote_v0317.ps1',
    'scripts\promote_v0318.ps1',
    'scripts\promote_v0319.ps1',
    'scripts\promote_v0320.ps1',
    'scripts\promote_v0321.ps1',
    'qml\Main.qml',
    'qml\CompanionBubble.qml',
    'qml\V03BoxWorldScene.qml',
    'qml\V03FocusTimerAura.qml',
    'qml\V03PetBody.qml',
    'qml\V03WorkPanel.qml',
    'src\lilies\app.py',
    'src\lilies\__main__.py',
    'src\lilies\backend.py',
    'src\lilies\companion_controller.py',
    'src\lilies\core\companion.py',
    'src\lilies\core\companion_runtime.py',
    'src\lilies\core\orchestration.py',
    'src\lilies\core\pet_habitat.py',
    'src\lilies\core\productivity.py',
    'src\lilies\core\themes.py',
    'scripts\verify_box_world_presentation.py',
    'scripts\verify_box_world_scene.py',
    'scripts\verify_box_world_ui.py',
    'scripts\verify_codex_subscription_smoke.py',
    'scripts\verify_companion_flow_ui.py',
    'scripts\verify_companion_bubble_matrix.py',
    'scripts\verify_companion_observer.py',
    'scripts\verify_compact_resources.py',
    'scripts\verify_compact_ui.py',
    'scripts\verify_focus_timer_aura.py',
    'scripts\verify_habitat_ui.py',
    'scripts\verify_focus_main_integration.py',
    'scripts\verify_packaged_windows_startup.ps1',
    'scripts\verify_packaged_compact_resources.ps1',
    'scripts\verify_pose_geometry_ui.py',
    'scripts\verify_pose_outfit_policy_ui.py',
    'scripts\verify_selection_ui.py',
    'scripts\verify_transient_window_resources.py',
    'tests\test_backend_v03_contract.py',
    'tests\test_box_world_presentation_offscreen.py',
    'tests\test_box_world_scene_offscreen.py',
    'tests\test_box_world_ui_offscreen.py',
    'tests\test_codex_subscription_v02.py',
    'tests\test_compact_hit_test.py',
    'tests\test_compact_animation_budget_v0320.py',
    'tests\test_compact_resource_lifecycle.py',
    'tests\test_compact_ui_offscreen.py',
    'tests\test_companion.py',
    'tests\test_companion_controller.py',
    'tests\test_companion_discoverability_qml.py',
    'tests\test_companion_presentation_gate_qml.py',
    'tests\test_companion_flow_ui_offscreen.py',
    'tests\test_companion_bubbles_offscreen.py',
    'tests\test_cross_dpi_layout_v0312.py',
    'tests\test_focus_timer_aura_qml.py',
    'tests\test_focus_main_integration_qml.py',
    'tests\test_habitat_ui_offscreen.py',
    'tests\test_orchestration_v03.py',
    'tests\test_native_drag_press_contract_v0320.py',
    'tests\test_pose_asset_gate.py',
    'tests\test_packaged_user_flow_self_test_contract.py',
    'tests\test_pet_habitat_v03.py',
    'tests\test_pose_geometry_offscreen.py',
    'tests\test_pose_outfit_policy_offscreen.py',
    'tests\test_promotion_retry_contract.py',
    'tests\test_process_entrypoints.py',
    'tests\test_single_instance_activation.py',
    'tests\test_quick_window_resource_lifecycle.py',
    'tests\test_release_probe_cleanup_contract.py',
    'tests\test_runtime_snapshot_socket.py',
    'tests\test_theme_socket.py',
    'tests\test_version_alignment_v0318.py',
    'tests\test_version_alignment_v0319.py',
    'tests\test_version_alignment_v0320.py',
    'tests\test_version_alignment_v0321.py',
    'tests\test_version_alignment_v0322.py',
    'tests\test_windows_startup_probe_contract.py',
    'tests\test_wardrobe_manifest_contract.py',
    'tests\test_windows_version_resource_contract.py',
    'themes\first-encounter\assets\lilith-pose-expansion-sheet-v1.png',
    'themes\first-encounter\assets\lilith-pose-focus-kneel-v1.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v1-rgb.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v2-rgb.png',
    'art-reference\generated-v0.3\lilith-pose-responsive-concept-v1.md',
    'artifacts\packaged-compact-resource-v0322.json',
    'artifacts\packaged-windows-startup-v0322.json'
)

& $Engine `
    -FormalRoot $FormalRoot `
    -ReleaseVersion $ReleaseVersion `
    -PromotionScript 'scripts\promote_v0322.ps1' `
    -PackagedReport 'artifacts\packaged-self-test-v0322.json' `
    -AdditionalFiles $AdditionalFiles
