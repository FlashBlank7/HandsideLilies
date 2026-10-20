"""Offscreen menu/delegate regression. Uses synthetic icons and an inert backend."""
import os
import sys
import json
from pathlib import Path
os.environ.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software", QT_QUICK_CONTROLS_STYLE="Basic")
from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, QPoint, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
class Fixture(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.items = []
        self.calls = []
    @Property("QVariantList", notify=changed)
    def desktopItems(self): return self.items
    @Slot(str, bool)
    def setIconPinned(self, item_id, pinned): self.calls.append(["pin", item_id, pinned])
    @Slot(str)
    def revealItem(self, item_id): self.calls.append(["reveal", item_id])
    @Slot(str, str)
    def setIconGroup(self, item_id, group): self.calls.append(["group", item_id, group])
    @Slot(str)
    def hideIcon(self, item_id): self.calls.append(["hide", item_id])
    @Slot(str)
    def openItem(self, item_id): self.calls.append(["open", item_id])
    @Slot(str, float, float)
    def saveIconPosition(self, item_id, x, y): self.calls.append(["position", item_id, x, y])

def main():
    app = QApplication([])
    fixture = Fixture()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda values:warnings.extend(v.toString() for v in values))
    engine.rootContext().setContextProperty("fixture", fixture)
    engine.loadData(b'''import QtQuick
import QtQuick.Window
Window { width: 1100; height: 800; visible: true
    DesktopIconLayer { anchors.fill: parent; appBackend: fixture }
}''', QUrl.fromLocalFile(str(ROOT / "qml" / "icon-test.qml")))
    root = engine.rootObjects()[0]
    layer = root.findChild(QQuickItem, "desktopIconLayer")
    menu = root.findChild(QObject, "desktopIconContextMenu")
    counts = []
    for count in (1, 50, 200):
        fixture.items = [dict(itemId="test-"+str(i), name="测试图标 "+str(i), source="desktop", glyph="T",
                              pinned=False, group="未分组", x=(i%10)*98, y=(i//10)*105) for i in range(count)]
        fixture.changed.emit(); QTest.qWait(25)
        delegates = [v for v in layer.childItems() if v.objectName().startswith("desktopIcon_")]
        assert len(delegates) == count and layer.property("iconCount") == count
        assert len(root.findChildren(QObject, "desktopIconContextMenu")) == 1
        counts.append(count)

    QTest.mouseClick(root, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(30,30))
    QTest.qWait(200)
    assert menu.property("visible") and layer.property("selectedItemId") == "test-0"
    pin = root.findChild(QObject, "desktopIconPin")
    pin.triggered.emit()
    assert fixture.calls[-1] == ["pin", "test-0", True]
    menu.close(); QTest.qWait(200)
    QTest.mouseClick(root, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(128,30))
    QTest.qWait(200)
    assert menu.property("visible") and layer.property("selectedItemId") == "test-1"
    assert layer.invokeContextAction("reveal", "")
    assert layer.invokeContextAction("group", "资料")
    assert layer.invokeContextAction("hide", "")
    assert fixture.calls[-3:] == [["reveal","test-1"],["group","test-1","资料"],["hide","test-1"]]
    before = len(fixture.calls)
    fixture.items = [item for item in fixture.items if item["itemId"] != "test-1"]
    fixture.changed.emit(); QTest.qWait(200)
    assert not menu.property("visible")
    assert not layer.invokeContextAction("hide", "") and len(fixture.calls) == before
    layer.setVisible(False); QTest.qWait(20)
    assert layer.property("iconCount") == 0
    layer.setVisible(True); QTest.qWait(20)
    assert layer.property("iconCount") == 199
    QTest.mouseDClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30,30))
    QTest.qWait(20)
    assert ["open", "test-0"] in fixture.calls
    QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(30,30))
    QTest.mouseMove(root, QPoint(60,60), 20)
    QTest.mouseMove(root, QPoint(110,110), 20)
    QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(110,110))
    QTest.qWait(20)
    positions = [call for call in fixture.calls if call[:2] == ["position","test-0"]]
    assert positions and positions[-1][2] > 12 and positions[-1][3] > 12
    assert not warnings, warnings[:3]
    root.hide()
    print(json.dumps(dict(passed=True,iconCounts=counts,sharedMenus=1,staleTargetRejected=True)))

if __name__ == "__main__": main()
