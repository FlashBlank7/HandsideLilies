import QtQml

QtObject {
    id: root
    objectName: "v03PetPoseResolver"

    // Keep pose arbitration data-only so it can be verified without creating
    // another native window.  Host-edge geometry is always authoritative:
    // changing to an activity pose while attached would pull the visible
    // contact point away from the window Lilith is resting on.
    property var habitatState: ({})
    property bool chatOpen: false
    property var selectionBubble: ({})
    property var companionBubble: ({})
    property var readingStatus: ({})
    property var focusStatus: ({})
    property string equippedPose: "idle-prayer"

    readonly property bool habitatAttached: Boolean(habitatState.attached)
    readonly property string habitatPose: String(habitatState.pose || "")
    readonly property bool selectionVisible: Boolean(selectionBubble.visible)
    readonly property bool companionVisible: Boolean(companionBubble.visible)
    readonly property bool readingActive: Boolean(readingStatus.active)
                                          && !Boolean(readingStatus.paused)
    readonly property bool focusActive: Boolean(focusStatus.active)
                                        && !Boolean(focusStatus.paused)
                                        && String(focusStatus.state || "") === "running"

    readonly property string contextKind: {
        if (habitatAttached)
            return "habitat"
        if (chatOpen)
            return "chat"
        if (selectionVisible)
            return "selection"
        if (companionVisible)
            return "companion"
        if (readingActive)
            return "reading"
        if (focusActive)
            return "focus"
        return "equipped"
    }

    readonly property string resolvedPose: {
        if (habitatAttached) {
            if (habitatPose.indexOf("edge-peek") === 0)
                return "edge-peek-live"
            if (habitatPose === "listening-live")
                return "listening-live"
            if (habitatPose === "title-sit")
                return "title-sit"
            if (habitatPose === "perch-top")
                return "perch-prone"
            // A future/third-party habitat pose must not accidentally activate
            // an arbitrary QML recipe.  Preserve the user's known-safe loadout
            // until the pose is added to this fixed resolver contract.
            return String(equippedPose || "idle-prayer")
        }
        if (chatOpen)
            return "listening-live"
        if (selectionVisible)
            return "reading"
        if (companionVisible)
            return "presenting"
        if (readingActive)
            return "reading"
        if (focusActive)
            return "focus-watch"
        return String(equippedPose || "idle-prayer")
    }

    // Context state can live for minutes; it must not pin the desktop pet to
    // 60 FPS for the whole bubble/chat lifetime.  The body already raises its
    // cadence while the 220--300 ms pose cross-fade is actually running, and
    // Main owns real pointer/menu/chat interaction boosts.  Keep the resolver
    // purely semantic so a quietly visible companion bubble still settles to
    // the 15 FPS breathing budget.
    readonly property bool requiresHighMotion: false
}
