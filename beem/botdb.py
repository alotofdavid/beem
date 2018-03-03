"""Access and update the sqlite3 database."""

import logging
import os.path
import sqlite3

_log = logging.getLogger()

class BotDB():
    """This class allows access and updates to a user table in an sqlite3 DB.
    It loads the data into an in-memory copy that it keeps up to date as
    changes are made."""

    def __init__(self, db_file, db_tables, user_table=None):
        self.db_file = db_file
        self.db_tables = db_tables
        self.db_data = {}
        self.user_table = user_table

    def get_table_keys(self, table):
        keys = []
        for f in self.db_tables[table]:
            if f.get('primary'):
                keys.append(f['name'])

        return keys

    def create_table(self, cursor, name):
        field_terms = []
        for field in self.db_tables[name]:
            term = field['name']

            if field['type'] == "text":
                term += " TEXT COLLATE NOCASE"
            elif field['type'] == "integer":
                term += " INT"
            else:
                raise Exception("unknown type {} for field {}".format(
                    field['name'], field['type']))

            if field.get('primary'):
                term += " PRIMARY KEY"

            field_terms.append(term)

        cursor.execute("CREATE TABLE {} ({});".format(name,
            ", ".join(field_terms)))

    def check_db(self):
        if not os.path.exists(self.db_file):
            _log.info("Sqlite DB didn't exist; creating it now")

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        try:
            for t in self.db_tables:
                query = ("SELECT name FROM sqlite_master WHERE type='table'"
                         " AND name = ?")
                if not cursor.execute(query, [t]).fetchone():
                    _log.info("Creating table {}".format(t))
                    self.create_table(cursor, t)

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.commit()
                conn.close()

    def load_db(self):
        """Load the user database from the sqlite3 DB, creating one if
        necessary. The sqlite3 data are loaded into an in-memory copy that can
        be retrieved through `get_user_data()`."""

        self.check_db()

        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()

        try:

            for t in self.db_tables:
                keys = self.get_table_keys(t)

                fields = self.db_tables[t]
                fields_statement = ", ".join([f['name'] for f in fields])

                query = ("SELECT {} FROM {}".format(fields_statement, t))

                self.db_data[t] = {}
                for row in cursor.execute(query):
                    key_vals = []
                    db_entry = {}
                    for i, f in enumerate(fields):
                        if f['name'] in keys:
                            if f['type'] == "text":
                                key_vals.append(row[i].lower())
                            else:
                                key_vals.append(row[i])

                        db_entry[f['name']] = row[i]

                    self.db_data[t][tuple(key_vals)] = db_entry

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()


    def add_row(self, table, row):
        """Add a row to the given DB table."""

        key_fields = self.get_table_keys(table)
        row_key = []
        for k in key_fields:
            if k not in row:
                raise Exception("row missing key {}".format(k))

            if type(row[k]) is str:
                row_key.append(row[k].lower())
            else:
                row_key.append(row[k])

        row_key = tuple(row_key)
        if row_key in self.db_data[table]:
            raise Exception("row key {} already exists".format(row_key))

        row_entry = {}
        vals = []
        for i, field in enumerate(self.db_tables[table]):
            if field['name'] not in row:
                vals.append(field['default'])
            else:
                vals.append(row[field['name']])

            row_entry[field['name']] = vals[i]

        field_terms = ", ".join([f['name'] for f in self.db_tables[table]])
        param_terms = ", ".join(['?'] * len(vals))

        try:
            conn = sqlite3.connect(self.db_file)

            statement = "INSERT INTO {} ({}) VALUES ({})".format(table,
                field_terms, param_terms)

            cursor = conn.cursor()
            cursor.execute(statement, vals)
            conn.commit()

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        self.db_data[table][row_key] = row_entry
        return row_entry

    def set_row_field(self, table, keys, field, value):
        """Set a field for the given table and unique row, and update the
        in-memory copy of the DB."""

        entry = self.get_row(table, keys)
        if not entry:
            raise Exception("row not found for key {}".format(keys))

        key_fields = self.get_table_keys(table)
        key_terms = " AND ".join(['WHERE {} = ?'.format(k)
            for k in key_fields])

        params = [str(value)] + keys

        try:
            conn = sqlite3.connect(self.db_file)

            statement= "UPDATE {} SET {} = ? " "{}".format(table, field,
                    key_terms)

            cursor = conn.cursor()
            cursor.execute(statement, params)
            conn.commit()

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        entry[field] = value

    def get_row(self, table, keys):
        """Get the data for the given row in the the given table using the
        row's keys. Handles any case-insensitivity of the lookup. Returns None
        if row not found."""

        row_key = tuple(k.lower() if type(k) is str else k for k in keys)
        return self.db_data[table].get(row_key)

    def get_user_data(self, user_name):
        return self.get_row(self.user_table, [user_name])

    def register_user(self, user_name):
        keys = self.get_table_keys(self.user_table)
        return self.add_row(self.user_table, {keys[0] : user_name})

    def set_user_field(self, user_name, field, value):
        return self.set_row_field(self.user_table, [user_name], field,
                value)
