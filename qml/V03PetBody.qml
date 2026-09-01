import QtQuick

Item {
    id: root
    objectName: "v03PetBody"

    required property var appBackend

    // Public pose names mirror ThemeManifest.character.poseBundles.
    property string pose: "idle-prayer"
    property bool paused: false
    property bool lowPower: false
    property bool inputEnabled: true
    // A manual drag changes from a host-edge silhouette to the free-standing
    // silhouette.  During that gesture the exact grabbed pixels must stay
    // under the pointer, so habitat-driven geometry changes snap once instead
    // of spending 220--300 ms sliding inside the moving transparent window.
    property bool interactionSnap: false
    property bool geometryFrozenForInteraction: false
    property real characterHeight: height * 0.70
    property color cordColor: "#9f3129"
    property color crackColor: "#e9ffff"
    property point cordStart: supportCordPoint
    property point cordEnd: Qt.point(width * 0.88, height * 0.58)
    property int cordNodeCount: 14
    // These public bounds are consumed by the rope, resize handle and native
    // hit-region publisher.  Interpolating them keeps those dependants glued
    // to Lilith while habitat artwork cross-fades into the layered outfit.
    readonly property rect layeredFigureBounds: {
        // mapToItem() itself is not a QML property dependency.  Read every
        // placement/transform input explicitly so native hit bounds, the
        // rope and nearby bubbles follow a manifest-driven prone silhouette.
        var tracking = figureFrame.x + figureFrame.y
                     + figureFrame.width + figureFrame.height
                     + renderedLayeredBodyRotation
                     + (habitatMirror ? 1.0 : 0.0)
        return itemBoundsInRoot(figureFrame, tracking)
    }
    readonly property real figureLeft: poseArtworkFrame.x * renderedArtworkBlend
                                       + layeredFigureBounds.x * (1.0 - renderedArtworkBlend)
    readonly property real figureTop: poseArtworkFrame.y * renderedArtworkBlend
                                      + layeredFigureBounds.y * (1.0 - renderedArtworkBlend)
    readonly property real figureWidth: poseArtworkFrame.width * renderedArtworkBlend
                                        + layeredFigureBounds.width * (1.0 - renderedArtworkBlend)
    readonly property real figureHeight: poseArtworkFrame.height * renderedArtworkBlend
                                         + layeredFigureBounds.height * (1.0 - renderedArtworkBlend)
    readonly property var habitatLayout: appBackend ? appBackend.habitatState : ({})
    readonly property string habitatProfile: String(habitatLayout.profile || "desktop")
    readonly property bool habitatLayoutActive: Boolean(habitatLayout.attached)
                                                  && habitatProfile !== "desktop"
    readonly property real habitatCharacterScale: {
        var value = Number(habitatLayout.characterScale)
        if (!habitatLayoutActive || !isFinite(value))
            return 1.0
        return Math.max(0.50, Math.min(1.0, value))
    }
    readonly property real habitatAnchorNormX: finiteHabitatValue(
        "anchorNormX", 0.5 + poseShiftX / Math.max(1.0, width))
    readonly property real habitatAnchorNormY: finiteHabitatValue(
        "anchorNormY", 0.5 + poseShiftY / Math.max(1.0, height))
    readonly property real habitatContactX: usesProceduralLayeredFallback
                                               ? proceduralLayeredContactAnchor.x
                                               : usesOptionalHabitatArtwork
                                               ? optionalPoseArtworkAnchor.x
                                               : finiteHabitatValue(
                                                   "contactX",
                                                   usesPoseArtwork
                                                   ? poseArtworkAnchor.x : 0.5)
    readonly property real habitatContactY: usesProceduralLayeredFallback
                                               ? proceduralLayeredContactAnchor.y
                                               : usesOptionalHabitatArtwork
                                               ? optionalPoseArtworkAnchor.y
                                               : finiteHabitatValue(
                                                   "contactY",
                                                   usesPoseArtwork
                                                   ? poseArtworkAnchor.y : 0.5)
    readonly property bool habitatMirror: habitatLayoutActive
                                           && Boolean(habitatLayout.mirror)
    readonly property string habitatPoseVariant: habitatLayoutActive
                                                   ? String(habitatLayout.poseVariant
                                                            || habitatProfile)
                                                   : "desktop-prayer"
    readonly property var habitatVariantDefinition: {
        if (!appBackend)
            return ({})
        var manifest = appBackend.themeManifest || ({})
        var character = manifest.character || ({})
        var variants = character.habitatPoseVariants || ({})
        return variants[habitatPoseVariant] || ({})
    }
    readonly property string habitatStrategy: habitatLayoutActive
                                               ? String(habitatLayout.habitatStrategy
                                                        || habitatVariantDefinition.strategy
                                                        || "edge")
                                               : "desktop"
    readonly property var habitatVariantLayered:
        habitatVariantDefinition.layered || ({})
    readonly property var habitatVariantArtwork:
        habitatVariantDefinition.artwork || ({})
    readonly property string habitatMotionStyle: habitatLayoutActive
                                                  ? String(habitatLayout.motionStyle
                                                           || "quiet-breathe")
                                                  : "quiet-breathe"
    readonly property real habitatMotionPeriod: {
        var value = Number(habitatLayout.motionPeriod)
        if (!habitatLayoutActive || !isFinite(value))
            return 3.4
        return Math.max(1.8, Math.min(8.0, value))
    }
    readonly property real habitatPeekFraction: {
        var value = Number(habitatLayout.peekFraction)
        if (!habitatLayoutActive || !isFinite(value))
            return 1.0
        return Math.max(0.50, Math.min(1.0, value))
    }
    readonly property real fallbackPoseHeightFactor: {
        if (!habitatLayoutActive)
            return 1.0
        var declared = Number(habitatVariantDefinition.heightFactor)
        if (isFinite(declared))
            return Math.max(0.40, Math.min(1.0, declared))
        if (habitatProfile === "small-title")
            return 0.90
        if (habitatProfile === "portrait-title")
            return 0.82
        if (habitatProfile === "medium-perch" || habitatProfile === "large-perch")
            return 0.58
        return 1.0
    }
    readonly property bool variantLayeredSideAware:
        Boolean(habitatVariantLayered.sideAware)
    readonly property bool variantArtworkSideAware:
        Boolean(habitatVariantArtwork.sideAware)
    readonly property bool usesProceduralLayeredFallback:
        Boolean(habitatVariantDefinition.proceduralLayeredFallback)
        && !usesOptionalHabitatArtwork
    readonly property point proceduralLayeredContactAnchor: {
        var values = habitatVariantLayered.layeredContactAnchor
        if (!values || values.length !== 2)
            return Qt.point(0.5, 0.5)
        var anchorX = Number(values[0])
        var anchorY = Number(values[1])
        if (!isFinite(anchorX) || !isFinite(anchorY))
            return Qt.point(0.5, 0.5)
        anchorX = Math.max(0.0, Math.min(1.0, anchorX))
        anchorY = Math.max(0.0, Math.min(1.0, anchorY))
        if (variantLayeredSideAware && habitatMirror)
            anchorX = 1.0 - anchorX
        return Qt.point(anchorX, anchorY)
    }
    readonly property real variantBodyRotation:
        boundedVariantNumber(habitatVariantLayered, "bodyRotation", 0.0, -82.0, 82.0)
    readonly property real variantHeadRotation:
        boundedVariantNumber(habitatVariantLayered, "headRotation", 0.0, -18.0, 18.0)
    readonly property real variantTorsoRotation:
        boundedVariantNumber(habitatVariantLayered, "torsoRotation", 0.0, -12.0, 12.0)
    readonly property real variantSkirtRotation:
        boundedVariantNumber(habitatVariantLayered, "skirtRotation", 0.0, -12.0, 12.0)
    readonly property real variantHeadOffsetX: boundedVariantOffset("headOffset", 0)
    readonly property real variantHeadOffsetY: boundedVariantOffset("headOffset", 1)
    readonly property real variantTorsoOffsetX: boundedVariantOffset("torsoOffset", 0)
    readonly property real variantTorsoOffsetY: boundedVariantOffset("torsoOffset", 1)
    readonly property real variantSkirtOffsetX: boundedVariantOffset("skirtOffset", 0)
    readonly property real variantSkirtOffsetY: boundedVariantOffset("skirtOffset", 1)
    readonly property real variantHeadScaleX: boundedVariantScale("headScale", 0)
    readonly property real variantHeadScaleY: boundedVariantScale("headScale", 1)
    readonly property real variantTorsoScaleX: boundedVariantScale("torsoScale", 0)
    readonly property real variantTorsoScaleY: boundedVariantScale("torsoScale", 1)
    readonly property real variantSkirtScaleX: boundedVariantScale("skirtScale", 0)
    readonly property real variantSkirtScaleY: boundedVariantScale("skirtScale", 1)
    readonly property real variantHeadClipEnd:
        boundedVariantNumber(habitatVariantLayered, "headClipEnd", 0.315, 0.24, 0.40)
    readonly property real variantTorsoClipEnd: Math.max(
        variantHeadClipEnd + 0.16,
        boundedVariantNumber(habitatVariantLayered, "torsoClipEnd", 0.60, 0.48, 0.72))
    readonly property real variantArtworkRotationBias:
        boundedVariantNumber(habitatVariantArtwork, "rotationBias", 0.0, -2.0, 2.0)
    readonly property real variantArtworkMotionGain:
        boundedVariantNumber(habitatVariantArtwork, "motionGain", 1.0, 0.30, 1.30)
    property real renderedCharacterScale: habitatCharacterScale
    property real renderedAnchorNormX: habitatAnchorNormX
    property real renderedAnchorNormY: habitatAnchorNormY
    property real renderedContactX: habitatContactX
    property real renderedContactY: habitatContactY
    property real renderedFallbackPoseHeightFactor: fallbackPoseHeightFactor
    property real renderedHabitatBlend: habitatLayoutActive ? 1.0 : 0.0
    // Drive representation blending explicitly. A declarative Behavior can
    // lose its pending target when a drag snap interrupts a rapid
    // artwork -> layered -> artwork sequence; the value then remains at zero
    // even though `usesPoseArtwork` is true. The explicit retargetable
    // animation below preserves the same 220 ms cross-fade and makes the
    // final representation deterministic.
    property real renderedArtworkBlend: 0.0
    property int artworkBlendSyncCount: 0
    property real renderedPoseArtworkRatio: poseArtworkAspectRatio
    property real renderedPoseCordAnchorX: poseArtworkCordAnchor.x
    property real renderedPoseCordAnchorY: poseArtworkCordAnchor.y
    // Kept public so the off-screen gate can prove the regional silhouette is
    // distinct even with whole-frame rotation neutralised.
    property real layeredBodyRotationMultiplier: 1.0
    readonly property real layeredPoseRotation: (poseRotation
                                                   + variantBodyRotation
                                                   * (variantLayeredSideAware
                                                      ? habitatSideSign : 1.0))
                                                  * layeredBodyRotationMultiplier
    property real renderedLayeredRequestedRotation: layeredPoseRotation
    readonly property real renderedLayeredBodyRotation:
        boundedLayeredRotation(renderedLayeredRequestedRotation)
    property real renderedVariantHeadRotation: variantHeadRotation
    property real renderedVariantTorsoRotation: variantTorsoRotation
    property real renderedVariantSkirtRotation: variantSkirtRotation
    property real renderedVariantHeadOffsetX: variantHeadOffsetX
                                               * (variantLayeredSideAware
                                                  ? habitatSideSign : 1.0)
    property real renderedVariantHeadOffsetY: variantHeadOffsetY
    property real renderedVariantTorsoOffsetX: variantTorsoOffsetX
                                                * (variantLayeredSideAware
                                                   ? habitatSideSign : 1.0)
    property real renderedVariantTorsoOffsetY: variantTorsoOffsetY
    property real renderedVariantSkirtOffsetX: variantSkirtOffsetX
                                               * (variantLayeredSideAware
                                                  ? habitatSideSign : 1.0)
    property real renderedVariantSkirtOffsetY: variantSkirtOffsetY
    property real renderedVariantHeadScaleX: variantHeadScaleX
    property real renderedVariantHeadScaleY: variantHeadScaleY
    property real renderedVariantTorsoScaleX: variantTorsoScaleX
    property real renderedVariantTorsoScaleY: variantTorsoScaleY
    property real renderedVariantSkirtScaleX: variantSkirtScaleX
    property real renderedVariantSkirtScaleY: variantSkirtScaleY
    property real renderedVariantHeadClipEnd: variantHeadClipEnd
    property real renderedVariantTorsoClipEnd: variantTorsoClipEnd
    readonly property bool poseTransitionRunning: presentationTransition.running
                                                   || characterScaleAnimation.running
                                                   || anchorXAnimation.running
                                                   || anchorYAnimation.running
                                                   || contactXAnimation.running
                                                   || contactYAnimation.running
                                                   || fallbackHeightAnimation.running
                                                   || habitatBlendAnimation.running
                                                   || artworkBlendAnimation.running
                                                   || artworkRatioAnimation.running
                                                   || cordAnchorXAnimation.running
                                                   || cordAnchorYAnimation.running
                                                   || layeredBodyRotationAnimation.running
                                                   || variantHeadRotationAnimation.running
                                                   || variantTorsoRotationAnimation.running
                                                   || variantSkirtRotationAnimation.running
                                                   || variantHeadOffsetXAnimation.running
                                                   || variantHeadOffsetYAnimation.running
                                                   || variantTorsoOffsetXAnimation.running
                                                   || variantTorsoOffsetYAnimation.running
                                                   || variantSkirtOffsetXAnimation.running
                                                   || variantSkirtOffsetYAnimation.running
                                                   || variantHeadScaleXAnimation.running
                                                   || variantHeadScaleYAnimation.running
                                                   || variantTorsoScaleXAnimation.running
                                                   || variantTorsoScaleYAnimation.running
                                                   || variantSkirtScaleXAnimation.running
                                                   || variantSkirtScaleYAnimation.running
                                                   || variantHeadClipAnimation.running
                                                   || variantTorsoClipAnimation.running

    function activeTransitionAnimations() {
        var active = []
        if (presentationTransition.running) active.push("presentation")
        if (characterScaleAnimation.running) active.push("character-scale")
        if (anchorXAnimation.running) active.push("anchor-x")
        if (anchorYAnimation.running) active.push("anchor-y")
        if (contactXAnimation.running) active.push("contact-x")
        if (contactYAnimation.running) active.push("contact-y")
        if (fallbackHeightAnimation.running) active.push("fallback-height")
        if (habitatBlendAnimation.running) active.push("habitat-blend")
        if (artworkBlendAnimation.running) active.push("artwork-blend")
        if (artworkRatioAnimation.running) active.push("artwork-ratio")
        if (cordAnchorXAnimation.running) active.push("cord-anchor-x")
        if (cordAnchorYAnimation.running) active.push("cord-anchor-y")
        if (layeredBodyRotationAnimation.running) active.push("body-rotation")
        if (variantHeadRotationAnimation.running) active.push("head-rotation")
        if (variantTorsoRotationAnimation.running) active.push("torso-rotation")
        if (variantSkirtRotationAnimation.running) active.push("skirt-rotation")
        if (variantHeadOffsetXAnimation.running) active.push("head-offset-x")
        if (variantHeadOffsetYAnimation.running) active.push("head-offset-y")
        if (variantTorsoOffsetXAnimation.running) active.push("torso-offset-x")
        if (variantTorsoOffsetYAnimation.running) active.push("torso-offset-y")
        if (variantSkirtOffsetXAnimation.running) active.push("skirt-offset-x")
        if (variantSkirtOffsetYAnimation.running) active.push("skirt-offset-y")
        if (variantHeadScaleXAnimation.running) active.push("head-scale-x")
        if (variantHeadScaleYAnimation.running) active.push("head-scale-y")
        if (variantTorsoScaleXAnimation.running) active.push("torso-scale-x")
        if (variantTorsoScaleYAnimation.running) active.push("torso-scale-y")
        if (variantSkirtScaleXAnimation.running) active.push("skirt-scale-x")
        if (variantSkirtScaleYAnimation.running) active.push("skirt-scale-y")
        if (variantHeadClipAnimation.running) active.push("head-clip")
        if (variantTorsoClipAnimation.running) active.push("torso-clip")
        return active
    }

    Behavior on renderedCharacterScale {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: characterScaleAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedAnchorNormX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: anchorXAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedAnchorNormY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: anchorYAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedContactX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: contactXAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedContactY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: contactYAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedFallbackPoseHeightFactor {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: fallbackHeightAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedHabitatBlend {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: habitatBlendAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    NumberAnimation {
        id: artworkBlendAnimation
        target: root
        property: "renderedArtworkBlend"
        duration: 220
        easing.type: Easing.OutCubic
    }
    Behavior on renderedPoseArtworkRatio {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: artworkRatioAnimation
            duration: 280
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedPoseCordAnchorX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: cordAnchorXAnimation
            duration: 220
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedPoseCordAnchorY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: cordAnchorYAnimation
            duration: 220
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedLayeredRequestedRotation {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: layeredBodyRotationAnimation
            duration: 300
            easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadRotation {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadRotationAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoRotation {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoRotationAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantSkirtRotation {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantSkirtRotationAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadOffsetX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadOffsetXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadOffsetY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadOffsetYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoOffsetX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoOffsetXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoOffsetY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoOffsetYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantSkirtOffsetX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantSkirtOffsetXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantSkirtOffsetY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantSkirtOffsetYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadScaleX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadScaleXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadScaleY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadScaleYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoScaleX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoScaleXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoScaleY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoScaleYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantSkirtScaleX {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantSkirtScaleXAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantSkirtScaleY {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantSkirtScaleYAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantHeadClipEnd {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantHeadClipAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }
    Behavior on renderedVariantTorsoClipEnd {
        enabled: !root.interactionSnap
        NumberAnimation {
            id: variantTorsoClipAnimation
            duration: 280; easing.type: Easing.OutCubic
        }
    }

    readonly property string outfitId: {
        if (!appBackend)
            return "first-encounter"
        var state = appBackend.wardrobeState
        if (!state)
            return "first-encounter"
        var current = state.current || state
        return String(current.outfit_id || current.outfitId || "first-encounter")
    }
    readonly property string outfitAssetKey: {
        switch (outfitId) {
        case "summer-cotton-dress": return "desktopPetSummer"
        case "home-cardigan": return "desktopPetHome"
        case "reading-smock": return "desktopPetReading"
        case "focus-coat": return "desktopPetFocus"
        case "rest-nightdress": return "desktopPetRest"
        default: return "desktopPet"
        }
    }
    // Outfit canvases share anchor contract v2.  Preserve each source's exact
    // aspect ratio, then align its solid-alpha subject inside the stable
    // figure frame.  The frame, rope endpoints and native hit bounds do not
    // jump when clothes change; all three breathing slices receive the same
    // image-space offset.
    readonly property int outfitAnchorVersion: 2
    readonly property real outfitArtworkAspectRatio: {
        switch (outfitAssetKey) {
        case "desktopPetSummer": return 941.0 / 1672.0
        case "desktopPetHome": return 940.0 / 1672.0
        case "desktopPetReading": return 941.0 / 1672.0
        case "desktopPetFocus": return 944.0 / 1666.0
        case "desktopPetRest": return 940.0 / 1672.0
        default: return 941.0 / 1672.0
        }
    }
    readonly property real outfitSolidCenterX: {
        switch (outfitAssetKey) {
        case "desktopPetSummer": return 939.0 / 1882.0
        case "desktopPetHome": return 936.0 / 1880.0
        case "desktopPetReading": return 940.0 / 1882.0
        case "desktopPetFocus": return 936.0 / 1888.0
        case "desktopPetRest": return 938.0 / 1880.0
        default: return 939.0 / 1882.0
        }
    }
    readonly property real outfitFeetY: {
        switch (outfitAssetKey) {
        case "desktopPetSummer": return 1599.0 / 1672.0
        case "desktopPetHome": return 1607.0 / 1672.0
        case "desktopPetReading": return 1646.0 / 1672.0
        case "desktopPetFocus": return 1595.0 / 1666.0
        case "desktopPetRest": return 1590.0 / 1672.0
        default: return 1599.0 / 1672.0
        }
    }
    readonly property real canonicalOutfitSolidCenterX: 939.0 / 1882.0
    readonly property real canonicalOutfitFeetY: 1599.0 / 1672.0
    readonly property real outfitHorizontalOffset: canonicalOutfitSolidCenterX
                                                   - outfitSolidCenterX
    readonly property real outfitVerticalOffset: canonicalOutfitFeetY - outfitFeetY
    readonly property real outfitSupportCordX: 0.39
    readonly property real outfitSupportCordY: 0.425
    readonly property url outfitSource: appBackend ? appBackend.assetUrl(outfitAssetKey) : ""
    readonly property string poseArtworkSpriteId: {
        switch (pose) {
        case "reading": return "reading"
        case "presenting": return "presenting"
        case "box-support": return "box-support"
        case "resting": return "resting"
        default: return ""
        }
    }
    readonly property string poseArtworkKey: {
        var optionalKey = String(habitatVariantDefinition.optionalArtworkAsset || "")
        // Optional generated concepts are dormant until an asset has passed
        // the transparent-master gate and the manifest explicitly opts in.
        // A mapped file alone is never enough: RGB checkerboards must keep
        // falling back to the three previously verified RGBA habitat poses.
        if (Boolean(habitatVariantDefinition.optionalArtworkEnabled)
                && optionalKey !== "" && appBackend
                && String(appBackend.assetUrl(optionalKey)) !== "")
            return optionalKey
        // These variants have a deliberately distinct all-outfit procedural
        // silhouette while their optional transparent masters are dormant.
        // Returning an empty key selects the audited layered outfit renderer;
        // it must not silently collapse back to the old edge/prone bitmap.
        if (Boolean(habitatVariantDefinition.proceduralLayeredFallback))
            return ""
        if (habitatProfile === "top-space-listen")
            return "poseListeningLive"
        switch (pose) {
        case "perch-prone": return "posePerchProne"
        case "title-sit": return "poseTitleSit"
        case "edge-peek-live": return "poseEdgePeek"
        case "listening-live": return "poseListeningLive"
        case "focus-watch": return "poseFocusKneel"
        case "reading":
        case "presenting":
        case "box-support":
        case "resting": return "poseExpansionSheet"
        default: return ""
        }
    }
    readonly property bool usesOptionalHabitatArtwork: {
        var optionalKey = String(habitatVariantDefinition.optionalArtworkAsset || "")
        return optionalKey !== "" && poseArtworkKey === optionalKey
    }
    readonly property var poseArtworkDefinition: {
        if (!appBackend)
            return ({})
        var manifest = appBackend.themeManifest || ({})
        var character = manifest.character || ({})
        var specifications = character.poseArtworkSpecs || ({})
        return specifications[poseArtworkKey] || ({})
    }
    readonly property var poseBundleDefinition: {
        if (!appBackend)
            return ({})
        var manifest = appBackend.themeManifest || ({})
        var character = manifest.character || ({})
        var bundles = character.poseBundles || ({})
        return bundles[pose] || ({})
    }
    readonly property var poseArtworkOutfits:
        String(habitatVariantDefinition.optionalArtworkAsset || "") === poseArtworkKey
        ? (habitatVariantDefinition.artworkOutfits || [])
        : (poseBundleDefinition.artworkOutfits || [])
    readonly property var poseArtworkSpriteDefinition: {
        var sprites = poseArtworkDefinition.sprites || ({})
        return sprites[poseArtworkSpriteId] || ({})
    }
    readonly property var poseArtworkClickMask:
        poseArtworkSpriteId !== ""
        ? (poseArtworkSpriteDefinition.clickMask || ({}))
        : (poseArtworkDefinition.clickMask || ({}))
    readonly property rect poseArtworkClipRect: {
        var values = poseArtworkSpriteDefinition.sourceRect
        if (!values || values.length !== 4)
            return Qt.rect(0, 0, 0, 0)
        var left = Number(values[0])
        var top = Number(values[1])
        var clipWidth = Number(values[2])
        var clipHeight = Number(values[3])
        if (!isFinite(left) || !isFinite(top)
                || !isFinite(clipWidth) || !isFinite(clipHeight)
                || clipWidth <= 0 || clipHeight <= 0)
            return Qt.rect(0, 0, 0, 0)
        return Qt.rect(left, top, clipWidth, clipHeight)
    }
    readonly property rect poseArtworkHitRect: {
        var clickMask = poseArtworkSpriteDefinition.clickMask || ({})
        var values = clickMask.rect
        if (!values || values.length !== 4)
            return Qt.rect(0, 0, 1, 1)
        var left = Number(values[0])
        var top = Number(values[1])
        var hitWidth = Number(values[2])
        var hitHeight = Number(values[3])
        if (!isFinite(left) || !isFinite(top)
                || !isFinite(hitWidth) || !isFinite(hitHeight)
                || hitWidth <= 0 || hitHeight <= 0)
            return Qt.rect(0, 0, 1, 1)
        return Qt.rect(left, top, hitWidth, hitHeight)
    }
    readonly property point poseArtworkAnchor: {
        var values = poseArtworkSpriteDefinition.anchor
                || poseArtworkDefinition.anchor
        if (!values || values.length !== 2)
            return Qt.point(0.5, 0.5)
        var anchorX = Number(values[0])
        var anchorY = Number(values[1])
        return isFinite(anchorX) && isFinite(anchorY)
                ? Qt.point(anchorX, anchorY) : Qt.point(0.5, 0.5)
    }
    readonly property point optionalPoseArtworkAnchor: Qt.point(
        variantArtworkSideAware && habitatMirror
        ? 1.0 - poseArtworkAnchor.x : poseArtworkAnchor.x,
        poseArtworkAnchor.y)
    readonly property point poseArtworkCordAnchor: {
        var values = poseArtworkSpriteDefinition.cordAnchor
                || poseArtworkDefinition.cordAnchor
        if (!values || values.length !== 2)
            return Qt.point(outfitSupportCordX, outfitSupportCordY)
        var anchorX = Number(values[0])
        var anchorY = Number(values[1])
        if (!isFinite(anchorX) || !isFinite(anchorY))
            return Qt.point(outfitSupportCordX, outfitSupportCordY)
        if (usesOptionalHabitatArtwork
                && variantArtworkSideAware && habitatMirror)
            anchorX = 1.0 - anchorX
        return Qt.point(anchorX, anchorY)
    }
    readonly property bool usesPoseArtwork: poseArtworkKey !== ""
                                               && poseArtworkRepresentsOutfit(outfitId)
    readonly property url poseArtworkSource: usesPoseArtwork && appBackend
                                               ? appBackend.assetUrl(poseArtworkKey) : ""

    function synchronizeArtworkBlend(animated) {
        // A requested source is not a renderable presentation until one of
        // the two image slots has decoded it successfully.  Keeping the
        // layered figure visible during the initial load (and after an error)
        // prevents the transparent tool window from showing only its cord and
        // shadow while an asynchronous Image is still empty.
        var targetBlend = poseArtworkFrame.shouldRenderArtwork ? 1.0 : 0.0
        artworkBlendSyncCount += 1
        artworkBlendAnimation.stop()
        if (!animated || Math.abs(renderedArtworkBlend - targetBlend) < 0.001) {
            renderedArtworkBlend = targetBlend
            return
        }
        artworkBlendAnimation.from = renderedArtworkBlend
        artworkBlendAnimation.to = targetBlend
        artworkBlendAnimation.restart()
    }

    function freezeGeometryAnimations() {
        if (geometryFrozenForInteraction)
            return
        geometryFrozenForInteraction = true
        // A pose cross-fade is a standalone animation rather than a Behavior.
        // Collapse it to the currently dominant decoded slot before freezing
        // geometry, otherwise two high-resolution alpha images keep blending
        // for another 220 ms after the user has grabbed the window.
        poseArtworkFrame.stabilizeTransition()
        // Disabling a Behavior prevents the next transition but does not stop
        // an animation that is already running.  Self-assignment while every
        // Behavior is disabled cancels that animation at its current pixel
        // value, so a press cannot spend its first 220--300 ms relaying out
        // the silhouette, click mask and rope while the native window moves.
        renderedCharacterScale = renderedCharacterScale
        renderedAnchorNormX = renderedAnchorNormX
        renderedAnchorNormY = renderedAnchorNormY
        renderedContactX = renderedContactX
        renderedContactY = renderedContactY
        renderedFallbackPoseHeightFactor = renderedFallbackPoseHeightFactor
        renderedHabitatBlend = renderedHabitatBlend
        renderedPoseArtworkRatio = renderedPoseArtworkRatio
        renderedPoseCordAnchorX = renderedPoseCordAnchorX
        renderedPoseCordAnchorY = renderedPoseCordAnchorY
        renderedLayeredRequestedRotation = renderedLayeredRequestedRotation
        renderedVariantHeadRotation = renderedVariantHeadRotation
        renderedVariantTorsoRotation = renderedVariantTorsoRotation
        renderedVariantSkirtRotation = renderedVariantSkirtRotation
        renderedVariantHeadOffsetX = renderedVariantHeadOffsetX
        renderedVariantHeadOffsetY = renderedVariantHeadOffsetY
        renderedVariantTorsoOffsetX = renderedVariantTorsoOffsetX
        renderedVariantTorsoOffsetY = renderedVariantTorsoOffsetY
        renderedVariantSkirtOffsetX = renderedVariantSkirtOffsetX
        renderedVariantSkirtOffsetY = renderedVariantSkirtOffsetY
        renderedVariantHeadScaleX = renderedVariantHeadScaleX
        renderedVariantHeadScaleY = renderedVariantHeadScaleY
        renderedVariantTorsoScaleX = renderedVariantTorsoScaleX
        renderedVariantTorsoScaleY = renderedVariantTorsoScaleY
        renderedVariantSkirtScaleX = renderedVariantSkirtScaleX
        renderedVariantSkirtScaleY = renderedVariantSkirtScaleY
        renderedVariantHeadClipEnd = renderedVariantHeadClipEnd
        renderedVariantTorsoClipEnd = renderedVariantTorsoClipEnd
    }

    function restoreGeometryBindings() {
        if (!geometryFrozenForInteraction)
            return
        geometryFrozenForInteraction = false
        renderedCharacterScale = Qt.binding(function() {
            return root.habitatCharacterScale
        })
        renderedAnchorNormX = Qt.binding(function() {
            return root.habitatAnchorNormX
        })
        renderedAnchorNormY = Qt.binding(function() {
            return root.habitatAnchorNormY
        })
        renderedContactX = Qt.binding(function() {
            return root.habitatContactX
        })
        renderedContactY = Qt.binding(function() {
            return root.habitatContactY
        })
        renderedFallbackPoseHeightFactor = Qt.binding(function() {
            return root.fallbackPoseHeightFactor
        })
        renderedHabitatBlend = Qt.binding(function() {
            return root.habitatLayoutActive ? 1.0 : 0.0
        })
        renderedPoseArtworkRatio = Qt.binding(function() {
            return root.poseArtworkAspectRatio
        })
        renderedPoseCordAnchorX = Qt.binding(function() {
            return root.poseArtworkCordAnchor.x
        })
        renderedPoseCordAnchorY = Qt.binding(function() {
            return root.poseArtworkCordAnchor.y
        })
        renderedLayeredRequestedRotation = Qt.binding(function() {
            return root.layeredPoseRotation
        })
        renderedVariantHeadRotation = Qt.binding(function() {
            return root.variantHeadRotation
        })
        renderedVariantTorsoRotation = Qt.binding(function() {
            return root.variantTorsoRotation
        })
        renderedVariantSkirtRotation = Qt.binding(function() {
            return root.variantSkirtRotation
        })
        renderedVariantHeadOffsetX = Qt.binding(function() {
            return root.variantHeadOffsetX
                   * (root.variantLayeredSideAware ? root.habitatSideSign : 1.0)
        })
        renderedVariantHeadOffsetY = Qt.binding(function() {
            return root.variantHeadOffsetY
        })
        renderedVariantTorsoOffsetX = Qt.binding(function() {
            return root.variantTorsoOffsetX
                   * (root.variantLayeredSideAware ? root.habitatSideSign : 1.0)
        })
        renderedVariantTorsoOffsetY = Qt.binding(function() {
            return root.variantTorsoOffsetY
        })
        renderedVariantSkirtOffsetX = Qt.binding(function() {
            return root.variantSkirtOffsetX
                   * (root.variantLayeredSideAware ? root.habitatSideSign : 1.0)
        })
        renderedVariantSkirtOffsetY = Qt.binding(function() {
            return root.variantSkirtOffsetY
        })
        renderedVariantHeadScaleX = Qt.binding(function() {
            return root.variantHeadScaleX
        })
        renderedVariantHeadScaleY = Qt.binding(function() {
            return root.variantHeadScaleY
        })
        renderedVariantTorsoScaleX = Qt.binding(function() {
            return root.variantTorsoScaleX
        })
        renderedVariantTorsoScaleY = Qt.binding(function() {
            return root.variantTorsoScaleY
        })
        renderedVariantSkirtScaleX = Qt.binding(function() {
            return root.variantSkirtScaleX
        })
        renderedVariantSkirtScaleY = Qt.binding(function() {
            return root.variantSkirtScaleY
        })
        renderedVariantHeadClipEnd = Qt.binding(function() {
            return root.variantHeadClipEnd
        })
        renderedVariantTorsoClipEnd = Qt.binding(function() {
            return root.variantTorsoClipEnd
        })
    }

    onUsesPoseArtworkChanged: synchronizeArtworkBlend(!interactionSnap)
    // `usesPoseArtwork` contains a JavaScript outfit-policy call. Some Qt
    // builds do not emit its derived-property notifier after a long chain of
    // rapid habitat changes, even though querying the value already returns
    // the new result. The primitive key/outfit notifiers are reliable and
    // keep the blend converged on that same authoritative value.
    onPoseArtworkKeyChanged: synchronizeArtworkBlend(!interactionSnap)
    onOutfitIdChanged: synchronizeArtworkBlend(!interactionSnap)
    onInteractionSnapChanged: {
        if (interactionSnap) {
            synchronizeArtworkBlend(false)
            freezeGeometryAnimations()
        } else {
            restoreGeometryBindings()
        }
    }
    Component.onCompleted: synchronizeArtworkBlend(false)
    // Do not derive layout from Image.implicitWidth while an asynchronous
    // source is loading: it briefly reports zero and visibly snaps the
    // contact anchor.  These ratios come from the checked transparent masters.
    readonly property real poseArtworkAspectRatio: {
        var clipWidth = poseArtworkClipRect.width
        var clipHeight = poseArtworkClipRect.height
        if (clipWidth > 0 && clipHeight > 0)
            return clipWidth / clipHeight
        var manifestRatio = poseArtworkManifestRatio()
        if (manifestRatio > 0.0)
            return manifestRatio
        switch (poseArtworkKey) {
        case "poseMicroCornerGripV1": return 1536.0 / 1536.0
        case "poseWindowProneV2": return 1536.0 / 1024.0
        case "poseWindowDangleV1": return 1280.0 / 1536.0
        case "poseWideWindowSprawlV1": return 2048.0 / 1024.0
        case "poseEdgeLeanV1": return 1024.0 / 2048.0
        case "posePerchProne": return 365.0 / 364.0
        case "poseTitleSit": return 350.0 / 592.0
        case "poseEdgePeek": return 227.0 / 620.0
        case "poseListeningLive": return 314.0 / 614.0
        case "poseFocusKneel": return 1145.0 / 1374.0
        default: return 0.545
        }
    }
    readonly property real layeredSupportCordX: habitatMirror
                                                 ? 1.0 - outfitSupportCordX
                                                 : outfitSupportCordX
    readonly property point layeredSupportCordPoint: {
        var tracking = figureFrame.x + figureFrame.y
                     + figureFrame.width + figureFrame.height
                     + renderedLayeredBodyRotation
                     + (habitatMirror ? 1.0 : 0.0)
        return figureFrame.mapToItem(
                    root,
                    figureFrame.width * layeredSupportCordX,
                    figureFrame.height * outfitSupportCordY)
    }
    readonly property point artworkSupportCordPoint: Qt.point(
        poseArtworkFrame.x
        + poseArtworkFrame.width * renderedPoseCordAnchorX,
        poseArtworkFrame.y
        + poseArtworkFrame.height * renderedPoseCordAnchorY)
    readonly property point supportCordPoint: {
        // Follow the representation that is actually painted, not merely a
        // Ready image retained in the inactive/failure cache. A failed target
        // leaves the previous slot Ready while renderedArtworkBlend converges
        // to zero; using that stale slot pulled the cord away from the layered
        // hand after fallback.
        var artworkWeight = poseArtworkFrame.hasReadyPresentation
                ? Math.max(0.0, Math.min(1.0, renderedArtworkBlend)) : 0.0
        return Qt.point(
            layeredSupportCordPoint.x * (1.0 - artworkWeight)
                + artworkSupportCordPoint.x * artworkWeight,
            layeredSupportCordPoint.y * (1.0 - artworkWeight)
                + artworkSupportCordPoint.y * artworkWeight)
    }
    readonly property bool characterHovered: characterPointer.containsMouse
    readonly property bool characterPressed: characterPointer.pressed
    readonly property bool animationRunning: visible && !paused
    readonly property int targetFps: paused ? 0 : (lowPower ? 15 : 60)

    signal characterClicked()
    signal characterPressStarted(real x, real y)
    signal characterPointerMoved(real x, real y)
    signal characterReleased(bool moved)
    signal characterCanceled(bool moved)
    signal wheelStepped(real steps)

    property real motionPhase: 0.0
    // Habitat artwork has a separate phase so its window-size-specific
    // cadence never changes the layered character's 3.4 s breathing rhythm.
    // Every transform below uses the declared contact point as its origin;
    // the hand/cheek therefore remains pinned to the host edge while the
    // silhouette leans, watches or grips around it.
    property real habitatMotionPhase: 0.0
    property real pressX: 0.0
    property real pressY: 0.0
    property bool pointerMoved: false
    readonly property real dragActivationDistance: 4.0

    readonly property real poseShiftX: {
        switch (pose) {
        case "edge-peek":
        case "edge-peek-live": return width * 0.026
        case "listening":
        case "listening-live": return width * 0.010
        case "presenting": return -width * 0.012
        case "box-support": return -width * 0.016
        default: return 0
        }
    }
    readonly property real poseShiftY: {
        switch (pose) {
        case "perch-top":
        case "perch-prone": return height * 0.018
        case "reading": return height * 0.012
        case "focus-watch": return -height * 0.006
        case "resting": return height * 0.035
        default: return 0
        }
    }
    readonly property real poseRotation: {
        switch (pose) {
        case "perch-top":
        case "perch-prone": return -1.0
        case "edge-peek":
        case "edge-peek-live": return 2.2
        case "listening":
        case "listening-live": return -1.4
        case "focus-watch": return 0.35
        case "reading": return 0.7
        case "presenting": return -1.0
        case "box-support": return -0.7
        case "resting": return -2.4
        default: return 0
        }
    }
    readonly property real headPoseRotation: {
        switch (pose) {
        case "edge-peek":
        case "edge-peek-live": return 1.4
        case "listening":
        case "listening-live": return -2.0
        case "focus-watch": return 1.25
        case "reading": return 2.0
        case "resting": return -1.5
        default: return 0
        }
    }
    readonly property real torsoPoseRotation: {
        switch (pose) {
        case "focus-watch": return -0.45
        case "presenting": return -0.7
        case "box-support": return 0.8
        case "resting": return -1.0
        default: return 0
        }
    }
    readonly property real habitatSideSign: habitatMirror ? -1.0 : 1.0
    readonly property real requestedPoseArtworkMotionRotation: {
        var wave = Math.sin(habitatMotionPhase)
        var baseRotation = 0.0
        switch (habitatMotionStyle) {
        case "screen-watch":
            baseRotation = habitatSideSign * (-0.85 + wave * 0.55)
            break
        case "cautious-peek":
            baseRotation = habitatSideSign * (0.55 + wave * 0.35)
            break
        case "caption-lean":
            baseRotation = habitatSideSign * (1.60 + wave * 0.40)
            break
        case "corner-grip":
            baseRotation = habitatSideSign * (-0.55 + wave * 0.28)
            break
        case "edge-listen":
            baseRotation = habitatSideSign * (0.35 + wave * 0.28)
            break
        case "title-curl":
            baseRotation = -0.20 + wave * 0.24
            break
        case "title-balance":
            baseRotation = wave * 0.65
            break
        case "portrait-listen":
            baseRotation = -0.45 + wave * 0.32
            break
        case "perch-tuck":
            baseRotation = -0.30 + wave * 0.16
            break
        case "perch-stretch":
            baseRotation = habitatSideSign * (0.65 + wave * 0.35)
            break
        case "perch-drift":
            baseRotation = 0.35 + wave * 0.22
            break
        case "perch-breathe":
            baseRotation = 0.12 + wave * 0.18
            break
        default:
            baseRotation = 0.0
            break
        }
        var biasSign = variantArtworkSideAware ? habitatSideSign : 1.0
        return baseRotation * variantArtworkMotionGain
             + variantArtworkRotationBias * biasSign
    }
    // A baked bitmap cannot breathe by stretching as a whole without making
    // the head, feet and contact edge rubbery.  Keep these public transform
    // values for verifier/API stability, but make them strict identities.
    // The layered idle character still breathes locally through independent
    // chest, shoulder, hair and skirt slices below.
    readonly property real poseArtworkMotionScaleX: 1.0
    readonly property real poseArtworkMotionScaleY: 1.0
    readonly property real poseArtworkMotionRotation:
        boundedPoseArtworkRotation(requestedPoseArtworkMotionRotation)

    function poseArtworkRepresentsOutfit(candidateOutfitId) {
        var values = poseArtworkOutfits
        if (!values || values.length === 0)
            return false
        var candidate = String(candidateOutfitId || "")
        for (var index = 0; index < values.length; ++index) {
            var declared = String(values[index])
            if (declared === "*" || declared === candidate)
                return true
        }
        return false
    }

    function rotatedPoseArtworkInside(angle) {
        if (!isFinite(Number(angle)) || poseArtworkFrame.width <= 0
                || poseArtworkFrame.height <= 0)
            return true
        var radians = Number(angle) * Math.PI / 180.0
        var cosine = Math.cos(radians)
        var sine = Math.sin(radians)
        var originX = poseArtworkFrame.x
                + poseArtworkFrame.width * renderedContactX
        var originY = poseArtworkFrame.y
                + poseArtworkFrame.height * renderedContactY
        var corners = [
            [poseArtworkFrame.x, poseArtworkFrame.y],
            [poseArtworkFrame.x + poseArtworkFrame.width, poseArtworkFrame.y],
            [poseArtworkFrame.x, poseArtworkFrame.y + poseArtworkFrame.height],
            [poseArtworkFrame.x + poseArtworkFrame.width,
             poseArtworkFrame.y + poseArtworkFrame.height]
        ]
        for (var index = 0; index < corners.length; ++index) {
            var dx = corners[index][0] - originX
            var dy = corners[index][1] - originY
            var mappedX = originX + dx * cosine - dy * sine
            var mappedY = originY + dx * sine + dy * cosine
            if (mappedX < -0.05 || mappedY < -0.05
                    || mappedX > width + 0.05 || mappedY > height + 0.05)
                return false
        }
        return true
    }

    function boundedPoseArtworkRotation(requestedAngle) {
        var requested = Number(requestedAngle)
        if (!isFinite(requested) || Math.abs(requested) < 0.0001)
            return 0.0
        if (rotatedPoseArtworkInside(requested))
            return requested
        // Reserve the largest safe part of the subtle rotation instead of
        // letting the native transparent tool window clip hair or dress hems.
        var sign = requested < 0 ? -1.0 : 1.0
        var low = 0.0
        var high = Math.abs(requested)
        for (var step = 0; step < 10; ++step) {
            var middle = (low + high) / 2.0
            if (rotatedPoseArtworkInside(sign * middle))
                low = middle
            else
                high = middle
        }
        return sign * low
    }

    function rotatedLayeredFrameInside(angle) {
        if (!isFinite(Number(angle)) || figureFrame.width <= 0
                || figureFrame.height <= 0)
            return true
        var radians = Number(angle) * Math.PI / 180.0
        var cosine = Math.cos(radians)
        var sine = Math.sin(radians)
        var localOriginX = figureFrame.width
                * (habitatLayoutActive ? renderedContactX : 0.5)
        var localOriginY = figureFrame.height
                * (habitatLayoutActive ? renderedContactY : 1.0)
        var originX = figureFrame.x + localOriginX
        var originY = figureFrame.y + localOriginY
        var corners = [
            [figureFrame.x, figureFrame.y],
            [figureFrame.x + figureFrame.width, figureFrame.y],
            [figureFrame.x, figureFrame.y + figureFrame.height],
            [figureFrame.x + figureFrame.width,
             figureFrame.y + figureFrame.height]
        ]
        for (var index = 0; index < corners.length; ++index) {
            var dx = corners[index][0] - originX
            var dy = corners[index][1] - originY
            var mappedX = originX + dx * cosine - dy * sine
            var mappedY = originY + dx * sine + dy * cosine
            if (mappedX < -0.05 || mappedY < -0.05
                    || mappedX > width + 0.05 || mappedY > height + 0.05)
                return false
        }
        return true
    }

    function boundedLayeredRotation(requestedAngle) {
        var requested = Number(requestedAngle)
        if (!isFinite(requested) || Math.abs(requested) < 0.0001)
            return 0.0
        if (rotatedLayeredFrameInside(requested))
            return requested
        var sign = requested < 0 ? -1.0 : 1.0
        var low = 0.0
        var high = Math.abs(requested)
        for (var step = 0; step < 10; ++step) {
            var middle = (low + high) / 2.0
            if (rotatedLayeredFrameInside(sign * middle))
                low = middle
            else
                high = middle
        }
        return sign * low
    }

    function normalizedPoint(point, fallbackX, fallbackY) {
        if (point && isFinite(Number(point.x)) && isFinite(Number(point.y)))
            return Qt.point(Number(point.x), Number(point.y))
        return Qt.point(fallbackX, fallbackY)
    }

    function poseArtworkManifestRatio() {
        var pixelSize = poseArtworkDefinition.pixelSize
        if (!pixelSize || pixelSize.length !== 2)
            return 0.0
        var pixelWidth = Number(pixelSize[0])
        var pixelHeight = Number(pixelSize[1])
        return isFinite(pixelWidth) && isFinite(pixelHeight)
                && pixelWidth > 0 && pixelHeight > 0
                ? pixelWidth / pixelHeight : 0.0
    }

    function boundedVariantNumber(container, name, fallbackValue, low, high) {
        if (!container)
            return fallbackValue
        var value = Number(container[name])
        if (!isFinite(value))
            return fallbackValue
        return Math.max(low, Math.min(high, value))
    }

    function boundedVariantOffset(name, index) {
        var values = habitatVariantLayered[name]
        if (!values || values.length !== 2)
            return 0.0
        var value = Number(values[index])
        return isFinite(value) ? Math.max(-0.12, Math.min(0.12, value)) : 0.0
    }

    function boundedVariantScale(name, index) {
        var values = habitatVariantLayered[name]
        if (!values || values.length !== 2)
            return 1.0
        var value = Number(values[index])
        return isFinite(value) ? Math.max(0.60, Math.min(1.40, value)) : 1.0
    }

    function itemBoundsInRoot(item, dependencyValue) {
        // dependencyValue is intentionally unused after being read by the
        // binding caller; it makes changes to a QQuickItem transform visible
        // to the declarative dependency tracker.
        if (!item || item.width <= 0 || item.height <= 0)
            return Qt.rect(0, 0, 0, 0)
        var corners = [
            item.mapToItem(root, 0.0, 0.0),
            item.mapToItem(root, item.width, 0.0),
            item.mapToItem(root, 0.0, item.height),
            item.mapToItem(root, item.width, item.height)
        ]
        var left = corners[0].x
        var top = corners[0].y
        var right = corners[0].x
        var bottom = corners[0].y
        for (var index = 1; index < corners.length; ++index) {
            left = Math.min(left, corners[index].x)
            top = Math.min(top, corners[index].y)
            right = Math.max(right, corners[index].x)
            bottom = Math.max(bottom, corners[index].y)
        }
        return Qt.rect(left, top, right - left, bottom - top)
    }

    function finiteHabitatValue(name, fallbackValue) {
        if (!habitatLayoutActive)
            return fallbackValue
        var value = Number(habitatLayout[name])
        return isFinite(value) ? Math.max(0.0, Math.min(1.0, value)) : fallbackValue
    }

    function habitatFrameX(frameWidth, fallbackX) {
        // The rendered anchor and contact already animate from the centred
        // layered pose into the habitat contract.  Keeping this equation exact
        // makes the visible contact point invariant on every transition frame.
        return width * renderedAnchorNormX - frameWidth * renderedContactX
    }

    function habitatFrameY(frameHeight, fallbackY) {
        return height * renderedAnchorNormY - frameHeight * renderedContactY
    }

    function containsCharacterPoint(rootX, rootY) {
        // QML input and Main.qml's native hit-region publisher both call this
        // function.  During either cross-fade, accept the union of the layers
        // that actually contribute visible pixels.  A zero-opacity incoming
        // pose therefore cannot create a hit island, while the outgoing
        // silhouette remains draggable until it has really faded away.
        var artworkVisible = renderedArtworkBlend > 0.001
                && poseArtworkFrame.hasReadyPresentation
        if (artworkVisible) {
            var artworkPoint = poseArtworkFrame.mapFromItem(root, Number(rootX), Number(rootY))
            if (poseArtworkMask.contains(artworkPoint))
                return true
        }
        if (1.0 - renderedArtworkBlend > 0.001) {
            var point = figureFrame.mapFromItem(root, Number(rootX), Number(rootY))
            if (silhouetteMask.contains(point))
                return true
        }
        return false
    }

    function normalizedCharacterGrab(rootX, rootY) {
        // Store the grabbed source-relative character point instead of a
        // coordinate in the much larger transparent pet window.  Habitat
        // detach can replace a prone/edge silhouette with the free-standing
        // layered figure on the first drag frame; this canonical point lets
        // Main.qml put the same part of Lilith back under the pointer.
        var frame = renderedArtworkBlend >= 0.5
                && poseArtworkFrame.hasReadyPresentation
                ? poseArtworkFrame : figureFrame
        if (!frame || frame.width <= 0 || frame.height <= 0)
            return ({ "valid": false, "x": 0.5, "y": 0.5 })
        var point = frame.mapFromItem(root, Number(rootX), Number(rootY))
        var normalizedX = Math.max(0.0, Math.min(1.0,
            Number(point.x) / Math.max(1.0, frame.width)))
        var normalizedY = Math.max(0.0, Math.min(1.0,
            Number(point.y) / Math.max(1.0, frame.height)))
        // Image.mirror reverses the visible X axis.  Store canonical artwork
        // space so a mirrored edge pose maps to the same anatomical side when
        // the detached standing pose is no longer mirrored.
        if (habitatMirror)
            normalizedX = 1.0 - normalizedX
        return ({ "valid": true, "x": normalizedX, "y": normalizedY })
    }

    function characterPointForNormalizedGrab(normalizedX, normalizedY) {
        var frame = renderedArtworkBlend >= 0.5
                && poseArtworkFrame.hasReadyPresentation
                ? poseArtworkFrame : figureFrame
        if (!frame || frame.width <= 0 || frame.height <= 0)
            return Qt.point(root.width * 0.5, root.height * 0.5)
        normalizedX = Math.max(0.0, Math.min(1.0, Number(normalizedX)))
        normalizedY = Math.max(0.0, Math.min(1.0, Number(normalizedY)))
        if (habitatMirror)
            normalizedX = 1.0 - normalizedX
        return frame.mapToItem(root,
            frame.width * normalizedX, frame.height * normalizedY)
    }

    function dragDisplacementExceeded(dx, dy) {
        dx = Number(dx)
        dy = Number(dy)
        // Measure the real pointer travel.  A Manhattan-distance check made
        // small diagonal hand jitter such as (3, 2) cross the 4 px boundary
        // even though the pointer had only travelled about 3.6 px.
        return dx * dx + dy * dy
               > dragActivationDistance * dragActivationDistance
    }

    function beginPointer(x, y) {
        root.pressX = x
        root.pressY = y
        root.pointerMoved = false
        root.characterPressStarted(x, y)
    }

    function movePointer(x, y, pressed) {
        if (!pressed)
            return
        var dx = x - root.pressX
        var dy = y - root.pressY
        if (root.dragDisplacementExceeded(dx, dy))
            root.pointerMoved = true
        // Send the current pointer position, not an accumulated delta. The
        // host combines it with the moving window origin to recover the
        // global cursor position without feedback or double accumulation.
        root.characterPointerMoved(x, y)
    }

    function endPointer() {
        var moved = root.pointerMoved
        root.characterReleased(moved)
        if (!moved)
            root.characterClicked()
    }

    Timer {
        id: motionClock
        interval: root.lowPower ? 67 : 16
        repeat: true
        running: root.animationRunning
        onTriggered: {
            root.motionPhase = (root.motionPhase + interval / 1000.0 * Math.PI * 2.0 / 3.4)
                               % (Math.PI * 2.0)
            root.habitatMotionPhase = (
                root.habitatMotionPhase
                + interval / 1000.0 * Math.PI * 2.0 / root.habitatMotionPeriod
            ) % (Math.PI * 2.0)
            supportCord.advance()
        }
    }

    // The shadow is intentionally independent of the painted character layers.
    Rectangle {
        id: groundShadow
        objectName: "petGroundShadowLayer"
        visible: root.renderedArtworkBlend < 0.999
        z: 0
        width: root.figureWidth * (root.pose === "perch-prone" ? 0.76
                                                            : 0.43 + Math.sin(root.motionPhase + 1.7) * 0.008)
        height: Math.max(3, root.figureHeight * 0.012)
        x: root.figureLeft + (root.figureWidth - width) / 2
        y: root.figureTop + root.figureHeight * 0.965
        radius: height / 2
        color: "#514b45"
        opacity: (root.paused ? 0.09 : 0.105 + Math.sin(root.motionPhase + 1.7) * 0.012)
                 * (1.0 - root.renderedArtworkBlend)
    }

    Canvas {
        id: supportCord
        objectName: "desktopPetCordV03"
        anchors.fill: parent
        z: 1
        antialiasing: true

        property var nodes: []
        property real segmentLength: 1.0

        function startPoint() {
            return root.normalizedPoint(root.cordStart,
                                        figureFrame.x + figureFrame.width
                                        * root.outfitSupportCordX,
                                        figureFrame.y + figureFrame.height
                                        * root.outfitSupportCordY)
        }

        function endPoint() {
            return root.normalizedPoint(root.cordEnd, root.width * 0.88, root.height * 0.58)
        }

        function resetCord() {
            var start = startPoint()
            var end = endPoint()
            var nextNodes = []
            var count = Math.max(12, Math.min(16, root.cordNodeCount))
            var dx = end.x - start.x
            var dy = end.y - start.y
            var straightLength = Math.max(1, Math.sqrt(dx * dx + dy * dy))
            segmentLength = straightLength * 1.055 / (count - 1)
            for (var i = 0; i < count; ++i) {
                var ratio = i / (count - 1)
                var sag = Math.sin(ratio * Math.PI) * Math.min(root.height * 0.045,
                                                           straightLength * 0.11)
                var px = start.x + dx * ratio
                var py = start.y + dy * ratio + sag
                nextNodes.push({"x": px, "y": py, "oldX": px, "oldY": py})
            }
            nodes = nextNodes
            requestPaint()
        }

        function pinEndpoints() {
            if (nodes.length < 2)
                return
            var start = startPoint()
            var end = endPoint()
            nodes[0].x = start.x
            nodes[0].y = start.y
            nodes[0].oldX = start.x
            nodes[0].oldY = start.y
            var last = nodes.length - 1
            nodes[last].x = end.x
            nodes[last].y = end.y
            nodes[last].oldX = end.x
            nodes[last].oldY = end.y
        }

        function satisfyConstraints() {
            for (var pass = 0; pass < 5; ++pass) {
                pinEndpoints()
                for (var i = 0; i < nodes.length - 1; ++i) {
                    var left = nodes[i]
                    var right = nodes[i + 1]
                    var dx = right.x - left.x
                    var dy = right.y - left.y
                    var distance = Math.max(0.001, Math.sqrt(dx * dx + dy * dy))
                    var correction = (distance - segmentLength) / distance
                    if (i === 0) {
                        right.x -= dx * correction
                        right.y -= dy * correction
                    } else if (i + 1 === nodes.length - 1) {
                        left.x += dx * correction
                        left.y += dy * correction
                    } else {
                        left.x += dx * correction * 0.5
                        left.y += dy * correction * 0.5
                        right.x -= dx * correction * 0.5
                        right.y -= dy * correction * 0.5
                    }
                }
            }
            pinEndpoints()
        }

        function advance() {
            var expected = Math.max(12, Math.min(16, root.cordNodeCount))
            if (nodes.length !== expected) {
                resetCord()
                return
            }
            var gravity = Math.max(0.015, root.height * 0.00004)
            for (var i = 1; i < nodes.length - 1; ++i) {
                var node = nodes[i]
                var vx = (node.x - node.oldX) * 0.91
                var vy = (node.y - node.oldY) * 0.91
                node.oldX = node.x
                node.oldY = node.y
                node.x += vx
                node.y += vy + gravity
            }
            satisfyConstraints()
            requestPaint()
        }

        function strokeCord(context, color, lineWidth, alpha) {
            if (nodes.length < 2)
                return
            context.globalAlpha = alpha
            context.strokeStyle = color
            context.lineWidth = lineWidth
            context.lineCap = "round"
            context.lineJoin = "round"
            context.beginPath()
            context.moveTo(nodes[0].x, nodes[0].y)
            for (var i = 1; i < nodes.length - 1; ++i) {
                var midX = (nodes[i].x + nodes[i + 1].x) * 0.5
                var midY = (nodes[i].y + nodes[i + 1].y) * 0.5
                context.quadraticCurveTo(nodes[i].x, nodes[i].y, midX, midY)
            }
            var last = nodes.length - 1
            context.quadraticCurveTo(nodes[last - 1].x, nodes[last - 1].y,
                                     nodes[last].x, nodes[last].y)
            context.stroke()
        }

        onPaint: {
            var context = getContext("2d")
            context.clearRect(0, 0, width, height)
            strokeCord(context, "#3b1b19", Math.max(2.2, root.width * 0.009), 0.20)
            strokeCord(context, root.cordColor, Math.max(1.25, root.width * 0.0055), 0.90)
            context.globalAlpha = 1.0
        }

        Component.onCompleted: resetCord()
        onWidthChanged: resetCord()
        onHeightChanged: resetCord()
    }

    onCordStartChanged: supportCord.resetCord()
    onCordEndChanged: supportCord.resetCord()
    onCordNodeCountChanged: supportCord.resetCord()

    Item {
        id: poseArtworkFrame
        objectName: "petPoseArtworkFrame"
        visible: hasReadyPresentation && root.renderedArtworkBlend > 0.001
        opacity: root.renderedArtworkBlend
        z: 3
        property url displayedSource: ""
        property url outgoingSource: ""
        property bool displayedMirror: false
        property bool outgoingMirror: false
        property rect displayedClipRect: Qt.rect(0, 0, 0, 0)
        property rect outgoingClipRect: Qt.rect(0, 0, 0, 0)
        property var displayedClickMask: ({})
        property var outgoingClickMask: ({})
        property real presentationProgress: 1.0
        // Two persistent Image slots are alternated.  The currently committed
        // Ready slot is never assigned a new URL; the inactive slot decodes
        // the request and only becomes incoming after Image.Ready.
        property int activeSlot: 0
        property int loadingSlot: 0
        property int transitionFromSlot: 0
        property int transitionToSlot: 0
        property int requestSerial: 0
        property bool requestPending: false
        property bool holdReadyArtworkDuringLoad: false
        property bool targetLoadFailed: false
        property url slotASource: ""
        property url slotBSource: ""
        property bool slotAMirror: false
        property bool slotBMirror: false
        property rect slotAClipRect: Qt.rect(0, 0, 0, 0)
        property rect slotBClipRect: Qt.rect(0, 0, 0, 0)
        property var slotAClickMask: ({})
        property var slotBClickMask: ({})
        readonly property bool transitionActive:
            transitionFromSlot !== 0 && transitionToSlot !== 0
        readonly property bool activeSlotReady:
            activeSlot === 1 ? artworkSlotA.status === Image.Ready
                             : activeSlot === 2 ? artworkSlotB.status === Image.Ready
                                                : false
        readonly property bool hasReadyPresentation: activeSlotReady
                                                     || transitionActive
        readonly property bool committedMatchesRequest:
            activeSlotReady
            && String(displayedSource) === String(root.poseArtworkSource || "")
            && displayedMirror === root.habitatMirror
            && clipRectsEqual(displayedClipRect, root.poseArtworkClipRect)
        readonly property bool shouldRenderArtwork:
            root.usesPoseArtwork && !targetLoadFailed
            && (committedMatchesRequest || transitionActive
                || ((requestPending || loadingSlot !== 0)
                    && holdReadyArtworkDuringLoad))
        readonly property real artworkRatio: root.renderedPoseArtworkRatio
        // Animate the pose silhouette factor together with the contact anchor.
        // Otherwise title-sit (0.90) -> perch (0.58) changes this frame in one
        // tick even though the artwork itself is cross-fading.
        // Preserve the source clip's exact aspect ratio even when an unusually
        // narrow work area constrains the pet window.  Capping only `width`
        // used to make the Image letterbox inside a taller frame; the
        // manifest hit mask and cord anchor were then evaluated against that
        // letterboxed frame rather than the painted sprite.  Fitting both
        // dimensions keeps artwork, click mask and anchors in one coordinate
        // system from the emergency compact size through high-DPI layouts.
        readonly property real requestedHeight:
            root.characterHeight * root.renderedFallbackPoseHeightFactor
                                 * root.renderedCharacterScale
        height: Math.min(requestedHeight,
                         root.width * 0.72 / Math.max(0.001, artworkRatio))
        width: height * artworkRatio
        readonly property real fallbackX: (root.width - width) / 2 + root.poseShiftX
        readonly property real fallbackY: (root.pose === "perch-prone"
                                            ? root.height - height - root.height * 0.12
                                            : (root.pose === "title-sit"
                                               ? root.height - height - 5
                                               : (root.height - height) / 2 + root.poseShiftY))
        x: root.habitatFrameX(width, fallbackX)
        y: root.habitatFrameY(height, fallbackY)
        transform: [
            Scale {
                objectName: "petPoseProfileScale"
                origin.x: poseArtworkFrame.width * root.renderedContactX
                origin.y: poseArtworkFrame.height * root.renderedContactY
                xScale: root.poseArtworkMotionScaleX
                yScale: root.poseArtworkMotionScaleY
            },
            Rotation {
                objectName: "petPoseProfileRotation"
                origin.x: poseArtworkFrame.width * root.renderedContactX
                origin.y: poseArtworkFrame.height * root.renderedContactY
                angle: root.poseArtworkMotionRotation
            }
        ]

        function clipRectsEqual(first, second) {
            return Math.abs(first.x - second.x) < 0.001
                    && Math.abs(first.y - second.y) < 0.001
                    && Math.abs(first.width - second.width) < 0.001
                    && Math.abs(first.height - second.height) < 0.001
        }

        function slotSource(slot) {
            return slot === 1 ? slotASource : slot === 2 ? slotBSource : ""
        }

        function slotMirror(slot) {
            return slot === 1 ? slotAMirror : slot === 2 ? slotBMirror : false
        }

        function slotClipRect(slot) {
            return slot === 1 ? slotAClipRect
                              : slot === 2 ? slotBClipRect
                                           : Qt.rect(0, 0, 0, 0)
        }

        function slotClickMask(slot) {
            return slot === 1 ? slotAClickMask
                              : slot === 2 ? slotBClickMask : ({})
        }

        function setSlot(slot, sourceValue, mirrorValue, clipRectValue, clickMaskValue) {
            if (slot === 1) {
                slotAMirror = Boolean(mirrorValue)
                slotAClipRect = clipRectValue || Qt.rect(0, 0, 0, 0)
                slotAClickMask = clickMaskValue || ({})
                slotASource = sourceValue
            } else {
                slotBMirror = Boolean(mirrorValue)
                slotBClipRect = clipRectValue || Qt.rect(0, 0, 0, 0)
                slotBClickMask = clickMaskValue || ({})
                slotBSource = sourceValue
            }
        }

        function clearSlot(slot) {
            if (slot === 1) {
                slotASource = ""
                slotAClickMask = ({})
            } else if (slot === 2) {
                slotBSource = ""
                slotBClickMask = ({})
            }
        }

        function commitMetadata(slot) {
            displayedSource = slotSource(slot)
            displayedMirror = slotMirror(slot)
            displayedClipRect = slotClipRect(slot)
            displayedClickMask = slotClickMask(slot)
        }

        function stabilizeTransition() {
            if (!transitionActive)
                return
            var keepSlot = presentationProgress >= 0.5
                    ? transitionToSlot : transitionFromSlot
            presentationTransition.stop()
            activeSlot = keepSlot
            commitMetadata(keepSlot)
            transitionFromSlot = 0
            transitionToSlot = 0
            presentationProgress = 1.0
            outgoingSource = ""
            outgoingClickMask = ({})
        }

        function adoptPresentation(sourceValue, mirrorValue, clipRectValue, clickMaskValue) {
            var nextSource = String(sourceValue || "")
            var nextMirror = Boolean(mirrorValue)
            var nextClipRect = clipRectValue || Qt.rect(0, 0, 0, 0)
            var nextClickMask = clickMaskValue || ({})
            requestSerial += 1
            targetLoadFailed = false
            if (nextSource === "") {
                loadingSlot = 0
                holdReadyArtworkDuringLoad = false
                root.synchronizeArtworkBlend(!root.interactionSnap)
                return
            }
            stabilizeTransition()
            if (activeSlotReady && String(displayedSource) === nextSource
                    && displayedMirror === nextMirror
                    && clipRectsEqual(displayedClipRect, nextClipRect)) {
                displayedClickMask = nextClickMask
                if (activeSlot === 1)
                    slotAClickMask = nextClickMask
                else
                    slotBClickMask = nextClickMask
                root.synchronizeArtworkBlend(!root.interactionSnap)
                return
            }
            var nextSlot = activeSlot === 1 ? 2 : 1
            holdReadyArtworkDuringLoad = activeSlotReady
                    && root.renderedArtworkBlend > 0.5
            loadingSlot = nextSlot
            setSlot(nextSlot, sourceValue, nextMirror, nextClipRect, nextClickMask)
            // Image may synchronously reuse a decoded cache entry.
            Qt.callLater(function() { poseArtworkFrame.commitLoadedSlot(nextSlot) })
        }

        function scheduleAdoptPresentation() {
            if (requestPending)
                return
            holdReadyArtworkDuringLoad = activeSlotReady
                    && root.renderedArtworkBlend > 0.5
            requestPending = true
            Qt.callLater(function() {
                poseArtworkFrame.requestPending = false
                poseArtworkFrame.adoptPresentation(
                            root.poseArtworkSource,
                            root.habitatMirror,
                            root.poseArtworkClipRect,
                            root.poseArtworkClickMask)
            })
        }

        function commitLoadedSlot(slot) {
            if (loadingSlot !== slot)
                return
            var image = slot === 1 ? artworkSlotA : artworkSlotB
            if (image.status === Image.Error) {
                loadingSlot = 0
                holdReadyArtworkDuringLoad = false
                targetLoadFailed = true
                clearSlot(slot)
                root.synchronizeArtworkBlend(!root.interactionSnap)
                return
            }
            if (image.status !== Image.Ready)
                return
            loadingSlot = 0
            holdReadyArtworkDuringLoad = false
            if (!activeSlotReady) {
                activeSlot = slot
                commitMetadata(slot)
                presentationProgress = 1.0
                root.synchronizeArtworkBlend(!root.interactionSnap)
                return
            }
            outgoingSource = slotSource(activeSlot)
            outgoingMirror = slotMirror(activeSlot)
            outgoingClipRect = slotClipRect(activeSlot)
            outgoingClickMask = slotClickMask(activeSlot)
            transitionFromSlot = activeSlot
            transitionToSlot = slot
            commitMetadata(slot)
            presentationProgress = 0.0
            presentationTransition.restart()
            root.synchronizeArtworkBlend(!root.interactionSnap)
        }

        function handleSlotStatus(slot) {
            if (loadingSlot === slot) {
                commitLoadedSlot(slot)
                return
            }
            var image = slot === 1 ? artworkSlotA : artworkSlotB
            if (activeSlot === slot && image.status === Image.Error) {
                activeSlot = 0
                targetLoadFailed = true
                displayedSource = ""
                displayedClickMask = ({})
                clearSlot(slot)
                root.synchronizeArtworkBlend(!root.interactionSnap)
            }
        }

        Component.onCompleted: scheduleAdoptPresentation()
        Connections {
            target: root
            function onPoseArtworkSourceChanged() {
                poseArtworkFrame.scheduleAdoptPresentation()
            }
            function onHabitatMirrorChanged() {
                poseArtworkFrame.scheduleAdoptPresentation()
            }
            function onPoseArtworkClipRectChanged() {
                poseArtworkFrame.scheduleAdoptPresentation()
            }
            function onPoseArtworkClickMaskChanged() {
                poseArtworkFrame.scheduleAdoptPresentation()
            }
        }
        NumberAnimation {
            id: presentationTransition
            target: poseArtworkFrame
            property: "presentationProgress"
            from: 0.0
            to: 1.0
            duration: 220
            easing.type: Easing.OutCubic
            onFinished: {
                var retiredSlot = poseArtworkFrame.transitionFromSlot
                poseArtworkFrame.activeSlot = poseArtworkFrame.transitionToSlot
                poseArtworkFrame.transitionFromSlot = 0
                poseArtworkFrame.transitionToSlot = 0
                poseArtworkFrame.outgoingSource = ""
                poseArtworkFrame.outgoingClickMask = ({})
                poseArtworkFrame.clearSlot(retiredSlot)
            }
        }

        Image {
            id: artworkSlotB
            objectName: "petPoseArtworkOutgoingImage"
            anchors.fill: parent
            source: poseArtworkFrame.slotBSource
            sourceClipRect: poseArtworkFrame.slotBClipRect
            mirror: poseArtworkFrame.slotBMirror
            opacity: poseArtworkFrame.transitionActive
                     ? (poseArtworkFrame.transitionToSlot === 2
                        ? poseArtworkFrame.presentationProgress
                        : poseArtworkFrame.transitionFromSlot === 2
                          ? 1.0 - poseArtworkFrame.presentationProgress : 0.0)
                     : (poseArtworkFrame.activeSlot === 2 ? 1.0 : 0.0)
            visible: source !== "" && opacity > 0.001
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            asynchronous: true
            onStatusChanged: poseArtworkFrame.handleSlotStatus(2)
        }

        Image {
            id: artworkSlotA
            objectName: "petPoseArtworkImage"
            anchors.fill: parent
            source: poseArtworkFrame.slotASource
            sourceClipRect: poseArtworkFrame.slotAClipRect
            mirror: poseArtworkFrame.slotAMirror
            opacity: poseArtworkFrame.transitionActive
                     ? (poseArtworkFrame.transitionToSlot === 1
                        ? poseArtworkFrame.presentationProgress
                        : poseArtworkFrame.transitionFromSlot === 1
                          ? 1.0 - poseArtworkFrame.presentationProgress : 0.0)
                     : (poseArtworkFrame.activeSlot === 1 ? 1.0 : 0.0)
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            asynchronous: true
            visible: source !== "" && opacity > 0.001
            onStatusChanged: poseArtworkFrame.handleSlotStatus(1)
        }

        QtObject {
            id: poseArtworkMask

            function containsRect(nx, ny, values) {
                if (!values || values.length !== 4)
                    return false
                var left = Number(values[0])
                var top = Number(values[1])
                var width = Number(values[2])
                var height = Number(values[3])
                return isFinite(left) && isFinite(top)
                        && isFinite(width) && isFinite(height)
                        && width > 0.0 && height > 0.0
                        && nx >= left && nx <= left + width
                        && ny >= top && ny <= top + height
            }

            function containsEllipse(nx, ny, values) {
                if (!values || values.length !== 4)
                    return false
                var centerX = Number(values[0])
                var centerY = Number(values[1])
                var radiusX = Number(values[2])
                var radiusY = Number(values[3])
                if (!isFinite(centerX) || !isFinite(centerY)
                        || !isFinite(radiusX) || !isFinite(radiusY)
                        || radiusX <= 0.0 || radiusY <= 0.0)
                    return false
                var dx = (nx - centerX) / radiusX
                var dy = (ny - centerY) / radiusY
                return dx * dx + dy * dy <= 1.0
            }

            function containsPolygon(nx, ny, points) {
                if (!points || points.length < 3)
                    return false
                var inside = false
                var previous = points.length - 1
                for (var index = 0; index < points.length; ++index) {
                    var currentPoint = points[index]
                    var previousPoint = points[previous]
                    if (!currentPoint || currentPoint.length !== 2
                            || !previousPoint || previousPoint.length !== 2)
                        return false
                    var currentX = Number(currentPoint[0])
                    var currentY = Number(currentPoint[1])
                    var previousX = Number(previousPoint[0])
                    var previousY = Number(previousPoint[1])
                    if (!isFinite(currentX) || !isFinite(currentY)
                            || !isFinite(previousX) || !isFinite(previousY))
                        return false
                    var crosses = (currentY > ny) !== (previousY > ny)
                    if (crosses) {
                        var crossingX = (previousX - currentX)
                                * (ny - currentY)
                                / (previousY - currentY) + currentX
                        if (nx < crossingX)
                            inside = !inside
                    }
                    previous = index
                }
                return inside
            }

            function containsDeclaredMask(nx, ny, mask) {
                var maskType = String(mask.type || "")
                if (maskType === "rect" && containsRect(nx, ny, mask.rect))
                    return true
                if (maskType === "ellipse"
                        && containsEllipse(nx, ny, mask.ellipse))
                    return true
                var rects = mask.rects || []
                for (var rectIndex = 0; rectIndex < rects.length; ++rectIndex) {
                    if (containsRect(nx, ny, rects[rectIndex]))
                        return true
                }
                var ellipses = mask.ellipses || []
                for (var ellipseIndex = 0;
                     ellipseIndex < ellipses.length; ++ellipseIndex) {
                    if (containsEllipse(nx, ny, ellipses[ellipseIndex]))
                        return true
                }
                var polygons = mask.polygons || []
                for (var polygonIndex = 0;
                     polygonIndex < polygons.length; ++polygonIndex) {
                    if (containsPolygon(nx, ny, polygons[polygonIndex]))
                        return true
                }
                return false
            }

            function containsSlot(point, slot) {
                var nx = point.x / Math.max(1, poseArtworkFrame.width)
                var ny = point.y / Math.max(1, poseArtworkFrame.height)
                if (!isFinite(nx) || !isFinite(ny)
                        || nx < 0.0 || nx > 1.0 || ny < 0.0 || ny > 1.0)
                    return false
                // Manifest masks are authored in source-image coordinates.
                // Mirror the query—not the declared geometry—when a side-aware
                // habitat pose is displayed from the opposite screen edge.
                if (poseArtworkFrame.slotMirror(slot))
                    nx = 1.0 - nx
                var declaredMask = poseArtworkFrame.slotClickMask(slot) || ({})
                if (String(declaredMask.type || "") !== "")
                    return containsDeclaredMask(nx, ny, declaredMask)
                // Compatibility fallback for third-party v0.2 themes.  The
                // shipped theme and all future optional artwork are required
                // to provide their own manifest-authoritative mask.
                if (root.pose === "perch-prone") {
                    var hx = (nx - 0.50) / 0.48
                    var hy = (ny - 0.50) / 0.48
                    return hx * hx + hy * hy <= 1.0
                }
                if (root.pose === "edge-peek-live")
                    return nx >= 0.04 && nx <= 0.97 && ny >= 0.02 && ny <= 0.99
                var headX = (nx - 0.50) / 0.44
                var headY = (ny - 0.22) / 0.22
                var bodyHalfWidth = 0.18 + Math.max(0, ny - 0.42) * 0.32
                return headX * headX + headY * headY <= 1.0
                       || (ny >= 0.32 && ny <= 0.99
                           && Math.abs(nx - 0.50) <= bodyHalfWidth)
            }

            function contains(point: point): bool {
                if (poseArtworkFrame.transitionActive) {
                    if (poseArtworkFrame.presentationProgress < 0.999
                            && containsSlot(point,
                                poseArtworkFrame.transitionFromSlot))
                        return true
                    if (poseArtworkFrame.presentationProgress > 0.001
                            && containsSlot(point,
                                poseArtworkFrame.transitionToSlot))
                        return true
                    return false
                }
                return poseArtworkFrame.activeSlotReady
                        && containsSlot(point, poseArtworkFrame.activeSlot)
            }
        }

        MouseArea {
            id: legacyPosePointer
            objectName: "desktopPetPoseHitMask"
            anchors.fill: parent
            enabled: false
            containmentMask: poseArtworkMask
            acceptedButtons: Qt.LeftButton
            hoverEnabled: true
            preventStealing: false
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            onPressed: function(mouse) {
                var point = poseArtworkFrame.mapToItem(root, mouse.x, mouse.y)
                root.beginPointer(point.x, point.y)
            }
            onPositionChanged: function(mouse) {
                var point = poseArtworkFrame.mapToItem(root, mouse.x, mouse.y)
                root.movePointer(point.x, point.y, pressed)
            }
            onReleased: root.endPointer()
            onCanceled: root.characterCanceled(root.pointerMoved)
            onWheel: function(wheel) {
                if (wheel.angleDelta.y === 0)
                    return
                root.wheelStepped(wheel.angleDelta.y / 120.0)
                wheel.accepted = true
            }
        }
    }

    Item {
        id: figureFrame
        objectName: "petFigureFrame"
        clip: true
        visible: root.renderedArtworkBlend < 0.999
        opacity: 1.0 - root.renderedArtworkBlend
        z: 3
        readonly property real fittedHeight: Math.min(
            root.characterHeight * root.renderedCharacterScale
                                 * root.renderedFallbackPoseHeightFactor,
            root.width / root.outfitArtworkAspectRatio
        )
        width: fittedHeight * root.outfitArtworkAspectRatio
        height: fittedHeight
        readonly property real fallbackX: (root.width - width) / 2 + root.poseShiftX
        readonly property real fallbackY: (root.height - height) / 2 + root.poseShiftY
        x: root.habitatFrameX(width, fallbackX)
        y: root.habitatFrameY(height, fallbackY)
        transform: Rotation {
            objectName: "petLayeredProfileRotation"
            origin.x: figureFrame.width
                      * (root.habitatLayoutActive ? root.renderedContactX : 0.5)
            origin.y: figureFrame.height
                      * (root.habitatLayoutActive ? root.renderedContactY : 1.0)
            angle: root.renderedLayeredBodyRotation
        }

        // Each region uses the same high-resolution transparent source but a
        // different clipping window. Breathing therefore never scales the
        // complete character bitmap.
        Item {
            id: headRegion
            objectName: "petHairHeadLayer"
            clip: true
            x: root.renderedVariantHeadOffsetX * figureFrame.width
               + Math.sin(root.motionPhase + 0.15) * figureFrame.width * 0.0025
            y: root.renderedVariantHeadOffsetY * figureFrame.height
               + Math.sin(root.motionPhase + 0.15) * figureFrame.height * 0.0018
            width: parent.width
            height: parent.height * root.renderedVariantHeadClipEnd
            rotation: root.headPoseRotation + root.renderedVariantHeadRotation
                      + Math.sin(root.motionPhase + 0.55) * 0.16
            transformOrigin: Item.Bottom
            transform: Scale {
                origin.x: headRegion.width / 2
                origin.y: headRegion.height
                xScale: root.renderedVariantHeadScaleX
                yScale: root.renderedVariantHeadScaleY
            }

            Image {
                id: headImage
                objectName: "petHairHeadImage"
                x: root.outfitHorizontalOffset * figureFrame.width
                y: root.outfitVerticalOffset * figureFrame.height
                width: figureFrame.width
                height: figureFrame.height
                source: root.outfitSource
                mirror: root.habitatMirror
                fillMode: Image.Stretch
                smooth: true
                mipmap: true
                asynchronous: true
            }
        }

        Item {
            id: shoulderRegion
            objectName: "petShoulderHandsLayer"
            clip: true
            x: root.renderedVariantTorsoOffsetX * parent.width
               - (width * (torsoBreath.xScale - 1.0)) / 2
            y: parent.height * (root.renderedVariantHeadClipEnd
                                + root.renderedVariantTorsoOffsetY)
               + Math.sin(root.motionPhase + 2.15) * parent.height * 0.0016
            width: parent.width
            height: parent.height * (root.renderedVariantTorsoClipEnd
                                     - root.renderedVariantHeadClipEnd)
            rotation: root.torsoPoseRotation + root.renderedVariantTorsoRotation
                      + Math.sin(root.motionPhase + 2.15) * 0.10
            transformOrigin: Item.Bottom
            transform: [
                Scale {
                    id: torsoBreath
                    origin.x: shoulderRegion.width / 2
                    origin.y: shoulderRegion.height
                    xScale: 1.0 + Math.sin(root.motionPhase + 2.15) * 0.004
                    yScale: 1.0 + Math.sin(root.motionPhase + 2.15) * 0.0015
                },
                Scale {
                    origin.x: shoulderRegion.width / 2
                    origin.y: shoulderRegion.height / 2
                    xScale: root.renderedVariantTorsoScaleX
                    yScale: root.renderedVariantTorsoScaleY
                }
            ]

            Image {
                objectName: "petShoulderHandsImage"
                x: root.outfitHorizontalOffset * figureFrame.width
                y: -figureFrame.height * root.renderedVariantHeadClipEnd
                   + root.outfitVerticalOffset * figureFrame.height
                width: figureFrame.width
                height: figureFrame.height
                source: root.outfitSource
                mirror: root.habitatMirror
                fillMode: Image.Stretch
                smooth: true
                mipmap: true
                asynchronous: true
            }
        }

        Item {
            id: skirtRegion
            objectName: "petSkirtLayer"
            clip: true
            x: root.renderedVariantSkirtOffsetX * parent.width
               + Math.sin(root.motionPhase + 4.20) * parent.width * 0.0024
            y: parent.height * (root.renderedVariantTorsoClipEnd
                                + root.renderedVariantSkirtOffsetY)
               + Math.sin(root.motionPhase + 4.20) * parent.height * 0.0012
            width: parent.width
            height: parent.height * (1.0 - root.renderedVariantTorsoClipEnd)
            rotation: root.renderedVariantSkirtRotation
                      + Math.sin(root.motionPhase + 4.20) * 0.13
            transformOrigin: Item.Top
            transform: Scale {
                origin.x: skirtRegion.width / 2
                origin.y: 0
                xScale: root.renderedVariantSkirtScaleX
                yScale: root.renderedVariantSkirtScaleY
            }

            Image {
                objectName: "petSkirtImage"
                x: root.outfitHorizontalOffset * figureFrame.width
                y: -figureFrame.height * root.renderedVariantTorsoClipEnd
                   + root.outfitVerticalOffset * figureFrame.height
                width: figureFrame.width
                height: figureFrame.height
                source: root.outfitSource
                mirror: root.habitatMirror
                fillMode: Image.Stretch
                smooth: true
                mipmap: true
                asynchronous: true
            }
        }

        Canvas {
            id: crackGlow
            objectName: "petCrackGlowLayer"
            anchors.fill: parent
            opacity: root.paused ? 0.18 : 0.26 + Math.sin(root.motionPhase + 3.0) * 0.12
            antialiasing: true
            onPaint: {
                var context = getContext("2d")
                context.clearRect(0, 0, width, height)
                context.strokeStyle = root.crackColor
                context.lineCap = "round"
                context.lineJoin = "round"
                context.lineWidth = Math.max(0.7, width * 0.006)
                context.beginPath()
                context.moveTo(width * 0.44, height * 0.47)
                context.lineTo(width * 0.40, height * 0.51)
                context.lineTo(width * 0.45, height * 0.55)
                context.lineTo(width * 0.41, height * 0.60)
                context.moveTo(width * 0.56, height * 0.69)
                context.lineTo(width * 0.61, height * 0.73)
                context.lineTo(width * 0.56, height * 0.78)
                context.lineTo(width * 0.60, height * 0.83)
                context.stroke()
            }
        }

        // A geometric containment mask leaves the transparent corners and the
        // space beside the slim silhouette click-through.
        QtObject {
            id: silhouetteMask
            function contains(point: point): bool {
                var nx = point.x / Math.max(1, figureFrame.width)
                var ny = point.y / Math.max(1, figureFrame.height)
                var hx = (nx - (0.50 + root.renderedVariantHeadOffsetX))
                         / (0.42 * root.renderedVariantHeadScaleX)
                var hy = (ny - (root.renderedVariantHeadClipEnd * 0.56
                                + root.renderedVariantHeadOffsetY))
                         / (0.19 * root.renderedVariantHeadScaleY)
                var inHead = hx * hx + hy * hy <= 1.0
                var torsoMiddle = (root.renderedVariantHeadClipEnd
                                   + root.renderedVariantTorsoClipEnd) / 2.0
                var tx = (nx - (0.50 + root.renderedVariantTorsoOffsetX))
                         / (0.235 * root.renderedVariantTorsoScaleX)
                var ty = (ny - (torsoMiddle + root.renderedVariantTorsoOffsetY))
                         / (0.24 * root.renderedVariantTorsoScaleY)
                var inTorso = tx * tx + ty * ty <= 1.0
                var skirtTop = root.renderedVariantTorsoClipEnd
                               + root.renderedVariantSkirtOffsetY
                var skirtProgress = Math.max(0.0, ny - skirtTop)
                var skirtHalfWidth = (0.19 + skirtProgress * 0.42)
                                     * root.renderedVariantSkirtScaleX
                var inSkirt = ny >= skirtTop
                              && ny <= Math.min(0.995,
                                  skirtTop + (1.0 - root.renderedVariantTorsoClipEnd)
                                  * root.renderedVariantSkirtScaleY)
                              && Math.abs(nx - (0.50
                                  + root.renderedVariantSkirtOffsetX)) <= skirtHalfWidth
                return inHead || inTorso || inSkirt
            }
        }

        MouseArea {
            id: legacyCharacterPointer
            objectName: "desktopPetLayeredLegacyHitMask"
            anchors.fill: parent
            enabled: false
            containmentMask: silhouetteMask
            acceptedButtons: Qt.LeftButton
            hoverEnabled: true
            preventStealing: false
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor

            onPressed: function(mouse) {
                var point = figureFrame.mapToItem(root, mouse.x, mouse.y)
                root.beginPointer(point.x, point.y)
            }
            onPositionChanged: function(mouse) {
                var point = figureFrame.mapToItem(root, mouse.x, mouse.y)
                root.movePointer(point.x, point.y, pressed)
            }
            onReleased: root.endPointer()
            onCanceled: root.characterCanceled(root.pointerMoved)
            onWheel: function(wheel) {
                if (wheel.angleDelta.y === 0)
                    return
                root.wheelStepped(wheel.angleDelta.y / 120.0)
                wheel.accepted = true
            }
        }
    }

    // One root-coordinate target is shared by layered silhouettes, both
    // artwork buffers and Main.qml's native hit-region query.  Keeping input
    // above the visual frames also avoids two MouseAreas racing for a point
    // while representation opacity crosses 50 percent.
    QtObject {
        id: unifiedCharacterMask
        function contains(point: point): bool {
            return root.containsCharacterPoint(point.x, point.y)
        }
    }

    MouseArea {
        id: characterPointer
        objectName: "desktopPetCharacterHitMask"
        anchors.fill: parent
        z: 20
        enabled: root.visible && root.inputEnabled
        containmentMask: unifiedCharacterMask
        acceptedButtons: Qt.LeftButton
        hoverEnabled: true
        preventStealing: false
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        onPressed: function(mouse) {
            root.beginPointer(mouse.x, mouse.y)
        }
        onPositionChanged: function(mouse) {
            root.movePointer(mouse.x, mouse.y, pressed)
        }
        onReleased: root.endPointer()
        onCanceled: root.characterCanceled(root.pointerMoved)
        onWheel: function(wheel) {
            if (wheel.angleDelta.y === 0)
                return
            root.wheelStepped(wheel.angleDelta.y / 120.0)
            wheel.accepted = true
        }
    }
}
