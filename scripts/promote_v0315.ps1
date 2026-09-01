param(
    [string]$FormalRoot = 'F:\code\Lilies in the box'
)

$ErrorActionPreference = 'Stop'
$Engine = Join-Path $PSScriptRoot 'promote_v0313.ps1'
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
    throw "Promotion engine is missing: $Engine"
}

$AdditionalFiles = @(
    '.gitignore',
    'qml\CompanionBubble.qml',
    'qml\V03WorkPanel.qml',
    'scripts\verify_focus_timer_aura.py',
    'tests\test_focus_timer_aura_qml.py',
    'src\lilies\core\companion.py',
    'src\lilies\companion_controller.py',
    'src\lilies\core\themes.py',
    'scripts\verify_companion_observer.py',
    'scripts\verify_companion_flow_ui.py',
    'scripts\verify_box_world_presentation.py',
    'tests\test_companion.py',
    'tests\test_companion_controller.py',
    'tests\test_companion_flow_ui_offscreen.py',
    'tests\test_box_world_presentation_offscreen.py',
    'tests\test_compact_ui_offscreen.py',
    'tests\test_compact_hit_test.py',
    'tests\test_theme_socket.py',
    'themes\first-encounter\assets\lilith-pose-expansion-sheet-v1.png',
    'artifacts\packaged-compact-resource-v0315.json'
)

& $Engine `
    -FormalRoot $FormalRoot `
    -ReleaseVersion '0.3.15' `
    -PromotionScript 'scripts\promote_v0315.ps1' `
    -PackagedReport 'artifacts\packaged-self-test-v0315.json' `
    -AdditionalFiles $AdditionalFiles
