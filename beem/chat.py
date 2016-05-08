import asyncio
import logging
import re
import time

from .config import beem_conf, services
from .dcss import dcss_manager, is_dcss_command
from .userdb import get_user_data, register_user, set_user_field

_log = logging.getLogger()

class ChatWatcher():
    def __init__(self):
        super().__init__()
        self.message_times = []

    def is_beem_command(self, message):
        pattern = r'!{}( +[^ ]|$)'.format(self.bot_name)
        return re.match(pattern, message, re.I)

    def is_allowed_user(self, user):
        """Ignore chat messages from ourself and disallowed users."""
        return user != self.bot_name

    @asyncio.coroutine
    def send_command_usage(self, command):
        msg = "Usage: {} {}".format(self.bot_name, command)
        command_entry = services[self.service]["commands"][command]
        if command_entry["arg_description"]:
            msg += " [{}]".format(command_entry["arg_description"])
        yield from self.send_chat(msg)

    @asyncio.coroutine
    def read_beem_command(self, sender, message):
        message = re.sub(r'^!{} *'.format(self.bot_name), "", message)
        message = re.sub(r' +$', "", message)
        orig_message = message
        if not message:
            command = "help"
            args = []
        else:
            args = re.split(r' +', message)
            command = args.pop(0).lower()
        single_user_commands = ["register", "nick"]
        admin = beem_conf.user_is_admin(self.service, sender)
        if command == "help":
            help_text = beem_conf.get(self.service)["help_text"]
            help_text = help_text.replace("\n", " ")
            help_text = help_text.replace("%n", self.bot_name)
            yield from self.send_chat(help_text)
            return

        if admin and args and args[0].startswith("@"):
            target_user = args.pop(0).lower()[1:]
        else:
            target_user = sender

        found = False
        command_func = None
        for name, entry in services[self.service]["commands"].items():
            if (name != command
                or beem_conf.get("single_user") and not entry["single_user"]
                or name == "status" and not admin):
                continue

            # Don't allow non-admins to set twitch-user
            if (name == "twitch-user" and len(args) and not admin):
                yield from self.send_chat("Twitch usernames for WebTiles "
                                          "accounts must be set by an admin")
                return

            if (args and not entry["arg_pattern"]
                or len(args) > 1
                or args and not re.match(entry["arg_pattern"], args[0])):
                yield from self.send_command_usage(command)
                return

            command_func = entry["function"]
            break

        if not command_func:
            yield from self.send_chat("Unknown command. Type !{} help for "
                                      "assistance".format(self.bot_name))
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
            _log.error("%s: Unable to handle beem command (watch user: %s, "
                       "request user: %s): command: %s, error: %s",
                       services[self.service]["name"], self.game_username,
                       sender, orig_command, e.args[0])
        else:
            _log.info("%s: Did beem command (watch user: %s, request user: "
                      "%s): %s", services[self.service]["name"],
                      self.game_username, sender, orig_command)

    def get_nick(self, username):
        """Return the nick we have mapped for the given user. Return the
        username if no such mapping exists.

        """

        user_data = get_user_data(self.service, username)
        if not user_data or not user_data["nick"]:
            return username

        return user_data["nick"]

    def is_bad_pattern(self, message):
        if beem_conf.dcss.get("bad_patterns"):
            for pat in beem_conf.dcss["bad_patterns"]:
                if re.search(pat, message):
                    _log.debug("DCSS: Bad pattern message: %s", message)
                    return True

        bad_patterns = beem_conf.get(self.service).get("bad_patterns")
        if bad_patterns:
            for pat in bad_patterns:
                if re.search(pat, message):
                    _log.debug("%s: Bad %s pattern message: %s",
                               services[self.service]["name"], message)
                    return True

        return False

    @asyncio.coroutine
    def read_chat(self, sender, message):
        """Read a chat message and process any beem or DCSS commands"""

        if not self.is_allowed_user(sender):
            return

        if self.is_bad_pattern(message):
            return

        beem_command = self.is_beem_command(message)
        if not beem_command and not is_dcss_command(message):
            return

        current_time = time.time()
        for timestamp in list(self.message_times):
            if current_time - timestamp >= beem_conf.command_period:
                self.message_times.remove(timestamp)
        if len(self.message_times) >= beem_conf.command_limit:
            _log.info("%s: Command ignored due to command limit (watch "
                      "user: %s, requester: %s): %s",
                      services[self.service]["name"], self.game_username,
                      sender, message)
            return

        self.message_times.append(current_time)
        if beem_command:
            yield from self.read_beem_command(sender, message)
        # If we're watching in the chat of the bot itself, only
        # respond to beem commands. This is something we do for
        # Twitch, allowing users to make the Twitch beem commands from
        # the bot's channel.
        else:
            yield from dcss_manager.read_command(self, sender, message)


@asyncio.coroutine
def beem_nick_command(source, target_user, nick=None):
    """`!<bot-name> nick` chat command for both WebTiles and Twitch"""

    user_data = get_user_data(source.service, target_user)
    if not nick:
        if not user_data or not user_data["nick"]:
            yield from source.send_chat(
                "No nick for user {}".format(target_user))
        else:
            yield from source.send_chat(
                "Nick for user {}: {}".format(target_user, user_data["nick"]))
        return

    if not user_data:
        user_data = register_user(source.service, target_user)

    set_user_field(source.service, target_user, "nick", nick)
    yield from source.send_chat(
        "Nick for user {} set to {}".format(target_user, nick))

def is_bot_command(message):
    """Check if the messages might get parsed by other chat bots and will
    need escaping.

    """

    return (is_dcss_command(message)
            or message[0] == "!"
            or message[0] == "_")
