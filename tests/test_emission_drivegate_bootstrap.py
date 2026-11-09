"""S: a created-only observation must not borrow existing world/member IDs.

Fake CLI processes create real receipts. Package and the prior precreate audit
are test seams; current created layout, session topology, protection/diffs and
absence-before comparison are real. No world is contacted or modified.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json
import os
import sys

import pytest

from scripts.emission_editor import drivegate_bootstrap as bootstrap
from scripts.emission_editor import drivegate_created as created
from scripts.emission_editor import drivegate_native as native
from scripts.emission_editor import drivegate_runtime as runtime
from scripts.emission_editor.drivegate_capture import DiffRequest
from scripts.emission_editor.drivegate_evidence import ReadWindow, Verdict
from scripts.emission_editor.read_receipts import ReadRequest, collect_read
from test_emission_drivegate_created import created_fixture
from test_emission_drivegate_identity import panel_fixture
from test_emission_drivegate_protection import protection_inputs


def inputs(root, monkeypatch):
    root.mkdir()
    tree, state_bytes, seal = created_fixture()
    state = json.loads(state_bytes)
    ids = set()
    def identities(value):
        if isinstance(value,dict):
            if 'id' in value: ids.add(value['id'])
            for child in value.values(): identities(child)
        elif isinstance(value,list):
            for child in value: identities(child)
    identities(tree)
    mapping = {old:f'Reso_{0x1000+i:X}' for i,old in enumerate(sorted(ids))}
    def rebind(value):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in ('id','parentId','targetId') and child in mapping:
                    value[key] = mapping[child]
                else: rebind(child)
        elif isinstance(value,list):
            for child in value: rebind(child)
    rebind(tree); rebind(state)
    state_bytes = json.dumps(state, ensure_ascii=False).encode()
    panel, identity = panel_fixture()
    protection = protection_inputs(root)
    before_root = deepcopy(protection.context)
    before_root['children'].append(deepcopy(panel))
    def slot_members(node):
        node.setdefault('members',{})
        for child in node['children']: slot_members(child)
    slot_members(before_root)
    protection = replace(protection, context=deepcopy(before_root),roots=tuple(deepcopy(before_root['children'][:2])))
    after_root = deepcopy(before_root)
    embedded = deepcopy(tree)
    def prefix(node):
        node['path'] = 'Root/' + node['path']
        for child in node['children']: prefix(child)
    prefix(embedded)
    after_root['children'].append(embedded)
    after_root['position'] = {'x':4.25,'y':.5,'z':1.125}
    package, execution = root/'package', root/'execution'
    package.mkdir(); execution.mkdir()
    declaration = json.dumps(seal['normalizedFixture']).encode()
    (package/'fixture.json').write_bytes(declaration)
    (execution/'fixture.json').write_bytes(declaration)
    state_path = execution/'world.json'
    state_path.write_bytes(state_bytes)
    for role in ('probe','color','bool'):
        (package/'manifests').mkdir(exist_ok=True)
        (package/'manifests'/f'{role}.json').write_text(json.dumps({'worldState':str(state_path)}))
    def plan(ownership, state_file, operations):
        return {'valid':True,'schemaVersion':'1','ownershipKey':ownership,'stateFile':str(state_file),
            'operations':operations,'changes':[],'creates':0,'updates':0,'noOps':len(operations),
            'renames':0,'deletes':0,'atomic':False,'recovery':'checkpoint-and-reapply','warnings':[]}
    operations = [{'action':'no-op','kind':kind,'key':key,'path':value.get('path','Root/private/'+key),
                   'atomic':False,'recovery':'checkpoint-and-reapply'}
        for kind, table in (('slot',state['slots']),('component',state['components'])) for key,value in table.items()]
    fixture_plan = plan('emission-drivegate-offline', state_path, operations)
    request = DiffRequest('ws://localhost:16070',execution/'fixture.json',state_path)
    fixture_diff = runtime.DiffInput(root/'unused',request,state_bytes,fixture_plan,declaration)
    templates = []
    plans = {str(request.declaration):fixture_plan}
    for entry in protection.plans:
        entry.request.declaration.write_bytes(entry.sealed_declaration)
        entry.request.state.write_bytes(entry.expected_state)
        complete = plan(entry.expected_plan['ownershipKey'],entry.request.state,entry.expected_plan['operations'])
        templates.append(replace(entry,expected_plan=complete))
        plans[str(entry.request.declaration)] = complete
    protection = replace(protection,plans=tuple(templates))
    payload = {'fixture':tree,'root':after_root,'component':panel['children'][0]['children'][0]['components'][0],
               'discovery':{'count':1,'sessions':[{'sessionId':identity.session,'url':request.url}]},'plans':plans}
    payload_path = root/'payload.json'
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    executable = root/'resoloop'
    executable.write_text(f'#!{sys.executable}\n'+'''import json, os, pathlib, sys
args = sys.argv[1:]
p = json.loads(pathlib.Path(os.environ['BOOTSTRAP_PAYLOAD']).read_text())
if args[0] == 'inspect': data = p['root'] if args[1] == 'Root' else p['fixture']
elif args[:2] == ['component','inspect']: data = p['component']
elif args[0] == 'discover': data = p['discovery']
elif args[0] == 'diff':
    data = p['plans'][args[1]]
    if os.environ.get('BOOTSTRAP_CHANGE_STATE'):
        pathlib.Path(os.environ['BOOTSTRAP_CHANGE_STATE']).write_bytes(b'changed-state')
else: sys.exit(99)
print(json.dumps({'ok':True,'data':data},ensure_ascii=False))
''')
    executable.chmod(0o700)
    monkeypatch.setenv('PATH',str(root)+os.pathsep+os.environ['PATH'])
    monkeypatch.setenv('BOOTSTRAP_PAYLOAD',str(payload_path))
    # These seams do not waive the NEW scope/ID/metadata/initial-value checks.
    monkeypatch.setattr(bootstrap,'assess_precreate_history',lambda *a:Verdict('passed'))
    monkeypatch.setattr(runtime,'verify_runtime_execution',lambda *a:Verdict('passed'))
    for module in (bootstrap,created,runtime):
        monkeypatch.setattr(module,'verify_package',lambda p:seal)
    monkeypatch.setattr(native,'_dlls',lambda p:seal['dllSha256'])
    monkeypatch.setattr(bootstrap,'_dlls',lambda p:seal['dllSha256'])
    # Save an original prior Root read using the same fake process boundary.
    payload['root'] = before_root
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    started = datetime.now(UTC)
    rr = ReadRequest('inspect',request.url,'Root',64)
    parent_directory = root/'before-root'
    collect_read(rr,parent_directory,cwd=root)
    finished = datetime.now(UTC)
    payload['root'] = after_root
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    precreate = runtime.PrecreateScope(ReadWindow(request.url,started,finished,finished),identity,
        root/'prior-session-before',root/'prior-session-after',(rr,parent_directory),[],
        before_root,protection,replace(fixture_diff,expected_state=None),tuple(templates),{})
    kwargs = dict(package=package,execution=execution,precreate=precreate,fixture_diff=fixture_diff,
        panel_id=panel['id'],native_library_path=root/'lib',native_sdk_directory=root/'sdk',cwd=root)
    return kwargs, payload_path


def test_created_capture_keeps_raw_and_refuses_borrowed_slot_member_ids(tmp_path,monkeypatch):
    # S: a correct name/state/initial-value snapshot can still be an old object
    # (including a member ID), so every new native identity needs absence-before.
    kwargs, payload_path = inputs(tmp_path/'case',monkeypatch)
    good = bootstrap.collect_created_stage(directory=tmp_path/'created',**kwargs)
    assert isinstance(good,runtime.CreatedStage)
    assert good.native_session_evidence is not None
    assert bootstrap.assess_created_transition(kwargs['package'],kwargs['execution'],kwargs['precreate'],good).status == 'passed'
    assert good.roles == ()
    assert not runtime.assess_deployed_stage(kwargs['package'],kwargs['execution'],good).executionPermission
    # Native session evidence stays attached; removing it cannot authorize the
    # transition, even though the legacy low-level graph API remains available.
    with pytest.raises(ValueError,match='created-native-session-required'):
        bootstrap.require_created_transition(kwargs['package'],kwargs['execution'],kwargs['precreate'],
            replace(good,native_session_evidence=None))
    from scripts.emission_editor.read_receipts import load_read
    parent = load_read(kwargs['precreate'].parent_read[1])['response']['data']
    borrowed = deepcopy(parent)
    borrowed['components'].append({'id':'existing-component','type':'[FrooxEngine]FrooxEngine.ObjectRoot',
        'members':{'old':{'kind':'field','id':good.reviewed_fixture['members']['Name']['id'],
                         'type':'System.String','value':'以前からある'}}})
    # Re-capture a different genuine before receipt rather than editing raw.
    payload = json.loads(payload_path.read_bytes())
    payload['root'] = borrowed
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    prior = tmp_path/'borrowed-before'
    rr = kwargs['precreate'].parent_read[0]
    collect_read(rr,prior,cwd=kwargs['cwd'])
    precreate = replace(kwargs['precreate'],parent_read=(rr,prior))
    payload['root']['children'].append(deepcopy(json.loads((tmp_path/'created/root-after/stdout.bin').read_bytes())['data']['children'][-1]))
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    # Make a new later capture; the only adverse condition is identity reuse.
    with pytest.raises(bootstrap.NativeReadFailure,match='created-preexisting-native-id'):
        bootstrap.collect_created_stage(directory=tmp_path/'borrowed',**(kwargs|{'precreate':precreate}))
    assert json.loads((tmp_path/'borrowed/result.json').read_bytes())['status'] == 'failed'
    assert (tmp_path/'borrowed/fixture/stdout.bin').is_file()
    assert kwargs['fixture_diff'].request.state.read_bytes() == kwargs['fixture_diff'].expected_state




def test_probe_anchor_joins_original_created_scope_not_current_private_defaults(tmp_path,monkeypatch):
    # S: the FIRST deployed anchor has no earlier deployed anchor. Accepting
    # its observed private values as defaults would launder a premature capture.
    # Module compiler/runtime authentication is stubbed ONLY in this connection
    # test; the native layout/runtime guards have their existing representative S.
    kwargs, payload_path = inputs(tmp_path/'case',monkeypatch)
    original = bootstrap.collect_created_stage(directory=tmp_path/'created',**kwargs)
    payload = json.loads(payload_path.read_bytes())
    probe_parent = payload['fixture']['children'][1]
    child = {'id':'Reso_F000','name':'sealed-probe','parentId':probe_parent['id'],
        'path':probe_parent['path']+'/sealed-probe','isReferenceOnly':False,'components':[],
        'children':[],'members':{'Name':{'id':'Reso_F001','kind':'field','type':'System.String','value':'sealed-probe'}}}
    probe_parent['children'] = [child]
    embedded = deepcopy(payload['fixture'])
    def prefix(node):
        node['path'] = 'Root/'+node['path']
        for child in node['children']: prefix(child)
    prefix(embedded)
    payload['root']['children'][-1] = embedded
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    monkeypatch.setattr(bootstrap,'load_native_layout',lambda *a,**k:Verdict('passed',data=({},)))
    monkeypatch.setattr(bootstrap,'current_fixture_expectation',lambda current,**k:Verdict('passed',data=(deepcopy(current),)))
    def assess(package,execution,stage):
        if isinstance(stage,runtime.CreatedStage):
            return runtime.assess_deployed_stage(package,execution,stage)
        return Verdict('passed',data=runtime._owned_scope(package,stage.reviewed_fixture,stage.expected_state,stage.roles)[:2])
    monkeypatch.setattr(bootstrap,'assess_deployed_stage',assess)
    anchor_args = dict(package=kwargs['package'],execution=kwargs['execution'],precreate=kwargs['precreate'],
        created=original,parsed_record=tmp_path/'parsed',native_layout=tmp_path/'layout',cwd=kwargs['cwd'])
    good = bootstrap.collect_probe_anchor(directory=tmp_path/'anchor',**anchor_args)
    assert type(good) is runtime.NativeDeployedStage
    assert good.roles == ('probe',) and good.expected_state == original.expected_state
    assert good.native_session_evidence is not None
    assert original.reviewed_fixture['children'][1]['children'] == []
    # Correct initial private values, but a probe member borrowed from the old
    # Root is also invalid, independently of the future lifecycle write journal.
    borrowed = deepcopy(good.reviewed_fixture)
    borrowed['children'][1]['children'][0]['members']['Name']['id'] = kwargs['precreate'].protected_baseline.roots[0]['components'][0]['members']['Value']['id']
    bad = replace(good,reviewed_fixture=borrowed)
    rejected = bootstrap.assess_probe_transition(kwargs['package'],kwargs['execution'],kwargs['precreate'],original,bad)
    assert rejected.status != 'passed' and 'probe-preexisting-or-lost-native-id' in rejected.issues[0]
    payload['fixture']['children'][0]['components'][0]['members']['Value']['value'] = 1
    payload['root']['children'][-1]['children'][0]['components'][0]['members']['Value']['value'] = 1
    payload_path.write_text(json.dumps(payload,ensure_ascii=False))
    with pytest.raises(bootstrap.NativeReadFailure,match='probe-initial-fields'):
        bootstrap.collect_probe_anchor(directory=tmp_path/'premature-capture',**anchor_args)
    assert (tmp_path/'premature-capture/fixture/stdout.bin').is_file()
    assert original.expected_state == kwargs['fixture_diff'].expected_state
