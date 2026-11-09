"""Standalone editing assembly for the approved single-user Issue #3 scope.

Uses existing native bodies; no running Python process is needed after deploy.
Only prepares declarations/manifests. Public live registration remains separate.
"""
from pathlib import Path
import re
import shutil

from . import ui


EDITING_SOURCES = {
    'Session.pg': 'staged-s6/Session.pg',
    'Selection.pg': 'staged-s6/Selection.pg',
    'RowPresentation.pg': 'staged-s6/RowPresentation.pg',
    'BeginRow.pg': 'current/BeginRow.pg',
    'RowPreview.pg': 'current/RowPreview.pg',
    'RowCommand.pg': 'current/RowCommand.pg',
    'Commit.pg': 'current/Commit.pg',
}


def _ref(key, member='Value'):
    return {'target': '$member:' + key + '.' + member, 'mode': 'source'}


def _components(tree):
    result = {c['key']: c for c in tree.get('components', [])}
    for child in tree.get('children', []):
        result.update(_components(child))
    return result


def render_panel(registry, *, template):
    """Add command-owned visibility/accept fields without changing saved originals."""
    document = ui.render_panel(registry, template=template)
    registry_root = next(n for n in document['children'] if n['slot']['key'] == 'registry-root')
    generation = next(n for n in registry_root['children'] if n['slot']['key'] == 'reg-' + registry.generation)
    components = _components(document)
    components['ui-title-text']['fields']['Content'] = '動作確認用・新パネル [' + registry.generation[:8] + ']'
    components['ui-subtitle-text']['fields']['Content'] = 'RGB入力でライブ反映 · OK/キャンセル後は再選択 · 共有材は外部にも影響（未走査）'
    acquire = components['ui-acquire-label-text']
    acquire['initialFields'] = {'Content':acquire['fields'].pop('Content')}
    for i, row in enumerate(registry.rows):
        key = f'reg-{registry.generation}-row-{i}'
        record = next(n for n in generation['children'] if n['slot']['key'] == key)
        # RowView still supplies Selected to preview/command. RowCommand is the
        # sole view visibility driver, including a pending unselected row.
        components[key + '-selected']['fields']['Drives'] = []
        record['children'] += [
            ui.node(key + '-visible-slot', components=[ui.component(key + '-visible',
                ui.F + 'ValueMultiDriver<bool>', {'Drives':['$slot-member:' + key + '-view.IsActive']}, {'Value':False})]),
            ui.state(key + '-can-accept', 'bool', False),
            # Recovery controls are not exposed in the minimum single-user UI;
            # the existing native handler still needs a concrete button input.
            ui.node(key + '-accept-slot', components=[ui.component(key + '-accept-button',
                ui.U + 'Button', initial={'Enabled':False})]),
        ]
        # RowPreview owns confirmation by snapshot epoch; the old toggle must
        # not also write its driven Confirmed field.
        toggle = key + '-confirm-toggle'
        def remove_toggle(node):
            node['components'] = [c for c in node.get('components', []) if c['key'] != toggle]
            for child in node.get('children', []):
                remove_toggle(child)
        remove_toggle(document)
    return document


def reference_values(registry):
    """Bind every row to its own captured Source/provider/uses, never row zero."""
    if not registry.complete or not registry.rows:
        raise ValueError('incomplete-or-empty-registration')
    prefix = 'reg-'+registry.generation
    values = [(f'{prefix}-avatar-{i}','Reference',a.ref) for i,a in enumerate(registry.avatars)]
    for i,row in enumerate(registry.rows):
        key = f'{prefix}-row-{i}'
        values += [(key+'-material','Reference',row.material_ref),
                   (key+'-source','Reference',row.field_ref),
                   (key+'-current-copy','Source',row.field_ref),
                   (key+'-provider','Reference',row.material_ref)]
        for j,use in enumerate(row.uses):
            if use.material_ref != row.material_ref or not use.list_ref:
                raise ValueError('unresolved-row-use')
            values += [(key+f'-mesh-{j}','Reference',use.mesh_ref),
                       (key+f'-renderer-{j}','Reference',use.renderer_ref),
                       (key+f'-binding-{j}','Reference',use.binding_ref),
                       (key+f'-list-{j}','Reference',use.list_ref),
                       (key+f'-index-{j}','Value',use.material_index)]
    return values


def _globals(generation):
    values = {
        'Operator':('ui-operator','Reference'), 'OperatorGuard':('registry-operator','Value'),
        'Ready':('registry-ready','Value'), 'UiReady':('ui-runtime-ready','Value'),
        'Busy':('registry-busy','Value'), 'RecoveryPending':('registry-pending','Value'),
        'PreviewActive':('registry-preview-active','Value'), 'OperationStage':('registry-operation-stage','Value'),
        'EditEpoch':('ui-edit-epoch','Value'), 'Intent':('ui-intent','Value'),
        'CommandSeq':('ui-command-seq','Value'), 'CommandKind':('ui-command-kind','Value'),
        'CommandUser':('ui-command-user','Reference'), 'AckRemaining':('ui-ack-remaining','Value'),
        'AckWait':('ui-ack-wait','Value'), 'AckRefuse':('ui-ack-refuse','Value'), 'AckFail':('ui-ack-fail','Value'),
        'SelectedRoot':('ui-selected-root','Reference'), 'NullRoot':('ui-null-root','Reference'),
        'NullUser':('ui-null-user','Reference'), 'RecoverTarget':('ui-recover-target','Reference'),
        'Status':('ui-status','Value'), 'RowCount':(f'reg-{generation}-row-count','Value'),
        'ActiveGeneration':(f'reg-{generation}-visible','Value'),
        'CanOk':('ui-ok-button','Enabled'), 'CanRestore':('ui-restore-button','Enabled'),
        'CanCancel':('ui-cancel-button','Enabled'), 'AcquireLabel':('ui-acquire-label-text','Content'),
    }
    result = {name:_ref(key,member) for name,(key,member) in values.items()}
    for name, key in (('OkButton','ui-ok-button'),('RestoreButton','ui-restore-button'),('CancelButton','ui-cancel-button')):
        result[name] = {'target':'$component:' + key,'mode':'source'}
    return result


def _row(registry, index):
    key = f'reg-{registry.generation}-row-{index}'
    suffixes = {
        'Selected':'selected', 'SnapshotEpoch':'snapshot-epoch', 'External':'external',
        'Valid':'valid', 'Draft':'draft', 'Original':'original', 'LastApplied':'last-applied',
        'PreviewWritten':'preview-written', 'RowPending':'row-pending', 'InputSeq':'input-seq',
        'AppliedSeq':'applied-seq', 'Result':'result', 'ResultSeq':'result-seq',
        'OpStage':'op-stage', 'OpKind':'op-kind', 'OpSeq':'op-seq', 'OpId':'op-id',
        'PendingPrevious':'pending-previous', 'PendingCandidate':'pending-candidate',
        'AckSeq':'ack-seq', 'ConfirmedEpoch':'external-confirmed-epoch',
        'Confirmed':'external-confirmed', 'CanAccept':'can-accept', 'Visible':'visible',
    }
    result = {name:_ref(key + '-' + suffix) for name,suffix in suffixes.items()}
    result.update(ui.row_guard_bindings(registry,index))
    result['SourceField'] = _ref(key + '-source','Reference')
    for letter in 'rgb':
        result['Input' + letter.upper()] = _ref(key + '-' + letter + '-value-text','Content')
    for name,suffix in (('ConfirmButton','confirm-button'),('AcceptButton','accept-button')):
        result[name] = {'target':'$component:' + key + '-' + suffix,'mode':'source'}
    return result


def _bindings(source, available):
    """Select explicit known selectors for the actual chosen native body's ports."""
    result = {}
    for line in source.read_text().splitlines():
        match = re.match(r'^(in|out) (\w+):', line)
        if match:
            direction,name = match.groups()
            result[name] = {**available[name], 'mode':'drive' if direction == 'out' else 'source'}
    return result


def session_manifest(registry, *, world_state, deploy_state, source_directory, source_root):
    """Extend existing UI bundle with Preview/Command/Commit under exact row parents."""
    bundle = ui.session_manifest(registry, world_state=world_state, deploy_state=deploy_state,
        source_directory=source_directory, source_root=source_root)
    manifests = bundle['manifests']
    stage, base = Path(source_directory).resolve(), Path(source_root).resolve()
    global_bindings = _globals(registry.generation)
    for manifest in manifests:
        module = manifest['modules'][0]
        filename = Path(module['source']).name
        if filename in EDITING_SOURCES:
            shutil.copy2(base/EDITING_SOURCES[filename], stage/filename)
        available = {**global_bindings, **module['bindings']}
        if '-row-' in module['name']:
            index = int(module['name'].split('-row-')[1].split('-')[0])
            available.update(_row(registry,index))
        module['bindings'] = _bindings(Path(module['source']), available)
    # Each new instance has its own stable parent and deploy checkpoint, as with
    # the existing bundle. Native module identities never depend on row ordering.
    for i,_ in enumerate(registry.rows):
        key = f'reg-{registry.generation}-row-{i}'
        for kind in ('RowPreview','RowCommand'):
            filename = kind + '.pg'
            shutil.copy2(base/EDITING_SOURCES[filename], stage/filename)
            checkpoint = Path(deploy_state).resolve()
            manifests.append({'schemaVersion':'1','projectDirectory':str(stage),
                'parent':'$slot:' + key,'worldState':str(Path(world_state).resolve()),
                'deployState':str(checkpoint.with_name(checkpoint.stem + '-' + key + checkpoint.suffix)),
                'modules':[{'name':key + '-' + kind.lower(),'source':str(stage/filename),'module':kind,
                    'dependsOn':[], 'bindings':_bindings(stage/filename,{**global_bindings,**_row(registry,i)})}]})
    shutil.copy2(base/EDITING_SOURCES['Commit.pg'], stage/'Commit.pg')
    checkpoint = Path(deploy_state).resolve()
    manifests.append({'schemaVersion':'1','projectDirectory':str(stage),'parent':'$slot:registry-root',
        'worldState':str(Path(world_state).resolve()),
        'deployState':str(checkpoint.with_name(checkpoint.stem + '-registry-root' + checkpoint.suffix)),
        'modules':[{'name':'ui-commit','source':str(stage/'Commit.pg'),'module':'Commit','dependsOn':[],
                    'bindings':_bindings(stage/'Commit.pg',global_bindings)}]})
    ui.validate_runtime_destinations(manifests)
    return bundle
