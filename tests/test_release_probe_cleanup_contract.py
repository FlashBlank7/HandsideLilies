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


def _run_cleanup_function(tmp_path: Path, body: str) -> dict[str, object]:
    source = RESOURCE_PROBE.read_text(encoding="utf-8")
    function = _powershell_function(
        source, "Remove-ExactDiagnosticDirectoryWithRetry"
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
