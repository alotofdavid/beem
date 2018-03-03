"""Chat message handling"""

import asyncio
if not hasattr(asyncio, "ensure_future"):
    ensure_future = asyncio.async
else:
    ensure_future = asyncio.ensure_future

import logging
import re
import time
import traceback
import sys

_log = logging.getLogger()

class ChatWatcher():
    """A base class used by beem and lomlobot for handling chat messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message_times = []
        self.bot_command_prefix = '!'
        self.admin_target_prefix = "@"

    def log_exception(self, error_msg):
        """Log an exception message with a stacktrace."""

        exc_type, exc_value, exc_tb = sys.exc_info()
        _log.error("%s: In %s, %s:", self.manager.service, self.describe(),
                error_msg)
        _log.error("".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)))

    def is_allowed_user(self, user):
        """Do we read commands at all from the given user? Ignore chat
        messages from ourself."""

        return user != self.login_user

    def user_allowed_dcss(self, user):
        """Return True if the user is allowed to execute dcss bot commands."""

        return True

    def get_chat_name(self, user, sanitize=False):
        """A shortened form of the user's name. If `sanitize` is True, all
        chars that are not alphanumeric, underscore, or hyphen are removed.
        Used by the bot to determine the command name to respond for help
        purposes."""

        if sanitize:
            return re.sub('[^A-Za-z0-9_-]', '', user)
        else:
            return user

    def get_dcss_nick(self, user):
        """Return the nick we have mapped for a given user."""

        return user

    def get_chat_dcss_nicks(self, sender):
        """Return a set containing the nick mapping for all users in
        chat. Returning none will cause no $chat variable nick
        substitution to occur."""

        return None

    @asyncio.coroutine
    def send_command_usage(self, command):
        msg = "Usage: {}{}".format(self.bot_command_prefix, command)
        entry = self.manager.bot_commands[command]
        entry_args = entry["args"] if entry["args"] else []
        for a in entry_args:
            arg_desc = a["description"]
            if not a["required"]:
                arg_desc = "[{}]".format(arg_desc)
            msg += " {}".format(arg_desc)
        yield from self.send_chat(msg)

    def parse_bot_command(self, message):
        """Try to parse the message as a bot command, returning a tuple
        of the command (without the bot command prefix) and a list of
        any args. Any trailing whitespace in the message is removed."""

        message = message.rstrip()
        if (not message.startswith(self.bot_command_prefix)
                or message == self.bot_command_prefix):
            return (None, None)

        message = message[len(self.bot_command_prefix):]
        args = message.split(maxsplit=1)
        command = args.pop(0).lower()
        # Make !<bot-name> and !help aliases for !bothelp.
        if (command == self.get_chat_name(self.login_user, True).lower()
            or command == "help"):
            command = "bothelp"

        if not command in self.manager.bot_commands:
            return (None, None)

        entry = self.manager.bot_commands[command]
        if args and entry["args"] is not None:
            args = args[0].split(maxsplit=len(entry["args"]) - 1)
        return (command, args)

    def bot_command_allowed(self, user, command):
        entry = self.manager.bot_commands[command]
        if (entry["source_restriction"]
            and not self.manager.user_is_admin(user)):
            if entry["source_restriction"] == "admin":
                return (False, "This command must be run by an admin")

            if (entry["source_restriction"] == "user"
                and user != self.user):
                return (False, "This command must be run from your own chat.")

            if (entry["source_restriction"] == "bot"
                and self.user != self.login_user):
                return (False, "This command must be run from {}".format(
                            self.bot_source_desc))

        return (True, None)

    @asyncio.coroutine
    def run_bot_command(self, sender, command, args, orig_message):
        """Attempt to run a bot command."""

        if self.manager.single_user and not entry["single_user_allowed"]:
            return

        allowed, reason = self.bot_command_allowed(sender, command)
        if not allowed:
            yield from self.send_chat(reason)
            return

        admin = self.manager.user_is_admin(sender)
        if admin and args and args[0].startswith(self.admin_target_prefix):
            target_user = args.pop(0)[len(self.admin_target_prefix):]
            if not target_user:
                yield from self.send_command_usage(command)
                return

        else:
            target_user = sender

        entry = self.manager.bot_commands[command]
        valid = True
        args_left = len(args)
        entry_args = entry["args"] if entry["args"] else []
        for i, a in enumerate(entry_args):
            if a["required"] and args_left == 0:
                valid = False
                break

            if args_left > 0 and not re.match(a["pattern"], args[i]):
                valid = False
                break

            args_left -= 1

        if not valid or args_left > 0:
            yield from self.send_command_usage(command)
            return

        try:
            yield from entry["function"](self, target_user, *args)

        except Exception:
            self.log_exception("unable to handle bot command"
                    "(requester: {}, command: {})".format(sender, orig_message))
        else:
            _log.info("%s: Did bot command (source: %s, request user: "
                      "%s): %s", self.manager.service, self.describe(), sender,
                      orig_message)

    def message_needs_escape(self, message):
        """Check if the messages might get parsed by other chat bots and will
        need escaping.

        """

        return message[0] == "!"

    def handle_timeout(self):
        current_time = time.time()
        mconf = self.manager.conf
        for timestamp in list(self.message_times):
            if current_time - timestamp >= mconf["command_period"]:
                self.message_times.remove(timestamp)
        if len(self.message_times) >= mconf["command_limit"]:
            _log.info("%s: Command ignored due to command limit (source: %s, "
                      "requester: %s): %s",
                      self.manager.service, self.describe(),
                      sender, message)
            return True

        self.message_times.append(current_time)
        return False

    @asyncio.coroutine
    def read_chat(self, sender, message):
        """Read a chat message and process any bot or DCSS commands"""

        if not self.is_allowed_user(sender):
            return

        command, args = self.parse_bot_command(message)
        if (not command
            and (not self.manager.dcss_manager.is_dcss_message(message)
                 or not self.user_allowed_dcss(sender))):
            return

        if self.handle_timeout():
            return

        if command:
            yield from self.run_bot_command(sender, command, args, message)
        else:
            yield from self.manager.dcss_manager.read_message(self, sender,
                    message)

@asyncio.coroutine
def bot_help_command(source, user):
    help_text = source.manager.conf["help_text"]
    help_text = help_text.replace("\n", " ")
    help_text = help_text.replace("%n", source.get_chat_name(source.login_user))
    yield from source.send_chat(help_text)
