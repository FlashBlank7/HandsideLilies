param(
    [Parameter(Mandatory=$true)][string]$BuildId,
    [string]$RunId = [guid]::NewGuid().ToString('N').Substring(0, 8),
    [switch]$IncludeShortcuts
)

# File-only rehearsal. Never dot-source the installer, launch a Lilies binary,
# touch the installed program/Start Menu, or open the user's database.
# Optional real COM links are generated only inside the fresh F-drive fixture.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'install_transaction.ps1')
if ($BuildId -cnotmatch '^v0357-[A-Za-z0-9-]{1,80}$') { throw 'Invalid build identity.' }
if ($RunId -cnotmatch '^[a-f0-9]{8}$') { throw 'Invalid rehearsal identity.' }
$SourceRoot = Get-LiliesPhysicalPath (Join-Path $ProjectRoot ('dist\' + $BuildId + '\LiliesInTheBox'))
$SandboxRoot = Get-LiliesPhysicalPath ('F:\CodeField\.tmp\ir-' + $RunId)
$ArtifactRoot = Get-LiliesPhysicalPath (Join-Path $ProjectRoot ('artifacts\install-transaction-rehearsal-' + $RunId))
if ([IO.Path]::GetPathRoot($ProjectRoot) -ine 'F:\') { throw 'Rehearsal artifacts must stay on F drive.' }
foreach ($path in @($SandboxRoot, $ArtifactRoot)) {
    if (Test-Path -LiteralPath $path) { throw "Refusing to reuse a previous rehearsal: $path" }
}
function Assert-RehearsalPayload([string]$Directory) {
    $exe = Get-Item -LiteralPath (Join-Path $Directory 'LiliesInTheBox.exe')
    if ($exe.VersionInfo.FileVersion -cne '0.3.57.0' -or $exe.VersionInfo.ProductVersion -cne '0.3.57') {
        throw 'The file-only rehearsal requires the v0.3.57 candidate executable.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Directory '_internal\qml\Main.qml') -PathType Leaf)) {
        throw 'Candidate QML is missing.'
    }
}
Assert-RehearsalPayload $SourceRoot
$sourceSnapshot = Get-LiliesTreeSnapshot $SourceRoot
[IO.Directory]::CreateDirectory($ArtifactRoot) | Out-Null
[IO.Directory]::CreateDirectory($SandboxRoot) | Out-Null
$env:TEMP = $SandboxRoot
$env:TMP = $SandboxRoot
$env:PSModuleAnalysisCachePath = Join-Path $SandboxRoot 'powershell-module-cache'
$base = Join-Path $SandboxRoot 'programs'
$target = Join-Path $base 'Lilies in the box'
$journal = Join-Path $SandboxRoot 'journals'
[IO.Directory]::CreateDirectory((Join-Path $target 'empty-old-directory')) | Out-Null
[IO.File]::WriteAllText((Join-Path $target 'LiliesInTheBox.exe'), 'synthetic old fixture, never executable')
[IO.File]::WriteAllText((Join-Path $target 'keep-old.txt'), 'preserve every old byte')
$oldSnapshot = Get-LiliesTreeSnapshot $target
$common = @{ Target = $target; AllowedBase = $base; JournalRoot = $journal }
$shortcutPlan = $null
$oldLinks = $null
if ($IncludeShortcuts) {
    $linkRoot = Join-Path $SandboxRoot 'fixture-links'
    [IO.Directory]::CreateDirectory((Join-Path $linkRoot 'Startup')) | Out-Null
    $oldPayloads = New-LiliesShortcutPayloads $target (Join-Path $SandboxRoot 'old-links') $true
    # Model an earlier forced-compact primary/startup link using a real .lnk,
    # while leaving the explicit desktop/pet entries absent before upgrade.
    [IO.File]::WriteAllBytes((Get-LiliesShortcutPath $linkRoot 'primary'), $oldPayloads.pet)
    [IO.File]::WriteAllBytes((Get-LiliesShortcutPath $linkRoot 'startup'), $oldPayloads.pet)
    [IO.File]::WriteAllText((Join-Path $linkRoot 'unrelated.txt'), 'untouched')
    $oldLinks = Get-LiliesTreeSnapshot $linkRoot
    $shortcutPlan = New-LiliesManagedShortcutPlan $target $linkRoot (Join-Path $SandboxRoot 'new-links') $false
    if (@($shortcutPlan.entries).Count -ne 4) { throw 'Existing startup preference was not retained.' }
    Assert-LiliesTreeMatches $linkRoot $oldLinks
}
$result = [ordered]@{
    schemaVersion = 1; buildId = $BuildId; runId = $RunId
    source = $SourceRoot; sandbox = $SandboxRoot
    sourceFiles = @($sourceSnapshot.files).Count
    sourceBytes = [long](($sourceSnapshot.files | Measure-Object -Property length -Sum).Sum)
    sourceInventorySha256 = Get-LiliesStringHash ($sourceSnapshot | ConvertTo-Json -Depth 8 -Compress)
    passed = $false; releaseReady = $false
    shortcutsIncluded = [bool]$IncludeShortcuts
    scope = 'Complete candidate file tree staged, activated and explicitly rolled back over synthetic old files. Optional COM links stay in an F-drive fixture, never executed. No app/process, Windows Start Menu, shell, user data or real installation.'
}
try {
    $t = New-LiliesInstallTransaction @common -SourceRoot $SourceRoot `
        -InstallManifestJson ('{"version":"0.3.57","purpose":"isolated-rehearsal"}') `
        -ShortcutPlan $shortcutPlan `
        -ValidatePayload { param($directory); Assert-RehearsalPayload $directory }
    $result['transactionId'] = $t.transactionId
    Assert-LiliesTreeMatches $target $oldSnapshot
    if ($IncludeShortcuts) { Assert-LiliesTreeMatches $linkRoot $oldLinks }
    $stageSnapshot = Get-LiliesTreeSnapshot $t.stage
    $activated = Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId
    Assert-LiliesTreeMatches $target $stageSnapshot
    Assert-LiliesTreeMatches $activated.previous $oldSnapshot
    if ($IncludeShortcuts) {
        Assert-LiliesShortcutState $shortcutPlan 'after'
        $result['shortcutActivationVerified'] = $true
    }
    $restored = Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted
    Assert-LiliesTreeMatches $target $oldSnapshot
    Assert-LiliesTreeMatches $restored.retainedFailed $stageSnapshot
    Assert-LiliesTreeMatches $SourceRoot $sourceSnapshot
    if ($IncludeShortcuts) {
        Assert-LiliesTreeMatches $linkRoot $oldLinks
        $result['originalShortcutsRestored'] = $true
    }
    $result['activationState'] = $activated.state
    $result['recoveryState'] = $restored.state
    $result['retainedNewProgram'] = $restored.retainedFailed
    $result['oldFilesRestored'] = $true
    $result['sourceUnchanged'] = $true
    $result['passed'] = $true
} catch {
    $result['error'] = $_.Exception.Message
    throw
} finally {
    $result['verifiedAt'] = [DateTimeOffset]::UtcNow.ToString('o')
    Write-LiliesExclusiveJson (Join-Path $ArtifactRoot 'result.json') $result
}
Write-Output ($result | ConvertTo-Json -Depth 6 -Compress)
