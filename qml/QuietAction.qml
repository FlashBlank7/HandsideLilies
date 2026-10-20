import QtQuick
import QtQuick.Controls

Button {
    id: action
    property bool primary: false
    property bool selected: false
    implicitWidth: Math.max(72, label.implicitWidth + 28)
    implicitHeight: 40
    padding: 10
    contentItem: Text {
        id: label
        text: action.text; textFormat: Text.PlainText
        color: action.primary ? "#ffffff" : action.selected ? "#985c69" : "#687180"
        opacity: action.enabled ? 1 : 0.48
        font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter; horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 10
        color: action.primary ? (action.enabled ? "#985c69" : "#c6adb5")
               : action.selected ? "#f2e9ed" : action.hovered ? "#eceef2" : "transparent"
    }
}
