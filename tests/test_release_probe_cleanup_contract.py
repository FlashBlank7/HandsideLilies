from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOX_WORLD_TEST = PROJECT_ROOT / "tests" / "test_box_world_presentation_offscreen.py"
BOX_WORLD_VERIFIER = PROJECT_ROOT / "scripts" / "verify_box_world_presentation.py"
RESOURCE_PROBE = PROJECT_ROOT / "scripts" / "verify_packaged_compact_resources.ps1"
CURRENT_PROMOTION = PROJECT_ROOT / "scripts" / "promote_v0354.ps1"


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


def _run_cleanup_function(
    tmp_path: Path,
    body: str,
    *,
    script: Path = RESOURCE_PROBE,
    function_names: tuple[str, ...] = ("Remove-ExactDiagnosticDirectoryWithRetry",),
) -> dict[str, object]:
    source = script.read_text(encoding="utf-8")
    function = "\n\n".join(
        _powershell_function(source, name) for name in function_names
    )
    environment = dict(os.environ)
    environment["LILIES_CLEANUP_TEST_ROOT"] = str(tmp_path)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            function + "\n$ErrorActionPreference = 'Stop'\n" + body,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_box_world_process_timeout_and_cleanup_structure_are_fail_safe() -> None:
    harness = BOX_WORLD_TEST.read_text(encoding="utf-8")
    verifier = BOX_WORLD_VERIFIER.read_text(encoding="utf-8")

    assert "BOX_WORLD_PROCESS_TIMEOUT_SECONDS = 30" in harness
    assert harness.count("timeout=BOX_WORLD_PROCESS_TIMEOUT_SECONDS") == 2
    assert "with tempfile.TemporaryDirectory(" in verifier
    assert "def _run_verification(" in verifier
    main = verifier[verifier.index("def main() -> int:") :]
    assert main.index("with tempfile.TemporaryDirectory(") < main.index(
        "backend = Backend("
    )
    assert main.index("finally:\n                try:") < main.index(
        "backend.shutdown()"
    )
    assert main.index("backend.shutdown()") < main.index("app.processEvents()")
    assert main.rindex('os.environ["LILIES_DATA_DIR"]') > main.index(
        "with tempfile.TemporaryDirectory("
    )


def test_diagnostic_cleanup_retries_transient_io_failures(tmp_path: Path) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_CLEANUP_TEST_ROOT)
$target = Join-Path $root 'lilies-packaged-resource-transient'
[IO.Directory]::CreateDirectory($target) | Out-Null
Set-Content -LiteralPath (Join-Path $target 'held.txt') -Value 'probe'
$script:deleteAttempts = 0
$deleter = {
    param([string]$Target)
    $script:deleteAttempts++
    if ($script:deleteAttempts -lt 3) {
        throw [IO.IOException]::new('synthetic transient lock')
    }
    [IO.Directory]::Delete($Target, $true)
}
$result = Remove-ExactDiagnosticDirectoryWithRetry `
    -Path $target `
    -TemporaryRoot $root `
    -MaxAttempts 5 `
    -InitialDelayMilliseconds 10 `
    -DeleteDirectory $deleter
[ordered]@{
    removed = [bool]$result.removed
    reportedAttempts = [int]$result.attempts
    injectedAttempts = $script:deleteAttempts
    stillExists = Test-Path -LiteralPath $target
} | ConvertTo-Json -Compress
''',
    )
    assert report == {
        "removed": True,
        "reportedAttempts": 3,
        "injectedAttempts": 3,
        "stillExists": False,
    }


def test_diagnostic_cleanup_aggregates_terminal_retry_failures(
    tmp_path: Path,
) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_CLEANUP_TEST_ROOT)
$target = Join-Path $root 'lilies-packaged-resource-terminal'
[IO.Directory]::CreateDirectory($target) | Out-Null
$script:deleteAttempts = 0
$deleter = {
    param([string]$Target)
    $script:deleteAttempts++
    throw [UnauthorizedAccessException]::new('synthetic persistent lock')
}
$caught = $false
$innerCount = 0
try {
    Remove-ExactDiagnosticDirectoryWithRetry `
        -Path $target `
        -TemporaryRoot $root `
        -MaxAttempts 5 `
        -InitialDelayMilliseconds 10 `
        -DeleteDirectory $deleter | Out-Null
} catch [AggregateException] {
    $caught = $true
    $innerCount = $_.Exception.InnerExceptions.Count
}
[IO.Directory]::Delete($target, $true)
[ordered]@{
    caughtAggregate = $caught
    innerCount = $innerCount
    injectedAttempts = $script:deleteAttempts
} | ConvertTo-Json -Compress
''',
    )
    assert report == {
        "caughtAggregate": True,
        "innerCount": 5,
        "injectedAttempts": 5,
    }


def test_diagnostic_cleanup_refuses_unexpected_path_type(tmp_path: Path) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_CLEANUP_TEST_ROOT)
$target = Join-Path $root 'lilies-packaged-resource-not-a-directory'
[IO.File]::WriteAllText($target, 'do not delete')
$caught = $false
$innerCount = 0
try {
    Remove-ExactDiagnosticDirectoryWithRetry `
        -Path $target `
        -TemporaryRoot $root `
        -MaxAttempts 5 `
        -InitialDelayMilliseconds 10 | Out-Null
} catch [AggregateException] {
    $caught = $true
    $innerCount = $_.Exception.InnerExceptions.Count
}
$stillExists = Test-Path -LiteralPath $target -PathType Leaf
[IO.File]::Delete($target)
[ordered]@{
    caughtAggregate = $caught
    innerCount = $innerCount
    preservedUnexpectedFile = $stillExists
} | ConvertTo-Json -Compress
''',
    )
    assert report == {
        "caughtAggregate": True,
        "innerCount": 1,
        "preservedUnexpectedFile": True,
    }


def test_diagnostic_cleanup_refuses_a_prefixed_sibling_directory(
    tmp_path: Path,
) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
$root = [IO.Path]::GetFullPath($env:LILIES_CLEANUP_TEST_ROOT)
$outside = Join-Path (Split-Path -Parent $root) (
    'lilies-packaged-resource-outside-' + [Guid]::NewGuid().ToString('N')
)
[IO.Directory]::CreateDirectory($outside) | Out-Null
$script:deleteInvoked = $false
$deleter = {
    param([string]$Target)
    $script:deleteInvoked = $true
    [IO.Directory]::Delete($Target, $true)
}
$caught = $false
try {
    Remove-ExactDiagnosticDirectoryWithRetry `
        -Path $outside `
        -TemporaryRoot $root `
        -DeleteDirectory $deleter | Out-Null
} catch [InvalidOperationException] {
    $caught = $true
}
$preserved = Test-Path -LiteralPath $outside -PathType Container
[IO.Directory]::Delete($outside, $true)
[ordered]@{
    rejected = $caught
    deleteInvoked = $script:deleteInvoked
    preservedOutsideDirectory = $preserved
} | ConvertTo-Json -Compress
''',
    )
    assert report == {
        "rejected": True,
        "deleteInvoked": False,
        "preservedOutsideDirectory": True,
    }


def test_resource_probe_cannot_pass_when_cleanup_fails() -> None:
    source = RESOURCE_PROBE.read_text(encoding="utf-8")
    cleanup = _powershell_function(
        source, "Remove-ExactDiagnosticDirectoryWithRetry"
    )

    assert "[ValidateRange(2, 5)]" in cleanup
    assert "catch [System.UnauthorizedAccessException]" in cleanup
    assert "catch [System.IO.IOException]" in cleanup
    assert "Start-Sleep -Milliseconds" in cleanup
    assert "Refusing diagnostic cleanup outside the exact temporary directory" in cleanup
    assert "$cleanupFailures.Add($_.Exception)" in source
    assert "foreach ($inner in $_.Exception.InnerExceptions)" in source
    assert "did not stop within five seconds" in source
    assert "$cleanupSucceeded" in source
    passed_expression = source[source.index("passed = [bool](") :]
    assert "$cleanupSucceeded" in passed_expression.split(")\n", 1)[0]
    assert "errors = @($cleanupFailures" in source


def test_live_resource_snapshot_survives_process_cleanup(tmp_path: Path) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
$fake = [pscustomobject]@{
    Id = 42; HasExited = $false; RefreshCount = 0
    WorkingSet64 = [long]67108864; PrivateMemorySize64 = [long]41943040
    Threads = @(1, 2, 3, 4); HandleCount = 17; Responding = $true
    Modules = @(
        [pscustomobject]@{ ModuleName = 'Qt6Core.dll' },
        [pscustomobject]@{ ModuleName = 'Qt6Gui.dll' }
    )
}
$fake | Add-Member -MemberType ScriptMethod -Name Refresh -Value {
    $this.RefreshCount++
}
$snapshot = Get-LiveProcessResourceSnapshot -Process $fake -ExpectedProcessId 42
# Simulate Stop-Process and refreshed lazy Process properties before JSON output.
$fake.HasExited = $true
$fake.WorkingSet64 = 0
$fake.PrivateMemorySize64 = 0
$fake.Threads = @()
$fake.HandleCount = 0
$fake.Responding = $false
$fake.Modules[0].ModuleName = 'Qt6Multimedia.dll'
$fake.Modules = @()
[ordered]@{
    snapshot = $snapshot
    refreshCount = $fake.RefreshCount
    lazyProcessExited = $fake.HasExited
} | ConvertTo-Json -Compress -Depth 4
''',
        function_names=("Get-LiveProcessResourceSnapshot",),
    )
    snapshot = report["snapshot"]
    assert snapshot["workingSetBytes"] == 67108864
    assert snapshot["privateBytes"] == 41943040
    assert snapshot["threads"] == 4
    assert snapshot["handles"] == 17
    assert snapshot["responding"] is True
    assert snapshot["sampledWhileAlive"] is True
    assert snapshot["processId"] == 42
    assert snapshot["capturedAt"]
    assert snapshot["moduleNames"] == ["Qt6Core.dll", "Qt6Gui.dll"]
    assert report["refreshCount"] == 2
    assert report["lazyProcessExited"] is True


def test_resource_snapshot_rejects_exited_empty_and_racing_processes(
    tmp_path: Path,
) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
function New-FakeProcess {
    $fake = [pscustomobject]@{
        Id = 42; HasExited = $false; RefreshCount = 0; ExitOnSecondRefresh = $false
        WorkingSet64 = [long]67108864; PrivateMemorySize64 = [long]41943040
        Threads = @(1, 2); HandleCount = 17; Responding = $true
        Modules = @([pscustomobject]@{ ModuleName = 'Qt6Core.dll' })
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Refresh -Value {
        $this.RefreshCount++
        if ($this.ExitOnSecondRefresh -and $this.RefreshCount -eq 2) {
            $this.HasExited = $true
        }
    }
    return $fake
}
$rejected = [ordered]@{}
foreach ($kind in @('exited', 'identity', 'race', 'working', 'private', 'threads', 'handles', 'modules')) {
    $fake = New-FakeProcess
    switch ($kind) {
        'exited' { $fake.HasExited = $true }
        'identity' { $fake.Id = 43 }
        'race' { $fake.ExitOnSecondRefresh = $true }
        'working' { $fake.WorkingSet64 = 0 }
        'private' { $fake.PrivateMemorySize64 = 0 }
        'threads' { $fake.Threads = @() }
        'handles' { $fake.HandleCount = 0 }
        'modules' { $fake.Modules = @() }
    }
    $rejected[$kind] = $false
    try { Get-LiveProcessResourceSnapshot -Process $fake -ExpectedProcessId 42 | Out-Null }
    catch { $rejected[$kind] = $true }
}
$rejected | ConvertTo-Json -Compress
''',
        function_names=("Get-LiveProcessResourceSnapshot",),
    )
    assert len(report) == 8
    assert all(value is True for value in report.values())


def test_current_promotion_rejects_zero_or_nonlive_resource_evidence(
    tmp_path: Path,
) -> None:
    report = _run_cleanup_function(
        tmp_path,
        r'''
function New-ResourceReport {
    return [pscustomobject]@{
        sampledWhileAlive = $true; sampledProcessId = 42
        workingSetMiB = 64.0; privateMiB = 40.0; threads = 4; handles = 17
    }
}
$validAccepted = $true
try { Assert-LiveCompactResourceSnapshot (New-ResourceReport) }
catch { $validAccepted = $false }
$rejected = [ordered]@{}
foreach ($name in @('workingSetMiB', 'privateMiB', 'threads', 'handles', 'sampledProcessId')) {
    $probe = New-ResourceReport
    $probe.$name = 0
    $rejected[$name] = $false
    try { Assert-LiveCompactResourceSnapshot $probe }
    catch { $rejected[$name] = $true }
}
foreach ($kind in @('missing', 'not-live', 'text-live', 'nan', 'infinite')) {
    $probe = New-ResourceReport
    switch ($kind) {
        'missing' { $probe.PSObject.Properties.Remove('sampledWhileAlive') }
        'not-live' { $probe.sampledWhileAlive = $false }
        'text-live' { $probe.sampledWhileAlive = 'true' }
        'nan' { $probe.workingSetMiB = [double]::NaN }
        'infinite' { $probe.privateMiB = [double]::PositiveInfinity }
    }
    $rejected[$kind] = $false
    try { Assert-LiveCompactResourceSnapshot $probe }
    catch { $rejected[$kind] = $true }
}
[ordered]@{ validAccepted = $validAccepted; rejected = $rejected } |
    ConvertTo-Json -Compress -Depth 3
''',
        script=CURRENT_PROMOTION,
        function_names=(
            "Assert-JsonBoolean",
            "Get-RequiredJsonInteger",
            "Get-RequiredJsonNumber",
            "Assert-LiveCompactResourceSnapshot",
        ),
    )
    assert report["validAccepted"] is True
    assert len(report["rejected"]) == 10
    assert all(value is True for value in report["rejected"].values())


def test_report_uses_materialized_snapshot_only_after_probe_cleanup() -> None:
    source = RESOURCE_PROBE.read_text(encoding="utf-8")
    snapshot_call = source.index("$resourceSnapshot = Get-LiveProcessResourceSnapshot")
    assert snapshot_call < source.index("Stop-Process -Id $process.Id")
    final_report = source[source.index("$responding = [bool]$resourceSnapshot.responding") :]
    assert "$sample." not in final_report
    for name in ("workingSetBytes", "privateBytes", "threads", "handles", "responding"):
        assert f"$resourceSnapshot.{name}" in final_report
    wrapper = CURRENT_PROMOTION.read_text(encoding="utf-8")
    assert "Assert-LiveCompactResourceSnapshot $compactResource" in wrapper
