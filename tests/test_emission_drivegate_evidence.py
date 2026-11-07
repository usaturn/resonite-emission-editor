"""S: reject plausible but wrong typed values, stale/replayed evidence and false phases.

Fixtures are independently authored raw CLI envelopes, not validator-produced
expectations. No test contacts Resonite. Removing the named guard in each test
must admit silent damage, not merely change wording or static configuration.
"""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
import shutil

import pytest

from scripts.emission_editor.read_receipts import ReadRequest
from scripts.emission_editor import drivegate_evidence as evidence


URL = "ws://localhost:16070"  # Fake endpoint; never connected.
START = datetime(2026, 11, 7, 12, tzinfo=UTC)



def field(typ, value, ident="Reso_F0"):
    return {"id": ident, "kind": "field", "type": typ, "value": deepcopy(value)}


def color():
    return {"r": 1.5, "g": .25, "b": 3, "a": .625, "profile": "Linear"}


def raw_read(root, name, request, data, second, *, exit_code=0, ok=True):
    directory = root / name
    directory.mkdir()
    stdout = json.dumps({"ok": ok, "data": data}, ensure_ascii=False).encode()
    stderr = b""
    (directory / "stdout.bin").write_bytes(stdout)
    (directory / "stderr.bin").write_bytes(stderr)
    receipt = {
        "schemaVersion": 1, "request": {"operation": request.operation, "url": request.url,
            "target": request.target, "depth": request.depth}, "argv": request.argv(),
        "cwd": str(root), "startedAt": (START + timedelta(seconds=second)).isoformat(),
        "finishedAt": (START + timedelta(seconds=second + .5)).isoformat(),
        "exitCode": exit_code, "launchError": None,
        "stdoutSha256": hashlib.sha256(stdout).hexdigest(),
        "stderrSha256": hashlib.sha256(stderr).hexdigest(),
    }
    (directory / "receipt.json").write_text(json.dumps(receipt))
    return directory


def window():
    return evidence.ReadWindow(URL, START, START + timedelta(seconds=30), START + timedelta(seconds=31))


def identity():
    return evidence.NativeIdentity("Reso_AA", "Value", "世界・試験")


def identity_data(value="世界・試験"):
    return {"id": "Reso_AA", "type": "[FrooxEngine]FrooxEngine.ValueField<string>",
            "members": {"Value": field("System.String", value)}}


def observation(root, *, value=None):
    before = raw_read(root, "session-before", ReadRequest("component-inspect", URL, "Reso_AA"), identity_data(), 1)
    request = ReadRequest("component-inspect", URL, "Reso_BB")
    middle = raw_read(root, "value", request, value or {"id": "Reso_BB", "members": {}}, 3)
    after = raw_read(root, "session-after", ReadRequest("component-inspect", URL, "Reso_AA"), identity_data(), 5)
    return before, [(request, middle)], after


def test_typed_comparison_retains_color_profile_alpha_and_bool_identity():
    # S: Python's True == 1, or RGB-only comparison, silently accepts wrong state.
    compare = evidence.compare_typed
    expected = field("ResoniteLink.colorX", color())
    same = field("ResoniteLink.colorX", {"r": 1.5, "g": .25, "b": 3.0, "a": .625, "profile": "Linear"})
    assert compare(expected, same).status == "passed"
    changed = deepcopy(same)
    changed["value"]["profile"] = "sRGB"
    assert compare(expected, changed).status == "failed"
    changed = deepcopy(same)
    changed["value"]["a"] = 1
    assert compare(expected, changed).status == "failed"
    assert expected["value"]["profile"] == "Linear"  # Negative fixture owns its dict.
    assert compare(field("System.Boolean", True), field("System.Boolean", 1)).status == "failed"
    assert compare(field("System.Int32", 1), field("System.Int32", True)).status == "failed"
    assert compare(field("System.Boolean", False), {"id": "Reso_F0", "kind": "field", "type": "System.Boolean"}).status == "failed"
    assert compare(field("System.Boolean", False), field("System.Boolean", None)).status == "failed"


def test_receipts_bind_native_session_requests_order_and_fresh_window(tmp_path):
    # S: transport success must not authorize another session/target/time window.
    before, reads, after = observation(tmp_path)
    validator = evidence.load_observation
    result = validator(before, reads, after, window=window(), identity=identity())
    assert result.status == "passed"
    assert result.data[0]["id"] == "Reso_BB"
    assert not result.complete and not result.executionPermission
    changed = tmp_path / "session-after" / "stdout.bin"
    payload = json.loads(changed.read_bytes())
    payload["data"]["members"]["Value"]["value"] = "別世界"
    changed.write_text(json.dumps(payload, ensure_ascii=False))
    receipt = after / "receipt.json"
    metadata = json.loads(receipt.read_bytes())
    metadata["stdoutSha256"] = hashlib.sha256(changed.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(metadata))
    assert validator(before, reads, after, window=window(), identity=identity()).status == "failed"


@pytest.mark.parametrize("damage", ["alias", "old", "future", "reverse", "target", "exit", "native-missing"])
def test_replayed_or_unbound_reads_never_pass(damage, tmp_path):
    # S: one representative per independent replay/freshness/request/identity guard.
    before, reads, after = observation(tmp_path)
    request, middle = reads[0]
    if damage == "alias":
        alias = raw_read(tmp_path, "alias", request, {"id": "Reso_BB", "members": {}}, 4)
        (alias / "stdout.bin").unlink()
        os.link(middle / "stdout.bin", alias / "stdout.bin")
        reads.append((request, alias))
    elif damage == "target":
        reads[0] = (ReadRequest("component-inspect", URL, "Reso_CC"), middle)
    else:
        path = (before if damage == "native-missing" else middle) / "receipt.json"
        metadata = json.loads(path.read_bytes())
        if damage in {"old", "future", "reverse"}:
            metadata["startedAt"] = (START + timedelta(seconds={"old": -5, "future": 60, "reverse": 4}[damage])).isoformat()
            metadata["finishedAt"] = (START + timedelta(seconds={"old": -4, "future": 61, "reverse": 2}[damage])).isoformat()
        elif damage == "exit":
            metadata["exitCode"] = 7
        else:
            raw = json.dumps({"ok": True, "data": {"connectionId": "世界・試験"}}).encode()
            (before / "stdout.bin").write_bytes(raw)
            metadata["stdoutSha256"] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(metadata))
    assert evidence.load_observation(before, reads, after, window=window(), identity=identity()).status != "passed"


def subtree():
    return {"id": "Reso_A0", "name": "試験ルート", "isReferenceOnly": False,
            "components": [], "children": [
                {"id": "Reso_B0", "name": "private", "isReferenceOnly": False,
                 "components": [{"id": "Reso_C0", "type": "[FrooxEngine]FrooxEngine.ValueField<bool>",
                                 "members": {"Value": field("System.Boolean", False)}}], "children": []},
                {"id": "Reso_D0", "name": "helper", "isReferenceOnly": False,
                 "components": [], "children": []}]}


def test_full_subtree_and_all_members_are_required_before_deletion():
    # S: ignoring a missing descendant/member or wrong root permits unsafe delete.
    expected, observed = subtree(), subtree()
    compare = evidence.compare_subtree
    assert compare(expected, observed).status == "passed"
    observed["children"][0]["components"][0]["members"] = {}
    assert compare(expected, observed).status != "passed"
    observed = subtree()
    observed["children"][0]["isReferenceOnly"] = True
    assert compare(expected, observed).status == "inconclusive"
    after = subtree()
    after["children"].pop()
    deletion = evidence.compare_deletion(expected, subtree(), after, "Reso_D0")
    assert deletion.status == "passed"
    assert evidence.compare_deletion(expected, subtree(), after, "Reso_B0").status == "failed"
    after["children"][0]["components"][0]["members"]["Value"]["value"] = True
    assert evidence.compare_deletion(expected, subtree(), after, "Reso_D0").status == "failed"


def phase_baseline():
    values = {"Command": ("System.Int32", 0), "Done": ("System.Int32", 0),
              "Captured": ("System.Boolean", False), "BeforeColor": ("ResoniteLink.colorX", color()),
              "BeforeBool": ("System.Boolean", False),
              "ColorTarget": ("ResoniteLink.colorX", {"r": 4.25, "g": .5, "b": 1.125, "a": .3125, "profile": "sRGB"}),
              "BoolTarget": ("System.Boolean", True), "ColorAfter": ("ResoniteLink.colorX", color()),
              "BoolAfter": ("System.Boolean", False), "ColorOutcome": ("System.Int32", 0),
              "BoolOutcome": ("System.Int32", 0)}
    return {name: field(typ, value, "Reso_" + format(i+256, "X"))
            for i, (name, (typ, value)) in enumerate(values.items())}


def phases():
    # Independently hand-derived native expectations, NOT the validator's table.
    result = []
    for phase, rgba, boolean, outcomes in [
        (1, (4.25, .5, 1.125, .3125, "sRGB"), True, (0, 0)),
        (2, (1.5, .25, 3, .625, "Linear"), True, (1, 1)),
        (3, (1.5, .25, 3, .625, "Linear"), True, (2, 2)),
        (4, (.875, 5.5, .0625, .8125, "sRGB"), True, (1, 2)),
        (5, (2.5, .75, .125, .375, "Linear"), False, (1, 1)),
        (6, (4.25, .5, 1.125, .3125, "sRGB"), True, (1, 1)),
    ]:
        members = phase_baseline()
        updates = {"Command": phase, "Done": phase, "Captured": True,
                   "BeforeColor": {"r": 4.25, "g": .5, "b": 1.125, "a": .3125, "profile": "sRGB"},
                   "BeforeBool": True,
                   "ColorTarget": dict(zip(("r", "g", "b", "a", "profile"), rgba)),
                   "ColorAfter": dict(zip(("r", "g", "b", "a", "profile"), rgba)),
                   "BoolTarget": boolean, "BoolAfter": boolean,
                   "ColorOutcome": outcomes[0], "BoolOutcome": outcomes[1]}
        for key, value in updates.items():
            members[key]["value"] = value
        result.append((phase, members))
    return result


def test_phase_comparison_requires_rejection_outcomes_readback_and_restoration():
    # S: Done/exit zero alone must not turn a Drive rejection/write-through into PASS.
    evaluate = evidence.compare_phase_sequence
    baseline = phase_baseline()
    result = evaluate(baseline, phases())
    assert result.status == "passed"
    assert not result.nativeSucceeded and not result.complete and not result.executionPermission
    broken = phases()
    broken[2][1]["ColorOutcome"]["value"] = 1
    assert evaluate(baseline, broken).status == "failed"
    broken = phases()
    broken[3][1]["ColorAfter"]["value"]["profile"] = "Linear"
    assert evaluate(baseline, broken).status == "failed"
    broken = phases()
    broken[5][1]["BeforeBool"]["value"] = False
    assert evaluate(baseline, broken).status == "failed"
    assert evaluate(baseline, phases()[:3] + phases()[4:]).status == "inconclusive"
    broken = phases()
    broken[1][1]["Done"]["value"] = True
    assert evaluate(baseline, broken).status == "failed"


@pytest.mark.parametrize("blind_spot", ["capture-color", "capture-bool", "restore-bool"])
def test_unobservable_capture_or_restoration_cannot_pass(blind_spot):
    # S: dropping any baseline sentinel guard blesses a skipped capture/restore.
    baseline, observed = phase_baseline(), phases()
    assert evidence.compare_phase_sequence(baseline, observed).status == "passed"
    if blind_spot == "capture-color":
        baseline["BeforeColor"]["value"] = deepcopy(baseline["ColorTarget"]["value"])
    elif blind_spot == "capture-bool":
        baseline["BeforeBool"]["value"] = True
    else:
        # Original false already equals C: phase 6 can skip the bool restore.
        baseline["BoolTarget"]["value"] = False
        for phase in (0, 5):
            for name in ("BoolTarget", "BoolAfter"):
                observed[phase][1][name]["value"] = False
        for _, members in observed:
            members["BeforeBool"]["value"] = False
    assert evidence.compare_phase_sequence(baseline, observed).status != "passed"


def test_generated_fixture_makes_capture_and_restore_observable():
    # S: a valid probe with indistinguishable generated defaults cannot prove
    # capture/restoration. Exercise the declared values, not a copied constant list.
    from scripts.emission_editor.drivegate_package import fixture_declaration
    baseline = phase_baseline()
    declaration = fixture_declaration("offline-sentinels")
    for component in declaration["children"][0]["components"]:
        name = component["key"].removeprefix("drivegate-")
        value = deepcopy(component["initialFields"]["Value"])
        if baseline[name]["type"] == "ResoniteLink.colorX":
            # Offline typed input, not a claim about CLI profile conversion.
            value = dict(zip(("r", "g", "b", "a"), value)) | {"profile": "sRGB"}
        baseline[name]["value"] = value
    observed = phases()
    for index, members in observed:
        members["BeforeColor"]["value"] = {"r": 4.25, "g": .5, "b": 1.125, "a": .3125, "profile": "sRGB"}
        if index in (1, 6):
            for name in ("ColorTarget", "ColorAfter"):
                members[name]["value"] = {"r": 4.25, "g": .5, "b": 1.125, "a": .3125, "profile": "sRGB"}
    result = evidence.compare_phase_sequence(baseline, observed)
    assert result.status == "passed", result.issues



SDK_BOOL = "[ProtoFluxBindings]FrooxEngine.ProtoFlux.Runtimes.Execution.Nodes.ValueInput<bool>"
SDK_DRIVE = "[ProtoFluxBindings]FrooxEngine.FrooxEngine.ProtoFlux.CoreNodes.ValueFieldDrive<bool>"
SDK_PROXY = "[FrooxEngine]FrooxEngine.ProtoFlux.CoreNodes.FieldDriveBase<bool>+Proxy"


def sdk_record():
    # Minimal independent representation of the installed SDK's actual envelope.
    def slot(name, components=(), children=()):
        return {"Name": {"Data": name}, "Components": {"Data": list(components)}, "Children": list(children)}
    def component(index, ident, **members):
        return {"Type": index, "Data": {"ID": ident, **{name: {"ID": ident+"-"+name, "Data": value}
                                                       for name, value in ({"UpdateOrder": 0, "Enabled": True} | members).items()}}}
    module = slot("<color=#C5C0C9>BoolDrive</color> (3)", children=[
        slot("Driven", [component(1, "node-value", Value=True)]),
        slot("Output:Driven", [component(2, "node-drive", Value="node-value"),
                               component(3, "node-proxy", Node="node-drive", Path=[], Drive=None)])])
    return {"Types": ["[FrooxEngine]FrooxEngine.MeshRenderer", SDK_BOOL, SDK_DRIVE, SDK_PROXY],
            "Object": slot("BoolDrive", [component(0, "presentation")], [slot("Flux", children=[module])])}


def runtime_bool():
    components = [
        {"id": "Reso_1", "type": SDK_BOOL, "members": {"Value": field("System.Boolean", True, "Reso_11")}},
        {"id": "Reso_2", "type": SDK_DRIVE, "members": {"Value": {"id": "Reso_21", "kind": "reference", "targetId": "Reso_1"}}},
        {"id": "Reso_3", "type": SDK_PROXY, "members": {
            "Node": {"id": "Reso_31", "kind": "reference", "targetId": "Reso_2"},
            "Path": {"id": "Reso_32", "kind": "list", "elements": []},
            "Drive": {"id": "Reso_33", "kind": "reference", "targetId": "Reso_FF"}}}]
    for i, component in enumerate(components):
        component["members"]["UpdateOrder"] = field("System.Int32", 0, f"Reso_D{i}0")
        component["members"]["Enabled"] = field("System.Boolean", True, f"Reso_D{i}1")
    return components


def test_sdk_runtime_resolves_type_indices_and_all_edges_not_presentation_names():
    # S: hashed template/name matches cannot detect a foreign Drive target or lost node.
    compare = evidence.compare_sdk_runtime
    args = {"module": "BoolDrive", "id_map": {"node-value": "Reso_1", "node-drive": "Reso_2", "node-proxy": "Reso_3"},
            "private_bindings": {"Driven": "Reso_FF"}}
    assert compare(sdk_record(), runtime_bool(), **args).status == "passed"
    observed = runtime_bool()
    observed[2]["members"]["Drive"]["targetId"] = "Reso_FE"
    assert compare(sdk_record(), observed, **args).status == "failed"
    assert compare(sdk_record(), runtime_bool()[:-1], **args).status != "passed"
    observed = runtime_bool()
    observed[1]["members"]["Value"]["targetId"] = "Reso_FE"
    assert compare(sdk_record(), observed, **args).status == "failed"
    observed = runtime_bool()
    observed[0]["type"] = "[FrooxEngine]FrooxEngine.MeshRenderer"
    assert compare(sdk_record(), observed, **args).status == "failed"
    changed = sdk_record()
    changed["Object"]["Children"][0]["Children"][0]["Children"][1]["Components"]["Data"][0]["Type"] = True
    assert compare(changed, runtime_bool(), **args).status != "passed"



def sealed_fixture(root):
    # Small self-contained valid v2 inventory, not a mock of verification.
    from scripts.emission_editor.drivegate_package import fixture_declaration, _manifests, FORMAT
    package, execution = root / "seal", root / "execution"
    package.mkdir()
    execution.mkdir()
    (package / "fixture.json").write_text(json.dumps(fixture_declaration("offline")))
    (package / "sources").mkdir()
    (package / "manifests").mkdir()
    for name in ("DriveGateProbe", "ColorDrive", "BoolDrive"):
        (package / "sources" / (name + ".pg")).write_text("module " + name)
        (package / "sources" / (name + ".pg.brson")).write_bytes(b"sealed-sdk-record")
    for role, manifest in _manifests(execution).items():
        (package / "manifests" / (role + ".json")).write_text(json.dumps(manifest))
    inventory = {str(p.relative_to(package)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in package.rglob("*") if p.is_file()}
    seal = {"format": FORMAT, "token": "offline", "executionRoot": str(execution), "files": inventory,
            "codeCommit": "0" * 40, "cliVersion": "test", "sdkVersion": "test", "dllSha256": {"test.dll": "0" * 64},
            "complete": False, "executionPermission": False, "nativeSucceeded": False}
    (package / "seal.json").write_text(json.dumps(seal))
    (package / "seal.sha256").write_text(hashlib.sha256((package / "seal.json").read_bytes()).hexdigest())
    for name in ("fixture.json", "sources", "manifests"):
        source = package / name
        if source.is_dir():
            shutil.copytree(source, execution / name)
        else:
            shutil.copyfile(source, execution / name)
    return package, execution


def test_runtime_area_allows_only_manifest_state_not_rebuilt_or_unknown_inputs(tmp_path):
    # S: accepting runtime state must not also authorize changed inputs or unknown effects.
    compare = evidence.verify_runtime_execution
    package, execution = sealed_fixture(tmp_path)
    assert compare(package, execution).status == "passed"
    state = execution / "runtime" / "probe" / "world.json"
    state.parent.mkdir(parents=True)
    state.write_text('{"sessionId":"fake-connection-not-native"}')
    assert compare(package, execution).status == "passed"
    record = execution / "sources" / "ColorDrive.pg.brson"
    original = record.read_bytes()
    record.write_bytes(b"rebuilt-different-record")
    assert compare(package, execution).status == "failed"
    record.write_bytes(original)
    (execution / "unknown-state.json").write_text("{}")
    assert compare(package, execution).status == "failed"



def test_observation_rejects_response_for_another_target(tmp_path):
    # S: success for the requested argv must not vouch for a different response ID.
    before, reads, after = observation(tmp_path, value={"id": "Reso_CC", "members": {}})
    assert evidence.load_observation(before, reads, after, window=window(), identity=identity()).status == "failed"


def test_reference_null_is_known_only_for_a_present_identified_reference():
    # S: normalizing an absent member as null could authorize a disconnected writer.
    expected = {"kind": "reference", "id": "Reso_F1", "targetId": None,
                "targetType": "INodeOperation"}
    omitted = {"kind": "reference", "id": "Reso_F1", "targetType": "INodeOperation"}
    assert evidence.compare_typed(expected, omitted).status == "passed"
    assert evidence.compare_typed(expected, {}).status == "inconclusive"
    assert evidence.compare_typed(expected, {"kind": "reference"}).status == "inconclusive"
    connected = expected | {"targetId": "Reso_F2"}
    assert evidence.compare_typed(connected, omitted).status == "failed"


def test_slot_member_projection_cannot_hide_unknown_values_as_metadata():
    # S: identical incomplete slot-member projections do not establish protection.
    expected, actual = subtree(), subtree()
    expected["members"] = {"IsActive": field("System.Boolean", True, "Reso_E1")}
    actual["members"] = {"IsActive": field("System.Boolean", True, "Reso_E1")}
    assert evidence.compare_subtree(expected, actual).status == "passed"
    del expected["members"]["IsActive"]["value"]
    del actual["members"]["IsActive"]["value"]
    assert evidence.compare_subtree(expected, actual).status == "inconclusive"


def test_present_nullable_string_is_not_missing_or_empty():
    # S: treating omitted null as empty hides a changed slot Tag; an absent
    # member must not be invented from the serializer's omission policy.
    expected = field("System.String", None, "Reso_E2")
    omitted = {"id": "Reso_E2", "kind": "field", "type": "System.String"}
    assert evidence.compare_typed(expected, omitted).status == "passed"
    assert evidence.compare_typed(expected, field("System.String", "", "Reso_E2")).status == "failed"
    assert evidence.compare_typed(expected, {}).status == "inconclusive"
    actual = deepcopy(omitted)
    del actual["type"]
    assert evidence.compare_typed(expected, actual).status == "inconclusive"
    before, after = subtree(), subtree()
    before["members"] = {"Tag": expected}
    after["members"] = {"Tag": omitted}
    assert evidence.compare_subtree(before, after).status == "passed"
    del after["members"]["Tag"]
    assert evidence.compare_subtree(before, after).status != "passed"


def test_protected_opaque_payloads_are_preserved_not_authorized_private_types():
    # S: filtering uninterpreted types or nested playback values silently loses
    # protected data. Globally allowing them would widen private writer checks.
    before, after = subtree(), subtree()
    members = {
        "Type": field("System.Type", {"name": "型", "args": [None, True, 1]}, "Reso_E3"),
        "Playback": {"kind": "SyncPlayback", "id": "Reso_E4",
                     "type": "[FrooxEngine]FrooxEngine.SyncPlayback",
                     "value": {"loop": True, "position": 1.25, "nested": [None, {"speed": 2}]}},
        "Enum": field("Engine.Mode", "First", "Reso_E5"),
        "Pair": field("ResoniteLink.float2", {"x": 1, "y": .5}, "Reso_E6"),
    }
    before["children"][0]["components"][0]["members"].update(deepcopy(members))
    after["children"][0]["components"][0]["members"].update(deepcopy(members))
    assert evidence.compare_subtree(before, after).status == "passed"
    assert evidence.compare_typed(members["Type"], deepcopy(members["Type"])).status == "inconclusive"
    actual = after["children"][0]["components"][0]["members"]
    actual["Playback"]["value"]["nested"][1]["speed"] = 3
    assert evidence.compare_subtree(before, after).status == "failed"
    actual["Playback"] = deepcopy(members["Playback"])
    actual["Type"]["value"]["args"][1] = 1  # bool must not coerce even inside opaque JSON.
    assert evidence.compare_subtree(before, after).status == "failed"
    actual["Type"] = deepcopy(members["Type"])
    del actual["Pair"]["type"]
    assert evidence.compare_subtree(before, after).status != "passed"
    actual["Pair"] = {"kind": "unrecognized", "id": "Reso_E6", "value": 1}
    before["children"][0]["components"][0]["members"]["Pair"] = deepcopy(actual["Pair"])
    assert evidence.compare_subtree(before, after).status == "inconclusive"


def test_slot_numeric_shapes_preserve_every_axis_and_int64_without_bool_coercion():
    # S: dropping quaternion w, coercing bool or rounding a large OrderOffset
    # can report a changed protected slot as unchanged.
    before, after = subtree(), subtree()
    members = {"Position": field("ResoniteLink.float3", {"x": 1, "y": .25, "z": -3}, "Reso_E7"),
               "Rotation": field("ResoniteLink.floatQ", {"x": 0, "y": .5, "z": 0, "w": .75}, "Reso_E8"),
               "OrderOffset": field("System.Int64", 2**60 + 1, "Reso_E9")}
    before["members"], after["members"] = deepcopy(members), deepcopy(members)
    assert evidence.compare_subtree(before, after).status == "passed"
    after["members"]["Rotation"]["value"]["w"] = .5
    assert evidence.compare_subtree(before, after).status == "failed"
    after["members"] = deepcopy(members)
    after["members"]["OrderOffset"]["value"] = 2**60
    assert evidence.compare_subtree(before, after).status == "failed"
    assert evidence.compare_typed(field("ResoniteLink.float3", {"x": 1, "y": 0, "z": 0}),
                                  field("ResoniteLink.float3", {"x": True, "y": 0, "z": 0})).status == "failed"


def test_sdk_common_metadata_keeps_native_persistent_control_and_explicit_values():
    # S: dropping native persistent after accepting SDK bookkeeping hides a
    # changed/missing control. Bookkeeping GUIDs are NOT deployed member IDs.
    # Replacing an explicit false with the initializer also loses semantics.
    record, actual = sdk_record(), runtime_bool()
    children = record["Object"]["Children"][0]["Children"][0]["Children"]
    raw_components = children[0]["Components"]["Data"] + children[1]["Components"]["Data"]
    for i, (raw, observed) in enumerate(zip(raw_components, actual)):
        raw["Data"]["persistant-ID"] = f"00000000-0000-0000-0000-{i+64:012x}"
        observed["members"]["persistent"] = field("System.Boolean", True, f"Reso_E{i}A")
    args = {"module": "BoolDrive", "id_map": {"node-value": "Reso_1", "node-drive": "Reso_2", "node-proxy": "Reso_3"},
            "private_bindings": {"Driven": "Reso_FF"}}
    assert evidence.compare_sdk_runtime(record, actual, **args).status == "passed"
    # S: do not silently discard one of two ambiguous metadata identities.
    ambiguous = deepcopy(record)
    ambiguous_raw = ambiguous["Object"]["Children"][0]["Children"][0]["Children"][0]["Components"]["Data"][0]["Data"]
    ambiguous_raw["persistent-ID"] = "00000000-0000-0000-0000-000000000099"
    assert evidence.compare_sdk_runtime(ambiguous, actual, **args).status != "passed"
    actual[0]["members"]["persistent"]["value"] = False
    assert evidence.compare_sdk_runtime(record, actual, **args).status == "failed"
    missing = deepcopy(actual)
    del missing[0]["members"]["persistent"]
    assert evidence.compare_sdk_runtime(record, missing, **args).status != "passed"
    raw_components[0]["Data"]["persistent"] = {"ID": "explicit-field", "Data": False}
    assert evidence.compare_sdk_runtime(record, actual, **args).status == "passed"
    actual[0]["members"]["persistent"]["value"] = True
    assert evidence.compare_sdk_runtime(record, actual, **args).status == "failed"
    raw_components[0]["Data"]["persistent"]["Data"] = 0
    actual[0]["members"]["persistent"] = field("System.Int32", 0, "Reso_E0A")
    assert evidence.compare_sdk_runtime(record, actual, **args).status != "passed"


def test_known_empty_output_identity_is_preserved_through_nested_members():
    # S: an identity-only mapped EmptyElement is observed, not missing; ignoring
    # it would hide a rewired output. This does not authorize an empty writer.
    expected, actual = subtree(), subtree()
    outputs = {"kind": "list", "id": "Reso_EB", "elements": [
        {"kind": "syncObject", "id": "Reso_EC", "members": {
            "Source": {"kind": "empty", "id": "Reso_ED"}}}]}
    expected["members"] = {"Outputs": deepcopy(outputs)}
    actual["members"] = {"Outputs": deepcopy(outputs)}
    assert evidence.compare_subtree(expected, actual).status == "passed"
    member = actual["members"]["Outputs"]["elements"][0]["members"]["Source"]
    member["id"] = "Reso_EE"
    assert evidence.compare_subtree(expected, actual).status == "failed"
    del member["id"]
    assert evidence.compare_subtree(expected, actual).status == "inconclusive"
    assert evidence.compare_typed({"kind": "empty", "id": "Reso_ED"},
                                  {"kind": "empty", "id": "Reso_ED"}).status == "inconclusive"
