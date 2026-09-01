from __future__ import annotations

import tempfile
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from lilies.core.database import Database
from lilies.core.model import ChatService


def main() -> int:
    app = QCoreApplication([])
    temporary = tempfile.TemporaryDirectory(prefix="lilies-model-smoke-")
    service = ChatService(Database(Path(temporary.name) / "smoke.db"))
    chunks: list[str] = []
    errors: list[str] = []

    service.chunk.connect(chunks.append)
    service.error.connect(errors.append)

    def finish(_reply: str) -> None:
        print("".join(chunks).strip())
        service.shutdown()
        temporary.cleanup()
        app.exit(1 if errors or not chunks else 0)

    service.responseFinished.connect(finish)
    QTimer.singleShot(120_000, lambda: (service.cancel(), app.exit(2)))
    service.send(sys.argv[1] if len(sys.argv) > 1 else "只用一句中文介绍你自己。")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
