from maya import cmds
import ddrig_setup

cmds.evalDeferred(ddrig_setup.add_python_path)
cmds.evalDeferred(ddrig_setup.load_menu)
