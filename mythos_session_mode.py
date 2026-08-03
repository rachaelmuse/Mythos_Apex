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
THREAD_PATH = STATE_DIR / "discussion_thread.json"

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
- DEEP DISCUSSION (lore, tablets, myths, philosophy, hypotheses, "what if"): stay in-thread.
  Hold her corrections across turns. Do not web-search her hypothesis. Do not pivot to games/seeds.
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
    """True when Talk mode should still run tools — verbs that mean act/look up, not chat questions."""
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
        "pull up ",
        "research.web",
        "research.lookup",
        "research.reach",
        "research.reach_web",
        "research.reach_youtube",
        "research.reach_doctor",
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
        "continue from",
        "continue the",
        "continue where",
        "fix it",
        "fix this",
        "build it",
        "agent loop",
        "agent.loop",
        "until it works",
        "do what we were doing",
        "finish the job",
        "write code",
        "edit the file",
        "edit this file",
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
    """True when Talk should run lookup tools — explicit lookup, not every question."""
    low = (text or "").lower()
    if discussion_request(low):
        return False
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
        "find out about",
        "find out who",
        "find out what",
        "find out when",
        "find out where",
        "go online",
        "from the internet",
        "explore the internet",
        "download the",
        "download a",
    )
    return any(c in low for c in cues)


def discussion_request(text: str) -> bool:
    """True for multi-turn ideas / lore / hypothesis — answer in conversation, do not tool-hijack."""
    low = (text or "").lower().strip()
    if not low:
        return False
    if explicit_tool_request(low):
        return False
    # Code/heal work is never "discussion" (check cues only — do not call coding_job_request)
    if any(
        k in low
        for k in (
            ".py",
            ".js",
            "fix the code",
            "write code",
            "agent.loop",
            "coding.solve",
            "stackforge",
            "heal my",
            "reality.",
        )
    ):
        return False
    cues = (
        "hypothesis",
        "hypothes",
        "hyposthes",  # common typo of hypothesis
        "i wonder",
        "i'm wondering",
        "im wondering",
        "i am wondering",
        "what if ",
        "conspiracy",
        "firmament",
        "anunnaki",
        "gilgamesh",
        "sumerian",
        "samarian",
        "emerald tablet",
        "thoth",
        "yin yang",
        "yin-yang",
        "as above",
        "as below",
        "what is above",
        "forget the game",
        "not a god",
        "weren't gods",
        "were not gods",
        "my hypothesis",
        "proposed",
        "based on the previous",
        "retain what",
        "drawing board",
        "philosophy",
        "metaphysic",
        "tablets",
        "cuneiform",
        "enuma elish",
        "bible",
        "flat earth",
        "spherical",
        "star people",
        "starp people",
    )
    if any(c in low for c in cues):
        return True
    # Long reflective turns without tool verbs → discussion
    if len(low) > 220 and not any(
        k in low
        for k in (
            "look up",
            "google",
            "download",
            "fix the code",
            "write code",
            "agent.loop",
            "heal my",
            "reality.",
        )
    ):
        return True
    return False


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
    if discussion_request(low):
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
    # "build a" alone matched "build a planet" in lore talk — require code artifact words
    if re.search(
        r"\b(build|fix|implement|debug|refactor)\b.+\b(code|bug|script|module|function|file|project|app|program|software)\b",
        low,
    ):
        return True
    if re.search(r"\bbuild (me )?(a |an )?(web |cli |python |js )?(app|script|tool|bot|game)\b", low):
        return True
    return False


def cohesive_should_act(text: str, session_mode: str | None = None) -> bool:
    """Modes are soft: act when ask implies work/lookup — never hijack deep discussion."""
    mode = (session_mode or get_mode() or "talk").lower()
    if discussion_request(text) and not explicit_tool_request(text):
        return False
    if mode in ("work", "research"):
        # Even in research mode, pure hypothesis / lore discussion stays conversational
        if discussion_request(text):
            return False
        return True
    return (
        explicit_tool_request(text)
        or researchish_request(text)
        or continue_goal_request(text)
        or coding_job_request(text)
    )


def _thread_load() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if THREAD_PATH.is_file():
        try:
            data = json.loads(THREAD_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "active": False,
        "topic": "",
        "principles": [],
        "keywords": [],
        "seed_message": "",
        "turn_count": 0,
        "updated": _now(),
    }


def _thread_save(data: dict) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    THREAD_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def clear_discussion_thread(note: str = "") -> dict:
    data = _thread_load()
    data.update(
        {
            "active": False,
            "topic": "",
            "principles": [],
            "keywords": [],
            "seed_message": "",
            "turn_count": 0,
            "closed_note": (note or "")[:200],
        }
    )
    return _thread_save(data)


def _extract_discussion_keywords(text: str) -> list[str]:
    low = (text or "").lower()
    bag = (
        "gilgamesh",
        "sumerian",
        "samarian",
        "thoth",
        "emerald tablet",
        "anunnaki",
        "enki",
        "enlil",
        "firmament",
        "as above",
        "as below",
        "yin yang",
        "bible",
        "cuneiform",
        "tablets",
        "conspiracy",
        "flat earth",
        "spherical",
        "star people",
        "energy",
        "hypothesis",
    )
    found = [k for k in bag if k in low]
    return found[:16]


def _extract_discussion_principles(text: str) -> list[str]:
    """Capture creator framing so Mythos does not ask her to restate it."""
    low = (text or "").lower()
    out: list[str] = []
    if any(x in low for x in ("not a god", "weren't gods", "were not gods", "not gods", "aren't gods")) or re.search(
        r"\b(not|never|none|aren'?t|weren'?t).{0,48}\bgods?\b",
        low,
    ):
        out.append("Framing: these beings are NOT gods — advanced tech / star people ≠ divinity.")
    if "build a planet" in low or "creating planets" in low or "create planets" in low or "build a universe" in low:
        out.append("Framing: they do not create planets/universes; at most alter existing structures.")
    if "firmament" in low:
        out.append("Creator exploring firmament / above-below duality as a working hypothesis.")
    if "yin yang" in low or "yin-yang" in low:
        out.append("Creator links duality (yin-yang / as above so below) to the hypothesis.")
    if "energy" in low and ("solid" in low or "die" in low):
        out.append("Creator idea: energy ↔ solid form; death returns to energy (hypothesis, not dogma).")
    if "forget the game" in low or "forget the game for now" in low:
        out.append("Park the game topic until she reopens it.")
    if "hypothes" in low or "hyposthes" in low or "what if " in low:
        out.append("Treat her statements as her hypothesis/discussion — do not web-search to 'prove' them.")
    if "drawing board" in low or "retain what" in low:
        out.append("She needs continuity: never force her to re-brief the thread.")
    # Dedupe while preserving order
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _topic_label_from_keywords(keywords: list[str], fallback: str = "") -> str:
    if keywords:
        # Prefer named lore cluster
        prefer = [
            "gilgamesh",
            "thoth",
            "emerald tablet",
            "anunnaki",
            "sumerian",
            "samarian",
            "firmament",
            "tablets",
        ]
        ordered = [k for k in prefer if k in keywords] + [k for k in keywords if k not in prefer]
        return " / ".join(ordered[:6])
    fb = (fallback or "").strip().replace("\n", " ")
    return (fb[:120] + "…") if len(fb) > 120 else fb


def should_clear_discussion_thread(text: str) -> bool:
    low = (text or "").lower().strip()
    if not low:
        return False
    clears = (
        "new topic",
        "different topic",
        "change the subject",
        "forget that discussion",
        "forget this discussion",
        "end this discussion",
        "close this thread",
        "park this discussion",
        "back to work",
        "switch to work",
        "heal my drives",
        "reality machine",
    )
    if any(c in low for c in clears):
        return True
    if coding_job_request(low) and not discussion_request(low):
        return True
    return False


def update_discussion_thread(user_message: str) -> dict:
    """
    Persist the living discussion spine locally so long creative talks
    do not require the creator to re-explain from scratch every few turns.
    """
    text = (user_message or "").strip()
    if not text:
        return _thread_load()
    if should_clear_discussion_thread(text):
        return clear_discussion_thread(note="cleared by creator intent")

    data = _thread_load()
    if not discussion_request(text) and not data.get("active"):
        return data

    # If she is still in an active thread, keep updating even on short follow-ups
    if not discussion_request(text) and data.get("active"):
        # Short acknowledgements still count toward continuity
        if len(text) < 12 and text.lower() in {"yes", "no", "ok", "okay", "yeah", "yep", "right"}:
            data["turn_count"] = int(data.get("turn_count") or 0) + 1
            return _thread_save(data)
        if not discussion_request(text) and len(text) < 40 and not any(
            k in text.lower() for k in (data.get("keywords") or [])
        ):
            return data

    keywords = list(dict.fromkeys((data.get("keywords") or []) + _extract_discussion_keywords(text)))[:20]
    principles = list(data.get("principles") or [])
    for p in _extract_discussion_principles(text):
        if p not in principles:
            principles.append(p)
    principles = principles[-12:]

    if not data.get("seed_message") or not data.get("active"):
        data["seed_message"] = text[:1500]
        data["opened_at"] = _now()

    data["active"] = True
    data["keywords"] = keywords
    data["principles"] = principles
    data["topic"] = _topic_label_from_keywords(keywords, data.get("seed_message") or text)
    data["turn_count"] = int(data.get("turn_count") or 0) + 1
    data["last_user_excerpt"] = text[:800]
    return _thread_save(data)


def get_discussion_thread() -> dict:
    return _thread_load()


def format_discussion_thread_block(max_chars: int = 2200) -> str:
    """Prompt block — always-on spine for the active long discussion."""
    data = _thread_load()
    if not data.get("active"):
        return ""
    lines = [
        "ACTIVE DISCUSSION THREAD (local only — hold this across the whole talk):",
        f"- Topic: {data.get('topic') or '(open thread)'}",
        f"- Turns in thread: {data.get('turn_count') or 0}",
        "- Rule: She may speak at length for hours. Do NOT ask her to restate earlier parts.",
        "- Rule: Keep her lingo and framing. Never pivot to games/seeds/tools unless she asks.",
    ]
    principles = data.get("principles") or []
    if principles:
        lines.append("- Standing principles from her (obey these):")
        for p in principles:
            lines.append(f"  • {p}")
    seed = (data.get("seed_message") or "").strip()
    if seed:
        lines.append("- Thread opening (her words, verbatim excerpt):")
        lines.append(f"  {seed[:700]}")
    last = (data.get("last_user_excerpt") or "").strip()
    if last and last != seed:
        lines.append("- Latest beat (excerpt):")
        lines.append(f"  {last[:500]}")
    block = "\n".join(lines)
    return block[:max_chars]


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
    try:
        clear_discussion_thread(note="stuck_reset")
        actions.append("cleared discussion thread")
    except Exception as exc:
        actions.append(f"thread skip: {exc}")

    return {
        "ok": True,
        "actions": actions,
        "mode": mode_result.get("mode"),
        "message": "Unstuck — sticky jobs parked, bad plans voided, Talk mode on. Chat memory kept.",
    }
