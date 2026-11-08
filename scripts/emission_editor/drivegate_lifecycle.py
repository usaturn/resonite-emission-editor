"""Internal fixed create/first-probe subset, not full preflight or a live CLI.

Only our stopped fixed apply/deploy-probe process plus its state snapshot and
newly validated native transition can establish applied creation/deployment.
Neither an observed fixture nor existing state is adopted as process ownership.
Helper deployment/removal/restored cleanup and designated review are still owed.
"""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import json
from pathlib import Path
import time

from .drivegate_bootstrap import native_ids, require_created_transition, require_probe_transition
from .drivegate_controller import _encode, _execute_attempt, _new, _new_bytes, _unresolved, journal_directory
from .drivegate_evidence import _strict_equal, _tree, compare_subtree
from .drivegate_identity import NativeSessionEvidence, assess_native_session
from .drivegate_package import _code_commit, _no_links, verify_package
from .drivegate_protection import ProtectedScope, compare_protection, compare_protection_scope
from .drivegate_runtime import CreatedStage, NativeDeployedStage, PrecreateScope, assess_precreate_history, assess_precreate_scope
from .read_receipts import ReadRequest, load_read


@dataclass(frozen=True)
class CreationScope:
    """Precreate scope AND source session evidence; neither alone permits apply."""
    scope: PrecreateScope
    native_session_evidence: NativeSessionEvidence


def _checked_scope(package, execution, observation, *, fresh):
    if not isinstance(observation, CreationScope) or not isinstance(observation.scope, PrecreateScope):
        raise ValueError('create-typed-precreate-required')
    scope, evidence = observation.scope, observation.native_session_evidence
    if not isinstance(evidence, NativeSessionEvidence) or not isinstance(scope.protected_baseline, ProtectedScope):
        raise ValueError('create-source-session-required')
    if (evidence.before_session != scope.before_session or evidence.after_session != scope.after_session
            or len(evidence.reads) != 2 or scope.parent_read not in evidence.reads
            or any(r != ReadRequest('inspect', scope.window.url, 'Root', 64) for r, _ in evidence.reads)):
        raise ValueError('create-source-bracket-binding')
    assess = assess_precreate_scope if fresh else assess_precreate_history
    result = assess(package, execution, scope)
    session = assess_native_session(evidence, window=scope.window, identity=scope.identity)
    if result.status != 'passed' or session.status != 'passed':
        raise ValueError('create-precreate-or-session:' + repr((*result.issues, *session.issues)))
    name = verify_package(package)['normalizedFixture']['slot']['name']
    for _, directory in evidence.reads:
        root = load_read(directory)['response']['data']
        slots, _, issues = _tree(root)
        if issues or any(node['name'] == name for node, _ in slots.values()):
            raise ValueError('create-absence-in-both-roots-required')
        if compare_protection(scope.protected_baseline, root).status != 'passed':
            raise ValueError('create-both-root-protection')
    return scope


def _same_scope(original, current):
    if (current.identity != original.identity or current.window.url != original.window.url
            or current.fixture_diff.request != original.fixture_diff.request
            or current.fixture_diff.expected_state is not None
            or current.fixture_diff.sealed_declaration != original.fixture_diff.sealed_declaration
            or not _strict_equal(current.fixture_diff.expected_plan, original.fixture_diff.expected_plan)
            or not _strict_equal(current.reviewed_parent, original.reviewed_parent)
            or current.parsed_records != original.parsed_records
            or compare_protection_scope(original.protected_baseline, current.protected_baseline).status != 'passed'):
        raise ValueError('create-original-scope-changed')


def _original_ids_absent(original, tree):
    created_ids = native_ids(tree, unique=True)
    for _, directory in original.native_session_evidence.reads:
        if native_ids(load_read(directory)['response']['data']) & created_ids:
            raise ValueError('create-original-native-id-borrowed')


def _original_created(package, execution, original, stage):
    require_created_transition(package, execution, original.scope, stage)
    _original_ids_absent(original, stage.reviewed_fixture)
    if stage.native_session_evidence.panel_id != original.native_session_evidence.panel_id:
        raise ValueError('create-original-session-producer-changed')


def _state_bytes(path):
    _no_links(path)
    return path.read_bytes() if path.exists() else None


def _root_receipt(directory):
    """Persist original read identities too, not just its decoded tree or path."""
    load_read(directory)
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in ('receipt.json', 'stdout.bin', 'stderr.bin')}


def _create_argv(execution, state, url):
    ReadRequest('doctor', url).argv()  # same strict loopback URL validation
    return ['resoloop', 'apply', str(execution / 'fixture.json'), '--state', str(state),
            '--url', url, '--timeout', '10', '--command-timeout', '30', '--json']


def _creation_binding(seal, package, execution, original):
    return {'operation': 'create', 'codeCommit': seal['codeCommit'],
            'package': str(package), 'execution': str(execution), 'original': asdict(original)}


def _process_provenance(journal, binding, argv, stage, *, operation='create', new_ids=None):
    """Authenticate our latest fixed launch/output, not another operation/state.

    A crash before the output snapshot is durable is unknown, not a replay permit.
    Whole-state consistent hostile journal rewriting is outside the threat model.
    """
    if operation not in ('create', 'deploy-probe'):
        raise ValueError('lifecycle-fixed-provenance-operation')
    candidates = []
    for checkpoint in journal.glob(f'run-*/attempt-*/{operation}-checkpoint.json'):
        attempt = checkpoint.parent
        recorded = json.loads(checkpoint.read_bytes())
        if recorded['bindingSha256'] != binding:
            continue
        intent = json.loads((attempt / 'intent.json').read_bytes())
        process = json.loads((attempt / 'process-result.json').read_bytes())
        if (intent['argv'] != argv or intent['cwd'] != str(stage.diff_request.declaration.parent)
                or process['groupStopped'] is not True or 'launchError' in process):
            continue
        ended = datetime.fromisoformat(process['finishedAt'])
        candidates.append((ended, attempt, recorded))
    if not candidates:
        raise ValueError(operation + '-missing-process-provenance')
    ended, attempt, recorded = max(candidates, key=lambda item: item[0])
    snapshot = attempt / ('created-state.bin' if operation == 'create' else 'deploy-probe-state.bin')
    if stage.window.started < ended or snapshot.read_bytes() != stage.expected_state:
        raise ValueError(operation + '-process-state-or-window')
    process = json.loads((attempt / 'process-result.json').read_bytes())
    for name in ('stdout', 'stderr'):
        if hashlib.sha256((attempt / (name + '.bin')).read_bytes()).hexdigest() != process[name + 'Sha256']:
            raise ValueError(operation + '-process-raw-changed')
    created_ids = native_ids(stage.reviewed_fixture, unique=True) if new_ids is None else new_ids
    for root in recorded['roots']:
        directory = Path(root['directory'])
        if _root_receipt(directory) != root['receiptSha256']:
            raise ValueError(operation + '-prewrite-root-changed')
        if native_ids(load_read(directory)['response']['data']) & created_ids:
            raise ValueError(operation + '-prewrite-native-id-borrowed')
    return str(attempt)


def run_create(*, package: Path, execution: Path, original: CreationScope, observe,
               backoff_seconds: float = 1.0) -> dict:
    """Observe/classify/fixed apply/readback under Command's SAME writer lock.

    The read-only observe() returns a NEW CreationScope (absent) or CreatedStage
    (present). It must use original expectations, never adopt a found fixture.
    Partial/unknown observations return source-investigation for diagnosis and
    recovery; confirmed absence with NO state may retry in a separate attempt.
    Full preflight/current toolchain/driver/package/designated review remain the
    caller's responsibility. This internal subset is not connected to live CLI.
    """
    if type(backoff_seconds) not in (int, float) or not 0 <= backoff_seconds <= 30:
        raise ValueError('create-backoff-range')
    package, execution = Path(package), Path(execution)
    journal = journal_directory(execution)
    _no_links(package)
    if not package.is_absolute() or package != package.resolve(strict=True):
        raise ValueError('create-canonical-package-required')
    _no_links(journal)
    journal.mkdir(mode=0o700, exist_ok=True)
    lock_path = journal / 'writer.lock'
    _no_links(lock_path)
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seal = verify_package(package)
        if seal['codeCommit'] != _code_commit():
            raise ValueError('create-current-clean-code-required')
        scope = _checked_scope(package, execution, original, fresh=False)
        state = scope.fixture_diff.request.state
        manifest = json.loads((package / 'manifests/probe.json').read_bytes())
        if (state != Path(manifest['worldState']) or scope.fixture_diff.request.declaration != execution / 'fixture.json'
                or scope.fixture_diff.expected_state is not None):
            raise ValueError('create-fixed-fixture-and-state-required')
        _no_links(state)
        argv = _create_argv(execution, state, scope.window.url)
        binding = _creation_binding(seal, package, execution, original)
        encoded_binding = _encode(binding)
        binding_sha256 = hashlib.sha256(encoded_binding).hexdigest()
        binding_path = journal / 'creation-baseline.json'
        _no_links(binding_path)
        if binding_path.exists():
            if binding_path.read_bytes() != encoded_binding:
                raise ValueError('create-original-binding-changed')
        else:
            _new(binding_path, binding)
        number = 1
        while (journal / f'run-{number}').exists():
            number += 1
        run = journal / f'run-{number}'
        run.mkdir(mode=0o700)
        _new(run / 'creation-baseline.json', binding)

        def finish(status, application, **details):
            result = {'status': status, 'application': application, 'operation': 'create',
                      'directory': str(run), 'complete': False, 'executionPermission': False,
                      'nativeSucceeded': False, 'issueHalted': False, **details}
            _new(run / 'result.json', result)
            return result

        pending = _unresolved(journal)
        if pending:
            return finish('source-investigation', 'unknown', unresolvedProcess=pending)
        attempts, unconfirmed, observations = 0, 0, 0
        while True:
            observations += 1
            started = datetime.now(UTC)
            try:
                before = _state_bytes(state)
                current = observe()
                _new(run / f'observation-{observations}.json', asdict(current))
                if _state_bytes(state) != before:
                    raise ValueError('create-state-changed-during-observation')
                window = current.scope.window if isinstance(current, CreationScope) else current.window
                if (window.started < started or window.finished > datetime.now(UTC)
                        or window.started < scope.window.finished):
                    raise ValueError('create-replayed-or-future-observation')
                if type(current) is CreatedStage:
                    _original_created(package, execution, original, current)
                    if before != current.expected_state:
                        raise ValueError('create-current-state-mismatch')
                    provenance = _process_provenance(journal, binding_sha256, argv, current)
                    return finish('applied', 'applied', observations=observations, attempts=attempts,
                                  processEvidence=provenance)
                fresh = _checked_scope(package, execution, current, fresh=True)
                _same_scope(scope, fresh)
                if current.native_session_evidence.panel_id != original.native_session_evidence.panel_id:
                    raise ValueError('create-original-session-producer-changed')
                if before is not None:
                    application = 'partial'
                else:
                    application = 'unapplied'
                signature = application
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                application, signature = 'unknown', type(exc).__name__ + ':' + str(exc)
                _new(run / f'observation-{observations}-error.json', {'error': signature})
            if application != 'unapplied':
                unconfirmed += 1
                if unconfirmed >= 2:
                    return finish('source-investigation', application, signature=signature,
                                  observations=observations, attempts=attempts)
                time.sleep(backoff_seconds)
                continue
            unconfirmed = 0
            if attempts >= 2:
                return finish('source-investigation', 'unapplied', observations=observations, attempts=attempts)
            attempts += 1
            attempt = run / f'attempt-{attempts}'
            attempt.mkdir(mode=0o700)
            # Bind the immediate prewrite Root as well as the immutable ORIGINAL
            # absence-before scope, so newly appearing foreign IDs cannot be used.
            roots = [{'directory': str(directory), 'receiptSha256': _root_receipt(directory)}
                     for _, directory in current.native_session_evidence.reads]
            _new(attempt / 'create-checkpoint.json', {'bindingSha256': binding_sha256, 'roots': roots})
            state.parent.mkdir(parents=True, exist_ok=True)
            if _state_bytes(state) is not None:
                return finish('source-investigation', 'unknown', signature='create-state-appeared-before-launch')
            process = _execute_attempt(attempt, argv, execution, lock, observation=observations)
            if process['groupStopped'] is not True:
                return finish('source-investigation', 'unknown', unresolvedProcess=str(attempt))
            output = _state_bytes(state)
            if output is not None:
                _new_bytes(attempt / 'created-state.bin', output)
            time.sleep(backoff_seconds)


def run_deploy_probe(*, package: Path, execution: Path, original: CreationScope,
                     created: CreatedStage, observe, backoff_seconds: float = 1.0) -> dict:
    """Deploy ONLY the first probe to our proven created fixture's empty parent.

    observe() returns a fresh CreatedStage or collect_probe_anchor's authenticated
    NativeDeployedStage. The sealed probe manifest is the ONLY deployment input.
    Present output needs our stopped process AND full native transition/new-ID
    checks; a missing module with partial state is never blindly redeployed.
    This is not helper deployment, full preflight or a live command-line entry.
    """
    if type(backoff_seconds) not in (int, float) or not 0 <= backoff_seconds <= 30:
        raise ValueError('probe-backoff-range')
    package, execution = Path(package), Path(execution)
    journal = journal_directory(execution)
    _no_links(package)
    if not package.is_absolute() or package != package.resolve(strict=True):
        raise ValueError('probe-canonical-package-required')
    _no_links(journal)
    journal.mkdir(mode=0o700, exist_ok=True)
    lock_path = journal / 'writer.lock'
    _no_links(lock_path)
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seal = verify_package(package)
        if seal['codeCommit'] != _code_commit():
            raise ValueError('probe-current-clean-code-required')
        scope = _checked_scope(package, execution, original, fresh=False)
        _original_created(package, execution, original, created)
        create_binding = _encode(_creation_binding(seal, package, execution, original))
        if (journal / 'creation-baseline.json').read_bytes() != create_binding:
            raise ValueError('probe-original-create-binding-changed')
        create_hash = hashlib.sha256(create_binding).hexdigest()
        state = created.diff_request.state
        create_argv = _create_argv(execution, state, scope.window.url)
        _process_provenance(journal, create_hash, create_argv, created)
        manifest_path = execution / 'manifests/probe.json'
        manifest = json.loads((package / 'manifests/probe.json').read_bytes())
        flux_state = Path(manifest['deployState'])
        if state != Path(manifest['worldState']) or created.diff_request != scope.fixture_diff.request:
            raise ValueError('probe-original-world-state-required')
        _no_links(flux_state)
        ReadRequest('doctor', scope.window.url).argv()
        argv = ['resoloop', 'flux', 'deploy-manifest', str(manifest_path),
                '--url', scope.window.url, '--timeout', '10', '--command-timeout', '30', '--json']
        binding = {'operation': 'deploy-probe', 'creationBindingSha256': create_hash,
                   'created': asdict(created)}
        encoded = _encode(binding)
        binding_hash = hashlib.sha256(encoded).hexdigest()
        path = journal / 'probe-baseline.json'
        _no_links(path)
        if path.exists():
            if path.read_bytes() != encoded:
                raise ValueError('probe-original-created-binding-changed')
        else:
            _new(path, binding)
        number = 1
        while (journal / f'run-{number}').exists():
            number += 1
        run = journal / f'run-{number}'
        run.mkdir(mode=0o700)
        _new(run / 'probe-baseline.json', binding)

        def finish(status, application, **details):
            result = {'status': status, 'application': application, 'operation': 'deploy-probe',
                      'directory': str(run), 'complete': False, 'executionPermission': False,
                      'nativeSucceeded': False, 'issueHalted': False, **details}
            _new(run / 'result.json', result)
            return result

        pending = _unresolved(journal)
        if pending:
            return finish('source-investigation', 'unknown', unresolvedProcess=pending)
        attempts, unconfirmed, observations = 0, 0, 0
        old_ids = native_ids(created.reviewed_fixture, unique=True)
        while True:
            observations += 1
            started = datetime.now(UTC)
            try:
                before = (_state_bytes(state), _state_bytes(flux_state))
                if before[0] != created.expected_state:
                    raise ValueError('probe-original-state-changed')
                current = observe()
                _new(run / f'observation-{observations}.json', asdict(current))
                if (_state_bytes(state), _state_bytes(flux_state)) != before:
                    raise ValueError('probe-state-changed-during-observation')
                if (current.window.started < started or current.window.finished > datetime.now(UTC)
                        or current.window.started < created.window.finished):
                    raise ValueError('probe-replayed-or-future-observation')
                if current.native_session_evidence.panel_id != original.native_session_evidence.panel_id:
                    raise ValueError('probe-original-session-producer-changed')
                if type(current) is NativeDeployedStage:
                    require_probe_transition(package, execution, scope, created, current)
                    _original_ids_absent(original, current.reviewed_fixture)
                    added = native_ids(current.reviewed_fixture, unique=True) - old_ids
                    provenance = _process_provenance(journal, binding_hash, argv, current,
                        operation='deploy-probe', new_ids=added)
                    recorded = Path(provenance)
                    status = json.loads((recorded / 'flux-state-status.json').read_bytes())
                    if (status != {'present': before[1] is not None}
                            or (before[1] is not None and (recorded / 'flux-state.bin').read_bytes() != before[1])):
                        raise ValueError('probe-process-deploy-state-changed')
                    return finish('applied', 'applied', observations=observations, attempts=attempts,
                                  processEvidence=provenance)
                _original_created(package, execution, original, current)
                if (current.expected_state != created.expected_state
                        or current.diff_request != created.diff_request
                        or not _strict_equal(current.expected_plan, created.expected_plan)
                        or compare_subtree(created.reviewed_fixture, current.reviewed_fixture).status != 'passed'):
                    raise ValueError('probe-original-created-checkpoint-changed')
                application = 'unapplied' if before[1] is None else 'partial'
                signature = application
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                application, signature = 'unknown', type(exc).__name__ + ':' + str(exc)
                _new(run / f'observation-{observations}-error.json', {'error': signature})
            if application != 'unapplied':
                unconfirmed += 1
                if unconfirmed >= 2:
                    return finish('source-investigation', application, signature=signature,
                                  observations=observations, attempts=attempts)
                time.sleep(backoff_seconds)
                continue
            unconfirmed = 0
            if attempts >= 2:
                return finish('source-investigation', 'unapplied', observations=observations, attempts=attempts)
            attempts += 1
            attempt = run / f'attempt-{attempts}'
            attempt.mkdir(mode=0o700)
            roots = [{'directory': str(directory), 'receiptSha256': _root_receipt(directory)}
                     for _, directory in current.native_session_evidence.reads]
            _new(attempt / 'deploy-probe-checkpoint.json', {'bindingSha256': binding_hash, 'roots': roots})
            flux_state.parent.mkdir(parents=True, exist_ok=True)
            if _state_bytes(state) != created.expected_state or _state_bytes(flux_state) is not None:
                return finish('source-investigation', 'unknown', signature='probe-state-changed-before-launch')
            process = _execute_attempt(attempt, argv, execution, lock, observation=observations)
            if process['groupStopped'] is not True:
                return finish('source-investigation', 'unknown', unresolvedProcess=str(attempt))
            output = _state_bytes(state)
            if output is not None:
                _new_bytes(attempt / 'deploy-probe-state.bin', output)
            output = _state_bytes(flux_state)
            _new(attempt / 'flux-state-status.json', {'present': output is not None})
            if output is not None:
                _new_bytes(attempt / 'flux-state.bin', output)
            time.sleep(backoff_seconds)
