"""Offline native SDK operation evidence, NOT a deploy or layout-success gate.

The operation list includes passive metadata and native slot layout, unlike
the 3D BSON record. Unsent engine defaults, current alias/ownership resolution,
runtime graph comparison and controller recovery remain separate requirements.
All successful Verdicts retain complete/executionPermission/nativeSucceeded=false.
"""
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess

from .drivegate_evidence import Verdict
from .drivegate_package import MODULES, FIELDS, REPO_ROOT, _files, _hash, _no_links, verify_package
from .read_receipts import _decode, _write_new

EXPORTER = REPO_ROOT / "tools" / "drivegate-native-layout"
TOOL_FILES = ("NativeLayout.fsproj", "Program.fs")


def _bindings(module):
    if module not in MODULES.values():
        raise ValueError("native-layout-unknown-module")
    return {"inputs": {name: "private:" + name for name in FIELDS} if module == "DriveGateProbe" else {},
            "outputs": {} if module == "DriveGateProbe" else {"Driven": "private:Driven"}}


def _dlls(directory):
    _no_links(directory)
    if not directory.is_dir():
        raise ValueError("native-layout-library-directory")
    result = {}
    for path in sorted(directory.glob("*.dll")):
        _no_links(path)
        if not path.is_file():
            raise ValueError("native-layout-nonregular-library")
        result[path.name] = _hash(path)
    if not result:
        raise ValueError("native-layout-empty-library-inventory")
    return result


def _inputs(package, library, sdk):
    seal = verify_package(package)
    native = _dlls(library)
    if native != seal["dllSha256"]:
        raise ValueError("native-layout-native-library-mismatch")
    return {"sealSha256": _hash(package / "seal.json"),
            "sources": {m + ".pg": _hash(package / "sources" / (m + ".pg")) for m in MODULES.values()},
            "exporter": {name: _hash(EXPORTER / name) for name in TOOL_FILES},
            "sdkDllSha256": _dlls(sdk), "nativeDllSha256": native}


def _paths(package, module, directory, library, sdk):
    _bindings(module)
    package, directory, library, sdk = [Path(p).absolute() for p in (package, directory, library, sdk)]
    for path in (package, directory, library, sdk, EXPORTER):
        _no_links(path)
    execution = Path(verify_package(package)["executionRoot"])
    for source in (package, execution, REPO_ROOT, library, sdk):
        if directory.is_relative_to(source) or source.is_relative_to(directory):
            raise ValueError("native-layout-output-overlaps-input")
    argv = ["dotnet", "run", "--project", str(directory / "tool" / "NativeLayout.fsproj"),
            "-p:SdkDirectory=" + str(sdk), "--", str(directory / "sources"), module,
            str(library), str(directory / "bindings.json"), str(directory / "layout.json")]
    return package, directory, library, sdk, argv


def _validate_document(document, module, sdk_version):
    if (document.get("format") != "emission-native-layout-v1" or document.get("module") != module
            or document.get("sdkVersion") != sdk_version):
        raise ValueError("native-layout-producer-identity")
    operations = document["operations"]
    if (not isinstance(operations, list) or not operations
            or type(document.get("operationCount")) is not int or document["operationCount"] != len(operations)):
        raise ValueError("native-layout-operation-coverage")
    ids, slots, targets = set(), set(), []
    roots = 0

    def members(value):
        if isinstance(value, dict):
            ident = value.get("id")
            if ident is not None:
                if not isinstance(ident, str) or not ident or ident in ids:
                    raise ValueError("native-layout-duplicate-identity")
                ids.add(ident)
            if "targetId" in value and value["targetId"] is not None:
                targets.append(value["targetId"])
            for key, child in value.items():
                if key != "id":
                    members(child)
        elif isinstance(value, list):
            for child in value:
                members(child)

    for operation in operations:
        data = operation["data"]
        ident = data["id"]
        if not isinstance(ident, str) or not ident:
            raise ValueError("native-layout-missing-identity")
        kind = operation.get("operation")
        if kind == "AddSlot":
            parent = data["parent"]["targetId"]
            if parent == "native-layout-parent":
                roots += 1
                if data["name"]["value"] != module:
                    raise ValueError("native-layout-module-root")
            elif parent not in slots:
                raise ValueError("native-layout-slot-parent")
            slots.add(ident)
        elif kind == "AddComponent":
            if operation.get("containerSlotId") not in slots:
                raise ValueError("native-layout-component-parent")
            if not isinstance(data.get("componentType"), str) or not isinstance(data.get("members"), dict):
                raise ValueError("native-layout-component-shape")
        else:
            raise ValueError("native-layout-unknown-operation")
        members(data)
    bindings = _bindings(module)
    symbols = set(bindings["inputs"].values()) | set(bindings["outputs"].values())
    if (roots != 1 or targets.count("native-layout-parent") != 1
            or any(not isinstance(t, str) or t not in ids | symbols | {"native-layout-parent"} for t in targets)):
        raise ValueError("native-layout-foreign-reference")
    if not symbols <= set(targets) or ids & (symbols | {"native-layout-parent"}):
        raise ValueError("native-layout-port-coverage")


def collect_native_layout(package: Path, module: str, directory: Path, *,
                          library_path: Path, sdk_directory: Path) -> Verdict:
    """Build operation evidence from copied sealed PGs, with NO native connection.

    Uses a fixed exporter, not a caller-supplied command. Does not overwrite/reuse
    an output directory, modify a package, deploy, or retry an ambiguous mutation.
    Failed attempts remain separate. The caller may diagnose and start a new one.
    """
    try:
        package, directory, library, sdk, argv = _paths(package, module, directory, library_path, sdk_directory)
        before = _inputs(package, library, sdk)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        (directory / "tool").mkdir()
        (directory / "sources").mkdir()
        for name in TOOL_FILES:
            shutil.copyfile(EXPORTER / name, directory / "tool" / name)
        for name in before["sources"]:
            shutil.copyfile(package / "sources" / name, directory / "sources" / name)
        _write_new(directory / "bindings.json", json.dumps(_bindings(module)).encode())
        started, launch_error = datetime.now(UTC).isoformat(), None
        try:
            process = subprocess.run(argv, cwd=directory, stdin=subprocess.DEVNULL, capture_output=True, check=False)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except OSError as exc:
            stdout, stderr, code, launch_error = b"", str(exc).encode(), None, str(exc)
        finished = datetime.now(UTC).isoformat()
        _write_new(directory / "stdout.bin", stdout)
        _write_new(directory / "stderr.bin", stderr)
        try:
            after, after_error = _inputs(package, library, sdk), None
        except (OSError, ValueError, KeyError) as exc:
            after, after_error = None, str(exc)
        receipt = {"module": module, "package": str(package), "library": str(library), "sdk": str(sdk),
                   "argv": argv, "startedAt": started, "finishedAt": finished, "exitCode": code,
                   "launchError": launch_error, "inputsBefore": before, "inputsAfter": after,
                   "afterError": after_error, "files": _files(directory)}
        _write_new(directory / "receipt.json", json.dumps(receipt, ensure_ascii=False, indent=2).encode())
        return load_native_layout(package, module, directory, library_path=library, sdk_directory=sdk)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Verdict("failed", ("native-layout:" + str(exc),))


def load_native_layout(package: Path, module: str, directory: Path, *,
                       library_path: Path, sdk_directory: Path) -> Verdict:
    """Reload original producer evidence; PASS means offline generation only."""
    try:
        package, directory, library, sdk, argv = _paths(package, module, directory, library_path, sdk_directory)
        current = _inputs(package, library, sdk)
        receipt = _decode((directory / "receipt.json").read_bytes())
        if (receipt["module"] != module or receipt["argv"] != argv or receipt["package"] != str(package)
                or receipt["library"] != str(library) or receipt["sdk"] != str(sdk)):
            raise ValueError("native-layout-request-mismatch")
        if type(receipt["exitCode"]) is not int or receipt["exitCode"] != 0 or receipt["launchError"] is not None:
            raise ValueError("native-layout-producer-failed")
        if receipt["inputsBefore"] != current or receipt["inputsAfter"] != current or receipt["afterError"] is not None:
            raise ValueError("native-layout-input-changed")
        files = _files(directory)
        files.pop("receipt.json")
        if not {"stdout.bin", "stderr.bin", "layout.json", "bindings.json"} <= files.keys():
            raise ValueError("native-layout-missing-original-output")
        if files != receipt["files"]:
            raise ValueError("native-layout-artifact-changed")
        for name, expected in current["sources"].items():
            if _hash(directory / "sources" / name) != expected:
                raise ValueError("native-layout-source-copy")
        for name, expected in current["exporter"].items():
            if _hash(directory / "tool" / name) != expected:
                raise ValueError("native-layout-exporter-copy")
        if _decode((directory / "bindings.json").read_bytes()) != _bindings(module):
            raise ValueError("native-layout-private-binding-request")
        document = _decode((directory / "layout.json").read_bytes())
        _validate_document(document, module, verify_package(package)["sdkVersion"])
        return Verdict("passed", data=(document,))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return Verdict("failed", ("native-layout:" + str(exc),))
