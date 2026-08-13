"""TDD contracts for Blender 5 action/slot-safe pipeline behavior."""

import importlib.util
import os
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location(
    "action_safety", os.path.join(ROOT, "core", "action_safety.py")
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Action(dict):
    def __init__(self, name, paths, library=None, slots=None):
        super().__init__()
        self.name = name
        self.fcurves = [types.SimpleNamespace(data_path=path) for path in paths]
        self.library = library
        if slots is not None:
            self.slots = slots

    def copy(self):
        return Action(self.name, [curve.data_path for curve in self.fcurves], slots=getattr(self, "slots", None))


class Blender5ActionSafetyTests(unittest.TestCase):
    def test_skeletal_action_is_primary_and_shape_key_action_is_auxiliary(self):
        skeletal = Action("Walk", ["pose.bones[\"root\"].rotation_quaternion"])
        shape = Action("Walk_ShapeKeys", ["key_blocks[\"Smile\"].value"])

        primary, auxiliary = MODULE.select_primary_action([shape, skeletal])

        self.assertIs(primary, skeletal)
        self.assertEqual(auxiliary, [shape])

    def test_blender5_channelbag_fcurves_detect_skeletal_action(self):
        channelbag = types.SimpleNamespace(fcurves=[
            types.SimpleNamespace(data_path='pose.bones["root"].location')
        ])
        strip = types.SimpleNamespace(channelbags=[channelbag])
        action = Action("Walk", [], slots=[object()])
        action.layers = [types.SimpleNamespace(strips=[strip])]

        self.assertTrue(MODULE.is_skeletal_action(action))

    def test_unrelated_preexisting_actions_are_not_cleanup_candidates(self):
        owned = Action("Imported", [])
        caller = Action("CallerAction", [])
        self.assertEqual(MODULE.owned_actions([caller, owned], [caller]), [owned])

    def test_recreated_wrapper_for_preexisting_action_is_not_owned(self):
        original_wrapper = Action("External", [])
        recreated_wrapper = Action("External", [])
        original_wrapper.as_pointer = lambda: 12345
        recreated_wrapper.as_pointer = lambda: 12345

        self.assertEqual(
            MODULE.owned_actions([recreated_wrapper], [original_wrapper]),
            [],
        )

    def test_remove_owned_action_removes_new_action_only(self):
        preexisting = Action("UserAction", [])
        loaded = Action("LoadedAction", [])
        actions = [preexisting, loaded]

        self.assertTrue(MODULE.remove_owned_action(actions, loaded, [preexisting]))
        self.assertEqual(actions, [preexisting])
        self.assertFalse(MODULE.remove_owned_action(actions, preexisting, [preexisting]))
        self.assertEqual(actions, [preexisting])

    def test_remove_owned_action_returns_false_when_collection_remove_raises(self):
        loaded = Action("LoadedAction", [])

        class Actions(list):
            def remove(self, action):
                raise RuntimeError("datablock is still in use")

        actions = Actions([loaded])
        self.assertFalse(MODULE.remove_owned_action(actions, loaded, []))
        self.assertEqual(actions, [loaded])

    def test_datablock_identity_safely_falls_back_for_test_doubles(self):
        missing_pointer = Action("Missing", [])
        failing_pointer = Action("Failing", [])
        zero_pointer = Action("Zero", [])
        failing_pointer.as_pointer = lambda: (_ for _ in ()).throw(RuntimeError("invalid wrapper"))
        zero_pointer.as_pointer = lambda: 0

        identities = {
            MODULE.datablock_identity(missing_pointer),
            MODULE.datablock_identity(failing_pointer),
            MODULE.datablock_identity(zero_pointer),
        }

        self.assertEqual(len(identities), 3)

    def test_blender5_slot_actions_are_explicitly_unsupported_for_legacy_cleanup(self):
        action = Action("Walk", [], slots=[object()])
        self.assertFalse(MODULE.supports_legacy_cleanup(action))

    def test_localize_linked_action_returns_copy_without_mutating_source(self):
        linked = Action("Walk", [], library=types.SimpleNamespace(filepath="library.blend"))
        linked.copy = lambda: Action("Walk", [], library=None)
        copied = MODULE.localize_action(linked)
        self.assertIsNot(copied, linked)
        self.assertIsNone(copied.library)
        self.assertIsNotNone(linked.library)

    def test_assign_action_selects_matching_blender5_slot(self):
        matching = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Hero")
        other = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Enemy")
        action = Action("Walk", [], slots=[other, matching])
        data = types.SimpleNamespace(action=None, action_slot=None)
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE", animation_data=data)

        MODULE.assign_action(armature, action)

        self.assertIs(data.action, action)
        self.assertIs(data.action_slot, matching)

    def test_assign_action_selects_matching_real_blender51_slot(self):
        matching = types.SimpleNamespace(
            target_id_type="OBJECT", name_display="Hero", identifier="OBHero"
        )
        other = types.SimpleNamespace(
            target_id_type="OBJECT", name_display="Enemy", identifier="OBEnemy"
        )
        action = Action("Walk", [], slots=[other, matching])
        data = types.SimpleNamespace(action=None, action_slot=None)
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE", animation_data=data)

        MODULE.assign_action(armature, action)

        self.assertIs(data.action_slot, matching)

    def test_assign_action_restores_action_and_slot_when_slot_setter_fails(self):
        old_action = Action("Old", [])

        class AnimationData:
            def __init__(self):
                self._action = old_action
                self._slot = "old-slot"

            @property
            def action(self):
                return self._action

            @action.setter
            def action(self, value):
                self._action = value

            @property
            def action_slot(self):
                return self._slot

            @action_slot.setter
            def action_slot(self, value):
                if value != "old-slot":
                    raise RuntimeError("slot setter failed")
                self._slot = value

        slot = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Hero")
        data = AnimationData()
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE", animation_data=data)
        with self.assertRaisesRegex(RuntimeError, "slot setter failed"):
            MODULE.assign_action(armature, Action("New", [], slots=[slot]))
        self.assertIs(data.action, old_action)
        self.assertEqual(data.action_slot, "old-slot")

    def test_assign_action_restores_absent_animation_data_when_setter_fails(self):
        slot = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Hero")

        class AnimationData:
            def __init__(self):
                self.action = None

            @property
            def action_slot(self):
                return None

            @action_slot.setter
            def action_slot(self, value):
                raise RuntimeError("slot setter failed")

        class Armature:
            name = "Hero"
            type = "ARMATURE"
            animation_data = None

            def animation_data_create(self):
                self.animation_data = AnimationData()
                return self.animation_data

        armature = Armature()
        action = Action("New", [], slots=[slot])
        with self.assertRaisesRegex(RuntimeError, "slot setter failed"):
            MODULE.assign_action(armature, action)
        self.assertIsNone(armature.animation_data)

    def test_assign_action_accepts_compatible_slot_after_target_rename(self):
        slot = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="OldHero")
        action = Action("Walk", [], slots=[slot])
        data = types.SimpleNamespace(action=None, action_slot=None)
        armature = types.SimpleNamespace(name="RenamedHero", type="ARMATURE", animation_data=data)

        MODULE.assign_action(armature, action)

        self.assertIs(data.action_slot, slot)

    def test_assign_action_validates_slot_before_creating_animation_data(self):
        incompatible = types.SimpleNamespace(target_id_type="KEY", target_id_name="Hero")
        action = Action("Walk", [], slots=[incompatible])

        class Armature:
            name = "Hero"
            type = "ARMATURE"
            animation_data = None
            create_calls = 0

            def animation_data_create(self):
                self.create_calls += 1
                self.animation_data = types.SimpleNamespace(action=None, action_slot=None)
                return self.animation_data

        armature = Armature()

        with self.assertRaisesRegex(RuntimeError, "has no slot"):
            MODULE.assign_action(armature, action)

        self.assertEqual(armature.create_calls, 0)
        self.assertIsNone(armature.animation_data)

    def test_nla_mute_state_round_trips(self):
        tracks = [
            types.SimpleNamespace(mute=False),
            types.SimpleNamespace(mute=True),
        ]
        data = types.SimpleNamespace(nla_tracks=tracks)

        state = MODULE.disable_nla(data)

        self.assertEqual([track.mute for track in tracks], [True, True])
        MODULE.restore_nla_mutes(state)
        self.assertEqual([track.mute for track in tracks], [False, True])

    def test_replace_does_not_clear_nla_when_assignment_fails(self):
        tracks = [object(), object()]

        class AnimationData:
            action_slot = None

            @property
            def action(self):
                return None

            @action.setter
            def action(self, value):
                raise RuntimeError("incompatible slot")

        data = AnimationData()
        data.nla_tracks = tracks
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE", animation_data=data)

        with self.assertRaisesRegex(RuntimeError, "cannot safely replace"):
            MODULE.replace_action(armature, Action("Walk", []))

        self.assertEqual(len(tracks), 2)

    def test_replace_rejects_before_mutating_existing_nla_tracks(self):
        class Strips(list):
            def new(self, name, frame_start, action):
                strip = types.SimpleNamespace(
                    name=name, frame_start=frame_start, action=action, mute=False,
                )
                self.append(strip)
                return strip

        class Tracks(list):
            def __init__(self, values):
                super().__init__(values)
                self.failed = False

            def remove(self, track):
                if track.name == "Second" and not self.failed:
                    self.failed = True
                    raise RuntimeError("track removal failed")
                super().remove(track)

            def new(self):
                track = types.SimpleNamespace(name="", mute=False, strips=Strips())
                self.append(track)
                return track

        old_action = Action("Old", [])
        first_strip = types.SimpleNamespace(name="A", frame_start=1.0, action=old_action, mute=True)
        second_strip = types.SimpleNamespace(name="B", frame_start=12.0, action=old_action, mute=False)
        tracks = Tracks([
            types.SimpleNamespace(name="First", mute=True, strips=Strips([first_strip])),
            types.SimpleNamespace(name="Second", mute=False, strips=Strips([second_strip])),
        ])
        data = types.SimpleNamespace(action=old_action, action_slot=None, nla_tracks=tracks)
        armature = types.SimpleNamespace(name="Hero", type="ARMATURE", animation_data=data)

        with self.assertRaisesRegex(RuntimeError, "cannot safely replace"):
            MODULE.replace_action(armature, Action("New", []))

        self.assertIs(data.action, old_action)
        self.assertEqual([track.name for track in tracks], ["First", "Second"])
        self.assertEqual([track.mute for track in tracks], [True, False])
        self.assertEqual([strip.name for track in tracks for strip in track.strips], ["A", "B"])
        self.assertEqual([strip.mute for track in tracks for strip in track.strips], [True, False])

    def test_overwrite_keeps_existing_strips_when_replacement_creation_fails(self):
        old_action = Action("Old", [])

        class Strips(list):
            def new(self, name, frame_start, action):
                raise RuntimeError("strip creation failed")

        first = types.SimpleNamespace(name="A", frame_start=1.0, action=old_action, mute=True)
        second = types.SimpleNamespace(name="B", frame_start=12.0, action=old_action, mute=False)
        track = types.SimpleNamespace(strips=Strips([first, second]))
        with self.assertRaisesRegex(RuntimeError, "cannot safely replace"):
            MODULE.overwrite_nla_strip(track, "New", 1, Action("New", []), [first])
        self.assertEqual(list(track.strips), [first, second])

    def test_overwrite_rejects_before_removing_existing_action_strips(self):
        old_action = Action("Old", [])

        class Strips(list):
            def __init__(self, values):
                super().__init__(values)
                self.removals = 0

            def new(self, name, frame_start, action):
                strip = types.SimpleNamespace(name=name, frame_start=frame_start, action=action, mute=False)
                self.append(strip)
                return strip

            def remove(self, strip):
                self.removals += 1
                if self.removals == 2:
                    raise RuntimeError("strip removal failed")
                super().remove(strip)

        first = types.SimpleNamespace(name="A", frame_start=1.0, action=old_action, mute=True)
        second = types.SimpleNamespace(name="B", frame_start=12.0, action=old_action, mute=False)
        strips = Strips([first, second])
        track = types.SimpleNamespace(strips=strips)
        with self.assertRaisesRegex(RuntimeError, "cannot safely replace"):
            MODULE.overwrite_nla_strip(track, "New", 1, Action("New", []), [first, second])
        self.assertEqual([strip.name for strip in strips], ["A", "B"])
        self.assertEqual([strip.mute for strip in strips], [True, False])

    def test_overwrite_preserves_non_action_strip_when_creation_fails(self):
        class TransitionStrip:
            def __init__(self, name):
                self.name = name
                self.type = "TRANSITION"
                self.frame_start = 1.0
                self.frame_end = 10.0

        class Strips(list):
            def new(self, name, frame_start, action):
                raise RuntimeError("strip creation failed")

        transition = TransitionStrip("Blend")
        track = types.SimpleNamespace(strips=Strips([transition]))

        with self.assertRaisesRegex(RuntimeError, "cannot safely replace"):
            MODULE.overwrite_nla_strip(
                track, "New", 1, Action("New", []), [transition], object()
            )

        self.assertEqual(track.strips[0].name, "Blend")

    def test_nla_strip_uses_resolved_blender5_slot(self):
        slot = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Hero")
        action = Action("Walk", [], slots=[slot])
        target = types.SimpleNamespace(name="Hero", type="ARMATURE")

        class Strips(list):
            def new(self, name, frame_start, action):
                strip = types.SimpleNamespace(
                    name=name, frame_start=frame_start, action=action, action_slot=None,
                )
                self.append(strip)
                return strip

        strip = MODULE.append_nla_strip(types.SimpleNamespace(strips=Strips()), action, target, 1)

        self.assertIs(strip.action_slot, slot)

    def test_nla_strip_creation_rolls_back_when_slot_assignment_fails(self):
        slot = types.SimpleNamespace(target_id_type="OBJECT", target_id_name="Hero")
        action = Action("Walk", [], slots=[slot])
        target = types.SimpleNamespace(name="Hero", type="ARMATURE")

        class Strip:
            name = "Walk"
            frame_start = 1.0

            @property
            def action_slot(self):
                return None

            @action_slot.setter
            def action_slot(self, value):
                raise RuntimeError("slot setter failed")

        class Strips(list):
            def new(self, name, frame_start, action):
                strip = Strip()
                self.append(strip)
                return strip

        strips = Strips()
        with self.assertRaisesRegex(RuntimeError, "slot setter failed"):
            MODULE.append_nla_strip(types.SimpleNamespace(strips=strips), action, target, 1)

        self.assertEqual(strips, [])

    def test_nla_track_is_not_created_for_incompatible_action_slot(self):
        incompatible = types.SimpleNamespace(target_id_type="KEY", target_id_name="Hero")
        action = Action("Walk", [], slots=[incompatible])
        target = types.SimpleNamespace(name="Hero", type="ARMATURE")

        class Tracks(list):
            def __init__(self):
                super().__init__()
                self.create_calls = 0

            def new(self):
                self.create_calls += 1
                track = types.SimpleNamespace(name="", strips=[])
                self.append(track)
                return track

        tracks = Tracks()
        data = types.SimpleNamespace(nla_tracks=tracks)

        with self.assertRaisesRegex(RuntimeError, "has no slot"):
            MODULE.get_or_create_nla_track(data, action, target)

        self.assertEqual(tracks.create_calls, 0)
        self.assertEqual(tracks, [])

    def test_nla_track_validation_failure_preserves_existing_tracks(self):
        incompatible = types.SimpleNamespace(target_id_type="KEY", target_id_name="Hero")
        action = Action("Walk", [], slots=[incompatible])
        target = types.SimpleNamespace(name="Hero", type="ARMATURE")
        existing = types.SimpleNamespace(name="Existing", strips=[object()])
        tracks = [existing]
        data = types.SimpleNamespace(nla_tracks=tracks)

        with self.assertRaisesRegex(RuntimeError, "has no slot"):
            MODULE.get_or_create_nla_track(data, action, target)

        self.assertEqual(tracks, [existing])
        self.assertEqual(existing.strips, [existing.strips[0]])

    def test_failed_append_removes_only_newly_created_empty_track(self):
        action = Action("Walk", [])
        target = types.SimpleNamespace(name="Hero", type="ARMATURE")
        existing = types.SimpleNamespace(name="Existing", strips=[object()])

        class Strips(list):
            def new(self, name, frame_start, action):
                raise RuntimeError("strip creation failed")

        class Tracks(list):
            def new(self):
                track = types.SimpleNamespace(name="", strips=Strips())
                self.append(track)
                return track

            def remove(self, track):
                super().remove(track)

        tracks = Tracks([existing])
        data = types.SimpleNamespace(nla_tracks=tracks)

        # Force creation despite the preexisting track so rollback ownership is explicit.
        with self.assertRaisesRegex(RuntimeError, "strip creation failed"):
            MODULE.append_nla_strip_to_new_track(data, action, target, 1)

        self.assertEqual(tracks, [existing])
        self.assertEqual(len(existing.strips), 1)

    def test_pipeline_owned_copy_never_returns_external_source(self):
        source = Action("Walk", [], library=types.SimpleNamespace(filepath="library.blend"))

        copied = MODULE.copy_action_for_pipeline(source)

        self.assertIsNot(copied, source)
        self.assertIsNone(copied.library)

    def test_restore_absent_animation_data_does_not_create_it(self):
        class Armature:
            type = "ARMATURE"
            animation_data = None

            def animation_data_create(self):
                self.animation_data = types.SimpleNamespace(action=None, action_slot=None)
                return self.animation_data

        armature = Armature()
        MODULE.restore_animation_state(armature, MODULE.animation_state(armature))
        self.assertIsNone(armature.animation_data)


if __name__ == "__main__":
    unittest.main()
