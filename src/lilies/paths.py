from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path


APP_NAME = "Lilies in the box"
APP_ID = "lilies-in-the-box"
WINDOWS_PRIVATE_DATA_ROOT = Path(r"F:\code\Lilies in the box\private-data")


class DataRootPurpose(Enum):
    """Call-site authority for a non-production ``LILIES_DATA_DIR``.

    The environment variable is useful for isolated tests and release probes,
    but it is not a production configuration knob.  Requiring one of these
    enum values at the call site keeps an inherited or user-edited environment
    from silently relocating the real database and Qt caches to the system
    drive.
    """

    PRODUCTION = "production"
    TEST = "test"
    DIAGNOSTIC = "diagnostic"
    NATIVE_CAPTURE_HELPER = "native-capture-helper"


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


def _same_windows_location(left: Path, right: Path) -> bool:
    """Compare two absolute Windows locations without relying on existence."""

    return os.path.normcase(os.path.normpath(str(left))) == os.path.normcase(
        os.path.normpath(str(right))
    )


def data_root(*, purpose: DataRootPurpose = DataRootPurpose.PRODUCTION) -> Path:
    """Return the only data directory authorized for this process role.

    A normal Windows process is pinned to ``WINDOWS_PRIVATE_DATA_ROOT``.  It
    may inherit ``LILIES_DATA_DIR`` only when that value resolves to the same
    fixed directory.  Tests, non-interactive release diagnostics and the
    bounded native-capture helper must opt in explicitly at their call sites;
    merely setting another environment variable can therefore never weaken a
    production launch.

    Android remains able to supply its platform-owned directory through
    ``LILIES_DATA_DIR`` because the fixed F: boundary is Windows-specific.
    """

    if not isinstance(purpose, DataRootPurpose):
        raise TypeError("purpose must be a DataRootPurpose")

    override = os.environ.get("LILIES_DATA_DIR", "").strip()
    try:
        requested = Path(override).expanduser().resolve() if override else None
        if os.name == "nt":
            if requested is not None and purpose is not DataRootPurpose.PRODUCTION:
                path = requested
            else:
                fixed_root = WINDOWS_PRIVATE_DATA_ROOT.resolve()
                # A directory junction must not silently turn the fixed F:
                # location into a system-drive data store either.  Resolve it
                # before mkdir so recovery cannot first create a C: directory.
                if (
                    fixed_root.drive.casefold()
                    != WINDOWS_PRIVATE_DATA_ROOT.drive.casefold()
                ):
                    raise DataRootUnavailableError(
                        WINDOWS_PRIVATE_DATA_ROOT,
                        "the fixed data root resolves outside its dedicated volume",
                    )
                if requested is not None and not _same_windows_location(
                    requested, fixed_root
                ):
                    raise DataRootUnavailableError(
                        requested,
                        "LILIES_DATA_DIR is not permitted during normal startup; "
                        f"the fixed data root is {fixed_root}",
                    )
                # Even an equivalent spelling of the environment override is
                # discarded so all production consumers receive one canonical
                # path object.
                path = fixed_root
        elif requested is not None:
            # Android supplies an app-private platform directory here.  Source
            # builds on other platforms use the same explicit override.
            path = requested
        else:
            path = (project_root() / "private-data").resolve()
    except DataRootUnavailableError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        failed_path = Path(override) if override else WINDOWS_PRIVATE_DATA_ROOT
        raise DataRootUnavailableError(failed_path, str(exc)) from exc

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

    try:
        resolved_root = Path(root).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise DataRootUnavailableError(Path(root), str(exc)) from exc
    cache_root = resolved_root / "cache"
    qml_cache_root = cache_root / "qmlcache"
    pipeline_cache_path = cache_root / "qt-rhi-pipeline-cache.bin"

    def is_within_root(path: Path) -> bool:
        try:
            path.resolve().relative_to(resolved_root)
        except (OSError, RuntimeError, ValueError):
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

    # Containment must be checked before touching the filesystem: cache or
    # qmlcache may already be a junction to another drive.
    try:
        qml_cache_root.mkdir(parents=True, exist_ok=True)
        if not qml_cache_root.is_dir() or not os.access(qml_cache_root, os.W_OK):
            raise OSError("Qt cache directory is not writable")
    except OSError as exc:
        raise DataRootUnavailableError(resolved_root, str(exc)) from exc

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
