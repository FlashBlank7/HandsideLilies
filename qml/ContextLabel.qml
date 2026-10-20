import QtQuick
import QtQuick.Controls
import QtQuick.Window

Label {
    id: label
    property var evidence: ({})
    property string extraDetail: ""
    readonly property string description: String(evidence.detail || "没有可核对的输入记录。")
        + (evidence.at ? "\n时间：" + new Date(evidence.at).toLocaleString(Qt.locale(), "M/d hh:mm:ss") : "")
        + (extraDetail ? "\n" + extraDetail : "")
    text: String(evidence.label || "依据未记录")
    textFormat: Text.PlainText
    color: "#7c8491"
    font.pixelSize: 12
    elide: Text.ElideRight
    Accessible.name: "这条内容的依据：" + text
    Accessible.description: description
    HoverHandler { id: evidenceHover }
    ToolTip {
        visible: evidenceHover.hovered && label.visible && Boolean(label.Window.window && label.Window.window.visible)
        delay: 450; timeout: 15000
        width: Math.min(330, Math.max(220, label.Window.window ? label.Window.window.width - 40 : 330))
        padding: 12
        background: Rectangle { radius: 10; color: "#fafafb"; border.color: "#e3e5e9" }
        contentItem: Text { text: label.description; textFormat: Text.PlainText; wrapMode: Text.Wrap; font.pixelSize: 12; color: "#59616d"; lineHeight: 1.3 }
    }
}
