"""Drag & Drop installer for Maya 2018+.

Drop this file into a Maya viewport. The installer detects whether DDRIG
is already present (``~/maya/modules/ddrig.mod`` on disk) and offers:

    Not installed  ->  [Install]  [Cancel]
    Installed      ->  [Reinstall] [Uninstall] [Cancel]

The heavy lifting is delegated to ``ddrig.tools.hotswap`` so no Maya
restart is required in any of the three paths. If hotswap raises for
any reason, a legacy fallback still writes the .mod descriptor (install
path) or deletes it (uninstall path) so the user is never worse off
than the pre-hotswap behaviour.
"""

import os
import sys
import traceback

# confirm the maya python interpreter
CONFIRMED = False
try:
    from maya import cmds

    CONFIRMED = True
except ImportError:
    CONFIRMED = False


def onMayaDroppedPythonFile(*args, **kwargs):
    """Maya drag-and-drop entry point."""
    repo_python_dir = os.path.dirname(os.path.abspath(__file__))
    installed_at = _detect_installed()
    if installed_at:
        _prompt_installed(repo_python_dir, installed_at)
    else:
        _prompt_fresh(repo_python_dir)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _user_mod_path():
    return os.path.join(
        cmds.internalVar(userAppDir=True), "modules", "ddrig.mod"
    )


def _detect_installed():
    """Return the full path to ``~/maya/modules/ddrig.mod`` if it exists,
    else None. Existence of the .mod file is the authoritative signal
    that DDRIG is registered with Maya (regardless of whether the menu
    has been torn down in the current session)."""
    mod_path = _user_mod_path()
    return mod_path if os.path.isfile(mod_path) else None


def _current_mod_target(mod_path):
    """Return the filesystem path the .mod file currently points at,
    or None if the file cannot be parsed."""
    try:
        with open(mod_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("+ ddrig"):
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        return parts[3]
    except (OSError, UnicodeDecodeError):
        return None
    return None


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def _prompt_fresh(repo_python_dir):
    choice = cmds.confirmDialog(
        title="DDRIG — Install",
        message="Install DDRIG from:\n\n%s" % repo_python_dir,
        button=["Install", "Cancel"],
        defaultButton="Install",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    if choice == "Install":
        _do_install(repo_python_dir)


def _prompt_installed(repo_python_dir, installed_at):
    current_target = _current_mod_target(installed_at) or "(unparseable)"
    choice = cmds.confirmDialog(
        title="DDRIG — Already Installed",
        message=(
            "DDRIG is already installed.\n\n"
            ".mod file : %s\n"
            "points to : %s\n\n"
            "New source: %s\n\n"
            "Choose an action:" % (installed_at, current_target, repo_python_dir)
        ),
        button=["Reinstall", "Uninstall", "Cancel"],
        defaultButton="Cancel",
        cancelButton="Cancel",
        dismissString="Cancel",
    )
    if choice == "Reinstall":
        _do_reinstall(repo_python_dir)
    elif choice == "Uninstall":
        _do_uninstall(repo_python_dir)


# ---------------------------------------------------------------------------
# Actions — each one tries hotswap first, falls back to minimum-viable
#           .mod-only behaviour on error.
# ---------------------------------------------------------------------------

def _ensure_hotswap_importable(repo_python_dir):
    """Make sure ``ddrig.tools.hotswap`` can be imported right now by
    injecting this file's directory (which is the repo's python/ dir)
    into sys.path."""
    if repo_python_dir not in sys.path:
        sys.path.insert(0, repo_python_dir)


def _do_install(repo_python_dir):
    _ensure_hotswap_importable(repo_python_dir)
    try:
        from ddrig.tools import hotswap
        result = hotswap.install(silent=True)
        cmds.confirmDialog(
            title="DDRIG",
            message=(
                "DDRIG installed — menu 'DDRIG' is ready in the main "
                "menu bar. No restart required.\n\n"
                "mod file: %s" % result["mod_file"]
            ),
        )
    except Exception:
        traceback.print_exc()
        _legacy_install_mod_only(repo_python_dir)


def _do_reinstall(repo_python_dir):
    _ensure_hotswap_importable(repo_python_dir)
    try:
        from ddrig.tools import hotswap
        result = hotswap.reinstall(source_root=None)
        cmds.confirmDialog(
            title="DDRIG",
            message=(
                "DDRIG reinstalled.\n\n"
                "Previous state cleaned (menu / sys.modules / sys.path), "
                "new menu built from:\n\n%s" % result["install"]["source_root"]
            ),
        )
    except Exception:
        traceback.print_exc()
        _legacy_install_mod_only(repo_python_dir)


def _do_uninstall(repo_python_dir):
    _ensure_hotswap_importable(repo_python_dir)
    try:
        from ddrig.tools import hotswap
        result = hotswap.uninstall()
        cmds.confirmDialog(
            title="DDRIG",
            message=(
                "DDRIG uninstalled.\n\n"
                "Menu removed: %s\n"
                "Windows closed: %d\n"
                "Modules purged: %d\n"
                ".mod deleted: %s\n\n"
                "Scene rig nodes were NOT touched — use File > New if you "
                "need a clean scene." % (
                    result["menu_removed"],
                    len(result["closed_windows"]),
                    result["purged_modules"],
                    result["mod_file_deleted"],
                )
            ),
        )
    except Exception:
        traceback.print_exc()
        _legacy_uninstall_mod_only()


# ---------------------------------------------------------------------------
# Legacy fallbacks — used only when hotswap is broken.
# ---------------------------------------------------------------------------

def _legacy_install_mod_only(repo_python_dir):
    """Writes the .mod descriptor and asks the user to restart Maya."""
    ddrig_module = os.path.normpath(
        os.path.join(repo_python_dir, "maya_modules", "shelves_module")
    )
    module_file_content = "+ ddrig 0.0.1 %s" % ddrig_module

    user_module_dir = os.path.join(cmds.internalVar(uad=True), "modules")
    if not os.path.isdir(user_module_dir):
        os.makedirs(user_module_dir)
    user_module_file = os.path.join(user_module_dir, "ddrig.mod")
    if os.path.isfile(user_module_file):
        os.remove(user_module_file)
    with open(user_module_file, "w") as f:
        f.write(module_file_content)
    cmds.confirmDialog(
        title="DDRIG (fallback)",
        message=(
            "Hot install failed (see Script Editor for traceback).\n\n"
            "The .mod file has been written; please restart Maya to "
            "pick up the module the traditional way."
        ),
    )


def _legacy_uninstall_mod_only():
    """Removes the .mod descriptor and asks the user to restart Maya."""
    user_module_file = _user_mod_path()
    removed = False
    if os.path.isfile(user_module_file):
        try:
            os.remove(user_module_file)
            removed = True
        except OSError:
            pass
    cmds.confirmDialog(
        title="DDRIG (fallback)",
        message=(
            "Hot uninstall failed (see Script Editor for traceback).\n\n"
            ".mod file %s. Please restart Maya for a full cleanup." %
            ("removed" if removed else "could not be removed")
        ),
    )
