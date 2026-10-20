param(
    [switch]$OffscreenOnly,
    [string]$BuildId = ('v0357-' + [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if ($BuildId -cnotmatch '^v0357-[A-Za-z0-9-]{1,80}$') { throw 'Invalid candidate build identity' }
if ([IO.Path]::GetPathRoot($ProjectRoot) -ine 'F:\') { throw 'Candidate builds must stay on F drive' }
$BuildRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ('build\' + $BuildId)))
$DistParent = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ('dist\' + $BuildId)))
$DistRoot = Join-Path $DistParent 'LiliesInTheBox'
$CandidateExe = Join-Path $DistRoot 'LiliesInTheBox.exe'
$ArtifactRoot = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ('artifacts\' + $BuildId)))
$SourceManifest = Join-Path $ArtifactRoot 'source-manifest.json'
$BundleReport = Join-Path $ArtifactRoot 'bundle-integrity.json'
$ReceiptPath = Join-Path $ArtifactRoot 'candidate-build.json'
$PackagedSelfTest = Join-Path $ArtifactRoot 'packaged-self-test-v0357.json'
$PackagedCompactResource = Join-Path $ArtifactRoot 'packaged-compact-resource-v0357.json'
$PackagedWindowsStartup = Join-Path $ArtifactRoot 'packaged-windows-startup-v0357.json'
$PoseClickMask = Join-Path $ArtifactRoot 'pose-click-mask-v0357.json'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Build environment is missing; prepare it explicitly before building'
}
foreach ($target in @($BuildRoot, $DistParent, $ArtifactRoot)) {
    if (Test-Path -LiteralPath $target) { throw "Build identity already exists; previous evidence is preserved: $target" }
    # Reject linked ancestors before creating any directory or starting a build.
    $parent = Split-Path -Parent $target
    while ($parent) {
        if ((Test-Path -LiteralPath $parent) -and ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing linked build parent: $parent"
        }
        $parent = Split-Path -Parent $parent
    }
}
New-Item -ItemType Directory -Path $ArtifactRoot -ErrorAction Stop | Out-Null
$BuildTemp = Join-Path $BuildRoot 'temp'
New-Item -ItemType Directory -Path $BuildTemp -ErrorAction Stop | Out-Null
$env:TEMP = $BuildTemp
$env:TMP = $BuildTemp
$env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot 'pyinstaller-cache'
$env:LILIES_DATA_DIR = Join-Path $BuildTemp 'self-test-data'
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QSG_RHI_BACKEND = 'software'
$env:QT_QUICK_BACKEND = 'software'
$env:QML_DISABLE_DISK_CACHE = '1'
$env:LILIES_BUILD_MANIFEST = $SourceManifest
$ShibokenDll = Join-Path $ProjectRoot '.venv\Lib\site-packages\shiboken6\shiboken6.abi3.dll'
if (-not (Test-Path -LiteralPath $ShibokenDll -PathType Leaf)) {
    throw "Required PySide6 runtime was not found: $ShibokenDll"
}
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'

function Remove-StaleReleaseArtifact([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    $prefix = $ArtifactRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a release artifact outside artifacts: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
            throw "Refusing to replace a non-file release artifact: $resolved"
        }
        [IO.File]::Delete($resolved)
    }
}

Push-Location $ProjectRoot
try {
    & $Python (Join-Path $PSScriptRoot 'verify_candidate_bundle.py') snapshot --root $ProjectRoot --build-id $BuildId --output $SourceManifest
    if ($LASTEXITCODE -ne 0) { throw 'Could not snapshot candidate inputs' }
    # The spec pins Python's matching OpenSSL pair ahead of ambient PATH
    # copies (for example MySQL's libssl), and collects tzdata for Calendar.
    & $Python -m PyInstaller --noconfirm --clean --workpath (Join-Path $BuildRoot 'pyinstaller') --distpath $DistParent 'LiliesInTheBox.spec'
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }

    # PyInstaller resolves transitive DLLs through PATH. On this machine that
    # can pick up the old Universal CRT and ICU 58 shims from Anaconda. They
    # shadow the Windows 11 API-set/UCRT/ICU forwarders and make Qt6Core fail
    # with ERROR_PROC_NOT_FOUND (127). Limit cleanup to known files directly in
    # this build's exact _internal directory; never recurse or touch an install.
    $InternalRoot = [IO.Path]::GetFullPath((Join-Path $DistRoot '_internal'))
    if ((Split-Path -Parent $InternalRoot) -cne $DistRoot -or
        (Split-Path -Leaf $InternalRoot) -cne '_internal' -or
        -not (Test-Path -LiteralPath $InternalRoot -PathType Container)) {
        throw "Refusing runtime cleanup outside the expected build directory: $InternalRoot"
    }

    $ExactStaleRuntimeNames = @('ucrtbase.dll', 'icuuc.dll', 'icudt58.dll')
    $StaleRuntimeFiles = @(
        Get-ChildItem -LiteralPath $InternalRoot -File |
            Where-Object {
                $_.Name -in $ExactStaleRuntimeNames -or
                $_.Name -like 'api-ms-win-*.dll'
            }
    )
    foreach ($RuntimeFile in $StaleRuntimeFiles) {
        if ([IO.Path]::GetFullPath($RuntimeFile.DirectoryName) -cne $InternalRoot) {
            throw "Refusing to remove a runtime outside the exact _internal root: $($RuntimeFile.FullName)"
        }
        Remove-Item -LiteralPath $RuntimeFile.FullName -Force
    }

    $RemainingStaleRuntime = @(
        Get-ChildItem -LiteralPath $InternalRoot -File |
            Where-Object {
                $_.Name -in $ExactStaleRuntimeNames -or
                $_.Name -like 'api-ms-win-*.dll'
            }
    )
    if ($RemainingStaleRuntime.Count -ne 0) {
        throw "Stale root runtimes remain after packaging cleanup: $($RemainingStaleRuntime.Name -join ', ')"
    }

    & $Python (Join-Path $PSScriptRoot 'verify_candidate_bundle.py') verify --root $ProjectRoot --manifest $SourceManifest --package $DistRoot --output $BundleReport
    if ($LASTEXITCODE -ne 0) { throw 'Packaged modules or resources do not match the candidate inputs' }
} finally {
    Pop-Location
}

# Generate the complete v0.3.57 evidence set in dependency order.  Removing
# each fixed artifact first prevents a failed probe from leaving an older
# successful report that a later validation could mistake for this build.
foreach ($artifact in @(
    $PackagedSelfTest,
    $PackagedCompactResource,
    $PackagedWindowsStartup,
    $PoseClickMask
)) {
    Remove-StaleReleaseArtifact $artifact
}

if (-not (Test-Path -LiteralPath $CandidateExe -PathType Leaf)) {
    throw "Packaged executable is missing after build: $CandidateExe"
}

$quotedSelfTestReport = '"' + $PackagedSelfTest + '"'
$selfTestProcess = Start-Process -FilePath $CandidateExe `
    -ArgumentList @('--self-test', $quotedSelfTestReport) `
    -WindowStyle Hidden -PassThru
if (-not $selfTestProcess.WaitForExit(60000)) {
    Stop-Process -Id $selfTestProcess.Id -Force
    throw 'Packaged self-test timed out after 60 seconds.'
}
$selfTestProcess.Refresh()
if ($selfTestProcess.ExitCode -ne 0) {
    throw "Packaged self-test failed with exit code $($selfTestProcess.ExitCode)"
}
if (-not (Test-Path -LiteralPath $PackagedSelfTest -PathType Leaf)) {
    throw "Packaged self-test report is missing: $PackagedSelfTest"
}

& (Join-Path $PSScriptRoot 'verify_packaged_compact_resources.ps1') `
    -Executable $CandidateExe `
    -SelfTestReport $PackagedSelfTest `
    -ReportPath $PackagedCompactResource
if ($LASTEXITCODE -ne 0) {
    throw "Packaged compact-resource probe failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $PackagedCompactResource -PathType Leaf)) {
    throw "Packaged compact-resource report is missing: $PackagedCompactResource"
}

# Offscreen QML verification cannot exercise qwindows, HWND creation or the
# application-wide native event filter. Run a second, intrinsically hidden
# cold-start probe against the just-built executable before it can be installed.
if (-not $OffscreenOnly) {
    & (Join-Path $PSScriptRoot 'verify_packaged_windows_startup.ps1') `
        -Executable $CandidateExe `
        -ReportPath $PackagedWindowsStartup
    if ($LASTEXITCODE -ne 0) {
        throw "Hidden qwindows startup probe failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-Path -LiteralPath $PackagedWindowsStartup -PathType Leaf)) {
        throw "Packaged Windows startup report is missing: $PackagedWindowsStartup"
    }
}

& $Python (Join-Path $PSScriptRoot 'verify_pose_click_masks.py') `
    --executable $CandidateExe `
    --report-path $PoseClickMask `
    --resource-root $InternalRoot
if ($LASTEXITCODE -ne 0) {
    throw "Pose click-mask probe failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $PoseClickMask -PathType Leaf)) {
    throw "Pose click-mask report is missing: $PoseClickMask"
}

$bundle = Get-Content -LiteralPath $BundleReport -Raw | ConvertFrom-Json
$receipt = [ordered]@{
    schemaVersion = 1
    applicationVersion = '0.3.57'
    buildId = $BuildId
    package = $DistRoot
    executableSha256 = $bundle.executableSha256
    sourceDigest = $bundle.sourceDigest
    offscreenOnly = [bool]$OffscreenOnly
    windowsStartup = $(if ($OffscreenOnly) { 'not-run' } else { 'passed-hidden-probe' })
    packageChecksPassed = $true
    releaseReady = $false
    remaining = @('native drag and long-running responsiveness', 'live model quality', 'migration and rollback acceptance', 'final release acceptance')
}
[IO.File]::WriteAllText($ReceiptPath, ($receipt | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
Write-Output "Built Lilies v0.3.57 candidate $BuildId. Package checks passed; this is not release acceptance. Receipt: $ReceiptPath"
