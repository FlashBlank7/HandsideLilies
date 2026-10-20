"""Real file/COM semantics in F-drive fixtures; never Windows Start Menu."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from test_install_transaction import inventory, quote, run_ps, tree  # noqa: F401


ROLES = {
    "primary": "Lilies in the box.lnk",
    "desktop": "Lilies in the box - Desktop.lnk",
    "pet": "Lilies in the box - Pet only.lnk",
    "startup": "Startup/Lilies in the box.lnk",
}


@pytest.fixture
def links(tree):
    root = tree / "links"
    (root / "Startup").mkdir(parents=True)
    for role in ("primary", "startup"):
        (root / ROLES[role]).write_bytes(("old-" + role).encode())
    (root / "other-user-app.lnk").write_bytes(b"untouched-user-entry")
    return tree


SETUP = """
$linkRoot = Join-Path $root 'links'
$payloads = @{}
foreach ($role in @('primary', 'desktop', 'pet', 'startup')) {
    $payloads[$role] = [Text.Encoding]::UTF8.GetBytes('new-' + $role)
}
$plan = New-LiliesShortcutPlan $linkRoot $payloads
function PrepareLinks { New-LiliesInstallTransaction @common -SourceRoot $source -ShortcutPlan $plan }
"""


def manifest(links):
    paths = list((links / "journals").glob("*/*/manifest.json"))
    assert len(paths) == 1
    return json.loads(paths[0].read_text("utf-8"))


def run_links(links, body, **kwargs):
    return run_ps(links, SETUP + body, **kwargs)


def test_shortcut_plan_is_durable_before_program_swap_and_repeat_commit_is_read_only(links):
    before = inventory(links / "links")
    prepared = run_links(links, "Result (PrepareLinks)")
    assert inventory(links / "links") == before
    stored = manifest(links)
    assert stored["schemaVersion"] == 2
    assert len(stored["shortcuts"]["entries"]) == 4
    result = run_ps(links, f"""
Complete-LiliesInstallTransaction @common -TransactionId {quote(prepared['transactionId'])} | Out-Null
Result (Complete-LiliesInstallTransaction @common -TransactionId {quote(prepared['transactionId'])} -AfterActivate {{ throw 'must-not-replay' }})
""")
    assert result["alreadyCommitted"]
    for role, filename in ROLES.items():
        assert (links / "links" / filename).read_bytes() == ("new-" + role).encode()
    assert (links / "links/other-user-app.lnk").read_bytes() == b"untouched-user-entry"


def test_explicit_rollback_restores_existing_and_removes_only_fresh_shortcuts(links):
    before = inventory(links / "links")
    old = inventory(links / "programs/Lilies in the box")
    result = run_links(links, """
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted | Out-Null
Result (Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted)
""")
    assert result["alreadyRestored"]
    assert inventory(links / "links") == before
    assert inventory(links / "programs/Lilies in the box") == old
    retained = [p.read_bytes() for p in (links / "journals").glob("*/*/s-*.bin")]
    assert b"new-pet" in retained and b"new-desktop" in retained


@pytest.mark.parametrize("point", ["before-shortcut-primary", "after-shortcut-desktop", "after-shortcut-pet", "after-shortcut-startup", "before-commit"])
def test_shortcut_or_commit_failure_rolls_back_program_and_all_links(links, point):
    before = inventory(links / "links")
    old = inventory(links / "programs/Lilies in the box")
    result = run_links(links, f"""
$t = PrepareLinks
$caught = ''
try {{ Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint {{
    param($name, $paths); if ($name -eq {quote(point)}) {{ throw 'injected-shortcut-failure' }}
}} | Out-Null }} catch {{ $caught = $_.Exception.Message }}
Result @{{ error = $caught; recovery = (Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId) }}
""")
    assert result["error"] == "injected-shortcut-failure"
    assert result["recovery"]["alreadyRestored"]
    assert inventory(links / "links") == before
    assert inventory(links / "programs/Lilies in the box") == old


@pytest.mark.parametrize("point", ["after-new-move", "after-shortcut-desktop", "after-shortcut-primary", "after-shortcut-swap-primary", "before-commit"])
def test_abrupt_exit_after_any_partial_link_change_recovers_in_fresh_process(links, point):
    before = inventory(links / "links")
    old = inventory(links / "programs/Lilies in the box")
    run_links(links, f"""
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint {{
    param($name, $paths); if ($name -eq {quote(point)}) {{ [Environment]::Exit(87) }}
}}
""", code=87)
    identity = manifest(links)["transactionId"]
    result = run_ps(links, f"Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)})")
    assert result["state"] == "restored"
    assert inventory(links / "links") == before
    assert inventory(links / "programs/Lilies in the box") == old


@pytest.mark.parametrize("role", ["desktop", "primary", "startup"])
def test_shortcut_recovery_can_itself_be_interrupted(links, role):
    before = inventory(links / "links")
    run_links(links, """
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Result $t
""")
    identity = manifest(links)["transactionId"]
    run_ps(links, f"""
Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted -Checkpoint {{
    param($name, $paths); if ($name -eq 'after-shortcut-restore-{role}') {{ [Environment]::Exit(88) }}
}}
""", code=88)
    run_ps(links, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted | Out-Null")
    assert inventory(links / "links") == before


def test_startup_failure_quiesces_owned_process_before_any_recovery(links):
    before = inventory(links / "links")
    old = inventory(links / "programs/Lilies in the box")
    result = run_links(links, """
$t = PrepareLinks
$state = @{ stopped = $false; sawNewProgram = $false; sawNewLinks = $false }
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {
    throw 'synthetic-startup-health-failure'
} -BeforeRollback {
    $state.sawNewProgram = Test-LiliesOwnedTarget (Get-LiliesTransactionPaths $target $base $journals $t.transactionId)
    $state.sawNewLinks = [IO.File]::ReadAllText((Join-Path $linkRoot 'Lilies in the box.lnk')) -ceq 'new-primary'
    $state.stopped = $true
} | Out-Null } catch { if ($_.Exception.Message -ne 'synthetic-startup-health-failure') { throw } }
Result $state
""")
    assert result == {"stopped": True, "sawNewProgram": True, "sawNewLinks": True}
    assert inventory(links / "links") == before
    assert inventory(links / "programs/Lilies in the box") == old


def test_failed_quiescence_does_not_swap_files_under_a_running_app(links):
    result = run_links(links, """
$t = PrepareLinks
$failed = $false
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate { throw 'health-failed' } -BeforeRollback { throw 'could-not-stop-owned-child' } | Out-Null }
catch { $failed = $_.Exception.Message -like '*recovery both failed*' }
$paths = Get-LiliesTransactionPaths $target $base $journals $t.transactionId
Result @{ failed = $failed; newProgramStillPresent = (Test-LiliesOwnedTarget $paths); states = (Get-LiliesInstallStates $paths); id = $t.transactionId }
""")
    assert result["failed"] and result["newProgramStillPresent"]
    assert "restored" not in result["states"] and "committed" not in result["states"]
    assert (links / "links/Lilies in the box.lnk").read_bytes() == b"new-primary"
    # Simulate the caller later verifying the process has stopped. The file
    # helper itself never starts, kills or queries any actual app process.
    run_ps(links, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(result['id'])} | Out-Null")


@pytest.mark.parametrize("phase", ["prepared", "committed"])
def test_external_shortcut_edit_is_preserved_without_program_rollback(links, phase):
    body = "$t = PrepareLinks\n"
    if phase == "committed":
        body += "Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null\n"
    prepared = run_links(links, body + "Result $t")
    changed = links / "links/Lilies in the box.lnk"
    changed.write_bytes(b"user-customized-link")
    before_links, before_programs = inventory(links / "links"), inventory(links / "programs")
    operation = "Complete-LiliesInstallTransaction" if phase == "prepared" else "Restore-LiliesInstallTransaction"
    suffix = "" if phase == "prepared" else " -RollbackCommitted"
    run_ps(links, f"{operation} @common -TransactionId {quote(prepared['transactionId'])}{suffix}", code=1)
    assert inventory(links / "links") == before_links
    assert inventory(links / "programs") == before_programs


@pytest.mark.parametrize("change", ["unknown-role", "duplicate-role", "corrupt-backup", "missing-plan", "overlap"])
def test_corrupt_or_unsafe_plan_is_rejected_before_any_swap(links, change):
    prepared = run_links(links, "Result (PrepareLinks)")
    path = Path(prepared["journal"]) / "manifest.json"
    stored = json.loads(path.read_text("utf-8"))
    if change == "unknown-role":
        stored["shortcuts"]["entries"][0]["role"] = "../../unrelated"
    elif change == "duplicate-role":
        stored["shortcuts"]["entries"][1]["role"] = stored["shortcuts"]["entries"][0]["role"]
    elif change == "corrupt-backup":
        next(e for e in stored["shortcuts"]["entries"] if e["before"])["before"]["data"] = "YnJva2Vu"
    elif change == "missing-plan":
        del stored["shortcuts"]
    else:
        stored["shortcuts"]["root"] = str(links / "programs")
    path.write_text(json.dumps(stored), "utf-8")
    before_links, before_programs = inventory(links / "links"), inventory(links / "programs")
    run_ps(links, f"Complete-LiliesInstallTransaction @common -TransactionId {quote(prepared['transactionId'])}", code=1)
    assert inventory(links / "links") == before_links
    assert inventory(links / "programs") == before_programs


def test_real_com_shortcuts_are_generated_only_in_f_staging_and_keep_arguments(links):
    before = inventory(links / "links")
    result = run_ps(links, """
$payloads = New-LiliesShortcutPayloads $target (Join-Path $root 'link-payloads') $true
$shell = New-Object -ComObject WScript.Shell
$values = @{}
foreach ($role in @('primary','desktop','pet')) {
    $link = $shell.CreateShortcut((Join-Path (Join-Path $root 'link-payloads') ($role + '.lnk')))
    $values[$role] = @{ arguments = $link.Arguments; target = $link.TargetPath; workdir = $link.WorkingDirectory }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($link) | Out-Null
}
[Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null
Result @{ roles = @($payloads.Keys); values = $values; startupMatches = (Get-LiliesBytesHash $payloads.startup) -ceq (Get-LiliesBytesHash $payloads.primary) }
""")
    assert set(result["roles"]) == set(ROLES)
    assert result["startupMatches"]
    for role, arguments in {"primary": "", "desktop": "--visual", "pet": "--compact"}.items():
        assert result["values"][role]["arguments"] == arguments
        assert Path(result["values"][role]["target"]) == links / "programs/Lilies in the box/LiliesInTheBox.exe"
    assert inventory(links / "links") == before


@pytest.mark.parametrize("purpose", ["replaced", "remove"])
def test_foreign_displaced_shortcut_stops_recovery_before_any_mutation(links, purpose):
    prepared = run_links(links, """
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Result $t
""")
    # Model the durable boundary after an atomic move/replacement displaced a
    # concurrently edited link. It is not ours merely because its name is.
    residue = links / "links" / f".lilies-{prepared['transactionId']}-primary-{purpose}.pending"
    residue.write_bytes(b"third-party-edited-link")
    before_links, before_programs = inventory(links / "links"), inventory(links / "programs")
    result = run_ps(links, f"""
$failed = $false
try {{ Restore-LiliesInstallTransaction @common -TransactionId {quote(prepared['transactionId'])} -RollbackCommitted | Out-Null }}
catch {{ $failed = $true }}
$paths = Get-LiliesTransactionPaths $target $base $journals {quote(prepared['transactionId'])}
Result @{{ failed = $failed; states = (Get-LiliesInstallStates $paths) }}
""")
    assert result["failed"], "A displaced external edit must stop recovery for review"
    assert "recovering" not in result["states"]
    assert inventory(links / "links") == before_links
    assert inventory(links / "programs") == before_programs


@pytest.mark.parametrize("phase", ["apply", "restore", "remove"])
def test_external_write_at_atomic_boundary_is_retained_and_cannot_be_silently_recovered(links, phase):
    prepared = run_links(links, "Result (PrepareLinks)")
    identity = prepared["transactionId"]
    if phase != "apply":
        run_ps(links, f"Complete-LiliesInstallTransaction @common -TransactionId {quote(identity)} | Out-Null")
    role = "pet" if phase == "remove" else "primary"
    purpose = "remove" if phase == "remove" else "replaced"
    point = f"before-shortcut-{'remove' if phase == 'remove' else 'swap'}-{role}"
    operation = "Complete-LiliesInstallTransaction" if phase == "apply" else "Restore-LiliesInstallTransaction"
    suffix = "" if phase == "apply" else " -RollbackCommitted"
    result = run_ps(links, f"""
$failed = $false
try {{ {operation} @common -TransactionId {quote(identity)}{suffix} -Checkpoint {{
    param($name, $paths)
    if ($name -ceq {quote(point)}) {{
        [IO.File]::WriteAllBytes({quote(links / 'links' / ROLES[role])}, [Text.Encoding]::UTF8.GetBytes('external-at-atomic-boundary'))
    }}
}} | Out-Null }} catch {{ $failed = $true }}
Result @{{ failed = $failed }}
""")
    assert result["failed"]
    residue = links / "links" / f".lilies-{identity}-{role}-{purpose}.pending"
    assert residue.read_bytes() == b"external-at-atomic-boundary"
    before_links, before_programs = inventory(links / "links"), inventory(links / "programs")
    run_ps(links, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)}{suffix}", code=1)
    assert inventory(links / "links") == before_links
    assert inventory(links / "programs") == before_programs


@pytest.mark.parametrize("point", ["after-shortcut-swap-primary", "after-shortcut-remove-pet"])
def test_crash_during_atomic_shortcut_restore_is_restartable(links, point):
    before = inventory(links / "links")
    old = inventory(links / "programs/Lilies in the box")
    prepared = run_links(links, """
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Result $t
""")
    identity = prepared["transactionId"]
    run_ps(links, f"""
Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted -Checkpoint {{
    param($name, $paths); if ($name -ceq {quote(point)}) {{ [Environment]::Exit(89) }}
}}
""", code=89)
    run_ps(links, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted | Out-Null")
    assert inventory(links / "links") == before
    assert inventory(links / "programs/Lilies in the box") == old


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("opt_in", [False, True])
def test_managed_plan_keeps_startup_opt_in_and_never_changes_links_during_preparation(tree, existing, opt_in):
    root = tree / "managed-links"
    (root / "Startup").mkdir(parents=True)
    if existing:
        (root / ROLES["startup"]).write_bytes(b"user-existing-startup")
    before = inventory(root)
    result = run_ps(tree, f"""
$plan = New-LiliesManagedShortcutPlan $target {quote(root)} (Join-Path $root 'managed-payloads') ${str(opt_in).lower()}
Result @{{ roles = @($plan.entries | ForEach-Object {{ $_.role }}); startupOld = @($plan.entries | Where-Object {{ $_.role -ceq 'startup' }} | ForEach-Object {{ $_.before }}) }}
""")
    assert set(result["roles"]) == ({"primary", "desktop", "pet", "startup"} if existing or opt_in else {"primary", "desktop", "pet"})
    assert inventory(root) == before
    if existing:
        assert result["startupOld"][0]["length"] == len(b"user-existing-startup")
