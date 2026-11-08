"""S: lost create acknowledgements/foreign state must not duplicate or adopt data.

The fake apply/read CLI runs as real child processes. Original precreate and
native session authentication are explicit seams inherited from bootstrap's
fixture; actual created model/receipts/ID absence/protection comparison are used.
These are NOT full preflight, toolchain or actual native integration tests.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path

from scripts.emission_editor import drivegate_bootstrap as bootstrap
from scripts.emission_editor import drivegate_lifecycle as lifecycle
from scripts.emission_editor.drivegate_evidence import ReadWindow, Verdict
from scripts.emission_editor.drivegate_identity import NativeSessionEvidence
from scripts.emission_editor.read_receipts import collect_read, load_read
from test_emission_drivegate_bootstrap import inputs


def setup_create(root, monkeypatch, *, mode='applied', borrow_latest=False):
    kwargs, payload_path = inputs(root, monkeypatch)
    original = kwargs['precreate']
    state_path = kwargs['fixture_diff'].request.state
    state_bytes = state_path.read_bytes()
    state_path.unlink()
    seed = root / 'output-state'
    seed.write_bytes(state_bytes)
    payload = json.loads(payload_path.read_bytes())
    after_root = deepcopy(payload['root'])
    before_root = load_read(original.parent_read[1])['response']['data']
    if borrow_latest:
        # This element appeared AFTER the original precreate but BEFORE launch.
        # Creation must not borrow it, even though original absence passes.
        before_root['components'].append({'id':'Reso_EF01','type':'Foreign', 'members':{
            'Name':{'kind':'field','id':payload['fixture']['members']['Name']['id'],
                    'type':'System.String','value':'後から存在した'}}})
    payload['root'] = before_root
    payload['createdRoot'] = after_root
    payload_path.write_text(json.dumps(payload, ensure_ascii=False))
    calls = root / 'apply-calls'
    monkeypatch.setenv('CREATE_CALLS', str(calls))
    monkeypatch.setenv('CREATE_STATE_TEMPLATE', str(seed))
    monkeypatch.setenv('CREATE_MODE', mode)
    executable = root / 'resoloop'
    executable.write_text(executable.read_text().replace(
        "if args[0] == 'inspect':", """if args[0] == 'apply':
    calls = pathlib.Path(os.environ['CREATE_CALLS'])
    count = int(calls.read_text()) + 1 if calls.exists() else 1
    calls.write_text(str(count))
    mode = os.environ['CREATE_MODE']
    if mode != 'unapplied-once' or count > 1:
        output = pathlib.Path(args[args.index('--state') + 1])
        output.write_bytes(pathlib.Path(os.environ['CREATE_STATE_TEMPLATE']).read_bytes()
                           if mode != 'partial' else b'partial checkpoint')
        if mode != 'partial':
            p['root'] = p['createdRoot']
            pathlib.Path(os.environ['BOOTSTRAP_PAYLOAD']).write_text(json.dumps(p))
    print(json.dumps({'ok':False,'error':'lost acknowledgement'}))
    sys.exit(1)
if args[0] == 'inspect':"""))
    seal = bootstrap.verify_package(kwargs['package']) | {'codeCommit':'test-clean'}
    monkeypatch.setattr(lifecycle, 'verify_package', lambda p: seal)
    monkeypatch.setattr(lifecycle, '_code_commit', lambda: 'test-clean')
    monkeypatch.setattr(lifecycle, 'assess_precreate_history', lambda *a: Verdict('passed'))
    monkeypatch.setattr(lifecycle, 'assess_precreate_scope', lambda *a: Verdict('passed'))
    monkeypatch.setattr(lifecycle, 'assess_native_session', lambda *a, **k: Verdict('passed'))

    def scoped(scope, second):
        evidence = NativeSessionEvidence(root/'discovery-before', scope.before_session,
            (scope.parent_read, (scope.parent_read[0], second)), scope.after_session,
            root/'discovery-after', kwargs['panel_id'])
        return lifecycle.CreationScope(scope, evidence)

    second = root/'original-root-two'
    payload['root'] = load_read(original.parent_read[1])['response']['data']
    payload_path.write_text(json.dumps(payload))
    collect_read(original.parent_read[0], second, cwd=kwargs['cwd'])
    payload['root'] = before_root
    payload_path.write_text(json.dumps(payload))
    bound = scoped(original, second)
    counter = 0
    def observe():
        nonlocal counter
        counter += 1
        directory = root / f'observe-{counter}'
        if state_path.exists() and state_path.read_bytes() == state_bytes:
            return bootstrap.collect_created_stage(directory=directory, **kwargs)
        directory.mkdir()
        started = datetime.now(UTC)
        request = original.parent_read[0]
        collect_read(request, directory/'root-one', cwd=kwargs['cwd'])
        collect_read(request, directory/'root-two', cwd=kwargs['cwd'])
        finished = datetime.now(UTC)
        scope = replace(original, window=ReadWindow(original.window.url, started, finished, finished),
                        parent_read=(request, directory/'root-one'))
        return scoped(scope, directory/'root-two')
    args = dict(package=kwargs['package'], execution=kwargs['execution'], original=bound,
                observe=observe, backoff_seconds=0)
    return args, calls, state_path, state_bytes, payload_path


def test_create_lost_ack_uses_own_process_state_and_latest_absence_before(tmp_path, monkeypatch):
    # S: nonzero apply can have created a complete fixture. Verify rather than
    # duplicating it, including restart, and reserve the newest prewrite IDs.
    args, calls, state, expected, _ = setup_create(tmp_path/'good', monkeypatch)
    result = lifecycle.run_create(**args)
    assert result['status'] == 'applied' and calls.read_text() == '1'
    attempt = Path(result['processEvidence'])
    assert (attempt/'created-state.bin').read_bytes() == expected == state.read_bytes()
    assert json.loads((attempt/'process-result.json').read_bytes())['exitCode'] == 1
    again = lifecycle.run_create(**args)
    assert again['status'] == 'applied' and calls.read_text() == '1'
    assert not again['nativeSucceeded'] and not again['executionPermission']
    # A structurally valid fixture cannot replace output from our process.
    (attempt/'created-state.bin').write_bytes(b'not this process output')
    rejected = lifecycle.run_create(**args)
    assert rejected['application'] == 'unknown' and calls.read_text() == '1'
    assert 'create-process-state-or-window' in rejected['signature']

    borrowed, calls, _, _, _ = setup_create(tmp_path/'borrowed', monkeypatch, borrow_latest=True)
    rejected = lifecycle.run_create(**borrowed)
    assert rejected['application'] == 'unknown' and calls.read_text() == '1'
    assert 'create-prewrite-native-id-borrowed' in rejected['signature']


def test_create_confirmed_absent_retries_but_partial_state_is_never_replayed(tmp_path, monkeypatch):
    # S: preserve failure/attempts, retry only when new absence AND no state are
    # confirmed. A partial state checkpoint must not be treated as fresh input.
    args, calls, _, _, _ = setup_create(tmp_path/'retry', monkeypatch, mode='unapplied-once')
    result = lifecycle.run_create(**args)
    assert result['status'] == 'applied' and calls.read_text() == '2'
    run = Path(result['directory'])
    for number in (1, 2):
        assert json.loads((run/f'attempt-{number}/process-result.json').read_bytes())['exitCode'] == 1
    args, calls, state, _, _ = setup_create(tmp_path/'partial', monkeypatch, mode='partial')
    result = lifecycle.run_create(**args)
    assert result['status'] == 'source-investigation' and result['application'] == 'partial'
    assert calls.read_text() == '1' and state.read_bytes() == b'partial checkpoint'


def test_create_read_stage_without_own_process_or_changed_context_cannot_authorize_apply(tmp_path, monkeypatch):
    # S: a valid found fixture/state is not proof we created it. A foreign
    # session cannot authorize a different create operation.
    args, calls, state, expected, payload_path = setup_create(tmp_path/'found', monkeypatch)
    state.write_bytes(expected)
    payload = json.loads(payload_path.read_bytes())
    payload['root'] = payload['createdRoot']
    payload_path.write_text(json.dumps(payload))
    result = lifecycle.run_create(**args)
    assert result['application'] == 'unknown' and not calls.exists()
    assert 'create-missing-process-provenance' in result['signature']

    args, calls, state, _, _ = setup_create(tmp_path/'foreign', monkeypatch)
    normal = args['observe']
    def foreign():
        current = normal()
        return replace(current, scope=replace(current.scope,
            identity=replace(current.scope.identity, session='別のworld')))
    result = lifecycle.run_create(**(args | {'observe':foreign}))
    assert result['application'] == 'unknown' and not calls.exists()
    assert 'create-original-scope-changed' in result['signature']


def setup_probe(root, monkeypatch, *, mode='applied'):
    args, create_calls, state, _, payload_path = setup_create(root, monkeypatch)
    assert lifecycle.run_create(**args)['status'] == 'applied'
    created = args['observe']()
    flux_state = args['execution']/'flux.json'
    for path in (args['package']/'manifests/probe.json', args['execution']/'manifests/probe.json'):
        path.parent.mkdir(exist_ok=True)
        manifest = json.loads((args['package']/'manifests/probe.json').read_bytes())
        path.write_text(json.dumps(manifest | {'deployState':str(flux_state)}))
    payload = json.loads(payload_path.read_bytes())
    fixture = deepcopy(payload['fixture'])
    parent = fixture['children'][1]
    parent['children'] = [{'id':'Reso_F000','name':'sealed-probe','parentId':parent['id'],
        'path':parent['path']+'/sealed-probe','isReferenceOnly':False,'components':[], 'children':[],
        'members':{'Name':{'id':'Reso_F001','kind':'field','type':'System.String','value':'sealed-probe'}}}]
    embedded = deepcopy(fixture)
    def prefix(node):
        node['path'] = 'Root/'+node['path']
        for child in node['children']: prefix(child)
    prefix(embedded)
    deployed_root = deepcopy(payload['root'])
    deployed_root['children'][-1] = embedded
    payload['probeFixture'], payload['probeRoot'] = fixture, deployed_root
    payload_path.write_text(json.dumps(payload))
    executable = root/'resoloop'
    executable.write_text(executable.read_text().replace("if args[0] == 'inspect':", """if args[:2] == ['flux','deploy-manifest']:
    calls = pathlib.Path(os.environ['PROBE_CALLS'])
    count = int(calls.read_text()) + 1 if calls.exists() else 1
    calls.write_text(str(count))
    mode = os.environ['PROBE_MODE']
    if mode == 'partial-state':
        pathlib.Path(os.environ['PROBE_STATE']).write_bytes(b'partial deploy checkpoint')
    elif mode != 'unapplied-once' or count > 1:
        p['fixture'], p['root'] = p['probeFixture'], p['probeRoot']
        pathlib.Path(os.environ['BOOTSTRAP_PAYLOAD']).write_text(json.dumps(p))
    print(json.dumps({'ok':False,'error':'lost deployment acknowledgement'}))
    sys.exit(1)
if args[0] == 'inspect':"""))
    calls = root/'probe-calls'
    monkeypatch.setenv('PROBE_CALLS',str(calls))
    monkeypatch.setenv('PROBE_MODE',mode)
    monkeypatch.setenv('PROBE_STATE',str(flux_state))
    # Explicit compiler/runtime seams, as in the original first-probe bootstrap
    # connection test. Process/state ownership and delta-ID checks remain real.
    monkeypatch.setattr(bootstrap,'load_native_layout',lambda *a,**k:Verdict('passed',data=({},)))
    monkeypatch.setattr(bootstrap,'current_fixture_expectation',lambda current,**k:Verdict('passed',data=(deepcopy(current),)))
    from scripts.emission_editor import drivegate_runtime as runtime
    def assess(package, execution, stage):
        if type(stage) is runtime.NativeDeployedStage:
            return Verdict('passed',data=runtime._owned_scope(package,stage.reviewed_fixture,stage.expected_state,stage.roles)[:2])
        return runtime.assess_deployed_stage(package,execution,stage)
    monkeypatch.setattr(bootstrap,'assess_deployed_stage',assess)
    counter = 0
    def observe():
        nonlocal counter
        payload = json.loads(payload_path.read_bytes())
        if not payload['fixture']['children'][1]['children']:
            return args['observe']()
        counter += 1
        return bootstrap.collect_probe_anchor(package=args['package'],execution=args['execution'],
            precreate=args['original'].scope,created=created,parsed_record=root/'parsed',
            native_layout=root/'layout',directory=root/f'probe-observe-{counter}',cwd=root)
    return dict(package=args['package'],execution=args['execution'],original=args['original'],
        created=created,observe=observe,backoff_seconds=0), calls, create_calls, payload_path


def test_probe_lost_ack_and_restart_require_own_deployment_not_only_a_native_read(tmp_path,monkeypatch):
    # S: don't replace an already deployed graph after a failed acknowledgement.
    # A matching graph found without our deploy process must not be adopted.
    args, calls, create_calls, _ = setup_probe(tmp_path/'good',monkeypatch)
    result = lifecycle.run_deploy_probe(**args)
    assert result['status'] == 'applied' and calls.read_text() == create_calls.read_text() == '1'
    attempt = Path(result['processEvidence'])
    assert (attempt/'deploy-probe-state.bin').read_bytes() == args['created'].expected_state
    assert json.loads((attempt/'process-result.json').read_bytes())['exitCode'] == 1
    assert lifecycle.run_deploy_probe(**args)['status'] == 'applied'
    assert calls.read_text() == '1' and not result['nativeSucceeded']

    args, calls, _, payload_path = setup_probe(tmp_path/'found',monkeypatch)
    payload = json.loads(payload_path.read_bytes())
    payload['fixture'], payload['root'] = payload['probeFixture'],payload['probeRoot']
    payload_path.write_text(json.dumps(payload))
    result = lifecycle.run_deploy_probe(**args)
    assert result['application'] == 'unknown' and not calls.exists()
    assert 'missing-process-provenance' in result['signature']


def test_probe_confirmed_absent_retries_but_partial_deploy_state_does_not(tmp_path,monkeypatch):
    # S: absent graph alone cannot authorize replacing a partially checkpointed
    # deployment; only fresh empty parent plus absent deploy state may retry.
    args, calls, _, _ = setup_probe(tmp_path/'retry',monkeypatch,mode='unapplied-once')
    result = lifecycle.run_deploy_probe(**args)
    assert result['status'] == 'applied' and calls.read_text() == '2'
    for number in (1,2):
        assert json.loads((Path(result['directory'])/f'attempt-{number}/process-result.json').read_bytes())['exitCode'] == 1
    args, calls, _, _ = setup_probe(tmp_path/'partial',monkeypatch,mode='partial-state')
    result = lifecycle.run_deploy_probe(**args)
    assert result['application'] == 'partial' and calls.read_text() == '1'
