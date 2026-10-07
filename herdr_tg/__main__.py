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
from herdr_tg.bot import Bot
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


def watch(bot: Bot, tg: Telegram, every: float, stop: threading.Event) -> None:
    bot.changes()   # learn the current states quietly, so startup sends nothing
    while not stop.wait(every):
        try:
            for message in bot.changes():
                tg.send(bot.owner, message)
        except (herdr.HerdrError, TelegramError) as e:
            print(f"watcher: {e}", file=sys.stderr)


def main() -> None:
    cfg = settings()
    token = cfg.get("HERDR_TG_TOKEN")
    if not token:
        sys.exit(f"Set HERDR_TG_TOKEN (see {CONFIG}). Create a bot with @BotFather to get one.")
    owner = int(cfg["HERDR_TG_CHAT_ID"]) if cfg.get("HERDR_TG_CHAT_ID") else None
    tg = Telegram(token)
    bot = Bot(owner)
    offset = skip_backlog(tg)
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
            try:
                for update in tg.updates(offset):
                    offset = update["update_id"] + 1
                    msg = update.get("message") or {}
                    reply = bot.handle(msg.get("chat", {}).get("id"), msg.get("text", ""))
                    if reply:
                        tg.send(msg["chat"]["id"], reply)
            except TelegramError as e:
                print(f"telegram: {e}; retrying in 5s", file=sys.stderr)
                time.sleep(5)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
