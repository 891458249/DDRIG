"""Build a parallel UE-compatible skeleton from DDRIG's flat deform joints.

Overview
--------

    Flat layout (left untouched):              Hierarchical UE copy (new):
    ---------------------------                -------------------------------
    ddrig_grp/                                 ue_skeleton_grp/
      L_arm_grp/                                 root_jUE
        L_arm_defJoints_grp/                       C_spine_pelvis_jUE
          L_arm_collar_jDef                          C_spine_0_jUE
          L_arm_up_0_jDef                              ...
          ...                                           L_arm_collar_jUE
          L_arm_hand_jDef                                 L_arm_up_0_jUE
                                                            ...
                                                              L_arm_hand_jUE

Each ``_jUE`` carries a parent + scale constraint from its source ``_jDef``
so animation plays through identically.  ``segmentScaleCompensate`` is
forced off (UE is not compatible with Maya's default True).

Algorithm summary
-----------------

1. Collect modules via ``Initials.get_scene_roots`` plus, per module,
   every ``_jDef`` under ``{module_name}_defJoints_grp``.
2. Bucket each ``_jDef`` to its closest guide joint by world distance.
3. Flatten buckets into one jDef chain per module (depth-first over
   guide chain; within each bucket sort along the prev->next guide
   axis when possible).
4. Duplicate each jDef as a jUE, chaining them inside the module.
5. Walk the guide's DAG-parent chain to find each module's "attach to
   this other module" relationship; parent each module's head jUE to
   the closest jUE in that other module's chain.  Top-level modules
   (whose guide root has no joint ancestor) parent to ``root_jUE``.
6. Add parent + scale constraint (source ``_jDef`` -> target ``_jUE``).

Public API
----------

``build_ue_skeleton``, ``rebuild_ue_skeleton``, ``delete_ue_skeleton``
"""
from __future__ import annotations

from maya import cmds
import maya.api.OpenMaya as om

from ddrig.core import filelog

log = filelog.Filelog(logname=__name__, filename="ddrig_log")


UE_SUFFIX = "_jUE"
JDEF_SUFFIX = "_jDef"
UE_GROUP = "ue_skeleton_grp"
UE_ROOT = "root_jUE"
_SOURCE_ATTR = "sourceJDef"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _short(name):
    """Strip any DAG path to the leaf short name."""
    return name.rsplit("|", 1)[-1] if name else name


def _world_pos(node):
    """Return world translation of ``node`` as an MVector."""
    t = cmds.xform(node, query=True, worldSpace=True, translation=True)
    return om.MVector(t[0], t[1], t[2])


def _find_parent_joint(node):
    """Walk up the DAG from ``node`` and return the first joint ancestor
    (short name).  Returns ``None`` if no joint ancestor exists -- that
    indicates a top-level module."""
    parent = cmds.listRelatives(node, parent=True, fullPath=True)
    while parent:
        p = parent[0]
        if cmds.nodeType(p) == "joint":
            return _short(p)
        parent = cmds.listRelatives(p, parent=True, fullPath=True)
    return None


def _jdef_to_jue_name(jdef_short, suffix):
    """Map a ``*_jDef`` short name to its ``*_jUE`` counterpart."""
    jdef_short = _short(jdef_short)
    if jdef_short.endswith(JDEF_SUFFIX):
        return jdef_short[:-len(JDEF_SUFFIX)] + suffix
    return jdef_short + suffix


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

def _collect_modules():
    """Return a list of per-module dicts containing everything the
    build pipeline needs:

        {
            "info": <get_scene_roots entry>,
            "guide_root": <short name>,
            "guide_chain": [guide_root, ...depth-first descendants],
            "jdefs": [<short name>, ...],
        }
    """
    # Lazy import: ddrig.base.initials cascades into Qt.  Deferring to
    # call time keeps ``ddrig.tools.ue_skeleton.builder`` import-clean in
    # environments that do not yet have Qt available (e.g. hot-reload).
    from ddrig.base import initials
    init = initials.Initials()
    scene_roots = init.get_scene_roots()
    if not scene_roots:
        raise RuntimeError(
            "No DDRIG guide roots found in the scene. Build a rig first."
        )

    modules = []
    for info in scene_roots:
        guide_root = info["root_joint"]
        module_name = info["module_name"]
        descendants = cmds.listRelatives(
            guide_root, allDescendents=True, type="joint", fullPath=False
        ) or []
        guide_chain = [guide_root] + list(descendants)

        # Harvest jDefs from this module's defJoints_grp.
        def_grp = "%s_defJoints_grp" % module_name
        jdefs = []
        if cmds.objExists(def_grp):
            under = cmds.listRelatives(
                def_grp, allDescendents=True, type="joint", fullPath=False
            ) or []
            jdefs = [j for j in under if j.endswith(JDEF_SUFFIX)]

        modules.append({
            "info": info,
            "guide_root": guide_root,
            "guide_chain": guide_chain,
            "jdefs": jdefs,
        })
    return modules


def _find_module_of_guide(guide_short, modules):
    """Return the module_name whose guide_chain contains ``guide_short``,
    or ``None``."""
    for m in modules:
        if guide_short in m["guide_chain"]:
            return m["info"]["module_name"]
    return None


# ---------------------------------------------------------------------------
# jDef -> guide bucket assignment + chain flattening
# ---------------------------------------------------------------------------

def _bucket_jdefs_by_closest_guide(jdefs, guide_chain):
    """Assign each jDef to its closest guide (world-space Euclidean).

    Returns ``{guide_short: [(distance, jdef_short), ...]}`` with each
    bucket sorted by distance ascending.  A guide with no jDefs in it
    still appears in the dict with an empty list.
    """
    bucket = {g: [] for g in guide_chain}
    if not jdefs or not guide_chain:
        return bucket
    guide_positions = {g: _world_pos(g) for g in guide_chain}
    for jdef in jdefs:
        jp = _world_pos(jdef)
        best_g, best_d = None, float("inf")
        for g, gp in guide_positions.items():
            d = (gp - jp).length()
            if d < best_d:
                best_d, best_g = d, g
        if best_g is not None:
            bucket[best_g].append((best_d, jdef))
    for g in bucket:
        bucket[g].sort(key=lambda t: t[0])
    return bucket


def _order_bucket_along_axis(bucket_entries, prev_guide, current_guide, next_guide):
    """Sort a bucket's jDefs by projection onto the axis connecting
    prev_guide -> next_guide (or any adjacent pair we have).  Falls back
    to the pre-computed distance-to-current-guide order when the axis
    is degenerate (zero length)."""
    if not bucket_entries:
        return []
    if prev_guide and next_guide:
        p0 = _world_pos(prev_guide)
        p1 = _world_pos(next_guide)
    elif prev_guide:
        p0 = _world_pos(prev_guide)
        p1 = _world_pos(current_guide)
    elif next_guide:
        p0 = _world_pos(current_guide)
        p1 = _world_pos(next_guide)
    else:
        # Sole guide in chain -- keep the precomputed distance order.
        return [jd for _, jd in bucket_entries]

    axis = p1 - p0
    length = axis.length()
    if length < 1e-6:
        return [jd for _, jd in bucket_entries]
    axis = axis / length

    def projection(jdef):
        return (_world_pos(jdef) - p0) * axis

    return sorted((jd for _, jd in bucket_entries), key=projection)


def _flatten_module_chain(jdefs, guide_chain):
    """Produce the ordered jDef chain for a module by sweeping through
    the guide chain in order and laying each guide's bucket along the
    previous->next guide axis."""
    bucket = _bucket_jdefs_by_closest_guide(jdefs, guide_chain)
    chain = []
    for i, g in enumerate(guide_chain):
        prev_g = guide_chain[i - 1] if i > 0 else None
        next_g = guide_chain[i + 1] if i + 1 < len(guide_chain) else None
        ordered = _order_bucket_along_axis(bucket[g], prev_g, g, next_g)
        chain.extend(ordered)
    return chain


# ---------------------------------------------------------------------------
# jUE node creation
# ---------------------------------------------------------------------------

def _create_jue_joint(jdef, jue_name, parent_jue=None):
    """Duplicate a jDef as a fresh joint (no shapes, no children, no
    incoming connections), optionally parent under ``parent_jue``, force
    segmentScaleCompensate off, and tag the sourceJDef attribute for
    later skin-swap lookup.

    Returns the actual short name Maya assigned (may be uniquified)."""
    cmds.select(clear=True)
    dup = cmds.duplicate(jdef, parentOnly=True, name=jue_name)[0]
    dup_short = _short(dup)
    # Strip any residual parent from the duplicate (duplicate keeps the
    # source's parent by default).
    try:
        cmds.parent(dup_short, world=True)
    except RuntimeError:
        pass
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % dup_short, 0)
    except RuntimeError:
        pass
    # Record the source jDef on the jUE so skin-swap can map back.
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
    """Attach a parent + scale constraint from ``jdef`` to ``jue`` so
    the UE joint follows the DDRIG deform joint at every frame."""
    try:
        cmds.parentConstraint(jdef, jue, maintainOffset=False)
    except RuntimeError as exc:
        log.warning("parentConstraint(%s -> %s) failed: %s" % (jdef, jue, exc))
        return False
    try:
        cmds.scaleConstraint(jdef, jue, maintainOffset=False)
    except RuntimeError:
        # parent-only is still usable; log and keep going.
        pass
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def delete_ue_skeleton(group_name=UE_GROUP):
    """Remove the UE skeleton group and everything under it.

    Constraints that drive the jUE joints live on the jUE nodes themselves,
    so deleting the group tears them down with it.  Source jDefs are
    untouched.

    Returns:
        bool: True if something was removed, False if the group did not
        exist.
    """
    if cmds.objExists(group_name):
        cmds.delete(group_name)
        return True
    return False


def build_ue_skeleton(root_name=UE_ROOT, group_name=UE_GROUP, suffix=UE_SUFFIX):
    """Build a parallel UE-compatible skeleton mirroring all ``_jDef``
    deform joints in the scene.

    See the module docstring for the algorithm.  Refuses to run if
    ``group_name`` already exists; call :func:`rebuild_ue_skeleton` to
    wipe and rebuild.

    Returns:
        dict with keys:
            root             -- the ``root_jUE`` joint short name.
            group            -- the ``ue_skeleton_grp`` transform.
            created          -- list of jUE short names, in creation order.
            skipped          -- list of ``(item, reason)`` tuples.
            module_chains    -- ``{module_name: [jue_short, ...]}``.
            jdef_to_jue      -- ``{jdef_short: jue_short}``.

    Raises:
        RuntimeError: if no guide roots exist (no DDRIG rig in scene) or
            the target group name is already taken.
    """
    if cmds.objExists(group_name):
        raise RuntimeError(
            "%r already exists. Use rebuild_ue_skeleton() or "
            "delete_ue_skeleton() first." % group_name
        )

    modules = _collect_modules()

    # Pre-create the holder group + root joint.
    group = cmds.group(empty=True, name=group_name)
    cmds.select(clear=True)
    ue_root = cmds.joint(name=root_name)
    ue_root_short = _short(ue_root)
    cmds.parent(ue_root_short, group)
    try:
        cmds.setAttr("%s.segmentScaleCompensate" % ue_root_short, 0)
    except RuntimeError:
        pass

    module_chains = {}   # module_name -> [jue short names in chain order]
    jdef_to_jue = {}     # jdef short -> jue short
    jue_to_jdef = {}     # jue short  -> jdef short  (for pass 3 constraint)
    created = []
    skipped = []

    # Pass 1: per-module intra-chain (linear jUE chain, no cross-module
    # parenting yet).
    for m in modules:
        mod_name = m["info"]["module_name"]
        chain_jdefs = _flatten_module_chain(m["jdefs"], m["guide_chain"])
        if not chain_jdefs:
            skipped.append((mod_name, "no jDefs found under %s_defJoints_grp"
                            % mod_name))
            module_chains[mod_name] = []
            continue
        chain_jues = []
        prev_jue = None
        for jdef in chain_jdefs:
            jue_target_name = _jdef_to_jue_name(jdef, suffix)
            if cmds.objExists(jue_target_name):
                skipped.append((jue_target_name, "name already taken in scene"))
                # Link via existing anyway? Safer to skip the whole node.
                continue
            jue_short = _create_jue_joint(jdef, jue_target_name, prev_jue)
            jdef_to_jue[jdef] = jue_short
            jue_to_jdef[jue_short] = jdef
            chain_jues.append(jue_short)
            created.append(jue_short)
            prev_jue = jue_short
        module_chains[mod_name] = chain_jues

    # Pass 2: cross-module parenting by guide topology.
    for m in modules:
        mod_name = m["info"]["module_name"]
        chain = module_chains.get(mod_name, [])
        if not chain:
            continue
        head = chain[0]
        guide_root = m["guide_root"]
        parent_guide_short = _find_parent_joint(guide_root)
        if parent_guide_short is None:
            # Top-level module -- parent to root_jUE directly.
            try:
                cmds.parent(head, ue_root_short)
            except RuntimeError as exc:
                skipped.append((head, "parent to root failed: %s" % exc))
            continue
        parent_module_name = _find_module_of_guide(parent_guide_short, modules)
        if parent_module_name is None:
            log.warning(
                "UE skeleton: cannot resolve parent module for guide %r "
                "(from module %r); attaching head to root." %
                (parent_guide_short, mod_name)
            )
            try:
                cmds.parent(head, ue_root_short)
            except RuntimeError:
                pass
            continue
        parent_chain = module_chains.get(parent_module_name, [])
        if not parent_chain:
            log.warning(
                "UE skeleton: parent module %r has no jUE chain; "
                "attaching %r head to root." % (parent_module_name, mod_name)
            )
            try:
                cmds.parent(head, ue_root_short)
            except RuntimeError:
                pass
            continue
        # Closest jUE in the parent chain by world distance.
        head_pos = _world_pos(head)
        best_parent = min(
            parent_chain,
            key=lambda j: (_world_pos(j) - head_pos).length(),
        )
        try:
            cmds.parent(head, best_parent)
        except RuntimeError as exc:
            log.warning(
                "UE skeleton: parent %r under %r failed: %s" %
                (head, best_parent, exc)
            )
            skipped.append((head, "reparent failed: %s" % exc))

    # Pass 3: add driving constraints last, so hierarchy is settled.
    for jue_short, jdef in jue_to_jdef.items():
        _mirror_animation(jdef, jue_short)

    return {
        "root": ue_root_short,
        "group": group,
        "created": created,
        "skipped": skipped,
        "module_chains": module_chains,
        "jdef_to_jue": jdef_to_jue,
    }


def rebuild_ue_skeleton(root_name=UE_ROOT, group_name=UE_GROUP, suffix=UE_SUFFIX):
    """Delete the existing UE skeleton (if any) and rebuild from scratch.

    Idempotent wrapper around :func:`build_ue_skeleton`."""
    delete_ue_skeleton(group_name=group_name)
    return build_ue_skeleton(
        root_name=root_name, group_name=group_name, suffix=suffix
    )
