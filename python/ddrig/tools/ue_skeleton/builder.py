"""Build a parallel UE-compatible skeleton -- rule-driven, def-only.

Overview
--------

DDRIG's deform joints (`_jDef` and the base module's singleton `_j`)
form a flat layout optimised for Maya's matrix math.  UE / Unity
require a single-root hierarchical skeleton, so this tool generates a
PARALLEL skeleton of `_jUE` joints that mirror each deform joint 1:1,
wired up by parent+scale constraints so they follow the DDRIG rig
every frame.

Topology sourcing (no guide chain, no spatial heuristics)
---------------------------------------------------------

Every `_jDef` short name carries two pieces of information:

    * a segment_key (``collar`` / ``up`` / ``elbow`` / ``socket_root`` /
      ``spline`` / ...)
    * an optional trailing integer index

:func:`parse_def_name` extracts ``(segment_key, index)`` from each name,
honouring a user-extensible submodule prefix map (e.g.
``Spine_spine_0_jDef`` -> segment ``spline`` index ``0``).

:data:`_DEFAULT_UE_TOPOLOGY_RULES` declares, for every ``module_type``,
the ordered list of segments with per-segment mode (``single`` vs
``index_asc``) and parent reference.  :func:`build_module_topology`
buckets each module's deforms by segment, sorts ``index_asc`` segments
by index, then emits ``(child_def, parent_def)`` pairs by walking the
rule.  The result is a deterministic, reproducible UE hierarchy that
does not depend on guide joints, world positions, or arbitrary
thresholds.

Deform-joint detection (twin-layer insurance)
---------------------------------------------

:func:`collect_module_deform_joints` returns the UNION of:

    1. **DAG ancestry**: every joint under the module's limbGrp whose
       short name ends in ``_jDef`` (or non-helper ``_j``), excluding
       anything under a ``*_rigJoints_grp``.
    2. **Skin insurance**: every joint that is an influence of any
       ``skinCluster`` and lives under the same limbGrp, regardless of
       name.  Catches hand-wired bindings with non-standard naming.

Key invariants
--------------
* Only the ``ue_skeleton_grp`` subtree is touched.  Source `_jDef`,
  skin clusters, and guide joints are never modified.
* Each `_jUE` carries a ``sourceJDef`` string attribute pointing to its
  driving `_jDef` (never empty in the rule-driven pipeline).
  :mod:`ddrig.tools.ue_skeleton.skin_swap` uses this map to transfer
  weights without guessing names.
* Repeat-safe: :func:`rebuild_ue_skeleton` is delete + build in one
  undo chunk.  :func:`build_ue_skeleton` auto-clears a stale group
  before starting.
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


# ---------------------------------------------------------------------------
# Topology rules + submodule prefix map
# ---------------------------------------------------------------------------
# Rule entry format: (segment_key, mode, parent_ref)
#   segment_key   token emitted by parse_def_name after stripping
#                 module prefix + ``_jDef`` / ``_j`` suffix.  ``""``
#                 matches bare ``{module}_{N}_jDef``.  ``_singleton``
#                 matches the case where the stripped core is empty
#                 (e.g. ``base_j`` -> core="" -> "_singleton").
#   mode          ``"single"``     - one joint in this segment
#                 ``"index_asc"``  - multiple joints sorted by trailing _N
#   parent_ref    ``None``                - module root (cross-module attach)
#                 ``"segment"``           - parents to that single segment's joint
#                 ``"segment:last"``      - parents to the last joint of an
#                                            index_asc segment
_DEFAULT_UE_TOPOLOGY_RULES = {
    "arm": [
        ("collar",       "single",    None),
        ("up",           "index_asc", "collar"),
        ("elbow",        "single",    "up:last"),
        ("low",          "index_asc", "elbow"),
        ("hand",         "single",    "low:last"),
    ],
    "leg": [
        ("legRoot",      "single",    None),
        ("up",           "index_asc", "legRoot"),
        ("knee",         "single",    "up:last"),
        ("low",          "index_asc", "knee"),
        ("foot",         "single",    "low:last"),
        ("ball",         "single",    "foot"),
        ("toe",          "single",    "ball"),
    ],
    "hindleg": [
        ("hindLegRoot",  "single",    None),
        ("hindHip",      "single",    "hindLegRoot"),
        ("stifle",       "single",    "hindHip"),
        ("hock",         "single",    "stifle"),
        ("phalanges",    "single",    "hock"),
    ],
    "spine": [
        ("socket_root",  "single",    None),
        ("spline",       "index_asc", "socket_root"),
        ("socket_chest", "single",    "spline:last"),
    ],
    "head": [
        ("spline",       "index_asc", None),
        ("head",         "single",    "spline:last"),
        ("headEnd",      "single",    "head"),
    ],
    "base":      [("_singleton", "single",    None)],
    "connector": [("_singleton", "single",    None)],
    "eye":       [("_singleton", "single",    None)],
    "finger":    [("",           "index_asc", None)],
    "fkik":      [("",           "index_asc", None)],
    "singleton": [("",           "index_asc", None)],
    "surface":   [("",           "index_asc", None)],
    "tail":      [("",           "index_asc", None)],
    "tentacle":  [("",           "index_asc", None)],
}


# Default submodule-prefix -> (target_module, target_segment_key).
# Parse step 2 consults this before stripping the module name, letting
# spline / spline-IK internals (``Spine_spine_0_jDef``,
# ``neckSplineIK_head_0_jDef``) land in the right segment bucket without
# each module having to change its naming.  Users can extend this list
# via the Prefix Mapping dialog -- the UI merges defaults + user entries
# and passes the combined list down as ``prefix_map``.
_DEFAULT_SUBMOD_PREFIX_MAP = [
    {"submod_prefix": "Spine_spine",
     "target_module": "spine", "target_segment": "spline"},
    {"submod_prefix": "neckSplineIK_head",
     "target_module": "head",  "target_segment": "spline"},
]


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

        * Names ending in ``_jDef`` are NEVER helpers.
        * Names ending in ``_j`` are checked against the token
          blacklist -- the ``_j`` suffix is overloaded across IK/FK
          helpers, plugs, and the base module's singleton deform.
        * Other suffixes fall through as non-helper."""
    if short_name.endswith(JDEF_SUFFIX):
        return False
    if not short_name.endswith("_j"):
        return False
    padded = "_%s_" % short_name
    return any(tok in padded for tok in _HELPER_TOKENS)


# ---------------------------------------------------------------------------
# limbGrp anchoring + DAG ancestry helpers
# ---------------------------------------------------------------------------

def _find_limb_grp(module_name):
    """Return the full DAG path of the module's limbGrp transform.

    Per ``core/module.py``, every module creates a top-level transform
    named exactly ``module_name`` to house all its rig groups
    (scale_grp, nonScale_grp, defJoints_grp, rigJoints_grp, nested
    sub-module grps for spline modules).

    Returns None when no matching transform exists (module not built,
    or user renamed the limbGrp)."""
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
    including the node itself).  ``node_path`` must be a full DAG path.
    Used to veto joints living under ``*_rigJoints_grp`` without
    resolving every parent via separate Maya calls."""
    parts = node_path.strip("|").split("|")
    for p in parts[:-1]:
        yield p


def _legacy_prefix_scan(module_name):
    """Fallback when the module's limbGrp cannot be located.  Returns a
    list of full DAG paths."""
    jdef_hits = cmds.ls(module_name + "_*_jDef", type="joint", long=True) or []
    j_raw = cmds.ls(module_name + "_*_j", type="joint", long=True) or []
    j_hits = [j for j in j_raw if not _is_helper_joint(_short(j))]
    base_singleton = []
    base_cand = module_name + "_j"
    hits = cmds.ls(base_cand, type="joint", long=True) or []
    base_singleton.extend(hits)
    seen = set()
    out = []
    for n in jdef_hits + j_hits + base_singleton:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _dag_scan_deforms(module_name, limb_grp):
    """Layer 1: every joint under ``limb_grp`` whose short name ends in
    ``_jDef``, plus the one special-case ``{module_name}_j`` singleton
    used by the ``base`` module.  Returns list of full DAG paths.

    Rationale for the strict ``_j`` rule: the ``_j`` suffix is heavily
    overloaded inside DDRIG (spline-IK drivers like
    ``Spine_spine_splineDriver_N_j``, IK/FK origins, plugs, ribbon
    internals, ...).  The :data:`_HELPER_TOKENS` blacklist was not
    exhaustive enough to reliably exclude every such node -- new helper
    patterns would sneak through as "deform" and contaminate the UE
    skeleton.  The only ``_j`` joint we positively want is the base
    module's singleton, which by construction has the exact short name
    ``{module_name}_j``; every other ``_j`` is a helper and rejected.

    The skin-cluster insurance layer (:func:`_skin_influences_under`)
    still catches any non-conventional ``_j`` joint that is actually
    bound to a mesh, so strictness here does not risk dropping real
    deform joints that have been skinned."""
    if limb_grp is None:
        return _legacy_prefix_scan(module_name)
    all_joints = cmds.listRelatives(
        limb_grp, allDescendents=True, type="joint", fullPath=True
    ) or []
    singleton_short = module_name + "_j"
    result = []
    for j in all_joints:
        short = _short(j)
        if short.endswith(JDEF_SUFFIX):
            result.append(j)
            continue
        if short == singleton_short:
            ancestors = list(_iter_ancestor_shorts(j))
            if any(a.endswith("_rigJoints_grp") for a in ancestors):
                continue
            result.append(j)
        # Any other _j suffix is a helper -- silently rejected.
    return result


def _skin_influences_under(limb_grp):
    """Layer 2: every joint that is a ``skinCluster`` influence AND
    lives under ``limb_grp`` (long-path ancestry check).  Returns list
    of full DAG paths.  Catches hand-wired bindings whose names do not
    follow DDRIG conventions."""
    if limb_grp is None:
        return []
    limb_long = cmds.ls(limb_grp, long=True) or []
    if not limb_long:
        return []
    limb_prefix = limb_long[0]

    result = []
    seen = set()
    for sc in cmds.ls(type="skinCluster") or []:
        try:
            influences = cmds.skinCluster(sc, query=True, influence=True) or []
        except RuntimeError:
            continue
        for inf in influences:
            try:
                inf_long_list = cmds.ls(inf, long=True) or []
            except RuntimeError:
                continue
            if not inf_long_list:
                continue
            inf_path = inf_long_list[0]
            if inf_path in seen:
                continue
            if inf_path == limb_prefix or inf_path.startswith(limb_prefix + "|"):
                seen.add(inf_path)
                result.append(inf_path)
    return result


def collect_module_deform_joints(module_name):
    """Return all deform-purpose joints for ``module_name`` as full DAG
    paths.  Twin-layer detection -- union of DAG ancestry under limbGrp
    and skinCluster influence-under-limbGrp -- preserves DAG-scan order
    first, then appends skin-only joints.

    Joints that the skin layer pulls in but the (strict) DAG layer
    rejected are logged at info level so users can spot non-deform
    helpers that leaked in via skin binding (typical pattern: a user
    accidentally bound a splineDriver joint)."""
    limb_grp = _find_limb_grp(module_name)

    dag_hits = _dag_scan_deforms(module_name, limb_grp)
    dag_set = set(dag_hits)

    skin_hits = _skin_influences_under(limb_grp) if limb_grp else []

    result = list(dag_hits)
    for j in skin_hits:
        if j not in dag_set:
            log.info(
                "UE skeleton: skin-only inclusion in %s -- %s "
                "(not in DAG defs; check if this is an intentional bind)"
                % (module_name, _short(j))
            )
            result.append(j)
    return result


def module_has_rig(module_name, root=None):  # noqa: ARG001 (root accepted for API convenience)
    """Public: True iff ``module_name`` has any detected deform joint.

    The optional ``root`` argument is accepted so UI callers that hold
    the guide root path can pass it without us looking it up, but it is
    not consulted -- detection is purely limbGrp-based."""
    return len(collect_module_deform_joints(module_name)) > 0


# ---------------------------------------------------------------------------
# Name parsing + topology construction
# ---------------------------------------------------------------------------

def parse_def_name(short_name, module_name, prefix_map=None):
    """Parse a deform joint short name into ``(segment_key, index)``.

    Returns ``None`` when the name does not belong to ``module_name``
    under any interpretation.  ``index`` is an ``int`` when the name
    has a trailing ``_N`` component, else ``None``.

    Parsing order:
        1. Strip ``_jDef`` / ``_j`` suffix.  Names with neither are
           rejected outright.
        2. Consult the submodule prefix map first -- entries whose
           ``submod_prefix + "_"`` is an exact prefix of the stripped
           core (and whose ``target_module`` matches ``module_name``)
           override the default prefix strip and yield the declared
           ``target_segment`` plus any trailing integer in the remainder.
        3. Strip ``module_name + "_"`` from the core, case-insensitively.
           If the core IS exactly the module name, return ``_singleton``
           (e.g. ``base_j`` -> core="base" -> segment="_singleton").
        4. Split the remainder by ``_``; if the last piece is an
           integer, treat it as the index and the rest as segment_key.
           Otherwise the whole remainder is the segment_key and index
           is ``None``.

    See module docstring for worked examples."""
    if prefix_map is None:
        prefix_map = _DEFAULT_SUBMOD_PREFIX_MAP

    # Step 1: strip suffix.
    if short_name.endswith(JDEF_SUFFIX):
        core = short_name[:-len(JDEF_SUFFIX)]
    elif short_name.endswith("_j"):
        core = short_name[:-len("_j")]
    else:
        return None

    # Step 2: submodule prefix override.
    #
    # Strict policy: a prefix entry claims a name only when the
    # remainder after ``submod_prefix_`` is **pure digits**.  Any other
    # suffix (e.g. ``splineDriver_0``, ``IK_1``, ``orig_up``) means the
    # node is a helper inside the sub-submodule, NOT a ribbon member,
    # and must fall through to Step 3 so it either parses as a generic
    # segment or is reported as unmapped.
    for entry in prefix_map:
        if entry.get("target_module") != module_name:
            continue
        pfx = entry.get("submod_prefix") or ""
        if not pfx:
            continue
        if core.startswith(pfx + "_"):
            remainder = core[len(pfx) + 1:]
            if remainder.isdigit():
                return (entry.get("target_segment") or "", int(remainder))
            # Non-pure-digit remainder -> let another prefix or Step 3
            # handle it.  Continue scanning in case a longer prefix
            # entry also matches.
            continue
        if core == pfx:
            return (entry.get("target_segment") or "", None)

    # Step 3: strip module_name prefix (case-insensitive).
    mn_lower = module_name.lower()
    core_lower = core.lower()
    if core_lower.startswith(mn_lower + "_"):
        core = core[len(module_name) + 1:]
    elif core_lower == mn_lower:
        return ("_singleton", None)
    else:
        # Doesn't belong to this module.
        return None

    # Step 4: final segment/index split.
    if not core:
        return ("_singleton", None)
    parts = core.split("_")
    if parts[-1].isdigit():
        return ("_".join(parts[:-1]), int(parts[-1]))
    return ("_".join(parts), None)


def _resolve_parent_ref(parent_ref, seg_single, seg_last):
    """Translate a rule's ``parent_ref`` token into a concrete def joint
    full path.  Returns ``None`` when the reference resolves to the
    module root (cross-module attachment target, filled in later), or
    when the referenced segment has no emitted joint yet."""
    if parent_ref is None:
        return None
    if ":" in parent_ref:
        seg, which = parent_ref.split(":", 1)
        if which == "last":
            return seg_last.get(seg)
        return seg_single.get(seg) or seg_last.get(seg)
    return seg_single.get(parent_ref) or seg_last.get(parent_ref)


def build_module_topology(module_name, module_type, all_defs,
                          prefix_map=None, rules=None):
    """Convert a module's deform joints into UE hierarchy pairs.

    Returns ``(chain, warnings)`` where:
        * ``chain`` is a list of ``(child_def_fullpath, parent_def_fullpath_or_None)``.
          Pairs with ``parent_def_fullpath == None`` are the module's
          roots -- cross-module attachment is resolved by the caller.
        * ``warnings`` is a list of human-readable strings describing
          unparseable or off-rule deforms; caller should log them but
          not abort.

    Parsing is done against the supplied ``rules`` (default:
    :data:`_DEFAULT_UE_TOPOLOGY_RULES[module_type]`) and
    ``prefix_map`` (default + user merged by the UI layer)."""
    if rules is None:
        rules = _DEFAULT_UE_TOPOLOGY_RULES.get(module_type)
    if not rules:
        return [], ["No topology rule for module_type %r" % module_type]

    if prefix_map is None:
        prefix_map = _DEFAULT_SUBMOD_PREFIX_MAP

    # Step 1: bucket by segment_key.
    segments = {key: [] for (key, _mode, _pref) in rules}
    warnings = []
    for d in all_defs:
        short = _short(d)
        parsed = parse_def_name(short, module_name, prefix_map)
        if parsed is None:
            warnings.append(
                "Unmapped deform %r (could not parse for module %r)"
                % (short, module_name)
            )
            continue
        seg, idx = parsed
        if seg in segments:
            segments[seg].append((idx, d))
        else:
            warnings.append(
                "Unmapped deform %r -> segment=%r not in rules for %r"
                % (short, seg, module_type)
            )

    # Step 2: sort index_asc segments.
    for (key, mode, _pref) in rules:
        if mode == "index_asc":
            segments[key].sort(
                key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0)
            )

    # Step 3: emit chain pairs in rule order.
    chain = []
    seg_single = {}
    seg_last = {}
    for (key, mode, parent_ref) in rules:
        entries = segments.get(key) or []
        if not entries:
            continue
        if mode == "single":
            d = entries[0][1]
            parent = _resolve_parent_ref(parent_ref, seg_single, seg_last)
            chain.append((d, parent))
            seg_single[key] = d
            seg_last[key] = d
            if len(entries) > 1:
                warnings.append(
                    "segment %r in %r had %d candidates in 'single' mode; "
                    "kept first, discarded the rest" %
                    (key, module_name, len(entries))
                )
        elif mode == "index_asc":
            parent = _resolve_parent_ref(parent_ref, seg_single, seg_last)
            for (_idx, d) in entries:
                chain.append((d, parent))
                parent = d
            seg_last[key] = entries[-1][1]
    return chain, warnings


# ---------------------------------------------------------------------------
# jUE creation primitives + cross-module helpers
# ---------------------------------------------------------------------------

def _def_to_jue_name(def_fullpath, suffix):
    """Translate a deform joint full path into the desired jUE short
    name.  ``{name}_jDef`` -> ``{name}{suffix}``; ``{name}_j`` ->
    ``{name}{suffix}``; anything else gets ``suffix`` appended."""
    short = _short(def_fullpath)
    if short.endswith(JDEF_SUFFIX):
        return short[:-len(JDEF_SUFFIX)] + suffix
    if short.endswith("_j"):
        return short[:-len("_j")] + suffix
    return short + suffix


def _create_jue_from_def(def_fullpath, jue_name):
    """Create a jUE at ``def_fullpath``'s world translation+rotation,
    tagged with ``sourceJDef`` = def's short name.  Returns the jUE's
    short name.

    DDRIG's nested rig groups (``rig_grp/ddrig_grp/{module}/...``) often
    carry non-identity scale on intermediate transforms.  When we
    duplicate a deform joint, those accumulated scales bleed into the
    duplicate's local channels; users see jUE.scale reading something
    like ``(0.21, 0.71, 5.33)`` -- distinctly NOT 1.  UE expects each
    bone to start at unit scale and inherit only what the rig animation
    tells it to.

    The 5-step recipe below produces a jUE whose local scale is exactly
    ``(1, 1, 1)`` and whose world translation/rotation match the def
    joint (scale is intentionally NOT mirrored to world):

        1. ``cmds.duplicate(parentOnly=True)`` -- copy the joint without
           shapes/children.
        2. Re-parent the duplicate under the world so we have a clean
           context to wipe local channels.
        3. Capture the def's world translation + rotation (NOT scale).
        4. Reset duplicate's local ``scale`` to 1, ``shear`` to 0, and
           ``jointOrient`` to 0 -- this kills any inherited residue.
        5. Re-apply the captured world translation + rotation via
           ``xform``; tag ``sourceJDef``; disable ``segmentScaleCompensate``.
    """
    cmds.select(clear=True)

    # Step 1: duplicate the def joint shapeless / childless.
    dup = cmds.duplicate(def_fullpath, name=jue_name, parentOnly=True)[0]
    dup_short = _short(dup)

    # Step 2: detach to world so we can clean local channels safely.
    try:
        if cmds.listRelatives(dup_short, parent=True, fullPath=True):
            dup_short = _short(cmds.parent(dup_short, world=True)[0])
    except RuntimeError:
        pass

    # Step 3: capture def's world translation + rotation (intentionally
    # NOT world scale -- mirroring world scale is what poisons the
    # local channels).
    try:
        def_world_t = cmds.xform(
            def_fullpath, query=True, worldSpace=True, translation=True
        )
    except RuntimeError:
        def_world_t = None
    try:
        def_world_ro = cmds.xform(
            def_fullpath, query=True, worldSpace=True, rotation=True
        )
    except RuntimeError:
        def_world_ro = None

    # Step 4: reset local channels on the duplicate.
    for ch in ("scaleX", "scaleY", "scaleZ"):
        try:
            cmds.setAttr("%s.%s" % (dup_short, ch), 1.0)
        except RuntimeError:
            pass
    for ch in ("shearXY", "shearXZ", "shearYZ"):
        try:
            cmds.setAttr("%s.%s" % (dup_short, ch), 0.0)
        except RuntimeError:
            pass
    if cmds.attributeQuery("jointOrient", node=dup_short, exists=True):
        try:
            cmds.setAttr(
                "%s.jointOrient" % dup_short, 0, 0, 0, type="double3"
            )
        except RuntimeError:
            pass

    # Step 5: re-apply translation + rotation; tag; SSC off.
    if def_world_t is not None:
        try:
            cmds.xform(
                dup_short, worldSpace=True, translation=def_world_t
            )
        except RuntimeError:
            pass
    if def_world_ro is not None:
        try:
            cmds.xform(
                dup_short, worldSpace=True, rotation=def_world_ro
            )
        except RuntimeError:
            pass
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % dup_short, 0)
    except RuntimeError:
        pass
    if not cmds.attributeQuery(_SOURCE_ATTR, node=dup_short, exists=True):
        cmds.addAttr(dup_short, longName=_SOURCE_ATTR, dataType="string")
    cmds.setAttr(
        "%s.%s" % (dup_short, _SOURCE_ATTR),
        _short(def_fullpath),
        type="string",
    )
    return dup_short


def _mirror_animation(def_fullpath, jue_short):
    """Attach parent+scale constraints from def -> jUE.

    Critical: ``maintainOffset=True``.  With ``maintainOffset=False``,
    Maya forces ``jUE.worldTransform == def.worldTransform`` every
    frame, copying DDRIG's accumulated rig-group scale into jUE's local
    channels.  With ``maintainOffset=True``, the constraint records
    the current (clean, scale=1) offset and only follows DEF's
    *relative* changes from there -- so jUE's local scale stays at 1
    in the rest pose and only deviates if the rig itself animates a
    non-1 scale on the def joint."""
    try:
        cmds.parentConstraint(def_fullpath, jue_short, maintainOffset=True)
    except RuntimeError as exc:
        log.warning("parentConstraint(%s -> %s) failed: %s" %
                    (def_fullpath, jue_short, exc))
        return False
    try:
        cmds.scaleConstraint(def_fullpath, jue_short, maintainOffset=True)
    except RuntimeError:
        pass
    return True


def _find_owning_module_by_guide(guide_name, scene_roots):
    """Walk up the DAG from a guide joint until we hit any module's
    ``root_joint``; return that module's ``module_name``.

    Guide joints all live under ``ddrig_refGuides`` -- a DAG branch
    entirely separate from each module's limbGrp -- so the old
    "scan fullpath for a limbGrp-named segment" trick never matched.
    The correct signal is the guide's **own** parent chain: modules
    are wired together at the guide level (e.g. ``L_arm_collar_jInit``
    parented under ``spine_3_jInit``), so walking up from any guide
    until we meet another module's registered root_joint tells us the
    owning parent module.

    Returns ``None`` when no ancestor guide is a scene root (e.g. the
    top-level ``base`` module whose root has no joint parent).
    """
    roots_by_short = {r["root_joint"]: r["module_name"] for r in scene_roots}
    full_list = cmds.ls(guide_name, long=True) or []
    if not full_list:
        return None
    current = full_list[0]
    visited = set()
    while current and current not in visited:
        visited.add(current)
        short = current.rsplit("|", 1)[-1]
        if short in roots_by_short:
            return roots_by_short[short]
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            return None
        current = parents[0]
    return None


def _pick_cross_module_attach(child_roots, candidate_jues):
    """Among ``candidate_jues`` (the parent module's jUE set), pick the
    one spatially closest to the first child jUE root.  Simple and
    adequate when the parent module has a single linear chain -- the
    "correct" attach point is typically the last or closest jUE."""
    candidate_jues = [j for j in candidate_jues if j and cmds.objExists(j)]
    if not child_roots or not candidate_jues:
        return None
    try:
        child_pos = _world_pos(child_roots[0])
    except RuntimeError:
        return candidate_jues[0]
    best = None
    best_dist = float("inf")
    for jue in candidate_jues:
        try:
            pos = _world_pos(jue)
        except RuntimeError:
            continue
        d = (pos - child_pos).length()
        if d < best_dist:
            best = jue
            best_dist = d
    return best


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def _delete_ue_skeleton_impl(group_name):
    """Unwrapped delete -- does not open its own undo chunk.

    Deletes the entire ``group_name`` subtree, explicitly purging any
    parent/scale constraint nodes that live under each jUE FIRST.
    Constraints occasionally hold connections from external rig nodes
    that prevent a clean cascade-delete and leave orphan _jUE behind,
    which then triggers "name already taken" on the next Build."""
    if not cmds.objExists(group_name):
        return False

    all_descendants = cmds.listRelatives(
        group_name, allDescendents=True, fullPath=True
    ) or []

    constraints_to_delete = []
    for node in all_descendants:
        if not cmds.objExists(node):
            continue
        try:
            if cmds.objectType(node, isAType="constraint"):
                constraints_to_delete.append(node)
        except RuntimeError:
            pass
        try:
            kids = cmds.listRelatives(
                node, children=True, type="constraint", fullPath=True
            ) or []
        except RuntimeError:
            kids = []
        constraints_to_delete.extend(kids)
    constraints_to_delete = list(dict.fromkeys(constraints_to_delete))
    for c in constraints_to_delete:
        if cmds.objExists(c):
            try:
                cmds.delete(c)
            except RuntimeError as exc:
                log.warning(
                    "UE skeleton delete: constraint %s failed: %s" % (c, exc)
                )

    if cmds.objExists(group_name):
        try:
            cmds.delete(group_name)
        except RuntimeError as exc:
            log.warning(
                "UE skeleton delete: group %s failed: %s" % (group_name, exc)
            )
            return False
    return True


def delete_ue_skeleton(group_name=UE_GROUP):
    """Remove the UE skeleton group and everything beneath it, inside
    a single undo chunk."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_delete_skeleton")
    try:
        return _delete_ue_skeleton_impl(group_name)
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _build_ue_skeleton_impl(root_name, group_name, suffix, prefix_map):
    """Core build logic (no undo-chunk wrapping, no kwarg shuffling).

    Pipeline:
        1. Pre-build conflict clear + orphan sweep.
        2. Create root joint + holder group.
        3. Per module: ``collect_module_deform_joints`` ->
           ``build_module_topology`` -> create jUE for every pair ->
           intra-module reparent.
        4. Cross-module reparent: each module's jUE root attaches to
           the parent module's spatially nearest jUE (or to the root
           jUE when the guide root has no joint ancestor in another
           module).
        5. Constraint drivers per (def -> jUE).
    """
    # -------- 1) Conflict pre-check ----------------------------------------
    if cmds.objExists(group_name):
        log.warning(
            "UE skeleton build: pre-clearing existing %r "
            "(leftover from previous run)" % group_name
        )
        _delete_ue_skeleton_impl(group_name)

    orphans_cleaned = 0
    orphans_kept = 0
    for j in cmds.ls("*" + suffix, type="joint", long=True) or []:
        parents = cmds.listRelatives(j, parent=True, fullPath=True) or []
        parent_short = _short(parents[0]) if parents else None
        if parent_short in (group_name, root_name):
            continue
        try:
            conns = cmds.listConnections(
                j, source=True, destination=True, skipConversionNodes=True
            ) or []
            conns = [c for c in conns if not c.startswith("default")]
        except RuntimeError:
            conns = []
        if conns:
            log.warning(
                "UE skeleton build: orphan %s has %d connection(s); "
                "left in place" % (_short(j), len(conns))
            )
            orphans_kept += 1
            continue
        try:
            cmds.delete(j)
            orphans_cleaned += 1
        except RuntimeError as exc:
            log.warning(
                "UE skeleton build: cleaning orphan %s failed: %s" %
                (_short(j), exc)
            )
    if orphans_cleaned or orphans_kept:
        log.info(
            "UE skeleton build: orphan sweep -- %d cleaned, %d kept" %
            (orphans_cleaned, orphans_kept)
        )

    # -------- 2) Root group + root joint -----------------------------------
    group = cmds.group(empty=True, name=group_name)
    cmds.select(clear=True)
    ue_root = cmds.joint(name=root_name)
    ue_root_short = _short(ue_root)
    cmds.parent(ue_root_short, group)
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % ue_root_short, 0)
    except RuntimeError:
        pass

    # -------- 3) Per-module intra-chain ------------------------------------
    from ddrig.base import initials
    try:
        scene_roots = initials.Initials().get_scene_roots()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "UE skeleton build: failed to enumerate scene roots: %s" % exc
        )

    per_module = {}   # module_name -> {"jue_map", "roots", "module_type"}
    created = []
    all_warnings = []
    jdef_to_jue = {}   # def short -> jUE short (legacy consumer: skin_swap)

    for item in scene_roots:
        mn = item["module_name"]
        mtype = item["module_type"]
        all_defs = collect_module_deform_joints(mn)
        if not all_defs:
            log.info("UE skeleton build: %s has no deform joints, skipping"
                     % mn)
            continue

        chain, warnings = build_module_topology(
            mn, mtype, all_defs, prefix_map=prefix_map
        )
        for w in warnings:
            all_warnings.append("[%s] %s" % (mn, w))
        if not chain:
            log.warning(
                "UE skeleton build: %s produced 0 topology pairs "
                "(check module_type=%r rules and naming)" % (mn, mtype)
            )
            continue

        jue_map = {}
        module_roots = []

        # 3a) Create every jUE at world-space position.
        for (child_def, _parent_def) in chain:
            jue_name = _def_to_jue_name(child_def, suffix)
            if cmds.objExists(jue_name):
                log.warning(
                    "UE skeleton build: skipping %r (name collision)"
                    % jue_name
                )
                continue
            try:
                jue_short = _create_jue_from_def(child_def, jue_name)
            except RuntimeError as exc:
                log.warning(
                    "UE skeleton build: create %r from %s failed: %s"
                    % (jue_name, child_def, exc)
                )
                continue
            jue_map[child_def] = jue_short
            jdef_to_jue[_short(child_def)] = jue_short
            created.append(jue_short)

        # 3b) Intra-module parenting now that every jUE exists.
        for (child_def, parent_def) in chain:
            child_jue = jue_map.get(child_def)
            if not child_jue:
                continue
            if parent_def is None:
                module_roots.append(child_jue)
                continue
            parent_jue = jue_map.get(parent_def)
            if not parent_jue:
                # Parent def was skipped (name collision / create fail)
                # -- fall back to treating this jUE as a module root.
                module_roots.append(child_jue)
                continue
            try:
                cmds.parent(child_jue, parent_jue)
            except RuntimeError as exc:
                log.warning(
                    "UE skeleton build: parent %s under %s failed: %s"
                    % (child_jue, parent_jue, exc)
                )

        # 3c) Constraint drivers.
        for (child_def, _parent_def) in chain:
            jue = jue_map.get(child_def)
            if jue:
                _mirror_animation(child_def, jue)

        per_module[mn] = {
            "jue_map": jue_map,
            "roots": module_roots,
            "module_type": mtype,
        }
        log.info("UE skeleton build: %s (%s): %d jUE joints"
                 % (mn, mtype, len(jue_map)))

    # -------- 4) Cross-module attachment -----------------------------------
    for mn, data in per_module.items():
        info = next((r for r in scene_roots if r["module_name"] == mn), None)
        if not info:
            continue
        guide_root = info["root_joint"]
        try:
            guide_parent = cmds.listRelatives(
                guide_root, parent=True, type="joint", fullPath=True
            ) or []
        except RuntimeError:
            guide_parent = []

        attach_target = ue_root_short
        if guide_parent:
            # Walk up the guide parent chain until we hit another
            # module's root_joint -- that module owns our attach point.
            parent_mn = _find_owning_module_by_guide(
                guide_parent[0], scene_roots
            )
            if parent_mn and parent_mn in per_module and parent_mn != mn:
                parent_data = per_module[parent_mn]
                pick = _pick_cross_module_attach(
                    data["roots"], list(parent_data["jue_map"].values())
                )
                if pick:
                    attach_target = pick

        for jue_root in data["roots"]:
            if not cmds.objExists(jue_root):
                continue
            try:
                current = cmds.listRelatives(
                    jue_root, parent=True, fullPath=False
                ) or []
                if current and current[0] == attach_target:
                    continue
                # Capture world transform BEFORE reparent -- the
                # reparent itself recomputes local channels and can
                # introduce scale residue if the new parent has
                # non-identity inherited scale.
                try:
                    pre_t = cmds.xform(
                        jue_root, query=True, worldSpace=True, translation=True
                    )
                    pre_ro = cmds.xform(
                        jue_root, query=True, worldSpace=True, rotation=True
                    )
                except RuntimeError:
                    pre_t = pre_ro = None

                cmds.parent(jue_root, attach_target)

                # Force local scale back to 1 and re-apply world
                # translation + rotation so the visual position is
                # unchanged but the local channels are clean.
                for ch in ("scaleX", "scaleY", "scaleZ"):
                    try:
                        cmds.setAttr("%s.%s" % (jue_root, ch), 1.0)
                    except RuntimeError:
                        pass
                if pre_t is not None:
                    try:
                        cmds.xform(
                            jue_root, worldSpace=True, translation=pre_t
                        )
                    except RuntimeError:
                        pass
                if pre_ro is not None:
                    try:
                        cmds.xform(
                            jue_root, worldSpace=True, rotation=pre_ro
                        )
                    except RuntimeError:
                        pass
            except RuntimeError as exc:
                log.warning(
                    "UE skeleton build: cross-module parent %s under %s "
                    "failed: %s" % (jue_root, attach_target, exc)
                )
        log.info("UE skeleton build: %s attached under %s" %
                 (mn, attach_target))

    if all_warnings:
        log.info("UE skeleton build: %d warning(s)" % len(all_warnings))
        for w in all_warnings:
            log.warning("  %s" % w)

    return {
        "root": ue_root_short,
        "group": group,
        "created": created,
        "per_module": per_module,
        "jdef_to_jue": jdef_to_jue,
        "warnings": all_warnings,
    }


def build_ue_skeleton(root_name=UE_ROOT, group_name=UE_GROUP,
                      suffix=UE_SUFFIX, prefix_map=None):
    """Public entry: build the UE skeleton inside a single undo chunk."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_build_skeleton")
    try:
        return _build_ue_skeleton_impl(
            root_name=root_name,
            group_name=group_name,
            suffix=suffix,
            prefix_map=prefix_map,
        )
    finally:
        cmds.undoInfo(closeChunk=True)


def rebuild_ue_skeleton(root_name=UE_ROOT, group_name=UE_GROUP,
                        suffix=UE_SUFFIX, prefix_map=None):
    """Delete-then-build inside a single undo chunk."""
    cmds.undoInfo(openChunk=True, chunkName="ddrig_ue_rebuild_skeleton")
    try:
        _delete_ue_skeleton_impl(group_name)
        return _build_ue_skeleton_impl(
            root_name=root_name,
            group_name=group_name,
            suffix=suffix,
            prefix_map=prefix_map,
        )
    finally:
        cmds.undoInfo(closeChunk=True)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def module_status_snapshot():
    """Return per-module status dicts for UI previewing.  Safe when no
    guides exist -- returns []."""
    try:
        from ddrig.base import initials
        scene_roots = initials.Initials().get_scene_roots()
    except Exception:   # noqa: BLE001
        return []
    out = []
    for info in scene_roots:
        mn = info["module_name"]
        guide_root = info["root_joint"]
        try:
            descendants = cmds.listRelatives(
                guide_root, allDescendents=True, type="joint"
            ) or []
        except RuntimeError:
            descendants = []
        guide_count = 1 + len(descendants)
        deforms = collect_module_deform_joints(mn)
        out.append({
            "module_name": mn,
            "module_type": info.get("module_type", ""),
            "side": info.get("side", ""),
            "root_joint": guide_root,
            "guide_count": guide_count,
            "has_rig": len(deforms) > 0,
            "deform_count": len(deforms),
            # Legacy alias.
            "jdef_count": len(deforms),
        })
    return out


def detection_report():
    """Return per-module detection summaries for the UI's Dump
    Detection Report button.  Each entry::

        {
            "module_name":  str,
            "module_type":  str,
            "has_rig":      bool,
            "guide_count":  int,
            "deforms":      [fullpath, ...],
        }
    """
    try:
        from ddrig.base import initials
        scene_roots = initials.Initials().get_scene_roots()
    except Exception:   # noqa: BLE001
        return []
    out = []
    for info in scene_roots:
        mn = info["module_name"]
        guide_root = info["root_joint"]
        try:
            descendants = cmds.listRelatives(
                guide_root, allDescendents=True, type="joint"
            ) or []
        except RuntimeError:
            descendants = []
        guide_count = 1 + len(descendants)
        deforms = collect_module_deform_joints(mn)
        out.append({
            "module_name": mn,
            "module_type": info.get("module_type", ""),
            "has_rig": len(deforms) > 0,
            "guide_count": guide_count,
            "deforms": list(deforms),
        })
    return out
