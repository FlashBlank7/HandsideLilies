pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root
    objectName: "v03BoxWorldScene"
    transientParent: null

    property var appBackend: null
    property var presentationArea: ({})
    property bool requestedVisible: false
    property bool stayOnTopWhenPresented: false
    property string lastNotice: ""
    property int presentationCount: 0
    signal exitRequested()
    signal manageDecorationsRequested()

    readonly property color paper: "#fffaf0"
    readonly property color paperRaised: "#fffdf8"
    readonly property color ink: "#49443e"
    readonly property color mutedInk: "#82786d"
    readonly property color hairline: "#d6c4ad"
    readonly property color cord: "#93352e"
    readonly property color calm: "#667f74"
    readonly property bool narrowViewport: width < 840 || height < 610
    readonly property bool microViewport: width < 500 || height < 430
    readonly property real areaLeft: finiteAreaValue("left", Screen.virtualX)
    readonly property real areaTop: finiteAreaValue("top", Screen.virtualY)
    readonly property real areaWidth: {
        var fallback = Math.max(640, Screen.width)
        var value = Number(presentationArea.width)
        if (isFinite(value) && value > 0)
            return value
        var right = Number(presentationArea.right)
        var left = Number(presentationArea.left)
        return isFinite(right) && isFinite(left) && right > left ? right - left : fallback
    }
    readonly property real areaHeight: {
        var fallback = Math.max(480, Screen.height)
        var value = Number(presentationArea.height)
        if (isFinite(value) && value > 0)
            return value
        var bottom = Number(presentationArea.bottom)
        var top = Number(presentationArea.top)
        return isFinite(bottom) && isFinite(top) && bottom > top ? bottom - top : fallback
    }
    readonly property real areaRight: finiteAreaValue("right", areaLeft + areaWidth)
    readonly property real areaBottom: finiteAreaValue("bottom", areaTop + areaHeight)

    readonly property var boxWorldInfo: backendValue("boxWorldStatus", ({ objects: [] }))
    readonly property var boxWorldObjects: boxWorldInfo.objects || []
    readonly property var growthInfo: boxWorldInfo.growth
                                       || backendValue("growthStatus", ({ points: 0, stage: "初遇" }))
                                       || ({})
    readonly property var wardrobeInfo: boxWorldInfo.wardrobe || ({})
    readonly property int resonancePoints: Number(growthInfo.points !== undefined
                                                   ? growthInfo.points
                                                   : (growthInfo.totalPoints || 0))
    readonly property int totalCount: Number(boxWorldInfo.totalCount !== undefined
                                             ? boxWorldInfo.totalCount
                                             : boxWorldObjects.length)
    readonly property int unlockedCount: Number(boxWorldInfo.unlockedCount || 0)
    readonly property int placedCount: Number(boxWorldInfo.placedCount || 0)
    readonly property var placedObjects: collectPlacedObjects()
    readonly property real resonanceProgress: normalizedResonanceProgress()

    minimumWidth: Math.max(360, Math.min(760, areaWidth - 24))
    minimumHeight: Math.max(360, Math.min(540, areaHeight - 24))
    width: Math.max(minimumWidth, Math.min(1240, areaWidth - 32))
    height: Math.max(minimumHeight, Math.min(820, areaHeight - 40))
    x: areaLeft + Math.max(12, (areaWidth - width) / 2)
    y: areaTop + Math.max(12, (areaHeight - height) / 2)
    visible: requestedVisible
    color: "#2f2923"
    title: "莉莉丝 · 盒中世界"
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
           | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
           | (stayOnTopWhenPresented ? Qt.WindowStaysOnTopHint : 0)

    function finiteAreaValue(name, fallbackValue) {
        var value = Number(presentationArea[name])
        return isFinite(value) ? value : fallbackValue
    }

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

    function collectPlacedObjects() {
        var result = []
        for (var index = 0; index < boxWorldObjects.length; ++index) {
            var item = boxWorldObjects[index]
            if (Boolean(item.placed))
                result.push(item)
        }
        return result
    }

    function objectId(item) {
        return String(item.object_id || item.id || "unknown")
    }

    function objectName(item) {
        return String(item.display_name || item.name || item.title || objectId(item))
    }

    function objectHint(item) {
        return String(item.unlockHint || item.unlock_hint || "继续积累共鸣")
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

    function normalizedResonanceProgress() {
        var supplied = Number(growthInfo.progress)
        if (isFinite(supplied) && supplied >= 0 && supplied <= 1)
            return supplied
        if (resonancePoints >= 1200)
            return 1
        var base = stageBase(resonancePoints)
        return Math.max(0, Math.min(1,
            (resonancePoints - base) / Math.max(1, nextThreshold(resonancePoints) - base)))
    }

    function outfitSource(outfitId) {
        var id = String(outfitId || "first-encounter")
        if (id === "home-cardigan")
            return "../themes/first-encounter/assets/lilith-outfit-home-cardigan-transparent-v2.png"
        if (id === "reading-smock")
            return "../themes/first-encounter/assets/lilith-outfit-reading-smock-transparent-v1.png"
        if (id === "focus-coat")
            return "../themes/first-encounter/assets/lilith-outfit-focus-coat-transparent-v1.png"
        if (id === "rest-nightdress")
            return "../themes/first-encounter/assets/lilith-outfit-rest-nightdress-transparent-v2.png"
        return "../themes/first-encounter/assets/lilith-desktop-pet-chibi-v4.png"
    }

    function worldPosition(item, axis) {
        var position = item.position || item.position_json || ({})
        var value = Number(position[axis])
        if (isFinite(value) && value >= 0 && value <= 1)
            return value
        var id = objectId(item)
        if (axis === "x") {
            if (id === "paper-shelf") return 0.77
            if (id === "workbench") return 0.63
            if (id === "living-corner") return 0.25
            if (id === "letter-rack") return 0.82
            if (id === "rest-cushion") return 0.34
            return 0.50
        }
        if (id === "paper-shelf") return 0.37
        if (id === "workbench") return 0.72
        if (id === "living-corner") return 0.67
        if (id === "letter-rack") return 0.62
        if (id === "rest-cushion") return 0.82
        return 0.58
    }

    function ensureReachable() {
        // Prefer a completely contained large scene whenever the supplied
        // work area can hold it.  This also repairs coordinates remembered
        // from a monitor that has since been disconnected.
        if (width <= areaWidth) {
            if (x < areaLeft)
                x = areaLeft
            else if (x + width > areaRight)
                x = areaRight - width
        }
        if (height <= areaHeight) {
            if (y < areaTop)
                y = areaTop
            else if (y + height > areaBottom)
                y = areaBottom - height
        }
        var visibleWidth = Math.max(0, Math.min(x + width, areaRight) - Math.max(x, areaLeft))
        var visibleHeight = Math.max(0, Math.min(y + height, areaBottom) - Math.max(y, areaTop))
        if (visibleWidth >= Math.min(160, width * 0.30)
                && visibleHeight >= Math.min(100, height * 0.24))
            return
        x = areaLeft + Math.max(12, (areaWidth - width) / 2)
        y = areaTop + Math.max(12, (areaHeight - height) / 2)
    }

    function present() {
        requestedVisible = true
        presentationCount += 1
        if (visibility === Window.Minimized)
            showNormal()
        ensureReachable()
        raise()
        requestActivate()
    }

    function toggleFullScreen() {
        if (visibility === Window.FullScreen)
            showNormal()
        else
            showFullScreen()
    }

    onRequestedVisibleChanged: {
        if (requestedVisible)
            presentationTimer.restart()
        else
            presentationTimer.stop()
    }

    onPresentationAreaChanged: {
        if (requestedVisible)
            ensureReachable()
    }

    onClosing: function(close) {
        close.accepted = false
        requestedVisible = false
        exitRequested()
    }

    Timer {
        id: presentationTimer
        interval: 0
        repeat: false
        onTriggered: root.present()
    }

    component PaperButton: Button {
        id: paperButton
        property bool accent: false
        implicitHeight: 42
        leftPadding: 17
        rightPadding: 17
        font.pixelSize: 13
        contentItem: Text {
            text: paperButton.text
            color: paperButton.accent ? "white" : root.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            font: paperButton.font
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 10
            color: paperButton.accent
                   ? (paperButton.down ? "#78302b" : root.cord)
                   : (paperButton.hovered ? "#f4ebdf" : root.paperRaised)
            border.color: paperButton.accent ? "#742b27" : root.hairline
            border.width: 1
        }
    }

    component WorldDecoration: Item {
        id: decoration
        required property var modelData
        readonly property string decorationId: root.objectId(modelData)
        objectName: "boxWorldPlaced_" + decorationId
        width: Math.max(88, Math.min(138, worldStage.width * 0.16))
        height: width * 0.76
        x: Math.max(8, Math.min(worldStage.width - width - 8,
            root.worldPosition(modelData, "x") * worldStage.width - width / 2))
        y: Math.max(worldStage.height * 0.23, Math.min(worldStage.height - height - 20,
            root.worldPosition(modelData, "y") * worldStage.height - height / 2))
        z: 6 + y / Math.max(1, worldStage.height) * 4

        Rectangle {
            id: tokenShadow
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            width: parent.width * 0.76
            height: parent.height * 0.13
            radius: height / 2
            color: "#442e1d24"
            scale: 1.0 + Math.sin(sceneClock.phase + decoration.x * 0.01) * 0.02
        }

        Canvas {
            id: tokenArt
            objectName: "boxWorldDecorationArt_" + decoration.decorationId
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: tokenShadow.verticalCenter
            width: parent.width * 0.76
            height: parent.height * 0.72
            antialiasing: true
            onPaint: {
                var context = getContext("2d")
                context.clearRect(0, 0, width, height)
                var id = decoration.decorationId
                context.lineJoin = "round"
                context.lineCap = "round"
                context.strokeStyle = "#8b735a"
                context.lineWidth = Math.max(1.4, width * 0.025)
                context.fillStyle = "#fffaf0"
                if (id === "paper-shelf") {
                    context.fillRect(width * 0.13, height * 0.08, width * 0.74, height * 0.84)
                    context.strokeRect(width * 0.13, height * 0.08, width * 0.74, height * 0.84)
                    for (var shelf = 1; shelf < 3; ++shelf) {
                        context.beginPath()
                        context.moveTo(width * 0.16, height * (0.08 + shelf * 0.28))
                        context.lineTo(width * 0.84, height * (0.08 + shelf * 0.28))
                        context.stroke()
                    }
                    context.strokeStyle = "#a74b45"
                    for (var book = 0; book < 5; ++book) {
                        context.beginPath()
                        context.moveTo(width * (0.23 + book * 0.11), height * 0.17)
                        context.lineTo(width * (0.22 + book * 0.11), height * 0.34)
                        context.stroke()
                    }
                } else if (id === "workbench") {
                    context.fillRect(width * 0.08, height * 0.35, width * 0.84, height * 0.22)
                    context.strokeRect(width * 0.08, height * 0.35, width * 0.84, height * 0.22)
                    context.beginPath()
                    context.moveTo(width * 0.20, height * 0.57)
                    context.lineTo(width * 0.14, height * 0.93)
                    context.moveTo(width * 0.80, height * 0.57)
                    context.lineTo(width * 0.86, height * 0.93)
                    context.stroke()
                    context.fillStyle = "#ece7dd"
                    context.fillRect(width * 0.35, height * 0.14, width * 0.30, height * 0.20)
                    context.strokeRect(width * 0.35, height * 0.14, width * 0.30, height * 0.20)
                } else if (id === "letter-rack") {
                    context.beginPath()
                    context.moveTo(width * 0.12, height * 0.24)
                    context.lineTo(width * 0.88, height * 0.24)
                    context.lineTo(width * 0.78, height * 0.90)
                    context.lineTo(width * 0.22, height * 0.90)
                    context.closePath()
                    context.fill()
                    context.stroke()
                    context.strokeStyle = "#a74b45"
                    for (var letter = 0; letter < 3; ++letter)
                        context.strokeRect(width * (0.24 + letter * 0.14), height * (0.10 + letter * 0.06), width * 0.36, height * 0.24)
                } else if (id === "rest-cushion") {
                    context.beginPath()
                    context.ellipse(width * 0.50, height * 0.63, width * 0.43, height * 0.26, 0, 0, Math.PI * 2)
                    context.fill()
                    context.stroke()
                    context.strokeStyle = "#c99f9b"
                    context.beginPath()
                    context.moveTo(width * 0.22, height * 0.62)
                    context.quadraticCurveTo(width * 0.50, height * 0.75, width * 0.78, height * 0.62)
                    context.stroke()
                } else if (id === "living-corner") {
                    context.beginPath()
                    context.moveTo(width * 0.12, height * 0.88)
                    context.lineTo(width * 0.12, height * 0.24)
                    context.lineTo(width * 0.50, height * 0.08)
                    context.lineTo(width * 0.88, height * 0.24)
                    context.lineTo(width * 0.88, height * 0.88)
                    context.closePath()
                    context.fill()
                    context.stroke()
                    context.fillStyle = "#d8e4dc"
                    context.fillRect(width * 0.25, height * 0.52, width * 0.22, height * 0.34)
                    context.fillStyle = "#ead8cb"
                    context.fillRect(width * 0.56, height * 0.43, width * 0.18, height * 0.43)
                } else {
                    context.beginPath()
                    context.arc(width * 0.50, height * 0.52, width * 0.36, 0, Math.PI * 2)
                    context.fill()
                    context.stroke()
                    context.strokeStyle = root.cord
                    context.beginPath()
                    context.arc(width * 0.50, height * 0.52, width * 0.22, 0, Math.PI * 2)
                    context.stroke()
                }
            }
        }

        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            visible: decoration.decorationId !== "box-core"
            width: Math.min(parent.width, decorationName.implicitWidth + 20)
            height: 25
            radius: 8
            color: "#fdf8efeb"
            border.color: "#d8c7b2"
            Text {
                id: decorationName
                anchors.fill: parent
                anchors.leftMargin: 9
                anchors.rightMargin: 9
                text: root.objectName(decoration.modelData)
                color: root.ink
                font.pixelSize: 11
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "#302921"

        Image {
            anchors.fill: parent
            source: "../themes/first-encounter/assets/first-encounter-background.png"
            fillMode: Image.PreserveAspectCrop
            smooth: true
            opacity: 0.16
        }

        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#2c261fdd" }
                GradientStop { position: 0.42; color: "#534536b8" }
                GradientStop { position: 1.0; color: "#211d19ee" }
            }
        }
    }

    Rectangle {
        id: titleBar
        objectName: "boxWorldSceneTitleBar"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: root.microViewport ? 64 : 72
        color: "#fffaf0f5"
        border.color: root.hairline
        z: 40

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: root.microViewport ? 14 : 24
            anchors.rightMargin: root.microViewport ? 12 : 18
            spacing: root.microViewport ? 8 : 14

            Rectangle {
                Layout.preferredWidth: root.microViewport ? 10 : 12
                Layout.preferredHeight: root.microViewport ? 10 : 12
                radius: width / 2
                color: root.cord
                Rectangle {
                    anchors.centerIn: parent
                    width: 4
                    height: 4
                    radius: 2
                    color: "#fff8ed"
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    objectName: "boxWorldSceneTitleText"
                    Layout.fillWidth: true
                    text: "盒中世界"
                    color: root.ink
                    font.pixelSize: root.microViewport ? 18 : 21
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    visible: root.width >= 540
                    text: "现实里完成的事情，会在这片洁白空间留下形状。"
                    color: root.mutedInk
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
            }

            PaperButton {
                id: fullScreenButton
                objectName: "boxWorldSceneFullScreenButton"
                Layout.preferredWidth: root.microViewport ? 58
                                       : (root.width < 640 ? 72 : 102)
                Layout.preferredHeight: root.microViewport ? 36 : 42
                leftPadding: root.microViewport ? 9 : 17
                rightPadding: root.microViewport ? 9 : 17
                text: root.visibility === Window.FullScreen
                      ? (root.width < 640 ? "还原" : "退出全屏")
                      : (root.width < 640 ? "全屏" : "全屏沉浸")
                onClicked: root.toggleFullScreen()
            }
            PaperButton {
                id: exitButton
                objectName: "boxWorldSceneExitButton"
                Layout.preferredWidth: root.microViewport ? 56
                                       : (root.width < 640 ? 68 : 82)
                Layout.preferredHeight: root.microViewport ? 36 : 42
                leftPadding: root.microViewport ? 9 : 17
                rightPadding: root.microViewport ? 9 : 17
                text: "离开"
                accent: true
                onClicked: {
                    root.requestedVisible = false
                    root.exitRequested()
                }
            }
        }
    }

    Item {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: titleBar.bottom
        anchors.bottom: footer.top

        Rectangle {
            id: worldStage
            objectName: "boxWorldSceneStage"
            x: root.narrowViewport ? 12 : 22
            y: 14
            width: root.narrowViewport ? body.width - 24 : body.width - infoPanel.width - 58
            height: body.height - 28
            radius: 24
            clip: true
            color: "#eee5d6"
            border.color: "#bca589"
            border.width: 2

            Canvas {
                id: paperRoom
                objectName: "boxWorldPaperRoom"
                anchors.fill: parent
                antialiasing: true
                onPaint: {
                    var context = getContext("2d")
                    context.clearRect(0, 0, width, height)
                    function polygon(points, fill, stroke) {
                        context.beginPath()
                        context.moveTo(points[0][0] * width, points[0][1] * height)
                        for (var index = 1; index < points.length; ++index)
                            context.lineTo(points[index][0] * width, points[index][1] * height)
                        context.closePath()
                        context.fillStyle = fill
                        context.fill()
                        context.strokeStyle = stroke
                        context.lineWidth = Math.max(1, width * 0.0015)
                        context.stroke()
                    }
                    var back = context.createLinearGradient(0, height * 0.08, 0, height * 0.90)
                    back.addColorStop(0, "#fffef9")
                    back.addColorStop(0.62, "#f8f4e9")
                    back.addColorStop(1, "#e9dfcf")
                    polygon([[0.16, 0.08], [0.84, 0.08], [0.90, 0.84], [0.10, 0.84]], back, "#d7c4aa")
                    polygon([[0, 0.03], [0.16, 0.08], [0.10, 0.84], [0, 0.98]], "#a77e58", "#7f5d42")
                    polygon([[1, 0.03], [0.84, 0.08], [0.90, 0.84], [1, 0.98]], "#9b714f", "#765139")
                    var floor = context.createLinearGradient(0, height * 0.66, 0, height)
                    floor.addColorStop(0, "#f5efe4")
                    floor.addColorStop(1, "#d8c5ab")
                    polygon([[0.10, 0.84], [0.90, 0.84], [1, 1], [0, 1]], floor, "#b99e7c")
                    polygon([[0, 0], [0.47, 0], [0.31, 0.13], [0.03, 0.17]], "#c69a6c", "#856241")
                    polygon([[1, 0], [0.53, 0], [0.69, 0.13], [0.97, 0.17]], "#bd8e61", "#805c3e")
                    context.strokeStyle = "#eee9df"
                    context.globalAlpha = 0.55
                    context.lineWidth = 1
                    for (var line = 0; line < 7; ++line) {
                        context.beginPath()
                        context.moveTo(width * (0.17 + line * 0.095), height * 0.13)
                        context.lineTo(width * (0.12 + line * 0.11), height * 0.82)
                        context.stroke()
                    }
                    context.globalAlpha = 1
                }
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
            }

            Item {
                id: rearLight
                anchors.centerIn: parent
                width: parent.width * 0.53
                height: parent.height * 0.67
                y: -parent.height * 0.04
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    color: "#f7ffff"
                    opacity: 0.19 + Math.sin(sceneClock.phase) * 0.025
                    scale: 1.0 + Math.sin(sceneClock.phase) * 0.012
                }
            }

            Repeater {
                model: 16
                delegate: Rectangle {
                    required property int index
                    width: 2 + (index % 3)
                    height: width
                    radius: width / 2
                    x: worldStage.width * (0.12 + ((index * 37) % 73) / 100)
                    y: worldStage.height * (0.12 + ((index * 23) % 70) / 100)
                       + Math.sin(sceneClock.phase + index) * 7
                    color: index % 4 === 0 ? "#d9ffff" : "#fffdf4"
                    opacity: 0.30 + (index % 5) * 0.08
                    z: 3
                }
            }

            QtObject {
                id: sceneClock
                property real phase: 0
                NumberAnimation on phase {
                    from: 0
                    to: Math.PI * 2
                    duration: 6200
                    loops: Animation.Infinite
                    running: root.visible
                }
            }

            Repeater {
                id: decorationRepeater
                model: root.placedObjects
                delegate: WorldDecoration {}
            }

            Item {
                id: characterLayer
                objectName: "boxWorldLilithLayer"
                width: Math.max(root.microViewport ? 112 : 150,
                                Math.min(330, worldStage.width
                                         * (root.narrowViewport ? 0.34 : 0.31)))
                height: Math.min(worldStage.height * 0.78, width / 0.5628)
                x: Math.max(12, worldStage.width * 0.42 - width / 2)
                y: worldStage.height - height - Math.max(12, worldStage.height * 0.035) + breathOffset
                z: 14
                property real breathOffset: 0

                NumberAnimation on breathOffset {
                    from: -1.5
                    to: 2.0
                    duration: 3300
                    easing.type: Easing.InOutSine
                    loops: Animation.Infinite
                    running: root.visible
                }

                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    width: parent.width * 0.48
                    height: parent.height * 0.035
                    radius: height / 2
                    color: "#58473724"
                }

                Image {
                    id: worldLilith
                    objectName: "boxWorldLilithImage"
                    anchors.fill: parent
                    source: root.outfitSource(root.wardrobeInfo.outfitId)
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
                    asynchronous: true
                    rotation: Math.sin(sceneClock.phase + 0.6) * 0.18
                    transformOrigin: Item.Bottom
                }

                Image {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: parent.height * 0.37
                    width: parent.width * 0.42
                    height: parent.height * 0.38
                    source: "../themes/first-encounter/assets/crack-glow.svg"
                    fillMode: Image.PreserveAspectFit
                    opacity: 0.11 + Math.sin(sceneClock.phase + 2.3) * 0.045
                }
            }

            Rectangle {
                id: greetingCard
                objectName: "boxWorldGreetingCard"
                visible: !root.narrowViewport
                x: Math.max(18, worldStage.width * 0.07)
                y: Math.max(24, worldStage.height * 0.16)
                width: Math.max(190, Math.min(330, worldStage.width * 0.38))
                height: greetingColumn.implicitHeight + 30
                radius: 14
                color: "#fffdf8e8"
                border.color: root.hairline
                z: 20

                Column {
                    id: greetingColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 15
                    spacing: 8
                    Text {
                        width: parent.width
                        text: "这里比纸箱外面安静一些。"
                        color: root.ink
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                        lineHeight: 1.18
                    }
                    Text {
                        id: greetingText
                        objectName: "boxWorldGreetingText"
                        width: parent.width
                        text: root.placedCount > 1
                              ? ("已经有 " + String(root.placedCount) + " 件共同留下的陈设。我会替你看好它们。")
                              : "现在还很空。没关系，完成过的事情会慢慢成为这里的家具。"
                        color: root.mutedInk
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        lineHeight: 1.32
                    }
                }
            }

            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: 9
                color: "#8f6846"
                opacity: 0.52
                z: 30
            }
        }

        Rectangle {
            id: infoPanel
            objectName: "boxWorldInfoPanel"
            x: root.narrowViewport ? worldStage.x + 14 : worldStage.x + worldStage.width + 16
            y: root.narrowViewport ? worldStage.y + worldStage.height - height - 14 : worldStage.y + 10
            width: root.narrowViewport ? worldStage.width - 28
                                       : Math.max(278, Math.min(334, body.width * 0.29))
            height: root.microViewport
                    ? Math.max(88, Math.min(98, worldStage.height - 20))
                    : (root.narrowViewport
                       ? Math.max(128, Math.min(142, worldStage.height - 28))
                       : worldStage.height - 20)
            radius: 18
            color: "#fffaf0f5"
            border.color: root.hairline
            border.width: 1
            z: 35

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: root.microViewport ? 8
                                 : (root.narrowViewport ? 12 : 17)
                spacing: root.microViewport ? 4
                         : (root.narrowViewport ? 7 : 11)

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            Layout.fillWidth: true
                            visible: !root.narrowViewport
                            text: String(root.growthInfo.stage || root.stageFor(root.resonancePoints))
                                  + " · " + String(root.resonancePoints) + " 点"
                            color: root.ink
                            font.pixelSize: 17
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: root.resonancePoints >= 1200
                                  ? "共鸣已经抵达相伴"
                                  : ("距离下一阶段还需 "
                                     + String(Math.max(0, root.nextThreshold(root.resonancePoints)
                                                       - root.resonancePoints)) + " 点")
                            color: root.mutedInk
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }
                    }
                    Rectangle {
                        Layout.preferredWidth: root.microViewport ? 42 : 48
                        Layout.preferredHeight: root.microViewport ? 26 : 30
                        radius: 9
                        color: "#f0e5d8"
                        Text {
                            anchors.centerIn: parent
                            text: String(root.placedCount) + "/" + String(root.totalCount)
                            color: root.cord
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                        }
                    }
                }

                ProgressBar {
                    id: worldResonanceProgress
                    objectName: "boxWorldResonanceProgress"
                    Layout.fillWidth: true
                    from: 0
                    to: 1
                    value: root.resonanceProgress
                    background: Rectangle { radius: 4; color: "#e9dfd2" }
                    contentItem: Item {
                        implicitHeight: 8
                        Rectangle {
                            width: parent.width * worldResonanceProgress.visualPosition
                            height: parent.height
                            radius: 4
                            color: root.cord
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: !root.narrowViewport
                    text: "盒中陈设"
                    color: root.ink
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                }

                Flickable {
                    id: objectFlick
                    objectName: "boxWorldObjectList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    visible: !root.narrowViewport
                    clip: true
                    contentWidth: width
                    contentHeight: objectColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {
                        id: objectScrollBar
                        policy: ScrollBar.AsNeeded
                        width: 7
                        contentItem: Rectangle {
                            implicitWidth: 5
                            radius: width / 2
                            color: objectScrollBar.pressed ? root.cord : "#a9937a"
                            opacity: objectScrollBar.active ? 0.78 : 0.42
                        }
                        background: Rectangle {
                            implicitWidth: 7
                            color: "transparent"
                        }
                    }

                    Column {
                        id: objectColumn
                        width: objectFlick.width - (objectFlick.contentHeight > objectFlick.height ? 10 : 0)
                        spacing: 7
                        Repeater {
                            model: root.boxWorldObjects
                            delegate: Rectangle {
                                id: objectRow
                                required property var modelData
                                objectName: "boxWorldObjectState_" + root.objectId(modelData)
                                width: objectColumn.width
                                height: rowColumn.implicitHeight + 18
                                radius: 10
                                color: Boolean(modelData.placed) ? "#eef5f0"
                                       : (Boolean(modelData.unlocked) ? "#fbf3e8" : "#f5f0e9")
                                border.color: Boolean(modelData.placed) ? "#aac2b5" : "#ded0bf"

                                RowLayout {
                                    anchors.fill: parent
                                    anchors.margins: 9
                                    spacing: 9
                                    Rectangle {
                                        Layout.preferredWidth: 8
                                        Layout.preferredHeight: 8
                                        radius: 4
                                        color: Boolean(objectRow.modelData.placed) ? root.calm
                                               : (Boolean(objectRow.modelData.unlocked) ? "#b48a5e" : "#cfc3b5")
                                    }
                                    ColumnLayout {
                                        id: rowColumn
                                        Layout.fillWidth: true
                                        spacing: 3
                                        Text {
                                            Layout.fillWidth: true
                                            text: root.objectName(objectRow.modelData)
                                            color: root.ink
                                            font.pixelSize: 12
                                            font.weight: Font.Medium
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: Boolean(objectRow.modelData.placed) ? "已摆放在空间中"
                                                  : (Boolean(objectRow.modelData.unlocked) ? "已解锁 · 尚未摆放"
                                                     : root.objectHint(objectRow.modelData))
                                            color: root.mutedInk
                                            font.pixelSize: 10
                                            wrapMode: Text.WordWrap
                                            lineHeight: 1.24
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    visible: root.narrowViewport && !root.microViewport
                    text: String(root.placedCount) + " 件正在空间中 · "
                          + String(Math.max(0, root.unlockedCount - root.placedCount))
                          + " 件待摆放"
                    color: root.mutedInk
                    font.pixelSize: 11
                    elide: Text.ElideRight
                }

                PaperButton {
                    id: manageButton
                    objectName: "boxWorldManageDecorationsButton"
                    Layout.fillWidth: true
                    Layout.preferredHeight: root.microViewport ? 34 : 42
                    text: root.unlockedCount > root.placedCount
                          ? ("管理陈设 · " + String(root.unlockedCount - root.placedCount) + " 件待摆放")
                          : "管理陈设"
                    accent: true
                    onClicked: root.manageDecorationsRequested()
                }
            }
        }
    }

    Rectangle {
        id: footer
        objectName: "boxWorldSceneFooter"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: root.microViewport ? 28 : 32
        color: "#2a241fdd"
        z: 40
        Text {
            anchors.centerIn: parent
            width: parent.width - 30
            text: "陈设只记录共同经历，不会改变系统权限。"
            color: "#e8ddce"
            opacity: 0.82
            font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }
}
