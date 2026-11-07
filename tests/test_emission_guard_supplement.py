"""S: the supplement must bind each registered use to its own renderer list and index."""

from dataclasses import replace

import pytest

from scripts.emission_editor.guard_supplement import apply
from scripts.emission_editor.model import plan_registry
from scripts.emission_editor.resoloop_io import (
    INT,
    LIST_REF,
    PROVIDER_REF,
    Limits,
    ResoloopPanelPort,
    capture_registration,
)

XIEXE = "[FrooxEngine]FrooxEngine.XiexeToonMaterial"
MARKER = [{"id": "avatar-marker", "type": "[FrooxEngine]FrooxEngine.CommonAvatar.AvatarRoot"}]


class World:
    """Read-only renderer/material observation used by capture_registration."""

    def __init__(self):
        self.session = "native-session"
        # renderer -> (mesh slot, list ID, [(element ID, material)])
        self.renderers = {
            "r-dress": ("dress", "list-dress", [("b-dress-0", "other"), ("b-dress-1", "mat")]),
            "r-sleeve": ("sleeve", "list-sleeve", [("b-sleeve-0", "mat")]),
        }

    def hierarchy(self, depth):
        meshes = [
            {"id": slot, "name": slot, "components": [{"id": ref, "type": "Renderer"}]}
            for ref, (slot, _, _) in self.renderers.items()
        ]
        materials = [{"id": ref, "type": XIEXE} for ref in ("mat", "other")]
        return {"id": "Root", "name": "Root", "children": [
            {"id": "avatar", "name": "Plum", "components": MARKER, "children": meshes},
            {"id": "materials", "name": "Materials", "components": materials},
        ]}

    def owned_roots(self):
        return ()

    def material_list_types(self, types):
        return {"Renderer"}

    def component(self, ref):
        if ref in self.renderers:
            _, list_ref, elements = self.renderers[ref]
            return {"id": ref, "type": "Renderer", "members": {"Materials": {
                "kind": "list", "id": list_ref,
                "elements": [{"id": e, "kind": "reference", "targetId": t} for e, t in elements],
            }}}
        color = {"r": 1.5, "g": 2, "b": 3, "a": 0.37, "profile": "sRGB"}
        return {"id": ref, "type": XIEXE, "members": {"EmissionColor": {
            "kind": "field", "id": "color-" + ref, "type": "ResoniteLink.colorX", "value": color,
        }}}


class Panel(ResoloopPanelPort):
    """Owned field store standing in for the verified observe/set boundary."""

    def __init__(self, world, capture, fields, types):
        super().__init__(world, capture)
        self.fields = fields
        self.types = types
        self.writes = []

    def _state(self):
        return {"ownershipKey": "tools-emission-editor",
                "components": {key: {"type": typ} for key, typ in self.types.items()}}

    def _observe_owned(self, selectors):
        return {
            s: {"targetId": self.fields[s]} if s[1] == "Reference" else {"value": self.fields[s]}
            for s in selectors
        }

    def _set(self, key, member, value):
        self.writes.append((key, member, value))
        self.fields[(key, member)] = value


def registered():
    """The legacy registration: its capture predates list IDs, as for efdd5984."""
    world = World()
    capture = capture_registration(world, ("avatar",), Limits())
    assert capture.complete
    capture = replace(capture, generation="efdd5984",
                      uses=tuple(replace(u, list_ref=None) for u in capture.uses))
    plan = plan_registry(capture)
    p = "reg-" + plan.generation
    fields = {
        ("registry-publication", "Value"): "", ("registry-busy", "Value"): False,
        ("registry-operator", "Value"): "acquired-user", ("registry-pending", "Value"): False,
        ("registry-preview-active", "Value"): False, ("registry-operation-stage", "Value"): 0,
        ("ui-intent", "Value"): 0, ("registry-active", "Value"): plan.generation,
        (p + "-avatar-0", "Reference"): "avatar",
        (p + "-row-count", "Value"): 0,
    }
    types = {p + "-row-count": INT}
    for i, row in enumerate(plan.rows):
        r = f"{p}-row-{i}"
        fields[(r + "-material", "Reference")] = row.material_ref
        fields[(r + "-source", "Reference")] = row.field_ref
        fields[(r + "-provider", "Reference")] = None
        types[r + "-provider"] = PROVIDER_REF
        for k, use in enumerate(row.uses):
            fields[(f"{r}-mesh-{k}", "Reference")] = use.mesh_ref
            fields[(f"{r}-renderer-{k}", "Reference")] = use.renderer_ref
            fields[(f"{r}-binding-{k}", "Reference")] = use.binding_ref
            fields[(f"{r}-list-{k}", "Reference")] = None
            fields[(f"{r}-index-{k}", "Value")] = -1
            types[f"{r}-list-{k}"] = LIST_REF
            types[f"{r}-index-{k}"] = INT
    return world, capture, plan, fields, types


def test_guard_supplement_binds_registered_list_and_index_or_refuses():
    """Binding another renderer's list or another index would let the guard pass wrongly."""
    world, capture, plan, fields, types = registered()
    p = "reg-" + plan.generation
    assert [row.material_ref for row in plan.rows] == ["mat", "other"]
    expected = {
        (p + "-row-0-index-0", "Value"): 1, (p + "-row-0-list-0", "Reference"): "list-dress",
        (p + "-row-0-index-1", "Value"): 0, (p + "-row-0-list-1", "Reference"): "list-sleeve",
        (p + "-row-0-provider", "Reference"): "mat",
        (p + "-row-1-index-0", "Value"): 0, (p + "-row-1-list-0", "Reference"): "list-dress",
        (p + "-row-1-provider", "Reference"): "other",
        (p + "-row-count", "Value"): 2,
    }
    before = dict(fields)
    panel = Panel(world, capture, fields, types)
    assert apply(panel, plan)["success"]
    assert len(panel.writes) == len(expected)
    assert {(k, m): v for k, m, v in panel.writes} == expected
    assert panel.fields == {**before, **expected}  # nothing outside the guard fields

    def refused(change):
        world, capture, plan, fields, types = registered()
        change(world, fields)
        panel = Panel(world, capture, fields, types)
        with pytest.raises(ValueError):
            apply(panel, plan)
        assert panel.writes == []

    def swapped(world, fields):  # the registered index now holds another material
        world.renderers["r-dress"] = ("dress", "list-dress", [("b-dress-0", "mat"), ("b-dress-1", "other")])

    def rebound(world, fields):  # same material, but not the registered element
        world.renderers["r-dress"] = ("dress", "list-dress", [("b-dress-0", "other"), ("b-dress-new", "mat")])

    def restarted(world, fields):
        world.session = "replacement-session"

    def misregistered(world, fields):  # panel references disagree with the capture
        fields[(p + "-row-0-binding-0", "Reference")] = "b-sleeve-0"

    def foreign(world, fields):  # neither the planned value nor the initial one
        fields[(p + "-row-0-list-0", "Reference")] = "list-sleeve"

    for change in (swapped, rebound, restarted, misregistered, foreign):
        refused(change)

    # An interrupted run resumes only after an exact written prefix.
    world, capture, plan, fields, types = registered()
    done = list(expected)[:2]
    fields.update({key: expected[key] for key in done})
    panel = Panel(world, capture, fields, types)
    assert apply(panel, plan)["already_written"] == 2
    assert {(k, m) for k, m, _ in panel.writes} == set(expected) - set(done)
    assert all(panel.fields[key] == value for key, value in expected.items())
