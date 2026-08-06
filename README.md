# FModel Tools Next

FModel Tools Next is a Blender add-on for project-first UE/FModel asset workflows.

## Animation Workflow

1. Optionally set a project root in **Project Settings**. Project Settings uses Open, Scan, and Rebuild: use **Open** to inspect an existing index, **Scan** to create or refresh it, and **Rebuild** only to replace an incompatible index. The root also supplies the Project Default animation source.
2. Importing a model automatically prepares the Animation Pipeline for its returned Armature. Its Project Default source and per-skeleton library defaults are filled when a Project Root is set.
3. Explicitly open and review the Animation Pipeline, then choose **Project Default** or **Manual Directory** and adjust the output directory if needed.
4. In **Project Default**, Step 3 validates the indexed plan for every animation of the selected skeleton. In **Manual Directory**, Step 3 scans the raw animation directory. Then import and build the asset library.

The indexed animation selector is intentionally hidden from the default pipeline UI to keep normal workflows responsive. Its backend selection and publication route remain available for project-index integrations.

The derived project index is stored at `.fmodel/project-assets.sqlite` under the project root. Use **Rebuild** when the index schema is outdated or the derived data needs to be replaced.

Legacy dictionary imports remain available for existing exports, but new work should begin with the project index.

See `README_EN.md` and `README_ZH.md` for the full feature reference and export guidance.
