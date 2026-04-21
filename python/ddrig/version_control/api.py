"""This module is to communicate with other python applications sharing the same interpreter session."""

class ApiHandler():
    main_ui = None

    @classmethod
    def set_ddrig_handler(cls, ddrig_handler):
        """Define the ddrig handler."""
        cls.main_ui = ddrig_handler

    def validate_ddrig_handler(self):
        """Validate the ddrig handler."""
        if not self.main_ui:
            raise RuntimeError("DDRIG handler is not defined.")

    def save_session(self):
        """Save the current session."""
        self.validate_ddrig_handler()
        self.main_ui.save_ddrig()

    def save_session_as(self, file_path):
        """Save the current session."""
        self.validate_ddrig_handler()
        self.main_ui.vcs_save_session(file_path)
        return file_path

    def export_session(self, file_path):
        """Export the current session to the given file path."""
        self.validate_ddrig_handler()
        self.main_ui.actions_handler.export_session(file_path)

    def open_session(self, file_path):
        """Open the given file path."""
        self.validate_ddrig_handler()
        self.main_ui.open_ddrig(file_path)

    def build_session(self):
        """Build the session."""
        self.validate_ddrig_handler()
        self.main_ui.actions_handler.run_all_actions()

    def is_modified(self):
        """Returns True if the scene has unsaved changes."""
        self.validate_ddrig_handler()
        return self.main_ui.actions_handler.is_modified()

    def get_session_file(self):
        """Get the current ddrig session."""
        self.validate_ddrig_handler()
        return self.main_ui.actions_handler.session_path

    def get_ddrig_version(self):
        """Return the version of the ddrig."""
        self.validate_ddrig_handler()
        return self.main_ui.get_version()

    def update_info(self, *args, **kwargs):
        work_obj, version = self.tik.project.get_current_work()

        if work_obj:
            self.display_widgets.resolved_text.set_text(
                f"{work_obj.path}/{work_obj.name} - Version:{version}"
            )
            self.display_widgets.resolved_text.set_color("cyan")
        else:
            self.display_widgets.resolved_text.set_text(
                "Current Session is not a Tik Manager Work"
            )
            self.display_widgets.resolved_text.set_color("yellow")

