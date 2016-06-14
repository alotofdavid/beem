# beem command guide

beem is a bot that listens to [DCSS](http://crawl.develz.org/wordpress/)
WebTiles game chat and sends queries to the IRC knowledge bots. If you see beem
spectating to a game's chat, you can type commands for any of the knowledge
bots Sequell, Gretell, or Cheibriados to have beem return the results.

This bot is available on the official servers CAO, CBRO, CJR, CPO, CUE, and
CXC. If you need to find beem on your server, look for the user with the most
spectators, since the bot will automatically watch that game.

When you see beem in chat, type the following command to have beem
automatically watch your games on the current server:

    !beem subscribe

To prevent beem from watching your games, type:

    !beem unsubscribe

### Knowledge bot command examples

A quick guide to the types of knowledge bot commands that beem recognizes. This
is not an exhaustive list, just a series of quick examples with pointers to
where you can read for more information.

##### LearnDB lookup

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

##### Monster lookup:

  Look up monster statistics with the prefix `@??`:

    @??the royal jelly
    @??orb_guardian perm_ench:berserk

  Alternately look up monsters using the bot Cheibriados with the prefix
  `%??`. Cheibriados also has monster information from some previous versions
  of Crawl:

    %??the royal jelly
    %0.15?cigotuvi's monster

##### List games and milestones

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

##### FooTV

  Watch ascii recordings of games played on servers that support them (all but
  cpo, cwz, and lld):

    !lm . orb -tv

  Load ttyrecs from a LearnDB entry:

    !learntv hilarious_deaths[27]

  See `??footv` and `??ttyrec` for further details.

##### Morgues and in-progress game dumps

  Add `-log` to any `!lg` query, or use `!log`:

    !lg . splat -log
    !log . splat

  Look up dumps for in-progress games:

    &dump
    &dump . cbro trunk

##### Other Sequell commands:

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


##### Git commit lookup

  Look up commits in the official crawl github repository by commit or branch:

        %git 0a147b9
        %git stone_soup-0.17

  Search for the most recent commit matching a string:

        %git HEAD^{/Moon Base}

  Search for the n-th most recent commit matching a string:

        !gitgrep 2 moon troll

### beem control commands

Use these commands from Webtiles chat to control beem. These commands can be
run from the WebTiles chat of any game where beem is listening, including your
own games.

- `!beem subscribe`

  Have beem watch your games automatically whenever it sees them. You only need
  to run this command once, since beem will remember your subscription. You can
  also run this command to resubscribe after having used the `!beem
  unsubscribe` command, and there's no limit to resubscriptions.

- `!beem unsubscribe`

  Prevent beem from watching your games. beem will leave your game's chat after
  you run this command. You can run `!beem subscribe` from any other game's
  chat where beem is listening to resubscribe.

- `!beem nick [<name>]`

  Set the nick beem will use for you when making queries to Sequell. On
  WebTiles, this is only useful if you play on multiple accounts and have set
  your nick within Sequell using the `!nick` command. You can set your Sequell
  nick in the [##crawl](http://webchat.freenode.net/?channels=##crawl) channel
  on Freenode. If you only play on one WebTiles account, it's not necessary to
  set your nick with beem, since it will use your current account name for
  queries automatically.
