"""Defines the `config.conf` beem configuration instance and functions
for looking up and modifying the user DB.

"""

import sqlite3
import logging
import os
import os.path
import pytoml

_DEFAULT_CONFIG_PATH = "./beem_config.toml"

class beem_config(object):
    """Holds the beem configuration data loaded from the TOML file. There
    is one instance of this class available as `config.conf`.

    """

    def __init__(self, path=_DEFAULT_CONFIG_PATH):
        self._data = {}
        self.path = path

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    def get(self, *args):
        """Allow directly accessing attributes to get the corresponding TOML
        data.

        """

        return self._data.get(*args)

    def _require_fields(self, table, fields):
        for field in fields:
            if not self.get(table).get(field):
                _error("In {} table, {} undefined.".format(table, field))

    def _check_path(self):
        if not self.path and os.environ.get("BEEM_CONF"):
            self.path = os.environ["BEEM_CONF"]

        if not os.path.exists(self.path):
            errmsg = "Couldn't find the config file ({})!".format(self.path)
            if (self.path == _DEFAULT_CONFIG_PATH
                and os.path.exists(self.path + ".sample")):
                errmsg += (" Copy beem_config.toml.sample to beem_config.toml "
                           "and edit.")
            _error(errmsg)

    def _init_logging(self):
        if not self.get("logging_config"):
            _error("logging_config table undefined.")

        log_conf = self.logging_config
        self._require_fields("logging_config", ["format"])

        if log_conf.get("filename"):
            if not log_conf.get("max_bytes"):
                _error("in logging_config table, filename enabled but "
                       "max_bytes undefined.")

            if not log_conf.get("backup_count"):
                _error("in logging_config table, filename enabled but "
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

    def _check_dcss(self):
        if not self.get("dcss"):
            self._error("The dcss table is undefined.")

        self._require_fields("dcss",
                             ["hostname", "port", "nick", "sequell_nick",
                              "sequell_patterns", "gretell_nick",
                              "gretell_patterns", "cheibriados_nick",
                              "cheibriados_patterns"])

    def _check_webtiles(self):
        if not self.get("webtiles") or not self.webtiles.get("enabled"):
            return

        webtiles = self.webtiles
        self._require_fields("webtiles", ["server_url", "username", "password"])


        if self.get("single_user"):
            if not webtiles.get("listen_user"):
                _error("single_user enabled but listen_user not defined in "
                       "webtiles table")
            self.webtiles["max_listened_subscribers"] = 1
            self.webtiles["max_game_idle"] = float("inf")
            self.webtiles["game_relisten_timeout"] = float("inf")
            self.webtiles["autolisten_enabled"] = False
            return

        self._require_fields("webtiles", ["max_listened_subscribers",
                                          "max_game_idle",
                                          "game_relisten_timeout"])

        if webtiles.get("autolisten_enabled"):
            self._require_fields("webtiles", ["min_autolisten_spectators"])

        if webtiles.get("twitch_reminder_text"):
            self._require_fields("webtiles", ["twitch_reminder_period"])

    def _check_twitch(self):
        if not self.get("twitch") or not self.twitch.get("enabled"):
            return

        self._require_fields("twitch", ["hostname", "port", "nick", "password",
                                        "message_limit",
                                        "moderator_message_limit",
                                        "message_timeout", "max_chat_idle",
                                        "request_expire_time"])

        if self.get("single_user"):
            if not self.twitch.get("listen_user"):
                _error("single_user enabled but listen_user not defined in "
                       "twitch table")
            self.twitch["max_listened_subscribers"] = 1
            self.twitch["max_chat_idle"] = float("inf")
            self.twitch["request_expire_time"] = float("inf")
            return

        self._require_fields("twitch", ["max_listened_subscribers",
                                        "max_chat_idle", "request_expire_time"])

    def load(self):
        """Read the main TOML configuration data from self.path and check
        that the configuration is valid.

        """

        self._check_path()

        try:
            config_fh = open(self.path, "r")
        except EnvironmentError as e:
            _error("Couldn't open file: ({})".format(e.strerror))
        else:
            try:
                self._data = pytoml.load(config_fh)
            except pytoml.TomlError as e:
                _error("Couldn't parse TOML file {} at line {}, col {}: "
                       "{}".format(e.filename, e.line, e.col, e.message))
            finally:
                config_fh.close()

        self._init_logging()
        self._check_dcss()
        self._check_webtiles()
        self._check_twitch()

        have_service = False
        for service in services:
            if self.service_enabled(service):
                have_service = True
                break
        if not have_service:
            _error("At least one service must be enabled")

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


def _error(msg):
    raise Exception("Config file {}: {}".format(conf.path, msg))

def _ensure_user_db_exists():
    if os.path.exists(conf.user_db):
        return

    _log.warn("User DB didn't exist; creating it now")
    c = None
    conn = None

    try:
        conn = sqlite3.connect(conf.user_db)
        c = conn.cursor()

        for service in services:
            user_fields = services[service]["user_fields"]
            field_defaults = services[service]["user_field_defaults"]
            statements = ["id integer primary key",
                          "username text collate nocase"]
            for i, f in enumerate(user_fields):
                statement = f
                if type(field_defaults[i]) is str:
                    statement += " text collate nocase"
                elif type(field_defaults[i]) is int:
                    statement += " integer"
                else:
                    raise Exception("unknown type {} for field {}".format(
                        user_fields[i], type(field_defaults[i])))
                statements.append(statement)

            schema = "CREATE TABLE {}_users ({});".format(service,
                ", ".join(statements))
            c.execute(schema)
            conn.commit()

    except sqlite3.Error as e:
        raise Exception("sqlite3 table creation: {}".format(e.args[0]))

    finally:
        if c:
            c.close()
        if conn:
            conn.close()

def load_user_db():
    """Load the user database from the sqlite3 DB, creating one if
    necessary. The sqlite3 data are loaded into an in-memory copy that
    can be retrieved through `get_user_data()`.

    """

    conn = None
    c = None

    _ensure_user_db_exists()

    try:
        conn = sqlite3.connect(conf.user_db)
        c = conn.cursor()

        for service in services:
            _user_data[service] = {}
            user_fields = services[service]["user_fields"]
            fields_statement = "username, "
            fields_statement += ", ".join(user_fields)
            _user_data[service] = {}
            query = ("SELECT {} FROM {}_users".format(fields_statement,
                                                      service))
            for row in c.execute(query):
                _user_data[service][row[0].lower()] = {
                    f : row[i + 1] for i, f in enumerate(user_fields)}

    except sqlite3.Error as e:
        raise Exception("sqlite3 select: {}".format(e.args[0]))

    finally:
        if c:
            c.close()
        if conn:
            conn.close()
    msgs = []
    for service in services:
        msgs.append("{} {} users".format(len(_user_data[service]),
                                         services[service]["name"]))
    _log.info("Loaded data for {} users".format(", ".join(msgs)))

def register_user(service, username):
    """Register the user for the given service in the user DB and make an
    entry in the in-memory copy of the DB.

    """

    conn = None
    c = None

    user_entry = {}
    vals = []
    user_fields = services[service]["user_fields"]
    default_values = services[service]["user_field_defaults"]
    for i, f in enumerate(user_fields):
        if type(user_fields[i]) is str:
            vals.append("'{}'".format(default_values[i]))
        else:
            vals.append(str(default_values[i]))
        user_entry[f] = default_values[i]

    fields_statement = ", ".join(user_fields)
    values_statement = ", ".join(vals)

    try:
        conn = sqlite3.connect(conf.user_db)
        c = conn.cursor()
        statement = ("SELECT id FROM {}_users "
                     "WHERE username=? collate nocase".format(service))
        c.execute(statement, (username,))
        if c.fetchone():
            raise Exception("user already registered")

        statement = ("INSERT INTO {}_users (username, {}) "
                     "VALUES (?, {})".format(service, fields_statement,
                                             values_statement))
        c.execute(statement, (username,))
        conn.commit()

    except sqlite3.Error as e:
        raise Exception("sqlite3: {}".format(e.args[0]))

    finally:
        if c:
            c.close()
        if conn:
            conn.close()

    _user_data[service][username.lower()] = user_entry
    return user_entry

def set_user_field(service, username, field, value):
    """Set a field for the user of the given service in the userDB and
    update the in-memory copy of the DB.

    """

    entry = get_user_data(service, username)
    if not entry:
        raise Exception("user not found")

    conn = None
    c = None
    try:
        conn = sqlite3.connect(conf.user_db)
        c = conn.cursor()

        statement= ("UPDATE {}_users "
                    "SET    {} = ? "
                    "WHERE  username = ?".format(service, field))
        c.execute(statement, (str(value), username))
        conn.commit()

    except sqlite3.Error as e:
        raise Exception("sqlite3: {}".format(e.args[0]))

    finally:
        if c:
            c.close()
        if conn:
            conn.close()

    entry[field] = value

def get_user_data(service, username):
    """Get the user's data for the given service from the in-memory copy
    of the user DB. This handles the case-insensitivity of the
    username lookup.

    """

    return _user_data[service].get(username.lower())

# Service data is available from this module, but the entries are set
# by the appropriate module.
services = {}
_user_data = {}

# This beem configuration instance available to other modules.
conf = beem_config()

_log = logging.getLogger()
