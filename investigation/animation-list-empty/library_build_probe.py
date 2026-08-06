import bpy
import importlib.util
import os
import shutil
import sys

ADDON_ROOT = r"C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\FModel-2-Blender-Tools"
OUTPUT_DIR = r"F:\Blender Scripts Github\FModel-2-Blender-Tools\investigation\animation-list-empty\library-build-probe"
SOURCE_BLEND = r"G:\Game assets\UE AssetFiles\SilverPalace（白银之城）\SilverPalace\Assets\AnimLibrary_SK_AlfDefault_U_Body01_Skeleton.blend"
SKELETON = "SK_AlfDefault_U_Body01_Skeleton"

shutil.rmtree(OUTPUT_DIR, ignore_errors=True)
os.makedirs(OUTPUT_DIR)

spec = importlib.util.spec_from_file_location(
    "fmodel_tools_probe",
    os.path.join(ADDON_ROOT, "__init__.py"),
    submodule_search_locations=[ADDON_ROOT],
)
addon = importlib.util.module_from_spec(spec)
sys.modules["fmodel_tools_probe"] = addon
spec.loader.exec_module(addon)
addon.register()

with bpy.data.libraries.load(SOURCE_BLEND, link=False) as (data_from, data_to):
    data_to.actions = [data_from.actions[0]]
action = data_to.actions[0]


class Context:
    imported_assets = {
        "imported_actions": [{
            "action_name": action.name,
            "source_manifest_ref": f"{SKELETON}/ArtRes/Probe/Walk",
            "source_file": "",
            "staged_blend": "",
            "import_status": "success",
        }]
    }
    dictionary_manifest = {"skeletons": {}}
    config = {
        "output_dir": OUTPUT_DIR,
        "library_name": "ProbeLibrary",
        "chunk_size": 500,
        "force_rebuild": False,
        "generate_thumbnails": False,
        "source_dir": "",
    }

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def log_info(self, stage, message):
        print("INFO", stage, message)

    def log_warning(self, stage, message):
        print("WARN", stage, message)

    def log_error(self, stage, message):
        print("ERROR", stage, message)


stages = sys.modules["fmodel_tools_probe.Fmodel.Pipeline.stages"]
context = Context()
result = stages.LibraryBuildingStage().execute(context)
expected_manifest = os.path.join(OUTPUT_DIR, f"ProbeLibrary_{SKELETON}_manifest.json")
print(f"PROBE_RESULT={result!r}")
print(f"PROBE_FILES={sorted(os.listdir(OUTPUT_DIR))!r}")
print(f"PROBE_MANIFEST_EXISTS={os.path.exists(expected_manifest)!r}")

addon.unregister()
