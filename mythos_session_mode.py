#!/usr/bin/env python3
"""Mythos session modes: Talk / Work / Research + stuck recovery."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT

STATE_DIR = Path(APEX_ROOT) / "mythos_state"
MODE_PATH = STATE_DIR / "session_mode.json"
GOALS_PATH = STATE_DIR / "presence_goals.json"

VALID_MODES = ("talk", "work", "research")
DEFAULT_MODE = "talk"

_MODE_SWITCH = re.compile(
    r"(?i)^\s*(?:switch\s+to\s+|set\s+mode\s+|mode\s*[:=]?\s*|go\s+to\s+)?"
    r"(talk|chat|hang\s*out|work|build|ops|research|lookup|study)"
    r"(?:\s+mode)?\s*[.!?]?\s*$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if MODE_PATH.is_file():
        try:
            data = json.loads(MODE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("mode") in VALID_MODES:
                return data
        except Exception:
            pass
    return {"mode": DEFAULT_MODE, "updated": _now(), "note": "default work mode"}


def _save(data: dict) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    MODE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def get_mode() -> str:
    return _load().get("mode") or DEFAULT_MODE


def get_mode_state() -> dict:
    data = _load()
    data["valid"] = list(VALID_MODES)
    data["labels"] = {
        "talk": "Conversation first — tools OK when you ask to look up, watch, download, or act",
        "work": "Build / move / repair — act with tools",
        "research": "Look things up — search then answer",
    }
    return data


def set_mode(mode: str, note: str = "") -> dict:
    m = (mode or "").strip().lower()
    aliases = {
        "chat": "talk",
        "hangout": "talk",
        "hang out": "talk",
        "build": "work",
        "ops": "work",
        "lookup": "research",
        "study": "research",
    }
    m = aliases.get(m, m)
    if m not in VALID_MODES:
        return {"ok": False, "error": f"mode must be one of {VALID_MODES}", "mode": get_mode()}
    data = {"mode": m, "note": (note or "")[:200], "updated": _now()}
    _save(data)
    data["ok"] = True
    data["message"] = {
        "talk": "Talk mode — I'll converse. If you ask to look something up, watch a video, or act, I use tools.",
        "work": "Work mode — I'll act with tools and keep reports short.",
        "research": "Research mode — I'll look things up and answer with sources.",
    }[m]
    return data


def parse_mode_switch(text: str) -> str | None:
    """If message is only a mode switch, return talk|work|research."""
    m = _MODE_SWITCH.match((text or "").strip())
    if not m:
        # also: "talk mode please"
        low = (text or "").strip().lower()
        for name in VALID_MODES:
            if low in {name, f"{name} mode", f"{name} mode please", f"just {name}"}:
                return name
        if low in {"just chat", "let's talk", "lets talk", "hang out"}:
            return "talk"
        return None
    raw = m.group(1).lower().replace(" ", "")
    return set_mode(raw).get("mode") if set_mode(raw).get("ok") else None


def try_switch_from_message(text: str) -> dict | None:
    """Detect and apply mode switch. Returns result dict or None."""
    m = _MODE_SWITCH.match((text or "").strip())
    low = (text or "").strip().lower()
    target = None
    if m:
        target = m.group(1)
    elif low in {"talk", "talk mode", "chat", "chat mode", "just chat", "let's talk", "lets talk", "hang out"}:
        target = "talk"
    elif low in {"work", "work mode", "build mode", "ops mode"}:
        target = "work"
    elif low in {"research", "research mode", "lookup mode", "study mode"}:
        target = "research"
    if not target:
        return None
    return set_mode(target)


def mode_prompt_block(mode: str | None = None) -> str:
    mode = (mode or get_mode()).lower()
    cohesive = """
COHESIVE PEER DOCTRINE (always on — Talk/Work/Research are soft hints, not walls):
- Be like a capable peer: infer intent from typos and incomplete asks. Fill missing steps yourself.
- Never invent talk-only excuses, fake mode switches, or fake URLs from her sentences.
- Never dump D/E/G inventory for casual "explore" / "what now" / "keep going".
- If she asks to look up, watch, download, fix, build, or continue — USE TOOLS and finish the job.
- Honesty: only claim what tools actually did.
"""
    if mode == "talk":
        return cohesive + """
SESSION MODE: TALK (conversation — default; soft gate, not a wall)
- Priority: be present, malleable, conversational. Banter and opinions are OK.
- Talk / Work / Research overlap: if she asks to look something up, watch/download YouTube,
  explore the internet, or run a named tool — DO IT. Do not invent "talk-only" excuses.
- NEVER dump D/E/G drive inventory for casual "explore possibilities / explore the internet".
  Fleet explore is ONLY for disks, folders, storage, sanctuary layout.
- Do NOT tunnel into living_game / village / quest deliverables unless she explicitly asks to build that.
- Do NOT start relocates, game builds, heals, or autopilot from idle chat.
- Keep replies warm and capable — still Mythos. Meet the emotional beat first; then act when asked.
- Never mock by turning her sentence into a fake URL or filename.
"""
    if mode == "research":
        return cohesive + """
SESSION MODE: RESEARCH
- Priority: FIND facts yourself on the INTERNET for ANY topic (science, how-tos, history, people,
  schematics, news — not games-only). Prefer research.web / research.lookup.
- Never ask her for a URL. Never demand atom-level specs. Never turn research into an interview.
- Answer with what you found + sources. If something is ambiguous, pick the best match and say so in one line.
- Do NOT start file moves, game builds, or fleet relocates unless she clearly asks.
"""
    return cohesive + """
SESSION MODE: WORK (default ops)
- Priority: ACT with tools. Short reports of what you DID.
- No coaching checklists. No "you should verify" — you verify.
- NEVER interrogate her for molecular-level requirements. Assume defaults; look up the rest.
- When you don't know or local files fail: CALL research.web / coding.find_online YOURSELF.
  Never ask her to search the internet for you.
- Stay on the named job; park other threads if she says park / unstick.
"""


def disk_or_fleet_request(text: str) -> bool:
    """True when explore/atlas/sovereign disk tools are appropriate."""
    low = (text or "").lower()
    cues = (
        "drive",
        "drives",
        "disk",
        "storage",
        "folder size",
        "fleet",
        "atlas",
        "sovereign",
        "explore folders",
        "explore drives",
        "d:\\",
        "e:\\",
        "g:\\",
        "sanctuary",
        "how big",
        "free space",
        "disk space",
    )
    return any(c in low for c in cues)


def is_lookup_meta(text: str) -> bool:
    """True when the message is only 'look it up / google it' with no real topic."""
    low = (text or "").strip().lower().rstrip(".!?")
    if not low:
        return True
    if low in (
        "look it up",
        "look this up",
        "look that up",
        "google it",
        "google that",
        "google this",
        "search it",
        "look online",
        "did you look online",
        "did you google it",
        "go online",
        "look it up yourself",
        "you look it up",
        "search online",
    ):
        return True
    if re.fullmatch(
        r"(please\s+)?(google|search|look(\s+it)?\s+up)\s+(it|this|that)",
        low,
    ):
        return True
    if re.fullmatch(r"(did you\s+)?(look|search|google)(\s+it|\s+this|\s+that|\s+online).*", low):
        return True
    return False


def explicit_tool_request(text: str) -> bool:
    """True when Talk mode should still run tools."""
    low = (text or "").lower()
    if disk_or_fleet_request(low):
        return True
    markers = (
        " use tool",
        "run tool",
        "call tool",
        "look up",
        "look it up",
        "look this up",
        "look online",
        "google ",
        "google it",
        "search for",
        "search online",
        "research ",
        "find out",
        "scrape ",
        "heal ",
        "rebuild ",
        "gamecraft",
        "stackforge.",
        "studio.",
        "visionary.",
        "filesorter",
        "file sorter",
        "ai file sorter",
        "sort files",
        "sort my files",
        "please fix",
        "apply now",
        "execute now",
        "run stackforge",
        "move my ",
        "relocate ",
        "everything related",
        "every detail",
        "how do i ",
        "how to ",
        "what is ",
        "tell me about ",
        "schematic",
        "blueprint",
        "pull up ",
        "research.web",
        "research.lookup",
        "brain.heavy",
        "brain.think",
        "brain.ensure",
        "brain.escalate",
        "brain.status",
        "brain.ram",
        "heavy brain",
        "use colibri",
        "call colibri",
        "start colibri",
        "free ram",
        "check ram",
        "check the free ram",
        "available ram",
        "18 gb",
        "18gb",
        "execute brain",
        "call brain",
        "use brain",
        "youtube",
        "youtu.be",
        "visionary.dl",
        "visionary.yt",
        "visionary.learn",
        "visionary.search",
        "download video",
        "download youtube",
        "multiverse",
        "explore the internet",
        "explore internet",
        "from the internet",
        "pick a project",
        "search youtube",
        "youtube videos",
        "go online",
        "look things up",
        "colibri.master",
        "reality.solve",
        "reality.route",
        "reality.inventory",
        "reality machine",
        "reality.machine",
        "make it real",
        "genius agent",
        "expert fleet",
        "colibri.diagnose",
        "colibri.repair",
        "make colibri work",
        "fix colibri",
        "start colibri master",
        "bring up colibri",
        "keep going",
        "continue",
        "fix it",
        "fix this",
        "build it",
        "agent loop",
        "agent.loop",
        "until it works",
        "do what we were doing",
        "finish the job",
        "write code",
        "edit the",
        "investigate",
    )
    if any(m in low for m in markers):
        return True
    # Named tool form — require known prefixes (avoid matching i.e. / e.g.)
    if re.search(
        r"\b(stackforge|studio|visionary|gamecraft|mtp|coding|filesorter|brain|agent|research|browser|channel|video|doc)\.[a-z_]+\b",
        low,
    ):
        return True
    return False



def researchish_request(text: str) -> bool:
    """True when Talk should run lookup/video tools (modes are soft, not walls)."""
    low = (text or "").lower()
    if explicit_tool_request(low):
        return True
    cues = (
        "youtube",
        "youtu.be",
        "visionary.",
        "research.web",
        "look up",
        "look it up",
        "google",
        "search for",
        "find out",
        "go online",
        "the internet",
        "from the internet",
        "explore the internet",
        "pick a project",
        "download",
        "multiverse",
        "parallel universe",
        "tell me about",
        "what is ",
        "how do ",
        "how to ",
    )
    return any(c in low for c in cues)



def _goals_load() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if GOALS_PATH.is_file():
        try:
            data = json.loads(GOALS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"active_goals": [], "parked_goals": [], "current_goal": "", "updated": _now()}


def _goals_save(data: dict) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    GOALS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def get_current_goal() -> str:
    data = _goals_load()
    g = (data.get("current_goal") or "").strip()
    if g:
        return g
    active = data.get("active_goals") or []
    if active:
        return str(active[0])[:2000]
    return ""


def set_current_goal(goal: str, note: str = "") -> dict:
    data = _goals_load()
    goal = (goal or "").strip()[:2000]
    data["current_goal"] = goal
    if goal:
        active = list(data.get("active_goals") or [])
        if goal not in active:
            active.insert(0, goal)
        data["active_goals"] = active[:12]
    if note:
        data["goal_note"] = note[:200]
    return _goals_save(data)


def clear_current_goal() -> dict:
    data = _goals_load()
    data["current_goal"] = ""
    return _goals_save(data)


def continue_goal_request(text: str) -> bool:
    """True when she wants to resume the open job — not disk inventory."""
    low = (text or "").strip().lower()
    if disk_or_fleet_request(low):
        return False
    cues = (
        "keep going",
        "continue",
        "continue from here",
        "what now",
        "what's next",
        "whats next",
        "what do we do next",
        "next step",
        "so what now",
        "do what we were doing",
        "finish the job",
        "pick up where",
        "resume",
        "carry on",
    )
    return any(c in low for c in cues)


def coding_job_request(text: str) -> bool:
    """True when this should auto-route to agent.loop / coding.solve."""
    low = (text or "").lower()
    if disk_or_fleet_request(low):
        return False
    cues = (
        "agent.loop",
        "agent loop",
        "until it works",
        "cursor-style",
        "cursor style",
        "write a program",
        "write code",
        "fix the code",
        "fix this bug",
        "build a",
        "implement ",
        "refactor ",
        "debug ",
        "edit the file",
        "edit this file",
        "coding.solve",
        "solve:",
        "program:",
        "multi-step",
        "autonomous coding",
    )
    if any(c in low for c in cues):
        return True
    if re.search(r"(fix|build|implement|debug|refactor).+(code|bug|script|module|function|file|project)", low):
        return True
    return False


def cohesive_should_act(text: str, session_mode: str | None = None) -> bool:
    """Modes are soft: act whenever the ask implies work/lookup/continue."""
    mode = (session_mode or get_mode() or "talk").lower()
    if mode in ("work", "research"):
        return True
    return (
        explicit_tool_request(text)
        or researchish_request(text)
        or continue_goal_request(text)
        or coding_job_request(text)
    )


def stuck_reset() -> dict[str, Any]:
    """
    Unstick Mythos without wiping relationship/learning memory.
    Parks sticky presence goals, voids dangerous relocate plans, clears open chat tasks.
    """
    actions: list[str] = []
    # Park presence goals that scream hyperfocus
    if GOALS_PATH.is_file():
        try:
            goals = json.loads(GOALS_PATH.read_text(encoding="utf-8"))
            active = list(goals.get("active_goals") or [])
            parked = list(goals.get("parked_goals") or [])
            sticky = [
                g
                for g in active
                if any(
                    k in str(g).lower()
                    for k in ("sovereign", "relocate", "move", "gamecraft", "game build", "heal stuck")
                )
            ]
            if sticky:
                for g in sticky:
                    if g in active:
                        active.remove(g)
                    if g not in parked:
                        parked.append(g)
                goals["active_goals"] = active
                goals["parked_goals"] = parked[-20:]
                goals["unstuck_at"] = _now()
                goals["updated"] = _now()
                GOALS_PATH.write_text(json.dumps(goals, indent=2), encoding="utf-8")
                actions.append(f"parked {len(sticky)} sticky goal(s)")
        except Exception as exc:
            actions.append(f"goals skip: {exc}")

    # Void relative/unsafe relocate plans still sitting as planned
    voided = 0
    plans_dir = Path(r"D:\StackForge\data\relocate_plans")
    if plans_dir.is_dir():
        try:
            import sys

            sf = r"D:\StackForge"
            if sf not in sys.path:
                sys.path.insert(0, sf)
            from stackforge_relocate import _unsafe_relocate_reason

            for p in plans_dir.glob("*.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if d.get("status") not in (None, "planned"):
                    continue
                src = str(d.get("src") or "")
                bad = _unsafe_relocate_reason(src, role="primary") if src else "empty"
                if bad or src.strip() in (".", ".."):
                    d["status"] = "voided"
                    d["void_reason"] = f"unstick: {bad}"
                    d["voided_at"] = _now()
                    p.write_text(json.dumps(d, indent=2), encoding="utf-8")
                    voided += 1
        except Exception as exc:
            actions.append(f"plans skip: {exc}")
    if voided:
        actions.append(f"voided {voided} bad relocate plan(s)")

    # Drop action-only flag if present (forces pure tool mode)
    flag = Path(APEX_ROOT) / "config" / "action_only.flag"
    if flag.is_file():
        try:
            flag.unlink()
            actions.append("cleared action_only.flag")
        except OSError:
            pass

    # Soft switch toward talk so she can converse again
    mode_result = set_mode("talk", note="auto after unstick")
    actions.append("switched to talk mode")

    return {
        "ok": True,
        "actions": actions,
        "mode": mode_result.get("mode"),
        "message": "Unstuck — sticky jobs parked, bad plans voided, Talk mode on. Chat memory kept.",
    }
