# Baseline

## Symptom

After restarting Blender, a previously built animation asset library contains actions but its animation list is empty.

## Reproduction

The installed add-on is loaded in Blender. Its asset-library scan builds `tree_items`; DETAIL_H or DETAIL_V derives visible actions from the selected folder. A previous Blender-background reproduction confirmed that a `LIBRARY` selection produces no visible actions, while a `FOLDER` selection produces its direct action.

## Current evidence

- User reports the animation list remains empty after deploying automatic first-folder selection.
- The deployed add-on source includes that selection fix, so the remaining failure is not yet reproduced against the user's actual library output and saved scene state.
- Investigation is read-only. No production files or add-on source will be modified during evidence collection.

## Stopping criterion

Identify the first boundary at which the user's real library actions are absent or filtered, and demonstrate the cause with a targeted reversal when a reproducible code condition is available.
