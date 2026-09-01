from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMOTION_ENGINE = PROJECT_ROOT / "scripts" / "promote_v0313.ps1"
RELEASE_WRAPPER = PROJECT_ROOT / "scripts" / "promote_v0340.ps1"


def _powershell_function(source: str, name: str) -> str:
    match = re.search(rf"(?m)^function\s+{re.escape(name)}\b", source)
    assert match is not None, f"missing PowerShell function: {name}"
    opening = source.find("{", match.end())
    assert opening >= 0
    depth = 0
    quote = ""
    index = opening
    while index < len(source):
        character = source[index]
        if quote:
            if character == "`":
                index += 2
                continue
            if character == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 2
                    continue
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
        index += 1
    raise AssertionError(f"unterminated PowerShell function: {name}")


def _run_powershell(
    tmp_path: Path,
    function_names: tuple[str, ...],
    body: str,
) -> dict[str, object]:
    source = PROMOTION_ENGINE.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _powershell_function(source, name) for name in function_names
    )
    environment = dict(os.environ)
    environment["LILIES_RETRY_TEST_ROOT"] = str(tmp_path / "release-transaction")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            functions + "\n$ErrorActionPreference = 'Stop'\n" + body,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_release_transaction_contract_is_narrow_and_fail_safe() -> None:
    source = PROMOTION_ENGINE.read_text(encoding="utf-8")
    wrapper = RELEASE_WRAPPER.read_text(encoding="utf-8")
    retry_function = _powershell_function(source, "Move-ReleaseTreeWithRetry")
    remove_function = _powershell_function(source, "Remove-ReleaseScratchWithRetry")
    cleanup_function = _powershell_function(source, "Complete-ReleaseScratchCleanup")
    rollback_function = _powershell_function(source, "Invoke-ReleaseRollback")

    assert retry_function.count("catch [System.UnauthorizedAccessException]") == 1
    assert retry_function.count("catch [System.IO.IOException]") == 1
    assert re.search(r"(?m)^\s*catch\s*\{", retry_function) is None
    assert "[ValidateRange(4, 5)][int]$MaxAttempts = 5" in retry_function
    assert "Start-Sleep -Milliseconds $delay" in retry_function
    assert not re.search(r"(?im)^\s*Move-Item\b[^\r\n]*-Force\b", source)
    assert len(re.findall(r"(?m)^\s*Move-Item\b", retry_function)) == 2
    assert len(re.findall(r"(?m)^\s*Copy-Item\b", retry_function)) == 1
    assert not re.search(r"(?im)^\s*Copy-Item\b[^\r\n]*-Force\b", retry_function)
    assert "CopyFallbackIncoming" in retry_function
    assert "'.release-incoming-'" in retry_function
    assert retry_function.index(
        "Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedIncoming"
    ) < retry_function.index("-Source $resolvedIncoming")

    promotion_flow = source[source.index("$RelativeFiles = @(") :]
    assert promotion_flow.count("-AllowCopyFallback") == 1
    assert promotion_flow.count("-CopyFallbackIncoming") == 1
    assert "[guid]::NewGuid().ToString('N')" in promotion_flow
    install_flow = promotion_flow[
        promotion_flow.index("$stageDistSnapshot") :
        promotion_flow.index("Assert-MatchingTrees $CandidateDist $FormalDist")
    ]
    assert install_flow.index("$newDistInstalled = $true") < install_flow.index(
        "Move-ReleaseTreeWithRetry"
    )
    assert "-CopyFallbackIncoming $IncomingDist" in install_flow
    backup_flow = promotion_flow[
        promotion_flow.index("$formalDistSnapshot") :
        promotion_flow.index("$stageDistSnapshot")
    ]
    assert backup_flow.index("$distBackedUp = $true") < backup_flow.index(
        "Move-ReleaseTreeWithRetry"
    )
    assert promotion_flow.index("$appliedFiles += $relativePath") < promotion_flow.index(
        "Copy-Item -LiteralPath $staged -Destination $target -Force"
    )
    assert "-Records $rollbackRecords" in promotion_flow

    assert remove_function.count("catch [System.UnauthorizedAccessException]") == 1
    assert remove_function.count("catch [System.IO.IOException]") == 1
    assert "[ValidateRange(2, 5)][int]$MaxAttempts = 5" in remove_function
    assert "Write-Warning" in cleanup_function
    assert "cleanupPending" in cleanup_function
    assert "PromotionManifestPath" in cleanup_function
    assert "Complete-ReleaseScratchCleanup" in promotion_flow
    assert "Release cleanup remains pending" in promotion_flow

    assert rollback_function.count("} catch {") >= 3
    assert "-AllowCopyFallback" not in rollback_function
    assert "Rollback could not quarantine" in rollback_function
    assert "Rollback could not restore the backed-up formal dist" in rollback_function
    assert "Rollback could not restore source file" in rollback_function
    assert "-LiteralPath $BackupDist" in rollback_function
    assert "-Source $resolvedRestoreIncoming" in rollback_function
    assert "Assert-MatchingTreeSnapshot $backupDistSnapshot $BackupDist" in rollback_function
    assert "[AggregateException]::new(" in promotion_flow
    assert "Invoke-ReleaseRollback" in promotion_flow

    assert "'scripts\\promote_v0313.ps1'" in wrapper
    assert "'tests\\test_promotion_retry_contract.py'" in wrapper
    assert "$ReleaseVersion = '0.3.40'" in wrapper
    assert "$FileVersion = '0.3.40.0'" in wrapper
    assert "packaged-self-test-v0340.json" in wrapper
    assert "packaged-compact-resource-v0340.json" in wrapper
    assert "packaged-windows-startup-v0340.json" in wrapper
    assert "pose-click-mask-v0340.json" in wrapper
    assert "v0.3.40 release gate" in wrapper
    assert "Assert-VersionInfoString" in wrapper
    assert "compactResource.cleanup 'succeeded'" in wrapper
    assert "compactResource.cleanup.attempts" in wrapper
    assert "compactResource.cleanup.errors is missing" in wrapper
    for required in (
        "'LiliesInTheBox.spec'",
        "'packaging\\hooks\\hook-PySide6.QtQml.py'",
        "'packaging\\windows_version_info.txt'",
        "'scripts\\promote_v0318.ps1'",
        "'scripts\\promote_v0319.ps1'",
        "'scripts\\promote_v0320.ps1'",
        "'scripts\\promote_v0321.ps1'",
        "'scripts\\promote_v0322.ps1'",
        "'scripts\\promote_v0323.ps1'",
        "'scripts\\promote_v0324.ps1'",
        "'scripts\\promote_v0325.ps1'",
        "'scripts\\promote_v0326.ps1'",
        "'scripts\\promote_v0327.ps1'",
        "'scripts\\promote_v0328.ps1'",
        "'scripts\\promote_v0329.ps1'",
        "'scripts\\promote_v0330.ps1'",
        "'scripts\\promote_v0331.ps1'",
        "'scripts\\promote_v0332.ps1'",
        "'scripts\\promote_v0333.ps1'",
        "'scripts\\promote_v0334.ps1'",
        "'scripts\\promote_v0335.ps1'",
        "'scripts\\promote_v0336.ps1'",
        "'scripts\\promote_v0337.ps1'",
        "'scripts\\promote_v0338.ps1'",
        "'scripts\\promote_v0339.ps1'",
        "'scripts\\promote_v0340.ps1'",
        "'docs\\pose-asset-gate.md'",
        "'scripts\\build_windows.ps1'",
        "'scripts\\inspect_desktop_surface.py'",
        "'qml\\Main.qml'",
        "'qml\\CompanionBubble.qml'",
        "'qml\\FocusDiversionBubble.qml'",
        "'qml\\V03ConnectorSetup.qml'",
        "'qml\\V03Dock.qml'",
        "'qml\\V03BoxWorldScene.qml'",
        "'qml\\V03FocusTimerAura.qml'",
        "'qml\\V03PetBody.qml'",
        "'qml\\V03PetPoseResolver.qml'",
        "'qml\\V03WorkPanel.qml'",
        "'src\\lilies\\app.py'",
        "'src\\lilies\\paths.py'",
        "'src\\lilies\\backend.py'",
        "'src\\lilies\\companion_controller.py'",
        "'src\\lilies\\connectors\\slack_socket.py'",
        "'src\\lilies\\connectors\\slack_websocket.py'",
        "'src\\lilies\\core\\activity.py'",
        "'src\\lilies\\core\\data_migration.py'",
        "'src\\lilies\\core\\database.py'",
        "'src\\lilies\\core\\focus_diversion.py'",
        "'src\\lilies\\core\\model.py'",
        "'src\\lilies\\core\\pet_habitat.py'",
        "'src\\lilies\\core\\socket_server.py'",
        "'src\\lilies\\core\\themes.py'",
        "'scripts\\verify_box_world_scene.py'",
        "'scripts\\verify_box_world_click_path.py'",
        "'scripts\\verify_entry_actions.py'",
        "'scripts\\verify_main_qml_ui.py'",
        "'scripts\\verify_dock_ui.py'",
        "'scripts\\verify_companion_bubble_matrix.py'",
        "'scripts\\verify_companion_frequency_draft.py'",
        "'scripts\\verify_companion_flow_ui.py'",
        "'scripts\\verify_companion_bubbles.py'",
        "'scripts\\verify_drag_runtime_v0325.py'",
        "'scripts\\verify_packaged_compact_resources.ps1'",
        "'scripts\\verify_packaged_windows_startup.ps1'",
        "'scripts\\verify_pose_assets.py'",
        "'scripts\\verify_pet_pose_resolver.py'",
        "'scripts\\verify_pose_click_masks.py'",
        "'scripts\\verify_native_window_capture.py'",
        "'scripts\\verify_pose_geometry_ui.py'",
        "'scripts\\verify_focus_timer_aura.py'",
        "'scripts\\verify_focus_main_integration.py'",
        "'scripts\\verify_desktop_surface_replay_v0328.py'",
        "'scripts\\verify_pose_outfit_policy_ui.py'",
        "'scripts\\verify_procedural_habitat_poses.py'",
        "'tests\\conftest.py'",
        "'tests\\test_box_world_scene_offscreen.py'",
        "'tests\\test_box_world_click_path_offscreen.py'",
        "'tests\\test_backend_v03_contract.py'",
        "'tests\\test_browser_capture_safety_v0332.py'",
        "'tests\\test_companion_bubbles_offscreen.py'",
        "'tests\\test_atomic_companion_frequency.py'",
        "'tests\\test_companion_controller.py'",
        "'tests\\test_companion_preferences_signal.py'",
        "'tests\\test_companion_frequency_projection_hardening.py'",
        "'tests\\test_companion_resilience_v0333.py'",
        "'tests\\test_companion_frequency_draft_v0332.py'",
        "'tests\\test_companion_flow_ui_offscreen.py'",
        "'tests\\test_companion_discoverability_qml.py'",
        "'tests\\test_companion_native_presentation.py'",
        "'tests\\test_companion_persistence.py'",
        "'tests\\test_data_migration.py'",
        "'tests\\test_drag_runtime_v0325.py'",
        "'tests\\test_dock_ui_offscreen.py'",
        "'tests\\test_entry_actions_offscreen.py'",
        "'tests\\test_desktop_native_presentation_v0328.py'",
        "'tests\\test_desktop_surface_replay_v0328.py'",
        "'tests\\test_packaged_user_flow_self_test_contract.py'",
        "'tests\\test_pose_outfit_policy_offscreen.py'",
        "'tests\\test_release_probe_cleanup_contract.py'",
        "'tests\\test_release_focus_gate_v0329.py'",
        "'tests\\test_release_focus_gate_v0330.py'",
        "'tests\\test_release_focus_gate_v0331.py'",
        "'tests\\test_release_focus_gate_v0332.py'",
        "'tests\\test_release_focus_gate_v0333.py'",
        "'tests\\test_release_qt_cache_gate_v0331.py'",
        "'tests\\test_release_qt_cache_gate_v0332.py'",
        "'tests\\test_release_qt_cache_gate_v0333.py'",
        "'tests\\test_release_focus_gate_v0334.py'",
        "'tests\\test_release_qt_cache_gate_v0334.py'",
        "'tests\\test_single_instance_activation.py'",
        "'tests\\test_slack_socket_v03.py'",
        "'tests\\test_slack_websocket_transport.py'",
        "'tests\\test_selection.py'",
        "'tests\\test_windows_startup_probe_contract.py'",
        "'tests\\test_focus_timer_aura_qml.py'",
        "'tests\\test_focus_main_integration_qml.py'",
        "'tests\\test_focus_diversion_v03.py'",
        "'tests\\test_habitat_pose_variants_v0322.py'",
        "'tests\\test_activity_context.py'",
        "'tests\\test_model_broker_wiring_v03.py'",
        "'tests\\test_main_qml_ui_v0333.py'",
        "'tests\\test_outfit_asset_gate.py'",
        "'tests\\test_optional_habitat_pose_gate_v0326.py'",
        "'tests\\test_packaging_qml_pruning_v0340.py'",
        "'tests\\test_idle_runtime_budget_v0340.py'",
        "'tests\\test_qml_warning_release_gate_v0340.py'",
        "'tests\\test_packaged_footprint_gate_v0340.py'",
        "'tests\\test_pet_habitat_v03.py'",
        "'tests\\test_pet_pose_resolver_v0332.py'",
        "'tests\\test_pose_asset_gate.py'",
        "'tests\\test_pose_click_mask_qml_v0329.py'",
        "'tests\\test_pose_geometry_offscreen.py'",
        "'tests\\test_procedural_habitat_poses_v0326.py'",
        "'tests\\test_version_alignment_v0318.py'",
        "'tests\\test_version_alignment_v0319.py'",
        "'tests\\test_version_alignment_v0320.py'",
        "'tests\\test_version_alignment_v0321.py'",
        "'tests\\test_version_alignment_v0322.py'",
        "'tests\\test_version_alignment_v0323.py'",
        "'tests\\test_version_alignment_v0324.py'",
        "'tests\\test_version_alignment_v0325.py'",
        "'tests\\test_version_alignment_v0326.py'",
        "'tests\\test_version_alignment_v0327.py'",
        "'tests\\test_version_alignment_v0328.py'",
        "'tests\\test_version_alignment_v0329.py'",
        "'tests\\test_version_alignment_v0330.py'",
        "'tests\\test_version_alignment_v0331.py'",
        "'tests\\test_version_alignment_v0332.py'",
        "'tests\\test_version_alignment_v0333.py'",
        "'tests\\test_version_alignment_v0334.py'",
        "'tests\\test_version_alignment_v0335.py'",
        "'tests\\test_version_alignment_v0336.py'",
        "'tests\\test_version_alignment_v0337.py'",
        "'tests\\test_version_alignment_v0338.py'",
        "'tests\\test_version_alignment_v0339.py'",
        "'tests\\test_version_alignment_v0340.py'",
        "'tests\\test_native_window_capture_probe_v0334.py'",
        "'tests\\test_qt_cache_routing_v0331.py'",
        "'tests\\test_qt_cache_routing_v0332.py'",
        "'tests\\test_wardrobe_manifest_contract.py'",
        "'tests\\test_windows_version_resource_contract.py'",
        "'art-reference\\generated-v0.3\\lilith-pose-responsive-concept-v1-rgb.png'",
        "'art-reference\\generated-v0.3\\lilith-pose-responsive-concept-v2-rgb.png'",
        "'art-reference\\generated-v0.3\\lilith-pose-responsive-concept-v1.md'",
        "'art-reference\\generated-v0.3\\lilith-pose-micro-corner-grip-draft-v6-baked-checkerboard.png'",
        "'art-reference\\generated-v0.3\\README.md'",
        "'themes\\first-encounter\\assets\\lilith-pose-focus-kneel-v1.png'",
        "'artifacts\\pose-click-mask-v0329.json'",
        "'artifacts\\pose-click-mask-v0330.json'",
        "'artifacts\\pose-click-mask-v0331.json'",
        "'artifacts\\pose-click-mask-v0332.json'",
        "'artifacts\\pose-click-mask-v0334.json'",
        "'artifacts\\pose-asset-gate.json'",
        "'artifacts\\procedural-habitat-pose-gate.json'",
        "'artifacts\\habitat-pose-coverage.json'",
    ):
        assert required in wrapper

    spec = (PROJECT_ROOT / "LiliesInTheBox.spec").read_text(encoding="utf-8")
    theme = (PROJECT_ROOT / "themes" / "first-encounter" / "theme.json").read_text(
        encoding="utf-8"
    )
    assert "art-reference" not in spec
    assert "lilith-pose-responsive-concept" not in spec
    assert "lilith-pose-responsive-concept" not in theme


def test_v0340_additional_release_files_exist_before_build() -> None:
    wrapper = RELEASE_WRAPPER.read_text(encoding="utf-8")
    block = wrapper[
        wrapper.index("$AdditionalFiles = @(") : wrapper.index("\n& $Engine `")
    ]
    relative_files = re.findall(r"(?m)^\s*'([^']+)'\s*,?\s*$", block)
    assert relative_files
    assert len(relative_files) == len(set(relative_files))

    # These reports are deliberately created by release verification or a real
    # post-build run. Every source input must already exist in the candidate.
    generated_reports = {
        "artifacts\\pose-click-mask-v0334.json",
        "artifacts\\packaged-compact-resource-v0334.json",
        "artifacts\\packaged-windows-startup-v0334.json",
        "artifacts\\pose-click-mask-v0335.json",
        "artifacts\\packaged-self-test-v0335.json",
        "artifacts\\packaged-compact-resource-v0335.json",
        "artifacts\\packaged-windows-startup-v0335.json",
        "artifacts\\pose-click-mask-v0336.json",
        "artifacts\\packaged-self-test-v0336.json",
        "artifacts\\packaged-compact-resource-v0336.json",
        "artifacts\\packaged-windows-startup-v0336.json",
        "artifacts\\pose-click-mask-v0337.json",
        "artifacts\\packaged-self-test-v0337.json",
        "artifacts\\packaged-compact-resource-v0337.json",
        "artifacts\\packaged-windows-startup-v0337.json",
        "artifacts\\pose-click-mask-v0338.json",
        "artifacts\\packaged-self-test-v0338.json",
        "artifacts\\packaged-compact-resource-v0338.json",
        "artifacts\\packaged-windows-startup-v0338.json",
        "artifacts\\pose-click-mask-v0339.json",
        "artifacts\\packaged-self-test-v0339.json",
        "artifacts\\packaged-compact-resource-v0339.json",
        "artifacts\\packaged-windows-startup-v0339.json",
        "artifacts\\pose-click-mask-v0340.json",
        "artifacts\\packaged-self-test-v0340.json",
        "artifacts\\packaged-compact-resource-v0340.json",
        "artifacts\\packaged-windows-startup-v0340.json",
    }
    missing = [
        relative
        for relative in relative_files
        if relative not in generated_reports
        and not (PROJECT_ROOT / Path(relative.replace("\\", "/"))).is_file()
    ]
    assert missing == []


def test_atomic_move_retry_uses_verified_sibling_incoming(tmp_path: Path) -> None:
    report = _run_powershell(
        tmp_path,
        (
            "Get-ReleaseTreeSnapshot",
            "Assert-MatchingTreeSnapshot",
            "Move-ReleaseTreeWithRetry",
        ),
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_RETRY_TEST_ROOT)
New-Item -ItemType Directory -Path $root -Force | Out-Null

function New-TestTree([string]$Name, [string]$Payload) {
    $tree = Join-Path $root $Name
    New-Item -ItemType Directory -Path (Join-Path $tree 'nested') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $tree 'nested\payload.txt') -Value $Payload -Encoding UTF8
    return $tree
}

$retrySource = New-TestTree 'retry-source' 'retry-content'
$retryDestination = Join-Path $root 'retry-destination'
$retrySnapshot = @(Get-ReleaseTreeSnapshot $retrySource)
$script:retryAttempts = 0
$retryMover = {
    param($Source, $Destination)
    $script:retryAttempts++
    if ($script:retryAttempts -lt 3) {
        throw [System.UnauthorizedAccessException]::new('transient access denial')
    }
    Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
}
Move-ReleaseTreeWithRetry `
    -Source $retrySource `
    -Destination $retryDestination `
    -ExpectedSnapshot $retrySnapshot `
    -InitialDelayMilliseconds 0 `
    -MoveOperation $retryMover

$completedSource = New-TestTree 'completed-source' 'completed-content'
$completedDestination = Join-Path $root 'completed-destination'
$completedSnapshot = @(Get-ReleaseTreeSnapshot $completedSource)
$script:completedAttempts = 0
$completedMover = {
    param($Source, $Destination)
    $script:completedAttempts++
    Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
    throw [System.UnauthorizedAccessException]::new('provider reported after rename')
}
Move-ReleaseTreeWithRetry `
    -Source $completedSource `
    -Destination $completedDestination `
    -ExpectedSnapshot $completedSnapshot `
    -InitialDelayMilliseconds 0 `
    -MoveOperation $completedMover

$ioSource = New-TestTree 'io-source' 'io-content'
$ioDestination = Join-Path $root 'io-destination'
$ioSnapshot = @(Get-ReleaseTreeSnapshot $ioSource)
$script:ioAttempts = 0
$ioMover = {
    param($Source, $Destination)
    $script:ioAttempts++
    throw [System.IO.IOException]::new('persistent I/O failure')
}
$ioCaught = $false
try {
    Move-ReleaseTreeWithRetry `
        -Source $ioSource `
        -Destination $ioDestination `
        -ExpectedSnapshot $ioSnapshot `
        -InitialDelayMilliseconds 0 `
        -MoveOperation $ioMover
} catch [System.IO.IOException] {
    $ioCaught = $true
}

$copySource = New-TestTree 'copy-source' 'copy-content'
$copyDestination = Join-Path $root 'copy-destination'
$copyIncoming = Join-Path $root '.release-incoming-copy-success'
$copySnapshot = @(Get-ReleaseTreeSnapshot $copySource)
$script:copyMoveAttempts = 0
$script:copyFinalAttempts = 0
$copyMover = {
    param($Source, $Destination)
    $script:copyMoveAttempts++
    throw [System.IO.IOException]::new('stage rename remains unavailable')
}
$copyFinalMover = {
    param($Source, $Destination)
    $script:copyFinalAttempts++
    Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
}
Move-ReleaseTreeWithRetry `
    -Source $copySource `
    -Destination $copyDestination `
    -ExpectedSnapshot $copySnapshot `
    -InitialDelayMilliseconds 0 `
    -AllowCopyFallback `
    -CopyFallbackIncoming $copyIncoming `
    -MoveOperation $copyMover `
    -FinalMoveOperation $copyFinalMover
Assert-MatchingTreeSnapshot $copySnapshot $copyDestination

$lockedSource = New-TestTree 'locked-source' 'locked-content'
$lockedDestination = Join-Path $root 'locked-destination'
$lockedIncoming = Join-Path $root '.release-incoming-final-locked'
$lockedSnapshot = @(Get-ReleaseTreeSnapshot $lockedSource)
$script:lockedInitialAttempts = 0
$script:lockedFinalAttempts = 0
$lockedInitialMover = {
    param($Source, $Destination)
    $script:lockedInitialAttempts++
    throw [System.IO.IOException]::new('stage rename remains unavailable')
}
$lockedFinalMover = {
    param($Source, $Destination)
    $script:lockedFinalAttempts++
    throw [System.UnauthorizedAccessException]::new('incoming is scanner-locked')
}
$lockedCaught = $false
try {
    Move-ReleaseTreeWithRetry `
        -Source $lockedSource `
        -Destination $lockedDestination `
        -ExpectedSnapshot $lockedSnapshot `
        -InitialDelayMilliseconds 0 `
        -AllowCopyFallback `
        -CopyFallbackIncoming $lockedIncoming `
        -MoveOperation $lockedInitialMover `
        -FinalMoveOperation $lockedFinalMover
} catch [System.UnauthorizedAccessException] {
    $lockedCaught = $true
}
Assert-MatchingTreeSnapshot $lockedSnapshot $lockedIncoming

$partialSource = New-TestTree 'partial-source' 'partial-content'
Set-Content -LiteralPath (Join-Path $partialSource 'nested\second.txt') -Value 'second' -Encoding UTF8
$partialDestination = Join-Path $root 'partial-destination'
$partialIncoming = Join-Path $root '.release-incoming-partial-copy'
$partialSnapshot = @(Get-ReleaseTreeSnapshot $partialSource)
$script:partialInitialAttempts = 0
$partialInitialMover = {
    param($Source, $Destination)
    $script:partialInitialAttempts++
    throw [System.IO.IOException]::new('stage rename remains unavailable')
}
$partialCopy = {
    param($Source, $Destination)
    New-Item -ItemType Directory -Path (Join-Path $Destination 'nested') -Force | Out-Null
    Copy-Item `
        -LiteralPath (Join-Path $Source 'nested\payload.txt') `
        -Destination (Join-Path $Destination 'nested\payload.txt') `
        -ErrorAction Stop
    throw [System.IO.IOException]::new('copy interrupted')
}
$partialCaught = $false
try {
    Move-ReleaseTreeWithRetry `
        -Source $partialSource `
        -Destination $partialDestination `
        -ExpectedSnapshot $partialSnapshot `
        -InitialDelayMilliseconds 0 `
        -AllowCopyFallback `
        -CopyFallbackIncoming $partialIncoming `
        -MoveOperation $partialInitialMover `
        -CopyOperation $partialCopy
} catch [System.IO.IOException] {
    $partialCaught = $true
}

$fatalSource = New-TestTree 'fatal-source' 'fatal-content'
$fatalDestination = Join-Path $root 'fatal-destination'
$fatalIncoming = Join-Path $root '.release-incoming-fatal'
$fatalSnapshot = @(Get-ReleaseTreeSnapshot $fatalSource)
$script:fatalAttempts = 0
$script:fatalCopyAttempts = 0
$fatalMover = {
    param($Source, $Destination)
    $script:fatalAttempts++
    throw [System.InvalidOperationException]::new('non-retryable failure')
}
$fatalCopy = {
    param($Source, $Destination)
    $script:fatalCopyAttempts++
}
$fatalCaught = $false
try {
    Move-ReleaseTreeWithRetry `
        -Source $fatalSource `
        -Destination $fatalDestination `
        -ExpectedSnapshot $fatalSnapshot `
        -InitialDelayMilliseconds 0 `
        -AllowCopyFallback `
        -CopyFallbackIncoming $fatalIncoming `
        -MoveOperation $fatalMover `
        -CopyOperation $fatalCopy
} catch [System.InvalidOperationException] {
    $fatalCaught = $true
}

$ambiguousSource = New-TestTree 'ambiguous-source' 'ambiguous-content'
$ambiguousDestination = Join-Path $root 'ambiguous-destination'
$ambiguousIncoming = Join-Path $root '.release-incoming-ambiguous'
$ambiguousSnapshot = @(Get-ReleaseTreeSnapshot $ambiguousSource)
$script:ambiguousMoveAttempts = 0
$script:ambiguousCopyAttempts = 0
$ambiguousMover = {
    param($Source, $Destination)
    $script:ambiguousMoveAttempts++
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -ErrorAction Stop
    throw [System.IO.IOException]::new('provider left both names visible')
}
$ambiguousCopy = {
    param($Source, $Destination)
    $script:ambiguousCopyAttempts++
}
$ambiguousCaught = $false
try {
    Move-ReleaseTreeWithRetry `
        -Source $ambiguousSource `
        -Destination $ambiguousDestination `
        -ExpectedSnapshot $ambiguousSnapshot `
        -InitialDelayMilliseconds 0 `
        -AllowCopyFallback `
        -CopyFallbackIncoming $ambiguousIncoming `
        -MoveOperation $ambiguousMover `
        -CopyOperation $ambiguousCopy
} catch {
    $ambiguousCaught = $_.Exception.Message -like '*ambiguous duplicate*'
}

[ordered]@{
    retryAttempts = $script:retryAttempts
    retrySucceeded = (-not (Test-Path -LiteralPath $retrySource)) -and
        (Test-Path -LiteralPath $retryDestination -PathType Container)
    completedAttempts = $script:completedAttempts
    completedAfterReportedFailure = (-not (Test-Path -LiteralPath $completedSource)) -and
        (Test-Path -LiteralPath $completedDestination -PathType Container)
    ioAttempts = $script:ioAttempts
    ioRethrown = $ioCaught
    ioSourcePreserved = (Test-Path -LiteralPath $ioSource -PathType Container) -and
        (-not (Test-Path -LiteralPath $ioDestination))
    copyMoveAttempts = $script:copyMoveAttempts
    copyFinalAttempts = $script:copyFinalAttempts
    copySourcePreserved = Test-Path -LiteralPath $copySource -PathType Container
    copyIncomingConsumed = -not (Test-Path -LiteralPath $copyIncoming)
    lockedInitialAttempts = $script:lockedInitialAttempts
    lockedFinalAttempts = $script:lockedFinalAttempts
    lockedRejectedWithoutFormal = $lockedCaught -and
        (-not (Test-Path -LiteralPath $lockedDestination)) -and
        (Test-Path -LiteralPath $lockedIncoming -PathType Container)
    partialCopyIsolated = $partialCaught -and
        (-not (Test-Path -LiteralPath $partialDestination)) -and
        (Test-Path -LiteralPath $partialIncoming -PathType Container) -and
        (-not (Test-Path -LiteralPath (Join-Path $partialIncoming 'nested\second.txt')))
    partialInitialAttempts = $script:partialInitialAttempts
    fatalAttempts = $script:fatalAttempts
    fatalCopyAttempts = $script:fatalCopyAttempts
    fatalRethrown = $fatalCaught
    ambiguousMoveAttempts = $script:ambiguousMoveAttempts
    ambiguousCopyAttempts = $script:ambiguousCopyAttempts
    ambiguousRejected = $ambiguousCaught
} | ConvertTo-Json -Compress
''',
    )
    assert report == {
        "retryAttempts": 3,
        "retrySucceeded": True,
        "completedAttempts": 1,
        "completedAfterReportedFailure": True,
        "ioAttempts": 5,
        "ioRethrown": True,
        "ioSourcePreserved": True,
        "copyMoveAttempts": 5,
        "copyFinalAttempts": 1,
        "copySourcePreserved": True,
        "copyIncomingConsumed": True,
        "lockedInitialAttempts": 5,
        "lockedFinalAttempts": 5,
        "lockedRejectedWithoutFormal": True,
        "partialCopyIsolated": True,
        "partialInitialAttempts": 5,
        "fatalAttempts": 1,
        "fatalCopyAttempts": 0,
        "fatalRethrown": True,
        "ambiguousMoveAttempts": 1,
        "ambiguousCopyAttempts": 0,
        "ambiguousRejected": True,
    }


def test_cleanup_is_nonfatal_and_rollback_phases_continue(tmp_path: Path) -> None:
    report = _run_powershell(
        tmp_path,
        (
            "Assert-ChildPath",
            "Assert-ReleaseScratch",
            "Get-ReleaseTreeSnapshot",
            "Assert-MatchingTreeSnapshot",
            "Move-ReleaseTreeWithRetry",
            "Remove-ReleaseScratchWithRetry",
            "Complete-ReleaseScratchCleanup",
            "Invoke-ReleaseRollback",
        ),
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_RETRY_TEST_ROOT)
New-Item -ItemType Directory -Path $root -Force | Out-Null

function New-TreeAt([string]$Tree, [string]$Payload) {
    New-Item -ItemType Directory -Path (Join-Path $Tree 'nested') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $Tree 'nested\payload.txt') -Value $Payload -Encoding UTF8
}

$committedFormal = Join-Path $root 'committed-formal'
New-TreeAt $committedFormal 'committed-dist'
$stagePrefix = '.release-stage-v0316-'
$stageRoot = Join-Path $root ($stagePrefix + 'cleanup-test')
New-TreeAt $stageRoot 'staged-scratch'
$manifestPath = Join-Path $root 'promotion.json'
$promotionRecord = [ordered]@{
    version = '0.3.16'
    cleanupPending = [object[]]@($stageRoot)
}
$promotionRecord | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$script:removeAttempts = 0
$lockedRemover = {
    param($ScratchPath)
    $script:removeAttempts++
    throw [System.IO.IOException]::new('scanner still owns cleanup handle')
}
$cleanupResult = Complete-ReleaseScratchCleanup `
    -ScratchItems @([pscustomobject]@{
        path = $stageRoot
        parent = $root
        prefix = $stagePrefix
    }) `
    -PromotionCommitted $true `
    -PromotionRecord $promotionRecord `
    -PromotionManifestPath $manifestPath `
    -MaxAttempts 5 `
    -InitialDelayMilliseconds 0 `
    -RemoveOperation $lockedRemover
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

$formalRoot = Join-Path $root 'rollback-formal-root'
$backupRoot = Join-Path $formalRoot 'release-backups\pre-v0316-test'
$formalDist = Join-Path $formalRoot 'dist\LiliesInTheBox'
$backupDist = Join-Path $backupRoot 'dist\LiliesInTheBox'
$restoreIncoming = Join-Path (Split-Path -Parent $formalDist) '.release-restore-v0316-locked'
New-TreeAt $formalDist 'partial-new-dist'
New-TreeAt $backupDist 'old-good-dist'

$validRelative = 'scripts\valid.ps1'
$missingRelative = 'scripts\missing.ps1'
$validTarget = Join-Path $formalRoot $validRelative
$validBackup = Join-Path $backupRoot $validRelative
$missingTarget = Join-Path $formalRoot $missingRelative
New-Item -ItemType Directory -Path (Split-Path -Parent $validTarget) -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $validBackup) -Force | Out-Null
Set-Content -LiteralPath $validTarget -Value 'new-source' -Encoding UTF8
Set-Content -LiteralPath $validBackup -Value 'old-source' -Encoding UTF8
Set-Content -LiteralPath $missingTarget -Value 'changed-without-backup' -Encoding UTF8
$records = @(
    [pscustomobject]@{ path = $missingRelative; existed = $true },
    [pscustomobject]@{ path = $validRelative; existed = $true }
)
$script:quarantineAttempts = 0
$quarantineLocked = {
    param($Source, $Destination)
    $script:quarantineAttempts++
    throw [System.UnauthorizedAccessException]::new('partial formal dist is locked')
}
$rollbackFailures = @(Invoke-ReleaseRollback `
    -NewDistInstalled $true `
    -DistBackedUp $true `
    -FormalDist $formalDist `
    -BackupDist $backupDist `
    -RestoreIncoming $restoreIncoming `
    -BackupRoot $backupRoot `
    -FormalRoot $formalRoot `
    -ReleaseTag 'v0316' `
    -Records $records `
    -MoveMaxAttempts 5 `
    -InitialDelayMilliseconds 0 `
    -QuarantineMoveOperation $quarantineLocked)

$successFormalRoot = Join-Path $root 'rollback-success-root'
$successBackupRoot = Join-Path $successFormalRoot 'release-backups\pre-v0316-test'
$successFormalDist = Join-Path $successFormalRoot 'dist\LiliesInTheBox'
$successBackupDist = Join-Path $successBackupRoot 'dist\LiliesInTheBox'
$successRestoreIncoming = Join-Path (Split-Path -Parent $successFormalDist) '.release-restore-v0316-success'
New-TreeAt $successFormalDist 'discarded-new-dist'
New-TreeAt $successBackupDist 'preserved-old-dist'
$successFailures = @(Invoke-ReleaseRollback `
    -NewDistInstalled $true `
    -DistBackedUp $true `
    -FormalDist $successFormalDist `
    -BackupDist $successBackupDist `
    -RestoreIncoming $successRestoreIncoming `
    -BackupRoot $successBackupRoot `
    -FormalRoot $successFormalRoot `
    -ReleaseTag 'v0316' `
    -Records @() `
    -InitialDelayMilliseconds 0)
$successSnapshot = @(Get-ReleaseTreeSnapshot $successBackupDist)
Assert-MatchingTreeSnapshot $successSnapshot $successFormalDist
Assert-MatchingTreeSnapshot $successSnapshot $successBackupDist

$intentFormalRoot = Join-Path $root 'backup-intent-root'
$intentBackupRoot = Join-Path $intentFormalRoot 'release-backups\pre-v0316-test'
$intentFormalDist = Join-Path $intentFormalRoot 'dist\LiliesInTheBox'
$intentBackupDist = Join-Path $intentBackupRoot 'dist\LiliesInTheBox'
$intentRestoreIncoming = Join-Path (Split-Path -Parent $intentFormalDist) '.release-restore-v0316-intent'
New-TreeAt $intentFormalDist 'old-dist-before-intent-failure'
New-Item -ItemType Directory -Path (Split-Path -Parent $intentBackupDist) -Force | Out-Null
$intentSnapshot = @(Get-ReleaseTreeSnapshot $intentFormalDist)
$intentFailureCaught = $false
$moveThenReportFailure = {
    param($Source, $Destination)
    Move-Item -LiteralPath $Source -Destination $Destination -ErrorAction Stop
    throw [InvalidOperationException]::new('post-move verification provider failed')
}
try {
    Move-ReleaseTreeWithRetry `
        -Source $intentFormalDist `
        -Destination $intentBackupDist `
        -ExpectedSnapshot $intentSnapshot `
        -InitialDelayMilliseconds 0 `
        -MoveOperation $moveThenReportFailure
} catch [InvalidOperationException] {
    $intentFailureCaught = $true
}
$intentRollbackFailures = @(Invoke-ReleaseRollback `
    -NewDistInstalled $false `
    -DistBackedUp $true `
    -FormalDist $intentFormalDist `
    -BackupDist $intentBackupDist `
    -RestoreIncoming $intentRestoreIncoming `
    -BackupRoot $intentBackupRoot `
    -FormalRoot $intentFormalRoot `
    -ReleaseTag 'v0316' `
    -Records @() `
    -InitialDelayMilliseconds 0)
Assert-MatchingTreeSnapshot $intentSnapshot $intentFormalDist
Assert-MatchingTreeSnapshot $intentSnapshot $intentBackupDist

$aggregateInnerCount = 0
try {
    $allFailures = [System.Collections.Generic.List[System.Exception]]::new()
    $allFailures.Add([InvalidOperationException]::new('original promotion failure'))
    foreach ($rollbackFailure in $rollbackFailures) {
        $allFailures.Add($rollbackFailure)
    }
    throw [AggregateException]::new(
        'promotion and rollback failures',
        $allFailures.ToArray()
    )
} catch [AggregateException] {
    $aggregateInnerCount = $_.Exception.InnerExceptions.Count
}

[ordered]@{
    cleanupAttempts = $script:removeAttempts
    cleanupDidNotFailCommit = (Test-Path -LiteralPath $committedFormal -PathType Container) -and
        (Test-Path -LiteralPath $manifestPath -PathType Leaf)
    cleanupPendingCount = @($cleanupResult.cleanupPending).Count
    cleanupPendingRecorded = @($manifest.cleanupPending).Count -eq 1 -and
        [string]$manifest.cleanupPending[0] -eq $stageRoot
    cleanupManifestUpdated = [bool]$cleanupResult.manifestUpdated
    rollbackFailureCount = $rollbackFailures.Count
    rollbackMessages = [object[]]@($rollbackFailures | ForEach-Object { $_.Message })
    quarantineAttempts = $script:quarantineAttempts
    partialFormalPreserved = Test-Path -LiteralPath $formalDist -PathType Container
    backupDistPreserved = Test-Path -LiteralPath $backupDist -PathType Container
    validSourceRestored = (Get-Content -LiteralPath $validTarget -Raw).Trim() -eq 'old-source'
    validSourceBackupPreserved = Test-Path -LiteralPath $validBackup -PathType Leaf
    missingSourceStillPresent = Test-Path -LiteralPath $missingTarget -PathType Leaf
    successfulRollbackFailures = $successFailures.Count
    successfulRollbackPreservedBackup = (Test-Path -LiteralPath $successFormalDist -PathType Container) -and
        (Test-Path -LiteralPath $successBackupDist -PathType Container) -and
        (-not (Test-Path -LiteralPath $successRestoreIncoming))
    backupIntentRecoverySucceeded = $intentFailureCaught -and
        $intentRollbackFailures.Count -eq 0 -and
        (Test-Path -LiteralPath $intentFormalDist -PathType Container) -and
        (Test-Path -LiteralPath $intentBackupDist -PathType Container)
    aggregateInnerCount = $aggregateInnerCount
} | ConvertTo-Json -Compress -Depth 4
''',
    )
    assert report["cleanupAttempts"] == 5
    assert report["cleanupDidNotFailCommit"] is True
    assert report["cleanupPendingCount"] == 1
    assert report["cleanupPendingRecorded"] is True
    assert report["cleanupManifestUpdated"] is True
    assert report["rollbackFailureCount"] == 3
    assert report["rollbackMessages"] == [
        "Rollback could not quarantine the partial formal dist.",
        "Rollback could not restore the backed-up formal dist.",
        "Rollback could not restore source file: scripts\\missing.ps1",
    ]
    assert report["quarantineAttempts"] == 5
    assert report["partialFormalPreserved"] is True
    assert report["backupDistPreserved"] is True
    assert report["validSourceRestored"] is True
    assert report["validSourceBackupPreserved"] is True
    assert report["missingSourceStillPresent"] is True
    assert report["successfulRollbackFailures"] == 0
    assert report["successfulRollbackPreservedBackup"] is True
    assert report["backupIntentRecoverySucceeded"] is True
    assert report["aggregateInnerCount"] == 4
