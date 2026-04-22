"""Drag & Drop installer for Maya 2018+.

Dragging this file into a Maya viewport triggers a no-restart install:
the ddrig.tools.hotswap.install() helper writes ~/maya/modules/ddrig.mod,
injects the repo's python/ into sys.path, and builds the 'DDRIG' menu.

On error, the legacy fall-back path still writes the .mod file and asks
the user to restart; this mirrors the pre-hotswap behaviour so a failed
hot install doesn't leave the user without a working module descriptor.
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
    _install()


def _install():
    """Install DDRIG without restarting Maya.

    Strategy:
      1. Put the repo's python/ dir on sys.path so ``ddrig.tools.hotswap``
         is importable right now.
      2. Delegate to hotswap.install() which handles .mod, menu, shelf.
      3. If hotswap raises for any reason, fall back to the legacy
         'write .mod + ask to restart' flow so the user is never worse
         off than the pre-hotswap baseline.
    """
    repo_root = os.path.dirname(os.path.abspath(__file__))
    python_dir = repo_root  # dragAndDropMe.py lives in the python/ dir itself
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)

    try:
        from ddrig.tools import hotswap
        result = hotswap.install(silent=True)
        cmds.confirmDialog(
            title="DDRIG",
            message=(
                "DDRIG installed.\n\n"
                "Menu 'DDRIG' is now in the main menu bar.\n"
                "No restart required.\n\n"
                "mod file: %s" % result["mod_file"]
            ),
        )
    except Exception:
        traceback.print_exc()
        _legacy_write_mod_only()


def _legacy_write_mod_only():
    """Fallback used only if ``ddrig.tools.hotswap`` blows up. Writes the
    .mod descriptor and asks the user to restart Maya — same behaviour the
    installer had before hotswap existed."""
    ddrig_module = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "maya_modules", "shelves_module")
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
