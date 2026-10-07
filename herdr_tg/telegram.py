"""The two Telegram Bot API calls this needs, over plain HTTPS."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

LIMIT = 4096  # Telegram's maximum message length


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self._url = f"{base}/bot{token}/"

    def _call(self, method: str, timeout: float, **params) -> object:
        req = urllib.request.Request(
            self._url + method, data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # The URL holds the token, so never let it reach a log or a message.
            raise TelegramError(f"{method} failed: HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise TelegramError(f"{method} failed: {type(e).__name__}") from None
        if not reply.get("ok"):
            raise TelegramError(f"{method} failed: {reply.get('description', 'unknown error')}")
        return reply["result"]

    def updates(self, offset: int | None, wait: int = 30) -> list[dict]:
        """New messages, waiting up to `wait` seconds for one to arrive."""
        params = {"timeout": wait, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", timeout=wait + 10, **params)

    def send(self, chat_id: int, text: str) -> None:
        for part in split(text):
            self._call("sendMessage", timeout=15, chat_id=chat_id, text=part)


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Break a long message on line ends where possible, never over `limit`."""
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts + [text] if text else parts or [""]
