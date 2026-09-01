from __future__ import annotations

"""Small, project-owned cache for native application icons.

The cache deliberately lives below the caller supplied data root.  It never
writes beside an executable and it returns an empty URL when Qt cannot obtain
an icon, allowing the Dock to keep its text fallback.
"""

import hashlib
import os
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

    def resolve(self, record: IconRecord) -> str:
        executable = Path(str(record.executable_path or "").strip().strip('"'))
        if not executable.is_file():
            return ""
        try:
            stat = executable.stat()
        except OSError:
            return ""
        fingerprint = "\0".join(
            (
                os.path.normcase(str(executable.resolve())),
                str(stat.st_size),
                str(stat.st_mtime_ns),
                str(self.size),
            )
        )
        digest = hashlib.sha256(fingerprint.encode("utf-8", errors="surrogatepass")).hexdigest()
        cached_url = self._memory.get(digest)
        if cached_url:
            return cached_url
        destination = self.cache_root / f"{digest}.png"
        if destination.is_file() and destination.stat().st_size > 0:
            url = destination.as_uri()
            self._memory[digest] = url
            return url

        try:
            from PySide6.QtCore import QFileInfo, QSize
            from PySide6.QtGui import QGuiApplication
            from PySide6.QtWidgets import QFileIconProvider

            if QGuiApplication.instance() is None:
                return ""
            self.cache_root.mkdir(parents=True, exist_ok=True)
            if self._provider is None:
                self._provider = QFileIconProvider()
            icon = self._provider.icon(QFileInfo(str(executable)))
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
        self._memory[digest] = url
        return url


__all__ = ["WindowIconCache"]
