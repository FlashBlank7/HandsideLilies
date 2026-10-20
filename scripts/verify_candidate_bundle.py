"""Bind a candidate to all runtime inputs and inspect its packaged code/data.

This tool never imports the application, opens a window, accesses user data,
installs a package, or declares native/release acceptance. Generated artifacts
are restricted to the candidate's F-drive build/report trees.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import marshal
import platform
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import CodeType


ROOT_FILES = ("main.py", "LiliesInTheBox.spec", "pyproject.toml", "uv.lock",
              "packaging/windows_version_info.txt")
RESOURCE_ROOTS = ("qml", "themes", "assets")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_file(root: Path, path: Path) -> Path:
    absolute = path.absolute()
    resolved = path.resolve(strict=True)
    if resolved != absolute or not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Linked or escaped release input: {path.name}")
    if not resolved.is_file():
        raise ValueError(f"Release input is not a file: {path.name}")
    return resolved


def input_files(root: Path) -> dict[str, str]:
    paths = [root / name for name in ROOT_FILES]
    paths.extend((root / "src" / "lilies").rglob("*.py"))
    paths.extend((root / "packaging" / "hooks").glob("*.py"))
    for name in RESOURCE_ROOTS:
        paths.extend(path for path in (root / name).rglob("*") if path.is_file())
    result = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts:
            continue
        # Private files do not belong in any of the package's resource trees.
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".key", ".pem", ".p12"} or path.name.startswith(".env"):
            raise ValueError(f"Private-looking file in release inputs: {relative}")
        result[relative] = sha256(checked_file(root, path))
    if not any(name.startswith("qml/") for name in result) or "src/lilies/backend.py" not in result:
        raise ValueError("Runtime input tree is incomplete")
    return dict(sorted(result.items()))


def source_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def snapshot(root: Path, build_id: str) -> dict:
    if not re.fullmatch(r"v0357-[A-Za-z0-9-]{1,80}", build_id):
        raise ValueError("Invalid v0.3.57 build identity")
    version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if version != "0.3.57":
        raise ValueError("Candidate runtime must have version 0.3.57")
    files = input_files(root)
    return {"schemaVersion": 1, "applicationVersion": version, "buildId": build_id,
            "createdAt": datetime.now(UTC).isoformat(), "pythonVersion": platform.python_version(),
            "sourceDigest": source_digest(files), "files": files}


def assert_current(root: Path, manifest: dict) -> None:
    if manifest.get("schemaVersion") != 1 or manifest.get("applicationVersion") != "0.3.57":
        raise ValueError("Unsupported candidate manifest")
    files = input_files(root)
    if manifest.get("files") != files or manifest.get("sourceDigest") != source_digest(files):
        raise ValueError("Runtime inputs changed since this build started; build a new candidate")


def module_name(relative: str) -> str:
    name = relative.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return name.removesuffix(".__init__")


def assert_code_matches(source: Path, code: CodeType) -> None:
    if not isinstance(code, CodeType):
        raise ValueError(f"Packaged module has no code: {source.name}")
    expected = compile(source.read_bytes(), code.co_filename, "exec", dont_inherit=True, optimize=0)
    if expected != code:
        raise ValueError(f"Packaged Python code differs from source: {source.name}")


def verify_bundle(root: Path, manifest: dict, package: Path) -> dict:
    from PyInstaller.archive.readers import CArchiveReader

    assert_current(root, manifest)
    executable = checked_file(package, package / "LiliesInTheBox.exe")
    internal = package / "_internal"
    embedded = checked_file(package, internal / "source-manifest.json")
    if json.loads(embedded.read_text("utf-8")) != manifest:
        raise ValueError("Packaged source manifest differs from build inputs")
    resource_count = 0
    expected_resources = {name for name in manifest["files"] if name.split("/", 1)[0] in RESOURCE_ROOTS}
    actual_resources = set()
    for directory in RESOURCE_ROOTS:
        for path in (internal / directory).rglob("*"):
            if path.is_file():
                checked_file(package, path)
                actual_resources.add(path.relative_to(internal).as_posix())
    if actual_resources != expected_resources:
        raise ValueError(f"Packaged resources differ: missing={sorted(expected_resources - actual_resources)}, extra={sorted(actual_resources - expected_resources)}")
    for relative in expected_resources:
        if sha256(internal / relative) != manifest["files"][relative]:
            raise ValueError(f"Packaged resource differs: {relative}")
        resource_count += 1
    worker = checked_file(package, internal / "runtime" / "local_model_worker.py")
    if sha256(worker) != manifest["files"]["src/lilies/local_model_worker.py"]:
        raise ValueError("Packaged local model worker differs")
    archive = CArchiveReader(str(executable))
    pyz_names = [name for name, entry in archive.toc.items() if entry[-1] == "z"]
    if len(pyz_names) != 1:
        raise ValueError("Expected one embedded Python archive")
    pyz = archive.open_embedded_archive(pyz_names[0])
    modules = []
    for relative in manifest["files"]:
        if relative.startswith("src/lilies/") and relative.endswith(".py"):
            name = module_name(relative)
            if name not in pyz.toc:
                raise ValueError(f"Missing packaged runtime module: {name}")
            assert_code_matches(root / relative, pyz.extract(name))
            modules.append(name)
    assert_code_matches(root / "main.py", marshal.loads(archive.extract("main")))
    package_files = {path.relative_to(package).as_posix(): sha256(checked_file(package, path))
                     for path in sorted(package.rglob("*")) if path.is_file()}
    return {"schemaVersion": 1, "applicationVersion": manifest["applicationVersion"],
            "buildId": manifest["buildId"], "sourceDigest": manifest["sourceDigest"],
            "executableSha256": sha256(executable).upper(), "resourceCount": resource_count,
            "pythonModules": modules, "pythonCodeMatches": True, "packageFiles": package_files,
            "verifiedAt": datetime.now(UTC).isoformat(), "passed": True,
            "releaseReady": False, "scope": "Package input/code/data integrity only; no native or live-account acceptance"}


def check_candidate(root: Path, build_id: str) -> dict:
    """Read-only install preflight for one immutable side-by-side build.

    Recompute the actual package/code/source relationship, then bind it to
    the original build receipt and full package inventory. Neither a version
    string nor a manually toggled releaseReady field authorizes deployment.
    Native and whole-installer acceptance remain separate, still-closed gates.
    """
    if not re.fullmatch(r"v0357-[A-Za-z0-9-]{1,80}", build_id):
        raise ValueError("An exact v0.3.57 build identity is required")
    package = root / "dist" / build_id / "LiliesInTheBox"
    artifacts = root / "artifacts" / build_id

    def read_record(name: str) -> dict:
        value = json.loads(checked_file(root, artifacts / name).read_text("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Candidate evidence must be an object")
        if (value.get("schemaVersion") != 1
                or value.get("applicationVersion") != "0.3.57"
                or value.get("buildId") != build_id):
            raise ValueError("Candidate evidence identifies a different build")
        return value

    manifest = read_record("source-manifest.json")
    receipt = read_record("candidate-build.json")
    original = read_record("bundle-integrity.json")
    if (receipt.get("packageChecksPassed") is not True
            or type(receipt.get("offscreenOnly")) is not bool
            or receipt.get("windowsStartup") != (
                "not-run" if receipt["offscreenOnly"] else "passed-hidden-probe"
            )):
        raise ValueError("Incomplete or inconsistent candidate build receipt")
    if (not isinstance(receipt.get("package"), str)
            or Path(receipt["package"]).absolute() != package.absolute()):
        raise ValueError("Candidate receipt points outside the exact build directory")
    current = verify_bundle(root, manifest, package)
    for record in (receipt, original):
        if (record.get("sourceDigest") != current["sourceDigest"]
                or record.get("executableSha256") != current["executableSha256"]):
            raise ValueError("Candidate identity/hash differs from its build evidence")
    if (original.get("passed") is not True
            or original.get("pythonCodeMatches") is not True
            or original.get("packageFiles") != current["packageFiles"]):
        raise ValueError("Candidate files differ from the original verified package")
    return {"schemaVersion": 1, "applicationVersion": "0.3.57",
            "buildId": build_id, "package": str(package.absolute()),
            "executableSha256": current["executableSha256"],
            "sourceDigest": current["sourceDigest"],
            "files": len(current["packageFiles"]), "passed": True,
            "releaseReady": False,
            "scope": "Read-only current-source package identity; not deployment or native acceptance"}


def output_json(root: Path, target: Path, value: dict) -> None:
    root = root.resolve()
    target = target.absolute()
    if target.drive.casefold() != "f:" or not any(target.is_relative_to(root / name) for name in ("build", "artifacts")):
        raise ValueError("Candidate reports must stay in this project's F-drive build/artifacts trees")
    if target.resolve(strict=False) != target or target.exists():
        raise ValueError("Refusing linked destination or replacement of existing evidence")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves earlier failed/successful build evidence.
    with target.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "verify", "check-candidate"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--build-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.action == "check-candidate":
        if args.output is not None or args.manifest is not None or args.package is not None:
            parser.error("check-candidate derives exact build paths and never writes a report")
        print(json.dumps(check_candidate(root, args.build_id or ""), ensure_ascii=True))
        return
    if args.output is None:
        parser.error("snapshot and verify require --output")
    if args.action == "snapshot":
        result = snapshot(root, args.build_id or "")
    else:
        if args.manifest is None or args.package is None:
            parser.error("verify requires --manifest and --package")
        result = verify_bundle(root, json.loads(args.manifest.read_text("utf-8")), args.package.resolve())
    output_json(root, args.output, result)
    print(json.dumps({key: value for key, value in result.items() if key not in {"files", "packageFiles", "pythonModules"}}, ensure_ascii=True))


if __name__ == "__main__":
    main()
