import importlib
from maya import cmds
import maya.api.OpenMaya as om

from ddrig.core.decorators import undo
from ddrig.core import database

from ddrig.library import functions, naming
from ddrig.library import joint
from ddrig.library import connection
from ddrig.library import attribute

from ddrig import modules

from ddrig.core import filelog

log = filelog.Filelog(logname=__name__, filename="ddrig_log")
db = database.Database()


class Initials(object):
    def __init__(self):
        super(Initials, self).__init__()
        self.parseSettings()
        self.projectName = "ddrig"

        self.module_dict =  {module_name: data["guide"].limb_data for module_name, data in modules.class_data.items()}
        self.valid_limbs = self.module_dict.keys()
        self.validRootList = [
            values["members"][0] for values in self.module_dict.values()
        ]
        self.non_sided_limbs = [
            limb for limb in self.valid_limbs if not self.module_dict[limb]["sided"]
        ]

    def parseSettings(self):
        parsingDictionary = {
            "+x": (1, 0, 0),
            "+y": (0, 1, 0),
            "+z": (0, 0, 1),
            "-x": (-1, 0, 0),
            "-y": (0, -1, 0),
            "-z": (0, 0, -1),
        }
        self.upVector_asString = db.userSettings.upAxis
        self.lookVector_asString = db.userSettings.lookAxis
        self.mirrorVector_asString = db.userSettings.mirrorAxis

        self.upVector = om.MVector(parsingDictionary[db.userSettings.upAxis])
        self.lookVector = om.MVector(parsingDictionary[db.userSettings.lookAxis])
        self.mirrorVector = om.MVector(parsingDictionary[db.userSettings.mirrorAxis])

        # get transformation matrix:
        self.upVector.normalize()
        self.lookVector.normalize()
        # get the third axis with the cross vector
        side_vect = self.upVector ^ self.lookVector
        # recross in case up and front were not originally orthoganl:
        front_vect = side_vect ^ self.upVector
        # the new matrix is
        self.tMatrix = om.MMatrix(
            (
                (side_vect.x, side_vect.y, side_vect.z, 0),
                (self.upVector.x, self.upVector.y, self.upVector.z, 0),
                (front_vect.x, front_vect.y, front_vect.z, 0),
                (0, 0, 0, 1),
            )
        )

    def autoGet(self, parentBone):
        """
        Gets the mirror of the given object by its name. Returns the left if it finds right and vice versa
        Args:
            parentBone: (string) the object which name will be checked

        Returns: (Tuple) None/String, alignment of the given Obj(string),
                alignment of the returned Obj(string)  Ex.: (bone_left, "left", "right")

        """
        if not cmds.objExists(parentBone):
            log.warning("Joints cannot be identified automatically")
            return None, None, None
        if parentBone.startswith("R_"):
            mirrorBoneName = parentBone.replace("R_", "L_")
            alignmentGiven = "right"
            alignmentReturn = "left"
        elif parentBone.startswith("L_"):
            mirrorBoneName = parentBone.replace("L_", "R_")
            alignmentGiven = "left"
            alignmentReturn = "right"
        elif parentBone.startswith("C_"):
            return None, "both", None
        else:
            log.warning("Joints cannot be identified automatically")
            return None, None, None
        if cmds.objExists(mirrorBoneName):
            return mirrorBoneName, alignmentGiven, alignmentReturn
        else:
            # log.warning("cannot find mirror Joint automatically")
            return None, alignmentGiven, None

    @undo
    def initLimb(
        self,
        limb_name,
        whichSide="left",
        constrainedTo=None,
        parentNode=None,
        defineAs=False,
        *args,
        **kwargs
    ):
        if limb_name not in self.valid_limbs:
            log.error("%s is not a valid limb" % limb_name)

        currentselection = cmds.ls(sl=True)

        ## Create the holder group if it does not exist
        holderGroup = "{0}_refGuides".format(self.projectName)
        if not cmds.objExists(holderGroup):
            holderGroup = cmds.group(name=holderGroup, em=True)

        ## skip side related stuff for no-side related limbs
        if limb_name in self.non_sided_limbs:
            whichSide = "c"
            side = "C"
        else:
            ## check validity of side arguments
            valid_sides = ["left", "right", "center", "both", "auto"]
            if whichSide not in valid_sides:
                # log.error(
                #     "side argument '%s' is not valid. Valid arguments are: %s" % (whichSide, valid_sides))
                raise ValueError
            if (
                len(cmds.ls(sl=True, type="joint")) != 1
                and whichSide == "auto"
                and defineAs == False
            ):
                log.warning("You need to select a single joint to use Auto method")
                return
            ## get the necessary info from arguments
            if whichSide == "left":
                side = "L"
            elif whichSide == "right":
                side = "R"
            else:
                side = "C"

        limb_group_name = naming.parse([limb_name], side=side)
        limb_group_name = naming.unique_name(
            "{}_guides".format(limb_group_name), suffix="_guides"
        )
        # limb_group_name = limb_group_name.replace("_guides", "")
        # strip the side and suffix and get the name of the limb
        limb_name_parts = limb_group_name.split("_")[:-1]  # don't include the suffix
        # remove the side and suffix
        limb_name_parts = [
            part for part in limb_name_parts if part not in ["L", "R", "C", "grp"]
        ]
        unique_limb = "_".join(limb_name_parts)

        ## if defineAs is True, define the selected joints as the given limb instead creating new ones.
        if defineAs:
            # TODO: AUTO argument can be included by running a seperate method to determine the side of the root joint according to the matrix
            construct_command = "modules.{0}.Guides(suffix='{1}', side='{2}')".format(
                limb_name, unique_limb, side
            )
            guide = eval(construct_command)
            guide.convertJoints(currentselection)
            self.adjust_guide_display(guide)
            return

        if not parentNode:
            if cmds.ls(selection=True, type="joint"):
                j = cmds.ls(selection=True)[-1]
                try:
                    if joint.identify(j, self.module_dict)[1] in self.valid_limbs:
                        masterParent = cmds.ls(sl=True)[-1]
                    else:
                        masterParent = None
                except KeyError:
                    masterParent = None
            else:
                masterParent = None
        else:
            masterParent = parentNode
        if whichSide == "both":
            locators1, jnt_dict_side1 = self.initLimb(limb_name, "left", **kwargs)
            locators2, jnt_dict_side2 = self.initLimb(
                limb_name, "right", constrainedTo=locators1, **kwargs
            )
            jnt_dict_side1.update(jnt_dict_side2)
            return (locators1 + locators2), jnt_dict_side1
        if whichSide == "auto" and masterParent:
            mirrorParent, givenAlignment, returnAlignment = self.autoGet(masterParent)
            locators1, jnt_dict_side1 = self.initLimb(
                limb_name, givenAlignment, **kwargs
            )
            if mirrorParent:
                locators2, jnt_dict_side2 = self.initLimb(
                    limb_name,
                    returnAlignment,
                    constrainedTo=locators1,
                    parentNode=mirrorParent,
                    **kwargs
                )
                total_locators = locators1 + locators2
                jnt_dict_side1.update(jnt_dict_side2)
            else:
                total_locators = locators1
            return total_locators, jnt_dict_side1

        limb_group = cmds.group(empty=True, name=limb_group_name)
        cmds.parent(limb_group, holderGroup)
        cmds.select(clear=True)

        guide = modules.class_data[limb_name]["guide"](side=side, suffix=unique_limb, tMatrix=self.tMatrix, upVector=self.upVector, mirrorVector=self.mirrorVector, lookVector=self.lookVector, **kwargs)
        guide.createGuides()

        self.adjust_guide_display(guide)

        cmds.select(d=True)

        ### Constrain locating

        # loc_grp = cmds.group(name=("locGrp_%s" % unique_limb), em=True)
        loc_grp = cmds.group(
            name=naming.parse([unique_limb, "locators"], side=side, suffix="grp"),
            em=True,
        )
        cmds.setAttr("{0}.v".format(loc_grp), 0)
        locatorsList = []

        for jnt in range(0, len(guide.guideJoints)):
            locator = cmds.spaceLocator(name="loc_%s" % guide.guideJoints[jnt])[0]
            locatorsList.append(locator)
            if constrainedTo:
                functions.align_to(
                    locator, guide.guideJoints[jnt], position=True, rotation=False
                )
                connection.connect_mirror(
                    constrainedTo[jnt],
                    locatorsList[jnt],
                    mirror_axis=self.mirrorVector_asString,
                )

                functions.align_to(
                    guide.guideJoints[jnt], locator, position=True, rotation=False
                )
                cmds.parentConstraint(locator, guide.guideJoints[jnt], mo=True)
                # extra.matrixConstraint(locator, limbJoints[jnt], mo=True)
            else:
                cmds.parentConstraint(guide.guideJoints[jnt], locator, mo=False)
                # extra.matrixConstraint(limbJoints[jnt], locator, mo=False)

            cmds.parent(locator, loc_grp)
        cmds.parent(loc_grp, limb_group)

        ### MOVE THE LIMB TO THE DESIRED LOCATION
        if masterParent:
            if not constrainedTo:
                # align the none constrained near to the selected joint
                functions.align_to(guide.guideJoints[0], masterParent)
                # move it a little along the mirrorAxis
                # move it along offsetvector
                cmds.move(
                    guide.offsetVector[0],
                    guide.offsetVector[1],
                    guide.offsetVector[2],
                    guide.guideJoints[0],
                    relative=True,
                )
            else:
                for jnt in guide.guideJoints:
                    attribute.lock_and_hide(
                        jnt, ["tx", "ty", "tz", "rx", "ry", "rz"], hide=False
                    )
            cmds.parent(guide.guideJoints[0], masterParent)
        else:
            cmds.parent(guide.guideJoints[0], limb_group)
        cmds.select(currentselection)

        return locatorsList, {side: guide.guideJoints}

    def _getMirror(self, vector):
        """Returns reflection of the vector along the mirror axis"""
        return vector - 2 * (vector * self.mirrorVector) * self.mirrorVector

    @undo
    def initHumanoid(self, spineSegments=3, neckSegments=3, fingers=5):
        # Humanoid preset pins the canonical DDRIG axis convention regardless
        # of the global GuidesCore defaults or db.userSettings at call time:
        #   lookAxis   = +X  (bone chain forward)
        #   upAxis     = +Y  (up)
        #   mirrorAxis = +X  (mirror plane normal -> YZ plane)
        # The Shared initLimb signature is intentionally left untouched —
        # passing axes via **kwargs would collide with initLimb's own explicit
        # upVector=self.upVector etc. on the Guides() constructor call
        # (duplicate-keyword TypeError). Instead we temporarily override the
        # Initials instance attributes that initLimb reads, and restore them
        # in `finally` so the preset is safe even under exceptions.
        _saved_axes = (
            self.upVector, self.mirrorVector, self.lookVector,
            self.upVector_asString, self.mirrorVector_asString,
            self.lookVector_asString, self.tMatrix,
        )
        self.upVector = om.MVector(0, 1, 0)
        self.mirrorVector = om.MVector(1, 0, 0)
        self.lookVector = om.MVector(1, 0, 0)
        self.upVector_asString = "+y"
        self.mirrorVector_asString = "+x"
        self.lookVector_asString = "+x"
        # tMatrix reconstruction mirrors parseSettings():
        #   side = up ^ look = (+Y) ^ (+X) = -Z
        #   front = side ^ up = (-Z) ^ (+Y) = +X
        _side = self.upVector ^ self.lookVector
        _front = _side ^ self.upVector
        self.tMatrix = om.MMatrix(
            (
                (_side.x, _side.y, _side.z, 0),
                (self.upVector.x, self.upVector.y, self.upVector.z, 0),
                (_front.x, _front.y, _front.z, 0),
                (0, 0, 0, 1),
            )
        )
        try:
            _, base_dict = self.initLimb("base", "center")
            base = base_dict["C"][0]
            cmds.select(base)
            _, spine_dict = self.initLimb("spine", "auto", segments=spineSegments)
            pelvis = spine_dict["C"][0]
            cmds.setAttr("%s.ty" % pelvis, 14)
            chest = spine_dict["C"][-1]
            cmds.select(pelvis)
            _, leg_dict = self.initLimb("leg", "auto")
            cmds.select(chest)
            _, arm_dict = self.initLimb("arm", "auto")
            _, head_dict = self.initLimb("head", "auto", segments=neckSegments)
            left_hand = arm_dict["L"][-1]
            fingers = []
            for nmb in range(5):
                cmds.select(left_hand)
                _, finger_dict = self.initLimb("finger", whichSide="auto", segments=3)
                # import pdb
                # pdb.set_trace()
                # fingers.append(finger_dict["L"])
                fingers.append(finger_dict)

            thumb_pos_data = [
                (1.1, 0.9, 0.25),
                (0.8, 0.0, 0.0),
                (0.55, 0.0, 0.00012367864829724757),
                (0.45, 0.0, 0.0),
            ]
            thumb_rot_data = [
                (31.0, 45.0, 3.0000000000000004),
                (-1.0, -2.0, 17.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ]
            index_pos_data = [
                (2.0, 0.55, 0.0),
                (1.0, 0.0, 0.0),
                (0.65, 0.0, 0.0),
                (0.6, 0.0, 0.0),
            ]
            index_rot_data = [
                (1.0, 17.0, -3.0000000000000004),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ]
            middle_pos_data = [
                (2.0, -0.05, -0.09983537560644819),
                (0.9997424668383346, 0.0, 0.0),
                (0.7, 0.0, 0.0),
                (0.7, 0.0, 0.0),
            ]
            middle_rot_data = [
                (0.0, 7.805352401908098, -0.9999999999999998),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ]
            ring_pos_data = [
                (1.8, -0.55, -0.10011550541107042),
                (0.95, 0.0, 0.0),
                (0.7, 0.0, 0.0),
                (0.6, 0.0, 0.0),
            ]
            ring_rot_data = [
                (0.0, -5.0, -1.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ]
            pinky_pos_data = [
                (1.5, -1.1, 0.0),
                (0.8, 0.0, 0.0),
                (0.5, 0.0, 0.0),
                (0.5, 0.0, 0.0),
            ]
            pinky_rot_data = [
                (0.0, -12.000000000000002, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0),
            ]

            for nmb, member in enumerate(fingers[0]["L"]):
                cmds.xform(
                    member,
                    absolute=True,
                    translation=thumb_pos_data[nmb],
                    rotation=thumb_rot_data[nmb],
                )
            cmds.setAttr("%s.fingerType" % fingers[0]["L"][0], 1)
            cmds.setAttr("%s.fingerType" % fingers[0]["R"][0], 1)

            # for nmb, member in enumerate(fingers[0]):
            #     cmds.xform(member, absolute=True, translation=thumb_pos_data[nmb], rotation=thumb_rot_data[nmb])
            # cmds.setAttr("%s.fingerType" % fingers[0][0], 1)

            for nmb, member in enumerate(fingers[1]["L"]):
                cmds.xform(
                    member,
                    absolute=True,
                    translation=index_pos_data[nmb],
                    rotation=index_rot_data[nmb],
                )
            cmds.setAttr("%s.fingerType" % fingers[1]["L"][0], 2)
            cmds.setAttr("%s.fingerType" % fingers[1]["R"][0], 2)

            for nmb, member in enumerate(fingers[2]["L"]):
                cmds.xform(
                    member,
                    absolute=True,
                    translation=middle_pos_data[nmb],
                    rotation=middle_rot_data[nmb],
                )
            cmds.setAttr("%s.fingerType" % fingers[2]["L"][0], 3)
            cmds.setAttr("%s.fingerType" % fingers[2]["R"][0], 3)

            for nmb, member in enumerate(fingers[3]["L"]):
                cmds.xform(
                    member,
                    absolute=True,
                    translation=ring_pos_data[nmb],
                    rotation=ring_rot_data[nmb],
                )
            cmds.setAttr("%s.fingerType" % fingers[3]["L"][0], 4)
            cmds.setAttr("%s.fingerType" % fingers[3]["L"][0], 4)

            for nmb, member in enumerate(fingers[4]["L"]):
                cmds.xform(
                    member,
                    absolute=True,
                    translation=pinky_pos_data[nmb],
                    rotation=pinky_rot_data[nmb],
                )
            cmds.setAttr("%s.fingerType" % fingers[4]["L"][0], 5)
            cmds.setAttr("%s.fingerType" % fingers[4]["R"][0], 5)
            return True
        finally:
            (self.upVector, self.mirrorVector, self.lookVector,
             self.upVector_asString, self.mirrorVector_asString,
             self.lookVector_asString, self.tMatrix) = _saved_axes

    def adjust_guide_display(self, guide_object):
        """Adjusts the display proerties of guid joints according to the settings. Accepts guide object as input"""

        for jnt in guide_object.guideJoints:
            cmds.setAttr("%s.displayLocalAxis" % jnt, 1)
            cmds.setAttr("%s.drawLabel" % jnt, 1)

        if guide_object.side == "C":
            functions.colorize(
                guide_object.guideJoints, db.userSettings.majorCenterColor, shape=False
            )
        if guide_object.side == "L":
            functions.colorize(
                guide_object.guideJoints, db.userSettings.majorLeftColor, shape=False
            )
        if guide_object.side == "R":
            functions.colorize(
                guide_object.guideJoints, db.userSettings.majorRightColor, shape=False
            )

    def get_scene_roots(self):
        """collects the root joints in the scene and returns the dictionary with properties"""
        all_joints = cmds.ls(type="joint")
        # get roots
        guide_roots = [
            jnt for jnt in all_joints if joint.get_joint_type(jnt) in self.validRootList
        ]
        roots_dictionary_list = []
        for jnt in guide_roots:
            # get module name
            try:
                module_name = cmds.getAttr("%s.moduleName" % jnt)
            except ValueError:
                continue
            # get module info
            j_type, limb, side = joint.identify(jnt, self.module_dict)
            roots_dictionary_list.append(
                {
                    "module_name": module_name,
                    "side": side,
                    "root_joint": jnt,
                    "module_type": limb,
                }
            )

        return roots_dictionary_list

    def select_root(self, joint_name):
        cmds.select(joint_name)

    def get_property(self, jnt, attr):
        try:
            return cmds.getAttr("%s.%s" % (jnt, attr))
        except ValueError:
            log.warning("Attribute cannot find %s.%s" % (jnt, attr))
            return False

    def set_property(self, jnt, attr, value):
        if type(value) == int or type(value) == float or type(value) == bool:
            cmds.setAttr("%s.%s" % (jnt, attr), value)
        else:
            cmds.setAttr("%s.%s" % (jnt, attr), value, type="string")

    def rename_module(self, root_jnt, new_user_input):
        """Rename every DAG node associated with a guide module so the scene
        Outliner stays in sync with the user-facing module name.

        Rule-aware: the module's name may carry its side token as a prefix,
        suffix, infix, or not at all, depending on the active naming rule.
        The algorithm identifies the "core" part of each affected node name
        — the single ``_``-delimited token that equals the module's core
        (e.g. ``arm`` in ``L_arm_collar_jInit`` or ``arm_collar_jInit_l`` or
        ``arm_l_collar_jInit``) — and substitutes it with the new core.
        This handles every builtin rule uniformly and extends to user-
        defined rules that follow the same single-core convention.

        Args:
            root_jnt (str): Current DAG name of the guide root joint.
            new_user_input (str): New name as typed by the user. May include
                the active rule's side decoration or just be a bare core.
                Cross-side rename is refused.

        Returns:
            dict: ``{"new_module_name", "new_root_jnt", "renamed_count"}``.
            No-op when the re-styled new name equals the stored one.

        Raises:
            ValueError: on invalid input, cross-side rename, reserved-token
                collision, or pre-flight name conflict. No DAG change is
                made in the error path.
        """
        from ddrig.library import naming_rules
        active_rule = naming_rules.get_active_rule()

        # --- Step A: identify side and old_core ---
        if not new_user_input or not new_user_input.strip():
            raise ValueError("New module name is empty.")
        new_input = new_user_input.strip()

        old_module_name = cmds.getAttr("%s.moduleName" % root_jnt)

        # .side enum is authoritative; fall back to leading token of
        # moduleName only if the enum somehow lost its value.
        side = joint.get_joint_side(root_jnt)
        if side not in naming_rules.VALID_SIDES:
            first_tok = old_module_name.split("_", 1)[0]
            if first_tok in naming_rules.VALID_SIDES:
                side = first_tok
            else:
                raise ValueError(
                    "Cannot determine side for module %r." % old_module_name
                )

        # Strip the active rule off the stored moduleName to get old_core.
        # If the stored name was built under a different rule and strip
        # fails, treat the whole name as the core — best-effort rename.
        old_core = naming_rules.strip_side(old_module_name, side, active_rule)
        if old_core is None:
            old_core = old_module_name

        # --- Step A2: parse user input into new_core ---
        # First, reject cross-side input: does new_input match ANY
        # other side's rule pattern (besides the module's own)?
        for other_side in naming_rules.VALID_SIDES:
            if other_side == side:
                continue
            other_cfg = active_rule["sides"].get(other_side, {}) if active_rule else {}
            # "none" mode strips to the input unchanged, which would always
            # "match" — that is not a genuine cross-side signal.
            if other_cfg.get("mode") == "none":
                continue
            if naming_rules.strip_side(new_input, other_side, active_rule) is not None:
                raise ValueError(
                    "Input %r uses the %s-side decoration but the module "
                    "is on %s. Cross-side rename is not supported." %
                    (new_input, other_side, side)
                )

        # If the input already wears the current side's decoration, strip it.
        self_cfg = active_rule["sides"].get(side, {}) if active_rule else {}
        stripped_self = naming_rules.strip_side(new_input, side, active_rule)
        if stripped_self is not None and self_cfg.get("mode") != "none":
            new_core = stripped_self
        else:
            new_core = new_input

        if not new_core:
            raise ValueError("New module name core part is empty.")
        if any(tok in new_core.split("_")
               for tok in ("jInit", "guides", "locators", "grp")):
            raise ValueError(
                "New module name core %r contains a reserved DDRIG token "
                "(jInit/guides/locators/grp)." % new_core
            )

        new_module_name = naming_rules.apply_side(
            side, [new_core], rule=active_rule
        )
        if new_module_name == old_module_name:
            return {
                "new_module_name": new_module_name,
                "new_root_jnt": root_jnt,
                "renamed_count": 0,
            }

        # --- Step B: collect every node to rename, pinned by UUID ---
        def _short(path):
            return path.rsplit("|", 1)[-1]

        def _uid(path):
            result = cmds.ls(path, uuid=True)
            if not result:
                raise ValueError("Cannot resolve UUID for %r" % path)
            return result[0]

        def _substitute_core(name, old, new):
            """Replace the leftmost ``_``-delimited token equal to ``old``
            with ``new``. Returns None if ``old`` is not a token in ``name``.
            """
            tokens = name.split("_")
            for i, tok in enumerate(tokens):
                if tok == old:
                    tokens[i] = new
                    return "_".join(tokens)
            return None

        nodes_to_rename = []

        # B.1 root joint itself
        root_short = _short(root_jnt)
        root_new = _substitute_core(root_short, old_core, new_core)
        if root_new is None:
            raise ValueError(
                "Root joint %r does not contain the module core %r as an "
                "underscore-delimited token; cannot rename under the active "
                "naming rule." % (root_short, old_core)
            )
        root_uuid = _uid(root_jnt)
        nodes_to_rename.append((root_uuid, root_new))

        # B.2 descendant joints in the guide chain
        descendants = cmds.listRelatives(
            root_jnt, allDescendents=True, type="joint", fullPath=True
        ) or []
        for child in descendants:
            short = _short(child)
            new_short = _substitute_core(short, old_core, new_core)
            if new_short is not None:
                nodes_to_rename.append((_uid(child), new_short))

        # B.3 ancestor limb group + its locators_grp sibling + locator leaves
        parent = cmds.listRelatives(root_jnt, parent=True, fullPath=True)
        while parent:
            parent_short = _short(parent[0])
            # Limb group: a node whose name ends with '_guides' AND contains
            # our core as an inner token. That matches prefix/suffix/mid
            # rule-styled limb groups uniformly.
            if parent_short.endswith("_guides"):
                limb_new = _substitute_core(parent_short, old_core, new_core)
                if limb_new is not None:
                    nodes_to_rename.append((_uid(parent[0]), limb_new))
                    siblings = cmds.listRelatives(
                        parent[0], children=True, fullPath=True
                    ) or []
                    for sib in siblings:
                        sib_short = _short(sib)
                        if sib_short.endswith("_locators_grp"):
                            sib_new = _substitute_core(
                                sib_short, old_core, new_core
                            )
                            if sib_new is not None:
                                nodes_to_rename.append((_uid(sib), sib_new))
                                loc_children = cmds.listRelatives(
                                    sib, children=True, fullPath=True
                                ) or []
                                for loc in loc_children:
                                    loc_short = _short(loc)
                                    loc_new = _substitute_core(
                                        loc_short, old_core, new_core
                                    )
                                    if loc_new is not None:
                                        nodes_to_rename.append(
                                            (_uid(loc), loc_new)
                                        )
                break
            parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)

        # --- Step C: pre-flight collision check ---
        planned_names = {new for _, new in nodes_to_rename}
        uuid_set = {uid for uid, _ in nodes_to_rename}
        for uid, new_name in nodes_to_rename:
            if cmds.objExists(new_name):
                existing = cmds.ls(new_name, uuid=True)
                if existing and existing[0] not in uuid_set:
                    raise ValueError(
                        "Name conflict: %r already exists in scene "
                        "(uuid=%s). Rename aborted, no changes applied." %
                        (new_name, existing[0])
                    )
        # sanity: all planned new names must be unique
        if len(planned_names) != len(nodes_to_rename):
            raise ValueError(
                "Internal error: duplicate target name in plan (%d plans "
                "but %d distinct names). Aborting." %
                (len(nodes_to_rename), len(planned_names))
            )

        # --- Step D: execute in a single undo chunk ---
        renamed = 0
        cmds.undoInfo(openChunk=True, chunkName="ddrig_rename_module")
        try:
            for uid, new_name in nodes_to_rename:
                current_path = cmds.ls(uid)[0]
                cmds.rename(current_path, new_name)
                renamed += 1
            new_root_path = cmds.ls(root_uuid)[0]
            cmds.setAttr(
                "%s.moduleName" % new_root_path, new_module_name, type="string"
            )
        except Exception as exc:
            # Wrap Maya errors so the UI layer only has to catch ValueError.
            raise ValueError(
                "Maya rename failed mid-operation: %s "
                "(Ctrl+Z in Maya reverts the partial work)." % exc
            )
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "new_module_name": new_module_name,
            "new_root_jnt": _short(cmds.ls(root_uuid)[0]),
            "renamed_count": renamed,
        }

    def delete_module(self, root_jnt):
        """Delete every DAG node belonging to a guide module.

        Walks the ancestor chain of ``root_jnt`` to find the limb group
        ``{module_name}_guides`` and deletes that whole subtree in one go
        (guide joints, ``_locators_grp``, every ``loc_*`` leaf). If after
        the delete the ``{projectName}_refGuides`` holder has no children
        left, the holder is deleted too so the Outliner stays tidy.

        Fallback: if no ancestor limb group is found (e.g. the joint was
        manually reparented), only the root joint and its descendants are
        deleted. This loses the locators group, but surfaces the anomaly
        via ``deleted_limb_group=False`` in the return dict so the caller
        can warn the user.

        Args:
            root_jnt (str): Current DAG name of the guide root joint.

        Returns:
            dict with keys:
                module_name         -- ``.moduleName`` that was on the root.
                deleted_limb_group  -- True if the ``_guides`` group was
                                       located and removed as a whole.
                deleted_holder      -- True if the ``{projectName}_refGuides``
                                       holder ended up empty and was removed.
                nodes_deleted       -- Rough count (group/joint + descendants
                                       + holder if applicable).
        """
        module_name = cmds.getAttr("%s.moduleName" % root_jnt)
        holder_name = "%s_refGuides" % self.projectName

        # Locate limb group via ancestor walk (same traversal as rename_module).
        limb_group = None
        parent = cmds.listRelatives(root_jnt, parent=True, fullPath=True)
        while parent:
            parent_short = parent[0].rsplit("|", 1)[-1]
            if parent_short == "%s_guides" % module_name:
                limb_group = parent[0]
                break
            parent = cmds.listRelatives(parent[0], parent=True, fullPath=True)

        cmds.undoInfo(openChunk=True, chunkName="ddrig_delete_module")
        try:
            if limb_group:
                descendants = cmds.listRelatives(
                    limb_group, allDescendents=True, fullPath=True
                ) or []
                nodes_deleted = 1 + len(descendants)
                cmds.delete(limb_group)
                deleted_limb_group = True
            else:
                # Fallback: no limb_group — delete the joint subtree only.
                descendants = cmds.listRelatives(
                    root_jnt, allDescendents=True, fullPath=True
                ) or []
                nodes_deleted = 1 + len(descendants)
                cmds.delete(root_jnt)
                deleted_limb_group = False

            deleted_holder = False
            if cmds.objExists(holder_name):
                remaining = cmds.listRelatives(holder_name, children=True) or []
                if not remaining:
                    cmds.delete(holder_name)
                    deleted_holder = True
                    nodes_deleted += 1
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "module_name": module_name,
            "deleted_limb_group": deleted_limb_group,
            "deleted_holder": deleted_holder,
            "nodes_deleted": nodes_deleted,
        }

    def cleanup_orphan_guide_groups(self):
        """Find and delete orphan '_guides' limb groups — those that no
        longer have a corresponding guide root joint in the scene.

        Typical cause: the user deleted the guide joint chain from the
        Outliner (or via Del in the Maya viewport) which left the
        '{side}_{unique}_guides' group with only the locators subgroup
        behind. ``populate_guides`` cannot see those orphans because it
        scans by joint type, so without this helper they accumulate
        invisibly in '{projectName}_refGuides'.

        Returns:
            dict with keys:
                orphan_groups   -- short names of the groups that were
                                   removed (empty list if none found).
                deleted_holder  -- True if '{projectName}_refGuides'
                                   ended up empty and was removed too.
                nodes_deleted   -- rough total (each group + its
                                   descendants + the holder if removed).
        """
        holder_name = "%s_refGuides" % self.projectName
        if not cmds.objExists(holder_name):
            return {
                "orphan_groups": [],
                "deleted_holder": False,
                "nodes_deleted": 0,
            }

        live_module_names = {
            info["module_name"] for info in self.get_scene_roots()
        }

        children = cmds.listRelatives(
            holder_name, children=True, fullPath=True
        ) or []
        orphan_paths = []
        for child in children:
            short = child.rsplit("|", 1)[-1]
            if not short.endswith("_guides"):
                continue
            mod_name = short[: -len("_guides")]
            if mod_name not in live_module_names:
                orphan_paths.append((child, short))

        nodes_deleted = 0
        deleted_holder = False
        cmds.undoInfo(openChunk=True, chunkName="ddrig_cleanup_orphans")
        try:
            for path, _short in orphan_paths:
                descendants = cmds.listRelatives(
                    path, allDescendents=True, fullPath=True
                ) or []
                nodes_deleted += 1 + len(descendants)
                cmds.delete(path)
            if cmds.objExists(holder_name):
                remaining = cmds.listRelatives(
                    holder_name, children=True
                ) or []
                if not remaining:
                    cmds.delete(holder_name)
                    deleted_holder = True
                    nodes_deleted += 1
        finally:
            cmds.undoInfo(closeChunk=True)

        return {
            "orphan_groups": [short for _path, short in orphan_paths],
            "deleted_holder": deleted_holder,
            "nodes_deleted": nodes_deleted,
        }

    def get_extra_properties(self, module_type):
        module_type_dict = self.module_dict.get(module_type)
        if module_type_dict:
            return module_type_dict["properties"]

    def get_user_attrs(self, jnt):
        """
        Returns a list of dictionaries for every supported custom attribute

        This is part of guide data collection and this data is going to be used while re-creating guides
        """

        supported_attrs = [
            "long",
            "short",
            "bool",
            "enum",
            "float",
            "double",
            "string",
            "typed",
        ]  # wtf is typed
        list_of_dicts = []
        user_attr_list = cmds.listAttr(jnt, userDefined=True)
        if not user_attr_list:
            return []
        for attr in user_attr_list:
            attr_type = cmds.attributeQuery(attr, node=jnt, at=True)
            if attr_type not in supported_attrs:
                continue
            tmp_dict = {}
            tmp_dict["attr_name"] = cmds.attributeQuery(attr, node=jnt, ln=True)
            tmp_dict["attr_type"] = attr_type
            tmp_dict["nice_name"] = cmds.attributeQuery(attr, node=jnt, nn=True)
            tmp_dict["default_value"] = cmds.getAttr("%s.%s" % (jnt, attr))
            if attr_type == "enum":
                tmp_dict["enum_list"] = cmds.attributeQuery(attr, node=jnt, le=True)[0]
            elif attr_type == "bool":
                pass
            elif attr_type == "typed":
                ## Wtf is "typed" anyway??
                tmp_dict["attr_type"] = "string"
            else:
                try:
                    tmp_dict["min_value"] = cmds.attributeQuery(
                        attr, node=jnt, min=True
                    )[0]
                except RuntimeError:
                    pass
                try:
                    tmp_dict["max_value"] = cmds.attributeQuery(
                        attr, node=jnt, max=True
                    )[0]
                except RuntimeError:
                    pass

            list_of_dicts.append(tmp_dict)
        return list_of_dicts

    def getWholeLimb(self, node):
        multi_guide_jnts = [
            value["multi_guide"]
            for value in self.module_dict.values()
            if value["multi_guide"]
        ]
        limb_dict = {}
        multiList = []
        limb_name, limb_type, limb_side = joint.identify(node, self.module_dict)

        limb_dict[limb_name] = node
        nextNode = node
        z = True
        while z:
            children = cmds.listRelatives(nextNode, children=True, type="joint")
            children = [] if not children else children
            if len(children) < 1:
                z = False
            failedChildren = 0
            for child in children:
                child_limb_name, child_limb_type, child_limb_side = joint.identify(
                    child, self.module_dict
                )
                if (
                    child_limb_name not in self.validRootList
                    and child_limb_type == limb_type
                ):
                    nextNode = child
                    if child_limb_name in multi_guide_jnts:
                        multiList.append(child)
                        limb_dict[child_limb_name] = multiList
                    else:
                        limb_dict[child_limb_name] = child
                else:
                    failedChildren += 1
            if len(children) == failedChildren:
                z = False
        return [limb_dict, limb_type, limb_side]

    @undo
    def test_build(self, root_jnt=None, progress_bar=None):
        kinematics = importlib.import_module("ddrig.actions.kinematics")
        if not root_jnt:
            selection = cmds.ls(selection=True)
            if len(selection) == 1:
                root_jnt = selection[0]
            else:
                log.warning("Select a single root_jnt joint")
        if not cmds.objectType(root_jnt, isType="joint"):
            log.error("root_jnt is not a joint")
        root_name, root_type, root_side = joint.identify(root_jnt, self.module_dict)
        if root_name not in self.validRootList:
            log.error("Selected joint is not in the valid Guide Root")

        test_kinematics = kinematics.Kinematics(root_jnt, progress_bar=progress_bar)
        test_kinematics.afterlife = 0
        test_kinematics.action()
        return test_kinematics
