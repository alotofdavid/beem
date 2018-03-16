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
import string
import sys
import time
import traceback

_log = logging.getLogger()

# How long to wait in second for a query before ignoring any result from a bot
# and reusing the query ID. Sequell times out after 90s, so we use 100s.
_MAX_REQUEST_TIME = 100
# How long to wait after a connection failure before reattempting the
# connection.
_RECONNECT_TIMEOUT = 5

# Strings for services provided by DCSS bots. Used to match fields in the
# config andto indicate what type of query was performed.
bot_services = ["sequell", "monster", "git"]

# For extracting the single-character prefix from a Sequell !RELAY result.
_QUERY_PREFIX_CHARS = string.ascii_letters + string.digits
_query_prefix_regex = re.compile(r"^([a-zA-Z0-9])")

# Patterns for adding player and chat variables to Sequell queries.
_player_var_regex = re.compile(r"\$p(?=\W|$)|\$\{p\}")
_chat_var_regex = re.compile(r"\$chat(?=\W|$)|\$\{chat\}")

class IRCBot():
    """Coodrinate queries for a bot."""

    def __init__(self, manager, conf):
        self.manager = manager
        self.conf = conf

        self.init_services()

    def init_services(self):
        """Find any services we have in the config and create their regex
        pattern objects."""

        self.services = []
        self.service_patterns = {}

        for s in bot_services:
            field = "{}_patterns".format(s)
            if not field in self.conf:
                continue

            self.services.append(s)

            patterns = []
            for p in self.conf[field]:
                patterns.append(re.compile(p))
            self.service_patterns[s] = patterns

    def init_query_data(self):
        """Reset message and query tracking variables."""

        # A dict of query dicts keyed by the query id. Each entry holds info
        # about a DCSS query made to this bot so we can properly route the
        # result message when it's received back from the bot.
        self.queries = {}

        # A queue of query IDs used for synchronous services like monster and
        # git.
        self.queue = []

        # The query dict for the last query whose result we handled. Used to
        # route multiple part messages, primarily for monster queries, but
        # Sequell can sometimes send multiple messages in response to a single
        # query despite use of '!RELAY -n 1'
        self.last_answered_query = None

    def get_message_service(self, message):
        """Which type of service is this message intentded for? Returns None if
        none is found."""

        for s in self.services:
            for p in self.service_patterns[s]:
                if p.search(message):
                    return s

    def prepare_sequell_message(self, source, requester, query_id, message):
        """Format a message containing a query to send through Sequell's !RELAY
        command."""

        # Replace '$p' instances with the player's nick.
        if source.user:
            message = _player_var_regex.sub(source.get_dcss_nick(source.user),
                    message)

        # Replace '$chat' instances with a |-separated list of chat nicks.
        chat_nicks = source.get_chat_dcss_nicks(requester)
        if chat_nicks:
            message = _chat_var_regex.sub('@' + '|@'.join(chat_nicks), message)

        requester_nick = source.get_dcss_nick(requester)
        return "!RELAY -nick {} -prefix {} -n 1 {}".format(requester_nick,
                _QUERY_PREFIX_CHARS[query_id], message)

    def expire_query_entries(self, current_time):
        """Expire query entries in the queries dict, the last returned query,
        and in the query id queue if they're too old relative to the given
        time."""

        last_query_age = None
        if self.last_answered_query:
            last_query_age = current_time - self.last_answered_query["time"]
        if last_query_age and last_query_age >= _MAX_REQUEST_TIME:
            self.last_answered_query = None

        for i in range(0, len(_QUERY_PREFIX_CHARS)):
            if i not in self.queries:
                if i in self.queue:
                    self.queue.remove(i)

            elif current_time - self.queries[i]["time"] >= _MAX_REQUEST_TIME:
                del self.queries[i]
                if i in self.queue:
                    self.queue.remove(i)

    def make_query_entry(self, source, username, message):
        """Find a query id available for use, recording the details of the
        requesting source and username as well as the time of the request in a
        dict that is stored in our dict of pending queries."""

        current_time = time.time()
        self.expire_query_entries(current_time)

        # Find an available query id.
        query_id = None
        for i in range(0, len(_QUERY_PREFIX_CHARS)):
            if (not i in self.queries
                    and (not self.last_answered_query
                        or i != self.last_answered_query['id'])):
                query_id = i
                break

        if query_id is None:
            raise Exception("too many queries in queue")

        query = {'id'           : query_id,
                 'requester'    : username,
                 'source_ident' : source.get_source_ident(),
                 'time'         : current_time,
                 'type'         : self.get_message_service(message)}
        self.queries[query_id] = query

        return query

    @asyncio.coroutine
    def send_query_message(self, source, requester, message):
        """Send a message containing a DCSS query to the bot."""

        query_entry = self.make_query_entry(source, requester, message)

        if 'sequell' in self.services:
            message = self.prepare_sequell_message(source, requester,
                    query_entry['id'], message)
        else:
            self.queue.append(query_entry['id'])

        yield from self.manager.send(self.conf["nick"], message)

    def get_message_query_id(self, message):
        """Get the originating query ID associated with the given IRC message
        recieved from the bot."""

        # This bot has Sequell, which means we assume it uses !RELAY and that
        # Sequell queries are the only type of queries the bot handles.
        if 'sequell' in self.services:
            match = _query_prefix_regex.match(message)
            if not match:
                _log.warning("DCSS: Received %s message with invalid "
                             "relay prefix: %s", self.conf["nick"], message)
                return

            return int(_QUERY_PREFIX_CHARS.index(match.group(1)))

        # The remain query types are non-Sequell and have no equivalent to
        # Sequell's !RELAY. These bots are synchronous, so the first entry in
        # the query id queue will be the relevant query id.
        else:
            if not len(self.queue):
                return

            return self.queue.pop(0)

    def get_message_query(self, message):
        """Find the query details we have based on the message or the queue."""

        self.expire_query_entries(time.time())

        query_id = self.get_message_query_id(message)
        # If we have no query information at all, return the query we last
        # answered, which may be None.
        if query_id is None:
            return self.last_answered_query

        # We have a query ID but no query data, meaning there's no unanswered
        # query corresponding to this ID. This can happen for Sequell when it
        # decides to send another line of response for a query even through we
        # use '-n 1' with '!RELAY'. In this case we can use the last answered
        # query if the IDs match.
        if not query_id in self.queries:
            if (self.last_answered_query
                and query_id == self.last_answered_query['id']):
                return self.last_answered_query

            else:
                _log.warning("DCSS: Unable to find query for %s result: %s",
                        nick, message)
                return

        self.last_answered_query = self.queries[query_id]
        del self.queries[query_id]
        return self.last_answered_query


class DCSSManager():
    """DCSS manager. Responsible for managing an IRC connection, sending queries
    to the knowledge bots and sending the results to the right source
    chat."""

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

    def log_exception(self, error_msg):
        """Log an exception and its traceback with the given message describing
        the source of the exception."""

        exc_type, exc_value, exc_tb = sys.exc_info()
        _log.error("DCSS: %s", error_msg)
        _log.error("".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)))

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

        # Holds received IRC messages until they can be processed.
        self.messages = []

        for bot_nick, bot in self.bots.items():
            bot.init_query_data()

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
        raise."""

        if self.conf.get("fake_connect") or not self.server.is_connected():
            return

        try:
            self.server.disconnect()

        except Exception:
            self.log_exception("Error when disconnecting IRC")

    def is_connected(self):
        """Are we connected to IRC? This does not mean we are done with any IRC
        authentication we might be doing, just that the connection is up."""

        # If fake_connect is true, we still need connect() to be called once.
        # We check self.server.authenticated, since that will be set to True by
        # connect() under fake_connect.
        if self.conf.get("fake_connect"):
            return self.server.authenticated

        return self.server.is_connected()

    @asyncio.coroutine
    def start(self):
        """Start the DCSS manager task."""

        _log.info("DCSS: Starting manager")

        while True:
            # If we're not connected, attempt to connect, reconnecting after a
            # wait period upon error.
            tried_connect = False
            while not self.is_connected():
                if tried_connect:
                    yield from asyncio.sleep(_RECONNECT_TIMEOUT)

                try:
                    yield from self.connect()

                except asyncio.CancelledError:
                    return

                except Exception:
                    self.log_exception("Unable to connect IRC")

                tried_connect = True

            # This will populate self.messages with any IRC messages we've
            # received.
            try:
                self.reactor.process_once()

            except Exception:
                self.log_exception("Error reading IRC connection")
                self.disconnect()

            for m in list(self.messages):
                nick, message = m
                self.messages.remove(m)
                yield from self.read_irc(nick, message)

            # XXX This seems needed to give other coroutines a chance to run.
            # It may be needed until we can rework the irc connection to use
            # asyncio's connections/streams.
            yield from asyncio.sleep(0.1)

    @asyncio.coroutine
    def send(self, nick, message):
        """Send a private IRC message to the given nick."""

        _log.debug("DCSS: Sending message to %s: %s", nick, message)
        if self.conf.get("fake_connect"):
            return

        self.server.privmsg(nick, message)

    def dispatcher(self, connection, event):
        """Dispatch events to on_<event.type> method, if present. All messages
        we're interested in are either related to SASL authentication or are
        private messages from DCSS IRC that are DCSS query results."""

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
        """Hande a 900 event message that indicates SASL authentication is
        complete. Connection details are managed by self.server, so we only log
        this."""

        _log.info("DCSS: SASL authentication complete")

    def on_904_message(self, event):
        _log.critical("DCSS: SASL authentication failed, shutting down")
        os.kill(os.getpid(), signal.SIGTERM)

    def on_privmsg(self, event):
        """Handle an IRC private message."""

        if not self.ready():
            return

        # Remove any terminal control characters and extract the nick.
        message = re.sub(
            "\x1f|\x02|\x12|\x0f|\x16|\x03(?:\d{1,2}(?:,\d{1,2})?)?", "",
            event.arguments[0], flags=re.UNICODE)
        nick = re.sub(r"([^!]+)!.*", r"\1", event.source)
        self.messages.append((nick, message))

    @asyncio.coroutine
    def read_irc(self, nick, message):
        """Process an IRC message, forwarding any query results to the query
        source."""

        if not self.bots.get(nick):
            _log.warning("DCSS: Ignoring message from %s: %s", nick, message)
            return

        query = self.bots[nick].get_message_query(message)
        if not query:
            return

        manager = self.managers[query["source_ident"]["service"]]
        source = manager.get_source_by_ident(query["source_ident"])
        if not source:
            _log.warning("DCSS: Ignoring %s message with unknown source: %s",
                         nick, message)
            return

        # Sequell can output queries for other bots.
        if query["type"] == "sequell":
            # Remove relay prefix
            message = _query_prefix_regex.sub("", message)
            bot = None
            for n, b in self.bots.items():
                if n == nick:
                    continue

                if b.get_message_service(message):
                    bot = b
                    break

            if bot:
                try:
                    yield from bot.send_query_message(source,
                            query["requester"], message)
                    return

                except Exception:
                    self.log_exception("Unable to relay message to {} from {} "
                            "on behalf of {}: {}".format(bot.conf['nick'],
                                source.describe(), query["requester"],
                                message))
                    return

            # Sequell returns /me literally instead of using an IRC action, so
            # we do the dirty work here.
            if message.lower().startswith("/me "):
                message_type = "action"
                message = message[4:]
            else:
                message_type = "normal"
        else:
            message_type = query["type"]

        yield from source.send_chat(message, message_type)

    @asyncio.coroutine
    def read_message(self, source, username, message):
        """Read a message from the given source and username, sending any query
        to the appropriate bot."""

        bot = None
        for n, b in self.bots.items():
            if b.get_message_service(message):
                bot = b
                break

        if not bot:
            raise Exception("Unknown bot message: {}".format(message))

        try:
            yield from bot.send_query_message(source, username, message)

        except Exception:
            self.log_exception("Unable to send message from {} to {} "
                    "(requester: {}, message: {})".format(source.describe(),
                        bot.conf["nick"], username, message))

        else:
            _log.debug("DCSS: Sent %s message (source: %s, requester: %s): %s",
                    bot.conf["nick"], source.describe(), username, message)

    def is_bad_pattern(self, message):
        """Does this message match against a 'bad pattern' regexp that excludes
        it from processing?"""

        if not self.conf.get("bad_patterns"):
            return False

        for pat in self.conf["bad_patterns"]:
            if re.search(pat, message):
                _log.debug("DCSS: Bad pattern message: %s", message)
                return True

    def is_dcss_message(self, message):
        """Does this message a dcss message handled by one of the bots?"""

        if self.is_bad_pattern(message):
            return False

        for bot_nick, bot in self.bots.items():
            if bot.get_message_service(message):
                return True

        return False


class ServerConnection(irc.client.ServerConnection):
    """The ServerConnection class from irc.client, modified to send a
    differently formatted USER command, to support automatic capability
    requests, and to support SASL authentication. Once SASL authentication is
    complete, the authenticated property will be True."""

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

        Returns the ServerConnection object."""

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
        command sent in the original irc.client.ServerConnection."""

        self.send_raw("USER {0} {0} {1} :{2}".format(username, self.server,
                                                     realname))

    def authenticate(self, request_method=False):
        """AUTHENTICATE command. If request_method is True, request PLAIN
        authentication, which is the only type we support. Otherwise send the
        authentication credentials."""

        if request_method:
            self.send_raw("AUTHENTICATE PLAIN")
            return

        authdata = base64.b64encode("{0}\x00{0}\x00{1}".format(
            self.username, self.password).encode())

        self.send_raw("AUTHENTICATE {}".format(authdata.decode()))

# For unrecognized byte sequences, use a replacement character.
ServerConnection.buffer_class.errors = 'replace'

class Reactor(irc.client.Reactor):
    """The Reactor class from irc.client that uses our modified ServerConnection
    class and coordinates capabilities and SASL requests."""

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
