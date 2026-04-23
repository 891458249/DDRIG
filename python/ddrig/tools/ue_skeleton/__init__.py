"""UE-compatible export skeleton tooling.

DDRIG rigs use a flat deform-joint layout (each ``_jDef`` driven by its
own matrix/parent constraint) so matrix math inside Maya stays simple.
That layout is incompatible with UE / Unity FBX importers which require
a single-root hierarchical skeleton.

This package builds a **parallel** hierarchical skeleton of ``_jUE``
joints that mirror the ``_jDef`` animation via constraints, plus a
companion skin-influence swap utility.  The original rig is never
modified -- build, validate, then export the ``_jUE`` skeleton alongside
the skinned mesh.
"""
from __future__ import annotations

from ddrig.tools.ue_skeleton.builder import (
    UE_GROUP,
    UE_ROOT,
    UE_SUFFIX,
    JDEF_SUFFIX,
    GUIDE_SUFFIX,
    build_ue_skeleton,
    delete_ue_skeleton,
    rebuild_ue_skeleton,
    module_status_snapshot,
    module_has_rig,
    collect_module_deform_joints,
    detection_report,
)
from ddrig.tools.ue_skeleton.skin_swap import transfer_skin_to_ue

__all__ = [
    "UE_GROUP",
    "UE_ROOT",
    "UE_SUFFIX",
    "JDEF_SUFFIX",
    "GUIDE_SUFFIX",
    "build_ue_skeleton",
    "delete_ue_skeleton",
    "rebuild_ue_skeleton",
    "module_status_snapshot",
    "module_has_rig",
    "collect_module_deform_joints",
    "detection_report",
    "transfer_skin_to_ue",
]
