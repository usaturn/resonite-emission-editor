"""Pure registration observations and reference-identity aggregation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Avatar:
    ref: str
    name: str
    slot_refs: tuple[str, ...]
    path: tuple[str, ...] = ()
    # Locator at RegistrationCapture.observed_at; never authorizes target selection.
    observed_world_position: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Material:
    ref: str
    name: str
    field_ref: str
    color: dict


# The native row guard has one List/Index port pair per registered use.
MAX_ROW_USES = 16


@dataclass(frozen=True)
class MaterialUse:
    mesh_ref: str
    mesh_name: str
    renderer_ref: str
    material_index: int
    binding_ref: str
    material_ref: str
    # Renderer Materials list ID observed with this use (session-scoped). None
    # only in captures recorded before the list was captured.
    list_ref: str | None = None


@dataclass(frozen=True)
class RegistrationCapture:
    session: str
    generation: str
    observed_at: str
    avatars: tuple[Avatar, ...]
    materials: tuple[Material, ...]
    uses: tuple[MaterialUse, ...]
    complete: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class MaterialRow:
    material_ref: str
    material_name: str
    field_ref: str
    color: dict
    uses: tuple[MaterialUse, ...]
    avatars: tuple[Avatar, ...]

    @property
    def mesh_refs(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(use.mesh_ref for use in self.uses))

    def external_mesh_refs(self, avatar_ref: str) -> tuple[str, ...]:
        avatar = next(a for a in self.avatars if a.ref == avatar_ref)
        return tuple(ref for ref in self.mesh_refs if ref not in avatar.slot_refs)


@dataclass(frozen=True)
class RegistryPlan:
    session: str
    generation: str
    observed_at: str
    avatars: tuple[Avatar, ...]
    rows: tuple[MaterialRow, ...]
    complete: bool
    issues: tuple[str, ...]


def plan_registry(capture: RegistrationCapture) -> RegistryPlan:
    issues = list(capture.issues)
    refs = [m.ref for m in capture.materials]
    if len(refs) != len(set(refs)):
        issues.append("duplicate-material-identity")
    if len({a.ref for a in capture.avatars}) != len(capture.avatars):
        issues.append("duplicate-avatar-identity")
    known = set(refs)
    if any(use.material_ref not in known for use in capture.uses):
        issues.append("unresolved-material")
    for m in capture.materials:
        # Never truncate: a dropped use would be written without being guarded.
        if sum(use.material_ref == m.ref for use in capture.uses) > MAX_ROW_USES:
            issues.append("row-use-limit:" + m.ref)
    fields = [m.field_ref for m in capture.materials]
    if len(fields) != len(set(fields)):
        issues.append("duplicate-source-field")
    rows = tuple(
        MaterialRow(
            m.ref,
            m.name,
            m.field_ref,
            dict(m.color),
            tuple(u for u in capture.uses if u.material_ref == m.ref),
            capture.avatars,
        )
        for m in capture.materials
    )
    return RegistryPlan(
        capture.session,
        capture.generation,
        capture.observed_at,
        capture.avatars,
        rows,
        capture.complete and not issues,
        tuple(issues),
    )
