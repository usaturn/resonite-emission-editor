"""Fixed read-only deployed-stage collector for the private Drivegate.

The immutable anchor authenticates stable identity/metadata, not current private
values. Current typed private values are classification DATA, not new Before or
phase expectations. Module values/defaults come from original sealed layout and
the DLL model. No arbitrary command, mutation, baseline adoption or success flag.
"""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import UTC, datetime
import json
from pathlib import Path

from .drivegate_capture import collect_diff
from .drivegate_evidence import Verdict, ReadWindow, compare_subtree, compare_typed
from .drivegate_layout import compare_native_layout
from .drivegate_identity import NativeSessionEvidence
from .drivegate_native import load_native_layout
from .drivegate_package import MODULES, _no_links, verify_package
from .drivegate_protection import ProtectedScope
from .drivegate_runtime import NativeDeployedStage, _owned_scope, assess_deployed_stage
from .read_receipts import ReadRequest, _write_new, collect_read


class NativeReadFailure(ValueError):
    """The saved new attempt failed; callers may reobserve, never blindly write."""


def current_fixture_expectation(current, *, package, anchor, state_bytes, roles,
                                layouts, native_dll_sha256) -> Verdict:
    """Authenticate stable data while returning current private fields as data.

    Only Value *values* of the exact independently owned private fields vary.
    Their member IDs/type/kind/keyset are still bound to the anchor. All other
    stable metadata is exact; role children are independently built from original
    layout/model. This comparison does NOT decide a phase or re-capture success.
    """
    try:
        if (not roles or len(set(roles)) != len(roles) or not set(roles) <= MODULES.keys()
                or set(layouts) != set(roles)):
            raise ValueError('reader-role-layout-coverage')
        fields, stable, _, _ = _owned_scope(package, current, state_bytes, roles)
        expected = deepcopy(anchor)
        # Anchor may have a different module-role subset. Clear only children of
        # the three independently identified stable role parents before the join.
        # No stable metadata/private field is copied from current observations.
        state = json.loads(state_bytes)
        role_ids = {state['slots']['drivegate-' + role]['id'] for role in MODULES}
        for child in expected['children']:
            if child['id'] in role_ids:
                child['children'] = []
        original_fields, original_stable, _, _ = _owned_scope(package, expected, state_bytes, roles)
        for name in fields:
            candidate = deepcopy(original_fields[name])
            candidate['value'] = deepcopy(fields[name]['value'])
            if compare_typed(candidate, fields[name]).status != 'passed':
                raise ValueError('reader-private-identity-type:' + name)
            original_fields[name]['value'] = deepcopy(fields[name]['value'])
        for role, module in MODULES.items():
            target = original_stable['drivegate-' + role]
            target['children'] = []
            if role not in roles:
                continue
            bindings = ({k: m['id'] for k, m in original_fields.items()} if role == 'probe' else
                        {'Driven':original_fields['ColorTarget' if role == 'color' else 'BoolTarget']['id']})
            checked = compare_native_layout(layouts[role], stable['drivegate-' + role], module=module,
                private_bindings=bindings, native_dll_sha256=native_dll_sha256)
            if checked.status != 'passed':
                return checked
            target['children'] = [checked.data[0]]
        result = compare_subtree(expected, current)
        return Verdict(result.status, result.issues, (expected,) if result.status == 'passed' else ())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Verdict('failed', ('reader-current-fixture:' + str(exc),))


def _save(path, value):
    def default(item):
        if isinstance(item, (Path, datetime)):
            return str(item)
        if isinstance(item, bytes):
            # The original current state is separately recorded byte-for-byte.
            import hashlib
            return {'sha256':hashlib.sha256(item).hexdigest()}
        raise TypeError(type(item).__name__)
    _write_new(path, (json.dumps(value, default=default, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode())


def collect_native_stage(package: Path, execution: Path, anchor: NativeDeployedStage,
                         directory: Path, *, cwd: Path, roles=None,
                         parsed_records=None, native_layouts=None) -> NativeDeployedStage:
    """Fixed native reads and all protected diffs in one bracket; NO writes.

    The anchor and original layout/BSON receipts must already exist and pass
    authentication. This does not replace creation-only scope/fullpreflight or
    produce the initial anchor. A failed collection retains all original files
    and raises NativeReadFailure for Command controller read recovery.
    """
    directory = Path(directory)
    _no_links(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    try:
        if not isinstance(anchor, NativeDeployedStage):
            raise ValueError('reader-native-anchor-required')
        authenticated = assess_deployed_stage(package, execution, anchor)
        if authenticated.status != 'passed':
            raise ValueError('reader-anchor:' + repr(authenticated.issues))
        roles = anchor.roles if roles is None else tuple(roles)
        records = dict(anchor.parsed_records if parsed_records is None else parsed_records)
        layout_paths = dict(anchor.native_layouts if native_layouts is None else native_layouts)
        if (not roles or len(set(roles)) != len(roles) or not set(roles) <= MODULES.keys()
                or set(records) != set(roles) or set(layout_paths) != set(roles)):
            raise ValueError('reader-original-role-evidence-required')
        layouts = {}
        for role in roles:
            loaded = load_native_layout(package, MODULES[role], layout_paths[role],
                library_path=anchor.native_library_path, sdk_directory=anchor.native_sdk_directory)
            if loaded.status != 'passed':
                raise ValueError('reader-layout:' + repr(loaded.issues))
            layouts[role] = loaded.data[0]
        state = anchor.diff_request.state
        _no_links(state)
        expected_state = anchor.expected_state
        _write_new(directory / 'state.bin', expected_state)
        if state.read_bytes() != expected_state:
            raise ValueError('reader-checkpoint-state-changed')
        _save(directory / 'anchor.json', asdict(anchor))
        url = anchor.window.url
        session_request = ReadRequest('component-inspect', url, anchor.identity.component)
        fixture_request = ReadRequest('inspect', url, anchor.reviewed_fixture['id'], 64)
        protection = anchor.protected_baseline
        protected_request = ReadRequest('inspect', url,
            'Root' if isinstance(protection,ProtectedScope) else protection['id'], 64)
        protected_diffs = tuple(replace(p,directory=directory/('protected-diff-'+str(i)))
            for i,p in enumerate(protection.plans)) if isinstance(protection,ProtectedScope) else ()
        source_session = anchor.native_session_evidence
        if source_session is not None and not isinstance(protection, ProtectedScope):
            raise ValueError('reader-source-session-root-protection-required')
        started = datetime.now(UTC)
        if source_session is not None:
            collect_read(ReadRequest('discover', url), directory/'discover-before', cwd=cwd)
        collect_read(session_request, directory / 'session-before', cwd=cwd)
        if source_session is not None:
            collect_read(protected_request, directory/'root-before', cwd=cwd)
        fixture = collect_read(fixture_request, directory / 'fixture', cwd=cwd)
        if source_session is None:
            collect_read(protected_request, directory / 'protected', cwd=cwd)
        collect_diff(anchor.diff_request, directory / 'diff', cwd=cwd)
        for entry in protected_diffs:
            collect_diff(entry.request, entry.directory, cwd=cwd)
        if source_session is not None:
            collect_read(protected_request, directory/'protected', cwd=cwd)
        collect_read(session_request, directory / 'session-after', cwd=cwd)
        if source_session is not None:
            collect_read(ReadRequest('discover', url), directory/'discover-after', cwd=cwd)
        finished = datetime.now(UTC)
        if state.read_bytes() != expected_state:
            raise ValueError('reader-checkpoint-state-changed')
        if fixture['transportSucceeded'] is not True:
            raise ValueError('reader-fixture-transport')
        seal = verify_package(package)
        expected = current_fixture_expectation(fixture['response']['data'], package=package,
            anchor=anchor.reviewed_fixture, state_bytes=expected_state, roles=roles,
            layouts=layouts, native_dll_sha256=seal['dllSha256'])
        if expected.status != 'passed':
            raise ValueError('reader-current:' + repr(expected.issues))
        stage = NativeDeployedStage(window=ReadWindow(url, started, finished, datetime.now(UTC)),
            identity=anchor.identity, before_session=directory/'session-before', after_session=directory/'session-after',
            fixture_read=(fixture_request,directory/'fixture'), protected_read=(protected_request,directory/'protected'),
            reviewed_fixture=expected.data[0], protected_baseline=deepcopy(anchor.protected_baseline),
            diff_directory=directory/'diff', diff_request=anchor.diff_request, expected_state=expected_state,
            expected_plan=deepcopy(anchor.expected_plan), parsed_records=records, id_maps={}, roles=roles,
            native_layouts=layout_paths, native_library_path=anchor.native_library_path,
            native_sdk_directory=anchor.native_sdk_directory, protected_diffs=protected_diffs,
            native_session_evidence=(NativeSessionEvidence(directory/'discover-before', directory/'session-before',
                ((protected_request, directory/'root-before'), (protected_request, directory/'protected')),
                directory/'session-after', directory/'discover-after', source_session.panel_id)
                if source_session is not None else None))
        _save(directory / 'stage.json', asdict(stage))
        assessed = assess_deployed_stage(package, execution, stage)
        _save(directory / 'result.json', asdict(assessed))
        if assessed.status != 'passed':
            raise NativeReadFailure(str(directory) + ':' + repr(assessed.issues))
        return stage
    except NativeReadFailure:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _save(directory / 'result.json', asdict(Verdict('failed', ('reader-collection:' + str(exc),))))
        raise NativeReadFailure(str(directory) + ':' + str(exc)) from exc
