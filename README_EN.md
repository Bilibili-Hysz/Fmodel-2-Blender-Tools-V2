# FModel 2 Blender Tools

A full-stack asset pipeline addon for **UE/FModel** workflows in Blender 5.0.

---

## Modules

### 1. Asset Pipeline

A 4-step guided wizard to import animations, build asset libraries, and generate thumbnails in one click.

| Step | Function |
|------|----------|
| Flow | Select FModel flow |
| Project | Set source/output directories, skeleton filter (eyedropper pick) |
| Scan | Scan `.ueanim` dictionary — auto-discover skeletons & animations |
| Import/Package | Set import params → import animations → build library → generate thumbnails |

Thumbnails:
- OpenGL viewport snapshot (fast) and Render engine (high quality) modes
- Isolate skeleton children (hide other scene objects)
- Deterministic hash naming — rebaking overwrites old thumbnails
- "Rebake Thumbnails" button for updating without re-importing

Animation Pipeline is now Folder-only: Step 2 selects an animation folder, output directory, and library name; Step 3 directly scans .ueanim / .uepose files from that folder. Indexed Project, Project Root, the SQLite animation index, and background index-maintenance controls are removed from the active UI and execution path. Legacy state fields remain only so older .blend files can be opened safely.

---

### 2. Material Tools

Auto-parse UE JSON material data to build PBR/NPR shader node graphs.

**Phase 1 — Asset Discovery**  
Parse FModel-exported model JSON and material instance JSON to extract texture paths and scalar/vector params.

**Phase 2 — Channel Resolution**  
UE param name → standard channel mapping, via:
- GameProfile exact match
- CUE4Parse material param dictionary (200+ entries)
- Built-in keyword matching
- ORM auto-detect (`_ORM`/`_ARM`/`_MRAO` suffix → splits to AO/Roughness/Metallic)

**Phase 3 — Node Building**  
Auto-build Blender material node graphs: Texture → Tool Chain → Shader Group, with NormalMap / Invert / ORM Decode support.

---

### 3. Shader Manager

- **Template Import**: One-click import of PBR Standard, NPR Toon, and more from `Shaders/shaders.blend`
- **Custom Shaders**: Save node groups as reusable templates
- **Interface Mapping**: Auto-detect group sockets, map to standard channels (Base Color, Normal, Roughness, etc.)
- **Preset System**: Export/load presets as JSON, usable from the Material panel

---

### 4. Asset Library

**Animation Assets**  
- Tree / list / card three view modes
- Skeleton filter + name search
- Thumbnail preview + detail panel (frame count, duration, skeleton, source)
- One-click preview/apply to armature
- NLA deployment (replace/append/overwrite)

**Model Assets**  
- Save character models to library (with preview)
- Card grid browser with inline thumbnails
- One-click import to scene

---

### 5. Extension Tools

| Tool | Functions |
|------|-----------|
| Mesh | Clean empty morphs / separate by material |
| Skeleton | Sort bones / batch rename / UE standard naming / bone mapping / pose mirror |
| Animation | Clean action keyframes / batch clean |

---

### 6. GameProfile Manager

Edit GameProfile presets in the Shader Editor:
- Create / delete / edit profiles
- UE param name → standard channel mapping
- Auto-saves to `Shaders/GameProfiles/`

---

## FModel Export Guide

### Native UEFormat (recommended)
Current canonical UEFormat resources carry the model/animation skeleton identity
needed by V2. For `.uemodel`, `.ueanim`, and `.uepose`:

```
✅ Native binary resource
✅ Preserve the original UE directory layout
⭕ Same-name JSON: not required for native skeleton discovery
⭕ Dictionary manifest JSON: not required by Folder scanning
```

V2 reads the binary skeleton path, optional GUID, bone-layout hash, and provenance
fingerprint first. The UEFormat-only line no longer exposes the JSON fallback in its
user-facing import path; the fallback remains only as a temporary migration component.

### Materials and textures
The canonical UEFormat importer handles native model materials/textures when the
current FModel capability bridge is available. The former `Universal_tools/material` JSON resolver remains registered as a
manual migration and repair tool for old projects, but it is not part of the
UEFormat-only native import path.
Keep external textures (`.png`, `.tga`, `.dds`, etc.) and preserve their
`Content/Textures/...` relative layout whenever the selected importer needs them.

### JSON that remains intentional
JSON is not removed globally. It remains an explicit compatibility or state format for:

- V2 checkpoints, reports, library manifests, shader presets, and other internal state;
- read-only migration inputs for old projects during the transition window.

### Recommended settings
- Export native canonical UEFormat; do not add JSON for the normal UEFormat-only workflow
- Texture format: PNG (preserve alpha for WebP thumbnails)
- Directory structure: **Preserve the original UE hierarchy**

---

## User-visible reports

The Toolbox now keeps a persistent **Last Operation** section. It reports the
latest UEFormat import status, source, created Datablocks and actionable
failures, and provides **Copy Report** and **Open Source** actions. The copied
report also includes the active runtime path and verified Vendor tree digest.

Choose one Blender installation mode: use the V2 bundled runtime, or install the
standalone `io_scene_ueformat` addon. Do not enable both in one session.

## Bundled UEFormat Vendor

The release includes the FModel-aware `io_scene_ueformat` **1.2.0** vendor. It
imports `.uemodel`, `.ueanim`, and `.uepose` and carries the FModel
material/texture and strict skeleton surfaces used by V2.

V2 registers this dependency in **headless mode**. The operators, file handlers,
settings, and import implementation are active, but the canonical UEFormat
panels and **File > Import** menu entries are not created, so users see only
the V2 FModel UI. A standalone UEFormat add-on still uses normal registration
and keeps its canonical UI.

Windows x64 `zstandard` wheels for CPython 3.11/3.12/3.13 are bundled and
selected according to the running Blender Python.

## Installation

Place `FModel_Tools/` into the Blender addons directory:

```
%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\
```

Enable **FModel Tools** in Blender Preferences.

---

## Requirements

- Blender 5.0+
- Bundled runtime dependency: `io_scene_ueformat` 1.2.0 (headless registration)
- PSK/PSA/glTF/FBX support is disabled in the UEFormat-only line

---

## Internationalization

- Default: Chinese interface
- Switch Blender language to English → all panels, buttons, descriptions auto-translate (309 entries, `README_ZH.md` for the Chinese version)
