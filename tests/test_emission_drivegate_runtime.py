"""S: immutable SDK evidence and owned fixture scope, not caller PASS/ID lists."""
from copy import deepcopy
import json
import os
import sys

from test_emission_drivegate_evidence import (
    sealed_fixture, sdk_record, runtime_bool, field, phase_baseline,
)


from scripts.emission_editor import drivegate_runtime as runtime


def fake_parser(root, monkeypatch):
    executable = root / "flux-sdk"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
if len(sys.argv) != 5 or sys.argv[1] != 'parse-record' or sys.argv[3] != '--out': sys.exit(99)
if pathlib.Path(sys.argv[2]).read_bytes() != b'sealed-sdk-record': sys.exit(98)
pathlib.Path(sys.argv[4]).write_text(os.environ['FAKE_SDK_RECORD'])
print('parsed')
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_SDK_RECORD", json.dumps(sdk_record()))


def owned_fixture():
    def slot(ident, name, components=(), children=()):
        return {"id": ident, "name": name, "isReferenceOnly": False,
                "components": list(components), "children": list(children)}
    types = {"System.Int32": "int", "System.Boolean": "bool", "ResoniteLink.colorX": "colorX"}
    fields = [{"id": "field-" + name, "type": "[FrooxEngine]FrooxEngine.ValueField<" + types[member['type']] + ">",
               "members": {"Value": member}} for name, member in phase_baseline().items()]
    children = [slot("fields", "Private fields", fields), slot("probe", "DriveGateProbe"),
                slot("color", "ColorDrive"), slot("bool", "BoolDrive", children=[
                    slot("bool-module", "BoolDrive", children=[
                        slot("bool-value", "Driven", runtime_bool()[:1]),
                        slot("bool-output", "Output:Driven", runtime_bool()[1:])])])]
    tree = slot("fixture", "ResoLoop_Test_EmissionEditor_Drivegate_offline",
                [{"id": "objectroot", "type": "[FrooxEngine]FrooxEngine.ObjectRoot", "members": {}}], children)
    # Bind the actual proxy to the independently declared private BoolTarget.
    children[3]["children"][0]["children"][1]["components"][1]["members"]["Drive"]["targetId"] = phase_baseline()["BoolTarget"]["id"]
    state = {"schemaVersion": 2, "ownershipKey": "emission-drivegate-offline", "assets": {},
             "slots": {"drivegate-root": {"id": "fixture", "path": "Root/ResoLoop_Test_EmissionEditor_Drivegate_offline"},
                       **{"drivegate-" + key: {"id": key, "path": "Root/private/" + name}
                          for key, name in (("fields", "Private fields"), ("probe", "DriveGateProbe"), ("color", "ColorDrive"), ("bool", "BoolDrive"))}},
             "components": {"drivegate-objectroot": {"id": "objectroot", "slotKey": "drivegate-root", "type": "[FrooxEngine]FrooxEngine.ObjectRoot", "typeOrdinal": 0},
                            **{"drivegate-" + name: {"id": "field-" + name, "slotKey": "drivegate-fields", "type": comp["type"], "typeOrdinal": 0}
                               for name, comp in zip(phase_baseline(), fields)}}}
    tree["components"].append({
        "id": "provenance", "type": "[FrooxEngine]FrooxEngine.AI_GeneratedContent",
        "members": {"persistent": field("System.Boolean", True, "prov-persistent"),
                    "UpdateOrder": field("System.Int32", 0, "prov-order"),
                    "Enabled": field("System.Boolean", True, "prov-enabled"),
                    "Source": field("System.String", "[resoloop 0.2.0]", "prov-source")}})
    state["components"]["drivegate-root/component:FrooxEngine.AI_GeneratedContent:0"] = {
        "id": "provenance", "slotKey": "drivegate-root", "type": "[FrooxEngine]FrooxEngine.AI_GeneratedContent", "typeOrdinal": 0}
    # State paths must represent actual stable ancestry, not merely matching IDs.
    for key, value in state["slots"].items():
        if key != "drivegate-root":
            value["path"] = state["slots"]["drivegate-root"]["path"] + "/" + next(c['name'] for c in children if c['id'] == value['id'])
    return tree, json.dumps(state).encode()


def test_parsed_record_is_bound_to_verified_sealed_brson(tmp_path, monkeypatch):
    # S: substituting an unsealed parsed graph can bless a different deployed graph.
    package, execution = sealed_fixture(tmp_path)
    fake_parser(tmp_path, monkeypatch)
    directory = tmp_path / "parsed"
    assert runtime.parse_sealed_record(package, "BoolDrive", directory).status == "passed"
    assert runtime.load_sealed_record(package, "BoolDrive", directory).status == "passed"
    parsed = directory / "record.json"
    data = json.loads(parsed.read_bytes())
    data["Types"][1] = "foreign"
    parsed.write_text(json.dumps(data))
    assert runtime.load_sealed_record(package, "BoolDrive", directory).status != "passed"


def test_graph_binding_is_derived_from_complete_owned_fixture_not_caller_ids(tmp_path, monkeypatch):
    # S: accepting an external component list/private ID map as ownership permits
    # Drive against another panel. Derive bindings from declared fields in-tree.
    package, execution = sealed_fixture(tmp_path)
    fake_parser(tmp_path, monkeypatch)
    directory = tmp_path / "parsed"
    runtime.parse_sealed_record(package, "BoolDrive", directory)
    tree, state = owned_fixture()
    args = dict(package=package, execution=execution, reviewed_tree=deepcopy(tree), state_bytes=state,
                parsed_records={"bool": directory}, id_maps={"bool": {"node-value": "Reso_1", "node-drive": "Reso_2", "node-proxy": "Reso_3"}}, roles=("bool",))
    assert runtime.assess_owned_graph(tree, **args).status == "passed"
    foreign = deepcopy(tree)
    foreign["children"][3]["children"][0]["children"][1]["components"][1]["members"]["Drive"]["targetId"] = "another-panel"
    # Even a reviewed tree cannot redefine what the declared private port means.
    assert runtime.assess_owned_graph(foreign, **(args | {"reviewed_tree": deepcopy(foreign)})).status == "failed"
    wrong_state = json.loads(state)
    wrong_state["components"]["drivegate-BoolTarget"]["id"] = "another-panel"
    assert runtime.assess_owned_graph(tree, **(args | {"state_bytes": json.dumps(wrong_state).encode()})).status != "passed"
    # A matching list of components outside the stable module parent is not enough.
    misplaced = deepcopy(tree)
    misplaced["children"][2]["children"] = misplaced["children"][3]["children"]
    misplaced["children"][3]["children"] = []
    assert runtime.assess_owned_graph(misplaced, **(args | {"reviewed_tree": deepcopy(misplaced)})).status != "passed"


def test_generated_root_provenance_is_exact_and_never_a_private_value(tmp_path, monkeypatch):
    # S: ignoring generated metadata or trusting a reviewed tree alone can admit
    # an undeclared component, wrong attribution, or a marker on another parent.
    package, execution = sealed_fixture(tmp_path)
    fake_parser(tmp_path, monkeypatch)
    parsed = tmp_path / "parsed"
    assert runtime.parse_sealed_record(package, "BoolDrive", parsed).status == "passed"
    tree, state_bytes = owned_fixture()
    state = json.loads(state_bytes)
    marker_key = "drivegate-root/component:FrooxEngine.AI_GeneratedContent:0"
    def assess(current, current_state):
        return runtime.assess_owned_graph(current, package=package, execution=execution,
            reviewed_tree=deepcopy(current), state_bytes=json.dumps(current_state).encode(),
            parsed_records={"bool": parsed}, id_maps={"bool": {
                "node-value": "Reso_1", "node-drive": "Reso_2", "node-proxy": "Reso_3"}}, roles=("bool",))

    result = assess(tree, state)
    assert result.status == "passed", result.issues
    assert set(result.data[0]) == set(phase_baseline())  # Marker is not a private port.
    missing, missing_state = deepcopy(tree), deepcopy(state)
    missing["components"].pop()
    del missing_state["components"][marker_key]
    assert assess(missing, missing_state).status != "passed"
    wrong_source = deepcopy(tree)
    wrong_source["components"][-1]["members"]["Source"]["value"] = "another tool"
    assert assess(wrong_source, state).status != "passed"
    missing_source = deepcopy(tree)
    del missing_source["components"][-1]["members"]["Source"]
    assert assess(missing_source, state).status != "passed"
    relocated, relocated_state = deepcopy(tree), deepcopy(state)
    relocated["children"][0]["components"].append(relocated["components"].pop())
    relocated_state["components"][marker_key]["slotKey"] = "drivegate-fields"
    assert assess(relocated, relocated_state).status != "passed"
    extra = deepcopy(tree)
    extra["components"].append({"id": "extra", "type": "[FrooxEngine]FrooxEngine.ObjectRoot", "members": {}})
    assert assess(extra, state).status != "passed"


def staged_fixture(root, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from scripts.emission_editor.read_receipts import ReadRequest
    from scripts.emission_editor.drivegate_evidence import NativeIdentity, ReadWindow
    from scripts.emission_editor.drivegate_capture import DiffRequest, collect_diff
    from test_emission_drivegate_capture import fake_cli
    from test_emission_drivegate_evidence import raw_read, identity_data, START
    package, execution = sealed_fixture(root)
    fake_parser(root, monkeypatch)
    parsed = root / "parsed"
    runtime.parse_sealed_record(package, "BoolDrive", parsed)
    tree, state = owned_fixture()
    state_path = execution / "runtime" / "bool" / "world.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(state)
    state_data = json.loads(state)
    operations = [{"action": "no-op", "kind": kind, "key": key, "path": value.get("path", "Root/private/" + key),
                   "atomic": False, "recovery": "checkpoint-and-reapply"}
                  for kind, table in (("slot", state_data["slots"]), ("component", state_data["components"]))
                  for key, value in table.items()]
    plan = {"valid": True, "schemaVersion": "1", "ownershipKey": "emission-drivegate-offline",
            "stateFile": str(state_path), "operations": operations, "changes": [],
            "creates": 0, "updates": 0, "renames": 0, "deletes": 0, "noOps": len(operations),
            "atomic": False, "recovery": "checkpoint-and-reapply", "warnings": []}
    fake_cli(root, monkeypatch)
    monkeypatch.setenv("FAKE_DIFF_DATA", json.dumps(plan))
    url = "ws://localhost:16070"
    started = datetime.now(UTC) - timedelta(seconds=10)
    offset = (started - START).total_seconds()
    native = NativeIdentity("Reso_AA", "Value", "世界・試験")
    before = raw_read(root, "session-before", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), offset)
    # The raw fixture IDs are intentionally symbolic in lower-level graph tests;
    # original collector request selectors remain valid actual CLI ID shapes.
    tree["id"] = "Reso_FA"
    state_data["slots"]["drivegate-root"]["id"] = "Reso_FA"
    state = json.dumps(state_data).encode()
    state_path.write_bytes(state)
    fixture_request = ReadRequest("inspect", url, "Reso_FA", 64)
    fixture = raw_read(root, "fixture", fixture_request, tree, offset + 1)
    protected_tree = {"id": "Reso_EA", "name": "protected・panel", "isReferenceOnly": False,
                      "components": [], "children": []}
    protected_request = ReadRequest("inspect", url, "Reso_EA", 64)
    protected = raw_read(root, "protected", protected_request, protected_tree, offset + 2)
    diff_request = DiffRequest(url, execution / "fixture.json", state_path)
    diff_dir = root / "diff"
    collect_diff(diff_request, diff_dir, cwd=root)
    finished = datetime.now(UTC) + timedelta(seconds=2)
    after = raw_read(root, "session-after", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), (finished - START).total_seconds() - 1)
    stage = runtime.DeployedStage(
        window=ReadWindow(url, started, finished, finished), identity=native,
        before_session=before, after_session=after, fixture_read=(fixture_request, fixture),
        protected_read=(protected_request, protected), reviewed_fixture=deepcopy(tree),
        protected_baseline=deepcopy(protected_tree), diff_directory=diff_dir,
        diff_request=diff_request, expected_state=state, expected_plan=plan,
        parsed_records={"bool": parsed}, id_maps={"bool": {"node-value": "Reso_1", "node-drive": "Reso_2", "node-proxy": "Reso_3"}}, roles=("bool",))
    return package, execution, stage


def test_deployed_stage_requires_raw_session_protection_diff_scope_and_sdk_together(tmp_path, monkeypatch):
    # S: checking isolated values/graphs must not mask a missing provenance gate.
    from dataclasses import replace
    package, execution, stage = staged_fixture(tmp_path, monkeypatch)
    assert runtime.assess_deployed_stage(package, execution, stage).status == "passed"
    # A caller PASS string cannot replace an absent original diff capture.
    assert runtime.assess_deployed_stage(package, execution, replace(stage, diff_directory=tmp_path / "missing")).status != "passed"
    wrong = deepcopy(stage.protected_baseline)
    wrong["name"] = "another panel"
    assert runtime.assess_deployed_stage(package, execution, replace(stage, protected_baseline=wrong)).status == "failed"
    assert not runtime.assess_deployed_stage(package, execution, stage).nativeSucceeded
    # The same raw/session/diff gate must select BOTH roots from a Root read
    # which also contains the owned fixture, and require each fresh no-op diff.
    from scripts.emission_editor import drivegate_capture
    from scripts.emission_editor.read_receipts import ReadRequest
    from test_emission_drivegate_evidence import raw_read, START
    from test_emission_drivegate_protection import protection_inputs
    from datetime import timedelta
    protection = protection_inputs(tmp_path)
    templates, fresh = [], []
    class Clock:
        ticks = iter(range(4,8))
        @classmethod
        def now(cls, zone): return stage.window.started + timedelta(seconds=next(cls.ticks))
    with monkeypatch.context() as patched:
        patched.setattr(drivegate_capture,'datetime',Clock)
        for entry in protection.plans:
            plan = entry.expected_plan | {'valid':True,'schemaVersion':'1','creates':0,'updates':0,
                'noOps':len(entry.expected_plan['operations']),'renames':0,'deletes':0,'atomic':False,'recovery':'checkpoint-and-reapply','warnings':[]}
            template = replace(entry,expected_plan=plan)
            template.request.declaration.write_bytes(template.sealed_declaration)
            template.request.state.write_bytes(template.expected_state)
            monkeypatch.setenv('FAKE_DIFF_DATA',json.dumps(plan))
            drivegate_capture.collect_diff(template.request,template.directory,cwd=tmp_path)
            templates.append(template); fresh.append(template)
    protection = replace(protection,plans=tuple(templates))
    root_tree = deepcopy(protection.context)
    root_tree['position'] = {'x':4.25,'y':.5,'z':1.125}
    root_tree['children'].append(deepcopy(stage.reviewed_fixture))
    rr = ReadRequest('inspect',stage.window.url,'Root',64)
    root_read = raw_read(tmp_path,'protected-root',rr,root_tree,(stage.window.started-START).total_seconds()+2)
    multi = replace(stage,protected_baseline=protection,protected_read=(rr,root_read),protected_diffs=tuple(fresh))
    result = runtime.assess_deployed_stage(package,execution,multi)
    assert result.status == 'passed', result.issues
    assert runtime.assess_deployed_stage(package,execution,replace(multi,protected_diffs=tuple(fresh[:1]))).status != 'passed'


def phase_sdk(module):
    """Hand-built compiler-shape graph for stage coupling, not a native simulator.

    Phase values below are independently hand-derived in phases(); this graph
    fixture exercises provenance/ownership wiring, not the real PG's semantics.
    """
    nodes = []
    execution_nodes = "[ProtoFluxBindings]FrooxEngine.ProtoFlux.Runtimes.Execution.Nodes."
    core_nodes = "[ProtoFluxBindings]FrooxEngine.FrooxEngine.ProtoFlux.CoreNodes."
    def node(ident, slot, typ, **members):
        nodes.append((ident, slot, typ, {"UpdateOrder": 0, "Enabled": True} | members))
        return ident
    def col(prefix, slot, values):
        refs = {axis: node(prefix + axis, prefix + axis, execution_nodes + "ValueInput<float>", Value=value)
                for axis, value in zip(("R", "G", "B", "A"), values[:4])}
        enum = node(prefix + "enum", prefix + "enum", execution_nodes + "ValueInput<int>", Value=values[4])
        profile = node(prefix + "profile", prefix + "profile", execution_nodes + "Enums.IntToEnum<[Renderite.Shared]Renderite.Shared.ColorProfile>", Value=enum)
        return node(prefix, slot, execution_nodes + "Operators.Pack_ColorX", **refs, Profile=profile)
    if module == "BoolDrive":
        return sdk_record()
    if module == "ColorDrive":
        value = col("color", "Driven", (1.5, .25, 3, .625, 0))
        drive = node("drive", "Output:Driven", core_nodes + "ValueFieldDrive<colorX>", Value=value)
        node("proxy", "Output:Driven", "[FrooxEngine]FrooxEngine.ProtoFlux.CoreNodes.FieldDriveBase<colorX>+Proxy", Node=drive, Path=[], Drive=None)
    else:
        sources = {}
        for name, member in phase_baseline().items():
            native_type = {"System.Int32": "int", "System.Boolean": "bool", "ResoniteLink.colorX": "colorX"}[member["type"]]
            glob = node("global-" + name, "Input:" + name,
                        "[FrooxEngine]FrooxEngine.ProtoFlux.GlobalReference<[FrooxEngine]FrooxEngine.IValue<" + native_type + ">>", Reference=None)
            sources[name] = node("source-" + name, "Input:" + name, core_nodes + "ValueSource<" + native_type + ">", Source=glob)
        host = node("host", "host", execution_nodes + "FrooxEngine.Users.HostUser")
        node("entry", "entry", execution_nodes + "Actions.FireOnValueChange<int>", OnlyForUser=host, Value=sources["Command"], OnChanged="write-ColorTarget")
        for target, outcome in (("ColorTarget", "ColorOutcome"), ("BoolTarget", "BoolOutcome")):
            edges = {}
            for edge, number in (("OnWritten", 1), ("OnFail", 2)):
                literal = node(target + edge + "literal", target + edge + "literal", execution_nodes + "ValueInput<int>", Value=number)
                edges[edge] = node(target + edge, target + edge, execution_nodes + "ValueWrite<int>",
                                   Variable=sources[outcome], Value=literal, OnWritten=None, OnFail=None)
            node("write-" + target, "write-" + target,
                 execution_nodes + "ValueWrite<[FrooxEngine]FrooxEngine.ProtoFlux.FrooxEngineContext," + ("colorX>" if target == "ColorTarget" else "bool>"),
                 Variable=sources[target], Value=sources[target], **edges)
        for suffix, values in (("a", (1.5, .25, 3, .625, 0)), ("b", (.875, 5.5, .0625, .8125, 1)), ("c", (2.5, .75, .125, .375, 0))):
            col("color-" + suffix, "_" + suffix, values)
    types = list(dict.fromkeys(n[2] for n in nodes))
    def slot(name, components=(), children=()):
        return {"Name": {"Data": name}, "Components": {"Data": list(components)}, "Children": list(children)}
    grouped = {}
    for ident, name, typ, members in nodes:
        grouped.setdefault(name, []).append({"Type": types.index(typ), "Data": {"ID": ident,
            **{k: {"ID": ident + k, "Data": v} for k, v in members.items()}}})
    return {"Types": types, "Object": slot(module, children=[slot("Flux", children=[slot(module,
        children=[slot(name, comps) for name, comps in grouped.items()])])])}


def phase_stage_inputs(root, monkeypatch):
    from datetime import timedelta
    from scripts.emission_editor import drivegate_capture
    from scripts.emission_editor.drivegate_evidence import NativeIdentity, ReadWindow
    from scripts.emission_editor.read_receipts import ReadRequest
    from test_emission_drivegate_capture import fake_cli
    from test_emission_drivegate_evidence import raw_read, identity_data, START, phases
    package, execution = sealed_fixture(root)
    fake_parser(root, monkeypatch)
    parsed, maps, live = {}, {}, {}
    for index, (role, module) in enumerate((("probe", "DriveGateProbe"), ("color", "ColorDrive"), ("bool", "BoolDrive"))):
        record = phase_sdk(module)
        monkeypatch.setenv("FAKE_SDK_RECORD", json.dumps(record))
        directory = root / ("parsed-" + role)
        assert runtime.parse_sealed_record(package, module, directory).status == "passed"
        parsed[role] = directory
        module_node = record["Object"]["Children"][0]["Children"][0]
        ids = [c["Data"]["ID"] for s in module_node["Children"] for c in s["Components"]["Data"]]
        mapping = {ident: "Reso_" + format(4096 * (index + 1) + i, "X") for i, ident in enumerate(ids)}
        maps[role] = mapping
        counter = iter(range(65536 * (index + 1), 65536 * (index + 2)))
        slots = []
        for s in module_node["Children"]:
            components = []
            name = s["Name"]["Data"]
            for c in s["Components"]["Data"]:
                raw = c["Data"]
                typ = record["Types"][c["Type"]]
                members = {}
                for key, item in raw.items():
                    if not isinstance(item, dict) or "Data" not in item: continue
                    value = item["Data"]
                    mid = "Reso_" + format(next(counter), "X")
                    port = name.split(":", 1)[1] if ":" in name else None
                    if (key == "Reference" and role == "probe") or key == "Drive":
                        target = phase_baseline()[port if role == "probe" else "ColorTarget" if role == "color" else "BoolTarget"]["id"]
                        member = {"kind": "reference", "id": mid, "targetId": target}
                    elif value is None or (isinstance(value, str) and value in mapping):
                        member = {"kind": "reference", "id": mid}
                        if value is not None: member["targetId"] = mapping[value]
                    elif isinstance(value, list):
                        member = {"kind": "list", "id": mid, "elements": []}
                    else:
                        native_type = "System.Boolean" if type(value) is bool else "System.Single" if key == "Value" and "ValueInput<float>" in typ else "System.Int32"
                        member = field(native_type, value, mid)
                    members[key] = member
                components.append({"id": mapping[raw["ID"]], "type": typ, "members": members})
            slots.append({"id": "slot-" + role + name, "name": name, "isReferenceOnly": False, "components": components, "children": []})
        live[role] = {"id": "module-" + role, "name": module, "isReferenceOnly": False, "components": [], "children": slots}
    fake_cli(root, monkeypatch)
    tree, state = owned_fixture()
    state_data = json.loads(state)
    tree["id"] = state_data["slots"]["drivegate-root"]["id"] = "Reso_FA"
    state = json.dumps(state_data).encode()
    state_path = execution / "runtime" / "probe" / "world.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(state)
    operations = [{"action": "no-op", "kind": kind, "key": key, "path": entry.get("path", "Root/private/" + key),
                   "atomic": False, "recovery": "checkpoint-and-reapply"}
                  for kind, table in (("slot", state_data["slots"]), ("component", state_data["components"])) for key, entry in table.items()]
    plan = {"valid": True, "schemaVersion": "1", "ownershipKey": "emission-drivegate-offline", "stateFile": str(state_path),
            "operations": operations, "changes": [], "creates": 0, "updates": 0, "noOps": len(operations), "renames": 0, "deletes": 0,
            "atomic": False, "recovery": "checkpoint-and-reapply", "warnings": []}
    monkeypatch.setenv("FAKE_DIFF_DATA", json.dumps(plan))
    result = []
    for phase, fields in [(0, phase_baseline()), *phases()]:
        sub = root / ("phase-" + str(phase))
        sub.mkdir()
        roles = ("probe", "color", "bool") if phase == 3 else ("probe", "bool") if phase == 4 else ("probe",)
        observed_tree = deepcopy(tree)
        for comp in observed_tree["children"][0]["components"]:
            comp["members"]["Value"] = deepcopy(fields[comp["id"].removeprefix("field-")])
        for i, role in enumerate(("probe", "color", "bool"), 1):
            observed_tree["children"][i]["children"] = [deepcopy(live[role])] if role in roles else []
        seconds = phase * 30
        before = raw_read(sub, "before", ReadRequest("component-inspect", "ws://localhost:16070", "Reso_AA"), identity_data(), seconds + 1)
        fr = ReadRequest("inspect", "ws://localhost:16070", "Reso_FA", 64)
        fixture = raw_read(sub, "fixture", fr, observed_tree, seconds + 2)
        protected_tree = {"id": "Reso_EA", "name": "protected", "isReferenceOnly": False, "components": [], "children": []}
        pr = ReadRequest("inspect", "ws://localhost:16070", "Reso_EA", 64)
        protected = raw_read(sub, "protected", pr, protected_tree, seconds + 3)
        # Deterministic clock at the I/O boundary; actual CLI subprocess still runs.
        class Clock:
            ticks = iter((seconds + 6, seconds + 7))
            @classmethod
            def now(cls, zone): return START + timedelta(seconds=next(cls.ticks))
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(drivegate_capture, "datetime", Clock)
            dr = drivegate_capture.DiffRequest("ws://localhost:16070", execution / "fixture.json", state_path)
            drivegate_capture.collect_diff(dr, sub / "diff", cwd=root)
        after = raw_read(sub, "after", ReadRequest("component-inspect", "ws://localhost:16070", "Reso_AA"), identity_data(), seconds + 10)
        result.append(runtime.DeployedStage(window=ReadWindow("ws://localhost:16070", START + timedelta(seconds=seconds), START + timedelta(seconds=seconds+12), START + timedelta(seconds=300)),
            identity=NativeIdentity("Reso_AA", "Value", "世界・試験"), before_session=before, after_session=after,
            fixture_read=(fr, fixture), protected_read=(pr, protected), reviewed_fixture=deepcopy(observed_tree), protected_baseline=protected_tree,
            diff_directory=sub / "diff", diff_request=dr, expected_state=state, expected_plan=plan,
            parsed_records={r: parsed[r] for r in roles}, id_maps={r: maps[r] for r in roles}, roles=roles))
    return package, execution, result


def test_phase_stage_aggregate_requires_helper_lifecycle_and_distinct_native_batches(tmp_path, monkeypatch):
    # S: a value-only phase PASS cannot cover a retained helper or replayed batch.
    from dataclasses import replace
    package, execution, stages = phase_stage_inputs(tmp_path, monkeypatch)
    args = (package, execution, stages[0], list(enumerate(stages[1:], 1)))
    result = runtime.assess_phase_stages(*args)
    assert result.status == "passed", result.issues
    assert not result.nativeSucceeded and not result.complete and not result.executionPermission
    # Updating a per-phase expectation must not silently adopt panel damage.
    import hashlib
    changed_panel = deepcopy(stages[4].protected_baseline)
    changed_panel["name"] = "silently changed panel"
    raw_path = stages[4].protected_read[1] / "stdout.bin"
    receipt_path = stages[4].protected_read[1] / "receipt.json"
    original_raw, original_receipt = raw_path.read_bytes(), receipt_path.read_bytes()
    raw_path.write_text(json.dumps({"ok": True, "data": changed_panel}))
    metadata = json.loads(original_receipt)
    metadata["stdoutSha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(metadata))
    altered = stages.copy()
    altered[4] = replace(altered[4], protected_baseline=changed_panel)
    assert runtime.assess_phase_stages(package, execution, altered[0], list(enumerate(altered[1:], 1))).status != "passed"
    raw_path.write_bytes(original_raw)
    receipt_path.write_bytes(original_receipt)
    damaged = stages.copy()
    damaged[4] = replace(damaged[4], roles=("probe", "color", "bool"))
    assert runtime.assess_phase_stages(package, execution, damaged[0], list(enumerate(damaged[1:], 1))).status != "passed"
    replayed = stages.copy()
    replayed[5] = stages[2]
    assert runtime.assess_phase_stages(package, execution, replayed[0], list(enumerate(replayed[1:], 1))).status != "passed"


def test_cleanup_stage_requires_the_owned_restored_fixture_absent_and_siblings_preserved(tmp_path, monkeypatch):
    # S: deletion of a different root or merely reporting deletion cannot finish cleanup.
    from datetime import timedelta
    import hashlib
    from scripts.emission_editor.drivegate_evidence import ReadWindow
    from scripts.emission_editor.read_receipts import ReadRequest
    from test_emission_drivegate_evidence import raw_read, identity_data, START
    package, execution, stages = phase_stage_inputs(tmp_path, monkeypatch)
    last = stages[-1]
    # Real ID-inspect paths start at fixture Name; Root-inspect paths start at
    # Root. Preserve all parent metadata and rebase only the path on a copy.
    from dataclasses import replace
    relative = deepcopy(last.reviewed_fixture)
    def relative_paths(node,parent,path):
        node['parentId'],node['path'] = parent,path
        for child in node['children']:
            relative_paths(child,node['id'],path+'/'+child['name'])
    relative_paths(relative,'Root',relative['name'])
    stdout = json.dumps({'ok':True,'data':relative}).encode()
    (last.fixture_read[1]/'stdout.bin').write_bytes(stdout)
    receipt_path = last.fixture_read[1]/'receipt.json'
    receipt = json.loads(receipt_path.read_bytes())
    receipt['stdoutSha256'] = hashlib.sha256(stdout).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    last = replace(last,reviewed_fixture=relative)
    root = tmp_path / "cleanup"
    root.mkdir()
    url = last.window.url
    parent = {"id": "Root", "name": "Root", "isReferenceOnly": False, "components": [],
              "children": [deepcopy(last.reviewed_fixture), deepcopy(last.protected_baseline),
                           {"id": "unprotected-sibling", "name": "other", "isReferenceOnly": False,
                            "components": [], "children": []}]}
    # Root motion is not collateral deletion; the explicit protected panel is
    # still exact. This positive control must survive the scoped deletion gate.
    parent['path'],parent['parentId'] = 'Root',None
    def prefix_paths(node):
        node['path'] = 'Root/'+node['path']
        for child in node['children']: prefix_paths(child)
    prefix_paths(parent['children'][0])
    parent["position"] = {"x": 0, "y": 0, "z": 0}
    after_tree = deepcopy(parent)
    after_tree["children"].pop(0)
    after_tree["position"]["x"] = 1.25
    before = raw_read(root, "session-before", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), 241)
    request = ReadRequest("inspect", url, "Root", 64)
    pb = raw_read(root, "parent-before", request, parent, 242)
    pa = raw_read(root, "parent-after", request, after_tree, 243)
    pr = ReadRequest("inspect", url, "Reso_EA", 64)
    protected = raw_read(root, "protected", pr, last.protected_baseline, 244)
    after = raw_read(root, "session-after", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), 245)
    cleanup = runtime.CleanupStage(window=ReadWindow(url, START + timedelta(seconds=240), START + timedelta(seconds=250), START + timedelta(seconds=300)),
        before_session=before, after_session=after, parent_before=(request, pb), parent_after=(request, pa),
        protected_read=(pr, protected), reviewed_parent=deepcopy(parent))
    assert runtime.assess_cleanup_stage(package, execution, last, cleanup).status == "passed"
    # A new timestamp must not launder a physical stdout reused from phase six.
    original = (protected / "stdout.bin").read_bytes()
    (protected / "stdout.bin").unlink()
    os.link(last.protected_read[1] / "stdout.bin", protected / "stdout.bin")
    assert runtime.assess_cleanup_stage(package, execution, last, cleanup).status != "passed"
    (protected / "stdout.bin").unlink()
    (protected / "stdout.bin").write_bytes(original)
    # Even when the owned target is gone, loss of an outside object cannot be
    # silently reported as scoped cleanup. Its values need not be protected.
    bad_after = deepcopy(after_tree)
    bad_after["children"].pop(1)
    stdout = json.dumps({"ok": True, "data": bad_after}).encode()
    (pa / "stdout.bin").write_bytes(stdout)
    metadata = json.loads((pa / "receipt.json").read_bytes())
    metadata["stdoutSha256"] = hashlib.sha256(stdout).hexdigest()
    (pa / "receipt.json").write_text(json.dumps(metadata))
    assert runtime.assess_cleanup_stage(package, execution, last, cleanup).status == "failed"


def test_precreate_scope_uses_absence_and_creation_plan_not_nonexistent_deployed_graph(tmp_path, monkeypatch):
    # S: interpreting a creation plan as preservation or accepting an existing
    # same-named root can target somebody else's object before any deployment.
    from datetime import timedelta
    from dataclasses import replace
    from scripts.emission_editor import drivegate_capture
    from scripts.emission_editor.drivegate_evidence import NativeIdentity, ReadWindow
    from scripts.emission_editor.read_receipts import ReadRequest
    from test_emission_drivegate_capture import fake_cli
    from test_emission_drivegate_evidence import raw_read, identity_data, START
    package, execution = sealed_fixture(tmp_path)
    fake_parser(tmp_path, monkeypatch)
    parsed = {}
    for role, module in (("probe", "DriveGateProbe"), ("color", "ColorDrive"), ("bool", "BoolDrive")):
        monkeypatch.setenv("FAKE_SDK_RECORD", json.dumps(phase_sdk(module)))
        parsed[role] = tmp_path / ("parsed-" + role)
        assert runtime.parse_sealed_record(package, module, parsed[role]).status == "passed"
    fake_cli(tmp_path, monkeypatch)
    url = "ws://localhost:16070"
    from test_emission_drivegate_protection import protection_inputs
    protection = protection_inputs(tmp_path)
    parent = deepcopy(protection.context)
    before = raw_read(tmp_path, "session-before", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), 1)
    pr = ReadRequest("inspect", url, "Root", 64)
    one = raw_read(tmp_path, "protected-one", pr, parent, 2)
    two = raw_read(tmp_path, "protected-two", pr, parent, 3)
    rr = ReadRequest("inspect", url, "Root", 64)
    observed_parent = deepcopy(parent)
    observed_parent["position"] = {"x": 4.25, "y": .5, "z": 1.125}
    # A normal time-dependent Root value must not veto a scoped creation plan.
    root_read = raw_read(tmp_path, "parent", rr, observed_parent, 4)
    _, state = owned_fixture()
    state_data = json.loads(state)
    operations = [{"action": "create", "kind": kind, "key": key, "path": value.get("path", "Root/private/" + key),
                   "atomic": False, "recovery": "checkpoint-and-reapply"}
                  for kind, table in (("slot", state_data["slots"]), ("component", state_data["components"])) for key, value in table.items()]
    state_path = execution / "runtime" / "probe" / "world.json"
    creation = {"valid": True, "schemaVersion": "1", "ownershipKey": "emission-drivegate-offline", "stateFile": str(state_path),
                "operations": operations, "changes": deepcopy(operations), "creates": len(operations), "updates": 0, "noOps": 0, "renames": 0, "deletes": 0,
                "atomic": False, "recovery": "checkpoint-and-reapply", "warnings": []}
    templates = []
    for i,entry in enumerate(protection.plans):
        plan = creation | entry.expected_plan | {'creates':0,'noOps':len(entry.expected_plan['operations'])}
        entry.request.declaration.write_bytes(entry.sealed_declaration)
        entry.request.state.write_bytes(entry.expected_state)
        templates.append(replace(entry,expected_plan=plan,directory=tmp_path/('diff-'+str(i+1))))
    protection = replace(protection,plans=tuple(templates))
    class Clock:
        ticks = iter(range(6,12))
        @classmethod
        def now(cls, zone): return START + timedelta(seconds=next(cls.ticks))
    requests = [drivegate_capture.DiffRequest(url, execution / "fixture.json", state_path),*(p.request for p in templates)]
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(drivegate_capture, "datetime", Clock)
        for index, plan in enumerate((creation,*(p.expected_plan for p in templates))):
            monkeypatch.setenv("FAKE_DIFF_DATA", json.dumps(plan))
            drivegate_capture.collect_diff(requests[index], tmp_path / ("diff-" + str(index)), cwd=tmp_path)
    after = raw_read(tmp_path, "session-after", ReadRequest("component-inspect", url, "Reso_AA"), identity_data(), 14)
    fixture_diff = runtime.DiffInput(tmp_path / "diff-0", requests[0], None, creation, (package / "fixture.json").read_bytes())
    scope = runtime.PrecreateScope(ReadWindow(url, START, START + timedelta(seconds=15), START + timedelta(seconds=16)),
        NativeIdentity("Reso_AA", "Value", "世界・試験"), before, after, (rr, root_read), [(pr, one), (pr, two)],
        deepcopy(parent), protection, fixture_diff, tuple(templates), parsed)
    result = runtime.assess_precreate_scope(package, execution, scope)
    assert result.status == "passed", result.issues
    assert not result.complete and not result.executionPermission
    assert runtime.assess_precreate_scope(package, execution, replace(scope, fixture_diff=templates[0])).status != "passed"
    # A single protected read is not the independent two-process observation.
    assert runtime.assess_precreate_scope(package, execution, replace(scope, protected_reads=[(pr, one)])).status != "passed"
    # The same originals may be revalidated after creation, but the fresh-only
    # entry must NOT silently bless state/replay as a new preflight.
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(state)
    assert runtime.assess_precreate_scope(package, execution, scope).status != 'passed'
    assert runtime.assess_precreate_history(package, execution, scope).status == 'passed'
    assert runtime.assess_precreate_history(package, execution, replace(scope, fixture_diff=templates[0])).status != 'passed'
    (execution / 'unknown.json').write_bytes(b'{}')
    assert runtime.assess_precreate_history(package, execution, scope).status != 'passed'
