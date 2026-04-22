"""Build a parallel UE-compatible skeleton -- guide-first, rig-optional.

Overview
--------

DDRIG's deform joints (`_jDef`) form a flat layout optimised for Maya's
matrix math.  UE / Unity require a single-root hierarchical skeleton,
so this tool generates a PARALLEL skeleton of `_jUE` joints with proper
parent-child topology, driven (where possible) by parent+scale
constraints from the original `_jDef`.

Two orthogonal axes shape the output:

    source       = "guide" | "rig" | "auto"
    granularity  = "main"  | "full"

source
    guide  Topology and world positions come from guide joints (`_jInit`)
           only.  No animation drivers.  Works even if NO module has
           been built yet.

    rig    Each module must have an `_defJoints_grp` populated with
           `_jDef` joints.  Positions + parent/scale constraints come
           from the `_jDef`.  Unbuilt modules are skipped (with warning).

    auto   Per-module adaptive: use rig when available, fall back to
           guide for unbuilt modules.  Recommended default; never
           skips a module.

granularity
    main   One `_jUE` per guide joint (e.g. arm = 4: collar / shoulder
           / elbow / hand).  Works for every source.

    full   In addition to the main joints, insert every twist / ribbon
           `_jDef` that lies between two consecutive guide joints on
           the segment axis (e.g. arm = 13: collar / up_0..up_4 /
           elbow / low_0..low_4 / hand).  Requires rig data; for
           guide-sourced modules silently degrades to main.

Key invariants
--------------
* Only the `ue_skeleton_grp` subtree is touched.  Source `_jDef`,
  `defJoints_grp`, skin clusters and guide joints are never modified.
* Each `_jUE` carries a `sourceJDef` string attribute pointing to its
  driving `_jDef` (empty when static).  skin_swap uses this map to
  transfer weights without guessing names.
* Repeat-safe: `rebuild_ue_skeleton` is `delete_ue_skeleton + build_ue_skeleton`.
* Unbuilt modules do NOT break the cross-module chain -- their
  jUEs are created from guide positions and the chain remains intact.
"""
from __future__ import annotations

from maya import cmds
import maya.api.OpenMaya as om

from ddrig.core import filelog

log = filelog.Filelog(logname=__name__, filename="ddrig_log")


UE_SUFFIX = "_jUE"
JDEF_SUFFIX = "_jDef"
GUIDE_SUFFIX = "_jInit"
UE_GROUP = "ue_skeleton_grp"
UE_ROOT = "root_jUE"
_SOURCE_ATTR = "sourceJDef"

_VALID_SOURCES = ("guide", "rig", "auto")
_VALID_GRANULARITIES = ("main", "full")

# Placeholder used in the intra-module sequence for "parent is in another
# module" -- resolved in the cross-module pass after every module's
# intra-chain is built.
_CROSS_MODULE_PLACEHOLDER = "@cross_module_parent"


# ---------------------------------------------------------------------------
# IK-helper guide blacklist
# ---------------------------------------------------------------------------
# Some DDRIG modules declare guide joints that are NOT part of the deform
# chain -- they are IK pivots / control helpers used only by the rig's
# control system (e.g. leg's BankIN / BankOUT / HeelPV / ToePV drive the
# foot-roll IK but never participate in skinning).
#
# Without filtering these out, build_ue_skeleton duplicates them into the
# UE skeleton, where they are useless and clutter the hierarchy.
#
# Each entry maps a module_type (as returned by joint.identify -> the
# ``module_type`` field of get_scene_roots) to the set of guide joint
# TYPE NAMES (as returned by joint.get_joint_type, via the .otherType
# string attr for non-standard types) that should be excluded from the
# UE skeleton regardless of source / granularity.
#
# Expand this table as new modules reveal IK-only guide roles.
_HELPER_GUIDE_TYPES_BY_MODULE = {
    "leg": {"BankIN", "BankOUT", "HeelPV", "ToePV"},
    # arm / hindleg / spine / head / etc. have no IK-only guide joints
    # -- every guide in those chains participates in deformation.
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _short(name):
    """Strip any DAG path to the leaf short name."""
    return name.rsplit("|", 1)[-1] if name else name


def _world_pos(node):
    """World-space translation of ``node`` as an MVector."""
    t = cmds.xform(node, query=True, worldSpace=True, translation=True)
    return om.MVector(t[0], t[1], t[2])


def _find_parent_joint(node):
    """Walk up the DAG from ``node`` and return the first joint ancestor
    short name, or ``None`` if no joint ancestor exists."""
    parent = cmds.listRelatives(node, parent=True, fullPath=True)
    while parent:
        p = parent[0]
        if cmds.nodeType(p) == "joint":
            return _short(p)
        parent = cmds.listRelatives(p, parent=True, fullPath=True)
    return None


def _guide_to_jue_name(guide_name, suffix):
    """`L_arm_collar_jInit` -> `L_arm_collar_jUE`."""
    guide_name = _short(guide_name)
    if guide_name.endswith(GUIDE_SUFFIX):
        return guide_name[:-len(GUIDE_SUFFIX)] + suffix
    return guide_name + suffix


def _guide_to_jdef_name(guide_name):
    """`L_arm_collar_jInit` -> `L_arm_collar_jDef`, or ``None`` if the
    guide name does not carry the expected suffix."""
    guide_name = _short(guide_name)
    if guide_name.endswith(GUIDE_SUFFIX):
        return guide_name[:-len(GUIDE_SUFFIX)] + JDEF_SUFFIX
    return None


def _jdef_to_jue_name(jdef_name, suffix):
    """`L_arm_up_0_jDef` -> `L_arm_up_0_jUE`."""
    jdef_name = _short(jdef_name)
    if jdef_name.endswith(JDEF_SUFFIX):
        return jdef_name[:-len(JDEF_SUFFIX)] + suffix
    return jdef_name + suffix


def _module_defgrp_name(module_name):
    """{module_name}_defJoints_grp -- the convention from core/module.py:143."""
    return "%s_defJoints_grp" % module_name


def _module_has_rig(module_name):
    """True iff the module has a populated `_defJoints_grp` with at
    least one `_jDef` inside."""
    grp = _module_defgrp_name(module_name)
    if not cmds.objExists(grp):
        return False
    children = cmds.listRelatives(
        grp, allDescendents=True, type="joint"
    ) or []
    return any(c.endswith(JDEF_SUFFIX) for c in children)


def _module_jdefs(module_name):
    """All `_jDef` joints under the module's `_defJoints_grp` (short
    names).  Empty list if the module has no built rig."""
    grp = _module_defgrp_name(module_name)
    if not cmds.objExists(grp):
        return []
    children = cmds.listRelatives(
        grp, allDescendents=True, type="joint"
    ) or []
    return [c for c in children if c.endswith(JDEF_SUFFIX)]


# ---------------------------------------------------------------------------
# Module / guide topology collection
# ---------------------------------------------------------------------------

def _guide_chain_for_module(guide_root, module_root_set):
    """Preorder-DFS traversal starting at ``guide_root``, stopping at
    any joint that belongs to ANOTHER module (i.e. another entry in
    ``module_root_set``).  Returns an ordered list of joint short names.

    Also returns a ``{child: parent}`` dict limited to the in-module
    pairs, so callers can reconstruct edges for twist insertion.
    """
    chain = [guide_root]
    parent_map = {}

    def _walk(node):
        children = cmds.listRelatives(
            node, children=True, type="joint", fullPath=False
        ) or []
        for c in children:
            if c in module_root_set and c != guide_root:
                # Another module's root -- stop.  That child is the
                # concern of its own module entry.
                continue
            chain.append(c)
            parent_map[c] = node
            _walk(c)

    _walk(guide_root)
    return chain, parent_map


def _collect_modules():
    """Return per-module info required by the build pipeline.

    Each entry::

        {
            "info":          <get_scene_roots entry verbatim>,
            "guide_root":    <short name>,
            "guide_chain":   [root, ...DFS descendants, stopping at other module roots],
            "guide_parent_map": {child_guide: parent_guide, ...}  (in-module only),
            "has_rig":       bool,
            "jdefs":         [all jDef short names under the module's defJointsGrp],
        }
    """
    # Lazy import: Initials cascades through Qt; keep this file standalone-importable.
    from ddrig.base import initials
    scene_roots = initials.Initials().get_scene_roots()
    if not scene_roots:
        raise RuntimeError(
            "No DDRIG guide roots found in the scene.  Create some guides first."
        )

    module_root_set = {r["root_joint"] for r in scene_roots}

    modules = []
    for info in scene_roots:
        guide_root = info["root_joint"]
        module_name = info["module_name"]
        chain, parent_map = _guide_chain_for_module(guide_root, module_root_set)
        modules.append({
            "info": info,
            "guide_root": guide_root,
            "guide_chain": chain,
            "guide_parent_map": parent_map,
            "has_rig": _module_has_rig(module_name),
            "jdefs": _module_jdefs(module_name),
        })
    return modules


def _find_module_of_guide(guide_short, modules):
    """Return the ``module_name`` whose guide_chain contains
    ``guide_short``, or ``None``."""
    for m in modules:
        if guide_short in m["guide_chain"]:
            return m["info"]["module_name"]
    return None


# ---------------------------------------------------------------------------
# Twist jDef placement
# ---------------------------------------------------------------------------

def _partition_jdefs(guide_chain, all_jdefs):
    """Split a module's `_jDef` list into:
        * ``main_jdef_of_guide`` : {guide_short: jdef_short}  for guides
                                   whose naming counterpart exists.
        * ``twist_jdefs``        : jDefs that do not match any guide
                                   (ribbon / sub-chain deform joints).
    """
    expected = {}
    for g in guide_chain:
        candidate = _guide_to_jdef_name(g)
        if candidate:
            expected[candidate] = g
    main_of = {}
    jdef_set = set(all_jdefs)
    for jd in all_jdefs:
        if jd in expected:
            main_of[expected[jd]] = jd
    twist = [jd for jd in all_jdefs if jd not in main_of.values()]
    return main_of, twist


def _twist_jdefs_between(guide_a, guide_b, twist_jdefs):
    """Return twist jDefs whose world position projects into the open
    interval (0, 1) of the parameter t along segment a->b.

    Ordering: by t ascending.  Projection is the scalar
    ``t = ((p - a) . (b - a)) / |b - a|^2``.
    """
    pa = _world_pos(guide_a)
    pb = _world_pos(guide_b)
    axis = pb - pa
    len_sq = axis * axis
    if len_sq < 1e-12:
        return []
    picks = []
    for jd in twist_jdefs:
        pj = _world_pos(jd)
        t = ((pj - pa) * axis) / len_sq
        if 0.0 < t < 1.0:
            picks.append((t, jd))
    picks.sort(key=lambda x: x[0])
    return [jd for _, jd in picks]


# ---------------------------------------------------------------------------
# jUE creation primitives
# ---------------------------------------------------------------------------

def _create_static_jue(from_node, jue_name, parent_jue=None):
    """Create a jUE that copies ``from_node``'s world transform verbatim.

    Used when the source is a guide joint (no driver available) or any
    other static reference.  Returns the short name actually created
    (may be uniquified by Maya)."""
    cmds.select(clear=True)
    # duplicate gives us joint orient + radius for free and drops any
    # inbound connections from matrixConstraints etc.
    dup = cmds.duplicate(from_node, parentOnly=True, name=jue_name)[0]
    dup_short = _short(dup)
    try:
        cmds.parent(dup_short, world=True)
    except RuntimeError:
        pass
    # Overwrite transform to match from_node exactly, just in case the
    # duplicate inherited some transform we did not want.
    matrix = cmds.xform(from_node, query=True, worldSpace=True, matrix=True)
    cmds.xform(dup_short, worldSpace=True, matrix=matrix)
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % dup_short, 0)
    except RuntimeError:
        pass
    # sourceJDef tag: empty string means "static; no driving _jDef".
    if not cmds.attributeQuery(_SOURCE_ATTR, node=dup_short, exists=True):
        cmds.addAttr(dup_short, longName=_SOURCE_ATTR, dataType="string")
    cmds.setAttr("%s.%s" % (dup_short, _SOURCE_ATTR), "", type="string")
    if parent_jue and cmds.objExists(parent_jue):
        try:
            cmds.parent(dup_short, parent_jue)
        except RuntimeError as exc:
            log.warning("UE skeleton: parent %s under %s failed: %s" %
                        (dup_short, parent_jue, exc))
    return dup_short


def _create_driven_jue(jdef, jue_name, parent_jue=None):
    """Create a jUE at ``jdef``'s world transform, tag it with
    ``sourceJDef = jdef``.  Returns the short name actually created."""
    cmds.select(clear=True)
    dup = cmds.duplicate(jdef, parentOnly=True, name=jue_name)[0]
    dup_short = _short(dup)
    try:
        cmds.parent(dup_short, world=True)
    except RuntimeError:
        pass
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % dup_short, 0)
    except RuntimeError:
        pass
    if not cmds.attributeQuery(_SOURCE_ATTR, node=dup_short, exists=True):
        cmds.addAttr(dup_short, longName=_SOURCE_ATTR, dataType="string")
    cmds.setAttr("%s.%s" % (dup_short, _SOURCE_ATTR), jdef, type="string")
    if parent_jue and cmds.objExists(parent_jue):
        try:
            cmds.parent(dup_short, parent_jue)
        except RuntimeError as exc:
            log.warning("UE skeleton: parent %s under %s failed: %s" %
                        (dup_short, parent_jue, exc))
    return dup_short


def _mirror_animation(jdef, jue):
    """Attach parent+scale constraints from ``jdef`` -> ``jue`` so the
    UE joint follows the DDRIG deform joint every frame."""
    try:
        cmds.parentConstraint(jdef, jue, maintainOffset=False)
    except RuntimeError as exc:
        log.warning("parentConstraint(%s -> %s) failed: %s" % (jdef, jue, exc))
        return False
    try:
        cmds.scaleConstraint(jdef, jue, maintainOffset=False)
    except RuntimeError:
        pass
    return True


# ---------------------------------------------------------------------------
# Module sequence planning
# ---------------------------------------------------------------------------

def _filter_deform_guides(guide_chain, guide_parent_map, module_type):
    """Drop IK-helper guides from ``guide_chain`` per
    :data:`_HELPER_GUIDE_TYPES_BY_MODULE` and rewire
    ``guide_parent_map`` so each surviving guide's parent is the
    nearest surviving ancestor (or None when the original chain root
    itself was dropped -- impossible in practice, but handled).

    Returns ``(filtered_chain, repaired_parent_map, filtered_out_list)``.
    A guide is filtered out iff ``joint.get_joint_type(g)`` returns a
    name present in the module's helper set.  Reading the joint type
    is a Maya call; failures (e.g. the attribute is missing) fall back
    to "keep the guide" to avoid accidentally dropping real deform
    chain members.
    """
    helpers = _HELPER_GUIDE_TYPES_BY_MODULE.get(module_type, set())
    if not helpers:
        return list(guide_chain), dict(guide_parent_map), []

    # Import lazily -- joint library touches cmds at module scope,
    # which is fine inside Maya but we keep it defensive.
    from ddrig.library import joint as joint_lib

    filtered_out = set()
    for g in guide_chain:
        try:
            jt = joint_lib.get_joint_type(g)
        except Exception:   # noqa: BLE001
            jt = None
        if jt in helpers:
            filtered_out.add(g)

    if not filtered_out:
        return list(guide_chain), dict(guide_parent_map), []

    keep_chain = [g for g in guide_chain if g not in filtered_out]
    keep_set = set(keep_chain)
    repaired = {}
    for g in keep_chain:
        p = guide_parent_map.get(g)
        # Walk up through filtered-out ancestors until we find a surviving
        # parent or exhaust the chain.
        while p is not None and p not in keep_set:
            p = guide_parent_map.get(p)
        if p is not None:
            repaired[g] = p
    return keep_chain, repaired, sorted(filtered_out)


def _plan_module_sequence(module, use_rig, granularity, suffix):
    """Produce an ordered list of jUE creation records for one module.

    Each record is a dict::

        {
            "jue_name":  <desired short name>,
            "pos_src":   <node whose world transform to duplicate from>,
            "parent_ref": <previous jUE short name> OR _CROSS_MODULE_PLACEHOLDER,
            "drive":     <jdef short name> OR None (for static jUE),
            "guide_src": <guide short name> OR None (main jUEs only),
        }

    Also returns the list of guide joints filtered out as IK helpers
    (via _HELPER_GUIDE_TYPES_BY_MODULE) so callers can log them.

    The order is "root first"; creating them in order respects parent
    references within the same module.
    """
    module_type = module["info"]["module_type"]
    guide_chain, guide_parent_map, filtered_helpers = _filter_deform_guides(
        module["guide_chain"],
        module["guide_parent_map"],
        module_type,
    )
    main_jdef_of_guide, twist_jdefs = _partition_jdefs(
        guide_chain, module["jdefs"] if use_rig else []
    )

    sequence = []
    guide_to_jue_in_module = {}  # guide -> jue_name within this module

    for g in guide_chain:
        parent_g = guide_parent_map.get(g)

        # ---- Twist insertion between parent_g and g (Full + rig only) ---
        incoming_parent_jue = None
        if parent_g is None:
            incoming_parent_jue = _CROSS_MODULE_PLACEHOLDER
        else:
            incoming_parent_jue = guide_to_jue_in_module[parent_g]
            if granularity == "full" and use_rig:
                twists = _twist_jdefs_between(parent_g, g, twist_jdefs)
                for twist_jd in twists:
                    twist_jue = _jdef_to_jue_name(twist_jd, suffix)
                    sequence.append({
                        "jue_name": twist_jue,
                        "pos_src": twist_jd,
                        "parent_ref": incoming_parent_jue,
                        "drive": twist_jd,
                        "guide_src": None,
                    })
                    incoming_parent_jue = twist_jue

        # ---- Main jUE for this guide ------------------------------------
        main_jdef = main_jdef_of_guide.get(g) if use_rig else None
        if main_jdef and cmds.objExists(main_jdef):
            pos_src = main_jdef
            drive = main_jdef
        else:
            pos_src = g
            drive = None
        jue_name = _guide_to_jue_name(g, suffix)
        sequence.append({
            "jue_name": jue_name,
            "pos_src": pos_src,
            "parent_ref": incoming_parent_jue,
            "drive": drive,
            "guide_src": g,
        })
        guide_to_jue_in_module[g] = jue_name

    return sequence, filtered_helpers


def _per_module_source_decision(module, source):
    """Return (use_rig: bool, skip: bool, note: str) for the module.

    * source="guide": always use guide.
    * source="rig":   require rig; if missing -> skip.
    * source="auto":  use rig if present, else guide.
    """
    has_rig = module["has_rig"]
    module_name = module["info"]["module_name"]
    if source == "guide":
        return False, False, "guide-sourced"
    if source == "rig":
        if has_rig:
            return True, False, "rig-sourced"
        return False, True, "no rig; skipped (source=rig)"
    # auto
    if has_rig:
        return True, False, "rig-sourced (auto)"
    return False, False, "guide-sourced (auto fallback; module not built)"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def delete_ue_skeleton(group_name=UE_GROUP):
    """Remove the UE skeleton group and everything beneath it.  Source
    `_jDef`, guides, and skin clusters are untouched.  Returns True if
    something was removed."""
    if cmds.objExists(group_name):
        cmds.delete(group_name)
        return True
    return False


def build_ue_skeleton(
    source="auto",
    granularity="main",
    root_name=UE_ROOT,
    group_name=UE_GROUP,
    suffix=UE_SUFFIX,
):
    """Build a parallel UE-compatible skeleton.

    Args:
        source: ``"guide"`` | ``"rig"`` | ``"auto"`` -- see module docstring.
        granularity: ``"main"`` | ``"full"`` -- see module docstring.
        root_name: short name for the single root joint.
        group_name: short name for the container group.
        suffix: suffix applied to all jUE joints (default ``_jUE``).

    Returns:
        dict with keys::

            {
                "root":           <root_jue>,
                "group":          <group>,
                "created":        [<jue short names in creation order>],
                "skipped":        [(item, reason), ...],
                "module_chains":  {module_name: [jue_short, ...]},
                "module_status":  {module_name: "rig-sourced" | "guide-sourced"
                                                 | "skipped: ..."},
                "jdef_to_jue":    {jdef_short: jue_short},
                "source":         <echoed>,
                "granularity":    <echoed>,
            }

    Raises:
        RuntimeError: no guide roots in the scene, or ``group_name``
            already exists (caller should use ``rebuild_ue_skeleton``).
        ValueError: invalid source / granularity.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(
            "source must be one of %s, got %r" % (_VALID_SOURCES, source)
        )
    if granularity not in _VALID_GRANULARITIES:
        raise ValueError(
            "granularity must be one of %s, got %r" %
            (_VALID_GRANULARITIES, granularity)
        )
    if cmds.objExists(group_name):
        raise RuntimeError(
            "%r already exists. Use rebuild_ue_skeleton() or "
            "delete_ue_skeleton() first." % group_name
        )

    modules = _collect_modules()

    # Holder group + single root joint
    group = cmds.group(empty=True, name=group_name)
    cmds.select(clear=True)
    ue_root = cmds.joint(name=root_name)
    ue_root_short = _short(ue_root)
    cmds.parent(ue_root_short, group)
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % ue_root_short, 0)
    except RuntimeError:
        pass

    module_chains = {}   # module_name -> [jue short names]
    module_status = {}   # module_name -> status label for log
    jdef_to_jue = {}     # jdef short -> jue short
    jue_to_drive = {}    # jue short -> driving jdef (for pass 3 constraints)
    created = []
    skipped = []

    # ------- Pass 1: per-module intra-chain ---------------------------------
    for m in modules:
        mod_name = m["info"]["module_name"]
        use_rig, skip_flag, note = _per_module_source_decision(m, source)
        module_status[mod_name] = note
        if skip_flag:
            module_chains[mod_name] = []
            skipped.append((mod_name, note))
            continue

        sequence, filtered_helpers = _plan_module_sequence(
            m, use_rig, granularity, suffix
        )
        if filtered_helpers:
            # Annotate module_status so the dialog log can show how many
            # IK-helper guides were deliberately excluded.
            module_status[mod_name] = (
                "%s; skipped %d IK helper(s): %s" %
                (note, len(filtered_helpers), ", ".join(filtered_helpers))
            )
        if not sequence:
            module_chains[mod_name] = []
            continue

        chain = []
        # Map jue_name -> (placeholder or resolved parent)
        # Within a module, parents appear in sequence before their children,
        # so resolving is straightforward.
        intra_name_to_real = {}
        for rec in sequence:
            parent_ref = rec["parent_ref"]
            if parent_ref == _CROSS_MODULE_PLACEHOLDER:
                real_parent = None  # placeholder; resolved in pass 2
            else:
                real_parent = intra_name_to_real.get(parent_ref)
            target_name = rec["jue_name"]
            if cmds.objExists(target_name):
                skipped.append(
                    (target_name, "name already taken in scene; skipping")
                )
                continue
            if rec["drive"]:
                jue_short = _create_driven_jue(
                    rec["pos_src"], target_name, parent_jue=real_parent
                )
                jue_to_drive[jue_short] = rec["drive"]
                jdef_to_jue[rec["drive"]] = jue_short
            else:
                jue_short = _create_static_jue(
                    rec["pos_src"], target_name, parent_jue=real_parent
                )
            created.append(jue_short)
            chain.append(jue_short)
            intra_name_to_real[rec["jue_name"]] = jue_short
            # First jUE of the module is the "head" (parent_ref was the
            # placeholder).  Remember the placeholder owner too.
        module_chains[mod_name] = chain

    # ------- Pass 2: cross-module parenting ---------------------------------
    # For every module whose head jUE was created with the placeholder parent,
    # find the correct attachment point: the jUE that corresponds to
    # (guide_root's joint-ancestor).  Fallback: attach to root_jUE.
    for m in modules:
        mod_name = m["info"]["module_name"]
        chain = module_chains.get(mod_name) or []
        if not chain:
            continue
        head = chain[0]
        guide_root = m["guide_root"]
        parent_guide_short = _find_parent_joint(guide_root)
        target_parent = None
        if parent_guide_short is not None:
            expected_jue = _guide_to_jue_name(parent_guide_short, suffix)
            if cmds.objExists(expected_jue):
                target_parent = expected_jue
        if target_parent is None:
            # Either top-level module, or the parent module was skipped /
            # produced no jUE for that guide.  Attach head to root_jUE.
            target_parent = ue_root_short

        # head's current parent after pass 1 is world (because the
        # placeholder resolved to None).  Re-parent under target.
        try:
            cmds.parent(head, target_parent)
        except RuntimeError as exc:
            # Already parented correctly or something blocks the move.
            log.warning(
                "UE skeleton: parent %s under %s failed: %s" %
                (head, target_parent, exc)
            )

    # ------- Pass 3: animation drivers --------------------------------------
    for jue_short, jdef in jue_to_drive.items():
        _mirror_animation(jdef, jue_short)

    return {
        "root": ue_root_short,
        "group": group,
        "created": created,
        "skipped": skipped,
        "module_chains": module_chains,
        "module_status": module_status,
        "jdef_to_jue": jdef_to_jue,
        "source": source,
        "granularity": granularity,
    }


def rebuild_ue_skeleton(
    source="auto",
    granularity="main",
    root_name=UE_ROOT,
    group_name=UE_GROUP,
    suffix=UE_SUFFIX,
):
    """Delete the existing UE skeleton (if any) and rebuild from scratch.
    Idempotent wrapper around :func:`build_ue_skeleton`."""
    delete_ue_skeleton(group_name=group_name)
    return build_ue_skeleton(
        source=source,
        granularity=granularity,
        root_name=root_name,
        group_name=group_name,
        suffix=suffix,
    )


# ---------------------------------------------------------------------------
# UI helper: module status snapshot
# ---------------------------------------------------------------------------

def module_status_snapshot():
    """Return a list of per-module status dicts for UI previewing.

    Each entry::

        {
            "module_name":  str,
            "module_type":  str,
            "side":         "L" | "R" | "C",
            "root_joint":   str,
            "guide_count":  int,
            "has_rig":      bool,
            "jdef_count":   int,
        }

    Safe to call even if there are no guide roots -- returns [].
    """
    try:
        modules = _collect_modules()
    except RuntimeError:
        return []
    out = []
    for m in modules:
        info = m["info"]
        out.append({
            "module_name": info["module_name"],
            "module_type": info["module_type"],
            "side": info["side"],
            "root_joint": info["root_joint"],
            "guide_count": len(m["guide_chain"]),
            "has_rig": m["has_rig"],
            "jdef_count": len(m["jdefs"]),
        })
    return out
