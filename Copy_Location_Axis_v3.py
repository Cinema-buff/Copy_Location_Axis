bl_info = {
    "name": "Copy Location From Selected",
    "author": "Claude",
    "version": (1, 2, 0),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar (N) > Copy Location",
    "description": "Pick one selected object as the source; all other selected objects match where it is displayed in 3D space on chosen axes. Clear undoes every copy made so far",
    "category": "Object",
}

import json

import bpy
from mathutils import Matrix, Vector
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

# Types that have no visible geometry box: their displayed position is their origin
NO_GEOMETRY = {"EMPTY", "LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE"}


def displayed_position(ob, depsgraph, mode):
    """World-space position of where the object is displayed.

    DISPLAYED: center of the object's evaluated geometry bounding box
               (includes modifiers, shape keys, constraints, parents, animation).
               Falls back to the origin for empties, lights, cameras, etc.
    ORIGIN:    the object's origin in world space.
    """
    ob_eval = ob.evaluated_get(depsgraph)
    origin = ob_eval.matrix_world.translation.copy()

    if mode == "ORIGIN" or ob.type in NO_GEOMETRY:
        return origin

    try:
        corners = [ob_eval.matrix_world @ Vector(c) for c in ob_eval.bound_box]
    except Exception:
        return origin
    if not corners:
        return origin

    center = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
    return center


# ---------------------------------------------------------------------------
# History (what Clear undoes)
#
# The history is kept in a property on the scene (a JSON string), so Blender's own
# Ctrl+Z / Ctrl+Shift+Z keep it in step with the scene: if you undo a copy with Ctrl+Z,
# that copy also disappears from the history and Clear will not try to undo it twice.
# ---------------------------------------------------------------------------

def _location_fcurves(ob):
    """All location F-curves of the object (works on old and new Blender action APIs)."""
    ad = ob.animation_data
    act = ad.action if ad else None
    if act is None:
        return []
    fcs = getattr(act, "fcurves", None)
    if fcs is None:
        fcs = []
        try:
            slot = ad.action_slot
            for layer in act.layers:
                for strip in layer.strips:
                    cb = strip.channelbag(slot)
                    if cb:
                        fcs.extend(cb.fcurves)
        except Exception:
            pass
    return [fc for fc in fcs if fc.data_path == "location"]


def _key_state(ob, frame):
    """For X, Y, Z: [had a key on this frame, that key's value]."""
    state = [[False, 0.0], [False, 0.0], [False, 0.0]]
    for fc in _location_fcurves(ob):
        if 0 <= fc.array_index <= 2:
            for kp in fc.keyframe_points:
                if abs(kp.co[0] - frame) < 1e-4:
                    state[fc.array_index] = [True, kp.co[1]]
                    break
    return state


def _load_history(s):
    try:
        data = json.loads(s.history or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(s, data):
    s.history = json.dumps(data)


def source_items(self, context):
    items = []
    for i, ob in enumerate(context.selected_objects):
        items.append((ob.name, ob.name, f"Copy position from {ob.name} ({ob.type.title()})", i))
    if not items:
        items.append(("NONE", "(select 2+ objects)", "", 0))
    return items


class CLA_Settings(bpy.types.PropertyGroup):
    source: EnumProperty(
        name="Source",
        description="The selected object whose position the others will copy",
        items=source_items,
    )
    use_x: BoolProperty(name="X", default=True)
    use_y: BoolProperty(name="Y", default=True)
    use_z: BoolProperty(name="Z", default=True)
    mode: EnumProperty(
        name="Position",
        description="Which position to match",
        items=[
            ("DISPLAYED", "Displayed", "Center of the geometry as it appears in the 3D viewport "
                                       "(accounts for modifiers, constraints, parents, animation)"),
            ("ORIGIN", "Origin", "The object's origin point (the small dot) in world space"),
        ],
        default="DISPLAYED",
    )
    auto_key: BoolProperty(
        name="Insert Keyframes",
        description="Insert a location keyframe on each affected object at the current frame",
        default=False,
    )
    history: StringProperty(
        name="History",
        description="Internal: what Copy Location has changed, so Clear can undo it",
        default="[]",
        options={"HIDDEN"},
    )


class CLA_OT_copy_location(bpy.types.Operator):
    bl_idname = "object.copy_location_axis"
    bl_label = "Copy Location"
    bl_description = "Move all other selected objects so they appear at the source's position on the chosen axes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) >= 2

    def execute(self, context):
        s = context.scene.cla_settings
        axes = (s.use_x, s.use_y, s.use_z)

        if not any(axes):
            self.report({"WARNING"}, "Enable at least one axis (X, Y or Z)")
            return {"CANCELLED"}

        src = bpy.data.objects.get(s.source)
        if src is None or src not in context.selected_objects:
            self.report({"WARNING"}, "Pick a source from the selected objects")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        target_pos = displayed_position(src, depsgraph, s.mode)

        history = _load_history(s)
        frame = context.scene.frame_current

        count = 0
        for ob in list(context.selected_objects):
            if ob == src:
                continue

            depsgraph = context.evaluated_depsgraph_get()
            current = displayed_position(ob, depsgraph, s.mode)

            # Shift the object by the difference, only on enabled axes
            delta = Vector((0.0, 0.0, 0.0))
            for i in range(3):
                if axes[i]:
                    delta[i] = target_pos[i] - current[i]

            # Remember exactly how this object was before we touch it
            entry = {
                "n": ob.name,
                "mb": [list(row) for row in ob.matrix_basis],
                "a": bool(ob.animation_data and ob.animation_data.action),
                "f": frame,
                "k": _key_state(ob, frame) if s.auto_key else None,
            }
            history.append(entry)

            mw = ob.matrix_world.copy()
            mw.translation += delta
            ob.matrix_world = mw

            if s.auto_key:
                ob.keyframe_insert(data_path="location", frame=frame)

            context.view_layer.update()
            count += 1

        _save_history(s, history)
        self.report({"INFO"}, f"Matched {count} object(s) to '{src.name}'")
        return {"FINISHED"}


class CLA_OT_clear(bpy.types.Operator):
    bl_idname = "object.copy_location_axis_clear"
    bl_label = "Clear"
    bl_description = ("Undo everything Copy Location has done so far: put every moved object back "
                      "where it was and remove the keyframes this add-on inserted "
                      "(keys that already existed are put back as they were)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "cla_settings", None)
        return s is not None and len(_load_history(s)) > 0

    def execute(self, context):
        s = context.scene.cla_settings
        history = _load_history(s)
        if not history:
            self.report({"INFO"}, "Nothing to clear")
            return {"CANCELLED"}

        restored = set()
        missing = 0

        # Newest first, so an object copied several times ends up exactly as it started
        for entry in reversed(history):
            ob = bpy.data.objects.get(entry.get("n", ""))
            if ob is None:
                missing += 1
                continue

            original = Matrix(entry["mb"])
            frame = entry.get("f")
            keys = entry.get("k")

            if keys is not None and frame is not None:
                for i in range(3):
                    existed, value = keys[i]
                    if existed:
                        # A key was already here: put its old value back
                        ob.location[i] = value
                        try:
                            ob.keyframe_insert(data_path="location", index=i, frame=frame)
                        except Exception:
                            pass
                    else:
                        # We created this key: remove it
                        try:
                            ob.keyframe_delete(data_path="location", index=i, frame=frame)
                        except Exception:
                            pass

                # Don't leave an empty animation behind on an object that had none
                if not entry.get("a", False) and not _location_fcurves(ob):
                    if ob.animation_data and ob.animation_data.action is not None:
                        try:
                            ob.animation_data_clear()
                        except Exception:
                            pass

            ob.matrix_basis = original
            restored.add(ob.name)

        _save_history(s, [])
        context.view_layer.update()
        for area in context.screen.areas:
            area.tag_redraw()

        msg = f"Cleared: restored {len(restored)} object(s)"
        if missing:
            msg += f" ({missing} entr{'y' if missing == 1 else 'ies'} skipped: object no longer exists)"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class CLA_PT_panel(bpy.types.Panel):
    bl_label = "Copy Location"
    bl_idname = "CLA_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Copy Location"

    def draw(self, context):
        layout = self.layout
        s = context.scene.cla_settings

        if len(context.selected_objects) < 2:
            layout.label(text="Select at least 2 objects", icon="INFO")
        else:
            layout.prop(s, "source", text="From")

            row = layout.row(align=True)
            row.prop(s, "use_x", toggle=True)
            row.prop(s, "use_y", toggle=True)
            row.prop(s, "use_z", toggle=True)

            layout.prop(s, "mode", text="Match")
            layout.prop(s, "auto_key")
            layout.operator(CLA_OT_copy_location.bl_idname, icon="SNAP_ON")

        # Clear is always available when there is something to undo,
        # whatever is selected right now
        n = len(_load_history(s))
        if n:
            layout.separator()
            layout.operator(CLA_OT_clear.bl_idname,
                            text=f"Clear ({n} change{'s' if n != 1 else ''})",
                            icon="LOOP_BACK")


classes = (CLA_Settings, CLA_OT_copy_location, CLA_OT_clear, CLA_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.cla_settings = PointerProperty(type=CLA_Settings)


def unregister():
    del bpy.types.Scene.cla_settings
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
