import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: palette
    objectName: "boxSearchWindow"
    transientParent: null
    required property var appBackend
    readonly property var controller: appBackend.boxSearch
    readonly property var state: controller.state
    readonly property var preview: state.preview || ({})
    property string category: "all"
    property bool wasActive: false
    property bool suppressed: appBackend.companionSuppressed
    visible: Boolean(state.open) && !suppressed
    width: Math.min(620, Screen.width - 40)
    height: Math.min(preview.kind === "new-reminder" ? 340 : preview.kind === "task" ? 420 : 600, Screen.height - 80)
    minimumWidth: 360
    x: Screen.virtualX + (Screen.width - width) / 2
    y: Screen.virtualY + Math.max(24, (Screen.height - height) / 3)
    color: "transparent"
    title: "莉莉丝 · 盒子快搜"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint

    function performSearch() { controller.search(query.text, category) }
    function choose() {
        if (state.busy || query.inputMethodComposing) return
        if (preview.kind) { if (preview.action) controller.confirm(); return }
        if (results.currentIndex >= 0 && results.currentIndex < (state.items || []).length)
            controller.select(state.items[results.currentIndex].id)
    }
    function back() {
        if (preview.kind) { performSearch(); query.forceActiveFocus() }
        else controller.close()
    }
    onVisibleChanged: {
        if (visible) {
            wasActive = false
            Qt.callLater(function() {
                if (palette.visible) {
                    palette.category = "all"
                    query.clear()
                    palette.raise(); palette.requestActivate(); query.forceActiveFocus()
                }
            })
        } else { wasActive = false; searchDelay.stop() }
    }
    onActiveChanged: {
        if (active) wasActive = true
        else if (wasActive && visible) Qt.callLater(function() {
            if (!palette.active && palette.wasActive && palette.visible) palette.controller.close()
        })
    }
    onSuppressedChanged: if (suppressed) Qt.callLater(function() { if (palette.suppressed) palette.controller.close() })
    onClosing: function(event) { event.accepted = false; controller.close() }
    Timer { id: searchDelay; interval: 120; onTriggered: palette.performSearch() }
    Shortcut { sequence: "Escape"; context: Qt.WindowShortcut; enabled: palette.visible && !query.inputMethodComposing; onActivated: palette.back() }
    Connections {
        target: palette.controller
        function onChanged() { results.currentIndex = (palette.state.items || []).length ? 0 : -1 }
    }

    Rectangle {
        anchors.fill: parent
        color: "#fafafb"; radius: 18; border.color: "#e2e4eb"
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 22; spacing: 14
            RowLayout {
                Layout.fillWidth: true
                Text { text: "盒子快搜"; font.pixelSize: 20; color: "#353b45"; Layout.fillWidth: true }
                Text { text: palette.appBackend.boxSearchHotkey; color: "#868b97"; font.pixelSize: 12 }
                QuietAction { objectName: "boxSearchClose"; text: "×"; implicitWidth: 32; onClicked: palette.controller.close() }
            }
            PaperField {
                id: query; objectName: "boxSearchQuery"
                Layout.fillWidth: true
                placeholderText: "应用、任务、旧话，或论文里的一个词"
                maximumLength: 200
                enabled: !Boolean(palette.state.executing)
                onTextChanged: {
                    // Immediately invalidate visible tokens; debounce only the expensive query.
                    if (palette.visible) { palette.controller.search("", palette.category); searchDelay.restart() }
                }
                onAccepted: {
                    if (searchDelay.running) { searchDelay.stop(); palette.performSearch() }
                    else palette.choose()
                }
                Keys.onDownPressed: function(event) {
                    if (inputMethodComposing) { event.accepted = false; return }
                    results.currentIndex = Math.min(results.count - 1, results.currentIndex + 1)
                    results.positionViewAtIndex(results.currentIndex, ListView.Contain)
                }
                Keys.onUpPressed: function(event) {
                    if (inputMethodComposing) { event.accepted = false; return }
                    results.currentIndex = Math.max(0, results.currentIndex - 1)
                    results.positionViewAtIndex(results.currentIndex, ListView.Contain)
                }
                Keys.onEscapePressed: function(event) { if (inputMethodComposing) event.accepted = false; else palette.back() }
            }
            Flow {
                Layout.fillWidth: true; spacing: 3
                visible: !palette.preview.kind
                Repeater {
                    model: [{id:"all",label:"全部"},{id:"app",label:"应用"},{id:"task",label:"任务"},
                        {id:"chat",label:"对话"},{id:"paper",label:"论文"},{id:"card",label:"阅读卡"},{id:"reminder",label:"提醒"}]
                    QuietAction {
                        required property var modelData
                        objectName: "boxSearchFilter_" + modelData.id
                        text: modelData.label; selected: palette.category === modelData.id
                        implicitWidth: modelData.id === "card" ? 64 : 52; implicitHeight: 32
                        onClicked: { searchDelay.stop(); palette.category = modelData.id; palette.performSearch(); query.forceActiveFocus() }
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                visible: Boolean(palette.state.error)
                text: String(palette.state.error || ""); textFormat: Text.PlainText
                color: "#995363"; wrapMode: Text.Wrap; font.pixelSize: 13
            }
            ListView {
                id: results; objectName: "boxSearchResults"
                visible: !palette.preview.kind
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; spacing: 5; model: palette.state.items || []
                ScrollBar.vertical: ScrollBar { }
                delegate: ItemDelegate {
                    id: resultRow
                    required property var modelData
                    required property int index
                    objectName: "boxSearchResult_" + index
                    width: results.width; height: 78
                    padding: 12; hoverEnabled: true
                    background: Rectangle { radius: 10; color: resultRow.index === results.currentIndex ? "#f0e9ed" : resultRow.hovered ? "#f0f1f4" : "transparent" }
                    contentItem: Column {
                        spacing: 7
                        Text { width: parent.width; text: resultRow.modelData.title; textFormat: Text.PlainText;
                            color: "#353b45"; font.pixelSize: 14; elide: Text.ElideRight }
                        Text { width: parent.width; text: resultRow.modelData.subtitle; textFormat: Text.PlainText;
                            color: "#727a89"; font.pixelSize: 12; elide: Text.ElideMiddle }
                    }
                    onClicked: { results.currentIndex = index; palette.controller.select(modelData.id) }
                    ToolTip {
                        visible: resultRow.hovered && palette.visible
                        delay: 700
                        contentItem: Text {
                            text: String(resultRow.modelData.hint || resultRow.modelData.subtitle)
                            textFormat: Text.PlainText; wrapMode: Text.Wrap; color: "#535b68"
                            width: Math.min(380, palette.width - 60)
                        }
                    }
                }
                Text {
                    anchors.centerIn: parent; width: Math.max(100, parent.width - 24)
                    visible: results.count === 0
                    text: palette.state.busy ? "查找中…" : query.text ? "没有找到。可以换个词，或选择分类。" : "找回一段话，或开始手边的事。\n\n也可以输入：提醒 20分钟后 喝水"
                    textFormat: Text.PlainText; color: "#7b8290"; horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: 14; wrapMode: Text.Wrap; lineHeight: 1.5
                }
            }
            ColumnLayout {
                visible: Boolean(palette.preview.kind)
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                Text { Layout.fillWidth: true; text: String(palette.preview.title || ""); textFormat: Text.PlainText;
                    color: "#353b45"; font.pixelSize: 17; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight }
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    TextArea {
                        objectName: "boxSearchDetail"
                        text: String(palette.preview.detail || ""); textFormat: TextEdit.PlainText
                        readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                        font.pixelSize: 14; color: "#545d6b"; background: null
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    QuietAction { objectName: "boxSearchBack"; text: "返回结果"; onClicked: palette.back() }
                    Item { Layout.fillWidth: true }
                    QuietAction { objectName: "boxSearchConfirm"; text: String(palette.preview.action || ""); primary: true
                        visible: Boolean(palette.preview.action); enabled: !palette.state.busy
                        onClicked: palette.controller.confirm() }
                }
            }
            Text { Layout.fillWidth: true; text: "↑ ↓ 选择    Enter 打开 / 确认    Esc 返回"; color: "#8a909b"; font.pixelSize: 11 }
        }
    }
}
