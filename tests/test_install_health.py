"""Synthetic heartbeats, temporary loopback services, own test PID only."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

from test_install_transaction import run_ps, tree  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_health", ROOT / "scripts/verify_install_health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


def snapshot(**changes):
    return {"schemaVersion": 1, "qtHeartbeatAgeMs": 100, "qtResponsive": True,
            "shellMode": "compact", "renderer": "scene2d", **changes}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Process:
    pid = os.getpid()

    def __init__(self):
        self.alive = True

    def check_alive(self):
        if not self.alive:
            raise health.HealthError("process-exited")


@pytest.mark.parametrize("changes", [
    {"schemaVersion": True}, {"schemaVersion": "1"}, {"schemaVersion": 2},
    {"qtHeartbeatAgeMs": True}, {"qtHeartbeatAgeMs": "100"}, {"qtHeartbeatAgeMs": -1},
    {"qtHeartbeatAgeMs": 86400001}, {"qtResponsive": 1},
    {"qtResponsive": False}, {"qtHeartbeatAgeMs": 3000},
    {"shellMode": "arbitrary"}, {"renderer": "unknown"},
], ids=lambda v: next(iter(v)))
def test_invalid_or_inconsistent_snapshot_is_not_health(changes):
    with pytest.raises(health.HealthError):
        health.validate_snapshot(snapshot(**changes))


@pytest.mark.parametrize("timeout", [True, 0, 4, 61, float("inf"), float("nan"), "30"])
def test_bad_timeouts_cannot_remove_bounds(timeout):
    with pytest.raises(health.HealthError, match="timeout-invalid"):
        health.await_health(Process(), Path("unused"), timeout=timeout)


def test_health_requires_sustained_fresh_heartbeat_not_process_or_ping():
    clock = Clock()
    observations = []

    def probe(*args, **kwargs):
        observations.append(clock())
        return snapshot()

    result = health.await_health(Process(), Path("unused"), probe=probe, clock=clock, sleep=clock.sleep)
    assert result["passed"] and result["stableMilliseconds"] >= 3000
    assert result["healthySamples"] >= 13
    assert observations[-1] - observations[0] >= 3


def test_initial_backend_timestamp_without_qt_ticks_does_not_pass():
    clock = Clock()
    def probe(*args, **kwargs):
        age = round(clock() * 1000)
        return snapshot(qtHeartbeatAgeMs=age, qtResponsive=age <= 2000)
    with pytest.raises(health.HealthError, match="startup-timeout:qt-heartbeat-stale"):
        health.await_health(Process(), Path("unused"), timeout=5, probe=probe, clock=clock, sleep=clock.sleep)
    assert clock() == 5


def test_transient_failure_restarts_the_whole_stable_window():
    clock = Clock()
    def probe(*args, **kwargs):
        if clock() == 2:
            raise health.NotReady("token-not-ready")
        return snapshot()
    result = health.await_health(Process(), Path("unused"), probe=probe, clock=clock, sleep=clock.sleep)
    assert result["elapsedMilliseconds"] >= 5250
    assert result["stableMilliseconds"] == 3000


def test_late_valid_response_cannot_cross_overall_deadline():
    clock = Clock()
    def probe(*args, **kwargs):
        clock.now += 6
        return snapshot()
    with pytest.raises(health.HealthError, match="startup-timeout"):
        health.await_health(Process(), Path("unused"), timeout=5, probe=probe, clock=clock, sleep=clock.sleep)


def test_process_death_is_fatal_without_more_requests():
    process, clock = Process(), Clock()
    calls = []
    def probe(*args, **kwargs):
        calls.append(1)
        process.alive = False
        return snapshot()
    with pytest.raises(health.HealthError, match="process-exited"):
        health.await_health(process, Path("unused"), probe=probe, clock=clock, sleep=clock.sleep)
    assert len(calls) == 1


@contextmanager
def server(reply):
    seen, errors = [], []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    assert port != health.PORT
    def handle():
        try:
            listener.settimeout(4)
            with listener.accept()[0] as connection:
                connection.settimeout(4)
                request = bytearray()
                while b"\n" not in request:
                    part = connection.recv(4096)
                    if not part:
                        seen.append(bytes(request))
                        return
                    request.extend(part)
                seen.append(bytes(request))
                data = json.loads(request)
                encoded = reply(data)
                if isinstance(encoded, dict):
                    encoded = json.dumps(encoded).encode() + b"\n"
                connection.sendall(encoded)
                # Keep the established endpoint alive for the second native
                # ownership check, as the production persistent server does.
                try:
                    connection.recv(1)
                except (ConnectionError, TimeoutError):
                    pass
        except ConnectionError:
            pass
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()
    thread = threading.Thread(target=handle, daemon=True)
    thread.start()
    try:
        yield port, seen
    finally:
        thread.join(5)
        assert not thread.is_alive()
        assert not errors, errors


def valid_reply(request):
    return {"id": request["id"], "ok": True, "result": snapshot()}


def test_wrong_owner_receives_no_token_or_request():
    read = []
    with server(valid_reply) as (port, seen):
        with pytest.raises(health.HealthError, match="wrong-peer-process"):
            health.request_snapshot(Process(), Path("unused"), deadline=health.time.monotonic()+3,
                port=port, owner=lambda *_: os.getpid()+1, token_reader=lambda _: read.append(1))
    assert not read and seen == [b""]


def test_owner_change_after_response_cannot_pass():
    owners = iter([os.getpid(), os.getpid()+1])
    with server(valid_reply) as (port, _):
        with pytest.raises(health.HealthError, match="peer-changed"):
            health.request_snapshot(Process(), Path("unused"), deadline=health.time.monotonic()+3,
                port=port, owner=lambda *_: next(owners), token_reader=lambda _: "x"*43)


@pytest.mark.parametrize("kind", ["wrong-id", "unauthorized", "ok-int", "invalid-json", "too-large", "extra-frame"])
def test_untrusted_response_rejected_without_echoing_contents(kind):
    def reply(request):
        if kind == "wrong-id":
            return {**valid_reply(request), "id": "old-response"}
        if kind in {"unauthorized", "ok-int"}:
            return {**valid_reply(request), "ok": False if kind == "unauthorized" else 1, "error": "SECRET-MUST-NOT-LEAK"}
        if kind == "invalid-json":
            return b"SECRET-MUST-NOT-LEAK\n"
        if kind == "too-large":
            return b"x"*(health.MAX_REPLY+1)+b"\n"
        return json.dumps(valid_reply(request)).encode()+b"\n{}\n"
    with server(reply) as (port, _):
        with pytest.raises(health.HealthError) as caught:
            health.request_snapshot(Process(), Path("unused"), deadline=health.time.monotonic()+3,
                port=port, owner=lambda *_: os.getpid(), token_reader=lambda _: "x"*43)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("payload", [b"", b"abc", b"x"*42, b"x"*44, b"x"*300, b"/"*43])
def test_token_is_bounded_and_validated_only_in_f_fixture(tmp_path, payload):
    assert tmp_path.drive.lower() == "f:"
    (tmp_path / "socket-token.txt").write_bytes(payload)
    with pytest.raises(health.HealthError, match="token-format-invalid"):
        health.read_token(tmp_path)


def test_missing_token_retries_and_non_f_root_is_rejected(tmp_path):
    with pytest.raises(health.NotReady, match="token-not-ready"):
        health.read_token(tmp_path)
    with pytest.raises(health.HealthError, match="token-root-invalid"):
        health.read_token(Path(r"C:\not-accessed"))


@pytest.fixture
def own_identity(tree):
    # Independent .NET FILETIME/image view of this exact test process. This
    # hidden child only reads metadata; never queries/starts a Lilies instance.
    identity = run_ps(tree, f"""
$observedProcess = Get-Process -Id {os.getpid()}
Result @{{ executable = $observedProcess.Path; created = $observedProcess.StartTime.ToFileTimeUtc() }}
""")
    # uv's interpreter alias is a directory junction: .NET reports the alias,
    # QueryFullProcessImageName reports the versioned physical image. Supply
    # the canonical expected image; do not weaken the production comparison.
    identity["executable"] = str(Path(identity["executable"]).resolve(strict=True))
    return identity


def test_native_process_and_tcp_owner_with_temporary_authenticated_service(tree, own_identity):
    (tree / "socket-token.txt").write_text("x"*43, "ascii")
    process = health.WindowsProcess(os.getpid(), own_identity["executable"], own_identity["created"])
    try:
        with server(valid_reply) as (port, seen):
            result = health.request_snapshot(process, tree, deadline=health.time.monotonic()+3, port=port)
        health.validate_snapshot(result)
        request = json.loads(seen[0])
        assert request["method"] == "system.runtime_snapshot" and request["params"] == {}
        assert request["auth"] == "x"*43
    finally:
        process.close()


@pytest.mark.parametrize("kind", ["timestamp", "image"])
def test_native_process_identity_mismatch_is_rejected_before_connect(own_identity, kind):
    with pytest.raises(health.HealthError, match="process-identity-mismatch"):
        health.WindowsProcess(os.getpid(), own_identity["executable"] if kind == "timestamp" else r"F:\not-this-process.exe",
                              own_identity["created"] + (1 if kind == "timestamp" else 0))


@pytest.mark.parametrize("expected_peer", ["child", "wrong-process"])
def test_native_tcp_attests_server_process_not_the_client_process(tree, expected_peer):
    # A separate hidden Python fixture makes the two endpoint PIDs different.
    # Testing both endpoints in this pytest process alone would miss a reversed
    # tuple/owner bug. No real Lilies endpoint or session token is accessed.
    script = tree / "socket_fixture.py"
    script.write_text('''import json, socket
with socket.socket() as server:
    server.bind(("127.0.0.1", 0))
    server.listen()
    print(server.getsockname()[1], flush=True)
    server.settimeout(10)
    with server.accept()[0] as client:
        client.settimeout(5)
        data = bytearray()
        while b"\\n" not in data:
            part = client.recv(4096)
            if not part:
                break
            data.extend(part)
        if data:
            request = json.loads(data)
            assert request["auth"] == "x"*43
            assert request["method"] == "system.runtime_snapshot"
            client.sendall(json.dumps({"id":request["id"],"ok":True,"result":{
                "schemaVersion":1,"qtHeartbeatAgeMs":100,"qtResponsive":True,
                "shellMode":"compact","renderer":"scene2d"}}).encode()+b"\\n")
            client.recv(1)
''', "utf-8")
    (tree / "socket-token.txt").write_text("x"*43, "ascii")
    executable = Path(sys._base_executable).resolve(strict=True)
    child = subprocess.Popen([str(executable), "-B", str(script)], cwd=tree,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    process = None
    reads = []
    try:
        port = int(child.stdout.readline())
        assert port != health.PORT
        identity = run_ps(tree, f"""
$observedProcess = Get-Process -Id {child.pid}
Result @{{ created=$observedProcess.StartTime.ToFileTimeUtc() }}
""")
        process = health.WindowsProcess(child.pid, executable, identity["created"])
        def token_reader(root):
            reads.append(True)
            return health.read_token(root)
        if expected_peer == "child":
            result = health.request_snapshot(process, tree, deadline=health.time.monotonic()+3,
                                             port=port, token_reader=token_reader)
            health.validate_snapshot(result)
            assert reads == [True]
        else:
            with pytest.raises(health.HealthError, match="wrong-peer-process"):
                health.request_snapshot(Process(), tree, deadline=health.time.monotonic()+3,
                                        port=port, token_reader=token_reader)
            assert not reads
        assert child.wait(5) == 0, child.stderr.read()
    finally:
        if process is not None:
            process.close()
        if child.poll() is None:
            child.kill()
            child.wait(5)
        child.stdout.close()
        child.stderr.close()
