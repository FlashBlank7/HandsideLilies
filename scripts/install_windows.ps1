param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Lilies in the box'),
    [switch]$StartAtLogin,
    [switch]$NoShortcuts,
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $ProjectRoot 'dist\LiliesInTheBox'
$SourceExe = Join-Path $SourceRoot 'LiliesInTheBox.exe'
$ExpectedFileVersion = '0.3.50.0'
$ExpectedProductVersion = '0.3.50'
$AllowedBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$Target = [IO.Path]::GetFullPath($InstallRoot)

function Assert-SafeChildPath([string]$Candidate, [string]$Base) {
    $prefix = $Base.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if ($Candidate -eq $Base -or -not $Candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing unsafe install target: $Candidate"
    }
}

function Assert-ReleaseExecutable([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Build not found: $Path"
    }
    $versionInfo = (Get-Item -LiteralPath $Path).VersionInfo
    if ([string]$versionInfo.FileVersion -cne $ExpectedFileVersion) {
        throw "Refusing install: EXE FileVersion must be $ExpectedFileVersion, got '$($versionInfo.FileVersion)'."
    }
    if ([string]$versionInfo.ProductVersion -cne $ExpectedProductVersion) {
        throw "Refusing install: EXE ProductVersion must be $ExpectedProductVersion, got '$($versionInfo.ProductVersion)'."
    }
}

Assert-SafeChildPath $Target $AllowedBase
# This preflight is deliberately before process shutdown, deletion, copying,
# shortcuts and manifest writes.  An old or unversioned EXE must never be
# relabelled as v0.3.50 by a successful-looking install manifest.
Assert-ReleaseExecutable $SourceExe

$TargetExe = Join-Path $Target 'LiliesInTheBox.exe'
if (Test-Path -LiteralPath $TargetExe -PathType Leaf) {
    # CIM process enumeration is commonly denied to a normal user on managed
    # Windows installations.  The personal build has a fixed executable name,
    # so use the non-elevated process API and compare resolved paths instead.
    $runningBeforeUpdate = @(Get-Process -Name 'LiliesInTheBox' -ErrorAction SilentlyContinue |
        Where-Object {
            try { [IO.Path]::GetFullPath($_.Path) -eq [IO.Path]::GetFullPath($TargetExe) }
            catch { $false }
        })
    if ($NoLaunch -and $runningBeforeUpdate.Count -gt 0) {
        throw 'Lilies is currently running. No-launch update was cancelled so the desktop companion and WPS selection monitor stay available.'
    }
    # A windowed executable invoked with `&` may return control before its
    # process has initialized.  That allowed the old restore process to appear
    # after enumeration and keep DLLs locked while the target directory was
    # being replaced.  Run the already-version-checked source copy, wait for
    # its exact PID, and only then terminate processes from the installed path.
    $restoreProcess = Start-Process -FilePath $SourceExe `
        -ArgumentList '--restore' -WindowStyle Hidden -PassThru
    if (-not $restoreProcess.WaitForExit(20000)) {
        Stop-Process -Id $restoreProcess.Id -Force -ErrorAction SilentlyContinue
        throw 'Lilies shell restore timed out before installation.'
    }
    $restoreProcess.Refresh()
    if ($restoreProcess.ExitCode -ne 0) {
        throw "Lilies shell restore failed with exit code $($restoreProcess.ExitCode)."
    }
    $installedProcesses = @(Get-Process -Name 'LiliesInTheBox' -ErrorAction SilentlyContinue |
        Where-Object {
            try { [IO.Path]::GetFullPath($_.Path) -eq [IO.Path]::GetFullPath($TargetExe) }
            catch { $false }
        } |
        ForEach-Object {
            # The restore-only process can finish between enumeration and
            # termination.  Treat that normal race as already stopped.
            Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
            $_
        })
    foreach ($installedProcess in $installedProcesses) {
        Wait-Process -Id $installedProcess.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
}
if (Test-Path -LiteralPath $Target) {
    Assert-SafeChildPath ([IO.Path]::GetFullPath($Target)) $AllowedBase
    $removed = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $Target -Recurse -Force
            $removed = $true
            break
        } catch {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $removed) { throw "Could not replace install target: $Target" }
}
New-Item -ItemType Directory -Path $Target -Force | Out-Null
Copy-Item -Path (Join-Path $SourceRoot '*') -Destination $Target -Recurse -Force

$manifest = [ordered]@{
    id = 'lilies-in-the-box'
    version = '0.3.50'
    installedAt = [DateTimeOffset]::UtcNow.ToString('o')
    source = $SourceRoot
    executable = $TargetExe
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Target 'install-manifest.json') -Encoding UTF8

if (-not $NoShortcuts) {
    $shell = New-Object -ComObject WScript.Shell
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    $shortcutPath = Join-Path $startMenu 'Lilies in the box.lnk'
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $TargetExe
    # The primary entry respects the shell mode already selected by the user.
    # Assign an empty value explicitly so upgrading an older shortcut clears
    # the legacy forced-compact argument.
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = $Target
    $shortcut.Description = 'Lilies in the box'
    $shortcut.Save()

    $desktopShortcutPath = Join-Path $startMenu 'Lilies in the box - Desktop.lnk'
    $desktopShortcut = $shell.CreateShortcut($desktopShortcutPath)
    $desktopShortcut.TargetPath = $TargetExe
    $desktopShortcut.Arguments = '--visual'
    $desktopShortcut.WorkingDirectory = $Target
    $desktopShortcut.Description = 'Open the full Lilies desktop explicitly'
    $desktopShortcut.Save()

    $petShortcutPath = Join-Path $startMenu 'Lilies in the box - Pet only.lnk'
    $petShortcut = $shell.CreateShortcut($petShortcutPath)
    $petShortcut.TargetPath = $TargetExe
    $petShortcut.Arguments = '--compact'
    $petShortcut.WorkingDirectory = $Target
    $petShortcut.Description = '仅启动 Lilies 桌宠，不展开完整桌面'
    $petShortcut.Save()
    # Startup follows the user's persisted mode; it must not force the
    # optional compact-only entry.  Existing v0.3.3 startup links are upgraded
    # even when this install was not passed -StartAtLogin, otherwise their
    # legacy --compact argument would return on the next sign-in.
    $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\Lilies in the box.lnk'
    if ($StartAtLogin -or (Test-Path -LiteralPath $startup -PathType Leaf)) {
        Copy-Item -LiteralPath $shortcutPath -Destination $startup -Force
    }
}

if (-not $NoLaunch) {
    Start-Process -FilePath $TargetExe
}
Write-Output "Installed Lilies in the box to $Target"
