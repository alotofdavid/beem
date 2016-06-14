# beem

beem is a multi-user chat bot that can relay queries to the IRC knowledge bots
for [DCSS](http://crawl.develz.org/wordpress/) from WebTiles chat. See the
[command guide](docs/commands.md) for details on using beem from WebTiles
chat. The remaining instructions on this page are only relevant if you want to
run a custom instance of this bot.

### Details

beem supports monitoring the chat of any number of concurrent WebTiles games
based on user subscriptions made in chat. It can also dedicate a connection to
watching the most-spectated game on the server automatically. It manages a
single Freenode IRC connection that relays queries to the knowledge bots,
receives the results, and forwards them to the game chat that originated the
query. The beem server is single-threaded and uses
[asyncio](https://docs.python.org/3.4/library/asyncio.html) to manage an an
event loop with concurrent tasks.

### Installation

The following are required:

* Python 3.4 or later
* asyncio module (3.4.3 tested)
* irc module (13.1 tested)
* pytoml module (0.1.5 tested)
* websockets module (3.0 tested)
* [webtiles](https://github.com/gammafunk/webtiles) module

All packages above except *webtiles* are available in PyPI. You can install the
*webtiles* and *beem* packages directly from their respective repositories with
pip3. For example:

    pip3 install --user git+https://github.com/gammafunk/webtiles.git
    pip3 install --user git+https://github.com/gammafunk/beem.git

### Configuration

Copy the [beem_config.toml.sample](beem_config.toml.sample) file to
`beem_config.toml` and edit the necessary fields based on how you'd like to run
the bot. The config file format is [toml](https://github.com/toml-lang/toml),
and the various field you can change are in this file are documented in
comments.
