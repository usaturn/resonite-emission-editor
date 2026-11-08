"""Full offline native layout/default/alias comparison, NOT native success.

Expected values come from sealed operation evidence and the exact-DLL model,
never an observed value baseline. Only element IDs are bound structurally.
The caller still must authenticate the operation/record receipts, native session,
role parent and private field ownership. No I/O to a world or mutation/retry API.
"""
from copy import deepcopy
from pathlib import Path

from .drivegate_evidence import (
    Verdict, _result, _tree, _sdk_graph, _RUNTIME_PREFIXES, compare_subtree,
    compare_sdk_runtime,
)
from .drivegate_native import _bindings, _validate_document
from .read_receipts import _decode

_MODEL = Path(__file__).with_name('drivegate_native_model.json')
_WIRE_FIELDS = {'bool': 'System.Boolean', 'int': 'System.Int32', 'long': 'System.Int64',
                'float': 'System.Single', 'double': 'System.Double', 'string': 'System.String',
                'type': 'System.Type'}


def _model(native_dll_sha256):
    model = _decode(_MODEL.read_bytes())
    if (model['format'] != 'emission-native-model-v1'
            or any(native_dll_sha256.get(k) != v for k, v in model['nativeDllSha256'].items())):
        raise ValueError('missing:source-grounded-native-model-for-dlls')
    return model


def _slot_key(node):
    # Null is known only when the complete typed field is present. Top-level
    # SlotInfo.name collapses null; never use it to infer a native field value.
    def value(key):
        m = node['members'][key]
        if (m.get('kind') != 'field' or m.get('type') != 'System.String'
                or not isinstance(m.get('id'), str) or not m['id']):
            raise ValueError('missing:native-slot-name-tag')
        return m.get('value')
    return value('Name'), value('Tag')


def _layout_expectation(document, parent, module, private_bindings, model):
    _validate_document(document, module, document['sdkVersion'])
    ports = _bindings(module)
    names = set(ports['inputs']) | set(ports['outputs'])
    if (set(private_bindings) != names
            or not all(isinstance(i, str) and i for i in private_bindings.values())
            or len(set(private_bindings.values())) != len(private_bindings)):
        raise ValueError('native-private-binding-coverage')
    if (not isinstance(parent.get('id'), str) or not parent['id']
            or not isinstance(parent.get('path'), str) or not parent['path']
            or parent.get('components') != [] or len(parent['children']) != 1):
        raise ValueError('native-role-parent-coverage')
    slots, components, issues = _tree(parent)
    if issues:
        raise ValueError('missing:complete-native-layout')
    # Every observed identity, including unsent/default and nested members, is
    # globally distinct and cannot borrow an external/private port identity.
    used = set(private_bindings.values())
    def claim(ident):
        if not isinstance(ident, str) or not ident or ident in used:
            raise ValueError('native-duplicate-or-external-identity')
        used.add(ident)
    for ident in (*slots, *components):
        claim(ident)
    def claim_member(member):
        claim(member['id'])
        for m in member.get('elements', []):
            claim_member(m)
        for m in member.get('members', {}).values():
            claim_member(m)
    for n, _ in [*slots.values(), *components.values()]:
        for m in n.get('members', {}).values():
            claim_member(m)

    aliases = {'native-layout-parent': parent['id'],
               **{'private:' + k: v for k, v in private_bindings.items()}}
    reverse = {v: k for k, v in aliases.items()}
    if len(reverse) != len(aliases):
        raise ValueError('native-external-alias-collision')
    def bind(offline, actual):
        if offline is None:
            return
        if offline in aliases or actual in reverse:
            raise ValueError('native-alias-collision')
        aliases[offline], reverse[actual] = actual, offline
    def bind_member(wire, actual):
        bind(wire.get('id'), actual['id'])
        if wire.get('$type') == 'list':
            if actual.get('kind') != 'list' or len(actual['elements']) != len(wire['elements']):
                raise ValueError('native-list-binding-coverage')
            for a, b in zip(wire['elements'], actual['elements']):
                bind_member(a, b)

    expected_slots, expected_components, observed = {}, {}, {}
    for op in document['operations']:
        d = op['data']
        is_slot = op['operation'] == 'AddSlot'
        op_keys = {'operation', 'messageId', 'data'} | (set() if is_slot else {'containerSlotId'})
        data_keys = ({'id', 'isReferenceOnly', 'parent', 'position', 'rotation', 'scale', 'isActive',
                      'isPersistent', 'name', 'tag', 'orderOffset', 'components', 'children'} if is_slot
                     else {'id', 'isReferenceOnly', 'componentType', 'members'})
        if (set(op) != op_keys or set(d) != data_keys or op['messageId'] is not None
                or d['isReferenceOnly'] is not False
                or (is_slot and (d['components'] is not None or d['children'] is not None))):
            raise ValueError('native-unknown-operation-payload')
        if is_slot:
            actual_parent = slots[aliases[d['parent']['targetId']]][0]
            key = (d['name']['value'], (d.get('tag') or {}).get('value'))
            matches = [s for s in actual_parent['children'] if _slot_key(s) == key]
            # Native sibling order is NOT an ownership identity. Repeated names
            # are disambiguated by the compiler's PG source-span Tag, not ordinal.
            if len(matches) != 1:
                raise ValueError('native-slot-ambiguous-or-missing')
            actual = matches[0]
            bind(d['id'], actual['id'])
            expected_slots[d['id']] = d
            for name in model['slotMembers']:
                wire = d.get(name[0].lower() + name[1:])
                if wire is not None:
                    bind_member(wire, actual['members'][name])
        else:
            typ = d['componentType']
            schema = model['components'].get(typ)
            if schema is None:
                raise ValueError('missing:source-grounded-component-schema:' + typ)
            candidates = slots[aliases[op['containerSlotId']]][0]['components']
            variable = d['members'].get('VariableName')
            matches = [c for c in candidates if c['type'] == typ and (variable is None or
                       c['members'].get('VariableName', {}).get('value') == variable['value'])]
            if len(matches) != 1:
                raise ValueError('native-component-ambiguous-or-missing')
            actual = matches[0]
            bind(d['id'], actual['id'])
            expected_components[d['id']] = (d, op['containerSlotId'])
            if set(d['members']) - set(schema['members']):
                raise ValueError('native-sent-member-outside-schema')
            for name, wire in d['members'].items():
                bind_member(wire, actual['members'][name])
        observed[d['id']] = actual
    if ({aliases[k] for k in expected_slots} != set(slots) - {parent['id']}
            or {aliases[k] for k in expected_components} != set(components)):
        raise ValueError('native-all-layout-coverage')

    def expected_member(shape, wire, actual, default_marker=()):
        result = {k: deepcopy(v) for k, v in shape.items() if k not in ('element', 'default')}
        result['id'] = actual['id']
        kind = shape['kind']
        if wire is None:
            if not default_marker:
                raise ValueError('missing:independent-native-default')
            raw = default_marker[0]
            if kind == 'list':
                if raw != []:
                    raise ValueError('missing:nonempty-default-list')
                result['elements'] = []
            elif kind == 'field':
                result['value'] = deepcopy(raw)
            else:
                raise ValueError('missing:native-default-kind')
        elif kind == 'field':
            is_type = wire.get('$type') == 'type'
            keys = {'id', 'type' if is_type else 'value'} | ({'$type'} if '$type' in wire else set())
            if (set(wire) != keys
                    or ('$type' in wire and _WIRE_FIELDS.get(wire['$type']) != shape['type'])):
                raise ValueError('native-wire-field-type-or-shape')
            # Field_Type.BoxedValue is its encoded type string, not a BSON index.
            value = wire['type'] if is_type else wire['value']
            if is_type and value is not None and not isinstance(value, str):
                raise ValueError('native-wire-type-value')
            result['value'] = deepcopy(None if is_type and value == '' else value)
        elif kind == 'reference':
            if (set(wire) != {'id', 'targetId', 'targetType'} | ({'$type'} if '$type' in wire else set())
                    or wire.get('$type', 'reference') != 'reference'
                    or wire['targetType'] not in (None, shape['targetType'])):
                raise ValueError('native-wire-reference-kind-or-shape')
            target = wire['targetId']
            result['targetId'] = None if target is None else aliases[target]
        elif kind == 'list':
            if wire.get('$type') != 'list' or set(wire) != {'id', '$type', 'elements'}:
                raise ValueError('native-wire-list-kind-or-shape')
            result['elements'] = [expected_member(shape['element'], w, a)
                                  for w, a in zip(wire['elements'], actual['elements'])]
        else:
            raise ValueError('missing:native-member-kind')
        return result

    built = {}
    for ident, d in expected_slots.items():
        actual = observed[ident]
        node = {'id': aliases[ident], 'name': d['name']['value'], 'isReferenceOnly': False,
                'parentId': aliases[d['parent']['targetId']], 'components': [], 'children': [], 'members': {}}
        owner_path = parent['path'] if d['parent']['targetId'] == 'native-layout-parent' else built[d['parent']['targetId']]['path']
        node['path'] = owner_path.rstrip('/') + '/' + node['name']
        for name, shape in model['slotMembers'].items():
            key = name[0].lower() + name[1:]
            wire = d.get(key)
            default = (shape['default'],) if 'default' in shape else ()
            node['members'][name] = expected_member(shape, wire, actual['members'][name], default)
            if name != 'Parent' and name != 'OrderOffset':
                value = node['members'][name]['value']
                if value is not None:
                    node[key] = deepcopy(value)
        built[ident] = node
        if d['parent']['targetId'] != 'native-layout-parent':
            built[d['parent']['targetId']]['children'].append(node)
    for ident, (d, sid) in expected_components.items():
        schema = model['components'][d['componentType']]
        comp = {'id': aliases[ident], 'type': d['componentType'], 'members': {}}
        for name, shape in schema['members'].items():
            default = (schema['defaults'][name],) if name in schema['defaults'] else ()
            comp['members'][name] = expected_member(shape, d['members'].get(name), observed[ident]['members'][name], default)
        built[sid]['components'].append(comp)
    root = next(n for i, n in built.items() if expected_slots[i]['parent']['targetId'] == 'native-layout-parent')
    return root, aliases, observed


def compare_native_layout(document: dict, parent: dict, *, module: str,
                          private_bindings: dict, native_dll_sha256: dict) -> Verdict:
    """Compare ALL owned descendants and typed members using a native model.

    Receipt/DLL-currentness and role/private ownership checks are the caller's
    responsibility; use the receipt-bound entry point for a deployed assessment.
    No caller ID map or observed default-value baseline can override this model.
    """
    try:
        model = _model(native_dll_sha256)
        expected, aliases, observed = _layout_expectation(document, parent, module, private_bindings, model)
        compared = compare_subtree(expected, parent['children'][0])
        if compared.status != 'passed':
            return compared
        return Verdict('passed', data=(expected, aliases, observed))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return _result([str(exc) if str(exc).startswith('missing:') else 'native-layout:' + str(exc)])


def compare_native_runtime(record: dict, document: dict, parent: dict, *, module: str,
                           private_bindings: dict, native_dll_sha256: dict) -> Verdict:
    """Full-layout first, then audited BSON runtime meaning; no blanket filtering.

    Only independently validated native defaults absent from the BSON graph may
    be projected out. Every runtime component and every other member/edge must
    join by source-span slot identity and component type, not persistence GUID.
    Both input documents must separately be bound to their original receipts.
    """
    checked = compare_native_layout(document, parent, module=module,
                                    private_bindings=private_bindings, native_dll_sha256=native_dll_sha256)
    if checked.status != 'passed':
        return checked
    try:
        model = _model(native_dll_sha256)
        _, aliases, observed = checked.data
        graph, _ = _sdk_graph(record, module)
        native = {}
        slot_data = {o['data']['id']: o['data'] for o in document['operations'] if o['operation'] == 'AddSlot'}
        for op in document['operations']:
            d = op['data']
            if op['operation'] != 'AddComponent' or not d['componentType'].startswith(_RUNTIME_PREFIXES):
                continue  # Passive components have ALREADY passed the full layout.
            s = slot_data[op['containerSlotId']]
            key = (s['name']['value'], (s.get('tag') or {}).get('value'), d['componentType'])
            if key in native:
                raise ValueError('native-runtime-ambiguous-source-span')
            native[key] = d['id']
        if len(native) != len(graph):
            raise ValueError('native-runtime-all-component-coverage')
        mapping, projection = {}, []
        for ident, c in graph.items():
            key = (c['slot'], c.get('tag'), c['type'])
            native_id = native.pop(key)
            actual = observed[native_id]
            extra = set(actual['members']) - set(c['members'])
            if not extra <= model['components'][c['type']]['defaults'].keys():
                raise ValueError('native-runtime-unchecked-extra-member')
            mapping[ident] = aliases[native_id]
            projection.append({'id': actual['id'], 'type': actual['type'],
                               'members': {k: actual['members'][k] for k in c['members']}})
        result = compare_sdk_runtime(record, projection, module=module, id_map=mapping, private_bindings=private_bindings)
        return Verdict('passed', data=(mapping,)) if result.status == 'passed' else result
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return _result(['native-runtime:' + str(exc)])
