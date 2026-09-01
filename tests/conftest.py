from __future__ import annotations

import os


# The suite mixes controller-only tests with QML tests. Qt cannot replace a
# QCoreApplication with a QGuiApplication later in the same process, so keep a
# single offscreen GUI application alive for the whole test session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication


_SESSION_QT_APP: QGuiApplication | None = None


def pytest_sessionstart(session) -> None:
    del session
    global _SESSION_QT_APP
    existing = QCoreApplication.instance()
    if existing is not None and not isinstance(existing, QGuiApplication):
        raise RuntimeError(
            "A QCoreApplication was created before the session QGuiApplication"
        )
    _SESSION_QT_APP = existing or QGuiApplication([])


def pytest_sessionfinish(session, exitstatus) -> None:
    del session, exitstatus
    if _SESSION_QT_APP is not None:
        _SESSION_QT_APP.processEvents()
