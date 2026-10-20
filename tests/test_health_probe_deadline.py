"""Bounded health subprocess tests; own hidden Python, synthetic F files only."""
import json
from pathlib import Path

import pytest

from test_install_transaction import ROOT, inventory, quote, run_ps, tree  # noqa: F401
from test_install_startup import SETUP, PYTHON


HEALTH = {'schemaVersion': 1, 'passed': True, 'processId': 91,
          'healthySamples': 13, 'stableMilliseconds': 3000, 'elapsedMilliseconds': 3100}


def health_script(tree, code):
    directory = tree / '验证 程序'
    directory.mkdir(exist_ok=True)
    path = directory / '健康检查.py'
    path.write_text(code, 'utf-8')
    return path


def invoke(tree, script, extra='', *, timeout=600):
    return run_ps(tree, SETUP + f"""
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
Start-LiliesOwnedChild $launch
$clock = [Diagnostics.Stopwatch]::StartNew()
$caught = ''
try {{ Assert-LiliesStartupHealth $launch {quote(PYTHON)} {quote(script)} -TimeoutMilliseconds {timeout} }}
catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; elapsed=$clock.ElapsedMilliseconds; passed=($null -ne $launch.health); quiet=$launch.healthProbeQuiescent{extra}}}
""")


def test_outer_deadline_rejects_late_success_instead_of_waiting_for_interpreter(tree):
    script = health_script(tree, 'import time\ntime.sleep(2)\nprint(' + repr(json.dumps(HEALTH)) + ')\n')
    result = invoke(tree, script)
    assert not result['passed'] and 'timed out' in result['error']
    assert result['quiet'] is True
    assert result['elapsed'] < 1800


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_output_is_bounded_before_parsing_or_logging(tree, stream):
    script = health_script(tree, f"import sys,time\nsys.{stream}.write('X' * 200000)\nsys.{stream}.flush()\ntime.sleep(5)\n")
    result = invoke(tree, script, timeout=2000)
    assert not result['passed'] and 'output limit' in result['error']
    assert result['quiet'] is True and 'XXX' not in result['error']


def test_success_uses_utf8_paths_and_no_shell_or_user_site_import(tree):
    script = health_script(tree, 'import sys,json\nassert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode\nprint(' + repr(json.dumps(HEALTH)) + ')\n')
    result = invoke(tree, script, timeout=2000)
    assert result['passed'] and result['quiet'] is True and not result['error']
    assert not list(tree.rglob('__pycache__'))


def test_nonzero_exit_cannot_forge_a_successful_health_object(tree):
    script = health_script(tree, 'import sys\nprint(' + repr(json.dumps(HEALTH)) + ')\nsys.exit(4)\n')
    result = invoke(tree, script, timeout=2000)
    assert not result['passed'] and result['quiet'] is True


@pytest.mark.parametrize('output', ['not-json', '[]', '{"passed":true}', '\ud800'])
def test_malformed_reply_never_becomes_startup_health(tree, output):
    if output == '\ud800':
        code = "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe')\n"
    else:
        code = 'print(' + repr(output) + ')\n'
    result = invoke(tree, health_script(tree, code), timeout=2000)
    assert not result['passed'] and result['error'] and result['quiet'] is True


def test_health_reply_cannot_smuggle_arbitrary_text_into_audit(tree):
    payload = {**HEALTH, 'privateText': 'fixture-body-must-not-enter-health-record'}
    result = invoke(tree, health_script(tree, 'print(' + repr(json.dumps(payload)) + ')\n'), extra='; health=$launch.health', timeout=2000)
    assert 'fixture-body' not in json.dumps(result.get('health'))


def test_windows_argument_quoting_round_trips_without_shell_interpretation(tree):
    script = health_script(tree, 'import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n')
    args = ['', 'space here', '引号"与末尾\\', 'C:\\trailing space\\', '& echo SHOULD_NOT_RUN', 'a\\\\"b']
    setup = f". {quote(ROOT / 'scripts/install_startup.ps1')}\n"
    result = run_ps(tree, setup + f"""
$owner = @{{healthProbeQuiescent=$true}}
$reply = Invoke-LiliesHealthProbe -Owner $owner -Python {quote(PYTHON)} -Arguments @('-I','-S','-B','-X','utf8',{quote(script)},{','.join(quote(arg) for arg in args)}) -TimeoutMilliseconds 2000
Result @{{output=$reply.output; quiet=$owner.healthProbeQuiescent; code=$reply.exitCode}}
""")
    assert json.loads(result['output']) == args
    assert result['quiet'] is True and result['code'] == 0


def test_venv_redirector_is_resolved_to_base_interpreter_without_starting_it(tree):
    env = tree / 'fake-venv'
    (env / 'Scripts').mkdir(parents=True)
    redirector = env / 'Scripts/python.exe'
    redirector.write_bytes(b'not-executable')
    (env / 'pyvenv.cfg').write_text('home = ' + str(PYTHON.parent) + '\n', 'utf-8')
    result = run_ps(tree, f". {quote(ROOT / 'scripts/install_startup.ps1')}\nResult @{{python=(Get-LiliesProbePython {quote(redirector)})}}")
    assert Path(result['python']) == PYTHON


def test_health_probe_cleanup_uncertainty_blocks_file_rollback(tree):
    before = inventory(tree / 'programs/Lilies in the box')
    result = run_ps(tree, SETUP + """
$t = Prepare
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
$caught = ''
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {
    Start-LiliesOwnedChild $launch
    $launch.healthProbeQuiescent = $false
    throw 'unknown-probe-exit'
} -BeforeRollback {Restore-LiliesAfterStartupFailure $launch} | Out-Null }
catch {$caught=$_.Exception.Message}
$paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{error=$caught; retained=(Test-LiliesOwnedTarget $paths); previous=$paths.previous; calls=$calls.ToArray()}
""")
    assert 'recovery both failed' in result['error'] and result['retained']
    assert inventory(Path(result['previous'])) == before
    assert 'start:--restore' not in result['calls']


def test_timeout_only_stops_probe_not_another_owned_python_process(tree):
    sleeping = health_script(tree, 'import time\ntime.sleep(20)\n')
    result = run_ps(tree, f". {quote(ROOT / 'scripts/install_startup.ps1')}\n" + f"""
$canary = New-LiliesLaunchState {quote(PYTHON)}
try {{
    Start-LiliesOwnedChild $canary @('-I','-S','-B', (ConvertTo-LiliesWindowsArgument {quote(sleeping)}))
    $owner = @{{healthProbeQuiescent=$true}}
    $caught = ''
    try {{ Invoke-LiliesHealthProbe -Owner $owner -Python {quote(PYTHON)} -Arguments @('-I','-S','-B',{quote(sleeping)}) -TimeoutMilliseconds 300 | Out-Null }}
    catch {{$caught=$_.Exception.Message}}
    $canary.process.Refresh()
    Result @{{error=$caught; canaryAlive=(-not $canary.process.HasExited); probeStopped=$owner.healthProbe.stopped; quiet=$owner.healthProbeQuiescent}}
}} finally {{
    Stop-LiliesOwnedChild $canary
    Close-LiliesLaunchHandle $canary
}}
""")
    assert 'timed out' in result['error'] and result['canaryAlive']
    assert result['probeStopped'] and result['quiet']


@pytest.mark.parametrize('config', ['home = \n', 'home = relative\n', 'home = C:\\missing-probe-python-7149\n', 'home = F:\\one\nhome = F:\\two\n'])
def test_invalid_venv_identity_is_rejected_without_starting_a_redirector(tree, config):
    env = tree / 'fake-venv'
    (env / 'Scripts').mkdir(parents=True)
    redirector = env / 'Scripts/python.exe'
    redirector.write_bytes(b'not-executable')
    (env / 'pyvenv.cfg').write_text(config, 'utf-8')
    result = run_ps(tree, f". {quote(ROOT / 'scripts/install_startup.ps1')}\n" + f"""
$caught = ''
try {{Get-LiliesProbePython {quote(redirector)} | Out-Null}} catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught}}
""")
    assert result['error']


def test_health_cannot_pass_after_application_identity_changes(tree):
    payload = json.dumps(HEALTH)
    result = run_ps(tree, SETUP + f"""
function Invoke-LiliesHealthProbe {{
    param($Owner,$Python,$Arguments,$TimeoutMilliseconds)
    $Owner.process.StartTime = $Owner.process.StartTime.AddSeconds(1)
    return [pscustomobject]@{{output={quote(payload)}; exitCode=0}}
}}
$launch = New-LiliesLaunchState (Join-Path $target 'LiliesInTheBox.exe')
Start-LiliesOwnedChild $launch
$caught = ''
try {{ Assert-LiliesStartupHealth $launch {quote(PYTHON)} (Join-Path $root 'unused.py') }} catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; health=$launch.health}}
""")
    assert 'identity' in result['error'] and result['health'] is None


def test_partial_success_json_followed_by_stall_cannot_commit(tree):
    script = health_script(tree, 'import sys,time\nprint(' + repr(json.dumps(HEALTH)) + ',flush=True)\ntime.sleep(20)\n')
    result = invoke(tree, script, timeout=500)
    assert not result['passed'] and 'timed out' in result['error'] and result['quiet']


def test_health_probe_wait_has_no_shell_or_unbounded_read_to_end():
    helper = (ROOT / 'scripts/install_probe.ps1').read_text('utf-8')
    assert 'UseShellExecute = $false' in helper and 'CreateNoWindow = $true' in helper
    assert 'ReadToEnd' not in helper and 'Stop-Process' not in helper and 'GetProcessById' not in helper
    assert '.WaitForExit()' not in helper
    assert '$timer.ElapsedMilliseconds -ge $TimeoutMilliseconds' in helper
    assert 'Stop-LiliesOwnedChild $probe' in helper


def test_one_element_array_cannot_masquerade_as_health_object(tree):
    result = invoke(tree, health_script(tree, 'print(' + repr(json.dumps([HEALTH])) + ')\n'), timeout=2000)
    assert not result['passed'] and result['quiet']


def test_unknown_probe_cannot_be_overwritten_by_a_second_attempt(tree):
    result = run_ps(tree, f". {quote(ROOT / 'scripts/install_startup.ps1')}\n" + f"""
$sentinel = @{{identity='held-original'}}
$owner = @{{healthProbeQuiescent=$false; healthProbe=$sentinel}}
$caught = ''
try {{ Invoke-LiliesHealthProbe -Owner $owner -Python {quote(PYTHON)} -Arguments @('-I','-S','-B','-c','print(1)') -TimeoutMilliseconds 500 | Out-Null }}
catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; same=[object]::ReferenceEquals($owner.healthProbe,$sentinel); quiet=$owner.healthProbeQuiescent}}
""")
    assert result['error'] and result['same'] and not result['quiet']


def test_failed_owned_probe_stop_leaves_explicit_unknown_state_and_held_handle(tree):
    script = health_script(tree, 'import time\ntime.sleep(20)\n')
    result = run_ps(tree, f". {quote(ROOT / 'scripts/install_startup.ps1')}\n" + f"""
function Stop-LiliesOwnedChild {{param($State); throw 'synthetic-stop-refused'}}
$owner = @{{healthProbeQuiescent=$true}}
$caught = ''
try {{
    try {{Invoke-LiliesHealthProbe -Owner $owner -Python {quote(PYTHON)} -Arguments @('-I','-S','-B',{quote(script)}) -TimeoutMilliseconds 300 | Out-Null}}
    catch {{$caught=$_.Exception.Message}}
    Result @{{error=$caught; quiet=$owner.healthProbeQuiescent; held=($owner.healthProbe.handle -ne [IntPtr]::Zero); alive=(-not $owner.healthProbe.process.HasExited)}}
}} finally {{
    # Test cleanup is only for the actual child we just created, never by PID.
    if ($null -ne $owner.healthProbe.process) {{
        if (-not $owner.healthProbe.process.HasExited) {{$owner.healthProbe.process.Kill(); $owner.healthProbe.process.WaitForExit(5000) | Out-Null}}
        $owner.healthProbe.process.Dispose()
    }}
}}
""")
    assert 'exit was not verified' in result['error']
    assert not result['quiet'] and result['held'] and result['alive']
