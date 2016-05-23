# beem command guide

beem is a bot that sends queries to the
[DCSS](http://crawl.develz.org/wordpress/) IRC knowledge bots from WebTiles
chat. If beem is listening to your chat, type commands for the bots Sequell,
Gretell, and Cheibriados to have beem return the results.

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

  Look up monster statistics with the prefix `@??`:

    @??the royal jelly
    @??orb_guardian perm_ench:berserk

  Alternately look up monsters using the bot Cheibriados with the prefix
  `%??`. Cheibriados also has monster information from some previous versions
  of Crawl:

    %??the royal jelly
    %0.15?cigotuvi's monster

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

        %git 0a147b9
        %git stone_soup-0.17

  Search for the most recent commit matching a string:

        %git HEAD^{/Moon Base}

  Search for the n-th most recent commit matching a string:

        !gitgrep 2 moon troll


beem control commands
---------------------

Use these chat commands to control the bot when it's listening to your WebTiles
games. These can be run from any WebTiles chat where beem is listening.

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
