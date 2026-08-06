# Tree Indent Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every Assets tree level with one fixed horizontal indentation unit in both the library and animation lists.

**Architecture:** Keep indentation centralized in `_draw_indent()` in `Assets/ui_assets.py`. Each visible depth level emits one `row.separator()` with the fixed view-specific factor, rather than using depth as a multiplier for a single separator.

**Tech Stack:** Python 3, Blender `bpy` UIList API, `unittest`, AST parsing.

## Global Constraints

- Preserve the existing tree data and folder hierarchy.
- Apply the same fixed-per-level indentation behavior to the library, animation, and folder lists.
- Do not change list row count, filtering, selection, or animation actions.
- Deploy only after Blender exits to `C:\Users\86177\AppData\Roaming\Blender Foundation\Blender\5.1\scripts\addons\FModel-2-Blender-Tools`.

---

### Task 1: Use Fixed Per-Level Tree Indentation

**Files:**
- Modify: `Assets/ui_assets.py:10-13`
- Modify: `test_assets_ui_stability.py`

**Interfaces:**
- Consumes: a Blender `UILayout` row, `depth: int`, and a fixed separator factor.
- Produces: one `row.separator(factor=factor)` call for each hierarchy level.

- [ ] **Step 1: Write the failing source-level regression test**

```python
def test_tree_indent_uses_one_fixed_separator_per_level(self):
    source = UI_ASSETS.read_text(encoding="utf-8")
    helper = source[source.index("def _draw_indent"):source.index("# ============================================================", source.index("def _draw_indent"))]
    self.assertIn("for _ in range(depth):", helper)
    self.assertIn("row.separator(factor=factor)", helper)
    self.assertNotIn("factor=depth * factor", helper)
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: failure because `_draw_indent()` currently passes `depth * factor` as one separator factor.

- [ ] **Step 3: Implement the fixed per-level helper**

```python
def _draw_indent(row, depth, factor=1.0):
    """用固定宽度的单级间隔绘制树节点缩进。"""
    for _ in range(depth):
        row.separator(factor=factor)
```

- [ ] **Step 4: Run regression tests and syntax verification**

Run: `python -m unittest test_assets_ui_stability.py`

Expected: all tests pass.

Run: `python -c "import ast; ast.parse(open('Assets/ui_assets.py', encoding='utf-8').read()); ast.parse(open('test_assets_ui_stability.py', encoding='utf-8').read()); print('tree UI syntax passed')"`

Expected: `tree UI syntax passed`.

- [ ] **Step 5: Deploy the verified UI source after Blender exits**

Run: `Copy-Item Assets\ui_assets.py <target>\Assets\ui_assets.py`

Expected: deployed file matches repository source and parses successfully.
