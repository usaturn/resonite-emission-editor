"""S: prevent unintended CLI operations, evidence loss and false success.

All subprocesses run an authored fake CLI. No test may contact Resonite.
"""
import json
import os
import stat
import sys

import pytest

from scripts.emission_editor.read_receipts import ReadRequest, collect_read, load_read


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    binary = tmp_path / "bin"
    binary.mkdir()
    stdout = tmp_path / "reply.bin"
    stdout.write_bytes('{\n "ok": true, "data": {"name": "試験用\\n材質"}\n}\n'.encode())
    stderr = tmp_path / "error.bin"
    stderr.write_bytes(b"diagnostic\n\xff\n")
    log = tmp_path / "calls.jsonl"
    executable = binary / "resoloop"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['FAKE_CALL_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.stdout.buffer.write(Path(os.environ['FAKE_STDOUT']).read_bytes())\n"
        "sys.stderr.buffer.write(Path(os.environ['FAKE_STDERR']).read_bytes())\n"
        "sys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n"
    )
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_STDOUT", str(stdout))
    monkeypatch.setenv("FAKE_STDERR", str(stderr))
    monkeypatch.setenv("FAKE_CALL_LOG", str(log))
    monkeypatch.setenv("FAKE_EXIT", "0")
    # A configured different destination must never override the explicit target.
    monkeypatch.setenv("RESONITE_LINK_URL", "ws://localhost:9")
    return stdout, stderr, log


def request():
    return ReadRequest("component-inspect", "ws://localhost:16070", "Reso_AB")


def test_raw_capture_pins_target_preserves_bytes_and_never_grants_permission(fake_cli, tmp_path):
    stdout, stderr, log = fake_cli
    output = tmp_path / "capture"
    result = collect_read(request(), output, cwd=tmp_path)
    assert result["transportSucceeded"] is True
    assert result["complete"] is False and result["executionPermission"] is False
    assert result["response"]["data"]["name"] == "試験用\n材質"
    assert (output / "stdout.bin").read_bytes() == stdout.read_bytes()
    assert (output / "stderr.bin").read_bytes() == stderr.read_bytes()
    args = json.loads(log.read_text())
    assert args[:3] == ["component", "inspect", "Reso_AB"]
    assert args[args.index("--url") + 1] == "ws://localhost:16070"
    assert load_read(output) == result
    # F∩S: raw world evidence must not become readable to other local users.
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE((output / name).stat().st_mode) == 0o600
               for name in ("receipt.json", "stdout.bin", "stderr.bin"))


def test_discover_url_is_bound_to_the_captured_invocation(fake_cli, tmp_path):
    # S: omitting discovery's URL permits a saved request to be silently relabelled.
    _, _, log = fake_cli
    output = tmp_path / "discovery"
    result = collect_read(ReadRequest("discover", "ws://localhost:16070"), output, cwd=tmp_path)
    assert result["transportSucceeded"] is True
    assert result["complete"] is False and result["executionPermission"] is False
    assert load_read(output) == result
    args = json.loads(log.read_text())
    assert args[0] == "discover"
    assert "--url" in args
    assert args[args.index("--url") + 1] == "ws://localhost:16070"

    path = output / "receipt.json"
    receipt = json.loads(path.read_text())
    receipt["request"]["url"] = "ws://localhost:16071"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="read-receipt-argv-mismatch"):
        load_read(output)


@pytest.mark.parametrize("bad", [
    ReadRequest("component-set", "ws://localhost:16070", "Reso_AB"),
    ReadRequest("component-inspect", "ws://localhost:16070", "--yes"),
    ReadRequest("component-inspect", "ws://other-host:16070", "Reso_AB"),
])
def test_unauthorized_operation_target_or_destination_never_starts_cli(bad, fake_cli, tmp_path):
    _, _, log = fake_cli
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        collect_read(bad, output, cwd=tmp_path)
    assert not log.exists()
    assert not output.exists()


def test_existing_evidence_is_not_reused_or_overwritten(fake_cli, tmp_path):
    _, _, log = fake_cli
    output = tmp_path / "capture"
    output.mkdir()
    kept = output / "stdout.bin"
    kept.write_bytes(b"previous evidence")
    with pytest.raises(FileExistsError):
        collect_read(request(), output, cwd=tmp_path)
    assert kept.read_bytes() == b"previous evidence"
    assert not log.exists()


@pytest.mark.parametrize("exit_code, payload", [
    (7, b'{"ok":true,"data":{}}'),
    (0, b'{"ok":false,"error":{"code":"READ_FAILED"}}'),
    (0, b"not a JSON reply"),
])
def test_transport_failures_are_preserved_without_reporting_success(
    exit_code, payload, fake_cli, tmp_path, monkeypatch
):
    stdout, _, log = fake_cli
    stdout.write_bytes(payload)
    monkeypatch.setenv("FAKE_EXIT", str(exit_code))
    output = tmp_path / "failed"
    result = collect_read(request(), output, cwd=tmp_path)
    assert result["transportSucceeded"] is False
    assert result["exitCode"] == exit_code
    assert result["complete"] is False and result["executionPermission"] is False
    assert (output / "stdout.bin").read_bytes() == payload
    assert len(log.read_text().splitlines()) == 1  # Never retry a failed read.
    assert load_read(output) == result


def test_changed_raw_response_cannot_replace_the_captured_observation(fake_cli, tmp_path):
    output = tmp_path / "capture"
    collect_read(request(), output, cwd=tmp_path)
    (output / "stdout.bin").write_bytes(b'{"ok":true,"data":{"changed":true}}')
    with pytest.raises(ValueError):
        load_read(output)


def test_changed_argv_cannot_relabel_a_read_as_another_target(fake_cli, tmp_path):
    output = tmp_path / "capture"
    collect_read(request(), output, cwd=tmp_path)
    path = output / "receipt.json"
    receipt = json.loads(path.read_text())
    receipt["argv"][2] = "Reso_CD"
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        load_read(output)


def test_boolean_exit_code_is_not_accepted_as_successful_integer_zero(fake_cli, tmp_path):
    output = tmp_path / "capture"
    collect_read(request(), output, cwd=tmp_path)
    path = output / "receipt.json"
    receipt = json.loads(path.read_text())
    receipt["exitCode"] = False
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        load_read(output)
