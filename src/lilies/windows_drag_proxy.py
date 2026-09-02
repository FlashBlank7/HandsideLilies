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
    ) -> bool: ...

    def cancel_move(self, handle: int) -> bool: ...

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
        self._owner_thread: int | None = None

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

    def _check_thread(self) -> None:
        current = threading.get_ident()
        if self._owner_thread is None:
            self._owner_thread = current
        elif self._owner_thread != current:
            raise WindowsDragProxyError("the proxy window must stay on its creating thread")

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
    ) -> bool:
        self._check_thread()
        if cursor_position is None:
            cursor = _POINT()
            if self._user32.GetCursorPos(ctypes.byref(cursor)):
                cursor_position = (int(cursor.x), int(cursor.y))
        lparam = 0
        if cursor_position is not None:
            x, y = cursor_position
            lparam = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        # This posts a normal non-client move request to our own HWND.  It does
        # not use global input injection and cannot move another HWND.
        posted = bool(
            self._user32.PostMessageW(handle, WM_NCLBUTTONDOWN, HTCAPTION, lparam)
        )
        # Preserve the original QML capture if the queue request itself fails;
        # that lets the established direct fallback continue normally.
        if posted:
            self._user32.ReleaseCapture()
        return posted

    def cancel_move(self, handle: int) -> bool:
        """Ask User32 to leave the proxy's modal move loop asynchronously."""

        self._check_thread()
        return bool(self._user32.PostMessageW(handle, WM_CANCELMODE, 0, 0))

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
        self._user32.ShowWindow(handle, SW_HIDE)
        return True

    def destroy_window(self, handle: int) -> bool:
        self._check_thread()
        if not self._user32.DestroyWindow(handle):
            return False
        self._handles.discard(handle)
        if not self._handles and self._registered and self._class_name is not None:
            if self._user32.UnregisterClassW(self._class_name, self._instance):
                self._registered = False
                self._class_name = None
                self._window_proc = None
        return True


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
        requested = bool(
            self.api.request_move(
                self._handle,
                cursor_position=cursor_position,
            )
        )
        if not requested:
            self._move_origin = origin
        return requested

    request_move = start_move

    def cancel_move(self) -> bool:
        """Cancel an in-flight native move without injecting input."""

        if self._handle is None or not self._visible:
            return False
        cancel = getattr(self.api, "cancel_move", None)
        if not callable(cancel):
            return False
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
        return True

    def __enter__(self) -> "WindowsDragProxy":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del traceback
        if self._handle is None:
            return
        try:
            self.destroy()
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
