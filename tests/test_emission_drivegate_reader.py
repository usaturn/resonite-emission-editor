"""S: current private values are data, never a new phase/default baseline."""
from copy import deepcopy
import json

from scripts.emission_editor import drivegate_reader as reader
from scripts.emission_editor.drivegate_package import FIELDS, GENERATED_CONTENT_KEY
from test_emission_drivegate_layout import native_fixture
from test_emission_drivegate_runtime import owned_fixture


def reader_fixture():
    anchor, state = owned_fixture()
    document, native, args, _, _ = native_fixture()
    parent = anchor['children'][3]
    native['children'][0]['parentId'] = parent['id']
    parent['path'] = 'Root/' + anchor['name'] + '/BoolDrive'
    parent['children'] = native['children']
    target = next(c['members']['Value']['id'] for c in anchor['children'][0]['components']
                  if c['id'] == json.loads(state)['components']['drivegate-BoolTarget']['id'])
    def rewrite(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k == 'targetId' and v == 'private-bool':
                    value[k] = target
                elif k == 'targetId' and v == 'owned-parent':
                    value[k] = parent['id']
                else:
                    rewrite(v)
        elif isinstance(value, list):
            for item in value: rewrite(item)
    rewrite(parent)
    # Paths are determined by the independent declared stable parent.
    def paths(node, path):
        node['path'] = path + '/' + node['name']
        for child in node['children']: paths(child, node['path'])
    paths(parent['children'][0], parent['path'])
    declaration = {'normalizedFixture': {
        'slot': {'key':'drivegate-root','name':anchor['name']},
        'ownership':{'key':'emission-drivegate-offline'},
        'components':[{'key':'drivegate-objectroot','type':'[FrooxEngine]FrooxEngine.ObjectRoot'},
            {'key':GENERATED_CONTENT_KEY,'type':'FrooxEngine.AI_GeneratedContent',
             'fields':{'Source':'[resoloop 0.2.0]'}}],
        'children':[{'slot':{'key':'drivegate-fields','name':'Private fields'},
                     'components':[{'key':'drivegate-'+n,'type':'[FrooxEngine]FrooxEngine.ValueField<'+t+'>'}
                                   for n,(t,_) in FIELDS.items()]},
                    *[{'slot':{'key':'drivegate-'+r,'name':name},'components':[]}
                      for r,name in [('probe','DriveGateProbe'),('color','ColorDrive'),('bool','BoolDrive')]]]}}
    return anchor, state, document, args, declaration


def test_current_values_do_not_bless_changed_metadata_or_overwrite_original_anchor(monkeypatch):
    # S: accepting an observed whole tree as expected can hide wrong metadata,
    # an unrelated Drive target, or a changed Before as an alleged new baseline.
    anchor, state, document, args, seal = reader_fixture()
    from scripts.emission_editor import drivegate_runtime
    monkeypatch.setattr(reader, 'verify_package', lambda p: seal)
    monkeypatch.setattr(drivegate_runtime, 'verify_package', lambda p: seal)
    current = deepcopy(anchor)
    current['children'][0]['components'][0]['members']['Value']['value'] = 1
    original = deepcopy(anchor)
    kwargs = dict(package=None, anchor=anchor, state_bytes=state, roles=('bool',),
                  layouts={'bool':document}, native_dll_sha256=args['native_dll_sha256'])
    result = reader.current_fixture_expectation(current, **kwargs)
    assert result.status == 'passed', result.issues
    assert anchor == original
    assert result.data[0]['children'][0]['components'][0]['members']['Value']['value'] == 1
    assert not result.complete and not result.nativeSucceeded
    metadata = deepcopy(current)
    metadata['children'][0]['name'] = 'foreign fields'
    assert reader.current_fixture_expectation(metadata, **kwargs).status != 'passed'
    typed = deepcopy(current)
    typed['children'][0]['components'][0]['members']['Value']['value'] = True
    assert reader.current_fixture_expectation(typed, **kwargs).status != 'passed'
    foreign = deepcopy(current)
    def break_drive(node):
        for comp in node.get('components', []):
            if 'Drive' in comp['members']:
                comp['members']['Drive']['targetId'] = 'other-panel'
        for child in node['children']: break_drive(child)
    break_drive(foreign['children'][3])
    assert reader.current_fixture_expectation(foreign, **kwargs).status != 'passed'


def test_reader_rejects_changed_state_and_retains_failed_raw(tmp_path, monkeypatch):
    # S: capturing a diff against changed/adopted state could select another
    # fixture while reporting that the original operation was authenticated.
    from scripts.emission_editor.drivegate_evidence import Verdict
    from scripts.emission_editor.drivegate_runtime import NativeDeployedStage
    from test_emission_drivegate_runtime import staged_fixture
    package, execution, old = staged_fixture(tmp_path, monkeypatch)
    stage = NativeDeployedStage(**vars(old), native_layouts={'bool':tmp_path/'layout'}, native_library_path=tmp_path/'lib', native_sdk_directory=tmp_path/'sdk')
    from dataclasses import replace
    from test_emission_drivegate_protection import protection_inputs
    stage = replace(stage,protected_baseline=protection_inputs(tmp_path))
    monkeypatch.setattr(reader, 'assess_deployed_stage', lambda *a: Verdict('passed'))
    # This test covers transport/provenance only; real native model is tested above.
    monkeypatch.setattr(reader, 'load_native_layout', lambda *a, **k: Verdict('passed', data=({},)))
    monkeypatch.setattr(reader, 'current_fixture_expectation', lambda *a, **k: Verdict('passed',data=(old.reviewed_fixture,)))
    monkeypatch.setenv('FAKE_DIFF_DATA', json.dumps(old.expected_plan))
    # Fixed fake read transport supplies new originals (never contacts a world).
    from scripts.emission_editor.read_receipts import _write_new, _digest
    from datetime import UTC, datetime
    def fake_read(request, directory, *, cwd):
        directory.mkdir()
        data = {'ok':True,'data':old.reviewed_fixture}
        raw = json.dumps(data).encode()
        _write_new(directory/'stdout.bin', raw)
        _write_new(directory/'stderr.bin', b'')
        now = datetime.now(UTC).isoformat()
        receipt = {'schemaVersion':1,'request':vars(request),'argv':request.argv(),'cwd':str(cwd),
                   'startedAt':now,'finishedAt':now,'exitCode':0,'launchError':None,
                   'stdoutSha256':_digest(raw),'stderrSha256':_digest(b'')}
        _write_new(directory/'receipt.json', json.dumps(receipt).encode())
        return {'response':data,'transportSucceeded':True}
    monkeypatch.setattr(reader, 'collect_read', fake_read)
    # Positive capture followed by adversarial state change in a new capture.
    diffs = []
    monkeypatch.setattr(reader, 'collect_diff', lambda request,directory,**k: diffs.append((request,directory)))
    success = reader.collect_native_stage(package, execution, stage, tmp_path/'fresh', cwd=tmp_path)
    assert isinstance(success, NativeDeployedStage)
    assert success.protected_read[0].target == 'Root'
    assert [r for r,_ in diffs] == [old.diff_request,*(p.request for p in stage.protected_baseline.plans)]
    assert len(success.protected_diffs) == 2
    assert all(p.directory.parent == tmp_path/'fresh' for p in success.protected_diffs)
    # A source-identified bootstrap anchor must not silently degrade back to
    # string-only session reads. This transport test still stubs native audit.
    from scripts.emission_editor.drivegate_identity import NativeSessionEvidence
    evidence = NativeSessionEvidence(tmp_path/'old-discover', stage.before_session,(),stage.after_session,
                                     tmp_path/'old-discover-after','Reso_EA')
    stage = replace(stage,native_session_evidence=evidence)
    sourced = reader.collect_native_stage(package, execution, stage, tmp_path/'source-aware', cwd=tmp_path)
    assert sourced.native_session_evidence.panel_id == evidence.panel_id
    assert sourced.native_session_evidence.before_session == tmp_path/'source-aware/session-before'
    assert [p.name for _,p in sourced.native_session_evidence.reads] == ['root-before','protected']
    assert (tmp_path/'source-aware/discover-after/receipt.json').is_file()
    def changed_read(request, directory, *, cwd):
        result = fake_read(request, directory, cwd=cwd)
        old.diff_request.state.write_bytes(b'changed-state')
        return result
    monkeypatch.setattr(reader, 'collect_read', changed_read)
    import pytest
    with pytest.raises(reader.NativeReadFailure):
        reader.collect_native_stage(package, execution, stage, tmp_path/'changed', cwd=tmp_path)
    assert (tmp_path/'changed/session-before/stdout.bin').is_file()
    assert json.loads((tmp_path/'changed/result.json').read_bytes())['status'] != 'passed'
    old.diff_request.state.write_bytes(old.expected_state)
    monkeypatch.setattr(reader, 'collect_read', fake_read)
    monkeypatch.setattr(reader, 'assess_deployed_stage', lambda p,e,s:
                        Verdict('passed') if s is stage else Verdict('failed', ('native-mismatch',)))
    with pytest.raises(reader.NativeReadFailure):
        reader.collect_native_stage(package, execution, stage, tmp_path/'rejected', cwd=tmp_path)
    assert json.loads((tmp_path/'rejected/result.json').read_bytes())['status'] == 'failed'
