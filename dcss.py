import asyncio
import irc.client
import logging
import re

import beem
import config

_conf = config.conf
_log = logging.getLogger()

class dcss_manager():
    ## Can't depend on config.conf, as this isn't loaded yet.
    def __init__(self):
        self.logged_in = False
        self._reactor = irc.client.Reactor()
        self._reactor.add_global_handler("all_events", self._dispatcher, -10)
        self._server = self._reactor.server()
        self._slot_map = {}
        for service in config.service_data:
            self._slot_map[service] = {}

    @asyncio.coroutine
    def _connect(self):
        """Connect to DCSS irc."""

        self._messages = []
        self.logged_in = False
        self._gretell_sources = []
        self._cheibriados_sources = []

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
                if self._server.is_connected():
                    self._server.disconnect()
                return

    def stop(self):
        if not _conf.dcss.get("fake_connect") and self._server.is_connected():
            self._server.disconnect()

        self._messages = []
        self.logged_in = False
        self._gretell_sources = []
        self._cheibriados_sources = []

    def is_connected(self):
        if _conf.dcss.get("fake_connect"):
            return self.logged_in
        else:
            return self._server.is_connected()

    @asyncio.coroutine
    def start(self):
        _log.info("DCSS: Starting manager")
        while True:
            while not self.is_connected():
                try:
                    yield from self._connect()
                except:
                    yield from asyncio.sleep(_conf["reconnect_timeout"])

            try:
                self._reactor.process_once()
            except Exception as e:
                _log.error("DCSS: Error reading IRC connection:: %s", e.args[0])

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
        is_action = False
        nick = re.sub(r"([^!]+)!.*", r"\1", event.source)
        self._messages.append((nick, message))

    def _update_slot_map(self, source):
        source_key = source.get_source_key()
        self._slot_map[source.service_name][source.slot_num] = source_key

    @asyncio.coroutine
    def _send_sequell_command(self, source, user, command):
        service = source.service_name
        self._update_slot_map(source)
        prefix = "{}{}:".format(config.service_data[service]["prefix"],
                                source.slot_num)
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
        service = source.service_name
        self._update_slot_map(source)
        self._gretell_sources.append((service, source.slot_num))

        try:
            yield from self._send(_conf.dcss["gretell_nick"], command)
        except:
            self._gretell_queue.pop()
            raise

    @asyncio.coroutine
    def _send_cheibriados_command(self, source, command):
        service = source.service_name
        self._update_slot_map(source)
        self._cheibriados_sources.append((source.service_name, source.slot_num))

        try:
            yield from self._send(_conf.dcss["cheibriados_nick"], command)
        except:
            self._cheibriados_queue.pop()
            raise

    def _get_dcss_source(self, nick, message):
        monster_pattern = r'Invalid|unknown|bad|[^()|]+ \(.\) \|'
        if nick == _conf.dcss["sequell_nick"]:
            match = re.match(r"^([a-z])([0-9]+):", message)
            prefix_msg = ("DCSS: Received Sequell message with invalid prefix: "
                          "{}".format(message))
            if not match:
                _log.warning("DCSS: Received Sequell message with invalid "
                             "prefix: {}".format(message))
                return

            slot_num = int(match.group(2))
            service = _get_service_by_prefix(match.group(1))
            if not service:
                _log.error(prefix_msg)
                return

        elif nick == _conf.dcss["gretell_nick"]:
            ## Only accept what looks like the first part of a monster result
            if not re.match(monster_pattern, message):
                return

            if not len(self._gretell_sources):
                _log.error("DCSS: Received Gretell result but no request in "
                           "queue: %s", message)
                return

            service, slot_num = self._gretell_sources.pop(0)

        elif nick == _conf.dcss["cheibriados_nick"]:
            ## Only accept what looks like the first part of a monster
            ## result or the first part of a git result.
            if (not re.match(monster_pattern, message)
                and not re.match(r"github\.com|Could not", message)):
                _log.debug("DCSS: Ignored unrecognized message from "
                           "Cheibriados: %s", message)
                return

            if not len(self._cheibriados_sources):
                _log.error("DCSS: Received Cheibriados result but no request in "
                           "queue: %s", message)
                return

            service, slot_num = self._cheibriados_sources.pop(0)
        else:
            _log.warning("DCSS: Received message from unknown nick %s: %s",
                         nick, message)
            return

        try:
            source_key = self._slot_map[service][slot_num]
        except KeyError:
            _log.warning("DCSS: Received %s message with unknown slot number "
                         "in prefix: %s", nick, message)
            return

        source_manager = config.service_data[service]["manager"]
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
            message = re.sub(r"^[a-z][0-9]+:", "", message)
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
                _log.error("DCSS: Unable to relay %s command (service: %s, "
                           "chat: %s, error: %s): %s ",
                           command_type,
                           config.service_data[source.service_name]["desc"],
                           source.username, e.args[0], message)
                return

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
            _log.error("DCSS: Unable to send %s command (service: %s, "
                       "chat: %s, requester %s, error: %s): %s ", command_type,
                       config.service_data[source.service_name]["desc"],
                       source.username, requester, e.args[0], message)
        else:
            _log.info("DCSS: Sent %s command (service: %s, chat: %s, "
                      "requester: %s): %s",
                      config.service_data[source.service_name]["desc"],
                      command_type, source.username, requester, message)

def _is_bad_pattern(command):
    if not _conf.dcss.get("bad_patterns"):
        return False

    for bp in _conf.dcss["bad_patterns"]:
        if re.search(bp, command):
            return True
    return False

def is_dcss_command(message):
    if _is_bad_pattern(message):
        _log.debug("DCSS: Bad pattern message: %s", message)
        return False

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
    for service, data in config.service_data.items():
        if prefix == data["prefix"]:
            return service

manager = dcss_manager()
# Currently unused.
config.register_service("irc", "Freenode", "i", manager)
