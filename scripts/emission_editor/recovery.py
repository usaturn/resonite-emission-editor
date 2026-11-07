"""Keep compiler sibling outputs inside an explicit deployment source directory."""
import json
import shutil
from pathlib import Path


def isolate_manifest(manifest_path, stage_root):
    manifest_path = Path(manifest_path).resolve()
    stage_root = Path(stage_root).resolve()
    stage_root.mkdir(parents=True, exist_ok=True)
    data = json.loads(manifest_path.read_text())
    for module in data['modules']:
        source = (manifest_path.parent / module['source']).resolve()
        target = stage_root / source.name
        if source != target:
            shutil.copy2(source, target)
        module['source'] = str(target)
    for key in ('worldState', 'deployState'):
        if key in data:
            data[key] = str((manifest_path.parent / data[key]).resolve())
    data['projectDirectory'] = str(stage_root)
    output = stage_root / manifest_path.name
    output.write_text(json.dumps(data, indent=2) + '\n')
    # Make the original entrypoint safe for direct CLI replay as well.
    if output != manifest_path:
        manifest_path.write_text(json.dumps(data, indent=2) + '\n')
    return output


def preserved_hashes(baseline, *, root):
    """F∩S frozen evidence and user-owned history must remain byte-identical.

    Relative names resolve against the explicit project root, never the cwd,
    and every target must stay inside that root (same rule as validate_context).
    """
    import hashlib
    root = Path(root).resolve()
    expected = json.loads(Path(baseline).read_text())
    actual = {}
    for name in expected:
        target = Path(name)
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
        if not target.is_relative_to(root):
            raise ValueError('path-outside-project-root')
        actual[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError('Preserved artifact changed: ' + ', '.join(name for name in expected if actual[name] != expected[name]))
    return actual
