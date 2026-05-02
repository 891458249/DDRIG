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
from ddrig.library import attribute
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
        # ---- Phase 2: nonLinear deformer counts ----
        # Each adds a sine / wave / bend / twist nonLinear deformer to
        # the ribbon surface; per-deformer attrs are exposed on the
        # ``main`` controller (the first ctrl in self.ctrl_objects).
        # Default 0 = no deformers built, Phase 1 behaviour preserved.
        {
            "attr_name": "sineCount",
            "nice_name": "Sine_Count",
            "attr_type": "long",
            "min_value": 0,
            "max_value": 5,
            "default_value": 0,
        },
        {
            "attr_name": "waveCount",
            "nice_name": "Wave_Count",
            "attr_type": "long",
            "min_value": 0,
            "max_value": 5,
            "default_value": 0,
        },
        {
            "attr_name": "bendCount",
            "nice_name": "Bend_Count",
            "attr_type": "long",
            "min_value": 0,
            "max_value": 5,
            "default_value": 0,
        },
        {
            "attr_name": "twistCount",
            "nice_name": "Twist_Count",
            "attr_type": "long",
            "min_value": 0,
            "max_value": 5,
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
        # Phase 2 deformer counts -- guarded by attributeQuery so an
        # older guide (built before Phase 2) without these attrs falls
        # back to 0 cleanly without crashing the build.
        self.sine_count = self._read_count_attr("sineCount")
        self.wave_count = self._read_count_attr("waveCount")
        self.bend_count = self._read_count_attr("bendCount")
        self.twist_count = self._read_count_attr("twistCount")

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
        # Main controller used to host Phase 2 deformer attrs;
        # populated at the end of ``_build_controllers`` from the
        # first entry in ``self.ctrl_objects``.
        self.main_ctrl = None
        self.deformer_grp = None

    # -- helpers -------------------------------------------------------

    def _read_count_attr(self, attr_name):
        """Robustly read one of the Phase 2 ``*Count`` attrs off the
        root guide, falling back to 0 if the attribute is missing
        (older guide created before Phase 2 was added)."""
        node = self.inits[0]
        if not cmds.attributeQuery(attr_name, node=node, exists=True):
            return 0
        try:
            return int(cmds.getAttr("%s.%s" % (node, attr_name)))
        except (ValueError, RuntimeError):
            return 0

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
        offset is split symmetrically (+/- W/2) along the module's
        ``mirror_axis`` so the init chain sits on the surface's
        center line (U/V = 0.5).  Follicles default to parameter 0.5
        too, which means bind joints land exactly on the guide chain
        instead of being offset by a full +W on one side."""
        width = max(self.total_length * 0.05, 1.0)
        half_width = width * 0.5
        mirror = om.MVector(self.mirror_axis).normal()
        if mirror.length() < 1e-6:
            mirror = om.MVector(1, 0, 0)
        pts_a = [
            self._vec(om.MVector(p) - mirror * half_width)
            for p in self.init_positions
        ]
        pts_b = [
            self._vec(om.MVector(p) + mirror * half_width)
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

        # If a dedicated module-level main controller has been created
        # (see :meth:`_build_main_controller`), reparent every
        # per-segment ctrl underneath it so the rig has a single
        # selectable handle that drags the whole ribbon, mirroring
        # tentacle's hierarchy.  We reparent the topmost OFFSET
        # transform (not the controller itself) so the animator's
        # transform channels stay clean.
        if self.main_ctrl and cmds.objExists(self.main_ctrl):
            for cont in self.ctrl_objects:
                offsets = cont.get_offsets() or []
                top = offsets[0] if offsets else cont.name
                if not cmds.objExists(top):
                    continue
                current_parent = cmds.listRelatives(
                    top, parent=True, fullPath=True
                ) or []
                cur_short = (
                    current_parent[0].rsplit("|", 1)[-1]
                    if current_parent else None
                )
                if cur_short == self.main_ctrl:
                    continue
                try:
                    cmds.parent(top, self.main_ctrl)
                except RuntimeError as exc:
                    LOG.warning(
                        "ribbon: reparenting %s under %s failed: %s"
                        % (top, self.main_ctrl, exc)
                    )

    def _build_main_controller(self):
        """Create the module-level main controller.

        The main controller sits at the root guide's world position
        and serves three purposes: (1) it's the parent of every
        per-segment controller, so a single selection drags the whole
        ribbon, (2) it's the host for the Phase 2 deformer attrs, and
        (3) it gives the animator a clear handle to anchor / parent
        the module to other rigs.

        Mirrors tentacle's main-controller pattern: a Square shape,
        scaled relative to chain length, side-coloured, registered
        through the standard :attr:`controllers` list so DDRIG's
        ModuleCore parents it under :attr:`controllerGrp` via the
        usual pipeline."""
        if self.main_ctrl and cmds.objExists(self.main_ctrl):
            # Already created (e.g. caller stacked _build_main twice).
            return
        size = max(self.total_length * 0.18, 0.5) * self.ctrl_size
        # Use look_axis as the controller's normal so the Square sits
        # perpendicular to the chain (visually obvious).  Fall back to
        # +Y for degenerate axis vectors.
        look = om.MVector(self.look_axis)
        if look.length() < 1e-6:
            look = om.MVector(0, 1, 0)
        look = look.normal()
        main = Controller(
            name=naming.parse(
                [self.module_name, "main"], suffix="cont"
            ),
            shape="Square",
            scale=(size, size, size),
            normal=(look[0], look[1], look[2]),
            side=self.side,
            tier="primary",
        )
        # Snap to root world position; freeze so the controller's
        # transform reads (0, 0, 0) at rest.
        try:
            cmds.xform(
                main.name, worldSpace=True,
                translation=self._vec(self.init_positions[0]),
            )
        except RuntimeError:
            pass
        main.add_offset("OFF")
        main.freeze()
        self.controllers.append(main)
        self.main_ctrl = main.name

    # -- Phase 2: nonLinear deformers ---------------------------------

    def _build_deformers(self):
        """Add nonLinear deformers (sine / wave / bend / twist) to the
        ribbon surface, with per-deformer attrs exposed on
        :attr:`main_ctrl`.  Counts are read from the guide root's
        ``sineCount`` / ``waveCount`` / ``bendCount`` / ``twistCount``
        attrs.  When all four are 0 (default) this method is a no-op
        and Phase 1 behaviour is preserved exactly.

        Mirrors ``createDeformer`` from the user's
        ``ribbon_rig_tool_freelancer3.2`` adapted to DDRIG containers
        (deformer transforms parented under
        ``{module}_deformer_grp -> nonScaleGrp``; main_ctrl drives the
        deformer group via parent + scale constraint with
        ``maintainOffset=True``)."""
        total = (
            self.sine_count + self.wave_count
            + self.bend_count + self.twist_count
        )
        if total <= 0:
            return
        if not self.main_ctrl or not cmds.objExists(self.main_ctrl):
            LOG.warning(
                "ribbon: no main_ctrl available, skipping deformer creation"
            )
            return
        if not self.surface or not cmds.objExists(self.surface):
            LOG.warning(
                "ribbon: surface missing, skipping deformer creation"
            )
            return

        self.deformer_grp = cmds.group(
            empty=True,
            name=naming.parse(
                [self.module_name, "deformer"], suffix="grp"
            ),
        )
        cmds.parent(self.deformer_grp, self.nonScaleGrp)

        for deform_type, count in (
            ("sine", self.sine_count),
            ("wave", self.wave_count),
            ("bend", self.bend_count),
            ("twist", self.twist_count),
        ):
            for idx in range(1, count + 1):
                self._build_one_deformer(deform_type, idx)

        # main_ctrl drives the whole deformer group with offset, so
        # animators can move the rig root and the deformers come
        # along.  ``maintainOffset=True`` records the rest pose
        # offset; the constraint only follows main_ctrl's relative
        # changes from there.
        try:
            cmds.parentConstraint(
                self.main_ctrl, self.deformer_grp, maintainOffset=True
            )
            cmds.scaleConstraint(
                self.main_ctrl, self.deformer_grp, maintainOffset=True
            )
        except RuntimeError as exc:
            LOG.warning(
                "ribbon: deformer_grp constraint failed: %s" % exc
            )

        self._restore_deformer_scale()

    def _build_one_deformer(self, deform_type, idx):
        """Create one nonLinear deformer of the given type, mirror its
        attrs onto :attr:`main_ctrl`, connect ctrl -> deformer, aim
        the deformer transform along the chain (skipped for ``wave``,
        matching the source tool's behaviour), and parent it under
        the deformer group."""
        short = deform_type[0]
        sn_idx = str(idx)

        # Separator label on main_ctrl.
        sep_attr = "%s_%s_deform" % (deform_type, sn_idx)
        if not cmds.attributeQuery(sep_attr, node=self.main_ctrl, exists=True):
            cmds.addAttr(
                self.main_ctrl,
                longName=sep_attr,
                attributeType="enum",
                enumName="------------:",
                keyable=True,
            )
            cmds.setAttr(
                "%s.%s" % (self.main_ctrl, sep_attr),
                keyable=False, channelBox=True, lock=True,
            )

        # Envelope is universal across nonLinear deformers.
        cmds.addAttr(
            self.main_ctrl,
            longName="%s_%s_envelope" % (deform_type, sn_idx),
            shortName="%s%se" % (short, sn_idx),
            attributeType="float",
            keyable=True, defaultValue=0, minValue=0, maxValue=1,
        )

        # Type-specific attrs (mirrors source tool line 805-819).
        if deform_type == "bend":
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_curvature" % (deform_type, sn_idx),
                shortName="%s%sc" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-180, maxValue=180,
            )
        elif deform_type in ("sine", "wave"):
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_amplitude" % (deform_type, sn_idx),
                shortName="%s%sa" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0.1,
                minValue=-5, maxValue=5,
            )
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_wavelength" % (deform_type, sn_idx),
                shortName="%s%sw" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=2,
                minValue=0.1, maxValue=10,
            )
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_offset" % (deform_type, sn_idx),
                shortName="%s%so" % (short, sn_idx),
                attributeType="float", keyable=True,
            )
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_dropoff" % (deform_type, sn_idx),
                shortName="%s%sd" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-1, maxValue=1,
            )
        elif deform_type == "twist":
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_startAngle" % (deform_type, sn_idx),
                shortName="%s%ssa" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-180, maxValue=180,
            )
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_endAngle" % (deform_type, sn_idx),
                shortName="%s%sea" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-180, maxValue=180,
            )

        # Wave gets an extra "dropoffPosition" channel.
        if deform_type == "wave":
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_dropoffPosition" % (deform_type, sn_idx),
                shortName="%s%sdp" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-1, maxValue=1,
            )

        # Twist / bend / sine all expose lowBound + highBound so
        # animators can constrain the deformer's range along the
        # ribbon's long axis.
        if deform_type in ("twist", "bend", "sine"):
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_lowBound" % (deform_type, sn_idx),
                shortName="%s%slb" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=0,
                minValue=-10, maxValue=0,
            )
            cmds.addAttr(
                self.main_ctrl,
                longName="%s_%s_highBound" % (deform_type, sn_idx),
                shortName="%s%shb" % (short, sn_idx),
                attributeType="float",
                keyable=True, defaultValue=2,
                minValue=0, maxValue=10,
            )

        # Create the actual nonLinear deformer node.  cmds.nonLinear
        # returns [deformer_node, deformer_handle_transform].  Rename
        # both for readability + DDRIG naming conventions.
        result = cmds.nonLinear(
            self.surface,
            type=deform_type,
            name=naming.parse(
                [self.module_name, deform_type, idx], suffix="def"
            ),
        )
        deformer_node = result[0]
        deformer_handle = result[1]
        deformer_handle = cmds.rename(
            deformer_handle,
            naming.parse(
                [self.module_name, deform_type, idx], suffix="handle"
            ),
        )

        # Wire each ctrl attr we just added to the matching channel on
        # the deformer node (envelope, amplitude, wavelength, ...).
        ctrl_attrs = cmds.listAttr(
            self.main_ctrl, keyable=True, userDefined=True
        ) or []
        prefix = "%s_%s" % (deform_type, sn_idx)
        for attr in ctrl_attrs:
            if not attr.startswith(prefix):
                continue
            node_attr = attr.split("_")[-1]
            if not cmds.attributeQuery(
                node_attr, node=deformer_node, exists=True
            ):
                continue
            src = "%s.%s" % (self.main_ctrl, attr)
            dst = "%s.%s" % (deformer_node, node_attr)
            if not cmds.isConnected(src, dst):
                try:
                    cmds.connectAttr(src, dst, force=True)
                except RuntimeError as exc:
                    LOG.warning(
                        "ribbon: connectAttr %s -> %s failed: %s"
                        % (src, dst, exc)
                    )

        # Aim the deformer transform along the ribbon (skipped for
        # ``wave`` -- source tool keeps wave aligned to its default
        # orientation).  Aim target is the last bind follicle; we
        # build a temp aim transform at that follicle's world
        # transform, snap the handle onto main_ctrl's position, then
        # aim the handle at the temp object and bake by deleting the
        # constraint.
        if deform_type != "wave" and self.bind_follicles:
            aim_target = self.bind_follicles[-1]
            try:
                temp_aim = cmds.createNode(
                    "transform",
                    name=naming.parse(
                        [self.module_name, deform_type, idx, "tempAim"],
                        suffix="grp",
                    ),
                )
                tmp_pc = cmds.parentConstraint(
                    aim_target, temp_aim, maintainOffset=False
                )
                cmds.delete(tmp_pc)
                cmds.matchTransform(
                    deformer_handle, self.main_ctrl, position=True
                )
                tmp_ac = cmds.aimConstraint(
                    temp_aim, deformer_handle,
                    aimVector=[0, 1, 0],
                    upVector=[1, 0, 0],
                    worldUpType="vector",
                    worldUpVector=[0, 1, 0],
                    maintainOffset=False,
                )
                cmds.delete(tmp_ac)
                cmds.delete(temp_aim)
            except RuntimeError as exc:
                LOG.warning(
                    "ribbon: deformer aim setup failed for %s: %s"
                    % (deformer_handle, exc)
                )

        cmds.parent(deformer_handle, self.deformer_grp)
        cmds.select(clear=True)

    def _restore_deformer_scale(self):
        """Set every deformer transform's scale to half the bind chain
        length.  The deformer's ``lowBound`` / ``highBound`` are
        normalised to the deformer's local scale, so unless we scale
        the handle to span the ribbon the deformer's effective range
        is tiny relative to the actual surface.  Mirrors the source
        tool's ``restoreDeformerScale``."""
        if not self.deformer_grp:
            return
        if not self.bind_joints or len(self.bind_joints) < 2:
            return
        try:
            p_first = cmds.xform(
                self.bind_joints[0],
                query=True, worldSpace=True, translation=True,
            )
            p_last = cmds.xform(
                self.bind_joints[-1],
                query=True, worldSpace=True, translation=True,
            )
        except RuntimeError:
            return
        distance = sum(
            (a - b) ** 2 for a, b in zip(p_first, p_last)
        ) ** 0.5
        if distance <= 0:
            return
        scale_val = distance / 2.0
        deformer_xforms = cmds.listRelatives(
            self.deformer_grp, allDescendents=True, type="transform"
        ) or []
        for d in deformer_xforms:
            for ax in ("sx", "sy", "sz"):
                try:
                    if cmds.getAttr("%s.%s" % (d, ax)) != scale_val:
                        cmds.setAttr("%s.%s" % (d, ax), scale_val)
                except RuntimeError:
                    pass

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
        controller layout without re-running :meth:`create_joints`.

        Order matters:
          1. ``_build_main_controller`` -- creates the module-level
             main controller and sets ``self.main_ctrl`` to it.
          2. ``_build_controllers``      -- creates per-segment ctrls
             and reparents them under main_ctrl at the end.
          3. ``_build_deformers``        -- creates nonLinear deformers
             whose attrs are hosted on main_ctrl.  No-op when every
             deformer count is 0 (default), so Phase 1 behaviour is
             preserved exactly."""
        self._build_main_controller()
        self._build_controllers()
        self._build_deformers()

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

    def define_attributes(self):
        """Override the base behaviour to force-create every LIMB_DATA
        property on EVERY guide joint -- including the root.

        ``ddrig.base.initials.Initials.get_property`` warns
        ``"Attribute cannot find {jnt}.{attr}"`` whenever
        ``cmds.getAttr`` raises ``ValueError``.  The UI's
        ``_refresh_module_props`` queries every property listed in
        ``LIMB_DATA["properties"]`` against the module root joint, so
        any missing attribute on the root produces one warning per
        selection.

        Two failure modes both lead to that warning:

        1. **Stale guide**: the user built the ribbon guide chain in
           an older session, before a property (e.g. the four Phase 2
           ``*Count`` attrs) existed.  ``GuidesCore.define_attributes``
           ran with the older ``LIMB_DATA`` and never created the new
           properties on the root.
        2. **Segment guides**: any code path that iterates every
           guide joint (e.g. ``base/initials.py`` walks) finds segment
           guides missing the properties even though the root has them.

        Calling ``super`` first preserves base-class side-effects
        (joint sides, global axis attrs, root property creation),
        then we walk **every** guide joint -- root included -- and
        back-fill any missing property.  The
        ``attributeQuery exists`` guard makes the operation idempotent
        on freshly-created guides; the ``try/except RuntimeError``
        swallows the benign 'attribute already exists' that
        ``addAttr`` raises if a name collision sneaks past the guard
        (e.g. a parent class added a similarly-named attribute first).

        Functional behaviour is unchanged: the build class still
        reads from ``self.inits[0]``."""
        super(Guides, self).define_attributes()
        if not self.guideJoints:
            return
        for jnt in self.guideJoints:
            if not jnt or not cmds.objExists(jnt):
                continue
            for attr_dict in self.limb_data["properties"]:
                attr_name = attr_dict["attr_name"]
                if cmds.attributeQuery(attr_name, node=jnt, exists=True):
                    continue
                try:
                    attribute.create_attribute(jnt, attr_dict)
                except RuntimeError:
                    # Benign duplicate -- some attribute creation
                    # paths race or normalise names; ignore.
                    pass
                except Exception as exc:   # noqa: BLE001
                    LOG.warning(
                        "ribbon Guides: failed creating %s on %s: %s"
                        % (attr_name, jnt, exc)
                    )
