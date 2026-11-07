"""Explicit project locations. Historical URL and state are not defaults."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class ProjectContext:
    project_root: Path
    source_root: Path
    registry_template: Path
    world_state: Path
    flux_state: Path
    preservation_baseline: Path
    url: str
    owner: str
    root_name: str


def _resolved_inside(root: Path, path: Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("path-outside-project-root")
    return resolved


def _existing_ancestor(path: Path) -> Path:
    current = Path(path).resolve()
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise ValueError("ancestor-outside-project-root")
        current = parent
    return current


def validate_context(context: ProjectContext) -> None:
    """Reject missing, escaped, or historically implied project resources."""
    root = Path(context.project_root).resolve()
    if not root.is_dir():
        raise ValueError("project-root-missing")
    source = _resolved_inside(root, context.source_root)
    if not source.is_dir():
        raise ValueError("source-root-missing")
    template = _resolved_inside(root, context.registry_template)
    if not template.is_file():
        raise ValueError("registry-template-missing")
    baseline_path = _resolved_inside(root, context.preservation_baseline)
    if not baseline_path.is_file():
        raise ValueError("preservation-baseline-missing")
    if Path(context.world_state).resolve() == Path(context.flux_state).resolve():
        raise ValueError("state-paths-not-distinct")
    for path in (context.world_state, context.flux_state):
        ancestor = _existing_ancestor(path)
        if not ancestor.is_relative_to(root):
            raise ValueError("ancestor-outside-project-root")
    url = context.url
    if not (url.startswith("ws://") or url.startswith("wss://")):
        raise ValueError("url-not-explicit-websocket")
    if url in {"ws://", "wss://"} or url.endswith("://"):
        raise ValueError("url-not-explicit-websocket")
    if context.owner != "tools-emission-editor":
        raise ValueError("owner-not-allowed")
    if context.root_name != "ResoLoop_Test_EmissionEditor":
        raise ValueError("root-name-not-allowed")
    try:
        mapping = json.loads(baseline_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError("preservation-baseline-unreadable") from exc
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("empty-preservation-baseline")
    for name, digest in mapping.items():
        if not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("preservation-baseline-malformed")
        target = Path(name)
        if not target.is_absolute():
            target = root / target
        resolved = _resolved_inside(root, target)
        if not resolved.is_file():
            raise ValueError("baseline-target-missing")
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != digest:
            raise ValueError("baseline-hash-mismatch")
