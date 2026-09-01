import QtQuick
import QtQuick.Controls
import QtQuick.Window

Window {
    id: root
    objectName: "v03FocusTimerAura"
    transientParent: null

    // Main.qml can bind these maps directly to the backend. Keeping them
    // overrideable also makes the component independently testable without
    // constructing the full desktop shell.
    property var appBackend: null
    property var focusInfo: appBackend ? appBackend.focusStatus : ({})
    property var focusTransition: appBackend ? appBackend.focusTransition : ({})
    property var presenceInfo: appBackend ? appBackend.habitatState : ({})
    // The pet and this independent tool window can live on different QScreen
    // objects for one frame while crossing a mixed-DPI monitor seam.  Relying
    // on this window's Screen in that frame clamps the aura back to the old
    // monitor.  Main.qml therefore supplies the work area containing Lilith;
    // standalone uses keep the current Screen as a safe fallback.
    property var placementArea: ({})
    property bool presentationEnabled: true
    // The focus clock follows the same 60/15 FPS budget as Lilith.  Main.qml
    // enables lowPower while the pet is idle or the full scene is throttled;
    // the countdown remains backend-authoritative in both modes.
    property bool lowPower: false
    // Main.qml scales the dial with Lilith, while the component keeps a
    // sensible standalone default.  The work-area fit below remains the final
    // authority on unusually small or high-DPI monitors.
    property real preferredExtent: 176
    property real anchorX: Screen.virtualX + Screen.width - 112
    property real anchorY: Screen.virtualY + 112
    property real subjectLeft: anchorX - 64
    property real subjectRight: anchorX + 64
    property real subjectCenterY: anchorY + 112
    property real sideGap: 12

    property color paperColor: "#fffdf8"
    property color inkColor: "#4d4a45"
    property color mutedColor: "#8e887e"
    property color trackColor: "#e4ddd2"
    property color progressColor: "#708b84"
    property color pausedColor: "#b28c62"
    property color completedColor: "#9f3129"

    readonly property string rawState: String(
        focusInfo && focusInfo.state !== undefined ? focusInfo.state : "")
    readonly property bool sessionActive: Boolean(
        focusInfo && focusInfo.active !== undefined ? focusInfo.active
                                                    : rawState === "running" || rawState === "paused")
    readonly property bool paused: sessionActive && Boolean(
        focusInfo && focusInfo.paused !== undefined ? focusInfo.paused : rawState === "paused")
    readonly property string presenceState: String(
        presenceInfo && presenceInfo.state !== undefined ? presenceInfo.state : "")
    readonly property bool suppressed: presenceState === "silent" || presenceState === "blocked"

    readonly property int durationSeconds: {
        var seconds = Number(focusInfo && focusInfo.planned_seconds !== undefined
                             ? focusInfo.planned_seconds : 0)
        if (!(seconds > 0))
            seconds = Number(focusInfo && focusInfo.plannedSeconds !== undefined
                             ? focusInfo.plannedSeconds : 0)
        if (!(seconds > 0))
            seconds = Number(focusInfo && focusInfo.durationMinutes !== undefined
                             ? focusInfo.durationMinutes : 25) * 60
        return Math.max(1, Math.round(seconds))
    }
    readonly property int elapsedSeconds: {
        var seconds = Number(focusInfo && focusInfo.elapsedSeconds !== undefined
                             ? focusInfo.elapsedSeconds : 0)
        if (!(seconds >= 0))
            seconds = 0
        return Math.max(0, Math.round(seconds))
    }
    readonly property int remainingSeconds: Math.max(0, durationSeconds - elapsedSeconds)
    readonly property real progressTarget: Math.max(
        0.0, Math.min(1.0, elapsedSeconds / Math.max(1, durationSeconds)))

    readonly property int transitionSequence: Math.max(
        0, Number(focusTransition && focusTransition.sequence !== undefined
                  ? focusTransition.sequence : 0) || 0)
    readonly property string transitionKind: String(
        focusTransition && focusTransition.kind !== undefined
        ? focusTransition.kind : "")
    property int consumedTransitionSequence: 0
    property bool componentReady: false
    property bool completionVisible: false
    property string completionLabel: "已完成"
    property string endingKind: ""
    property int endingElapsedSeconds: 0
    property int endingDurationSeconds: 1
    readonly property real endingProgress: Math.max(
        0.0, Math.min(1.0, endingElapsedSeconds / Math.max(1, endingDurationSeconds)))
    readonly property real displayedProgressTarget: completionVisible
                                                  ? (endingKind === "completed"
                                                     ? 1.0 : endingProgress)
                                                  : progressTarget
    property real animatedProgress: 0.0
    property real breath: 0.0
    property real completionPulse: 0.0
    // This acknowledgement ripple is intentionally independent of elapsed
    // time. A new session can therefore look alive without rewinding the old
    // completed duration arc through values that are no longer true.
    property real startPulse: 0.0
    property real orbitAngle: 0.0
    property real motionPhase: 0.0
    property int motionTickCount: 0
    // The red cord knot belongs to the duration arc itself: its angle is the
    // exact, interpolated share of the planned session.  orbitAngle remains a
    // separate low-contrast activity sweep, so motion never lies about how
    // much focus time has actually elapsed.
    readonly property real progressHeadAngle: Math.max(
        0.0, Math.min(360.0, animatedProgress * 360.0))
    readonly property string sessionIdentity: String(
        focusInfo && focusInfo.sessionId !== undefined ? focusInfo.sessionId
        : (focusInfo && focusInfo.id !== undefined ? focusInfo.id : ""))

    readonly property bool canAnimate: presentationEnabled && shouldShow && !suppressed
    readonly property bool breathing: canAnimate && sessionActive && !paused
                                      && !completionVisible
    // An explicit start gets one bounded high-clarity acknowledgement even
    // when the steady-state aura is on its 15 FPS budget.  This is finite
    // (880 ms), so an idle compact pet returns to low power instead of keeping
    // a permanent 60 FPS render loop alive.
    readonly property bool startAcknowledgementActive: canAnimate
                                                       && !paused
                                                       && focusStartAnimation.running
    readonly property bool fullMotionAnimationActive: canAnimate
                                                      && !paused
                                                      && (focusStartAnimation.running
                                                          || (!lowPower
                                                              && (progressCatchup.running
                                                                  || completionPulseAnimation.running)))
    readonly property int targetFps: startAcknowledgementActive ? 60
                                               : (breathing ? (lowPower ? 15 : 60)
                                               : (fullMotionAnimationActive ? 60 : 0))
    readonly property bool shouldShow: (sessionActive || completionVisible) && !suppressed
    readonly property string visualState: {
        if (suppressed)
            return "silent"
        if (completionVisible)
            return endingKind || "finished"
        if (paused)
            return "paused"
        if (sessionActive)
            return "running"
        return "idle"
    }
    readonly property string stateLabel: {
        if (completionVisible)
            return completionLabel
        if (visualState === "paused")
            return "已暂停"
        if (visualState === "running")
            return "专注中"
        if (visualState === "silent")
            return "静默"
        return "未开始"
    }
    readonly property string timeText: completionVisible
                                       ? (endingKind === "completed" ? "00:00"
                                          : formatSeconds(Math.max(
                                              0, endingDurationSeconds - endingElapsedSeconds)))
                                       : formatSeconds(remainingSeconds)
    readonly property int displayedElapsedSeconds: completionVisible
                                                   ? endingElapsedSeconds : elapsedSeconds
    readonly property int displayedDurationSeconds: completionVisible
                                                    ? endingDurationSeconds : durationSeconds
    readonly property string remainingLabel: completionVisible
                                             ? completionLabel
                                             : (paused ? "已暂停 · 剩余"
                                                       : "专注中 · 剩余")
    readonly property string elapsedText: formatSeconds(displayedElapsedSeconds)
    readonly property string durationText: formatSeconds(displayedDurationSeconds)
    readonly property string usedTimeText: "已用 " + elapsedText + " / " + durationText

    function formatSeconds(value) {
        var total = Math.max(0, Math.floor(Number(value) || 0))
        var minutes = Math.floor(total / 60)
        var seconds = total % 60
        return (minutes < 10 ? "0" : "") + minutes
                + ":" + (seconds < 10 ? "0" : "") + seconds
    }

    function showEnding(kind, label, transition) {
        focusStartAnimation.stop()
        startPulse = 0.0
        completionPulse = 0.0
        endingKind = String(kind || "finished")
        completionLabel = String(label || "已结束")
        var elapsed = Number(transition && transition.elapsedSeconds !== undefined
                             ? transition.elapsedSeconds : elapsedSeconds)
        var duration = Number(transition && transition.durationSeconds !== undefined
                              ? transition.durationSeconds : durationSeconds)
        endingElapsedSeconds = Math.max(0, Math.round(elapsed || 0))
        endingDurationSeconds = Math.max(1, Math.round(duration || 1))
        completionVisible = true
        // A completion that happens during a full-screen/sensitive interval
        // must not spend its whole acknowledgement lifetime while invisible.
        // Keep it pending and give it the normal dwell time once suppression
        // ends; no content is shown inside the suppressed state itself.
        if (suppressed || !presentationEnabled)
            completionTimer.stop()
        else
            completionTimer.restart()
    }

    function showCompleted(label) {
        showEnding("completed", label || "专注完成", {
            "elapsedSeconds": durationSeconds,
            "durationSeconds": durationSeconds
        })
    }

    function applyFocusTransition() {
        if (!componentReady || transitionSequence <= 0
                || transitionSequence === consumedTransitionSequence)
            return
        consumedTransitionSequence = transitionSequence
        // Read the kind from the source map here. Both readonly projections
        // are invalidated by the same QVariantMap notification and QML does
        // not guarantee which projection is reevaluated first.
        var currentKind = String(
            focusTransition && focusTransition.kind !== undefined
            ? focusTransition.kind : "")
        if (currentKind === "completed")
            showEnding("completed", "专注完成", focusTransition)
        else if (currentKind === "finished")
            showEnding("finished", "已结束", focusTransition)
        else if (currentKind === "cancelled")
            showEnding("cancelled", "已取消", focusTransition)
        else if (currentKind === "started")
            beginSessionVisual()
    }

    function clearCompletion() {
        completionTimer.stop()
        completionVisible = false
        completionPulse = 0.0
    }

    function snapAnimatedProgress() {
        progressCatchup.stop()
        animatedProgress = Math.max(0.0, Math.min(1.0, displayedProgressTarget))
    }

    function beginSessionVisual() {
        clearCompletion()
        Qt.callLater(function() {
            // The backend can publish a started transition and a pause in
            // adjacent event-loop turns.  Re-check the settled state before
            // starting the finite acknowledgement so an already-paused
            // clock is visually still and does not briefly claim 60 FPS.
            if (!sessionActive || paused)
                return
            snapAnimatedProgress()
            if (canAnimate) {
                focusStartAnimation.stop()
                startPulse = 0.0
                focusStartAnimation.start()
            }
        })
    }

    function syncAnimatedProgress() {
        if (!componentReady)
            return
        var desired = Math.max(0.0, Math.min(1.0, displayedProgressTarget))
        progressCatchup.stop()
        // The 15 FPS clock is the only recurring low-power animation.  A
        // nearly one-second interpolation after every backend tick would
        // otherwise keep the render loop hot despite targetFps reporting 15.
        if (lowPower || !canAnimate) {
            animatedProgress = desired
            return
        }
        // Pause must freeze on the backend's exact accumulated second.  The
        // QVariantMap may invalidate paused/progressTarget in either order,
        // therefore all callers queue this function until both projections
        // have settled before deciding whether to animate or snap.
        if (paused) {
            animatedProgress = desired
            return
        }
        if (Math.abs(animatedProgress - desired) < 0.000001) {
            animatedProgress = desired
            return
        }
        progressCatchup.from = animatedProgress
        progressCatchup.to = desired
        progressCatchup.duration = completionVisible ? 180 : 920
        progressCatchup.easing.type = completionVisible ? Easing.OutCubic : Easing.Linear
        progressCatchup.restart()
    }

    readonly property real placementLeft: {
        var value = Number(placementArea && placementArea.left !== undefined
                           ? placementArea.left : Screen.virtualX)
        return isFinite(value) ? value : Screen.virtualX
    }
    readonly property real placementTop: {
        var value = Number(placementArea && placementArea.top !== undefined
                           ? placementArea.top : Screen.virtualY)
        return isFinite(value) ? value : Screen.virtualY
    }
    readonly property real placementWidth: {
        var value = Number(placementArea && placementArea.width !== undefined
                           ? placementArea.width : Screen.width)
        if (!(isFinite(value) && value > 0)) {
            var right = Number(placementArea && placementArea.right !== undefined
                               ? placementArea.right : NaN)
            value = isFinite(right) ? right - placementLeft : Screen.width
        }
        return Math.max(1, value)
    }
    readonly property real placementHeight: {
        var value = Number(placementArea && placementArea.height !== undefined
                           ? placementArea.height : Screen.height)
        if (!(isFinite(value) && value > 0)) {
            var bottom = Number(placementArea && placementArea.bottom !== undefined
                                ? placementArea.bottom : NaN)
            value = isFinite(bottom) ? bottom - placementTop : Screen.height
        }
        return Math.max(1, value)
    }
    readonly property real placementRight: placementLeft + placementWidth
    readonly property real placementBottom: placementTop + placementHeight
    // Preserve the full 176px dial whenever possible, but let the native
    // transparent window itself shrink on pathological logical work areas.
    // This is preferable to leaving an invisible input-sized overhang outside
    // a 200%-or-higher DPI monitor's available geometry.
    readonly property real requestedDialExtent: {
        var value = Number(preferredExtent)
        return isFinite(value) && value > 0 ? value : 176
    }
    readonly property real dialExtent: Math.max(
        1, Math.min(requestedDialExtent,
                    placementWidth > 16 ? placementWidth - 16 : placementWidth,
                    placementHeight > 16 ? placementHeight - 16 : placementHeight))
    readonly property real dialScale: dialExtent / 176.0
    // All radial geometry must contract with the native window.  Keeping the
    // old fixed 8/12/23 px values made the progress track collapse into a
    // solid disc on very small logical work areas (for example a narrow
    // portrait monitor at a high Windows scale factor).
    readonly property real surfaceInset: Math.max(1, Math.min(12, 12 * dialScale))
    readonly property real ringInset: Math.max(2, 12 * dialScale)
    readonly property real ringStroke: Math.max(1.5, 8 * dialScale)
    readonly property real activityInset: Math.max(ringInset + 2, 23 * dialScale)
    width: dialExtent
    height: dialExtent
    readonly property real horizontalMargin: Math.min(
        8, Math.max(0, (placementWidth - width) / 2))
    readonly property real verticalMargin: Math.min(
        8, Math.max(0, (placementHeight - height) / 2))
    readonly property bool useSidePlacement:
        anchorY - height / 2 < placementTop + verticalMargin
    readonly property real leftSideRoom:
        subjectLeft - placementLeft - horizontalMargin
    readonly property real rightSideRoom:
        placementRight - subjectRight - horizontalMargin
    readonly property bool placeOnRight: rightSideRoom >= width + sideGap
                                          || rightSideRoom >= leftSideRoom
    x: {
        var desired = anchorX - width / 2
        if (useSidePlacement)
            desired = placeOnRight ? subjectRight + sideGap
                                   : subjectLeft - width - sideGap
        return Math.max(placementLeft + horizontalMargin,
                        Math.min(desired, placementRight - width - horizontalMargin))
    }
    y: {
        var desired = useSidePlacement ? subjectCenterY - height / 2
                                       : anchorY - height / 2
        return Math.max(placementTop + verticalMargin,
                        Math.min(desired, placementBottom - height - verticalMargin))
    }
    visible: presentationEnabled && shouldShow
    color: "transparent"
    title: "Lilies · 专注"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
           | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput

    onSessionActiveChanged: {
        // A legacy/test focus source without transition metadata may still
        // request the start affordance.  Real persisted sessions have an
        // identity and are deliberately restored without replaying "start".
        if (sessionActive && !paused && sessionIdentity === ""
                && transitionSequence <= 0)
            beginSessionVisual()
        else if (sessionActive)
            clearCompletion()
    }
    onSessionIdentityChanged: {
        // Identity changes clear stale completion presentation, but the
        // explicit sequenced `started` transition is the sole production
        // trigger for the acknowledgement.  This prevents an active or
        // paused session restored after restart from looking newly started.
        if (sessionActive)
            clearCompletion()
    }
    onProgressTargetChanged: Qt.callLater(syncAnimatedProgress)
    onEndingProgressChanged: Qt.callLater(syncAnimatedProgress)
    onPausedChanged: {
        if (paused) {
            focusStartAnimation.stop()
            startPulse = 0.0
        }
        Qt.callLater(syncAnimatedProgress)
    }
    onLowPowerChanged: {
        if (lowPower) {
            completionPulse = 0.0
        }
        Qt.callLater(syncAnimatedProgress)
    }
    onPresentationEnabledChanged: {
        Qt.callLater(syncAnimatedProgress)
        if (!completionVisible)
            return
        if (!presentationEnabled || suppressed)
            completionTimer.stop()
        else
            completionTimer.restart()
    }
    onCanAnimateChanged: {
        if (!canAnimate) {
            focusStartAnimation.stop()
            startPulse = 0.0
            completionPulse = 0.0
        }
        Qt.callLater(syncAnimatedProgress)
    }
    onCompletionVisibleChanged: {
        if (!completionVisible)
            completionPulse = 0.0
        Qt.callLater(syncAnimatedProgress)
    }
    onSuppressedChanged: {
        Qt.callLater(syncAnimatedProgress)
        if (!completionVisible)
            return
        if (suppressed)
            completionTimer.stop()
        else if (presentationEnabled)
            completionTimer.restart()
    }
    onTransitionSequenceChanged: applyFocusTransition()
    Component.onCompleted: {
        consumedTransitionSequence = transitionSequence
        componentReady = true
        snapAnimatedProgress()
    }

    NumberAnimation {
        id: progressCatchup
        target: root
        property: "animatedProgress"
    }

    // A timer clock, rather than an unconstrained infinite NumberAnimation,
    // makes the render budget explicit and testable.  Low-power focus still
    // breathes and sweeps at 15 FPS instead of becoming visually frozen.
    Timer {
        id: focusMotionClock
        interval: root.lowPower ? 67 : 16
        repeat: true
        running: root.breathing
        onTriggered: {
            var delta = interval / 1000.0
            root.motionPhase = (root.motionPhase
                                + delta * Math.PI * 2.0 / 2.5) % (Math.PI * 2.0)
            root.breath = 0.5 - Math.cos(root.motionPhase) * 0.5
            root.orbitAngle = (root.orbitAngle + delta * 40.0) % 360.0
            root.motionTickCount += 1
        }
    }

    SequentialAnimation on completionPulse {
        id: completionPulseAnimation
        running: root.canAnimate && !root.lowPower
                 && root.completionVisible && root.endingKind === "completed"
        loops: Animation.Infinite
        NumberAnimation { from: 0.0; to: 1.0; duration: 460; easing.type: Easing.OutCubic }
        NumberAnimation { from: 1.0; to: 0.0; duration: 680; easing.type: Easing.InOutSine }
    }

    SequentialAnimation {
        id: focusStartAnimation
        NumberAnimation {
            target: root
            property: "startPulse"
            from: 0.0
            to: 1.0
            duration: 260
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: root
            property: "startPulse"
            from: 1.0
            to: 0.0
            duration: 620
            easing.type: Easing.InOutSine
        }
    }

    // The faint activity sweep above makes "the clock is running" visible
    // between one-second backend updates. It freezes while paused and never
    // creates another native input surface. The red knot below is *not*
    // driven by this loop: it stays attached to true duration progress.

    Timer {
        id: completionTimer
        interval: root.endingKind === "completed" ? 3000 : 2000
        repeat: false
        onTriggered: root.completionVisible = false
    }

    Rectangle {
        id: auraSurface
        objectName: "focusTimerAuraSurface"
        anchors.centerIn: parent
        width: Math.max(1, Math.min(root.width - root.surfaceInset,
                                   (164 + root.breath * 4
                                    + root.completionPulse * 5) * root.dialScale))
        height: width
        radius: width / 2
        color: root.paperColor
        opacity: root.paused ? 0.91 : 0.96
        border.width: 1
        border.color: root.completionVisible
                      ? (root.endingKind === "completed" ? root.completedColor
                         : (root.endingKind === "cancelled" ? root.mutedColor
                                                            : root.progressColor))
                      : (root.paused ? root.pausedColor : "#d5cdc1")
        scale: 1.0 + root.breath * 0.012 + root.completionPulse * 0.018
               + Math.sin(root.startPulse * Math.PI) * 0.006

        Rectangle {
            id: focusStartWave
            objectName: "focusTimerStartWave"
            anchors.centerIn: parent
            width: Math.max(1, parent.width - 36 * root.dialScale
                            + root.startPulse * 22 * root.dialScale)
            height: width
            radius: width / 2
            color: "transparent"
            border.width: 1
            border.color: root.progressColor
            opacity: root.sessionActive && !root.paused && !root.suppressed
                     ? Math.sin(root.startPulse * Math.PI) * 0.26 : 0.0
        }

        Rectangle {
            anchors.centerIn: parent
            width: Math.max(1, parent.width - 18 * root.dialScale)
            height: width
            radius: width / 2
            color: "transparent"
            border.width: 1
            border.color: root.progressColor
            opacity: root.breathing ? 0.12 + root.breath * 0.12 : 0.08
        }

        Canvas {
            id: progressRing
            objectName: "focusTimerProgressRing"
            anchors.fill: parent
            antialiasing: true

            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0, 0, width, height)
                var centerX = width / 2
                var centerY = height / 2
                var radius = Math.max(
                    1, Math.min(width, height) / 2 - root.ringInset)
                var start = -Math.PI / 2
                var end = start + Math.PI * 2 * Math.max(0, Math.min(1, root.animatedProgress))

                ctx.lineWidth = root.ringStroke
                ctx.lineCap = "round"
                ctx.strokeStyle = root.trackColor
                ctx.beginPath()
                ctx.arc(centerX, centerY, radius, 0, Math.PI * 2, false)
                ctx.stroke()

                if (end > start + 0.001) {
                    ctx.strokeStyle = root.completionVisible
                                      ? (root.endingKind === "completed" ? root.completedColor
                                         : (root.endingKind === "cancelled" ? root.mutedColor
                                                                            : root.progressColor))
                                      : (root.paused ? root.pausedColor : root.progressColor)
                    ctx.beginPath()
                    ctx.arc(centerX, centerY, radius, start, end, false)
                    ctx.stroke()
                }
            }

            Connections {
                target: root
                function onAnimatedProgressChanged() { progressRing.requestPaint() }
                function onPausedChanged() { progressRing.requestPaint() }
                function onCompletionVisibleChanged() { progressRing.requestPaint() }
            }
        }

        Rectangle {
            id: orbitKnotGlow
            objectName: "focusTimerOrbitKnotGlow"
            readonly property real orbitRadius: Math.max(
                1, Math.min(parent.width, parent.height) / 2 - root.ringInset)
            readonly property real radians: (root.progressHeadAngle - 90.0) * Math.PI / 180.0
            width: Math.max(4, 18 * root.dialScale)
            height: width
            radius: width / 2
            x: parent.width / 2 + Math.cos(radians) * orbitRadius - width / 2
            y: parent.height / 2 + Math.sin(radians) * orbitRadius - height / 2
            color: root.completedColor
            opacity: root.sessionActive && !root.completionVisible
                     ? (root.paused ? 0.08 : 0.14 + root.breath * 0.06)
                     : 0.0
        }

        Rectangle {
            id: orbitKnot
            objectName: "focusTimerOrbitKnot"
            readonly property real orbitRadius: Math.max(
                1, Math.min(parent.width, parent.height) / 2 - root.ringInset)
            readonly property real radians: (root.progressHeadAngle - 90.0) * Math.PI / 180.0
            width: Math.max(3, 7 * root.dialScale)
            height: width
            radius: width / 2
            x: parent.width / 2 + Math.cos(radians) * orbitRadius - width / 2
            y: parent.height / 2 + Math.sin(radians) * orbitRadius - height / 2
            color: root.completedColor
            border.width: 1
            border.color: "#fff8f3"
            opacity: root.sessionActive && !root.completionVisible
                     ? (root.paused ? 0.62 : 0.92)
                     : 0.0
        }

        Rectangle {
            id: activitySweep
            objectName: "focusTimerActivitySweep"
            readonly property real orbitRadius: Math.max(
                1, Math.min(parent.width, parent.height) / 2 - root.activityInset)
            readonly property real radians: (root.orbitAngle - 90.0) * Math.PI / 180.0
            width: Math.max(2, 4 * root.dialScale)
            height: width
            radius: width / 2
            x: parent.width / 2 + Math.cos(radians) * orbitRadius - width / 2
            y: parent.height / 2 + Math.sin(radians) * orbitRadius - height / 2
            color: root.progressColor
            opacity: root.breathing ? 0.30 + root.breath * 0.18 : 0.0
        }

        Label {
            id: stateText
            objectName: "focusTimerStateLabel"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: timeTextItem.top
            anchors.bottomMargin: Math.max(2, 7 * root.dialScale)
            text: root.remainingLabel
            color: root.completionVisible
                   ? (root.endingKind === "completed" ? root.completedColor
                      : (root.endingKind === "cancelled" ? root.mutedColor
                                                         : root.progressColor))
                   : (root.paused ? root.pausedColor : root.mutedColor)
            font.pixelSize: Math.max(6, 12 * root.dialScale)
            font.weight: Font.DemiBold
            font.letterSpacing: Math.max(0.2, 1.2 * root.dialScale)
        }

        Label {
            id: timeTextItem
            objectName: "focusTimerTimeText"
            anchors.centerIn: parent
            text: root.timeText
            color: root.inkColor
            font.pixelSize: Math.max(9, 30 * root.dialScale)
            font.weight: Font.DemiBold
            font.letterSpacing: 0.6
        }

        Label {
            id: usedTimeTextItem
            objectName: "focusTimerUsedTimeText"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: timeTextItem.bottom
            anchors.topMargin: Math.max(2, 8 * root.dialScale)
            width: Math.max(1, parent.width - 24 * root.dialScale)
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: root.usedTimeText
            color: root.completionVisible
                   ? (root.endingKind === "completed" ? root.completedColor
                      : (root.endingKind === "cancelled" ? root.mutedColor
                                                         : root.progressColor))
                   : root.mutedColor
            font.pixelSize: Math.max(6, 8 * root.dialScale)
        }
    }
}
