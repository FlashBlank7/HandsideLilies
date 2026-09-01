import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: bubbleWindow
    objectName: "companionBubbleWindow"
    transientParent: null
    required property var controller
    // Injected by app.py in production.  It acknowledges only after the
    // native QQuickWindow is truly exposed; it never receives bubble prose.
    property var nativePresentationController: null
    property string uiFontFamily: Qt.application.font.family
    property color paperColor: "#fffdf8"
    property color inkColor: "#4d4a45"
    property color mutedColor: "#8e887e"
    property color hairlineColor: "#d4c6b3"
    property color cordColor: "#9f3129"
    property bool suppressed: false
    property real anchorX: Screen.virtualX + Screen.width - 200
    property real anchorY: Screen.virtualY + Screen.height - 280
    // Optional subject bounds let Main keep the bubble clear of the whole
    // character instead of treating one point inside the artwork as an edge.
    // Point-sized defaults preserve the original anchorX/anchorY contract.
    property real subjectLeft: anchorX
    property real subjectRight: anchorX
    property real subjectCenterY: anchorY + height / 2
    property var bubbleData: controller ? controller.bubble : ({})
    property string bubbleId: String(bubbleData.id || "")
    // A malformed payload must look like a generation failure, never like a
    // canned observation that Lilith supposedly decided to say.
    property string summaryText: String(bubbleData.summary || "").trim() || "这次没有生成可显示的内容。"
    property string detailText: String(bubbleData.detail || "").trim() || summaryText
    readonly property bool effectiveBusy: Boolean(controller && controller.busy)
                                               || Boolean(bubbleData.busy)
    readonly property bool detailIncludesSummary: detailText.indexOf(summaryText) >= 0
    readonly property string expandedDisplayText: detailIncludesSummary
        ? detailText : summaryText + "\n\n" + detailText
    readonly property int collapsedCharacterLimit: Math.max(96,
        Math.floor(Math.max(1, collapsedWidth - 54) / 15) * 7)
    readonly property bool collapsedTextTruncated:
        summaryText.length > collapsedCharacterLimit
    readonly property string collapsedDisplayText: collapsedTextTruncated
        ? summaryText.slice(0, collapsedCharacterLimit - 1).trim() + "…"
        : summaryText
    property bool hasDetail: detailText !== summaryText || collapsedTextTruncated
    property bool detailPinned: false
    property bool replying: false
    // A new bubble can replace the current one while this native window is
    // already visible (for example, “换一个”). Track every presentation so
    // the new card can be raised again without taking keyboard focus.
    property int presentationRevision: 0
    readonly property bool expanded: detailPinned || replying
    readonly property real screenMargin: 12
    readonly property real sideGap: 10
    readonly property real maximumWindowWidth: Math.max(260, Screen.width - screenMargin * 2)
    readonly property real maximumWindowHeight: Math.max(220, Screen.height - screenMargin * 2)
    readonly property bool sourceVisible: Boolean(bubbleData.source && bubbleData.source.name)
    readonly property bool sourceIsContext:
        String(bubbleData.sourceRole || "").toLowerCase() === "context"
    readonly property int auxiliaryRowCount: (sourceVisible ? 1 : 0)
                                              + (effectiveBusy ? 1 : 0)
    readonly property real collapsedChromeHeight: 28
        + headerRow.implicitHeight + actionRow.implicitHeight
        + (sourceVisible ? sourceLabel.implicitHeight : 0)
        + (effectiveBusy ? busyLabel.implicitHeight : 0)
        + 9 * (2 + auxiliaryRowCount)
        + 8
    readonly property real expandedChromeHeight: collapsedChromeHeight
        + (replying ? replyRow.implicitHeight + 9 : 0)
    property real collapsedWidth: Math.min(maximumWindowWidth, Math.max(380, Math.min(480,
        300 + Math.sqrt(Math.max(1, summaryText.length)) * 15)))
    property real expandedWidth: Math.min(maximumWindowWidth, Math.max(collapsedWidth, 560))
    property real collapsedHeight: Math.min(maximumWindowHeight, Math.max(224,
        Math.min(340, summaryMeasure.implicitHeight + collapsedChromeHeight + 12)))
    property real expandedHeight: Math.min(maximumWindowHeight, Math.max(replying ? 330 : 250,
        Math.min(540, detailMeasure.implicitHeight + expandedChromeHeight + 12)))
    readonly property real leftSideRoom: subjectLeft - Screen.virtualX - screenMargin
    readonly property real rightSideRoom: Screen.virtualX + Screen.width
                                           - subjectRight - screenMargin
    // Choose from subject geometry only so width animation cannot make the
    // bubble jump across the character while expanding into reply mode.
    readonly property bool placeOnRight: rightSideRoom >= leftSideRoom

    width: expanded ? expandedWidth : collapsedWidth
    height: expanded ? expandedHeight : collapsedHeight
    x: Math.max(Screen.virtualX + screenMargin,
                Math.min(placeOnRight ? subjectRight + sideGap
                                      : subjectLeft - width - sideGap,
                         Screen.virtualX + Screen.width - width - screenMargin))
    y: Math.max(Screen.virtualY + screenMargin,
                Math.min(subjectCenterY - height / 2,
                         Screen.virtualY + Screen.height - height - screenMargin))
    visible: Boolean(bubbleData.visible) && !suppressed
    color: "transparent"
    title: "莉莉丝"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
           | (!replying ? Qt.WindowDoesNotAcceptFocus : 0)
    function syncPresentationSuppression() {
        if (suppressed && nativePresentationController
                && typeof nativePresentationController.cancelPending === "function")
            nativePresentationController.cancelPending()
        if (controller
                && typeof controller.setPresentationSuppressed === "function")
            controller.setPresentationSuppressed(Boolean(suppressed))
    }
    function dismissExplicitly() {
        if (!controller)
            return
        if (typeof controller.dismissExplicit === "function")
            controller.dismissExplicit()
        else if (typeof controller.dismiss === "function")
            controller.dismiss()
    }
    function acknowledgeInteraction(reason) {
        if (controller
                && typeof controller.acknowledgeInteraction === "function")
            controller.acknowledgeInteraction(bubbleId, String(reason || "detail"))
    }
    function acknowledgePresentation() {
        if (!controller || !bubbleId || suppressed || !bubbleWindow.visible)
            return
        if (nativePresentationController
                && typeof nativePresentationController.requestAck === "function") {
            nativePresentationController.requestAck(bubbleId, presentationRevision)
            return
        }
        // Component-level tests have no app-owned native bridge.  Keep a
        // narrowly scoped offscreen fallback; real desktop platforms fail
        // closed and let the controller preserve the bubble as unread.
        if (String(Qt.platform.pluginName || "").toLowerCase() === "offscreen"
                && typeof controller.ackPresented === "function")
            controller.ackPresented(
                bubbleId,
                Boolean(bubbleWindow.visible),
                Boolean(bubbleWindow.visible && !suppressed),
                presentationRevision)
    }
    Component.onCompleted: syncPresentationSuppression()
    onSuppressedChanged: syncPresentationSuppression()
    function presentWithoutFocus() {
        presentationRevision += 1
        raise()
        // Let the new flags/geometry reach the platform plugin, then repeat
        // the Z-order request. Deliberately do not call requestActivate().
        Qt.callLater(function() {
            if (bubbleWindow.visible && !bubbleWindow.replying)
                bubbleWindow.raise()
            Qt.callLater(function() { bubbleWindow.acknowledgePresentation() })
        })
    }
    onVisibleChanged: {
        if (visible) {
            presentWithoutFocus()
        } else {
            resetBubbleUi()
        }
    }
    onBubbleIdChanged: {
        resetBubbleUi()
        if (visible)
            presentWithoutFocus()
    }

    function resetBubbleUi() {
        detailPinned = false
        replying = false
        // This handler can run while the component is still being completed,
        // so defer access to the editor object itself.
        Qt.callLater(function() {
            if (!bubbleWindow.visible || !bubbleWindow.replying) {
                replyInput.focus = false
                replyInput.clear()
            }
        })
    }

    function beginReply() {
        acknowledgeInteraction("reply")
        replying = true
        detailPinned = true
        // The native WindowDoesNotAcceptFocus flag must be removed before
        // activation. Deferring both operations makes reply focus explicit
        // and avoids racing the flags binding on Windows.
        Qt.callLater(function() {
            if (!bubbleWindow.visible || !bubbleWindow.replying)
                return
            bubbleWindow.requestActivate()
            replyInput.forceActiveFocus()
        })
    }

    function finishReplyFocus() {
        replyInput.focus = false
        replying = false
    }

    component PaperActionButton: Rectangle {
        id: paperAction
        property string text: ""
        signal clicked()
        implicitWidth: Math.max(76, actionLabel.implicitWidth + 28)
        implicitHeight: 40
        radius: 12
        color: !enabled ? "#f5f1eb"
                        : (actionHover.hovered ? "#efe7dc" : "#faf6ef")
        border.color: actionHover.hovered && enabled
                      ? bubbleWindow.cordColor : bubbleWindow.hairlineColor
        opacity: enabled ? 1.0 : 0.48
        Accessible.role: Accessible.Button
        Accessible.name: text
        Text {
            id: actionLabel
            anchors.centerIn: parent
            text: paperAction.text
            color: bubbleWindow.inkColor
            font.family: bubbleWindow.uiFontFamily
            font.pixelSize: 14
        }
        HoverHandler {
            id: actionHover
            enabled: paperAction.enabled
            cursorShape: Qt.PointingHandCursor
        }
        TapHandler {
            enabled: paperAction.enabled
            onTapped: paperAction.clicked()
        }
    }

    Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

    Text {
        id: summaryMeasure
        visible: false
        width: Math.max(1, bubbleWindow.collapsedWidth - 44)
        text: bubbleWindow.collapsedDisplayText
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font.family: bubbleWindow.uiFontFamily
        font.pixelSize: 15
    }

    Text {
        id: detailMeasure
        visible: false
        width: Math.max(1, bubbleWindow.expandedWidth - 44)
        text: bubbleWindow.expandedDisplayText
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font.family: bubbleWindow.uiFontFamily
        font.pixelSize: 15
    }

    Rectangle {
        anchors.fill: parent
        radius: expanded ? 26 : 21
        color: paperColor
        border.color: hairlineColor
        border.width: 1

        Rectangle {
            id: facingAccent
            objectName: "companionFacingAccent"
            anchors.left: bubbleWindow.placeOnRight ? parent.left : undefined
            anchors.right: bubbleWindow.placeOnRight ? undefined : parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 6
            radius: 3
            color: cordColor
            opacity: 0.78
        }

        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 22
            anchors.rightMargin: 20
            anchors.topMargin: 14
            anchors.bottomMargin: 14
            spacing: 10

            RowLayout {
                id: headerRow
                objectName: "companionHeaderRow"
                Layout.fillWidth: true
                spacing: 7
                Rectangle {
                    Layout.preferredWidth: 10
                    Layout.preferredHeight: 10
                    radius: 5
                    color: bubbleWindow.effectiveBusy ? "#dffdf8" : cordColor
                    opacity: 0.78
                    SequentialAnimation on opacity {
                        running: bubbleWindow.effectiveBusy
                        loops: Animation.Infinite
                        NumberAnimation { from: 0.28; to: 1.0; duration: 720 }
                        NumberAnimation { from: 1.0; to: 0.28; duration: 720 }
                    }
                }
                Label {
                    objectName: "companionCategoryLabel"
                    text: String(bubbleData.category || "莉莉丝")
                    color: inkColor
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 14
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Layout.maximumWidth: Math.max(90, bubbleWindow.width * 0.34)
                }
                Label {
                    id: sceneLabel
                    objectName: "companionSceneLabel"
                    Layout.fillWidth: true
                    text: bubbleData.sceneLabel ? "· " + String(bubbleData.sceneLabel) : ""
                    visible: text.length > 0
                    color: mutedColor
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                ToolButton {
                    objectName: "companionMenuButton"
                    text: "..."
                    Accessible.name: "更多陪伴操作"
                    Layout.minimumWidth: Math.max(36, implicitWidth)
                    Layout.preferredWidth: Math.max(36, implicitWidth)
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    Layout.preferredHeight: Math.max(36, implicitHeight)
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 13
                    enabled: !bubbleWindow.effectiveBusy
                    onClicked: bubbleMenu.open()
                    Menu {
                        id: bubbleMenu
                        font.family: bubbleWindow.uiFontFamily
                        Menu {
                            title: "想听什么"
                            MenuItem { text: "科普"; onTriggered: controller.requestCategory("科普") }
                            MenuItem { text: "吐槽"; onTriggered: controller.requestCategory("吐槽") }
                            MenuItem { text: "笑话"; onTriggered: controller.requestCategory("笑话") }
                            MenuItem { text: "哲思"; onTriggered: controller.requestCategory("哲思") }
                            MenuItem { text: "新闻"; onTriggered: controller.requestCategory("新闻") }
                            MenuItem { text: "科研进展"; onTriggered: controller.requestCategory("科研进展") }
                            MenuItem { text: "盒中世界"; onTriggered: controller.requestCategory("盒中世界") }
                        }
                        MenuItem { text: "减少频率"; onTriggered: controller.setFrequency("quiet", 45, 6) }
                        MenuItem { text: "暂停一小时"; onTriggered: controller.snooze(60) }
                        MenuItem { text: "此应用静默"; onTriggered: controller.muteCurrentApp() }
                        MenuSeparator {}
                        MenuItem {
                            text: "打开来源"
                            enabled: Boolean(bubbleData.source)
                                     && Boolean(bubbleData.source.url)
                            onTriggered: controller.openSource()
                        }
                        MenuItem {
                            text: "保存此刻"
                            enabled: Boolean(bubbleData.hasCapture)
                            onTriggered: controller.saveMoment()
                        }
                        MenuItem { text: "转入盒子"; onTriggered: controller.moveToBox() }
                        MenuItem { text: "收起气泡"; onTriggered: bubbleWindow.dismissExplicitly() }
                    }
                }
                ToolButton {
                    objectName: "companionCloseButton"
                    text: "×"
                    Accessible.name: "收起陪伴气泡"
                    Layout.minimumWidth: Math.max(36, implicitWidth)
                    Layout.preferredWidth: Math.max(36, implicitWidth)
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    Layout.preferredHeight: Math.max(36, implicitHeight)
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 16
                    onClicked: bubbleWindow.dismissExplicitly()
                }
            }

            Label {
                id: sourceLabel
                objectName: "companionSourceLabel"
                Layout.fillWidth: true
                visible: bubbleWindow.sourceVisible
                text: bubbleWindow.sourceVisible
                      ? (bubbleWindow.sourceIsContext ? "起始来源 · " : "")
                        + String(bubbleData.source.name)
                        + (bubbleData.source.publishedAt
                           ? " · " + String(bubbleData.source.publishedAt).slice(0, 10) : "")
                      : ""
                color: "#6f817c"
                font.family: bubbleWindow.uiFontFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }

            ScrollView {
                id: bodyScroll
                objectName: "companionBodyScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 54
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical: ScrollBar {
                    id: bodyVerticalBar
                    objectName: "companionBodyScrollBar"
                    parent: bodyScroll
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    policy: bubbleWindow.expanded
                            ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
                    interactive: true
                    width: 8
                    contentItem: Rectangle {
                        implicitWidth: 6
                        radius: width / 2
                        color: bodyVerticalBar.pressed ? bubbleWindow.cordColor
                                                       : "#9b8c7c"
                        opacity: bodyVerticalBar.active ? 0.72 : 0.42
                    }
                    background: Rectangle {
                        implicitWidth: 8
                        radius: width / 2
                        color: "#eee7dc"
                        opacity: bodyVerticalBar.active ? 0.62 : 0.34
                    }
                }
                TextArea {
                    id: bodyText
                    objectName: "companionBodyText"
                    width: bodyScroll.availableWidth
                    text: bubbleWindow.expanded
                          ? bubbleWindow.expandedDisplayText
                          : bubbleWindow.collapsedDisplayText
                    readOnly: true
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    color: inkColor
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 15
                    leftPadding: 4
                    rightPadding: 10
                    topPadding: 6
                    bottomPadding: 6
                    selectByMouse: true
                    background: null
                }
            }

            Label {
                id: busyLabel
                objectName: "companionBusyLabel"
                Layout.fillWidth: true
                visible: bubbleWindow.effectiveBusy
                text: "正在整理……"
                color: mutedColor
                font.family: bubbleWindow.uiFontFamily
                font.pixelSize: 11
                horizontalAlignment: Text.AlignRight
            }

            RowLayout {
                id: actionRow
                objectName: "companionActionRow"
                Layout.fillWidth: true
                spacing: 8
                PaperActionButton {
                    objectName: "companionAnotherButton"
                    text: "换一个"
                    Layout.minimumWidth: implicitWidth
                    Layout.preferredHeight: implicitHeight
                    enabled: !bubbleWindow.effectiveBusy
                    onClicked: controller.another(String(bubbleData.id || ""))
                }
                PaperActionButton {
                    objectName: "companionDetailButton"
                    visible: bubbleWindow.hasDetail || bubbleWindow.detailPinned
                    text: bubbleWindow.detailPinned ? "简单点" : "详细点"
                    Layout.minimumWidth: implicitWidth
                    Layout.preferredHeight: implicitHeight
                    enabled: !bubbleWindow.replying && !bubbleWindow.effectiveBusy
                    onClicked: {
                        bubbleWindow.acknowledgeInteraction("detail")
                        bubbleWindow.detailPinned = !bubbleWindow.detailPinned
                    }
                }
                PaperActionButton {
                    objectName: "companionReplyButton"
                    text: "回复"
                    Layout.minimumWidth: implicitWidth
                    Layout.preferredHeight: implicitHeight
                    enabled: !bubbleWindow.effectiveBusy
                    onClicked: bubbleWindow.beginReply()
                }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                id: replyRow
                objectName: "companionReplyRow"
                Layout.fillWidth: true
                visible: bubbleWindow.replying
                spacing: 8
                TextArea {
                    id: replyInput
                    objectName: "companionReplyInput"
                    Accessible.name: "陪伴气泡回复内容"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 150
                    Layout.minimumHeight: 80
                    Layout.preferredHeight: 80
                    placeholderText: "认真回复，或说“想听听别的”"
                    wrapMode: Text.Wrap
                    color: inkColor
                    font.family: bubbleWindow.uiFontFamily
                    font.pixelSize: 14
                    leftPadding: 12
                    rightPadding: 12
                    topPadding: 10
                    bottomPadding: 10
                    background: Rectangle {
                        color: "#ffffff"
                        radius: 12
                        border.color: replyInput.activeFocus ? "#6f817c" : hairlineColor
                        border.width: replyInput.activeFocus ? 2 : 1
                    }
                    Keys.onPressed: function(event) {
                        var enter = event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                        var newline = (event.modifiers & Qt.ShiftModifier) !== 0
                        if (enter && !newline && !inputMethodComposing) {
                            sendReply()
                            event.accepted = true
                        }
                    }
                    function sendReply() {
                        var value = text.trim()
                        if (!value || bubbleWindow.effectiveBusy) return
                        if (value === "想听听别的" || value === "换一个")
                            controller.another(String(bubbleData.id || ""))
                        else
                            controller.reply(String(bubbleData.id || ""), value)
                        clear()
                        bubbleWindow.finishReplyFocus()
                    }
                }
                PaperActionButton {
                    objectName: "companionSendButton"
                    text: "发送"
                    Layout.minimumWidth: implicitWidth
                    Layout.preferredHeight: implicitHeight
                    enabled: !bubbleWindow.effectiveBusy
                    onClicked: replyInput.sendReply()
                }
            }
        }
    }
}
