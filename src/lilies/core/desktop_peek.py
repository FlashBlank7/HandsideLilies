from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from . import windows


JOURNAL_VERSION = 1
JOURNAL_RELATIVE_PATH = Path("runtime") / "desktop-peek-transaction.json"


class DesktopWindowApi(Protocol):
    """Injectable Win32 boundary; tests never touch the real desktop."""

    def list_handles(self) -> list[int]: ...

    def identity(self, handle: int) -> dict[str, Any] | None: ...

    def placement(self, handle: int) -> dict[str, Any] | None: ...

    def is_minimized(self, handle: int) -> bool: ...

    def minimize(self, handle: int, placement: dict[str, Any]) -> bool: ...

    def restore(self, handle: int, placement: dict[str, Any]) -> bool: ...

    def foreground(self) -> int: ...

    def set_foreground(self, handle: int) -> bool: ...


class NativeDesktopWindowApi:
    def list_handles(self) -> list[int]:
        return windows.enumerate_manageable_window_handles()

    def identity(self, handle: int) -> dict[str, Any] | None:
        return windows.window_identity(handle)

    def placement(self, handle: int) -> dict[str, Any] | None:
        return windows.get_window_placement(handle)

    def is_minimized(self, handle: int) -> bool:
        return windows.is_window_minimized(handle)

    def minimize(self, handle: int, placement: dict[str, Any]) -> bool:
        return windows.minimize_window_from_placement(handle, placement)

    def restore(self, handle: int, placement: dict[str, Any]) -> bool:
        return windows.set_window_placement(handle, placement)

    def foreground(self) -> int:
        return windows.foreground_window()

    def set_foreground(self, handle: int) -> bool:
        return windows.request_foreground_window(handle)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _identity_matches(expected: dict[str, Any], current: dict[str, Any] | None) -> bool:
    """Reject recycled HWNDs without requiring a title to remain unchanged."""
    if current is None:
        return False
    if int(expected.get("handle", 0)) != int(current.get("handle", 0)):
        return False
    if int(expected.get("processId", 0)) != int(current.get("processId", 0)):
        return False
    if str(expected.get("className", "")) != str(current.get("className", "")):
        return False

    expected_started = expected.get("processStarted")
    current_started = current.get("processStarted")
    if expected_started is not None:
        return current_started is not None and int(expected_started) == int(current_started)

    expected_executable = str(expected.get("executableHash", ""))
    if expected_executable:
        return expected_executable == str(current.get("executableHash", ""))

    # Access to elevated processes may deny both creation time and image path.
    # PID + class + a title hash is conservative enough for that fallback; a
    # title change simply causes a safe skip rather than restoring the wrong HWND.
    expected_title = str(expected.get("titleHash", ""))
    return bool(expected_title and expected_title == str(current.get("titleHash", "")))


def _validate_identity(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError("invalid window identity")
    handle = source.get("handle")
    process_id = source.get("processId")
    if isinstance(handle, bool) or not isinstance(handle, int) or handle <= 0:
        raise ValueError("invalid window handle")
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise ValueError("invalid process id")
    started = source.get("processStarted")
    if started is not None and (isinstance(started, bool) or not isinstance(started, int) or started < 0):
        raise ValueError("invalid process creation time")
    clean = {
        "handle": handle,
        "processId": process_id,
        "processStarted": started,
        "processName": str(source.get("processName", ""))[:260],
        "executableHash": str(source.get("executableHash", ""))[:128],
        "className": str(source.get("className", ""))[:256],
        "titleHash": str(source.get("titleHash", ""))[:128],
    }
    return clean


def _validate_transaction(source: Any) -> dict[str, Any]:
    if not isinstance(source, dict) or source.get("version") != JOURNAL_VERSION:
        raise ValueError("unsupported desktop peek journal")
    transaction_id = str(source.get("transactionId", ""))
    uuid.UUID(transaction_id)
    state = source.get("state")
    if state not in {"prepared", "peeking", "restoring", "recovery-pending"}:
        raise ValueError("invalid desktop peek state")
    raw_windows = source.get("windows")
    if not isinstance(raw_windows, list) or len(raw_windows) > 512:
        raise ValueError("invalid desktop peek window list")
    entries: list[dict[str, Any]] = []
    for raw in raw_windows:
        if not isinstance(raw, dict):
            raise ValueError("invalid desktop peek window entry")
        placement = raw.get("placement")
        windows.placement_from_dict(placement)
        z_order = raw.get("zOrder")
        if isinstance(z_order, bool) or not isinstance(z_order, int) or not 0 <= z_order < 512:
            raise ValueError("invalid desktop peek z-order")
        entries.append(
            {
                "identity": _validate_identity(raw.get("identity")),
                "placement": placement,
                "zOrder": z_order,
                "attempted": bool(raw.get("attempted", False)),
                "minimizedByLilies": bool(raw.get("minimizedByLilies", False)),
            }
        )
    foreground = source.get("foreground")
    return {
        "version": JOURNAL_VERSION,
        "transactionId": transaction_id,
        "state": state,
        "createdAt": str(source.get("createdAt", ""))[:64],
        "updatedAt": str(source.get("updatedAt", ""))[:64],
        "foreground": _validate_identity(foreground) if foreground is not None else None,
        "windows": entries,
    }


class DesktopPeekService:
    """Two-state, crash-recoverable desktop peek transaction.

    Construction and ``status`` are read-only. The application explicitly calls
    ``recover_pending`` during its recovery phase, so importing this module can
    never alter the user's windows.
    """

    def __init__(self, data_directory: Path | str, api: DesktopWindowApi | None = None) -> None:
        self.data_directory = Path(data_directory).resolve()
        self.journal_path = self.data_directory / JOURNAL_RELATIVE_PATH
        self.api: DesktopWindowApi = api or NativeDesktopWindowApi()
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            try:
                transaction = self._load()
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                return {
                    "active": False,
                    "recoverable": False,
                    "windowCount": 0,
                    "error": f"invalid-journal: {exc}",
                }
            if transaction is None:
                return {"active": False, "recoverable": False, "windowCount": 0}
            count = sum(
                1
                for item in transaction["windows"]
                if item["attempted"] or item["minimizedByLilies"]
            )
            return {
                "active": True,
                "recoverable": count > 0,
                "windowCount": count,
                "state": transaction["state"],
                "transactionId": transaction["transactionId"],
                "createdAt": transaction["createdAt"],
            }

    def toggle(self) -> dict[str, Any]:
        with self._lock:
            if self._load() is not None:
                return self._restore_locked(recovery=False)
            return self._peek_locked()

    def peek(self) -> dict[str, Any]:
        with self._lock:
            if self._load() is not None:
                raise RuntimeError("desktop peek is already active")
            return self._peek_locked()

    def restore(self) -> dict[str, Any]:
        with self._lock:
            return self._restore_locked(recovery=False)

    def recover_pending(self) -> dict[str, Any]:
        """Restore a previous process's transaction after a crash."""
        with self._lock:
            return self._restore_locked(recovery=True)

    def _peek_locked(self) -> dict[str, Any]:
        handles = self.api.list_handles()
        foreground_handle = self.api.foreground()
        foreground_identity = self.api.identity(foreground_handle) if foreground_handle else None
        entries: list[dict[str, Any]] = []
        for z_order, handle in enumerate(handles[:512]):
            try:
                if self.api.is_minimized(handle):
                    continue
                identity = self.api.identity(handle)
                placement = self.api.placement(handle)
                if identity is None or placement is None:
                    continue
                # Validation happens before any window is touched.
                identity = _validate_identity(identity)
                windows.placement_from_dict(placement)
            except (OSError, ValueError):
                continue
            entries.append(
                {
                    "identity": identity,
                    "placement": placement,
                    "zOrder": z_order,
                    "attempted": False,
                    "minimizedByLilies": False,
                }
            )

        if not entries:
            return {"active": False, "minimized": 0, "skipped": len(handles)}

        transaction = {
            "version": JOURNAL_VERSION,
            "transactionId": str(uuid.uuid4()),
            "state": "prepared",
            "createdAt": _utc_now(),
            "updatedAt": _utc_now(),
            "foreground": _validate_identity(foreground_identity) if foreground_identity else None,
            "windows": entries,
        }
        # Persist all original placements before the first SetWindowPlacement.
        self._write(transaction)
        minimized = 0
        for entry in entries:
            entry["attempted"] = True
            transaction["state"] = "peeking"
            transaction["updatedAt"] = _utc_now()
            self._write(transaction)
            handle = int(entry["identity"]["handle"])
            try:
                changed = bool(self.api.minimize(handle, entry["placement"]))
            except OSError:
                changed = False
            entry["minimizedByLilies"] = changed
            if not changed:
                entry["attempted"] = False
            else:
                minimized += 1
            transaction["updatedAt"] = _utc_now()
            self._write(transaction)

        if not minimized:
            self._clear()
            return {"active": False, "minimized": 0, "skipped": len(handles)}
        return {
            "active": True,
            "minimized": minimized,
            "skipped": len(handles) - minimized,
            "transactionId": transaction["transactionId"],
        }

    def _restore_locked(self, recovery: bool) -> dict[str, Any]:
        transaction = self._load()
        if transaction is None:
            return {"active": False, "restored": 0, "skipped": 0, "failed": 0}
        transaction["state"] = "restoring"
        transaction["updatedAt"] = _utc_now()
        self._write(transaction)

        restored = 0
        skipped = 0
        remaining: list[dict[str, Any]] = []
        # SetWindowPlacement generally raises a restored window in Z order. Work
        # from the original bottom to top so touched windows retain their relative
        # hierarchy, then request only the original foreground window once.
        ordered = sorted(transaction["windows"], key=lambda item: item["zOrder"], reverse=True)
        for entry in ordered:
            if not (entry["attempted"] or entry["minimizedByLilies"]):
                skipped += 1
                continue
            handle = int(entry["identity"]["handle"])
            try:
                current_identity = self.api.identity(handle)
                if not _identity_matches(entry["identity"], current_identity):
                    skipped += 1
                    continue
                # A user may have manually restored a window while peeking. Such
                # a window is deliberately left exactly as the user set it.
                if not self.api.is_minimized(handle):
                    skipped += 1
                    continue
                if self.api.restore(handle, entry["placement"]):
                    restored += 1
                else:
                    remaining.append(entry)
            except (OSError, ValueError):
                remaining.append(entry)

        foreground = transaction.get("foreground")
        focus_restored = False
        if foreground is not None:
            handle = int(foreground["handle"])
            try:
                if (
                    _identity_matches(foreground, self.api.identity(handle))
                    and not self.api.is_minimized(handle)
                ):
                    focus_restored = bool(self.api.set_foreground(handle))
            except OSError:
                focus_restored = False

        if remaining:
            transaction["windows"] = sorted(remaining, key=lambda item: item["zOrder"])
            transaction["state"] = "recovery-pending"
            transaction["updatedAt"] = _utc_now()
            self._write(transaction)
        else:
            self._clear()
        return {
            "active": bool(remaining),
            "restored": restored,
            "skipped": skipped,
            "failed": len(remaining),
            "focusRestored": focus_restored,
            "recovery": recovery,
            "transactionId": transaction["transactionId"],
        }

    def _load(self) -> dict[str, Any] | None:
        if not self.journal_path.exists():
            return None
        with self.journal_path.open("r", encoding="utf-8") as stream:
            return _validate_transaction(json.load(stream))

    def _write(self, transaction: dict[str, Any]) -> None:
        validated = _validate_transaction(transaction)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.journal_path.with_name(
            f".{self.journal_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(validated, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.journal_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _clear(self) -> None:
        self.journal_path.unlink(missing_ok=True)


def recover_desktop_peek(data_directory: Path | str) -> dict[str, Any]:
    """Monitor/watchdog entry point with no dependency on Qt."""
    return DesktopPeekService(data_directory).recover_pending()


__all__ = [
    "DesktopPeekService",
    "DesktopWindowApi",
    "NativeDesktopWindowApi",
    "recover_desktop_peek",
]
