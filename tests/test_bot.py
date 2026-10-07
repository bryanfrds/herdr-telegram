"""The bot's replies and notifications, against a fake herdr."""

from __future__ import annotations

import unittest

from herdr_tg import herdr as real_herdr
from herdr_tg.bot import Bot
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

    def agents(self):
        if self.fail:
            raise real_herdr.HerdrError("server not running")
        return list(self.list)

    def read(self, agent, lines=30):
        return self.screens.get(agent.pane, f"screen of {agent.label}")

    def prompt(self, agent, text):
        self.prompts.append((agent.pane, text))

    def set_status(self, pane, status):
        self.list = [Agent(a.number, a.label, a.pane, status) if a.pane == pane else a
                     for a in self.list]


def fleet():
    return FakeHerdr(Agent(1, "Claude Maxxin", "w1:p1", "working"),
                     Agent(2, "rex applicant", "w2:p1", "idle"),
                     Agent(3, "rex", "w3:p1", "idle"))


class Security(unittest.TestCase):
    def test_strangers_get_no_reply_and_nothing_runs(self):
        h = fleet()
        bot = Bot(OWNER, h)
        for text in ("/agents", "/to 2 rm -rf", "/read 2", "/use 2", "hello"):
            self.assertIsNone(bot.handle(999, text))
        self.assertEqual(h.prompts, [])

    def test_an_unpaired_bot_only_tells_you_your_chat_id(self):
        h = fleet()
        reply = Bot(None, h).handle(555, "/to 2 do something")
        self.assertIn("555", reply)
        self.assertEqual(h.prompts, [])


class Commands(unittest.TestCase):
    def setUp(self):
        self.h = fleet()
        self.bot = Bot(OWNER, self.h)

    def say(self, text):
        return self.bot.handle(OWNER, text)

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
        reply = self.say("/to rex a quick fix")       # "rex" + prompt, or "rex a(pplicant)"?
        self.assertIn("Did you mean", reply)
        self.assertEqual(self.h.prompts, [])

    def test_a_number_is_never_ambiguous(self):
        self.say("/to 3 a quick fix")
        self.assertEqual(self.h.prompts, [("w3:p1", "a quick fix")])

    def test_the_longest_whole_name_wins(self):
        self.h.list.append(Agent(4, "rex vms", "w4:p1", "idle"))
        self.say("/to rex vms ship it")
        self.assertEqual(self.h.prompts, [("w4:p1", "ship it")])
        self.assertIn("Did you mean", self.say("/to rex v ship it"))   # rex + "v ship it"?
        self.assertEqual(len(self.h.prompts), 1)

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

    def test_read_shows_the_screen_and_caps_lines(self):
        self.h.screens["w2:p1"] = "❯ merge it"
        self.assertIn("❯ merge it", self.say("/read 2"))
        self.say("/use 2")
        self.assertIn("❯ merge it", self.say("/read"))

    def test_unknown_agent_and_command_are_explained(self):
        self.assertIn("no agent matches", self.say("/to 9 hi"))
        self.assertIn("Unknown command", self.say("/nope"))

    def test_herdr_being_down_is_reported_not_raised(self):
        self.h.fail = True
        self.assertIn("server not running", self.say("/agents"))

    def test_bot_suffix_in_group_commands_is_ignored(self):
        self.assertIn("Claude Maxxin", self.say("/agents@MyHerdrBot"))

    def test_the_picked_agent_going_away_is_handled(self):
        self.say("/use 2")
        self.h.list = [a for a in self.h.list if a.pane != "w2:p1"]
        self.assertIn("has gone", self.say("hello"))


class Notifications(unittest.TestCase):
    def test_finishing_and_blocking_are_reported_once(self):
        h = fleet()
        bot = Bot(OWNER, h)
        self.assertEqual(bot.changes(), [])            # first look: learn states quietly
        h.set_status("w1:p1", "idle")
        msgs = bot.changes()
        self.assertEqual(len(msgs), 1)
        self.assertIn("Claude Maxxin finished", msgs[0])
        self.assertEqual(bot.changes(), [])            # not repeated
        h.set_status("w1:p1", "working")
        bot.changes()
        h.set_status("w1:p1", "blocked")
        self.assertIn("needs your input", bot.changes()[0])

    def test_an_agent_you_prompt_is_watched_even_if_it_finishes_fast(self):
        h = fleet()
        bot = Bot(OWNER, h)
        bot.changes()
        bot.handle(OWNER, "/to 2 quick one")           # idle → working → idle between checks
        self.assertIn("rex applicant finished", bot.changes()[0])

    def test_idle_agents_staying_idle_say_nothing(self):
        h = fleet()
        bot = Bot(OWNER, h)
        bot.changes()
        self.assertEqual(bot.changes(), [])


if __name__ == "__main__":
    unittest.main()
