"""Load the beem configuration data."""

import logging
from logging.handlers import RotatingFileHandler
import os
import os.path
import pytoml

_DEFAULT_CONFIG_PATH = "./beem_config.toml"

_log = logging.getLogger()

class BeemConfig(object):
    """Holds the beem configuration data loaded from the TOML file. There
    is one instance of this class available as `config.conf`.

    """

    def __init__(self, path=_DEFAULT_CONFIG_PATH):
        self.data = {}
        self.path = path

    def __getattr__(self, name):
        try:
            return self.data[name]
        except KeyError:
            raise AttributeError(name)

    def get(self, *args):
        """Allow directly accessing attributes to get the corresponding TOML
        data.

        """

        return self.data.get(*args)

    def require_fields(self, table, fields):
        for field in fields:
            if not self.get(table).get(field):
                self.error("In {} table, {} undefined.".format(table, field))

    def check_path(self):
        if not self.path and os.environ.get("BEEM_CONF"):
            self.path = os.environ["BEEM_CONF"]

        if not os.path.exists(self.path):
            errmsg = "Couldn't find the config file ({})!".format(self.path)
            if (self.path == _DEFAULT_CONFIG_PATH
                and os.path.exists(self.path + ".sample")):
                errmsg += (" Copy beem_config.toml.sample to beem_config.toml "
                           "and edit.")
            self.error(errmsg)

    def init_logging(self):
        if not self.get("logging_config"):
            self.error("logging_config table undefined.")

        log_conf = self.logging_config
        self.require_fields("logging_config", ["format"])

        if log_conf.get("filename"):
            if not log_conf.get("max_bytes"):
                self.error("in logging_config table, filename enabled but "
                           "max_bytes undefined.")

            if not log_conf.get("backup_count"):
                self.error("in logging_config table, filename enabled but "
                           "backup_count undefined.")

            handler = RotatingFileHandler(log_conf["filename"],
                                          maxBytes=log_conf["max_bytes"],
                                          backupCount=log_conf["backup_count"])
        else:
            handler = logging.StreamHandler(None)
            handler.setFormatter(
                logging.Formatter(log_conf["format"], log_conf.get("datefmt")))

        _log.addHandler(handler)

        if log_conf.get("level"):
            _log.setLevel(log_conf["level"])

    def check_dcss(self):
        if not self.get("dcss"):
            self.error("The dcss table is undefined.")

        self.require_fields("dcss",
                            ["hostname", "port", "nick", "sequell_nick",
                             "sequell_patterns", "gretell_nick",
                            "gretell_patterns", "cheibriados_nick",
                             "cheibriados_patterns"])

    def check_webtiles(self):
        if not self.get("webtiles") or not self.webtiles.get("enabled"):
            return

        webtiles = self.webtiles
        self.require_fields("webtiles", ["server_url", "username", "password"])


        if self.get("single_user"):
            if not webtiles.get("watch_user"):
                self.error("single_user enabled but watch_user not defined in "
                           "webtiles table")
            self.webtiles["max_watched_subscribers"] = 1
            self.webtiles["max_game_idle"] = float("inf")
            self.webtiles["game_rewatch_timeout"] = float("inf")
            self.webtiles["autowatch_enabled"] = False
            return

        self.require_fields("webtiles", ["max_watched_subscribers",
                                         "max_game_idle",
                                         "game_rewatch_timeout"])

        if webtiles.get("autowatch_enabled"):
            self.require_fields("webtiles", ["min_autowatch_spectators"])

        if webtiles.get("twitch_reminder_text"):
            self.require_fields("webtiles", ["twitch_reminder_period"])

    def check_twitch(self):
        if not self.get("twitch") or not self.twitch.get("enabled"):
            return

        self.require_fields("twitch", ["hostname", "port", "nick", "password",
                                       "message_limit",
                                       "moderator_message_limit",
                                       "message_timeout", "max_chat_idle",
                                       "request_expire_time"])

        if self.get("single_user"):
            if not self.twitch.get("watch_user"):
                self.error("single_user enabled but watch_user not defined in "
                           "twitch table")
            self.twitch["max_watched_subscribers"] = 1
            self.twitch["max_chat_idle"] = float("inf")
            self.twitch["request_expire_time"] = float("inf")
            return

        self.require_fields("twitch", ["max_watched_subscribers",
                                        "max_chat_idle", "request_expire_time"])

    def load(self):
        """Read the main TOML configuration data from self.path and check
        that the configuration is valid.

        """

        self.check_path()

        try:
            config_fh = open(self.path, "r")
        except EnvironmentError as e:
            self.error("Couldn't open file: ({})".format(e.strerror))
        else:
            try:
                self.data = pytoml.load(config_fh)
            except pytoml.TomlError as e:
                self.error("Couldn't parse TOML file {} at line {}, col {}: "
                           "{}".format(e.filename, e.line, e.col, e.message))
            finally:
                config_fh.close()

        self.init_logging()
        self.check_dcss()
        self.check_webtiles()
        self.check_twitch()

        have_service = False
        for service in services:
            if self.service_enabled(service):
                have_service = True
                break
        if not have_service:
            self.error("At least one service must be enabled")

    def user_is_admin(self, service, user):
        """Return True if the user is a beem admin for the given service."""

        admins = self.get(service).get("admins")
        if not admins:
            return False

        for u in admins:
            if u.lower() == user.lower():
                return True
        return False

    def service_enabled(self, service):
        """Return True if the given service is enabled in the configuration"""

        assert service in services

        return self.get(service) and self.get(service).get("enabled")

    def error(self, msg):
        raise Exception("Config file {}: {}".format(self.path, msg))


# Service data is available from this module, but the entries are set
# by the appropriate module.
services = {}

# This beem configuration instance available to other modules.
beem_conf = BeemConfig()
