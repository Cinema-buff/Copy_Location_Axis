bl_info = {
    "name": "Copy Location From Selected",
    "author": "Claude",
    "version": (1, 1, 0),
    "blender": (2, 80, 0),
    "location": "View3D > Sidebar (N) > Copy Location",
    "description": "Pick one selected object as the source; all other selected objects match where it is displayed in 3D space on chosen axes",
    "category": "Object",
}

import bpy
from mathutils import Vector
from bpy.props import BoolProperty, EnumProperty, PointerProperty

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

            mw = ob.matrix_world.copy()
            mw.translation += delta
            ob.matrix_world = mw

            if s.auto_key:
                ob.keyframe_insert(data_path="location")

            context.view_layer.update()
            count += 1

        self.report({"INFO"}, f"Matched {count} object(s) to '{src.name}'")
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
            return

        layout.prop(s, "source", text="From")

        row = layout.row(align=True)
        row.prop(s, "use_x", toggle=True)
        row.prop(s, "use_y", toggle=True)
        row.prop(s, "use_z", toggle=True)

        layout.prop(s, "mode", text="Match")
        layout.prop(s, "auto_key")
        layout.operator(CLA_OT_copy_location.bl_idname, icon="SNAP_ON")


classes = (CLA_Settings, CLA_OT_copy_location, CLA_PT_panel)


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
