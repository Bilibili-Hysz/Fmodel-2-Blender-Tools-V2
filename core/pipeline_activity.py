"""Process-local activity state shared by pipeline code and Blender handlers.

The Blender depsgraph handler must be able to tell whether a model import pipeline
is currently mutating the scene.  Keeping this state outside a specific module
allows it to survive add-on module reloads and keeps the implementation usable in
host-Python tests without requiring Blender APIs.
"""

from contextlib import contextmanager
import builtins


_STATE_KEY = "_fmodel_tools_pipeline_activity"


def _state():
    state = builtins.__dict__.get(_STATE_KEY)
    if not isinstance(state, dict) or not isinstance(state.get("depth"), int):
        state = {"depth": 0}
        builtins.__dict__[_STATE_KEY] = state
    return state


def is_pipeline_active() -> bool:
    """Return whether one or more pipeline operations are mutating Blender."""
    return _state()["depth"] > 0


@contextmanager
def pipeline_activity():
    """Mark a pipeline operation active for the duration of a context.

    The depth counter intentionally supports nested calls (for example a full
    pipeline invoking individual stages) and is restored even when a stage raises.
    """
    state = _state()
    state["depth"] += 1
    try:
        yield
    finally:
        state["depth"] = max(0, state["depth"] - 1)
