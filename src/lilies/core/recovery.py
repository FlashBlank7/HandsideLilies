"""Bounded, content-free outcome for the standalone recovery entry points."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .desktop_peek import recover_desktop_peek


def finish_recovery_report(shell_result: Any, peek_directory: Path | None) -> dict[str, Any]:
    # Only explicit success is accepted; legacy None and truthy strings cannot
    # authorize an installer rollback or hide failed system restoration.
    shell_ok = isinstance(shell_result, dict) and shell_result.get("ok") is True
    report: dict[str, Any] = {"schemaVersion": 1, "ok": False, "shellRestored": shell_ok,
                              "desktopPeekRestored": False, "errors": []}
    if isinstance(shell_result, dict):
        # Carry only known, content-free observations, never arbitrary details
        # or an exception string from a caller/test adapter.
        report["shellState"] = {name: shell_result[name] for name in (
            "taskbarVisibilityMatched", "desktopIconsVisibilityMatched", "loginShellMatched",
            "explorerStartRequested") if type(shell_result.get(name)) is bool}
        backup_status = shell_result.get("backupStatus")
        if isinstance(backup_status, str) and backup_status in {
            "legacy", "validated", "missing", "invalid", "unreadable", "unavailable"
        }:
            report["shellState"]["backupStatus"] = backup_status
    if not shell_ok:
        report["errors"].append("shell-recovery-unverified")
    if peek_directory is None:
        report["errors"].append("peek-storage-unavailable")
        return report
    try:
        result = recover_desktop_peek(peek_directory)
        complete = (isinstance(result, dict) and result.get("active") is False
                    and type(result.get("failed")) is int and result["failed"] == 0)
        report["desktopPeekRestored"] = complete
        if not complete:
            report["errors"].append("peek-recovery-pending")
    except (OSError, RuntimeError, ValueError):
        # The original journal stays available for next-start recovery.
        report["errors"].append("peek-recovery-failed")
    report["ok"] = shell_ok and report["desktopPeekRestored"]
    return report
