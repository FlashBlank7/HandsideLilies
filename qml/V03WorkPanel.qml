pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root
    objectName: "v03WorkPanel"
    transientParent: null

    property var appBackend: null
    signal connectorRequested(string providerName)
    property color paper: "#fffaf2"
    property color paperRaised: "#fffdf9"
    property color ink: "#4a4641"
    property color mutedInk: "#887f76"
    property color hairline: "#dacdbd"
    property color cord: "#91332f"
    property color calm: "#668779"
    property string lastNotice: ""
    property string activeSection: "work"
    property string requestedSection: "work"
    property string pendingSection: "work"
    property string pendingAnchor: ""
    property string lastRevealedAnchor: ""
    property string highlightedAnchor: ""
    property int anchorPresentationCount: 0
    property bool selectingSectionProgrammatically: false
    // The compact pet deliberately cannot activate the application window.
    // Its full-size panels therefore need an explicit presentation layer when
    // opened from that no-focus tool window.  Main.qml enables this only in
    // compact mode; visual desktop mode keeps ordinary application Z-order.
    property bool stayOnTopWhenPresented: false
    property int presentationAttempts: 0
    property var presentationArea: ({})
    readonly property bool narrowViewport: width < 620
    readonly property string sectionHeading: activeSection === "world" ? "盒中世界"
        : activeSection === "growth" ? "共鸣与衣橱"
        : activeSection === "connectors" ? "日历与 Slack 信笺"
        : "任务与专注"
    readonly property string sectionCaption: activeSection === "world"
        ? "现实里的完成，会在这只盒子里留下确定的陈设与共同痕迹。"
        : activeSection === "growth"
          ? "共鸣只记录真实完成；服装、姿态和盒中陈设都按明确里程碑解锁。"
          : activeSection === "connectors"
            ? "外部事项只在你主动连接后出现；任何写入都必须先看预览再确认。"
            : "现实事项完成后，成长会被准确记录；失败、延期和休息都不会扣分。"
    readonly property real presentationAreaWidth: {
        var areaWidth = Number(presentationArea.width)
        return isFinite(areaWidth) && areaWidth > 0 ? areaWidth : Screen.width
    }
    readonly property real presentationAreaHeight: {
        var areaHeight = Number(presentationArea.height)
        return isFinite(areaHeight) && areaHeight > 0 ? areaHeight : Screen.height
    }

    // Keep the native frame reachable even when Windows exposes a very small
    // logical work area (large DPI scale, portrait monitor, split-screen or a
    // remote session).  The page content keeps its comfortable 640px paper
    // width and becomes horizontally scrollable below that threshold instead
    // of forcing the whole native window beyond the monitor edges.
    minimumWidth: Math.max(320, Math.min(720, presentationAreaWidth - 24))
    minimumHeight: Math.max(320, Math.min(540, presentationAreaHeight - 24))
    width: Math.max(minimumWidth, Math.min(920, presentationAreaWidth - 32))
    height: Math.max(minimumHeight, Math.min(720, presentationAreaHeight - 40))
    visible: false
    color: paper
    title: "莉莉丝 · " + sectionHeading
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
           | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
           | (stayOnTopWhenPresented ? Qt.WindowStaysOnTopHint : 0)

    function value(name, fallbackValue) {
        if (!appBackend)
            return fallbackValue
        try {
            var result = appBackend[name]
            return result === undefined || result === null ? fallbackValue : result
        } catch (error) {
            return fallbackValue
        }
    }

    function invoke(name, args, unavailableText) {
        var fallback = unavailableText || "操作没有完成，请稍后重试"
        if (!appBackend) {
            lastNotice = unavailableText || "服务尚未启动"
            return false
        }
        try {
            var callback = appBackend[name]
            if (typeof callback !== "function") {
                lastNotice = unavailableText || "此功能正在接入"
                return false
            }
            var result = callback.apply(appBackend, args || [])
            if (result === false
                    || (result !== null && typeof result === "object"
                        && result.ok !== undefined && result.ok === false)) {
                lastNotice = result && (result.error || result.message)
                             ? String(result.error || result.message).slice(0, 320)
                             : fallback
                return false
            }
            lastNotice = ""
            return true
        } catch (error) {
            lastNotice = "操作没有完成，请稍后重试"
            return false
        }
    }

    function openConnector(name) {
        connectorRequested(name === "slack" ? "slack" : "calendar")
    }

    function itemId(item) {
        if (!item)
            return ""
        return String(item.id || item.eventId || item.event_id || "")
    }

    function selectConnectorItem(providerName, eventId) {
        var normalized = providerName === "slack" ? "slack" : "calendar"
        var selectedId = String(eventId || "")
        if (selectedId === "") {
            lastNotice = "这个条目没有可用的标识，无法准备写入提案"
            return
        }
        if (!invoke("connectorSelectItem", [normalized, selectedId],
                    "连接器条目选择尚未接入"))
            return
        connectorRequested(normalized)
    }

    function sectionForTabIndex(index) {
        if (index === 1)
            return "growth"
        if (index === 2)
            return "connectors"
        if (index === 3)
            return "world"
        return "work"
    }

    function syncSectionFromTab(index) {
        var section = sectionForTabIndex(index)
        var shouldEnterWorld = section === "world" && activeSection !== "world"
        activeSection = section
        if (shouldEnterWorld && !selectingSectionProgrammatically)
            invoke("enterBoxWorld", [], "盒中世界尚未接入")
    }

    function selectSection(name) {
        var requested = String(name || "work")
        var index = 0
        var section = "work"
        if (requested === "growth" || requested === "wardrobe") {
            index = 1
            section = "growth"
        } else if (requested === "connectors" || requested === "calendar" || requested === "slack") {
            index = 2
            section = "connectors"
        } else if (requested === "world" || requested === "box-world") {
            index = 3
            section = "world"
        }
        activeSection = section
        selectingSectionProgrammatically = true
        sectionTabs.currentIndex = index
        selectingSectionProgrammatically = false
    }

    function showSection(name) {
        selectSection(name)
        if (!visible)
            return
        presentationAttempts += 1
        // `visible` stays true while a native QWindow is minimized.  Merely
        // raising that window leaves the world page hidden in the taskbar, so
        // every explicit navigation request must restore it first.
        if (visibility === Window.Minimized)
            showNormal()
        ensurePresentationReachable()
        raise()
        requestActivate()
    }

    function ensurePresentationReachable() {
        var left = Number(presentationArea.left)
        var top = Number(presentationArea.top)
        var right = Number(presentationArea.right)
        var bottom = Number(presentationArea.bottom)
        if (!isFinite(left) || !isFinite(top) || !isFinite(right)
                || !isFinite(bottom) || right <= left || bottom <= top) {
            left = Screen.virtualX
            top = Screen.virtualY
            right = left + Screen.width
            bottom = top + Screen.height
        }
        var visibleWidth = Math.max(0, Math.min(x + width, right) - Math.max(x, left))
        var visibleHeight = Math.max(0, Math.min(y + height, bottom) - Math.max(y, top))
        var requiredWidth = Math.min(112, width * 0.30)
        var requiredHeight = Math.min(72, height * 0.22)
        if (visibleWidth >= requiredWidth && visibleHeight >= requiredHeight)
            return
        // A panel requested from a pet on another monitor should appear on
        // that monitor, not silently raise on the old/primary display.
        x = left + Math.max(12, (right - left - width) / 2)
        y = top + Math.max(12, (bottom - top - height) / 2)
    }

    function presentSection(name) {
        pendingSection = String(name || "work")
        selectSection(pendingSection)
        if (visible)
            presentationTimer.restart()
    }

    function anchorDescriptor(name) {
        var normalized = String(name || "").trim().toLocaleLowerCase()
        if (normalized === "focus")
            return { section: "work", scroll: workScroll,
                     content: workContent, target: focusCard }
        if (normalized === "reading")
            return { section: "work", scroll: workScroll,
                     content: workContent, target: readingCard }
        if (normalized === "wardrobe")
            return { section: "growth", scroll: resonanceScroll,
                     content: resonanceContent, target: wardrobeCard }
        if (normalized === "slack-inbox")
            return { section: "connectors", scroll: connectorScroll,
                     content: connectorContent, target: slackInboxAnchor }
        return null
    }

    function revealAnchor(name) {
        var normalized = String(name || "").trim().toLocaleLowerCase()
        var descriptor = anchorDescriptor(normalized)
        if (!descriptor)
            return false
        if (normalized === "slack-inbox" && !Boolean(slackInfo.connected))
            return false
        pendingAnchor = normalized
        selectSection(descriptor.section)
        if (visible)
            anchorPresentationTimer.restart()
        return true
    }

    function applyPendingAnchor() {
        if (!visible || pendingAnchor === "")
            return false
        var normalized = pendingAnchor
        var descriptor = anchorDescriptor(normalized)
        if (!descriptor) {
            pendingAnchor = ""
            return false
        }
        if (normalized === "slack-inbox" && !Boolean(slackInfo.connected)) {
            pendingAnchor = ""
            return false
        }
        var flickable = descriptor.scroll.contentItem
        if (!flickable)
            return false
        var mapped = descriptor.target.mapToItem(descriptor.content, 0, 0)
        var minimumY = Number(flickable.originY || 0)
        var maximumY = Math.max(minimumY,
                                Number(flickable.contentHeight || 0)
                                - Number(flickable.height || 0))
        flickable.contentY = Math.max(
            minimumY, Math.min(maximumY, Number(mapped.y || 0) - 18))

        // On narrow/high-DPI work areas the focus and reading cards sit in a
        // horizontally scrollable row.  Keep the requested card inside the
        // viewport instead of merely switching to the broad work tab.
        var minimumX = Number(flickable.originX || 0)
        var maximumX = Math.max(minimumX,
                                Number(flickable.contentWidth || 0)
                                - Number(flickable.width || 0))
        var currentX = Number(flickable.contentX || minimumX)
        var targetLeft = Number(mapped.x || 0)
        var targetRight = targetLeft + Number(descriptor.target.width || 0)
        var viewportWidth = Number(flickable.width || 0)
        if (targetLeft < currentX + 18)
            currentX = targetLeft - 18
        else if (targetRight > currentX + viewportWidth - 18)
            currentX = targetRight - viewportWidth + 18
        flickable.contentX = Math.max(minimumX, Math.min(maximumX, currentX))

        descriptor.target.forceActiveFocus()
        highlightedAnchor = normalized
        anchorHighlightTimer.restart()
        lastRevealedAnchor = normalized
        pendingAnchor = ""
        anchorPresentationCount += 1
        return true
    }

    onRequestedSectionChanged: {
        pendingSection = String(requestedSection || "work")
        if (visible)
            presentationTimer.restart()
    }

    onVisibleChanged: {
        if (visible) {
            pendingSection = String(requestedSection || pendingSection || "work")
            selectSection(pendingSection)
            presentationTimer.restart()
            if (pendingAnchor !== "")
                anchorPresentationTimer.restart()
        } else {
            presentationTimer.stop()
            anchorPresentationTimer.stop()
        }
    }

    Connections {
        target: root.appBackend
        ignoreUnknownSignals: true
        function onWorkPanelAnchorRequested(anchorName) {
            root.revealAnchor(anchorName)
        }
    }

    Timer {
        id: presentationTimer
        interval: 0
        repeat: false
        onTriggered: {
            if (root.visible)
                root.showSection(root.pendingSection)
        }
    }

    Timer {
        id: anchorPresentationTimer
        interval: 0
        repeat: false
        onTriggered: {
            // Wait one layout turn after selecting the connector tab so the
            // ScrollView knows its final content height and target position.
            Qt.callLater(function() { root.applyPendingAnchor() })
        }
    }

    Timer {
        id: anchorHighlightTimer
        interval: 1200
        repeat: false
        onTriggered: root.highlightedAnchor = ""
    }

    function stageFor(points) {
        if (points >= 1200)
            return "相伴"
        if (points >= 700)
            return "亲近"
        if (points >= 300)
            return "信赖"
        if (points >= 100)
            return "熟悉"
        return "初遇"
    }

    function stageBase(points) {
        if (points >= 1200)
            return 1200
        if (points >= 700)
            return 700
        if (points >= 300)
            return 300
        if (points >= 100)
            return 100
        return 0
    }

    function nextThreshold(points) {
        if (points >= 1200)
            return 1200
        if (points >= 700)
            return 1200
        if (points >= 300)
            return 700
        if (points >= 100)
            return 300
        return 100
    }

    function nextStage(points) {
        if (points >= 1200)
            return "已抵达相伴"
        if (points >= 700)
            return "相伴"
        if (points >= 300)
            return "亲近"
        if (points >= 100)
            return "信赖"
        return "熟悉"
    }

    property var taskItems: value("taskItems", value("tasks", []))
    property var reminderItems: value("reminderItems", value("reminders", []))
    property var focusInfo: value("focusStatus", ({ active: false, elapsedSeconds: 0, durationMinutes: 25 }))
    property var readingInfo: value("readingStatus", ({ active: false, elapsedSeconds: 0 }))
    property var growthInfo: value("growthStatus", ({ points: 0, stage: "初遇", unlocks: [] }))
    property int resonancePoints: Number(growthInfo.points !== undefined ? growthInfo.points
                                                                         : (growthInfo.value || 0))
    property var outfitItems: value("wardrobeOutfits", value("outfits", []))
    property var poseItems: value("wardrobePoses", value("poses", []))
    property var calendarInfo: value("calendarStatus", ({ connected: false, lastSyncAt: "", policy: {} }))
    property var slackInfo: value("slackStatus", ({ connected: false, workspace: "", policy: {} }))
    onSlackInfoChanged: {
        if (!Boolean(slackInfo.connected) && pendingAnchor === "slack-inbox") {
            anchorPresentationTimer.stop()
            pendingAnchor = ""
        }
    }
    property var calendarItems: value("calendarUpcoming", [])
    property var slackItems: value("slackInbox", [])
    property var boxWorldInfo: value("boxWorldStatus", ({ objects: [] }))
    readonly property var boxWorldObjects: boxWorldInfo.objects || []
    readonly property var boxWorldGrowth: boxWorldInfo.growth || growthInfo || ({})
    readonly property var boxWorldWardrobe: boxWorldInfo.wardrobe || ({})
    readonly property int boxWorldTotalCount: Number(
        boxWorldInfo.totalCount !== undefined ? boxWorldInfo.totalCount : boxWorldObjects.length)
    readonly property int boxWorldUnlockedCount: Number(
        boxWorldInfo.unlockedCount !== undefined ? boxWorldInfo.unlockedCount : 0)
    readonly property int boxWorldPlacedCount: Number(
        boxWorldInfo.placedCount !== undefined ? boxWorldInfo.placedCount : 0)
    readonly property var nextLockedWorldObject: {
        for (var index = 0; index < boxWorldObjects.length; ++index) {
            if (!Boolean(boxWorldObjects[index].unlocked))
                return boxWorldObjects[index]
        }
        return ({})
    }

    function worldObjectName(item) {
        return String(item.display_name || item.name || item.title
                      || item.object_id || item.id || "未命名陈设")
    }

    function worldUnlockHint(item) {
        return String(item.unlockHint || item.unlock_hint || "继续积累共鸣")
    }

    function worldKindLabel(kind) {
        var normalized = String(kind || "")
        if (normalized === "room")
            return "空间"
        if (normalized === "furniture")
            return "陈设"
        return normalized === "" ? "盒中物件" : normalized
    }

    component PaperCard: Rectangle {
        id: paperCard
        default property alias contentData: cardLayout.data
        property string heading: ""
        property string caption: ""
        property string anchorName: ""
        activeFocusOnTab: anchorName !== ""
        Layout.fillWidth: true
        implicitHeight: cardLayout.implicitHeight + 28
        radius: 15
        color: root.paperRaised
        border.color: root.highlightedAnchor === anchorName && anchorName !== ""
                      ? root.cord : root.hairline
        border.width: root.highlightedAnchor === anchorName && anchorName !== "" ? 2 : 1

        ColumnLayout {
            id: cardLayout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 9

            Text {
                visible: paperCard.heading !== ""
                Layout.fillWidth: true
                text: paperCard.heading
                color: root.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                visible: paperCard.caption !== ""
                Layout.fillWidth: true
                text: paperCard.caption
                color: root.mutedInk
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
        }
    }

    component QuietButton: Button {
        id: quietButton
        property bool accent: false
        implicitHeight: 36
        leftPadding: 14
        rightPadding: 14
        font.pixelSize: 12
        contentItem: Text {
            text: quietButton.text
            color: quietButton.accent ? "white" : root.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: quietButton.font
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 11
            color: quietButton.accent ? root.cord : (quietButton.hovered ? "#eee6db" : "#f8f3ec")
            border.color: quietButton.accent ? root.cord : root.hairline
        }
    }

    component StatusPill: Rectangle {
        id: statusPill
        property bool good: false
        property string label: ""
        implicitWidth: pillText.implicitWidth + 18
        implicitHeight: 24
        radius: 12
        color: good ? "#e5f0eb" : "#f1ebe2"
        border.color: good ? "#a8c0b7" : root.hairline
        Text {
            id: pillText
            anchors.centerIn: parent
            text: statusPill.label
            color: statusPill.good ? "#4c7565" : root.mutedInk
            font.pixelSize: 10
        }
    }

    component PaperScrollBar: ScrollBar {
        id: paperScrollBar
        implicitWidth: 10
        policy: ScrollBar.AsNeeded
        padding: 2
        background: Rectangle { color: "transparent" }
        contentItem: Rectangle {
            implicitWidth: 5
            radius: width / 2
            color: paperScrollBar.pressed ? root.cord : root.hairline
            opacity: paperScrollBar.active ? 0.82 : 0.46
        }
    }

    Rectangle {
        anchors.fill: parent
        color: root.paper

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 12

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        objectName: "workPanelSectionHeading"
                        text: root.sectionHeading
                        color: root.ink
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "workPanelSectionCaption"
                        Layout.fillWidth: true
                        text: root.sectionCaption
                        color: root.mutedInk
                        font.pixelSize: 11
                        wrapMode: Text.WordWrap
                    }
                }
                StatusPill {
                    good: true
                    label: root.stageFor(root.resonancePoints) + " · " + String(root.resonancePoints)
                }
            }

            Rectangle {
                visible: root.lastNotice !== ""
                Layout.fillWidth: true
                Layout.preferredHeight: 34
                radius: 10
                color: "#f4e7e2"
                border.color: "#d6b3aa"
                Text {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    verticalAlignment: Text.AlignVCenter
                    text: root.lastNotice
                    color: "#7c4942"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }
            }

            TabBar {
                id: sectionTabs
                objectName: "workPanelSectionTabs"
                Layout.fillWidth: true
                background: Rectangle { color: "transparent" }
                onCurrentIndexChanged: root.syncSectionFromTab(currentIndex)
                TabButton {
                    objectName: "workPanelWorkTab"
                    text: root.narrowViewport ? "任务" : "任务与专注"
                }
                TabButton {
                    objectName: "workPanelGrowthTab"
                    text: root.narrowViewport ? "共鸣" : "共鸣与衣橱"
                }
                TabButton {
                    objectName: "workPanelConnectorsTab"
                    text: root.narrowViewport ? "信笺" : "日历与 Slack"
                }
                TabButton {
                    objectName: "workPanelWorldTab"
                    text: root.narrowViewport ? "盒中" : "盒中世界"
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                currentIndex: sectionTabs.currentIndex

                ScrollView {
                    id: workScroll
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    ScrollBar.vertical: PaperScrollBar {}

                    ColumnLayout {
                        id: workContent
                        width: Math.max(workScroll.availableWidth, 640)
                        spacing: 12

                        PaperCard {
                            heading: "任务收件箱"
                            caption: "Calendar 和 Slack 不会自动变成任务；只有你确认后才会创建。"

                            RowLayout {
                                Layout.fillWidth: true
                                TextField {
                                    id: newTaskTitle
                                    Layout.fillWidth: true
                                    placeholderText: "写下一件要完成的事"
                                    selectByMouse: true
                                    Keys.onReturnPressed: createTaskButton.clicked()
                                    background: Rectangle {
                                        radius: 11
                                        color: "#fffdf9"
                                        border.color: newTaskTitle.activeFocus ? root.cord : root.hairline
                                    }
                                }
                                ComboBox {
                                    id: taskPriority
                                    model: ["普通", "较高", "重要"]
                                    implicitWidth: 88
                                }
                                QuietButton {
                                    id: createTaskButton
                                    text: "加入"
                                    accent: true
                                    enabled: newTaskTitle.text.trim() !== ""
                                    onClicked: {
                                        var title = newTaskTitle.text.trim()
                                        var priorityKeys = ["normal", "high", "critical"]
                                        if (root.invoke("tasksCreate", [{
                                                title: title,
                                                priority: priorityKeys[taskPriority.currentIndex],
                                                category: "inbox"
                                            }], "任务服务尚未接入"))
                                            newTaskTitle.clear()
                                    }
                                }
                            }

                            ListView {
                                id: taskList
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.min(250, Math.max(54, contentHeight))
                                model: root.taskItems
                                spacing: 5
                                clip: true
                                boundsBehavior: Flickable.StopAtBounds

                                delegate: Rectangle {
                                    id: taskRow
                                    required property var modelData
                                    width: ListView.view ? ListView.view.width : 0
                                    height: 42
                                    radius: 10
                                    color: taskHover.hovered ? "#f1e9de" : "#faf6ef"
                                    border.color: root.hairline

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 8
                                        spacing: 8
                                        Rectangle {
                                            Layout.preferredWidth: 7
                                            Layout.preferredHeight: 7
                                            radius: 3.5
                                            color: taskRow.modelData.priority === "critical" ? root.cord
                                                   : (taskRow.modelData.priority === "high" ? "#b38262" : "#9caa9f")
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: String(taskRow.modelData.title || "未命名任务")
                                            color: taskRow.modelData.completed ? root.mutedInk : root.ink
                                            font.strikeout: Boolean(taskRow.modelData.completed)
                                            elide: Text.ElideRight
                                            font.pixelSize: 12
                                        }
                                        Text {
                                            text: String(taskRow.modelData.dueLabel || taskRow.modelData.dueAt || "")
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                        }
                                        QuietButton {
                                            visible: !Boolean(taskRow.modelData.completed)
                                            text: "完成"
                                            implicitHeight: 30
                                            onClicked: root.invoke("tasksComplete", [taskRow.modelData.id || taskRow.modelData.taskId],
                                                                   "任务服务尚未接入")
                                        }
                                    }
                                    HoverHandler { id: taskHover }
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: taskList.count === 0
                                    text: "收件箱是空的"
                                    color: root.mutedInk
                                    font.pixelSize: 12
                                }
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 12

                            PaperCard {
                                id: focusCard
                                objectName: "workPanelFocusCard"
                                anchorName: "focus"
                                Layout.fillWidth: true
                                heading: "专注"
                                caption: "只记录显式会话。完成 25 分钟有效专注可获得 +8 共鸣，每日最多四次。"

                                RowLayout {
                                    Layout.fillWidth: true
                                    SpinBox {
                                        id: focusMinutes
                                        objectName: "focusMinutesInput"
                                        from: 5
                                        to: 180
                                        value: Number(root.focusInfo.durationMinutes || 25)
                                        editable: true
                                        enabled: !Boolean(root.focusInfo.active)
                                    }
                                    Text { text: "分钟"; color: root.mutedInk; font.pixelSize: 11 }
                                    Item { Layout.fillWidth: true }
                                    Text {
                                        text: root.focusInfo.active
                                              ? ("已进行 " + Math.floor(Number(root.focusInfo.elapsedSeconds || 0) / 60) + " 分钟")
                                              : "尚未开始"
                                        color: root.focusInfo.active ? root.calm : root.mutedInk
                                        font.pixelSize: 11
                                    }
                                }
                                RowLayout {
                                    QuietButton {
                                        objectName: "focusStartButton"
                                        visible: !Boolean(root.focusInfo.active)
                                        text: "开始专注"
                                        accent: true
                                        onClicked: root.invoke("focusStart", [focusMinutes.value], "专注服务尚未接入")
                                    }
                                    QuietButton {
                                        visible: Boolean(root.focusInfo.active)
                                        text: "结束本段"
                                        accent: true
                                        onClicked: root.invoke("focusFinish", [], "专注服务尚未接入")
                                    }
                                    QuietButton {
                                        visible: Boolean(root.focusInfo.active)
                                        text: root.focusInfo.paused ? "继续" : "暂停"
                                        onClicked: root.invoke(root.focusInfo.paused ? "focusResume" : "focusPause", [],
                                                               "专注服务尚未接入")
                                    }
                                    QuietButton {
                                        visible: Boolean(root.focusInfo.active)
                                        text: "取消"
                                        onClicked: root.invoke("focusCancel", [], "专注服务尚未接入")
                                    }
                                }
                            }

                            PaperCard {
                                id: readingCard
                                objectName: "workPanelReadingCard"
                                anchorName: "reading"
                                Layout.fillWidth: true
                                heading: "论文阅读"
                                caption: "检测到 PDF 或 WPS 时只提出建议，不会擅自开始计时。每 20 分钟有效阅读可获得 +6 共鸣。"

                                Text {
                                    Layout.fillWidth: true
                                    text: root.readingInfo.active
                                          ? ("正在阅读 · " + Math.floor(Number(root.readingInfo.elapsedSeconds || 0) / 60) + " 分钟")
                                          : "等待你手动开始"
                                    color: root.readingInfo.active ? root.calm : root.mutedInk
                                    font.pixelSize: 12
                                }
                                RowLayout {
                                    QuietButton {
                                        visible: !Boolean(root.readingInfo.active)
                                        text: "开始阅读"
                                        accent: true
                                        onClicked: root.invoke("readingStart", [], "阅读服务尚未接入")
                                    }
                                    QuietButton {
                                        visible: Boolean(root.readingInfo.active)
                                        text: "完成阅读"
                                        accent: true
                                        onClicked: root.invoke("readingFinish", [], "阅读服务尚未接入")
                                    }
                                }
                            }
                        }

                        PaperCard {
                            heading: "提醒"
                            caption: "提醒由本地调度器执行；模型不可用时仍然会准时工作。"
                            RowLayout {
                                Layout.fillWidth: true
                                TextField {
                                    id: reminderTitle
                                    Layout.fillWidth: true
                                    placeholderText: "提醒内容"
                                    background: Rectangle {
                                        radius: 11
                                        color: "#fffdf9"
                                        border.color: reminderTitle.activeFocus ? root.cord : root.hairline
                                    }
                                }
                                SpinBox { id: reminderDelay; from: 1; to: 1440; value: 30; editable: true }
                                Text { text: "分钟后"; color: root.mutedInk; font.pixelSize: 11 }
                                QuietButton {
                                    text: "创建提醒"
                                    enabled: reminderTitle.text.trim() !== ""
                                    onClicked: {
                                        var due = new Date(Date.now() + reminderDelay.value * 60000)
                                        if (root.invoke("remindersCreate", [{
                                                title: reminderTitle.text.trim(),
                                                dueAt: due.toISOString()
                                            }], "提醒服务尚未接入"))
                                            reminderTitle.clear()
                                    }
                                }
                            }
                            Repeater {
                                model: root.reminderItems.slice(0, 5)
                                delegate: RowLayout {
                                    id: reminderRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Text {
                                        Layout.fillWidth: true
                                        text: String(reminderRow.modelData.title || "提醒")
                                        color: root.ink
                                        elide: Text.ElideRight
                                        font.pixelSize: 11
                                    }
                                    Text {
                                        text: String(reminderRow.modelData.dueLabel || reminderRow.modelData.dueAt || "")
                                        color: root.mutedInk
                                        font.pixelSize: 10
                                    }
                                    QuietButton {
                                        text: "稍后"
                                        implicitHeight: 28
                                        onClicked: root.invoke("remindersSnooze", [reminderRow.modelData.id, 10], "提醒服务尚未接入")
                                    }
                                    QuietButton {
                                        text: "完成"
                                        implicitHeight: 28
                                        onClicked: root.invoke("remindersDismiss", [reminderRow.modelData.id], "提醒服务尚未接入")
                                    }
                                }
                            }
                        }
                    }
                }

                ScrollView {
                    id: resonanceScroll
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    ScrollBar.vertical: PaperScrollBar {}

                    ColumnLayout {
                        id: resonanceContent
                        width: Math.max(resonanceScroll.availableWidth, 640)
                        spacing: 12

                        PaperCard {
                            heading: "共鸣进度"
                            caption: "共鸣来自不可变事件账本；重复事件、重启重放和双击完成不会重复奖励。"

                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: root.stageFor(root.resonancePoints)
                                    color: root.ink
                                    font.pixelSize: 26
                                    font.weight: Font.DemiBold
                                }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: root.resonancePoints >= 1200
                                          ? (String(root.resonancePoints) + " · 已抵达相伴")
                                          : (String(root.resonancePoints) + " / "
                                             + String(root.nextThreshold(root.resonancePoints))
                                             + " · 下一阶段：" + root.nextStage(root.resonancePoints))
                                    color: root.mutedInk
                                    font.pixelSize: 12
                                }
                            }

                            ProgressBar {
                                id: resonanceProgress
                                Layout.fillWidth: true
                                from: 0
                                to: 1
                                value: {
                                    if (root.resonancePoints >= 1200)
                                        return 1
                                    var base = root.stageBase(root.resonancePoints)
                                    var ceiling = root.nextThreshold(root.resonancePoints)
                                    return (root.resonancePoints - base) / Math.max(1, ceiling - base)
                                }
                                background: Rectangle { radius: 4; color: "#ece3d7" }
                                contentItem: Item {
                                    implicitHeight: 8
                                    Rectangle {
                                        width: parent.width * resonanceProgress.visualPosition
                                        height: parent.height
                                        radius: 4
                                        color: root.cord
                                    }
                                }
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 3
                                columnSpacing: 14
                                rowSpacing: 6
                                Text { text: "任务完成 +10"; color: root.mutedInk; font.pixelSize: 11 }
                                Text { text: "专注 25 分钟 +8"; color: root.mutedInk; font.pixelSize: 11 }
                                Text { text: "论文阅读 20 分钟 +6"; color: root.mutedInk; font.pixelSize: 11 }
                                Text { text: "Slack 整理 +5"; color: root.mutedInk; font.pixelSize: 11 }
                                Text { text: "失败与延期 0"; color: root.mutedInk; font.pixelSize: 11 }
                                Text { text: "重复积分日上限 60"; color: root.mutedInk; font.pixelSize: 11 }
                            }
                        }

                        PaperCard {
                            id: wardrobeCard
                            objectName: "workPanelWardrobeCard"
                            anchorName: "wardrobe"
                            heading: "服装"
                            caption: "解锁只改变表达、动作和盒中陈设，不改变工具权限。"
                            Flow {
                                Layout.fillWidth: true
                                spacing: 8
                                Repeater {
                                    model: root.outfitItems
                                    delegate: QuietButton {
                                        id: outfitButton
                                        required property var modelData
                                        text: String(outfitButton.modelData.name || outfitButton.modelData.title
                                                     || outfitButton.modelData.id || "服装")
                                        enabled: outfitButton.modelData.unlocked === undefined
                                                 || Boolean(outfitButton.modelData.unlocked)
                                        accent: Boolean(outfitButton.modelData.equipped)
                                        onClicked: root.invoke("wardrobeEquip", [outfitButton.modelData.id], "衣橱服务尚未接入")
                                        ToolTip.visible: hovered && !enabled
                                        ToolTip.text: String(outfitButton.modelData.unlockHint || "尚未解锁")
                                    }
                                }
                                Text {
                                    visible: root.outfitItems.length === 0
                                    text: "衣橱数据正在接入"
                                    color: root.mutedInk
                                    font.pixelSize: 11
                                }
                            }
                        }

                        PaperCard {
                            heading: "姿态"
                            caption: "姿态只接受固定枚举；模型不能生成坐标、脚本或任意动作。"
                            Flow {
                                Layout.fillWidth: true
                                spacing: 8
                                Repeater {
                                    model: root.poseItems
                                    delegate: QuietButton {
                                        id: poseButton
                                        required property var modelData
                                        text: String(poseButton.modelData.name || poseButton.modelData.title
                                                     || poseButton.modelData.id || "姿态")
                                        enabled: poseButton.modelData.unlocked === undefined
                                                 || Boolean(poseButton.modelData.unlocked)
                                        accent: Boolean(poseButton.modelData.equipped)
                                        onClicked: {
                                            if (!root.invoke("poseEquip", [poseButton.modelData.id], ""))
                                                root.invoke("wardrobeEquipPose", [poseButton.modelData.id], "姿态服务尚未接入")
                                        }
                                    }
                                }
                                Text {
                                    visible: root.poseItems.length === 0
                                    text: "姿态数据正在接入"
                                    color: root.mutedInk
                                    font.pixelSize: 11
                                }
                            }
                        }

                        PaperCard {
                            heading: "已解锁"
                            Repeater {
                                model: root.growthInfo.unlocks || []
                                delegate: RowLayout {
                                    id: unlockRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Rectangle {
                                        Layout.preferredWidth: 6
                                        Layout.preferredHeight: 6
                                        radius: 3
                                        color: root.calm
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: String(unlockRow.modelData.name || unlockRow.modelData.title
                                                     || unlockRow.modelData.id || unlockRow.modelData)
                                        color: root.ink
                                        font.pixelSize: 11
                                    }
                                }
                            }
                            Text {
                                visible: !(root.growthInfo.unlocks && root.growthInfo.unlocks.length > 0)
                                text: "完成第一件现实事项后，莉莉丝会在这里留下变化。"
                                color: root.mutedInk
                                font.pixelSize: 11
                            }
                        }
                    }
                }

                ScrollView {
                    id: connectorScroll
                    objectName: "workPanelConnectorScroll"
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    ScrollBar.vertical: PaperScrollBar {}

                    ColumnLayout {
                        id: connectorContent
                        width: Math.max(connectorScroll.availableWidth, 640)
                        spacing: 12

                        PaperCard {
                            heading: "Google Calendar"
                            caption: "默认关闭。连接后采用必要范围、安静提醒、只留元数据和点击后协助；新建或修改都只会先生成预览，未确认绝不写入。"

                            RowLayout {
                                Layout.fillWidth: true
                                StatusPill {
                                    good: Boolean(root.calendarInfo.connected)
                                    label: root.calendarInfo.connected ? "已连接" : "未连接"
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.calendarInfo.connected
                                          ? ("最近同步 " + String(root.calendarInfo.lastSyncLabel
                                                                    || root.calendarInfo.lastSyncAt || "等待首次同步"))
                                          : "使用系统浏览器完成个人 Desktop OAuth"
                                    color: root.mutedInk
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                                QuietButton {
                                    text: root.calendarInfo.connected ? "刷新" : "连接向导"
                                    onClicked: {
                                        if (root.calendarInfo.connected)
                                            root.invoke("calendarRefresh", [], "Calendar 同步服务尚未接入")
                                        else
                                            root.openConnector("calendar")
                                    }
                                }
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 4
                                columnSpacing: 12
                                Text { text: "范围"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "打扰"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "留存"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "协助"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: String((root.calendarInfo.policy || {}).scope || "必要"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.calendarInfo.policy || {}).interruption || "安静"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.calendarInfo.policy || {}).retention || "元数据"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.calendarInfo.policy || {}).assistance || "协助"); color: root.ink; font.pixelSize: 11 }
                            }

                            ColumnLayout {
                                visible: root.calendarInfo.connected
                                Layout.fillWidth: true
                                spacing: 5
                                Text {
                                    text: root.calendarItems.length > 0 ? "近期日程" : "同步后，近期日程会出现在这里"
                                    color: root.mutedInk
                                    font.pixelSize: 10
                                }
                                Repeater {
                                    model: root.calendarItems.slice(0, 5)
                                    delegate: Rectangle {
                                        id: calendarEventRow
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: Math.max(54, calendarEventLayout.implicitHeight + 18)
                                        radius: 10
                                        color: "#fbf6ee"
                                        border.color: root.hairline
                                        RowLayout {
                                            id: calendarEventLayout
                                            anchors.fill: parent
                                            anchors.margins: 9
                                            Text {
                                                Layout.fillWidth: true
                                                text: String(calendarEventRow.modelData.summary || "未保留标题的日程")
                                                      + "  ·  " + String(calendarEventRow.modelData.occurredAt || "")
                                                color: root.ink
                                                font.pixelSize: 11
                                                elide: Text.ElideRight
                                            }
                                            QuietButton {
                                                text: "准备修改"
                                                enabled: root.itemId(calendarEventRow.modelData) !== ""
                                                onClicked: root.selectConnectorItem(
                                                               "calendar", root.itemId(calendarEventRow.modelData))
                                                ToolTip.visible: hovered
                                                ToolTip.text: String((root.calendarInfo.policyCanonical || {}).assistance || "")
                                                                  === "confirm-execute"
                                                              ? "打开仅含标题与时间的修改草稿"
                                                              : "可查看草稿；策略改为确认执行后才能生成写入提案"
                                            }
                                            QuietButton {
                                                text: "打开"
                                                enabled: Boolean(calendarEventRow.modelData.link)
                                                onClicked: root.invoke("calendarOpenEvent",
                                                                       [root.itemId(calendarEventRow.modelData)],
                                                                       "日程没有打开")
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        PaperCard {
                            id: slackInboxCard
                            objectName: "workPanelSlackInboxCard"
                            heading: "Slack 信笺"
                            caption: "个人 custom app 通过 Socket Mode 接收事件。未选来源会立即丢弃；单项回复只能先生成预览，未确认绝不发送。"

                            RowLayout {
                                Layout.fillWidth: true
                                StatusPill {
                                    good: Boolean(root.slackInfo.connected)
                                    label: root.slackInfo.connected ? "已连接" : "未连接"
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: root.slackInfo.connected
                                          ? String(root.slackInfo.workspace || "已连接工作区")
                                          : "需要你创建个人 Slack custom app"
                                    color: root.mutedInk
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                                QuietButton {
                                    objectName: "workPanelSlackInboxButton"
                                    text: root.slackInfo.connected ? "打开信笺匣" : "连接向导"
                                    onClicked: {
                                        if (root.slackInfo.connected)
                                            root.invoke("slackOpenInbox", [], "Slack 信笺匣尚未接入")
                                        else
                                            root.openConnector("slack")
                                    }
                                }
                            }

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 4
                                columnSpacing: 12
                                Text { text: "范围"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "打扰"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "留存"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: "协助"; color: root.mutedInk; font.pixelSize: 10 }
                                Text { text: String((root.slackInfo.policy || {}).scope || "必要"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.slackInfo.policy || {}).interruption || "安静"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.slackInfo.policy || {}).retention || "元数据"); color: root.ink; font.pixelSize: 11 }
                                Text { text: String((root.slackInfo.policy || {}).assistance || "协助"); color: root.ink; font.pixelSize: 11 }
                            }

                            ColumnLayout {
                                visible: root.slackInfo.connected
                                Layout.fillWidth: true
                                spacing: 5
                                Text {
                                    id: slackInboxAnchor
                                    objectName: "workPanelSlackInboxAnchor"
                                    activeFocusOnTab: true
                                    text: root.slackItems.length > 0 ? "本地信笺匣" : "等待私信、本人提及或精选来源"
                                    color: root.mutedInk
                                    font.pixelSize: 10
                                }
                                Repeater {
                                    model: root.slackItems.slice(0, 6)
                                    delegate: Rectangle {
                                        id: slackMessageRow
                                        required property var modelData
                                        Layout.fillWidth: true
                                        implicitHeight: Math.max(54, slackMessageLayout.implicitHeight + 18)
                                        radius: 10
                                        color: "#fbf6ee"
                                        border.color: root.hairline
                                        RowLayout {
                                            id: slackMessageLayout
                                            anchors.fill: parent
                                            anchors.margins: 9
                                            Text {
                                                Layout.fillWidth: true
                                                text: String(slackMessageRow.modelData.text
                                                             || slackMessageRow.modelData.summary
                                                             || "正文未在当前留存档位保存")
                                                color: root.ink
                                                font.pixelSize: 11
                                                elide: Text.ElideRight
                                            }
                                            StatusPill {
                                                good: Boolean(slackMessageRow.modelData.isDirect
                                                              || slackMessageRow.modelData.isMention)
                                                label: slackMessageRow.modelData.isDirect ? "私信"
                                                       : (slackMessageRow.modelData.isMention ? "提及" : "频道")
                                            }
                                            QuietButton {
                                                text: "回复"
                                                enabled: root.itemId(slackMessageRow.modelData) !== ""
                                                onClicked: root.selectConnectorItem(
                                                               "slack", root.itemId(slackMessageRow.modelData))
                                                ToolTip.visible: hovered
                                                ToolTip.text: String((root.slackInfo.policyCanonical || {}).assistance || "")
                                                                  === "confirm-execute"
                                                              ? "打开可编辑的回复草稿"
                                                              : "可查看草稿；策略改为确认执行后才能生成发送提案"
                                            }
                                            QuietButton {
                                                text: "打开"
                                                enabled: Boolean(slackMessageRow.modelData.link)
                                                onClicked: root.invoke("slackOpenMessage",
                                                                       [root.itemId(slackMessageRow.modelData)],
                                                                       "Slack 消息没有打开")
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        PaperCard {
                            heading: "执行边界"
                            caption: "Calendar 修改与 Slack 发送不会暴露给模型作为直接工具。莉莉丝只能准备提案，最终差异由确认界面提交。"
                            RowLayout {
                                Layout.fillWidth: true
                                Rectangle {
                                    Layout.preferredWidth: 7
                                    Layout.preferredHeight: 7
                                    radius: 3.5
                                    color: root.calm
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: "未确认绝不写入；外部文本按不可信数据处理。"
                                    color: root.ink
                                    font.pixelSize: 12
                                }
                                QuietButton {
                                    text: "连接器设置"
                                    onClicked: root.openConnector("calendar")
                                }
                            }
                        }
                    }
                }

                ScrollView {
                    id: worldScroll
                    clip: true
                    ScrollBar.horizontal.policy: ScrollBar.AsNeeded
                    ScrollBar.vertical: PaperScrollBar {}

                    ColumnLayout {
                        width: Math.max(worldScroll.availableWidth, 640)
                        spacing: 12

                        PaperCard {
                            objectName: "boxWorldOverview"
                            heading: "盒中世界"
                            caption: "这是纸卡式的空间记录，不是 3D 场景。现实事项会解锁陈设；摆放只改变盒中记录，不会改变工具权限。"

                            RowLayout {
                                Layout.fillWidth: true
                                StatusPill {
                                    good: Boolean(root.boxWorldInfo.entered || root.boxWorldInfo.active)
                                    label: root.boxWorldInfo.entered || root.boxWorldInfo.active ? "已进入" : "可查看"
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: String(root.boxWorldInfo.name || "莉莉丝的盒中空间")
                                    color: root.ink
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: String(root.boxWorldPlacedCount) + " 件正在摆放"
                                    color: root.mutedInk
                                    font.pixelSize: 11
                                }
                            }

                            RowLayout {
                                objectName: "boxWorldProgressSummary"
                                Layout.fillWidth: true
                                spacing: 8

                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 70
                                    radius: 11
                                    color: "#f6efe6"
                                    border.color: root.hairline
                                    Column {
                                        anchors.centerIn: parent
                                        spacing: 3
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: String(root.boxWorldUnlockedCount) + " / "
                                                  + String(root.boxWorldTotalCount)
                                            color: root.cord
                                            font.pixelSize: 18
                                            font.weight: Font.DemiBold
                                        }
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: "已解锁陈设"
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 70
                                    radius: 11
                                    color: "#f1f6f2"
                                    border.color: "#bfd0c8"
                                    Column {
                                        anchors.centerIn: parent
                                        spacing: 3
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: String(root.boxWorldGrowth.stage || root.stageFor(root.resonancePoints))
                                            color: root.calm
                                            font.pixelSize: 18
                                            font.weight: Font.DemiBold
                                        }
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: String(root.boxWorldGrowth.points !== undefined
                                                         ? root.boxWorldGrowth.points : root.resonancePoints)
                                                  + " 点共鸣"
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: 70
                                    radius: 11
                                    color: "#f8f3ec"
                                    border.color: root.hairline
                                    Column {
                                        anchors.centerIn: parent
                                        width: parent.width - 14
                                        spacing: 3
                                        Text {
                                            width: parent.width
                                            horizontalAlignment: Text.AlignHCenter
                                            text: String(root.boxWorldWardrobe.outfitName || "初遇裂纹裙")
                                            color: root.ink
                                            font.pixelSize: 12
                                            font.weight: Font.Medium
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            width: parent.width
                                            horizontalAlignment: Text.AlignHCenter
                                            text: String(root.boxWorldWardrobe.poseName || "抱拳祈祷")
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                            }

                            ProgressBar {
                                id: boxWorldProgress
                                Layout.fillWidth: true
                                from: 0
                                to: Math.max(1, root.boxWorldTotalCount)
                                value: root.boxWorldUnlockedCount
                                background: Rectangle { radius: 3; color: "#ece3d7" }
                                contentItem: Item {
                                    implicitHeight: 6
                                    Rectangle {
                                        width: parent.width * boxWorldProgress.visualPosition
                                        height: parent.height
                                        radius: 3
                                        color: root.cord
                                    }
                                }
                            }
                        }

                        PaperCard {
                            objectName: "boxWorldObjectsCard"
                            heading: "陈设进度"
                            caption: "已解锁但尚未摆放的陈设可以加入这份纸卡空间；未解锁项目只显示明确线索。"

                            Rectangle {
                                objectName: "boxWorldNextStep"
                                visible: String(root.nextLockedWorldObject.object_id || "") !== ""
                                Layout.fillWidth: true
                                implicitHeight: Math.max(54, nextStepLayout.implicitHeight + 18)
                                radius: 11
                                color: "#fbf2e7"
                                border.color: "#d8bea4"

                                RowLayout {
                                    id: nextStepLayout
                                    anchors.fill: parent
                                    anchors.margins: 9
                                    spacing: 10
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 2
                                        Text {
                                            Layout.fillWidth: true
                                            text: "下一条盒中线索 · "
                                                  + root.worldObjectName(root.nextLockedWorldObject)
                                            color: root.ink
                                            font.pixelSize: 11
                                            font.weight: Font.DemiBold
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: root.worldUnlockHint(root.nextLockedWorldObject)
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                            elide: Text.ElideRight
                                        }
                                    }
                                    QuietButton {
                                        objectName: "boxWorldGrowthButton"
                                        text: "查看成长与衣橱"
                                        onClicked: root.selectSection("growth")
                                    }
                                }
                            }

                            Repeater {
                                model: root.boxWorldObjects
                                delegate: Rectangle {
                                    id: worldObjectRow
                                    required property var modelData
                                    objectName: "boxWorldObject_" + String(worldObjectRow.modelData.object_id
                                                                           || worldObjectRow.modelData.id || "unknown")
                                    Layout.fillWidth: true
                                    implicitHeight: 62
                                    radius: 10
                                    color: Boolean(worldObjectRow.modelData.placed) ? "#f1f6f2" : "#fbf6ee"
                                    border.color: Boolean(worldObjectRow.modelData.placed) ? root.calm : root.hairline

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 10

                                        Rectangle {
                                            Layout.preferredWidth: 8
                                            Layout.preferredHeight: 8
                                            radius: 4
                                            color: Boolean(worldObjectRow.modelData.placed) ? root.calm
                                                   : (Boolean(worldObjectRow.modelData.unlocked)
                                                      ? "#b7966f" : root.hairline)
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2
                                            Text {
                                                Layout.fillWidth: true
                                                text: root.worldObjectName(worldObjectRow.modelData)
                                                color: Boolean(worldObjectRow.modelData.unlocked)
                                                       ? root.ink : root.mutedInk
                                                font.pixelSize: 12
                                                font.weight: Font.Medium
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: Boolean(worldObjectRow.modelData.unlocked)
                                                      ? (root.worldKindLabel(worldObjectRow.modelData.object_kind)
                                                         + " · 已收入盒中世界")
                                                      : ("解锁线索：" + root.worldUnlockHint(worldObjectRow.modelData))
                                                color: root.mutedInk
                                                font.pixelSize: 10
                                                elide: Text.ElideRight
                                            }
                                        }

                                        StatusPill {
                                            visible: Boolean(worldObjectRow.modelData.placed)
                                                     || !Boolean(worldObjectRow.modelData.unlocked)
                                            good: Boolean(worldObjectRow.modelData.placed)
                                            label: worldObjectRow.modelData.placed ? "已摆放"
                                                   : "待解锁"
                                        }
                                        QuietButton {
                                            objectName: "boxWorldPlace_" + String(worldObjectRow.modelData.object_id
                                                                                 || worldObjectRow.modelData.id || "")
                                            visible: Boolean(worldObjectRow.modelData.unlocked)
                                                     && !Boolean(worldObjectRow.modelData.placed)
                                            text: "摆入盒中"
                                            accent: true
                                            onClicked: root.invoke(
                                                "boxWorldPlace",
                                                [String(worldObjectRow.modelData.object_id
                                                        || worldObjectRow.modelData.id || "")],
                                                "盒中陈设服务尚未接入")
                                        }
                                    }
                                }
                            }

                            Text {
                                visible: root.boxWorldObjects.length === 0
                                Layout.fillWidth: true
                                text: "盒中目录正在接入；现实事项带来的解锁会出现在这里。"
                                color: root.mutedInk
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }
    }
}
