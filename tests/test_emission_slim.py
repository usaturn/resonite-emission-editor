"""S: two valid same-typed rows must never preview/restore each other's material."""
from pathlib import Path

from scripts.emission_editor import slim
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
