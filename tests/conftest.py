"""Shared author-created project context. Historical baselines are not defaults."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.emission_editor.context import ProjectContext


def _authored_context(root: Path) -> ProjectContext:
    """One author-created file inside a new root, with its real hash."""
    root.mkdir(parents=True, exist_ok=True)
    source = root / "flux"
    source.mkdir()
    (source / "Session.pg").write_text("module Session\n")
    template = root / "registry.json"
    template.write_text('{"schemaVersion": "1", "children": []}\n')
    kept = root / "kept.txt"
    kept.write_text("author-created\n")
    baseline = root / "baseline.json"
    baseline.write_text(
        json.dumps({"kept.txt": hashlib.sha256(kept.read_bytes()).hexdigest()})
    )
    state = root / "state"
    state.mkdir()
    return ProjectContext(
        project_root=root,
        source_root=source,
        registry_template=template,
        world_state=state / "world.json",
        flux_state=state / "flux.json",
        preservation_baseline=baseline,
        url="ws://127.0.0.1:9",
        owner="tools-emission-editor",
        root_name="ResoLoop_Test_EmissionEditor",
    )


@pytest.fixture
def authored_context():
    return _authored_context
