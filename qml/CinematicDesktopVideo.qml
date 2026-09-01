import QtQuick
import QtMultimedia

Item {
    id: root
    objectName: "desktopVideoSurface"

    property var appBackend: null
    readonly property string playerState: cinematicPlayer.playbackState === MediaPlayer.PlayingState
                                          ? "playing"
                                          : (cinematicPlayer.playbackState === MediaPlayer.PausedState
                                             ? "paused" : "stopped")

    function synchronizePlayback() {
        if (appBackend !== null
                && appBackend.shellMode !== "compact"
                && appBackend.renderer === "video"
                && appBackend.sceneActive)
            cinematicPlayer.play()
        else
            cinematicPlayer.pause()
    }

    MediaPlayer {
        id: cinematicPlayer
        objectName: "desktopCinematicPlayer"
        source: root.appBackend !== null ? root.appBackend.assetUrl("video") : ""
        videoOutput: cinematicOutput
        loops: MediaPlayer.Infinite
    }

    VideoOutput {
        id: cinematicOutput
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectCrop
    }

    Connections {
        target: root.appBackend
        function onSceneActiveChanged() {
            root.synchronizePlayback()
        }
    }

    onAppBackendChanged: Qt.callLater(root.synchronizePlayback)
}
