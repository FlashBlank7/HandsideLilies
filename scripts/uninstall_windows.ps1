param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\Lilies in the box'),
    [switch]$RemoveData,
    [switch]$NoShortcuts,
    [string]$BuildId,
    [string]$RecoverTransactionId,
    [switch]$RollbackCommitted
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'install_transaction.ps1')
. (Join-Path $PSScriptRoot 'install_startup.ps1')
. (Join-Path $PSScriptRoot 'uninstall_transaction.ps1')
. (Join-Path $PSScriptRoot 'uninstall_startup.ps1')
$AllowedBase = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$Target = Get-LiliesPhysicalPath $InstallRoot
$JournalRoot = 'F:\code\Lilies in the box\private-data\install-transactions'
$TransactionParameters = @{Target=$Target; AllowedBase=$AllowedBase; JournalRoot=$JournalRoot}
# Validate exact immediate-child boundaries and every existing ancestor even
# on a refused preflight. This creates no directory or journal.
Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot ([guid]::NewGuid().ToString('N')) | Out-Null

if ($RecoverTransactionId) {
    if ($RemoveData -or $NoShortcuts -or $BuildId) { throw 'Recovery uses the recorded file/link plan; do not combine it with uninstall options.' }
    $recoveryPaths = Get-LiliesTransactionPaths $Target $AllowedBase $JournalRoot $RecoverTransactionId
    $recoveryExecutables = @($recoveryPaths.target, $recoveryPaths.stage, $recoveryPaths.previous, $recoveryPaths.failed) |
        ForEach-Object { Join-Path $_ 'LiliesInTheBox.exe' }
    Assert-LiliesImagesQuiescent $recoveryExecutables
    Restore-LiliesUninstallTransaction @TransactionParameters -TransactionId $RecoverTransactionId `
        -RollbackCommitted:$RollbackCommitted -BeforeRestore { Assert-LiliesImagesQuiescent $recoveryExecutables }
    return
}
if ($RollbackCommitted) { throw 'RollbackCommitted requires an exact RecoverTransactionId.' }
# Permanent data erasure remains a separate, unfinished release requirement.
# Refuse explicitly before any shutdown/write; never silently reinterpret the
# old opt-in flag as data preservation, or recursively delete recovery logs.
if ($RemoveData) { throw 'Permanent data cleanup is not released. No uninstall or data removal was attempted.' }
if ($BuildId -cnotmatch '^v0357-[A-Za-z0-9-]{1,80}$') {
    throw 'An exact side-by-side v0.3.57 recovery BuildId is required; the legacy installed restore command is not sufficient.'
}
$SourceRoot = Get-LiliesPhysicalPath (Join-Path $ProjectRoot ('dist\' + $BuildId + '\LiliesInTheBox'))
$VerifierPython = Get-LiliesPhysicalPath (Join-Path $ProjectRoot '.venv\Scripts\python.exe')
if (-not (Test-Path -LiteralPath $VerifierPython -PathType Leaf)) { throw 'Candidate verification environment is missing; nothing was uninstalled.' }

function Assert-LiliesUninstallCandidate([string]$Directory) {
    if ($Directory -ine $SourceRoot) { throw 'Recovery source changed.' }
    $identityJson = & $VerifierPython -B (Join-Path $PSScriptRoot 'verify_candidate_bundle.py') `
        check-candidate --root $ProjectRoot --build-id $BuildId
    if ($LASTEXITCODE -ne 0) { throw 'Recovery package verification failed; program files were not removed.' }
    $identity = ($identityJson -join "`n") | ConvertFrom-Json
    if ($identity.passed -ne $true -or $identity.buildId -cne $BuildId -or $identity.package -ine $SourceRoot) {
        throw 'Recovery verifier returned a mismatched candidate.'
    }
    $executable = Get-LiliesPhysicalPath (Join-Path $SourceRoot 'LiliesInTheBox.exe')
    $version = (Get-Item -LiteralPath $executable).VersionInfo
    if ([string]$version.FileVersion -cne '0.3.57.0' -or [string]$version.ProductVersion -cne '0.3.57' -or
        (Get-LiliesProgramFileHash $executable) -ine $identity.executableSha256) {
        throw 'Recovery executable version or hash does not match the verified package.'
    }
}
Assert-LiliesUninstallCandidate $SourceRoot

# File fixtures and exact package checks do not prove native shell recovery.
# There is no force switch or receipt flag that can bypass this release gate.
throw 'v0.3.57 uninstall is not released: native restoration, uninstall and permanent-cleanup acceptance remain required. Existing installations and data are unchanged. Explicit file recovery remains available.'

$shortcutPlan = $null
if (-not $NoShortcuts) {
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    $ownership = New-LiliesRemovalShortcutPlan $Target $startMenu
    $shortcutPlan = $ownership.plan
    if (@($ownership.skippedRoles).Count) { Write-Output 'Customized or unrelated shortcuts will be preserved.' }
}
$uninstalled = Invoke-LiliesCheckedUninstall @TransactionParameters -RecoveryRoot $SourceRoot `
    -ValidateRecovery { param($directory); Assert-LiliesUninstallCandidate $directory } -ShortcutPlan $shortcutPlan
Write-Output "Removed installed entry at $Target. Recoverable program copy retained at $($uninstalled.retained). Personal data was preserved; this is not permanent cleanup."
