from __future__ import annotations

import threading
import time
import hashlib
import ipaddress
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from PySide6.QtCore import Property, QObject, QThread, QTimer, Signal, Slot

from .core.activity import (
    ActivityContextService,
    CaptureCancelled,
    CaptureEncodeError,
    CaptureStorageError,
    CaptureStaging,
    ForegroundContext,
    LowInformationCapture,
    ObservationPolicy,
    ProtectedCaptureContent,
    SensitiveWindowGuard,
    StagedCapture,
    Win32ForegroundContextReader,
    Win32ForegroundEventHook,
    Win32IdleProvider,
    capture_window_image,
    ephemeral_title_fingerprint,
)
from .core.companion import (
    DEFAULT_BUBBLE_ACTIONS,
    BubbleSession,
    BubbleSource,
    CompanionEngine,
    CompanionPreferences,
    ContentCategory,
    SceneMomentum,
    SpeechBubble,
    rank_content,
    summaries_are_near_duplicates,
)
from .core.companion_runtime import CompanionRuntime, LUNA_MODEL, TERRA_MODEL
from .core.companion_delivery import (
    COMPANION_DELIVERY_REASONS,
    COMPANION_DELIVERY_STATES,
    normalize_companion_delivery_reason,
)
from .core.content import ContentItem, ContentService, RssAtomProvider, UrllibFetcher
from .core.content_cache import DatabaseContentCache
from .core.database import Database
from .core.memory import MemoryService
from .core.model import _BrokerTaskLease
from .core.native_capture_helper import (
    NativeCaptureHelperError,
    native_capture_helper_available,
    stage_window_capture_with_helper,
)
from .core.orchestration import ModelTaskBroker, ModelTaskKind
from .core.windows import foreground_window, open_web_url


_APPLICATION_SCENES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset(
            {
                "acrord32",
                "cajviewer",
                "foxitpdfreader",
                "mupdf-gl",
                "pdfxedit",
                "sumatrapdf",
            }
        ),
        "论文阅读",
    ),
    (
        frozenset(
            {
                "et",
                "excel",
                "libreoffice",
                "powerpnt",
                "presentation",
                "winword",
                "wps",
                "wpp",
            }
        ),
        "文档工作",
    ),
    (
        frozenset({"brave", "chrome", "firefox", "msedge", "opera", "vivaldi"}),
        "网页浏览",
    ),
    (
        frozenset(
            {
                "code",
                "devenv",
                "idea64",
                "pycharm64",
                "rider64",
                "studio64",
                "windowsterminal",
            }
        ),
        "开发与整理",
    ),
    (
        frozenset({"discord", "feishu", "qq", "slack", "teams", "wechat"}),
        "沟通",
    ),
    (frozenset({"explorer"}), "文件整理"),
)

_BROWSER_PROCESSES = frozenset(
    {
        "brave.exe",
        "chrome.exe",
        "firefox.exe",
        "msedge.exe",
        "opera.exe",
        "vivaldi.exe",
    }
)

# Capture diagnostics are a fixed, content-free vocabulary.  In particular,
# exception strings, native HWNDs, titles and staging paths must never become
# a persistent reason value.
_IMAGE_QUALITY_CAPTURE_REASONS = frozenset(
    {
        "image-anchor-generic",
        "image-anchor-unrelated",
        "image-circuit-open",
        "image-generation-failed",
        "image-low-confidence",
        "image-model-unavailable",
        "image-result-invalid",
        "philosophy-quality-invalid",
    }
)

_QUIET_GENERATION_SKIP_REASONS = _IMAGE_QUALITY_CAPTURE_REASONS | frozenset(
    {
        "capture-attempt-failed",
        "philosophy-quality-invalid",
        "source-metadata-repeated",
        "source-metadata-unavailable",
        "subjective-generation-failed",
        "subjective-model-unavailable",
        "text-result-invalid",
        "text-visual-claim",
    }
)

_CAPTURE_REASON_CODES = frozenset(
    {
        "",
        "activity-disabled",
        "activity-paused",
        "authorization-revoked",
        "broker-cancelled",
        "broker-cancelled-before-model",
        "browser-authorization-revoked",
        "browser-capture-not-authorized",
        "browser-capture-paused",
        "capture-context-changed-before-model",
        "capture-context-changed-before-presentation",
        "companion-shutdown",
        "encode-failed",
        "encode-storage-failed",
        "foreground-changed",
        "image-model-completed",
        "image-model-unavailable",
        "legacy-failure-unknown",
        "low-information",
        "model-error",
        "model-request-starting",
        "native-grab-failed",
        "native-print-failed",
        "native-print-staged",
        "presentation-suppressed",
        "privacy-suppressed",
        "protected-black",
        "request-cancelled",
        "timing-not-ready",
        "window-content-changed",
        "worker-start-failed",
    }
) | _IMAGE_QUALITY_CAPTURE_REASONS

_POST_STAGE_CAPTURE_REASONS = frozenset(
    {
        "authorization-revoked",
        "broker-cancelled",
        "broker-cancelled-before-model",
        "browser-authorization-revoked",
        "capture-context-changed-before-model",
        "capture-context-changed-before-presentation",
        "foreground-changed",
        "model-error",
        "presentation-suppressed",
        "request-cancelled",
    }
) | _IMAGE_QUALITY_CAPTURE_REASONS

_CAPTURE_PRESENTATION_OUTCOMES = frozenset(
    {"unknown", "pending", "shown", "quiet", "cancelled", "unread"}
)
_CAPTURE_PRESENTATION_REASONS = frozenset(
    {
        "",
        "awaiting-presentation",
        "window-exposed",
        "presentation-ack-timeout",
        "presentation-suppressed",
        "privacy-suppressed",
        "foreground-changed",
        "capture-context-changed-before-presentation",
        "duplicate-suppressed",
        "engine-rejected",
        "authorization-revoked",
        "browser-authorization-revoked",
        "generation-cancelled",
        "quality-rejected",
        "dismissed-before-presentation",
        "process-restarted-before-presentation",
        "unsafe-resume",
    }
)


_BUBBLE_PRESENTATION_SECONDS = 240.0
_PRESENTATION_ACK_TIMEOUT_MS = 2_500
_UNREAD_REDELIVERY_RETRY_SECONDS = 60.0
# An ignored bubble is useful as a recoverable notification, but it must never
# become a permanent global lock on companionship.  Keep the durable session
# and prose indefinitely; only the one-item delivery flag expires.
_UNREAD_RETENTION_SECONDS = 6.0 * 60.0 * 60.0
_UNREAD_REDELIVERY_LIMIT = 2
_MODALITY_RETRY_DELAYS_SECONDS = (15, 30, 60, 120, 300)


def _compact_diagnostic_text(value: object, limit: int) -> str:
    """Bound one content-free diagnostic label without retaining line data."""

    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _capture_model_id(value: object) -> str:
    """Return only a model identifier controlled by this application."""

    current = _compact_diagnostic_text(value, 80)
    return current if current in {LUNA_MODEL, TERRA_MODEL} else ""


def _capture_receipt_id(value: object) -> str:
    """Accept only generated, content-free identifiers in capture receipts."""

    current = _compact_diagnostic_text(value, 160)
    if not current or any(
        not (character.isascii() and (character.isalnum() or character in "_-"))
        for character in current
    ):
        return ""
    return current


def _combined_generated_prose(summary: object, detail: object) -> str:
    """Combine only Lilies-generated bubble prose for local novelty checks."""

    clean_summary = str(summary or "").strip()
    clean_detail = str(detail or "").strip()
    if not clean_detail or clean_detail == clean_summary:
        return clean_summary
    return f"{clean_summary}\n{clean_detail}" if clean_summary else clean_detail


class CompanionController(QObject):
    changed = Signal()
    preferencesChanged = Signal()
    bubbleChanged = Signal()
    sourcesChanged = Signal()
    _generationReady = Signal(object)
    _replyReady = Signal(object)
    _modalitiesReady = Signal(object)
    _sourceReady = Signal(object)

    def __init__(
        self,
        database: Database,
        data_directory: Path,
        *,
        active: bool,
        status_sink: Callable[[str], None],
        move_to_box: Callable[[dict[str, Any]], None],
        unified_event_hub: object | None = None,
        model_broker: ModelTaskBroker | None = None,
        foreground_provider: Callable[[], int] = foreground_window,
    ) -> None:
        super().__init__()
        self.database = database
        self.data_directory = Path(data_directory)
        self.status_sink = status_sink
        self.move_to_box_callback = move_to_box
        self._foreground_provider = foreground_provider
        self._model_broker = model_broker
        self._model_task_ids: set[str] = set()
        self._model_task_lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._capture_diagnostic_lock = threading.RLock()
        self._worker_lock = threading.Lock()
        self._worker_threads: set[threading.Thread] = set()
        self._closing = False
        self.memory = MemoryService(database)
        self.preferences_model, self._custom_frequency = (
            self._load_frequency_preferences()
        )
        raw_interests = database.get_setting("companion_interests", [])
        self._interests = (
            list(raw_interests) if isinstance(raw_interests, list) else []
        )
        self._recent_summary_snippets: deque[str] = deque(
            database.recent_proactive_summaries(12), maxlen=12
        )
        self._recent_prose_snippets: deque[str] = deque(
            (
                _combined_generated_prose(item.get("summary"), item.get("detail"))
                for item in database.recent_proactive_prose(12)
            ),
            maxlen=12,
        )
        self.engine = CompanionEngine(
            self.preferences_model,
            event_sink=self._record_event,
        )
        self.engine.gate.restore(database.get_setting("companion_emission_state", {}))
        policies = database.get_setting("activity_application_policies", {})
        if not isinstance(policies, dict):
            policies = {}
        self.reader = Win32ForegroundContextReader()
        self.activity = ActivityContextService(
            self.reader,
            Win32IdleProvider(),
            # v0.3 routes foreground events through the app-owned WinEventHub.
            # Keep the legacy source only for standalone/controller tests.
            event_source=None if unified_event_hub is not None else Win32ForegroundEventHook(),
            guard=SensitiveWindowGuard(policies),
            cooldown_seconds=self.preferences_model.frequency.minimum_minutes * 60.0,
        )
        self.momentum = SceneMomentum(self.preferences_model.momentum_half_life_minutes)
        self.capture_staging = CaptureStaging(
            self.data_directory / "capture-staging",
            self.data_directory / "capture-library",
            1600,
        )
        self.capture_staging.cleanup_stale(0)
        self.content = ContentService(cache=DatabaseContentCache(database))
        self._load_custom_sources()
        self.runtime = CompanionRuntime(self.data_directory, self.memory)
        self._activity_enabled = bool(database.get_setting("activity_context_enabled", True))
        self._smart_observation = bool(database.get_setting("smart_observation_authorized", False))
        # Browser pixels are deliberately unavailable in v0.3.36 while the
        # capture backend and page-level privacy boundaries are being hardened.
        # Clear the retired opt-in even for legacy databases: a stored consent
        # must never silently re-enable this path after an upgrade.
        self._browser_single_capture = False
        if bool(database.get_setting("browser_single_capture_authorized", False)):
            database.set_setting("browser_single_capture_authorized", False)
        self._online_content = bool(database.get_setting("online_content_authorized", False))
        if self._online_content:
            self.content.fetcher = UrllibFetcher()
        self._bubble: dict[str, Any] = {}
        self._bubble_object: SpeechBubble | None = None
        self._capture: StagedCapture | None = None
        self._inflight_capture: StagedCapture | None = None
        self._bubble_ttl_remaining_seconds = _BUBBLE_PRESENTATION_SECONDS
        self._bubble_presented = False
        self._bubble_interacted = False
        self._presentation_ack_pending = False
        self._capture_diagnostic_serial = 0
        self._active_capture_diagnostic_token = 0
        self._last_capture_status_token = 0
        self._bubble_capture_diagnostic_token = 0
        self._active_generation_has_capture = False
        self._active_generation_capture_diagnostic_token = 0
        raw_delivery = database.get_setting("companion_delivery_status", {})
        self._delivery_record = (
            dict(raw_delivery) if isinstance(raw_delivery, dict) else {}
        )
        self._restore_delivery_record_after_startup()
        self._busy = False
        self._probe_busy = False
        self._generation_cancel_event: threading.Event | None = None
        self._active_generation_model_id = ""
        self._last_unread_redelivery_at = -float("inf")
        self._prune_unread_delivery()
        raw_capture_status = database.get_setting("companion_last_capture_status", {})
        capture_status = (
            dict(raw_capture_status) if isinstance(raw_capture_status, dict) else {}
        )
        capture_status_migrated = not isinstance(raw_capture_status, dict)
        stored_capture_outcome = str(capture_status.get("outcome", "never"))[:40]
        stored_capture_reason = str(capture_status.get("reason", ""))[:80]
        if stored_capture_reason == "capture-unavailable":
            # Older builds reused this coarse label for native acquisition,
            # low-information frames, encoding and staging failures.  Do not
            # manufacture certainty while migrating a content-free diagnostic.
            stored_capture_reason = "legacy-failure-unknown"
            capture_status_migrated = True
        elif stored_capture_reason not in _CAPTURE_REASON_CODES:
            # Never persist the legacy exception/path text.  A failed record
            # remains diagnosable without retaining any content-bearing value.
            stored_capture_reason = (
                "legacy-failure-unknown"
                if stored_capture_outcome == "failed"
                else ""
            )
            capture_status_migrated = True
        inferred_attempted = stored_capture_outcome not in {"never", "skipped"}
        inferred_submitted = stored_capture_outcome in {"submitted", "used"}
        inferred_accepted = stored_capture_outcome == "used"
        attempted_raw = capture_status.get("captureAttempted")
        submitted_raw = capture_status.get("imageSubmitted")
        accepted_raw = capture_status.get("imageResponseAccepted")
        model_raw = capture_status.get("model", "")
        confidence_raw = str(
            capture_status.get("evidenceConfidence", "none")
        ).casefold()[:16]
        pixels_used_raw = capture_status.get("pixelsUsed")
        capture_session_id = _capture_receipt_id(
            capture_status.get("sessionId", "")
        )
        capture_bubble_id = _capture_receipt_id(
            capture_status.get("bubbleId", "")
        )
        if capture_session_id != capture_status.get("sessionId", ""):
            capture_status_migrated = True
        if capture_bubble_id != capture_status.get("bubbleId", ""):
            capture_status_migrated = True
        if not all(
            isinstance(value, bool)
            for value in (attempted_raw, submitted_raw, accepted_raw)
        ):
            capture_status_migrated = True
        if not isinstance(model_raw, str):
            model_raw = ""
            capture_status_migrated = True
        if confidence_raw not in {"none", "low", "medium", "high"}:
            confidence_raw = "none"
            capture_status_migrated = True
        if not isinstance(pixels_used_raw, bool):
            pixels_used_raw = inferred_accepted
            capture_status_migrated = True
        submitted = bool(
            submitted_raw
            if isinstance(submitted_raw, bool)
            else inferred_submitted
        )
        accepted = bool(
            accepted_raw
            if isinstance(accepted_raw, bool)
            else inferred_accepted
        )
        pixels_used = bool(pixels_used_raw)
        if stored_capture_outcome == "used":
            submitted = accepted = pixels_used = True
        else:
            accepted = pixels_used = False
        attempted = bool(
            attempted_raw
            if isinstance(attempted_raw, bool)
            else inferred_attempted
        )
        if submitted or pixels_used:
            if not attempted:
                capture_status_migrated = True
            attempted = True
        normalized_model = _capture_model_id(model_raw) if submitted else ""
        normalized_confidence = (
            confidence_raw if pixels_used else "none"
        )
        if normalized_model != model_raw or normalized_confidence != confidence_raw:
            capture_status_migrated = True
        presentation_outcome = str(
            capture_status.get("presentationOutcome", "")
        )[:24]
        if presentation_outcome not in _CAPTURE_PRESENTATION_OUTCOMES:
            presentation_outcome = (
                "unknown"
                if stored_capture_outcome in {"never", "used"}
                else (
                    "pending"
                    if stored_capture_outcome in {"staged", "submitted"}
                    else (
                        "cancelled"
                        if stored_capture_outcome == "cancelled"
                        else "quiet"
                    )
                )
            )
            capture_status_migrated = True
        presentation_reason = str(
            capture_status.get("presentationReason", "")
        )[:80]
        if presentation_reason not in _CAPTURE_PRESENTATION_REASONS:
            presentation_reason = ""
            capture_status_migrated = True
        presentation_time_normalized = False
        if not pixels_used and presentation_outcome in {"shown", "unread"}:
            presentation_outcome = "quiet"
            presentation_reason = "quality-rejected"
            capture_status_migrated = True
            presentation_time_normalized = True
        if presentation_outcome == "pending":
            # A pending native-window ACK cannot survive a process restart.
            # Keep the pixel receipt intact, but resolve the independent
            # presentation receipt so settings never claim to be waiting
            # forever after a crash or ordinary restart.
            delivery_matches_capture = bool(
                capture_session_id
                and capture_bubble_id
                and capture_session_id
                == _capture_receipt_id(self._delivery_record.get("sessionId", ""))
                and capture_bubble_id
                == _capture_receipt_id(self._delivery_record.get("bubbleId", ""))
            )
            if (
                pixels_used
                and delivery_matches_capture
                and bool(self._delivery_record.get("unread"))
            ):
                presentation_outcome = "unread"
                presentation_reason = "process-restarted-before-presentation"
            else:
                presentation_outcome = "cancelled"
                presentation_reason = "generation-cancelled"
            capture_status_migrated = True
            presentation_time_normalized = True
        presentation_at = str(capture_status.get("presentationAt", ""))[:80]
        if presentation_time_normalized:
            presentation_at = datetime.now(UTC).isoformat()
        if capture_status.get("schemaVersion") != 4:
            capture_status_migrated = True
        if stored_capture_outcome == "failed" and not stored_capture_reason:
            stored_capture_reason = "legacy-failure-unknown"
            capture_status_migrated = True
        stored_capture_at = str(capture_status.get("at", ""))[:80]
        self._last_capture_status = {
            "schemaVersion": 4,
            "outcome": stored_capture_outcome,
            "reason": stored_capture_reason,
            "at": stored_capture_at,
            "captureAttempted": attempted,
            "imageSubmitted": submitted,
            "imageResponseAccepted": accepted,
            "pixelsUsed": pixels_used,
            "model": normalized_model,
            "evidenceConfidence": normalized_confidence,
            "presentationOutcome": presentation_outcome,
            "presentationReason": presentation_reason,
            "presentationAt": presentation_at,
            "sessionId": capture_session_id,
            "bubbleId": capture_bubble_id,
        }
        if capture_status_migrated:
            try:
                database.set_setting(
                    "companion_last_capture_status", dict(self._last_capture_status)
                )
            except Exception:
                # Diagnostics must never prevent the desktop companion from
                # starting.  The normalized in-memory view is still safe.
                pass
        self._modality_retry_attempt = 0
        self._modality_retry_due_at = 0.0
        self._archive_busy = False
        self._source_busy = False
        self._source_index = 0
        self._requested_category: ContentCategory | None = None
        self._category_smooth_scores: dict[ContentCategory, int] = {
            category: 0 for category in ContentCategory
        }
        self._content_items: list[ContentItem] = []
        self._source_counts: dict[str, int] = {}
        self._fresh_content_ids: set[str] = set()
        self._hydrate_cached_content()
        self._last_generation_model = ""
        self._last_generation_error = ""
        self._request_feedback = "主动陪伴已就绪；默认只感知应用类别，不读取屏幕文字"
        self._request_feedback_kind = "ready"
        self._recent_content_ids: deque[str] = deque(maxlen=40)
        self._recent_sources: deque[str] = deque(maxlen=20)
        self._scene_hits: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=12))
        self._screen_memory_mode = str(
            database.get_setting("screen_observation_memory", "significant")
        )
        if self._screen_memory_mode not in {"replies", "significant", "all"}:
            self._screen_memory_mode = "significant"
        self._preferences_state = self._compose_preferences_snapshot()
        self._active = bool(active)
        # The native QML surface is the final presentation authority.  A
        # production controller starts fail-closed until CompanionBubble has
        # synchronised its current ``suppressed`` value.  Standalone/offscreen
        # controllers (``active=False``) retain their existing testability.
        self._presentation_sync_ready = False
        self._presentation_suppressed = False
        self._presentation_epoch = 0
        self._generation_serial = 0
        self._active_generation_token = 0
        self._generation_attempt_not_before = -float("inf")
        self._legacy_dismiss_serial = 0
        self._last_foreground_reconcile_at = -float("inf")
        self._generationReady.connect(self._accept_generation)
        self._replyReady.connect(self._accept_reply)
        self._modalitiesReady.connect(self._accept_modalities)
        self._sourceReady.connect(self._accept_source)
        self._timer = QTimer(self)
        # This heartbeat reads only GetForegroundWindow/GetLastInputInfo.  It
        # never enumerates windows, reads keys or captures pixels.  A short
        # interval makes the configured 6–60 second natural-pause window
        # reliable without turning activity sensing into a continuous scan.
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._consider)
        # Bubble expiry is presentation lifecycle, not activity sensing.  It
        # must continue while activity observation is disabled or paused.
        self._bubble_expiry_timer = QTimer(self)
        self._bubble_expiry_timer.setSingleShot(True)
        self._bubble_expiry_timer.timeout.connect(self._expire_bubble)
        self._presentation_ack_timer = QTimer(self)
        self._presentation_ack_timer.setSingleShot(True)
        self._presentation_ack_timer.timeout.connect(
            self._presentation_ack_timed_out
        )
        self._archive_timer = QTimer(self)
        self._archive_timer.setInterval(60_000)
        self._archive_timer.timeout.connect(self._consider_archival)
        self._content_timer = QTimer(self)
        self._content_timer.setInterval(5 * 60_000)
        self._content_timer.timeout.connect(self._refresh_next_source)
        self._modality_retry_timer = QTimer(self)
        self._modality_retry_timer.setSingleShot(True)
        self._modality_retry_timer.timeout.connect(self._probe_modalities)
        if self._active:
            self._archive_timer.start()
            if self._online_content:
                self._content_timer.start()
                QTimer.singleShot(1200, self._refresh_next_source)
            if self._activity_enabled:
                self.activity.start()
                # Start the recoverable heartbeat before the best-effort
                # startup probe. User32 can briefly fail while Explorer or a
                # login shell is changing; that must not abort Backend
                # construction or leave activity enabled with no scheduler.
                self._timer.start()
                try:
                    current = self._foreground_provider()
                    if current:
                        self.activity.update_foreground(self.reader(current))
                except (OSError, RuntimeError, TypeError, ValueError):
                    pass
        if self._active and self._smart_observation:
            self._probe_modalities()

    def _load_frequency_preferences(
        self,
    ) -> tuple[CompanionPreferences, dict[str, int]]:
        resolved: dict[str, Any] = {}

        def reconcile(rows: dict[str, Any]) -> dict[str, Any]:
            raw_preferences = rows.get("companion_preferences", {})
            try:
                preferences = CompanionPreferences.from_mapping(
                    raw_preferences if isinstance(raw_preferences, dict) else {}
                )
            except (TypeError, ValueError):
                preferences = CompanionPreferences()

            current_frequency = preferences.frequency
            default_custom_minutes = (
                current_frequency.minimum_minutes
                if current_frequency.name == "custom"
                else 25
            )
            default_custom_daily = (
                current_frequency.daily_limit
                if current_frequency.name == "custom"
                else 12
            )
            raw_custom_frequency = rows.get("companion_custom_frequency", {})
            if not isinstance(raw_custom_frequency, dict):
                raw_custom_frequency = {}
            try:
                custom_minutes = int(
                    raw_custom_frequency.get(
                        "minimumMinutes", default_custom_minutes
                    )
                )
                custom_daily = int(
                    raw_custom_frequency.get("dailyLimit", default_custom_daily)
                )
            except (TypeError, ValueError):
                custom_minutes = default_custom_minutes
                custom_daily = default_custom_daily
            custom_frequency = {
                "minimumMinutes": max(5, min(custom_minutes, 180)),
                "dailyLimit": max(1, min(custom_daily, 50)),
            }

            repairs: dict[str, Any] = {}
            if current_frequency.name == "custom":
                # The active custom preference is the runtime authority.  Old
                # databases may have no remembered row, and interrupted or
                # legacy writers may have left it at a different generation.
                custom_frequency = {
                    "minimumMinutes": current_frequency.minimum_minutes,
                    "dailyLimit": current_frequency.daily_limit,
                }
                if raw_custom_frequency != custom_frequency:
                    repairs = {
                        "companion_custom_frequency": dict(custom_frequency),
                    }

            resolved["preferences"] = preferences
            resolved["custom_frequency"] = custom_frequency
            return repairs

        self.database.reconcile_settings(
            ("companion_preferences", "companion_custom_frequency"),
            reconcile,
        )
        return resolved["preferences"], resolved["custom_frequency"]

    def _restore_delivery_record_after_startup(self) -> None:
        """Normalize the content-free delivery journal after a process restart.

        Bubble prose already lives in ``proactive_sessions``.  This separate
        setting stores only identifiers, timestamps and fixed state labels so
        the tray can report one missed bubble without becoming another content
        store.  A process cannot prove that an in-flight native tool window was
        seen before it exited, therefore unfinished delivery becomes unread.
        """

        stored_state = str(self._delivery_record.get("state", "idle"))
        stored_reason = str(self._delivery_record.get("reason", ""))[:80]
        state = stored_state
        if state not in COMPANION_DELIVERY_STATES:
            state = "idle"
        session_id = str(self._delivery_record.get("sessionId", ""))[:160]
        bubble_id = str(self._delivery_record.get("bubbleId", ""))[:160]
        stored_unread = bool(self._delivery_record.get("unread", False))
        try:
            redelivery_count = max(
                0, int(self._delivery_record.get("redeliveryCount", 0) or 0)
            )
        except (TypeError, ValueError):
            redelivery_count = 0
        if session_id and bubble_id and state in {
            "waiting-present-ack",
            "presented",
            "suppressed",
        }:
            state = "unread"
            reason = "process-restarted-before-read"
        else:
            reason = normalize_companion_delivery_reason(stored_reason)
        # On disk, ``state`` is the authoritative lifecycle marker.  Legacy or
        # malformed records must not expose an unread count that disagrees with
        # it.  A journal without delivery identifiers cannot be reopened, so
        # archive it with the same content-free reason used by health pruning.
        unread = state == "unread"
        if unread and (not session_id or not bubble_id):
            state = "expired"
            reason = "unread-session-missing"
            unread = False
        generated_at = str(self._delivery_record.get("generatedAt", ""))[:80]
        presented_at = str(self._delivery_record.get("presentedAt", ""))[:80]
        unread_since = str(self._delivery_record.get("unreadSince", ""))[:80]
        if unread and not unread_since:
            # Existing v1 records have no unread clock.  Use their earliest
            # trustworthy delivery timestamp so an abandoned legacy record
            # cannot acquire a fresh six-hour lock on every application start.
            unread_since = presented_at or generated_at or datetime.now(UTC).isoformat()
        self._delivery_record = {
            "schemaVersion": 2,
            "sessionId": session_id,
            "bubbleId": bubble_id,
            "state": state,
            "reason": reason,
            "generatedAt": generated_at,
            "presentedAt": presented_at,
            "expiresAt": "",
            "unread": unread,
            "unreadSince": unread_since if unread else "",
            "redeliveryCount": redelivery_count if unread else 0,
            "lastRedeliveryAt": (
                str(self._delivery_record.get("lastRedeliveryAt", ""))[:80]
                if unread
                else ""
            ),
        }
        if (
            state == "unread"
            or state != stored_state
            or unread != stored_unread
            or reason != stored_reason
        ):
            self._persist_delivery_record()

    def _persist_delivery_record(self) -> None:
        """Best-effort persistence for delivery metadata, never bubble prose."""

        try:
            self.database.set_setting(
                "companion_delivery_status", dict(self._delivery_record)
            )
        except Exception:
            # Delivery diagnostics must not be able to suppress the actual
            # bubble.  The in-memory state remains useful for this process.
            return

    def _set_delivery_state(
        self,
        state: str,
        reason: str,
        *,
        unread: bool | None = None,
        presented_at: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        normalized = str(state)
        if normalized not in COMPANION_DELIVERY_STATES:
            raise ValueError(f"unknown companion delivery state: {state}")
        normalized_reason = str(reason)
        if normalized_reason not in COMPANION_DELIVERY_REASONS:
            raise ValueError("unknown companion delivery reason")
        self._delivery_record["schemaVersion"] = 2
        self._delivery_record["state"] = normalized
        self._delivery_record["reason"] = normalized_reason
        if unread is not None:
            self._delivery_record["unread"] = bool(unread)
        if bool(self._delivery_record.get("unread")):
            if not str(self._delivery_record.get("unreadSince", "")):
                self._delivery_record["unreadSince"] = datetime.now(UTC).isoformat()
            try:
                self._delivery_record["redeliveryCount"] = max(
                    0, int(self._delivery_record.get("redeliveryCount", 0) or 0)
                )
            except (TypeError, ValueError):
                self._delivery_record["redeliveryCount"] = 0
            self._delivery_record.setdefault("lastRedeliveryAt", "")
        else:
            self._delivery_record["unreadSince"] = ""
            self._delivery_record["redeliveryCount"] = 0
            self._delivery_record["lastRedeliveryAt"] = ""
        if presented_at is not None:
            self._delivery_record["presentedAt"] = str(presented_at)[:80]
        if expires_at is not None:
            self._delivery_record["expiresAt"] = str(expires_at)[:80]
        self._persist_delivery_record()

    @staticmethod
    def _delivery_datetime(value: object) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

    def _unread_session_health(self) -> str:
        """Return valid/missing/unavailable without exposing stored prose."""

        session_id = str(self._delivery_record.get("sessionId", ""))
        bubble_id = str(self._delivery_record.get("bubbleId", ""))
        if not session_id or not bubble_id:
            return "missing"
        try:
            saved = self.database.proactive_session(session_id)
        except Exception:
            # A transient lock or unavailable F: drive must not silently mark
            # a real notification read.  Try again on the next heartbeat.
            return "unavailable"
        if not isinstance(saved, dict):
            return "missing"
        if str(saved.get("bubble_id", "")) != bubble_id:
            return "missing"
        if not str(saved.get("summary", "")).strip():
            return "missing"
        try:
            ContentCategory(str(saved.get("category", "")))
        except ValueError:
            return "missing"
        if self._delivery_datetime(saved.get("created_at")) is None:
            return "missing"
        return "valid"

    def _unread_expiry_reason(self, now: datetime | None = None) -> str:
        if not bool(self._delivery_record.get("unread")):
            return ""
        health = self._unread_session_health()
        if health == "missing":
            return "unread-session-missing"
        if health == "unavailable":
            return ""
        try:
            attempts = max(
                0, int(self._delivery_record.get("redeliveryCount", 0) or 0)
            )
        except (TypeError, ValueError):
            attempts = 0
        if attempts >= _UNREAD_REDELIVERY_LIMIT:
            return "unread-redelivery-exhausted"
        current = (now or datetime.now(UTC)).astimezone(UTC)
        unread_since = self._delivery_datetime(
            self._delivery_record.get("unreadSince")
        )
        if unread_since is None:
            unread_since = self._delivery_datetime(
                self._delivery_record.get("presentedAt")
            ) or self._delivery_datetime(self._delivery_record.get("generatedAt"))
        if unread_since is not None and (
            current - unread_since
        ).total_seconds() >= _UNREAD_RETENTION_SECONDS:
            return "unread-retention-expired"
        return ""

    def _prune_unread_delivery(self, now: datetime | None = None) -> bool:
        """Archive an unusable/exhausted delivery flag, never its history."""

        reason = self._unread_expiry_reason(now)
        if not reason:
            return False
        self._delivery_record["state"] = "expired"
        self._delivery_record["reason"] = reason
        self._delivery_record["unread"] = False
        self._delivery_record["expiresAt"] = ""
        self._delivery_record["unreadSince"] = ""
        self._delivery_record["redeliveryCount"] = 0
        self._delivery_record["lastRedeliveryAt"] = ""
        self._persist_delivery_record()
        return True

    @Property("QVariantMap", notify=changed)
    def deliveryStatus(self) -> dict[str, Any]:
        expires_in = 0.0
        raw_expiry = str(self._bubble.get("expiresAt", ""))
        if raw_expiry:
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                expires_in = max(0.0, (expiry - datetime.now(UTC)).total_seconds())
            except ValueError:
                expires_in = 0.0
        try:
            redelivery_count = max(
                0, int(self._delivery_record.get("redeliveryCount", 0) or 0)
            )
        except (TypeError, ValueError):
            redelivery_count = 0
        unread_since = self._delivery_datetime(
            self._delivery_record.get("unreadSince")
        )
        unread_retention_remaining = (
            max(
                0.0,
                _UNREAD_RETENTION_SECONDS
                - (datetime.now(UTC) - unread_since).total_seconds(),
            )
            if unread_since is not None
            and bool(self._delivery_record.get("unread"))
            else 0.0
        )
        return {
            "state": str(self._delivery_record.get("state", "idle")),
            "reason": str(self._delivery_record.get("reason", "")),
            "presentationReady": bool(
                self._presentation_sync_ready or not self._active
            ),
            "suppressed": bool(self._presentation_suppressed),
            "ackPending": bool(self._presentation_ack_pending),
            "presented": bool(self._bubble_presented),
            "hasBubble": bool(self._bubble),
            "unreadCount": 1 if bool(self._delivery_record.get("unread")) else 0,
            "unreadRedeliveriesRemaining": (
                max(0, _UNREAD_REDELIVERY_LIMIT - redelivery_count)
                if bool(self._delivery_record.get("unread"))
                else 0
            ),
            "unreadRetentionSeconds": round(unread_retention_remaining, 1),
            "expiresInSeconds": round(expires_in, 1),
        }

    def _compose_preferences_snapshot(
        self,
        *,
        preferences: CompanionPreferences | None = None,
        custom_frequency: dict[str, int] | None = None,
        interests: list[Any] | None = None,
        screen_memory_mode: str | None = None,
    ) -> dict[str, Any]:
        value = (preferences or self.preferences_model).to_mapping()
        selected_custom = (
            self._custom_frequency
            if custom_frequency is None
            else custom_frequency
        )
        value["customMinimumMinutes"] = int(
            selected_custom["minimumMinutes"]
        )
        value["customDailyLimit"] = int(selected_custom["dailyLimit"])
        value["interests"] = list(
            self._interests if interests is None else interests
        )
        value["screenMemoryMode"] = (
            self._screen_memory_mode
            if screen_memory_mode is None
            else screen_memory_mode
        )
        return value

    def _preferences_snapshot(self) -> dict[str, Any]:
        # Return a detached copy of the one state generation published after a
        # successful commit.  In particular, a property read must never open a
        # fallible database connection or combine fields from two generations.
        value = dict(self._preferences_state)
        value["categoryWeights"] = dict(value.get("categoryWeights", {}))
        value["interests"] = list(value.get("interests", []))
        return value

    @Property("QVariantMap", notify=preferencesChanged)
    def preferences(self) -> dict[str, Any]:
        return self._preferences_snapshot()

    @Property("QVariantList", notify=changed)
    def applicationPolicies(self) -> list[dict[str, Any]]:
        """Return only title-free application identities and effective policy."""

        labels = {
            ObservationPolicy.BLOCKED.value: "静默",
            ObservationPolicy.SIGNAL_ONLY.value: "仅使用场景信号",
            ObservationPolicy.OBSERVE.value: "允许单次观察，不显示气泡",
            ObservationPolicy.ALLOW_BUBBLE.value: "允许气泡",
        }
        return [
            {
                "application": application,
                "policy": policy,
                "policyLabel": labels.get(policy, policy),
                "safetyLocked": self.activity.guard.is_safety_locked(application),
            }
            for application, policy in sorted(self.activity.guard.policies().items())
        ]

    @Property("QVariantMap", notify=changed)
    def activityStatus(self) -> dict[str, Any]:
        value = dict(self.activity.status())
        state = str(value.get("state", ""))
        now = datetime.now(UTC)
        gate = self.engine.gate.state(now)
        state_labels = {
            "not-started": "尚未启动",
            "waiting-for-foreground": "等待一个可用窗口",
            "waiting": "等待自然停顿",
            "stabilizing": "正在熟悉当前窗口",
            "window-not-stable": "正在熟悉当前窗口",
            "user-active": "你还在操作，先不打扰",
            "user-away": "你暂时离开，保持安静",
            "cooldown": "本轮安静间隔",
            "allowed": "检测到自然停顿",
            "sent": "刚刚说过一句",
            "disabled": "活动感知已关闭",
            "stopped": "活动感知已关闭",
            "paused": "活动感知已暂停",
            "no-context": "等待一个可用窗口",
            "no-foreground-window": "等待一个可用窗口",
            "assistant-ui": "莉莉丝自己的界面不观察",
            "signals-only": "当前应用只使用场景信号",
            "protected-content": "受保护内容，保持安静",
            "password-manager": "密码应用，保持安静",
            "security-dialog": "系统安全界面，保持安静",
            "remote-desktop": "远程桌面，保持安静",
            "meeting": "会议中，保持安静",
            "private-browsing": "隐私浏览，保持安静",
            "payment-window": "支付窗口，保持安静",
            "application-blocked": "此应用已设为静默",
        }
        context_type = str(value.get("lastContextType", "none"))
        context_labels = {
            "none": "尚未发送",
            "application-signal": "应用级信号（未截图）",
            "active-window-image": "单次活动窗口画面",
        }
        capture_reason_labels = {
            "timing-not-ready": "尚未到自然停顿",
            "window-content-changed": "文档刚刚变化，重新等待稳定",
            "image-model-unavailable": "暂无图像模型",
            "image-circuit-open": "图像模型正在短暂退避",
            "image-generation-failed": "图像理解本次未完成",
            "image-anchor-generic": "图像模型没有指出具体视觉细节",
            "image-anchor-unrelated": "画面细节没有进入最终表达",
            "image-low-confidence": "图像模型没有足够的画面把握",
            "image-result-invalid": "图像模型返回格式不完整",
            "philosophy-quality-invalid": "画面已理解，但表达没有通过哲思质量检查",
            "legacy-failure-unknown": "旧版截图失败记录（具体阶段未知）",
            "browser-capture-not-authorized": "浏览器只使用应用类别",
            "browser-capture-paused": "浏览器像素观察暂不开放，本次只使用应用类别",
            "native-grab-failed": "系统未能取得活动窗口画面",
            "native-print-failed": "目标窗口没有返回可用画面，本次保持安静",
            "native-print-staged": "目标窗口画面已在隔离进程中准备",
            "protected-black": "受保护或黑屏画面已拒绝",
            "low-information": "画面信息不足，本次没有生成屏幕对话",
            "encode-storage-failed": "截图暂存不可用",
            "encode-failed": "截图编码失败",
            "foreground-changed": "前台已变化",
            "authorization-revoked": "屏幕观察授权已撤销",
            "browser-authorization-revoked": "浏览器截图授权已撤销",
            "privacy-suppressed": "隐私场景静默",
            "activity-disabled": "感知已关闭",
            "activity-paused": "感知已暂停",
            "presentation-suppressed": "当前界面静默",
            "model-error": "图像理解失败",
            "broker-cancelled": "请求已取消",
            "broker-cancelled-before-model": "请求已取消",
            "request-cancelled": "请求已取消",
            "worker-start-failed": "生成未能启动",
            "model-request-starting": "画面已准备，等待模型",
            "image-model-completed": "图像理解完成",
            "capture-context-changed-before-model": "提交前活动窗口已变化",
            "capture-context-changed-before-presentation": "显示前活动窗口已变化",
        }
        presentation_labels = {
            "unknown": "旧记录没有保存最终显示结果",
            "pending": "等待气泡显示",
            "shown": "气泡已显示",
            "quiet": "气泡未显示",
            "cancelled": "本轮已取消",
            "unread": "未确认显示，已保留为未读",
        }
        presentation_reason_labels = {
            "": "",
            "awaiting-presentation": "等待原生窗口确认",
            "window-exposed": "原生窗口已确认可见",
            "presentation-ack-timeout": "窗口未在时限内确认可见",
            "presentation-suppressed": "当前界面要求保持安静",
            "privacy-suppressed": "隐私场景要求保持安静",
            "foreground-changed": "显示前活动窗口已变化",
            "capture-context-changed-before-presentation": "显示前活动窗口已变化",
            "duplicate-suppressed": "与近期内容过于相似",
            "engine-rejected": "本轮没有创建出可显示气泡",
            "authorization-revoked": "屏幕观察授权已撤销",
            "browser-authorization-revoked": "浏览器观察授权已撤销",
            "generation-cancelled": "生成任务已取消",
            "quality-rejected": "内容没有通过本地质量检查",
            "dismissed-before-presentation": "确认显示前已关闭",
            "process-restarted-before-presentation": "程序重启前未确认窗口可见，已保留为未读",
            "unsafe-resume": "恢复时当前场景不再安全",
        }
        detail = ""
        if state in {"stabilizing", "window-not-stable"}:
            remaining = max(
                0,
                round(
                    float(value.get("requiredStableSeconds", 0))
                    - float(value.get("stableSeconds", 0))
                ),
            )
            detail = f"约 {remaining} 秒后开始等待自然停顿"
        elif state == "cooldown":
            remaining = max(0, round(float(value.get("cooldownRemainingSeconds", 0))))
            detail = f"约 {max(1, (remaining + 59) // 60)} 分钟后可再次出现"
        elif state == "user-active":
            detail = "停顿 6–60 秒时，莉莉丝才会轻声出现"
        elif state == "user-away":
            detail = "回到电脑并自然停顿后再继续"
        modality = dict(self.runtime.modality_status)
        image_model = str(modality.get("imageModel", "") or "")
        modality_retry_seconds = max(
            0.0, self._modality_retry_due_at - time.monotonic()
        )
        if not self._activity_enabled:
            observation_mode_short = "感知已关闭"
            observation_mode_label = "应用感知已关闭"
            observation_mode_detail = "不会触发主动陪伴或截图"
        elif self.activity.paused:
            observation_mode_short = "感知已暂停"
            observation_mode_label = "应用感知已暂停"
            observation_mode_detail = "恢复后才会继续等待自然停顿；暂停期间不会截图"
        elif self._smart_observation and modality.get("checked") and not image_model:
            observation_mode_short = "感知中 · 不截图"
            observation_mode_label = "应用感知已开启 · 当前不截图"
            observation_mode_detail = (
                "屏幕观察虽已授权，但当前没有可用图像模型；"
                "仍只使用应用类别与自然停顿"
            )
        elif self._smart_observation:
            observation_mode_short = "屏幕观察已授权"
            observation_mode_label = "应用感知已开启 · 屏幕观察已授权"
            observation_mode_detail = (
                "自动陪伴只会在本地隐私规则允许、窗口稳定且自然停顿时，"
                "尝试一次非浏览器活动窗口截图；浏览器像素观察在 v0.3.36 "
                "暂不开放，只使用应用类别"
            )
        else:
            observation_mode_short = "感知中 · 不截图"
            observation_mode_label = "应用感知已开启 · 不截图"
            observation_mode_detail = (
                "默认只把应用类别（不含窗口标题与内容）交给已登录的 "
                "ChatGPT/Codex 订阅生成，不需要 API Key；截图必须另行明确授权"
            )
        quiet_states = {
            "disabled",
            "stopped",
            "paused",
            "no-context",
            "no-foreground-window",
            "assistant-ui",
            "signals-only",
            "protected-content",
            "password-manager",
            "security-dialog",
            "remote-desktop",
            "meeting",
            "private-browsing",
            "payment-window",
            "application-blocked",
        }
        gate_reason = str(gate.get("reason", "allowed"))
        gate_labels = {
            "frequency-off": "主动陪伴频率已关闭",
            "snoozed": "主动陪伴已暂停一会儿",
            "daily-limit": "今天的主动陪伴已达到上限",
            "cooldown": "正在保持安静间隔",
        }
        gate_detail = ""
        if gate_reason == "frequency-off":
            gate_detail = "在频率中选择安静、平衡、活泼或自定义后，才会自动出现"
        elif gate_reason == "snoozed":
            until = str(gate.get("snoozeUntil", ""))
            try:
                local_until = datetime.fromisoformat(until).astimezone()
                gate_detail = f"会安静到 {local_until.strftime('%H:%M')}"
            except ValueError:
                gate_detail = "暂停结束后会继续等待自然停顿"
        elif gate_reason == "daily-limit":
            gate_detail = (
                f"今天已出现 {int(gate.get('countToday', 0))} 条；"
                "明天会自动恢复"
            )
        elif gate_reason == "cooldown":
            remaining = max(0, round(float(gate.get("remainingSeconds", 0))))
            gate_detail = f"约 {max(1, (remaining + 59) // 60)} 分钟后可再次出现"

        # This is deliberately an earliest *possible* opportunity, not a
        # countdown promise.  Automatic companionship still needs a natural
        # pause and a non-sensitive foreground window when the wait reaches
        # zero.  Keep the three independent clocks visible for diagnostics,
        # while giving QML one read-only aggregate to format consistently.
        stable_remaining = max(
            0.0,
            float(value.get("requiredStableSeconds", 0.0) or 0.0)
            - float(value.get("stableSeconds", 0.0) or 0.0),
        )
        activity_cooldown_remaining = max(
            0.0,
            float(value.get("cooldownRemainingSeconds", 0.0) or 0.0),
        )
        gate_remaining = max(
            0.0,
            float(gate.get("remainingSeconds", 0.0) or 0.0),
        )
        automatic_opportunity_block = ""
        if bool(self._delivery_record.get("unread")):
            automatic_opportunity_block = "unread-pending"
        elif not self._activity_enabled:
            automatic_opportunity_block = "disabled"
        elif self.activity.paused:
            automatic_opportunity_block = "paused"
        elif gate_reason == "frequency-off":
            automatic_opportunity_block = "frequency-off"
        elif gate_reason == "daily-limit":
            automatic_opportunity_block = "daily-limit"
        automatic_opportunity = {
            "available": not automatic_opportunity_block,
            "blockReason": automatic_opportunity_block,
            "waitSeconds": round(
                max(
                    stable_remaining,
                    activity_cooldown_remaining,
                    gate_remaining,
                ),
                1,
            ),
            "stableRemainingSeconds": round(stable_remaining, 1),
            "activityCooldownRemainingSeconds": round(
                activity_cooldown_remaining, 1
            ),
            "gateRemainingSeconds": round(gate_remaining, 1),
        }

        generation_mode = (
            "local-safe-fallback"
            if self._last_generation_model == "local-safe-fallback"
            else ("subscription" if self._last_generation_model else "not-used")
        )
        generation_label = {
            "local-safe-fallback": "内置本地陪伴文案",
            "subscription": "ChatGPT/Codex 订阅模型",
            "not-used": "尚未生成",
        }[generation_mode]
        if (
            state == "sent"
            and generation_mode == "local-safe-fallback"
            and not detail
        ):
            detail = "订阅或本机模型暂不可用；本次已使用内置本地文案，主动陪伴仍在运行"

        # The activity state answers whether a natural pause is safe.  The
        # persisted emission gate separately answers whether the user asked us
        # to stay quiet.  Surface the stricter result so settings never claim
        # “allowed” while frequency, snooze, interval or the daily cap is
        # actually suppressing every bubble.
        effective_state_label = state_labels.get(state, state or "等待")
        effective_state_detail = detail
        if (
            not self._busy
            and self._activity_enabled
            and not self.activity.paused
            and state not in quiet_states
            and not bool(gate.get("allowed", False))
        ):
            effective_state_label = gate_labels.get(gate_reason, effective_state_label)
            effective_state_detail = gate_detail or effective_state_detail
        if generation_mode == "local-safe-fallback" and (
            state == "sent" or bool(self._bubble)
        ):
            fallback_detail = (
                "订阅或本机模型暂不可用；本次已使用内置本地文案，"
                "主动陪伴仍在运行"
            )
            if fallback_detail not in effective_state_detail:
                effective_state_detail = (
                    f"{effective_state_detail}；{fallback_detail}"
                    if effective_state_detail
                    else fallback_detail
                )

        compact_state_labels = {
            "not-started": "陪伴 · 尚未启动",
            "waiting-for-foreground": "陪伴 · 等待应用",
            "waiting": "陪伴 · 等待停顿",
            "stabilizing": "陪伴 · 熟悉窗口",
            "window-not-stable": "陪伴 · 熟悉窗口",
            "user-active": "陪伴 · 等你停顿",
            "user-away": "陪伴 · 等你回来",
            "cooldown": "陪伴 · 安静间隔",
            "allowed": "陪伴 · 准备说话",
            "sent": "陪伴 · 刚刚说过",
            "disabled": "陪伴 · 已关闭",
            "stopped": "陪伴 · 已关闭",
            "paused": "陪伴 · 已暂停",
            "no-context": "陪伴 · 等待应用",
            "no-foreground-window": "陪伴 · 等待应用",
            "assistant-ui": "陪伴 · 当前安静",
            "signals-only": "陪伴 · 当前静默",
            "protected-content": "陪伴 · 隐私静默",
            "password-manager": "陪伴 · 隐私静默",
            "security-dialog": "陪伴 · 系统静默",
            "remote-desktop": "陪伴 · 当前静默",
            "meeting": "陪伴 · 会议静默",
            "private-browsing": "陪伴 · 隐私静默",
            "payment-window": "陪伴 · 隐私静默",
            "application-blocked": "陪伴 · 此应用静默",
        }
        compact_status_label = compact_state_labels.get(state, "陪伴 · 等待")
        if bool(self._delivery_record.get("unread")):
            compact_status_label = "陪伴 · 1 条未读"
        elif self._busy and state not in quiet_states:
            compact_status_label = "陪伴 · 正在整理"
        elif (
            not bool(gate.get("allowed", False))
            and state not in quiet_states
            and state != "sent"
        ):
            compact_status_label = {
                "frequency-off": "陪伴 · 频率关闭",
                "snoozed": "陪伴 · 暂停中",
                "daily-limit": "陪伴 · 今日已满",
                "cooldown": "陪伴 · 安静间隔",
            }.get(gate_reason, compact_status_label)

        cached_news = sum(
            1 for item in self._content_items if item.category is ContentCategory.NEWS
        )
        cached_research = sum(
            1 for item in self._content_items if item.category is ContentCategory.RESEARCH
        )
        value.update(
            {
                "smartObservationEnabled": self._smart_observation,
                "browserSingleCaptureEnabled": self._browser_single_capture,
                "browserContext": self._is_browser_context(
                    self.activity.current_context
                ),
                "onlineContentEnabled": self._online_content,
                "configuredEnabled": self._activity_enabled,
                "modality": modality,
                "modalityProbeBusy": self._probe_busy,
                "modalityRetrySeconds": round(modality_retry_seconds, 1),
                "modalityProbeState": (
                    "checking"
                    if self._probe_busy
                    else (
                        "ready"
                        if image_model
                        else (
                            "retry-scheduled"
                            if modality_retry_seconds > 0
                            else ("unavailable" if modality.get("checked") else "unchecked")
                        )
                    )
                ),
                "busy": self._busy,
                "gate": gate,
                "generationMode": generation_mode,
                "generationLabel": generation_label,
                "lastGenerationModel": self._last_generation_model,
                "lastGenerationError": self._last_generation_error,
                "requestFeedback": self._request_feedback,
                "requestFeedbackKind": self._request_feedback_kind,
                "delivery": self.deliveryStatus,
                "lastCaptureOutcome": str(
                    self._last_capture_status.get("outcome", "never")
                ),
                "lastCaptureReason": str(
                    self._last_capture_status.get("reason", "")
                ),
                "lastCaptureReasonLabel": capture_reason_labels.get(
                    str(self._last_capture_status.get("reason", "")),
                    "状态已更新",
                ),
                "lastCaptureAt": str(self._last_capture_status.get("at", "")),
                "captureAttempted": bool(
                    self._last_capture_status.get("captureAttempted", False)
                ),
                "imageSubmitted": bool(
                    self._last_capture_status.get("imageSubmitted", False)
                ),
                "imageResponseAccepted": bool(
                    self._last_capture_status.get("imageResponseAccepted", False)
                ),
                "lastCapturePixelsUsed": bool(
                    self._last_capture_status.get("pixelsUsed", False)
                ),
                "lastCaptureModel": str(
                    self._last_capture_status.get("model", "")
                ),
                "lastCaptureModelLabel": {
                    LUNA_MODEL: "Luna",
                    TERRA_MODEL: "Terra",
                }.get(str(self._last_capture_status.get("model", "")), ""),
                "lastCaptureEvidenceConfidence": str(
                    self._last_capture_status.get("evidenceConfidence", "none")
                ),
                "lastCapturePresentationOutcome": str(
                    self._last_capture_status.get(
                        "presentationOutcome", "unknown"
                    )
                ),
                "lastCapturePresentationLabel": presentation_labels.get(
                    str(
                        self._last_capture_status.get(
                            "presentationOutcome", "unknown"
                        )
                    ),
                    "最终显示状态未知",
                ),
                "lastCapturePresentationReason": str(
                    self._last_capture_status.get("presentationReason", "")
                ),
                "lastCapturePresentationReasonLabel": (
                    presentation_reason_labels.get(
                        str(
                            self._last_capture_status.get(
                                "presentationReason", ""
                            )
                        ),
                        "",
                    )
                ),
                "lastCapturePresentationAt": str(
                    self._last_capture_status.get("presentationAt", "")
                ),
                "automaticOpportunity": automatic_opportunity,
                "contentAvailability": {
                    "onlineAuthorized": self._online_content,
                    "cachedItems": len(self._content_items),
                    "newsItems": cached_news,
                    "researchItems": cached_research,
                },
                "observationModeShort": observation_mode_short,
                "observationModeLabel": observation_mode_label,
                "observationModeDetail": observation_mode_detail,
                "compactStatusLabel": compact_status_label,
                "stateLabel": (
                    "正在整理一句话"
                    if self._busy and state not in quiet_states
                    else effective_state_label
                ),
                "stateDetail": effective_state_detail,
                "lastContextLabel": context_labels.get(context_type, context_type or "尚未发送"),
            }
        )
        return value

    @Property("QVariantMap", notify=bubbleChanged)
    def bubble(self) -> dict[str, Any]:
        return dict(self._bubble)

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def presentationSuppressed(self) -> bool:
        return self._presentation_suppressed

    def _pause_bubble_lifetime(self) -> None:
        if (
            not self._bubble
            or not bool(self._bubble.get("visible"))
            or str(self._bubble.get("deliveryState", "")) == "unread"
        ):
            return
        self._bubble_expiry_timer.stop()
        self._presentation_ack_timer.stop()
        raw_expiry = str(self._bubble.get("expiresAt", ""))
        if raw_expiry:
            try:
                expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                self._bubble_ttl_remaining_seconds = max(
                    0.1, (expiry - datetime.now(UTC)).total_seconds()
                )
            except ValueError:
                self._bubble_ttl_remaining_seconds = _BUBBLE_PRESENTATION_SECONDS
        self._presentation_ack_pending = False
        self._bubble_presented = False
        self._bubble["expiresAt"] = ""
        self._bubble["deliveryState"] = "suppressed"
        self._set_delivery_state(
            "suppressed",
            "privacy-suppressed",
            expires_at="",
        )
        if self._bubble_capture_diagnostic_token:
            self._record_capture_presentation(
                "pending",
                "privacy-suppressed",
                attempt_token=self._bubble_capture_diagnostic_token,
            )
        self.bubbleChanged.emit()

    def _resume_bubble_after_suppression(self) -> None:
        if (
            not self._bubble
            or str(self._bubble.get("deliveryState", "")) != "suppressed"
        ):
            return
        _context, safe, _reason = self._reconcile_foreground_for_bubble()
        if not safe:
            # Keep no hidden prose/capture alive in a newly unsafe context.
            # The durable proactive session can be reconstructed only after
            # the user explicitly reopens it in a later safe context.
            self._clear_bubble(reason="unsafe-resume", preserve_unread=True)
            return
        self._bubble["visible"] = True
        self._bubble["expiresAt"] = ""
        self._bubble["deliveryState"] = "waiting-present-ack"
        self._bubble_presented = False
        self._presentation_ack_pending = True
        self._set_delivery_state(
            "waiting-present-ack",
            "privacy-resumed",
            expires_at="",
        )
        if self._bubble_capture_diagnostic_token:
            self._record_capture_presentation(
                "pending",
                "awaiting-presentation",
                attempt_token=self._bubble_capture_diagnostic_token,
            )
        self._presentation_ack_timer.start(_PRESENTATION_ACK_TIMEOUT_MS)
        self.bubbleChanged.emit()

    @Slot(str, bool, bool, int, result=bool)
    def ackPresented(
        self,
        bubble_id: str,
        visible: bool,
        exposed: bool,
        _presentation_revision: int = 0,
    ) -> bool:
        """Acknowledge native-window exposure after one presentation turn."""

        if (
            self._closing
            or self._presentation_suppressed
            or not self._presentation_ack_pending
            or str(self._bubble.get("id", "")) != str(bubble_id)
            or not bool(visible)
            or not bool(exposed)
        ):
            return False
        self._presentation_ack_timer.stop()
        self._presentation_ack_pending = False
        self._bubble_presented = True
        presented_at = datetime.now(UTC)
        lifetime = max(0.1, float(self._bubble_ttl_remaining_seconds))
        expires_at = presented_at + timedelta(seconds=lifetime)
        self._bubble["expiresAt"] = expires_at.isoformat()
        self._bubble["deliveryState"] = "presented"
        if self._bubble_capture_diagnostic_token:
            self._record_capture_presentation(
                "shown",
                "window-exposed",
                attempt_token=self._bubble_capture_diagnostic_token,
            )
            # Once exposure is proven, later TTL/dismissal cleanup must not
            # rewrite the historical fact that this image-grounded bubble was
            # actually shown.
            self._bubble_capture_diagnostic_token = 0
        self._set_delivery_state(
            "presented",
            "window-exposed",
            presented_at=presented_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )
        if str(self._bubble.get("contextType", "")) == "active-window-image":
            self._set_request_feedback(
                "气泡已经呈现；本次使用单次活动窗口截图，暂存文件已删除，压缩图仅在气泡期间留在内存",
                "shown",
            )
        else:
            self._set_request_feedback(
                "气泡已经呈现；本次仅使用应用类别，没有读取窗口标题或文字",
                "shown",
            )
        self._schedule_bubble_expiry()
        self.bubbleChanged.emit()
        self.changed.emit()
        return True

    def _presentation_ack_timed_out(self) -> None:
        if self._closing or not self._presentation_ack_pending or not self._bubble:
            return
        self._presentation_ack_pending = False
        self._set_delivery_state(
            "unread",
            "presentation-ack-timeout",
            unread=True,
            expires_at="",
        )
        self._clear_bubble(
            reason="presentation-ack-timeout",
            preserve_unread=True,
        )

    @Slot(bool)
    def setPresentationSuppressed(self, suppressed: bool) -> None:
        """Synchronise the QML/native presentation gate on the Qt thread.

        Entering suppression invalidates the current proactive generation so a
        late worker result cannot be committed after the quiet interval.  The
        existing bubble remains intact. Its presentation TTL is paused while
        hidden, then a safe foreground must receive a fresh QML ACK before the
        remaining lifetime resumes.
        """

        next_value = bool(suppressed)
        was_ready = self._presentation_sync_ready
        changed = next_value != self._presentation_suppressed
        self._presentation_sync_ready = True
        if changed:
            self._presentation_suppressed = next_value
            if next_value:
                self._presentation_epoch += 1
                self._cancel_active_generation(
                    "presentation-suppressed",
                    capture_reason="presentation-suppressed",
                )
                self._pause_bubble_lifetime()
            else:
                self._resume_bubble_after_suppression()
        if changed or not was_ready:
            self.changed.emit()

    @Property("QVariantList", notify=sourcesChanged)
    def sources(self) -> list[dict[str, Any]]:
        return [
            {
                **value,
                "authorized": self._online_content,
                "cachedItems": self._source_counts.get(str(value["id"]), 0),
                "custom": str(value["id"]).startswith("custom-"),
            }
            for value in self.content.sources()
        ]

    @staticmethod
    def _validated_feed_url(value: str) -> str:
        clean = str(value).strip()
        parsed = urlsplit(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("订阅地址必须是公开的 HTTP/HTTPS URL")
        host = parsed.hostname.casefold()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ValueError("不能订阅本机或局域网地址")
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError("不能订阅本机或局域网地址")
        return clean[:2048]

    def _custom_source_values(self) -> list[dict[str, str]]:
        raw = self.database.get_setting("content_custom_feeds", [])
        return [dict(value) for value in raw if isinstance(value, dict)] if isinstance(raw, list) else []

    def _load_custom_sources(self) -> None:
        for value in self._custom_source_values():
            try:
                provider_id = str(value["id"])
                label = str(value["label"]).strip()[:80]
                url = self._validated_feed_url(str(value["url"]))
                if not provider_id.startswith("custom-") or not label:
                    continue
                self.content.providers[provider_id] = RssAtomProvider(provider_id, label, url)
            except (KeyError, TypeError, ValueError):
                continue

    def _hydrate_cached_content(self) -> None:
        known: dict[str, ContentItem] = {}
        for provider_id in self.content.providers:
            try:
                result = self.content.refresh(
                    provider_id,
                    "",
                    limit=10,
                    allow_network=False,
                    force=False,
                )
            except (KeyError, RuntimeError, ValueError):
                continue
            self._source_counts[provider_id] = len(result.items)
            for item in result.items:
                known[item.id] = item
        self._content_items = list(known.values())[-240:]

    def _scene_label(self, context: ForegroundContext | None) -> str:
        if context is None:
            return ""
        if context.scene_label:
            return str(context.scene_label)[:80]
        if context.is_game:
            return "游戏"
        process = Path(context.process_name).stem.casefold()
        for names, label in _APPLICATION_SCENES:
            if process in names:
                return label
        return "当前应用"

    @staticmethod
    def _same_window_identity(
        left: ForegroundContext | None,
        right: ForegroundContext | None,
    ) -> bool:
        if left is None or right is None:
            return False
        return (
            int(left.hwnd),
            int(left.process_id),
            left.process_name.casefold(),
            left.window_class.casefold(),
        ) == (
            int(right.hwnd),
            int(right.process_id),
            right.process_name.casefold(),
            right.window_class.casefold(),
        )

    @staticmethod
    def _same_capture_identity(
        left: ForegroundContext | None,
        right: ForegroundContext | None,
    ) -> bool:
        return bool(
            CompanionController._same_window_identity(left, right)
            and left is not None
            and right is not None
            and ephemeral_title_fingerprint(left.title)
            == ephemeral_title_fingerprint(right.title)
        )

    @staticmethod
    def _is_browser_context(context: ForegroundContext | None) -> bool:
        if context is None:
            return False
        return Path(str(context.process_name)).name.casefold() in _BROWSER_PROCESSES

    def _capture_policy(
        self, context: ForegroundContext | None
    ) -> tuple[bool, str]:
        """Return the effective pixel policy without changing bubble policy."""

        if context is None:
            return False, "foreground-changed"
        # v0.3.36 has no browser-pixel escape hatch.  Check this before title-
        # based guards so ordinary pages, private windows and login surfaces
        # all share the same fail-closed pixel boundary.
        if self._is_browser_context(context):
            return False, "browser-capture-paused"
        decision = self.activity.guard.evaluate(context)
        if not decision.can_capture:
            return False, decision.reason
        return True, "allowed"

    @staticmethod
    def _model_context_metadata(
        context: ForegroundContext | None,
        scene_label: str,
        momentum_topic: str = "",
    ) -> dict[str, object]:
        """Return the intentionally small cloud-generation context.

        Active companion generation is allowed to know the coarse application
        category. Window titles, document names, URLs and window classes stay
        in the ephemeral local guard context and are never placed in the model
        prompt, even when smart observation is disabled.
        """

        value: dict[str, object] = {
            "applicationCategory": str(scene_label or "当前应用")[:80],
            "fullScreen": bool(context.full_screen) if context is not None else False,
            "inputScope": "application-category-only",
        }
        weak_topic = str(momentum_topic).strip()[:80]
        if weak_topic and weak_topic != value["applicationCategory"]:
            value["weakMomentumTopic"] = weak_topic
        return value

    def _consider(self) -> None:
        if self._closing:
            return
        self._expire_bubble()
        # Missing history, an exhausted redelivery budget or an expired
        # retention window archives only the delivery flag.  The durable
        # proactive session remains searchable and new companionship can
        # continue on this same heartbeat.
        self._prune_unread_delivery()
        context = self.activity.current_context
        # WinEvent hooks can legitimately miss the transition that happens
        # when one of Lilies' own no-activate/settings windows closes.  The old
        # code noticed the HWND mismatch and returned forever, leaving the
        # companion stuck on an assistant-ui context.  Reconcile only that
        # mismatch; normal context collection remains event-driven.
        try:
            current_hwnd = int(self._foreground_provider() or 0)
        except (OSError, RuntimeError, TypeError, ValueError):
            current_hwnd = 0
        if current_hwnd and (context is None or current_hwnd != context.hwnd):
            now = time.monotonic()
            if now - self._last_foreground_reconcile_at >= 1.0:
                self._last_foreground_reconcile_at = now
                try:
                    self.updateForegroundContext(self.reader(current_hwnd))
                except (OSError, RuntimeError, TypeError, ValueError):
                    self.changed.emit()
                    return
                context = self.activity.current_context
        decision = self.activity.consider_observation()
        if context is not None:
            label = self._scene_label(context)
            self.momentum.observe(label, 1.0, time.monotonic())
        self.changed.emit()
        if (
            str(self._delivery_record.get("state", "")) == "unread"
            and bool(self._delivery_record.get("unread"))
        ):
            now = time.monotonic()
            safe_to_redeliver = bool(
                self._activity_enabled
                and not self.activity.paused
                and context is not None
                and self.activity.guard.evaluate(context).can_bubble
                and float(decision.stable_seconds) >= self.activity.stable_seconds
                and self.activity.minimum_idle_seconds
                <= float(decision.idle_seconds)
                <= self.activity.maximum_idle_seconds
            )
            if (
                not self._busy
                and safe_to_redeliver
                and now - self._last_unread_redelivery_at
                >= _UNREAD_REDELIVERY_RETRY_SECONDS
            ):
                self._last_unread_redelivery_at = now
                self._reopen_unread(automatic=True)
            return
        if self._busy or not decision.can_bubble:
            return
        allowed, _reason = self.engine.gate.can_emit(datetime.now(UTC))
        if not allowed:
            return
        self._start_generation(context, force=False)

    def _enabled_categories(self) -> list[ContentCategory]:
        return [
            category
            for category in ContentCategory
            if self.preferences_model.category_enabled(category)
        ]

    def _choose_content(
        self,
        scene_label: str,
        category: ContentCategory | None = None,
    ) -> ContentItem | None:
        candidates = [
            item
            for item in self._content_items
            if category is None or item.category is category
        ]
        if not candidates:
            return None
        interests = {
            str(value).casefold(): 1.0
            for value in self.database.get_setting("companion_interests", [])
            if str(value).strip()
        }
        ranked = rank_content(
            candidates,
            interests=interests,
            scene_label=scene_label,
            preferences=self.preferences_model,
            recent_ids=self._recent_content_ids,
            recent_sources=self._recent_sources,
        )
        return ranked[0][1] if ranked else None

    def _choose_category(
        self, scene_label: str
    ) -> tuple[ContentCategory | None, ContentItem | None]:
        requested = self._requested_category
        self._requested_category = None
        if requested is not None:
            candidates = [requested]
            for category in candidates:
                if category in {ContentCategory.NEWS, ContentCategory.RESEARCH}:
                    content_item = self._choose_content(scene_label, category)
                    if content_item is not None and content_item.published_at:
                        return category, content_item
                    continue
                return category, None
            return None, None

        eligible: dict[ContentCategory, ContentItem | None] = {}
        for category in self._enabled_categories():
            if category in {ContentCategory.NEWS, ContentCategory.RESEARCH}:
                content_item = self._choose_content(scene_label, category)
                if content_item is not None and content_item.published_at:
                    eligible[category] = content_item
                continue
            eligible[category] = None
        if not eligible:
            return None, None

        # Smooth weighted round-robin gives the 0–100 preference sliders real,
        # deterministic meaning without producing random bursts.  With equal
        # weights this is the previous round-robin order; unequal weights are
        # distributed evenly over time rather than repeated in one block.
        total_weight = 0
        for category in ContentCategory:
            if category not in eligible:
                continue
            weight = int(self.preferences_model.category_weights[category])
            total_weight += weight
            self._category_smooth_scores[category] = (
                self._category_smooth_scores.get(category, 0) + weight
            )
        selected = max(
            eligible,
            key=lambda category: self._category_smooth_scores.get(category, 0),
        )
        self._category_smooth_scores[selected] -= total_weight
        return selected, eligible[selected]

    def _submit_model_task(
        self,
        model_id: str,
        kind: ModelTaskKind,
        payload: dict[str, Any],
        *,
        context_bound: bool,
        ttl_seconds: float,
    ) -> str:
        if self._closing or self._model_broker is None:
            return ""
        task = self._model_broker.submit(
            model_id,
            kind,
            payload,
            context_bound=context_bound,
            expires_at=time.monotonic() + max(1.0, float(ttl_seconds)),
        )
        with self._model_task_lock:
            self._model_task_ids.add(task.id)
        return task.id

    def _forget_model_task(self, task_id: str) -> None:
        if not task_id:
            return
        with self._model_task_lock:
            self._model_task_ids.discard(task_id)

    def _abandon_model_task(self, task_id: str, reason: str) -> None:
        if task_id and self._model_broker is not None:
            task = self._model_broker.get(task_id)
            if task is not None and not task.terminal:
                try:
                    self._model_broker.cancel(task_id, reason=reason)
                except (KeyError, ValueError):
                    pass
        self._forget_model_task(task_id)

    def _cancel_model_tasks(self, reason: str) -> None:
        if self._model_broker is None:
            return
        context_bound_only = reason == "foreground-context-changed"
        with self._model_task_lock:
            task_ids = tuple(self._model_task_ids)
        for task_id in task_ids:
            task = self._model_broker.get(task_id)
            if task is None or task.terminal:
                continue
            if context_bound_only and not task.context_bound:
                continue
            try:
                self._model_broker.cancel(task_id, reason=reason)
            except (KeyError, ValueError):
                pass

    def _begin_capture_diagnostic(self) -> int:
        """Start one content-free capture attempt and fence older workers."""

        with self._capture_diagnostic_lock:
            self._capture_diagnostic_serial += 1
            self._active_capture_diagnostic_token = self._capture_diagnostic_serial
            return self._active_capture_diagnostic_token

    def _record_capture_outcome(
        self,
        outcome: str,
        reason: str = "",
        *,
        attempt_token: int | None = None,
        model: str = "",
        evidence_confidence: str = "none",
    ) -> None:
        """Persist only a content-free capture diagnostic.

        Window handles, titles, image paths and model prose are deliberately
        excluded.  A generation-scoped token also prevents a cancelled old
        worker from overwriting the diagnostic for a newer foreground turn.
        """

        with self._capture_diagnostic_lock:
            if attempt_token is None:
                # A controller-level cancellation/revocation starts a new
                # terminal turn and invalidates every older worker.
                attempt_token = self._begin_capture_diagnostic()
            else:
                try:
                    attempt_token = int(attempt_token)
                except (TypeError, ValueError):
                    return
                if attempt_token != self._active_capture_diagnostic_token:
                    return
            normalized_outcome = str(outcome)[:40]
            normalized_reason = str(reason)[:80]
            if normalized_reason not in _CAPTURE_REASON_CODES:
                normalized_reason = ""
            same_attempt = self._last_capture_status_token == attempt_token
            previous_submitted = bool(
                self._last_capture_status.get("imageSubmitted", False)
            ) and same_attempt
            attempted = normalized_outcome not in {"never", "skipped"}
            submitted = normalized_outcome in {"submitted", "used"} or (
                previous_submitted
                and normalized_outcome in {"cancelled", "discarded", "failed"}
                and normalized_reason in _POST_STAGE_CAPTURE_REASONS
            )
            normalized_confidence = str(evidence_confidence).casefold()[:16]
            if normalized_confidence not in {"none", "low", "medium", "high"}:
                normalized_confidence = "none"
            pixels_used = normalized_outcome == "used"
            previous_model = (
                _capture_model_id(self._last_capture_status.get("model", ""))
                if same_attempt and previous_submitted
                else ""
            )
            previous_session_id = (
                _capture_receipt_id(
                    self._last_capture_status.get("sessionId", "")
                )
                if same_attempt
                else ""
            )
            previous_bubble_id = (
                _capture_receipt_id(
                    self._last_capture_status.get("bubbleId", "")
                )
                if same_attempt
                else ""
            )
            normalized_model = (
                _capture_model_id(model) or previous_model
                if submitted
                else ""
            )
            presentation_outcome = (
                "pending"
                if normalized_outcome in {"staged", "submitted", "used"}
                else (
                    "cancelled"
                    if normalized_outcome == "cancelled"
                    else (
                        "unknown"
                        if normalized_outcome == "never"
                        else "quiet"
                    )
                )
            )
            presentation_reason = (
                "awaiting-presentation"
                if presentation_outcome == "pending"
                else (
                    normalized_reason
                    if normalized_reason in _CAPTURE_PRESENTATION_REASONS
                    else (
                        "quality-rejected"
                        if normalized_reason in _QUIET_GENERATION_SKIP_REASONS
                        else ""
                    )
                )
            )
            now = datetime.now(UTC).isoformat()
            self._last_capture_status = {
                "schemaVersion": 4,
                "outcome": normalized_outcome,
                "reason": normalized_reason,
                "at": now,
                "captureAttempted": attempted,
                "imageSubmitted": submitted,
                "imageResponseAccepted": pixels_used,
                "pixelsUsed": pixels_used,
                "model": normalized_model,
                "evidenceConfidence": (
                    normalized_confidence if pixels_used else "none"
                ),
                "presentationOutcome": presentation_outcome,
                "presentationReason": presentation_reason,
                "presentationAt": now,
                "sessionId": previous_session_id,
                "bubbleId": previous_bubble_id,
            }
            self._last_capture_status_token = attempt_token
            self._persist_capture_status()

    def _persist_capture_status(self) -> bool:
        """Persist content-free capture metadata without affecting delivery.

        Diagnostics are intentionally best-effort. A full or temporarily
        unavailable database must never prevent a bubble ACK, privacy cleanup,
        capture release, or TTL scheduling.
        """

        try:
            self.database.set_setting(
                "companion_last_capture_status", dict(self._last_capture_status)
            )
        except Exception:
            return False
        return True

    def _record_capture_presentation(
        self,
        outcome: str,
        reason: str,
        *,
        attempt_token: int,
        session_id: str = "",
        bubble_id: str = "",
    ) -> None:
        """Finalize display state without rewriting whether pixels were used."""

        normalized_outcome = str(outcome)[:24]
        normalized_reason = str(reason)[:80]
        if normalized_outcome not in _CAPTURE_PRESENTATION_OUTCOMES:
            return
        if normalized_reason not in _CAPTURE_PRESENTATION_REASONS:
            normalized_reason = ""
        try:
            token = int(attempt_token)
        except (TypeError, ValueError):
            return
        with self._capture_diagnostic_lock:
            if (
                token <= 0
                or token != self._active_capture_diagnostic_token
                or token != self._last_capture_status_token
            ):
                return
            normalized_session_id = _capture_receipt_id(session_id)
            normalized_bubble_id = _capture_receipt_id(bubble_id)
            if bool(normalized_session_id) != bool(normalized_bubble_id):
                normalized_session_id = normalized_bubble_id = ""
            self._last_capture_status["schemaVersion"] = 4
            self._last_capture_status["presentationOutcome"] = normalized_outcome
            self._last_capture_status["presentationReason"] = normalized_reason
            self._last_capture_status["presentationAt"] = datetime.now(
                UTC
            ).isoformat()
            if normalized_session_id and normalized_bubble_id:
                self._last_capture_status["sessionId"] = normalized_session_id
                self._last_capture_status["bubbleId"] = normalized_bubble_id
            self._persist_capture_status()

    def _set_inflight_capture(self, capture: StagedCapture | None) -> None:
        with self._capture_lock:
            self._inflight_capture = capture

    def _take_inflight_capture(
        self, expected: StagedCapture | None = None
    ) -> StagedCapture | None:
        with self._capture_lock:
            current = self._inflight_capture
            if expected is not None and current is not expected:
                return None
            self._inflight_capture = None
            return current

    def _cancel_active_generation(
        self,
        reason: str,
        *,
        capture_reason: str = "cancelled",
    ) -> bool:
        """Invalidate, abort and clean one proactive generation atomically."""

        had_active = bool(
            self._active_generation_token
            or self._generation_cancel_event is not None
            or self._inflight_capture is not None
        )
        if had_active:
            self._generation_serial += 1
            self._active_generation_token = 0
        had_capture = bool(
            self._active_generation_has_capture or self._inflight_capture is not None
        )
        capture_diagnostic_token = (
            self._active_generation_capture_diagnostic_token if had_active else 0
        )
        if had_capture and not capture_diagnostic_token:
            # Defensive compatibility for an in-flight capture restored or
            # injected without the newer generation-token association. We can
            # truthfully record cancellation, but must not invent submission
            # or pixel-use evidence.
            capture_diagnostic_token = self._begin_capture_diagnostic()
        self._active_generation_has_capture = False
        self._active_generation_capture_diagnostic_token = 0
        cancel_event = self._generation_cancel_event
        if had_active:
            self._generation_cancel_event = None
        if cancel_event is not None:
            cancel_event.set()
        self._cancel_model_tasks(reason)
        if had_active:
            self._active_generation_model_id = ""
        # The broker lease owns model cancellation.  Calling abort_model here
        # is unsafe while this proactive request is still queued: another
        # higher-priority chat may currently own the same Luna/Terra runtime.
        # Once our lease is acquired its cancellation watcher performs the
        # abort, including for the broker-less offline fallback.
        capture = self._take_inflight_capture() if had_active else None
        if capture is not None:
            try:
                capture.release()
            except OSError:
                pass
        if had_capture and capture_diagnostic_token:
            # Preserve the strongest fact already known about the pixels.  A
            # cancellation after model completion means “used, but not
            # presented”, while a cancellation during an in-flight model call
            # means “submitted, response not adopted”.  Neither may be
            # rewritten to “pixels were never sent”.
            capture_was_used = bool(
                self._last_capture_status_token == capture_diagnostic_token
                and self._last_capture_status.get("pixelsUsed", False)
            )
            if capture_was_used:
                presentation_reason = (
                    capture_reason
                    if capture_reason in _CAPTURE_PRESENTATION_REASONS
                    else "generation-cancelled"
                )
                self._record_capture_presentation(
                    "cancelled",
                    presentation_reason,
                    attempt_token=capture_diagnostic_token,
                )
            else:
                self._record_capture_outcome(
                    "cancelled",
                    capture_reason,
                    attempt_token=capture_diagnostic_token,
                )
                presentation_reason = (
                    capture_reason
                    if capture_reason in _CAPTURE_PRESENTATION_REASONS
                    else "generation-cancelled"
                )
                self._record_capture_presentation(
                    "cancelled",
                    presentation_reason,
                    attempt_token=capture_diagnostic_token,
                )
            # Fence the cancelled worker only after committing the terminal
            # receipt; any late callback carrying the old token is ignored.
            self._begin_capture_diagnostic()
        if had_active:
            self._busy = False
            if self._bubble.get("busy"):
                self._bubble["busy"] = False
                self.bubbleChanged.emit()
        return had_active

    def _start_worker(self, target: Callable[[], None], *, name: str) -> bool:
        """Start one tracked daemon unless shutdown has begun.

        Workers remain daemons because network/model transports can be outside
        Python's control.  Tracking still gives shutdown a bounded opportunity
        to reap cooperative workers, while the closing fence prevents any late
        signal from mutating UI or persistence state.
        """

        def tracked() -> None:
            try:
                target()
            finally:
                with self._worker_lock:
                    self._worker_threads.discard(threading.current_thread())

        with self._worker_lock:
            if self._closing:
                return False
            worker = threading.Thread(target=tracked, name=name, daemon=True)
            self._worker_threads.add(worker)
            try:
                worker.start()
            except RuntimeError:
                self._worker_threads.discard(worker)
                return False
        return True

    def _join_workers(self, timeout: float = 1.5) -> None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._worker_lock:
                workers = tuple(
                    worker
                    for worker in self._worker_threads
                    if worker is not threading.current_thread()
                )
            if not workers:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for worker in workers:
                worker.join(min(0.15, max(0.0, deadline - time.monotonic())))

    def _start_generation(
        self,
        context: ForegroundContext | None,
        *,
        force: bool,
        duplicate_retry: int = 0,
        manual_capture: bool = False,
    ) -> bool:
        if not force and time.monotonic() < self._generation_attempt_not_before:
            return False
        self._prune_unread_delivery()
        if bool(self._delivery_record.get("unread")):
            if force:
                self._set_request_feedback(
                    "还有一句未读陪伴；请先从托盘重新打开",
                    "quiet",
                )
                self.changed.emit()
            return False
        if not force and bool(self._bubble.get("visible")):
            # A short degraded retry must not replace a bubble that the user is
            # still reading.  Once it is dismissed, the independent retry
            # clock below can try the subscription model again promptly.
            return False
        if (
            self._closing
            or self._busy
            or self._probe_busy
            or self._presentation_suppressed
            or (self._active and not self._presentation_sync_ready)
        ):
            return False
        generation_context = context
        # The current window is the only factual scene label. Momentum may
        # gently influence topic selection, but must never make a new window
        # look like the previous application to the model or user.
        scene_label = self._scene_label(generation_context)
        category, content_item = self._choose_category(scene_label)
        if category is None:
            if force:
                self._set_request_feedback(
                    "没有可用内容：请至少开启一个本地类别，新闻与科研需另行授权来源",
                    "warning",
                )
                self.status_sink(
                    "没有可用的陪伴内容：请开启至少一个本地类别，"
                    "或为新闻/科研进展授权并刷新内容源"
                )
            self.changed.emit()
            return False
        captured_image: Any | None = None
        capture_requested = False
        capture_attempt_failed = False
        capture_fallback_requested = False
        # The ordinary explicit “生成一条场景陪伴” action remains text-only.
        # ``manual_capture`` belongs to a separate, plainly labelled one-shot
        # consent action and must still cross every automatic privacy guard.
        capture_enabled_for_turn = bool(
            self._smart_observation and (not force or manual_capture)
        )
        capture_diagnostic_token = (
            self._begin_capture_diagnostic() if capture_enabled_for_turn else 0
        )

        def record_capture(
            outcome: str,
            reason: str,
            *,
            model: str = "",
            evidence_confidence: str = "none",
        ) -> None:
            if capture_diagnostic_token:
                self._record_capture_outcome(
                    outcome,
                    reason,
                    attempt_token=capture_diagnostic_token,
                    model=model,
                    evidence_confidence=evidence_confidence,
                )

        def reject_manual_capture(message: str, reason: str) -> bool:
            if not manual_capture:
                return False
            record_capture("skipped", reason)
            self._set_request_feedback(str(message), "warning")
            self.status_sink(str(message))
            self.changed.emit()
            return True

        try:
            current_hwnd = int(self._foreground_provider() or 0)
        except (OSError, RuntimeError, TypeError, ValueError):
            current_hwnd = 0
        # Automatic observation has already passed its timing gate in
        # ``_consider``. Explicit scene-level generation never enters the
        # capture branch below, even if an image model is available.
        capture_timing_ready = capture_enabled_for_turn
        if capture_enabled_for_turn and not (
            self.runtime.modality_status.get("checked")
            and self.runtime.modality_status.get("imageModel")
        ):
            record_capture("skipped", "image-model-unavailable")
            if reject_manual_capture(
                "当前订阅模型没有可用的图像输入能力；没有截取窗口。",
                "image-model-unavailable",
            ):
                return False
        contextual_capture_allowed, contextual_capture_reason = self._capture_policy(
            context
        )
        if (
            capture_enabled_for_turn
            and capture_timing_ready
            and not contextual_capture_allowed
            and contextual_capture_reason
            in {"browser-capture-not-authorized", "browser-capture-paused"}
        ):
            # This is a pixel-only restriction.  The normal application-level
            # bubble continues below and never receives a title or page text.
            record_capture("skipped", contextual_capture_reason)
        if manual_capture and not contextual_capture_allowed:
            message = (
                "浏览器像素观察暂不开放；没有截取当前窗口。"
                if contextual_capture_reason
                in {"browser-capture-not-authorized", "browser-capture-paused"}
                else "当前窗口命中隐私或静默规则；没有截取窗口。"
            )
            if reject_manual_capture(
                message, contextual_capture_reason or "privacy-suppressed"
            ):
                return False
        if manual_capture and (context is None or not current_hwnd):
            if reject_manual_capture(
                "没有找到可安全观察的前台窗口；请先回到目标应用再试。",
                "foreground-changed",
            ):
                return False
        if manual_capture and context is not None and current_hwnd != context.hwnd:
            if reject_manual_capture(
                "前台窗口刚刚发生变化；没有截图，请回到目标窗口后再试。",
                "foreground-changed",
            ):
                return False
        if (
            capture_enabled_for_turn
            and capture_timing_ready
            and contextual_capture_allowed
            and context is not None
            and current_hwnd == context.hwnd
            and self.runtime.modality_status.get("checked")
            and self.runtime.modality_status.get("imageModel")
        ):
            try:
                verified_context = self.reader(current_hwnd)
                verified_hwnd = int(self._foreground_provider() or 0)
            except (OSError, RuntimeError, TypeError, ValueError):
                verified_context = None
                verified_hwnd = 0
            if (
                verified_context is not None
                and verified_hwnd == verified_context.hwnd
                and self._same_window_identity(context, verified_context)
            ):
                decision = self.activity.guard.evaluate(verified_context)
                preflight_changed = self.updateForegroundContext(verified_context)
                if preflight_changed:
                    record_capture("skipped", "window-content-changed")
                    if manual_capture:
                        self._set_request_feedback(
                            "窗口内容刚刚发生变化；请停留片刻后再观察一次。",
                            "warning",
                        )
                        self.changed.emit()
                    return False
                verified_capture_allowed, _verified_capture_reason = (
                    self._capture_policy(verified_context)
                )
                if not decision.can_capture or not verified_capture_allowed:
                    if manual_capture:
                        reject_manual_capture(
                            "当前窗口命中隐私或静默规则；没有截取窗口。",
                            "privacy-suppressed",
                        )
                    return False
                generation_context = verified_context
                scene_label = self._scene_label(generation_context)
                try:
                    # ImageGrab performs only the native HWND pixel copy on
                    # the Qt thread.  Validation, scaling and PNG encoding are
                    # intentionally deferred to the worker below.
                    captured_image = capture_window_image(verified_context.hwnd)
                except (OSError, RuntimeError, ValueError):
                    captured_image = None
                    capture_fallback_requested = bool(
                        verified_context.process_id
                        and native_capture_helper_available()
                    )
                    capture_attempt_failed = not capture_fallback_requested
                    capture_requested = capture_fallback_requested
                    record_capture("failed", "native-grab-failed")
                if captured_image is None and not capture_attempt_failed:
                    if not capture_fallback_requested:
                        capture_fallback_requested = bool(
                            verified_context.process_id
                            and native_capture_helper_available()
                        )
                        capture_attempt_failed = not capture_fallback_requested
                        capture_requested = capture_fallback_requested
                        record_capture("failed", "native-grab-failed")
                if captured_image is not None:
                    try:
                        after_hwnd = int(self._foreground_provider() or 0)
                        after_context = self.reader(after_hwnd) if after_hwnd else None
                    except (OSError, RuntimeError, TypeError, ValueError):
                        after_context = None
                        after_hwnd = 0
                    after_safe = (
                        after_context is not None
                        and after_hwnd == after_context.hwnd
                        and self._same_capture_identity(verified_context, after_context)
                        and self._capture_policy(after_context)[0]
                    )
                    if not after_safe:
                        captured_image.close()
                        captured_image = None
                        record_capture("discarded", "foreground-changed")
                        if after_context is not None:
                            self.updateForegroundContext(after_context)
                        return False
                    capture_requested = True
                    generation_context = after_context
                    self.updateForegroundContext(after_context)
            elif verified_context is not None:
                self.updateForegroundContext(verified_context)
                if manual_capture:
                    reject_manual_capture(
                        "前台窗口身份已经变化；没有截图，请重新选择目标窗口。",
                        "foreground-changed",
                    )
                return False
        if manual_capture and not capture_requested:
            if captured_image is not None:
                captured_image.close()
            if reject_manual_capture(
                "当前窗口截图没有成功；本次不会改用泛化文字冒充观察。",
                "native-grab-failed",
            ):
                return False
        # Capture preparation can take long enough for QML to enter a quiet
        # presentation state.  Recheck immediately before allocating a worker.
        if self._presentation_suppressed or (
            self._active and not self._presentation_sync_ready
        ):
            if captured_image is not None:
                captured_image.close()
                record_capture("cancelled", "presentation-suppressed")
            return False
        self._generation_serial += 1
        generation_token = self._generation_serial
        self._active_generation_token = generation_token
        self._active_generation_has_capture = capture_requested
        self._active_generation_capture_diagnostic_token = (
            capture_diagnostic_token if capture_requested else 0
        )
        self._busy = True
        self.changed.emit()
        metadata = self._model_context_metadata(
            generation_context, scene_label, self.momentum.current
        )
        raw_interest_hints = self.database.get_setting("companion_interests", [])
        generation_interest_hints = (
            [str(value) for value in raw_interest_hints]
            if isinstance(raw_interest_hints, list)
            else []
        )
        generation_interest_weight = int(self.preferences_model.interest_weight)
        generation_scene_weight = int(self.preferences_model.scene_weight)
        image_model = str(self.runtime.modality_status.get("imageModel", ""))
        model_id = TERRA_MODEL if capture_requested and image_model == "terra" else LUNA_MODEL
        generation_cancel_event = threading.Event()
        self._generation_cancel_event = generation_cancel_event
        self._active_generation_model_id = model_id
        task_kind = (
            ModelTaskKind.SCREEN_UNDERSTANDING
            if capture_requested
            else ModelTaskKind.PROACTIVE
        )
        generation_context_identity = self.activity.context_identity
        broker_task_id = self._submit_model_task(
            model_id,
            task_kind,
            {
                "requestId": uuid.uuid4().hex,
                "category": category.value,
                "hasCapture": capture_requested,
                "manualCapture": bool(manual_capture),
                "hasSource": content_item is not None,
            },
            context_bound=True,
            ttl_seconds=120.0,
        )

        def worker() -> None:
            nonlocal captured_image, capture_attempt_failed, capture_fallback_requested
            staged: StagedCapture | None = None
            lease = _BrokerTaskLease(
                self._model_broker,
                broker_task_id or None,
                model_id,
                abort=lambda: self.runtime.abort_model(model_id),
                local_cancel=generation_cancel_event,
            )
            try:
                if captured_image is not None:
                    raw_capture = captured_image
                    captured_image = None
                    try:
                        staged = self.capture_staging.stage_image(
                            int(generation_context.hwnd),
                            raw_capture,
                            cancelled=generation_cancel_event.is_set,
                        )
                    except CaptureCancelled:
                        record_capture("cancelled", "request-cancelled")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                    except ProtectedCaptureContent:
                        staged = None
                        # A black compositor frame may be an intentional
                        # protection boundary.  Never try another capture
                        # mechanism to work around it.
                        capture_fallback_requested = False
                        capture_attempt_failed = True
                        record_capture("failed", "protected-black")
                    except LowInformationCapture:
                        staged = None
                        capture_fallback_requested = bool(
                            generation_context.process_id
                            and native_capture_helper_available()
                        )
                        capture_attempt_failed = not capture_fallback_requested
                        record_capture("failed", "low-information")
                    except CaptureStorageError:
                        staged = None
                        capture_attempt_failed = True
                        record_capture("failed", "encode-storage-failed")
                    except (CaptureEncodeError, RuntimeError, ValueError):
                        staged = None
                        capture_fallback_requested = bool(
                            generation_context.process_id
                            and native_capture_helper_available()
                        )
                        capture_attempt_failed = not capture_fallback_requested
                        record_capture("failed", "encode-failed")
                    except OSError:
                        staged = None
                        capture_attempt_failed = True
                        record_capture("failed", "encode-storage-failed")
                    finally:
                        # Once the bounded PNG has been produced, the raw
                        # full-resolution HWND copy is no longer needed.  Do
                        # not retain tens of megabytes while this low-priority
                        # task waits behind chat or while the model generates.
                        raw_capture.close()
                if staged is None and capture_fallback_requested:
                    try:
                        helper_hwnd = int(self._foreground_provider() or 0)
                        helper_context = self.reader(helper_hwnd) if helper_hwnd else None
                    except (OSError, RuntimeError, TypeError, ValueError):
                        helper_context = None
                        helper_hwnd = 0
                    helper_safe = bool(
                        helper_context is not None
                        and helper_hwnd == helper_context.hwnd
                        and self._same_capture_identity(
                            generation_context, helper_context
                        )
                        and self._capture_policy(helper_context)[0]
                    )
                    if not helper_safe:
                        record_capture("discarded", "foreground-changed")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                    try:
                        staged = stage_window_capture_with_helper(
                            self.capture_staging,
                            helper_context.hwnd,
                            helper_context.process_id,
                            cancelled=generation_cancel_event.is_set,
                        )
                        capture_attempt_failed = False
                        record_capture("staged", "native-print-staged")
                    except CaptureCancelled:
                        record_capture("cancelled", "request-cancelled")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                    except (
                        NativeCaptureHelperError,
                        CaptureEncodeError,
                        OSError,
                        RuntimeError,
                        ValueError,
                    ):
                        staged = None
                        capture_attempt_failed = True
                        record_capture("failed", "native-print-failed")

                if staged is not None:
                    self._set_inflight_capture(staged)
                    record_capture("staged", "model-request-starting")
                    if generation_cancel_event.is_set():
                        self._take_inflight_capture(staged)
                        staged.release()
                        record_capture("cancelled", "request-cancelled")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                    try:
                        encoded_hwnd = int(self._foreground_provider() or 0)
                        encoded_context = (
                            self.reader(encoded_hwnd) if encoded_hwnd else None
                        )
                    except (OSError, RuntimeError, TypeError, ValueError):
                        encoded_context = None
                        encoded_hwnd = 0
                    encoded_safe = bool(
                        encoded_context is not None
                        and encoded_hwnd == encoded_context.hwnd
                        and self._same_capture_identity(
                            generation_context, encoded_context
                        )
                        and self._capture_policy(encoded_context)[0]
                    )
                    if not encoded_safe:
                        self._take_inflight_capture(staged)
                        staged.release()
                        staged = None
                        record_capture("discarded", "foreground-changed")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                if capture_attempt_failed:
                    # Once this turn attempted to inspect pixels, falling back
                    # to a text-only generation would falsely present generic
                    # prose as a screen observation. Keep the turn quiet and
                    # let the controller schedule a short, content-free retry.
                    self._abandon_model_task(
                        broker_task_id, "capture-attempt-failed"
                    )
                    self._generationReady.emit(
                        {
                            "result": {
                                "summary": "",
                                "detail": "",
                                "model": "",
                                "contextType": "application-signal",
                                "imageGrounded": False,
                                "skip": True,
                                "skipReason": "capture-attempt-failed",
                                "degraded": False,
                                "retryAfterSeconds": 30.0,
                            },
                            "category": category,
                            "sceneLabel": scene_label,
                            "contentItem": content_item,
                            "capture": None,
                            "force": force,
                            "duplicateRetry": int(duplicate_retry),
                            "contextIdentity": generation_context_identity,
                            "generationToken": generation_token,
                            "captureDiagnosticToken": capture_diagnostic_token,
                            "browserCapture": False,
                            "manualCapture": bool(manual_capture),
                        }
                    )
                    return
                if not lease.acquire():
                    if staged:
                        self._take_inflight_capture(staged)
                        staged.release()
                        record_capture("cancelled", "broker-cancelled")
                    self._generationReady.emit(
                        {
                            "cancelled": True,
                            "capture": None,
                            "force": force,
                            "generationToken": generation_token,
                        }
                    )
                    return
                if staged is not None:
                    # The bounded PNG may have waited behind a higher-priority
                    # owner after its encoding-time check. Re-read the live
                    # foreground only after this task owns the runtime, and
                    # immediately before handing the staged pixels to it.
                    try:
                        model_hwnd = int(self._foreground_provider() or 0)
                        model_context = (
                            self.reader(model_hwnd) if model_hwnd else None
                        )
                    except (OSError, RuntimeError, TypeError, ValueError):
                        model_context = None
                        model_hwnd = 0
                    model_context_safe = bool(
                        model_context is not None
                        and model_hwnd == model_context.hwnd
                        and self._same_capture_identity(
                            generation_context, model_context
                        )
                        and self._capture_policy(model_context)[0]
                    )
                    if not model_context_safe:
                        self._take_inflight_capture(staged)
                        staged.release()
                        staged = None
                        self._abandon_model_task(
                            broker_task_id,
                            "capture-context-changed-before-model",
                        )
                        record_capture(
                            "cancelled", "capture-context-changed-before-model"
                        )
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                if generation_cancel_event.is_set() or lease.cancelled:
                    if staged is not None:
                        self._take_inflight_capture(staged)
                        staged.release()
                        staged = None
                        record_capture("cancelled", "broker-cancelled-before-model")
                    self._generationReady.emit(
                        {
                            "cancelled": True,
                            "capture": None,
                            "force": force,
                            "generationToken": generation_token,
                        }
                    )
                    return
                if staged is not None:
                    # This is the first instant at which pixels are actually
                    # handed to the model runtime. Queued/staged images must
                    # never be reported as submitted before this line.
                    record_capture(
                        "submitted",
                        "model-request-starting",
                        model=model_id,
                    )
                result = self.runtime.generate(
                    category=category,
                    scene_label=scene_label,
                    context_metadata=metadata,
                    image_path=staged.path if staged else None,
                    content_item=content_item,
                    recent_summaries=list(self._recent_summary_snippets),
                    variation_nonce=int(duplicate_retry),
                    interest_hints=generation_interest_hints,
                    interest_weight=generation_interest_weight,
                    scene_weight=generation_scene_weight,
                    allow_latest=bool(
                        isinstance(content_item, ContentItem)
                        and content_item.id in self._fresh_content_ids
                        and not content_item.stale()
                    ),
                )
                if lease.cancelled:
                    if staged:
                        self._take_inflight_capture(staged)
                        staged.release()
                        record_capture("cancelled", "broker-cancelled")
                    self._generationReady.emit(
                        {
                            "cancelled": True,
                            "capture": None,
                            "force": force,
                            "generationToken": generation_token,
                        }
                    )
                    return
                capture_for_payload = staged
                if staged:
                    owned_capture = self._take_inflight_capture(staged)
                    if owned_capture is None or generation_cancel_event.is_set():
                        staged.release()
                        record_capture("cancelled", "request-cancelled")
                        self._generationReady.emit(
                            {
                                "cancelled": True,
                                "capture": None,
                                "force": force,
                                "generationToken": generation_token,
                            }
                        )
                        return
                    capture_succeeded = bool(
                        not str(result.get("error", "") or "").strip()
                        and str(result.get("contextType", ""))
                        == "active-window-image"
                        and result.get("imageGrounded") is not False
                    )
                    if capture_succeeded:
                        staged.retain_in_memory()
                        record_capture(
                            "used",
                            "image-model-completed",
                            model=str(result.get("model", "")),
                            evidence_confidence=str(
                                result.get("evidenceConfidence", "none")
                            ),
                        )
                    else:
                        staged.release()
                        capture_for_payload = None
                        image_skip_reason = str(
                            result.get("skipReason", "") or ""
                        ).strip()
                        record_capture(
                            "failed",
                            (
                                image_skip_reason
                                if bool(result.get("skip"))
                                and image_skip_reason
                                in _IMAGE_QUALITY_CAPTURE_REASONS
                                else "model-error"
                            ),
                            model=str(result.get("model", "")),
                            evidence_confidence=str(
                                result.get("evidenceConfidence", "none")
                            ),
                        )
                if generation_cancel_event.is_set():
                    if capture_for_payload is not None:
                        capture_for_payload.release()
                    record_capture("cancelled", "request-cancelled")
                    self._generationReady.emit(
                        {
                            "cancelled": True,
                            "capture": None,
                            "force": force,
                            "generationToken": generation_token,
                        }
                    )
                    return
                if not lease.commit(result={"completed": True}):
                    if capture_for_payload is not None:
                        capture_for_payload.release()
                    record_capture("cancelled", "broker-cancelled")
                    self._generationReady.emit(
                        {
                            "cancelled": True,
                            "capture": None,
                            "force": force,
                            "generationToken": generation_token,
                        }
                    )
                    return
                self._generationReady.emit(
                    {
                        "result": result,
                        "category": category,
                        "sceneLabel": scene_label,
                        "contentItem": content_item,
                        "capture": capture_for_payload,
                        "force": force,
                        "duplicateRetry": int(duplicate_retry),
                        "contextIdentity": generation_context_identity,
                        "generationToken": generation_token,
                        "captureDiagnosticToken": capture_diagnostic_token,
                        "browserCapture": bool(
                            capture_requested
                            and self._is_browser_context(generation_context)
                        ),
                        "manualCapture": bool(manual_capture),
                    }
                )
            except BaseException as exc:
                if staged:
                    self._take_inflight_capture(staged)
                    staged.release()
                    record_capture(
                        "cancelled" if lease.cancelled else "failed",
                        "broker-cancelled" if lease.cancelled else "model-error",
                    )
                self._generationReady.emit(
                    {
                        "cancelled": True,
                        "capture": None,
                        "force": force,
                        "generationToken": generation_token,
                    }
                    if lease.cancelled
                    else {
                        "error": str(exc),
                        "capture": None,
                        "generationToken": generation_token,
                    }
                )
            finally:
                if captured_image is not None:
                    captured_image.close()
                lease.close(result={"completed": not lease.cancelled})
                self._forget_model_task(broker_task_id)

        if self._start_worker(worker, name="lilies-companion-generate"):
            return True
        if captured_image is not None:
            captured_image.close()
            record_capture("cancelled", "worker-start-failed")
        self._busy = False
        if self._active_generation_token == generation_token:
            self._active_generation_token = 0
        if self._generation_cancel_event is generation_cancel_event:
            self._generation_cancel_event = None
        if self._active_generation_model_id == model_id:
            self._active_generation_model_id = ""
        self._active_generation_has_capture = False
        self._active_generation_capture_diagnostic_token = 0
        self._abandon_model_task(broker_task_id, "worker-start-failed")
        return False

    def _accept_generation(self, payload: object) -> None:
        value = dict(payload) if isinstance(payload, dict) else {}
        capture = value.get("capture")
        try:
            generation_token = int(value.get("generationToken") or 0)
        except (TypeError, ValueError):
            generation_token = -1
        try:
            capture_diagnostic_token = int(
                value.get("captureDiagnosticToken") or 0
            )
        except (TypeError, ValueError):
            capture_diagnostic_token = 0

        def record_presentation(
            outcome: str,
            reason: str,
            *,
            session_id: str = "",
            bubble_id: str = "",
        ) -> None:
            if capture_diagnostic_token:
                self._record_capture_presentation(
                    outcome,
                    reason,
                    attempt_token=capture_diagnostic_token,
                    session_id=session_id,
                    bubble_id=bubble_id,
                )

        def quiet_presentation_reason(reason: object) -> str:
            normalized = str(reason or "")
            if normalized in _CAPTURE_PRESENTATION_REASONS:
                return normalized
            if normalized in {
                "protected-content",
                "password-manager",
                "security-dialog",
                "remote-desktop",
                "meeting",
                "private-browsing",
                "payment-window",
                "application-blocked",
                "signals-only",
                "disabled",
                "paused",
            }:
                return "privacy-suppressed"
            return "foreground-changed"

        if self._closing:
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "dismissed-before-presentation")
            return
        if generation_token and generation_token != self._active_generation_token:
            # A presentation suppression invalidates the worker token.  Its
            # eventual signal is intentionally silent and must not clear a
            # newer generation's busy state.
            if isinstance(capture, StagedCapture):
                capture.release()
            return
        self._active_generation_has_capture = False
        self._active_generation_capture_diagnostic_token = 0
        browser_capture_revoked = bool(
            value.get("browserCapture") and not self._browser_single_capture
        )
        if isinstance(capture, StagedCapture) and (
            not self._smart_observation or browser_capture_revoked
        ):
            # Revocation may race the model response.  Re-check authorization
            # on the Qt acceptance turn before creating any visible bubble.
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            capture.release()
            record_presentation(
                "quiet",
                (
                    "browser-authorization-revoked"
                    if browser_capture_revoked
                    else "authorization-revoked"
                ),
            )
            self.changed.emit()
            return
        if self._presentation_suppressed or (
            self._active and not self._presentation_sync_ready
        ):
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "presentation-suppressed")
            self.changed.emit()
            return
        if value.get("cancelled"):
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            record_presentation("cancelled", "generation-cancelled")
            if bool(value.get("force")):
                self._set_request_feedback("当前场景发生变化，这句话已安静地取消", "quiet")
            self.changed.emit()
            return
        if value.get("error"):
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            self._generation_attempt_not_before = max(
                self._generation_attempt_not_before,
                time.monotonic() + 60.0,
            )
            record_presentation("quiet", "engine-rejected")
            self._set_request_feedback(
                "这次没有生成成功；稍后可再点一次“生成一条场景陪伴”", "warning"
            )
            self.status_sink(f"主动陪伴暂时没有生成：{value['error']}")
            self.changed.emit()
            return
        generation_identity = str(value.get("contextIdentity", ""))
        if (
            not bool(value.get("force"))
            and generation_identity
            and generation_identity != self.activity.context_identity
        ):
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "foreground-changed")
            self.changed.emit()
            return
        result = dict(value.get("result") or {})
        skip_reason = str(result.get("skipReason", "") or "").strip()
        quality_skip = bool(result.get("skip")) and (
            skip_reason in _QUIET_GENERATION_SKIP_REASONS
        )
        generation_already_invalidated = bool(
            generation_token and generation_token != self._active_generation_token
        )
        if quality_skip and not generation_already_invalidated:
            if generation_token == self._active_generation_token:
                self._active_generation_token = 0
                self._generation_cancel_event = None
                self._active_generation_model_id = ""
            self._busy = False
            if isinstance(capture, StagedCapture):
                capture.release()
            record_presentation("quiet", "quality-rejected")
            try:
                retry_seconds = max(
                    15.0,
                    min(
                        1800.0,
                        float(result.get("retryAfterSeconds", 30.0) or 30.0),
                    ),
                )
            except (TypeError, ValueError):
                retry_seconds = 30.0
            self._generation_attempt_not_before = max(
                self._generation_attempt_not_before,
                time.monotonic() + retry_seconds,
            )
            self._last_generation_model = str(result.get("model", ""))
            self._last_generation_error = ""
            if bool(value.get("force")):
                if skip_reason == "capture-attempt-failed":
                    feedback = "截图没有成功；没有弹出气泡，请稍后再试"
                elif skip_reason in {"text-result-invalid", "text-visual-claim"}:
                    feedback = "生成内容没有通过事实边界检查；这次没有弹出气泡"
                elif skip_reason in {
                    "image-circuit-open",
                    "image-generation-failed",
                    "image-model-unavailable",
                }:
                    feedback = "图像理解暂时不可用；这次没有弹出气泡"
                elif skip_reason == "philosophy-quality-invalid":
                    feedback = "这次哲思没有形成具体问题或对照；已经安静跳过"
                elif skip_reason == "subjective-model-unavailable":
                    feedback = "订阅模型暂不可用；这次保持安静，没有使用固定文案"
                elif skip_reason == "subjective-generation-failed":
                    feedback = "模型没有生成出可用内容；这次保持安静，没有使用固定文案"
                elif skip_reason == "source-metadata-unavailable":
                    feedback = "没有经过验证的来源与日期；这次没有生成新闻或科研气泡"
                elif skip_reason == "source-metadata-repeated":
                    feedback = "这条来源近期已经展示过；这次保持安静"
                else:
                    feedback = "画面证据不足；没有弹出气泡，请稍后再试"
                self._set_request_feedback(feedback, "quiet")
            self.changed.emit()
            return
        _context, reconciled, reconcile_reason = self._reconcile_foreground_for_bubble()
        generation_invalidated = bool(
            generation_token
            and generation_token != self._active_generation_token
        )
        if not generation_invalidated and generation_token == self._active_generation_token:
            self._active_generation_token = 0
            self._generation_cancel_event = None
            self._active_generation_model_id = ""
        self._busy = False
        if generation_invalidated:
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation(
                    "quiet", "capture-context-changed-before-presentation"
                )
            self.changed.emit()
            return
        if not reconciled:
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation(
                    "quiet", quiet_presentation_reason(reconcile_reason)
                )
            label = self._request_reason_label(reconcile_reason)
            self._set_request_feedback(f"没有弹出气泡：{label}", "quiet")
            self.status_sink(f"当前场景保持安静 · {label}")
            self.changed.emit()
            return
        allowed, reason = self._can_present_bubble()
        if not allowed:
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", quiet_presentation_reason(reason))
            label = self._request_reason_label(reason)
            self._set_request_feedback(f"没有弹出气泡：{label}", "quiet")
            self.status_sink(f"当前场景保持安静 · {label}")
            self.changed.emit()
            return
        category = value.get("category")
        content_item = value.get("contentItem")
        source = content_item.source_attribution() if isinstance(content_item, ContentItem) else None
        degraded = bool(result.get("degraded")) or str(
            result.get("model", "")
        ) == "local-safe-fallback"
        try:
            degraded_retry_seconds = max(
                15.0,
                min(120.0, float(result.get("retryAfterSeconds", 60.0) or 60.0)),
            )
        except (TypeError, ValueError):
            degraded_retry_seconds = 60.0
        if degraded:
            self._last_generation_model = str(
                result.get("model", "local-safe-fallback")
            )
            self._last_generation_error = str(result.get("error", ""))
            self._generation_attempt_not_before = max(
                self._generation_attempt_not_before,
                time.monotonic() + degraded_retry_seconds,
            )
            if not bool(value.get("force")):
                # Automatic model failures stay quiet instead of spending the
                # normal success gate.  The per-model/per-modality circuit gets
                # another chance after its bounded backoff.
                capture = value.get("capture")
                if isinstance(capture, StagedCapture):
                    capture.release()
                    record_presentation("quiet", "engine-rejected")
                self._set_request_feedback(
                    f"模型暂时不可用；这次保持安静，约 {int(degraded_retry_seconds)} 秒后再试",
                    "quiet",
                )
                self.changed.emit()
                return
        if bool(result.get("skip")):
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "quality-rejected")
            if not degraded:
                self.activity.mark_observation_sent("fallback-exhausted")
                self._generation_attempt_not_before = max(
                    self._generation_attempt_not_before,
                    time.monotonic()
                    + max(
                        60.0,
                        min(
                            300.0,
                            self.preferences_model.frequency.minimum_minutes
                            * 60.0,
                        ),
                    ),
                )
            self._set_request_feedback(
                "这次没有可显示的可靠内容；已经保持安静", "quiet"
            )
            self.changed.emit()
            return
        generated_summary = str(result.get("summary", ""))
        generated_detail = str(result.get("detail", ""))
        generated_prose = _combined_generated_prose(
            generated_summary, generated_detail
        )
        summary_repeated = any(
            summaries_are_near_duplicates(generated_summary, previous)
            for previous in self._recent_summary_snippets
        )
        prose_repeated = any(
            summaries_are_near_duplicates(generated_prose, previous)
            for previous in self._recent_prose_snippets
        )
        is_repeated = summary_repeated or prose_repeated
        if is_repeated:
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "duplicate-suppressed")
            retry_count = max(0, int(value.get("duplicateRetry") or 0))
            if bool(value.get("force")) and retry_count < 1:
                self._set_request_feedback(
                    "这句话太像最近说过的；正在换一个观察角度……", "busy"
                )
                self._requested_category = (
                    category if isinstance(category, ContentCategory) else None
                )
                retry_context = self.activity.current_context

                def retry_distinct_generation() -> None:
                    retry_arguments: dict[str, Any] = {
                        "force": True,
                        "duplicate_retry": retry_count + 1,
                    }
                    if bool(value.get("manualCapture")):
                        retry_arguments["manual_capture"] = True
                    started = self._start_generation(
                        retry_context, **retry_arguments
                    )
                    if not started:
                        self._requested_category = None
                        self._set_request_feedback(
                            "刚才的说法与近期内容太相似，已经安静跳过", "quiet"
                        )
                        self.changed.emit()

                QTimer.singleShot(0, retry_distinct_generation)
            else:
                self.activity.mark_observation_sent("duplicate-suppressed")
                self._generation_attempt_not_before = max(
                    self._generation_attempt_not_before,
                    time.monotonic()
                    + max(
                        60.0,
                        min(
                            300.0,
                            self.preferences_model.frequency.minimum_minutes * 60.0,
                        ),
                    ),
                )
                self._set_request_feedback(
                    "刚才的说法与近期内容太相似，已经安静跳过", "quiet"
                )
            self.changed.emit()
            return
        try:
            bubble = self.engine.emit(
                category=category,
                summary=generated_summary,
                detail=generated_detail,
                source=source,
                scene_label=str(value.get("sceneLabel", "")),
                content_id=content_item.id if isinstance(content_item, ContentItem) else "",
                generation={
                    "model": str(result.get("model", "")),
                    "contextType": str(
                        result.get("contextType", "application-signal")
                    ),
                    "imageGrounded": bool(
                        str(result.get("contextType", ""))
                        == "active-window-image"
                        and isinstance(value.get("capture"), StagedCapture)
                        and result.get("imageGrounded") is not False
                    ),
                    "evidenceConfidence": str(
                        result.get("evidenceConfidence", "none")
                    ),
                },
                force=bool(value.get("force")),
            )
        except ValueError as exc:
            bubble = None
            self.status_sink(str(exc))
        if bubble is None:
            capture = value.get("capture")
            if isinstance(capture, StagedCapture):
                capture.release()
                record_presentation("quiet", "engine-rejected")
            self.changed.emit()
            return
        if self._bubble_capture_diagnostic_token:
            self._record_capture_presentation(
                "quiet",
                "dismissed-before-presentation",
                attempt_token=self._bubble_capture_diagnostic_token,
            )
            self._bubble_capture_diagnostic_token = 0
        self._release_capture()
        self._capture = value.get("capture") if isinstance(value.get("capture"), StagedCapture) else None
        self._bubble_object = bubble
        self._last_generation_model = str(result.get("model", ""))
        self._last_generation_error = str(result.get("error", ""))
        self._bubble = {
            **bubble.to_mapping(),
            "visible": True,
            # Presentation lifetime begins only after QML confirms that it
            # completed a visible native-window turn.
            "expiresAt": "",
            "busy": False,
            "model": str(result.get("model", "")),
            "contextType": str(result.get("contextType", "application-signal")),
            "imageGrounded": bool(
                str(result.get("contextType", "")) == "active-window-image"
                and self._capture is not None
                and result.get("imageGrounded") is not False
            ),
            "evidenceConfidence": str(
                result.get("evidenceConfidence", "none")
            ),
            "error": str(result.get("error", "")),
            "hasCapture": self._capture is not None,
            "conversation": [],
            "deliveryState": "waiting-present-ack",
        }
        self._bubble_ttl_remaining_seconds = max(
            0.1,
            (bubble.expires_at - bubble.created_at).total_seconds(),
        )
        self._bubble_presented = False
        self._bubble_interacted = False
        self._presentation_ack_pending = True
        self._bubble_capture_diagnostic_token = (
            capture_diagnostic_token
            if self._capture is not None
            and str(self._bubble.get("contextType", "")) == "active-window-image"
            else 0
        )
        if self._bubble_capture_diagnostic_token:
            record_presentation(
                "pending",
                "awaiting-presentation",
                session_id=bubble.session_id,
                bubble_id=bubble.id,
            )
        self._delivery_record = {
            "schemaVersion": 2,
            "sessionId": bubble.session_id,
            "bubbleId": bubble.id,
            "state": "waiting-present-ack",
            "reason": "generated",
            "generatedAt": bubble.created_at.isoformat(),
            "presentedAt": "",
            "expiresAt": "",
            "unread": False,
            "unreadSince": "",
            "redeliveryCount": 0,
            "lastRedeliveryAt": "",
        }
        self._persist_delivery_record()
        self._presentation_ack_timer.start(_PRESENTATION_ACK_TIMEOUT_MS)
        if isinstance(content_item, ContentItem):
            self._recent_content_ids.append(content_item.id)
            self._recent_sources.append(content_item.source)
        if not degraded:
            self.activity.mark_observation_sent(str(self._bubble["contextType"]))
        if str(self._bubble["contextType"]) == "active-window-image":
            self._set_request_feedback(
                "模型已使用本次活动窗口画面；暂存文件已删除，气泡正在等待可见确认",
                "pending",
            )
        else:
            self._set_request_feedback(
                "内容已生成并等待气泡可见确认；本次仅使用应用类别，没有读取窗口标题或文字",
                "pending",
            )
        # Publish first. Optional memory indexing is deliberately isolated so
        # a full disk or transient SQLite lock cannot swallow the final QML
        # notification after the session was already created.
        self.bubbleChanged.emit()
        self.changed.emit()
        try:
            self._remember_observation_if_needed(bubble)
        except Exception as exc:
            self.status_sink(f"气泡已生成；本次观察记忆未保存：{type(exc).__name__}")

    def _remember_observation_if_needed(self, bubble: SpeechBubble) -> None:
        if self._screen_memory_mode == "replies":
            return
        label = bubble.scene_label or "未分类"
        now = time.time()
        hits = self._scene_hits[label]
        hits.append(now)
        while hits and now - hits[0] > 24 * 3600:
            hits.popleft()
        context = self.activity.current_context
        stable = time.monotonic() - context.changed_at if context and context.changed_at else 0.0
        significant = stable >= 20 * 60 or len(hits) >= 3
        if self._screen_memory_mode == "all" or significant:
            self.database.save_memory_fragment(
                source_type="screen-observation",
                source_id=bubble.id,
                content=f"场景：{label}。莉莉丝说：{bubble.summary}",
                partition_id="daily",
                summary=bubble.summary,
                importance=0.35 if self._screen_memory_mode == "all" else 0.55,
            )

    def _record_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        if kind == "bubble-created":
            bubble = dict(event["bubble"])
            self.database.save_proactive_session(
                session_id=str(event["sessionId"]),
                bubble=bubble,
                generation=(
                    dict(event.get("generation") or {})
                    if isinstance(event.get("generation"), dict)
                    else {}
                ),
            )
            summary = str(bubble.get("summary", "")).strip()
            if summary:
                self._recent_summary_snippets.append(summary)
                self._recent_prose_snippets.append(
                    _combined_generated_prose(summary, bubble.get("detail", ""))
                )
        elif kind in {"bubble-reply", "bubble-answer"}:
            message = dict(event.get("message") or {})
            self.database.add_proactive_message(
                str(event.get("sessionId", "")),
                str(message.get("role", "")),
                str(message.get("text", "")),
                memory_eligible=bool(event.get("memoryEligible", True)),
            )
        elif kind == "bubble-moved-to-box":
            self.database.save_proactive_session(
                session_id=str(event.get("sessionId", "")),
                bubble=dict(event.get("bubble") or {}),
                moved_to_box=True,
            )
        if kind in {"bubble-created", "companion-snoozed"}:
            self.database.set_setting("companion_emission_state", self.engine.gate.snapshot())

    def _expire_bubble(self) -> None:
        if not self._bubble.get("visible") or not self._bubble.get("expiresAt"):
            return
        try:
            expiry = datetime.fromisoformat(str(self._bubble["expiresAt"]).replace("Z", "+00:00"))
        except ValueError:
            return
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if datetime.now(UTC) < expiry:
            # Windows' coarse timer may fire a few milliseconds before the
            # requested deadline.  Re-arm against the authoritative timestamp
            # instead of silently leaving a bubble immortal.
            self._schedule_bubble_expiry()
            return
        if self._bubble_interacted:
            self._set_delivery_state(
                "expired",
                "expired-after-interaction",
                unread=False,
                expires_at="",
            )
            self._clear_bubble(reason="expired-after-interaction")
        else:
            self._set_delivery_state(
                "unread",
                "expired-without-interaction",
                unread=True,
                expires_at="",
            )
            self._clear_bubble(
                reason="expired-without-interaction",
                preserve_unread=True,
            )

    def _schedule_bubble_expiry(self) -> None:
        self._bubble_expiry_timer.stop()
        raw_expiry = self._bubble.get("expiresAt")
        if not self._bubble.get("visible") or not raw_expiry:
            return
        try:
            expiry = datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00"))
        except ValueError:
            return
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        remaining_ms = int((expiry - datetime.now(UTC)).total_seconds() * 1000)
        if remaining_ms <= 0:
            self._expire_bubble()
            return
        self._bubble_expiry_timer.start(min(remaining_ms + 1, 2_147_000_000))

    def _can_present_bubble(self) -> tuple[bool, str]:
        """Check the current privacy and quiet-state boundary for bubbles."""

        if self._presentation_suppressed:
            return False, "presentation-suppressed"
        if self._active and not self._presentation_sync_ready:
            return False, "presentation-unavailable"
        if not self._activity_enabled:
            return False, "disabled"
        if self.activity.paused:
            return False, "paused"
        context = self.activity.current_context
        if context is None:
            return True, "no-context"
        decision = self.activity.guard.evaluate(context)
        return bool(decision.can_bubble), str(decision.reason)

    @staticmethod
    def _request_reason_label(reason: str) -> str:
        """Translate a deterministic privacy/quiet reason for the visible UI."""

        return {
            "disabled": "活动感知已关闭",
            "paused": "活动感知已暂停",
            "presentation-suppressed": "当前界面保持安静",
            "presentation-unavailable": "陪伴气泡尚未准备好",
            "no-foreground-window": "暂时无法确认当前窗口",
            "foreground-read-failed": "暂时无法安全确认当前窗口",
            "protected-content": "当前是受保护内容",
            "password-manager": "当前是密码应用",
            "security-dialog": "当前是系统安全界面",
            "remote-desktop": "当前是远程桌面",
            "meeting": "当前处于会议场景",
            "private-browsing": "当前是隐私浏览窗口",
            "payment-window": "当前是支付窗口",
            "application-blocked": "此应用已设为静默",
            "signals-only": "此应用只允许场景信号",
            "assistant-ui": "莉莉丝自己的界面不会被观察",
        }.get(str(reason), str(reason or "当前场景保持安静"))

    def _set_request_feedback(self, message: str, kind: str) -> None:
        self._request_feedback = str(message).strip()[:240]
        self._request_feedback_kind = str(kind or "info")[:24]

    def _reconcile_foreground_for_bubble(
        self,
    ) -> tuple[ForegroundContext | None, bool, str]:
        """Re-read the live external foreground before showing a bubble.

        A user can click the request control from one of Lilies' own windows;
        that assistant window is intentionally ignored and the last external
        context remains the reference.  Every other live HWND is re-read even
        when its integer handle matches the cache, because a browser can enter
        a payment/password surface without changing HWND.  A failed live read
        is fail-closed instead of trusting a stale safe context.
        """

        cached = self.activity.current_context
        try:
            current_hwnd = int(self._foreground_provider() or 0)
        except (OSError, RuntimeError, TypeError, ValueError):
            return cached, False, "foreground-read-failed"
        # Standalone/offscreen operation has no native foreground provider.
        # Keep its explicit synthetic context testable; the active Windows
        # service normally always supplies a non-zero HWND outside lock/UAC.
        if not current_hwnd:
            if self._active:
                return None, False, "no-foreground-window"
            return cached, True, "no-context" if cached is None else "cached-context"
        try:
            current = self.reader(current_hwnd)
        except (OSError, RuntimeError, TypeError, ValueError):
            return cached, False, "foreground-read-failed"
        decision = self.activity.guard.evaluate(current)
        if decision.reason == "assistant-ui":
            # Clicking settings/chat brings Lilies itself to the foreground;
            # do not erase or falsely block the last external work context.
            return cached, True, "assistant-ui"
        self.updateForegroundContext(current)
        return current, bool(decision.can_bubble), str(decision.reason)

    def _start_manual_generation(self) -> bool:
        context, reconciled, reconcile_reason = self._reconcile_foreground_for_bubble()
        if not reconciled:
            label = self._request_reason_label(reconcile_reason)
            self._set_request_feedback(f"没有打扰你：{label}", "quiet")
            self.status_sink(f"当前场景保持安静 · {label}")
            self.changed.emit()
            return False
        allowed, reason = self._can_present_bubble()
        if not allowed:
            label = self._request_reason_label(reason)
            self._set_request_feedback(f"没有打扰你：{label}", "quiet")
            self.status_sink(f"当前场景保持安静 · {label}")
            self.changed.emit()
            return False
        started = self._start_generation(context, force=True)
        if not started and not self._busy:
            self._set_request_feedback("这次没有找到可用的陪伴内容", "warning")
            self.changed.emit()
        return started

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def _mark_bubble_interacted(self, reason: str) -> None:
        if not self._bubble:
            return
        self._bubble_interacted = True
        self._set_delivery_state(
            "interacted", str(reason), unread=False, expires_at=str(
                self._bubble.get("expiresAt", "")
            )
        )

    def _clear_bubble(
        self,
        *,
        reason: str = "dismissed",
        preserve_unread: bool = False,
        mark_read: bool = False,
    ) -> None:
        """Immediately clear a bubble for an explicit or TTL-driven action."""

        had_bubble = bool(self._bubble)
        capture_diagnostic_token = self._bubble_capture_diagnostic_token
        if capture_diagnostic_token:
            presentation_outcome = "unread" if preserve_unread else "quiet"
            presentation_reason = (
                str(reason)
                if str(reason) in _CAPTURE_PRESENTATION_REASONS
                else "dismissed-before-presentation"
            )
            self._record_capture_presentation(
                presentation_outcome,
                presentation_reason,
                attempt_token=capture_diagnostic_token,
            )
            self._bubble_capture_diagnostic_token = 0
        self._legacy_dismiss_serial += 1
        self._bubble_expiry_timer.stop()
        self._presentation_ack_timer.stop()
        self._release_capture()
        self._bubble = {}
        self._bubble_object = None
        self._bubble_presented = False
        self._bubble_interacted = False
        self._presentation_ack_pending = False
        self._bubble_ttl_remaining_seconds = _BUBBLE_PRESENTATION_SECONDS
        if had_bubble:
            if preserve_unread:
                self._set_delivery_state(
                    "unread", reason, unread=True, expires_at=""
                )
            elif mark_read:
                self._set_delivery_state(
                    "dismissed", reason, unread=False, expires_at=""
                )
        self.bubbleChanged.emit()
        self.changed.emit()

    @Slot()
    def dismiss(self) -> None:
        """Legacy ambient dismissal used by Backend privacy transitions.

        Backend currently requests cleanup just before it publishes the new
        ``dockSuppressed`` value.  Defer one event-loop turn so QML can
        synchronise that value.  If a suppression edge occurred, preserve the
        bubble; otherwise retain the historical dismissal behaviour.
        """

        if not self._presentation_sync_ready:
            self._clear_bubble(
                reason="ambient-dismissed",
                preserve_unread=not self._bubble_interacted,
            )
            return
        if self._presentation_suppressed:
            return
        self._legacy_dismiss_serial += 1
        request_serial = self._legacy_dismiss_serial
        presentation_epoch = self._presentation_epoch
        bubble_id = str(self._bubble.get("id", ""))

        def finish() -> None:
            if self._closing or request_serial != self._legacy_dismiss_serial:
                return
            if (
                self._presentation_suppressed
                or presentation_epoch != self._presentation_epoch
            ):
                return
            if str(self._bubble.get("id", "")) != bubble_id:
                return
            self._clear_bubble(
                reason="ambient-dismissed",
                preserve_unread=not self._bubble_interacted,
            )

        QTimer.singleShot(0, finish)

    @Slot()
    def dismissExplicit(self) -> None:
        self._mark_bubble_interacted("explicit-dismiss")
        self._cancel_active_generation(
            "bubble-explicit-dismiss", capture_reason="explicit-dismiss"
        )
        self._busy = False
        self._clear_bubble(reason="explicit-dismiss", mark_read=True)

    def _load_unread_bubble(self) -> bool:
        session_id = str(self._delivery_record.get("sessionId", ""))
        bubble_id = str(self._delivery_record.get("bubbleId", ""))
        if not session_id or not bubble_id:
            return False
        try:
            saved = self.database.proactive_session(session_id)
        except Exception:
            return False
        if not isinstance(saved, dict) or str(saved.get("bubble_id", "")) != bubble_id:
            return False
        try:
            category = ContentCategory(str(saved.get("category", "")))
            created_at = datetime.fromisoformat(
                str(saved.get("created_at", "")).replace("Z", "+00:00")
            )
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return False
        source_value = saved.get("source")
        source: BubbleSource | None = None
        if isinstance(source_value, dict) and str(source_value.get("name", "")).strip():
            published_at = None
            raw_published = str(source_value.get("publishedAt", ""))
            if raw_published:
                try:
                    published_at = datetime.fromisoformat(
                        raw_published.replace("Z", "+00:00")
                    )
                    if published_at.tzinfo is None:
                        published_at = published_at.replace(tzinfo=UTC)
                except ValueError:
                    published_at = None
            source = BubbleSource(
                str(source_value.get("name", ""))[:160],
                str(source_value.get("url", ""))[:2048],
                published_at,
            )
        bubble = SpeechBubble(
            id=bubble_id,
            category=category,
            summary=str(saved.get("summary", ""))[:1000],
            detail=str(saved.get("detail", ""))[:12000],
            source=source,
            actions=DEFAULT_BUBBLE_ACTIONS,
            scene_label=str(saved.get("scene_label", ""))[:120],
            created_at=created_at,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=_BUBBLE_PRESENTATION_SECONDS),
            session_id=session_id,
        )
        if not bubble.summary:
            return False
        self.engine.bubbles[bubble.id] = bubble
        self.engine.sessions[bubble.session_id] = BubbleSession(
            bubble.session_id, bubble.id, bubble.category
        )
        self._bubble_object = bubble
        self._bubble = {
            **bubble.to_mapping(),
            "visible": True,
            "expiresAt": "",
            "busy": False,
            "model": "persisted-unread",
            "contextType": "application-signal",
            "error": "",
            "hasCapture": False,
            "conversation": [],
            "deliveryState": "waiting-present-ack",
        }
        return True

    def _reopen_unread(self, *, automatic: bool) -> bool:
        """Reopen one missed bubble after a fresh privacy check.

        Only ambient redelivery consumes the finite budget.  A deliberate tray
        or pet click is user intent and may reopen the retained history without
        spending another automatic attempt.
        """

        if self._closing:
            return False
        if self._prune_unread_delivery():
            self.changed.emit()
            return False
        if not bool(self._delivery_record.get("unread")):
            return False
        if self._presentation_suppressed or (
            self._active and not self._presentation_sync_ready
        ):
            self._set_request_feedback(
                "当前界面保持安静；这条未读仍会保留", "quiet"
            )
            self.changed.emit()
            return False
        _context, safe, reason = self._reconcile_foreground_for_bubble()
        if not safe:
            self._set_request_feedback(
                f"暂未重新显示：{self._request_reason_label(reason)}；未读仍会保留",
                "quiet",
            )
            self.changed.emit()
            return False
        if not self._bubble and not self._load_unread_bubble():
            self._set_request_feedback(
                "未读记录暂时无法重新显示；投递状态仍会保留", "warning"
            )
            self.changed.emit()
            return False
        self._bubble_ttl_remaining_seconds = _BUBBLE_PRESENTATION_SECONDS
        self._bubble_presented = False
        self._bubble_interacted = False
        self._presentation_ack_pending = True
        self._bubble["visible"] = True
        self._bubble["expiresAt"] = ""
        self._bubble["deliveryState"] = "waiting-present-ack"
        if automatic:
            try:
                redelivery_count = max(
                    0,
                    int(self._delivery_record.get("redeliveryCount", 0) or 0),
                )
            except (TypeError, ValueError):
                redelivery_count = 0
            self._delivery_record["redeliveryCount"] = min(
                _UNREAD_REDELIVERY_LIMIT, redelivery_count + 1
            )
            self._delivery_record["lastRedeliveryAt"] = datetime.now(UTC).isoformat()
        self._set_delivery_state(
            "waiting-present-ack",
            "auto-redelivered" if automatic else "reopened",
            unread=True,
            expires_at="",
        )
        self._set_request_feedback("正在重新显示未读陪伴……", "shown")
        self._presentation_ack_timer.start(_PRESENTATION_ACK_TIMEOUT_MS)
        self.bubbleChanged.emit()
        self.changed.emit()
        return True

    @Slot(result=bool)
    def reopenUnread(self) -> bool:
        """Explicitly reopen one retained bubble without consuming its budget."""

        return self._reopen_unread(automatic=False)

    @Slot(result=bool)
    def markUnreadRead(self) -> bool:
        """Clear only the unread delivery flag; keep session/history intact."""

        if self._closing or not bool(self._delivery_record.get("unread")):
            return False
        if self._bubble:
            self._clear_bubble(reason="explicit-mark-read", mark_read=True)
        else:
            self._set_delivery_state(
                "dismissed",
                "explicit-mark-read",
                unread=False,
                expires_at="",
            )
            self.changed.emit()
        return True

    @Slot(str, str)
    def acknowledgeInteraction(self, bubble_id: str, reason: str = "detail") -> None:
        if str(self._bubble.get("id", "")) != str(bubble_id):
            return
        allowed = {
            "detail",
            "reply",
            "another",
            "open-source",
            "save-moment",
            "move-to-box",
        }
        normalized = str(reason).casefold()
        self._mark_bubble_interacted(
            normalized if normalized in allowed else "detail"
        )
        self.changed.emit()

    @Slot(str)
    def another(self, bubble_id: str = "") -> None:
        if bubble_id and self._bubble.get("id") != bubble_id:
            return
        self._mark_bubble_interacted("another")
        bubble = self._bubble_object
        requested = bubble.category if bubble is not None else None
        self._requested_category = requested
        if not self._start_manual_generation():
            self._requested_category = None

    @Slot(str, result=bool)
    def requestCategory(self, category: str) -> bool:
        try:
            requested = ContentCategory(category)
        except ValueError:
            self.status_sink("未知的陪伴内容类别")
            return False
        # Choosing a category is an interaction with the current bubble even
        # when the requested replacement later fails validation or generation.
        self._mark_bubble_interacted("category")
        if not self.preferences_model.category_enabled(requested):
            self.status_sink(f"“{requested.value}”已在陪伴偏好中关闭")
            return False
        if requested in {ContentCategory.NEWS, ContentCategory.RESEARCH}:
            current_scene = self._scene_label(self.activity.current_context)
            weak_topic = str(self.momentum.current or "")
            ranking_query = " ".join(
                value for value in (current_scene, weak_topic) if value
            )
            if self._choose_content(ranking_query, requested) is None:
                self.status_sink(
                    f"“{requested.value}”暂时没有带来源和日期的可用内容；"
                    "可在设置中授权或刷新内容源"
                )
                return False
        self._requested_category = requested
        started = self._start_manual_generation()
        if not started:
            self._requested_category = None
        return started

    @Slot(result=bool)
    def requestNow(self) -> bool:
        """Generate one user-requested bubble without waiting for the idle gate."""

        if self._busy:
            self._set_request_feedback("上一句话还在整理，请稍等一下", "busy")
            self.status_sink("莉莉丝正在整理上一句话")
            self.changed.emit()
            return False
        started = self._start_manual_generation()
        if started:
            self._set_request_feedback("正在结合当前应用场景，整理一小句话……", "busy")
            self.status_sink("莉莉丝正在看看手边发生的事")
        self.changed.emit()
        return bool(started)

    @Slot(result=bool)
    def requestScreenNow(self) -> bool:
        """Request one explicit, guarded screenshot observation.

        The settings UI closes itself before invoking this slot so the user's
        target application can regain foreground.  Unlike ``requestNow``, a
        failed capture never degrades into generic scene prose.
        """

        if self._closing or self._busy:
            self._set_request_feedback("上一句话还在整理，请稍等一下", "busy")
            self.changed.emit()
            return False
        if not self._smart_observation:
            self._set_request_feedback(
                "请先阅读说明并授权智能屏幕观察；当前没有截图。", "warning"
            )
            self.changed.emit()
            return False
        modality = dict(self.runtime.modality_status)
        if not modality.get("checked") or not modality.get("imageModel"):
            if not self._probe_busy:
                self._probe_modalities()
            self._set_request_feedback(
                "正在确认订阅模型的图像能力；确认后请再试一次。", "warning"
            )
            self.changed.emit()
            return False
        context, reconciled, reconcile_reason = self._reconcile_foreground_for_bubble()
        if not reconciled or context is None:
            label = self._request_reason_label(reconcile_reason)
            self._set_request_feedback(f"没有截图：{label}", "warning")
            self.changed.emit()
            return False
        allowed, reason = self._can_present_bubble()
        if not allowed:
            self._set_request_feedback(
                f"没有截图：{self._request_reason_label(reason)}", "warning"
            )
            self.changed.emit()
            return False
        candidates = [
            category
            for category in (
                ContentCategory.PHILOSOPHY,
                ContentCategory.SCIENCE,
                ContentCategory.ROAST,
                ContentCategory.JOKE,
                ContentCategory.LORE,
            )
            if self.preferences_model.category_enabled(category)
        ]
        if not candidates:
            self._set_request_feedback(
                "请至少开启哲思、科普、吐槽、笑话或盒中世界中的一个类别。",
                "warning",
            )
            self.changed.emit()
            return False
        self._requested_category = max(
            candidates,
            key=lambda category: int(
                self.preferences_model.category_weights.get(category, 0)
            ),
        )
        started = self._start_generation(
            context, force=True, manual_capture=True
        )
        if not started:
            self._requested_category = None
        else:
            self._set_request_feedback(
                "正在理解一次当前活动窗口截图；窗口若变化会立即取消。", "busy"
            )
            self.status_sink("莉莉丝正在观察当前窗口的一次画面")
        self.changed.emit()
        return bool(started)

    @Slot(str, str)
    def reply(self, bubble_id: str, text: str) -> None:
        clean = str(text).strip()
        bubble = self._bubble_object
        if (
            self._closing
            or self._busy
            or not clean
            or bubble is None
            or bubble.id != bubble_id
        ):
            return
        self._mark_bubble_interacted("reply")
        session = self.engine.reply(bubble.id, clean)
        self._busy = True
        self._bubble["busy"] = True
        self.bubbleChanged.emit()
        self.changed.emit()
        dialogue = [message.to_mapping() for message in session.messages]
        broker_task_id = self._submit_model_task(
            LUNA_MODEL,
            ModelTaskKind.EXPLICIT_CHAT_REPLY,
            {"requestId": uuid.uuid4().hex, "bubbleId": bubble.id},
            context_bound=False,
            ttl_seconds=120.0,
        )

        def worker() -> None:
            lease = _BrokerTaskLease(
                self._model_broker,
                broker_task_id or None,
                LUNA_MODEL,
                abort=lambda: self.runtime.abort_model(LUNA_MODEL),
            )
            try:
                if not lease.acquire():
                    self._replyReady.emit(
                        {"bubbleId": bubble.id, "cancelled": True}
                    )
                    return
                answer = self.runtime.reply(bubble, dialogue, clean)
                if lease.commit(result={"completed": True}):
                    self._replyReady.emit(
                        {"bubbleId": bubble.id, "answer": answer}
                    )
                else:
                    self._replyReady.emit(
                        {"bubbleId": bubble.id, "cancelled": True}
                    )
            except BaseException as exc:
                self._replyReady.emit(
                    {"bubbleId": bubble.id, "cancelled": True}
                    if lease.cancelled
                    else {"bubbleId": bubble.id, "error": str(exc)}
                )
            finally:
                lease.close(result={"completed": not lease.cancelled})
                self._forget_model_task(broker_task_id)

        if not self._start_worker(worker, name="lilies-companion-reply"):
            self._busy = False
            self._bubble["busy"] = False
            self._abandon_model_task(broker_task_id, "worker-start-failed")

    def _accept_reply(self, payload: object) -> None:
        if self._closing:
            return
        value = dict(payload) if isinstance(payload, dict) else {}
        bubble = self._bubble_object
        if bubble is None or bubble.id != value.get("bubbleId"):
            self._busy = False
            self.changed.emit()
            return
        if value.get("cancelled"):
            self._busy = False
            self._bubble["busy"] = False
            self.bubbleChanged.emit()
            self.changed.emit()
            return
        if value.get("error"):
            self._busy = False
            self._bubble["busy"] = False
            self._bubble["error"] = "这次回复没有生成成功，可以稍后再试。"
            self.status_sink(f"气泡回复暂时失败：{value['error']}")
            self.bubbleChanged.emit()
            self.changed.emit()
            return
        answer = str(value.get("answer", "")).strip()
        if not answer:
            self._busy = False
            self._bubble["busy"] = False
            self._bubble["error"] = "这次回复没有生成成功，可以稍后再试。"
            self.status_sink("气泡回复暂时失败：模型返回了空内容")
            self.bubbleChanged.emit()
            self.changed.emit()
            return
        try:
            session = self.engine.answer(bubble.id, answer)
        except ValueError as exc:
            self._busy = False
            self._bubble["busy"] = False
            self._bubble["error"] = "这次回复没有生成成功，可以稍后再试。"
            self.status_sink(f"气泡回复暂时失败：{exc}")
            self.bubbleChanged.emit()
            self.changed.emit()
            return
        self._bubble.update(
            {
                "summary": answer[:1000],
                "detail": answer,
                "category": "继续对话",
                "sceneLabel": "短会话",
                "sourceRole": "context" if bubble.source is not None else "",
                "busy": False,
                "error": "",
                "conversation": [message.to_mapping() for message in session.messages],
            }
        )
        self._busy = False
        self.bubbleChanged.emit()
        self.changed.emit()

    @Slot(int)
    def snooze(self, minutes: int = 60) -> str:
        until = self.engine.snooze(int(minutes))
        self.status_sink(f"莉莉丝会安静到 {until.astimezone().strftime('%H:%M')}")
        self._mark_bubble_interacted("snooze")
        self._cancel_active_generation("companion-snoozed", capture_reason="snoozed")
        self._busy = False
        self._clear_bubble(reason="snooze", mark_read=True)
        self.changed.emit()
        return until.isoformat()

    @Slot()
    def muteCurrentApp(self) -> None:
        context = self.activity.current_context
        if context is None or not context.process_name:
            return
        self._mark_bubble_interacted("mute-app")
        self._clear_bubble(reason="mute-app", mark_read=True)
        self.setPolicy(context.process_name, ObservationPolicy.BLOCKED.value)

    @Slot(str, str)
    def setPolicy(self, application: str, policy: str) -> dict[str, Any]:
        normalized_application = self.activity.guard.application_key(application)
        current_context = self.activity.current_context
        current_application = ""
        previous_decision = None
        if current_context is not None:
            try:
                current_application = self.activity.guard.application_key(
                    current_context.process_name
                )
            except ValueError:
                current_application = ""
            if current_application == normalized_application:
                previous_decision = self.activity.guard.evaluate(current_context)
        requested_policy = str(policy).strip().casefold()
        if requested_policy in {"default", "inherit"}:
            self.activity.remove_policy(normalized_application)
            effective_policy = "default"
        else:
            self.activity.set_policy(normalized_application, requested_policy)
            effective_policy = self.activity.guard.policies()[normalized_application]
        self.database.set_setting(
            "activity_application_policies", self.activity.guard.policies()
        )
        if (
            current_context is not None
            and current_application == normalized_application
            and previous_decision is not None
            and (previous_decision.can_capture or previous_decision.can_bubble)
            and effective_policy in {
                ObservationPolicy.BLOCKED.value,
                ObservationPolicy.SIGNAL_ONLY.value,
            }
        ):
            # A privacy downgrade applies to the current window immediately,
            # not on the next 1.5-second heartbeat. Invalidate raw/staged/model
            # work first, then remove any retained capture and visible bubble.
            self._cancel_active_generation(
                "application-policy-tightened",
                capture_reason="privacy-suppressed",
            )
            self._release_capture()
            if self._bubble:
                self._clear_bubble(
                    reason="application-policy-tightened",
                    preserve_unread=not self._bubble_interacted,
                )
        self.changed.emit()
        return {
            "application": normalized_application,
            "policy": effective_policy,
        }

    @Slot(bool)
    def setActivityEnabled(self, enabled: bool) -> None:
        self._activity_enabled = bool(enabled)
        self.database.set_setting("activity_context_enabled", self._activity_enabled)
        if self._active and self._activity_enabled:
            self.activity.start()
            self._timer.start()
            try:
                current = self._foreground_provider()
                if current:
                    self.updateForegroundContext(self.reader(current))
            except (OSError, RuntimeError, TypeError, ValueError):
                # The already-running heartbeat reconciles the next readable
                # foreground HWND. Keep the control truthfully enabled.
                pass
        else:
            self._cancel_active_generation(
                "activity-disabled", capture_reason="activity-disabled"
            )
            self.activity.stop()
            self._timer.stop()
        self.changed.emit()

    def updateForegroundContext(self, context: ForegroundContext) -> bool:
        """Accept one event/reconciliation context and apply privacy at once."""

        changed = self.activity.update_foreground(context)
        decision = self.activity.guard.evaluate(context)
        if changed:
            self._cancel_active_generation(
                (
                    "foreground-context-changed"
                    if decision.can_bubble
                    else f"companion-quiet:{decision.reason}"
                ),
                capture_reason=(
                    "foreground-changed"
                    if decision.can_bubble
                    else "privacy-suppressed"
                ),
            )
        if not decision.can_bubble:
            # A late async result must not survive entry into a password,
            # meeting, remote-desktop or full-screen-game context.
            if self._busy:
                self._cancel_active_generation(
                    f"companion-quiet:{decision.reason}",
                    capture_reason="privacy-suppressed",
                )
            if self._bubble:
                self.dismiss()
        self.changed.emit()
        return changed

    @Slot(bool)
    def setPaused(self, paused: bool) -> None:
        self.activity.set_paused(paused)
        if paused:
            self._cancel_active_generation(
                "activity-paused", capture_reason="activity-paused"
            )
        self.changed.emit()

    @Slot(bool)
    def authorizeSmartObservation(self, enabled: bool) -> None:
        if self._closing:
            return
        requested = bool(enabled)
        try:
            # Persist before publishing the new value to QML.  A failed write
            # therefore leaves both the controller and durable consent at the
            # previously committed value, and must not escape through the slot.
            self.database.set_setting("smart_observation_authorized", requested)
        except Exception as exc:
            action = "开启" if requested else "撤销"
            message = f"屏幕观察授权{action}保存失败，设置未更改，请稍后重试"
            self._set_request_feedback(message, "warning")
            self.status_sink(f"{message}：{type(exc).__name__}")
            self.changed.emit()
            return
        self._smart_observation = requested
        if self._smart_observation:
            self._modality_retry_attempt = 0
            self._modality_retry_due_at = 0.0
            self._modality_retry_timer.stop()
            self._probe_modalities()
        else:
            self._modality_retry_timer.stop()
            self._modality_retry_due_at = 0.0
            self._cancel_active_generation(
                "smart-observation-revoked",
                capture_reason="authorization-revoked",
            )
            self._release_capture()
            if self._bubble.get("hasCapture"):
                self._bubble["hasCapture"] = False
                self.bubbleChanged.emit()
        self.changed.emit()

    @Slot(bool)
    def authorizeBrowserSingleCapture(self, enabled: bool) -> None:
        """Keep the retired browser-pixel switch fail-closed in v0.3.36."""

        if self._closing:
            return
        was_enabled = bool(self._browser_single_capture)
        self._browser_single_capture = False
        self.database.set_setting("browser_single_capture_authorized", False)
        if bool(enabled) or was_enabled:
            # A stale UI or component call cannot resurrect the retired path.
            # Fence any possibly pre-upgrade browser request and clear a
            # retained browser image before reporting the disabled state.
            self._cancel_active_generation(
                "browser-capture-revoked",
                capture_reason="browser-authorization-revoked",
            )
            if self._is_browser_context(self.activity.current_context):
                self._release_capture()
                if self._bubble:
                    self._clear_bubble(
                        reason="browser-capture-paused",
                        preserve_unread=not self._bubble_interacted,
                    )
            self.status_sink("浏览器像素观察在 v0.3.36 暂不开放")
        self.changed.emit()

    def _probe_modalities(self) -> None:
        if self._closing or self._probe_busy or not self._smart_observation:
            return
        if self._busy or self._active_generation_token:
            # The modality probe shares the subscription bridge. Never start
            # it while a generation owns (or is waiting for) a model lease.
            # Retain the retry request without incrementing the error backoff.
            delay = 2.0
            self._modality_retry_due_at = time.monotonic() + delay
            self._modality_retry_timer.start(int(delay * 1000))
            self.changed.emit()
            return
        self._modality_retry_timer.stop()
        self._modality_retry_due_at = 0.0
        self._probe_busy = True
        self.changed.emit()

        def worker() -> None:
            try:
                value = self.runtime.probe_modalities()
            except Exception as exc:
                value = {"checked": True, "error": str(exc)[:800]}
            if not self._closing:
                self._modalitiesReady.emit(value)

        if not self._start_worker(worker, name="lilies-companion-modalities"):
            self._probe_busy = False

    def _accept_modalities(self, value: object) -> None:
        if self._closing:
            return
        self._probe_busy = False
        if isinstance(value, dict):
            status = dict(value)
            status.setdefault("checked", True)
            status.setdefault("imageModel", "")
            status.setdefault("error", "")
            self.runtime.modality_status = status
        modality = dict(self.runtime.modality_status)
        image_model = str(modality.get("imageModel", "") or "")
        probe_error = bool(str(modality.get("error", "") or "").strip())
        if image_model:
            self._modality_retry_attempt = 0
            self._modality_retry_due_at = 0.0
            self._modality_retry_timer.stop()
        elif self._smart_observation and probe_error:
            delay = _MODALITY_RETRY_DELAYS_SECONDS[
                min(
                    self._modality_retry_attempt,
                    len(_MODALITY_RETRY_DELAYS_SECONDS) - 1,
                )
            ]
            self._modality_retry_attempt += 1
            self._modality_retry_due_at = time.monotonic() + float(delay)
            self._modality_retry_timer.start(int(delay * 1000))
        else:
            self._modality_retry_due_at = 0.0
            self._modality_retry_timer.stop()
        if self._smart_observation and not image_model:
            self.status_sink("Luna 与 Terra 均未声明图像输入；已退回应用级感知")
        self.changed.emit()

    @Slot(result=bool)
    def retrySmartObservationProbe(self) -> bool:
        if (
            self._closing
            or not self._smart_observation
            or self._probe_busy
            or self._busy
            or bool(self._active_generation_token)
        ):
            return False
        self._modality_retry_attempt = 0
        self._modality_retry_due_at = 0.0
        self._modality_retry_timer.stop()
        self._probe_modalities()
        return self._probe_busy

    @Slot(bool)
    def authorizeOnlineContent(self, enabled: bool) -> None:
        if self._closing:
            return
        self._online_content = bool(enabled)
        self.database.set_setting("online_content_authorized", self._online_content)
        self.content.fetcher = UrllibFetcher() if self._online_content else None
        if self._active and self._online_content:
            self._content_timer.start()
            QTimer.singleShot(0, self._refresh_next_source)
        else:
            self._content_timer.stop()
        self.sourcesChanged.emit()
        self.changed.emit()

    @Slot(str, int, int, result=bool)
    def setFrequency(self, mode: str, minimum_minutes: int, daily_limit: int) -> bool:
        # QML invokes this on the controller's owner thread. Reject accidental
        # direct calls from workers before they can read or publish a mixed
        # preference generation; cross-thread callers must queue to Qt first.
        if QThread.currentThread() != self.thread():
            return False
        try:
            previous = self._preferences_snapshot()
            normalized_mode = str(mode).casefold()
            candidate_custom: dict[str, int] | None = None
            if normalized_mode == "custom":
                custom_minutes = max(5, min(int(minimum_minutes), 180))
                custom_daily = max(1, min(int(daily_limit), 50))
                candidate_custom = {
                    "minimumMinutes": custom_minutes,
                    "dailyLimit": custom_daily,
                }
                minimum_minutes = custom_minutes
                daily_limit = custom_daily
            expected_minutes = int(minimum_minutes)
            expected_daily = int(daily_limit)
            value = self.preferences_model.to_mapping()
            value.update(
                {
                    "frequency": normalized_mode,
                    "minimumMinutes": expected_minutes,
                    "dailyLimit": expected_daily,
                }
            )
            committed = self._update_preferences(
                value,
                previous=previous,
                custom_frequency=candidate_custom,
            )
        except Exception:
            # This slot is called directly by QML. A durable-write failure is
            # recoverable UI state, not an exception that should tear down the
            # QML call stack and strand an uncommitted ComboBox selection.
            self.status_sink("主动陪伴频率保存失败，请稍后重试")
            return False
        return (
            str(committed.get("frequency", "")) == normalized_mode
            and int(committed.get("minimumMinutes", -1)) == expected_minutes
            and int(committed.get("dailyLimit", -1)) == expected_daily
        )

    @Slot(int, int, int)
    def setMix(self, interest: int, scene: int, half_life: int) -> None:
        value = self.preferences_model.to_mapping()
        value.update(
            {
                "interestWeight": int(interest),
                "sceneWeight": int(scene),
                "momentumHalfLifeMinutes": int(half_life),
            }
        )
        self._update_preferences(value)

    @Slot(str, int)
    def setCategoryWeight(self, category: str, weight: int) -> None:
        value = self.preferences_model.to_mapping()
        weights = dict(value["categoryWeights"])
        weights[str(category)] = int(weight)
        value["categoryWeights"] = weights
        self._update_preferences(value)

    def _update_preferences(
        self,
        value: dict[str, Any],
        *,
        previous: dict[str, Any] | None = None,
        custom_frequency: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        previous_snapshot = (
            self._preferences_snapshot() if previous is None else previous
        )
        try:
            preferences = CompanionPreferences.from_mapping(value)
        except (TypeError, ValueError) as exc:
            self.status_sink(str(exc))
            return self.preferences_model.to_mapping()
        preference_mapping = preferences.to_mapping()
        next_scores = {category: 0 for category in ContentCategory}
        next_cooldown = preferences.frequency.minimum_minutes * 60.0
        next_momentum = SceneMomentum(preferences.momentum_half_life_minutes)
        next_custom_frequency = (
            dict(self._custom_frequency)
            if custom_frequency is None
            else dict(custom_frequency)
        )
        next_snapshot = self._compose_preferences_snapshot(
            preferences=preferences,
            custom_frequency=next_custom_frequency,
        )

        # A custom frequency has two durable projections: the active
        # preference and the remembered custom draft used after switching to
        # a preset.  Commit them together before publishing any in-memory
        # state so a disk error cannot leave the UI, gate and database split.
        if custom_frequency is None:
            self.database.set_setting("companion_preferences", preference_mapping)
        else:
            self.database.set_settings(
                {
                    "companion_custom_frequency": dict(custom_frequency),
                    "companion_preferences": preference_mapping,
                }
            )

        if custom_frequency is not None:
            self._custom_frequency = next_custom_frequency
        self.preferences_model = preferences
        self.engine.update_preferences(preferences)
        # Preference changes start a fresh deterministic weighted cycle.
        # Otherwise a category that was disabled while carrying a positive
        # score could burst immediately when it is enabled again.
        self._category_smooth_scores = next_scores
        self.activity.cooldown_seconds = next_cooldown
        self.momentum = next_momentum
        self._preferences_state = next_snapshot
        self.changed.emit()
        if next_snapshot != previous_snapshot:
            self.preferencesChanged.emit()
        return preference_mapping

    @Slot(str)
    def setInterests(self, value: str) -> None:
        previous = self._preferences_snapshot()
        interests = [
            item.strip()[:80]
            for item in str(value).replace("，", ",").split(",")
            if item.strip()
        ][:30]
        next_interests = list(dict.fromkeys(interests))
        self.database.set_setting("companion_interests", next_interests)
        self._interests = next_interests
        next_snapshot = self._compose_preferences_snapshot()
        self._preferences_state = next_snapshot
        self.changed.emit()
        if next_snapshot != previous:
            self.preferencesChanged.emit()

    @Slot(str)
    def setScreenMemoryMode(self, mode: str) -> None:
        if mode not in {"replies", "significant", "all"}:
            return
        previous = self._preferences_snapshot()
        self.database.set_setting("screen_observation_memory", mode)
        self._screen_memory_mode = mode
        next_snapshot = self._compose_preferences_snapshot()
        self._preferences_state = next_snapshot
        self.changed.emit()
        if next_snapshot != previous:
            self.preferencesChanged.emit()

    @Slot(str)
    def refreshSource(self, provider_id: str) -> None:
        if self._closing or not provider_id or self._source_busy:
            return
        self._source_busy = True
        query = "science technology" if provider_id == "gdelt" else ""

        def worker() -> None:
            try:
                result = self.refresh_source_component(provider_id, query, 10)
            except Exception as exc:
                result = {"error": str(exc), "providerId": provider_id}
            if not self._closing:
                self._sourceReady.emit(result)

        if not self._start_worker(worker, name="lilies-content-refresh"):
            self._source_busy = False

    def _refresh_next_source(self) -> None:
        if self._closing or not self._active or not self._online_content or self._source_busy:
            return
        # GDELT remains a manual opt-in source.  Local interests are never sent
        # to any provider; they are used only for on-device ranking.
        provider_ids = [value for value in self.content.providers if value != "gdelt"]
        if not provider_ids:
            return
        provider_id = provider_ids[self._source_index % len(provider_ids)]
        self._source_index += 1
        self.refreshSource(provider_id)

    @Slot(str, str)
    def addCustomSource(self, label: str, url: str) -> None:
        clean_label = " ".join(str(label).split())[:80]
        try:
            clean_url = self._validated_feed_url(url)
        except ValueError as exc:
            self.status_sink(str(exc))
            return
        if not clean_label:
            self.status_sink("请填写订阅名称")
            return
        provider_id = "custom-" + hashlib.sha256(clean_url.encode("utf-8")).hexdigest()[:16]
        values = [value for value in self._custom_source_values() if value.get("id") != provider_id]
        values.append({"id": provider_id, "label": clean_label, "url": clean_url})
        self.database.set_setting("content_custom_feeds", values[-40:])
        self.content.providers[provider_id] = RssAtomProvider(provider_id, clean_label, clean_url)
        self.sourcesChanged.emit()
        self.status_sink(f"已添加订阅 · {clean_label}")

    @Slot(str)
    def removeCustomSource(self, provider_id: str) -> None:
        clean = str(provider_id)
        if not clean.startswith("custom-"):
            return
        values = [value for value in self._custom_source_values() if value.get("id") != clean]
        self.database.set_setting("content_custom_feeds", values)
        self.content.providers.pop(clean, None)
        self._source_counts.pop(clean, None)
        self.sourcesChanged.emit()

    def refresh_source_component(self, provider_id: str, query: str, limit: int) -> dict[str, Any]:
        result = self.content.refresh(
            provider_id,
            query,
            limit=limit,
            allow_network=self._online_content,
            force=True,
        )
        known = {item.id: item for item in self._content_items}
        for item in result.items:
            known[item.id] = item
            if result.state == "refreshed":
                self._fresh_content_ids.add(item.id)
            else:
                self._fresh_content_ids.discard(item.id)
        self._content_items = list(known.values())[-240:]
        self._source_counts[str(provider_id)] = len(result.items)
        return result.to_mapping()

    def _accept_source(self, payload: object) -> None:
        if self._closing:
            return
        self._source_busy = False
        value = dict(payload) if isinstance(payload, dict) else {}
        if value.get("error"):
            self.status_sink(f"内容源刷新失败：{value['error']}")
        else:
            self.status_sink(f"内容源已更新 · {value.get('providerId', '')}")
        self.sourcesChanged.emit()

    @Slot()
    def openSource(self) -> None:
        source = self._bubble.get("source")
        url = str(source.get("url", "")) if isinstance(source, dict) else ""
        if url:
            self._mark_bubble_interacted("open-source")
            open_web_url(url)

    @Slot(result=str)
    def saveMoment(self) -> str:
        if self._capture is None:
            self.status_sink("这个气泡没有保留截图")
            return ""
        self._mark_bubble_interacted("save-moment")
        target = self._capture.save(self._bubble.get("id"))
        self.database.save_memory_fragment(
            source_type="saved-observation",
            source_id=str(self._bubble.get("id", target.stem)),
            content=f"用户主动保存了场景：{self._bubble.get('sceneLabel', '')}。{self._bubble.get('summary', '')}",
            partition_id="daily",
            summary=str(self._bubble.get("summary", "")),
            importance=0.75,
        )
        self.status_sink(f"这一刻已保存到 F 盘 · {target.name}")
        return str(target)

    @Slot()
    def moveToBox(self) -> None:
        if self._bubble_object is None:
            return
        self._mark_bubble_interacted("move-to-box")
        payload = self.engine.move_to_box(self._bubble_object.id)
        self.move_to_box_callback(payload)
        self._clear_bubble(reason="move-to-box", mark_read=True)

    def _consider_archival(self) -> None:
        if self._closing or self._archive_busy:
            return
        try:
            idle_seconds = float(self.activity.idle_provider.idle_seconds())
        except (OSError, TypeError, ValueError):
            return
        if idle_seconds < 30.0:
            return
        self._archive_busy = True
        broker_task_id = self._submit_model_task(
            LUNA_MODEL,
            ModelTaskKind.MEMORY_ARCHIVE,
            {"requestId": uuid.uuid4().hex},
            context_bound=False,
            ttl_seconds=55.0,
        )

        def worker() -> None:
            lease = _BrokerTaskLease(
                self._model_broker,
                broker_task_id or None,
                LUNA_MODEL,
                abort=lambda: self.runtime.abort_model(LUNA_MODEL),
            )
            try:
                if not lease.acquire():
                    return
                proposal = self.runtime.propose_archive_one_pending()
                if lease.commit(
                    result={"completed": True, "proposed": proposal is not None}
                ) and proposal is not None:
                    try:
                        self.runtime.apply_archive_proposal(proposal)
                    except Exception as exc:
                        self.status_sink(
                            "后台记忆归档写入失败；原记忆仍保留为待归档"
                            f"（{type(exc).__name__}）"
                        )
            finally:
                lease.close(result={"completed": not lease.cancelled})
                self._forget_model_task(broker_task_id)
                self._archive_busy = False

        if not self._start_worker(worker, name="lilies-memory-archive"):
            self._archive_busy = False
            self._abandon_model_task(broker_task_id, "worker-start-failed")

    # Component bindings -------------------------------------------------
    def status_component(self) -> dict[str, Any]:
        return self.activityStatus

    def preferences_component(self) -> dict[str, Any]:
        return self.preferences

    def reply_component(self, bubble_id: str, text: str) -> dict[str, Any]:
        self.reply(bubble_id, text)
        return {"queued": self._busy, "bubbleId": bubble_id}

    def another_component(self, bubble_id: str) -> dict[str, Any]:
        self.another(bubble_id)
        return {"queued": self._busy, "previousBubbleId": bubble_id}

    def snooze_component(self, minutes: int) -> str:
        return self.snooze(minutes)

    def shutdown(self) -> None:
        with self._worker_lock:
            if self._closing:
                return
            self._closing = True
        self._active = False
        self._timer.stop()
        self._bubble_expiry_timer.stop()
        self._presentation_ack_timer.stop()
        self._archive_timer.stop()
        self._content_timer.stop()
        self._modality_retry_timer.stop()
        self.activity.stop()
        self._cancel_active_generation(
            "companion-shutdown", capture_reason="companion-shutdown"
        )
        self._release_capture()
        self.runtime.shutdown()
        self._join_workers()
        self._busy = False
        self._probe_busy = False
        self._archive_busy = False
        self._source_busy = False


__all__ = ["CompanionController"]
