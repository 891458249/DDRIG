"""Transfer skin weights from DDRIG's ``_jDef`` influences to the
parallel ``_jUE`` influences produced by :mod:`ddrig.tools.ue_skeleton.builder`.

The mapping jDef -> jUE is carried on each jUE as a string attribute
``sourceJDef`` (set by :func:`_create_jue_joint`).  This is more
robust than deriving the jue name from the jdef name because Maya
may have uniquified duplicates.

Policy
------

    * ``_jUE`` is added as an influence (weight 0) if not already in
      the skinCluster.
    * For every vertex with non-zero weight on the jDef, that weight
      is transferred to the jUE and the jDef is set to 0 at that
      vertex.
    * ``_jDef`` is NOT removed from the cluster by default.  Keeping
      both with jDef at zero means the original rig still drives the
      mesh correctly when the user re-binds or un-transfers, and the
      FBX exporter only picks up nodes under ``ue_skeleton_grp`` anyway.
    * ``dry_run=True`` prints the plan without touching any weights.
"""
from __future__ import annotations

from maya import cmds

from ddrig.core import filelog
from ddrig.tools.ue_skeleton.builder import JDEF_SUFFIX, UE_SUFFIX

log = filelog.Filelog(logname=__name__, filename="ddrig_log")

_SOURCE_ATTR = "sourceJDef"


def _short(name):
    return name.rsplit("|", 1)[-1] if name else name


def _build_jdef_to_jue_map():
    """Scan every ``*_jUE`` joint in the scene and build a reverse map
    from the ``sourceJDef`` attribute.  Falls back to name-based
    derivation (strip ``_jDef``, append ``_jUE``) for any jUE missing
    the attr -- legacy / hand-created skeletons."""
    mapping = {}
    ue_candidates = cmds.ls("*" + UE_SUFFIX, type="joint") or []
    for jue in ue_candidates:
        jue_short = _short(jue)
        if cmds.attributeQuery(_SOURCE_ATTR, node=jue_short, exists=True):
            jdef = cmds.getAttr("%s.%s" % (jue_short, _SOURCE_ATTR))
            if jdef:
                mapping[_short(jdef)] = jue_short
        else:
            # Fallback: reverse the naming convention.
            if jue_short.endswith(UE_SUFFIX):
                mapping[jue_short[:-len(UE_SUFFIX)] + JDEF_SUFFIX] = jue_short
    return mapping


def _skincluster_geometry(sc):
    """Return the connected shape node for a skinCluster, or None."""
    geo = cmds.skinCluster(sc, query=True, geometry=True) or []
    return _short(geo[0]) if geo else None


def _vertex_count(shape):
    """Return vertex count for a mesh shape (0 if not a polygon)."""
    if cmds.nodeType(shape) != "mesh":
        return 0
    try:
        return cmds.polyEvaluate(shape, vertex=True)
    except RuntimeError:
        return 0


def transfer_skin_to_ue(remove_jdef_influence=False, dry_run=False):
    """Copy skin weights from every ``_jDef`` influence to the
    corresponding ``_jUE`` influence.

    Args:
        remove_jdef_influence: If True, remove the ``_jDef`` from the
            skinCluster after weights have been zeroed out there.  Off
            by default -- keeping both influences (jDef at zero) is
            safer for round-tripping and has no runtime cost.
        dry_run: If True, scan and report the planned work without
            modifying any skinCluster or weight.

    Returns:
        dict: ``{
            "skinclusters_processed": int,
            "influences_added": int,
            "influences_removed": int,
            "vertices_touched": int,
            "warnings": [str, ...],
        }``
    """
    jdef_to_jue = _build_jdef_to_jue_map()
    if not jdef_to_jue:
        return {
            "skinclusters_processed": 0,
            "influences_added": 0,
            "influences_removed": 0,
            "vertices_touched": 0,
            "warnings": [
                "No *_jUE joints found. Run build_ue_skeleton first."
            ],
        }

    warnings = []
    scs_processed = 0
    inf_added = 0
    inf_removed = 0
    vtx_touched = 0

    for sc in cmds.ls(type="skinCluster") or []:
        influences = cmds.skinCluster(sc, query=True, influence=True) or []
        inf_short = [_short(i) for i in influences]
        jdef_infls = [i for i in inf_short if i.endswith(JDEF_SUFFIX)]
        if not jdef_infls:
            continue

        shape = _skincluster_geometry(sc)
        if not shape:
            warnings.append(
                "%s: no geometry connection; skipping." % sc
            )
            continue
        vtx_count = _vertex_count(shape)
        if not vtx_count:
            warnings.append(
                "%s: shape %s has no vertex count; skipping." % (sc, shape)
            )
            continue

        # Build jdef -> jue pairs for this cluster.
        pairs = []
        for jdef in jdef_infls:
            jue = jdef_to_jue.get(jdef)
            if not jue or not cmds.objExists(jue):
                warnings.append(
                    "%s: no jUE counterpart for %s; skipping that influence."
                    % (sc, jdef)
                )
                continue
            pairs.append((jdef, jue))

        if not pairs:
            warnings.append(
                "%s: no jDef->jUE pair resolved; skipping cluster." % sc
            )
            continue

        if dry_run:
            log.info(
                "[dry-run] %s on %s (%d verts): %d jdef->jue pairs" %
                (sc, shape, vtx_count, len(pairs))
            )
            scs_processed += 1
            continue

        # Ensure every jUE is an influence on this cluster (weight 0).
        for jdef, jue in pairs:
            if jue not in inf_short:
                try:
                    cmds.skinCluster(
                        sc, edit=True, addInfluence=jue, weight=0.0
                    )
                    inf_added += 1
                    inf_short.append(jue)
                except RuntimeError as exc:
                    warnings.append(
                        "%s: addInfluence %s failed: %s" % (sc, jue, exc)
                    )

        # Weight transfer: per jdef, batch-query per-vertex weights, then
        # per-vertex setValue pairs (jue = w, jdef = 0) only where w > 0.
        vrange = "%s.vtx[0:%d]" % (shape, vtx_count - 1)
        for jdef, jue in pairs:
            try:
                per_vtx = cmds.skinPercent(
                    sc, vrange, query=True, transform=jdef
                )
            except RuntimeError as exc:
                warnings.append(
                    "%s: query weights for %s failed: %s" % (sc, jdef, exc)
                )
                continue
            if isinstance(per_vtx, (int, float)):
                per_vtx = [float(per_vtx)]
            for i, w in enumerate(per_vtx):
                if w and w > 1e-6:
                    try:
                        cmds.skinPercent(
                            sc, "%s.vtx[%d]" % (shape, i),
                            transformValue=[(jue, w), (jdef, 0.0)],
                        )
                        vtx_touched += 1
                    except RuntimeError:
                        # Locked influences etc. -- accumulate once per
                        # jdef, not once per vertex.
                        pass

        if remove_jdef_influence:
            for jdef, _ in pairs:
                try:
                    cmds.skinCluster(
                        sc, edit=True, removeInfluence=jdef
                    )
                    inf_removed += 1
                except RuntimeError as exc:
                    warnings.append(
                        "%s: removeInfluence %s failed: %s" % (sc, jdef, exc)
                    )

        scs_processed += 1

    return {
        "skinclusters_processed": scs_processed,
        "influences_added": inf_added,
        "influences_removed": inf_removed,
        "vertices_touched": vtx_touched,
        "warnings": warnings,
    }
