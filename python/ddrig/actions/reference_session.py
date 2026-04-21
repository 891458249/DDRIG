"""Reference another DDRIG session"""

import os
from ddrig.core import filelog

# from ddrig.base.actions_session import ActionsSession

import importlib
from ddrig.ui.Qt import QtWidgets
from ddrig.ui.widgets.browser import BrowserButton, FileLineEdit

from ddrig.core.action import ActionCore


log = filelog.Filelog(logname=__name__, filename="ddrig_log")


ACTION_DATA = {"ddrig_file_path": ""}


# Name of the class MUST be the capitalized version of file name. eg. morph.py => Morph, split_shapes.py => Split_shapes
class Reference_session(ActionCore):
    action_data = ACTION_DATA

    def __init__(self, **kwargs):
        super(Reference_session, self).__init__(kwargs)
        # user defined variables
        self.ddrigFilePath = None

        # class variables

    def feed(self, action_data, *args, **kwargs):
        """Mandatory Method - Feeds the instance with the action data stored in actions session"""
        self.ddrigFilePath = action_data.get("ddrig_file_path")

    def action(self):
        """Mandatory Method - Execute Action"""
        # everything in this method will be executed automatically.
        # This method does not accept any arguments. all the user variable must be defined to the instance before
        if not self.ddrigFilePath:
            log.warning("Reference DDRIG Session path not defined. Skipping")
            return
        if not os.path.isfile(self.ddrigFilePath):
            log.error("DDRIG File does not exists => %s" % self.ddrigFilePath)

        actions_session = importlib.import_module("ddrig.base.actions_session")
        referenced_session = actions_session.ActionsSession()
        referenced_session.load_session(self.ddrigFilePath)
        referenced_session.run_all_actions(reset_scene=False)

    def save_action(self):
        """Mandatory Method - Save Action"""
        # This method will be called automatically and accepts no arguments.
        # If the action has an option to save files, this method will be used by the UI.
        # Else, this method can stay empty
        pass

    def ui(self, ctrl, layout, handler, *args, **kwargs):
        """
        Mandatory Method - UI setting definitions

        Args:
            ctrl: (model_ctrl) ctrl object instance of /ui/model_ctrl. Updates UI and Model
            layout: (QLayout) The layout object from the main ui. All setting widgets should be added to this layout
            handler: (actions_session) An instance of the actions_session. TRY NOT TO USE HANDLER UNLESS ABSOLUTELY NECESSARY
            *args:
            **kwargs:

        Returns: None

        """

        ddrig_file_path_lbl = QtWidgets.QLabel(text="DDRIG Session:")
        ddrig_file_path_hLay = QtWidgets.QHBoxLayout()
        ddrig_file_path_le = FileLineEdit()
        ddrig_file_path_hLay.addWidget(ddrig_file_path_le)
        browse_path_pb = BrowserButton(
            mode="openFile",
            update_widget=ddrig_file_path_le,
            filterExtensions=["DDRIG Session (*.tr)"],
            overwrite_check=False,
        )
        ddrig_file_path_hLay.addWidget(browse_path_pb)
        layout.addRow(ddrig_file_path_lbl, ddrig_file_path_hLay)

        ctrl.connect(ddrig_file_path_le, "ddrig_file_path", str)
        ctrl.update_ui()

        ddrig_file_path_le.textChanged.connect(lambda x=0: ctrl.update_model())
        browse_path_pb.clicked.connect(lambda x=0: ctrl.update_model())
        # to validate on initial browse result
        browse_path_pb.clicked.connect(ddrig_file_path_le.validate)
