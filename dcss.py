import asyncio
import irc.client
import logging
import re
import time

import config

_conf = config.conf
_log = logging.getLogger()

# Max number of queries we can have waiting for a response before we refuse
# further queries. The limit is 10**_QUERY_ID_DIGITS.
_QUERY_ID_DIGITS = 2
# How long to wait for a query before ignoring any result from a bot and
# reusing the query ID.
_MAX_REQUEST_TIME = 80
# How long to wait after a connection failure before reattempting the
# connection.
_RECONNECT_TIMEOUT = 10

class dcss_manager():
    ## Can't depend on config.conf, as this isn't loaded yet.
    def __init__(self):
        self.logged_in = False
        self._reactor = irc.client.Reactor()
        self._reactor.add_global_handler("all_events", self._dispatcher, -10)
        self._server = self._reactor.server()

    @asyncio.coroutine
    def _connect(self):
        """Connect to DCSS irc."""

        self.logged_in = False
        self._messages = []
        self._queries = {}
        self._gretell_queue = []
        self._cheibriados_queue = []

        if _conf.dcss.get("fake_connect"):
            self.logged_in = True
            return

        if self._server.is_connected():
            self._server.disconnect()

        _log.info("DCSS: Connecting to IRC server %s using nick %s",
                  _conf.dcss["hostname"], _conf.dcss["nick"])
        self._server.connect(_conf.dcss["hostname"], _conf.dcss["port"],
                             _conf.dcss["nick"], None, None, _conf.dcss["nick"])

        if _conf.dcss.get("nickserv_password"):
            msg = "identify {}".format(_conf.dcss["nickserv_password"])
            try:
                yield from self._send("NickServ", msg)
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

        if _conf.dcss.get("fake_connect") or not self._server.is_connected():
            return

        try:
            self._server.disconnect()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Error when disconnecting: %s", err_reason)

    def is_connected(self):
        # Make sure _connect() is run at least once even under fake_connect
        if _conf.dcss.get("fake_connect") and self.logged_in:
            return True

        return self._server.is_connected()

    @asyncio.coroutine
    def start(self):
        _log.info("DCSS: Starting manager")
        while True:
            while not self.is_connected():
                try:
                    yield from self._connect()
                except:
                    yield from asyncio.sleep(_RECONNECT_TIMEOUT)


            try:
                self._reactor.process_once()
            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("DCSS: Error reading IRC connection: %s", err_reason)
                yield from self.disconnect()

            for m in list(self._messages):
                nick, message = m
                self._messages.remove(m)
                yield from self._read_irc(nick, message)

            ## XXX This seems needed to give other coroutines a chance to
            ## run. It may be needed until we can rework the irc connection to
            ## use asyncio's connections/streams.
            yield from asyncio.sleep(0.1)

    @asyncio.coroutine
    def _send(self, nick, message):
        _log.debug("DCSS: Sending message to %s: %s", nick, message)
        if _conf.dcss.get("fake_connect"):
            return

        self._server.privmsg(nick, message)

    def _dispatcher(self, connection, event):
        """
        Dispatch events to on_<event.type> method, if present.
        """

        if event.type == "privnotice":
            self._on_privnotice(event)
        elif event.type == "privmsg":
            self._on_privmsg(event)

    def _on_privnotice(self, event):
        message = event.arguments[0]
        if (not self.logged_in
            and _conf.dcss.get("nickserv_password")
            and event.source.find("NickServ!") == 0
            and message.lower().find("you are now identified") > -1):
            _log.info("DCSS: Identified by NickServ")
            self.logged_in = True

    def _on_privmsg(self, event):
        """
        Handle irc private message
        """

        if not self.logged_in:
            return

        message = re.sub(
            "\x1f|\x02|\x12|\x0f|\x16|\x03(?:\d{1,2}(?:,\d{1,2})?)?", "",
            event.arguments[0], flags=re.UNICODE)
        nick = re.sub(r"([^!]+)!.*", r"\1", event.source)
        self._messages.append((nick, message))

    def _make_query_id(self, source):
        new_id = None
        for i in range(0, 10 ** _QUERY_ID_DIGITS):
            if i not in self._queries:
                new_id = i
                break

            source_key, query_time = self._queries[i]
            if time.time() - query_time >= _MAX_REQUEST_TIME:
                new_id = i
                break

        if new_id is None:
            raise Exception("too many queries in queue")

        self._queries[new_id] = (source.get_source_key(), time.time())
        return new_id

    @asyncio.coroutine
    def _send_sequell_command(self, source, user, command):
        query_id = self._make_query_id(source)

        id_format = "{{:0{}}}".format(_QUERY_ID_DIGITS)
        prefix = id_format.format(query_id)
        # Hack to make $p get assigned to the player for Sequell purposes.
        message = re.sub(r"\$p(?=\W|$)|\$\{p\}",
                         source.get_nick(source.username), command)
        # Hack to make $chat get assigned to the |-separated list of chat users.
        # This won't work for twitch until we can track list of twitch chat
        # users and also map twitch users to crawl logins.
        spec_nicks = [source.get_nick(name) for name in source.spectators]
        message = re.sub(r"\$chat(?=\W|$)|\$\{chat\}", "|".join(spec_nicks),
                         message)
        yield from self._send(
            _conf.dcss["sequell_nick"],
            "!RELAY -nick {} -channel {} -prefix {} -n 1 {}".format(
                source.get_nick(user), source.irc_channel, prefix, message))

    @asyncio.coroutine
    def _send_gretell_command(self, source, command):
        query_id = self._make_query_id(source)
        self._gretell_queue.append(query_id)

        try:
            yield from self._send(_conf.dcss["gretell_nick"], command)
        except:
            self._gretell_queue.pop()
            raise

    @asyncio.coroutine
    def _send_cheibriados_command(self, source, command):
        query_id = self._make_query_id(source)
        self._cheibriados_queue.append(query_id)

        try:
            yield from self._send(_conf.dcss["cheibriados_nick"], command)
        except:
            self._cheibriados_queue.pop()
            raise

    def _get_dcss_source(self, nick, message):
        monster_pattern = r'Invalid|unknown|bad|[^()|]+ \(.\) \|'
        if nick == _conf.dcss["sequell_nick"]:
            match = re.match(r"^([0-9]{{{}}})".format(_QUERY_ID_DIGITS),
                             message)
            if not match:
                _log.warning("DCSS: Received Sequell message with invalid "
                             "prefix: %s", message)
                return

            query_id = int(match.group(1))

        elif nick == _conf.dcss["gretell_nick"]:
            ## Only accept what looks like the first part of a monster result
            if not re.match(monster_pattern, message):
                return

            if not len(self._gretell_queue):
                _log.error("DCSS: Received Gretell result but no request in "
                           "queue: %s", message)
                return

            query_id = self._gretell_queue.pop(0)

        elif nick == _conf.dcss["cheibriados_nick"]:
            ## Only accept what looks like the first part of a monster
            ## result or the first part of a git result.
            if (not re.match(monster_pattern, message)
                and not re.search(r"github\.com|^Could not", message)):
                _log.debug("DCSS: Ignoring unrecognized message from "
                           "Cheibriados: %s", message)
                return

            if not len(self._cheibriados_queue):
                _log.error("DCSS: Received Cheibriados result but no request in "
                           "queue: %s", message)
                return

            query_id = self._cheibriados_queue.pop(0)
        else:
            _log.warning("DCSS: Received message from unknown nick %s: %s",
                         nick, message)
            return

        try:
            source_key, query_time = self._queries[query_id]
            del self._queries[query_id]
        except KeyError:
            _log.warning("DCSS: Received %s message with unknown query id: "
                         "%s ", nick, message)
            return

        if time.time() - query_time >= _MAX_REQUEST_TIME:
            _log.debug("DCSS: Ignoring old %s message: %s", nick, message)
            return

        source_manager = config.services[source_key[0]]["manager"]
        source = source_manager.get_source_by_key(source_key)
        if not source:
            _log.warning("DCSS: Ignoring %s message with unknown source: %s",
                         nick, message)
        return source

    @asyncio.coroutine
    def _read_irc(self, nick, message):
        source = self._get_dcss_source(nick, message)
        if not source:
            return

        is_action = False
        if nick == _conf.dcss["sequell_nick"]:
            # Remove relay prefix
            message = re.sub(r"^[0-9]{{{}}}".format(_QUERY_ID_DIGITS), "",
                             message)
            # Handle any relays to other bots
            try:
                if _is_gretell_command(message):
                    command_type = "Gretell"
                    yield from self._send_gretell_command(source, message)
                    return

                elif _is_cheibriados_command(message):
                    command_type = "Cheibriados"
                    yield from self._send_cheibriados_command(source, message)
                    return

            except Exception as e:
                err_reason = type(e).__name__
                if e.args:
                    err_reason = e.args[0]
                _log.error("DCSS: Unable to relay %s command (service: %s, "
                           "chat: %s, error: %s): %s ", command_type,
                           config.services[source.service]["name"],
                           source.username, err_reason, message)
                raise

            # Sequell returns /me literally instead of using an IRC action, so
            # we do the dirty work here.
            if message.lower().startswith("/me "):
                is_action = True
                message = message[4:]

        elif (nick != _conf.dcss["gretell_nick"]
              and nick != _conf.dcss["cheibriados_nick"]):
            _log.info("DCSS: Ignoring message from %s: %s", nick, message)
            return

        yield from source.send_chat(message, is_action)

    @asyncio.coroutine
    def read_command(self, source, requester, message):
        command_type = None
        try:
            if _is_sequell_command(message):
                command_type = "Sequell"
                yield from self._send_sequell_command(source, requester,
                                                      message)
            elif _is_gretell_command(message):
                command_type = "Gretell"
                yield from self._send_gretell_command(source, message)
            elif _is_cheibriados_command(message):
                command_type = "Cheibriados"
                yield from self._send_cheibriados_command(source, message)
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Unable to send %s command (service: %s, "
                       "chat: %s, requester %s, error: %s): %s ", command_type,
                       config.services[source.service]["name"],
                       source.username, requester, err_reason, message)
        else:
            _log.info("DCSS: Sent %s command (service: %s, chat: %s, "
                      "requester: %s): %s", command_type,
                      config.services[source.service]["name"],
                      source.username, requester, message)

def is_dcss_command(message):
    return (_is_sequell_command(message)
            or _is_cheibriados_command(message)
            or _is_gretell_command(message))

def _is_sequell_command(message):
    for p in _conf.dcss["sequell_patterns"]:
        if re.match(p, message):
            return True
    return False

def _is_cheibriados_command(message):
    for p in _conf.dcss["cheibriados_patterns"]:
        if re.match(p, message):
            return True
    return False

def _is_gretell_command(message):
    for p in _conf.dcss["gretell_patterns"]:
        if re.match(p, message):
            return True
    return False

def _get_service_by_prefix(prefix):
    for service, data in config.services.items():
        if prefix == data["prefix"]:
            return service

manager = dcss_manager()
