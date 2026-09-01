# -*- coding: utf-8 -*-
"""Collect only the QML modules imported by Lilies.

PyInstaller's stock QtQml hook recursively collects every QML plugin shipped
in the PySide6 wheel.  On Windows that pulled in Qt WebEngine, Quick3D, Charts,
PDF, Location and their transitive DLLs even though Lilies imports none of
them; Qt6WebEngineCore.dll alone is over 190 MiB.  Keep the normal QtQml Python
dependencies, but constrain plugin discovery to the import closure reported by
``qmlimportscanner`` for this application's QML tree.

The list is intentionally explicit and release-tested.  Adding a new QML
import requires updating this hook, after which the packaged self-test catches
any missing runtime dependency before installation.
"""

from pathlib import Path, PurePath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)


_REQUIRED_QML_MODULES = (
    "QtQuick",
    "QtQml",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtMultimedia",
    "QtQuick/Controls",
    "QtQuick/Controls/Fusion",
    "QtQuick/Controls/Imagine",
    "QtQuick/Controls/Material",
    "QtQuick/Controls/Universal",
    "QtQuick/Controls/FluentWinUI3",
    "QtQuick/Controls/Windows",
    "QtQuick/Controls/Basic",
    "QtQuick/Templates",
    "QtQuick/Controls/impl",
    "QtQuick/Controls/Fusion/impl",
    "QtQuick/Window",
    "QtQuick/Controls/Imagine/impl",
    "QtQuick/Controls/Material/impl",
    "QtQuick/Controls/Universal/impl",
    "QtQuick/Controls/FluentWinUI3/impl",
    "QtQuick/Effects",
    "QtQuick/Layouts",
    "QtQuick/Shapes",
    "QtQuick/NativeStyle",
    "QtQuick/Controls/Windows/impl",
    "QtQuick/Controls/Basic/impl",
    "QtQuick/Dialogs",
    "QtQuick/Dialogs/quickimpl",
    "Qt/labs/folderlistmodel",
)


def _qml_import_root() -> Path:
    locations = pyside6_library_info.location
    raw = locations.get("QmlImportsPath") or locations.get("Qml2ImportsPath")
    if not raw:
        raise RuntimeError("PySide6 did not report a QML imports directory")
    root = Path(raw).resolve()
    if not root.is_dir():
        raise RuntimeError(f"PySide6 QML imports directory is missing: {root}")
    return root


def _collect_required_qml_modules() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    source_root = _qml_import_root()
    destination_root = PurePath(pyside6_library_info.qt_rel_dir) / "qml"
    selected_binaries: dict[tuple[str, str], None] = {}
    selected_datas: dict[tuple[str, str], None] = {}

    def destination(source: Path) -> str:
        relative = source.relative_to(source_root)
        if source.is_file():
            relative = relative.parent
        return str(destination_root / relative)

    for relative_module in _REQUIRED_QML_MODULES:
        qmldir = source_root / PurePath(relative_module) / "qmldir"
        if not qmldir.is_file():
            raise RuntimeError(f"Required QML module is missing: {relative_module}")
        plugin_binaries, plugin_datas = pyside6_library_info._process_qml_plugin(
            qmldir
        )
        if not plugin_binaries and not plugin_datas:
            raise RuntimeError(
                f"Required QML module could not be collected: {relative_module}"
            )
        for source in plugin_binaries:
            item = Path(source).resolve()
            selected_binaries[(str(item), destination(item))] = None
        for source in plugin_datas:
            item = Path(source).resolve()
            selected_datas[(str(item), destination(item))] = None

    return list(selected_binaries), list(selected_datas)


qml_binaries, qml_datas = _collect_required_qml_modules()
binaries += qml_binaries
datas += qml_datas

