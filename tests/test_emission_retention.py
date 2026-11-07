"""S: dropping retained desired subtrees silently loses recovery data on later prune.
The real stage must preserve existing runtime values without replaying old fields.
"""
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_emission_registry import shared_capture
from scripts.emission_editor.model import plan_registry
from scripts.emission_editor.resoloop_io import ResoloopPanelPort
from pathlib import Path
from scripts.emission_editor.ui import render_panel

_TEMPLATE = Path(__file__).resolve().parents[1] / "content/emission-editor/registry.json"


def test_stage_retains_existing_records_without_replaying_values_or_foreign_scope(tmp_path):
    capture = shared_capture()
    old = render_panel(plan_registry(replace(capture, generation='old')), template=_TEMPLATE)
    path = tmp_path / 'old.json'; path.write_text(json.dumps(old))
    state = {'ownershipKey': 'tools-emission-editor', 'slots': {}, 'components': {}}
    # Boundary state models already expanded recipe slots, like the actual CLI state.
    def add(key, name, parent):
        state['slots'][key] = {'pathSegments': parent + [name]}
    add('reg-old', 'Generation_old', ['Root', 'ResoLoop_Test_EmissionEditor', 'Registry'])
    add('reg-old-ui', 'reg-old-ui', ['Root', 'ResoLoop_Test_EmissionEditor', 'ui-canvas'])
    add('reg-old-original-slot', 'original', state['slots']['reg-old']['pathSegments'])
    state['components']['reg-old-original'] = {'slotKey': 'reg-old-original-slot', 'type': 'ColorField'}
    statepath = tmp_path / 'state.json'
    statepath.write_text(json.dumps(state))
    retained = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'state_sha256': hashlib.sha256(statepath.read_bytes()).hexdigest(),
                'session': capture.session, 'generations': ['old']}
    plan = plan_registry(capture)
    world = {'reg-old-original': {'Original': [1, 2, 3, .37], 'LastApplied': 'kept', 'input': 'abc', 'Source': None}}
    before = copy.deepcopy(world)
    def run(command, file, *args):
        declaration = json.loads(file.read_text())
        nodes = {}
        def walk(n):
            if 'slot' in n: nodes[n['slot']['key']] = n
            for child in n.get('children', []): walk(child)
        walk(declaration)
        if command == 'diff':
            missing = not {'reg-old', 'reg-old-ui', 'reg-old-original-slot'} <= nodes.keys()
            return {'deletes': 2 if missing else 0, 'operations': [{'action': 'create', 'key': 'reg-' + plan.generation}]}
        assert command == 'apply'
        # Omitted retained component fields leave actual mutable records alone.
        component = nodes['reg-old-original-slot']['components'][0]
        world['reg-old-original'].update(component.get('fields', {}))
        world['new-generation'] = True
        return {}
    read = SimpleNamespace(session=capture.session, state=statepath, evidence=tmp_path / 'positive', run=run)
    port = ResoloopPanelPort(read, capture)
    port.retained_declaration = retained
    port.render_declaration = lambda plan: render_panel(plan, template=_TEMPLATE)
    port._set = lambda *args: None
    port.stage(plan)
    assert world['reg-old-original'] == before['reg-old-original']
    assert world['new-generation']
    # Each representative corruption must stop before diff/apply, not merely error later.
    for index, corruption in enumerate(('owner', 'session', 'parent', 'key', 'collision', 'existing-new')):
        badstate = copy.deepcopy(state); bad = dict(retained)
        if corruption == 'owner': badstate['ownershipKey'] = 'foreign'
        if corruption == 'session': bad['session'] = 'other-session'
        if corruption == 'parent': badstate['slots']['reg-old']['pathSegments'][1] = 'Foreign'
        if corruption == 'key':
            badstate['components']['foreign'] = badstate['components'].pop('reg-old-original')
        if corruption == 'collision': bad['generations'] = [plan.generation]
        if corruption == 'existing-new': badstate['slots']['reg-' + plan.generation] = {'pathSegments': ['Root', 'Somewhere']}
        statepath.write_text(json.dumps(badstate))
        bad['state_sha256'] = hashlib.sha256(statepath.read_bytes()).hexdigest()
        rejected = ResoloopPanelPort(SimpleNamespace(session=capture.session, state=statepath,
            evidence=tmp_path / str(index), run=lambda *a: pytest.fail('unsafe retention reached transport')), capture)
        rejected.retained_declaration = bad
        rejected.render_declaration = lambda plan: render_panel(plan, template=_TEMPLATE)
        with pytest.raises(ValueError): rejected.stage(plan)
