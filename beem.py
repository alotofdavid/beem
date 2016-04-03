#!/usr/bin/env python3

"""
beem

"""

import argparse
import asyncio
import functools
import logging
import os
import signal
import sys

import config
import dcss
import twitch
import webtiles

## Initial config is empty, will be loaded by beem_server
_conf = config.conf
## Will configured when beem_server is started
_log = logging.getLogger()

class beem_server:
    def __init__(self, config_file=None):
        self._dcss_task = None
        self._twitch_task = None
        self._webtiles_task = None
        self._loop = asyncio.get_event_loop()
        self._shutdown_error = False

        ## Load config file
        if config_file:
            _conf.path = config_file

        try:
            _conf.read()
        except config.config_error as e:
            _log.critical(e.msg)
            sys.exit(1)

        try:
            config.load_user_db()
        except Exception as e:
            err_reason = type(e).__name__
            if len(e.args):
                err_reason = e.args[0]
            _log.critical("Unable to load user DB: %s", err_reason)
            sys.exit(1)

    def start(self):
        _log.info("Starting beem server.")

        def do_exit(signame):
            _log.error("Got signal %s: exit", signame)
            is_error = True if signame == "SIGTERM" else False
            asyncio.ensure_future(self.stop(is_error))

        for signame in ("SIGINT", "SIGTERM"):
            self._loop.add_signal_handler(getattr(signal, signame),
                                           functools.partial(do_exit, signame))

        print("Event loop running forever, press Ctrl+C to interrupt.")
        print("pid %s: send SIGINT or SIGTERM to exit." % os.getpid())

        self._loop.run_until_complete(self._process())
        sys.exit(self._shutdown_error)

    @asyncio.coroutine
    def stop(self, is_error=False):
        _log.info("Stopping beem server.")
        self._shutdown_error = is_error

        dcss.manager.disconnect()

        if self._dcss_task and not self._dcss_task.done():
            self._dcss_task.cancel()

        if _conf.service_enabled("twitch"):
            twitch.manager.disconnect()

            if self._twitch_task and not self._twitch_task.done():
                self._twitch_task.cancel()

        if _conf.service_enabled("webtiles"):
            yield from webtiles.manager.stop()

            if self._webtiles_task and not self._webtiles_task.done():
                self._webtiles_task.cancel()

    @asyncio.coroutine
    def _process(self):
        tasks = []

        if _conf.service_enabled("webtiles"):
            self._webtiles_task = asyncio.ensure_future(
                webtiles.manager.start())
            tasks.append(self._webtiles_task)

        self._dcss_task = asyncio.ensure_future(dcss.manager.start())
        tasks.append(self._dcss_task)

        if _conf.service_enabled("twitch"):
            self._twitch_task = asyncio.ensure_future(twitch.manager.start())
            tasks.append(self._twitch_task)

        yield from asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-c", dest="config_file", metavar="<toml-file>",
                        default=None, help="The beem config file to use.")
    args = parser.parse_args()


    server = beem_server(args.config_file)
    server.start()
