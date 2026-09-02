from __future__ import annotations

import base64
import hashlib
import re
import socket
import struct
import threading

import pytest

from lilies.connectors import slack_websocket as ws


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def server_frame(opcode: int, payload: bytes = b"", *, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        return bytes((first, length)) + payload
    if length <= 0xFFFF:
        return bytes((first, 126)) + struct.pack("!H", length) + payload
    return bytes((first, 127)) + struct.pack("!Q", length) + payload


def decode_client_frame(frame: bytes) -> tuple[int, bool, bytes]:
    first, second = frame[:2]
    assert first & 0x80
    masked = bool(second & 0x80)
    length = second & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack("!H", frame[offset : offset + 2])[0]
        offset += 2
    elif length == 127:
        length = struct.unpack("!Q", frame[offset : offset + 8])[0]
        offset += 8
    mask = frame[offset : offset + 4]
    offset += 4
    payload = frame[offset : offset + length]
    if masked:
        payload = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
    assert offset + length == len(frame)
    return first & 0x0F, masked, payload


class ScriptedSocket:
    def __init__(self, frames: bytes = b"", *, response: str = "valid") -> None:
        self.frames = frames
        self.response = response
        self.incoming = bytearray()
        self.request = b""
        self.client_frames: list[bytes] = []
        self.events: list[tuple[object, ...]] = []
        self.timeout = None
        self.shutdown_calls = 0
        self.close_calls = 0
        self.fail_text_send = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        if data.startswith(b"GET "):
            self.request = data
            match = re.search(br"Sec-WebSocket-Key: ([^\r]+)", data)
            assert match
            key = match.group(1).decode("ascii")
            accept = base64.b64encode(
                hashlib.sha1((key + GUID).encode("ascii")).digest()
            ).decode("ascii")
            if self.response == "bad-accept":
                accept = "not-the-right-accept"
            if self.response == "redirect":
                response = (
                    "HTTP/1.1 302 Found\r\n"
                    "Location: wss://redirect.invalid/socket\r\n\r\n"
                ).encode("ascii")
            else:
                response = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "uPgRaDe: WebSocket\r\n"
                    "Connection: keep-alive, Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                ).encode("ascii")
            # Deliberately return the first WebSocket frame in the same recv as
            # the HTTP headers to exercise Upgrade over-read preservation.
            self.incoming.extend(response + self.frames)
            return
        opcode, masked, payload = decode_client_frame(data)
        if self.fail_text_send and opcode == 0x1:
            raise OSError("scripted ACK failure")
        self.client_frames.append(data)
        self.events.append(("send", opcode, masked, payload, threading.get_ident()))

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def shutdown(self, _how: int) -> None:
        self.shutdown_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class FakeContext:
    def __init__(self, fake_socket: ScriptedSocket) -> None:
        self.fake_socket = fake_socket
        self.server_hostname = None

    def wrap_socket(self, raw_socket, *, server_hostname: str):
        assert raw_socket is self.fake_socket
        self.server_hostname = server_hostname
        return raw_socket


def install_fake_tls(monkeypatch, fake_socket: ScriptedSocket):
    calls: dict[str, object] = {}
    context = FakeContext(fake_socket)

    def create_connection(address, *, timeout):
        calls["address"] = address
        calls["connect_timeout"] = timeout
        return fake_socket

    def create_default_context(*, purpose):
        calls["purpose"] = purpose
        return context

    monkeypatch.setattr(ws.socket, "create_connection", create_connection)
    monkeypatch.setattr(ws.ssl, "create_default_context", create_default_context)
    return calls, context


def test_fragmented_envelope_is_acked_before_delivery_on_transport_thread(
    monkeypatch,
):
    raw = b'{"type":"events_api","envelope_id":"env-1","payload":{}}'
    split = 17
    frames = b"".join(
        (
            server_frame(0x1, raw[:split], fin=False),
            server_frame(0x9, b"probe"),
            server_frame(0x0, raw[split:]),
            server_frame(0x8, struct.pack("!H", 1000) + b"done"),
        )
    )
    fake = ScriptedSocket(frames)
    calls, context = install_fake_tls(monkeypatch, fake)
    monkeypatch.setattr(ws.secrets, "token_bytes", lambda size: b"\x01" * size)
    closes = []
    main_thread = threading.get_ident()

    def on_text(value: str) -> None:
        fake.events.append(("text", value, threading.get_ident()))

    transport = ws.SlackWebSocketTransport(
        on_text=on_text,
        on_close=lambda code, reason: closes.append((code, reason)),
    )
    transport.start("wss://socket.example.test:444/link?ticket=a%2Fb")

    assert transport.join(2.0)
    assert calls == {
        "address": ("socket.example.test", 444),
        "connect_timeout": 10.0,
        "purpose": ws.ssl.Purpose.SERVER_AUTH,
    }
    assert context.server_hostname == "socket.example.test"
    assert fake.timeout == 1.0
    assert fake.request.startswith(b"GET /link?ticket=a%2Fb HTTP/1.1\r\n")
    assert b"Host: socket.example.test:444\r\n" in fake.request
    assert b"User-Agent: lilies-in-the-box/0.3.47\r\n" in fake.request
    assert b"Origin:" not in fake.request

    sends = [event for event in fake.events if event[0] == "send"]
    assert [(event[1], event[2], event[3]) for event in sends] == [
        (0xA, True, b"probe"),
        (0x1, True, b'{"envelope_id":"env-1"}'),
        (0x8, True, struct.pack("!H", 1000) + b"done"),
    ]
    ack_index = next(
        index
        for index, event in enumerate(fake.events)
        if event[:4] == ("send", 0x1, True, b'{"envelope_id":"env-1"}')
    )
    text_index = next(index for index, event in enumerate(fake.events) if event[0] == "text")
    assert ack_index < text_index
    assert fake.events[text_index][1] == raw.decode()
    assert fake.events[text_index][2] != main_thread
    assert fake.events[ack_index][4] == fake.events[text_index][2]
    assert closes == [(1000, "done")]


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        ("bad-accept", "Sec-WebSocket-Accept"),
        ("redirect", "redirects are forbidden"),
    ],
)
def test_invalid_upgrade_is_rejected_without_delivering_text(
    monkeypatch, response, expected_error
):
    fake = ScriptedSocket(server_frame(0x1, b"ignored"), response=response)
    install_fake_tls(monkeypatch, fake)
    errors = []
    texts = []
    closes = []
    transport = ws.SlackWebSocketTransport(
        on_text=texts.append,
        on_error=errors.append,
        on_close=lambda code, reason: closes.append((code, reason)),
    )

    transport.start("wss://socket.example.test/socket")

    assert transport.join(2.0)
    assert texts == []
    assert len(errors) == 1 and expected_error in errors[0]
    assert closes == [(None, "")]
    assert not fake.client_frames


def test_ack_send_failure_prevents_text_delivery(monkeypatch):
    envelope = b'{"type":"events_api","envelope_id":"env-fail"}'
    fake = ScriptedSocket(server_frame(0x1, envelope))
    fake.fail_text_send = True
    install_fake_tls(monkeypatch, fake)
    texts = []
    errors = []
    closes = []
    transport = ws.SlackWebSocketTransport(
        on_text=texts.append,
        on_error=errors.append,
        on_close=lambda code, reason: closes.append((code, reason)),
    )

    transport.start("wss://socket.example.test/socket")

    assert transport.join(2.0)
    assert texts == []
    assert errors == ["scripted ACK failure"]
    assert closes == [(None, "")]


def test_declared_oversized_frame_is_rejected_before_payload_read(monkeypatch):
    # No payload follows this header.  A safe implementation rejects its
    # declared length instead of trying to buffer it.
    oversized_header = b"\x81\x7f" + struct.pack("!Q", 70_000)
    fake = ScriptedSocket(oversized_header)
    install_fake_tls(monkeypatch, fake)
    errors = []
    transport = ws.SlackWebSocketTransport(
        on_error=errors.append,
        max_frame_bytes=64,
    )

    transport.start("wss://socket.example.test/socket")

    assert transport.join(2.0)
    assert errors == ["WebSocket frame exceeds configured size limit"]
    opcode, masked, payload = decode_client_frame(fake.client_frames[0])
    assert (opcode, masked, struct.unpack("!H", payload[:2])[0]) == (0x8, True, 1009)


def test_masked_server_frame_is_rejected_as_protocol_error(monkeypatch):
    mask = b"\x01\x02\x03\x04"
    payload = b"x"
    masked_payload = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
    fake = ScriptedSocket(b"\x81\x81" + mask + masked_payload)
    install_fake_tls(monkeypatch, fake)
    errors = []
    closes = []
    transport = ws.SlackWebSocketTransport(
        on_error=errors.append,
        on_close=lambda code, reason: closes.append((code, reason)),
    )

    transport.start("wss://socket.example.test/socket")

    assert transport.join(2.0)
    assert errors == ["server WebSocket frames must not be masked"]
    assert closes == [(None, "")]
    opcode, client_masked, close_payload = decode_client_frame(fake.client_frames[0])
    assert (opcode, client_masked, struct.unpack("!H", close_payload[:2])[0]) == (
        0x8,
        True,
        1002,
    )


@pytest.mark.parametrize("size", [0, 125, 126, 65_535, 65_536])
def test_client_frame_length_boundaries_are_masked(monkeypatch, size):
    monkeypatch.setattr(ws.secrets, "token_bytes", lambda count: b"\xa5" * count)
    payload = b"x" * size

    opcode, masked, decoded = decode_client_frame(ws._encode_client_frame(0x1, payload))

    assert (opcode, masked, decoded) == (0x1, True, payload)


def test_server_frame_length_boundaries_are_read(monkeypatch):
    sizes = [125, 126, 65_535, 65_536]
    frames = b"".join(server_frame(0x1, b"x" * size) for size in sizes)
    frames += server_frame(0x8, struct.pack("!H", 1000))
    fake = ScriptedSocket(frames)
    install_fake_tls(monkeypatch, fake)
    received_sizes = []
    transport = ws.SlackWebSocketTransport(
        on_text=lambda raw: received_sizes.append(len(raw)),
        max_frame_bytes=70_000,
        max_message_bytes=70_000,
    )

    transport.start("wss://socket.example.test/socket")

    assert transport.join(2.0)
    assert received_sizes == sizes


class BlockingSocket(ScriptedSocket):
    def __init__(self) -> None:
        super().__init__()
        self.unblock = threading.Event()

    def recv(self, size: int) -> bytes:
        if self.incoming:
            return super().recv(size)
        self.unblock.wait(5.0)
        raise OSError("socket shut down")

    def shutdown(self, how: int) -> None:
        super().shutdown(how)
        self.unblock.set()


def test_close_cancels_blocked_receive_and_notifies_once(monkeypatch):
    fake = BlockingSocket()
    install_fake_tls(monkeypatch, fake)
    opened = threading.Event()
    closes = []
    transport = ws.SlackWebSocketTransport(
        on_open=opened.set,
        on_close=lambda code, reason: closes.append((code, reason)),
    )
    transport.start("wss://socket.example.test/socket")
    assert opened.wait(1.0)

    assert transport.close(wait=True, timeout=1.0)
    assert transport.close(wait=True, timeout=1.0)

    assert not transport.running
    assert fake.shutdown_calls >= 1
    assert closes == [(1000, "")]
    opcode, masked, payload = decode_client_frame(fake.client_frames[0])
    assert (opcode, masked, payload) == (0x8, True, struct.pack("!H", 1000))


class HandshakeBlockingSocket(ScriptedSocket):
    def __init__(self) -> None:
        super().__init__()
        self.handshake_cancelled = threading.Event()

    def shutdown(self, how: int) -> None:
        super().shutdown(how)
        self.handshake_cancelled.set()


class HandshakeBlockingContext:
    def __init__(self) -> None:
        self.entered = threading.Event()

    def wrap_socket(self, raw_socket, *, server_hostname: str):
        assert server_hostname == "socket.example.test"
        self.entered.set()
        assert raw_socket.handshake_cancelled.wait(2.0)
        raise OSError("TLS handshake cancelled")


def test_close_can_shutdown_raw_socket_during_tls_handshake(monkeypatch):
    fake = HandshakeBlockingSocket()
    context = HandshakeBlockingContext()
    monkeypatch.setattr(
        ws.socket,
        "create_connection",
        lambda _address, *, timeout: fake,
    )
    monkeypatch.setattr(
        ws.ssl,
        "create_default_context",
        lambda *, purpose: context,
    )
    errors = []
    closes = []
    transport = ws.SlackWebSocketTransport(
        on_error=errors.append,
        on_close=lambda code, reason: closes.append((code, reason)),
    )
    transport.start("wss://socket.example.test/socket")
    assert context.entered.wait(1.0)

    assert transport.close(wait=True, timeout=1.0)

    assert errors == []
    assert closes == [(1000, "")]
    assert fake.handshake_cancelled.is_set()


@pytest.mark.parametrize(
    "url",
    [
        "ws://socket.example.test/socket",
        "https://socket.example.test/socket",
        "wss:///missing-host",
        "wss://user:password@socket.example.test/socket",
        "wss://socket.example.test/socket#fragment",
    ],
)
def test_start_rejects_non_wss_or_unsafe_urls_without_a_thread(url):
    transport = ws.SlackWebSocketTransport()
    with pytest.raises((TypeError, ValueError)):
        transport.start(url)
    assert not transport.running
