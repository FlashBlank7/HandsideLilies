import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs as Dialogs

Item {
    id: desk
    objectName: "paperDesk"
    required property var appBackend
    property var state: appBackend.paperState
    property var paper: state.current || ({})
    property string paperId: String(paper.paper_id || "")
    property string draftPaperId: ""
    property string notice: ""
    property bool adding: false
    property bool editingPosition: false
    property bool allCards: false
    property bool reviewing: false
    property bool findingPaper: false
    property var detailCard: ({})
    readonly property var visibleCards: state.cards || []
    readonly property var cardPage: state.cardPage || ({})

    function browse(offset) {
        appBackend.paperBrowseCards(allCards, reviewing, allCards ? searchInput.text : "", offset || 0)
    }
    onAllCardsChanged: browse(0)
    onReviewingChanged: browse(0)

    function act(method, args) {
        try {
            var result = appBackend[method].apply(appBackend, args || [])
            if (result && result.ok === false) { notice = result.error; return false }
            notice = result && result.note ? result.note : ""
            return true
        } catch (error) { notice = "这次没有完成，内容仍保留在这里。"; return false }
    }
    function selectedIndex() {
        return (state.papers || []).findIndex(function(item) { return item.paper_id === desk.paperId })
    }
    function syncPaper() {
        paperPicker.currentIndex = selectedIndex()
        var switched = draftPaperId !== paperId
        if (!switched && editingPosition) return
        draftPaperId = paperId
        pageDraft.text = String(paper.last_page || 1)
        locatorDraft.text = String(paper.locator || "")
        nextDraft.text = String(paper.next_step || "")
        if (switched) { editingPosition = false; reviewing = false }
    }
    function revealPaperRecord(targetId) {
        if (String(targetId) !== paperId) return
        // Source navigation must reveal the paper, even when it is already
        // selected underneath an unrelated all-cards search. It is not a save.
        allCards = false
        reviewing = false
        searchInput.text = ""
        libraryQuery.text = ""
        findingPaper = false
        notice = ""
        deleteDialog.close()
        cardDialog.close()
        detailCard = ({})
        appBackend.paperBrowseLibrary("", 0)
        browse(0)
        syncPaper() // Preserves an in-progress position draft for the same paper.
    }
    onStateChanged: Qt.callLater(syncPaper)
    Component.onCompleted: { syncPaper(); browse(0) }
    Connections {
        target: desk.appBackend
        function onPaperRecordOpened(paperId) { desk.revealPaperRecord(paperId) }
        function onPaperReviewRequested() { desk.allCards = false; desk.reviewing = true }
    }

    Dialogs.FileDialog {
        id: filePicker
        title: "选择这次要读的论文"
        nameFilters: ["PDF 文件 (*.pdf)"]
        onAccepted: {
            if (desk.act("paperAdd", ["", String(selectedFile)])) { desk.adding = false; desk.allCards = false }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text { text: "读到这里"; font.pixelSize: 23; color: "#353b45"; Layout.fillWidth: true }
            QuietAction { objectName: "paperLibraryTab"; text: "论文"; selected: !desk.allCards; onClicked: desk.allCards = false }
            QuietAction { objectName: "paperAllCardsTab"; text: "全部卡片"; selected: desk.allCards; onClicked: desk.allCards = true }
        }
        RowLayout {
            visible: !desk.allCards
            Layout.fillWidth: true; spacing: 6
            ComboBox {
                id: paperPicker; objectName: "paperPicker"
                Layout.fillWidth: true; Layout.minimumWidth: 80; implicitHeight: 40
                textRole: "title"; valueRole: "paper_id"; model: desk.state.papers || []
                displayText: desk.paperId ? desk.paper.title : "选择一篇论文"
                contentItem: Text { text: paperPicker.displayText; textFormat: Text.PlainText; color: "#687180"; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                leftPadding: 12; rightPadding: 28
                background: Rectangle { color: "#f1f2f5"; radius: 10 }
                indicator: Item {
                    x: paperPicker.width - 22; y: 16; width: 10; height: 8
                    Rectangle { x: 0; y: 2; width: 6; height: 1.5; rotation: 45; color: "#7c8491" }
                    Rectangle { x: 4; y: 2; width: 6; height: 1.5; rotation: -45; color: "#7c8491" }
                }
                onActivated: { desk.act("paperSelect", [String(currentValue)]); Qt.callLater(desk.syncPaper) }
            }
            QuietAction { objectName: "paperChoosePdf"; text: "选 PDF"; onClicked: filePicker.open() }
            QuietAction { objectName: "paperAddLinkToggle"; text: "添加链接"; selected: desk.adding; onClicked: desk.adding = !desk.adding }
        }
        RowLayout {
            visible: !desk.allCards && (Number((desk.state.library || {}).total || 0) > 10 || desk.findingPaper)
            Layout.fillWidth: true
            PaperField {
                id: libraryQuery; objectName: "paperLibrarySearch"; Layout.fillWidth: true; placeholderText: "找书架里的论文"
                onAccepted: { desk.findingPaper = true; appBackend.paperBrowseLibrary(text, 0) }
            }
            QuietAction { text: "找"; onClicked: { desk.findingPaper = true; appBackend.paperBrowseLibrary(libraryQuery.text, 0) } }
            QuietAction { text: "前页"; enabled: Number((desk.state.library || {}).offset || 0) > 0; onClicked: appBackend.paperBrowseLibrary(libraryQuery.text, desk.state.library.offset - desk.state.library.limit) }
            QuietAction { text: "后页"; enabled: Boolean((desk.state.library || {}).hasMore); onClicked: appBackend.paperBrowseLibrary(libraryQuery.text, desk.state.library.offset + desk.state.library.limit) }
        }
        PaperDialog {
            visible: desk.adding && !desk.allCards
            onClosed: desk.adding = false
            modal: true; focus: true; title: "放进书架"
            anchors.centerIn: Overlay.overlay
            width: Math.min(desk.width - 16, 520)
            contentItem: ColumnLayout {
            spacing: 8
            PaperField { id: sourceDraft; objectName: "paperSourceInput"; Layout.fillWidth: true; placeholderText: "论文网址，也可以粘贴本机 PDF 路径" }
            RowLayout {
                Layout.fillWidth: true
                PaperField { id: titleDraft; objectName: "paperTitleInput"; Layout.fillWidth: true; placeholderText: "论文名称（选填）" }
                QuietAction {
                    objectName: "paperAddButton"; text: "放进书架"; primary: true; enabled: sourceDraft.text.trim() !== ""
                    onClicked: if (desk.act("paperAdd", [titleDraft.text, sourceDraft.text])) { desk.adding = false; sourceDraft.clear(); titleDraft.clear() }
                }
            }
            Text { Layout.fillWidth: true; visible: desk.notice !== ""; text: desk.notice; textFormat: Text.PlainText; color: "#985c69"; wrapMode: Text.Wrap; font.pixelSize: 12 }
            }
        }
        Rectangle {
            visible: !desk.allCards && desk.paperId !== ""
            Layout.fillWidth: true
            implicitHeight: paperHeader.implicitHeight + 36
            radius: 16; color: "#ffffff"; border.color: "#e6e8ee"
            ColumnLayout {
                id: paperHeader
                anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 18; y: 18
                spacing: 8
                Text { Layout.fillWidth: true; text: desk.paper.title || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight; font.pixelSize: 19; color: "#353b45" }
                Text {
                    Layout.fillWidth: true
                    text: "第 " + (desk.paper.last_page || 1) + " 页" + (desk.paper.locator ? " · " + desk.paper.locator : "")
                    textFormat: Text.PlainText; color: "#7c8491"; font.pixelSize: 12; elide: Text.ElideRight
                }
                Text { Layout.fillWidth: true; visible: Boolean(desk.paper.next_step); text: "接着做：" + (desk.paper.next_step || ""); textFormat: Text.PlainText; color: "#687180"; font.pixelSize: 13; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight }
                RowLayout {
                    Layout.fillWidth: true
                    QuietAction {
                        objectName: "paperStartFinishButton"
                        text: desk.state.active ? "收起这篇" : "陪我读这篇"; primary: true
                        onClicked: {
                            if (desk.state.active) { appBackend.readingFinish(); desk.reviewing = true }
                            else { desk.act("paperStart", [desk.paperId]); desk.reviewing = false }
                        }
                    }
                    QuietAction { objectName: "paperOpenSourceButton"; text: "原文"; onClicked: desk.act("paperOpenSource", [desk.paperId, ""]) }
                    Item { Layout.fillWidth: true }
                    QuietAction { objectName: "paperPositionToggle"; text: "记进度"; selected: desk.editingPosition; onClicked: desk.editingPosition = !desk.editingPosition }
                }
                Text {
                    Layout.fillWidth: true
                    text: desk.state.active ? "划词保存时确认是否属于本篇；解释仍不带上下文。" : "页码由你标记，不会自动读取文档。"
                    font.pixelSize: 12; color: "#7c8491"; wrapMode: Text.Wrap
                }
            }
        }
        PaperDialog {
            visible: desk.editingPosition && !desk.allCards && desk.paperId !== ""
            onClosed: desk.editingPosition = false
            modal: true; focus: true; title: "记到这里"
            anchors.centerIn: Overlay.overlay
            width: Math.min(desk.width - 16, 520)
            contentItem: ColumnLayout {
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                Text { text: "页码"; color: "#7c8491"; font.pixelSize: 12 }
                PaperField { id: pageDraft; objectName: "paperPageInput"; Layout.preferredWidth: 74; validator: IntValidator { bottom: 1; top: 100000 } }
                PaperField { id: locatorDraft; objectName: "paperLocatorInput"; Layout.fillWidth: true; placeholderText: "位置，如：消融实验" }
            }
            RowLayout {
                Layout.fillWidth: true
                PaperField { id: nextDraft; objectName: "paperNextStepInput"; Layout.fillWidth: true; placeholderText: "下次接着做什么？" }
                QuietAction {
                    objectName: "paperSavePositionButton"; text: "记下"; primary: true
                    onClicked: if (desk.act("paperSavePosition", [desk.paperId, Number(pageDraft.text), locatorDraft.text, nextDraft.text])) desk.editingPosition = false
                }
            }
            Text { Layout.fillWidth: true; visible: desk.notice !== ""; text: desk.notice; textFormat: Text.PlainText; color: "#985c69"; wrapMode: Text.Wrap; font.pixelSize: 12 }
            }
        }
        RowLayout {
            visible: !desk.allCards && desk.paperId !== ""
            Layout.fillWidth: true
            QuietAction { objectName: "paperCardsTab"; text: "卡片"; selected: !desk.reviewing; onClicked: desk.reviewing = false }
            QuietAction { objectName: "paperReviewTab"; text: "整理 · " + Number((desk.state.review || {}).markedCount || 0); selected: desk.reviewing; onClicked: desk.reviewing = true }
            Item { Layout.fillWidth: true }
            QuietAction { objectName: "paperQuestionToggle"; text: "留个问题"; onClicked: questionDialog.open() }
        }
        RowLayout {
            visible: desk.allCards; Layout.fillWidth: true
            PaperField {
                id: searchInput; objectName: "paperCardSearch"; Layout.fillWidth: true
                placeholderText: "找原文、解释或一个问题"
                onAccepted: desk.browse(0)
                onTextChanged: if (!text) desk.browse(0)
            }
            QuietAction { text: "查找"; onClicked: desk.browse(0) }
        }
        RowLayout {
            Layout.fillWidth: true; visible: Number(desk.cardPage.total || 0) > Number(desk.cardPage.limit || 40)
            QuietAction { objectName: "paperCardsPrevious"; text: "上一页"; enabled: Number(desk.cardPage.offset || 0) > 0; onClicked: desk.browse(desk.cardPage.offset - desk.cardPage.limit) }
            Text { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; text: String(Number(desk.cardPage.offset || 0) + 1) + "–" + Math.min(Number(desk.cardPage.offset || 0) + desk.visibleCards.length, Number(desk.cardPage.total || 0)) + " / " + Number(desk.cardPage.total || 0); color: "#7c8491"; font.pixelSize: 12 }
            QuietAction { objectName: "paperCardsNext"; text: "下一页"; enabled: Boolean(desk.cardPage.hasMore); onClicked: desk.browse(desk.cardPage.offset + desk.cardPage.limit) }
        }
        Text { Layout.fillWidth: true; visible: desk.notice !== ""; text: desk.notice; textFormat: Text.PlainText; font.pixelSize: 12; color: "#985c69"; wrapMode: Text.Wrap; maximumLineCount: 3; elide: Text.ElideRight }
        ListView {
            id: cards
            objectName: "readingCardList"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 70
            model: desk.visibleCards; clip: true; spacing: 10
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: Rectangle {
                id: cardRow
                required property var modelData
                required property int index
                width: ListView.view.width; height: 124
                color: "white"; radius: 12; border.color: "#e6e8ee"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 14; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: cardRow.modelData.kind === "question-note" ? "你留下的问题" : cardRow.modelData.title; textFormat: Text.PlainText; font.pixelSize: 14; color: "#353b45"; elide: Text.ElideRight }
                        Text { visible: Boolean(cardRow.modelData.page); text: "p. " + (cardRow.modelData.page || ""); color: "#89929d"; font.pixelSize: 11 }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: cardRow.modelData.answer || cardRow.modelData.source_text
                        textFormat: Text.PlainText; font.pixelSize: 13; color: "#687180"
                        wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { Layout.fillWidth: true; text: cardRow.modelData.markLabel || "独立划词"; font.pixelSize: 11; color: "#985c69" }
                        QuietAction { objectName: "paperCardDetails_" + cardRow.index; text: "展开"; implicitHeight: 28; onClicked: { desk.detailCard = cardRow.modelData; cardDialog.open() } }
                    }
                }
            }
            Text {
                anchors.centerIn: parent; width: parent.width - 40; horizontalAlignment: Text.AlignHCenter
                visible: cards.count === 0; wrapMode: Text.Wrap; color: "#89929d"; font.pixelSize: 13
                text: desk.allCards ? "还没有保存的划词卡片。"
                    : !desk.paperId ? "选一篇手边的论文。\n不用搬走原文件，也不用换阅读器。"
                    : desk.reviewing ? "还没标记重点或问题。\n未整理的卡片不会混进来。"
                    : "读到不明白的地方，划词解释后点保存。\n也可以先留一个问题。"
            }
        }
    }
    PaperDialog {
        id: questionDialog; objectName: "paperQuestionDialog"
        modal: true; focus: true; title: "留下一个问题"
        anchors.centerIn: parent; width: Math.min(desk.width - 20, 500)
        background: Rectangle { color: "#fafafb"; radius: 14; border.color: "#e6e8ee" }
        contentItem: ColumnLayout {
            TextArea { id: questionInput; objectName: "paperQuestionInput"; Layout.fillWidth: true; implicitHeight: 120; placeholderText: "哪里还没想明白？"; wrapMode: TextEdit.Wrap; selectByMouse: true; font.pixelSize: 15; color: "#353b45" }
            Text { Layout.fillWidth: true; visible: desk.notice !== ""; text: desk.notice; textFormat: Text.PlainText; color: "#985c69"; wrapMode: Text.Wrap; font.pixelSize: 12 }
        }
        footer: DialogButtonBox {
            background: Item {}
            QuietAction { text: "取消"; onClicked: questionDialog.close() }
            QuietAction { objectName: "paperQuestionSave"; text: "记下"; primary: true; enabled: questionInput.text.trim() !== ""; onClicked: if (desk.act("paperLeaveQuestion", [desk.paperId, questionInput.text])) { questionInput.clear(); questionDialog.close() } }
        }
    }
    PaperDialog {
        id: cardDialog; objectName: "paperCardDialog"
        anchors.centerIn: parent; width: Math.min(desk.width - 16, 590)
        height: Math.min(desk.height - 20, Math.max(350, paperDetailText.contentHeight + 280))
        modal: true; focus: true; padding: 20
        title: desk.detailCard.kind === "question-note" ? "你留下的问题" : "阅读卡片"
        background: Rectangle { color: "#fafafb"; radius: 16; border.color: "#e6e8ee" }
        contentItem: ColumnLayout {
            spacing: 12
            Text { Layout.fillWidth: true; visible: Boolean(desk.detailCard.paper_title); text: desk.detailCard.paper_title || ""; textFormat: Text.PlainText; font.pixelSize: 15; color: "#353b45"; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: desk.detailCard.page ? "第 " + desk.detailCard.page + " 页 · " + (desk.detailCard.locator || "手动标记") : "独立划词，没有关联论文"; color: "#89929d"; font.pixelSize: 12; textFormat: Text.PlainText; wrapMode: Text.Wrap }
            ScrollView {
                id: detailScroll; Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                TextEdit {
                    id: paperDetailText
                    width: detailScroll.availableWidth
                    text: (desk.detailCard.source_text || "") + (desk.detailCard.answer ? "\n\n" + desk.detailCard.answer : "")
                    textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap
                    font.pixelSize: 15; color: "#424852"
                }
            }
            Text { Layout.fillWidth: true; text: "解释不带历史；保存的卡片可由记忆查阅。可在记忆页要求忘记。"; font.pixelSize: 12; color: "#7c8491"; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; visible: desk.notice !== ""; text: desk.notice; textFormat: Text.PlainText; color: "#985c69"; wrapMode: Text.Wrap; font.pixelSize: 12 }
            Flow {
                Layout.fillWidth: true; spacing: 4; visible: Boolean(desk.detailCard.paper_id)
                Repeater {
                    model: [{id: "understood", label: "明白了"}, {id: "question", label: "还没明白"}, {id: "citation", label: "值得引用"}, {id: "unmarked", label: "取消标记"}]
                    QuietAction {
                        required property var modelData
                        objectName: "paperMark_" + modelData.id; text: modelData.label
                        selected: desk.detailCard.mark === modelData.id
                        onClicked: if (desk.act("paperMarkCard", [desk.detailCard.paper_id, desk.detailCard.card_id, modelData.id])) { desk.detailCard = Object.assign({}, desk.detailCard, {mark: modelData.id}) }
                    }
                }
            }
        }
        footer: DialogButtonBox {
            background: Item {}
            QuietAction { objectName: "paperCardDelete"; text: "删除"; onClicked: deleteDialog.open() }
            QuietAction { objectName: "paperCardOpenSource"; text: "回到原文"; visible: Boolean(desk.detailCard.paper_id); onClicked: desk.act("paperOpenSource", [desk.detailCard.paper_id, desk.detailCard.card_id]) }
            QuietAction { objectName: "paperCardClose"; text: "收起"; onClicked: cardDialog.close() }
        }
    }
    PaperDialog {
        id: deleteDialog; modal: true; anchors.centerIn: parent; title: "删除这张卡片？"
        width: Math.min(desk.width - 20, 420)
        contentItem: Label { text: "只删除本地卡片，不改动原论文。"; color: "#687180" }
        footer: DialogButtonBox {
            background: Item {}
            QuietAction { text: "取消"; onClicked: deleteDialog.close() }
            QuietAction { objectName: "paperDeleteConfirm"; text: "删除卡片"; primary: true; onClicked: { appBackend.deleteReadingCard(desk.detailCard.card_id); deleteDialog.close(); cardDialog.close() } }
        }
    }
}
