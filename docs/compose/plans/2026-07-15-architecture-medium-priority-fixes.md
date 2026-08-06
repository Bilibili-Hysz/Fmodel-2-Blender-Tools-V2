# Architecture Medium-Priority Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve index data, UI choices, and external Blender preferences through scan/configuration failures.

**Architecture:** Fail closed before overlapping-root publication; use SQLite read-only connections for UI state; publish list changes only after query success; and replace display-name cleanup with session-owned dynamic-library identities.

**Tech Stack:** Python, SQLite URI read-only mode, Blender preferences API, pytest/fake Blender fixtures.

## Global Constraints

- Do not modify normal single-root project scanning or indexed animation import behavior.
- Existing external Blender asset-library entries must never be removed by FModel cleanup.
- Write and run a failing regression test before each production change.
- Deploy runtime files to Blender 5.1 after aggregate verification and review.

---

### Task 1: Reject Overlapping Project Roots

**Covers:** [S2, S6]

**Files:**
- Modify: `core/project_index.py:141-153, canonical root helpers`
- Test: `test_project_index.py`

- [ ] Write a ready-index regression with `[root, root / "nested"]`, assert scan fails, prior generation and rows remain unchanged.
- [ ] Run focused test and observe RED.
- [ ] Normalize roots then reject any root contained by another requested root before discovery/publish.
- [ ] Run project-index tests and observe GREEN.

### Task 2: Preserve Project Settings Reads And Choices

**Covers:** [S3, S4, S6]

**Files:**
- Modify: `Fmodel/project_assets.py:17-54,130-149`
- Test: `test_project_assets_ui.py`

- [ ] Add a missing-index test proving skeleton/count UI reads do not create a database file.
- [ ] Add a failed choice-refresh test proving existing choices and selected flags remain unchanged.
- [ ] Run focused tests and observe RED.
- [ ] Introduce one read-only connection helper using `sqlite3.connect("file:...?...", uri=True)` and defer collection replacement until after a successful path query.
- [ ] Run focused Project Settings tests and observe GREEN.

### Task 3: Track Dynamic Asset-Library Ownership

**Covers:** [S5, S6]

**Files:**
- Modify: `core/utils.py:73-132`
- Test: existing/new dynamic-library test module

- [ ] Add fake-preferences test showing a prefix-matching external library survives cleanup.
- [ ] Add test showing an entry added by `mount_dynamic_library()` is removed by cleanup, while a matching existing entry is not claimed.
- [ ] Run focused test and observe RED.
- [ ] Track canonical path/library ID only after a successful preference add; remove only tracked identities and clear the session registry.
- [ ] Run dynamic-library and aggregate tests and observe GREEN.

### Task 4: Integration Verification

**Covers:** [S6]

- [ ] Run `python -m pytest -q`, compile changed modules, and `git diff --check`.
- [ ] Request independent review of root containment, read-only URI use, choice atomicity, and library ownership.
- [ ] Deploy `core/project_index.py`, `Fmodel/project_assets.py`, and `core/utils.py` to Blender 5.1.
- [ ] Run an installed-addon probe for ready-index read/rebuild registration and safe library helper availability.
