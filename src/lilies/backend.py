from __future__ import annotations

import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import (
    QPoint,
    Property,
    QObject,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication

from .companion_controller import CompanionController
from .connectors import (
    CalendarReminderBridge,
    CalendarRuntime,
    InMemorySecretBackend,
    LoopbackOAuthReceiver,
    SecretStore,
    SecretStoreUnavailableError,
    SlackRuntime,
    UrllibHttpTransport,
)
from .connectors.slack_socket import SlackSocketService
from .core.components import (
    V02ComponentBindings,
    build_registry,
    register_v02_components,
)
from .core.activity import ForegroundContext
from .core.connector_assist import ConnectorAssistService
from .core.database import Database
from .core.data_migration import prepare_private_data, validate_startup_and_finalize
from .core.desktop import DesktopIndex
from .core.desktop_peek import DesktopPeekService
from .core.hotkeys import GlobalHotkey, parse_hotkey
from .core.focus_diversion import FocusDiversionMonitor, is_entertainment_process
from .core.input_pulse import InputPulseSource
from .core.model import ChatService
from .core.orchestration import (
    IntentArbiter,
    ModelTaskBroker,
    PresenceSignals,
    PresenceState,
    PresenceStateMachine,
)
from .core.permissions import PermissionBroker, PermissionMode
from .core.pet_habitat import PetHabitatController
from .core.productivity import (
    BoxWorldService,
    EventOutbox,
    FocusService,
    GrowthEngine,
    NarrativeDirector,
    ReadingSessionService,
    ReminderScheduler,
    TaskService,
    WardrobeService,
)
from .core.selection import SelectionService
from .core.shell import ShellController
from .core.socket_server import (
    LocalSocketServer,
    PRIMARY_SOCKET_PORT,
    RUNTIME_QT_HEARTBEAT_MAX_AGE_MS,
    RUNTIME_QT_HEARTBEAT_STALE_MS,
)
from .core.themes import ThemeManifest
from .core.v03_components import V03Services, register_v03_components
from .core.win_event import WinEvent, WinEventHub, WinEventKind
from .core.window_catalog import WindowCatalogService
from .core.window_icons import WindowIconCache
from .core.windows import (
    activate_window,
    foreground_window,
    list_windows,
    open_settings,
    reveal_in_explorer,
    system_status,
    window_fully_occluded,
)
from .paths import data_root, legacy_data_root, theme_root, to_file_url


_PRODUCTIVITY_COMPONENT_MUTATIONS = frozenset(
    {
        ("tasks", action)
        for action in {"create", "update", "complete", "reopen", "archive"}
    }
    | {
        ("focus", action)
        for action in {"start", "pause", "resume", "finish", "cancel"}
    }
    | {("reading", action) for action in {"start", "finish"}}
    | {
        ("reminders", action)
        for action in {"create", "snooze", "dismiss", "delete"}
    }
    | {("wardrobe", "equip"), ("box-world", "enter")}
)


_CORE_QUICK_ACTIONS: tuple[dict[str, Any], ...] = (
    {
        "action": "chat",
        "label": "对话",
        "shortLabel": "对话",
        "description": "打开与莉莉丝的完整对话。",
        "angle": -25.0,
        "fixed": True,
    },
    {
        "action": "world",
        "label": "盒中世界",
        "shortLabel": "盒中世界",
        "description": "进入盒中空间与成长陈设。",
        "angle": 155.0,
        "fixed": True,
    },
    {
        "action": "settings",
        "label": "设置",
        "shortLabel": "设置",
        "description": "打开设置与功能库。",
        "angle": -85.0,
        "fixed": True,
    },
)

_OPTIONAL_QUICK_ACTIONS: tuple[dict[str, str], ...] = (
    {
        "action": "companion",
        "label": "生活陪伴与屏幕观察",
        "shortLabel": "陪伴",
        "description": "立刻让莉莉丝说一句；观察状态与内容偏好仍可在设置中调整。",
    },
    {"action": "work", "label": "任务与工作台", "shortLabel": "工作", "description": "任务、提醒与工作进度。"},
    {"action": "focus", "label": "专注", "shortLabel": "专注", "description": "打开专注与休息控制。"},
    {"action": "letters", "label": "日历与 Slack 信笺", "shortLabel": "信笺", "description": "查看日程和通信入口。"},
    {"action": "peek", "label": "看桌面／返回工作", "shortLabel": "看桌面", "description": "临时收起或恢复本轮窗口。"},
    {
        "action": "lilies-desktop",
        "label": "莉莉丝桌面／仅桌宠",
        "shortLabel": "莉桌面",
        "description": "在完整动态桌面与透明桌宠之间切换；不会关闭当前应用。",
    },
    {"action": "reading", "label": "论文阅读", "shortLabel": "阅读", "description": "开始或管理论文阅读会话。"},
    {"action": "memory", "label": "记忆地图", "shortLabel": "记忆", "description": "查看分区记忆与原始出处。"},
    {"action": "wardrobe", "label": "衣橱与姿态", "shortLabel": "衣橱", "description": "更换已解锁的服装和姿态。"},
)

_OPTIONAL_QUICK_ACTION_IDS = frozenset(value["action"] for value in _OPTIONAL_QUICK_ACTIONS)
_OPTIONAL_QUICK_ACTION_ANGLES = (35.0, 95.0, -145.0)
_COMPACT_PET_MIN_SIZE = 110.0
_COMPACT_PET_MAX_SIZE = 320.0
_COMPACT_PET_EMERGENCY_MIN_SIZE = 48.0
_RUNTIME_VIDEO_PLAYBACK_STATES = frozenset(
    {"unloaded", "playing", "paused", "stopped", "error", "unknown"}
)
_RUNTIME_QT_HEARTBEAT_INTERVAL_MS = 250
_ACTIVATION_APPLY_TIMEOUT_SECONDS = 0.75


class _DiagnosticWindowProvider:
    """Empty provider used by smoke/offscreen runs.

    Diagnostic UI tests must never enumerate or activate the person's real
    windows.  Individual tests can still feed deterministic records directly
    into WindowCatalog/PetHabitat.
    """

    @property
    def available(self) -> bool:
        return False

    def enumerate_windows(self) -> tuple[()]:
        return ()

    def activate(self, _handle: int) -> bool:
        return False


class _ActivationRequest:
    """One socket-worker request handed to the Backend's Qt thread.

    ``deadline`` bounds only how long the socket waits for a synchronous
    result.  It must not cancel the queued action: loading the full desktop can
    legitimately keep the Qt thread inside QML for longer than the handshake
    budget.  In that case the caller receives an explicit ``pending`` ACK and
    this object remains claimable/completable by the Qt thread.
    """

    def __init__(self, action: str, timeout: float) -> None:
        self.action = action
        self.deadline = time.monotonic() + max(0.01, float(timeout))
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._state = "pending"
        self._result: dict[str, Any] | None = None

    def claim(self) -> bool:
        with self._lock:
            if self._state != "pending":
                return False
            self._state = "applying"
            return True

    def complete(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._result = dict(result)
            self._state = "done"
            self._event.set()

    def wait(self) -> dict[str, Any] | None:
        remaining = max(0.0, self.deadline - time.monotonic())
        if self._event.wait(remaining):
            with self._lock:
                return dict(self._result) if self._result is not None else None
        with self._lock:
            if self._state == "done" and self._result is not None:
                return dict(self._result)
        return None


class Backend(QObject):
    desktopItemsChanged = Signal()
    shellModeChanged = Signal()
    rendererChanged = Signal()
    chatChanged = Signal()
    chatOpenChanged = Signal()
    chatBusyChanged = Signal()
    statusChanged = Signal()
    modelStatusChanged = Signal()
    introChanged = Signal()
    memoryItemsChanged = Signal()
    readingItemsChanged = Signal()
    pendingToolChanged = Signal()
    windowItemsChanged = Signal()
    windowGroupsChanged = Signal()
    habitatChanged = Signal()
    inputPulseChanged = Signal()
    productivityChanged = Signal()
    connectorStatusChanged = Signal()
    connectorAssistChanged = Signal()
    connectorOAuthFinished = Signal(str, bool, str)
    connectorOperationFinished = Signal(str, str, bool, object)
    workPanelOpenChanged = Signal()
    workPanelSectionChanged = Signal()
    workPanelNavigationRequested = Signal(str)
    workPanelAnchorRequested = Signal(str)
    boxWorldEntryRequested = Signal()
    boxWorldPresentationRequested = Signal()
    boxWorldSceneOpenChanged = Signal()
    reminderDue = Signal(str, str)
    focusDiversionChanged = Signal()
    desktopLayoutsChanged = Signal()
    historyResultsChanged = Signal()
    permissionChanged = Signal()
    shellHealthChanged = Signal()
    systemStatusChanged = Signal()
    sceneActiveChanged = Signal()
    frameRateChanged = Signal()
    selectionChanged = Signal()
    selectionSettingsChanged = Signal()
    petFloatModeChanged = Signal()
    petAvoidanceChanged = Signal()
    petDragModeChanged = Signal()
    quickActionsChanged = Signal()
    desktopPeekChanged = Signal()
    desktopPeekHotkeyTriggered = Signal()
    memoryMapChanged = Signal()
    openConversationRequested = Signal()
    externalActivationRequested = Signal(str)
    _activationApplyRequested = Signal(object)
    _windowCatalogRefreshFinished = Signal(object)
    applicationActivationRequested = Signal(str)

    def __init__(
        self,
        smoke: bool = False,
        force_compact: bool = False,
        force_visual: bool = False,
        force_login: bool = False,
        activation_socket: Any | None = None,
    ) -> None:
        super().__init__()
        self._shutdown_complete = False
        self._shutdown_in_progress = False
        self._database_session: Any | None = None
        self._preview_mode = smoke
        self._status = "盒子已连接 · 本地"
        self.data_directory = data_root()
        self._migration_session = secrets.token_hex(16)
        self._migration_result: dict[str, Any] = {"status": "diagnostic"}
        if not smoke and not os.environ.get("LILIES_DATA_DIR"):
            migration = prepare_private_data(self.data_directory, legacy_data_root())
            self._migration_result = {
                "status": migration.status,
                "source": migration.source,
                "destination": migration.destination,
                "integrity": migration.database_integrity,
                "backupDirectory": migration.backup_directory,
            }
        self.database = Database(self.data_directory / "lilies.db")
        self.win_event_hub = WinEventHub()
        self.window_icon_cache = WindowIconCache(
            self.data_directory / "cache" / "window-icons"
        )
        self.window_catalog = WindowCatalogService(
            provider=_DiagnosticWindowProvider() if smoke else None,
            event_hub=self.win_event_hub,
            icon_resolver=self.window_icon_cache.resolve,
        )
        self.pet_habitat = PetHabitatController()
        self.input_pulse = InputPulseSource(enabled=not smoke)
        self.focus_diversion = FocusDiversionMonitor()
        self.presence = PresenceStateMachine()
        self.intent_arbiter = IntentArbiter()
        self.model_task_broker = ModelTaskBroker()
        self._window_items: list[dict[str, Any]] = []
        self._window_groups: list[dict[str, Any]] = []
        self._window_catalog_refresh_running = False
        self._window_catalog_thread: threading.Thread | None = None
        self._window_catalog_shutting_down = False
        self._window_catalog_pending_payload: dict[str, Any] | None = None
        self._window_catalog_refresh_queued = False
        self._window_catalog_cancel_event: threading.Event | None = None
        # Presence decisions normally arrive with EVENT_SYSTEM_FOREGROUND.
        # Keep the HWND/fingerprint that produced the last successful decision
        # so WindowCatalog can reconcile same-window full-screen transitions
        # and retry a transient context-reader failure without polling input.
        self._presence_context_handle = 0
        self._presence_retry_handle = 0
        self._presence_catalog_fingerprint: tuple[int, bool] = (0, False)
        self._habitat_status: dict[str, Any] = self.pet_habitat.status()
        self._companion_suppressed_status = (
            self.presence.snapshot().state is not PresenceState.NORMAL
        )
        self._input_pulse_status: dict[str, Any] = self.input_pulse.snapshot()
        self._work_panel_open = False
        self._work_panel_section = "work"
        self._box_world_scene_open = False
        self._focus_diversion_bubble: dict[str, Any] = {}
        self._focus_transition_sequence = 0
        self._focus_transition: dict[str, Any] = {
            "sequence": 0,
            "kind": "",
            "sessionId": "",
            "elapsedSeconds": 0,
            "durationSeconds": 0,
            "occurredAt": "",
        }
        self._last_work_window_handle = 0
        self._connector_proposal: dict[str, Any] = {}
        self._connector_selected_item: dict[str, str] = {"provider": "", "id": ""}
        self._connector_assist_result: dict[str, Any] = {
            "provider": "", "eventId": "", "instruction": "", "text": "",
            "busy": False, "error": "",
        }
        self._connector_error = ""
        self._connector_runtime_errors = {"calendar": "", "slack": ""}
        self._oauth_receivers: dict[str, LoopbackOAuthReceiver] = {}
        self._connector_threads: set[threading.Thread] = set()
        self._connector_confirm_inflight: dict[str, str] = {}
        self._calendar_refresh_running = False
        self._last_connector_maintenance_at = 0.0
        self._productivity_tick_failures = 0
        self._last_productivity_tick_at = time.monotonic()
        self._productivity_tick_in_progress = False
        self._productivity_dirty_lock = threading.Lock()
        self._productivity_dirty_generation = 0
        self._productivity_published_generation = 0
        self._last_v03_pump_at = time.monotonic()
        self.desktop_peek = DesktopPeekService(self.data_directory)
        self._desktop_peek_status = self.desktop_peek.status()
        if not smoke and self._desktop_peek_status.get("active"):
            self._desktop_peek_status = self.desktop_peek.recover_pending()
        self.permissions = PermissionBroker(self.database)
        self.theme = ThemeManifest.load(theme_root() / "theme.json")
        self.desktop = DesktopIndex(self.database)
        self.shell = ShellController(self.database, self.data_directory, smoke=smoke)
        self.shell.hotkey.callback = self.emergencyRestore
        self.selection = SelectionService(
            self.database,
            active=not smoke,
            model_broker=self.model_task_broker,
        )
        if force_compact:
            self.shell.mode = "compact"
        elif force_visual:
            self.shell.mode = "visual"
        elif force_login:
            self.shell.mode = "login"
        self.chat = ChatService(self.database, model_broker=self.model_task_broker)
        self.companion = CompanionController(
            self.database,
            self.data_directory,
            active=not smoke,
            status_sink=self._set_status,
            move_to_box=self._move_companion_to_box,
            unified_event_hub=self.win_event_hub,
            model_broker=self.model_task_broker,
        )
        self._companion_interaction_suspended = False
        self.growth = GrowthEngine(self.database)
        self.tasks = TaskService(self.database, growth=self.growth)
        self.focus = FocusService(self.database, growth=self.growth)
        self.reading_sessions = ReadingSessionService(self.database, growth=self.growth)
        self.reminders = ReminderScheduler(self.database)
        self.wardrobe = WardrobeService(self.database, self.theme)
        self.box_world = BoxWorldService(self.database)
        self.narrative = NarrativeDirector(self.database)
        self.event_outbox = EventOutbox(self.database)
        self.calendar_reminder_bridge = CalendarReminderBridge(
            self.database, self.reminders
        )
        self.connector_assist = ConnectorAssistService(
            self.data_directory / "codex-connector-assist",
            broker=self.model_task_broker,
        )
        self.connector_assist.resultReady.connect(self._on_connector_assist_result)
        self.connector_assist.busyChanged.connect(self._on_connector_assist_busy)
        self.calendar_connector: CalendarRuntime | None = None
        self.slack_connector: SlackRuntime | None = None
        self.slack_socket: SlackSocketService | None = None
        try:
            secret_store = SecretStore(
                "lilies-in-the-box-v03",
                backend=InMemorySecretBackend() if smoke else None,
            )
            connector_transport = UrllibHttpTransport(timeout=20.0)
            self.calendar_connector = CalendarRuntime(
                self.database,
                account_id="personal",
                secret_store=secret_store,
                transport=connector_transport,
            )
            self.slack_connector = SlackRuntime(
                self.database,
                account_id="personal",
                secret_store=secret_store,
                transport=connector_transport,
            )
            self.slack_socket = SlackSocketService(self.slack_connector, self)
            self.slack_socket.statusChanged.connect(self._on_slack_socket_status)
            self.slack_socket.itemReceived.connect(self._on_slack_item)
        except (OSError, RuntimeError, SecretStoreUnavailableError) as exc:
            # Credentials never fall back to a plaintext file.  Local tasks,
            # reminders and growth remain usable when secure storage is absent.
            self._connector_error = str(exc)
        self.registry = build_registry(
            self.database,
            self.permissions,
            self.desktop,
            self.shell,
            self.theme,
            self.chat.status,
            window_list=self.window_catalog.list_windows,
            window_activate=self.window_catalog.activate,
        )
        register_v02_components(
            self.registry,
            V02ComponentBindings(
                desktop_peek_status=self.desktop_peek.status,
                desktop_peek_toggle=self._component_desktop_peek_toggle,
                desktop_peek_restore=self._component_desktop_peek_restore,
                activity_status=self.companion.status_component,
                activity_set_policy=self.companion.setPolicy,
                companion_preferences=self.companion.preferences_component,
                companion_reply=self.companion.reply_component,
                companion_another=self.companion.another_component,
                companion_snooze=self.companion.snooze_component,
                memory_partitions=self.chat.memory.partitions,
                memory_recall=self._component_memory_recall,
                memory_reindex=self.chat.memory.reindex,
                memory_forget=self.chat.memory.forget,
                content_sources=lambda: self.companion.sources,
                content_refresh=self.companion.refresh_source_component,
            ),
        )
        register_v03_components(
            self.registry,
            V03Services(
                tasks=self.tasks,
                focus=self.focus,
                reading=self.reading_sessions,
                reminders=self.reminders,
                growth=self.growth,
                wardrobe=self.wardrobe,
                box_world=self.box_world,
                window_catalog=self.window_catalog,
                pet_habitat=self.pet_habitat,
                calendar=self.calendar_connector,
                slack=self.slack_connector,
                box_world_enter=self._componentEnterBoxWorld,
            ),
        )
        # Registry handlers may run on the model or local-socket worker and
        # bypass every QML slot below.  Keep a generation-only dirty marker at
        # that boundary; the one-second Qt tick performs the actual signal on
        # the Backend thread.  Chat-originated actions also receive an
        # immediate refresh through ``_on_component_invoked``.
        self._registry_invoke = self.registry.invoke
        self.registry.invoke = self._invoke_registered_component
        self.chat.bind_registry(self.registry)
        self.externalActivationRequested.connect(
            self._consumeExternalActivation,
            Qt.ConnectionType.QueuedConnection,
        )
        self._activationApplyRequested.connect(
            self._completeExternalActivationRequest,
            Qt.ConnectionType.QueuedConnection,
        )
        # Component actions may run in the model/socket worker thread.  Keep
        # the durable world-state write there, then marshal only the window
        # navigation back onto the Qt object thread.
        self.boxWorldEntryRequested.connect(
            self._presentBoxWorld,
            Qt.ConnectionType.QueuedConnection,
        )
        initial_runtime_renderer = str(
            self.database.get_setting(
                "theme_renderer", self.theme.default_renderer
            )
        )
        if initial_runtime_renderer not in self.theme.renderers:
            initial_runtime_renderer = self.theme.default_renderer
        self._runtime_snapshot_lock = threading.RLock()
        self._runtime_snapshot_state: dict[str, Any] = {
            "schemaVersion": 1,
            "shellMode": self.shell.mode,
            "renderer": initial_runtime_renderer,
            # Internal only: the public snapshot projects this monotonic value
            # to a bounded age and a boolean. It can never contain user data.
            "qtHeartbeatMonotonic": time.monotonic(),
            "scene": {
                "active": True,
                "scene2dLoaded": False,
                "videoLoaded": False,
                "videoPlaybackState": "unloaded",
            },
            "companion": {
                "enabled": bool(self.companion._activity_enabled),
                "paused": bool(self.companion.activity.paused),
                "presentationReady": False,
                "suppressed": False,
                "busy": False,
                "ackPending": False,
                "hasBubble": False,
                "unreadCount": 0,
                "state": "idle",
                "reason": "",
                "expiresInSeconds": 0.0,
            },
        }
        self._runtime_heartbeat_timer = QTimer(self)
        self._runtime_heartbeat_timer.setInterval(
            _RUNTIME_QT_HEARTBEAT_INTERVAL_MS
        )
        self._runtime_heartbeat_timer.timeout.connect(
            self._recordRuntimeHeartbeat
        )
        self._runtime_heartbeat_timer.start()
        self.socket = LocalSocketServer(
            self.registry,
            self.data_directory,
            port=0 if smoke and activation_socket is None else PRIMARY_SOCKET_PORT,
            activation_sink=self._requestExternalActivation,
            runtime_snapshot_provider=self.runtimeSnapshot,
            prebound_socket=activation_socket,
        )
        self.socket.start()
        self._desktop_items: list[dict[str, Any]] = []
        self._chat_text = ""
        self._chat_open = False
        self._chat_busy = False
        self._model_status = self.chat.status()
        self._memory_items = self.database.memory_cards()
        self._memory_partitions = self.chat.memory.partitions()
        self._memory_map = self.chat.memory.memory_map()
        self._reading_items = self.database.reading_cards()
        self._pending_tool: dict[str, Any] = {}
        self._history_results: list[dict[str, Any]] = []
        self._shell_health: dict[str, Any] = {"ok": False, "message": "尚未检查"}
        self._system_status: dict[str, Any] = system_status()
        self._scene_active = True
        self._frame_rate = 0.0
        self._selection_bubble = self.selection.bubble
        self._desktop_window_handle = 0
        self._intro_active = not bool(self.database.get_setting("intro_seen", False)) and not smoke
        self._renderer = str(self.database.get_setting("theme_renderer", self.theme.default_renderer))
        if self._renderer not in self.theme.renderers:
            self._renderer = self.theme.default_renderer
        self.shellModeChanged.connect(self._refreshRuntimeSnapshotIdentity)
        self.rendererChanged.connect(self._refreshRuntimeSnapshotIdentity)
        self.sceneActiveChanged.connect(self._refreshRuntimeSnapshotIdentity)
        self.companion.changed.connect(self._refreshRuntimeSnapshotIdentity)
        self._refreshRuntimeSnapshotIdentity()
        self._pet_float_mode = str(self.database.get_setting("pet_float_mode", "always"))
        if self._pet_float_mode not in {"always", "normal"}:
            self._pet_float_mode = "always"
        self._pet_avoidance_mode = str(
            self.database.get_setting("pet_pointer_avoidance", "gentle")
        )
        if self._pet_avoidance_mode not in {"off", "gentle", "lively"}:
            self._pet_avoidance_mode = "gentle"
        # Let Windows' compositor own a fresh installation's drag gesture.
        # It keeps the transparent top-level window on the native input path
        # instead of round-tripping every pointer frame through Python/QML.
        # An explicitly persisted ``direct`` value remains a supported
        # compatibility choice for window managers that reject system moves.
        self._pet_drag_mode = str(
            self.database.get_setting("pet_drag_mode", "system")
        )
        if self._pet_drag_mode not in {"direct", "system"}:
            self._pet_drag_mode = "system"
        self._pet_geometry: dict[str, Any] = {}
        self._pet_cursor_sample: tuple[float, float, float] | None = None
        self._pet_avoidance_cooldown_until = 0.0
        self._pet_interaction_lock_reasons: set[str] = set()
        self._pet_interaction_locked = False
        self._pet_pointer_critical_locked = False
        self._pet_pointer_paused_timers: dict[str, tuple[bool, int]] = {}
        self._pet_pointer_input_pulse_suspended = False
        self._pet_interaction_grace_until = 0.0
        self.pet_habitat.set_floating_mode(self._pet_float_mode)
        initial_box = self.boxLayout()
        initial_size = float(initial_box.get("size", 184))
        self.pet_habitat.set_pet_size(initial_size * 3.50, initial_size * 3.30)
        self._desktop_peek_hotkey_text = str(
            self.database.get_setting("desktop_peek_hotkey", "Ctrl+Alt+D")
        )
        try:
            self._desktop_peek_hotkey_text = parse_hotkey(self._desktop_peek_hotkey_text).display
        except ValueError:
            self._desktop_peek_hotkey_text = "Ctrl+Alt+D"
        self._desktop_peek_hotkey = GlobalHotkey(lambda: self.desktopPeekHotkeyTriggered.emit())
        self.desktopPeekHotkeyTriggered.connect(self.toggleDesktopPeek)
        if not smoke:
            self._desktop_peek_hotkey.start(self._desktop_peek_hotkey_text)
        self.chat.chunk.connect(self._on_chat_chunk)
        self.chat.responseStarted.connect(self._on_chat_started)
        self.chat.responseFinished.connect(self._on_chat_finished)
        self.chat.error.connect(self._on_chat_error)
        self.chat.installProgress.connect(self._set_status)
        self.chat.statusChanged.connect(self._set_model_status)
        self.chat.confirmationRequested.connect(self._on_confirmation)
        self.chat.componentInvoked.connect(self._on_component_invoked)
        self.chat.memoryCandidateCreated.connect(self.refreshMemory)
        self.connectorOAuthFinished.connect(self._on_connector_oauth_finished)
        self.connectorOperationFinished.connect(self._on_connector_operation_finished)
        self.selection.bubbleChanged.connect(self._on_selection_bubble)
        self.selection.settingsChanged.connect(self.selectionSettingsChanged)
        self.selection.statusChanged.connect(self._set_status)
        self._shell_monitor = QTimer(self)
        self._shell_monitor.setInterval(2000)
        self._shell_monitor.timeout.connect(self._monitor_shell)
        self._shell_monitor.start()
        self._windowCatalogRefreshFinished.connect(
            self._apply_window_catalog_refresh
        )
        self._catalog_unsubscribe = self.window_catalog.subscribe(self._on_window_catalog)
        if smoke:
            self.window_catalog.refresh()
        else:
            self.window_catalog.start()
            self.input_pulse.start()
        self._hub_activity_unsubscribe = self.win_event_hub.subscribe(self._on_unified_win_event)
        self._hub_habitat_unsubscribe = self.win_event_hub.subscribe(self.pet_habitat.handle_event)
        self._v03_timer = QTimer(self)
        self._v03_timer.setInterval(75)
        self._v03_timer.timeout.connect(self._pump_v03)
        self._v03_timer.start()
        self._productivity_timer = QTimer(self)
        self._productivity_timer.setInterval(1000)
        self._productivity_timer.timeout.connect(self._refreshProductivityTick)
        self._productivity_timer.start()
        self._calendar_sync_timer = QTimer(self)
        self._calendar_sync_timer.setSingleShot(True)
        self._calendar_sync_timer.timeout.connect(self.calendarRefresh)
        self.scanIcons()
        if not smoke and self.calendar_connector is not None:
            try:
                calendar_connected = bool(
                    self.calendar_connector.status().get("connected")
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                calendar_connected = False
                self._connector_runtime_errors["calendar"] = (
                    "calendar-status-unavailable"
                )
                self._calendar_sync_timer.start(60_000)
            if calendar_connected:
                # Startup refresh is deliberately delayed until QML and
                # recovery monitoring have finished initialization.
                self._calendar_sync_timer.start(2500)
        if (
            not smoke
            and self.slack_socket is not None
            and self.slack_connector is not None
        ):
            try:
                slack_status = self.slack_connector.status()
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                slack_status = {}
                self._connector_runtime_errors["slack"] = (
                    "slack-status-unavailable"
                )
            if slack_status.get("connected") and slack_status.get("socketReady"):
                QTimer.singleShot(0, self.slack_socket.start)
        if not smoke and not os.environ.get("LILIES_DATA_DIR"):
            self._migration_result = validate_startup_and_finalize(
                self.data_directory,
                legacy_data_root(),
                self._migration_session,
            )
        # The GUI thread reads several independent productivity projections on
        # every one-second tick.  Reopening the F:-backed SQLite database for
        # each projection produced 10--30 ms event-loop stalls even though the
        # queries themselves take well under a millisecond.  Keep exactly one
        # explicit, thread-owned connection for Backend's lifetime; worker
        # threads continue to receive their own short-lived connections.
        self._database_session = self.database.connection_session()
        self._database_session.__enter__()
        # Construction itself can legitimately exceed the stale threshold
        # before QApplication starts dispatching timers. Record completion on
        # this same Qt thread so the first post-construction snapshot is fresh.
        self._recordRuntimeHeartbeat()

    @Property("QVariantList", notify=desktopItemsChanged)
    def desktopItems(self) -> list[dict[str, Any]]:
        return self._desktop_items

    @Property("QVariantList", notify=desktopItemsChanged)
    def pinnedItems(self) -> list[dict[str, Any]]:
        return [
            self._icon_public(value)
            for value in self.database.desktop_items()
            if bool(value.get("pinned"))
        ][:3]

    @Property("QVariantList", notify=desktopItemsChanged)
    def dockLaunchItems(self) -> list[dict[str, Any]]:
        """Return the launchable library consumed by the grouped Dock drawer.

        ``desktopItems`` intentionally contains only the calm desktop page,
        while the Dock search is also expected to find Start-menu shortcuts
        and user-added files.  Keep this read-only projection separate so a
        Dock search never rewrites the desktop surface's temporary layout.
        """

        values = self.database.desktop_items()
        values.sort(
            key=lambda item: (
                not bool(item.get("pinned")),
                str(item.get("name", "")).casefold(),
                str(item.get("path", "")).casefold(),
            )
        )
        return [self._icon_public(value) for value in values[:400]]

    @Property(str, notify=shellModeChanged)
    def shellMode(self) -> str:
        return self.shell.mode

    @Property(str, notify=petFloatModeChanged)
    def petFloatMode(self) -> str:
        return self._pet_float_mode

    @Property(str, notify=petAvoidanceChanged)
    def petAvoidanceMode(self) -> str:
        return self._pet_avoidance_mode

    @Property(str, notify=petDragModeChanged)
    def petDragMode(self) -> str:
        return self._pet_drag_mode

    def _selected_quick_action_ids(self) -> list[str]:
        raw = self.database.get_setting("desktop_pet_quick_actions_v1", [])
        if not isinstance(raw, list):
            return []
        selected: list[str] = []
        for value in raw:
            action = str(value or "").strip().casefold()
            if action in _OPTIONAL_QUICK_ACTION_IDS and action not in selected:
                selected.append(action)
            if len(selected) == len(_OPTIONAL_QUICK_ACTION_ANGLES):
                break
        return selected

    @Property("QVariantList", notify=quickActionsChanged)
    def quickActions(self) -> list[dict[str, Any]]:
        catalog = {value["action"]: value for value in _OPTIONAL_QUICK_ACTIONS}
        actions = [dict(value) for value in _CORE_QUICK_ACTIONS]
        for index, action in enumerate(self._selected_quick_action_ids()):
            actions.append(
                {
                    **catalog[action],
                    "angle": _OPTIONAL_QUICK_ACTION_ANGLES[index],
                    "fixed": False,
                }
            )
        return actions

    @Slot(result="QVariantList")
    def currentQuickActions(self) -> list[dict[str, Any]]:
        """Return a fresh radial-menu snapshot, bypassing QML property caching."""
        return self.quickActions

    @Property("QVariantList", notify=quickActionsChanged)
    def functionCatalog(self) -> list[dict[str, Any]]:
        selected = set(self._selected_quick_action_ids())
        return [
            {**value, "selected": value["action"] in selected}
            for value in _OPTIONAL_QUICK_ACTIONS
        ]

    @Property(str, notify=rendererChanged)
    def renderer(self) -> str:
        return self._renderer

    @Property(str, notify=chatChanged)
    def chatText(self) -> str:
        return self._chat_text

    @Property(bool, notify=chatOpenChanged)
    def chatOpen(self) -> bool:
        return self._chat_open

    @Property(bool, notify=chatBusyChanged)
    def chatBusy(self) -> bool:
        return self._chat_busy

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property("QVariantMap", notify=modelStatusChanged)
    def modelStatus(self) -> dict[str, Any]:
        return self._model_status

    @Property(bool, notify=introChanged)
    def introActive(self) -> bool:
        return self._intro_active

    @Property("QVariantList", notify=memoryItemsChanged)
    def memoryItems(self) -> list[dict[str, Any]]:
        return self._memory_items

    @Property("QVariantList", notify=readingItemsChanged)
    def readingItems(self) -> list[dict[str, Any]]:
        return self._reading_items

    @Property("QVariantList", notify=memoryMapChanged)
    def memoryPartitions(self) -> list[dict[str, Any]]:
        return self._memory_partitions

    @Property("QVariantMap", notify=memoryMapChanged)
    def memoryMap(self) -> dict[str, Any]:
        return self._memory_map

    @Property(QObject, constant=True)
    def companionService(self) -> QObject:
        return self.companion

    @Property("QVariantMap", notify=pendingToolChanged)
    def pendingTool(self) -> dict[str, Any]:
        return self._pending_tool

    @Property("QVariantList", notify=windowItemsChanged)
    def windowItems(self) -> list[dict[str, Any]]:
        return self._window_items

    @Property("QVariantList", notify=windowGroupsChanged)
    def windowGroups(self) -> list[dict[str, Any]]:
        return self._window_groups

    @Property("QVariantMap", notify=habitatChanged)
    def habitatState(self) -> dict[str, Any]:
        return dict(self._habitat_status)

    @Property("QVariantMap", notify=inputPulseChanged)
    def inputPulse(self) -> dict[str, Any]:
        return dict(self._input_pulse_status)

    @Property(bool, notify=habitatChanged)
    def dockSuppressed(self) -> bool:
        return str(self._habitat_status.get("state", "")) in {"blocked", "silent"}

    @Property(bool, notify=habitatChanged)
    def companionSuppressed(self) -> bool:
        """Privacy/game suppression independent of pet/Dock full-screen hiding."""

        return bool(self._companion_suppressed_status)

    @Property("QVariantList", notify=productivityChanged)
    def taskItems(self) -> list[dict[str, Any]]:
        return [self._task_public(value) for value in self.tasks.list(limit=200)]

    @Property("QVariantMap", notify=productivityChanged)
    def focusStatus(self) -> dict[str, Any]:
        value = self.focus.status()
        if not value:
            return {"active": False, "paused": False, "elapsedSeconds": 0, "durationMinutes": 25}
        return {
            **dict(value),
            "id": value.get("session_id", ""),
            "sessionId": value.get("session_id", ""),
            "active": value.get("state") in {"running", "paused"},
            "paused": value.get("state") == "paused",
            "elapsedSeconds": int(value.get("live_active_seconds", value.get("active_seconds", 0))),
            "durationMinutes": max(5, int(value.get("planned_seconds", 1500)) // 60),
        }

    @Property("QVariantMap", notify=productivityChanged)
    def focusTransition(self) -> dict[str, Any]:
        """Latest sequenced lifecycle edge for one-shot focus animations."""

        return dict(self._focus_transition)

    @Property("QVariantMap", notify=focusDiversionChanged)
    def focusDiversion(self) -> dict[str, Any]:
        return dict(self._focus_diversion_bubble)

    @Property("QVariantMap", notify=productivityChanged)
    def readingStatus(self) -> dict[str, Any]:
        value = self.reading_sessions.status()
        if not value:
            return {"active": False, "paused": False, "elapsedSeconds": 0}
        return {
            **dict(value),
            "id": value.get("session_id", ""),
            "sessionId": value.get("session_id", ""),
            "active": value.get("state") in {"running", "paused"},
            "paused": value.get("state") == "paused",
            "elapsedSeconds": int(value.get("live_active_seconds", value.get("active_seconds", 0))),
        }

    @Property("QVariantList", notify=productivityChanged)
    def reminderItems(self) -> list[dict[str, Any]]:
        return [self._reminder_public(value) for value in self.reminders.list(limit=200)]

    @Property("QVariantMap", notify=productivityChanged)
    def growthStatus(self) -> dict[str, Any]:
        status = self.growth.status()
        return {
            **status,
            "points": int(status.get("totalPoints", 0)),
            "unlocks": self.growth.unlocks(),
        }

    @Property("QVariantMap", notify=productivityChanged)
    def wardrobeState(self) -> dict[str, Any]:
        return self.wardrobe.list()

    @Property("QVariantList", notify=productivityChanged)
    def wardrobeOutfits(self) -> list[dict[str, Any]]:
        return list(self.wardrobe.list().get("outfits", []))

    @Property("QVariantList", notify=productivityChanged)
    def wardrobePoses(self) -> list[dict[str, Any]]:
        return list(self.wardrobe.list().get("poses", []))

    @Property("QVariantMap", notify=productivityChanged)
    def boxWorldStatus(self) -> dict[str, Any]:
        world = self.box_world.status()
        growth = self.growth.status()
        wardrobe = self.wardrobe.list()
        current = dict(wardrobe.get("current") or {})
        current_outfit_id = str(current.get("outfit_id", ""))
        current_pose_id = str(current.get("pose_id", ""))
        current_outfit = next(
            (
                value
                for value in wardrobe.get("outfits", [])
                if str(value.get("id", "")) == current_outfit_id
            ),
            {},
        )
        current_pose = next(
            (
                value
                for value in wardrobe.get("poses", [])
                if str(value.get("id", "")) == current_pose_id
            ),
            {},
        )
        return {
            **world,
            "growth": {
                "points": int(growth.get("totalPoints", 0)),
                "stage": str(growth.get("stage", "初遇")),
                "nextStage": str(growth.get("nextStage", "")),
                "remaining": int(growth.get("remaining", 0)),
                "progress": float(growth.get("progress", 0.0)),
            },
            "wardrobe": {
                "outfitId": current_outfit_id,
                "outfitName": str(current_outfit.get("name", "当前服装")),
                "poseId": current_pose_id,
                "poseName": str(current_pose.get("name", "当前姿态")),
            },
        }

    @Property("QVariantMap", notify=connectorStatusChanged)
    def calendarStatus(self) -> dict[str, Any]:
        return self._connector_status("calendar")

    @Property("QVariantMap", notify=connectorStatusChanged)
    def slackStatus(self) -> dict[str, Any]:
        return self._connector_status("slack")

    @Property("QVariantList", notify=connectorStatusChanged)
    def calendarUpcoming(self) -> list[dict[str, Any]]:
        if self.calendar_connector is None:
            return []
        try:
            return [dict(value) for value in self.calendar_connector.upcoming(limit=30)]
        except (KeyError, OSError, RuntimeError, ValueError):
            return []

    @Property("QVariantList", notify=connectorStatusChanged)
    def slackInbox(self) -> list[dict[str, Any]]:
        if self.slack_connector is None:
            return []
        try:
            return [dict(value) for value in self.slack_connector.inbox(limit=30)]
        except (KeyError, OSError, RuntimeError, ValueError):
            return []

    @Property("QVariantMap", notify=connectorStatusChanged)
    def connectorProposal(self) -> dict[str, Any]:
        value = dict(self._connector_proposal)
        provider = str(value.get("connector", ""))
        if provider:
            value["executing"] = provider in self._connector_confirm_inflight
        return value

    @Property("QVariantMap", notify=connectorStatusChanged)
    def connectorSelectedItem(self) -> dict[str, str]:
        return dict(self._connector_selected_item)

    @Property("QVariantMap", notify=connectorAssistChanged)
    def connectorAssistResult(self) -> dict[str, Any]:
        return dict(self._connector_assist_result)

    @Property(str, notify=connectorStatusChanged)
    def slackManifestText(self) -> str:
        if self.slack_connector is None:
            return ""
        try:
            return json.dumps(
                self.slack_connector.manifest(), ensure_ascii=False, indent=2
            )
        except (KeyError, OSError, RuntimeError, ValueError):
            return ""

    @Property(bool, notify=workPanelOpenChanged)
    def workPanelOpen(self) -> bool:
        return self._work_panel_open

    @Property(str, notify=workPanelSectionChanged)
    def workPanelSection(self) -> str:
        return self._work_panel_section

    @Property(bool, notify=boxWorldSceneOpenChanged)
    def boxWorldSceneOpen(self) -> bool:
        return self._box_world_scene_open

    @Property("QVariantList", notify=desktopLayoutsChanged)
    def desktopLayouts(self) -> list[dict[str, Any]]:
        return [
            {"layoutId": value["layout_id"], "name": value["name"], "active": value["active"]}
            for value in self.database.desktop_layouts()
        ]

    @Property(str, notify=desktopLayoutsChanged)
    def activeDesktopLayout(self) -> str:
        return str(self.database.get_setting("active_desktop_layout", "default"))

    @Property("QVariantList", notify=historyResultsChanged)
    def historyResults(self) -> list[dict[str, Any]]:
        return self._history_results

    @Property(str, notify=permissionChanged)
    def permissionMode(self) -> str:
        return self.permissions.mode.value

    @Property("QVariantList", notify=permissionChanged)
    def trustedActions(self) -> list[dict[str, Any]]:
        allowed = set(self.database.get_setting("trusted_allowlist", []))
        return [
            {
                "key": f"{value['componentId']}.{value['actionId']}",
                "title": value["title"],
                "risk": value["risk"],
                "allowed": f"{value['componentId']}.{value['actionId']}" in allowed,
            }
            for value in self.registry.list()
            if value["risk"] == "mutate"
        ]

    @Property("QVariantMap", notify=shellHealthChanged)
    def shellHealth(self) -> dict[str, Any]:
        return self._shell_health

    @Property("QVariantMap", notify=systemStatusChanged)
    def systemStatus(self) -> dict[str, Any]:
        return self._system_status

    @Property(bool, notify=sceneActiveChanged)
    def sceneActive(self) -> bool:
        return self._scene_active

    @Property(float, notify=frameRateChanged)
    def frameRate(self) -> float:
        return self._frame_rate

    @Property("QVariantMap", notify=selectionChanged)
    def selectionBubble(self) -> dict[str, Any]:
        return self._selection_bubble

    @Property(bool, notify=selectionSettingsChanged)
    def selectionEnabled(self) -> bool:
        return self.selection.enabled

    @Property(bool, notify=selectionSettingsChanged)
    def selectionSubscriptionReady(self) -> bool:
        return self.selection.subscription_ready

    @Property(str, notify=selectionSettingsChanged)
    def selectionStatus(self) -> str:
        return self.selection.status

    @Property("QVariantMap", notify=desktopPeekChanged)
    def desktopPeekStatus(self) -> dict[str, Any]:
        return dict(self._desktop_peek_status)

    @Property(str, notify=desktopPeekChanged)
    def desktopPeekHotkey(self) -> str:
        return self._desktop_peek_hotkey_text

    @Property("QVariantMap", notify=desktopPeekChanged)
    def dataMigrationStatus(self) -> dict[str, Any]:
        return dict(self._migration_result)

    @Property(str, constant=True)
    def privateDataPath(self) -> str:
        return str(self.data_directory)

    @Property(bool, notify=shellHealthChanged)
    def loginShellEnabled(self) -> bool:
        return bool(self.database.get_setting("login_shell_enabled", False))

    @Property(str, constant=True)
    def socketEndpoint(self) -> str:
        return self.socket.endpoint

    @Property(str, constant=True)
    def themeTitle(self) -> str:
        return self.theme.title

    @Property("QVariantMap", constant=True)
    def themeManifest(self) -> dict[str, Any]:
        return self.theme.public()

    @Property(bool, constant=True)
    def previewMode(self) -> bool:
        return self._preview_mode

    @Slot(str, result=str)
    def assetUrl(self, key: str) -> str:
        value = self.theme.asset(key)
        return to_file_url(value) if value else ""

    @Slot(result="QVariantMap")
    def boxLayout(self) -> dict[str, Any]:
        value = self.database.get_setting(
            "compact_box_layout",
            {"x": 1180, "y": 650, "size": 184},
        )
        if not isinstance(value, dict):
            value = {"x": 1180, "y": 650, "size": 184}
        try:
            size = float(value.get("size", 184))
        except (TypeError, ValueError):
            size = 184.0
        return {
            **value,
            "size": max(_COMPACT_PET_MIN_SIZE, min(_COMPACT_PET_MAX_SIZE, size)),
        }

    @Slot(result="QVariantMap")
    def cursorPosition(self) -> dict[str, int]:
        """Return the current global pointer position for exact pet dragging.

        The value is sampled only while the user is actively holding Lilith;
        it is never persisted, logged, or forwarded to a model.
        """

        point = QCursor.pos()
        return {"x": int(point.x()), "y": int(point.y())}

    @Slot(float, float, result="QVariantMap")
    def screenWorkAreaAt(self, x: float, y: float) -> dict[str, Any]:
        """Return the logical Qt work area nearest one global pointer point."""

        point = QPoint(round(float(x)), round(float(y)))
        screen = QGuiApplication.screenAt(point)
        screens = list(QGuiApplication.screens())
        if screen is None and screens:
            def distance_squared(candidate: object) -> int:
                rect = candidate.geometry()
                nearest_x = max(rect.left(), min(point.x(), rect.right()))
                nearest_y = max(rect.top(), min(point.y(), rect.bottom()))
                return (point.x() - nearest_x) ** 2 + (point.y() - nearest_y) ** 2

            screen = min(screens, key=distance_squared)
        if screen is None:
            return {
                "left": 0,
                "top": 0,
                "right": 1920,
                "bottom": 1080,
                "width": 1920,
                "height": 1080,
                "name": "fallback",
                "devicePixelRatio": 1.0,
            }
        rect = screen.availableGeometry()
        return {
            "left": int(rect.x()),
            "top": int(rect.y()),
            "right": int(rect.x() + rect.width()),
            "bottom": int(rect.y() + rect.height()),
            "width": int(rect.width()),
            "height": int(rect.height()),
            "name": str(screen.name()),
            "devicePixelRatio": float(screen.devicePixelRatio()),
        }

    @Slot(float, float, float)
    def saveBoxLayout(self, x: float, y: float, size: float) -> None:
        safe_size = max(_COMPACT_PET_MIN_SIZE, min(_COMPACT_PET_MAX_SIZE, size))
        self.database.set_setting(
            "compact_box_layout",
            {"x": round(x, 2), "y": round(y, 2), "size": round(safe_size, 2)},
        )
        self.pet_habitat.set_pet_size(safe_size * 3.50, safe_size * 3.30)

    @Slot(float)
    def setCompactPetEffectiveSize(self, size: float) -> None:
        """Keep habitat calculations aligned with the size QML is rendering.

        The saved user preference remains in the normal 110..320 range.  A
        very small logical work area may temporarily render below 110 so the
        entire click target remains reachable; that emergency fit must not
        overwrite the preferred size used when the pet returns to a larger
        monitor.
        """

        try:
            numeric_size = float(size)
        except (TypeError, ValueError):
            return
        if not math.isfinite(numeric_size):
            return
        effective_size = max(
            _COMPACT_PET_EMERGENCY_MIN_SIZE,
            min(_COMPACT_PET_MAX_SIZE, numeric_size),
        )
        self.pet_habitat.set_pet_size(effective_size * 3.50, effective_size * 3.30)

    @Slot(str, bool)
    def setQuickActionPinned(self, action: str, pinned: bool) -> None:
        action_id = str(action or "").strip().casefold()
        if action_id not in _OPTIONAL_QUICK_ACTION_IDS:
            self._set_status("这个功能不在可选功能库中")
            return
        selected = self._selected_quick_action_ids()
        changed = False
        if pinned and action_id not in selected:
            if len(selected) >= len(_OPTIONAL_QUICK_ACTION_ANGLES):
                self._set_status("常用功能最多选择 3 个")
                return
            selected.append(action_id)
            changed = True
        elif not pinned and action_id in selected:
            selected.remove(action_id)
            changed = True
        if changed:
            self.database.set_setting("desktop_pet_quick_actions_v1", selected)
            self.quickActionsChanged.emit()
            self._set_status(f"常用功能已更新 · {len(selected)}/3")

    @Slot(str, int)
    def moveQuickAction(self, action: str, direction: int) -> None:
        selected = self._selected_quick_action_ids()
        action_id = str(action or "").strip().casefold()
        if action_id not in selected:
            return
        old_index = selected.index(action_id)
        new_index = max(0, min(len(selected) - 1, old_index + (-1 if direction < 0 else 1)))
        if new_index == old_index:
            return
        selected.insert(new_index, selected.pop(old_index))
        self.database.set_setting("desktop_pet_quick_actions_v1", selected)
        self.quickActionsChanged.emit()

    @Slot()
    def clearQuickActions(self) -> None:
        self.database.set_setting("desktop_pet_quick_actions_v1", [])
        self.quickActionsChanged.emit()

    @Slot("QVariantMap")
    def updatePetGeometry(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        numeric_fields = (
            "windowX", "windowY", "windowWidth", "windowHeight",
            "figureLeft", "figureTop", "figureWidth", "figureHeight",
            "workLeft", "workTop", "workWidth", "workHeight",
        )
        geometry: dict[str, Any] = {}
        try:
            for field in numeric_fields:
                geometry[field] = float(value.get(field, 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        for field in ("menuOpen", "pointerDown", "visible"):
            geometry[field] = bool(value.get(field, False))
        self._pet_geometry = geometry

    @Slot(str)
    def setPetAvoidanceMode(self, mode: str) -> None:
        value = str(mode or "").strip().casefold()
        if value not in {"off", "gentle", "lively"}:
            return
        if value == self._pet_avoidance_mode:
            return
        self._pet_avoidance_mode = value
        self.database.set_setting("pet_pointer_avoidance", value)
        self._pet_cursor_sample = None
        self.pet_habitat.clear_avoidance()
        self._habitat_status = self.pet_habitat.status()
        self.petAvoidanceChanged.emit()
        self.habitatChanged.emit()

    @Slot(str)
    def setPetDragMode(self, mode: str) -> None:
        value = str(mode or "").strip().casefold()
        if value not in {"direct", "system"} or value == self._pet_drag_mode:
            return
        self._pet_drag_mode = value
        self.database.set_setting("pet_drag_mode", value)
        self.petDragModeChanged.emit()

    def _publish_pet_interaction_lock(self) -> None:
        value = bool(self._pet_interaction_lock_reasons)
        pointer_critical = any(
            reason not in {"menu", "companion-unread", "desktop-mode-tab"}
            for reason in self._pet_interaction_lock_reasons
        )
        if value != self._pet_interaction_locked:
            self._pet_interaction_locked = value
            self._pet_cursor_sample = None
            if value:
                self._pet_interaction_grace_until = 0.0
            else:
                self._pet_interaction_grace_until = time.monotonic() + 0.8
            self.selection.set_interaction_suspended(value)

        if pointer_critical == self._pet_pointer_critical_locked:
            return
        self._pet_pointer_critical_locked = pointer_critical
        if pointer_critical:
            self._pause_pointer_critical_runtime()
            self.companion.set_interaction_suspended(True)
            self._companion_interaction_suspended = True
            # A native catalogue walk consists of many short ctypes calls and
            # Python EnumWindows callbacks.  It is already off the GUI thread,
            # but those callbacks can still briefly acquire the GIL.  Ask the
            # current snapshot to stop cooperatively and retain one refresh
            # obligation for after the drag release grace period.
            if self._window_catalog_refresh_running:
                self._window_catalog_refresh_queued = True
                cancel_event = self._window_catalog_cancel_event
                if cancel_event is not None:
                    cancel_event.set()
        else:
            # Recurring Companion timers restart at their full intervals, so
            # releasing the pointer-critical edge cannot immediately wake
            # low-priority work.  Resume here instead of waiting for the
            # general interaction grace: a stationary character click leaves
            # the radial menu lock active, and an explicit Companion action
            # from that menu must not reject itself as "suspended".
            self._resume_companion_after_pet_interaction()
            self._resume_pointer_critical_runtime()

    def _pause_pointer_critical_runtime(self) -> None:
        """Remove recurring Python/Qt wakeups from a held native gesture."""

        if self._pet_pointer_paused_timers:
            return
        # Keep the liveness heartbeat and recovery monitor running: a long,
        # intentional drag must not be reported as a frozen GUI, and safety
        # timers must retain their own cancellation/generation semantics.
        # Only the recurring projection/productivity work that can compete
        # with DWM is paused here.
        for name in (
            "_v03_timer",
            "_productivity_timer",
        ):
            timer = getattr(self, name, None)
            if timer is None:
                continue
            try:
                active = bool(timer.isActive())
            except (AttributeError, RuntimeError, TypeError):
                active = False
            if not active:
                continue
            try:
                single_shot = bool(timer.isSingleShot())
            except (AttributeError, RuntimeError, TypeError):
                single_shot = False
            try:
                remaining = int(timer.remainingTime())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                remaining = -1
            self._pet_pointer_paused_timers[name] = (
                single_shot,
                max(1, remaining) if single_shot else 0,
            )
            try:
                timer.stop()
            except (AttributeError, RuntimeError):
                self._pet_pointer_paused_timers.pop(name, None)
        try:
            self.input_pulse.set_interaction_suspended(True)
            self._pet_pointer_input_pulse_suspended = True
        except (AttributeError, RuntimeError):
            self._pet_pointer_input_pulse_suspended = False

    def _resume_pointer_critical_runtime(self) -> None:
        """Resume paused services at full intervals, never with a catch-up burst."""

        paused = self._pet_pointer_paused_timers
        self._pet_pointer_paused_timers = {}
        if self._shutdown_in_progress or self._shutdown_complete:
            self._pet_pointer_input_pulse_suspended = False
            return
        if self._pet_pointer_input_pulse_suspended:
            try:
                self.input_pulse.set_interaction_suspended(False)
            except (AttributeError, RuntimeError):
                pass
            self._pet_pointer_input_pulse_suspended = False
        for name, (single_shot, remaining) in paused.items():
            timer = getattr(self, name, None)
            if timer is None:
                continue
            try:
                if single_shot:
                    timer.start(max(1, int(remaining)))
                else:
                    timer.start()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        self._recordRuntimeHeartbeat()

    def _resume_companion_after_pet_interaction(self) -> None:
        """Resume companionship after the pointer-critical owner releases."""

        if not self._companion_interaction_suspended:
            return
        self.companion.set_interaction_suspended(False)
        self._companion_interaction_suspended = False

    @Slot(str, bool)
    def setPetInteractionLock(self, reason: str, locked: bool) -> None:
        """Aggregate interaction locks so one gesture cannot unlock another."""

        key = str(reason or "").strip().casefold()
        if not key:
            return
        if bool(locked):
            self._pet_interaction_lock_reasons.add(key)
        else:
            self._pet_interaction_lock_reasons.discard(key)
        self._publish_pet_interaction_lock()

    @Slot()
    def clearPetInteractionLocks(self) -> None:
        self._pet_interaction_lock_reasons.clear()
        self._publish_pet_interaction_lock()

    @Slot(bool)
    def setPetInteractionLocked(self, locked: bool) -> None:
        """Compatibility shim for callers that predate reason-aware locks."""

        self.setPetInteractionLock("legacy", locked)

    @Slot(str, float, result="QVariantMap")
    def componentLayout(self, action: str, angle: float) -> dict[str, float]:
        # v2 orbits the controls around Lilith herself.  Keep it separate from
        # the old box-centred coordinates so stale layouts cannot pull buttons
        # away from the new desktop pet.
        layouts = self.database.get_setting("desktop_pet_component_layout_v3", {})
        value = layouts.get(action, {}) if isinstance(layouts, dict) else {}
        radians = math.radians(angle)
        return {
            "dx": float(value.get("dx", math.cos(radians) * 1.18)),
            "dy": float(value.get("dy", math.sin(radians) * 1.18)),
            "scale": float(value.get("scale", 1.0)),
        }

    @Slot(str, float, float, float)
    def saveComponentLayout(self, action: str, dx: float, dy: float, scale: float) -> None:
        layouts = self.database.get_setting("desktop_pet_component_layout_v3", {})
        if not isinstance(layouts, dict):
            layouts = {}
        layouts[action] = {
            "dx": round(max(-1.48, min(1.48, dx)), 4),
            "dy": round(max(-1.42, min(1.42, dy)), 4),
            "scale": round(max(0.70, min(1.55, scale)), 4),
        }
        self.database.set_setting("desktop_pet_component_layout_v3", layouts)

    @Slot()
    def resetComponentLayouts(self) -> None:
        self.database.set_setting("desktop_pet_component_layout_v3", {})
        self.quickActionsChanged.emit()

    @Slot(result="QVariantMap")
    def accessoryBoxLayout(self) -> dict[str, float]:
        return self.database.get_setting(
            "desktop_pet_accessory_box_v2",
            {"dx": 0.62, "dy": 0.56, "scale": 0.42},
        )

    @Slot(float, float, float)
    def saveAccessoryBoxLayout(self, dx: float, dy: float, scale: float) -> None:
        self.database.set_setting(
            "desktop_pet_accessory_box_v2",
            {
                "dx": round(max(-1.20, min(1.20, dx)), 4),
                "dy": round(max(-1.10, min(1.22, dy)), 4),
                "scale": round(max(0.28, min(0.66, scale)), 4),
            },
        )

    @Slot()
    def scanIcons(self) -> None:
        self.desktop.scan()
        values = self.desktop.desktop_view()
        self._desktop_items = [self._icon_public(value) for value in values]
        self.desktopItemsChanged.emit()

    @Slot(str)
    def searchIcons(self, query: str) -> None:
        self._desktop_items = [self._icon_public(value) for value in self.desktop.items(query)]
        self.desktopItemsChanged.emit()

    def _icon_public(self, value: dict[str, Any]) -> dict[str, Any]:
        glyph = {"folder": "▱", "application": "◉", "file": "□"}.get(value.get("kind"), "□")
        return {
            "itemId": value["item_id"],
            "name": value["name"],
            "path": value["path"],
            "source": value["source"],
            "kind": value["kind"],
            "group": value["group_name"],
            "x": float(value["x"]),
            "y": float(value["y"]),
            "pinned": bool(value["pinned"]),
            "glyph": glyph,
        }

    @Slot(str, result=bool)
    def openItem(self, item_id: str) -> bool:
        for value in self.database.desktop_items(include_hidden=True):
            if value["item_id"] == item_id:
                try:
                    self.registry.invoke("desktop-icons", "open", {"path": value["path"]}, confirmed=True)
                    self._set_status(f"已打开 · {value['name']}")
                    return True
                except Exception as exc:
                    self._set_status(f"无法打开 · {exc}")
                    return False
        self._set_status("项目不存在或已经从启动库移除")
        return False

    @Slot(str, float, float)
    def saveIconPosition(self, item_id: str, x: float, y: float) -> None:
        self.database.update_desktop_layout(item_id, x=round(x, 1), y=round(y, 1))

    @Slot(str, bool)
    def setIconPinned(self, item_id: str, pinned: bool) -> None:
        self.database.update_desktop_layout(item_id, pinned=int(pinned))
        self.scanIcons()

    @Slot(str)
    def hideIcon(self, item_id: str) -> None:
        self.database.update_desktop_layout(item_id, hidden=1)
        self.scanIcons()

    @Slot(str, str)
    def setIconGroup(self, item_id: str, group_name: str) -> None:
        self.database.update_desktop_layout(item_id, group_name=group_name.strip()[:40] or "未分组")
        self.scanIcons()

    @Slot(str)
    def revealItem(self, item_id: str) -> None:
        match = next(
            (value for value in self.database.desktop_items(include_hidden=True) if value["item_id"] == item_id),
            None,
        )
        if match:
            reveal_in_explorer(match["path"])

    @Slot()
    def unhideAllIcons(self) -> None:
        for value in self.database.desktop_items(include_hidden=True):
            if bool(value["hidden"]):
                self.database.update_desktop_layout(value["item_id"], hidden=0)
        self.scanIcons()

    @Slot(str)
    def addDesktopRoot(self, value: str) -> None:
        path = Path(QUrl(value).toLocalFile() if value.startswith("file:") else value).resolve()
        if not path.is_dir():
            self._set_status("所选目录不存在")
            return
        roots = [str(Path(item).resolve()) for item in self.database.get_setting("desktop_extra_roots", [])]
        if str(path) not in roots:
            roots.append(str(path))
            self.database.set_setting("desktop_extra_roots", roots)
        self.scanIcons()
        self._set_status(f"已添加扫描目录 · {path.name}")

    @Slot(str)
    def createDesktopLayout(self, name: str) -> None:
        layouts = self.database.desktop_layouts()
        fallback = f"布局 {len(layouts) + 1}"
        self.database.create_desktop_layout(name.strip() or fallback)
        self.scanIcons()
        self.desktopLayoutsChanged.emit()

    @Slot(str)
    def activateDesktopLayout(self, layout_id: str) -> None:
        if self.database.activate_desktop_layout(layout_id):
            self.scanIcons()
            self.desktopLayoutsChanged.emit()

    @Slot(str)
    def deleteDesktopLayout(self, layout_id: str) -> None:
        if self.database.delete_desktop_layout(layout_id):
            self.scanIcons()
            self.desktopLayoutsChanged.emit()
        else:
            self._set_status("默认布局不能删除")

    @Slot()
    def refreshWindows(self) -> None:
        # User-initiated refreshes are durable. If a worker is already inside
        # EnumWindows, coalesce any number of clicks into exactly one fresh
        # snapshot after it completes rather than silently dropping them.
        self._request_window_catalog_refresh(queue_if_busy=True)

    def _request_window_catalog_refresh(
        self,
        now: float | None = None,
        *,
        queue_if_busy: bool = False,
    ) -> bool:
        """Enumerate DWM/process state without blocking Qt's GUI thread.

        Cache misses intentionally keep the Dock's text fallback until a GUI
        startup pass can populate them.  QFileIconProvider/QPixmap must never
        be constructed in this worker. Runtime GUI-thread icon hydration is
        intentionally not attempted: one synchronous shell icon lookup has no
        reliable latency bound and could begin immediately before a drag.
        """

        if self._window_catalog_shutting_down:
            return False
        interaction_busy = (
            self._pet_interaction_locked
            or time.monotonic() < self._pet_interaction_grace_until
        )
        if self._window_catalog_refresh_running or interaction_busy:
            if queue_if_busy:
                self._window_catalog_refresh_queued = True
            return False
        durable_request = bool(
            queue_if_busy or self._window_catalog_refresh_queued
        )
        self._window_catalog_refresh_running = True
        requested_at = time.monotonic() if now is None else float(now)
        cancel_event = threading.Event()
        self._window_catalog_cancel_event = cancel_event

        def refresh_in_background() -> None:
            try:
                groups = self.window_catalog.refresh(
                    requested_at,
                    notify=False,
                    icon_resolver=self.window_icon_cache.lookup,
                    should_cancel=cancel_event.is_set,
                )
                payload: dict[str, Any] = {
                    "ok": True,
                    "groups": groups,
                }
            except Exception as exc:
                payload = {
                    "ok": False,
                    "error": type(exc).__name__,
                    "groups": self.window_catalog.groups(),
                }
            if not self._window_catalog_shutting_down:
                try:
                    self._windowCatalogRefreshFinished.emit(payload)
                except RuntimeError:
                    # QObject teardown may win the narrow race after the
                    # shutdown flag check. The catalogue is a cache, so a late
                    # result is safe to discard.
                    pass

        thread = threading.Thread(
            target=refresh_in_background,
            name="lilies-window-catalog-refresh",
            daemon=True,
        )
        self._window_catalog_thread = thread
        try:
            thread.start()
        except RuntimeError:
            self._window_catalog_thread = None
            self._window_catalog_refresh_running = False
            self._window_catalog_cancel_event = None
            if durable_request:
                self._window_catalog_refresh_queued = True
            return False
        self._window_catalog_refresh_queued = False
        return True

    @Slot(object)
    def _apply_window_catalog_refresh(self, payload: object) -> None:
        self._window_catalog_refresh_running = False
        self._window_catalog_thread = None
        self._window_catalog_cancel_event = None
        if self._window_catalog_shutting_down:
            return
        result = payload if isinstance(payload, dict) else {}
        if (
            self._pet_interaction_locked
            or time.monotonic() < self._pet_interaction_grace_until
        ):
            # Even the cheap Qt-side projection can emit presence/habitat and
            # QML model changes. Never merge it inside Windows' modal move
            # loop; retain only the newest catalogue until the release grace
            # period has elapsed.
            self._window_catalog_pending_payload = dict(result)
            return
        self._commit_window_catalog_refresh(result)
        if self._window_catalog_refresh_queued:
            self._request_window_catalog_refresh(queue_if_busy=True)

    def _commit_window_catalog_refresh(self, result: dict[str, Any]) -> None:
        if not bool(result.get("ok")):
            return
        groups = result.get("groups")
        if not isinstance(groups, list):
            return
        self._on_window_catalog(groups)

    def _on_window_catalog(self, groups: list[dict[str, object]]) -> None:
        next_groups = [dict(value) for value in groups]
        next_items = [dict(value) for value in self.window_catalog.list_windows()]
        groups_changed = next_groups != self._window_groups
        items_changed = next_items != self._window_items
        self._window_groups = next_groups
        self._window_items = next_items
        active = next((value for value in next_items if bool(value.get("active"))), None)
        active_process_id = int((active or {}).get("processId") or 0)
        if active_process_id != os.getpid():
            # A focusable Lilies panel is not an application switch for the
            # pet's manual-detach scope.  Keep the last external HabitatHost
            # intact while the person types in chat/settings instead of
            # reattaching fifteen seconds after that panel closes.
            self.pet_habitat.update_foreground(active)
        self._reconcile_presence_from_catalog(active)
        if groups_changed:
            self.windowGroupsChanged.emit()
        if items_changed:
            self.windowItemsChanged.emit()

    def _apply_foreground_context(self, context: ForegroundContext) -> None:
        """Apply one successfully read foreground context on the Qt thread."""

        self.companion.updateForegroundContext(context)
        decision = self.companion.activity.guard.evaluate(context)
        reason = decision.reason
        snapshot = self.presence.update(
            PresenceSignals(
                sensitive=reason in {
                    "protected-content", "password-manager", "security-dialog",
                    "private-browsing", "payment-window", "application-blocked",
                },
                meeting=reason == "meeting",
                remote_desktop=reason == "remote-desktop",
                uac=reason == "security-dialog",
                fullscreen_game=bool(context.full_screen and context.is_game),
            )
        )
        self._presence_context_handle = int(context.hwnd)
        self._presence_retry_handle = 0
        self.pet_habitat.set_presence(snapshot.state.value)
        suppressed = snapshot.state is not PresenceState.NORMAL
        self.input_pulse.set_suppressed(suppressed)
        if not suppressed and not self._preview_mode:
            self.input_pulse.start()
        self.model_task_broker.set_foreground_context(
            f"{context.process_id}:{context.hwnd}:{context.process_name.casefold()}"
        )
        entertainment = is_entertainment_process(
            context.process_name, is_game=bool(context.is_game)
        )
        if not entertainment and snapshot.state is PresenceState.NORMAL:
            self._last_work_window_handle = int(context.hwnd)
        self.focus_diversion.update_foreground(
            f"{context.process_id}:{context.hwnd}",
            context.process_name,
            entertainment=entertainment,
            full_screen_game=bool(context.full_screen and context.is_game),
            defer_reminder=snapshot.state is PresenceState.BLOCKED,
        )
        # Publish the new privacy state synchronously.  Waiting for the next
        # 75 ms pump leaves a visible topmost window over a full-screen or
        # sensitive application for one frame interval.
        self._sync_habitat_state(force_cleanup=suppressed)

    def _reconcile_presence_from_catalog(
        self, active: dict[str, Any] | None
    ) -> None:
        """Reconcile event-derived privacy state with debounced native geometry.

        WinEvent does not emit a second foreground event when a game toggles
        full-screen on the same HWND.  The native catalogue does observe that
        geometry edge, so it is the authoritative fallback for clearing a
        stale ``fullscreen_game`` bit.  It also retries context classification
        after a transient reader failure.  BLOCKED reasons remain fail-closed
        until a real context read succeeds.
        """

        active_handle = int((active or {}).get("handle") or 0)
        full_screen = bool((active or {}).get("fullScreen", False))
        fingerprint = (active_handle, full_screen)
        previous_fingerprint = self._presence_catalog_fingerprint
        self._presence_catalog_fingerprint = fingerprint

        should_recheck = active_handle > 0 and (
            active_handle != self._presence_context_handle
            or active_handle == self._presence_retry_handle
            or fingerprint != previous_fingerprint
        )
        if should_recheck:
            try:
                context = self.companion.reader(active_handle)
                self._apply_foreground_context(context)
                return
            except (OSError, RuntimeError, ValueError):
                # WindowCatalog's next dirty/safety refresh retries this HWND.
                # Do not clear sensitive/UAC/meeting reasons on an unreadable
                # window; only native geometry may safely clear full-screen.
                self._presence_retry_handle = active_handle

        snapshot = self.presence.snapshot()
        if full_screen and not snapshot.signals.fullscreen_game:
            # If context classification is temporarily unavailable, native
            # full-screen geometry is still enough to fail closed.  A later
            # successful reader pass restores the more specific game/privacy
            # classification.
            snapshot = self.presence.update(fullscreen_game=True)
            self.pet_habitat.set_presence(snapshot.state.value)
            self.input_pulse.set_suppressed(True)
            self._sync_habitat_state(force_cleanup=True)
            return
        if (
            snapshot.signals.fullscreen_game
            and not full_screen
            and active_handle == self._presence_context_handle
        ):
            snapshot = self.presence.update(fullscreen_game=False)
            self.pet_habitat.set_presence(snapshot.state.value)
            suppressed = snapshot.state is not PresenceState.NORMAL
            self.input_pulse.set_suppressed(suppressed)
            if not suppressed and not self._preview_mode:
                self.input_pulse.start()
            self._sync_habitat_state(force_cleanup=suppressed)

    def _dismiss_transient_overlays(self, *, force: bool = False) -> None:
        """Remove non-explicit overlays when the current context is private/silent.

        ActivityContext normally reaches this path through a foreground
        WinEvent.  The native window catalogue also has an independent
        full-screen detector, though, so keeping the cleanup in one helper
        prevents a selection or focus bubble from surviving when that fallback
        is the first detector to identify a full-screen window.
        """

        companion_suppressed = (
            self.presence.snapshot().state is not PresenceState.NORMAL
        )
        companion_bubble = self.companion.bubble
        if companion_suppressed and (
            force or self.companion.busy or bool(companion_bubble)
        ):
            self.companion.dismiss()
        selection_bubble = self.selection.bubble
        if force or bool(selection_bubble.get("visible")) or bool(
            selection_bubble.get("busy")
        ):
            self.selection.dismiss()
        if self._focus_diversion_bubble:
            self._focus_diversion_bubble = {}
            self.focusDiversionChanged.emit()

    def _sync_habitat_state(self, *, force_cleanup: bool = False) -> dict[str, Any]:
        """Publish habitat changes and keep every transient overlay in sync."""

        next_habitat = self.pet_habitat.status()
        next_companion_suppressed = (
            self.presence.snapshot().state is not PresenceState.NORMAL
        )
        previous_companion_suppressed = self._companion_suppressed_status
        companion_suppression_changed = (
            next_companion_suppressed != previous_companion_suppressed
        )
        self._companion_suppressed_status = next_companion_suppressed
        if companion_suppression_changed:
            # Companion privacy follows sensitive/game presence, not the
            # habitat's broader rule that hides the pet and Dock for every
            # full-screen application (including WPS/PDF readers).
            self.companion.setPresentationSuppressed(next_companion_suppressed)
        previous_state = str(self._habitat_status.get("state", ""))
        next_state = str(next_habitat.get("state", ""))
        was_suppressed = previous_state in {"blocked", "silent"}
        is_suppressed = next_state in {"blocked", "silent"}
        if is_suppressed:
            # The active-map checks make this cheap and idempotent on every
            # 75 ms tick while still catching an asynchronous result that
            # arrives after the initial foreground event.
            self._dismiss_transient_overlays(
                force=force_cleanup or not was_suppressed
            )
        elif was_suppressed:
            # Do not let a result that completed during the quiet interval
            # reappear as an obsolete bubble when the user leaves full-screen.
            self._dismiss_transient_overlays(force=True)
        if next_habitat != self._habitat_status or companion_suppression_changed:
            self._habitat_status = next_habitat
            self.habitatChanged.emit()
        return next_habitat

    def _on_unified_win_event(self, event: WinEvent) -> None:
        if event.kind is not WinEventKind.FOREGROUND:
            return
        try:
            context = self.companion.reader(event.hwnd)
            self._apply_foreground_context(context)
        except (OSError, RuntimeError, ValueError):
            # The debounced native catalogue will retry this exact active HWND.
            # Keeping the previous BLOCKED state until that succeeds is the
            # conservative privacy choice, but the failure is no longer a
            # permanent suppression latch.
            self._presence_retry_handle = int(event.hwnd)
            return

    def _pump_pet_avoidance(self, now: float) -> None:
        geometry = self._pet_geometry
        if (
            self._preview_mode
            or self._pet_avoidance_mode == "off"
            or not geometry.get("visible")
            or self._pet_interaction_locked
            or now < self._pet_interaction_grace_until
            or geometry.get("menuOpen")
            or geometry.get("pointerDown")
            or str(self._habitat_status.get("state", "")) in {"blocked", "silent"}
        ):
            self._pet_cursor_sample = None
            return

        point = QCursor.pos()
        cursor_x = float(point.x())
        cursor_y = float(point.y())
        previous = self._pet_cursor_sample
        self._pet_cursor_sample = (cursor_x, cursor_y, now)
        if previous is None or now < self._pet_avoidance_cooldown_until:
            return

        figure_left = float(geometry.get("figureLeft", 0.0))
        figure_top = float(geometry.get("figureTop", 0.0))
        figure_width = float(geometry.get("figureWidth", 0.0))
        figure_height = float(geometry.get("figureHeight", 0.0))
        if figure_width < 44.0 or figure_height < 44.0:
            return
        figure_right = figure_left + figure_width
        figure_bottom = figure_top + figure_height
        # Once the pointer reaches the actual character, stop moving so a
        # deliberate click always wins over the avoidance animation.
        if figure_left <= cursor_x <= figure_right and figure_top <= cursor_y <= figure_bottom:
            return
        padding = 72.0 if self._pet_avoidance_mode == "gentle" else 118.0
        if not (
            figure_left - padding <= cursor_x <= figure_right + padding
            and figure_top - padding <= cursor_y <= figure_bottom + padding
        ):
            return

        old_x, old_y, old_time = previous
        elapsed = now - old_time
        if elapsed <= 0.0 or elapsed > 0.45:
            return
        center_x = figure_left + figure_width / 2
        center_y = figure_top + figure_height / 2
        old_distance = math.hypot(old_x - center_x, old_y - center_y)
        new_distance = math.hypot(cursor_x - center_x, cursor_y - center_y)
        speed = math.hypot(cursor_x - old_x, cursor_y - old_y) / elapsed
        threshold = 175.0 if self._pet_avoidance_mode == "gentle" else 95.0
        if speed < threshold or new_distance >= old_distance - 2.0:
            return

        work_left = float(geometry.get("workLeft", 0.0))
        work_top = float(geometry.get("workTop", 0.0))
        work_width = float(geometry.get("workWidth", 0.0))
        work_height = float(geometry.get("workHeight", 0.0))
        window_width = float(geometry.get("windowWidth", 0.0))
        window_height = float(geometry.get("windowHeight", 0.0))
        if min(work_width, work_height, window_width, window_height) <= 0.0:
            return
        margin = 10.0
        right = work_left + work_width - window_width - margin
        bottom = work_top + work_height - window_height - margin
        left = work_left + margin
        top = work_top + margin
        middle_y = work_top + (work_height - window_height) / 2
        candidates = (
            (left, top), (right, top), (left, bottom), (right, bottom),
            (left, middle_y), (right, middle_y),
        )
        current_x = float(geometry.get("windowX", 0.0))
        current_y = float(geometry.get("windowY", 0.0))

        def score(candidate: tuple[float, float]) -> float:
            candidate_center_x = candidate[0] + window_width / 2
            candidate_center_y = candidate[1] + window_height / 2
            pointer_distance = math.hypot(
                candidate_center_x - cursor_x,
                candidate_center_y - cursor_y,
            )
            travel = math.hypot(candidate[0] - current_x, candidate[1] - current_y)
            return pointer_distance - travel * 0.16

        target_x, target_y = max(candidates, key=score)
        if math.hypot(target_x - current_x, target_y - current_y) < 72.0:
            return
        work_area = self._habitat_work_area(
            work_left, work_top, work_width, work_height
        )
        if self.pet_habitat.set_avoidance_position(
            target_x, target_y, work_area, now=now
        ):
            self._pet_avoidance_cooldown_until = now + (
                5.0 if self._pet_avoidance_mode == "gentle" else 3.0
            )

    def _refresh_focus_diversion(
        self,
        focus: dict[str, Any] | None,
        habitat: dict[str, Any],
    ) -> bool:
        """Advance the human-scale focus reminder cadence.

        Diversion prompts are based on a 45-second stability threshold.  They
        do not belong in the 75 ms native-event pump: querying SQLite there
        kept the UI thread busy even when Lilies had no active session.  The
        one-second productivity clock is precise enough for this feature and
        keeps the high-frequency path free of storage work.
        """

        self.focus_diversion.set_focus(
            str(focus.get("session_id", "")) if focus else "",
            str(focus.get("state", "")) if focus else "",
        )
        diversion = self.focus_diversion.tick()
        if diversion is None:
            return False

        message = "刚才的专注还在。要回去、把这段算作休息，还是结束专注？"
        habitat_state = str(habitat.get("state", ""))
        if diversion.delivery == "windows-notification" or habitat_state == "silent":
            self.reminderDue.emit("莉莉丝 · 专注", message)
        elif habitat_state != "blocked":
            self._focus_diversion_bubble = {
                **diversion.to_dict(),
                "visible": True,
                "title": "专注轻提醒",
                "text": message,
            }
            self.focusDiversionChanged.emit()
        return True

    def _invoke_registered_component(
        self,
        component_id: str,
        action_id: str,
        payload: dict[str, Any] | None = None,
        *,
        origin: str = "ui",
        confirmed: bool = False,
    ) -> Any:
        """Track productivity writes even when a worker invokes the registry."""

        result = self._registry_invoke(
            component_id,
            action_id,
            payload,
            origin=origin,
            confirmed=confirmed,
        )
        if (str(component_id), str(action_id)) in _PRODUCTIVITY_COMPONENT_MUTATIONS:
            self._mark_productivity_dirty()
        return result

    def _mark_productivity_dirty(self) -> None:
        """Retain one committed refresh obligation across failures and threads."""

        with self._productivity_dirty_lock:
            self._productivity_dirty_generation += 1

    def _productivity_dirty_snapshot(self) -> tuple[int, bool]:
        with self._productivity_dirty_lock:
            generation = self._productivity_dirty_generation
            return (
                generation,
                generation > self._productivity_published_generation,
            )

    def _publish_productivity_generation(self, generation: int) -> None:
        with self._productivity_dirty_lock:
            self._productivity_published_generation = max(
                self._productivity_published_generation,
                int(generation),
            )

    @staticmethod
    def _habitat_work_area(left: float, top: float, width: float, height: float):
        from .core.window_catalog import WindowRect

        return WindowRect(
            round(left),
            round(top),
            round(left + width),
            round(top + height),
        )

    def _pump_v03(self) -> None:
        # Pointer-critical ownership normally stops this timer outright. Keep
        # the guard first for direct test calls and an already-posted timeout:
        # even draining WinEventHub here would invoke every Python subscriber
        # inside User32's native move loop.
        if self._pet_interaction_locked:
            return
        now = time.monotonic()
        woke_from_sleep = now - self._last_v03_pump_at > 90.0
        self._last_v03_pump_at = now
        if woke_from_sleep and not self._preview_mode:
            # A long Qt timer gap is the portable signal that this personal
            # desktop resumed from sleep.  Refresh once; the normal jittered
            # schedule resumes when the operation finishes.
            self.calendarRefresh()
        catalog_due = self.window_catalog.pump(now, refresh=False)
        catalog_quiet = (
            not self._pet_interaction_locked
            and now >= self._pet_interaction_grace_until
        )
        if catalog_quiet:
            self._resume_companion_after_pet_interaction()
            pending_catalog = self._window_catalog_pending_payload
            committed_pending_catalog = pending_catalog is not None
            if pending_catalog is not None:
                self._window_catalog_pending_payload = None
                self._commit_window_catalog_refresh(pending_catalog)
            if self._window_catalog_refresh_queued:
                self._request_window_catalog_refresh(
                    now,
                    queue_if_busy=True,
                )
            elif catalog_due and not committed_pending_catalog:
                self._request_window_catalog_refresh(now)
        habitat = self._sync_habitat_state()
        self._pump_pet_avoidance(now)
        # A producer may have completed while the state remained suppressed;
        # remove that result before the next frame can expose it.
        if str(habitat.get("state", "")) in {"blocked", "silent"}:
            self._dismiss_transient_overlays()
        next_pulse = self.input_pulse.snapshot()
        if next_pulse != self._input_pulse_status:
            self._input_pulse_status = next_pulse
            self.inputPulseChanged.emit()

    @Slot()
    def _refreshProductivityTick(self) -> None:
        """Keep the one-second scheduler alive across one transient failure."""

        # This recurring path performs several SQLite reads. Mutating slots
        # still refresh immediately, but the scheduler can safely wait for the
        # next one-second tick instead of stealing a native drag frame.
        if self._pet_interaction_locked:
            return
        try:
            self._productivity_tick_in_progress = True
            self.refreshProductivity()
        except Exception:
            # This boundary is intentionally broad: every concrete service
            # keeps its own typed error handling, while an unexpected plugin,
            # SQLite or Qt signal failure must not turn a recurring timer into
            # an invisible traceback in the console-free build.
            self._productivity_tick_failures = min(
                self._productivity_tick_failures + 1,
                999,
            )
            self._set_status("任务与提醒刷新暂时失败；下一秒会自动重试")
            return
        finally:
            self._productivity_tick_in_progress = False
        self._productivity_tick_failures = 0
        self._last_productivity_tick_at = time.monotonic()

    @Slot()
    def refreshProductivity(self) -> None:
        # The services calculate live elapsed seconds at read time.  A single
        # bounded signal keeps QML clocks and deterministic reminder delivery
        # current without tracking all-day application usage.
        if not self._productivity_tick_in_progress:
            # A QML/public slot call follows a mutation and therefore owns an
            # immediate refresh obligation.  Keep it durable until a signal
            # actually reaches the end of this method.
            self._mark_productivity_dirty()
        active_focus = self.focus.status()
        focus_finished = bool(
            active_focus
            and self._finish_elapsed_focus_if_due(active_focus)
        )
        if focus_finished:
            self._mark_productivity_dirty()
            active_focus = None
        active_reading = self.reading_sessions.status()
        # Direct mutations call this slot explicitly and must always publish
        # their new list/state.  Only the recurring one-second tick may skip a
        # redundant UI invalidation while the productivity system is idle.
        _, refresh_pending = self._productivity_dirty_snapshot()
        refresh_ui = bool(
            refresh_pending
            or focus_finished
            or active_focus
            or active_reading
        )
        self._refresh_focus_diversion(active_focus, self._habitat_status)
        now = time.monotonic()
        if now - self._last_connector_maintenance_at >= 3600.0:
            self._last_connector_maintenance_at = now
            for connector in (self.calendar_connector, self.slack_connector):
                if connector is not None:
                    try:
                        connector.status()  # also enforces the 30-day vault TTL
                    except (KeyError, OSError, RuntimeError, ValueError):
                        pass
        if self.presence.snapshot().state is PresenceState.BLOCKED:
            if refresh_ui:
                generation, _ = self._productivity_dirty_snapshot()
                self.productivityChanged.emit()
                self._publish_productivity_generation(generation)
            return
        due = self.reminders.claim_due(channel="bubble", limit=10)
        if due:
            self._mark_productivity_dirty()
            refresh_ui = True
        for item in due:
            title = str(item.get("title", "提醒"))
            # The durable claim already prevents duplicate scheduling.  Do
            # not record a successful delivery before its user-facing signal:
            # a crash in that gap would otherwise say "delivered" for a
            # reminder the user never had a chance to receive.
            self.reminderDue.emit(title, str(item.get("body", "")))
            self.reminders.mark_delivery(str(item["deliveryId"]), delivered=True)
            self._set_status(f"提醒 · {title}")
        # Growth writes an outbox row in the same SQLite transaction as the
        # immutable ledger.  Only after the UI signal is emitted do we mark it
        # delivered, so a crash leaves a replayable pending record.
        outbox = self.event_outbox.pending(limit=50)
        if outbox:
            self._mark_productivity_dirty()
            refresh_ui = True
        narrative = self.narrative.pending(limit=1)
        if narrative:
            self._mark_productivity_dirty()
            refresh_ui = True
            item = narrative[0]
            self.reminderDue.emit(
                str(item.get("title", "共鸣")), str(item.get("body", ""))
            )
            self.narrative.acknowledge(str(item["narrative_id"]))
        if refresh_ui:
            generation, _ = self._productivity_dirty_snapshot()
            self.productivityChanged.emit()
            self._publish_productivity_generation(generation)
        for event in outbox:
            try:
                self.event_outbox.delivered(str(event["outbox_id"]))
            except Exception as exc:
                self.event_outbox.failed(str(event["outbox_id"]), str(exc))

    def _publish_focus_transition(self, kind: str, value: dict[str, Any]) -> bool:
        """Publish one lifecycle edge without replaying an already-ended row."""

        normalized = str(kind).casefold()
        if normalized not in {
            "started", "paused", "resumed", "completed", "finished", "cancelled"
        }:
            raise ValueError(f"unknown focus transition: {kind}")
        session = dict(value.get("session") or value)
        session_id = str(session.get("session_id", session.get("sessionId", "")))
        elapsed = max(
            0,
            int(session.get("live_active_seconds", session.get("active_seconds", 0)) or 0),
        )
        duration = max(1, int(session.get("planned_seconds", 1500) or 1500))
        if normalized == "started":
            occurred_at = str(session.get("started_at", session.get("created_at", "")))
        elif normalized == "paused":
            occurred_at = str(session.get("paused_at", session.get("updated_at", "")))
        elif normalized == "resumed":
            occurred_at = str(session.get("last_resumed_at", session.get("updated_at", "")))
        else:
            occurred_at = str(session.get("ended_at", session.get("updated_at", "")))
        occurred_at = occurred_at or datetime.now(UTC).isoformat()

        # Repeating an idempotent finish/cancel call must not replay the visual
        # completion.  The persisted ended_at value identifies the same edge.
        previous = self._focus_transition
        if normalized in {"completed", "finished", "cancelled"} and (
            str(previous.get("kind", "")) == normalized
            and str(previous.get("sessionId", "")) == session_id
            and str(previous.get("occurredAt", "")) == occurred_at
        ):
            return False

        self._focus_transition_sequence += 1
        self._focus_transition = {
            "sequence": self._focus_transition_sequence,
            "kind": normalized,
            "sessionId": session_id,
            "elapsedSeconds": elapsed,
            "durationSeconds": duration,
            "occurredAt": occurred_at,
        }
        return True

    def _finish_elapsed_focus_if_due(
        self, active: dict[str, Any] | None = None
    ) -> bool:
        """Finish a due session exactly once before the UI status is emitted."""

        if active is None:
            active = self.focus.status()
        if not active or str(active.get("state", "")) not in {"running", "paused"}:
            return False
        elapsed = int(active.get("live_active_seconds", active.get("active_seconds", 0)) or 0)
        planned = int(active.get("planned_seconds", 0) or 0)
        if planned <= 0 or elapsed < planned:
            return False
        result = self.focus.finish(str(active["session_id"]), outcome="focused")
        self._publish_focus_transition("completed", result)
        self._set_status("专注计时完成")
        return True

    @staticmethod
    def _focus_finish_transition_kind(value: object) -> str:
        if not isinstance(value, dict) or bool(value.get("alreadyFinished")):
            return ""
        session = dict(value.get("session") or value)
        active = max(0, int(session.get("active_seconds", 0) or 0))
        planned = max(0, int(session.get("planned_seconds", 0) or 0))
        return "completed" if planned > 0 and active >= planned else "finished"

    @staticmethod
    def _task_public(value: dict[str, Any]) -> dict[str, Any]:
        priorities = {0: "low", 1: "normal", 2: "high", 3: "critical"}
        task_id = str(value.get("task_id", ""))
        return {
            **dict(value),
            "id": task_id,
            "taskId": task_id,
            "priority": priorities.get(int(value.get("priority", 1)), "normal"),
            "completed": value.get("status") == "completed",
            "dueAt": value.get("due_at") or "",
        }

    @staticmethod
    def _reminder_public(value: dict[str, Any]) -> dict[str, Any]:
        reminder_id = str(value.get("reminder_id", ""))
        return {
            **dict(value),
            "id": reminder_id,
            "reminderId": reminder_id,
            "dueAt": value.get("snoozed_until") or value.get("fire_at") or "",
        }

    def _connector_status(self, provider: str) -> dict[str, Any]:
        connector = self._connector_for(provider)
        if connector is None:
            return {
                "provider": provider,
                "connected": False,
                "state": "secure-storage-unavailable",
                "lastSyncAt": "",
                "error": self._connector_error or "Windows Credential Manager unavailable",
                "policy": {
                    "scope": "必要",
                    "interruption": "安静",
                    "retention": "元数据",
                    "assistance": "协助",
                },
            }
        try:
            value = dict(connector.status())
            runtime_error = self._connector_runtime_errors.get(provider, "")
            if runtime_error:
                value["error"] = runtime_error
            if provider == "slack" and self.slack_socket is not None:
                value["socket"] = self.slack_socket.status
            return value
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            return {
                "provider": provider,
                "connected": False,
                "state": "error",
                "lastSyncAt": "",
                "error": str(exc),
                "policy": connector.axes.to_dict(),
            }

    def _connector_for(self, provider: str) -> CalendarRuntime | SlackRuntime | None:
        if str(provider).casefold() in {"calendar", "google-calendar"}:
            return self.calendar_connector
        if str(provider).casefold() == "slack":
            return self.slack_connector
        return None

    @Slot(bool)
    def setWorkPanelOpen(self, open_value: bool) -> None:
        next_value = bool(open_value)
        if next_value == self._work_panel_open:
            return
        self._work_panel_open = next_value
        self.workPanelOpenChanged.emit()

    @Slot(bool)
    def setBoxWorldSceneOpen(self, open_value: bool) -> None:
        next_value = bool(open_value)
        if next_value == self._box_world_scene_open:
            return
        self._box_world_scene_open = next_value
        self.boxWorldSceneOpenChanged.emit()

    @Slot(str)
    def openWorkPanelSection(self, section_name: str) -> None:
        requested = str(section_name or "work").strip().casefold()
        section = {
            "growth": "growth",
            "wardrobe": "growth",
            "connectors": "connectors",
            "calendar": "connectors",
            "slack": "connectors",
            "world": "world",
            "box-world": "world",
        }.get(requested, "work")
        anchor = {
            "focus": "focus",
            "reading": "reading",
            "wardrobe": "wardrobe",
        }.get(requested, "")
        if section != self._work_panel_section:
            self._work_panel_section = section
            self.workPanelSectionChanged.emit()
        # Visibility is committed before the navigation request.  QML can
        # therefore select and raise the requested page even on the very first
        # open, while repeat requests still re-select a tab the user changed.
        self.setWorkPanelOpen(True)
        self.workPanelNavigationRequested.emit(section)
        if anchor:
            self.workPanelAnchorRequested.emit(anchor)

    @Slot()
    def toggleWorkPanel(self) -> None:
        if self._work_panel_open:
            self.setWorkPanelOpen(False)
        else:
            self.openWorkPanelSection(self._work_panel_section)

    @Slot("QVariantMap")
    def tasksCreate(self, value: object) -> None:
        payload = dict(value) if isinstance(value, dict) else {}
        priority_value = payload.get("priority", 1)
        priorities = {"low": 0, "normal": 1, "high": 2, "critical": 3}
        priority = priorities.get(str(priority_value), priority_value)
        try:
            self.tasks.create(
                str(payload.get("title", "")),
                note=str(payload.get("note", "")),
                category=str(payload.get("category", "inbox")),
                priority=int(priority),
                due_at=payload.get("dueAt"),
                timezone_name=str(payload.get("timezone", "UTC")),
                recurrence=payload.get("recurrence"),
            )
            self._set_status("任务已放入收件箱")
        except (KeyError, TypeError, ValueError) as exc:
            self._set_status(f"任务没有创建：{exc}")
        self.refreshProductivity()

    @Slot(str)
    def tasksComplete(self, task_id: str) -> None:
        try:
            result = self.tasks.complete(task_id)
            points = int((result.get("growth") or {}).get("points", 0))
            self._set_status(f"任务完成 · 共鸣 +{points}" if points else "任务已经完成")
        except (KeyError, ValueError) as exc:
            self._set_status(f"任务没有完成：{exc}")
        self.refreshProductivity()

    @Slot(int)
    def focusStart(self, minutes: int = 25) -> None:
        try:
            value = self.focus.start(minutes=int(minutes))
            self._publish_focus_transition("started", value)
            self._set_status(f"开始 {int(minutes)} 分钟专注")
        except (RuntimeError, ValueError) as exc:
            self._set_status(str(exc))
        self.refreshProductivity()

    @Slot()
    def focusPause(self) -> None:
        value = self.focus.status()
        if value:
            paused = self.focus.pause(str(value["session_id"]))
            self._publish_focus_transition("paused", paused)
        self.refreshProductivity()

    @Slot()
    def focusResume(self) -> None:
        value = self.focus.status()
        if value:
            resumed = self.focus.resume(str(value["session_id"]))
            self._publish_focus_transition("resumed", resumed)
        self.refreshProductivity()

    @Slot()
    def focusFinish(self) -> None:
        # The one-second UI timer and a click can become ready in the same
        # event-loop turn.  Once the planned deadline has passed, natural
        # completion owns that edge and must not be relabelled as a manual end.
        if self._finish_elapsed_focus_if_due():
            self.refreshProductivity()
            return
        value = self.focus.status()
        if value:
            result = self.focus.finish(str(value["session_id"]))
            transition_kind = self._focus_finish_transition_kind(result)
            if transition_kind:
                self._publish_focus_transition(transition_kind, result)
            self._set_status("专注已手动结束")
        self.refreshProductivity()

    @Slot()
    def focusCancel(self) -> None:
        # Natural completion owns the exact-deadline edge, just as it does
        # for ``focusFinish``.  Otherwise a cancel click can race the 1 s UI
        # tick and relabel an already elapsed session as cancelled.
        if self._finish_elapsed_focus_if_due():
            self.refreshProductivity()
            return
        value = self.focus.status()
        if value:
            cancelled = self.focus.cancel(str(value["session_id"]))
            self._publish_focus_transition("cancelled", cancelled)
            self._set_status("专注已取消；不会扣除共鸣")
        self.refreshProductivity()

    @Slot(str)
    @Slot(str, str)
    def focusDiversionAction(self, action: str, session_id: str = "") -> None:
        normalized = str(action).casefold()
        requested_session_id = str(session_id or "").strip()
        bubble_session_id = str(
            self._focus_diversion_bubble.get("sessionId", "") or ""
        ).strip()
        active = self.focus.status()
        active_session_id = str((active or {}).get("session_id", "") or "").strip()

        # Every bubble gesture is scoped to the session that created it.  A
        # retained native window can deliver a late click after that session
        # ended and another one started; such a click must neither terminate
        # the new clock nor return to the previous session's work window.  The
        # one-argument slot remains callable for compatibility, but without a
        # session identity it has no authority to act or dismiss a newer card.
        if (
            not requested_session_id
            or requested_session_id != bubble_session_id
            or requested_session_id != active_session_id
        ):
            if requested_session_id and requested_session_id == bubble_session_id:
                self._focus_diversion_bubble = {}
                self.focusDiversionChanged.emit()
            self._set_status("这条专注提醒已经失效")
            return

        # The one-second productivity timer can become ready in the same
        # event-loop turn as a bubble click.  Natural completion owns that
        # edge, exactly as it does for the main finish/cancel controls.  Once
        # settled, discard the now-ended bubble without replaying the action.
        if self._finish_elapsed_focus_if_due():
            if str(self._focus_diversion_bubble.get("sessionId", "")) == requested_session_id:
                self._focus_diversion_bubble = {}
                self.focusDiversionChanged.emit()
            self.refreshProductivity()
            return

        active = self.focus.status()
        if str((active or {}).get("session_id", "") or "") != requested_session_id:
            if str(self._focus_diversion_bubble.get("sessionId", "")) == requested_session_id:
                self._focus_diversion_bubble = {}
                self.focusDiversionChanged.emit()
            self._set_status("这条专注提醒已经失效")
            return
        try:
            self.focus_diversion.acknowledge(normalized)
            if normalized == "return" and self._last_work_window_handle:
                self.window_catalog.activate(self._last_work_window_handle)
                self._set_status("回到刚才的工作")
            elif normalized == "rest" and active:
                result = self.focus.finish(str(active["session_id"]), outcome="rest")
                self._publish_focus_transition("finished", result)
                self._set_status("这段专注已按主动休息结束；不会扣除共鸣")
            elif normalized == "finish" and active:
                result = self.focus.finish(str(active["session_id"]), outcome="focused")
                transition_kind = self._focus_finish_transition_kind(result)
                if transition_kind:
                    self._publish_focus_transition(transition_kind, result)
                self._set_status("专注已结束")
            elif normalized == "dismiss":
                self._set_status("本次提醒已收起")
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            self._set_status(f"专注状态没有改变：{exc}")
        if str(self._focus_diversion_bubble.get("sessionId", "")) == requested_session_id:
            self._focus_diversion_bubble = {}
            self.focusDiversionChanged.emit()
        self.refreshProductivity()

    @Slot()
    def readingStart(self) -> None:
        try:
            self.reading_sessions.start(title="论文阅读")
            self._set_status("论文阅读计时已开始")
        except RuntimeError as exc:
            self._set_status(str(exc))
        self.refreshProductivity()

    @Slot()
    def readingFinish(self) -> None:
        value = self.reading_sessions.status()
        if value:
            self.reading_sessions.finish(str(value["session_id"]))
            self._set_status("论文阅读会话已完成")
        self.refreshProductivity()

    @Slot("QVariantMap")
    def remindersCreate(self, value: object) -> None:
        payload = dict(value) if isinstance(value, dict) else {}
        try:
            self.reminders.create(
                str(payload.get("title", "")),
                str(payload.get("dueAt", payload.get("fireAt", ""))),
                body=str(payload.get("body", "")),
                timezone_name=str(payload.get("timezone", "UTC")),
            )
            self._set_status("提醒已创建")
        except (KeyError, TypeError, ValueError) as exc:
            self._set_status(f"提醒没有创建：{exc}")
        self.refreshProductivity()

    @Slot(str, int)
    def remindersSnooze(self, reminder_id: str, minutes: int = 10) -> None:
        self.reminders.snooze(reminder_id, minutes)
        self.refreshProductivity()

    @Slot(str)
    def remindersDismiss(self, reminder_id: str) -> None:
        self.reminders.dismiss(reminder_id)
        self.refreshProductivity()

    @Slot(str)
    def wardrobeEquip(self, outfit_id: str) -> None:
        try:
            self.wardrobe.equip(outfit_id=outfit_id)
            self._set_status("莉莉丝换好了衣服")
        except (KeyError, PermissionError, ValueError) as exc:
            self._set_status(str(exc))
        self.refreshProductivity()

    @Slot(str)
    def wardrobeEquipPose(self, pose_id: str) -> None:
        try:
            self.wardrobe.equip(pose_id=pose_id)
        except (KeyError, PermissionError, ValueError) as exc:
            self._set_status(str(exc))
        self.refreshProductivity()

    @Slot()
    def enterBoxWorld(self) -> None:
        self.box_world.enter()
        self._presentBoxWorld()

    @Slot(str)
    def boxWorldPlace(self, object_id: str) -> None:
        try:
            value = self.box_world.place(str(object_id))
            name = str(value.get("display_name", object_id))
            self._set_status(f"{name}已摆入盒中世界")
        except (KeyError, PermissionError, ValueError) as exc:
            self._set_status(f"陈设没有改变：{exc}")
        self.refreshProductivity()

    @Slot()
    def _presentBoxWorld(self) -> None:
        was_already_open = self._box_world_scene_open
        self.setBoxWorldSceneOpen(True)
        # A minimized scene is still logically open, so assigning ``True``
        # again does not emit the property change signal.  Keep presentation
        # as a distinct event so every explicit entry request can restore and
        # raise the existing scene without manufacturing a false state change.
        if was_already_open:
            self.boxWorldPresentationRequested.emit()
        self._set_status("已进入盒中世界；成长陈设会在这里留下来")
        self.refreshProductivity()

    def _componentEnterBoxWorld(self) -> dict[str, Any]:
        result = self.box_world.enter()
        # The function library can call this either from QML/the Qt thread or
        # from the model/socket worker.  Queue only the cross-thread case.
        # Always queuing the local case left a visible dead interval under a
        # busy render loop, so a rapid close -> component-enter sequence could
        # look as if the button did nothing until the next event batch.
        if QThread.currentThread() == self.thread():
            self._presentBoxWorld()
        else:
            self.boxWorldEntryRequested.emit()
        return result

    @Slot()
    def calendarOpenSetup(self) -> None:
        self._set_status("Calendar 连接使用系统浏览器；凭据只会进入 Windows 凭据管理器")

    @Slot()
    def slackOpenSetup(self) -> None:
        self._set_status("Slack 使用你的个人 custom app 与 Socket Mode；不会建立 Lilies 云端服务器")

    @Slot(str, str, result=bool)
    def connectorSelectItem(self, provider: str, event_id: str) -> bool:
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        connector = self._connector_for(normalized)
        remote_id = str(event_id).strip()
        if connector is None or normalized not in {"calendar", "slack"} or not remote_id:
            return False
        row = connector.connection.execute(
            """SELECT 1 FROM connector_external_items
               WHERE connector_id=? AND account_id=? AND remote_id=?""",
            (connector.connector_id, connector.account_id, remote_id),
        ).fetchone()
        if row is None:
            self._set_status("这项外部内容已不在当前本地缓存中")
            return False
        if (
            self._connector_assist_result.get("busy")
            and self._connector_selected_item != {"provider": normalized, "id": remote_id}
        ):
            self.connector_assist.cancel()
        self._connector_selected_item = {"provider": normalized, "id": remote_id}
        if (
            self._connector_assist_result.get("provider") != normalized
            or self._connector_assist_result.get("eventId") != remote_id
        ):
            self._connector_assist_result = {
                "provider": normalized, "eventId": remote_id, "instruction": "",
                "text": "", "busy": False, "error": "",
            }
            self.connectorAssistChanged.emit()
        self.connectorStatusChanged.emit()
        return True

    @Slot(str, str, str, result=bool)
    def connectorAssist(self, provider: str, event_id: str, instruction: str) -> bool:
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        selected = self._connector_selected_item
        if selected.get("provider") != normalized or selected.get("id") != str(event_id):
            self._set_status("请先在日程或信笺列表中明确点选当前单项")
            return False
        connector = self._connector_for(normalized)
        if connector is None:
            return False
        self._connector_assist_result = {
            "provider": normalized,
            "eventId": str(event_id),
            "instruction": str(instruction),
            "text": "",
            "busy": True,
            "error": "",
        }
        self.connectorAssistChanged.emit()
        try:
            started = self.connector_assist.request(
                normalized, connector, str(event_id), str(instruction)
            )
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            self._connector_assist_result = {
                **self._connector_assist_result,
                "busy": False,
                "error": str(exc),
            }
            self.connectorAssistChanged.emit()
            self._set_status(f"当前单项没有交给模型：{exc}")
            return False
        if not started:
            self._connector_assist_result = {
                **self._connector_assist_result,
                "busy": False,
                "error": "另一个连接器协助正在进行",
            }
            self.connectorAssistChanged.emit()
            return False
        self._set_status("只把你点选的这一项交给 Luna；不会附带长期记忆或邻近内容")
        return True

    def _on_connector_assist_busy(self, busy: bool) -> None:
        if bool(self._connector_assist_result.get("busy")) != bool(busy):
            self._connector_assist_result = {
                **self._connector_assist_result,
                "busy": bool(busy),
            }
            self.connectorAssistChanged.emit()

    def _on_connector_assist_result(self, value: object) -> None:
        payload = dict(value) if isinstance(value, dict) else {}
        if self._connector_selected_item != {
            "provider": str(payload.get("provider", "")),
            "id": str(payload.get("eventId", "")),
        }:
            # The user selected another item, cleared its content or disconnected
            # while the model was working.  A stale result must not reappear.
            return
        self._connector_assist_result = {
            "provider": str(payload.get("provider", "")),
            "eventId": str(payload.get("eventId", "")),
            "instruction": str(payload.get("instruction", "")),
            "text": str(payload.get("text", "")),
            "busy": False,
            "error": str(payload.get("error", "")),
        }
        self.connectorAssistChanged.emit()
        if self._connector_assist_result["error"]:
            self._set_status(f"连接器协助没有完成：{self._connector_assist_result['error']}")
        else:
            self._set_status("莉莉丝只处理了当前点选的一项；结果尚未发送或写入")

    @staticmethod
    def _calendar_change(value: object, *, creating: bool) -> dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        title = str(payload.get("title", "")).strip()
        start_text = str(payload.get("start", "")).strip()
        end_text = str(payload.get("end", "")).strip()
        timezone_name = str(payload.get("timeZone", "")).strip()[:120]
        if timezone_name:
            try:
                zone = ZoneInfo(timezone_name)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("Calendar 时区必须是有效的 IANA 名称") from exc
        else:
            zone = datetime.now().astimezone().tzinfo or UTC

        def temporal(raw: str, field: str) -> tuple[dict[str, str], datetime]:
            if not raw:
                raise ValueError(f"Calendar {field} 不能为空")
            if len(raw) == 10:
                day = date.fromisoformat(raw)
                return {"date": raw}, datetime.combine(day, datetime.min.time(), tzinfo=zone)
            normalized = f"{raw[:-1]}+00:00" if raw.endswith("Z") else raw
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=zone)
            part = {"dateTime": raw}
            if timezone_name:
                part["timeZone"] = timezone_name
            return part, parsed

        result: dict[str, Any] = {}
        if title:
            result["summary"] = title[:1024]
        if creating and not title:
            raise ValueError("Calendar 标题不能为空")
        start_part = end_part = None
        start_value = end_value = None
        if start_text or creating:
            start_part, start_value = temporal(start_text, "开始时间")
            result["start"] = start_part
        if end_text or creating:
            end_part, end_value = temporal(end_text, "结束时间")
            result["end"] = end_part
        if (start_text and not end_text) or (end_text and not start_text):
            raise ValueError("修改时间时必须同时提供开始与结束")
        if start_value is not None and end_value is not None:
            if end_value.astimezone(UTC) <= start_value.astimezone(UTC):
                raise ValueError("Calendar 结束时间必须晚于开始时间")
            if bool(start_part and start_part.get("date")) != bool(end_part and end_part.get("date")):
                raise ValueError("全天日程的开始与结束必须都使用日期")
        if payload.get("reminderMinutes") not in (None, ""):
            minutes = int(payload["reminderMinutes"])
            if not 0 <= minutes <= 40_320:
                raise ValueError("Calendar 提醒分钟必须在 0 到 40320 之间")
            result["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": minutes}],
            }
        if not result:
            raise ValueError("没有可预览的 Calendar 修改")
        return result

    @Slot("QVariantMap", result="QVariantMap")
    def calendarProposeCreate(self, value: object) -> dict[str, Any]:
        if self.calendar_connector is None:
            return {"ok": False, "error": "Calendar 连接器不可用"}
        try:
            proposal = self.calendar_connector.propose_create(
                self._calendar_change(value, creating=True)
            )
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            self._set_status(f"Calendar 预览没有生成：{exc}")
            return {"ok": False, "error": str(exc)}
        self._connector_proposal = dict(proposal)
        self.connectorStatusChanged.emit()
        self._set_status("Calendar 创建预览已生成；未确认前不会写入")
        return {"ok": True, "proposal": proposal}

    @Slot(str, "QVariantMap", result="QVariantMap")
    def calendarProposeUpdate(self, event_id: str, value: object) -> dict[str, Any]:
        if self.calendar_connector is None:
            return {"ok": False, "error": "Calendar 连接器不可用"}
        if self._connector_selected_item != {"provider": "calendar", "id": str(event_id)}:
            return {"ok": False, "error": "请先明确点选要修改的日程"}
        try:
            proposal = self.calendar_connector.propose_update(
                str(event_id), self._calendar_change(value, creating=False)
            )
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            self._set_status(f"Calendar 修改预览没有生成：{exc}")
            return {"ok": False, "error": str(exc)}
        self._connector_proposal = dict(proposal)
        self.connectorStatusChanged.emit()
        self._set_status("Calendar 修改前后差异已生成；未确认前不会写入")
        return {"ok": True, "proposal": proposal}

    @Slot(str, str, result="QVariantMap")
    def slackProposeReply(self, event_id: str, text: str) -> dict[str, Any]:
        if self.slack_connector is None:
            return {"ok": False, "error": "Slack 连接器不可用"}
        if self._connector_selected_item != {"provider": "slack", "id": str(event_id)}:
            return {"ok": False, "error": "请先明确点选要回复的信笺"}
        try:
            proposal = self.slack_connector.propose_reply(str(event_id), str(text).strip())
        except (KeyError, OSError, PermissionError, RuntimeError, TypeError, ValueError) as exc:
            self._set_status(f"Slack 回复预览没有生成：{exc}")
            return {"ok": False, "error": str(exc)}
        self._connector_proposal = dict(proposal)
        self.connectorStatusChanged.emit()
        self._set_status("Slack 最终回复预览已生成；未确认前不会发送")
        return {"ok": True, "proposal": proposal}

    @Slot(str, result=bool)
    def connectorClearContent(self, provider: str) -> bool:
        connector = self._connector_for(provider)
        if connector is None:
            return False
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        if normalized in self._connector_confirm_inflight:
            self._set_status("外部写入正在提交，完成前不能清除本地提案")
            return False
        if self._connector_assist_result.get("provider") == normalized:
            self.connector_assist.cancel()
        try:
            connector.clear_cached_content(keep_metadata=True)
            if normalized == "calendar":
                self.calendar_reminder_bridge.clear()
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status(f"本地内容没有清除：{exc}")
            return False
        self._connector_proposal = {}
        if self._connector_selected_item.get("provider") == normalized:
            self._connector_selected_item = {"provider": "", "id": ""}
        self._connector_assist_result = {
            "provider": "", "eventId": "", "instruction": "", "text": "",
            "busy": False, "error": "",
        }
        self.connectorStatusChanged.emit()
        self.connectorAssistChanged.emit()
        self._set_status("连接器正文、摘要与待确认预览已从本地清除；账号凭据仍保留")
        if normalized == "calendar":
            self.refreshProductivity()
        return True

    @Slot(str, result=bool)
    def connectorDisconnect(self, provider: str) -> bool:
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        connector = self._connector_for(normalized)
        if connector is None:
            return False
        if normalized in self._connector_confirm_inflight:
            self._set_status("外部写入正在提交，完成前不能断开账号")
            return False
        if self._connector_assist_result.get("provider") == normalized:
            self.connector_assist.cancel()
        try:
            if normalized == "slack" and self.slack_socket is not None:
                self.slack_socket.stop()
            if normalized == "calendar":
                self._calendar_sync_timer.stop()
            connector.disconnect()
            if normalized == "calendar":
                self.calendar_reminder_bridge.clear()
        except (OSError, RuntimeError, ValueError) as exc:
            self._set_status(f"账号没有断开：{exc}")
            return False
        if self._connector_selected_item.get("provider") == normalized:
            self._connector_selected_item = {"provider": "", "id": ""}
        self._connector_proposal = {}
        if self._connector_assist_result.get("provider") == normalized:
            self._connector_assist_result = {
                "provider": "", "eventId": "", "instruction": "", "text": "",
                "busy": False, "error": "",
            }
            self.connectorAssistChanged.emit()
        self.connectorStatusChanged.emit()
        self._set_status("账号凭据和本地内容已清除；连接器配置仍保留，方便以后重连")
        if normalized == "calendar":
            self.refreshProductivity()
        return True

    @Slot(str, "QVariantMap", result="QVariantMap")
    def connectorConfigure(self, provider: str, value: object) -> dict[str, Any]:
        connector = self._connector_for(provider)
        if connector is None:
            message = self._connector_error or "安全凭据存储不可用"
            self._set_status(message)
            return {"ok": False, "error": message}
        payload = dict(value) if isinstance(value, dict) else {}
        try:
            result = connector.configure(payload)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._set_status(f"连接配置没有保存：{exc}")
            return {"ok": False, "error": str(exc)}
        self._connector_runtime_errors[
            "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        ] = ""
        self.connectorStatusChanged.emit()
        self._set_status("连接配置已安全保存；尚未授权前不会访问外部服务")
        return {"ok": True, "status": result}

    @Slot(str, result=bool)
    def connectorBeginOAuth(self, provider: str) -> bool:
        if self._preview_mode:
            self._set_status("离屏预览不会启动浏览器授权")
            return False
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        connector = self._connector_for(normalized)
        if connector is None or normalized not in {"calendar", "slack"}:
            self._set_status(self._connector_error or "连接器不可用")
            return False
        if normalized in self._oauth_receivers:
            self._set_status("这个连接器正在等待浏览器授权")
            return False
        receiver: LoopbackOAuthReceiver | None = None
        try:
            if normalized == "calendar":
                state = secrets.token_urlsafe(32)
                receiver = LoopbackOAuthReceiver(expected_state=state)
                authorization = connector.authorization(receiver.redirect_uri, state=state)
            else:
                authorization = connector.authorization()
                redirect = urlparse(authorization.redirect_uri)
                if (
                    redirect.scheme != "http"
                    or redirect.hostname != "127.0.0.1"
                    or not redirect.port
                ):
                    raise ValueError("Slack Redirect URI 必须是已注册的 127.0.0.1 loopback 地址")
                receiver = LoopbackOAuthReceiver(
                    expected_state=authorization.state,
                    port=int(redirect.port),
                    callback_path=redirect.path or "/oauth/callback",
                )
            receiver.start()
            if not QDesktopServices.openUrl(QUrl(authorization.authorization_url)):
                raise OSError("系统浏览器没有接受授权链接")
            self._oauth_receivers[normalized] = receiver
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if receiver is not None:
                receiver.close()
            self._set_status(f"无法开始授权：{exc}")
            return False

        def exchange_in_background() -> None:
            try:
                callback = receiver.wait(timeout=180.0)
                if callback.error or not callback.code:
                    raise PermissionError(callback.error or "授权未完成")
                connector.exchange(callback.code, authorization)
                self.connectorOAuthFinished.emit(normalized, True, "授权已完成")
            except Exception as exc:  # worker boundary; message is returned without credentials
                self.connectorOAuthFinished.emit(normalized, False, str(exc))
            finally:
                receiver.close()

        thread = threading.Thread(
            target=exchange_in_background,
            name=f"lilies-{normalized}-oauth",
            daemon=True,
        )
        self._connector_threads.add(thread)
        thread.start()
        self._set_status("已在系统浏览器中打开授权页")
        return True

    def _on_connector_oauth_finished(self, provider: str, ok: bool, message: str) -> None:
        self._oauth_receivers.pop(provider, None)
        self._connector_threads = {
            thread for thread in self._connector_threads if thread.is_alive()
        }
        self._connector_runtime_errors[provider] = "" if ok else str(message)
        self._set_status(message if ok else f"授权没有完成：{message}")
        if ok and provider == "slack" and self.slack_socket is not None:
            status = self.slack_connector.status() if self.slack_connector else {}
            if status.get("socketReady"):
                self.slack_socket.start()
        elif ok and provider == "calendar":
            QTimer.singleShot(0, self.calendarRefresh)
        self.connectorStatusChanged.emit()

    def _on_slack_socket_status(self, _status: object) -> None:
        self.connectorStatusChanged.emit()

    def _on_slack_item(self, item: object) -> None:
        value = dict(item) if isinstance(item, dict) else {}
        policy = self.slack_connector.axes if self.slack_connector is not None else None
        if (
            policy is not None
            and policy.interruption.value in {"priority", "immediate"}
            and self.presence.snapshot().state is PresenceState.NORMAL
        ):
            label = "Slack 私信" if value.get("isDirect") else "Slack 提及或精选频道"
            self.reminderDue.emit("莉莉丝 · Slack", f"收到一封新的{label}信笺。")
        self.connectorStatusChanged.emit()

    @Slot(str, str, result=bool)
    def connectorConfirmProposal(self, provider: str, proposal_id: str) -> bool:
        connector = self._connector_for(provider)
        current_id = str(
            self._connector_proposal.get("id", self._connector_proposal.get("proposalId", ""))
        )
        current_provider = str(self._connector_proposal.get("connector", ""))
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        if (
            connector is None
            or not proposal_id
            or proposal_id != current_id
            or current_provider != normalized
        ):
            self._set_status("提案与当前连接器不匹配，未执行")
            return False
        if normalized in self._connector_confirm_inflight:
            self._set_status("这项提案已经在提交，请勿重复确认")
            return False

        self._connector_confirm_inflight[normalized] = proposal_id
        self.connectorStatusChanged.emit()

        def execute_in_background() -> None:
            try:
                result = connector.confirm_and_execute(proposal_id)
                self.connectorOperationFinished.emit(normalized, "confirm", True, result)
            except Exception as exc:  # network/policy/ETag boundary
                self.connectorOperationFinished.emit(normalized, "confirm", False, str(exc))

        thread = threading.Thread(
            target=execute_in_background,
            name=f"lilies-{normalized}-confirmed-action",
            daemon=True,
        )
        self._connector_threads.add(thread)
        try:
            thread.start()
        except Exception:
            self._connector_confirm_inflight.pop(normalized, None)
            self.connectorStatusChanged.emit()
            raise
        self._set_status("正在严格按预览内容提交；窗口可以继续使用")
        return True

    @Slot(str, str, result=bool)
    def connectorRejectProposal(self, provider: str, proposal_id: str) -> bool:
        connector = self._connector_for(provider)
        current_id = str(
            self._connector_proposal.get("id", self._connector_proposal.get("proposalId", ""))
        )
        normalized = "calendar" if provider in {"calendar", "google-calendar"} else str(provider)
        if normalized in self._connector_confirm_inflight:
            self._set_status("提案正在提交，不能同时撤销")
            return False
        if connector is None or proposal_id != current_id:
            return False
        try:
            connector.reject(proposal_id)
        except (KeyError, RuntimeError, ValueError) as exc:
            self._set_status(f"提案没有撤销：{exc}")
            return False
        self._connector_proposal = {}
        self.connectorStatusChanged.emit()
        self._set_status("提案已拒绝，没有写入外部服务")
        return True

    @Slot(str, str, result="QVariantMap")
    def connectorReplaceSlackProposal(self, proposal_id: str, text: str) -> dict[str, Any]:
        if self.slack_connector is None:
            return {"ok": False, "error": "Slack 连接器不可用"}
        if "slack" in self._connector_confirm_inflight:
            return {"ok": False, "error": "Slack 提案正在提交，不能同时替换"}
        current_id = str(
            self._connector_proposal.get("id", self._connector_proposal.get("proposalId", ""))
        )
        if proposal_id != current_id or not str(text).strip():
            return {"ok": False, "error": "最终正文为空或提案已经变化"}
        try:
            replacement = self.slack_connector.replace_reply_proposal(proposal_id, text)
        except (KeyError, RuntimeError, ValueError) as exc:
            self._set_status(f"最终预览没有更新：{exc}")
            return {"ok": False, "error": str(exc)}
        self._connector_proposal = dict(replacement)
        self.connectorStatusChanged.emit()
        self._set_status("已生成新的不可变最终预览；旧提案已作废")
        return {"ok": True, "proposal": replacement}

    def _on_connector_operation_finished(
        self, provider: str, action: str, ok: bool, payload: object
    ) -> None:
        if action == "confirm":
            self._connector_confirm_inflight.pop(provider, None)
        self._connector_threads = {
            thread for thread in self._connector_threads if thread.is_alive()
        }
        if ok and action == "confirm":
            self._connector_runtime_errors[provider] = ""
            if provider == "slack" and isinstance(payload, dict):
                proposal_id = str(payload.get("id", payload.get("proposalId", "")))
                if proposal_id:
                    self.growth.record_slack_cleanup(
                        proposal_id, handled_count=0, reply_sent=True
                    )
            self._connector_proposal = {}
            self._set_status("确认内容已经提交")
            self.productivityChanged.emit()
            if provider == "calendar":
                QTimer.singleShot(0, self.calendarRefresh)
        elif ok and action == "refresh":
            self._calendar_refresh_running = False
            self._connector_runtime_errors[provider] = ""
            if self.calendar_connector is not None:
                try:
                    self.calendar_reminder_bridge.reconcile(
                        self.calendar_connector.upcoming(limit=100)
                    )
                except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    self._connector_runtime_errors[provider] = f"本地提醒没有更新：{exc}"
            self._set_status("Calendar 已刷新")
            # Reconciliation creates, replaces or removes local reminders.
            # With idle invalidations suppressed, publish that durable change
            # here instead of waiting for an unrelated productivity event.
            self.refreshProductivity()
        else:
            if action == "refresh":
                self._calendar_refresh_running = False
            self._connector_runtime_errors[provider] = str(payload)
            self._set_status(f"连接器操作没有完成：{payload}")
        if provider == "calendar" and action == "refresh":
            self._schedule_calendar_refresh()
        self.connectorStatusChanged.emit()

    def _schedule_calendar_refresh(self) -> None:
        if self._preview_mode or self.calendar_connector is None:
            return
        try:
            connected = bool(self.calendar_connector.status().get("connected"))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            self._connector_runtime_errors["calendar"] = (
                "calendar-status-unavailable"
            )
            self._calendar_sync_timer.start(60_000)
            self.connectorStatusChanged.emit()
            return
        if not connected:
            self._calendar_sync_timer.stop()
            return
        # Google installed apps do not rely on a local push endpoint.  A
        # jittered 10–15 minute timer avoids synchronized polling bursts.
        self._calendar_sync_timer.start(600_000 + secrets.randbelow(300_001))

    @Slot()
    def calendarRefresh(self) -> None:
        connector = self.calendar_connector
        if connector is None:
            self._set_status("Calendar 尚未连接；未发送任何网络请求")
            return
        try:
            connected = bool(connector.status().get("connected"))
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            self._connector_runtime_errors["calendar"] = (
                "calendar-status-unavailable"
            )
            self._calendar_sync_timer.start(60_000)
            self._set_status("Calendar 状态暂时不可用；一分钟后自动重试")
            self.connectorStatusChanged.emit()
            return
        if not connected:
            self._set_status("Calendar 尚未连接；未发送任何网络请求")
            return
        if self._calendar_refresh_running:
            return
        self._calendar_refresh_running = True
        self._calendar_sync_timer.stop()

        def refresh_in_background() -> None:
            try:
                result = connector.refresh()
                self.connectorOperationFinished.emit("calendar", "refresh", True, result)
            except Exception as exc:
                self.connectorOperationFinished.emit("calendar", "refresh", False, str(exc))

        thread = threading.Thread(
            target=refresh_in_background,
            name="lilies-calendar-refresh",
            daemon=True,
        )
        self._connector_threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            self._connector_threads.discard(thread)
            self._calendar_refresh_running = False
            self._connector_runtime_errors["calendar"] = (
                "calendar-worker-start-failed"
            )
            self._calendar_sync_timer.start(60_000)
            self._set_status("Calendar 后台刷新暂时无法启动；一分钟后自动重试")
            self.connectorStatusChanged.emit()
            return
        self._set_status("Calendar 正在后台刷新")

    @Slot(result=bool)
    def slackOpenInbox(self) -> bool:
        if self.slack_connector is None or not self.slack_connector.status().get("connected"):
            self._set_status("Slack 尚未连接")
            return False
        self.openWorkPanelSection("connectors")
        # Selecting the already-open connectors page is not a visible action.
        # Give the panel an explicit destination so the button scrolls to and
        # focuses the local inbox even when the same page is already active.
        self.workPanelAnchorRequested.emit("slack-inbox")
        self._set_status("Slack 信笺匣已展开")
        return True

    @Slot(str)
    def calendarOpenEvent(self, event_id: str) -> None:
        if self.calendar_connector is None:
            return
        try:
            target = self.calendar_connector.open_event(event_id)
            url = str(target.get("url", ""))
            if not url or not QDesktopServices.openUrl(QUrl(url)):
                raise OSError("系统浏览器没有打开日程")
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            self._set_status(f"日程没有打开：{exc}")

    @Slot(str)
    def slackOpenMessage(self, event_id: str) -> None:
        if self.slack_connector is None:
            return
        try:
            item = self.slack_connector.open_message(event_id)
            url = str(item.get("link", ""))
            if not url or not QDesktopServices.openUrl(QUrl(url)):
                raise OSError("Slack 没有接受消息链接")
        except (KeyError, OSError, RuntimeError, ValueError) as exc:
            self._set_status(f"Slack 消息没有打开：{exc}")

    @Slot(float, float)
    def detachPetHabitat(self, x: float, y: float) -> None:
        self.pet_habitat.set_desktop_position(float(x), float(y))
        self.pet_habitat.detach()
        self._habitat_status = self.pet_habitat.status()
        self.habitatChanged.emit()

    @Slot()
    def refreshSystemStatus(self) -> None:
        self._system_status = system_status()
        self.systemStatusChanged.emit()

    def set_desktop_window_handle(self, window_handle: int) -> None:
        self._desktop_window_handle = int(window_handle)

    @Slot()
    def refreshSceneActivity(self) -> None:
        # Smoke/offscreen runs must not inspect the person's foreground window.
        # Compact mode has no full desktop scene to throttle, and its character
        # animation must not inherit a stale ``False`` value from a previously
        # covered visual desktop.
        active = (
            self._preview_mode
            or self.shell.mode == "compact"
            or not window_fully_occluded(self._desktop_window_handle)
        )
        if active != self._scene_active:
            self._scene_active = active
            self.sceneActiveChanged.emit()

    @Slot(float)
    def reportFrameRate(self, value: float) -> None:
        clean = round(max(0.0, min(240.0, float(value))), 1)
        if clean != self._frame_rate:
            self._frame_rate = clean
            self.frameRateChanged.emit()

    @Slot()
    def _refreshRuntimeSnapshotIdentity(self) -> None:
        """Refresh the lock-protected primitives exposed to the socket worker."""

        shell_mode = str(getattr(self.shell, "mode", "visual"))
        if shell_mode not in {"visual", "login", "compact"}:
            shell_mode = "visual"
        renderer = str(getattr(self, "_renderer", self.theme.default_renderer))
        if renderer not in self.theme.renderers:
            renderer = self.theme.default_renderer
        active = bool(getattr(self, "_scene_active", True))
        delivery = self.companion.deliveryStatus
        companion = {
            "enabled": bool(self.companion._activity_enabled),
            "paused": bool(self.companion.activity.paused),
            "presentationReady": bool(delivery.get("presentationReady", False)),
            "suppressed": bool(delivery.get("suppressed", False)),
            "busy": bool(self.companion.busy),
            "ackPending": bool(delivery.get("ackPending", False)),
            "hasBubble": bool(delivery.get("hasBubble", False)),
            "unreadCount": max(0, min(int(delivery.get("unreadCount", 0) or 0), 1)),
            "state": str(delivery.get("state", "idle")),
            "reason": str(delivery.get("reason", "")),
            "expiresInSeconds": max(
                0.0, float(delivery.get("expiresInSeconds", 0.0) or 0.0)
            ),
        }
        with self._runtime_snapshot_lock:
            self._runtime_snapshot_state["shellMode"] = shell_mode
            self._runtime_snapshot_state["renderer"] = renderer
            scene = dict(self._runtime_snapshot_state["scene"])
            scene["active"] = active
            self._runtime_snapshot_state["scene"] = scene
            self._runtime_snapshot_state["companion"] = companion

    @Slot(bool, bool, str)
    def reportSceneRuntimeState(
        self,
        scene2d_loaded: bool,
        video_loaded: bool,
        video_playback_state: str,
    ) -> None:
        """Accept a fixed QML loader projection on the Qt main thread."""

        playback = str(video_playback_state or "").strip().casefold()
        if playback not in _RUNTIME_VIDEO_PLAYBACK_STATES:
            playback = "unknown"
        with self._runtime_snapshot_lock:
            scene = dict(self._runtime_snapshot_state["scene"])
            scene.update(
                {
                    "scene2dLoaded": bool(scene2d_loaded),
                    "videoLoaded": bool(video_loaded),
                    "videoPlaybackState": playback,
                }
            )
            self._runtime_snapshot_state["scene"] = scene

    @Slot()
    def _recordRuntimeHeartbeat(self) -> None:
        """Record one content-free pulse from the Qt object's own thread."""

        with self._runtime_snapshot_lock:
            self._runtime_snapshot_state["qtHeartbeatMonotonic"] = time.monotonic()

    @Slot(result="QVariantMap")
    def runtimeSnapshot(self) -> dict[str, Any]:
        """Return a defensive copy of the fixed, non-content runtime schema."""

        with self._runtime_snapshot_lock:
            scene = dict(self._runtime_snapshot_state["scene"])
            companion = dict(self._runtime_snapshot_state["companion"])
            heartbeat_age_ms = round(
                max(
                    0.0,
                    min(
                        float(RUNTIME_QT_HEARTBEAT_MAX_AGE_MS),
                        (
                            time.monotonic()
                            - float(
                                self._runtime_snapshot_state[
                                    "qtHeartbeatMonotonic"
                                ]
                            )
                        )
                        * 1000.0,
                    ),
                )
            )
            return {
                "schemaVersion": 1,
                "shellMode": str(self._runtime_snapshot_state["shellMode"]),
                "renderer": str(self._runtime_snapshot_state["renderer"]),
                "qtHeartbeatAgeMs": heartbeat_age_ms,
                "qtResponsive": bool(
                    heartbeat_age_ms <= RUNTIME_QT_HEARTBEAT_STALE_MS
                ),
                "scene": {
                    "active": bool(scene["active"]),
                    "scene2dLoaded": bool(scene["scene2dLoaded"]),
                    "videoLoaded": bool(scene["videoLoaded"]),
                    "videoPlaybackState": str(scene["videoPlaybackState"]),
                },
                "companion": {
                    "enabled": bool(companion["enabled"]),
                    "paused": bool(companion["paused"]),
                    "presentationReady": bool(companion["presentationReady"]),
                    "suppressed": bool(companion["suppressed"]),
                    "busy": bool(companion["busy"]),
                    "ackPending": bool(companion["ackPending"]),
                    "hasBubble": bool(companion["hasBubble"]),
                    "unreadCount": int(companion["unreadCount"]),
                    "state": str(companion["state"]),
                    "reason": str(companion["reason"]),
                    "expiresInSeconds": round(
                        max(0.0, float(companion["expiresInSeconds"])), 1
                    ),
                },
            }

    @Slot(result="QVariantMap")
    def performanceSnapshot(self) -> dict[str, Any]:
        return {
            "renderer": self._renderer,
            "fps": self._frame_rate,
            "sceneActive": self._scene_active,
            "activeTargetFps": int(self.theme.performance.get("activeFps", 60)),
            "coveredTargetFps": int(self.theme.performance.get("coveredFps", 15)),
        }

    @Slot(int, result=bool)
    def activateWindow(self, handle: int) -> bool:
        activated = bool(self.window_catalog.activate(handle))
        if activated:
            self._set_status("已切换窗口")
        else:
            self._set_status("窗口已经关闭或当前无法切换")
        self.refreshWindows()
        return activated

    @Slot(str)
    def setShellMode(self, mode: str) -> None:
        if mode == "compact":
            self.shell.enter_compact()
        elif mode == "visual":
            self.shell.enter_visual()
        else:
            self._set_status("登录外壳只能在设置中明确启用")
            return
        # Window re-parenting and taskbar changes must not leave the 30 ms
        # global WPS/PDF selection monitor paused.
        self.selection.ensure_monitor()
        self.refreshSceneActivity()
        self.shellModeChanged.emit()

    @Slot(result=str)
    def toggleDesktopShell(self) -> str:
        """Toggle the Lilies full desktop without conflating it with peek."""

        target = "visual" if self.shell.mode == "compact" else "compact"
        self.setShellMode(target)
        if self.shell.mode == "visual":
            self._set_status("莉莉丝桌面已展开 · 当前应用仍保持打开")
        else:
            self._set_status("已收成透明桌宠 · Windows 桌面已恢复")
        return self.shell.mode

    @Slot()
    def showCurrentSurface(self) -> None:
        """Reveal the selected shell surface without changing either mode.

        The tray icon and the plain Start-menu entry share this exact action.
        In particular it must not turn an active desktop-peek transaction into
        a shell switch, nor rewrite the user's persisted visual/compact choice.
        """

        self._consumeExternalActivation("show")

    def _activationResult(
        self,
        *,
        accepted: bool,
        applied: bool,
        surface_disposition: str,
        error: str = "",
        mode: str | None = None,
    ) -> dict[str, Any]:
        current_mode = str(mode if mode is not None else self.shell.mode)
        if current_mode not in {"visual", "login", "compact"}:
            current_mode = "visual"
        return {
            "accepted": bool(accepted),
            "applied": bool(applied),
            "mode": current_mode,
            "surfaceDisposition": surface_disposition,
            "error": str(error or "").strip()[:512],
        }

    def _requestExternalActivation(self, action: str) -> dict[str, Any]:
        """Wait boundedly for one socket action to finish on the Qt thread."""

        normalized = str(action or "").strip().casefold()
        if normalized not in {"visual", "compact", "show"}:
            return self._activationResult(
                accepted=False,
                applied=False,
                surface_disposition="not-applied",
                error="unsupported activation action",
            )
        # Direct dispatch from the object's own thread is useful in tests and
        # internal callers.  Queuing and then waiting here would deadlock the
        # very event loop that must consume the request.
        if QThread.currentThread() == self.thread():
            return self._consumeExternalActivation(normalized)

        request = _ActivationRequest(normalized, _ACTIVATION_APPLY_TIMEOUT_SECONDS)
        self._activationApplyRequested.emit(request)
        result = request.wait()
        if result is not None:
            return result
        snapshot_mode = str(self.runtimeSnapshot().get("shellMode", "visual"))
        return self._activationResult(
            accepted=True,
            applied=False,
            mode=snapshot_mode,
            surface_disposition="pending",
        )

    @Slot(object)
    def _completeExternalActivationRequest(self, request: object) -> None:
        if not isinstance(request, _ActivationRequest) or not request.claim():
            return
        try:
            result = self._consumeExternalActivation(request.action)
        except Exception as exc:  # Defensive: the consumer normally contains errors.
            result = self._activationResult(
                accepted=True,
                applied=False,
                surface_disposition="not-applied",
                error=str(exc),
            )
        request.complete(result)

    @Slot(str, result="QVariantMap")
    def _consumeExternalActivation(self, action: str) -> dict[str, Any]:
        """Apply a fixed launcher request and report its main-thread outcome."""

        normalized = str(action or "").strip().casefold()
        if normalized not in {"visual", "compact", "show"}:
            self._set_status("已拒绝未知的实例激活请求")
            return self._activationResult(
                accepted=False,
                applied=False,
                surface_disposition="not-applied",
                error="unsupported activation action",
            )
        try:
            if normalized == "visual":
                self.setShellMode("visual")
                if self.shell.mode != "visual" or self.database.get_setting(
                    "shell_mode"
                ) != "visual":
                    raise RuntimeError("visual shell mode was not persisted")
                self._set_status("已从快捷方式展开莉莉丝桌面")
            elif normalized == "compact":
                self.setShellMode("compact")
                if self.shell.mode != "compact" or self.database.get_setting(
                    "shell_mode"
                ) != "compact":
                    raise RuntimeError("compact shell mode was not persisted")
                self._set_status("已从快捷方式切回透明桌宠")
            else:
                # Keep the user's selected mode; only refresh and raise its surface.
                self.selection.ensure_monitor()
                self.shellModeChanged.emit()
                self._set_status("莉莉丝已回到当前界面")
            surface_disposition = (
                "privacy-suppressed" if self.dockSuppressed else "shown"
            )
            self.applicationActivationRequested.emit(normalized)
            return self._activationResult(
                accepted=True,
                applied=True,
                surface_disposition=surface_disposition,
            )
        except Exception as exc:
            self._set_status(f"实例激活失败：{exc}")
            return self._activationResult(
                accepted=True,
                applied=False,
                surface_disposition="not-applied",
                error=str(exc),
            )

    @Slot(result="QVariantMap")
    def toggleDesktopPeek(self) -> dict[str, Any]:
        try:
            self.shell.ensure_recovery_monitor()
            self._desktop_peek_status = self.desktop_peek.toggle()
            if self._desktop_peek_status.get("active"):
                count = int(self._desktop_peek_status.get("minimized", 0))
                self._set_status(f"已临时收起 {count} 个窗口 · 再按一次返回工作")
            else:
                count = int(self._desktop_peek_status.get("restored", 0))
                self._set_status(f"已返回工作 · 恢复 {count} 个窗口")
        except (OSError, RuntimeError, ValueError) as exc:
            self._desktop_peek_status = self.desktop_peek.status()
            self._desktop_peek_status["error"] = str(exc)
            self._set_status(f"桌面往返失败：{exc}")
        self.desktopPeekChanged.emit()
        self.database.log_desktop_peek("toggle", self._desktop_peek_status)
        return dict(self._desktop_peek_status)

    @Slot(result="QVariantMap")
    def restoreDesktopPeek(self) -> dict[str, Any]:
        try:
            self._desktop_peek_status = self.desktop_peek.restore()
            self._set_status("已恢复由 Lilies 收起的窗口")
        except (OSError, RuntimeError, ValueError) as exc:
            self._desktop_peek_status = self.desktop_peek.status()
            self._desktop_peek_status["error"] = str(exc)
            self._set_status(f"窗口恢复仍待重试：{exc}")
        self.desktopPeekChanged.emit()
        self.database.log_desktop_peek("restore", self._desktop_peek_status)
        return dict(self._desktop_peek_status)

    @Slot()
    def emergencyRestore(self) -> None:
        # Stop the visual-mode monitor before restoring Explorer.  Leaving the
        # in-memory mode as ``visual`` made maintain_explorer() hide the taskbar
        # and desktop icons again on its next two-second tick, so the emergency
        # recovery only lasted momentarily.
        previous_mode = self.shell.mode
        self.shell.mode = "compact"
        errors: list[str] = []
        try:
            self.database.set_setting("shell_mode", "compact")
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            # Runtime safety takes priority if the F-drive database disappears:
            # keeping the in-memory mode compact prevents another hide cycle.
            errors.append(f"模式持久化失败：{exc}")

        peek_status = self.restoreDesktopPeek()
        if peek_status.get("error"):
            errors.append(f"窗口恢复失败：{peek_status['error']}")
        try:
            self.shell.emergency_restore()
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            errors.append(f"Windows 外壳恢复失败：{exc}")

        self.selection.ensure_monitor()
        self.refreshSceneActivity()
        if previous_mode != "compact":
            self.shellModeChanged.emit()
        if errors:
            self._set_status("已停止 Lilies 桌面监视，但部分恢复待重试：" + "；".join(errors))
        else:
            self._set_status("已执行紧急恢复：已切回桌宠，窗口、桌面与任务栏均已恢复")

    def _component_desktop_peek_toggle(self) -> dict[str, Any]:
        return self.toggleDesktopPeek()

    def _component_desktop_peek_restore(self) -> dict[str, Any]:
        return self.restoreDesktopPeek()

    def _component_memory_recall(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.chat.memory.recall(
            partition_ids=payload.get("partitionIds", []),
            query=str(payload.get("query", "")),
            time_range=payload.get("timeRange"),
            limit=int(payload.get("limit", 6)),
            turn_id=secrets.token_hex(12),
            reason="盒子组件按需查阅",
        )

    @Slot(str)
    def setDesktopPeekHotkey(self, value: str) -> None:
        try:
            parsed = parse_hotkey(value)
        except ValueError as exc:
            self._set_status(str(exc))
            return
        if not self._preview_mode and not self._desktop_peek_hotkey.start(parsed.display):
            self._set_status(self._desktop_peek_hotkey.error or "无法注册这个快捷键")
            return
        self._desktop_peek_hotkey_text = parsed.display
        self.database.set_setting("desktop_peek_hotkey", parsed.display)
        self.desktopPeekChanged.emit()
        self._set_status(f"看桌面快捷键已改为 {parsed.display}")

    @Slot()
    def revealSystemDrawer(self) -> None:
        self.shell.reveal_system_drawer()
        self._set_status("Windows 系统栏将在 8 秒后重新收起")

    @Slot(str)
    def openSystemSettings(self, page: str) -> None:
        if page not in {"network", "sound", "notifications", "display"}:
            self._set_status("未知系统设置入口")
            return
        try:
            open_settings("ms-settings:" + page)
            self._set_status("已打开 Windows 系统设置")
        except Exception as exc:
            self._set_status(f"无法打开系统设置：{exc}")

    @Slot(str)
    def setRenderer(self, renderer: str) -> None:
        if renderer not in self.theme.renderers:
            return
        self.registry.invoke("theme", "activate", {"renderer": renderer}, confirmed=True)
        self._renderer = renderer
        self.rendererChanged.emit()

    @Slot()
    def replayIntro(self) -> None:
        # The intro belongs to the full visual desktop.  Replaying it from the
        # compact pet's settings page previously armed an invisible overlay:
        # the desktop window stayed hidden and the settings window remained
        # above it.  Commit the visible surface first, then close that settings
        # surface before starting the animation.
        if self._chat_open:
            self.setChatOpen(False)
        if self.shell.mode == "compact":
            self.setShellMode("visual")
        self._intro_active = True
        self.introChanged.emit()

    @Slot()
    def completeIntro(self) -> None:
        self._intro_active = False
        self.database.set_setting("intro_seen", True)
        self.introChanged.emit()

    @Slot(bool)
    def setChatOpen(self, open_value: bool) -> None:
        self._chat_open = bool(open_value)
        self.chatOpenChanged.emit()

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        if not text.strip() or self._chat_busy:
            return
        self._chat_text += ("\n\n" if self._chat_text else "") + "你：" + text.strip() + "\n\n莉莉丝："
        self.chatChanged.emit()
        self.chat.send(text)

    @Slot()
    def cancelMessage(self) -> None:
        self.chat.cancel()

    @Slot()
    def retryLastMessage(self) -> None:
        if self._chat_busy:
            return
        messages = self.database.recent_messages(self.chat.conversation_id, 80)
        match = next((value for value in reversed(messages) if value["role"] == "user"), None)
        if match:
            self.sendMessage(str(match["content"]))

    @Slot()
    def newConversation(self) -> None:
        if self._chat_busy:
            return
        self.chat.new_conversation()
        self._chat_text = ""
        self.chatChanged.emit()

    @Slot(str)
    def searchHistory(self, query: str) -> None:
        self._history_results = [
            {
                "messageId": value["message_id"],
                "conversationId": value["conversation_id"],
                "role": value["role"],
                "speaker": "你" if value["role"] == "user" else "莉莉丝",
                "content": value["content"],
                "createdAt": value["created_at"],
            }
            for value in self.database.search_messages(query)
        ]
        self.historyResultsChanged.emit()

    @Slot()
    def installModel(self) -> None:
        self.chat.install_model()

    @Slot()
    def refreshModelStatus(self) -> None:
        self._set_model_status(self.chat.status())

    @Slot(bool)
    def resolveToolConfirmation(self, approved: bool) -> None:
        self.chat.resolve_confirmation(approved)
        self._pending_tool = {}
        self.pendingToolChanged.emit()

    @Slot(str)
    def setPermissionMode(self, value: str) -> None:
        try:
            self.permissions.set_mode(PermissionMode(value))
            self._set_status("权限模式已更新")
            self.permissionChanged.emit()
        except ValueError:
            self._set_status("未知权限模式")

    @Slot(str, bool)
    def setTrustedAction(self, key: str, enabled: bool) -> None:
        allowed = set(self.database.get_setting("trusted_allowlist", []))
        if enabled:
            allowed.add(key)
        else:
            allowed.discard(key)
        self.database.set_setting("trusted_allowlist", sorted(allowed))
        self.permissionChanged.emit()

    @Slot(bool)
    def setSelectionEnabled(self, enabled: bool) -> None:
        self.selection.set_enabled(enabled)

    @Slot()
    def refreshSelectionSubscription(self) -> None:
        self.selection.refresh_subscription()
        self._set_status(self.selection.status)

    @Slot(str)
    def setPetFloatMode(self, mode: str) -> None:
        if mode not in {"always", "normal"}:
            self._set_status("未知的桌宠浮层模式")
            return
        if mode == self._pet_float_mode:
            return
        self._pet_float_mode = mode
        self.pet_habitat.set_floating_mode(mode)
        self.database.set_setting("pet_float_mode", mode)
        self.petFloatModeChanged.emit()
        self._habitat_status = self.pet_habitat.status()
        self.habitatChanged.emit()
        self._set_status("莉莉丝将始终置顶" if mode == "always" else "莉莉丝将遵循普通窗口层级")

    @Slot()
    def dismissSelectionBubble(self) -> None:
        self.selection.dismiss()

    @Slot(str, str)
    def requestSelectionAction(self, action: str, question: str = "") -> None:
        try:
            self.selection.request_action(action, question)
        except ValueError as exc:
            self._set_status(str(exc))

    @Slot(result=str)
    def saveSelectionCard(self) -> str:
        card_id = self.selection.save_current_card()
        if card_id:
            self.refreshReadingCards()
            self._set_status("已放进论文卡片盒")
        else:
            self._set_status("当前没有可保存的划词结果")
        return card_id

    @Slot(str, str)
    def searchReadingCards(self, query: str = "", kind: str = "") -> None:
        self._reading_items = self.database.reading_cards(query.strip(), kind.strip())
        self.readingItemsChanged.emit()

    @Slot(str)
    def deleteReadingCard(self, card_id: str) -> None:
        self.database.delete_reading_card(card_id)
        self.refreshReadingCards()

    @Slot()
    def refreshReadingCards(self) -> None:
        self._reading_items = self.database.reading_cards()
        self.readingItemsChanged.emit()

    @Slot()
    def runShellHealthCheck(self) -> None:
        self._shell_health = self.shell.health_check()
        self.shellHealthChanged.emit()
        self._set_status(str(self._shell_health.get("message", "健康检查完成")))

    @Slot()
    def enableLoginShell(self) -> None:
        if not self._shell_health.get("ok"):
            self._set_status("请先通过视觉模式健康检查")
            return
        executable = Path(sys.executable)
        if not getattr(sys, "frozen", False):
            candidate = Path(__file__).resolve().parents[2] / "dist" / "LiliesInTheBox" / "LiliesInTheBox.exe"
            if candidate.is_file():
                executable = candidate
        command = f'"{executable}" --login-shell'
        try:
            self.shell.enable_login_shell(command, "ENABLE_LILIES_LOGIN_SHELL")
            self._set_status("实验登录外壳已设置，将在下次登录生效")
            self.shellHealthChanged.emit()
        except Exception as exc:
            self._set_status(f"无法启用登录外壳：{exc}")

    @Slot()
    def disableLoginShell(self) -> None:
        self.shell.disable_login_shell()
        self._set_status("已恢复原登录外壳")
        self.shellHealthChanged.emit()

    @Slot(str, str, str)
    def addMemory(self, title: str, content: str, category: str = "事实") -> None:
        if title.strip() and content.strip():
            self.database.save_memory(title.strip(), content.strip(), category.strip() or "事实")
            self.refreshMemory()

    @Slot(str, str, str)
    def updateMemory(self, memory_id: str, title: str, content: str) -> None:
        match = next((value for value in self.database.memory_cards() if value["memory_id"] == memory_id), None)
        if match and title.strip() and content.strip():
            self.database.save_memory(
                title.strip(), content.strip(), match["category"], memory_id, bool(match["enabled"])
            )
            self.refreshMemory()

    @Slot(str, bool)
    def setMemoryEnabled(self, memory_id: str, enabled: bool) -> None:
        match = next((value for value in self.database.memory_cards() if value["memory_id"] == memory_id), None)
        if match:
            self.database.save_memory(match["title"], match["content"], match["category"], memory_id, enabled)
            self.refreshMemory()

    @Slot(str)
    def deleteMemory(self, memory_id: str) -> None:
        self.database.delete_memory(memory_id)
        self.refreshMemory()

    @Slot()
    def refreshMemory(self) -> None:
        self._memory_items = self.database.memory_cards()
        self.memoryItemsChanged.emit()
        self._memory_partitions = self.chat.memory.partitions()
        self._memory_map = self.chat.memory.memory_map()
        self.memoryMapChanged.emit()

    @Slot(str)
    def refreshMemoryMap(self, partition_id: str = "") -> None:
        self._memory_partitions = self.chat.memory.partitions()
        self._memory_map = self.chat.memory.memory_map(partition_id or None)
        self.memoryMapChanged.emit()

    @Slot(str, bool)
    def forgetMemoryFragment(self, fragment_id: str, delete_source: bool = False) -> None:
        self.chat.memory.forget(fragment_id, delete_source)
        self.refreshMemory()
        self._set_status("原对话与记忆已删除" if delete_source else "已从记忆检索中排除")

    @Slot(str, str)
    def moveMemoryFragment(self, fragment_id: str, partition_id: str) -> None:
        if self.chat.memory.move(fragment_id, partition_id):
            self.refreshMemoryMap(partition_id)
            self._set_status("记忆已移动到新分区")

    @Slot()
    def reindexMemory(self) -> None:
        result = self.chat.memory.reindex()
        self.refreshMemoryMap("")
        self._set_status(f"记忆索引已重建 · {result.get('fragments', 0)} 条")

    @Slot()
    def exitAndRestore(self) -> None:
        self.shell.emergency_restore()
        self.shutdown()
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.quit()

    def _set_status(self, value: str) -> None:
        self._status = str(value)
        self.statusChanged.emit()

    def _set_model_status(self, value: object) -> None:
        self._model_status = dict(value) if isinstance(value, dict) else self.chat.status()
        self.modelStatusChanged.emit()

    def _on_chat_started(self) -> None:
        self._chat_busy = True
        self.chatBusyChanged.emit()

    def _on_chat_chunk(self, value: str) -> None:
        self._chat_text += value
        self.chatChanged.emit()

    def _on_chat_finished(self, _value: str) -> None:
        self._chat_busy = False
        self.chatBusyChanged.emit()
        self._set_model_status(self.chat.status())

    def _on_chat_error(self, value: str) -> None:
        self._chat_text += f"\n\n[本地模型] {value}"
        self.chatChanged.emit()
        self._set_status(value)

    def _on_confirmation(self, value: object) -> None:
        self._pending_tool = dict(value) if isinstance(value, dict) else {}
        self.pendingToolChanged.emit()

    def _on_component_invoked(self, component_id: str, action_id: str, _result: object) -> None:
        # ComponentRegistry always returns an audit envelope.  Older direct
        # tests supplied the inner value, so accept both shapes while keeping
        # focus timing/transition metadata tied to the real service result.
        component_result = (
            _result.get("result")
            if isinstance(_result, dict) and "result" in _result
            else _result
        )
        if component_id == "theme" and action_id == "activate":
            renderer = str(self.database.get_setting("theme_renderer", self.theme.default_renderer))
            if renderer != self._renderer:
                self._renderer = renderer
                self.rendererChanged.emit()
        elif component_id == "desktop-icons":
            self.scanIcons()
            self.desktopLayoutsChanged.emit()
        elif component_id == "window-manager":
            self.refreshWindows()
        elif component_id == "shell-mode" and action_id == "switch":
            # Natural-language and local-socket shell switches bypass
            # setShellMode(), so renew the selection monitor here as well.
            self.selection.ensure_monitor()
            self.refreshSceneActivity()
            self.shellModeChanged.emit()
        elif component_id == "memory":
            self.refreshMemory()
        elif component_id == "reading-cards":
            self.refreshReadingCards()
        elif component_id == "focus" and action_id in {
            "start", "pause", "resume", "finish", "cancel"
        }:
            if isinstance(component_result, dict):
                transition_kind = {
                    "start": "started",
                    "pause": "paused",
                    "resume": "resumed",
                    "cancel": "cancelled",
                }.get(action_id)
                if action_id == "finish":
                    transition_kind = self._focus_finish_transition_kind(
                        component_result
                    )
                if transition_kind:
                    self._publish_focus_transition(
                        transition_kind, component_result
                    )
            self.refreshProductivity()
        elif (
            (component_id, action_id) in _PRODUCTIVITY_COMPONENT_MUTATIONS
            and component_id != "box-world"
        ):
            self.refreshProductivity()
        elif (
            component_id in {"calendar", "slack"}
            and action_id.startswith("propose-")
            and isinstance(component_result, dict)
        ):
            self._connector_proposal = dict(component_result)
            self.connectorStatusChanged.emit()
            self._set_status("外部操作只生成了预览；确认前不会写入")

    def _on_selection_bubble(self, value: object) -> None:
        self._selection_bubble = dict(value) if isinstance(value, dict) else self.selection.bubble
        self.selectionChanged.emit()
        if bool(self._selection_bubble.get("savedCardId")):
            self.refreshReadingCards()

    def _move_companion_to_box(self, payload: dict[str, Any]) -> None:
        if self._chat_busy:
            self._set_status("当前对话仍在生成，稍后再转入盒子")
            return
        self.chat.new_conversation()
        bubble = dict(payload.get("bubble") or {})
        messages = list(payload.get("messages") or [])
        initial = str(bubble.get("summary", "")).strip()
        transcript: list[str] = []
        if initial:
            self.database.add_message(
                self.chat.conversation_id,
                "assistant",
                initial,
                {"origin": "companion", "bubbleId": bubble.get("id", "")},
            )
            transcript.append("莉莉丝：" + initial)
        for message in messages:
            role = str(message.get("role", ""))
            text = str(message.get("text", "")).strip()
            if role not in {"user", "assistant"} or not text:
                continue
            self.database.add_message(
                self.chat.conversation_id,
                role,
                text,
                {"origin": "companion", "bubbleId": bubble.get("id", "")},
            )
            transcript.append(("你：" if role == "user" else "莉莉丝：") + text)
        self._chat_text = "\n\n".join(transcript)
        self._chat_open = True
        self.chatChanged.emit()
        self.chatOpenChanged.emit()
        self.openConversationRequested.emit()
        self._set_status("这个话题已转入盒子")

    def _monitor_shell(self) -> None:
        # Explorer/recovery checks launch process and registry queries. They
        # remain on their two-second timer and simply run on the next tick
        # after a pointer-critical pet gesture finishes.
        if self._pet_interaction_locked:
            return
        try:
            if self.shell.maintain_recovery_monitor():
                self._set_status("Windows 恢复监视器已重新启动")
        except OSError:
            self._set_status("Windows 恢复监视器暂时无法启动；将自动重试")
        try:
            if self.shell.maintain_explorer():
                self._set_status("Explorer 已自动重新启动")
                # Explorer rebuilds Progman/WorkerW after a crash.  A Qt
                # WindowStaysOnBottom surface can remain alive underneath the
                # newly-created wallpaper window, so ask QML to re-present the
                # already-selected visual surface without switching modes or
                # stealing focus from the current application.
                self.applicationActivationRequested.emit("show")
        except OSError as exc:
            self._set_status(f"Explorer 恢复失败：{exc}")

    def enter_initial_mode(self) -> None:
        if self.shell.mode == "login":
            self.shell.enter_login()
        elif self.shell.mode == "visual":
            self.shell.enter_visual()
        else:
            self.shell.enter_compact()
        self.refreshSceneActivity()
        self.shellModeChanged.emit()

    def shutdown(self) -> None:
        if self._shutdown_complete or self._shutdown_in_progress:
            return
        self._shutdown_in_progress = True
        try:
            try:
                self._shutdown_services()
            finally:
                database_session = self._database_session
                if database_session is not None:
                    database_session.close()
                    self._database_session = None
        finally:
            self._shutdown_in_progress = False
        self.database.close()
        self._shutdown_complete = True

    def _shutdown_services(self) -> None:
        self._window_catalog_shutting_down = True
        self._window_catalog_pending_payload = None
        self._window_catalog_refresh_queued = False
        catalog_cancel_event = self._window_catalog_cancel_event
        if catalog_cancel_event is not None:
            catalog_cancel_event.set()
        self._runtime_heartbeat_timer.stop()
        self._shell_monitor.stop()
        self._v03_timer.stop()
        self._productivity_timer.stop()
        self._calendar_sync_timer.stop()
        self._desktop_peek_hotkey.stop()
        try:
            self._desktop_peek_status = self.desktop_peek.restore()
        except (OSError, RuntimeError, ValueError):
            pass
        self.companion.shutdown()
        self.connector_assist.shutdown()
        if self.slack_socket is not None:
            self.slack_socket.stop()
        for receiver in tuple(self._oauth_receivers.values()):
            try:
                receiver.close()
            except (OSError, RuntimeError):
                pass
        self._oauth_receivers.clear()
        self.input_pulse.stop()
        self.window_catalog.stop()
        catalog_thread = self._window_catalog_thread
        if catalog_thread is not None and catalog_thread.is_alive():
            catalog_thread.join(timeout=0.25)
        for unsubscribe in (
            self._catalog_unsubscribe,
            self._hub_activity_unsubscribe,
            self._hub_habitat_unsubscribe,
        ):
            try:
                unsubscribe()
            except (AttributeError, RuntimeError):
                pass
        self.win_event_hub.stop()
        self.selection.shutdown()
        self.chat.shutdown()
        self.socket.stop()
        self.shell.shutdown()
        for thread in tuple(self._connector_threads):
            thread.join(timeout=0.15)
        if not any(thread.is_alive() for thread in self._connector_threads):
            for connector in (self.calendar_connector, self.slack_connector):
                if connector is not None:
                    try:
                        connector.close()
                    except (OSError, RuntimeError):
                        pass
