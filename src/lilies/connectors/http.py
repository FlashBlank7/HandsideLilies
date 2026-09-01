"""Small transport boundary shared by connectors.

There is deliberately no urllib/requests implementation here.  Production code
must make a conscious transport choice and tests can inject a deterministic fake.
"""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        if not self.body:
            return {}
        if isinstance(self.body, bytes):
            return json.loads(self.body.decode("utf-8"))
        if isinstance(self.body, str):
            return json.loads(self.body)
        return self.body


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse:
        """Perform one request.  Implementations own timeouts and TLS policy."""


class UrllibHttpTransport:
    """Explicit production transport used only after a connector is enabled."""

    def __init__(self, *, timeout: float = 20.0, max_body_bytes: int = 8 * 1024 * 1024) -> None:
        self.timeout = max(2.0, min(float(timeout), 120.0))
        self.max_body_bytes = max(1024, min(int(max_body_bytes), 32 * 1024 * 1024))
        self._ssl_context = ssl.create_default_context()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        params: Optional[Mapping[str, Any]] = None,
        json_body: Any = None,
        data: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse:
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or not parsed.netloc or parsed.username:
            raise ConnectorTransportError("Connector HTTP transport only permits public HTTPS URLs")
        query = list(parse_qsl(parsed.query, keep_blank_values=True))
        if params:
            query.extend((str(key), str(value)) for key, value in params.items())
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
        body: bytes | None = None
        request_headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if json_body is not None and data is not None:
            raise ValueError("json_body and data are mutually exclusive")
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        elif data is not None:
            body = urlencode({str(key): str(value) for key, value in data.items()}).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        request = Request(target, data=body, headers=request_headers, method=str(method).upper())
        try:
            with urlopen(request, timeout=self.timeout, context=self._ssl_context) as response:
                raw = response.read(self.max_body_bytes + 1)
                if len(raw) > self.max_body_bytes:
                    raise ConnectorTransportError("Connector response exceeded the configured size limit")
                return HttpResponse(int(response.status), raw, dict(response.headers.items()))
        except HTTPError as exc:
            raw = exc.read(self.max_body_bytes + 1)
            return HttpResponse(int(exc.code), raw[: self.max_body_bytes], dict(exc.headers.items()))
        except (OSError, URLError) as exc:
            raise ConnectorTransportError(str(exc)) from exc


class ConnectorTransportError(RuntimeError):
    """Base class for transport-facing connector failures."""


class TransportNotConfiguredError(ConnectorTransportError):
    """Raised rather than silently creating a real network client."""


class ConnectorHttpError(ConnectorTransportError):
    def __init__(self, status_code: int, message: str, response: HttpResponse):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class RateLimitError(ConnectorHttpError):
    def __init__(self, response: HttpResponse):
        raw = response.headers.get("Retry-After", response.headers.get("retry-after", "0"))
        try:
            retry_after = max(0.0, float(raw))
        except (TypeError, ValueError):
            retry_after = 0.0
        self.retry_after_seconds = retry_after
        super().__init__(429, "Connector rate limited", response)


def require_transport(transport: Optional[HttpTransport]) -> HttpTransport:
    if transport is None:
        raise TransportNotConfiguredError(
            "No HTTP transport was injected; connector network access is disabled"
        )
    return transport


def raise_for_status(response: HttpResponse, *, expected: tuple[int, ...]) -> None:
    if response.status_code in expected:
        return
    if response.status_code == 429:
        raise RateLimitError(response)
    raise ConnectorHttpError(
        response.status_code,
        "Unexpected connector HTTP status: %s" % response.status_code,
        response,
    )
