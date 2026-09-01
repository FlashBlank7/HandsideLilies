from __future__ import annotations

"""Pure placement/state logic for Lilith's quiet window habitat.

This module never calls ``SetParent``, moves a host window, or changes its
style.  It only returns a candidate point/pose for Lilies' independent Qt Tool
window.  Keeping placement pure makes DPI/monitor behaviour testable without a
desktop session and lets the UI animate toward a new candidate safely.
"""

import math
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Mapping

from .win_event import WinEvent, WinEventKind
from .window_catalog import WindowRecord, WindowRect


class FloatingMode(str, Enum):
    ALWAYS = "always"
    NORMAL = "normal"


class PresenceMode(str, Enum):
    NORMAL = "normal"
    BLOCKED = "blocked"
    SILENT = "silent"


# ``windowSizeClass`` remains the hysteretic coarse geometry class used by
# the placement math.  ``habitatStrategy`` is the presentation contract that
# QML and theme manifests consume.  Keeping the two separate lets a tiny or
# maximized host select a purpose-built silhouette without destabilising the
# small/medium/large resize thresholds.
HOST_HABITAT_STRATEGIES = (
    "tiny",
    "small",
    "medium",
    "large",
    "maximized",
    "edge",
)


@dataclass(frozen=True, slots=True)
class HabitatHost:
    handle: int
    rect: WindowRect | None
    work_area: WindowRect | None
    app_id: str = ""
    process_id: int = 0
    visible: bool = True
    minimized: bool = False
    maximized: bool = False
    full_screen: bool = False
    sensitive: bool = False
    monitor_id: str = ""
    dpi: int = 96
    title_bar_height: float | None = None

    @classmethod
    def from_value(cls, value: object) -> "HabitatHost | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, WindowRecord):
            return cls(
                handle=value.handle,
                rect=value.rect,
                work_area=value.work_area,
                app_id=str(value.app_id or ""),
                process_id=max(0, int(value.process_id)),
                visible=value.visible,
                minimized=value.minimized,
                maximized=value.maximized,
                full_screen=value.full_screen,
                monitor_id=value.monitor_id,
                dpi=value.dpi,
                title_bar_height=value.title_bar_height,
            )
        if not isinstance(value, Mapping):
            raise TypeError("host must be a WindowRecord, mapping, HabitatHost, or None")
        try:
            handle = int(value.get("handle", value.get("hwnd", 0)) or 0)
        except (TypeError, ValueError):
            handle = 0
        if not handle:
            return None
        try:
            dpi = max(1, int(value.get("dpi", 96) or 96))
        except (TypeError, ValueError):
            dpi = 96
        try:
            process_id = max(
                0, int(value.get("processId", value.get("process_id", 0)) or 0)
            )
        except (TypeError, ValueError):
            process_id = 0
        raw_title_bar_height = value.get(
            "titleBarHeight", value.get("title_bar_height")
        )
        try:
            title_bar_height = (
                None
                if raw_title_bar_height is None
                else max(0.0, float(raw_title_bar_height))
            )
        except (TypeError, ValueError):
            title_bar_height = None
        return cls(
            handle=handle,
            rect=WindowRect.from_value(value.get("rect")),
            work_area=WindowRect.from_value(
                value.get("workArea", value.get("work_area"))
            ),
            app_id=str(value.get("appId", value.get("app_id", "")) or ""),
            process_id=process_id,
            visible=bool(value.get("visible", True)),
            minimized=bool(value.get("minimized", False)),
            maximized=bool(value.get("maximized", False)),
            full_screen=bool(value.get("fullScreen", value.get("full_screen", False))),
            sensitive=bool(
                value.get("sensitive", value.get("blocked", value.get("protected", False)))
            ),
            monitor_id=str(value.get("monitorId", value.get("monitor_id", "")) or ""),
            dpi=dpi,
            title_bar_height=title_bar_height,
        )

    @property
    def stable_app_identity(self) -> str:
        """Return the narrowest stable identity available for manual detach.

        WindowCatalog already normalizes ``appId`` from AUMID/executable/name.
        Keeping manual placement against that identity means switching between
        two WPS/browser windows does not look like switching applications.  A
        PID is the best safe fallback for older/synthetic callers; HWND is
        intentionally last because it is a window identity, not an app one.
        """

        app_id = self.app_id.strip().casefold()
        if app_id:
            return f"app:{app_id}"
        if self.process_id > 0:
            return f"pid:{self.process_id}"
        return f"hwnd:{self.handle}"


@dataclass(frozen=True, slots=True)
class HabitatCandidate:
    x: float
    y: float
    scale: float
    pose: str
    side: str
    anchor_x: float
    anchor_y: float
    profile: str = "desktop"
    size_class: str = "desktop"
    strategy: str = "desktop"
    anchor_norm_x: float = 0.5
    anchor_norm_y: float = 1.0
    contact_x: float = 0.5
    contact_y: float = 1.0
    mirror: bool = False
    title_bar_height: float = 0.0
    pose_variant: str = "desktop-prayer"
    motion_style: str = "quiet-breathe"
    motion_period: float = 3.4
    peek_fraction: float = 1.0

    def to_dict(self) -> dict[str, float | str | bool]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "scale": round(self.scale, 4),
            "pose": self.pose,
            "side": self.side,
            "anchorX": round(self.anchor_x, 2),
            "anchorY": round(self.anchor_y, 2),
            "profile": self.profile,
            "windowSizeClass": self.size_class,
            "habitatStrategy": self.strategy,
            "characterScale": round(self.scale, 4),
            "anchorNormX": round(self.anchor_norm_x, 4),
            "anchorNormY": round(self.anchor_norm_y, 4),
            "contactX": round(self.contact_x, 4),
            "contactY": round(self.contact_y, 4),
            "mirror": self.mirror,
            "titleBarHeight": round(self.title_bar_height, 2),
            "poseVariant": self.pose_variant,
            "motionStyle": self.motion_style,
            "motionPeriod": round(self.motion_period, 3),
            "peekFraction": round(self.peek_fraction, 4),
        }


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        return low
    return max(low, min(value, high))


def _covers_work_area(rect: WindowRect, work: WindowRect) -> bool:
    tolerance = 3
    return (
        rect.left <= work.left + tolerance
        and rect.top <= work.top + tolerance
        and rect.right >= work.right - tolerance
        and rect.bottom >= work.bottom - tolerance
    )


def _window_size_class(
    rect: WindowRect,
    dpi_scale: float,
    previous_size_class: str = "",
) -> str:
    logical_width = rect.width / dpi_scale
    logical_height = rect.height / dpi_scale
    aspect_ratio = logical_width / max(1.0, logical_height)
    previous = str(previous_size_class).strip().casefold()
    # A very wide caption edge can safely carry the existing panoramic large
    # pose even when the host is not tall enough for the conventional large
    # rectangle.  Match the panoramic artwork's width hysteresis and add an
    # aspect-ratio band so ordinary landscape windows keep their old class.
    # The height floor preserves the small/micro boundary for short strips.
    ultrawide_enter = (
        logical_width >= 1760
        and logical_height >= 420
        and aspect_ratio >= 3.0
    )
    ultrawide_leave = (
        logical_width >= 1680
        and logical_height >= 390
        and aspect_ratio >= 2.8
    )
    # Different enter/leave thresholds prevent a resize animation or a one-px
    # frame correction from repeatedly swapping the artwork bundle.
    if previous == "small":
        # The ordinary small-class leave band is taller than the panoramic
        # enter floor.  Let an unequivocally ultrawide surface cross directly
        # into the large panorama contract instead of pinning a same-window
        # resize to ``small`` until it also grows past 460 logical pixels.
        if ultrawide_enter:
            return "large"
        if logical_width < 680 or logical_height < 460:
            return "small"
    elif previous == "large":
        if (
            logical_width >= 1240 and logical_height >= 680
        ) or ultrawide_leave:
            return "large"
    elif previous == "medium":
        if logical_width < 610 or logical_height < 390:
            return "small"
        if (
            logical_width >= 1320 and logical_height >= 760
        ) or ultrawide_enter:
            return "large"
        return "medium"
    if logical_width < 640 or logical_height < 420:
        return "small"
    if (
        logical_width >= 1280 and logical_height >= 720
    ) or ultrawide_enter:
        return "large"
    return "medium"


def choose_habitat_candidate(
    host_rect: WindowRect,
    work_area: WindowRect,
    *,
    pet_width: float,
    pet_height: float,
    maximized: bool = False,
    preferred_side: str = "auto",
    title_bar_height: float | None = None,
    dpi_scale: float = 1.0,
    previous_profile: str = "",
    previous_size_class: str = "",
    previous_side: str = "",
    previous_pose_variant: str = "",
) -> HabitatCandidate:
    """Choose a deterministic pose, scale and artwork contact anchor.

    The returned window position remains compatible with the existing QML
    shell.  The normalized anchor/contact values let ``V03PetBody`` align the
    visible character inside that unchanged transparent tool window, so a
    smaller pose does not drift away from its host edge.
    """

    width = max(48.0, float(pet_width))
    height = max(72.0, float(pet_height))
    dpi = max(0.5, min(4.0, float(dpi_scale)))
    if host_rect.width <= 0 or host_rect.height <= 0:
        raise ValueError("host rectangle must have positive dimensions")
    if work_area.width <= 0 or work_area.height <= 0:
        raise ValueError("work area must have positive dimensions")

    is_maximized = bool(maximized or _covers_work_area(host_rect, work_area))
    size_class = _window_size_class(host_rect, dpi, previous_size_class)
    logical_width = host_rect.width / dpi
    logical_height = host_rect.height / dpi
    previous_profile_value = str(previous_profile).strip().casefold()
    previous_side_value = str(previous_side).strip().casefold()
    previous_variant_value = str(previous_pose_variant).strip().casefold()
    caption_height = (
        32.0 * dpi
        if title_bar_height is None
        else max(0.0, float(title_bar_height))
    )
    logical_caption_height = caption_height / dpi
    available_above = max(0.0, float(host_rect.top - work_area.top))

    def edge_candidate(
        profile: str,
        target_scale: float,
        *,
        listening: bool = False,
    ) -> HabitatCandidate:
        # The checked transparent edge-peek master is deliberately reused,
        # but each constrained window geometry gets its own presentation
        # contract.  Contact points vary how much of Lilith is revealed while
        # the QML motion style rotates/scales around that exact contact point.
        # This gives micro, off-screen, narrow-caption and maximized windows
        # recognisably different behaviour without admitting the generated
        # checkerboard concept images into the runtime theme.
        edge_presentations = {
            "maximized-edge": {
                "strategy": "maximized",
                "variant": "screen-edge-watch",
                "motion": "screen-watch",
                "period": 5.2,
                "visible_fraction": 0.72,
                "contact_x": 0.18,
                "contact_y": 0.58,
            },
            "offscreen-window-edge": {
                "strategy": "edge",
                "variant": "cautious-return",
                "motion": "cautious-peek",
                "period": 4.6,
                "visible_fraction": 0.70,
                "contact_x": 0.34,
                "contact_y": 0.54,
            },
            "narrow-caption-edge": {
                "strategy": "edge",
                "variant": "caption-side-lean",
                "motion": "caption-lean",
                "period": 3.8,
                "visible_fraction": 0.78,
                "contact_x": 0.12,
                "contact_y": 0.74,
            },
            "micro-window-edge": {
                "strategy": "tiny",
                "variant": "micro-corner-grip",
                "motion": "corner-grip",
                "period": 2.9,
                "visible_fraction": 0.74,
                "contact_x": 0.28,
                "contact_y": 0.64,
            },
            "top-space-listen": {
                "strategy": "edge",
                "variant": "edge-listen",
                "motion": "edge-listen",
                "period": 4.2,
                "visible_fraction": 0.72,
                "contact_x": 0.30,
                "contact_y": 0.62,
            },
        }
        presentation = edge_presentations.get(
            profile,
            {
                "strategy": "edge",
                "variant": "edge-peek",
                "motion": "quiet-breathe",
                "period": 3.4,
                "visible_fraction": 0.72,
                "contact_x": 0.15,
                "contact_y": 0.68,
            },
        )
        center = (host_rect.left + host_rect.right) / 2
        work_center = (work_area.left + work_area.right) / 2
        if preferred_side in {"left", "right"}:
            side = preferred_side
        else:
            # Maximized windows use the right edge by default.  A smaller host
            # keeps Lilith near the screen edge closest to its own centre.  A
            # broad centre dead-zone preserves the previous side and prevents
            # repeated mirroring while a centred window is being resized.
            side_margin = work_area.width * 0.08
            if previous_side_value == "left" and center <= work_center + side_margin:
                side = "left"
            elif previous_side_value == "right" and center >= work_center - side_margin:
                side = "right"
            else:
                side = "left" if center < work_center else "right"
        character_ratio = 2.0 / 3.0
        max_scale = work_area.height / max(1.0, height * character_ratio)
        scale = min(1.0, max(0.58, min(target_scale, max_scale)))
        visual_height = height * character_ratio * scale
        contact_y = float(presentation["contact_y"])
        edge_margin = min(18.0 * dpi, max(0.0, work_area.height - visual_height) / 2)
        if profile == "maximized-edge":
            # A maximized host owns the whole screen, so a calm upper-third
            # resting point is more useful than following one arbitrary
            # caption coordinate.
            desired_visual_top = float(work_area.top) + max(
                edge_margin, (work_area.height - visual_height) * 0.34
            )
        else:
            # Narrow-caption, tiny-window and no-top-space fallbacks still
            # belong to the foreground window.  Keep the character's hand /
            # cheek contact point level with that window's title edge instead
            # of teleporting her to a fixed one-third screen position.
            desired_anchor_y = float(host_rect.top) + _clamp(
                caption_height,
                18.0 * dpi,
                48.0 * dpi,
            )
            desired_visual_top = desired_anchor_y - visual_height * contact_y
        visual_top = _clamp(
            desired_visual_top,
            float(work_area.top) + edge_margin,
            float(work_area.bottom) - visual_height - edge_margin,
        )
        y = _clamp(
            visual_top,
            float(work_area.top),
            float(work_area.bottom) - height,
        )
        visible_fraction = float(presentation["visible_fraction"])
        anchor_y = visual_top + visual_height * contact_y
        left_contact_x = float(presentation["contact_x"])
        if side == "left":
            x = float(work_area.left) - width * (1.0 - visible_fraction)
            pose = "edge-peek-left"
            anchor_x = float(work_area.left)
            contact_x = left_contact_x
        else:
            x = float(work_area.right) - width * visible_fraction
            pose = "edge-peek-right"
            anchor_x = float(work_area.right)
            contact_x = 1.0 - left_contact_x
        return HabitatCandidate(
            x=x,
            y=y,
            scale=scale,
            pose=pose,
            side=side,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            profile=profile,
            size_class=size_class,
            strategy=str(presentation["strategy"]),
            anchor_norm_x=_clamp((anchor_x - x) / width, 0.0, 1.0),
            anchor_norm_y=_clamp((anchor_y - y) / height, 0.0, 1.0),
            contact_x=contact_x,
            contact_y=contact_y,
            mirror=side == "right",
            title_bar_height=caption_height,
            pose_variant=str(presentation["variant"]),
            motion_style=str(presentation["motion"]),
            motion_period=float(presentation["period"]),
            peek_fraction=visible_fraction,
        )

    if is_maximized:
        return edge_candidate("maximized-edge", 0.82)

    visible_width = max(
        0.0,
        float(min(host_rect.right, work_area.right) - max(host_rect.left, work_area.left)),
    )
    visible_height = max(
        0.0,
        float(min(host_rect.bottom, work_area.bottom) - max(host_rect.top, work_area.top)),
    )
    visible_width_ratio = visible_width / max(1.0, float(host_rect.width))
    visible_height_ratio = visible_height / max(1.0, float(host_rect.height))
    offscreen_leave_ratio = (
        0.72 if previous_profile_value == "offscreen-window-edge" else 0.56
    )
    if (
        visible_width_ratio < offscreen_leave_ratio
        or visible_height_ratio < offscreen_leave_ratio
    ):
        # If most of the host is beyond the selected monitor, a title-edge
        # contact point would itself be unreachable.  Stay on the nearest
        # screen edge until a comfortably large part of the host is visible.
        return edge_candidate("offscreen-window-edge", 0.70)

    narrow_threshold = 26.0 if previous_profile_value == "narrow-caption-edge" else 20.0
    if logical_caption_height <= narrow_threshold:
        return edge_candidate("narrow-caption-edge", 0.74)

    # A conventional caption does not make a very small host safe to perch
    # on.  Below these thresholds even the compact title-sit artwork would
    # overlap most of the title text or the system buttons.  The wider leave
    # thresholds add hysteresis while the user resizes around the boundary.
    micro_width_limit = 420.0 if previous_profile_value == "micro-window-edge" else 360.0
    micro_height_limit = 290.0 if previous_profile_value == "micro-window-edge" else 240.0
    if logical_width < micro_width_limit or logical_height < micro_height_limit:
        return edge_candidate("micro-window-edge", 0.66)

    # A portrait editor/reader has enough area to host Lilith, but the square
    # prone pose consumes too much of its short title edge.  Give this common
    # geometry its own verified title-sit presentation.  The wider leave
    # threshold prevents a resize around 1:1 from swapping silhouettes every
    # frame.
    aspect_ratio = logical_width / max(1.0, logical_height)
    portrait_limit = 1.02 if previous_profile_value == "portrait-title" else 0.92
    portrait_window = size_class == "medium" and aspect_ratio < portrait_limit

    if size_class == "small":
        profile = "small-title"
        pose = "title-sit"
        target_scale = 0.68
        minimum_scale = 0.56
        pose_height_ratio = (2.0 / 3.0) * 0.90
        contact_x = 0.60
        contact_y = 0.72
        host_fraction = 0.34
        compact_title = (
            logical_width
            < (460.0 if previous_variant_value == "compact-title-curl" else 420.0)
            or logical_height
            < (330.0 if previous_variant_value == "compact-title-curl" else 300.0)
        )
        if compact_title:
            pose_variant = "compact-title-curl"
            motion_style = "title-curl"
            motion_period = 3.8
        else:
            pose_variant = "title-sit-balance"
            motion_style = "title-balance"
            motion_period = 3.3
    elif portrait_window:
        profile = "portrait-title"
        pose = "title-sit"
        target_scale = 0.76
        minimum_scale = 0.60
        pose_height_ratio = (2.0 / 3.0) * 0.82
        contact_x = 0.56
        contact_y = 0.76
        # Match medium-perch's anchor fraction at the 1.02 leave boundary so
        # crossing portrait/landscape moves the artwork, not the tool window.
        host_fraction = 0.318
        pose_variant = "portrait-title-watch"
        motion_style = "portrait-listen"
        motion_period = 4.7
    elif size_class == "large":
        profile = "large-perch"
        pose = "perch-top"
        width_progress = _clamp((logical_width - 1280.0) / 640.0, 0.0, 1.0)
        target_scale = 0.94 + width_progress * 0.06
        minimum_scale = 0.68
        pose_height_ratio = (2.0 / 3.0) * 0.58
        contact_x = 0.50
        contact_y = 0.94
        host_fraction = 0.26 - width_progress * 0.02
        panoramic = logical_width >= (
            1680.0 if previous_variant_value == "panoramic-prone" else 1760.0
        )
        if panoramic:
            pose_variant = "panoramic-prone"
            motion_style = "perch-drift"
            motion_period = 6.2
        else:
            pose_variant = "wide-window-sprawl"
            motion_style = "perch-stretch"
            motion_period = 5.0
    else:
        profile = "medium-perch"
        pose = "perch-top"
        width_progress = _clamp((logical_width - 640.0) / 640.0, 0.0, 1.0)
        target_scale = 0.72 + width_progress * 0.22
        minimum_scale = 0.62
        pose_height_ratio = (2.0 / 3.0) * 0.58
        contact_x = 0.50
        contact_y = 0.94
        host_fraction = 0.34 - width_progress * 0.08
        compact_perch = (
            logical_width
            < (960.0 if previous_variant_value == "window-perch-tucked" else 880.0)
            or logical_height
            < (540.0 if previous_variant_value == "window-perch-tucked" else 500.0)
        )
        if compact_perch:
            pose_variant = "window-perch-tucked"
            motion_style = "perch-tuck"
            motion_period = 3.7
        else:
            pose_variant = "window-perch"
            motion_style = "perch-breathe"
            motion_period = 4.1

    overlap = _clamp(caption_height * 0.42, 10.0 * dpi, 24.0 * dpi)
    maximum_top_scale = (available_above + overlap) / max(
        1.0, height * pose_height_ratio * contact_y
    )
    scale = min(target_scale, maximum_top_scale)
    leave_edge_margin = 0.06 if previous_profile_value == "top-space-listen" else 0.0
    if scale < minimum_scale + leave_edge_margin:
        # The existing listening-live transparent layer becomes a quiet
        # "I cannot sit here, so I will listen from the edge" variation.
        return edge_candidate("top-space-listen", 0.70, listening=True)

    visual_height = height * pose_height_ratio * scale
    artwork_ratio = 0.591 if pose == "title-sit" else 1.003
    visual_width = visual_height * artwork_ratio
    caption_reserved = max(96.0 * dpi, caption_height * 3.2)
    left_anchor = float(host_rect.left) + visual_width * contact_x + 10.0 * dpi
    right_anchor = (
        float(host_rect.right) - caption_reserved
        - visual_width * (1.0 - contact_x) - 8.0 * dpi
    )
    target_anchor = float(host_rect.left) + host_rect.width * host_fraction
    if right_anchor < left_anchor:
        target_anchor = float(host_rect.left) + host_rect.width / 2
        right_anchor = float(host_rect.right) - visual_width * (1.0 - contact_x) - 8.0 * dpi
    anchor_x = _clamp(target_anchor, left_anchor, right_anchor)
    anchor_y = float(host_rect.top) + 1.0
    x = _clamp(
        anchor_x - width / 2,
        float(work_area.left),
        float(work_area.right) - width,
    )
    y = _clamp(
        anchor_y - visual_height * contact_y,
        float(work_area.top),
        float(work_area.bottom) - height,
    )
    return HabitatCandidate(
        x=x,
        y=y,
        scale=scale,
        pose=pose,
        side="top",
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        profile=profile,
        size_class=size_class,
        strategy=size_class,
        anchor_norm_x=_clamp((anchor_x - x) / width, 0.0, 1.0),
        anchor_norm_y=_clamp((anchor_y - y) / height, 0.0, 1.0),
        contact_x=contact_x,
        contact_y=contact_y,
        mirror=False,
        title_bar_height=caption_height,
        pose_variant=pose_variant,
        motion_style=motion_style,
        motion_period=motion_period,
        peek_fraction=1.0,
    )


class PetHabitatController:
    """Fifteen-second foreground stability and manual-detach state machine."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        stable_seconds: float = 15.0,
        pet_width: float = 150.0,
        pet_height: float = 250.0,
        desktop_x: float = 48.0,
        desktop_y: float = 240.0,
    ) -> None:
        self.clock = clock
        self.stable_seconds = max(0.0, float(stable_seconds))
        self.pet_width = max(48.0, float(pet_width))
        self.pet_height = max(72.0, float(pet_height))
        self.desktop_x = float(desktop_x)
        self.desktop_y = float(desktop_y)
        self._floating_mode = FloatingMode.ALWAYS
        self._presence = PresenceMode.NORMAL
        self._host: HabitatHost | None = None
        self._foreground_since: float | None = None
        self._detached_app_identity: str | None = None
        self._preferred_side = "auto"
        self._avoidance_candidate: HabitatCandidate | None = None
        self._avoidance_handle: int | None = None
        self._avoidance_ready_at = 0.0
        self._avoidance_last_now: float | None = None
        self._avoidance_recent_targets: list[tuple[int, float, float, float]] = []
        self._last_candidate: HabitatCandidate | None = None
        self._last_candidate_handle: int | None = None

    def set_floating_mode(self, mode: str | FloatingMode) -> None:
        self._floating_mode = FloatingMode(mode)

    def set_presence(self, mode: str | PresenceMode) -> None:
        self._presence = PresenceMode(mode)
        if self._presence is not PresenceMode.NORMAL:
            self.clear_avoidance()

    def set_pet_size(self, width: float, height: float) -> None:
        next_width = max(48.0, float(width))
        next_height = max(72.0, float(height))
        if (
            abs(next_width - self.pet_width) > 0.01
            or abs(next_height - self.pet_height) > 0.01
        ):
            # An avoidance corner computed for the previous native window
            # size may become clipped after resize.
            self.clear_avoidance()
        self.pet_width = next_width
        self.pet_height = next_height

    def set_desktop_position(self, x: float, y: float) -> None:
        self.desktop_x = float(x)
        self.desktop_y = float(y)
        # This setter is called by the completed manual drag path.  The user's
        # chosen position must supersede a hostless avoidance hop as well as
        # an attached one.
        self.clear_avoidance()

    def clear_avoidance(self) -> None:
        self._avoidance_candidate = None
        self._avoidance_handle = None
        self._avoidance_ready_at = 0.0
        self._avoidance_last_now = None
        self._avoidance_recent_targets.clear()

    def set_avoidance_position(
        self,
        x: float,
        y: float,
        work_area: WindowRect,
        *,
        now: float | None = None,
    ) -> bool:
        """Apply one transient, fully on-screen pointer-avoidance position.

        The candidate is deliberately not persisted.  A three-second grace
        period prevents Lilies from repeatedly running away, so a person can
        follow and click her after the first small hop.
        """

        current = self.clock() if now is None else float(now)
        if self._avoidance_last_now is not None and current < self._avoidance_last_now:
            # Production uses monotonic time, but injected clocks and rare
            # platform anomalies must not leave debounce deadlines stranded
            # on an abandoned future timeline.
            self._avoidance_ready_at = 0.0
            self._avoidance_recent_targets.clear()
        self._avoidance_last_now = current
        if self._presence is not PresenceMode.NORMAL or current < self._avoidance_ready_at:
            return False
        if self._detached_app_identity is not None:
            # Manual placement is authoritative even while the foreground is
            # temporarily an unmanaged/Lilies surface with no HabitatHost.
            return False
        host = self._host
        if host is not None:
            if (
                host.sensitive
                or host.full_screen
                or not host.visible
                or host.minimized
                or host.rect is None
            ):
                return False
            stable = (
                0.0
                if self._foreground_since is None
                else max(0.0, current - self._foreground_since)
            )
            if stable < self.stable_seconds:
                return False
        if work_area.width <= 0 or work_area.height <= 0:
            return False
        if self.pet_width > work_area.width or self.pet_height > work_area.height:
            # The controller moves the real Qt window, not a cropped proxy.
            # Refuse a promise of "fully on screen" when that window cannot
            # physically fit; the compact-size policy will retry after it has
            # selected an emergency size for this work area.
            return False
        width = self.pet_width
        height = self.pet_height
        safe_x = _clamp(float(x), float(work_area.left), float(work_area.right) - width)
        safe_y = _clamp(float(y), float(work_area.top), float(work_area.bottom) - height)
        avoidance_handle = host.handle if host else 0
        # Candidate scoring happens against six stable screen locations.  Do
        # not let small cursor/geometry noise reselect the same location, or
        # bounce A -> B -> A every cooldown.  A location becomes eligible
        # again after twelve seconds, long enough for the hop animation and a
        # deliberate click, but short enough that avoidance never feels stuck.
        reuse_seconds = 12.0
        deadband = max(36.0, min(width, height) * 0.12)
        self._avoidance_recent_targets = [
            value
            for value in self._avoidance_recent_targets
            if value[0] == avoidance_handle and current - value[3] < reuse_seconds
        ]
        if any(
            math.hypot(safe_x - previous_x, safe_y - previous_y) <= deadband
            for _handle, previous_x, previous_y, _accepted_at
            in self._avoidance_recent_targets
        ):
            # Throttle repeated duplicate proposals without extending the
            # full click grace period indefinitely.
            self._avoidance_ready_at = current + 0.75
            return False
        work_center = (work_area.left + work_area.right) / 2
        side = "left" if safe_x + width / 2 < work_center else "right"
        self._avoidance_candidate = HabitatCandidate(
            x=safe_x,
            y=safe_y,
            scale=1.0,
            pose="title-sit",
            side=side,
            anchor_x=safe_x + width / 2,
            anchor_y=safe_y + height * 0.72,
            profile="avoidance",
            size_class="desktop",
            strategy="desktop",
            anchor_norm_x=0.5,
            anchor_norm_y=0.72,
            contact_x=0.5,
            contact_y=0.72,
            pose_variant="pointer-safe-rest",
            motion_style="title-balance",
            motion_period=4.4,
        )
        self._avoidance_handle = avoidance_handle
        self._avoidance_recent_targets.append(
            (avoidance_handle, safe_x, safe_y, current)
        )
        self._avoidance_recent_targets = self._avoidance_recent_targets[-8:]
        self._avoidance_ready_at = current + 3.0
        return True

    def set_preferred_side(self, side: str) -> None:
        value = str(side).strip().casefold()
        if value not in {"auto", "left", "right"}:
            raise ValueError("preferred side must be auto, left, or right")
        self._preferred_side = value

    def update_foreground(self, value: object, now: float | None = None) -> None:
        current = self.clock() if now is None else float(now)
        host = HabitatHost.from_value(value)
        previous_host = self._host
        if (
            host is not None
            and previous_host is not None
            and host.handle == previous_host.handle
        ):
            # Geometry-only WinEvent refreshes can omit catalog identity
            # fields.  Treat that as a partial update, not evidence that the
            # owning application changed underneath the same HWND.
            if not host.app_id and previous_host.app_id:
                host = replace(host, app_id=previous_host.app_id)
            if host.process_id <= 0 and previous_host.process_id > 0:
                host = replace(host, process_id=previous_host.process_id)
        previous_handle = self._host.handle if self._host else 0
        current_handle = host.handle if host else 0
        previous_app_identity = (
            previous_host.stable_app_identity if previous_host else ""
        )
        current_app_identity = host.stable_app_identity if host else ""
        app_identity_changed = bool(
            previous_host is not None
            and host is not None
            and previous_app_identity != current_app_identity
        )
        reused_handle = bool(
            previous_host is not None
            and host is not None
            and previous_handle == current_handle
            and previous_host.process_id > 0
            and host.process_id > 0
            and previous_host.process_id != host.process_id
        )
        if previous_handle != current_handle or reused_handle or app_identity_changed:
            self._foreground_since = current if host else None
            # A manual placement belongs to the foreground application, not
            # one HWND.  Preserve it across another document/window of the
            # same app and across a short unmanaged/Lilies-owned foreground
            # interval.  Only a positively identified different external app
            # restores automatic habitat placement.
            if (
                host is not None
                and self._detached_app_identity is not None
                and current_app_identity != self._detached_app_identity
            ):
                self._detached_app_identity = None
            self._last_candidate = None
            self._last_candidate_handle = current_handle or None
            self.clear_avoidance()
        elif host is not None and self._foreground_since is None:
            self._foreground_since = current
        if (
            host is not None
            and previous_host is not None
            and previous_handle == current_handle
            and (
                host.monitor_id != previous_host.monitor_id
                or host.work_area != previous_host.work_area
                or host.sensitive
                or host.full_screen
                or not host.visible
                or host.minimized
            )
        ):
            # Screen corners are monitor-relative.  Never resurrect an old
            # avoidance point after a monitor transfer, privacy transition or
            # minimize/restore cycle.
            self.clear_avoidance()
        self._host = host

    def update_host(self, value: object, now: float | None = None) -> None:
        self.update_foreground(value, now)

    def detach(self) -> bool:
        if self._host is None:
            # A hostless desktop drag is still an explicit user placement.
            # Keep it authoritative until a real external application becomes
            # foreground; otherwise pointer avoidance can move Lilith again
            # immediately after the drag grace expires.
            self._detached_app_identity = "desktop"
            self.clear_avoidance()
            return True
        self._detached_app_identity = self._host.stable_app_identity
        self.clear_avoidance()
        return True

    def reattach(self) -> bool:
        if self._host is None:
            return False
        self._detached_app_identity = None
        return True

    def remove_host(self, handle: int) -> None:
        if self._host and self._host.handle == int(handle):
            self._host = None
            self._foreground_since = None
            self._last_candidate = None
            self._last_candidate_handle = None
            self.clear_avoidance()

    def handle_event(self, event: WinEvent) -> None:
        host = self._host
        if host is None or host.handle != event.hwnd:
            return
        if event.kind in {WinEventKind.DESTROY, WinEventKind.HIDE}:
            self.remove_host(event.hwnd)
        elif event.kind is WinEventKind.MINIMIZE_START:
            self._host = replace(host, minimized=True)
            self.clear_avoidance()
        elif event.kind is WinEventKind.MINIMIZE_END:
            self._host = replace(host, minimized=False)

    def status(self, now: float | None = None) -> dict[str, object]:
        current = self.clock() if now is None else float(now)
        host = self._host
        stable = (
            0.0
            if host is None or self._foreground_since is None
            else max(0.0, current - self._foreground_since)
        )
        common: dict[str, object] = {
            "floatingMode": self._floating_mode.value,
            "topmost": self._floating_mode is FloatingMode.ALWAYS,
            "noActivate": True,
            "hostHandle": host.handle if host else 0,
            "hostDpiScale": round((host.dpi if host else 96) / 96.0, 4),
            "stableSeconds": round(stable, 3),
            "requiredStableSeconds": self.stable_seconds,
            "attached": False,
            "visible": True,
            "x": round(self.desktop_x, 2),
            "y": round(self.desktop_y, 2),
            "scale": 1.0,
            "characterScale": 1.0,
            "pose": "desktop-prayer",
            "side": "desktop",
            "anchorX": round(self.desktop_x + self.pet_width / 2, 2),
            "anchorY": round(self.desktop_y + self.pet_height, 2),
            "profile": "desktop",
            "windowSizeClass": "desktop",
            "habitatStrategy": "desktop",
            "anchorNormX": 0.5,
            "anchorNormY": 1.0,
            "contactX": 0.5,
            "contactY": 1.0,
            "mirror": False,
            "titleBarHeight": 0.0,
            "poseVariant": "desktop-prayer",
            "motionStyle": "quiet-breathe",
            "motionPeriod": 3.4,
            "peekFraction": 1.0,
        }
        if self._presence is PresenceMode.BLOCKED:
            common.update(state="blocked", reason="sensitive-window", visible=False)
            return common
        if self._presence is PresenceMode.SILENT or (host and host.full_screen):
            common.update(state="silent", reason="full-screen", visible=False)
            return common
        if host is None:
            if self._detached_app_identity is not None:
                common.update(state="detached", reason="manual-detach-unmanaged")
                return common
            if self._avoidance_candidate is not None and self._avoidance_handle == 0:
                common.update(self._avoidance_candidate.to_dict())
                common.update(state="avoiding", reason="pointer-avoidance", attached=True)
                return common
            common.update(state="desktop", reason="no-host")
            return common
        if host.sensitive:
            common.update(state="blocked", reason="sensitive-window", visible=False)
            return common
        if not host.visible or host.minimized or host.rect is None:
            common.update(state="desktop", reason="host-unavailable")
            return common
        if self._detached_app_identity == host.stable_app_identity:
            common.update(state="detached", reason="manual-detach")
            return common
        if (
            self._avoidance_candidate is not None
            and self._avoidance_handle == host.handle
        ):
            common.update(self._avoidance_candidate.to_dict())
            common.update(state="avoiding", reason="pointer-avoidance", attached=True)
            return common
        if stable < self.stable_seconds:
            common.update(state="waiting", reason="foreground-not-stable")
            return common
        work_area = host.work_area or host.rect
        candidate = choose_habitat_candidate(
            host.rect,
            work_area,
            pet_width=self.pet_width,
            pet_height=self.pet_height,
            maximized=host.maximized,
            preferred_side=self._preferred_side,
            title_bar_height=host.title_bar_height,
            dpi_scale=host.dpi / 96.0,
            previous_profile=(
                self._last_candidate.profile
                if self._last_candidate_handle == host.handle and self._last_candidate
                else ""
            ),
            previous_size_class=(
                self._last_candidate.size_class
                if self._last_candidate_handle == host.handle and self._last_candidate
                else ""
            ),
            previous_side=(
                self._last_candidate.side
                if self._last_candidate_handle == host.handle and self._last_candidate
                else ""
            ),
            previous_pose_variant=(
                self._last_candidate.pose_variant
                if self._last_candidate_handle == host.handle and self._last_candidate
                else ""
            ),
        )
        self._last_candidate = candidate
        self._last_candidate_handle = host.handle
        common.update(candidate.to_dict())
        common.update(state="attached", reason="stable-host", attached=True)
        return common


__all__ = [
    "FloatingMode",
    "HOST_HABITAT_STRATEGIES",
    "HabitatCandidate",
    "HabitatHost",
    "PetHabitatController",
    "PresenceMode",
    "choose_habitat_candidate",
]
