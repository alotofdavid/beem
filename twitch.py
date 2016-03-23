import asyncio
import irc.client
import logging
import os
import re
import sqlite3
import time

import beem
import chat
import config
import dcss

_conf = config.conf
_log = logging.getLogger()

class twitch_channel(chat.chat_listener):
    def __init__(self, slot_num, username):
        super().__init__()
        self.username = username
        self.slot_num = slot_num
        self.service_name = "twitch"
        self.irc_channel = "#" + username
        self.bot_name = _conf.twitch["nick"]
        self._message_count = 0
        self.time_last_message = time.time()
        self.is_moderator = False

    def get_source_key(self):
        return self.username

    @asyncio.coroutine
    def send_chat(self, message, is_action=False):
        ## These are interpreted by the Twitch irc server, so prepend a
        ## space, which will get removed by the server anyhow.
        if message[0] == "." or message[0] == "/":
            message = " " + message
        elif not is_action and chat.is_bot_command(message):
            message = "]" + message

        manager.send_channel(self, message, is_action)


class twitch_manager():
    ## Can't depend on config.conf or beem.server, as these isn't loaded yet.
    def __init__(self):
        self.messages = None
        self._message_count = 0
        self._channels = set()
        self._listen_queue = []
        self._time_last_message = None
        self._reactor = irc.client.Reactor()
        self._reactor.add_global_handler("all_events", self._dispatcher, -10)
        self._server = self._reactor.server()

    @asyncio.coroutine
    def _connect(self):
        """Connect to Twitch IRC"""

        if _conf.twitch.get("fake_connect"):
            return

        if self._server.is_connected():
            self._server.disconnect()

        _log.info("Twitch: Connecting to IRC server %s using nick %s",
                  _conf.twitch["hostname"], _conf.twitch["nick"])
        self._server.connect(_conf.twitch["hostname"], _conf.twitch["port"],
                             _conf.twitch["nick"], _conf.twitch["password"],
                             None, _conf.twitch["nick"])
        # To get JOIN/PART/USER data
        self._server.cap("REQ", ":twitch.tv/membership")

    def is_connected(self):
        if _conf.twitch.get("fake_connect"):
            return True

        return self._server.is_connected()

    def _timeout_finished(self):
        timeout = _conf.twitch["message_timeout"]
        return (self._time_last_message
                and time.time() - self._time_last_message > timeout)

    def get_source_by_key(self, source_key):
        return self.get_channel(source_key)

    @asyncio.coroutine
    def start(self):
        self._messages = []
        self._listen_queue = []
        _log.info("Twitch: Starting manager")
        while True:
            while not self.is_connected():
                try:
                    yield from self._connect()
                except irc.client.IRCError as e:
                    _log.error("Twitch: IRC error when connecting: {0}".format(
                        e.args[0]))
                    yield from asyncio.sleep(_conf.reconnect_timeout)

            if self._timeout_finished():
                self._message_count = 0
                self._time_last_message = None
                self._sent_normal_message = False

            try:
                self._reactor.process_once()
            except irc.client.IRCError as e:
                _log.error("Twitch: IRC error when reading: {0}".format(
                    e.args[0]))

            yield from self._update_queue()

            ## Handle any incoming messages, sending them to the appropriate
            ## channel listener.
            for m in list(self._messages):
                username, sender, message = m
                self._messages.remove(m)
                chan = self.get_channel(username)
                if not chan:
                    _log.warning("Twitch: Can't find channel for user: %s",
                                 username)
                    continue

                yield from chan.read_chat(sender, message)

            ## This seems needed to give other coroutines a chance to run.
            yield from asyncio.sleep(0.1)

    def stop(self):
        if not _conf.twitch.get("fake_connect") and self._server.is_connected():
            self._server.disconnect()

        self._messages = []
        self._channels = set()
        self._listen_queue = []

    def _dispatcher(self, connection, event):
        """
        Dispatch events to on_<event.type> method, if present.
        """

        if event.type == "pubmsg":
            self._on_pubmsg(event)

    def _on_pubmsg(self, event):
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
        self._messages.append((username, sender, message))

    def _add_queue(self, username):
        entry = {"username"     : username,
                 "parted"       : False,
                 "time_request" : None}
        self._listen_queue.append(entry)

    def remove_queue(self, username):
        for entry in list(self._listen_queue):
            if entry["username"] == username:
                self._listen_queue.remove(entry)
                return

    def _get_queue_entry(self, username):
        for entry in self._listen_queue:
            if entry["username"] == username:
                return entry

        return

    def get_channel(self, username):
        for chan in self._channels:
            if chan.username == username:
                return chan

        return

    @asyncio.coroutine
    def _stop_listening(self, channel):
        try:
            if self._message_limited(True):
                raise StandardError("Reached message limit")

            self._sent_normal_message = True
            if _conf.twitch.get("fake_connect"):
                return

            self._server.part(channel.irc_channel)

        except Exception as e:
            _log.error("Twitch: Unable to sending part message to %s: %s",
                       channel.irc_channel, e.args[0])
            raise

        else:
            _log.info("Twitch: Leaving channel of user %s", channel.username)

        finally:
            self._channels.remove(channel)

    @asyncio.coroutine
    def _new_channel(self, username):
        slot_nums = list(range(0, _conf.twitch["max_listened_subscribers"]))
        idle_chan = None
        max_idle = -1
        current_time = time.time()
        for chan in self._channels:
            if chan.slot_num in slot_nums:
                slot_nums.remove(chan.slot_num)
            idle_time = current_time - chan.time_last_message
            if idle_time >= _conf.twitch["min_idle"] and idle_time >= max_idle:
                idle_chan = chan

        if len(slot_nums):
            chosen_slot = min(slot_nums)
        elif idle_chan:
            try:
                yield from self._stop_listening(idle_chan)
            except:
                return
            chosen_slot = idle_chan.slot_num
        else:
            return

        chan = twitch_channel(chosen_slot, username)
        try:
            self._join_channel(chan)
        except:
            return

        _log.info("Twitch: Joining channel of user %s", username)
        self._channels.add(chan)
        return chan

    @asyncio.coroutine
    def _update_queue(self):
        twconf = _conf.twitch
        expire_time = twconf["request_expire_time"]
        max_idle = twconf["max_chat_idle"]
        # Update the subscriber listen queue, joining/parting channels as
        # necessary.
        able = _able_to_listen()
        for entry in list(self._listen_queue):
            chan = self.get_channel(entry["username"])
            allowed = _can_listen_user(entry["username"])
            expired = (entry["time_request"]
                       and time.time() - entry["time_request"] >= expire_time)
            if (chan
                and (not able or not allowed or entry["parted"] or expired)):
                try:
                    yield from self._stop_listening(chan)
                # If there's an error parting, leave the entry to the next
                # update.
                except:
                    continue

            if not allowed or entry["parted"] or expired:
                self._listen_queue.remove(entry)
                continue

            if chan:
                continue

            yield from self._new_channel(entry["username"])

    def _message_limited(self, is_normal):
        if is_normal or self._sent_normal_message:
            limit = _conf.twitch["message_limit"]
        else:
            limit = _conf.twitch["moderator_message_limit"]
        return self._message_count >= limit

    def _join_channel(self, channel):
        if self._message_limited(True):
            msg = ("Twitch: Didn't send join message for user {} due to "
                   "message limit".format(channel.username))
            _log.info(msg)
            raise StandardError(msg)

        self._sent_normal_message = True
        if _conf.twitch.get("fake_connect"):
            return

        try:
            self._server.join(channel.irc_channel)
        except irc.client.IRCError as e:
            _log.error("Twitch: Unable to send join message for user %s: %s",
                       channel.username, e.args[0])

    def send_channel(self, channel, message, is_action=False):

        if self._message_limited(not channel.is_moderator):
            _log.info("Twitch: Didn't send chat message for channel %s due to "
                      "message limit", )
            return

        if _conf.twitch.get("fake_connect"):
            send_func = lambda channel, message: True
        elif is_action:
            send_func = self._server.action
        else:
            send_func = self._server.privmsg

        try:
            send_func(channel.irc_channel, message)
        except irc.client.IRCError as e:
            _log.error("Twitch: Unable to send chat message: %s", e.args[0])
            return

        channel.time_last_message = time.time()
        if not self._time_last_message:
            self._time_last_message = channel.time_last_message
        if not channel.is_moderator:
            self._sent_normal_message = True
        self._message_count += 1

    @asyncio.coroutine
    def beem_command(self, source, sender, user, command, args):
        twconf = _conf.twitch
        bot_name = twconf["nick"]

        target_user = user
        if source.service_name == "webtiles":
            webtiles_data = config.get_user_data("webtiles", user)
            if not webtiles_data:
                yield from source.send_chat(
                    "WebTiles user {} is not registered.".format(user))
                return

            target_user = webtiles_data["twitch_user"]
            if not target_user:
                yield from source.send_chat(
                    "You must have an admin link to your WebTiles username to "
                    "your Twitch username")
                return

        user_data = config.get_user_data("twitch", target_user)
        if command == "nick":
            if not args:
                if not user_data or not user_data["nick"]:
                    yield from source.send_chat(
                        "No nick for Twitch user {}".format(target_user))
                else:
                    yield from source.send_chat(
                        "Nick for Twitch user {}: {}".format(
                            target_user, user_data["nick"]))
                return

            if not user_data:
                try:
                    config.register_user(source, sender, "twitch", target_user)
                except:
                    yield from source.send_chat("Error when registering")
                    return

            try:
                if user_data["nick"] != args[0]:
                    config.set_user_field(source, sender, "twitch",
                                          target_user, "nick", args[0])
            except:
                yield from source.send_chat("Error when setting nick")
                return

            yield from source.send_chat(
                "Nick for Twitch user {} set to {}".format(
                    target_user, args[0]))
            return

        if command == "join":
            if self.get_channel(target_user):
                yield from source.send_chat(
                    "Already in chat of Twitch user {}".format(target_user))
                return

            if user_data:
                entry = self._get_queue_entry(target_user)
                if entry:
                    yield from source.send_chat(
                        "Join request for Twitch user {} is already in the "
                        "queue".format(target_user))
                else:
                    self._add_queue(target_user)
                    yield from source.send_chat(
                        "Join request added, {} will join Twitch chat of user "
                        "{} soon".format(bot_name, target_user))
                return

            else:
                yield from source.send_chat(
                    "You must first register with {} from Twitch chat using "
                    "the following command: {} register".format(bot_name))
            return

        if command == "part":
            chan = self.get_channel(target_user)
            if not chan:
                yield from source.send_chat(
                    "Not in chat of user {}".format(target_user))
            else:
                try:
                    yield from self._stop_listening(chan)
                except:
                    yield from source.send_chat(
                        "Error when trying to part Twitch chat")
                    return

                finally:
                    entry = self._get_queue_entry(target_user)
                    if entry:
                        entry["parted"] = True
                # Don't send a message to the channel we've just parted.
                if (source.service_name != "twitch"
                    or source.username != target_user):
                    yield from source.send_chat(
                        "Leaving Twitch chat of user {}".format(target_user))
            return

def _can_listen_user(user):
    if _conf.get("single_user"):
        return user == _conf.twitch["listen_user"]

    if not _conf.twitch.get("never_listen"):
        return True

    for u in _conf.twitch["never_listen"]:
        if u.lower() == user.lower():
            return False

    return True

def _able_to_listen():
    """Are we presently able to listen to any game?"""

    # Don't listen to games if dcss irc isn't ready.
    return _conf.service_enabled("twitch") and dcss.manager.logged_in


manager = twitch_manager()
config.register_service("twitch", "Twitch", "t", manager)
