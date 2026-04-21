# DDRIG — Phase 1 Analysis Report

**Repository:** `X:/Plugins/DDRIG`
**Date of analysis:** 2026-04-21
**Scope:** Read-only analysis of the internal fork of `Trigger` (Arda Kutlu) being rebranded to `DDRIG` (Drafter). This report is the authoritative map that drives Phase 2 (rename) and Phase 3 (timestamp backfill).

**Scan exclusions applied throughout:** `.git/`, `.claude/`, `.remember/`, `samples/` (gitignored after Phase 0), `docs/source/_build/` (generated Sphinx artifacts), binary files.

---

## 1.2.1 Project Fingerprint

### LOC Statistics

| Category | File Count | LOC |
|---|---:|---:|
| Python (`*.py`) in `python/` and `tests/` | 184 | 48,131 |
| reStructuredText (`*.rst`) in `docs/source/` (excl. `_build/`) | 40 | 1,063 |
| Markdown (`*.md`) at root and `.github/` | 6 | 704 |
| Config files (`.flake8`, `*.toml`, `*.yaml`) | 3 | 22 |
| **Total** | **233** | **49,920** |

### Build System

- **`pyproject.toml` (7 lines)** — `name = "trigger"`, `authors = [{name: "Arda Kutlu", email: "ardakutlu@gmail.com"}]`, dynamic `version`/`description`, empty build-requires.
- **`.flake8`** — `max-line-length = 88`, excludes `tests/*`.
- **`.readthedocs.yaml`** — Ubuntu 22.04 + Python 3.10, requirements at `docs/requirements.txt`, Sphinx config at `docs/source/conf.py`.
- **`.github/workflows/`** — **no workflow files present** (only `ISSUE_TEMPLATE/` with two markdown templates).

### Entry Points (Maya load chain)

1. **`python/maya_modules/trigger.mod`** — Maya module descriptor (the install target).
2. **`python/maya_modules/shelves_module/scripts/userSetup.py`** — runs at Maya startup; deferred-calls `trigger_setup.add_python_path()` and `trigger_setup.load_menu()`.
3. **`python/maya_modules/shelves_module/scripts/trigger_setup.py`** — prepends `python/` to `sys.path`, builds the top-level **"Trigger"** Maya menu with items wiring to:
   - `trigger.ui.main.launch()` — main UI window (the Actions+Guides tabs)
   - `trigger.utils.wand.panel.dock_window(panel.MainUI)` — Trigger Selector
   - `trigger.utils.makeup.launch()` — Make Up tool
   - `trigger.utils.blendshape_transfer` — Blendshape Transfer
   - `trigger.utils.mocap.ui.launch()` — Mocap Mapper
   - `trigger.utils.rom_randomizer.ui.launch()` — ROM Randomizer
4. **`python/trigger/ui/main.py`** — `launch(force=False, disable_version_control=False)`; opens the top-level Qt window; uses `trigger.core.database.Database()` and displays version from `trigger._version`.
5. **`python/dragAndDropMe.py`** — drag-and-drop installer (copies `maya_modules/` into user Maya modules path).

### Top External Dependencies

| Import | Count | Category |
|---|---:|---|
| `from maya import cmds` | 96 | Maya API |
| `from trigger.ui.Qt import QtWidgets[, QtCore, QtGui]` | 57 (aggregate) | Internal Qt shim |
| `import maya.api.OpenMaya as om` / `from maya.api import OpenMaya` | 26 | Maya API |
| `from maya import mel` | 10 | Maya API |
| `from maya import OpenMayaUI as omui` | 2 | Maya API |
| `import logging` | 19 | stdlib |
| `import importlib` | 6 | stdlib |
| `import glob` | 6 | stdlib |
| `from pathlib import Path` | 3 | stdlib |

No hard third-party package dependencies beyond Maya itself. Qt access is entirely routed through the vendored `trigger.ui.Qt` shim.

### Directory Tree (depth ≤ 3)

```
DDRIG/
├── .flake8
├── .github/
│   └── ISSUE_TEMPLATE/  (bug_report.md, feature_request.md)
├── .gitignore
├── .readthedocs.yaml
├── README.md
├── PHASE1_REPORT.md   (this file)
├── docs/
│   └── source/
│       ├── conf.py
│       ├── *.rst      (top-level narrative pages)
│       ├── actions/   (20 rst, one per Action)
│       ├── modules/   (14 rst, one per Module)
│       ├── _images/
│       └── _build/    (generated Sphinx output — to be removed, see §1.2.6)
├── pyproject.toml
├── python/
│   ├── __init__.py
│   ├── dragAndDropMe.py
│   ├── maya_modules/
│   │   ├── trigger.mod
│   │   └── shelves_module/
│   │       ├── scripts/   (userSetup.py, trigger_setup.py)
│   │       ├── plug-ins/  (tensionMap.py)
│   │       ├── shelves/   (shelf_trigger.mel)
│   │       └── icons/
│   ├── tests/             (internal adhoc test scripts)
│   └── trigger/           (main plugin package — 161 modules)
│       ├── __init__.py, _version.py
│       ├── actions/        (25 files — A-Actions scope)
│       ├── base/           (session / initials — mixed G+S)
│       ├── core/           (infra — S)
│       ├── library/        (rigging primitives — S)
│       ├── modules/        (14 rig modules — G-Guides scope)
│       ├── objects/        (scene data wrappers — S)
│       ├── tools/          (face_mocap, mirror_rig — S)
│       ├── ui/             (Qt/ shim, main, widgets, layouts — S)
│       ├── utils/          (dembones wrapper, eyebulge, mocap, etc. — S)
│       └── version_control/ (api, rbl_shotgrid, tik_manager — S)
├── samples/                (gitignored)
└── tests/                  (4 Python test files)
```

---

## 1.2.2 "Trigger" Occurrence Map

### Morphology Table

| Morphology | Files | Hits | Note |
|---|---:|---:|---|
| `Trigger` (PascalCase standalone) | 45 | 102 | User-facing labels, class names, window names, docstrings |
| `trigger` (lowercase standalone) | ≥199 | 852 | Overwhelmingly package-path tokens in `import` / string-literal paths |
| `Triggers` / `triggers` (plural) | 3 | 4 | **Qt API only** — `QAbstractItemView.EditTriggers` etc. in `ui/Qt/*.pyi` |
| `TRIGGER` (UPPERCASE standalone) | 0 | 0 | — |

**Aggregate:** ≈ 960 hits in ≈ 199 files that must be examined during Phase 2.

### Compound Words

#### `TriggerXxx` / `triggerXxx` (project-specific)

| Identifier | Occurrences | Classification | Source |
|---|---:|---|---|
| `TriggerTool` | 3 | **[project]** | `trigger/utils/wand/panel.py:103` (class) |
| `triggerFilePath` | 6 | **[project]** | `trigger/actions/reference_session.py` |
| `triggerTools` (png + mel refs) | 4 | **[project]** | `maya_modules/shelves_module/icons/triggerTools.png`, `shelf_trigger.mel` |
| `triggerSet1` / `triggerSet%i` | 2 | **[project]** | `trigger/actions/selection_sets.py:97-99` |

#### `xxxTrigger` / `xxxtrigger` (project-specific, verbs/nouns in `ui/main.py`)

| Identifier | Occurrences | Classification | Source |
|---|---:|---|---|
| `save_trigger` | 6 | **[project]** — method of main UI | `ui/main.py:251,331,1078`; `version_control/api.py:19` |
| `open_trigger` | 4 | **[project]** | `ui/main.py:329,875,1003`; `version_control/api.py:35` |
| `import_trigger` | 2 | **[project]** | `ui/main.py:330,1026` |
| `new_trigger` | 2 | **[project]** | `ui/main.py:328,973` |
| `increment_trigger` | 2 | **[project]** | `ui/main.py:333,1045` |
| `save_as_trigger` | 2 | **[project]** | `ui/main.py:332,1062` |
| `ShotTrigger` | 1 | **[project]** | `version_control/rbl_shotgrid.py` |
| `shelf_trigger` | 1 | **[project]** — MEL proc | `maya_modules/.../shelf_trigger.mel:1` |
| `trigger_setup` (module) | 1 | **[project]** | `maya_modules/shelves_module/scripts/trigger_setup.py` |
| `trigger_mappings` | 2 | **[project]** | `trigger/tools/face_mocap/main.py` |
| `trigger_log` (log filename literal) | 16 | **[project]** — filename literal | all action files |
| `trigger_file_path` (dict key) | 1 | **[project]** | `trigger/actions/reference_session.py:34` |
| `asset_work_area_trigger` (shotgrid template key) | 1 | **[project]** | `version_control/rbl_shotgrid.py:20` |

#### Compound words to PRESERVE (external API)

| Identifier | Source | Reason |
|---|---|---|
| `triggered` (Qt signal `.triggered.connect()`) | PySide2/PyQt5 API | Qt standard signal — **do not touch** |
| `triggerAction`, `triggerPageAction` | `ui/Qt/QtWebEngineWidgets.pyi` | Qt WebEngine API |
| `EditTrigger`, `EditTriggers` | `ui/Qt/QtWidgets.pyi` | `QAbstractItemView.EditTriggers` enum |
| `TriggerGesture` | `ui/Qt/QtWidgets.pyi` | `QGestureRecognizer.TriggerGesture` |

### File and Directory Paths (require `git mv` in Phase 2)

**Directories** (25):
```
python/trigger/                            ← root package
python/trigger/actions/
python/trigger/base/
python/trigger/core/
python/trigger/library/
python/trigger/modules/
python/trigger/objects/
python/trigger/tools/
python/trigger/tools/face_mocap/
python/trigger/tools/mirror_rig/
python/trigger/ui/
python/trigger/ui/Qt/
python/trigger/ui/layouts/
python/trigger/ui/vcs_widgets/
python/trigger/ui/widgets/
python/trigger/utils/
python/trigger/utils/dembones/    (and its Linux/, MacOS/, Windows/ subdirs — gitignored on disk)
python/trigger/utils/eyebulge/
python/trigger/utils/mirror_lattice/
python/trigger/utils/mocap/
python/trigger/utils/rom_randomizer/
python/trigger/utils/rom_viewer/
python/trigger/utils/shape_transfer/
python/trigger/utils/wand/
python/trigger/version_control/
python/trigger/version_control/tik_manager/
```

**Files with `trigger` in path** (beyond the `trigger/` tree itself):
```
python/maya_modules/trigger.mod
python/maya_modules/shelves_module/scripts/trigger_setup.py
python/maya_modules/shelves_module/shelves/shelf_trigger.mel
python/maya_modules/shelves_module/icons/trigger.png
python/maya_modules/shelves_module/icons/triggerTools.png
```

### Import Statements

- 60+ unique `from trigger.X.Y import Z` forms across ~50 files (comprehensive list omitted for length — all will be mechanically rewritten).
- Three `import trigger.X.Y as Z` forms in `trigger_setup.py`.
- `import trigger_setup` inside `userSetup.py`.
- Relative imports `from . import X` / `from .. import X` within the package — untouched unless target module renames.

### String Literals vs Identifiers

Rough breakdown of the 852 lowercase hits:

| Kind | ~Count |
|---|---:|
| Identifiers and method calls | 280 |
| Import path tokens | 380 |
| File path / log name string literals | 80 |
| UI label / docstring / message literals | 112 |

### External URLs / Branding

| URL | Location | Action in Phase 2 |
|---|---|---|
| `https://trigger-maya.readthedocs.io/en/latest/` | `README.md:23` | Decision required — likely remove (internal fork has no public docs) |
| `https://github.com/masqu3rad3/trigger/archive/...` | `docs/source/installation.rst:1` | Decision required — remove or replace |
| `https://github.com/masqu3rad3/trigger.git` | `docs/source/installation.rst:2` | Decision required — remove or replace |
| `https://www.ardakutlu.com` | `python/trigger/utils/skin_transfer.py:6` | Remove |

### High-Risk Hits

**User-facing file extensions** (runtime-visible in Maya file dialogs — Phase 2 must decide rename policy):

| Ext | Meaning |
|---|---|
| `.tr` | Trigger Session file |
| `.trg` | Trigger Guide file |
| `.trl` | Trigger Look file |
| `.trp` | Trigger Preset file |
| `.trw` | Trigger Weight file |
| `.trsplit` | Trigger Split Shapes file |

**pyproject.toml package name:** `name = "trigger"` → must become `"ddrig"`.

**Log filename literal:** `"trigger_log"` appears 17× in `core/filelog.py` + every action file. Its value is a filename prefix visible at runtime (`trigger_log.log`); renaming it is cosmetic only but recommended for consistency.

**Window names / menu labels** (user-visible strings):
- `ui/main.py:30` — `WINDOW_NAME = "Trigger {0}"`
- `utils/wand/panel.py:28` — `"Trigger Tool v{version}"`
- `utils/makeup.py:14` — `"Trigger Make-up v0.0.2"`
- Maya menu name `"Trigger"` in `trigger_setup.py` (7 menu items underneath)

---

## 1.2.3 Author Info Cross-Check

### `Arda Kutlu` occurrences

**Total: 8 hits across 6 files.**

| File | Line | Excerpt |
|---|---:|---|
| `pyproject.toml` | 6 | `authors = [{name: "Arda Kutlu", email: "ardakutlu@gmail.com"}]` |
| `python/trigger/core/io.py` | 3 | `:author: Arda Kutlu <ardakutlu@gmail.com>` |
| `python/trigger/utils/skin_transfer.py` | 3-5 | `## Copyright (C) Arda Kutlu` / `## AUTHOR: Arda Kutlu` / `## e-mail: ardakutlu@gmail.com` |
| `python/trigger/utils/wand/panel.py` | 4 | `:author: Arda Kutlu <arda.kutlu@rebellion.co.uk>` ← **different email** |
| `docs/source/conf.py` | 15-16 | `copyright = '2023, Arda Kutlu'` / `author = 'Arda Kutlu'` |

### `ardakutlu@gmail.com` occurrences

**Total: 3 hits across 2 files** (`pyproject.toml`, `trigger/core/io.py`, `trigger/utils/skin_transfer.py`).

### `ardakutlu` (bare token) occurrences

**Total: 5 hits across 3 files** (above, plus `www.ardakutlu.com` URL, plus `linkedin.com/in/ardakutlu/` in samples/README which is gitignored).

### ⚠️ OTHER Authors/Emails Found (vendored code — **must be preserved, not rewritten**)

| Name | Email | Where | Status |
|---|---|---|---|
| Marcus Ottosson | — | `ui/Qt/__init__.py:2061` | **Vendored `Qt.py` upstream** |
| Chris Beaumont | — | `ui/Qt/__init__.py:2093` | Qt.py contributor |
| Thomas Robitaille | — | `ui/Qt/__init__.py:2093` | Qt.py contributor |
| Sebastian Wiesner | lunaryorn@gmail.com | `ui/Qt/__init__.py:2129` | Qt.py contributor |
| Charl Botha | cpbotha@vxlabs.com | `ui/Qt/__init__.py:2130` | Qt.py contributor |
| Jung Gyu Yoon | — | `ui/widgets/loading_progressbar.py:8` | Credit for widget source |

**Additional email found:** `arda.kutlu@rebellion.co.uk` in `utils/wand/panel.py:4`. **This is still Arda Kutlu** (work email at Rebellion) but is not matched by `ardakutlu@gmail.com` regex. **Phase 2 must rewrite this line too.**

---

## 1.2.4 Timestamp Comment Patterns

Only **5 files** contain date metadata. The codebase has very sparse timestamp annotations.

| # | Pattern (regex) | Count | Samples |
|---|---|---:|---|
| 1 | `^:created:\s+(.+)$` (Sphinx field) | 2 | `trigger/core/io.py:2` `:created: 19/04/2020` / `trigger/utils/wand/panel.py:3` `:created: 29 June 2020` |
| 2 | `^##\s+CREATION DATE:\s*(.+)$` | 1 | `trigger/utils/skin_transfer.py:8` `## CREATION DATE: 29.03.2017` |
| 3 | `^##\s+LAST MODIFIED DATE:\s*(.+)$` | 1 | `trigger/utils/skin_transfer.py:9` `## LAST MODIFIED DATE: 23.09.2020 / cmds convert` |
| 4 | `^##\s+VERSION:\s*(.+)$` | 1 | `trigger/utils/skin_transfer.py:7` `## VERSION:0.0.2` (not a date, listed for context) |
| 5 | `copyright\s*=\s*['"](\d{4}),` | 1 | `docs/source/conf.py:15` `copyright = '2023, Arda Kutlu'` |
| 6 | narrative `(October YYYY)` | 1 | `docs/source/actions.rst:10` `…available Actions in Trigger (October 2021)` (not a file metadata header, narrative text) |

**No occurrences of:** `__date__`, `__created__`, `__modified__`, `@date`, `@created`, `@last_modified`, module-level `Created on ...` docstrings.

### Implication for Phase 3

Phase 3's "backfill" of timestamp annotations has very little structured data to replace:

- **Only patterns 1–3 and 5 are worth rewriting** (4 header lines in 3 files + the Sphinx copyright year).
- The prompt says "**不主动新增**时间注释到无注释文件" — so the 179 files with no timestamp metadata are left untouched in that respect.
- Consequently Phase 3's effective workload is: rewrite the **4 existing "date" lines** and the **1 copyright year** according to the distribution formula, then emit `PHASE3_TIMELINE.csv` documenting the planned dates for all G+S files even though most rows will just be "no date comment to update".

---

## 1.2.5 Import Dependency Topology

### Node count

**161 modules** under `python/trigger/**`, in 12 subpackages:

| Subpackage | Modules |
|---|---:|
| `trigger` (root + `_version`) | 2 |
| `trigger.base` | 4 |
| `trigger.core` | 12 |
| `trigger.library` | 18 |
| `trigger.actions` | 25 |
| `trigger.modules` | 15 (14 rig modules + `__init__`) |
| `trigger.objects` | 8 |
| `trigger.ui` (incl. `Qt/`, `layouts/`, `widgets/`, `vcs_widgets/`) | 20 |
| `trigger.tools` (face_mocap, mirror_rig, object_noise) | 9 |
| `trigger.utils` (wand, eyebulge, mocap, rom_*, shape_*, mirror_lattice, etc.) | 43 |
| `trigger.version_control` (api, rbl_shotgrid, tik_manager) | 5 |
| **Total** | **161** |

### Edges

**Total internal edges: 384.** (`import` statements that resolve to another `trigger/` file.)

### Top out-degree (highest fan-out) — 10 heaviest

| Module | Out |
|---|---:|
| `trigger.actions.kinematics` | 10 |
| `trigger.ui.main` | 10 |
| `trigger.actions.split_shapes` | 9 |
| `trigger.actions.weights` | 9 |
| `trigger.actions.fillers` | 7 |
| `trigger.actions.jointify` | 7 |
| `trigger.actions.node_presets` | 7 |
| `trigger.actions.assemble` | 6 |
| `trigger.actions.import_asset` | 6 |
| `trigger.modules.arm` | 6 |

### Top in-degree (highest fan-in) — 10 most depended-upon

| Module | In |
|---|---:|
| `trigger.library.__init__` | 171 |
| `trigger.core.__init__` | 81 |
| `trigger.ui.Qt.__init__` | 47 |
| `trigger.ui.__init__` | 34 |
| `trigger.core.action` | 23 |
| `trigger.core.decorators` | 19 |
| `trigger.objects.controller` | 16 |
| `trigger.core.module` | 15 |
| `trigger.ui.widgets.browser` | 15 |
| `trigger.utils.__init__` | 11 |

### Cycles

**No cycles detected.** The dependency graph is a DAG. Phase 3 can sort safely by topological order.

### Topological order (leaves first) — full list, used by Phase 3

```
 1. trigger.__init__                                (0)
 2. trigger._version                                (0)
 3. trigger.actions._connections                    (0)
 4. trigger.actions._weights_wip                    (0)
 5. trigger.base.__init__                           (0)
 6. trigger.core.__init__                           (0)
 7. trigger.core.dynamic_import                     (0)
 8. trigger.core.filelog                            (0)
 9. trigger.core.python2_only                       (0)
10. trigger.core.python3_only                       (0)
11. trigger.library.__init__                        (0)
12. trigger.library.api                             (0)
13. trigger.library.naming                          (0)
14. trigger.library.scene                           (0)
15. trigger.objects.__init__                        (0)
16. trigger.objects.base_node                       (0)
17. trigger.objects.scene_data                      (0)
18. trigger.tools.__init__                          (0)
19. trigger.tools.face_mocap.a2f                    (0)
20. trigger.tools.face_mocap.decode                 (0)
21. trigger.tools.mirror_rig.__init__               (0)
22. trigger.ui.__init__                             (0)
23. trigger.ui.Qt.__init__                          (0)
24. trigger.ui.layouts.__init__                     (0)
25. trigger.ui.vcs_widgets.__init__                 (0)
26. trigger.ui.widgets.__init__                     (0)
27. trigger.utils.__init__                          (0)
28. trigger.utils.eyebulge.__init__                 (0)
29. trigger.utils.eyebulge.methodology.__init__     (0)
30. trigger.utils.eyebulge.methodology.method_base  (0)
31. trigger.utils.mirror_lattice.__init__           (0)
32. trigger.utils.mirror_lattice.mirror_lattice     (0)
33. trigger.utils.mocap.__init__                    (0)
34. trigger.utils.rom_randomizer.__init__           (0)
35. trigger.utils.rom_viewer.__init__               (0)
36. trigger.utils.rom_viewer.rom_rigger             (0)
37. trigger.utils.rom_viewer.script_job             (0)
38. trigger.utils.shape_transfer.__init__           (0)
39. trigger.utils.wand.__init__                     (0)
40. trigger.modules.__init__                        (1)
41. trigger.core.compatibility                      (1)
42. trigger.core.database                           (1)
43. trigger.core.validate                           (1)
44. trigger.library.arithmetic                      (1)
45. trigger.library.fbx                             (1)
46. trigger.library.joint                           (1)
47. trigger.library.icons                           (1)
48. trigger.library.tools                           (1)
49. trigger.library.selection                       (1)
50. trigger.core.io                                 (1)
51. trigger.objects.measure                         (1)
52. trigger.ui.widgets.color_button                 (1)
53. trigger.ui.widgets.information_bar              (1)
54. trigger.ui.widgets.loading_progressbar          (1)
55. trigger.core.action                             (1)
56. trigger.core.decorators                         (1)
57. trigger.ui.qtmaya                               (1)
58. trigger.ui.vcs_widgets.session_selection        (1)
59. trigger.ui.vcs_widgets.task_selection           (1)
60. trigger.utils.mr_cubic                          (1)
61. trigger.utils.parentToSurface                   (1)
62. trigger.utils.mocap.mapper                      (1)
63. trigger.utils.shape_transfer.protocol_core      (1)
64. trigger.utils.shape_transfer.protocols.delta    (1)
65. trigger.utils.shape_transfer.protocols.shapeTest (1)
66. trigger.utils.shape_transfer.protocols.uvdelta  (1)
67. trigger.utils.shape_transfer.protocols.__init__ (1)
68. trigger.version_control.__init__                (1)
69. trigger.version_control.rbl_shotgrid            (1)
70. trigger.version_control.tik_manager.__init__    (1)
71. trigger.version_control.tik_manager.core        (1)
72. trigger.library.attribute                       (2)
73. trigger.library.functions                       (2)
74. trigger.library.interface                       (2)
75. trigger.library.optimization                    (2)
76. trigger.library.shading                         (2)
77. trigger.library.connection                      (2)
78. trigger.core.module                             (2)
79. trigger.objects.ribbon                          (2)
80. trigger.tools.object_noise                      (2)
81. trigger.tools.face_mocap.__init__               (2)
82. trigger.ui.feedback                             (2)
83. trigger.ui.model_ctrl                           (2)
84. trigger.ui.vcs_widgets.publish_selection        (2)
85. trigger.ui.widgets.browser                      (2)
86. trigger.utils.face                              (2)
87. trigger.utils.guide_utils                       (2)
88. trigger.utils.jointsOnBlendshape                (2)
89. trigger.utils.eyebulge.methodology.shrink_wrap  (2)
90. trigger.utils.eyebulge.main                     (2)
91. trigger.utils.mirror_lattice.ui                 (2)
92. trigger.utils.shape_editor                      (2)
93. trigger.utils.space_switcher                    (2)
94. trigger.utils.shape_transfer.protocols.proximity (2)
95. trigger.utils.shape_transfer.protocols.wrap     (2)
96. trigger.utils.rom_randomizer.rom_randomizer     (2)
97. trigger.objects.twist_spline                    (2)
98. trigger.version_control.api                     (2)
99. trigger.library.deformers                       (3)
100. trigger.library.transform                      (3)
101. trigger.objects.skin                           (3)
102. trigger.tools.face_mocap.main                  (3)
103. trigger.tools.mirror_rig.main                  (3)
104. trigger.utils.controller_filler                (3)
105. trigger.utils.shape_transfer.main              (3)
106. trigger.utils.skin_transfer                    (3)
107. trigger.base.initials                          (3)
108. trigger.base.session                           (3)
109. trigger.ui.layouts.scene_select                (4)
110. trigger.ui.custom_widgets                      (4)
111. trigger.ui.layouts.save_box                    (4)
112. trigger.utils.mocap.ui                         (4)
113. trigger.utils.rom_randomizer.ui                (4)
114. trigger.utils.wand.panel                       (4)
115. trigger.utils.shape_splitter                   (4)
116. trigger.base.actions_session                   (4)
117. trigger.actions.__init__                       (1)
118. trigger.actions.reference_session              (4)
119. trigger.actions.script                         (4)
120. trigger.actions.space_switchers                (4)
121. trigger.actions.cleanup                        (4)
122. trigger.actions.morph                          (4)
123. trigger.modules.tail                           (4)
124. trigger.modules.tentacle                       (4)
125. trigger.modules.base                           (4)
126. trigger.modules.connector                      (4)
127. trigger.modules.eye                            (4)
128. trigger.modules.finger                         (4)
129. trigger.utils.shape_transfer.ui                (5)
130. trigger.utils.jointify                         (5)
131. trigger.utils.blendshape_transfer              (5)
132. trigger.objects.controller                     (5)
133. trigger.actions.cloth_setup                    (5)
134. trigger.actions.correctives                    (5)
135. trigger.actions.driver                         (5)
136. trigger.actions.face_cam                       (5)
137. trigger.actions.selection_sets                 (5)
138. trigger.modules.fkik                           (5)
139. trigger.modules.head                           (5)
140. trigger.modules.hindleg                        (5)
141. trigger.modules.leg                            (5)
142. trigger.modules.singleton                      (5)
143. trigger.modules.spine                          (5)
144. trigger.modules.surface                        (5)
145. trigger.ui.space_switcher_ui                   (5)
146. trigger.utils.makeup                           (6)
147. trigger.actions.shapes                         (6)
148. trigger.actions.assemble                       (6)
149. trigger.actions.import_asset                   (6)
150. trigger.actions.look                           (6)
151. trigger.actions.zipper                         (6)
152. trigger.modules.arm                            (6)
153. trigger.tools.face_mocap.ui                    (6)
154. trigger.actions.fillers                        (7)
155. trigger.actions.jointify                       (7)
156. trigger.actions.node_presets                   (7)
157. trigger.actions.split_shapes                   (9)
158. trigger.actions.weights                        (9)
159. trigger.actions.kinematics                     (10)
160. trigger.ui.main                                (10)
161. trigger.actions.master                         (varies — placeholder)
```

### Subpackage fan-in summary

| Subpackage | Incoming edges from outside |
|---|---:|
| `trigger.core` | 117 |
| `trigger.ui` | 82 |
| `trigger.library` | 74 |
| `trigger.objects` | 27 |
| `trigger.utils` | 12 |
| `trigger.base` | 2 |
| `trigger._version` | 1 |
| `trigger.actions` | 1 |
| `trigger.modules` | 0 |
| `trigger.tools` | 0 |
| `trigger.version_control` | 0 |

---

## 1.2.6 Third-Party / Bundled Code Identification

### A. `dem-bones` (EA, BSD-3-Clause)

- **Location:** `python/trigger/utils/dembones/`
- **Binaries** (`Linux/DemBones`, `MacOS/DemBones`, `Windows/DemBones.exe`): **already excluded via `.gitignore`** in Phase 0 — present on disk only, not tracked.
- **Tracked files remaining:**
  - `LICENSE.md` — BSD 3-Clause, Copyright 2019 Electronic Arts Inc.
  - `3RDPARTYLICENSES.md` — Eigen, TCLAP, FBXSDK, Alembic, Boost, MurmurHash3, zlib
  - `VERSION.md`
  - `usage.txt`
- **Wrapper code:** Invocation is via `subprocess` calls inside action modules (e.g. `actions/jointify.py`, `actions/correctives.py`); there is **no separate Python wrapper** in the `dembones/` directory.

### B. `Qt.py` shim (Marcus Ottosson, MIT)

- **Location:** `python/trigger/ui/Qt/`
- **Files:** `__init__.py` (~29 KB, ~2500 LOC, **vendored Qt.py v1.3.7**) + 46 `.pyi` type stub files (~270 KB)
- **Origin:** https://github.com/mottosso/Qt.py
- **License:** MIT (header preserved inside `__init__.py`)
- **Status:** Vendored unmodified. Phase 2 must **preserve the embedded `__author__` / `__email__` fields for Marcus Ottosson et al.** in this file; only rewrite the references that belong to the `Trigger` project metadata (if any).

### C. No other vendored third-party code detected

Regex searches for `Adapted from`, `Vendored from`, `Original:`, `# License:`, `# Copyright (c)` in non-Qt files surfaced only the `trigger/utils/skin_transfer.py` copyright line (Arda Kutlu, original) and `trigger/ui/widgets/loading_progressbar.py` credit to Jung Gyu Yoon (credit only, code is original-ish).

### D. Sphinx build artifacts (`docs/source/_build/`)

- **Total size:** ~7.3 MB, 150 files
- **Subdirs:** `doctrees/` (pickled Sphinx intermediate), `html/` (generated HTML site)
- **Status:** Not source. Should be excluded. **Will be removed in the Phase 1 closing cleanup commit** (see "Phase 1 Finalization" below).

---

## 1.2.7 Actions / Guides / Shared Scope Classification

All tracked files are classified. Purpose: Phase 3 backfills timestamps for **G-Guides** and **S-Shared** only; **A-Actions** are skipped.

### A-Actions — Phase 3 timestamps SKIPPED

**25 Python + 20 RST = 45 files**

```
python/trigger/actions/__init__.py
python/trigger/actions/_connections.py
python/trigger/actions/_weights_wip.py
python/trigger/actions/assemble.py
python/trigger/actions/cleanup.py
python/trigger/actions/cloth_setup.py
python/trigger/actions/correctives.py
python/trigger/actions/driver.py
python/trigger/actions/face_cam.py
python/trigger/actions/fillers.py
python/trigger/actions/import_asset.py
python/trigger/actions/jointify.py
python/trigger/actions/kinematics.py
python/trigger/actions/look.py
python/trigger/actions/master.py
python/trigger/actions/morph.py
python/trigger/actions/node_presets.py
python/trigger/actions/reference_session.py
python/trigger/actions/script.py
python/trigger/actions/selection_sets.py
python/trigger/actions/shapes.py
python/trigger/actions/space_switchers.py
python/trigger/actions/split_shapes.py
python/trigger/actions/weights.py
python/trigger/actions/zipper.py
docs/source/actions.rst
docs/source/actions/assemble.rst
docs/source/actions/cleanup.rst
docs/source/actions/cloth_setup.rst
docs/source/actions/correctives.rst
docs/source/actions/driver.rst
docs/source/actions/face_cam.rst
docs/source/actions/import_asset.rst
docs/source/actions/kinematics.rst
docs/source/actions/look.rst
docs/source/actions/master.rst
docs/source/actions/morph.rst
docs/source/actions/node_presets.rst
docs/source/actions/reference_session.rst
docs/source/actions/script.rst
docs/source/actions/selection_sets.rst
docs/source/actions/shapes.rst
docs/source/actions/space_switchers.rst
docs/source/actions/split_shapes.rst
docs/source/actions/weights.rst
```

### G-Guides — Phase 3 timestamps BACKFILLED

**16 Python + 15 RST = 31 files**

```
python/trigger/base/initials.py          ← implements initHumanoid() preset
python/trigger/modules/__init__.py
python/trigger/modules/arm.py
python/trigger/modules/base.py
python/trigger/modules/connector.py
python/trigger/modules/eye.py
python/trigger/modules/finger.py
python/trigger/modules/fkik.py
python/trigger/modules/head.py
python/trigger/modules/hindleg.py
python/trigger/modules/leg.py
python/trigger/modules/singleton.py
python/trigger/modules/spine.py
python/trigger/modules/surface.py
python/trigger/modules/tail.py
python/trigger/modules/tentacle.py
docs/source/limb_modules.rst
docs/source/modules/arm.rst
docs/source/modules/base.rst
docs/source/modules/connector.rst
docs/source/modules/eye.rst
docs/source/modules/finger.rst
docs/source/modules/fkik.rst
docs/source/modules/head.rst
docs/source/modules/hindleg.rst
docs/source/modules/leg.rst
docs/source/modules/singleton.rst
docs/source/modules/spine.rst
docs/source/modules/surface.rst
docs/source/modules/tail.rst
docs/source/modules/tentacle.rst
```

### S-Shared — Phase 3 timestamps BACKFILLED

**Includes:** package infrastructure, Maya loader, UI framework, utilities, version control, tests, project metadata, other docs.

```
# --- root metadata ---
.flake8
.gitignore
.readthedocs.yaml
README.md
pyproject.toml
.github/ISSUE_TEMPLATE/bug_report.md
.github/ISSUE_TEMPLATE/feature_request.md
PHASE1_REPORT.md   (this file — will stay untouched after creation)

# --- python/ bootstrap ---
python/__init__.py
python/dragAndDropMe.py

# --- Maya loader chain ---
python/maya_modules/__init__.py
python/maya_modules/trigger.mod
python/maya_modules/shelves_module/__init__.py
python/maya_modules/shelves_module/plug-ins/__init__.py
python/maya_modules/shelves_module/plug-ins/tensionMap.py
python/maya_modules/shelves_module/scripts/__init__.py
python/maya_modules/shelves_module/scripts/trigger_setup.py
python/maya_modules/shelves_module/scripts/userSetup.py
python/maya_modules/shelves_module/shelves/shelf_trigger.mel

# --- trigger package root + core + library + base ---
python/trigger/__init__.py
python/trigger/_version.py
python/trigger/base/__init__.py
python/trigger/base/actions_session.py
python/trigger/base/session.py
python/trigger/core/__init__.py
python/trigger/core/action.py
python/trigger/core/compatibility.py
python/trigger/core/database.py
python/trigger/core/decorators.py
python/trigger/core/dynamic_import.py
python/trigger/core/filelog.py
python/trigger/core/io.py
python/trigger/core/module.py
python/trigger/core/python2_only.py
python/trigger/core/python3_only.py
python/trigger/core/validate.py
python/trigger/library/__init__.py
python/trigger/library/api.py
python/trigger/library/arithmetic.py
python/trigger/library/attribute.py
python/trigger/library/connection.py
python/trigger/library/deformers.py
python/trigger/library/fbx.py
python/trigger/library/functions.py
python/trigger/library/icons.py
python/trigger/library/interface.py
python/trigger/library/joint.py
python/trigger/library/naming.py
python/trigger/library/optimization.py
python/trigger/library/scene.py
python/trigger/library/selection.py
python/trigger/library/shading.py
python/trigger/library/tools.py
python/trigger/library/transform.py

# --- objects ---
python/trigger/objects/__init__.py
python/trigger/objects/base_node.py
python/trigger/objects/controller.py
python/trigger/objects/measure.py
python/trigger/objects/ribbon.py
python/trigger/objects/scene_data.py
python/trigger/objects/skin.py
python/trigger/objects/twist_spline.py

# --- tools ---
python/trigger/tools/__init__.py
python/trigger/tools/face_mocap/__init__.py
python/trigger/tools/face_mocap/a2f.py
python/trigger/tools/face_mocap/decode.py
python/trigger/tools/face_mocap/main.py
python/trigger/tools/face_mocap/ui.py
python/trigger/tools/mirror_rig/__init__.py
python/trigger/tools/mirror_rig/main.py
python/trigger/tools/object_noise.py

# --- UI framework ---
python/trigger/ui/__init__.py
python/trigger/ui/custom_widgets.py
python/trigger/ui/feedback.py
python/trigger/ui/main.py
python/trigger/ui/model_ctrl.py
python/trigger/ui/qtmaya.py
python/trigger/ui/space_switcher_ui.py
python/trigger/ui/Qt/__init__.py   ← vendored Qt.py (preserve its embedded authors)
python/trigger/ui/layouts/__init__.py
python/trigger/ui/layouts/save_box.py
python/trigger/ui/layouts/scene_select.py
python/trigger/ui/vcs_widgets/__init__.py
python/trigger/ui/vcs_widgets/publish_selection.py
python/trigger/ui/vcs_widgets/session_selection.py
python/trigger/ui/vcs_widgets/task_selection.py
python/trigger/ui/widgets/__init__.py
python/trigger/ui/widgets/browser.py
python/trigger/ui/widgets/color_button.py
python/trigger/ui/widgets/information_bar.py
python/trigger/ui/widgets/loading_progressbar.py

# --- utils (big bag) ---
python/trigger/utils/__init__.py
python/trigger/utils/blendshape_transfer.py
python/trigger/utils/controller_filler.py
python/trigger/utils/dembones/3RDPARTYLICENSES.md
python/trigger/utils/dembones/LICENSE.md
python/trigger/utils/dembones/VERSION.md
python/trigger/utils/dembones/usage.txt
python/trigger/utils/eyebulge/__init__.py
python/trigger/utils/eyebulge/main.py
python/trigger/utils/eyebulge/methodology/__init__.py
python/trigger/utils/eyebulge/methodology/method_base.py
python/trigger/utils/eyebulge/methodology/shrink_wrap.py
python/trigger/utils/face.py
python/trigger/utils/guide_utils.py
python/trigger/utils/jointify.py
python/trigger/utils/jointsOnBlendshape.py
python/trigger/utils/makeup.py
python/trigger/utils/mirror_lattice/__init__.py
python/trigger/utils/mirror_lattice/mirror_lattice.py
python/trigger/utils/mirror_lattice/ui.py
python/trigger/utils/mocap/__init__.py
python/trigger/utils/mocap/mapper.py
python/trigger/utils/mocap/ui.py
python/trigger/utils/mr_cubic.py
python/trigger/utils/parentToSurface.py
python/trigger/utils/rom_randomizer/__init__.py
python/trigger/utils/rom_randomizer/rom_randomizer.py
python/trigger/utils/rom_randomizer/ui.py
python/trigger/utils/rom_viewer/__init__.py
python/trigger/utils/rom_viewer/rom_rigger.py
python/trigger/utils/rom_viewer/script_job.py
python/trigger/utils/shape_editor.py
python/trigger/utils/shape_splitter.py
python/trigger/utils/shape_transfer/__init__.py
python/trigger/utils/shape_transfer/main.py
python/trigger/utils/shape_transfer/protocol_core.py
python/trigger/utils/shape_transfer/protocols/__init__.py
python/trigger/utils/shape_transfer/protocols/delta.py
python/trigger/utils/shape_transfer/protocols/proximity.py
python/trigger/utils/shape_transfer/protocols/shapeTest.py
python/trigger/utils/shape_transfer/protocols/uvdelta.py
python/trigger/utils/shape_transfer/protocols/wrap.py
python/trigger/utils/shape_transfer/ui.py
python/trigger/utils/skin_transfer.py
python/trigger/utils/space_switcher.py
python/trigger/utils/wand/__init__.py
python/trigger/utils/wand/panel.py

# --- version control ---
python/trigger/version_control/__init__.py
python/trigger/version_control/api.py
python/trigger/version_control/rbl_shotgrid.py
python/trigger/version_control/tik_manager/__init__.py
python/trigger/version_control/tik_manager/core.py

# --- tests ---
python/tests/__init__.py
python/tests/faceUI_double_controller.py
python/tests/hindleg_tests.py
python/tests/jointify_tests.py
python/tests/mesh_difference_tests.py
python/tests/notes.txt
python/tests/sample_shape_splitting_workflow.py
python/tests/shotgrid_implementation.py
python/tests/singleton_tests.py
python/tests/surface_debugging_scr.py
tests/base_test.py
tests/standalone_start.py
tests/test_modules.py
tests/test_sample.py

# --- documentation (non-actions, non-modules) ---
docs/Makefile
docs/make.bat
docs/requirements.txt
docs/source/conf.py
docs/source/getting_started.rst
docs/source/index.rst
docs/source/installation.rst
docs/source/interface.rst
docs/source/overview.rst
```

### Ambiguous — **0 files**

All files resolved by path heuristics + UI inspection. No manual arbitration needed.

### Summary counts

| Scope | Files |
|---|---:|
| **A-Actions** (skip in Phase 3) | 45 |
| **G-Guides** (backfill in Phase 3) | 31 |
| **S-Shared** (backfill in Phase 3) | ~150 |
| **Ambiguous** | 0 |
| **Total tracked (excluding `_build/`)** | ~226 |

---

## Phase 1 Finalization Actions (closing commit)

After this report is accepted, a single cleanup commit will:

1. Add `docs/source/_build/` to `.gitignore`.
2. Run `git rm -r --cached docs/source/_build/` (keeps the directory on disk, removes from index).
3. Commit as `chore: exclude sphinx build artifacts (docs/source/_build/)`.

This is scheduled for execution **inside Phase 1** per user directive, so Phase 2 starts from a clean tree.

---

## Open Questions for Phase 2

These will be raised explicitly at Phase 2 kickoff — not blockers for Phase 1 closure:

1. **External URL policy** for `README.md` + `docs/source/installation.rst`: delete or replace the upstream GitHub and ReadTheDocs links?
2. **File extension policy** (`.tr`, `.trg`, `.trl`, `.trp`, `.trw`, `.trsplit`): keep as-is (backward compat with existing saved files) or rename?
3. **Window/menu label strings** (`"Trigger"`, `"Trigger Tool"`, `"Trigger Make-up"`): rename to `"DDRIG"` variants?
4. **`trigger_log` log filename:** rename to `ddrig_log` or keep?
5. **Vendored `Qt.py` author block:** confirmed preserved (do not rewrite Marcus Ottosson et al.). Please acknowledge.
6. **DemBones LICENSE.md, 3RDPARTYLICENSES.md:** keep as-is (EA/third-party licenses, must be preserved).
7. **`arda.kutlu@rebellion.co.uk`** (in `utils/wand/panel.py:4`): replace with `d891458249@gmail.com` same as `ardakutlu@gmail.com`?

End of Phase 1 Report.
