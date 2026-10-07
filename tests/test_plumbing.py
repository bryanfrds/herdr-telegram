"""Talking to herdr and Telegram, and reading settings, with both faked."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_tg import herdr, telegram
from herdr_tg.__main__ import handle_update, settings, skip_backlog, watch_once
from herdr_tg.telegram import Telegram, TelegramError, split


def completed(stdout="", code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


WORKSPACES = {"result": {"workspaces": [
    {"workspace_id": "wY", "number": 2, "label": "rex applicant"},
    {"workspace_id": "wX", "number": 1, "label": "Claude Maxxin"}]}}
AGENTS = {"result": {"agents": [
    {"pane_id": "wY:p1", "workspace_id": "wY", "agent_status": "idle"},
    {"pane_id": "wX:p1", "workspace_id": "wX", "agent_status": "working"}]}}


class Herdr(unittest.TestCase):
    def test_agents_are_named_after_their_workspace_in_sidebar_order(self):
        replies = {"workspace": json.dumps(WORKSPACES), "agent": json.dumps(AGENTS)}
        with mock.patch.object(subprocess, "run",
                               side_effect=lambda args, **kw: completed(replies[args[1]])):
            found = herdr.agents()
        self.assertEqual([a.name() for a in found], ["1 · Claude Maxxin", "2 · rex applicant"])
        self.assertEqual(found[0].status, "working")

    def test_prompt_and_read_pass_the_pane_and_text_as_separate_arguments(self):
        agent = herdr.Agent(2, "x", "wY:p1", "idle")
        with mock.patch.object(subprocess, "run", return_value=completed("ok\n")) as run:
            herdr.prompt(agent, "it's; $(rm -rf ~)")
            herdr.read(agent, 12)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["herdr", "agent", "prompt", "wY:p1", "it's; $(rm -rf ~)"])
        self.assertEqual(run.call_args_list[1].args[0],
                         ["herdr", "agent", "read", "wY:p1", "--lines", "12"])
        self.assertNotIn("shell", run.call_args.kwargs)    # never through a shell

    def test_herdr_failures_become_herdr_errors(self):
        with mock.patch.object(subprocess, "run", return_value=completed(code=1, stderr="no server")):
            with self.assertRaisesRegex(herdr.HerdrError, "no server"):
                herdr.agents()
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(herdr.HerdrError, "isn't installed"):
                herdr.agents()
        with mock.patch.object(subprocess, "run", return_value=completed("not json")):
            with self.assertRaises(herdr.HerdrError):
                herdr.agents()


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TelegramTests(unittest.TestCase):
    def test_send_posts_json_to_the_bot_url(self):
        with mock.patch.object(telegram.urllib.request, "urlopen",
                               return_value=FakeResponse({"ok": True, "result": {}})) as op:
            Telegram("TOKEN123").send(42, "hi")
        req = op.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/botTOKEN123/sendMessage"))
        self.assertEqual(json.loads(req.data), {"chat_id": 42, "text": "hi"})

    def test_errors_never_include_the_token(self):
        err = telegram.urllib.error.HTTPError(
            "https://api.telegram.org/botSECRET/x", 401, "Unauthorized", {}, None)
        with mock.patch.object(telegram.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(TelegramError) as ctx:
                Telegram("SECRET").send(1, "x")
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)

    def test_updates_asks_telegram_to_wait_for_new_messages(self):
        with mock.patch.object(telegram.urllib.request, "urlopen",
                               return_value=FakeResponse({"ok": True, "result": [{"update_id": 1}]})
                               ) as op:
            got = Telegram("T").updates(offset=7, wait=25)
        self.assertEqual(got, [{"update_id": 1}])
        body = json.loads(op.call_args.args[0].data)
        self.assertEqual(body, {"timeout": 25, "offset": 7, "allowed_updates": ["message"]})
        self.assertEqual(op.call_args.kwargs["timeout"], 35)   # HTTP waits past the long poll

    def test_a_dropped_connection_is_a_telegram_error(self):
        for exc in (ConnectionResetError(), telegram.http.client.RemoteDisconnected("x"),
                    TimeoutError()):
            with mock.patch.object(telegram.urllib.request, "urlopen", side_effect=exc):
                with self.assertRaises(TelegramError):
                    Telegram("T").updates(None)

    def test_a_malformed_token_never_appears_in_the_error(self):
        with self.assertRaises(TelegramError) as ctx:
            Telegram("SECRET with space").send(1, "x")   # http.client.InvalidURL quotes the URL
        self.assertNotIn("SECRET", str(ctx.exception))

    def test_long_messages_split_on_lines_and_stay_under_the_limit(self):
        text = "\n".join("line %d %s" % (i, "x" * 50) for i in range(200))
        parts = split(text, 1000)
        self.assertTrue(all(len(p) <= 1000 for p in parts))
        self.assertEqual("\n".join(parts), text)
        self.assertEqual(split("x" * 2500, 1000), ["x" * 1000, "x" * 1000, "x" * 500])


class Startup(unittest.TestCase):
    def test_settings_read_the_file_and_the_environment_wins(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "config")
            path.write_text('# comment\nHERDR_TG_TOKEN="from-file"\nHERDR_TG_CHAT_ID=5\n')
            cfg = settings(path, env={"HERDR_TG_CHAT_ID": "7", "PATH": "/bin"})
        self.assertEqual(cfg, {"HERDR_TG_TOKEN": "from-file", "HERDR_TG_CHAT_ID": "7"})

    def test_messages_sent_while_the_bot_was_off_are_skipped(self):
        tg = mock.Mock()
        tg.updates.return_value = [{"update_id": 41}]
        self.assertEqual(skip_backlog(tg), 42)
        tg.updates.assert_called_once_with(offset=-1, wait=0)
        tg.updates.return_value = []
        self.assertIsNone(skip_backlog(tg))




class Loops(unittest.TestCase):
    def test_the_watcher_survives_herdr_being_down_at_startup(self):
        bot, tg = mock.Mock(), mock.Mock()
        bot.prime.side_effect = [herdr.HerdrError("not running"), None]
        self.assertFalse(watch_once(bot, tg, False))   # still not primed, but alive
        self.assertTrue(watch_once(bot, tg, False))

    def test_the_watcher_survives_any_error_and_commits_only_after_sending(self):
        bot, tg = mock.Mock(), mock.Mock()
        notice = mock.Mock(text="done")
        bot.changes.return_value = [notice]
        tg.send.side_effect = TelegramError("offline")
        self.assertTrue(watch_once(bot, tg, True))
        bot.commit.assert_not_called()
        tg.send.side_effect = None
        watch_once(bot, tg, True)
        bot.commit.assert_called_once_with(notice)
        bot.changes.side_effect = KeyError("agent_status")   # herdr's format changed
        self.assertTrue(watch_once(bot, tg, True))

    def test_updates_without_a_chat_are_skipped(self):
        bot, tg = mock.Mock(), mock.Mock()
        handle_update(bot, tg, {"update_id": 1})
        handle_update(bot, tg, {"update_id": 2, "message": {"text": "hi"}})
        bot.handle_message.assert_not_called()
        tg.send.assert_not_called()

    def test_a_crash_handling_one_message_is_reported_not_fatal(self):
        bot, tg = mock.Mock(), mock.Mock()
        bot.handle_message.side_effect = ValueError("boom")
        handle_update(bot, tg, {"update_id": 1, "message": {"chat": {"id": 5}, "text": "x"}})
        self.assertIn("still running", tg.send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
