"""The real Telegram client and update loop, against a local stand-in for Telegram.

The other tests fake Telegram entirely, which is how a bug that stopped the bot
starting (getUpdates always raised) got past them. This one sends real HTTP.
"""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from herdr_tg.__main__ import handle_update, skip_backlog
from herdr_tg.bot import Bot
from herdr_tg.telegram import Telegram
from tests.test_bot import OWNER, fleet, msg


class FakeTelegram(BaseHTTPRequestHandler):
    pending: list[dict] = []
    sent: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        method = self.path.rsplit("/", 1)[-1]
        if method == "getUpdates":
            offset = body.get("offset")
            if offset == -1:
                result = self.pending[-1:]
            else:
                result = [u for u in self.pending if offset is None or u["update_id"] >= offset]
        else:
            self.sent.append(body)
            result = {}
        data = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class EndToEnd(unittest.TestCase):
    def setUp(self):
        FakeTelegram.pending, FakeTelegram.sent = [], []
        self.server = HTTPServer(("127.0.0.1", 0), FakeTelegram)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tg = Telegram("TOKEN", base=f"http://127.0.0.1:{self.server.server_port}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_startup_skips_old_messages_then_answers_new_ones(self):
        h = fleet()
        bot = Bot(OWNER, h)
        FakeTelegram.pending = [{"update_id": 5, "message": msg("/to 2 stale order")}]
        offset = skip_backlog(self.tg)
        self.assertEqual(offset, 6)
        FakeTelegram.pending.append({"update_id": 6, "message": msg("/to 2 fresh order")})
        for update in self.tg.updates(offset, wait=0):
            handle_update(bot, self.tg, update)
        self.assertEqual(h.prompts, [("w2:p1", "fresh order")])   # the stale one never ran
        self.assertEqual(FakeTelegram.sent[-1]["chat_id"], OWNER)
        self.assertIn("Sent to 2 · rex applicant", FakeTelegram.sent[-1]["text"])


if __name__ == "__main__":
    unittest.main()
