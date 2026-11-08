"""Internal, closed private Command 1..6/recapture executor, NOT a live CLI.

The caller still owes full preflight/designated review, fresh native reader,
helper deploy/removal evidence and final cleanup. No arbitrary argv/writer is
accepted. Every observation must pass NativeDeployedStage authentication. This
module's 'applied' is one command only, never native/full-Issue success.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from .drivegate_evidence import compare_subtree, compare_typed, phase_expectations
from .drivegate_package import _code_commit, _no_links, verify_package
from .drivegate_initial import compare_initial_fields
from .drivegate_runtime import NativeDeployedStage, assess_deployed_stage
from .read_receipts import ReadRequest, _write_new


def _encode(value):
    def default(item):
        if isinstance(item, (Path, datetime)):
            return str(item)
        if isinstance(item, bytes):
            return {"sha256": hashlib.sha256(item).hexdigest()}
        raise TypeError(type(item).__name__)
    return (json.dumps(value, default=default, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _new(path, value):
    with path.open("xb") as stream:
        stream.write(_encode(value))
        stream.flush()
        os.fsync(stream.fileno())


def journal_directory(execution: Path) -> Path:
    """One stable lock per canonical execution; outside immutable runtime inputs."""
    path = Path(execution)
    _no_links(path)
    if not path.is_absolute() or path != path.resolve(strict=True):
        raise ValueError("command-canonical-execution-required")
    return path.with_name(path.name + "-command-journal")


def _checked(package, execution, stage):
    if not isinstance(stage, NativeDeployedStage):
        raise ValueError("command-native-stage-required")
    result = assess_deployed_stage(package, execution, stage)
    if result.status != "passed":
        raise ValueError("command-native-stage:" + repr(result.issues))
    return result.data


def _equal(expected, actual):
    return (actual.keys() == expected.keys()
            and all(compare_typed(expected[k], actual[k]).status == "passed" for k in expected))


def _context(baseline, previous, stage, stable, original_stable):
    if (stage.identity != baseline.identity or stage.window.url != baseline.window.url
            or stage.reviewed_fixture["id"] != baseline.reviewed_fixture["id"]
            or stage.expected_state != previous.expected_state or stage.roles != previous.roles
            or compare_subtree(baseline.protected_baseline, stage.protected_baseline).status != "passed"
            or stable.keys() != original_stable.keys()
            or any(stable[k]["id"] != original_stable[k]["id"] for k in stable)):
        raise ValueError("command-context-or-ownership-change")


def _group_stopped(pid):
    try:
        os.killpg(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


def _unresolved(journal):
    """A lost launch/PID is unknown, never an absent/failed write assertion."""
    for intent in sorted(journal.glob("run-*/attempt-*/intent.json")):
        attempt = intent.parent
        result = attempt / "process-result.json"
        if result.exists():
            if json.loads(result.read_bytes())["groupStopped"] is not True:
                pid = json.loads((attempt / "process.json").read_bytes())["pid"]
                if not _group_stopped(pid):
                    return str(attempt)
            continue
        process = attempt / "process.json"
        if not process.exists() or not _group_stopped(json.loads(process.read_bytes())["pid"]):
            return str(attempt)
    return None


def run_command(*, package: Path, execution: Path, baseline: NativeDeployedStage,
                previous: NativeDeployedStage, command: int | str, observe,
                backoff_seconds: float = 1.0) -> dict:
    """Observe -> classify -> fixed synchronous write -> observe, under one lock.

    observe() must collect NEW original session-bracketed native evidence. It is
    read-only; this is the sole writer. Repeated unapplied/partial/unknown reads
    return source-investigation for the coordinating agent to fix/replan, NOT an
    Issue halt. No partial command is resent. The durable original baseline is
    immutable; incomplete process evidence is inspected before any new write.
    """
    if not (type(command) is int and 1 <= command <= 6) and command != "recapture":
        raise ValueError("command-fixed-step-required")
    if not isinstance(backoff_seconds, (int, float)) or not 0 <= backoff_seconds <= 30:
        raise ValueError("command-backoff-range")
    package, execution = Path(package), Path(execution)
    journal = journal_directory(execution)
    _no_links(journal)
    journal.mkdir(mode=0o700, exist_ok=True)
    lock_path = journal / "writer.lock"
    _no_links(lock_path)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        seal = verify_package(package)
        if seal["codeCommit"] != _code_commit():
            raise ValueError("command-current-clean-code-required")
        base_fields, base_stable = _checked(package, execution, baseline)
        if compare_initial_fields(seal, base_fields).status != "passed":
            raise ValueError("command-baseline-not-declared-initial")
        old_fields, old_stable = _checked(package, execution, previous)
        expectations = phase_expectations(base_fields)
        if expectations.status != "passed" or baseline.roles != ("probe",):
            raise ValueError("command-unused-native-baseline-required")
        if previous.window.started < baseline.window.started:
            raise ValueError("command-checkpoint-before-baseline")
        recapture = command == "recapture"
        number = 1 if recapture else command
        prior_phase = 2 if recapture else number - 1
        expected_previous = deepcopy(expectations.data[prior_phase])
        # After the separate recapture-refusal observation, next is phase 3.
        if number == 3 and old_fields["Command"]["value"] == 1:
            expected_previous["Command"]["value"] = 1
        roles = ("probe", "color", "bool") if number == 3 else ("probe", "bool") if number == 4 else ("probe",)
        if (previous.roles != roles or not _equal(expected_previous, old_fields)
                or previous.identity != baseline.identity or previous.window.url != baseline.window.url
                or compare_subtree(baseline.protected_baseline, previous.protected_baseline).status != "passed"
                or old_stable.keys() != base_stable.keys()
                or any(old_stable[k]["id"] != base_stable[k]["id"] for k in base_stable)):
            raise ValueError("command-invalid-previous-checkpoint")
        expected = deepcopy(expectations.data[2 if recapture else number])
        if recapture:
            expected["Command"]["value"] = 1
        binding = {"codeCommit": seal["codeCommit"], "package": str(package.resolve()),
                   "execution": str(execution), "baseline": asdict(baseline)}
        binding_path = journal / "baseline.json"
        _no_links(binding_path)
        if binding_path.exists():
            if binding_path.read_bytes() != _encode(binding):
                raise ValueError("command-baseline-binding-changed")
        else:
            _new(binding_path, binding)
        count = 1
        while (journal / ("run-" + str(count))).exists():
            count += 1
        run = journal / ("run-" + str(count))
        run.mkdir(mode=0o700)
        _new(run / "baseline.json", binding)
        _new(run / "checkpoint.json", {"command": command, "previous": asdict(previous)})

        def finish(status, application, **details):
            result = {"status": status, "application": application, "command": command,
                      "directory": str(run), "complete": False, "executionPermission": False,
                      "nativeSucceeded": False, "issueHalted": False, **details}
            _new(run / "result.json", result)
            return result

        pending = _unresolved(journal)
        if pending:
            return finish("source-investigation", "unknown", unresolvedProcess=pending)
        attempts, unconfirmed, observations = 0, 0, 0
        while True:
            observations += 1
            capture_started = datetime.now(UTC)
            try:
                stage = observe()
                if not isinstance(stage, NativeDeployedStage):
                    raise ValueError("command-native-stage-required")
                _new(run / f"observation-{observations}.json", asdict(stage))
                now = datetime.now(UTC)
                if (stage.window.started < capture_started or stage.window.finished > now
                        or stage.window.started < previous.window.finished):
                    raise ValueError("command-replayed-or-future-observation")
                fields, stable = _checked(package, execution, stage)
                _context(baseline, previous, stage, stable, base_stable)
                if _equal(expected, fields):
                    return finish("applied", "applied", observations=observations, attempts=attempts)
                application = "unapplied" if _equal(old_fields, fields) else "partial"
                signature = application
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                application, signature = "unknown", type(exc).__name__ + ":" + str(exc)
                _new(run / f"observation-{observations}-error.json", {"error": signature})
            # Read recovery is separate from confirmed-unapplied write retries:
            # even the first failed read AFTER a write gets another observation.
            if application != "unapplied":
                unconfirmed += 1
                if unconfirmed >= 2:
                    return finish("source-investigation", application, signature=signature,
                                  observations=observations, attempts=attempts)
                time.sleep(backoff_seconds)
                continue
            unconfirmed = 0
            if attempts >= 2:
                return finish("source-investigation", application, signature=signature,
                              observations=observations, attempts=attempts)
            # Derive the component from the authenticated world checkpoint;
            # only its Value is written, never a caller-supplied member/target.
            state = json.loads(stage.expected_state)
            target = state["components"]["drivegate-Command"]["id"]
            if not re.fullmatch(r"Reso_[0-9A-Fa-f]+", target):
                return finish("source-investigation", "unknown", signature="command-invalid-private-component")
            ReadRequest("doctor", stage.window.url).argv()  # exact loopback validation
            argv = ["resoloop", "component", "set", target, "Value", str(number),
                    "--url", stage.window.url, "--timeout", "10", "--command-timeout", "30", "--json"]
            attempts += 1
            attempt = run / f"attempt-{attempts}"
            attempt.mkdir(mode=0o700)
            _new(attempt / "intent.json", {"argv": argv, "startedAt": datetime.now(UTC).isoformat(),
                                          "observation": observations, "cwd": str(execution)})
            # No outer timeout. The child inherits the lock across parent death;
            # missing process/result evidence still never authorizes blind replay.
            try:
                process = subprocess.Popen(argv, cwd=execution, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, pass_fds=(lock.fileno(),))
            except OSError as exc:
                _new(attempt / "process-result.json", {"exitCode": None, "launchError": str(exc), "groupStopped": True})
            else:
                _new(attempt / "process.json", {"pid": process.pid})
                stdout, stderr = process.communicate()
                _write_new(attempt / "stdout.bin", stdout)
                _write_new(attempt / "stderr.bin", stderr)
                stopped = _group_stopped(process.pid)
                _new(attempt / "process-result.json", {"exitCode": process.returncode,
                    "groupStopped": stopped, "finishedAt": datetime.now(UTC).isoformat(),
                    "stdoutSha256": hashlib.sha256(stdout).hexdigest(),
                    "stderrSha256": hashlib.sha256(stderr).hexdigest()})
                if not stopped:
                    return finish("source-investigation", "unknown", unresolvedProcess=str(attempt))
            time.sleep(backoff_seconds)
