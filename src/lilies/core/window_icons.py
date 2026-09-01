from __future__ import annotations

"""Small, project-owned cache for native application icons.

The cache deliberately lives below the caller supplied data root.  It never
writes beside an executable and it returns an empty URL when Qt cannot obtain
an icon, allowing the Dock to keep its text fallback.
"""

import hashlib
import os
import threading
from pathlib import Path
from typing import Protocol


class IconRecord(Protocol):
    executable_path: str
    icon_key: str


class WindowIconCache:
    """Resolve an executable's shell icon to a stable local ``file:`` URL.

    ``QFileIconProvider`` uses the same Windows shell icon machinery as file
    managers and therefore handles ordinary Win32 applications without
    shipping a second icon extraction implementation.  Imports are lazy so
    catalogue-only tests and non-GUI tooling do not need to initialise Qt.
    """

    def __init__(self, cache_root: Path | str, *, size: int = 64) -> None:
        self.cache_root = Path(cache_root).resolve()
        self.size = max(32, min(int(size), 256))
        self._memory: dict[str, str] = {}
        self._provider: object | None = None
        self._lock = threading.RLock()

    def _cache_entry(self, record: IconRecord) -> tuple[Path, str, Path] | None:
        """Return the stable cache identity without touching a Qt object."""

        executable = Path(str(record.executable_path or "").strip().strip('"'))
        if not executable.is_file():
            return None
        try:
            stat = executable.stat()
            resolved = executable.resolve()
        except OSError:
            return None
        fingerprint = "\0".join(
            (
                os.path.normcase(str(resolved)),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(self.size),
            )
        )
        digest = hashlib.sha256(
            fingerprint.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        return executable, digest, self.cache_root / f"{digest}.png"

    def _lookup_entry(self, digest: str, destination: Path) -> str:
        with self._lock:
            cached_url = self._memory.get(digest)
        if cached_url:
            return cached_url
        try:
            available = destination.is_file() and destination.stat().st_size > 0
        except OSError:
            available = False
        if not available:
            return ""
        url = destination.as_uri()
        with self._lock:
            self._memory[digest] = url
        return url

    def lookup(self, record: IconRecord) -> str:
        """Return an already cached icon without using Qt GUI classes.

        Window enumeration runs away from the GUI thread.  That worker may
        safely call this cache-only path; QFileIconProvider and QPixmap remain
        confined to :meth:`resolve` on Qt's GUI thread.
        """

        entry = self._cache_entry(record)
        if entry is None:
            return ""
        _executable, digest, destination = entry
        return self._lookup_entry(digest, destination)

    def resolve(self, record: IconRecord) -> str:
        entry = self._cache_entry(record)
        if entry is None:
            return ""
        executable, digest, destination = entry
        cached_url = self._lookup_entry(digest, destination)
        if cached_url:
            return cached_url

        try:
            from PySide6.QtCore import QFileInfo, QSize, QThread
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QFileIconProvider

            application = QGuiApplication.instance()
            if (
                application is None
                or QThread.currentThread() != application.thread()
            ):
                return ""
            self.cache_root.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if self._provider is None:
                    self._provider = QFileIconProvider()
                provider = self._provider
            icon = provider.icon(QFileInfo(str(executable)))
            if icon.isNull():
                return ""
            pixmap = icon.pixmap(QSize(self.size, self.size))
            if pixmap.isNull():
                return ""
            temporary = destination.with_suffix(".tmp.png")
            if not pixmap.save(str(temporary), "PNG"):
                return ""
            os.replace(temporary, destination)
        except (ImportError, OSError, RuntimeError):
            return ""
        url = destination.as_uri()
        with self._lock:
            self._memory[digest] = url
        return url


__all__ = ["WindowIconCache"]
