"""Run the bot:  python3 -m herdr_tg

Settings come from the environment, or from ~/.config/herdr-telegram/config as
KEY=value lines:
    HERDR_TG_TOKEN    the bot token from @BotFather (required)
    HERDR_TG_CHAT_ID  your chat id; leave it out at first and the bot tells you it
    HERDR_TG_POLL     seconds between checks for finished agents (default 5)
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from herdr_tg import herdr
from herdr_tg.bot import COMMANDS, Bot, Reply
from herdr_tg.telegram import Telegram, TelegramError

CONFIG = Path.home() / ".config" / "herdr-telegram" / "config"


def settings(path: Path = CONFIG, env: dict | None = None) -> dict[str, str]:
    """The config file's KEY=value lines, overridden by the environment."""
    env = os.environ if env is None else env
    found: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                found[key.strip()] = value.strip().strip('"').strip("'")
    found.update({k: v for k, v in env.items() if k.startswith("HERDR_TG_")})
    return found


def skip_backlog(tg: Telegram) -> int | None:
    """The offset after anything sent while the bot was off. Old commands are not run:
    a "/to 2 delete the branch" from yesterday shouldn't fire on restart."""
    pending = tg.updates(offset=-1, wait=0)
    return pending[-1]["update_id"] + 1 if pending else None


def watch_once(bot: Bot, tg: Telegram, primed: bool) -> bool:
    """One check. Returns whether the bot has learned the starting states yet."""
    try:
        if not primed:
            bot.prime()   # first look is quiet, so starting the bot sends nothing
            return True
        for notice in bot.changes():
            tg.send(bot.owner, notice.text)
            bot.commit(notice)   # only once delivered; otherwise it's offered again
    except Exception as e:   # noqa: BLE001 - the watcher must outlive any one failure
        print(f"watcher: {type(e).__name__}: {e}", file=sys.stderr)
    return primed


def watch(bot: Bot, tg: Telegram, every: float, stop: threading.Event) -> None:
    primed = watch_once(bot, tg, False)
    while not stop.wait(every):
        primed = watch_once(bot, tg, primed)


def send_reply(tg: Telegram, chat_id: int, reply) -> None:
    if isinstance(reply, Reply):
        tg.send(chat_id, reply.text, reply.buttons)
    elif reply:
        tg.send(chat_id, reply)


def handle_update(bot: Bot, tg: Telegram, update: dict) -> None:
    cb = update.get("callback_query")
    if cb:
        chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
        try:
            popup, reply = bot.handle_callback(cb)
        except Exception as e:   # noqa: BLE001 - one bad tap mustn't stop the bot
            print(f"callback: {type(e).__name__}: {e}", file=sys.stderr)
            popup, reply = "Something went wrong", None
        try:
            tg.answer(cb.get("id"), popup)   # always, or the button keeps spinning
        except TelegramError as e:
            # Usually "query is too old" after a slow herdr call. The tap still
            # happened, so send its reply anyway.
            print(f"answer: {e}", file=sys.stderr)
        if reply and chat_id is not None:
            send_reply(tg, chat_id, reply)
        return
    msg = update.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id is None:
        return
    try:
        reply = bot.handle_message(msg)
    except Exception as e:   # noqa: BLE001 - one bad message mustn't stop the bot
        print(f"handle: {type(e).__name__}: {e}", file=sys.stderr)
        reply = f"Something went wrong ({type(e).__name__}). The bot is still running."
    send_reply(tg, chat_id, reply)


BAD_TOKEN = 78   # EX_CONFIG: a setup problem, not a crash


def connect(tg: Telegram) -> int | None:
    """Skip the backlog, retrying until Telegram is reachable. A refused token stops
    here with a clear message instead of retrying forever."""
    while True:   # started at login, the network may not be up yet
        try:
            offset = skip_backlog(tg)
            break
        except TelegramError as e:
            if e.bad_token:
                print(f"telegram refused the bot token ({e}). Check HERDR_TG_TOKEN in "
                      f"{CONFIG}, or get a new one from @BotFather.", file=sys.stderr)
                sys.exit(BAD_TOKEN)
            print(f"telegram: {e}; retrying in 5s", file=sys.stderr)
            time.sleep(5)
    try:
        tg.set_commands(COMMANDS)   # only the "/" menu; never worth failing over
    except TelegramError as e:
        print(f"telegram: couldn't set the command menu ({e})", file=sys.stderr)
    return offset


def poll_once(bot: Bot, tg: Telegram, offset: int | None) -> int | None:
    """Handle one batch of updates and return the next offset. A token revoked while
    running stops the bot; anything else is waited out."""
    try:
        for update in tg.updates(offset):
            offset = update["update_id"] + 1
            handle_update(bot, tg, update)
    except TelegramError as e:
        if e.bad_token:
            print(f"telegram refused the bot token ({e}). Stopping.", file=sys.stderr)
            sys.exit(BAD_TOKEN)
        print(f"telegram: {e}; retrying in 5s", file=sys.stderr)
        time.sleep(5)
    return offset


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        print("herdr-telegram: stopped.", file=sys.stderr)


def run() -> None:
    cfg = settings()
    token = cfg.get("HERDR_TG_TOKEN")
    if not token:
        sys.exit(f"Set HERDR_TG_TOKEN (see {CONFIG}). Create a bot with @BotFather to get one.")
    owner = int(cfg["HERDR_TG_CHAT_ID"]) if cfg.get("HERDR_TG_CHAT_ID") else None
    tg = Telegram(token)
    bot = Bot(owner)
    offset = connect(tg)
    stop = threading.Event()
    if owner is not None:
        threading.Thread(target=watch, args=(bot, tg, float(cfg.get("HERDR_TG_POLL", 5)), stop),
                         daemon=True).start()
        print("herdr-telegram: running. Message your bot.", file=sys.stderr)
    else:
        print("herdr-telegram: not paired. Send your bot any message to get your chat id.",
              file=sys.stderr)
    try:
        while True:
            offset = poll_once(bot, tg, offset)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
