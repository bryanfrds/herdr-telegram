"""Talking to herdr and Telegram, and reading settings, with both faked."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_tg import herdr, telegram
from herdr_tg.__main__ import settings, skip_backlog
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


if __name__ == "__main__":
    unittest.main()
