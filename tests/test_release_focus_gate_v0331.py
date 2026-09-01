from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATE_SOURCES = (
    PROJECT_ROOT / "scripts" / "promote_v0331.ps1",
    PROJECT_ROOT / "scripts" / "verify_packaged_compact_resources.ps1",
)
GATE_FUNCTIONS = (
    "Assert-JsonBoolean",
    "Get-RequiredJsonInteger",
    "Get-RequiredJsonNumber",
    "Assert-JsonString",
    "Assert-FocusTimerAnimationReport",
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


def _valid_report() -> dict[str, object]:
    return {
        "focusTimerAnimationPassed": True,
        "focusTimerAnimation": {
            "windowFound": True,
            "surfaceFound": True,
            "sequencesStrictlyIncreasing": True,
            "passed": True,
            "transitionSequences": [1, 2, 3, 4],
            "started": {
                "passed": True,
                "transitionKind": "started",
                "sequence": 1,
                "backendState": "running",
                "visualState": "running",
                "windowVisible": True,
                "windowExposed": True,
                "startAcknowledgementActive": True,
                "startPulse": 0.5,
                "targetFps": 60,
                "motionTickBefore": 2,
                "motionTickAfter": 8,
            },
            "paused": {
                "passed": True,
                "transitionKind": "paused",
                "sequence": 2,
                "backendState": "paused",
                "visualState": "paused",
                "breathing": False,
                "startAcknowledgementActive": False,
                "startPulse": 0.0,
                "targetFps": 0,
                "motionTickBefore": 10,
                "motionTickAfter": 10,
                "surfaceScaleBefore": 1.0,
                "surfaceScaleAfter": 1.0,
            },
            "resumed": {
                "passed": True,
                "transitionKind": "resumed",
                "sequence": 3,
                "backendState": "running",
                "visualState": "running",
                "breathing": True,
                "startAcknowledgementActive": False,
                "startPulse": 0.0,
                "targetFps": 15,
                "motionTickBefore": 10,
                "motionTickAfter": 15,
            },
            "finished": {
                "passed": True,
                "transitionKind": "finished",
                "sequence": 4,
                "backendActive": False,
                "visualState": "finished",
                "completionVisible": True,
                "windowVisible": True,
                "windowExposed": True,
                "startPulse": 0.0,
            },
        },
    }


def _exercise_gate(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8")
    functions = "\n\n".join(
        _powershell_function(source, name) for name in GATE_FUNCTIONS
    )
    report_json = json.dumps(_valid_report(), separators=(",", ":"))
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
        Assert-FocusTimerAnimationReport $Probe
        return $false
    }} catch {{
        return $true
    }}
}}

$validAccepted = $true
$validError = ''
try {{ Assert-FocusTimerAnimationReport $valid }} catch {{
    $validAccepted = $false
    $validError = $_.Exception.Message
}}

$badTotal = Copy-Probe $valid
$badTotal.focusTimerAnimationPassed = $false
$badStage = Copy-Probe $valid
$badStage.focusTimerAnimation.paused.passed = $false
$badSequence = Copy-Probe $valid
$badSequence.focusTimerAnimation.finished.sequence = 3
$badStart = Copy-Probe $valid
$badStart.focusTimerAnimation.started.startPulse = 0.0
$badPause = Copy-Probe $valid
$badPause.focusTimerAnimation.paused.motionTickAfter = 11
$badResume = Copy-Probe $valid
$badResume.focusTimerAnimation.resumed.startAcknowledgementActive = $true
$badFinish = Copy-Probe $valid
$badFinish.focusTimerAnimation.finished.completionVisible = $false

[ordered]@{{
    validAccepted = $validAccepted
    validError = $validError
    rejectsBadTotal = Test-Rejected $badTotal
    rejectsBadStage = Test-Rejected $badStage
    rejectsBadSequence = Test-Rejected $badSequence
    rejectsBadStart = Test-Rejected $badStart
    rejectsBadPause = Test-Rejected $badPause
    rejectsBadResume = Test-Rejected $badResume
    rejectsBadFinish = Test-Rejected $badFinish
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
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_both_release_paths_enforce_the_real_four_stage_focus_contract() -> None:
    expected = {
        "validAccepted": True,
        "validError": "",
        "rejectsBadTotal": True,
        "rejectsBadStage": True,
        "rejectsBadSequence": True,
        "rejectsBadStart": True,
        "rejectsBadPause": True,
        "rejectsBadResume": True,
        "rejectsBadFinish": True,
    }
    for path in GATE_SOURCES:
        assert _exercise_gate(path) == expected
