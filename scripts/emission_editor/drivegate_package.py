"""Offline, copy-only inputs for the short private native Drive gate.

A seal detects accidental corruption, not an adversary rewriting both the seal
and its checksum. It never constitutes native success or permission to execute.
Runtime paths belong to a separate, initially fresh execution area, never the seal.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "flux" / "emission-editor" / "drivegate"
MODULES = {"probe": "DriveGateProbe", "color": "ColorDrive", "bool": "BoolDrive"}
FIELDS = {
    "Command": ("int", 0), "Captured": ("bool", False),
    "BeforeColor": ("colorX", [0.0, 0.0, 0.0, 1.0]), "BeforeBool": ("bool", False),
    # Distinct from Before and C/false: skipped capture/restore must be visible.
    "ColorTarget": ("colorX", [4.25, 0.5, 1.125, 0.3125]), "BoolTarget": ("bool", True),
    "ColorOutcome": ("int", 0), "BoolOutcome": ("int", 0),
    "ColorAfter": ("colorX", [0.0, 0.0, 0.0, 1.0]), "BoolAfter": ("bool", False),
    "Done": ("int", 0),
}
FORMAT = "emission-drivegate-package-v3"
GENERATED_CONTENT_KEY = "drivegate-root/component:FrooxEngine.AI_GeneratedContent:0"
GENERATED_CONTENT_TYPE = "FrooxEngine.AI_GeneratedContent"


def fixture_declaration(token: str) -> dict:
    """Only private primitive fields and stable module parents; no external IDs."""
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", token):
        raise ValueError("invalid-drivegate-token")
    fields = [
        {"key": "drivegate-" + name, "type": "[FrooxEngine]FrooxEngine.ValueField<" + typ + ">",
         "initialFields": {"Value": value.copy() if isinstance(value, list) else value}}
        for name, (typ, value) in FIELDS.items()
    ]
    return {
        "schemaVersion": "1", "ownership": {"key": "emission-drivegate-" + token},
        "slot": {"key": "drivegate-root", "name": "ResoLoop_Test_EmissionEditor_Drivegate_" + token,
                 "parent": "Root"},
        "components": [{"key": "drivegate-objectroot", "type": "[FrooxEngine]FrooxEngine.ObjectRoot"}],
        "children": [
            {"slot": {"key": "drivegate-fields", "name": "Private fields"}, "components": fields, "children": []},
            *[{"slot": {"key": "drivegate-" + role, "name": name}, "components": [], "children": []}
              for role, name in MODULES.items()],
        ],
    }


def normalized_fixture(token: str, cli_version: str) -> dict:
    """Expected installed normalization, NOT a declaration to pass to apply.

    ResoLoop 0.2.0 GeneratedContentMetadata.AddToGeneratedRoots appends one
    unkeyed marker to this root. WorldService derives its stable key from the
    bare type and ordinal. No child in this private fixture is a portable root.
    Fail closed for a CLI whose normalization has not been characterized.
    """
    if cli_version != "0.2.0":
        raise ValueError("drivegate-uncharacterized-normalization")
    fixture = fixture_declaration(token)
    fixture["components"].append({
        "key": GENERATED_CONTENT_KEY, "type": GENERATED_CONTENT_TYPE,
        "fields": {"Source": "[resoloop " + cli_version + "]"},
    })
    return fixture


def _manifests(root: Path) -> dict:
    def binding(name, mode="source"):
        return {"target": "$member:drivegate-" + name + ".Value", "mode": mode}

    result = {}
    for role, name in MODULES.items():
        bindings = ({field: binding(field) for field in FIELDS} if role == "probe" else
                    {"Driven": binding("ColorTarget" if role == "color" else "BoolTarget", "drive")})
        result[role] = {
            "schemaVersion": "1", "projectDirectory": str(root / "sources"),
            "parent": "$slot:drivegate-" + role,
            "worldState": str(root / "runtime" / role / "world.json"),
            "deployState": str(root / "runtime" / role / "flux.json"),
            "modules": [{"name": "drivegate-" + role, "source": str(root / "sources" / (name + ".pg")),
                         "module": name, "dependsOn": [], "bindings": bindings}],
        }
    return result


def _no_links(path: Path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("drivegate-symlink-path")


def _json_new(path: Path, data):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files(root: Path) -> dict:
    result = {}
    _no_links(root)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("drivegate-symlink-artifact")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("drivegate-nonregular-artifact")
        result[path.relative_to(root).as_posix()] = _hash(path)
    return result


def _offline(root: Path, label: str, args: list[str], *, compiler=False) -> dict:
    # Deliberately not a general command runner: all invocations are constructed
    # below, from the fixed offline version/check/build/manifest allowlist.
    result = subprocess.run(["resoloop", *args, "--json"], cwd=root, capture_output=True)
    (root / "logs" / (label + ".stdout")).write_bytes(result.stdout)
    (root / "logs" / (label + ".stderr")).write_bytes(result.stderr)
    _json_new(root / "logs" / (label + ".exit.json"), {"exitCode": result.returncode})
    try:
        envelope = json.loads(result.stdout)
        data = envelope["data"]
        if result.returncode != 0 or envelope.get("ok") is not True or not isinstance(data, dict):
            raise ValueError("offline-command-failed")
        if compiler and (data.get("success") is not True or type(data.get("exitCode")) is not int
                         or data["exitCode"] != 0):
            raise ValueError("offline-compiler-failed")
        if data.get("valid") is False or data.get("success") is False:
            raise ValueError("offline-validation-failed")
        return data
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("offline-invalid-response") from exc


def _code_commit() -> str:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
                            capture_output=True, check=True).stdout.decode().strip()
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=normal"],
                            cwd=REPO_ROOT, capture_output=True, check=True)
    if status.stdout:
        raise ValueError("drivegate-dirty-code-checkout")
    return commit


def _output_path(path: Path, source_root: Path) -> Path:
    path = Path(path).absolute()
    _no_links(path)
    path = path.resolve()
    if path.is_relative_to(source_root) or source_root.is_relative_to(path):
        raise ValueError("drivegate-output-overlaps-source")
    if path.is_relative_to(REPO_ROOT):
        raise ValueError("drivegate-output-inside-checkout")
    if path.exists():
        raise FileExistsError(path)
    return path


def prepare_package(destination: Path, token: str, *, library_path: Path) -> dict:
    """Compile/validate new isolated copies and seal only after every exit is zero.

    Also reserve a fresh sibling <destination>-execution containing deploy inputs.
    Failure leaves diagnostics but no seal; no retry, deploy or live call occurs.
    """
    fixture = fixture_declaration(token)
    source_root = Path(SOURCE_ROOT).absolute()
    _no_links(source_root)
    source_root = source_root.resolve()
    destination = _output_path(destination, source_root)
    execution = _output_path(destination.with_name(destination.name + "-execution"), source_root)
    for name in MODULES.values():
        source = source_root / (name + ".pg")
        _no_links(source)
        if not source.is_file():
            raise ValueError("drivegate-source-missing")
    library_path = Path(library_path).resolve(strict=True)
    if not library_path.is_dir():
        raise ValueError("drivegate-library-not-directory")
    dlls = {p.name: _hash(p) for p in sorted(library_path.glob("*.dll")) if p.is_file()}
    if not dlls:
        raise ValueError("drivegate-library-empty")
    commit = _code_commit()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    execution.mkdir(mode=0o700, exist_ok=False)
    root = destination.resolve()
    for directory in ("sources", "manifests", "logs"):
        (root / directory).mkdir()
    _json_new(root / "fixture.json", fixture)
    original = {name: (source_root / (name + ".pg")).read_bytes() for name in MODULES.values()}
    for name, content in original.items():
        with (root / "sources" / (name + ".pg")).open("xb") as stream:
            stream.write(content)
    cli = _offline(root, "cli-version", ["--version"])
    sdk = _offline(root, "sdk-version", ["flux", "status"])
    if sdk.get("available") is not True or not sdk.get("version") or not cli.get("version"):
        raise ValueError("drivegate-tool-version-missing")
    normalized = normalized_fixture(token, cli["version"])
    for name in MODULES.values():
        path = root / "sources" / (name + ".pg")
        for action in ("check", "build"):
            _offline(root, name + "-" + action,
                     ["flux", action, str(path), "--library-path", str(library_path)], compiler=True)
        record = path.with_suffix(".pg.brson")
        if not record.is_file() or record.is_symlink() or not record.stat().st_size:
            raise ValueError("drivegate-record-missing")
        if path.read_bytes() != original[name] or (source_root / path.name).read_bytes() != original[name]:
            raise ValueError("drivegate-source-changed")
    shutil.copytree(root / "sources", execution / "sources")
    shutil.copyfile(root / "fixture.json", execution / "fixture.json")
    (execution / "manifests").mkdir()
    for role in MODULES:
        (execution / "runtime" / role).mkdir(parents=True)
    for role, manifest in _manifests(execution).items():
        path = root / "manifests" / (role + ".json")
        _json_new(path, manifest)
        _json_new(execution / "manifests" / (role + ".json"), manifest)
        validation = _offline(root, role + "-manifest", ["flux", "validate-manifest", str(path)])
        if validation.get("valid") is not True:
            raise ValueError("drivegate-manifest-validation-failed")
        if any(Path(manifest[k]).exists() for k in ("worldState", "deployState")):
            raise ValueError("drivegate-runtime-state-exists")
    if _code_commit() != commit:
        raise ValueError("drivegate-code-commit-changed")
    seal = {
        "format": FORMAT, "token": token, "codeCommit": commit, "executionRoot": str(execution),
        "cliVersion": cli["version"], "sdkVersion": sdk["version"], "dllSha256": dlls,
        "normalizedFixture": normalized,
        "complete": False, "executionPermission": False, "nativeSucceeded": False,
        "files": _files(root),
    }
    _verify_execution_files(execution, seal["files"])
    _json_new(root / "seal.json", seal)
    with (root / "seal.sha256").open("x") as stream:
        stream.write(_hash(root / "seal.json") + "\n")
    return verify_execution(root, execution)


def verify_package(path: Path) -> dict:
    """Reject any missing/changed/added/link artifact, including reserved state."""
    root = Path(path).absolute()
    _no_links(root)
    root = root.resolve()
    files = _files(root)
    try:
        if (root / "seal.sha256").read_text().strip() != _hash(root / "seal.json"):
            raise ValueError("drivegate-seal-changed")
        seal = json.loads((root / "seal.json").read_bytes())
        inventory = seal["files"]
        if not isinstance(inventory, dict) or not inventory:
            raise ValueError("drivegate-inventory-invalid")
        for name, digest in inventory.items():
            relative = Path(name)
            if (relative.is_absolute() or relative.as_posix() != name or ".." in relative.parts
                    or name in ("seal.json", "seal.sha256") or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise ValueError("drivegate-inventory-path-or-hash")
        if {k: v for k, v in files.items() if k not in ("seal.json", "seal.sha256")} != inventory:
            raise ValueError("drivegate-file-set-or-hash-changed")
        if seal["format"] != FORMAT or any(seal[k] is not False for k in ("complete", "executionPermission", "nativeSucceeded")):
            raise ValueError("drivegate-seal-invalid")
        if json.loads((root / "fixture.json").read_bytes()) != fixture_declaration(seal["token"]):
            raise ValueError("drivegate-fixture-changed")
        if seal["normalizedFixture"] != normalized_fixture(seal["token"], seal["cliVersion"]):
            raise ValueError("drivegate-normalization-changed")
        execution = Path(seal["executionRoot"])
        if (not execution.is_absolute() or execution != execution.resolve()
                or execution.is_relative_to(root) or root.is_relative_to(execution)):
            raise ValueError("drivegate-execution-overlaps-seal")
        for role, manifest in _manifests(execution).items():
            if json.loads((root / "manifests" / (role + ".json")).read_bytes()) != manifest:
                raise ValueError("drivegate-manifest-changed")
        for name in MODULES.values():
            if not {"sources/" + name + suffix for suffix in (".pg", ".pg.brson")} <= inventory.keys():
                raise ValueError("drivegate-required-artifact-missing")
        return seal
    except (KeyError, TypeError, json.JSONDecodeError, FileNotFoundError) as exc:
        raise ValueError("drivegate-package-invalid") from exc


def _verify_execution_files(execution: Path, inventory: dict):
    expected = {name: digest for name, digest in inventory.items()
                if name == "fixture.json" or name.startswith(("sources/", "manifests/"))}
    if _files(execution) != expected:
        raise ValueError("drivegate-execution-file-set-or-hash-changed")


def verify_execution(package_path: Path, execution_path: Path) -> dict:
    """Compare a fresh execution area to sealed expectations, never grant permission.

    Call immediately before handing its manifests to any separately authorized
    consumer. Rebuilt records must still match; state/unknown files are rejected.
    This is not a runtime receipt verifier or a concurrent-writer lock.
    """
    seal = verify_package(package_path)
    execution = Path(execution_path).absolute()
    _no_links(execution)
    execution = execution.resolve()
    if str(execution) != seal["executionRoot"]:
        raise ValueError("drivegate-wrong-execution-area")
    _verify_execution_files(execution, seal["files"])
    return seal
