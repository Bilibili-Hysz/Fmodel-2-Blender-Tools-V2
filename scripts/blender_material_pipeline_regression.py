from pathlib import Path
import ast, hashlib, importlib, json, os, shutil, sys
import bpy
sys.dont_write_bytecode = True
args = sys.argv[sys.argv.index("--") + 1:]
module_root, fixtures, evidence, label, v2_root = map(Path, args[:5])
label = str(label)
sys.path.insert(0, str(module_root))
from io_scene_ueformat.importer.import_context import UEFormatImport
from io_scene_ueformat.options import UEModelOptions
from io_scene_ueformat.importer.archive.reader import FArchiveReader
from io_scene_ueformat.importer.wire import parse_header
import io_scene_ueformat.importer.runtime_material_images as image_module
models = fixtures / "AuditGame/Content/Models"
base_path = fixtures / "AuditGame/Content/Textures/BaseColor/T_Same.png"
initial_bytes = base_path.read_bytes()
updated_bytes = (fixtures / "updates/T_Same.png").read_bytes()
report = {"label": label, "blenderVersion": bpy.app.version_string, "moduleFile": image_module.__file__, "scope": "real Blender consumer regression with actual R67 producer/writer controlled encoded PNGs; no game export/GUI", "checks": [], "findings": []}
def check(name, passed, **details):
    report["checks"].append({"name": name, "passed": bool(passed), **details})
def finding(name, reproduced, **details):
    report["findings"].append({"name": name, "reproduced": bool(reproduced), **details})
    check(name + "_fixed", not reproduced, **details)
def pixel(image):
    values = list(image.pixels[:4])
    return [round(v, 6) for v in values]
def dominant(values):
    return max(range(3), key=lambda i: values[i]) if len(values) >= 3 else None
def imported(filename):
    importer = UEFormatImport(UEModelOptions(link=True, import_fmodel_materials=True, build_fmodel_material_nodes=True))
    obj, dto = importer.import_file_with_dto(models / filename)
    mesh = obj if obj.type == "MESH" else next(c for c in obj.children if c.type == "MESH")
    material = mesh.data.materials[0]
    nodes = {n.name: n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"}
    return mesh, material, nodes
initial_mesh = None
initial_base = None
for compression in ("none", "gzip", "zstd"):
    mesh, material, nodes = imported("initial-" + compression + ".uemodel")
    image_records = []
    expected = {"FMODEL_TEXTURES.BaseColor": "BaseColor", "FMODEL_TEXTURES.Normal": "Normal", "FMODEL_TEXTURES.Orm": "Orm"}
    for node_name, folder in expected.items():
        image = nodes[node_name].image
        physical = Path(image.get("fmodel_source_path", ""))
        image_records.append({"nodeName": node_name, "imageName": image.name, "sourcePath": str(physical), "blenderFilepath": image.filepath, "pixel": pixel(image), "colorspace": image.colorspace_settings.name, "packed": bool(image.packed_file), "diskSha256": hashlib.sha256(physical.read_bytes()).hexdigest()})
        assert physical.parent.name == folder
    base = nodes["FMODEL_TEXTURES.BaseColor"].image
    normal = nodes["FMODEL_TEXTURES.Normal"].image
    orm = nodes["FMODEL_TEXTURES.Orm"].image
    principled = next(n for n in material.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled")
    rough = principled.inputs["Roughness"].links[0]
    metal = principled.inputs["Metallic"].links[0]
    ao_used = any(l.from_node.bl_idname == "ShaderNodeSeparateColor" and l.from_socket.name == "Red" for l in material.node_tree.links)
    check("distinct_same_basename_" + compression, dominant(pixel(base)) == 0 and normal != orm and normal != base and orm != base, images=image_records)
    check("orm_channels_" + compression, rough.from_socket.name == "Green" and metal.from_socket.name == "Blue", roughness=rough.from_socket.name, metallic=metal.from_socket.name, aoConnected=ao_used)
    check("normal_non_color_" + compression, normal.colorspace_settings.name == "Non-Color")
    if initial_mesh is None:
        initial_mesh, initial_base = mesh, base
        original_name = base.name
        base.name = "UserRenamed_Texture_Display"
        repeat_mesh, repeat_mat, repeat_nodes = imported("initial-" + compression + ".uemodel")
        repeated_base = repeat_nodes["FMODEL_TEXTURES.BaseColor"].image
        check("datablock_rename_does_not_change_binding", repeated_base == base and dominant(pixel(repeated_base)) == 0, originalName=original_name, renamed=base.name, sourcePath=base.get("fmodel_source_path"))

# Old material profiles must be rebuilt after the image/color behavior changed.
old_material = initial_mesh.data.materials[0]
old_profile = json.loads(old_material["fmodel_material_build_profile"])
old_profile.pop("implementation_version", None)
old_material["fmodel_material_build_profile"] = json.dumps(old_profile, separators=(",", ":"), sort_keys=True)
new_mesh, new_material, new_nodes = imported("initial-none.uemodel")
check("old_material_cache_is_migrated", new_material != old_material and json.loads(new_material["fmodel_material_build_profile"])["implementation_version"] == 2)

linear_mesh, linear_material, linear_nodes = imported("linear-basecolor-none.uemodel")
linear_image = linear_nodes["FMODEL_TEXTURES.BaseColor"].image
material_cache = json.loads(linear_material["fmodel_material_cache_identity"])
check("linear_basecolor_has_linear_color_space", linear_image.colorspace_settings.name in {"Linear Rec.709", "Linear"} and linear_image != initial_base, actualPixel=pixel(linear_image))
finding("linear_basecolor_flag_ignored", linear_image.colorspace_settings.name == "sRGB", declaredSrgb=False, actualColorspace=linear_image.colorspace_settings.name, actualPixel=pixel(linear_image), sourceImageName=linear_image.name, cacheMetadata=material_cache)

long_mesh, long_material, long_nodes = imported("long-name-none.uemodel")
long_image = long_nodes["FMODEL_TEXTURES.BaseColor"].image
long_source = Path(long_image["fmodel_source_path"])
check("long_unicode_filename_source_correct", dominant(pixel(long_image)) == 0 and long_image.name.startswith(long_source.name) and "~" not in long_image.name, filename=long_source.name, imageName=long_image.name, blenderFilepath=long_image.filepath, loadPath=image_module._blender_file_path(long_source), imageNameBytes=len(long_image.name.encode("utf-8")))

# Initial model manifest must reject a replaced file and a missing file, before
# any wrong PNG can reach Blender. Mutations affect this process's fixture copy.
before = (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.images))
base_path.write_bytes(updated_bytes)
try:
    imported("initial-none.uemodel")
except Exception as exc:
    after = (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.images))
    check("sha_mismatch_rejects_wrong_png_and_rolls_back", "mismatch" in str(exc).lower() and before == after, error=str(exc), before=before, after=after)
else:
    check("sha_mismatch_rejects_wrong_png_and_rolls_back", False)
base_path.write_bytes(initial_bytes)
missing = base_path.with_suffix(".audit-missing")
assert missing.resolve().is_relative_to(fixtures.resolve())
base_path.rename(missing)
try:
    imported("initial-none.uemodel")
except Exception as exc:
    after = (len(bpy.data.objects), len(bpy.data.materials), len(bpy.data.images))
    check("missing_png_rejects_and_rolls_back", before == after, error=str(exc), before=before, after=after)
else:
    check("missing_png_rejects_and_rolls_back", False)
finally:
    missing.rename(base_path)

# Disk is now a correct newly exported PNG for the updated model metadata.
# Force old ImBufs into memory before changing the disk content.
old_plain = {i.as_pointer() for i in bpy.data.images if i.get("fmodel_source_path") == str(base_path)}
for image in bpy.data.images:
    if image.as_pointer() in old_plain: pixel(image)
base_path.write_bytes(updated_bytes)
updated_mesh, updated_material, updated_nodes = imported("updated-none.uemodel")
updated_image = updated_nodes["FMODEL_TEXTURES.BaseColor"].image
updated_pixel = pixel(updated_image)
finding("same_path_reexport_plain_image_stale", dominant(updated_pixel) != 1, expectedDominant="green", actualPixel=updated_pixel, actualName=updated_image.name, diskSha256=hashlib.sha256(base_path.read_bytes()).hexdigest(), imagePacked=bool(updated_image.packed_file))

# Packed pre-existing images with the same path are another check_existing case.
base_path.write_bytes(initial_bytes)
for image in list(bpy.data.images):
    if image.get("fmodel_source_path") == str(base_path): bpy.data.images.remove(image, do_unlink=True)
foreign = bpy.data.images.load(image_module._blender_file_path(base_path), check_existing=False)
foreign.name = "ForeignPackedOldBaseColor"
foreign.pack()
old_foreign_pixel = pixel(foreign)
# Remove importer-owned materials so this check tests image loading, not material reuse.
for material in list(bpy.data.materials):
    if material.get("fmodel_importer_owned"): bpy.data.materials.remove(material, do_unlink=True)
base_path.write_bytes(updated_bytes)
packed_mesh, packed_material, packed_nodes = imported("updated-none.uemodel")
packed_image = packed_nodes["FMODEL_TEXTURES.BaseColor"].image
finding("same_path_reexport_packed_image_stale", dominant(pixel(packed_image)) != 1, expectedDominant="green", actualPixel=pixel(packed_image), imagePacked=bool(packed_image.packed_file), foreignUnchanged=pixel(foreign)==old_foreign_pixel, foreignPackedSha=hashlib.sha256(foreign.packed_file.data).hexdigest(), importedPackedSha=hashlib.sha256(packed_image.packed_file.data).hexdigest() if packed_image.packed_file else None, expectedNewDiskSha=hashlib.sha256(base_path.read_bytes()).hexdigest())

# Model library save: execute the two actual functions from the checked source
# without importing/registering unrelated UI/timers/settings.
source_file = v2_root / "Assets/Models/operators.py"
parsed = ast.parse(source_file.read_text(encoding="utf-8"))
functions = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in {"export_bone_mapping", "save_model_logic"}]
spec = importlib.util.spec_from_file_location("fmodel_snapshot_regression", v2_root / "core/model_snapshot.py")
snapshot_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot_module)
namespace = {"bpy": bpy, "os": os, "json": json, "write_model_snapshot": snapshot_module.write_model_snapshot}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(source_file), "exec"), namespace)
rig_data = bpy.data.armatures.new("AuditRigData")
rig = bpy.data.objects.new("AuditRig", rig_data)
bpy.context.scene.collection.objects.link(rig)
bpy.ops.object.select_all(action="DESELECT")
rig.select_set(True)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
bone = rig_data.edit_bones.new("root")
bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode="OBJECT")
holder = bpy.data.objects.new("NestedMeshHolder", None)
bpy.context.scene.collection.objects.link(holder)
holder.parent = rig
packed_mesh.parent = holder
modifier = packed_mesh.modifiers.new("AuditArmature", "ARMATURE")
modifier.object = rig
unparented_mesh = packed_mesh.copy()
unparented_mesh.data = packed_mesh.data.copy()
unparented_mesh.parent = None
unparented_mesh.name = "ModifierBoundUnparentedMesh"
bpy.context.scene.collection.objects.link(unparented_mesh)
nested_tree = bpy.data.node_groups.new("SnapshotNestedTextureGroup", "ShaderNodeTree")
nested_image_node = nested_tree.nodes.new("ShaderNodeTexImage")
nested_image_node.image = packed_image
nested_node = packed_material.node_tree.nodes.new("ShaderNodeGroup")
nested_node.node_tree = nested_tree
original_names = {m.as_pointer(): m.name for m in bpy.data.materials}
original_selection = {o.as_pointer() for o in bpy.context.selected_objects}
original_active = bpy.context.view_layer.objects.active
kinds = ("objects", "meshes", "armatures", "materials", "images", "collections", "node_groups")
before_save = {k: len(getattr(bpy.data,k)) for k in kinds}
library_dir = evidence / (label + "-model-library")
result = namespace["save_model_logic"](rig, str(library_dir), "AuditModel", False)
with bpy.data.libraries.load(str(library_dir / "AuditModel_mesh.blend"), link=False) as (data_from, data_to):
    library_objects = list(data_from.objects)
    library_meshes = list(data_from.meshes)
    library_materials = list(data_from.materials)
    library_images = list(data_from.images)
metadata = json.loads((library_dir / "AuditModel.json").read_text(encoding="utf-8"))
finding("model_library_save_drops_child_mesh_material_images", result and not library_meshes, saveReturned=result, libraryObjects=library_objects, libraryMeshes=library_meshes, libraryMaterials=library_materials, libraryImages=library_images, sidecarMeshCount=metadata["mesh_count"], originalChildren=[o.name for o in rig.children])

check("model_library_has_complete_dependencies", result and len(library_meshes) == 2 and library_materials and len(library_images) == 3 and metadata["mesh_count"] == 2 and metadata["texture_storage"] == "packed_snapshot", objects=library_objects, images=library_images)
check("model_save_preserves_scene", before_save == {k: len(getattr(bpy.data,k)) for k in kinds} and all(m.name == original_names[m.as_pointer()] for m in bpy.data.materials) and original_selection == {o.as_pointer() for o in bpy.context.selected_objects} and original_active == bpy.context.view_layer.objects.active and not packed_image.packed_file)
# Late publication failure must clean all copies and temporary files.
original_replace = snapshot_module.os.replace
snapshot_module.os.replace = lambda *args: (_ for _ in ()).throw(OSError("injected publication failure"))
try:
    namespace["save_model_logic"](rig, str(library_dir), "FailedPublication", False)
except OSError as exc:
    check("snapshot_publication_failure_cleans_dependencies", "injected" in str(exc) and before_save == {k: len(getattr(bpy.data,k)) for k in kinds} and not list(library_dir.glob(".fmodel-snapshot-*")) and not (library_dir/"FailedPublication_mesh.blend").exists() and original_selection == {o.as_pointer() for o in bpy.context.selected_objects} and original_active == bpy.context.view_layer.objects.active)
else:
    check("snapshot_publication_failure_cleans_dependencies", False)
finally:
    snapshot_module.os.replace = original_replace
# A changed disk texture must not silently become a different snapshot.
library_path = library_dir / "AuditModel_mesh.blend"
library_hash = hashlib.sha256(library_path.read_bytes()).hexdigest()
base_path.write_bytes(initial_bytes)
try:
    namespace["save_model_logic"](rig, str(library_dir), "AuditModel", False)
except ValueError as exc:
    check("changed_texture_snapshot_rejected", "changed since import" in str(exc) and hashlib.sha256(library_path.read_bytes()).hexdigest() == library_hash and before_save == {k: len(getattr(bpy.data,k)) for k in kinds})
else:
    check("changed_texture_snapshot_rejected", False)
# Link from the actual saved library after external bytes have changed.
with bpy.data.libraries.load(str(library_path), link=True) as (available, linked):
    linked.collections = available.collections
saved_collection = linked.collections[0]
saved_rig = next(o for o in saved_collection.objects if o.type == "ARMATURE")
saved_meshes = [o for o in saved_collection.objects if o.type == "MESH"]
check("saved_library_rebinds_rig", all(any(m.type == "ARMATURE" and m.object == saved_rig for m in o.modifiers) for o in saved_meshes) and any(o.parent and o.parent.parent == saved_rig for o in saved_meshes))
saved_material = saved_meshes[0].data.materials[0]
saved_image = saved_material.node_tree.nodes["FMODEL_TEXTURES.BaseColor"].image
check("saved_library_keeps_verified_pixels", saved_image.packed_file and hashlib.sha256(saved_image.packed_file.data).hexdigest() == hashlib.sha256(updated_bytes).hexdigest() and dominant(pixel(saved_image)) == 1, actualPixel=pixel(saved_image), actualName=saved_image.name)
saved_group = next(n.node_tree for n in saved_material.node_tree.nodes if n.type == "GROUP")
check("nested_group_images_are_snapshotted", saved_group != nested_tree and next(n.image for n in saved_group.nodes if n.type == "TEX_IMAGE") == saved_image and nested_image_node.image == packed_image and not packed_image.packed_file)
base_path.write_bytes(initial_bytes)
# Exercise both current owner-relative resources and ExportRoot through V2 options.
source_file = v2_root / "core/ueformat_bridge.py"
functions = [n for n in ast.parse(source_file.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef) and n.name in {"derive_fmodel_roots", "build_model_option_kwargs"}]
root_namespace = {"Path": Path}
exec(compile(ast.Module(body=functions,type_ignores=[]), str(source_file), "exec"), root_namespace)
for variant in ("initial", "export-root", "cross-mount"):
    for compression in ("none", "gzip", "zstd"):
        model_path = models / f"{variant}-{compression}.uemodel"
        kwargs = root_namespace["build_model_option_kwargs"](scale_factor=.01,bone_length=1,reorient_bones=False,target_lod=0,import_collision=False,import_morph_targets=True,import_sockets=False,import_virtual_bones=False,model_path=model_path)
        for source, options in (("default",UEModelOptions(link=True)),("v2",UEModelOptions(**kwargs))):
            result = UEFormatImport(options).import_file(model_path)
            check(f"root_{variant}_{compression}_{source}", result is not None and kwargs["fmodel_ue_export_root"] == str(fixtures))
report["allPositiveChecksPassed"] = all(c["passed"] for c in report["checks"])
report["reproducedFindings"] = [f["name"] for f in report["findings"] if f["reproduced"]]
(evidence / (label + "-blender-regression.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("MATERIAL_REGRESSION_RESULT", json.dumps({"label": label, "passed": report["allPositiveChecksPassed"], "checks": len(report["checks"]), "findings": report["reproducedFindings"]}, ensure_ascii=False))
assert report["allPositiveChecksPassed"], [c["name"] for c in report["checks"] if not c["passed"]]
