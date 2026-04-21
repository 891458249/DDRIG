from maya import cmds

cmds.file(new=True, f=True)

from ddrig.modules import spine
reload(spine)

from ddrig.library import icons
reload(controllers)

from ddrig.library import tools
reload(tools)

from ddrig.library import functions
reload(functions)

from ddrig import modules
reload(modules)

from ddrig.modules import hindleg
reload(hindleg)

from ddrig.modules import base

from ddrig.base import initials
reload(initials)

initializer = initials.Initials()
baseG = base.Guides()
baseG.createGuides()
guider = hindleg.Guides(side="L")
guider.createGuides()
cmds.setAttr("%s.localJoints" %guider.guideJoints[0], True)
cmds.setAttr("%s.stretchyIK" %guider.guideJoints[0], True)
cmds.setAttr("%s.ribbon" %guider.guideJoints[0], True)
cmds.parent(guider.guideJoints[0], baseG.guideJoints[0])

initializer.test_build(baseG.guideJoints[0])


cmds.setAttr("pref_cont.Rig_Visibility", 1)

