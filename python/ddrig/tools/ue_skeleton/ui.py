"""Qt dialog wrapper around :mod:`ddrig.tools.ue_skeleton`.

Layout (top to bottom)::

    Module status table (snapshot of current scene: guide count, build
                         status per module)              [Refresh]

    Settings:  Root / Group / Suffix name fields
                                               [Prefix Mapping...]

    Skeleton:  [Build]  [Rebuild]  [Delete]

    Skin:      [ ] Remove _jDef after transfer
               [Preview (dry run)]  [Transfer]

    Log panel + Clear + Close

The Source and Granularity controls are gone: topology is fully
determined by :data:`ddrig.tools.ue_skeleton.builder._DEFAULT_UE_TOPOLOGY_RULES`
plus the (default + user) submodule prefix map loaded from
:mod:`ddrig.tools.ue_skeleton.prefix_config`.
"""
from __future__ import annotations

import traceback

from ddrig.ui.Qt import QtCore, QtWidgets
from ddrig.tools.ue_skeleton import builder, prefix_config, skin_swap


# objectName begins with "DDRIG " so ddrig.tools.hotswap._close_qt_windows
# sweeps this dialog up during uninstall / reinstall.
WINDOW_OBJECT_NAME = "DDRIG UE Skeleton"


class PrefixMappingDialog(QtWidgets.QDialog):
    """Edit user-defined submodule prefix mappings.

    Builtin defaults are shown read-only at the top; user entries are
    editable in the table below.  Saving writes to
    :func:`prefix_config.save_user_map`."""

    def __init__(self, parent=None):
        super(PrefixMappingDialog, self).__init__(parent)
        self.setWindowTitle("UE Prefix Mapping")
        self.setMinimumWidth(560)
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(QtWidgets.QLabel("Builtin mappings (read-only):"))
        self.builtin_table = QtWidgets.QTableWidget(0, 3)
        self.builtin_table.setHorizontalHeaderLabels([
            "Submodule prefix", "Target module", "Target segment",
        ])
        self.builtin_table.setEditTriggers(
            QtWidgets.QAbstractItemView.NoEditTriggers
        )
        self.builtin_table.verticalHeader().setVisible(False)
        self.builtin_table.horizontalHeader().setStretchLastSection(True)
        self.builtin_table.setMaximumHeight(120)
        layout.addWidget(self.builtin_table)

        layout.addWidget(QtWidgets.QLabel("User mappings (editable):"))
        self.user_table = QtWidgets.QTableWidget(0, 3)
        self.user_table.setHorizontalHeaderLabels([
            "Submodule prefix", "Target module", "Target segment",
        ])
        self.user_table.verticalHeader().setVisible(False)
        self.user_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.user_table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add row")
        self.remove_btn = QtWidgets.QPushButton("Remove selected")
        btn_row.addWidget(self.add_btn)
        btn_row.addWidget(self.remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        path_label = QtWidgets.QLabel(
            "Saved to: %s" % prefix_config.config_path()
        )
        path_label.setStyleSheet("color: gray; font-size: 10px;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        dialog_btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        dialog_btns.accepted.connect(self._on_save)
        dialog_btns.rejected.connect(self.reject)
        layout.addWidget(dialog_btns)

        self.add_btn.clicked.connect(self._add_row)
        self.remove_btn.clicked.connect(self._remove_selected)

    def _populate(self):
        for entry in builder._DEFAULT_SUBMOD_PREFIX_MAP:
            self._append_row(
                self.builtin_table,
                entry["submod_prefix"],
                entry["target_module"],
                entry["target_segment"],
            )
        for entry in prefix_config.load_user_map():
            self._append_row(
                self.user_table,
                entry["submod_prefix"],
                entry["target_module"],
                entry["target_segment"],
            )
        self.builtin_table.resizeColumnsToContents()

    def _append_row(self, table, pfx, mod, seg):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QtWidgets.QTableWidgetItem(pfx))
        table.setItem(row, 1, QtWidgets.QTableWidgetItem(mod))
        table.setItem(row, 2, QtWidgets.QTableWidgetItem(seg))

    def _add_row(self):
        self._append_row(self.user_table, "", "", "")
        # Put focus on the new row's first cell so the user can type.
        self.user_table.setCurrentCell(self.user_table.rowCount() - 1, 0)
        self.user_table.editItem(
            self.user_table.item(self.user_table.rowCount() - 1, 0)
        )

    def _remove_selected(self):
        rows = sorted(
            {idx.row() for idx in self.user_table.selectedIndexes()},
            reverse=True,
        )
        for r in rows:
            self.user_table.removeRow(r)

    def _cell_text(self, row, col):
        item = self.user_table.item(row, col)
        return item.text().strip() if item else ""

    def _on_save(self):
        entries = []
        for r in range(self.user_table.rowCount()):
            pfx = self._cell_text(r, 0)
            mod = self._cell_text(r, 1)
            seg = self._cell_text(r, 2)
            if pfx and mod and seg:
                entries.append({
                    "submod_prefix": pfx,
                    "target_module": mod,
                    "target_segment": seg,
                })
        try:
            prefix_config.save_user_map(entries)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Save failed",
                "Could not write %s:\n%s" %
                (prefix_config.config_path(), exc),
            )
            return
        self.accept()


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
            "Log the per-module deform detection result AND the parse "
            "classification (segment_key + index) of every deform joint, "
            "without building anything.  ✓ = segment is in the rules "
            "table; ✗ = segment unknown or name could not be parsed."
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

        self.root_name_le = QtWidgets.QLineEdit(builder.UE_ROOT)
        self.group_name_le = QtWidgets.QLineEdit(builder.UE_GROUP)
        self.suffix_le = QtWidgets.QLineEdit(builder.UE_SUFFIX)
        self.suffix_le.setMaxLength(16)
        settings_lay.addRow("Root joint name", self.root_name_le)
        settings_lay.addRow("Skeleton group name", self.group_name_le)
        settings_lay.addRow("jUE suffix", self.suffix_le)

        prefix_row = QtWidgets.QHBoxLayout()
        self.prefix_btn = QtWidgets.QPushButton("Prefix Mapping...")
        self.prefix_btn.setToolTip(
            "Add project-specific submodule prefix -> segment mappings.\n"
            "Merged with builtin defaults at Build time."
        )
        prefix_row.addStretch(1)
        prefix_row.addWidget(self.prefix_btn)
        settings_lay.addRow("", prefix_row)

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
            "Off (default): keep both influences; _jDef weights go to "
            "zero but stay in the cluster.  Safer for round-tripping.\n"
            "On: strip _jDef from each cluster after the transfer.  "
            "Smaller FBX but cannot be reversed in-scene."
        )
        skin_lay.addWidget(self.remove_jdef_cb)

        skin_btn_lay = QtWidgets.QHBoxLayout()
        self.dry_run_btn = QtWidgets.QPushButton(
            "Preview Skin Transfer (dry run)"
        )
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
        self.prefix_btn.clicked.connect(self.on_open_prefix_dialog)
        self.build_btn.clicked.connect(self.on_build)
        self.rebuild_btn.clicked.connect(self.on_rebuild)
        self.delete_btn.clicked.connect(self.on_delete)
        self.dry_run_btn.clicked.connect(self.on_transfer_dry_run)
        self.transfer_btn.clicked.connect(self.on_transfer_live)
        self.clear_log_btn.clicked.connect(self.log_te.clear)
        self.close_btn.clicked.connect(self.close)

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
        """Kwargs for build_ue_skeleton / rebuild_ue_skeleton /
        delete_ue_skeleton (filtered to each function's signature by
        the callers)."""
        return {
            "root_name": self.root_name_le.text().strip() or builder.UE_ROOT,
            "group_name": self.group_name_le.text().strip() or builder.UE_GROUP,
            "suffix": self.suffix_le.text().strip() or builder.UE_SUFFIX,
            "prefix_map": prefix_config.get_effective_map(),
        }

    def _log_build_result(self, result):
        self._log(
            "Built skeleton: root=%s, group=%s, %d jUE joints created." %
            (result["root"], result["group"], len(result.get("created") or []))
        )
        per_module = result.get("per_module") or {}
        if per_module:
            self._log("Module output:")
            for mn, data in per_module.items():
                self._log("  %-25s %3d joints   (type=%s)" %
                          (mn, len(data["jue_map"]), data["module_type"]))
        warnings = result.get("warnings") or []
        if warnings:
            self._log("Warnings (%d):" % len(warnings))
            for w in warnings:
                self._log("  - %s" % w)

    # ---- Slots: skeleton actions ---------------------------------------

    def on_build(self):
        kw = self._current_kwargs()
        self._log("=== Build Skeleton ===")
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
        kw = self._current_kwargs()
        self._log("=== Rebuild (delete + build) ===")
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

    # ---- Slots: prefix mapping dialog ----------------------------------

    def on_open_prefix_dialog(self):
        dlg = PrefixMappingDialog(self)
        dlg.exec_()

    # ---- Slots: detection report (non-destructive diagnostic) ---------

    def on_dump_detection_report(self):
        """Log each module's deform joints together with their
        parse classification.  ``✓`` = segment is defined in the
        topology rules for this module_type; ``✗`` = segment unknown
        (possible naming convention mismatch) or parse outright
        failed."""
        from maya import cmds

        pmap = prefix_config.get_effective_map()
        try:
            report = builder.detection_report()
        except Exception:   # noqa: BLE001
            self._log_exception("Detection report failed:")
            return
        self._log("=== Detection Report ===")
        if not report:
            self._log("  (no guide roots in scene)")
            return

        for entry in report:
            mn = entry["module_name"]
            mtype = entry["module_type"]
            rules = builder._DEFAULT_UE_TOPOLOGY_RULES.get(mtype)
            self._log("")
            self._log("--- %s (type=%s) ---" % (mn, mtype))
            if not rules:
                self._log(
                    "  WARNING: no topology rule for module_type %r -- "
                    "this module will be skipped by Build." % mtype
                )
                continue
            valid_segments = {k for (k, _, _) in rules}

            if not entry["has_rig"]:
                self._log("  NOT BUILT  (%d guides)" % entry["guide_count"])
                all_nodes = cmds.ls(mn + "*", type="joint") or []
                if all_nodes:
                    self._log(
                        "  Scene has %d joint(s) whose short name starts "
                        "with %r:" % (len(all_nodes), mn)
                    )
                    for n in all_nodes[:15]:
                        self._log("    ? %s" % n)
                    if len(all_nodes) > 15:
                        self._log("    ... +%d more" % (len(all_nodes) - 15))
                continue

            deforms = entry["deforms"]
            self._log("  BUILT: %d deform joints" % len(deforms))
            for d in deforms:
                short = d.rsplit("|", 1)[-1]
                try:
                    parents = cmds.listRelatives(
                        d, parent=True, fullPath=False
                    ) or []
                except RuntimeError:
                    parents = []
                parent_short = parents[0] if parents else "?"

                parsed = builder.parse_def_name(short, mn, pmap)
                if parsed is None:
                    self._log(
                        "    x %-40s (under %s)  -> UNPARSED" %
                        (short, parent_short)
                    )
                    continue
                seg, idx = parsed
                mark = "ok" if seg in valid_segments else "xx"
                idx_str = "[%d]" % idx if idx is not None else ""
                self._log(
                    "    %s %-40s (under %s)  -> segment=%r%s" %
                    (mark, short, parent_short, seg, idx_str)
                )
