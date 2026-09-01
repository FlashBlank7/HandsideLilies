param(
    [string]$FormalRoot = 'F:\code\Lilies in the box',
    [string]$ReleaseVersion = '0.3.13',
    [string]$PromotionScript = 'scripts\promote_v0313.ps1',
    [string]$PackagedReport = 'artifacts\packaged-self-test-v0313.json',
    [string[]]$AdditionalFiles = @()
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
if ($ReleaseVersion -notmatch '^0\.3\.\d+$') {
    throw "Refusing unexpected release version: $ReleaseVersion"
}
$ReleaseTag = 'v' + ($ReleaseVersion -replace '[^0-9]', '')

function Assert-ChildPath([string]$Candidate, [string]$Parent) {
    $resolvedCandidate = [IO.Path]::GetFullPath($Candidate)
    $resolvedParent = [IO.Path]::GetFullPath($Parent)
    $prefix = $resolvedParent.TrimEnd([IO.Path]::DirectorySeparatorChar) +
        [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedCandidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing path outside expected parent: $resolvedCandidate"
    }
}

function Assert-ReleaseScratch([string]$Path, [string]$Parent, [string]$Prefix) {
    Assert-ChildPath $Path $Parent
    if (-not (Split-Path -Leaf $Path).StartsWith($Prefix, [StringComparison]::Ordinal)) {
        throw "Refusing unexpected release scratch path: $Path"
    }
}

function Assert-MatchingTrees([string]$SourceRoot, [string]$TargetRoot) {
    $sourceResolved = [IO.Path]::GetFullPath($SourceRoot)
    $targetResolved = [IO.Path]::GetFullPath($TargetRoot)
    if (-not (Test-Path -LiteralPath $sourceResolved -PathType Container) -or
        -not (Test-Path -LiteralPath $targetResolved -PathType Container)) {
        throw 'Cannot compare missing release trees.'
    }
    $sourceFiles = @(Get-ChildItem -LiteralPath $sourceResolved -Recurse -File)
    $targetFiles = @(Get-ChildItem -LiteralPath $targetResolved -Recurse -File)
    if ($sourceFiles.Count -ne $targetFiles.Count) {
        throw "Release tree file-count mismatch: $($sourceFiles.Count) != $($targetFiles.Count)"
    }
    $targetByRelative = @{}
    foreach ($targetFile in $targetFiles) {
        $relative = $targetFile.FullName.Substring($targetResolved.Length + 1)
        $targetByRelative[$relative] = $targetFile.FullName
    }
    foreach ($sourceFile in $sourceFiles) {
        $relative = $sourceFile.FullName.Substring($sourceResolved.Length + 1)
        if (-not $targetByRelative.ContainsKey($relative)) {
            throw "Release tree target is missing: $relative"
        }
        $sourceHash = (Get-FileHash -LiteralPath $sourceFile.FullName -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath $targetByRelative[$relative] -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) {
            throw "Release tree hash mismatch: $relative"
        }
    }
}

function Get-ReleaseTreeSnapshot([string]$Root) {
    $resolvedRoot = [IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
        throw "Cannot snapshot missing release tree: $resolvedRoot"
    }
    $snapshot = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File)) {
        $snapshot += [pscustomobject]@{
            relative = $file.FullName.Substring($resolvedRoot.Length + 1)
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        }
    }
    return $snapshot
}

function Assert-MatchingTreeSnapshot(
    [object[]]$ExpectedSnapshot,
    [string]$TargetRoot
) {
    $targetResolved = [IO.Path]::GetFullPath($TargetRoot)
    if (-not (Test-Path -LiteralPath $targetResolved -PathType Container)) {
        throw "Release move target is missing: $targetResolved"
    }
    $targetFiles = @(Get-ChildItem -LiteralPath $targetResolved -Recurse -File)
    if ($ExpectedSnapshot.Count -ne $targetFiles.Count) {
        throw "Release move file-count mismatch: $($ExpectedSnapshot.Count) != $($targetFiles.Count)"
    }
    $targetByRelative = @{}
    foreach ($targetFile in $targetFiles) {
        $relative = $targetFile.FullName.Substring($targetResolved.Length + 1)
        $targetByRelative[$relative] = $targetFile.FullName
    }
    foreach ($expected in $ExpectedSnapshot) {
        $relative = [string]$expected.relative
        if (-not $targetByRelative.ContainsKey($relative)) {
            throw "Release move target is missing: $relative"
        }
        $targetHash = (
            Get-FileHash -LiteralPath $targetByRelative[$relative] -Algorithm SHA256
        ).Hash
        if ([string]$expected.sha256 -ne $targetHash) {
            throw "Release move hash mismatch: $relative"
        }
    }
}

function Move-ReleaseTreeWithRetry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][object[]]$ExpectedSnapshot,
        [ValidateRange(4, 5)][int]$MaxAttempts = 5,
        [ValidateRange(0, 2000)][int]$InitialDelayMilliseconds = 250,
        [switch]$AllowCopyFallback,
        [string]$CopyFallbackIncoming = '',
        [scriptblock]$MoveOperation = {
            param([string]$MoveSource, [string]$MoveDestination)
            Move-Item -LiteralPath $MoveSource -Destination $MoveDestination -ErrorAction Stop
        },
        [scriptblock]$CopyOperation = {
            param([string]$CopySource, [string]$CopyDestination)
            Copy-Item `
                -LiteralPath $CopySource `
                -Destination $CopyDestination `
                -Recurse `
                -ErrorAction Stop
        },
        [scriptblock]$FinalMoveOperation = {
            param([string]$MoveSource, [string]$MoveDestination)
            Move-Item -LiteralPath $MoveSource -Destination $MoveDestination -ErrorAction Stop
        }
    )

    $resolvedSource = [IO.Path]::GetFullPath($Source)
    $resolvedDestination = [IO.Path]::GetFullPath($Destination)
    if ([IO.Path]::GetPathRoot($resolvedSource) -cne
        [IO.Path]::GetPathRoot($resolvedDestination)) {
        throw 'Release tree retry only supports same-volume directory moves.'
    }
    $resolvedIncoming = ''
    if ($AllowCopyFallback) {
        if ([string]::IsNullOrWhiteSpace($CopyFallbackIncoming)) {
            throw 'Release copy fallback requires a unique incoming path.'
        }
        $resolvedIncoming = [IO.Path]::GetFullPath($CopyFallbackIncoming)
        $destinationParent = [IO.Path]::GetDirectoryName($resolvedDestination)
        $incomingParent = [IO.Path]::GetDirectoryName($resolvedIncoming)
        if ($incomingParent -cne $destinationParent -or
            -not [IO.Path]::GetFileName($resolvedIncoming).StartsWith(
                '.release-incoming-',
                [StringComparison]::Ordinal
            )) {
            throw 'Release copy fallback must use a unique sibling incoming path.'
        }
        if ($resolvedIncoming -ceq $resolvedSource -or
            $resolvedIncoming -ceq $resolvedDestination) {
            throw 'Release copy fallback incoming path is not unique.'
        }
    }

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        $sourceExists = Test-Path -LiteralPath $resolvedSource -PathType Container
        $destinationExists = Test-Path -LiteralPath $resolvedDestination
        if (-not $sourceExists -and $destinationExists) {
            Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedDestination
            return
        }
        if ($sourceExists -and $destinationExists) {
            # A same-volume rename cannot legitimately leave both directory
            # names behind.  Verify the destination for diagnostics, but do
            # not accept an ambiguous duplicate as a completed replacement.
            Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedDestination
            throw "Release move left both source and destination present: $resolvedSource"
        }
        if (-not $sourceExists -and -not $destinationExists) {
            throw "Release move lost both source and destination: $resolvedSource"
        }

        $moveFailure = $null
        try {
            & $MoveOperation $resolvedSource $resolvedDestination
        } catch [System.UnauthorizedAccessException] {
            $moveFailure = $_
        } catch [System.IO.IOException] {
            $moveFailure = $_
        }

        $sourceExists = Test-Path -LiteralPath $resolvedSource -PathType Container
        $destinationExists = Test-Path -LiteralPath $resolvedDestination
        if (-not $sourceExists -and $destinationExists) {
            # Move-Item may complete the atomic rename before surfacing a
            # transient provider error.  Only a full tree hash match may turn
            # that state into success.
            Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedDestination
            return
        }
        if ($sourceExists -and $destinationExists) {
            Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedDestination
            throw "Release move produced an ambiguous duplicate: $resolvedDestination"
        }
        if (-not $sourceExists -and -not $destinationExists) {
            throw "Release move lost both directory names: $resolvedSource"
        }
        if ($null -eq $moveFailure) {
            throw "Release move returned without moving its source: $resolvedSource"
        }
        if ($attempt -ge $MaxAttempts) {
            if ($AllowCopyFallback) {
                # Never copy directly into the visible formal path.  Build a
                # unique sibling tree, verify every file, and only then expose
                # it with a final atomic rename into the absent destination.
                if (Test-Path -LiteralPath $resolvedIncoming) {
                    throw "Release copy fallback incoming already exists: $resolvedIncoming"
                }
                & $CopyOperation $resolvedSource $resolvedIncoming
                $sourceExists = Test-Path -LiteralPath $resolvedSource -PathType Container
                $destinationExists = Test-Path -LiteralPath $resolvedDestination
                $incomingExists = Test-Path -LiteralPath $resolvedIncoming -PathType Container
                if (-not $sourceExists -or $destinationExists -or -not $incomingExists) {
                    throw "Release copy fallback did not preserve an isolated incoming tree: $resolvedIncoming"
                }
                Assert-MatchingTreeSnapshot $ExpectedSnapshot $resolvedIncoming
                Move-ReleaseTreeWithRetry `
                    -Source $resolvedIncoming `
                    -Destination $resolvedDestination `
                    -ExpectedSnapshot $ExpectedSnapshot `
                    -MaxAttempts $MaxAttempts `
                    -InitialDelayMilliseconds $InitialDelayMilliseconds `
                    -MoveOperation $FinalMoveOperation
                return
            }
            throw $moveFailure
        }
        $delay = [int]($InitialDelayMilliseconds * [math]::Pow(2, $attempt - 1))
        Start-Sleep -Milliseconds $delay
    }
}

function Remove-ReleaseScratchWithRetry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Prefix,
        [ValidateRange(2, 5)][int]$MaxAttempts = 5,
        [ValidateRange(0, 2000)][int]$InitialDelayMilliseconds = 250,
        [scriptblock]$RemoveOperation = {
            param([string]$ScratchPath)
            Remove-Item -LiteralPath $ScratchPath -Recurse -Force -ErrorAction Stop
        }
    )

    $resolvedPath = [IO.Path]::GetFullPath($Path)
    Assert-ReleaseScratch $resolvedPath $Parent $Prefix
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            return
        }
        $removeFailure = $null
        try {
            & $RemoveOperation $resolvedPath
        } catch [System.UnauthorizedAccessException] {
            $removeFailure = $_
        } catch [System.IO.IOException] {
            $removeFailure = $_
        }
        if (-not (Test-Path -LiteralPath $resolvedPath)) {
            return
        }
        if ($null -eq $removeFailure) {
            throw "Release scratch cleanup returned without removing its target: $resolvedPath"
        }
        if ($attempt -ge $MaxAttempts) {
            throw $removeFailure
        }
        $delay = [int]($InitialDelayMilliseconds * [math]::Pow(2, $attempt - 1))
        Start-Sleep -Milliseconds $delay
    }
}

function Complete-ReleaseScratchCleanup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][object[]]$ScratchItems,
        [bool]$PromotionCommitted = $false,
        [System.Collections.IDictionary]$PromotionRecord = $null,
        [string]$PromotionManifestPath = '',
        [ValidateRange(2, 5)][int]$MaxAttempts = 5,
        [ValidateRange(0, 2000)][int]$InitialDelayMilliseconds = 250,
        [scriptblock]$RemoveOperation = {
            param([string]$ScratchPath)
            Remove-Item -LiteralPath $ScratchPath -Recurse -Force -ErrorAction Stop
        }
    )

    $pending = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $ScratchItems) {
        $scratchPath = [IO.Path]::GetFullPath([string]$item.path)
        try {
            if (Test-Path -LiteralPath $scratchPath) {
                Remove-ReleaseScratchWithRetry `
                    -Path $scratchPath `
                    -Parent ([string]$item.parent) `
                    -Prefix ([string]$item.prefix) `
                    -MaxAttempts $MaxAttempts `
                    -InitialDelayMilliseconds $InitialDelayMilliseconds `
                    -RemoveOperation $RemoveOperation
            }
        } catch {
            Write-Warning "Release cleanup is pending for $scratchPath`: $($_.Exception.Message)"
        }
        try {
            if (Test-Path -LiteralPath $scratchPath) {
                $pending.Add($scratchPath)
            }
        } catch {
            Write-Warning "Release cleanup state could not be read for $scratchPath`: $($_.Exception.Message)"
            $pending.Add($scratchPath)
        }
    }

    $manifestUpdated = $false
    if ($PromotionCommitted -and
        $null -ne $PromotionRecord -and
        -not [string]::IsNullOrWhiteSpace($PromotionManifestPath)) {
        $PromotionRecord['cleanupPending'] = [object[]]@($pending)
        try {
            $PromotionRecord |
                ConvertTo-Json |
                Set-Content -LiteralPath $PromotionManifestPath -Encoding UTF8
            $manifestUpdated = $true
        } catch {
            # The manifest was written before cleanup with a conservative list
            # of scratch paths, so an update failure cannot invalidate commit.
            Write-Warning "Release cleanup manifest update is pending: $($_.Exception.Message)"
        }
    }

    return [pscustomobject]@{
        cleanupPending = [object[]]@($pending)
        manifestUpdated = $manifestUpdated
    }
}

function Invoke-ReleaseRollback {
    [CmdletBinding()]
    param(
        [bool]$NewDistInstalled,
        [bool]$DistBackedUp,
        [Parameter(Mandatory = $true)][string]$FormalDist,
        [Parameter(Mandatory = $true)][string]$BackupDist,
        [Parameter(Mandatory = $true)][string]$RestoreIncoming,
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][string]$FormalRoot,
        [Parameter(Mandatory = $true)][string]$ReleaseTag,
        [object[]]$Records = @(),
        [ValidateRange(4, 5)][int]$MoveMaxAttempts = 5,
        [ValidateRange(0, 2000)][int]$InitialDelayMilliseconds = 250,
        [scriptblock]$QuarantineMoveOperation = {
            param([string]$MoveSource, [string]$MoveDestination)
            Move-Item -LiteralPath $MoveSource -Destination $MoveDestination -ErrorAction Stop
        },
        [scriptblock]$RestoreMoveOperation = {
            param([string]$MoveSource, [string]$MoveDestination)
            Move-Item -LiteralPath $MoveSource -Destination $MoveDestination -ErrorAction Stop
        }
    )

    $rollbackFailures = [System.Collections.Generic.List[System.Exception]]::new()

    try {
        if ($NewDistInstalled -and (Test-Path -LiteralPath $FormalDist -PathType Container)) {
            $failedDist = Join-Path $BackupRoot "failed-$ReleaseTag-dist\LiliesInTheBox"
            Assert-ChildPath $failedDist $BackupRoot
            New-Item -ItemType Directory -Path (Split-Path -Parent $failedDist) -Force | Out-Null
            $failedDistSnapshot = @(Get-ReleaseTreeSnapshot $FormalDist)
            Move-ReleaseTreeWithRetry `
                -Source $FormalDist `
                -Destination $failedDist `
                -ExpectedSnapshot $failedDistSnapshot `
                -MaxAttempts $MoveMaxAttempts `
                -InitialDelayMilliseconds $InitialDelayMilliseconds `
                -MoveOperation $QuarantineMoveOperation
        }
    } catch {
        $rollbackFailures.Add([InvalidOperationException]::new(
            'Rollback could not quarantine the partial formal dist.',
            $_.Exception
        ))
    }

    try {
        if ($DistBackedUp) {
            $backupTreeExists = Test-Path -LiteralPath $BackupDist -PathType Container
            $backupItemExists = Test-Path -LiteralPath $BackupDist
            $formalItemExists = Test-Path -LiteralPath $FormalDist
            if (-not $backupTreeExists) {
                if (-not $backupItemExists -and
                    -not $NewDistInstalled -and
                    (Test-Path -LiteralPath $FormalDist -PathType Container)) {
                    # Backup intent was recorded before the atomic move.  If
                    # the move never happened, the untouched old formal tree
                    # already is the correct rollback state.
                    $formalItemExists = $true
                } else {
                    throw "Rollback dist backup is missing or invalid: $BackupDist"
                }
            } else {
                $backupDistSnapshot = @(Get-ReleaseTreeSnapshot $BackupDist)
                $resolvedRestoreIncoming = [IO.Path]::GetFullPath($RestoreIncoming)
                $formalParent = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($FormalDist))
                if ([IO.Path]::GetDirectoryName($resolvedRestoreIncoming) -cne $formalParent -or
                    -not [IO.Path]::GetFileName($resolvedRestoreIncoming).StartsWith(
                        '.release-restore-',
                        [StringComparison]::Ordinal
                    )) {
                    throw 'Rollback restore must use a unique sibling incoming path.'
                }
                if ($formalItemExists) {
                    throw "Rollback restore target is still occupied: $FormalDist"
                }
                if (Test-Path -LiteralPath $resolvedRestoreIncoming) {
                    throw "Rollback restore incoming already exists: $resolvedRestoreIncoming"
                }
                Copy-Item `
                    -LiteralPath $BackupDist `
                    -Destination $resolvedRestoreIncoming `
                    -Recurse `
                    -ErrorAction Stop
                Assert-MatchingTreeSnapshot $backupDistSnapshot $resolvedRestoreIncoming
                Move-ReleaseTreeWithRetry `
                    -Source $resolvedRestoreIncoming `
                    -Destination $FormalDist `
                    -ExpectedSnapshot $backupDistSnapshot `
                    -MaxAttempts $MoveMaxAttempts `
                    -InitialDelayMilliseconds $InitialDelayMilliseconds `
                    -MoveOperation $RestoreMoveOperation
                Assert-MatchingTreeSnapshot $backupDistSnapshot $FormalDist
                Assert-MatchingTreeSnapshot $backupDistSnapshot $BackupDist
            }
        }
    } catch {
        $rollbackFailures.Add([InvalidOperationException]::new(
            'Rollback could not restore the backed-up formal dist.',
            $_.Exception
        ))
    }

    foreach ($record in $Records) {
        try {
            $target = [IO.Path]::GetFullPath((Join-Path $FormalRoot ([string]$record.path)))
            $backup = [IO.Path]::GetFullPath((Join-Path $BackupRoot ([string]$record.path)))
            Assert-ChildPath $target $FormalRoot
            Assert-ChildPath $backup $BackupRoot
            if ([bool]$record.existed) {
                if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) {
                    throw "Rollback source backup is missing: $backup"
                }
                New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
                Copy-Item -LiteralPath $backup -Destination $target -Force -ErrorAction Stop
                if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne
                    (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
                    throw "Rollback source hash mismatch: $($record.path)"
                }
            } elseif (Test-Path -LiteralPath $target -PathType Leaf) {
                Remove-Item -LiteralPath $target -Force -ErrorAction Stop
            }
        } catch {
            $rollbackFailures.Add([InvalidOperationException]::new(
                "Rollback could not restore source file: $($record.path)",
                $_.Exception
            ))
        }
    }

    return $rollbackFailures.ToArray()
}

$RelativeFiles = @(
    'pyproject.toml',
    'uv.lock',
    'README.md',
    'qml\Main.qml',
    'qml\V03FocusTimerAura.qml',
    'qml\V03PetBody.qml',
    'src\lilies\__init__.py',
    'src\lilies\app.py',
    'src\lilies\backend.py',
    'src\lilies\core\codex_subscription.py',
    'src\lilies\core\productivity.py',
    'src\lilies\core\socket_server.py',
    'src\lilies_in_the_box.egg-info\PKG-INFO',
    'themes\first-encounter\theme.json',
    'scripts\install_windows.ps1',
    'scripts\verify_compact_ui.py',
    'scripts\verify_cross_dpi_layout.py',
    'scripts\verify_habitat_ui.py',
    'scripts\verify_outfit_assets.py',
    'scripts\verify_outfit_ui.py',
    'scripts\verify_pose_assets.py',
    $PromotionScript,
    'tests\test_backend_v03_contract.py',
    'tests\test_cross_dpi_layout_v0312.py',
    'tests\test_habitat_ui_offscreen.py',
    'tests\test_outfit_asset_gate.py',
    'tests\test_pose_asset_gate.py',
    'tests\test_productivity_v03.py',
    'tests\test_single_instance_activation.py',
    'docs\outfit-asset-gate.md',
    'docs\pose-asset-gate.md',
    'art-reference\generated-v0.3\README.md',
    'art-reference\generated-v0.3\lilith-outfit-summer-dress-concept-v2.png',
    'artifacts\cross-dpi-layout-audit.json',
    'artifacts\focus-timer-aura-audit.json',
    'artifacts\habitat-pose-coverage.json',
    'artifacts\outfit-asset-gate.json',
    'artifacts\outfit-runtime-gate.json',
    'artifacts\pose-asset-gate.json',
    $PackagedReport
)
$RelativeFiles = @($RelativeFiles + $AdditionalFiles | Select-Object -Unique)

foreach ($relativePath in $RelativeFiles) {
    $source = [IO.Path]::GetFullPath((Join-Path $CandidateRoot $relativePath))
    Assert-ChildPath $source $CandidateRoot
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Candidate release file is missing: $source"
    }
}

$CandidateDist = [IO.Path]::GetFullPath((Join-Path $CandidateRoot 'dist\LiliesInTheBox'))
$CandidateExe = Join-Path $CandidateDist 'LiliesInTheBox.exe'
Assert-ChildPath $CandidateDist $CandidateRoot
if (-not (Test-Path -LiteralPath $CandidateExe -PathType Leaf)) {
    throw "Packaged candidate is missing: $CandidateExe"
}

$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
$StagePrefix = ".release-stage-$ReleaseTag-"
$StageRoot = [IO.Path]::GetFullPath((Join-Path $FormalRoot "$StagePrefix$PID-$stamp"))
$BackupRoot = [IO.Path]::GetFullPath((Join-Path $FormalRoot "release-backups\pre-$ReleaseTag-$stamp"))
Assert-ReleaseScratch $StageRoot $FormalRoot $StagePrefix
Assert-ChildPath $BackupRoot $FormalRoot
if (Test-Path -LiteralPath $StageRoot) {
    throw "Release stage already exists: $StageRoot"
}
if (Test-Path -LiteralPath $BackupRoot) {
    throw "Release backup already exists: $BackupRoot"
}

$FormalDist = [IO.Path]::GetFullPath((Join-Path $FormalRoot 'dist\LiliesInTheBox'))
$StageDist = [IO.Path]::GetFullPath((Join-Path $StageRoot 'dist\LiliesInTheBox'))
$BackupDist = [IO.Path]::GetFullPath((Join-Path $BackupRoot 'dist\LiliesInTheBox'))
$IncomingParent = [IO.Path]::GetDirectoryName($FormalDist)
$IncomingPrefix = ".release-incoming-$ReleaseTag-"
$IncomingDist = [IO.Path]::GetFullPath((Join-Path $IncomingParent (
    $IncomingPrefix + $PID + '-' + [guid]::NewGuid().ToString('N')
)))
$RestoreIncomingPrefix = ".release-restore-$ReleaseTag-"
$RestoreIncoming = [IO.Path]::GetFullPath((Join-Path $IncomingParent (
    $RestoreIncomingPrefix + $PID + '-' + [guid]::NewGuid().ToString('N')
)))
Assert-ChildPath $FormalDist $FormalRoot
Assert-ChildPath $StageDist $StageRoot
Assert-ChildPath $BackupDist $BackupRoot
Assert-ReleaseScratch $IncomingDist $IncomingParent $IncomingPrefix
Assert-ReleaseScratch $RestoreIncoming $IncomingParent $RestoreIncomingPrefix
if (Test-Path -LiteralPath $IncomingDist) {
    throw "Release incoming already exists: $IncomingDist"
}
if (Test-Path -LiteralPath $RestoreIncoming) {
    throw "Release restore incoming already exists: $RestoreIncoming"
}
$ReleaseScratchItems = @(
    [pscustomobject]@{ path = $StageRoot; parent = $FormalRoot; prefix = $StagePrefix },
    [pscustomobject]@{ path = $IncomingDist; parent = $IncomingParent; prefix = $IncomingPrefix },
    [pscustomobject]@{
        path = $RestoreIncoming
        parent = $IncomingParent
        prefix = $RestoreIncomingPrefix
    }
)

$records = @()
$distBackedUp = $false
$newDistInstalled = $false
$appliedFiles = @()
$promotionCommitted = $false
$promotionRecord = $null
$promotionManifestPath = Join-Path $BackupRoot 'promotion.json'
$cleanupResult = $null
try {
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    foreach ($relativePath in $RelativeFiles) {
        $source = Join-Path $CandidateRoot $relativePath
        $staged = Join-Path $StageRoot $relativePath
        Assert-ChildPath $staged $StageRoot
        New-Item -ItemType Directory -Path (Split-Path -Parent $staged) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $staged -Force
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash) {
            throw "Staged source hash mismatch: $relativePath"
        }
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $StageDist) -Force | Out-Null
    Copy-Item -LiteralPath $CandidateDist -Destination (Split-Path -Parent $StageDist) -Recurse
    Assert-MatchingTrees $CandidateDist $StageDist

    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    foreach ($relativePath in $RelativeFiles) {
        $target = [IO.Path]::GetFullPath((Join-Path $FormalRoot $relativePath))
        $backup = [IO.Path]::GetFullPath((Join-Path $BackupRoot $relativePath))
        Assert-ChildPath $target $FormalRoot
        Assert-ChildPath $backup $BackupRoot
        $existed = Test-Path -LiteralPath $target -PathType Leaf
        $records += [pscustomobject]@{ path = $relativePath; existed = $existed }
        if ($existed) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $backup) -Force | Out-Null
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
    }
    $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BackupRoot 'source-files.json') -Encoding UTF8

    foreach ($relativePath in $RelativeFiles) {
        $staged = Join-Path $StageRoot $relativePath
        $target = Join-Path $FormalRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        # A copy may modify the target before reporting failure, so mark it as
        # rollback-pending immediately before the first mutating operation.
        $appliedFiles += $relativePath
        Copy-Item -LiteralPath $staged -Destination $target -Force
        if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
            throw "Promoted source hash mismatch: $relativePath"
        }
    }

    if (Test-Path -LiteralPath $FormalDist -PathType Container) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupDist) -Force | Out-Null
        $formalDistSnapshot = @(Get-ReleaseTreeSnapshot $FormalDist)
        # Record backup intent before the atomic move.  If the rename completes
        # but post-move verification fails, rollback must discover BackupDist.
        $distBackedUp = $true
        Move-ReleaseTreeWithRetry `
            -Source $FormalDist `
            -Destination $BackupDist `
            -ExpectedSnapshot $formalDistSnapshot
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $FormalDist) -Force | Out-Null
    $stageDistSnapshot = @(Get-ReleaseTreeSnapshot $StageDist)
    # Mark the install transaction before either the direct rename or the
    # isolated incoming fallback starts.
    $newDistInstalled = $true
    Move-ReleaseTreeWithRetry `
        -Source $StageDist `
        -Destination $FormalDist `
        -ExpectedSnapshot $stageDistSnapshot `
        -AllowCopyFallback `
        -CopyFallbackIncoming $IncomingDist
    Assert-MatchingTrees $CandidateDist $FormalDist

    $exeHash = (Get-FileHash -LiteralPath $CandidateExe -Algorithm SHA256).Hash
    $initialCleanupPending = @(
        $ReleaseScratchItems |
            Where-Object { Test-Path -LiteralPath $_.path } |
            ForEach-Object { [IO.Path]::GetFullPath([string]$_.path) }
    )
    $promotionRecord = [ordered]@{
        version = $ReleaseVersion
        promotedAt = [DateTimeOffset]::UtcNow.ToString('o')
        files = $RelativeFiles.Count
        distFiles = @(Get-ChildItem -LiteralPath $FormalDist -Recurse -File).Count
        exeSha256 = $exeHash
        backup = $BackupRoot
        cleanupPending = [object[]]@($initialCleanupPending)
    }
    $promotionRecord |
        ConvertTo-Json |
        Set-Content -LiteralPath $promotionManifestPath -Encoding UTF8
    $promotionCommitted = $true

    Write-Output "Promoted Lilies in the box $ReleaseTag"
    Write-Output "Files: $($RelativeFiles.Count)"
    Write-Output "Backup: $BackupRoot"
    Write-Output "EXE SHA256: $exeHash"
} catch {
    $failure = $_
    $rollbackFailures = @()
    $rollbackRecords = @(
        $records | Where-Object { $appliedFiles -contains [string]$_.path }
    )
    try {
        $rollbackFailures = @(Invoke-ReleaseRollback `
            -NewDistInstalled $newDistInstalled `
            -DistBackedUp $distBackedUp `
            -FormalDist $FormalDist `
            -BackupDist $BackupDist `
            -RestoreIncoming $RestoreIncoming `
            -BackupRoot $BackupRoot `
            -FormalRoot $FormalRoot `
            -ReleaseTag $ReleaseTag `
            -Records $rollbackRecords)
    } catch {
        $rollbackFailures = @([InvalidOperationException]::new(
            'Rollback coordinator failed unexpectedly.',
            $_.Exception
        ))
    }
    if ($rollbackFailures.Count -gt 0) {
        $allFailures = [System.Collections.Generic.List[System.Exception]]::new()
        $allFailures.Add($failure.Exception)
        foreach ($rollbackFailure in $rollbackFailures) {
            $allFailures.Add($rollbackFailure)
        }
        throw [AggregateException]::new(
            "Promotion failed and rollback reported $($rollbackFailures.Count) error(s).",
            $allFailures.ToArray()
        )
    }
    throw $failure
} finally {
    try {
        $cleanupResult = Complete-ReleaseScratchCleanup `
            -ScratchItems $ReleaseScratchItems `
            -PromotionCommitted $promotionCommitted `
            -PromotionRecord $promotionRecord `
            -PromotionManifestPath $promotionManifestPath
    } catch {
        # A conservative cleanupPending list was committed before cleanup.
        # Scratch cleanup must never mask a successful release or its failure.
        Write-Warning "Release cleanup remains pending: $($_.Exception.Message)"
    }
}
