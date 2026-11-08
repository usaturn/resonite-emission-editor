"""S: two valid same-typed rows must never preview/restore each other's material."""
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.emission_editor import slim, slim_demo
from scripts.emission_editor.model import Avatar, MaterialRow, MaterialUse, RegistryPlan


def test_editing_assembly_keeps_each_rows_source_and_original_separate(tmp_path):
    avatar = Avatar('avatar','日本語の試験対象',('mesh-a','mesh-b'))
    rows = tuple(MaterialRow('material-'+str(i),'材質'+str(i),'field-'+str(i),
        {'r':2.5+i,'g':.5,'b':1.,'a':.37+i*.1,'profile':'sRGB'},
        (MaterialUse('mesh-'+name,name,'renderer-'+name,0,'binding-'+name,'material-'+str(i),'list-'+name),),
        (avatar,)) for i,name in enumerate(('a','b')))
    plan = RegistryPlan('session','test-generation','now',(avatar,),rows,True,())
    source = Path(__file__).resolve().parents[1]
    document = slim.render_panel(plan,template=source/'content/emission-editor/registry.json')
    components = slim._components(document)
    bundle = slim.session_manifest(plan,world_state=tmp_path/'world.json',deploy_state=tmp_path/'flux.json',
        source_directory=tmp_path/'sources',source_root=source/'flux/emission-editor')
    instances = {(m['parent'],m['modules'][0]['module']):m['modules'][0]
                 for m in bundle['manifests']}
    for i,row in enumerate(rows):
        key = f'reg-{plan.generation}-row-{i}'
        for kind in ('RowPreview','RowCommand'):
            bindings = instances[('$slot:'+key,kind)]['bindings']
            assert bindings['SourceField'] == {'target':'$member:'+key+'-source.Reference','mode':'source'}
            assert bindings['LastApplied']['target'] == '$member:'+key+'-last-applied.Value'
            assert bindings['Provider']['target'] == '$member:'+key+'-provider.Reference'
        command = instances[('$slot:'+key,'RowCommand')]['bindings']
        assert command['Original']['target'] == '$member:'+key+'-original.Value'
        assert components[key+'-original']['initialFields']['Value'] == [row.color[k] for k in ('r','g','b','a')]
        assert components[key+'-selected']['fields']['Drives'] == []
        assert components[key+'-visible']['fields']['Drives'] == ['$slot-member:'+key+'-view.IsActive']


def test_fixture_attachment_preserves_material_and_refuses_another_panel():
    # S: a placement-only adjustment must not reset a committed color or attach
    # the test avatar to a different trial panel. Keep the original document too.
    document = slim_demo.fixture('sample-a')
    document['children'][0]['components'][0]['initialFields']['EmissionColor'] = [7,.5,2,.23]
    original = deepcopy(document)
    attached = slim_demo.attached_fixture(document,'ResoLoop_Test_EmissionEditor_Slim_sample-a')
    assert attached['slot']['parent'] == 'Root/ResoLoop_Test_EmissionEditor_Slim_sample-a'
    assert attached['ownership'] == original['ownership']
    assert attached['children'][0] == original['children'][0]
    for before,after in zip(original['children'][1:],attached['children'][1:]):
        assert before['components'] == after['components']
        assert before['slot']['key'] == after['slot']['key']
    assert document == original
    with pytest.raises(ValueError,match='fixture-panel-identity-mismatch'):
        slim_demo.attached_fixture(document,'ResoLoop_Test_EmissionEditor_Slim_sample-b')
