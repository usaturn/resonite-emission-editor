"""S: full native ownership/default/alias checks before runtime projection.

The operation JSON is an offline compiler INPUT fixture, not a value-list test.
No mocks here are evidence of native success.
"""
from copy import deepcopy
import json
from pathlib import Path

from scripts.emission_editor import drivegate_layout as layout
from test_emission_drivegate_evidence import sdk_record


def native_fixture(document=None):
    if document is None:
        document = json.loads((Path(__file__).parent / 'data/native_bool_layout.json').read_text())
    model = json.loads((Path(layout.__file__).parent / 'drivegate_native_model.json').read_text())
    # A separate native alias space, including aliases of SyncRef members.
    aliases = {'native-layout-parent': 'owned-parent'}
    def symbols(value):
        if isinstance(value, dict):
            target = value.get('targetId')
            if isinstance(target, str) and target.startswith('private:'):
                aliases[target] = 'private-' + target.split(':', 1)[1]
            for v in value.values():
                symbols(v)
        elif isinstance(value, list):
            for v in value:
                symbols(v)
    symbols(document)
    if 'private:Driven' in aliases:
        aliases['private:Driven'] = 'private-bool'
    serial = 0
    def ident(raw=None):
        nonlocal serial
        serial += 1
        result = 'observed-' + str(serial)
        if raw is not None:
            aliases[raw] = result
        return result
    slots, components, pending = {}, {}, []
    for op in document['operations']:
        data = op['data']
        if op['operation'] == 'AddSlot':
            node = {'id': ident(data['id']), 'name': data['name']['value'], 'isReferenceOnly': False,
                    'components': [], 'children': [], 'members': {}}
            slots[data['id']] = node
        else:
            c = {'id': ident(data['id']), 'type': data['componentType'], 'members': {}}
            components[data['id']] = c
            slots[op['containerSlotId']]['components'].append(c)
        def member(shape, wire, default=None):
            m = {k: v for k, v in shape.items() if k != 'element'} | {'id': ident(wire.get('id'))}
            if shape['kind'] == 'field':
                m['value'] = wire.get('type') if wire.get('$type') == 'type' else wire.get('value', default)
            elif shape['kind'] == 'reference':
                pending.append((m, wire.get('targetId')))
            else:
                m['elements'] = [member(shape['element'], e) for e in wire.get('elements', [])]
            return m
        if op['operation'] == 'AddSlot':
            defaults = {'Position': {'x': 0, 'y': 0, 'z': 0}, 'Rotation': {'x': 0, 'y': 0, 'z': 0, 'w': 1},
                        'Scale': {'x': 1, 'y': 1, 'z': 1}, 'Tag': None, 'IsActive': True,
                        'IsPersistent': True, 'OrderOffset': 0}
            for key, shape in model['slotMembers'].items():
                wire = data.get(key[0].lower() + key[1:]) or {}
                node['members'][key] = member({k: v for k, v in shape.items() if k != 'default'}, wire, defaults.get(key))
            for key in ('Name', 'Position', 'Rotation', 'Scale', 'IsActive', 'IsPersistent', 'Tag'):
                value = node['members'][key]['value']
                if value is not None:
                    node[key[0].lower() + key[1:]] = value
        else:
            defaults = {'persistent': True, 'Enabled': True, 'UpdateOrder': 0,
                        'OverrideOnLink': False, 'OnlyDirectBinding': False, 'Path': []}
            for key, shape in model['components'][c['type']]['members'].items():
                c['members'][key] = member(shape, data['members'].get(key, {}), defaults.get(key))
    parent = {'id': 'owned-parent', 'name': 'BoolDrive parent', 'isReferenceOnly': False,
              'path': 'Root/private/BoolDrive parent', 'components': [], 'children': []}
    for op in document['operations']:
        if op['operation'] != 'AddSlot':
            continue
        d = op['data']; n = slots[d['id']]; p = d['parent']['targetId']
        owner = parent if p == 'native-layout-parent' else slots[p]
        owner['children'].append(n)
        n['parentId'] = owner['id']
        n['path'] = owner['path'].rstrip('/') + '/' + n['name']
    for m, target in pending:
        if target is not None:
            m['targetId'] = aliases[target]
    args = {'module': document['module'],
            'private_bindings': {k.split(':', 1)[1]: v for k, v in aliases.items() if k.startswith('private:')},
            'native_dll_sha256': model['nativeDllSha256']}
    return document, parent, args, slots, components


def test_full_layout_checks_passive_coverage_defaults_and_type_payload():
    # S: silently ignoring passive metadata can bless a wrong/foreign module.
    document, parent, args, slots, components = native_fixture()
    assert layout.compare_native_layout(document, parent, **args).status == 'passed'
    unknown = deepcopy(document)
    unknown['operations'][0]['data']['unmodeled'] = True
    assert layout.compare_native_layout(unknown, parent, **args).status == 'failed'
    native = next(c for c in components.values() if c['type'].endswith('DynamicTypeVariable'))
    native['members']['Value']['value'] = 'int'
    assert layout.compare_native_layout(document, parent, **args).status == 'failed'
    native['members']['Value']['value'] = 'bool'
    dvs = next(c for c in components.values() if c['type'].endswith('DynamicVariableSpace'))
    dvs['members']['unreviewed'] = {'id': 'foreign-member', 'kind': 'field', 'type': 'System.Boolean', 'value': True}
    assert layout.compare_native_layout(document, parent, **args).status == 'failed'
    del dvs['members']['unreviewed']
    node = next(n for n in slots.values() if n['name'] == 'Assets: Templates')
    node['members']['IsActive']['value'] = 0  # bool/int must not conflate.
    assert layout.compare_native_layout(document, parent, **args).status == 'failed'


def test_aliases_resolve_members_not_only_components_and_reject_collisions():
    # S: a DynamicIO ref to the private field instead of the Drive SyncRef can
    # appear plausible while bypassing the actual port indirection/ownership.
    document, parent, args, _, components = native_fixture()
    assert layout.compare_native_layout(document, parent, **args).status == 'passed'
    target = next(c for c in components.values() if c['members'].get('VariableName', {}).get('value') == 'ProtoGraph/Target')
    original = target['members']['Reference']['targetId']
    target['members']['Reference']['targetId'] = 'private-bool'
    assert layout.compare_native_layout(document, parent, **args).status == 'failed'
    target['members']['Reference']['targetId'] = original
    target['members']['Reference']['id'] = next(c['id'] for c in components.values())
    assert layout.compare_native_layout(document, parent, **args).status != 'passed'


def test_model_cannot_attest_different_native_dlls():
    # S: a valid old default/schema table must not bless another native ABI.
    document, parent, args, _, _ = native_fixture()
    assert layout.compare_native_layout(document, parent, **args).status == 'passed'
    changed = deepcopy(args['native_dll_sha256'])
    changed['FrooxEngine.dll'] = 'different-build'
    assert layout.compare_native_layout(document, parent, **(args | {'native_dll_sha256': changed})).status != 'passed'


def test_runtime_projection_is_only_after_full_layout_and_has_no_unchecked_extras():
    # S: the all-component/runtime-only join must neither reject correct native
    # metadata nor drop an unchecked graph edge while projecting the subset.
    document, parent, args, slots, components = native_fixture()
    record = sdk_record()
    bson_nodes = record['Object']['Children'][0]['Children'][0]['Children']
    tags = {n['name']: n['members']['Tag'].get('value') for n in slots.values()}
    for node in bson_nodes:
        node['Tag'] = {'Data': tags[node['Name']['Data']]}
        for c in node['Components']['Data']:
            c['Data']['persistent'] = {'Data': True}
    result = layout.compare_native_runtime(record, document, parent, **args)
    assert result.status == 'passed', result.issues
    proxy = next(c for c in components.values() if c['type'].endswith('+Proxy'))
    proxy['members']['Path']['elements'].append({'id': 'foreign-edge', 'kind': 'reference', 'targetId': 'outside'})
    assert layout.compare_native_runtime(record, document, parent, **args).status != 'passed'
