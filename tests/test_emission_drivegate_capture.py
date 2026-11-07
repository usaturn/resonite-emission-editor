"""S: fixed read-only diff provenance must not bless another input or a failed CLI."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
import sys

from scripts.emission_editor import drivegate_capture as capture
from scripts.emission_editor.drivegate_evidence import ReadWindow


URL = "ws://localhost:16070"  # Authored fake endpoint, never connected.


def plan():
    return {"valid": True, "schemaVersion": "1", "ownershipKey": "private-owner",
            "stateFile": "REPLACED", "operations": [
                {"action": "create", "kind": "slot", "key": "private", "path": "Root/試験",
                 "atomic": False, "recovery": "checkpoint-and-reapply"}],
            "changes": [{"action": "create", "kind": "slot", "key": "private", "path": "Root/試験",
                         "atomic": False, "recovery": "checkpoint-and-reapply"}],
            "creates": 1, "updates": 0, "noOps": 0, "renames": 0, "deletes": 0,
            "atomic": False, "recovery": "checkpoint-and-reapply", "warnings": []}


def fake_cli(root, monkeypatch):
    # Real executable boundary: enforce the fixed read-only command, then emit
    # a complete CLI response or change a captured input to model a concurrent edit.
    executable = root / "resoloop"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
args = sys.argv[1:]
if len(args) != 12 or args[0] != 'diff' or args[2] != '--state' or args[4:] != ['--url', 'ws://localhost:16070', '--timeout', '10', '--command-timeout', '30', '--json', '--quiet']:
    sys.exit(99)
mode = os.environ.get('FAKE_DIFF_MODE', '')
if mode == 'declaration': pathlib.Path(args[1]).write_text('{"changed":true}')
if mode == 'state': pathlib.Path(args[3]).write_text('{"unexpected":true}')
data = json.loads(os.environ['FAKE_DIFF_DATA'])
data['stateFile'] = args[3]
print(json.dumps({'ok': True, 'data': data}, ensure_ascii=False))
print('diagnostic・stderr', file=sys.stderr)
sys.exit(7 if mode == 'exit' else 0)
''')
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(root) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_DIFF_DATA", json.dumps(plan(), ensure_ascii=False))


def test_fixed_diff_binds_complete_creation_plan_and_invocation_bytes(tmp_path, monkeypatch):
    # Removing the byte/state/window/complete-operation guards admits a plausible
    # plan for a different declaration, old state or hidden destructive operation.
    fake_cli(tmp_path, monkeypatch)
    declaration = tmp_path / "fixture.json"
    original = b'{"name":"private"}\n'
    declaration.write_bytes(original)
    state = tmp_path / "fresh-state.json"
    request = capture.DiffRequest(URL, declaration, state)
    started = datetime.now(UTC)
    directory = tmp_path / "capture"
    captured = capture.collect_diff(request, directory, cwd=tmp_path)
    finished = datetime.now(UTC)
    expected = plan() | {"stateFile": str(state)}
    args = dict(request=request, window=ReadWindow(URL, started, finished, finished + timedelta(seconds=1)),
                sealed_declaration=original, expected_state=None, expected_plan=expected)
    assert captured["transportSucceeded"] is True
    assert capture.assess_diff(directory, **args).status == "passed"
    assert (directory / "stderr.bin").read_bytes()  # CLI failure diagnostics survive.
    changed = deepcopy(expected)
    changed["operations"][0]["action"] = "delete"
    assert capture.assess_diff(directory, **(args | {"expected_plan": changed})).status == "failed"
    assert capture.assess_diff(directory, **(args | {"sealed_declaration": b"{}"})).status == "failed"
    assert capture.assess_diff(directory, **(args | {"expected_state": b"{}"})).status == "failed"
    wrong_window = ReadWindow(URL, finished + timedelta(seconds=1), finished + timedelta(seconds=2), finished + timedelta(seconds=3))
    assert capture.assess_diff(directory, **(args | {"window": wrong_window})).status == "failed"
    # Hash checks must catch output damage, not just trust the saved PASS boolean.
    (directory / "stdout.bin").write_bytes(b'{"ok":true,"data":{}}')
    assert capture.assess_diff(directory, **args).status != "passed"


def test_diff_failure_or_input_change_preserves_raw_but_cannot_pass(tmp_path, monkeypatch):
    # S: a nonzero real exit, or state created/changed during the read, must not
    # become successful evidence just because stdout contains valid JSON.
    fake_cli(tmp_path, monkeypatch)
    declaration = tmp_path / "fixture.json"
    declaration.write_bytes(b"{}")
    state = tmp_path / "state.json"
    for mode in ("exit", "declaration", "state"):
        declaration.write_bytes(b"{}")
        state.write_bytes(b'{"ownershipKey":"private-owner"}')
        monkeypatch.setenv("FAKE_DIFF_MODE", mode)
        started = datetime.now(UTC)
        request = capture.DiffRequest(URL, declaration, state)
        directory = tmp_path / mode
        capture.collect_diff(request, directory, cwd=tmp_path)
        finished = datetime.now(UTC)
        result = capture.assess_diff(directory, request=request,
            window=ReadWindow(URL, started, finished, finished), sealed_declaration=b"{}",
            expected_state=b'{"ownershipKey":"private-owner"}', expected_plan=plan() | {"stateFile": str(state)})
        assert result.status != "passed"
        assert (directory / "stdout.bin").read_bytes()
        assert (directory / "receipt.json").is_file()
