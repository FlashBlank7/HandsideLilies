import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root
    objectName: "focusDiversionBubbleWindow"
    transientParent: null
    required property var appBackend
    property bool suppressed: false
    property string uiFontFamily: "Microsoft YaHei UI"
    property real anchorX: Screen.virtualX + Screen.width - 220
    property real anchorY: Screen.virtualY + Screen.height - 300
    property real subjectLeft: anchorX
    property real subjectRight: anchorX
    property real subjectCenterY: anchorY + height / 2
    readonly property var bubbleData: appBackend ? appBackend.focusDiversion : ({})
    readonly property string sessionIdentity: String(bubbleData.sessionId || "")
    readonly property string titleText: String(bubbleData.title || "").trim() || "专注轻提醒"
    readonly property string messageText: String(bubbleData.text || "").trim()
                                          || "刚才的专注还在。要回去、把这段算作休息，还是结束专注？"
    readonly property real screenMargin: 12
    readonly property real sideGap: 10
    readonly property real maximumWindowWidth: Math.max(300, Screen.width - screenMargin * 2)
    readonly property real maximumWindowHeight: Math.max(210, Screen.height - screenMargin * 2)
    readonly property real preferredBodyHeight: Math.max(44, Math.min(104,
        messageMeasure.implicitHeight + 4))
    readonly property real preferredWindowWidth: Math.max(390,
        Math.min(480, actionGrid.threeColumnMinimum + 32))
    readonly property real preferredWindowHeight: 32 + 36 + 20
        + preferredBodyHeight + actionGrid.implicitHeight
    readonly property real leftSideRoom: subjectLeft - Screen.virtualX - screenMargin
    readonly property real rightSideRoom: Screen.virtualX + Screen.width
                                           - subjectRight - screenMargin
    readonly property bool placeOnRight: rightSideRoom >= leftSideRoom

    width: Math.min(maximumWindowWidth, preferredWindowWidth)
    height: Math.min(maximumWindowHeight, Math.max(200, preferredWindowHeight))
    x: Math.max(Screen.virtualX + screenMargin,
                Math.min(placeOnRight ? subjectRight + sideGap
                                      : subjectLeft - width - sideGap,
                         Screen.virtualX + Screen.width - width - screenMargin))
    y: Math.max(Screen.virtualY + screenMargin,
                Math.min(subjectCenterY - height / 2,
                         Screen.virtualY + Screen.height - height - screenMargin))
    visible: Boolean(bubbleData.visible) && !suppressed
    color: "transparent"
    title: "莉莉丝 · 专注"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
           | Qt.WindowDoesNotAcceptFocus

    Text {
        id: messageMeasure
        visible: false
        width: Math.max(1, root.width - 32)
        text: root.messageText
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font.family: root.uiFontFamily
        font.pixelSize: 14
    }

    Rectangle {
        anchors.fill: parent
        radius: 22
        color: "#fffaf2"
        border.color: "#d9cdbf"

        Rectangle {
            objectName: "focusDiversionFacingAccent"
            anchors.left: root.placeOnRight ? parent.left : undefined
            anchors.right: root.placeOnRight ? undefined : parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 5
            radius: 3
            color: "#92362f"
        }

        ColumnLayout {
            id: contentColumn
            objectName: "focusDiversionContentColumn"
            anchors.fill: parent
            anchors.margins: 16
            spacing: 10

            RowLayout {
                objectName: "focusDiversionHeaderRow"
                Layout.fillWidth: true
                Layout.minimumHeight: 36
                Label {
                    objectName: "focusDiversionTitleLabel"
                    Layout.fillWidth: true
                    text: root.titleText
                    color: "#494540"
                    font.family: root.uiFontFamily
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                ToolButton {
                    objectName: "focusDiversionCloseButton"
                    text: "×"
                    Accessible.name: "收起专注提醒"
                    Layout.minimumWidth: Math.max(36, implicitWidth)
                    Layout.preferredWidth: Math.max(36, implicitWidth)
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    Layout.preferredHeight: Math.max(36, implicitHeight)
                    font.family: root.uiFontFamily
                    font.pixelSize: 16
                    onClicked: root.appBackend.focusDiversionAction(
                                   "dismiss", root.sessionIdentity)
                }
            }

            ScrollView {
                id: bodyScroll
                objectName: "focusDiversionBodyScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: Math.min(44, root.preferredBodyHeight)
                Layout.preferredHeight: root.preferredBodyHeight
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded

                Label {
                    objectName: "focusDiversionBodyLabel"
                    width: bodyScroll.availableWidth
                    text: root.messageText
                    color: "#5f5952"
                    font.family: root.uiFontFamily
                    font.pixelSize: 14
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                }
            }

            GridLayout {
                id: actionGrid
                objectName: "focusDiversionActionGrid"
                Layout.fillWidth: true
                property real actionGap: 7
                readonly property real threeColumnMinimum:
                    returnButton.implicitWidth + restButton.implicitWidth
                    + finishButton.implicitWidth + actionGap * 2
                readonly property real twoColumnMinimum: Math.max(
                    returnButton.implicitWidth + restButton.implicitWidth + actionGap,
                    finishButton.implicitWidth)
                columns: width >= threeColumnMinimum ? 3
                         : (width >= twoColumnMinimum ? 2 : 1)
                columnSpacing: actionGap
                rowSpacing: actionGap
                Button {
                    id: returnButton
                    objectName: "focusDiversionReturnButton"
                    text: "回到刚才的工作"
                    Accessible.name: text
                    Layout.fillWidth: true
                    Layout.minimumWidth: implicitWidth
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    font.family: root.uiFontFamily
                    font.pixelSize: 13
                    onClicked: root.appBackend.focusDiversionAction(
                                   "return", root.sessionIdentity)
                }
                Button {
                    id: restButton
                    objectName: "focusDiversionRestButton"
                    text: "这是休息"
                    Accessible.name: text
                    Layout.fillWidth: true
                    Layout.minimumWidth: implicitWidth
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    font.family: root.uiFontFamily
                    font.pixelSize: 13
                    onClicked: root.appBackend.focusDiversionAction(
                                   "rest", root.sessionIdentity)
                }
                Button {
                    id: finishButton
                    objectName: "focusDiversionFinishButton"
                    text: "结束专注"
                    Accessible.name: text
                    Layout.fillWidth: true
                    Layout.minimumWidth: implicitWidth
                    Layout.minimumHeight: Math.max(36, implicitHeight)
                    font.family: root.uiFontFamily
                    font.pixelSize: 13
                    onClicked: root.appBackend.focusDiversionAction(
                                   "finish", root.sessionIdentity)
                }
            }
        }
    }
}
