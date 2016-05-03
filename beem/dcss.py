"""Defines the `dcss_manager` instance and functions for checking
messages for knowledge bot commands.

"""

import asyncio
import irc.client
import logging
import re
import time

from .config import beem_conf, services

_log = logging.getLogger()

# Max number of queries we can have waiting for a response before we refuse
# further queries. The limit is 10**_QUERY_ID_DIGITS.
_QUERY_ID_DIGITS = 2
# How long to wait for a query before ignoring any result from a bot and
# reusing the query ID.
_MAX_REQUEST_TIME = 80
# How long to wait after a connection failure before reattempting the
# connection.
_RECONNECT_TIMEOUT = 5

class DCSSManager():
    """DCSS manager. Responsible for managing the Freenode IRC connection,
    sending queries the knowledge bots and sending the results to the
    right source chat. There is one instance of this object available
    as `dcss.manager`.

    """

    ## Can't depend on beem_conf, as this isn't loaded yet.
    def __init__(self):
        self.logged_in = False
        self.reactor = irc.client.Reactor()
        self.reactor.add_global_handler("all_events", self.dispatcher, -10)
        self.server = self.reactor.server()

    @asyncio.coroutine
    def connect(self):
        """Connect to DCSS irc."""

        self.logged_in = False
        self.messages = []
        self.queries = {}
        self.gretell_queue = []
        self.cheibriados_queue = []

        if beem_conf.dcss.get("fake_connect"):
            self.logged_in = True
            return

        if self.server.is_connected():
            self.server.disconnect()

        _log.info("DCSS: Connecting to IRC server %s using nick %s",
                  beem_conf.dcss["hostname"], beem_conf.dcss["nick"])
        self.server.connect(beem_conf.dcss["hostname"], beem_conf.dcss["port"],
                            beem_conf.dcss["nick"], None, None,
                            beem_conf.dcss["nick"])

        if beem_conf.dcss.get("nickserv_password"):
            msg = "identify {}".format(beem_conf.dcss["nickserv_password"])
            try:
                yield from self.send("NickServ", msg)
            except Exception as e:
                _log.error("DCSS: Unable to send auth to NickServ: %s",
                           e.args[0])
                yield from self.disconnect()
                raise
        else:
            self.logged_in = True

    def disconnect(self):
        """Disconnect DCSS IRC. This will log any disconnection error, but never
        raise.

        """

        if (beem_conf.dcss.get("fake_connect")
            or not self.server.is_connected()):
            return

        try:
            self.server.disconnect()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Error when disconnecting: %s", err_reason)

    def is_connected(self):
        # Make sure _connect() is run at least once even under fake_connect
        if beem_conf.dcss.get("fake_connect") and self.logged_in:
            return True

        return self.server.is_connected()

    @asyncio.coroutine
    def start(self):
        _log.info("DCSS: Starting manager")
        while True:
            while not self.is_connected():
                try:
                    yield from self.connect()
                except:
                    yield from asyncio.sleep(_RECONNECT_TIMEOUT)


            try:
                self.reactor.process_once()
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("DCSS: Error reading IRC connection: %s", err_reason)
                yield from self.disconnect()

            for m in list(self.messages):
                nick, message = m
                self.messages.remove(m)
                yield from self.read_irc(nick, message)

            ## XXX This seems needed to give other coroutines a chance to
            ## run. It may be needed until we can rework the irc connection to
            ## use asyncio's connections/streams.
            yield from asyncio.sleep(0.1)

    @asyncio.coroutine
    def send(self, nick, message):
        _log.debug("DCSS: Sending message to %s: %s", nick, message)
        if beem_conf.dcss.get("fake_connect"):
            return

        self.server.privmsg(nick, message)

    def dispatcher(self, connection, event):
        """
        Dispatch events to on_<event.type> method, if present.
        """

        if event.type == "privnotice":
            self.on_privnotice(event)
        elif event.type == "privmsg":
            self.on_privmsg(event)

    def on_privnotice(self, event):
        message = event.arguments[0]
        if (not self.logged_in
            and beem_conf.dcss.get("nickserv_password")
            and event.source.find("NickServ!") == 0
            and message.lower().find("you are now identified") > -1):
            _log.info("DCSS: Identified by NickServ")
            self.logged_in = True

    def on_privmsg(self, event):
        """
        Handle irc private message
        """

        if not self.logged_in:
            return

        message = re.sub(
            "\x1f|\x02|\x12|\x0f|\x16|\x03(?:\d{1,2}(?:,\d{1,2})?)?", "",
            event.arguments[0], flags=re.UNICODE)
        nick = re.sub(r"([^!]+)!.*", r"\1", event.source)
        self.messages.append((nick, message))

    def make_query_id(self, source):
        new_id = None
        for i in range(0, 10 ** _QUERY_ID_DIGITS):
            if i not in self.queries:
                new_id = i
                break

            source_key, query_time = self.queries[i]
            if time.time() - query_time >= _MAX_REQUEST_TIME:
                new_id = i
                break

        if new_id is None:
            raise Exception("too many queries in queue")

        self.queries[new_id] = (source.get_source_key(), time.time())
        return new_id

    @asyncio.coroutine
    def send_sequell_command(self, source, user, command):
        query_id = self.make_query_id(source)

        id_format = "{{:0{}}}".format(_QUERY_ID_DIGITS)
        prefix = id_format.format(query_id)
        # Hack to make $p get assigned to the player for Sequell purposes.
        message = re.sub(r"\$p(?=\W|$)|\$\{p\}",
                         source.get_nick(source.game_username), command)
        # Hack to make $chat get assigned to the |-separated list of chat users.
        # This won't work for twitch until we can track list of twitch chat
        # users and also map twitch users to crawl logins.
        spec_nicks = [source.get_nick(name) for name in source.spectators]
        message = re.sub(r"\$chat(?=\W|$)|\$\{chat\}", "|".join(spec_nicks),
                         message)
        yield from self.send(
            beem_conf.dcss["sequell_nick"],
            "!RELAY -nick {} -channel {} -prefix {} -n 1 {}".format(
                source.get_nick(user), source.irc_channel, prefix, message))

    @asyncio.coroutine
    def send_gretell_command(self, source, command):
        query_id = self.make_query_id(source)
        self.gretell_queue.append(query_id)

        try:
            yield from self.send(beem_conf.dcss["gretell_nick"], command)
        except:
            self.gretell_queue.pop()
            raise

    @asyncio.coroutine
    def send_cheibriados_command(self, source, command):
        query_id = self.make_query_id(source)
        self.cheibriados_queue.append(query_id)

        try:
            yield from self.send(beem_conf.dcss["cheibriados_nick"], command)
        except:
            self.cheibriados_queue.pop()
            raise

    def get_dcss_source(self, nick, message):
        monster_pattern = r'Monster stats|Invalid|unknown|bad|[^()|]+ \(.\) \|'
        if nick == beem_conf.dcss["sequell_nick"]:
            match = re.match(r"^([0-9]{{{}}})".format(_QUERY_ID_DIGITS),
                             message)
            if not match:
                _log.warning("DCSS: Received Sequell message with invalid "
                             "prefix: %s", message)
                return

            query_id = int(match.group(1))

        elif nick == beem_conf.dcss["gretell_nick"]:
            ## Only accept what looks like the first part of a monster result
            if not re.match(monster_pattern, message):
                return

            if not len(self.gretell_queue):
                _log.error("DCSS: Received Gretell result but no request in "
                           "queue: %s", message)
                return

            query_id = self.gretell_queue.pop(0)

        elif nick == beem_conf.dcss["cheibriados_nick"]:
            ## Only accept what looks like the first part of a monster
            ## result or the first part of a git result.
            if (not re.match(monster_pattern, message)
                and not re.search(r"github\.com|^Could not", message)):
                _log.debug("DCSS: Ignoring unrecognized message from "
                           "Cheibriados: %s", message)
                return

            if not len(self.cheibriados_queue):
                _log.error("DCSS: Received Cheibriados result but no request in "
                           "queue: %s", message)
                return

            query_id = self.cheibriados_queue.pop(0)
        else:
            _log.warning("DCSS: Received message from unknown nick %s: %s",
                         nick, message)
            return

        try:
            source_key, query_time = self.queries[query_id]
            del self.queries[query_id]
        except KeyError:
            _log.warning("DCSS: Received %s message with unknown query id: "
                         "%s ", nick, message)
            return

        if time.time() - query_time >= _MAX_REQUEST_TIME:
            _log.debug("DCSS: Ignoring old %s message: %s", nick, message)
            return

        source_manager = services[source_key[0]]["manager"]
        source = source_manager.get_source_by_key(source_key)
        if not source:
            _log.warning("DCSS: Ignoring %s message with unknown source: %s",
                         nick, message)
        return source

    @asyncio.coroutine
    def read_irc(self, nick, message):
        source = self.get_dcss_source(nick, message)
        if not source:
            return

        is_action = False
        if nick == beem_conf.dcss["sequell_nick"]:
            # Remove relay prefix
            message = re.sub(r"^[0-9]{{{}}}".format(_QUERY_ID_DIGITS), "",
                             message)
            # Handle any relays to other bots
            try:
                if is_gretell_command(message):
                    command_type = "Gretell"
                    yield from self.send_gretell_command(source, message)
                    return

                elif is_cheibriados_command(message):
                    command_type = "Cheibriados"
                    yield from self.send_cheibriados_command(source, message)
                    return

            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("DCSS: Unable to relay %s command (service: %s, "
                           "chat: %s, error: %s): %s ", command_type,
                           services[source.service]["name"],
                           source.game_username, err_reason, message)
                raise

            # Sequell returns /me literally instead of using an IRC action, so
            # we do the dirty work here.
            if message.lower().startswith("/me "):
                is_action = True
                message = message[4:]

        elif (nick != beem_conf.dcss["gretell_nick"]
              and nick != beem_conf.dcss["cheibriados_nick"]):
            _log.info("DCSS: Ignoring message from %s: %s", nick, message)
            return

        yield from source.send_chat(message, is_action)

    @asyncio.coroutine
    def read_command(self, source, requester, message):
        command_type = None
        try:
            if is_sequell_command(message):
                command_type = "Sequell"
                yield from self.send_sequell_command(source, requester,
                                                      message)
            elif is_gretell_command(message):
                command_type = "Gretell"
                yield from self.send_gretell_command(source, message)
            elif is_cheibriados_command(message):
                command_type = "Cheibriados"
                yield from self.send_cheibriados_command(source, message)
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Unable to send %s command (service: %s, "
                       "chat: %s, requester %s, error: %s): %s ", command_type,
                       services[source.service]["name"],
                       source.game_username, requester, err_reason, message)
        else:
            _log.info("DCSS: Sent %s command (service: %s, chat: %s, "
                      "requester: %s): %s", command_type,
                      services[source.service]["name"],
                      source.game_username, requester, message)

            
def is_dcss_command(message):
    return (is_sequell_command(message)
            or is_cheibriados_command(message)
            or is_gretell_command(message))

def is_sequell_command(message):
    for p in beem_conf.dcss["sequell_patterns"]:
        if re.match(p, message):
            return True
    return False

def is_cheibriados_command(message):
    for p in beem_conf.dcss["cheibriados_patterns"]:
        if re.match(p, message):
            return True
    return False

def is_gretell_command(message):
    for p in beem_conf.dcss["gretell_patterns"]:
        if re.match(p, message):
            return True
    return False

def get_service_by_prefix(prefix):
    for service, data in services.items():
        if prefix == data["prefix"]:
            return service

# The single dcss manager instance available to other modules.
dcss_manager = DCSSManager()
