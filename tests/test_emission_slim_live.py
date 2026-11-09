"""S: explicit capture must not select the world, swap same-named providers or mutate avatars."""
from copy import deepcopy

import pytest

from scripts.emission_editor import slim_live, ui


class ReadOnlyClient:
    url = 'ws://example.invalid:16070'

    def __init__(self):
        self.tree = {'id':'avatar','name':'日本語アバター','components':[
            {'id':'avatar-marker','type':ui.F+'CommonAvatar.AvatarRoot'}],
            'children':[{'id':'mesh-'+str(i),'name':'同名メッシュ','components':[
                {'id':'renderer-'+str(i),'type':ui.F+'MeshRenderer'}],'children':[]} for i in range(2)]}
        self.components = {}
        for i,refs in enumerate((('material-a','material-b'),('material-a',None))):
            self.components['renderer-'+str(i)] = {'id':'renderer-'+str(i),'type':ui.F+'MeshRenderer',
                'members':{'Materials':{'kind':'list','id':'list-'+str(i),'elements':[
                    {'kind':'reference','id':f'binding-{i}-{j}',**({'targetId':ref} if ref else {})}
                    for j,ref in enumerate(refs)]}}}
        for i,name in enumerate(('a','b')):
            self.components['material-'+name] = {'id':'material-'+name,'type':ui.F+'XiexeToonMaterial',
                'members':{'EmissionColor':{'kind':'field','type':'ResoniteLink.colorX','id':'field-'+name,
                    'value':{'r':2.5+i,'g':.5,'b':1.,'a':.37+i*.2,'profile':'sRGB' if i==0 else 'Linear'}}}}

    def run(self,*args):
        # Any write/deploy from capture fails even on the accepted input.
        if args[0]=='discover':return {'sessions':[{'url':self.url,'sessionId':'session'}]}
        if args[0]=='hierarchy':return deepcopy(self.tree)
        if args[:2]==('component','inspect'):return deepcopy(self.components[args[2]])
        raise AssertionError('capture attempted mutation: '+str(args))


def test_scoped_capture_uses_reference_identity_and_preserves_full_hdr_color():
    client = ReadOnlyClient()
    before = deepcopy(client.components)
    plan,_ = slim_live.capture_avatar(client,'avatar')
    rows = {r.material_ref:r for r in plan.rows}
    assert set(rows)=={'material-a','material-b'}
    assert rows['material-a'].field_ref=='field-a' and rows['material-b'].field_ref=='field-b'
    assert [(u.renderer_ref,u.material_index,u.list_ref) for u in rows['material-a'].uses]==[
        ('renderer-0',0,'list-0'),('renderer-1',0,'list-1')]
    assert [(u.renderer_ref,u.material_index) for u in rows['material-b'].uses]==[('renderer-0',1)]
    for row in plan.rows:
        assert row.color==before[row.material_ref]['members']['EmissionColor']['value']
    assert client.components==before
    assert plan.avatars[0].slot_refs==('avatar','mesh-0','mesh-1')


def test_capture_refuses_world_even_if_avatar_marker_was_attached_there():
    client = ReadOnlyClient()
    client.tree['id']='Root'
    with pytest.raises(ValueError,match='world-or-user-root'):
        slim_live.capture_avatar(client,'Root')
    # The positive case in the other test rules out rejecting every capture.


def test_capture_refuses_a_different_valid_avatar_than_the_explicit_id():
    # S: another valid-looking AvatarRoot must not silently replace the user's target.
    client = ReadOnlyClient()
    with pytest.raises(ValueError,match='avatar-selector-mismatch'):
        slim_live.capture_avatar(client,'Reso_123')
