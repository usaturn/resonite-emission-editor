"""S: omitted context must not revive a historical session or write outside the new root."""

import json
from dataclasses import replace

import pytest

from scripts.emission_editor.context import validate_context
from scripts.emission_editor.model import plan_registry
from scripts.emission_editor.resoloop_io import ResoloopReadPort, publish_registry
from test_emission_publish import FakePanelPort
from test_emission_registry import shared_capture


def test_live_cli_refuses_before_transport(tmp_path, monkeypatch):
    calls = []

    def transport(*args, **kwargs):
        calls.append(args)
        raise AssertionError("transport")

    monkeypatch.setattr("scripts.emission_editor.resoloop_io.subprocess.run", transport)
    from scripts.emission_editor.__main__ import main

    evidence = tmp_path / "evidence"
    code = main(
        [
            "register",
            "--url",
            "ws://127.0.0.1:9",
            "--output",
            str(tmp_path / "out" / "result.json"),
            "--avatar",
            "Avatar",
            "--state",
            str(tmp_path / "state.json"),
        ]
    )
    assert code != 0
    assert calls == [] and not evidence.exists()
    written = json.loads((tmp_path / "out" / "result.json").read_text())
    assert written["success"] is False
    assert "native-switch-not-approved" in written["error"]


def test_context_refuses_resources_outside_new_root(tmp_path, monkeypatch, authored_context):
    calls = []
    monkeypatch.setattr(
        "scripts.emission_editor.resoloop_io.subprocess.run",
        lambda *args, **kwargs: calls.append(args),
    )
    root = tmp_path / "project"
    fake_context = authored_context(root)
    validate_context(fake_context)
    outside = tmp_path / "outside-baseline.json"
    outside.write_text("{}\n")
    rejected = root / "rejected-evidence"
    with pytest.raises(ValueError):
        validate_context(replace(fake_context, preservation_baseline=outside))
    with pytest.raises(ValueError):
        ResoloopReadPort(
            replace(fake_context, preservation_baseline=outside), evidence=rejected
        )
    assert calls == [] and not rejected.exists()
    port = FakePanelPort()
    port.context = fake_context
    result = publish_registry(plan_registry(shared_capture()), port)
    assert result.success and result.ready
    assert port.avatar_writes == []
    assert port.old_recovery == {"baseline": "preserve", "candidate": "preserve"}
