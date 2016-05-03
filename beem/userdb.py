"""Access and update the sqlite3 user database."""

import logging
import os.path
import sqlite3

from .config import beem_conf, services

_log = logging.getLogger()

def ensure_user_db_exists():
    if os.path.exists(beem_conf.user_db):
        return

    _log.warn("User DB didn't exist; creating it now")
    c = None
    conn = None

    try:
        conn = sqlite3.connect(beem_conf.user_db)
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

    ensure_user_db_exists()

    try:
        conn = sqlite3.connect(beem_conf.user_db)
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
        conn = sqlite3.connect(beem_conf.user_db)
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
        conn = sqlite3.connect(beem_conf.user_db)
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

_user_data = {}
