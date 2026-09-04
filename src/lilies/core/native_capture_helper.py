from __future__ import annotations

"""Short-lived native capture helper used by the frozen Windows build.

The main Lilies process never performs the ``WM_PRINT`` allocation itself.
Instead, this module launches the same packaged executable in a hidden helper
mode, applies a whole-process deadline, and adopts only a validated PNG from
the private capture-staging directory.  A hung or unusually large target can
therefore be terminated without retaining its native buffers in the desktop
process.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from .activity import (
    CaptureCancelled,
    CaptureEncodeError,
    CaptureStorageError,
    CaptureStaging,
    LowInformationCapture,
    ProtectedCaptureContent,
    StagedCapture,
    capture_window_image_via_print_window,
    encode_window_image_png,
)
from ..paths import DataRootPurpose, DataRootUnavailableError, data_root


_CAPTURE_NAME = re.compile(r"capture-[0-9a-f]{32}\.png", re.ASCII)
_HELPER_SUCCESS = 0
_HELPER_INVALID_REQUEST = 20
_HELPER_CAPTURE_FAILED = 21
_HELPER_ENCODE_FAILED = 22
_HELPER_NO_VISUAL_EVIDENCE = 23
_HELPER_STORAGE_FAILED = 24


class NativeCaptureHelperError(RuntimeError):
    """A content-free failure from the bounded helper process."""


def native_capture_helper_available() -> bool:
    """Return whether production may launch the isolated helper.

    Source tests and editable development sessions remain deterministic unless
    they explicitly opt in.  The packaged Windows application enables the
    helper automatically.
    """

    return bool(
        os.name == "nt"
        and (
            getattr(sys, "frozen", False)
            or os.environ.get("LILIES_ENABLE_NATIVE_CAPTURE_HELPER") == "1"
        )
    )


def _resolved_staging_destination(value: str | Path) -> tuple[Path, Path]:
    root = (
        data_root(purpose=DataRootPurpose.NATIVE_CAPTURE_HELPER)
        / "capture-staging"
    ).resolve()
    destination = Path(value).resolve()
    if destination.parent != root or not _CAPTURE_NAME.fullmatch(destination.name):
        raise ValueError("capture destination is outside private staging")
    return root, destination


def run_native_capture_helper(
    hwnd_text: str,
    process_id_text: str,
    destination_text: str,
    *,
    max_edge: int = 1600,
) -> int:
    """Execute one bounded capture request and return a content-free code."""

    destination: Path | None = None
    image: Image.Image | None = None
    try:
        hwnd = int(hwnd_text)
        process_id = int(process_id_text)
        if hwnd <= 0 or process_id <= 0:
            return _HELPER_INVALID_REQUEST
        root, destination = _resolved_staging_destination(destination_text)
        root.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        bounded_edge = max(256, min(int(max_edge), 1600))
    except (DataRootUnavailableError, OSError, TypeError, ValueError):
        return _HELPER_INVALID_REQUEST

    try:
        image = capture_window_image_via_print_window(
            hwnd,
            expected_process_id=process_id,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        destination.unlink(missing_ok=True)
        return _HELPER_CAPTURE_FAILED

    try:
        encode_window_image_png(image, destination, max_edge=bounded_edge)
    except (ProtectedCaptureContent, LowInformationCapture):
        destination.unlink(missing_ok=True)
        return _HELPER_NO_VISUAL_EVIDENCE
    except CaptureStorageError:
        destination.unlink(missing_ok=True)
        return _HELPER_STORAGE_FAILED
    except OSError:
        destination.unlink(missing_ok=True)
        return _HELPER_STORAGE_FAILED
    except (RuntimeError, TypeError, ValueError):
        destination.unlink(missing_ok=True)
        return _HELPER_ENCODE_FAILED
    finally:
        image.close()
    return _HELPER_SUCCESS


def native_capture_helper_main(argv: list[str]) -> int:
    """Parse the private helper CLI without importing the Qt application."""

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--native-capture-helper",
        nargs=3,
        metavar=("HWND", "PID", "DESTINATION"),
        required=True,
    )
    parser.add_argument("--native-capture-max-edge", type=int, default=1600)
    try:
        options = parser.parse_args(list(argv))
    except SystemExit:
        return _HELPER_INVALID_REQUEST
    return run_native_capture_helper(
        options.native_capture_helper[0],
        options.native_capture_helper[1],
        options.native_capture_helper[2],
        max_edge=options.native_capture_max_edge,
    )


def _helper_command(
    hwnd: int,
    process_id: int,
    destination: Path,
    max_edge: int,
) -> list[str]:
    arguments = [
        "--native-capture-helper",
        str(int(hwnd)),
        str(int(process_id)),
        str(destination),
        "--native-capture-max-edge",
        str(int(max_edge)),
    ]
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve()), *arguments]
    return [str(Path(sys.executable).resolve()), "-m", "lilies", *arguments]


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.4)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=0.4)
        except (OSError, subprocess.TimeoutExpired):
            pass


def stage_window_capture_with_helper(
    staging: CaptureStaging,
    hwnd: int,
    expected_process_id: int,
    *,
    cancelled: Callable[[], bool] | None = None,
    timeout_seconds: float = 5.0,
) -> StagedCapture:
    """Capture one HWND in an isolated process and adopt its bounded PNG."""

    if not native_capture_helper_available():
        raise NativeCaptureHelperError("native capture helper is unavailable")
    if int(hwnd) <= 0 or int(expected_process_id) <= 0:
        raise NativeCaptureHelperError("native capture identity is unavailable")

    staging.root.mkdir(parents=True, exist_ok=True)
    expected_root = staging.root.resolve()
    if expected_root.name.casefold() != "capture-staging":
        raise NativeCaptureHelperError("native capture staging is invalid")
    destination = expected_root / f"capture-{os.urandom(16).hex()}.png"
    child_environment = dict(os.environ)
    child_environment["LILIES_DATA_DIR"] = str(expected_root.parent)
    child_environment["PYTHONUTF8"] = "1"
    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    process: subprocess.Popen[bytes] | None = None
    deadline = time.monotonic() + max(1.0, min(float(timeout_seconds), 8.0))
    try:
        process = subprocess.Popen(
            _helper_command(hwnd, expected_process_id, destination, staging.max_edge),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment,
            creationflags=creation_flags,
        )
        while process.poll() is None:
            if cancelled is not None and cancelled():
                _stop_process(process)
                raise CaptureCancelled("native capture helper cancelled")
            if time.monotonic() >= deadline:
                _stop_process(process)
                raise NativeCaptureHelperError("native capture helper timed out")
            time.sleep(0.02)
        if process.returncode != _HELPER_SUCCESS or not destination.is_file():
            raise NativeCaptureHelperError("native capture helper failed")

        try:
            with Image.open(destination) as verification:
                width, height = verification.size
                verification.verify()
            if (
                width <= 1
                or height <= 1
                or max(width, height) > staging.max_edge
            ):
                raise CaptureEncodeError("native helper image is outside bounds")
        except (OSError, RuntimeError, ValueError) as exc:
            raise NativeCaptureHelperError("native helper image is invalid") from exc
        if cancelled is not None and cancelled():
            raise CaptureCancelled("native capture helper cancelled")
        return StagedCapture(destination, staging.library_root)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if process is not None and process.poll() is None:
            _stop_process(process)
