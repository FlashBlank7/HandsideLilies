from __future__ import annotations

import pytest

from lilies.core.pet_habitat import (
    HabitatHost,
    PetHabitatController,
    choose_habitat_candidate,
)
from lilies.core.window_catalog import WindowRecord, WindowRect


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


WORK = WindowRect(0, 0, 1920, 1040)
ULTRAWIDE_WORK = WindowRect(0, 0, 3840, 1200)


def host(handle: int, **changes):
    value = {
        "handle": handle,
        "rect": {"left": 180, "top": 240, "right": 1380, "bottom": 900},
        "workArea": WORK.to_dict(),
        "visible": True,
        "minimized": False,
        "maximized": False,
    }
    value.update(changes)
    return value


def test_normal_window_prefers_top_edge_and_avoids_caption_buttons() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(180, 240, 1380, 900),
        WORK,
        pet_width=150,
        pet_height=250,
    )

    assert candidate.pose == "perch-top"
    assert candidate.anchor_y == 241
    assert candidate.x + 150 * candidate.scale < 1380 - 90


def test_maximized_window_uses_a_screen_edge_peek() -> None:
    candidate = choose_habitat_candidate(
        WORK,
        WORK,
        pet_width=150,
        pet_height=250,
        maximized=True,
    )

    assert candidate.pose == "edge-peek-right"
    assert candidate.side == "right"
    assert candidate.anchor_x == WORK.right
    assert candidate.profile == "maximized-edge"
    assert candidate.mirror is True


@pytest.mark.parametrize(
    ("rect", "expected_size", "expected_profile", "expected_pose", "expected_scale"),
    [
        (
            WindowRect(180, 240, 620, 560),
            "small",
            "small-title",
            "title-sit",
            0.68,
        ),
        (
            WindowRect(180, 240, 1180, 850),
            "medium",
            "medium-perch",
            "perch-top",
            0.84375,
        ),
        (
            WindowRect(80, 220, 1800, 1000),
            "large",
            "large-perch",
            "perch-top",
            0.98125,
        ),
    ],
)
def test_window_size_selects_a_deterministic_pose_profile(
    rect: WindowRect,
    expected_size: str,
    expected_profile: str,
    expected_pose: str,
    expected_scale: float,
) -> None:
    candidate = choose_habitat_candidate(
        rect,
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
    )

    assert candidate.size_class == expected_size
    assert candidate.profile == expected_profile
    assert candidate.pose == expected_pose
    assert candidate.scale == pytest.approx(expected_scale)
    assert 0 <= candidate.anchor_norm_x <= 1
    assert 0 <= candidate.anchor_norm_y <= 1
    assert 0 <= candidate.contact_x <= 1
    assert 0 <= candidate.contact_y <= 1


def test_all_primary_window_classes_have_distinct_runtime_presentations() -> None:
    cases = (
        (
            WindowRect(180, 240, 500, 450), 32, False,
            "micro-window-edge", "micro-corner-grip", "corner-grip",
        ),
        (
            WindowRect(180, 240, 1180, 850), 18, False,
            "narrow-caption-edge", "caption-side-lean", "caption-lean",
        ),
        (
            WindowRect(180, 240, 620, 560), 32, False,
            "small-title", "title-sit-balance", "title-balance",
        ),
        (
            WindowRect(300, 160, 980, 960), 32, False,
            "portrait-title", "portrait-title-watch", "portrait-listen",
        ),
        (
            WindowRect(180, 240, 1180, 850), 32, False,
            "medium-perch", "window-perch", "perch-breathe",
        ),
        (
            WindowRect(80, 220, 1800, 1000), 32, False,
            "large-perch", "wide-window-sprawl", "perch-stretch",
        ),
        (
            WORK, 32, True,
            "maximized-edge", "screen-edge-watch", "screen-watch",
        ),
    )
    candidates = [
        choose_habitat_candidate(
            rect,
            WORK,
            pet_width=150,
            pet_height=250,
            title_bar_height=caption_height,
            maximized=maximized,
        )
        for rect, caption_height, maximized, _profile, _variant, _motion in cases
    ]

    for candidate, expected in zip(candidates, cases):
        assert (
            candidate.profile,
            candidate.pose_variant,
            candidate.motion_style,
        ) == expected[3:]
    assert len({candidate.pose_variant for candidate in candidates}) == len(cases)
    assert len({candidate.motion_style for candidate in candidates}) == len(cases)


def test_portrait_window_uses_title_edge_pose_instead_of_square_prone_pose() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(300, 160, 980, 960),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
    )

    assert candidate.size_class == "medium"
    assert candidate.profile == "portrait-title"
    assert candidate.pose == "title-sit"
    assert candidate.pose_variant == "portrait-title-watch"
    assert candidate.motion_style == "portrait-listen"
    assert candidate.contact_x == pytest.approx(0.56)
    assert candidate.contact_y == pytest.approx(0.76)
    assert candidate.mirror is False


def test_portrait_profile_has_aspect_ratio_hysteresis() -> None:
    portrait = choose_habitat_candidate(
        WindowRect(300, 160, 980, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )
    nearly_square = choose_habitat_candidate(
        WindowRect(300, 160, 1100, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=portrait.profile,
        previous_size_class=portrait.size_class,
        previous_side=portrait.side,
    )
    landscape = choose_habitat_candidate(
        WindowRect(300, 160, 1130, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=nearly_square.profile,
        previous_size_class=nearly_square.size_class,
        previous_side=nearly_square.side,
    )

    assert portrait.profile == "portrait-title"
    assert nearly_square.profile == "portrait-title"
    assert landscape.profile == "medium-perch"


def test_portrait_to_landscape_switch_keeps_global_anchor_continuous() -> None:
    before = choose_habitat_candidate(
        WindowRect(300, 160, 1115, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile="portrait-title",
        previous_size_class="medium",
        previous_side="top",
    )
    after = choose_habitat_candidate(
        WindowRect(300, 160, 1117, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=before.profile,
        previous_size_class=before.size_class,
        previous_side=before.side,
    )

    assert before.profile == "portrait-title"
    assert after.profile == "medium-perch"
    assert abs(after.anchor_x - before.anchor_x) < 2
    assert abs(after.anchor_y - before.anchor_y) < 1


def test_large_perch_keeps_central_contact_while_using_distinct_motion() -> None:
    medium = choose_habitat_candidate(
        WindowRect(180, 240, 1180, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )
    large = choose_habitat_candidate(
        WindowRect(80, 220, 1800, 1000), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )

    assert medium.profile == "medium-perch"
    assert medium.mirror is False
    assert large.profile == "large-perch"
    assert large.mirror is False
    assert large.motion_style == "perch-stretch"
    assert large.motion_style != medium.motion_style
    assert large.contact_x == pytest.approx(0.5)
    assert large.x + 150 * large.anchor_norm_x == pytest.approx(large.anchor_x)


@pytest.mark.parametrize(("width", "height"), ((2000, 600), (3000, 500)))
def test_ultrawide_short_window_uses_the_panorama_pose(
    width: int,
    height: int,
) -> None:
    candidate = choose_habitat_candidate(
        WindowRect(200, 300, 200 + width, 300 + height),
        ULTRAWIDE_WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
    )

    assert candidate.size_class == "large"
    assert candidate.profile == "large-perch"
    assert candidate.strategy == "large"
    assert candidate.pose == "perch-top"
    assert candidate.pose_variant == "panoramic-prone"
    assert candidate.motion_style == "perch-drift"


def test_ultrawide_thresholds_have_width_and_aspect_ratio_hysteresis() -> None:
    def candidate(width: int, height: int, previous=None):
        return choose_habitat_candidate(
            WindowRect(200, 300, 200 + width, 300 + height),
            ULTRAWIDE_WORK,
            pet_width=150,
            pet_height=250,
            title_bar_height=32,
            previous_profile=previous.profile if previous else "",
            previous_size_class=previous.size_class if previous else "",
            previous_side=previous.side if previous else "",
            previous_pose_variant=previous.pose_variant if previous else "",
        )

    before_width = candidate(1759, 500)
    entered_width = candidate(1760, 500)
    held_width = candidate(1680, 500, entered_width)
    left_width = candidate(1679, 500, held_width)

    assert before_width.profile == "medium-perch"
    assert entered_width.pose_variant == "panoramic-prone"
    assert held_width.pose_variant == "panoramic-prone"
    assert left_width.profile == "medium-perch"

    before_aspect = candidate(1760, 587)
    entered_aspect = candidate(1760, 586)
    held_aspect = candidate(1760, 628, entered_aspect)
    left_aspect = candidate(1760, 629, held_aspect)

    assert before_aspect.profile == "medium-perch"
    assert entered_aspect.pose_variant == "panoramic-prone"
    assert held_aspect.pose_variant == "panoramic-prone"
    assert left_aspect.profile == "medium-perch"


def test_ultrawide_enter_overrides_small_history_and_keeps_width_hysteresis() -> None:
    def candidate(width: int, height: int, previous=None):
        return choose_habitat_candidate(
            WindowRect(200, 300, 200 + width, 300 + height),
            ULTRAWIDE_WORK,
            pet_width=150,
            pet_height=250,
            title_bar_height=32,
            previous_profile=previous.profile if previous else "",
            previous_size_class=previous.size_class if previous else "",
            previous_side=previous.side if previous else "",
            previous_pose_variant=previous.pose_variant if previous else "",
        )

    small = candidate(600, 420)
    entered = candidate(1800, 430, small)
    held = candidate(1680, 430, entered)
    left = candidate(1679, 430, held)
    stayed_out = candidate(1680, 430, left)
    reentered = candidate(1760, 430, stayed_out)

    assert (small.size_class, small.profile) == ("small", "small-title")
    assert (
        entered.size_class,
        entered.profile,
        entered.pose_variant,
    ) == ("large", "large-perch", "panoramic-prone")
    assert held.pose_variant == "panoramic-prone"
    assert (left.size_class, left.profile) == ("medium", "medium-perch")
    assert (stayed_out.size_class, stayed_out.profile) == (
        "medium",
        "medium-perch",
    )
    assert reentered.pose_variant == "panoramic-prone"


def test_same_host_resize_from_small_to_ultrawide_updates_controller_history() -> None:
    controller = PetHabitatController(
        stable_seconds=0,
        pet_width=150,
        pet_height=250,
    )

    def resize(width: int, height: int) -> dict[str, object]:
        controller.update_foreground(
            host(
                73,
                rect={
                    "left": 200,
                    "top": 300,
                    "right": 200 + width,
                    "bottom": 300 + height,
                },
                workArea=ULTRAWIDE_WORK.to_dict(),
                titleBarHeight=32,
                appId="panorama-editor.exe",
                processId=7300,
            )
        )
        return controller.status()

    small = resize(600, 420)
    entered = resize(1800, 430)
    held = resize(1680, 430)
    left = resize(1679, 430)
    stayed_out = resize(1680, 430)
    reentered = resize(1760, 430)

    assert (small["windowSizeClass"], small["profile"]) == (
        "small",
        "small-title",
    )
    assert (
        entered["windowSizeClass"],
        entered["profile"],
        entered["poseVariant"],
    ) == ("large", "large-perch", "panoramic-prone")
    assert held["poseVariant"] == "panoramic-prone"
    assert (left["windowSizeClass"], left["profile"]) == (
        "medium",
        "medium-perch",
    )
    assert (stayed_out["windowSizeClass"], stayed_out["profile"]) == (
        "medium",
        "medium-perch",
    )
    assert reentered["poseVariant"] == "panoramic-prone"


def test_narrow_title_bar_uses_nearest_screen_edge_instead_of_covering_controls() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(180, 240, 1180, 850),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=18,
    )

    assert candidate.profile == "narrow-caption-edge"
    assert candidate.pose == "edge-peek-left"
    assert candidate.side == "left"
    assert candidate.mirror is False
    assert candidate.title_bar_height == 18


def test_micro_window_uses_edge_pose_instead_of_covering_its_caption() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(100, 600, 350, 780),
        WORK,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
    )

    assert candidate.profile == "micro-window-edge"
    assert candidate.pose == "edge-peek-left"
    assert candidate.side == "left"
    # The peek remains contextually level with the tiny host instead of using
    # the unrelated maximized-window upper-third resting point.
    assert 600 <= candidate.anchor_y <= 648


def test_constrained_edge_profiles_have_distinct_runtime_pose_contracts() -> None:
    cases = (
        (
            choose_habitat_candidate(
                WORK,
                WORK,
                pet_width=150,
                pet_height=250,
                maximized=True,
                title_bar_height=32,
            ),
            "maximized-edge",
            "screen-edge-watch",
            "screen-watch",
            0.72,
            0.82,
        ),
        (
            choose_habitat_candidate(
                WindowRect(-820, 220, 180, 780),
                WORK,
                pet_width=150,
                pet_height=250,
                title_bar_height=32,
            ),
            "offscreen-window-edge",
            "cautious-return",
            "cautious-peek",
            0.70,
            0.66,
        ),
        (
            choose_habitat_candidate(
                WindowRect(180, 240, 1180, 850),
                WORK,
                pet_width=150,
                pet_height=250,
                title_bar_height=18,
            ),
            "narrow-caption-edge",
            "caption-side-lean",
            "caption-lean",
            0.78,
            0.88,
        ),
        (
            choose_habitat_candidate(
                WindowRect(180, 240, 500, 450),
                WORK,
                pet_width=150,
                pet_height=250,
                title_bar_height=32,
            ),
            "micro-window-edge",
            "micro-corner-grip",
            "corner-grip",
            0.74,
            0.72,
        ),
    )

    variants: set[str] = set()
    motions: set[str] = set()
    presentations: set[tuple[float, float, float]] = set()
    for candidate, profile, variant, motion, peek_fraction, visible_character in cases:
        assert candidate.profile == profile
        assert candidate.pose_variant == variant
        assert candidate.motion_style == motion
        assert candidate.peek_fraction == pytest.approx(peek_fraction)
        # For left-side cases this is the fraction of the actual transparent
        # artwork revealed past the invariant screen-edge contact.  The
        # maximized case is mirrored, hence 1 - contact_x.
        revealed = (
            candidate.contact_x
            if candidate.side == "right"
            else 1.0 - candidate.contact_x
        )
        assert revealed == pytest.approx(visible_character)
        assert candidate.x + 150 * candidate.anchor_norm_x == pytest.approx(
            candidate.anchor_x
        )
        assert candidate.y + 250 * candidate.anchor_norm_y == pytest.approx(
            candidate.anchor_y
        )
        variants.add(candidate.pose_variant)
        motions.add(candidate.motion_style)
        presentations.add(
            (
                candidate.peek_fraction,
                candidate.contact_x,
                candidate.contact_y,
            )
        )

    assert len(variants) == len(cases)
    assert len(motions) == len(cases)
    assert len(presentations) == len(cases)


def test_micro_window_profile_has_resize_hysteresis() -> None:
    micro = choose_habitat_candidate(
        WindowRect(100, 300, 450, 620),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
    )
    still_micro = choose_habitat_candidate(
        WindowRect(100, 300, 490, 620),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
        previous_profile=micro.profile,
        previous_size_class=micro.size_class,
        previous_side=micro.side,
    )
    title_safe = choose_habitat_candidate(
        WindowRect(100, 300, 530, 620),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
        previous_profile=still_micro.profile,
        previous_size_class=still_micro.size_class,
        previous_side=still_micro.side,
    )

    assert micro.profile == "micro-window-edge"
    assert still_micro.profile == "micro-window-edge"
    assert title_safe.profile == "small-title"


def test_micro_edge_anchor_is_stable_on_a_negative_coordinate_monitor() -> None:
    secondary_work = WindowRect(-1920, 0, 0, 1040)
    candidate = choose_habitat_candidate(
        WindowRect(-500, 600, -210, 800),
        secondary_work,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
    )

    assert candidate.profile == "micro-window-edge"
    assert candidate.side == "right"
    assert candidate.anchor_x == 0
    assert 0.0 <= candidate.anchor_norm_x <= 1.0
    assert 0.0 <= candidate.anchor_norm_y <= 1.0
    assert candidate.anchor_y == pytest.approx(632.0)


def test_micro_profile_is_dpi_invariant_for_equivalent_geometry() -> None:
    base = choose_habitat_candidate(
        WindowRect(100, 600, 350, 780),
        WORK,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
        dpi_scale=1.0,
    )
    scaled = choose_habitat_candidate(
        WindowRect(150, 900, 525, 1170),
        WindowRect(0, 0, 2880, 1560),
        pet_width=945,
        pet_height=891,
        title_bar_height=48,
        dpi_scale=1.5,
    )

    assert scaled.profile == base.profile == "micro-window-edge"
    assert scaled.side == base.side
    assert scaled.scale == pytest.approx(base.scale)
    assert scaled.anchor_norm_x == pytest.approx(base.anchor_norm_x)
    assert scaled.anchor_norm_y == pytest.approx(base.anchor_norm_y)


def test_mostly_offscreen_host_uses_a_reachable_edge_anchor() -> None:
    secondary_work = WindowRect(-1920, 0, 0, 1040)
    candidate = choose_habitat_candidate(
        WindowRect(-2970, 396, -834, 865),
        secondary_work,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
    )

    assert candidate.profile == "offscreen-window-edge"
    assert candidate.side == "left"
    assert candidate.anchor_x == secondary_work.left
    assert candidate.x <= candidate.anchor_x <= candidate.x + 630
    assert candidate.y <= candidate.anchor_y <= candidate.y + 594
    assert candidate.x + 630 * candidate.anchor_norm_x == pytest.approx(
        candidate.anchor_x
    )
    assert candidate.y + 594 * candidate.anchor_norm_y == pytest.approx(
        candidate.anchor_y
    )


def test_offscreen_profile_does_not_flap_at_monitor_boundary() -> None:
    initial = choose_habitat_candidate(
        WindowRect(-920, 240, 1080, 850),
        WORK,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
    )
    still_edge = choose_habitat_candidate(
        WindowRect(-600, 240, 1400, 850),
        WORK,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
        previous_profile=initial.profile,
        previous_size_class=initial.size_class,
        previous_side=initial.side,
    )
    comfortably_visible = choose_habitat_candidate(
        WindowRect(-500, 240, 1500, 850),
        WORK,
        pet_width=630,
        pet_height=594,
        title_bar_height=32,
        previous_profile=still_edge.profile,
        previous_size_class=still_edge.size_class,
        previous_side=still_edge.side,
    )

    assert initial.profile == "offscreen-window-edge"
    assert still_edge.profile == "offscreen-window-edge"
    # Once the 2000x610 host is comfortably back on-screen it is still an
    # ultrawide surface.  Leave the defensive edge pose, but preserve the
    # panoramic artwork selected for this aspect ratio instead of falling
    # back to the ordinary medium-window perch.
    assert comfortably_visible.profile == "large-perch"
    assert comfortably_visible.pose_variant == "panoramic-prone"


def test_title_bar_threshold_is_dpi_aware() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(270, 360, 1770, 1275),
        WindowRect(0, 0, 2880, 1560),
        pet_width=225,
        pet_height=375,
        title_bar_height=30,
        dpi_scale=1.5,
    )

    assert candidate.profile == "narrow-caption-edge"
    assert candidate.size_class == "medium"


def test_equivalent_dpi_geometry_keeps_the_same_adaptive_pose_contract() -> None:
    base = choose_habitat_candidate(
        WindowRect(100, 240, 1100, 850),
        WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
        dpi_scale=1.0,
    )
    scaled = choose_habitat_candidate(
        WindowRect(150, 360, 1650, 1275),
        WindowRect(0, 0, 2880, 1560),
        pet_width=225,
        pet_height=375,
        title_bar_height=48,
        dpi_scale=1.5,
    )

    assert scaled.profile == base.profile == "medium-perch"
    assert scaled.size_class == base.size_class == "medium"
    assert scaled.scale == pytest.approx(base.scale)
    assert scaled.anchor_norm_x == pytest.approx(base.anchor_norm_x)
    assert scaled.anchor_norm_y == pytest.approx(base.anchor_norm_y)
    assert (scaled.contact_x, scaled.contact_y) == (base.contact_x, base.contact_y)


@pytest.mark.parametrize("dpi_scale", (1.25, 1.5, 1.75, 2.0))
@pytest.mark.parametrize(("logical_width", "logical_height"), ((2000, 600), (3000, 500)))
def test_ultrawide_pose_is_dpi_invariant_from_125_to_200_percent(
    dpi_scale: float,
    logical_width: int,
    logical_height: int,
) -> None:
    def scaled(value: float) -> int:
        return round(value * dpi_scale)

    logical_rect = WindowRect(
        200,
        300,
        200 + logical_width,
        300 + logical_height,
    )
    base = choose_habitat_candidate(
        logical_rect,
        ULTRAWIDE_WORK,
        pet_width=150,
        pet_height=250,
        title_bar_height=32,
        dpi_scale=1.0,
    )
    candidate = choose_habitat_candidate(
        WindowRect(
            scaled(logical_rect.left),
            scaled(logical_rect.top),
            scaled(logical_rect.right),
            scaled(logical_rect.bottom),
        ),
        WindowRect(
            scaled(ULTRAWIDE_WORK.left),
            scaled(ULTRAWIDE_WORK.top),
            scaled(ULTRAWIDE_WORK.right),
            scaled(ULTRAWIDE_WORK.bottom),
        ),
        pet_width=150 * dpi_scale,
        pet_height=250 * dpi_scale,
        title_bar_height=32 * dpi_scale,
        dpi_scale=dpi_scale,
    )

    assert candidate.size_class == base.size_class == "large"
    assert candidate.profile == base.profile == "large-perch"
    assert candidate.strategy == base.strategy == "large"
    assert candidate.pose_variant == base.pose_variant == "panoramic-prone"
    assert candidate.motion_style == base.motion_style == "perch-drift"
    assert candidate.scale == pytest.approx(base.scale, abs=0.002)
    assert candidate.anchor_norm_x == pytest.approx(base.anchor_norm_x, abs=0.002)
    assert candidate.anchor_norm_y == pytest.approx(base.anchor_norm_y, abs=0.002)
    assert candidate.contact_x == pytest.approx(base.contact_x)
    assert candidate.contact_y == pytest.approx(base.contact_y)


def test_portrait_profile_is_dpi_invariant_for_equivalent_geometry() -> None:
    base = choose_habitat_candidate(
        WindowRect(300, 160, 980, 960), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        dpi_scale=1.0,
    )
    scaled = choose_habitat_candidate(
        WindowRect(450, 240, 1470, 1440),
        WindowRect(0, 0, 2880, 1560),
        pet_width=225,
        pet_height=375,
        title_bar_height=48,
        dpi_scale=1.5,
    )

    assert scaled.profile == base.profile == "portrait-title"
    assert scaled.pose_variant == base.pose_variant == "portrait-title-watch"
    assert scaled.scale == pytest.approx(base.scale)
    assert scaled.contact_x == pytest.approx(base.contact_x)
    assert scaled.contact_y == pytest.approx(base.contact_y)


@pytest.mark.parametrize("dpi_scale", (1.25, 1.5, 1.75, 2.0))
@pytest.mark.parametrize(
    ("logical_rect", "expected_profile"),
    [
        (WindowRect(100, 600, 290, 780), "micro-window-edge"),
        (WindowRect(100, 600, 1000, 730), "micro-window-edge"),
        (WindowRect(300, 160, 980, 960), "portrait-title"),
        (WindowRect(100, 240, 1100, 850), "medium-perch"),
    ],
)
def test_extreme_window_profiles_remain_logically_invariant_from_125_to_200_percent(
    dpi_scale: float,
    logical_rect: WindowRect,
    expected_profile: str,
) -> None:
    def scaled(value: float) -> int:
        return round(value * dpi_scale)

    base = choose_habitat_candidate(
        logical_rect,
        WORK,
        pet_width=385,
        pet_height=363,
        title_bar_height=32,
        dpi_scale=1.0,
    )
    candidate = choose_habitat_candidate(
        WindowRect(
            scaled(logical_rect.left),
            scaled(logical_rect.top),
            scaled(logical_rect.right),
            scaled(logical_rect.bottom),
        ),
        WindowRect(0, 0, scaled(1920), scaled(1040)),
        pet_width=385 * dpi_scale,
        pet_height=363 * dpi_scale,
        title_bar_height=32 * dpi_scale,
        dpi_scale=dpi_scale,
    )

    assert candidate.profile == base.profile == expected_profile
    assert candidate.pose == base.pose
    assert candidate.side == base.side
    assert candidate.scale == pytest.approx(base.scale, abs=0.002)
    assert candidate.anchor_norm_x == pytest.approx(base.anchor_norm_x, abs=0.002)
    assert candidate.anchor_norm_y == pytest.approx(base.anchor_norm_y, abs=0.002)
    assert candidate.contact_x == pytest.approx(base.contact_x)
    assert candidate.contact_y == pytest.approx(base.contact_y)


def test_controller_exposes_pose_layout_contract_for_qml() -> None:
    controller = PetHabitatController(stable_seconds=0, pet_width=150, pet_height=250)
    controller.update_foreground(host(33, titleBarHeight=18))

    state = controller.status()

    assert state["profile"] == "narrow-caption-edge"
    assert state["characterScale"] == state["scale"]
    assert state["windowSizeClass"] == "medium"
    assert state["titleBarHeight"] == 18
    assert {
        "anchorNormX",
        "anchorNormY",
        "contactX",
        "contactY",
        "mirror",
        "poseVariant",
        "motionStyle",
        "motionPeriod",
        "peekFraction",
    } <= set(state)
    assert state["poseVariant"] == "caption-side-lean"
    assert state["motionStyle"] == "caption-lean"


def test_size_class_hysteresis_prevents_pose_flapping_near_thresholds() -> None:
    medium = choose_habitat_candidate(
        WindowRect(100, 240, 1100, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )
    near_small = choose_habitat_candidate(
        WindowRect(100, 240, 730, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=medium.profile,
        previous_size_class=medium.size_class,
        previous_side=medium.side,
    )
    crossed_small = choose_habitat_candidate(
        WindowRect(100, 240, 700, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=near_small.profile,
        previous_size_class=near_small.size_class,
        previous_side=near_small.side,
    )

    assert near_small.profile == "medium-perch"
    assert crossed_small.profile == "small-title"

    large = choose_habitat_candidate(
        WindowRect(100, 200, 1500, 980), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )
    near_medium = choose_habitat_candidate(
        WindowRect(100, 200, 1350, 900), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=large.profile,
        previous_size_class=large.size_class,
        previous_side=large.side,
    )

    assert large.profile == "large-perch"
    assert near_medium.profile == "large-perch"


def test_caption_and_side_hysteresis_stabilize_edge_mirroring() -> None:
    narrow = choose_habitat_candidate(
        WindowRect(180, 240, 1180, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=18,
    )
    still_narrow = choose_habitat_candidate(
        WindowRect(560, 240, 1360, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=24,
        previous_profile=narrow.profile,
        previous_size_class=narrow.size_class,
        previous_side=narrow.side,
    )
    crossed_center = choose_habitat_candidate(
        WindowRect(850, 240, 1650, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=24,
        previous_profile=still_narrow.profile,
        previous_size_class=still_narrow.size_class,
        previous_side=still_narrow.side,
    )
    standard_caption = choose_habitat_candidate(
        WindowRect(850, 240, 1650, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=27,
        previous_profile=crossed_center.profile,
        previous_size_class=crossed_center.size_class,
        previous_side=crossed_center.side,
    )

    assert narrow.side == "left"
    assert still_narrow.profile == "narrow-caption-edge"
    assert still_narrow.side == "left"
    assert still_narrow.mirror is False
    assert crossed_center.side == "right"
    assert crossed_center.mirror is True
    assert standard_caption.profile == "medium-perch"


def test_profile_switch_keeps_the_global_contact_anchor_continuous() -> None:
    before = choose_habitat_candidate(
        WindowRect(100, 240, 779, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile="small-title",
        previous_size_class="small",
        previous_side="top",
    )
    after = choose_habitat_candidate(
        WindowRect(100, 240, 781, 850), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=before.profile,
        previous_size_class=before.size_class,
        previous_side=before.side,
    )

    assert before.profile == "small-title"
    assert after.profile == "medium-perch"
    assert abs(after.anchor_x - before.anchor_x) < 4
    assert abs(after.anchor_y - before.anchor_y) < 1


def test_top_space_fallback_reuses_the_transparent_listening_pose_contract() -> None:
    candidate = choose_habitat_candidate(
        WindowRect(180, 8, 1180, 780), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )

    assert candidate.profile == "top-space-listen"
    assert candidate.pose.startswith("edge-peek-")
    assert candidate.contact_x in {0.30, 0.70}
    assert candidate.contact_y == pytest.approx(0.62)


def test_top_space_hysteresis_prevents_edge_and_perch_flapping() -> None:
    edge = choose_habitat_candidate(
        WindowRect(180, 40, 1180, 780), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
    )
    still_edge = choose_habitat_candidate(
        WindowRect(180, 45, 1180, 785), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=edge.profile,
        previous_size_class=edge.size_class,
        previous_side=edge.side,
    )
    perched = choose_habitat_candidate(
        WindowRect(180, 50, 1180, 790), WORK,
        pet_width=150, pet_height=250, title_bar_height=32,
        previous_profile=still_edge.profile,
        previous_size_class=still_edge.size_class,
        previous_side=still_edge.side,
    )

    assert edge.profile == "top-space-listen"
    assert still_edge.profile == "top-space-listen"
    assert perched.profile == "medium-perch"


def test_detach_preserves_strategy_until_reattach_but_app_switch_resets_it() -> None:
    controller = PetHabitatController(stable_seconds=0, pet_width=150, pet_height=250)
    controller.update_foreground(host(40))
    initial = controller.status()
    assert initial["profile"] == "medium-perch"

    assert controller.detach() is True
    controller.update_foreground(
        host(40, rect={"left": 180, "top": 240, "right": 810, "bottom": 850})
    )
    assert controller.status()["state"] == "detached"
    assert controller.reattach() is True
    reattached = controller.status()
    assert reattached["profile"] == "medium-perch"

    controller.update_foreground(
        host(41, rect={"left": 180, "top": 240, "right": 810, "bottom": 850})
    )
    switched = controller.status()
    assert switched["profile"] == "small-title"


def test_stable_foreground_attaches_after_fifteen_seconds() -> None:
    clock = Clock(100.0)
    controller = PetHabitatController(clock=clock, stable_seconds=15.0)
    controller.update_foreground(host(10))

    assert controller.status()["state"] == "waiting"
    clock.value = 114.99
    assert controller.status()["attached"] is False
    clock.value = 115.0
    state = controller.status()
    assert state["attached"] is True
    assert state["pose"] == "perch-top"


def test_manual_drag_detaches_only_until_the_foreground_app_changes() -> None:
    clock = Clock(0.0)
    controller = PetHabitatController(clock=clock, stable_seconds=15.0)
    controller.update_foreground(
        host(10, appId="wps.exe", processId=101), now=0.0
    )
    clock.value = 20.0
    assert controller.status()["attached"] is True
    assert controller.detach() is True

    # A second document window and even a replacement process from the same
    # normalized app remain in the user's manually detached scope.
    controller.update_foreground(
        host(11, appId="WPS.EXE", processId=202), now=20.0
    )
    assert controller.status(now=20.0)["state"] == "detached"

    # A transient unmanaged/Lilies-owned foreground must not silently start a
    # fresh 15-second countdown that moves the pet under the pointer again.
    controller.update_foreground(None, now=21.0)
    transient = controller.status(now=21.0)
    assert transient["state"] == "detached"
    assert transient["reason"] == "manual-detach-unmanaged"
    controller.update_foreground(
        host(12, appId="wps.exe", processId=303), now=22.0
    )
    assert controller.status(now=40.0)["state"] == "detached"

    # Only a positively identified different external application releases
    # the manual override.
    controller.update_foreground(
        host(20, appId="firefox.exe", processId=404), now=41.0
    )
    assert controller.status(now=41.0)["state"] == "waiting"
    assert controller.status(now=55.99)["state"] == "waiting"
    assert controller.status(now=56.0)["state"] == "attached"


def test_hostless_manual_placement_blocks_avoidance_until_external_app() -> None:
    controller = PetHabitatController(stable_seconds=0.0)

    assert controller.detach() is True
    assert controller.status(now=0.0)["state"] == "detached"
    assert controller.set_avoidance_position(200, 200, WORK, now=10.0) is False

    controller.update_foreground(
        host(30, appId="typora.exe", processId=505), now=11.0
    )
    state = controller.status(now=11.0)
    assert state["state"] == "attached"
    assert state["profile"] == "medium-perch"


def test_reused_hwnd_with_a_new_process_identity_resets_manual_detach() -> None:
    clock = Clock(0.0)
    controller = PetHabitatController(clock=clock, stable_seconds=15.0)
    controller.update_foreground(host(10, processId=101), now=0.0)
    clock.value = 20.0
    assert controller.status()["attached"] is True
    assert controller.detach() is True

    # Native HWND values are reusable.  A different owning process is a real
    # foreground switch even when a missed destroy event leaves the integer
    # handle unchanged.
    controller.update_foreground(host(10, processId=202), now=20.0)
    assert controller.status(now=20.0)["state"] == "waiting"
    assert controller.status(now=34.99)["state"] == "waiting"
    assert controller.status(now=35.0)["state"] == "attached"
    assert controller.detach() is True
    assert controller.status()["state"] == "detached"

    controller.update_foreground(host(10, rect={"left": 200, "top": 260, "right": 1400, "bottom": 920}))
    assert controller.status()["state"] == "detached"
    controller.update_foreground(host(11))
    assert controller.status()["state"] == "waiting"
    clock.value = 35.0
    assert controller.status()["attached"] is True


def test_floating_and_privacy_states_are_explicit() -> None:
    controller = PetHabitatController(stable_seconds=0)
    controller.set_floating_mode("normal")
    controller.update_foreground(host(1, sensitive=True))
    blocked = controller.status()
    assert blocked["state"] == "blocked"
    assert blocked["reason"] == "sensitive-window"
    assert blocked["visible"] is False
    assert blocked["topmost"] is False
    assert blocked["noActivate"] is True

    controller.update_foreground(host(2, fullScreen=True))
    silent = controller.status()
    assert silent["state"] == "silent"
    assert silent["reason"] == "full-screen"
    assert silent["visible"] is False


def test_native_window_record_full_screen_propagates_to_silent_habitat() -> None:
    native = WindowRecord(
        handle=77,
        title="Unknown full-screen application",
        rect=WindowRect(0, 0, 1920, 1080),
        work_area=WORK,
        full_screen=True,
    )

    host_value = HabitatHost.from_value(native)
    assert host_value is not None
    assert host_value.full_screen is True

    controller = PetHabitatController(stable_seconds=0)
    controller.update_foreground(native)
    silent = controller.status()
    assert silent["state"] == "silent"
    assert silent["reason"] == "full-screen"
    assert silent["visible"] is False


def test_pointer_avoidance_is_on_screen_transient_and_has_click_grace() -> None:
    clock = Clock(20.0)
    controller = PetHabitatController(
        clock=clock,
        stable_seconds=0,
        pet_width=420,
        pet_height=360,
    )
    controller.update_foreground(host(9), now=0.0)

    assert controller.set_avoidance_position(1800, 900, WORK, now=20.0) is True
    avoiding = controller.status(now=20.0)
    assert avoiding["state"] == "avoiding"
    assert avoiding["attached"] is True
    assert 0 <= avoiding["x"] <= WORK.right - 420
    assert 0 <= avoiding["y"] <= WORK.bottom - 360
    assert controller.set_avoidance_position(0, 0, WORK, now=22.9) is False
    assert controller.set_avoidance_position(0, 0, WORK, now=23.0) is True
    second = controller.status(now=23.0)
    assert second["poseVariant"] == "pointer-safe-rest"
    assert second["motionStyle"] == "title-balance"

    # The previous target cannot be selected again immediately, preventing a
    # cursor hovering near the threshold from producing A -> B -> A jitter.
    assert controller.set_avoidance_position(1800, 900, WORK, now=26.0) is False
    assert controller.status(now=26.0)["x"] == second["x"]
    assert controller.set_avoidance_position(1800, 900, WORK, now=32.0) is True

    controller.detach()
    assert controller.status(now=23.0)["state"] == "detached"


def test_pointer_avoidance_never_overrides_manual_drag_or_unstable_foreground() -> None:
    clock = Clock(0.0)
    controller = PetHabitatController(
        clock=clock,
        stable_seconds=15,
        pet_width=420,
        pet_height=360,
    )
    controller.update_foreground(host(70), now=0.0)

    assert controller.status(now=10.0)["state"] == "waiting"
    assert controller.set_avoidance_position(0, 0, WORK, now=10.0) is False

    assert controller.status(now=15.0)["state"] == "attached"
    assert controller.detach() is True
    assert controller.set_avoidance_position(1500, 600, WORK, now=20.0) is False
    assert controller.status(now=20.0)["state"] == "detached"

    # Reattaching returns to the deterministic host profile.  No avoidance
    # target was allowed to queue behind the user's manual position.
    assert controller.reattach() is True
    reattached = controller.status(now=20.0)
    assert reattached["state"] == "attached"
    assert reattached["profile"] == "medium-perch"


def test_desktop_drag_and_resize_clear_stale_avoidance_targets() -> None:
    controller = PetHabitatController(
        stable_seconds=0,
        pet_width=420,
        pet_height=360,
        desktop_x=40,
        desktop_y=80,
    )
    assert controller.set_avoidance_position(1400, 600, WORK, now=10.0) is True
    assert controller.status(now=10.0)["state"] == "avoiding"

    controller.set_desktop_position(260, 180)
    dragged = controller.status(now=10.1)
    assert dragged["state"] == "desktop"
    assert (dragged["x"], dragged["y"]) == (260, 180)

    assert controller.set_avoidance_position(1400, 600, WORK, now=20.0) is True
    controller.set_pet_size(600, 500)
    resized = controller.status(now=20.1)
    assert resized["state"] == "desktop"
    assert (controller.pet_width, controller.pet_height) == (600, 500)


def test_avoidance_debounce_resets_on_clock_rollback_and_context_clear() -> None:
    controller = PetHabitatController(
        stable_seconds=0,
        pet_width=420,
        pet_height=360,
    )
    assert controller.set_avoidance_position(1400, 600, WORK, now=100.0) is True

    # A reset injected clock starts a new debounce timeline instead of waiting
    # until the old t=103 deadline is reached again.
    assert controller.set_avoidance_position(0, 0, WORK, now=5.0) is True
    controller.clear_avoidance()
    assert controller.set_avoidance_position(1400, 600, WORK, now=5.1) is True


def test_oversized_pet_does_not_claim_an_impossible_on_screen_avoidance() -> None:
    controller = PetHabitatController(
        stable_seconds=0,
        pet_width=1120,
        pet_height=1056,
    )
    assert WORK.height == 1040
    assert controller.set_avoidance_position(0, 0, WORK, now=10.0) is False
    assert controller.status(now=10.0)["state"] == "desktop"


def test_monitor_and_privacy_transitions_discard_old_avoidance_corner() -> None:
    controller = PetHabitatController(
        stable_seconds=0,
        pet_width=420,
        pet_height=360,
    )
    controller.update_foreground(host(80, monitorId="primary"), now=0.0)
    assert controller.set_avoidance_position(1400, 600, WORK, now=10.0) is True
    assert controller.status(now=10.0)["state"] == "avoiding"

    secondary = WindowRect(-1920, 0, 0, 1040)
    controller.update_foreground(
        host(
            80,
            monitorId="secondary",
            workArea=secondary.to_dict(),
            rect={"left": -1700, "top": 240, "right": -500, "bottom": 900},
        ),
        now=11.0,
    )
    moved = controller.status(now=11.0)
    assert moved["state"] == "attached"
    assert moved["profile"] == "medium-perch"
    assert moved["x"] < 0

    assert controller.set_avoidance_position(-1500, 600, secondary, now=20.0) is True
    controller.update_foreground(
        host(
            80,
            monitorId="secondary",
            workArea=secondary.to_dict(),
            rect={"left": -1700, "top": 240, "right": -500, "bottom": 900},
            fullScreen=True,
        ),
        now=21.0,
    )
    assert controller.status(now=21.0)["state"] == "silent"
    controller.update_foreground(
        host(
            80,
            monitorId="secondary",
            workArea=secondary.to_dict(),
            rect={"left": -1700, "top": 240, "right": -500, "bottom": 900},
        ),
        now=22.0,
    )
    assert controller.status(now=22.0)["state"] == "attached"
