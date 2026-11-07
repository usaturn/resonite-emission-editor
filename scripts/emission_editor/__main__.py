"""Read and render the owned emission registry. Live registration stays disabled."""

import argparse
import json
import subprocess
from pathlib import Path

from .model import Avatar, Material, MaterialUse, RegistrationCapture, plan_registry


def load_capture(path):
    data = json.loads(Path(path).read_text())
    data["avatars"] = tuple(
        Avatar(a["ref"], a["name"], tuple(a["slot_refs"]), tuple(a.get("path", ())),
               tuple(a["observed_world_position"]) if a.get("observed_world_position") is not None else None)
        for a in data["avatars"]
    )
    data["materials"] = tuple(Material(**m) for m in data["materials"])
    data["uses"] = tuple(MaterialUse(**u) for u in data["uses"])
    data["issues"] = tuple(data["issues"])
    return RegistrationCapture(**data)


def unregister(port):
    """Deactivate only; retain all registration and recovery records for diagnosis."""
    session = port.session
    fields = [
        "busy",
        "operator",
        "pending",
        "preview-active",
        "operation-stage",
        "publication",
    ]
    if any(port._value("registry-" + name) for name in fields) or port._value(
        "ui-intent"
    ):
        return {
            "success": False,
            "ready": False,
            "error": "panel-in-use-or-recovery-pending",
        }
    if port.session != session:
        return {"success": False, "ready": False, "error": "session-changed"}
    port.set_ready(False)
    port._set("registry-active", "Value", "")
    return {
        "success": port._value("registry-active") == ""
        and port._value("registry-ready") is False,
        "ready": False,
        "retainedRecords": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Emission registry. Live registration is not available in this package."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "register", "unregister"):
        command = commands.add_parser(name)
        command.add_argument("--url", required=True)
        command.add_argument("--output", required=True)
        if name != "unregister":
            command.add_argument("--avatar", action="append", required=True)
        if name != "inspect":
            command.add_argument("--state", required=True)
    command = commands.add_parser("render")
    command.add_argument("--capture", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--template", required=True, type=Path)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        try:
            if args.command != "render":
                raise ValueError(
                    "native-switch-not-approved: live inspect, register, and unregister "
                    "stay disabled until a separate native plan"
                )
            from .ui import render_panel

            result = render_panel(
                plan_registry(load_capture(args.capture)),
                template=args.template,
            )
        except (
            RuntimeError,
            ValueError,
            OSError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as exc:
            result = {"success": False, "ready": False, "error": str(exc)}
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("success") is False or result.get("complete") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
