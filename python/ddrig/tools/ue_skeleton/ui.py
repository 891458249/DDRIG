"""Qt dialog wrapper around :mod:`ddrig.tools.ue_skeleton`.

Layout (top to bottom)::

    Module status table (snapshot of the current scene: guide count,
                         build status per module)            [Refresh]

    Settings:  Data source   (o)Guide  (o)Rig  (*)Auto
               Granularity   (*)Main   (o)Include Twist (rig only)
               Root / Group / Suffix name fields

    Skeleton:  [Build]  [Rebuild]  [Delete]

    Skin:      [ ] Remove _jDef after transfer
               [Preview (dry run)]  [Transfer]

    Log panel + Clear + Close

Source = Guide disables the "Include Twist" radio (twist requires rig).
Source = Rig pops a confirmation dialog listing unbuilt modules before
building (otherwise those modules would be skipped silently).
"""
from __future__ import annotations

import traceback

from ddrig.ui.Qt import QtCore, QtWidgets
from ddrig.tools.ue_skeleton import builder, skin_swap


# objectName begins with "DDRIG " so ddrig.tools.hotswap._close_qt_windows
# sweeps this dialog up during uninstall / reinstall.
WINDOW_OBJECT_NAME = "DDRIG UE Skeleton"


class UESkeletonDialog(QtWidgets.QDialog):
    """Non-modal operation console for the UE skeleton builder."""

    def __init__(self, parent=None):
        super(UESkeletonDialog, self).__init__(parent)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("DDRIG -- UE Export Skeleton")
        self.setMinimumSize(680, 620)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self._build_ui()
        self._refresh_status_table()
        self._sync_granularity_enablement()

    # ---- UI construction ------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # ---- Module status table ---------------------------------------
        status_box = QtWidgets.QGroupBox("Module status (current scene)")
        status_lay = QtWidgets.QVBoxLayout(status_box)
        self.status_table = QtWidgets.QTableWidget(0, 6)
        self.status_table.setHorizontalHeaderLabels([
            "Name", "Type", "Side", "Guide", "Build status", "Detected deform",
        ])
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectRows
        )
        self.status_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.status_table.setAlternatingRowColors(True)
        header = self.status_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.status_table.setMinimumHeight(140)
        status_lay.addWidget(self.status_table)

        status_btn_lay = QtWidgets.QHBoxLayout()
        self.dump_report_btn = QtWidgets.QPushButton("Dump Detection Report")
        self.dump_report_btn.setToolTip(
            "Log the scene-wide deform-joint detection result per module "
            "without building anything.  Useful for verifying that every "
            "module is correctly recognised as built before running Build."
        )
        self.refresh_status_btn = QtWidgets.QPushButton("Refresh Status")
        status_btn_lay.addStretch(1)
        status_btn_lay.addWidget(self.dump_report_btn)
        status_btn_lay.addWidget(self.refresh_status_btn)
        status_lay.addLayout(status_btn_lay)
        layout.addWidget(status_box)

        # ---- Settings group --------------------------------------------
        settings_box = QtWidgets.QGroupBox("Settings")
        settings_lay = QtWidgets.QFormLayout(settings_box)

        # Data source (radio group)
        self.source_group = QtWidgets.QButtonGroup(self)
        self.source_guide_rb = QtWidgets.QRadioButton("Guide")
        self.source_guide_rb.setToolTip(
            "Topology + positions from guide joints (_jInit).\n"
            "No animation drivers. Works even with no module built."
        )
        self.source_rig_rb = QtWidgets.QRadioButton("Rig")
        self.source_rig_rb.setToolTip(
            "Requires every module to have _jDef (Test Built).\n"
            "Unbuilt modules are skipped."
        )
        self.source_auto_rb = QtWidgets.QRadioButton("Auto (recommended)")
        self.source_auto_rb.setToolTip(
            "Per-module: rig if available, guide fallback otherwise.\n"
            "Never skips a module."
        )
        self.source_auto_rb.setChecked(True)
        for rb in (self.source_guide_rb, self.source_rig_rb, self.source_auto_rb):
            self.source_group.addButton(rb)
        src_lay = QtWidgets.QHBoxLayout()
        src_lay.addWidget(self.source_guide_rb)
        src_lay.addWidget(self.source_rig_rb)
        src_lay.addWidget(self.source_auto_rb)
        src_lay.addStretch(1)
        settings_lay.addRow("Data source", src_lay)

        # Granularity (radio group)
        self.gran_group = QtWidgets.QButtonGroup(self)
        self.gran_main_rb = QtWidgets.QRadioButton("Main only")
        self.gran_main_rb.setToolTip(
            "One _jUE per guide joint (e.g. arm = 4)."
        )
        self.gran_main_rb.setChecked(True)
        self.gran_full_rb = QtWidgets.QRadioButton("Include Twist (rig only)")
        self.gran_full_rb.setToolTip(
            "Main joints + every twist/ribbon _jDef between consecutive\n"
            "guides (e.g. arm = 13). Guide-sourced modules degrade to Main."
        )
        self.gran_group.addButton(self.gran_main_rb)
        self.gran_group.addButton(self.gran_full_rb)
        gran_lay = QtWidgets.QHBoxLayout()
        gran_lay.addWidget(self.gran_main_rb)
        gran_lay.addWidget(self.gran_full_rb)
        gran_lay.addStretch(1)
        settings_lay.addRow("Granularity", gran_lay)

        # Name fields
        self.root_name_le = QtWidgets.QLineEdit(builder.UE_ROOT)
        self.group_name_le = QtWidgets.QLineEdit(builder.UE_GROUP)
        self.suffix_le = QtWidgets.QLineEdit(builder.UE_SUFFIX)
        self.suffix_le.setMaxLength(16)
        settings_lay.addRow("Root joint name", self.root_name_le)
        settings_lay.addRow("Skeleton group name", self.group_name_le)
        settings_lay.addRow("jUE suffix", self.suffix_le)
        layout.addWidget(settings_box)

        # ---- Skeleton actions ------------------------------------------
        build_box = QtWidgets.QGroupBox("Skeleton")
        build_lay = QtWidgets.QHBoxLayout(build_box)
        self.build_btn = QtWidgets.QPushButton("Build Skeleton")
        self.rebuild_btn = QtWidgets.QPushButton("Rebuild (delete + build)")
        self.delete_btn = QtWidgets.QPushButton("Delete Skeleton")
        build_lay.addWidget(self.build_btn)
        build_lay.addWidget(self.rebuild_btn)
        build_lay.addWidget(self.delete_btn)
        layout.addWidget(build_box)

        # ---- Skin transfer ---------------------------------------------
        skin_box = QtWidgets.QGroupBox("Skin influences")
        skin_lay = QtWidgets.QVBoxLayout(skin_box)
        self.remove_jdef_cb = QtWidgets.QCheckBox(
            "Remove _jDef from skinClusters after transfer"
        )
        self.remove_jdef_cb.setToolTip(
            "Off (default): keep both influences; _jDef weights go to zero "
            "but stay in the cluster.  Safer for round-tripping.\n"
            "On: strip _jDef from each cluster after the transfer.  Smaller "
            "FBX but cannot be reversed in-scene."
        )
        skin_lay.addWidget(self.remove_jdef_cb)

        skin_note = QtWidgets.QLabel(
            "Skin transfer only operates on _jUE joints backed by a _jDef "
            "driver (source='Rig' or 'Auto' with a built module).\n"
            "Static _jUEs (from guide-only modules) are skipped with a "
            "warning."
        )
        skin_note.setWordWrap(True)
        skin_note.setStyleSheet("color: gray; font-size: 11px;")
        skin_lay.addWidget(skin_note)

        skin_btn_lay = QtWidgets.QHBoxLayout()
        self.dry_run_btn = QtWidgets.QPushButton("Preview Skin Transfer (dry run)")
        self.transfer_btn = QtWidgets.QPushButton("Transfer Skin Weights")
        skin_btn_lay.addWidget(self.dry_run_btn)
        skin_btn_lay.addWidget(self.transfer_btn)
        skin_lay.addLayout(skin_btn_lay)
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

        # ---- Close -----------------------------------------------------
        close_lay = QtWidgets.QHBoxLayout()
        close_lay.addStretch(1)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setFixedWidth(100)
        close_lay.addWidget(self.close_btn)
        layout.addLayout(close_lay)

        # ---- Signals ---------------------------------------------------
        self.refresh_status_btn.clicked.connect(self._refresh_status_table)
        self.dump_report_btn.clicked.connect(self.on_dump_detection_report)
        self.source_guide_rb.toggled.connect(self._sync_granularity_enablement)
        self.source_rig_rb.toggled.connect(self._sync_granularity_enablement)
        self.source_auto_rb.toggled.connect(self._sync_granularity_enablement)
        self.build_btn.clicked.connect(self.on_build)
        self.rebuild_btn.clicked.connect(self.on_rebuild)
        self.delete_btn.clicked.connect(self.on_delete)
        self.dry_run_btn.clicked.connect(self.on_transfer_dry_run)
        self.transfer_btn.clicked.connect(self.on_transfer_live)
        self.clear_log_btn.clicked.connect(self.log_te.clear)
        self.close_btn.clicked.connect(self.close)

    # ---- Helpers: UI state --------------------------------------------

    def _selected_source(self):
        if self.source_guide_rb.isChecked():
            return "guide"
        if self.source_rig_rb.isChecked():
            return "rig"
        return "auto"

    def _selected_granularity(self):
        return "full" if self.gran_full_rb.isChecked() else "main"

    def _sync_granularity_enablement(self):
        """Source = Guide disables the 'Include Twist' radio -- twist
        requires rig data.  When disabled, force selection back to
        'Main only' so the user's intent is unambiguous."""
        src = self._selected_source()
        twist_allowed = src != "guide"
        self.gran_full_rb.setEnabled(twist_allowed)
        if not twist_allowed and self.gran_full_rb.isChecked():
            self.gran_main_rb.setChecked(True)
        if not twist_allowed:
            self.gran_full_rb.setToolTip(
                "Twist joints require rig build (_jDef). "
                "Switch data source to Rig or Auto to enable."
            )
        else:
            self.gran_full_rb.setToolTip(
                "Main joints + every twist/ribbon _jDef between consecutive\n"
                "guides (e.g. arm = 13). Guide-sourced modules degrade to Main."
            )

    # ---- Helpers: module status table ---------------------------------

    def _refresh_status_table(self):
        self.status_table.setRowCount(0)
        try:
            snapshot = builder.module_status_snapshot()
        except Exception:   # noqa: BLE001
            self._log_exception("Module status snapshot failed:")
            return
        for info in snapshot:
            row = self.status_table.rowCount()
            self.status_table.insertRow(row)
            self._set_cell(row, 0, info["module_name"])
            self._set_cell(row, 1, info["module_type"])
            self._set_cell(row, 2, info["side"])
            self._set_cell(row, 3, "%d guides" % info["guide_count"])
            if info["has_rig"]:
                self._set_cell(row, 4, "BUILT")
                self._set_cell(row, 5, "%d deform" % info["deform_count"])
            else:
                self._set_cell(row, 4, "-- not built --")
                self._set_cell(row, 5, "-")
        self.status_table.resizeColumnsToContents()

    def _set_cell(self, row, col, text):
        item = QtWidgets.QTableWidgetItem(str(text))
        self.status_table.setItem(row, col, item)

    # ---- Log helpers ---------------------------------------------------

    def _log(self, msg):
        self.log_te.appendPlainText(msg)
        sb = self.log_te.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _log_exception(self, prefix):
        self._log("%s\n%s" % (prefix, traceback.format_exc()))

    def _current_kwargs(self):
        return {
            "source": self._selected_source(),
            "granularity": self._selected_granularity(),
            "root_name": self.root_name_le.text().strip() or builder.UE_ROOT,
            "group_name": self.group_name_le.text().strip() or builder.UE_GROUP,
            "suffix": self.suffix_le.text().strip() or builder.UE_SUFFIX,
        }

    def _log_build_result(self, result):
        self._log(
            "Built skeleton: root=%s, group=%s, %d jUE joints created." %
            (result["root"], result["group"], len(result["created"]))
        )
        self._log("  source=%s, granularity=%s" %
                  (result["source"], result["granularity"]))
        chains = result.get("module_chains") or {}
        status = result.get("module_status") or {}
        if chains:
            self._log("Module chains:")
            for mod, chain in chains.items():
                note = status.get(mod, "")
                self._log("  %-25s %3d joints   (%s)" %
                          (mod, len(chain), note))
        skipped = result.get("skipped") or []
        if skipped:
            self._log("Skipped (%d):" % len(skipped))
            for item, reason in skipped:
                self._log("  - %s: %s" % (item, reason))

    # ---- Pre-build: Rig-source sanity check ----------------------------

    def _warn_if_rig_source_with_unbuilt(self):
        """When the user picks source='Rig', preview unbuilt modules and
        ask for confirmation -- they would otherwise be silently
        skipped.  Returns True to proceed, False to cancel."""
        if self._selected_source() != "rig":
            return True
        try:
            snapshot = builder.module_status_snapshot()
        except Exception:   # noqa: BLE001
            return True   # let the build itself surface the real error
        unbuilt = [s["module_name"] for s in snapshot if not s["has_rig"]]
        if not unbuilt:
            return True
        names = "\n".join("  - %s" % m for m in unbuilt)
        reply = QtWidgets.QMessageBox.warning(
            self,
            "Unbuilt modules will be skipped",
            "Data source is 'Rig' and the following %d module(s) have "
            "not been Test Built:\n\n%s\n\nThey will be skipped.  Continue?"
            % (len(unbuilt), names),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Cancel,
        )
        return reply == QtWidgets.QMessageBox.Yes

    # ---- Slots: skeleton actions ---------------------------------------

    def on_build(self):
        if not self._warn_if_rig_source_with_unbuilt():
            return
        kw = self._current_kwargs()
        self._log("=== Build Skeleton (source=%s, granularity=%s) ===" %
                  (kw["source"], kw["granularity"]))
        try:
            result = builder.build_ue_skeleton(**kw)
        except (RuntimeError, ValueError) as exc:
            self._log("Build failed: %s" % exc)
            return
        except Exception:   # noqa: BLE001
            self._log_exception("Build raised an unexpected error:")
            return
        self._log_build_result(result)
        self._refresh_status_table()

    def on_rebuild(self):
        if not self._warn_if_rig_source_with_unbuilt():
            return
        kw = self._current_kwargs()
        self._log("=== Rebuild (delete + build) "
                  "(source=%s, granularity=%s) ===" %
                  (kw["source"], kw["granularity"]))
        try:
            result = builder.rebuild_ue_skeleton(**kw)
        except (RuntimeError, ValueError) as exc:
            self._log("Rebuild failed: %s" % exc)
            return
        except Exception:   # noqa: BLE001
            self._log_exception("Rebuild raised an unexpected error:")
            return
        self._log_build_result(result)
        self._refresh_status_table()

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

    # ---- Slots: skin transfer -----------------------------------------

    def _run_transfer(self, dry_run):
        remove = self.remove_jdef_cb.isChecked()
        self._log("=== Transfer Skin Weights (%s%s) ===" %
                  ("dry run" if dry_run else "live",
                   ", remove _jDef" if remove else ""))
        try:
            result = skin_swap.transfer_skin_to_ue(
                remove_jdef_influence=remove, dry_run=dry_run
            )
        except Exception:   # noqa: BLE001
            self._log_exception("Transfer raised an unexpected error:")
            return
        self._log("skinclusters processed : %d" % result["skinclusters_processed"])
        self._log("influences added       : %d" % result["influences_added"])
        self._log("influences removed     : %d" % result["influences_removed"])
        self._log("vertices touched       : %d" % result["vertices_touched"])
        warnings = result.get("warnings") or []
        if warnings:
            self._log("Warnings (%d):" % len(warnings))
            for w in warnings:
                self._log("  - %s" % w)

    def on_transfer_dry_run(self):
        self._run_transfer(dry_run=True)

    def on_transfer_live(self):
        self._run_transfer(dry_run=False)

    # ---- Slots: detection report (non-destructive diagnostic) ---------

    def on_dump_detection_report(self):
        """Log what the detection layer sees right now, without
        building.  For each BUILT module also simulates a greedy
        guide -> deform pairing (mirroring the real build's
        ``_partition_deforms`` sweep) and prints the world-space
        distance of each pairing, so the user can visually verify
        that Priority 4's spatial fallback picks reasonable matches
        before committing to Build."""
        from maya import cmds
        import maya.api.OpenMaya as om
        from ddrig.base import initials

        try:
            report = builder.detection_report()
        except Exception:   # noqa: BLE001
            self._log_exception("Detection report failed:")
            return
        self._log("=== Detection Report ===")
        if not report:
            self._log("  (no guide roots in scene)")
            return

        # Map module_name -> guide_root (for the matching preview).
        try:
            scene_roots = initials.Initials().get_scene_roots()
        except Exception:   # noqa: BLE001
            scene_roots = []
        roots_by_module = {r["module_name"]: r["root_joint"] for r in scene_roots}

        for entry in report:
            mn = entry["module_name"]
            if not entry["has_rig"]:
                self._log(
                    "  %s: NOT BUILT  (%d guides; limbGrp %r missing or empty)"
                    % (mn, entry["guide_count"], mn)
                )
                continue
            deforms = entry["deforms"]
            self._log(
                "  %s: BUILT  (%d guides, %d deform)" %
                (mn, entry["guide_count"], len(deforms))
            )
            # Show up to 15 deform joints with their immediate parent
            # transform, so the user can spot joints that landed under
            # an unexpected sub-group.
            for d in deforms[:15]:
                try:
                    parents = cmds.listRelatives(
                        d, parent=True, fullPath=False
                    ) or []
                except RuntimeError:
                    parents = []
                parent = parents[0] if parents else "?"
                self._log("    - %s  (under %s)" % (d, parent))
            if len(deforms) > 15:
                self._log("    ... (%d more)" % (len(deforms) - 15))

            # ---- Matching preview (mirrors _partition_deforms) --------
            guide_root = roots_by_module.get(mn)
            if not guide_root or not cmds.objExists(guide_root):
                continue
            descendants = cmds.listRelatives(
                guide_root, allDescendents=True, type="joint"
            ) or []
            # listRelatives(ad=True) returns deep-to-shallow; reverse it
            # and prepend root for a natural parent-first traversal.
            guide_chain = [guide_root] + list(reversed(descendants))

            self._log("    Guide matching preview:")
            used = set()
            for g in guide_chain:
                gs = g.rsplit("|", 1)[-1]
                if not gs.endswith(builder.GUIDE_SUFFIX):
                    continue
                match = builder.find_deform_for_guide(
                    gs, mn, module_deforms=deforms, exclude=used
                )
                if match is None:
                    self._log("      %s -> (no deform available)" % gs)
                    continue
                used.add(match)
                try:
                    gp = cmds.xform(gs, q=True, ws=True, t=True)
                    mp = cmds.xform(match, q=True, ws=True, t=True)
                    dist = (om.MVector(*gp) - om.MVector(*mp)).length()
                    self._log(
                        "      %s -> %s  (dist=%.2f)" % (gs, match, dist)
                    )
                except RuntimeError:
                    self._log("      %s -> %s  (dist=?)" % (gs, match))
