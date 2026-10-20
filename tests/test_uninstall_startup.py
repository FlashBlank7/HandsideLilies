"""Exact uninstall orchestration with synthetic process metadata and F files."""
from pathlib import Path

import pytest

from test_install_transaction import ROOT, inventory, quote, run_ps, ticket, tree  # noqa: F401
from test_install_quiescence import SETUP as PROCESS_SETUP
from test_uninstall_transaction import SETUP as REMOVAL_SETUP, removal  # noqa: F401


SETUP = REMOVAL_SETUP + PROCESS_SETUP + f". {quote(ROOT / 'scripts/uninstall_startup.ps1')}\n"
CALL = 'Invoke-LiliesCheckedUninstall @common -RecoveryRoot $source -ValidateRecovery $validate -ShortcutPlan $plan'


def test_checked_restore_finishes_before_program_and_shortcut_removal(removal):
    old = inventory(removal / 'programs/Lilies in the box')
    result = run_ps(removal, SETUP + f"""
$completed = @({CALL} -Checkpoint {{param($name,$paths); $events.Add($name)}})
Result @{{events=$events.ToArray(); mainKills=$main.KillCount; watchdogKills=$watchdog.KillCount; restorePath=$fixture.restore.Path; results=$completed.Count; state=$completed[0].state}}
""")
    events = result['events']
    assert events.index('validate') < events.index('kill:91') < events.index('watchdog-finished')
    assert events.index('watchdog-finished') < events.index('recover') < events.index('before-uninstall-program-move')
    assert events.index('after-uninstall-program-move') < events.index('before-shortcut-remove-primary')
    assert result['mainKills'] == 1 and result['watchdogKills'] == 0
    assert result['results'] == 1 and result['state'] == 'uninstalled'
    assert Path(result['restorePath']) == removal / 'release/LiliesInTheBox.exe'
    identity = ticket(removal)['transactionId']
    assert inventory(removal / 'programs' / ('.lilies-previous-' + identity)) == old


@pytest.mark.parametrize('fault', ['restore-error', 'restore-timeout', 'unknown-process', 'enumeration-error', 'candidate-changed'])
def test_failed_or_unknown_restore_never_removes_installed_files(removal, fault):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    change = {
        'restore-error': '$fixture.restoreExit=4',
        'restore-timeout': '$fixture.restoreTimeout=$true',
        'unknown-process': '$fixture.records[92].CommandLine=\'"\'+$installedExe+\'" --smoke\'',
        'enumeration-error': '$fixture.enumerationFailure=$true',
        'candidate-changed': "$validate={ $events.Add('validate'); if (@($events | Where-Object {$_ -ceq 'validate'}).Count -eq 2) {[IO.File]::WriteAllText((Join-Path $source 'unexpected-file'), 'changed')} }",
    }[fault]
    result = run_ps(removal, SETUP + change + f"""
$caught = ''
try {{ {CALL} | Out-Null }} catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; mainKills=$main.KillCount; watchdogKills=$watchdog.KillCount; events=$events.ToArray()}}
""")
    assert result['error']
    assert inventory(removal / 'programs') == old and inventory(removal / 'links') == links
    assert result['watchdogKills'] == 0
    if fault in {'unknown-process', 'enumeration-error', 'candidate-changed'}:
        assert result['mainKills'] == 0
        assert 'recover' not in result['events']


def test_uninstall_does_not_roll_files_back_beneath_late_process(removal):
    result = run_ps(removal, SETUP + f"""
$caught = ''
try {{ {CALL} -Checkpoint {{
    param($name,$paths)
    if ($name -ceq 'after-uninstall-program-move') {{
        $fixture.processes = @(FakeProcess 94 (Join-Path $paths.previous 'LiliesInTheBox.exe') '--compact')
        throw 'synthetic-late-process'
    }}
}} | Out-Null }} catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; kills=$fixture.processes[0].KillCount}}
""")
    assert 'recovery both failed' in result['error'] and result['kills'] == 0
    assert not (removal / 'programs/Lilies in the box').exists()
    assert len(list((removal / 'programs').glob('.lilies-previous-*'))) == 1


def test_post_restore_file_failure_restores_original_program_and_links(removal):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_ps(removal, SETUP + f"""
$caught = ''
try {{ {CALL} -Checkpoint {{param($name,$paths); if ($name -ceq 'after-shortcut-remove-pet') {{throw 'injected'}}}} | Out-Null }} catch {{$caught=$_.Exception.Message}}
Result @{{error=$caught; events=$events.ToArray()}}
""")
    assert result['error'] == 'injected' and 'recover' in result['events']
    assert inventory(removal / 'programs') == old and inventory(removal / 'links') == links


def test_recovery_package_overlap_is_rejected_before_process_actions(removal):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_ps(removal, SETUP + """
$caught = ''
try { Invoke-LiliesCheckedUninstall @common -RecoveryRoot $target -ValidateRecovery $validate -ShortcutPlan $plan | Out-Null }
catch {$caught=$_.Exception.Message}
Result @{error=$caught; events=$events.ToArray(); kills=$main.KillCount}
""")
    assert 'independent' in result['error'] and result['events'] == [] and result['kills'] == 0
    assert inventory(removal / 'programs') == old and inventory(removal / 'links') == links
