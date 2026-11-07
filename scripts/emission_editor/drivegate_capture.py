"""Fixed detailed-diff capture, not a command executor or execution permission.

Only collect_diff starts a process, synchronously. load_diff checks saved bytes;
assess_diff also binds them to an independent declaration/state/complete plan and
an explicit URL/time window. No summaries, adoption, filters, retries or writes.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

from .drivegate_package import _no_links
from .drivegate_evidence import ReadWindow, Verdict, _aware, _strict_equal
from .read_receipts import ReadRequest, _decode, _digest, _write_new


@dataclass(frozen=True)
class DiffRequest:
    url: str
    declaration: Path
    state: Path

    def argv(self):
        ReadRequest("doctor", self.url).argv()  # Reuse exact loopback validation.
        for path in (self.declaration, self.state):
            if not isinstance(path, Path) or not path.is_absolute() or path != path.resolve():
                raise ValueError("diff-explicit-canonical-path-required")
            _no_links(path)
        if self.declaration == self.state:
            raise ValueError("diff-input-state-overlap")
        return ["resoloop", "diff", str(self.declaration), "--state", str(self.state),
                "--url", self.url, "--timeout", "10", "--command-timeout", "30", "--json", "--quiet"]


def _snapshot(path, *, optional=False):
    _no_links(path)
    if optional and not path.exists():
        return None
    if not path.is_file():
        raise ValueError("diff-nonregular-input")
    return path.read_bytes()


def _json_new(path, value):
    _write_new(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def collect_diff(request: DiffRequest, directory: Path, *, cwd: Path) -> dict:
    """Capture invocation-time bytes, actual process result and unchanged inputs.

    Failure leaves diagnostics in a new exclusive directory. A previously absent
    state is recorded as absence, never replaced with empty bytes or adopted.
    CLI timeouts apply; no outer timeout interrupts the process.
    """
    argv = request.argv()
    declaration = _snapshot(request.declaration)
    state = _snapshot(request.state, optional=True)
    working = Path(cwd).resolve(strict=True)
    if not working.is_dir():
        raise ValueError("diff-cwd-not-directory")
    directory = Path(directory)
    _no_links(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    _write_new(directory / "declaration.bin", declaration)
    if state is not None:
        _write_new(directory / "state.bin", state)
    started = datetime.now(UTC).isoformat()
    error = None
    try:
        result = subprocess.run(argv, cwd=working, stdin=subprocess.DEVNULL, capture_output=True, check=False)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except OSError as exc:
        error = str(exc)
        stdout, stderr, code = b"", error.encode(errors="replace"), None
    finished = datetime.now(UTC).isoformat()
    unchanged = False
    try:
        unchanged = (declaration == _snapshot(request.declaration)
                     and state == _snapshot(request.state, optional=True))
    except (OSError, ValueError):
        pass
    _write_new(directory / "stdout.bin", stdout)
    _write_new(directory / "stderr.bin", stderr)
    _json_new(directory / "receipt.json", {
        "schemaVersion": 1, "url": request.url, "declaration": str(request.declaration),
        "state": str(request.state), "argv": argv, "cwd": str(working),
        "startedAt": started, "finishedAt": finished, "exitCode": code, "launchError": error,
        "inputsUnchanged": unchanged, "declarationSha256": _digest(declaration),
        "stateSha256": None if state is None else _digest(state),
        "stdoutSha256": _digest(stdout), "stderrSha256": _digest(stderr),
    })
    return load_diff(directory)


def load_diff(directory: Path) -> dict:
    """Verify local provenance only; coordinated raw/receipt forgery is out of scope."""
    directory = Path(directory)
    _no_links(directory)
    for path in directory.iterdir():
        _no_links(path)
        if not path.is_file():
            raise ValueError("diff-nonregular-evidence")
    receipt = _decode((directory / "receipt.json").read_bytes())
    if type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != 1:
        raise ValueError("diff-receipt-schema")
    request = DiffRequest(receipt["url"], Path(receipt["declaration"]), Path(receipt["state"]))
    if receipt["argv"] != request.argv():
        raise ValueError("diff-receipt-argv")
    state_present = receipt["stateSha256"] is not None
    names = {"receipt.json", "stdout.bin", "stderr.bin", "declaration.bin"}
    if state_present:
        names.add("state.bin")
    if {p.name for p in directory.iterdir()} != names:
        raise ValueError("diff-evidence-file-set")
    hashes = [("declaration.bin", "declarationSha256"), ("stdout.bin", "stdoutSha256"),
              ("stderr.bin", "stderrSha256")]
    if state_present:
        hashes.append(("state.bin", "stateSha256"))
    for filename, key in hashes:
        if _digest((directory / filename).read_bytes()) != receipt[key]:
            raise ValueError("diff-evidence-hash")
    code, error = receipt["exitCode"], receipt["launchError"]
    if not ((type(code) is int and error is None) or (code is None and isinstance(error, str) and error)):
        raise ValueError("diff-process-result")
    try:
        response = _decode((directory / "stdout.bin").read_bytes())
    except (ValueError, UnicodeError):
        response = None
    return {**receipt, "response": response,
            "transportSucceeded": type(code) is int and code == 0 and error is None
                and isinstance(response, dict) and response.get("ok") is True and isinstance(response.get("data"), dict),
            "complete": False, "executionPermission": False, "nativeSucceeded": False}


def _plan(data):
    """Require the full installed ApplyPlanResult, with counts derived from entries."""
    required = {"valid", "schemaVersion", "ownershipKey", "stateFile", "operations", "changes",
                "creates", "updates", "noOps", "renames", "deletes", "atomic", "recovery", "warnings"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("missing:complete-diff-plan")
    if (data["valid"] is not True or data["schemaVersion"] != "1" or type(data["atomic"]) is not bool
            or not isinstance(data["recovery"], str) or not isinstance(data["warnings"], list)
            or not isinstance(data["operations"], list) or not data["operations"]):
        raise ValueError("diff-plan-shape")
    counts = dict.fromkeys(("create", "update", "no-op", "rename", "delete"), 0)
    seen = set()
    for op in data["operations"]:
        if not isinstance(op, dict) or not {"action", "kind", "key", "path", "atomic", "recovery"} <= op.keys():
            raise ValueError("missing:diff-operation-details")
        if op["action"] not in counts or op["kind"] not in ("slot", "component"):
            raise ValueError("diff-operation-kind")
        identity = (op["kind"], op["key"], op["path"])
        if not all(isinstance(v, str) and v for v in identity) or identity in seen:
            raise ValueError("diff-operation-identity")
        seen.add(identity)
        counts[op["action"]] += 1
    for action, key in (("create", "creates"), ("update", "updates"), ("no-op", "noOps"), ("rename", "renames"), ("delete", "deletes")):
        if type(data[key]) is not int or data[key] != counts[action]:
            raise ValueError("diff-count-coverage")
    if not _strict_equal(data["changes"], [op for op in data["operations"] if op["action"] != "no-op"]):
        raise ValueError("diff-change-coverage")
    # Connection-scoped IDs/metrics are not a native identity or plan semantics.
    return {k: data[k] for k in required}


def assess_diff(directory: Path, *, request: DiffRequest, window: ReadWindow,
                sealed_declaration: bytes, expected_state: bytes | None, expected_plan: dict) -> Verdict:
    """Named detailed-diff gate: original invocation + independently reviewed plan.

    Creation and preservation have distinct expected complete operations. The
    caller must seal/review expected_plan independently, never copy the output.
    This stage alone does not establish world-session/ownership or full preflight.
    """
    try:
        loaded = load_diff(directory)
        if loaded["argv"] != request.argv() or window.url != request.url:
            return Verdict("failed", ("diff-request",))
        times = [window.started, datetime.fromisoformat(loaded["startedAt"]),
                 datetime.fromisoformat(loaded["finishedAt"]), window.finished, window.now]
        if not all(_aware(t) for t in times) or times != sorted(times):
            return Verdict("failed", ("diff-time-window",))
        if loaded["transportSucceeded"] is not True or loaded["inputsUnchanged"] is not True:
            return Verdict("failed", ("diff-process-or-input-change",))
        declaration = (Path(directory) / "declaration.bin").read_bytes()
        state = (Path(directory) / "state.bin").read_bytes() if loaded["stateSha256"] is not None else None
        if declaration != sealed_declaration or state != expected_state:
            return Verdict("failed", ("diff-independent-input-bytes",))
        actual, expected = _plan(loaded["response"]["data"]), _plan(expected_plan)
        if expected["stateFile"] != str(request.state) or not expected["ownershipKey"]:
            return Verdict("failed", ("diff-independent-context",))
        if not _strict_equal(actual, expected):
            return Verdict("failed", ("diff-complete-plan",))
        return Verdict("passed", data=(actual,))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return Verdict("inconclusive" if str(exc).startswith("missing:") else "failed", ("diff-evidence:" + str(exc),))
