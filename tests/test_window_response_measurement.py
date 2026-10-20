import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def measure(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("window_response_measurement", ROOT / "scripts/measure_window_response.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("ok,error,status", [(1, 0, "acknowledged"), (0, 1460, "timeout"), (0, 5, "denied"), (0, 0, "failed-unknown")])
def test_ping_is_only_zero_parameter_null_and_distinguishes_zero_result(measure, monkeypatch, ok, error, status):
    queries = measure.WindowQueries.__new__(measure.WindowQueries)
    queries.process = SimpleNamespace(pid=123, check_alive=lambda: None)
    queries.owner = lambda hwnd: (123, 456)
    calls = []

    def send(*args):
        calls.append(args[:-1])
        args[-1]._obj.value = 0  # WM_NULL returns zero even on successful handling.
        return ok

    queries.api = SimpleNamespace(SendMessageTimeoutW=send)
    monkeypatch.setattr(measure.ctypes, "set_last_error", lambda _: None)
    monkeypatch.setattr(measure.ctypes, "get_last_error", lambda: error)
    result = queries.ping((789, 456, True))
    assert calls == [(789, 0, 0, 0, 0x23, 75)]
    assert result["status"] == status
    assert result["queryMs"] >= 0
    assert result["error"] == error


@pytest.mark.parametrize("hwnd,owner", [(0, (123, 456)), (0xffff, (123, 456)), (789, (124, 456)), (789, (123, 457))])
def test_no_broadcast_or_changed_window_query(measure, hwnd, owner):
    queries = measure.WindowQueries.__new__(measure.WindowQueries)
    queries.process = SimpleNamespace(pid=123, check_alive=lambda: None)
    queries.owner = lambda _: owner
    queries.api = SimpleNamespace(SendMessageTimeoutW=lambda *a: pytest.fail("no message to unknown target"))
    assert queries.ping((hwnd, 456, True)) == {"status": "target-changed", "queryMs": None}


def test_enumeration_filters_process_and_prefers_visible_per_thread(measure):
    queries = measure.WindowQueries.__new__(measure.WindowQueries)
    queries.process = SimpleNamespace(pid=123, check_alive=lambda: None)
    queries.callback_type = lambda fn: fn
    owners = {101: (123, 10), 102: (123, 10), 103: (321, 20), 104: (123, 30)}
    queries.owner = owners.__getitem__

    def enumerate_windows(callback, unused):
        assert unused == 0
        for handle in owners:
            assert callback(handle, 0)
        return True

    queries.api = SimpleNamespace(EnumWindows=enumerate_windows, IsWindowVisible=lambda hwnd: hwnd in {102, 103})
    assert queries.targets() == [(102, 10, True), (104, 30, False)]


@pytest.mark.parametrize("count,interval", [(0, 1), (41, 1), (True, 1), (3, .01), (3, 10), (3, float("nan"))])
def test_bounds_rejected_before_any_native_query(measure, count, interval):
    with pytest.raises(ValueError, match="bounds"):
        measure.sample(SimpleNamespace(targets=lambda: pytest.fail("invalid request")), count, interval)


def test_probe_reports_separate_costs_and_stops_without_catchup(measure):
    now = [0.0]
    naps = []

    def targets():
        now[0] += .006
        return [(123, 456, True)]

    def ping(target):
        assert target == (123, 456, True)
        now[0] += .021
        return {"status": "acknowledged", "queryMs": 20.0, "error": 0}

    def sleep(seconds):
        naps.append(seconds)
        now[0] += seconds

    report = measure.sample(SimpleNamespace(targets=targets, ping=ping), 3, 1.0, clock=lambda: now[0], sleep=sleep)
    assert naps == [1.0, 1.0]
    assert report["stopReason"] == "sample-count"
    assert report["durationMs"] == 2081.0
    for row in report["samples"]:
        assert row["enumerationMs"] == 6.0
        assert row["targets"][0]["wholeCheckMs"] == 21.0
        assert row["targets"][0]["queryMs"] == 20.0


def test_slow_metadata_call_is_not_misreported_as_message_delay(measure):
    now = [0.0]

    def targets():
        now[0] += 51.0
        return [(123, 456, True)]

    report = measure.sample(SimpleNamespace(targets=targets, ping=lambda _: pytest.fail("past deadline")),
                            3, 1, clock=lambda: now[0], sleep=lambda _: pytest.fail("no new wait"))
    assert report["stopReason"] == "observation-deadline"
    assert report["samples"] == [{"elapsedMs": 0.0, "enumerationMs": 51000.0, "targets": []}]


@pytest.mark.skipif(os.name != "nt", reason="dedicated F artifact boundary")
def test_report_path_is_new_exact_f_artifact_and_never_overwrites(measure, tmp_path, monkeypatch):
    monkeypatch.setattr(measure, "ROOT", tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = artifacts / "window-response-owned.json"
    assert measure.output_path(target) == target
    target.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        measure.output_path(target)
    for bad in (Path("window-response-relative.json"), artifacts / "wrong.json",
                tmp_path / "window-response-outside.json", Path(r"C:\window-response-bad.json")):
        with pytest.raises(ValueError):
            measure.output_path(bad)
    assert target.read_text(encoding="utf-8") == "preserve"


def test_script_has_no_target_mutation_or_private_content_read(measure):
    source = Path(measure.__file__).read_text(encoding="utf-8")
    for forbidden in ("GetWindowText", "SetWindowPos", "SetForegroundWindow", "SendInput",
                      "GetCursorPos", "GetAsyncKeyState", "TerminateProcess", "ReadProcessMemory",
                      "request_existing_instance", "runtime-token", "screenshot"):
        assert forbidden not in source


def test_isolated_cli_loads_helper_but_refuses_invalid_bounds_before_access(measure, tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", measure.__file__, "--pid", "0",
         "--executable", str(tmp_path / "must-not-open.exe"), "--created", "0",
         "--samples", "100", "--output", str(tmp_path / "must-not-create.json")],
        capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 2
    assert b"bounded samples" in result.stderr
    assert not (tmp_path / "must-not-create.json").exists()
