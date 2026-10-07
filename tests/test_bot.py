"""The bot's replies and notifications, against a fake herdr."""

from __future__ import annotations

import unittest

from herdr_tg import herdr as real_herdr
from herdr_tg.bot import PROMPT_GRACE, Bot
from herdr_tg.herdr import Agent

OWNER = 111


class FakeHerdr:
    """Stands in for the herdr module: the same functions, no real panes."""

    HerdrError = real_herdr.HerdrError
    find = staticmethod(real_herdr.find)
    split_ref = staticmethod(real_herdr.split_ref)

    def __init__(self, *agents: Agent):
        self.list = list(agents)
        self.prompts: list[tuple[str, str]] = []
        self.screens: dict[str, str] = {}
        self.fail = False
        self.read_fails = False

    def agents(self):
        if self.fail:
            raise real_herdr.HerdrError("server not running")
        return list(self.list)

    def read(self, agent, lines=30):
        if self.read_fails:
            raise real_herdr.HerdrError("read failed")
        return self.screens.get(agent.pane, f"screen of {agent.label}")

    def prompt(self, agent, text):
        self.prompts.append((agent.pane, text))

    def set_status(self, pane, status):
        """Like herdr: a status change bumps that agent's state_change_seq."""
        self.list = [Agent(a.number, a.label, a.pane, status, a.seq + 1) if a.pane == pane else a
                     for a in self.list]


def fleet():
    return FakeHerdr(Agent(1, "Claude Maxxin", "w1:p1", "working", 10),
                     Agent(2, "rex applicant", "w2:p1", "idle", 20),
                     Agent(3, "rex", "w3:p1", "idle", 30))


def msg(text, chat=OWNER, sender=OWNER, kind="private", **extra):
    return {"chat": {"id": chat, "type": kind}, "from": {"id": sender}, "text": text, **extra}


class Security(unittest.TestCase):
    def setUp(self):
        self.h = fleet()
        self.bot = Bot(OWNER, self.h)

    def test_strangers_get_no_reply_and_nothing_runs(self):
        for text in ("/agents", "/to 2 rm -rf", "/read 2", "/use 2", "hello"):
            self.assertIsNone(self.bot.handle_message(msg(text, chat=999, sender=999)))
        self.assertEqual(self.h.prompts, [])

    def test_groups_are_ignored_even_with_the_owners_id(self):
        # Pairing from a group would let every member type into agents.
        self.assertIsNone(self.bot.handle_message(msg("/to 2 hi", kind="group")))
        self.assertIsNone(self.bot.handle_message(msg("/to 2 hi", kind="supergroup")))
        self.assertEqual(self.h.prompts, [])

    def test_the_sender_must_be_the_owner_too(self):
        self.assertIsNone(self.bot.handle_message(msg("/to 2 hi", sender=999)))
        self.assertEqual(self.h.prompts, [])

    def test_forwarded_and_via_bot_messages_are_not_prompts(self):
        self.bot.handle_message(msg("/use 2"))
        for extra in ({"forward_origin": {"type": "user"}}, {"forward_from": {"id": 5}},
                      {"via_bot": {"id": 7}}):
            self.assertIn("Forwarded", self.bot.handle_message(msg("delete prod", **extra)))
        self.assertEqual(self.h.prompts, [])

    def test_an_unpaired_bot_only_tells_you_your_chat_id(self):
        bot = Bot(None, self.h)
        self.assertIn("555", bot.handle_message(msg("/to 2 do it", chat=555, sender=555)))
        self.assertIsNone(bot.handle_message(msg("/to 2 do it", chat=-5, kind="group")))
        self.assertEqual(self.h.prompts, [])


class Commands(unittest.TestCase):
    def setUp(self):
        self.h = fleet()
        self.bot = Bot(OWNER, self.h)

    def say(self, text):
        return self.bot.handle_message(msg(text))

    def test_agents_lists_each_with_its_status(self):
        reply = self.say("/agents")
        self.assertIn("1 · Claude Maxxin (working)", reply)
        self.assertIn("2 · rex applicant (idle)", reply)

    def test_to_sends_a_prompt_by_number_or_name(self):
        self.say("/to 2 fix the login bug")
        self.say("/to rex applicant add a test")
        self.assertEqual(self.h.prompts, [("w2:p1", "fix the login bug"), ("w2:p1", "add a test")])

    def test_a_partial_name_used_by_one_agent_only_needs_no_number(self):
        self.say("/to claude max run the tests")
        self.say("/to cla run the tests")
        self.assertEqual(self.h.prompts, [("w1:p1", "run the tests")] * 2)

    def test_an_exact_name_beats_a_longer_one_starting_the_same(self):
        self.say("/to rex check the build")
        self.assertEqual(self.h.prompts, [("w3:p1", "check the build")])

    def test_an_ambiguous_name_asks_you_to_be_specific(self):
        self.assertIn("be more specific", self.say("/to re go"))   # rex applicant, rex
        self.assertEqual(self.h.prompts, [])

    def test_words_that_could_name_two_agents_are_not_guessed(self):
        self.assertIn("Did you mean", self.say("/to rex a quick fix"))
        self.assertEqual(self.h.prompts, [])

    def test_a_number_is_never_ambiguous(self):
        self.say("/to 3 a quick fix")
        self.assertEqual(self.h.prompts, [("w3:p1", "a quick fix")])

    def test_the_longest_whole_name_wins(self):
        self.h.list.append(Agent(4, "rex vms", "w4:p1", "idle"))
        self.say("/to rex vms ship it")
        self.assertEqual(self.h.prompts, [("w4:p1", "ship it")])
        self.assertIn("Did you mean", self.say("/to rex v ship it"))
        self.assertEqual(len(self.h.prompts), 1)

    def test_names_match_whatever_the_spacing(self):
        self.h.list.append(Agent(4, "rex  boi", "w4:p1", "idle"))   # two spaces in herdr
        self.say("/to rex boi fix it")
        self.assertEqual(self.h.prompts, [("w4:p1", "fix it")])

    def test_prompts_keep_their_line_breaks_and_spacing(self):
        self.say("/to 2 fix this:\n\n    def f():\n        pass")
        self.assertEqual(self.h.prompts, [("w2:p1", "fix this:\n\n    def f():\n        pass")])

    def test_to_without_a_prompt_explains_itself(self):
        self.assertIn("Usage", self.say("/to 2"))
        self.assertEqual(self.h.prompts, [])

    def test_use_then_plain_messages_go_to_that_agent(self):
        self.assertIn("Now talking to 2 · rex applicant", self.say("/use 2"))
        self.say("run the tests")
        self.assertEqual(self.h.prompts, [("w2:p1", "run the tests")])

    def test_plain_message_without_use_asks_which_agent(self):
        self.assertIn("/use", self.say("run the tests"))
        self.assertEqual(self.h.prompts, [])

    def test_a_blocked_agent_is_not_sent_more_text(self):
        self.h.set_status("w2:p1", "blocked")
        self.assertIn("waiting on a question", self.say("/to 2 yes"))
        self.assertEqual(self.h.prompts, [])

    def test_read_shows_the_screen(self):
        self.h.screens["w2:p1"] = "❯ merge it"
        self.assertIn("❯ merge it", self.say("/read 2"))
        self.say("/use 2")
        self.assertIn("❯ merge it", self.say("/read"))

    def test_odd_digits_are_handled_not_crashed_on(self):
        self.assertIn("no agent matches", self.say("/to ² hi"))
        self.assertIn("no agent matches", self.say("/read 2 ²"))   # "²" isn't a line count

    def test_a_number_that_moved_since_agents_is_refused(self):
        self.say("/agents")                     # 2 is rex applicant here
        self.h.list = [a for a in self.h.list if a.pane != "w2:p1"]
        self.h.list = [Agent(2, "rex", "w3:p1", "idle", 30) if a.pane == "w3:p1" else a
                       for a in self.h.list]   # a space closed; rex is now 2
        self.assertIn("numbers have changed", self.say("/to 2 deploy"))
        self.assertIn("numbers have changed", self.say("/use 2"))
        self.assertEqual(self.h.prompts, [])
        self.say("/agents")
        self.say("/to 2 deploy")
        self.assertEqual(self.h.prompts, [("w3:p1", "deploy")])

    def test_unknown_agent_and_command_are_explained(self):
        self.assertIn("no agent matches", self.say("/to 9 hi"))
        self.assertIn("Unknown command", self.say("/nope"))

    def test_herdr_being_down_is_reported_not_raised(self):
        self.h.fail = True
        self.assertIn("server not running", self.say("/agents"))

    def test_bot_suffix_on_commands_is_ignored(self):
        self.assertIn("Claude Maxxin", self.say("/agents@MyHerdrBot"))

    def test_the_picked_agent_going_away_is_handled(self):
        self.say("/use 2")
        self.h.list = [a for a in self.h.list if a.pane != "w2:p1"]
        self.assertIn("has gone", self.say("hello"))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Notifications(unittest.TestCase):
    def setUp(self):
        self.h = fleet()
        self.clock = Clock()
        self.bot = Bot(OWNER, self.h, clock=self.clock)
        self.bot.prime()

    def deliver(self):
        """What the watcher sends, committing each one as delivered."""
        notices = self.bot.changes()
        for n in notices:
            self.bot.commit(n)
        return [n.text for n in notices]

    def test_finishing_and_blocking_are_reported_once(self):
        self.h.set_status("w1:p1", "idle")
        sent = self.deliver()
        self.assertEqual(len(sent), 1)
        self.assertIn("Claude Maxxin finished", sent[0])
        self.assertEqual(self.deliver(), [])
        self.h.set_status("w1:p1", "working")
        self.deliver()
        self.h.set_status("w1:p1", "blocked")
        self.assertIn("needs your input", self.deliver()[0])

    def test_no_false_finish_before_the_prompted_agent_starts(self):
        # herdr may still say "idle" for a moment after accepting a prompt.
        self.bot.handle_message(msg("/to 2 long job"))
        self.assertEqual(self.deliver(), [])
        self.h.set_status("w2:p1", "working")
        self.assertEqual(self.deliver(), [])
        self.h.set_status("w2:p1", "idle")
        self.assertIn("rex applicant finished", self.deliver()[0])

    def test_a_job_that_finishes_between_checks_is_still_reported(self):
        self.bot.handle_message(msg("/to 2 quick one"))
        self.h.set_status("w2:p1", "working")
        self.h.set_status("w2:p1", "idle")       # never seen working by the watcher
        self.assertIn("rex applicant finished", self.deliver()[0])

    def test_a_prompt_that_never_starts_is_dropped_quietly(self):
        self.bot.handle_message(msg("/to 2 hello?"))
        self.clock.now = PROMPT_GRACE + 1
        self.assertEqual(self.deliver(), [])
        self.assertNotIn("w2:p1", self.bot.expecting)

    def test_an_undelivered_notice_is_offered_again(self):
        self.h.set_status("w1:p1", "idle")
        self.assertEqual(len(self.bot.changes()), 1)   # send failed: not committed
        self.assertEqual(len(self.bot.changes()), 1)   # so it comes back
        self.assertEqual(len(self.deliver()), 1)
        self.assertEqual(self.deliver(), [])

    def test_a_failed_screen_read_still_sends_the_notice(self):
        self.h.read_fails = True
        self.h.set_status("w1:p1", "idle")
        self.assertEqual(self.deliver(), ["✅ 1 · Claude Maxxin finished."])

    def test_idle_agents_staying_idle_say_nothing(self):
        self.assertEqual(self.deliver(), [])


if __name__ == "__main__":
    unittest.main()
