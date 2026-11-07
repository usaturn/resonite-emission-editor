"""Offline, copy-only inputs for the short private native Drive gate.

A seal detects accidental corruption, not an adversary rewriting both the seal
and its checksum. It never constitutes native success or permission to execute.
Runtime state paths are reserved, initially absent, and never reused by prepare.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "flux" / "emission-editor" / "drivegate"
MODULES = {"probe": "DriveGateProbe", "color": "ColorDrive", "bool": "BoolDrive"}
FIELDS = {
    "Command": ("int", 0), "Captured": ("bool", False),
    "BeforeColor": ("colorX", [0.0, 0.0, 0.0, 1.0]), "BeforeBool": ("bool", False),
    "ColorTarget": ("colorX", [0.0, 0.0, 0.0, 1.0]), "BoolTarget": ("bool", False),
    "ColorOutcome": ("int", 0), "BoolOutcome": ("int", 0),
    "ColorAfter": ("colorX", [0.0, 0.0, 0.0, 1.0]), "BoolAfter": ("bool", False),
    "Done": ("int", 0),
}
FORMAT = "emission-drivegate-package-v1"


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


def prepare_package(destination: Path, token: str, *, library_path: Path) -> dict:
    """Compile/validate new isolated copies and seal only after every exit is zero.

    Failure leaves diagnostic files but no seal; no overwrite, retry, deploy,
    execution copy, live call, or state adoption is performed here.
    """
    fixture = fixture_declaration(token)
    destination = Path(destination).absolute()
    _no_links(destination)
    destination = destination.resolve()
    source_root = Path(SOURCE_ROOT).absolute()
    _no_links(source_root)
    source_root = source_root.resolve()
    if destination.is_relative_to(source_root) or source_root.is_relative_to(destination):
        raise ValueError("drivegate-output-overlaps-source")
    # Do not allow other authoring files in this checkout to become build outputs.
    if destination.is_relative_to(REPO_ROOT):
        raise ValueError("drivegate-output-inside-checkout")
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
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    root = destination.resolve()
    for directory in ("sources", "manifests", "logs"):
        (root / directory).mkdir()
    for role in MODULES:
        (root / "runtime" / role).mkdir(parents=True)
    _json_new(root / "fixture.json", fixture)
    original = {name: (source_root / (name + ".pg")).read_bytes() for name in MODULES.values()}
    for name, content in original.items():
        with (root / "sources" / (name + ".pg")).open("xb") as stream:
            stream.write(content)
    cli = _offline(root, "cli-version", ["--version"])
    sdk = _offline(root, "sdk-version", ["flux", "status"])
    if sdk.get("available") is not True or not sdk.get("version") or not cli.get("version"):
        raise ValueError("drivegate-tool-version-missing")
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
    for role, manifest in _manifests(root).items():
        path = root / "manifests" / (role + ".json")
        _json_new(path, manifest)
        validation = _offline(root, role + "-manifest", ["flux", "validate-manifest", str(path)])
        if validation.get("valid") is not True:
            raise ValueError("drivegate-manifest-validation-failed")
        if any(Path(manifest[k]).exists() for k in ("worldState", "deployState")):
            raise ValueError("drivegate-runtime-state-exists")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, check=True).stdout.decode().strip()
    seal = {
        "format": FORMAT, "token": token, "codeCommit": commit,
        "cliVersion": cli["version"], "sdkVersion": sdk["version"], "dllSha256": dlls,
        "complete": False, "executionPermission": False, "nativeSucceeded": False,
        "files": _files(root),
    }
    _json_new(root / "seal.json", seal)
    with (root / "seal.sha256").open("x") as stream:
        stream.write(_hash(root / "seal.json") + "\n")
    return verify_package(root)


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
        for role, manifest in _manifests(root.resolve()).items():
            if json.loads((root / "manifests" / (role + ".json")).read_bytes()) != manifest:
                raise ValueError("drivegate-manifest-changed")
            if any(Path(manifest[k]).exists() for k in ("worldState", "deployState")):
                raise ValueError("drivegate-runtime-state-exists")
        for name in MODULES.values():
            if not {"sources/" + name + suffix for suffix in (".pg", ".pg.brson")} <= inventory.keys():
                raise ValueError("drivegate-required-artifact-missing")
        return seal
    except (KeyError, TypeError, json.JSONDecodeError, FileNotFoundError) as exc:
        raise ValueError("drivegate-package-invalid") from exc
