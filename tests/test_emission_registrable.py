"""Safety tests for the Issue #6 native multi-drop registration panel."""

import json
from pathlib import Path

import pytest

from scripts.emission_editor import registrable


def _walk(node):
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


def _components(panel):
    components = {}
    for node in _walk(panel):
        for component in node.get("components", []):
            components[component["key"]] = component
    return components


def _initial(component, member):
    if "initialFields" in component and member in component["initialFields"]:
        return component["initialFields"][member]
    return component.get("fields", {}).get(member)


def test_initial_panel_has_no_external_references():
    """A saved initial panel must not point at an avatar, material, user or fixture.

    Wrong initial references would make a fresh inventory item edit an object
    the user did not register (data loss / wrong target).
    """
    panel = registrable.build_panel(token="test0001", position=[0, 1.5, 1.5])
    components = _components(panel)
    for key in (
        "ui-operator", "ui-selected-root", "ui-pending-root", "ui-null-root",
        "ui-command-user", "ui-recover-target",
    ):
        assert _initial(components[key], "Reference") is None
    for i in range(registrable.POOL_ROWS):
        assert _initial(components[f"rp-{i}-dropped"], "Reference") is None
        assert _initial(components[f"rp-{i}-captured"], "Reference") is None
    assert not any("fixture" in component["key"] for component in components.values())
    assert not any(
        forbidden in component["type"]
        for component in components.values()
        for forbidden in (
            "MeshRenderer", "SkinnedMeshRenderer", "XiexeToonMaterial",
            "BoxMesh", "SolidColorTexture", "StaticTexture2D",
        )
    )
    assert _initial(components["ui-selected-row"], "Value") == -1
    assert _initial(components["ui-runtime-ready"], "Value") is False
    assert _initial(components["registry-ready"], "Value") is False


def test_row_modules_are_isolated():
    """Each row must be wired to its own dropped/captured fields.

    A cross-row binding would write one row's edit into another row's material.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        bundle = registrable.session_manifest(
            rows=registrable.POOL_ROWS,
            world_state=root / "world.json",
            deploy_state=root / "flux.json",
            source_directory=root / "sources",
            source_root=registrable.CODE_ROOT / "flux/emission-editor",
        )
    modules = {manifest["modules"][0]["name"]: manifest["modules"][0] for manifest in bundle["manifests"]}
    for i in range(registrable.POOL_ROWS):
        p = f"rp-{i}"
        for suffix in ("begin", "value", "preview", "command", "presentation"):
            bindings = modules[f"{p}-{suffix}"]["bindings"]
            for target in bindings.values():
                target = target.get("target", "")
                for j in range(registrable.POOL_ROWS):
                    if j != i:
                        assert f"rp-{j}-" not in target, (p, suffix, target)


def test_row_pool_limit_is_rejected():
    """The 16-row limit must not silently create a 17th row without bindings."""
    registrable.build_panel(token="test0002", position=[0, 1.5, 1.5], rows=16)
    with pytest.raises(ValueError):
        registrable.build_panel(token="test0003", position=[0, 1.5, 1.5], rows=17)
    with pytest.raises(ValueError):
        registrable.build_panel(token="test0004", position=[0, 1.5, 1.5], rows=0)


class _FakeDemo:
    """Records the calls create_panel makes; never talks to Resonite."""

    instances = []
    latency_ms = 100.0

    def __init__(self, directory, url, library):
        self.directory = Path(directory)
        self.url = url
        self.library = str(Path(library).resolve())
        self.calls = []
        self.writes = []
        _FakeDemo.instances.append(self)

    def run(self, *args):
        self.calls.append(args)
        if args[0] == "status":
            return {"connected": True, "latencyMs": type(self).latency_ms}
        if args[0] == "doctor":
            return {"ready": True}
        if args[0] == "inspect":
            panel = json.loads((self.directory / "panel.json").read_bytes())
            return {"id": args[1], "name": panel["slot"]["name"], "parentId": "Root"}
        if args[0] == "apply":
            panel = json.loads(Path(args[1]).read_bytes())
            state = Path(args[args.index("--state") + 1])
            state.write_text(json.dumps({
                "ownershipKey": panel["ownership"]["key"],
                "slots": {"emission-root": {"id": "emission-root-id"}},
            }))
            return {}
        if args[0] == "flux" and args[1] == "deploy-manifest":
            manifest = json.loads(Path(args[2]).read_bytes())
            name = manifest["modules"][0]["name"]
            state_path = Path(manifest["deployState"])
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state = json.loads(state_path.read_bytes()) if state_path.exists() else {"modules": {}}
            state.setdefault("modules", {})[name] = {"slotId": "fake"}
            state_path.write_text(json.dumps(state))
            return {"success": True}
        return {}

    def set(self, state, key, member, value):
        self.writes.append((key, member, value))


def test_create_panel_writes_only_ready_flags(monkeypatch, tmp_path):
    """Deploying the initial panel must not write a material or avatar value.

    A registration step that writes a material would change the avatar without
    the user starting an edit. Only the two readiness flags are set after deploy.
    """
    _FakeDemo.instances.clear()
    monkeypatch.setattr(registrable, "Demo", _FakeDemo)
    result = registrable.create_panel(
        tmp_path / "panel", "ws://127.0.0.1:1", tmp_path / "library", [0, 1.5, 1.5]
    )
    assert result["ready"] is True
    client = _FakeDemo.instances[0]
    assert client.writes == [
        ("registry-ready", "Value", True),
        ("ui-runtime-ready", "Value", True),
    ]
    assert any(call[:2] == ("flux", "deploy-manifest") for call in client.calls)


def test_no_modules_then_deploy_and_finalize(monkeypatch, tmp_path):
    """Readiness must stay off until every staged deploy is recorded."""
    _FakeDemo.instances.clear()
    monkeypatch.setattr(registrable, "Demo", _FakeDemo)
    directory = tmp_path / "panel"
    created = registrable.create_panel(
        directory, "ws://127.0.0.1:1", tmp_path / "library", [0, 1.5, 1.5],
        rows=1, deploy_modules=False,
    )
    assert created["ready"] is False
    assert created["modulesDeployed"] == 0
    assert created["modulesTotal"] == 9
    assert not any(call[:2] == ("flux", "deploy-manifest") for call in _FakeDemo.instances[0].calls)
    assert (directory / "module-08.json").exists()

    registrable.deploy_panel(
        directory, "ws://127.0.0.1:1", tmp_path / "library", from_index=0, count=1,
    )
    assert _FakeDemo.instances[-1].writes == []
    registrable.deploy_panel(
        directory, "ws://127.0.0.1:1", tmp_path / "library", from_index=1,
    )
    assert _FakeDemo.instances[-1].writes == []
    registrable.finalize_panel(directory, "ws://127.0.0.1:1", tmp_path / "library")
    assert _FakeDemo.instances[-1].writes == [
        ("registry-ready", "Value", True),
        ("ui-runtime-ready", "Value", True),
    ]
    assert (directory / "finalized.json").exists()


def test_finalize_refuses_missing_modules(monkeypatch, tmp_path):
    """Readiness must not be enabled when a staged deploy is missing."""
    _FakeDemo.instances.clear()
    monkeypatch.setattr(registrable, "Demo", _FakeDemo)
    directory = tmp_path / "panel"
    registrable.create_panel(
        directory, "ws://127.0.0.1:1", tmp_path / "library", [0, 1.5, 1.5],
        rows=1, deploy_modules=False,
    )
    with pytest.raises(ValueError):
        registrable.finalize_panel(directory, "ws://127.0.0.1:1", tmp_path / "library")
    assert _FakeDemo.instances[-1].writes == []


def test_health_check_stops_before_deploy_on_latency_spike(monkeypatch, tmp_path):
    """A latency spike must stop the run before any module is deployed."""
    _FakeDemo.instances.clear()
    monkeypatch.setattr(registrable, "Demo", _FakeDemo)
    monkeypatch.setattr(_FakeDemo, "latency_ms", 5000.0)
    with pytest.raises(RuntimeError):
        registrable.create_panel(
            tmp_path / "panel", "ws://127.0.0.1:1", tmp_path / "library", [0, 1.5, 1.5],
        )
    assert not any(call[:2] == ("flux", "deploy-manifest") for call in _FakeDemo.instances[0].calls)
