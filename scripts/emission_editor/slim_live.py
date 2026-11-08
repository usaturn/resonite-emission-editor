"""Single-user standalone registration. Capture reads the explicit avatar only.

Registration creates a NEW owned panel; it never changes an avatar. Native UI
writes only after the user starts editing and selects a registered avatar.
"""
import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from uuid import uuid4

from . import slim, ui
from .model import Avatar, Material, MaterialUse, RegistrationCapture, plan_registry
from .resoloop_io import flatten
from .slim_demo import CODE_ROOT, Demo, _save, _attach_fixture, _populate_and_deploy, fixture

RENDERERS = {ui.F+'MeshRenderer',ui.F+'SkinnedMeshRenderer'}


def session(client):
    sessions = [s for s in client.run('discover')['sessions']
                if s['url'].rstrip('/')==client.url.rstrip('/')]
    if len(sessions)!=1:
        raise ValueError('specified-session-not-unique')
    return sessions[0]['sessionId']


def capture_avatar(client, avatar, *, generation=None):
    """Read selected hierarchy/renderer lists/referenced materials, no world scan."""
    current_session = session(client)
    tree = client.run('hierarchy','--under',avatar,'--depth','64','--summary','--include-components')
    slots = flatten(tree)
    if avatar.casefold().startswith('reso_') and tree['id'].casefold()!=avatar.casefold():
        raise ValueError('avatar-selector-mismatch')
    if tree['id']=='Root' or any(c['type']==ui.F+'UserRoot' for n,_ in slots.values() for c in n.get('components',[])):
        raise ValueError('world-or-user-root-is-not-an-avatar')
    if not any(c['type']==ui.F+'CommonAvatar.AvatarRoot' for c in tree.get('components',[])):
        raise ValueError('explicit-root-missing-AvatarRoot')
    if len(slots)>50000 or any(n.get('isReferenceOnly') for n,_ in slots.values()):
        raise ValueError('avatar-hierarchy-incomplete')
    target = Avatar(tree['id'],tree['name'],tuple(slots))
    uses = []
    for ref,(node,_) in slots.items():
        for comp in node.get('components',[]):
            if comp['type'] not in RENDERERS:
                continue
            data = client.run('component','inspect',comp['id'])
            if data['id']!=comp['id'] or data['type']!=comp['type']:
                raise ValueError('renderer-identity-changed')
            materials = data['members']['Materials']
            if materials['kind']!='list' or not materials.get('id') or not isinstance(materials.get('elements'),list):
                raise ValueError('unresolved-material-list')
            for index,entry in enumerate(materials['elements']):
                if entry.get('kind')!='reference' or not entry.get('id'):
                    raise ValueError('unresolved-material-entry')
                if entry.get('targetId'):
                    uses.append(MaterialUse(ref,node['name'],comp['id'],index,entry['id'],entry['targetId'],materials['id']))
    warnings = ['外部共有は未走査です。共有材質の変更はアバター外にも影響する場合があります。',
                'MeshRenderer / SkinnedMeshRenderer の公開colorX EmissionColorが対象です。']
    materials = []
    for ref in dict.fromkeys(u.material_ref for u in uses):
        data = client.run('component','inspect',ref)
        if data['id']!=ref:
            raise ValueError('material-identity-changed')
        field = data.get('members',{}).get('EmissionColor')
        if not field or field.get('kind')!='field' or field.get('type')!='ResoniteLink.colorX':
            warnings.append('公開colorX EmissionColor非対応の材質を除外: '+ref)
            continue
        color = field.get('value')
        if (not field.get('id') or not isinstance(color,dict) or set(color)!={'r','g','b','a','profile'}
            or not isinstance(color['profile'],str) or not color['profile']
            or any(type(color[k]) not in (int,float) or not math.isfinite(color[k]) for k in ('r','g','b','a'))):
            raise ValueError('incomplete-emission-color:'+ref)
        first = next(u for u in uses if u.material_ref==ref)
        name = first.mesh_name+' ['+str(first.material_index)+'] '+data['type'].split('.')[-1]
        materials.append(Material(ref,name,field['id'],dict(color)))
        if data['type']==ui.F+'XiexeToonMaterial' and not data['members'].get('EmissionMap',{}).get('targetId'):
            warnings.append('EmissionMap未設定のためXiexeの見た目は発光しません（自動変更しません）: '+ref)
    refs = {m.ref for m in materials}
    capture = RegistrationCapture(current_session,generation or uuid4().hex[:8],datetime.now(UTC).isoformat(),
        (target,),tuple(materials),tuple(u for u in uses if u.material_ref in refs),True,())
    plan = plan_registry(capture)
    if not plan.complete or not plan.rows or len(plan.rows)>256:
        raise ValueError('registration-incomplete-or-empty:'+','.join(plan.issues))
    if session(client)!=current_session:
        raise ValueError('session-changed')
    return plan,warnings


def verify_sources(client, plan):
    """Recheck the actual field IDs and known list entries before enabling UI."""
    if session(client)!=plan.session:
        raise ValueError('session-changed')
    renderers = {}
    for row in plan.rows:
        current = client.run('component','inspect',row.material_ref)
        field = current.get('members',{}).get('EmissionColor',{})
        if current['id']!=row.material_ref or field.get('id')!=row.field_ref or field.get('type')!='ResoniteLink.colorX':
            raise ValueError('source-field-changed')
        for use in row.uses:
            if use.renderer_ref not in renderers:
                renderers[use.renderer_ref] = client.run('component','inspect',use.renderer_ref)
            listing = renderers[use.renderer_ref]['members']['Materials']
            entry = listing['elements'][use.material_index]
            if listing['id']!=use.list_ref or entry['id']!=use.binding_ref or entry.get('targetId')!=row.material_ref:
                raise ValueError('known-material-binding-changed')


def new_directory(directory, url, library):
    root = Path(directory).absolute()
    if root.resolve().is_relative_to(CODE_ROOT):
        raise ValueError('output-must-be-outside-checkout')
    root.mkdir(parents=True,exist_ok=False)
    client = Demo(root,url,library)
    if not client.run('doctor','--library-path',client.library).get('ready'):
        raise ValueError('doctor-not-ready')
    return root,client


def create_panel(root, client, plan, warnings, position, *, saved_fixture=None, declaration=None):
    _save(root/'registry-plan.json',asdict(plan))
    _save(root/'warnings.json',warnings)
    template = json.loads((CODE_ROOT/'content/emission-editor/registry.json').read_bytes())
    template['children'] = [n for n in template['children'] if n['slot']['key']!='fixture-root']
    _save(root/'template.json',template)
    panel = slim.render_panel(plan,template=root/'template.json')
    panel['ownership']['key'] = 'emission-slim-panel-'+plan.generation
    panel['slot'].update(name='ResoLoop_Test_EmissionEditor_Slim_'+plan.generation,position=position)
    title = ('複数行確認・新パネル' if declaration else 'アバター発光エディター')+' ['+plan.generation+']'
    slim._components(panel)['ui-title-text']['fields']['Content'] = title
    _save(root/'panel.json',panel)
    state = root/'panel-state.json'
    client.run('apply',root/'panel.json','--state',state)
    saved_panel = json.loads(state.read_bytes())
    if saved_panel['ownershipKey']!=panel['ownership']['key']:
        raise ValueError('new-panel-ownership-mismatch')
    if declaration:
        _attach_fixture(client,root,declaration,panel)
    return _populate_and_deploy(root,client,plan,saved_fixture,saved_panel,panel,
        verify_sources=lambda:verify_sources(client,plan))


def register(directory,url,library,avatar,position):
    root,client = new_directory(directory,url,library)
    plan,warnings = capture_avatar(client,avatar)
    return create_panel(root,client,plan,warnings,position)


def demo(directory,url,library,position):
    root,client = new_directory(directory,url,library)
    token = uuid4().hex[:8]
    declaration = fixture(token,material_count=2)
    declaration['slot']['name'] = '発光テスト（2材質・共有メッシュ）['+token+']'
    _save(root/'fixture.json',declaration)
    state = root/'fixture-state.json'
    client.run('apply',root/'fixture.json','--state',state)
    saved = json.loads(state.read_bytes())
    if saved['ownershipKey']!=declaration['ownership']['key']:
        raise ValueError('new-fixture-ownership-mismatch')
    plan,warnings = capture_avatar(client,saved['slots']['demo-root']['id'],generation=token)
    return create_panel(root,client,plan,warnings,position,saved_fixture=saved,declaration=declaration)


def observe(directory):
    root = Path(directory).resolve();summary = json.loads((root/'deployment.json').read_bytes())
    client = Demo(root,summary['url'],summary['library'])
    if session(client)!=summary['session']:
        raise ValueError('session-changed-register-again')
    output = {'generation':summary['generation'],'rows':[]}
    suffixes = ('original','last-applied','valid','result','snapshot-epoch','visible','preview-written','row-pending','op-stage')
    globals_ = ('ui-status','ui-operator','ui-selected-root','ui-edit-epoch','ui-intent','registry-preview-active','registry-pending','registry-operation-stage')
    keys = [f'reg-{summary["generation"]}-row-{i}-{s}' for i in range(len(summary['rows'])) for s in suffixes]
    keys += list(globals_)
    state = json.loads((root/'panel-state.json').read_bytes())
    selectors = []
    for key in keys:
        member = 'Reference' if state['components'][key]['type'].startswith(ui.F+'ReferenceField<') else 'Value'
        selectors.append('$member:'+key+'.'+member)
    fields = {}
    for start in range(0,len(selectors),64):
        fields.update(client.run('observe',*selectors[start:start+64],'--state',root/'panel-state.json')['values'])
    values = {key:fields[selector]['member'] for key,selector in zip(keys,selectors)}
    for i,row in enumerate(summary['rows']):
        data = client.run('component','inspect',row['material_ref'])
        field = data['members']['EmissionColor']
        if field['id']!=row['field_ref']:
            raise ValueError('source-field-changed')
        key = 'reg-'+summary['generation']+'-row-'+str(i)
        observed = {'material':field}
        for suffix in suffixes:
            observed[suffix]=values[key+'-'+suffix]
        output['rows'].append(observed)
    for key in globals_:
        output[key]=values[key]
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    for name in ('register','demo'):
        p = commands.add_parser(name)
        p.add_argument('--directory',type=Path,required=True)
        p.add_argument('--url',required=True)
        p.add_argument('--library-path',type=Path,required=True)
        p.add_argument('--position',nargs=3,type=float,default=[0,1.5,1.5])
        if name=='register':p.add_argument('--avatar',required=True,help='Explicit AvatarRoot slot ID or path; never auto-picked')
    p=commands.add_parser('observe');p.add_argument('--directory',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='observe':result=observe(args.directory)
    elif args.command=='register':result=register(args.directory,args.url,args.library_path,args.avatar,args.position)
    else:result=demo(args.directory,args.url,args.library_path,args.position)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
