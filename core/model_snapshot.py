"""Write a self-contained model library without changing source datablocks."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import bpy


def _model_objects(armature):
    objects = {armature, *armature.children_recursive}
    # An armature modifier can bind a mesh without parenting it to the rig.
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and any(
            modifier.type == "ARMATURE" and modifier.object == armature
            for modifier in obj.modifiers
        ):
            objects.update((obj, *obj.children_recursive))
    return sorted(objects, key=lambda obj: obj.name)


def _verify_image_source(image):
    expected = image.get("fmodel_content_sha256", "")
    if not expected:
        return
    if image.packed_file:
        actual = hashlib.sha256(image.packed_file.data).hexdigest()
    else:
        path = Path(image.get("fmodel_source_path") or bpy.path.abspath(image.filepath))
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(f"Texture changed since import; reimport before saving: {image.name}")


def write_model_snapshot(armature, destination, model_name):
    """Copy the rig and its dependencies, pack textures, and verify the library."""
    prefix = model_name + "__"
    owned = []
    temporary_path = None
    objects, materials, images, groups = {}, {}, {}, {}

    def own(manager, datablock):
        owned.append((manager, datablock))
        return datablock

    def snapshot_image(source):
        if source in images:
            return images[source]
        if source.source not in {"FILE", "GENERATED"}:
            raise ValueError(f"Model snapshot does not support {source.source} image: {source.name}")
        _verify_image_source(source)
        copied = own(bpy.data.images, source.copy())
        images[source] = copied
        if not copied.packed_file:
            copied.pack()
        if not copied.packed_file:
            raise ValueError(f"Texture could not be packed into the model library: {source.name}")
        _verify_image_source(copied)
        return copied

    def snapshot_tree(tree):
        for node in tree.nodes:
            if hasattr(node, "image") and node.image is not None:
                node.image = snapshot_image(node.image)
            if node.type == "GROUP" and node.node_tree is not None:
                source = node.node_tree
                if source not in groups:
                    copied = own(bpy.data.node_groups, source.copy())
                    groups[source] = copied
                    snapshot_tree(copied)
                node.node_tree = groups[source]

    def snapshot_material(source):
        if source not in materials:
            copied = own(bpy.data.materials, source.copy())
            copied.name = prefix + source.name
            materials[source] = copied
            if copied.node_tree:
                snapshot_tree(copied.node_tree)
        return materials[source]

    try:
        collection = own(bpy.data.collections, bpy.data.collections.new(model_name))
        for source in _model_objects(armature):
            copied = own(bpy.data.objects, source.copy())
            objects[source] = copied
            copied.name = prefix + source.name
            if source.data is not None:
                manager = {"ARMATURE": bpy.data.armatures, "MESH": bpy.data.meshes,
                           "CURVE": bpy.data.curves, "FONT": bpy.data.curves,
                           "SURFACE": bpy.data.curves, "META": bpy.data.metaballs,
                           "LIGHT": bpy.data.lights, "CAMERA": bpy.data.cameras}.get(source.type)
                if manager is None:
                    raise ValueError(f"Unsupported model dependency: {source.type} {source.name}")
                copied.data = own(manager, source.data.copy())
                copied.data.name = prefix + source.data.name
                if hasattr(copied.data, "materials"):
                    for index, material in enumerate(copied.data.materials):
                        if material is not None:
                            copied.data.materials[index] = snapshot_material(material)
                for slot in copied.material_slots:
                    if slot.link == "OBJECT" and slot.material is not None:
                        slot.material = snapshot_material(slot.material)
            collection.objects.link(copied)

        def remap_references(owner):
            for prop in owner.bl_rna.properties:
                if prop.type == "POINTER" and not prop.is_readonly:
                    value = getattr(owner, prop.identifier, None)
                    if isinstance(value, bpy.types.Object) and value in objects:
                        setattr(owner, prop.identifier, objects[value])

        for source, copied in objects.items():
            copied.parent = objects.get(source.parent)
            if source.parent is not None and copied.parent is None:
                copied.matrix_world = source.matrix_world.copy()
            for owner in (*copied.modifiers, *copied.constraints):
                remap_references(owner)
            if copied.pose:
                for bone in copied.pose.bones:
                    for constraint in bone.constraints:
                        remap_references(constraint)
            for owner in (copied, copied.data):
                animation = getattr(owner, "animation_data", None)
                if animation:
                    for driver in animation.drivers:
                        for variable in driver.driver.variables:
                            for target in variable.targets:
                                if isinstance(target.id, bpy.types.Object) and target.id in objects:
                                    target.id = objects[target.id]

        destination = Path(destination)
        fd, name = tempfile.mkstemp(prefix=".fmodel-snapshot-", suffix=".blend", dir=destination.parent)
        os.close(fd)
        temporary_path = Path(name)
        bpy.data.libraries.write(str(temporary_path), {collection}, fake_user=True)
        expected = {
            "collections": {collection.name},
            "objects": {obj.name for obj in objects.values()},
            "meshes": {obj.data.name for obj in objects.values() if obj.type == "MESH"},
            "materials": {material.name for material in materials.values()},
            "images": {image.name for image in images.values()},
        }
        with bpy.data.libraries.load(str(temporary_path), link=False) as (available, _):
            for kind, names in expected.items():
                missing = names.difference(getattr(available, kind))
                if missing:
                    raise ValueError(f"Model library is missing {kind}: {sorted(missing)}")
        os.replace(temporary_path, destination)
        temporary_path = None
        return {
            "object_count": len(objects),
            "mesh_count": len(expected["meshes"]),
            "material_count": len(materials),
            "image_count": len(images),
            "texture_storage": "packed_snapshot",
            "library_verified": True,
        }
    finally:
        # Remove only copies owned by this operation; no selection or mode changes.
        for manager, datablock in reversed(owned):
            try:
                manager.remove(datablock, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
