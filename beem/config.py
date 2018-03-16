"""Load beem configuration data."""

import logging
from logging.handlers import RotatingFileHandler
import os
import os.path
import pytoml

from .dcss import bot_services

class BotConfig():
    """Base class for TOML config parsing for bots."""

    def __init__(self, path):
        self.data = {}
        self.path = path

    def __getattr__(self, name):
        try:
            return self.data[name]

        except KeyError:
            raise AttributeError(name)

    def get(self, *args):
        """Allow directly accessing attributes to get the corresponding TOML
        data."""

        return self.data.get(*args)

    def error(self, msg):
        raise Exception("Config file {}: {}".format(self.path, msg))

    def require_table_fields(self, table_name, table, fields,
                             condition_field=None):
        """Require the given fields be defined in the given table. If
        condition_field is defined, the fields will only be required if the
        field in condition_field is defined."""

        if condition_field and condition_field not in table:
            return

        condition_text = ""
        if condition_field:
            condition_text = "field {} defined but "
        for field in fields:
            if field not in table:
                self.error("In table {}, {}field {} undefined.".format(
                    table_name, condition_text, field))

    def init_logging(self):
        """Check the logging configuration in the TOML file and initialize the
        Python logger based on this."""

        if not self.get("logging_config"):
            self.error("logging_config table undefined.")

        log_conf = self.logging_config
        self.require_table_fields("logging_config", log_conf, ["format"])
        self.require_table_fields("logging_config", log_conf,
                                  ["max_bytes", "backup_count"], "filename")

        if log_conf.get("filename"):
            handler = RotatingFileHandler(log_conf["filename"],
                                          maxBytes=log_conf["max_bytes"],
                                          backupCount=log_conf["backup_count"])
        else:
            handler = logging.StreamHandler(None)
            handler.setFormatter(
                logging.Formatter(log_conf["format"], log_conf.get("datefmt")))

        logger = logging.getLogger()
        logger.addHandler(handler)

        if log_conf.get("level"):
            logger.setLevel(log_conf["level"])

    def check_dcss(self):
        """Check that there is a 'dcss' table in the TOML data and that it has
        the necessary entries."""

        if not self.get("dcss"):
            self.error("The dcss table is undefined.")

        self.require_table_fields("dcss", self.dcss,
                                  ["hostname", "port", "nick"])

        self.require_table_fields("dcss", self.dcss, ["password"], "username")

        if not self.dcss.get("bots"):
            self.error("No IRC bots defined in the dcss.bots table.")

        for i, entry in enumerate(self.dcss["bots"]):
            table_desc = "dcss.bots, entry {}".format(i + 1)

            self.require_table_fields(table_desc, entry, ["nick"])

            found_service = False
            pattern_fields = []
            for s in bot_services:
                field = "{}_patterns".format(s)
                pattern_fields.append(field)

                if entry.get(field):
                    found_service = True
                    break

            if not found_service:
                self.error("In {}, at least one of the pattern fields {} "
                        "must be defined.".format(table_desc,
                            ", ".join(pattern_fields)))

    def load(self):
        """Read the main TOML configuration data from self.path and check that
        the configuration is valid."""

        if not os.path.exists(self.path):
            self.error("Couldn't find file!")

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


class BeemConfig(BotConfig):
    """Handle configuration data loading for beem."""

    def check_webtiles(self):
        """Check that there is a 'dcss' table in the TOML data and that it has
        the necessary entries."""

        webtiles = self.webtiles
        self.require_table_fields("webtiles", webtiles,
                                  ["server_url", "protocol_version",
                                   "username", "password", "help_text"])

        if self.get("watch_player"):
            self.webtiles["max_watched_subscribers"] = 1
            self.webtiles["max_game_idle"] = float("inf")
            self.webtiles["game_rewatch_timeout"] = float("inf")
            self.webtiles["autowatch_enabled"] = False
            return

        self.require_table_fields("webtiles", webtiles,
                                  ["max_watched_subscribers", "max_game_idle",
                                   "game_rewatch_timeout"])

        self.require_table_fields("webtiles", webtiles,
                                  ["min_autowatch_spectators"],
                                  "autowatch_enabled")

    def load(self):
        """Read the main TOML configuration data from self.path and check that
        the configuration is valid."""

        super().load()

        if not self.get("db_file"):
            self.error("Field db_file undefined.")

        self.check_webtiles()
        self.check_dcss()
