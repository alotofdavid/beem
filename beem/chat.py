"""Chat message handling"""

import asyncio
if not hasattr(asyncio, "ensure_future"):
    ensure_future = asyncio.async
else:
    ensure_future = asyncio.ensure_future

import logging
import re
import time

_log = logging.getLogger()

class ChatWatcher():
    """A base class used by beem and lomlobot for handling chat messages."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message_times = []

    def is_bot_command(self, message):
        pattern = r'!{}( +[^ ]|$)'.format(self.login_username)
        return re.match(pattern, message, re.I)

    def is_allowed_user(self, username):
        """Ignore chat messages from ourself and disallowed users."""
        return username != self.login_username

    @asyncio.coroutine
    def send_command_usage(self, command):
        msg = "Usage: {} {}".format(self.login_username, command)
        command_entry = self.manager.bot_commands[command]
        if command_entry["arg_description"]:
            msg += " [{}]".format(command_entry["arg_description"])
        yield from self.send_chat(msg)

    @asyncio.coroutine
    def read_bot_command(self, sender, message):
        message = re.sub(r' +$', "", message)
        orig_message = message
        message = re.sub(r'^!{} *'.format(self.login_username), "", message,
                         flags=re.I)
        if not message:
            command = "help"
            args = []
        else:
            args = re.split(r' +', message)
            command = args.pop(0).lower()
        admin = self.manager.user_is_admin(sender)
        if command == "help":
            help_text = self.manager.conf["help_text"]
            help_text = help_text.replace("\n", " ")
            help_text = help_text.replace("%n", self.login_username)
            yield from self.send_chat(help_text)
            return

        if admin and args and args[0].startswith("@"):
            target_user = args.pop(0).lower()[1:]
        else:
            target_user = sender

        found = False
        command_func = None
        single_user = self.manager.conf.get("watch_username") is not None
        for name, entry in self.manager.bot_commands.items():
            if (name != command
                or not entry["single_user_allowed"] and single_user
                or entry["admin"] and not admin):
                continue

            if (args and not entry["arg_pattern"]
                or len(args) > 1
                or args and not re.match(entry["arg_pattern"], args[0])):
                yield from self.send_command_usage(command)
                return

            command_func = entry["function"]
            break

        if not command_func:
            yield from self.send_chat(
                "Unknown command. Type !{} help for assistance".format(
                    self.login_username))
            return

        try:
            if args:
                yield from command_func(self, target_user, args[0])
            else:
                yield from command_func(self, target_user)

        except Exception as e:
            err_reason = type(e).__name__
            if len(e.args):
                err_reason = e.args[0]
            _log.error("%s: Unable to handle bot command (watch user: %s, "
                       "request user: %s): command: %s, error: %s",
                       self.manager.service, self.watch_username, sender,
                       orig_message, e.args[0])
        else:
            _log.info("%s: Did bot command (watch user: %s, request user: "
                      "%s): %s", self.manager.service, self.watch_username,
                      sender, orig_message)

    def message_needs_escape(self, message):
        """Check if the messages might get parsed by other chat bots and will
        need escaping.

        """

        return message[0] == "!" or message[0] == "_"

    @asyncio.coroutine
    def read_chat(self, sender, message):
        """Read a chat message and process any bot or DCSS commands"""

        if not self.is_allowed_user(sender):
            return

        bot_command = self.is_bot_command(message)
        if (not bot_command
            and not self.manager.dcss_manager.is_dcss_message(message)):
            return

        current_time = time.time()
        for timestamp in list(self.message_times):
            if current_time - timestamp >= self.manager.conf["command_period"]:
                self.message_times.remove(timestamp)
        if len(self.message_times) >= self.manager.conf["command_limit"]:
            _log.info("%s: Command ignored due to command limit (watch "
                      "user: %s, requester: %s): %s", self.manager.service,
                      self.watch_username, sender, message)
            return

        self.message_times.append(current_time)
        if bot_command:
            yield from self.read_bot_command(sender, message)
        else:
            yield from self.manager.dcss_manager.read_message(
                self, sender, message)


@asyncio.coroutine
def bot_nick_command(source, target_user, nick=None):
    """`!<bot-name> nick` chat command"""

    user_db = source.manager.user_db
    user_data = user_db.get_user_data(target_user)
    if not nick:
        if not user_data or not user_data["nick"]:
            yield from source.send_chat(
                "No nick for user {}".format(target_user))
        else:
            yield from source.send_chat(
                "Nick for user {}: {}".format(target_user, user_data["nick"]))
        return

    if not user_data:
        user_data = user_db.register_user(target_user)

    user_db.set_user_field(target_user, "nick", nick)
    yield from source.send_chat(
        "Nick for user {} set to {}".format(target_user, nick))
