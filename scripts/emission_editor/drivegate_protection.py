"""Explicit protected subtrees in a full Root read; no world writes or adoption.

Legacy offline callers may still supply a single subtree dictionary. Native
multi-root callers supply an independently reviewed context, selected subtrees
and complete no-op diff templates. Whole Root values are never protected.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .drivegate_evidence import (
    Verdict, _tree, _strict_equal, compare_root_context, compare_subtree,
)
from .read_receipts import _decode

if TYPE_CHECKING:
    from .drivegate_runtime import DiffInput


@dataclass(frozen=True)
class ProtectedScope:
    context: dict
    roots: tuple[dict, ...]
    plans: tuple['DiffInput', ...]


def protection_roots(baseline):
    if not isinstance(baseline, ProtectedScope):
        return (baseline,)
    if not baseline.roots or not isinstance(baseline.roots, tuple):
        raise ValueError('protected-scope-empty-roots')
    ids = set()
    for root in baseline.roots:
        slots, components, issues = _tree(root)
        current = set(slots) | set(components)
        if issues or root['id'] == 'Root' or ids & current:
            raise ValueError('protected-scope-invalid-or-overlapping-roots')
        ids.update(current)
    matched = compare_root_context(baseline.context, baseline.context, protected=baseline.roots)
    if matched.status != 'passed':
        raise ValueError('protected-scope-context:' + ','.join(matched.issues))
    return baseline.roots


def protection_ids(baseline):
    result = set()
    for root in protection_roots(baseline):
        slots, components, issues = _tree(root)
        if issues:
            raise ValueError('protected-scope-incomplete-tree')
        result.update(slots)
        result.update(components)
    return result


def compare_protection(baseline, observed):
    if isinstance(baseline, ProtectedScope):
        return compare_root_context(baseline.context, observed, protected=protection_roots(baseline))
    return compare_subtree(baseline, observed)


def compare_protection_scope(original, current):
    """Reject root-set reduction and baseline/template replacement across stages."""
    if isinstance(original, ProtectedScope) != isinstance(current, ProtectedScope):
        return Verdict('failed', ('protected-scope-kind-changed',))
    if not isinstance(original, ProtectedScope):
        return compare_subtree(original, current)
    left = {r['id']:r for r in protection_roots(original)}
    right = {r['id']:r for r in protection_roots(current)}
    if left.keys() != right.keys():
        return Verdict('failed', ('protected-scope-root-set-changed',))
    for ident in left:
        checked = compare_subtree(left[ident],right[ident])
        if checked.status != 'passed':
            return checked
    if (len(original.plans) != len(current.plans)
            or any(a.request != b.request or a.expected_state != b.expected_state
                   or a.sealed_declaration != b.sealed_declaration
                   or not _strict_equal(a.expected_plan,b.expected_plan)
                   for a,b in zip(original.plans,current.plans))):
        return Verdict('failed', ('protected-scope-diff-templates-changed',))
    return compare_root_context(original.context,current.context,protected=original.roots)


def _bound_root(plan, roots):
    """Bind a complete no-op declaration/state/plan to exactly one selected root.

    These are fresh read-only state maps, not authority to replay ApplyState.
    Actual diff transport/full-plan/window checks are performed by assess_diff.
    """
    declaration = _decode(plan.sealed_declaration)
    state = _decode(plan.expected_state)
    ownership = declaration['ownership']['key']
    if (type(state.get('schemaVersion')) is not int or state['schemaVersion'] != 2
            or state['ownershipKey'] != ownership or state.get('assets') != {}
            or plan.expected_plan['ownershipKey'] != ownership
            or plan.expected_plan['stateFile'] != str(plan.request.state)):
        raise ValueError('protected-diff-state-context')
    ident = state['slots'][declaration['slot']['key']]['id']
    if ident not in roots:
        raise ValueError('protected-diff-root-outside-selection')
    slots, components, issues = _tree(roots[ident])
    if issues:
        raise ValueError('protected-diff-incomplete-root')
    keys, slot_ids, component_ids = set(), set(), set()
    def visit(node, parent):
        key = node['slot']['key']
        entry = state['slots'][key]
        actual, actual_parent = slots[entry['id']]
        if (('slot',key) in keys or entry['id'] in slot_ids
                or actual_parent != parent or node['slot']['name'] != actual['name']
                or entry['path'] != actual['path']):
            raise ValueError('protected-diff-slot-binding')
        keys.add(('slot',key)); slot_ids.add(entry['id'])
        for comp in node['components']:
            ck = comp['key']; ce = state['components'][ck]
            observed, owner = components[ce['id']]
            if (('component',ck) in keys or ce['id'] in component_ids
                    or ce['slotKey'] != key or owner != actual['id'] or ce['type'] != observed['type']):
                raise ValueError('protected-diff-component-binding')
            keys.add(('component',ck)); component_ids.add(ce['id'])
        for child in node['children']:
            visit(child,actual['id'])
    visit(declaration,None)
    if (slot_ids != set(slots) or component_ids != set(components)
            or set(state['slots']) != {k for kind,k in keys if kind == 'slot'}
            or set(state['components']) != {k for kind,k in keys if kind == 'component'}):
        raise ValueError('protected-diff-complete-coverage')
    operations = plan.expected_plan['operations']
    # WorldService.BuildComponentPlans uses the explicit key after '/@'.
    def path(op):
        if op['kind'] == 'slot':
            return state['slots'][op['key']]['path']
        owner = state['components'][op['key']]['slotKey']
        return state['slots'][owner]['path'] + '/@' + op['key']
    if (plan.expected_plan['changes'] or len(operations) != len(keys)
            or {(op['kind'],op['key']) for op in operations} != keys
            or any(op['action'] != 'no-op' or op['path'] != path(op) for op in operations)):
        raise ValueError('protected-diff-not-complete-noop')
    return ident


def validate_protected_diffs(baseline, supplied):
    supplied = tuple(supplied)
    if not isinstance(baseline,ProtectedScope):
        return supplied
    roots = {r['id']:r for r in protection_roots(baseline)}
    try:
        bound = [_bound_root(p,roots) for p in baseline.plans]
        if len(bound) != len(roots) or set(bound) != set(roots) or len(supplied) != len(bound):
            raise ValueError('protected-diff-root-coverage')
        for template, actual in zip(baseline.plans,supplied):
            if (template.request != actual.request or template.expected_state != actual.expected_state
                    or template.sealed_declaration != actual.sealed_declaration
                    or not _strict_equal(template.expected_plan,actual.expected_plan)):
                raise ValueError('protected-diff-template-changed')
        return supplied
    except (KeyError,TypeError,AttributeError) as exc:
        raise ValueError('protected-diff-binding:' + str(exc)) from exc
