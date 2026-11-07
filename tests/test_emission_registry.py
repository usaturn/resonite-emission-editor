"""S: mistaken identities or incomplete observations would enroll wrong targets silently."""

from dataclasses import replace

AVATAR_MARKERS = [
    {"id": "avatar-marker", "type": "[FrooxEngine]FrooxEngine.CommonAvatar.AvatarRoot"}
]

from scripts.emission_editor.model import (
    Avatar,
    Material,
    MaterialUse,
    RegistrationCapture,
    plan_registry,
)


def shared_capture():
    return RegistrationCapture(
        session="live-session",
        generation="generation-1",
        observed_at="2026-11-03T11:00:00Z",
        avatars=(
            Avatar("avatar-a", "Plum", ("avatar-a", "dress-1", "dress-2")),
            Avatar("avatar-b", "Plum", ("avatar-b", "dress-3", "sleeve")),
        ),
        materials=(
            Material(
                "shared-material",
                "Emission",
                "field-a",
                {
                    "r": 1.2791259,
                    "g": 15.484132,
                    "b": 271.17404,
                    "a": 0.37,
                    "profile": "sRGB",
                },
            ),
            Material(
                "other-material",
                "Emission",
                "field-b",
                {"r": 0, "g": 1, "b": 2, "a": 0.2, "profile": "Linear"},
            ),
        ),
        uses=tuple(
            MaterialUse(mesh, mesh, "renderer-" + mesh, 0, "binding-" + mesh, material)
            for mesh, material in (
                ("dress-1", "shared-material"),
                ("dress-2", "shared-material"),
                ("dress-3", "shared-material"),
                ("external-mesh", "shared-material"),
                ("sleeve", "other-material"),
            )
        ),
        complete=True,
        issues=(),
    )


def test_reference_identity_groups_shared_materials():
    plan = plan_registry(shared_capture())
    assert len(plan.rows) == 2
    assert len({row.material_ref for row in plan.rows}) == 2
    shared = next(row for row in plan.rows if row.material_ref == "shared-material")
    assert set(shared.mesh_refs) == {"dress-1", "dress-2", "dress-3", "external-mesh"}
    assert shared.external_mesh_refs("avatar-a") == ("dress-3", "external-mesh")
    assert shared.external_mesh_refs("avatar-b") == (
        "dress-1",
        "dress-2",
        "external-mesh",
    )
    assert shared.color["a"] == 0.37 and shared.color["r"] == 1.2791259


def test_plan_registry_refuses_row_with_more_than_16_uses():
    """S: truncating a row to the 16 guard ports would leave the rest unchecked."""
    base = shared_capture()

    def plan_with(count):
        uses = tuple(
            MaterialUse(f"mesh-{k}", "Mesh", f"renderer-{k}", 0, f"binding-{k}", "shared-material")
            for k in range(count)
        )
        return plan_registry(replace(base, uses=uses + base.uses[-1:]))

    assert plan_with(16).complete
    refused = plan_with(17)
    assert not refused.complete
    assert "row-use-limit:shared-material" in refused.issues


def test_incomplete_capture_keeps_plan_ineligible():
    capture = replace(shared_capture(), complete=False, issues=("slot-limit",))
    assert not plan_registry(capture).complete


def test_capture_rejects_wrong_roots_limits_and_changed_observation():
    from scripts.emission_editor.resoloop_io import Limits, capture_registration

    class Read:
        session = "live"
        reads = 0

        def hierarchy(self, depth):
            self.reads += 1
            return {
                "id": "Root",
                "name": "Root",
                "components": [],
                "children": [
                    {
                        "id": "avatar",
                        "name": "Plum",
                        "components": AVATAR_MARKERS,
                        "children": [],
                    },
                    {
                        "id": "user",
                        "name": "User",
                        "components": [
                            {"id": "u", "type": "[FrooxEngine]FrooxEngine.UserRoot"}
                        ],
                        "children": [],
                    },
                ],
            }

        def owned_roots(self):
            return ()

        def material_list_types(self, types):
            return set()

        def component(self, ref):
            raise AssertionError("no components should be read")

    port = Read()
    assert capture_registration(port, ("avatar",), Limits()).complete
    for refs, limits in (
        (("Root",), Limits()),
        (("user",), Limits()),
        (("missing",), Limits()),
        (("avatar",), Limits(slots=1)),
    ):
        assert not capture_registration(Read(), refs, limits).complete

    class Changed(Read):
        def hierarchy(self, depth):
            result = super().hierarchy(depth)
            if self.reads > 1:
                result["children"][0]["id"] = "replacement"
            return result

    assert not capture_registration(Changed(), ("avatar",), Limits()).complete


def test_capture_uses_reflected_material_lists_not_class_names():
    """A custom renderer name must not silently hide an external sharing use."""
    from scripts.emission_editor.resoloop_io import Limits, capture_registration

    class Read:
        session = "native-session"

        def hierarchy(self, depth):
            return {
                "id": "Root",
                "name": "Root",
                "children": [
                    {
                        "id": "avatar",
                        "name": "Plum",
                        "components": AVATAR_MARKERS,
                        "children": [
                            {
                                "id": "dress",
                                "name": "日本語Dress",
                                "components": [{"id": "r1", "type": "CustomMesh"}],
                            }
                        ],
                    },
                    {
                        "id": "external",
                        "name": "SharedOutside",
                        "components": [{"id": "r2", "type": "CustomMesh"}],
                    },
                    {
                        "id": "material-slot",
                        "name": "Material",
                        "components": [
                            {
                                "id": "material",
                                "type": "[FrooxEngine]FrooxEngine.XiexeToonMaterial",
                            }
                        ],
                    },
                ],
            }

        def owned_roots(self):
            return ()

        def material_list_types(self, types):
            return {"CustomMesh"}

        def component(self, ref):
            if ref in ("r1", "r2"):
                return {
                    "id": ref,
                    "type": "CustomMesh",
                    "members": {
                        "Materials": {
                            "kind": "list",
                            "id": "list-" + ref,
                            "elements": [
                                {
                                    "id": "binding-" + ref,
                                    "kind": "reference",
                                    "targetId": "material",
                                }
                            ],
                        }
                    },
                }
            return {
                "id": ref,
                "type": "[FrooxEngine]FrooxEngine.XiexeToonMaterial",
                "members": {
                    "EmissionColor": {
                        "kind": "field",
                        "id": "color-field",
                        "type": "ResoniteLink.colorX",
                        "value": {
                            "r": 1,
                            "g": 2,
                            "b": 3,
                            "a": 0.37,
                            "profile": "Linear",
                        },
                    }
                },
            }

    capture = capture_registration(Read(), ("avatar",), Limits())
    plan = plan_registry(capture)
    assert capture.complete
    assert plan.rows[0].mesh_refs == ("dress", "external")
    assert plan.rows[0].external_mesh_refs("avatar") == ("external",)


def test_owned_boundary_uses_verified_reference_even_after_rename():
    """Renaming the tool must not make it eligible as an avatar; a name collision is distinct."""
    from scripts.emission_editor.resoloop_io import Limits, capture_registration

    class Read:
        session = "native"

        def hierarchy(self, depth):
            return {
                "id": "Root",
                "name": "Root",
                "children": [
                    {"id": "owned-tool", "name": "Renamed tool"},
                    {
                        "id": "avatar",
                        "name": "ResoLoop_Test_EmissionEditor",
                        "components": AVATAR_MARKERS,
                    },
                ],
            }

        def owned_roots(self):
            return ("owned-tool",)

        def material_list_types(self, types):
            return set()

    assert not capture_registration(Read(), ("owned-tool",), Limits()).complete
    assert capture_registration(Read(), ("avatar",), Limits()).complete


def test_missing_reflected_material_payload_cannot_be_called_complete():
    """Omitted list data is unknown, not an empty material assignment."""
    from scripts.emission_editor.resoloop_io import Limits, capture_registration

    class Read:
        session = "native"

        def __init__(self, payload):
            self.payload = payload

        def hierarchy(self, depth):
            return {
                "id": "Root",
                "name": "Root",
                "children": [
                    {
                        "id": "avatar",
                        "name": "Plum",
                        "components": AVATAR_MARKERS
                        + [{"id": "renderer", "type": "ActualRenderer"}],
                    }
                ],
            }

        def owned_roots(self):
            return ()

        def material_list_types(self, types):
            return {"ActualRenderer"}

        def component(self, ref):
            return {"id": ref, "type": "ActualRenderer", "members": self.payload}

    for payload in ({}, {"Materials": {"kind": "list"}}):
        capture = capture_registration(Read(payload), ("avatar",), Limits())
        assert not capture.complete
    assert capture_registration(
        Read({"Materials": {"kind": "list", "elements": []}}), ("avatar",), Limits()
    ).complete


def test_unavailable_type_metadata_requires_every_current_instance_payload(tmp_path):
    """S: unavailable class metadata must not silently hide a renderer's sharing."""
    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopReadPort

    type_name = "RuntimeTypeWithBrokenMetadata"

    def port_for(members):
        port = object.__new__(ResoloopReadPort)
        port._material_type_cache = {}
        port._observed_components = {"actual-component": type_name}
        port.evidence = tmp_path
        port.run = lambda *args, **kwargs: {
            "verified": False,
            "types": [{"requestedType": type_name, "verified": False, "members": []}],
        }
        port.component = lambda ref: {"id": ref, "type": type_name, "members": members}
        return port

    assert port_for(
        {"Materials": {"kind": "list", "elements": []}}
    ).material_list_types({type_name}) == {type_name}
    with pytest.raises(ValueError, match="instance-metadata-incomplete"):
        port_for(None).material_list_types({type_name})
    with pytest.raises(ValueError, match="instance-metadata-incomplete"):
        port_for(
            {"Name": {"kind": "field", "error": "read-denied"}}
        ).material_list_types({type_name})


def test_reflection_fallback_never_accepts_permission_or_unknown_errors(
    tmp_path, monkeypatch
):
    """S: the single known metadata NRE must not make an access refusal look complete."""
    import json
    from types import SimpleNamespace

    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopReadPort

    port = object.__new__(ResoloopReadPort)
    port.sequence = 0
    port.url = "ws://localhost:19652"
    port.evidence = tmp_path
    report = {
        "verified": False,
        "types": [],
        "differences": [
            {
                "code": "COMPONENT_TYPE_NOT_FOUND",
                "actual": "Object reference not set to an instance of an object.",
            }
        ],
    }
    response = {
        "ok": False,
        "error": {"code": "REFLECTION_CHECK_FAILED", "context": {"result": report}},
    }
    monkeypatch.setattr(
        "scripts.emission_editor.resoloop_io.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=6, stdout="", stderr=json.dumps(response)
        ),
    )
    assert port.run("type", "query", accept_missing_members=True) == report
    report["differences"] = [{"code": "ACCESS_DENIED"}]
    with pytest.raises(RuntimeError):
        port.run("type", "query", accept_missing_members=True)


def test_batch_reads_account_for_all_expected_component_identities(monkeypatch):
    """S: a partial filtered scan must not drop a known external sharing renderer."""
    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopReadPort

    port = object.__new__(ResoloopReadPort)
    port._observed_components = {"r1": "RendererType", "r2": "RendererType"}
    port._component_cache = {}
    records = [
        {
            "slotId": "mesh1",
            "component": {"id": "r1", "type": "RendererType", "members": {}},
        }
    ]
    port.run = lambda *args: {"count": len(records), "components": records}
    with pytest.raises(ValueError, match="batch-component-observation-incomplete"):
        port.prefetch_components(("r1", "r2"))
    records.append(
        {
            "slotId": "mesh2",
            "component": {"id": "r2", "type": "RendererType", "members": {}},
        }
    )
    port.prefetch_components(("r1", "r2"))
    assert port.component("r2")["id"] == "r2"


def test_capture_requires_avatar_marker_and_deduplicates_multiple_markers():
    """S: arbitrary renderable slots must never become editable registered avatars."""
    from scripts.emission_editor.resoloop_io import Limits, capture_registration

    class Read:
        session = "live"
        markers = ()

        def hierarchy(self, depth):
            return {
                "id": "Root",
                "name": "Root",
                "children": [
                    {"id": "avatar", "name": "Plum", "components": self.markers}
                ],
            }

        def owned_roots(self):
            return ()

        def material_list_types(self, types):
            return set()

    port = Read()
    assert not capture_registration(port, ("avatar",), Limits()).complete
    port.markers = AVATAR_MARKERS + [
        {"id": "second-marker", "type": AVATAR_MARKERS[0]["type"]}
    ]
    result = capture_registration(port, ("avatar",), Limits())
    assert result.complete and len(result.avatars) == 1


def test_ui_membership_uses_actual_mesh_owners_not_all_registered_avatars(tmp_path):
    """S: a row belonging only to avatar B must not be editable when A is selected."""
    from scripts.emission_editor.ui import membership_indices, session_manifest

    plan = plan_registry(shared_capture())
    shared, other = plan.rows
    assert membership_indices(shared) == [0, 1]
    assert membership_indices(other) == [1]
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    manifest = session_manifest(
        plan, world_state="current-world.json", deploy_state="current-flux.json",
        source_directory=tmp_path / "sources", source_root=root / "flux/emission-editor",
    )
    view = next(m for part in manifest["manifests"] for m in part["modules"] if m["name"].endswith("row-1-view"))
    assert view["bindings"]["Root0"]["target"].endswith("avatar-1.Reference")
    assert all(
        view["bindings"][f"Root{i}"]["target"].endswith("ui-null-root.Reference")
        for i in range(1, 16)
    )
