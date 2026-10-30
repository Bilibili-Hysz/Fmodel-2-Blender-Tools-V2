"""Small Blender-version-safe helpers for pipeline action ownership."""

import copy


ABSENT_ANIMATION_STATE = object()


def datablock_identity(datablock):
    """Return stable Blender identity, with a safe fake-object fallback."""
    as_pointer = getattr(datablock, "as_pointer", None)
    if callable(as_pointer):
        try:
            pointer = as_pointer()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pointer = None
        if pointer:
            return ("blender", pointer)
    return ("python", id(datablock))


def _data_paths(action):
    curves = list(getattr(action, "fcurves", ()) or ())
    for layer in getattr(action, "layers", ()) or ():
        for strip in getattr(layer, "strips", ()) or ():
            channelbags = getattr(strip, "channelbags", None)
            if channelbags is None:
                channelbag = getattr(strip, "channelbag", None)
                channelbags = (channelbag,) if channelbag is not None else ()
            for channelbag in channelbags or ():
                curves.extend(getattr(channelbag, "fcurves", ()) or ())
    return [getattr(curve, "data_path", "") for curve in curves]


def is_skeletal_action(action):
    paths = _data_paths(action)
    return any("pose.bones[" in path for path in paths)


def select_primary_action(actions):
    actions = list(actions)
    skeletal = [action for action in actions if is_skeletal_action(action)]
    if len(skeletal) == 1:
        primary = skeletal[0]
    elif len(actions) == 1:
        primary = actions[0]
    else:
        primary = None
    return primary, [action for action in actions if action is not primary]


def owned_actions(current_actions, baseline_actions):
    baseline_ids = {datablock_identity(action) for action in baseline_actions}
    return [action for action in current_actions if datablock_identity(action) not in baseline_ids]


def remove_owned_action(actions, action, baseline_actions):
    """Remove an action only when this operation introduced its datablock."""
    baseline_ids = {datablock_identity(item) for item in baseline_actions}
    if action is None or datablock_identity(action) in baseline_ids:
        return False
    current_ids = {datablock_identity(item) for item in actions}
    if datablock_identity(action) not in current_ids:
        return False
    try:
        actions.remove(action)
    except Exception:
        return False
    return True


def supports_legacy_cleanup(action):
    """Legacy fcurve cleanup is unsafe when Blender 5 slots are present."""
    slots = getattr(action, "slots", None)
    return not slots


def localize_action(action):
    """Return a local copy before metadata mutation; never mutate linked data."""
    if getattr(action, "library", None) is None:
        return action
    copy_action = getattr(action, "copy", None)
    if callable(copy_action):
        local = copy_action()
        if local is not None:
            return local
    make_local = getattr(action, "make_local", None)
    if callable(make_local):
        local = make_local()
        return local or action
    local = copy.copy(action)
    local.library = None
    return local


def copy_action_for_pipeline(action):
    """Create a pipeline-owned local action without mutating its source."""
    copy_action = getattr(action, "copy", None)
    local = copy_action() if callable(copy_action) else copy.copy(action)
    if local is None or local is action:
        local = copy.copy(action)
    if getattr(local, "library", None) is not None:
        make_local = getattr(local, "make_local", None)
        if callable(make_local):
            local = make_local() or local
        else:
            local.library = None
    return local


def animation_state(obj):
    data = getattr(obj, "animation_data", None)
    if data is None:
        return ABSENT_ANIMATION_STATE
    return getattr(data, "action", None), getattr(data, "action_slot", None)


def restore_animation_state(obj, state):
    if state is ABSENT_ANIMATION_STATE:
        clear = getattr(obj, "animation_data_clear", None)
        if callable(clear) and getattr(obj, "animation_data", None) is not None:
            clear()
        elif getattr(obj, "animation_data", None) is not None:
            try:
                obj.animation_data = None
            except (AttributeError, RuntimeError, TypeError):
                pass
        return
    data = getattr(obj, "animation_data", None)
    if data is None:
        create = getattr(obj, "animation_data_create", None)
        if not callable(create):
            return
        data = create()
    data.action = state[0]
    if hasattr(data, "action_slot"):
        data.action_slot = state[1]


def resolve_action_slot(action, obj):
    """Return the unique Blender 5 action slot compatible with an armature."""
    compatible = [
        slot for slot in getattr(action, "slots", ()) or ()
        if getattr(slot, "target_id_type", None) == "OBJECT"
    ]
    obj_name = getattr(obj, "name", None)
    exact = []
    for slot in compatible:
        identifier = getattr(slot, "identifier", None)
        slot_names = {
            getattr(slot, "name_display", None),
            getattr(slot, "target_id_name", None),
            identifier,
            identifier[2:] if isinstance(identifier, str) and len(identifier) > 2 else None,
        }
        if obj_name is not None and obj_name in slot_names:
            exact.append(slot)
    if len(exact) == 1:
        return exact[0]
    if len(compatible) == 1:
        return compatible[0]
    return None


def _require_action_slot(action, target):
    slots = getattr(action, "slots", None)
    if not slots:
        return None
    if target is None or getattr(target, "type", None) != "ARMATURE":
        raise RuntimeError("Blender 5 action strips require an armature target")
    slot = resolve_action_slot(action, target)
    if slot is None:
        raise RuntimeError(f"action has no slot for armature {target.name}")
    return slot


def assign_action(obj, action):
    """Assign an action and slot as one rollback-safe mutation."""
    if getattr(obj, "type", None) != "ARMATURE":
        raise ValueError("target object must be an armature")
    slot = _require_action_slot(action, obj)
    old_state = animation_state(obj)
    data = getattr(obj, "animation_data", None)
    if data is None:
        create = getattr(obj, "animation_data_create", None)
        if not callable(create):
            raise RuntimeError("target has no animation data")
        data = create()
    try:
        data.action = action
        if hasattr(data, "action_slot"):
            data.action_slot = slot
    except Exception:
        restore_animation_state(obj, old_state)
        raise
    return data


def append_nla_strip(track, action, target, frame_start, name=None):
    """Create a slot-compatible action strip or leave the track unchanged."""
    slot = _require_action_slot(action, target)
    strips = track.strips
    strip = None
    try:
        strip = strips.new(name or getattr(action, "name", "Action"), frame_start, action)
        if getattr(action, "slots", None):
            if not hasattr(strip, "action_slot"):
                raise RuntimeError("created NLA strip has no action slot")
            strip.action_slot = slot
        return strip
    except Exception:
        if strip is not None:
            try:
                strips.remove(strip)
            except Exception:
                pass
        raise


def append_nla_strip_to_new_track(data, action, target, frame_start, name=None):
    """Create a track and strip as one rollback-safe mutation."""
    _require_action_slot(action, target)
    tracks = data.nla_tracks
    track = tracks.new()
    try:
        track.name = "NLA_Track"
        strip = append_nla_strip(track, action, target, frame_start, name)
        return track, strip
    except Exception:
        if not track.strips:
            try:
                tracks.remove(track)
            except Exception:
                pass
        raise


def get_or_create_nla_track(data, action, target):
    """Return the last NLA track, validating action compatibility first."""
    _require_action_slot(action, target)
    tracks = data.nla_tracks
    if tracks:
        return tracks[-1]
    track = tracks.new()
    track.name = "NLA_Track"
    return track


def overwrite_nla_strip(track, name, frame_start, action, overlaps, target=None):
    """Append only when replacement cannot destroy existing NLA strip types.

    Blender has no general API to restore a removed transition, meta, sound, or
    action strip exactly. Reject any destructive overwrite before creating or
    removing a strip instead of fabricating an incomplete rollback.
    """
    if tuple(overlaps):
        raise RuntimeError("cannot safely replace existing NLA strips")
    return append_nla_strip(track, action, target, frame_start, name)


def disable_nla(data):
    """Mute NLA tracks temporarily and return their exact prior state."""
    state = []
    try:
        for track in getattr(data, "nla_tracks", ()) or ():
            state.append((track, track.mute))
            track.mute = True
    except Exception:
        restore_nla_mutes(state)
        raise
    return state


def restore_nla_mutes(state):
    for track, mute in state:
        track.mute = mute


def replace_action(obj, action):
    """Replace the active action without destructively rebuilding NLA tracks."""
    return assign_action(obj, action)
