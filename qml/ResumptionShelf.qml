import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs as Dialogs

Item {
    id: shelf
    objectName: "resumptionShelf"
    required property var appBackend
    readonly property var controller: appBackend.workResumption
    readonly property var state: controller.state
    readonly property var page: state.page || ({items: []})
    property var selectedWindows: []
    property var selectedResources: []
    property var draftSources: []
    property string notice: ""
    property string previewNotice: ""
    property var deleteItem: ({})
    property var taskChoices: [{id: "", title: "不关联任务"}]
    implicitHeight: bar.implicitHeight + (notice || state.error ? feedback.implicitHeight + 8 : 0)

    function toggle(list, id, checked) {
        return checked ? list.concat([id]) : list.filter(function(v) { return v !== id })
    }
    function addSource(value) {
        var clean = String(value).trim()
        if (clean && draftSources.indexOf(clean) === -1 && draftSources.length < 12) draftSources = draftSources.concat([clean])
    }
    Connections {
        target: shelf.controller
        function onCompleted(operation) {
            if (operation === "capture") {
                shelf.selectedWindows = []; shelf.draftSources = []
                // Copy primitives: Qt sequence entries can remain live references
                // to the backend list and change identity after a refresh.
                shelf.taskChoices = [{id: "", title: "不关联任务"}].concat(
                    shelf.appBackend.taskItems.filter(function(t) { return !t.completed && !t.archived })
                        .map(function(t) { return {id: String(t.id), title: String(t.title)} }))
                var taskId = shelf.appBackend.focusStatus.taskId || ""
                taskChoice.currentIndex = Math.max(0, shelf.taskChoices.findIndex(function(t) { return t.id === taskId }))
                nameInput.text = shelf.appBackend.focusStatus.taskTitle || ""
                nextInput.text = ""
                showWindows.checked = false
                saveSheet.open()
            } else if (operation === "refreshCapture") {
                shelf.selectedWindows = []
            } else if (operation === "preview") {
                shelf.selectedWindows = []; shelf.selectedResources = []
                shelf.previewNotice = ""
                previewSheet.open()
            } else if (operation === "save") {
                saveSheet.close(); shelf.notice = "留好了。回来时，从这里继续。"
            } else if (operation === "confirm") {
                previewSheet.close()
                var result = shelf.state.result
                shelf.notice = result.items.length ? "已请求 " + result.requested + " 项 · 跳过 " + result.skipped + " 项 · 未完成 " + result.failed + " 项" : "下一步留在这里，随时可以回来。"
                if (result.items.length) resultSheet.open()
            } else if (operation === "delete") {
                deleteSheet.close(); shelf.notice = "只移除了这份记录，原文件和任务都还在。"
            }
        }
    }
    Component.onCompleted: controller.refresh(0)

    RowLayout {
        id: bar
        width: parent.width; spacing: 2
        QuietAction {
            objectName: "resumptionCapture"
            text: shelf.state.busy ? "稍等一下…" : "留住刚才"
            enabled: !shelf.state.busy
            onClicked: { shelf.notice = ""; shelf.controller.capture() }
        }
        Item { Layout.fillWidth: true }
        QuietAction {
            objectName: "resumptionLatest"
            visible: shelf.page.items.length > 0
            text: "继续刚才"
            enabled: !shelf.state.busy
            onClicked: shelf.controller.preview(shelf.page.items[0].record_id)
        }
        QuietAction {
            objectName: "resumptionLibrary"
            visible: shelf.page.total > 0
            text: "记录 · " + shelf.page.total
            enabled: !shelf.state.busy
            onClicked: { shelf.controller.refresh(0); librarySheet.open() }
        }
    }
    Text {
        id: feedback
        anchors.top: bar.bottom; anchors.topMargin: 8; width: parent.width
        visible: Boolean(shelf.notice || shelf.state.error)
        text: shelf.state.error || shelf.notice; textFormat: Text.PlainText
        font.pixelSize: 12; color: shelf.state.error ? "#985c69" : "#7c8491"; wrapMode: Text.Wrap
    }

    component Hint: Text {
        Layout.fillWidth: true; font.pixelSize: 12; color: "#7c8491"
        wrapMode: Text.Wrap; textFormat: Text.PlainText; lineHeight: 1.25
    }
    component SectionLabel: Text {
        Layout.fillWidth: true; font.pixelSize: 13; color: "#353b45"; textFormat: Text.PlainText
    }
    component ErrorLine: Hint { visible: Boolean(shelf.state.error); color: "#985c69"; text: shelf.state.error }
    component Choice: CheckBox {
        id: choice
        Layout.fillWidth: true
        implicitHeight: Math.max(36, choiceText.implicitHeight + 12)
        spacing: 10
        indicator: Rectangle {
            x: 4; y: (choice.height - height) / 2; width: 20; height: 20; radius: 5
            color: choice.checked ? "#985c69" : "white"; border.color: choice.checked ? "#985c69" : "#cfd3dd"
            opacity: choice.enabled ? 1 : 0.4
            Text { anchors.centerIn: parent; text: choice.checked ? "✓" : ""; color: "white"; font.pixelSize: 14 }
        }
        contentItem: Text {
            id: choiceText
            text: choice.text; textFormat: Text.PlainText; color: "#353b45"
            font.pixelSize: 13; leftPadding: 34; wrapMode: Text.Wrap
            verticalAlignment: Text.AlignVCenter; opacity: choice.enabled ? 1 : 0.5
        }
    }

    PaperDialog {
        id: saveSheet; objectName: "resumptionSaveSheet"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620, parent.width - 32); height: Math.min(660, parent.height - 32)
        title: "留住刚才"
        closePolicy: shelf.state.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        onClosed: { if (!shelf.state.busy) shelf.controller.cancel() }
        contentItem: ColumnLayout {
            spacing: 12
            ScrollView {
                id: saveScroll
                objectName: "resumptionSaveScroll"
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: saveScroll.availableWidth; spacing: 12
                    Hint { text: "留一句下一步。资料和窗口可以按需选，不会现在打开或移动。" }
                    PaperField { id: nameInput; objectName: "resumptionName"; Layout.fillWidth: true; placeholderText: "给这一段起个名字"; maximumLength: 120 }
                    TextArea {
                        id: nextInput; objectName: "resumptionNextStep"
                        Layout.fillWidth: true; Layout.preferredHeight: 100
                        placeholderText: "回来后，先做什么？"; wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.PlainText; selectByMouse: true
                        font.pixelSize: 15; color: "#353b45"; padding: 12
                        background: Rectangle { radius: 10; color: "white"; border.color: "#e6e8ee" }
                    }
                    Hint { visible: nextInput.text.length > 2000; text: "下一步最多 2000 字，请缩短后再保存。"; color: "#985c69" }
                    ComboBox {
                        id: taskChoice; objectName: "resumptionTaskChoice"
                        Layout.fillWidth: true; model: shelf.taskChoices; textRole: "title"
                        implicitHeight: 42
                        background: Rectangle { radius: 10; color: "white"; border.color: "#e6e8ee" }
                        contentItem: Text { text: taskChoice.displayText; textFormat: Text.PlainText; color: "#687180"; font.pixelSize: 13; leftPadding: 12; rightPadding: 28; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                    }
                    SectionLabel { text: "要带上的资料" }
                    RowLayout {
                        Layout.fillWidth: true
                        QuietAction { objectName: "resumptionChooseFile"; text: "选文件"; onClicked: filePicker.open() }
                        QuietAction { text: "选文件夹"; onClicked: folderPicker.open() }
                        QuietAction {
                            objectName: "resumptionCurrentPaper"
                            text: "当前论文"; visible: Boolean((shelf.appBackend.paperState.current || {}).paper_id)
                            onClicked: {
                                var paper = shelf.appBackend.paperState.current
                                shelf.addSource(paper.source_uri)
                                if (!nameInput.text) nameInput.text = paper.title
                                if (!nextInput.text) nextInput.text = paper.next_step || ("从第 " + paper.last_page + " 页继续")
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        PaperField { id: sourceInput; objectName: "resumptionSource"; Layout.fillWidth: true; placeholderText: "粘贴网页或本机路径"; maximumLength: 4096 }
                        QuietAction { objectName: "resumptionAddSource"; text: "加入"; enabled: shelf.draftSources.length < 12 && sourceInput.text.trim() !== ""; onClicked: { shelf.addSource(sourceInput.text); sourceInput.clear() } }
                    }
                    Repeater {
                        model: shelf.draftSources
                        RowLayout {
                            required property string modelData
                            Layout.fillWidth: true
                            Hint { text: modelData; maximumLineCount: 2; elide: Text.ElideMiddle }
                            QuietAction { text: "移除"; onClicked: shelf.draftSources = shelf.draftSources.filter(function(v) { return v !== modelData }) }
                        }
                    }
                    Choice { id: showWindows; objectName: "resumptionShowWindows"; text: "也留下窗口位置（可选）" }
                    Hint { visible: showWindows.checked; text: "最多列 24 个可确认身份的窗口；不保存窗口标题或内部内容。已收起的窗口不纳入。" }
                    QuietAction { objectName: "resumptionRefreshWindows"; visible: showWindows.checked; text: "刷新窗口列表"; enabled: !shelf.state.busy; onClicked: shelf.controller.refreshCapture() }
                    Repeater {
                        model: showWindows.checked ? (shelf.state.draft.windows || []) : []
                        Choice {
                            required property var modelData
                            required property int index
                            objectName: "resumptionWindow_" + index
                            Layout.fillWidth: true
                            text: (index + 1) + ". " + modelData.label + " · " + modelData.state
                            checked: shelf.selectedWindows.indexOf(modelData.id) !== -1
                            onToggled: shelf.selectedWindows = shelf.toggle(shelf.selectedWindows, modelData.id, checked)
                        }
                    }
                    Hint { visible: showWindows.checked && !(shelf.state.draft.windows || []).length; text: "当前没有可保存的窗口，仍可以留下下一步和资料。" }
                    ErrorLine {}
                }
            }
            RowLayout {
                Layout.fillWidth: true
                QuietAction { text: "取消"; enabled: !shelf.state.busy; onClicked: saveSheet.close() }
                Item { Layout.fillWidth: true }
                QuietAction {
                    objectName: "resumptionSave"; text: shelf.state.busy ? "正在保存…" : "留好这一段"; primary: true
                    enabled: !shelf.state.busy && nameInput.text.trim() !== "" && nextInput.text.trim() !== "" && nextInput.text.length <= 2000
                    onClicked: shelf.controller.save({captureId: shelf.state.draft.id, title: nameInput.text, nextStep: nextInput.text,
                        taskId: (shelf.taskChoices[taskChoice.currentIndex] || {}).id || "", sources: shelf.draftSources,
                        windowIds: showWindows.checked ? shelf.selectedWindows : []})
                }
            }
        }
    }

    PaperDialog {
        id: previewSheet; objectName: "resumptionPreviewSheet"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620, parent.width - 32)
        height: Math.min(parent.height - 32, Math.max(380, previewContent.implicitHeight + 150))
        title: shelf.state.preview.title || "继续刚才"
        closePolicy: shelf.state.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        onClosed: { if (!shelf.state.busy) shelf.controller.cancel() }
        contentItem: ColumnLayout {
            spacing: 16
            ScrollView {
                id: previewScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    id: previewContent
                    width: previewScroll.availableWidth; spacing: 12
                    Hint { text: "下一步" }
                    Text { objectName: "resumptionPreviewStep"; Layout.fillWidth: true; text: shelf.state.preview.next_step || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 22; lineHeight: 1.25; color: "#353b45" }
                    Hint { visible: Boolean(shelf.state.preview.task_title); text: "关联任务：" + (shelf.state.preview.task_title || "") + (shelf.state.preview.taskAvailable ? "" : "（已完成、归档或不可用）") }
                    QuietAction {
                        objectName: "resumptionFocusTask"
                        visible: Boolean(shelf.state.preview.taskAvailable) && !shelf.appBackend.focusStatus.active
                        text: "为这件事专注 25 分钟"
                        enabled: !shelf.state.busy
                        onClicked: {
                            var result = shelf.appBackend.focusStartTask(shelf.state.preview.task_id, 25)
                            if (result && result.ok === false) shelf.previewNotice = result.error
                            else previewSheet.close()
                        }
                    }
                    Hint { visible: Boolean(shelf.previewNotice); text: shelf.previewNotice; color: "#985c69" }
                    SectionLabel { visible: (shelf.state.preview.resources || []).length > 0; text: "按需打开资料" }
                    Repeater {
                        model: shelf.state.preview.resources || []
                        ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true; spacing: 2
                            Choice { objectName: "resumeResource_" + index; text: modelData.label; enabled: modelData.available; checked: shelf.selectedResources.indexOf(modelData.id) !== -1; onToggled: shelf.selectedResources = shelf.toggle(shelf.selectedResources, modelData.id, checked) }
                            Hint { text: modelData.reason || modelData.source; maximumLineCount: 3; elide: Text.ElideMiddle }
                            Hint { visible: Boolean(modelData.note); text: modelData.note || ""; color: "#985c69" }
                        }
                    }
                    SectionLabel { visible: (shelf.state.preview.windows || []).length > 0; text: "按需恢复窗口位置" }
                    Repeater {
                        model: shelf.state.preview.windows || []
                        ColumnLayout {
                            required property var modelData
                            required property int index
                            Layout.fillWidth: true; spacing: 2
                            Choice { objectName: "resumeWindow_" + index; text: modelData.label + " · " + modelData.state; enabled: modelData.available; checked: shelf.selectedWindows.indexOf(modelData.id) !== -1; onToggled: shelf.selectedWindows = shelf.toggle(shelf.selectedWindows, modelData.id, checked) }
                            Hint { visible: Boolean(modelData.reason); text: modelData.reason }
                        }
                    }
                    Hint { text: "不会恢复未保存内容、网页滚动位置或软件内部状态。关闭的窗口不会重建；预览后又有变化的文件会跳过。" }
                    ErrorLine {}
                }
            }
            RowLayout {
                Layout.fillWidth: true
                QuietAction { text: "先不恢复"; enabled: !shelf.state.busy; onClicked: previewSheet.close() }
                Item { Layout.fillWidth: true }
                QuietAction {
                    objectName: "resumptionConfirm"; primary: true
                    text: shelf.state.busy ? "正在处理…" : shelf.selectedWindows.length + shelf.selectedResources.length > 0 ? "执行选中的 " + (shelf.selectedWindows.length + shelf.selectedResources.length) + " 项" : "记住了"
                    enabled: !shelf.state.busy
                    onClicked: shelf.controller.confirm(shelf.state.preview.token, shelf.selectedWindows, shelf.selectedResources)
                }
            }
        }
    }

    PaperDialog {
        id: librarySheet; objectName: "resumptionLibrarySheet"
        parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(620, parent.width - 32); height: Math.min(560, parent.height - 32)
        title: "留过的片刻"
        contentItem: ColumnLayout {
            spacing: 12
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8
                model: shelf.page.items
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: itemColumn.implicitHeight + 24; radius: 12; color: "white"
                    ColumnLayout {
                        id: itemColumn; x: 12; y: 12; width: parent.width - 24; spacing: 6
                        SectionLabel { text: modelData.title; elide: Text.ElideRight }
                        Hint { text: modelData.next_step; maximumLineCount: 2; elide: Text.ElideRight }
                        RowLayout {
                            Layout.fillWidth: true
                            Hint { text: modelData.created_at.slice(0, 10) }
                            QuietAction { text: "预览"; enabled: !shelf.state.busy; onClicked: { librarySheet.close(); shelf.controller.preview(modelData.record_id) } }
                            QuietAction { objectName: "resumptionDelete_" + modelData.record_id; text: "移除"; enabled: !shelf.state.busy; onClicked: { shelf.deleteItem = modelData; deleteSheet.open() } }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                QuietAction { text: "上一页"; visible: shelf.page.offset > 0; enabled: !shelf.state.busy; onClicked: shelf.controller.refresh(shelf.page.offset - 20) }
                QuietAction { text: "下一页"; visible: shelf.page.hasMore; enabled: !shelf.state.busy; onClicked: shelf.controller.refresh(shelf.page.offset + 20) }
                Item { Layout.fillWidth: true }
                QuietAction { text: "收起"; onClicked: librarySheet.close() }
            }
        }
    }
    PaperDialog {
        id: deleteSheet; parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(430, parent.width - 32); title: "移除这份记录？"
        closePolicy: shelf.state.busy ? Popup.NoAutoClose : Popup.CloseOnEscape
        contentItem: ColumnLayout {
            Hint { text: "只删除“" + (shelf.deleteItem.title || "") + "”的续接记录，不删除任务、原文件或网页。" }
            ErrorLine {}
            RowLayout {
                Item { Layout.fillWidth: true }
                QuietAction { text: "保留"; enabled: !shelf.state.busy; onClicked: deleteSheet.close() }
                QuietAction { objectName: "resumptionDeleteConfirm"; text: "移除记录"; primary: true; enabled: !shelf.state.busy; onClicked: shelf.controller.delete(shelf.deleteItem.record_id) }
            }
        }
    }
    PaperDialog {
        id: resultSheet; parent: Overlay.overlay; anchors.centerIn: parent
        width: Math.min(520, parent.width - 32); height: Math.min(420, parent.height - 32); title: "这次接回了什么"
        contentItem: ColumnLayout {
            Hint { text: shelf.notice }
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 12
                model: shelf.state.result.items || []
                delegate: Text { required property var modelData; width: ListView.view.width; text: modelData.label + " · " + modelData.reason; textFormat: Text.PlainText; color: "#687180"; font.pixelSize: 13; wrapMode: Text.Wrap }
            }
            QuietAction { Layout.alignment: Qt.AlignRight; text: "收好"; onClicked: resultSheet.close() }
        }
    }
    Dialogs.FileDialog { id: filePicker; title: "选择这一段要用的资料"; nameFilters: ["文档与图片 (*.pdf *.txt *.md *.csv *.docx *.xlsx *.pptx *.png *.jpg *.webp)"]; onAccepted: shelf.addSource(selectedFile.toString()) }
    Dialogs.FolderDialog { id: folderPicker; title: "选择资料文件夹"; onAccepted: shelf.addSource(selectedFolder.toString()) }
}
