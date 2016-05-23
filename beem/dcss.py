"""IRC connection management for DCSS knowledge bots"""

import asyncio
import irc.client
import logging
import re
import time

_log = logging.getLogger()

# Max number of queries we can have waiting for a response before we refuse
# further queries. The limit is 10 ** _QUERY_ID_DIGITS.
_QUERY_ID_DIGITS = 2
# How long to wait for a query before ignoring any result from a bot and
# reusing the query ID.
_MAX_REQUEST_TIME = 80
# How long to wait after a connection failure before reattempting the
# connection.
_RECONNECT_TIMEOUT = 5

class IRCBot():
    def __init__(self, manager, conf):
        self.manager = manager
        self.conf = conf
        self.queue = []

    def is_bot_message(self, message):
        """Is the given message intended for this bot? Check against its message
        patterns.

        """

        for p in self.conf["patterns"]:
            if re.match(p, message):
                return True
        return False

    @asyncio.coroutine
    def send_message(self, source, username, message):
        query_id = self.manager.make_query_id(source, username)

        if self.conf["use_relay"]:
            message = self.prepare_relay_message(source, username, query_id,
                                                 message)
        else:
            self.queue.append(query_id)

        try:
            yield from self.manager.send(self.conf["nick"], message)
        except:
            if not self.conf["use_relay"]:
                self.queue.pop()
            raise

    def prepare_relay_message(self, source, username, query_id, message):
        id_format = "{{:0{}}}".format(_QUERY_ID_DIGITS)
        prefix = id_format.format(query_id)
        # Hack to make $p get assigned to the player for Sequell purposes.
        message = re.sub(r"\$p(?=\W|$)|\$\{p\}",
                         source.get_nick(source.watch_username), message)
        # Hack to make $chat get assigned to the |-separated list of chat users.
        spec_nicks = [source.get_nick(name) for name in source.spectators]
        message = re.sub(r"\$chat(?=\W|$)|\$\{chat\}", "|".join(spec_nicks),
                         message)
        message = "!RELAY -nick {} -channel {} -prefix {} -n 1 {}".format(
            source.get_nick(username), source.irc_channel, prefix, message)
        return message

    def get_query_id(self, nick, message):
        monster_pattern = (r"Monster stats|Invalid|unknown|bad|[^()|]+ "
                           "\(.\) \|")
        git_pattern = r"github\.com|^Could not"
        query_id = None
        if self.conf["use_relay"]:
            match = re.match(r"^([0-9]{{{}}})".format(_QUERY_ID_DIGITS),
                             message)
            if not match:
                _log.warning("DCSS: Received %s message with invalid "
                             "prefix: %s", nick, message)
                return

            query_id = int(match.group(1))

        elif (self.conf["has_monster"] and re.match(monster_pattern, message)
              or self.conf["has_git"] and re.search(git_pattern, message)):
            if not len(self.queue):
                _log.error("DCSS: Received %s result but no request in "
                           "queue: %s", nick, message)
                return
            query_id = self.queue.pop(0)

        return query_id

class DCSSManager():
    """DCSS manager. Responsible for managing an IRC connection, sending queries
    to the knowledge bots and sending the results to the right source
    chat.

    """

    ## Can't depend on beem_conf, as this isn't loaded yet.
    def __init__(self, conf):
        self.conf = conf
        self.bots = {}
        for bot_conf in self.conf["bots"]:
            bot = IRCBot(self, bot_conf)
            self.bots[bot_conf["nick"]] = bot
        self.managers = {}

        self.logged_in = False
        self.reactor = irc.client.Reactor()
        self.reactor.add_global_handler("all_events", self.dispatcher, -10)
        self.server = self.reactor.server()

    @asyncio.coroutine
    def connect(self):
        """Connect to IRC."""

        self.logged_in = False
        self.messages = []
        self.queries = {}
        for bot_nick, bot in self.bots.items():
            bot.queue = []

        if self.conf.get("fake_connect"):
            self.logged_in = True
            return

        if self.server.is_connected():
            self.server.disconnect()

        _log.info("DCSS: Connecting to IRC server %s using nick %s",
                  self.conf["hostname"], self.conf["nick"])
        self.server.connect(self.conf["hostname"], self.conf["port"],
                            self.conf["nick"], None, None, self.conf["nick"])

        if self.conf.get("nickserv_password"):
            try:
                yield from self.send("NickServ", "identify {}".format(
                    self.conf["nickserv_password"]))
            except Exception as e:
                _log.error("DCSS: Unable to send auth to NickServ: %s",
                           e.args[0])
                yield from self.disconnect()
                raise
        else:
            self.logged_in = True

    def disconnect(self):
        """Disconnect IRC. This will log any disconnection error, but never raise.

        """

        if self.conf.get("fake_connect") or not self.server.is_connected():
            return

        try:
            self.server.disconnect()
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Error when disconnecting: %s", err_reason)

    def is_connected(self):
        # Make sure connect() is run at least once even under fake_connect
        if self.conf.get("fake_connect") and self.logged_in:
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
        if self.conf.get("fake_connect"):
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

    def make_query_id(self, source, username):
        new_id = None
        for i in range(0, 10 ** _QUERY_ID_DIGITS):
            if i not in self.queries:
                new_id = i
                break

            source_key, username, query_time = self.queries[i]
            if time.time() - query_time >= _MAX_REQUEST_TIME:
                new_id = i
                break

        if new_id is None:
            raise Exception("too many queries in queue")

        self.queries[new_id] = (source.get_source_key(), username, time.time())
        return new_id

    def get_query(self, nick, message):
        query_id = self.bots[nick].get_query_id(nick, message)
        if query_id is None:
            return

        try:
            source_key, username, query_time = self.queries[query_id]
            del self.queries[query_id]
        except KeyError:
            _log.warning("DCSS: Received %s message with unknown query id: "
                         "%s ", nick, message)
            return

        manager = self.managers[source_key[0]]
        source = manager.get_source_by_key(source_key)
        if not source:
            _log.warning("DCSS: Ignoring %s message with unknown source: %s",
                         nick, message)
            return

        return (source, username)

    @asyncio.coroutine
    def read_irc(self, nick, message):
        """Process an IRC message, forwarding any query results to the query
        source.

        """

        source_bot = self.bots.get(nick)
        if not source_bot:
            _log.warning("DCSS: Ignoring message from %s: %s", nick, message)
            return

        query = self.get_query(nick, message)
        if not query:
            return

        source, username = query
        is_action = False
        # Sequell can output queries for other bots.
        if source_bot.conf["use_relay"]:
            # Remove relay prefix
            message = re.sub(r"^[0-9]{{{}}}".format(_QUERY_ID_DIGITS), "",
                             message)
            dest_bot = None
            for dest_nick, bot in self.bots.items():
                if dest_nick == nick:
                    continue

                if bot.is_bot_message(message):
                    dest_bot = bot
                    break

            if dest_bot:
                try:
                    yield from dest_bot.send_message(source, username, message)
                    return

                except Exception as e:
                    err_reason = type(e).__name__
                    if e.args:
                        err_reason = e.args[0]
                    _log.error("DCSS: Unable to relay %s message (service: %s, "
                               "watch user: %s), message: %s, error: %s",
                               nick, source.manager.service,
                               source.watch_username, message, err_reason)
                    raise

            # Sequell returns /me literally instead of using an IRC action, so
            # we do the dirty work here.
            if message.lower().startswith("/me "):
                is_action = True
                message = message[4:]

        yield from source.send_chat(message, is_action)

    @asyncio.coroutine
    def read_message(self, source, username, message):
        dest_bot = None
        for bot_nick, bot in self.bots.items():
            if bot.is_bot_message(message):
                dest_bot = bot
                break

        if not dest_bot:
            raise Exception("Unknown bot message: {}".format(message))

        try:
            yield from dest_bot.send_message(source, username, message)
        except Exception as e:
            err_reason = type(e).__name__
            if e.args:
                err_reason = e.args[0]
            _log.error("DCSS: Unable to send %s command (watch user: %s, "
                       "request user: %s): command: %s, error: %s",
                       dest_bot.conf["nick"], source.watch_username, username,
                       message, err_reason)
        else:
            _log.info("DCSS: Sent %s command (watch user: %s, request user: "
                      "%s): %s", dest_bot.conf["nick"], source.watch_username,
                      username, message)

    def is_bad_pattern(self, message):
        if not self.conf.get("bad_patterns"):
            return False

        for pat in self.conf["bad_patterns"]:
            if re.search(pat, message):
                _log.debug("DCSS: Bad pattern message: %s", message)
                return True

    def is_dcss_message(self, message):
        if self.is_bad_pattern(message):
            return False

        for bot_nick, bot in self.bots.items():
            if bot.is_bot_message(message):
                return True

        return False
