import asyncio
import logging
import re
import time

import config
import dcss

_conf = config.conf
_log = logging.getLogger()

_command_data = {
    "subscribe" : {
        "services" : ["webtiles"],
        "arg_pattern" : None,
        "arg_description" : None},
    "unsubscribe" : {
        "services" : ["webtiles"],
        "arg_pattern" : None,
        "arg_description" : None},
    "nick" : {
        "services" : ["webtiles", "twitch"],
        "arg_pattern" : r'^[a-zA-Z0-9-]{2,}$',
        "arg_description" : "<nick>"},
    "twitch-user" : {
        "services" : ["webtiles"],
        "arg_pattern" : r'^[a-zA-Z0-9][a-zA-Z0-9_]{3,24}$',
        "arg_description" : "<twitch-username>"},
    "twitch-reminder" : {
        "services" : ["webtiles"],
        "arg_pattern" : r'^(on|off)$',
        "arg_description" : "on|off"},
    "join" : {
        "services" : ["twitch"],
        "arg_pattern" : None,
        "arg_description" : None},
    "part" : {
        "services" : ["twitch"],
        "arg_pattern" : None,
        "arg_description" : None}}

class chat_listener():
    def __init__(self):
        super().__init__()
        self.spectators = set()
        self._command_count = 0
        self._time_last_command = None

    def stop_listening(self):
        self.spectators = set()
        self._command_count = 0

    def _is_beem_command(self, message):
        pattern = r'!{} +[^ ]'.format(self.bot_name)
        return re.match(pattern, message, re.I)

    def _is_allowed_user(self, user):
        """Ignore chat messages from ourself and disallowed users."""
        return user != self.bot_name

    @asyncio.coroutine
    def _send_command_usage(self, command, in_service):
        msg = "Usage: {} {}".format(self.bot_name, command)
        msg += " {}".format(self.service_name) if not in_service else ""
        if _command_data[command]["arg_description"]:
            msg += " [{}]".format(_command_data[command]["arg_description"])
        yield from self.send_chat(msg)

    @asyncio.coroutine
    def _read_beem_command(self, sender, message):
        message = re.sub(r'^!{} +'.format(self.bot_name), "", message)
        message = re.sub(r' +$', "", message)
        args = re.split(r' +', message)
        single_user_commands = ["register", "nick"]
        admin = _conf.user_is_admin(self.service_name, sender)
        service = self.service_name
        if not admin and self._command_count >= _conf.command_limit:
            _log.info("%s: Command from %s in chat of %s ignored due to "
                      "command limit: %s", config.service_data[service]["desc"],
                      sender, self.username, message)
            return

        command = args.pop(0).lower()
        if command == "help":
            yield from self.send_chat(_conf.help_text)
            return


        if command in config.service_data:
            if not len(args):
                yield from self.send_chat("Invalid command")
                return

            service = command
            command = args.pop(0).lower()

        if (not _conf.service_enabled(service)
            or (command.startswith("twitch")
                and not _conf.service_enabled("twitch"))
            or (_conf.get("single_user")
                and not admin
                and command not in single_user_commands)):
            yield from self.send_chat("Invalid command")
            return

        if admin and args and args[0].startswith("@"):
            target_user = args.pop(0).lower()[1:]
        else:
            target_user = sender

        in_service = service == self.service_name
        service_desc = config.service_data[service]["desc"]
        found = False
        for name, entry in _command_data.items():
            if name != command:
                continue

            if (not service in entry["services"]
                # Don't allow non-admins to set twitch-user
                or name == "twitch-user" and len(args) and not admin):
                yield from self.send_chat("Command not allowed")
                return

            if (args and not entry["arg_pattern"]
                or len(args) > 1
                or args and not re.match(entry["arg_pattern"], args[0])):
                yield from self._send_command_usage(command, in_service)
                return

            found = True
            break

        if not found:
            yield from self.send_chat("Unknown command")
            return

        if not admin:
            self._time_last_command = time.time()
            self._command_count += 1
        manager = config.service_data[service]["manager"]
        yield from manager.beem_command(self, sender, target_user, command,
                                        args)

    def get_nick(self, username):
        user_data = config.get_user_data(self.service_name, username)
        if not user_data or not user_data["nick"]:
            return username

        return user_data["nick"]

    @asyncio.coroutine
    def read_chat(self, sender, message):
        time_last = self._time_last_command
        if (time_last and time.time() - time_last > _conf.command_timeout):
            self._command_count = 0

        if not self._is_allowed_user(sender):
            return

        if self._is_beem_command(message):
            yield from self._read_beem_command(sender, message)
        elif dcss.is_dcss_command(message):
            yield from dcss.manager.read_command(self, sender, message)


def is_bot_command(message):
    """Messages that might get parsed by other bots and will need escaping"""

    return (dcss.is_dcss_command(message)
            or message[0] == "!"
            or message[0] == "_")
