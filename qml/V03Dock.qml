pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Item {
    id: root

    property var appBackend: null
    property bool suppressed: false
    property color paper: "#fffaf1"
    property color paperRaised: "#fffdf8"
    property color ink: "#4b4742"
    property color mutedInk: "#8a8279"
    property color hairline: "#d9ccbd"
    property color cord: "#91332f"
    property int maximumDockGroups: 7
    property var selectedGroup: ({})
    property bool previewOpen: false
    property bool drawerOpen: false
    property double returnFocusHandle: 0
    readonly property bool dockRaised: dockWindow.raised
    readonly property real dockWindowWidth: dockWindow.width
    readonly property real dockWindowHeight: dockWindow.height
    readonly property int visibleDockGroupCount: dockGroups.length
    readonly property int previewVisibleCardCount: previewWindow.cards.length
    readonly property int previewTotalCardCount: root.groupWindows(root.selectedGroup).length
    readonly property int drawerRowCount: drawerWindow.drawerRows.length
    readonly property int drawerFilteredGroupCount: drawerWindow.filteredGroups.length
    readonly property int drawerFilteredWindowCount: drawerWindow.filteredWindowCount
    readonly property int drawerFilteredLaunchCount: drawerWindow.filteredLaunchCount
    property int catalogRevision: 0

    visible: false
    width: 0
    height: 0

    function backendValue(name, fallbackValue) {
        if (!appBackend)
            return fallbackValue
        try {
            var value = appBackend[name]
            return value === undefined || value === null ? fallbackValue : value
        } catch (error) {
            return fallbackValue
        }
    }

    function invoke(name, args) {
        if (!appBackend)
            return false
        try {
            var callback = appBackend[name]
            if (typeof callback !== "function")
                return false
            var result = callback.apply(appBackend, args || [])
            return result !== false
                    && !(result !== null && typeof result === "object"
                         && result.ok !== undefined && result.ok === false)
        } catch (error) {
            return false
        }
    }

    function normalizedWindowGroups() {
        // Make the dynamic QObject lookups react to either catalogue signal.
        var revision = catalogRevision
        var readyGroups = backendValue("windowGroups", [])
        if (readyGroups && readyGroups.length !== undefined && readyGroups.length > 0) {
            var copied = []
            for (var readyIndex = 0; readyIndex < readyGroups.length; ++readyIndex)
                copied.push(copyWindowGroup(readyGroups[readyIndex]))
            return copied
        }

        // v0.2 compatibility: turn the old flat window list into one-window
        // groups until WindowCatalogService is connected to the backend.
        var flatWindows = backendValue("windowItems", [])
        if (!flatWindows || flatWindows.length === undefined)
            return []
        var groupsByKey = ({})
        var order = []
        for (var index = 0; index < flatWindows.length; ++index) {
            var item = flatWindows[index]
            var key = String(item.appId || item.processName || item.handle || index)
            if (groupsByKey[key] === undefined) {
                groupsByKey[key] = {
                    appId: key,
                    displayName: item.displayName || item.processName || item.title || "应用",
                    iconKey: item.iconKey || "",
                    active: Boolean(item.active),
                    minimized: Boolean(item.minimized),
                    windowCount: 0,
                    windows: []
                }
                order.push(key)
            }
            groupsByKey[key].windows.push(item)
            groupsByKey[key].windowCount += 1
            groupsByKey[key].active = groupsByKey[key].active || Boolean(item.active)
        }
        var result = []
        for (var orderIndex = 0; orderIndex < order.length; ++orderIndex)
            result.push(groupsByKey[order[orderIndex]])
        var copiedResult = []
        for (var resultIndex = 0; resultIndex < result.length; ++resultIndex)
            copiedResult.push(copyWindowGroup(result[resultIndex]))
        return copiedResult
    }

    function copyWindowGroup(group) {
        return {
            handle: Number(group.handle || 0),
            appId: String(group.appId || group.displayName || group.handle || ""),
            displayName: String(group.displayName || group.title || "应用"),
            iconKey: String(group.iconKey || ""),
            iconUrl: String(group.iconUrl || ""),
            active: Boolean(group.active),
            minimized: Boolean(group.minimized),
            windowCount: Number(group.windowCount
                                || (group.windows ? group.windows.length : 0)),
            windows: group.windows || [],
            pinned: Boolean(group.pinned),
            itemId: String(group.itemId || ""),
            launchKind: String(group.launchKind || ""),
            launchPath: String(group.launchPath || ""),
            glyph: String(group.glyph || "")
        }
    }

    function normalizedIdentity(value) {
        return String(value || "").toLocaleLowerCase()
                .replace(/\.(?:lnk|url|exe|appref-ms)$/i, "")
                .replace(/[^0-9a-z\u4e00-\u9fff]+/g, "")
    }

    function pathStem(pathValue) {
        var parts = String(pathValue || "").split(/[\\/]/)
        return parts.length > 0 ? parts[parts.length - 1]
                                  .replace(/\.(?:lnk|url|exe|appref-ms)$/i, "") : ""
    }

    function identityKeys(value, windowGroup) {
        var candidates = windowGroup
                ? [value.displayName, value.appId,
                   value.windows && value.windows.length > 0
                       ? value.windows[0].processName : "",
                   value.windows && value.windows.length > 0
                       ? pathStem(value.windows[0].executablePath) : ""]
                : [value.name, pathStem(value.path)]
        var keys = []
        for (var index = 0; index < candidates.length; ++index) {
            var key = normalizedIdentity(candidates[index])
            if (key.length >= 3 && keys.indexOf(key) < 0)
                keys.push(key)
        }
        return keys
    }

    function sameApplication(item, group) {
        if (String(item.kind || "") !== "application")
            return false
        var itemKeys = identityKeys(item, false)
        var groupKeys = identityKeys(group, true)
        for (var itemIndex = 0; itemIndex < itemKeys.length; ++itemIndex) {
            for (var groupIndex = 0; groupIndex < groupKeys.length; ++groupIndex) {
                var left = itemKeys[itemIndex]
                var right = groupKeys[groupIndex]
                if (left === right)
                    return true
                // A three-character substring merged unrelated applications
                // such as Word/WordPad and Code/Codec.  Keep a conservative
                // alias path only for substantial, similarly sized names;
                // the common WPS and Visual Studio Code cases use exact
                // normalized display names.
                var shorter = Math.min(left.length, right.length)
                var longer = Math.max(left.length, right.length)
                if (shorter >= 6 && shorter / longer >= 0.60
                        && (left.indexOf(right) >= 0 || right.indexOf(left) >= 0))
                    return true
            }
        }
        return false
    }

    function normalizedLaunchItems() {
        var revision = catalogRevision
        var source = backendValue("dockLaunchItems", undefined)
        if (source === undefined || source === null || source.length === undefined) {
            source = []
            var pinned = backendValue("pinnedItems", []) || []
            var desktop = backendValue("desktopItems", []) || []
            for (var pinnedIndex = 0; pinnedIndex < pinned.length; ++pinnedIndex)
                source.push(pinned[pinnedIndex])
            for (var desktopIndex = 0; desktopIndex < desktop.length; ++desktopIndex)
                source.push(desktop[desktopIndex])
        }
        var result = []
        var seen = []
        for (var index = 0; index < source.length; ++index) {
            var item = source[index] || ({})
            var key = String(item.itemId || item.path || "")
            if (key === "" || seen.indexOf(key) >= 0)
                continue
            seen.push(key)
            result.push(item)
        }
        return result
    }

    function mergedGroups(pinnedOnly) {
        var result = normalizedWindowGroups()
        for (var itemIndex = 0; itemIndex < launchItems.length; ++itemIndex) {
            var item = launchItems[itemIndex]
            if (pinnedOnly && !Boolean(item.pinned))
                continue
            var match = -1
            for (var groupIndex = 0; groupIndex < result.length; ++groupIndex) {
                if (sameApplication(item, result[groupIndex])) {
                    match = groupIndex
                    break
                }
            }
            if (match >= 0) {
                result[match].itemId = String(item.itemId || "")
                result[match].launchKind = String(item.kind || "application")
                result[match].launchPath = String(item.path || "")
                result[match].pinned = result[match].pinned || Boolean(item.pinned)
                result[match].glyph = String(item.glyph || result[match].glyph || "")
                continue
            }
            result.push({
                handle: 0,
                appId: "launch:" + String(item.itemId || item.path || itemIndex),
                displayName: String(item.name || "项目"),
                iconKey: "",
                iconUrl: String(item.iconUrl || ""),
                active: false,
                minimized: false,
                windowCount: 0,
                windows: [],
                pinned: Boolean(item.pinned),
                itemId: String(item.itemId || ""),
                launchKind: String(item.kind || "file"),
                launchPath: String(item.path || ""),
                glyph: String(item.glyph || "")
            })
        }
        if (pinnedOnly) {
            var fixed = []
            var running = []
            for (var sortIndex = 0; sortIndex < result.length; ++sortIndex) {
                if (Boolean(result[sortIndex].pinned))
                    fixed.push(result[sortIndex])
                else
                    running.push(result[sortIndex])
            }
            return fixed.concat(running)
        }
        return result
    }

    property var launchItems: normalizedLaunchItems()
    property var groups: mergedGroups(true)
    property var drawerGroups: mergedGroups(false)
    property var dockGroups: groups.slice(0, maximumDockGroups)
    property int overflowGroupCount: Math.max(0, groups.length - maximumDockGroups)

    Connections {
        target: root.appBackend
        ignoreUnknownSignals: true
        function onWindowGroupsChanged() { root.catalogRevision += 1 }
        function onWindowItemsChanged() { root.catalogRevision += 1 }
        function onDesktopItemsChanged() { root.catalogRevision += 1 }
    }

    function groupWindows(group) {
        if (!group || !group.windows || group.windows.length === undefined)
            return []
        return group.windows
    }

    function activate(handle) {
        var numericHandle = Number(handle || 0)
        if (numericHandle <= 0)
            return false
        // Keep the current cards visible when the target went stale.  The
        // backend returns false and publishes a short status message, so the
        // user can choose another window instead of watching the drawer close
        // as if activation had succeeded.
        if (!invoke("activateWindow", [numericHandle]))
            return false
        previewOpen = false
        drawerOpen = false
        returnFocusHandle = 0
        return true
    }

    function launch(itemId) {
        var identifier = String(itemId || "")
        if (identifier === "")
            return false
        var launched = invoke("openItem", [identifier])
        if (!launched)
            return false
        previewOpen = false
        drawerOpen = false
        returnFocusHandle = 0
        return true
    }

    function currentActiveHandle() {
        for (var groupIndex = 0; groupIndex < groups.length; ++groupIndex) {
            var windows = groupWindows(groups[groupIndex])
            for (var windowIndex = 0; windowIndex < windows.length; ++windowIndex) {
                if (Boolean(windows[windowIndex].active))
                    return Number(windows[windowIndex].handle || 0)
            }
        }
        return 0
    }

    function openDrawer() {
        returnFocusHandle = currentActiveHandle()
        previewOpen = false
        drawerOpen = true
    }

    function closeDrawerAndRestore() {
        var handle = returnFocusHandle
        drawerOpen = false
        returnFocusHandle = 0
        if (handle > 0)
            Qt.callLater(function() { root.invoke("activateWindow", [handle]) })
    }

    function openGroup(group) {
        var windows = groupWindows(group)
        if (windows.length === 1) {
            activate(windows[0].handle)
            return
        }
        if (windows.length > 1) {
            selectedGroup = group
            previewOpen = true
            drawerOpen = false
            return
        }
        if (windows.length === 0)
            launch(group.itemId)
    }

    function iconText(group) {
        var label = String((group && (group.displayName || group.title)) || "应用").trim()
        return label.length > 0 ? label.charAt(0).toLocaleUpperCase() : "·"
    }

    component AppGlyph: Rectangle {
        id: appGlyph
        required property var groupData
        property bool compact: false
        width: compact ? 28 : 34
        height: width
        radius: compact ? 9 : 11
        color: groupData.active ? "#f3e7dc" : "#f7f3ec"
        border.color: groupData.active ? root.cord : root.hairline
        border.width: groupData.active ? 1.4 : 1

        Image {
            id: iconImage
            anchors.centerIn: parent
            width: parent.width - 8
            height: width
            source: String(appGlyph.groupData.iconUrl || "")
            visible: source.toString() !== ""
            fillMode: Image.PreserveAspectFit
            mipmap: true
        }

        Text {
            anchors.centerIn: parent
            visible: !iconImage.visible
            text: String(appGlyph.groupData.glyph || root.iconText(appGlyph.groupData))
            color: root.ink
            font.pixelSize: appGlyph.compact ? 12 : 14
            font.weight: Font.DemiBold
        }

        Rectangle {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            width: 8
            height: 8
            radius: 4
            color: appGlyph.groupData.active ? "#6d9183" : "transparent"
            border.color: root.paperRaised
            border.width: appGlyph.groupData.active ? 1 : 0
        }
    }

    component DockGroupButton: Rectangle {
        id: dockGroupButton
        objectName: "v03DockGroupButton_" + String(groupData.appId || "unknown")
        required property var groupData
        signal activated()
        implicitWidth: 48
        implicitHeight: 42
        radius: 13
        color: groupHover.hovered ? "#eee7dc" : "transparent"

        AppGlyph {
            anchors.centerIn: parent
            groupData: dockGroupButton.groupData
        }

        Rectangle {
            visible: Number(dockGroupButton.groupData.windowCount || root.groupWindows(dockGroupButton.groupData).length) > 1
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: 2
            anchors.topMargin: 1
            width: 17
            height: 17
            radius: 8.5
            color: root.cord
            Text {
                anchors.centerIn: parent
                text: String(dockGroupButton.groupData.windowCount || root.groupWindows(dockGroupButton.groupData).length)
                color: "white"
                font.pixelSize: 9
                font.weight: Font.DemiBold
            }
        }

        HoverHandler {
            id: groupHover
            cursorShape: Qt.PointingHandCursor
        }
        TapHandler { onTapped: dockGroupButton.activated() }
        ToolTip.visible: groupHover.hovered
        ToolTip.delay: 450
        ToolTip.text: String(groupData.displayName || groupData.title || "应用")
    }

    Window {
        id: dockWindow
        objectName: "v03PaperFoldDock"
        transientParent: null
        property bool raised: false
        readonly property real collapsedPaperHeight: 6
        readonly property real collapsedHitHeight: 16
        width: raised ? Math.min(Screen.width - 32, 640) : 64
        // Keep a forgiving native hit strip while rendering only the quiet
        // six-pixel paper seam.  The old 6 px window floated three pixels over
        // the edge and was unnecessarily difficult to acquire with a mouse.
        height: raised ? 52 : collapsedHitHeight
        x: Screen.virtualX + (Screen.width - width) / 2
        // QWindow coordinates are integral on fractional DPI screens.  Ceil
        // the lower edge calculation so rounding cannot leave a one-pixel gap.
        y: Math.ceil(Screen.virtualY + Screen.height - height)
        visible: root.appBackend !== null
                 && !root.suppressed
                 && String(root.backendValue("shellMode", "visual")) !== "compact"
        color: "transparent"
        flags: Qt.FramelessWindowHint
               | Qt.WindowStaysOnTopHint
               | Qt.Tool
               | Qt.WindowDoesNotAcceptFocus

        onVisibleChanged: {
            if (!visible) {
                raised = false
                root.previewOpen = false
                root.drawerOpen = false
            }
        }

        Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        // y must follow the animated height immediately.  A second animation
        // here lagged behind it and left the collapsed native hit window one
        // pixel above the task edge on fractional-DPI screens.

        Rectangle {
            id: dockPaperSurface
            objectName: "v03DockPaperSurface"
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: dockWindow.raised ? parent.height : dockWindow.collapsedPaperHeight
            radius: dockWindow.raised ? 12 : 3
            color: root.paperRaised
            opacity: dockWindow.raised ? 0.98 : 0.90
            border.color: root.hairline
            border.width: 1

            // A tiny paper-grain suggestion without a bitmap or a heavy shader.
            Repeater {
                model: dockWindow.raised ? 12 : 0
                delegate: Rectangle {
                    required property int index
                    x: 18 + (index * 47) % Math.max(20, parent.width - 36)
                    y: 7 + (index * 13) % Math.max(8, parent.height - 14)
                    width: 14 + (index % 3) * 5
                    height: 1
                    color: index % 2 === 0 ? "#e9dfd2" : "#f6eee4"
                    opacity: 0.45
                    rotation: (index % 5) - 2
                }
            }

            Rectangle {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: dockWindow.raised ? 5 : 1
                width: dockWindow.raised ? 34 : 20
                height: 2
                radius: 1
                color: root.cord
                opacity: 0.75
            }

            Rectangle {
                visible: !dockWindow.raised
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                width: 5
                height: 5
                radius: 2.5
                color: root.cord
            }

            RowLayout {
                visible: dockWindow.raised
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                anchors.topMargin: 7
                anchors.bottomMargin: 3
                spacing: 3

                Repeater {
                    model: root.dockGroups
                    delegate: DockGroupButton {
                        required property var modelData
                        groupData: modelData
                        onActivated: root.openGroup(modelData)
                    }
                }

                Item { Layout.fillWidth: true; Layout.minimumWidth: 4 }

                RoundButton {
                    id: overflowButton
                    objectName: "v03DockOverflowButton"
                    visible: root.overflowGroupCount > 0
                    implicitWidth: 39
                    implicitHeight: 36
                    flat: true
                    text: "+" + String(root.overflowGroupCount)
                    font.pixelSize: 12
                    onClicked: {
                        root.openDrawer()
                    }
                    ToolTip.visible: hovered
                    ToolTip.text: "按应用组查看全部窗口"
                }

                RoundButton {
                    id: searchButton
                    objectName: "v03DockSearchButton"
                    implicitWidth: 39
                    implicitHeight: 36
                    flat: true
                    text: ""
                    contentItem: Item {
                        implicitWidth: 18
                        implicitHeight: 18
                        Rectangle {
                            x: 2
                            y: 2
                            width: 10
                            height: 10
                            radius: 5
                            color: "transparent"
                            border.color: root.ink
                            border.width: 1.5
                        }
                        Rectangle {
                            x: 11
                            y: 12
                            width: 7
                            height: 1.5
                            radius: 0.75
                            rotation: 45
                            color: root.ink
                        }
                    }
                    onClicked: {
                        root.openDrawer()
                    }
                    ToolTip.visible: hovered
                    ToolTip.text: "搜索全部窗口"
                }

                Text {
                    id: clockLabel
                    Layout.leftMargin: 3
                    text: Qt.formatDateTime(now, "hh:mm")
                    property date now: new Date()
                    color: root.ink
                    font.pixelSize: 12
                    font.weight: Font.DemiBold
                    Timer {
                        interval: 1000
                        repeat: true
                        running: dockWindow.visible
                        onTriggered: clockLabel.now = new Date()
                    }
                }
            }
        }

        HoverHandler {
            id: dockHover
            onHoveredChanged: {
                if (hovered) {
                    collapseTimer.stop()
                    dockWindow.raised = true
                } else {
                    collapseTimer.restart()
                }
            }
        }

        Timer {
            id: collapseTimer
            interval: 700
            repeat: false
            onTriggered: {
                if (!root.previewOpen && !root.drawerOpen)
                    dockWindow.raised = false
            }
        }

        Timer {
            interval: 10000
            repeat: true
            running: dockWindow.visible
            onTriggered: root.invoke("refreshWindows", [])
        }
    }

    Window {
        id: previewWindow
        objectName: "v03WindowPreviewShelf"
        transientParent: null
        property var cards: root.groupWindows(root.selectedGroup).slice(0, 6)
        width: Math.min(460, Screen.width - 32)
        height: Math.min(356, 82 + cards.length * 44)
        x: Math.max(Screen.virtualX + 16,
                    Math.min(dockWindow.x + (dockWindow.width - width) / 2,
                             Screen.virtualX + Screen.width - width - 16))
        y: Math.max(Screen.virtualY + 16, dockWindow.y - height - 8)
        visible: dockWindow.visible && root.previewOpen && cards.length > 1
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
               | Qt.WindowDoesNotAcceptFocus

        onVisibleChanged: if (visible) raise()

        Rectangle {
            anchors.fill: parent
            radius: 16
            color: root.paperRaised
            border.color: root.hairline

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 7

                RowLayout {
                    Layout.fillWidth: true
                    AppGlyph { compact: true; groupData: root.selectedGroup }
                    Text {
                        objectName: "v03PreviewTitle"
                        Layout.fillWidth: true
                        text: String(root.selectedGroup.displayName || "窗口")
                        color: root.ink
                        elide: Text.ElideRight
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "v03PreviewCount"
                        text: String(previewWindow.cards.length) + " / "
                              + String(root.groupWindows(root.selectedGroup).length)
                        color: root.mutedInk
                        font.pixelSize: 11
                    }
                    RoundButton {
                        flat: true
                        text: "×"
                        implicitWidth: 30
                        implicitHeight: 30
                        onClicked: root.previewOpen = false
                    }
                }

                Repeater {
                    model: previewWindow.cards
                    delegate: Rectangle {
                        id: previewCard
                        objectName: "v03PreviewCard_" + String(modelData.handle || "unknown")
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.preferredHeight: 38
                        radius: 10
                        color: previewHover.hovered ? "#f0e8dc" : "#faf6ef"
                        border.color: modelData.active ? root.cord : root.hairline
                        border.width: modelData.active ? 1.2 : 1

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 10
                            anchors.rightMargin: 10
                            spacing: 8
                            Rectangle {
                                Layout.preferredWidth: 6
                                Layout.preferredHeight: 6
                                radius: 3
                                color: previewCard.modelData.active ? "#678b7d"
                                                                    : (previewCard.modelData.minimized ? "#b5a99b" : "transparent")
                            }
                            Text {
                                Layout.fillWidth: true
                                text: String(previewCard.modelData.title || "未命名窗口")
                                color: root.ink
                                elide: Text.ElideMiddle
                                font.pixelSize: 12
                            }
                            Text {
                                text: previewCard.modelData.minimized
                                      ? "已最小化" : String(previewCard.modelData.monitorId || "")
                                color: root.mutedInk
                                font.pixelSize: 10
                            }
                        }

                        HoverHandler { id: previewHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: root.activate(previewCard.modelData.handle) }
                    }
                }
            }
        }
    }

    Window {
        id: drawerWindow
        objectName: "v03AllWindowsDrawer"
        transientParent: null
        property string filterText: windowSearch.text.trim().toLocaleLowerCase()
        function matchingWindows(group) {
            var windows = root.groupWindows(group)
            if (filterText === ""
                    || String(group.displayName || "").toLocaleLowerCase().indexOf(filterText) >= 0)
                return windows
            return windows.filter(function(windowData) {
                return String(windowData.title || "").toLocaleLowerCase().indexOf(filterText) >= 0
            })
        }
        property var filteredGroups: {
            if (filterText === "")
                return root.drawerGroups
            return root.drawerGroups.filter(function(group) {
                if (String(group.displayName || "").toLocaleLowerCase().indexOf(filterText) >= 0)
                    return true
                if (String(group.launchPath || "").toLocaleLowerCase().indexOf(filterText) >= 0)
                    return true
                return drawerWindow.matchingWindows(group).length > 0
            })
        }
        // The drawer is deliberately flattened into one ListView.  A grouped
        // delegate containing a Repeater instantiated every window in a large
        // application at once; this row model keeps 50+ window catalogues
        // virtualized while preserving an inline group header (no nested
        // popup or second-level flyout).
        property var drawerRows: {
            var rows = []
            for (var groupIndex = 0; groupIndex < filteredGroups.length; ++groupIndex) {
                var group = filteredGroups[groupIndex]
                var windows = matchingWindows(group)
                var appId = String(group.appId || group.displayName || groupIndex)
                rows.push({
                    rowKind: "group",
                    key: appId,
                    groupData: group,
                    windowData: ({}),
                    visibleWindowCount: windows.length,
                    totalWindowCount: root.groupWindows(group).length
                })
                for (var windowIndex = 0; windowIndex < windows.length; ++windowIndex) {
                    var windowData = windows[windowIndex]
                    rows.push({
                        rowKind: "window",
                        key: appId + "_" + String(windowData.handle || windowIndex),
                        groupData: group,
                        windowData: windowData,
                        visibleWindowCount: windows.length,
                        totalWindowCount: root.groupWindows(group).length
                    })
                }
            }
            return rows
        }
        property int filteredWindowCount: {
            var count = 0
            for (var index = 0; index < filteredGroups.length; ++index)
                count += matchingWindows(filteredGroups[index]).length
            return count
        }
        property int filteredLaunchCount: {
            var count = 0
            for (var index = 0; index < filteredGroups.length; ++index) {
                if (root.groupWindows(filteredGroups[index]).length === 0
                        && String(filteredGroups[index].itemId || "") !== "")
                    count += 1
            }
            return count
        }
        property real drawerContentHeight: {
            var value = Math.max(0, drawerRows.length - 1) * 3
            for (var index = 0; index < drawerRows.length; ++index)
                value += drawerRows[index].rowKind === "group" ? 44 : 40
            return value
        }
        width: Math.min(600, Screen.width - 32)
        height: Math.min(Screen.height - 80, Math.max(300,
            122 + drawerContentHeight))
        x: Math.max(Screen.virtualX + 16,
                    Math.min(dockWindow.x + (dockWindow.width - width) / 2,
                             Screen.virtualX + Screen.width - width - 16))
        y: Math.max(Screen.virtualY + 16, dockWindow.y - height - 8)
        visible: dockWindow.visible && root.drawerOpen
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

        Behavior on height {
            NumberAnimation { duration: 150; easing.type: Easing.OutCubic }
        }

        onVisibleChanged: {
            if (visible) {
                raise()
                requestActivate()
                Qt.callLater(function() { windowSearch.forceActiveFocus() })
            } else {
                windowSearch.text = ""
            }
        }

        Rectangle {
            anchors.fill: parent
            radius: 18
            color: root.paperRaised
            border.color: root.hairline

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 10

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        objectName: "v03DrawerTitle"
                        Layout.fillWidth: true
                        text: drawerWindow.filteredLaunchCount === 0
                              ? (drawerWindow.filterText === ""
                                 ? "全部窗口 · " + String(root.drawerGroups.length) + " 个应用组"
                                 : "搜索结果 · " + String(drawerWindow.filteredGroups.length) + " 个应用组")
                              : ((drawerWindow.filterText === "" ? "全部" : "搜索结果")
                                 + " · " + String(drawerWindow.filteredWindowCount) + " 个窗口 · "
                                 + String(drawerWindow.filteredLaunchCount) + " 个可启动项目")
                        color: root.ink
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                    }
                    RoundButton {
                        flat: true
                        text: "×"
                        implicitWidth: 32
                        implicitHeight: 32
                        onClicked: root.closeDrawerAndRestore()
                    }
                }

                TextField {
                    id: windowSearch
                    objectName: "v03WindowSearch"
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    placeholderText: "搜索应用、文件或窗口标题"
                    selectByMouse: true
                    color: root.ink
                    leftPadding: 13
                    rightPadding: 13
                    background: Rectangle {
                        radius: 12
                        color: "#fffdf9"
                        border.color: windowSearch.activeFocus ? root.cord : root.hairline
                    }
                    Keys.onEscapePressed: root.closeDrawerAndRestore()
                }

                ListView {
                    id: groupedWindowList
                    objectName: "v03GroupedWindowList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: drawerWindow.drawerRows
                    spacing: 3
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    reuseItems: true
                    cacheBuffer: 80
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                    delegate: Rectangle {
                        id: drawerRow
                        required property var modelData
                        objectName: "v03DrawerRow_" + String(modelData.rowKind)
                                    + "_" + String(modelData.key)
                        x: modelData.rowKind === "group" ? 0 : 10
                        width: Math.max(0, (ListView.view ? ListView.view.width : 0) - x)
                        height: modelData.rowKind === "group" ? 44 : 40
                        radius: modelData.rowKind === "group" ? 12 : 9
                        color: modelData.rowKind === "group"
                               ? "#faf6ef"
                               : (drawerRowHover.hovered ? "#eee5d9" : "transparent")
                        border.color: modelData.rowKind === "group"
                                      ? (modelData.groupData.active ? "#c7a8a1" : root.hairline)
                                      : (modelData.windowData.active ? "#d5b9b1" : "transparent")
                        border.width: modelData.rowKind === "group" || modelData.windowData.active ? 1 : 0

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: drawerRow.modelData.rowKind === "group" ? 7 : 10
                            anchors.rightMargin: 9
                            spacing: drawerRow.modelData.rowKind === "group" ? 9 : 7

                            AppGlyph {
                                visible: drawerRow.modelData.rowKind === "group"
                                compact: true
                                groupData: drawerRow.modelData.groupData
                            }
                            Rectangle {
                                visible: drawerRow.modelData.rowKind === "window"
                                Layout.preferredWidth: 5
                                Layout.preferredHeight: 5
                                radius: 2.5
                                color: drawerRow.modelData.windowData.active ? "#698879"
                                       : (drawerRow.modelData.windowData.minimized ? "#b7aa9b" : "transparent")
                            }
                            Text {
                                Layout.fillWidth: true
                                text: drawerRow.modelData.rowKind === "group"
                                      ? (String(drawerRow.modelData.groupData.displayName || "应用")
                                         + (drawerRow.modelData.totalWindowCount > 0
                                            ? (" · " + String(drawerRow.modelData.visibleWindowCount)) : ""))
                                      : String(drawerRow.modelData.windowData.title || "未命名窗口")
                                color: root.ink
                                elide: drawerRow.modelData.rowKind === "group"
                                       ? Text.ElideRight : Text.ElideMiddle
                                font.pixelSize: drawerRow.modelData.rowKind === "group" ? 13 : 12
                                font.weight: drawerRow.modelData.rowKind === "group"
                                             ? Font.DemiBold : Font.Normal
                            }
                            Text {
                                visible: drawerRow.modelData.rowKind === "group"
                                         ? (Boolean(drawerRow.modelData.groupData.active)
                                            || drawerRow.modelData.totalWindowCount === 0)
                                         : Boolean(drawerRow.modelData.windowData.minimized)
                                           || String(drawerRow.modelData.windowData.monitorId || "") !== ""
                                text: drawerRow.modelData.rowKind === "group"
                                      ? (drawerRow.modelData.totalWindowCount > 0
                                         ? "正在使用"
                                         : (Boolean(drawerRow.modelData.groupData.pinned)
                                            ? "已固定"
                                            : (String(drawerRow.modelData.groupData.launchKind || "") === "folder"
                                               ? "文件夹"
                                               : (String(drawerRow.modelData.groupData.launchKind || "") === "file"
                                                  ? "文件" : "可启动"))))
                                      : (drawerRow.modelData.windowData.minimized
                                         ? "已最小化"
                                         : String(drawerRow.modelData.windowData.monitorId || ""))
                                color: drawerRow.modelData.rowKind === "group" ? "#698879" : root.mutedInk
                                font.pixelSize: 10
                            }
                        }

                        HoverHandler {
                            id: drawerRowHover
                            enabled: drawerRow.modelData.rowKind === "window"
                                     || (drawerRow.modelData.rowKind === "group"
                                         && drawerRow.modelData.totalWindowCount === 0
                                         && String(drawerRow.modelData.groupData.itemId || "") !== "")
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        }
                        TapHandler {
                            enabled: drawerRowHover.enabled
                            onTapped: {
                                if (drawerRow.modelData.rowKind === "window")
                                    root.activate(drawerRow.modelData.windowData.handle)
                                else
                                    root.launch(drawerRow.modelData.groupData.itemId)
                            }
                        }
                    }

                    Text {
                        objectName: "v03DrawerEmptyLabel"
                        anchors.centerIn: parent
                        visible: groupedWindowList.count === 0
                        text: drawerWindow.filterText === ""
                              ? "目前没有可切换的窗口或可启动项目"
                              : "没有匹配的应用、文件或窗口"
                        color: root.mutedInk
                        font.pixelSize: 13
                    }
                }
            }
        }
    }
}
