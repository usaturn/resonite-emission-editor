"""S: ambiguous CLI failure must not duplicate private writes or recapture Before.

Native authentication is stubbed at its module boundary here; these are NOT
native-stage integration tests or evidence of live execution.
"""
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import sys

import pytest

from test_emission_drivegate_evidence import phase_baseline
from scripts.emission_editor import drivegate_controller as controller
from scripts.emission_editor.drivegate_evidence import NativeIdentity, ReadWindow, Verdict, phase_expectations
from scripts.emission_editor.drivegate_runtime import NativeDeployedStage


def setup_controller(tmp_path, monkeypatch):
    package, execution = tmp_path / "package", tmp_path / "execution"
    package.mkdir()
    execution.mkdir()
    fields = phase_baseline()
    expected = phase_expectations(fields).data
    identity = NativeIdentity("Reso_AA", "Value", "世界・試験")

    def stage(current, *, session=identity, roles=("probe",)):
        now = datetime.now(UTC)
        value = NativeDeployedStage(ReadWindow("ws://localhost:16070", now, now, now), session,
            tmp_path / "before", tmp_path / "after", (None, tmp_path / "fixture"),
            (None, tmp_path / "protected"), {"id": "Reso_FA"},
            {"id": "Reso_BE", "name": "既存", "isReferenceOnly": False, "components": [], "children": []},
            tmp_path / "diff", None,
            b"original-state", {}, {}, {}, roles, {}, tmp_path / "library", tmp_path / "sdk")
        data[id(value)] = (deepcopy(current), {"drivegate-root": {"id": "Reso_FA"}})
        return value

    data = {}
    monkeypatch.setattr(controller, "assess_deployed_stage", lambda p, e, s: Verdict("passed", data=data[id(s)]))
    monkeypatch.setattr(controller, "verify_package", lambda p: {"codeCommit": "clean-code"})
    monkeypatch.setattr(controller, "_code_commit", lambda: "clean-code")
    # fixed fake CLI really runs as a process; all observations remain fake.
    executable = tmp_path / "resoloop"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, pathlib, sys
assert sys.argv[1:6] == ['component', 'set', 'Reso_CC', 'Value', '1']
p = pathlib.Path(os.environ['CALLS'])
n = int(p.read_text()) if p.exists() else 0
p.write_text(str(n + 1))
print(json.dumps({'ok': False, 'error': 'simulated lost acknowledgement'}))
sys.exit(1)
''')
    executable.chmod(0o700)
    calls = tmp_path / "calls"
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CALLS", str(calls))
    baseline = stage(fields)
    # Match the private component identity derived from the world checkpoint.
    state = {"components": {"drivegate-Command": {"id": "Reso_CC"}}}
    object.__setattr__(baseline, "expected_state", json.dumps(state).encode())
    baseline_bytes = baseline.expected_state

    def read(current, **kwargs):
        value = stage(current, **kwargs)
        object.__setattr__(value, "expected_state", baseline_bytes)
        return value

    args = dict(package=package, execution=execution, baseline=baseline, previous=baseline,
                command=1, backoff_seconds=0)
    return args, read, expected, calls, data


def test_lost_ack_applied_advances_once_and_freezes_original_before(tmp_path, monkeypatch):
    # S: retrying a timed-out successful capture can destroy its original Before.
    args, read, expected, calls, _ = setup_controller(tmp_path, monkeypatch)
    observations = iter((expected[0], expected[1]))
    result = controller.run_command(**args, observe=lambda: read(next(observations)))
    assert result["status"] == "applied" and result["application"] == "applied"
    assert calls.read_text() == "1" and not result["nativeSucceeded"]
    root = Path(result["directory"])
    assert json.loads((root / "attempt-1/process-result.json").read_bytes())["exitCode"] == 1
    assert (root / "baseline.json").exists()
    # A fresh invocation observing the same applied result must send nothing.
    again = controller.run_command(**args, observe=lambda: read(expected[1]))
    assert again["status"] == "applied" and calls.read_text() == "1"
    # The separate native request must retain the captured Before and Done=2,
    # not reinterpret the current A value as a new baseline.
    refusal = deepcopy(expected[2])
    refusal["Command"]["value"] = 1
    observations = iter((expected[2], refusal))
    recapture = controller.run_command(**(args | {"previous": read(expected[2]), "command": "recapture"}),
                                      observe=lambda: read(next(observations)))
    assert recapture["status"] == "applied" and calls.read_text() == "2"


def test_confirmed_unapplied_retries_in_new_attempt_without_overwriting_failure(tmp_path, monkeypatch):
    # S: failure must not be success; a proven unapplied mutation may retry.
    args, read, expected, calls, _ = setup_controller(tmp_path, monkeypatch)
    observations = iter((expected[0], expected[0], expected[1]))
    result = controller.run_command(**args, observe=lambda: read(next(observations)))
    assert result["status"] == "applied" and calls.read_text() == "2"
    for n in (1, 2):
        receipt = Path(result["directory"]) / f"attempt-{n}/process-result.json"
        assert json.loads(receipt.read_bytes())["exitCode"] == 1


def test_partial_capture_and_foreign_session_are_never_replayed(tmp_path, monkeypatch):
    # S: Command/Done mismatch or session change cannot authorize another capture.
    args, read, expected, calls, _ = setup_controller(tmp_path, monkeypatch)
    partial = deepcopy(expected[0])
    partial["Command"]["value"] = 1
    partial["BeforeColor"]["value"] = deepcopy(expected[1]["BeforeColor"]["value"])
    partial["Captured"]["value"] = True
    result = controller.run_command(**args, observe=lambda: read(partial))
    assert result["status"] == "source-investigation" and result["application"] == "partial"
    assert not calls.exists()
    other = NativeIdentity("Reso_AA", "Value", "different-world")
    result = controller.run_command(**args, observe=lambda: read(expected[0], session=other))
    assert result["status"] == "source-investigation" and not calls.exists()
    def changed_protection():
        current = read(expected[0])
        current.protected_baseline["name"] = "different protected scope"
        return current
    result = controller.run_command(**args, observe=changed_protection)
    assert result["application"] == "unknown" and not calls.exists()
    # Successful expected-field shape alone cannot replace the original baseline.
    changed = deepcopy(expected[0])
    changed["ColorTarget"]["value"]["r"] += 1
    replacement = read(changed)
    with pytest.raises(ValueError, match="baseline-binding"):
        controller.run_command(**(args | {"baseline": replacement, "previous": replacement}),
                               observe=lambda: read(changed))


def test_writer_lock_and_unconfirmed_launch_do_not_send_a_second_writer(tmp_path, monkeypatch):
    # S: another writer (including a crashed launch with unknown PID) must not overlap.
    import fcntl
    args, read, expected, calls, _ = setup_controller(tmp_path, monkeypatch)
    journal = controller.journal_directory(args["execution"])
    journal.mkdir()
    lock = (journal / "writer.lock").open("a+b")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(BlockingIOError):
        controller.run_command(**args, observe=lambda: read(expected[0]))
    lock.close()
    result = controller.run_command(**args, observe=lambda: read(expected[1]))
    old = Path(result["directory"]) / "attempt-1"
    old.mkdir()
    (old / "intent.json").write_text('{}')
    result = controller.run_command(**args, observe=lambda: read(expected[0]))
    assert result["status"] == "source-investigation" and result["application"] == "unknown"
    assert not calls.exists()
    (old / "process.json").write_text(json.dumps({"pid": os.getpid()}))
    monkeypatch.setattr(controller, "_group_stopped", lambda pid: False)
    result = controller.run_command(**args, observe=lambda: read(expected[0]))
    assert result["application"] == "unknown" and not calls.exists()
    # Even after stopping the prior process, reusing its old stage is not a read.
    monkeypatch.setattr(controller, "_group_stopped", lambda pid: True)
    stale = read(expected[0])
    result = controller.run_command(**args, observe=lambda: stale)
    assert result["application"] == "unknown" and not calls.exists()
