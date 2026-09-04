from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PIL import Image

import lilies.core.native_capture_helper as helper_module
from lilies.core.activity import CaptureCancelled, CaptureStaging
from lilies.core.native_capture_helper import NativeCaptureHelperError


def _helper_destination(command: list[str]) -> Path:
    marker = command.index("--native-capture-helper")
    return Path(command[marker + 3])


class _CompletingProcess:
    def __init__(self, command, **kwargs) -> None:
        self.command = list(command)
        self.kwargs = dict(kwargs)
        self.returncode = 0
        destination = _helper_destination(self.command)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.effect_noise((48, 32), 32).convert("RGB").save(destination, "PNG")

    def poll(self):
        return self.returncode


class _HungProcess:
    instances: list["_HungProcess"] = []

    def __init__(self, command, **kwargs) -> None:
        self.command = list(command)
        self.kwargs = dict(kwargs)
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.destination = _helper_destination(self.command)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_bytes(b"incomplete helper output")
        type(self).instances.append(self)

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        assert timeout == 0.4
        self.returncode = -15 if self.terminated else -9
        return self.returncode


def _staging(tmp_path: Path) -> CaptureStaging:
    return CaptureStaging(
        tmp_path / "private" / "capture-staging",
        tmp_path / "capture-library",
        max_edge=512,
    )


def test_helper_cli_dispatch_is_before_qt_and_single_instance_work() -> None:
    app_path = Path(helper_module.__file__).parents[1] / "app.py"
    source = app_path.read_text("utf-8")
    tree = ast.parse(source)
    main = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "main"
    )

    first_statement = main.body[0]
    helper_branch = main.body[1]
    assert isinstance(first_statement, ast.Assign)
    assert isinstance(helper_branch, ast.If)
    assert ast.unparse(helper_branch.test) == "args.native_capture_helper"
    helper_source = ast.get_source_segment(source, helper_branch) or ""
    assert "run_native_capture_helper" in helper_source
    helper_calls = {
        ast.unparse(node.func)
        for node in ast.walk(helper_branch)
        if isinstance(node, ast.Call)
    }
    assert "QApplication" not in helper_calls
    assert "forward_to_existing_instance" not in helper_calls

    helper_offset = source.index("if args.native_capture_helper:", source.index("def main("))
    assert helper_offset < source.index("forward_to_existing_instance(args)", helper_offset)
    assert helper_offset < source.index("QApplication([", helper_offset)

    project_root = Path(helper_module.__file__).parents[3]
    for entrypoint in (
        project_root / "main.py",
        project_root / "src" / "lilies" / "__main__.py",
    ):
        entrypoint_source = entrypoint.read_text("utf-8")
        helper_import = entrypoint_source.index("native_capture_helper_main")
        app_import = entrypoint_source.index("app import main")
        assert helper_import < app_import
        assert '"--native-capture-helper" not in arguments' in entrypoint_source


def test_destination_is_limited_to_private_capture_staging_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    purposes: list[object] = []

    def helper_data_root(*, purpose: object) -> Path:
        purposes.append(purpose)
        return tmp_path / "private"

    monkeypatch.setattr(helper_module, "data_root", helper_data_root)
    valid = tmp_path / "private" / "capture-staging" / ("capture-" + "a" * 32 + ".png")
    root, destination = helper_module._resolved_staging_destination(valid)
    assert root == (tmp_path / "private" / "capture-staging").resolve()
    assert destination == valid.resolve()

    invalid = (
        tmp_path / "private" / ("capture-" + "a" * 32 + ".png"),
        tmp_path / "private" / "capture-staging" / "capture-not-hex.png",
        tmp_path / "private" / "capture-staging" / ("capture-" + "a" * 31 + ".png"),
        tmp_path / "private" / "capture-staging" / ("capture-" + "a" * 32 + ".jpg"),
    )
    for candidate in invalid:
        with pytest.raises(ValueError, match="private staging"):
            helper_module._resolved_staging_destination(candidate)

    assert purposes == [helper_module.DataRootPurpose.NATIVE_CAPTURE_HELPER] * 5


def test_parent_helper_adopts_valid_png_from_expected_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    processes: list[_CompletingProcess] = []

    def popen(command, **kwargs):
        process = _CompletingProcess(command, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(helper_module, "native_capture_helper_available", lambda: True)
    monkeypatch.setattr(helper_module.subprocess, "Popen", popen)
    staging = _staging(tmp_path)

    staged = helper_module.stage_window_capture_with_helper(staging, 41, 73)
    try:
        assert staged.path.is_file()
        assert staged.path.parent == staging.root.resolve()
        assert staged.path.name.startswith("capture-")
        assert staged.path.name.endswith(".png")
        assert len(staged.path.stem.removeprefix("capture-")) == 32
        with Image.open(staged.path) as image:
            assert image.size == (48, 32)
            assert image.format == "PNG"
        assert _helper_destination(processes[0].command) == staged.path
        assert processes[0].kwargs["env"]["LILIES_DATA_DIR"] == str(
            staging.root.resolve().parent
        )
    finally:
        staged.release()


def test_parent_helper_timeout_stops_process_and_removes_partial_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _HungProcess.instances.clear()
    ticks = iter((10.0, 12.0))
    monkeypatch.setattr(helper_module, "native_capture_helper_available", lambda: True)
    monkeypatch.setattr(helper_module.subprocess, "Popen", _HungProcess)
    monkeypatch.setattr(helper_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(helper_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(NativeCaptureHelperError, match="timed out"):
        helper_module.stage_window_capture_with_helper(
            _staging(tmp_path), 41, 73, timeout_seconds=1.0
        )

    process = _HungProcess.instances[0]
    assert process.terminated is True
    assert process.killed is False
    assert not process.destination.exists()


def test_parent_helper_cancellation_stops_process_and_removes_partial_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _HungProcess.instances.clear()
    monkeypatch.setattr(helper_module, "native_capture_helper_available", lambda: True)
    monkeypatch.setattr(helper_module.subprocess, "Popen", _HungProcess)

    with pytest.raises(CaptureCancelled, match="cancelled"):
        helper_module.stage_window_capture_with_helper(
            _staging(tmp_path), 41, 73, cancelled=lambda: True
        )

    process = _HungProcess.instances[0]
    assert process.terminated is True
    assert process.killed is False
    assert not process.destination.exists()
