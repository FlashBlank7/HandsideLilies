import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: window
    objectName: "sharedWorldWindow"
    transientParent: null
    required property var appBackend
    readonly property var controller: appBackend.sharedWorld
    readonly property var state: controller.state
    readonly property var page: state.page || ({})
    readonly property var detail: state.detail || ({})
    property bool suppressed: appBackend.companionSuppressed
    property string removeId: ""
    property string removeTitle: ""
    visible: Boolean(state.open) && !suppressed
    width: Math.min(640, Screen.width - 32)
    height: Math.min(660, Screen.height - 72)
    minimumWidth: 360; minimumHeight: 360
    x: Screen.virtualX + (Screen.width - width) / 2
    y: Screen.virtualY + (Screen.height - height) / 2
    color: "transparent"
    title: "莉莉丝 · 共同记录"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    onVisibleChanged: if (visible) Qt.callLater(function() { if (window.visible) { window.raise(); window.requestActivate() } })
    onActiveChanged: if (active) Qt.callLater(function() { if (window.active) window.controller.refreshShown() })
    onSuppressedChanged: if (suppressed) Qt.callLater(function() { if (window.suppressed) window.controller.close() })
    onClosing: function(event) { event.accepted = false; controller.close() }
    function browse(shelf, hidden, offset) { removeId = ""; controller.browse(shelf, hidden, offset) }
    function back() {
        if (detail.id) browse(page.shelf, Boolean(page.hidden), Number(page.offset || 0))
        else controller.close()
    }
    function dateLabel(value) { return value ? Qt.formatDateTime(new Date(value), "yyyy-MM-dd HH:mm") : "" }
    Shortcut { sequence: "Escape"; context: Qt.WindowShortcut; enabled: window.visible && !removeDialog.opened; onActivated: window.back() }

    Rectangle {
        anchors.fill: parent; radius: 18; color: "#fafafb"; border.color: "#e3e5ec"
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 22; spacing: 16
            RowLayout {
                Layout.fillWidth: true
                Item {
                    Layout.fillWidth: true; Layout.preferredHeight: 38
                    Text { anchors.verticalCenter: parent.verticalCenter; text: "一起留下的"; font.pixelSize: 23; color: "#353b45" }
                    MouseArea { anchors.fill: parent; onPressed: window.startSystemMove() }
                }
                QuietAction { objectName: "sharedWorldClose"; text: "×"; implicitWidth: 32; onClicked: window.controller.close() }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !window.detail.id
                Repeater {
                    model: [{id:"papers", label:"纸页架"}, {id:"letters", label:"小信笺"}, {id:"projects", label:"工作台"}]
                    QuietAction {
                        required property var modelData
                        objectName: "sharedWorldShelf_" + modelData.id
                        Layout.fillWidth: true; Layout.minimumWidth: 0
                        text: modelData.label
                        selected: window.page.shelf === modelData.id
                        enabled: !window.state.busy
                        onClicked: window.browse(modelData.id, false, 0)
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; visible: !window.detail.id
                Text {
                    Layout.fillWidth: true; text: window.page.hidden ? "暂时收起的记录" : String(window.page.total || 0) + " 份记录"
                    color: "#7c8491"; font.pixelSize: 12
                }
                QuietAction { objectName: "sharedWorldHidden"; text: window.page.hidden ? "回到架上" : "已隐藏"; implicitHeight: 32
                    enabled: !window.state.busy; onClicked: window.browse(window.page.shelf, !window.page.hidden, 0) }
                QuietAction { objectName: "sharedWorldRefresh"; text: "刷新"; implicitHeight: 32
                    enabled: !window.state.busy; onClicked: window.browse(window.page.shelf, Boolean(window.page.hidden), Number(window.page.offset)) }
            }
            Text { Layout.fillWidth: true; visible: Boolean(window.state.error); text: String(window.state.error || "")
                textFormat: Text.PlainText; color: "#985c69"; font.pixelSize: 13; wrapMode: Text.Wrap }
            ListView {
                id: records; objectName: "sharedWorldList"
                Layout.fillWidth: true; Layout.fillHeight: true
                visible: !window.detail.id; clip: true; spacing: 8
                model: window.page.items || []; reuseItems: true
                ScrollBar.vertical: ScrollBar { }
                delegate: ItemDelegate {
                    id: row
                    required property var modelData
                    required property int index
                    objectName: "sharedWorldRecord_" + index
                    width: records.width; height: 91; padding: 14
                    enabled: !window.state.busy
                    background: Rectangle { radius: 12; color: row.hovered ? "#f2ecef" : "#f0f1f4" }
                    contentItem: Column {
                        spacing: 9
                        Text { width: parent.width; text: row.modelData.title; textFormat: Text.PlainText; color: "#353b45"; font.pixelSize: 15; elide: Text.ElideRight }
                        Text { width: parent.width; text: row.modelData.label; textFormat: Text.PlainText; color: "#737d8c"; font.pixelSize: 12; elide: Text.ElideRight }
                        Text { width: parent.width; text: window.dateLabel(row.modelData.occurredAt); color: "#8b919b"; font.pixelSize: 11 }
                    }
                    onClicked: window.controller.inspect(String(modelData.id))
                }
                Text {
                    anchors.centerIn: parent; width: parent.width - 28
                    visible: records.count === 0; color: "#78808e"; font.pixelSize: 14
                    horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; lineHeight: 1.45
                    text: window.state.busy ? "正在翻开…" : window.page.hidden ? "这里没有收起的记录。"
                        : window.page.shelf === "letters" ? "在对话里点“留在盒中”，\n把想留住的话放到这里。"
                        : window.page.shelf === "projects" ? "完成一件“项目”分类的事项后，\n它会出现在工作台上。"
                        : "开启过本篇阅读的论文，会放在这里。\n只添加链接，还不算开始阅读。"
                }
            }
            ColumnLayout {
                visible: Boolean(window.detail.id); Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                Text { Layout.fillWidth: true; text: window.detail.shelf === "letters" ? "小信笺" : String(window.detail.title || ""); textFormat: Text.PlainText
                    color: "#353b45"; font.pixelSize: 19; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight }
                Text { Layout.fillWidth: true; text: String(window.detail.label || "") + " · " + window.dateLabel(window.detail.occurredAt)
                    textFormat: Text.PlainText; color: "#7b8492"; font.pixelSize: 12; wrapMode: Text.Wrap }
                ScrollView {
                    objectName: "sharedWorldDetailScroll"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    TextArea { objectName: "sharedWorldBody"; text: String(window.detail.body || ""); textFormat: TextEdit.PlainText
                        readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                        font.pixelSize: window.detail.shelf === "letters" ? 16 : 14; color: "#545d6b"; background: null }
                }
                Text { Layout.fillWidth: true; visible: window.detail.shelf === "letters"
                    text: "保存的是这次对话，不是正式设定。"; color: "#8b929e"; font.pixelSize: 12; wrapMode: Text.Wrap }
                Flow {
                    Layout.fillWidth: true; spacing: 4
                    QuietAction { objectName: "sharedWorldBack"; text: "返回"; enabled: !window.state.busy; onClicked: window.back() }
                    QuietAction { objectName: "sharedWorldSource"; text: "打开出处"; visible: Boolean((window.detail.route || {}).kind)
                        primary: true; enabled: !window.state.busy; onClicked: window.controller.openSource() }
                    QuietAction { objectName: "sharedWorldHide"; text: window.detail.visibility === "hidden" ? "放回架上" : "暂时隐藏"
                        enabled: !window.state.busy; onClicked: window.controller.changeVisibility(window.detail.id, window.detail.visibility === "hidden" ? "visible" : "hidden") }
                    QuietAction { objectName: "sharedWorldRemove"; text: "移除"; enabled: !window.state.busy
                        onClicked: { window.removeId = String(window.detail.id); window.removeTitle = String(window.detail.title); removeDialog.open() } }
                }
            }
            RowLayout {
                visible: !window.detail.id && Number(window.page.total || 0) > Number(window.page.limit || 20)
                Layout.fillWidth: true
                QuietAction { objectName: "sharedWorldPrevious"; text: "上一页"; enabled: !window.state.busy && Number(window.page.offset) > 0
                    onClicked: window.browse(window.page.shelf, Boolean(window.page.hidden), Number(window.page.offset) - Number(window.page.limit)) }
                Item { Layout.fillWidth: true }
                Text { text: String(Number(window.page.offset || 0) + 1) + "–"
                        + String(Math.min(Number(window.page.offset || 0) + Number(window.page.limit || 20), Number(window.page.total || 0)))
                        + " / " + String(window.page.total || 0); color: "#7b8491"; font.pixelSize: 12 }
                QuietAction { objectName: "sharedWorldNext"; text: "下一页"; enabled: !window.state.busy && Boolean(window.page.hasMore)
                    onClicked: window.browse(window.page.shelf, Boolean(window.page.hidden), Number(window.page.offset) + Number(window.page.limit)) }
            }
        }
    }
    PaperDialog {
        id: removeDialog; objectName: "sharedWorldRemoveDialog"
        parent: window.contentItem; x: (parent.width - width) / 2; y: (parent.height - height) / 2
        width: Math.min(410, window.width - 36); title: "从盒中移除？"
        contentItem: ColumnLayout {
            spacing: 16
            Text { Layout.fillWidth: true; text: window.removeTitle; textFormat: Text.PlainText; color: "#353b45"; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight }
            Text { Layout.fillWidth: true; text: "这里只移除展示记录。原对话、论文和任务都会保留；自动整理不会把它重新放回来。"
                color: "#747d8b"; font.pixelSize: 13; wrapMode: Text.Wrap }
            RowLayout {
                Layout.fillWidth: true
                QuietAction { text: "保留"; onClicked: removeDialog.close() }
                Item { Layout.fillWidth: true }
                QuietAction { objectName: "sharedWorldRemoveConfirm"; text: "确认移除"; primary: true; enabled: !window.state.busy
                    onClicked: { window.controller.changeVisibility(window.removeId, "removed"); removeDialog.close() } }
            }
        }
    }
}
