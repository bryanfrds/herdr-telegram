"""Talking to herdr and Telegram, and reading settings, with both faked."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from herdr_tg import herdr, telegram
from herdr_tg.__main__ import BAD_TOKEN, connect, handle_update, settings, skip_backlog, watch_once
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
        self.assertTrue(ctx.exception.bad_token)

    def test_only_a_refused_token_counts_as_a_bad_token(self):
        self.assertTrue(TelegramError("x", 404).bad_token)   # Telegram's answer to an unknown bot
        for status in (None, 429, 500, 502):
            self.assertFalse(TelegramError("x", status).bad_token)

    def test_updates_asks_telegram_to_wait_for_new_messages(self):
        with mock.patch.object(telegram.urllib.request, "urlopen",
                               return_value=FakeResponse({"ok": True, "result": [{"update_id": 1}]})
                               ) as op:
            got = Telegram("T").updates(offset=7, wait=25)
        self.assertEqual(got, [{"update_id": 1}])
        body = json.loads(op.call_args.args[0].data)
        self.assertEqual(body, {"timeout": 25, "offset": 7,
                                "allowed_updates": ["message", "callback_query"]})
        self.assertEqual(op.call_args.kwargs["timeout"], 35)   # HTTP waits past the long poll

    def test_buttons_go_under_the_last_part_only(self):
        with mock.patch.object(telegram.urllib.request, "urlopen",
                               return_value=FakeResponse({"ok": True, "result": {}})) as op:
            Telegram("T").send(1, "a" * 5000, [[("Pick", "use:w1:p1")]])
        bodies = [json.loads(c.args[0].data) for c in op.call_args_list]
        self.assertNotIn("reply_markup", bodies[0])
        self.assertEqual(bodies[-1]["reply_markup"],
                         {"inline_keyboard": [[{"text": "Pick", "callback_data": "use:w1:p1"}]]})

    def test_answer_and_command_menu_calls(self):
        with mock.patch.object(telegram.urllib.request, "urlopen",
                               return_value=FakeResponse({"ok": True, "result": True})) as op:
            Telegram("T").answer("cb1", "ok")
            Telegram("T").set_commands([("agents", "Pick an agent")])
        a, c = (json.loads(x.args[0].data) for x in op.call_args_list)
        self.assertEqual(a, {"callback_query_id": "cb1", "text": "ok"})
        self.assertEqual(c, {"commands": [{"command": "agents", "description": "Pick an agent"}]})

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

    def test_a_refused_token_stops_with_a_clear_message_instead_of_retrying(self):
        tg = mock.Mock()
        tg.updates.side_effect = TelegramError("getUpdates failed: HTTP 401", 401)
        retried = AssertionError("retried a refused token")
        with mock.patch("sys.stderr") as err, mock.patch("time.sleep", side_effect=retried):
            with self.assertRaises(SystemExit) as ctx:
                connect(tg)
        self.assertEqual(ctx.exception.code, BAD_TOKEN)
        self.assertIn("@BotFather", "".join(c.args[0] for c in err.write.call_args_list))

    def test_startup_waits_out_the_network_then_carries_on(self):
        tg = mock.Mock()
        tg.updates.side_effect = [TelegramError("getUpdates failed: URLError"),
                                  TelegramError("getUpdates failed: HTTP 502", 502),
                                  [{"update_id": 9}]]
        with mock.patch("sys.stderr"), mock.patch("time.sleep") as sleep:
            self.assertEqual(connect(tg), 10)
        self.assertEqual(sleep.call_count, 2)

    def test_a_failed_command_menu_never_blocks_startup(self):
        tg = mock.Mock()
        tg.updates.return_value = []
        tg.set_commands.side_effect = TelegramError("setMyCommands failed: HTTP 400", 400)
        with mock.patch("sys.stderr"), mock.patch("time.sleep") as sleep:
            self.assertIsNone(connect(tg))
        tg.updates.assert_called_once()
        sleep.assert_not_called()



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

    def test_a_lock_guards_the_state_both_threads_share(self):
        from herdr_tg.bot import Bot
        bot = Bot(1, herdr=mock.Mock())
        with bot.lock:   # held by "the other thread"
            done = []
            t = __import__("threading").Thread(target=lambda: done.append(bot.commit(mock.Mock(pane="p", status="idle"))))
            t.start()
            t.join(0.2)
            self.assertEqual(done, [])   # commit waited for the lock
        t.join(1)
        self.assertEqual(done, [None])

    def test_updates_without_a_chat_are_skipped(self):
        bot, tg = mock.Mock(), mock.Mock()
        handle_update(bot, tg, {"update_id": 1})
        handle_update(bot, tg, {"update_id": 2, "message": {"text": "hi"}})
        bot.handle_message.assert_not_called()
        tg.send.assert_not_called()

    def test_a_tap_is_always_answered_and_its_reply_sent_with_buttons(self):
        from herdr_tg.bot import Reply
        bot, tg = mock.Mock(), mock.Mock()
        bot.handle_callback.return_value = ("rex", Reply("Now talking", [[("Read", "read:p")]]))
        handle_update(bot, tg, {"update_id": 1, "callback_query": {
            "id": "cb9", "data": "use:p", "message": {"chat": {"id": 5}}}})
        tg.answer.assert_called_once_with("cb9", "rex")
        tg.send.assert_called_once_with(5, "Now talking", [[("Read", "read:p")]])
        bot.handle_callback.side_effect = ValueError("boom")   # still answered
        handle_update(bot, tg, {"update_id": 2, "callback_query": {"id": "cb10", "message": {}}})
        tg.answer.assert_called_with("cb10", "Something went wrong")

    def test_a_tap_that_cant_be_acknowledged_still_gets_its_reply(self):
        bot, tg = mock.Mock(), mock.Mock()
        bot.handle_callback.return_value = (None, "screen")
        tg.answer.side_effect = TelegramError("answerCallbackQuery failed: HTTP 400", 400)
        with mock.patch("sys.stderr"):
            handle_update(bot, tg, {"update_id": 1, "callback_query": {
                "id": "old", "data": "read:p", "message": {"chat": {"id": 5}}}})
        tg.send.assert_called_once_with(5, "screen")

    def test_a_crash_handling_one_message_is_reported_not_fatal(self):
        bot, tg = mock.Mock(), mock.Mock()
        bot.handle_message.side_effect = ValueError("boom")
        handle_update(bot, tg, {"update_id": 1, "message": {"chat": {"id": 5}, "text": "x"}})
        self.assertIn("still running", tg.send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
