"""Explicit new-fixture/new-panel demo; never registers or edits an existing avatar.

Run `prepare` once into a NEW directory; subsequent `observe` is read-only.
The deployed native panel continues working without this process.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

from . import slim, ui
from .model import Avatar, MaterialRow, MaterialUse, RegistryPlan

CODE_ROOT = Path(__file__).resolve().parents[2]


def _save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


class Demo:
    def __init__(self, directory, url, library):
        self.directory = Path(directory).resolve()
        self.url, self.library = url, str(Path(library).resolve())
        self.cli = shutil.which('resoloop')
        if self.cli is None:
            raise ValueError('resoloop-not-found')

    def run(self, *args):
        calls = self.directory/'calls'
        calls.mkdir(exist_ok=True)
        number = len(list(calls.glob('*.request.json'))) + 1
        prefix = calls/f'{number:03d}'
        argv = [self.cli,*map(str,args),'--url',self.url,'--timeout','10','--command-timeout','300','--json']
        _save(prefix.with_suffix('.request.json'), argv)
        # CLI owns its deadline; no outer timeout/automatic mutation resend.
        process = subprocess.run(argv,capture_output=True,cwd=self.directory)
        prefix.with_suffix('.stdout.bin').write_bytes(process.stdout)
        prefix.with_suffix('.stderr.bin').write_bytes(process.stderr)
        _save(prefix.with_suffix('.exit.json'), {'exitCode':process.returncode})
        response = json.loads(process.stdout or process.stderr)
        if process.returncode != 0 or response.get('ok') is not True:
            raise RuntimeError(f'CLI failed; inspect {prefix}.stdout.bin: ' + str(response.get('error')))
        return response['data']

    def component(self, state, key):
        return self.run('component','inspect','$component:'+key,'--state',state)

    def set(self, state, key, member, value):
        raw = value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        return self.run('component','set','$component:'+key,member,raw,'--state',state)


def fixture(token):
    emission_map = ui.node('demo-emission-map-slot',components=[ui.component('demo-emission-map',
        ui.F+'SolidColorTexture',{'Color':[1.,1.,1.,1.],'Size':[1,1],'Mipmaps':False})])
    material = ui.node('demo-material-slot',components=[ui.component('demo-material',
        ui.F+'XiexeToonMaterial',{'EmissionMap':'$component:demo-emission-map'},
        initial={'EmissionColor':[2.,1.,1.,.37]})])
    meshes = [ui.node('demo-mesh-'+str(i),position=[i*.4,0,0],components=[
        ui.component('demo-box-'+str(i),ui.F+'BoxMesh',{'Size':[.3,.3,.3]}),
        ui.component('demo-renderer-'+str(i),ui.F+'MeshRenderer',{
            'Mesh':'$component:demo-box-'+str(i),'Materials':['$component:demo-material']}),
    ]) for i in range(2)]
    return {'schemaVersion':'1','ownership':{'key':'emission-slim-fixture-'+token},
        'slot':{'key':'demo-root','name':'ResoLoop_Test_EmissionEditor_SlimFixture_'+token,
                'parent':'Root','position':[-.65,1.25,1.5]},
        'components':[ui.component('demo-objectroot',ui.F+'ObjectRoot'),
                      ui.component('demo-avatar',ui.F+'CommonAvatar.AvatarRoot')],
        'children':[material,emission_map,*meshes]}


def attached_fixture(document, panel_name):
    """Keep both samples beside their matching panel without touching material data."""
    prefix = 'emission-slim-fixture-'
    owner = document['ownership']['key']
    if not owner.startswith(prefix) or panel_name != 'ResoLoop_Test_EmissionEditor_Slim_'+owner[len(prefix):]:
        raise ValueError('fixture-panel-identity-mismatch')
    result = deepcopy(document)
    result['slot'].update(parent='Root/'+panel_name,position=[1.1,-.05,-.15],relocationTransform='local')
    for i in range(2):
        mesh = next(n for n in result['children'] if n['slot']['key']=='demo-mesh-'+str(i))
        mesh['slot']['position'] = [0,-.4*i,0]
    return result


def _attach_fixture(demo, root, declaration, panel):
    state = root/'fixture-state.json'
    original_state = json.loads(state.read_bytes())
    before = demo.component(state,'demo-material')['members']['EmissionColor']['value']
    path = root/'fixture-attached.json'
    _save(path,attached_fixture(declaration,panel['slot']['name']))
    plan = demo.run('diff',path,'--state',state)
    if (plan['creates'] or plan['deletes'] or any(op['kind']!='slot' or op['key'] not in ('demo-root','demo-mesh-1') for op in plan['changes'])):
        raise ValueError('fixture-placement-unexpected-plan')
    demo.run('apply',path,'--state',state)
    current_state = json.loads(state.read_bytes())
    for kind in ('slots','components'):
        if {k:v['id'] for k,v in original_state[kind].items()} != {k:v['id'] for k,v in current_state[kind].items()}:
            raise ValueError('fixture-placement-changed-identities')
    if demo.component(state,'demo-material')['members']['EmissionColor']['value'] != before:
        raise ValueError('fixture-placement-changed-color')


def prepare(directory, url, library):
    root = Path(directory).absolute()
    if root.resolve().is_relative_to(CODE_ROOT):
        raise ValueError('demo-output-must-be-outside-checkout')
    root.mkdir(parents=True,exist_ok=False)
    demo = Demo(root,url,library)
    doctor = demo.run('doctor','--library-path',demo.library)
    if doctor.get('ready') is not True:
        raise ValueError('doctor-not-ready')
    discovery = demo.run('discover')
    sessions = [s for s in discovery['sessions'] if s['url'].rstrip('/') == url.rstrip('/')]
    if len(sessions) != 1:
        raise ValueError('specified-session-not-unique')
    token = uuid4().hex[:8]
    declaration = fixture(token)
    fixture_path, fixture_state = root/'fixture.json',root/'fixture-state.json'
    _save(fixture_path,declaration)
    demo.run('apply',fixture_path,'--state',fixture_state)
    saved = json.loads(fixture_state.read_bytes())
    if saved['ownershipKey'] != declaration['ownership']['key']:
        raise ValueError('fixture-state-ownership-mismatch')
    material = demo.component(fixture_state,'demo-material')
    color = material['members']['EmissionColor']
    avatar = Avatar(saved['slots']['demo-root']['id'],'発光テスト（試験用）',
        tuple(saved['slots']['demo-mesh-'+str(i)]['id'] for i in range(2)))
    uses = []
    for i in range(2):
        renderer = demo.component(fixture_state,'demo-renderer-'+str(i))
        members = renderer['members']['Materials']
        element = members['elements'][0]
        if element['targetId'] != material['id']:
            raise ValueError('fixture-renderer-material-mismatch')
        uses.append(MaterialUse(avatar.slot_refs[i],'共有メッシュ'+str(i+1),renderer['id'],0,
                                element['id'],material['id'],members['id']))
    row = MaterialRow(material['id'],'試験用の共有発光材',color['id'],color['value'],tuple(uses),(avatar,))
    plan = RegistryPlan(sessions[0]['sessionId'],token,datetime.now(UTC).isoformat(),(avatar,),(row,),True,())
    _save(root/'registry-plan.json',asdict(plan))
    template = json.loads((CODE_ROOT/'content/emission-editor/registry.json').read_bytes())
    template['children'] = [n for n in template['children'] if n['slot']['key'] != 'fixture-root']
    _save(root/'template.json',template)
    panel = slim.render_panel(plan,template=root/'template.json')
    panel['ownership']['key'] = 'emission-slim-panel-'+token
    panel['slot'].update(name='ResoLoop_Test_EmissionEditor_Slim_'+token,position=[0,1.5,1.5])
    panel_path, panel_state = root/'panel.json',root/'panel-state.json'
    _save(panel_path,panel)
    demo.run('apply',panel_path,'--state',panel_state)
    saved_panel = json.loads(panel_state.read_bytes())
    if saved_panel['ownershipKey'] != panel['ownership']['key']:
        raise ValueError('panel-state-ownership-mismatch')
    _attach_fixture(demo,root,declaration,panel)
    return _populate_and_deploy(root,demo,plan,saved,saved_panel,panel)


def _populate_and_deploy(root,demo,plan,saved,saved_panel,panel):
    """Internal continuation after successful fresh fixture/panel creation."""
    token = plan.generation
    panel_state = root/'panel-state.json'
    avatar, row = plan.avatars[0], plan.rows[0]
    uses = row.uses
    key = 'reg-'+token+'-row-0'
    # Only populate this NEW panel, with references read from this NEW fixture.
    values = [('reg-'+token+'-avatar-0','Reference',avatar.ref),
              (key+'-material','Reference',row.material_ref), (key+'-source','Reference',row.field_ref),
              (key+'-current-copy','Source',row.field_ref), (key+'-provider','Reference',row.material_ref)]
    for i,use in enumerate(uses):
        values += [(key+f'-mesh-{i}','Reference',use.mesh_ref),
                   (key+f'-renderer-{i}','Reference',use.renderer_ref),
                   (key+f'-binding-{i}','Reference',use.binding_ref),
                   (key+f'-list-{i}','Reference',use.list_ref),(key+f'-index-{i}','Value',use.material_index)]
    for target,member,value in values:
        demo.set(panel_state,target,member,value)
    bundle = slim.session_manifest(plan,world_state=panel_state,deploy_state=root/'flux-state.json',
                                  source_directory=root/'sources',source_root=CODE_ROOT/'flux/emission-editor')
    _save(root/'bundle.json',bundle)
    for i,manifest in enumerate(bundle['manifests']):
        path = root/f'module-{i:02d}.json'
        _save(path,manifest)
        demo.run('flux','validate-manifest',path,'--library-path',demo.library)
        result = demo.run('flux','deploy-manifest',path,'--library-path',demo.library)
        if result.get('success') is not True:
            raise ValueError('native-module-deployment-failed')
        print('deployed '+manifest['modules'][0]['module'],flush=True)
    for target,value in [('registry-session',plan.session),('registry-generation',token),('registry-active',token),
                          ('reg-'+token+'-row-count',1),('registry-ready',True),('ui-runtime-ready',True)]:
        demo.set(panel_state,target,'Value',value)
    summary = {'url':demo.url,'library':demo.library,'generation':token,
               'panelRoot':saved_panel['slots']['emission-root']['id'],
               'fixtureRoot':saved['slots']['demo-root']['id'],
               'panelName':panel['slot']['name'],'before':row.color,
               'ready':True,'manualOperationVerified':False}
    _save(root/'deployment.json',summary)
    return summary


def observe(directory):
    root = Path(directory).resolve()
    summary = json.loads((root/'deployment.json').read_bytes())
    demo = Demo(root,summary['url'],summary['library'])
    panel_state, fixture_state = root/'panel-state.json',root/'fixture-state.json'
    key = 'reg-'+summary['generation']+'-row-0'
    output = {'material':demo.component(fixture_state,'demo-material')['members']['EmissionColor']}
    for name in ('ui-status','ui-operator','ui-selected-root','ui-edit-epoch','ui-intent',
                 'registry-preview-active',key+'-original',key+'-last-applied',key+'-result',key+'-valid'):
        members = demo.component(panel_state,name)['members']
        output[name] = members.get('Value',members.get('Reference'))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    command = commands.add_parser('prepare')
    command.add_argument('--directory',required=True,type=Path)
    command.add_argument('--url',required=True)
    command.add_argument('--library-path',required=True,type=Path)
    command = commands.add_parser('observe')
    command.add_argument('--directory',required=True,type=Path)
    args = parser.parse_args()
    result = (prepare(args.directory,args.url,args.library_path) if args.command=='prepare' else observe(args.directory))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
