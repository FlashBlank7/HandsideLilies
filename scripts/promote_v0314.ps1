param(
    [string]$FormalRoot = 'F:\code\Lilies in the box'
)

$ErrorActionPreference = 'Stop'
$Engine = Join-Path $PSScriptRoot 'promote_v0313.ps1'
if (-not (Test-Path -LiteralPath $Engine -PathType Leaf)) {
    throw "Promotion engine is missing: $Engine"
}

$AdditionalFiles = @(
    'scripts\promote_v0313.ps1',
    'qml\CinematicDesktopVideo.qml',
    'src\lilies\core\pet_habitat.py',
    'scripts\verify_compact_resources.py',
    'tests\test_compact_resource_lifecycle.py',
    'tests\test_pet_habitat_v03.py',
    'artifacts\compact-resource-lifecycle.json',
    'artifacts\packaged-compact-resource-v0314.json'
)

& $Engine `
    -FormalRoot $FormalRoot `
    -ReleaseVersion '0.3.14' `
    -PromotionScript 'scripts\promote_v0314.ps1' `
    -PackagedReport 'artifacts\packaged-self-test-v0314.json' `
    -AdditionalFiles $AdditionalFiles
