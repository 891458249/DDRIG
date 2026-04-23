"""User-configurable submodule prefix mappings, persisted to disk.

The UE skeleton builder uses two layers of prefix knowledge:

    * :data:`ddrig.tools.ue_skeleton.builder._DEFAULT_SUBMOD_PREFIX_MAP`
      -- hard-coded defaults for spline-IK internals that ship with
      DDRIG (e.g. ``Spine_spine_N_jDef`` -> segment ``spline`` of
      module ``spine``).
    * **User map** -- this module's on-disk JSON file, editable through
      the Prefix Mapping dialog.  Per-project extensions live here so a
      user's custom submodule names can be taught to the builder
      without touching DDRIG code.

:func:`get_effective_map` returns the concatenation (defaults first,
user entries appended) for consumption by
:func:`ddrig.tools.ue_skeleton.builder.build_ue_skeleton`.
"""
from __future__ import annotations

import json
import os


# Place the config next to DDRIG's other persisted UI state, under
# ``python/ddrig/resources/ue_prefix_map.json``.  The parent of
# ``python/ddrig/tools/ue_skeleton/`` is ``python/ddrig/tools/``; the
# grandparent is ``python/ddrig/``.
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources",
    "ue_prefix_map.json",
)


def config_path():
    """Return the absolute path where the user map is persisted.
    Exposed for the UI (to show the path in a tooltip / status line)."""
    return _CONFIG_PATH


def load_user_map():
    """Return the user-added prefix entries as a list of dicts.
    Missing / malformed file -> empty list (no exception propagates)."""
    if not os.path.exists(_CONFIG_PATH):
        return []
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    cleaned = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        pfx = (entry.get("submod_prefix") or "").strip()
        mod = (entry.get("target_module") or "").strip()
        seg = (entry.get("target_segment") or "").strip()
        if pfx and mod and seg:
            cleaned.append({
                "submod_prefix": pfx,
                "target_module": mod,
                "target_segment": seg,
            })
    return cleaned


def save_user_map(entries):
    """Persist the user entries list of dicts to JSON.  Creates the
    parent directory on demand.  Entries are validated (non-empty
    fields) before writing."""
    cleaned = []
    for entry in entries or []:
        pfx = (entry.get("submod_prefix") or "").strip()
        mod = (entry.get("target_module") or "").strip()
        seg = (entry.get("target_segment") or "").strip()
        if pfx and mod and seg:
            cleaned.append({
                "submod_prefix": pfx,
                "target_module": mod,
                "target_segment": seg,
            })
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)


def get_effective_map():
    """Return ``builder._DEFAULT_SUBMOD_PREFIX_MAP`` + user entries."""
    # Lazy import -- builder touches Maya at import time, which is fine
    # inside Maya but we keep the dependency one-way.
    from ddrig.tools.ue_skeleton.builder import _DEFAULT_SUBMOD_PREFIX_MAP
    return list(_DEFAULT_SUBMOD_PREFIX_MAP) + load_user_map()
