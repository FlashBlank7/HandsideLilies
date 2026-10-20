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
    property string section: "use"
    property bool choosingItem: false
    property bool creatingCalendar: false
    property bool showAllItems: false
    property string lastSelectionKey: ""
    readonly property bool settingsPage: section === "settings"
    readonly property bool narrow: width < 640
    property color paper: "#fafafb"
    property color paperRaised: "#ffffff"
    property color ink: "#353b45"
    property color mutedInk: "#758091"
    property color hairline: "#e4e5ea"
    property color cord: "#985d6d"
    property color calm: "#567b6c"

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
    readonly property bool connectionHealthy: Boolean(currentInfo.connected)
                                               && String(currentInfo.error || currentInfo.errorMessage || "") === ""
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
    readonly property bool mayAssist: (retentionValue === "searchable-summary" || retentionValue === "extended-cache")
                                      && (assistanceValue === "assist"
                                          || assistanceValue === "confirm-execute")
    readonly property bool mayConfirmExecute: assistanceValue === "confirm-execute"
    property string selectedCalendarId: ""
    property string selectedSlackId: ""
    readonly property var selectedCalendarItem: calendarItem(selectedCalendarId)
    readonly property var selectedSlackItem: slackItem(selectedSlackId)
    readonly property var currentItems: provider === "slack" ? slackItems : calendarItems
    readonly property string currentSelectedId: provider === "slack" ? selectedSlackId : selectedCalendarId
    readonly property var currentSelectedItem: provider === "slack" ? selectedSlackItem : selectedCalendarItem
    readonly property string assistExplanation: assistanceExplanation(currentSelectedItem)
    property string pendingAssistProvider: ""
    property string pendingAssistEventId: ""
    property string calendarAssistSuggestion: ""
    // Each connector owns an independent, non-secret edit draft.  Secrets are
    // intentionally never copied into these objects or restored into fields.
    property var calendarConfigurationDraft: null
    property var slackConfigurationDraft: null
    property var calendarConfigurationBaseline: null
    property var slackConfigurationBaseline: null
    property string loadedProvider: ""
    property bool applyingProviderDraft: false
    property bool configurationReady: false
    readonly property string slackManifestDraftKey: JSON.stringify({
        scope: scopeBox.currentIndex, assistance: assistanceBox.currentIndex,
        redirectUri: slackRedirectUri.text.trim(), provider: provider
    })
    onSlackManifestDraftKeyChanged: {
        if (slackManifest.text !== "") {
            slackManifest.text = ""
            notice = "配置选项已改变，请重新生成预览。未保存或授权。"
        }
    }

    width: Math.min(900, Screen.width - 48)
    height: Math.min(760, Screen.height - 64)
    minimumWidth: 480
    minimumHeight: 480
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

    function showUsage() {
        section = "use"
        choosingItem = false
        creatingCalendar = false
        notice = ""
    }

    function canAssistItem(item) {
        return mayAssist && item && item.assistanceState === "available"
    }

    function assistanceExplanation(item) {
        if (assistanceValue !== "assist" && assistanceValue !== "confirm-execute")
            return "当前只做提醒。需要总结或拟稿，可在“连接与隐私”中开启协助。"
        if (retentionValue === "metadata")
            return "当前不保存正文，无法总结或拟稿。你可以保留这个设置，直接查看原文。"
        if (!canAssistItem(item))
            return "这条内容没有可用的正文或摘要，暂时无法协助。调整留存设置不会补回旧内容。"
        return "只会把这一项和你的要求交给模型，不附带其他消息或长期记忆。"
    }

    function chooseItem(eventId) {
        var response = callBackend("connectorSelectItem", [provider, eventId], "这条内容暂时无法打开")
        if (response.ok) {
            synchronizeBackendSelection()
            section = "use"
            choosingItem = false
            creatingCalendar = false
            connectorSetupScroll.contentItem.contentY = 0
        }
    }

    onSectionChanged: {
        if (section !== "settings" && configurationReady)
            slackXappToken.text = ""
        Qt.callLater(function() { connectorSetupScroll.contentItem.contentY = 0 })
    }

    function canonicalAxis(axis, value) {
        var text = String(value || "").trim()
        var aliases = {
            "必要": "necessary", "精选": "selected", "广泛": "broad",
            "安静": "quiet", "优先": "priority", "即时": "immediate",
            "元数据": "metadata", "可搜索摘要": "searchable-summary",
            "扩展缓存": "extended-cache", "提醒": "reminder",
            "协助": "assist", "确认执行": "confirm-execute"
        }
        text = aliases[text] || text
        var allowed = {
            scope: ["necessary", "selected", "broad"],
            interruption: ["quiet", "priority", "immediate"],
            retention: ["metadata", "searchable-summary", "extended-cache"],
            assistance: ["reminder", "assist", "confirm-execute"]
        }
        var defaults = {
            scope: "necessary", interruption: "quiet",
            retention: "metadata", assistance: "assist"
        }
        return allowed[axis].indexOf(text) >= 0 ? text : defaults[axis]
    }

    function safeStringList(value) {
        var result = []
        if (value === undefined || value === null || typeof value === "string")
            return result
        try {
            for (var index = 0; index < value.length; ++index) {
                var item = String(value[index] || "").trim()
                if (item !== "" && result.indexOf(item) < 0)
                    result.push(item)
            }
        } catch (error) {
            return []
        }
        return result
    }

    function draftFromInfo(providerName, info) {
        var status = info || ({})
        var savedPolicy = status.policyCanonical || ({})
        var safeConfiguration = status.configuration || ({})
        var policy = {
            scope: canonicalAxis("scope", savedPolicy.scope),
            interruption: canonicalAxis("interruption", savedPolicy.interruption),
            retention: canonicalAxis("retention", savedPolicy.retention),
            assistance: canonicalAxis("assistance", savedPolicy.assistance),
            selectedSources: safeStringList(savedPolicy.selectedSources)
        }
        if (providerName === "calendar") {
            return {
                policy: policy,
                clientId: String(safeConfiguration.clientId || "")
            }
        }
        return {
            policy: policy,
            clientId: String(safeConfiguration.clientId || ""),
            currentUserId: String(safeConfiguration.currentUserId || ""),
            redirectUri: String(safeConfiguration.redirectUri
                                || "http://127.0.0.1:53682/oauth/callback"),
            selectedChannels: policy.selectedSources.join(", ")
        }
    }

    function providerDraft(providerName) {
        return providerName === "slack" ? slackConfigurationDraft
                                         : calendarConfigurationDraft
    }

    function setProviderDraft(providerName, draft) {
        if (providerName === "slack")
            slackConfigurationDraft = draft
        else
            calendarConfigurationDraft = draft
    }

    function ensureProviderDraft(providerName) {
        var draft = providerDraft(providerName)
        if (draft !== null && draft !== undefined)
            return draft
        draft = draftFromInfo(providerName,
                              providerName === "slack" ? slackInfo : calendarInfo)
        setProviderDraft(providerName, draft)
        setProviderBaseline(providerName, draft)
        return draft
    }

    function setProviderBaseline(providerName, draft) {
        if (providerName === "slack")
            slackConfigurationBaseline = draft
        else
            calendarConfigurationBaseline = draft
    }

    function mergeCanonicalDraft(previous, edited, incoming) {
        // Refresh untouched fields from canonical state without throwing away
        // the user's independent, unsaved tab edits. Arrays are atomic values.
        if (JSON.stringify(edited) === JSON.stringify(previous))
            return incoming
        if (incoming === null || typeof incoming !== "object" || Array.isArray(incoming))
            return edited
        var result = ({})
        for (var key in incoming)
            result[key] = mergeCanonicalDraft((previous || ({}))[key],
                                               (edited || ({}))[key], incoming[key])
        return result
    }

    function synchronizeProviderStatus(providerName) {
        if (!configurationReady || applyingProviderDraft)
            return
        var incoming = draftFromInfo(providerName,
                                     providerName === "slack" ? slackInfo : calendarInfo)
        var baseline = providerName === "slack" ? slackConfigurationBaseline
                                                 : calendarConfigurationBaseline
        if (JSON.stringify(incoming) === JSON.stringify(baseline))
            return
        if (loadedProvider === providerName)
            captureProviderDraft(providerName)
        var edited = providerDraft(providerName)
        setProviderDraft(providerName, baseline === null || edited === null
                         ? incoming : mergeCanonicalDraft(baseline, edited, incoming))
        setProviderBaseline(providerName, incoming)
        if (loadedProvider === providerName)
            applyProviderDraft(providerName, false)
    }

    function policyFromControls(existingPolicy) {
        var previous = existingPolicy || ({})
        return {
            scope: ["necessary", "selected", "broad"][Math.max(0, scopeBox.currentIndex)],
            interruption: ["quiet", "priority", "immediate"][Math.max(0, interruptionBox.currentIndex)],
            retention: ["metadata", "searchable-summary", "extended-cache"][Math.max(0, retentionBox.currentIndex)],
            assistance: ["reminder", "assist", "confirm-execute"][Math.max(0, assistanceBox.currentIndex)],
            selectedSources: safeStringList(previous.selectedSources)
        }
    }

    function captureProviderDraft(providerName) {
        if (applyingProviderDraft || providerName === "")
            return
        var previous = ensureProviderDraft(providerName)
        var nextPolicy = policyFromControls(previous.policy)
        if (providerName === "slack") {
            nextPolicy.selectedSources = selectedChannels()
            setProviderDraft("slack", {
                policy: nextPolicy,
                clientId: slackClientId.text.trim(),
                currentUserId: slackCurrentUserId.text.trim(),
                redirectUri: slackRedirectUri.text.trim(),
                selectedChannels: slackChannels.text
            })
        } else {
            setProviderDraft("calendar", {
                policy: nextPolicy,
                clientId: googleClientId.text.trim()
            })
        }
    }

    function applyProviderDraft(providerName, clearSecret) {
        var draft = ensureProviderDraft(providerName)
        var policy = draft.policy || ({})
        applyingProviderDraft = true
        scopeBox.currentIndex = ["necessary", "selected", "broad"].indexOf(
                                    canonicalAxis("scope", policy.scope))
        interruptionBox.currentIndex = ["quiet", "priority", "immediate"].indexOf(
                                           canonicalAxis("interruption", policy.interruption))
        retentionBox.currentIndex = ["metadata", "searchable-summary", "extended-cache"].indexOf(
                                        canonicalAxis("retention", policy.retention))
        assistanceBox.currentIndex = ["reminder", "assist", "confirm-execute"].indexOf(
                                         canonicalAxis("assistance", policy.assistance))
        if (providerName === "calendar") {
            googleClientId.text = String(draft.clientId || "")
        } else {
            slackClientId.text = String(draft.clientId || "")
            slackCurrentUserId.text = String(draft.currentUserId || "")
            slackRedirectUri.text = String(draft.redirectUri
                                           || "http://127.0.0.1:53682/oauth/callback")
            slackChannels.text = String(draft.selectedChannels || "")
        }
        // Tokens remain write-only. An unrelated background status refresh
        // must not erase a token while the user is typing it in this tab.
        if (clearSecret !== false)
            slackXappToken.text = ""
        loadedProvider = providerName
        applyingProviderDraft = false
    }

    function activateProvider(providerName) {
        if (!configurationReady)
            return
        var normalized = normalizedProvider(providerName)
        if (normalized !== "calendar" && normalized !== "slack")
            normalized = "calendar"
        if (loadedProvider !== "")
            captureProviderDraft(loadedProvider)
        applyProviderDraft(normalized)
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
        var key = selectedProvider + ":" + selectedId
        if (lastSelectionKey === key)
            return
        lastSelectionKey = key
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
        if (selectedId !== "") {
            section = "use"
            creatingCalendar = false
            choosingItem = false
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
        section = "use"
        Qt.callLater(function() { connectorSetupScroll.contentItem.contentY = Math.max(0, proposalCard.y - 8) })
        return true
    }

    function proposeCalendarCreate() {
        if (!mayConfirmExecute) {
            notice = "请先在“连接与隐私”中将协助能力改为“确认执行”，并完成所需授权。"
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
            notice = "请先在“连接与隐私”中开启“确认执行”并完成授权。"
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
            notice = "请先在“连接与隐私”中开启“确认执行”并完成授权。"
            return
        }
        var response = callBackend("slackProposeReply",
                                   [selectedSlackId, slackReplyText.text.trim()],
                                   "Slack 回复提案没有生成")
        proposalAccepted(response, "已生成 Slack 回复预览；未确认前不会发送。")
    }

    function requestAssist(providerName, eventId, instruction) {
        var item = providerName === "slack" ? slackItem(eventId) : calendarItem(eventId)
        if (!canAssistItem(item)) {
            notice = assistanceExplanation(item)
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
    onProviderChanged: {
        activateProvider(provider)
        creatingCalendar = false
        choosingItem = false
        showAllItems = false
        notice = ""
    }
    onCalendarInfoChanged: synchronizeProviderStatus("calendar")
    onSlackInfoChanged: synchronizeProviderStatus("slack")
    onCalendarItemsChanged: {
        if (selectedCalendarId !== "" && calendarUpdateTitle.text === ""
                && calendarUpdateStart.text === "" && calendarUpdateEnd.text === "")
            populateCalendarUpdate()
    }
    onVisibleChanged: {
        if (visible) {
            activateProvider(provider)
            synchronizeBackendSelection()
        } else if (configurationReady) {
            captureProviderDraft(loadedProvider)
            slackXappToken.text = ""
        }
    }
    Component.onCompleted: {
        // Hydrate both connectors immediately, even though only one tab is
        // visible.  This makes the persisted policies authoritative and keeps
        // the first provider switch from inheriting the other tab's controls.
        ensureProviderDraft("calendar")
        ensureProviderDraft("slack")
        configurationReady = true
        activateProvider(provider)
        synchronizeBackendSelection()
    }

    function selectedChannels() {
        return slackChannels.text.split(/[，,\n]/).map(function(item) {
            return item.trim()
        }).filter(function(item) {
            return item.length > 0
        })
    }

    function policyMap() {
        var currentDraft = ensureProviderDraft(provider)
        var result = policyFromControls(currentDraft.policy)
        if (provider === "slack")
            result.selectedSources = selectedChannels()
        return result
    }

    function configurationMap() {
        var result = policyMap()
        result.policy = policyMap()
        result.clientId = provider === "calendar" ? googleClientId.text.trim()
                                                   : slackClientId.text.trim()
        if (provider === "slack") {
            // Tokens are write-only and omitted entirely when the user did not
            // type a new value.  Existing Credential Manager entries survive.
            if (slackXappToken.text.trim() !== "")
                result.xappToken = slackXappToken.text
            result.currentUserId = slackCurrentUserId.text.trim()
            result.selectedChannels = selectedChannels()
            result.redirectUri = slackRedirectUri.text.trim()
        }
        return result
    }

    function saveConfiguration(showConfirmation) {
        var response = callBackend("connectorConfigure", [provider, configurationMap()],
                                   "配置没有保存，请检查必填项")
        if (response.ok) {
            var returnedStatus = response.result && response.result.status
                                 ? response.result.status : null
            if (returnedStatus) {
                var savedDraft = draftFromInfo(provider, returnedStatus)
                setProviderDraft(provider, savedDraft)
                setProviderBaseline(provider, savedDraft)
            } else
                captureProviderDraft(provider)
            applyProviderDraft(provider)
            if (showConfirmation)
                notice = "配置已交给本地连接器保存"
        }
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
        if (provider !== "slack")
            return
        // Do not call configurationMap(): it includes write-only xapp input.
        // A preview must neither save a draft nor fall back to saved policy.
        slackManifest.text = ""
        var policy = policyMap()
        var response = callBackend("slackGenerateManifest", [{
            policy: {scope: policy.scope, assistance: policy.assistance},
            redirectUri: slackRedirectUri.text.trim()
        }], "配置预览没有生成，请检查回调地址和权限选项")
        if (!response.ok)
            return
        var result = response.result
        if (!result || result.ok !== true || typeof result.manifest !== "string"
                || result.manifest.trim() === "") {
            notice = "配置预览没有生成，未使用旧配置。"
            return
        }
        slackManifest.text = result.manifest
        notice = "已生成本地预览；未保存配置、读取令牌或连接账号。"
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
            notice = "当前未开启“确认执行”，不能更新发送预览。"
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
        if (String(info.error || info.errorMessage || "") !== "")
            return "连接异常"
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
        if (state === "configured")
            return "尚未连接"
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
                wrapMode: Text.WordWrap
            }

            Label {
                visible: card.caption !== ""
                Layout.fillWidth: true
                text: card.caption
                color: root.mutedInk
                font.pixelSize: 13
                lineHeight: 1.35
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
        property bool selected: false
        implicitHeight: 37
        leftPadding: 15
        rightPadding: 15
        font.pixelSize: 12
        contentItem: Label {
            text: quietButton.text
            textFormat: Text.PlainText
            color: quietButton.accent ? "#fffdf9" : (quietButton.selected ? root.cord : root.ink)
            opacity: quietButton.enabled ? 1 : 0.45
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 10
            color: quietButton.accent ? root.cord
                                      : (quietButton.selected ? "#f2e8ed" : (quietButton.hovered ? "#f0f1f4" : root.paperRaised))
            border.color: quietButton.accent ? root.cord : root.hairline
            opacity: quietButton.enabled ? 1 : 0.45
        }
    }

    component InputField: TextField {
        implicitHeight: 40
        leftPadding: 11
        rightPadding: 11
        font.pixelSize: 13
        color: root.ink
        placeholderTextColor: root.mutedInk
        background: Rectangle {
            radius: 9
            color: "#f5f6f8"
            border.color: parent.activeFocus ? root.cord : root.hairline
        }
    }

    component NoteField: TextArea {
        padding: 11
        font.pixelSize: 13
        textFormat: TextEdit.PlainText
        placeholderTextColor: root.mutedInk
        background: Rectangle {
            radius: 9
            color: "#f5f6f8"
            border.color: parent.activeFocus ? root.cord : root.hairline
        }
    }

    component PolicyAxis: ColumnLayout {
        id: axis
        required property string label
        required property string explanation
        required property string controlName
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
            objectName: axis.controlName
            Layout.fillWidth: true
            implicitHeight: 38
            font.pixelSize: 12
            Accessible.name: axis.label
        }

        Label {
            Layout.fillWidth: true
            text: axis.explanation
            color: root.mutedInk
            font.pixelSize: 12
            lineHeight: 1.35
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

            GridLayout {
                Layout.fillWidth: true
                columns: root.narrow ? 1 : 2
                columnSpacing: 12
                rowSpacing: 12

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 3

                    Label {
                        Layout.fillWidth: true
                        text: "日历与 Slack 信笺"
                        color: root.ink
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                    }

                    Label {
                        Layout.fillWidth: true
                        text: "可选连接，不影响本地任务和阅读。"
                        color: root.mutedInk
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                    }
                }

                RowLayout {
                    objectName: "connectorProviderTabs"
                    Layout.fillWidth: root.narrow
                    QuietButton {
                        objectName: "connectorCalendarProvider"
                        text: "Google Calendar"
                        selected: root.provider === "calendar"
                        onClicked: root.provider = "calendar"
                    }
                    QuietButton {
                        objectName: "connectorSlackProvider"
                        text: "Slack"
                        selected: root.provider === "slack"
                        onClicked: root.provider = "slack"
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                QuietButton {
                    objectName: "connectorUseTab"
                    text: root.providerHasProposal ? "日程与信笺 · 待确认" : "日程与信笺"
                    accent: !root.settingsPage
                    onClicked: root.section = "use"
                }
                QuietButton {
                    objectName: "connectorSettingsTab"
                    text: "连接与隐私"
                    accent: root.settingsPage
                    onClicked: root.section = "settings"
                }
                Item { Layout.fillWidth: true }
            }

            ScrollView {
                id: connectorSetupScroll
                objectName: "connectorSetupScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                ColumnLayout {
                    width: connectorSetupScroll.availableWidth - 12
                    spacing: 18

                    PaperCard {
                        objectName: "connectorOptionalIntro"
                        visible: !root.settingsPage && !root.currentInfo.connected
                                 && root.currentItems.length === 0 && !root.providerHasProposal
                        heading: "需要时，再连接"
                        caption: root.provider === "calendar"
                                 ? "连接后可查看日程。需要你自己的 Google Desktop OAuth Client；目前不是一键登录。"
                                 : "连接后可查看信笺。需要你创建 Slack custom app，组织管理员也可能限制接入。"
                        QuietButton {
                            objectName: "connectorStartSetup"
                            text: "查看连接步骤"
                            onClicked: root.section = "settings"
                        }
                    }

                    PaperCard {
                        visible: root.settingsPage || root.currentInfo.connected || root.currentItems.length > 0
                        heading: root.settingsPage ? (root.provider === "calendar" ? "Google Calendar 状态" : "Slack 状态") : ""

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.settingsPage && root.narrow ? 1 : 3
                            columnSpacing: 10
                            rowSpacing: 8

                            Rectangle {
                                implicitWidth: stateText.implicitWidth + 22
                                implicitHeight: 27
                                radius: 13
                                color: root.connectionHealthy ? "#e5efe9" : "#f2e8dd"
                                border.color: root.connectionHealthy ? root.calm : root.hairline

                                Label {
                                    id: stateText
                                    objectName: "connectorConnectionState"
                                    anchors.centerIn: parent
                                    text: root.readableState(root.currentInfo)
                                    color: root.connectionHealthy ? root.calm : root.mutedInk
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                }
                            }

                            Label {
                                objectName: "connectorAccountLabel"
                                Layout.fillWidth: true
                                text: String(root.currentInfo.accountName
                                             || root.currentInfo.displayName
                                             || root.currentInfo.workspace
                                             || root.currentInfo.account
                                             || (root.currentInfo.connected ? "当前账号" : "尚未绑定账号"))
                                color: root.ink
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }

                            Label {
                                Layout.fillWidth: true
                                visible: root.settingsPage
                                text: String(root.currentInfo.lastSyncAt || "") === ""
                                      ? "尚未同步"
                                      : "最后同步：" + String(root.currentInfo.lastSyncAt)
                                color: root.mutedInk
                                font.pixelSize: 12
                                wrapMode: Text.WordWrap
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
                        objectName: "connectorItemPicker"
                        visible: !root.settingsPage && !root.creatingCalendar && !root.providerHasProposal
                                 && (root.currentSelectedId === "" || root.choosingItem)
                                 && (root.currentInfo.connected || root.currentItems.length > 0)
                        heading: root.provider === "calendar" ? "选一条日程" : "选一封信笺"
                        caption: root.currentItems.length === 0 ? "这里暂时没有内容。" : "点选后再决定查看、协助或准备修改，不会自动交给模型。"
                        Repeater {
                            model: root.currentItems.slice(0, root.showAllItems ? 30 : 6)
                            delegate: QuietButton {
                                required property var modelData
                                required property int index
                                Layout.fillWidth: true
                                objectName: "connectorChooseItem_" + index
                                text: String(modelData.summary || modelData.title || modelData.text
                                             || (root.provider === "calendar" ? "日程" : "信笺"))
                                      .replace(/\s+/g, " ").slice(0, 120)
                                onClicked: root.chooseItem(root.itemId(modelData))
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            QuietButton {
                                visible: root.currentItems.length > 6
                                text: root.showAllItems ? "收起" : "显示更多"
                                onClicked: root.showAllItems = !root.showAllItems
                            }
                            QuietButton {
                                visible: root.provider === "calendar" && root.currentInfo.connected
                                text: "刷新日程"
                                onClicked: root.callBackend("calendarRefresh", [], "刷新没有开始")
                            }
                        }
                    }

                    RowLayout {
                        visible: !root.settingsPage && !root.providerHasProposal && (root.currentInfo.connected || root.currentSelectedId !== "")
                        Layout.fillWidth: true
                        QuietButton {
                            visible: root.currentSelectedId !== "" && !root.choosingItem
                            text: "换一条"
                            onClicked: { root.choosingItem = true; root.creatingCalendar = false }
                        }
                        QuietButton {
                            objectName: "connectorOpenOriginal"
                            visible: root.currentSelectedId !== "" && !root.choosingItem
                            text: "打开原文"
                            enabled: String(root.currentSelectedItem.link || "") !== ""
                            onClicked: root.callBackend(root.provider === "calendar" ? "calendarOpenEvent" : "slackOpenMessage",
                                                        [root.currentSelectedId], "原文没有打开，请检查链接或默认浏览器")
                        }
                        QuietButton {
                            objectName: "connectorCreateCalendarToggle"
                            visible: root.provider === "calendar" && root.currentInfo.connected
                            text: root.creatingCalendar ? "收起新建" : "新建日程"
                            onClicked: root.creatingCalendar = !root.creatingCalendar
                        }
                        Item { Layout.fillWidth: true }
                    }

                    PaperCard {
                        objectName: "connectorGoogleSetupCard"
                        visible: root.settingsPage && root.provider === "calendar"
                        heading: "Google Desktop OAuth"
                        caption: "使用你自己的 Desktop OAuth Client。授权在系统浏览器中完成，莉莉丝不会嵌入登录页。"

                        FieldLabel { text: "Desktop OAuth Client ID" }
                        InputField {
                            id: googleClientId
                            objectName: "connectorGoogleClientId"
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
                        objectName: "connectorSlackSetupCard"
                        visible: root.settingsPage && root.provider === "slack"
                        heading: "Slack 本地个人接入"
                        caption: "使用你创建的 custom app 与 Socket Mode。令牌只交给 Windows 安全存储，不写入项目数据目录。"

                        Label {
                            Layout.fillWidth: true
                            text: "先生成配置，在 Slack 创建应用；再填写应用信息并授权。生成预览不需要 Client ID 或令牌。"
                            color: root.ink
                            font.pixelSize: 13
                            lineHeight: 1.35
                            wrapMode: Text.WordWrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            QuietButton {
                                objectName: "connectorGenerateSlackManifest"
                                text: "生成应用配置"
                                onClicked: root.generateSlackManifest()
                            }
                            QuietButton {
                                objectName: "connectorCopySlackManifest"
                                text: "复制全部"
                                enabled: slackManifest.text !== ""
                                onClicked: { slackManifest.selectAll(); slackManifest.copy() }
                            }
                        }
                        ScrollView {
                            objectName: "connectorSlackManifestScroll"
                            visible: slackManifest.text !== ""
                            Layout.fillWidth: true
                            Layout.preferredHeight: 190
                            clip: true
                            NoteField {
                                id: slackManifest
                                objectName: "connectorSlackManifestPreview"
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.NoWrap
                                font.family: "Consolas"
                                font.pixelSize: 12
                                Accessible.name: "Slack App manifest 本地预览"
                            }
                        }
                        Label {
                            Layout.fillWidth: true
                            text: "预览使用下方所选的权限；导入前请核对。配置会启用 PKCE，Slack 提示该设置不能自行关闭。是否能连接仍需完成授权验证。"
                            color: root.mutedInk
                            font.pixelSize: 12
                            lineHeight: 1.35
                            wrapMode: Text.WordWrap
                        }
                        FieldLabel { text: "应用创建后，再填写以下信息" }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.narrow ? 1 : 2
                            columnSpacing: 12
                            rowSpacing: 7

                            FieldLabel { text: "OAuth Client ID" }
                            InputField {
                                id: slackClientId
                                objectName: "connectorSlackClientId"
                                Layout.fillWidth: true
                                placeholderText: "Slack App Client ID"
                                selectByMouse: true
                                Accessible.name: "Slack OAuth Client ID"
                            }

                            FieldLabel { text: "Redirect URI" }
                            InputField {
                                id: slackRedirectUri
                                objectName: "connectorSlackRedirectUri"
                                Layout.fillWidth: true
                                text: "http://127.0.0.1:53682/oauth/callback"
                                placeholderText: "已在 Slack App 注册的回调 URI"
                                selectByMouse: true
                                Accessible.name: "Slack OAuth Redirect URI"
                            }

                            FieldLabel { text: "Socket Mode xapp Token" }
                            InputField {
                                id: slackXappToken
                                objectName: "connectorSlackXappToken"
                                Layout.fillWidth: true
                                placeholderText: "xapp-…"
                                echoMode: TextInput.Password
                                passwordCharacter: "●"
                                selectByMouse: true
                                Accessible.name: "Slack Socket Mode Token"
                            }

                            FieldLabel { text: "当前用户 ID" }
                            InputField {
                                id: slackCurrentUserId
                                objectName: "connectorSlackCurrentUserId"
                                Layout.fillWidth: true
                                placeholderText: "例如 U0123456789"
                                selectByMouse: true
                                Accessible.name: "Slack 当前用户 ID"
                            }

                            FieldLabel { text: "精选频道" }
                            InputField {
                                id: slackChannels
                                objectName: "connectorSlackChannels"
                                Layout.fillWidth: true
                                placeholderText: "频道 ID，用逗号分隔"
                                selectByMouse: true
                                Accessible.name: "Slack 精选频道"
                            }
                        }

                        Label {
                            Layout.fillWidth: true
                            text: "回调地址须与 Slack 中登记的地址完全一致。本版只接收带固定端口的 http://127.0.0.1 本机回调，不支持自定义 URI。"
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

                    }

                    PaperCard {
                        visible: root.settingsPage
                        heading: "四项独立权限"
                        caption: "改变其中一项不会暗中升级其他项；共鸣成长也不会改动这里。"

                        GridLayout {
                            Layout.fillWidth: true
                            columns: width >= 700 ? 2 : 1
                            columnSpacing: 14
                            rowSpacing: 12

                            PolicyAxis {
                                id: scopeBox
                                controlName: "connectorPolicyScope"
                                Layout.fillWidth: true
                                label: "信息范围"
                                explanation: "必要：主日历／私信与本人提及；精选：只保留你勾选的来源；广泛：授权范围内全部来源。"
                                model: ["必要", "精选", "广泛"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: interruptionBox
                                controlName: "connectorPolicyInterruption"
                                Layout.fillWidth: true
                                label: "打扰方式"
                                explanation: "安静会排队；优先使用系统通知；即时会在非敏感场景显示气泡。"
                                model: ["安静", "优先", "即时"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: retentionBox
                                controlName: "connectorPolicyRetention"
                                Layout.fillWidth: true
                                label: "本地留存"
                                explanation: "元数据不存正文，也无法总结或拟稿。摘要与扩展缓存会加密；改变设置不会补回旧内容。"
                                model: ["元数据", "可搜索摘要", "扩展缓存"]
                                currentIndex: 0
                            }

                            PolicyAxis {
                                id: assistanceBox
                                controlName: "connectorPolicyAssistance"
                                Layout.fillWidth: true
                                label: "协助能力"
                                explanation: "提醒不调用模型；协助仅在点击后处理当前项；确认执行仍需逐次预览并手动确认。"
                                model: ["提醒", "协助", "确认执行"]
                                currentIndex: 1
                            }
                        }
                    }

                    PaperCard {
                        objectName: "connectorCalendarCreateCard"
                        visible: !root.settingsPage && !root.providerHasProposal && root.provider === "calendar" && root.creatingCalendar
                        heading: "准备新建日程"
                        caption: "填写内容只会生成一份创建预览；在最终预览中手动确认以前，Calendar 不会写入任何日程。"

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.narrow ? 1 : 2
                            columnSpacing: 12
                            rowSpacing: 8

                            FieldLabel {
                                text: "标题"
                                Layout.preferredWidth: 150
                            }
                            InputField {
                                id: calendarCreateTitle
                                Layout.fillWidth: true
                                placeholderText: "例如：项目复盘"
                                selectByMouse: true
                                Accessible.name: "新建日程标题"
                            }

                            FieldLabel { text: "开始时间（ISO 8601）" }
                            InputField {
                                id: calendarCreateStart
                                Layout.fillWidth: true
                                placeholderText: "2026-09-01T10:00:00+09:00"
                                selectByMouse: true
                                Accessible.name: "新建日程开始时间"
                            }

                            FieldLabel { text: "结束时间（ISO 8601）" }
                            InputField {
                                id: calendarCreateEnd
                                Layout.fillWidth: true
                                placeholderText: "2026-09-01T11:00:00+09:00"
                                selectByMouse: true
                                Accessible.name: "新建日程结束时间"
                            }

                            FieldLabel { text: "IANA 时区" }
                            InputField {
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
                                      : "需要在“连接与隐私”中开启“确认执行”并完成授权。"
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
                        objectName: "connectorCalendarItemCard"
                        visible: !root.settingsPage && !root.providerHasProposal && !root.creatingCalendar && !root.choosingItem
                                 && root.provider === "calendar" && root.selectedCalendarId !== ""
                        heading: "准备修改所选日程"
                        caption: "可以准备标题或时间的修改，最终确认后才提交。"

                        Label {
                            Layout.fillWidth: true
                            text: "所选日程：" + (root.calendarTitle(root.selectedCalendarItem) || root.selectedCalendarId)
                            color: root.ink
                            font.pixelSize: 11
                            elide: Text.ElideRight
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.narrow ? 1 : 2
                            columnSpacing: 12
                            rowSpacing: 8

                            FieldLabel {
                                text: "标题"
                                Layout.preferredWidth: 150
                            }
                            InputField {
                                id: calendarUpdateTitle
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程标题"
                            }

                            FieldLabel { text: "开始时间（ISO 8601）" }
                            InputField {
                                id: calendarUpdateStart
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程开始时间"
                            }

                            FieldLabel { text: "结束时间（ISO 8601）" }
                            InputField {
                                id: calendarUpdateEnd
                                Layout.fillWidth: true
                                placeholderText: "保留为空则不修改"
                                selectByMouse: true
                                Accessible.name: "修改日程结束时间"
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            InputField {
                                id: calendarAssistInstruction
                                Layout.fillWidth: true
                                placeholderText: "可选：请莉莉丝给出修改建议（不会自动写入）"
                                selectByMouse: true
                                Accessible.name: "日程协助说明"
                            }
                            QuietButton {
                                objectName: "connectorCalendarAssistButton"
                                text: Boolean(root.connectorAssistResult.busy)
                                      && root.pendingAssistProvider === "calendar" ? "协助中…" : "协助"
                                enabled: root.canAssistItem(root.selectedCalendarItem) && calendarAssistInstruction.text.trim() !== ""
                                         && !(Boolean(root.connectorAssistResult.busy)
                                              && root.pendingAssistProvider === "calendar")
                                onClicked: root.requestAssist("calendar", root.selectedCalendarId,
                                                              calendarAssistInstruction.text)
                            }
                        }

                        Label {
                            objectName: "connectorCalendarAssistExplanation"
                            Layout.fillWidth: true
                            text: root.assistExplanation
                            color: root.mutedInk
                            font.pixelSize: 13
                            lineHeight: 1.35
                            wrapMode: Text.WordWrap
                        }

                        NoteField {
                            visible: root.calendarAssistSuggestion !== ""
                            Layout.fillWidth: true
                            implicitHeight: Math.min(150, Math.max(72, contentHeight + 20))
                            text: root.calendarAssistSuggestion
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 13
                            Accessible.name: "日程协助建议"
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: root.mayConfirmExecute
                                      ? "请核对变更；未确认前不会写入 Calendar。"
                                      : "需要在“连接与隐私”中开启“确认执行”并完成授权。"
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
                        objectName: "connectorSlackItemCard"
                        visible: !root.settingsPage && !root.providerHasProposal && !root.choosingItem
                                 && root.provider === "slack" && root.selectedSlackId !== ""
                        heading: "准备回复所选 Slack 信笺"
                        caption: "回复原频道、私信或线程。先准备正文，最终确认后才发送。"

                        Label {
                            Layout.fillWidth: true
                            text: "原信笺 · " + root.selectedSlackId
                            color: root.mutedInk
                            font.pixelSize: 10
                            elide: Text.ElideRight
                        }

                        NoteField {
                            Layout.fillWidth: true
                            implicitHeight: Math.min(130, Math.max(64, contentHeight + 18))
                            text: String(root.selectedSlackItem.text || root.selectedSlackItem.summary
                                         || "当前留存档位没有保存原文")
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.mutedInk
                            font.pixelSize: 13
                            Accessible.name: "所选 Slack 信笺正文"
                        }

                        FieldLabel { text: "回复正文" }
                        NoteField {
                            id: slackReplyText
                            objectName: "connectorSlackReplyText"
                            Layout.fillWidth: true
                            implicitHeight: Math.min(190, Math.max(90, contentHeight + 20))
                            placeholderText: "输入最终回复正文"
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 13
                            Accessible.name: "Slack 回复草稿正文"
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            InputField {
                                id: slackAssistInstruction
                                objectName: "connectorSlackAssistInstruction"
                                Layout.fillWidth: true
                                placeholderText: "可选：说明希望怎样拟稿（不会自动发送）"
                                selectByMouse: true
                                Accessible.name: "Slack 回复协助说明"
                            }
                            QuietButton {
                                objectName: "connectorSlackAssistButton"
                                text: Boolean(root.connectorAssistResult.busy)
                                      && root.pendingAssistProvider === "slack" ? "协助中…" : "协助拟稿"
                                enabled: root.canAssistItem(root.selectedSlackItem) && slackAssistInstruction.text.trim() !== ""
                                         && !(Boolean(root.connectorAssistResult.busy)
                                              && root.pendingAssistProvider === "slack")
                                onClicked: root.requestAssist("slack", root.selectedSlackId,
                                                              slackAssistInstruction.text)
                            }
                        }

                        Label {
                            objectName: "connectorSlackAssistExplanation"
                            Layout.fillWidth: true
                            text: root.assistExplanation
                            color: root.mutedInk
                            font.pixelSize: 13
                            lineHeight: 1.35
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                Layout.fillWidth: true
                                text: root.mayConfirmExecute
                                      ? "生成后仍需核对最终正文并确认；未确认不会发送。"
                                      : "需要在“连接与隐私”中开启“确认执行”并完成授权。"
                                color: root.mayConfirmExecute ? root.mutedInk : root.cord
                                font.pixelSize: 10
                                wrapMode: Text.WordWrap
                            }
                            QuietButton {
                                objectName: "connectorSlackProposeButton"
                                text: "生成回复预览"
                                accent: true
                                enabled: root.mayConfirmExecute && slackReplyText.text.trim() !== ""
                                onClicked: root.proposeSlackReply()
                            }
                        }
                    }

                    PaperCard {
                        id: proposalCard
                        objectName: "connectorProposalCard"
                        visible: !root.settingsPage && root.providerHasProposal
                        heading: "最终操作预览"
                        caption: "下方内容在确认后才会提交。关闭窗口、拒绝或让提案过期都不会写入日历或发送 Slack 消息。"

                        NoteField {
                            Layout.fillWidth: true
                            implicitHeight: Math.min(270, Math.max(130, contentHeight + 24))
                            text: root.proposalText()
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.family: "Consolas"
                            font.pixelSize: 13
                            Accessible.name: "连接器操作最终预览"
                        }

                        FieldLabel {
                            visible: root.provider === "slack"
                            text: "可编辑的最终回复正文"
                        }

                        NoteField {
                            id: slackFinalText
                            visible: root.provider === "slack"
                            Layout.fillWidth: true
                            implicitHeight: Math.min(190, Math.max(90, contentHeight + 20))
                            text: root.providerHasProposal
                                  ? String((root.proposal.after || ({})).text || "") : ""
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: root.ink
                            font.pixelSize: 13
                            Accessible.name: "Slack 最终回复正文"
                        }

                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.narrow ? 1 : 4
                            columnSpacing: 8
                            rowSpacing: 8
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
                                objectName: "connectorRejectProposalButton"
                                text: "拒绝"
                                enabled: !root.proposalExecuting
                                onClicked: root.resolveProposal(false)
                            }
                            QuietButton {
                                objectName: "connectorConfirmProposalButton"
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

            GridLayout {
                visible: root.settingsPage
                Layout.fillWidth: true
                columns: root.narrow ? 2 : 3
                columnSpacing: 9
                rowSpacing: 8

                Label {
                    Layout.fillWidth: true
                    Layout.columnSpan: root.narrow ? 2 : 1
                    text: "清除或断开前会再次确认，不会删除外部日程和消息。"
                    color: root.mutedInk
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }

                QuietButton {
                    objectName: "connectorClearContentButton"
                    text: "清除本地内容"
                    onClicked: clearContentDialog.open()
                }

                QuietButton {
                    objectName: "connectorDisconnectButton"
                    text: "断开账号"
                    onClicked: disconnectDialog.open()
                }
            }

            GridLayout {
                visible: root.settingsPage
                Layout.fillWidth: true
                columns: root.narrow ? 2 : 3
                columnSpacing: 9
                rowSpacing: 8

                Label {
                    Layout.fillWidth: true
                    Layout.columnSpan: root.narrow ? 2 : 1
                    text: "令牌只存入 Windows 凭据管理器。"
                    color: root.mutedInk
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }

                QuietButton {
                    objectName: "connectorSaveConfigurationButton"
                    text: "保存配置"
                    onClicked: root.saveConfiguration(true)
                }

                QuietButton {
                    objectName: "connectorBeginOAuthButton"
                    text: root.currentInfo.connected ? "在浏览器中重新授权" : "在浏览器中连接"
                    accent: true
                    onClicked: root.beginOAuth()
                }
            }
        }
    }

    PaperDialog {
        id: clearContentDialog
        objectName: "connectorClearContentDialog"
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        width: Math.min(520, root.width - 48)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        title: "再次确认：清除本地内容"
        standardButtons: Dialog.Cancel | Dialog.Ok
        contentWidth: width - leftPadding - rightPadding
        contentItem: Column {
            Label {
                width: parent.width
                text: "将删除当前 " + (root.provider === "calendar" ? "Calendar" : "Slack")
                      + " 连接器在本机保存的正文、摘要与索引。不会删除外部服务中的内容，也不会断开账号。此本地清除无法撤销，是否继续？"
                color: root.ink
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
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

    PaperDialog {
        id: disconnectDialog
        objectName: "connectorDisconnectDialog"
        x: Math.round((root.width - width) / 2)
        y: Math.round((root.height - height) / 2)
        width: Math.min(540, root.width - 48)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        title: "再次确认：断开账号"
        standardButtons: Dialog.Cancel | Dialog.Ok
        contentWidth: width - leftPadding - rightPadding
        contentItem: Column {
            Label {
                width: parent.width
                text: root.provider === "slack"
                      ? "将移除 Slack 凭据、清除本地留存并停止接收。custom-app 配置保留，之后需要重新连接。不会发送或删除 Slack 消息。是否继续？"
                      : "将移除 Google 凭据、清除本地留存并停止同步。不会删除 Calendar 中的日程。之后需要重新授权才能连接，是否继续？"
                color: root.ink
                font.pixelSize: 12
                wrapMode: Text.WordWrap
            }
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
