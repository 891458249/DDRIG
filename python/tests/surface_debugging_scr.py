from importlib import reload
from ddrig.library import functions
reload(functions)
from ddrig.core import io
reload(io)
from ddrig.core import settings
reload(settings)
from ddrig.ui import main
reload(main)
from ddrig.base import initials
reload(initials)
from ddrig.modules import arm
reload(arm)
from ddrig.modules import spine
reload(spine)
from ddrig.modules import head
reload(head)

#a = main.MainUI().show()

from ddrig.actions import kinematics
reload(kinematics)
from ddrig.modules import surface
reload(surface)
from ddrig.modules import tentacle
reload(tentacle)
tentacle_handler = kinematics.Kinematics(root_joint="jInit_tentacle_center_0")
tentacle_handler.action()
surface_handler = kinematics.Kinematics(root_joint="surface_center")
surface_handler.action()

functions.delete_object("ddrig_refGuides")

from ddrig.actions import weights
reload(weights)
weight_handler = weights.Weights()

#weight_handler.save_weights(deformer="skinCluster3", file_path="C:\\Users\\kutlu\\Documents\\testObj_final.json")
#weight_handler.save_weights(deformer="skinCluster11", file_path="C:\\Users\\kutlu\\Documents\\testObj_local.json")

weight_handler.create_deformer("C:\\Users\\kutlu\\Documents\\testObj_final.json")
weight_handler.create_deformer("C:\\Users\\kutlu\\Documents\\testObj_local.json")