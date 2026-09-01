from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lilies.core.activity import (
    ActivityContextService,
    CaptureCancelled,
    CaptureStaging,
    ForegroundContext,
    LowInformationCapture,
    ObservationPolicy,
    SensitiveWindowGuard,
    sanitize_window_title,
)


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class Idle:
    def __init__(self, value: float) -> None:
        self.value = value

    def idle_seconds(self) -> float:
        return self.value


def test_title_status_is_redacted() -> None:
    title = r"paper C:\Users\Alice\secret.pdf https://example.test/a alice@example.test 123456789"
    safe = sanitize_window_title(title)
    assert "Alice" not in safe
    assert "example.test/a" not in safe
    assert "alice@example" not in safe
    assert "123456789" not in safe
    assert {"[path]", "[url]", "[email]", "[number]"} <= set(safe.split())


def test_sensitive_windows_are_blocked_before_per_app_overrides() -> None:
    guard = SensitiveWindowGuard({"bitwarden.exe": ObservationPolicy.ALLOW_BUBBLE})
    password = ForegroundContext(10, process_name="Bitwarden.exe")
    private = ForegroundContext(11, process_name="chrome.exe", title="Private Browsing")
    meeting = ForegroundContext(12, process_name="zoom.exe", title="Weekly call")
    assert guard.evaluate(password).reason == "password-manager"
    assert guard.evaluate(private).reason == "private-browsing"
    assert guard.evaluate(meeting).reason == "meeting"
    assert guard.evaluate(ForegroundContext(13, process_name="LiliesInTheBox.exe")).reason == "assistant-ui"


def test_fullscreen_game_defaults_to_signals_but_has_per_app_controls() -> None:
    context = ForegroundContext(
        3, process_name="game.exe", full_screen=True, is_game=True
    )
    guard = SensitiveWindowGuard()
    assert guard.evaluate(context).policy is ObservationPolicy.SIGNAL_ONLY
    guard.set_policy("game.exe", "observe")
    decision = guard.evaluate(context)
    assert decision.can_capture is True
    assert decision.can_bubble is False
    guard.set_policy("game.exe", "bubble")
    assert guard.evaluate(context).can_bubble is True


def test_activity_requires_stability_natural_pause_and_cooldown() -> None:
    clock = Clock(100.0)
    idle = Idle(10.0)
    service = ActivityContextService(
        context_reader=lambda hwnd: ForegroundContext(hwnd, process_name="wps.exe"),
        idle_provider=idle,
        clock=clock,
        stable_seconds=120.0,
        cooldown_seconds=600.0,
    )
    service.start()
    assert service.update_foreground(
        ForegroundContext(9, process_name="wps.exe", title="Paper")
    ) is True
    assert service.update_foreground(
        ForegroundContext(9, process_name="wps.exe", title="Paper")
    ) is False
    clock.value = 150.0
    assert service.update_foreground(
        ForegroundContext(9, process_name="wps.exe", title="Private title stays ephemeral")
    ) is True
    assert "Private title" not in service.context_identity
    assert "title" not in service.status()["foreground"]
    assert "fingerprint" not in repr(service.status()).casefold()
    assert service.consider_observation().reason == "window-not-stable"

    clock.value = 270.0
    ready = service.consider_observation()
    assert ready.can_capture is True
    assert ready.can_bubble is True
    service.mark_observation_sent()
    assert service.consider_observation().reason == "cooldown"

    clock.value = 871.0
    idle.value = 3.0
    assert service.consider_observation().reason == "user-active"
    idle.value = 61.0
    assert service.consider_observation().reason == "user-away"
    idle.value = 20.0
    assert service.consider_observation().can_capture is True
    status = service.status()
    assert status["lastContextType"] == "active-window-image"
    assert status["requiredStableSeconds"] == 120.0
    assert status["naturalPauseMinimumSeconds"] == 6.0
    assert status["naturalPauseMaximumSeconds"] == 60.0
    assert status["idleSeconds"] == 20.0


def test_same_hwnd_document_title_change_restarts_stability_without_status_leak() -> None:
    clock = Clock(10.0)
    service = ActivityContextService(
        context_reader=lambda hwnd: ForegroundContext(hwnd),
        idle_provider=Idle(10.0),
        clock=clock,
        stable_seconds=120.0,
    )
    service.start()
    assert service.update_foreground(
        ForegroundContext(77, process_name="wps.exe", title="paper-alpha.pdf")
    )
    clock.value = 129.0
    assert service.consider_observation().reason == "window-not-stable"
    assert service.update_foreground(
        ForegroundContext(77, process_name="wps.exe", title="paper-beta.pdf")
    )
    clock.value = 248.0
    assert service.consider_observation().reason == "window-not-stable"
    clock.value = 249.0
    assert service.consider_observation().can_capture is True
    serialized_status = repr(service.status()).casefold()
    assert "paper-alpha" not in serialized_status
    assert "paper-beta" not in serialized_status
    assert "fingerprint" not in serialized_status
    assert "title" not in serialized_status


def test_same_hwnd_title_becoming_empty_also_restarts_stability() -> None:
    clock = Clock(10.0)
    service = ActivityContextService(
        context_reader=lambda hwnd: ForegroundContext(hwnd),
        idle_provider=Idle(10.0),
        clock=clock,
        stable_seconds=120.0,
    )
    service.start()
    assert service.update_foreground(
        ForegroundContext(77, process_name="wps.exe", title="paper-alpha.pdf")
    )
    clock.value = 130.0
    assert service.consider_observation().can_capture is True

    assert service.update_foreground(
        ForegroundContext(77, process_name="wps.exe", title="")
    )
    assert service.consider_observation().reason == "window-not-stable"
    clock.value = 250.0
    assert service.consider_observation().can_capture is True


def test_foreground_update_publishes_privacy_reason_before_next_timer_tick() -> None:
    service = ActivityContextService(
        context_reader=lambda hwnd: ForegroundContext(hwnd),
        idle_provider=Idle(10.0),
    )
    service.start()
    service.update_foreground(
        ForegroundContext(41, process_name="1password.exe", title="Vault")
    )
    assert service.status()["state"] == "password-manager"
    assert service.consider_observation().can_bubble is False


def test_activity_pause_and_signal_only_never_capture() -> None:
    clock = Clock(500.0)
    idle = Idle(20.0)
    service = ActivityContextService(
        context_reader=lambda hwnd: ForegroundContext(hwnd),
        idle_provider=idle,
        clock=clock,
    )
    service.start()
    service.update_foreground(
        ForegroundContext(1, process_name="play.exe", full_screen=True, is_game=True)
    )
    clock.value += 200
    assert service.consider_observation().reason == "signals-only"
    service.set_paused(True)
    assert service.consider_observation().reason == "paused"


def test_capture_staging_resizes_and_always_removes_temporary_file(tmp_path: Path) -> None:
    staging = CaptureStaging(
        tmp_path / "private-data" / "capture-staging",
        tmp_path / "private-data" / "capture-library",
    )

    def capture(_hwnd: int, destination: Path) -> None:
        Image.new("RGB", (3200, 2000), "white").save(destination)

    with staging.prepared(101, capture) as current:
        assert current.path.is_file()
        with Image.open(current.path) as image:
            assert max(image.size) == 1600
        compressed = current.retain_in_memory()
        assert compressed.startswith(b"\x89PNG")
        assert not current.path.exists()
        saved = current.save("paper-moment")
        assert saved.is_file()
    assert not current.path.exists()
    assert saved.is_file()
    current.release()
    assert current._image_bytes is None


def test_capture_staging_cleans_failure(tmp_path: Path) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library")

    def fail(_hwnd: int, destination: Path) -> None:
        destination.write_bytes(b"not-an-image")
        raise RuntimeError("protected content")

    with pytest.raises(RuntimeError, match="protected"):
        staging.stage(2, fail)
    assert list((tmp_path / "staging").glob("*")) == []


def test_capture_staging_cleanup_is_best_effort_for_a_locked_orphan(
    tmp_path: Path, monkeypatch
) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library")
    staging.root.mkdir(parents=True)
    locked = staging.root / ("capture-" + "a" * 32 + ".png")
    removable = staging.root / ("capture-" + "b" * 32 + ".png")
    locked.write_bytes(b"locked")
    removable.write_bytes(b"stale")
    original_unlink = Path.unlink

    def unlink(path: Path, *args, **kwargs):
        if path == locked:
            raise PermissionError("sharing violation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert staging.cleanup_stale(0) == 1
    assert staging.last_cleanup_failures == 1
    assert locked.exists()
    assert not removable.exists()

    # A later pass may retry the quarantined orphan; it was never returned as
    # a staged capture or adopted into the library.
    monkeypatch.setattr(Path, "unlink", original_unlink)
    assert staging.cleanup_stale(0) == 1
    assert staging.last_cleanup_failures == 0
    assert not locked.exists()


def test_capture_stage_preserves_original_error_when_partial_file_is_locked(
    tmp_path: Path, monkeypatch
) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library")
    original_unlink = Path.unlink

    def unlink(path: Path, *args, **kwargs):
        if path.parent == staging.root and path.name.startswith("capture-"):
            raise PermissionError("sharing violation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    def fail(_hwnd: int, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise RuntimeError("capture-source-failed")

    with pytest.raises(RuntimeError, match="capture-source-failed"):
        staging.stage(3, fail)
    assert len(list(staging.root.glob("capture-*.png"))) == 1


def test_stage_image_encodes_on_caller_thread_and_honors_cancellation(tmp_path: Path) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library", 1600)
    image = Image.new("RGB", (3200, 2000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((500, 350, 2600, 1500), outline="#4b5563", width=40)
    draw.line((650, 1250, 1500, 700, 2450, 1050), fill="#7f1d1d", width=55)
    try:
        staged = staging.stage_image(7, image, cancelled=lambda: False)
        with Image.open(staged.path) as encoded:
            assert max(encoded.size) == 1600
        staged.release()
        with pytest.raises(CaptureCancelled):
            staging.stage_image(7, image, cancelled=lambda: True)
        assert list((tmp_path / "staging").glob("*")) == []
    finally:
        image.close()


@pytest.mark.parametrize("color", ["white", "#7f7f7f", "#fdfdfd"])
def test_capture_staging_rejects_uniform_low_information_frames(
    tmp_path: Path, color: str
) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library", 1600)
    image = Image.new("RGB", (800, 600), color)
    try:
        with pytest.raises(LowInformationCapture):
            staging.stage_image(7, image, cancelled=lambda: False)
        assert list((tmp_path / "staging").glob("*")) == []
    finally:
        image.close()


def test_capture_staging_keeps_white_document_with_visible_structure(tmp_path: Path) -> None:
    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library", 1600)
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 90, 680, 510), outline="#4b5563", width=12)
    draw.line((180, 420, 350, 260, 520, 330, 620, 170), fill="#9f1239", width=16)
    try:
        staged = staging.stage_image(7, image, cancelled=lambda: False)
        assert staged.path.is_file()
        staged.release()
    finally:
        image.close()


def test_capture_staging_keeps_sparse_paper_glyphs(tmp_path: Path) -> None:
    """A mostly blank paper page with one thin formula line is still evidence."""

    staging = CaptureStaging(tmp_path / "staging", tmp_path / "library", 1600)
    image = Image.new("RGB", (1600, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.line((710, 600, 890, 600), fill="#30343b", width=2)
    draw.line((800, 540, 800, 660), fill="#30343b", width=2)
    try:
        staged = staging.stage_image(7, image, cancelled=lambda: False)
        assert staged.path.is_file()
        staged.release()
    finally:
        image.close()
