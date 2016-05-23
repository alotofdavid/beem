beem
====

beem is a multi-user chat bot that can relay queries to the IRC
knowledge bots for `DCSS <http://crawl.develz.org/wordpress/>`__ from
WebTiles chat. This bot is being available on
`CBRO <http://crawl.berotato.org:8080/>`__ and
`CAO <http://crawl.akrasiac.org:8080/>`__,
`CXC <http://crawl.xtahua.com:8080/>`__, and
`CUE <http://www.underhound.eu:8080/#lobby>`__, where it will
automatically join your chat if your game has the most spectators and
isn't idle for too long. You can subscribe to have beem watch your games
automatically. See the `command guide <docs/beem_commands.md>`__ for
details on using beem from chat.

The remaining instructions on this page are only relevant if you want to
run a custom instance of this bot.

Details
-------

beem manages a Freenode IRC connection that sends queries to the bots on
Freenode and receive the results. It supports monitoring the chat of any
number of WebTiles games based on user subscription made in chat, and
can dedicate a connection to watch the most-spectated game on the
server. The server is single-threaded and uses
`asyncio <https://docs.python.org/3.4/library/asyncio.html>`__ to manage
an an event loop with concurrent tasks.

Installation
~~~~~~~~~~~~

The following are required:

-  Python 3.4 or later
-  asyncio module (3.4.3 tested)
-  irc module (13.1 tested)
-  pytoml module (0.1.5 tested)
-  websockets module (3.0 tested)
-  `webtiles <https://github.com/gammafunk/webtiles>`__ module

All but *webtiles* are available in PyPI. You can install *webtiles*
from the github repository and then likewise the *beem* package directly
from the repositories with pip3. For example, for a local install:

::

    pip3 install --user git+https://github.com/gammafunk/webtiles.git
    pip3 install --user git+https://github.com/gammafunk/beem.git

Configuration
~~~~~~~~~~~~~

Copy the `beem\_config.toml.sample <beem_config.toml.sample>`__ file to
``beem_config.toml`` and edit the necessary fields based on how you'd
like to run the bot. The config file format is
`toml <https://github.com/toml-lang/toml>`__, and the various field you
can change are in this file are documented in comments.
