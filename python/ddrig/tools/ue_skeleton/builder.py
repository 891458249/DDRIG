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


def _jdef_to_jue_name(jdef_name, suffix):
    """`L_arm_up_0_jDef` -> `L_arm_up_0_jUE`.  For the legacy base-style
    `_j` suffix we strip that instead; other names just get ``suffix``
    appended so the caller never sees an empty string."""
    jdef_name = _short(jdef_name)
    if jdef_name.endswith(JDEF_SUFFIX):
        return jdef_name[:-len(JDEF_SUFFIX)] + suffix
    if jdef_name.endswith("_j"):
        return jdef_name[:-len("_j")] + suffix
    return jdef_name + suffix


# ---------------------------------------------------------------------------
# Deform-joint detection (scene-persistent, rig-built authority)
# ---------------------------------------------------------------------------
# DDRIG's test_build action (actions/kinematics.py) registers every
# module.deformerJoints list into an objectSet named
# ``def_jointsSet_{rig_name}`` (typically "def_jointsSet_ddrig").  That
# set is the ONLY scene-persistent source of truth for "which joints
# actually skin the mesh" -- far more reliable than scanning by
# `_jDef` suffix or by `{module_name}_defJoints_grp` group existence,
# both of which miss base (singleton `_j`) and anything registered with
# irregular naming (spine's sockets, head's spline outputs, etc.).
#
# Below scans all def_jointsSet_* sets in the scene (supports multiple
# rigs per scene) and merges members into one dedup'd list of short
# names.  Per-module slicing is done by prefix match on module_name.

_HELPER_TOKENS = (
    "_IK_", "_FK_",
    "_orig_", "_SC_", "_RP_",
    "_plug_",
    "_collarEnd_", "_headEnd_",
    "_phalangesTip_", "_lowEnd_", "_upEnd_",
)


def _is_helper_joint(short_name):
    """True iff this joint short name looks like a rig control
    auxiliary (IK / FK / plug / end-leaf) rather than a real deform
    joint.

    Decision rule:
        * Names ending in ``_jDef`` are NEVER helpers -- the ``_jDef``
          suffix is DDRIG's explicit marker for deform joints.  A
          token like ``_headEnd_`` in a jDef name (as in
          ``C_head_headEnd_jDef``) is still a deform -- head's end
          joint genuinely participates in the skin.
        * Names ending in ``_j`` are suspect because the ``_j`` suffix
          is heavily overloaded in DDRIG: base's singleton deform uses
          it, as do arm's IK/FK helpers and every module's ``_plug_j``.
          For these we apply the token blacklist.
        * Anything else (e.g. a custom module using a different suffix)
          falls through as non-helper.

    The name is padded with underscores on both ends so each blacklist
    token matches as a full path segment rather than a stray
    substring."""
    if short_name.endswith(JDEF_SUFFIX):
        return False
    if not short_name.endswith("_j"):
        return False
    padded = "_%s_" % short_name
    return any(tok in padded for tok in _HELPER_TOKENS)


def _find_limb_grp(module_name):
    """Return the full DAG path of the module's limbGrp transform.

    Per ``core/module.py:65``, every module creates a top-level
    transform named exactly ``module_name`` (via
    ``naming.parse([self.module_name])`` with no side, which collapses
    to the module_name verbatim under any naming rule) to house all
    its rig groups -- scale_grp, nonScale_grp, defJoints_grp,
    rigJoints_grp, and nested sub-module grps from spline modules.

    Returns None if no matching transform exists (module not built, or
    limbGrp was renamed by the user)."""
    candidates = cmds.ls(module_name, long=True) or []
    for c in candidates:
        try:
            if cmds.objectType(c) == "transform":
                return c
        except RuntimeError:
            continue
    return None


def _iter_ancestor_shorts(node_path):
    """Yield the short names of every ancestor of ``node_path`` (NOT
    including the node itself).  ``node_path`` must be a full DAG path
    (starts with ``|``).  Used to veto joints living under certain
    sub-grps (e.g. ``*_rigJoints_grp``) without resolving every
    parent via separate Maya calls."""
    parts = node_path.strip("|").split("|")
    for p in parts[:-1]:
        yield p


def _legacy_prefix_scan(module_name):
    """Fallback when the module's limbGrp cannot be located -- mirrors
    the pre-DAG detection that Commit 4bdcb69 shipped.  Keeps us
    working if the user renamed the limbGrp manually."""
    jdef_hits = cmds.ls(module_name + "_*_jDef", type="joint") or []
    j_raw = cmds.ls(module_name + "_*_j", type="joint") or []
    j_hits = [j for j in j_raw if not _is_helper_joint(_short(j))]
    base_singleton = []
    base_cand = module_name + "_j"
    if cmds.objExists(base_cand):
        try:
            if cmds.objectType(base_cand) == "joint":
                base_singleton.append(base_cand)
        except RuntimeError:
            pass
    # Dedup while preserving order.
    seen = set()
    out = []
    for n in jdef_hits + j_hits + base_singleton:
        s = _short(n)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _deform_set_members():
    """Return every joint in any ``def_jointsSet_*`` objectSet, as
    short names, deduplicated.  Retained for diagnostics: Commit 4bdcb69
    used this as the authoritative source, but the DAG-ancestor scan
    in :func:`collect_module_deform_joints` is now primary because it
    catches deform joints whose names do not carry the module's prefix
    (spine's ``Spine_spine_0_jDef``, head's ``neckSplineIK_head_0_jDef``)."""
    sets = cmds.ls("def_jointsSet_*", type="objectSet") or []
    seen = set()
    out = []
    for s in sets:
        members = cmds.sets(s, query=True) or []
        for m in members:
            short = _short(m)
            if short and short not in seen:
                seen.add(short)
                out.append(short)
    return out


def collect_module_deform_joints(module_name):
    """Return all deform joints attributable to ``module_name`` by DAG
    ancestry, as short names.  Matches joints whose limbGrp ancestor
    is the module's top transform, regardless of intermediate grp
    naming (scale_grp / nonScale_grp / defJoints_grp / nested
    sub-module grps).

    Filtering:
        * ``*_jDef`` joints are accepted unconditionally -- DDRIG's
          explicit deform marker.
        * ``*_j`` joints are accepted only if they pass
          :func:`_is_helper_joint` AND are NOT descendants of a
          ``*_rigJoints_grp`` (which houses rig control internals
          using the same ``_j`` suffix as base's singleton deform).
        * Other suffixes are ignored.

    When the limbGrp cannot be located (module not built, or its name
    was changed), falls back to :func:`_legacy_prefix_scan` -- the
    pre-DAG prefix string match."""
    limb_grp = _find_limb_grp(module_name)
    if limb_grp is None:
        return _legacy_prefix_scan(module_name)

    all_joints = cmds.listRelatives(
        limb_grp, allDescendents=True, type="joint", fullPath=True
    ) or []
    seen = set()
    out = []
    for j in all_joints:
        short = _short(j)
        if short in seen:
            continue
        if short.endswith(JDEF_SUFFIX):
            seen.add(short)
            out.append(short)
            continue
        if short.endswith("_j") and not _is_helper_joint(short):
            # Exclude rig-internal joints that happen to use the _j
            # suffix -- those live under *_rigJoints_grp.  Descending
            # into a module's own rigJoints_grp would sweep FK/IK
            # control joints into the deform bucket.
            ancestors = list(_iter_ancestor_shorts(j))
            if any(a.endswith("_rigJoints_grp") for a in ancestors):
                continue
            seen.add(short)
            out.append(short)
    return out


def module_has_rig(module_name):
    """Public: True iff ``module_name``'s limbGrp contains at least
    one deform joint (i.e. the module has been Test Built)."""
    return len(collect_module_deform_joints(module_name)) > 0


def find_deform_for_guide(guide_name, module_name, module_deforms=None):
    """Given a guide joint short name like ``L_arm_collar_jInit``,
    return its deform counterpart (short name) if one exists, else
    ``None``.

    Matching strategy, in priority order:

    1. ``{stem}_jDef`` -- the common convention for arm / leg /
       hindleg / head end / eye / ...
    2. ``{stem}_j`` -- retained for modules like ``base`` that build
       a single-joint deform with the ``_j`` suffix, or for hindleg's
       ``phalangesTip`` leaf.  Helper tokens (``_IK_`` etc.) in the
       candidate short-circuit this branch.
    3. Base-style special: if the guide stem ends with ``_root``,
       try ``{module_name}_j`` (base module's naming).

    The candidate is only accepted if the module actually registered
    it as a deform joint (i.e. it appears in ``module_deforms`` -- the
    list from ``collect_module_deform_joints``).  That gate prevents
    leftover helpers or names-that-happen-to-match from sneaking in."""
    guide_short = _short(guide_name)
    if not guide_short.endswith(GUIDE_SUFFIX):
        return None
    stem = guide_short[:-len(GUIDE_SUFFIX)]
    if module_deforms is None:
        module_deforms = collect_module_deform_joints(module_name)
    member_set = set(module_deforms)

    # Priority 1: stem_jDef
    cand = stem + JDEF_SUFFIX
    if cand in member_set:
        return cand
    # Priority 2: stem_j (rejecting helpers is redundant because
    # module_deforms is already filtered, but belt-and-suspenders).
    cand = stem + "_j"
    if cand in member_set and not _is_helper_joint(cand):
        return cand
    # Priority 3: base-style -- guide stem like "{mod}_root" maps to
    # "{mod}_j" (base module has only one deform joint, named after
    # the module itself with a bare "_j").
    if stem.endswith("_root"):
        base_cand = stem[: -len("_root")] + "_j"
        if base_cand in member_set and not _is_helper_joint(base_cand):
            return base_cand
    return None


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
        # DAG-ancestor scan through the module's limbGrp: catches every
        # deform joint parented anywhere inside (scale_grp / nonScale_grp
        # / defJoints_grp / nested spline-IK sub-module grps).
        module_deforms = collect_module_deform_joints(module_name)
        modules.append({
            "info": info,
            "guide_root": guide_root,
            "guide_chain": chain,
            "guide_parent_map": parent_map,
            "has_rig": bool(module_deforms),
            "deforms": module_deforms,
            # Legacy alias -- some older callers read 'jdefs'.
            "jdefs": module_deforms,
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

def _partition_deforms(guide_chain, module_name, module_deforms):
    """Split a module's registered deform joints into:

        * ``main_of`` : ``{guide_short: deform_short}`` -- guides whose
                        deform counterpart is resolved by
                        ``find_deform_for_guide`` (stem_jDef / stem_j /
                        base-style).
        * ``twist``   : remaining deform joints, in the order they appear
                        in ``module_deforms``, that did NOT claim any
                        guide.  These are ribbon / spline / phalanges-tip
                        joints that get inserted between main joints in
                        Full granularity.
    """
    main_of = {}
    claimed = set()
    for g in guide_chain:
        d = find_deform_for_guide(g, module_name, module_deforms)
        if d is not None and d not in claimed:
            main_of[g] = d
            claimed.add(d)
    twist = [d for d in module_deforms if d not in claimed]
    return main_of, twist


def _twist_deforms_between(guide_a, guide_b, twist_deforms):
    """Return twist deform joints whose world position projects into
    the open interval (0, 1) of the parameter t along segment a->b.

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
    for jd in twist_deforms:
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


def _plan_module_sequence(module, granularity, suffix):
    """Produce an ordered list of jUE creation records for one module.

    Each record is a dict::

        {
            "jue_name":  <desired short name>,
            "pos_src":   <source node whose world transform to duplicate>,
            "parent_ref": <previous jUE short name> OR _CROSS_MODULE_PLACEHOLDER,
            "drive":     <deform joint short name>,   # always non-None now
            "guide_src": <guide short name> OR None,
        }

    Returns ``(sequence, filtered_helpers, skipped_guides)`` so callers
    can log both the IK-helper guides filtered out by
    ``_HELPER_GUIDE_TYPES_BY_MODULE`` and any guide that had no deform
    counterpart (strict rig-only; no guide fallback).

    The module is expected to be built (``module["has_rig"]`` True)
    when this is called; callers that want to skip unbuilt modules
    check that flag up-front in ``build_ue_skeleton``.
    """
    module_name = module["info"]["module_name"]
    module_type = module["info"]["module_type"]
    module_deforms = module["deforms"]

    guide_chain, guide_parent_map, filtered_helpers = _filter_deform_guides(
        module["guide_chain"],
        module["guide_parent_map"],
        module_type,
    )

    main_of_guide, twist_deforms = _partition_deforms(
        guide_chain, module_name, module_deforms
    )

    sequence = []
    skipped_guides = []
    guide_to_jue_in_module = {}  # guide -> jue_name within this module
    # Track the last-created jUE name so that if the current guide has
    # no deform, the NEXT guide's parent reference walks past the hole
    # (i.e. its parent becomes whatever the nearest surviving ancestor
    # produced).
    last_jue_for_guide_path = {}   # guide -> jue created for it (None if skipped)

    for g in guide_chain:
        parent_g = guide_parent_map.get(g)

        # Find the nearest surviving ancestor's jUE (skip over
        # previously-skipped guides that had no deform).
        if parent_g is None:
            incoming_parent_jue = _CROSS_MODULE_PLACEHOLDER
        else:
            # Walk up: parent_g might itself have been skipped.
            p = parent_g
            while p is not None and last_jue_for_guide_path.get(p) is None:
                p = guide_parent_map.get(p)
            if p is None:
                incoming_parent_jue = _CROSS_MODULE_PLACEHOLDER
            else:
                incoming_parent_jue = last_jue_for_guide_path[p]

            # ---- Twist insertion between nearest surviving ancestor and g ---
            if granularity == "full":
                # Use the actual surviving parent-guide for segment projection,
                # NOT parent_g -- gives correct ordering when an intermediate
                # guide was skipped (e.g. arm's Shoulder with no shoulder_jDef).
                proj_parent_g = p if p is not None else None
                if proj_parent_g is not None:
                    twists = _twist_deforms_between(
                        proj_parent_g, g, twist_deforms
                    )
                    for twist_d in twists:
                        twist_jue = _jdef_to_jue_name(twist_d, suffix)
                        sequence.append({
                            "jue_name": twist_jue,
                            "pos_src": twist_d,
                            "parent_ref": incoming_parent_jue,
                            "drive": twist_d,
                            "guide_src": None,
                        })
                        incoming_parent_jue = twist_jue

        # ---- Main jUE for this guide ------------------------------------
        deform = main_of_guide.get(g)
        if deform is None:
            # Strict rig-only mode: no deform => no jUE for this guide.
            # (Shoulder in arm is the canonical case -- IK-only guide
            # with no shoulder_jDef.)
            skipped_guides.append(g)
            last_jue_for_guide_path[g] = None
            continue

        jue_name = _guide_to_jue_name(g, suffix)
        sequence.append({
            "jue_name": jue_name,
            "pos_src": deform,
            "parent_ref": incoming_parent_jue,
            "drive": deform,
            "guide_src": g,
        })
        guide_to_jue_in_module[g] = jue_name
        last_jue_for_guide_path[g] = jue_name

    return sequence, filtered_helpers, skipped_guides


def _per_module_source_decision(module, source):
    """Return ``(skip: bool, note: str)`` for the module.

    In the guide-first -> rig-only refactor, ``source`` no longer
    selects a data source -- all three values now require the module
    to have a built rig (i.e. at least one member in
    ``def_jointsSet_*``).  The difference between the three is UI
    intent only:

        source='rig'   -- user explicitly demanded rig; loudly skip unbuilt.
        source='auto'  -- skip unbuilt, same as rig, but with a gentler log.
        source='guide' -- kept for backward-compat; same behaviour as auto.

    Modules that ARE built proceed through ``_plan_module_sequence``;
    modules that are NOT built are skipped with a clear log entry.
    Guide fallback (generating static jUEs at guide positions) has
    been removed per user requirement -- it produced wrong topology
    since guide chains include control helpers that are not part of
    the deform rig.
    """
    has_rig = module["has_rig"]
    if has_rig:
        label = {
            "rig":   "rig-built",
            "auto":  "rig-built (auto)",
            "guide": "rig-built",
        }.get(source, "rig-built")
        return False, label
    skip_note = {
        "rig":   "not built; skipped (source=rig)",
        "auto":  "not built; skipped",
        "guide": "not built; skipped",
    }.get(source, "not built; skipped")
    return True, skip_note


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _delete_ue_skeleton_impl(group_name):
    """Unwrapped delete -- does not open its own undo chunk (callers
    that want to group delete + build must open a single chunk
    themselves)."""
    if cmds.objExists(group_name):
        cmds.delete(group_name)
        return True
    return False


def delete_ue_skeleton(group_name=UE_GROUP):
    """Remove the UE skeleton group and everything beneath it.  Source
    `_jDef`, guides, and skin clusters are untouched.  Returns True if
    something was removed.

    Wrapped in a single undo chunk so Ctrl+Z revives the skeleton in
    one step."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_delete_skeleton")
    try:
        return _delete_ue_skeleton_impl(group_name)
    finally:
        cmds.undoInfo(closeChunk=True)


def _build_ue_skeleton_impl(
    source,
    granularity,
    root_name,
    group_name,
    suffix,
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
        skip_flag, note = _per_module_source_decision(m, source)
        module_status[mod_name] = note
        if skip_flag:
            module_chains[mod_name] = []
            skipped.append((mod_name, note))
            continue

        sequence, filtered_helpers, skipped_guides = _plan_module_sequence(
            m, granularity, suffix
        )
        # Enrich module_status with any filtered IK helpers and any
        # guides that had no deform counterpart (neither _jDef nor _j
        # matched, and it wasn't the base-style special).  Both counts
        # are useful for diagnosing why a build is smaller than expected.
        annotations = []
        if filtered_helpers:
            annotations.append(
                "skipped %d IK helper(s): %s" %
                (len(filtered_helpers), ", ".join(filtered_helpers))
            )
        if skipped_guides:
            annotations.append(
                "%d guide(s) with no deform counterpart: %s" %
                (len(skipped_guides), ", ".join(skipped_guides))
            )
        if annotations:
            module_status[mod_name] = "%s; %s" % (
                note, "; ".join(annotations)
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
            # Strict rig-only: every planned record carries a driver.
            # _create_static_jue is kept in the module for hotfix / legacy
            # reruns but build_ue_skeleton no longer emits static records.
            jue_short = _create_driven_jue(
                rec["pos_src"], target_name, parent_jue=real_parent
            )
            if rec["drive"]:
                jue_to_drive[jue_short] = rec["drive"]
                jdef_to_jue[rec["drive"]] = jue_short
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


def build_ue_skeleton(
    source="auto",
    granularity="main",
    root_name=UE_ROOT,
    group_name=UE_GROUP,
    suffix=UE_SUFFIX,
):
    """Public entry: build the UE skeleton inside a single undo chunk
    so a Ctrl+Z reverts the whole operation in one step.

    See :func:`_build_ue_skeleton_impl` for parameter semantics and
    return shape."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_build_skeleton")
    try:
        return _build_ue_skeleton_impl(
            source=source,
            granularity=granularity,
            root_name=root_name,
            group_name=group_name,
            suffix=suffix,
        )
    finally:
        cmds.undoInfo(closeChunk=True)


def rebuild_ue_skeleton(
    source="auto",
    granularity="main",
    root_name=UE_ROOT,
    group_name=UE_GROUP,
    suffix=UE_SUFFIX,
):
    """Delete-then-build in a single undo chunk, so a Ctrl+Z reverts
    both stages at once (restoring whatever skeleton existed before
    Rebuild was called)."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_rebuild_skeleton")
    try:
        _delete_ue_skeleton_impl(group_name)
        return _build_ue_skeleton_impl(
            source=source,
            granularity=granularity,
            root_name=root_name,
            group_name=group_name,
            suffix=suffix,
        )
    finally:
        cmds.undoInfo(closeChunk=True)


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
            "deform_count": len(m["deforms"]),
            # Legacy alias retained so callers coded against the old
            # field name keep working.
            "jdef_count": len(m["deforms"]),
        })
    return out


def detection_report():
    """Return a list of dicts summarising the detection state of every
    module in the scene -- used by the UI's "Dump Detection Report"
    button to expose the raw scan results without kicking off a build.

    Each entry::

        {
            "module_name":  str,
            "has_rig":      bool,
            "guide_count":  int,
            "deforms":      [short name, ...],      # full list
        }
    """
    try:
        modules = _collect_modules()
    except RuntimeError:
        return []
    out = []
    for m in modules:
        out.append({
            "module_name": m["info"]["module_name"],
            "has_rig": m["has_rig"],
            "guide_count": len(m["guide_chain"]),
            "deforms": list(m["deforms"]),
        })
    return out
