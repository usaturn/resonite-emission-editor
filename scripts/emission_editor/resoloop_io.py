"""Read-only observation and owned panel publishing; no avatar mutation methods."""

import json
import hashlib
import math
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .context import ProjectContext, validate_context
from .model import (
    MAX_ROW_USES,
    Avatar,
    Material,
    MaterialUse,
    RegistrationCapture,
    RegistryPlan,
    plan_registry,
)


@dataclass(frozen=True)
class PanelStatus:
    owned: bool = True
    busy: bool = False
    operator: str = ""
    pending: bool = False
    preview_active: bool = False
    operation_stage: int = 0
    publication: str = ""
    intent: int = 0


@dataclass(frozen=True)
class PublishResult:
    success: bool
    ready: bool
    generation: str = ""
    error: str = ""


@dataclass(frozen=True)
class Limits:
    avatars: int = 16
    materials: int = 256
    renderers: int = 4096
    slots: int = 50000
    depth: int = 64


class ReadPort(Protocol):
    session: str

    def hierarchy(self, depth: int) -> dict: ...
    def component(self, ref: str) -> dict: ...
    def material_list_types(self, types: set[str]) -> set[str]: ...
    def owned_roots(self) -> tuple[str, ...]: ...


class PanelPort(Protocol):
    session: str

    def guard(self, plan: RegistryPlan) -> PanelStatus: ...
    def set_ready(self, ready: bool) -> None: ...
    def begin(self, plan: RegistryPlan) -> None: ...
    def finish(self, plan: RegistryPlan) -> None: ...
    def stage(self, plan: RegistryPlan) -> None: ...
    def verify_staged(self, plan: RegistryPlan) -> bool: ...
    def activate(self, plan: RegistryPlan) -> None: ...
    def verify_active(self, plan: RegistryPlan) -> bool: ...


def flatten(tree):
    result = {}

    def visit(node, parent):
        if node["id"] in result:
            raise ValueError("duplicate slot identity")
        result[node["id"]] = (node, parent)
        for child in node.get("children", []):
            visit(child, node["id"])

    visit(tree, None)
    return result


def registration_topology(tree):
    """Match the former summary guard: pose is locator metadata, not identity."""
    return {
        "id": tree["id"], "name": tree["name"],
        "isReferenceOnly": tree.get("isReferenceOnly", False),
        "components": [{"id": c["id"], "type": c["type"]} for c in tree.get("components", [])],
        "children": [registration_topology(c) for c in tree.get("children", [])],
    }


def observed_world_position(slots, ref):
    """Transform local origin into Root space, using observed local TRS only.

    Resoloop SlotInfo uses xyzw quaternions. Its current ToolAudit/SceneArtifacts
    compose scale * rotation * translation * parent (row-vector convention).
    Applying each local transform from child towards Root is equivalent and
    preserves nonuniform scales with rotated ancestors without decomposing TRS.
    Root itself defines world space; absent/invalid ancestor data stays unknown.
    """
    point = (0.0, 0.0, 0.0)
    while ref != "Root":
        if ref is None or ref not in slots:
            return None
        node, ref = slots[ref]
        values = []
        for key, axes in (("position", "xyz"), ("rotation", "xyzw"), ("scale", "xyz")):
            raw = node.get(key)
            if not isinstance(raw, dict) or any(
                type(raw.get(axis)) not in (int, float) or not math.isfinite(raw[axis])
                for axis in axes
            ):
                return None
            values.append(tuple(raw[axis] for axis in axes))
        position, quaternion, scale = values
        norm = math.hypot(*quaternion)
        if not math.isfinite(norm) or norm == 0:
            return None
        x, y, z, w = (q / norm for q in quaternion)
        vx, vy, vz = (v * s for v, s in zip(point, scale))
        # q * v * inverse(q), expressed using two cross products.
        cx, cy, cz = y * vz - z * vy, z * vx - x * vz, x * vy - y * vx
        point = (
            vx + 2 * (w * cx + y * cz - z * cy) + position[0],
            vy + 2 * (w * cy + z * cx - x * cz) + position[1],
            vz + 2 * (w * cz + x * cy - y * cx) + position[2],
        )
        if not all(math.isfinite(v) for v in point):
            return None
    return point


def capture_registration(
    port: ReadPort, avatar_ids: tuple[str, ...], limits: Limits
) -> RegistrationCapture:
    session = port.session
    issues = []
    tree = port.hierarchy(limits.depth)
    slots = flatten(tree)
    if (
        not avatar_ids
        or len(set(avatar_ids)) != len(avatar_ids)
        or len(avatar_ids) > limits.avatars
    ):
        issues.append("avatar-limit-or-duplicate")
    if len(slots) > limits.slots:
        issues.append("slot-limit")
    if any(n.get("isReferenceOnly", False) for n, _ in slots.values()):
        issues.append("depth-incomplete")
    owned = port.owned_roots()
    if any(ref not in slots for ref in owned):
        issues.append("owned-root-unresolved")

    def under(ref, root):
        while ref is not None:
            if ref == root:
                return True
            ref = slots[ref][1]
        return False

    avatars = []
    for ref in avatar_ids:
        if ref not in slots or ref == "Root":
            issues.append("unknown-or-world-root:" + ref)
            continue
        node = slots[ref][0]
        if any(
            c["type"] == "[FrooxEngine]FrooxEngine.UserRoot"
            for c in node.get("components", [])
        ) or any(under(ref, root) for root in owned):
            issues.append("forbidden-root:" + ref)
            continue
        if not any(
            c["type"] == "[FrooxEngine]FrooxEngine.CommonAvatar.AvatarRoot"
            for c in node.get("components", [])
        ):
            issues.append("missing-avatar-root:" + ref)
            continue
        path = []
        current = ref
        while current is not None:
            path.append(slots[current][0]["name"])
            current = slots[current][1]
        avatars.append(
            Avatar(
                ref,
                node["name"],
                tuple(r for r in slots if under(r, ref)),
                tuple(reversed(path)),
                observed_world_position(slots, ref),
            )
        )
    selected = {ref for avatar in avatars for ref in avatar.slot_refs}
    types = {
        comp["type"]
        for node, _ in slots.values()
        for comp in node.get("components", [])
    }
    component_slots = {}
    for slot_ref, (node, _) in slots.items():
        for component in node.get("components", []):
            if component["id"] in component_slots:
                issues.append("duplicate-component-identity:" + component["id"])
            component_slots[component["id"]] = (slot_ref, component["type"])
    material_types = port.material_list_types(types) if not issues else set()
    renderers = [
        (ref, node, comp)
        for ref, (node, _) in slots.items()
        for comp in node.get("components", [])
        if comp["type"] in material_types
    ]
    if len(renderers) > limits.renderers:
        issues.append("renderer-limit")
    uses = []
    candidates = set()
    inspected = {}
    if not issues:
        if hasattr(port, "prefetch_components"):
            port.prefetch_components(tuple(comp["id"] for _, _, comp in renderers))
        for ref, node, comp in renderers:
            data = port.component(comp["id"])
            if data.get("id") != comp["id"] or data.get("type") != comp["type"]:
                issues.append("renderer-identity-changed:" + comp["id"])
                continue
            inspected[comp["id"]] = ("renderer", data)
            materials = data.get("members", {}).get("Materials")
            if (
                not isinstance(materials, dict)
                or materials.get("kind") != "list"
                or not isinstance(materials.get("elements"), list)
            ):
                issues.append("unresolved-renderer-list:" + comp["id"])
                continue
            list_ref = materials.get("id")
            renderer_uses = []
            for index, member in enumerate(materials.get("elements", [])):
                target = member.get("targetId")
                if not target:
                    # Null material entries are resolved absence, not a material row.
                    if member.get("kind") != "reference":
                        issues.append("unresolved-material-entry:" + comp["id"])
                    continue
                renderer_uses.append(
                    MaterialUse(
                        ref, node["name"], comp["id"], index, member["id"], target,
                        list_ref,
                    )
                )
            if renderer_uses and (not isinstance(list_ref, str) or not list_ref):
                # The row guard needs the list itself, not only its element bindings.
                issues.append("unresolved-renderer-list-id:" + comp["id"])
                continue
            uses.extend(renderer_uses)
            if ref in selected:
                candidates.update(use.material_ref for use in renderer_uses)
        if len(candidates) > limits.materials:
            issues.append("material-limit")
    materials = []
    if not issues:
        for ref in sorted(candidates):
            data = port.component(ref)
            if (
                ref not in component_slots
                or data.get("id") != ref
                or data.get("type") != component_slots[ref][1]
            ):
                issues.append("material-identity-unresolved:" + ref)
                continue
            inspected[ref] = ("material", data)
            emission = data.get("members", {}).get("EmissionColor")
            if data["type"] != "[FrooxEngine]FrooxEngine.XiexeToonMaterial":
                continue
            if (
                not emission
                or emission.get("kind") != "field"
                or emission.get("type") != "ResoniteLink.colorX"
                or not isinstance(emission.get("value"), dict)
            ):
                issues.append("unresolved-emission:" + ref)
                continue
            color = emission["value"]
            if set(color) != {"r", "g", "b", "a", "profile"}:
                issues.append("incomplete-color:" + ref)
                continue
            material_slot = component_slots[ref][0]
            material_path = []
            current_slot = material_slot
            while current_slot is not None:
                material_path.append(slots[current_slot][0]["name"])
                current_slot = slots[current_slot][1]
            materials.append(
                Material(
                    ref,
                    "/".join(reversed(material_path[:3])),
                    emission["id"],
                    dict(color),
                )
            )
        # Recheck the full topology and every relevant renderer binding/field identity.
        if registration_topology(tree) != registration_topology(port.hierarchy(limits.depth)):
            issues.append("structure-changed")
        if hasattr(port, "prefetch_components"):
            port.prefetch_components(tuple(inspected))
        for ref, (kind, previous) in inspected.items():
            members = (
                ("Materials", "Mesh") if kind == "renderer" else ("EmissionColor",)
            )
            current = port.component(ref)
            if previous["type"] != current["type"] or any(
                previous.get("members", {}).get(m) != current.get("members", {}).get(m)
                for m in members
            ):
                issues.append("component-changed:" + ref)
    if port.session != session:
        issues.append("session-changed")
    material_refs = {m.ref for m in materials}
    return RegistrationCapture(
        session,
        uuid4().hex,
        datetime.now(UTC).isoformat(),
        tuple(avatars),
        tuple(materials),
        tuple(u for u in uses if u.material_ref in material_refs),
        not issues,
        tuple(issues),
    )


def publish_registry(plan: RegistryPlan, port: PanelPort) -> PublishResult:
    if not plan.complete or port.session != plan.session:
        return PublishResult(
            False, False, plan.generation, "incomplete-or-session-changed"
        )
    started = False
    try:
        status = port.guard(plan)
        if (
            not status.owned
            or status.busy
            or status.operator
            or status.pending
            or status.preview_active
            or status.operation_stage
            or status.publication
            or status.intent
        ):
            return PublishResult(
                False, False, plan.generation, "panel-owned-or-recovery-guard"
            )
        port.set_ready(False)
        started = True
        port.begin(plan)
        port.stage(plan)
        if not port.verify_staged(plan):
            raise ValueError("staged-readback-mismatch")
        # Recheck ownership/session/recovery immediately before changing the active generation.
        status = port.guard(plan)
        if (
            port.session != plan.session
            or not status.owned
            or status.busy
            or status.operator
            or status.pending
            or status.preview_active
            or status.operation_stage
            or status.publication
            or status.intent
        ):
            raise ValueError("guard-changed-before-activation")
        port.activate(plan)
        port.set_ready(True)
        if not port.verify_active(plan):
            raise ValueError("active-readback-mismatch")
        port.finish(plan)
        if not port.verify_active(plan):
            raise ValueError("final-readback-mismatch")
        return PublishResult(True, True, plan.generation)
    except (
        RuntimeError,
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        if started:
            try:
                port.set_ready(False)
            except (
                RuntimeError,
                ValueError,
                OSError,
                KeyError,
                TypeError,
                subprocess.SubprocessError,
            ) as cleanup_error:
                return PublishResult(
                    False,
                    False,
                    plan.generation,
                    str(exc) + "; ready-disable-unconfirmed: " + str(cleanup_error),
                )
        return PublishResult(False, False, plan.generation, str(exc))


STRING = "[FrooxEngine]FrooxEngine.ValueField<string>"
SLOT_REF = "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.Slot>"
COMPONENT_REF = (
    "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.Component>"
)
COLOR_REF = (
    "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.IValue<colorX>>"
)
INT = "[FrooxEngine]FrooxEngine.ValueField<int>"
LIST_REF = (
    "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.SyncAssetList"
    "<[FrooxEngine]FrooxEngine.Material>>"
)
PROVIDER_REF = (
    "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.IAssetProvider"
    "<[FrooxEngine]FrooxEngine.Material>>"
)

BINDING_REF = "[FrooxEngine]FrooxEngine.ReferenceField<[FrooxEngine]FrooxEngine.SyncRef<[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Material>>>"


def save_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class ResoloopReadPort:
    """Current CLI transport. Session identity is an owned native Flux read adapter."""

    def __init__(self, context: ProjectContext, *, evidence: Path):
        validate_context(context)
        root = Path(context.project_root).resolve()
        evidence_path = Path(evidence)
        if evidence_path.exists():
            raise ValueError("evidence-already-exists")
        intended = evidence_path.resolve()
        if not intended.is_relative_to(root):
            raise ValueError("evidence-outside-project-root")
        ancestor = intended
        while not ancestor.exists():
            parent = ancestor.parent
            if parent == ancestor or not parent.is_relative_to(root):
                raise ValueError("evidence-outside-project-root")
            ancestor = parent
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.mkdir(parents=False, exist_ok=False)
        self.context = context
        self.url = context.url
        self.state = Path(context.world_state)
        self.flux_state = Path(context.flux_state)
        self.preservation_baseline = Path(context.preservation_baseline)
        self.evidence = evidence_path
        self.sequence = 0
        self._material_type_cache = {}
        self._observed_components = {}
        self._fallback_types = set()
        self._component_cache = {}

    def run(self, *args, accept_missing_members=False):
        self.sequence += 1
        cmd = [
            "resoloop",
            *map(str, args),
            "--url",
            self.url,
            "--json",
            "--timeout",
            "30",
        ]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        try:
            payload = json.loads(result.stdout.strip() or result.stderr.strip())
        except ValueError:
            payload = {
                "ok": False,
                "error": {"message": result.stderr or result.stdout},
            }
        save_new(
            self.evidence / f"{self.sequence:04d}.json",
            {"argv": cmd, "exit": result.returncode, "response": payload},
        )
        if result.returncode or not payload.get("ok"):
            error = payload.get("error", {})
            report = error.get("context", {}).get("result", {})
            if (
                accept_missing_members
                and error.get("code") == "REFLECTION_CHECK_FAILED"
                and all(
                    (
                        d.get("code") == "MEMBER_NOT_FOUND"
                        and d.get("member") == "Materials"
                    )
                    or (
                        d.get("code") == "COMPONENT_TYPE_NOT_FOUND"
                        and d.get("actual")
                        == "Object reference not set to an instance of an object."
                    )
                    for d in report.get("differences", [])
                )
            ):
                return report
            raise RuntimeError(str(error))
        return payload["data"]

    @property
    def session(self):
        data = self.run(
            "observe", "$member:registry-session.Value", "--state", self.state
        )
        # Observe returns a values dictionary; retain native identity instead of connectionId.
        values = data.get("values", data.get("observations", []))
        if not values:
            raise ValueError("native-session-identity-unavailable")
        value = next(iter(values.values()))["member"].get("value")
        if not isinstance(value, str) or not value:
            raise ValueError("native-session-identity-empty")
        return value

    def owned_roots(self):
        root = self.run(
            "inspect", "$slot:emission-root", "--state", self.state, "--depth", "0"
        )
        return (root["id"],)

    def material_list_types(self, types):
        fallback = getattr(self, "_fallback_types", set())
        unknown = sorted(
            (types - self._material_type_cache.keys()) | (types & fallback)
        )
        for start in range(0, len(unknown), 64):
            selected = unknown[start : start + 64]
            request = self.evidence / ("reflection-" + uuid4().hex + ".json")
            save_new(
                request,
                {
                    "types": [
                        {"type": name, "members": ["Materials"]} for name in selected
                    ]
                },
            )
            report = self.run(
                "type", "query", "--request", request, accept_missing_members=True
            )
            if {r["requestedType"] for r in report["types"]} != set(selected):
                raise ValueError("renderer-reflection-incomplete")
            for reflected in report["types"]:
                name = reflected["requestedType"]
                if not reflected.get("verified"):
                    refs = [
                        ref
                        for ref, actual_type in self._observed_components.items()
                        if actual_type == name
                    ]
                    if not refs:
                        raise ValueError("instance-metadata-incomplete")
                    payloads = []
                    for ref in refs:
                        payload = self.component(ref)
                        if (
                            payload.get("id") != ref
                            or payload.get("type") != name
                            or not isinstance(payload.get("members"), dict)
                            or any(
                                flag in payload
                                for flag in (
                                    "error",
                                    "errors",
                                    "truncated",
                                    "isReferenceOnly",
                                )
                            )
                            or any(
                                not isinstance(m, dict)
                                or any(
                                    flag in m
                                    for flag in ("error", "errors", "truncated")
                                )
                                or m.get("kind")
                                not in (
                                    "field",
                                    "reference",
                                    "list",
                                    "dictionary",
                                    "syncObject",
                                )
                                for m in payload["members"].values()
                            )
                        ):
                            raise ValueError("instance-metadata-incomplete")
                        payloads.append(payload)
                    members = [
                        p["members"]["Materials"]
                        for p in payloads
                        if "Materials" in p["members"]
                    ]
                    if any(
                        m.get("kind") != "list"
                        or not isinstance(m.get("elements"), list)
                        for m in members
                    ):
                        raise ValueError("instance-material-list-incomplete")
                    self._material_type_cache[name] = bool(members)
                    fallback.add(name)
                    save_new(
                        self.evidence / ("fallback-" + uuid4().hex + ".json"),
                        {
                            "type": name,
                            "basis": "all-current-instance-payloads",
                            "instances": payloads,
                            "has_materials_list": bool(members),
                        },
                    )
                else:
                    self._material_type_cache[name] = any(
                        m["name"] == "Materials" and m["kind"] == "list"
                        for m in reflected["members"]
                    )
        self._fallback_types = fallback
        return {t for t in types if self._material_type_cache[t]}

    def hierarchy(self, depth):
        # Without --include-components the normal hierarchy retains local TRS
        # and component identities but does not request component member data.
        tree = self.run("hierarchy", "--depth", depth)
        self._observed_components = {
            c["id"]: c["type"]
            for node, _ in flatten(tree).values()
            for c in node.get("components", [])
        }
        self._component_cache = {}
        return tree

    def prefetch_components(self, refs):
        requested = set(refs)
        if not requested <= self._observed_components.keys():
            raise ValueError("batch-component-observation-incomplete")
        for type_name in sorted({self._observed_components[ref] for ref in requested}):
            result = self.run(
                "inspect",
                "Root",
                "--depth",
                "64",
                "--components-only",
                "--component",
                type_name,
            )
            records = result.get("components")
            if not isinstance(records, list) or result.get("count") != len(records):
                raise ValueError("batch-component-observation-incomplete")
            seen = set()
            for record in records:
                component = record.get("component", {})
                ref = component.get("id")
                if ref not in requested:
                    continue
                if (
                    ref in seen
                    or component.get("type") != self._observed_components[ref]
                    or not isinstance(component.get("members"), dict)
                ):
                    raise ValueError("batch-component-observation-incomplete")
                seen.add(ref)
                self._component_cache[ref] = component
            expected = {
                ref for ref in requested if self._observed_components[ref] == type_name
            }
            if not expected <= seen:
                raise ValueError("batch-component-observation-incomplete")

    def component(self, ref):
        if ref in self._component_cache:
            return self._component_cache[ref]
        return self.run("component", "inspect", ref)


def render_registry(plan: RegistryPlan, *, template: Path) -> dict:
    """Storage-only declaration. Session-scoped IDs are bound during live registration."""
    base = json.loads(Path(template).read_text())
    root = next(n for n in base["children"] if n["slot"]["key"] == "registry-root")
    prefix = "reg-" + plan.generation
    components = [
        {"key": prefix + "-data", "type": STRING, "initialFields": {"Value": ""}}
    ]
    for index, avatar in enumerate(plan.avatars):
        components.append({"key": f"{prefix}-avatar-{index}", "type": SLOT_REF})
    children = []
    for index, row in enumerate(plan.rows):
        rowprefix = f"{prefix}-row-{index}"
        rowcomps = [
            {"key": rowprefix + "-material", "type": COMPONENT_REF},
            {"key": rowprefix + "-source", "type": COLOR_REF},
            {
                "key": rowprefix + "-data",
                "type": STRING,
                "initialFields": {"Value": ""},
            },
        ]
        for number, use in enumerate(row.uses):
            rowcomps.extend(
                [
                    {"key": f"{rowprefix}-mesh-{number}", "type": SLOT_REF},
                    {"key": f"{rowprefix}-renderer-{number}", "type": COMPONENT_REF},
                    {"key": f"{rowprefix}-binding-{number}", "type": BINDING_REF},
                ]
            )
        children.append(
            {
                "slot": {"key": rowprefix, "name": f"Material {index + 1}"},
                "components": rowcomps,
            }
        )
    root.setdefault("children", []).append(
        {
            "slot": {"key": prefix, "name": "Generation_" + plan.generation},
            "components": components,
            "children": children,
        }
    )

    # Every same-type provider receives a distinct Slot for safe reconnect resolution.
    def providers(node):
        components = node.pop("components", [])
        if node.get("slot", {}).get("key", "").startswith("reg-"):
            node.setdefault("children", []).extend(
                {
                    "slot": {"key": c["key"] + "-slot", "name": c["key"]},
                    "components": [c],
                }
                for c in components
            )
        elif components:
            node["components"] = components
        for child in list(node.get("children", [])):
            if child.get("slot", {}).get("key", "").endswith("-slot"):
                continue
            providers(child)

    providers(root)
    return base


class ResoloopPanelPort:
    """Write surface restricted to independently resolved managed Registry descendants."""

    def __init__(self, read: ResoloopReadPort, capture: RegistrationCapture, retained_declaration=None):
        self.retained_declaration = retained_declaration
        self.read = read
        self.capture = capture
        self.bindings = {}
        self.staged_plan = None
        self.operation_session = (
            capture.session if capture is not None else read.session
        )
        self._publication = ""

    @property
    def session(self):
        return self.read.session

    def _state(self):
        state = json.loads(self.read.state.read_text())
        if state.get("ownershipKey") != "tools-emission-editor":
            raise ValueError("checkpoint-ownership-mismatch")
        return state

    def _owned_component(self, key):
        state = self._state()
        component = state["components"][key]
        root = self.read.run(
            "inspect", "$slot:registry-root", "--state", self.read.state, "--depth", "0"
        )
        emission = self.read.run(
            "inspect", "$slot:emission-root", "--state", self.read.state, "--depth", "0"
        )
        if (
            root.get("parentId") != emission["id"]
            or emission.get("name") != "ResoLoop_Test_EmissionEditor"
        ):
            raise ValueError("registry-root-outside-owned-root")
        target = self.read.run(
            "inspect",
            "$slot:" + component["slotKey"],
            "--state",
            self.read.state,
            "--depth",
            "0",
        )
        # State keys select the exact managed component; parent chain proves the write boundary.
        current = target
        seen = set()
        while current["id"] != root["id"]:
            if current["id"] in seen or current.get("parentId") in (None, "Root"):
                raise ValueError("target-outside-registry")
            seen.add(current["id"])
            current = self.read.run("inspect", current["parentId"], "--depth", "0")
        observed = self.read.run(
            "component", "inspect", "$component:" + key, "--state", self.read.state
        )
        if observed["type"] != component["type"]:
            raise ValueError("component-type-changed")
        if observed["id"] not in {c["id"] for c in target.get("components", [])}:
            raise ValueError("component-slot-changed")
        return observed

    def _set(self, key, member, value):
        if self.session != self.operation_session:
            raise ValueError("session-changed-before-owned-write")
        component = self._owned_component(key)
        before = component["members"].get(member)
        if before is None:
            raise ValueError("unverified-member")
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        print(
            json.dumps(
                {
                    "reviewedOwnedWrite": {
                        "key": key,
                        "slot": component["id"],
                        "member": member,
                        "reason": "publish verified registry data",
                    }
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if self.session != self.operation_session:
            raise ValueError("session-changed-before-owned-write")
        self.read.run("component", "set", component["id"], member, raw)
        self.bindings[(key, member)] = value

    def _value(self, key, observed=None):
        member = (
            observed
            if observed is not None
            else self._owned_component(key)["members"]["Value"]
        )
        if "value" not in member:
            raise ValueError("missing-status-value:" + key)
        value = member["value"]
        expected = (
            str
            if key
            in (
                "registry-operator",
                "registry-active",
                "registry-generation",
                "registry-publication",
            )
            else int
            if key in ("registry-operation-stage", "ui-intent")
            else bool
        )
        if type(value) is not expected:
            raise ValueError("status-type-changed:" + key)
        return value

    def guard(self, plan):
        if self.session != plan.session:
            raise ValueError("native-session-changed")
        if plan_registry(self.capture) != plan:
            raise ValueError("plan-capture-mismatch")
        current = capture_registration(
            self.read, tuple(a.ref for a in plan.avatars), Limits()
        )
        # Exact names/structure/values and binding IDs must still match this current capture.
        # A capture recorded before list IDs were captured cannot vouch for them;
        # every other observed member is still compared exactly.
        legacy = all(use.list_ref is None for use in self.capture.uses)
        expected = plan_registry_capture(self.capture)
        observed = plan_registry_capture(current, without_list_refs=legacy)
        if not current.complete or expected != observed:
            raise ValueError("capture-no-longer-current")
        keys = [
            "registry-publication",
            "registry-busy",
            "registry-operator",
            "registry-pending",
            "registry-preview-active",
            "registry-operation-stage",
            "ui-intent",
        ]
        observed_status = self._observe_owned((key, "Value") for key in keys)
        values = {
            key: self._value(key, observed_status[(key, "Value")]) for key in keys
        }
        publication = values["registry-publication"]
        own_publication = self._publication and publication == self._publication
        status = PanelStatus(
            owned=True,
            busy=values["registry-busy"] and not own_publication,
            publication="" if own_publication else publication,
            operator=values["registry-operator"],
            pending=values["registry-pending"],
            preview_active=values["registry-preview-active"],
            operation_stage=values["registry-operation-stage"],
            intent=values["ui-intent"],
        )
        # The verified current observation, for writes derived from this guard.
        self.observed_capture = current
        return status

    def begin(self, plan):
        if self._value("registry-publication") or self._value("registry-busy"):
            raise ValueError("publication-already-active")
        self._set("registry-publication", "Value", plan.generation)
        self._publication = plan.generation
        self._set("registry-busy", "Value", True)
        if self._value("registry-publication") != plan.generation or not self._value(
            "registry-busy"
        ):
            raise ValueError("publication-claim-readback-failed")

    def finish(self, plan):
        if (
            self._value("registry-publication") != plan.generation
            or self._publication != plan.generation
        ):
            raise ValueError("publication-claim-changed")
        self._set("registry-busy", "Value", False)
        self._set("registry-publication", "Value", "")
        self._publication = ""

    def set_ready(self, ready):
        self._set("registry-ready", "Value", ready)

    def _apply_in_session(self, path):
        if self.session != self.operation_session:
            raise ValueError("session-changed-before-apply")
        return self.read.run("apply", path, "--state", self.read.state)

    def render_declaration(self, plan):
        return render_registry(plan, template=self.read.context.registry_template)

    def _retain_generations(self, declaration, plan):
        context = self.retained_declaration
        if context is None:
            return declaration  # Existing strict diff guard refuses any stale generations.
        source = Path(context['path']).read_bytes()
        state_bytes = Path(self.read.state).read_bytes()
        old = json.loads(source)
        state = json.loads(state_bytes)
        generations = context['generations']
        if (hashlib.sha256(source).hexdigest() != context['sha256']
            or hashlib.sha256(state_bytes).hexdigest() != context['state_sha256']
            or context['session'] != plan.session or self.session != plan.session
            or old.get('ownership', {}).get('key') != 'tools-emission-editor'
            or state.get('ownershipKey') != 'tools-emission-editor'
            or old.get('slot') != {'key': 'emission-root', 'name': 'ResoLoop_Test_EmissionEditor', 'parent': 'Root'}
            or not generations or len(set(generations)) != len(generations)
            or plan.generation in generations):
            raise ValueError('retention-context-mismatch')
        def parent(document, key):
            matches = [n for n in document['children'] if n.get('slot', {}).get('key') == key]
            if len(matches) != 1:
                raise ValueError('retention-parent-ambiguous')
            return matches[0]
        def scoped(key, prefix):
            return key == prefix or key.startswith(prefix + '-') or key.startswith('uix-text-input--' + prefix + '-')
        if any(scoped(key, 'reg-' + plan.generation) for key in (*state['slots'], *state['components'])):
            raise ValueError('retention-new-generation-already-exists')
        for generation in generations:
            if not generation or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' for c in generation):
                raise ValueError('retention-generation-invalid')
            prefix = 'reg-' + generation
            for parent_key, root_key in (('registry-root', prefix), ('ui-canvas', prefix + '-ui')):
                oldparent = parent(old, parent_key)
                target = parent(declaration, parent_key)
                roots = [n for n in oldparent.get('children', []) if n.get('slot', {}).get('key') == root_key]
                if len(roots) != 1:
                    raise ValueError('retention-root-ambiguous')
                basepath = ['Root', 'ResoLoop_Test_EmissionEditor', oldparent['slot']['name']]
                rootpath = basepath + [roots[0]['slot']['name']]
                if state['slots'].get(root_key, {}).get('pathSegments') != rootpath:
                    raise ValueError('retention-parent-mismatch')
                # Use expanded managed state, not recipe replay. Structural declarations
                # deliberately omit ALL fields, references, initializers and transforms.
                nodes = {}
                for key, record in state['slots'].items():
                    path = record.get('pathSegments', [])
                    if path[:len(rootpath)] != rootpath:
                        continue
                    if not scoped(key, prefix) or tuple(path) in nodes:
                        raise ValueError('retention-foreign-or-ambiguous-key')
                    nodes[tuple(path)] = {'slot': {'key': key, 'name': path[-1]}, 'components': [], 'children': []}
                bykey = {n['slot']['key']: n for n in nodes.values()}
                for key, record in state['components'].items():
                    if record['slotKey'] in bykey:
                        if not scoped(key, prefix):
                            raise ValueError('retention-foreign-component')
                        bykey[record['slotKey']]['components'].append({'key': key, 'type': record['type']})
                for path, node in nodes.items():
                    if list(path) != rootpath:
                        if path[:-1] not in nodes:
                            raise ValueError('retention-ancestry-incomplete')
                        nodes[path[:-1]]['children'].append(node)
                target.setdefault('children', []).append(nodes[tuple(rootpath)])
        return declaration

    def stage(self, plan):
        declaration = self._retain_generations(self.render_declaration(plan), plan)
        path = self.read.evidence / "staging.json"
        save_new(path, declaration)
        diff = self.read.run("diff", path, "--state", self.read.state, "--brief")
        save_new(self.read.evidence / "reviewed-diff.json", diff)
        prefix = "reg-" + plan.generation
        if any(diff.get(kind, 0) for kind in ("updates", "renames", "deletes")) or any(
            op.get("action") != "create"
            or not (
                op.get("key") == prefix
                or op.get("key", "").startswith(prefix + "-")
                or (
                    op.get("key", "").startswith("uix-text-input--" + prefix + "-")
                    and op.get("path", "").startswith(
                        "Root/ResoLoop_Test_EmissionEditor/ui-canvas/" + prefix + "-ui/"
                    )
                )
            )
            for op in diff.get("operations", [])
        ):
            raise ValueError("registration-diff-outside-new-generation")
        # Declaration has no external writes/prune; every new key belongs to this generation.
        print(
            json.dumps({"reviewedRegistryDiff": diff}, ensure_ascii=False), flush=True
        )
        self._apply_in_session(path)
        convergence = self._apply_in_session(path)
        save_new(self.read.evidence / "convergence.json", convergence)
        if any(
            convergence.get(k, 0)
            for k in (
                "componentsAdded",
                "componentsUpdated",
                "slotsCreated",
                "slotsUpdated",
                "componentsDeleted",
                "slotsDeleted",
            )
        ):
            raise ValueError("registration-reapply-did-not-converge")
        prefix = "reg-" + plan.generation
        self._set(
            prefix + "-data",
            "Value",
            json.dumps(runtime_plan_data(plan), ensure_ascii=False),
        )
        for index, avatar in enumerate(plan.avatars):
            self._set(f"{prefix}-avatar-{index}", "Reference", avatar.ref)
        for index, row in enumerate(plan.rows):
            key = f"{prefix}-row-{index}"
            self._set(key + "-material", "Reference", row.material_ref)
            self._set(key + "-source", "Reference", row.field_ref)
            self._set(
                key + "-data",
                "Value",
                json.dumps(runtime_row_data(row), ensure_ascii=False),
            )
            for number, use in enumerate(row.uses):
                self._set(f"{key}-mesh-{number}", "Reference", use.mesh_ref)
                self._set(f"{key}-renderer-{number}", "Reference", use.renderer_ref)
                self._set(f"{key}-binding-{number}", "Reference", use.binding_ref)
        self.staged_plan = plan

    def _observe_owned(self, selectors):
        # Prove Registry's relationship to the managed owner, then account for every observed target.
        self._owned_component("registry-ready")
        tree = self.read.run(
            "hierarchy",
            "--under",
            "$slot:registry-root",
            "--state",
            self.read.state,
            "--depth",
            "64",
            "--summary",
            "--include-components",
        )
        slots = flatten(tree)
        if any(node.get("isReferenceOnly") for node, _ in slots.values()):
            raise ValueError("owned-readback-incomplete")
        attached = {
            c["id"]: (slot_ref, c["type"])
            for slot_ref, (node, _) in slots.items()
            for c in node.get("components", [])
        }
        state = self._state()
        result = {}
        selected = list(selectors)
        for start in range(0, len(selected), 64):
            batch = selected[start : start + 64]
            observed = self.read.run(
                "observe",
                *["$member:" + key + "." + member for key, member in batch],
                "--state",
                self.read.state,
            )["values"]
            for key, member in batch:
                record = observed["$member:" + key + "." + member]
                expected = state["components"][key]
                slot_ref = state["slots"][expected["slotKey"]]["id"]
                if (
                    record["componentId"] != expected["id"]
                    or attached.get(record["componentId"])
                    != (slot_ref, expected["type"])
                    or record["componentType"] != expected["type"]
                ):
                    raise ValueError("readback-target-outside-registry")
                result[(key, member)] = record["member"]
        return result

    def verify_staged(self, plan):
        if self.staged_plan != plan:
            return False
        observed = self._observe_owned(self.bindings)
        for (key, member), expected in self.bindings.items():
            actual = observed[(key, member)]
            value = (
                actual.get("targetId") if member == "Reference" or actual.get("kind") == "reference" else actual.get("value")
            )
            if value != expected:
                return False
        return True

    def activate(self, plan):
        self._set("registry-generation", "Value", plan.generation)
        self._set("registry-active", "Value", plan.generation)

    def verify_active(self, plan):
        return (
            self.session == plan.session
            and self.bindings.get(("registry-active", "Value")) == plan.generation
            and self.bindings.get(("registry-ready", "Value")) is True
            and self.verify_staged(plan)
        )


def plan_registry_capture(capture, *, without_list_refs=False):
    uses = capture.uses
    if without_list_refs:
        uses = tuple(replace(use, list_ref=None) for use in uses)
    return (
        capture.session,
        tuple((a.ref, a.name, a.slot_refs, a.path) for a in capture.avatars),
        capture.materials,
        uses,
        capture.complete,
        capture.issues,
    )


def guard_writes(plan, observed):
    """Ordered owned writes binding each registered use to its observed list.

    Shared by the current-generation supplement and future registration so both
    bind the same list, index and provider. `observed` is the verified current
    capture; every planned use must appear in it unchanged, with its list ID.
    Order: per row index-K and list-K for each use, then provider; row-count last.
    """
    if not plan.complete or observed.session != plan.session or not observed.complete:
        raise ValueError("guard-writes-unverified-observation")
    lists = {}
    for use in observed.uses:
        if not isinstance(use.list_ref, str) or not use.list_ref:
            raise ValueError("guard-writes-list-unobserved:" + use.renderer_ref)
        if lists.setdefault(use.renderer_ref, use.list_ref) != use.list_ref:
            raise ValueError("guard-writes-list-ambiguous:" + use.renderer_ref)
    if len(set(lists.values())) != len(lists):
        raise ValueError("guard-writes-list-shared-by-renderers")
    current = {replace(use, list_ref=None): use for use in observed.uses}
    prefix = "reg-" + plan.generation
    writes = []
    for index, row in enumerate(plan.rows):
        if not row.uses or len(row.uses) > MAX_ROW_USES:
            raise ValueError("guard-writes-row-use-limit:" + row.material_ref)
        key = f"{prefix}-row-{index}"
        for number, use in enumerate(row.uses):
            seen = current.get(replace(use, list_ref=None))
            if (
                seen is None
                or use.material_ref != row.material_ref
                or (use.list_ref is not None and use.list_ref != seen.list_ref)
            ):
                raise ValueError("guard-writes-use-not-current:" + use.binding_ref)
            writes.append((f"{key}-index-{number}", "Value", use.material_index))
            writes.append((f"{key}-list-{number}", "Reference", seen.list_ref))
        writes.append((key + "-provider", "Reference", row.material_ref))
    writes.append((prefix + "-row-count", "Value", len(plan.rows)))
    return tuple(writes)


def runtime_plan_data(plan):
    return {
        "schema": 1,
        "generation": plan.generation,
        "observation_session": plan.session,
        "observed_at": plan.observed_at,
        "complete": plan.complete,
        "avatars": [asdict(a) for a in plan.avatars],
        "row_count": len(plan.rows),
    }


def runtime_row_data(row):
    return {
        "material_ref": row.material_ref,
        "material_name": row.material_name,
        "field_ref": row.field_ref,
        "color_at_registration": dict(row.color),
        "uses": [
            dict(
                asdict(use),
                inside_avatars=[
                    a.ref for a in row.avatars if use.mesh_ref in a.slot_refs
                ],
            )
            for use in row.uses
        ],
    }
