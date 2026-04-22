import os

from maya import cmds
from ddrig.library import scene
from ddrig.library import functions
from ddrig.library import attribute
from ddrig.library import joint
from ddrig.library import api
from ddrig.library import naming_rules

from ddrig.core import io
from ddrig.core import filelog
from ddrig.core import compatibility as compat

from ddrig.base import initials

log = filelog.Filelog(logname=__name__, filename="ddrig_log")


# --- session archive envelope format ---------------------------------------
# Legacy archives (pre-2.4) were a bare list[joint_dict].  New archives are
# {"metadata": {...}, "joints": [...]} so the namingRule that was active at
# save time can be embedded for future migration tools and UI hints.
# Load code accepts both shapes and treats a bare list as a legacy archive
# under the LEGACY_FALLBACK rule.
_ARCHIVE_FORMAT_VERSION = 1


def _wrap_archive(joints_data, rule_name):
    """Wrap a list of joint dicts into the versioned envelope."""
    return {
        "formatVersion": _ARCHIVE_FORMAT_VERSION,
        "metadata": {"namingRule": rule_name},
        "joints": joints_data,
    }


def _unwrap_archive(raw):
    """Normalise an archive blob into (joints_list, rule_name).

    Accepts the new envelope dict OR a legacy bare list.  Returns the
    joints list plus the rule name that was active when the archive was
    saved (LEGACY_FALLBACK for unmarked archives).
    """
    if isinstance(raw, dict) and "joints" in raw:
        joints = raw.get("joints", []) or []
        rule_name = (
            raw.get("metadata", {}).get("namingRule")
            or naming_rules.LEGACY_FALLBACK
        )
        return joints, rule_name
    # Legacy bare-list archive.
    return raw or [], naming_rules.LEGACY_FALLBACK


class Session(object):
    def __init__(self):
        super(Session, self).__init__()

        # at least a file name is necessary while instancing the IO
        self.io = io.IO(file_name="tmp_session.trg")
        self.init = initials.Initials()

    def save_session(self, file_path):
        """Saves the session to the given file path."""
        if not os.path.splitext(file_path)[1]:
            file_path = "%s.trg" % file_path
        self.io.file_path = file_path
        guides_data = self.collect_guides()
        archive = _wrap_archive(
            guides_data, naming_rules.get_active_rule_name()
        )
        self.io.write(archive)
        log.info("Session Saved Successfully...")

    def load_session(self, file_path, reset_scene=False):
        """Loads the session from the file.

        The archive's namingRule metadata is read for logging / future
        migration use, but joint DAG names are restored verbatim from the
        file (rebuild_guides uses the literal ``name`` field in each
        joint dict), so no rule-based re-styling happens on load.
        """
        if reset_scene:
            # self.reset_scene()
            scene.reset()
        guides_data, archive_rule = self._get_guides_data(file_path)
        if guides_data:
            if archive_rule != naming_rules.get_active_rule_name():
                log.info(
                    "Archive was saved under naming rule %r; current active "
                    "rule is %r. Joint names are restored verbatim." %
                    (archive_rule, naming_rules.get_active_rule_name())
                )
            self.rebuild_guides(guides_data)
            log.info("Guides Loaded Successfully...")
        else:
            log.error("Guides File doesn't exist or unreadable => %s" % file_path)
            raise Exception

    def get_roots_from_file(self, file_path):
        guides_data, _ = self._get_guides_data(file_path)
        for j in guides_data:
            if j["type"] in self.init.validRootList:
                yield j["name"]

    def _get_guides_data(self, file_path):
        """Returns ``(joints_list, namingRule)`` tuple.

        Accepts both the new envelope format and legacy bare-list files.
        Returns ``([], LEGACY_FALLBACK)`` if the file is missing/empty.
        """
        self.io.file_path = file_path
        raw = self.io.read()
        return _unwrap_archive(raw)

    def collect_guides(self):
        """Collect all necessary guide data ready to write"""

        all_root_jnts_data = self.init.get_scene_roots()
        root_joints_list = []

        all_ddrig_joints = []
        for r_dict in all_root_jnts_data:
            root_jnt = r_dict.get("root_joint")
            root_joints_list.append(root_jnt)
            limb_dict, _, __ = self.init.getWholeLimb(root_jnt)
            all_ddrig_joints.append(limb_dict.values())

        flat_jnt_list = list(compat.flatten(all_ddrig_joints))

        save_data = []

        for jnt in flat_jnt_list:
            cmds.select(d=True)
            tmp_jnt = cmds.joint()
            functions.align_to(tmp_jnt, jnt, position=True, rotation=True)
            world_pos = tuple(api.get_world_translation(tmp_jnt))
            rotation = cmds.getAttr("%s.rotate" % tmp_jnt)[0]
            joint_orient = cmds.getAttr("%s.jointOrient" % tmp_jnt)[0]
            # scale = cmds.getAttr("%s.scale" % jnt)[0]
            scale = (1, 1, 1)
            side = joint.get_joint_side(jnt)
            j_type = joint.get_joint_type(jnt)
            color = cmds.getAttr("%s.overrideColor" % jnt)
            radius = cmds.getAttr("%s.radius" % jnt)
            parent = functions.get_parent(jnt)
            if parent in flat_jnt_list:
                pass
            else:
                parent = None
            # get all custom attributes
            # this returns list of dictionaries compatible with create_attribute method in library.functions
            user_attrs = self.init.get_user_attrs(jnt)

            jnt_dict = {
                "name": jnt,
                "position": world_pos,
                "rotation": rotation,
                "joint_orient": joint_orient,
                "scale": scale,
                "parent": parent,
                "side": side,
                "type": j_type,
                "color": color,
                "radius": radius,
                "user_attributes": user_attrs,
            }
            save_data.append(jnt_dict)
            cmds.delete(tmp_jnt)
        return save_data

    def rebuild_guides(self, guides_data):
        """
        Rebuild all initial joints
        Args:
            guides_data: [list] List of dictionaries. Output from 'collect_initials' method

        Returns: None

        """
        holder_grp = "%s_refGuides" % self.init.projectName
        if not cmds.objExists(holder_grp):
            holder_grp = cmds.group(name=holder_grp, em=True)
        for jnt_dict in guides_data:
            cmds.select(d=True)
            jnt = cmds.joint(name=jnt_dict.get("name"), p=jnt_dict.get("position"))
            attribute.create_global_joint_attrs(jnt)
            cmds.setAttr("%s.rotate" % jnt, *jnt_dict.get("rotation"))
            cmds.setAttr("%s.jointOrient" % jnt, *jnt_dict.get("joint_orient"))
            cmds.setAttr("%s.scale" % jnt, *jnt_dict.get("scale"))
            cmds.setAttr("%s.radius" % jnt, jnt_dict.get("radius"))
            cmds.setAttr("%s.drawLabel" % jnt, 1)
            cmds.setAttr("%s.displayLocalAxis" % jnt, 1)
            cmds.setAttr("%s.overrideEnabled" % jnt, True)
            cmds.setAttr("%s.overrideColor" % jnt, jnt_dict.get("color"))
            joint.set_joint_side(jnt, jnt_dict.get("side"))
            joint.set_joint_type(jnt, jnt_dict.get("type"))
            property_attrs = jnt_dict.get("user_attributes")
            for attr_dict in property_attrs:
                attribute.create_attribute(jnt, attr_dict)

        for jnt_dict in guides_data:
            if jnt_dict.get("parent"):
                cmds.parent(jnt_dict.get("name"), jnt_dict.get("parent"))
            else:
                cmds.parent(jnt_dict.get("name"), holder_grp)

    def reset_scene(self):
        scene.reset()
