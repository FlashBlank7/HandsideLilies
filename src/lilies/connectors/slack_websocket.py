from __future__ import annotations

"""Small RFC 6455 transport for Slack Socket Mode.

The transport deliberately owns only one WebSocket connection.  Reconnect
policy, connection generations, and Qt state belong to ``SlackSocketService``.
All callbacks run on the transport thread; in particular a Slack envelope is
acknowledged before ``on_text`` is called.
"""

import base64
import hashlib
import hmac
import json
import secrets
import socket
import ssl
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit


_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_USER_AGENT = "lilies-in-the-box/0.3.48"
_HEADER_TOKEN_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)


class WebSocketError(RuntimeError):
    """Base class for transport failures."""


class WebSocketHandshakeError(WebSocketError):
    """The peer did not complete a valid WebSocket HTTP Upgrade."""


class WebSocketProtocolError(WebSocketError):
    """The peer sent an invalid or unsupported RFC 6455 frame."""

    def __init__(self, message: str, *, close_code: int = 1002) -> None:
        super().__init__(message)
        self.close_code = int(close_code)


class _Cancelled(Exception):
    pass


@dataclass(frozen=True)
class _Endpoint:
    host: str
    port: int
    host_header: str
    request_target: str


@dataclass(frozen=True)
class _Frame:
    fin: bool
    opcode: int
    payload: bytes


def _noop(*_args: object) -> None:
    return None


class _SocketReader:
    def __init__(
        self,
        sock: Any,
        stop_event: threading.Event,
        initial: bytes = b"",
    ) -> None:
        self.sock = sock
        self.stop_event = stop_event
        self.buffer = bytearray(initial)

    def read_exact(self, size: int) -> bytes:
        if size < 0:
            raise ValueError("read size cannot be negative")
        while len(self.buffer) < size:
            self._receive(min(65_536, max(1, size - len(self.buffer))))
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def read_http_headers(self, limit: int) -> bytes:
        marker = b"\r\n\r\n"
        while True:
            end = self.buffer.find(marker)
            if end >= 0:
                total = end + len(marker)
                if total > limit:
                    raise WebSocketHandshakeError("HTTP Upgrade headers are too large")
                result = bytes(self.buffer[:end])
                del self.buffer[:total]
                return result
            if len(self.buffer) >= limit:
                raise WebSocketHandshakeError("HTTP Upgrade headers are too large")
            self._receive(min(4096, limit - len(self.buffer)))

    def _receive(self, size: int) -> None:
        if self.stop_event.is_set():
            raise _Cancelled()
        try:
            chunk = self.sock.recv(size)
        except socket.timeout:
            if self.stop_event.is_set():
                raise _Cancelled() from None
            return
        except OSError:
            if self.stop_event.is_set():
                raise _Cancelled() from None
            raise
        if not chunk:
            if self.stop_event.is_set():
                raise _Cancelled()
            raise WebSocketError("connection closed without a WebSocket close frame")
        self.buffer.extend(chunk)


class SlackWebSocketTransport:
    """One background-thread WSS connection with in-thread Slack ACKs."""

    def __init__(
        self,
        on_open: Callable[[], None] | None = None,
        on_text: Callable[[str], None] | None = None,
        on_close: Callable[[int | None, str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        *,
        connect_timeout: float = 10.0,
        socket_timeout: float = 1.0,
        max_message_bytes: int = 4 * 1024 * 1024,
        max_frame_bytes: int = 1024 * 1024,
        max_header_bytes: int = 64 * 1024,
    ) -> None:
        if connect_timeout <= 0 or socket_timeout <= 0:
            raise ValueError("socket timeouts must be positive")
        if max_message_bytes <= 0 or max_frame_bytes <= 0:
            raise ValueError("WebSocket size limits must be positive")
        if max_header_bytes < 1024:
            raise ValueError("HTTP header limit is too small")

        self._on_open = on_open or _noop
        self._on_text = on_text or _noop
        self._on_close = on_close or _noop
        self._on_error = on_error or _noop
        self.connect_timeout = float(connect_timeout)
        self.socket_timeout = float(socket_timeout)
        self.max_message_bytes = int(max_message_bytes)
        self.max_frame_bytes = int(max_frame_bytes)
        self.max_header_bytes = int(max_header_bytes)

        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._socket: Any | None = None
        self._connected = False
        self._close_sent = False
        self._close_notified = False
        self._local_close: tuple[int, str] | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def start(self, url: str) -> None:
        endpoint = _parse_endpoint(url)
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("WebSocket transport is already running")
            self._stop_event.clear()
            self._socket = None
            self._connected = False
            self._close_sent = False
            self._close_notified = False
            self._local_close = None
            thread = threading.Thread(
                target=self._run,
                args=(endpoint,),
                name="lilies-slack-websocket",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def join(self, timeout: float | None = None) -> bool:
        with self._state_lock:
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def close(
        self,
        *,
        code: int = 1000,
        reason: str = "",
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool:
        payload = _encode_close_payload(code, reason)
        with self._state_lock:
            if self._local_close is None:
                self._local_close = (int(code), str(reason))
            sock = self._socket
            connected = self._connected
        self._stop_event.set()

        # A non-blocking lock acquisition keeps close cancellable even when a
        # worker send is stuck.  shutdown() below interrupts any blocked I/O.
        if sock is not None and connected and self._send_lock.acquire(blocking=False):
            try:
                self._send_close_unlocked(sock, payload)
            except OSError:
                pass
            finally:
                self._send_lock.release()
        _shutdown_socket(sock)
        if wait:
            return self.join(timeout)
        return not self.running

    def _run(self, endpoint: _Endpoint) -> None:
        peer_close: tuple[int | None, str] | None = None
        sock: Any | None = None
        try:
            sock = self._connect(endpoint)
            reader = self._upgrade(sock, endpoint)
            with self._state_lock:
                self._connected = True
            if self._stop_event.is_set():
                raise _Cancelled()
            self._invoke(self._on_open)
            peer_close = self._read_messages(reader)
        except _Cancelled:
            pass
        except WebSocketProtocolError as exc:
            self._try_protocol_close(exc.close_code, str(exc))
            if not self._stop_event.is_set():
                self._emit_error(str(exc))
        except (OSError, ValueError, WebSocketError) as exc:
            if not self._stop_event.is_set():
                self._emit_error(str(exc))
        except Exception as exc:  # callback/integration boundary
            if not self._stop_event.is_set():
                self._emit_error(f"unexpected WebSocket failure: {exc}")
        finally:
            with self._state_lock:
                current = self._socket
                self._socket = None
                self._connected = False
                local_close = self._local_close
            _shutdown_socket(current if current is not None else sock)
            if peer_close is not None:
                self._notify_close(*peer_close)
            elif local_close is not None:
                self._notify_close(*local_close)
            else:
                self._notify_close(None, "")

    def _connect(self, endpoint: _Endpoint) -> Any:
        raw_socket = socket.create_connection(
            (endpoint.host, endpoint.port),
            timeout=self.connect_timeout,
        )
        # Publish the TCP socket before TLS wrapping.  close() can then mark the
        # connection cancelled and shutdown a handshake that is blocked in
        # wrap_socket(), rather than waiting for the full connect timeout.
        with self._state_lock:
            self._socket = raw_socket
        if self._stop_event.is_set():
            _shutdown_socket(raw_socket)
            raise _Cancelled()
        try:
            context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
            sock = context.wrap_socket(raw_socket, server_hostname=endpoint.host)
        except Exception:
            _shutdown_socket(raw_socket)
            raise
        sock.settimeout(self.socket_timeout)
        with self._state_lock:
            self._socket = sock
        if self._stop_event.is_set():
            raise _Cancelled()
        return sock

    def _upgrade(self, sock: Any, endpoint: _Endpoint) -> _SocketReader:
        nonce = secrets.token_bytes(16)
        key = base64.b64encode(nonce).decode("ascii")
        request = (
            f"GET {endpoint.request_target} HTTP/1.1\r\n"
            f"Host: {endpoint.host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"User-Agent: {_USER_AGENT}\r\n"
            "\r\n"
        ).encode("ascii")
        with self._send_lock:
            sock.sendall(request)

        reader = _SocketReader(sock, self._stop_event)
        header_block = reader.read_http_headers(self.max_header_bytes)
        status, headers = _parse_http_response(header_block)
        if 300 <= status <= 399:
            raise WebSocketHandshakeError("WebSocket redirects are forbidden")
        if status != 101:
            raise WebSocketHandshakeError(
                f"WebSocket Upgrade returned HTTP status {status}"
            )
        if "websocket" not in _header_tokens(headers.get("upgrade", [])):
            raise WebSocketHandshakeError("missing WebSocket Upgrade response header")
        if "upgrade" not in _header_tokens(headers.get("connection", [])):
            raise WebSocketHandshakeError("missing Connection: Upgrade response header")

        accept_values = headers.get("sec-websocket-accept", [])
        if len(accept_values) != 1:
            raise WebSocketHandshakeError("invalid Sec-WebSocket-Accept header count")
        expected = base64.b64encode(
            hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()
        ).decode("ascii")
        if not hmac.compare_digest(accept_values[0].strip(), expected):
            raise WebSocketHandshakeError("invalid Sec-WebSocket-Accept value")
        if headers.get("sec-websocket-extensions"):
            raise WebSocketHandshakeError("server selected an unrequested WebSocket extension")
        if headers.get("sec-websocket-protocol"):
            raise WebSocketHandshakeError("server selected an unrequested WebSocket protocol")
        return reader

    def _read_messages(self, reader: _SocketReader) -> tuple[int | None, str] | None:
        fragments: bytearray | None = None
        while not self._stop_event.is_set():
            frame = self._read_frame(reader)
            if frame.opcode == 0x8:
                code, reason = _decode_close_payload(frame.payload)
                self._send_close_payload(frame.payload)
                return code, reason
            if frame.opcode == 0x9:
                self._send_frame(0xA, frame.payload)
                continue
            if frame.opcode == 0xA:
                continue
            if frame.opcode == 0x2:
                raise WebSocketProtocolError(
                    "binary WebSocket messages are unsupported", close_code=1003
                )
            if frame.opcode == 0x1:
                if fragments is not None:
                    raise WebSocketProtocolError(
                        "new data frame arrived before fragmented message completed"
                    )
                if len(frame.payload) > self.max_message_bytes:
                    raise WebSocketProtocolError(
                        "WebSocket message exceeds configured size limit", close_code=1009
                    )
                if frame.fin:
                    self._deliver_text(frame.payload)
                else:
                    fragments = bytearray(frame.payload)
                continue
            if frame.opcode == 0x0:
                if fragments is None:
                    raise WebSocketProtocolError("unexpected continuation frame")
                if len(fragments) + len(frame.payload) > self.max_message_bytes:
                    raise WebSocketProtocolError(
                        "WebSocket message exceeds configured size limit", close_code=1009
                    )
                fragments.extend(frame.payload)
                if frame.fin:
                    payload = bytes(fragments)
                    fragments = None
                    self._deliver_text(payload)
                continue
            raise WebSocketProtocolError(f"unsupported WebSocket opcode 0x{frame.opcode:x}")
        return None

    def _read_frame(self, reader: _SocketReader) -> _Frame:
        first, second = reader.read_exact(2)
        fin = bool(first & 0x80)
        if first & 0x70:
            raise WebSocketProtocolError("WebSocket RSV bits are set without an extension")
        opcode = first & 0x0F
        if second & 0x80:
            raise WebSocketProtocolError("server WebSocket frames must not be masked")

        payload_length = second & 0x7F
        if payload_length == 126:
            payload_length = struct.unpack("!H", reader.read_exact(2))[0]
            if payload_length < 126:
                raise WebSocketProtocolError("WebSocket frame uses a non-minimal length")
        elif payload_length == 127:
            encoded = reader.read_exact(8)
            if encoded[0] & 0x80:
                raise WebSocketProtocolError("WebSocket frame length has its high bit set")
            payload_length = struct.unpack("!Q", encoded)[0]
            if payload_length <= 0xFFFF:
                raise WebSocketProtocolError("WebSocket frame uses a non-minimal length")

        if opcode >= 0x8:
            if not fin:
                raise WebSocketProtocolError("WebSocket control frames cannot be fragmented")
            if payload_length > 125:
                raise WebSocketProtocolError("WebSocket control frame is too large")
        if payload_length > self.max_frame_bytes:
            raise WebSocketProtocolError(
                "WebSocket frame exceeds configured size limit", close_code=1009
            )
        return _Frame(fin=fin, opcode=opcode, payload=reader.read_exact(payload_length))

    def _deliver_text(self, payload: bytes) -> None:
        try:
            raw = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise WebSocketProtocolError(
                "WebSocket text message is not valid UTF-8", close_code=1007
            ) from exc

        try:
            envelope = json.loads(raw)
        except (TypeError, ValueError):
            envelope = None
        if isinstance(envelope, dict):
            envelope_id = envelope.get("envelope_id")
            if envelope_id:
                ack = json.dumps(
                    {"envelope_id": str(envelope_id)}, separators=(",", ":")
                ).encode("utf-8")
                # A failed ACK raises and intentionally prevents on_text.
                self._send_frame(0x1, ack)
        self._invoke(self._on_text, raw)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        with self._state_lock:
            sock = self._socket
        if sock is None:
            raise WebSocketError("WebSocket is not connected")
        with self._send_lock:
            sock.sendall(_encode_client_frame(opcode, payload))

    def _send_close_payload(self, payload: bytes) -> None:
        with self._state_lock:
            sock = self._socket
        if sock is None:
            return
        with self._send_lock:
            self._send_close_unlocked(sock, payload)

    def _send_close_unlocked(self, sock: Any, payload: bytes) -> None:
        with self._state_lock:
            if self._close_sent:
                return
        sock.sendall(_encode_client_frame(0x8, payload))
        with self._state_lock:
            self._close_sent = True

    def _try_protocol_close(self, code: int, reason: str) -> None:
        try:
            reason_bytes = reason.encode("utf-8")[:123]
            while True:
                try:
                    safe_reason = reason_bytes.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    reason_bytes = reason_bytes[:-1]
            self._send_close_payload(_encode_close_payload(code, safe_reason))
        except (OSError, ValueError, WebSocketError):
            pass

    def _invoke(self, callback: Callable[..., None], *args: object) -> None:
        try:
            callback(*args)
        except Exception as exc:
            self._emit_error(f"WebSocket callback failed: {exc}")

    def _emit_error(self, message: str) -> None:
        try:
            self._on_error(str(message)[:500])
        except Exception:
            pass

    def _notify_close(self, code: int | None, reason: str) -> None:
        with self._state_lock:
            if self._close_notified:
                return
            self._close_notified = True
        try:
            self._on_close(code, reason)
        except Exception as exc:
            self._emit_error(f"WebSocket close callback failed: {exc}")


def _parse_endpoint(url: str) -> _Endpoint:
    if not isinstance(url, str):
        raise TypeError("WebSocket URL must be a string")
    try:
        parts: SplitResult = urlsplit(url)
        explicit_port = parts.port is not None
        port = parts.port or 443
    except ValueError as exc:
        raise ValueError(f"invalid WebSocket URL: {exc}") from exc
    if parts.scheme.lower() != "wss":
        raise ValueError("only wss:// WebSocket URLs are allowed")
    if not parts.hostname:
        raise ValueError("WebSocket URL has no host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("WebSocket URL credentials are not allowed")
    if parts.fragment:
        raise ValueError("WebSocket URL fragments are not allowed")
    if not 1 <= port <= 65535:
        raise ValueError("WebSocket URL port is out of range")

    host = parts.hostname.encode("idna").decode("ascii")
    display_host = f"[{host}]" if ":" in host else host
    host_header = f"{display_host}:{port}" if explicit_port else display_host
    request_target = parts.path or "/"
    if parts.query:
        request_target += "?" + parts.query
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in request_target):
        raise ValueError("WebSocket URL contains control characters")
    try:
        request_target.encode("ascii")
        host_header.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("WebSocket URL must use an ASCII-encoded request target") from exc
    return _Endpoint(host, port, host_header, request_target)


def _parse_http_response(header_block: bytes) -> tuple[int, dict[str, list[str]]]:
    try:
        lines = header_block.decode("iso-8859-1").split("\r\n")
    except UnicodeDecodeError as exc:  # pragma: no cover - iso-8859-1 is total
        raise WebSocketHandshakeError("HTTP Upgrade response is not decodable") from exc
    if not lines or not lines[0]:
        raise WebSocketHandshakeError("HTTP Upgrade response has no status line")
    status_parts = lines[0].split(" ", 2)
    if len(status_parts) < 2 or status_parts[0] != "HTTP/1.1":
        raise WebSocketHandshakeError("HTTP Upgrade response is not HTTP/1.1")
    try:
        status = int(status_parts[1])
    except ValueError as exc:
        raise WebSocketHandshakeError("HTTP Upgrade response has an invalid status") from exc

    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        if not line or line[0].isspace() or ":" not in line:
            raise WebSocketHandshakeError("HTTP Upgrade response has a malformed header")
        name, value = line.split(":", 1)
        if not name or any(character not in _HEADER_TOKEN_CHARS for character in name):
            raise WebSocketHandshakeError("HTTP Upgrade response has an invalid header name")
        headers.setdefault(name.lower(), []).append(value.strip())
    return status, headers


def _header_tokens(values: list[str]) -> set[str]:
    return {
        token.strip().lower()
        for value in values
        for token in value.split(",")
        if token.strip()
    }


def _encode_client_frame(opcode: int, payload: bytes) -> bytes:
    if not 0 <= opcode <= 0x0F:
        raise ValueError("invalid WebSocket opcode")
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length <= 0xFFFF:
        header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
    elif length <= 0x7FFF_FFFF_FFFF_FFFF:
        header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
    else:  # pragma: no cover - Python cannot allocate a payload this large
        raise ValueError("WebSocket payload is too large")
    mask = secrets.token_bytes(4)
    masked = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
    return header + mask + masked


def _valid_close_code(code: int) -> bool:
    if code in {1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010, 1011, 1012, 1013, 1014}:
        return True
    return 3000 <= code <= 4999


def _encode_close_payload(code: int, reason: str) -> bytes:
    if not _valid_close_code(int(code)):
        raise ValueError("invalid WebSocket close code")
    reason_bytes = str(reason).encode("utf-8")
    if len(reason_bytes) > 123:
        raise ValueError("WebSocket close reason is too long")
    return struct.pack("!H", int(code)) + reason_bytes


def _decode_close_payload(payload: bytes) -> tuple[int | None, str]:
    if not payload:
        return None, ""
    if len(payload) == 1:
        raise WebSocketProtocolError("WebSocket close frame has a one-byte payload")
    code = struct.unpack("!H", payload[:2])[0]
    if not _valid_close_code(code):
        raise WebSocketProtocolError("WebSocket close frame has an invalid status code")
    try:
        reason = payload[2:].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise WebSocketProtocolError(
            "WebSocket close reason is not valid UTF-8", close_code=1007
        ) from exc
    return code, reason


def _shutdown_socket(sock: Any | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


__all__ = [
    "SlackWebSocketTransport",
    "WebSocketError",
    "WebSocketHandshakeError",
    "WebSocketProtocolError",
]
