"""S: explicit multi-root preservation must not silently shrink or protect all Root."""
from copy import deepcopy
from dataclasses import replace

from scripts.emission_editor.drivegate_evidence import compare_embedded_subtree
from scripts.emission_editor.drivegate_protection import (
    ProtectedScope, compare_protection, compare_protection_scope, protection_ids,
    validate_protected_diffs,
)
from scripts.emission_editor.drivegate_runtime import DiffInput
from scripts.emission_editor.drivegate_capture import DiffRequest
import json
import pytest


def node(ident, name, parent, path, children=()):
    return {'id':ident, 'name':name, 'parentId':parent, 'path':path,
            'isReferenceOnly':False, 'components':[], 'children':list(children)}


def protection_inputs(tmp_path):
    roots = tuple(node(ident, name, 'Root', 'Root/'+name)
                  for ident,name in (('Reso_EA','panel'),('Reso_EB','登録済み')))
    for i, root in enumerate(roots):
        root['components'].append({'id':'Reso_F'+str(i),'type':'[FrooxEngine]FrooxEngine.ValueField<bool>',
            'members':{'Value':{'kind':'field','id':'Reso_D'+str(i),'type':'System.Boolean','value':True}}})
    context = node('Root','Root',None,'Root',deepcopy(roots))
    plans = []
    for index, root in enumerate(roots):
        key = 'protected-'+str(index)
        component = root['components'][0]; ck = key+'-value'
        declaration = {'slot':{'key':key,'name':root['name']},
                       'components':[{'key':ck,'type':component['type'],'fields':{'Value':True}}],
                       'children':[], 'ownership':{'key':key}}
        state = {'schemaVersion':2,'ownershipKey':key,'slots':{key:{'id':root['id'],'path':root['path']}},
                 'components':{ck:{'id':component['id'],'slotKey':key,'type':component['type']}},'assets':{}}
        request = DiffRequest('ws://localhost:16070',tmp_path/(key+'.json'),tmp_path/(key+'-state.json'))
        operation = {'kind':'slot','key':key,'path':root['path'],'action':'no-op',
                     'atomic':False,'recovery':'checkpoint-and-reapply'}
        component_operation = operation | {'kind':'component','key':ck,'path':root['path']+'/@'+ck}
        plan = {'ownershipKey':key,'stateFile':str(request.state),'operations':[operation,component_operation],'changes':[]}
        plans.append(DiffInput(tmp_path/key,request,json.dumps(state).encode(),plan,json.dumps(declaration).encode()))
    return ProtectedScope(context,roots,tuple(plans))


def test_protection_preserves_both_roots_not_unrelated_root_values_or_fixture(tmp_path):
    # S: Root includes the owned fixture. Selecting all Root would veto valid
    # operations; selecting just one protected root silently loses user data.
    baseline = protection_inputs(tmp_path)
    observed = deepcopy(baseline.context)
    observed['position'] = {'x':4.25,'y':.5,'z':1.125}
    observed['children'].append(node('Reso_EC','owned','Root','Root/owned'))
    assert compare_protection(baseline,observed).status == 'passed'
    assert 'Reso_EC' not in protection_ids(baseline)
    assert compare_protection_scope(baseline,replace(baseline,context=observed)).status == 'passed'
    changed = deepcopy(observed)
    changed['children'][1]['name'] = 'changed'
    assert compare_protection(baseline,changed).status != 'passed'
    reduced = replace(baseline,roots=baseline.roots[:1],plans=baseline.plans[:1])
    assert compare_protection_scope(baseline,reduced).status != 'passed'
    moved = deepcopy(observed)
    protected = moved['children'].pop(1)
    protected['parentId'] = 'Reso_EC'
    moved['children'][-1]['children'].append(protected)
    assert compare_protection(baseline,moved).status != 'passed'
    fresh = tuple(replace(p,directory=tmp_path/('fresh-'+str(i))) for i,p in enumerate(baseline.plans))
    assert validate_protected_diffs(baseline,fresh) == fresh
    with pytest.raises(ValueError,match='protected-diff'):
        validate_protected_diffs(baseline,fresh[:1])
    other_state = json.loads(fresh[1].expected_state)
    other_state['slots']['protected-1']['id'] = 'Reso_EC'
    forged = replace(fresh[1],expected_state=json.dumps(other_state).encode())
    forged_scope = replace(baseline,plans=(fresh[0],forged))
    with pytest.raises(ValueError,match='protected-diff'):
        validate_protected_diffs(forged_scope,forged_scope.plans)


def test_explicit_diff_projection_keeps_the_omitted_native_graph_fully_protected(tmp_path):
    # S: CLI Apply declarations reject generated duplicate sibling Names. A
    # structural diff may omit explicit graph subtrees, NEVER their strict raw
    # preservation, nor an entire protected root or an unrecorded boundary.
    baseline = protection_inputs(tmp_path)
    roots = deepcopy(baseline.roots)
    graph = node('Reso_A0','PG','Reso_EA','Root/panel/PG',[
        node('Reso_A1','AND_Bool','Reso_A0','Root/panel/PG/AND_Bool'),
        node('Reso_A2','AND_Bool','Reso_A0','Root/panel/PG/AND_Bool')])
    roots[0]['children'].append(graph)
    context = node('Root','Root',None,'Root',deepcopy(roots))
    baseline = replace(baseline,roots=roots,context=context,diff_omitted_roots=('Reso_A0',))
    assert validate_protected_diffs(baseline,baseline.plans) == baseline.plans
    assert compare_protection(baseline,deepcopy(context)).status == 'passed'
    changed = deepcopy(context)
    changed['children'][0]['children'][0]['children'][0]['name'] = 'foreign graph'
    assert compare_protection(baseline,changed).status != 'passed'
    all_ids = protection_ids(baseline)
    assert {'Reso_A0','Reso_A1','Reso_A2'} <= all_ids
    with pytest.raises(ValueError,match='protected-diff-complete-coverage'):
        validate_protected_diffs(replace(baseline,diff_omitted_roots=()),baseline.plans)
    assert compare_protection_scope(baseline,replace(baseline,diff_omitted_roots=())).status != 'passed'
    with pytest.raises(ValueError,match='protected-diff-omitted'):
        validate_protected_diffs(replace(baseline,diff_omitted_roots=('Reso_EA',)),baseline.plans)


def test_cleanup_rebases_only_valid_relative_paths_and_actual_parent():
    # S: ignoring path/parent mismatches could authenticate another subtree.
    child = node('Reso_EB','非ASCII','Reso_EA','owned/非ASCII')
    expected = node('Reso_EA','owned','Root','owned',[child])
    embedded = deepcopy(expected)
    embedded['path'] = 'Root/owned'
    embedded['children'][0]['path'] = 'Root/owned/非ASCII'
    container = node('Root','Root',None,'Root',[embedded])
    original = deepcopy(expected)
    assert compare_embedded_subtree(expected,container).status == 'passed'
    assert expected == original  # The original anchor/Before is immutable.
    bad = deepcopy(expected)
    bad['children'][0]['path'] = 'other/非ASCII'
    assert compare_embedded_subtree(bad,container).status != 'passed'
    bad = deepcopy(expected)
    bad['parentId'] = 'Reso_EF'
    assert compare_embedded_subtree(bad,container).status != 'passed'
    bad_container = deepcopy(container)
    bad_container['children'][0]['children'][0]['path'] = 'Root/elsewhere/非ASCII'
    assert compare_embedded_subtree(expected,bad_container).status != 'passed'
