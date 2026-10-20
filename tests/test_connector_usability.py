import json
import os
import subprocess
import sys
from pathlib import Path


def test_connector_optional_navigation_and_per_item_availability(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen", QML_DISABLE_DISK_CACHE="1")
    result = subprocess.run([sys.executable, str(root / "scripts" / "verify_connector_usability.py"), str(tmp_path)],
                            cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8",
                            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["passed"] and len(payload["checks"]) == 7 and not payload["qmlWarnings"]


def test_original_link_slots_report_actual_launch_result_without_live_browser(monkeypatch):
    from types import SimpleNamespace
    import lilies.backend as module
    from lilies.backend import Backend

    statuses, opened = [], []
    connector = SimpleNamespace(open_event=lambda identifier: {"url": "https://example.org/calendar"},
                                open_message=lambda identifier: {"link": "https://example.org/slack"})
    receiver = SimpleNamespace(calendar_connector=connector, slack_connector=connector, _set_status=statuses.append)
    for method in (Backend.calendarOpenEvent, Backend.slackOpenMessage):
        monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or False)
        assert method(receiver, "selected") is False
        assert statuses
        monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
        assert method(receiver, "selected") is True
    assert len(opened) == 4
    receiver.calendar_connector = receiver.slack_connector = None
    assert Backend.calendarOpenEvent(receiver, "selected") is False
    assert Backend.slackOpenMessage(receiver, "selected") is False


def test_slack_first_time_manifest_bootstrap_without_live_services(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONUTF8="1", QT_QPA_PLATFORM="offscreen", QML_DISABLE_DISK_CACHE="1")
    result = subprocess.run([sys.executable, str(root / "scripts" / "verify_slack_bootstrap_ui.py"), str(tmp_path)],
                            cwd=root, env=environment, capture_output=True, text=True, encoding="utf-8",
                            timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["passed"] and len(payload["checks"]) == 6 and not payload["qmlWarnings"]
