"""Restricted raw read receipts, not a native preflight or permission gate.

This internal building block is deliberately not wired into the package's live
CLI. A matching receipt proves only local consistency, not who produced it.
"""
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit


_TARGET = re.compile(r"(?:Root|Reso_[0-9a-fA-F]+|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")


@dataclass(frozen=True)
class ReadRequest:
    operation: str
    url: str
    target: str | None = None
    depth: int = 0

    def argv(self) -> list[str]:
        """Construct one permitted operation; never accept caller-supplied argv."""
        if not isinstance(self.url, str):
            raise ValueError("explicit-loopback-url-required")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(c.isspace() or ord(c) < 32 for c in self.url)
        ):
            raise ValueError("explicit-loopback-url-required")
        if type(self.depth) is not int or not 0 <= self.depth <= 64:
            raise ValueError("invalid-read-depth")
        if self.operation in {"discover", "doctor"}:
            if self.target is not None or self.depth != 0:
                raise ValueError("unexpected-read-target")
            args = [self.operation]
            if self.operation == "discover":
                args += ["--discovery-seconds", "12"]
        elif self.operation in {"hierarchy", "inspect", "component-inspect"}:
            if not isinstance(self.target, str) or not _TARGET.fullmatch(self.target):
                raise ValueError("explicit-read-id-required")
            if self.operation == "hierarchy":
                args = ["hierarchy", "--under", self.target, "--depth", str(self.depth),
                        "--include-components", "--summary"]
            elif self.operation == "inspect":
                args = ["inspect", self.target, "--depth", str(self.depth), "--members"]
            else:
                if self.depth != 0:
                    raise ValueError("unexpected-read-depth")
                args = ["component", "inspect", self.target]
        else:
            raise ValueError("read-operation-not-allowed")
        # Discovery only listens to announcements; it does not select/connect to
        # a session. Every connected operation has an explicit destination.
        if self.operation != "discover":
            args += ["--url", self.url]
        return ["resoloop", *args, "--timeout", "10", "--command-timeout", "30", "--json"]


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_new(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def _reject_constant(value):
    raise ValueError("non-json-number:" + value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-json-key")
        result[key] = value
    return result


def _decode(raw: bytes):
    return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def collect_read(request: ReadRequest, directory: Path, *, cwd: Path) -> dict:
    """Synchronously capture exactly one read, preserving failures without retry.

    The caller explicitly chooses a new directory under an existing parent.
    CLI timeouts govern the operation; no outer timeout kills a running CLI.
    """
    argv = request.argv()  # Reject before creating files or starting a process.
    working_directory = Path(cwd).resolve(strict=True)
    if not working_directory.is_dir():
        raise ValueError("read-cwd-not-directory")
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    started = datetime.now(UTC).isoformat()
    launch_error = None
    try:
        result = subprocess.run(argv, cwd=working_directory, stdin=subprocess.DEVNULL,
                                capture_output=True, check=False)
        stdout, stderr, exit_code = result.stdout, result.stderr, result.returncode
    except OSError as exc:
        launch_error = str(exc)
        stdout, stderr, exit_code = b"", launch_error.encode("utf-8", errors="replace"), None
    finished = datetime.now(UTC).isoformat()
    _write_new(directory / "stdout.bin", stdout)
    _write_new(directory / "stderr.bin", stderr)
    receipt = {
        "schemaVersion": 1,
        "request": asdict(request),
        "argv": argv,
        "cwd": str(working_directory),
        "startedAt": started,
        "finishedAt": finished,
        "exitCode": exit_code,
        "launchError": launch_error,
        "stdoutSha256": _digest(stdout),
        "stderrSha256": _digest(stderr),
    }
    _write_new(directory / "receipt.json",
               (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return load_read(directory)


def load_read(directory: Path) -> dict:
    """Check a receipt's local integrity, never native success or authorization.

    Hashes detect changed bytes against the receipt. They cannot authenticate a
    receipt and both raw files that an attacker has rewritten consistently.
    """
    directory = Path(directory)
    receipt = _decode((directory / "receipt.json").read_bytes())
    if not isinstance(receipt, dict) or type(receipt.get("schemaVersion")) is not int or receipt["schemaVersion"] != 1:
        raise ValueError("invalid-read-receipt-schema")
    try:
        request = ReadRequest(**receipt["request"])
        if receipt["argv"] != request.argv():
            raise ValueError("read-receipt-argv-mismatch")
        exit_code = receipt["exitCode"]
        launch_error = receipt["launchError"]
        if exit_code is None:
            if not isinstance(launch_error, str) or not launch_error:
                raise ValueError("missing-read-launch-failure")
        elif type(exit_code) is not int or launch_error is not None:
            raise ValueError("invalid-read-exit-code")
        stdout = (directory / "stdout.bin").read_bytes()
        stderr = (directory / "stderr.bin").read_bytes()
        if _digest(stdout) != receipt["stdoutSha256"] or _digest(stderr) != receipt["stderrSha256"]:
            raise ValueError("read-receipt-raw-mismatch")
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid-read-receipt") from exc
    try:
        response = _decode(stdout)
    except (ValueError, UnicodeError):
        response = None
    envelope_ok = (isinstance(response, dict) and response.get("ok") is True
                   and isinstance(response.get("data"), dict))
    return {
        "request": asdict(request),
        "argv": receipt["argv"],
        "cwd": receipt["cwd"],
        "startedAt": receipt["startedAt"],
        "finishedAt": receipt["finishedAt"],
        "exitCode": exit_code,
        "launchError": launch_error,
        "transportSucceeded": exit_code == 0 and launch_error is None and envelope_ok,
        "complete": False,
        "executionPermission": False,
        "response": response,
    }
