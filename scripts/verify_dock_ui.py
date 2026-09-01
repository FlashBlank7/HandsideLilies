from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# This verifier must never attach to the real Windows desktop. Set the
# platform before importing Qt and drive V03Dock.qml with a tiny QObject mock.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QSG_RHI_BACKEND", "software")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import QObject, QPoint, QPointF, Property, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QJSValue, QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QML_FILE = PROJECT_ROOT / "qml" / "V03Dock.qml"
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts" / "dock-stress"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


class MockDockBackend(QObject):
    windowGroupsChanged = Signal()
    windowItemsChanged = Signal()
    desktopItemsChanged = Signal()
    shellModeChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._groups: list[dict[str, Any]] = []
        self._launch_items: list[dict[str, Any]] = []
        self.activated: list[int] = []
        self.opened: list[str] = []
        self.open_result = True
        self.activation_result = True
        self.refresh_count = 0

    @Property("QVariantList", notify=windowGroupsChanged)
    def windowGroups(self) -> list[dict[str, Any]]:
        return list(self._groups)

    @Property("QVariantList", notify=windowItemsChanged)
    def windowItems(self) -> list[dict[str, Any]]:
        return []

    @Property("QVariantList", notify=desktopItemsChanged)
    def dockLaunchItems(self) -> list[dict[str, Any]]:
        return list(self._launch_items)

    @Property("QVariantList", notify=desktopItemsChanged)
    def desktopItems(self) -> list[dict[str, Any]]:
        return list(self._launch_items)

    @Property("QVariantList", notify=desktopItemsChanged)
    def pinnedItems(self) -> list[dict[str, Any]]:
        return [item for item in self._launch_items if item.get("pinned")]

    @Property(str, notify=shellModeChanged)
    def shellMode(self) -> str:
        return "visual"

    def set_groups(self, groups: list[dict[str, Any]]) -> None:
        self._groups = list(groups)
        self.windowGroupsChanged.emit()

    def set_launch_items(self, items: list[dict[str, Any]]) -> None:
        self._launch_items = list(items)
        self.desktopItemsChanged.emit()

    @Slot(int, result=bool)
    def activateWindow(self, handle: int) -> bool:
        self.activated.append(int(handle))
        return self.activation_result

    @Slot(str, result=bool)
    def openItem(self, item_id: str) -> bool:
        self.opened.append(str(item_id))
        return self.open_result

    @Slot()
    def refreshWindows(self) -> None:
        self.refresh_count += 1


APP_NAMES = (
    "WPS Office",
    "Typora",
    "Visual Studio Code",
    "Microsoft Edge",
    "Zotero",
    "文件资源管理器",
    "微信",
    "PowerPoint",
    "Excel",
    "Obsidian",
    "终端",
)


def make_groups(counts: list[int]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    handle = 10_000
    for group_index, count in enumerate(counts):
        app_id = f"app{group_index + 1:02d}"
        windows: list[dict[str, Any]] = []
        for window_index in range(count):
            is_active = group_index == 0 and window_index == 0
            title = (
                f"论文 {window_index + 1:02d} · 细胞命运与生成模型"
                if group_index == 0
                else f"{APP_NAMES[group_index % len(APP_NAMES)]} · 窗口 {window_index + 1:02d}"
            )
            windows.append(
                {
                    "handle": handle,
                    "title": title,
                    "active": is_active,
                    "minimized": not is_active and (window_index + group_index) % 4 == 0,
                    "monitorId": "主屏" if window_index % 3 == 0 else "",
                }
            )
            handle += 1
        groups.append(
            {
                "appId": app_id,
                "displayName": APP_NAMES[group_index % len(APP_NAMES)],
                "active": group_index == 0,
                "minimized": all(bool(window["minimized"]) for window in windows),
                "windowCount": count,
                "windows": windows,
            }
        )
    return groups


SCENARIOS: dict[str, list[int]] = {
    "1-window": [1],
    "4-windows": [2, 1, 1],
    # Eight application groups prove that the seven-slot Dock overflows by
    # group, not by individual window.
    "12-windows": [5, 1, 1, 1, 1, 1, 1, 1],
    # Eleven groups / fifty windows exercise both ListView virtualization and
    # multi-window grouping without creating any native host windows.
    "50-windows": [8, 5, 5, 5, 5, 4, 4, 4, 4, 3, 3],
}


def load_windows_ui_fonts(app: QApplication) -> dict[str, Any]:
    fonts_root = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    loaded: list[str] = []
    families: list[str] = []
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        candidate = fonts_root / filename
        if not candidate.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(candidate))
        if font_id < 0:
            continue
        loaded.append(str(candidate))
        families.extend(QFontDatabase.applicationFontFamilies(font_id))
    preferred = next(
        (family for family in families if "YaHei UI" in family),
        next((family for family in families if "YaHei" in family), "Microsoft YaHei UI"),
    )
    font = QFont(preferred)
    font.setPointSizeF(10.0)
    app.setFont(font)
    return {"loaded": loaded, "family": app.font().family()}


def component_errors(component: QQmlComponent) -> str:
    return "\n".join(error.toString() for error in component.errors())


def settle(app: QApplication, milliseconds: int = 70) -> None:
    app.processEvents()
    QTest.qWait(milliseconds)
    app.processEvents()


def qml_variant(value: Any) -> Any:
    return value.toVariant() if isinstance(value, QJSValue) else value


def visual_descendants(item: QQuickItem) -> list[QQuickItem]:
    result: list[QQuickItem] = []
    pending = list(item.childItems())
    while pending:
        child = pending.pop()
        result.append(child)
        pending.extend(child.childItems())
    return result


def find_visual(window: QQuickWindow, name: str) -> QQuickItem:
    for item in visual_descendants(window.contentItem()):
        if item.objectName() == name:
            return item
    raise RuntimeError(f"missing QML item: {name}")


def visible_named_count(window: QQuickWindow, prefix: str) -> int:
    return sum(
        item.isVisible() and item.objectName().startswith(prefix)
        for item in visual_descendants(window.contentItem())
    )


def click(window: QQuickWindow, item: QQuickItem) -> None:
    center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(
        window,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(round(center.x()), round(center.y())),
    )


def stable_text(item: QQuickItem) -> str:
    value = str(item.property("text") or "")
    if "�" in value:
        raise RuntimeError(f"replacement glyph leaked into Dock label: {value!r}")
    return value


def main() -> int:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    font_report = load_windows_ui_fonts(app)
    backend = MockDockBackend()
    engine = QQmlEngine()
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_FILE)))
    if component.status() == QQmlComponent.Status.Error:
        raise RuntimeError(component_errors(component))
    root = component.createWithInitialProperties(
        {"appBackend": backend, "suppressed": False}
    )
    if root is None:
        raise RuntimeError(component_errors(component) or "V03Dock.qml did not load")

    dock = root.findChild(QQuickWindow, "v03PaperFoldDock")
    preview = root.findChild(QQuickWindow, "v03WindowPreviewShelf")
    drawer = root.findChild(QQuickWindow, "v03AllWindowsDrawer")
    if dock is None or preview is None or drawer is None:
        raise RuntimeError("grouped Dock windows are incomplete")

    results: dict[str, Any] = {"fonts": font_report, "scenarios": {}}
    all_passed = True

    for scenario_name, counts in SCENARIOS.items():
        groups = make_groups(counts)
        total_windows = sum(counts)
        backend.set_launch_items([])
        backend.set_groups(groups)
        root.setProperty("previewOpen", False)
        root.setProperty("drawerOpen", False)
        dock.setProperty("raised", True)
        settle(app, 240)

        dock_buttons = visible_named_count(dock, "v03DockGroupButton_")
        overflow_button = find_visual(dock, "v03DockOverflowButton")
        overflow_visible = overflow_button.isVisible()
        overflow_text = stable_text(overflow_button)
        expected_overflow = max(0, len(groups) - 7)

        first_group_button = find_visual(dock, "v03DockGroupButton_app01")
        activated_before = len(backend.activated)
        click(dock, first_group_button)
        settle(app)
        preview_visible = preview.isVisible()
        preview_cards = int(root.property("previewVisibleCardCount") or 0)
        preview_total = int(root.property("previewTotalCardCount") or 0)
        direct_activation = backend.activated[activated_before:]

        if counts[0] > 1:
            preview_title = stable_text(find_visual(preview, "v03PreviewTitle"))
            preview_count_label = stable_text(find_visual(preview, "v03PreviewCount"))
        else:
            preview_title = ""
            preview_count_label = ""

        # Opening the all-windows drawer must close the MRU shelf.
        click(dock, find_visual(dock, "v03DockSearchButton"))
        settle(app, 190)
        drawer_visible = drawer.isVisible()
        preview_closed_for_drawer = not preview.isVisible()
        drawer_title_item = find_visual(drawer, "v03DrawerTitle")
        drawer_title = stable_text(drawer_title_item)
        drawer_rows = int(root.property("drawerRowCount") or 0)
        instantiated_rows = visible_named_count(drawer, "v03DrawerRow_")
        drawer_height = float(drawer.height())
        drawer_screen_cap = float(drawer.screen().availableGeometry().height() - 80)

        # Search for one title in the first group. Only that window and its
        # inline group header should remain in the flat row model.
        search = find_visual(drawer, "v03WindowSearch")
        search.setProperty("text", "论文 01")
        settle(app, 190)
        search_group_count = int(root.property("drawerFilteredGroupCount") or 0)
        search_window_count = int(root.property("drawerFilteredWindowCount") or 0)
        search_row_count = int(root.property("drawerRowCount") or 0)
        search_title = stable_text(drawer_title_item)
        search_height = float(drawer.height())
        search.setProperty("text", "")
        settle(app, 170)

        screenshot = ARTIFACT_ROOT / f"{scenario_name}.png"
        drawer.grabWindow().save(str(screenshot))

        checks = {
            "windowCountExact": total_windows
            == sum(len(group["windows"]) for group in groups),
            "dockShowsAtMostSevenGroups": dock_buttons == min(7, len(groups)),
            "overflowVisibility": overflow_visible == (expected_overflow > 0),
            "overflowCountsGroups": overflow_text
            == (f"+{expected_overflow}" if expected_overflow else "+0"),
            "singleWindowActivatesDirectly": counts[0] != 1
            or direct_activation == [int(groups[0]["windows"][0]["handle"])],
            "multiWindowUsesPreview": counts[0] == 1
            or (
                preview_visible
                and preview_cards == min(6, counts[0])
                and preview_total == counts[0]
                and preview_title == "WPS Office"
                and preview_count_label == f"{min(6, counts[0])} / {counts[0]}"
            ),
            "oneDrawerNoNestedPreview": drawer_visible and preview_closed_for_drawer,
            "drawerRowsAreFlat": drawer_rows == len(groups) + total_windows,
            "drawerHeightIsBounded": 300 <= drawer_height <= drawer_screen_cap + 1,
            "searchFiltersToOneInlinePair": search_group_count == 1
            and search_window_count == 1
            and search_row_count == 2,
            "searchHeightContracts": 300 <= search_height <= drawer_height,
            "labelsAreStable": drawer_title
            == f"全部窗口 · {len(groups)} 个应用组"
            and search_title == "搜索结果 · 1 个应用组",
            "largeDrawerIsVirtualized": total_windows < 50
            or instantiated_rows < drawer_rows,
        }
        scenario_passed = all(checks.values())
        all_passed = all_passed and scenario_passed
        results["scenarios"][scenario_name] = {
            "groupCount": len(groups),
            "windowCount": total_windows,
            "dockButtonCount": dock_buttons,
            "overflowGroupCount": expected_overflow,
            "overflowLabel": overflow_text,
            "preview": {
                "visibleBeforeDrawer": preview_visible,
                "visibleCards": preview_cards,
                "totalCards": preview_total,
                "title": preview_title,
                "countLabel": preview_count_label,
            },
            "drawer": {
                "visible": drawer_visible,
                "height": drawer_height,
                "screenCap": drawer_screen_cap,
                "rowCount": drawer_rows,
                "instantiatedRows": instantiated_rows,
                "title": drawer_title,
                "searchRowCount": search_row_count,
                "searchHeight": search_height,
                "searchTitle": search_title,
            },
            "checks": checks,
            "passed": scenario_passed,
            "screenshot": str(screenshot),
        }

    # A stale HWND must not dismiss the only place where the user can choose
    # another window.  Production Backend.activateWindow publishes the reason
    # through its status signal; this QML verifier checks the Dock-side state
    # contract without touching the real Windows catalogue.
    stale_groups = make_groups([1])
    stale_handle = int(stale_groups[0]["windows"][0]["handle"])
    backend.set_groups(stale_groups)
    backend.activation_result = False
    root.setProperty("previewOpen", False)
    root.setProperty("drawerOpen", True)
    settle(app, 220)
    stale_row = find_visual(
        drawer, f"v03DrawerRow_window_app01_{stale_handle}"
    )
    activated_before = len(backend.activated)
    click(drawer, stale_row)
    settle(app, 120)
    stale_activation = {
        "attemptedHandle": backend.activated[activated_before:]
        == [stale_handle],
        "drawerPreserved": bool(root.property("drawerOpen"))
        and drawer.isVisible(),
    }
    stale_activation["passed"] = all(stale_activation.values())
    results["staleActivation"] = stale_activation
    all_passed = all_passed and bool(stale_activation["passed"])
    backend.activation_result = True
    root.setProperty("drawerOpen", False)

    launch_groups = make_groups([1, 1, 1])
    launch_groups[1]["appId"] = "exe:word"
    launch_groups[1]["displayName"] = "Word"
    launch_groups[1]["windows"][0]["title"] = "Word · Draft"
    launch_items = [
        {
            "itemId": "wps-shortcut",
            "name": "WPS Office",
            "path": r"C:\ProgramData\Microsoft\Windows\Start Menu\WPS Office.lnk",
            "kind": "application",
            "pinned": True,
            "glyph": "W",
        },
        {
            "itemId": "vscode-shortcut",
            "name": "Visual Studio Code",
            "path": r"C:\ProgramData\Microsoft\Windows\Start Menu\Visual Studio Code.lnk",
            "kind": "application",
            "pinned": True,
            "glyph": "V",
        },
        {
            "itemId": "wordpad-shortcut",
            "name": "WordPad",
            "path": r"C:\ProgramData\Microsoft\Windows\Start Menu\WordPad.lnk",
            "kind": "application",
            "pinned": True,
            "glyph": "W",
        },
        {
            "itemId": "paper-library",
            "name": "论文资料",
            "path": r"C:\Users\User\Documents\论文资料",
            "kind": "folder",
            "pinned": True,
            "glyph": "▱",
        },
        {
            "itemId": "research-report",
            "name": "研究报告.pdf",
            "path": r"C:\Users\User\Documents\研究报告.pdf",
            "kind": "file",
            "pinned": False,
            "glyph": "□",
        },
    ]
    backend.set_groups(launch_groups)
    backend.set_launch_items(launch_items)
    root.setProperty("previewOpen", False)
    root.setProperty("drawerOpen", False)
    dock.setProperty("raised", True)
    settle(app, 260)

    merged_dock_count = int(root.property("visibleDockGroupCount") or 0)
    merged_groups = qml_variant(root.property("groups")) or []
    drawer_groups = qml_variant(root.property("drawerGroups")) or []
    drawer_group_count = len(drawer_groups)
    merged_names = [str(value.get("displayName", "")) for value in merged_groups]
    folder_button = find_visual(dock, "v03DockGroupButton_launch:paper-library")
    click(dock, folder_button)
    settle(app)
    folder_opened = backend.opened[-1:] == ["paper-library"]

    dock.setProperty("raised", True)
    click(dock, find_visual(dock, "v03DockSearchButton"))
    settle(app, 190)
    search = find_visual(drawer, "v03WindowSearch")
    search.setProperty("text", "研究报告")
    settle(app, 190)
    launch_search_count = int(root.property("drawerFilteredLaunchCount") or 0)
    report_row = find_visual(drawer, "v03DrawerRow_group_launch:research-report")
    click(drawer, report_row)
    settle(app)
    report_opened = backend.opened[-1:] == ["research-report"]

    backend.open_result = False
    dock.setProperty("raised", True)
    click(dock, find_visual(dock, "v03DockSearchButton"))
    settle(app, 190)
    search.setProperty("text", "研究报告")
    settle(app, 190)
    click(drawer, find_visual(drawer, "v03DrawerRow_group_launch:research-report"))
    settle(app)
    failed_launch_preserved_drawer = bool(root.property("drawerOpen"))
    backend.open_result = True

    root.setProperty("drawerOpen", False)
    root.setProperty("previewOpen", False)
    dock.setProperty("raised", False)
    settle(app, 260)
    paper_surface = find_visual(dock, "v03DockPaperSurface")
    screen_geometry = dock.screen().geometry()
    collapsed_geometry = {
        "width": float(dock.width()),
        "height": float(dock.height()),
        "paperWidth": float(paper_surface.width()),
        "paperHeight": float(paper_surface.height()),
        "bottom": float(dock.y() + dock.height()),
        "screenBottom": float(screen_geometry.y() + screen_geometry.height()),
        "bottomGap": float(
            screen_geometry.y() + screen_geometry.height() - (dock.y() + dock.height())
        ),
    }
    root.setProperty("suppressed", True)
    settle(app)
    suppressed_hidden = not dock.isVisible()
    root.setProperty("suppressed", False)
    settle(app)

    launch_checks = {
        "runningAndPinnedApplicationDeduplicated": merged_dock_count == 5,
        "drawerIncludesUnpinnedLaunchableItem": drawer_group_count == 6,
        "wordAndWordPadStaySeparate": merged_names.count("Word") == 1
        and merged_names.count("WordPad") == 1,
        "wpsAndVsCodeDeduplicate": merged_names.count("WPS Office") == 1
        and merged_names.count("Visual Studio Code") == 1,
        "pinnedFolderClickInvokesOpenItem": folder_opened,
        "drawerSearchFindsLaunchableFile": launch_search_count == 1,
        "drawerFileClickInvokesOpenItem": report_opened,
        "failedLaunchKeepsDrawerVisible": failed_launch_preserved_drawer,
        "collapsedNativeHitTargetIs64By16": collapsed_geometry["width"] == 64
        and collapsed_geometry["height"] == 16,
        "collapsedPaperRemains64By6": collapsed_geometry["paperWidth"] == 64
        and collapsed_geometry["paperHeight"] == 6,
        "collapsedWindowTouchesScreenBottom": abs(
            collapsed_geometry["bottom"] - collapsed_geometry["screenBottom"]
        ) <= 0.5,
        "suppressionHidesWholeNativeWindow": suppressed_hidden,
    }
    launch_passed = all(launch_checks.values())
    all_passed = all_passed and launch_passed
    results["launchScenario"] = {
        "mergedDockCount": merged_dock_count,
        "drawerGroupCount": drawer_group_count,
        "mergedNames": merged_names,
        "searchLaunchCount": launch_search_count,
        "opened": list(backend.opened),
        "collapsedGeometry": collapsed_geometry,
        "checks": launch_checks,
        "passed": launch_passed,
    }

    report_path = ARTIFACT_ROOT / "verification.json"
    report_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))

    root.deleteLater()
    settle(app)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
