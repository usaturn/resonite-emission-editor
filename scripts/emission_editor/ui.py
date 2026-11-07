"""Native UIX declarations. References are stable keys, never captured runtime IDs."""

import math
import os
import shutil
from pathlib import Path

from .model import MAX_ROW_USES, RegistryPlan
from .recovery import preserved_hashes
from .resoloop_io import (
    LIST_REF,
    PROVIDER_REF,
    ResoloopPanelPort,
    render_registry,
    save_new,
)

F = "[FrooxEngine]FrooxEngine."
U = F + "UIX."
CYAN = [0.12, 0.85, 0.91, 1]
INK = [0.035, 0.047, 0.061, 1]
TEXT = [0.88, 0.93, 0.96, 1]
# Keep historical root sources/binaries immutable; choose current native bodies
# explicitly before copying by basename into the isolated compiler directory.
CURRENT_NATIVE_SOURCES = {
    "Selection.pg": "current/Selection.pg",
    "RowView.pg": "current/RowView.pg",
}
# CanInteract (operator, ready, idle) gates the drop strip. Canvas.Release still
# calls a disabled receiver; only a disabled Button keeps the drop from reaching it.
UI_READY_DRIVES = ["$member:ui-drop-receiver.Enabled", "$member:ui-drop-button.Enabled"]
# Rows never scroll: heights reserve wrapped text at the narrowest Canvas width
# (1100 minus the viewport and row margins) and the Canvas grows with them.
NARROW_TEXT_WIDTH = 1100 - 2 * 30 - 2 * 12
ROW_RGB_X = tuple(488 + j * 220 for j in range(3))
ROW_CONFIRM_X = 1148
# Footer below the viewport; the status Text reserves four 22 px lines because
# the longest native status message wraps at the 1100 width.
FOOTER_HEIGHT = 200


def text_height(text, size, width=NARROW_TEXT_WIDTH):
    """Offline wrap estimate; native glyph metrics are not measured here.

    Allow 0.7 em per ASCII character (about 0.56 em measured) and a full em for
    every other character, wrap greedily at spaces, break words longer than a
    line, use a 1.25 em line pitch and keep one spare line once text wraps.
    Never shorten the text to fit.
    """
    def advance(chars):
        return sum(size * (0.7 if " " <= ch <= "~" else 1.0) for ch in chars)

    lines = 0
    for paragraph in text.split("\n"):
        lines += 1
        used = 0.0
        for word in paragraph.split(" "):
            gap = advance(" ") if used else 0.0
            if used + gap + advance(word) <= width:
                used += gap + advance(word)
                continue
            if used:
                lines += 1
                used = 0.0
            for ch in word:
                if used and used + advance(ch) > width:
                    lines += 1
                    used = 0.0
                used += advance(ch)
    if lines > text.count("\n") + 1:
        lines += 1
    return math.ceil(lines * size * 1.25)


def native_source_path(name, *, source_root: Path):
    return Path(source_root) / CURRENT_NATIVE_SOURCES.get(name, name)


def component(key, typ, fields=None, initial=None):
    c = {"key": key, "type": typ}
    if fields:
        c["fields"] = fields
    if initial is not None:
        c["initialFields"] = initial
    return c


def node(key, children=(), components=(), **slot):
    return {
        "slot": {"key": key, "name": key, **slot},
        "components": list(components),
        "children": list(children),
    }


def rect(key, x=0, y=0, w=1440, h=900, *, anchor="left", parent_width=1440):
    """Horizontal presentation geometry relative to the normal-width parent."""
    left_anchor = 1 if anchor == "right" else 0
    right_anchor = 0 if anchor == "left" else 1
    return component(
        key + "-rect",
        U + "RectTransform",
        {
            "AnchorMin": [left_anchor, 1],
            "AnchorMax": [right_anchor, 1],
            "Pivot": [0, 1],
            "OffsetMin": [x - left_anchor * parent_width, -y - h],
            "OffsetMax": [x + w - right_anchor * parent_width, -y],
        },
    )


def label(key, text, x, y, w, h, size=24, color=TEXT, editable=False, **geometry):
    return node(
        key,
        components=[
            rect(key, x, y, w, h, **geometry),
            component(
                key + "-text",
                U + "Text",
                {"Size": size, "Color": color, "ParseRichText": False},
                {"Content": text} if editable else None,
            )
            if editable
            else component(
                key + "-text",
                U + "Text",
                {"Size": size, "Color": color, "ParseRichText": False, "Content": text},
            ),
        ],
    )


def image(key, x, y, w, h, color=INK, material=None, hit=False, **geometry):
    fields = {"Tint": color, "InteractionTarget": hit}
    if material:
        fields["Material"] = "$component:" + material
    return node(
        key,
        components=[
            rect(key, x, y, w, h, **geometry),
            component(key + "-image", U + "Image", fields),
        ],
    )


def button(key, text, x, y, w=180, h=50, **geometry):
    n = image(key, x, y, w, h, [0.08, 0.19, 0.23, 1], hit=True, **geometry)
    n["components"].append(
        component(key + "-button", U + "Button", {"SendSlotEvents": True})
    )
    n["children"] = [label(key + "-label", text, 14, 8, w - 28, h - 12, 23)]
    return n


def text_input(key, text, x, y, w=195, h=48, **geometry):
    n = {
        "$recipe": "text-input",
        "$with": {
            "key": key,
            "rect": rect(key, x, y, w, h, **geometry)["fields"],
            "text": "$component:" + key + "-value-text",
        },
        "components": [
            component(
                key + "-face",
                U + "Image",
                {"Tint": [0.09, 0.12, 0.15, 1], "InteractionTarget": True},
            )
        ],
        "children": [
            label(key + "-value", text, 10, 6, w - 20, h - 10, 25, editable=True)
        ],
    }
    return n


def state(key, typ, initial):
    return node(
        key + "-slot",
        components=[
            component(key, F + "ValueField<" + typ + ">", initial={"Value": initial})
        ],
    )


def reference(key, typ):
    return node(
        key + "-slot",
        components=[component(key, F + "ReferenceField<" + F + typ + ">")],
    )


def guard_fields(p, row):
    """Registration guard targets, fail-closed until the supplement binds them.

    Null list/provider and index -1 are refused by the row guard; values are set
    only by the verified owned writes (resoloop_io.guard_writes), never declared.
    """
    if not row.uses or len(row.uses) > MAX_ROW_USES:
        raise ValueError("row-use-limit")
    nodes = [node(p + "-provider-slot", components=[component(p + "-provider", PROVIDER_REF)])]
    for k in range(len(row.uses)):
        nodes.append(node(f"{p}-list-{k}-slot", components=[component(f"{p}-list-{k}", LIST_REF)]))
        nodes.append(state(f"{p}-index-{k}", "int", -1))
    return nodes


def row_operation_fields(p, color):
    """Per-row Preview/Command records; runtime-owned, initial values only."""
    return [
        state(p + "-op-stage", "int", 0),
        state(p + "-op-kind", "int", 0),
        state(p + "-op-seq", "int", 0),
        state(p + "-op-id", "int", 0),
        state(p + "-pending-previous", "colorX", color),
        state(p + "-pending-candidate", "colorX", color),
        state(p + "-row-pending", "bool", False),
        state(p + "-input-seq", "int", 0),
        state(p + "-applied-seq", "int", 0),
        state(p + "-result", "int", 0),
        state(p + "-result-seq", "int", 0),
        state(p + "-ack-seq", "int", 0),
        state(p + "-external-confirmed-epoch", "int", -1),
    ]


def session_command_fields():
    """Session Commit records; ui-null-user is always null (value source only)."""
    return [
        state("ui-intent", "int", 0),
        state("ui-command-seq", "int", 0),
        state("ui-command-kind", "int", 0),
        reference("ui-command-user", "User"),
        state("ui-ack-remaining", "int", 0),
        state("ui-ack-wait", "int", 0),
        state("ui-ack-refuse", "int", 0),
        state("ui-ack-fail", "int", 0),
        reference("ui-recover-target", "User"),
        reference("ui-null-user", "User"),
    ]


def row_guard_bindings(registry, index):
    """Guard ports for one row: every registered use gets its own List/Index pair.

    Unused ports repeat use 0 (still the registered material). RowPreview and
    RowCommand must both take their guard ports from here.
    """
    row = registry.rows[index]
    if not row.uses or len(row.uses) > MAX_ROW_USES:
        raise ValueError("row-use-limit")
    p = f"reg-{registry.generation}-row-{index}"
    bindings = {"Provider": {"target": "$member:" + p + "-provider.Reference", "mode": "source"}}
    for k in range(MAX_ROW_USES):
        use = k if k < len(row.uses) else 0
        bindings[f"List{k}"] = {"target": f"$member:{p}-list-{use}.Reference", "mode": "source"}
        bindings[f"Index{k}"] = {"target": f"$member:{p}-index-{use}.Value", "mode": "source"}
    return bindings


def shell(height=900):
    # Footer controls stay just below the table viewport; see FOOTER_HEIGHT.
    footer = height - FOOTER_HEIGHT
    assets = node(
        "ui-assets",
        children=[
            node(
                "ui-front-material-slot",
                components=[
                    component(
                        "ui-front-material",
                        F + "UI_UnlitMaterial",
                        {
                            "ZWrite": "On",
                            "ZTest": "LessOrEqual",
                            "OffsetFactor": 1,
                            "OffsetUnits": 100,
                        },
                    )
                ],
            ),
            node(
                "ui-rear-material-slot",
                components=[
                    component(
                        "ui-rear-material",
                        F + "UI_UnlitMaterial",
                        {
                            "ZWrite": "On",
                            "ZTest": "LessOrEqual",
                            "OffsetFactor": 1,
                            "OffsetUnits": 100,
                            "Sidedness": "Back",
                        },
                    )
                ],
            ),
        ],
    )
    canvas = node(
        "ui-canvas",
        scale=[0.000625] * 3,
        components=[
            component(
                "ui-canvas-component",
                U + "Canvas",
                {"Size": [1440, height], "UnitScale": 1},
            )
        ],
        children=[
            image("ui-backdrop", 0, 0, 1440, height, INK, "ui-front-material", anchor="stretch"),
            image("ui-rear-cover", 0, 0, 1440, height, INK, "ui-rear-material", anchor="stretch"),
            label("ui-title", "AVATAR  /  EMISSION", 32, 24, 950, 52, 36, CYAN, anchor="stretch"),
            label(
                "ui-subtitle",
                "登録済みのアバターだけを編集 · RGB は小数点 . / 指数表記に対応",
                32,
                78,
                1380,
                40,
                21,
                anchor="stretch",
            ),
            button("ui-acquire", "編集を開始", 1195, 28, 210, anchor="right"),
            label(
                "ui-status-display",
                "操作を開始してください",
                32,
                footer + 78,
                720,
                110,
                22,
                CYAN,
                editable=True,
                anchor="stretch",
            ),
            button("ui-reload", "値を再読込", 30, footer + 4),
            button("ui-restore", "元に戻す", 780, footer + 84, anchor="right"),
            button("ui-cancel", "キャンセル", 980, footer + 84, anchor="right"),
            button("ui-ok", "OK", 1180, footer + 84, 220, anchor="right"),
        ],
    )
    drop = image("ui-drop", 30, 126, 1380, 70, [0.045, 0.11, 0.14, 1], hit=True, anchor="stretch")
    drop["components"].append(
        component(
            "ui-drop-receiver",
            U + "ReferenceReceiver<" + F + "Slot>",
            {"Reference": "$member:ui-pending-root.Reference", "Undoable": False},
        )
    )
    # Canvas.Release asks IUIGrabReceiver only from the hovered IUIInteractable.
    drop["components"].append(component("ui-drop-button", U + "Button"))
    # Button owns these live interaction fields after initialization.
    drop_face = drop["components"][1]
    drop_face["initialFields"] = drop_face.pop("fields")
    drop["children"] = [
        label(
            "ui-drop-label",
            "登録済みルートをここへドロップ",
            18,
            15,
            1300,
            44,
            25,
            CYAN,
            anchor="stretch",
            parent_width=1380,
        )
    ]
    canvas["children"].append(drop)
    return assets, canvas


def membership_indices(row):
    return [
        i
        for i, a in enumerate(row.avatars)
        if any(u.mesh_ref in a.slot_refs for u in row.uses)
    ]


def row_texts(registry, i, row):
    members = [j + 1 for j in membership_indices(row)]
    names = " / ".join(
        dict.fromkeys(
            (
                "["
                + ",".join(
                    str(j + 1)
                    for j, a in enumerate(registry.avatars)
                    if u.mesh_ref in a.slot_refs
                )
                + "] "
                if any(u.mesh_ref in a.slot_refs for a in registry.avatars)
                else "[外部] "
            )
            + u.mesh_name
            for u in row.uses
        )
    )
    return f"{i + 1:02}  {row.material_name}   avatars {members}", names


def row_geometry(name_text, names):
    """Row-local tops: full-width name, then RGB/confirm, meshes, values and status."""
    name_height = text_height(name_text, 24)
    controls = 6 + name_height + 6
    meshes = controls + 48 + 8
    meshes_height = text_height(names, 19)
    values = meshes + meshes_height + 6
    return {
        "name_height": name_height,
        "controls": controls,
        "meshes": meshes,
        "meshes_height": meshes_height,
        "original": values,
        "current": values + 24,
        "status": values + 48,
        "height": values + 48 + 28 + 8,
    }


def render_panel(registry: RegistryPlan, *, template: Path) -> dict:
    d = render_registry(registry, template=template)
    # Initial creation uses the already established placement; ongoing apply never owns it.
    d["slot"].pop("position", None)
    if not any(c["key"] == "emission-grabbable" for c in d["components"]):
        d["components"].append(component("emission-grabbable", F + "Grabbable"))
    root = next(n for n in d["children"] if n["slot"]["key"] == "registry-root")
    states = [
        reference("ui-operator", "User"),
        reference("ui-selected-root", "Slot"),
        reference("ui-pending-root", "Slot"),
        reference("ui-null-root", "Slot"),
        state("ui-edit-epoch", "int", 0),
        state("ui-runtime-ready", "bool", False),
        node(
            "ui-ready-slot",
            components=[
                component(
                    "ui-ready",
                    F + "ValueMultiDriver<bool>",
                    {"Drives": list(UI_READY_DRIVES)},
                    {"Value": False},
                )
            ],
        ),
        node(
            "ui-status-slot",
            components=[
                component(
                    "ui-status",
                    F + "ValueMultiDriver<string>",
                    {"Drives": ["$member:ui-status-display-text.Content"]},
                    {"Value": "操作を開始してください"},
                )
            ],
        ),
        state("ui-external-confirmed", "bool", False),
        *session_command_fields(),
    ]
    known = {n["slot"]["key"] for n in root["children"]}
    root["children"] += [n for n in states if n["slot"]["key"] not in known]
    # The scaffold's ui-ready predates the drop Button; add only missing targets.
    ready = next(
        c for n in root["children"] if n["slot"]["key"] == "ui-ready-slot"
        for c in n["components"] if c["key"] == "ui-ready"
    )
    ready["fields"]["Drives"] += [
        t for t in UI_READY_DRIVES if t not in ready["fields"]["Drives"]
    ]
    texts = [row_texts(registry, i, row) for i, row in enumerate(registry.rows)]
    rows = [row_geometry(name_text, names) for name_text, names in texts]
    top = 220 + len(registry.avatars) * 102
    viewport_top = top + 109
    # Only rows of the selected avatar are active, and inactive rows leave the
    # VerticalLayout, so the tallest per-avatar set must fit without scrolling.
    content_height = max(
        (
            sum(g["height"] for g in active) + 8 * (len(active) - 1)
            for active in (
                [g for g, row in zip(rows, registry.rows) if j in membership_indices(row)]
                for j in range(len(registry.avatars))
            )
            if active
        ),
        default=0,
    )
    viewport_height = content_height + 8
    footer = viewport_top + viewport_height
    height = footer + FOOTER_HEIGHT
    if any(n["slot"]["key"] == "ui-canvas" for n in d["children"]):
        canvas = next(n for n in d["children"] if n["slot"]["key"] == "ui-canvas")
    else:
        assets, canvas = shell(height)
        d["children"] += [assets, canvas]
    # registry.json retains the original shell scaffold. Update only its
    # presentation Rect fields and Canvas height; keep its controls, live state
    # and bindings.
    _, responsive_shell = shell(height)
    canvas_component = next(c for c in canvas["components"] if c["key"] == "ui-canvas-component")
    canvas_component["fields"]["Size"] = responsive_shell["components"][0]["fields"]["Size"]
    shell_nodes = {n["slot"]["key"]: n for n in canvas["children"]}
    for presentation in responsive_shell["children"]:
        existing = shell_nodes[presentation["slot"]["key"]]
        existing["components"][0]["fields"] = presentation["components"][0]["fields"]
        if presentation["slot"]["key"] == "ui-drop":
            existing["children"][0]["components"][0]["fields"] = presentation["children"][0]["components"][0]["fields"]
            # The scaffold predates the drop Button; add only components it lacks.
            have = {c["key"] for c in existing["components"]}
            existing["components"] += [c for c in presentation["components"] if c["key"] not in have]
            face = next(c for c in existing["components"] if c["key"] == "ui-drop-image")
            face.pop("fields", None)
            face["initialFields"] = next(
                c for c in presentation["components"] if c["key"] == "ui-drop-image"
            )["initialFields"]
    prefix = "reg-" + registry.generation
    gen = next(n for n in root["children"] if n["slot"]["key"] == prefix)
    shell_canvas = canvas
    canvas = node(prefix + "-ui", components=[rect(prefix + "-ui", h=height, anchor="stretch")])
    shell_canvas["children"].append(canvas)
    gen["children"].append(
        state(prefix + "-ui-generation", "string", registry.generation)
    )
    # Commit refuses 0; set only by the verified owned guard writes.
    gen["children"].append(state(prefix + "-row-count", "int", 0))
    gen["children"].append(
        node(
            prefix + "-visible-slot",
            components=[
                component(
                    prefix + "-visible",
                    F + "ValueMultiDriver<bool>",
                    {"Drives": ["$slot-member:" + prefix + "-ui.IsActive"]},
                    {"Value": False},
                )
            ],
        )
    )
    for i, a in enumerate(registry.avatars):
        gen["children"].append(state(prefix + f"-avatar-{i}-selected", "bool", False))
        canvas["children"].append(
            button(
                prefix + f"-select-{i}",
                f"{i + 1:02}  {a.name}   登録 #{i + 1} · {registry.generation[:8]}",
                30,
                212 + i * 102,
                1380,
                94,
                anchor="stretch",
            )
        )
        # Keep the selector caption and full wrapped path in separate rectangles.
        select = canvas["children"][-1]
        select["children"][0]["components"][0] = rect(
            prefix + f"-select-{i}-label", 14, 8, 1352, 30,
            anchor="stretch", parent_width=1380,
        )
        position = (
            "(" + ", ".join(f"{v:.6g}" for v in a.observed_world_position) + ") m"
            if a.observed_world_position is not None else "取得不可"
        )
        locator = (
            " / ".join(a.path) + f" · 登録 #{i + 1} / {registry.generation[:8]}\n"
            + f"登録時の世界位置: {position} · 観測 {registry.observed_at}"
        )
        canvas["children"][-1]["children"].append(
            label(prefix + f"-avatar-{i}-path", locator, 14, 40, 1350, 46, 16,
                  anchor="stretch", parent_width=1380)
        )
    canvas["children"].append(
        label(
            prefix + "-table-heading",
            "材質 / 共有メッシュ\n元値 / 状態",
            30,
            top,
            650,
            62,
            22,
            CYAN,
            anchor="stretch",
        )
    )
    # Decorative headings share the right-anchored row input coordinates.
    for k, x in zip("rgb", ROW_RGB_X):
        canvas["children"].append(
            label(prefix + "-heading-" + k, k.upper(), 30 + x, top + 66, 205, 35,
                  22, CYAN, anchor="right")
        )
    viewport = image(
        prefix + "-viewport",
        30,
        viewport_top,
        1380,
        viewport_height,
        [0.045, 0.055, 0.07, 1],
        hit=True,
        anchor="stretch",
    )
    viewport["components"].append(
        component(prefix + "-viewport-mask", U + "Mask", {"ShowMaskGraphic": True})
    )
    content = node(
        prefix + "-table",
        components=[
            component(
                prefix + "-table-rect",
                U + "RectTransform",
                {"AnchorMin": [0, 1], "AnchorMax": [1, 1], "Pivot": [0, 1]},
            ),
            component(
                prefix + "-layout",
                U + "VerticalLayout",
                {"Spacing": 8, "ForceExpandWidth": True, "ForceExpandHeight": False},
            ),
            component(
                prefix + "-fitter",
                U + "ContentSizeFitter",
                {"HorizontalFit": "Disabled", "VerticalFit": "PreferredSize"},
            ),
            component(
                prefix + "-scroll",
                U + "ScrollRect",
                {"ViewportOverride": "$component:" + prefix + "-viewport-rect"},
            ),
        ],
    )
    for i, row in enumerate(registry.rows):
        p = f"{prefix}-row-{i}"
        name_text, names = texts[i]
        g = rows[i]
        externals = {
            a.ref: row.external_mesh_refs(a.ref)
            for a in registry.avatars
            if any(u.mesh_ref in a.slot_refs for u in row.uses)
        }
        r = image(p + "-view", 0, i * 145, 1380, 138, [0.065, 0.08, 0.10, 1])
        r["components"][0] = component(p + "-view-rect", U + "RectTransform")
        r["components"].append(
            component(
                p + "-layout-element",
                U + "LayoutElement",
                {"PreferredHeight": g["height"], "MinHeight": g["height"]},
            )
        )
        r["children"] = [
            label(
                p + "-name",
                name_text,
                12,
                6,
                1356,
                g["name_height"],
                24,
                anchor="stretch",
                parent_width=1380,
            ),
            label(p + "-meshes", names, 12, g["meshes"], 1356, g["meshes_height"], 19,
                  anchor="stretch", parent_width=1380),
            label(
                p + "-original-label",
                "元値の取得待ち",
                97,
                g["original"],
                1271,
                23,
                16,
                editable=True,
                anchor="stretch",
                parent_width=1380,
            ),
            label(p + "-original-caption", "元値", 12, g["original"], 80, 23, 16, parent_width=1380),
            label(p + "-current-caption", "現在値", 12, g["current"], 80, 23, 16, parent_width=1380),
            label(p + "-current-label", "現在値の取得待ち", 97, g["current"], 1271, 23, 16, editable=True,
                  anchor="stretch", parent_width=1380),
            label(
                p + "-status-label",
                "外部共有あり・確認が必要" if any(externals.values()) else "未編集",
                12,
                g["status"],
                1356,
                28,
                16,
                CYAN,
                editable=True,
                anchor="stretch",
                parent_width=1380,
            ),
        ]
        for j, k in enumerate("rgb"):
            r["children"].append(
                text_input(p + "-" + k, str(row.color[k]), ROW_RGB_X[j], g["controls"], 205,
                           anchor="right", parent_width=1380)
            )
        confirm = button(p + "-confirm", "外部共有を確認", ROW_CONFIRM_X, g["controls"], 220, 48,
                         anchor="right", parent_width=1380)
        confirm["components"].append(
            component(
                p + "-confirm-toggle",
                F + "ButtonToggle",
                {"TargetValue": "$member:" + p + "-external-confirmed.Value"},
            )
        )
        # Button owns these live interaction fields after initialization.
        face = confirm["components"][1]
        face["initialFields"] = face.pop("fields")
        r["children"].append(confirm)
        content["children"].append(r)
        color = [row.color[k] for k in ("r", "g", "b", "a")]
        target = next(c for c in root["children"] if c["slot"]["key"] == prefix)
        rownode = next(c for c in target["children"] if c["slot"]["key"] == p)
        rownode["children"] += [
            state(p + "-original", "colorX", color),
            state(p + "-last-applied", "colorX", color),
            state(p + "-preview-written", "bool", False),
            state(p + "-snapshot-epoch", "int", -1),
            state(p + "-valid", "bool", False),
            state(p + "-dirty", "bool", False),
            state(p + "-draft", "colorX", color),
            state(p + "-current", "colorX", color),
            node(p + "-current-copy-slot", components=[component(p + "-current-copy", F + "ValueCopy<colorX>", {"WriteBack": False, "Target": "$member:" + p + "-current.Value"})]),
            state(p + "-profile-preserved", "bool", False),
            node(
                p + "-selected-slot",
                components=[
                    component(
                        p + "-selected",
                        F + "ValueMultiDriver<bool>",
                        {"Drives": ["$slot-member:" + p + "-view.IsActive"]},
                        {"Value": False},
                    )
                ],
            ),
            state(p + "-external-confirmed", "bool", False),
            state(p + "-external", "bool", any(externals.values())),
            node(
                p + "-can-edit-slot",
                components=[
                    component(
                        p + "-can-edit",
                        F + "ValueMultiDriver<bool>",
                        {
                            "Drives": [
                                f"$member:uix-text-input--{p}-{k}-{role}.Enabled"
                                for k in "rgb"
                                for role in ("button", "input")
                            ]
                        },
                        {"Value": False},
                    )
                ],
            ),
            *row_operation_fields(p, color),
            *guard_fields(p, row),
        ]
    viewport["children"] = [content]
    canvas["children"].append(viewport)
    canvas["children"].append(
        label(
            prefix + "-observed",
            "登録時の観測: "
            + registry.observed_at
            + "  · 構成変更・別ワールドでは再登録",
            240,
            footer + 8,
            1170,
            68,
            18,
            anchor="stretch",
        )
    )
    return d


def bench_panel():
    assets, canvas = shell()
    canvas["children"] += [
        text_input("bench-input", "1e-3", 32, 260, 700),
        label(
            "bench-help",
            "Native TextField / TextEditor + ReferenceReceiver<Slot>",
            32,
            340,
            1380,
            80,
        ),
    ]
    return {
        "schemaVersion": "1",
        "ownership": {"key": "tools-emission-editor-uibench"},
        "slot": {
            "key": "ui-bench-root",
            "name": "ResoLoop_Test_EmissionEditor_UIBench",
            "parent": "Root",
            "position": [1, 1.5, 2],
        },
        "components": [
            component("bench-objectroot", F + "ObjectRoot"),
            component("bench-grabbable", F + "Grabbable"),
        ],
        "children": [assets, reference("ui-pending-root", "Slot"), canvas],
    }


def session_manifest(registry, *, world_state, deploy_state, source_directory, source_root: Path):
    """Exact native event/state interface. Task5 adds Preview/Commit, not Python callbacks."""
    p = "reg-" + registry.generation

    def src(key, member="Value"):
        return {"target": "$member:" + key + "." + member, "mode": "source"}

    def drv(key, member="Value"):
        return {"target": "$member:" + key + "." + member, "mode": "drive"}

    shared = {
        "Operator": src("ui-operator", "Reference"),
        "Ready": src("registry-ready"),
        "UiReady": src("ui-runtime-ready"),
        "Busy": src("registry-busy"),
        "RecoveryPending": src("registry-pending"),
        "PreviewActive": src("registry-preview-active"),
        "OperationStage": src("registry-operation-stage"),
        "Status": src("ui-status"),
    }
    modules = [
        {
            "name": "ui-session",
            "source": "Session.pg",
            "module": "Session",
            "dependsOn": [],
            "bindings": {
                **shared,
                "AcquireButton": {
                    "target": "$component:ui-acquire-button",
                    "mode": "source",
                },
                "OperatorGuard": src("registry-operator"),
                "UiReady": src("ui-runtime-ready"),
                "CanInteract": drv("ui-ready"),
            },
        }
    ]
    modules.append(
        {
            "name": p + "-generation",
            "source": "Generation.pg",
            "module": "Generation",
            "dependsOn": [],
            "bindings": {
                "Generation": src(p + "-ui-generation"),
                "Active": src("registry-active"),
                "Visible": drv(p + "-visible"),
            },
        }
    )
    modules.append(
        {
            "name": p + "-drop-guard",
            "source": "DropGuard.pg",
            "module": "DropGuard",
            "dependsOn": ["ui-session"],
            "bindings": {
                "PendingRoot": src("ui-pending-root", "Reference"),
                "Operator": src("ui-operator", "Reference"),
                "Status": src("ui-status"),
                **{
                    f"Root{j}": src(
                        p + f"-avatar-{j}"
                        if j < len(registry.avatars)
                        else "ui-null-root",
                        "Reference",
                    )
                    for j in range(16)
                },
            },
        }
    )
    for i, a in enumerate(registry.avatars):
        modules.append(
            {
                "name": p + f"-selection-{i}",
                "source": "Selection.pg",
                "module": "Selection",
                "dependsOn": ["ui-session"],
                "bindings": {
                    **shared,
                    "SelectButton": {
                        "target": f"$component:{p}-select-{i}-button",
                        "mode": "source",
                    },
                    "RegisteredRoot": src(p + f"-avatar-{i}", "Reference"),
                    "PendingRoot": src("ui-pending-root", "Reference"),
                    "SelectedRoot": src("ui-selected-root", "Reference"),
                    "EditEpoch": src("ui-edit-epoch"),
                    "Selected": drv(p + f"-avatar-{i}-selected"),
                },
            }
        )
    for i, row in enumerate(registry.rows):
        r = p + f"-row-{i}"
        membership = membership_indices(row)
        modules.append(
            {
                "name": r + "-view",
                "source": "RowView.pg",
                "module": "RowView",
                "dependsOn": ["ui-session"],
                "bindings": {
                    "SelectedRoot": src("ui-selected-root", "Reference"),
                    **{
                        f"Root{j}": src(
                            p + f"-avatar-{membership[j]}"
                            if j < len(membership)
                            else "ui-null-root",
                            "Reference",
                        )
                        for j in range(16)
                    },
                    "Operator": src("ui-operator", "Reference"),
                    "Ready": src("registry-ready"),
                    "UiReady": src("ui-runtime-ready"),
                    "Busy": src("registry-busy"),
                    "RecoveryPending": src("registry-pending"),
                    "OperationStage": src("registry-operation-stage"),
                    "EditEpoch": src("ui-edit-epoch"),
                    "SnapshotEpoch": src(r + "-snapshot-epoch"),
                    "External": src(r + "-external"),
                    "ExternalConfirmed": src(r + "-external-confirmed"),
                    "Selected": drv(r + "-selected"),
                    "CanEdit": drv(r + "-can-edit"),
                    "CanConfirm": drv(r + "-confirm-button", "Enabled"),
                },
            }
        )
        modules.append(
            {
                "name": r + "-begin",
                "source": "BeginRow.pg",
                "module": "BeginRow",
                "dependsOn": ["ui-session"],
                "bindings": {
                    "SourceField": src(r + "-source", "Reference"),
                    "UiReady": src("ui-runtime-ready"),
                    "Operator": src("ui-operator", "Reference"),
                    "Selected": src(r + "-selected"),
                    "EditEpoch": src("ui-edit-epoch"),
                    "SnapshotEpoch": src(r + "-snapshot-epoch"),
                    "Original": src(r + "-original"),
                    "LastApplied": src(r + "-last-applied"),
                    "PreviewWritten": src(r + "-preview-written"),
                    "RecoveryPending": src("registry-pending"),
                    "OperationStage": src("registry-operation-stage"),
                    **{
                        "Input" + k.upper(): src(r + "-" + k + "-value-text", "Content")
                        for k in "rgb"
                    },
                },
            }
        )
        modules.append(
            {
                "name": r + "-value",
                "source": "RowValue.pg",
                "module": "RowValue",
                "dependsOn": [r + "-begin"],
                "bindings": {
                    "SourceField": src(r + "-source", "Reference"),
                    "Baseline": src(r + "-original"),
                    **{
                        "Input" + k.upper(): src(r + "-" + k + "-value-text", "Content")
                        for k in "rgb"
                    },
                    "Valid": drv(r + "-valid"),
                    "Dirty": drv(r + "-dirty"),
                    "Draft": drv(r + "-draft"),
                    "Current": drv(r + "-current"),
                    "ProfilePreserved": drv(r + "-profile-preserved"),
                },
            }
        )
        modules.append(
            {
                "name": r + "-presentation",
                "source": "RowPresentation.pg",
                "module": "RowPresentation",
                "dependsOn": [r + "-value"],
                "bindings": {
                    "ReloadButton": {"target": "$component:ui-reload-button", "mode": "source"},
                    "SourceField": src(r + "-source", "Reference"),
                    "Operator": src("ui-operator", "Reference"),
                    **{name: src(r + "-" + key) for name, key in {
                        "Original": "original", "LastApplied": "last-applied", "Current": "current",
                        "Valid": "valid", "Dirty": "dirty", "PreviewWritten": "preview-written",
                        "External": "external", "ExternalConfirmed": "external-confirmed", "CanEdit": "can-edit",
                    }.items()},
                    "RecoveryPending": src("registry-pending"),
                    "OperationStage": src("registry-operation-stage"),
                    **{"Input" + k.upper(): src(r + "-" + k + "-value-text", "Content") for k in "rgb"},
                    "OriginalDisplay": drv(r + "-original-label-text", "Content"),
                    "CurrentDisplay": drv(r + "-current-label-text", "Content"),
                    "RowStatus": drv(r + "-status-label-text", "Content"),
                },
            }
        )
    for module in modules:
        kind = module["module"]
        bindings = module["bindings"]
        if kind in ("Selection", "RowView", "BeginRow"):
            bindings["ActiveGeneration"] = src(p + "-visible")
        if kind in ("BeginRow", "RowValue"):
            row_key = module["name"].rsplit("-", 1)[0]
            bindings["Current"] = src(row_key + "-current")
        if kind in ("RowValue", "RowPresentation"):
            bindings.pop("SourceField")
    base = Path(source_root).resolve()
    stage = Path(source_directory).resolve()
    if stage == base or stage.is_relative_to(base):
        raise ValueError("compiler-stage-inside-authoring-tree")
    stage.mkdir(parents=True, exist_ok=False)
    for module in modules:
        source = native_source_path(module["source"], source_root=base)
        target = stage / source.name
        if not target.exists():
            shutil.copy2(source, target)
        if source.read_bytes() != target.read_bytes():
            raise ValueError("isolated-source-copy-mismatch")
        module["source"] = str(target)
    manifests = []
    for module in modules:
        kind = module["module"]
        if kind in ("Session", "DropGuard"):
            parent_key = "registry-root"
        elif kind == "Generation":
            parent_key = p
        elif kind == "Selection":
            parent_key = p + "-avatar-" + module["name"].rsplit("-", 1)[1] + "-slot"
        else:
            parent_key = module["name"].rsplit("-", 1)[0]
        checkpoint = Path(deploy_state).resolve()
        manifests.append({
            "schemaVersion": "1", "projectDirectory": str(stage),
            "parent": "$slot:" + parent_key,
            "worldState": str(Path(world_state).resolve()),
            "deployState": str(checkpoint.with_name(checkpoint.stem + "-" + parent_key + checkpoint.suffix)),
            "modules": [{**module, "dependsOn": []}],
        })
    validate_runtime_destinations(manifests)
    # This is an inventory, deliberately not a CLI Flux manifest. Each saved child
    # has its own supported parent and state; no replay can collapse all instances.
    return {"format": "emission-ui-manifest-bundle-v1", "manifests": manifests}


def validate_runtime_destinations(manifests):
    destinations = set()
    for manifest in manifests:
        for module in manifest["modules"]:
            destination = (manifest["parent"], module["module"])
            if destination in destinations:
                raise ValueError("duplicate-native-module-destination")
            destinations.add(destination)



class UIPanelPort(ResoloopPanelPort):
    """Ready is published only after native UI and graph bindings are installed."""

    def render_declaration(self, plan):
        return render_panel(plan, template=self.read.context.registry_template)

    def stage(self, plan):
        library = os.environ.get("RESONITE_MANAGED_DATA_PATH")
        if not library or not Path(library).is_dir():
            raise ValueError(
                "Set RESONITE_MANAGED_DATA_PATH to current runtime DLLs before UI registration"
            )
        self._set("ui-runtime-ready", "Value", False)
        super().stage(plan)
        for index, row in enumerate(plan.rows):
            key = f"reg-{plan.generation}-row-{index}"
            observed = self._observe_owned((
                (key + "-current-copy", "WriteBack"),
                (key + "-current-copy", "Target"),
                (key + "-current", "Value"),
            ))
            # Recheck live safety settings after apply, before exposing an external field.
            current_id = observed[(key + "-current", "Value")].get("id")
            if (
                observed[(key + "-current-copy", "WriteBack")].get("value") is not False
                or not current_id
                or observed[(key + "-current-copy", "Target")].get("targetId") != current_id
            ):
                raise ValueError("ui-current-copy-unsafe")
            self._set(key + "-current-copy", "Source", row.field_ref)
        stage = (self.read.evidence / "deploy-sources").resolve()
        bundle = session_manifest(
            plan, world_state=self.read.state, deploy_state=self.read.flux_state,
            source_directory=stage, source_root=self.read.context.source_root,
        )
        save_new(stage / "ui-current.bundle.json", bundle)
        for index, single in enumerate(bundle["manifests"]):
            module = single["modules"][0]
            single_path = stage / f"ui-{index:03d}.flux.json"
            save_new(single_path, single)
            self.read.run("flux", "validate-manifest", single_path)
            preserved_hashes(self.read.preservation_baseline)
            if self.session != self.operation_session:
                raise ValueError("session-changed-before-ui-deploy")
            try:
                result = self.read.run("flux", "deploy-manifest", single_path, "--library-path", library)
            finally:
                preserved_hashes(self.read.preservation_baseline)
            if self.session != self.operation_session:
                raise ValueError("session-changed-after-ui-deploy")
            source = Path(module["source"])
            if source.read_bytes() != native_source_path(source.name, source_root=self.read.context.source_root).read_bytes():
                raise ValueError("isolated-source-changed-during-deploy")
            deployed_modules = result.get("modules", [])
            if (
                result.get("success") is not True
                or len(deployed_modules) != 1
                or deployed_modules[0].get("name") != module["name"]
                or not deployed_modules[0].get("moduleSlotIdAfter")
            ):
                raise ValueError("ui-module-deploy-incomplete")
            bindings = deployed_modules[0].get("bindings", [])
            if len(bindings) != len(module["bindings"]) or {
                b.get("name"): (b.get("selector"), b.get("mode"))
                for b in bindings if b.get("targetId")
            } != {
                name: (binding["target"], binding["mode"])
                for name, binding in module["bindings"].items()
            }:
                raise ValueError("ui-module-bindings-incomplete")
            for deployed in result["modules"]:
                target = deployed.get("moduleSlotIdAfter")
                if target:
                    self.read.run("inspect", target, "--depth", "3", "--members")
        self.enable_verified_ui(plan)

    def enable_verified_ui(self, plan):
        """Publish usability only after real native reads match registered raw sources."""
        self._set("ui-runtime-ready", "Value", False)
        self.verify_native_sources(plan)
        self._set("ui-runtime-ready", "Value", True)
        self.ui_staged = plan.generation

    def verify_native_sources(self, plan):
        """Read-only semantic gate; receipts and reference types alone are insufficient."""
        if self.session != plan.session:
            raise ValueError("session-changed-before-native-readback")
        prefix = "reg-" + plan.generation
        observed = self._observe_owned(
            (f"{prefix}-row-{index}-{suffix}", member)
            for index in range(len(plan.rows))
            for suffix, member in (("current", "Value"), ("source", "Reference"))
        )
        for index, row in enumerate(plan.rows):
            material = self.read.run("component", "inspect", row.material_ref)
            raw = material["members"]["EmissionColor"]
            key = f"{prefix}-row-{index}"
            if (
                raw.get("id") != row.field_ref
                or raw.get("value") != row.color
                or observed[(key + "-source", "Reference")].get("targetId") != row.field_ref
                or observed[(key + "-current", "Value")].get("value") != raw.get("value")
            ):
                raise ValueError("ui-native-source-mismatch")
        if self.session != plan.session:
            raise ValueError("session-changed-after-native-readback")

    def verify_staged(self, plan):
        return getattr(
            self, "ui_staged", None
        ) == plan.generation and super().verify_staged(plan)
