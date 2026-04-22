"""Central registry for 'reset to defaults' actions across DDRIG.

Each feature that supports being reset registers itself here at module
import time.  The Help > Reset... dialog enumerates registered items as
checkboxes so the user can selectively restore defaults.

Adding a new reset-able feature::

    from ddrig.core import reset_registry
    reset_registry.register(
        key="my_feature",
        label="My Feature Defaults",
        description="Restores ... to bundled defaults.",
        default_checked=True,
        action=my_module.reset_func,
    )

Registration order is preserved; dialog presents items in the order they
were registered.  Re-registering the same key replaces the previous
entry (useful for hot-reload workflows).
"""
from __future__ import annotations

from collections import OrderedDict


class ResetItem(object):
    """A single reset-able feature entry."""
    __slots__ = ("key", "label", "description", "default_checked", "action")

    def __init__(self, key, label, description, default_checked, action):
        self.key = key
        self.label = label
        self.description = description
        self.default_checked = default_checked
        self.action = action


_REGISTRY = OrderedDict()


def register(key, label, description, action, default_checked=True):
    """Register a reset-able feature.

    Args:
        key: Stable identifier (used as dict key internally).  Re-registering
            the same key replaces the previous entry.
        label: Short human-readable title shown next to the checkbox.
        description: Longer explanation shown as tooltip + grey caption
            below the checkbox.
        action: Zero-argument callable invoked when the user resets this
            item.  Exceptions propagate to the caller so the UI can
            display them.
        default_checked: Initial checkbox state.

    Raises:
        TypeError: if action is not callable.
    """
    if not callable(action):
        raise TypeError("reset_registry.register: action must be callable")
    _REGISTRY[key] = ResetItem(key, label, description, default_checked, action)


def unregister(key):
    """Remove a previously-registered item.  No-op if key is unknown."""
    _REGISTRY.pop(key, None)


def items():
    """Return list of ResetItem in registration order."""
    return list(_REGISTRY.values())


def get(key):
    """Return the ResetItem for ``key`` or None."""
    return _REGISTRY.get(key)


def keys():
    """Return list of registered keys (for introspection / tests)."""
    return list(_REGISTRY.keys())
