# The beem command guide

beem is a multi-user chat bot that can relay queries to the IRC knowledge bots
for [DCSS](http://crawl.develz.org/wordpress/) from WebTiles or Twitch chat. If
beem watching a game in WebTiles, type commands into chat for the bots Sequell,
Gretell, and Cheibriados to have beem return the results. beem supports nearly
any command you would use use for these bots in the *##crawl* channel on
Freenode.

If you see beem watching a game played on
[CBRO](http://crawl.berotato.org:8080/) or
[CAO](http://crawl.akrasiac.org:8080/), type the following in chat to get
subscribed and have beem begin watching your games:

    !beem subscribe

See the [beem command](#beem-control-commands) section for other commands you
can use to control beem.

DCSS bot commands
-----------------

A quick guide for commonly used DCSS bot commands you can use in any chat where
beem is listening.

###List games and milestones

- `!lg`

  List games that have finished (ie. quit, died or won). The first argument is
  the target player, with `*` meaning all players and `.` meaning yourself. The
  remaining arguments add filters to select specific games. There are many
  fields you can filter and display, as well as more complicated queries. See
  `??listgame_examples` for some examples and `??lg` for further details.

- `!lm`

  List milestones for any game (either finished or ongoing). Takes most of the
  same arguments as `!lg`. See `??lm` for details and examples.

##### Special chat variables

- `$p`

  This will expand to the name of the current player in WebTiles or streamer in
  Twitch. Use this as a shortcut when making queries for the player/streamer's
  games. For example, the current player's last death at XL17 or higher:

        !lg $p splat

- `$chat`

  Currently supported only in WebTiles, this expands to a list of all users
  currently in chat. For example win rates of everyone in chat for games in
  recent versions that were not quit, sorted by win rate:

        !lg $chat recent !boring s=name / won o=%

###Morgues

- `!log` or the `-log` argument added to any `!lg` query

  Get the URL to the morgue of a finished game. `!log` takes any argument that
  `!lg` would.

- `&dump`

  Get the morgue of your latest in-progress game. Takes arguments for 1) player
  name 2) server and 3) version, with "trunk" being the default version. Here
  is gammafunk's in-progress game on cbro for version 0.17:

        &dump gammafunk cbro trunk

###FooTV

Games played on all online server except lld, cwz, and cpo have ttyrecs
recordings made automatically. These are ascii console recordings of online
games and are made on all servers except lld and cwz.

- `!tv` or `-tv` added to any `!lg` or `!lm` query

  Queue's the ttyrec in FooTV and gives a URL to watch in your web browser.

- `!learntv`

  Run a FooTV command that's stored in a learnDB entry. For example, this
  queues the 27th entry in `??hilarious_deaths`:

        !learntv hilarious_deaths[27]

- `!ttyrec` and `-ttyrec` with any `!lg` or `!lm` query

  Get the URL to the ttyrec file for use with a local ttyrec player like
  (jettyplay)[http://nethack4.org/projects/jettyplay/].

See `??footv` and `??ttyrec` for further examples and details.

###Sequell commands

There are many internal and user-defined commands you can use with
Sequell. Commands recognized by beem begin with `&`, `!`, `.`, or `=`. In
Twitch chat, use `_` instead of `.` at the beginning of commands.

A few examples:

- `!gamesby` and `!won`

  Details on all games played and won. Looks up your games by default but both
  accept `!lg` arguments.

- `!stats` and `!apt`

  Starting stats of a particular combo and aptitudes of a particular race or
  aptitudes of all races for a particular skill:

        !stats HEFi
        !apt HE
        !apt Summonings

- `!goodplayer`, `!greatplayer`, `!greaterplayer`, `!greatrace`, `!greatrole`

  Look up progress towards various win achievements.

Many commands do complicated !lg or !lm commands and accept arguments for
those. For example:

    !killratio sigmund * recent

will give sigmund's player kill-rate in recent versions of DCSS.

Type `??sequell[2]` to see the user-defined commands available and use `!help`
to see documentation for commands that have this.

###LearnDB

LearnDB is a user-contributed database of crawl knowledge (and jokes). Read a
topic using the `??` prefix.

For topics with multiple entries, add an index:

    ??singing_sword[2]

You can search for entries within a topic by using putting search text instead
of an index in the brackets:

    ??apropos_randart[minotaur]

You Find all topics/entries matching a term using `?/`:

    ?/goblin

See `??learndb` for other ways to read the entries. Note that you can't
add/edit entries using beem. You must do that from the ##crawl irc channel on
Freenode.

###Monster Database

Look up monster information relative to the trunk version through Gretell by
typing a query like:

    @??the royal jelly


In these monster queries, you can set some fields in the form `field:value`:

    @??ice_beast hd:27
    @??orb_guardian perm_ench:berserk
    @??sigmund spells:fire_storm.200.magical

to see monster details when they have a specific status, number of HD, or to
see how much damage they would do with a specific spell

For a specific serpent of hell add geh, dis, coc, or tar:

    @??serpent_of_hell geh

For spells, seperate entries with `;` and make each have the form
`spell_name.200.magical`. Here 200 is the spell frequency and "magical" is cast
type, but these don't matter for purposes of looking up monsters.

For the 0.17 monster database, you can make the same query through Cheibriados
using prefix `%??`.

###Git

- `%git`

  Look up commits in the official crawl github repository.

  You can specify a branch or commit hash as an argument. For the last commit in
  the 0.17 stable version:

        %git stone_soup-0.17

  For a specific commit:

        %git 0a147b9

  If you have a specific version number like "0.17-a0-488-g0a147b9" and want
  the corresponding commit, the hash string are the characters after the final
  dash but with the initial "g" removed.

  To search commits, use `HEAD^{/<search term>}` as an argument. For example,
  the last trunk commit with 'Moon Base' in the commit message:

        %git HEAD^{/Moon Base}

- `!gitgrep`

  Use queries of the form:

        !gitgrep <n> <search>

  to search for the n-th from last matching a specific string. This gets the
  2nd from last commit containing "moon troll":

        !gitgrep 2 moon troll


beem control commands
---------------------

These commands change your beem user settings and control the bot when it's
listening to your WebTiles games or your Twitch channel.

###WebTiles

These commands should be run from WebTiles chat of any game where beem is
listening.

- `!beem subscribe`

  Have beem watch your games whenever it sees them. Type this from any chat
  where you see beem watching to have it begin watching to your games. Note
  that beem has a limit to the number of games it can watch, so if it doesn't
  join your game right away, just wait a bit until someone else stops playing
  or goes idle, and beem will begin spectating.

- `!beem unsubscribe`

  Prevent beem from watching your games. beem will leave your chat after you
  run this command. You can run `beem subscribe` from any other chat where you
  see beem to have it watch your games again.

- `!beem nick [<name>]`

  Use it to check or set the nick beem will use when making queries to
  Sequell. This is most important for `!lg` commands if you have a nick defined
  in Sequell to track multiple accounts. Note this doesn't change your nick
  within Sequell itself; to do that you need to use the `!nick` command in the
  *##crawl* on Freenode IRC.

- `!beem twitch-reminder [on|off]`

  To use this command, you must have an admin link your WebTiles username to
  your Twitch username. Use `twitch-reminder` to enable/disable a WebTiles chat
  reminder for when beem is watching both your WebTiles game and your Twitch
  channel (e.g. you're playing on WebTiles during a Twitch stream). This
  reminder is sent every 15 minutes, telling your WebTiles spectators that
  you're streaming in Twitch and won't be responding to WebTiles chat. The url
  of your Twitch stream is included in the message.

### Twitch chat commands

On Twitch, beem uses the account *r4nr*, and responds to commands using the
`!r4nr` prefix. You can always find r4nr listening in its own chat at:

https://www.twitch.tv/r4nr/

From there you can run any of the commands below. Once r4nr has joined your
Twitch chat, it will respond to DCSS bot commands as well as `!r4nr` commands.

Here are the bot commands r4nr recognizes:

- `!r4nr nick [<name>]`

  Set the nick r4nr will use for you when making Sequell queries.

- `!r4nr join`

  Have beem join your Twitch chat and respond to queries. Note that there are a
  limited number of chat channels r4nr will listen to at once, and you may have
  to wait for a channel become idle or ask r4nr to part.

  Once you've issued `!r4nr join`, check the viewer list in your channel for
  r4nr or simply type a test command to verify that it's listening.

- `!r4nr part`

  Run this command in your chat after you're done streaming and no longer need
  r4nr.

If there are too many requests to watch Twitch channels, r4nr will leave your
chat as necessary after it becomes idle for at least 30 minutes. Running `!r4nr
part` after you're done streaming (or otherwise no longer need the bot) frees
it from having to track your channel and is nice to do for your fellow Twitch
users.
