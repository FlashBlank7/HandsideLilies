from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMOTION = PROJECT_ROOT / "scripts" / "promote_v0331.ps1"


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


def test_v0331_cache_gate_rejects_missing_false_and_escaped_routing() -> None:
    source = PROMOTION.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _powershell_function(source, name)
        for name in ("Assert-JsonBoolean", "Assert-QtCacheRoutingReport")
    )
    valid = {
        "qtCacheRouting": {
            "dataRoot": r"F:\probe\private-data",
            "cacheRoot": r"F:\probe\private-data\cache",
            "qmlDiskCachePath": r"F:\probe\private-data\cache\qmlcache",
            "rhiPipelineCacheLoadPath": (
                r"F:\probe\private-data\cache\qt-rhi-pipeline-cache.bin"
            ),
            "rhiPipelineCacheSavePath": (
                r"F:\probe\private-data\cache\qt-rhi-pipeline-cache.bin"
            ),
            "pathsWithinDataRoot": True,
            "environmentApplied": True,
            "qtShaderDiskCacheDisabled": True,
            "passed": True,
        }
    }
    report_json = json.dumps(valid, separators=(",", ":"))
    body = rf'''
$ErrorActionPreference = 'Stop'
$valid = @'
{report_json}
'@ | ConvertFrom-Json

function Copy-Probe([object]$Source) {{
    return $Source | ConvertTo-Json -Depth 20 | ConvertFrom-Json
}}

function Test-Rejected([object]$Probe) {{
    try {{
        Assert-QtCacheRoutingReport $Probe 'probe'
        return $false
    }} catch {{
        return $true
    }}
}}

$validAccepted = $true
try {{ Assert-QtCacheRoutingReport $valid 'probe' }} catch {{ $validAccepted = $false }}
$missing = [pscustomobject]@{{}}
$badEnvironment = Copy-Probe $valid
$badEnvironment.qtCacheRouting.environmentApplied = $false
$badShader = Copy-Probe $valid
$badShader.qtCacheRouting.qtShaderDiskCacheDisabled = $false
$escapedQml = Copy-Probe $valid
$escapedQml.qtCacheRouting.qmlDiskCachePath = 'C:\Users\probe\AppData\Local\qmlcache'
$siblingPrefix = Copy-Probe $valid
$siblingPrefix.qtCacheRouting.cacheRoot = 'F:\probe\private-data-escaped\cache'
$mismatchedPipeline = Copy-Probe $valid
$mismatchedPipeline.qtCacheRouting.rhiPipelineCacheSavePath = 'F:\probe\private-data\cache\other.bin'

[ordered]@{{
    validAccepted = $validAccepted
    rejectsMissing = Test-Rejected $missing
    rejectsBadEnvironment = Test-Rejected $badEnvironment
    rejectsBadShader = Test-Rejected $badShader
    rejectsEscapedQml = Test-Rejected $escapedQml
    rejectsSiblingPrefix = Test-Rejected $siblingPrefix
    rejectsMismatchedPipeline = Test-Rejected $mismatchedPipeline
}} | ConvertTo-Json -Compress
'''
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            functions + "\n" + body,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=25,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {
        "validAccepted": True,
        "rejectsMissing": True,
        "rejectsBadEnvironment": True,
        "rejectsBadShader": True,
        "rejectsEscapedQml": True,
        "rejectsSiblingPrefix": True,
        "rejectsMismatchedPipeline": True,
    }


def test_v0331_gate_applies_cache_contract_to_both_packaged_reports() -> None:
    source = PROMOTION.read_text(encoding="utf-8")
    assert "Assert-QtCacheRoutingReport $selfTest 'selfTest'" in source
    assert (
        "Assert-QtCacheRoutingReport $windowsStartup 'windowsStartup'" in source
    )
