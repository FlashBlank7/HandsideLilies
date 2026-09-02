from __future__ import annotations

"""Small, injectable Win32 layered-window primitive for drag previews.

The module deliberately owns only the temporary proxy window.  It never sends
input, changes another HWND, or decides where the application window belongs.
The native adapter is constructed lazily and all proxy behaviour can be tested
with an injected :class:`Win32DragProxyApi` implementation.

``ARGB32 premultiplied`` follows the Windows DIB convention: each pixel is an
ARGB value, while its byte layout on little-endian Windows is ``B, G, R, A``.
"""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import Any, NamedTuple, Protocol


WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_POPUP = 0x80000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOACTIVATE = 0x0010

WM_NCLBUTTONDOWN = 0x00A1
WM_CANCELMODE = 0x001F
HTCAPTION = 2
VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
SM_SWAPBUTTON = 23
PM_REMOVE = 0x0001
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
SMTO_ERRORONEXIT = 0x0020

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

DEFAULT_PROXY_EX_STYLE = (
    WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST
)
DEFAULT_PROXY_STYLE = WS_POPUP
WINDOWS_AVAILABLE = os.name == "nt"


class _NativeMoveReleaseSentinel:
    """End a posted native move even when its GUI thread is modal-blocked.

    A proxy move starts with a posted ``WM_NCLBUTTONDOWN``.  A very fast
    release can happen before that message is dispatched.  DefWindowProc then
    enters its move loop *after* the only button-up has already passed, which
    also prevents every Qt timer on that GUI thread from running.  This tiny
    daemon observes only the aggregate left-button bit and posts
    ``WM_CANCELMODE`` when it becomes clear.  It never reads coordinates or
    injects input.

    Generation changes and cancellation posts are serialized by one lock.
    Therefore a worker from session N either posts before session N+1's move
    request (FIFO in the same target queue), or sees that it is stale and does
    nothing.  It can never cancel a later session.
    """

    _POLL_SECONDS = 0.008
    _RELAXED_POLL_SECONDS = 0.025
    _FAST_POLL_WINDOW_SECONDS = 0.25
    _MAX_HOLD_SECONDS = 180.0
    _CANCEL_POST_ATTEMPTS = 2
    _CANCEL_RETRY_INITIAL_SECONDS = 0.004
    _CANCEL_RETRY_MAX_SECONDS = 0.100
    _FORCE_CANCEL_AFTER_SECONDS = 0.250
    _FORCE_CANCEL_RETRY_SECONDS = 0.500

    _IDLE = "idle"
    _RESERVED = "reserved"
    _ARMED = "armed"
    _CANCEL_POSTING = "cancel-posting"
    _CANCEL_POSTED = "cancel-posted"
    _POISONED = "poisoned"
    _CLOSED = "closed"

    def __init__(
        self,
        *,
        left_button_is_down: Any,
        post_cancel: Any,
        target_is_valid: Any | None = None,
        force_cancel: Any | None = None,
        monotonic: Any = time.monotonic,
    ) -> None:
        self._left_button_is_down = left_button_is_down
        self._post_cancel = post_cancel
        self._target_is_valid = target_is_valid
        self._force_cancel = force_cancel
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._generation = 0
        self._active_handle = 0
        self._state = self._IDLE
        self._armed_at = 0.0
        self._deadline = 0.0
        self._retire_pending = False
        self._worker: threading.Thread | None = None

    @property
    def active(self) -> bool:
        with self._condition:
            return self._state in {
                self._RESERVED,
                self._ARMED,
                self._CANCEL_POSTING,
                self._CANCEL_POSTED,
            }

    @property
    def poisoned(self) -> bool:
        with self._condition:
            return self._state == self._POISONED

    @property
    def worker_ready(self) -> bool:
        with self._condition:
            worker = self._worker
            return bool(worker is not None and worker.is_alive())

    def state_for(self, handle: int, generation: int) -> str:
        """Return the state only when it still belongs to this exact ticket."""

        target = max(0, int(handle))
        ticket = max(0, int(generation))
        with self._condition:
            if (
                target <= 0
                or ticket <= 0
                or target != self._active_handle
                or ticket != self._generation
            ):
                return "stale"
            return self._state

    def ensure_worker(self) -> bool:
        """Start the persistent observer before a native DOWN can be posted."""

        with self._condition:
            if self._state in {self._CLOSED, self._POISONED}:
                return False
            worker = self._worker
            if worker is not None and worker.is_alive():
                return True
            worker = threading.Thread(
                target=self._watch,
                name="lilies-proxy-release-sentinel",
                daemon=True,
            )
            try:
                worker.start()
            except RuntimeError:
                return False
            self._worker = worker
            return True

    def _post_cancel_bounded(self, target: int) -> bool:
        """Try a small fixed number of non-blocking terminal posts."""

        for _attempt in range(self._CANCEL_POST_ATTEMPTS):
            try:
                if bool(self._post_cancel(target)):
                    return True
            except Exception:
                # A broken adapter is equivalent to PostMessage failure.  The
                # caller moves to POISONED rather than stranding CANCEL_POSTING.
                pass
        return False

    def _target_still_valid(self, target: int) -> bool:
        probe = self._target_is_valid
        if not callable(probe):
            return True
        try:
            return bool(probe(target))
        except Exception:
            # Unknown is not proof that a queued DOWN became harmless.
            return True

    def _force_cancel_bounded(self, target: int) -> bool:
        force = self._force_cancel
        if not callable(force):
            return False
        try:
            return bool(force(target))
        except Exception:
            return False

    def _finish_cancel_posting(
        self,
        handle: int,
        generation: int,
    ) -> None:
        """Retry cancellation off-GUI until queued DOWN cannot strand it.

        PostMessage can transiently fail when a thread queue is under pressure.
        Never convert two adjacent failures into a terminal state: back off on
        this worker, periodically use a bounded SendMessageTimeout fallback,
        and stop only after cancellation succeeds, the HWND is gone, the exact
        generation is retired, or shutdown closes the sentinel.
        """

        delay = self._CANCEL_RETRY_INITIAL_SECONDS
        force_at = self._monotonic() + self._FORCE_CANCEL_AFTER_SECONDS
        while True:
            with self._condition:
                if (
                    generation != self._generation
                    or handle != self._active_handle
                    or self._state != self._CANCEL_POSTING
                ):
                    return
            try:
                posted = bool(self._post_cancel(handle))
            except Exception:
                posted = False
            target_gone = not self._target_still_valid(handle)
            now = self._monotonic()
            forced = False
            if not posted and not target_gone and now >= force_at:
                forced = self._force_cancel_bounded(handle)
                force_at = now + self._FORCE_CANCEL_RETRY_SECONDS
                if not forced:
                    target_gone = not self._target_still_valid(handle)
            if posted or forced or target_gone:
                with self._condition:
                    if (
                        generation == self._generation
                        and handle == self._active_handle
                        and self._state == self._CANCEL_POSTING
                    ):
                        # TARGET_GONE is equally terminal for ordering: there
                        # is no HWND on which the old DOWN could later execute.
                        # A GUI retire received while posting was pending may
                        # become IDLE only now, after this ordering fence.
                        if self._retire_pending:
                            self._active_handle = 0
                            self._state = self._IDLE
                            self._armed_at = 0.0
                            self._deadline = 0.0
                            self._retire_pending = False
                        else:
                            self._state = self._CANCEL_POSTED
                        self._condition.notify_all()
                return
            with self._condition:
                if (
                    generation != self._generation
                    or handle != self._active_handle
                    or self._state != self._CANCEL_POSTING
                ):
                    return
                self._condition.wait(delay)
            delay = min(self._CANCEL_RETRY_MAX_SECONDS, delay * 2.0)

    def reserve(self, handle: int) -> int:
        """Invalidate the previous generation before a new DOWN is posted."""

        target = max(0, int(handle))
        if target <= 0:
            return 0
        with self._condition:
            # CANCEL_POSTED remains owned until the GUI-side terminal path has
            # sampled/committed and explicitly retired its ticket. Reusing the
            # same HWND any earlier would let an old queued cancel cross into a
            # new gesture.
            worker = self._worker
            if (
                self._state != self._IDLE
                or worker is None
                or not worker.is_alive()
            ):
                return 0
            self._generation += 1
            generation = self._generation
            self._active_handle = target
            self._state = self._RESERVED
            self._armed_at = 0.0
            self._deadline = 0.0
            self._retire_pending = False
        return generation

    def commit(self, handle: int, generation: int) -> bool:
        """Start observation only for the matching reserved generation."""

        target = max(0, int(handle))
        ticket = max(0, int(generation))
        with self._condition:
            if (
                target <= 0
                or ticket <= 0
                or ticket != self._generation
                or target != self._active_handle
                or self._state != self._RESERVED
            ):
                return False
            worker = self._worker
            if worker is None or not worker.is_alive():
                # DOWN is already in the queue. Queue CANCEL behind it before
                # returning. Production request_move preflights ensure_worker,
                # so this is only the bounded race/fault recovery path.
                posted = self._post_cancel_bounded(target)
                self._state = self._CANCEL_POSTED if posted else self._POISONED
                self._condition.notify_all()
                return False
            self._state = self._ARMED
            self._armed_at = self._monotonic()
            self._deadline = self._armed_at + self._MAX_HOLD_SECONDS
            self._condition.notify_all()
            return True

    def arm(self, handle: int) -> int:
        """Convenience API for deterministic tests without a posted DOWN."""

        if not self.ensure_worker():
            return 0
        generation = self.reserve(handle)
        if generation <= 0 or not self.commit(handle, generation):
            return 0
        return generation

    def retire(
        self,
        handle: int | None = None,
        generation: int | None = None,
    ) -> bool:
        target = None if handle is None else max(0, int(handle))
        ticket = None if generation is None else max(0, int(generation))
        with self._condition:
            if target is not None and self._active_handle not in {0, target}:
                return False
            if ticket is not None and ticket != self._generation:
                return False
            if self._state == self._POISONED and (
                target is None
                or ticket is None
                or target != self._active_handle
                or ticket != self._generation
            ):
                # A failed async cancel is recoverable only when the GUI-side
                # terminal path proves it owns the exact generation. Broad or
                # stale cleanup must never unpoison somebody else's session.
                return False
            if self._state == self._CANCEL_POSTING:
                if (
                    target is None
                    or ticket is None
                    or target != self._active_handle
                    or ticket != self._generation
                ):
                    return False
                # GUI presentation is terminal, but the ordered CANCEL is not
                # in the queue yet. Keep the ticket reserved until the worker
                # posts it (or proves the target HWND gone), so a new DOWN can
                # never reuse this handle ahead of the old cancel.
                self._retire_pending = True
                self._condition.notify_all()
                return True
            was_active = self._state in {
                self._RESERVED,
                self._ARMED,
                self._CANCEL_POSTING,
                self._CANCEL_POSTED,
                self._POISONED,
            }
            if self._state == self._CLOSED:
                return False
            self._active_handle = 0
            self._state = self._IDLE
            self._armed_at = 0.0
            self._deadline = 0.0
            self._retire_pending = False
            self._condition.notify_all()
            return was_active

    def cancel(self, handle: int, generation: int | None = None) -> bool:
        """Post one ordered cancel and retire the matching generation."""

        target = max(0, int(handle))
        ticket = None if generation is None else max(0, int(generation))
        if target <= 0:
            return False
        with self._condition:
            if ticket is not None and (
                ticket != self._generation or target != self._active_handle
            ):
                return False
            if self._state == self._CANCEL_POSTED:
                return True
            if self._state not in {self._RESERVED, self._ARMED}:
                return False
            self._state = self._CANCEL_POSTING
            posted = self._post_cancel_bounded(target)
            worker = self._worker
            self._state = (
                self._CANCEL_POSTED
                if posted
                else (
                    self._CANCEL_POSTING
                    if worker is not None and worker.is_alive()
                    else self._POISONED
                )
            )
            self._condition.notify_all()
            return posted

    def abort(self, handle: int, generation: int) -> bool:
        """Release a reservation whose DOWN could not be posted."""

        target = max(0, int(handle))
        ticket = max(0, int(generation))
        with self._condition:
            if (
                self._state != self._RESERVED
                or target != self._active_handle
                or ticket != self._generation
            ):
                return False
            self._active_handle = 0
            self._state = self._IDLE
            self._retire_pending = False
            self._condition.notify_all()
            return True

    def discard_failed_post(self, handle: int, generation: int) -> bool:
        """Retire a failed ticket after its queued DOWN is proven unreachable.

        ``request_move`` calls this only after removing the exact DOWN from the
        owner queue or destroying its target HWND.  It deliberately cannot
        retire CANCEL_POSTED because that terminal message must remain owned
        until the ordinary GUI completion path consumes it.
        """

        target = max(0, int(handle))
        ticket = max(0, int(generation))
        with self._condition:
            if (
                target <= 0
                or ticket <= 0
                or target != self._active_handle
                or ticket != self._generation
                or self._state in {self._CANCEL_POSTED, self._CLOSED}
            ):
                return False
            self._active_handle = 0
            self._state = self._IDLE
            self._armed_at = 0.0
            self._deadline = 0.0
            self._retire_pending = False
            self._condition.notify_all()
            return True

    def close(self, timeout: float = 0.25) -> bool:
        with self._condition:
            if self._state == self._CLOSED:
                worker = self._worker
            else:
                self._state = self._CLOSED
                self._active_handle = 0
                self._retire_pending = False
                self._condition.notify_all()
                worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, float(timeout)))
        return bool(worker is None or not worker.is_alive())

    def _watch(self) -> None:
        while True:
            with self._condition:
                while self._state not in {
                    self._ARMED,
                    self._CANCEL_POSTING,
                    self._CLOSED,
                }:
                    self._condition.wait()
                if self._state == self._CLOSED:
                    return
                handle = self._active_handle
                generation = self._generation
                if self._state == self._CANCEL_POSTING:
                    armed_at = 0.0
                    deadline = 0.0
                    retry_cancel = True
                else:
                    retry_cancel = False
                    armed_at = self._armed_at
                    deadline = self._deadline
            if retry_cancel:
                self._finish_cancel_posting(handle, generation)
                continue
            try:
                released = not bool(self._left_button_is_down())
            except Exception:
                # An unavailable aggregate state is unsafe for a posted modal
                # move: cancel rather than allow the transparent pet to vanish.
                released = True
            now = self._monotonic()
            expired = now >= deadline
            if released or expired:
                with self._condition:
                    if (
                        generation != self._generation
                        or handle != self._active_handle
                        or self._state != self._ARMED
                    ):
                        continue
                    self._state = self._CANCEL_POSTING
                    self._condition.notify_all()
                continue
            interval = (
                self._POLL_SECONDS
                if now - armed_at < self._FAST_POLL_WINDOW_SECONDS
                else self._RELAXED_POLL_SECONDS
            )
            with self._condition:
                if (
                    generation == self._generation
                    and handle == self._active_handle
                    and self._state == self._ARMED
                ):
                    self._condition.wait(interval)


class WindowsDragProxyError(RuntimeError):
    """A layered proxy operation could not be completed."""


class WindowsDragProxyUnavailable(WindowsDragProxyError):
    """The native drag proxy is unavailable on this platform."""


class WindowRect(NamedTuple):
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @classmethod
    def from_position(cls, x: int, y: int, width: int, height: int) -> "WindowRect":
        return cls(x, y, x + width, y + height)


class DragDelta(NamedTuple):
    x: int
    y: int

    @property
    def dx(self) -> int:
        return self.x

    @property
    def dy(self) -> int:
        return self.y


class DragProxyFinal(NamedTuple):
    rect: WindowRect
    delta: DragDelta

    @property
    def delta_x(self) -> int:
        return self.delta.x

    @property
    def delta_y(self) -> int:
        return self.delta.y


def _plain_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not -(2**31) <= value <= 2**31 - 1:
        raise ValueError(f"{name} is outside the Win32 coordinate range")
    return value


def _pixel_bytes(value: Any) -> bytes:
    if isinstance(value, str):
        raise TypeError("pixels must be a bytes-like object")
    try:
        view = memoryview(value)
    except TypeError as exc:
        raise TypeError("pixels must be a bytes-like object") from exc
    try:
        return view.cast("B").tobytes()
    except TypeError:
        return view.tobytes()


class ArgbPremultipliedBitmap:
    """Validated top-down ARGB32-premultiplied bitmap.

    ``pixels`` contains BGRA bytes on little-endian Windows.  A stride larger
    than ``width * 4`` is accepted so callers can pass an existing image buffer;
    padding bytes are removed before the DIB upload.
    """

    __slots__ = ("width", "height", "pixels", "stride")

    def __init__(
        self,
        width: int,
        height: int,
        pixels: Any,
        *,
        stride: int | None = None,
        _trusted_premultiplied: bool = False,
    ) -> None:
        width = _plain_int(width, "width")
        height = _plain_int(height, "height")
        if width <= 0 or height <= 0:
            raise ValueError("bitmap dimensions must be positive")
        row_bytes = width * 4
        actual_stride = row_bytes if stride is None else _plain_int(stride, "stride")
        if actual_stride < row_bytes:
            raise ValueError("bitmap stride is smaller than one pixel row")
        data = _pixel_bytes(pixels)
        if len(data) != actual_stride * height:
            raise ValueError("bitmap byte length does not match its dimensions and stride")

        # UpdateLayeredWindow expects colour channels that have already been
        # multiplied by alpha. Public/raw callers keep the strict validation.
        # The idle QQuickItem capture path, however, starts from Qt's explicit
        # Format_ARGB32_Premultiplied conversion. Re-walking a high-DPI image
        # pixel-by-pixel in Python can stall the GUI thread for more than a
        # frame just before a press, so that trusted producer uses the private
        # fast path below while dimensions and byte length remain validated.
        if not _trusted_premultiplied:
            for row in range(height):
                row_start = row * actual_stride
                for offset in range(row_start, row_start + row_bytes, 4):
                    blue, green, red, alpha = data[offset : offset + 4]
                    if blue > alpha or green > alpha or red > alpha:
                        raise ValueError(
                            "bitmap pixels must be premultiplied by alpha"
                        )

        self.width = width
        self.height = height
        self.pixels = data
        self.stride = actual_stride

    @classmethod
    def from_qt_premultiplied(
        cls,
        width: int,
        height: int,
        pixels: Any,
        *,
        stride: int | None = None,
    ) -> "ArgbPremultipliedBitmap":
        """Copy bytes already guaranteed premultiplied by a Qt QImage format.

        This is deliberately narrower than the ordinary constructor: only the
        QImage capture adapter should use it. Raw or external buffers continue
        through the per-channel validation in ``__init__``.
        """

        return cls(
            width,
            height,
            pixels,
            stride=stride,
            _trusted_premultiplied=True,
        )

    @property
    def tight_pixels(self) -> bytes:
        row_bytes = self.width * 4
        if self.stride == row_bytes:
            return self.pixels
        return b"".join(
            self.pixels[row * self.stride : row * self.stride + row_bytes]
            for row in range(self.height)
        )

    @classmethod
    def from_rgba(
        cls,
        width: int,
        height: int,
        pixels: Any,
        *,
        stride: int | None = None,
    ) -> "ArgbPremultipliedBitmap":
        """Convert top-down straight-alpha RGBA bytes to premultiplied BGRA."""

        width = _plain_int(width, "width")
        height = _plain_int(height, "height")
        if width <= 0 or height <= 0:
            raise ValueError("bitmap dimensions must be positive")
        source_stride = width * 4 if stride is None else _plain_int(stride, "stride")
        if source_stride < width * 4:
            raise ValueError("bitmap stride is smaller than one pixel row")
        source = _pixel_bytes(pixels)
        if len(source) != source_stride * height:
            raise ValueError("bitmap byte length does not match its dimensions and stride")

        output = bytearray(width * height * 4)
        destination = 0
        for row in range(height):
            source_row = row * source_stride
            for offset in range(source_row, source_row + width * 4, 4):
                red, green, blue, alpha = source[offset : offset + 4]
                output[destination] = (blue * alpha + 127) // 255
                output[destination + 1] = (green * alpha + 127) // 255
                output[destination + 2] = (red * alpha + 127) // 255
                output[destination + 3] = alpha
                destination += 4
        return cls(width, height, output)

    def __repr__(self) -> str:
        return (
            f"ArgbPremultipliedBitmap(width={self.width}, height={self.height}, "
            f"stride={self.stride}, byte_length={len(self.pixels)})"
        )


class Win32DragProxyApi(Protocol):
    """Injectable boundary around the Win32/GDI calls used by the proxy."""

    def create_window(self, *, ex_style: int, style: int) -> int: ...

    def update_layered_window(
        self,
        handle: int,
        *,
        bitmap: ArgbPremultipliedBitmap,
        x: int,
        y: int,
    ) -> bool: ...

    def show_no_activate(
        self,
        handle: int,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool: ...

    def request_move(
        self,
        handle: int,
        *,
        cursor_position: tuple[int, int] | None,
    ) -> bool | int: ...

    def cancel_move(self, handle: int, generation: int | None = None) -> bool: ...

    def get_window_rect(self, handle: int) -> WindowRect | None: ...

    def hide_window(self, handle: int) -> bool: ...

    def destroy_window(self, handle: int) -> bool: ...


_LRESULT = ctypes.c_ssize_t
_WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", wintypes.BYTE),
        ("rgbGreen", wintypes.BYTE),
        ("rgbRed", wintypes.BYTE),
        ("rgbReserved", wintypes.BYTE),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", wintypes.BYTE),
        ("BlendFlags", wintypes.BYTE),
        ("SourceConstantAlpha", wintypes.BYTE),
        ("AlphaFormat", wintypes.BYTE),
    ]


class NativeWin32DragProxyApi:
    """ctypes Win32 adapter.  Construction has no visible-window side effect."""

    def __init__(self) -> None:
        if not WINDOWS_AVAILABLE or not hasattr(ctypes, "WinDLL"):
            raise WindowsDragProxyUnavailable(
                "the native layered drag proxy is available only on Windows"
            )
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_functions()
        self._instance = int(self._kernel32.GetModuleHandleW(None) or 0)
        if not self._instance:
            self._raise_last_error("GetModuleHandleW")
        self._class_name: str | None = None
        self._window_proc: Any = None
        self._registered = False
        self._handles: set[int] = set()
        self._invalidated_handles: set[int] = set()
        self._move_tickets: dict[int, int] = {}
        self._owner_thread: int | None = None
        self._move_release_sentinel = _NativeMoveReleaseSentinel(
            left_button_is_down=lambda: bool(
                self._user32.GetAsyncKeyState(
                    VK_RBUTTON
                    if self._user32.GetSystemMetrics(SM_SWAPBUTTON)
                    else VK_LBUTTON
                )
                & 0x8000
            ),
            post_cancel=lambda handle: bool(
                self._user32.PostMessageW(handle, WM_CANCELMODE, 0, 0)
            ),
            target_is_valid=lambda handle: bool(
                self._user32.IsWindow(handle)
            ),
            force_cancel=self._force_cancel_with_timeout,
        )
        # Prewarm outside the pointer-press path. request_move never creates an
        # OS thread; if this bounded startup fails it simply declines native
        # movement before posting any DOWN.
        self._move_release_sentinel.ensure_worker()

    def _configure_functions(self) -> None:
        user32 = self._user32
        gdi32 = self._gdi32
        kernel32 = self._kernel32

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = _LRESULT
        user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.WORD
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE),
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            wintypes.DWORD,
            ctypes.POINTER(_BLENDFUNCTION),
            wintypes.DWORD,
        ]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.ReleaseCapture.argtypes = []
        user32.ReleaseCapture.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        user32.SendMessageTimeoutW.restype = ctypes.c_size_t

        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        gdi32.SelectObject.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteObject.restype = wintypes.BOOL

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error = ctypes.get_last_error()
        raise OSError(error, f"{operation} failed")

    def _force_cancel_with_timeout(self, handle: int) -> bool:
        """Bounded cross-thread fallback for a saturated posted queue."""

        result = ctypes.c_size_t()
        return bool(
            self._user32.SendMessageTimeoutW(
                handle,
                WM_CANCELMODE,
                0,
                0,
                SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT,
                100,
                ctypes.byref(result),
            )
        )

    def _check_thread(self) -> None:
        current = threading.get_ident()
        if self._owner_thread is None:
            self._owner_thread = current
        elif self._owner_thread != current:
            raise WindowsDragProxyError("the proxy window must stay on its creating thread")

    def _unregister_class_if_unused(self) -> bool:
        """Release this adapter's unique WNDCLASS after its last HWND."""

        if self._handles:
            return True
        if not getattr(self, "_registered", False):
            return True
        class_name = getattr(self, "_class_name", None)
        if class_name is None:
            return True
        if not self._user32.UnregisterClassW(class_name, self._instance):
            return False
        self._registered = False
        self._class_name = None
        self._window_proc = None
        return True

    def _ensure_window_class(self) -> None:
        if self._registered:
            return
        class_name = (
            f"LiliesLayeredDragProxy_{os.getpid()}_"
            f"{threading.get_native_id()}_{id(self):x}"
        )

        # Point the class directly at User32's native default procedure.  A
        # decorated Python WNDPROC callback would be invoked for every
        # WM_MOVING/WM_WINDOWPOSCHANGING message while User32 owns its modal
        # move loop, recreating the exact C++ -> Python hot path this proxy is
        # meant to remove.  Casting an existing exported function address does
        # not allocate a ctypes callback trampoline; retaining the typed
        # pointer merely keeps the WNDCLASS field and its calling convention
        # explicit for the lifetime of the registered class.
        window_proc = ctypes.cast(self._user32.DefWindowProcW, _WNDPROC)
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = window_proc
        window_class.hInstance = self._instance
        window_class.lpszClassName = class_name
        if not self._user32.RegisterClassW(ctypes.byref(window_class)):
            self._raise_last_error("RegisterClassW")
        self._class_name = class_name
        self._window_proc = window_proc  # Keep the typed native pointer alive.
        self._registered = True

    def create_window(self, *, ex_style: int, style: int) -> int:
        self._check_thread()
        self._ensure_window_class()
        handle = int(
            self._user32.CreateWindowExW(
                ex_style,
                self._class_name,
                "",
                style,
                0,
                0,
                0,
                0,
                None,
                None,
                self._instance,
                None,
            )
            or 0
        )
        if not handle:
            self._raise_last_error("CreateWindowExW")
        self._handles.add(handle)
        return handle

    def update_layered_window(
        self,
        handle: int,
        *,
        bitmap: ArgbPremultipliedBitmap,
        x: int,
        y: int,
    ) -> bool:
        self._check_thread()
        screen_dc = self._user32.GetDC(None)
        if not screen_dc:
            self._raise_last_error("GetDC")
        memory_dc = None
        dib = None
        old_object = None
        try:
            memory_dc = self._gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                self._raise_last_error("CreateCompatibleDC")
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = bitmap.width
            info.bmiHeader.biHeight = -bitmap.height  # top-down DIB
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = BI_RGB
            info.bmiHeader.biSizeImage = bitmap.width * bitmap.height * 4
            bits = ctypes.c_void_p()
            dib = self._gdi32.CreateDIBSection(
                screen_dc,
                ctypes.byref(info),
                DIB_RGB_COLORS,
                ctypes.byref(bits),
                None,
                0,
            )
            if not dib or not bits.value:
                self._raise_last_error("CreateDIBSection")
            tight_pixels = bitmap.tight_pixels
            ctypes.memmove(bits.value, tight_pixels, len(tight_pixels))
            old_object = self._gdi32.SelectObject(memory_dc, dib)
            if not old_object or int(old_object) == -1:
                self._raise_last_error("SelectObject")

            destination = _POINT(x, y)
            size = _SIZE(bitmap.width, bitmap.height)
            source = _POINT(0, 0)
            blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            return bool(
                self._user32.UpdateLayeredWindow(
                    handle,
                    screen_dc,
                    ctypes.byref(destination),
                    ctypes.byref(size),
                    memory_dc,
                    ctypes.byref(source),
                    0,
                    ctypes.byref(blend),
                    ULW_ALPHA,
                )
            )
        finally:
            if memory_dc and old_object and int(old_object) != -1:
                self._gdi32.SelectObject(memory_dc, old_object)
            if dib:
                self._gdi32.DeleteObject(dib)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(None, screen_dc)

    def show_no_activate(
        self,
        handle: int,
        *,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> bool:
        self._check_thread()
        positioned = bool(
            self._user32.SetWindowPos(
                handle,
                None,
                x,
                y,
                width,
                height,
                SWP_NOACTIVATE,
            )
        )
        if positioned:
            # ShowWindow's return reports previous visibility, not success.
            self._user32.ShowWindow(handle, SW_SHOWNOACTIVATE)
        return positioned

    def request_move(
        self,
        handle: int,
        *,
        cursor_position: tuple[int, int] | None,
    ) -> bool | int:
        self._check_thread()
        if cursor_position is None:
            cursor = _POINT()
            if self._user32.GetCursorPos(ctypes.byref(cursor)):
                cursor_position = (int(cursor.x), int(cursor.y))
        lparam = 0
        if cursor_position is not None:
            x, y = cursor_position
            lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        # Reserve the generation before DOWN is queued. reserve also verifies
        # the observer was prewarmed; request_move must never cold-start an OS
        # thread on the pointer-press hot path. If an old worker is in
        # its locked CANCEL_POSTING transition, reserve waits for that post and
        # then refuses reuse until the old GUI terminal path retires it. An old
        # cancel can therefore never land behind this new DOWN on the same HWND.
        ticket = self._move_release_sentinel.reserve(handle)
        if ticket <= 0:
            return False
        # This posts a normal non-client move request to our own HWND. It does
        # not use global input injection and cannot move another HWND.
        posted = bool(
            self._user32.PostMessageW(handle, WM_NCLBUTTONDOWN, HTCAPTION, lparam)
        )
        if not posted:
            self._move_release_sentinel.abort(handle, ticket)
            return False
        # The worker was prewarmed before the DOWN. A very narrow observer
        # failure race can still occur here: only a confirmed CANCEL_POSTED is
        # safe to continue. POISONED is retracted without releasing the root's
        # capture, so direct fallback receives the real release normally.
        committed = self._move_release_sentinel.commit(handle, ticket)
        if not committed:
            state = self._move_release_sentinel.state_for(handle, ticket)
            if state != self._move_release_sentinel._CANCEL_POSTED:
                queued = wintypes.MSG()
                removed = bool(
                    self._user32.PeekMessageW(
                        ctypes.byref(queued),
                        handle,
                        WM_NCLBUTTONDOWN,
                        WM_NCLBUTTONDOWN,
                        PM_REMOVE,
                    )
                )
                invalidated = False
                if not removed:
                    # Same-thread PeekMessage should always find our just-posted
                    # DOWN. Destroying the target is the final bounded guard if
                    # an adapter/queue violates that invariant: queued messages
                    # for the dead HWND cannot later enter a modal move loop.
                    invalidated = bool(self._user32.DestroyWindow(handle))
                    if invalidated:
                        self._handles.discard(int(handle))
                        self._invalidated_handles.add(int(handle))
                        self._unregister_class_if_unused()
                if removed or invalidated:
                    self._move_release_sentinel.discard_failed_post(
                        handle, ticket
                    )
                return False
        self._move_tickets[int(handle)] = ticket
        self._user32.ReleaseCapture()
        return ticket

    def consume_invalidated_handle(self, handle: int) -> bool:
        """Tell the lifecycle wrapper a poison guard destroyed this HWND."""

        self._check_thread()
        target = int(handle)
        if target not in self._invalidated_handles:
            return False
        self._invalidated_handles.discard(target)
        return True

    def cancel_move(
        self,
        handle: int,
        generation: int | None = None,
    ) -> bool:
        """Ask User32 to leave the proxy's modal move loop asynchronously."""

        self._check_thread()
        ticket = (
            int(generation)
            if generation is not None
            else int(self._move_tickets.get(int(handle), 0))
        )
        return self._move_release_sentinel.cancel(handle, ticket or None)

    def get_window_rect(self, handle: int) -> WindowRect | None:
        self._check_thread()
        rect = _RECT()
        if not self._user32.GetWindowRect(handle, ctypes.byref(rect)):
            return None
        return WindowRect(
            int(rect.left),
            int(rect.top),
            int(rect.right),
            int(rect.bottom),
        )

    def hide_window(self, handle: int) -> bool:
        self._check_thread()
        ticket = self._move_tickets.pop(int(handle), 0)
        self._move_release_sentinel.retire(handle, ticket or None)
        self._user32.ShowWindow(handle, SW_HIDE)
        return True

    def destroy_window(self, handle: int) -> bool:
        self._check_thread()
        ticket = self._move_tickets.pop(int(handle), 0)
        self._move_release_sentinel.retire(handle, ticket or None)
        if not self._user32.DestroyWindow(handle):
            return False
        self._handles.discard(handle)
        self._unregister_class_if_unused()
        return True

    def close(self, timeout: float = 0.25) -> bool:
        """Stop the prewarmed observer during ordinary proxy shutdown."""

        self._check_thread()
        observer_closed = self._move_release_sentinel.close(timeout)
        class_released = self._unregister_class_if_unused()
        return bool(observer_closed and class_released)


class WindowsDragProxy:
    """Lifecycle and geometry for one temporary layered drag proxy."""

    def __init__(self, api: Win32DragProxyApi | None = None) -> None:
        self.api: Win32DragProxyApi = api or NativeWin32DragProxyApi()
        self._handle: int | None = None
        self._bitmap: ArgbPremultipliedBitmap | None = None
        self._position = (0, 0)
        self._visible = False
        self._move_origin: WindowRect | None = None
        self._last_rect: WindowRect | None = None
        self._final: DragProxyFinal | None = None
        self._move_ticket = 0

    @property
    def handle(self) -> int | None:
        return self._handle

    @property
    def is_created(self) -> bool:
        return self._handle is not None

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def final_state(self) -> DragProxyFinal | None:
        return self._final

    @property
    def final_rect(self) -> WindowRect | None:
        return self._final.rect if self._final is not None else None

    def create(self) -> int:
        if self._handle is not None:
            return self._handle
        handle = int(
            self.api.create_window(
                ex_style=DEFAULT_PROXY_EX_STYLE,
                style=DEFAULT_PROXY_STYLE,
            )
        )
        if handle <= 0:
            raise WindowsDragProxyError("CreateWindowExW did not return a window handle")
        self._handle = handle
        self._final = None
        return handle

    def upload_bitmap(self, bitmap: ArgbPremultipliedBitmap) -> None:
        if not isinstance(bitmap, ArgbPremultipliedBitmap):
            raise TypeError("bitmap must be an ArgbPremultipliedBitmap")
        handle = self.create()
        x, y = self._position
        if self._visible:
            current = self.rect()
            if current is not None:
                x, y = current.left, current.top
                self._position = (x, y)
        if not self.api.update_layered_window(
            handle,
            bitmap=bitmap,
            x=x,
            y=y,
        ):
            raise WindowsDragProxyError("UpdateLayeredWindow failed")
        self._bitmap = bitmap
        self._last_rect = WindowRect.from_position(x, y, bitmap.width, bitmap.height)

    update_bitmap = upload_bitmap

    def upload_argb_premultiplied(
        self,
        width: int,
        height: int,
        pixels: Any,
        *,
        stride: int | None = None,
    ) -> ArgbPremultipliedBitmap:
        bitmap = ArgbPremultipliedBitmap(width, height, pixels, stride=stride)
        self.upload_bitmap(bitmap)
        return bitmap

    update_argb_premultiplied = upload_argb_premultiplied

    def show_at(self, x: int, y: int) -> WindowRect:
        x = _plain_int(x, "x")
        y = _plain_int(y, "y")
        if self._bitmap is None:
            raise WindowsDragProxyError("upload a bitmap before showing the proxy")
        handle = self.create()
        if not self.api.show_no_activate(
            handle,
            x=x,
            y=y,
            width=self._bitmap.width,
            height=self._bitmap.height,
        ):
            raise WindowsDragProxyError("the proxy could not be shown without activation")
        self._position = (x, y)
        self._visible = True
        self._last_rect = WindowRect.from_position(
            x, y, self._bitmap.width, self._bitmap.height
        )
        self._move_origin = self._last_rect
        return self._last_rect

    def start_move(self, cursor_position: tuple[int, int] | None = None) -> bool:
        if self._handle is None or not self._visible:
            raise WindowsDragProxyError("show the proxy before requesting a move")
        if cursor_position is not None:
            if not isinstance(cursor_position, tuple) or len(cursor_position) != 2:
                raise TypeError("cursor_position must be an (x, y) tuple")
            cursor_position = (
                _plain_int(cursor_position[0], "cursor x"),
                _plain_int(cursor_position[1], "cursor y"),
            )
        origin = self.rect()
        if origin is None:
            raise WindowsDragProxyError("the proxy rectangle is unavailable")
        self._move_origin = origin
        request_result = self.api.request_move(
            self._handle,
            cursor_position=cursor_position,
        )
        requested = bool(request_result)
        self._move_ticket = (
            int(request_result)
            if requested and not isinstance(request_result, bool)
            else 0
        )
        if not requested:
            self._move_origin = origin
            consume_invalidated = getattr(
                self.api, "consume_invalidated_handle", None
            )
            if callable(consume_invalidated) and consume_invalidated(
                self._handle
            ):
                # The native adapter destroyed a poisoned target before its
                # queued DOWN could dispatch. Forget it immediately; the next
                # idle snapshot creates and uploads a fresh proxy HWND.
                self._handle = None
                self._bitmap = None
                self._visible = False
                self._move_ticket = 0
        return requested

    request_move = start_move

    def cancel_move(self) -> bool:
        """Cancel an in-flight native move without injecting input."""

        if self._handle is None or not self._visible:
            return False
        cancel = getattr(self.api, "cancel_move", None)
        if not callable(cancel):
            return False
        try:
            return bool(cancel(self._handle, self._move_ticket or None))
        except TypeError:
            # Existing injected test adapters implement the original one-arg
            # protocol; production NativeWin32DragProxyApi validates tickets.
            return bool(cancel(self._handle))

    def rect(self) -> WindowRect | None:
        if self._handle is None:
            return None
        value = self.api.get_window_rect(self._handle)
        if value is None:
            return None
        rect = value if isinstance(value, WindowRect) else WindowRect(*value)
        self._last_rect = rect
        self._position = (rect.left, rect.top)
        return rect

    def delta(self) -> DragDelta:
        current = self.rect() or self._last_rect
        origin = self._move_origin
        if current is None or origin is None:
            return DragDelta(0, 0)
        return DragDelta(current.left - origin.left, current.top - origin.top)

    def hide(self) -> bool:
        if self._handle is None or not self._visible:
            return False
        hidden = bool(self.api.hide_window(self._handle))
        if hidden:
            self._visible = False
            self._move_ticket = 0
        return hidden

    def finalize(self, *, destroy: bool = True) -> DragProxyFinal:
        if self._handle is None:
            if self._final is not None:
                return self._final
            raise WindowsDragProxyError("the proxy has not been created")
        current = self.rect() or self._last_rect
        if current is None:
            raise WindowsDragProxyError("the final proxy rectangle is unavailable")
        origin = self._move_origin or current
        final = DragProxyFinal(
            current,
            DragDelta(current.left - origin.left, current.top - origin.top),
        )
        self._final = final
        self.hide()
        if destroy:
            self.destroy()
        return final

    finish = finalize

    def destroy(self) -> bool:
        if self._handle is None:
            return False
        self.hide()
        handle = self._handle
        if not self.api.destroy_window(handle):
            raise WindowsDragProxyError("DestroyWindow failed")
        self._handle = None
        self._bitmap = None
        self._visible = False
        self._move_ticket = 0
        return True

    def close(self, timeout: float = 0.25) -> bool:
        """Destroy the window and join any native release observer."""

        destroyed = True
        if self._handle is not None:
            try:
                destroyed = self.destroy()
            except WindowsDragProxyError:
                destroyed = False
        close_api = getattr(self.api, "close", None)
        observer_closed = True
        if callable(close_api):
            try:
                observer_closed = bool(close_api(timeout))
            except TypeError:
                observer_closed = bool(close_api())
        return bool(destroyed and observer_closed)

    def __enter__(self) -> "WindowsDragProxy":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del traceback
        try:
            self.close()
        except Exception:
            if exc_type is None and exc is None:
                raise


# The alternative name reads naturally at integration sites and remains an
# alias, so there is only one lifecycle implementation to test.
LayeredDragProxy = WindowsDragProxy
CtypesWin32DragProxyApi = NativeWin32DragProxyApi


def windows_drag_proxy_available() -> bool:
    """Return whether the native adapter can be constructed on this platform."""

    return WINDOWS_AVAILABLE and hasattr(ctypes, "WinDLL")


__all__ = [
    "AC_SRC_ALPHA",
    "ArgbPremultipliedBitmap",
    "CtypesWin32DragProxyApi",
    "DEFAULT_PROXY_EX_STYLE",
    "DEFAULT_PROXY_STYLE",
    "DragDelta",
    "DragProxyFinal",
    "HTCAPTION",
    "LayeredDragProxy",
    "NativeWin32DragProxyApi",
    "SW_SHOWNOACTIVATE",
    "ULW_ALPHA",
    "WM_CANCELMODE",
    "WM_NCLBUTTONDOWN",
    "WS_EX_LAYERED",
    "WS_EX_NOACTIVATE",
    "WS_EX_TOPMOST",
    "WS_EX_TOOLWINDOW",
    "Win32DragProxyApi",
    "WindowRect",
    "WindowsDragProxy",
    "WindowsDragProxyError",
    "WindowsDragProxyUnavailable",
    "windows_drag_proxy_available",
]
