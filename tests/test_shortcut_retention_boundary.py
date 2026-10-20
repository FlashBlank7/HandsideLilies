"""Concurrent link-byte replacement at the last retention boundary, F only."""
from test_install_shortcuts import links, run_links  # noqa: F401
from test_install_transaction import inventory, quote, run_ps, tree  # noqa: F401


def test_late_external_displaced_bytes_are_not_archived_and_forgotten(links):
    prepared = run_links(links, """
$t = PrepareLinks
Complete-LiliesInstallTransaction @common -TransactionId $t.transactionId | Out-Null
Result $t
""")
    identity = prepared["transactionId"]
    result = run_ps(links, f"""
$failed = $false
try {{ Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted -Checkpoint {{
    param($name, $paths)
    if ($name -ceq 'after-shortcut-restore-primary') {{
        $plan = (Read-LiliesInstallManifest $paths).shortcuts
        $entry = @($plan.entries | Where-Object {{ $_.role -ceq 'primary' }})[0]
        $pending = Get-LiliesShortcutTemp $paths $plan $entry 'replaced'
        [IO.File]::WriteAllBytes($pending, [Text.Encoding]::UTF8.GetBytes('external-after-preflight'))
    }}
}} | Out-Null }} catch {{ $failed = $true }}
$paths = Get-LiliesTransactionPaths $target $base $journals {quote(identity)}
Result @{{ failed = $failed; newProgramStillPresent = (Test-LiliesOwnedTarget $paths) }}
""")
    assert result["failed"], "Retention must not silently absorb a late external change"
    assert result["newProgramStillPresent"]
    residue = links / "links" / f".lilies-{identity}-primary-replaced.pending"
    assert residue.read_bytes() == b"external-after-preflight"
    before_links, before_programs = inventory(links / "links"), inventory(links / "programs")
    run_ps(links, f"Restore-LiliesInstallTransaction @common -TransactionId {quote(identity)} -RollbackCommitted", code=1)
    assert inventory(links / "links") == before_links
    assert inventory(links / "programs") == before_programs
