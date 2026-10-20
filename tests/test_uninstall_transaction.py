"""Recoverable uninstall in F-drive fixtures, never a live installation.

Program bytes are deliberately not executable. Process/restoration guards are
injected; only test-owned hidden PowerShell children terminate themselves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_install_transaction import ROOT, inventory, quote, run_ps, ticket, tree  # noqa: F401
from test_install_shortcuts import ROLES


LOAD = f". {quote(ROOT / 'scripts/uninstall_transaction.ps1')}\n"
SETUP = LOAD + """
$linkRoot = Join-Path $root 'links'
$entries = @('primary','desktop','pet','startup') | ForEach-Object {
    $path = Get-LiliesShortcutPath $linkRoot $_
    [pscustomobject]@{role=$_; before=$null; after=(ConvertTo-LiliesShortcutSnapshot (Read-LiliesShortcutBytes $path))}
}
$plan = [pscustomobject]@{schemaVersion=1; root=$linkRoot; entries=@($entries)}
function PrepareRemoval { New-LiliesUninstallTransaction @common -ShortcutPlan $plan }
"""


@pytest.fixture
def removal(tree):
    (tree / 'links/Startup').mkdir(parents=True)
    for role, path in ROLES.items():
        (tree / 'links' / path).write_bytes(('original-' + role + '-莉莉丝').encode())
    (tree / 'links/another-app.lnk').write_bytes(b'not-owned')
    return tree


def run_removal(path, body, **kwargs):
    return run_ps(path, SETUP + body, **kwargs)


def test_commit_retains_program_removes_only_named_links_and_preserves_data(removal):
    old = inventory(removal / 'programs/Lilies in the box')
    links = inventory(removal / 'links')
    data = inventory(removal / 'private-data')
    prepared = run_removal(removal, 'Result (PrepareRemoval)')
    assert inventory(removal / 'links') == links
    assert inventory(removal / 'programs/Lilies in the box') == old
    manifest = ticket(removal)
    assert manifest['schemaVersion'] == 3 and manifest['operation'] == 'uninstall'
    result = run_ps(removal, LOAD + f"""
$id = {quote(prepared['transactionId'])}
Complete-LiliesUninstallTransaction @common -TransactionId $id -BeforeRemove {{}} -BeforeRollback {{}} | Out-Null
Result (Complete-LiliesUninstallTransaction @common -TransactionId $id -BeforeRemove {{throw 'must-not-replay'}} -BeforeRollback {{throw 'must-not-rollback'}})
""")
    assert result['state'] == 'uninstalled' and result['alreadyCommitted']
    assert not (removal / 'programs/Lilies in the box').exists()
    assert inventory(Path(result['retained'])) == old
    assert inventory(removal / 'links') == {'Startup': None, 'another-app.lnk': b'not-owned'}
    assert inventory(removal / 'private-data') == data
    retained = [path.read_bytes() for path in (removal / 'journals').glob('*/*/s-*.bin')]
    assert all(links[path] in retained for path in ROLES.values())


def test_committed_recovery_is_explicit_repeatable_and_restores_exact_links(removal):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_removal(removal, """
$t = PrepareRemoval
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} | Out-Null
$errorText = ''
try { Restore-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {} | Out-Null }
catch { $errorText = $_.Exception.Message }
Restore-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {} -RollbackCommitted | Out-Null
Result @{error=$errorText; repeat=(Restore-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {} -RollbackCommitted)}
""")
    assert 'explicit rollback' in result['error'] and result['repeat']['alreadyRestored']
    assert inventory(removal / 'programs') == old
    assert inventory(removal / 'links') == links


@pytest.mark.parametrize('point', [
    'before-uninstall-program-move', 'after-uninstall-program-move',
    'before-shortcut-remove-primary', 'after-shortcut-remove-primary',
    'after-shortcut-remove-pet', 'after-shortcut-restore-startup', 'before-uninstall-commit',
])
def test_exception_rolls_back_program_and_all_links(removal, point):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_removal(removal, f"""
$t = PrepareRemoval
$caught = ''
try {{ Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {{}} -BeforeRollback {{}} -Checkpoint {{
    param($name,$paths); if ($name -ceq {quote(point)}) {{ throw 'injected-remove-failure' }}
}} | Out-Null }} catch {{ $caught = $_.Exception.Message }}
Result @{{error=$caught; restored=(Restore-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {{}})}}
""")
    assert result['error'] == 'injected-remove-failure'
    assert result['restored']['alreadyRestored']
    assert inventory(removal / 'programs') == old
    assert inventory(removal / 'links') == links


@pytest.mark.parametrize('point', [
    'before-uninstall-program-move', 'after-uninstall-program-move',
    'after-shortcut-remove-primary', 'after-shortcut-restore-pet', 'before-uninstall-commit',
])
def test_real_abrupt_exit_then_fresh_process_restores(removal, point):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    run_removal(removal, f"""
$t = PrepareRemoval
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {{}} -BeforeRollback {{}} -Checkpoint {{
    param($name,$paths); if ($name -ceq {quote(point)}) {{ [Environment]::Exit(87) }}
}}
""", code=87)
    identity = ticket(removal)['transactionId']
    result = run_ps(removal, LOAD + f"Result (Restore-LiliesUninstallTransaction @common -TransactionId {quote(identity)} -BeforeRestore {{}})")
    assert result['state'] == 'restored'
    assert inventory(removal / 'programs') == old
    assert inventory(removal / 'links') == links
    assert run_removal(removal, 'Result (PrepareRemoval)')['state'] == 'prepared'


@pytest.mark.parametrize('point', ['after-uninstall-program-restore', 'after-shortcut-swap-primary', 'after-uninstall-link-restore-startup'])
def test_recovery_itself_can_be_interrupted_and_resumed(removal, point):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    prepared = run_removal(removal, """
$t = PrepareRemoval
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} | Out-Null
Result $t
""")
    identity = quote(prepared['transactionId'])
    run_ps(removal, LOAD + f"""
Restore-LiliesUninstallTransaction @common -TransactionId {identity} -BeforeRestore {{}} -RollbackCommitted -Checkpoint {{
    param($name,$paths); if ($name -ceq {quote(point)}) {{ [Environment]::Exit(88) }}
}}
""", code=88)
    run_ps(removal, 'Prepare', code=1)  # interrupted recovery blocks a new install
    run_ps(removal, LOAD + f'Restore-LiliesUninstallTransaction @common -TransactionId {identity} -BeforeRestore {{}} -RollbackCommitted | Out-Null')
    assert inventory(removal / 'programs') == old
    assert inventory(removal / 'links') == links


def test_unknown_restoration_result_never_enters_file_rollback(removal):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_removal(removal, """
$t = PrepareRemoval
$state = @{rollback=$false; error=''}
try { Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {throw 'unknown-shell-state'} -BeforeRollback {$state.rollback=$true} | Out-Null }
catch {$state.error=$_.Exception.Message}
$paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
$state.states = Get-LiliesInstallStates $paths
Result $state
""")
    assert result == {'error': 'unknown-shell-state', 'rollback': False, 'states': ['prepared']}
    assert inventory(removal / 'programs') == old and inventory(removal / 'links') == links


def test_unknown_process_at_failure_preserves_partial_state_until_explicit_recovery(removal):
    old, links = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_removal(removal, """
$t = PrepareRemoval
$caught = ''
try { Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {throw 'still-running'} -Checkpoint {
    param($name,$paths); if ($name -eq 'after-shortcut-remove-primary') {throw 'partial-removal'}
} | Out-Null } catch {$caught=$_.Exception.Message}
Result @{id=$t.transactionId; error=$caught}
""")
    assert 'recovery both failed' in result['error']
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    run_ps(removal, LOAD + f"Restore-LiliesUninstallTransaction @common -TransactionId {quote(result['id'])} -BeforeRestore {{throw 'still-running'}}", code=1)
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before
    run_ps(removal, LOAD + f"Restore-LiliesUninstallTransaction @common -TransactionId {quote(result['id'])} -BeforeRestore {{}} | Out-Null")
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == (old, links)


@pytest.mark.parametrize('phase', ['prepared', 'committed'])
@pytest.mark.parametrize('change', ['program', 'shortcut', 'extra-target', 'reserved-stage'])
def test_external_changes_refuse_without_overwriting(removal, phase, change):
    prepared = run_removal(removal, '$t = PrepareRemoval\n' + (
        'Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} | Out-Null\n'
        if phase == 'committed' else '') + 'Result $t')
    identity = prepared['transactionId']
    target = removal / 'programs/Lilies in the box'
    if change == 'program':
        directory = Path(prepared['retained']) if phase == 'committed' else target
        (directory / 'old.dll').write_bytes(b'external-change')
    elif change == 'shortcut':
        (removal / 'links' / ROLES['primary']).write_bytes(b'user-customized')
    elif change == 'extra-target':
        directory = target if phase == 'committed' else Path(prepared['retained'])
        directory.mkdir()
        (directory / 'external.txt').write_bytes(b'other-installation')
    else:
        (removal / 'programs' / ('.lilies-stage-' + identity)).mkdir()
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    action = 'Restore-LiliesUninstallTransaction' if phase == 'committed' else 'Complete-LiliesUninstallTransaction'
    guard = '-BeforeRestore {} -RollbackCommitted' if phase == 'committed' else '-BeforeRemove {} -BeforeRollback {}'
    run_ps(removal, LOAD + f'{action} @common -TransactionId {quote(identity)} {guard}', code=1)
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before


@pytest.mark.parametrize('operation', ['Complete-LiliesInstallTransaction', 'Restore-LiliesInstallTransaction'])
def test_install_entrypoints_cannot_replay_uninstall_plan(removal, operation):
    prepared = run_removal(removal, 'Result (PrepareRemoval)')
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    run_ps(removal, f'{operation} @common -TransactionId {quote(prepared["transactionId"])}', code=1)
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before


def test_prepared_transactions_block_both_new_install_and_uninstall(removal):
    run_removal(removal, 'PrepareRemoval | Out-Null')
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    run_ps(removal, 'Prepare', code=1)
    run_removal(removal, 'PrepareRemoval', code=1)
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before


@pytest.mark.parametrize('name', ['stage', 'failed'])
def test_guard_rechecks_all_reserved_paths_before_moving_program(removal, name):
    old, links = inventory(removal / 'programs/Lilies in the box'), inventory(removal / 'links')
    result = run_removal(removal, f"""
$t = PrepareRemoval
$state = @{{rollback=$false; error=''}}
try {{ Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {{param($paths); [IO.Directory]::CreateDirectory($paths.{name}) | Out-Null}} -BeforeRollback {{$state.rollback=$true}} | Out-Null }}
catch {{$state.error=$_.Exception.Message}}
Result $state
""")
    assert 'Unexpected tree' in result['error'] and not result['rollback']
    assert inventory(removal / 'programs/Lilies in the box') == old
    assert inventory(removal / 'links') == links


def test_real_com_ownership_keeps_customized_arguments_and_other_targets(tree):
    result = run_ps(tree, LOAD + """
$linkRoot = Join-Path $root 'links'
[IO.Directory]::CreateDirectory((Join-Path $linkRoot 'Startup')) | Out-Null
$payload = New-LiliesShortcutPayloads $target (Join-Path $root 'link-staging') $true
foreach ($role in @('primary','desktop','pet','startup')) {
    [IO.File]::WriteAllBytes((Get-LiliesShortcutPath $linkRoot $role), $payload[$role])
}
$shell = New-Object -ComObject WScript.Shell
try {
    $pet = $shell.CreateShortcut((Get-LiliesShortcutPath $linkRoot 'pet'))
    $pet.Arguments = '--compact --custom'; $pet.Save()
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($pet) | Out-Null
    $desktop = $shell.CreateShortcut((Get-LiliesShortcutPath $linkRoot 'desktop'))
    $desktop.TargetPath = Join-Path $source 'LiliesInTheBox.exe'; $desktop.Save()
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($desktop) | Out-Null
} finally {[Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null}
$before = Get-LiliesTreeSnapshot $linkRoot
$owned = New-LiliesRemovalShortcutPlan $target $linkRoot
Assert-LiliesTreeMatches $linkRoot $before
$t = New-LiliesUninstallTransaction @common -ShortcutPlan $owned.plan
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} | Out-Null
Result @{roles=@($owned.plan.entries.role); skipped=@($owned.skippedRoles)}
""")
    assert result['roles'] == ['primary', 'startup']
    assert result['skipped'] == ['desktop', 'pet']
    assert (tree / 'links' / ROLES['desktop']).exists() and (tree / 'links' / ROLES['pet']).exists()
    assert not (tree / 'links' / ROLES['primary']).exists()


def test_no_shortcuts_and_missing_executable_are_handled_without_assumptions(tree):
    original = inventory(tree / 'programs/Lilies in the box')
    result = run_ps(tree, LOAD + """
$t = New-LiliesUninstallTransaction @common
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} | Out-Null
Result (Restore-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRestore {} -RollbackCommitted)
""")
    assert result['state'] == 'restored'
    assert inventory(tree / 'programs/Lilies in the box') == original
    (tree / 'programs/Lilies in the box/LiliesInTheBox.exe').rename(tree / 'test-owned-exe')
    before = inventory(tree / 'programs')
    run_ps(tree, LOAD + 'New-LiliesUninstallTransaction @common', code=1)
    assert inventory(tree / 'programs') == before


@pytest.mark.parametrize('present', [False, True])
def test_absent_shortcut_root_or_startup_is_not_created(tree, present):
    if present:
        (tree / 'links').mkdir()
    result = run_ps(tree, LOAD + "Result (New-LiliesRemovalShortcutPlan $target (Join-Path $root 'links'))")
    assert result == {'plan': None, 'skippedRoles': []}
    assert (tree / 'links').exists() == present
    assert not (tree / 'links/Startup').exists()


def test_guard_holds_the_same_exclusive_lock_as_install(tree):
    result = run_ps(tree, LOAD + """
$t = New-LiliesUninstallTransaction @common
$state = @{blocked=$false}
Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {
    param($paths)
    try {$other=Open-LiliesInstallLock $paths; $other.Dispose()} catch {$state.blocked=$true}
} -BeforeRollback {} | Out-Null
Result $state
""")
    assert result['blocked']


def test_noop_empty_journal_recovery_does_not_guess_program_changes(tree):
    before = inventory(tree / 'programs')
    result = run_ps(tree, LOAD + """
$id = [guid]::NewGuid().ToString('N')
$paths = Get-LiliesTransactionPaths $target $base $journals $id
[IO.Directory]::CreateDirectory($paths.journal) | Out-Null
Result (Restore-LiliesUninstallTransaction @common -TransactionId $id -BeforeRestore {})
""")
    assert result['state'] == 'not-prepared'
    assert inventory(tree / 'programs') == before


@pytest.mark.parametrize('location', ['target', 'ancestor'])
def test_junction_cannot_redirect_uninstall_into_other_data(tree, location):
    data = inventory(tree / 'private-data')
    if location == 'target':
        body = "New-Item -ItemType Junction -Path (Join-Path $target 'linked') -Target (Join-Path $root 'private-data') | Out-Null\n"
    else:
        body = "New-Item -ItemType Junction -Path (Join-Path $root 'linked-programs') -Target $base | Out-Null\n$common.Target = Join-Path $root 'linked-programs/Lilies in the box'\n$common.AllowedBase = Join-Path $root 'linked-programs'\n"
    result = run_ps(tree, LOAD + body + """
$caught = ''
try { New-LiliesUninstallTransaction @common | Out-Null } catch {$caught=$_.Exception.Message}
Result @{error=$caught; targetStillExists=(Test-Path -LiteralPath (Join-Path $target 'LiliesInTheBox.exe'))}
""")
    assert 'Linked' in result['error'] and result['targetStillExists']
    assert inventory(tree / 'private-data') == data
    assert not list((tree / 'journals').glob('*/*/manifest.json'))


@pytest.mark.parametrize('change', ['operation', 'snapshot', 'plan-direction'])
def test_modified_manifest_cannot_change_removal_semantics(removal, change):
    prepared = run_removal(removal, 'Result (PrepareRemoval)')
    identity = prepared['transactionId']
    mutation = {
        'operation': "$manifest.operation='install'",
        'snapshot': '$manifest.newSnapshot=$manifest.oldSnapshot',
        'plan-direction': '$manifest.shortcuts.entries[0].before=$manifest.shortcuts.entries[0].after',
    }[change]
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    result = run_ps(removal, LOAD + f"""
$paths = Get-LiliesTransactionPaths $target $base $journals {quote(identity)}
$file = Join-Path $paths.journal 'manifest.json'
$manifest = Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
{mutation}
[IO.File]::WriteAllText($file, ($manifest | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
$state = @{{called=$false; error=''}}
try {{ Complete-LiliesUninstallTransaction @common -TransactionId {quote(identity)} -BeforeRemove {{$state.called=$true}} -BeforeRollback {{}} | Out-Null }}
catch {{$state.error=$_.Exception.Message}}
Result $state
""")
    assert result['error'] and not result['called']
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before


def test_external_bytes_racing_shortcut_removal_are_retained_not_restored_over(removal):
    result = run_removal(removal, """
$t = PrepareRemoval
$caught = ''
try { Complete-LiliesUninstallTransaction @common -TransactionId $t.transactionId -BeforeRemove {} -BeforeRollback {} -Checkpoint {
    param($name,$paths)
    if ($name -ceq 'before-shortcut-remove-primary') {
        [IO.File]::WriteAllText((Get-LiliesShortcutPath $linkRoot 'primary'),'external-link-race')
    }
} | Out-Null } catch {$caught=$_.Exception.Message}
Result @{error=$caught; id=$t.transactionId}
""")
    assert 'recovery both failed' in result['error']
    displaced = list((removal / 'links').glob('.lilies-*-primary-remove.pending'))
    assert len(displaced) == 1 and displaced[0].read_bytes() == b'external-link-race'
    before = inventory(removal / 'programs'), inventory(removal / 'links')
    run_ps(removal, LOAD + f"Restore-LiliesUninstallTransaction @common -TransactionId {quote(result['id'])} -BeforeRestore {{}}", code=1)
    assert (inventory(removal / 'programs'), inventory(removal / 'links')) == before
