"""DDRIG ``ribbon`` module -- stable nurbs-driven ribbon rig.

Algorithm ported from the user's ``ribbon_rig_tool_freelancer3.2`` whose
behaviour is known to be robust under controller tweaking.  The
existing :mod:`ddrig.modules.tentacle` has been observed to drift when
animators sweep its parameters; ``ribbon`` is offered as an
alternative module so users can pick whichever fits their shot.

Phase 1 (this commit)
---------------------
* Auto-generated nurbsSurface from the init joint chain (loft of two
  offset curves, width = 5% of chain length along the mirror axis).
* U/V long-axis detection (via duplicateCurve + arclen comparison),
  honouring an optional user override on the root guide.
* ``jointRes`` follicles spaced evenly along the long axis, each
  driving one ``_jDef`` deform joint via a ``parentConstraint``.
* Bind joints chained as a DAG parent-child sequence so the UE
  Skeleton tool auto-picks them up as a hierarchy without any extra
  rule-table work.
* ``ctrlRes`` user-facing controllers (DDRIG's :class:`Controller`),
  positioned at evenly-spaced "ctrl follicles" on the surface.  The
  ctrl joints are skinned to the nurbsSurface so dragging a controller
  deforms the ribbon, which in turn drives every bind joint via the
  follicles.
* Standard DDRIG containers: surface + follicles -> ``nonScaleGrp``;
  bind joints -> ``defJointsGrp`` (chained); controllers ->
  controller list; ``limbPlug`` -> ``scaleGrp``.

Out of scope (Phase 2/3 follow-ups)
-----------------------------------
* Sine / wave / curl deformer overlay (user tool's ``createDeformer``).
* IK variant (user tool's ``ribbonForIK`` + ``createCtrlForIK``).
* Dynamics integration (user tool's ``ribbonDynamics.py``).
* Mirroring / IK-FK switching -- the user tool itself does not provide
  these; tentacle remains the choice when those are needed.
"""

from maya import cmds
import maya.api.OpenMaya as om

from ddrig.library import functions, joint
from ddrig.library import naming
from ddrig.library import api
from ddrig.objects.controller import Controller
from ddrig.core.module import ModuleCore, GuidesCore

from ddrig.core import filelog

LOG = filelog.Filelog(logname=__name__, filename="ddrig_log")


LIMB_DATA = {
    "members": ["RibbonRoot", "Ribbon", "RibbonEnd"],
    "properties": [
        {
            "attr_name": "jointRes",
            "nice_name": "Joint_Res",
            "attr_type": "long",
            "min_value": 2,
            "max_value": 9999,
            "default_value": 11,
        },
        {
            "attr_name": "ctrlRes",
            "nice_name": "Ctrl_Res",
            "attr_type": "long",
            "min_value": 2,
            "max_value": 9999,
            "default_value": 5,
        },
        {
            "attr_name": "ctrlSize",
            "nice_name": "Ctrl_Size",
            "attr_type": "float",
            "min_value": 0.1,
            "max_value": 100.0,
            "default_value": 1.0,
        },
        {
            "attr_name": "uvDirection",
            "nice_name": "UV_Direction",
            "attr_type": "enum",
            "enum_list": "auto:U:V",
            "default_value": 0,
        },
    ],
    "multi_guide": "Ribbon",
    "sided": True,
}


# ----------------------------------------------------------------------
# Build class
# ----------------------------------------------------------------------

class Ribbon(ModuleCore):
    name = "Ribbon"

    def __init__(self, build_data=None, inits=None):
        super(Ribbon, self).__init__()
        if build_data:
            self.ribbonRoot = build_data.get("RibbonRoot")
            self.ribbons = build_data.get("Ribbon") or []
            self.ribbonEnd = build_data.get("RibbonEnd")
            mids = self.ribbons if isinstance(self.ribbons, list) else [self.ribbons]
            self.inits = [self.ribbonRoot] + list(mids)
            if self.ribbonEnd:
                self.inits.append(self.ribbonEnd)
        elif inits:
            if len(inits) < 2:
                cmds.error(
                    "Ribbon setup needs at least 2 initial joints "
                    "(root + end)"
                )
                return
            self.inits = list(inits)
        else:
            LOG.error(
                "Ribbon needs either build_data or inits to be constructed"
            )
            return

        # Coordinate / orientation reference comes from the root guide.
        self.up_axis, self.mirror_axis, self.look_axis = joint.get_rig_axes(
            self.inits[0]
        )

        # Properties read off the root guide (set by the
        # ``properties`` block in LIMB_DATA above).
        self.useRefOrientation = cmds.getAttr(
            "%s.useRefOri" % self.inits[0]
        )
        self.joint_res = int(cmds.getAttr("%s.jointRes" % self.inits[0]))
        self.ctrl_res = int(cmds.getAttr("%s.ctrlRes" % self.inits[0]))
        self.ctrl_size = float(cmds.getAttr("%s.ctrlSize" % self.inits[0]))
        # 0 = auto, 1 = U, 2 = V (matches the ``enum_list`` order).
        self.uv_dir = int(cmds.getAttr("%s.uvDirection" % self.inits[0]))

        # Standard DDRIG identity fields.
        self.side = joint.get_joint_side(self.inits[0])
        self.sideMult = -1 if self.side == "R" else 1
        self.module_name = naming.unique_name(
            cmds.getAttr("%s.moduleName" % self.inits[0])
        )

        # World positions snapshot.
        self.init_positions = [
            api.get_world_translation(j) for j in self.inits
        ]
        self.total_length = self._chain_length()

        # Module variables filled in by :meth:`create_joints` /
        # :meth:`create_controllers`.
        self.surface = None
        self.surface_shape = None
        # Resolved direction: 1 = U follicle (segments along V),
        # 0 = V follicle (segments along U).  Same convention as the
        # source tool's ``isItU_or_V`` argument.
        self.uv_dir_resolved = 0
        self.bind_joints = []
        self.bind_follicles = []
        self.ctrl_joints = []
        self.ctrl_follicles = []
        self.ctrl_objects = []

    # -- helpers -------------------------------------------------------

    def _chain_length(self):
        """World-space arclength of the init chain.  Used to size the
        auto-generated nurbsSurface and to ballpark default control
        scales when ``ctrlSize`` is 1.0."""
        total = 0.0
        prev = None
        for p in self.init_positions:
            if prev is not None:
                v = om.MVector(p) - om.MVector(prev)
                total += v.length()
            prev = p
        return total

    def _vec(self, p):
        """Coerce ``p`` (MVector / list / tuple) to a 3-tuple of floats."""
        return (float(p[0]), float(p[1]), float(p[2]))

    # -- surface generation -------------------------------------------

    def _build_surface(self):
        """Loft two offset curves running through the init positions to
        form a flat nurbsSurface that follows the chain.  The width
        offset is along the module's ``mirror_axis`` so the surface
        opens "sideways" in the correct direction for left / right
        modules."""
        width = max(self.total_length * 0.05, 1.0)
        mirror = om.MVector(self.mirror_axis).normal()
        if mirror.length() < 1e-6:
            mirror = om.MVector(1, 0, 0)
        pts_a = [self._vec(p) for p in self.init_positions]
        pts_b = [
            self._vec(om.MVector(p) + mirror * width)
            for p in self.init_positions
        ]
        # Curve degree 2 keeps the loft well-conditioned even when
        # only a few init joints are used.
        deg = 2 if len(pts_a) >= 3 else 1
        curve_a = cmds.curve(
            point=pts_a,
            degree=deg,
            name=naming.parse([self.module_name, "loftA"], suffix="crv"),
        )
        curve_b = cmds.curve(
            point=pts_b,
            degree=deg,
            name=naming.parse([self.module_name, "loftB"], suffix="crv"),
        )
        surface = cmds.loft(
            curve_a, curve_b,
            ch=False, uniform=True, close=False, autoReverse=True,
            degree=3, sectionSpans=1, range=False, polygon=0,
            reverseSurfaceNormals=True,
            name=naming.parse([self.module_name, "ribbon"], suffix="nSurf"),
        )[0]
        cmds.delete(curve_a, curve_b)
        # Rebuild for clean parameterisation -- follicle spacing
        # depends on UV being uniform.
        cmds.rebuildSurface(
            surface, ch=False, replaceOriginal=True, rebuildType=0,
            endKnots=1, keepRange=0, keepCorners=False,
            keepControlPoints=False, spansU=max(len(pts_a) - 1, 1),
            degreeU=3, spansV=1, degreeV=3, fitRebuild=0, direction=2,
        )
        self.surface = surface
        self.surface_shape = functions.get_shapes(surface)[0]
        cmds.parent(self.surface, self.nonScaleGrp)
        return surface

    # -- U/V resolution ------------------------------------------------

    def _resolve_uv_direction(self):
        """Decide which axis (U or V) of the surface to walk follicles
        along.  ``uvDirection = 0`` (auto) compares the arclengths of
        the U and V isoparms at parameter 0.5 and picks the longer
        one, mirroring the source tool's behaviour and reversing the
        surface when V is longer (so callers always see a "U-long"
        surface).  ``uvDirection = 1`` / ``2`` honours the user's
        explicit choice without reversing anything."""
        if self.uv_dir == 1:
            self.uv_dir_resolved = 1
            return
        if self.uv_dir == 2:
            self.uv_dir_resolved = 0
            return
        # auto:
        u_curve = cmds.duplicateCurve(
            "%s.v[.5]" % self.surface, local=True, ch=False
        )
        v_curve = cmds.duplicateCurve(
            "%s.u[.5]" % self.surface, local=True, ch=False
        )
        try:
            u_len = cmds.arclen(u_curve)
            v_len = cmds.arclen(v_curve)
        finally:
            cmds.delete(u_curve + v_curve)
        if u_len >= v_len:
            self.uv_dir_resolved = 1
        else:
            cmds.reverseSurface(
                self.surface, direction=3, ch=False, rpo=True
            )
            cmds.reverseSurface(
                self.surface, direction=0, ch=False, rpo=True
            )
            self.uv_dir_resolved = 1

    # -- follicle + bind joint chain ----------------------------------

    def _attach_follicle(self, prefix, index, count):
        """Create one follicle attached to ``self.surface_shape`` at
        the index-th evenly-spaced parameter along the long axis.
        Returns the follicle's transform short name."""
        folli_shape = cmds.createNode(
            "follicle",
            name=naming.parse(
                [self.module_name, prefix, index], suffix="folliShape"
            ),
        )
        folli_xf = cmds.listRelatives(folli_shape, parent=True)[0]
        folli_xf = cmds.rename(
            folli_xf,
            naming.parse(
                [self.module_name, prefix, index], suffix="folli"
            ),
        )
        # Re-resolve the shape's full name in case rename changed it.
        folli_shape = cmds.listRelatives(
            folli_xf, shapes=True, type="follicle"
        )[0]
        cmds.connectAttr(
            "%s.local" % self.surface_shape,
            "%s.inputSurface" % folli_shape,
            force=True,
        )
        cmds.connectAttr(
            "%s.worldMatrix[0]" % self.surface_shape,
            "%s.inputWorldMatrix" % folli_shape,
            force=True,
        )
        cmds.connectAttr(
            "%s.outTranslate" % folli_shape,
            "%s.translate" % folli_xf,
            force=True,
        )
        cmds.connectAttr(
            "%s.outRotate" % folli_shape,
            "%s.rotate" % folli_xf,
            force=True,
        )
        param = float(index) / float(count - 1) if count > 1 else 0.5
        if self.uv_dir_resolved == 1:
            cmds.setAttr("%s.parameterU" % folli_shape, 0.5)
            cmds.setAttr("%s.parameterV" % folli_shape, param)
        else:
            cmds.setAttr("%s.parameterV" % folli_shape, 0.5)
            cmds.setAttr("%s.parameterU" % folli_shape, param)
        return folli_xf

    def _build_bind_chain(self):
        """Create ``self.joint_res`` follicles and corresponding
        ``_jDef`` joints, each parented under the previous bind joint
        to form a linear DAG hierarchy."""
        folli_grp = cmds.group(
            empty=True,
            name=naming.parse(
                [self.module_name, "bindFolli"], suffix="grp"
            ),
        )
        cmds.parent(folli_grp, self.nonScaleGrp)

        for i in range(self.joint_res):
            folli_xf = self._attach_follicle("bind", i, self.joint_res)
            cmds.parent(folli_xf, folli_grp)
            self.bind_follicles.append(folli_xf)

            cmds.select(clear=True)
            jdef = cmds.joint(
                name=naming.parse(
                    [self.module_name, i], suffix="jDef"
                ),
                radius=1.0,
            )
            # Track parameter on the joint (matches source tool's
            # ``Position`` attr) -- handy if Phase-2 deformers want to
            # query it.
            cmds.addAttr(
                jdef, longName="ribbonPosition",
                attributeType="float", keyable=True,
                minValue=0.0, maxValue=1.0,
                defaultValue=(i / float(self.joint_res - 1))
                if self.joint_res > 1 else 0.0,
            )
            cmds.setAttr("%s.ribbonPosition" % jdef, lock=True)

            cmds.parentConstraint(folli_xf, jdef, maintainOffset=False)

            if i == 0:
                cmds.parent(jdef, self.defJointsGrp)
            else:
                cmds.parent(jdef, self.bind_joints[i - 1])

            self.bind_joints.append(jdef)
            self.deformerJoints.append(jdef)
            self.sockets.append(jdef)

    # -- controllers ---------------------------------------------------

    def _build_controllers(self):
        """Create ``self.ctrl_res`` controllers spaced along the
        surface.  Each controller is positioned by sampling a
        temporary follicle on the surface (so the spacing matches the
        bind chain), then a DDRIG :class:`Controller` is aligned to
        that follicle's transform.  The controllers' transforms are
        skinned to the surface so animators can deform the ribbon by
        moving them; the bind chain follows for free via the bind
        follicles."""
        ctrl_grp = cmds.group(
            empty=True,
            name=naming.parse(
                [self.module_name, "ctrlFolli"], suffix="grp"
            ),
        )
        cmds.parent(ctrl_grp, self.nonScaleGrp)

        ctrl_scale_per_axis = max(self.total_length * 0.08, 0.1) * self.ctrl_size

        for i in range(self.ctrl_res):
            folli_xf = self._attach_follicle("ctrl", i, self.ctrl_res)
            cmds.parent(folli_xf, ctrl_grp)
            self.ctrl_follicles.append(folli_xf)

            # The ctrl joint sits at the follicle's world position;
            # we duplicate it to a top-level joint so we can skin it
            # to the surface independently of the follicle hierarchy.
            cmds.select(clear=True)
            ctrl_jnt = cmds.joint(
                name=naming.parse(
                    [self.module_name, "ctrlJ", i], suffix="j"
                ),
                radius=2.0,
            )
            functions.align_to(
                ctrl_jnt, folli_xf, position=True, rotation=True
            )
            cmds.makeIdentity(ctrl_jnt, apply=True)
            self.ctrl_joints.append(ctrl_jnt)

            # User-facing animator control sitting on top of the joint.
            cont = Controller(
                name=naming.parse(
                    [self.module_name, "ribbon", i], suffix="cont"
                ),
                shape="Circle",
                scale=(
                    ctrl_scale_per_axis,
                    ctrl_scale_per_axis,
                    ctrl_scale_per_axis,
                ),
                normal=(1, 0, 0),
                side=self.side,
                tier="primary",
            )
            functions.align_to(
                cont.name, ctrl_jnt, position=True, rotation=True
            )
            cont.add_offset("OFF")
            cont.freeze()
            self.controllers.append(cont)
            self.ctrl_objects.append(cont)

            # Drive ctrl_jnt with a matrixConstraint from the
            # controller -- moving the controller moves the ctrl joint,
            # which deforms the surface (since we skin it below).
            try:
                cmds.parentConstraint(
                    cont.name, ctrl_jnt, maintainOffset=False
                )
                cmds.scaleConstraint(
                    cont.name, ctrl_jnt, maintainOffset=False
                )
            except RuntimeError as exc:
                LOG.warning(
                    "ribbon: ctrl_jnt constraint failed: %s" % exc
                )

            cmds.parent(ctrl_jnt, self.scaleGrp)

        # Skin the surface to the ctrl joints.  ``smoothWeights`` /
        # ``maximumInfluences`` mirror the source tool's settings so
        # the deformation falloff matches what users got in the
        # standalone tool.
        try:
            cmds.skinCluster(
                self.ctrl_joints, self.surface,
                toSelectedBones=True,
                maximumInfluences=3,
                smoothWeights=0.5,
                name=naming.parse(
                    [self.module_name, "ribbon"], suffix="skin"
                ),
            )
        except RuntimeError as exc:
            LOG.warning("ribbon: surface skinCluster failed: %s" % exc)

    # -- pipeline ------------------------------------------------------

    def create_joints(self):
        """First half of the build: limbPlug, surface, follicles, bind
        joints.  Controllers are created in :meth:`create_controllers`
        so callers that want to inject custom controller logic can
        override that step alone."""
        cmds.select(deselect=True)
        self.limbPlug = cmds.joint(
            name=naming.parse([self.module_name, "plug"], suffix="j"),
            position=self._vec(self.init_positions[0]),
            radius=3,
        )
        cmds.parent(self.limbPlug, self.scaleGrp)

        self._build_surface()
        self._resolve_uv_direction()
        self._build_bind_chain()

    def create_controllers(self):
        """Second half of the build -- isolated so subclasses /
        Phase-2 deformer integrations can substitute their own
        controller layout without re-running :meth:`create_joints`."""
        self._build_controllers()

    def round_up(self):
        """Final wiring: scale-follow the rig root, hide rig-internal
        nodes, register scale constraints, restore controller
        defaults, lock controller scales (they shouldn't scale per
        user-tool semantics -- only ``ctrlSize`` at build time
        controls visual size)."""
        cmds.parentConstraint(
            self.limbPlug, self.scaleGrp, maintainOffset=False
        )
        cmds.setAttr("%s.rigVis" % self.scaleGrp, 0)

        for cont in self.ctrl_objects:
            cont.lock(["sx", "sy", "sz"])

        self.scaleConstraints = [self.scaleGrp]
        self.anchors = [(c.name, "parent", 1, None) for c in self.ctrl_objects]

        for cont in self.controllers:
            cont.set_defaults()

    def execute(self):
        self.create_joints()
        self.create_controllers()
        self.round_up()


# ----------------------------------------------------------------------
# Guides class
# ----------------------------------------------------------------------

class Guides(GuidesCore):
    name = "Ribbon"
    limb_data = LIMB_DATA

    def __init__(self, *args, **kwargs):
        super(Guides, self).__init__(*args, **kwargs)
        # ``segments`` is the count of *middle* ribbon guide joints
        # (i.e. excluding root and end).  Default 3 produces a 5-joint
        # init chain (root + 3 mids + end).
        self.segments = kwargs.get("segments", 3)

    def draw_joints(self):
        """Draw a straight chain along the side-aware reference vector.
        Joint count = ``self.segments + 2`` (root + middle segments +
        end).  ``RibbonRoot`` is the moduleName / side / useRefOri data
        source for the build class; ``Ribbon`` joints are the middle
        chain (consumed as a list because ``multi_guide="Ribbon"``);
        ``RibbonEnd`` marks the chain tip and is dropped if absent."""
        r_point = om.MVector(0, 14, 0) * self.tMatrix
        if self.side == "C":
            n_point = om.MVector(0, 14, 10) * self.tMatrix
        else:
            n_point = om.MVector(10 * self.sideMultiplier, 14, 0) * self.tMatrix

        total = self.segments + 1
        step = (n_point - r_point) / float(total)
        # The offset vector is used by Initials to position child guide
        # chains relative to a freshly-created module -- pointing it
        # along the chain direction matches what tentacle does.
        self.offsetVector = (n_point - r_point).normal()

        for seg in range(total + 1):
            jnt = cmds.joint(
                position=(r_point + (step * seg)),
                name=naming.parse(
                    [self.name, seg], side=self.side, suffix="jInit"
                ),
            )
            self.guideJoints.append(jnt)

        joint.orient_joints(
            self.guideJoints,
            world_up_axis=self.upVector,
            up_axis=(0, 1, 0),
            reverse_aim=self.sideMultiplier,
            reverse_up=self.sideMultiplier,
        )

    def define_guides(self):
        """Tag joint types: first = ``RibbonRoot``, last = ``RibbonEnd``,
        every middle joint = ``Ribbon``.  joint.identify uses these to
        bucket the guides into ``build_data`` (Ribbon -> list because
        of ``multi_guide``)."""
        if not self.guideJoints:
            return
        joint.set_joint_type(self.guideJoints[0], "RibbonRoot")
        if len(self.guideJoints) > 1:
            joint.set_joint_type(self.guideJoints[-1], "RibbonEnd")
        for jnt in self.guideJoints[1:-1]:
            joint.set_joint_type(jnt, "Ribbon")
