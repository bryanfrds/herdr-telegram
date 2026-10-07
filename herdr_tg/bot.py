"""What the bot does with each message, and the watcher that reports finished agents."""

from __future__ import annotations

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


class Bot:
    def __init__(self, owner_chat: int | None, herdr=default_herdr):
        self.owner = owner_chat
        self.herdr = herdr
        self.current: str | None = None   # pane of the agent picked with /use
        self.last_status: dict[str, str] = {}

    def handle(self, chat_id: int, text: str) -> str | None:
        """The reply to one message, or None to stay silent."""
        if self.owner is None:
            # Not paired yet: tell whoever messaged their chat id so they can set it.
            return (f"This bot isn't paired yet. Your chat id is {chat_id}.\n"
                    f"Add HERDR_TG_CHAT_ID={chat_id} to the config and restart it.")
        if chat_id != self.owner:
            return None   # strangers get nothing, not even an error
        text = (text or "").strip()
        if not text:
            return None
        command, _, rest = text.partition(" ")
        command = command.split("@")[0].lower()   # "/read@MyBot" in groups
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

    def pick(self, ref: str):
        return self.herdr.find(self.herdr.agents(), ref)

    def pick_current(self):
        for a in self.herdr.agents():
            if a.pane == self.current:
                return a
        self.current = None
        raise LookupError("The agent you picked has gone. Send /agents and /use another.")

    def list_agents(self) -> str:
        found = self.herdr.agents()
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
        self.last_status[agent.pane] = "working"
        return f"Sent to {agent.name()}. I'll tell you when it's done."

    def read(self, rest: str) -> str:
        args = rest.split()
        lines = 30
        if len(args) >= 2 and args[-1].isdigit():
            lines = min(int(args.pop()), 200)
        if args:
            agent = self.pick(" ".join(args))
        elif self.current:
            agent = self.pick_current()
        else:
            return "Which one? /read 2, or /use 2 first."
        screen = self.herdr.read(agent, lines) or "(empty screen)"
        return f"{agent.name()}:\n\n{screen}"

    def changes(self) -> list[str]:
        """Messages for agents that stopped working since the last check."""
        out = []
        for a in self.herdr.agents():
            before = self.last_status.get(a.pane)
            self.last_status[a.pane] = a.status
            if before == "working" and a.status in NOTIFY:
                tail = self.herdr.read(a, 12)
                out.append(f"{ICONS[a.status]} {a.name()} {NOTIFY[a.status]}.\n\n{tail}")
        return out
