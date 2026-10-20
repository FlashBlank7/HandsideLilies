"""Execute program-directory transactions in synthetic F-drive trees only.

No production installer, executable, shortcut, shell restoration, credentials,
database or desktop interaction is invoked. Abrupt-exit cases kill only their
own hidden PowerShell child, then recover through a separate process.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "install_transaction.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows directory/lock semantics")


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    assert tmp_path.resolve().drive.casefold() == "f:"
    source = tmp_path / "release"
    (source / "图层 [原稿]" / "empty").mkdir(parents=True)
    (source / "LiliesInTheBox.exe").write_bytes(b"synthetic-new-program-not-executable")
    (source / "图层 [原稿]" / "莉莉丝.txt").write_text("洁净、苍白 🕊", encoding="utf-8")
    (source / ".hidden").write_bytes(b"hidden-must-copy")
    target = tmp_path / "programs" / "Lilies in the box"
    target.mkdir(parents=True)
    (target / "old-empty").mkdir()
    (target / "LiliesInTheBox.exe").write_bytes(b"synthetic-old-program-not-executable")
    (target / "old.dll").write_bytes(b"old-library")
    (tmp_path / "private-data").mkdir()
    (tmp_path / "private-data" / "sentinel.db").write_bytes(b"not-a-real-user-database")
    return tmp_path


def inventory(path: Path) -> dict[str, bytes | None]:
    if not path.exists():
        return {}
    return {p.relative_to(path).as_posix(): None if p.is_dir() else p.read_bytes() for p in path.rglob("*")}


def run_ps(tree: Path, body: str, *, code: int = 0) -> dict:
    assert tree.resolve().drive.casefold() == "f:"
    prefix = f"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
. {quote(HELPER)}
$root = {quote(tree)}
$source = Join-Path $root 'release'
$base = Join-Path $root 'programs'
$target = Join-Path $base 'Lilies in the box'
$journals = Join-Path $root 'journals'
$common = @{{ Target = $target; AllowedBase = $base; JournalRoot = $journals }}
function Result($value) {{ Write-Output ('LILIES_RESULT=' + ($value | ConvertTo-Json -Depth 16 -Compress)) }}
function Prepare {{ New-LiliesInstallTransaction @common -SourceRoot $source -InstallManifestJson '{{"version":"synthetic"}}' }}
"""
    encoded = base64.b64encode((prefix + body).encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-OutputFormat", "Text", "-EncodedCommand", encoded],
        cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "TEMP": str(tree), "TMP": str(tree), "PSModuleAnalysisCachePath": str(tree / "powershell-module-cache")},
    )
    assert result.returncode == code, f"exit {result.returncode}, expected {code}\n{result.stdout}\n{result.stderr}"
    messages = [line.removeprefix("LILIES_RESULT=") for line in result.stdout.splitlines() if line.startswith("LILIES_RESULT=")]
    return json.loads(messages[-1]) if messages else {}


def ticket(tree: Path) -> dict:
    manifests = list((tree / "journals").glob("*/*/manifest.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_text("utf-8"))


def test_prepare_commit_retains_old_and_repeat_commit_does_not_replay(tree: Path) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    source = inventory(tree / "release")
    prepared = run_ps(tree, "Result (Prepare)")
    assert inventory(tree / "programs/Lilies in the box") == old
    stage = inventory(Path(prepared["stage"]))
    assert all(stage[k] == v for k, v in source.items())
    assert len(stage) == len(source) + 2
    identity = prepared["transactionId"]
    result = run_ps(tree, f"""
Complete-LiliesInstallTransaction @common -TransactionId {quote(identity)} -AfterActivate {{ 'callback-output-must-not-leak' }} | Out-Null
Result (Complete-LiliesInstallTransaction @common -TransactionId {quote(identity)} -AfterActivate {{ throw 'must-not-replay' }})
""")
    assert result["alreadyCommitted"] is True
    assert inventory(Path(result["previous"])) == old
    assert inventory(tree / "programs/Lilies in the box") == stage
    assert inventory(tree / "release") == source
    assert (tree / "private-data/sentinel.db").read_bytes() == b"not-a-real-user-database"


def test_explicit_rollback_retains_new_and_is_repeatable(tree: Path) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$t = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
try { Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId; throw 'expected-refusal' }
catch { if ($_.Exception.Message -notlike '*explicit rollback*') { throw } }
$first = Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted
$second = Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted
Result @{ first = $first; second = $second }
""")
    assert inventory(tree / "programs/Lilies in the box") == old
    assert (Path(result["first"]["retainedFailed"]) / "LiliesInTheBox.exe").read_bytes().startswith(b"synthetic-new")
    assert result["second"]["alreadyRestored"] is True


@pytest.mark.parametrize("point", ["stage-created", "copy-file", "stage-copied"])
def test_staging_failure_never_changes_old(tree: Path, point: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    run_ps(tree, f"""
try {{ New-LiliesInstallTransaction @common -SourceRoot $source -Checkpoint {{ param($name, $paths); if ($name -eq {quote(point)}) {{ throw 'injected-copy-failure' }} }} }}
catch {{ if ($_.Exception.Message -ne 'injected-copy-failure') {{ throw }}; Result @{{ failed = $true }} }}
""")
    assert inventory(tree / "programs/Lilies in the box") == old
    assert not list((tree / "programs").glob(".lilies-previous-*"))
    assert list((tree / "programs").glob(".lilies-stage-*"))
    assert run_ps(tree, "Result (Prepare)")["state"] == "prepared"


@pytest.mark.parametrize("point", ["before-old-move", "after-old-move", "after-new-move", "before-commit"])
def test_activation_exception_restores_exact_old(tree: Path, point: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, f"""
$t = Prepare
try {{ Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint {{ param($name, $paths); if ($name -eq {quote(point)}) {{ throw 'injected-activation-failure' }} }} }}
catch {{ if ($_.Exception.Message -ne 'injected-activation-failure') {{ throw }} }}
Result (Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId)
""")
    assert result["alreadyRestored"] is True
    assert inventory(tree / "programs/Lilies in the box") == old


@pytest.mark.parametrize("point", ["stage-created", "after-old-move", "after-new-move", "before-commit"])
def test_actual_abrupt_process_exit_recovers_in_new_process(tree: Path, point: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    hook = f"{{ param($name, $paths); if ($name -eq {quote(point)}) {{ [Environment]::Exit(87) }} }}"
    if point == "stage-created":
        body = f"New-LiliesInstallTransaction @common -SourceRoot $source -Checkpoint {hook}"
    else:
        body = f"$t = Prepare; Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint {hook}"
    run_ps(tree, body, code=87)
    identity = ticket(tree)["transactionId"]
    if point == "after-old-move":
        assert not (tree / "programs/Lilies in the box").exists()
    result = run_ps(tree, f"Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)})")
    assert result["state"] == "restored"
    assert inventory(tree / "programs/Lilies in the box") == old
    # The dead child's exclusive file lock must not leave a stale live owner.
    assert run_ps(tree, "Result (Prepare)")["state"] == "prepared"


@pytest.mark.parametrize("point", ["after-quarantine", "after-old-restore"])
def test_recovery_itself_can_be_interrupted_and_retried(tree: Path, point: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    run_ps(tree, "$t = Prepare; Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint { param($name, $paths); if ($name -eq 'after-new-move') { [Environment]::Exit(87) } }", code=87)
    identity = ticket(tree)["transactionId"]
    run_ps(tree, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -Checkpoint {{ param($name, $paths); if ($name -eq {quote(point)}) {{ [Environment]::Exit(88) }} }}", code=88)
    assert run_ps(tree, f"Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)})")["state"] == "restored"
    assert inventory(tree / "programs/Lilies in the box") == old
    assert list((tree / "programs").glob(".lilies-failed-*"))


def test_fresh_install_rollback_leaves_no_target_and_keeps_payload(tree: Path) -> None:
    # Preserve the fixture instead of deleting it to simulate a fresh target.
    (tree / "programs/Lilies in the box").rename(tree / "untouched-old-fixture")
    result = run_ps(tree, """
$t = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Result (Restore-LiliesInstallTransaction @common -TransactionId $t.transactionId -RollbackCommitted)
""")
    assert not (tree / "programs/Lilies in the box").exists()
    assert (Path(result["retainedFailed"]) / "LiliesInTheBox.exe").exists()


def test_callback_failure_quarantines_modified_new_files(tree: Path) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$t = Prepare
try { Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -AfterActivate {
    [IO.File]::WriteAllText((Join-Path $target 'retain-me.txt'), 'callback-created'); throw 'callback-failed'
} } catch { if ($_.Exception.Message -ne 'callback-failed') { throw } }
Result @{ id = $t.transactionId }
""")
    assert inventory(tree / "programs/Lilies in the box") == old
    assert (tree / "programs" / (".lilies-failed-" + result["id"]) / "retain-me.txt").read_text() == "callback-created"


@pytest.mark.parametrize("kind", ["stage", "old", "manifest", "event", "pending-event"])
def test_corruption_is_not_silently_activated(tree: Path, kind: str) -> None:
    prepared = run_ps(tree, "Result (Prepare)")
    old = inventory(tree / "programs/Lilies in the box")
    journal = Path(prepared["journal"])
    if kind == "stage":
        (Path(prepared["stage"]) / "old.dll").write_bytes(b"unexpected")
    elif kind == "old":
        (tree / "programs/Lilies in the box/old.dll").write_bytes(b"externally-changed")
        old = inventory(tree / "programs/Lilies in the box")
    elif kind == "manifest":
        (journal / "manifest.json").write_text("{broken", encoding="utf-8")
    elif kind == "event":
        (journal / ("event-" + "a" * 32 + ".json")).write_text("{broken", encoding="utf-8")
    else:
        (journal / ("event-" + "a" * 32 + ".json.pending-" + "b" * 32)).write_text("{partial", encoding="utf-8")
    identity = quote(prepared["transactionId"])
    if kind == "pending-event":
        assert run_ps(tree, f"Result (Complete-LiliesInstallTransaction @common -TransactionId {identity})")["state"] == "committed"
    else:
        run_ps(tree, f"Complete-LiliesInstallTransaction @common -TransactionId {identity}", code=1)
        assert inventory(tree / "programs/Lilies in the box") == old
        assert not list((tree / "programs").glob(".lilies-previous-*"))


@pytest.mark.parametrize("kind", ["foreign-target", "bad-previous"])
def test_recovery_preserves_conflicting_files_for_manual_review(tree: Path, kind: str) -> None:
    run_ps(tree, "$t = Prepare; Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId -Checkpoint { param($name, $paths); if ($name -eq 'after-old-move') { [Environment]::Exit(87) } }", code=87)
    identity = ticket(tree)["transactionId"]
    if kind == "foreign-target":
        (tree / "programs/Lilies in the box").mkdir()
        (tree / "programs/Lilies in the box/foreign.txt").write_bytes(b"foreign")
    else:
        (tree / "programs" / (".lilies-previous-" + identity) / "old.dll").write_bytes(b"unknown-corruption")
    before = inventory(tree / "programs")
    run_ps(tree, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)}", code=1)
    assert inventory(tree / "programs") == before


def test_live_exclusive_lock_and_pending_install_block_new_preparation(tree: Path) -> None:
    result = run_ps(tree, """
$paths = Get-LiliesTransactionPaths $target $base $journals ('a' * 32)
$lock = Open-LiliesInstallLock $paths
try { try { Prepare; throw 'expected-lock-refusal' } catch { if ($_.Exception.Message -eq 'expected-lock-refusal') { throw }; $locked = $true } }
finally { $lock.Dispose() }
$t = Prepare
try { Prepare; throw 'expected-pending-refusal' } catch { if ($_.Exception.Message -notlike '*recovered first*') { throw } }
Result @{ locked = $locked; id = $t.transactionId }
""")
    assert result["locked"] is True
    assert len(list((tree / "programs").glob(".lilies-stage-*"))) == 1


@pytest.mark.parametrize("target", ["$base", "(Join-Path $base 'nested\\Lilies')", "(Join-Path $root 'outside')", "(Join-Path $base '.lilies-stage-foreign')", "(Join-Path $base 'Lilies.')", "(Join-Path $base 'Lilies:stream')"])
def test_unsafe_paths_do_not_touch_program_tree(tree: Path, target: str) -> None:
    before = inventory(tree / "programs")
    run_ps(tree, f"Get-LiliesTransactionPaths {target} $base $journals ('a' * 32)", code=1)
    assert inventory(tree / "programs") == before
    assert not (tree / "journals").exists()


def test_nested_junction_is_rejected_before_copy_or_move(tree: Path) -> None:
    before = inventory(tree / "programs")
    result = run_ps(tree, """
New-Item -ItemType Junction -Path (Join-Path $source 'linked') -Target (Join-Path $root 'private-data') | Out-Null
try { Prepare; throw 'expected-link-refusal' } catch { if ($_.Exception.Message -notlike '*Linked program entry*') { throw } }
Result @{ rejected = $true }
""")
    assert result["rejected"] is True
    assert inventory(tree / "programs") == before
    assert (tree / "private-data/sentinel.db").read_bytes() == b"not-a-real-user-database"


def test_atomic_json_write_is_exclusive_and_no_partial_final_record(tree: Path) -> None:
    run_ps(tree, """
$path = Join-Path $root 'immutable.json'
Write-LiliesExclusiveJson $path @{ message = 'first' }
try { Write-LiliesExclusiveJson $path @{ message = 'second' }; throw 'expected-exclusive-refusal' }
catch { if ($_.Exception.Message -notlike '*already exists*') { throw } }
Result (Get-Content -LiteralPath $path -Raw | ConvertFrom-Json)
""")
    assert json.loads((tree / "immutable.json").read_text("utf-8")) == {"message": "first"}
    assert not list(tree.glob(".pending-*"))


@pytest.mark.parametrize("partial_record", [False, True])
def test_death_before_manifest_publication_is_safe_to_recover(tree: Path, partial_record: bool) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    partial = "[IO.File]::WriteAllText((Join-Path $paths.journal ('.pending-' + ('a' * 32))), '{partial');" if partial_record else ""
    run_ps(tree, f"New-LiliesInstallTransaction @common -SourceRoot $source -Checkpoint {{ param($name, $paths); if ($name -eq 'before-manifest') {{ {partial} [Environment]::Exit(87) }} }}", code=87)
    journal = next(p for p in (tree / "journals").glob("*/*") if p.is_dir())
    result = run_ps(tree, f"Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(journal.name)})")
    assert result["state"] == "not-prepared"
    assert inventory(tree / "programs/Lilies in the box") == old
    assert run_ps(tree, "Result (Prepare)")["state"] == "prepared"
    if partial_record:
        assert (journal / (".pending-" + "a" * 32)).read_text() == "{partial"


@pytest.mark.parametrize("change", ["source", "stage", "validation"])
def test_preparation_rechecks_copied_files_and_payload(tree: Path, change: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    hook = "{}"
    validator = "{ 'noise' }"
    if change == "source":
        hook = "{ param($name, $paths); if ($name -eq 'copy-file') { [IO.File]::WriteAllText((Join-Path $source 'LiliesInTheBox.exe'), 'changed') } }"
    elif change == "stage":
        hook = "{ param($name, $paths); if ($name -eq 'stage-copied') { [IO.File]::WriteAllText((Join-Path $paths.stage 'injected.txt'), 'changed') } }"
    else:
        validator = "{ param($directory); if ((Split-Path -Leaf $directory) -like '.lilies-stage-*') { throw 'invalid-payload' } }"
    run_ps(tree, f"New-LiliesInstallTransaction @common -SourceRoot $source -Checkpoint {hook} -ValidatePayload {validator}", code=1)
    assert inventory(tree / "programs/Lilies in the box") == old
    assert not list((tree / "programs").glob(".lilies-previous-*"))


@pytest.mark.parametrize("journal", ["(Join-Path $target 'journal')", "$root", "'C:\\Lilies-should-not-create'", "'\\\\server\\share\\journals'"])
def test_journal_must_be_separate_local_f_storage(tree: Path, journal: str) -> None:
    before = inventory(tree / "programs")
    run_ps(tree, f"Get-LiliesTransactionPaths $target $base {journal} ('a' * 32)", code=1)
    assert inventory(tree / "programs") == before


def test_immutable_record_does_not_ignore_a_broken_published_manifest(tree: Path) -> None:
    prepared = run_ps(tree, "Result (Prepare)")
    (Path(prepared["journal"]) / "manifest.json").write_text("{partial", encoding="utf-8")
    before = inventory(tree / "programs")
    run_ps(tree, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(prepared['transactionId'])}", code=1)
    assert inventory(tree / "programs") == before


def test_direct_move_cannot_use_arbitrary_destinations_or_overwrite(tree: Path) -> None:
    before = inventory(tree / "programs")
    result = run_ps(tree, """
$paths = Get-LiliesTransactionPaths $target $base $journals ('a' * 32)
foreach ($destination in @((Join-Path $root 'private-data'), $target)) {
    try { Move-LiliesProgramDirectory $target $destination $paths; throw 'expected-refusal' }
    catch { if ($_.Exception.Message -notlike '*unsafe or overwriting*') { throw } }
}
Result @{ rejected = $true }
""")
    assert result["rejected"] is True
    assert inventory(tree / "programs") == before


@pytest.mark.parametrize("operation", ["rollback", "repeat-complete"])
def test_pending_newer_transaction_blocks_old_transaction_entry(tree: Path, operation: str) -> None:
    invoke = (
        "Restore-LiliesInstallTransaction @common -TransactionId $first.transactionId -RollbackCommitted"
        if operation == "rollback" else
        "Complete-LiliesInstallTransaction @common -TransactionId $first.transactionId"
    )
    result = run_ps(tree, f"""
$first = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $first.transactionId | Out-Null
$second = Prepare
$before = Get-LiliesTreeSnapshot $target | ConvertTo-Json -Depth 8 -Compress
$rejected = $false
try {{ {invoke} | Out-Null }} catch {{ $rejected = $true }}
$after = Get-LiliesTreeSnapshot $target | ConvertTo-Json -Depth 8 -Compress
Result @{{ rejected = $rejected; unchanged = ($before -ceq $after); first = $first.transactionId; second = $second.transactionId }}
""")
    assert result["rejected"] is True
    assert result["unchanged"] is True
    # Recover the pending newer operation first. The older rollback can then
    # proceed; blocked attempts must not poison either journal.
    recovered = run_ps(tree, f"""
Restore-LiliesInstallTransaction @common -TransactionId {quote(result['second'])} | Out-Null
Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(result['first'])} -RollbackCommitted)
""")
    assert recovered["state"] == "restored"
    assert (tree / "programs/Lilies in the box/old.dll").read_bytes() == b"old-library"


@pytest.mark.parametrize("point", ["after-quarantine", "after-old-restore"])
def test_interrupted_committed_rollback_blocks_new_prepare(tree: Path, point: str) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    run_ps(tree, f"""
$first = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $first.transactionId | Out-Null
Restore-LiliesInstallTransaction @common -TransactionId $first.transactionId -RollbackCommitted -Checkpoint {{
    param($name, $paths); if ($name -eq {quote(point)}) {{ [Environment]::Exit(89) }}
}}
""", code=89)
    identity = ticket(tree)["transactionId"]
    before = inventory(tree / "programs")
    result = run_ps(tree, """
$rejected = $false
try { Prepare | Out-Null } catch { $rejected = $true }
Result @{ rejected = $rejected }
""")
    assert result["rejected"] is True
    assert inventory(tree / "programs") == before
    assert run_ps(tree, f"Result (Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted)")["state"] == "restored"
    assert inventory(tree / "programs/Lilies in the box") == old
    assert run_ps(tree, "Result (Prepare)")["state"] == "prepared"


def test_wrong_older_rollback_has_no_recovery_intent_and_can_be_resolved_in_order(tree: Path) -> None:
    old = inventory(tree / "programs/Lilies in the box")
    result = run_ps(tree, """
$first = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $first.transactionId | Out-Null
$second = Prepare
Complete-LiliesInstallTransaction @common -TransactionId $second.transactionId | Out-Null
$before = Get-LiliesTreeSnapshot $target | ConvertTo-Json -Depth 8 -Compress
$rejected = $false
try { Restore-LiliesInstallTransaction @common -TransactionId $first.transactionId -RollbackCommitted | Out-Null }
catch { $rejected = $true }
$paths = Get-LiliesTransactionPaths $target $base $journals $first.transactionId
$states = Get-LiliesInstallStates $paths
$after = Get-LiliesTreeSnapshot $target | ConvertTo-Json -Depth 8 -Compress
Result @{ rejected = $rejected; unchanged = ($before -ceq $after); recoveryIntent = ('recovering' -in $states); first = $first.transactionId; second = $second.transactionId }
""")
    assert result["rejected"] is True
    assert result["unchanged"] is True
    assert result["recoveryIntent"] is False
    run_ps(tree, f"""
Restore-LiliesInstallTransaction @common -TransactionId {quote(result['second'])} -RollbackCommitted | Out-Null
Restore-LiliesInstallTransaction @common -TransactionId {quote(result['first'])} -RollbackCommitted | Out-Null
""")
    assert inventory(tree / "programs/Lilies in the box") == old
