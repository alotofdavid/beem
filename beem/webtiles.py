"""Define the `webtiles_manager` WebTiles manager instance and WebTiles service
data."""

import asyncio
import logging
import os
import re
import signal
import time
import webtiles
from websockets.exceptions import ConnectionClosed

from .chat import ChatWatcher, beem_nick_command, is_bot_command
from .config import beem_conf, services
from .dcss import dcss_manager
from .twitch import twitch_manager
from .userdb import get_user_data, register_user, set_user_field

_log = logging.getLogger()

# How many seconds to wait after sending a login or watch request before we
# timeout.
_REQUEST_TIMEOUT = 10
# How many seconds to wait after a game ends before attempting to watch the
# game again.
_REWATCH_WAIT = 5

class LobbyConnection(webtiles.WebTilesConnection):
    def __init__(self):
        super().__init__()
        self.task = None

    @asyncio.coroutine
    def start(self):
        try:
            yield from self.connect(beem_conf.webtiles["server_url"])
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to connect to %s: %s",
                       beem_conf.webtiles["server_url"], err_reason)
            yield from self.disconnect()
            # Wait a second before we'll attempt this again.
            yield from asyncio.sleep(1)
            return

        while True:
            messages = None
            try:
                messages = yield from self.read()
            # Task canceled by stop()
            except asyncio.CancelledError:
                return
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("WebTiles: Unable to read lobby WebSocket: %s",
                           err_reason)
                yield from self.disconnect()
                return

            if not messages:
                continue

            for message in messages:
                try:
                    messages = yield from self.handle_message(message)
                # Task canceled by stop_connection()
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    err_reason = type(e).__name__
                    if e.args:
                        err_reason = e.args[0]
                    _log.error("WebTiles: Unable to handle Lobby WebSocket "
                               "message: %s", err_reason)
                    yield from webtiles_manager.stop_connection(self)

            yield from asyncio.sleep(0.1)


class GameConnection(webtiles.WebTilesGameConnection, ChatWatcher):
    """A game websocket connection that watches chat and responds to commands.

    """

    def __init__(self, username, game_id):
        super().__init__()

        wtconf = beem_conf.webtiles
        user_data = get_user_data("webtiles", username)
        if user_data and user_data["subscription"] > 0:
            self.need_greeting = False
        else:
            self.need_greeting = True
        self.game_username = username
        self.game_id = game_id
        self.service = "webtiles"
        self.bot_name = beem_conf.webtiles["username"]
        self.irc_channel = "WebTiles"
        self.time_since_request = None
        self.task = None
        # Last time we either send the watch command or had watched a game,
        # used so we can reuse connections, but end them after being idle for
        # too long.
        self.last_reminder_time = None

    def request_timeout(self):
        return (self.time_since_request
                and time.time() - self.time_since_request >= _REQUEST_TIMEOUT)

    def get_source_key(self):
        """Get a unique identifier tuple of the game for this connection.
        Identifies this game connection as a source for chat watching. This is
        used to map DCSS queries to their results as they're received.

        """

        return (self.service, self.game_username, self.game_id)

    @asyncio.coroutine
    def start(self):
        wtconf = beem_conf.webtiles
        if not self.connected():
            try:
                yield from self.connect(wtconf["server_url"],
                                        wtconf["username"], wtconf["password"])
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("WebTiles: Unable to connect to %s: %s",
                           wtconf["server_url"], err_reason)
                yield from webtiles_manager.stop_connection(self)
                return

        while True:
            if self.request_timeout():
                yield from webtiles_manager.stop_connection(self)
                return

            if (self.logged_in
                and self.game_username
                and not self.watching
                and not self.time_since_request):
                yield from self.send_watch_game(self.game_username,
                                                self.game_id)
                self.time_since_request = time.time()

            yield from self.handle_greeting()
            yield from self.handle_reminder()

            messages = None
            try:
                messages = yield from self.read()
            # Task canceled by stop_connection()
            except asyncio.CancelledError:
                return
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("WebTiles: Unable to read game WebSocket (watch "
                           "user: %s): %s", self.game_username, err_reason)
                yield from webtiles_manager.stop_connection(self)

            if not messages:
                continue

            for message in messages:
                try:
                    messages = yield from self.handle_message(message)
                # Task canceled by stop_connection()
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    err_reason = type(e).__name__
                    if e.args:
                        err_reason = e.args[0]
                    _log.error("WebTiles: Unable to handle game WebSocket "
                               "message (watch user: %s): %s",
                               self.game_username, err_reason)
                    yield from webtiles_manager.stop_connection(self)

            yield from asyncio.sleep(0.1)

    @asyncio.coroutine
    def handle_greeting(self):
        if not self.watching or not self.need_greeting:
            return

        greeting = beem_conf.webtiles["greeting_text"].replace("\n", " ")
        greeting = greeting.replace("%n", self.bot_name)
        yield from self.send_chat(greeting)
        self.need_greeting = False

    @asyncio.coroutine
    def handle_reminder(self):
        if (not self.watching
            or not beem_conf.service_enabled("twitch")
            or not beem_conf.webtiles.get("twitch_reminder_text")
            or not twitch_manager.is_connected()
            or not self.spectators
            or self.spectators == set(self.game_username)):
            return

        user_data = get_user_data("webtiles", self.game_username)
        reminder_period = beem_conf.webtiles["twitch_reminder_period"]
        if (not user_data
            or not user_data["twitch_username"]
            or not user_data["twitch_reminder"]):
            return

        chan = twitch_manager.get_channel(user_data["twitch_username"])
        if not chan:
            return

        if (self.last_reminder_time
            and time.time() - self.last_reminder_time < reminder_period):
            return

        if self.game_username[-1] == "s" or self.game_username[-1] == "S":
            user_possessive = self.game_username + "'"
        else:
            user_possessive = self.game_username + "'s"

        msg = beem_conf.webtiles["twitch_reminder_text"].replace("\n", " ")
        msg = msg.replace("%us", user_possessive)
        msg = msg.replace("%u", self.game_username)
        msg = msg.replace("%t", user_data["twitch_username"])
        yield from self.send_chat(msg)
        self.last_reminder_time = time.time()

    @asyncio.coroutine
    def send_chat(self, message, is_action=False):
        """Send a WebTiles chat message. We currently shut down the game
        connection if an error occurs and log the event, but don't raise to the
        caller, since we don't care to take any action.

        """

        if is_action:
            message = "*{}* {}".format(beem_conf.webtiles["username"], message)
        # In case any other beem bot happens to watch in the same
        # channel, don't cause a feedback loop by relaying Sequell output.
        elif is_bot_command(message):
            message = "]" + message

        try:
            yield from self.send({"msg" : "chat_msg", "text" : message})
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("WebTiles: Unable to send chat message (watch user: "
                       "%s, error: %s): %s", self.game_username, err_reason,
                       message)
            yield from webtiles_manager.stop_connection(self)
            return

    @asyncio.coroutine
    def handle_message(self, message):
        if message["msg"] == "login_success":
            self.time_since_request = None

        elif message["msg"] == "login_fail":
            _log.info("WebTiles: Login to %s failed, shutting down server.",
                      beem_conf.webtiles["server_url"])
            os.kill(os.getpid(), signal.SIGTERM)

        elif message["msg"] == "watching_started":
            self.time_since_request = None
            _log.info("WebTiles: Watching user %s", self.game_username)

        elif message["msg"] == "game_ended" and self.watching:
            _log.info("WebTiles: Game ended for user %s", self.game_username)

        elif message["msg"] == "go_lobby" and self.watching:
            # The game we were watching stopped for some reason.
            _log.warning("Received go_lobby while watching user %s.",
                         self.game_username)

        elif self.logged_in and message["msg"] == "chat":
            user, chat_message = parse_chat(message["content"])
            yield from self.read_chat(user, chat_message)

        elif message["msg"] == "dump" and beem_conf.service_enabled("twitch"):
            user_data = get_user_data("webtiles", self.game_username)
            if user_data and user_data["twitch_username"]:
                chan = twitch_manager.get_channel(user_data["twitch_username"])
                if chan:
                    dump_msg = "Char dump: {}.txt".format(message["url"])
                    yield from chan.send_chat(dump_msg)

        yield from super().handle_message(message)


class WebTilesManager():
    # Can't depend on config.conf, as the data for this isn't loaded yet.
    def __init__(self):
        self.lobby = None
        self.autowatch_candidate = None
        self.autowatch = None
        self.watch_queue = []
        self.subscriber_conns = set()

    def get_connection(self, username, game_id):
        """Get any existing connection for the given game."""

        if (self.autowatch
            and self.autowatch.game_username
            and self.autowatch.game_username == username
            and self.autowatch.game_id == game_id):
            return self.autowatch

        for conn in self.subscriber_conns:
            if (conn.game_username
                and conn.game_username == username
                and conn.game_id == game_id):
                return conn

        return

    def get_source_by_key(self, source_key):
        return self.get_connection(source_key[1], source_key[2])

    @asyncio.coroutine
    def stop_connection(self, conn):
        """Shut down the game connection"""

        if conn.task and not conn.task.done():
            try:
                conn.task.cancel()
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("WebTiles: Error canceling task: %s", err_reason)

        if conn is self.autowatch:
            self.autowatch = None
        elif conn in self.subscriber_conns:
            if conn.watching:
                self.set_watch_end(conn)
            self.subscriber_conns.remove(conn)
        yield from conn.disconnect()

    @asyncio.coroutine
    def new_subscriber_conn(self, username, game_id):
        wtconf = beem_conf.webtiles
        if len(self.subscriber_conns) >= wtconf["max_watched_subscribers"]:
            return

        conn = GameConnection(username, game_id)
        conn.task = asyncio.ensure_future(conn.start())
        self.subscriber_conns.add(conn)
        return conn

    @asyncio.coroutine
    def stop(self):
        if self.lobby:
            if self.lobby.task and not self.lobby.task.done():
                self.lobby.task.cancel()
            yield from self.lobby.disconnect()

        if self.autowatch:
            yield from self.stop_connection(self.autowatch)

        for conn in list(self.subscriber_conns):
            yield from self.stop_connection(conn)

        self.watch_queue = []

    @asyncio.coroutine
    def start(self):
        _log.info("WebTiles: Starting manager")
        wtconf = beem_conf.webtiles
        if not self.lobby:
            self.lobby = LobbyConnection()
        while True:
            if not self.lobby.task or self.lobby.task.done():
                self.lobby.task = asyncio.ensure_future(self.lobby.start())

            autowatch_game = None
            if self.lobby.lobby_complete:
                autowatch_game = self.process_lobby()
            if autowatch_game:
                yield from self.do_autowatch_game(autowatch_game)
            else:
                yield from self.check_current_autowatch()

            yield from self.process_queue()
            yield from asyncio.sleep(0.1)

    def add_queue(self, username, game_id, pos=None):
        entry = {"username" : username,
                 "game_id"  : game_id,
                 "time_end" : None}
        if pos is None:
            pos = len(self.watch_queue)
        self.watch_queue.insert(pos, entry)
        pass

    def get_queue_entry(self, username, game_id):
        for entry in self.watch_queue:
            ### XXX For now we ignore game_id, since webtiles can't make unique
            ### watch URLs by game for the same user.
            if entry["username"] == username:
                return entry
        return

    @asyncio.coroutine
    def do_autowatch_game(self, game):
        username, game_id = game
        if (self.autowatch
            and self.autowatch.game_username == username
            and self.autowatch.game_id == game_id):
            return

        _log.info("WebTiles: Found new autowatch user %s", username)
        wtconf = beem_conf.webtiles
        if self.autowatch and self.autowatch.watching:
            _log.info("WebTiles: Stopping autowatch for user %s: new "
                      "autowatch game found", self.autowatch.game_username)

        if not self.autowatch:
            self.autowatch = GameConnection(username, game_id)
            self.autowatch.task = asyncio.ensure_future(self.autowatch.start())
        else:
            yield from self.autowatch.send_watch_game(username, game_id)

    @asyncio.coroutine
    def check_current_autowatch(self):
        """When we don't find a new autowatch candidate, check that we're still
        able to watch our present autowatch game.

        """

        if not self.autowatch:
            return

        wtconf = beem_conf.webtiles
        lobby_entry = None
        for entry in self.lobby.lobby_entries:
            if (entry["username"] == self.autowatch.game_username
                and entry["game_id"] == self.autowatch.game_id):
                lobby_entry = entry
                break

        # Game no longer has a lobby entry, but let the connection itself
        # handle any stop watching event from the server.
        if not lobby_entry:
            return

        # See if this game is no longer eligable for autowatch. We don't
        # require a min. spectator count after the initial autowatch, since
        # doing so just leads to a lot of flucutation in autowatching.
        idle_time = (lobby_entry["idle_time"] +
                     time.time() - lobby_entry["time_last_update"])
        game_allowed = is_game_allowed(self.autowatch.game_username,
                                       self.autowatch.game_id)
        end_reason = None
        if not game_allowed:
            end_reason = "Game disallowed"
        elif not dcss_manager.logged_in:
            end_reason = "DCSS not ready"
        elif idle_time >= wtconf["max_game_idle"]:
            end_reason = "Game idle"
        else:
            return

        _log.info("WebTiles: Stopping autowatch for user %s: %s",
                  self.autowatch.game_username, end_reason)
        yield from self.stop_connection(self.autowatch)

    def process_lobby(self):
        """Process lobby entries, adding games to the watch queue and return an
        autowatch candidate if one is found.

        """

        wtconf = beem_conf.webtiles
        min_spectators = wtconf["min_autowatch_spectators"]
        max_subscribers = wtconf["max_watched_subscribers"]
        autowatch_spectators = -1
        current_time = time.time()
        autowatch_game = None
        for entry in self.lobby.lobby_entries:
            idle_time = (entry["idle_time"] +
                         current_time - entry["time_last_update"])
            if (not is_game_allowed(entry["username"], entry["game_id"])
                or idle_time >= wtconf["max_game_idle"]):
                continue

            if (user_is_subscribed(entry["username"])
                and not self.get_queue_entry(entry["username"],
                                              entry["game_id"])):
                self.add_queue(entry["username"], entry["game_id"])

            conn = self.get_connection(entry["username"], entry["game_id"])
            # Only subscribers who don't have subscriber slots are valid
            # autowatch candidates.
            no_free_slot = (not conn in self.subscriber_conns
                            and len(self.subscriber_conns) >= max_subscribers)
            # Find an autowatch candidate
            if (wtconf.get("autowatch_enabled")
                and dcss_manager.logged_in
                and entry["spectator_count"] >= min_spectators
                and (not user_is_subscribed(entry["username"]) or no_free_slot)
                # If there's a tie, favor a game we're already autowatching
                # instead of letting the order of iteration decide.
                and (conn
                     and conn is self.autowatch
                     and entry["spectator_count"] == autowatch_spectators
                     or entry["spectator_count"] > autowatch_spectators)):
                autowatch_spectators = entry["spectator_count"]
                autowatch_game = (entry["username"], entry["game_id"])

        return autowatch_game

    @asyncio.coroutine
    def process_queue(self):
        """Update the subscriber watch queue, watching any games that we can.

        """

        wtconf = beem_conf.webtiles
        max_subscribers = wtconf["max_watched_subscribers"]
        rewatch_timeout = wtconf["game_rewatch_timeout"]
        for entry in list(self.watch_queue):
            lobby = self.lobby.get_lobby_entry(entry["username"], entry["game_id"])
            idle_time = 0
            if lobby:
                idle_time = (lobby["idle_time"] +
                             time.time() - lobby["time_last_update"])
            conn = self.get_connection(entry["username"], entry["game_id"])
            idle = idle_time >= wtconf["max_game_idle"]
            allowed = is_game_allowed(entry["username"], entry["game_id"])
            wait = (entry["time_end"]
                    and time.time() - entry["time_end"] < _REWATCH_WAIT)
            expired = (not entry["time_end"]
                       or time.time() - entry["time_end"] >= rewatch_timeout)
            if conn:
                end_reason = None
                if not allowed:
                    end_reason = "Game disallowed"
                if not dcss_manager.logged_in:
                    end_reason = "DCSS not ready"
                elif idle:
                    end_reason = "Game idle"
                if end_reason:
                    _log.info("WebTiles: Stopping watching of user %s: %s",
                              entry["username"], end_reason)
                    yield from self.stop_connection(conn)
                # An autowatched subscriber without a subscriber slot now has
                # one.
                elif (conn is self.autowatch
                      and len(self.subscriber_conns) < max_subscribers):
                    self.subscriber_conns.add(conn)
                    self.autowatch = None
                    continue

            # The game is no longer eligable or been offline for sufficiently
            # long.
            if not allowed or idle or not lobby and expired:
                self.watch_queue.remove(entry)
                continue

            # We can't watch yet or they already have a subscriber slot.
            if not dcss_manager.logged_in or not lobby or wait or conn:
                continue

            # Try to give the game a subscriber slot. If this fails, the entry
            # will remain in the queue for subsequent attempts.
            yield from self.new_subscriber_conn(entry["username"],
                                                entry["game_id"])

    def set_watch_end(self, conn):
        queue = self.get_queue_entry(conn.game_username, conn.game_id)
        if not queue:
            return

        queue["time_end"] = time.time()


def parse_chat(message):
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

def can_watch_user(user):
    if beem_conf.get("single_user"):
        return user == beem_conf.webtiles["watch_user"]

    if beem_conf.webtiles.get("never_watch"):
        for u in beem_conf.webtiles["never_watch"]:
            if u.lower() == user.lower():
                return False

    user_data = get_user_data("webtiles", user)
    if user_data and user_data["subscription"] < 0:
        return False

    return True

def user_is_subscribed(user):
    # User is subscribed.
    user_data = get_user_data("webtiles", user)
    return user_data and user_data["subscription"] > 0

def is_game_allowed(username, game_id):
    """Can this game ever be watched?

    A game is disallowed if the user is not allowed or the game is
    of too old a version.
    """
    if not can_watch_user(username):
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
def beem_subscribe_command(source, target_user):
    """`!<bot-name> subscribe` chat command"""

    user_data = get_user_data("webtiles", target_user)
    if not user_data:
        user_data = register_user("webtiles", target_user)

    if user_data["subscription"] == 1:
        yield from source.send_chat(
            "User {} is already subscribed".format(target_user))
        return

    set_user_field("webtiles", target_user, "subscription", 1)
    yield from source.send_chat(
        "Subscribed. {} will now watch all games of user {}".format(
            source.bot_name, target_user))

@asyncio.coroutine
def beem_unsubscribe_command(source, target_user):
    """`!<bot-name> unsubscribe` chat command"""

    user_data = get_user_data("webtiles", target_user)
    if not user_data:
        user_data = register_user("webtiles", target_user)

    if user_data["subscription"] == -1:
        yield from source.send_chat(
            "User {} is already unsubscribed".format(target_user))
        return

    set_user_field("webtiles", target_user, "subscription", -1)
    msg = "Unsubscribed. {} will no longer watch games of user {}.".format(
        source.bot_name, target_user)
    # We'll be leaving the chat of this source.
    if source.game_username == target_user:
        msg += " Bye!"
    yield from source.send_chat(msg)

@asyncio.coroutine
def beem_twitch_user_command(source, target_user, twitch_user=None):
    """`!<bot-name> twitch-user` chat command"""

    user_data = get_user_data("webtiles", target_user)
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
        user_data = register_user("webtiles", target_user)

    if not get_user_data("twitch", twitch_user):
        register_user("twitch", twitch_user)

    set_user_field("webtiles", target_user, "twitch_username", twitch_user)
    yield from source.send_chat("User {} linked to Twitch username {}".format(
        target_user, twitch_user))

@asyncio.coroutine
def beem_twitch_reminder_command(source, target_user, state=None):
    """`!<bot-name> twitch-reminder` chat command"""

    user_data = get_user_data("webtiles", target_user)
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
    set_user_field("webtiles", target_user, "twitch_reminder", state_val)
    yield from source.send_chat("Twitch reminder {} for user {}".format(
        state_desc, target_user))

# The WebTiles manager created when the module is loaded.
webtiles_manager = WebTilesManager()

# WebTiles service data
services["webtiles"] = {
    "name"                : "WebTiles",
    "manager"             : webtiles_manager,
    "user_fields"         : ["nick", "subscription", "twitch_username",
                             "twitch_reminder"],
    "user_field_defaults" : ["", 0, "", 0],
    "commands" : {
        "subscribe" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : beem_subscribe_command,
        },
        "unsubscribe" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : beem_unsubscribe_command,
        },
        "nick" : {
            "arg_pattern" : r"^[a-zA-Z0-9_-]+$",
            "arg_description" : "<nick>",
            "single_user" : True,
            "function" : beem_nick_command
        },
        "twitch-user" : {
            "arg_pattern" : r"^[a-zA-Z0-9][a-zA-Z0-9_]{3,24}$",
            "arg_description" : "<twitch-username>",
            "single_user" : False,
            "function" : beem_twitch_user_command,
        },
        "twitch-reminder" : {
            "arg_pattern" : r"^(on|off)$",
            "arg_description" : "on|off",
            "single_user" : True,
            "function" : beem_twitch_reminder_command,
        },
    }
}
