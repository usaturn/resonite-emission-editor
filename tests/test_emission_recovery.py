"""S: compiler success must not overwrite preserved authoring-side binary artifacts."""
import importlib
import json
import subprocess
import sys
from pathlib import Path


def test_compiler_sibling_output_stays_in_isolated_tree(tmp_path):
    source = tmp_path / 'author' / 'Graph.pg'
    source.parent.mkdir()
    source.write_text('module Graph\nwhere {}\n')
    original = Path(str(source) + '.brson')
    original.write_bytes(b'preserved-deploy-record')
    manifest = source.parent / 'manifest.json'
    manifest.write_text(json.dumps({'projectDirectory': '.', 'worldState': 'world.json', 'deployState': 'flux.json', 'modules': [{'name': 'graph', 'source': 'Graph.pg'}]}))
    recovery = importlib.import_module('scripts.emission_editor.recovery')
    prepared = recovery.isolate_manifest(manifest, tmp_path / 'isolated')
    data = json.loads(prepared.read_text())
    compiled_source = (prepared.parent / data['modules'][0]['source']).resolve()
    subprocess.run([sys.executable, '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]+".brson").write_bytes(b"compiled")', str(compiled_source)], check=True)
    assert Path(str(compiled_source) + '.brson').read_bytes() == b'compiled'
    assert compiled_source.read_bytes() == source.read_bytes()
    assert original.read_bytes() == b'preserved-deploy-record'
    assert compiled_source != source
    assert Path(data['projectDirectory']).resolve().is_relative_to(tmp_path / 'isolated')


def test_main_registration_isolates_compiler_and_keeps_failed_ui_closed(tmp_path, monkeypatch):
    """S: main registration must not compile into author history or publish partial UI."""
    from test_emission_registry import shared_capture
    from scripts.emission_editor.model import plan_registry
    from scripts.emission_editor.ui import UIPanelPort
    import pytest

    project = Path(__file__).resolve().parents[1]
    author = tmp_path / 'flux/emission-editor'
    author.mkdir(parents=True)
    for source in (project / 'flux/emission-editor').rglob('*.pg'):
        target = author / source.relative_to(project / 'flux/emission-editor')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        Path(str(target) + '.brson').write_bytes(b'preserved')
    library = tmp_path / 'library'
    library.mkdir()
    monkeypatch.setenv('RESONITE_MANAGED_DATA_PATH', str(library))
    monkeypatch.chdir(tmp_path)
    # render_registry intentionally starts from the historical storage declaration.
    registry = tmp_path / 'content/emission-editor/registry.json'
    registry.parent.mkdir(parents=True)
    registry.write_bytes((project / 'content/emission-editor/registry.json').read_bytes())
    capture = shared_capture()
    plan = plan_registry(capture)
    import hashlib
    preservation = tmp_path / 'preservation.json'
    preservation.write_text(json.dumps({str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in author.rglob('*.brson')}))

    class Read:
        session = capture.session
        state = tmp_path / '.resoloop/state/dedicated-current12991.json'
        flux_state = tmp_path / '.resoloop/flux-state/dedicated-current12991.json'
        preservation_baseline = preservation

        def __init__(self, fail):
            from scripts.emission_editor.context import ProjectContext
            self.evidence = tmp_path / (fail or 'success')
            self.evidence.mkdir()
            self.fail = fail
            self.manifests = []
            self.context = ProjectContext(
                project_root=tmp_path,
                source_root=author,
                registry_template=registry,
                world_state=self.state,
                flux_state=self.flux_state,
                preservation_baseline=preservation,
                url="ws://127.0.0.1:9",
                owner="tools-emission-editor",
                root_name="ResoLoop_Test_EmissionEditor",
            )

        def run(self, *args):
            if args[0] == 'diff':
                return {'operations': [{'action': 'create', 'key': 'reg-' + plan.generation}]}
            if args[:2] == ('component', 'inspect'):
                row = next(row for row in plan.rows if row.material_ref == args[2])
                return {'members': {'EmissionColor': {'id': row.field_ref, 'value': row.color}}}
            if args[:2] == ('flux', 'deploy-manifest'):
                manifest = json.loads(Path(args[2]).read_text())
                self.manifests.append(manifest)
                for module in manifest['modules']:
                    source = Path(module['source'])
                    subprocess.run([sys.executable, '-c', 'from pathlib import Path;import sys;Path(sys.argv[1]+".brson").write_bytes(b"compiled")', str(source)], check=True)
                if self.fail == 'deploy':
                    return {'success': False, 'modules': []}
                return {'success': True, 'modules': [{'name': module['name'], 'moduleSlotIdAfter': 'owned-module', 'bindings': [{'name': name, 'selector': binding['target'], 'mode': binding['mode'], 'targetId': 'owned-member'} for name, binding in module['bindings'].items()]}]}
            return {}

    class Port(UIPanelPort):
        def _set(self, key, member, value):
            self.bindings[(key, member)] = value

        def _observe_owned(self, selectors):
            values = {}
            for index, row in enumerate(plan.rows):
                color = dict(row.color)
                if self.read.fail == 'semantic':
                    color['a'] = 0.99
                key = f'reg-{plan.generation}-row-{index}'
                values[(key + '-current-copy', 'WriteBack')] = {'value': self.read.fail == 'writeback'}
                values[(key + '-current-copy', 'Target')] = {
                    'targetId': 'external-field' if self.read.fail == 'target' else key + '-private-current'
                }
                values[(key + '-current', 'Value')] = {'id': key + '-private-current', 'value': color}
                values[(f'reg-{plan.generation}-row-{index}-source', 'Reference')] = {'targetId': row.field_ref}
            return {selector: values[selector] for selector in selectors}

    # S: successful deployment receipts must not publish a native read with wrong alpha.
    # S: removing either live preconnection check can silently write outside the row,
    # even though the just-applied declaration requested a safe private-only reader.
    for fail in ('deploy', 'semantic', False, 'writeback', 'target'):
        read = Read(fail)
        port = Port(read, capture)
        if fail:
            expected = ('ui-module-deploy-incomplete' if fail == 'deploy' else
                        'ui-current-copy-unsafe' if fail in ('writeback', 'target') else
                        'ui-native-source-mismatch')
            with pytest.raises(ValueError, match=expected):
                port.stage(plan)
        else:
            port.stage(plan)
        if fail in ('writeback', 'target'):
            assert not any(member == 'Source' for _, member in port.bindings)
            assert read.manifests == []
        else:
            assert all(port.bindings[(f'reg-{plan.generation}-row-{i}-current-copy', 'Source')] == row.field_ref
                       for i, row in enumerate(plan.rows))
        assert port.bindings[('ui-runtime-ready', 'Value')] is (not fail)
        assert getattr(port, 'ui_staged', None) == (None if fail else plan.generation)
        for manifest in read.manifests:
            assert Path(manifest['worldState']) == read.state
            assert Path(manifest['deployState']).parent == read.flux_state.parent
            assert Path(manifest['deployState']).stem.startswith(read.flux_state.stem + '-')
            assert Path(manifest['projectDirectory']).is_relative_to(read.evidence)
            for module in manifest['modules']:
                source = Path(module['source'])
                assert source.is_relative_to(read.evidence)
                expected = (author / 'current' / source.name
                            if module['module'] in ('Selection', 'RowView')
                            else author / source.name)
                assert source.read_bytes() == expected.read_bytes()
                assert Path(str(source) + '.brson').read_bytes() == b'compiled'
        assert all(binary.read_bytes() == b'preserved' for binary in author.rglob('*.brson'))


def test_main_transport_rejects_legacy_checkpoint_before_recording(tmp_path, monkeypatch):
    """S: an explicit context is required, and resources outside the new root are refused."""
    import hashlib
    import pytest
    from scripts.emission_editor.context import ProjectContext, validate_context
    from scripts.emission_editor.resoloop_io import ResoloopReadPort
    monkeypatch.chdir(tmp_path)
    root = tmp_path / 'project'
    source = root / 'flux'
    source.mkdir(parents=True)
    (source / 'Session.pg').write_text('module Session\n')
    template = root / 'registry.json'
    template.write_text('{"schemaVersion": "1", "children": []}\n')
    kept = root / 'kept.txt'
    kept.write_text('author-created')
    baseline = root / 'baseline.json'
    baseline.write_text(json.dumps({
        str(kept): hashlib.sha256(kept.read_bytes()).hexdigest(),
    }))
    world = root / 'state' / 'world.json'
    flux = root / 'state' / 'flux.json'
    world.parent.mkdir()
    good = ProjectContext(
        project_root=root, source_root=source, registry_template=template,
        world_state=world, flux_state=flux, preservation_baseline=baseline,
        url='ws://127.0.0.1:9', owner='tools-emission-editor',
        root_name='ResoLoop_Test_EmissionEditor',
    )
    validate_context(good)
    accepted = root / 'accepted'
    current = ResoloopReadPort(good, evidence=accepted)
    assert current.url == 'ws://127.0.0.1:9'
    assert current.state == world
    assert current.flux_state == flux
    assert accepted.is_dir()
    outside_world = tmp_path / 'old-world' / '.resoloop' / 'state' / 'x.json'
    for bad_world, bad_flux in ((outside_world, flux), (world, world)):
        bad = ProjectContext(
            project_root=root, source_root=source, registry_template=template,
            world_state=bad_world, flux_state=bad_flux, preservation_baseline=baseline,
            url='ws://127.0.0.1:9', owner='tools-emission-editor',
            root_name='ResoLoop_Test_EmissionEditor',
        )
        rejected = root / 'rejected'
        with pytest.raises(ValueError):
            validate_context(bad)
        with pytest.raises(ValueError):
            ResoloopReadPort(bad, evidence=rejected)
        assert not rejected.exists()


def test_preserved_hashes_resolve_against_root_not_cwd(tmp_path, monkeypatch):
    """S: a same-named file in the cwd must not stand in for the frozen file."""
    import hashlib
    import pytest
    from scripts.emission_editor.recovery import preserved_hashes
    root = tmp_path / 'project'
    other = tmp_path / 'elsewhere'
    root.mkdir()
    other.mkdir()
    (root / 'kept.txt').write_text('author-created')
    (other / 'kept.txt').write_text('author-created')
    baseline = root / 'baseline.json'
    digest = hashlib.sha256(b'author-created').hexdigest()
    baseline.write_text(json.dumps({'kept.txt': digest}))
    monkeypatch.chdir(other)
    assert preserved_hashes(baseline, root=root) == {'kept.txt': digest}
    (root / 'kept.txt').write_text('changed')
    with pytest.raises(RuntimeError):
        preserved_hashes(baseline, root=root)
    outside = tmp_path / 'outside.txt'
    outside.write_text('author-created')
    baseline.write_text(json.dumps({str(outside): digest}))
    with pytest.raises(ValueError):
        preserved_hashes(baseline, root=root)


def test_runtime_instances_cannot_replace_another_row(tmp_path):
    """S: SDK replacement uses parent/native module, not manifest logical names."""
    from test_emission_registry import shared_capture
    from scripts.emission_editor.model import plan_registry
    from scripts.emission_editor.ui import session_manifest, validate_runtime_destinations
    import copy
    import pytest
    root = Path(__file__).resolve().parents[1]
    bundle = session_manifest(
        plan_registry(shared_capture()), world_state=tmp_path/'world.json',
        deploy_state=tmp_path/'flux.json', source_directory=tmp_path/'sources',
        source_root=root / 'flux/emission-editor',
    )
    manifests = bundle['manifests']
    validate_runtime_destinations(manifests)
    rows = [m for m in manifests if m['modules'][0]['module'] == 'RowValue']
    assert len(rows) == 2 and rows[0]['parent'] != rows[1]['parent']
    bad = copy.deepcopy(rows)
    bad[1]['parent'] = bad[0]['parent']
    with pytest.raises(ValueError, match='duplicate-native-module-destination'):
        validate_runtime_destinations(bad)


def test_row_guard_ports_cover_every_registered_use():
    """S: a use left out of List_k/Index_k would be written without being checked."""
    from test_emission_registry import shared_capture
    from scripts.emission_editor.model import plan_registry
    from scripts.emission_editor.ui import row_guard_bindings
    plan = plan_registry(shared_capture())
    assert [len(row.uses) for row in plan.rows] == [4, 1]
    for index, row in enumerate(plan.rows):
        p = f'$member:reg-{plan.generation}-row-{index}'
        bindings = row_guard_bindings(plan, index)
        assert bindings['Provider'] == {'target': p + '-provider.Reference', 'mode': 'source'}
        pairs = {(bindings[f'List{k}']['target'], bindings[f'Index{k}']['target'],
                  bindings[f'List{k}']['mode'], bindings[f'Index{k}']['mode']) for k in range(16)}
        assert pairs == {(f'{p}-list-{u}.Reference', f'{p}-index-{u}.Value', 'source', 'source')
                         for u in range(len(row.uses))}


def test_native_current_reader_cannot_write_back_to_registered_material():
    """F∩S: WriteBack/Target protect external materials from a reverse private-field write."""
    from test_emission_registry import shared_capture
    from scripts.emission_editor.model import plan_registry
    from scripts.emission_editor.ui import render_panel
    plan = plan_registry(shared_capture())
    def components(node):
        yield from node.get('components', [])
        for child in node.get('children', []):
            yield from components(child)
    template = Path(__file__).resolve().parents[1] / 'content/emission-editor/registry.json'
    copies = [c for c in components(render_panel(plan, template=template)) if c['type'] == '[FrooxEngine]FrooxEngine.ValueCopy<colorX>']
    assert len(copies) == len(plan.rows)
    for index, copy in enumerate(copies):
        assert copy['fields']['WriteBack'] is False
        assert copy['fields']['Target'] == f'$member:reg-{plan.generation}-row-{index}-current.Value'
        assert 'Source' not in copy['fields']  # Source is bound only from the verified current capture.
