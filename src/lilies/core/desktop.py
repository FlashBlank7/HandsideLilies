from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .database import Database


SUPPORTED_EXTENSIONS = {".lnk", ".url", ".exe", ".appref-ms"}


def _existing_unique(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if resolved.exists() and key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


class DesktopIndex:
    def __init__(self, database: Database) -> None:
        self.database = database

    def roots(self) -> list[tuple[str, Path]]:
        appdata = Path(os.environ.get("APPDATA", ""))
        programdata = Path(os.environ.get("PROGRAMDATA", ""))
        user = Path.home()
        candidates = [
            ("desktop", user / "Desktop"),
            ("desktop", user / "OneDrive" / "Desktop"),
            ("desktop", user / "OneDrive" / "桌面"),
            ("start-menu", appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"),
            ("start-menu", programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"),
            ("documents", user / "Documents"),
            ("documents", user / "OneDrive" / "Documents"),
            ("documents", user / "OneDrive" / "文档"),
            ("downloads", user / "Downloads"),
        ]
        extra = [Path(value) for value in self.database.get_setting("desktop_extra_roots", [])]
        candidates.extend(("custom", value) for value in extra)
        unique_paths = _existing_unique([value for _, value in candidates])
        source_by_path = {str(value.resolve()).casefold(): source for source, value in candidates if value.exists()}
        return [(source_by_path.get(str(path).casefold(), "custom"), path) for path in unique_paths]

    def scan(self, maximum: int = 240) -> list[dict[str, Any]]:
        count = 0
        columns = 7
        for source, root in self.roots():
            iterator = root.iterdir() if source in {"desktop", "custom"} else root.rglob("*")
            for path in iterator:
                if count >= maximum:
                    break
                try:
                    if path.name.startswith("."):
                        continue
                    if source == "start-menu" and (not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS):
                        continue
                    if source != "start-menu" and not (path.is_file() or path.is_dir()):
                        continue
                except OSError:
                    continue
                kind = "folder" if path.is_dir() else "application" if path.suffix.lower() in SUPPORTED_EXTENSIONS else "file"
                self.database.upsert_desktop_item(
                    {
                        "name": path.stem if path.is_file() else path.name,
                        "path": str(path),
                        "source": source,
                        "kind": kind,
                        "x": 36 + (count % columns) * 112,
                        "y": 72 + (count // columns) * 116,
                    }
                )
                count += 1
        return self.database.desktop_items()

    def desktop_view(self, limit: int = 42) -> list[dict[str, Any]]:
        """Return a calm desktop page; the full library remains searchable."""
        values = [
            value for value in self.database.desktop_items()
            if value["source"] == "desktop" or bool(value["pinned"])
        ]
        return values[:limit]

    def items(self, query: str = "", limit: int = 42) -> list[dict[str, Any]]:
        values = self.database.desktop_items()
        needle = query.strip().casefold()
        if needle:
            values = [value for value in values if needle in value["name"].casefold()]
            # Search results are a temporary virtual layout and do not rewrite
            # the user's saved desktop positions until an item is dragged.
            result: list[dict[str, Any]] = []
            for index, original in enumerate(values[:limit]):
                value = dict(original)
                value["x"] = 36 + (index % 7) * 112
                value["y"] = 72 + (index // 7) * 116
                result.append(value)
            return result
        return self.desktop_view(limit)

    @staticmethod
    def _search_key(value: str) -> str:
        return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())

    def applications(self, query: str, limit: int = 12, refresh_on_miss: bool = True) -> list[dict[str, Any]]:
        """Search launchable applications without exposing arbitrary filesystem paths."""

        needle = self._search_key(query.strip())
        if not needle:
            return []

        def ranked() -> list[dict[str, Any]]:
            matches: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
            source_rank = {"start-menu": 0, "desktop": 1, "custom": 2}
            for value in self.database.desktop_items(include_hidden=True):
                if value.get("kind") != "application":
                    continue
                name_key = self._search_key(str(value.get("name", "")))
                if name_key == needle:
                    match_rank = 0
                elif name_key.startswith(needle):
                    match_rank = 1
                elif needle in name_key:
                    match_rank = 2
                else:
                    continue
                score = (
                    match_rank,
                    len(name_key),
                    source_rank.get(str(value.get("source", "")), 3),
                    str(value.get("name", "")).casefold(),
                )
                matches.append((score, dict(value)))

            # The same shortcut commonly exists on both the desktop and Start
            # menu. Present it once so a natural-language request is not
            # treated as ambiguous.
            result: list[dict[str, Any]] = []
            seen_names: set[str] = set()
            for _score, value in sorted(matches, key=lambda pair: pair[0]):
                key = self._search_key(str(value.get("name", "")))
                if key in seen_names:
                    continue
                seen_names.add(key)
                result.append(value)
                if len(result) >= limit:
                    break
            return result

        result = ranked()
        if not result and refresh_on_miss:
            # The Start menu can change after Lilies starts. A targeted miss is
            # a better time for a full refresh than making every startup scan
            # the whole application tree.
            self.scan(maximum=4000)
            result = ranked()
        return result

    def _index_path(self, path: Path, source: str) -> dict[str, Any]:
        resolved = path.resolve()
        value = {
            "name": resolved.stem if resolved.is_file() else resolved.name,
            "path": str(resolved),
            "source": source,
            "kind": "folder" if resolved.is_dir() else "application" if resolved.suffix.lower() in SUPPORTED_EXTENSIONS else "file",
        }
        self.database.upsert_desktop_item(value)
        return next(
            item for item in self.database.desktop_items(include_hidden=True)
            if Path(item["path"]).resolve() == resolved
        )

    def resources(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        """Resolve files and folders by explicit path or within user content roots."""

        clean = query.strip().strip("\"'“”‘’")
        if not clean:
            return []
        user = Path.home()
        aliases = {
            "桌面": user / "Desktop",
            "桌面文件夹": user / "Desktop",
            "桌面目录": user / "Desktop",
            "文档": user / "Documents",
            "我的文档": user / "Documents",
            "文档文件夹": user / "Documents",
            "文档目录": user / "Documents",
            "下载": user / "Downloads",
            "下载文件夹": user / "Downloads",
            "下载目录": user / "Downloads",
        }
        alias = aliases.get(clean.casefold())
        if alias is not None and alias.exists():
            return [self._index_path(alias, "known-folder")]

        expanded = Path(os.path.expandvars(clean)).expanduser()
        if expanded.is_absolute() and expanded.exists():
            return [self._index_path(expanded, "explicit")]

        needle = self._search_key(clean)

        def ranked() -> list[dict[str, Any]]:
            values: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
            for item in self.database.desktop_items(include_hidden=True):
                if item.get("kind") not in {"file", "folder"}:
                    continue
                name_key = self._search_key(str(item.get("name", "")))
                path_name_key = self._search_key(Path(str(item.get("path", ""))).name)
                if name_key == needle or path_name_key == needle:
                    rank = 0
                elif name_key.startswith(needle):
                    rank = 1
                elif needle in name_key:
                    rank = 2
                else:
                    continue
                values.append(((rank, len(name_key), str(item.get("path", "")).casefold()), dict(item)))
            result: list[dict[str, Any]] = []
            seen: set[str] = set()
            for _score, item in sorted(values, key=lambda pair: pair[0]):
                key = str(Path(item["path"]).resolve()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                result.append(item)
                if len(result) >= limit:
                    break
            return result

        result = ranked()
        if result:
            return result

        # Search user content lazily on a miss. Only matching paths are added
        # to the index, so a large Documents tree does not flood the desktop DB.
        examined = 0
        for source, root in self.roots():
            if source == "start-menu":
                continue
            iterator = root.iterdir() if source in {"desktop", "custom"} else root.rglob("*")
            try:
                for path in iterator:
                    examined += 1
                    if examined > 12000:
                        break
                    try:
                        if not (path.is_file() or path.is_dir()):
                            continue
                        if needle not in self._search_key(path.stem if path.is_file() else path.name):
                            continue
                        self._index_path(path, source)
                    except OSError:
                        continue
                    if len(ranked()) >= limit:
                        break
            except OSError:
                continue
            if examined > 12000:
                break
        return ranked()

    def launch(self, path: str) -> None:
        resolved = Path(path).resolve()
        known = {Path(value["path"]).resolve() for value in self.database.desktop_items(include_hidden=True)}
        if resolved not in known:
            raise PermissionError("item is not present in the indexed desktop library")
        if not resolved.exists():
            raise FileNotFoundError(resolved)
        if os.name == "nt":
            os.startfile(str(resolved))
        else:
            subprocess.Popen(["xdg-open", str(resolved)])

    def open_folder(self, path: str) -> None:
        resolved = Path(path).resolve()
        target = resolved if resolved.is_dir() else resolved.parent
        if os.name == "nt":
            os.startfile(str(target))
        else:
            subprocess.Popen(["xdg-open", str(target)])
