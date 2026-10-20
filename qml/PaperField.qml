import QtQuick
import QtQuick.Controls

TextField {
    id: field
    implicitHeight: 42
    font.pixelSize: 14
    color: "#353b45"; placeholderTextColor: "#9297a1"
    leftPadding: 12; rightPadding: 12
    selectByMouse: true
    background: Rectangle { color: "white"; radius: 10; border.color: field.activeFocus ? "#985c69" : "#e6e8ee" }
}
