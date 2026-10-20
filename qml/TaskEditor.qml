import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: editor
    objectName: "taskEditorDialog"
    property var task: ({})
    property string errorText: ""
    property var categoryKeys: ["daily", "project", "research", "inbox"]
    property var categoryLabels: ["日常", "项目", "论文", "收件箱"]
    signal saveRequested(string taskId, var changes)
    title: "编辑任务"
    modal: true
    focus: true
    padding: 24
    width: Math.min(parent ? parent.width - 32 : 580, 580)
    height: Math.min(parent ? parent.height - 32 : 630, 630)
    anchors.centerIn: parent
    background: Rectangle { color: "#fafafb"; radius: 18; border.color: "#e6e8ee" }
    header: Label {
        text: "编辑任务"; font.pixelSize: 20; color: "#353b45"
        leftPadding: 24; topPadding: 24; bottomPadding: 6
    }

    function edit(value) {
        task = value
        errorText = ""
        titleInput.text = String(value.title || "")
        noteInput.text = String(value.note || "")
        dueInput.text = String(value.dueLocal || "")
        timezoneInput.text = String(value.timezone || "UTC")
        priorityInput.currentIndex = Math.max(0, ["low", "normal", "high", "critical"].indexOf(value.priority))
        categoryKeys = ["daily", "project", "research", "inbox"]
        categoryLabels = ["日常", "项目", "论文", "收件箱"]
        if (categoryKeys.indexOf(value.category) < 0 && value.category) {
            categoryKeys = categoryKeys.concat([value.category])
            categoryLabels = categoryLabels.concat([value.category])
        }
        categoryInput.currentIndex = Math.max(0, categoryKeys.indexOf(value.category))
        recurrenceInput.currentIndex = Math.max(0, ["", "daily", "weekly", "monthly"].indexOf((value.recurrence || {}).frequency || ""))
        intervalInput.text = String((value.recurrence || {}).interval || 1)
        open()
        titleInput.forceActiveFocus()
    }

    component Field: TextField {
        Layout.fillWidth: true
        implicitHeight: 42
        font.pixelSize: 14
        color: "#353b45"
        selectByMouse: true
        onTextChanged: editor.errorText = ""
        leftPadding: 12; rightPadding: 12
        background: Rectangle { color: "white"; radius: 9; border.color: parent.activeFocus ? "#985c69" : "#e6e8ee" }
    }
    component Caption: Label { color: "#7c8491"; font.pixelSize: 12 }
    component Picker: ComboBox {
        id: picker
        Layout.fillWidth: true
        implicitHeight: 42
        leftPadding: 12; rightPadding: 32
        contentItem: Text { text: picker.displayText; textFormat: Text.PlainText; color: "#353b45"; font.pixelSize: 14; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle { color: "white"; radius: 9; border.color: picker.activeFocus ? "#985c69" : "#e6e8ee" }
        indicator: Canvas {
            x: picker.width - 24; y: (picker.height - height) / 2; width: 12; height: 8
            onPaint: { var ctx = getContext("2d"); ctx.clearRect(0, 0, width, height); ctx.strokeStyle = "#7c8491"; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(1, 1); ctx.lineTo(6, 6); ctx.lineTo(11, 1); ctx.stroke() }
        }
        delegate: ItemDelegate {
            required property var modelData
            required property int index
            width: picker.width; height: 40
            text: String(modelData); font.pixelSize: 14
            highlighted: picker.highlightedIndex === index
            background: Rectangle { color: parent.highlighted ? "#f2e9ed" : "white" }
        }
        popup: Popup {
            y: picker.height + 4; width: picker.width; padding: 4
            implicitHeight: contentItem.implicitHeight + 8
            background: Rectangle { color: "white"; border.color: "#e6e8ee"; radius: 10 }
            contentItem: ListView { implicitHeight: contentHeight; model: picker.popup.visible ? picker.delegateModel : null; currentIndex: picker.highlightedIndex; clip: true }
        }
    }
    component ActionButton: Button {
        id: action
        property bool primary: false
        implicitWidth: 88; implicitHeight: 42
        contentItem: Text { text: action.text; color: action.primary ? "white" : "#59616d"; font.pixelSize: 14; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 10; color: action.primary ? (action.enabled ? "#985c69" : "#c6aeb5") : (action.hovered ? "#eceef2" : "transparent") }
    }
    contentItem: ScrollView {
        id: editorScroll
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            width: editorScroll.availableWidth
            spacing: 14
            Field { id: titleInput; objectName: "taskEditorTitle"; placeholderText: "要完成的事"; font.pixelSize: 18 }
            TextArea {
                id: noteInput; objectName: "taskEditorNote"
                Layout.fillWidth: true; Layout.preferredHeight: 90
                placeholderText: "留下一点具体的说明，也可以不写"
                color: "#353b45"; font.pixelSize: 14
                wrapMode: TextEdit.Wrap; selectByMouse: true
                padding: 12
                background: Rectangle { color: "white"; radius: 9; border.color: "#e6e8ee" }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                Picker { id: priorityInput; objectName: "taskEditorPriority"; model: ["低优先级", "普通", "较高", "重要"] }
                Picker { id: categoryInput; objectName: "taskEditorCategory"; model: editor.categoryLabels }
            }
            Caption { text: "截止时间 · 留空则不设期限" }
            Field { id: dueInput; objectName: "taskEditorDue"; placeholderText: "2026-10-04 18:00" }
            RowLayout {
                Layout.fillWidth: true
                Caption { text: "时区" }
                Field { id: timezoneInput; objectName: "taskEditorTimezone"; placeholderText: "Asia/Shanghai"; font.pixelSize: 12 }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 12
                Picker { id: recurrenceInput; objectName: "taskEditorRecurrence"; model: ["不重复", "按天重复", "按周重复", "按月重复"] }
                Caption { text: "间隔"; visible: recurrenceInput.currentIndex > 0 }
                Field {
                    id: intervalInput; objectName: "taskEditorInterval"
                    visible: recurrenceInput.currentIndex > 0
                    Layout.fillWidth: false; Layout.preferredWidth: 68
                    validator: IntValidator { bottom: 1; top: 365 }
                    inputMethodHints: Qt.ImhDigitsOnly
                }
            }
            Label {
                Layout.fillWidth: true; visible: editor.errorText !== ""
                text: editor.errorText; textFormat: Text.PlainText
                color: "#985c69"; wrapMode: Text.Wrap
            }
        }
    }
    footer: Item {
        implicitHeight: 76
        RowLayout {
            anchors.right: parent.right; anchors.rightMargin: 24
            anchors.verticalCenter: parent.verticalCenter; spacing: 12
            ActionButton { text: "取消"; onClicked: editor.close() }
            ActionButton {
                objectName: "taskEditorSave"
                text: "保存"; enabled: titleInput.text.trim() !== ""
                primary: true
                onClicked: editor.saveRequested(String(editor.task.id || editor.task.taskId), {
                    title: titleInput.text.trim(), note: noteInput.text,
                    priority: ["low", "normal", "high", "critical"][priorityInput.currentIndex],
                    category: editor.categoryKeys[categoryInput.currentIndex],
                    dueLocal: dueInput.text.trim(), timezone: timezoneInput.text.trim(),
                    recurrence: recurrenceInput.currentIndex === 0 ? {} : {
                        frequency: ["", "daily", "weekly", "monthly"][recurrenceInput.currentIndex], interval: Number(intervalInput.text)
                    }
                })
            }
        }
    }
}
