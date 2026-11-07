"""S: registration must neither mutate avatar content nor lose old recovery data."""

from dataclasses import replace

from test_emission_registry import shared_capture

from pathlib import Path

from scripts.emission_editor.model import plan_registry
from scripts.emission_editor.resoloop_io import PanelStatus, publish_registry

_ROOT = Path(__file__).resolve().parents[1]


class _Resources:
    registry_template = _ROOT / "content/emission-editor/registry.json"
    source_root = _ROOT / "flux/emission-editor"


class FakePanelPort:
    session = "live-session"
    verified_panel_slots = frozenset({"panel", "staging"})

    def __init__(self, fail_on_write=0, **status):
        self.status = PanelStatus(**status)
        self.changed_slots = []
        self.avatar_writes = []
        self.fail_on_write = fail_on_write
        self.writes = 0
        self.staged = None
        self.active = "previous-generation"
        self.ready = True
        self.old_recovery = {"baseline": "preserve", "candidate": "preserve"}

    def guard(self, plan):
        return self.status

    def change(self, slot):
        assert slot in self.verified_panel_slots
        self.writes += 1
        if self.fail_on_write == self.writes:
            raise RuntimeError("interrupted")
        self.changed_slots.append(slot)

    def set_ready(self, ready):
        self.change("panel")
        self.ready = ready

    def begin(self, plan):
        self.change("panel")

    def finish(self, plan):
        self.change("panel")

    def stage(self, plan):
        self.change("staging")
        self.staged = plan

    def verify_staged(self, plan):
        return self.staged == plan

    def activate(self, plan):
        self.change("panel")
        self.active = plan.generation

    def verify_active(self, plan):
        return self.active == plan.generation and self.ready


def test_registration_never_writes_avatar_content():
    port = FakePanelPort()
    result = publish_registry(plan_registry(shared_capture()), port)
    assert result.success and result.ready
    assert set(port.changed_slots) <= port.verified_panel_slots
    assert port.avatar_writes == []
    assert port.old_recovery == {"baseline": "preserve", "candidate": "preserve"}


def test_incomplete_or_interrupted_registration_is_not_ready():
    plan = plan_registry(shared_capture())
    port = FakePanelPort(fail_on_write=2)
    result = publish_registry(plan, port)
    assert not result.success and not result.ready
    assert port.ready is False
    assert port.active == "previous-generation"
    assert port.old_recovery == {"baseline": "preserve", "candidate": "preserve"}
    incomplete = FakePanelPort()
    result = publish_registry(replace(plan, complete=False), incomplete)
    assert not result.success and not result.ready
    assert incomplete.changed_slots == []


def test_busy_recovery_and_changed_session_reject_without_mutations():
    plan = plan_registry(shared_capture())
    for status in (
        {"busy": True},
        {"operator": "another-user"},
        {"pending": True},
        {"preview_active": True},
        {"operation_stage": 2},
        {"owned": False},
    ):
        port = FakePanelPort(**status)
        assert not publish_registry(plan, port).success
        assert port.changed_slots == []
    port = FakePanelPort()
    port.session = "new-session"
    assert not publish_registry(plan, port).success
    assert port.changed_slots == []


def test_failed_readback_never_publishes_ready():
    port = FakePanelPort()
    port.verify_staged = lambda plan: False
    result = publish_registry(plan_registry(shared_capture()), port)
    assert not result.success and not result.ready
    assert port.ready is False and port.active == "previous-generation"


def test_in_progress_publication_refuses_a_second_registration():
    """A durable publication claim must stop a later client from losing staged records."""
    port = FakePanelPort(publication="unfinished-generation")
    result = publish_registry(plan_registry(shared_capture()), port)
    assert not result.success
    assert port.changed_slots == []


def test_unreviewed_update_in_staging_diff_never_reaches_apply(tmp_path):
    """S: a stale declaration must not reset the proof or an existing panel while registering."""
    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopPanelPort

    capture = shared_capture()

    class Read:
        session = capture.session
        evidence = tmp_path
        state = "unused"
        context = _Resources()

        def __init__(self):
            self.commands = []

        def run(self, *args):
            self.commands.append(args)
            if args[0] == "diff":
                return {
                    "operations": [{"action": "update", "key": "baseline"}],
                    "updates": 1,
                }
            raise AssertionError("an unapproved diff reached mutation")

    read = Read()
    port = ResoloopPanelPort(read, capture)
    with pytest.raises(ValueError, match="registration-diff-outside-new-generation"):
        port.stage(plan_registry(capture))
    assert all(command[0] != "apply" for command in read.commands)


def test_staging_accepts_only_new_owned_generation_and_preserves_bindings(tmp_path):
    import pytest
    from scripts.emission_editor.resoloop_io import ResoloopPanelPort

    capture = shared_capture()
    plan = plan_registry(capture)

    class Read:
        session = capture.session
        evidence = tmp_path
        state = "unused"
        context = _Resources()
        recipe_generation = plan.generation
        recipe_path = "Root/ResoLoop_Test_EmissionEditor/ui-canvas/reg-" + plan.generation + "-ui/inputs/native-input"

        def __init__(self):
            self.commands = []

        def run(self, *args):
            self.commands.append(args)
            if args[0] == "diff":
                return {
                    "operations": [
                        {"action": "create", "key": "reg-" + plan.generation},
                        {"action": "create", "key": "uix-text-input--reg-" + self.recipe_generation + "-row-0-r-editor", "path": self.recipe_path},
                    ],
                    "updates": 0,
                }
            return {"componentsUpdated": 0, "slotsCreated": 0}

    read = Read()
    port = ResoloopPanelPort(read, capture)
    writes = []
    port._set = lambda key, member, value: writes.append((key, member, value))
    port.stage(plan)
    references = {value for _, member, value in writes if member == "Reference"}
    assert references == {
        "avatar-a",
        "avatar-b",
        "shared-material",
        "other-material",
        "field-a",
        "field-b",
        "dress-1",
        "dress-2",
        "dress-3",
        "external-mesh",
        "sleeve",
        "renderer-dress-1",
        "renderer-dress-2",
        "renderer-dress-3",
        "renderer-external-mesh",
        "renderer-sleeve",
        "binding-dress-1",
        "binding-dress-2",
        "binding-dress-3",
        "binding-external-mesh",
        "binding-sleeve",
    }
    assert len([c for c in read.commands if c[0] == "apply"]) == 2
    # S: accepting native recipe keys must still reject a different generation.
    rejected = Read()
    rejected.evidence = tmp_path / "rejected"
    rejected.recipe_generation = "another-generation"
    with pytest.raises(ValueError, match="registration-diff-outside-new-generation"):
        ResoloopPanelPort(rejected, capture).stage(plan)
    assert all(command[0] != "apply" for command in rejected.commands)
    outside = Read()
    outside.evidence = tmp_path / "outside"
    outside.recipe_path = "Root/Unrelated/native-input"
    outside_port = ResoloopPanelPort(outside, capture)
    outside_port._set = lambda *args: None
    with pytest.raises(ValueError, match="registration-diff-outside-new-generation"):
        outside_port.stage(plan)
    assert all(command[0] != "apply" for command in outside.commands)


def test_failure_releasing_publication_does_not_report_verified_ready():
    """Failure after the Ready write must still be failure, with a disable attempt."""
    port = FakePanelPort(fail_on_write=6)
    result = publish_registry(plan_registry(shared_capture()), port)
    assert not result.success and not result.ready
    assert port.ready is False
    assert port.old_recovery == {"baseline": "preserve", "candidate": "preserve"}


def test_live_port_proves_parent_boundary_before_sending_a_write(tmp_path):
    """S: a stale checkpoint path cannot redirect an owned write onto an external Slot."""
    import json

    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopPanelPort

    capture = shared_capture()
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "ownershipKey": "tools-emission-editor",
                "components": {
                    "owned-field": {"slotKey": "field-slot", "type": "StringField"}
                },
            }
        )
    )

    class Read:
        session = capture.session

        def __init__(self, outside):
            self.outside = outside
            self.state = state
            self.writes = []

        def run(self, *args):
            if args[0] == "component":
                if args[1] == "set":
                    self.writes.append(args)
                    return {}
                return {
                    "id": "field",
                    "type": "StringField",
                    "members": {"Value": {"id": "v", "kind": "field"}},
                }
            nodes = {
                "$slot:registry-root": {"id": "registry", "parentId": "owner"},
                "$slot:emission-root": {
                    "id": "owner",
                    "name": "ResoLoop_Test_EmissionEditor",
                },
                "$slot:field-slot": {
                    "id": "field-slot",
                    "parentId": "external" if self.outside else "registry",
                    "components": [{"id": "field"}],
                },
                "external": {"id": "external", "parentId": "Root"},
                "registry": {"id": "registry", "parentId": "owner"},
            }
            return nodes[args[1]]

    outside = Read(True)
    with pytest.raises(ValueError, match="target-outside-registry"):
        ResoloopPanelPort(outside, capture)._set(
            "owned-field", "Value", "日本語\nvalue"
        )
    assert outside.writes == []
    correct = Read(False)
    ResoloopPanelPort(correct, capture)._set("owned-field", "Value", "日本語\nvalue")
    assert correct.writes[0][-1] == "日本語\nvalue"


def test_unregister_refuses_recovery_or_publisher_and_retains_records_on_success():
    """S: deactivation must not discard a pending source restore or another publisher's state."""
    from scripts.emission_editor.__main__ import unregister

    class Port:
        session = "native"

        def __init__(self, **flags):
            self.values = {
                "registry-active": "old-generation",
                "registry-ready": True,
                **{
                    "registry-" + key: False
                    for key in (
                        "busy",
                        "operator",
                        "pending",
                        "preview-active",
                        "operation-stage",
                        "publication",
                    )
                },
                **{"registry-" + key: value for key, value in flags.items()},
                "ui-intent": 0,
            }
            self.records = {
                "baseline": "exact original",
                "candidate": "write awaiting bookkeeping",
            }
            self.writes = []

        def _value(self, key):
            return self.values[key]

        def _set(self, key, member, value):
            self.writes.append((key, value))
            self.values[key] = value

        def set_ready(self, value):
            self._set("registry-ready", "Value", value)

    for flags in (
        {"pending": True},
        {"publication": "another-publisher"},
        {"operation-stage": 2},
    ):
        port = Port(**flags)
        assert not unregister(port)["success"]
        assert port.writes == [] and port.values["registry-active"] == "old-generation"
    port = Port()
    assert unregister(port)["success"]
    assert (
        port.values["registry-active"] == "" and port.values["registry-ready"] is False
    )
    assert port.records == {
        "baseline": "exact original",
        "candidate": "write awaiting bookkeeping",
    }


def test_session_change_after_diff_sends_no_apply(tmp_path):
    """S: a matching owned declaration must not create data in a replacement session."""
    import pytest

    from scripts.emission_editor.resoloop_io import ResoloopPanelPort

    capture = shared_capture()
    plan = plan_registry(capture)

    class Read:
        session = capture.session
        evidence = tmp_path
        state = "unused"
        context = _Resources()

        def __init__(self):
            self.commands = []

        def run(self, *args):
            self.commands.append(args)
            if args[0] == "diff":
                self.session = "replacement-native-session"
                return {
                    "operations": [
                        {"action": "create", "key": "reg-" + plan.generation}
                    ],
                    "updates": 0,
                }
            return {"componentsUpdated": 0, "slotsCreated": 0}

    read = Read()
    port = ResoloopPanelPort(read, capture)
    port._set = lambda *args: None
    with pytest.raises(ValueError, match="session-changed-before-apply"):
        port.stage(plan)
    assert all(command[0] != "apply" for command in read.commands)
