# beem command guide

beem is a bot that sends queries to the
[DCSS](http://crawl.develz.org/wordpress/) IRC knowledge bots from WebTiles or
Twitch chat. If beem is listening to your chat, type commands for the bots
Sequell, Gretell, and Cheibriados to have beem return the results.

To have beem automatically watch your WebTiles games on a server, type:

    !beem subscribe

To prevent beem from watching your games, type:

    !beem unsubscribe

Knowledge bot command examples
------------------------------

A quick guide to the types of knowledge commands beem recognizes.

### LearnDB lookup

  Look up topics in a user-contributed database of knowledge:

    ??sigmund
    ??singing_sword[2]

  Search within a topic or across topics:

    ??apropos_randart[minotaur]
    ?/moon

  See `??learndb` for other ways to read the topics. You can't edit/add LearnDB
  entries from beem; visit the
  [##crawl](http://webchat.freenode.net/?channels=##crawl) channel on Freenode
  to do that.

### Monster lookup:

  Look up monster statistics:

    @??the royal jelly
    @??orb_guardian perm_ench:berserk

### List games and milestones

  Use `!lg` to see details from completed games, and `!lm` to see milestones
  from completed or in-progress games:

    !lg
    !lg . splat
    !lm . rune

  The variable `$p` is set to the player's name, and `$chat` is set to a string
  you can use to query for everyone in chat:

    !lg $p
    !lg $chat recent !boring s=name / won o=%

  See `??listgame_examples` for more examples and `??lg` and `??lm` for further
  details.

### FooTV

  Watch ascii recordings of games played on servers that support them (all but
  cpo, cwz, and lld):

    !lm . orb -tv

  Load ttyrecs from a LearnDB entry:

    !learntv hilarious_deaths[27]

  See `??footv` and `??ttyrec` for further details.

### Morgues and in-progress game dumps

  Add `-log` to any `!lg` query, or use `!log`:

    !lg . splat -log
    !log . splat

  Look up dumps for in-progress games:

    &dump
    &dump . cbro trunk

### Other Sequell commands:

  Details on all games played and won for a player or nick. These accept any
  arguments for `!lg`:

    !won
    !gamesby elliptic

  Starting stats of a particular combo and aptitudes of a particular race or
  aptitudes of all races for a particular skill:

    !stats HEFi
    !apt HE
    !apt Summonings

  Look up progress towards various win achievements:

    !greatplayer gammafunk
    !greaterplayer gammafunk
    !greatrace Dr Dynast
    !greatrole Tm Dynast

  See `??sequell[2]` for the user-defined commands available and use `!help` to
  see documentation for commands with help documentation available.


### Git commit lookup

  Look up commits in the official crawl github repository by commit or branch:

        %git stone_soup-0.17
        %git 0a147b9

  Search for the most recent commit matching a string:

        %git HEAD^{/Moon Base}

  Search for the n-th most recent commit matching a string:

        !gitgrep 2 moon troll


beem control commands
---------------------

Use these chat commands to control the bot when it's listening to your WebTiles
games or your Twitch channel.

### WebTiles

These can be run from any WebTiles chat where beem is listening.

- `!beem subscribe`

  Have beem watch your games whenever it sees them. Note that beem has a limit
  to the number of games it can watch at once, so if it doesn't join your game
  right away, just wait a bit until someone else stops playing or goes idle.

- `!beem unsubscribe`

  Prevent beem from watching your games. beem will leave your chat after you
  run this command. You can run `beem subscribe` from any other chat where you
  see beem to have it resume watching your games.

- `!beem nick [<name>]`

  Set the nick beem will use for you when making queries to Sequell. On
  WebTiles, this is only useful if you play on multiple accounts and have set
  your nick within Sequell using the `!nick` command. You can set your Sequell
  nick in the [##crawl](http://webchat.freenode.net/?channels=##crawl) channel
  on Freenode. If you only play on one account, it's not necessary to set your
  nick with beem.

- `!beem twitch-reminder [on|off]`

  To use this command, you must have an admin link your WebTiles username to
  your Twitch username. Use `twitch-reminder` to enable/disable a WebTiles chat
  reminder for when beem is watching both your WebTiles game and your Twitch
  channel (i.e. you're playing on WebTiles during a Twitch stream). This
  reminder is sent every 15 minutes, telling your WebTiles spectators that
  you're streaming in Twitch and won't be responding to WebTiles chat. The url
  of your Twitch stream is included in the message.

### Twitch

On Twitch, beem uses the account *r4nr*, and responds to commands using the
`!r4nr` prefix. You can always find r4nr listening in its own chat at:

https://www.twitch.tv/r4nr/

From there you can run any of the commands below. Once r4nr has joined your
Twitch chat, it will respond to DCSS bot commands as well as `!r4nr` commands.

- `!r4nr nick [<name>]`

  Set the nick beem will use for you when making queries to Sequell. This is
  important to set if your Twitch username isn't the same as your WebTiles
  account or Sequell nick.

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
  chat as necessary if it's idle for 30 minutes or more. Running `!r4nr part`
  after you're done streaming or otherwise no longer need the bot frees it from
  having to track your channel, which is nice to do for your fellow Twitch
  users.
