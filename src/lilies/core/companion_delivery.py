from __future__ import annotations


# Companion delivery diagnostics deliberately use a small, content-free
# vocabulary.  Keep this contract shared by the controller that writes the
# journal and the local socket that projects it; duplicating the two sets lets
# a valid lifecycle state make the health endpoint fail after an upgrade.
COMPANION_DELIVERY_STATES = frozenset(
    {
        "idle",
        "waiting-present-ack",
        "presented",
        "suppressed",
        "unread",
        "interacted",
        "dismissed",
        "expired",
    }
)

COMPANION_DELIVERY_REASONS = frozenset(
    {
        "",
        "generated",
        "window-exposed",
        "presentation-ack-timeout",
        "privacy-suppressed",
        "privacy-resumed",
        "unsafe-resume",
        "expired-without-interaction",
        "expired-after-interaction",
        "explicit-dismiss",
        "ambient-dismissed",
        "reply",
        "another",
        "category",
        "detail",
        "snooze",
        "mute-app",
        "open-source",
        "save-moment",
        "move-to-box",
        "reopened",
        "process-restarted-before-read",
        "unread-session-missing",
        "unread-redelivery-exhausted",
        "unread-retention-expired",
        "auto-redelivered",
        "explicit-mark-read",
        "application-policy-tightened",
        "browser-capture-paused",
        "dismissed",
        "legacy-reason-unknown",
    }
)


def normalize_companion_delivery_reason(value: object) -> str:
    """Return one fixed diagnostic code without retaining arbitrary text."""

    reason = str(value or "")[:80]
    if reason in COMPANION_DELIVERY_REASONS:
        return reason
    return "legacy-reason-unknown" if reason else ""
