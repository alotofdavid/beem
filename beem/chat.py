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

class BotCommandException(Exception):
    """Raised when a bot command doesn't parse or its arguments fail to satisfy
    its requirements. The error message will be reported in the chat source."""

def pluralize_name(name):
    """Make a name plural."""

    if name.lower().endswith('s'):
        name += "'"
    else:
        name += "'s"
    return name

class ChatWatcher():
    """A base class used for handling chat messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # message timestamps for command throttling.
        self.message_times = []
        self.bot_command_prefix = '!'
        self.admin_target_prefix = "^"

    def log_exception(self, error_msg):
        """Log an exception message with a stacktrace."""

        exc_type, exc_value, exc_tb = sys.exc_info()
        _log.error("%s: In %s, %s:", self.manager.service, self.describe(),
                error_msg)
        _log.error("".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)))

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
        chat. Returning None will cause no $chat variable nick
        substitution to occur."""

        return None

    def get_command_usage(self, command):
        """Make a command usage string to print when a bot command failed to
        parse."""

        msg = "Usage: {}{}".format(self.bot_command_prefix, command)

        entry = self.manager.bot_commands[command]
        entry_args = entry["args"] if entry["args"] else []
        for a in entry_args:
            arg_desc = a["description"]
            if not a["required"]:
                arg_desc = "[{}]".format(arg_desc)
            msg += " {}".format(arg_desc)

        return msg

    def get_target_user(self, user, arg_msg):
        """Get the target user for a bot command and any remaining arguments in
        the message"""

        if not arg_msg:
            return (user, arg_msg)

        # Get any target username for the command.
        target_name = None
        new_arg_msg = arg_msg.split(maxsplit=1)
        name = new_arg_msg.pop(0)
        if (name.startswith(self.admin_target_prefix)
            and not name == self.admin_target_prefix):
            target_name = name[len(self.admin_target_prefix):]
            if new_arg_msg:
                arg_msg = new_arg_msg[0]

        if target_name:
            if not self.manager.user_is_admin(user):
                raise BotCommandException(
                        "You must be an admin to specify a target user.")

            target_user = self.get_user_by_name(target_name)
            if not target_user:
                raise BotCommandException("Unknown user: {}".format(
                    target_name))

        else:
            target_user = user

        return (target_user, arg_msg)

    def get_bot_command_args(self, command, arg_msg):
        """Parse any remaining arguments for the given bot command after any
        target user has been parsed out."""

        entry = self.manager.bot_commands[command]
        if not entry.get("args"):
            return []

        args = []
        if arg_msg:
            args = arg_msg.split(maxsplit=len(entry["args"]) - 1)

        valid = True
        args_left = len(args)
        for i, a in enumerate(entry["args"]):
            if a["required"] and args_left == 0:
                valid = False
                break

            if args_left > 0 and not re.match(a["pattern"], args[i]):
                valid = False
                break

            args_left -= 1

        if not valid:
            raise BotCommandException(self.get_command_usage(command))

        return args

    def check_bot_command_restrictions(self, user, entry):
        """Check command-level restrictions that don't depend on argument
        values."""

        if self.manager.single_user and entry.get("disallow_single_user_mode"):
            raise BotCommandException(
                    "This command is not allowed in single user mode.")

        if self.manager.user_is_admin(user):
            return

        if entry.get("require_admin"):
            raise BotCommandException("This command must be run by an admin.")

        if entry.get("require_user_source") and user != self.user:
            raise BotCommandException(
                    "This command must be run from your own {}.".format(
                        self.source_type_desc))

        if entry.get("require_bot_source") and self.user != self.login_user:
            raise BotCommandException(
                    "This command must be run from {} {}.".format(
                        pluralize_name(self.login_user), self.bot_source_desc))

    def parse_bot_command(self, user, message):
        """Try to parse the message as a bot command, returning a tuple of the
        target user, the command (without the bot command prefix), and a list
        of any args. Any trailing whitespace in the message is removed."""

        message = message.rstrip()
        if (not message.startswith(self.bot_command_prefix)
            or message == self.bot_command_prefix):
            return

        message = message[len(self.bot_command_prefix):]

        arg_msg = message.split(maxsplit=1)
        command = arg_msg.pop(0).lower()
        if arg_msg:
            arg_msg = arg_msg[0]

        # Make !<bot-name> and !help aliases for !bothelp.
        if (command == self.get_chat_name(self.login_user, True).lower()
            or command == "help"):
            command = "bothelp"

        if not command in self.manager.bot_commands:
            return

        entry = self.manager.bot_commands[command]
        self.check_bot_command_restrictions(user, entry)

        target_user, arg_msg = self.get_target_user(user, arg_msg)

        args = self.get_bot_command_args(command, arg_msg)
        return (target_user, command, args)

    def get_user_by_name(self, name):
        """Get the user object of a user based on the user's name as a string.
        Currently only relevant for Discord."""

        return name

    @asyncio.coroutine
    def run_bot_command(self, sender, target_user, command, args,
            orig_message):
        """Attempt to run a bot command."""

        entry = self.manager.bot_commands[command]

        try:
            yield from entry["function"](self, target_user, *args)

        except BotCommandException as e:
            yield from self.send_chat(e.args[0])

        except Exception:
            self.log_exception("unable to handle bot command"
                    "(requester: {}, command: {})".format(sender, orig_message))

        else:
            if not entry.get("unlogged"):
                _log.info("%s: Did bot command (source: %s, request user: "
                        "%s): %s", self.manager.service, self.describe(),
                        sender, orig_message)

    def message_needs_escape(self, message):
        """Check if the messages might get parsed by other chat bots and will
        need escaping."""

        return message.startswith("!")

    def at_command_limit(self, command_time):
        # Expire any timestamps longer than the command period.
        for t in list(self.message_times):
            if command_time - t >= self.manager.conf["command_period"]:
                self.message_times.remove(t)

        if len(self.message_times) >= self.manager.conf["command_limit"]:
            return True

        return False

    @asyncio.coroutine
    def read_chat(self, sender, message):
        """Read a chat message and process any bot or DCSS commands"""

        if sender == self.login_user:
            return

        if not self.is_allowed_user(sender):
            return

        admin = self.manager.user_is_admin(sender)
        command_time = time.time()
        # We don't return right away so we can log attempts to issue commands
        # over the rate limit.
        at_limit = self.at_command_limit(command_time)
        invalid_usage = False
        bot_cmd = None
        try:
            bot_cmd = self.parse_bot_command(sender, message)

        except BotCommandException as e:
            if admin or not at_limit:
                yield from self.send_chat(e.args[0])

            invalid_usage = True

        # Message wasn't a command at all.
        if (not invalid_usage
                and not bot_cmd
                and not self.manager.dcss_manager.is_dcss_message(message)):
            return

        if not admin and at_limit:
             _log.warn("%s: Attempted command ignored due to command limit "
                     "(source: %s, requester: %s): %s", self.manager.service,
                       self.describe(), sender, message)
             return

        # This was an attempted command, record it for rate-limiting.
        if not admin:
            self.message_times.append(command_time)

        if invalid_usage:
            return

        if bot_cmd:
            target_user, command, args = bot_cmd
            yield from self.run_bot_command(sender, target_user, command, args,
                    message)
        else:
            yield from self.manager.dcss_manager.read_message(self, sender,
                    message)


@asyncio.coroutine
def bot_help_command(source, user):
    help_text = source.manager.conf["help_text"]
    help_text = help_text.replace("\n", " ")
    help_text = help_text.replace("%n", source.get_chat_name(source.login_user))
    yield from source.send_chat(help_text)
