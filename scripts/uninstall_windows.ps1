param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Lilies in the box'),
    [switch]$RemoveData,
    [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$AllowedBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$Target = [IO.Path]::GetFullPath($InstallRoot)

function Assert-SafeChildPath([string]$Candidate, [string]$Base) {
    $prefix = $Base.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($Candidate -eq $Base -or -not $Candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing unsafe uninstall target: $Candidate"
    }
}

Assert-SafeChildPath $Target $AllowedBase
$TargetExe = Join-Path $Target 'LiliesInTheBox.exe'
if (Test-Path -LiteralPath $TargetExe -PathType Leaf) {
    & $TargetExe --restore
    Get-CimInstance Win32_Process |
        Where-Object { $_.ExecutablePath -eq $TargetExe } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

if (-not $NoShortcuts) {
    $shortcuts = @(
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Lilies in the box.lnk'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Lilies in the box - Desktop.lnk'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Lilies in the box - Pet only.lnk'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Lilies in the box.lnk')
    )
    foreach ($shortcut in $shortcuts) {
        if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
    }
}

if (Test-Path -LiteralPath $Target) {
    Assert-SafeChildPath ([IO.Path]::GetFullPath($Target)) $AllowedBase
    Remove-Item -LiteralPath $Target -Recurse -Force
}

if ($RemoveData) {
    $PrivateDataTarget = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'private-data'))
    $ExpectedPrivateDataTarget = [IO.Path]::GetFullPath('F:\code\Lilies in the box\private-data')
    if (-not $PrivateDataTarget.Equals($ExpectedPrivateDataTarget, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing unexpected Lilies private-data target: $PrivateDataTarget"
    }
    if (Test-Path -LiteralPath $PrivateDataTarget) {
        Remove-Item -LiteralPath $PrivateDataTarget -Recurse -Force
    }

    # A once-started migration may deliberately retain the v0.1 copy until a
    # second healthy restart.  Remove that exact legacy child only when the
    # user explicitly supplied -RemoveData.
    $LegacyBase = [IO.Path]::GetFullPath($env:LOCALAPPDATA)
    $LegacyTarget = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Lilies in the box'))
    Assert-SafeChildPath $LegacyTarget $LegacyBase
    if (Test-Path -LiteralPath $LegacyTarget) {
        Remove-Item -LiteralPath $LegacyTarget -Recurse -Force
    }
}
Write-Output "Uninstalled Lilies in the box from $Target"
