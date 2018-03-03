#!/usr/bin/env python3

"""beem: A multi-user chat bot that can relay queries to the IRC
knowledge bots for DCSS from WebTiles chat.

"""

import argparse

import asyncio
if hasattr(asyncio, "async"):
    ensure_future = asyncio.async
else:
    ensure_future = asyncio.ensure_future

import functools
import logging
import os
import signal
import sys
import traceback
import webtiles

from .dcss import DCSSManager
from .config import BeemConfig
from .webtiles import WebTilesManager, db_tables
from .botdb import BotDB
from .version import version

## Will be configured by beem_server after the config is loaded.
_log = logging.getLogger()

_DEFAULT_BEEM_CONFIG_FILE = "beem_config.toml"

class BeemServer:
    """The beem server. Load the configuration and runs the tasks for the DCSS
    and WebTiles managers.

    """

    def __init__(self, config_file):
        self.dcss_task = None
        self.webtiles_task = None
        self.loop = asyncio.get_event_loop()
        self.shutdown_error = False

        self.conf = BeemConfig(config_file)

        try:
            self.conf.load()

        except Exception:
            self.critical_error("Error loading configuration file"
                    " {}:".format(self.conf.path))

        self.dcss_manager = DCSSManager(self.conf.dcss)
        self.load_webtiles()

    def critical_error(self, error_msg):
        _log.critical("Server error: %s", error_msg)

        exc_type, exc_value, exc_tb = sys.exc_info()
        _log.error("".join(traceback.format_exception(
            exc_type, exc_value, exc_tb)))

        sys.exit(1)

    def load_webtiles(self):
        bot_db = BotDB(self.conf.db_file, db_tables, "webtiles_users")

        try:
            bot_db.load_db()

        except Exception:
            self.critical_error(
                    "unable to load DB file {}:".format(self.conf.db_file))

        wtconf = self.conf.webtiles
        self.webtiles_manager = WebTilesManager(wtconf, bot_db,
                                                self.dcss_manager)

        if wtconf.get("watch_username"):
            user_data = bot_db.get_user_data(wtconf["watch_username"])
            if not user_data:
                user_data = bot_db.register_user(wtconf["watch_username"])
            if not user_data["subscription"]:
                bot_db.set_user_field(wtconf["watch_username"], "subscription",
                        1)

    def start(self):
        """Start the server, set up the event loop and signal handlers,
        and exit when the manager tasks finish.

        """

        _log.info("Starting beem server.")

        def do_exit(signame):
            is_error = True if signame == "SIGTERM" else False
            msg = "Shutting down server due to signal: {}".format(signame)
            if is_error:
                _log.error(msg)
            else:
                _log.info(msg)
            self.stop(is_error)

        for signame in ("SIGINT", "SIGTERM"):
            self.loop.add_signal_handler(getattr(signal, signame),
                                           functools.partial(do_exit, signame))

        print("Event loop running forever, press Ctrl+C to interrupt.")
        print("pid %s: send SIGINT or SIGTERM to exit." % os.getpid())

        try:
            self.loop.run_until_complete(self.process())
        except asyncio.CancelledError:
            pass

        self.loop.close()
        sys.exit(self.shutdown_error)

    def stop(self, is_error=False):
        """Stop the server by canceling any ongoing manager tasks, which
        will cause this beem server process to exit.

        """

        _log.info("Stopping beem server.")
        self.shutdown_error = is_error

        if self.dcss_task and not self.dcss_task.done():
            self.dcss_task.cancel()

        if self.webtiles_task and not self.webtiles_task.done():
            self.webtiles_task.cancel()

    @asyncio.coroutine
    def process(self):
        tasks = []

        self.webtiles_task = ensure_future(self.webtiles_manager.start())
        tasks.append(self.webtiles_task)

        self.dcss_task = ensure_future(self.dcss_manager.start())
        tasks.append(self.dcss_task)

        yield from asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

        self.dcss_manager.disconnect()
        yield from self.webtiles_manager.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-c", dest="config_file", metavar="<toml-file>",
                        default=_DEFAULT_BEEM_CONFIG_FILE,
                        help="The beem config file to use.")
    parser.add_argument("--version", action="version", version=version)
    args = parser.parse_args()

    server = BeemServer(args.config_file)
    server.start()
