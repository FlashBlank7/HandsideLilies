from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from PySide6.QtCore import QPoint, QObject, QTimer, QUrl
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lilies.backend import Backend
from lilies.paths import qml_path


def load_windows_ui_fonts() -> None:
    for candidate in (
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf",
    ):
        if candidate.is_file():
            QFontDatabase.addApplicationFont(str(candidate))


def main() -> int:
    temporary = tempfile.TemporaryDirectory(prefix="lilies-selection-ui-")
    os.environ["LILIES_DATA_DIR"] = temporary.name
    app = QApplication([])
    load_windows_ui_fonts()
    backend = Backend(smoke=True, force_compact=True)
    # The verifier owns the synthetic habitat transitions below.  A live
    # 75 ms pump would legitimately replace them with the empty smoke
    # catalogue and make the suppression/restoration assertion race.
    backend._v03_timer.stop()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("diagnosticWindowProbe", False)
    engine.load(QUrl.fromLocalFile(str(qml_path())))
    if not engine.rootObjects():
        raise RuntimeError("Main.qml failed to load")
    root = engine.rootObjects()[0]
    bubble = root.findChild(QObject, "selectionBubble")
    if bubble is None:
        raise RuntimeError("Selection bubble was not created")
    question = root.findChild(QObject, "selectionQuestion")
    save_action = root.findChild(QObject, "selectionSaveAction")
    save_label = root.findChild(QObject, "selectionSaveLabel")

    long_explanation = (
        "这段论述描述的是一种通过随机屏蔽部分神经元来降低过拟合的方法。"
        "它迫使模型不依赖少数局部特征，并在训练时形成多个子网络的近似集成。"
        "推理阶段不再随机屏蔽，而是使用完整网络和对应的尺度校正。"
        "在论文里，它通常作为正则化方法出现，用来改善模型在未见数据上的表现。"
        "如果作者把它放在消融实验中，通常是在比较不同正则化强度对最终指标的影响。"
        "读表格时可以重点关注训练集与验证集差距是否缩小，以及不同随机种子下结果是否稳定。"
    )
    backend._selection_bubble = {
        "visible": True,
        "text": long_explanation,
        "busy": False,
        "error": False,
        "x": 720,
        "y": 420,
        "action": "explain",
        "sourceLength": 180,
        "canSave": True,
        "savedCardId": "",
    }
    backend.selectionChanged.emit()
    outcome: dict[str, object] = {}

    def sample_collapsed() -> None:
        outcome["visible"] = bool(bubble.property("visible"))
        outcome["hasDetails"] = bool(bubble.property("hasDetails"))
        outcome["collapsed"] = [
            int(bubble.property("x")),
            int(bubble.property("y")),
            int(bubble.property("width")),
            int(bubble.property("height")),
        ]
        collapsed_screenshot = PROJECT_ROOT / "artifacts" / "selection-bubble-collapsed.png"
        app.primaryScreen().grabWindow(int(bubble.winId())).save(str(collapsed_screenshot))
        outcome["collapsedScreenshot"] = str(collapsed_screenshot)
        QTest.mouseMove(bubble, QPoint(int(bubble.property("width")) // 2, 42))
        QTimer.singleShot(650, sample_expanded)

    def sample_expanded() -> None:
        outcome["expanded"] = bool(bubble.property("expanded"))
        outcome["expandedSize"] = [int(bubble.property("width")), int(bubble.property("height"))]
        outcome["actionCount"] = int(root.property("selectionBubbleActionCount"))
        outcome["saveVisible"] = bool(save_action and save_action.property("visible"))
        outcome["saveLabel"] = {
            "exists": save_label is not None,
            "visible": bool(save_label and save_label.property("visible")),
            "text": str(save_label.property("text")) if save_label else "",
            "width": float(save_label.property("width")) if save_label else 0.0,
            "height": float(save_label.property("height")) if save_label else 0.0,
        }
        outcome["questionWindow"] = question is not None
        screenshot = PROJECT_ROOT / "artifacts" / "selection-bubble-expanded.png"
        app.primaryScreen().grabWindow(int(bubble.winId())).save(str(screenshot))
        outcome["screenshot"] = str(screenshot)
        backend._habitat_status = {"state": "silent", "visible": False}
        backend.habitatChanged.emit()
        app.processEvents()
        outcome["hiddenWhenSuppressed"] = not bool(bubble.property("visible"))
        backend._habitat_status = {"state": "desktop", "visible": True}
        backend.habitatChanged.emit()
        app.processEvents()
        outcome["restoredAfterSuppression"] = bool(bubble.property("visible"))
        outcome["passed"] = bool(
            outcome["visible"]
            and outcome["hasDetails"]
            and outcome["expanded"]
            and outcome["collapsed"][2] >= 340
            and outcome["collapsed"][3] >= 184
            and outcome["collapsed"][2] <= 430
            and outcome["collapsed"][3] <= 248
            and outcome["expandedSize"][0] > outcome["collapsed"][2]
            and outcome["actionCount"] == 4
            and outcome["saveVisible"]
            and outcome["saveLabel"]["visible"]
            and outcome["saveLabel"]["text"] in {"收进盒", "已收好"}
            and outcome["saveLabel"]["width"] > 0
            and outcome["saveLabel"]["height"] > 0
            and outcome["questionWindow"]
            and outcome["hiddenWhenSuppressed"]
            and outcome["restoredAfterSuppression"]
        )
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        backend.shutdown()
        app.quit()

    QTimer.singleShot(500, sample_collapsed)
    app.exec()
    temporary.cleanup()
    return 0 if outcome.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
