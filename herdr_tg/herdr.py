"""Talk to the local herdr server through its command-line API."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


class HerdrError(RuntimeError):
    """herdr is missing, not running, or refused the request."""


@dataclass(frozen=True)
class Agent:
    number: int      # the workspace number shown in herdr's sidebar
    label: str       # the workspace name, e.g. "rex applicant"
    pane: str        # what herdr's agent commands take as a target, e.g. "wY:p1"
    status: str      # idle, working, blocked, done or unknown
    seq: int = 0     # herdr's state_change_seq: goes up whenever the status changes

    def name(self) -> str:
        return f"{self.number} · {self.label}"


def _run(args: list[str], timeout: float = 15) -> str:
    try:
        out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise HerdrError("herdr isn't installed or isn't on PATH") from None
    except subprocess.TimeoutExpired:
        raise HerdrError(f"herdr {args[0]} {args[1]} took too long") from None
    if out.returncode != 0:
        raise HerdrError((out.stderr or out.stdout).strip() or f"herdr exited {out.returncode}")
    return out.stdout


def _json(args: list[str]) -> dict:
    try:
        return json.loads(_run(args))["result"]
    except (ValueError, KeyError) as e:
        raise HerdrError(f"unexpected reply from herdr {' '.join(args)}") from e


def agents() -> list[Agent]:
    """Every agent pane, named after its workspace, in sidebar order."""
    spaces = {w["workspace_id"]: w for w in _json(["workspace", "list"])["workspaces"]}
    found = []
    for a in _json(["agent", "list"])["agents"]:
        space = spaces.get(a["workspace_id"], {})
        found.append(Agent(
            number=space.get("number", 0),
            label=space.get("label") or a.get("terminal_title_stripped") or a["pane_id"],
            pane=a["pane_id"],
            status=a.get("agent_status", "unknown"),
            seq=int(a.get("state_change_seq") or 0),
        ))
    return sorted(found, key=lambda a: (a.number, a.pane))


def _norm(name: str) -> str:
    """Lower case, single spaces: "Rex  Boi" and "rex boi" are the same name."""
    return " ".join(name.lower().split())


def find(all_agents: list[Agent], ref: str) -> Agent:
    """An agent by workspace number ("2") or by the start of its name ("rex a")."""
    ref = _norm(ref)
    if ref.isdecimal():
        hits = [a for a in all_agents if a.number == int(ref)]
    else:
        hits = [a for a in all_agents if _norm(a.label) == ref] or \
               [a for a in all_agents if _norm(a.label).startswith(ref)]
    if not hits:
        raise LookupError(f"no agent matches {ref!r}; send /agents to see them")
    if len(hits) > 1:
        raise LookupError(f"{ref!r} matches {', '.join(a.name() for a in hits)}; be more specific")
    return hits[0]


def split_ref(all_agents: list[Agent], words: str) -> tuple[Agent, str]:
    """Split "rex a add a test" into the agent it starts with and the rest.

    Names can have spaces, so any run of leading words may be the name. If the words
    could name two different agents ("rex a quick fix": "rex", or "rex a" for
    rex applicant), it asks rather than guesses: a prompt sent to the wrong project
    can do real damage.
    """
    parts = words.split()
    exact: tuple[Agent, str] | None = None        # longest run that is a whole name
    partial: dict[str, tuple[int, Agent, str]] = {}   # per agent, its longest prefix run
    for i in range(1, len(parts)):
        ref = " ".join(parts[:i])
        try:
            agent = find(all_agents, ref)
        except LookupError:
            continue
        # The prompt is the original text after the name, newlines and all.
        rest = re.match(r"\s*(?:\S+\s+){%d}" % i, words).end()
        rest = words[rest:]
        if ref.isdecimal() or _norm(agent.label) == _norm(ref):
            exact = (agent, rest)
            partial = {k: v for k, v in partial.items() if v[0] > i}
        else:
            partial[agent.pane] = (i, agent, rest)
    others = [a for _, a, _ in partial.values() if exact is None or a.pane != exact[0].pane]
    if exact and not others:
        return exact
    if not exact and len(partial) == 1:
        _, agent, rest = next(iter(partial.values()))
        return agent, rest
    if exact or partial:
        names = " or ".join(a.name() for a in ([exact[0]] if exact else []) + others)
        raise LookupError(f"Did you mean {names}? Use the number, e.g. /to 2 ...")
    find(all_agents, parts[0] if parts else "")   # explains "no match" / "be more specific"
    raise LookupError("say which agent")


def read(agent: Agent, lines: int = 30) -> str:
    return _run(["agent", "read", agent.pane, "--lines", str(lines)]).rstrip()


def prompt(agent: Agent, text: str) -> None:
    _run(["agent", "prompt", agent.pane, text])
