from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _app_source() -> str:
    return (PROJECT_ROOT / "src" / "lilies" / "app.py").read_text(
        encoding="utf-8"
    )


def test_packaged_self_test_captures_qml_warnings_before_loading_main_qml() -> None:
    source = _app_source()
    engine_created = source.index("engine = QQmlApplicationEngine()")
    warning_connected = source.index(
        "engine.warnings.connect(record_qml_warnings)", engine_created
    )
    qml_loaded = source.index("engine.load(QUrl.fromLocalFile", warning_connected)

    assert engine_created < warning_connected < qml_loaded
    assert "message not in qml_warning_messages" in source
    assert "message = message[:4000]" in source
    assert "len(qml_warning_messages) < 128" in source


def test_packaged_self_test_reports_and_gates_an_empty_qml_warning_set() -> None:
    source = _app_source()
    self_test = source[source.index("    if args.self_test:") :]

    assert self_test.count('"qmlWarningCount": qml_warning_count') >= 2
    assert self_test.count('"qmlWarnings": list(qml_warning_messages)') >= 2
    assert self_test.count('"qmlWarningsPassed": qml_warning_count == 0') >= 2
    assert 'and result["qmlWarningsPassed"]' in self_test
    assert 'app.exit(0 if result["passed"] else 1)' in self_test
    assert 'app.exit(1)' in self_test


def test_runtime_uses_the_customizable_controls_style_before_qapplication() -> None:
    source = _app_source()

    style = source.index(
        'os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"'
    )
    application = source.index("app = QApplication(", style)
    assert style < application


def test_known_runtime_warning_sources_are_explicitly_scoped_and_typed() -> None:
    main_qml = (PROJECT_ROOT / "qml" / "Main.qml").read_text(encoding="utf-8")
    pet_qml = (PROJECT_ROOT / "qml" / "V03PetBody.qml").read_text(
        encoding="utf-8"
    )

    selection_surface = main_qml[
        main_qml.index("id: selectionBubble") : main_qml.index(
            "id: selectionQuestion", main_qml.index("id: selectionBubble")
        )
    ]
    assert "selectionBubble.bubbleData" in selection_surface
    assert "color: bubbleData" not in selection_surface
    assert "Boolean(bubbleData" not in selection_surface
    assert "String(bubbleData" not in selection_surface

    frequency_control = main_qml[
        main_qml.index("id: companionFrequency") : main_qml.index(
            "onActivated:", main_qml.index("id: companionFrequency")
        )
    ]
    assert "initializeFrequencyDraft()" not in frequency_control
    assert "implicitWidth: 540" in main_qml
    assert pet_qml.count("function contains(point: point): bool") == 3
