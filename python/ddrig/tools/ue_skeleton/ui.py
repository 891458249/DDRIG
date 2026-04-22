"""Qt dialog wrapper around :mod:`ddrig.tools.ue_skeleton`.

Non-modal dialog hosting three buttons (Build, Transfer Skin, Delete)
and a log panel that accumulates the results of each operation so the
user can read through warnings without losing them to the script
editor.

Launch from Tools > Build UE Skeleton... or programmatically::

    from ddrig.tools.ue_skeleton.ui import UESkeletonDialog
    UESkeletonDialog(parent=None).show()
"""
from __future__ import annotations

import traceback

from ddrig.ui.Qt import QtCore, QtWidgets
from ddrig.tools.ue_skeleton import builder, skin_swap


# objectName starts with "DDRIG " so hotswap._close_qt_windows picks it
# up via the shared prefix sweep during uninstall / reinstall.
WINDOW_OBJECT_NAME = "DDRIG UE Skeleton"


class UESkeletonDialog(QtWidgets.QDialog):
    """Build / Transfer / Delete operations on the parallel UE skeleton."""

    def __init__(self, parent=None):
        super(UESkeletonDialog, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("DDRIG -- UE Export Skeleton")
        self.setMinimumSize(560, 420)
        # Non-modal: the user may operate in Maya while this is open.
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self._build_ui()

    # ---- UI construction ------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # ---- Settings form (root name / group name / suffix) ----
        settings_box = QtWidgets.QGroupBox("Settings")
        form = QtWidgets.QFormLayout(settings_box)
        self.root_name_le = QtWidgets.QLineEdit(builder.UE_ROOT)
        self.group_name_le = QtWidgets.QLineEdit(builder.UE_GROUP)
        self.suffix_le = QtWidgets.QLineEdit(builder.UE_SUFFIX)
        self.suffix_le.setMaxLength(16)
        form.addRow("Root joint name", self.root_name_le)
        form.addRow("Skeleton group name", self.group_name_le)
        form.addRow("jUE suffix", self.suffix_le)
        layout.addWidget(settings_box)

        # ---- Build row -------------------------------------------------
        build_box = QtWidgets.QGroupBox("Skeleton")
        build_lay = QtWidgets.QVBoxLayout(build_box)

        build_hlay = QtWidgets.QHBoxLayout()
        self.build_btn = QtWidgets.QPushButton("Build Skeleton")
        self.build_btn.setToolTip(
            "Create ue_skeleton_grp with a single-root hierarchy mirroring "
            "every _jDef joint in the scene.  Fails if the group already "
            "exists -- use Rebuild for idempotent re-creation."
        )
        self.rebuild_btn = QtWidgets.QPushButton("Rebuild (delete + build)")
        self.rebuild_btn.setToolTip(
            "Delete any existing ue_skeleton_grp then build from scratch. "
            "Safe to run after rig changes."
        )
        self.delete_btn = QtWidgets.QPushButton("Delete Skeleton")
        self.delete_btn.setToolTip(
            "Remove ue_skeleton_grp (and its constraints). "
            "Does not touch source _jDef joints or skin clusters."
        )
        build_hlay.addWidget(self.build_btn)
        build_hlay.addWidget(self.rebuild_btn)
        build_hlay.addWidget(self.delete_btn)
        build_lay.addLayout(build_hlay)

        layout.addWidget(build_box)

        # ---- Skin transfer row ----------------------------------------
        skin_box = QtWidgets.QGroupBox("Skin influences")
        skin_lay = QtWidgets.QVBoxLayout(skin_box)

        self.remove_jdef_cb = QtWidgets.QCheckBox(
            "Remove _jDef from skinClusters after transfer"
        )
        self.remove_jdef_cb.setToolTip(
            "Off (default): keep both _jDef and _jUE as influences with "
            "weight migrated to _jUE.  The original rig still drives the "
            "mesh correctly if you roll back.\n\n"
            "On: strip _jDef out of every affected skinCluster after the "
            "transfer.  Smaller file, but not round-trippable."
        )
        skin_lay.addWidget(self.remove_jdef_cb)

        skin_hlay = QtWidgets.QHBoxLayout()
        self.dry_run_btn = QtWidgets.QPushButton("Preview Skin Transfer (dry run)")
        self.transfer_btn = QtWidgets.QPushButton("Transfer Skin Weights")
        self.transfer_btn.setToolTip(
            "For every skinCluster: add the matching _jUE as an influence "
            "(if not present), copy per-vertex weights from _jDef to _jUE, "
            "and zero the _jDef weights."
        )
        skin_hlay.addWidget(self.dry_run_btn)
        skin_hlay.addWidget(self.transfer_btn)
        skin_lay.addLayout(skin_hlay)

        layout.addWidget(skin_box)

        # ---- Log panel -------------------------------------------------
        log_box = QtWidgets.QGroupBox("Log")
        log_lay = QtWidgets.QVBoxLayout(log_box)
        self.log_te = QtWidgets.QPlainTextEdit()
        self.log_te.setReadOnly(True)
        self.log_te.setStyleSheet("font-family: Consolas, monospace;")
        self.clear_log_btn = QtWidgets.QPushButton("Clear log")
        self.clear_log_btn.setFixedWidth(100)
        log_lay.addWidget(self.log_te, 1)
        log_lay.addWidget(self.clear_log_btn, 0, QtCore.Qt.AlignRight)
        layout.addWidget(log_box, 1)

        # ---- Close button ---------------------------------------------
        close_hlay = QtWidgets.QHBoxLayout()
        close_hlay.addStretch(1)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setFixedWidth(100)
        close_hlay.addWidget(self.close_btn)
        layout.addLayout(close_hlay)

        # ---- Signals --------------------------------------------------
        self.build_btn.clicked.connect(self.on_build)
        self.rebuild_btn.clicked.connect(self.on_rebuild)
        self.delete_btn.clicked.connect(self.on_delete)
        self.dry_run_btn.clicked.connect(self.on_transfer_dry_run)
        self.transfer_btn.clicked.connect(self.on_transfer_live)
        self.clear_log_btn.clicked.connect(self.log_te.clear)
        self.close_btn.clicked.connect(self.close)

    # ---- Logging helpers -----------------------------------------------

    def _log(self, msg):
        """Append a line to the log panel and auto-scroll to bottom."""
        self.log_te.appendPlainText(msg)
        sb = self.log_te.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _log_exception(self, prefix):
        self._log("%s\n%s" % (prefix, traceback.format_exc()))

    def _current_kwargs(self):
        """Snapshot the three name fields with fallback to defaults."""
        return {
            "root_name": self.root_name_le.text().strip() or builder.UE_ROOT,
            "group_name": self.group_name_le.text().strip() or builder.UE_GROUP,
            "suffix": self.suffix_le.text().strip() or builder.UE_SUFFIX,
        }

    def _log_build_result(self, result):
        """Pretty-print the dict returned by build_ue_skeleton /
        rebuild_ue_skeleton."""
        self._log(
            "Built skeleton: root=%s, group=%s, %d jUE joints created."
            % (result["root"], result["group"], len(result["created"]))
        )
        chains = result.get("module_chains") or {}
        if chains:
            self._log("Module chains:")
            for mod, chain in chains.items():
                self._log("  %s: %d joints" % (mod, len(chain)))
        skipped = result.get("skipped") or []
        if skipped:
            self._log("Skipped (%d):" % len(skipped))
            for item, reason in skipped:
                self._log("  - %s: %s" % (item, reason))

    # ---- Slot handlers -------------------------------------------------

    def on_build(self):
        kw = self._current_kwargs()
        self._log("=== Build Skeleton ===")
        try:
            result = builder.build_ue_skeleton(**kw)
        except RuntimeError as exc:
            self._log("Build failed: %s" % exc)
            return
        except Exception:   # noqa: BLE001
            self._log_exception("Build raised an unexpected error:")
            return
        self._log_build_result(result)

    def on_rebuild(self):
        kw = self._current_kwargs()
        self._log("=== Rebuild Skeleton (delete + build) ===")
        try:
            result = builder.rebuild_ue_skeleton(**kw)
        except RuntimeError as exc:
            self._log("Rebuild failed: %s" % exc)
            return
        except Exception:   # noqa: BLE001
            self._log_exception("Rebuild raised an unexpected error:")
            return
        self._log_build_result(result)

    def on_delete(self):
        group = self._current_kwargs()["group_name"]
        self._log("=== Delete Skeleton ===")
        try:
            removed = builder.delete_ue_skeleton(group_name=group)
        except Exception:   # noqa: BLE001
            self._log_exception("Delete raised an unexpected error:")
            return
        if removed:
            self._log("Removed group %r (and everything under it)." % group)
        else:
            self._log("Nothing to delete: %r did not exist." % group)

    def _run_transfer(self, dry_run):
        remove = self.remove_jdef_cb.isChecked()
        label = "dry run" if dry_run else "live"
        self._log("=== Transfer Skin Weights (%s%s) ===" %
                  (label, ", remove _jDef" if remove else ""))
        try:
            result = skin_swap.transfer_skin_to_ue(
                remove_jdef_influence=remove, dry_run=dry_run
            )
        except Exception:   # noqa: BLE001
            self._log_exception("Transfer raised an unexpected error:")
            return
        self._log(
            "skinclusters processed : %d" % result["skinclusters_processed"]
        )
        self._log(
            "influences added       : %d" % result["influences_added"]
        )
        self._log(
            "influences removed     : %d" % result["influences_removed"]
        )
        self._log(
            "vertices touched       : %d" % result["vertices_touched"]
        )
        warnings = result.get("warnings") or []
        if warnings:
            self._log("Warnings (%d):" % len(warnings))
            for w in warnings:
                self._log("  - %s" % w)

    def on_transfer_dry_run(self):
        self._run_transfer(dry_run=True)

    def on_transfer_live(self):
        self._run_transfer(dry_run=False)
