beem
====

beem is a multi-user chat bot that can relay queries to the IRC knowledge bots
for [DCSS](http://crawl.develz.org/wordpress/) from WebTiles or Twitch
chat. This bot is being tested on [CBRO](http://crawl.berotato.org:8080/) and
[CAO](http://crawl.akrasiac.org:8080/), [CXC](http://crawl.xtahua.com:8080/),
and [CUE](http://www.underhound.eu:8080/#lobby), where it will automatically
join your chat if your game has the most spectators and isn't idle for too
long. You can subscribe to have beem watch your games automatically. See the
[command guide](docs/commands.md) for details on using beem from chat.

beem manages a Freenode IRC connection that sends queries to the bots on
Freenode and receive the results. It's a server that supports monitoring the
chat of any number of WebTiles games based on user subscription made in chat,
and can dedicate a connection to watch the most-spectated game on the
server. It can also watch the chat of any number of Twitch streams and respond
to queries. The server is single-threaded and uses
[asyncio](https://docs.python.org/3.4/library/asyncio.html) to manage an an
event loop with concurrent tasks.

Installation
------------

The following are required:

* Python 3.4 or later
* Recent asyncio module (3.4.3 tested)
* Recent irc module (13.1 tested)
* Recent pytoml module (0.1.5 tested)
* Recent websockets module (3.0 tested)
* Recent [webtiles](https://github.com/gammafunk/webtiles) module

All but *webtiles* are available in PyPI. You can install *webtiles* from the
github repository and then likewise the *beem* package directly from the
repositories with pip3. For example, for a local install:

    pip3 install --user git+https://github.com/gammafunk/webtiles.git
    pip3 install --user git+https://github.com/gammafunk/beem.git

Configuration
-------------

Copy the [beem_config.toml.sample](beem_config.toml.sample) file to
`beem_config.toml` and edit the necessary fields based on how you'd like to run
the bot. The config file format is [toml](https://github.com/toml-lang/toml),
and the various field you can change are in this file are documented in
comments.
