param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Lilies in the box'),
    [switch]$StartAtLogin,
    [switch]$NoShortcuts,
    [switch]$NoLaunch,
    [string]$BuildId,
    [string]$RecoverTransactionId,
    [switch]$RollbackCommitted
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ExpectedFileVersion = '0.3.57.0'
$ExpectedProductVersion = '0.3.57'
$AllowedBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$Target = [IO.Path]::GetFullPath($InstallRoot)
. (Join-Path $PSScriptRoot 'install_transaction.ps1')
. (Join-Path $PSScriptRoot 'install_startup.ps1')
$JournalRoot = 'F:\code\Lilies in the box\private-data\install-transactions'
$TransactionParameters = @{ Target = $Target; AllowedBase = $AllowedBase; JournalRoot = $JournalRoot }

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
# Recovery never needs a new build and never starts or terminates a process.
# In particular, do not force-stop a user's app merely to roll files back.
if ($RecoverTransactionId) {
    $recoveryPaths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $RecoverTransactionId
    $recoveryExecutables = @($recoveryPaths.target, $recoveryPaths.stage, $recoveryPaths.previous, $recoveryPaths.failed) |
        ForEach-Object { Join-Path $_ 'LiliesInTheBox.exe' }
    Assert-LiliesImagesQuiescent $recoveryExecutables
    Restore-LiliesInstallTransaction @TransactionParameters -TransactionId $RecoverTransactionId -RollbackCommitted:$RollbackCommitted `
        -BeforeRestore { Assert-LiliesImagesQuiescent $recoveryExecutables }
    return
}
if ($RollbackCommitted) { throw 'RollbackCommitted requires an exact RecoverTransactionId.' }
if ($BuildId -cnotmatch '^v0357-[A-Za-z0-9-]{1,80}$') {
    throw 'An exact side-by-side v0.3.57 BuildId is required; legacy dist directories are not install candidates.'
}
$SourceRoot = Get-LiliesPhysicalPath (Join-Path $ProjectRoot ('dist\' + $BuildId + '\LiliesInTheBox'))
$SourceExe = Join-Path $SourceRoot 'LiliesInTheBox.exe'
$VerifierPython = Get-LiliesPhysicalPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe')
if (-not (Test-Path -LiteralPath $VerifierPython -PathType Leaf)) {
    throw 'The candidate verification environment is missing; no installation was attempted.'
}
# Read-only: compare the fresh source, compiled modules, resources and entire
# package against this exact build's original receipt. No app import/start,
# user database, generated report or Python bytecode cache is needed.
$identityJson = & $VerifierPython -B (Join-Path $PSScriptRoot 'verify_candidate_bundle.py') `
    check-candidate --root $ProjectRoot --build-id $BuildId
if ($LASTEXITCODE -ne 0) { throw 'Candidate identity verification failed; existing installations are unchanged.' }
$candidateIdentity = ($identityJson -join "`n") | ConvertFrom-Json
if ($candidateIdentity.passed -ne $true -or $candidateIdentity.buildId -cne $BuildId -or
    $candidateIdentity.package -ine $SourceRoot) { throw 'Candidate verifier returned mismatched identity.' }
# This preflight is deliberately before process shutdown, staging, copying,
# shortcuts and manifest writes.  An old or unversioned EXE must never be
# relabelled as v0.3.57 by a successful-looking install manifest.
Assert-ReleaseExecutable $SourceExe

# Exact package identity alone is not native or installer acceptance. The
# remaining release gate must not be bypassed by a receipt's releaseReady
# boolean or by a Force switch. Native startup/health acceptance is pending.
throw 'v0.3.57 installation is not released: exact-build promotion and native acceptance are still required. Existing installations are unchanged. Explicit transaction recovery remains available.'

$TargetExe = Join-Path $Target 'LiliesInTheBox.exe'
# This check is read-only and fails closed on inaccessible process metadata.
# Repeat it under the transaction lock before any shutdown or file activation.
if ($NoLaunch) { Assert-LiliesImagesQuiescent @($TargetExe) }
$shortcutPlan = $null
if (-not $NoShortcuts) {
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    # Generate link bytes only in F-drive staging. Original Start Menu and
    # Startup links are snapshotted before any program is stopped or swapped.
    # An existing startup preference is preserved, never silently enabled.
    $shortcutStaging = Join-Path $JournalRoot ('shortcut-payloads\' + [guid]::NewGuid().ToString('N'))
    $shortcutPlan = New-LiliesManagedShortcutPlan $Target $startMenu $shortcutStaging ([bool]$StartAtLogin)
}
$manifest = [ordered]@{
    id = 'lilies-in-the-box'
    version = '0.3.57'
    installedAt = [DateTimeOffset]::UtcNow.ToString('o')
    source = $SourceRoot
    executable = $TargetExe
    buildId = $BuildId
    sourceDigest = $candidateIdentity.sourceDigest
    executableSha256 = $candidateIdentity.executableSha256
    launchRequested = [bool](-not $NoLaunch)
}
# All copying and full-tree checks finish while the previous program still
# exists. Metadata is included in the verified payload, not written afterward.
$transaction = New-LiliesInstallTransaction @TransactionParameters -SourceRoot $SourceRoot `
    -InstallManifestJson ($manifest | ConvertTo-Json -Compress) `
    -ShortcutPlan $shortcutPlan `
    -ValidatePayload { param($directory); Assert-ReleaseExecutable (Join-Path $directory 'LiliesInTheBox.exe') }
Write-Output "Prepared transaction $($transaction.transactionId). Recovery journal: $($transaction.journal)"
$RestoreExe = Join-Path $transaction.stage 'LiliesInTheBox.exe'

# One transaction covers the program and optional four fixed shortcuts.
# On failure it restores their prior bytes together, preserving conflicts and
# incomplete new files for inspection. No independent post-commit link writes.
$launch = New-LiliesLaunchState $TargetExe
try {
    $installed = Complete-LiliesInstallTransaction @TransactionParameters -TransactionId $transaction.transactionId `
        -BeforeActivate {
            param($paths)
            Stop-LiliesForUpgrade -TargetExecutable $TargetExe -RecoveryExecutable $RestoreExe -NoLaunch:$NoLaunch `
                -ValidateRecovery {
                    $preparedManifest = Read-LiliesInstallManifest $paths
                    Assert-LiliesTreeMatches $paths.stage $preparedManifest.newSnapshot
                }
        } `
        -AfterActivate {
            if (-not $NoLaunch) {
                Start-LiliesOwnedChild $launch
                Assert-LiliesStartupHealth $launch $VerifierPython (Join-Path $PSScriptRoot 'verify_install_health.py')
                Write-LiliesExclusiveJson (Join-Path $transaction.journal 'startup-health.json') $launch.health
            }
        } -BeforeRollback {
            Restore-LiliesAfterStartupFailure $launch
            $paths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $transaction.transactionId
            $executables = @($paths.target, $paths.stage, $paths.previous, $paths.failed) |
                ForEach-Object { Join-Path $_ 'LiliesInTheBox.exe' }
            Assert-LiliesImagesQuiescent $executables
        }
} finally {
    Close-LiliesLaunchHandle $launch
}
Write-Output "Installed Lilies in the box to $Target. Previous program files retained at $($installed.previous)."
