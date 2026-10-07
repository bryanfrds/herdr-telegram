# herdr-telegram

Message your [herdr](https://herdr.dev) agents from your phone. A small Telegram bot runs
on your Mac next to herdr: you see which agents are working, send one a prompt, read its
screen, and get a message when it finishes or needs you.

```
you:  /agents
bot:  ⏳ 1 · Claude Maxxin (working)
      ✅ 2 · rex applicant (idle)
you:  /to 2 run the tests and fix anything that fails
bot:  Sent to 2 · rex applicant. I'll tell you when it's done.
      … a few minutes later …
bot:  ✅ 2 · rex applicant finished.
      (the last lines of its screen)
```

No server, no open ports, nothing to install: it's plain Python 3.10+ talking to herdr's
command-line API and to Telegram over HTTPS.

## Commands

| Command | What it does |
|---|---|
| `/agents` | Every agent, with its herdr space number and status |
| `/to 2 fix the login bug` | Send one prompt to an agent |
| `/use 2` | Pick an agent; after that, plain messages go to it |
| `/read` | The picked agent's latest screen (or `/read 2`, `/read 2 60` for 60 lines) |
| `/help` | The list above |

Agents are named after their herdr space: by number (`2`) or by name (`rex applicant`,
or just the start of it, `cla`). A whole name beats a shorter one, so with spaces called
`rex` and `rex applicant`, `/to rex applicant …` goes to rex applicant. When a partial
name could mean another agent (is `/to rex a quick fix` for `rex`, or `rex a…`?), the bot
asks instead of guessing.

Numbers are never ambiguous, but they can move: closing a space renumbers the ones after
it. If a number now points at a different agent than your last `/agents` showed, the bot
refuses and asks you to check again. Two agents in the same space share its number and
name, so split them into separate spaces to reach both.

You get a message when an agent goes from working to finished (✅) or to waiting on a
question (✋). The bot won't send a prompt to an agent that is waiting on a question;
`/read` it and answer at the Mac.

## Setup

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot` and follow
   the steps. It gives you a **token**.
2. Save it, readable only by you:
   ```bash
   mkdir -p ~/.config/herdr-telegram
   echo 'HERDR_TG_TOKEN=paste-your-token' > ~/.config/herdr-telegram/config
   chmod 600 ~/.config/herdr-telegram/config
   ```
3. Start the bot and send it any message from your phone:
   ```bash
   git clone https://github.com/bryanfrds/herdr-telegram && cd herdr-telegram
   python3 -m herdr_tg
   ```
   It replies with your **chat id**. Add it to the config and restart:
   ```bash
   echo 'HERDR_TG_CHAT_ID=123456789' >> ~/.config/herdr-telegram/config
   python3 -m herdr_tg
   ```

Your Mac has to be awake for the bot to answer.

## Safety

This lets a phone type into coding agents, so it's built to fail closed:

- **Only you, only in a private chat.** The chat and the sender must both be you, and
  groups are ignored, since everyone in a group could type into your agents. Anything
  else gets no reply and runs nothing. An unpaired bot only tells people their own chat
  id.
- **Nothing forwarded.** Forwarded messages, and messages sent through another bot, are
  refused, so a pasted message can't become a prompt by accident.
- **No replay.** Messages sent while the bot was off are skipped at startup, so yesterday's
  `/to 2 …` doesn't fire when you restart it.
- **No shell.** Prompts go to herdr as a single argument, never through a shell.
- **The token stays secret.** Errors never include it, and `config` is git-ignored.

Someone with your token still can't control your agents, since commands only count from
you. But they could read the commands you send, message you as the bot, or knock it
offline, so treat the token like a password and revoke it in @BotFather if it leaks.
Also bear in mind that prompts and screens pass through Telegram's servers, and bot chats
aren't end-to-end encrypted.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

They use a fake herdr, and fake Telegram either in code or as a small local web server,
so they never touch your real agents or send real messages. GitHub runs them on Python
3.10 and 3.12 for every pull request.

## License

MIT, © 2026 bryanfrds
