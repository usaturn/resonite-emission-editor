"""S: a copied session string or another endpoint must not authorize a writer.

The fake CLI is a real process; neither test contacts a world. Producer types
and edges are inputs from the SessionIdentity source semantics, not live IDs.
"""
from dataclasses import replace
from datetime import UTC, datetime
import json

from test_emission_read_receipts import fake_cli
from scripts.emission_editor.read_receipts import ReadRequest, collect_read
from scripts.emission_editor.drivegate_evidence import NativeIdentity, ReadWindow, load_observation


def panel_fixture():
    counter = 0x100
    def ident():
        nonlocal counter
        counter += 1
        return f'Reso_{counter:X}'
    def field(typ, value):
        return {'kind':'field', 'id':ident(), 'type':typ, 'value':value}
    def reference(target, typ):
        return {'kind':'reference', 'id':ident(), 'targetId':target, 'targetType':typ}
    def component(typ, members):
        return {'id':ident(), 'type':typ, 'members':{'Enabled':field('System.Boolean',True), **members}}
    def slot(name, components=(), children=()):
        return {'id':ident(), 'name':name, 'isReferenceOnly':False,
                'components':list(components), 'children':list(children)}
    value = field('System.String', '試験-session')
    registry = component('[FrooxEngine]FrooxEngine.ValueField<string>', {'Value':value})
    node = component('[ProtoFluxBindings]FrooxEngine.ProtoFlux.Runtimes.Execution.Nodes.FrooxEngine.Worlds.WorldSessionID', {})
    drive = component('[ProtoFluxBindings]FrooxEngine.FrooxEngine.ProtoFlux.CoreNodes.ObjectFieldDrive<string>', {
        'Value':reference(node['id'], '[FrooxEngine]FrooxEngine.ProtoFlux.INodeObjectOutput<string>')})
    proxy = component('[FrooxEngine]FrooxEngine.ProtoFlux.CoreNodes.FieldDriveBase<string>+Proxy', {
        'Node':reference(drive['id'], '[FrooxEngine]FrooxEngine.ProtoFlux.IProtoFluxNode'),
        'Path':{'id':ident(), 'kind':'list', 'elements':[]},
        'Drive':reference(value['id'], '[FrooxEngine]FrooxEngine.IField<string>')})
    panel = slot('ResoLoop_Test_EmissionEditor', children=[slot('Registry', children=[
        slot('registry-session', [registry]), slot('SessionIdentity', children=[
            slot('SessionID', [node]), slot('Output:SessionID', [drive, proxy])])])])
    def parents(current, parent):
        current['parentId'] = parent
        current['isActive'] = True
        for child in current['children']:
            parents(child, current['id'])
    parents(panel, 'Root')
    return panel, NativeIdentity(registry['id'], 'Value', value['value'])


def capture(tmp_path, fake_cli, label, *, broken=False, announcements=None):
    from scripts.emission_editor.drivegate_identity import NativeSessionEvidence
    directory = tmp_path/label
    directory.mkdir()
    stdout, _, _ = fake_cli
    panel, identity = panel_fixture()
    if broken:
        # The string is still correct, but the output Drive no longer targets it.
        proxy = panel['children'][0]['children'][1]['children'][1]['components'][1]
        proxy['members']['Drive']['targetId'] = 'Reso_FFFF'
    before = datetime.now(UTC)
    def read(name, request, data):
        stdout.write_text(json.dumps({'ok':True, 'data':data}, ensure_ascii=False))
        path = directory/name
        collect_read(request, path, cwd=tmp_path)
        return path
    url = 'ws://localhost:16070'
    session = {'sessionId':identity.session, 'url':'ws://127.0.0.1:16070/'}
    discovery = {'count':1, 'sessions':[session]}
    discover_before = read('discover-before', ReadRequest('discover',url), discovery)
    comp = panel['children'][0]['children'][0]['components'][0]
    request = ReadRequest('component-inspect',url,identity.component)
    session_before = read('session-before',request,comp)
    request_panel = ReadRequest('inspect',url,panel['id'],64)
    reads = tuple((request_panel,read(n,request_panel,panel)) for n in ('panel-before','panel-after'))
    session_after = read('session-after',request,comp)
    discovery_after = read('discover-after',ReadRequest('discover',url),
                          discovery if announcements is None else {'count':len(announcements),'sessions':announcements})
    finish = datetime.now(UTC)
    evidence = NativeSessionEvidence(discover_before,session_before,reads,session_after,discovery_after,panel['id'])
    return evidence, ReadWindow(url,before,finish,finish), identity


def test_same_string_without_source_producer_edges_is_not_identity_evidence(tmp_path, fake_cli):
    from scripts.emission_editor.drivegate_identity import assess_native_session
    evidence, window, identity = capture(tmp_path,fake_cli,'valid')
    result = assess_native_session(evidence,window=window,identity=identity)
    assert result.status == 'passed', result.issues
    assert result.data[0]['registryComponent'] == identity.component
    assert result.data[0]['activeDriveObserved'] is False
    assert not result.executionPermission and not result.nativeSucceeded
    bad, bad_window, _ = capture(tmp_path,fake_cli,'broken',broken=True)
    # Behavioral witness: the previous value-only bracket accepts this input.
    assert load_observation(bad.before_session,list(bad.reads),bad.after_session,
                            window=bad_window,identity=identity).status == 'passed'
    rejected = assess_native_session(bad,window=bad_window,identity=identity)
    assert rejected.status == 'failed'
    assert 'session-producer-reference' in rejected.issues[0]


def test_endpoint_discovery_is_unique_on_both_sides_of_the_native_bracket(tmp_path, fake_cli):
    from scripts.emission_editor.drivegate_identity import assess_native_session
    good, window, identity = capture(tmp_path,fake_cli,'good')
    assert assess_native_session(good,window=window,identity=identity).status == 'passed'
    candidates = [{'sessionId':identity.session,'url':'ws://localhost:16071/'},
                  {'sessionId':'another-world','url':'ws://localhost:16070/'}]
    changed, other_window, _ = capture(tmp_path,fake_cli,'world-change',announcements=candidates)
    assert assess_native_session(changed,window=other_window,identity=identity).status != 'passed'
    candidates = [{'sessionId':identity.session,'url':'ws://localhost:16070/'},
                  {'sessionId':identity.session,'url':'ws://127.0.0.1:16070/'}]
    ambiguous, last_window, _ = capture(tmp_path,fake_cli,'ambiguous',announcements=candidates)
    assert assess_native_session(ambiguous,window=last_window,identity=identity).status != 'passed'
    # Even a valid old discovery cannot be substituted into a new capture.
    replay = replace(ambiguous,discovery_after=good.discovery_after)
    assert assess_native_session(replay,window=last_window,identity=identity).status != 'passed'
