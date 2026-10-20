import QtQuick
import QtQuick.Controls

// A single context menu serves every icon. Hundreds of invisible controls
// must not be constructed on the GUI thread whenever the desktop is opened.
Item {
    id: iconLayer
    objectName: "desktopIconLayer"
    required property var appBackend
    readonly property var items: visible ? appBackend.desktopItems : []
    property string selectedItemId: ""
    readonly property var selectedItem: {
        for (var i = 0; i < items.length; ++i) {
            if (String(items[i].itemId) === selectedItemId)
                return items[i]
        }
        return null
    }
    readonly property int iconCount: iconRepeater.count

    function openContextMenu(itemId, px, py) {
        selectedItemId = String(itemId)
        if (selectedItem)
            iconMenu.popup(iconLayer, px, py)
    }

    function invokeContextAction(action, group) {
        // Re-resolve against the current model. An icon removed while its
        // menu was open must not leave an action targeting stale data.
        var item = selectedItem
        if (!item)
            return false
        var id = String(item.itemId)
        if (action === "pin") appBackend.setIconPinned(id, !Boolean(item.pinned))
        else if (action === "reveal") appBackend.revealItem(id)
        else if (action === "group") appBackend.setIconGroup(id, group)
        else if (action === "hide") appBackend.hideIcon(id)
        else return false
        return true
    }

    function closeInvalidMenu() {
        if (!selectedItem)
            iconMenu.close()
    }
    function clearClosedSelection() {
        if (!iconMenu.visible)
            selectedItemId = ""
    }
    onSelectedItemChanged: if (!selectedItem) Qt.callLater(closeInvalidMenu)
    onVisibleChanged: if (!visible) iconMenu.close()

    Repeater {
        id: iconRepeater
        model: iconLayer.items
        delegate: Item {
            id: iconDelegate
            objectName: "desktopIcon_" + String(modelData.itemId)
            required property var modelData
            width: 92
            height: 100
            x: Math.max(12, Math.min(iconLayer.width - width - 12, Number(modelData.x)))
            y: Math.max(18, Math.min(iconLayer.height - height - 18, Number(modelData.y)))

            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                width: 60; height: 60; radius: 18
                color: iconMouse.containsMouse ? "#fffdf7" : "#f4eee4"
                border.color: iconMouse.containsMouse ? "#a99a84" : "#cfc3b2"
                border.width: 1
                Text {
                    anchors.centerIn: parent
                    text: modelData.glyph
                    color: modelData.source === "desktop" ? "#9f3129" : "#777168"
                    font.family: "Segoe UI Symbol"
                    font.pixelSize: 25
                }
                Rectangle {
                    visible: Boolean(modelData.pinned)
                    width: 9; height: 9; radius: 5; color: "#9f3129"
                    anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 5
                }
                Text {
                    visible: modelData.group && modelData.group !== "未分组"
                    anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 5
                    text: String(modelData.group || "").substring(0, 1)
                    color: "#887765"; font.pixelSize: 10
                }
            }
            Text {
                anchors.top: parent.top; anchors.topMargin: 66
                anchors.horizontalCenter: parent.horizontalCenter
                width: parent.width; text: modelData.name
                color: "#4d4a45"; font.family: "Microsoft YaHei UI"; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
            }
            MouseArea {
                id: iconMouse
                anchors.fill: parent; hoverEnabled: true
                acceptedButtons: Qt.LeftButton
                drag.target: iconDelegate
                drag.minimumX: 4; drag.maximumX: iconLayer.width - iconDelegate.width - 4
                drag.minimumY: 4; drag.maximumY: iconLayer.height - iconDelegate.height - 4
                onDoubleClicked: iconLayer.appBackend.openItem(modelData.itemId)
                onReleased: iconLayer.appBackend.saveIconPosition(modelData.itemId, iconDelegate.x, iconDelegate.y)
            }
            MouseArea {
                objectName: "desktopIconContext_" + String(modelData.itemId)
                anchors.fill: parent; acceptedButtons: Qt.RightButton
                onClicked: function(mouse) {
                    var point = iconDelegate.mapToItem(iconLayer, mouse.x, mouse.y)
                    iconLayer.openContextMenu(modelData.itemId, point.x, point.y)
                }
            }
        }
    }

    Menu {
        id: iconMenu
        objectName: "desktopIconContextMenu"
        onClosed: Qt.callLater(iconLayer.clearClosedSelection)
        MenuItem {
            objectName: "desktopIconPin"
            text: iconLayer.selectedItem && iconLayer.selectedItem.pinned ? "取消固定" : "固定到桌面"
            onTriggered: iconLayer.invokeContextAction("pin", "")
        }
        MenuItem { text: "打开所在位置"; onTriggered: iconLayer.invokeContextAction("reveal", "") }
        MenuSeparator { }
        Menu {
            title: "分组"
            MenuItem { text: "未分组"; onTriggered: iconLayer.invokeContextAction("group", "未分组") }
            MenuItem { text: "工作"; onTriggered: iconLayer.invokeContextAction("group", "工作") }
            MenuItem { text: "资料"; onTriggered: iconLayer.invokeContextAction("group", "资料") }
            MenuItem { text: "娱乐"; onTriggered: iconLayer.invokeContextAction("group", "娱乐") }
        }
        MenuItem { text: "从当前布局隐藏"; onTriggered: iconLayer.invokeContextAction("hide", "") }
    }
}
