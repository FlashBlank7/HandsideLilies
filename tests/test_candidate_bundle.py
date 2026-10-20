import importlib.util
import json
import marshal
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("candidate_bundle", ROOT / "scripts" / "verify_candidate_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


@pytest.fixture
def candidate(tmp_path):
    for name in bundle.ROOT_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[project]\nversion = "0.3.57"\n' if name == "pyproject.toml" else "# fixture\n", "utf-8")
    for relative, contents in {
        "src/lilies/__init__.py": '__version__ = "0.3.57"\n',
        "src/lilies/backend.py": 'def reply():\n    return "你好"\n',
        "src/lilies/local_model_worker.py": "answer = 42\n",
        "qml/Main.qml": "import QtQuick\nItem {}\n",
        "themes/first-encounter/theme.json": '{"version":"0.3.57"}',
        "assets/example.svg": "<svg/>"}.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, "utf-8")
    return tmp_path


def test_snapshot_covers_new_runtime_files_and_detects_added_or_changed_inputs(candidate):
    manifest = bundle.snapshot(candidate, "v0357-test")
    bundle.assert_current(candidate, manifest)
    (candidate / "qml" / "NewPanel.qml").write_text("Item {}", "utf-8")
    with pytest.raises(ValueError, match="changed"):
        bundle.assert_current(candidate, manifest)
    updated = bundle.snapshot(candidate, "v0357-test-new")
    assert "qml/NewPanel.qml" in updated["files"]
    (candidate / "src/lilies/backend.py").write_text("answer = 'changed'", "utf-8")
    with pytest.raises(ValueError, match="changed"):
        bundle.assert_current(candidate, updated)


@pytest.mark.parametrize("relative", ["assets/credentials.db", "qml/.env", "themes/example.key"])
def test_private_files_cannot_be_packaged(candidate, relative):
    (candidate / relative).write_text("synthetic private fixture", "utf-8")
    with pytest.raises(ValueError, match="Private-looking"):
        bundle.snapshot(candidate, "v0357-test")


@pytest.mark.parametrize("identity", ["v0356-old", "v0357-../escape", "v0357-a/b", "v0357-"])
def test_invalid_build_identity_rejected(candidate, identity):
    with pytest.raises(ValueError):
        bundle.snapshot(candidate, identity)


def test_package_verification_checks_compiled_modules_and_all_resources(candidate, monkeypatch):
    manifest = bundle.snapshot(candidate, "v0357-test")
    package = candidate / "dist" / "test" / "LiliesInTheBox"
    internal = package / "_internal"
    internal.mkdir(parents=True)
    (package / "LiliesInTheBox.exe").write_bytes(b"synthetic executable - never executed")
    for directory in bundle.RESOURCE_ROOTS:
        shutil.copytree(candidate / directory, internal / directory)
    (internal / "runtime").mkdir()
    shutil.copyfile(candidate / "src/lilies/local_model_worker.py", internal / "runtime/local_model_worker.py")
    (internal / "source-manifest.json").write_text(json.dumps(manifest), "utf-8")
    codes = {bundle.module_name(relative): compile((candidate / relative).read_bytes(), relative.removeprefix("src/"), "exec", dont_inherit=True)
             for relative in manifest["files"] if relative.startswith("src/lilies/")}
    main = compile((candidate / "main.py").read_bytes(), "main.py", "exec", dont_inherit=True)
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
        open_embedded_archive=lambda _: SimpleNamespace(toc=codes, extract=codes.__getitem__),
        extract=lambda _: marshal.dumps(main))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    result = bundle.verify_bundle(candidate, manifest, package)
    assert result["passed"] and result["pythonCodeMatches"] and not result["releaseReady"]
    assert set(result["pythonModules"]) == set(codes)
    assert result["resourceCount"] == 3 and result["packageFiles"]
    original = codes["lilies.backend"]
    codes["lilies.backend"] = compile("answer = 'stale package'", "lilies/backend.py", "exec", dont_inherit=True)
    with pytest.raises(ValueError, match="Python code differs"):
        bundle.verify_bundle(candidate, manifest, package)
    codes["lilies.backend"] = original
    (internal / "qml/Main.qml").write_text("changed", "utf-8")
    with pytest.raises(ValueError, match="resource differs"):
        bundle.verify_bundle(candidate, manifest, package)
    shutil.copyfile(candidate / "qml/Main.qml", internal / "qml/Main.qml")
    (internal / "qml/Unexpected.qml").write_text("extra", "utf-8")
    with pytest.raises(ValueError, match="resources differ"):
        bundle.verify_bundle(candidate, manifest, package)


def test_candidate_evidence_is_exclusive_and_f_drive_only(candidate):
    target = candidate / "artifacts" / "build-one" / "source-manifest.json"
    bundle.output_json(candidate, target, {"example": True})
    with pytest.raises(ValueError, match="replacement"):
        bundle.output_json(candidate, target, {"example": False})
    assert json.loads(target.read_text("utf-8"))["example"] is True
    with pytest.raises(ValueError, match="F-drive"):
        bundle.output_json(candidate, candidate / "private-data" / "wrong.json", {})


def test_source_identity_protects_all_new_runtime_features():
    manifest = bundle.snapshot(ROOT, "v0357-test-coverage")
    for relative in ("qml/PaperDesk.qml", "qml/TaskEditor.qml", "qml/BoxSearch.qml",
                     "qml/SharedWorldWindow.qml", "qml/QuietPetMotion.qml",
                     "src/lilies/core/papers.py", "src/lilies/core/work_resumption.py",
                     "src/lilies/core/shared_world.py", "src/lilies/box_search_controller.py",
                     "themes/first-encounter/assets/lilith-outfit-summer-cotton-transparent-v1.png"):
        assert relative in manifest["files"]


def test_build_retains_old_packages_and_never_calls_install_or_promotion():
    build = (ROOT / "scripts/build_windows.ps1").read_text("utf-8")
    assert "--distpath $DistParent" in build and "--workpath" in build
    assert "Build identity already exists" in build
    assert "[switch]$OffscreenOnly" in build
    assert "if (-not $OffscreenOnly)" in build
    assert "releaseReady = $false" in build
    assert "PYINSTALLER_CONFIG_DIR" in build and "LILIES_DATA_DIR" in build
    assert "install_windows.ps1" not in build and "promote_" not in build


@pytest.fixture
def sealed_candidate(candidate, monkeypatch):
    """A hash-complete fixture, never an executable or a real installation."""
    build_id = "v0357-check-fixture"
    manifest = bundle.snapshot(candidate, build_id)
    package = candidate / "dist" / build_id / "LiliesInTheBox"
    internal = package / "_internal"
    internal.mkdir(parents=True)
    (package / "LiliesInTheBox.exe").write_bytes(b"never-executed-fixture")
    (internal / "dependency.dll").write_bytes(b"fixture-runtime-library")
    for directory in bundle.RESOURCE_ROOTS:
        shutil.copytree(candidate / directory, internal / directory)
    (internal / "runtime").mkdir()
    shutil.copyfile(candidate / "src/lilies/local_model_worker.py", internal / "runtime/local_model_worker.py")
    (internal / "source-manifest.json").write_text(json.dumps(manifest), "utf-8")
    codes = {bundle.module_name(relative): compile((candidate / relative).read_bytes(), relative, "exec", dont_inherit=True)
             for relative in manifest["files"] if relative.startswith("src/lilies/")}
    archive = SimpleNamespace(toc={"PYZ.pyz": (0, 0, 0, 0, "z")},
        open_embedded_archive=lambda _: SimpleNamespace(toc=codes, extract=codes.__getitem__),
        extract=lambda _: marshal.dumps(compile((candidate / "main.py").read_bytes(), "main.py", "exec", dont_inherit=True)))
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    verified = bundle.verify_bundle(candidate, manifest, package)
    artifacts = candidate / "artifacts" / build_id
    artifacts.mkdir(parents=True)
    (artifacts / "source-manifest.json").write_text(json.dumps(manifest), "utf-8")
    (artifacts / "bundle-integrity.json").write_text(json.dumps(verified), "utf-8")
    receipt = {"schemaVersion": 1, "applicationVersion": "0.3.57", "buildId": build_id,
               "package": str(package), "sourceDigest": verified["sourceDigest"],
               "executableSha256": verified["executableSha256"], "packageChecksPassed": True,
               "offscreenOnly": True, "windowsStartup": "not-run", "releaseReady": False}
    (artifacts / "candidate-build.json").write_text(json.dumps(receipt), "utf-8")
    return candidate, package, artifacts, build_id


def _fixture_inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_exact_candidate_check_is_read_only_and_never_grants_release(sealed_candidate, capsys):
    root, package, artifacts, build_id = sealed_candidate
    # A hand-edited ready flag cannot turn an identity check into acceptance.
    path = artifacts / "candidate-build.json"
    receipt = json.loads(path.read_text("utf-8"))
    receipt["releaseReady"] = True
    path.write_text(json.dumps(receipt), "utf-8")
    before = _fixture_inventory(root)
    bundle.main(["check-candidate", "--root", str(root), "--build-id", build_id])
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] and not result["releaseReady"]
    assert result["package"] == str(package)
    assert result["files"] == len(_fixture_inventory(package))
    assert _fixture_inventory(root) == before


@pytest.mark.parametrize("field,value", [
    ("buildId", "v0357-other"), ("applicationVersion", "0.3.56"),
    ("sourceDigest", "0" * 64), ("executableSha256", "0" * 64),
    ("package", "F:/unrelated-program"), ("packageChecksPassed", False),
    ("packageChecksPassed", "true"), ("offscreenOnly", "false"),
    ("windowsStartup", "passed-hidden-probe"),
], ids=["build", "version", "source", "exe", "path", "failed", "string-success", "string-mode", "mode-mismatch"])
def test_receipt_identity_and_typed_completion_are_required(sealed_candidate, field, value):
    root, _package, artifacts, build_id = sealed_candidate
    path = artifacts / "candidate-build.json"
    receipt = json.loads(path.read_text("utf-8"))
    receipt[field] = value
    path.write_text(json.dumps(receipt), "utf-8")
    before = _fixture_inventory(root)
    with pytest.raises(ValueError):
        bundle.check_candidate(root, build_id)
    assert _fixture_inventory(root) == before


@pytest.mark.parametrize("kind", ["changed-library", "extra-library", "missing-library", "source-added", "source-changed", "missing-receipt", "corrupt-inventory"])
def test_stale_or_changed_candidate_cannot_reuse_prior_green_evidence(sealed_candidate, kind):
    root, package, artifacts, build_id = sealed_candidate
    if kind == "changed-library":
        (package / "_internal/dependency.dll").write_bytes(b"replaced after build")
    elif kind == "extra-library":
        (package / "_internal/extra.dll").write_bytes(b"unreviewed dependency")
    elif kind == "missing-library":
        (package / "_internal/dependency.dll").rename(root / "retained-library.fixture")
    elif kind == "source-added":
        (root / "src/lilies/drag_timing.py").write_text("new_runtime = True", "utf-8")
    elif kind == "source-changed":
        (root / "src/lilies/backend.py").write_text("new_runtime = True", "utf-8")
    elif kind == "missing-receipt":
        (artifacts / "candidate-build.json").rename(artifacts / "retained-receipt.fixture")
    else:
        path = artifacts / "bundle-integrity.json"
        record = json.loads(path.read_text("utf-8"))
        record["packageFiles"] = {}
        path.write_text(json.dumps(record), "utf-8")
    before = _fixture_inventory(root)
    with pytest.raises((ValueError, FileNotFoundError)):
        bundle.check_candidate(root, build_id)
    assert _fixture_inventory(root) == before


@pytest.mark.parametrize("record", ["source-manifest.json", "bundle-integrity.json"])
def test_all_evidence_must_identify_the_selected_build(sealed_candidate, record):
    root, _package, artifacts, build_id = sealed_candidate
    path = artifacts / record
    value = json.loads(path.read_text("utf-8"))
    value["buildId"] = "v0357-wrong"
    path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError, match="different build"):
        bundle.check_candidate(root, build_id)


@pytest.mark.parametrize("extra", ["--output", "--package", "--manifest"])
def test_read_only_preflight_cannot_override_paths_or_write_report(sealed_candidate, extra):
    root, _package, _artifacts, build_id = sealed_candidate
    before = _fixture_inventory(root)
    with pytest.raises(SystemExit) as exc:
        bundle.main(["check-candidate", "--root", str(root), "--build-id", build_id, extra, str(root / "unexpected")])
    assert exc.value.code == 2
    assert _fixture_inventory(root) == before


@pytest.mark.parametrize("action", ["snapshot", "verify"])
def test_other_candidate_commands_still_require_an_exclusive_output(action):
    with pytest.raises(SystemExit) as exc:
        bundle.main([action])
    assert exc.value.code == 2
