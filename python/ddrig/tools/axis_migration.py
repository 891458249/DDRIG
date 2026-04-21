"""Axis-convention migration utilities for DDRIG guide joints.

The DDRIG default `lookAxis` was changed from `(0, 0, 1)` (+Z forward) to
`(1, 0, 0)` (+X forward). Scenes built before that change carry the old
value on every guide-root joint. This module provides a scene-walker that
updates the three `lookAxisX/Y/Z` float attributes in place while leaving
user-customised values untouched.

Only `lookAxis` is migrated. `upAxis` and `mirrorAxis` defaults did NOT
change and do not need rewriting.

Typical use from the Maya Script Editor::

    from ddrig.tools import axis_migration
    migrated, skipped = axis_migration.migrate_look_axis_default(dry_run=True)

Review the dry-run output, then re-run with `dry_run=False` to apply.
"""
from __future__ import annotations

from maya import cmds

NEW_DEFAULT = (1.0, 0.0, 0.0)
OLD_DEFAULT = (0.0, 0.0, 1.0)
_TOL = 1e-6


def _get_look_axis(joint):
    """Return the current lookAxis tuple of a joint, or None if the
    attribute does not exist."""
    if not cmds.attributeQuery("lookAxis", node=joint, exists=True):
        return None
    return tuple(
        cmds.getAttr("%s.lookAxis%s" % (joint, axis)) for axis in "XYZ"
    )


def _equals(a, b, tol=_TOL):
    """Compare two length-3 tuples with float tolerance."""
    return all(abs(x - y) < tol for x, y in zip(a, b))


def _set_look_axis(joint, target):
    """Write target tuple to the joint's lookAxisX/Y/Z attributes."""
    for nmb, axis in enumerate("XYZ"):
        cmds.setAttr("%s.lookAxis%s" % (joint, axis), target[nmb])


def migrate_look_axis_default(dry_run=True, only_if_old_default=True):
    """Scan the current Maya scene and update each guide joint's lookAxis
    from the old DDRIG default ``(0, 0, 1)`` to the new default
    ``(1, 0, 0)``.

    Args:
        dry_run: If True (default), prints the list of joints that WOULD be
            migrated but makes no changes to the scene. Pass False only
            after reviewing the dry-run output.
        only_if_old_default: If True (default), only joints whose current
            lookAxis is exactly ``(0, 0, 1)`` (within float tolerance) are
            migrated. Other joints — including user-customised values and
            joints already on the new default — are skipped. If False, the
            function overwrites every discovered lookAxis with the new
            default; this is destructive and should only be used when the
            caller knows what they are doing.

    Returns:
        Two lists ``(migrated, skipped)``. Each element is a tuple
        ``(joint_name, old_look_axis, reason_or_new_value)``.
    """
    migrated = []
    skipped = []

    all_joints = cmds.ls(type="joint", long=False) or []
    guide_joints = [
        j for j in all_joints
        if cmds.attributeQuery("lookAxis", node=j, exists=True)
    ]

    mode_label = "DRY RUN" if dry_run else "APPLY"
    filter_label = "old-default only" if only_if_old_default else "ALL (unconditional)"
    print("=" * 70)
    print("[axis_migration] %s - filter: %s" % (mode_label, filter_label))
    print("[axis_migration] scanned joints with lookAxis attr: %d"
          % len(guide_joints))
    print("=" * 70)

    for jnt in guide_joints:
        current = _get_look_axis(jnt)
        if current is None:
            skipped.append((jnt, None, "lookAxis attr missing"))
            continue

        if only_if_old_default and not _equals(current, OLD_DEFAULT):
            skipped.append(
                (jnt, current, "not old default, preserved")
            )
            continue

        if _equals(current, NEW_DEFAULT):
            skipped.append((jnt, current, "already on new default"))
            continue

        print("  %-40s  %s  ->  %s"
              % (jnt, _fmt(current), _fmt(NEW_DEFAULT)))
        if not dry_run:
            _set_look_axis(jnt, NEW_DEFAULT)
        migrated.append((jnt, current, NEW_DEFAULT))

    print("-" * 70)
    print("[axis_migration] migrated: %d    skipped: %d"
          % (len(migrated), len(skipped)))
    if dry_run:
        print("[axis_migration] dry_run=True — NO writes performed. "
              "Re-run with dry_run=False to apply.")
    return migrated, skipped


def _fmt(axis_tuple):
    """Compact string form of an axis tuple for log output."""
    return "(%g, %g, %g)" % tuple(axis_tuple)
