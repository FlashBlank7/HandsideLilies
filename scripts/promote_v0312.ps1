param(
    [string]$FormalRoot = 'F:\code\Lilies in the box'
)

$ErrorActionPreference = 'Stop'
$CandidateRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ExpectedCandidate = [IO.Path]::GetFullPath('F:\CodeField\lilies-v03-work')
$FormalRoot = [IO.Path]::GetFullPath($FormalRoot)
$ExpectedFormal = [IO.Path]::GetFullPath('F:\code\Lilies in the box')

if ($CandidateRoot -cne $ExpectedCandidate) {
    throw "Refusing unexpected candidate root: $CandidateRoot"
}
if ($FormalRoot -cne $ExpectedFormal) {
    throw "Refusing unexpected formal root: $FormalRoot"
}
if (-not (Test-Path -LiteralPath $FormalRoot -PathType Container)) {
    throw "Formal project is missing: $FormalRoot"
}

$RelativeFiles = @(
    'pyproject.toml',
    'uv.lock',
    'qml\Main.qml',
    'qml\V03FocusTimerAura.qml',
    'qml\V03PetBody.qml',
    'src\lilies\__init__.py',
    'src\lilies\app.py',
    'src\lilies\core\codex_subscription.py',
    'src\lilies\core\pet_habitat.py',
    'src\lilies_in_the_box.egg-info\PKG-INFO',
    'themes\first-encounter\theme.json',
    'scripts\install_windows.ps1',
    'scripts\verify_compact_ui.py',
    'scripts\verify_focus_timer_aura.py',
    'scripts\verify_habitat_ui.py',
    'scripts\promote_v0312.ps1',
    'tests\test_focus_timer_aura_qml.py',
    'tests\test_habitat_ui_offscreen.py',
    'tests\test_pet_habitat_v03.py',
    'artifacts\companion-awareness-menu.png',
    'artifacts\desktop-discovery-settings.png',
    'artifacts\focus-timer-aura-audit.json',
    'artifacts\focus-timer-aura-running.png',
    'artifacts\habitat-pose-coverage.json',
    'artifacts\habitat-pose-runtime-v0312.md',
    'artifacts\packaged-self-test-v0312.json'
)

foreach ($RelativePath in $RelativeFiles) {
    $Source = [IO.Path]::GetFullPath((Join-Path $CandidateRoot $RelativePath))
    $Target = [IO.Path]::GetFullPath((Join-Path $FormalRoot $RelativePath))
    if (-not $Source.StartsWith($CandidateRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing source outside candidate root: $Source"
    }
    if (-not $Target.StartsWith($FormalRoot + [IO.Path]::DirectorySeparatorChar,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing target outside formal root: $Target"
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Candidate file is missing: $Source"
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    if ((Get-FileHash -LiteralPath $Source).Hash -ne (Get-FileHash -LiteralPath $Target).Hash) {
        throw "Promoted file hash mismatch: $RelativePath"
    }
}

$CandidateDist = [IO.Path]::GetFullPath((Join-Path $CandidateRoot 'dist\LiliesInTheBox'))
$FormalDist = [IO.Path]::GetFullPath((Join-Path $FormalRoot 'dist\LiliesInTheBox'))
$CandidateExe = Join-Path $CandidateDist 'LiliesInTheBox.exe'
if (-not (Test-Path -LiteralPath $CandidateExe -PathType Leaf)) {
    throw "Packaged candidate is missing: $CandidateExe"
}
New-Item -ItemType Directory -Path $FormalDist -Force | Out-Null
Copy-Item -Path (Join-Path $CandidateDist '*') -Destination $FormalDist -Recurse -Force

$DistMismatches = @(
    Get-ChildItem -LiteralPath $CandidateDist -Recurse -File | ForEach-Object {
        $Relative = $_.FullName.Substring($CandidateDist.Length + 1)
        $Target = Join-Path $FormalDist $Relative
        if (-not (Test-Path -LiteralPath $Target -PathType Leaf) -or
            (Get-FileHash -LiteralPath $_.FullName).Hash -ne (Get-FileHash -LiteralPath $Target).Hash) {
            $Relative
        }
    }
)
if ($DistMismatches.Count -ne 0) {
    throw "Formal dist hash mismatch: $($DistMismatches -join ', ')"
}

$ExeHash = (Get-FileHash -LiteralPath $CandidateExe -Algorithm SHA256).Hash
Write-Output "Promoted Lilies in the box v0.3.12"
Write-Output "Files: $($RelativeFiles.Count)"
Write-Output "Dist mismatches: $($DistMismatches.Count)"
Write-Output "EXE SHA256: $ExeHash"
