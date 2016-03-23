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

import beem
import chat
import config
import dcss
import twitch

_conf = config.conf
_log = logging.getLogger()

class webtiles_connection():
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
        self._time_connected = None
        self._logged_in = False
        websocket_url = wtconf["server_urls"][_conf.webtiles["server"]]

        try:
            self._websocket = yield from websockets.connect(websocket_url)
        except OSError as e:
            _log.error("WebTiles: Unable to connect to %s: %s",
                       _conf.webtiles["server"], e.strerror)
            yield from self.stop()
            raise

        self._time_connected = time.time()
        try:
            yield from self._send({"msg"      : "login",
                                   "username" : wtconf["username"],
                                   "password" : wtconf["password"]})
        except ConnectionClosed:
            _log.error("WebTiles: Websocket connection to closed while sending "
                       "login")
            yield from self.stop()
            raise

    def connected(self):
        return self._websocket and self._websocket.open

    def _login_timeout(self):
        return (self._time_connected
                and not self._logged_in
                and time.time() - self._time_connected >= 30)

    @asyncio.coroutine
    def stop(self):
        if self._websocket:
            try:
                yield from self._websocket.close()
            except:
                pass
        self._time_connected = None
        self._websocket = None
        self._logged_in = False

    @asyncio.coroutine
    def _read(self):

        try:
            comp_data = yield from self._websocket.recv()
        except ConnectionClosed:
            _log.error("WebTiles: Websocket connection closed during read")
            return

        except Exception as e:
            _log.error(
                "WebTiles: Websocket read error from {}: {}".format(e.args[0]))
            return

        comp_data += bytes([0, 0, 255, 255])
        json_message = self._decomp.decompress(comp_data)
        json_message = json_message.decode("utf-8")

        try:
            message = json.loads(json_message)
        except ValueError as e:
            # Invalid JSON happens with data sent from older games (0.11 and
            # below), so don't spam the log with these. XXX can we ignore only
            # those messages and log other parsing errors?
            _log.debug("WebTiles: Cannot parse JSON (%s): %s.", e.args[0],
                       json_message)
            return

        if "msgs" in message:
            messages = message["msgs"]
        elif "msg" in message:
            messages = [message]
        else:
            _log.error("WebTiles: JSON doesn't define either 'msg' or 'msgs'")
            return

        return messages

    @asyncio.coroutine
    def _send(self, message):
        yield from self._websocket.send(json.dumps(message))
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
            _log.error("WebTiles: Login failed; shutting down server.")
            os.kill(os.getpid(), signal.SIGTERM)
            return True

        return False


class lobby_connection(webtiles_connection):
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

        messages = yield from self._read()

        if not messages:
            return

        for message in messages:
            yield from self._handle_message(message)

    def get_entry(self, username, game_id):
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
    def __init__(self, slot_num):
        super().__init__()
        self.service_name = "webtiles"
        self.bot_name = _conf.webtiles["username"]
        self.irc_channel = "WebTiles"
        self.username = None
        self.game_id = None
        self.slot_num = slot_num
        # Last time we either send the listen command or had watched a game,
        # used so we can reuse connections, but end them after being idle for
        # too long.
        self._last_listen_time = None
        self._need_greeting = False
        self._last_reminder_time = None
        self.finished = False

    def get_source_key(self):
        return (self.username, self.game_id)

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

        messages = yield from self._read()
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
            or not self.spectators
            or self.spectators == [self.username]):
            return

        user_data = config.get_user_data("webtiles", self.username)
        reminder_period = _conf.webtiles["twitch_reminder_period"]
        if (not user_data
            or not user_data["twitch_user"]
            or not user_data["twitch_reminder"]):
            return

        chan = twitch.manager.get_channel(user_data["twitch_user"])
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
        msg = msg.replace("%t", user_data["twitch_user"])
        yield from self.send_chat(msg)
        self._last_reminder_time = time.time()

    @asyncio.coroutine
    def send_chat(self, message, is_action=False):
        if is_action:
            message = "*{}* {}".format(_conf.webtiles["username"], message)
        # In case any other beem bot happens to listen in the same
        # channel, don't cause a feedback loop by relaying Sequell output.
        elif chat.is_bot_command(message):
            message = "]" + message

        try:
            yield from self._send({"msg" : "chat_msg", "text" : message})
        except ConnectionClosed:
            _log.error("WebTiles: Websocket for user %s closed when sending "
                       "chat", self.username)
            # The connection will attempt reconnect.
            return
        except Exception as e:
            _log.error("WebTiles: Websocket error for user %s when sending "
                       "chat: %s", self.username, e.args[0])
            # We don't raise these errors because they're not important to
            # handle in terms of manager state. Just shutting down the
            # connection and let the manager try to make a new one if the game
            # is still (or becomes) active.
            yield from self.stop()
            return

    @asyncio.coroutine
    def listen_game(self, username, game_id):
        self.username = username
        self.game_id = game_id
        user_data = config.get_user_data("webtiles", username)
        if (user_data and user_data["subscribed"]
            or _conf.user_is_admin("webtiles", username)):
            self._need_greeting = False
        else:
            self._need_greeting = True

        if not self.connected():
            yield from self._connect()

        try:
            yield from self._send({"msg"      : "watch",
                                   "username" : self.username})
        except ConnectionClosed:
            _log.error("WebTiles: Websocket for user %s closed when sending "
                       "watch command", self.username)
            yield from self.stop()
            raise

        except Exception as e:
            _log.error("WebTiles: Unable to send watch message for user "
                       "%s: %s", self.username, e.args[0])
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

            except ConnectionClosed:
                _log.error("WebTiles: Websocket connection for user %s closed "
                           "when sending go_lobby command", self.username)
                yield from self.stop()
        if self.listening:
            manager.listen_end(self.username, self.game_id)
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
            _log.info("WebTiles: Listening to %s", self.username)
            return True

        if message["msg"] == "update_spectators":
            _log.debug("Got spectator string: %s", message["names"])
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
            _log.info("WebTiles: Game ended for %s", self.username)
            yield from self.stop_listening()
            return True

        if message["msg"] == "go_lobby":
            # The game we were watching stopped for some reason.
            _log.warning("Received go_lobby while listening to %s.",
                         self.username)
            yield from self.stop_listening()
            return True

        if self._logged_in and message["msg"] == "chat":
            user, command = _parse_chat(message["content"])
            yield from self.read_chat(user, command)
            return True

        if message["msg"] == "dump" and _conf.service_enabled("twitch"):
            user_data = config.get_user_data("webtiles", self.username)
            if not user_data or not user_data["twitch_user"]:
                return True

            chan = twitch.manager.get_channel(user_data["twitch_user"])
            if not chan:
                return True

            dump_msg = "Char dump: {}.txt".format(message["url"])
            yield from chan.send_chat(dump_msg)
            return True

        return False


class webtiles_manager():
    # Can't depend on beem.server or config.conf, as these aren't loaded yet.
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
            and self._autolisten.username == username
            and self._autolisten.game_id == game_id):
            return self._autolisten

        for conn in self._subscriber_conns:
            if (conn.username == username
                and conn.game_id == game_id):
                return conn

        return

    def get_source_by_key(self, source_key):
        return self._get_connection(source_key[0], source_key[1])

    @asyncio.coroutine
    def _new_subscriber_conn(self, username, game_id):
        # 0 is reserved for autolisten
        slot_nums = list(range(1,
                               _conf.webtiles["max_listened_subscribers"] + 1))
        listen_msg = ("WebTiles: Attempting to listen to subscribed user "
                      "{}".format(username))
        for conn in self._subscriber_conns:
            if (not conn.finished
                and conn.username
                and conn.slot_num in slot_nums):
                slot_nums.remove(conn.slot_num)

            if not conn.listening and not conn.finished:
                _log.info(listen_msg)
                try:
                    yield from conn.listen_game(username, game_id)
                except:
                    return

                return conn

        if not len(slot_nums):
            return

        conn = game_connection(min(slot_nums))
        _log.info(listen_msg)
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
    def _process_lobby(self):
        """Process lobby entries, adding games to the watch queue and checking for an
        autowatch candidate if need be.

        """

        wtconf = _conf.webtiles
        autolisten_username = None
        autolisten_game_id = None
        spectator_max = -1
        autolisten_enabled = (not wtconf.get("listen_user")
                              and wtconf.get("autolisten_enabled"))
        for entry in self._lobby.entries:
            idle_time = (entry["idle_time"] +
                         time.time() - entry["time_update"])
            if (not _game_allowed(entry["username"], entry["game_id"])
                or idle_time >= wtconf["max_game_idle"]):
                continue

            specs = entry["spectator_count"]
            if (autolisten_enabled
                and _able_to_listen()
                and specs >= wtconf["min_autolisten_spectators"]
                and specs > spectator_max):
                autolisten_username = entry["username"]
                autolisten_game_id = entry["game_id"]
                spectator_max = entry["spectator_count"]

            if (_should_listen_user(entry["username"])
                and not self._get_queue_entry(entry["username"],
                                              entry["game_id"])):
                self._add_queue(entry["username"], entry["game_id"])

        # Handle autolisten
        max_subs = wtconf["max_listened_subscribers"]
        if autolisten_username:
            conn = self._get_connection(autolisten_username, autolisten_game_id)
            if conn:
                # Autolisten candidate is already being watched as a
                # subscriber.
                if conn in self._subscriber_conns:
                    autolisten_username = None

                # A subscriber who's been autolistened now has an available
                # subscription slot. Give them a slot before giving one to
                # anyone else.
                elif (conn is self._autolisten
                      and _should_listen_user(autolisten_username)
                      and len(self._subscriber_conns) < max_subs):
                    self._subscriber_conns.add(self._autolisten)
                    self._autolisten = None
            # We found a game to autolisten
            else:
                _log.info("WebTiles: Attempting autolisten for user %s",
                          autolisten_username)
                if not self._autolisten:
                    self._autolisten = game_connection(0)
                try:
                    yield from self._autolisten.listen_game(autolisten_username,
                                                            autolisten_game_id)
                except:
                    self._autolisten = None

        # We don't have a valid game, so we shouldn't be autolistening.
        if (self._autolisten
            and self._autolisten.listening
            and not autolisten_username):
            _log.info("WebTiles: Stop autolistening for %s",
                      self._autolisten.username)
            yield from self._autolisten.stop_listening(True)

    @asyncio.coroutine
    def _process_queue(self):
        """Update the subscriber listen queue, listening any games that we can.

        """

        wtconf = _conf.webtiles
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
            wait_relisten = (entry["time_end"]
                             and time.time() - entry["time_end"] < 5)
            expireable = (not entry["time_end"]
                          or time.time() - entry["time_end"] >= relisten_timeout)
            able = _able_to_listen()
            if conn and (not able or not allowed or idle):
                if not able:
                    reason = "No longer able"
                elif not allowed:
                    reason = "No longer allowed"
                else:
                    reason = "Game idle"
                yield from conn.stop_listening(True)
                _log.info("WebTiles: Stop listening to %s: %s",
                          entry["username"], reason)
            # The game is no longer eligable or been offline for sufficiently
            # long.
            if not allowed or idle or not lobby and expireable:
                self._listen_queue.remove(entry)
                continue

            # We can't listen yet or we already have a designated connection.
            if not _able_to_listen() or not lobby or wait_relisten or conn:
                continue

            # Try to give the game a subscriber slot. If it fails, the entry
            # will remain in the queue until the entry expires.
            yield from self._new_subscriber_conn(entry["username"],
                                                 entry["game_id"])

    @asyncio.coroutine
    def beem_command(self, source, sender, user, command, args):
        wtconf = _conf.webtiles
        bot_name = wtconf["username"]
        target_user = user
        if source.service_name == "twitch":
            if not config.get_user_data("twitch", user):
                yield from source.send_chat(
                    "Twitch user {} is not registered".format(user))
                return

            target_user = config.get_webtiles_username(user)
            if not target_user:
                yield from source.send_chat(
                    "You must have an admin link to your WebTiles username to "
                    "your Twitch username")
                return

        user_data = config.get_user_data("webtiles", target_user)
        if command == "nick":
            if not args:
                if not user_data or not user_data["nick"]:
                    yield from source.send_chat(
                        "No nick for WebTiles user {}".format(target_user))
                else:
                    yield from source.send_chat(
                        "Nick for WebTiles user {}: {}".format(
                            target_user, user_data["nick"]))
                return

            if not user_data:
                try:
                    config.register_user(source, sender, "webtiles",
                                         target_user)
                except:
                    yield from source.send_chat("Error when registering")
                    return

            try:
                if user_data["nick"] != args[0]:
                    config.set_user_field(source, sender, "webtiles",
                                          target_user, "nick", args[0])
            except:
                yield from source.send_chat("Error when setting nick")
                return

            yield from source.send_chat(
                "Nick for WebTiles user {} set to {}".format(
                    target_user, args[0]))
            return

        if command == "subscribe":
            if not user_data:
                try:
                    config.register_user(source, sender, "webtiles",
                                         target_user)
                except:
                    yield from source.send_chat("Error when registering")
                    return

                user_data = config.get_user_data("webtiles", target_user)

            try:
                if not user_data["subscribed"]:
                    config.set_user_field(source, sender, "webtiles",
                                          target_user, "subscribed", 1)
            except:
                yield from source.send_chat("Error when subscribing")
                return

            yield from source.send_chat(
                "Subscribed. {} will watch the games of {} "
                "automatically".format(bot_name, target_user))
            return

        if command == "unsubscribe":
            if not user_data:
                try:
                    config.register_user(source, sender, "webtiles",
                                         target_user)
                except:
                    yield from source.send_chat("Error when registering")
                    return

                user_data = config.get_user_data("webtiles", target_user)

            try:
                if user_data["subscribed"]:
                    config.set_user_field(source, sender, "webtiles",
                                          target_user, "subscribed", 0)
            except:
                yield from source.send_chat("Error when unsubscribing")
                return

            yield from source.send_chat(
                "Unsubscribed. {} will not watch the games of {}".format(
                    bot_name, target_user))
            return

        if command == "twitch-user":
            if not args:
                if not user_data:
                    yield from source.send_chat(
                        "WebTiles User {} is not registered.".format(
                            target_user))
                    return

                if not user_data["twitch_user"]:
                    yield from source.send_chat("No Twitch link for WebTiles "
                                                "user {}".format(target_user))
                    return
                else:
                    yield from source.send_chat(
                        "Twitch link for WebTiles user {}: {}".format(
                            target_user, user_data["twitch_user"]))
                    return

            if not user_data:
                try:
                    config.register_user(source, sender, "webtiles",
                                         target_user)
                except:
                    yield from source.send_chat("Error when registering")
                    return

                user_data = config.get_user_data("webtiles", target_user)

            if not config.get_user_data("twitch", args[0]):
                try:
                    config.register_user(source, sender, "twitch", args[0])
                except:
                    yield from source.send_chat(
                        "Error when registering Twitch user")
                    return

            try:
                if user_data["twitch_user"] != args[0]:
                    config.set_user_field(source, sender, "webtiles",
                                          target_user, "twitch_user", args[0])
            except:
                yield from source.send_chat("Error when setting Twitch link")
                return

            yield from source.send_chat(
                "Twitch link for WebTiles user {} set to {}".format(
                    target_user, args[0]))
            return

        if command == "twitch-reminder":
            if not user_data:
                yield from source.send_chat(
                    "WebTiles user {} is not registered".format(target_user))
                return

            twitch_user = user_data["twitch_user"]
            if not twitch_user:
                yield from source.send_chat(
                    "You must have an admin link your WebTiles username to "
                    "your Twitch username")
                return

            if not args:
                state = "on" if user_data["twitch_reminder"] else "off"
                yield from source.send_chat(
                    "Twitch reminder for user {} is {}".format(target_user,
                                                               state))
                return

            state = 1 if args[0] == "on" else 0
            try:
                if user_data["twitch_reminder"] != state:
                    config.set_user_field(source, sender, "webtiles",
                                          target_user, "twitch_reminder", state)
            except:
                yield from source.send_chat(
                    "Error when setting Twitch reminder.")
                return

            yield from source.send_chat(
                "Twitch reminder for user {} is now {}".format(
                    target_user, args[0]))
            return

    @asyncio.coroutine
    def _process(self):
        """Based on lobby information, do autolisten and listen to subscribers"""

        assert(self._lobby)

        if self._lobby.complete:
            yield from self._process_lobby()
        yield from self._process_queue()


        if (self._autolisten
            and self._autolisten.finished
            and self._autolisten.get_task() is None):
            self._autolisten = None

        for conn in list(self._subscriber_conns):
            if conn.finished and conn.get_task() is None:
                self._subscriber_conns.remove(conn)

        # Update the queue once per second
        yield from asyncio.sleep(1)

    def listen_end(self, username, game_id):
        queue = self._get_queue_entry(username, game_id)
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

def _able_to_listen():
    """Are we presently able to listen to any game?"""

    # Don't listen to games if dcss irc isn't ready.
    return _conf.service_enabled("webtiles") and dcss.manager.logged_in


def _can_listen_user(user):
    if _conf.get("single_user"):
        return user == _conf.webtiles.get("listen_user")

    if _conf.user_is_admin("webtiles", user):
        return True

    if _conf.webtiles.get("never_listen"):
        for u in _conf.webtiles["never_listen"]:
            if u.lower() == user.lower():
                return False

    user_data = config.get_user_data("webtiles", user)
    if user_data and not user_data["subscribed"]:
        return False

    return True

def _should_listen_user(user):
    if _conf.webtiles.get("listen_user"):
        return user == _conf.webtiles["listen_user"]

    if _conf.user_is_admin("webtiles", user):
        return True

    # User is subscribed.
    user_data = config.get_user_data("webtiles", user)
    return user_data and user_data["subscribed"]

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

manager = webtiles_manager()
config.register_service("webtiles", "WebTiles", "w", manager)
