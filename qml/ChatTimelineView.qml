import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: timeline
    required property var timelineModel
    property bool busy: false
    property bool followTail: true
    // Only our paragraph markup is accepted. User/model text is always escaped;
    // pasted HTML, image URLs and links cannot become live rich-text content.
    function spacedPlainText(value) {
        var escaped = String(value).replace(/&/g, "&amp;")
                .replace(/</g, "&lt;").replace(/>/g, "&gt;")
                .replace(/\r\n?/g, "\n").replace(/\n/g, "<br/>")
        return "<p style='line-height:145%; white-space:pre-wrap; margin:0'>" + escaped + "</p>"
    }
    property color ink: "#30343a"

    function revealTail() {
        if (!followTail) return
        // A stable function identity coalesces requests. Do not force layout
        // from a delegate's document callback: at that point later rows may
        // still have their initial, incomplete height estimates.
        Qt.callLater(scrollToTail)
    }

    function scrollToTail() {
        if (followTail && messages.count > 0) messages.positionViewAtEnd()
    }

    Connections {
        target: timeline.timelineModel
        function onUpdated() { timeline.revealTail() }
        function onRowsInserted() { timeline.followTail = true; timeline.revealTail() }
        function onModelReset() { timeline.followTail = true; timeline.revealTail() }
    }

    Column {
        anchors.centerIn: parent
        visible: messages.count === 0
        spacing: 16
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            width: 32; height: 2; radius: 1; color: "#995c62"
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "她在这里。"
            font.pixelSize: 23; color: "#353a41"
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "说点什么，或把手边的问题交给她。"
            font.pixelSize: 13; color: "#83878d"
        }
    }

    ListView {
        id: messages
        objectName: "chatMessageList"
        anchors.fill: parent
        clip: true
        model: timeline.timelineModel
        spacing: 24
        topMargin: 18
        bottomMargin: 24
        boundsBehavior: Flickable.StopAtBounds
        reuseItems: true
        cacheBuffer: 240
        onContentHeightChanged: timeline.revealTail()
        onHeightChanged: timeline.revealTail()
        onMovementStarted: timeline.followTail = false
        onMovementEnded: timeline.followTail = atYEnd
        ScrollBar.vertical: ScrollBar {
            width: 6
            policy: ScrollBar.AsNeeded
            onPressedChanged: timeline.followTail = pressed ? false : messages.atYEnd
        }
        delegate: Item {
            id: messageRow
            required property int index
            objectName: "chatRow_" + index
            required property string roleName
            required property string messageText
            readonly property bool fromUser: roleName === "user"
            width: ListView.view.width
            height: messageBody.implicitHeight + 58
            Rectangle {
                x: messageRow.fromUser ? 40 : 0
                width: parent.width - (messageRow.fromUser ? 46 : 6)
                height: parent.height
                radius: 14
                color: messageRow.fromUser ? "#eff0f2" : "transparent"
                Column {
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.margins: 16
                    y: 12
                    spacing: 7
                    Text {
                        text: messageRow.fromUser ? "你" : "莉莉丝"
                        color: messageRow.fromUser ? "#858a91" : "#985c63"
                        font.pixelSize: 12
                    }
                    TextEdit {
                        id: messageBody
                        objectName: "chatMessageBody"
                        width: parent.width
                        text: timeline.spacedPlainText(messageRow.messageText || (timeline.busy ? "···" : ""))
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        textFormat: TextEdit.RichText
                        font.family: "Microsoft YaHei UI"
                        font.pixelSize: 16
                        color: timeline.ink
                        selectionColor: "#e2d6d9"
                        selectedTextColor: "#25282d"
                        // A fixed paragraph style keeps multiline replies airy;
                        // only this tail item changes during generation.
                    }
                }
            }
        }
    }
}
