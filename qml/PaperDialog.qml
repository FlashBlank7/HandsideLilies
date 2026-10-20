import QtQuick
import QtQuick.Controls

Dialog {
    id: dialog
    modal: true
    focus: true
    padding: 18
    background: Rectangle { radius: 16; color: "#fafafb"; border.color: "#e6e8ee" }
    header: Label {
        visible: dialog.title !== ""
        text: dialog.title; textFormat: Text.PlainText
        leftPadding: 18; rightPadding: 18; topPadding: 18; bottomPadding: 12
        font.family: "Microsoft YaHei UI"; font.pixelSize: 17
        color: "#353b45"; wrapMode: Text.Wrap
    }
}
