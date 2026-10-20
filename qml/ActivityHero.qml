import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: hero
    objectName: "activityHero"
    required property var appBackend
    property int defaultMinutes: 25
    signal addTaskRequested()
    signal actionFailed(string reason)
    property var focusInfo: appBackend.focusStatus
    property var readingInfo: appBackend.readingStatus
    property var paperInfo: appBackend.paperState
    property var nextTask: appBackend.taskItems.find(function(task) { return !task.completed && !task.archived }) || ({})
    readonly property string mode: readingInfo.active && (!focusInfo.active || String(readingInfo.started_at) > String(focusInfo.started_at)) ? "reading"
                                  : focusInfo.active ? "focus" : nextTask.id ? "task" : "idle"
    readonly property string heading: mode === "focus" ? (focusInfo.taskTitle || "手边的事")
        : mode === "reading" ? (readingInfo.title || "论文阅读")
        : mode === "task" ? nextTask.title : "现在，想做哪件事？"
    readonly property int remaining: Number(focusInfo.remainingSeconds || 0)
    readonly property string clockText: Math.floor(remaining / 60) + ":" + String(remaining % 60).padStart(2, "0")
    implicitHeight: content.implicitHeight + 44
    radius: 18; color: "#ffffff"; border.color: "#e6e8ee"
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.margins: 22; y: 22
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text { text: hero.mode === "focus" ? (hero.focusInfo.paused ? "暂时歇一会儿" : "正在专注") : hero.mode === "reading" ? "正在阅读" : hero.mode === "task" ? "手头这件事" : "今天"; color: "#985c69"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            Text { visible: hero.mode === "focus"; text: hero.clockText; font.pixelSize: 21; color: "#687180" }
        }
        Text {
            objectName: "activityHeroTitle"
            Layout.fillWidth: true; text: hero.heading; textFormat: Text.PlainText
            color: "#353b45"; font.pixelSize: 24; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        Text {
            objectName: "activityHeroReadingNotes"
            Layout.fillWidth: true
            visible: hero.mode === "reading" && Boolean(hero.readingInfo.paperId) && Number((hero.paperInfo.review || {}).totalCount || 0) > 0
            text: String(Number((hero.paperInfo.review || {}).questionCount || 0)) + " 个问题还留着"
                + ((hero.paperInfo.review || {}).latest ? " · 最近：" + String(hero.paperInfo.review.latest.title || "") : "")
            textFormat: Text.PlainText; color: "#687180"; font.pixelSize: 13; wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: hero.mode === "reading" && Boolean(hero.readingInfo.paperId)
            text: "第 " + ((hero.paperInfo.current || {}).last_page || 1) + " 页 · "
                  + ((hero.paperInfo.current || {}).locator || "从这里接着读")
            textFormat: Text.PlainText; color: "#7c8491"; font.pixelSize: 13; elide: Text.ElideRight
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            QuietAction {
                objectName: "activityHeroPrimary"
                primary: true
                text: hero.mode === "focus" ? (hero.focusInfo.paused ? "继续" : "暂停")
                    : hero.mode === "reading" ? "展开笔记" : hero.mode === "task" ? "开始这件事" : "记下一件事"
                onClicked: {
                    if (hero.mode === "focus") hero.focusInfo.paused ? appBackend.focusResume() : appBackend.focusPause()
                    else if (hero.mode === "reading") appBackend.openReadingDesk()
                    else if (hero.mode === "task") {
                        var result = appBackend.focusStartTask(hero.nextTask.id, hero.defaultMinutes)
                        if (result && result.ok === false) hero.actionFailed(result.error)
                    }
                    else hero.addTaskRequested()
                }
            }
            QuietAction {
                objectName: "activityHeroSecondary"
                text: hero.mode === "focus" ? "结束本段" : hero.mode === "reading" ? "收起这篇" : "读一篇论文"
                onClicked: {
                    if (hero.mode === "focus") appBackend.focusFinish()
                    else if (hero.mode === "reading") { appBackend.readingFinish(); appBackend.openReadingDesk() }
                    else appBackend.openReadingDesk()
                }
            }
            Item { Layout.fillWidth: true }
        }
    }
}
