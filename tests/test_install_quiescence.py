"""Pre-upgrade ownership and file gates; synthetic F-drive processes only."""
from pathlib import Path
import uuid

import pytest

from test_install_transaction import inventory, quote, run_ps, tree  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/install_startup.ps1"
SETUP = f". {quote(HELPER)}\n" + r'''
$events = [Collections.Generic.List[string]]::new()
$fixture = @{ processes=@(); records=@{}; restore=$null; restoreExit=0; restoreTimeout=$false; enumerationFailure=$false }
function FakeProcess([int]$Number, [string]$Executable, [string]$Arguments) {
    $child=[pscustomobject]@{
        Id=$Number; Handle=[IntPtr]($Number+100); Path=$Executable
        StartTime=[DateTime]'2026-10-04T00:00:00.1234567Z'
        HasExited=$false; ExitCode=0; KillCount=0; Disposed=$false
    }
    $child | Add-Member ScriptMethod Refresh {}
    $child | Add-Member ScriptMethod Kill {
        $events.Add('kill:'+ $this.Id); $this.KillCount++; $this.HasExited=$true
    }
    $child | Add-Member ScriptMethod WaitForExit {param($timeout); return $this.HasExited}
    $child | Add-Member ScriptMethod Dispose {$this.Disposed=$true}
    $fixture.records[$Number]=[pscustomobject]@{
        ProcessId=$Number; ExecutablePath=$Executable; CreationDate=$child.StartTime.AddTicks(-7)
        CommandLine=('"'+$Executable+'" '+$Arguments)
    }
    return $child
}
function Get-Process {
    param($Name,$ErrorAction)
    if ($fixture.enumerationFailure) {throw 'synthetic enumeration denied'}
    return $fixture.processes
}
function Get-CimInstance {
    param($ClassName,$Filter,$Property,$OperationTimeoutSec,$ErrorAction)
    if ($ClassName -ne 'Win32_Process' -or $OperationTimeoutSec -ne 5) {throw 'unsafe metadata request'}
    $number=[int]($Filter -replace 'ProcessId = ','')
    $events.Add('role:'+ $number)
    return $fixture.records[$number]
}
function Wait-LiliesImageExit {
    param($Executable)
    $events.Add('drain')
    foreach ($child in $fixture.processes) {
        if ($child.Path -ieq $Executable -and -not $child.HasExited) {
            if ($fixture.records[$child.Id].CommandLine -notmatch ' --watchdog ') {throw 'main remains during drain'}
            if ($child.KillCount -ne 0) {throw 'watchdog was killed'}
            $events.Add('watchdog-finished'); $child.HasExited=$true
        }
    }
}
function Start-Process {
    param($FilePath,$WorkingDirectory,$WindowStyle,$PassThru,$ErrorAction,$ArgumentList)
    if ($WindowStyle -cne 'Hidden' -or ($ArgumentList -join ' ') -cne '--restore') {throw 'unsafe recovery request'}
    $events.Add('recover')
    $fixture.restore=FakeProcess 99 $FilePath '--restore'
    $fixture.restore.HasExited = -not $fixture.restoreTimeout
    $fixture.restore.ExitCode=$fixture.restoreExit
    return $fixture.restore
}
$installedExe=Join-Path $target 'LiliesInTheBox.exe'
$main=FakeProcess 91 $installedExe '--compact'
$watchdog=FakeProcess 92 $installedExe '--watchdog 91 "F:\fixture\backup.json" "F:\fixture\clean"'
$fixture.processes=@($main,$watchdog)
$validate = {$events.Add('validate')}
'''


def test_pre_activation_refusal_keeps_prepared_stage_and_old_tree(tree):
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$t = Prepare
$caught = ''
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeActivate {
    throw 'synthetic-process-state-unknown'
} -BeforeRollback { throw 'must-not-rollback-unknown-process' } | Out-Null }
catch { $caught = $_.Exception.Message }
$paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{error=$caught; states=(Get-LiliesInstallStates $paths); stage=(Test-Path -LiteralPath $paths.stage)}
""")
    assert result == {"error": "synthetic-process-state-unknown", "states": ["prepared"], "stage": True}
    assert inventory(tree / "programs/Lilies in the box") == before


def test_image_drain_disposes_unrelated_enumerated_handle(tree):
    result = run_ps(tree, f". {quote(HELPER)}\n" + """
$child = [pscustomobject]@{Handle=[IntPtr]123; Id=91; Path=(Join-Path $root 'other/LiliesInTheBox.exe'); HasExited=$false; Disposed=$false}
$child | Add-Member ScriptMethod Refresh {}
$child | Add-Member ScriptMethod Dispose {$this.Disposed=$true}
function Get-Process { param($Name,$ErrorAction); return $child }
Wait-LiliesImageExit (Join-Path $target 'LiliesInTheBox.exe')
Result @{disposed=$child.Disposed}
""")
    assert result["disposed"]


def test_explicit_recovery_checks_quiescence_inside_lock_before_mutation(tree):
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$t=Prepare
$caught=''
try { Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {
    throw 'synthetic-running-image'
} | Out-Null } catch {$caught=$_.Exception.Message}
$paths=Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{error=$caught; states=(Get-LiliesInstallStates $paths); stage=(Test-Path -LiteralPath $paths.stage)}
""")
    assert result == {"error": "synthetic-running-image", "states": ["prepared"], "stage": True}
    assert inventory(tree / "programs/Lilies in the box") == before


def test_shutdown_validates_all_roles_leaves_watchdog_then_checks_staged_restore(tree):
    result = run_ps(tree, SETUP + """
Stop-LiliesForUpgrade $installedExe (Join-Path $source 'LiliesInTheBox.exe') $validate
Result @{events=$events.ToArray(); mainKills=$main.KillCount; watchdogKills=$watchdog.KillCount;
    disposed=($main.Disposed -and $watchdog.Disposed -and $fixture.restore.Disposed)}
""")
    assert result == {
        "events": ["validate", "role:91", "role:92", "kill:91", "drain", "watchdog-finished", "validate", "recover"],
        "mainKills": 1, "watchdogKills": 0, "disposed": True,
    }


@pytest.mark.parametrize("change", [
    "cim-denied", "cim-pid", "cim-time", "cim-path", "cim-missing", "cim-duplicate",
    "no-command", "wrong-command-image", "unknown-role", "invalid-command-boundary",
    "held-handle", "held-time", "held-path", "held-pid", "enumeration-denied",
])
def test_all_processes_verified_before_any_stop_and_failure_never_moves_files(tree, change):
    changes = {
        "cim-denied": "function Get-CimInstance { throw 'synthetic cim denied' }",
        "cim-pid": "$fixture.records[92].ProcessId=93",
        "cim-time": "$fixture.records[92].CreationDate=$watchdog.StartTime.AddSeconds(1)",
        "cim-path": "$fixture.records[92].ExecutablePath=Join-Path $source 'LiliesInTheBox.exe'",
        "cim-missing": "$fixture.records.Remove(92)",
        "cim-duplicate": "$fixture.records[92]=@($fixture.records[92],$fixture.records[92])",
        "no-command": "$fixture.records[92].CommandLine=$null",
        "wrong-command-image": "$fixture.records[92].CommandLine='\"F:\\somewhere\\LiliesInTheBox.exe\" --compact'",
        "unknown-role": "$fixture.records[92].CommandLine='\"'+$installedExe+'\" --smoke'",
        "invalid-command-boundary": "$fixture.records[92].CommandLine='\"'+$installedExe+'\"--compact'",
        "held-handle": "$fixture.records[92] | Add-Member -Force ScriptProperty CommandLine {$watchdog.Handle=[IntPtr]999; return '\"'+$installedExe+'\" --compact'}",
        "held-time": "$fixture.records[92] | Add-Member -Force ScriptProperty CommandLine {$watchdog.StartTime=$watchdog.StartTime.AddSeconds(1); return '\"'+$installedExe+'\" --compact'}",
        "held-path": "$fixture.records[92] | Add-Member -Force ScriptProperty CommandLine {$watchdog.Path=Join-Path $source 'LiliesInTheBox.exe'; return '\"'+$installedExe+'\" --compact'}",
        "held-pid": "$fixture.records[92] | Add-Member -Force ScriptProperty CommandLine {$watchdog.Id=300; return '\"'+$installedExe+'\" --compact'}",
        "enumeration-denied": "$fixture.enumerationFailure=$true",
    }[change]
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, SETUP + changes + """
$t=Prepare
$caught=$false
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeActivate {
    Stop-LiliesForUpgrade $installedExe (Join-Path $t.stage 'LiliesInTheBox.exe') $validate
} | Out-Null } catch {$caught=$true}
$paths=Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{refused=$caught; mainKills=$main.KillCount; watchdogKills=$watchdog.KillCount;
    recovered=($null -ne $fixture.restore); states=(Get-LiliesInstallStates $paths); stage=(Test-Path -LiteralPath $paths.stage)}
""")
    assert result == {"refused": True, "mainKills": 0, "watchdogKills": 0,
                      "recovered": False, "states": ["prepared"], "stage": True}
    assert inventory(tree / "programs/Lilies in the box") == before


@pytest.mark.parametrize("failure", ["exit", "timeout", "unknown-start", "drain", "late-process", "payload"])
def test_failed_recovery_or_late_process_keeps_prepared_tree_for_explicit_recovery(tree, failure):
    change = {
        "exit": "$fixture.restoreExit=4",
        "timeout": "$fixture.restoreTimeout=$true",
        "unknown-start": "function Start-Process {throw 'synthetic uncertain start'}",
        "drain": "function Wait-LiliesImageExit {throw 'synthetic watcher remains'}",
        "late-process": "$fixture.records[99]=$null; function Assert-LiliesImagesQuiescent {param($Executables); if ($null -ne $fixture.restore) {throw 'synthetic new process appeared'}}",
        "payload": "$validate={throw 'synthetic payload changed'}",
    }[failure]
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, SETUP + change + """
$t=Prepare
$caught=$false
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeActivate {
    Stop-LiliesForUpgrade $installedExe (Join-Path $t.stage 'LiliesInTheBox.exe') $validate
} | Out-Null } catch {$caught=$true}
$paths=Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{refused=$caught; states=(Get-LiliesInstallStates $paths); stage=(Test-Path -LiteralPath $paths.stage);
    watchdogKills=$watchdog.KillCount; recoveryStopped=($null -eq $fixture.restore -or $fixture.restore.HasExited)}
""")
    assert result == {"refused": True, "states": ["prepared"], "stage": True,
                      "watchdogKills": 0, "recoveryStopped": True}
    assert inventory(tree / "programs/Lilies in the box") == before


def test_no_launch_refuses_before_role_query_or_stop(tree):
    result = run_ps(tree, SETUP + """
$caught=$false
try {Stop-LiliesForUpgrade $installedExe (Join-Path $source 'LiliesInTheBox.exe') $validate -NoLaunch} catch {$caught=$true}
Result @{refused=$caught; events=$events.ToArray(); kills=$main.KillCount}
""")
    assert result == {"refused": True, "events": ["validate"], "kills": 0}


@pytest.mark.parametrize("arguments", ["", "--compact", "--visual", "--login-shell", "--restore", '--watchdog 91 "F:\\backup" "F:\\clean"'])
def test_recognized_command_roles_and_cim_microsecond_rounding(tree, arguments):
    result = run_ps(tree, SETUP + f"""
$main=FakeProcess 91 $installedExe {quote(arguments)}
$fixture.processes=@($main)
$leases=@(Get-LiliesImageLeases $installedExe)
try {{Result @{{role=(Get-LiliesExistingProcessRole $leases[0]); count=$leases.Count}}}}
finally {{foreach ($lease in $leases) {{Close-LiliesLaunchHandle $lease}}}}
""")
    assert result == {"role": "passive-recovery" if arguments.startswith(("--restore", "--watchdog")) else "main", "count": 1}


def test_readonly_gate_and_drain_reject_running_process_without_stopping_it(tree):
    result = run_ps(tree, SETUP + f"""
# Restore the real bounded wait helper, not the fixture's immediate drain.
. {quote(HELPER)}
$refused=0
try {{Assert-LiliesImagesQuiescent @($installedExe)}} catch {{$refused++}}
try {{Wait-LiliesImageExit $installedExe -TimeoutSeconds 0}} catch {{$refused++}}
Result @{{refused=$refused; kills=($main.KillCount+$watchdog.KillCount); disposed=($main.Disposed -and $watchdog.Disposed)}}
""")
    assert result == {"refused": 2, "kills": 0, "disposed": True}


def test_unknown_metadata_disposes_all_enumerated_handles_without_stop(tree):
    result = run_ps(tree, SETUP + """
$watchdog.Path=$null
$caught=$false
try {Get-LiliesImageLeases $installedExe | Out-Null} catch {$caught=$true}
Result @{refused=$caught; disposed=($main.Disposed -and $watchdog.Disposed); kills=$main.KillCount}
""")
    assert result == {"refused": True, "disposed": True, "kills": 0}


def test_main_exiting_during_role_query_is_not_replaced_by_its_pid(tree):
    result = run_ps(tree, SETUP + """
function Get-CimInstance {
    param($ClassName,$Filter,$Property,$OperationTimeoutSec,$ErrorAction)
    $number=[int]($Filter -replace 'ProcessId = ','')
    if ($number -eq 91) {$main.HasExited=$true; return $null}
    return $fixture.records[$number]
}
Stop-LiliesForUpgrade $installedExe (Join-Path $source 'LiliesInTheBox.exe') $validate
Result @{kills=$main.KillCount; watchdogKills=$watchdog.KillCount; recovered=($null -ne $fixture.restore)}
""")
    assert result == {"kills": 0, "watchdogKills": 0, "recovered": True}


def test_fresh_install_does_not_restore_a_shell_it_never_replaced(tree):
    result = run_ps(tree, SETUP + """
$fixture.processes=@()
Stop-LiliesForUpgrade (Join-Path $base 'Fresh/LiliesInTheBox.exe') (Join-Path $source 'LiliesInTheBox.exe') $validate -NoLaunch
Result @{events=$events.ToArray(); recovered=($null -ne $fixture.restore)}
""")
    assert result == {"events": ["validate", "drain"], "recovered": False}


def test_final_payload_and_process_gate_precede_real_fixture_directory_moves(tree):
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, SETUP + """
$t=Prepare
$installed=Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeActivate {
    param($paths)
    Stop-LiliesForUpgrade $installedExe (Join-Path $paths.stage 'LiliesInTheBox.exe') -ValidateRecovery {
        $manifest=Read-LiliesInstallManifest $paths
        Assert-LiliesTreeMatches $paths.stage $manifest.newSnapshot
        $events.Add('validate')
    }
} -Checkpoint {
    param($name,$paths)
    if ($name -eq 'before-old-move') {
        if (-not $main.HasExited -or -not $watchdog.HasExited -or
            $null -eq $fixture.restore -or -not $fixture.restore.HasExited -or $fixture.restore.ExitCode -ne 0) {
            throw 'files moved before recovery completed'
        }
        $events.Add('move')
    }
}
Result @{events=$events.ToArray(); previous=$installed.previous; state=$installed.state}
""")
    assert result["events"] == ["validate", "role:91", "role:92", "kill:91", "drain", "watchdog-finished", "validate", "recover", "move"]
    assert result["state"] == "committed"
    assert inventory(Path(result["previous"])) == before


def test_payload_changed_by_guard_is_not_activated_or_rolled_back_under_unknown_process(tree):
    before = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$t=Prepare
$caught=$false
try {Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -BeforeActivate {
    param($paths)
    [IO.File]::WriteAllText((Join-Path $paths.stage 'new-unknown-file'), 'synthetic late change')
} | Out-Null} catch {$caught=$true}
$paths=Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{refused=$caught; states=(Get-LiliesInstallStates $paths); retained=(Test-Path -LiteralPath (Join-Path $paths.stage 'new-unknown-file'))}
""")
    assert result == {"refused": True, "states": ["prepared"], "retained": True}
    assert inventory(tree / "programs/Lilies in the box") == before


@pytest.mark.parametrize("arguments,role", [
    ("--compact", "main"),
    ('--watchdog 91 "F:\\fixture\\backup.json" "F:\\fixture\\clean"', "passive-recovery"),
], ids=["main", "watchdog"])
def test_native_role_metadata_for_one_owned_hidden_fixture_child(tree, record_property, arguments, role):
    # A unique, windowless fixture executable, NOT an installed Lilies image.
    # Compiler temporary files and output are confined by run_ps to this F tree.
    executable = tree / ("莉莉丝 role fixture " + uuid.uuid4().hex + ".exe")
    result = run_ps(tree, f"""
. {quote(HELPER)}
Add-Type -TypeDefinition 'public class Probe {{ public static int Main(string[] args) {{ System.Threading.Thread.Sleep(30000); return 0; }} }}' -OutputAssembly {quote(executable)} -OutputType WindowsApplication
$child=New-LiliesLaunchState {quote(executable)}
$leases=@()
try {{
    Start-LiliesOwnedChild $child @({quote(arguments)})
    $leases=@(Get-LiliesImageLeases {quote(executable)})
    if ($leases.Count -ne 1 -or $leases[0].processId -ne $child.processId) {{throw 'Owned fixture identity not found'}}
    # Query only the exact process we just created. Do not enumerate users'
    # command lines. An actual access denial is reported as unavailable, not
    # silently promoted into a successful role verification.
    $unavailable=''
    try {{
        Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId = ' + $child.processId) -Property ProcessId,ExecutablePath,CreationDate,CommandLine -OperationTimeoutSec 5 -ErrorAction Stop | Out-Null
    }} catch {{
        if ($_.Exception -is [Microsoft.Management.Infrastructure.CimException] -and
            $_.Exception.NativeErrorCode -eq [Microsoft.Management.Infrastructure.NativeErrorCode]::AccessDenied) {{
            $unavailable='CIM access denied for owned fixture'
        }} else {{throw}}
    }}
    if ($unavailable) {{
        $refused=$false
        try {{Get-LiliesExistingProcessRole $leases[0] | Out-Null}} catch {{$refused=$_.Exception.Message -like 'Cannot identify the existing Lilies role*'}}
        if (-not $refused) {{throw 'CIM denial did not fail closed'}}
        Result @{{status='unavailable'; reason=$unavailable; refused=$true}}
    }} else {{
        Result @{{status='verified'; role=(Get-LiliesExistingProcessRole $leases[0])}}
    }}
}} finally {{
    # Never look up or terminate by PID here; cleanup our exact child only.
    try {{Stop-LiliesOwnedChild $child}} finally {{
        foreach ($lease in $leases) {{Close-LiliesLaunchHandle $lease}}
        Close-LiliesLaunchHandle $child
    }}
}}
""")
    record_property("native_owned_cim", result["status"])
    if result["status"] == "unavailable":
        assert result == {"status": "unavailable", "reason": "CIM access denied for owned fixture", "refused": True}
        pytest.skip(result["reason"] + "; fail-closed refusal verified, successful role lookup not proved")
    assert result == {"status": "verified", "role": role}
