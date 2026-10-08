"""The two Telegram Bot API calls this needs, over plain HTTPS."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request

LIMIT = 4096  # Telegram's maximum message length


class TelegramError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status   # the HTTP status, when Telegram answered at all

    @property
    def bad_token(self) -> bool:
        """Telegram refused the token itself: retrying won't help."""
        return self.status in (401, 404)


class Telegram:
    def __init__(self, token: str, base: str = "https://api.telegram.org"):
        self._url = f"{base}/bot{token}/"

    def _call(self, method: str, http_timeout: float, **params) -> object:
        req = urllib.request.Request(
            self._url + method, data=json.dumps(params).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as r:
                reply = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # The URL holds the token, so never let it reach a log or a message.
            raise TelegramError(f"{method} failed: HTTP {e.code}", e.code) from None
        except (OSError, http.client.HTTPException, ValueError) as e:
            # Dropped connections, timeouts, bad replies, and bad URLs (whose message
            # would quote the token) all become one error that names only the kind.
            raise TelegramError(f"{method} failed: {type(e).__name__}") from None
        if not reply.get("ok"):
            raise TelegramError(f"{method} failed: {reply.get('description', 'unknown error')}")
        return reply["result"]

    def updates(self, offset: int | None, wait: int = 30) -> list[dict]:
        """New messages, waiting up to `wait` seconds for one to arrive."""
        params = {"timeout": wait, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", http_timeout=wait + 10, **params)

    def send(self, chat_id: int, text: str,
             buttons: list[list[tuple[str, str]]] | None = None) -> None:
        """Send text, with optional tap-to-answer buttons (rows of (label, data))
        under the last part."""
        parts = split(text)
        for i, part in enumerate(parts):
            extra = {}
            if buttons and i == len(parts) - 1:
                extra["reply_markup"] = {"inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in buttons]}
            self._call("sendMessage", http_timeout=15, chat_id=chat_id, text=part, **extra)

    def answer(self, callback_id: str, text: str | None = None) -> None:
        """Acknowledge a button tap, so the phone stops showing it as loading."""
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
        self._call("answerCallbackQuery", http_timeout=15, **params)

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """The menu Telegram shows when you type "/"."""
        self._call("setMyCommands", http_timeout=15,
                   commands=[{"command": c, "description": d} for c, d in commands])


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
