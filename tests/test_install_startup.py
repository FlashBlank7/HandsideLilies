"""Owned-child lifecycle and transaction ordering; no real Lilies or shell."""
import json
from pathlib import Path
import sys

import pytest

from test_install_transaction import inventory, quote, run_ps, tree  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/install_startup.ps1"
PYTHON = Path(sys._base_executable).resolve(strict=True)
SETUP = f". {quote(HELPER)}\n" + r"""
$calls = [Collections.Generic.List[string]]::new()
$fixture = @{ child = $null; restoreExitCode = 0 }
function FakeChild {
    $child = [pscustomobject]@{
        Handle = [IntPtr]123; Id = 91; Path = $target + '\LiliesInTheBox.exe'
        StartTime = [DateTime]'2026-10-04T00:00:00Z'; HasExited = $false
        ExitCode = 0; KillCount = 0; Disposed = $false
    }
    $child | Add-Member ScriptMethod Refresh {}
    $child | Add-Member ScriptMethod Kill { $this.KillCount++; $this.HasExited = $true }
    $child | Add-Member ScriptMethod WaitForExit { param($timeout); return $this.HasExited }
    $child | Add-Member ScriptMethod Dispose { $this.Disposed = $true }
    return $child
}
function Start-Process {
    param($FilePath,$WorkingDirectory,$WindowStyle,$PassThru,$ErrorAction,$ArgumentList)
    if ($WindowStyle -cne 'Hidden') { throw 'Test launcher must be hidden.' }
    $calls.Add('start:' + ($ArgumentList -join ' '))
    $fixture.child = FakeChild
    if ($ArgumentList -contains '--restore') { $fixture.child.HasExited = $true; $fixture.child.ExitCode = $fixture.restoreExitCode }
    return $fixture.child
}
function Wait-LiliesImageExit { param($Executable); $calls.Add('drain') }
"""


def test_health_failure_stops_owned_child_and_recovers_shell_before_files(tree):
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, SETUP + """
$t = Prepare
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
$caught = ''
try {
    Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {
        Start-LiliesOwnedChild $launch
        throw 'synthetic-health-failed'
    } -BeforeRollback {
        $paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
        if (-not (Test-LiliesOwnedTarget $paths)) { throw 'Program moved before quiescence.' }
        Restore-LiliesAfterStartupFailure $launch
        if (-not (Test-LiliesOwnedTarget $paths)) { throw 'Program moved during quiescence.' }
    } | Out-Null
} catch { $caught = $_.Exception.Message }
Close-LiliesLaunchHandle $launch
Result @{ error = $caught; calls = $calls.ToArray(); killed = $launch.process.KillCount; disposed = $launch.process.Disposed }
""")
    assert result == {"error": "synthetic-health-failed", "calls": ["start:", "drain", "start:--restore", "drain"], "killed": 1, "disposed": True}
    assert inventory(tree / "programs/Lilies in the box") == before


@pytest.mark.parametrize("failure", ["unknown-start", "identity", "drain", "restore-exit"])
def test_uncertain_quiescence_preserves_pending_program_and_backup(tree, failure):
    changes = {
        "unknown-start": "function Start-Process { throw 'uncertain-start' }",
        "identity": "", "drain": "function Wait-LiliesImageExit { throw 'other-image-still-running' }",
        "restore-exit": "$fixture.restoreExitCode = 9",
    }[failure]
    after_launch = "$launch.createdFileTime++" if failure == "identity" else ""
    result = run_ps(tree, SETUP + changes + f"""
$t = Prepare
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
$failed = $false
try {{ Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {{
    Start-LiliesOwnedChild $launch
    {after_launch}
    throw 'health-failed'
}} -BeforeRollback {{ Restore-LiliesAfterStartupFailure $launch }} | Out-Null }}
catch {{ $failed = $_.Exception.Message -like '*recovery both failed*' }}
$paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{{ failed=$failed; retained=(Test-LiliesOwnedTarget $paths); states=(Get-LiliesInstallStates $paths); previous=(Test-Path -LiteralPath $paths.previous) }}
""")
    assert result["failed"] and result["retained"] and result["previous"]
    assert "restored" not in result["states"] and "committed" not in result["states"]


def test_no_launch_is_no_child_no_restore_and_does_not_claim_health(tree):
    result = run_ps(tree, SETUP + """
$t = Prepare
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {} -BeforeRollback { Restore-LiliesAfterStartupFailure $launch } | Out-Null
Restore-LiliesAfterStartupFailure $launch
Result @{ calls=$calls.ToArray(); health=$launch.health; attempted=$launch.attempted }
""")
    assert result == {"calls": [], "health": None, "attempted": False}


@pytest.mark.parametrize("change", [None, "passed-int", "pid", "schema-bool", "duration", "missing-samples"])
def test_verifier_result_must_be_typed_bound_to_child_and_sustained(tree, change):
    payload = {"schemaVersion": 1, "passed": True, "processId": 91,
               "healthySamples": 13, "stableMilliseconds": 3000, "elapsedMilliseconds": 3100}
    if change == "passed-int": payload["passed"] = 1
    elif change == "pid": payload["processId"] = 92
    elif change == "schema-bool": payload["schemaVersion"] = True
    elif change == "duration": payload["stableMilliseconds"] = 1
    elif change == "missing-samples": del payload["healthySamples"]
    stub = tree / "fake_health.py"
    stub.write_text("print(" + repr(json.dumps(payload)) + ")\n", "utf-8")
    result = run_ps(tree, SETUP + f"""
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
Start-LiliesOwnedChild $launch
$passed=$false
try {{ Assert-LiliesStartupHealth $launch {quote(PYTHON)} {quote(stub)}; $passed=$true }} catch {{}}
Result @{{ passed=$passed; hasHealth=($null -ne $launch.health) }}
""")
    assert result == {"passed": change is None, "hasHealth": change is None}


def test_native_owned_child_termination_rejects_wrong_identity_and_keeps_exact_handle(tree):
    script = tree / "sleeping_fixture.py"
    script.write_text("import time\ntime.sleep(30)\n", "utf-8")
    result = run_ps(tree, f"""
. {quote(HELPER)}
$launch = New-LiliesLaunchState {quote(PYTHON)}
try {{
    Start-LiliesOwnedChild $launch @({quote(script)})
    $original = $launch.createdFileTime
    $launch.createdFileTime++
    $refused=$false
    try {{ Stop-LiliesOwnedChild $launch }} catch {{ $refused=$true }}
    $launch.process.Refresh()
    $wasAlive = -not $launch.process.HasExited
    $launch.createdFileTime = $original
    Stop-LiliesOwnedChild $launch
    Stop-LiliesOwnedChild $launch
    Result @{{ refused=$refused; preserved=$wasAlive; stopped=$launch.process.HasExited }}
}} finally {{
    # Cleanup only the exact synthetic child created in this fixture.
    if ($null -ne $launch.process -and -not $launch.process.HasExited) {{ $launch.process.Kill(); $launch.process.WaitForExit(5000) | Out-Null }}
    Close-LiliesLaunchHandle $launch
}}
""")
    assert result == {"refused": True, "preserved": True, "stopped": True}


def test_installer_launch_health_and_shell_recovery_are_inside_transaction_gate():
    source = (ROOT / "scripts/install_windows.ps1").read_text("utf-8")
    call = source.split("$launch = New-LiliesLaunchState", 1)[1]
    assert "-AfterActivate" in call and "-BeforeRollback" in call
    assert call.index("Start-LiliesOwnedChild") < call.index("Assert-LiliesStartupHealth") < call.index("startup-health.json")
    assert "Restore-LiliesAfterStartupFailure $launch" in call
    assert "Close-LiliesLaunchHandle $launch" in call
    assert source.index("throw 'v0.3.57 installation is not released:") < source.index("$launch = New-LiliesLaunchState")


def test_exit_race_during_owned_kill_is_already_quiescent(tree):
    result = run_ps(tree, SETUP + """
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
Start-LiliesOwnedChild $launch
$launch.process | Add-Member -Force ScriptMethod Kill { $this.HasExited=$true; throw 'exited-before-kill' }
$passed=$false
try { Stop-LiliesOwnedChild $launch; $passed=$launch.stopped } catch {}
Result @{passed=$passed}
""")
    assert result["passed"]


def test_failed_process_enumeration_is_not_evidence_of_no_running_image(tree):
    result = run_ps(tree, f". {quote(HELPER)}\n" + """
function Get-Process { param($Name,$ErrorAction); Write-Error 'synthetic-enumeration-denied' -ErrorAction $ErrorAction }
$refused=$false
try { Wait-LiliesImageExit (Join-Path $target 'LiliesInTheBox.exe') } catch { $refused=$true }
Result @{refused=$refused}
""")
    assert result["refused"]


def test_no_such_image_is_a_valid_empty_process_set(tree):
    run_ps(tree, f". {quote(HELPER)}\n" + """
Wait-LiliesImageExit (Join-Path $target 'lilies-fixture-no-such-image-7182.exe')
Result @{passed=$true}
""")
