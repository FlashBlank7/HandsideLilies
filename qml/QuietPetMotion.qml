import QtQuick

Item {
    id: motion
    objectName: "quietPetMotion"
    property string activity: "idle"
    property var reaction: ({})
    property bool motionAllowed: true
    property bool suppressed: false
    property bool readingArtwork: false
    property rect figure: Qt.rect(0, 0, width, height)
    property point grip: Qt.point(width / 2, height / 2)
    property real notebookProgress: 0
    property real restProgress: 0
    property real acknowledgement: 0
    property int consumedSequence: 0
    property int acknowledgementCount: 0
    property bool initialized: false
    readonly property bool animating: acknowledge.running || notebookAnimation.running || restAnimation.running
    readonly property real headResponse: acknowledgement * 3.5 + restProgress * 0.8 + notebookProgress * 0.55
    readonly property real artworkResponse: acknowledgement * 0.9

    function syncActivity() {
        if (!initialized || !motionAllowed || suppressed) return
        notebookAnimation.stop()
        notebookAnimation.from = notebookProgress
        notebookAnimation.to = activity === "reading" && !readingArtwork ? 1 : 0
        if (Math.abs(notebookAnimation.to - notebookProgress) > 0.001) notebookAnimation.start()
        restAnimation.stop()
        restAnimation.from = restProgress
        restAnimation.to = activity === "rest" ? 1 : 0
        if (Math.abs(restAnimation.to - restProgress) > 0.001) restAnimation.start()
    }
    function consumeReaction() {
        var sequence = Number(reaction.sequence || 0)
        if (!isFinite(sequence) || sequence <= consumedSequence || sequence !== Math.floor(sequence)) return
        consumedSequence = sequence
        if (!initialized || !motionAllowed || suppressed || reaction.kind !== "task-completed") return
        acknowledgementCount += 1
        acknowledge.restart()
    }
    function gateChanged() {
        if (!initialized) return
        if (!motionAllowed || suppressed) {
            // Freeze grabbed pixels; never finish a nod under the cursor or
            // replay a missed gesture after a game/drag ends.
            acknowledge.stop(); notebookAnimation.stop(); restAnimation.stop()
        } else {
            acknowledgement = 0
            syncActivity()
        }
    }
    onActivityChanged: syncActivity()
    onReadingArtworkChanged: syncActivity()
    onReactionChanged: consumeReaction()
    onMotionAllowedChanged: gateChanged()
    onSuppressedChanged: gateChanged()
    Component.onCompleted: { consumedSequence = Number(reaction.sequence || 0); initialized = true; syncActivity() }
    SequentialAnimation {
        id: acknowledge
        NumberAnimation { target: motion; property: "acknowledgement"; from: 0; to: 1; duration: 180; easing.type: Easing.InOutSine }
        PauseAnimation { duration: 100 }
        NumberAnimation { target: motion; property: "acknowledgement"; from: 1; to: 0; duration: 450; easing.type: Easing.InOutSine }
    }
    NumberAnimation { id: notebookAnimation; target: motion; property: "notebookProgress"; duration: 280; easing.type: Easing.InOutCubic }
    NumberAnimation { id: restAnimation; target: motion; property: "restProgress"; duration: 320; easing.type: Easing.InOutCubic }

    Item {
        id: notebook
        objectName: "quietReadingNotebook"
        width: Math.min(48, Math.max(26, motion.figure.width * 0.28))
        height: width * 0.66
        x: Math.max(4, Math.min(motion.width - width - 4, motion.grip.x - width / 2))
        y: Math.max(4, Math.min(motion.height - height - 4, motion.grip.y - height / 2))
        opacity: motion.suppressed ? 0 : motion.notebookProgress
        visible: opacity > 0.001
        scale: 0.84 + motion.notebookProgress * 0.16
        rotation: -6 + (1 - motion.notebookProgress) * 12
        // Decorative only: no MouseArea, focus, text, hit-mask enlargement or timer.
        Rectangle { x: 0; y: 3; width: parent.width; height: parent.height; radius: 3; color: "#364b4145" }
        Rectangle { width: parent.width * 0.5; height: parent.height; radius: 2; color: "#fffdf6"; border.color: "#bfb7ad"; rotation: -4; transformOrigin: Item.Right }
        Rectangle { x: parent.width * 0.5; width: parent.width * 0.5; height: parent.height; radius: 2; color: "#f5f0e5"; border.color: "#bfb7ad"; rotation: 4; transformOrigin: Item.Left }
        Rectangle { x: parent.width * 0.50; y: 2; width: 1; height: parent.height - 3; color: "#d1c7ba" }
        Rectangle { x: parent.width * 0.72; y: 2; width: 2; height: parent.height * 0.52; radius: 1; color: "#975e65" }
        Repeater {
            model: 3
            Rectangle {
                required property int index
                x: notebook.width * 0.12; y: notebook.height * (0.30 + index * 0.17)
                width: notebook.width * (index === 2 ? 0.22 : 0.29); height: 1; color: "#d5cec2"
            }
        }
    }
}
