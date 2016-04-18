"""Define the `webtiles.manager` WebTiles manager instance and WebTiles service
data."""

import asyncio
import json
import logging
import os
import re
import signal
import sqlite3
import time
import websockets
from websockets.exceptions import ConnectionClosed
import zlib

import chat
import config
import dcss
import twitch

_conf = config.conf
_log = logging.getLogger()

# How many seconds after connecting and sending login data do we wait
# for login confirmation.
_LOGIN_TIMEOUT = 30
# How many seconds to wait after a game ends before attempting to watch again.
_RELISTEN_WAIT = 5

class webtiles_connection():
    """A websocket connection to a WebTiles server. Used as a base class for
    `lobby_connection` and `game_connection`.

    """

    def __init__(self):
        super().__init__()
        self._decomp = zlib.decompressobj(-zlib.MAX_WBITS)
        self._websocket = None
        self._logged_in = False
        self._task = None
        self._time_connected = None

    def get_task(self):
        if not self._task or self._task.done():
            self._task = asyncio.ensure_future(self._process())
        return self._task

    @asyncio.coroutine
    def _connect(self):
        assert(not self.connected())

        wtconf = _conf.webtiles
        try:
            self._websocket = yield from websockets.connect(
                wtconf["server_url"])
        except OSError as e:
            _log.error("WebTiles: Unable to connect to %s: %s",
                       _conf.webtiles["server_url"], e.strerror)
            yield from self.stop()
            raise

        self._time_connected = time.time()
        try:
            yield from self._send({"msg"      : "login",
                                   "username" : wtconf["username"],

    "password" : wtconf["password"]})
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to send login: %s", err_reason)
            yield from self.stop()
            raise

    def connected(self):
        """Return true if the websocket is connected."""

        return self._websocket and self._websocket.open

    def _login_timeout(self):
        return (self._time_connected
                and not self._logged_in
                and time.time() - self._time_connected >= _LOGIN_TIMEOUT)

    @asyncio.coroutine
    def stop(self):
        """Close the websocket if it's open and reset the connection state"""

        if self.connected():
            yield from self._websocket.close()
        self._websocket = None
        self._time_connected = None
        self._logged_in = False

    @asyncio.coroutine
    def _read(self):
        """Try to read a WebSocket message. Raises Exception if a read error
        occurs. This function returns None if we can't parse the JSON, since
        some older game versions send bad messages we need to ignore.

        """

        try:
            comp_data = yield from self._websocket.recv()
        except ConnectionClosed as e:
            raise Exception("connection closed: {}".format(e.args[1]))

        comp_data += bytes([0, 0, 255, 255])
        json_message = self._decomp.decompress(comp_data)
        json_message = json_message.decode("utf-8")

        try:
            message = json.loads(json_message)
        except ValueError as e:
            # Invalid JSON happens with data sent from older games (0.11 and
            # below), so don't spam the log with these. XXX can we ignore only
            # those messages and log other parsing errors?
            _log.debug("WebTiles: Ignoring unparseable JSON (error: %s): %s.",
                       e.args[0], json_message)
            return

        if "msgs" in message:
            messages = message["msgs"]
        elif "msg" in message:
            messages = [message]
        else:
            raise Exception("JSON doesn't define either 'msg' or 'msgs'")

        return messages

    @asyncio.coroutine
    def _send(self, message):
        try:
            yield from self._websocket.send(json.dumps(message))
        except ConnectionClosed as e:
            raise Exception("connection closed: {}".format(e.args[1]))

        _log.debug("WebTiles: Sent message: %s", message["msg"])

    @asyncio.coroutine
    def _handle_message(self, message):
        _log.debug("WebTiles: Received message {}.".format(message["msg"]))
        if message["msg"] == "ping":
            yield from self._send({"msg" : "pong"})
            return True

        if message["msg"] == "login_success":
            self._logged_in = True
            return True

        if message["msg"] == "login_fail":
            ## Very bad, shut down the server.
            _log.critical("WebTiles: Login to %s failed; shutting down server.",
                          _conf.webtiles["server_url"])
            os.kill(os.getpid(), signal.SIGTERM)
            return True

        return False


class lobby_connection(webtiles_connection):
    """Lobby websocket connection that watches for lobby data from the WebTiles
    server, updating its entries. This data is used by `webtiles_manager` to
    decide when to listen and stop listening to games.

    """

    def __init__(self):
        super().__init__()
        self.entries = []
        self.complete = False

    @asyncio.coroutine
    def _connect(self):
        self.entries = []
        self.complete = False
        yield from super()._connect()

    @asyncio.coroutine
    def stop(self):
        """Shut down the lobby connection"""

        self.entries = []
        self.complete = False
        yield from super().stop()

    @asyncio.coroutine
    def _process(self):

        if not self.connected():
            try:
                yield from self._connect()
            except:
                yield from asyncio.sleep(1)
                return

        elif self._login_timeout():
            ## Try to reconnect if we've not gotten login
            yield from self._websocket.close()
            return

        messages = None
        try:
            messages = yield from self._read()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to read lobby WebSocket: %s",
                       err_reason)
            yield from self.stop()

        if not messages:
            return

        for message in messages:
            yield from self._handle_message(message)

    def get_entry(self, username, game_id):
        """Get the lobby entry of a game"""

        for entry in self.entries:
            if entry["username"] == username and entry["game_id"] == game_id:
                return entry
        return

    @asyncio.coroutine
    def _handle_message(self, message):
        handled = yield from super()._handle_message(message)
        if handled:
            return True

        if message["msg"] == "lobby_entry":
            entry = self.get_entry(message["username"], message["game_id"])
            message["time_update"] = time.time()
            if entry:
                entry.update(message)
            else:
                self.entries.append(message)
            return True

        if message["msg"] == "lobby_remove":
            for entry in self.entries:
                if entry["id"] == message["id"]:
                    self.entries.remove(entry)
                    break
            return True

        if message["msg"] == "lobby_clear":
            self.entries = []
            self.complete = False
            return True

        if message["msg"] == "lobby_complete":
            self.complete = True
            return True

        return False

class game_connection(webtiles_connection, chat.chat_listener):
    """A game websocket connection that listens to chat and responds to commands.

    """

    def __init__(self):
        super().__init__()
        self.service = "webtiles"
        self.bot_name = _conf.webtiles["username"]
        self.irc_channel = "WebTiles"
        self.username = None
        self.game_id = None
        # Last time we either send the listen command or had watched a game,
        # used so we can reuse connections, but end them after being idle for
        # too long.
        self._last_listen_time = None
        self._need_greeting = False
        self._last_reminder_time = None
        self.finished = False

    def get_source_key(self):
        """Get a unique identifier tuple of the game for this connection.
        Identifies this game connection as a source for chat listening. This is
        used to map DCSS queries to their results as they're received.

        """

        return (self.service, self.username, self.game_id)

    def get_task(self):
        ## If we're finished, no further tasks made after the current one
        ## completes.
        if (not self._task or self._task.done()) and self.finished:
            if self._task:
                self._task = None
            return

        return super().get_task()

    @asyncio.coroutine
    def stop(self):
        """Shut down the game connection"""

        self.listening = False
        self.username = None
        self.game_id = None
        yield from super().stop()
        self.finished = True

    @asyncio.coroutine
    def _connect(self):
        self.listening = False
        self._last_listen_time = None
        yield from super()._connect()

    @asyncio.coroutine
    def _process(self):
        if not self.connected():
            try:
                yield from self._connect()
            except:
                yield from asyncio.sleep(1)
                return

        elif self._login_timeout():
            ## Try to reconnect if we've not gotten login
            yield from self._websocket.close()
            return

        if self._logged_in and not self.listening:
            assert(self._last_listen_time)

            max_idle = _conf.webtiles["max_connection_idle"]
            if time.time() - self._last_listen_time >= max_idle:
                _log.debug("WebTiles: Shutting down idle game connection")
                yield from self.stop()
                return

        yield from self._handle_greeting()
        yield from self._handle_reminder()

        messages = None
        try:
            messages = yield from self._read()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to read game WebSocket (listen user: "
                       "%s): %s", self.username, err_reason)
            yield from self.stop()

        if not messages:
            return

        for message in messages:
            yield from self._handle_message(message)

    @asyncio.coroutine
    def _handle_greeting(self):
        if not self.username or not self._need_greeting:
            return

        greeting = _conf.webtiles["greeting_text"].replace("\n", " ")
        greeting = greeting.replace("%n", self.bot_name)
        yield from self.send_chat(greeting)
        self._need_greeting = False

    @asyncio.coroutine
    def _handle_reminder(self):
        if (not _conf.service_enabled("twitch")
            or not _conf.webtiles.get("twitch_reminder_text")
            or not twitch.manager.is_connected()
            or not self.spectators
            or self.spectators == [self.username]):
            return

        user_data = config.get_user_data("webtiles", self.username)
        reminder_period = _conf.webtiles["twitch_reminder_period"]
        if (not user_data
            or not user_data["twitch_username"]
            or not user_data["twitch_reminder"]):
            return

        chan = twitch.manager.get_channel(user_data["twitch_username"])
        if not chan:
            return

        if (self._last_reminder_time
            and time.time() - self._last_reminder_time < reminder_period):
            return

        if self.username[-1] == "s" or self.username[-1] == "S":
            user_possessive = self.username + "'"
        else:
            user_possessive = self.username + "'s"

        msg = _conf.webtiles["twitch_reminder_text"].replace("\n", " ")
        msg = msg.replace("%us", user_possessive)
        msg = msg.replace("%u", self.username)
        msg = msg.replace("%t", user_data["twitch_username"])
        yield from self.send_chat(msg)
        self._last_reminder_time = time.time()

    @asyncio.coroutine
    def send_chat(self, message, is_action=False):
        """Send a WebTiles chat message. We currently shut down the game
        connection if an error occurs and log the event, but don't raise to the
        caller, since we don't care to take any action.

        """

        if is_action:
            message = "*{}* {}".format(_conf.webtiles["username"], message)
        # In case any other beem bot happens to listen in the same
        # channel, don't cause a feedback loop by relaying Sequell output.
        elif chat.is_bot_command(message):
            message = "]" + message

        try:
            yield from self._send({"msg" : "chat_msg", "text" : message})
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to send chat message (listen user: "
                       "%s, error: %s): %s", self.username, err_reason, message)
            yield from self.stop()
            return

    @asyncio.coroutine
    def listen_game(self, username, game_id):
        """Listen to the given game. After calling this method, the connection
        won't be in a 'listening' state until it receives a watch
        acknowledgement from the WebTiles server.

        """

        self.username = username
        self.game_id = game_id
        user_data = config.get_user_data("webtiles", username)
        if user_data and user_data["subscription"] > 0:
            self._need_greeting = False
        else:
            self._need_greeting = True

        if not self.connected():
            yield from self._connect()

        try:
            yield from self._send({"msg"      : "watch",
                                   "username" : self.username})
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to send watch message for user %s: "
                       "%s", self.username, err_reason)
            yield from self.stop()
            raise

        self.listening = False
        self._last_listen_time = time.time()

    @asyncio.coroutine
    def stop_listening(self, send_stop=False):
        super().stop_listening()

        if self.listening and send_stop:
            try:
                yield from self._send({"msg" : "go_lobby"})
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("WebTiles: Unable to send go_lobby command for user "
                           "%s: %s", self.username, err_reason)
                yield from self.stop()

        if self.listening:
            self._last_listen_time = time.time()
            manager.set_listen_end(self)
        self.listening = False
        self.username = None
        self.game_id = None

    @asyncio.coroutine
    def _handle_message(self, message):
        handled = yield from super()._handle_message(message)
        if handled:
            return True

        if message["msg"] == "watching_started":
            self.listening = True
            _log.info("WebTiles: Listening to user %s", self.username)
            return True

        if message["msg"] == "update_spectators":
            # Strip of html tags from names
            names = re.sub(r'</?(a|span)[^>]*>', "", message["names"])
            # Ignore the Anons.
            names = re.sub(r'( and )?\d+ Anon', "", names, 1)
            self.spectators = set()
            # Exclude ourself from this list.
            for n in names.split(", "):
                if n != self.bot_name:
                    self.spectators.add(n)
            return True

        # Messages here truly shouldn't happen until we've
        # gotten watching_started (and self.listening is hence True)
        if not self.listening:
            return False

        if message["msg"] == "game_ended":
            _log.info("WebTiles: Game ended for user %s", self.username)
            yield from self.stop_listening()
            return True

        if message["msg"] == "go_lobby":
            # The game we were watching stopped for some reason.
            _log.warning("Received go_lobby while listening to user %s.",
                         self.username)
            yield from self.stop_listening()
            return True

        if self._logged_in and message["msg"] == "chat":
            user, command = _parse_chat(message["content"])
            yield from self.read_chat(user, command)
            return True

        if message["msg"] == "dump" and _conf.service_enabled("twitch"):
            user_data = config.get_user_data("webtiles", self.username)
            if not user_data or not user_data["twitch_username"]:
                return True

            chan = twitch.manager.get_channel(user_data["twitch_username"])
            if not chan:
                return True

            dump_msg = "Char dump: {}.txt".format(message["url"])
            yield from chan.send_chat(dump_msg)
            return True

        return False


class webtiles_manager():
    # Can't depend on config.conf, as the data for this isn't loaded yet.
    def __init__(self):
        self._lobby = lobby_connection()
        self._autolisten_candidate = None
        self._autolisten = None
        self._listen_queue = []
        self._subscriber_conns = set()
        self._queue_task = None

    def _get_connection(self, username, game_id):
        """Get any existing connection for the given game."""

        if (self._autolisten
            and self._autolisten.username
            and self._autolisten.username == username
            and self._autolisten.game_id == game_id):
            return self._autolisten

        for conn in self._subscriber_conns:
            if (conn.username
                and conn.username == username
                and conn.game_id == game_id):
                return conn

        return

    def get_source_by_key(self, source_key):
        return self._get_connection(source_key[1], source_key[2])

    @asyncio.coroutine
    def _new_subscriber_conn(self, username, game_id):
        for conn in self._subscriber_conns:
            if not conn.username and not conn.finished:
                try:
                    yield from conn.listen_game(username, game_id)
                except:
                    return

                return conn

        wtconf = _conf.webtiles
        if len(self._subscriber_conns) >= wtconf["max_listened_subscribers"]:
            return

        conn = game_connection()
        try:
            yield from conn.listen_game(username, game_id)
        except:
            return

        self._subscriber_conns.add(conn)
        return conn

    @asyncio.coroutine
    def stop(self):
        if self._lobby:
            yield from self._lobby.stop()

        if self._autolisten:
            yield from self._autolisten.stop()
            self._autolisten = None

        for conn in self._subscriber_conns:
            yield from conn.stop()

        self._subscriber_conns = set()
        self._listen_queue = []

    @asyncio.coroutine
    def start(self):
        _log.info("WebTiles: Starting manager")
        # This loop maintains the lobby task, the queue task, and all game
        # tasks.

        while True:
            tasks = []
            subscriber_tasks = {}

            ## We must always have the lobby and queue update tasks.
            tasks.append(self._lobby.get_task())
            tasks.append(self._get_queue_task())

            if self._autolisten:
                task = self._autolisten.get_task()
                if task:
                    tasks.append(task)

            for conn in self._subscriber_conns:
                task = conn.get_task()
                if task:
                    tasks.append(task)

            yield from asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    def _add_queue(self, username, game_id, pos=None):
        entry = {"username" : username,
                 "game_id"  : game_id,
                 "time_end" : None}
        if pos is None:
            pos = len(self._listen_queue)
        self._listen_queue.insert(pos, entry)
        pass

    def _get_queue_entry(self, username, game_id):
        for entry in self._listen_queue:
            ### XXX For now we ignore game_id, since webtiles can't make unique
            ### watch URLs by game for the same user.
            if entry["username"] == username:
                return entry
        return

    def _get_queue_task(self):
        if not self._queue_task or self._queue_task.done():
            self._queue_task = asyncio.ensure_future(self._process())
        return self._queue_task

    @asyncio.coroutine
    def _autolisten_game(self, game):
        username, game_id = game
        if (self._autolisten
            and self._autolisten.username == username
            and self._autolisten.game_id == game_id):
            return

        _log.info("WebTiles: Found new autolisten user %s", username)
        if self._autolisten:
            _log.info("WebTiles: Stopping autolisten for user %s: new "
                      "autolisten game found", self._autolisten.username)
        else:
            self._autolisten = game_connection()
        try:
            yield from self._autolisten.listen_game(username, game_id)
        except:
            self._autolisten = None

    @asyncio.coroutine
    def _check_current_autolisten(self):
        """When we don't find a new autolisten candidate, check that we're still
        able to watch our present autolisten game.

        """

        if not self._autolisten:
            return

        wtconf = _conf.webtiles
        lobby_entry = None
        for entry in self._lobby.entries:
            if (entry["username"] == self._autolisten.username
                and entry["game_id"] == self._autolisten.game_id):
                lobby_entry = entry
                break

        # Game no longer has a lobby entry, but let the connection itself
        # handle any stop watching event from the server.
        if not lobby_entry:
            return

        # See if this game is no longer eligable for autolisten. We don't
        # require a min. spectator count after the initial autolisten, since
        # doing so just leads to a lot of flucutation in autolistening.
        idle_time = (lobby_entry["idle_time"] +
                     time.time() - lobby_entry["time_update"])
        game_allowed = _game_allowed(self._autolisten.username,
                                     self._autolisten.game_id)
        end_reason = None
        if not game_allowed:
            end_reason = "Game disallowed"
        elif not dcss.manager.logged_in:
            end_reason = "DCSS not ready"
        elif idle_time >= wtconf["max_game_idle"]:
            end_reason = "Game idle"
        else:
            return

        _log.info("WebTiles: Stopping autolisten for user %s: %s",
                  self._autolisten.username, end_reason)
        yield from self._autolisten.stop_listening(True)

    def _process_lobby(self):
        """Process lobby entries, adding games to the watch queue and return an
        autowatch candidate if one is found.

        """

        wtconf = _conf.webtiles
        min_spectators = wtconf["min_autolisten_spectators"]
        max_subscribers = wtconf["max_listened_subscribers"]
        autolisten_spectators = -1
        current_time = time.time()
        autolisten_game = None
        for entry in self._lobby.entries:
            idle_time = (entry["idle_time"] +
                         current_time - entry["time_update"])
            if (not _game_allowed(entry["username"], entry["game_id"])
                or idle_time >= wtconf["max_game_idle"]):
                continue

            conn = self._get_connection(entry["username"], entry["game_id"])
            # Find an autolisten candidate
            if (wtconf.get("autolisten_enabled")
                and dcss.manager.logged_in
                # Only subscribers who don't have subscriber slots are valid
                # autolisten candidates.
                and (not _user_is_subscribed(entry["username"])
                     or not conn in self._subscriber_conns
                     and len(self._subscriber_conns) >= max_subscribers)
                and entry["spectator_count"] >= min_spectators
                # If there's a tie, favor a game we're already autolistening
                # instead of letting the order of iteration decide.
                and (conn
                     and conn is self._autolisten
                     and entry["spectator_count"] == autolisten_spectators
                     or entry["spectator_count"] > autolisten_spectators)):
                autolisten_spectators = entry["spectator_count"]
                autolisten_game = (entry["username"], entry["game_id"])

            if (_user_is_subscribed(entry["username"])
                and not self._get_queue_entry(entry["username"],
                                              entry["game_id"])):
                self._add_queue(entry["username"], entry["game_id"])

        return autolisten_game

    @asyncio.coroutine
    def _process_queue(self):
        """Update the subscriber listen queue, listening any games that we can.

        """

        wtconf = _conf.webtiles
        max_subscribers = wtconf["max_listened_subscribers"]
        relisten_timeout = wtconf["game_relisten_timeout"]
        for entry in list(self._listen_queue):
            lobby = self._lobby.get_entry(entry["username"], entry["game_id"])
            idle_time = 0
            if lobby:
                idle_time = (lobby["idle_time"] +
                             time.time() - lobby["time_update"])
            conn = self._get_connection(entry["username"], entry["game_id"])
            idle = idle_time >= wtconf["max_game_idle"]
            allowed = _game_allowed(entry["username"], entry["game_id"])
            wait = (entry["time_end"]
                    and time.time() - entry["time_end"] < _RELISTEN_WAIT)
            expired = (not entry["time_end"]
                       or time.time() - entry["time_end"] >= relisten_timeout)
            if conn:
                end_reason = None
                if not allowed:
                    end_reason = "Game disallowed"
                if not dcss.manager.logged_in:
                    end_reason = "DCSS not ready"
                elif idle:
                    end_reason = "Game idle"
                if end_reason:
                    _log.info("WebTiles: Stopping listen for user %s: %s",
                              entry["username"], end_reason)
                    yield from conn.stop_listening(True)
                # An autolistened subscriber without a subscriber slot now has
                # one.
                elif (conn is self._autolisten
                      and len(self._subscriber_conns) < max_subscribers):
                    self._subscriber_conns.add(conn)
                    self._autolisten = None
                    continue

            # The game is no longer eligable or been offline for sufficiently
            # long.
            if not allowed or idle or not lobby and expired:
                self._listen_queue.remove(entry)
                continue

            # We can't listen yet or they already have a subscriber slot.
            if not dcss.manager.logged_in or not lobby or wait or conn:
                continue

            # Try to give the game a subscriber slot. If this fails, the entry
            # will remain in the queue for subsequent attempts.
            yield from self._new_subscriber_conn(entry["username"],
                                                 entry["game_id"])

    @asyncio.coroutine
    def _process(self):
        """Based on lobby information, do autolisten and listen to subscribers"""

        assert(self._lobby)

        autolisten_game = None
        if self._lobby.complete:
            autolisten_game = self._process_lobby()
        if autolisten_game:
            yield from self._autolisten_game(autolisten_game)
        else:
            yield from self._check_current_autolisten()

        yield from self._process_queue()

        if (self._autolisten
            and self._autolisten.finished
            and self._autolisten.get_task() is None):
            self._autolisten = None

        for conn in list(self._subscriber_conns):
            if conn.finished and conn.get_task() is None:
                self._subscriber_conns.remove(conn)

        # The manager updates once per second.
        yield from asyncio.sleep(1)

    def set_listen_end(self, conn):
        queue = self._get_queue_entry(conn.username, conn.game_id)
        if not queue:
            return

        queue["time_end"] = time.time()


def _parse_chat(message):
    # Remove html formatting
    msg_pattern = r'<span[^>]+>([^<]+)</span>: <span[^>]+>([^<]+)</span>'
    match = re.match(msg_pattern, message)
    if not match:
        _log.error("WebTiles: Unable to parse chat message: %s", message)
        return

    user = match.group(1)
    command = match.group(2)
    # Unescape these HTML entities
    command = command.replace("&amp;", "&")
    command = command.replace("&AMP;", "&")
    command = command.replace("&percnt;", "%")
    command = command.replace("&gt;", ">")
    command = command.replace("&lt;", "<")
    command = command.replace("&quot;", '"')
    command = command.replace("&apos;", "'")
    command = command.replace("&#39;", "'")
    command = command.replace("&nbsp;", " ")
    return (user, command)

def _can_listen_user(user):
    if _conf.get("single_user"):
        return user == _conf.webtiles["listen_user"]

    if _conf.webtiles.get("never_listen"):
        for u in _conf.webtiles["never_listen"]:
            if u.lower() == user.lower():
                return False

    user_data = config.get_user_data("webtiles", user)
    if user_data and user_data["subscription"] < 0:
        return False

    return True

def _user_is_subscribed(user):
    # User is subscribed.
    user_data = config.get_user_data("webtiles", user)
    return user_data and user_data["subscription"] > 0

def _game_allowed(username, game_id):
    """Can this game ever be listened to?

    A game is disallowed if the user is not allowed or the game is
    of too old a version.
    """
    if not _can_listen_user(username):
        return False

    # Check for old, untested versions.
    match = re.search(r"([.0-9]+)", game_id)
    if match:
        try:
            version = float(match.group(1))
        except ValueError:
            return True

        if version < 0.10:
            return False

    return True

@asyncio.coroutine
def _subscribe_command(source, target_user):
    """`!<bot-name> subscribe` chat command"""

    user_data = config.get_user_data("webtiles", target_user)
    if not user_data:
        user_data = config.register_user("webtiles", target_user)

    if user_data["subscription"] == 1:
        yield from source.send_chat(
            "User {} is already subscribed".format(target_user))
        return

    config.set_user_field("webtiles", target_user, "subscription", 1)
    yield from source.send_chat(
        "Subscribed. {} will now watch all games of user {}".format(
            source.bot_name, target_user))

@asyncio.coroutine
def _unsubscribe_command(source, target_user):
    """`!<bot-name> unsubscribe` chat command"""

    user_data = config.get_user_data("webtiles", target_user)
    if not user_data:
        user_data = config.register_user("webtiles", target_user)

    if user_data["subscription"] == -1:
        yield from source.send_chat(
            "User {} is already unsubscribed".format(target_user))
        return

    config.set_user_field("webtiles", target_user, "subscription", -1)
    msg = "Unsubscribed. {} will no longer watch games of user {}.".format(
        source.bot_name, target_user)
    # We'll be leaving the chat of this source.
    if source.username == target_user:
        msg += " Bye!"
    yield from source.send_chat(msg)

@asyncio.coroutine
def _twitch_user_command(source, target_user, twitch_user=None):
    """`!<bot-name> twitch-user` chat command"""

    user_data = config.get_user_data("webtiles", target_user)
    if not twitch_user:
        if not user_data:
            msg = "User {} is not registered.".format(target_user)
        elif not user_data["twitch_username"]:
            msg = ("An admin must link the WebTiles user {} to a Twitch "
                   "username".format(target_user))
        else:
            msg = "Twitch username for user {}: {}".format(
                target_user, user_data["twitch_username"])
        yield from source.send_chat(msg)
        return

    if not user_data:
        user_data = config.register_user("webtiles", target_user)

    if not config.get_user_data("twitch", twitch_user):
        config.register_user("twitch", twitch_user)

    config.set_user_field("webtiles", target_user, "twitch_username",
                          twitch_user)
    yield from source.send_chat("User {} linked to Twitch username {}".format(
        target_user, twitch_user))

@asyncio.coroutine
def _twitch_reminder_command(source, target_user, state=None):
    """`!<bot-name> twitch-reminder` chat command"""

    user_data = config.get_user_data("webtiles", target_user)
    if not user_data:
        yield from source.send_chat("User {} is not registered".format(
            target_user))
        return

    elif not user_data["twitch_username"]:
        yield from source.send_chat("An admin must link the WebTiles user {} "
                                    "to a Twitch username".format(target_user))
        return

    if state is None:
        yield from source.send_chat("Twitch reminder for user {} is {}".format(
            target_user, "on" if user_data["twitch_reminder"] else "off"))
        return

    state_val = 1 if state == "on" else 0
    state_desc = "enabled" if state_val else "disabled"
    config.set_user_field("webtiles", target_user, "twitch_reminder", state_val)
    yield from source.send_chat("Twitch reminder {} for user {}".format(
        state_desc, target_user))

# The WebTiles manager instance created when the module is loaded.
manager = webtiles_manager()

# WebTiles service data
config.services["webtiles"] = {
    "name"                : "WebTiles",
    "manager"             : manager,
    "user_fields"         : ["nick", "subscription", "twitch_username",
                             "twitch_reminder"],
    "user_field_defaults" : ["", 0, "", 0],
    "commands" : {
        "subscribe" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : _subscribe_command,
        },
        "unsubscribe" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : _unsubscribe_command,
        },
        "nick" : {
            "arg_pattern" : r"^[a-zA-Z0-9_-]+$",
            "arg_description" : "<nick>",
            "single_user" : True,
            "function" : chat.nick_command
        },
        "twitch-user" : {
            "arg_pattern" : r"^[a-zA-Z0-9][a-zA-Z0-9_]{3,24}$",
            "arg_description" : "<twitch-username>",
            "single_user" : False,
            "function" : _twitch_user_command,
        },
        "twitch-reminder" : {
            "arg_pattern" : r"^(on|off)$",
            "arg_description" : "on|off",
            "single_user" : True,
            "function" : _twitch_reminder_command,
        },
    }
}
