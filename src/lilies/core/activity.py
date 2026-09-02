from __future__ import annotations

"""Low-intrusion activity context primitives.

The module deliberately does not start a hook or take a screenshot at import
time.  The application owns the lifecycle and must explicitly call ``start``
or ``CaptureStaging.stage`` after the user enables observation.
"""

import contextlib
import ctypes
import hashlib
import math
import os
import re
import shutil
import threading
import time
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator, Protocol

from PIL import Image, ImageGrab, ImageStat


EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
PM_REMOVE = 0x0001
MONITOR_DEFAULTTONEAREST = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GWL_STYLE = -16
ES_PASSWORD = 0x0020
_TITLE_FINGERPRINT_KEY = os.urandom(32)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class ObservationPolicy(str, Enum):
    """Effective policy for one application."""

    BLOCKED = "blocked"
    SIGNAL_ONLY = "signal"
    OBSERVE = "observe"
    ALLOW_BUBBLE = "bubble"


@dataclass(frozen=True, slots=True)
class ForegroundContext:
    hwnd: int
    process_id: int = 0
    process_name: str = ""
    window_class: str = ""
    title: str = ""
    changed_at: float = 0.0
    full_screen: bool = False
    is_game: bool = False
    is_password: bool = False
    is_protected: bool = False
    scene_label: str = ""

    @property
    def app_key(self) -> str:
        return self.process_name.strip().casefold() or f"pid:{self.process_id}"

    def public_metadata(self) -> dict[str, object]:
        """Return the context safe for status UI and model input.

        Raw titles and their in-memory fingerprint are both intentionally
        absent.  They exist only long enough to enforce privacy/stability.
        """

        return {
            "process": self.process_name,
            "windowClass": self.window_class,
            "fullScreen": self.full_screen,
            "sceneLabel": self.scene_label,
        }


@dataclass(frozen=True, slots=True)
class ObservationDecision:
    policy: ObservationPolicy
    reason: str
    can_capture: bool
    can_bubble: bool
    context_type: str
    stable_seconds: float = 0.0
    idle_seconds: float = 0.0

    @classmethod
    def denied(
        cls,
        reason: str,
        *,
        policy: ObservationPolicy = ObservationPolicy.BLOCKED,
        stable_seconds: float = 0.0,
        idle_seconds: float = 0.0,
    ) -> "ObservationDecision":
        return cls(policy, reason, False, False, "signal", stable_seconds, idle_seconds)


def sanitize_window_title(value: str, limit: int = 120) -> str:
    text = str(value or "")
    # Lilies currently runs on Windows.  Restrict path redaction to drive and
    # UNC forms so the slashes in an HTTPS URL cannot be mistaken for a path.
    text = re.sub(r"(?:[A-Za-z]:\\|\\\\)[^\s|]+", "[path]", text)
    text = re.sub(r"https?://\S+", "[url]", text, flags=re.IGNORECASE)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    text = re.sub(r"\b\d{7,}\b", "[number]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def ephemeral_title_fingerprint(value: str) -> str:
    """Return a process-keyed, in-memory-only fingerprint of a raw title.

    The random key is never persisted, so the digest cannot be correlated
    across launches or used as a stable dictionary of document names.
    """

    normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()[:4096]
    if not normalized:
        return ""
    return hashlib.blake2b(
        normalized.encode("utf-8", errors="ignore"),
        key=_TITLE_FINGERPRINT_KEY,
        digest_size=16,
    ).hexdigest()


_PASSWORD_PROCESSES = {
    "1password.exe",
    "bitwarden.exe",
    "credentialuibroker.exe",
    "dashlane.exe",
    "keepass.exe",
    "keepassxc.exe",
    "lastpass.exe",
}
_REMOTE_PROCESSES = {
    "anydesk.exe",
    "mstsc.exe",
    "parsecd.exe",
    "rustdesk.exe",
    "sunloginclient.exe",
    "teamviewer.exe",
    "todesk.exe",
}
_MEETING_PROCESSES = {
    "ciscocollabhost.exe",
    "skype.exe",
    "teams.exe",
    "webex.exe",
    "zoom.exe",
}
_LILIES_PROCESSES = {"liliesinthebox.exe"}
_PRIVATE_TITLE = re.compile(r"(?:incognito|inprivate|private browsing|无痕|隐私浏览)", re.I)
_PAYMENT_TITLE = re.compile(
    r"(?:checkout|payment|credit card|banking|付款|支付|收银台|银行卡)", re.I
)
_UAC_CLASSES = {"credential dialog xaml host", "#32770:consent"}


class SensitiveWindowGuard:
    """Central, deterministic policy evaluation for foreground windows."""

    def __init__(self, app_policies: dict[str, str | ObservationPolicy] | None = None) -> None:
        self._app_policies: dict[str, ObservationPolicy] = {}
        for app, policy in (app_policies or {}).items():
            self.set_policy(app, policy)

    @staticmethod
    def application_key(app: str) -> str:
        """Return a title-free policy identity suitable for local settings UI."""

        key = Path(str(app)).name.strip().casefold()
        if not key:
            raise ValueError("application name cannot be empty")
        return key

    @classmethod
    def is_safety_locked(cls, app: str) -> bool:
        """Whether immutable privacy rules keep this application silent."""

        process = cls.application_key(app)
        return process in (
            _PASSWORD_PROCESSES
            | _REMOTE_PROCESSES
            | _MEETING_PROCESSES
            | _LILIES_PROCESSES
            | {"consent.exe", "credentialuibroker.exe"}
        )

    def set_policy(self, app: str, policy: str | ObservationPolicy) -> None:
        key = self.application_key(app)
        requested = ObservationPolicy(policy)
        # A user policy may become stricter, never weaker than an immutable
        # password/security/meeting/remote-desktop default.  The evaluate()
        # checks below remain the final fail-closed authority as well.
        if self.is_safety_locked(key) and requested is not ObservationPolicy.BLOCKED:
            requested = ObservationPolicy.BLOCKED
        self._app_policies[key] = requested

    def remove_policy(self, app: str) -> None:
        self._app_policies.pop(self.application_key(app), None)

    def policies(self) -> dict[str, str]:
        return {key: value.value for key, value in self._app_policies.items()}

    def evaluate(self, context: ForegroundContext) -> ObservationDecision:
        process = Path(context.process_name).name.casefold()
        title = context.title
        window_class = context.window_class.casefold()

        if not context.hwnd:
            return ObservationDecision.denied("no-foreground-window")
        if context.is_password or context.is_protected:
            return ObservationDecision.denied("protected-content")
        if process in _PASSWORD_PROCESSES:
            return ObservationDecision.denied("password-manager")
        if process in {"consent.exe", "credentialuibroker.exe"} or window_class in _UAC_CLASSES:
            return ObservationDecision.denied("security-dialog")
        if process in _REMOTE_PROCESSES:
            return ObservationDecision.denied("remote-desktop")
        if process in _MEETING_PROCESSES:
            return ObservationDecision.denied("meeting")
        if process in _LILIES_PROCESSES:
            return ObservationDecision.denied("assistant-ui")
        if _PRIVATE_TITLE.search(title):
            return ObservationDecision.denied("private-browsing")
        if _PAYMENT_TITLE.search(title):
            return ObservationDecision.denied("payment-window")

        configured = self._app_policies.get(process)
        if configured is None and context.full_screen and context.is_game:
            configured = ObservationPolicy.SIGNAL_ONLY
        policy = configured or ObservationPolicy.ALLOW_BUBBLE
        if policy is ObservationPolicy.BLOCKED:
            return ObservationDecision.denied("application-blocked")
        if policy is ObservationPolicy.SIGNAL_ONLY:
            return ObservationDecision.denied(
                "signals-only", policy=ObservationPolicy.SIGNAL_ONLY
            )
        return ObservationDecision(
            policy=policy,
            reason="allowed",
            can_capture=True,
            can_bubble=policy is ObservationPolicy.ALLOW_BUBBLE,
            context_type="active-window-image",
        )


class IdleProvider(Protocol):
    def idle_seconds(self) -> float: ...


class ForegroundEventSource(Protocol):
    def start(self, callback: Callable[[int], None]) -> None: ...

    def stop(self) -> None: ...


@dataclass(slots=True)
class ActivityContextService:
    """Event-driven observation eligibility state machine.

    ``consider_observation`` is intentionally side-effect free except for
    updating the exported status.  Capturing and model calls are separate,
    explicit steps owned by the application controller.
    """

    context_reader: Callable[[int], ForegroundContext]
    idle_provider: IdleProvider
    event_source: ForegroundEventSource | None = None
    guard: SensitiveWindowGuard = field(default_factory=SensitiveWindowGuard)
    stable_seconds: float = 120.0
    minimum_idle_seconds: float = 6.0
    maximum_idle_seconds: float = 60.0
    cooldown_seconds: float = 25.0 * 60.0
    clock: Callable[[], float] = time.monotonic
    enabled: bool = False
    paused: bool = False
    _context: ForegroundContext | None = field(default=None, init=False, repr=False)
    _last_observation_at: float = field(default=-math.inf, init=False, repr=False)
    _last_context_type: str = field(default="none", init=False, repr=False)
    _last_reason: str = field(default="not-started", init=False, repr=False)
    _last_stable_seconds: float = field(default=0.0, init=False, repr=False)
    _last_idle_seconds: float = field(default=0.0, init=False, repr=False)
    _title_fingerprint: str = field(default="", init=False, repr=False)

    def start(self) -> None:
        self.enabled = True
        self._last_reason = "waiting-for-foreground"
        if self.event_source is not None:
            self.event_source.start(self._on_foreground)

    def stop(self) -> None:
        if self.event_source is not None:
            self.event_source.stop()
        self.enabled = False
        self._last_reason = "stopped"

    def set_paused(self, value: bool) -> None:
        self.paused = bool(value)
        self._last_reason = "paused" if self.paused else "waiting"

    def set_policy(self, app: str, policy: str | ObservationPolicy) -> None:
        self.guard.set_policy(app, policy)

    def remove_policy(self, app: str) -> None:
        self.guard.remove_policy(app)

    def _on_foreground(self, hwnd: int) -> None:
        self.update_foreground(self.context_reader(int(hwnd)))

    def update_foreground(self, context: ForegroundContext) -> bool:
        """Replace the ephemeral foreground context.

        The return value tells the controller whether the window identity
        changed.  Native foreground hooks are intentionally lossy during
        sleep/resume and while Lilies' own no-activate windows are closing, so
        callers may safely use this method for a bounded reconciliation read
        as well as for normal WinEvent delivery.
        """

        now = self.clock()
        previous_identity = None if self._context is None else (
            self._context.hwnd,
            self._context.process_id,
            self._context.process_name.casefold(),
            self._context.window_class.casefold(),
        )
        current_identity = (
            context.hwnd,
            context.process_id,
            context.process_name.casefold(),
            context.window_class.casefold(),
        )
        identity_changed = previous_identity != current_identity
        current_title_fingerprint = ephemeral_title_fingerprint(context.title)
        title_changed = bool(
            self._context is not None
            and not identity_changed
            and self._title_fingerprint != current_title_fingerprint
        )
        changed = identity_changed or title_changed
        # Empty is a real title state, not an absence of evidence.  A WPS or
        # browser tab can replace a named document with an untitled/protected
        # surface while reusing the same HWND.  Always remember that
        # transition so the 120-second stability gate restarts before any
        # screenshot can be considered.
        self._title_fingerprint = current_title_fingerprint
        if changed:
            context = replace(context, changed_at=now)
        elif context.changed_at <= 0:
            context = replace(context, changed_at=self._context.changed_at)
        self._context = context
        policy = self.guard.evaluate(context)
        self._last_reason = "stabilizing" if policy.can_bubble else policy.reason
        self._last_stable_seconds = 0.0 if changed else max(
            0.0, now - context.changed_at
        )
        return changed

    def consider_observation(self, now: float | None = None) -> ObservationDecision:
        current_time = self.clock() if now is None else float(now)
        context = self._context
        if not self.enabled:
            decision = ObservationDecision.denied("disabled")
        elif self.paused:
            decision = ObservationDecision.denied("paused")
        elif context is None:
            decision = ObservationDecision.denied("no-context")
        else:
            stable = max(0.0, current_time - context.changed_at)
            idle = max(0.0, float(self.idle_provider.idle_seconds()))
            policy_decision = self.guard.evaluate(context)
            if not policy_decision.can_capture:
                decision = replace(policy_decision, stable_seconds=stable, idle_seconds=idle)
            elif stable < self.stable_seconds:
                decision = ObservationDecision.denied(
                    "window-not-stable", stable_seconds=stable, idle_seconds=idle
                )
            elif idle < self.minimum_idle_seconds:
                decision = ObservationDecision.denied(
                    "user-active", stable_seconds=stable, idle_seconds=idle
                )
            elif idle > self.maximum_idle_seconds:
                decision = ObservationDecision.denied(
                    "user-away", stable_seconds=stable, idle_seconds=idle
                )
            elif current_time - self._last_observation_at < self.cooldown_seconds:
                decision = ObservationDecision.denied(
                    "cooldown", stable_seconds=stable, idle_seconds=idle
                )
            else:
                decision = replace(
                    policy_decision, stable_seconds=stable, idle_seconds=idle
                )
        self._last_reason = decision.reason
        self._last_stable_seconds = float(decision.stable_seconds)
        self._last_idle_seconds = float(decision.idle_seconds)
        return decision

    def mark_observation_sent(self, context_type: str = "active-window-image") -> None:
        self._last_observation_at = self.clock()
        self._last_context_type = str(context_type)
        self._last_reason = "sent"

    def status(self) -> dict[str, object]:
        now = self.clock()
        cooldown_remaining = max(
            0.0,
            self.cooldown_seconds - (now - self._last_observation_at),
        )
        return {
            "enabled": self.enabled,
            "paused": self.paused,
            "state": self._last_reason,
            "lastContextType": self._last_context_type,
            "stableSeconds": round(self._last_stable_seconds, 1),
            "idleSeconds": round(self._last_idle_seconds, 1),
            "requiredStableSeconds": round(self.stable_seconds, 1),
            "naturalPauseMinimumSeconds": round(self.minimum_idle_seconds, 1),
            "naturalPauseMaximumSeconds": round(self.maximum_idle_seconds, 1),
            "cooldownRemainingSeconds": round(cooldown_remaining, 1),
            "applicationPolicies": self.guard.policies(),
            "foreground": self._context.public_metadata() if self._context else None,
        }

    @property
    def context_identity(self) -> str:
        """Return a title-free token used to reject stale model results."""

        context = self._context
        if context is None:
            return ""
        return (
            f"{context.process_id}:{context.hwnd}:"
            f"{context.process_name.casefold()}:{context.window_class.casefold()}"
        )

    @property
    def current_context(self) -> ForegroundContext | None:
        """Expose the short-lived in-memory context to the opted-in controller."""

        return self._context


class CaptureFunction(Protocol):
    def __call__(self, hwnd: int, destination: Path) -> None: ...


@dataclass(slots=True)
class StagedCapture:
    path: Path
    library_root: Path
    _keep: bool = False
    _image_bytes: bytes | None = None
    _bytes_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

    # A 1600 px RGBA frame is about 10 MiB before PNG compression.  Keeping a
    # hard ceiling here makes the privacy promise independent of encoder
    # behaviour and prevents an unexpectedly inefficient PNG from living in
    # the companion bubble for its whole lifetime.
    MAX_RETAINED_BYTES = 16 * 1024 * 1024

    def retain_in_memory(self) -> bytes:
        """Move the compressed capture out of staging for bubble lifetime.

        The model needs a local path only while its request is active.  Once it
        returns, the controller calls this method; the staging file disappears
        and “保存此刻” can still persist the in-memory PNG later.
        """

        with self._bytes_lock:
            if self._image_bytes is None:
                try:
                    size = self.path.stat().st_size
                except OSError as exc:
                    raise CaptureStorageError(
                        "capture staging is unavailable"
                    ) from exc
                if size <= 0 or size > self.MAX_RETAINED_BYTES:
                    raise CaptureStorageError(
                        "capture exceeds the in-memory retention bound"
                    )
                try:
                    payload = self.path.read_bytes()
                except OSError as exc:
                    raise CaptureStorageError(
                        "capture staging is unavailable"
                    ) from exc
                if len(payload) != size or len(payload) > self.MAX_RETAINED_BYTES:
                    raise CaptureStorageError(
                        "capture exceeds the in-memory retention bound"
                    )
                self._image_bytes = payload
            payload = self._image_bytes
        self.cleanup()
        assert payload is not None
        return payload

    def save(self, name: str | None = None) -> Path:
        """Persist this moment after an explicit user action."""

        self.library_root.mkdir(parents=True, exist_ok=True)
        suffix = self.path.suffix.casefold() or ".png"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name or self.path.stem).strip(".-")
        target = self.library_root / f"{safe_name or uuid.uuid4().hex}{suffix}"
        if target.exists():
            target = self.library_root / f"{target.stem}-{uuid.uuid4().hex[:8]}{suffix}"
        if self.path.is_file():
            shutil.copy2(self.path, target)
        else:
            with self._bytes_lock:
                payload = self._image_bytes
            if payload is not None:
                target.write_bytes(payload)
            else:
                raise RuntimeError("capture is no longer available")
        self._keep = True
        return target

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            # The staging directory is quarantined and startup cleanup retries
            # any orphan.  A transient sharing lock must not crash shutdown or
            # hide the model/capture error that caused this cleanup path.
            pass

    def release(self) -> None:
        """Release both disk and memory state when a bubble expires."""

        self.cleanup()
        with self._bytes_lock:
            self._image_bytes = None

    def __enter__(self) -> "StagedCapture":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.cleanup()


class CaptureStaging:
    """Owns the strict temporary lifecycle for opt-in active-window images."""

    def __init__(self, root: Path, library_root: Path, max_edge: int = 1600) -> None:
        self.root = Path(root)
        self.library_root = Path(library_root)
        self.max_edge = max(256, min(int(max_edge), 4096))
        self.last_cleanup_failures = 0

    def stage(self, hwnd: int, capture: CaptureFunction) -> StagedCapture:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"capture-{uuid.uuid4().hex}.png"
        try:
            capture(int(hwnd), path)
            if not path.is_file():
                raise RuntimeError("capture function did not create an image")
            self._resize_in_place(path)
            return StagedCapture(path, self.library_root)
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def stage_image(
        self,
        hwnd: int,
        image: Image.Image,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> StagedCapture:
        """Encode/resize a GUI-thread grab on the calling worker thread."""

        return self.stage(
            hwnd,
            lambda _hwnd, destination: encode_window_image_png(
                image,
                destination,
                max_edge=self.max_edge,
                cancelled=cancelled,
            ),
        )

    @contextlib.contextmanager
    def prepared(self, hwnd: int, capture: CaptureFunction) -> Iterator[StagedCapture]:
        staged = self.stage(hwnd, capture)
        try:
            yield staged
        finally:
            staged.cleanup()

    def cleanup_stale(self, older_than_seconds: float = 3600.0) -> int:
        self.last_cleanup_failures = 0
        try:
            if not self.root.exists():
                return 0
            candidates = tuple(self.root.glob("capture-*.png"))
        except OSError:
            # Startup recovery must not be held hostage by an antivirus scan,
            # a briefly unavailable F: drive directory, or one locked orphan.
            self.last_cleanup_failures = 1
            return 0
        threshold = time.time() - max(0.0, older_than_seconds)
        removed = 0
        for path in candidates:
            try:
                if path.stat().st_mtime <= threshold:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                # Leave the file quarantined under the staging-only name.  It
                # is never adopted by a later observation and a future cleanup
                # pass can retry deletion.
                self.last_cleanup_failures += 1
                continue
        return removed

    def _resize_in_place(self, path: Path) -> None:
        with Image.open(path) as image:
            width, height = image.size
            longest = max(width, height)
            if longest <= self.max_edge:
                image.load()
                return
            scale = self.max_edge / longest
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            output = image.resize(size, Image.Resampling.LANCZOS)
            output.save(path, format="PNG", optimize=True)


class CaptureCancelled(RuntimeError):
    """Raised when an opted-in capture is revoked while being encoded."""


class ProtectedCaptureContent(RuntimeError):
    """The compositor returned a protected/black placeholder, not evidence."""


class LowInformationCapture(RuntimeError):
    """The compositor returned a near-uniform frame with no usable evidence."""


class CaptureStorageError(RuntimeError):
    """The bounded PNG could not be written to the private staging area."""


class CaptureEncodeError(RuntimeError):
    """The in-memory window image could not be validated or encoded."""


def capture_window_image(hwnd: int) -> Image.Image:
    """Perform only the minimal HWND pixel grab on the GUI thread."""

    if os.name != "nt" or not int(hwnd):
        raise RuntimeError("active-window capture is only available on Windows")
    try:
        image = ImageGrab.grab(window=int(hwnd))
    except TypeError as exc:
        # Never fall back to a desktop-sized screenshot on an older Pillow;
        # application-level signals are the privacy-preserving fallback.
        raise RuntimeError("current Pillow runtime cannot capture one HWND") from exc
    if image.width <= 1 or image.height <= 1:
        image.close()
        raise RuntimeError("active window returned an empty capture")
    return image


def capture_window_image_via_print(
    hwnd: int,
    *,
    expected_process_id: int,
    timeout_ms: int = 750,
) -> Image.Image:
    """Ask exactly one HWND to paint its client area into a private DIB.

    This is a bounded fallback for compositor surfaces that return a uniform
    frame through ``ImageGrab``.  It never samples the desktop or another
    window. ``SendMessageTimeoutW`` keeps an unresponsive target from hanging
    the companion worker indefinitely; callers must still run this outside
    the Qt GUI thread and apply the normal foreground-identity fences.
    """

    if os.name != "nt" or not int(hwnd):
        raise RuntimeError("native window print is only available on Windows")
    expected_pid = int(expected_process_id)
    if expected_pid <= 0:
        raise ValueError("expected process id must be positive")
    timeout = max(100, min(int(timeout_ms), 2_000))
    user32 = _user32()
    gdi32 = _gdi32()
    native_hwnd = wintypes.HWND(int(hwnd))

    def validate_target() -> None:
        """Reject stale, hidden, or recycled handles on both sides of capture."""

        if not user32.IsWindow(native_hwnd):
            raise RuntimeError("target window handle is unavailable")
        if not user32.IsWindowVisible(native_hwnd):
            raise RuntimeError("target window is not visible")
        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(native_hwnd, ctypes.byref(process_id)):
            raise RuntimeError("target window process is unavailable")
        if int(process_id.value) != expected_pid:
            raise RuntimeError("target window process changed during capture")
        # DWM cloaking is a stronger invisibility signal than IsWindowVisible.
        # Treat only a successful, explicit cloaked result as authoritative:
        # DWM may be unavailable or reject the query on older/restricted hosts.
        if _window_is_explicitly_cloaked(native_hwnd):
            raise RuntimeError("target window is cloaked")

    validate_target()
    client = RECT()
    if not user32.GetClientRect(native_hwnd, ctypes.byref(client)):
        raise RuntimeError("target window client area is unavailable")
    width = int(client.right - client.left)
    height = int(client.bottom - client.top)
    # 12M pixels covers a full 4K client area (8.3M pixels) with headroom for
    # window chrome/DPI rounding while bounding the 32-bit DIB near 48 MB.
    if (
        width <= 1
        or height <= 1
        or width > 8_192
        or height > 8_192
        or width * height > 12_000_000
    ):
        raise RuntimeError("target window client area is outside capture bounds")

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    # A negative height creates a top-down DIB, matching Pillow's row order.
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB
    bits = ctypes.c_void_p()
    memory_dc = gdi32.CreateCompatibleDC(None)
    if not memory_dc:
        raise RuntimeError("native window print DC is unavailable")
    bitmap = wintypes.HANDLE()
    previous = wintypes.HANDLE()
    try:
        bitmap = gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(info),
            0,  # DIB_RGB_COLORS
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits.value:
            raise RuntimeError("native window print bitmap is unavailable")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or int(previous) == int(ctypes.c_void_p(-1).value):
            previous = wintypes.HANDLE()
            raise RuntimeError("native window print bitmap selection failed")
        byte_count = width * height * 4
        ctypes.memset(bits, 0, byte_count)
        result = ctypes.c_size_t()
        sent = user32.SendMessageTimeoutW(
            native_hwnd,
            0x0317,  # WM_PRINT
            wintypes.WPARAM(int(memory_dc)),
            wintypes.LPARAM(
                0x00000004  # PRF_CLIENT
                | 0x00000008  # PRF_ERASEBKGND
                | 0x00000010  # PRF_CHILDREN
                | 0x00000001  # PRF_CHECKVISIBLE
            ),
            0x0001 | 0x0002 | 0x0020,
            # SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT
            timeout,
            ctypes.byref(result),
        )
        if not sent:
            raise RuntimeError("native window print timed out or was rejected")
        validate_target()
        pixels = ctypes.string_at(bits, byte_count)
        return Image.frombuffer(
            "RGB",
            (width, height),
            pixels,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
    finally:
        if previous and memory_dc:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)


def capture_window_image_via_print_window(
    hwnd: int,
    *,
    expected_process_id: int,
) -> Image.Image:
    """Render one foreign HWND client area through the User32 broker.

    ``WM_PRINT`` with a caller-created HDC is reliable for the self-owned
    diagnostic window but a raw GDI handle is not a sound cross-process
    contract. ``PrintWindow`` is the public cross-process API that asks the
    target application to render into the caller's DC.  It is synchronous, so
    production invokes this function only inside the short-lived helper
    process; the parent owns the whole-process deadline and can terminate a
    hung target without blocking Lilies' Qt or worker threads.
    """

    if os.name != "nt" or not int(hwnd):
        raise RuntimeError("native PrintWindow capture is only available on Windows")
    expected_pid = int(expected_process_id)
    if expected_pid <= 0:
        raise ValueError("expected process id must be positive")
    user32 = _user32()
    gdi32 = _gdi32()
    native_hwnd = wintypes.HWND(int(hwnd))

    def validate_target() -> None:
        if not user32.IsWindow(native_hwnd):
            raise RuntimeError("target window handle is unavailable")
        if not user32.IsWindowVisible(native_hwnd):
            raise RuntimeError("target window is not visible")
        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(native_hwnd, ctypes.byref(process_id)):
            raise RuntimeError("target window process is unavailable")
        if int(process_id.value) != expected_pid:
            raise RuntimeError("target window process changed during capture")
        if _window_is_explicitly_cloaked(native_hwnd):
            raise RuntimeError("target window is cloaked")

    validate_target()
    client = RECT()
    if not user32.GetClientRect(native_hwnd, ctypes.byref(client)):
        raise RuntimeError("target window client area is unavailable")
    width = int(client.right - client.left)
    height = int(client.bottom - client.top)
    if (
        width <= 1
        or height <= 1
        or width > 8_192
        or height > 8_192
        or width * height > 12_000_000
    ):
        raise RuntimeError("target window client area is outside capture bounds")

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    memory_dc = gdi32.CreateCompatibleDC(None)
    if not memory_dc:
        raise RuntimeError("native PrintWindow DC is unavailable")
    bitmap = wintypes.HANDLE()
    previous = wintypes.HANDLE()
    try:
        bitmap = gdi32.CreateDIBSection(
            memory_dc,
            ctypes.byref(info),
            0,
            ctypes.byref(bits),
            None,
            0,
        )
        if not bitmap or not bits.value:
            raise RuntimeError("native PrintWindow bitmap is unavailable")
        previous = gdi32.SelectObject(memory_dc, bitmap)
        if not previous or int(previous) == int(ctypes.c_void_p(-1).value):
            previous = wintypes.HANDLE()
            raise RuntimeError("native PrintWindow bitmap selection failed")
        byte_count = width * height * 4
        ctypes.memset(bits, 0, byte_count)
        if not user32.PrintWindow(
            native_hwnd,
            memory_dc,
            0x00000001,
            # PW_CLIENTONLY.  The undocumented render-full-content flag can
            # return an intermittent compositor placeholder for off-screen
            # windows; client-only asks the target to paint deterministically.
        ):
            raise RuntimeError("native PrintWindow capture was rejected")
        validate_target()
        pixels = ctypes.string_at(bits, byte_count)
        return Image.frombuffer(
            "RGB",
            (width, height),
            pixels,
            "raw",
            "BGRX",
            0,
            1,
        ).copy()
    finally:
        if previous and memory_dc:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)


def encode_window_image_png(
    image: Image.Image,
    destination: Path,
    *,
    max_edge: int | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Validate, resize and encode a captured image away from the Qt thread."""

    def check_cancelled() -> None:
        if cancelled is not None and cancelled():
            raise CaptureCancelled("capture encoding cancelled")

    check_cancelled()
    try:
        sample = image.convert("L").resize((32, 32), Image.Resampling.BILINEAR)
    except (OSError, RuntimeError, ValueError) as exc:
        # Do not let Pillow's exception text (which may include a path or
        # decoder detail) escape into the persistent capture diagnostic.
        raise CaptureEncodeError("capture sample could not be encoded") from exc
    try:
        statistics = ImageStat.Stat(sample)
    except (OSError, RuntimeError, ValueError) as exc:
        raise CaptureEncodeError("capture sample could not be measured") from exc
    finally:
        sample.close()
    minimum, maximum = statistics.extrema[0]
    dynamic_range = float(maximum) - float(minimum)
    standard_deviation = float(statistics.stddev[0])
    if float(statistics.mean[0]) < 1.0 and float(maximum) < 4.0:
        raise ProtectedCaptureContent("active window returned protected content")
    # Protected surfaces are not always black.  Some compositors return an
    # almost uniform white/grey placeholder that contains no visual evidence
    # but would tempt a vision model to invent an anchor.  Keep the threshold
    # deliberately narrow: a white document with even a modest diagram or
    # text block has substantially more range/variance and passes.
    # Reject only genuinely near-uniform compositor placeholders.  A mostly
    # white paper page may contain just a few thin glyph strokes; using a
    # broader variance threshold would incorrectly discard that useful but
    # intentionally sparse evidence after the 32px sampling step.
    if dynamic_range < 2.0 and standard_deviation < 0.15:
        raise LowInformationCapture("active window returned low-information content")
    check_cancelled()
    output = image
    if max_edge is not None and max(image.size) > int(max_edge):
        scale = int(max_edge) / max(image.size)
        size = (
            max(1, round(image.width * scale)),
            max(1, round(image.height * scale)),
        )
        try:
            output = image.resize(size, Image.Resampling.LANCZOS)
        except (OSError, RuntimeError, ValueError) as exc:
            raise CaptureEncodeError("capture resize failed") from exc
    try:
        check_cancelled()
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CaptureStorageError("capture staging is unavailable") from exc
        try:
            output.save(destination, format="PNG", optimize=True)
        except OSError as exc:
            raise CaptureStorageError("capture staging write failed") from exc
        except (RuntimeError, ValueError) as exc:
            raise CaptureEncodeError("capture PNG encoding failed") from exc
        check_cancelled()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if output is not image:
            output.close()


def capture_window_png(hwnd: int, destination: Path) -> None:
    """Compatibility wrapper for callers that still need a direct PNG."""

    image = capture_window_image(hwnd)
    try:
        encode_window_image_png(image, destination)
    finally:
        image.close()


class Win32IdleProvider:
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def idle_seconds(self) -> float:
        if os.name != "nt":
            return 0.0
        info = self.LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32().GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        elapsed = (int(_kernel32().GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF
        return elapsed / 1000.0


class Win32ForegroundContextReader:
    """Build an in-memory context for a foreground event's HWND.

    Reading happens for a foreground switch or one explicit capture preflight.
    The object neither stores raw titles nor polls, and its password detection
    is limited to the standard ``ES_PASSWORD`` style on the focused control.
    """

    _GAME_CLASSES = {
        "cryengine",
        "grcwindow",
        "sdl_app",
        "unitywndclass",
        "unrealwindow",
    }

    def __call__(self, hwnd: int) -> ForegroundContext:
        if os.name != "nt" or not hwnd:
            return ForegroundContext(int(hwnd or 0))
        user32 = _user32()
        native = wintypes.HWND(int(hwnd))
        process_id = wintypes.DWORD()
        thread_id = int(user32.GetWindowThreadProcessId(native, ctypes.byref(process_id)))
        process_name = self._process_name(int(process_id.value))
        title = self._window_text(native)
        window_class = self._window_class(native)
        full_screen = self._is_full_screen(native)
        focused_password = self._focused_control_is_password(thread_id)
        process_key = process_name.casefold()
        protected = process_key in {"consent.exe", "credentialuibroker.exe"}
        is_game = window_class.casefold() in self._GAME_CLASSES
        return ForegroundContext(
            int(hwnd),
            process_id=int(process_id.value),
            process_name=process_name,
            window_class=window_class,
            title=title,
            full_screen=full_screen,
            is_game=is_game,
            is_password=focused_password,
            is_protected=protected,
        )

    @staticmethod
    def _window_text(hwnd: wintypes.HWND) -> str:
        length = max(0, int(_user32().GetWindowTextLengthW(hwnd)))
        buffer = ctypes.create_unicode_buffer(min(length + 1, 4096))
        _user32().GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _window_class(hwnd: wintypes.HWND) -> str:
        buffer = ctypes.create_unicode_buffer(512)
        _user32().GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    @staticmethod
    def _process_name(process_id: int) -> str:
        if not process_id:
            return ""
        kernel32 = _kernel32()
        process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not process:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                return ""
            return Path(buffer.value).name
        finally:
            kernel32.CloseHandle(process)

    @staticmethod
    def _is_full_screen(hwnd: wintypes.HWND) -> bool:
        user32 = _user32()
        window_rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            return False
        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        tolerance = 2
        return (
            window_rect.left <= info.rcMonitor.left + tolerance
            and window_rect.top <= info.rcMonitor.top + tolerance
            and window_rect.right >= info.rcMonitor.right - tolerance
            and window_rect.bottom >= info.rcMonitor.bottom - tolerance
        )

    @staticmethod
    def _focused_control_is_password(thread_id: int) -> bool:
        if not thread_id:
            return False
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(info)
        if not _user32().GetGUIThreadInfo(thread_id, ctypes.byref(info)) or not info.hwndFocus:
            return False
        style = int(_user32().GetWindowLongW(info.hwndFocus, GWL_STYLE))
        return bool(style & ES_PASSWORD)


@lru_cache(maxsize=1)
def _user32():
    library = ctypes.WinDLL("User32.dll", use_last_error=True)
    library.GetLastInputInfo.argtypes = [ctypes.c_void_p]
    library.GetLastInputInfo.restype = wintypes.BOOL
    library.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    library.GetWindowThreadProcessId.restype = wintypes.DWORD
    library.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    library.GetWindowTextLengthW.restype = ctypes.c_int
    library.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    library.GetWindowTextW.restype = ctypes.c_int
    library.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    library.GetClassNameW.restype = ctypes.c_int
    library.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    library.GetWindowRect.restype = wintypes.BOOL
    library.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    library.GetClientRect.restype = wintypes.BOOL
    library.IsWindow.argtypes = [wintypes.HWND]
    library.IsWindow.restype = wintypes.BOOL
    library.IsWindowVisible.argtypes = [wintypes.HWND]
    library.IsWindowVisible.restype = wintypes.BOOL
    library.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    library.MonitorFromWindow.restype = wintypes.HMONITOR
    library.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    library.GetMonitorInfoW.restype = wintypes.BOOL
    library.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    library.GetGUIThreadInfo.restype = wintypes.BOOL
    library.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    library.GetWindowLongW.restype = wintypes.LONG
    library.SetWinEventHook.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HMODULE,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    library.SetWinEventHook.restype = wintypes.HANDLE
    library.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    library.UnhookWinEvent.restype = wintypes.BOOL
    library.PeekMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    library.PeekMessageW.restype = wintypes.BOOL
    library.TranslateMessage.argtypes = [ctypes.c_void_p]
    library.DispatchMessageW.argtypes = [ctypes.c_void_p]
    library.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    library.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    library.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    library.PrintWindow.restype = wintypes.BOOL
    return library


@lru_cache(maxsize=1)
def _dwmapi():
    """Return optional DWM bindings without making capture depend on DWM."""

    try:
        library = ctypes.WinDLL("Dwmapi.dll", use_last_error=True)
        function = library.DwmGetWindowAttribute
    except (AttributeError, OSError):
        return None
    function.argtypes = [
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    function.restype = ctypes.c_long
    return library


def _window_is_explicitly_cloaked(hwnd: wintypes.HWND) -> bool:
    """Report DWM cloaking only when the optional query succeeds."""

    library = _dwmapi()
    if library is None:
        return False
    cloaked = wintypes.DWORD()
    try:
        status = library.DwmGetWindowAttribute(
            hwnd,
            14,  # DWMWA_CLOAKED
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
    except (AttributeError, OSError):
        return False
    return int(status) == 0 and bool(cloaked.value)


@lru_cache(maxsize=1)
def _gdi32():
    library = ctypes.WinDLL("Gdi32.dll", use_last_error=True)
    library.CreateCompatibleDC.argtypes = [wintypes.HDC]
    library.CreateCompatibleDC.restype = wintypes.HDC
    library.DeleteDC.argtypes = [wintypes.HDC]
    library.DeleteDC.restype = wintypes.BOOL
    library.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    library.CreateDIBSection.restype = wintypes.HANDLE
    library.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    library.SelectObject.restype = wintypes.HANDLE
    library.DeleteObject.argtypes = [wintypes.HANDLE]
    library.DeleteObject.restype = wintypes.BOOL
    return library


@lru_cache(maxsize=1)
def _kernel32():
    library = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    library.GetTickCount.argtypes = []
    library.GetTickCount.restype = wintypes.DWORD
    library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    library.OpenProcess.restype = wintypes.HANDLE
    library.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    library.QueryFullProcessImageNameW.restype = wintypes.BOOL
    library.CloseHandle.argtypes = [wintypes.HANDLE]
    library.CloseHandle.restype = wintypes.BOOL
    return library


class Win32ForegroundEventHook:
    """A SetWinEventHook-backed event source with an explicit lifecycle."""

    _CALLBACK = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    def __init__(self) -> None:
        self._callback: Callable[[int], None] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._hook = 0
        self._native_callback = None

    def start(self, callback: Callable[[int], None]) -> None:
        if os.name != "nt" or (self._thread and self._thread.is_alive()):
            return
        self._callback = callback
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="lilies-foreground-hook", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._thread = None

    def _run(self) -> None:
        user32 = _user32()

        def on_event(_hook, _event, hwnd, object_id, child_id, _thread, _time) -> None:
            if int(object_id) == 0 and int(child_id) == 0 and hwnd and self._callback:
                try:
                    self._callback(int(hwnd))
                except Exception:
                    # Hooks must never unwind into User32.
                    return

        self._native_callback = self._CALLBACK(on_event)
        self._hook = int(
            user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND,
                EVENT_SYSTEM_FOREGROUND,
                None,
                self._native_callback,
                0,
                0,
                WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
            )
            or 0
        )
        if not self._hook:
            return
        message = wintypes.MSG()
        try:
            while not self._stop.wait(0.05):
                while user32.PeekMessageW(
                    ctypes.byref(message), None, 0, 0, PM_REMOVE
                ):
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWinEvent(wintypes.HANDLE(self._hook))
            self._hook = 0
            self._native_callback = None
