"""Issue #6: native multi-drop registration panel.

The saved panel contains a fixed row pool. A user drops the avatar Root and then
each material's EmissionColor value field onto a row; registration writes only
panel-owned reference fields. After a row is selected, the captured field is the
editing target for the existing preview/restore/command semantics.

ProtoFlux cannot enumerate components, so this module never claims to discover
renderers or materials. It creates only panel-local declarations; deployment of
the native modules is the same validated/deployed manifest flow as slim.py.
"""
import argparse
import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

from . import slim, ui
from .slim_demo import CODE_ROOT, Demo, _save

POOL_ROWS = 16
ROW_HEIGHT = 200
IVALUE_COLORX = ui.F + 'IValue<colorX>'

# Copied by basename into an isolated compiler stage before deployment.
MODULE_SOURCES = {
    'RootGuard': 'registrable/RootGuard.pg',
    'RowGuard': 'registrable/RowGuard.pg',
    'BeginRow': 'registrable/BeginRow.pg',
    'RowPreview': 'registrable/RowPreview.pg',
    'RowCommand': 'registrable/RowCommand.pg',
    'Session': 'staged-s6/Session.pg',
    'RowValue': 'RowValue.pg',
    'RowPresentation': 'staged-s6/RowPresentation.pg',
    'Commit': 'current/Commit.pg',
}


def _components(tree):
    result = {c['key']: c for c in tree.get('components', [])}
    for child in tree.get('children', []):
        result.update(_components(child))
    return result


def _state(key, typ, initial):
    return ui.state(key, typ, initial)


def _reference(key, typ):
    return ui.reference(key, typ)


def _reference_value(key, typ):
    return ui.node(key + '-slot', components=[ui.component(key, ui.F + 'ReferenceField<' + typ + '>')])


def _field(key):
    return {'target': f'$member:{key}.Reference'}


def _component(key):
    return {'target': f'$component:{key}'}


def _row_nodes(index, height):
    """One row's UIX declaration and panel-owned state fields."""
    p = f'rp-{index}'

    def drive(key, member='Value'):
        return {'target': f'$member:{key}.{member}'}

    view = ui.image(p + '-view', 0, index * height, 1380, height - 8, [0.065, 0.08, 0.10, 1])
    view['components'][0] = ui.component(p + '-view-rect', ui.U + 'RectTransform')
    view['components'].append(ui.component(
        p + '-layout-element', ui.U + 'LayoutElement',
        {'PreferredHeight': height - 8, 'MinHeight': height - 8},
    ))
    view['children'] = [
        ui.label(p + '-name', '', 12, 6, 800, 26, 22, editable=True,
                 anchor='stretch', parent_width=1380),
        ui.label(p + '-registration', '', 820, 6, 548, 26, 17, ui.CYAN, editable=True,
                 anchor='stretch', parent_width=1380),
    ]
    drop = ui.image(p + '-drop', 12, 36, 500, 54, [0.045, 0.11, 0.14, 1], hit=True)
    drop['components'].append(ui.component(
        p + '-drop-receiver', ui.U + 'ReferenceReceiver<' + IVALUE_COLORX + '>',
        {'Reference': f'$member:{p}-dropped.Reference'},
    ))
    drop['components'].append(ui.component(p + '-drop-button', ui.U + 'Button'))
    drop_face = drop['components'][1]
    drop_face['initialFields'] = drop_face.pop('fields')
    drop['children'] = [
        ui.label(p + '-drop-label', 'EmissionColor をここへドロップ', 10, 10, 480, 34, 19, ui.CYAN)
    ]
    view['children'].append(drop)
    view['children'].append(ui.button(p + '-select', 'この行を選択', 520, 36, 200, 54))
    view['children'].append(ui.button(p + '-accept', '受け入れて閉じる', 728, 36, 240, 54))
    view['children'] += [
        ui.label(p + '-original-caption', '元値', 660, 96, 70, 23, 16, parent_width=1380),
        ui.label(p + '-original-label', '未登録', 735, 96, 621, 23, 16, editable=True,
                 anchor='stretch', parent_width=1380),
        ui.label(p + '-current-caption', '現在値', 660, 122, 70, 23, 16, parent_width=1380),
        ui.label(p + '-current-label', '未登録', 735, 122, 621, 23, 16, editable=True,
                 anchor='stretch', parent_width=1380),
        ui.label(p + '-status-label', '未登録', 12, 150, 1356, 28, 16, ui.CYAN,
                 editable=True, anchor='stretch', parent_width=1380),
    ]
    for j, k in enumerate('rgb'):
        view['children'].append(ui.text_input(
            p + '-' + k, '', 12 + j * 216, 96, 205, 48,
        ))

    state = ui.node(p, children=[
        _reference_value(p + '-dropped', IVALUE_COLORX),
        _reference_value(p + '-captured', IVALUE_COLORX),
        _state(p + '-index', 'int', index),
        _state(p + '-label', 'string', f'行 {index + 1:02d}'),
        _state(p + '-original', 'colorX', [0, 0, 0, 1]),
        _state(p + '-last-applied', 'colorX', [0, 0, 0, 1]),
        _state(p + '-draft', 'colorX', [0, 0, 0, 1]),
        _state(p + '-current', 'colorX', [0, 0, 0, 1]),
        _state(p + '-preview-written', 'bool', False),
        _state(p + '-snapshot-epoch', 'int', -1),
        _state(p + '-valid', 'bool', False),
        _state(p + '-dirty', 'bool', False),
        _state(p + '-profile-preserved', 'bool', False),
        _state(p + '-registered', 'bool', False),
        _state(p + '-valid-field', 'bool', False),
        _state(p + '-duplicate', 'bool', False),
        _state(p + '-selected', 'bool', False),
        ui.node(p + '-visible-slot', components=[ui.component(
            p + '-visible', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [f'$slot-member:{p}-view.IsActive']}, {'Value': False},
        )]),
        ui.node(p + '-can-select-slot', components=[ui.component(
            p + '-can-select', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [f'$member:{p}-select-button.Enabled']}, {'Value': False},
        )]),
        ui.node(p + '-can-accept-slot', components=[ui.component(
            p + '-can-accept', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [f'$member:{p}-accept-button.Enabled']}, {'Value': False},
        )]),
        ui.node(p + '-can-edit-slot', components=[ui.component(
            p + '-can-edit', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [
                f'$member:uix-text-input--{p}-{k}-{role}.Enabled'
                for k in 'rgb' for role in ('button', 'input')
            ]}, {'Value': False},
        )]),
        ui.node(p + '-drop-enabled-slot', components=[ui.component(
            p + '-drop-enabled', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [
                f'$member:{p}-drop-receiver.Enabled',
                f'$member:{p}-drop-button.Enabled',
            ]}, {'Value': False},
        )]),
        _state(p + '-op-stage', 'int', 0),
        _state(p + '-op-kind', 'int', 0),
        _state(p + '-op-seq', 'int', 0),
        _state(p + '-op-id', 'int', 0),
        _state(p + '-pending-previous', 'colorX', [0, 0, 0, 1]),
        _state(p + '-pending-candidate', 'colorX', [0, 0, 0, 1]),
        _state(p + '-row-pending', 'bool', False),
        _state(p + '-input-seq', 'int', 0),
        _state(p + '-applied-seq', 'int', 0),
        _state(p + '-result', 'int', 0),
        _state(p + '-result-seq', 'int', 0),
        _state(p + '-ack-seq', 'int', 0),
    ])
    return view, state


def build_panel(*, token, position, rows=POOL_ROWS):
    """Create an unregistered panel declaration. No external references or fixtures."""
    if rows < 1 or rows > POOL_ROWS:
        raise ValueError('row-pool-limit')
    footer = 220 + rows * ROW_HEIGHT
    height = footer + ui.FOOTER_HEIGHT
    assets, canvas = ui.shell(height)
    components = _components(canvas)
    components['ui-subtitle-text']['fields']['Content'] = (
        '材質の EmissionColor を各行へドロップ · PC補助なし · 最大16行')
    components['ui-drop-label-text']['fields']['Content'] = 'アバターの Root をここへドロップ'
    acquire = components['ui-acquire-label-text']
    acquire['initialFields'] = {'Content': acquire['fields'].pop('Content')}
    canvas['children'].append(ui.label(
        'ui-help', '各行へ材質の EmissionColor フィールドをドロップ → 行を選択して RGB を編集',
        30, 196, 1380, 26, 19, ui.CYAN, anchor='stretch'))
    viewport = ui.image(
        'ui-viewport', 30, 224, 1380, rows * ROW_HEIGHT,
        [0.045, 0.055, 0.07, 1], hit=True, anchor='stretch')
    viewport['components'].append(ui.component(
        'ui-viewport-mask', ui.U + 'Mask', {'ShowMaskGraphic': True}))
    content = ui.node('ui-table', components=[
        ui.component('ui-table-rect', ui.U + 'RectTransform',
                     {'AnchorMin': [0, 1], 'AnchorMax': [1, 1], 'Pivot': [0, 1]}),
        ui.component('ui-layout', ui.U + 'VerticalLayout',
                     {'Spacing': 8, 'ForceExpandWidth': True, 'ForceExpandHeight': False}),
        ui.component('ui-fitter', ui.U + 'ContentSizeFitter',
                     {'HorizontalFit': 'Disabled', 'VerticalFit': 'PreferredSize'}),
        ui.component('ui-scroll', ui.U + 'ScrollRect',
                     {'ViewportOverride': '$component:ui-viewport-rect'}),
    ])
    row_states = []
    for i in range(rows):
        view, state = _row_nodes(i, ROW_HEIGHT)
        content['children'].append(view)
        row_states.append(state)
    viewport['children'] = [content]
    canvas['children'].append(viewport)
    canvas['children'].append(ui.label(
        'ui-observed', '登録は参照の保存だけ · 材質名・影響メッシュ・共有先は未走査',
        240, footer + 8, 1170, 68, 18, anchor='stretch'))
    root = ui.node('registry-root', children=[
        _reference('ui-operator', 'User'),
        _reference('ui-selected-root', 'Slot'),
        _reference('ui-pending-root', 'Slot'),
        _reference('ui-null-root', 'Slot'),
        _reference_value('ui-null-element', IVALUE_COLORX),
        _state('ui-edit-epoch', 'int', 0),
        _state('ui-runtime-ready', 'bool', False),
        _state('ui-selected-row', 'int', -1),
        _state('ui-root-ready', 'bool', False),
        _state('ui-root-name', 'string', ''),
        _state('registry-ready', 'bool', False),
        _state('registry-operator', 'string', ''),
        _state('registry-busy', 'bool', False),
        _state('registry-pending', 'bool', False),
        _state('registry-preview-active', 'bool', False),
        _state('registry-operation-stage', 'int', 0),
        _state('ui-row-count', 'int', rows),
        _state('ui-false', 'bool', False),
        ui.node('ui-ready-slot', components=[ui.component(
            'ui-ready', ui.F + 'ValueMultiDriver<bool>',
            {'Drives': [
                '$member:ui-drop-receiver.Enabled',
                '$member:ui-drop-button.Enabled',
            ]}, {'Value': False},
        )]),
        ui.node('ui-status-slot', components=[ui.component(
            'ui-status', ui.F + 'ValueMultiDriver<string>',
            {'Drives': ['$member:ui-status-display-text.Content']},
            {'Value': '「編集を開始」を押してから登録してください'},
        )]),
        *ui.session_command_fields(),
        *row_states,
    ])
    panel = {
        'schemaVersion': '1',
        'ownership': {'key': 'emission-registrable-panel-' + token},
        'slot': {
            'key': 'emission-root',
            'name': 'ResoLoop_Test_EmissionEditor_Registrable_' + token,
            'parent': 'Root',
            'position': position,
        },
        'components': [
            ui.component('emission-objectroot', ui.F + 'ObjectRoot'),
            ui.component('emission-grabbable', ui.F + 'Grabbable'),
        ],
        'children': [assets, canvas, root],
    }
    return panel


def session_manifest(*, rows, world_state, deploy_state, source_directory, source_root):
    """Build validated, per-row-isolated manifests; each row uses its own fields only."""
    if rows < 1 or rows > POOL_ROWS:
        raise ValueError('row-pool-limit')
    stage = Path(source_directory).resolve()
    base = Path(source_root).resolve()
    if stage == base or stage.is_relative_to(base):
        raise ValueError('compiler-stage-inside-authoring-tree')
    stage.mkdir(parents=True, exist_ok=False)
    copies = {}

    def source_for(module):
        if module not in copies:
            source = base / MODULE_SOURCES[module]
            target = stage / source.name
            if not target.exists():
                shutil.copy2(source, target)
            if source.read_bytes() != target.read_bytes():
                raise ValueError('isolated-source-copy-mismatch')
            copies[module] = target
        return copies[module]

    manifests = []

    def add(name, module, parent, available):
        source = source_for(module)
        bindings = slim._bindings(source, {k: dict(v) for k, v in available.items()})
        manifests.append({
            'schemaVersion': '1',
            'projectDirectory': str(stage),
            'parent': '$slot:' + parent,
            'worldState': str(Path(world_state).resolve()),
            'deployState': str(Path(deploy_state).resolve().with_name(
                Path(deploy_state).resolve().stem + '-' + parent + Path(deploy_state).resolve().suffix)),
            'modules': [{'name': name, 'source': str(source), 'module': module,
                         'dependsOn': [], 'bindings': bindings}],
        })

    def src(key, member='Value'):
        return {'target': f'$member:{key}.{member}'}

    def drive(key, member='Value'):
        return {'target': f'$member:{key}.{member}'}

    def sel(component):
        return {'target': f'$component:{component}'}

    def field(key):
        return {'target': f'$member:{key}.Reference'}

    add('ui-session', 'Session', 'registry-root', {
        'AcquireButton': sel('ui-acquire-button'),
        'Operator': field('ui-operator'),
        'OperatorGuard': src('registry-operator'),
        'Ready': src('registry-ready'),
        'UiReady': src('ui-runtime-ready'),
        'Busy': src('registry-busy'),
        'RecoveryPending': src('registry-pending'),
        'PreviewActive': src('registry-preview-active'),
        'OperationStage': src('registry-operation-stage'),
        'Status': src('ui-status'),
        'EditEpoch': src('ui-edit-epoch'),
        'SelectedRoot': field('ui-selected-root'),
        'NullRoot': field('ui-null-root'),
        'CanInteract': drive('ui-ready'),
        'AcquireLabel': drive('ui-acquire-label-text', 'Content'),
    })
    add('ui-root-guard', 'RootGuard', 'registry-root', {
        'AvatarRoot': field('ui-pending-root'),
        'Operator': field('ui-operator'),
        'Status': src('ui-status'),
        'RootReady': drive('ui-root-ready'),
        'RootName': drive('ui-root-name'),
    })

    for i in range(rows):
        p = f'rp-{i}'
        others = {}
        for j in range(POOL_ROWS):
            others[f'Other{j}'] = (
                field(f'rp-{j}-dropped') if j < rows and j != i else field('ui-null-element')
            )
        add(p + '-guard', 'RowGuard', p, {
            'Dropped': field(p + '-dropped'),
            **others,
            'RowIndex': src(p + '-index'),
            'RowLabel': src(p + '-label'),
            'SelectButton': sel(p + '-select-button'),
            'AvatarRoot': field('ui-pending-root'),
            'RootReady': src('ui-root-ready'),
            'Operator': field('ui-operator'),
            'UiReady': src('ui-runtime-ready'),
            'Ready': src('registry-ready'),
            'Busy': src('registry-busy'),
            'Intent': src('ui-intent'),
            'OperationStage': src('registry-operation-stage'),
            'PreviewActive': src('registry-preview-active'),
            'RecoveryPending': src('registry-pending'),
            'SelectedRow': src('ui-selected-row'),
            'SelectedRoot': field('ui-selected-root'),
            'EditEpoch': src('ui-edit-epoch'),
            'Status': src('ui-status'),
            'Result': src(p + '-result'),
            'PreviewWritten': src(p + '-preview-written'),
            'RowPending': src(p + '-row-pending'),
            'OpStage': src(p + '-op-stage'),
            'SnapshotEpoch': src(p + '-snapshot-epoch'),
            'Registered': drive(p + '-registered'),
            'ValidField': drive(p + '-valid-field'),
            'Duplicate': drive(p + '-duplicate'),
            'Selected': drive(p + '-selected'),
            'CanSelect': drive(p + '-can-select'),
            'CanEdit': drive(p + '-can-edit'),
            'DropEnabled': drive(p + '-drop-enabled'),
            'Visible': drive(p + '-visible'),
            'NameText': drive(p + '-name-text', 'Content'),
            'RegistrationStatus': drive(p + '-registration-text', 'Content'),
        })
        add(p + '-begin', 'BeginRow', p, {
            'SourceField': field(p + '-dropped'),
            'CapturedField': field(p + '-captured'),
            'Registered': src(p + '-registered'),
            'Duplicate': src(p + '-duplicate'),
            'ActiveGeneration': src(p + '-registered'),
            'Operator': field('ui-operator'),
            'UiReady': src('ui-runtime-ready'),
            'Selected': src(p + '-selected'),
            'EditEpoch': src('ui-edit-epoch'),
            'SnapshotEpoch': src(p + '-snapshot-epoch'),
            'Original': src(p + '-original'),
            'LastApplied': src(p + '-last-applied'),
            'PreviewWritten': src(p + '-preview-written'),
            'RecoveryPending': src('registry-pending'),
            'OperationStage': src('registry-operation-stage'),
            'OpStage': src(p + '-op-stage'),
            'RowPending': src(p + '-row-pending'),
            'InputR': src(p + '-r-value-text', 'Content'),
            'InputG': src(p + '-g-value-text', 'Content'),
            'InputB': src(p + '-b-value-text', 'Content'),
            'InputSeq': src(p + '-input-seq'),
            'AppliedSeq': src(p + '-applied-seq'),
            'ResultSeq': src(p + '-result-seq'),
            'Result': src(p + '-result'),
            'Current': drive(p + '-current'),
        })
        add(p + '-value', 'RowValue', p, {
            'Baseline': src(p + '-original'),
            'InputR': src(p + '-r-value-text', 'Content'),
            'InputG': src(p + '-g-value-text', 'Content'),
            'InputB': src(p + '-b-value-text', 'Content'),
            'Valid': drive(p + '-valid'),
            'Dirty': drive(p + '-dirty'),
            'Draft': drive(p + '-draft'),
            'Current': src(p + '-current'),
            'ProfilePreserved': drive(p + '-profile-preserved'),
        })
        add(p + '-preview', 'RowPreview', p, {
            'ActiveGeneration': src(p + '-registered'),
            'Selected': src(p + '-selected'),
            'Operator': field('ui-operator'),
            'UiReady': src('ui-runtime-ready'),
            'Ready': src('registry-ready'),
            'Busy': src('registry-busy'),
            'Intent': src('ui-intent'),
            'CommandKind': src('ui-command-kind'),
            'OperationStage': src('registry-operation-stage'),
            'EditEpoch': src('ui-edit-epoch'),
            'SnapshotEpoch': src(p + '-snapshot-epoch'),
            'InputR': src(p + '-r-value-text', 'Content'),
            'InputG': src(p + '-g-value-text', 'Content'),
            'InputB': src(p + '-b-value-text', 'Content'),
            'Valid': src(p + '-valid'),
            'Draft': src(p + '-draft'),
            'SourceField': field(p + '-dropped'),
            'CapturedField': field(p + '-captured'),
            'Registered': src(p + '-registered'),
            'Duplicate': src(p + '-duplicate'),
            'LastApplied': src(p + '-last-applied'),
            'PreviewWritten': src(p + '-preview-written'),
            'PreviewActive': src('registry-preview-active'),
            'RecoveryPending': src('registry-pending'),
            'RowPending': src(p + '-row-pending'),
            'InputSeq': src(p + '-input-seq'),
            'AppliedSeq': src(p + '-applied-seq'),
            'Result': src(p + '-result'),
            'ResultSeq': src(p + '-result-seq'),
            'OpStage': src(p + '-op-stage'),
            'OpKind': src(p + '-op-kind'),
            'OpSeq': src(p + '-op-seq'),
            'OpId': src(p + '-op-id'),
            'PendingPrevious': src(p + '-pending-previous'),
            'PendingCandidate': src(p + '-pending-candidate'),
        })
        add(p + '-command', 'RowCommand', p, {
            'ActiveGeneration': src('ui-runtime-ready'),
            'Selected': src(p + '-selected'),
            'Operator': field('ui-operator'),
            'UiReady': src('ui-runtime-ready'),
            'Ready': src('registry-ready'),
            'Busy': src('registry-busy'),
            'Intent': src('ui-intent'),
            'OperationStage': src('registry-operation-stage'),
            'EditEpoch': src('ui-edit-epoch'),
            'SnapshotEpoch': src(p + '-snapshot-epoch'),
            'CommandUser': field('ui-command-user'),
            'CommandSeq': src('ui-command-seq'),
            'CommandKind': src('ui-command-kind'),
            'Valid': src(p + '-valid'),
            'Draft': src(p + '-draft'),
            'Original': src(p + '-original'),
            'SourceField': field(p + '-dropped'),
            'CapturedField': field(p + '-captured'),
            'Registered': src(p + '-registered'),
            'Duplicate': src(p + '-duplicate'),
            'AcceptButton': sel(p + '-accept-button'),
            'LastApplied': src(p + '-last-applied'),
            'PreviewWritten': src(p + '-preview-written'),
            'RecoveryPending': src('registry-pending'),
            'RowPending': src(p + '-row-pending'),
            'InputSeq': src(p + '-input-seq'),
            'AppliedSeq': src(p + '-applied-seq'),
            'Result': src(p + '-result'),
            'ResultSeq': src(p + '-result-seq'),
            'OpStage': src(p + '-op-stage'),
            'OpKind': src(p + '-op-kind'),
            'OpSeq': src(p + '-op-seq'),
            'OpId': src(p + '-op-id'),
            'PendingPrevious': src(p + '-pending-previous'),
            'PendingCandidate': src(p + '-pending-candidate'),
            'AckSeq': src(p + '-ack-seq'),
            'InputR': src(p + '-r-value-text', 'Content'),
            'InputG': src(p + '-g-value-text', 'Content'),
            'InputB': src(p + '-b-value-text', 'Content'),
            'AckRemaining': src('ui-ack-remaining'),
            'AckWait': src('ui-ack-wait'),
            'AckRefuse': src('ui-ack-refuse'),
            'AckFail': src('ui-ack-fail'),
            'CanAccept': drive(p + '-can-accept'),
        })
        add(p + '-presentation', 'RowPresentation', p, {
            'ReloadButton': sel('ui-reload-button'),
            'Operator': field('ui-operator'),
            'Original': src(p + '-original'),
            'LastApplied': src(p + '-last-applied'),
            'Current': src(p + '-current'),
            'Valid': src(p + '-valid'),
            'Dirty': src(p + '-dirty'),
            'PreviewWritten': src(p + '-preview-written'),
            'RecoveryPending': src('registry-pending'),
            'OperationStage': src('registry-operation-stage'),
            'External': src('ui-false'),
            'ExternalConfirmed': src('ui-false'),
            'CanEdit': src(p + '-can-edit'),
            'Intent': src('ui-intent'),
            'OpStage': src(p + '-op-stage'),
            'RowPending': src(p + '-row-pending'),
            'Result': src(p + '-result'),
            'CanAccept': src(p + '-can-accept'),
            'InputR': src(p + '-r-value-text', 'Content'),
            'InputG': src(p + '-g-value-text', 'Content'),
            'InputB': src(p + '-b-value-text', 'Content'),
            'OriginalDisplay': drive(p + '-original-label-text', 'Content'),
            'CurrentDisplay': drive(p + '-current-label-text', 'Content'),
            'RowStatus': drive(p + '-status-label-text', 'Content'),
        })

    add('ui-commit', 'Commit', 'registry-root', {
        'OkButton': sel('ui-ok-button'),
        'RestoreButton': sel('ui-restore-button'),
        'CancelButton': sel('ui-cancel-button'),
        'Operator': field('ui-operator'),
        'OperatorGuard': src('registry-operator'),
        'UiReady': src('ui-runtime-ready'),
        'Ready': src('registry-ready'),
        'Busy': src('registry-busy'),
        'SelectedRoot': field('ui-selected-root'),
        'RowCount': src('ui-row-count'),
        'Intent': src('ui-intent'),
        'CommandSeq': src('ui-command-seq'),
        'CommandKind': src('ui-command-kind'),
        'CommandUser': field('ui-command-user'),
        'AckRemaining': src('ui-ack-remaining'),
        'AckWait': src('ui-ack-wait'),
        'AckRefuse': src('ui-ack-refuse'),
        'AckFail': src('ui-ack-fail'),
        'PreviewActive': src('registry-preview-active'),
        'RecoveryPending': src('registry-pending'),
        'OperationStage': src('registry-operation-stage'),
        'Status': src('ui-status'),
        'RecoverTarget': field('ui-recover-target'),
        'NullUser': field('ui-null-user'),
        'NullRoot': field('ui-null-root'),
        'CanOk': drive('ui-ok-button', 'Enabled'),
        'CanRestore': drive('ui-restore-button', 'Enabled'),
        'CanCancel': drive('ui-cancel-button', 'Enabled'),
    })
    ui.validate_runtime_destinations(manifests)
    return {'format': 'emission-registrable-manifest-bundle-v1', 'manifests': manifests}


def _health(client, limit_ms):
    """Stop on a lost connection or a latency spike before the next live step."""
    data = client.run('status')
    if data.get('connected') is not True:
        raise RuntimeError('health-check-not-connected')
    latency = data.get('latencyMs')
    if isinstance(latency, (int, float)) and latency > limit_ms:
        raise RuntimeError(f'health-check-latency:{latency:.0f}>{limit_ms:.0f}')


def _write_manifests(root, bundle):
    paths = []
    for i, manifest in enumerate(bundle['manifests']):
        path = root / f'module-{i:02d}.json'
        if path.exists():
            if json.loads(path.read_bytes()) != manifest:
                raise ValueError('module-manifest-mismatch:' + str(path))
        else:
            _save(path, manifest)
        paths.append(path)
    return paths


def _verify_panel(client, panel, saved):
    root_id = saved.get('slots', {}).get('emission-root', {}).get('id')
    if not root_id:
        raise ValueError('panel-root-missing')
    data = client.run('inspect', root_id)
    if data.get('id') != root_id or data.get('name') != panel['slot']['name'] \
            or data.get('parentId') != 'Root':
        raise ValueError('panel-root-mismatch')
    return root_id


def _deploy_one(client, path, manifest, *, pause_seconds, health_latency_ms):
    client.run('flux', 'validate-manifest', path, '--library-path', client.library)
    result = client.run('flux', 'deploy-manifest', path, '--library-path', client.library)
    name = manifest['modules'][0]['name']
    if result.get('success') is not True:
        raise ValueError('native-module-deployment-failed:' + name)
    deploy_state = Path(manifest['deployState'])
    if not deploy_state.exists():
        raise ValueError('deploy-state-missing:' + str(deploy_state))
    state = json.loads(deploy_state.read_bytes())
    if name not in state.get('modules', {}):
        raise ValueError('deploy-state-missing-module:' + name)
    _health(client, health_latency_ms)
    if pause_seconds > 0:
        time.sleep(pause_seconds)


def _finalize(client, panel, saved, bundle, state_path, health_latency_ms):
    missing = []
    for manifest in bundle['manifests']:
        name = manifest['modules'][0]['name']
        deploy_state = Path(manifest['deployState'])
        if not deploy_state.exists():
            missing.append(name)
            continue
        state = json.loads(deploy_state.read_bytes())
        if name not in state.get('modules', {}):
            missing.append(name)
    if missing:
        raise ValueError('not-all-modules-deployed:' + ','.join(missing))
    _verify_panel(client, panel, saved)
    for target in ('registry-ready', 'ui-runtime-ready'):
        client.set(state_path, target, 'Value', True)
    _health(client, health_latency_ms)


def create_panel(directory, url, library, position, *, rows=POOL_ROWS,
                 deploy_modules=True, pause_seconds=0.0,
                 health_latency_ms=3000.0):
    """Apply a NEW unregistered panel; optionally deploy modules with health gates."""
    if rows < 1 or rows > POOL_ROWS:
        raise ValueError('row-pool-limit')
    root = Path(directory).absolute()
    if root.resolve().is_relative_to(CODE_ROOT):
        raise ValueError('output-must-be-outside-checkout')
    root.mkdir(parents=True, exist_ok=False)
    client = Demo(root, url, library)
    if not client.run('doctor', '--library-path', client.library).get('ready'):
        raise ValueError('doctor-not-ready')
    _health(client, health_latency_ms)
    token = uuid4().hex[:8]
    panel = build_panel(token=token, position=position, rows=rows)
    # UIX input recipes add JSON nodes after rendering. Keep a proportional
    # authoring budget for the row pool, within the CLI cap.
    def json_nodes(value):
        children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
        return 1 + sum(json_nodes(child) for child in children)
    panel['limits'] = {'expandedNodes': min(250000, max(10000, 2 * json_nodes(panel)))}
    _save(root / 'panel.json', panel)
    state = root / 'panel-state.json'
    client.run('apply', root / 'panel.json', '--state', state)
    saved = json.loads(state.read_bytes())
    if saved['ownershipKey'] != panel['ownership']['key']:
        raise ValueError('new-panel-ownership-mismatch')
    _verify_panel(client, panel, saved)
    _health(client, health_latency_ms)
    bundle = session_manifest(
        rows=rows, world_state=state, deploy_state=root / 'flux-state.json',
        source_directory=root / 'sources', source_root=CODE_ROOT / 'flux/emission-editor',
    )
    _save(root / 'bundle.json', bundle)
    paths = _write_manifests(root, bundle)
    deployed = 0
    if deploy_modules:
        for path, manifest in zip(paths, bundle['manifests']):
            _deploy_one(client, path, manifest,
                        pause_seconds=pause_seconds, health_latency_ms=health_latency_ms)
            deployed += 1
            print('deployed ' + manifest['modules'][0]['module'], flush=True)
        _finalize(client, panel, saved, bundle, state, health_latency_ms)
    summary = {
        'url': url,
        'library': str(Path(library).resolve()),
        'panelRoot': saved['slots']['emission-root']['id'],
        'panelName': panel['slot']['name'],
        'ownershipKey': panel['ownership']['key'],
        'rows': rows,
        'modulesTotal': len(paths),
        'modulesDeployed': deployed,
        'ready': deploy_modules,
        'manualOperationVerified': False,
    }
    _save(root / 'deployment.json', summary)
    return summary


def deploy_panel(directory, url, library, *, from_index=0, count=None,
                 pause_seconds=0.0, health_latency_ms=3000.0):
    """Deploy a selected range of an existing panel's manifests with health gates."""
    root = Path(directory).resolve()
    panel = json.loads((root / 'panel.json').read_bytes())
    saved = json.loads((root / 'panel-state.json').read_bytes())
    bundle = json.loads((root / 'bundle.json').read_bytes())
    if saved.get('ownershipKey') != panel['ownership']['key']:
        raise ValueError('panel-ownership-mismatch')
    manifests = bundle['manifests']
    if from_index < 0 or from_index >= len(manifests):
        raise ValueError('from-index-out-of-range')
    if count is None:
        end = len(manifests)
    else:
        if count < 1:
            raise ValueError('count-out-of-range')
        end = min(len(manifests), from_index + count)
    client = Demo(root, url, library)
    if not client.run('doctor', '--library-path', client.library).get('ready'):
        raise ValueError('doctor-not-ready')
    _health(client, health_latency_ms)
    _verify_panel(client, panel, saved)
    paths = _write_manifests(root, bundle)
    deployed = 0
    for i in range(from_index, end):
        _deploy_one(client, paths[i], manifests[i],
                    pause_seconds=pause_seconds, health_latency_ms=health_latency_ms)
        deployed += 1
        print('deployed ' + manifests[i]['modules'][0]['module'], flush=True)
    summary = {
        'url': url,
        'panelRoot': saved['slots']['emission-root']['id'],
        'panelName': panel['slot']['name'],
        'fromIndex': from_index,
        'toIndex': end,
        'modulesDeployed': deployed,
    }
    _save(root / f'deployment-{from_index:03d}-{end:03d}.json', summary)
    return summary


def finalize_panel(directory, url, library, *, health_latency_ms=3000.0):
    """Enable readiness only after every manifest's module is recorded as deployed."""
    root = Path(directory).resolve()
    panel = json.loads((root / 'panel.json').read_bytes())
    saved = json.loads((root / 'panel-state.json').read_bytes())
    bundle = json.loads((root / 'bundle.json').read_bytes())
    if saved.get('ownershipKey') != panel['ownership']['key']:
        raise ValueError('panel-ownership-mismatch')
    client = Demo(root, url, library)
    if not client.run('doctor', '--library-path', client.library).get('ready'):
        raise ValueError('doctor-not-ready')
    _health(client, health_latency_ms)
    _finalize(client, panel, saved, bundle, root / 'panel-state.json', health_latency_ms)
    summary = {
        'url': url,
        'panelRoot': saved['slots']['emission-root']['id'],
        'panelName': panel['slot']['name'],
        'ready': True,
    }
    _save(root / 'finalized.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--directory', type=Path, required=True)
    create.add_argument('--url', required=True)
    create.add_argument('--library-path', type=Path, required=True)
    create.add_argument('--position', nargs=3, type=float, default=[0, 1.5, 1.5])
    create.add_argument('--rows', type=int, choices=range(1, POOL_ROWS + 1), default=POOL_ROWS)
    create.add_argument('--no-modules', action='store_true')
    create.add_argument('--pause-seconds', type=float, default=0.0)
    create.add_argument('--health-latency-ms', type=float, default=3000.0)
    deploy = commands.add_parser('deploy')
    deploy.add_argument('--directory', type=Path, required=True)
    deploy.add_argument('--url', required=True)
    deploy.add_argument('--library-path', type=Path, required=True)
    deploy.add_argument('--from-index', type=int, default=0)
    deploy.add_argument('--count', type=int)
    deploy.add_argument('--pause-seconds', type=float, default=0.0)
    deploy.add_argument('--health-latency-ms', type=float, default=3000.0)
    finalize = commands.add_parser('finalize')
    finalize.add_argument('--directory', type=Path, required=True)
    finalize.add_argument('--url', required=True)
    finalize.add_argument('--library-path', type=Path, required=True)
    finalize.add_argument('--health-latency-ms', type=float, default=3000.0)
    args = parser.parse_args()
    if args.command == 'create':
        result = create_panel(
            args.directory, args.url, args.library_path, args.position,
            rows=args.rows, deploy_modules=not args.no_modules,
            pause_seconds=args.pause_seconds, health_latency_ms=args.health_latency_ms,
        )
    elif args.command == 'deploy':
        result = deploy_panel(
            args.directory, args.url, args.library_path,
            from_index=args.from_index, count=args.count,
            pause_seconds=args.pause_seconds, health_latency_ms=args.health_latency_ms,
        )
    else:
        result = finalize_panel(
            args.directory, args.url, args.library_path,
            health_latency_ms=args.health_latency_ms,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
