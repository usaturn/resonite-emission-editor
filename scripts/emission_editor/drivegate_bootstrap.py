"""Read-only created/first-probe capture, not a lifecycle writer or full preflight.

New native identities must be absent from the ORIGINAL precreate Root, including
slot/component members and nested list/array elements. Observed values never
become defaults: CreatedStage constructs its expectation from the sealed fixture
and current native model. All session/protection/diff originals remain attached.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from .drivegate_capture import collect_diff
from .drivegate_created import compare_created_fixture
from .drivegate_evidence import (
    ReadWindow, Verdict, _tree, _strict_equal, compare_embedded_subtree,
)
from .drivegate_identity import NativeSessionEvidence
from .drivegate_initial import compare_initial_fields
from .drivegate_native import _dlls, load_native_layout
from .drivegate_package import MODULES, _no_links, verify_package
from .drivegate_protection import ProtectedScope, compare_protection, compare_protection_scope
from .drivegate_reader import NativeReadFailure, _save, current_fixture_expectation
from .drivegate_runtime import (
    CreatedStage, NativeDeployedStage, DiffInput, PrecreateScope, assess_deployed_stage, assess_precreate_history,
)
from .read_receipts import ReadRequest, _decode, _write_new, collect_read, load_read


def native_ids(tree, *, unique=False):
    """Collect actual element IDs, never reference targets or opaque field values.

    Root reads can contain unrelated typed payloads; their values are not adopted
    or required to be static. Every identity in member/list/array structure still
    reserves its ID against borrowing by the newly created fixture.
    """
    slots, components, issues = _tree(tree)
    if issues:
        raise ValueError('created-complete-root-required:' + repr(issues))
    result = set(slots) | set(components)
    def member_ids(member):
        ident = member['id']
        if not isinstance(ident, str) or not ident:
            raise ValueError('created-complete-member-id-required')
        if unique and ident in result:
            raise ValueError('created-duplicate-native-id')
        result.add(ident)
        for child in member.get('members', {}).values():
            member_ids(child)
        for child in member.get('elements', []):
            member_ids(child)
    for node, _ in (*slots.values(), *components.values()):
        if not isinstance(node.get('members'), dict):
            raise ValueError('created-complete-member-coverage-required')
        for member in node['members'].values():
            member_ids(member)
    return result


def require_created_transition(package, execution, precreate, stage):
    """Revalidate creation scope and current CreatedStage, or refuse the anchor.

    This records a structural/initial-value transition only. It neither proves
    which process created the objects nor grants permission for a later writer.
    """
    if not isinstance(precreate, PrecreateScope) or not isinstance(stage, CreatedStage):
        raise ValueError('created-typed-transition-required')
    history = assess_precreate_history(package, execution, precreate)
    if history.status != 'passed':
        raise ValueError('created-precreate-history:' + repr(history.issues))
    evidence = stage.native_session_evidence
    if evidence is None:
        raise ValueError('created-native-session-required')
    if (stage.identity != precreate.identity or stage.window.url != precreate.window.url
            or stage.window.started < precreate.window.finished
            or stage.diff_request != precreate.fixture_diff.request
            or not isinstance(stage.protected_baseline, ProtectedScope)
            or compare_protection_scope(precreate.protected_baseline, stage.protected_baseline).status != 'passed'):
        raise ValueError('created-transition-context')
    _no_links(stage.diff_request.state)
    if stage.diff_request.state.read_bytes() != stage.expected_state:
        raise ValueError('created-state-changed')
    current = assess_deployed_stage(package, execution, stage)
    if current.status != 'passed':
        raise ValueError('created-native-stage:' + repr(current.issues))
    before_root = load_read(precreate.parent_read[1])['response']['data']
    if native_ids(before_root) & native_ids(stage.reviewed_fixture, unique=True):
        raise ValueError('created-preexisting-native-id')
    _root_binding(stage)
    return current


def _root_binding(stage):
    evidence = stage.native_session_evidence
    if evidence is None:
        raise ValueError('created-native-session-required')
    # Both complete Root observations must contain this exact fixture, not just
    # a name locator or a separate ID-inspect detached from the Root context.
    if len(evidence.reads) != 2 or any(r != ReadRequest('inspect', stage.window.url, 'Root', 64)
                                       for r, _ in evidence.reads):
        raise ValueError('created-two-complete-roots-required')
    for _, directory in evidence.reads:
        root = load_read(directory)['response']['data']
        embedded = compare_embedded_subtree(stage.reviewed_fixture, root)
        preserved = compare_protection(stage.protected_baseline, root)
        if embedded.status != 'passed' or preserved.status != 'passed':
            raise ValueError('created-root-binding:' + repr((*embedded.issues, *preserved.issues)))


def assess_created_transition(package, execution, precreate, stage) -> Verdict:
    try:
        return require_created_transition(package, execution, precreate, stage)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Verdict('failed', ('created-transition:' + str(exc),))


def _probe_layout(package, created, native_layout):
    loaded = load_native_layout(package, MODULES['probe'], native_layout,
        library_path=created.native_library_path, sdk_directory=created.native_sdk_directory)
    if loaded.status != 'passed':
        raise ValueError('probe-original-layout:' + repr(loaded.issues))
    return loaded.data[0]


def require_probe_transition(package, execution, precreate, created, stage):
    require_created_transition(package, execution, precreate, created)
    if (type(stage) is not NativeDeployedStage or stage.roles != ('probe',)
            or stage.identity != created.identity or stage.window.url != created.window.url
            or stage.window.started < created.window.finished or stage.diff_request != created.diff_request
            or stage.expected_state != created.expected_state
            or not _strict_equal(stage.expected_plan, created.expected_plan)
            or stage.native_library_path != created.native_library_path
            or stage.native_sdk_directory != created.native_sdk_directory
            or compare_protection_scope(created.protected_baseline, stage.protected_baseline).status != 'passed'):
        raise ValueError('probe-original-created-context')
    current = assess_deployed_stage(package, execution, stage)
    if current.status != 'passed':
        raise ValueError('probe-native-stage:' + repr(current.issues))
    if compare_initial_fields(verify_package(package), current.data[0]).status != 'passed':
        raise ValueError('probe-initial-fields')
    expected = current_fixture_expectation(stage.reviewed_fixture, package=package,
        anchor=created.reviewed_fixture, state_bytes=created.expected_state, roles=('probe',),
        layouts={'probe':_probe_layout(package, created, stage.native_layouts['probe'])},
        native_dll_sha256=_dlls(created.native_library_path))
    if expected.status != 'passed':
        raise ValueError('probe-created-anchor:' + repr(expected.issues))
    old_ids = native_ids(created.reviewed_fixture, unique=True)
    current_ids = native_ids(stage.reviewed_fixture, unique=True)
    new_ids = current_ids - old_ids
    before_root = load_read(created.protected_read[1])['response']['data']
    if not old_ids <= current_ids or not new_ids or new_ids & native_ids(before_root):
        raise ValueError('probe-preexisting-or-lost-native-id')
    _root_binding(stage)
    return current


def assess_probe_transition(package, execution, precreate, created, stage) -> Verdict:
    try:
        return require_probe_transition(package, execution, precreate, created, stage)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Verdict('failed', ('probe-transition:' + str(exc),))


def collect_probe_anchor(*, package: Path, execution: Path, precreate: PrecreateScope,
                         created: CreatedStage, parsed_record: Path, native_layout: Path,
                         directory: Path, cwd: Path) -> NativeDeployedStage:
    """First probe-only anchor from a verified created fixture, never new defaults.

    Current private fields must STILL match every sealed initial value. Module
    layout/BSON authentication remains mandatory; new probe IDs cannot borrow
    from the complete Root captured before probe deployment. No write is sent.
    """
    if not isinstance(created, CreatedStage) or created.native_session_evidence is None:
        raise ValueError('probe-verified-created-session-required')
    directory = Path(directory)
    fixture_diff = DiffInput(directory/'unused', created.diff_request, created.expected_state,
        deepcopy(created.expected_plan), (Path(package)/'fixture.json').read_bytes())
    return _collect_bootstrap(package=package, execution=execution, precreate=precreate,
        fixture_diff=fixture_diff, panel_id=created.native_session_evidence.panel_id,
        native_library_path=created.native_library_path, native_sdk_directory=created.native_sdk_directory,
        directory=directory, cwd=cwd, created=created, parsed_record=parsed_record, native_layout=native_layout)


def collect_created_stage(*, package: Path, execution: Path, precreate: PrecreateScope,
                          fixture_diff: DiffInput, panel_id: str, native_library_path: Path,
                          native_sdk_directory: Path, directory: Path, cwd: Path) -> CreatedStage:
    """Fixed discover/session/Root/fixture/diffs/Root/session/discover capture.

    The supplied post-create state and COMPLETE no-op expectation are prepared
    independently, not copied from this diff. The current state must be identical
    throughout. Failure saves originals and raises NativeReadFailure; no mutation,
    state adoption, undo, retry or write permission is performed here.
    """
    return _collect_bootstrap(package=package, execution=execution, precreate=precreate,
        fixture_diff=fixture_diff, panel_id=panel_id, native_library_path=native_library_path,
        native_sdk_directory=native_sdk_directory, directory=directory, cwd=cwd)


def _collect_bootstrap(*, package, execution, precreate, fixture_diff, panel_id,
                       native_library_path, native_sdk_directory, directory, cwd,
                       created=None, parsed_record=None, native_layout=None):
    directory = Path(directory)
    _no_links(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    try:
        if not isinstance(precreate, PrecreateScope) or not isinstance(precreate.protected_baseline, ProtectedScope):
            raise ValueError('created-precreate-scope-required')
        history = assess_precreate_history(package, execution, precreate)
        if history.status != 'passed':
            raise ValueError('created-precreate-history:' + repr(history.issues))
        if (fixture_diff.request != precreate.fixture_diff.request or fixture_diff.expected_state is None
                or fixture_diff.sealed_declaration != (Path(package)/'fixture.json').read_bytes()):
            raise ValueError('created-postcreate-diff-context')
        layout = None
        if created is not None:
            require_created_transition(package, execution, precreate, created)
            layout = _probe_layout(package, created, native_layout)
            _save(directory/'created.json', asdict(created))
        state_path = fixture_diff.request.state
        _no_links(state_path)
        if state_path.read_bytes() != fixture_diff.expected_state:
            raise ValueError('created-state-changed')
        state = _decode(fixture_diff.expected_state)
        url = precreate.window.url
        target = state['slots']['drivegate-root']['id']
        session_request = ReadRequest('component-inspect', url, precreate.identity.component)
        root_request = ReadRequest('inspect', url, 'Root', 64)
        fixture_request = ReadRequest('inspect', url, target, 64)
        fixture_request.argv()
        _save(directory/'precreate.json', asdict(precreate))
        _write_new(directory/'state.bin', fixture_diff.expected_state)
        _save(directory/'expected-plan.json', fixture_diff.expected_plan)
        protected_diffs = tuple(replace(p, directory=directory/('protected-diff-'+str(i)))
                                for i, p in enumerate(precreate.protected_baseline.plans))
        started = datetime.now(UTC)
        collect_read(ReadRequest('discover', url), directory/'discover-before', cwd=cwd)
        collect_read(session_request, directory/'session-before', cwd=cwd)
        collect_read(root_request, directory/'root-before', cwd=cwd)
        fixture = collect_read(fixture_request, directory/'fixture', cwd=cwd)
        collect_diff(fixture_diff.request, directory/'diff', cwd=cwd)
        for entry in protected_diffs:
            collect_diff(entry.request, entry.directory, cwd=cwd)
        collect_read(root_request, directory/'root-after', cwd=cwd)
        collect_read(session_request, directory/'session-after', cwd=cwd)
        collect_read(ReadRequest('discover', url), directory/'discover-after', cwd=cwd)
        finished = datetime.now(UTC)
        if state_path.read_bytes() != fixture_diff.expected_state:
            raise ValueError('created-state-changed')
        if fixture['transportSucceeded'] is not True:
            raise ValueError('created-fixture-transport')
        # This expectation copies ONLY identities, never observed defaults or
        # initial private values, from the current fixture.
        if created is None:
            checked = compare_created_fixture(fixture['response']['data'], package=package,
                state_bytes=fixture_diff.expected_state, native_dll_sha256=_dlls(native_library_path))
        else:
            checked = current_fixture_expectation(fixture['response']['data'], package=package,
                anchor=created.reviewed_fixture, state_bytes=fixture_diff.expected_state, roles=('probe',),
                layouts={'probe':layout}, native_dll_sha256=_dlls(native_library_path))
        if checked.status != 'passed':
            raise ValueError('created-fixture:' + repr(checked.issues))
        session = NativeSessionEvidence(directory/'discover-before', directory/'session-before',
            ((root_request, directory/'root-before'), (root_request, directory/'root-after')),
            directory/'session-after', directory/'discover-after', panel_id)
        stage_type = CreatedStage if created is None else NativeDeployedStage
        stage = stage_type(window=ReadWindow(url, started, finished, datetime.now(UTC)),
            identity=precreate.identity, before_session=directory/'session-before', after_session=directory/'session-after',
            fixture_read=(fixture_request, directory/'fixture'), protected_read=(root_request, directory/'root-after'),
            reviewed_fixture=checked.data[0], protected_baseline=deepcopy(precreate.protected_baseline),
            diff_directory=directory/'diff', diff_request=fixture_diff.request, expected_state=fixture_diff.expected_state,
            expected_plan=deepcopy(fixture_diff.expected_plan),
            parsed_records={} if created is None else {'probe':parsed_record}, id_maps={},
            roles=() if created is None else ('probe',),
            native_layouts={} if created is None else {'probe':native_layout},
            native_library_path=native_library_path, native_sdk_directory=native_sdk_directory,
            protected_diffs=protected_diffs, native_session_evidence=session)
        _save(directory/'stage.json', asdict(stage))
        result = (require_created_transition(package, execution, precreate, stage) if created is None else
                  require_probe_transition(package, execution, precreate, created, stage))
        _save(directory/'result.json', asdict(Verdict(result.status, result.issues)))
        return stage
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _save(directory/'result.json', asdict(Verdict('failed', ('created-capture:' + str(exc),))))
        raise NativeReadFailure(str(directory) + ':' + str(exc)) from exc
