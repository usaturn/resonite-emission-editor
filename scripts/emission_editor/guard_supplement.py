"""Registration supplement for the current generation: bind the row guard fields.

Writes only the owned guard targets of one generation (index-K, list-K, provider,
row-count) after the existing ResoloopPanelPort.guard(plan) has proved that the
registration capture is still current. Raw IDs come only from that verified
current observation; nothing is embedded in a declaration or source.
Fail closed: every check runs before the first write; any mismatch writes nothing.
A rerun continues only when the written values equal the plan as an ordered
prefix and every other target still holds its initial value.
"""

import re

from .resoloop_io import INT, LIST_REF, PROVIDER_REF, guard_writes

GUARD_TYPES = {"index": INT, "list": LIST_REF, "provider": PROVIDER_REF, "row-count": INT}
INITIAL = {"index": -1, "list": None, "provider": None, "row-count": 0}
MEMBERS = {"index": "Value", "list": "Reference", "provider": "Reference", "row-count": "Value"}


def guard_kind(plan, key, member):
    """Only this generation's guard targets may be written by the supplement."""
    prefix = re.escape("reg-" + plan.generation)
    for kind, pattern in (
        ("index", prefix + r"-row-\d+-index-\d+"),
        ("list", prefix + r"-row-\d+-list-\d+"),
        ("provider", prefix + r"-row-\d+-provider"),
        ("row-count", prefix + r"-row-count"),
    ):
        if re.fullmatch(pattern, key):
            if member != MEMBERS[kind]:
                break
            return kind
    raise ValueError("supplement-target-outside-guard-fields:" + key)


def registered_references(plan):
    """(key, member) -> raw target the registration stored for this plan."""
    prefix = "reg-" + plan.generation
    refs = {
        (f"{prefix}-avatar-{i}", "Reference"): avatar.ref
        for i, avatar in enumerate(plan.avatars)
    }
    for index, row in enumerate(plan.rows):
        key = f"{prefix}-row-{index}"
        refs[(key + "-material", "Reference")] = row.material_ref
        refs[(key + "-source", "Reference")] = row.field_ref
        for number, use in enumerate(row.uses):
            refs[(f"{key}-mesh-{number}", "Reference")] = use.mesh_ref
            refs[(f"{key}-renderer-{number}", "Reference")] = use.renderer_ref
            refs[(f"{key}-binding-{number}", "Reference")] = use.binding_ref
    return refs


def _read(observed, selector):
    record = observed[selector]
    if selector[1] == "Reference":
        return record.get("targetId")
    if "value" not in record:
        raise ValueError("supplement-readback-missing:" + selector[0])
    return record["value"]


def _same(actual, expected):
    return type(actual) is type(expected) and actual == expected


def written_prefix(plan, writes, current):
    """Length of the already-written ordered prefix; the rest must be initial."""
    done = 0
    while done < len(writes) and _same(current[writes[done][:2]], writes[done][2]):
        done += 1
    for key, member, _ in writes[done:]:
        if not _same(current[(key, member)], INITIAL[guard_kind(plan, key, member)]):
            raise ValueError("supplement-state-not-resumable:" + key)
    return done


def _observe(panel, plan, writes):
    refs = registered_references(plan)
    selectors = [*refs, *((key, member) for key, member, _ in writes), ("registry-active", "Value")]
    observed = panel._observe_owned(selectors)
    current = {selector: _read(observed, selector) for selector in selectors}
    if any(not _same(current[selector], expected) for selector, expected in refs.items()):
        raise ValueError("panel-references-differ-from-capture")
    if current[("registry-active", "Value")] != plan.generation:
        raise ValueError("supplement-generation-not-active")
    return current


def verify(panel, plan):
    """Read-only gate. Returns (ordered writes, length of the already-written prefix)."""
    if not plan.complete:
        raise ValueError("supplement-plan-incomplete")
    if panel.session != plan.session:
        raise ValueError("native-session-changed")
    status = panel.guard(plan)  # session, plan==capture, fresh capture identical
    if not _idle(status):
        raise ValueError("panel-not-idle-for-supplement")
    writes = guard_writes(plan, panel.observed_capture)
    components = panel._state()["components"]
    for key, member, _ in writes:
        kind = guard_kind(plan, key, member)
        if components.get(key, {}).get("type") != GUARD_TYPES[kind]:
            raise ValueError("supplement-field-missing-or-retyped:" + key)
    current = _observe(panel, plan, writes)
    return writes, written_prefix(plan, writes, current)


def _idle(status):
    # An acquired operator is allowed: no deployed module reads these fields.
    return status.owned and not (
        status.busy
        or status.publication
        or status.pending
        or status.preview_active
        or status.operation_stage
        or status.intent
    )


def apply(panel, plan, journal=None, record=None):
    """Owned guard writes in order, then full readback and renderer re-inspection."""
    writes, done = verify(panel, plan)
    if record:
        record(writes, done)
    for key, member, value in writes[done:]:
        if journal:
            journal(unit="supplement:" + key, state="started", member=member, expected=value,
                    next_safe="Reobserve every guard field; resume only as an exact prefix.")
        panel._set(key, member, value)
        if journal:
            journal(unit="supplement:" + key, state="completed", member=member,
                    next_safe="Readback and renderer re-inspection still pending.")
    current = _observe(panel, plan, writes)
    if any(not _same(current[(key, member)], value) for key, member, value in writes):
        raise ValueError("supplement-readback-mismatch")
    # Re-inspect every renderer: the bound lists must still be the registered ones.
    status = panel.guard(plan)
    if guard_writes(plan, panel.observed_capture) != writes or not _idle(status):
        raise ValueError("supplement-renderers-changed-after-write")
    return {
        "success": True,
        "generation": plan.generation,
        "already_written": done,
        "written": [list(write) for write in writes[done:]],
        "verified": len(writes),
    }
