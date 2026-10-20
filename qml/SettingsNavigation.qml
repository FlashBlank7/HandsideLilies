pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Filtering only changes visibility. Editors stay alive so changing a section
// never discards an unapplied frequency draft or writes a setting implicitly.
ColumnLayout {
    id: root
    required property string heading
    required property var sections
    required property var entries
    property string section: String(sections[0].id)
    property alias query: search.text
    readonly property string normalizedQuery: search.text.trim().toLocaleLowerCase()
    readonly property var matchingIds: {
        var tokens = normalizedQuery.split(/\s+/).filter(function(t) { return t.length > 0 })
        return entries.filter(function(entry) {
            if (!tokens.length) return entry.section === section
            var haystack = (entry.title + " " + (entry.keywords || "")).toLocaleLowerCase()
            return tokens.every(function(token) { return haystack.indexOf(token) >= 0 })
        }).map(function(entry) { return entry.id })
    }
    signal filterChanged()
    spacing: 16
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    onSectionChanged: filterChanged()
    onNormalizedQueryChanged: filterChanged()

    function matches(id) { return matchingIds.indexOf(id) >= 0 }
    function choose(id) { search.clear(); section = id }

    RowLayout {
        Layout.fillWidth: true
        spacing: 16
        Label {
            text: root.heading
            font.pixelSize: 24
            font.weight: Font.Medium
            color: "#30343d"
        }
        Item { Layout.fillWidth: true }
        TextField {
            id: search
            objectName: root.objectName + "Search"
            Layout.preferredWidth: Math.min(210, root.width * 0.46)
            Layout.minimumWidth: 100
            implicitHeight: 38
            leftPadding: 12
            rightPadding: 34
            placeholderText: "搜索设置"
            font.pixelSize: 13
            color: "#41454e"
            placeholderTextColor: "#8a8d96"
            selectByMouse: true
            background: Rectangle {
                radius: 10
                color: "#f3f4f6"
                border.width: search.activeFocus ? 1 : 0
                border.color: "#b88b94"
            }
            ToolButton {
                objectName: root.objectName + "ClearSearch"
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                width: 32; height: 32
                visible: search.text.length > 0
                text: "×"
                Accessible.name: "清空搜索"
                onClicked: search.clear()
            }
            Keys.onEscapePressed: clear()
        }
    }
    Flow {
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: root.sections
            delegate: Button {
                id: category
                required property var modelData
                objectName: root.objectName + "_" + modelData.id
                readonly property bool selected: !root.normalizedQuery && root.section === modelData.id
                text: modelData.title
                implicitWidth: categoryText.implicitWidth + 24
                implicitHeight: 36
                Accessible.name: modelData.title
                Accessible.role: Accessible.PageTab
                Accessible.selected: selected
                onClicked: root.choose(modelData.id)
                contentItem: Text {
                    id: categoryText
                    text: category.text
                    font.pixelSize: 13
                    color: category.selected ? "#945663" : "#777d87"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 9
                    color: category.selected ? "#f2e9ec" : category.hovered ? "#f2f3f5" : "transparent"
                    border.width: category.visualFocus ? 1 : 0
                    border.color: "#b88b94"
                }
            }
        }
    }
    Label {
        Layout.fillWidth: true
        visible: root.normalizedQuery.length > 0
        text: root.matchingIds.length ? "找到 " + root.matchingIds.length + " 组设置" : "没有找到，试试“拖动”“频率”或“模型”。"
        color: "#808590"
        font.pixelSize: 13
        wrapMode: Text.Wrap
    }
}
