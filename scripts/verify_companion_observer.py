from __future__ import annotations

"""Deterministic, non-capturing verification of proactive companionship.

The script uses a QCoreApplication, simulated foreground handles and an
explicitly unavailable model adapter.  It never creates a GUI window, reads
the real foreground desktop, captures pixels, uses the network or writes to
the user's Lilies database.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lilies.companion_controller import CompanionController  # noqa: E402
from lilies.core.activity import ForegroundContext  # noqa: E402
from lilies.core.database import Database  # noqa: E402


class Idle:
    def idle_seconds(self) -> float:
        return 10.0


class UnavailableModel:
    model = "unavailable"
    ready = False

    def complete(self, *_args, **_kwargs):
        raise AssertionError("unavailable model must not be invoked")

    def abort(self) -> None:
        pass

    def stop(self) -> None:
        pass


def wait_for(app: QCoreApplication, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(predicate())


def main() -> int:
    app = QCoreApplication.instance() or QCoreApplication([])
    current_hwnd = [202]
    contexts = {
        101: ForegroundContext(
            101, process_id=1, process_name="LiliesInTheBox.exe", title="Settings"
        ),
        202: ForegroundContext(
            202,
            process_id=2,
            process_name="wps.exe",
            title=r"Paper C:\Users\Alice\private.pdf",
            scene_label="论文阅读",
        ),
        303: ForegroundContext(
            303,
            process_id=3,
            process_name="game.exe",
            full_screen=True,
            is_game=True,
        ),
    }
    temp_root = PROJECT_ROOT / ".test-tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="companion-observer-", dir=temp_root) as raw:
        data_root = Path(raw)
        controller = CompanionController(
            Database(data_root / "lilies.db"),
            data_root,
            active=False,
            status_sink=lambda _message: None,
            move_to_box=lambda _payload: None,
            foreground_provider=lambda: current_hwnd[0],
        )
        controller.runtime.luna.stop()
        controller.runtime.luna = UnavailableModel()
        controller.reader = lambda hwnd: contexts[int(hwnd)]
        controller.activity.idle_provider = Idle()
        controller.activity.stable_seconds = 0.0
        controller.activity.cooldown_seconds = 0.0
        controller.activity.start()
        controller.activity.update_foreground(contexts[101])
        try:
            defaults = {
                "activityEnabled": controller._activity_enabled,
                "frequency": controller.preferences["frequency"],
                "smartObservationEnabled": controller.activityStatus[
                    "smartObservationEnabled"
                ],
            }
            controller._consider()
            emitted = wait_for(app, lambda: bool(controller.bubble.get("visible")))
            bubble = controller.bubble
            emitted_status = controller.activityStatus
            reconciled = controller.activity.current_context
            current_hwnd[0] = 303
            controller.updateForegroundContext(contexts[303])
            quiet = {
                "state": controller.activityStatus["state"],
                "label": controller.activityStatus["stateLabel"],
                "bubbleHidden": controller.bubble == {},
            }
            outcome = {
                "passed": bool(
                    defaults["activityEnabled"]
                    and defaults["frequency"] == "balanced"
                    and defaults["smartObservationEnabled"] is False
                    and emitted
                    and bubble.get("model") == "local-safe-fallback"
                    and bubble.get("contextType") == "application-signal"
                    and emitted_status["generationMode"] == "local-safe-fallback"
                    and emitted_status["generationLabel"] == "内置本地陪伴文案"
                    and "主动陪伴仍在运行" in emitted_status["stateDetail"]
                    and emitted_status["lastContextLabel"] == "应用级信号（未截图）"
                    and reconciled is not None
                    and reconciled.hwnd == 202
                    and "private.pdf" not in controller.activity.context_identity
                    and quiet["state"] == "signals-only"
                    and quiet["label"] == "当前应用只使用场景信号"
                    and quiet["bubbleHidden"]
                    and not (data_root / "capture-staging").exists()
                ),
                "defaults": defaults,
                "foregroundReconciledTo": reconciled.process_name if reconciled else "",
                "bubble": {
                    "visible": bool(bubble.get("visible")),
                    "model": bubble.get("model", ""),
                    "contextType": bubble.get("contextType", ""),
                    "contextLabel": emitted_status["lastContextLabel"],
                    "generationMode": emitted_status["generationMode"],
                    "generationLabel": emitted_status["generationLabel"],
                    "degradedButWorking": "主动陪伴仍在运行"
                    in emitted_status["stateDetail"],
                    "summary": bubble.get("summary", ""),
                },
                "fullScreenPrivacy": quiet,
                "captureStagingCreated": (data_root / "capture-staging").exists(),
            }
            print(json.dumps(outcome, ensure_ascii=False, indent=2))
            return 0 if outcome["passed"] else 1
        finally:
            controller.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
