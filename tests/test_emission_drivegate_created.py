"""S: never adopt created metadata/initial values or collide private aliases."""
from copy import deepcopy
import json
from pathlib import Path

from scripts.emission_editor import drivegate_created as created
from scripts.emission_editor import drivegate_runtime as runtime
from scripts.emission_editor.drivegate_package import FIELDS, normalized_fixture
from test_emission_drivegate_runtime import owned_fixture
from test_emission_drivegate_evidence import field


def created_fixture():
    tree, state_bytes = owned_fixture()
    state = json.loads(state_bytes)
    declaration = normalized_fixture('offline','0.2.0')
    model = json.loads((Path(created.__file__).parent/'drivegate_native_model.json').read_bytes())
    seal = {'normalizedFixture':declaration,'cliVersion':'0.2.0','dllSha256':model['nativeDllSha256']}
    slots = {tree['id']:tree, **{n['id']:n for n in tree['children']}}
    for slot in slots.values():
        if slot['id'] != tree['id'] and slot['name'] != 'Private fields':slot['children'] = []
        parent = 'Root' if slot is tree else tree['id']
        slot.update(parentId=parent, position={'x':0,'y':0,'z':0}, rotation={'x':0,'y':0,'z':0,'w':1},
                    scale={'x':1,'y':1,'z':1}, isActive=True, isPersistent=True,
                    path=tree['name'] if slot is tree else tree['name']+'/'+slot['name'])
        values = {'Parent':{'kind':'reference','id':slot['id']+'-Parent','targetId':parent,'targetType':'[FrooxEngine]FrooxEngine.Slot'},
                  'Name':field('System.String',slot['name'],slot['id']+'-Name'),
                  'Tag':field('System.String',None,slot['id']+'-Tag'),
                  'OrderOffset':field('System.Int64',0,slot['id']+'-OrderOffset')}
        for name,typ,v in [('Position','ResoniteLink.float3',slot['position']),('Rotation','ResoniteLink.floatQ',slot['rotation']),
                           ('Scale','ResoniteLink.float3',slot['scale']),('IsActive','System.Boolean',True),('IsPersistent','System.Boolean',True)]:
            values[name] = field(typ,v,slot['id']+'-'+name)
        slot['members'] = values
        for comp in slot['components']:
            for name,typ,value in [('persistent','System.Boolean',True),('UpdateOrder','System.Int32',0),('Enabled','System.Boolean',True)]:
                comp['members'][name] = field(typ,value,comp['id']+'-'+name)
    components = {c['id']:c for n in slots.values() for c in n['components']}
    for name,(_,value) in FIELDS.items():
        member = components[state['components']['drivegate-'+name]['id']]['members']['Value']
        member['value'] = dict(zip(('r','g','b','a','profile'),[*value,'sRGB'])) if isinstance(value,list) else value
    return tree,state_bytes,seal


def test_created_scope_rejects_metadata_initial_value_and_global_alias_adoption(monkeypatch):
    # S: reflecting a partial apply into the expected snapshot would hide wrong
    # defaults, an external parent, or a shared identity with a private field.
    tree,state,seal = created_fixture()
    monkeypatch.setattr(created,'verify_package',lambda p:seal)
    monkeypatch.setattr(runtime,'verify_package',lambda p:seal)
    args = dict(package=None,state_bytes=state,native_dll_sha256=seal['dllSha256'])
    result = created.compare_created_fixture(tree,**args)
    assert result.status == 'passed',result.issues
    assert not result.nativeSucceeded and not result.executionPermission
    wrong = deepcopy(tree)
    wrong['components'][0]['members']['Enabled']['value'] = False
    assert created.compare_created_fixture(wrong,**args).status != 'passed'
    wrong = deepcopy(tree)
    wrong['children'][0]['components'][0]['members']['Value']['value'] = 1
    assert created.compare_created_fixture(wrong,**args).status != 'passed'
    wrong = deepcopy(tree)
    wrong['members']['Parent']['targetId'] = 'foreign-parent'
    assert created.compare_created_fixture(wrong,**args).status != 'passed'
    wrong = deepcopy(tree)
    wrong['members']['Name']['id'] = wrong['children'][0]['components'][0]['members']['Value']['id']
    assert created.compare_created_fixture(wrong,**args).status != 'passed'
    wrong = deepcopy(tree)
    wrong['members']['unknown'] = field('System.Int32',0,'unknown-id')
    assert created.compare_created_fixture(wrong,**args).status != 'passed'


def test_created_stage_cannot_launder_an_unchecked_reader_anchor(tmp_path,monkeypatch):
    # S: the reader copies reviewed_fixture stable metadata. A correct observed
    # graph alone must not authenticate a different caller-supplied anchor.
    from dataclasses import replace
    import hashlib
    from scripts.emission_editor import drivegate_native
    from test_emission_drivegate_runtime import staged_fixture
    package,execution,old = staged_fixture(tmp_path,monkeypatch)
    tree,_,model_seal = created_fixture()
    original = tree['id']
    tree['id'] = 'Reso_FA'
    for child in tree['children']:
        if child['parentId'] == original:
            child['parentId'] = 'Reso_FA'
            child['members']['Parent']['targetId'] = 'Reso_FA'
    # Only package/library authentication is stubbed. Real original read/diff
    # receipts, ownership and complete created comparator run; no native call.
    seal = runtime.verify_package(package) | {'dllSha256':model_seal['dllSha256']}
    monkeypatch.setattr(runtime,'verify_package',lambda p:seal)
    monkeypatch.setattr(created,'verify_package',lambda p:seal)
    monkeypatch.setattr(drivegate_native,'_dlls',lambda p:seal['dllSha256'])
    directory = old.fixture_read[1]
    raw = json.dumps({'ok':True,'data':tree}).encode()
    (directory/'stdout.bin').write_bytes(raw)
    receipt = json.loads((directory/'receipt.json').read_bytes())
    receipt['stdoutSha256'] = hashlib.sha256(raw).hexdigest()
    (directory/'receipt.json').write_text(json.dumps(receipt))
    stage = runtime.CreatedStage(**(vars(old)|{'roles':(),'parsed_records':{},'reviewed_fixture':tree}),
            native_layouts={},native_library_path=tmp_path/'lib',native_sdk_directory=tmp_path/'sdk')
    assert runtime.assess_deployed_stage(package,execution,stage).status == 'passed'
    foreign_anchor = deepcopy(tree)
    foreign_anchor['components'][0]['members']['Enabled']['value'] = False
    assert runtime.assess_deployed_stage(package,execution,replace(stage,reviewed_fixture=foreign_anchor)).status != 'passed'
