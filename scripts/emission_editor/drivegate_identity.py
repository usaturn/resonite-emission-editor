"""Source-grounded session topology and two-sided discovery, read-only only.

SessionIdentity.pg's WorldSessionID -> string output is checked as native
ObjectFieldDrive/Proxy references to the typed registry field. Current source/IL
provenance and exact CLI/DLL inventories remain the full-preflight caller's job.
A passing result is NOT an observed active Drive, an independent transport,
full graph reproduction, full preflight or permission to mutate anything.
"""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .drivegate_evidence import Verdict, _aware, _tree, load_observation
from .read_receipts import ReadRequest, load_read, _digest


@dataclass(frozen=True)
class NativeSessionEvidence:
    discovery_before: Path
    before_session: Path
    reads: tuple[tuple[ReadRequest, Path], ...]
    after_session: Path
    discovery_after: Path
    panel_id: str


_NODE = '[ProtoFluxBindings]FrooxEngine.ProtoFlux.Runtimes.Execution.Nodes.FrooxEngine.Worlds.WorldSessionID'
_DRIVER = '[ProtoFluxBindings]FrooxEngine.FrooxEngine.ProtoFlux.CoreNodes.ObjectFieldDrive<string>'
_PROXY = '[FrooxEngine]FrooxEngine.ProtoFlux.CoreNodes.FieldDriveBase<string>+Proxy'
_VALUE = '[FrooxEngine]FrooxEngine.ValueField<string>'


def _one(items):
    items = list(items)
    if len(items) != 1:
        raise ValueError('session-producer-unique-source-element')
    return items[0]


def _child(parent, name):
    node = _one(n for n in parent['children'] if n['name'] == name)
    if node.get('parentId') != parent['id'] or node.get('isActive') is not True:
        raise ValueError('session-producer-parent-or-active-slot')
    return node


def _component(slot, typ):
    comp = _one(c for c in slot['components'] if c['type'] == typ)
    enabled = comp['members'].get('Enabled', {})
    if (enabled.get('kind') != 'field' or enabled.get('type') != 'System.Boolean'
            or enabled.get('value') is not True):
        raise ValueError('session-producer-enabled-field')
    return comp


def _reference(comp, name, target, typ):
    member = comp['members'][name]
    if (member.get('kind') != 'reference' or member.get('targetId') != target
            or member.get('targetType') != typ):
        raise ValueError('session-producer-reference:' + name)


def session_producer(tree, *, panel_id, identity):
    """Check declared types/edges, using current IDs only to resolve those edges.

    This is a topology predicate, not adoption of observed defaults or a claim
    that the existing graph was deployed by a current sealed manifest. Whole
    protected-subtree preservation and ownership are separate checks.
    """
    slots, _, issues = _tree(tree)
    if issues:
        raise ValueError('session-producer-complete-inspect-required')
    panel, parent = slots[panel_id]
    if (panel['name'] != 'ResoLoop_Test_EmissionEditor' or panel.get('parentId') != 'Root'
            or parent not in (None, 'Root') or panel.get('isActive') is not True):
        raise ValueError('session-producer-panel-context')
    registry = _child(panel, 'Registry')
    field_slot = _child(registry, 'registry-session')
    field = _component(field_slot, _VALUE)
    value = field['members'][identity.member]
    if (identity.member != 'Value' or field['id'] != identity.component
            or value.get('kind') != 'field' or value.get('type') != 'System.String'
            or type(value.get('value')) is not str or value['value'] != identity.session):
        raise ValueError('session-producer-registry-field')
    module = _child(registry, 'SessionIdentity')
    source = _component(_child(module, 'SessionID'), _NODE)
    output = _child(module, 'Output:SessionID')
    driver, proxy = _component(output, _DRIVER), _component(output, _PROXY)
    _reference(driver, 'Value', source['id'], '[FrooxEngine]FrooxEngine.ProtoFlux.INodeObjectOutput<string>')
    _reference(proxy, 'Node', driver['id'], '[FrooxEngine]FrooxEngine.ProtoFlux.IProtoFluxNode')
    _reference(proxy, 'Drive', value['id'], '[FrooxEngine]FrooxEngine.IField<string>')
    path = proxy['members']['Path']
    if path.get('kind') != 'list' or path.get('elements') != []:
        raise ValueError('session-producer-proxy-path')
    # Prevent a member alias from turning a reference into a different object.
    ps, pc, _ = _tree(panel)
    ids = set(ps) | set(pc)
    def member_ids(member):
        ident = member.get('id')
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError('session-producer-duplicate-or-missing-id')
        ids.add(ident)
        for nested in member.get('members', {}).values():
            member_ids(nested)
        for nested in member.get('elements', []):
            member_ids(nested)
    for slot, _ in ps.values():
        for member in slot.get('members', {}).values():
            member_ids(member)
    for comp, _ in pc.values():
        for member in comp['members'].values():
            member_ids(member)
    targeting = [c['id'] for c, _ in pc.values()
                 if c['members'].get('Drive', {}).get('targetId') == value['id']]
    if targeting != [proxy['id']]:
        raise ValueError('session-producer-ambiguous-drive-target')
    return {'panel':panel_id, 'registryComponent':field['id'], 'registryMember':value['id'],
            'producer':source['id'], 'driver':driver['id'], 'proxy':proxy['id'],
            'nativeSession':identity.session, 'activeDriveObserved':False,
            'independentTransport':False, 'sealedManifestMatch':False}


def _announcement(loaded, url, session):
    data = loaded['response']['data']
    candidates = data['sessions']
    if (not isinstance(candidates, list) or type(data['count']) is not int
            or data['count'] != len(candidates)):
        raise ValueError('session-discovery-shape')
    endpoint = urlsplit(url)
    matches = []
    for candidate in candidates:
        current = urlsplit(candidate['url'])
        if current.hostname in ('localhost', '127.0.0.1', '::1') and current.port == endpoint.port:
            ReadRequest('discover', candidate['url']).argv()
            matches.append(candidate)
    if len(matches) != 1 or type(matches[0].get('sessionId')) is not str or matches[0]['sessionId'] != session:
        raise ValueError('session-discovery-unique-endpoint-and-world')


def assess_native_session(evidence: NativeSessionEvidence, *, window, identity):
    """Verify original before/after discovery around typed native/session reads.

    At least two full panel/Root reads must have the same source-grounded
    topology. Receipt integrity/order/physical independence are local checks,
    not authentication against coordinated hostile evidence replacement.
    """
    try:
        if len(evidence.reads) < 2 or any(r.operation != 'inspect' for r, _ in evidence.reads):
            raise ValueError('session-two-complete-inspects-required')
        observed = load_observation(evidence.before_session, list(evidence.reads), evidence.after_session,
                                    window=window, identity=identity)
        if observed.status != 'passed':
            return observed
        discovery_request = ReadRequest('discover',window.url)
        # The existing loader checks inner evidence. Extend its physical/replay
        # and temporal checks to the TWO outer discovery originals as well.
        sequence = [(discovery_request,evidence.discovery_before),
                    (ReadRequest('component-inspect',window.url,identity.component),evidence.before_session),
                    *evidence.reads,
                    (ReadRequest('component-inspect',window.url,identity.component),evidence.after_session),
                    (discovery_request,evidence.discovery_after)]
        used, fingerprints = set(), set()
        previous = window.started
        for index, (request,directory) in enumerate(sequence):
            directory = Path(directory)
            for name in ('receipt.json','stdout.bin'):
                stat = (directory/name).stat()
                key = (stat.st_dev,stat.st_ino)
                if key in used:
                    raise ValueError('session-reused-physical-evidence')
                used.add(key)
            fingerprint = _digest((directory/'receipt.json').read_bytes())
            if fingerprint in fingerprints:
                raise ValueError('session-replayed-evidence')
            fingerprints.add(fingerprint)
            loaded = load_read(directory)
            if loaded['argv'] != request.argv() or loaded['transportSucceeded'] is not True:
                raise ValueError('session-read-request-or-transport')
            start, finish = (datetime.fromisoformat(loaded[k]) for k in ('startedAt','finishedAt'))
            if not _aware(start) or not _aware(finish) or not previous <= start <= finish <= window.finished:
                raise ValueError('session-read-time-or-order')
            previous = finish
            if index in (0,len(sequence)-1):
                _announcement(loaded,window.url,identity.session)
        bindings = [session_producer(tree,panel_id=evidence.panel_id,identity=identity) for tree in observed.data]
        if any(binding != bindings[0] for binding in bindings[1:]):
            raise ValueError('session-producer-binding-changed')
        # Tie the actual native field member to the producer's Drive target, not
        # just component+name+same string in a different member with reused ID.
        for directory in (evidence.before_session,evidence.after_session):
            field = load_read(directory)['response']['data']
            if (field.get('type') != _VALUE
                    or field['members'][identity.member]['id'] != bindings[0]['registryMember']):
                raise ValueError('session-native-field-member-binding')
        return Verdict('passed',data=(bindings[0],))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return Verdict('failed',('native-session-evidence:' + str(exc),))
