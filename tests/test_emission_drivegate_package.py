"""S: keep compilation off authoring/user files and reject unsealed execution inputs.

The fake CLI is an executable subprocess: like the real SDK it writes .brson and
build sidecars next to its input. No test contacts Resonite.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
import sys

import pytest

from scripts.emission_editor import drivegate_package as package


NAMES = ("DriveGateProbe", "ColorDrive", "BoolDrive")


@pytest.fixture
def compiler(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "resoloop"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version', '--json']:\n"
        "    data = {'version': '0.2.0'}\n"
        "elif args == ['flux', 'status', '--json']:\n"
        "    data = {'available': True, 'executable': 'flux-sdk', 'version': '1.9.0-test'}\n"
        "elif args[:2] in (['flux', 'check'], ['flux', 'build']):\n"
        "    assert args[3] == '--library-path' and args[5:] == ['--json']\n"
        "    source = Path(args[2])\n"
        "    assert source.is_absolute() and source.is_file()\n"
        "    if args[1] == 'build':\n"
        "        source.with_suffix('.pg.brson').write_bytes(b'compiled:' + source.read_bytes())\n"
        "        (source.parent / 'build').mkdir(exist_ok=True)\n"
        "        (source.parent / 'build' / (source.stem + '.map')).write_text('sdk-sidecar')\n"
        "    data = {'success': True, 'exitCode': 0, 'standardOutput': 'compiled', 'standardError': '', 'diagnostics': [], 'primaryDiagnostics': []}\n"
        "elif args[:2] == ['flux', 'validate-manifest']:\n"
        "    manifest = json.loads(Path(args[2]).read_text())\n"
        "    assert manifest['schemaVersion'] == '1'\n"
        "    assert len(manifest['modules']) == 1\n"
        "    for key in ('worldState', 'deployState'):\n"
        "        assert not Path(manifest[key]).exists()\n"
        "    data = {'valid': True, 'manifest': args[2], **{k: manifest[k] for k in ('projectDirectory', 'worldState', 'deployState')}, 'modules': [{**{k: m[k] for k in ('name', 'source', 'module', 'dependsOn')}, 'ports': len(m['bindings']), 'bindings': len(m['bindings'])} for m in manifest['modules']]}\n"
        "else:\n"
        "    raise AssertionError(args)\n"
        "mode = os.environ.get('FAKE_FAILURE', '')\n"
        "if mode == 'manifest' and args[:2] == ['flux', 'validate-manifest']: data['valid'] = 1\n"
        "if mode == 'execution-changed' and args[:2] == ['flux', 'validate-manifest']:\n"
        "    Path(manifest['modules'][0]['source'] + '.brson').write_bytes(b'changed-during-validation')\n"
        "if args[:2] == ['flux', 'build']:\n"
        "    if mode == 'exit':\n"
        "        print(json.dumps({'ok': True, 'data': data})); sys.exit(7)\n"
        "    if mode == 'envelope':\n"
        "        print(json.dumps({'ok': False, 'error': {'code': 'FAILED'}})); sys.exit(0)\n"
        "    if mode == 'compiler': data['success'] = False\n"
        "    if mode == 'nested-exit': data['exitCode'] = 3\n"
        "    if mode == 'no-record': Path(args[2] + '.brson').unlink()\n"
        "print(json.dumps({'ok': True, 'data': data}))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    library = tmp_path / "dlls"
    library.mkdir()
    (library / "FrooxEngine.dll").write_bytes(b'author-created-dll')
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    source = checkout / "authoring"
    source.mkdir()
    for name in NAMES:
        (source / (name + ".pg")).write_text("module " + name + "\n// 試験\n")
        (source / (name + ".pg.brson")).write_bytes(b'preexisting-record')
    (checkout / 'drivegate_package.py').write_bytes(Path(package.__file__).read_bytes())
    subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
    subprocess.run(['git', '-C', str(checkout), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(checkout), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    monkeypatch.setattr(package, 'REPO_ROOT', checkout)
    monkeypatch.setattr(package, 'SOURCE_ROOT', source)
    return source, library


def prepare(compiler, monkeypatch, destination):
    source, library = compiler
    monkeypatch.setattr(package, "SOURCE_ROOT", source)
    return package.prepare_package(destination, "trial-1", library_path=library)


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def walk(node):
    yield node
    for child in node.get('children', []):
        yield from walk(child)


def test_package_builds_copies_not_sources_and_never_grants_native_permission(compiler, monkeypatch, tmp_path):
    # Removing isolation would overwrite existing authoring records without error.
    source, _ = compiler
    before = snapshot(source)
    output = tmp_path / 'package'
    result = prepare(compiler, monkeypatch, output)
    assert snapshot(source) == before
    assert result['complete'] is False and result['executionPermission'] is False
    assert result['nativeSucceeded'] is False
    assert package.verify_package(output) == result
    assert len(list((output / 'sources').glob('*.pg.brson'))) == 3
    assert len(list((output / 'sources' / 'build').glob('*.map'))) == 3
    manifests = [json.loads(p.read_text()) for p in sorted((output / 'manifests').glob('*.json'))]
    assert len(manifests) == 3
    assert len({m['parent'] for m in manifests}) == 3
    states = [Path(m[key]) for m in manifests for key in ('worldState', 'deployState')]
    assert len(set(states)) == 6
    execution = tmp_path / 'package-execution'
    assert all(p.is_relative_to(execution) and not p.exists() for p in states)
    declaration = json.loads((output / 'fixture.json').read_text())
    assert declaration['slot']['name'] == 'ResoLoop_Test_EmissionEditor_Drivegate_trial-1'
    nodes = list(walk(declaration))
    keys = {c['key'] for n in nodes for c in n['components']}
    parents = {'$slot:' + n['slot']['key'] for n in nodes}
    allowed = {'[FrooxEngine]FrooxEngine.' + t for t in ('ObjectRoot', 'ValueField<int>', 'ValueField<bool>', 'ValueField<colorX>')}
    assert all(c['type'] in allowed for n in nodes for c in n['components'])
    for manifest in manifests:
        assert manifest['parent'] in parents
        assert Path(manifest['projectDirectory']).is_relative_to(execution)
        for mod in manifest['modules']:
            assert Path(mod['source']).is_relative_to(execution / 'sources')
            for binding in mod['bindings'].values():
                assert binding['target'].startswith('$member:')
                assert binding['target'][8:].split('.')[0] in keys
    # Fresh declarations cannot share mutable nested colour values between trials.
    another = package.fixture_declaration('trial-1')
    assert another == declaration
    first_color = next(c for n in walk(another) for c in n['components'] if c['type'].endswith('ValueField<colorX>'))
    first_color['initialFields']['Value'][0] = 99
    assert package.fixture_declaration('trial-1') == declaration


def test_existing_destination_preserves_user_files(compiler, monkeypatch, tmp_path):
    # Reusing an output directory must not overwrite an earlier attempt/user data.
    output = tmp_path / 'existing'
    output.mkdir()
    (output / 'fixture.json').write_bytes(b'user-owned')
    before = snapshot(output)
    with pytest.raises(FileExistsError):
        prepare(compiler, monkeypatch, output)
    assert snapshot(output) == before


@pytest.mark.parametrize('location', ['parent-traversal', 'linked-parent'])
def test_compiler_cannot_write_into_source_or_through_link(compiler, monkeypatch, tmp_path, location):
    source, _ = compiler
    protected = source
    if location == 'parent-traversal':
        # Representative source overlap: normalisation must precede containment.
        (tmp_path / 'neutral').mkdir()
        output = tmp_path / 'neutral' / '..' / 'checkout' / 'authoring' / 'new-package'
    else:
        # A different user-owned directory, not another alias of the source test.
        protected = tmp_path / 'user-files'
        protected.mkdir()
        (protected / 'keep.json').write_bytes(b'user-owned')
        link = tmp_path / 'alias'
        link.symlink_to(protected, target_is_directory=True)
        output = link / 'new-package'
    before = snapshot(protected)
    with pytest.raises(ValueError):
        prepare(compiler, monkeypatch, output)
    assert snapshot(protected) == before
    assert not (protected / 'new-package').exists()


def test_linked_source_is_not_compiled_or_copied(compiler, monkeypatch, tmp_path):
    source, _ = compiler
    original = source / 'ColorDrive.pg'
    original.unlink()
    outside = tmp_path / 'private.pg'
    outside.write_bytes(b'private-user-file')
    original.symlink_to(outside)
    with pytest.raises(ValueError):
        prepare(compiler, monkeypatch, tmp_path / 'package')
    assert outside.read_bytes() == b'private-user-file'
    assert not (tmp_path / 'package' / 'seal.json').exists()


@pytest.mark.parametrize('failure', ['exit', 'envelope', 'compiler', 'nested-exit', 'no-record', 'manifest', 'execution-changed'])
def test_failed_compilation_never_produces_a_success_seal(compiler, monkeypatch, tmp_path, failure):
    # Exit zero/envelope alone must not relabel compiler failure as success.
    monkeypatch.setenv('FAKE_FAILURE', failure)
    output = tmp_path / 'failed'
    with pytest.raises(ValueError):
        prepare(compiler, monkeypatch, output)
    assert not (output / 'seal.json').exists()
    with pytest.raises((ValueError, FileNotFoundError)):
        package.verify_package(output)


@pytest.mark.parametrize('tamper', ['record', 'seal', 'missing', 'unknown', 'link'])
def test_tampered_or_incomplete_package_is_not_accepted(compiler, monkeypatch, tmp_path, tamper):
    # The record represents the shared input/record/manifest hash guard (D).
    output = tmp_path / 'package'
    prepare(compiler, monkeypatch, output)
    target = output / 'sources' / 'ColorDrive.pg'
    if tamper in ('record', 'seal'):
        target = output / 'sources' / 'ColorDrive.pg.brson' if tamper == 'record' else output / 'seal.json'
        target.write_bytes(target.read_bytes() + b' ')
    elif tamper == 'missing':
        target.unlink()
    elif tamper == 'unknown':
        (output / 'unexpected.json').write_text('{}')
    elif tamper == 'link':
        outside = tmp_path / 'outside'
        target.rename(outside)
        target.symlink_to(outside)
    with pytest.raises(ValueError):
        package.verify_package(output)


def test_execution_builds_never_overwrite_sealed_expectations(compiler, monkeypatch, tmp_path):
    # Pointing a deploy manifest at the seal silently destroys its expected records.
    output = tmp_path / 'package'
    seal = prepare(compiler, monkeypatch, output)
    before = snapshot(output)
    manifests = [json.loads(p.read_text()) for p in (output / 'manifests').glob('*.json')]
    assert all(not Path(m['projectDirectory']).is_relative_to(output) for m in manifests)
    execution = tmp_path / 'package-execution'
    assert package.verify_execution(output, execution) == seal
    # Use precisely the sources/cwd a downstream deploy would use; no live deploy.
    for manifest in manifests:
        for module in manifest['modules']:
            subprocess.run(['resoloop', 'flux', 'build', module['source'], '--library-path',
                            str(compiler[1]), '--json'], cwd=manifest['projectDirectory'], check=True,
                           capture_output=True)
    assert snapshot(output) == before
    assert package.verify_execution(output, execution) == seal
    # A sealed package can be relocated without changing its inventory or paths.
    relocated = tmp_path / 'relocated-seal'
    shutil.copytree(output, relocated)
    assert package.verify_package(relocated) == seal
    assert package.verify_execution(relocated, execution) == seal
    # The execution copy must be compared, not just the original package verified.
    (execution / 'sources' / 'ColorDrive.pg.brson').write_bytes(b'wrong-but-plausible-record')
    with pytest.raises(ValueError):
        package.verify_execution(relocated, execution)
    assert snapshot(output) == before


def test_dirty_code_is_not_attributed_to_head(compiler, monkeypatch, tmp_path):
    # A clean commit ID must not silently vouch for uncommitted compiler inputs.
    source, _ = compiler
    (source / 'ColorDrive.pg').write_text('module ColorDrive\n// changed after commit\n')
    output = tmp_path / 'package'
    with pytest.raises(ValueError):
        prepare(compiler, monkeypatch, output)
    assert not (output / 'seal.json').exists()
