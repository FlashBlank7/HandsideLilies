from __future__ import annotations

from lilies.core.database import Database
from lilies.core.desktop import DesktopIndex


def test_application_search_ranks_and_deduplicates_start_menu_shortcuts(tmp_path):
    database = Database(tmp_path / "lilies.db")
    for name, path, source, kind in (
        ("WPS Office", str(tmp_path / "desktop-wps.lnk"), "desktop", "application"),
        ("WPS Office", str(tmp_path / "menu-wps.lnk"), "start-menu", "application"),
        ("WPS Office 工具", str(tmp_path / "tools-wps.lnk"), "start-menu", "application"),
        ("WPS 使用说明", str(tmp_path / "wps-help.txt"), "desktop", "file"),
    ):
        database.upsert_desktop_item({"name": name, "path": path, "source": source, "kind": kind})

    found = DesktopIndex(database).applications("wps", refresh_on_miss=False)
    assert [item["name"] for item in found] == ["WPS Office", "WPS Office 工具"]
    assert found[0]["source"] == "start-menu"


def test_resource_search_accepts_an_explicit_existing_path(tmp_path):
    database = Database(tmp_path / "lilies.db")
    document = tmp_path / "研究报告.pdf"
    document.write_bytes(b"pdf")
    desktop = DesktopIndex(database)

    found = desktop.resources(str(document))

    assert len(found) == 1
    assert found[0]["name"] == "研究报告"
    assert found[0]["kind"] == "file"
    assert found[0]["source"] == "explicit"
