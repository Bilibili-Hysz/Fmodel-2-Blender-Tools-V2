"""Deprecated export namespace.

FModel Tools is now UEFormat-only. The old glTF/FBX operator modules remain in
the source tree during the migration window but are no longer imported or
registered by the add-on.
"""


def register():
    """Keep the old namespace import-safe without registering exporters."""


def unregister():
    """No UEFormat-only exporters are registered from this namespace."""
