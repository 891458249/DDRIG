from importlib import reload

from ddrig.version_control import rbl_shotgrid

reload(rbl_shotgrid)

shot_ddrig = rbl_shotgrid.ShotDDRIG()
a = shot_ddrig.task




shot_ddrig.request_new_session_path("hedehot")

shot_ddrig.request_new_version_path()

shot_ddrig.get_sessions("charCube", "RIG", "AvA")

shot_ddrig.sg

tk = shot_ddrig._sg_template.tk  # get tk instance from sg_template, or elsewhere if you already have an instance
temp = tk.templates.get("asset_ddrig_sessionfile")
fields = {"Asset": "charSoldier", "Step": "RIG",
          "variant_name": "AvA"}  # assemble all of the fields you know
paths = tk.paths_from_template(temp, fields, ["version"],
                               skip_missing_optional_keys=True)  # the third arg is a list of all the fields you don't know, and you need to use the "skip_missing_optional_keys=True" option
for path in paths:
    f=temp.get_fields(path)
    print(f)
    print(f.get("version"))
print(self._sg_template.fields_from_path(paths[0]))


test = {}
test["asdf"] = [2]
test.update({"asdf":[32]})


shot_ddrig._sg_template

shot_ddrig.task
shot_ddrig.get_latest_path("asset_ddrig_guide", part_name="weights1")

shot_ddrig.get_steps(shot_ddrig.asset)

shot_ddrig.get_tasks(shot_ddrig.asset, shot_ddrig.step)


##

# try to get the asset_type, asset, step and task values. Order:
    # 1. (DISCARDED) DDRIG session file
    # 2. solve it with with work file
    # 3. None (pick the first of each column)

# Get the asset_types and feed the combo box
# set the

from PySide2 import QtWidgets
from ddrig.ui.vcs_widgets import session_selection
reload(asset_selection)
# from ddrig.ui.custom_widgets import ListBoxLayout
d = QtWidgets.QDialog()
r = asset_selection.SessionSelection()
r.new_session_signal.connect(lambda x: print("hede_%s" %x))
d.setLayout(r)

d.show()