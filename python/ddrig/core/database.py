"""Class to handle the database/settings"""
from copy import deepcopy
from ddrig.core import io


class Database(dict):
    def __init__(self):
        super(Database, self).__init__()
        self.userSettings = UserSettings()
        self.recentSessions = RecentSessions()


class RecentSessions(list):
    handler = io.IO(file_name="recentSessions.json", folder_name="ddrig")

    def __init__(self):
        super(RecentSessions, self).__init__()
        self._get_data()

    def _get_data(self):
        del self[:]
        data = self.handler.read()
        if data:
            self.extend(data)

    def add(self, new_path):
        if new_path in self:
            self.remove(new_path)
        self.insert(0, new_path)
        if len(self) > 9:
            self.pop(-1)
        self.handler.write(self)


class UserSettings(object):
    handler = io.IO(file_name="userSettings.json", folder_name="ddrig")
    compareData = {}
    # Class-level declaration so _parse_from's white-list check
    # (if UserSettings.__dict__.get(key)) accepts 'activeNamingRule'
    # on subsequent Maya launches.
    activeNamingRule = "DDRIG Default"

    def __init__(self):
        self.verboseLevel = 0
        self.upAxis = "+y"
        self.mirrorAxis = "+x"
        self.lookAxis = "+z"
        self.majorCenterColor = 17
        self.minorCenterColor = 20
        self.majorLeftColor = 6
        self.minorLeftColor = 18
        self.majorRightColor = 13
        self.minorRightColor = 12
        # Name of the active ddrig.library.naming_rules entry.  Written
        # back by naming_rules.set_active_rule_name() via .apply().
        self.activeNamingRule = "DDRIG Default"

        self.compareData = deepcopy(self._parse_to_dict())
        self._parse_from()

    def _parse_from(self):
        data = self.handler.read()
        if data:
            for key, value in data.items():
                if UserSettings.__dict__.get(key):
                    exec("self." + key + "=value")
            self.compareData = deepcopy(data)
        else:
            self.apply()

    def _parse_to_dict(self):
        data = deepcopy(self.__dict__)
        data.pop("handler", None)
        data.pop("compareData", None)
        return data
        # self.handler.write(data)

    def apply(self):
        self.handler.write(self._parse_to_dict())

    def is_changed(self):
        if self.compareData != self._parse_to_dict():
            return True
        else:
            return False
