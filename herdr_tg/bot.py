"""What the bot does with each message, and the watcher that reports finished agents."""

from __future__ import annotations

import time
from dataclasses import dataclass

from herdr_tg import herdr as default_herdr

HELP = """Talk to your herdr agents from here.

/agents  list agents and what they're doing
/use 2  pick an agent; plain messages then go to it
/to 2 fix the login bug  send one prompt to an agent
/read  its latest screen (or /read 2, /read 2 60)
/help  this message

Agents are named by their herdr space: the number, or the start of the name ("rex a").
You'll get a message when an agent finishes or needs you."""

ICONS = {"working": "⏳", "idle": "✅", "done": "✅", "blocked": "✋", "unknown": "·"}
# A change into one of these, from working, is worth a message.
NOTIFY = {"idle": "finished", "done": "finished", "blocked": "needs your input"}
# How long to wait for a prompted agent to show its new turn before giving up on it.
PROMPT_GRACE = 120


@dataclass
class Notice:
    pane: str
    status: str
    text: str


class Bot:
    def __init__(self, owner_chat: int | None, herdr=default_herdr, clock=time.monotonic):
        self.owner = owner_chat
        self.herdr = herdr
        self.clock = clock
        self.current: str | None = None   # pane of the agent picked with /use
        self.last_status: dict[str, str] = {}
        # Prompted agents whose new turn hasn't shown up yet: pane -> (seq at send, time).
        self.expecting: dict[str, tuple[int, float]] = {}
        # What each number meant in the last /agents, to catch numbers that moved since.
        self.shown: dict[int, str] = {}

    def handle_message(self, msg: dict) -> str | None:
        """The reply to one Telegram message, or None to stay silent."""
        chat = msg.get("chat") or {}
        sender = (msg.get("from") or {}).get("id")
        if chat.get("type") != "private":
            return None   # never in groups: every member there could type into agents
        if self.owner is None:
            # Not paired yet: tell whoever messaged their chat id so they can set it.
            return (f"This bot isn't paired yet. Your chat id is {chat.get('id')}.\n"
                    f"Add HERDR_TG_CHAT_ID={chat.get('id')} to the config and restart it.")
        if chat.get("id") != self.owner or sender != self.owner:
            return None   # strangers get nothing, not even an error
        if any(k in msg for k in ("forward_origin", "forward_from", "forward_date", "via_bot")):
            return "Forwarded messages aren't sent to agents. Type it yourself."
        return self.handle(msg.get("text") or "")

    def handle(self, text: str) -> str | None:
        text = text.strip()
        if not text:
            return None
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()   # "/read@MyBot"
        try:
            if command in ("/start", "/help"):
                return HELP
            if command == "/agents":
                return self.list_agents()
            if command == "/use":
                return self.use(rest)
            if command == "/to":
                if len(rest.split()) < 2:
                    return "Usage: /to 2 what you want it to do"
                agent, prompt = self.herdr.split_ref(self.herdr.agents(), rest)
                self.check_number(rest.split()[0], agent)
                return self.send(agent, prompt)
            if command == "/read":
                return self.read(rest)
            if command.startswith("/"):
                return f"Unknown command {command}. Send /help."
            if self.current is None:
                return "Pick an agent first with /use 2, or send /to 2 your message."
            return self.send(self.pick_current(), text)
        except LookupError as e:
            return str(e)
        except self.herdr.HerdrError as e:
            return f"herdr: {e}"

    def check_number(self, ref: str, agent) -> None:
        """Refuse a number that now points at a different agent than /agents showed:
        a space closed in between shifts the numbers."""
        if ref.isdecimal() and int(ref) in self.shown and self.shown[int(ref)] != agent.pane:
            raise LookupError(f"Space numbers have changed since your last /agents. "
                              f"{ref} is now {agent.name()}. Send /agents and try again.")

    def pick(self, ref: str):
        agent = self.herdr.find(self.herdr.agents(), ref)
        self.check_number(ref.strip(), agent)
        return agent

    def pick_current(self):
        for a in self.herdr.agents():
            if a.pane == self.current:
                return a
        self.current = None
        raise LookupError("The agent you picked has gone. Send /agents and /use another.")

    def list_agents(self) -> str:
        found = self.herdr.agents()
        self.shown = {a.number: a.pane for a in found}
        if not found:
            return "No agents are running in herdr."
        return "\n".join(f"{ICONS.get(a.status, '·')} {a.name()} ({a.status})"
                         + (" ← current" if a.pane == self.current else "") for a in found)

    def use(self, ref: str) -> str:
        if not ref.strip():
            return "Usage: /use 2 (or the start of a name)"
        agent = self.pick(ref)
        self.current = agent.pane
        return f"Now talking to {agent.name()}. Plain messages go there."

    def send(self, agent, prompt: str) -> str:
        if agent.status == "blocked":
            return (f"{agent.name()} is waiting on a question on screen. "
                    f"/read {agent.number} to see it.")
        self.herdr.prompt(agent, prompt)
        # Watch for this turn: report it once herdr shows a change, even a quick one.
        self.expecting[agent.pane] = (agent.seq, self.clock())
        return f"Sent to {agent.name()}. I'll tell you when it's done."

    def read(self, rest: str) -> str:
        args = rest.split()
        lines = 30
        if len(args) >= 2 and args[-1].isdecimal():
            lines = min(int(args.pop()), 200)
        if args:
            agent = self.pick(" ".join(args))
        elif self.current:
            agent = self.pick_current()
        else:
            return "Which one? /read 2, or /use 2 first."
        screen = self.herdr.read(agent, lines) or "(empty screen)"
        return f"{agent.name()}:\n\n{screen}"

    def prime(self) -> None:
        """Learn every agent's state without reporting anything."""
        for a in self.herdr.agents():
            self.last_status[a.pane] = a.status

    def changes(self) -> list[Notice]:
        """Agents that stopped working since the last check. Call commit() for each one
        once it has been delivered; an undelivered one is offered again next time."""
        out = []
        for a in self.herdr.agents():
            before = self.last_status.get(a.pane)
            if a.pane in self.expecting:
                seq, since = self.expecting[a.pane]
                if a.seq == seq and a.status != "working":
                    # herdr hasn't shown the new turn yet; "idle" here is the old state.
                    if self.clock() - since > PROMPT_GRACE:
                        del self.expecting[a.pane]
                        self.last_status[a.pane] = a.status
                    continue
                del self.expecting[a.pane]
                before = self.last_status[a.pane] = "working"
            if before == "working" and a.status in NOTIFY:
                try:
                    tail = "\n\n" + self.herdr.read(a, 12)
                except self.herdr.HerdrError:
                    tail = ""
                out.append(Notice(a.pane, a.status,
                                  f"{ICONS[a.status]} {a.name()} {NOTIFY[a.status]}.{tail}"))
            else:
                self.last_status[a.pane] = a.status
        return out

    def commit(self, notice: Notice) -> None:
        self.last_status[notice.pane] = notice.status
