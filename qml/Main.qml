import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Dialogs

Window {
    id: desktop
    property var savedBoxLayout: backend.boxLayout()
    property var themePalette: backend.themeManifest.palette || ({})
    property var themeDock: backend.themeManifest.dock || ({})
    property color paperColor: themePalette.paper || "#eee6d8"
    property color paperLightColor: themePalette.paperLight || "#fffdf7"
    property color surfaceColor: themePalette.surface || "#fffdf8"
    property color surfaceRaisedColor: themePalette.surfaceRaised || "#fffaf0"
    property color inkColor: themePalette.ink || "#4d4a45"
    property color mutedColor: themePalette.muted || "#8e887e"
    property color hairlineColor: themePalette.hairline || "#d4c6b3"
    property color focusColor: themePalette.focus || "#6f817c"
    property color cordColor: themePalette.cord || "#9f3129"
    readonly property real compactMinimumSize: 110
    readonly property real compactMaximumSize: 320
    readonly property real compactEmergencyMinimumSize: 48
    property real preferredCompactBoxSize: Math.max(compactMinimumSize,
        Math.min(compactMaximumSize, Number(savedBoxLayout.size || 184)))
    property real compactBoxSize: preferredCompactBoxSize
    width: Screen.width
    height: Screen.height
    x: Screen.virtualX
    y: Screen.virtualY
    visible: !diagnosticWindowProbe && backend.shellMode !== "compact"
    color: paperLightColor
    title: "Lilies in the box"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
           | (backend.previewMode ? Qt.WindowStaysOnTopHint : Qt.WindowStaysOnBottomHint)

    property real sceneBreath: 0
    property bool dockRaised: resonanceDock.dockRaised
    property int renderedFrames: 0
    property bool compactExpanded: compactWindow.expanded
    property real compactCharacterLeft: compactLilith.figureLeft
    property real compactCharacterTop: compactLilith.figureTop
    property real compactCharacterWidth: compactLilith.figureWidth
    property real compactCharacterHeight: compactLilith.figureHeight
    property real compactAccessoryLeft: compactBox.x
    property real compactAccessoryTop: compactBox.y
    property real compactAccessoryWidth: compactBox.width
    property int compactActionCount: actionRepeater.count
    property bool compactActionsVisible: compactWindow.orbitProgress > 0.95 && actionRepeater.count >= 3
    property real compactPetBreathScaleX: 1.0 + Math.sin(compactLilith.motionPhase + 2.15) * 0.004
    property real compactPetBreathScaleY: 1.0 + Math.sin(compactLilith.motionPhase + 2.15) * 0.0015
    property bool compactResizeHandleVisible: petResizeHandle.visible
    property bool paperDockRaised: resonanceDock.dockRaised
    property real paperDockWidth: resonanceDock.dockWindowWidth
    property real paperDockHeight: resonanceDock.dockWindowHeight
    property int selectionBubbleActionCount: selectionActionRepeater.count
    readonly property bool desktopVideoLoaded: desktopVideoLoader.item !== null
    readonly property bool desktopSceneLoaded: desktopSceneLoader.item !== null
    readonly property string desktopVideoPlaybackState: desktopVideoLoader.item
                                                         ? desktopVideoLoader.item.playerState
                                                         : "unloaded"
    property int chatPresentationToken: 0
    // Injected by app.py after QML construction.  It carries window-state
    // evidence only; no desktop pixels, titles, or user content cross it.
    property var nativeDesktopPresentationController: null
    property bool desktopPresentationPending: false
    property int desktopPresentationReplayCount: 0
    property int desktopPresentationDispatchCount: 0
    readonly property string petPresenceState: String((backend.habitatState || {}).state || "desktop")
    readonly property string petPresenceReason: String((backend.habitatState || {}).reason || "")
    readonly property bool petPresenceVisible: (backend.habitatState || {}).visible !== false
    readonly property bool petPresenceFocusActive: Boolean((backend.focusStatus || {}).active)
    readonly property bool petPresenceFocusPaused: petPresenceFocusActive
                                                   && Boolean((backend.focusStatus || {}).paused)
    readonly property bool petPresenceSuppressed: !petPresenceVisible
                                                   || petPresenceState === "silent"
                                                   || petPresenceState === "blocked"
    readonly property string petPresenceStatusText: {
        var state = petPresenceState
        var focusSuffix = petPresenceFocusPaused
                ? "，专注仍保持暂停"
                : petPresenceFocusActive
                ? "，专注计时仍在后台继续"
                : ""
        if (state === "silent" || petPresenceReason === "full-screen")
            return "当前 · 全屏界面中保持静默；离开全屏后莉莉丝会自动回来" + focusSuffix
        if (state === "blocked" || petPresenceReason === "sensitive-window")
            return "当前 · 受保护或敏感界面中暂时隐藏；离开后莉莉丝会自动回来" + focusSuffix
        if (state === "avoiding")
            return "当前 · 正在避开鼠标，仍会留在当前窗口附近"
        if (state === "attached") {
            var sizeLabels = {
                "tiny": "极小窗口",
                "small": "小窗口",
                "medium": "中等窗口",
                "large": "大窗口"
            }
            var sizeClass = String((backend.habitatState || {}).windowSizeClass || "")
            var targetLabel = sizeLabels[sizeClass] || "当前窗口"
            return "当前 · 已贴靠 " + targetLabel + "，移动窗口时会平滑跟随"
        }
        if (state === "waiting")
            return "当前 · 正在等待窗口稳定，稍后会安静贴靠"
        if (state === "detached")
            return "当前 · 已按你的拖动自由站立；切换应用后会重新寻找栖息点"
        if (state === "desktop" && petPresenceReason === "host-unavailable")
            return "当前 · 当前窗口暂不可用，莉莉丝保持原位"
        if (state === "desktop" && petPresenceReason === "no-host")
            return "当前 · 莉莉丝正在桌面安静停驻"
        if (!petPresenceVisible)
            return "当前 · 暂时隐藏" + focusSuffix
        return "当前 · 莉莉丝已显示，可以自由站立"
    }
    readonly property color petPresenceStatusColor:
        petPresenceState === "silent" ? "#8b6b58"
        : petPresenceState === "blocked" ? "#9f3129"
        : petPresenceState === "avoiding" ? "#866840"
        : "#4c7466"

    // The native/default Qt Quick Controls style is not deterministic on the
    // software/offscreen renderer used by both recovery mode and release QA.
    // In particular it can paint buttons and scrollbars as opaque black slabs.
    // Keep these small paper controls local to the box/settings surfaces so we
    // do not change the appearance of unrelated full-screen scene controls.
    component LiliesPaperButton: Button {
        id: paperButton
        property bool selected: false
        implicitWidth: Math.max(48, paperButtonText.implicitWidth + 24)
        implicitHeight: 34
        leftPadding: 12
        rightPadding: 12
        font.family: "Microsoft YaHei UI"
        font.pixelSize: 12
        contentItem: Text {
            id: paperButtonText
            text: paperButton.text
            color: !paperButton.enabled ? "#aaa198"
                   : paperButton.selected ? desktop.cordColor : desktop.inkColor
            font: paperButton.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 9
            color: paperButton.flat && !paperButton.hovered && !paperButton.selected
                   ? "transparent"
                   : paperButton.down ? "#eee5d9"
                   : paperButton.selected ? "#f1e7dc"
                   : paperButton.hovered ? "#f7efe5" : "#fffaf2"
            border.width: paperButton.flat && !paperButton.hovered
                          && !paperButton.selected ? 0 : 1
            border.color: paperButton.selected || paperButton.hovered
                          ? desktop.cordColor : desktop.hairlineColor
            opacity: paperButton.enabled ? 1.0 : 0.62
        }
    }

    component LiliesPaperScrollBar: ScrollBar {
        id: paperScrollBar
        implicitWidth: 9
        implicitHeight: 9
        padding: 1
        contentItem: Rectangle {
            implicitWidth: 7
            implicitHeight: 7
            radius: 4
            color: paperScrollBar.pressed ? desktop.cordColor : "#9f9385"
            opacity: paperScrollBar.active ? 0.74 : 0.36
        }
        background: Rectangle {
            color: "#eee6da"
            radius: 4
            opacity: paperScrollBar.active ? 0.52 : 0.22
        }
    }

    onCompactBoxSizeChanged: backend.setCompactPetEffectiveSize(compactBoxSize)
    onDesktopSceneLoadedChanged: Qt.callLater(desktop.reportRuntimeSceneState)
    onDesktopVideoLoadedChanged: Qt.callLater(desktop.reportRuntimeSceneState)
    onDesktopVideoPlaybackStateChanged: Qt.callLater(desktop.reportRuntimeSceneState)

    function reportRuntimeSceneState() {
        backend.reportSceneRuntimeState(
                    desktopSceneLoaded,
                    desktopVideoLoaded,
                    desktopVideoPlaybackState)
    }

    function finiteCoordinate(value, fallbackValue) {
        var numeric = Number(value)
        return isFinite(numeric) ? numeric : Number(fallbackValue)
    }

    function workAreaAt(globalX, globalY) {
        var value = backend.screenWorkAreaAt(Number(globalX), Number(globalY)) || ({})
        var left = Number(value.left)
        var top = Number(value.top)
        var width = Number(value.width)
        var height = Number(value.height)
        if (!isFinite(left) || !isFinite(top) || !isFinite(width)
                || !isFinite(height) || width <= 0 || height <= 0) {
            return {
                "left": Screen.virtualX,
                "top": Screen.virtualY,
                "right": Screen.virtualX + Screen.width,
                "bottom": Screen.virtualY + Screen.height,
                "width": Screen.width,
                "height": Screen.height
            }
        }
        return {
            "left": left,
            "top": top,
            "right": left + width,
            "bottom": top + height,
            "width": width,
            "height": height
        }
    }

    function fittedCompactSize(candidate, area) {
        var heightLimit = (Number(area.height) - 16) / 3.30
        var widthLimit = (Number(area.width) - 16) / 3.50
        var screenMaximum = Math.min(compactMaximumSize, heightLimit, widthLimit)
        if (!isFinite(screenMaximum) || screenMaximum <= 0)
            screenMaximum = compactMinimumSize
        screenMaximum = Math.max(compactEmergencyMinimumSize, screenMaximum)
        var areaMinimum = Math.min(compactMinimumSize, screenMaximum)
        var requested = Number(candidate)
        if (!isFinite(requested))
            requested = preferredCompactBoxSize
        return Math.max(areaMinimum, Math.min(screenMaximum, requested))
    }

    function clampPetToArea(area, allowEdgePeek) {
        var previousClampState = petWindow.geometryClampActive
        petWindow.geometryClampActive = true
        var petWidth = compactBoxSize * 3.50
        var petHeight = compactBoxSize * 3.30
        var edgeAllowance = allowEdgePeek ? petWidth * 0.30 : 0
        var minimumX = Number(area.left) + 8 - edgeAllowance
        var maximumX = Number(area.right) - petWidth - 8 + edgeAllowance
        var minimumY = Number(area.top) + 8
        var maximumY = Number(area.bottom) - petHeight - 8
        petWindow.x = maximumX < minimumX ? Number(area.left)
                                          : Math.max(minimumX, Math.min(petWindow.x, maximumX))
        petWindow.y = maximumY < minimumY ? Number(area.top)
                                          : Math.max(minimumY, Math.min(petWindow.y, maximumY))
        petWindow.geometryClampActive = previousClampState
    }

    function clampDraggedFigureToArea(area, allowPeek) {
        // The interactive window also contains transparent breathing room,
        // the radial menu and the detached box.  Clamping that entire canvas
        // after a drag can shove the visible character hundreds of pixels
        // away from the released pointer.  Only the rendered figure needs to
        // remain recoverable; menu buttons already place themselves inside
        // the current work-area intersection.
        var figureLeft = Number(compactLilith.figureLeft)
        var figureTop = Number(compactLilith.figureTop)
        var figureWidth = Number(compactLilith.figureWidth)
        var figureHeight = Number(compactLilith.figureHeight)
        if (!isFinite(figureLeft) || !isFinite(figureTop)
                || !isFinite(figureWidth) || !isFinite(figureHeight)
                || figureWidth < 1 || figureHeight < 1) {
            clampPetToArea(area, false)
            return
        }
        var previousClampState = petWindow.geometryClampActive
        petWindow.geometryClampActive = true
        var margin = 8
        var allowanceX = allowPeek
                ? Math.min(figureWidth * 0.22, Math.max(0, figureWidth - 56)) : 0
        var allowanceY = allowPeek
                ? Math.min(figureHeight * 0.16, Math.max(0, figureHeight - 72)) : 0
        var minimumX = Number(area.left) + margin - figureLeft - allowanceX
        var maximumX = Number(area.right) - margin
                       - figureLeft - figureWidth + allowanceX
        var minimumY = Number(area.top) + margin - figureTop - allowanceY
        var maximumY = Number(area.bottom) - margin
                       - figureTop - figureHeight + allowanceY
        petWindow.x = maximumX < minimumX
                ? Number(area.left) + (Number(area.width) - petWindow.width) / 2
                : Math.max(minimumX, Math.min(petWindow.x, maximumX))
        petWindow.y = maximumY < minimumY
                ? Number(area.top) + (Number(area.height) - petWindow.height) / 2
                : Math.max(minimumY, Math.min(petWindow.y, maximumY))
        petWindow.geometryClampActive = previousClampState
    }

    function constrainCompactPet(saveLayout) {
        var centerX = petWindow.x + petWindow.width / 2
        var centerY = petWindow.y + petWindow.height / 2
        var area = workAreaAt(centerX, centerY)
        compactBoxSize = fittedCompactSize(preferredCompactBoxSize, area)
        clampPetToArea(area, false)
        if (saveLayout)
            persistCompactLayout()
    }

    function persistCompactLayout() {
        backend.saveBoxLayout(petWindow.x, petWindow.y, preferredCompactBoxSize)
        // saveBoxLayout intentionally restores the persisted 110..320
        // preference inside the habitat controller.  Re-apply the smaller
        // effective size when this monitor temporarily requires one.
        backend.setCompactPetEffectiveSize(compactBoxSize)
    }

    function scheduleCompactLayoutPersistence() {
        compactLayoutPersistTimer.restart()
    }

    // Keep the two-argument entry point stable: Python/QMetaObject callers do
    // not apply JavaScript's optional-argument semantics.
    function resizeCompactPet(delta, persistDeferred) {
        resizeCompactPetForDrag(delta, persistDeferred, true,
                                Number.NaN, Number.NaN)
    }

    function resizeCompactPetForDrag(delta, persistDeferred, clampDuringResize,
                                     interactionGlobalX, interactionGlobalY) {
        var areaX = Number(interactionGlobalX)
        var areaY = Number(interactionGlobalY)
        if (!isFinite(areaX))
            areaX = petWindow.x + petWindow.width / 2
        if (!isFinite(areaY))
            areaY = petWindow.y + petWindow.height / 2
        // During a held resize the handle can enter another monitor before
        // the much larger window centre does.  Fit against the pointer's
        // screen so a mixed-DPI seam cannot resize one frame late.
        var area = workAreaAt(areaX, areaY)
        var desired = compactBoxSize + Number(delta)
        preferredCompactBoxSize = Math.max(compactMinimumSize,
            Math.min(compactMaximumSize, desired))
        compactBoxSize = fittedCompactSize(preferredCompactBoxSize, area)
        // A held resize handle has its own pointer-preserving window
        // reposition below.  Clamping the much larger transparent canvas on
        // every move breaks that grab offset and is the main reason the
        // handle used to race away from the cursor near a monitor edge.
        if (clampDuringResize === undefined || Boolean(clampDuringResize))
            clampPetToArea(area, false)
        if (persistDeferred)
            scheduleCompactLayoutPersistence()
    }

    function openWorkPanel(sectionName) {
        backend.openWorkPanelSection(String(sectionName || "work"))
    }

    function openConnectorSetup(providerName) {
        connectorSetup.provider = providerName === "slack" ? "slack" : "calendar"
        connectorSetup.requestedVisible = true
        if (!backend.dockSuppressed) {
            connectorSetup.raise()
            connectorSetup.requestActivate()
        }
    }

    function cancelDesktopPresentationProbe(recoverRemap) {
        var recover = recoverRemap === undefined ? true : Boolean(recoverRemap)
        if (nativeDesktopPresentationController
                && typeof nativeDesktopPresentationController.cancelPending === "function")
            nativeDesktopPresentationController.cancelPending(recover)
    }

    function queueCurrentSurfacePresentation() {
        // A boolean deliberately coalesces repeated shell/activation signals
        // into one replay when privacy/full-screen suppression ends.
        desktopPresentationPending = true
        cancelDesktopPresentationProbe()
    }

    function presentDesktopSurfaceStep() {
        if (visibility === Window.Minimized)
            visibility = Window.Windowed
        show()
        raise()
        if (backend.petFloatMode === "always") {
            petWindow.show()
            petWindow.raise()
        }
        return visible && visibility !== Window.Minimized
    }

    function ensureDesktopSurface() {
        // The full desktop is a bottom-band top-level window: it must stay
        // above Explorer's wallpaper while every ordinary application remains
        // above it.  Qt can retain a hidden native surface after an installer
        // restart, an Explorer WorkerW rebuild or a visual/compact round-trip.
        // Re-present it only at explicit lifecycle boundaries; a polling
        // raise would continuously disturb the desktop z-order.
        if (diagnosticWindowProbe || backend.shellMode === "compact") {
            cancelDesktopPresentationProbe(false)
            return false
        }
        if (backend.dockSuppressed) {
            queueCurrentSurfacePresentation()
            return false
        }
        desktopPresentationPending = false
        var presented = presentDesktopSurfaceStep()
        if (nativeDesktopPresentationController
                && typeof nativeDesktopPresentationController.requestPresentation === "function") {
            desktopPresentationDispatchCount += 1
            nativeDesktopPresentationController.requestPresentation()
        }
        return presented
    }

    function probeDesktopSurfaceHealth() {
        // WorkerW can be rebuilt and Show Desktop can minimize a bottom-band
        // window without changing shellMode or restarting Explorer.  A quiet
        // bounded native probe closes that event blind spot.  Healthy checks
        // are read-only; only a genuinely hidden/minimized/cloaked surface
        // uses the controller's finite showNormal/remap recovery, and it
        // never requests keyboard focus.
        if (diagnosticWindowProbe
                || backend.shellMode === "compact"
                || backend.dockSuppressed
                || !desktop.visible
                || !nativeDesktopPresentationController
                || typeof nativeDesktopPresentationController.requestPresentation
                   !== "function")
            return false
        desktopPresentationDispatchCount += 1
        nativeDesktopPresentationController.requestPresentation()
        return true
    }

    function replayPendingSurfacePresentation() {
        if (!desktopPresentationPending || diagnosticWindowProbe
                || backend.dockSuppressed)
            return false
        desktopPresentationPending = false
        desktopPresentationReplayCount += 1
        if (backend.shellMode === "compact") {
            cancelDesktopPresentationProbe(false)
            petWindow.show()
            if (backend.petFloatMode === "always")
                petWindow.raise()
            return petWindow.visible
        }
        return ensureDesktopSurface()
    }

    function presentChatWindow(token, attempt) {
        if (token !== chatPresentationToken)
            return
        if (!backend.chatOpen || backend.dockSuppressed) {
            chatWindow.presentationResetHidden = false
            chatWindow.presentationRecoveryArmed = false
            chatWindow.presentationStableChecks = 0
            return
        }
        // A retained Qt.Tool can stay natively minimized even though
        // showNormal() has already been accepted.  When the bounded recovery
        // below temporarily removes the window from the compositor, restore
        // the visibility binding on a separate event turn before asking for
        // the normal state again.  Keeping these as separate turns is what
        // avoids the intermittent "visible but still minimized" dead click.
        if (chatWindow.presentationResetHidden) {
            chatWindow.presentationResetHidden = false
            chatPresentationRetry.token = token
            chatPresentationRetry.attempt = attempt + 1
            chatPresentationRetry.restart()
            return
        }
        var needsRestore = chatWindow.visibility === Window.Minimized
        if (needsRestore && chatWindow.presentationRecoveryArmed) {
            // Clear the QWindow state explicitly before showNormal().  Calling
            // showNormal() alone can leave a retained Qt.Tool visible while
            // its native minimized bit stays set, especially on the first
            // restore after startup.
            chatWindow.presentationStableChecks = 0
            chatWindow.visibility = Window.Windowed
            chatWindow.showNormal()
            chatWindow.raise()
            chatWindow.requestActivate()
        } else if (attempt === 0) {
            // Raising and activating is part of the user's explicit click,
            // never part of the later stability probes.  Repeating it every
            // 80 ms would steal focus back from WPS after the window had
            // already recovered successfully.
            chatWindow.show()
            chatWindow.raise()
            chatWindow.requestActivate()
        } else if (!needsRestore && chatWindow.presentationRecoveryArmed) {
            chatWindow.presentationStableChecks += 1
            // Keep watching long enough to catch Qt's delayed Minimized
            // replay. Windowed checks are read-only, so this extended gate
            // cannot keep stealing focus from WPS.
            if (chatWindow.presentationStableChecks >= 10) {
                chatWindow.presentationRecoveryArmed = false
                return
            }
        } else {
            return
        }
        // Some Windows/Qt.Tool combinations retain the native minimized bit
        // even after several successful-looking showNormal() calls.  Remove
        // the window for one complete event turn; the next retry restores it.
        if (chatWindow.visibility === Window.Minimized
                && (attempt === 4 || attempt === 9)) {
            chatWindow.presentationResetHidden = true
            chatPresentationRetry.token = token
            chatPresentationRetry.attempt = attempt + 1
            chatPresentationRetry.restart()
            return
        }
        // On Windows a retained Qt.Tool can acknowledge showNormal() before
        // its native visibility leaves Minimized, or briefly report Windowed
        // before reverting.  The later passes are read-only while Windowed;
        // they never keep raising an already recovered window.
        if (chatWindow.presentationRecoveryArmed && attempt < 24) {
            chatPresentationRetry.token = token
            chatPresentationRetry.attempt = attempt + 1
            chatPresentationRetry.restart()
        }
    }

    function presentChatPage(pageIndex) {
        chatWindow.page = Math.max(0, Number(pageIndex || 0))
        chatWindow.presentationRecoveryArmed =
                chatWindow.visibility === Window.Minimized
        chatWindow.presentationStableChecks = 0
        chatWindow.presentationResetHidden = false
        backend.setChatOpen(true)
        chatPresentationToken += 1
        var token = chatPresentationToken
        Qt.callLater(function() { desktop.presentChatWindow(token, 0) })
    }

    function toggleChatPresentation() {
        if (backend.chatOpen
                && chatWindow.visible
                && chatWindow.visibility !== Window.Minimized) {
            backend.setChatOpen(false)
            return
        }
        presentChatPage(chatWindow.page)
    }

    function requestCurrentWindowObservation() {
        // Let the user's previous application regain foreground before the
        // guarded one-shot capture resolves its active-window context.
        backend.setChatOpen(false)
        companionScreenObservationDelay.restart()
    }

    function activateQuickAction(actionId) {
        var action = String(actionId || "")
        if (action === "peek") {
            // In visual mode the first half of peek must reveal the Lilies
            // desktop before the user's application windows are minimized.
            // Otherwise a stale hidden bottom surface looks like the feature
            // vanished even though the peek transaction itself succeeded.
            if (backend.shellMode !== "compact")
                ensureDesktopSurface()
            backend.toggleDesktopPeek()
        } else if (action === "lilies-desktop") {
            backend.toggleDesktopShell()
        } else if (action === "chat") {
            if (Number(backend.companionService.deliveryStatus.unreadCount || 0) > 0) {
                if (!backend.companionService.reopenUnread())
                    presentChatPage(3)
            } else {
                presentChatPage(0)
            }
        } else if (action === "world") {
            backend.enterBoxWorld()
        } else if (action === "settings") {
            presentChatPage(4)
        } else if (action === "companion") {
            // “陪伴” is an action, not another settings shortcut.  Give an
            // immediate, visible result even when the subscription bridge is
            // offline (the controller supplies its local-safe bubble).  When
            // privacy/pause/busy state rejects it, open the status page so the
            // reason and controls are visible instead of making the click
            // appear dead.
            if (Number(backend.companionService.deliveryStatus.unreadCount || 0) > 0) {
                if (!backend.companionService.reopenUnread())
                    presentChatPage(3)
            } else if (!backend.companionService.requestNow()) {
                presentChatPage(3)
            }
        } else if (action === "letters") {
            desktop.openWorkPanel("connectors")
        } else if (action === "memory") {
            presentChatPage(5)
            backend.refreshMemoryMap(chatWindow.selectedMemoryPartition)
        } else if (action === "wardrobe") {
            desktop.openWorkPanel("wardrobe")
        } else if (action === "focus") {
            desktop.openWorkPanel("focus")
        } else if (action === "reading") {
            desktop.openWorkPanel("reading")
        } else if (action === "work") {
            desktop.openWorkPanel("work")
        }
    }

    Timer {
        id: chatPresentationRetry
        // Leave a full native-state reconciliation gap between checks.
        interval: 80
        repeat: false
        property int token: 0
        property int attempt: 0
        onTriggered: desktop.presentChatWindow(token, attempt)
    }

    Timer {
        id: companionScreenObservationDelay
        objectName: "companionScreenObservationDelay"
        interval: 350
        repeat: false
        onTriggered: backend.companionService.requestScreenNow()
    }

    onFrameSwapped: renderedFrames += 1

    onClosing: function(close) {
        close.accepted = false
        backend.setShellMode("compact")
    }

    SequentialAnimation on sceneBreath {
        loops: Animation.Infinite
        // The full-desktop pulse must stop with the full-desktop window.  The
        // compact pet owns a separately paced breath below; keeping this 60 Hz
        // animation alive merely because petWindow was visible made an idle
        // desktop pet continuously render the hidden desktop scene as well.
        running: desktop.visible && backend.sceneActive
        NumberAnimation { from: 0; to: 1; duration: 1450; easing.type: Easing.InOutSine }
        NumberAnimation { from: 1; to: 0; duration: 1450; easing.type: Easing.InOutSine }
    }

    Loader {
        id: desktopVideoLoader
        objectName: "desktopVideoLoader"
        anchors.fill: parent
        active: backend.shellMode !== "compact"
                && backend.renderer === "video"
                && backend.assetUrl("video") !== ""
        asynchronous: false
        source: Qt.resolvedUrl("CinematicDesktopVideo.qml")
        onLoaded: {
            item.appBackend = backend
            Qt.callLater(item.synchronizePlayback)
        }
    }

    Connections {
        target: backend
        function onShellModeChanged() {
            if (backend.shellMode !== "compact")
                Qt.callLater(desktop.ensureDesktopSurface)
            else
                desktop.cancelDesktopPresentationProbe(false)
            if (backend.petFloatMode === "always")
                Qt.callLater(function() { petWindow.raise() })
        }
        function onOpenConversationRequested() {
            desktop.presentChatPage(0)
        }
        function onApplicationActivationRequested(action) {
            if (backend.dockSuppressed) {
                desktop.queueCurrentSurfacePresentation()
                return
            }
            if (backend.shellMode === "compact") {
                petWindow.show()
                petWindow.raise()
            } else {
                Qt.callLater(desktop.ensureDesktopSurface)
            }
        }
        function onHabitatChanged() {
            if (backend.dockSuppressed) {
                desktop.queueCurrentSurfacePresentation()
                return
            }
            if (desktop.desktopPresentationPending)
                Qt.callLater(desktop.replayPendingSurfacePresentation)
        }
    }

    Component.onCompleted: {
        Qt.callLater(function() { desktop.constrainCompactPet(true) })
        Qt.callLater(desktop.reportRuntimeSceneState)
        if (backend.shellMode !== "compact")
            Qt.callLater(desktop.ensureDesktopSurface)
    }

    Timer {
        id: compactLayoutPersistTimer
        interval: 220
        repeat: false
        onTriggered: desktop.persistCompactLayout()
    }

    Timer {
        interval: 1000
        running: desktop.visible
        repeat: true
        onTriggered: {
            backend.reportFrameRate(desktop.renderedFrames)
            desktop.renderedFrames = 0
            backend.refreshSceneActivity()
        }
    }

    Timer {
        id: desktopPresentationHealthTimer
        objectName: "desktopPresentationHealthTimer"
        interval: 5000
        repeat: true
        running: !diagnosticWindowProbe
                 && backend.shellMode !== "compact"
                 && desktop.visible
                 && !backend.dockSuppressed
        onTriggered: desktop.probeDesktopSurfaceHealth()
    }

    Loader {
        id: desktopSceneLoader
        objectName: "desktopSceneLoader"
        anchors.fill: parent
        active: backend.shellMode !== "compact"
                && (backend.renderer === "scene2d" || backend.assetUrl("video") === "")
        asynchronous: false
        sourceComponent: Component {
            Item {
                id: liveScene
                objectName: "desktopScene"
                anchors.fill: parent

                Image {
                    anchors.fill: parent
                    source: backend.assetUrl("background")
                    cache: false
                    fillMode: Image.PreserveAspectCrop
                    smooth: true
                    mipmap: true
                    opacity: 0.82
                }

        Image {
            anchors.fill: parent
            source: backend.assetUrl("cartonBack")
            cache: false
            fillMode: Image.Stretch
            smooth: true
            opacity: 0.74
        }

        Rectangle {
            anchors.fill: parent
            color: "#f9f5ed"
            opacity: 0.24
        }

        Image {
            anchors.fill: parent
            source: backend.assetUrl("crackGlow")
            cache: false
            fillMode: Image.Stretch
            smooth: true
            opacity: 0.22 + desktop.sceneBreath * 0.18
        }

        Repeater {
            model: 34
            Rectangle {
                required property int index
                property real startX: ((index * 83) % 997) / 997 * desktop.width
                property real durationValue: 6200 + (index % 9) * 730
                x: startX + Math.sin(y / 120 + index) * 13
                y: desktop.height + 30
                width: 2 + index % 3
                height: width
                radius: width / 2
                color: index % 11 === 0 ? "#d8f9f6" : "#ffffff"
                opacity: 0.18 + (index % 5) * 0.05
                SequentialAnimation on y {
                    running: liveScene.visible && backend.sceneActive
                    loops: Animation.Infinite
                    PauseAnimation { duration: (index % 7) * 360 }
                    NumberAnimation { from: desktop.height + 30; to: -40; duration: durationValue; easing.type: Easing.Linear }
                }
            }
        }

        Image {
            anchors.fill: parent
            source: backend.assetUrl("cartonForeground")
            cache: false
            fillMode: Image.Stretch
            smooth: true
            opacity: 0.70
            z: 20
        }

        Item {
            id: impossibleCarton
            width: Math.min(desktop.width * 0.44, 740)
            height: Math.min(desktop.height * 0.48, 520)
            anchors.right: parent.right
            anchors.rightMargin: Math.max(90, desktop.width * 0.075)
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 100

            Rectangle {
                id: innerVoid
                visible: false
                anchors.centerIn: parent
                width: parent.width * 0.76
                height: parent.height * 0.70
                radius: 34
                color: "#fffefa"
                border.color: "#d4ccbe"
                border.width: 2
                layer.enabled: true
                opacity: 0.82

                Rectangle {
                    anchors.centerIn: parent
                    width: parent.width * (0.78 + desktop.sceneBreath * 0.04)
                    height: parent.height * (0.72 + desktop.sceneBreath * 0.04)
                    radius: width / 2
                    color: "#e9ffff"
                    opacity: 0.08 + desktop.sceneBreath * 0.08
                }
            }

            Rectangle {
                visible: false
                width: parent.width * 0.84
                height: parent.height * 0.16
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                color: "#d7c5aa"
                border.color: "#aa9678"
                rotation: -4
                opacity: 0.92
            }
            Rectangle {
                visible: false
                width: parent.width * 0.18
                height: parent.height * 0.70
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                color: "#d1bea0"
                border.color: "#aa9678"
                rotation: 2
                opacity: 0.88
            }
            Rectangle {
                visible: false
                width: parent.width * 0.18
                height: parent.height * 0.70
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                color: "#d1bea0"
                border.color: "#aa9678"
                rotation: -2
                opacity: 0.88
            }
            Rectangle {
                visible: false
                width: parent.width * 0.90
                height: parent.height * 0.20
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                color: "#c8b28f"
                border.color: "#9f8867"
                rotation: 1
                opacity: 0.94
            }

            Image {
                id: lilithScene
                objectName: "desktopSceneLilith"
                width: parent.width * 0.58
                height: parent.height * 1.22
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: parent.height * 0.05 + desktop.sceneBreath * 2
                source: backend.assetUrl("lilith")
                cache: false
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true
                opacity: 0.95
                scale: 0.998 + desktop.sceneBreath * 0.004
            }

            Canvas {
                anchors.fill: parent
                opacity: 0.68
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    ctx.strokeStyle = "#9f3129"
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.moveTo(width * 0.50, height * 0.22)
                    ctx.bezierCurveTo(width * 0.66, height * 0.34, width * 0.70, height * 0.52, width * 0.82, height * 0.70)
                    ctx.stroke()
                }
            }

            Rectangle {
                id: sceneBoxCore
                width: Math.min(138, parent.width * 0.19)
                height: width
                radius: width / 2
                x: parent.width * 0.76
                y: parent.height * 0.57
                color: "#fffdf8"
                border.color: "#aaa297"
                border.width: 1.3
                rotation: desktop.sceneBreath * 2
                Rectangle {
                    anchors.centerIn: parent
                    width: parent.width * 0.64
                    height: width
                    radius: width / 2
                    color: "transparent"
                    border.color: "#d8d2c8"
                    border.width: 1
                }
                Rectangle {
                    width: parent.width * 0.06; height: width; radius: width/2
                    x: parent.width * 0.78; y: parent.height * 0.47
                    color: "#9f3129"
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: desktop.presentChatPage(0)
                }
            }
        }
    }
        }
    }

    Item {
        id: iconLayer
        anchors.fill: parent
        anchors.bottomMargin: 92
        visible: backend.shellMode !== "compact" && !backend.introActive

        Repeater {
            model: iconLayer.visible ? backend.desktopItems : []
            delegate: Item {
                id: iconDelegate
                required property var modelData
                width: 92
                height: 100
                x: Math.max(12, Math.min(iconLayer.width - width - 12, Number(modelData.x)))
                y: Math.max(18, Math.min(iconLayer.height - height - 18, Number(modelData.y)))

                Rectangle {
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: 60
                    height: 60
                    radius: 18
                    color: iconMouse.containsMouse ? "#fffdf7" : "#f4eee4"
                    border.color: iconMouse.containsMouse ? "#a99a84" : "#cfc3b2"
                    border.width: 1

                    Text {
                        anchors.centerIn: parent
                        text: modelData.glyph
                        color: modelData.source === "desktop" ? "#9f3129" : "#777168"
                        font.family: "Segoe UI Symbol"
                        font.pixelSize: 25
                    }
                    Rectangle {
                        visible: Boolean(modelData.pinned)
                        width: 9; height: 9; radius: 5
                        color: "#9f3129"
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 5
                    }
                    Text {
                        visible: modelData.group && modelData.group !== "未分组"
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.margins: 5
                        text: modelData.group.substring(0, 1)
                        color: "#887765"
                        font.pixelSize: 10
                    }
                }

                Text {
                    anchors.top: parent.top
                    anchors.topMargin: 66
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: parent.width
                    text: modelData.name
                    color: "#4d4a45"
                    font.family: "Microsoft YaHei UI"
                    font.pixelSize: 12
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    maximumLineCount: 2
                    elide: Text.ElideRight
                }

                MouseArea {
                    id: iconMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    drag.target: iconDelegate
                    drag.minimumX: 4
                    drag.maximumX: iconLayer.width - iconDelegate.width - 4
                    drag.minimumY: 4
                    drag.maximumY: iconLayer.height - iconDelegate.height - 4
                    onDoubleClicked: backend.openItem(modelData.itemId)
                    onReleased: backend.saveIconPosition(modelData.itemId, iconDelegate.x, iconDelegate.y)
                }

                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.RightButton
                    onClicked: iconMenu.popup()
                }

                Menu {
                    id: iconMenu
                    MenuItem {
                        text: modelData.pinned ? "取消固定" : "固定到桌面"
                        onTriggered: backend.setIconPinned(modelData.itemId, !Boolean(modelData.pinned))
                    }
                    MenuItem { text: "打开所在位置"; onTriggered: backend.revealItem(modelData.itemId) }
                    MenuSeparator { }
                    Menu {
                        title: "分组"
                        MenuItem { text: "未分组"; onTriggered: backend.setIconGroup(modelData.itemId, "未分组") }
                        MenuItem { text: "工作"; onTriggered: backend.setIconGroup(modelData.itemId, "工作") }
                        MenuItem { text: "资料"; onTriggered: backend.setIconGroup(modelData.itemId, "资料") }
                        MenuItem { text: "娱乐"; onTriggered: backend.setIconGroup(modelData.itemId, "娱乐") }
                    }
                    MenuItem { text: "从当前布局隐藏"; onTriggered: backend.hideIcon(modelData.itemId) }
                }
            }
        }
    }

    Rectangle {
        id: introOverlay
        anchors.fill: parent
        z: 900
        visible: backend.shellMode !== "compact" && backend.introActive
        color: "#d0b991"
        property real reveal: 0

        Image {
            anchors.fill: parent
            source: introOverlay.visible ? backend.assetUrl("background") : ""
            cache: false
            fillMode: Image.PreserveAspectCrop
            opacity: introOverlay.reveal
        }
        Rectangle {
            anchors.fill: parent
            color: "#fffdf7"
            opacity: introOverlay.reveal * 0.42
        }
        Rectangle {
            id: topFlap
            x: -width * 0.03
            y: -height * 0.08 - introOverlay.reveal * height * 0.70
            width: parent.width * 1.06
            height: parent.height * 0.55
            color: "#cdb58e"
            border.color: "#9f8764"
            rotation: -2 - introOverlay.reveal * 7
        }
        Rectangle {
            x: -width * 0.60 - introOverlay.reveal * width * 0.70
            y: 0
            width: parent.width * 0.62
            height: parent.height
            color: "#d6c19f"
            border.color: "#a58f6d"
            rotation: 1
        }
        Rectangle {
            x: parent.width * 0.98 + introOverlay.reveal * width * 0.70
            y: 0
            width: parent.width * 0.62
            height: parent.height
            color: "#d6c19f"
            border.color: "#a58f6d"
            rotation: -1
        }
        Rectangle {
            anchors.centerIn: parent
            width: 18 + introOverlay.reveal * 460
            height: width
            radius: width / 2
            color: "#f1ffff"
            opacity: 0.02 + introOverlay.reveal * 0.15
        }

        SequentialAnimation {
            running: introOverlay.visible
            PauseAnimation { duration: 550 }
            NumberAnimation { target: introOverlay; property: "reveal"; from: 0; to: 1; duration: 4100; easing.type: Easing.InOutCubic }
            PauseAnimation { duration: 900 }
            ScriptAction { script: backend.completeIntro() }
        }
        MouseArea {
            anchors.fill: parent
            onClicked: backend.completeIntro()
        }
    }

    component DockRoundAction: Rectangle {
        id: dockRoundAction
        property string symbol: ""
        property string hint: ""
        signal activated()
        implicitWidth: 40
        implicitHeight: 40
        radius: 5
        color: dockRoundHover.hovered ? desktop.paperColor : desktop.surfaceColor
        border.color: dockRoundHover.hovered ? desktop.cordColor : desktop.hairlineColor
        border.width: 1
        scale: dockRoundPress.pressed ? 0.94 : 1.0
        Behavior on color { ColorAnimation { duration: 120 } }
        Behavior on scale { NumberAnimation { duration: 100 } }
        Text {
            anchors.centerIn: parent
            text: dockRoundAction.symbol
            color: "#4d4842"
            font.pixelSize: Math.max(15, parent.height * 0.40)
            font.weight: Font.Medium
        }
        Rectangle {
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            width: dockRoundHover.hovered ? parent.width * 0.42 : 6
            height: 2
            color: desktop.cordColor
            opacity: 0.72
            Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        }
        HoverHandler { id: dockRoundHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: dockRoundPress; onTapped: dockRoundAction.activated() }
        ToolTip.visible: dockRoundHover.hovered && dockRoundAction.hint !== ""
        ToolTip.text: dockRoundAction.hint
        ToolTip.delay: 450
    }

    component DockWindowChip: Rectangle {
        id: dockWindowChip
        property string label: ""
        property string hint: label
        signal activated()
        implicitWidth: 132
        implicitHeight: 38
        radius: 5
        color: dockChipHover.hovered ? desktop.paperColor : desktop.surfaceColor
        border.color: dockChipHover.hovered ? desktop.cordColor : desktop.hairlineColor
        border.width: 1
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 7
            Rectangle {
                Layout.preferredWidth: 5
                Layout.preferredHeight: 5
                radius: 3
                color: "#9f3129"
                opacity: 0.78
            }
            Text {
                Layout.fillWidth: true
                text: dockWindowChip.label
                color: "#514c46"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
        }
        HoverHandler { id: dockChipHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: dockWindowChip.activated() }
        ToolTip.visible: dockChipHover.hovered
        ToolTip.text: dockWindowChip.hint
        ToolTip.delay: 450
    }

    Window {
        id: dockWindow
        objectName: "paperDock"
        transientParent: null
        property bool dockRaised: false
        property bool searchOpen: false
        property bool windowShelfOpen: false
        width: dockRaised ? Math.min(Screen.width - 40, Number(desktop.themeDock.expandedMaxWidth || 1080))
                          : Number(desktop.themeDock.collapsedWidth || 150)
        height: dockRaised ? Number(desktop.themeDock.expandedHeight || 68)
                           : Number(desktop.themeDock.collapsedHeight || 12)
        x: Screen.virtualX + (Screen.width - width) / 2
        y: Screen.virtualY + Screen.height - height - 6
        visible: false // replaced by the grouped v0.3 paper-fold Dock below
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        onVisibleChanged: if (!visible) windowShelfOpen = false

        Behavior on width { NumberAnimation { duration: 210; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 210; easing.type: Easing.OutCubic } }
        Behavior on x { NumberAnimation { duration: 210; easing.type: Easing.OutCubic } }
        Behavior on y { NumberAnimation { duration: 210; easing.type: Easing.OutCubic } }

        Rectangle {
            id: dockShadow
            visible: dockWindow.dockRaised
            anchors.fill: dockPaper
            anchors.topMargin: 5
            radius: 7
            color: "#2b2117"
            opacity: 0.10
        }

        Rectangle {
            id: dockPaper
            anchors.fill: parent
            radius: dockWindow.dockRaised ? 0 : 4
            color: dockWindow.dockRaised ? "transparent" : desktop.paperColor
            border.color: desktop.hairlineColor
            border.width: dockWindow.dockRaised ? 0 : 1
            opacity: dockWindow.dockRaised ? 0.97 : 0.82

            Canvas {
                id: dockFoldCanvas
                anchors.fill: parent
                visible: dockWindow.dockRaised
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    var w = width
                    var h = height
                    var middle = w / 2
                    ctx.beginPath()
                    ctx.moveTo(8, 10)
                    ctx.lineTo(middle - 42, 10)
                    ctx.lineTo(middle - 31, 1)
                    ctx.lineTo(middle + 31, 1)
                    ctx.lineTo(middle + 42, 10)
                    ctx.lineTo(w - 8, 10)
                    ctx.lineTo(w - 1, 18)
                    ctx.lineTo(w - 1, h - 1)
                    ctx.lineTo(1, h - 1)
                    ctx.lineTo(1, 18)
                    ctx.closePath()
                    ctx.fillStyle = desktop.surfaceRaisedColor.toString()
                    ctx.fill()
                    ctx.strokeStyle = desktop.hairlineColor.toString()
                    ctx.lineWidth = 1
                    ctx.stroke()
                }
            }

            Rectangle {
                anchors.top: parent.top
                anchors.horizontalCenter: parent.horizontalCenter
                width: dockWindow.dockRaised ? 54 : 38
                height: 2
                radius: 1
                color: "#9f3129"
                opacity: 0.62
                y: dockWindow.dockRaised ? 7 : 3
                Behavior on width { NumberAnimation { duration: 180 } }
            }

            Text {
                visible: !dockWindow.dockRaised
                anchors.centerIn: parent
                anchors.verticalCenterOffset: 3
                text: "⌃"
                color: "#756b60"
                font.pixelSize: 10
            }

            RowLayout {
                visible: dockWindow.dockRaised
                opacity: dockWindow.dockRaised ? 1 : 0
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                anchors.topMargin: 15
                anchors.bottomMargin: 6
                spacing: 8

                DockRoundAction {
                    symbol: "⌕"
                    hint: "搜索应用与桌面文件"
                    onActivated: {
                        dockWindow.searchOpen = !dockWindow.searchOpen
                        if (dockWindow.searchOpen) appSearch.forceActiveFocus()
                    }
                }
                TextField {
                    id: appSearch
                    visible: dockWindow.searchOpen
                    Layout.preferredWidth: 205
                    Layout.preferredHeight: 40
                    placeholderText: "搜索应用与文件"
                    color: "#4d4842"
                    font.pixelSize: 13
                    leftPadding: 13
                    rightPadding: 13
                    selectByMouse: true
                    background: Rectangle {
                        radius: 13
                        color: "#fffdf9"
                        border.color: appSearch.activeFocus ? "#a99d8d" : "#ddd3c5"
                        border.width: 1
                    }
                    onTextChanged: backend.searchIcons(text)
                    onEditingFinished: if (text === "") dockWindow.searchOpen = false
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 30
                    color: "#d8ccbd"
                    opacity: 0.8
                }

                Repeater {
                    model: backend.pinnedItems
                    delegate: DockRoundAction {
                        required property var modelData
                        symbol: modelData.glyph || modelData.name.charAt(0).toUpperCase()
                        hint: "已固定 · " + modelData.name
                        onActivated: backend.openItem(modelData.itemId)
                    }
                }

                Repeater {
                    model: backend.windowItems.slice(0, 4)
                    delegate: DockWindowChip {
                        required property var modelData
                        label: modelData.title
                        hint: modelData.title
                        onActivated: backend.activateWindow(Number(modelData.handle))
                    }
                }

                DockRoundAction {
                    visible: backend.windowItems.length > 4
                    implicitWidth: 50
                    symbol: "+" + String(Math.max(0, backend.windowItems.length - 4))
                    hint: "查看全部 " + String(backend.windowItems.length) + " 个窗口"
                    onActivated: {
                        dockWindow.windowShelfOpen = !dockWindow.windowShelfOpen
                        if (dockWindow.windowShelfOpen)
                            Qt.callLater(function() { windowShelf.requestActivate() })
                    }
                }

                Item { Layout.fillWidth: true; Layout.minimumWidth: 8 }

                RowLayout {
                    spacing: 7
                    Rectangle {
                        Layout.preferredWidth: 7
                        Layout.preferredHeight: 7
                        radius: 4
                        color: backend.systemStatus.online ? "#5d8878" : "#9f3129"
                    }
                    Text {
                        text: backend.systemStatus.battery
                        color: "#746c63"
                        font.pixelSize: 12
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 30
                    color: "#d8ccbd"
                    opacity: 0.8
                }

                DockRoundAction {
                    symbol: "◎"
                    hint: "打开莉莉丝的盒子"
                    onActivated: desktop.toggleChatPresentation()
                }
                DockRoundAction {
                    symbol: "⋯"
                    hint: "系统抽屉"
                    onActivated: systemMenu.open()
                }
                DockRoundAction {
                    symbol: "⌄"
                    hint: "收成桌宠"
                    onActivated: backend.setShellMode("compact")
                }
                ColumnLayout {
                    spacing: -2
                    Text {
                        id: clockText
                        property date now: new Date()
                        text: Qt.formatDateTime(now, "hh:mm")
                        color: "#49443e"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        Timer {
                            interval: 1000
                            running: dockWindow.visible
                            repeat: true
                            onTriggered: clockText.now = new Date()
                        }
                    }
                    Text {
                        text: Qt.formatDateTime(clockText.now, "MM/dd")
                        color: "#8b8278"
                        font.pixelSize: 10
                    }
                }
            }
        }

        Menu {
            id: systemMenu
            y: -height
            MenuItem { text: "网络设置"; onTriggered: backend.openSystemSettings("network") }
            MenuItem { text: "声音设置"; onTriggered: backend.openSystemSettings("sound") }
            MenuItem { text: "通知中心设置"; onTriggered: backend.openSystemSettings("notifications") }
            MenuItem { text: "显示设置"; onTriggered: backend.openSystemSettings("display") }
            MenuSeparator { }
            MenuItem { text: "临时显示 Windows 系统栏"; onTriggered: backend.revealSystemDrawer() }
        }

        MouseArea {
            id: dockPresenceArea
            anchors.fill: parent
            acceptedButtons: Qt.NoButton
            hoverEnabled: true
            onEntered: {
                dockCollapseTimer.stop()
                dockWindow.dockRaised = true
            }
            onExited: dockCollapseTimer.restart()
        }
        Timer {
            id: dockCollapseTimer
            interval: 850
            repeat: false
            onTriggered: {
                if (!appSearch.activeFocus && !systemMenu.visible && !windowShelf.visible) {
                    dockWindow.searchOpen = false
                    dockWindow.dockRaised = false
                }
            }
        }
        Timer {
            interval: 4000
            running: dockWindow.visible
            repeat: true
            onTriggered: backend.refreshWindows()
        }
        Timer {
            interval: 10000
            running: dockWindow.visible
            repeat: true
            onTriggered: backend.refreshSystemStatus()
        }
    }

    Window {
        id: windowShelf
        objectName: "windowShelf"
        transientParent: null
        property string filterText: windowFilter.text.trim().toLocaleLowerCase()
        property var filteredItems: {
            var items = backend.windowItems || []
            if (filterText === "")
                return items
            return items.filter(function(item) {
                return String(item.title || "").toLocaleLowerCase().indexOf(filterText) >= 0
            })
        }
        width: Math.min(420, Screen.width - 32)
        height: Math.min(430, Math.max(176, 118 + filteredItems.length * 44))
        x: Math.max(Screen.virtualX + 16,
                    Math.min(dockWindow.x + (dockWindow.width - width) / 2,
                             Screen.virtualX + Screen.width - width - 16))
        y: Math.max(Screen.virtualY + 16, dockWindow.y - height - 10)
        visible: false
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        onVisibleChanged: {
            if (visible) {
                raise()
                windowFilter.forceActiveFocus()
            } else {
                windowFilter.text = ""
            }
        }

        Rectangle {
            anchors.fill: parent
            radius: 18
            color: "#fffaf2"
            border.color: "#cbbda9"
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 9

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "所有窗口 · " + String(backend.windowItems.length)
                        color: "#4c4741"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }
                    RoundButton {
                        text: "×"
                        flat: true
                        implicitWidth: 30
                        implicitHeight: 30
                        onClicked: dockWindow.windowShelfOpen = false
                        ToolTip.visible: hovered
                        ToolTip.text: "关闭窗口列表"
                    }
                }

                TextField {
                    id: windowFilter
                    Layout.fillWidth: true
                    Layout.preferredHeight: 38
                    placeholderText: "搜索已打开的窗口"
                    selectByMouse: true
                    color: "#4d4842"
                    font.pixelSize: 13
                    leftPadding: 12
                    rightPadding: 12
                    background: Rectangle {
                        radius: 12
                        color: "#fffdf9"
                        border.color: windowFilter.activeFocus ? "#a99d8d" : "#ddd3c5"
                        border.width: 1
                    }
                    Keys.onEscapePressed: dockWindow.windowShelfOpen = false
                }

                ListView {
                    id: allWindowsList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 5
                    model: windowShelf.filteredItems
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view ? ListView.view.width : 0
                        height: 39
                        radius: 11
                        color: windowItemHover.hovered ? "#f1e9dc" : "#faf6ef"
                        border.color: windowItemHover.hovered ? "#afa393" : "#e2d9cd"
                        border.width: 1
                        Text {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            verticalAlignment: Text.AlignVCenter
                            text: modelData.title
                            color: "#514c46"
                            font.pixelSize: 13
                            elide: Text.ElideMiddle
                        }
                        HoverHandler { id: windowItemHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                dockWindow.windowShelfOpen = false
                                backend.activateWindow(Number(modelData.handle))
                            }
                        }
                        ToolTip.visible: windowItemHover.hovered
                        ToolTip.text: modelData.title
                        ToolTip.delay: 450
                    }

                    Text {
                        anchors.centerIn: parent
                        visible: allWindowsList.count === 0
                        text: windowShelf.filterText === "" ? "没有可切换窗口" : "没有匹配的窗口"
                        color: "#8b8278"
                        font.pixelSize: 13
                    }
                }
            }
        }
    }

    V03Dock {
        id: resonanceDock
        appBackend: backend
        suppressed: backend.dockSuppressed
    }

    V03WorkPanel {
        id: workPanel
        transientParent: null
        appBackend: backend
        requestedSection: backend.workPanelSection
        presentationArea: petWindow.presentationWorkArea()
        // petWindow is a no-focus Qt.Tool.  Windows may reject activation of a
        // normal sibling window after that click and leave it behind the app
        // the user was working in.  Keep the requested panel discoverable in
        // compact mode; hiding it or returning to visual mode drops the
        // effective Z-order immediately.
        stayOnTopWhenPresented: backend.shellMode === "compact"
        visible: backend.workPanelOpen && !backend.dockSuppressed
        Connections {
            target: backend
            function onWorkPanelNavigationRequested(sectionName) {
                workPanel.presentSection(sectionName)
            }
        }
        onConnectorRequested: function(providerName) {
            desktop.openConnectorSetup(providerName)
        }
        onClosing: function(close) {
            close.accepted = false
            backend.setWorkPanelOpen(false)
        }
    }

    V03BoxWorldScene {
        id: boxWorldScene
        objectName: "boxWorldSceneWindow"
        transientParent: null
        appBackend: backend
        requestedVisible: false
        presentationArea: petWindow.presentationWorkArea()
        // Entering the world is an explicit foreground action.  Keeping this
        // immersive scene above other applications prevents Windows' focus
        // restrictions on a no-focus desktop pet from opening it invisibly
        // behind the current document.  Leaving/minimizing the scene restores
        // the user's normal application order.
        stayOnTopWhenPresented: true

        function synchronizeFromBackend() {
            var shouldShow = Boolean(backend.boxWorldSceneOpen)
                             && !Boolean(backend.dockSuppressed)
            if (!shouldShow) {
                requestedVisible = false
                return
            }
            present()
        }

        Component.onCompleted: synchronizeFromBackend()
        Connections {
            target: backend
            function onBoxWorldSceneOpenChanged() {
                boxWorldScene.synchronizeFromBackend()
            }
            function onBoxWorldPresentationRequested() {
                boxWorldScene.synchronizeFromBackend()
            }
            function onHabitatChanged() {
                boxWorldScene.synchronizeFromBackend()
            }
        }
        onExitRequested: backend.setBoxWorldSceneOpen(false)
        onManageDecorationsRequested: {
            backend.setBoxWorldSceneOpen(false)
            desktop.openWorkPanel("world")
        }
    }

    V03ConnectorSetup {
        id: connectorSetup
        objectName: "v03ConnectorSetup"
        transientParent: null
        appBackend: backend
        property bool requestedVisible: false
        visible: requestedVisible && !backend.dockSuppressed
        onClosing: function(close) {
            close.accepted = false
            requestedVisible = false
        }
    }

    V03FocusTimerAura {
        id: focusTimerAura
        appBackend: backend
        focusInfo: backend.focusStatus
        focusTransition: backend.focusTransition
        presenceInfo: backend.habitatState
        presentationEnabled: !diagnosticWindowProbe && petWindow.visible
                             && !petWindow.manualDragActive
        paperColor: desktop.surfaceColor
        inkColor: desktop.inkColor
        mutedColor: desktop.mutedColor
        progressColor: desktop.focusColor
        completedColor: desktop.cordColor
        lowPower: !backend.sceneActive || !compactWindow.highMotion
        preferredExtent: Math.max(
            144, Math.min(208, desktop.compactBoxSize * 0.95))
        placementArea: petWindow.presentationWorkArea()
        anchorX: petWindow.presentationWindowX + compactLilith.figureLeft
                 + compactLilith.figureWidth / 2
        anchorY: petWindow.presentationWindowY + compactLilith.figureTop - height / 2 + 6
        subjectLeft: petWindow.presentationWindowX + compactLilith.figureLeft
        subjectRight: subjectLeft + compactLilith.figureWidth
        subjectCenterY: petWindow.presentationWindowY + compactLilith.figureTop
                        + compactLilith.figureHeight * 0.43
    }

    Window {
        id: petWindow
        objectName: "petWindow"
        transientParent: null
        width: desktop.compactBoxSize * 3.50
        height: desktop.compactBoxSize * 3.30
        // Restore the saved global coordinate first.  Clamping against the
        // attached Screen here would force every negative/right-hand monitor
        // position back onto the primary screen before Qt could assign the
        // window to its saved display.  The queued startup constraint below
        // resolves the correct work area from this raw global position.
        x: desktop.finiteCoordinate(desktop.savedBoxLayout.x,
                                    Screen.virtualX + Screen.width - width - 30)
        y: desktop.finiteCoordinate(desktop.savedBoxLayout.y,
                                    Screen.virtualY + Screen.height - height - 30)
        visible: !diagnosticWindowProbe && !desktop.petPresenceSuppressed
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
               | (backend.petFloatMode === "always" ? Qt.WindowStaysOnTopHint : 0)
        property bool compactExpanded: compactWindow.expanded
        property bool compactActionsInteractive: compactWindow.actionsInteractive
        property bool manualDragActive: false
        // Windows' compositor owns a successful system move.  This avoids
        // feeding a moving transparent QWindow through a JavaScript polling
        // loop and makes the grabbed point stay directly under the mouse.
        // Offscreen/unsupported platforms keep the deterministic fallback.
        property bool nativeSystemMoveActive: false
        property bool nativeSystemMoveAttempted: false
        property bool nativeSystemMoveStartPending: false
        property int nativeSystemMoveGestureCounter: 0
        property int nativeSystemMoveGestureSerial: 0
        property bool nativeSystemMoveCancelPending: false
        property bool geometryClampActive: false
        property bool resizeDragActive: false
        property real dragWindowX: 0
        property real dragWindowY: 0
        property real dragGrabOffsetX: 0
        property real dragGrabOffsetY: 0
        property real dragStartCursorX: 0
        property real dragStartCursorY: 0
        property real dragCharacterGrabNormX: 0.5
        property real dragCharacterGrabNormY: 0.5
        property bool dragCharacterGrabValid: false
        property bool dragCharacterGrabRemapped: false
        property bool dragMoved: false
        // Stable fallback used by offscreen probes and older platform bridges.
        // The Windows bridge keeps live samples in Python and exposes only the
        // newest point to each drag frame below.
        property real capturedPointerGlobalX: 0
        property real capturedPointerGlobalY: 0
        property int capturedPointerEventSerial: 0
        // Native events stay in Python until the scene graph's next frame.
        // Only the newest point matters; intermediate 500/1000 Hz samples are
        // deliberately discarded instead of becoming DWM geometry commits.
        property bool dragPointerEventPending: false
        property real dragFallbackPointerX: 0
        property real dragFallbackPointerY: 0
        property var dragWorkArea: ({})
        property bool dragWorkAreaValid: false
        // app.py installs a QObject bridge whose PySide slot preserves the
        // QWindow boolean independently of the QML method signature and owns
        // the matching WM_EXITSIZEMOVE gesture serial.
        property var nativeMoveController: null
        property int consumedPointerEventSerial: 0
        property real lastCapturedPointerEventAt: -100000
        readonly property real presentationWindowX:
            manualDragActive ? dragWindowX : x
        readonly property real presentationWindowY:
            manualDragActive ? dragWindowY : y
        property real compactCharacterLeft: compactLilith.figureLeft
        property real compactCharacterTop: compactLilith.figureTop
        property real compactCharacterWidth: compactLilith.figureWidth
        property real compactCharacterHeight: compactLilith.figureHeight
        property real compactAccessoryLeft: compactBox.x
        property real compactAccessoryTop: compactBox.y
        property real compactAccessoryWidth: compactBox.width
        onXChanged: recordNativeWindowMotion()
        onYChanged: recordNativeWindowMotion()
        onManualDragActiveChanged: {
            // Keep habitat geometry snapped for the whole held gesture, then
            // always restore ordinary pose transitions on release/cancel.
            // This is also a fail-safe for visibility or native-capture paths
            // that end the gesture without returning through the usual frame.
            if (!manualDragActive)
                compactLilith.interactionSnap = false
        }
        function characterContains(localX, localY) {
            return compactLilith.containsCharacterPoint(localX, localY)
        }
        function recordNativeWindowMotion() {
            if (!manualDragActive || dragMoved
                    || (!nativeSystemMoveStartPending
                        && !nativeSystemMoveActive))
                return
            if (!compactLilith.dragDisplacementExceeded(
                    x - dragWindowX, y - dragWindowY))
                return
            // A native system move does not deliver ordinary MouseArea moves.
            // Latch that the window crossed the drag threshold so an
            // out-and-back gesture cannot be mistaken for a stationary click.
            dragMoved = true
            compactWindow.expanded = false
            detachForManualDrag()
            remapCharacterGrabAfterDetach()
        }
        function detachForManualDrag() {
            compactLilith.interactionSnap = true
            backend.detachPetHabitat(x, y)
        }
        function captureCharacterGrab(localX, localY) {
            var grab = compactLilith.normalizedCharacterGrab(
                Number(localX), Number(localY)) || ({})
            var grabX = Number(grab.x)
            var grabY = Number(grab.y)
            dragCharacterGrabValid = Boolean(grab.valid)
                    && isFinite(grabX) && isFinite(grabY)
            dragCharacterGrabNormX = dragCharacterGrabValid ? grabX : 0.5
            dragCharacterGrabNormY = dragCharacterGrabValid ? grabY : 0.5
            dragCharacterGrabRemapped = false
        }
        function remapCharacterGrabAfterDetach() {
            if (!dragCharacterGrabValid || dragCharacterGrabRemapped)
                return false
            var point = compactLilith.characterPointForNormalizedGrab(
                dragCharacterGrabNormX, dragCharacterGrabNormY)
            var remappedX = Number(point.x)
            var remappedY = Number(point.y)
            if (!isFinite(remappedX) || !isFinite(remappedY))
                return false
            // From this frame onward the QWindow origin follows the new
            // free-standing representation.  Keeping this offset in the same
            // canonical character space prevents a 60--130 px visual jump
            // when a perch/edge pose detaches on the first drag frame.
            dragGrabOffsetX = remappedX
            dragGrabOffsetY = remappedY
            dragCharacterGrabRemapped = true
            return true
        }
        function finalizeInterruptedInteractionForHide() {
            // Presence can switch to SILENT/BLOCKED while the pointer is
            // still held.  The old cancellation path discarded that final
            // frame, so Lilith appeared to move in this process but jumped
            // back to the previous saved position after restart.  Commit the
            // QWindow's current coordinates without sampling the cursor: a
            // hidden window must never chase a pointer that may already be in
            // a protected application.
            var moved = manualDragActive
                    && (dragMoved
                        || compactLilith.dragDisplacementExceeded(
                            x - dragWindowX, y - dragWindowY))
            var resized = resizeDragActive
            if (moved) {
                var figureCenterX = x + compactLilith.figureLeft
                                    + compactLilith.figureWidth / 2
                var figureCenterY = y + compactLilith.figureTop
                                    + compactLilith.figureHeight / 2
                var targetArea = desktop.workAreaAt(
                    figureCenterX, figureCenterY)
                desktop.compactBoxSize = desktop.fittedCompactSize(
                    desktop.preferredCompactBoxSize, targetArea)
                desktop.clampDraggedFigureToArea(targetArea, true)
                detachForManualDrag()
            }
            if (moved || resized)
                desktop.persistCompactLayout()
            return moved || resized
        }
        onVisibleChanged: {
            if (visible && backend.petFloatMode === "always") {
                raise()
            } else if (!visible) {
                finalizeInterruptedInteractionForHide()
                if (nativeMoveController && nativeSystemMoveGestureSerial > 0)
                    nativeMoveController.acknowledgeSystemMoveFinished(
                        nativeSystemMoveGestureSerial)
                manualDragActive = false
                resizeDragActive = false
                nativeSystemMoveActive = false
                nativeSystemMoveAttempted = false
                nativeSystemMoveStartPending = false
                nativeSystemMoveGestureSerial = 0
                nativeSystemMoveCancelPending = false
                dragMoved = false
                dragPointerEventPending = false
                dragWorkAreaValid = false
                dragCharacterGrabValid = false
                dragCharacterGrabRemapped = false
                compactWindow.expanded = false
                backend.clearPetInteractionLocks()
            }
        }
        Behavior on x {
            enabled: !petWindow.manualDragActive
                     && !petWindow.resizeDragActive
                     && !petWindow.geometryClampActive
            NumberAnimation {
                id: petXPositionAnimation
                duration: 280
                easing.type: Easing.OutCubic
            }
        }
        Behavior on y {
            enabled: !petWindow.manualDragActive
                     && !petWindow.resizeDragActive
                     && !petWindow.geometryClampActive
            NumberAnimation {
                id: petYPositionAnimation
                duration: 280
                easing.type: Easing.OutCubic
            }
        }

        function cancelPositionAnimations() {
            // Pointer avoidance and habitat attachment intentionally glide into
            // place.  If the person grabs Lilith mid-glide, however, those
            // animations must relinquish the window immediately; merely
            // disabling the Behavior does not stop an animation already in
            // flight.
            petXPositionAnimation.stop()
            petYPositionAnimation.stop()
        }

        function moveWindowForDrag(targetX, targetY) {
            targetX = Number(targetX)
            targetY = Number(targetY)
            if (!isFinite(targetX) || !isFinite(targetY))
                return false
            try {
                if (nativeMoveController
                        && nativeMoveController.moveWindowForDrag(
                            targetX, targetY))
                    return true
            } catch (error) {
                // Tests and unsupported platforms can provide only the
                // system-move half of the bridge.  Keep an exact QML fallback.
            }
            x = targetX
            y = targetY
            return true
        }

        function pointInsideWorkArea(globalX, globalY, area) {
            if (!area)
                return false
            var left = Number(area.left)
            var top = Number(area.top)
            var right = Number(area.right)
            var bottom = Number(area.bottom)
            return isFinite(left) && isFinite(top) && isFinite(right)
                    && isFinite(bottom) && globalX >= left && globalX < right
                    && globalY >= top && globalY < bottom
        }

        function dragWorkAreaAt(globalX, globalY) {
            globalX = Number(globalX)
            globalY = Number(globalY)
            if (dragWorkAreaValid
                    && pointInsideWorkArea(globalX, globalY, dragWorkArea))
                return dragWorkArea
            dragWorkArea = desktop.workAreaAt(globalX, globalY)
            dragWorkAreaValid = true
            return dragWorkArea
        }

        function presentationWorkArea() {
            if ((manualDragActive || resizeDragActive) && dragWorkAreaValid)
                return dragWorkArea
            return desktop.workAreaAt(
                x + width / 2, y + height / 2)
        }

        function tryNativeSystemMove() {
            // Windows' compositor-owned move is the default. Direct movement
            // remains an explicit compatibility choice and is also the exact
            // fallback when the platform refuses startSystemMove().
            if (backend.petDragMode !== "system")
                return false
            if (nativeSystemMoveActive)
                return true
            if (nativeSystemMoveAttempted || backend.previewMode)
                return false
            nativeSystemMoveAttempted = true
            // startSystemMove() asks the Windows window manager to capture the
            // held pointer and move this frameless tool window at compositor
            // cadence.  It can legitimately return false (remote/offscreen),
            // in which case followPointerAt remains the exact fallback.
            var started = false
            var requestedSerial = nativeSystemMoveGestureSerial
            nativeSystemMoveStartPending = true
            try {
                started = Boolean(nativeMoveController
                                  && nativeMoveController.tryStartSystemMove(
                                      requestedSerial))
            } catch (error) {
                started = false
            }
            nativeSystemMoveStartPending = false
            // QWindow::startSystemMove releases the QML mouse capture before
            // returning.  WM_CAPTURECHANGED may therefore re-enter
            // onCharacterCanceled while this call is still on the stack.  Do
            // not resurrect an already-finished gesture after that re-entry.
            if (!manualDragActive || requestedSerial <= 0
                    || nativeSystemMoveGestureSerial !== requestedSerial) {
                if (started && nativeMoveController)
                    nativeMoveController.acknowledgeSystemMoveFinished(
                        requestedSerial)
                nativeSystemMoveActive = false
                return false
            }
            nativeSystemMoveActive = started
            if (!started) {
                nativeSystemMoveGestureSerial = 0
                if (nativeSystemMoveCancelPending) {
                    nativeSystemMoveCancelPending = false
                    Qt.callLater(function() {
                        if (petWindow.manualDragActive)
                            petWindow.finishCharacterGesture(
                                compactLilith.pointerMoved, false, 0)
                    })
                }
            }
            return started
        }

        function finishCharacterGesture(reportedMoved, toggleIfStationary,
                                        expectedGestureSerial) {
            var expected = Number(expectedGestureSerial || 0)
            if (!manualDragActive)
                return false
            if (expected > 0 && expected !== nativeSystemMoveGestureSerial)
                return false
            if (!nativeSystemMoveActive) {
                followPendingPointerEvent()
                if (!followCapturedPointerEvent())
                    followGlobalPointerNow()
            }
            // Item-local coordinates can change while a habitat pose or its
            // mirror/scale changes under a stationary pointer.  Only global
            // cursor travel (dragMoved) or real native-window displacement is
            // authoritative; reportedMoved is retained solely for the stable
            // signal ABI used by older themes.
            var actualMoved = dragMoved
                              || compactLilith.dragDisplacementExceeded(
                                  x - dragWindowX, y - dragWindowY)
            var completedSerial = nativeSystemMoveGestureSerial
            if (actualMoved) {
                var cursor = backend.cursorPosition()
                var targetArea = dragWorkAreaAt(
                    Number(cursor.x), Number(cursor.y))
                desktop.compactBoxSize = desktop.fittedCompactSize(
                    desktop.preferredCompactBoxSize, targetArea)
                desktop.clampDraggedFigureToArea(targetArea, true)
                // Synchronize the habitat controller only after the visible
                // release position is final.  Otherwise its desktop fallback
                // can retain the pre-clamp coordinates while the QWindow and
                // persisted layout use another position.
                detachForManualDrag()
                // A host-edge pose cross-fades back to the standing pose.
                // Recheck only after that visual transition has settled so
                // its final visible bounds, rather than the large transparent
                // QWindow canvas, remain recoverable at the monitor edge.
                dragPresentationSettleTimer.restart()
            }
            manualDragActive = false
            nativeSystemMoveActive = false
            nativeSystemMoveAttempted = false
            nativeSystemMoveStartPending = false
            nativeSystemMoveGestureSerial = 0
            nativeSystemMoveCancelPending = false
            dragPointerEventPending = false
            dragWorkAreaValid = false
            if (nativeMoveController && completedSerial > 0)
                nativeMoveController.acknowledgeSystemMoveFinished(
                    completedSerial)
            if (!actualMoved) {
                applyHabitatState()
                if (Boolean(toggleIfStationary))
                    compactWindow.expanded = !compactWindow.expanded
            }
            backend.setPetInteractionLock("character", false)
            desktop.scheduleCompactLayoutPersistence()
            dragCharacterGrabValid = false
            dragCharacterGrabRemapped = false
            return true
        }

        function finishNativeSystemMove(gestureSerial) {
            var serial = Number(gestureSerial)
            if (!nativeSystemMoveActive
                    || serial <= 0
                    || serial !== nativeSystemMoveGestureSerial)
                return false
            return finishCharacterGesture(false, true, serial)
        }

        function followPointerAt(cursorX, cursorY) {
            if (!manualDragActive)
                return
            cursorX = Number(cursorX)
            cursorY = Number(cursorY)
            if (!isFinite(cursorX) || !isFinite(cursorY))
                return
            var startDx = cursorX - dragStartCursorX
            var startDy = cursorY - dragStartCursorY
            if (!dragMoved
                    && !compactLilith.dragDisplacementExceeded(startDx, startDy))
                return
            if (!dragMoved) {
                dragMoved = true
                // Detach as soon as the gesture is undeniably a drag.  The
                // 220--300 ms host-pose transition can then complete while
                // the pointer is still moving instead of changing Lilith's
                // visible anchor after the mouse button is released.
                detachForManualDrag()
                remapCharacterGrabAfterDetach()
                // A radial menu travelling with the pointer feels like a
                // second object was accidentally grabbed.  Collapse it as
                // soon as this gesture is unambiguously a drag.
                compactWindow.expanded = false
                // A successful system move has owned the pointer since the
                // original press.  Do not reposition the QWindow from QML as
                // well: two independent move authorities are exactly what
                // causes the pet to lag behind and then jump under the hand.
                if (nativeSystemMoveActive)
                    return
                // Align the original grab point before handing motion to the
                // fallback mover.  The normal Windows path has already tried
                // startSystemMove() during the press event; this late request
                // is retained only for unusual platform event ordering.
                moveWindowForDrag(cursorX - dragGrabOffsetX,
                                  cursorY - dragGrabOffsetY)
                if (tryNativeSystemMove())
                    return
            }
            if (nativeSystemMoveActive)
                return
            var targetArea = dragWorkAreaAt(cursorX, cursorY)
            // Preserve the normalized point held by the user when crossing
            // into a smaller high-DPI work area.  Fitting before clamping
            // prevents an oversized window from degenerating to (left, top)
            // and keeps the same part of Lilith under the pointer.
            var oldWidth = Math.max(1, width)
            var oldHeight = Math.max(1, height)
            var grabRatioX = dragGrabOffsetX / oldWidth
            var grabRatioY = dragGrabOffsetY / oldHeight
            var targetSize = desktop.fittedCompactSize(
                desktop.preferredCompactBoxSize, targetArea)
            if (Math.abs(targetSize - desktop.compactBoxSize) > 0.01) {
                desktop.compactBoxSize = targetSize
                dragGrabOffsetX = grabRatioX * width
                dragGrabOffsetY = grabRatioY * height
            }
            var targetX = cursorX - dragGrabOffsetX
            var targetY = cursorY - dragGrabOffsetY
            // Do not clamp the whole window while the button is held.  At a
            // monitor seam the pointer is necessarily on the new screen edge;
            // clamping there would break the grab offset and make Lilith jump
            // away from the hand.  The release/cancel path keeps the visible
            // figure recoverable without clamping the much larger transparent
            // canvas, while the held point remains under the real cursor.
            moveWindowForDrag(targetX, targetY)
        }

        function consumePointerEvent(pointerX, pointerY) {
            try {
                if (nativeMoveController
                        && typeof nativeMoveController.takeLatestPointerEvent
                           === "function") {
                    var latest = nativeMoveController.takeLatestPointerEvent(
                        consumedPointerEventSerial) || ({})
                    var latestSerial = Number(latest.serial)
                    var latestX = Number(latest.x)
                    var latestY = Number(latest.y)
                    if (Boolean(latest.available)
                            && latestSerial > consumedPointerEventSerial
                            && isFinite(latestX) && isFinite(latestY)) {
                        consumedPointerEventSerial = latestSerial
                        lastCapturedPointerEventAt = Date.now()
                        return ({ x: latestX, y: latestY, captured: true })
                    }
                }
            } catch (error) {
                // Offscreen tests and older platform bridges continue through
                // the stable QML property fallback below.
            }
            var serial = Number(capturedPointerEventSerial)
            if (serial > consumedPointerEventSerial) {
                consumedPointerEventSerial = serial
                var capturedX = Number(capturedPointerGlobalX)
                var capturedY = Number(capturedPointerGlobalY)
                if (isFinite(capturedX) && isFinite(capturedY)) {
                    lastCapturedPointerEventAt = Date.now()
                    return ({ x: capturedX, y: capturedY, captured: true })
                }
            }
            // Direct/offscreen callers do not install the native event
            // bridge. Their supplied point is already relative to the current
            // window and remains a deterministic fallback.
            return ({
                x: x + Number(pointerX),
                y: y + Number(pointerY),
                captured: false
            })
        }

        function followPointerEvent(pointerX, pointerY) {
            if (!manualDragActive)
                return
            dragFallbackPointerX = Number(pointerX)
            dragFallbackPointerY = Number(pointerY)
            dragPointerEventPending = true
        }

        function followPendingPointerEvent() {
            if (!manualDragActive || !dragPointerEventPending)
                return false
            dragPointerEventPending = false
            var cursor = consumePointerEvent(
                dragFallbackPointerX, dragFallbackPointerY)
            followPointerAt(cursor.x, cursor.y)
            return true
        }

        function followCapturedPointerEvent() {
            if (!manualDragActive)
                return false
            // The production bridge coalesces native samples in Python and
            // deliberately does not wake QML by updating the fallback serial
            // on every event.  In particular, a stationary click's release
            // can therefore be newer in takeLatestPointerEvent() while the
            // QML fallback property still equals the already-consumed press.
            // Always ask consumePointerEvent(); it cheaply reports
            // captured=false when neither source has a newer sample.
            var cursor = consumePointerEvent(0, 0)
            if (!cursor.captured)
                return false
            followPointerAt(cursor.x, cursor.y)
            return true
        }

        function followGlobalPointerSample(forceSample) {
            if (!manualDragActive)
                return
            // This is a safety net for coalesced/missing window-system frames,
            // not a second authority. Sampling between healthy mouse events
            // makes an event-time position and a newer QCursor position fight.
            if (!Boolean(forceSample)
                    && Date.now() - lastCapturedPointerEventAt < 20)
                return
            var cursor = backend.cursorPosition()
            followPointerAt(Number(cursor.x), Number(cursor.y))
        }

        function followGlobalPointer() {
            followGlobalPointerSample(false)
        }

        function followPointerFrame() {
            if (!manualDragActive || nativeSystemMoveActive)
                return
            if (followPendingPointerEvent())
                return
            // MouseArea can miss a position callback while the native tool
            // window itself is moving, even though the QWindow event filter
            // has already received the new global point. Consume that event
            // first on every frame; only poll QCursor when no event is
            // waiting. This removes the short "held back, then catch up"
            // feeling without letting stale item-local coordinates move the
            // window twice.
            if (!followCapturedPointerEvent())
                followGlobalPointer()
        }

        function followGlobalPointerNow() {
            followGlobalPointerSample(true)
        }

        FrameAnimation {
            // Drive the compatibility fallback from the actual Qt Quick frame
            // clock. A fixed 16 ms timer drifts against 60/120/144 Hz displays
            // and can submit geometry just after DWM's composition deadline.
            running: petWindow.manualDragActive
                     && !petWindow.nativeSystemMoveActive
            onTriggered: petWindow.followPointerFrame()
        }
        Timer {
            id: dragPresentationSettleTimer
            interval: 340
            repeat: false
            onTriggered: {
                if (petWindow.manualDragActive || backend.habitatState.attached)
                    return
                var figureCenterX = petWindow.x + compactLilith.figureLeft
                                    + compactLilith.figureWidth / 2
                var figureCenterY = petWindow.y + compactLilith.figureTop
                                    + compactLilith.figureHeight / 2
                var area = desktop.workAreaAt(figureCenterX, figureCenterY)
                var previousX = petWindow.x
                var previousY = petWindow.y
                desktop.clampDraggedFigureToArea(area, true)
                if (Math.abs(petWindow.x - previousX) > 0.01
                        || Math.abs(petWindow.y - previousY) > 0.01) {
                    backend.detachPetHabitat(petWindow.x, petWindow.y)
                    desktop.persistCompactLayout()
                }
            }
        }
        function applyHabitatState() {
            if (petWindow.manualDragActive || petWindow.resizeDragActive)
                return
            var habitat = backend.habitatState || ({})
            if (!habitat.attached)
                return
            var habitatPose = String(habitat.pose || "")
            var edgeAllowance = habitatPose.indexOf("edge-peek") === 0
                                ? petWindow.width * 0.30 : 0
            var targetX = Number(habitat.x)
            var targetY = Number(habitat.y)
            if (!isFinite(targetX)) targetX = petWindow.x
            if (!isFinite(targetY)) targetY = petWindow.y
            var targetArea = desktop.workAreaAt(
                targetX + petWindow.width / 2,
                targetY + petWindow.height / 2)
            desktop.compactBoxSize = desktop.fittedCompactSize(
                desktop.preferredCompactBoxSize, targetArea)
            edgeAllowance = habitatPose.indexOf("edge-peek") === 0
                            ? petWindow.width * 0.30 : 0
            var minimumX = Number(targetArea.left) - edgeAllowance
            var maximumX = Number(targetArea.right) - petWindow.width
                           + edgeAllowance
            var minimumY = Number(targetArea.top)
            var maximumY = Number(targetArea.bottom) - petWindow.height
            petWindow.x = maximumX < minimumX ? Number(targetArea.left)
                : Math.max(minimumX, Math.min(targetX, maximumX))
            petWindow.y = maximumY < minimumY ? Number(targetArea.top)
                : Math.max(minimumY, Math.min(targetY, maximumY))
        }
        Connections {
            target: backend
            function onHabitatChanged() { petWindow.applyHabitatState() }
        }
        function scheduleScreenConstraint() {
            var observedGestureSerial = Number(nativeSystemMoveGestureSerial)
            var observedGestureCounter = Number(nativeSystemMoveGestureCounter)
            var observedDuringDrag = manualDragActive || resizeDragActive
            Qt.callLater(function() {
                // screenChanged is commonly delivered while a held window
                // crosses a mixed-DPI seam.  Its queued callback may not run
                // until after release; that stale full-window clamp must not
                // override finishCharacterGesture's figure-aware result.
                if (observedDuringDrag || observedGestureSerial > 0
                        || petWindow.manualDragActive
                        || petWindow.resizeDragActive
                        || observedGestureCounter
                           !== Number(petWindow.nativeSystemMoveGestureCounter))
                    return
                if (!backend.habitatState.attached)
                    desktop.constrainCompactPet(true)
            })
        }
        onScreenChanged: scheduleScreenConstraint()

        Item {
        id: compactWindow
        objectName: "desktopPet"
        property var savedLayout: backend.boxLayout()
        property real boxSize: desktop.compactBoxSize
        property real petCenterX: width * 0.50
        property real petCenterY: height * 0.51
        property var menuWorkArea: petWindow.presentationWorkArea()
        readonly property real actionVisibleLeft: Math.max(
            8, Number(menuWorkArea.left) - petWindow.presentationWindowX + 8)
        readonly property real actionVisibleTop: Math.max(
            8, Number(menuWorkArea.top) - petWindow.presentationWindowY + 8)
        readonly property real actionVisibleRight: Math.min(
            width - 8, Number(menuWorkArea.right) - petWindow.presentationWindowX - 8)
        readonly property real actionVisibleBottom: Math.min(
            height - 8, Number(menuWorkArea.bottom) - petWindow.presentationWindowY - 8)
        readonly property real actionVisibleWidth: Math.max(
            0, actionVisibleRight - actionVisibleLeft)
        readonly property real actionVisibleHeight: Math.max(
            0, actionVisibleBottom - actionVisibleTop)
        // A radial orbit is ideal while the full transparent pet canvas is on
        // screen.  Edge-peek habitats deliberately put part of that canvas
        // outside the monitor; independently clamping six orbit points used to
        // collapse two controls onto the same pixels.  In that case use three
        // deterministic rows on either side of Lilith.  It still rotates out,
        // but every visible pill owns a unique click target.
        readonly property bool actionGridMode:
            actionVisibleLeft > 8.5 || actionVisibleTop > 8.5
            || actionVisibleRight < width - 8.5
            || actionVisibleBottom < height - 8.5
        property bool expanded: false
        property real orbitProgress: expanded ? 1 : 0
        // Quiet breathing remains visible at 15 FPS.  Hovering, dragging or
        // opening one of Lilith's surfaces temporarily restores the 60 FPS
        // interaction cadence, matching the v0.3 active/idle animation budget.
        readonly property bool highMotion: expanded
                                                || petWindow.manualDragActive
                                                || compactLilith.characterPressed
                                                || compactLilith.characterHovered
                                                || compactLilith.poseTransitionRunning
                                                || petPoseResolver.requiresHighMotion
                                                || backend.chatOpen
                                                || backend.workPanelOpen
                                                || backend.boxWorldSceneOpen
        readonly property real quietBreath: 0.5
                                             + Math.sin(compactLilith.motionPhase) * 0.5
        // PySide's QVariantList property can remain cached by a QML Repeater
        // after a function-library checkbox emits quickActionsChanged. Keep a
        // local model and assign a fresh list explicitly so newly pinned
        // actions are instantiated immediately instead of appearing only
        // after a restart.
        property var quickActionModel: []
        property string statusToastText: ""
        // Visibility and input share one threshold.  A separate 18% input
        // gate left moving pills visible while both their QML MouseArea and
        // the native WM_NCHITTEST island were disabled, so an early click
        // reached WPS or a game behind Lilies.  The shared property is also
        // exported to CompactHitTestFilter by petWindow below.
        // CompactHitTestFilter ignores controls at opacity <= 0.05, so the
        // shared threshold starts immediately above that same native cutoff.
        readonly property bool actionsVisible: expanded && orbitProgress > 0.05
        readonly property bool actionsInteractive: actionsVisible
        property real turn: expanded ? 360 : 0
        property var savedAccessory: backend.accessoryBoxLayout()
        property real accessoryDx: Number(savedAccessory.dx)
        property real accessoryDy: Number(savedAccessory.dy)
        property real accessoryScale: Number(savedAccessory.scale)
        anchors.fill: parent
        visible: true

        function safeActionCoordinate(desired, itemExtent, windowOrigin,
                                      windowExtent, areaStart, areaEnd) {
            desired = Number(desired)
            itemExtent = Math.max(1, Number(itemExtent))
            windowOrigin = Number(windowOrigin)
            windowExtent = Math.max(1, Number(windowExtent))
            areaStart = Number(areaStart)
            areaEnd = Number(areaEnd)
            if (!isFinite(desired)) desired = 0
            if (!isFinite(windowOrigin)) windowOrigin = 0
            if (!isFinite(areaStart) || !isFinite(areaEnd) || areaEnd <= areaStart)
                return Math.max(0, Math.min(desired, windowExtent - itemExtent))
            var minimum = Math.max(0, areaStart - windowOrigin + 8)
            var maximum = Math.min(windowExtent - itemExtent,
                                   areaEnd - windowOrigin - itemExtent - 8)
            if (maximum >= minimum)
                return Math.max(minimum, Math.min(desired, maximum))
            // If the visible intersection is narrower than the control,
            // centre it in that intersection and retain as much as possible.
            var visibleStart = Math.max(0, areaStart - windowOrigin)
            var visibleEnd = Math.min(windowExtent, areaEnd - windowOrigin)
            var centred = (visibleStart + visibleEnd - itemExtent) / 2
            return Math.max(0, Math.min(centred,
                                       Math.max(0, windowExtent - itemExtent)))
        }

        function safeActionX(desired, itemWidth) {
            return safeActionCoordinate(
                desired, itemWidth, petWindow.presentationWindowX, width,
                Number(menuWorkArea.left), Number(menuWorkArea.right))
        }

        function safeActionY(desired, itemHeight) {
            return safeActionCoordinate(
                desired, itemHeight, petWindow.presentationWindowY, height,
                Number(menuWorkArea.top), Number(menuWorkArea.bottom))
        }

        function actionGridColumn(index) {
            // chat/right-middle, world/left-middle, settings/right-top,
            // then the three optional slots right-bottom/left-bottom/left-top.
            var columns = [1, 0, 1, 1, 0, 0]
            return columns[Math.max(0, Math.min(columns.length - 1,
                                                Number(index) || 0))]
        }

        function actionGridRow(index) {
            var rows = [1, 1, 0, 2, 2, 0]
            return rows[Math.max(0, Math.min(rows.length - 1,
                                             Number(index) || 0))]
        }

        function packedActionX(index, desired, itemWidth) {
            if (!actionGridMode)
                return safeActionX(desired, itemWidth)
            var left = actionVisibleLeft
            var right = Math.max(left + itemWidth, actionVisibleRight)
            return actionGridColumn(index) === 0
                    ? left : Math.max(left, right - itemWidth)
        }

        function packedActionY(index, desired, itemHeight) {
            if (!actionGridMode)
                return safeActionY(desired, itemHeight)
            var top = actionVisibleTop
            var bottom = Math.max(top + itemHeight, actionVisibleBottom)
            var travel = Math.max(0, bottom - top - itemHeight)
            return top + travel * actionGridRow(index) / 2
        }

        function rebuildQuickActionModel() {
            // Do not hand the Repeater PySide's cached QVariantList wrapper.
            // Clone both the list and every map so a pin/unpin always changes
            // model identity and creates/removes the delegate in this frame.
            var source = backend.currentQuickActions() || []
            var next = []
            for (var index = 0; index < source.length; ++index) {
                var action = source[index] || {}
                next.push({
                    "action": String(action.action || ""),
                    "label": String(action.label || ""),
                    "shortLabel": String(action.shortLabel || action.label || ""),
                    "description": String(action.description || ""),
                    "angle": Number(action.angle || 0),
                    "fixed": Boolean(action.fixed)
                })
            }
            quickActionModel = next
        }

        Component.onCompleted: rebuildQuickActionModel()
        onExpandedChanged: backend.setPetInteractionLock("menu", expanded)
        Connections {
            target: backend
            function onQuickActionsChanged() {
                compactWindow.rebuildQuickActionModel()
            }
            function onStatusChanged() {
                var message = String(backend.status || "").trim()
                if (message === "")
                    return
                compactWindow.statusToastText = message
                backendStatusToastTimer.restart()
            }
        }

        Timer {
            id: backendStatusToastTimer
            interval: 3600
            repeat: false
            onTriggered: compactWindow.statusToastText = ""
        }

        Behavior on orbitProgress {
            NumberAnimation { duration: 640; easing.type: Easing.OutCubic }
        }

        Item {
            anchors.fill: parent

            Canvas {
                id: supportCord
                objectName: "desktopPetCord"
                anchors.fill: parent
                visible: false
                opacity: 0.88
                z: 1
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.clearRect(0, 0, width, height)
                    var bx = compactBox.x + compactBox.width / 2
                    var by = compactBox.y + compactBox.height / 2
                    var r = compactBox.width / 2
                    var startX = compactLilith.supportCordPoint.x
                    var startY = compactLilith.supportCordPoint.y
                    var vx = bx - startX
                    var vy = by - startY
                    var distance = Math.max(1, Math.sqrt(vx * vx + vy * vy))
                    var endX = bx - vx / distance * r * 0.86
                    var endY = by - vy / distance * r * 0.86
                    ctx.strokeStyle = "#96342d"
                    ctx.lineWidth = Math.max(1.4, compactWindow.boxSize * 0.010)
                    ctx.lineCap = "round"
                    ctx.beginPath()
                    ctx.moveTo(startX, startY)
                    ctx.bezierCurveTo(startX - compactWindow.boxSize * 0.16,
                                      startY + compactWindow.boxSize * 0.20,
                                      endX - compactWindow.boxSize * 0.10,
                                      endY + compactWindow.boxSize * 0.18,
                                      endX, endY)
                    ctx.stroke()
                }
                Connections {
                    target: compactWindow
                    function onBoxSizeChanged() { supportCord.requestPaint() }
                    function onAccessoryDxChanged() { supportCord.requestPaint() }
                    function onAccessoryDyChanged() { supportCord.requestPaint() }
                    function onAccessoryScaleChanged() { supportCord.requestPaint() }
                }
            }

            V03PetPoseResolver {
                id: petPoseResolver
                habitatState: backend.habitatState || ({})
                chatOpen: backend.chatOpen
                selectionBubble: backend.selectionBubble || ({})
                companionBubble: backend.companionService.bubble || ({})
                readingStatus: backend.readingStatus || ({})
                focusStatus: backend.focusStatus || ({})
                equippedPose: String(
                    (backend.wardrobeState.current || {}).pose_id || "idle-prayer")
            }

            V03PetBody {
                id: compactLilith
                objectName: "compactLilith"
                anchors.fill: parent
                appBackend: backend
                characterHeight: compactWindow.boxSize * 2.20
                pose: petPoseResolver.resolvedPose
                paused: petWindow.manualDragActive
                        || petWindow.resizeDragActive
                        || !petWindow.visible
                        || backend.habitatState.state === "blocked"
                lowPower: backend.habitatState.state === "silent"
                          || !backend.sceneActive
                          || !compactWindow.highMotion
                cordStart: supportCordPoint
                cordEnd: {
                    var bx = compactBox.x + compactBox.width / 2
                    var by = compactBox.y + compactBox.height / 2
                    var sx = supportCordPoint.x
                    var sy = supportCordPoint.y
                    var vx = bx - sx
                    var vy = by - sy
                    var distance = Math.max(1, Math.sqrt(vx * vx + vy * vy))
                    var radius = compactBox.width / 2
                    return Qt.point(bx - vx / distance * radius * 0.86,
                                    by - vy / distance * radius * 0.86)
                }
                z: 3
                onCharacterPressStarted: function(pointerX, pointerY) {
                    backend.setPetInteractionLock("character", true)
                    petWindow.cancelPositionAnimations()
                    petWindow.manualDragActive = true
                    petWindow.nativeSystemMoveActive = false
                    petWindow.nativeSystemMoveAttempted = false
                    petWindow.nativeSystemMoveStartPending = false
                    petWindow.nativeSystemMoveGestureCounter += 1
                    petWindow.nativeSystemMoveGestureSerial =
                        petWindow.nativeSystemMoveGestureCounter
                    petWindow.nativeSystemMoveCancelPending = false
                    petWindow.dragMoved = false
                    petWindow.dragWindowX = petWindow.x
                    petWindow.dragWindowY = petWindow.y
                    // Capture the anatomical point before a threshold drag
                    // detaches a perch/edge pose into the standing pose.
                    // Item-local QML coordinates are the only reliable source
                    // for that point; the event bridge below supplies the
                    // independently stable global cursor position.
                    petWindow.captureCharacterGrab(pointerX, pointerY)
                    var cursor = petWindow.consumePointerEvent(pointerX, pointerY)
                    petWindow.dragPointerEventPending = false
                    petWindow.dragWorkArea = desktop.workAreaAt(cursor.x, cursor.y)
                    petWindow.dragWorkAreaValid = true
                    petWindow.dragGrabOffsetX = cursor.x - petWindow.x
                    petWindow.dragGrabOffsetY = cursor.y - petWindow.y
                    petWindow.dragStartCursorX = cursor.x
                    petWindow.dragStartCursorY = cursor.y
                    // QWindow::startSystemMove must be requested from the
                    // originating mouse-press delivery.  Starting it only
                    // after a drag threshold is crossed is too late on
                    // Windows and forces the visibly laggier polling path.
                    petWindow.tryNativeSystemMove()
                }
                onCharacterPointerMoved: function(pointerX, pointerY) {
                    petWindow.followPointerEvent(pointerX, pointerY)
                }
                onCharacterReleased: function(moved) {
                    petWindow.finishCharacterGesture(
                        moved, true, petWindow.nativeSystemMoveGestureSerial)
                }
                onCharacterCanceled: function(moved) {
                    if (petWindow.nativeSystemMoveStartPending
                            || petWindow.nativeSystemMoveActive) {
                        // ReleaseCapture can cancel the MouseArea grab before
                        // Windows posts WM_EXITSIZEMOVE.  That native message,
                        // not this ambiguous cancel, decides whether the same
                        // gesture moved or was a stationary menu click.
                        petWindow.nativeSystemMoveCancelPending = true
                        return
                    }
                    petWindow.finishCharacterGesture(moved, false, 0)
                }
                onWheelStepped: function(steps) {
                    desktop.resizeCompactPet(steps > 0 ? 12 : -12, true)
                }
            }

            Rectangle {
                id: companionUnreadCue
                objectName: "desktopPetCompanionUnreadCue"
                readonly property int unreadCount: Number(
                    (backend.companionService.deliveryStatus || {}).unreadCount || 0)
                visible: unreadCount > 0 && !compactWindow.expanded
                width: Math.max(30, Math.min(42, compactWindow.boxSize * 0.27))
                height: width
                radius: width * 0.42
                x: Math.max(4, Math.min(compactWindow.width - width - 4,
                    compactLilith.figureLeft + compactLilith.figureWidth - width * 0.72))
                y: Math.max(4, Math.min(compactWindow.height - height - 4,
                    compactLilith.figureTop - height * 0.18))
                color: unreadCueClick.containsMouse ? "#fff8ee" : "#fffefb"
                border.color: unreadCueClick.containsMouse
                              ? desktop.cordColor : "#b9aea0"
                border.width: unreadCueClick.containsMouse ? 2 : 1
                z: 22

                Rectangle {
                    width: parent.width * 0.24
                    height: width
                    rotation: 45
                    color: parent.color
                    border.color: parent.border.color
                    border.width: parent.border.width
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.rightMargin: parent.width * 0.08
                    anchors.bottomMargin: -height * 0.20
                    z: -1
                }

                Text {
                    anchors.centerIn: parent
                    text: companionUnreadCue.unreadCount > 9
                          ? "9+" : String(companionUnreadCue.unreadCount)
                    color: desktop.cordColor
                    font.pixelSize: Math.max(12, parent.width * 0.40)
                    font.weight: Font.DemiBold
                }

                MouseArea {
                    id: unreadCueClick
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.PointingHandCursor
                    onPressed: backend.setPetInteractionLock(
                        "companion-unread", true)
                    onCanceled: backend.setPetInteractionLock(
                        "companion-unread", false)
                    onReleased: backend.setPetInteractionLock(
                        "companion-unread", false)
                    onClicked: {
                        if (!backend.companionService.reopenUnread())
                            desktop.presentChatPage(3)
                    }
                }

                ToolTip.visible: unreadCueClick.containsMouse
                ToolTip.text: "一封没有读到的莉莉丝信笺"
            }

            Timer {
                // The figure/window bounds are stable between drag, resize and
                // pose transitions.  Rebuilding and crossing a QVariantMap on
                // every animation frame made the idle pet wake the GUI thread
                // needlessly; a two-Hz geometry heartbeat still updates well
                // inside the pointer-avoidance cooldown (3--5 seconds).
                interval: 500
                repeat: true
                running: petWindow.visible
                         && !petWindow.manualDragActive
                         && !petWindow.resizeDragActive
                onTriggered: {
                    var area = desktop.workAreaAt(
                        petWindow.x + petWindow.width / 2,
                        petWindow.y + petWindow.height / 2)
                    backend.updatePetGeometry({
                        windowX: petWindow.x,
                        windowY: petWindow.y,
                        windowWidth: petWindow.width,
                        windowHeight: petWindow.height,
                        figureLeft: petWindow.x + compactLilith.figureLeft,
                        figureTop: petWindow.y + compactLilith.figureTop,
                        figureWidth: compactLilith.figureWidth,
                        figureHeight: compactLilith.figureHeight,
                        workLeft: area.left,
                        workTop: area.top,
                        workWidth: area.width,
                        workHeight: area.height,
                        menuOpen: compactWindow.expanded,
                        pointerDown: compactLilith.characterPressed,
                        visible: petWindow.visible
                    })
                }
            }

            Rectangle {
                id: compactPetShadow
                visible: false // V03PetBody owns an independently animated shadow.
                width: compactLilith.figureWidth * (0.42 - compactWindow.quietBreath * 0.025)
                height: Math.max(5, compactWindow.boxSize * 0.035)
                radius: height / 2
                x: compactLilith.figureLeft + compactLilith.figureWidth / 2 - width / 2
                y: compactLilith.figureTop + compactLilith.figureHeight - height * 0.25
                color: "#5b5046"
                opacity: 0.11 - compactWindow.quietBreath * 0.035
                z: 2
            }

            Rectangle {
                id: petResizeHandle
                objectName: "desktopPetResizeHandle"
                width: Math.max(24, compactWindow.boxSize * 0.16)
                height: width
                radius: width / 2
                x: compactLilith.figureLeft + compactLilith.figureWidth - width * 0.78
                y: compactLilith.figureTop + compactLilith.figureHeight - height * 0.92
                color: "#fffefb"
                border.color: "#aaa298"
                border.width: 1
                opacity: petResizeDrag.active ? 0.92 : 0.68
                visible: compactLilith.characterHovered || resizeHover.hovered || petResizeDrag.active
                z: 18

                Text {
                    anchors.centerIn: parent
                    text: "↘"
                    color: "#665f57"
                    font.pixelSize: Math.max(13, parent.height * 0.52)
                }
                HoverHandler {
                    id: resizeHover
                    cursorShape: Qt.SizeFDiagCursor
                }
                DragHandler {
                    id: petResizeDrag
                    target: null
                    dragThreshold: 4
                    property real startSize: compactWindow.boxSize
                    property bool gestureStarted: false
                    property real startCursorX: 0
                    property real startCursorY: 0
                    property real startHandleGlobalX: 0
                    property real startHandleGlobalY: 0
                    property real startFigureRight: 0
                    property real startFigureBottom: 0
                    onActiveChanged: {
                        backend.setPetInteractionLock("resize", active)
                        if (active) {
                            compactLayoutPersistTimer.stop()
                            petWindow.cancelPositionAnimations()
                            petWindow.resizeDragActive = true
                            gestureStarted = true
                            startSize = compactWindow.boxSize
                            // scenePressPosition is stable for the whole
                            // gesture and still refers to the press-time
                            // window origin.  Unlike translation, it does not
                            // reset to zero on the frame that takes the grab.
                            startCursorX = petWindow.x
                                    + centroid.scenePressPosition.x
                            startCursorY = petWindow.y
                                    + centroid.scenePressPosition.y
                            startHandleGlobalX = petWindow.x
                                    + petResizeHandle.x
                                    + petResizeHandle.width / 2
                            startHandleGlobalY = petWindow.y
                                    + petResizeHandle.y
                                    + petResizeHandle.height / 2
                            startFigureRight = compactLilith.figureLeft
                                    + compactLilith.figureWidth
                            startFigureBottom = compactLilith.figureTop
                                    + compactLilith.figureHeight
                        } else if (gestureStarted) {
                            gestureStarted = false
                            var releaseCursor = backend.cursorPosition()
                            var releaseArea = desktop.workAreaAt(
                                Number(releaseCursor.x),
                                Number(releaseCursor.y))
                            desktop.clampDraggedFigureToArea(
                                releaseArea, false)
                            petWindow.resizeDragActive = false
                            desktop.persistCompactLayout()
                        } else {
                            petWindow.resizeDragActive = false
                        }
                    }
                    onTranslationChanged: {
                        if (!active) return
                        var cursor = backend.cursorPosition()
                        var cursorX = Number(cursor.x)
                        var cursorY = Number(cursor.y)
                        if (!isFinite(cursorX))
                            cursorX = startCursorX + translation.x
                        if (!isFinite(cursorY))
                            cursorY = startCursorY + translation.y
                        var pointerDx = cursorX - startCursorX
                        var pointerDy = cursorY - startCursorY
                        var delta = (pointerDx + pointerDy) * 0.42
                        var desired = startSize + delta
                        desktop.resizeCompactPetForDrag(
                            desired - desktop.compactBoxSize, false, false,
                            cursorX, cursorY)
                        // Resizing changes both the native window extent and
                        // the figure-relative handle position.  Counter-move
                        // the window so the exact point grabbed by the user,
                        // rather than merely the old window origin, follows
                        // the global cursor on every frame.
                        var desiredHandleX = startHandleGlobalX + pointerDx
                        var desiredHandleY = startHandleGlobalY + pointerDy
                        // figureLeft/figureWidth ultimately depend on
                        // mapToItem(), whose refreshed bound becomes visible
                        // only after the polish pass. Predict the identical
                        // scale-homogeneous corner from the press-time bounds
                        // instead of reading a stale pre-resize coordinate.
                        var sizeRatio = desktop.compactBoxSize
                                / Math.max(1, startSize)
                        var handleExtent = Math.max(
                            24, desktop.compactBoxSize * 0.16)
                        var nextHandleLocalX = startFigureRight * sizeRatio
                                - handleExtent * 0.28
                        var nextHandleLocalY = startFigureBottom * sizeRatio
                                - handleExtent * 0.42
                        petWindow.moveWindowForDrag(
                            desiredHandleX - nextHandleLocalX,
                            desiredHandleY - nextHandleLocalY)
                    }
                }
                WheelHandler {
                    onWheel: function(event) {
                        if (event.angleDelta.y === 0) return
                        desktop.resizeCompactPet(
                            event.angleDelta.y > 0 ? 12 : -12, true)
                        event.accepted = true
                    }
                }
            }

            Repeater {
                id: actionRepeater
                model: compactWindow.quickActionModel
                delegate: Rectangle {
                    id: componentButton
                    objectName: "desktopPetAction_" + modelData.action
                    required property int index
                    required property var modelData
                    property var savedComponent: backend.componentLayout(modelData.action, modelData.angle)
                    property real offsetX: Number(savedComponent.dx)
                    property real offsetY: Number(savedComponent.dy)
                    property real buttonScale: Number(savedComponent.scale)
                    property real dragStartOffsetX: offsetX
                    property real dragStartOffsetY: offsetY
                    property real spiralAngle: (1 - compactWindow.orbitProgress) * -Math.PI * 1.45
                    property real spiralX: (offsetX * Math.cos(spiralAngle) - offsetY * Math.sin(spiralAngle)) * compactWindow.orbitProgress
                    property real spiralY: (offsetX * Math.sin(spiralAngle) + offsetY * Math.cos(spiralAngle)) * compactWindow.orbitProgress
                    readonly property real desiredX:
                        compactWindow.petCenterX + spiralX * compactWindow.boxSize - width / 2
                    readonly property real desiredY:
                        compactWindow.petCenterY + spiralY * compactWindow.boxSize - height / 2
                    property var companionActivity: backend.companionService.activityStatus || ({})
                    readonly property bool desktopDiscoveryActive:
                        modelData.action === "settings" && backend.shellMode === "compact"
                    property string visibleLabel: modelData.action === "peek"
                                                  ? (backend.desktopPeekStatus.active ? "返回工作" : "看桌面")
                                                  : modelData.action === "lilies-desktop"
                                                    ? (backend.shellMode === "compact" ? "展开莉桌面" : "收成桌宠")
                                                  : desktopDiscoveryActive
                                                    ? "设置 · 桌面"
                                                  : modelData.action === "chat"
                                                    && Number(companionActivity.delivery
                                                              ? companionActivity.delivery.unreadCount : 0) > 0
                                                    ? "未读陪伴（1）"
                                                  : String(modelData.shortLabel || modelData.label || "功能")
                    property string visibleDescription: modelData.action === "chat"
                        ? (String(modelData.description || "")
                           + "\n" + String(companionActivity.observationModeLabel || "应用感知状态未知")
                           + "\n" + String(companionActivity.observationModeDetail || "")
                           + "\n当前 · " + String(companionActivity.stateLabel || "等待")
                           + (companionActivity.stateDetail
                              ? (" · " + String(companionActivity.stateDetail)) : "")
                           + "\n在对话窗口选择「陪伴」；也可在设置中把陪伴固定到环形菜单。")
                        : desktopDiscoveryActive
                          ? (String(modelData.description || "")
                             + "\n完整动态桌面仍在；打开后选择「展开莉莉丝桌面」。")
                        : String(modelData.description || "")
                    width: Math.min(compactWindow.boxSize * 0.84,
                                    Math.max(compactWindow.boxSize * 0.56 * buttonScale,
                                             actionLabel.implicitWidth + 30,
                                             companionAwarenessLabel.implicitWidth + 24,
                                             desktopDiscoveryLabel.implicitWidth + 24))
                    height: Math.max(modelData.action === "chat" || desktopDiscoveryActive ? 46 : 38,
                                     compactWindow.boxSize * 0.29 * buttonScale)
                    radius: height / 2
                    // Edge-peek habitats intentionally leave part of the
                    // transparent pet window beyond a monitor.  Keep every
                    // radial action inside the visible work-area intersection
                    // without moving Lilith away from her host-window pose.
                    x: compactWindow.packedActionX(index, desiredX, width)
                    y: compactWindow.packedActionY(index, desiredY, height)
                    color: actionClick.pressed ? "#eee5d9" : (actionClick.containsMouse ? "#fff9ef" : "#fffefb")
                    border.color: actionClick.pressed ? "#7d756b" : "#aca397"
                    border.width: actionClick.pressed ? 2 : 1
                    opacity: compactWindow.orbitProgress
                    scale: 0.34 + compactWindow.orbitProgress * 0.66
                    rotation: (1 - compactWindow.orbitProgress) * -320
                    visible: compactWindow.actionsVisible
                    z: 10
                    Text {
                        id: actionLabel
                        objectName: "desktopPetActionLabel_" + modelData.action
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        anchors.bottomMargin: modelData.action === "chat" || componentButton.desktopDiscoveryActive
                                              ? parent.height * 0.26 : 0
                        text: componentButton.visibleLabel
                        color: "#393632"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        wrapMode: Text.NoWrap
                        maximumLineCount: 1
                        elide: Text.ElideRight
                        font.pixelSize: Math.max(12, Math.min(15, compactWindow.boxSize * 0.105))
                        font.weight: Font.Medium
                    }
                    Text {
                        id: companionAwarenessLabel
                        objectName: "desktopPetAwarenessLabel_" + modelData.action
                        visible: modelData.action === "chat"
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        anchors.bottomMargin: Math.max(4, parent.height * 0.10)
                        text: visible
                              ? String(componentButton.companionActivity.compactStatusLabel
                                       || componentButton.companionActivity.observationModeShort
                                       || "陪伴 · 等待")
                              : ""
                        color: componentButton.companionActivity.paused
                               || !Boolean(componentButton.companionActivity.configuredEnabled)
                               ? "#8b6b58" : desktop.focusColor
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        font.pixelSize: Math.max(9, Math.min(11, compactWindow.boxSize * 0.072))
                        font.weight: Font.Medium
                    }
                    Text {
                        id: desktopDiscoveryLabel
                        objectName: modelData.action === "settings"
                                    ? "compactDesktopDiscoveryLabel" : ""
                        visible: componentButton.desktopDiscoveryActive
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.leftMargin: 10
                        anchors.rightMargin: 10
                        anchors.bottomMargin: Math.max(4, parent.height * 0.10)
                        text: visible ? "完整桌面入口" : ""
                        color: desktop.cordColor
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        font.pixelSize: Math.max(9, Math.min(11, compactWindow.boxSize * 0.072))
                        font.weight: Font.Medium
                    }
                    MouseArea {
                        id: actionClick
                        objectName: "desktopPetActionHit_" + modelData.action
                        anchors.fill: parent
                        enabled: componentButton.visible && compactWindow.actionsInteractive
                        hoverEnabled: true
                        acceptedButtons: Qt.LeftButton
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            // A selected function owns the next interaction;
                            // leave no invisible/lingering radial controls on
                            // top of the application it just opened.
                            compactWindow.expanded = false
                            desktop.activateQuickAction(modelData.action)
                        }
                    }
                    ToolTip.visible: compactWindow.actionsInteractive && actionClick.containsMouse
                    ToolTip.text: String(modelData.label || componentButton.visibleLabel)
                                      + "\n" + componentButton.visibleDescription
                                      + "\n左键打开 · 右键拖动 · 滚轮缩放"
                    DragHandler {
                        id: componentMoveDrag
                        target: null
                        dragThreshold: 4
                        acceptedButtons: Qt.RightButton
                        onActiveChanged: {
                            backend.setPetInteractionLock(
                                "component-" + String(modelData.action), active)
                            if (active) {
                                componentButton.dragStartOffsetX = componentButton.offsetX
                                componentButton.dragStartOffsetY = componentButton.offsetY
                            } else {
                                backend.saveComponentLayout(modelData.action, componentButton.offsetX,
                                                            componentButton.offsetY, componentButton.buttonScale)
                            }
                        }
                        onTranslationChanged: {
                            if (!active) return
                            componentButton.offsetX = Math.max(-1.32, Math.min(1.32,
                                componentButton.dragStartOffsetX + translation.x / compactWindow.boxSize))
                            componentButton.offsetY = Math.max(-1.26, Math.min(1.26,
                                componentButton.dragStartOffsetY + translation.y / compactWindow.boxSize))
                        }
                    }
                    WheelHandler {
                        enabled: compactWindow.actionsInteractive && actionClick.containsMouse
                        onWheel: function(event) {
                            if (event.angleDelta.y === 0) return
                            componentButton.buttonScale = Math.max(0.70, Math.min(1.55,
                                componentButton.buttonScale + (event.angleDelta.y > 0 ? 0.06 : -0.06)))
                            backend.saveComponentLayout(modelData.action, componentButton.offsetX,
                                                        componentButton.offsetY, componentButton.buttonScale)
                            event.accepted = true
                        }
                    }
                }
            }

            Rectangle {
                id: compactBox
                objectName: "compactAccessoryBox"
                width: compactWindow.boxSize * compactWindow.accessoryScale
                height: width
                radius: width / 2
                x: compactWindow.petCenterX + compactWindow.accessoryDx * compactWindow.boxSize - width / 2
                y: compactWindow.petCenterY + compactWindow.accessoryDy * compactWindow.boxSize - height / 2
                color: "#fffefa"
                border.color: "#a9a297"
                border.width: Math.max(1, width * 0.018)
                rotation: compactWindow.turn
                z: 4
                Behavior on rotation { NumberAnimation { duration: 760; easing.type: Easing.InOutCubic } }

                Rectangle {
                    anchors.centerIn: parent
                    width: parent.width * (1.08 + compactWindow.quietBreath * 0.035)
                    height: width
                    radius: width / 2
                    color: "transparent"
                    border.color: "#e3f7f2"
                    border.width: Math.max(3, parent.width * 0.075)
                    opacity: 0.22 + compactWindow.quietBreath * 0.12
                    z: -1
                }
                Canvas {
                    anchors.fill: parent
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        ctx.strokeStyle = "#c8c0b5"
                        ctx.lineWidth = Math.max(1, width * 0.014)
                        ctx.beginPath()
                        ctx.arc(width/2, height/2, width*0.30, 0.38, 5.56)
                        ctx.stroke()
                    }
                }
                Rectangle {
                    width: Math.max(5, parent.width * 0.075)
                    height: width
                    radius: width/2
                    color: "#9f3129"
                    x: parent.width * 0.79
                    y: parent.height * 0.48
                }

                HoverHandler { id: accessoryHover }
                TapHandler {
                    id: accessoryTap
                    onPressedChanged: backend.setPetInteractionLock(
                        "accessory-tap", pressed)
                    onTapped: compactWindow.expanded = !compactWindow.expanded
                }
                DragHandler {
                    id: accessoryDrag
                    target: null
                    dragThreshold: 4
                    property real startDx
                    property real startDy
                    onActiveChanged: {
                        backend.setPetInteractionLock("accessory-drag", active)
                        if (active) {
                            startDx = compactWindow.accessoryDx
                            startDy = compactWindow.accessoryDy
                        } else {
                            backend.saveAccessoryBoxLayout(compactWindow.accessoryDx,
                                                           compactWindow.accessoryDy,
                                                           compactWindow.accessoryScale)
                        }
                    }
                    onTranslationChanged: {
                        if (!active) return
                        // Keep the box within the real transparent window, but
                        // use all available room.  The old fixed +/-1.2 limits
                        // stopped a normally sized box tens of pixels before
                        // the visible edge and felt like lost pointer capture.
                        var scale = Math.max(1, compactWindow.boxSize)
                        var halfWidth = compactBox.width / 2
                        var halfHeight = compactBox.height / 2
                        var margin = 2
                        var minimumDx = (-compactWindow.petCenterX
                                         + halfWidth + margin) / scale
                        var maximumDx = (compactWindow.width
                                         - compactWindow.petCenterX
                                         - halfWidth - margin) / scale
                        var minimumDy = (-compactWindow.petCenterY
                                         + halfHeight + margin) / scale
                        var maximumDy = (compactWindow.height
                                         - compactWindow.petCenterY
                                         - halfHeight - margin) / scale
                        compactWindow.accessoryDx = Math.max(
                            minimumDx, Math.min(maximumDx,
                                startDx + translation.x / scale))
                        compactWindow.accessoryDy = Math.max(
                            minimumDy, Math.min(maximumDy,
                                startDy + translation.y / scale))
                    }
                }
                WheelHandler {
                    onWheel: function(event) {
                        compactWindow.accessoryScale = Math.max(0.28, Math.min(0.66,
                            compactWindow.accessoryScale + (event.angleDelta.y > 0 ? 0.025 : -0.025)))
                        backend.saveAccessoryBoxLayout(compactWindow.accessoryDx,
                                                       compactWindow.accessoryDy,
                                                       compactWindow.accessoryScale)
                        event.accepted = true
                    }
                }
            }

            // Shell form is a view control, not one of the three optional
            // feature slots.  Keeping this small paper tab beside the box
            // means a persisted compact session always has a direct route
            // back to the full Lilies desktop without first discovering the
            // settings page or sacrificing a radial-menu slot.
            Rectangle {
                id: desktopModeTab
                objectName: "desktopPetDesktopModeTab"
                readonly property bool placeRight: (
                    petWindow.presentationWindowX
                    + compactBox.x + compactBox.width
                    + width + 9 <= Number(compactWindow.menuWorkArea.right))
                width: Math.max(58, Math.min(86, compactWindow.boxSize * 0.58))
                height: Math.max(25, Math.min(34, compactWindow.boxSize * 0.23))
                x: compactWindow.safeActionX(
                    placeRight
                    ? compactBox.x + compactBox.width + 8
                    : compactBox.x - width - 8,
                    width)
                y: compactWindow.safeActionY(
                    compactBox.y + compactBox.height / 2 - height / 2,
                    height)
                radius: 5
                color: desktopModeClick.pressed
                       ? "#eee5d9"
                       : (desktopModeClick.containsMouse ? "#fff9ef" : "#fffdf8")
                border.color: desktopModeClick.containsMouse ? "#8c8378" : "#c8bdae"
                border.width: 1
                opacity: desktopModeClick.containsMouse ? 0.98 : 0.84
                visible: !backend.dockSuppressed && !compactWindow.expanded
                z: 9

                Rectangle {
                    width: Math.max(4, parent.width * 0.08)
                    height: parent.height - 8
                    anchors.left: parent.left
                    anchors.leftMargin: 4
                    anchors.verticalCenter: parent.verticalCenter
                    radius: width / 2
                    color: desktop.cordColor
                    opacity: 0.72
                }

                Text {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 6
                    text: backend.shellMode === "compact" ? "展开桌面" : "仅桌宠"
                    color: "#4d4943"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    font.pixelSize: Math.max(
                        10, Math.min(13, compactWindow.boxSize * 0.082))
                    font.weight: Font.Medium
                }

                MouseArea {
                    id: desktopModeClick
                    anchors.fill: parent
                    hoverEnabled: true
                    acceptedButtons: Qt.LeftButton
                    cursorShape: Qt.PointingHandCursor
                    onPressed: backend.setPetInteractionLock(
                        "desktop-mode-tab", true)
                    onCanceled: backend.setPetInteractionLock(
                        "desktop-mode-tab", false)
                    onReleased: backend.setPetInteractionLock(
                        "desktop-mode-tab", false)
                    onClicked: backend.toggleDesktopShell()
                }

                ToolTip.visible: desktopModeClick.containsMouse
                ToolTip.text: backend.shellMode === "compact"
                              ? "一键展开完整动态桌面；当前应用保持打开"
                              : "一键恢复 Windows 桌面，只保留莉莉丝"
            }

        }

        Rectangle {
            id: backendStatusToast
            objectName: "backendStatusToast"
            width: Math.min(300, Math.max(170, backendStatusToastText.implicitWidth + 28))
            height: Math.min(88, Math.max(38, backendStatusToastText.implicitHeight + 18))
            x: compactWindow.safeActionX(
                compactLilith.figureLeft + compactLilith.figureWidth / 2 - width / 2,
                width)
            y: compactWindow.safeActionY(
                compactLilith.figureTop - height - 10, height)
            radius: 13
            color: desktop.paperLightColor
            border.color: desktop.hairlineColor
            border.width: 1
            opacity: visible ? 0.96 : 0
            visible: compactWindow.statusToastText !== ""
                     && !backend.dockSuppressed
            z: 40

            Text {
                id: backendStatusToastText
                objectName: "backendStatusToastText"
                anchors.fill: parent
                anchors.margins: 9
                text: compactWindow.statusToastText
                color: desktop.inkColor
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.pixelSize: Math.max(
                    10, Math.min(13, compactWindow.boxSize * 0.078))
            }

            Behavior on opacity {
                NumberAnimation { duration: 130 }
            }
        }
    }

    }

    CompanionBubble {
        id: proactiveBubble
        controller: backend.companionService
        suppressed: backend.companionSuppressed
        paperColor: desktop.paperLightColor
        inkColor: desktop.inkColor
        mutedColor: desktop.mutedColor
        hairlineColor: desktop.hairlineColor
        cordColor: desktop.cordColor
        anchorX: petWindow.presentationWindowX + compactLilith.figureLeft
        anchorY: petWindow.presentationWindowY + compactLilith.figureTop + compactLilith.figureHeight * 0.18
        subjectLeft: petWindow.presentationWindowX + compactLilith.figureLeft
        subjectRight: subjectLeft + compactLilith.figureWidth
        subjectCenterY: petWindow.presentationWindowY + compactLilith.figureTop
                        + compactLilith.figureHeight * 0.42
    }

    FocusDiversionBubble {
        id: focusDiversionBubble
        appBackend: backend
        suppressed: backend.dockSuppressed
        anchorX: petWindow.presentationWindowX + compactLilith.figureLeft
                 + compactLilith.figureWidth * 0.35
        anchorY: petWindow.presentationWindowY + compactLilith.figureTop
                 + compactLilith.figureHeight * 0.22
        subjectLeft: petWindow.presentationWindowX + compactLilith.figureLeft
        subjectRight: subjectLeft + compactLilith.figureWidth
        subjectCenterY: petWindow.presentationWindowY + compactLilith.figureTop
                        + compactLilith.figureHeight * 0.42
    }

    Window {
        id: selectionBubble
        objectName: "selectionBubble"
        transientParent: null
        property var bubbleData: backend.selectionBubble
        property string bubbleText: String(selectionBubble.bubbleData.text || "")
        property bool hasDetails: bubbleText.length > 170 || bubbleText.indexOf("\n") >= 0
        property bool expanded: hasDetails && bubbleHover.hovered
        // Four reading actions plus the optional save action need a real
        // minimum width.  The former 260x150 floor caused both the final text
        // line and action row to collide on high-DPI displays.
        property int compactWidth: Math.max(340, Math.min(430,
            252 + Math.sqrt(Math.max(1, bubbleText.length)) * 14))
        property int compactHeight: Math.max(184, Math.min(248,
            140 + Math.ceil(Math.max(1, bubbleText.length) / 24) * 21))
        property int detailWidth: Math.max(430, Math.min(580,
            330 + Math.sqrt(Math.max(1, bubbleText.length)) * 16))
        property int detailHeight: Math.max(270, Math.min(440,
            155 + Math.ceil(Math.max(1, bubbleText.length) / 28) * 24))
        width: expanded ? Math.min(detailWidth, Screen.width - 24) : compactWidth
        height: expanded ? Math.min(detailHeight, Screen.height - 32) : compactHeight
        x: Math.max(Screen.virtualX + 12,
                    Math.min(Number(selectionBubble.bubbleData.x || 0) + 18,
                             Screen.virtualX + Screen.width - width - 12))
        y: {
            var below = Number(selectionBubble.bubbleData.y || 0) + 24
            if (below + height <= Screen.virtualY + Screen.height - 12)
                return Math.max(Screen.virtualY + 12, below)
            return Math.max(Screen.virtualY + 12, Number(selectionBubble.bubbleData.y || 0) - height - 18)
        }
        visible: Boolean(selectionBubble.bubbleData.visible) && !backend.dockSuppressed
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        onVisibleChanged: { if (visible) raise() }
        Behavior on width { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }

        Rectangle {
            id: bubbleSurface
            anchors.fill: parent
            radius: selectionBubble.expanded ? 25 : 21
            color: desktop.surfaceColor
            border.color: selectionBubble.bubbleData.error ? "#c77b72" : desktop.hairlineColor
            border.width: 1

            Rectangle {
                x: 0
                y: 22
                width: 4
                height: Math.min(46, parent.height - 44)
                radius: 2
                color: selectionBubble.bubbleData.error ? "#b95a52" : desktop.cordColor
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 18
                anchors.topMargin: 14
                anchors.bottomMargin: 14
                spacing: 9

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 7
                    Rectangle {
                        Layout.preferredWidth: 7
                        Layout.preferredHeight: 7
                        radius: 4
                        color: selectionBubble.bubbleData.busy ? "#b4423a" : "#9c9488"
                        SequentialAnimation on opacity {
                            running: Boolean(selectionBubble.bubbleData.busy)
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.25; duration: 720; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 1.0; duration: 720; easing.type: Easing.InOutSine }
                        }
                    }
                    Label {
                        text: "莉莉丝 · Luna-medium"
                        color: "#777067"
                        font.pixelSize: 12
                    }
                    Label {
                        visible: selectionBubble.hasDetails && !selectionBubble.expanded
                        text: "悬停展开"
                        color: "#a39080"
                        font.pixelSize: 11
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: "×"
                        color: closeHover.hovered ? "#73342f" : "#9d958a"
                        font.pixelSize: 17
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        Layout.preferredWidth: 22
                        Layout.preferredHeight: 22
                        HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: backend.dismissSelectionBubble() }
                    }
                }

                Label {
                    visible: !selectionBubble.expanded
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    text: selectionBubble.bubbleText
                    color: selectionBubble.bubbleData.error ? "#8b3933" : "#4e4b47"
                    font.pixelSize: 15
                    lineHeight: 1.3
                    wrapMode: Text.Wrap
                    elide: Text.ElideRight
                    maximumLineCount: 6
                    verticalAlignment: Text.AlignTop
                }

                ScrollView {
                    visible: selectionBubble.expanded
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    TextArea {
                        text: selectionBubble.bubbleText
                        color: selectionBubble.bubbleData.error ? "#8b3933" : "#4e4b47"
                        font.pixelSize: 15
                        wrapMode: Text.Wrap
                        readOnly: true
                        selectByMouse: true
                        background: null
                    }
                }

                RowLayout {
                    visible: !Boolean(selectionBubble.bubbleData.error)
                    Layout.fillWidth: true
                    Layout.preferredHeight: 30
                    spacing: 5

                    Repeater {
                        id: selectionActionRepeater
                        model: [
                            { label: "简释", action: "explain" },
                            { label: "翻译", action: "translate" },
                            { label: "术语卡", action: "term" },
                            { label: "追问", action: "ask" }
                        ]
                        delegate: Rectangle {
                            required property var modelData
                            objectName: "selectionAction_" + modelData.action
                            Layout.preferredWidth: modelData.action === "term" ? 58 : 48
                            Layout.preferredHeight: 30
                            radius: 4
                            color: String(selectionBubble.bubbleData.action || "explain") === modelData.action ? desktop.paperColor : desktop.surfaceColor
                            border.color: actionHover.hovered ? desktop.cordColor : desktop.hairlineColor
                            opacity: Boolean(selectionBubble.bubbleData.busy) ? 0.48 : 1
                            Label {
                                anchors.centerIn: parent
                                text: modelData.label
                                color: "#5c554d"
                                font.pixelSize: 11
                            }
                            HoverHandler { id: actionHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler {
                                enabled: !Boolean(selectionBubble.bubbleData.busy)
                                onTapped: {
                                    if (modelData.action === "ask") {
                                        selectionQuestion.requested = true
                                        Qt.callLater(function() {
                                            if (!selectionQuestion.visible)
                                                return
                                            selectionQuestion.raise()
                                            selectionQuestion.requestActivate()
                                            selectionQuestionInput.forceActiveFocus()
                                        })
                                    } else {
                                        backend.requestSelectionAction(modelData.action, "")
                                    }
                                }
                            }
                        }
                    }

                    Item { Layout.fillWidth: true }

                    Rectangle {
                        objectName: "selectionSaveAction"
                        visible: Boolean(selectionBubble.bubbleData.canSave)
                                 || Boolean(selectionBubble.bubbleData.savedCardId)
                        Layout.preferredWidth: 54
                        Layout.preferredHeight: 30
                        radius: 4
                        color: Boolean(selectionBubble.bubbleData.savedCardId)
                               ? "#e7eee8" : desktop.surfaceColor
                        border.color: saveHover.hovered ? desktop.focusColor : desktop.hairlineColor
                        Text {
                            id: selectionSaveLabel
                            objectName: "selectionSaveLabel"
                            anchors.fill: parent
                            text: Boolean(selectionBubble.bubbleData.savedCardId)
                                  ? "已收好" : "收进盒"
                            color: Boolean(selectionBubble.bubbleData.savedCardId)
                                   ? "#557069" : "#5c554d"
                            font.pixelSize: 11
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        HoverHandler { id: saveHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            enabled: !Boolean(selectionBubble.bubbleData.savedCardId)
                                     && !Boolean(selectionBubble.bubbleData.busy)
                            onTapped: backend.saveSelectionCard()
                        }
                    }
                }
            }

            HoverHandler { id: bubbleHover }
        }

        Timer {
            interval: 20000
            running: selectionBubble.visible && !Boolean(selectionBubble.bubbleData.busy) && !bubbleHover.hovered && !selectionQuestion.visible
            repeat: false
            onTriggered: backend.dismissSelectionBubble()
        }
    }

    Window {
        id: selectionQuestion
        objectName: "selectionQuestion"
        transientParent: null
        property bool requested: false
        width: Math.min(440, Screen.width - 24)
        height: 126
        x: Math.max(Screen.virtualX + 12,
                    Math.min(selectionBubble.x,
                             Screen.virtualX + Screen.width - width - 12))
        y: Math.max(Screen.virtualY + 12,
                    Math.min(selectionBubble.y + selectionBubble.height + 8,
                             Screen.virtualY + Screen.height - height - 12))
        visible: requested && selectionBubble.visible && !backend.dockSuppressed
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool

        onVisibleChanged: {
            if (!visible) {
                requested = false
                selectionQuestionInput.clear()
            }
        }

        function submitQuestion() {
            var value = selectionQuestionInput.text.trim()
            if (!value)
                return
            backend.requestSelectionAction("ask", value)
            selectionQuestionInput.clear()
            requested = false
        }

        Rectangle {
            anchors.fill: parent
            radius: 8
            color: desktop.surfaceColor
            border.color: desktop.hairlineColor
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                spacing: 8
                Label { text: "只针对这一次选区追问"; color: "#6e665d"; font.pixelSize: 12 }
                RowLayout {
                    Layout.fillWidth: true
                    TextField {
                        id: selectionQuestionInput
                        Layout.fillWidth: true
                        placeholderText: "例如：这里为什么使用这个假设？"
                        onAccepted: selectionQuestion.submitQuestion()
                    }
                    Button { text: "问"; onClicked: selectionQuestion.submitQuestion() }
                    Button { text: "取消"; flat: true; onClicked: selectionQuestion.requested = false }
                }
                Label {
                    text: "不会附带上一次回答、聊天记录或长期记忆。"
                    color: "#94897c"
                    font.pixelSize: 11
                }
            }
        }
    }

    Window {
        id: chatWindow
        objectName: "chatWindow"
        transientParent: null
        property int page: 0
        property string editingMemoryId: ""
        property string selectedMemoryPartition: ""
        property string pendingDeleteFragmentId: ""
        property string pendingDeleteFragmentSummary: ""
        property bool presentationResetHidden: false
        property bool presentationRecoveryArmed: false
        property int presentationStableChecks: 0
        width: 560
        height: Math.min(Screen.height - 80, 760)
        x: Screen.virtualX + Screen.width - width - 26
        y: Screen.virtualY + Math.max(24, (Screen.height - height) / 2)
        visible: backend.chatOpen && !backend.dockSuppressed
                 && !presentationResetHidden
        color: "transparent"
        flags: Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        onVisibleChanged: {
            // Reopening settings during the 350 ms foreground-restoration
            // grace period is an implicit cancellation.  Do not let the stale
            // click dispatch while Lilies is visible again.
            if (visible && companionScreenObservationDelay.running)
                companionScreenObservationDelay.stop()
        }
        onClosing: function(close) {
            close.accepted = false
            presentationRecoveryArmed = false
            presentationStableChecks = 0
            backend.setChatOpen(false)
        }

        Rectangle {
            anchors.fill: parent
            radius: 28
            color: desktop.surfaceRaisedColor
            border.color: desktop.hairlineColor
            border.width: 1

            Rectangle {
                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                width: 10; color: "#d6c19f"; radius: 5
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 22
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 7
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "莉莉丝的盒子"; font.pixelSize: 22; color: "#4d4a45" }
                        Item { Layout.fillWidth: true }
                        Label {
                            text: backend.chatBusy ? "正在整理" : "本地信笺"
                            color: "#81786e"
                            font.pixelSize: 12
                        }
                        LiliesPaperButton {
                            objectName: "chatCloseButton"
                            text: "×"
                            flat: true
                            implicitWidth: 34
                            onClicked: backend.setChatOpen(false)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        LiliesPaperButton {
                            objectName: "chatPageConversationButton"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "对话"
                            selected: chatWindow.page === 0
                            onClicked: chatWindow.page = 0
                        }
                        LiliesPaperButton {
                            objectName: "chatPageMemoryButton"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "记忆"
                            selected: chatWindow.page === 5
                            onClicked: {
                                chatWindow.page = 5
                                backend.refreshMemoryMap(chatWindow.selectedMemoryPartition)
                            }
                        }
                        LiliesPaperButton {
                            objectName: "chatPageReadingButton"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "阅读"
                            selected: chatWindow.page === 2
                            onClicked: { chatWindow.page = 2; backend.refreshReadingCards() }
                        }
                        LiliesPaperButton {
                            objectName: "chatPageCompanionButton"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "陪伴"
                            selected: chatWindow.page === 3
                            Accessible.name: "打开主动陪伴设置"
                            onClicked: chatWindow.page = 3
                        }
                        LiliesPaperButton {
                            objectName: "chatPageSettingsButton"
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            text: "设置"
                            selected: chatWindow.page === 4
                            onClicked: chatWindow.page = 4
                        }
                    }
                }

                StackLayout {
                    currentIndex: chatWindow.page
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: 0

                    ColumnLayout {
                        RowLayout {
                            Layout.fillWidth: true
                            Button { text: "新对话"; onClicked: backend.newConversation() }
                            Button { text: "重试"; enabled: !backend.chatBusy; onClicked: backend.retryLastMessage() }
                            TextField {
                                id: historyQuery
                                Layout.fillWidth: true
                                placeholderText: "搜索本地对话历史"
                                color: "#292622"
                                placeholderTextColor: "#756e65"
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 14
                                background: Rectangle {
                                    color: "#ffffff"
                                    radius: 8
                                    border.color: historyQuery.activeFocus ? "#6f817c" : "#a99e90"
                                    border.width: historyQuery.activeFocus ? 2 : 1
                                }
                                onAccepted: backend.searchHistory(text)
                                onTextChanged: if (!text) backend.searchHistory("")
                            }
                            Button { text: "搜索"; onClicked: backend.searchHistory(historyQuery.text) }
                        }
                        Rectangle {
                            visible: backend.historyResults.length > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: visible ? Math.min(180, 42 + backend.historyResults.length * 38) : 0
                            radius: 12
                            color: "#f4eadc"
                            border.color: "#d2c5b2"
                            ListView {
                                anchors.fill: parent
                                anchors.margins: 7
                                clip: true
                                spacing: 4
                                model: backend.historyResults
                                delegate: Label {
                                    required property var modelData
                                    width: ListView.view.width
                                    text: modelData.speaker + "：" + modelData.content
                                    color: modelData.role === "user" ? "#6f6256" : "#596f6a"
                                    elide: Text.ElideRight
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Rectangle {
                                Layout.preferredWidth: 11
                                Layout.preferredHeight: 11
                                radius: 6
                                color: backend.chatBusy ? "#dffdf8" : "#9bb8b1"
                                border.color: "#69847e"
                                opacity: backend.chatBusy ? 0.35 : 0.78
                                SequentialAnimation on opacity {
                                    running: backend.chatBusy
                                    loops: Animation.Infinite
                                    NumberAnimation { from: 0.30; to: 1.0; duration: 760; easing.type: Easing.InOutSine }
                                    NumberAnimation { from: 1.0; to: 0.30; duration: 760; easing.type: Easing.InOutSine }
                                }
                            }
                            Label {
                                Layout.fillWidth: true
                                text: backend.chatBusy ? "莉莉丝正在整理……" : "GPT 莉莉丝 · 记忆与对话只保存在这台电脑"
                                color: "#4e625e"
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }
                            Label {
                                text: backend.modelStatus.lastRun && backend.modelStatus.lastRun.tokensPerSecond
                                      ? Number(backend.modelStatus.lastRun.tokensPerSecond).toFixed(1) + " token/s" : ""
                                color: "#746f67"
                                font.pixelSize: 12
                            }
                        }
                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            TextArea {
                                id: conversationText
                                text: backend.chatText || "纸箱里很安静。她似乎在等你先开口。"
                                readOnly: true
                                selectByMouse: true
                                wrapMode: Text.Wrap
                                textFormat: Text.PlainText
                                color: "#24211e"
                                selectionColor: "#b9dcd7"
                                selectedTextColor: "#171513"
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 17
                                font.weight: Font.Medium
                                padding: 18
                                background: Rectangle {
                                    color: "#fffefb"
                                    radius: 14
                                    border.color: "#9c9183"
                                    border.width: 1
                                }
                            }
                        }
                        Rectangle {
                            id: toolConfirmation
                            property string commandText: backend.pendingTool.arguments && backend.pendingTool.arguments.command
                                                         ? backend.pendingTool.arguments.command : ""
                            visible: Object.keys(backend.pendingTool).length > 0
                            Layout.fillWidth: true
                            Layout.preferredHeight: confirmationColumn.implicitHeight + 20
                            radius: 14
                            color: "#f2dfd8"
                            border.color: "#9f3129"
                            ColumnLayout {
                                id: confirmationColumn
                                anchors.fill: parent; anchors.margins: 10
                                Label { Layout.fillWidth: true; text: "莉莉丝请求执行：" + (backend.pendingTool.componentId || "") + "." + (backend.pendingTool.actionId || ""); color: "#6f342f"; wrapMode: Text.Wrap }
                                Label { Layout.fillWidth: true; text: backend.pendingTool.reason || "需要确认"; color: "#75645e"; wrapMode: Text.Wrap }
                                Label {
                                    visible: toolConfirmation.commandText.length > 0
                                    Layout.fillWidth: true
                                    text: "命令：" + toolConfirmation.commandText
                                    color: "#402f2c"
                                    wrapMode: Text.WrapAnywhere
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                                RowLayout {
                                    Button { text: "允许一次"; onClicked: backend.resolveToolConfirmation(true) }
                                    Button { text: "拒绝"; onClicked: backend.resolveToolConfirmation(false) }
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            function submitPrompt() {
                                var value = promptInput.text.trim()
                                if (!value || backend.chatBusy)
                                    return
                                backend.sendMessage(value)
                                promptInput.clear()
                                Qt.callLater(function() { promptInput.forceActiveFocus() })
                            }
                            TextArea {
                                id: promptInput
                                Layout.fillWidth: true
                                Layout.preferredHeight: Math.max(74, Math.min(132, contentHeight + topPadding + bottomPadding + 8))
                                placeholderText: "打开应用、文件夹、文件或网页：…\nEnter 发送 · Shift+Enter 换行"
                                wrapMode: Text.Wrap
                                color: "#211f1c"
                                placeholderTextColor: "#746d65"
                                selectionColor: "#b9dcd7"
                                selectedTextColor: "#171513"
                                font.family: "Microsoft YaHei UI"
                                font.pixelSize: 16
                                font.weight: Font.Medium
                                padding: 12
                                background: Rectangle {
                                    color: "#ffffff"
                                    radius: 14
                                    border.color: promptInput.activeFocus ? "#6f817c" : "#9c9183"
                                    border.width: promptInput.activeFocus ? 2 : 1
                                }
                                Keys.onPressed: function(event) {
                                    var isEnter = event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                                    var wantsNewline = (event.modifiers & Qt.ShiftModifier) !== 0
                                    if (isEnter && !wantsNewline && !inputMethodComposing) {
                                        parent.submitPrompt()
                                        event.accepted = true
                                    }
                                }
                            }
                            Button {
                                text: backend.chatBusy ? "停止" : "发送"
                                onClicked: {
                                    if (backend.chatBusy) backend.cancelMessage()
                                    else parent.submitPrompt()
                                }
                            }
                        }
                    }

                    ColumnLayout {
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: "这是 v0.1 的审阅卡片兼容入口；分区记忆与原始出处请在记忆地图中管理。"
                                wrapMode: Text.Wrap
                                color: "#6f675e"
                            }
                            Button {
                                text: "返回记忆地图"
                                onClicked: {
                                    chatWindow.page = 5
                                    backend.refreshMemoryMap(chatWindow.selectedMemoryPartition)
                                }
                            }
                        }
                        ListView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 8
                            model: backend.memoryItems
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view.width
                                height: 92
                                radius: 14
                                color: modelData.enabled ? "#f7efe3" : "#eee9e1"
                                border.color: "#d1c3ae"
                                RowLayout {
                                    anchors.fill: parent; anchors.margins: 12
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        Label { text: modelData.title; font.bold: true; color: "#4d4a45" }
                                        Label { text: modelData.content; wrapMode: Text.Wrap; elide: Text.ElideRight; maximumLineCount: 2; Layout.fillWidth: true; color: "#746f67" }
                                    }
                                    CheckBox {
                                        text: checked ? "已装载" : "未装载"
                                        checked: Boolean(modelData.enabled)
                                        onToggled: backend.setMemoryEnabled(modelData.memory_id, checked)
                                    }
                                    Button {
                                        text: "编辑"
                                        onClicked: {
                                            chatWindow.editingMemoryId = modelData.memory_id
                                            memoryTitle.text = modelData.title
                                            memoryContent.text = modelData.content
                                        }
                                    }
                                    Button { text: "删除"; onClicked: backend.deleteMemory(modelData.memory_id) }
                                }
                            }
                        }
                        TextField { id: memoryTitle; Layout.fillWidth: true; placeholderText: "记忆标题" }
                        TextArea { id: memoryContent; Layout.fillWidth: true; Layout.preferredHeight: 82; placeholderText: "需要莉莉丝长期记住的内容"; wrapMode: Text.Wrap }
                        Button {
                            text: chatWindow.editingMemoryId ? "保存修改" : "保存为记忆卡片"
                            onClicked: {
                                if (chatWindow.editingMemoryId)
                                    backend.updateMemory(chatWindow.editingMemoryId, memoryTitle.text, memoryContent.text)
                                else
                                    backend.addMemory(memoryTitle.text, memoryContent.text, "事实")
                                chatWindow.editingMemoryId = ""
                                memoryTitle.text = ""
                                memoryContent.text = ""
                            }
                        }
                    }

                    ColumnLayout {
                        spacing: 10
                        Label {
                            Layout.fillWidth: true
                            text: "只有你主动收进盒子的选区会保存在这里；阅读卡不会自动进入主对话或长期记忆。"
                            wrapMode: Text.Wrap
                            color: "#6f675e"
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: readingQuery
                                Layout.fillWidth: true
                                placeholderText: "搜索原文、解释、追问或标题"
                                onAccepted: backend.searchReadingCards(text, readingKind.currentValue)
                                onTextChanged: if (!text) backend.searchReadingCards("", readingKind.currentValue)
                            }
                            ComboBox {
                                id: readingKind
                                Layout.preferredWidth: 112
                                textRole: "text"
                                valueRole: "value"
                                model: [
                                    { text: "全部", value: "" },
                                    { text: "简释", value: "explain" },
                                    { text: "翻译", value: "translate" },
                                    { text: "术语", value: "term" },
                                    { text: "追问", value: "ask" }
                                ]
                                onActivated: backend.searchReadingCards(readingQuery.text, currentValue)
                            }
                            Button { text: "搜索"; onClicked: backend.searchReadingCards(readingQuery.text, readingKind.currentValue) }
                        }
                        ListView {
                            id: readingCardList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 8
                            model: backend.readingItems
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view.width
                                height: 132
                                radius: 8
                                color: "#fffdf8"
                                border.color: "#d1c3ae"
                                border.width: 1
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 11
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: ({ explain: "简释", translate: "翻译", term: "术语", ask: "追问" })[modelData.kind] || modelData.kind
                                            color: "#a9473e"
                                            font.pixelSize: 11
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: modelData.title
                                            color: "#4d4a45"
                                            font.bold: true
                                            elide: Text.ElideRight
                                        }
                                        Button { text: "删除"; flat: true; onClicked: backend.deleteReadingCard(modelData.card_id) }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: modelData.source_text
                                        color: "#8a8177"
                                        font.pixelSize: 11
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        text: modelData.answer
                                        color: "#514c46"
                                        font.pixelSize: 13
                                        maximumLineCount: 3
                                        elide: Text.ElideRight
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: readingCardList.count === 0
                                text: "还没有收进盒子的论文卡片"
                                color: "#9b9287"
                            }
                        }
                    }

                    ScrollView {
                        id: companionSettingsScroll
                        objectName: "companionSettingsPage"
                        Layout.minimumWidth: 0
                        clip: true
                        // Basic Controls uses an overlay scrollbar. Reserve its
                        // paper strip as padding, then bind content to the
                        // resulting stable viewport instead of subtracting a
                        // second style-dependent width.
                        rightPadding: 9
                        contentWidth: availableWidth
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical: LiliesPaperScrollBar { }
                        function formatAutomaticWait(secondsValue) {
                            var total = Math.max(0, Math.ceil(Number(secondsValue || 0)))
                            var minutes = Math.floor(total / 60)
                            var seconds = total % 60
                            return (minutes < 10 ? "0" : "") + String(minutes)
                                    + ":" + (seconds < 10 ? "0" : "") + String(seconds)
                        }
                        function formatCaptureTime(value) {
                            var raw = String(value || "")
                            if (!raw.length)
                                return "时间未记录"
                            var parsed = new Date(raw)
                            if (isNaN(parsed.getTime()))
                                return "时间记录不可解析"
                            return Qt.formatDateTime(parsed, "MM-dd HH:mm")
                        }
                        function formatCompanionDelivery(deliveryValue) {
                            var delivery = deliveryValue || ({})
                            var unread = Math.max(0, Number(delivery.unreadCount || 0))
                            if (unread > 0)
                                return "1 条未读 · 已静默保留"
                            var state = String(delivery.state || "idle")
                            if (state === "waiting-present-ack")
                                return "等待显示确认"
                            if (state === "presented")
                                return "已呈现"
                            if (state === "suppressed")
                                return "静默保留 · 恢复安全界面后重显"
                            if (state === "interacted")
                                return "已阅读"
                            if (state === "dismissed")
                                return "已处理"
                            if (state === "expired")
                                return "已结束"
                            return "尚无待投递内容"
                        }
                        property bool frequencyDraftReady: false
                        property bool frequencyDraftDirty: false
                        property bool frequencyDraftSyncing: false
                        property bool frequencyDraftEditing: false
                        property bool frequencyDraftSyncPending: false
                        property bool frequencyDraftConflict: false
                        property string frequencyDraftError: ""
                        property string frequencyBaseline: "balanced"
                        property int customMinutesBaseline: 25
                        property int customDailyBaseline: 12
                        property string frequencyDraft: "balanced"
                        property int customMinutesDraft: 25
                        property int customDailyDraft: 12
                        function backendFrequencySnapshot() {
                            var preferences = backend.companionService.preferences || ({})
                            var savedMode = String(preferences.frequency || "balanced")
                            var rememberedMinimum = Number(
                                preferences.customMinimumMinutes)
                            var rememberedDailyLimit = Number(
                                preferences.customDailyLimit)
                            if (!isFinite(rememberedMinimum))
                                rememberedMinimum = savedMode === "custom"
                                    ? Number(preferences.minimumMinutes) : 25
                            if (!isFinite(rememberedDailyLimit))
                                rememberedDailyLimit = savedMode === "custom"
                                    ? Number(preferences.dailyLimit) : 12
                            return {
                                mode: savedMode,
                                customMinimum: Math.max(
                                    5, Math.min(180, Math.round(rememberedMinimum))),
                                customDailyLimit: Math.max(
                                    1, Math.min(50, Math.round(rememberedDailyLimit)))
                            }
                        }
                        function backendFrequencyDiffersFromBaseline(saved) {
                            return saved.mode !== frequencyBaseline
                                || saved.customMinimum !== customMinutesBaseline
                                || saved.customDailyLimit !== customDailyBaseline
                        }
                        function syncFrequencyDraftFromBackend(force) {
                            var saved = backendFrequencySnapshot()
                            var frequencyChanged = backendFrequencyDiffersFromBaseline(saved)
                            if (frequencyDraftDirty && !force) {
                                // preferencesChanged also covers interests, mix,
                                // category weights and screen-memory choices. A
                                // dirty frequency draft conflicts only when the
                                // committed frequency projection itself moved.
                                frequencyDraftConflict = frequencyChanged
                                frequencyDraftSyncPending = frequencyChanged
                                if (frequencyChanged)
                                    frequencyDraftError = ""
                                return false
                            }
                            if (frequencyDraftEditing && !force) {
                                frequencyDraftSyncPending = frequencyChanged
                                return false
                            }
                            var controlsReady = frequencyDraftReady
                            frequencyDraftSyncing = true
                            frequencyBaseline = saved.mode
                            customMinutesBaseline = saved.customMinimum
                            customDailyBaseline = saved.customDailyLimit
                            frequencyDraft = saved.mode
                            customMinutesDraft = saved.customMinimum
                            customDailyDraft = saved.customDailyLimit
                            // Interactive SpinBox edits can take ownership of
                            // their value property and detach its initial QML
                            // binding. Update live controls explicitly when a
                            // committed value is restored or changed outside
                            // this settings page.
                            if (controlsReady) {
                                customCompanionMinutes.value = saved.customMinimum
                                customCompanionDaily.value = saved.customDailyLimit
                            }
                            frequencyDraftDirty = false
                            frequencyDraftReady = true
                            frequencyDraftSyncPending = false
                            frequencyDraftConflict = false
                            frequencyDraftSyncing = false
                            frequencyDraftError = ""
                            return true
                        }
                        function noteFrequencyEditorTextChange() {
                            if (frequencyDraftSyncing || !frequencyDraftReady)
                                return
                            frequencyDraftError = ""
                            frequencyDraft = "custom"
                            // An external frequency commit can arrive after an
                            // untouched editor takes focus but before the first
                            // keystroke.  That path records a pending sync while
                            // preserving the editor.  Promote it to a real
                            // conflict as soon as the user starts changing the
                            // draft so ordinary Apply cannot silently overwrite
                            // the newer committed generation.
                            if (frequencyDraftSyncPending
                                    && backendFrequencyDiffersFromBaseline(
                                        backendFrequencySnapshot()))
                                frequencyDraftConflict = true
                            frequencyDraftDirty = true
                        }
                        function commitCustomFrequencyEditor(spinBox, fallback) {
                            var editor = spinBox.contentItem
                            var rawText = editor ? String(editor.text) : String(fallback)
                            var parsed = Number.fromLocaleString(spinBox.locale, rawText)
                            if (!isFinite(parsed))
                                parsed = Number(fallback)
                            var committed = Math.max(
                                Number(spinBox.from),
                                Math.min(Number(spinBox.to), Math.round(parsed)))
                            spinBox.value = committed
                            if (editor)
                                editor.text = spinBox.textFromValue(committed, spinBox.locale)
                            return committed
                        }
                        function updateFrequencyEditorFocus() {
                            var minutesEditor = customCompanionMinutes.contentItem
                            var dailyEditor = customCompanionDaily.contentItem
                            var settingsVisible = chatWindow.visible
                                    && Number(chatWindow.page) === 3
                            frequencyDraftEditing = Boolean(settingsVisible
                                && ((minutesEditor && minutesEditor.activeFocus)
                                    || (dailyEditor && dailyEditor.activeFocus)))
                            if (!frequencyDraftEditing
                                    && frequencyDraftSyncPending
                                    && !frequencyDraftDirty)
                                syncFrequencyDraftFromBackend(false)
                        }
                        function initializeFrequencyDraft() {
                            if (!frequencyDraftReady)
                                syncFrequencyDraftFromBackend(true)
                        }
                        function chooseFrequencyPreset(item) {
                            if (!item)
                                return
                            frequencyDraftError = ""
                            frequencyDraft = String(item.value)
                            if (frequencyDraft === "custom") {
                                frequencyDraftDirty = true
                                return
                            }
                            // Presets remain one-click actions. Hold the draft
                            // lock across the synchronous notify signal, then
                            // establish the committed backend value as the new
                            // baseline.
                            frequencyDraftDirty = true
                            var saved = backend.companionService.setFrequency(
                                frequencyDraft, item.minimum, item.daily)
                            if (!saved) {
                                frequencyDraftDirty = false
                                syncFrequencyDraftFromBackend(true)
                                frequencyDraftError = "保存失败，已恢复上一次设置"
                                return
                            }
                            frequencyDraftDirty = false
                            syncFrequencyDraftFromBackend(true)
                        }
                        function applyCustomFrequencyDraft(forceConflict) {
                            if (frequencyDraftConflict && forceConflict !== true) {
                                frequencyDraftError = "未应用：频率已在其他位置更新；请恢复最新设置，或选择“仍然应用”确认覆盖"
                                return false
                            }
                            // A mouse click on our flat paper button does not
                            // necessarily move focus out of an editable SpinBox.
                            // Commit both raw editors here so Apply never saves
                            // the last SpinBox value and then syncs over what the
                            // user just typed. Bounds remain enforced only at
                            // this commit point (or the editor's normal blur).
                            frequencyDraftSyncing = true
                            var committedMinutes = commitCustomFrequencyEditor(
                                customCompanionMinutes, customMinutesDraft)
                            var committedDaily = commitCustomFrequencyEditor(
                                customCompanionDaily, customDailyDraft)
                            customMinutesDraft = committedMinutes
                            customDailyDraft = committedDaily
                            frequencyDraft = "custom"
                            frequencyDraftSyncing = false
                            frequencyDraftDirty = true
                            frequencyDraftError = ""
                            var saved = backend.companionService.setFrequency(
                                "custom", customMinutesDraft, customDailyDraft)
                            if (!saved) {
                                frequencyDraftDirty = true
                                frequencyDraftError = "保存失败，修改仍保留；可以重试或恢复已保存"
                                return false
                            }
                            frequencyDraftDirty = false
                            frequencyDraftConflict = false
                            syncFrequencyDraftFromBackend(true)
                            return true
                        }
                        function restoreSavedFrequencyDraft() {
                            frequencyDraftDirty = false
                            frequencyDraftConflict = false
                            syncFrequencyDraftFromBackend(true)
                        }
                        Component.onCompleted: initializeFrequencyDraft()
                        Connections {
                            target: backend.companionService
                            function onPreferencesChanged() {
                                // Only committed preference updates reach the
                                // editor; activity heartbeats use the broader
                                // controller state signal.
                                companionSettingsScroll.syncFrequencyDraftFromBackend(false)
                            }
                        }
                        Connections {
                            target: chatWindow
                            function onVisibleChanged() {
                                companionSettingsScroll.updateFrequencyEditorFocus()
                            }
                            function onPageChanged() {
                                companionSettingsScroll.updateFrequencyEditorFocus()
                            }
                        }
                        ColumnLayout {
                            width: companionSettingsScroll.availableWidth
                            Layout.minimumWidth: 0
                            spacing: 14

                            GroupBox {
                                title: "主动陪伴"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    width: parent.width
                                    Rectangle {
                                        objectName: "companionModeSummary"
                                        Layout.fillWidth: true
                                        implicitHeight: companionModeSummaryColumn.implicitHeight + 20
                                        radius: 10
                                        color: "#f7f3eb"
                                        border.color: desktop.hairlineColor
                                        ColumnLayout {
                                            id: companionModeSummaryColumn
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 12
                                            anchors.rightMargin: 12
                                            spacing: 7
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Label {
                                                    text: "主动陪伴运行模式"
                                                    color: desktop.inkColor
                                                    font.bold: true
                                                }
                                                Item { Layout.fillWidth: true }
                                                Label {
                                                    objectName: "companionRunModeLabel"
                                                    text: !Boolean(backend.companionService.activityStatus.configuredEnabled)
                                                          ? "已关闭"
                                                          : (Boolean(backend.companionService.activityStatus.paused)
                                                             ? "已暂停" : "运行中")
                                                    color: !Boolean(backend.companionService.activityStatus.configuredEnabled)
                                                           || Boolean(backend.companionService.activityStatus.paused)
                                                           ? "#8b6b58" : desktop.focusColor
                                                    font.bold: true
                                                }
                                            }
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Label {
                                                    text: "智能屏幕理解"
                                                    color: desktop.inkColor
                                                    font.bold: true
                                                }
                                                Item { Layout.fillWidth: true }
                                                Label {
                                                    objectName: "companionSmartObservationStatusLabel"
                                                    text: Boolean(backend.companionService.activityStatus.smartObservationEnabled)
                                                          ? "已授权" : "未授权"
                                                    color: Boolean(backend.companionService.activityStatus.smartObservationEnabled)
                                                           ? desktop.focusColor : "#8b6b58"
                                                    font.bold: true
                                                }
                                                Button {
                                                    objectName: "companionSmartObservationAuthorizeButton"
                                                    visible: !Boolean(backend.companionService.activityStatus.smartObservationEnabled)
                                                    text: "了解并授权"
                                                    flat: true
                                                    onClicked: smartObservationConfirm.open()
                                                }
                                            }
                                        }
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Button {
                                            objectName: "companionRequestNowButton"
                                            text: backend.companionService.busy
                                                  ? "正在整理…" : "生成一条场景陪伴"
                                            enabled: !backend.companionService.busy
                                                     && !Boolean(backend.companionService.activityStatus.modalityProbeBusy)
                                            onClicked: backend.companionService.requestNow()
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: String(
                                                backend.companionService.activityStatus.observationModeLabel
                                                || "应用感知状态未知")
                                            color: Boolean(
                                                backend.companionService.activityStatus.configuredEnabled)
                                                && !Boolean(
                                                    backend.companionService.activityStatus.paused)
                                                ? desktop.focusColor : "#8b6b58"
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    Label {
                                        objectName: "companionRequestNowHint"
                                        Layout.fillWidth: true
                                        text: "这是只使用当前应用类别的场景级生成，不截图，也不是截图能力测试；仍会遵守暂停与敏感窗口静默。"
                                        color: "#746f67"
                                        font.pixelSize: 11
                                        lineHeight: 1.2
                                        wrapMode: Text.Wrap
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Button {
                                            objectName: "companionRequestScreenNowButton"
                                            text: "观察当前窗口一次"
                                            enabled: Boolean(
                                                         backend.companionService
                                                         .activityStatus.smartObservationEnabled)
                                                     && !backend.companionService.busy
                                                     && !Boolean(
                                                         backend.companionService
                                                         .activityStatus.modalityProbeBusy)
                                            onClicked: desktop.requestCurrentWindowObservation()
                                        }
                                        Item { Layout.fillWidth: true }
                                    }
                                    Label {
                                        objectName: "companionRequestScreenNowHint"
                                        Layout.fillWidth: true
                                        text: "只截取一次当前的非浏览器活动窗口；若授权、隐私规则、窗口状态或图像能力检查不通过，就直接失败，绝不退回泛化文字。点击后会先收起此窗口，约 350 毫秒后再观察，让原应用恢复前台。"
                                        color: "#746f67"
                                        font.pixelSize: 11
                                        lineHeight: 1.2
                                        wrapMode: Text.Wrap
                                    }
                                    Rectangle {
                                        objectName: "companionRequestFeedback"
                                        Layout.fillWidth: true
                                        implicitHeight: companionRequestFeedbackText.implicitHeight + 18
                                        radius: 9
                                        color: {
                                            var kind = String(
                                                backend.companionService.activityStatus.requestFeedbackKind
                                                || "ready")
                                            if (kind === "quiet" || kind === "warning")
                                                return "#fbf1e8"
                                            if (kind === "shown")
                                                return "#edf5f0"
                                            return "#f6f2eb"
                                        }
                                        border.color: {
                                            var kind = String(
                                                backend.companionService.activityStatus.requestFeedbackKind
                                                || "ready")
                                            if (kind === "quiet" || kind === "warning")
                                                return "#dfc1a8"
                                            if (kind === "shown")
                                                return "#bfd1c7"
                                            return desktop.hairlineColor
                                        }
                                        Label {
                                            id: companionRequestFeedbackText
                                            objectName: "companionRequestFeedbackText"
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 10
                                            anchors.rightMargin: 10
                                            text: String(
                                                backend.companionService.activityStatus.requestFeedback
                                                || "主动陪伴已就绪")
                                            color: "#665f57"
                                            font.pixelSize: 12
                                            lineHeight: 1.18
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: String(
                                            backend.companionService.activityStatus.observationModeDetail
                                            || "")
                                        color: "#746f67"
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "自动出现条件 · 当前窗口稳定约 2 分钟，并在你停顿 6–60 秒时轻声出现。默认不截图、不读取划词或窗口正文。"
                                        color: "#8a8177"
                                        font.pixelSize: 11
                                        lineHeight: 1.2
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        objectName: "companionEarliestAutomaticLabel"
                                        Layout.fillWidth: true
                                        property var opportunity: backend.companionService.activityStatus.automaticOpportunity || ({})
                                        text: {
                                            if (Boolean(opportunity.available))
                                                return "最早约 "
                                                        + companionSettingsScroll.formatAutomaticWait(
                                                            opportunity.waitSeconds)
                                                        + " 后可再自动出现"
                                            var reason = String(opportunity.blockReason || "")
                                            if (reason === "paused")
                                                return "自动出现已暂停；恢复后会重新计算最早时机"
                                            if (reason === "frequency-off")
                                                return "自动出现频率已关闭"
                                            if (reason === "daily-limit")
                                                return "今天的自动出现次数已用完"
                                            if (reason === "unread-pending")
                                                return "有 1 条未读陪伴；点击常驻对话入口即可重显"
                                            return "主动陪伴已关闭，不会自动出现"
                                        }
                                        color: desktop.focusColor
                                        font.bold: true
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        objectName: "companionEarliestAutomaticCaveat"
                                        Layout.fillWidth: true
                                        text: "这是三个等待条件给出的最早估计，不承诺届时一定触发；还要遇到自然停顿和非敏感窗口。"
                                        color: "#8a8177"
                                        font.pixelSize: 11
                                        lineHeight: 1.2
                                        wrapMode: Text.Wrap
                                    }
                                    Switch {
                                        objectName: "companionActivityEnabledSwitch"
                                        text: "根据当前应用与自然停顿主动说话"
                                        checked: Boolean(backend.companionService.activityStatus.enabled)
                                        onClicked: backend.companionService.setActivityEnabled(checked)
                                    }
                                    Switch {
                                        objectName: "companionPauseSwitch"
                                        text: "暂时暂停感知"
                                        checked: Boolean(backend.companionService.activityStatus.paused)
                                        onClicked: backend.companionService.setPaused(checked)
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "状态 · " + String(
                                                  backend.companionService.activityStatus.stateLabel || "等待")
                                              + (backend.companionService.activityStatus.stateDetail
                                                 ? ("\n" + String(backend.companionService.activityStatus.stateDetail))
                                                 : "")
                                              + "\n最近一次 · " + String(
                                                  backend.companionService.activityStatus.lastContextLabel
                                                  || "尚未发送")
                                        color: "#746f67"
                                        wrapMode: Text.Wrap
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            objectName: "companionLastCaptureStatusLabel"
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: {
                                                var status = backend.companionService.activityStatus
                                                var outcome = String(status.lastCaptureOutcome || "never")
                                                var outcomeLabels = {
                                                    "used": "模型已采用",
                                                    "submitted": "已提交给图像模型",
                                                    "skipped": "未截图",
                                                    "failed": "失败",
                                                    "cancelled": "已取消",
                                                    "discarded": "已丢弃",
                                                    "staged": "准备中",
                                                    "never": "尚未尝试"
                                                }
                                                if (outcome === "never")
                                                    return "最近截图 · 尚未尝试"
                                                var proof = ""
                                                if (Boolean(status.lastCapturePixelsUsed))
                                                    proof = " · 像素已用于生成"
                                                else if (Boolean(status.imageSubmitted))
                                                    proof = " · 像素已提交但未采用"
                                                else if (Boolean(status.captureAttempted))
                                                    proof = " · 像素未发送"
                                                var usedModel = String(status.lastCaptureModelLabel || "")
                                                if (usedModel.length > 0)
                                                    proof += " · 模型 " + usedModel
                                                if (Boolean(status.lastCapturePixelsUsed)) {
                                                    var confidence = String(
                                                        status.lastCaptureEvidenceConfidence || "none")
                                                    var confidenceLabels = {
                                                        "high": "高",
                                                        "medium": "中",
                                                        "low": "低"
                                                    }
                                                    if (confidenceLabels[confidence])
                                                        proof += " · 模型报告的画面把握："
                                                                + confidenceLabels[confidence]
                                                }
                                                return "最近截图 · " + String(outcomeLabels[outcome] || "状态已更新")
                                                        + (status.lastCaptureReason
                                                           ? (" · " + String(
                                                                   status.lastCaptureReasonLabel
                                                                   || "状态已更新")) : "")
                                                        + proof
                                                        + " · 发生于 "
                                                        + companionSettingsScroll.formatCaptureTime(
                                                            status.lastCaptureAt)
                                            }
                                            color: "#746f67"
                                            wrapMode: Text.Wrap
                                        }
                                        LiliesPaperButton {
                                            objectName: "companionModalityRecheckButton"
                                            text: Boolean(backend.companionService.activityStatus.modalityProbeBusy)
                                                  ? "检测中…" : "重新检测图像能力"
                                            enabled: Boolean(backend.companionService.activityStatus.smartObservationEnabled)
                                                     && !Boolean(backend.companionService.activityStatus.modalityProbeBusy)
                                                     && !backend.companionService.busy
                                            onClicked: backend.companionService.retrySmartObservationProbe()
                                        }
                                    }
                                    Label {
                                        objectName: "companionLastCapturePresentationStatusLabel"
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        visible: String(
                                                     backend.companionService.activityStatus
                                                     .lastCaptureOutcome || "never") !== "never"
                                        text: {
                                            var status = backend.companionService.activityStatus
                                            var presentation = String(
                                                status.lastCapturePresentationLabel
                                                || "最终呈现状态未知")
                                            var reason = String(
                                                status.lastCapturePresentationReasonLabel || "")
                                            return "最终呈现 · " + presentation
                                                    + (reason.length > 0
                                                       ? (" · " + reason) : "")
                                        }
                                        color: "#746f67"
                                        font.pixelSize: 11
                                        lineHeight: 1.2
                                        wrapMode: Text.Wrap
                                    }
                                    RowLayout {
                                        objectName: "companionDeliveryStatusRow"
                                        Layout.fillWidth: true
                                        Label {
                                            objectName: "companionDeliveryStatusLabel"
                                            Layout.fillWidth: true
                                            text: "投递 · " + companionSettingsScroll.formatCompanionDelivery(
                                                      backend.companionService.deliveryStatus)
                                            color: Number(backend.companionService.deliveryStatus.unreadCount || 0) > 0
                                                   ? "#8b5f3f" : "#746f67"
                                            font.bold: Number(
                                                backend.companionService.deliveryStatus.unreadCount || 0) > 0
                                            wrapMode: Text.Wrap
                                        }
                                        LiliesPaperButton {
                                            objectName: "companionReopenUnreadButton"
                                            text: "重新显示未读"
                                            enabled: Number(
                                                backend.companionService.deliveryStatus.unreadCount || 0) > 0
                                                && !Boolean(
                                                    backend.companionService.deliveryStatus.suppressed)
                                            onClicked: backend.companionService.reopenUnread()
                                        }
                                        LiliesPaperButton {
                                            objectName: "companionMarkUnreadReadButton"
                                            text: "标记已读"
                                            flat: true
                                            enabled: Number(
                                                backend.companionService.deliveryStatus.unreadCount || 0) > 0
                                            onClicked: backend.companionService.markUnreadRead()
                                        }
                                    }
                                    Label {
                                        objectName: "companionDeliveryPrivacyHint"
                                        Layout.fillWidth: true
                                        text: "这里只显示投递状态和未读数量，不显示气泡正文或窗口标题。"
                                        color: "#8a8177"
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                    GridLayout {
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        columns: 2
                                        columnSpacing: 10
                                        rowSpacing: 7
                                        Label { text: "频率" }
                                        ComboBox {
                                            id: companionFrequency
                                            objectName: "companionFrequencyDraft"
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            textRole: "text"
                                            valueRole: "value"
                                            model: [
                                                { text: "关闭", value: "off", minimum: 0, daily: 0 },
                                                { text: "安静", value: "quiet", minimum: 45, daily: 6 },
                                                { text: "平衡", value: "balanced", minimum: 25, daily: 12 },
                                                { text: "活泼", value: "lively", minimum: 10, daily: 30 },
                                                { text: "自定义", value: "custom", minimum: 25, daily: 12 }
                                            ]
                                            currentIndex: {
                                                var wanted = companionSettingsScroll.frequencyDraft
                                                for (var i = 0; i < model.length; ++i)
                                                    if (model[i].value === wanted) return i
                                                return 2
                                            }
                                            onActivated: function(index) {
                                                companionSettingsScroll.chooseFrequencyPreset(
                                                    model[index])
                                            }
                                        }
                                        Label {
                                            Layout.columnSpan: 2
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            text: companionFrequency.currentValue === "off"
                                                  ? "不会主动弹出"
                                                  : (companionFrequency.currentValue === "custom"
                                                     ? String(companionSettingsScroll.customMinutesDraft)
                                                       + " 分钟 / 日 "
                                                       + String(companionSettingsScroll.customDailyDraft)
                                                       + " 条 · 应用后生效"
                                                     : String(backend.companionService.preferences.minimumMinutes)
                                                       + " 分钟 / 日 "
                                                       + String(backend.companionService.preferences.dailyLimit) + " 条")
                                            color: "#746f67"
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                    Label {
                                        objectName: "companionFrequencySaveError"
                                        Layout.fillWidth: true
                                        visible: companionSettingsScroll.frequencyDraftError.length > 0
                                        text: companionSettingsScroll.frequencyDraftError
                                        color: "#9b4638"
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        objectName: "companionFrequencyConflictNotice"
                                        Layout.fillWidth: true
                                        visible: companionSettingsScroll.frequencyDraftConflict
                                        text: "频率已在其他位置更新。你的修改仍保留，普通“应用”不会覆盖新设置；可恢复最新设置，或明确选择“仍然应用”。"
                                        color: "#9b4638"
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                    GridLayout {
                                        visible: companionFrequency.currentValue === "custom"
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        columns: 2
                                        columnSpacing: 10
                                        rowSpacing: 7
                                        Label { text: "最短间隔" }
                                        SpinBox {
                                            id: customCompanionMinutes
                                            objectName: "customCompanionMinutesDraft"
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            from: 5; to: 180
                                            editable: true
                                            validator: IntValidator { bottom: -9999; top: 9999 }
                                            valueFromText: function(text, locale) {
                                                var parsed = Number.fromLocaleString(locale, text)
                                                if (!isFinite(parsed))
                                                    return value
                                                return Math.max(from, Math.min(to, Math.round(parsed)))
                                            }
                                            value: companionSettingsScroll.customMinutesDraft
                                            onValueChanged: {
                                                if (!companionSettingsScroll.frequencyDraftSyncing
                                                        && companionSettingsScroll.frequencyDraftReady
                                                        && companionFrequency.currentValue === "custom") {
                                                    companionSettingsScroll.customMinutesDraft = value
                                                    companionSettingsScroll.noteFrequencyEditorTextChange()
                                                }
                                            }
                                            Connections {
                                                target: customCompanionMinutes.contentItem
                                                ignoreUnknownSignals: true
                                                function onTextEdited() {
                                                    companionSettingsScroll.noteFrequencyEditorTextChange()
                                                }
                                                function onActiveFocusChanged() {
                                                    companionSettingsScroll.updateFrequencyEditorFocus()
                                                }
                                            }
                                            background: Rectangle {
                                                color: "#fffaf2"
                                                radius: 8
                                                border.width: customCompanionMinutes.activeFocus ? 2 : 1
                                                border.color: customCompanionMinutes.activeFocus
                                                              ? desktop.focusColor : desktop.hairlineColor
                                            }
                                        }
                                        Label { text: "每日上限" }
                                        SpinBox {
                                            id: customCompanionDaily
                                            objectName: "customCompanionDailyDraft"
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            from: 1; to: 50
                                            editable: true
                                            validator: IntValidator { bottom: -9999; top: 9999 }
                                            valueFromText: function(text, locale) {
                                                var parsed = Number.fromLocaleString(locale, text)
                                                if (!isFinite(parsed))
                                                    return value
                                                return Math.max(from, Math.min(to, Math.round(parsed)))
                                            }
                                            value: companionSettingsScroll.customDailyDraft
                                            onValueChanged: {
                                                if (!companionSettingsScroll.frequencyDraftSyncing
                                                        && companionSettingsScroll.frequencyDraftReady
                                                        && companionFrequency.currentValue === "custom") {
                                                    companionSettingsScroll.customDailyDraft = value
                                                    companionSettingsScroll.noteFrequencyEditorTextChange()
                                                }
                                            }
                                            Connections {
                                                target: customCompanionDaily.contentItem
                                                ignoreUnknownSignals: true
                                                function onTextEdited() {
                                                    companionSettingsScroll.noteFrequencyEditorTextChange()
                                                }
                                                function onActiveFocusChanged() {
                                                    companionSettingsScroll.updateFrequencyEditorFocus()
                                                }
                                            }
                                            background: Rectangle {
                                                color: "#fffaf2"
                                                radius: 8
                                                border.width: customCompanionDaily.activeFocus ? 2 : 1
                                                border.color: customCompanionDaily.activeFocus
                                                              ? desktop.focusColor : desktop.hairlineColor
                                            }
                                        }
                                        Label {
                                            Layout.columnSpan: 2
                                            Layout.fillWidth: true
                                            text: "允许范围 · 5–180 分钟 / 每日 1–50 条；应用或离开输入框时校正"
                                            color: "#8a8177"
                                            font.pixelSize: 11
                                            wrapMode: Text.Wrap
                                        }
                                        RowLayout {
                                            Layout.columnSpan: 2
                                            Layout.fillWidth: true
                                            LiliesPaperButton {
                                                objectName: "applyCustomCompanionFrequency"
                                                text: "应用"
                                                enabled: companionSettingsScroll.frequencyDraftDirty
                                                onClicked: companionSettingsScroll.applyCustomFrequencyDraft()
                                            }
                                            LiliesPaperButton {
                                                objectName: "restoreSavedCompanionFrequency"
                                                text: companionSettingsScroll.frequencyDraftConflict
                                                      ? "恢复最新" : "恢复已保存"
                                                flat: true
                                                enabled: companionSettingsScroll.frequencyDraftDirty
                                                onClicked: companionSettingsScroll.restoreSavedFrequencyDraft()
                                            }
                                            LiliesPaperButton {
                                                objectName: "forceApplyCustomCompanionFrequency"
                                                text: "仍然应用"
                                                visible: companionSettingsScroll.frequencyDraftConflict
                                                enabled: companionSettingsScroll.frequencyDraftDirty
                                                    && companionSettingsScroll.frequencyDraftConflict
                                                onClicked: companionSettingsScroll.applyCustomFrequencyDraft(true)
                                            }
                                            Item { Layout.fillWidth: true }
                                        }
                                    }
                                }
                            }

                            GroupBox {
                                objectName: "companionApplicationPolicyGroup"
                                title: "应用策略"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    width: parent.width
                                    spacing: 8
                                    Label {
                                        Layout.fillWidth: true
                                        text: "这里只显示脱敏后的应用标识与当前策略，不显示窗口标题或内容。安全敏感应用的默认静默不能被放宽。"
                                        color: "#8b6b58"
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        objectName: "companionApplicationPolicyEmpty"
                                        Layout.fillWidth: true
                                        visible: Number(
                                                     backend.companionService
                                                     .applicationPolicies.length || 0) === 0
                                        text: "还没有单独的应用策略。气泡中的“此应用静默”会在这里留下可撤销项。"
                                        color: "#746f67"
                                        wrapMode: Text.Wrap
                                    }
                                    Repeater {
                                        model: backend.companionService.applicationPolicies
                                        delegate: Rectangle {
                                            required property var modelData
                                            required property int index
                                            objectName: "companionApplicationPolicyRow_" + index
                                            Layout.fillWidth: true
                                            implicitHeight: companionPolicyRow.implicitHeight + 16
                                            radius: 9
                                            color: "#f7f3eb"
                                            border.color: desktop.hairlineColor
                                            RowLayout {
                                                id: companionPolicyRow
                                                anchors.fill: parent
                                                anchors.margins: 8
                                                spacing: 8
                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    spacing: 2
                                                    Label {
                                                        objectName: "companionApplicationPolicyIdentity_"
                                                                    + index
                                                        Layout.fillWidth: true
                                                        text: String(modelData.application || "application")
                                                        color: desktop.inkColor
                                                        font.bold: true
                                                        elide: Text.ElideMiddle
                                                        maximumLineCount: 1
                                                    }
                                                    Label {
                                                        objectName: "companionApplicationPolicyValue_"
                                                                    + index
                                                        Layout.fillWidth: true
                                                        text: String(modelData.policyLabel
                                                                     || modelData.policy || "默认")
                                                        color: Boolean(modelData.safetyLocked)
                                                               ? "#8b6b58" : "#746f67"
                                                        elide: Text.ElideRight
                                                        maximumLineCount: 1
                                                    }
                                                }
                                                Button {
                                                    objectName: "companionApplicationPolicyAllow_"
                                                                + index
                                                    text: "允许气泡"
                                                    enabled: !Boolean(modelData.safetyLocked)
                                                             && String(modelData.policy) !== "bubble"
                                                    onClicked: backend.companionService.setPolicy(
                                                                   String(modelData.application),
                                                                   "bubble")
                                                }
                                                Button {
                                                    objectName: "companionApplicationPolicyReset_"
                                                                + index
                                                    text: "恢复默认"
                                                    onClicked: backend.companionService.setPolicy(
                                                                   String(modelData.application),
                                                                   "default")
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            GroupBox {
                                title: "兴趣、场景与内容"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    width: parent.width
                                    RowLayout {
                                        Label { text: "兴趣 " + Math.round(companionInterest.value) }
                                        Slider {
                                            id: companionInterest
                                            objectName: "companionInterestWeight"
                                            Layout.fillWidth: true
                                            from: 0; to: 100; stepSize: 1
                                            value: backend.companionService.preferences.interestWeight === undefined
                                                   || backend.companionService.preferences.interestWeight === null
                                                   ? 60
                                                   : Number(backend.companionService.preferences.interestWeight)
                                            onMoved: backend.companionService.setMix(
                                                Math.round(value), Math.round(companionScene.value), Math.round(companionHalfLife.value))
                                        }
                                        Label { text: "场景 " + Math.round(companionScene.value) }
                                        Slider {
                                            id: companionScene
                                            objectName: "companionSceneWeight"
                                            Layout.fillWidth: true
                                            from: 0; to: 100; stepSize: 1
                                            value: backend.companionService.preferences.sceneWeight === undefined
                                                   || backend.companionService.preferences.sceneWeight === null
                                                   ? 40
                                                   : Number(backend.companionService.preferences.sceneWeight)
                                            onMoved: backend.companionService.setMix(
                                                Math.round(companionInterest.value), Math.round(value), Math.round(companionHalfLife.value))
                                        }
                                    }
                                    RowLayout {
                                        Label { text: "场景动量半衰期" }
                                        Slider {
                                            id: companionHalfLife
                                            Layout.fillWidth: true
                                            from: 5; to: 180; stepSize: 1
                                            value: Number(backend.companionService.preferences.momentumHalfLifeMinutes || 30)
                                            onMoved: backend.companionService.setMix(
                                                Math.round(companionInterest.value), Math.round(companionScene.value), Math.round(value))
                                        }
                                        Label { text: Math.round(companionHalfLife.value) + " 分钟" }
                                    }
                                    TextField {
                                        id: companionInterests
                                        objectName: "companionInterestsDraft"
                                        Layout.fillWidth: true
                                        placeholderText: "兴趣关键词，用逗号分隔：材料、AI、生物、宇宙……"
                                        property bool draftReady: false
                                        property bool draftDirty: false
                                        property bool draftSyncing: false
                                        function backendDraftText() {
                                            var saved = backend.companionService.preferences.interests
                                            return saved ? saved.join("，") : ""
                                        }
                                        function syncDraftFromBackend(force) {
                                            var activelyEditing = chatWindow.visible
                                                    && Number(chatWindow.page) === 3
                                                    && activeFocus
                                            if (!force && (draftDirty || activelyEditing))
                                                return false
                                            draftSyncing = true
                                            text = backendDraftText()
                                            draftDirty = false
                                            draftReady = true
                                            draftSyncing = false
                                            return true
                                        }
                                        function commitDraft() {
                                            if (!draftReady)
                                                syncDraftFromBackend(true)
                                            if (!draftDirty) {
                                                syncDraftFromBackend(false)
                                                return
                                            }
                                            backend.companionService.setInterests(text)
                                            draftDirty = false
                                            // Canonicalize separators and
                                            // de-duplication from the committed
                                            // controller snapshot only after the
                                            // user's draft has been persisted.
                                            syncDraftFromBackend(true)
                                        }
                                        text: ""
                                        Component.onCompleted: syncDraftFromBackend(true)
                                        onTextEdited: {
                                            if (!draftSyncing && draftReady)
                                                draftDirty = true
                                        }
                                        onAccepted: commitDraft()
                                        onEditingFinished: commitDraft()
                                        onActiveFocusChanged: {
                                            if (!activeFocus && !draftDirty)
                                                syncDraftFromBackend(false)
                                        }
                                        Connections {
                                            target: backend.companionService
                                            function onPreferencesChanged() {
                                                companionInterests.syncDraftFromBackend(false)
                                            }
                                        }
                                    }
                                    Repeater {
                                        model: ["科普", "吐槽", "笑话", "哲思", "新闻", "科研进展", "盒中世界"]
                                        delegate: RowLayout {
                                            required property var modelData
                                            property int savedWeight: {
                                                var weights = backend.companionService.preferences.categoryWeights || ({})
                                                return weights[modelData] === undefined ? 100 : Number(weights[modelData])
                                            }
                                            Layout.fillWidth: true
                                            CheckBox {
                                                text: modelData
                                                checked: parent.savedWeight > 0
                                                onClicked: backend.companionService.setCategoryWeight(modelData, checked ? 100 : 0)
                                            }
                                            Slider {
                                                Layout.fillWidth: true
                                                from: 0; to: 100; stepSize: 5
                                                value: parent.savedWeight
                                                onMoved: backend.companionService.setCategoryWeight(modelData, Math.round(value))
                                            }
                                            Label { text: parent.savedWeight; color: "#746f67" }
                                        }
                                    }
                                }
                            }

                            GroupBox {
                                title: "智能观察与本地记忆"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    width: parent.width
                                    Button {
                                        text: Boolean(backend.companionService.activityStatus.smartObservationEnabled)
                                              ? "关闭智能屏幕观察"
                                              : "查看说明并授权智能屏幕观察"
                                        onClicked: {
                                            if (Boolean(backend.companionService.activityStatus.smartObservationEnabled))
                                                backend.companionService.authorizeSmartObservation(false)
                                            else
                                                smartObservationConfirm.open()
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "默认关闭。自动陪伴在窗口稳定、自然停顿且本地隐私规则允许时，才会尝试一次非浏览器活动窗口截图；最长边 1600px，并发送给你已登录的 ChatGPT/Codex 订阅中的 Luna 或 Terra，请求结束立即清理暂存。手动“生成一条场景陪伴”只使用应用类别，不截图。浏览器像素观察在 v0.3.36 暂不开放；命中已知密码、支付、会议、远程桌面、隐私浏览、UAC、受保护内容或全屏游戏规则时会静默。"
                                        color: "#8b6b58"
                                        wrapMode: Text.Wrap
                                    }
                                    Button {
                                        objectName: "companionBrowserSingleCaptureButton"
                                        enabled: false
                                        text: "浏览器像素观察暂不开放"
                                    }
                                    Label {
                                        objectName: "companionBrowserCaptureWarning"
                                        Layout.fillWidth: true
                                        text: "普通网页、登录页与隐私窗口都不会进入像素截图路径。允许气泡的网页场景只提供“网页浏览”这一应用类别，不读取网页像素；隐私规则要求静默时则不会出现气泡。"
                                        color: "#8b5f3f"
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: backend.companionService.activityStatus.modality
                                              && backend.companionService.activityStatus.modality.imageModel
                                              ? ("图像模型 · " + backend.companionService.activityStatus.modality.imageModel)
                                              : "没有可用图像模型时，自动陪伴只使用应用级信号；单次窗口观察会直接失败，不生成泛化文字"
                                        color: "#746f67"
                                        wrapMode: Text.Wrap
                                    }
                                    RowLayout {
                                        Label { text: "观察记忆" }
                                        ComboBox {
                                            Layout.preferredWidth: 230
                                            textRole: "text"
                                            valueRole: "value"
                                            model: [
                                                { text: "只记发生过回复的对话", value: "replies" },
                                                { text: "显著或互动才记（默认）", value: "significant" },
                                                { text: "每次观察都记", value: "all" }
                                            ]
                                            currentIndex: {
                                                var wanted = String(backend.companionService.preferences.screenMemoryMode || "significant")
                                                for (var i = 0; i < model.length; ++i)
                                                    if (model[i].value === wanted) return i
                                                return 1
                                            }
                                            onActivated: function(index) {
                                                backend.companionService.setScreenMemoryMode(model[index].value)
                                            }
                                        }
                                    }
                                }
                            }

                            GroupBox {
                                title: "实时内容源"
                                Layout.fillWidth: true
                                ColumnLayout {
                                    width: parent.width
                                    Switch {
                                        text: "允许联网更新标题、来源、日期、链接与短摘要"
                                        checked: Boolean(backend.companionService.activityStatus.onlineContentEnabled)
                                        onClicked: backend.companionService.authorizeOnlineContent(checked)
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "默认关闭；不会保存完整文章。离线或来源过旧时，莉莉丝不会把内容称作“最新”。"
                                        color: "#8b6b58"
                                        wrapMode: Text.Wrap
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        TextField {
                                            id: customFeedLabel
                                            Layout.preferredWidth: 120
                                            placeholderText: "订阅名称"
                                        }
                                        TextField {
                                            id: customFeedUrl
                                            Layout.fillWidth: true
                                            placeholderText: "RSS / Atom 地址"
                                            onAccepted: {
                                                backend.companionService.addCustomSource(customFeedLabel.text, text)
                                                customFeedLabel.clear()
                                                clear()
                                            }
                                        }
                                        Button {
                                            text: "添加"
                                            onClicked: {
                                                backend.companionService.addCustomSource(customFeedLabel.text, customFeedUrl.text)
                                                customFeedLabel.clear()
                                                customFeedUrl.clear()
                                            }
                                        }
                                    }
                                    Repeater {
                                        model: backend.companionService.sources
                                        delegate: RowLayout {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            Label {
                                                Layout.fillWidth: true
                                                text: modelData.label
                                                color: "#5f5952"
                                                elide: Text.ElideRight
                                            }
                                            Button {
                                                text: "更新"
                                                enabled: Boolean(backend.companionService.activityStatus.onlineContentEnabled)
                                                onClicked: backend.companionService.refreshSource(modelData.id)
                                            }
                                            Button {
                                                visible: Boolean(modelData.custom)
                                                text: "移除"
                                                flat: true
                                                onClicked: backend.companionService.removeCustomSource(modelData.id)
                                            }
                                        }
                                    }
                                }
                            }
                            Item { Layout.fillHeight: true }
                        }
                    }

                    ScrollView {
                        id: mainSettingsScroll
                        objectName: "mainSettingsPage"
                        Layout.minimumWidth: 0
                        clip: true
                        rightPadding: 9
                        contentWidth: availableWidth
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                        ScrollBar.vertical: LiliesPaperScrollBar { }
                        ColumnLayout {
                        width: mainSettingsScroll.availableWidth
                        Layout.minimumWidth: 0
                        spacing: 14
                        GroupBox {
                            id: desktopShellDiscoveryCard
                            objectName: "compactDesktopDiscoveryCard"
                            title: "完整莉莉丝桌面"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                spacing: 8
                                Label {
                                    objectName: "desktopShellDescription"
                                    Layout.fillWidth: true
                                    text: backend.shellMode === "compact"
                                          ? "完整动态桌面没有消失；当前只是收成了不挡工作的透明桌宠。"
                                          : "当前正在使用完整莉莉丝桌面；切回桌宠不会关闭已经打开的应用。"
                                    color: backend.shellMode === "compact" ? "#8b6b58" : "#4c7466"
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    LiliesPaperButton {
                                        id: desktopShellToggleButton
                                        objectName: "compactDesktopShellToggle"
                                        text: backend.shellMode === "compact" ? "展开莉莉丝桌面" : "收成透明桌宠"
                                        onClicked: backend.toggleDesktopShell()
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "这是桌面外壳切换；不会关闭或重开当前应用。"
                                        color: "#746f67"
                                        wrapMode: Text.Wrap
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "它与“看桌面／返回工作”不同：后者只临时最小化并恢复本轮窗口。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        GroupBox {
                            title: "常用功能库"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                spacing: 8
                                Label {
                                    Layout.fillWidth: true
                                    text: "对话、盒中世界、设置始终常驻。其余功能由你选择，最多 3 个；顺序决定环上的常用槽位。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    spacing: 8
                                    Repeater {
                                        model: ["对话 · 常驻", "盒中世界 · 常驻", "设置 · 常驻"]
                                        delegate: Rectangle {
                                            required property var modelData
                                            implicitWidth: coreLabel.implicitWidth + 20
                                            implicitHeight: 30
                                            radius: 15
                                            color: "#f1ebe2"
                                            border.color: "#c8bbab"
                                            Label { id: coreLabel; anchors.centerIn: parent; text: modelData; color: "#625b53" }
                                        }
                                    }
                                }
                                Repeater {
                                    model: backend.functionCatalog
                                    delegate: RowLayout {
                                        id: functionChoice
                                        objectName: "functionLibraryChoice_" + modelData.action
                                        required property var modelData
                                        readonly property int quickActionIndex: {
                                            var selected = backend.quickActions || []
                                            for (var selectedIndex = 0;
                                                 selectedIndex < selected.length;
                                                 ++selectedIndex) {
                                                if (String(selected[selectedIndex].action || "")
                                                        === String(modelData.action || ""))
                                                    return selectedIndex
                                            }
                                            return -1
                                        }
                                        Layout.fillWidth: true
                                        Layout.minimumWidth: 0
                                        CheckBox {
                                            objectName: "functionLibraryPin_"
                                                        + String(functionChoice.modelData.action)
                                            text: ""
                                            Accessible.name: "固定到常用功能："
                                                             + String(functionChoice.modelData.label || "功能")
                                            Layout.minimumWidth: 34
                                            Layout.preferredWidth: 34
                                            checked: Boolean(functionChoice.modelData.selected)
                                            // The radial menu has three fixed actions and
                                            // exactly three user-selectable slots.  Keep
                                            // selected rows enabled so they can be removed,
                                            // but do not let a rejected fourth click leave
                                            // the control looking checked locally.
                                            enabled: Boolean(functionChoice.modelData.selected)
                                                     || Math.max(0, backend.quickActions.length - 3) < 3
                                            onClicked: {
                                                backend.setQuickActionPinned(
                                                    functionChoice.modelData.action, checked)
                                                // Refresh the radial Repeater in the
                                                // same UI transaction. Some Qt/PySide
                                                // builds cache QVariantList notify
                                                // values until the next event turn.
                                                compactWindow.rebuildQuickActionModel()
                                            }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            Layout.minimumWidth: 0
                                            spacing: 2
                                            Label {
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                text: String(functionChoice.modelData.label || "功能")
                                                color: desktop.inkColor
                                                font.weight: Font.Medium
                                                elide: Text.ElideRight
                                            }
                                            Label {
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                text: String(functionChoice.modelData.description || "")
                                                color: "#82796f"
                                                wrapMode: Text.Wrap
                                                maximumLineCount: 2
                                                elide: Text.ElideRight
                                            }
                                            Label {
                                                objectName: "functionLibraryStatus_" + functionChoice.modelData.action
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                visible: functionChoice.modelData.action === "companion"
                                                text: visible
                                                      ? String(backend.companionService.activityStatus.compactStatusLabel
                                                               || "陪伴 · 等待")
                                                      : ""
                                                color: backend.companionService.activityStatus.configuredEnabled
                                                       && !backend.companionService.activityStatus.paused
                                                       ? desktop.focusColor : "#8b6b58"
                                                font.pixelSize: 11
                                            }
                                        }
                                        LiliesPaperButton {
                                            objectName: "functionLibraryOpen_" + functionChoice.modelData.action
                                            visible: functionChoice.modelData.action === "companion"
                                            text: "打开"
                                            Accessible.name: "打开主动陪伴设置"
                                            Layout.minimumWidth: 52
                                            Layout.preferredWidth: 52
                                            onClicked: chatWindow.page = 3
                                        }
                                        LiliesPaperButton {
                                            objectName: "functionLibraryMoveUp_"
                                                        + functionChoice.modelData.action
                                            text: "↑"
                                            // The first three slots are fixed.  The first
                                            // optional action is therefore already at its
                                            // upper boundary when its absolute index is 3.
                                            enabled: Boolean(functionChoice.modelData.selected)
                                                     && functionChoice.quickActionIndex > 3
                                            flat: true
                                            Layout.minimumWidth: 30
                                            Layout.preferredWidth: 30
                                            onClicked: backend.moveQuickAction(functionChoice.modelData.action, -1)
                                        }
                                        LiliesPaperButton {
                                            objectName: "functionLibraryMoveDown_"
                                                        + functionChoice.modelData.action
                                            text: "↓"
                                            enabled: Boolean(functionChoice.modelData.selected)
                                                     && functionChoice.quickActionIndex >= 3
                                                     && functionChoice.quickActionIndex
                                                        < backend.quickActions.length - 1
                                            flat: true
                                            Layout.minimumWidth: 30
                                            Layout.preferredWidth: 30
                                            onClicked: backend.moveQuickAction(functionChoice.modelData.action, 1)
                                        }
                                    }
                                }
                                RowLayout {
                                    LiliesPaperButton { text: "清空可选常用"; onClicked: backend.clearQuickActions() }
                                    LiliesPaperButton { text: "恢复环形位置"; onClicked: backend.resetComponentLayouts() }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: "已选择 " + String(Math.max(0, backend.quickActions.length - 3)) + " / 3"
                                        color: "#746f67"
                                    }
                                }
                            }
                        }
                        GroupBox {
                            title: "自主移动"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                RowLayout {
                                    Label { text: "拖动方式" }
                                    ComboBox {
                                        Layout.preferredWidth: 210
                                        model: ["Windows 原生拖动（推荐）", "直接跟手（兼容）"]
                                        currentIndex: backend.petDragMode === "system" ? 0 : 1
                                        onActivated: backend.setPetDragMode(
                                            ["system", "direct"][currentIndex])
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: backend.petDragMode === "system"
                                          ? "由 Windows 和桌面合成器直接接管，跟手最稳定；系统拒绝时自动回退。"
                                          : "兼容模式按渲染帧合并鼠标事件；仅建议在原生拖动不可用时选择。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Label { text: "避开鼠标" }
                                    ComboBox {
                                        Layout.preferredWidth: 180
                                        model: ["关闭", "轻微（默认）", "活泼"]
                                        currentIndex: backend.petAvoidanceMode === "off" ? 0
                                                      : (backend.petAvoidanceMode === "lively" ? 2 : 1)
                                        onActivated: backend.setPetAvoidanceMode(["off", "gentle", "lively"][currentIndex])
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "光标快速靠近但尚未碰到莉莉丝时，她会跳到较少打扰的屏幕边缘；移动后至少停留 3 秒，人物始终完整留在屏幕内，也不会在菜单展开或按下时逃走。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        GroupBox {
                            title: "看桌面／返回工作"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                RowLayout {
                                    Button {
                                        text: backend.desktopPeekStatus.active ? "返回工作" : "看桌面"
                                        onClicked: backend.toggleDesktopPeek()
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: backend.desktopPeekStatus.active
                                              ? ("已临时收起 " + String(backend.desktopPeekStatus.windowCount || backend.desktopPeekStatus.minimized || 0) + " 个窗口")
                                              : "保持当前桌面模式不变，只临时收起本次仍在显示的窗口"
                                        color: backend.desktopPeekStatus.active ? "#9f3129" : "#4c7466"
                                        wrapMode: Text.Wrap
                                    }
                                }
                                RowLayout {
                                    Label { text: "全局快捷键" }
                                    TextField {
                                        id: desktopPeekHotkeyInput
                                        Layout.preferredWidth: 180
                                        text: backend.desktopPeekHotkey
                                        placeholderText: "Ctrl+Alt+D"
                                        onAccepted: backend.setDesktopPeekHotkey(text)
                                    }
                                    Button { text: "保存"; onClicked: backend.setDesktopPeekHotkey(desktopPeekHotkeyInput.text) }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "第二次触发只恢复本次由 Lilies 收起、身份仍匹配且仍处于最小化状态的窗口；期间新开的窗口和你手动恢复的窗口不动。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        GroupBox {
                            title: "私有数据"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                Label {
                                    Layout.fillWidth: true
                                    text: backend.privateDataPath
                                    color: "#4c7466"
                                    wrapMode: Text.WrapAnywhere
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "迁移状态 · " + String(backend.dataMigrationStatus.status || "等待")
                                          + (backend.dataMigrationStatus.integrity
                                             ? " · SQLite " + String(backend.dataMigrationStatus.integrity) : "")
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "聊天、分区记忆、截图暂存、内容缓存与运行日志只写入 F 盘；F 盘不可用时进入受限恢复，不会静默回退到 C 盘。"
                                    color: "#8b6b58"
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        GroupBox {
                            title: "动态桌面"
                            Layout.fillWidth: true
                            ColumnLayout {
                                RowLayout {
                                    Button { text: "实时纸雕"; enabled: backend.renderer !== "scene2d"; onClicked: backend.setRenderer("scene2d") }
                                    Button { text: "电影循环"; enabled: backend.renderer !== "video"; onClicked: backend.setRenderer("video") }
                                    Button { text: "重播初遇"; onClicked: backend.replayIntro() }
                                }
                                Label {
                                    text: "当前渲染 · " + Number(backend.frameRate).toFixed(0) + " FPS"
                                          + (backend.sceneActive ? "" : " · 已因完全遮挡暂停")
                                    color: backend.sceneActive ? "#4c7466" : "#8b6b58"
                                }
                                RowLayout {
                                    Label { text: "图标布局" }
                                    ComboBox {
                                        id: layoutPicker
                                        Layout.preferredWidth: 170
                                        model: backend.desktopLayouts
                                        textRole: "name"
                                        currentIndex: {
                                            for (var i = 0; i < model.length; ++i)
                                                if (model[i].layoutId === backend.activeDesktopLayout) return i
                                            return 0
                                        }
                                        onActivated: backend.activateDesktopLayout(model[index].layoutId)
                                    }
                                    TextField { id: newLayoutName; Layout.preferredWidth: 130; placeholderText: "新布局名称" }
                                }
                                RowLayout {
                                    Button {
                                        text: "复制新建"
                                        onClicked: { backend.createDesktopLayout(newLayoutName.text); newLayoutName.text = "" }
                                    }
                                    Button { text: "删除布局"; onClicked: backend.deleteDesktopLayout(backend.activeDesktopLayout) }
                                    Button { text: "恢复隐藏图标"; onClicked: backend.unhideAllIcons() }
                                    Button { text: "添加扫描目录"; onClicked: extraRootDialog.open() }
                                }
                            }
                        }
                        GroupBox {
                            title: "桌宠浮层"
                            Layout.fillWidth: true
                            ColumnLayout {
                                ButtonGroup { id: petFloatModeGroup }
                                RowLayout {
                                    RadioButton {
                                        text: "始终置顶（默认）"
                                        checked: backend.petFloatMode === "always"
                                        ButtonGroup.group: petFloatModeGroup
                                        onClicked: backend.setPetFloatMode("always")
                                    }
                                    RadioButton {
                                        text: "普通窗口"
                                        checked: backend.petFloatMode === "normal"
                                        ButtonGroup.group: petFloatModeGroup
                                        onClicked: backend.setPetFloatMode("normal")
                                    }
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    implicitHeight: petPresenceStatusRow.implicitHeight + 20
                                    radius: 10
                                    color: Qt.alpha(desktop.petPresenceStatusColor, 0.075)
                                    border.width: 1
                                    border.color: Qt.alpha(desktop.petPresenceStatusColor, 0.24)

                                    RowLayout {
                                        id: petPresenceStatusRow
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 9

                                        Rectangle {
                                            Layout.alignment: Qt.AlignTop
                                            Layout.topMargin: 5
                                            width: 8
                                            height: 8
                                            radius: 4
                                            color: desktop.petPresenceStatusColor
                                        }
                                        Label {
                                            objectName: "petPresenceStatusLabel"
                                            Layout.fillWidth: true
                                            text: desktop.petPresenceStatusText
                                            color: desktop.petPresenceStatusColor
                                            font.pixelSize: 13
                                            font.weight: Font.Medium
                                            lineHeight: 1.22
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: backend.petFloatMode === "always"
                                          ? "莉莉丝会浮在 WPS、浏览器等界面上；人物、盒子和功能可点击，周围透明区域不拦截操作。"
                                          : "莉莉丝仍可点击，但遵循普通窗口层级，可以被其他应用自然遮挡。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "全屏游戏、会议和受保护界面仍会触发静默保护；这不会改变置顶模式，也不会中断已经开始的专注计时。"
                                    color: "#8e887e"
                                    font.pixelSize: 12
                                    lineHeight: 1.18
                                    wrapMode: Text.Wrap
                                }
                            }
                        }
                        GroupBox {
                            title: "Agent 权限"
                            Layout.fillWidth: true
                            ColumnLayout {
                                RowLayout {
                                    Label { text: "模式" }
                                    ComboBox {
                                        model: ["谨慎", "标准", "信任白名单"]
                                        currentIndex: ["cautious", "standard", "trusted"].indexOf(backend.permissionMode)
                                        onActivated: backend.setPermissionMode(["cautious", "standard", "trusted"][currentIndex])
                                    }
                                }
                                Label {
                                    visible: backend.permissionMode === "trusted"
                                    text: "白名单只影响写操作；破坏性动作仍会逐次确认。"
                                    color: "#746f67"
                                }
                                Repeater {
                                    model: backend.trustedActions
                                    delegate: CheckBox {
                                        required property var modelData
                                        visible: backend.permissionMode === "trusted"
                                        text: modelData.title + " · " + modelData.key
                                        checked: Boolean(modelData.allowed)
                                        onClicked: backend.setTrustedAction(modelData.key, checked)
                                    }
                                }
                            }
                        }
                        GroupBox {
                            title: "莉莉丝对话模型"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                Layout.minimumWidth: 0
                                Label {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: backend.modelStatus.subscriptionReady
                                          ? (backend.modelStatus.model + " · GPT 订阅主模型")
                                          : (backend.modelStatus.fallbackAvailable ? (backend.modelStatus.fallbackModel + " · 本地备用") : "没有可用的对话模型")
                                    color: backend.modelStatus.modelInstalled ? "#4c7466" : "#9f3129"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: backend.modelStatus.subscriptionReady
                                          ? "记忆卡片与最近对话由本机注入；每次 GPT 会话相互隔离"
                                          : ("本机 CPU · " + (backend.modelStatus.context || 2048) + " 上下文 · 无需下载")
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    text: backend.modelStatus.subscriptionReady
                                          ? ("ChatGPT " + (backend.modelStatus.subscriptionPlan || "订阅") + " · Terra-medium · 空闲五分钟后卸载")
                                          : "发起对话前检测到 GPT 不可用，将使用本机 0.5B 离线备用"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                    objectName: "modelFallbackDescription"
                                    text: "主对话由 GPT 模拟莉莉丝；发起一轮前若检测到断网或订阅不可用，本机 Qwen2.5 0.5B 才接替。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Button { text: "检测"; onClicked: backend.refreshModelStatus() }
                                    Button { text: "本地 0.5B 备用"; enabled: false }
                                }
                            }
                        }
                        GroupBox {
                            title: "论文划词 · Luna-medium"
                            Layout.fillWidth: true
                            ColumnLayout {
                                width: parent.width
                                Switch {
                                    text: "鼠标划选文字后自动解释"
                                    checked: backend.selectionEnabled
                                    onClicked: backend.setSelectionEnabled(checked)
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: backend.selectionStatus
                                    color: backend.selectionSubscriptionReady && backend.selectionEnabled ? "#4c7466" : "#8b6b58"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "每次只发送这一次划选的文字，不带聊天记录或论文上下文。只监听常见浏览器、PDF 与论文阅读器，不会干扰游戏；程序会短暂复制选区并立即恢复原剪贴板。"
                                    color: "#746f67"
                                    wrapMode: Text.Wrap
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: "划选内容会通过本机 Codex App Server 发送给 OpenAI，并计入你的 GPT/Codex 订阅用量；密码管理器中的划选会被忽略。"
                                    color: "#8b6b58"
                                    wrapMode: Text.Wrap
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: backend.selectionSubscriptionReady
                                              ? "已使用 ChatGPT 订阅登录 · 无需 API Key"
                                              : "请先打开 ChatGPT/Codex 并登录你的 GPT 订阅"
                                        color: backend.selectionSubscriptionReady ? "#4c7466" : "#8b6b58"
                                        wrapMode: Text.Wrap
                                    }
                                    Button {
                                        text: "重新检测订阅"
                                        onClicked: backend.refreshSelectionSubscription()
                                    }
                                }
                            }
                        }
                        GroupBox {
                            title: "恢复"
                            Layout.fillWidth: true
                            ColumnLayout {
                                Label { text: "紧急恢复：Ctrl + Alt + Shift + F12"; color: "#746f67" }
                                Label {
                                    text: backend.shellHealth.message || "尚未运行健康检查"
                                    color: backend.shellHealth.ok ? "#4c7466" : "#8b6b58"
                                }
                                RowLayout {
                                    Button { text: "临时显示 Windows 系统栏"; onClicked: backend.revealSystemDrawer() }
                                    Button { text: "运行视觉模式健康检查"; onClicked: backend.runShellHealthCheck() }
                                    Button { text: "退出并恢复 Windows"; onClicked: backend.exitAndRestore() }
                                }
                                RowLayout {
                                    Button {
                                        text: "下次登录启用实验外壳"
                                        enabled: backend.shellHealth.ok && !backend.loginShellEnabled
                                        onClicked: loginShellConfirm.open()
                                    }
                                    Button {
                                        text: "恢复原登录外壳"
                                        enabled: backend.loginShellEnabled
                                        onClicked: backend.disableLoginShell()
                                    }
                                }
                            }
                        }
                        Item { Layout.fillHeight: true }
                        Label { text: "组件接口 · " + backend.socketEndpoint; color: "#8b857b" }
                        }
                    }

                    ColumnLayout {
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: "分区记忆地图 · 原始对话先进入“待归档”，后台空闲时再由 Luna 整理。"
                                color: "#6f675e"
                                wrapMode: Text.Wrap
                            }
                            Button { text: "旧记忆卡"; onClicked: { chatWindow.page = 1; backend.refreshMemory() } }
                            Button { text: "重建索引"; onClicked: backend.reindexMemory() }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Button {
                                text: "全部分区"
                                checkable: true
                                checked: chatWindow.selectedMemoryPartition === ""
                                onClicked: {
                                    chatWindow.selectedMemoryPartition = ""
                                    backend.refreshMemoryMap("")
                                }
                            }
                            ComboBox {
                                id: memoryPartitionPicker
                                Layout.fillWidth: true
                                textRole: "name"
                                model: backend.memoryPartitions
                                currentIndex: {
                                    for (var i = 0; i < model.length; ++i)
                                        if (model[i].partition_id === chatWindow.selectedMemoryPartition) return i
                                    return 0
                                }
                                displayText: chatWindow.selectedMemoryPartition === ""
                                             ? "选择分区筛选"
                                             : (currentIndex >= 0 && model[currentIndex]
                                                ? model[currentIndex].name + " · " + model[currentIndex].available + " 条"
                                                : "选择分区筛选")
                                onActivated: function(index) {
                                    chatWindow.selectedMemoryPartition = model[index].partition_id
                                    backend.refreshMemoryMap(chatWindow.selectedMemoryPartition)
                                }
                            }
                            Label {
                                text: String((backend.memoryMap.fragments || []).length) + " 条可审阅记录"
                                color: "#746f67"
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            Repeater {
                                model: backend.memoryPartitions
                                delegate: Label {
                                    required property var modelData
                                    text: modelData.name + " " + modelData.available
                                    color: modelData.partition_id === chatWindow.selectedMemoryPartition ? "#9f3129" : "#8a8177"
                                    font.pixelSize: 10
                                }
                            }
                        }
                        ListView {
                            id: memoryFragmentList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            spacing: 8
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                            model: backend.memoryMap.fragments || []
                            delegate: Rectangle {
                                required property var modelData
                                width: ListView.view ? ListView.view.width : 0
                                height: 148
                                radius: 14
                                color: Boolean(modelData.forgotten) ? "#eee9e1" : "#fffdf8"
                                border.color: modelData.partition_id === "identity" ? "#a9c9c2" : "#d1c3ae"
                                border.width: 1
                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 4
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label {
                                            text: {
                                                for (var i = 0; i < backend.memoryPartitions.length; ++i)
                                                    if (backend.memoryPartitions[i].partition_id === modelData.partition_id)
                                                        return backend.memoryPartitions[i].name
                                                return modelData.partition_id
                                            }
                                            color: "#6f817c"
                                            font.bold: true
                                        }
                                        Label {
                                            Layout.fillWidth: true
                                            text: String(modelData.source_type || "本地")
                                                  + (modelData.canon_kind === "canon" ? " · Canon"
                                                     : modelData.canon_kind === "shared" ? " · 共同故事" : "")
                                            color: "#8a8177"
                                            elide: Text.ElideRight
                                        }
                                        Label {
                                            text: Boolean(modelData.forgotten) ? "已忘记" : "可召回"
                                            color: Boolean(modelData.forgotten) ? "#9f3129" : "#4c7466"
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: String(modelData.summary || modelData.content || "")
                                        color: "#4d4a45"
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 3
                                        elide: Text.ElideRight
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: modelData.lastRecall && Boolean(modelData.lastRecall.reason)
                                        text: "最近查阅 · " + String(modelData.lastRecall.reason)
                                        color: "#8a8177"
                                        font.pixelSize: 10
                                        elide: Text.ElideRight
                                    }
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Label { text: "改分区"; color: "#746f67"; font.pixelSize: 11 }
                                        ComboBox {
                                            Layout.preferredWidth: 140
                                            textRole: "name"
                                            model: backend.memoryPartitions
                                            currentIndex: {
                                                for (var i = 0; i < model.length; ++i)
                                                    if (model[i].partition_id === modelData.partition_id) return i
                                                return 0
                                            }
                                            onActivated: function(index) {
                                                backend.moveMemoryFragment(modelData.fragment_id, model[index].partition_id)
                                                backend.refreshMemoryMap(chatWindow.selectedMemoryPartition)
                                            }
                                        }
                                        Item { Layout.fillWidth: true }
                                        Button {
                                            text: "忘记"
                                            enabled: !Boolean(modelData.forgotten)
                                            onClicked: backend.forgetMemoryFragment(modelData.fragment_id, false)
                                        }
                                        Button {
                                            text: "删除原始记录…"
                                            onClicked: {
                                                chatWindow.pendingDeleteFragmentId = modelData.fragment_id
                                                chatWindow.pendingDeleteFragmentSummary = String(modelData.summary || modelData.content || "").slice(0, 160)
                                                deleteMemorySourceConfirm.open()
                                            }
                                        }
                                    }
                                }
                            }
                            Label {
                                anchors.centerIn: parent
                                visible: memoryFragmentList.count === 0
                                text: "这个分区还没有可显示的记忆"
                                color: "#9b9287"
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "“忘记”只从检索中排除，聊天历史仍保留；删除原对话会再次确认。历史文本始终按不可信数据处理。"
                            color: "#8b6b58"
                            wrapMode: Text.Wrap
                            font.pixelSize: 11
                        }
                    }
                }
            }
        }

        Dialog {
            id: loginShellConfirm
            anchors.centerIn: parent
            modal: true
            title: "启用实验登录外壳"
            standardButtons: Dialog.Ok | Dialog.Cancel
            onAccepted: backend.enableLoginShell()
            Label {
                width: 380
                wrapMode: Text.Wrap
                text: "这会在下一次 Windows 登录时启动 Lilies，并保留 Explorer 作为失败回退。紧急恢复快捷键仍为 Ctrl + Alt + Shift + F12。确认继续吗？"
            }
        }

        Dialog {
            id: smartObservationConfirm
            title: "授权智能屏幕观察"
            modal: true
            // Basic Dialog normally derives implicitWidth from contentItem;
            // this wrapped paragraph in turn derives its implicit size from
            // the dialog's available width.  Give the paper confirmation a
            // stable intrinsic width and keep only the actual width adaptive.
            implicitWidth: 540
            width: Math.min(implicitWidth, Math.max(320, chatWindow.width - 24))
            contentWidth: Math.min(500, Math.max(280, chatWindow.width - 60))
            standardButtons: Dialog.Yes | Dialog.No
            onAccepted: backend.companionService.authorizeSmartObservation(true)
            contentItem: Label {
                width: smartObservationConfirm.contentWidth
                padding: 16
                wrapMode: Text.Wrap
                text: "启用后，自动陪伴会在窗口稳定、自然停顿且本地隐私规则允许时，偶尔尝试一次非浏览器活动窗口截图。手动“生成一条场景陪伴”始终是应用类别级生成，不截图。图片会缩到最长边 1600px，并发送给你已登录的 ChatGPT/Codex 订阅中的 Luna；若 Luna 不支持图片则尝试 Terra。截图只暂存在 F 盘，请求完成、失败或取消后都会删除。浏览器像素观察在 v0.3.36 暂不开放；命中已知敏感窗口规则时会跳过，但规则识别仍有边界，授权前请确认当前使用环境。\n\n是否授权自动观察？"
            }
        }

        Dialog {
            id: deleteMemorySourceConfirm
            anchors.centerIn: parent
            modal: true
            title: "同时删除原始记录？"
            standardButtons: Dialog.Ok | Dialog.Cancel
            onAccepted: {
                backend.forgetMemoryFragment(chatWindow.pendingDeleteFragmentId, true)
                chatWindow.pendingDeleteFragmentId = ""
                chatWindow.pendingDeleteFragmentSummary = ""
            }
            onRejected: {
                chatWindow.pendingDeleteFragmentId = ""
                chatWindow.pendingDeleteFragmentSummary = ""
            }
            Label {
                width: 400
                wrapMode: Text.Wrap
                text: "这会删除对应的原始聊天消息或来源记录，无法从 Lilies 的本地数据库恢复。\n\n"
                      + chatWindow.pendingDeleteFragmentSummary
            }
        }

        FolderDialog {
            id: extraRootDialog
            title: "添加到 Lilies 的应用与文件库"
            onAccepted: backend.addDesktopRoot(selectedFolder.toString())
        }
    }
}
