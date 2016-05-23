"""Access and update the sqlite3 user database."""

import logging
import os.path
import sqlite3

_log = logging.getLogger()

class UserDB():
    """This class allows access and updates to a user table in an sqlite3
    DB. It loads the data into an in-memory copy that it keeps up to
    date as changes are made.

    """
    
    def __init__(self, db_file, table_name, user_fields):
        self.db_file = db_file
        self.table_name = table_name
        self.user_fields = user_fields
        self.user_data = {}

    def ensure_db_exists(self):
        if os.path.exists(self.db_file):
            return

        _log.warn("User DB didn't exist; creating it now")
        c = None
        conn = None

        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()

            statements = ["id integer primary key",
                          "username text collate nocase"]
            for field in self.user_fields:
                name, default = field
                statement = name
                if type(default) is str:
                    statement += " text collate nocase"
                elif type(default) is int:
                    statement += " integer"
                else:
                    raise Exception("unknown type {} for field {}".format(
                        name, type(default)))
                statements.append(statement)

            schema = "CREATE TABLE {} ({});".format(self.table_name,
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

    def load_db(self):
        """Load the user database from the sqlite3 DB, creating one if
        necessary. The sqlite3 data are loaded into an in-memory copy that
        can be retrieved through `get_user_data()`.

        """

        self.ensure_db_exists()
        
        conn = None
        c = None

        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()

            fields_statement = "username, "
            fields_statement += ", ".join([f for f, d in self.user_fields])
            query = ("SELECT {} FROM {}".format(fields_statement,
                                                self.table_name))
            for row in c.execute(query):
                self.user_data[row[0].lower()] = {
                    f[0] : row[i + 1] for i, f in enumerate(self.user_fields)}

        except sqlite3.Error as e:
            raise Exception("sqlite3 select: {}".format(e.args[0]))

        finally:
            if c:
                c.close()
            if conn:
                conn.close()

    def register_user(self, username):
        """Register the user for the given user DB table and make an
        entry in the in-memory copy of the DB.

        """

        conn = None
        c = None

        user_entry = {}
        vals = []
        for field in self.user_fields:
            name, default = field
            if type(default) is str:
                vals.append("'{}'".format(default))
            else:
                vals.append(str(default))
            user_entry[name] = default

        fields_statement = ", ".join([f for f, d in self.user_fields])
        values_statement = ", ".join(vals)

        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()
            statement = ("SELECT id FROM {} "
                         "WHERE username=? collate nocase".format(
                             self.table_name))
            c.execute(statement, (username,))
            if c.fetchone():
                raise Exception("user already registered")

            statement = ("INSERT INTO {} (username, {}) "
                         "VALUES (?, {})".format(self.table_name,
                                                 fields_statement,
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

        self.user_data[username.lower()] = user_entry
        return user_entry

    def set_user_field(self, username, field, value):
        """Set a field for the user of the given service in the userDB and
        update the in-memory copy of the DB.

        """

        entry = self.get_user_data(username)
        if not entry:
            raise Exception("user not found")

        conn = None
        c = None
        try:
            conn = sqlite3.connect(self.db_file)
            c = conn.cursor()

            statement= ("UPDATE {} "
                        "SET    {} = ? "
                        "WHERE  username = ?".format(self.table_name, field))
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

    def get_user_data(self, username):
        """Get the user's data for the given service from the in-memory copy
        of the user DB. This handles the case-insensitivity of the
        username lookup.

        """

        return self.user_data.get(username.lower())
