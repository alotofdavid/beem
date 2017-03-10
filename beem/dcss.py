"""IRC connection management for DCSS knowledge bots"""

import asyncio
if hasattr(asyncio, "async"):
    ensure_future = asyncio.async
else:
    ensure_future = asyncio.ensure_future

import base64
import irc.client
import irc.functools as irc_functools
import logging
import os
import signal
import re
import ssl
import time

_log = logging.getLogger()

# Max number of queries we can have waiting for a response before we refuse
# further queries. The limit is 10 ** _QUERY_ID_DIGITS.
_QUERY_ID_DIGITS = 2
# How long to wait in second for a query before ignoring any result from a bot
# and reusing the query ID. Sequell times out after 90s, so we use 100s.
_MAX_REQUEST_TIME = 100
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
            if re.search(p, message):
                return True

        return False

    @asyncio.coroutine
    def send_message(self, source, user, message):
        query_id = self.manager.make_query_id(source, user)

        if self.conf["has_sequell"]:
            message = self.prepare_sequell_message(source, user, query_id,
                    message)
        else:
            self.queue.append(query_id)

        try:
            yield from self.manager.send(self.conf["nick"], message)
        except:
            if not self.conf["has_sequell"]:
                self.queue.pop()
            raise

    def prepare_sequell_message(self, source, sender, query_id, message):
        id_format = "{{:0{}}}".format(_QUERY_ID_DIGITS)
        prefix = id_format.format(query_id)

        # Hack to make $p get assigned to the player for Sequell purposes.
        if source.user:
            source_nick = source.get_dcss_nick(source.user)
            message = re.sub(r"\$p(?=\W|$)|\$\{p\}", source_nick, message)

        # Hack to make $chat get assigned to the |-separated list of chat
        # nicks.
        chat_nicks = source.get_chat_dcss_nicks(sender)
        if chat_nicks:
            message = re.sub(r"\$chat(?=\W|$)|\$\{chat\}",
                    "@" + "|@".join(chat_nicks), message)

        sender_nick = source.get_dcss_nick(sender)
        message = "!RELAY -nick {} -prefix {} -n 1 {}".format(sender_nick,
                prefix, message)
        return message

    def get_result_ident(self, message):
        ident = {}
        if self.conf["has_sequell"]:
            match = re.match(r"^([0-9]{{{}}})".format(_QUERY_ID_DIGITS),
                             message)
            if not match:
                _log.warning("DCSS: Received %s message with invalid "
                             "relay prefix: %s", self.conf["nick"], message)
                return

            ident["query_id"] = int(match.group(1))
            ident["type"] = "sequell"
            return ident

        if not len(self.queue):
            return

        # First part of a monster query result.
        monster_pattern = (r"Monster stats|Invalid|unknown|bad|[^()|]+ "
                           "\(.\) \|")
        # First part of a %git query result.
        git_pattern = r"github\.com|^Could not"
        if self.conf["has_monster"] and re.match(monster_pattern, message):
            ident["query_id"] = self.queue.pop(0)
            ident["type"] = "monster"

        elif self.conf["has_git"] and re.search(git_pattern, message):
            ident["query_id"] = self.queue.pop(0)
            ident["type"] = "git"

        return ident


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

        self.reactor = Reactor()
        self.reactor.add_global_handler("all_events", self.dispatcher, -10)
        self.server = self.reactor.server()

    def log_exception(self, e, error_msg):
        error_reason = type(e).__name__
        if e.args:
            error_reason = "{}: {}".format(error_reason, e.args[0])
        _log.error("DCSS: %s: %s", error_msg, error_reason)

    def ready(self):
        if not self.server.is_connected():
            return False

        if self.conf.get("password"):
            return self.server.authenticated

        return True

    @asyncio.coroutine
    def connect(self):
        """Connect to IRC."""

        assert not self.server.is_connected()

        self.messages = []
        self.queries = {}
        self.last_result_ident = None
        self.last_result_query = None
        for bot_nick, bot in self.bots.items():
            bot.queue = []

        if self.conf.get("fake_connect"):
            self.server.authenticated = True
            return

        if self.conf.get("use_ssl"):
            factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
        else:
            factory = irc.connection.Factory()

        _log.info("DCSS: Connecting to IRC server %s port %d using nick %s",
                  self.conf["hostname"], self.conf["port"], self.conf["nick"])
        self.server.connect(self.conf["hostname"], self.conf["port"],
                            self.conf["nick"],
                            username=self.conf.get("username"),
                            password=self.conf.get("password"),
                            connect_factory=factory)

    def disconnect(self):
        """Disconnect IRC. This will log any disconnection error, but never
        raise.

        """

        if self.conf.get("fake_connect") or not self.server.is_connected():
            return

        try:
            self.server.disconnect()
        except Exception as e:
            self.log_exception(e, "Error when disconnecting IRC")

    def is_connected(self):
        # We check server.authenticated to make sure connect() is called once
        # when fake_connect is true.
        if self.conf.get("fake_connect"):
            result = False
            try:
                return self.server.authenticated
            except:
                return False

        return self.server.is_connected()

    @asyncio.coroutine
    def start(self):
        _log.info("DCSS: Starting manager")
        while True:
            tried_connect = False
            while not self.is_connected():
                if tried_connect:
                    yield from asyncio.sleep(_RECONNECT_TIMEOUT)

                try:
                    yield from self.connect()
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    self.log_exception(e, "Unable to connect IRC")

                tried_connect = True

            try:
                self.reactor.process_once()
            except Exception as e:
                self.log_exception(e, "Error reading IRC connection")
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

        if event.type == "privmsg":
            self.on_privmsg(event)
            return

        # SASL-related message handling from here on.
        if not self.conf.get("password"):
            return

        elif event.type == "900":
            self.on_900_message(event)
        elif event.type == "904":
            self.on_904_message(event)

    def on_900_message(self, event):
        _log.info("DCSS: SASL authentication complete")

    def on_904_message(self, event):
        _log.critical("DCSS: SASL authentication failed, shutting down")
        os.kill(os.getpid(), signal.SIGTERM)

    def on_privmsg(self, event):
        """
        Handle irc private message
        """

        if not self.ready():
            return

        message = re.sub(
            "\x1f|\x02|\x12|\x0f|\x16|\x03(?:\d{1,2}(?:,\d{1,2})?)?", "",
            event.arguments[0], flags=re.UNICODE)
        nick = re.sub(r"([^!]+)!.*", r"\1", event.source)
        self.messages.append((nick, message))

    def make_query_id(self, source, user):
        new_id = None
        current_time = time.time()
        last_query_age = None
        if self.last_result_query:
            last_query_age = current_time - self.last_result_query["time"]
        if last_query_age and last_query_age >= _MAX_REQUEST_TIME:
            self.last_result_query = None
            self.last_result_ident = None

        for i in range(0, 10 ** _QUERY_ID_DIGITS):
            if i in self.queries:
                if current_time - self.queries[i]["time"] >= _MAX_REQUEST_TIME:
                    new_id = i
                    break

            if (not self.last_result_ident
                or i != self.last_result_ident["query_id"]):
                new_id = i
                break

        if new_id is None:
            raise Exception("too many queries in queue")

        self.queries[new_id] = {"source_ident" : source.get_source_ident(),
                                "requester" : user,
                                "time" : current_time}
        return new_id

    def get_result_ident(self, nick, message):
        """Get the result details from the message. If the bot's handler
        doesn't have result details, assume that the details are the same as
        the last returned result."""
        result_ident = self.bots[nick].get_result_ident(message)
        if result_ident is None:
            result_ident = self.last_result_ident
            if result_ident is None:
                _log.warning("DCSS: Unable to find query for %s result: %s",
                        nick, message)
                return

        return result_ident

    def get_query_result(self, nick, message):
        """Find the query details in our queue based on the query ID for the
        result determined by the bot handler."""
        ident = self.get_result_ident(nick, message)
        if not ident:
            return

        if (self.last_result_ident
            and self.last_result_ident["query_id"] is ident["query_id"]):
            return (self.last_result_query, self.last_result_ident)

        if not ident["query_id"] in self.queries:
            return

        query = self.queries[ident["query_id"]]
        del self.queries[ident["query_id"]]
        self.last_result_query = query
        self.last_result_ident = ident
        return query, ident

    @asyncio.coroutine
    def read_irc(self, nick, message):
        """Process an IRC message, forwarding any query results to the query
        source.

        """

        if not self.bots.get(nick):
            _log.warning("DCSS: Ignoring message from %s: %s", nick, message)
            return

        result = self.get_query_result(nick, message)
        if not result:
            return

        query, result_ident = result
        manager = self.managers[query["source_ident"]["service"]]
        source = manager.get_source_by_ident(query["source_ident"])
        if not source:
            _log.warning("DCSS: Ignoring %s message with unknown source: %s",
                         nick, message)
            return

        message_type = "normal"
        # Sequell can output queries for other bots.
        if result_ident["type"] == "sequell":
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
                    yield from dest_bot.send_message(source,
                            query["requester"], message)
                    return

                except Exception as e:
                    self.log_exception(e, "Unable to relay to {} from {} "
                            "on behalf of {} (message: {})".format(nick,
                                source.describe(), query["requester"], message))
                    raise

            # Sequell returns /me literally instead of using an IRC action, so
            # we do the dirty work here.
            if message.lower().startswith("/me "):
                message_type = "action"
                message = message[4:]
        else:
            message_type = result_ident["type"]

        yield from source.send_chat(message, message_type)

    @asyncio.coroutine
    def read_message(self, source, user, message):
        dest_bot = None
        for bot_nick, bot in self.bots.items():
            if bot.is_bot_message(message):
                dest_bot = bot
                break

        if not dest_bot:
            raise Exception("Unknown bot message: {}".format(message))

        try:
            yield from dest_bot.send_message(source, user, message)
        except Exception as e:
            self.log_exception(e, "Unable to send message from {} to {} "
                    "(requester: {}, message: {})".format(source.describe(),
                        dest_bot.conf["nick"], user, message))

        else:
            _log.debug("DCSS: Sent %s message (source: %s, requester: %s): %s",
                    dest_bot.conf["nick"], source.describe(), user, message)

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

class ServerConnection(irc.client.ServerConnection):
    """The ServerConnection class from irc.client, modified to send a differently
    formatted USER command, to support automatic capability requests, and to
    support SASL authentication. Once SASL authentication is complete, the
    authenticated property will be True.

    """

    # save the method args to allow for easier reconnection.
    @irc_functools.save_method_args
    def connect(self, server, port, nickname, username=None, password=None,
                ircname=None, capabilities=[],
                connect_factory=irc.connection.Factory()):
        """Connect/reconnect to a server.

        Arguments:

        * server - Server name
        * port - Port number
        * nickname - The nickname
        * username - The username
        * password - Password, which is used for SASL authentication
        * ircname - The IRC name ("realname")
        * capabilities - A list of strings of capabilities to request from the
                         server. The sasl capability is automatically added
                         if password is defined.
        * connect_factory - A callable that takes the server address and
          returns a connection (with a socket interface)

        This function can be called to reconnect a closed connection.

        Returns the ServerConnection object.

        """
        _log.debug("connect(server=%r, port=%r, nickname=%r, ...)", server,
            port, nickname)

        if self.connected:
            self.disconnect("Changing servers")

        self.buffer = self.buffer_class()
        self.handlers = {}
        self.real_server_name = ""
        self.real_nickname = nickname
        self.server = server
        self.port = port
        self.server_address = (server, port)
        self.nickname = nickname
        self.username = username or nickname
        self.ircname = ircname or nickname
        self.password = password
        self.authenticated = False
        self.capabilities = capabilities
        self.connect_factory = connect_factory
        try:
            self.socket = self.connect_factory(self.server_address)
        except socket.error as ex:
            raise irc.client.ServerConnectionError(
                "Couldn't connect to socket: %s" % ex)
        self.connected = True
        self.reactor._on_connect(self.socket)

        # Need SASL capability if we're using a password.
        if self.password and "sasl" not in self.capabilities:
            self.capabilities.append("sasl")

        if self.capabilities:
            self.cap("REQ", *self.capabilities)

        self.nick(self.nickname)
        self.user(self.username, self.ircname)
        return self

    def user(self, username, realname):
        """Send a USER command. This form is slightly modified from the USER
        command sent in the original irc.client.ServerConnection

        """

        self.send_raw("USER {0} {0} {1} :{2}".format(username, self.server,
                                                     realname))

    def authenticate(self, request_method=False):
        """AUTHENTICATE command. If request_method is True, request PLAIN
        authentication, which is the only type we support. Otherwise send the
        authentication credentials.

        """

        if request_method:
            self.send_raw("AUTHENTICATE PLAIN")
            return

        authdata = base64.b64encode("{0}\x00{0}\x00{1}".format(
            self.username, self.password).encode())

        self.send_raw("AUTHENTICATE {}".format(authdata.decode()))


class Reactor(irc.client.Reactor):
    """The Reactor class from irc.client that uses our modified ServerConnection
    class and coordinates capabilities and SASL requests.

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.add_global_handler("cap", self.handle_cap)
        self.add_global_handler("authenticate", self.handle_sasl_authenticate)
        self.add_global_handler("900", self.handle_sasl_900)

    def server(self):
        """Creates and returns a ServerConnection object."""

        c = ServerConnection(self)
        with self.mutex:
            self.connections.append(c)
        return c

    def handle_cap(self, connection, event):
        if not connection.capabilities:
            return

        # Start SASL authorization.
        elif event.arguments[0].lower() == "ack" and connection.password:
            connection.authenticate(True)

    def handle_sasl_authenticate(self, connection, event):
        if not connection.password:
            return

        if event.target == "+":
            connection.authenticate()

    def handle_sasl_900(self, connection, event):
        connection.cap("END")
        connection.authenticated = True
