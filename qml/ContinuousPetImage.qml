import QtQuick

Item {
    id: root
    property url source
    property bool mirrored: false
    property real phase: 0
    property real motionGain: 1
    property point direction: Qt.point(0, 0)
    readonly property bool gpuAvailable: GraphicsInfo.api !== GraphicsInfo.Software
                                               && GraphicsInfo.api !== GraphicsInfo.Unknown
    readonly property bool deformationReady: gpuAvailable && effect.status !== ShaderEffect.Error
    readonly property int imageStatus: textureImage.status
    readonly property string shaderLog: effect.log

    // One intact texture and one shared mesh. There are no independently
    // clipped head/face/body strips, so adjacent pixels cannot pull apart.
    Image {
        id: textureImage
        objectName: "petContinuousTexture"
        anchors.fill: parent
        source: root.source
        mirror: root.mirrored
        fillMode: Image.Stretch
        asynchronous: true
        smooth: true
        mipmap: true
        visible: !root.deformationReady
    }
    ShaderEffect {
        id: effect
        objectName: "petContinuousMesh"
        anchors.fill: parent
        visible: root.deformationReady && textureImage.status === Image.Ready
        property var source: textureImage
        property real phase: root.phase
        property real motionGain: Math.max(0, Math.min(1, root.motionGain))
        property size dimensions: Qt.size(width, height)
        property point direction: Qt.point(Math.max(-1, Math.min(1, root.direction.x)),
                                          Math.max(-1, Math.min(1, root.direction.y)))
        property real mirrorAmount: root.mirrored ? 1 : 0
        mesh: GridMesh { resolution: Qt.size(24, 40) }
        vertexShader: "shaders/pet-continuous.vert.qsb"
        fragmentShader: "shaders/pet-continuous.frag.qsb"
    }
}
