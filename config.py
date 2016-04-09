import asyncio
import sqlite3
import logging
import os
import os.path
import pytoml

_DEFAULT_CONFIG_PATH = "./beem_config.toml"

class config_error(Exception):
    def __init__(self, config_file=None, msg=None):
        self.msg = msg
        if config_file and msg:
            self.msg = "Config file {}: {}".format(config_file, msg)
        super().__init__(self, msg)

class beem_config(object):
    """Object representing beem configuration.

    One instance of this class should exist in the application.
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
        return self._data.get(*args)

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

    def read(self):
        """
        Reads the main TOML configuration data from self.path
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

        if not self.get("logging_config"):
            _error("logging_config table undefined.")

        log_conf = self.logging_config
        if log_conf.get("filename"):
            if not log_conf.get("max_bytes"):
                _error("In logging_config table, filename enabled but "
                       "max_bytes undefined.")

            if not log_conf.get("backup_count"):
                _error("In logging_config table, filename enabled but "
                       "backup_count undefined.")

        if not log_conf.get("format"):
            _error("In logging_config table, format undefined.")

        log_conf = self.logging_config
        if log_conf.get("filename"):
            handler = RotatingFileHandler(log_conf["filename"],
                                          maxBytes=log_conf["max_bytes"],
                                          backupCount=log_conf["backup_count"])
        else:
            handler = logging.StreamHandler(None)
            handler.setFormatter(
                logging.Formatter(log_conf["format"], log_conf.get("datefmt")))

        _log.addHandler(handler)
        if log_conf.get("level") is not None:
            _log.setLevel(log_conf["level"])

        if not self.get("dcss"):
            self._error("The dcss table is undefined.")

        required = ["hostname", "port", "nick", "sequell_nick",
                    "sequell_patterns", "gretell_nick", "gretell_patterns",
                    "cheibriados_nick", "cheibriados_patterns"]

        for field in required:
            if not self.dcss.get(field):
                _error("In dcss table, {} undefined.".format(field))

        if self.get("webtiles") and self.webtiles.get("enabled"):
            webtiles = self.webtiles
            required = ["server", "username", "password",
                        "max_listened_subscribers"]

            for field in required:
                if not webtiles.get(field):
                    _error("In webtiles table, {} undefined".format(field))

            if webtiles["max_listened_subscribers"] < 1:
                _error("In webtiles table, max_listened_subscribers must be at "
                       "least 1")

            urls = {}
            if not self.webtiles.get("servers"):
                _error("webtiles.servers table undefined.")

            for server in self.webtiles["servers"]:

                if not server.get("name"):
                    _error("Server {} missing name field.".format(
                        server["name"]))

                if not server.get("websocket_url"):
                    _error("Server {} missing websocket_url field.".format(
                        server["id"]))

                if server["name"] in urls:
                    _error("Duplicate server definition for '{}'.".format(
                        server["name"]))

                urls[server["name"]] = server["websocket_url"]

            self.webtiles["server_urls"] = urls

            if webtiles["server"] not in self.webtiles["server_urls"]:
                _error("In webtiles table, unknown server given: "
                       "{}".format(webtiles["server"]))

        if self.get("twitch") and self.twitch.get("enabled"):
            required = ["hostname", "port", "nick", "password",
                        "message_limit"]

            for field in required:
                if not self.twitch.get(field):
                    _error("In twitch table, {} undefined.".format(field))

    def service_enabled(self, service):
        assert service in services

        return self.get(service) and self.get(service).get("enabled")

    def dcss_enabled(self):
        """This returns False only in debug-mode to help test without connecting

        """
        return not self.dcss.get("fake_connect")

    def user_is_admin(self, service, user):
        admins = conf.get(service).get("admins")
        if not admins:
            return False

        for u in admins:
            if u.lower() == user.lower():
                return True
        return False

def get_webtiles_username(twitch_user):
    for user, data in _user_data["webtiles"].items():
        if data["twitch_user"] == twitch_user:
            return user

    return None

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
    return _user_data[service].get(username.lower())

def _error(msg):
    raise config_error(conf.path, msg)

services = {}
_user_data = {}
conf = beem_config()
_log = logging.getLogger()
