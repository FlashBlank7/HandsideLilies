from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_PROBE = (
    PROJECT_ROOT / "scripts" / "verify_packaged_compact_resources.ps1"
)
PROMOTION = PROJECT_ROOT / "scripts" / "promote_v0340.ps1"
FORBIDDEN_FAMILIES = (
    "QtWebEngine",
    "QtQuick3D",
    "QtCharts",
    "QtGraphs",
    "QtDataVisualization",
    "QtLocation",
    "QtPdf",
    "QtWebSockets",
)


def _powershell_function(source: str, name: str) -> str:
    match = re.search(rf"(?m)^function\s+{re.escape(name)}\b", source)
    assert match is not None, f"missing PowerShell function: {name}"
    opening = source.find("{", match.end())
    assert opening >= 0
    depth = 0
    quote = ""
    index = opening
    while index < len(source):
        character = source[index]
        if quote:
            if character == "`":
                index += 2
                continue
            if character == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    index += 2
                    continue
                quote = ""
        elif character in {"'", '"'}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
        index += 1
    raise AssertionError(f"unterminated PowerShell function: {name}")


def _run_powershell(
    tmp_path: Path,
    script: Path,
    function_names: tuple[str, ...],
    body: str,
) -> dict[str, object]:
    source = script.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _powershell_function(source, name) for name in function_names
    )
    environment = dict(os.environ)
    environment["LILIES_FOOTPRINT_TEST_ROOT"] = str(tmp_path / "dist")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            functions + "\n$ErrorActionPreference = 'Stop'\n" + body,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_resource_probe_scans_every_forbidden_qt_family(
    tmp_path: Path,
) -> None:
    report = _run_powershell(
        tmp_path,
        RESOURCE_PROBE,
        ("Get-PackagedDistFootprint",),
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_FOOTPRINT_TEST_ROOT)
[IO.Directory]::CreateDirectory($root) | Out-Null
$names = @(
    'Qt6WebEngineCore.dll',
    'Qt6Quick3D.dll',
    'Qt6Charts.dll',
    'Qt6Graphs.dll',
    'Qt6DataVisualization.dll',
    'Qt6Location.dll',
    'Qt6Pdf.dll',
    'Qt6WebSockets.dll'
)
foreach ($name in $names) {
    [IO.File]::WriteAllBytes((Join-Path $root $name), [byte[]]@(1))
}
$evidence = Get-PackagedDistFootprint $root
[ordered]@{
    totalBytes = [long]$evidence.totalBytes
    fileCount = [int]$evidence.fileCount
    configuredFamilies = [object[]]@($evidence.forbiddenFamilies)
    matchedFamilies = [object[]]@(
        $evidence.forbiddenMatches |
            Select-Object -ExpandProperty family -Unique
    )
    matchCount = @($evidence.forbiddenMatches).Count
} | ConvertTo-Json -Compress -Depth 4
''',
    )

    assert report["totalBytes"] == 8
    assert report["fileCount"] == 8
    assert report["configuredFamilies"] == list(FORBIDDEN_FAMILIES)
    assert set(report["matchedFamilies"]) == set(FORBIDDEN_FAMILIES)
    assert report["matchCount"] == 8


def test_promotion_recomputes_footprint_and_rejects_fabricated_evidence(
    tmp_path: Path,
) -> None:
    report = _run_powershell(
        tmp_path,
        PROMOTION,
        (
            "Assert-JsonBoolean",
            "Get-RequiredJsonInteger",
            "Get-PackagedDistFootprint",
            "Assert-PackagedFootprintReport",
        ),
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_FOOTPRINT_TEST_ROOT)
[IO.Directory]::CreateDirectory((Join-Path $root 'nested')) | Out-Null
[IO.File]::WriteAllBytes((Join-Path $root 'LiliesInTheBox.exe'), [byte[]]@(1, 2, 3))
[IO.File]::WriteAllBytes((Join-Path $root 'nested\payload.bin'), [byte[]]@(4, 5, 6, 7))

function New-Report([object]$Evidence) {
    $report = [pscustomobject]@{
        distTotalBytes = [long]$Evidence.totalBytes
        distFileCount = [int]$Evidence.fileCount
        forbiddenQtResources = [pscustomobject]@{
            scanRoot = [string]$Evidence.distRoot
            scanned = $true
            scannedItemCount = [int]$Evidence.itemCount
            families = [object[]]@($Evidence.forbiddenFamilies)
            matchCount = 0
            matches = [object[]]@()
            passed = $true
        }
    }
    return $report | ConvertTo-Json -Depth 5 | ConvertFrom-Json
}

$evidence = Get-PackagedDistFootprint $root
$validAccepted = $true
try {
    Assert-PackagedFootprintReport (New-Report $evidence) $root
} catch {
    $validAccepted = $false
}

$staleBytesRejected = $false
$stale = New-Report $evidence
$stale.distTotalBytes++
try {
    Assert-PackagedFootprintReport $stale $root
} catch {
    $staleBytesRejected = $true
}

$missingFamilyRejected = $false
$missingFamily = New-Report $evidence
$missingFamily.forbiddenQtResources.families = [object[]]@(
    $evidence.forbiddenFamilies | Select-Object -First 7
)
try {
    Assert-PackagedFootprintReport $missingFamily $root
} catch {
    $missingFamilyRejected = $true
}

$scalarMatchesRejected = $false
$scalarMatches = New-Report $evidence
$scalarMatches.forbiddenQtResources.matches = 'not-an-array'
try {
    Assert-PackagedFootprintReport $scalarMatches $root
} catch {
    $scalarMatchesRejected = $true
}

[IO.File]::WriteAllBytes((Join-Path $root 'Qt6Pdf.dll'), [byte[]]@(8))
$forbiddenEvidence = Get-PackagedDistFootprint $root
$fabricatedZeroRejected = $false
try {
    Assert-PackagedFootprintReport (New-Report $forbiddenEvidence) $root
} catch {
    $fabricatedZeroRejected = $true
}

[ordered]@{
    validAccepted = $validAccepted
    staleBytesRejected = $staleBytesRejected
    missingFamilyRejected = $missingFamilyRejected
    scalarMatchesRejected = $scalarMatchesRejected
    fabricatedZeroRejected = $fabricatedZeroRejected
} | ConvertTo-Json -Compress
''',
    )

    assert report == {
        "validAccepted": True,
        "staleBytesRejected": True,
        "missingFamilyRejected": True,
        "scalarMatchesRejected": True,
        "fabricatedZeroRejected": True,
    }


def test_promotion_requires_empty_typed_qml_warning_evidence(
    tmp_path: Path,
) -> None:
    report = _run_powershell(
        tmp_path,
        PROMOTION,
        (
            "Assert-JsonBoolean",
            "Get-RequiredJsonInteger",
            "Assert-QmlWarningEvidence",
        ),
        r'''
$valid = '{"qmlWarningsPassed":true,"qmlWarningCount":0,"qmlWarnings":[]}' |
    ConvertFrom-Json
$validAccepted = $true
try { Assert-QmlWarningEvidence $valid 'selfTest' } catch { $validAccepted = $false }

$falsePassedRejected = $false
$falsePassed = '{"qmlWarningsPassed":false,"qmlWarningCount":0,"qmlWarnings":[]}' |
    ConvertFrom-Json
try { Assert-QmlWarningEvidence $falsePassed 'selfTest' } catch { $falsePassedRejected = $true }

$nonzeroCountRejected = $false
$nonzeroCount = '{"qmlWarningsPassed":true,"qmlWarningCount":1,"qmlWarnings":[]}' |
    ConvertFrom-Json
try { Assert-QmlWarningEvidence $nonzeroCount 'selfTest' } catch { $nonzeroCountRejected = $true }

$scalarWarningsRejected = $false
$scalarWarnings = '{"qmlWarningsPassed":true,"qmlWarningCount":0,"qmlWarnings":""}' |
    ConvertFrom-Json
try { Assert-QmlWarningEvidence $scalarWarnings 'selfTest' } catch { $scalarWarningsRejected = $true }

$nonemptyWarningsRejected = $false
$nonemptyWarnings = '{"qmlWarningsPassed":true,"qmlWarningCount":0,"qmlWarnings":["warning"]}' |
    ConvertFrom-Json
try { Assert-QmlWarningEvidence $nonemptyWarnings 'selfTest' } catch { $nonemptyWarningsRejected = $true }

[ordered]@{
    validAccepted = $validAccepted
    falsePassedRejected = $falsePassedRejected
    nonzeroCountRejected = $nonzeroCountRejected
    scalarWarningsRejected = $scalarWarningsRejected
    nonemptyWarningsRejected = $nonemptyWarningsRejected
} | ConvertTo-Json -Compress
''',
    )

    assert report == {
        "validAccepted": True,
        "falsePassedRejected": True,
        "nonzeroCountRejected": True,
        "scalarWarningsRejected": True,
        "nonemptyWarningsRejected": True,
    }


def test_probe_and_promotion_wire_the_fail_closed_evidence() -> None:
    probe = RESOURCE_PROBE.read_text(encoding="utf-8")
    promotion = PROMOTION.read_text(encoding="utf-8")

    assert "distTotalBytes = [long]$distFootprint.totalBytes" in probe
    assert "forbiddenQtResources = [ordered]@{" in probe
    assert "$distFootprint.forbiddenMatches.Count -eq 0" in probe
    assert "Assert-PackagedFootprintReport $compactResource $CandidateDist" in promotion
    assert "Assert-QmlWarningEvidence $selfTest 'selfTest'" in promotion
    assert promotion.count("'tests\\test_packaged_footprint_gate_v0340.py'") == 2
    for family in FORBIDDEN_FAMILIES:
        assert f"{family} = '(?i)Qt(?:6)?" in probe
        assert f"{family} = '(?i)Qt(?:6)?" in promotion
