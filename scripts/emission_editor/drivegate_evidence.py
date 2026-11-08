"""Offline evidence comparisons for the short Drive gate; never a write API.

`passed` means only that the named comparison matched its supplied, independently
reviewed expectation. It is NOT full preflight, native execution, or permission.
Use load_observation on original read_receipts directories before comparing their
data. NativeIdentity must designate an independently reviewed native-session
field, not a connectionId or a string copied from the same observation.

No subprocesses, network, retry, state adoption, or automatic phase advancement.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import math
from pathlib import Path

from .read_receipts import ReadRequest, load_read


@dataclass(frozen=True)
class Verdict:
    status: str
    issues: tuple[str, ...] = ()
    data: tuple = ()
    complete: bool = field(default=False, init=False)
    executionPermission: bool = field(default=False, init=False)
    nativeSucceeded: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReadWindow:
    url: str
    started: datetime
    finished: datetime
    now: datetime


@dataclass(frozen=True)
class NativeIdentity:
    component: str
    member: str
    session: str


def _result(issues, data=()):
    failures = tuple(i for i in issues if not i.startswith("missing:"))
    return Verdict("failed" if failures else "inconclusive" if issues else "passed", tuple(issues), tuple(data))


def _real(value):
    return type(value) in (int, float) and math.isfinite(value)


def _value(typ, value):
    if typ == "System.Boolean":
        return type(value) is bool
    if typ == "System.Int32":
        return type(value) is int and -(2**31) <= value < 2**31
    if typ == "System.Int64":
        return type(value) is int and -(2**63) <= value < 2**63
    if typ in ("System.Single", "System.Double"):
        return _real(value)
    if typ == "System.String":
        return value is None or isinstance(value, str)
    if typ in ("ResoniteLink.float3", "ResoniteLink.floatQ"):
        axes = {"x", "y", "z", "w"} if typ.endswith("floatQ") else {"x", "y", "z"}
        return isinstance(value, dict) and value.keys() == axes and all(_real(v) for v in value.values())
    if typ == "ResoniteLink.colorX":
        return (isinstance(value, dict) and set(value) == {"r", "g", "b", "a", "profile"}
                and all(_real(value[k]) for k in ("r", "g", "b", "a"))
                and value["profile"] in ("Linear", "sRGB"))
    return False


def _strict_equal(left, right):
    # Non-field metadata (including IDs and booleans) must never coerce.
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_strict_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(v) for v in value)
    if isinstance(value, dict):
        return all(type(k) is str and _json_value(v) for k, v in value.items())
    return False


def _wire_real(value):
    # Installed ModelMapper/OutputWriter encode named nonfinite floats as strings.
    return _real(value) or (type(value) is str and value in ("Infinity", "-Infinity", "NaN"))


def _preservable_field(typ, value):
    if typ in ("System.Single", "System.Double"):
        return type(value) is str and _wire_real(value)
    # ResoniteLink.colorX.Profile is a string carrier, not a writer enum. Retain
    # complete unfamiliar profiles without interpreting/authorizing their use.
    return (typ == "ResoniteLink.colorX" and not _value(typ, value) and isinstance(value, dict)
            and value.keys() == {"r", "g", "b", "a", "profile"}
            and all(_wire_real(value[k]) for k in ("r", "g", "b", "a"))
            and isinstance(value["profile"], str) and bool(value["profile"]))


def _complete_array(member):
    # These exact known ResoniteLink classes take ModelMapper's serialized
    # fallback, carrying Values plus inherited ID, rather than SyncList members.
    value = member["value"]
    axes = {"x", "y", "z", "w"} if member["kind"] == "Array_floatQ" else {"x", "y", "z"}
    return (isinstance(value, dict) and value.keys() == {"id", "values"}
            and value["id"] == member["id"] and isinstance(value["values"], list)
            and all(isinstance(v, dict) and v.keys() == axes
                    and all(_wire_real(n) for n in v.values()) for v in value["values"]))


def _compare_opaque(expected, actual):
    # Preservation ONLY: exact complete JSON, not domain/writer authorization.
    if not _json_value(expected) or not _json_value(actual):
        return Verdict("inconclusive", ("missing:complete-json-payload",))
    return Verdict("passed") if _strict_equal(expected, actual) else Verdict("failed", ("preserved-payload",))


def compare_typed(expected: dict, actual: dict) -> Verdict:
    """Semantic comparison of known complete MemberValue types, including ID.

    Only schema-real values allow int/float equivalence. A PRESENT identified
    field/reference may encode null by omitting value/targetId (CLI 0.2.0);
    absent members/identity/type are unknown. Nonnullable null is never false/0.
    Uninterpreted fields and SyncPlayback are NOT allowed private target types.
    """
    return _compare_member(expected, actual, preservation=False)


def _compare_member(expected: dict, actual: dict, *, preservation: bool) -> Verdict:
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return Verdict("inconclusive", ("missing:typed-member",))
    # MapMember always emits a field object; OutputWriter omits its null Value.
    # Normalize only present identified/typed fields, never a missing member.
    def normalize(member):
        if (member.get("kind") == "field" and isinstance(member.get("id"), str) and member["id"]
                and isinstance(member.get("type"), str) and member["type"]):
            return {"value": None, **member}
        return member
    expected, actual = normalize(expected), normalize(actual)
    required = {"kind", "id"}
    kind = expected.get("kind")
    if kind == "field":
        required |= {"type", "value"}
    elif kind == "empty" and preservation:
        pass  # MapMember(Link.EmptyElement) emits only kind/id; preserve identity.
    elif kind == "SyncPlayback" and preservation:
        required |= {"type", "value"}
    elif kind in ("Array_float3", "Array_floatQ") and preservation:
        required |= {"value"}
    elif kind == "reference":
        # ResoLoop 0.2.0 maps every present reference; WhenWritingNull omits
        # targetId for a known null. Never normalize an absent/unidentified member.
        if all(m.get("kind") == "reference" and isinstance(m.get("id"), str) and m["id"]
               for m in (expected, actual)):
            expected = {"targetId": None, **expected}
            actual = {"targetId": None, **actual}
        required |= {"targetId"}
    elif kind in ("syncObject", "dictionary"):
        required |= {"members"}
    elif kind == "list":
        required |= {"elements"}
    else:
        return Verdict("inconclusive", ("missing:supported-member-kind",))
    if not required <= expected.keys() or not required <= actual.keys():
        return Verdict("inconclusive", ("missing:member-data",))
    if not isinstance(expected["id"], str) or not expected["id"]:
        return Verdict("inconclusive", ("missing:expected-member-id",))
    if expected.keys() != actual.keys():
        return Verdict("failed", ("member-field-set",))
    skip = {"value"} if kind in ("field", "SyncPlayback", "Array_float3", "Array_floatQ") else {"members"} if kind in ("syncObject", "dictionary") else {"elements"} if kind == "list" else set()
    if any(not _strict_equal(v, actual[k]) for k, v in expected.items() if k not in skip):
        return Verdict("failed", ("member-identity-or-type",))
    if kind == "field":
        typ = expected["type"]
        if not isinstance(typ, str) or not typ:
            return Verdict("inconclusive", ("missing:field-type",))
        known = typ in ("System.Boolean", "System.Int32", "System.Int64", "System.Single", "System.Double",
                        "System.String", "ResoniteLink.colorX", "ResoniteLink.float3", "ResoniteLink.floatQ")
        if preservation and (not known or _preservable_field(typ, expected["value"])):
            return _compare_opaque(expected["value"], actual["value"])
        if not _value(typ, expected["value"]):
            return Verdict("inconclusive", ("missing:valid-supported-expectation",))
        if not _value(typ, actual["value"]) or expected["value"] != actual["value"]:
            return Verdict("failed", ("typed-value",))
    elif kind == "SyncPlayback":
        if (expected["type"] != "[FrooxEngine]FrooxEngine.SyncPlayback"
                or not isinstance(expected["value"], dict) or not isinstance(actual["value"], dict)):
            return Verdict("inconclusive", ("missing:complete-playback-payload",))
        return _compare_opaque(expected["value"], actual["value"])
    elif kind in ("Array_float3", "Array_floatQ"):
        if not _complete_array(expected):
            return Verdict("inconclusive", ("missing:complete-known-array",))
        return _compare_opaque(expected["value"], actual["value"])
    elif kind in ("syncObject", "dictionary", "list"):
        key = "elements" if kind == "list" else "members"
        left, right = expected[key], actual[key]
        if kind == "list":
            if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
                return Verdict("failed", ("member-list-coverage",))
            pairs = zip(left, right)
        else:
            if not isinstance(left, dict) or not isinstance(right, dict) or left.keys() != right.keys():
                return Verdict("failed", ("member-map-coverage",))
            pairs = ((v, right[k]) for k, v in left.items())
        results = [_compare_member(a, b, preservation=preservation) for a, b in pairs]
        return _result([issue for result in results for issue in result.issues])
    return Verdict("passed")


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def load_observation(before_session: Path, reads: list[tuple[ReadRequest, Path]], after_session: Path,
                     *, window: ReadWindow, identity: NativeIdentity) -> Verdict:
    """Load ordered, non-overlapping independent reads bracketed by native ID.

    Both metadata and stdout inode identities AND receipt fingerprints are
    single-use within the batch, catching aliases/hardlinks and copied receipts.
    Caller must retain one batch for each phase and compare between batches;
    this API is not a persistent replay registry or an authenticity mechanism.
    """
    issues, output = [], []
    try:
        if (not all(_aware(t) for t in (window.started, window.finished, window.now))
                or not window.started <= window.finished <= window.now):
            return Verdict("failed", ("read-window",))
        if not identity.session or not identity.member:
            return Verdict("inconclusive", ("missing:native-identity",))
        session_request = ReadRequest("component-inspect", window.url, identity.component)
        sequence = [(session_request, before_session), *reads, (session_request, after_session)]
        if not reads:
            return Verdict("inconclusive", ("missing:observation-reads",))
        used_files, fingerprints = set(), set()
        previous = window.started
        for index, (request, directory) in enumerate(sequence):
            directory = Path(directory)
            for name in ("receipt.json", "stdout.bin"):
                stat = (directory / name).stat()
                physical = (stat.st_dev, stat.st_ino)
                if physical in used_files:
                    issues.append("reused-physical-receipt")
                used_files.add(physical)
            fingerprint = hashlib.sha256((directory / "receipt.json").read_bytes()).digest()
            if fingerprint in fingerprints:
                issues.append("replayed-receipt")
            fingerprints.add(fingerprint)
            loaded = load_read(directory)
            if request.url != window.url or loaded["request"] != asdict(request):
                issues.append("read-request")
            if loaded["transportSucceeded"] is not True:
                issues.append("read-transport")
            started, finished = (datetime.fromisoformat(loaded[k]) for k in ("startedAt", "finishedAt"))
            if (not _aware(started) or not _aware(finished)
                    or not previous <= started <= finished <= window.finished):
                issues.append("read-time-or-order")
            previous = finished
            data = loaded.get("response", {}).get("data", {}) if isinstance(loaded.get("response"), dict) else {}
            if index in (0, len(sequence) - 1):
                member = data.get("members", {}).get(identity.member, {})
                if data.get("id") != identity.component or not {"value", "id", "type", "kind"} <= member.keys():
                    issues.append("missing:native-identity")
                elif (member["kind"] != "field" or member["type"] != "System.String"
                      or type(member["value"]) is not str or member["value"] != identity.session):
                    issues.append("native-session")
            else:
                if request.operation in ("component-inspect", "inspect", "hierarchy"):
                    if "id" not in data:
                        issues.append("missing:response-target-id")
                    elif data["id"] != request.target:
                        issues.append("response-target-id")
                output.append(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        issues.append("missing:valid-receipt:" + type(exc).__name__)
    return _result(issues, output if not issues else ())


def _tree(tree):
    slots, components, issues = {}, {}, []

    def visit(node, parent):
        if not isinstance(node, dict) or not {"id", "name", "isReferenceOnly", "components", "children"} <= node.keys():
            issues.append("missing:subtree-data")
            return
        ident = node["id"]
        if not isinstance(ident, str) or not ident or ident in slots:
            issues.append("duplicate-or-invalid-slot")
            return
        slots[ident] = (node, parent)
        if node["isReferenceOnly"] is not False:
            issues.append("missing:full-subtree")
        if not isinstance(node["components"], list) or not isinstance(node["children"], list):
            issues.append("missing:subtree-lists")
            return
        for comp in node["components"]:
            if not isinstance(comp, dict) or not {"id", "type", "members"} <= comp.keys():
                issues.append("missing:all-component-members")
                continue
            cid = comp["id"]
            if not isinstance(cid, str) or not cid or cid in components or not isinstance(comp["members"], dict):
                issues.append("duplicate-or-invalid-component")
                continue
            components[cid] = (comp, ident)
        for child in node["children"]:
            visit(child, ident)

    visit(tree, None)
    if slots.keys() & components.keys():
        issues.append("duplicate-world-identity")
    return slots, components, issues


def compare_subtree(expected: dict, actual: dict) -> Verdict:
    """Exact all-slot/all-component/all-member comparison, order independent.

    Both trees use SlotInfo/ComponentInfo shapes, with `members` on EVERY
    component. A hierarchy --summary alone is deliberately insufficient. The
    expected tree must be independently reviewed; never copy current into it.
    Complete typed fields of uninterpreted types, known EmptyElement identities
    and the SyncPlayback mapper payload are preserved as strict JSON, NOT semantically authorized as
    private writer targets. Unknown kinds/incomplete recursive members cannot pass.
    """
    left, lc, li = _tree(expected)
    right, rc, ri = _tree(actual)
    if li or ri:
        return _result(li + ri)
    issues = []
    if left.keys() != right.keys() or lc.keys() != rc.keys():
        return Verdict("failed", ("subtree-coverage",))
    for ident, (node, parent) in left.items():
        other, other_parent = right[ident]
        meta = {k: v for k, v in node.items() if k not in ("children", "components", "members")}
        ometa = {k: v for k, v in other.items() if k not in ("children", "components", "members")}
        if parent != other_parent or not _strict_equal(meta, ometa):
            issues.append("slot-identity-or-metadata:" + ident)
        if "members" in node or "members" in other:
            lm, rm = node.get("members"), other.get("members")
            if not isinstance(lm, dict) or not isinstance(rm, dict) or lm.keys() != rm.keys():
                issues.append("missing:slot-member-coverage:" + ident)
            else:
                for name, member in lm.items():
                    issues.extend(_compare_member(member, rm[name], preservation=True).issues)
    for ident, (comp, parent) in lc.items():
        other, other_parent = rc[ident]
        if parent != other_parent or not _strict_equal(
                {k: v for k, v in comp.items() if k != "members"},
                {k: v for k, v in other.items() if k != "members"}):
            issues.append("component-identity:" + ident)
        if comp["members"].keys() != other["members"].keys():
            issues.append("component-member-coverage:" + ident)
        else:
            for name, member in comp["members"].items():
                result = _compare_member(member, other["members"][name], preservation=True)
                issues.extend(result.issues)
    return _result(issues)


def compare_root_context(expected: dict, actual: dict, *, protected: tuple[dict, ...] = ()) -> Verdict:
    """Check Root identity/shape and explicitly protected subtrees/ancestry.

    Whole-Root values are diagnostics ONLY. A passed context is not full
    preservation/preflight or permission. Unknown unprotected member payloads
    remain visible in wholeRootComparison; they are not validated defaults.
    Call only on receipt-bound reads, with independently reviewed protection.
    """
    left, lc, li = _tree(expected)
    right, rc, ri = _tree(actual)
    if li or ri:
        return _result(li + ri)
    if (expected["id"] != "Root" or actual["id"] != "Root"
            or expected["name"] != actual["name"]):
        return Verdict("failed", ("root-context-identity",))

    def ancestry(slots, ident):
        chain = []
        while ident is not None:
            node, parent = slots[ident]
            chain.append((ident, node["name"]))
            ident = parent
        return chain

    for baseline in protected:
        ident = baseline["id"]
        # Root itself is never an implicit protection selection: that would
        # restore whole-Root equality through this otherwise scoped API.
        if ident == "Root" or ident not in left or ident not in right:
            return Verdict("failed", ("root-protected-scope:" + ident,))
        for node in (left[ident][0], right[ident][0]):
            matched = compare_subtree(baseline, node)
            if matched.status != "passed":
                return matched
        if ancestry(left, ident) != ancestry(right, ident):
            return Verdict("failed", ("root-protected-ancestry:" + ident,))
    comparison = compare_subtree(expected, actual)
    diagnostic = {
        "wholeRootComparison": {"status": comparison.status, "issues": list(comparison.issues)},
        "slotsAdded": sorted(right.keys() - left.keys()),
        "slotsRemoved": sorted(left.keys() - right.keys()),
        "componentsAdded": sorted(rc.keys() - lc.keys()),
        "componentsRemoved": sorted(lc.keys() - rc.keys()),
    }
    return Verdict("passed", data=(diagnostic,))


def compare_root_deletion(expected_before: dict, before: dict, after: dict, target: str,
                          *, protected: tuple[dict, ...] = ()) -> Verdict:
    """Prove owned target removal and no other preexisting object loss.

    Root time-dependent values/additions are diagnostic, not equality gates.
    A concurrent outside disappearance cannot be attributed to this deletion,
    but cannot establish its required no-collateral-loss boundary either.
    Exact non-Root owned-parent deletion remains compare_deletion's job.
    """
    reviewed, _, issues = _tree(expected_before)
    if issues:
        return _result(issues)
    if target == "Root" or target not in reviewed:
        return Verdict("failed", ("deletion-target-outside-reviewed-descendants",))
    target_tree = reviewed[target][0]
    checked = compare_root_context(expected_before, before, protected=(*protected, target_tree))
    if checked.status != "passed":
        return checked
    checked_after = compare_root_context(expected_before, after, protected=protected)
    if checked_after.status != "passed":
        return checked_after
    bs, bc, _ = _tree(before)
    ats, ac, _ = _tree(after)
    removed_slots, removed_components, _ = _tree(target_tree)
    if removed_slots.keys() & ats.keys() or removed_components.keys() & ac.keys():
        return Verdict("failed", ("root-deletion-target-still-present",))
    if (bs.keys() - removed_slots.keys() - ats.keys()
            or bc.keys() - removed_components.keys() - ac.keys()):
        return Verdict("failed", ("root-deletion-outside-object-loss",))
    return Verdict("passed", data=(checked.data[0], checked_after.data[0]))


def compare_deletion(expected_before: dict, before: dict, after: dict, target: str) -> Verdict:
    """Verify ONLY the selected descendant disappeared; never execute deletion.

    To check final fixture-root absence supply its freshly read stable parent as
    the tree root. Deleting the observation root itself cannot prove absence.
    """
    from copy import deepcopy
    matched = compare_subtree(expected_before, before)
    if matched.status != "passed":
        return matched
    slots, _, _ = _tree(expected_before)
    if target not in slots or slots[target][1] is None:
        return Verdict("failed", ("deletion-target-outside-reviewed-descendants",))
    expected_after = deepcopy(expected_before)

    def remove(node):
        node["children"] = [child for child in node["children"] if child["id"] != target]
        for child in node["children"]:
            remove(child)

    remove(expected_after)
    return compare_subtree(expected_after, after)


_PHASE_TYPES = {
    "Command": "System.Int32", "Captured": "System.Boolean", "BeforeColor": "ResoniteLink.colorX",
    "BeforeBool": "System.Boolean", "ColorTarget": "ResoniteLink.colorX", "BoolTarget": "System.Boolean",
    "ColorOutcome": "System.Int32", "BoolOutcome": "System.Int32", "ColorAfter": "ResoniteLink.colorX",
    "BoolAfter": "System.Boolean", "Done": "System.Int32",
}


def compare_phase_sequence(baseline: dict, phases: list[tuple[int, dict]]) -> Verdict:
    """Compare values for phases 1..6, stopping at the FIRST discrepancy.

    This is a value-only comparison, usable for offline independently authored
    fixtures as well as native raw. Helper presence/deletions, graph, receipts,
    protected panel and fresh runtime state must be checked separately. The
    unused baseline must make both capture and bool restoration observable;
    matching preinitialized Before/Target values cannot prove those operations.
    Re-capture refusal still requires a separate native attempt/observation.
    This never labels a simulated input as native success or writes a command.
    """
    from copy import deepcopy
    if not isinstance(baseline, dict) or set(baseline) != set(_PHASE_TYPES):
        return Verdict("inconclusive", ("missing:baseline-fields",))
    ids = []
    for name, typ in _PHASE_TYPES.items():
        member = baseline[name]
        if (not isinstance(member, dict) or member.get("type") != typ
                or compare_typed(member, member).status != "passed"):
            return Verdict("inconclusive", ("missing:typed-baseline:" + name,))
        ids.append(member["id"])
    if len(set(ids)) != len(ids):
        return Verdict("failed", ("shared-private-field-id",))
    if (baseline["Command"]["value"] != 0 or baseline["Done"]["value"] != 0
            or baseline["Captured"]["value"] is not False
            or any(baseline[name]["value"] != 0 for name in ("ColorOutcome", "BoolOutcome"))):
        return Verdict("failed", ("baseline-already-used",))
    a = dict(zip(("r", "g", "b", "a", "profile"), (1.5, .25, 3., .625, "Linear")))
    b = dict(zip(("r", "g", "b", "a", "profile"), (.875, 5.5, .0625, .8125, "sRGB")))
    c = dict(zip(("r", "g", "b", "a", "profile"), (2.5, .75, .125, .375, "Linear")))
    original_color, original_bool = baseline["ColorTarget"]["value"], baseline["BoolTarget"]["value"]
    if (_strict_equal(baseline["BeforeColor"]["value"], original_color)
            or baseline["BeforeBool"]["value"] is original_bool or original_bool is False):
        return Verdict("failed", ("baseline-capture-or-restoration-unobservable",))
    wanted = [(original_color, original_bool, 0, 0), (a, True, 1, 1), (a, True, 2, 2),
              (b, True, 1, 2), (c, False, 1, 1), (original_color, original_bool, 1, 1)]
    if len(phases) != 6:
        return Verdict("inconclusive", ("missing:phase-sequence",))
    for index, (number, observed) in enumerate(phases, 1):
        if type(number) is not int or number != index:
            return Verdict("inconclusive", ("missing:ordered-phase:" + str(index),))
        if not isinstance(observed, dict) or set(observed) != set(_PHASE_TYPES):
            return Verdict("inconclusive", ("missing:phase-fields:" + str(index),))
        expected = deepcopy(baseline)
        col, boolean, co, bo = wanted[index - 1]
        changes = {"Command": index, "Done": index, "Captured": True, "BeforeColor": original_color,
                   "BeforeBool": original_bool, "ColorTarget": col, "ColorAfter": col,
                   "BoolTarget": boolean, "BoolAfter": boolean, "ColorOutcome": co, "BoolOutcome": bo}
        for name, value in changes.items():
            expected[name]["value"] = deepcopy(value)
        for name in expected:
            result = compare_typed(expected[name], observed[name])
            if result.status != "passed":
                return Verdict(result.status, ("phase:" + str(index) + ":" + name, *result.issues))
    return Verdict("passed")


_RUNTIME_PREFIXES = ("[ProtoFluxBindings]", "[FrooxEngine]FrooxEngine.ProtoFlux.GlobalReference<",
                     "[FrooxEngine]FrooxEngine.ProtoFlux.CoreNodes.FieldDriveBase<")


def _sdk_graph(record, module):
    import re
    from uuid import UUID
    types = record["Types"]
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        raise ValueError("sdk-types")
    flux = [n for n in record["Object"]["Children"] if n["Name"]["Data"] == "Flux"]
    if len(flux) != 1:
        raise ValueError("sdk-flux-container")
    candidates = [n for n in flux[0]["Children"]
                  if re.sub(r"<[^>]*>", "", n["Name"]["Data"]).rsplit(" (", 1)[0] == module]
    if len(candidates) != 1:
        raise ValueError("sdk-module-ambiguous")
    components, exports = {}, {}

    def visit(slot):
        name = slot["Name"]["Data"]
        for component in slot["Components"]["Data"]:
            index = component["Type"]
            if type(index) is not int or not 0 <= index < len(types):
                raise ValueError("sdk-type-index")
            typ = types[index]
            if not typ.startswith(_RUNTIME_PREFIXES):
                continue  # presentation/meta/assets are NOT deployed runtime nodes
            raw = component["Data"]
            ident = raw["ID"]
            if not isinstance(ident, str) or not ident or ident in components:
                raise ValueError("sdk-component-identity")
            members = {k: v["Data"] for k, v in raw.items() if isinstance(v, dict) and "Data" in v}
            persistence_metadata = [key for key in ("persistant-ID", "persistent-ID") if key in raw]
            if len(persistence_metadata) > 1:
                raise ValueError("sdk-ambiguous-persistence-metadata")
            if persistence_metadata:
                # Exact SDK common BSON bookkeeping (including the previously
                # characterized spelling), NOT an engine alias/member identity.
                # Native PackResoniteLink uses NodeComponent.Fields/AddComponent,
                # not this GUID; ComponentBase.Initialize independently sets the
                # native Boolean persistent=true. No other defaults are inferred.
                # Controller must recheck DLL/SDK fingerprints before execution.
                metadata_id = raw[persistence_metadata[0]]
                if not isinstance(metadata_id, str) or not UUID(metadata_id).int:
                    raise ValueError("sdk-persistent-field-identity")
                members.setdefault("persistent", True)  # Explicit serialized value wins.
            if "persistent" in members and type(members["persistent"]) is not bool:
                raise ValueError("sdk-persistent-boolean")
            components[ident] = {"type": typ, "members": members, "slot": name,
                                 "tag": slot.get("Tag", {}).get("Data")}
            if "GlobalReference<" in typ or ("FieldDriveBase<" in typ and typ.endswith("+Proxy")):
                prefix, port = name.split(":", 1)
                member = "Reference" if "GlobalReference<" in typ else "Drive"
                if (prefix != ("Input" if member == "Reference" else "Output") or port in exports
                        or member not in members or members[member] is not None):
                    raise ValueError("sdk-export-binding")
                exports[port] = (ident, member)
        for child in slot["Children"]:
            visit(child)

    visit(candidates[0])
    if not components:
        raise ValueError("sdk-runtime-empty")
    return components, exports


def _sdk_audit(components, exports, module):
    def nodes(fragment):
        return [(i, c) for i, c in components.items() if fragment in c["type"]]

    def color_value(ident):
        comp = components[ident]
        if not comp["type"].endswith(".Pack_ColorX"):
            raise ValueError("sdk-color-pack")
        value = {}
        for axis in ("R", "G", "B", "A"):
            literal = components[comp["members"][axis]]
            number = literal["members"]["Value"]
            if not literal["type"].endswith(".ValueInput<float>") or not _real(number):
                raise ValueError("sdk-color-number")
            value[axis.lower()] = number
        profile_node = components[comp["members"]["Profile"]]
        if ".IntToEnum<" not in profile_node["type"]:
            raise ValueError("sdk-color-profile-node")
        profile = components[profile_node["members"]["Value"]]["members"]["Value"]
        if type(profile) is not int or profile not in (0, 1):
            raise ValueError("sdk-color-profile")
        value["profile"] = ("Linear", "sRGB")[profile]
        return value

    entries, drives = nodes(".FireOnValueChange<"), nodes(".ValueFieldDrive<")
    if module == "DriveGateProbe":
        if len(entries) != 1 or drives or set(exports) != set(_PHASE_TYPES):
            raise ValueError("sdk-probe-entries-or-ports")
        sources = {}
        for port, (global_id, _) in exports.items():
            found = [(i, c) for i, c in nodes(".ValueSource<") if c["slot"] == "Input:" + port]
            if len(found) != 1 or found[0][1]["members"].get("Source") != global_id:
                raise ValueError("sdk-input-global")
            sources[port] = found[0][0]
        hosts = nodes(".HostUser")
        entry = entries[0][1]["members"]
        if (len(hosts) != 1 or entry.get("OnlyForUser") != hosts[0][0]
                or entry.get("Value") != sources["Command"] or entry.get("OnChanged") not in components):
            raise ValueError("sdk-host-command-entry")
        for target, outcome in (("ColorTarget", "ColorOutcome"), ("BoolTarget", "BoolOutcome")):
            writers = [(i, c) for i, c in nodes(".ValueWrite<") if c["members"].get("Variable") == sources[target]]
            if len(writers) != 1:
                raise ValueError("sdk-target-writer")
            write = writers[0][1]["members"]
            if not write.get("OnWritten") or not write.get("OnFail") or write["OnWritten"] == write["OnFail"]:
                raise ValueError("sdk-write-continuations")
            for edge, expected in (("OnWritten", 1), ("OnFail", 2)):
                recorder = components[write[edge]]["members"]
                literal = components[recorder["Value"]]["members"]["Value"]
                if recorder.get("Variable") != sources[outcome] or type(literal) is not int or literal != expected:
                    raise ValueError("sdk-outcome-continuation")
        expected_colors = {"_a": (1.5, .25, 3., .625, "Linear"), "_b": (.875, 5.5, .0625, .8125, "sRGB"),
                           "_c": (2.5, .75, .125, .375, "Linear")}
        for name, expected in expected_colors.items():
            found = [i for i, c in components.items() if c["slot"] == name and c["type"].endswith(".Pack_ColorX")]
            if len(found) != 1 or color_value(found[0]) != dict(zip(("r", "g", "b", "a", "profile"), expected)):
                raise ValueError("sdk-probe-color-candidate")
    elif module in ("ColorDrive", "BoolDrive"):
        if entries or nodes(".ValueWrite<") or len(drives) != 1 or set(exports) != {"Driven"}:
            raise ValueError("sdk-helper-scope")
        proxy_id, _ = exports["Driven"]
        proxy = components[proxy_id]["members"]
        if proxy.get("Node") != drives[0][0] or proxy.get("Path") != []:
            raise ValueError("sdk-drive-proxy")
        candidate = drives[0][1]["members"]["Value"]
        if module == "ColorDrive":
            if color_value(candidate) != {"r": 1.5, "g": .25, "b": 3., "a": .625, "profile": "Linear"}:
                raise ValueError("sdk-helper-color")
        elif (not components[candidate]["type"].endswith(".ValueInput<bool>")
              or components[candidate]["members"].get("Value") is not True):
            raise ValueError("sdk-helper-bool")
    else:
        raise ValueError("sdk-module-not-drivegate")


def compare_sdk_runtime(record: dict, actual_components: list[dict], *, module: str,
                        id_map: dict[str, str], private_bindings: dict[str, str]) -> Verdict:
    """Resolve real SDK Type indices and compare every runtime member/edge.

    `record` is parse-record JSON, not a PG string or hash. `id_map` maps ALL SDK
    runtime component IDs to their current IDs. `private_bindings` maps ALL port
    names to independently scope-verified private fixture member IDs. No other
    external binding is permitted. Presentation/template components are excluded
    by type, not by confusing the envelope's name with the deployed module.

    This compares the runtime graph, not deployment slot layout or ownership.
    Unknown/missing current members cannot pass; identified reference nulls use the CLI schema.
    Exact SDK persistence bookkeeping implies the independently characterized
    native Boolean true, NOT a mapped GUID; explicit serialized values win.
    Other extra native members are NOT ignored/defaulted.
    Parse JSON must separately be tied to a verified package's brson (this function cannot authenticate it).
    """
    issues = []
    try:
        graph, exports = _sdk_graph(record, module)
        _sdk_audit(graph, exports, module)
        if set(id_map) != set(graph) or len(set(id_map.values())) != len(id_map):
            return Verdict("failed", ("sdk-id-map-coverage",))
        if (set(private_bindings) != set(exports) or not all(isinstance(v, str) and v for v in private_bindings.values())
                or len(set(private_bindings.values())) != len(private_bindings)):
            return Verdict("failed", ("sdk-private-binding-coverage",))
        if not isinstance(actual_components, list) or len(actual_components) != len(graph):
            return Verdict("failed", ("sdk-runtime-coverage",))
        actual = {c["id"]: c for c in actual_components}
        if len(actual) != len(actual_components) or set(actual) != set(id_map.values()):
            return Verdict("failed", ("sdk-runtime-identity",))
        external = {key: private_bindings[name] for name, key in exports.items()}
        used_member_ids = set()

        def compare_data(raw, member, typ, external_target=None, member_name=""):
            if not isinstance(member, dict) or not isinstance(member.get("id"), str) or not member["id"]:
                issues.append("missing:sdk-member-identity")
                return
            if member["id"] in used_member_ids:
                issues.append("sdk-duplicate-member-id")
            used_member_ids.add(member["id"])
            if external_target is not None or raw is None or (isinstance(raw, str) and raw in graph):
                target = external_target if external_target is not None else id_map[raw] if raw is not None else None
                if member.get("kind") != "reference":
                    issues.append("missing:sdk-reference")
                elif member.get("targetId") != target:
                    issues.append("sdk-reference-target")
            elif isinstance(raw, list):
                if member.get("kind") != "list" or not isinstance(member.get("elements"), list):
                    issues.append("missing:sdk-list")
                elif len(raw) != len(member["elements"]):
                    issues.append("sdk-list-coverage")
                else:
                    for value, element in zip(raw, member["elements"]):
                        if not isinstance(value, dict) or "Data" not in value:
                            issues.append("missing:sdk-list-element-schema")
                        else:
                            compare_data(value["Data"], element, typ)
            elif type(raw) in (bool, int, float, str):
                expected_type = ("System.Boolean" if type(raw) is bool else "System.String" if type(raw) is str
                                 else "System.Single" if member_name == "Value" and ".ValueInput<float>" in typ else "System.Int32" if type(raw) is int else "System.Double")
                expected = {"id": member["id"], "kind": "field", "type": expected_type, "value": raw}
                issues.extend(compare_typed(expected, member).issues)
            else:
                issues.append("missing:sdk-member-schema")

        for ident, comp in graph.items():
            observed = actual[id_map[ident]]
            if observed.get("type") != comp["type"]:
                issues.append("sdk-component-type")
            members = observed.get("members")
            if not isinstance(members, dict) or set(members) != set(comp["members"]):
                issues.append("missing:sdk-all-member-coverage-or-reviewed-defaults")
                continue
            for name, raw in comp["members"].items():
                compare_data(raw, members[name], comp["type"], external.get((ident, name)), name)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError) as exc:
        issues.append("missing:valid-sdk-graph:" + str(exc))
    return _result(issues)



def verify_runtime_execution(package: Path, execution: Path) -> Verdict:
    """Recheck immutable inputs while allowing ONLY six manifest runtime files.

    Unlike Task 1 verify_execution (fresh-only), this may run after state was
    created. State contents are NOT adopted, replayed or vouched for. They still
    require this run's session/scope checks. All immutable file hashes and the
    entire remaining file-set are checked using Task 1's existing implementation.
    """
    import json
    from .drivegate_package import MODULES, _files, _no_links, verify_package
    try:
        seal = verify_package(package)
        execution = Path(execution).absolute()
        _no_links(execution)
        execution = execution.resolve()
        if str(execution) != seal["executionRoot"]:
            return Verdict("failed", ("runtime-wrong-execution-root",))
        immutable = {k: v for k, v in seal["files"].items()
                     if k == "fixture.json" or k.startswith(("sources/", "manifests/"))}
        allowed_states = set()
        for role in MODULES:
            manifest = json.loads((Path(package) / "manifests" / (role + ".json")).read_bytes())
            for key in ("worldState", "deployState"):
                allowed_states.add(Path(manifest[key]).relative_to(execution).as_posix())
        actual = _files(execution)
        if {k: v for k, v in actual.items() if k not in allowed_states} != immutable:
            return Verdict("failed", ("runtime-immutable-or-unknown-file",))
        return Verdict("passed")
    except (OSError, ValueError, KeyError, TypeError):
        return Verdict("failed", ("runtime-package-or-artifact-invalid",))
