beem
====

beem is a multi-user chat bot that can relay queries to the IRC knowledge bots
for [DCSS](http://crawl.develz.org/wordpress/) from WebTiles or Twitch
chat. This bot is being tested on [CSZO](http://crawl.s-z.org/) and will
automatically join your chat if your game has the most spectators and isn't
idle for too long. See the [command guide](docs/commands.md) for details on
using beem when it's listening in your chat.

beem manages a Freenode IRC connection, a Twitch IRC connection with arbitrary
many channels, and arbitrary many WebSocket game connections. It's
single-threaded and uses
[asyncio](https://docs.python.org/3.4/library/asyncio.html) to manage an an
event loop with concurrent tasks.

Dependencies
------------

* Python 3.4 or later
* Recent asyncio module (3.4.3 tested)
* Recent websockets module (3.0 tested)
* Recent irc module (13.1 tested)
* Recent pytoml module (0.1.5 tested)

These are in the [requirements.txt](requirements.txt) file and can be installed
with `pip3`.

Configuration
-------------

Copy the [beem_config.toml.sample](beem_config.toml.sample) file to
`beem_config.toml` and edit the necessary fields based on how you'd like to run
the bot. The config file format is
[toml](https://github.com/toml-lang/toml). and the fields in this file are
documented in comments.
