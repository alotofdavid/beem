import asyncio
import logging
import re
import time

import config
import dcss

_conf = config.conf
_log = logging.getLogger()

class chat_listener():
    def __init__(self):
        super().__init__()
        self.spectators = set()
        self._message_times = []

    def stop_listening(self):
        self.spectators = set()
        self._message_times = []

    def _is_beem_command(self, message):
        pattern = r'!{} +[^ ]'.format(self.bot_name)
        return re.match(pattern, message, re.I)

    def _is_allowed_user(self, user):
        """Ignore chat messages from ourself and disallowed users."""
        return user != self.bot_name

    @asyncio.coroutine
    def _send_command_usage(self, command, in_service):
        msg = "Usage: {} {}".format(self.bot_name, command)
        msg += " {}".format(self.service) if not in_service else ""
        if _command_data[command]["arg_description"]:
            msg += " [{}]".format(_command_data[command]["arg_description"])
        yield from self.send_chat(msg)

    @asyncio.coroutine
    def _read_beem_command(self, sender, message):
        message = re.sub(r'^!{} +'.format(self.bot_name), "", message)
        message = re.sub(r' +$', "", message)
        args = re.split(r' +', message)
        single_user_commands = ["register", "nick"]
        admin = _conf.user_is_admin(self.service, sender)
        command = args.pop(0).lower()
        if command == "help":
            yield from self.send_chat(_conf.help_text)
            return

        if admin and args and args[0].startswith("@"):
            target_user = args.pop(0).lower()[1:]
        else:
            target_user = sender

        found = False
        command_func = None
        for name, entry in config.services[self.service]["commands"].items():
            if name != command:
                continue

            # Don't allow non-admins to set twitch-user
            if (name == "twitch-user" and len(args) and not admin):
                yield from self.send_chat("Twitch usernames for WebTiles "
                                          "accounts must be set by an admin")
                return

            if (args and not entry["arg_pattern"]
                or len(args) > 1
                or args and not re.match(entry["arg_pattern"], args[0])):
                yield from self._send_command_usage(command)
                return

            command_func = entry["function"]
            break

        if not command_func:
            yield from self.send_chat("Unkown command. Type !{} help for "
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
            _log.error("%s: Unable to handle beem command (listen user: %s, "
                       "request user: %s, target user: %s, error: %s): %s",
                       config.services[self.service]["name"],
                       self.username, sender, target_user, e.args[0], command)
        else:
            _log.info("%s: Did beem command (listen user: %s, request user: %s, "
                      "target user: %s): %s",
                      config.services[self.service]["name"], self.username,
                      sender, target_user, command)

    def get_nick(self, username):
        user_data = config.get_user_data(self.service, username)
        if not user_data or not user_data["nick"]:
            return username

        return user_data["nick"]

    def _is_bad_pattern(self, message):
        if _conf.dcss.get("bad_patterns"):
            for pat in _conf.dcss["bad_patterns"]:
                if re.search(pat, message):
                    _log.debug("DCSS: Bad pattern message: %s", message)
                    return True

        bad_patterns = _conf.get(self.service).get("bad_patterns")
        if bad_patterns:
            for pat in bad_patterns:
                if re.search(pat, message):
                    _log.debug("%s: Bad %s pattern message: %s",
                               config.services[self.service]["name"],
                               message)
                    return True

        return False

    @asyncio.coroutine
    def read_chat(self, sender, message):
        if not self._is_allowed_user(sender):
            return

        if self._is_bad_pattern(message):
            return

        beem_command = self._is_beem_command(message)
        if not beem_command and not dcss.is_dcss_command(message):
            return

        admin = _conf.user_is_admin(self.service, sender)
        current_time = time.time()
        for timestamp in list(self._message_times):
            if current_time - timestamp >= _conf.command_period:
                self._message_times.remove(timestamp)
        if not admin:
            if len(self._message_times) >= _conf.command_limit:
                _log.info("%s: Command ignored due to command limit (listen "
                          "user: %s, requester: %s): %s",
                          config.services[self.service]["name"], self.username,
                          sender, message)
                return

            self._message_times.append(current_time)

        if beem_command:
            yield from self._read_beem_command(sender, message)
        # If we're listening in the chat of the bot itself, only
        # respond to beem commands. This is something we do for
        # Twitch, allowing users to make the Twitch beem commands from
        # the bot's channel.
        elif self.username != self.bot_name:
            yield from dcss.manager.read_command(self, sender, message)


@asyncio.coroutine
def nick_command(source, target_user, nick=None):
    _log.info("nicky")
    user_data = config.get_user_data(source.service, target_user)
    if not nick:
        if not user_data or not user_data["nick"]:
            yield from source.send_chat(
                "No nick for user {}".format(target_user))
        else:
            yield from source.send_chat(
                "Nick for user {}: {}".format(target_user, user_data["nick"]))
        return

    if not user_data:
        user_data = config.register_user(source.service, target_user)

    config.set_user_field(source.service, target_user, "nick", nick)
    yield from source.send_chat(
        "Nick for user {} set to {}".format(target_user, nick))

def is_bot_command(message):
    """Messages that might get parsed by other bots and will need escaping"""

    return (dcss.is_dcss_command(message)
            or message[0] == "!"
            or message[0] == "_")
