pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: root

    property var appBackend: null
    property string provider: "calendar"
    property string notice: ""
    property color paper: "#fffaf2"
    property color paperRaised: "#fffdf9"
    property color ink: "#494540"
    property color mutedInk: "#847d75"
    property color hairline: "#d9cdbf"
    property color cord: "#92362f"
    property color calm: "#66877a"

    readonly property var calendarInfo: value("calendarStatus", ({
        connected: false,
        state: "未配置",
        lastSyncAt: "",
        error: ""
    }))
    readonly property var slackInfo: value("slackStatus", ({
        connected: false,
        state: "未配置",
        lastSyncAt: "",
        error: ""
    }))
    readonly property var currentInfo: provider === "slack" ? slackInfo : calendarInfo
    readonly property var calendarItems: value("calendarUpcoming", [])
    readonly property var slackItems: value("slackInbox", [])
    readonly property var connectorSelection: value("connectorSelectedItem", ({}))
    readonly property var connectorAssistResult: value("connectorAssistResult", ({}))
    readonly property var proposal: value("connectorProposal", ({}))
    readonly property bool hasProposal: proposal !== null
                                        && proposal !== undefined
                                        && String(proposal.id || proposal.proposalId || "") !== ""
    readonly property string proposalProvider: String(proposal.connector || "") === "google-calendar"
                                               ? "calendar" : String(proposal.connector || "")
    readonly property bool providerHasProposal: hasProposal && proposalProvider === provider
    readonly property bool proposalExecuting: Boolean(proposal.executing)
    readonly property bool calendarCreateProposal: proposalProvider === "calendar"
                                                   && String(proposal.action || "").toLowerCase().indexOf("create") >= 0
    readonly property string assistanceValue: String((currentInfo.policyCanonical || ({})).assistance || "")
    readonly property string retentionValue: String((currentInfo.policyCanonical || ({})).retention || "")
    readonly property bool mayAssist: retentionValue !== "metadata"
                                      && (assistanceValue === "assist"
                                          || assistanceValue === "confirm-execute")
    readonly property bool mayConfirmExecute: assistanceValue === "confirm-execute"
    property string selectedCalendarId: ""
    property string selectedSlackId: ""
    readonly property var selectedCalendarItem: calendarItem(selectedCalendarId)
    readonly property var selectedSlackItem: slackItem(selectedSlackId)
    property string pendingAssistProvider: ""
    property string pendingAssistEventId: ""
    property string calendarAssistSuggestion: ""

    width: Math.min(900, Screen.width - 48)
    height: Math.min(760, Screen.height - 64)
    minimumWidth: 720
    minimumHeight: 560
    visible: false
    color: paper
    title: "莉莉丝 · 日历与 Slack 信笺"
    flags: Qt.Window | Qt.WindowTitleHint | Qt.WindowSystemMenuHint
           | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint

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

    function callBackend(name, args, failureText) {
        var fallback = failureText || "操作未完成，请检查配置后重试"
        if (!appBackend) {
            notice = failureText || "连接器服务尚未启动"
            return ({ ok: false, result: undefined })
        }
        try {
            var callback = appBackend[name]
            if (typeof callback !== "function") {
                notice = failureText || "当前版本尚未接入此操作"
                return ({ ok: false, result: undefined })
            }
            var result = callback.apply(appBackend, args || [])
            // A Qt slot can reject an operation without throwing.  Treat both
            // explicit ``false`` and the backend's ``{ok:false,error:...}``
            // contract as failures; otherwise the connection wizard claims a
            // browser/configuration action succeeded when nothing happened.
            if (result === false
                    || (result !== null && typeof result === "object"
                        && result.ok !== undefined && result.ok === false)) {
                var detail = result && (result.error || result.message)
                             ? String(result.error || result.message) : fallback
                notice = detail.slice(0, 320)
                return ({ ok: false, result: result })
            }
            notice = ""
            return ({ ok: true, result: result })
        } catch (error) {
            notice = fallback
            return ({ ok: false, result: undefined })
        }
    }

    function normalizedProvider(value) {
        return String(value || "") === "google-calendar" ? "calendar" : String(value || "")
    }

    function itemId(item) {
        if (!item)
            return ""
        return String(item.id || item.eventId || item.event_id || "")
    }

    function findItem(items, eventId) {
        var wanted = String(eventId || "")
        for (var index = 0; index < items.length; ++index) {
            if (itemId(items[index]) === wanted)
                return items[index]
        }
        return ({})
    }

    function calendarItem(eventId) {
        return findItem(calendarItems, eventId)
    }

    function slackItem(eventId) {
        return findItem(slackItems, eventId)
    }

    function calendarTime(item, key) {
        if (!item)
            return ""
        var raw = item[key]
        if (raw === undefined || raw === null || raw === "")
            raw = item[key + "At"]
        if ((raw === undefined || raw === null || raw === "") && key === "start")
            raw = item.occurredAt
        if (raw !== null && typeof raw === "object")
            raw = raw.dateTime || raw.date || ""
        return String(raw || "")
    }

    function calendarTitle(item) {
        return String((item || ({})).title || (item || ({})).summary || "")
    }

    function synchronizeBackendSelection() {
        var selectedProvider = normalizedProvider(connectorSelection.provider
                                                  || connectorSelection.connector)
        var selectedId = String(connectorSelection.id || connectorSelection.eventId || "")
        if (selectedProvider === "calendar" && selectedId !== "") {
            provider = "calendar"
            if (selectedCalendarId !== selectedId) {
                selectedCalendarId = selectedId
                calendarAssistSuggestion = ""
                calendarAssistInstruction.text = ""
                populateCalendarUpdate()
            }
        } else if (selectedProvider === "slack" && selectedId !== "") {
            provider = "slack"
            if (selectedSlackId !== selectedId) {
                selectedSlackId = selectedId
                slackReplyText.text = ""
                slackAssistInstruction.text = ""
            }
        } else if (selectedProvider === "" && selectedId === "") {
            selectedCalendarId = ""
            selectedSlackId = ""
            calendarAssistSuggestion = ""
        }
        if (visible) {
            Qt.callLater(function() {
                root.raise()
                root.requestActivate()
            })
        }
    }

    function populateCalendarUpdate() {
        var item = calendarItem(selectedCalendarId)
        calendarUpdateTitle.text = calendarTitle(item)
        calendarUpdateStart.text = calendarTime(item, "start")
        calendarUpdateEnd.text = calendarTime(item, "end")
    }

    function proposalAccepted(response, successText) {
        if (!response.ok)
            return false
        var result = response.result
        if (result && result.ok === false) {
            notice = String(result.error || result.message || "提案没有生成")
            return false
        }
        notice = successText
        return true
    }

    function proposeCalendarCreate() {
        if (!mayConfirmExecute) {
            notice = "只有协助能力为 confirm-execute 时才能生成写入提案；未确认不会写入。"
            return
        }
        var payload = {
            title: calendarCreateTitle.text.trim(),
            start: calendarCreateStart.text.trim(),
            end: calendarCreateEnd.text.trim(),
            timeZone: calendarCreateTimeZone.text.trim(),
            reminderMinutes: Number(calendarCreateReminder.value)
        }
        var response = callBackend("calendarProposeCreate", [payload], "新建日程提案没有生成")
        proposalAccepted(response, "已生成新建日程预览；未确认前不会写入 Calendar。")
    }

    function calendarUpdatePayload() {
        var item = selectedCalendarItem || ({})
        var payload = ({})
        var title = calendarUpdateTitle.text.trim()
        var start = calendarUpdateStart.text.trim()
        var end = calendarUpdateEnd.text.trim()
        var startChanged = start !== calendarTime(item, "start")
        var endChanged = end !== calendarTime(item, "end")
        if (title !== "" && title !== calendarTitle(item))
            payload.title = title
        if ((startChanged || endChanged) && start !== "" && end !== "") {
            payload.start = start
            payload.end = end
        }
        return payload
    }

    function proposeCalendarUpdate() {
        if (!mayConfirmExecute) {
            notice = "只有协助能力为 confirm-execute 时才能生成写入提案；未确认不会写入。"
            return
        }
        var originalStart = calendarTime(selectedCalendarItem, "start")
        var originalEnd = calendarTime(selectedCalendarItem, "end")
        var start = calendarUpdateStart.text.trim()
        var end = calendarUpdateEnd.text.trim()
        if ((start !== originalStart || end !== originalEnd) && (start === "" || end === "")) {
            notice = "修改时间时请同时填写开始与结束 ISO 时间。"
            return
        }
        var payload = calendarUpdatePayload()
        if (Object.keys(payload).length === 0) {
            notice = "请先修改标题、开始时间或结束时间。"
            return
        }
        var response = callBackend("calendarProposeUpdate", [selectedCalendarId, payload],
                                   "日程更新提案没有生成")
        proposalAccepted(response, "已生成日程更新预览；未确认前不会写入 Calendar。")
    }

    function proposeSlackReply() {
        if (!mayConfirmExecute) {
            notice = "只有协助能力为 confirm-execute 时才能生成发送提案；未确认不会发送。"
            return
        }
        var response = callBackend("slackProposeReply",
                                   [selectedSlackId, slackReplyText.text.trim()],
                                   "Slack 回复提案没有生成")
        proposalAccepted(response, "已生成 Slack 回复预览；未确认前不会发送。")
    }

    function requestAssist(providerName, eventId, instruction) {
        if (!mayAssist) {
            notice = "当前协助能力不允许处理所选条目。"
            return
        }
        pendingAssistProvider = normalizedProvider(providerName)
        pendingAssistEventId = String(eventId || "")
        var response = callBackend("connectorAssist",
                                   [pendingAssistProvider, pendingAssistEventId,
                                    String(instruction || "").trim()],
                                   "协助请求没有发出")
        if (response.ok && response.result !== false)
            notice = "正在为当前条目准备建议；这不会写入外部服务。"
        else if (response.result === false)
            notice = String(connectorAssistResult.error || "协助请求没有发出")
    }

    function applyAssistResult() {
        var result = connectorAssistResult
        if (result === undefined || result === null)
            return
        var resultProvider = normalizedProvider(result.provider || pendingAssistProvider)
        var resultEventId = String(result.eventId || pendingAssistEventId)
        if (resultProvider === "" || resultEventId === "")
            return
        if (pendingAssistEventId !== "" && resultEventId !== ""
                && resultEventId !== pendingAssistEventId)
            return
        if (Boolean(result.busy)) {
            notice = "正在为当前条目准备建议；这不会写入外部服务。"
            return
        }
        if (String(result.error || "") !== "") {
            notice = "协助没有完成：" + String(result.error)
            return
        }
        var suggestion = String(result.text || "")
        if (resultProvider === "calendar" && selectedCalendarId === resultEventId) {
            calendarAssistSuggestion = suggestion
            notice = "协助建议已返回；请自行核对并填写结构化时间，未确认不会写入。"
        } else if (resultProvider === "slack" && selectedSlackId === resultEventId) {
            if (suggestion !== "")
                slackReplyText.text = suggestion
            notice = "协助建议已填入回复草稿；仍需生成预览并手动确认。"
        }
    }

    onConnectorSelectionChanged: synchronizeBackendSelection()
    onConnectorAssistResultChanged: applyAssistResult()
    onCalendarItemsChanged: {
        if (selectedCalendarId !== "" && calendarUpdateTitle.text === ""
                && calendarUpdateStart.text === "" && calendarUpdateEnd.text === "")
            populateCalendarUpdate()
    }
    onVisibleChanged: {
        if (visible)
            synchronizeBackendSelection()
    }
    Component.onCompleted: synchronizeBackendSelection()

    function selectedChannels() {
        return slackChannels.text.split(/[，,\n]/).map(function(item) {
            return item.trim()
        }).filter(function(item) {
            return item.length > 0
        })
    }

    function policyMap() {
        return {
            scope: scopeBox.currentText,
            interruption: interruptionBox.currentText,
            retention: retentionBox.currentText,
            assistance: assistanceBox.currentText
        }
    }

    function configurationMap() {
        var result = policyMap()
        result.policy = policyMap()
        result.clientId = provider === "calendar" ? googleClientId.text.trim()
                                                   : slackClientId.text.trim()
        if (provider === "slack") {
            result.xappToken = slackXappToken.text
            result.appToken = slackXappToken.text
            result.currentUserId = slackCurrentUserId.text.trim()
            result.selectedChannels = selectedChannels()
            result.redirectUri = slackRedirectUri.text.trim()
        }
        return result
    }

    function saveConfiguration(showConfirmation) {
        var response = callBackend("connectorConfigure", [provider, configurationMap()],
                                   "配置没有保存，请检查必填项")
        if (response.ok && showConfirmation)
            notice = "配置已交给本地连接器保存"
        return response.ok
    }

    function beginOAuth() {
        if (!saveConfiguration(false))
            return
        var response = callBackend("connectorBeginOAuth", [provider],
                                   "无法启动系统浏览器授权")
        if (response.ok)
            notice = "已在系统浏览器打开授权页"
    }

    function generateSlackManifest() {
        if (!saveConfiguration(false))
            return
        var generated = ""
        if (appBackend) {
            try {
                var member = appBackend.slackManifestText
                if (typeof member === "function")
                    generated = String(member())
                else if (member !== undefined && member !== null)
                    generated = String(member)
            } catch (error) {
                generated = ""
            }
        }
        if (generated === "") {
            var response = callBackend("slackGenerateManifest", [configurationMap()],
                                       "暂时无法生成 Slack manifest")
            if (response.ok && response.result !== undefined && response.result !== null)
                generated = String(response.result)
            if (generated === "")
                generated = String(value("slackManifestText", ""))
        }
        slackManifest.text = generated
        notice = generated === "" ? "manifest 尚未生成" : "manifest 已生成，可全选复制"
    }

    function resolveProposal(confirmed) {
        if (!providerHasProposal || proposalExecuting)
            return
        var method = confirmed ? "connectorConfirmProposal" : "connectorRejectProposal"
        var proposalId = String(proposal.id || proposal.proposalId || "")
        var response = callBackend(method, [provider, proposalId],
                                   confirmed ? "提案尚未执行" : "提案尚未撤销")
        if (response.ok && response.result === true)
            notice = confirmed ? "已确认；连接器将严格按预览内容执行"
                               : "已拒绝这项提案"
        else
            notice = confirmed ? "提案未重复提交；请等待当前操作完成"
                               : "提案正在处理，暂时不能撤销"
    }

    function replaceSlackProposal() {
        if (!providerHasProposal || provider !== "slack")
            return
        if (!mayConfirmExecute) {
            notice = "当前策略不是 confirm-execute，不能生成替换提案；未确认不会发送。"
            return
        }
        var proposalId = String(proposal.id || proposal.proposalId || "")
        var response = callBackend(
            "connectorReplaceSlackProposal",
            [proposalId, slackFinalText.text],
            "最终正文没有更新"
        )
        if (response.ok)
            notice = "已作废旧提案并生成新的最终预览"
    }

    function readableState(info) {
        if (Boolean(info.connected))
            return "已连接"
        var state = String(info.authorizationState || info.state || "未配置")
        if (state === "connected")
            return "已连接"
        if (state === "authorizing")
            return "等待授权"
        if (state === "error")
            return "连接异常"
        if (state === "not-configured")
            return "未配置"
        return state
    }

    function proposalText() {
        if (!hasProposal)
            return ""
        var beforeValue = proposal.before || proposal.beforeValue || ({})
        var afterValue = proposal.after || proposal.afterValue || proposal.payload || ({})
        var parts = []
        parts.push(String(proposal.summary || proposal.action || "待确认操作"))
        if (String(proposal.target || "") !== "")
            parts.push("目标：" + String(proposal.target))
        if (Object.keys(beforeValue).length > 0)
            parts.push("修改前：\n" + JSON.stringify(beforeValue, null, 2))
        if (Object.keys(afterValue).length > 0)
            parts.push("修改后：\n" + JSON.stringify(afterValue, null, 2))
        if (String(proposal.expiresAt || "") !== "")
            parts.push("有效至：" + String(proposal.expiresAt))
        return parts.join("\n\n")
    }

    component PaperCard: Rectangle {
        id: card
        default property alias contentData: contentColumn.data
        property string heading: ""
        property string caption: ""
        Layout.fillWidth: true
        implicitHeight: contentColumn.implicitHeight + 28
        radius: 15
        color: root.paperRaised
        border.color: root.hairline
        border.width: 1

        ColumnLayout {
            id: contentColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 10

            Label {
                visible: card.heading !== ""
                Layout.fillWidth: true
                text: card.heading
                color: root.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }

            Label {
                visible: card.caption !== ""
                Layout.fillWidth: true
                text: card.caption
                color: root.mutedInk
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
        }
    }

    component FieldLabel: Label {
        Layout.fillWidth: true
        color: root.ink
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }

    component QuietButton: Button {
        id: quietButton
        property bool accent: false
        implicitHeight: 37
        leftPadding: 15
        rightPadding: 15
        font.pixelSize: 12
        contentItem: Label {
            text: quietButton.text
            color: quietButton.accent ? "#fffdf9" : root.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: quietButton.accent ? root.cord
                                      : (quietButton.hovered ? "#f2e9dd" : root.paperRaised)
            border.color: quietButton.accent ? root.cord : root.hairline
            opacity: quietButton.enabled ? 1 : 0.45
        }
    }

    component PolicyAxis: ColumnLayout {
        id: axis
        required property string label
        required property string explanation
        property alias model: choice.model
        property alias currentIndex: choice.currentIndex
        property alias currentText: choice.currentText
        spacing: 5

        Label {
            Layout.fillWidth: true
            text: axis.label
            color: root.ink
            font.pixelSize: 12
            font.weight: Font.Medium
        }

        ComboBox {
            id: choice
            Layout.fillWidth: true
            implicitHeight: 38
            font.pixelSize: 12
            Accessible.name: axis.label
        }

        Label {
            Layout.fillWidth: true
            text: axis.explanation
            color: root.mutedInk
            font.pixelSize: 10
            wrapMode: Text.WordWrap
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
                spacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3

                    Label {
                        text: "日历与 Slack 信笺"
                        color: root.ink
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                    }

                    Label {
                        text: "外部连接默认关闭；授权、留存和写入能力彼此独立。"
                        color: root.mutedInk
                        font.pixelSize: 11
                    }
                }

                TabBar {
                    id: providerTabs
                    currentIndex: root.provider === "slack" ? 1 : 0
                    onCurrentIndexChanged: root.provider = currentIndex === 1 ? "slack" : "calendar"

                    TabButton {
                        text: "Google Calendar"
                        width: implicitWidth + 24
                    }
                    TabButton {
                        text: "Slack"
                        width: implicitWidth + 24
                    }
                }
            }

            ScrollView {
                id: connectorSetupScroll
                objectName: "connectorSetupScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: Math.max(640, parent.width - 12)
                    spacing: 12

                    PaperCard {
                        heading: root.provider === "calendar" ? "Google Calendar 状态" : "Slack 状态"

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10

                            Rectangle {
                                implicitWidth: stateText.implicitWidth + 22
                                implicitHeight: 27
                                radius: 13
                                color: Boolean(root.currentInfo.connected) ? "#e5efe9" : "#f2e8dd"
                                border.color: Boolean(root.currentInfo.connected) ? root.calm : root.hairline

                                Label {
                                    id: stateText
                                    anchors.centerIn: parent
                                    text: root.readableState(root.currentInfo)
                                    color: Boolean(root.currentInfo.connected) ? root.calm : root.mutedInk
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                }
                            }

                            Label {
                                Layout.fillWidth: true
                                text: String(root.currentInfo.accountName
                                             || root.currentInfo.workspace
                                             || root.currentInfo.account
                                             || "尚未绑定账号")
                                color: root.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }

                            Label {
                                text: String(root.currentInfo.lastSyncAt || "") === ""
                                      ? "尚未同步"
                                      : "最后同步：" + String(root.currentInfo.lastSyncAt)
                                color: root.mutedInk
                                font.pixelSize: 10
                            }
                        }

                        Label {
                            visible: String(root.currentInfo.error || root.currentInfo.errorMessage || "") !== ""
                            Layout.fillWidth: true
                            text: "错误：" + String(root.currentInfo.error || root.currentInfo.errorMessage || "")
                            color: root.cord
                            font.pixelSize: 11
                            wrapMode: Text.WordWrap
                        }
                    }

                    PaperCard {
                        visible: root.provider === "calendar"
                        heading: "Google Desktop OAuth"
                        caption: "使用你自己的 Desktop OAuth Client。授权在系统浏览器中完成，莉莉丝不会嵌入登录页。"

                        FieldLabel { text: "Desktop OAuth Client ID" }
                        TextField {
                            id: googleClientId
                            Layout.fillWidth: true
                            placeholderText: "例如：……apps.googleusercontent.com"
                            selectByMouse: true
                            Accessible.name: "Google Desktop OAuth Client ID"
                        }

                        Label {
                            Layout.fillWidth: true
                            text: "初次连接只申请日历列表和事件只读权限；只有把“协助”升级为“确认执行”后，才会重新申请可写权限。"
                            color: root.mutedInk
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }
                    }

                    PaperCard {
                        visible: root.provider === "slack"
                        heading: "Slack 本地个人接入"
                        caption: "使用你创建的 custom app 与 Socket Mode。令牌只交给 Windows 安全存储，不写入项目数据目录。"

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 12
                            rowSpacing: 7

                            FieldLabel { text: "OAuth Client ID" }
                            TextField {
                                id: slackClientId
                                Layout.fillWidth: true
                                placeholderText: "Slack App Client ID"
                                selectByMouse: true
                                Accessible.name: "Slack OAuth Client ID"
                            }

                            FieldLabel { text: "Redirect URI" }
                            TextField {
                                id: slackRedirectUri
                                Layout.fillWidth: true
                                text: "http://127.0.0.1:53682/oauth/callback"
                                placeholderText: "已在 Slack App 注册的回调 URI"
                                selectByMouse: true
                                Accessible.name: "Slack OAuth Redirect URI"
                            }

                            FieldLabel { text: "Socket Mode xapp Token" }
                            TextField {
                                id: slackXappToken
                                Layout.fillWidth: true
                                placeholderText: "xapp-…"
                                echoMode: TextInput.Password
                                passwordCharacter: "●"
                                selectByMouse: true
                                Accessible.name: "Slack Socket Mode Token"
                            }

                            FieldLabel { text: "当前用户 ID" }
                            TextField {
                                id: slackCurrentUserId
                                Layout.fillWidth: true
                                placeholderText: "例如 U0123456789"
                                selectByMouse: true
                                Accessible.name: "Slack 当前用户 ID"
                            }

                            FieldLabel { text: "精选频道" }
                            TextField {
                                id: slackChannels
                                Layout.fillWidth: true
                                placeholderText: "频道 ID，用逗号分隔"
                                selectByMouse: true
                                Accessible.name: "Slack 精选频道"
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            text: "这个地址必须与 Slack App → OAuth & Permissions 中登记的 Redirect URL 完全一致。使用本机回调时请保留固定端口；也可以填写你已登记的自定义 URI。"
                            color: root.mutedInk
                            font.pixelSize: 10
                            wrapMode: Text.WordWrap
                        }

                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: slackScopeNotice.implicitHeight + 20
                            radius: 10
                            color: "#f7eee3"
                            border.color: root.hairline

                            Label {
                                id: slackScopeNotice
                                anchors.fill: parent
                                anchors.margins: 10
                                text: "Slack 的授权以会话类型为单位。应用会先收到所授权 scope 范围内的事件，再在本机立即丢弃未选择来源；“精选频道”不是 Slack 服务端过滤。"
                                color: root.ink
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true

                            QuietButton {
                                text: "生成 App manifest"
                                onClicked: root.generateSlackManifest()
                            }
                            Label {
                                Layout.fillWidth: true
                                text: "生成后可在 Slack App 管理页粘贴导入。"
                                color: root.mutedInk
                                font.pixelSize: 10
                            }
                        }

                        TextArea {
                            id: slackManifest
                            visible: text !== ""
                            Layout.fillWidth: true
                            implicitHeight: Math.min(190, Math.max(90, contentHeight + 20))
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.NoWrap
                            font.family: "Consolas"
                            font.pixelSize: 10
                            placeholderText: "生成的 manifest 会显示在这里"
                            Accessible.name: "Slack App manifest"
                        }
                    }

                    PaperCard {
                        heading: "四项独立权限"
                        caption: "改变其中一项不会暗中升级其他项；共鸣成长也不会改动这里。"

                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 700 ? 2 : 1
                            columnSpacing: 14
                            rowSpacing: 12

                            PolicyAxis {
                                id: scopeBox
                                Layout.fillWidth: true
                                label: "信息范围"
                                explanation: "必要：主日历／私信与本人提及；精选：只保留你勾选的来源；广泛：授权范围内全部来源。"
                                model: ["必要", "精选", "广泛"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: interruptionBox
                                Layout.fillWidth: true
                                label: "打扰方式"
                                explanation: "安静会排队；优先使用系统通知；即时会在非敏感场景显示气泡。"
                                model: ["安静", "优先", "即时"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: retentionBox
                                Layout.fillWidth: true
                                label: "本地留存"
                                explanation: "元数据不存正文；摘要与扩展缓存都会加密，扩展缓存仅保留已选来源。"
                                model: ["元数据", "可搜索摘要", "扩展缓存"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: assistanceBox
                                Layout.fillWidth: true
                                label: "协助能力"
                                explanation: "提醒不调用模型；协助仅在点击后处理当前项；确认执行仍需逐次预览并手动确认。"
                                model: ["提醒", "协助", "确认执行"]
                                currentIndex: 1
                            }
                        }
                    }

                    PaperCard {
                        visible: root.provider === "calendar"
                        heading: "准备新建日程"
                        caption: "填写内容只会生成一份创建预览；在最终预览中手动确认以前，Calendar 不会写入任何日程。"

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 12
                            rowSpacing: 8

                            FieldLabel {
                                text: "标题"
                                Layout.preferredWidth: 150
                            }
                            TextField {
                                id: calendarCreateTitle
                                Layout.fillWidth: true
                                placeholderText: "例如：项目复盘"
                                selectByMouse: true
                                Accessible.name: "新建日程标题"
                            }

                            FieldLabel { text: "开始时间（ISO 8601）" }
                            TextField {
                                id: calendarCreateStart
                                Layout.fillWidth: true
                                placeholderText: "2026-09-01T10:00:00+09:00"
                                selectByMouse: true
                                Accessible.name: "新建日程开始时间"
                            }

                            FieldLabel { text: "结束时间（ISO 8601）" }
                            TextField {
                                id: calendarCreateEnd
                                Layout.fillWidth: true
                                placeholderText: "2026-09-01T11:00:00+09:00"
                                selectByMouse: true
                                Accessible.name: "新建日程结束时间"
                            }

                            FieldLabel { text: "IANA 时区" }
                            TextField {
                                id: calendarCreateTimeZone
                                Layout.fillWidth: true
                                text: "Asia/Tokyo"
                                placeholderText: "例如 Asia/Tokyo"
                                selectByMouse: true
                                Accessible.name: "新建日程 IANA 时区"
                            }

                            FieldLabel { text: "提前提醒（分钟）" }
                            SpinBox {
                                id: calendarCreateReminder
                                from: 0
                                to: 10080
                                value: 10
                                editable: true
                                Accessible.name: "新建日程提前提醒分钟"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: root.mayConfirmExecute
                                      ? "生成后还必须核对最终差异并再次确认。"
                                      : "当前策略不是 confirm-execute，不能生成写入提案；未确认绝不写入。"
                                color: root.mayConfirmExecute ? root.mutedInk : root.cord
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                            QuietButton {
                                text: "生成创建预览"
                                accent: true
                                enabled: root.mayConfirmExecute
                                         && calendarCreateTitle.text.trim() !== ""
                                         && calendarCreateStart.text.trim() !== ""
                                         && calendarCreateEnd.text.trim() !== ""
                                         && calendarCreateTimeZone.text.trim() !== ""
                                onClicked: root.proposeCalendarCreate()
                            }
                        }
                    }

                    PaperCard {
                        visible: root.provider === "calendar" && root.selectedCalendarId !== ""
                        heading: "准备修改所选日程"
                        caption: "这里只允许提出标题、开始时间和结束时间的变化。生成预览不会写入；只有最终手动确认才会提交。协助建议还需将本地留存设为可搜索摘要或扩展缓存。"

                        Label {
                            Layout.fillWidth: true
                            text: "所选日程：" + (root.calendarTitle(root.selectedCalendarItem) || root.selectedCalendarId)
                            color: root.ink
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: 2
                            columnSpacing: 12
                            rowSpacing: 8

                            FieldLabel {
                                text: "标题"
                                Layout.preferredWidth: 150
                            }
                            TextField {
                                id: calendarUpdateTitle
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程标题"
                            }

                            FieldLabel { text: "开始时间（ISO 8601）" }
                            TextField {
                                id: calendarUpdateStart
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程开始时间"
                            }

                            FieldLabel { text: "结束时间（ISO 8601）" }
                            TextField {
                                id: calendarUpdateEnd
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程结束时间"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: calendarAssistInstruction
                                Layout.fillWidth: true
                                placeholderText: "可选：请莉莉丝给出修改建议（不会自动写入）"
                                selectByMouse: true
                                Accessible.name: "日程协助说明"
                            }
                            QuietButton {
                                text: Boolean(root.connectorAssistResult.busy)
                                      && root.pendingAssistProvider === "calendar" ? "协助中…" : "协助"
                                enabled: root.mayAssist && calendarAssistInstruction.text.trim() !== ""
                                         && !(Boolean(root.connectorAssistResult.busy)
                                              && root.pendingAssistProvider === "calendar")
                                onClicked: root.requestAssist("calendar", root.selectedCalendarId,
                                                              calendarAssistInstruction.text)
                            }
                        }

                        TextArea {
                            visible: root.calendarAssistSuggestion !== ""
                            Layout.fillWidth: true
                            implicitHeight: Math.min(150, Math.max(72, contentHeight + 20))
                            text: root.calendarAssistSuggestion
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 11
                            Accessible.name: "日程协助建议"
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: root.mayConfirmExecute
                                      ? "请核对变更；未确认前不会写入 Calendar。"
                                      : "当前策略不是 confirm-execute，不能生成更新提案；未确认绝不写入。"
                                color: root.mayConfirmExecute ? root.mutedInk : root.cord
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                            QuietButton {
                                text: "生成更新预览"
                                accent: true
                                enabled: root.mayConfirmExecute
                                         && Object.keys(root.calendarUpdatePayload()).length > 0
                                onClicked: root.proposeCalendarUpdate()
                            }
                        }
                    }

                    PaperCard {
                        visible: root.provider === "calendar" && root.selectedCalendarId === ""
                        heading: "修改已有日程"
                        caption: "请在“工作与共鸣”的近期日程中选择“准备修改”。选择只会打开草稿，不会写入 Calendar。"
                    }

                    PaperCard {
                        visible: root.provider === "slack" && root.selectedSlackId !== ""
                        heading: "准备回复所选 Slack 信笺"
                        caption: "正文可在这里编辑。生成预览不会发送；只有核对最终正文并手动确认后才会回复原频道、私信或线程。协助拟稿还需将本地留存设为可搜索摘要或扩展缓存。"

                        Label {
                            Layout.fillWidth: true
                            text: "原信笺 · " + root.selectedSlackId
                            color: root.mutedInk
                            font.pixelSize: 10
                            elide: Text.ElideRight
                        }

                        TextArea {
                            Layout.fillWidth: true
                            implicitHeight: Math.min(130, Math.max(64, contentHeight + 18))
                            text: String(root.selectedSlackItem.text || root.selectedSlackItem.summary
                                         || "当前留存档位没有保存原文")
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.mutedInk
                            font.pixelSize: 11
                            Accessible.name: "所选 Slack 信笺正文"
                        }

                        FieldLabel { text: "回复正文" }
                        TextArea {
                            id: slackReplyText
                            Layout.fillWidth: true
                            implicitHeight: Math.min(190, Math.max(90, contentHeight + 20))
                            placeholderText: "输入最终回复正文"
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 11
                            Accessible.name: "Slack 回复草稿正文"
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: slackAssistInstruction
                                Layout.fillWidth: true
                                placeholderText: "可选：说明希望怎样拟稿（不会自动发送）"
                                selectByMouse: true
                                Accessible.name: "Slack 回复协助说明"
                            }
                            QuietButton {
                                text: Boolean(root.connectorAssistResult.busy)
                                      && root.pendingAssistProvider === "slack" ? "协助中…" : "协助拟稿"
                                enabled: root.mayAssist && slackAssistInstruction.text.trim() !== ""
                                         && !(Boolean(root.connectorAssistResult.busy)
                                              && root.pendingAssistProvider === "slack")
                                onClicked: root.requestAssist("slack", root.selectedSlackId,
                                                              slackAssistInstruction.text)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: root.mayConfirmExecute
                                      ? "生成后仍需核对最终正文并确认；未确认不会发送。"
                                      : "当前策略不是 confirm-execute，不能生成发送提案；未确认绝不发送。"
                                color: root.mayConfirmExecute ? root.mutedInk : root.cord
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                            QuietButton {
                                text: "生成回复预览"
                                accent: true
                                enabled: root.mayConfirmExecute && slackReplyText.text.trim() !== ""
                                onClicked: root.proposeSlackReply()
                            }
                        }
                    }

                    PaperCard {
                        visible: root.provider === "slack" && root.selectedSlackId === ""
                        heading: "回复 Slack 信笺"
                        caption: "请在“工作与共鸣”的单条 Slack 信笺上选择“回复”。选择只会打开草稿，未确认不会发送。"
                    }

                    PaperCard {
                        visible: root.providerHasProposal
                        heading: "最终操作预览"
                        caption: "下方内容在确认后才会提交。关闭窗口、拒绝或让提案过期都不会写入日历或发送 Slack 消息。"

                        TextArea {
                            Layout.fillWidth: true
                            implicitHeight: Math.min(270, Math.max(130, contentHeight + 24))
                            text: root.proposalText()
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.family: "Consolas"
                            font.pixelSize: 11
                            Accessible.name: "连接器操作最终预览"
                        }

                        FieldLabel {
                            visible: root.provider === "slack"
                            text: "可编辑的最终回复正文"
                        }

                        TextArea {
                            id: slackFinalText
                            visible: root.provider === "slack"
                            Layout.fillWidth: true
                            implicitHeight: Math.min(190, Math.max(90, contentHeight + 20))
                            text: root.providerHasProposal
                                  ? String((root.proposal.after || ({})).text || "") : ""
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 11
                            Accessible.name: "Slack 最终回复正文"
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            QuietButton {
                                visible: root.provider === "slack"
                                text: "更新最终预览"
                                enabled: root.mayConfirmExecute
                                         && !root.proposalExecuting
                                         && slackFinalText.text.trim() !== ""
                                         && slackFinalText.text !== String((root.proposal.after || ({})).text || "")
                                onClicked: root.replaceSlackProposal()
                            }
                            Item { Layout.fillWidth: true }
                            QuietButton {
                                text: "拒绝"
                                enabled: !root.proposalExecuting
                                onClicked: root.resolveProposal(false)
                            }
                            QuietButton {
                                text: root.provider === "calendar"
                                      ? (root.calendarCreateProposal ? "确认并创建日程" : "确认并修改日程")
                                      : "确认并发送回复"
                                accent: true
                                visible: root.mayConfirmExecute
                                enabled: !root.proposalExecuting
                                         && (root.provider !== "slack"
                                             || slackFinalText.text === String((root.proposal.after || ({})).text || ""))
                                onClicked: root.resolveProposal(true)
                            }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                visible: root.notice !== ""
                implicitHeight: noticeText.implicitHeight + 16
                radius: 9
                color: "#f6ede3"
                border.color: root.hairline

                Label {
                    id: noticeText
                    anchors.fill: parent
                    anchors.margins: 8
                    text: root.notice
                    color: root.ink
                    font.pixelSize: 11
                    wrapMode: Text.WordWrap
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 9

                Label {
                    Layout.fillWidth: true
                    text: "危险边界：这些操作只由你在本窗口发起，不会提供给模型作为工具。"
                    color: root.mutedInk
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }

                QuietButton {
                    text: "清除本地内容"
                    onClicked: clearContentDialog.open()
                }

                QuietButton {
                    text: "断开账号"
                    onClicked: disconnectDialog.open()
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 9

                Label {
                    Layout.fillWidth: true
                    text: root.provider === "calendar"
                          ? "OAuth Token 仅存 Windows Credential Manager"
                          : "xapp 与 OAuth Token 仅存 Windows Credential Manager"
                    color: root.mutedInk
                    font.pixelSize: 10
                }

                QuietButton {
                    objectName: "connectorSaveConfigurationButton"
                    text: "保存配置"
                    onClicked: root.saveConfiguration(true)
                }

                QuietButton {
                    objectName: "connectorBeginOAuthButton"
                    text: root.provider === "calendar" ? "在浏览器中连接 Google" : "在浏览器中连接 Slack"
                    accent: true
                    onClicked: root.beginOAuth()
                }
            }
        }
    }

    Dialog {
        id: clearContentDialog
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        width: Math.min(520, root.width - 48)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        title: "再次确认：清除本地内容"
        standardButtons: Dialog.Cancel | Dialog.Ok

        contentItem: Label {
            text: "将删除当前 " + (root.provider === "calendar" ? "Calendar" : "Slack")
                  + " 连接器在本机保存的正文、摘要与索引。不会删除外部服务中的内容，也不会断开账号。此本地清除无法撤销，是否继续？"
            color: root.ink
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }

        onOpened: {
            standardButton(Dialog.Cancel).text = "取消"
            standardButton(Dialog.Ok).text = "确认清除"
            standardButton(Dialog.Cancel).forceActiveFocus()
        }
        onAccepted: {
            var response = root.callBackend("connectorClearContent", [root.provider],
                                            "本地内容没有清除")
            if (response.ok && response.result !== false
                    && !(response.result && response.result.ok === false))
                root.notice = "当前连接器的本地内容已清除；外部服务未被修改。"
            else if (response.result && response.result.error)
                root.notice = String(response.result.error)
            else
                root.notice = "本地内容没有清除。"
        }
        onClosed: Qt.callLater(function() { root.requestActivate() })
    }

    Dialog {
        id: disconnectDialog
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        width: Math.min(540, root.width - 48)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        title: "再次确认：断开账号"
        standardButtons: Dialog.Cancel | Dialog.Ok

        contentItem: Label {
            text: root.provider === "slack"
                  ? "将删除 Windows Credential Manager 中的 Slack OAuth token 并停止 Socket Mode。可保留 custom-app 配置，之后重新授权即可再次连接。不会发送或删除 Slack 消息。是否继续？"
                  : "将删除 Windows Credential Manager 中的 Google OAuth token 并停止同步。不会删除 Calendar 中的日程。之后需要重新授权才能连接，是否继续？"
            color: root.ink
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }

        onOpened: {
            standardButton(Dialog.Cancel).text = "取消"
            standardButton(Dialog.Ok).text = "确认断开"
            standardButton(Dialog.Cancel).forceActiveFocus()
        }
        onAccepted: {
            var response = root.callBackend("connectorDisconnect", [root.provider],
                                            "账号没有断开")
            if (response.ok && response.result !== false
                    && !(response.result && response.result.ok === false))
                root.notice = root.provider === "slack"
                              ? "Slack 已断开并停止 Socket Mode；custom-app 配置可继续保留。"
                              : "Calendar 已断开；外部日程未被删除。"
            else if (response.result && response.result.error)
                root.notice = String(response.result.error)
            else
                root.notice = "账号没有断开。"
        }
        onClosed: Qt.callLater(function() { root.requestActivate() })
    }
}
