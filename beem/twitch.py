"""Define the `twitch_manager` Twitch manager instance and Twitch
service data"""

import asyncio
import irc.client
import functools
import logging
import os
import re
import sqlite3
import time

from .chat import ChatWatcher, is_bot_command, beem_nick_command
from .config import beem_conf, services
from .userdb import get_user_data, register_user
from .dcss import dcss_manager

_log = logging.getLogger()

class TwitchChannel(ChatWatcher):
    """Holds data for a single Twitch channel and sends chat commands to
    `twitch.manager`.

    """

    def __init__(self, username):
        super().__init__()
        self.game_username = username
        self.service = "twitch"
        self.irc_channel = "#" + username
        self.bot_name = beem_conf.twitch["nick"]
        self.message_count = 0
        self.time_last_message = time.time()
        self.is_moderator = False
        self.spectators = set()

    def get_source_key(self):
        """Get a unique identifier tuple of the game for this connection.
        Identifies this game connection as a source for chat
        watching. This is used to map DCSS queries to their results
        as they're received.

        """

        return (self.service, self.game_username)

    @asyncio.coroutine
    def send_chat(self, message, is_action=False):
        """Send a Twitch chat message. We currently shut down the game
        connection if an error occurs and log the event, but don't raise to the
        caller, since we don't care to take any action.

        """

        ## These are interpreted by the Twitch irc server, so prepend a
        ## space, which will get removed by the server anyhow.
        if message[0] == "." or message[0] == "/":
            message = " " + message
        elif not is_action and is_bot_command(message):
            message = "]" + message

        twitch_manager.send_channel(self, message, is_action)


class TwitchManager():
    # Can't depend on beem_config, as the data for this isn't loaded yet.
    def __init__(self):
        self.channels = set()
        self.reactor = irc.client.Reactor()
        self.reactor.add_global_handler("all_events", self.dispatcher, -10)
        self.server = self.reactor.server()
        self.logged_in = False

    @asyncio.coroutine
    def connect(self):
        """Connect to Twitch IRC"""

        self.messages = []
        self.message_count = 0
        self.channels = set()
        self.time_last_message = None
        self.sent_normal_message = False
        self.logged_in = False

        if beem_conf.get("single_user"):
            self.bot_channel = TwitchChannel(beem_conf.twitch["watch_user"])
        else:
            self.bot_channel = TwitchChannel(beem_conf.twitch["nick"])

        if beem_conf.twitch.get("fake_connect"):
            self.logged_in = True
            return

        if self.server.is_connected():
            self.server.disconnect()

        _log.info("Twitch: Connecting to IRC server %s using nick %s",
                  beem_conf.twitch["hostname"], beem_conf.twitch["nick"])
        self.server.connect(beem_conf.twitch["hostname"],
                            beem_conf.twitch["port"], beem_conf.twitch["nick"],
                            beem_conf.twitch["password"], None,
                            beem_conf.twitch["nick"])
        # To get JOIN/PART/USER data
        self.server.cap("REQ", ":twitch.tv/membership")
        self.join_channel(self.bot_channel)
        self.logged_in = True

    def is_connected(self):
        # Make sure _connect() is run once even under fake_connect
        if beem_conf.twitch.get("fake_connect") and self.logged_in:
            return True

        return self.server.is_connected()

    def timeout_finished(self):
        timeout = beem_conf.twitch["message_timeout"]
        return (self.time_last_message
                and time.time() - self.time_last_message > timeout)

    def get_source_by_key(self, source_key):
        return self.get_channel(source_key[1])

    @asyncio.coroutine
    def start(self):
        self.watch_queue = []
        _log.info("Twitch: Starting manager")
        while True:
            while not self.is_connected():
                try:
                    yield from self.connect()
                except irc.client.IRCError as e:
                    _log.error("Twitch: IRC error when connecting: {0}".format(
                        e.args[0]))
                    yield from asyncio.sleep(beem_conf.reconnect_timeout)

            if self.timeout_finished():
                self.message_count = 0
                self.time_last_message = None
                self.sent_normal_message = False

            try:
                self.reactor.process_once()
            except irc.client.IRCError as e:
                _log.error("Twitch: IRC error when reading: {0}".format(
                    e.args[0]))

            self.update_queue()

            ## Handle any incoming messages, sending them to the appropriate
            ## channel watcher.
            for m in list(self.messages):
                username, sender, message = m
                self.messages.remove(m)
                chan = self.get_channel(username)
                if not chan:
                    _log.warning("Twitch: Can't find channel for user: %s",
                                 username)
                    continue

                yield from chan.read_chat(sender, message)

            ## This seems needed to give other coroutines a chance to run.
            yield from asyncio.sleep(0.1)

    def disconnect(self):
        """Disconnect Twitch IRC. This will log any disconnection error, but never
        raise.

        """
        if (beem_conf.twitch.get("fake_connect")
            or not self.server.is_connected()):
            return

        try:
            self.server.disconnect()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("Twitch: Error when disconnecting: %s", err_reason)

    def dispatcher(self, connection, event):
        """
        Dispatch events to on_<event.type> method, if present.
        """

        if event.type == "pubmsg":
            self.on_pubmsg(event)

    def on_pubmsg(self, event):
        """
        Handle Twitch chat messages
        """
        match = re.match("([^!]+)!", event.source)
        if not match:
            return

        sender = match.group(1)
        message = event.arguments[0]
        username = event.target[1:]
        # Twitch uses messages beginning with . as channel commands, so we
        # have users use _ and replace this with . for sending to Sequell.
        if message[0] == "_":
            message = "." + message[1:]
        self.messages.append((username, sender, message))

    def add_queue(self, username):
        entry = {"username"     : username,
                 "parted"       : False,
                 "time_request" : None}
        self.watch_queue.append(entry)

    def get_queue_entry(self, username):
        for entry in self.watch_queue:
            if entry["username"] == username:
                return entry

        return

    def get_channel(self, username):
        if beem_conf.get("single_user"):
            if username == beem_conf.twitch["watch_user"]:
                return self.bot_channel
            else:
                return

        elif username == beem_conf.twitch["nick"]:
            return self.bot_channel

        for chan in self.channels:
            if chan.game_username == username:
                return chan

        return

    def stop_watching(self, channel):
        self.channels.remove(channel)
        if self.message_limited(True):
            raise Exception("reached message limit")

        self.sent_normal_message = True
        self.message_count += 1
        if beem_conf.twitch.get("fake_connect"):
            return

        try:
            self.server.part(channel.irc_channel)
        except irc.client.IRCError as e:
            raise Exception("irc error: {}".format(e.args[0]))
        else:
            _log.info("Twitch: Leaving channel of user %s",
                      channel.game_username)

    def new_channel(self, username):
        twconf = beem_conf.twitch
        if len(self.channels) >= twconf["max_watched_subscribers"]:
            idle_chan = None
            max_idle = -1
            for chan in self.channels:
                idle_time = time.time() - chan.time_last_message
                if idle_time >= twconf["min_idle"] and idle_time >= max_idle:
                    idle_chan = chan
                    break

            if not idle_chan:
                raise Exception("Watching too many channels.")

            chan = idle_chan
            self.stop_watching(idle_chan)
        else:
            chan = TwitchChannel(username)

        self.join_channel(chan)
        self.channels.add(chan)

    def update_queue(self):
        twconf = beem_conf.twitch
        expire_time = twconf["request_expire_time"]
        max_idle = twconf["max_chat_idle"]
        # Update the subscriber watch queue, joining/parting channels as
        # necessary.
        able = able_to_watch()
        for entry in list(self.watch_queue):
            chan = self.get_channel(entry["username"])
            allowed = can_watch_user(entry["username"])
            expired = (entry["time_request"]
                       and time.time() - entry["time_request"] >= expire_time)
            if (chan
                and (not able or not allowed or entry["parted"] or expired)):
                try:
                    self.stop_watching(chan)
                except Exception as e:
                    err_reason = type(e).__name__
                    if e.args:
                        err_reason = e.args[0]
                    _log.error("Twitch: Unable to send part message for user "
                               "%s: ", chan.game_username, err_reason)
                    # If there's an error parting, leave the entry to the next
                    # update.
                    continue

            if not allowed or entry["parted"] or expired:
                self.watch_queue.remove(entry)
                continue

            if chan:
                continue

            try:
                self.new_channel(entry["username"])
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("Twitch: Unable to join channel for user %s: "
                           "%s", entry["username"], err_reason)

    def message_limited(self, is_normal):
        if is_normal or self.sent_normal_message:
            limit = beem_conf.twitch["message_limit"]
        else:
            limit = beem_conf.twitch["moderator_message_limit"]
        return self.message_count >= limit

    def join_channel(self, channel):
        if self.message_limited(True):
            raise Exception("reached message limit")

        self.sent_normal_message = True
        self.message_count += 1
        if beem_conf.twitch.get("fake_connect"):
            return

        self.server.join(channel.irc_channel)
        _log.info("Twitch: Joining channel of user %s", channel.game_username)

    def send_channel(self, channel, message, is_action=False):
        """Send a message to the given channel. If `is_action` is True, the
        message will be an IRC action message, which is displayed
        differently in chat. If an error occurs, we log the event, but
        don't raise to the caller, since we don't care to take any
        action.

        """

        if self.message_limited(not channel.is_moderator):
            _log.info("Twitch: Didn't send chat message for channel %s due to "
                      "message limit", )
            return

        if beem_conf.twitch.get("fake_connect"):
            send_func = lambda channel, message: True
        elif is_action:
            send_func = self.server.action
        else:
            send_func = self.server.privmsg

        try:
            send_func(channel.irc_channel, message)
        except irc.client.IRCError as e:
            _log.error("Twitch: Unable to send chat message (watch user: %s, "
                       "error: %s): %s", channel.game_username, e.args[0],
                       message)
            return

        channel.time_last_message = time.time()
        if not self.time_last_message:
            self.time_last_message = channel.time_last_message
        if not channel.is_moderator:
            self.sent_normal_message = True
        self.message_count += 1

    @asyncio.coroutine
    def beem_join_command(self, source, target_user):
        """`!<bot-name> join` chat command"""

        user_data = get_user_data("twitch", target_user)
        if not user_data:
            user_data = register_user("twitch", target_user)
        elif self.get_channel(target_user):
            yield from source.send_chat(
                "Already in chat of Twitch user {}".format(target_user))
            return

        entry = self.get_queue_entry(target_user)
        if entry:
            yield from source.send_chat(
                "Join request for Twitch user {} is already in the "
                "queue".format(target_user))
            return

        self.add_queue(target_user)
        yield from source.send_chat(
            "Join request added, {} will join Twitch chat of user "
            "{} soon".format(beem_conf.twitch["nick"], target_user))

    @asyncio.coroutine
    def beem_part_command(self, source, target_user):
        """`!<bot-name> part` chat command"""

        user_data = get_user_data("twitch", target_user)
        if not user_data:
            yield from source.send_chat(
                "Twitch user {} is not registered".format(target_user))
            return

        chan = self.get_channel(target_user)
        if not chan:
            yield from source.send_chat(
                "Not in chat of Twitch user {}".format(target_user))
            return

        self.stop_watching(chan)
        entry = self.get_queue_entry(target_user)
        if entry:
            entry["parted"] = True
        yield from source.send_chat(
            "Leaving Twitch chat of user {}".format(target_user))


def can_watch_user(user):
    if beem_conf.get("single_user"):
        return user == beem_conf.twitch["watch_user"]

    if not beem_conf.twitch.get("never_watch"):
        return True

    for u in beem_conf.twitch["never_watch"]:
        if u.lower() == user.lower():
            return False

    return True

def able_to_watch():
    return beem_conf.service_enabled("twitch") and dcss_manager.logged_in

# The Twitch manager instance created when the module is loaded.
twitch_manager = TwitchManager()

# Twitch service data
services["twitch"] = {
    "name"                : "Twitch",
    "manager"             : twitch_manager,
    "user_fields"         : ["nick"],
    "user_field_defaults" : [""],
    "commands"            : {
        "nick" : {
            "arg_pattern" : r"^[a-zA-Z0-9_-]+$",
            "arg_description" : "<nick>",
            "single_user" : True,
            "function" : beem_nick_command
        },
        "join" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : twitch_manager.beem_join_command
        },
        "part" : {
            "arg_pattern" : None,
            "arg_description" : None,
            "single_user" : False,
            "function" : twitch_manager.beem_part_command
        },
    }
}
