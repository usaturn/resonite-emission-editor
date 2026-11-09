"""Offline native SDK operation evidence, NOT a deploy or layout-success gate.

The operation list includes passive metadata and native slot layout, unlike
the 3D BSON record. Unsent engine defaults, current alias/ownership resolution,
runtime graph comparison and controller recovery remain separate requirements.
All successful Verdicts retain complete/executionPermission/nativeSucceeded=false.
"""
from datetime import UTC, datetime
import json
import os
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
    execution = Path(verify_package(package)["executionRoot"]).absolute()
    for path in (package, directory, library, sdk, execution, EXPORTER):
        if ".." in path.parts:
            raise ValueError("native-layout-parent-traversal")
        _no_links(path)
    for source in (package, execution, REPO_ROOT, library, sdk):
        if directory.is_relative_to(source) or source.is_relative_to(directory):
            raise ValueError("native-layout-output-overlaps-input")
    argv = ["dotnet", str(directory / "tool" / "bin" / "Debug" / "net10.0" / "NativeLayout.dll"),
            str(directory / "sources"), module, str(library),
            str(directory / "bindings.json"), str(directory / "layout.json")]
    return package, directory, library, sdk, argv


def _build_properties(sdk):
    return ["-p:SdkDirectory=" + str(sdk), "-p:ImportDirectoryBuildProps=false",
            "-p:ImportDirectoryBuildTargets=false", "-p:ManagePackageVersionsCentrally=false",
            "-p:NuGetAudit=false", "-p:Configuration=Debug", "-p:UseSharedCompilation=false",
            "-p:ImportUserLocationsByWildcardBeforeMicrosoftCommonProps=false",
            "-p:ImportUserLocationsByWildcardAfterMicrosoftCommonProps=false",
            "-p:ImportUserLocationsByWildcardBeforeMicrosoftCommonTargets=false",
            "-p:ImportUserLocationsByWildcardAfterMicrosoftCommonTargets=false"]


def _environment(directory):
    # MSBuild promotes arbitrary environment names to build properties. A deny
    # list is insufficient; do not inherit compiler/startup hooks or credentials.
    return {**{k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ},
            "LANG": "C.UTF-8", "DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1",
            "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
            "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true",
            "DOTNET_CLI_HOME": str(directory / "dotnet-home"),
            "NUGET_PACKAGES": str(directory / "nuget-packages"),
            "NUGET_HTTP_CACHE_PATH": str(directory / "nuget-http"), "TMPDIR": str(directory / "temp")}


def _build_context(directory):
    # Record SDK selection/ancestor build inputs as provenance, not as an
    # attestation against a malicious SDK. Directory imports are also disabled.
    result = {}
    for parent in (directory / "tool", directory, *directory.parents):
        for name in ("global.json", "Directory.Build.props", "Directory.Build.targets",
                     "Directory.Packages.props", "NuGet.Config", "MSBuild.rsp", "Directory.Build.rsp"):
            path = parent / name
            _no_links(path)
            if path.exists():
                result[str(path)] = _hash(path)
    return result


def _commands(directory, sdk, argv):
    # Use MSBuild directly: unlike dotnet run, its -noAutoResponse is an explicit
    # documented switch. Execute the built DLL separately, never rebuild at run.
    build = ["dotnet", "msbuild", str(directory / "tool" / "NativeLayout.fsproj"),
             "-noAutoResponse", "-nodeReuse:false", *_build_properties(sdk)]
    return [("toolchain", ["dotnet", "--info"]),
            ("restore", [*build, "-target:Restore", "-p:RestoreConfigFile=" + str(directory / "NuGet.Config")]),
            ("build", [*build, "-target:Build"]), ("run", argv)]


def _run_commands(directory, commands, environment):
    results = []
    for label, argv in commands:
        launch_error = None
        try:
            process = subprocess.run(argv, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                     capture_output=True, check=False)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except OSError as exc:
            stdout, stderr, code, launch_error = b"", str(exc).encode(), None, str(exc)
        prefix = "" if label == "run" else label + "."
        _write_new(directory / (prefix + "stdout.bin"), stdout)
        _write_new(directory / (prefix + "stderr.bin"), stderr)
        results.append({"stage": label, "argv": argv, "exitCode": code, "launchError": launch_error})
        if code != 0 or launch_error is not None:
            break
    return results


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
        _write_new(directory / "NuGet.Config", b'<configuration><packageSources><clear /></packageSources></configuration>')
        (directory / "temp").mkdir()
        environment, context_before = _environment(directory), _build_context(directory)
        started = datetime.now(UTC).isoformat()
        commands = _run_commands(directory, _commands(directory, sdk, argv), environment)
        finished = datetime.now(UTC).isoformat()
        try:
            after, context_after, after_error = _inputs(package, library, sdk), _build_context(directory), None
        except (OSError, ValueError, KeyError) as exc:
            after, context_after, after_error = None, None, str(exc)
        receipt = {"module": module, "package": str(package), "library": str(library), "sdk": str(sdk),
                   "argv": argv, "startedAt": started, "finishedAt": finished,
                   "exitCode": commands[-1]["exitCode"], "launchError": commands[-1]["launchError"],
                   "commands": commands, "environment": environment,
                   "buildContextBefore": context_before, "buildContextAfter": context_after,
                   "inputsBefore": before, "inputsAfter": after,
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
        expected_commands = [{"stage": label, "argv": args, "exitCode": 0, "launchError": None}
                             for label, args in _commands(directory, sdk, argv)]
        if (receipt["commands"] != expected_commands
                or any(type(command["exitCode"]) is not int for command in receipt["commands"])
                or receipt["environment"] != _environment(directory)
                or receipt["buildContextBefore"] != _build_context(directory)
                or receipt["buildContextAfter"] != receipt["buildContextBefore"]):
            raise ValueError("native-layout-build-context")
        files = _files(directory)
        files.pop("receipt.json")
        required = {"stdout.bin", "stderr.bin", "layout.json", "bindings.json", "NuGet.Config",
                    "toolchain.stdout.bin", "toolchain.stderr.bin", "restore.stdout.bin", "restore.stderr.bin",
                    "build.stdout.bin", "build.stderr.bin"}
        if not required <= files.keys():
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
