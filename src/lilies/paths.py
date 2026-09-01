from __future__ import annotations

import os
import sys
from pathlib import Path


APP_NAME = "Lilies in the box"
APP_ID = "lilies-in-the-box"
WINDOWS_PRIVATE_DATA_ROOT = Path(r"F:\code\Lilies in the box\private-data")


class DataRootUnavailableError(RuntimeError):
    """Raised when the dedicated F: private-data volume is unavailable.

    Production must never silently move personal Lilies data back to the
    system drive.  The application catches this error and enters its
    read-only recovery path instead.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f"Lilies 私有数据目录不可用：{path}（{reason}）")


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    override = os.environ.get("LILIES_DATA_DIR", "").strip()
    if override:
        path = Path(override).resolve()
    elif os.name == "nt":
        path = WINDOWS_PRIVATE_DATA_ROOT
    else:
        # Android will provide an explicit LILIES_DATA_DIR.  Source builds on
        # other platforms keep project-owned data beside the project instead
        # of falling back to a hidden home-directory copy.
        path = project_root() / "private-data"
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir() or not os.access(path, os.W_OK):
            raise OSError("目录不可写")
    except OSError as exc:
        raise DataRootUnavailableError(path, str(exc)) from exc
    return path


def configure_qt_cache_environment(root: Path) -> dict[str, object]:
    """Route every Qt Quick disk cache into Lilies' dedicated data root.

    Qt otherwise derives both the QML bytecode cache and the RHI pipeline
    cache from ``QStandardPaths.CacheLocation``.  On Windows that silently
    recreates an application directory below ``LOCALAPPDATA`` even though all
    Lilies-owned data is required to stay on the dedicated F: data root.

    This function must run before ``QApplication`` and before any QML engine
    or scene graph is constructed.  Values are deliberately assigned rather
    than installed with ``setdefault``: an inherited cache path outside the
    active data root must never weaken the storage boundary.
    """

    resolved_root = Path(root).resolve()
    cache_root = resolved_root / "cache"
    qml_cache_root = cache_root / "qmlcache"
    pipeline_cache_path = cache_root / "qt-rhi-pipeline-cache.bin"
    try:
        qml_cache_root.mkdir(parents=True, exist_ok=True)
        if not qml_cache_root.is_dir() or not os.access(qml_cache_root, os.W_OK):
            raise OSError("Qt cache directory is not writable")
    except OSError as exc:
        raise DataRootUnavailableError(resolved_root, str(exc)) from exc

    def is_within_root(path: Path) -> bool:
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError:
            return False
        return True

    paths_within_root = all(
        is_within_root(path)
        for path in (cache_root, qml_cache_root, pipeline_cache_path)
    )
    if not paths_within_root:
        raise DataRootUnavailableError(
            resolved_root,
            "Qt cache path resolves outside the dedicated data root",
        )

    os.environ["QML_DISK_CACHE_PATH"] = str(qml_cache_root)
    os.environ["QSG_RHI_PIPELINE_CACHE_LOAD"] = str(pipeline_cache_path)
    os.environ["QSG_RHI_PIPELINE_CACHE_SAVE"] = str(pipeline_cache_path)
    # The legacy OpenGL shader-program cache has no supported per-app path
    # override and otherwise uses QStandardPaths.  Lilies uses the explicit
    # RHI pipeline cache above, so disable that separate system-drive cache.
    os.environ["QT_DISABLE_SHADER_DISK_CACHE"] = "1"

    environment_applied = bool(
        os.environ.get("QML_DISK_CACHE_PATH") == str(qml_cache_root)
        and os.environ.get("QSG_RHI_PIPELINE_CACHE_LOAD")
        == str(pipeline_cache_path)
        and os.environ.get("QSG_RHI_PIPELINE_CACHE_SAVE")
        == str(pipeline_cache_path)
        and os.environ.get("QT_DISABLE_SHADER_DISK_CACHE") == "1"
    )
    return {
        "dataRoot": str(resolved_root),
        "cacheRoot": str(cache_root),
        "qmlDiskCachePath": str(qml_cache_root),
        "rhiPipelineCacheLoadPath": str(pipeline_cache_path),
        "rhiPipelineCacheSavePath": str(pipeline_cache_path),
        "qtShaderDiskCacheDisabled": True,
        "pathsWithinDataRoot": paths_within_root,
        "environmentApplied": environment_applied,
        "passed": bool(paths_within_root and environment_applied),
    }


def disable_qt_disk_caches_for_recovery() -> None:
    """Fail closed when the dedicated data root is unavailable.

    Recovery mode creates only a small Widgets dialog.  Disabling both QML
    and shader caches before QApplication prevents an accidental system-drive
    fallback even if Qt internals are initialized while displaying it.
    """

    # QML_FORCE_DISK_CACHE overrides QML_DISABLE_DISK_CACHE, so remove it as
    # well as every inherited path that could point outside the unavailable
    # Lilies data root.
    for name in (
        "QML_FORCE_DISK_CACHE",
        "QML_DISK_CACHE_PATH",
        "QSG_RHI_PIPELINE_CACHE_LOAD",
        "QSG_RHI_PIPELINE_CACHE_SAVE",
    ):
        os.environ.pop(name, None)
    os.environ["QML_DISABLE_DISK_CACHE"] = "1"
    os.environ["QT_DISABLE_SHADER_DISK_CACHE"] = "1"
    os.environ["QSG_RHI_DISABLE_DISK_CACHE"] = "1"


def legacy_data_root() -> Path:
    """Return the old v0.1 location without creating or mutating it."""

    if os.name != "nt":
        return Path()
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / APP_NAME


def qml_path(name: str = "Main.qml") -> Path:
    return project_root() / "qml" / name


def theme_root(theme_id: str = "first-encounter") -> Path:
    return project_root() / "themes" / theme_id


def to_file_url(path: Path | str) -> str:
    return Path(path).resolve().as_uri()
