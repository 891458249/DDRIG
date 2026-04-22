"""Hot install / uninstall / reload for the DDRIG plugin — no Maya restart.

Entry points
------------
    install(source_root=None, with_shelf=False, silent=False)
    uninstall(keep_mod_file=False, flush_undo=True)
    reload_in_place()
    reinstall(source_root=None, with_shelf=False)

Scope of cleanup on uninstall
-----------------------------
    - Maya main-window menu   "DDRIG_widget"
    - Shelf                   "ddrig"                (if present)
    - Six known Qt windows    (DDRIG main UI, Tool dock, Make-up, Mocap
                               Mapper, Face Mocap, Noise Expressions,
                               Blendshape Transfer) plus a DDRIG* prefix
                               sweep as fallback
    - One workspaceControl    "ddrigTools"           (wand dock)
    - sys.modules             every key matching ``ddrig`` / ``ddrig.*``
    - sys.path                every entry containing the DDRIG root
    - ~/maya/modules/ddrig.mod file
    - cmds.flushUndo()        to drop undo-chunk references to rig nodes

Intentionally NOT touched
-------------------------
    - Scene nodes (guide joints, rigs, locators) — user data. If you need
      to wipe the scene, call ``cmds.file(new=True, force=True)`` first.
    - sys.modules key ``ddrig_setup`` — that module lives under
      python/maya_modules/shelves_module/scripts and is imported by Maya's
      userSetup.py on launch; leaving it in place keeps the next Maya
      boot working.
    - scriptJobs registered by running DDRIG UIs — those are cleared by
      each window's own close() callback when the UI is closed above.

Layout assumptions
------------------
    python/
      dragAndDropMe.py
      maya_modules/
        ddrig.mod
        shelves_module/
          scripts/ddrig_setup.py
          shelves/shelf_ddrig.mel
      ddrig/                      <- the Python package
        tools/hotswap.py          <- this file

``sys.path`` needs the `python/` directory (parent of `ddrig/`) — that is
what ``ddrig_setup.add_python_path()`` inserts.
"""
from __future__ import annotations

import os
import sys

from maya import cmds


# ---------------------------------------------------------------------------
# Known UI surface area
# ---------------------------------------------------------------------------

# Qt objectName values used by DDRIG UI modules. Harvested from grep on
# WINDOW_NAME / windowName / setObjectName in python/ddrig/. Names with a
# {version} placeholder are wildcarded at runtime.
_KNOWN_QT_WINDOW_PREFIXES = (
    "DDRIG ",           # ui/main.py           -> "DDRIG {version}"
    "DDRIG Tool v",     # utils/wand/panel.py  -> "DDRIG Tool v{version}"
    "DDRIG Make-up v",  # utils/makeup.py      -> "DDRIG Make-up v0.0.2"
    "Mocap Mapper v",   # utils/mocap/ui.py    -> "Mocap Mapper v{version}"
    "Face Mocap v",     # tools/face_mocap/ui.py
    "Noise Expressions",  # tools/object_noise.py
    "Blendshape Transfer",  # utils/blendshape_transfer.py
)

_WORKSPACE_CONTROLS = ("ddrigTools",)  # utils/wand/panel.py MainUI.CONTROL_NAME
_MAYA_MENU_WIDGET = "DDRIG_widget"     # ddrig_setup.load_menu() builds this
_SHELF_NAME = "ddrig"                   # shelf_ddrig.mel's shelfLayout name
_MOD_FILENAME = "ddrig.mod"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _detect_source_root(source_root=None):
    """Return the DDRIG repository root (the directory that contains
    ``python/`` and ``maya_modules/``).

    If ``source_root`` is given it is used verbatim (after expanduser).
    Otherwise we derive it from this file's own location — this module
    lives at ``<root>/python/ddrig/tools/hotswap.py``.
    """
    if source_root:
        return os.path.abspath(os.path.expanduser(source_root))
    # Walk up: tools -> ddrig -> python -> <root>
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, os.pardir, os.pardir, os.pardir))


def _python_root(source_root):
    """Directory that must be on sys.path so ``import ddrig`` resolves."""
    return os.path.join(source_root, "python")


def _shelves_module_dir(source_root):
    """Directory the .mod file points at (the Maya 'module' payload)."""
    return os.path.join(source_root, "python", "maya_modules", "shelves_module")


def _user_mod_file():
    """~/maya/modules/ddrig.mod — where Maya discovers our module."""
    user_dir = cmds.internalVar(userAppDir=True)
    return os.path.join(user_dir, "modules", _MOD_FILENAME)


# ---------------------------------------------------------------------------
# sys.path / sys.modules manipulation
# ---------------------------------------------------------------------------

def _inject_sys_path(python_root):
    python_root = os.path.normpath(python_root)
    for entry in sys.path:
        if os.path.normpath(entry) == python_root:
            return False
    sys.path.insert(0, python_root)
    return True


def _strip_sys_path(python_root):
    python_root = os.path.normpath(python_root)
    removed = 0
    remaining = []
    for entry in sys.path:
        if os.path.normpath(entry) == python_root:
            removed += 1
            continue
        remaining.append(entry)
    sys.path[:] = remaining
    return removed


def _purge_sys_modules():
    """Drop every sys.modules entry for the ``ddrig`` package and its
    submodules. Returns the count of removed keys."""
    keys = [k for k in list(sys.modules)
            if k == "ddrig" or k.startswith("ddrig.")]
    for k in keys:
        del sys.modules[k]
    return len(keys)


# ---------------------------------------------------------------------------
# Maya UI teardown
# ---------------------------------------------------------------------------

def _close_qt_windows():
    """Close every top-level Qt widget whose objectName matches a known
    DDRIG UI. Returns the list of closed objectNames."""
    try:
        from ddrig.ui.Qt import QtWidgets
    except ImportError:
        # Package already unloaded / never loaded — there is nothing to do.
        return []
    closed = []
    for w in list(QtWidgets.QApplication.allWidgets()):
        try:
            name = w.objectName()
        except RuntimeError:
            continue
        if not name:
            continue
        if any(name.startswith(p) for p in _KNOWN_QT_WINDOW_PREFIXES):
            try:
                w.close()
                w.deleteLater()
                closed.append(name)
            except RuntimeError:
                pass
    return closed


def _delete_workspace_controls():
    deleted = []
    for ctrl in _WORKSPACE_CONTROLS:
        if cmds.workspaceControl(ctrl, exists=True):
            try:
                cmds.deleteUI(ctrl, control=True)
                deleted.append(ctrl)
            except RuntimeError:
                pass
    return deleted


def _delete_maya_menu():
    if cmds.menu(_MAYA_MENU_WIDGET, exists=True):
        cmds.deleteUI(_MAYA_MENU_WIDGET, menu=True)
        return True
    return False


def _delete_shelf():
    if cmds.shelfLayout(_SHELF_NAME, exists=True):
        cmds.deleteUI(_SHELF_NAME)
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install(source_root=None, with_shelf=False, silent=False):
    """Hot-install DDRIG without restarting Maya.

    Steps:
        1. If ``~/maya/modules/ddrig.mod`` is missing or points at a
           different path, (re)write it so next Maya launch also finds us.
        2. Prepend the ``python/`` directory to ``sys.path``.
        3. Build the "DDRIG" main-window menu by calling
           ``ddrig_setup.load_menu()``.
        4. Optionally load the ddrig shelf via ``load_shelves(reset=True)``.

    The operation is idempotent: repeat calls won't duplicate the menu.

    Args:
        source_root: DDRIG repo root. Defaults to the root this hotswap
            module itself was imported from.
        with_shelf: If True, also (re)build the DDRIG shelf layout. Off
            by default to avoid clobbering a user's customised shelf.
        silent: If True, suppress the "install complete" popup (used by
            programmatic / batch callers).

    Returns:
        dict describing what changed.
    """
    root = _detect_source_root(source_root)
    python_root = _python_root(root)
    mod_target = _shelves_module_dir(root)

    if not os.path.isdir(os.path.join(python_root, "ddrig")):
        raise RuntimeError(
            "Cannot find '%s/ddrig' — is '%s' really the DDRIG repo root?" %
            (python_root, root)
        )

    # 1. module descriptor
    mod_file = _user_mod_file()
    mod_written = False
    mod_content = "+ ddrig 0.0.1 %s" % mod_target
    if (not os.path.isfile(mod_file)
            or open(mod_file, "r").read().strip() != mod_content.strip()):
        os.makedirs(os.path.dirname(mod_file), exist_ok=True)
        with open(mod_file, "w") as f:
            f.write(mod_content)
        mod_written = True

    # 2. sys.path
    path_added = _inject_sys_path(python_root)

    # Also make sure ddrig_setup (which lives under shelves_module/scripts)
    # is importable, because load_menu() and load_shelves() live there.
    scripts_dir = os.path.join(mod_target, "scripts")
    if os.path.isdir(scripts_dir):
        for entry in sys.path:
            if os.path.normpath(entry) == os.path.normpath(scripts_dir):
                break
        else:
            sys.path.insert(0, scripts_dir)

    # 3 + 4. menu / shelf — import lazily so the path tweak above takes effect
    import ddrig_setup  # noqa: E402  (depends on sys.path mutations)
    ddrig_setup.load_menu()
    if with_shelf:
        ddrig_setup.load_shelves(reset=True)

    if not silent and not cmds.about(batch=True):
        cmds.inViewMessage(
            assistMessage="<hl>DDRIG</hl> installed — menu ready.",
            position="topCenter",
            fade=True,
        )

    return {
        "source_root": root,
        "python_root": python_root,
        "mod_file": mod_file,
        "mod_written": mod_written,
        "path_added": path_added,
        "shelf_loaded": with_shelf,
    }


def uninstall(keep_mod_file=False, flush_undo=True):
    """Hot-uninstall DDRIG without restarting Maya.

    Does NOT touch the current Maya scene's rig nodes (that is user data;
    call ``cmds.file(new=True, force=True)`` yourself if you want a clean
    scene).

    Args:
        keep_mod_file: if True, leaves ``~/maya/modules/ddrig.mod`` in
            place so Maya will auto-reload DDRIG on next launch.
        flush_undo: if True, calls ``cmds.flushUndo()`` to drop any
            references the undo stack holds to soon-to-be-stale Python
            class objects. Off is safe only if you know you have no
            DDRIG-created nodes in history.

    Returns:
        dict describing what was torn down.
    """
    closed_windows = _close_qt_windows()
    deleted_workspaces = _delete_workspace_controls()
    menu_removed = _delete_maya_menu()
    shelf_removed = _delete_shelf()

    if flush_undo:
        cmds.flushUndo()

    purged_modules = _purge_sys_modules()

    # Locate the source_root BEFORE we blow away sys.modules so we can
    # still reach hotswap's own __file__ — except we *are* hotswap, so
    # __file__ is still accessible via the current stack frame.
    root = _detect_source_root()
    python_root = _python_root(root)
    path_removed = _strip_sys_path(python_root)

    mod_file = _user_mod_file()
    mod_deleted = False
    if not keep_mod_file and os.path.isfile(mod_file):
        try:
            os.remove(mod_file)
            mod_deleted = True
        except OSError:
            pass

    return {
        "closed_windows": closed_windows,
        "deleted_workspaces": deleted_workspaces,
        "menu_removed": menu_removed,
        "shelf_removed": shelf_removed,
        "purged_modules": purged_modules,
        "path_entries_removed": path_removed,
        "mod_file_deleted": mod_deleted,
        "mod_file_kept": keep_mod_file and os.path.isfile(mod_file),
    }


def reload_in_place():
    """Drop every ``ddrig.*`` entry from sys.modules so the next import
    picks up edited source, but leave the menu / .mod / sys.path alone.

    Useful during development: edit a .py file, call this, re-run whatever
    imports the edited module. The menu items fire fresh imports each
    time so they pick up the new code without re-building the menu.
    """
    purged = _purge_sys_modules()
    return {"purged_modules": purged}


def reinstall(source_root=None, with_shelf=False):
    """uninstall + install. Use when switching to a DDRIG checkout in a
    new location, or when the menu got into a weird state."""
    teardown = uninstall(keep_mod_file=False, flush_undo=True)
    setup = install(source_root=source_root, with_shelf=with_shelf, silent=True)
    return {"uninstall": teardown, "install": setup}
