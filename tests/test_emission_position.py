"""S: locator metadata must stay associated with the observed reference, never guess zero."""

import copy
import json
from dataclasses import asdict, replace
from math import sqrt

import pytest

from scripts.emission_editor.__main__ import load_capture
from scripts.emission_editor.model import MaterialUse, plan_registry
from scripts.emission_editor.resoloop_io import (
    Limits, capture_registration, plan_registry_capture,
)


class PositionRead:
    session = "position-fixture"

    def __init__(self):
        def slot(ref, name, pos, children=(), *, rotation=None, scale=None, avatar=False):
            return dict(id=ref, name=name, position=dict(zip("xyz", pos)),
                        rotation=dict(zip("xyzw", rotation or (0, 0, 0, 1))),
                        scale=dict(zip("xyz", scale or (1, 1, 1))),
                        components=[dict(id=ref + "-marker", type="[FrooxEngine]FrooxEngine.CommonAvatar.AvatarRoot")] if avatar else [],
                        children=list(children))
        self.tree = slot("Root", "Root", (0, 0, 0), [slot("grandparent", "階層", (100, 200, 300), [
            slot("left-parent", "duplicate", (10, 20, 30), [
                slot("left", "同名Plum", (1, 2, 3), avatar=True),
            ], rotation=(0, 0, sqrt(.5), sqrt(.5)), scale=(2, 3, 4)),
            slot("right-parent", "duplicate", (-10, 0, 1), [
                slot("right", "同名Plum", (1, 2, 3), avatar=True),
            ]),
        ], rotation=(sqrt(.5), 0, 0, sqrt(.5)), scale=(1, 2, 3))])

    def hierarchy(self, depth):
        return copy.deepcopy(self.tree)

    def material_list_types(self, types):
        return set()

    def owned_roots(self):
        return ()


def test_world_locator_identity_survives_capture_dump_load_plan_and_ui(tmp_path):
    """Dropping ancestors or swapping same-name roots silently points the user at another avatar."""
    from scripts.emission_editor.ui import render_panel

    capture = capture_registration(PositionRead(), ("right", "left"), Limits())
    assert capture.complete
    # Child (1,2,3) -> left parent (4,22,42) -> grandparent (104,74,344).
    expected = {"left": (104, 74, 344), "right": (91, 188, 304)}
    assert capture.avatars[0].path == capture.avatars[1].path
    for avatar in capture.avatars:
        assert avatar.observed_world_position == pytest.approx(expected[avatar.ref])
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(asdict(capture), ensure_ascii=False))
    restored = load_capture(path)
    assert restored == capture
    # The renderer list ID guards the registered use; it must survive the file too.
    listed = replace(capture, uses=(MaterialUse("mesh", "Mesh", "renderer", 1, "binding", "material", "list"),))
    listed_path = tmp_path / "listed.json"
    listed_path.write_text(json.dumps(asdict(listed), ensure_ascii=False))
    assert load_capture(listed_path).uses == listed.uses
    plan = plan_registry(restored)
    assert plan.observed_at == capture.observed_at
    from pathlib import Path
    panel = render_panel(plan, template=Path(__file__).resolve().parents[1] / "content/emission-editor/registry.json")

    def find(node, key):
        if node.get("slot", {}).get("key") == key:
            return node
        for child in node.get("children", []):
            if found := find(child, key):
                return found

    for i, avatar in enumerate(plan.avatars):
        locator = find(panel, f"reg-{plan.generation}-avatar-{i}-path")
        text = next(c["fields"]["Content"] for c in locator["components"] if "Content" in c.get("fields", {}))
        assert capture.observed_at in text
        assert all(f"{v:g}" in text for v in expected[avatar.ref])


def test_unobserved_position_is_unavailable_and_pose_is_not_publication_identity(tmp_path):
    """Missing metadata must not invent a real locator or freeze unrelated pose on publication."""
    read = PositionRead()
    first = capture_registration(read, ("left",), Limits())
    del read.tree["children"][0]["children"][0]["rotation"]
    missing = capture_registration(read, ("left",), Limits())
    assert missing.complete
    assert missing.avatars[0].observed_world_position is None
    legacy = asdict(first)
    del legacy["avatars"][0]["observed_world_position"]
    path = tmp_path / "old.json"
    path.write_text(json.dumps(legacy))
    assert load_capture(path).avatars[0].observed_world_position is None
    moved = replace(first, avatars=(replace(first.avatars[0], observed_world_position=(100, 2, 3)),))
    assert plan_registry_capture(first) == plan_registry_capture(moved)
    wrong = replace(first, avatars=(replace(first.avatars[0], ref="other-avatar"),))
    assert plan_registry_capture(first) != plan_registry_capture(wrong)
