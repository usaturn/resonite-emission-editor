"""Sealed SDK/owned-scope stage assessments, never a world mutation runner.

No full-preflight or full-native-success API is exposed here. A passed named
stage proves only its documented inputs. Parse processes are fixed and offline.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

from .drivegate_package import MODULES, _no_links, verify_package, verify_execution
from .drivegate_evidence import (
    Verdict, _result, _tree, _sdk_graph, _sdk_audit, compare_subtree,
    compare_sdk_runtime, verify_runtime_execution, _PHASE_TYPES,
    ReadWindow, NativeIdentity, load_observation, compare_phase_sequence, compare_deletion,
)
from .read_receipts import ReadRequest, load_read, _decode, _digest, _write_new
from .drivegate_capture import DiffRequest, assess_diff, load_diff


def _parser_paths(package, module, directory):
    if module not in MODULES.values():
        raise ValueError("sdk-unknown-module")
    package, directory = Path(package).absolute(), Path(directory).absolute()
    _no_links(package)
    _no_links(directory)
    if directory.is_relative_to(package) or package.is_relative_to(directory):
        raise ValueError("sdk-parser-output-overlaps-package")
    source = package / "sources" / (module + ".pg.brson")
    argv = ["flux-sdk", "parse-record", str(source), "--out", str(directory / "record.json")]
    return package, directory, source, argv


def parse_sealed_record(package: Path, module: str, directory: Path) -> Verdict:
    """Run only parse-record on verified sealed brson into a NEW exclusive area.

    Save actual stdout/stderr/exit/argv, input/package and parsed-output hashes.
    No unsealed JSON argument or caller-supplied successful verdict is accepted.
    """
    package, directory, source, argv = _parser_paths(package, module, directory)
    verify_package(package)
    seal_hash, input_hash = _digest((package / "seal.json").read_bytes()), _digest(source.read_bytes())
    directory.mkdir(mode=0o700, exist_ok=False)
    started = datetime.now(UTC).isoformat()
    error = None
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except OSError as exc:
        error = str(exc)
        stdout, stderr, code = b"", error.encode(errors="replace"), None
    finished = datetime.now(UTC).isoformat()
    _write_new(directory / "stdout.bin", stdout)
    _write_new(directory / "stderr.bin", stderr)
    parsed = directory / "record.json"
    _no_links(parsed)
    output_hash = _digest(parsed.read_bytes()) if parsed.is_file() else None
    receipt = {"module": module, "argv": argv, "startedAt": started, "finishedAt": finished,
               "exitCode": code, "launchError": error, "sealSha256": seal_hash, "brsonSha256": input_hash,
               "recordSha256": output_hash, "stdoutSha256": _digest(stdout), "stderrSha256": _digest(stderr)}
    _write_new(directory / "receipt.json", json.dumps(receipt, ensure_ascii=False, indent=2).encode())
    return load_sealed_record(package, module, directory)


def load_sealed_record(package: Path, module: str, directory: Path) -> Verdict:
    """Reload the original offline parse, rechecking package and parsed bytes."""
    try:
        package, directory, source, argv = _parser_paths(package, module, directory)
        verify_package(package)
        for p in directory.iterdir():
            _no_links(p)
            if not p.is_file():
                raise ValueError("sdk-parse-nonregular-output")
        if {p.name for p in directory.iterdir()} != {"record.json", "receipt.json", "stdout.bin", "stderr.bin"}:
            raise ValueError("missing:sdk-complete-parser-output")
        receipt = _decode((directory / "receipt.json").read_bytes())
        if receipt["module"] != module or receipt["argv"] != argv:
            raise ValueError("sdk-parser-request")
        if type(receipt["exitCode"]) is not int or receipt["exitCode"] != 0 or receipt["launchError"] is not None:
            raise ValueError("sdk-parser-failed")
        for path, key in ((package / "seal.json", "sealSha256"), (source, "brsonSha256"),
                          (directory / "record.json", "recordSha256"), (directory / "stdout.bin", "stdoutSha256"),
                          (directory / "stderr.bin", "stderrSha256")):
            if _digest(path.read_bytes()) != receipt[key]:
                raise ValueError("sdk-parser-input-or-output-changed")
        record = _decode((directory / "record.json").read_bytes())
        graph, exports = _sdk_graph(record, module)
        _sdk_audit(graph, exports, module)
        return Verdict("passed", data=(record,))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        return _result([str(exc) if str(exc).startswith("missing:") else "sdk-artifact:" + str(exc)])


def _owned_scope(package, tree, state_bytes, roles):
    """Join installed ApplyState keys to exact declared stable slots/private fields.

    State is not native session identity and is never replayed/adopted. Its bytes
    must ALSO be bound to a current detailed diff by the observation stage.
    """
    declaration = _decode((Path(package) / "fixture.json").read_bytes())
    state = _decode(state_bytes)
    if (type(state.get("schemaVersion")) is not int or state["schemaVersion"] != 2
            or state.get("ownershipKey") != declaration["ownership"]["key"] or state.get("assets") != {}):
        raise ValueError("fixture-state-ownership-or-version")
    slots, components, issues = _tree(tree)
    if issues:
        raise ValueError("missing:complete-owned-subtree")
    declared_slots = {declaration["slot"]["key"]: (declaration, None)}
    for child in declaration["children"]:
        declared_slots[child["slot"]["key"]] = (child, declaration["slot"]["key"])
    if set(state["slots"]) != set(declared_slots):
        raise ValueError("fixture-state-slot-coverage")
    expected_components, stable, paths = {}, {}, {}
    for key, (declared, parent_key) in declared_slots.items():
        entry = state["slots"][key]
        ident = entry["id"]
        node, parent = slots[ident]
        wanted_parent = state["slots"][parent_key]["id"] if parent_key else None
        if parent != wanted_parent or node["name"] != declared["slot"]["name"]:
            raise ValueError("fixture-stable-slot-identity")
        path = (paths[parent_key] if parent_key else "Root") + "/" + node["name"]
        paths[key] = path
        if entry["path"] != path:
            raise ValueError("fixture-state-slot-path")
        stable[key] = node
        for comp in declared["components"]:
            expected_components[comp["key"]] = (comp, key)
    if tree["id"] != state["slots"]["drivegate-root"]["id"] or set(state["components"]) != set(expected_components):
        raise ValueError("fixture-state-component-coverage")
    fields = {}
    stable_comp_ids = set()
    for key, (declared, slot_key) in expected_components.items():
        entry = state["components"][key]
        comp, parent = components[entry["id"]]
        if (entry["slotKey"] != slot_key or parent != state["slots"][slot_key]["id"]
                or entry["type"] != declared["type"] or comp["type"] != declared["type"]):
            raise ValueError("fixture-private-component-identity")
        stable_comp_ids.add(comp["id"])
        if key != "drivegate-objectroot":
            name = key.removeprefix("drivegate-")
            value = comp["members"].get("Value")
            if not isinstance(value, dict) or value.get("kind") != "field" or value.get("type") != _PHASE_TYPES[name]:
                raise ValueError("missing:declared-private-value")
            fields[name] = value
    # No undeclared component is permitted on the stable fixture/field parents.
    for key in ("drivegate-root", "drivegate-fields"):
        if any(c["id"] not in stable_comp_ids for c in stable[key]["components"]):
            raise ValueError("fixture-undeclared-stable-component")
    if stable["drivegate-fields"]["children"] or len(tree["children"]) != len(declaration["children"]):
        raise ValueError("fixture-undeclared-stable-slot")
    module_components, module_slots = {}, {}
    for role in MODULES:
        parent = stable["drivegate-" + role]
        if parent["components"]:
            raise ValueError("fixture-nodes-on-stable-parent")
        rs, rc, ri = _tree(parent)
        if ri:
            raise ValueError("missing:complete-module-subtree")
        if role not in roles and parent["children"]:
            raise ValueError("fixture-unexpected-helper-or-module")
        module_components[role] = [c for c, _ in rc.values()]
        module_slots[role] = {cid: rs[sid][0]["name"] for cid, (_, sid) in rc.items()}
    # IDs of recursively observed component members must be distinct across ownership.
    ids = set(slots) | set(components)
    def member_ids(member):
        ident = member.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError("fixture-duplicate-or-missing-member-id")
        ids.add(ident)
        for nested in member.get("members", {}).values():
            member_ids(nested)
        for nested in member.get("elements", []):
            member_ids(nested)
    for comp, _ in components.values():
        for member in comp["members"].values():
            member_ids(member)
    return fields, stable, module_components, module_slots


def assess_owned_graph(tree: dict, *, package: Path, execution: Path, reviewed_tree: dict,
                       state_bytes: bytes, parsed_records: dict, id_maps: dict, roles: tuple[str, ...]) -> Verdict:
    """Artifact/scope/graph stage, NOT raw/session/full-preflight evidence.

    Read bindings only from declared private fields in the complete current tree;
    caller ID maps are constrained to ALL components under the stable role parent
    and their SDK slot names. Ownership keys/slot paths/types come from captured
    ApplyState, not caller-provided PASS/IDs. reviewed_tree is an independent full
    baseline (including native defaults), not copied from this observation.
    """
    try:
        if (not roles or len(set(roles)) != len(roles) or not set(roles) <= MODULES.keys()
                or set(parsed_records) != set(roles) or set(id_maps) != set(roles)):
            return Verdict("failed", ("module-role-coverage",))
        artifact = verify_runtime_execution(package, execution)
        if artifact.status != "passed":
            return artifact
        scope = compare_subtree(reviewed_tree, tree)
        if scope.status != "passed":
            return scope
        fields, stable, actual, names = _owned_scope(package, tree, state_bytes, roles)
        for role in roles:
            parsed = load_sealed_record(package, MODULES[role], parsed_records[role])
            if parsed.status != "passed":
                return parsed
            record = parsed.data[0]
            graph, _ = _sdk_graph(record, MODULES[role])
            mapping = id_maps[role]
            if set(mapping) != set(graph) or any(names[role].get(mapping[k]) != c["slot"] for k, c in graph.items()):
                return Verdict("failed", ("sdk-owned-slot-map",))
            bindings = ({k: m["id"] for k, m in fields.items()} if role == "probe" else
                        {"Driven": fields["ColorTarget" if role == "color" else "BoolTarget"]["id"]})
            graph_result = compare_sdk_runtime(record, actual[role], module=MODULES[role],
                                              id_map=mapping, private_bindings=bindings)
            if graph_result.status != "passed":
                return graph_result
        return Verdict("passed", data=(fields, stable))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _result([str(exc) if str(exc).startswith("missing:") else "owned-graph:" + str(exc)])


@dataclass(frozen=True)
class DeployedStage:
    """Original evidence for ONE post-deploy observation, not a PASS assertion.

    reviewed_fixture/protected_baseline/expected_plan must be independently
    reviewed and sealed by the controller. Do not build them from these reads.
    A pre-create stage is different: it has no deployed graph to compare.
    """
    window: ReadWindow
    identity: NativeIdentity
    before_session: Path
    after_session: Path
    fixture_read: tuple[ReadRequest, Path]
    protected_read: tuple[ReadRequest, Path]
    reviewed_fixture: dict
    protected_baseline: dict
    diff_directory: Path
    diff_request: DiffRequest
    expected_state: bytes
    expected_plan: dict
    parsed_records: dict
    id_maps: dict
    roles: tuple[str, ...]


def assess_deployed_stage(package: Path, execution: Path, stage: DeployedStage) -> Verdict:
    """Join raw/session/window, protection, captured diff, owned scope and SDK.

    Returns private fields/stable parents ONLY when every required check passes.
    Does not establish frontmost/exclusion/discover/doctor, initial root absence,
    the phase sequence or final deletion. It is not full preflight/native success.
    """
    try:
        verify_package(package)
        observed = load_observation(stage.before_session, [stage.fixture_read, stage.protected_read],
                                    stage.after_session, window=stage.window, identity=stage.identity)
        if observed.status != "passed":
            return observed
        fixture, protected = observed.data
        if any(request.operation != "inspect" for request, _ in (stage.fixture_read, stage.protected_read)):
            return Verdict("failed", ("stage-complete-inspect-required",))
        protected_result = compare_subtree(stage.protected_baseline, protected)
        if protected_result.status != "passed":
            return protected_result
        fs, fc, _ = _tree(fixture)
        ps, pc, _ = _tree(protected)
        if (set(fs) | set(fc)) & (set(ps) | set(pc)):
            return Verdict("failed", ("stage-protected-fixture-overlap",))
        fixture_bytes = (Path(package) / "fixture.json").read_bytes()
        declaration = _decode(fixture_bytes)
        world_states = {_decode((Path(package) / "manifests" / (r + ".json")).read_bytes())["worldState"] for r in MODULES}
        if (str(stage.diff_request.state) not in world_states
                or stage.expected_plan.get("ownershipKey") != declaration["ownership"]["key"]):
            return Verdict("failed", ("stage-diff-state-or-ownership",))
        diff = assess_diff(stage.diff_directory, request=stage.diff_request, window=stage.window,
                           sealed_declaration=fixture_bytes, expected_state=stage.expected_state,
                           expected_plan=stage.expected_plan)
        if diff.status != "passed":
            return diff
        # A deployed-preservation observation cannot be backed by a creation plan.
        if diff.data[0]["changes"]:
            return Verdict("failed", ("stage-fixture-not-converged",))
        # All declared stable objects must be represented, not an arbitrary subset
        # of plausible no-ops. Paths/types are subsequently joined through state.
        declared_keys = set()
        def keys(node):
            declared_keys.add(("slot", node["slot"]["key"]))
            declared_keys.update(("component", c["key"]) for c in node["components"])
            for child in node["children"]:
                keys(child)
        keys(declaration)
        if {(op["kind"], op["key"]) for op in diff.data[0]["operations"]} != declared_keys:
            return Verdict("failed", ("stage-diff-declaration-coverage",))
        # The diff must lie inside the SAME native-session brackets, not just a
        # broad global attempt window that could encompass a session transition.
        receipt = load_diff(stage.diff_directory)
        before = load_read(stage.before_session)
        after = load_read(stage.after_session)
        if not (datetime.fromisoformat(before["finishedAt"]) <= datetime.fromisoformat(receipt["startedAt"])
                <= datetime.fromisoformat(receipt["finishedAt"]) <= datetime.fromisoformat(after["startedAt"])):
            return Verdict("failed", ("stage-diff-outside-native-bracket",))
        physical = set()
        for directory in (stage.before_session, stage.fixture_read[1], stage.protected_read[1], stage.after_session, stage.diff_directory):
            for name in ("receipt.json", "stdout.bin"):
                stat = (Path(directory) / name).stat()
                key = (stat.st_dev, stat.st_ino)
                if key in physical:
                    return Verdict("failed", ("stage-reused-physical-evidence",))
                physical.add(key)
        return assess_owned_graph(fixture, package=package, execution=execution,
                                  reviewed_tree=stage.reviewed_fixture, state_bytes=stage.expected_state,
                                  parsed_records=stage.parsed_records, id_maps=stage.id_maps, roles=stage.roles)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _result(["missing:deployed-stage-evidence:" + str(exc)])


def assess_phase_stages(package: Path, execution: Path, baseline: DeployedStage,
                        phases: list[tuple[int, DeployedStage]]) -> Verdict:
    """Join stages 0..6, complete phase values and individual helper removals.

    This is an offline evidence assessment, NOT a simulator/native experiment.
    There is no command execution/advance/retry. Full preflight and final fixture
    deletion remain separate required gates; no native-success flag is granted.
    """
    if len(phases) != 6 or any(type(n) is not int or n != i for i, (n, _) in enumerate(phases, 1)):
        return Verdict("inconclusive", ("missing:all-six-ordered-stages",))
    stages = [baseline, *(s for _, s in phases)]
    required_roles = [("probe",), ("probe",), ("probe",), ("probe", "color", "bool"),
                      ("probe", "bool"), ("probe",), ("probe",)]
    fields, parents = [], []
    seen, fingerprints = set(), set()
    previous = None
    try:
        for number, (stage, roles) in enumerate(zip(stages, required_roles)):
            preservation = compare_subtree(baseline.protected_baseline, stage.protected_baseline)
            if preservation.status != "passed":
                return Verdict(preservation.status, ("phase-changed-protected-baseline", *preservation.issues))
            if stage.roles != roles:
                return Verdict("failed", ("phase-helper-presence:" + str(number),))
            if stage.identity != baseline.identity or stage.window.url != baseline.window.url:
                return Verdict("failed", ("phase-native-session-or-url",))
            if previous is not None and stage.window.started < previous:
                return Verdict("failed", ("phase-replayed-or-overlapping-window",))
            previous = stage.window.finished
            for directory in (stage.before_session, stage.fixture_read[1], stage.protected_read[1], stage.after_session, stage.diff_directory):
                for name in ("receipt.json", "stdout.bin"):
                    path = Path(directory) / name
                    stat = path.stat()
                    key = (stat.st_dev, stat.st_ino)
                    if key in seen:
                        return Verdict("failed", ("phase-reused-physical-evidence",))
                    seen.add(key)
                    if name == "receipt.json":
                        digest = _digest(path.read_bytes())
                        if digest in fingerprints:
                            return Verdict("failed", ("phase-replayed-receipt",))
                        fingerprints.add(digest)
            result = assess_deployed_stage(package, execution, stage)
            if result.status != "passed":
                return Verdict(result.status, ("phase-stage:" + str(number), *result.issues))
            current_fields, stable = result.data
            if parents and any(stable[k]["id"] != parents[0][k]["id"] for k in stable):
                return Verdict("failed", ("phase-changed-fixture-identity",))
            fields.append(current_fields)
            parents.append(stable)
        values = compare_phase_sequence(fields[0], list(enumerate(fields[1:], 1)))
        if values.status != "passed":
            return values
        for role, before_phase, after_phase in (("color", 3, 4), ("bool", 4, 5)):
            before_parent = parents[before_phase]["drivegate-" + role]
            after_parent = parents[after_phase]["drivegate-" + role]
            if len(before_parent["children"]) != 1:
                return Verdict("inconclusive", ("missing:unique-owned-helper-root:" + role,))
            reviewed_slots, _, issues = _tree(stages[before_phase].reviewed_fixture)
            if issues:
                return _result(issues)
            expected_parent = reviewed_slots[before_parent["id"]][0]
            removed = compare_deletion(expected_parent, before_parent, after_parent, before_parent["children"][0]["id"])
            if removed.status != "passed":
                return Verdict(removed.status, ("helper-removal:" + role, *removed.issues))
        return Verdict("passed")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _result(["missing:phase-stage-evidence:" + str(exc)])


@dataclass(frozen=True)
class CleanupStage:
    """Fresh native-bracketed full stable-parent reads around final removal."""
    window: ReadWindow
    before_session: Path
    after_session: Path
    parent_before: tuple[ReadRequest, Path]
    parent_after: tuple[ReadRequest, Path]
    protected_read: tuple[ReadRequest, Path]
    reviewed_parent: dict


def assess_cleanup_stage(package: Path, execution: Path, last: DeployedStage,
                         cleanup: CleanupStage) -> Verdict:
    """Verify the owned restored root alone disappeared, never execute deletion.

    This does not replace the earlier six-phase assessment or full preflight.
    A stable parent is essential: reading a missing root alone proves nothing
    about collateral removal, preservation, or its earlier ownership.
    """
    try:
        restored = assess_deployed_stage(package, execution, last)
        if restored.status != "passed":
            return restored
        fields, _ = restored.data
        if (last.roles != ("probe",) or fields["Command"]["value"] != 6 or fields["Done"]["value"] != 6
                or fields["Captured"]["value"] is not True
                or fields["ColorTarget"]["value"] != fields["BeforeColor"]["value"]
                or fields["BoolTarget"]["value"] != fields["BeforeBool"]["value"]):
            return Verdict("failed", ("cleanup-not-restored-phase-six",))
        if cleanup.window.url != last.window.url or cleanup.window.started < last.window.finished:
            return Verdict("failed", ("cleanup-window-or-url",))
        if (cleanup.parent_before[0] != cleanup.parent_after[0]
                or any(r.operation != "inspect" for r, _ in (cleanup.parent_before, cleanup.parent_after, cleanup.protected_read))):
            return Verdict("failed", ("cleanup-stable-parent-request",))
        used = set()
        for directory in (last.before_session, last.fixture_read[1], last.protected_read[1], last.after_session, last.diff_directory,
                          cleanup.before_session, cleanup.parent_before[1], cleanup.parent_after[1], cleanup.protected_read[1], cleanup.after_session):
            for name in ("receipt.json", "stdout.bin"):
                stat = (Path(directory) / name).stat()
                key = (stat.st_dev, stat.st_ino)
                if key in used:
                    return Verdict("failed", ("cleanup-reused-physical-evidence",))
                used.add(key)
        observed = load_observation(cleanup.before_session,
                                    [cleanup.parent_before, cleanup.parent_after, cleanup.protected_read],
                                    cleanup.after_session, window=cleanup.window, identity=last.identity)
        if observed.status != "passed":
            return observed
        before, after, protected = observed.data
        preserved = compare_subtree(last.protected_baseline, protected)
        if preserved.status != "passed":
            return preserved
        slots, _, issues = _tree(before)
        if issues:
            return _result(issues)
        target = last.reviewed_fixture["id"]
        if target not in slots:
            return Verdict("inconclusive", ("missing:owned-fixture-before-cleanup",))
        same_fixture = compare_subtree(last.reviewed_fixture, slots[target][0])
        if same_fixture.status != "passed":
            return same_fixture
        return compare_deletion(cleanup.reviewed_parent, before, after, target)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _result(["missing:cleanup-evidence:" + str(exc)])


@dataclass(frozen=True)
class DiffInput:
    directory: Path
    request: DiffRequest
    expected_state: bytes | None
    expected_plan: dict
    sealed_declaration: bytes


@dataclass(frozen=True)
class PrecreateScope:
    """Scope subset of preflight. NO deployed module/raw is required yet.

    discover/doctor/frontmost/exclusion and review of the protected baseline are
    NOT supplied by this type; assess_precreate_scope is NOT full preflight.
    """
    window: ReadWindow
    identity: NativeIdentity
    before_session: Path
    after_session: Path
    parent_read: tuple[ReadRequest, Path]
    protected_reads: list[tuple[ReadRequest, Path]]
    reviewed_parent: dict
    protected_baseline: dict
    fixture_diff: DiffInput
    protected_diff: DiffInput
    parsed_records: dict


def assess_precreate_scope(package: Path, execution: Path, scope: PrecreateScope) -> Verdict:
    """Fresh inputs + two protected reads + absence + creation/preservation diffs.

    This useful pre-create scope gate never calls a creation plan zero-diff. It
    audits sealed SDK graphs offline, not a nonexistent deployed module. Its
    status cannot substitute for full preflight, baseline review or permission.
    """
    try:
        verify_execution(package, execution)
        if len(scope.protected_reads) != 2 or scope.protected_reads[0][0] != scope.protected_reads[1][0]:
            return Verdict("inconclusive", ("missing:two-independent-protected-reads",))
        if (scope.parent_read[0].operation != "inspect" or scope.parent_read[0].target != "Root"
                or scope.protected_reads[0][0].operation != "inspect"):
            return Verdict("failed", ("precreate-full-root-and-panel-inspect-required",))
        observation = load_observation(scope.before_session, [*scope.protected_reads, scope.parent_read],
                                       scope.after_session, window=scope.window, identity=scope.identity)
        if observation.status != "passed":
            return observation
        one, two, parent = observation.data
        for observed in (one, two):
            matched = compare_subtree(scope.protected_baseline, observed)
            if matched.status != "passed":
                return matched
        parent_result = compare_subtree(scope.reviewed_parent, parent)
        if parent_result.status != "passed":
            return parent_result
        declaration_bytes = (Path(package) / "fixture.json").read_bytes()
        declaration = _decode(declaration_bytes)
        slots, _, issues = _tree(parent)
        if issues:
            return _result(issues)
        if any(node["name"] == declaration["slot"]["name"] for node, _ in slots.values()):
            return Verdict("failed", ("precreate-fixture-already-present",))
        world_states = {_decode((Path(package) / "manifests" / (r + ".json")).read_bytes())["worldState"] for r in MODULES}
        fixture_input = scope.fixture_diff
        if (fixture_input.sealed_declaration != declaration_bytes or fixture_input.expected_state is not None
                or str(fixture_input.request.state) not in world_states
                or fixture_input.expected_plan.get("ownershipKey") != declaration["ownership"]["key"]):
            return Verdict("failed", ("precreate-fixture-diff-context",))
        all_directories = [scope.before_session, *(p for _, p in scope.protected_reads), scope.parent_read[1], scope.after_session]
        before, after = load_read(scope.before_session), load_read(scope.after_session)
        for diff_input in (scope.fixture_diff, scope.protected_diff):
            compared = assess_diff(diff_input.directory, request=diff_input.request, window=scope.window,
                                   sealed_declaration=diff_input.sealed_declaration, expected_state=diff_input.expected_state,
                                   expected_plan=diff_input.expected_plan)
            if compared.status != "passed":
                return compared
            diff_receipt = load_diff(diff_input.directory)
            if not (datetime.fromisoformat(before["finishedAt"]) <= datetime.fromisoformat(diff_receipt["startedAt"])
                    <= datetime.fromisoformat(diff_receipt["finishedAt"]) <= datetime.fromisoformat(after["startedAt"])):
                return Verdict("failed", ("precreate-diff-outside-native-bracket",))
            all_directories.append(diff_input.directory)
        used = set()
        for directory in all_directories:
            for name in ("receipt.json", "stdout.bin"):
                stat = (Path(directory) / name).stat()
                key = (stat.st_dev, stat.st_ino)
                if key in used:
                    return Verdict("failed", ("precreate-reused-physical-evidence",))
                used.add(key)
        expected_keys = set()
        def keys(node):
            expected_keys.add(("slot", node["slot"]["key"]))
            expected_keys.update(("component", c["key"]) for c in node["components"])
            for child in node["children"]:
                keys(child)
        keys(declaration)
        operations = scope.fixture_diff.expected_plan["operations"]
        if (any(op["action"] != "create" for op in operations)
                or {(op["kind"], op["key"]) for op in operations} != expected_keys):
            return Verdict("failed", ("precreate-not-complete-creation-plan",))
        if scope.protected_diff.expected_plan["changes"]:
            return Verdict("failed", ("precreate-protected-diff-has-changes",))
        if set(scope.parsed_records) != set(MODULES):
            return Verdict("inconclusive", ("missing:all-sealed-module-graphs",))
        for role, module in MODULES.items():
            parsed = load_sealed_record(package, module, scope.parsed_records[role])
            if parsed.status != "passed":
                return parsed
        return Verdict("passed")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return _result(["missing:precreate-scope-evidence:" + str(exc)])
