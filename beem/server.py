#!/usr/bin/env python3

"""beem: A multi-user chat bot that can relay queries to the IRC
knowledge bots for DCSS from WebTiles or Twitch chat.

"""

import argparse
import asyncio
import functools
import logging
import os
import signal
import sys
import webtiles

from .config import beem_conf
from .dcss import dcss_manager
from .twitch import twitch_manager
from .userdb import get_user_data, load_user_db, register_user, set_user_field
from .webtiles import webtiles_manager

## Will be configured by beem_server after the config is loaded.
_log = logging.getLogger()

class BeemServer:
    """The beem server. Load the beem_configuration instance and runs the
    tasks for the DCSS manager and the managers of any services
    enabled in the config.

    """

    def __init__(self, config_file=None):
        self.dcss_task = None
        self.twitch_task = None
        self.webtiles_task = None
        self.loop = asyncio.get_event_loop()
        self.shutdown_error = False

        ## Load config file
        if config_file:
            beem_conf.path = config_file

        try:
            beem_conf.load()
        except Exception as e:
            err_reason = type(e).__name__
            if len(e.args):
                err_reason = e.args[0]
            _log.critical(err_reason)
            sys.exit(1)

        try:
            self.load_users()

        except Exception as e:
            err_reason = type(e).__name__
            if len(e.args):
                err_reason = e.args[0]
            _log.critical("Unable to load user DB: %s", err_reason)
            sys.exit(1)

    def load_users(self):

        load_user_db()

        if not beem_conf.get("single_user"):
            return

        # Make sure we're registered for all services in single user mode.
        for service in services:
            if not beem_conf.service_enabled(service):
                continue

            sconf = beem_conf.get(service)
            _log.info(service)
            if not get_user_data(service, sconf["listen_user"]):
                register_user(service, sconf["listen_user"])

        # Link the listen user's WebTiles and Twitch usernames.
        if beem_conf.service_enabled("webtiles"):
            set_user_field("webtiles", beem_conf.webtiles["listen_user"],
                                  "subscription", 1)
            if beem_conf.service_enabled("twitch"):
                set_user_field("webtiles", beem_conf.webtiles["listen_user"],
                               "twitch_username",
                               beem_conf.twitch["listen_user"])

    def start(self):
        """Start the server, set up the event loop and signal handlers,
        and exit when the manager tasks finish.

        """

        _log.info("Starting beem server.")

        def do_exit(signame):
            _log.error("Got signal %s: exit", signame)
            is_error = True if signame == "SIGTERM" else False
            asyncio.ensure_future(self.stop(is_error))

        for signame in ("SIGINT", "SIGTERM"):
            self.loop.add_signal_handler(getattr(signal, signame),
                                           functools.partial(do_exit, signame))

        print("Event loop running forever, press Ctrl+C to interrupt.")
        print("pid %s: send SIGINT or SIGTERM to exit." % os.getpid())

        self.loop.run_until_complete(self.process())
        sys.exit(self.shutdown_error)

    @asyncio.coroutine
    def stop(self, is_error=False):
        """Stop the server by canceling any ongoing manager tasks, which
        will cause this beem server process to exit.

        """

        _log.info("Stopping beem server.")
        self.shutdown_error = is_error

        dcss_manager.disconnect()

        if self.dcss_task and not self.dcss_task.done():
            self.dcss_task.cancel()

        if beem_conf.service_enabled("twitch"):
            twitch_manager.disconnect()

            if self.twitch_task and not self.twitch_task.done():
                self.twitch_task.cancel()

        if beem_conf.service_enabled("webtiles"):
            yield from webtiles_manager.stop()

            if self.webtiles_task and not self.webtiles_task.done():
                self.webtiles_task.cancel()

    @asyncio.coroutine
    def process(self):
        tasks = []

        if beem_conf.service_enabled("webtiles"):
            self.webtiles_task = asyncio.ensure_future(
                webtiles_manager.start())
            tasks.append(self.webtiles_task)

        self.dcss_task = asyncio.ensure_future(dcss_manager.start())
        tasks.append(self.dcss_task)

        if beem_conf.service_enabled("twitch"):
            self.twitch_task = asyncio.ensure_future(twitch_manager.start())
            tasks.append(self.twitch_task)

        yield from asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-c", dest="config_file", metavar="<toml-file>",
                        default=None, help="The beem config file to use.")
    args = parser.parse_args()

    server = BeemServer(args.config_file)
    server.start()
