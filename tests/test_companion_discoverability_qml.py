from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _main_qml() -> str:
    return (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")


def test_unread_companion_has_a_persistent_pet_level_recovery_target() -> None:
    main_source = _main_qml()
    app_source = (
        PROJECT_ROOT / "src" / "lilies" / "app.py"
    ).read_text(encoding="utf-8")

    assert 'objectName: "desktopPetCompanionUnreadCue"' in main_source
    assert "backend.companionService.reopenUnread()" in main_source
    assert '"companion-unread", true' in main_source
    assert 'name == "desktopPetCompanionUnreadCue"' in app_source


def test_companion_page_exposes_two_distinct_modes_and_safe_manual_try() -> None:
    source = _main_qml()
    assert 'objectName: "companionRunModeLabel"' in source
    assert 'text: "主动陪伴运行模式"' in source
    assert 'objectName: "companionSmartObservationStatusLabel"' in source
    assert 'text: "智能屏幕理解"' in source
    assert '? "已授权" : "未授权"' in source
    assert '"生成一条场景陪伴"' in source
    assert "这是只使用当前应用类别的场景级生成" in source
    assert "不截图，也不是截图能力测试" in source
    assert "仍会遵守暂停与敏感窗口静默" in source

    authorize_start = source.index(
        'objectName: "companionSmartObservationAuthorizeButton"'
    )
    authorize_block = source[authorize_start : authorize_start + 500]
    assert "visible: !Boolean(" in authorize_block
    assert "smartObservationEnabled" in authorize_block
    assert "onClicked: smartObservationConfirm.open()" in authorize_block
    assert "authorizeSmartObservation(true)" not in authorize_block


def test_one_shot_screen_observation_hides_chat_before_guarded_dispatch() -> None:
    source = _main_qml()
    button_start = source.index(
        'objectName: "companionRequestScreenNowButton"'
    )
    button_block = source[button_start : button_start + 1100]
    assert 'text: "观察当前窗口一次"' in button_block
    assert "activityStatus.smartObservationEnabled" in button_block
    assert "&& !backend.companionService.busy" in button_block
    assert "activityStatus.modalityProbeBusy" in button_block
    assert "onClicked: desktop.requestCurrentWindowObservation()" in button_block

    function_start = source.index("function requestCurrentWindowObservation()")
    function_block = source[function_start : function_start + 420]
    close_index = function_block.index("backend.setChatOpen(false)")
    delay_index = function_block.index("companionScreenObservationDelay.restart()")
    assert close_index < delay_index
    assert "requestNow()" not in function_block
    assert "requestScreenNow()" not in function_block

    timer_start = source.index("id: companionScreenObservationDelay")
    timer_block = source[timer_start : timer_start + 360]
    assert 'objectName: "companionScreenObservationDelay"' in timer_block
    assert "interval: 350" in timer_block
    assert "repeat: false" in timer_block
    assert "onTriggered: backend.companionService.requestScreenNow()" in timer_block
    assert "requestNow()" not in timer_block

    chat_start = source.index('objectName: "chatWindow"')
    chat_block = source[chat_start : chat_start + 1500]
    visible_handler = chat_block.index("onVisibleChanged:")
    cancellation = chat_block.index("companionScreenObservationDelay.stop()")
    assert visible_handler < cancellation
    assert "visible && companionScreenObservationDelay.running" in chat_block

    request_now_start = source.index('objectName: "companionRequestNowButton"')
    request_now_block = source[request_now_start : request_now_start + 600]
    assert "onClicked: backend.companionService.requestNow()" in request_now_block


def test_one_shot_screen_observation_copy_promises_no_generic_fallback() -> None:
    source = _main_qml()
    assert "只截取一次当前的非浏览器活动窗口" in source
    assert "就直接失败，绝不退回泛化文字" in source
    assert "约 350 毫秒后再观察，让原应用恢复前台" in source
    assert "单次窗口观察会直接失败，不生成泛化文字" in source


def test_malformed_bubble_uses_an_explicit_failure_not_a_canned_character_line() -> None:
    source = (PROJECT_ROOT / "qml" / "CompanionBubble.qml").read_text(
        encoding="utf-8"
    )
    assert "这次没有生成可显示的内容。" in source
    assert "我在这里，想和你说句话。" not in source


def test_companion_page_labels_earliest_time_as_a_non_guaranteed_estimate() -> None:
    source = _main_qml()
    assert 'objectName: "companionEarliestAutomaticLabel"' in source
    assert "activityStatus.automaticOpportunity" in source
    assert 'return "最早约 "' in source
    assert '+ " 后可再自动出现"' in source
    assert "不承诺届时一定触发" in source
    assert "自然停顿和非敏感窗口" in source


def test_smart_observation_copy_distinguishes_automatic_from_text_only_manual() -> None:
    source = _main_qml()
    assert "自动陪伴在窗口稳定、自然停顿且本地隐私规则允许时" in source
    assert "手动“生成一条场景陪伴”只使用应用类别，不截图" in source
    assert "浏览器像素观察在 v0.3.36 暂不开放" in source
    assert "规则识别仍有边界" in source


def test_modality_recheck_is_disabled_while_generation_or_probe_is_busy() -> None:
    source = _main_qml()
    start = source.index('objectName: "companionModalityRecheckButton"')
    block = source[start : start + 700]
    assert "!Boolean(backend.companionService.activityStatus.modalityProbeBusy)" in block
    assert "!backend.companionService.busy" in block


def test_capture_status_displays_a_human_readable_attempt_time() -> None:
    source = _main_qml()
    assert "function formatCaptureTime(value)" in source
    assert 'Qt.formatDateTime(parsed, "MM-dd HH:mm")' in source
    assert '" · 发生于 "' in source
    assert "status.lastCaptureAt" in source


def test_capture_status_explains_whether_pixels_were_really_used() -> None:
    source = _main_qml()
    assert "status.lastCapturePixelsUsed" in source
    assert 'proof = " · 像素已用于生成"' in source
    assert 'proof = " · 像素已提交但未采用"' in source
    assert "status.lastCaptureModelLabel" in source
    assert "status.lastCaptureEvidenceConfidence" in source
    assert "if (Boolean(status.lastCapturePixelsUsed))" in source
    assert "confidenceLabels[confidence]" in source


def test_capture_status_uses_backend_labels_and_exposes_final_presentation() -> None:
    source = _main_qml()
    assert "var reasonLabels" not in source
    assert "status.lastCaptureReasonLabel" in source
    assert 'objectName: "companionLastCapturePresentationStatusLabel"' in source
    assert "status.lastCapturePresentationLabel" in source
    assert "status.lastCapturePresentationReasonLabel" in source

    status_start = source.index('objectName: "companionLastCaptureStatusLabel"')
    status_block = source[status_start : status_start + 4300]
    assert "Layout.minimumWidth: 0" in status_block
