"""Read-only startup health for one caller-owned Windows process.

No Lilies/Qt imports, process mutation, file writes, component calls or model
requests. The CLI uses only the fixed loopback endpoint and F data root.
Identity is the expected image + exact FILETIME + a held process handle. TCP
ownership is checked on the connected server endpoint, not just its listener.
See Microsoft GetExtendedTcpTable / MIB_TCPROW_OWNER_PID / GetProcessTimes.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import time

PORT = 47631
DATA_ROOT = Path(r"F:\code\Lilies in the box\private-data")
STABLE_SECONDS = 3.0  # Longer than the server's 2 s stale-heartbeat threshold.
MAX_REPLY = 65536


class HealthError(RuntimeError):
    """Messages are fixed reason codes; never echo a token or server reply."""


class NotReady(HealthError):
    pass


def _image_key(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(value))


class WindowsProcess:
    def __init__(self, pid: int, executable: str | Path, created: int):
        if os.name != "nt" or type(pid) is not int or not 0 < pid < 2**32 or type(created) is not int or not 0 < created < 2**64:
            raise HealthError("invalid-process-identity")
        self.pid = pid
        self.handle = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not self.handle:
            raise HealthError("process-unavailable")
        try:
            self.check_alive()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(self.handle, *(ctypes.byref(v) for v in times)):
                raise HealthError("process-time-unavailable")
            actual_created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if not self.kernel.QueryFullProcessImageNameW(self.handle, 0, buffer, ctypes.byref(size)):
                raise HealthError("process-image-unavailable")
            if actual_created != created:
                raise HealthError("process-identity-mismatch:creation")
            if _image_key(buffer.value) != _image_key(executable):
                raise HealthError("process-identity-mismatch:image")
        except BaseException:
            self.close()
            raise

    def check_alive(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result == 0:
            raise HealthError("process-exited")
        if result != 258:
            raise HealthError("process-state-unavailable")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def connection_owner(server_port: int, client_port: int) -> int:
    """Owner of 127.0.0.1:server -> 127.0.0.1:client, only ESTABLISHED.

    DWORD ports/addresses are stored in network order. The x86/x64 Windows
    OWNER_PID row consists of six 32-bit DWORDs (24 bytes), preceded by count.
    """
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                    wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    if api(None, ctypes.byref(size), False, socket.AF_INET, 5, 0) not in (0, 122):
        raise HealthError("connection-owner-unavailable")
    for _ in range(4):
        if not 4 <= size.value <= 16 * 1024 * 1024:
            raise HealthError("connection-table-size-invalid")
        allocated = size.value
        buffer = ctypes.create_string_buffer(allocated)
        result = api(buffer, ctypes.byref(size), False, socket.AF_INET, 5, 0)
        if result == 122:
            continue
        if result != 0 or size.value > allocated:
            raise HealthError("connection-owner-unavailable")
        count = struct.unpack_from("<I", buffer)[0]
        if 4 + count * 24 > allocated:
            raise HealthError("connection-table-invalid")
        loopback = struct.unpack("<I", socket.inet_aton("127.0.0.1"))[0]
        owners = []
        for index in range(count):
            state, local, lp, remote, rp, pid = struct.unpack_from("<6I", buffer, 4 + index * 24)
            if (state == 5 and local == remote == loopback
                    and socket.ntohs(lp & 0xffff) == server_port
                    and socket.ntohs(rp & 0xffff) == client_port):
                owners.append(pid)
        if len(owners) == 1:
            return owners[0]
        if owners:
            raise HealthError("connection-owner-ambiguous")
        raise NotReady("connection-owner-not-ready")
    raise NotReady("connection-table-changing")


def read_token(root: Path) -> str:
    if root.drive.casefold() != "f:" or not root.is_absolute():
        raise HealthError("token-root-invalid")
    path = root / "socket-token.txt"
    try:
        for entry in (path, *path.parents):
            info = entry.lstat()
            if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT or stat.S_ISLNK(info.st_mode):
                raise HealthError("token-path-linked")
        with path.open("rb") as stream:
            payload = stream.read(257)
    except FileNotFoundError:
        raise NotReady("token-not-ready") from None
    except OSError:
        raise HealthError("token-unavailable") from None
    # The server creates token_urlsafe(32), exactly 43 ASCII characters.
    if not re.fullmatch(rb"[A-Za-z0-9_-]{43}", payload.strip()) or len(payload) > 256:
        raise HealthError("token-format-invalid")
    return payload.strip().decode("ascii")


def _timeout(sock, deadline: float, clock):
    remaining = deadline - clock()
    if remaining <= 0:
        raise NotReady("response-timeout")
    sock.settimeout(remaining)


def request_snapshot(process, data_root: Path, *, deadline: float, port: int = PORT,
                     owner=connection_owner, token_reader=read_token, clock=time.monotonic):
    process.check_alive()
    end = min(deadline, clock() + 1.5)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            _timeout(client, end, clock)
            client.connect(("127.0.0.1", port))
            client_port = client.getsockname()[1]
            # Never send the session secret to an unverified listener.
            if owner(port, client_port) != process.pid:
                raise HealthError("wrong-peer-process")
            process.check_alive()
            token = token_reader(data_root)
            request_id = secrets.token_hex(16)
            payload = json.dumps({"id": request_id, "auth": token,
                                  "method": "system.runtime_snapshot", "params": {}}).encode() + b"\n"
            _timeout(client, end, clock)
            client.sendall(payload)
            chunks = bytearray()
            while b"\n" not in chunks:
                _timeout(client, end, clock)
                chunk = client.recv(min(4096, MAX_REPLY + 1 - len(chunks)))
                if not chunk:
                    raise NotReady("response-incomplete")
                chunks.extend(chunk)
                if len(chunks) > MAX_REPLY:
                    raise HealthError("response-too-large")
            raw, trailing = bytes(chunks).split(b"\n", 1)
            if trailing:
                raise HealthError("response-framing-invalid")
            try:
                response = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise HealthError("response-invalid") from None
            if not isinstance(response, dict) or response.get("id") != request_id or response.get("ok") is not True:
                raise NotReady("snapshot-unavailable")
            if owner(port, client_port) != process.pid:
                raise HealthError("peer-changed")
            process.check_alive()
            return response.get("result")
    except (TimeoutError, ConnectionError):
        raise NotReady("connection-not-ready") from None
    except OSError:
        raise NotReady("connection-unavailable") from None


def validate_snapshot(value):
    if not isinstance(value, dict) or type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
        raise HealthError("snapshot-schema-invalid")
    age = value.get("qtHeartbeatAgeMs")
    responsive = value.get("qtResponsive")
    if type(age) is not int or not 0 <= age <= 86400000 or type(responsive) is not bool:
        raise HealthError("heartbeat-invalid")
    if responsive != (age <= 2000):
        raise HealthError("heartbeat-inconsistent")
    if value.get("shellMode") not in ("compact", "visual", "login") or value.get("renderer") not in ("scene2d", "video"):
        raise HealthError("runtime-mode-invalid")
    if not responsive:
        raise NotReady("qt-heartbeat-stale")


def await_health(process, data_root: Path, *, timeout: float = 30.0,
                 probe=request_snapshot, clock=time.monotonic, sleep=time.sleep):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 5 <= timeout <= 60:
        raise HealthError("timeout-invalid")
    start = clock()
    deadline = start + timeout
    ready_since = None
    samples = 0
    last_reason = "startup-not-ready"
    while clock() < deadline:
        process.check_alive()
        try:
            value = probe(process, data_root, deadline=deadline)
            validate_snapshot(value)
            now = clock()
            if now >= deadline:
                break
            if ready_since is None:
                ready_since = now
                samples = 0
            samples += 1
            if now - ready_since >= STABLE_SECONDS:
                process.check_alive()
                return {"schemaVersion": 1, "passed": True, "processId": process.pid,
                        "healthySamples": samples, "stableMilliseconds": round((now - ready_since) * 1000),
                        "elapsedMilliseconds": round((now - start) * 1000),
                        "scope": "Owned-process authenticated Qt startup heartbeat; not visual or performance acceptance"}
        except NotReady as exc:
            ready_since = None
            samples = 0
            last_reason = str(exc)
        sleep(min(0.25, max(0, deadline - clock())))
    raise HealthError("startup-timeout:" + last_reason)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--created-filetime", required=True, type=int)
    args = parser.parse_args(argv)
    process = None
    try:
        process = WindowsProcess(args.pid, args.executable, args.created_filetime)
        result = await_health(process, DATA_ROOT)
    except HealthError as exc:
        result = {"schemaVersion": 1, "passed": False, "reason": str(exc)}
    except Exception:
        result = {"schemaVersion": 1, "passed": False, "reason": "health-check-failed"}
    finally:
        if process is not None:
            process.close()
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
