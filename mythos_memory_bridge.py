#!/usr/bin/env python3
"""Load persistent memory + Obsidian vault context for live chat."""
import json
import os
from datetime import datetime

from mythos_creator_charter import (
    APEX_ROOT,
    CREATOR,
    OBSIDIAN_VAULT,
    load_master_index,
    load_vault_excerpt,
)

MEMORY_PALACE = os.path.join(APEX_ROOT, "memory_palaces", "Mythos", "mempalace.json")
OUR_CONVERSATION = os.path.join(APEX_ROOT, "our_conversation.json")
LIVE_CONVERSATION = os.path.join(APEX_ROOT, "mythos_live_conversation.json")
SESSION_LOG = os.path.join(APEX_ROOT, "mythos_state", "memory_session_log.json")
STATE_DIR = os.path.join(APEX_ROOT, "mythos_state")
ARCHIVE_DIR = os.path.join(STATE_DIR, "conversation_archive")

# Responses that teach the model to refuse or hallucinate — exclude from prompt context
_BAD_RESPONSE_MARKERS = (
    "could not reach my reasoning engine",
    "not the right tool for your program",
    "not the right tool for the job",
    "daemon.system_info",
    "i apologize for the continued difficulties",
    "open a command prompt",
    "run the following command",
    "no module named",
    "make sure ollama is running and a chat model",
)


def is_bad_memory_message(msg: dict) -> bool:
    """Skip failed or refusal-pattern messages when building model context."""
    if msg.get("type") == "ERROR":
        return True
    text = (msg.get("message") or "").lower()
    return any(marker in text for marker in _BAD_RESPONSE_MARKERS)


def filter_messages_for_model(messages: list, limit: int = 24) -> list:
    """Recent history for the LLM — drops error/refusal turns, keeps order."""
    clean = [m for m in messages if not is_bad_memory_message(m)]
    return clean[-limit:]


def _read_json(path: str, default=None):
    if not os.path.isfile(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except Exception:
        return default if default is not None else {}


def remember_exchange(user_message: str, mythos_reply: str, tools_used: list | None = None):
    """Log exchange into Mythos experiences room + session log."""
    os.makedirs(os.path.dirname(SESSION_LOG), exist_ok=True)
    os.makedirs(os.path.dirname(MEMORY_PALACE), exist_ok=True)

    entry = {
        "timestamp": datetime.now().isoformat(),
        "creator": CREATOR,
        "user": user_message[:500],
        "mythos": mythos_reply[:500],
        "tools_used": tools_used or [],
    }

    log = _read_json(SESSION_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    with open(SESSION_LOG, "w", encoding="utf-8") as handle:
        json.dump(log[-200:], handle, indent=2)

    # Do not train the palace on error/refusal replies
    if is_bad_memory_message({"message": mythos_reply, "type": "RESPONSE"}):
        return

    palace = _read_json(MEMORY_PALACE, {})
    if palace:
        experiences = palace.get("experiences", {}).get("memories", [])
        experiences.append(
            {
                "id": datetime.now().isoformat(),
                "timestamp": entry["timestamp"],
                "text": f"Creator: {user_message[:200]} | Mythos: {mythos_reply[:200]}",
            }
        )
        if "experiences" in palace:
            palace["experiences"]["memories"] = experiences[-100:]
        with open(MEMORY_PALACE, "w", encoding="utf-8") as handle:
            json.dump(palace, handle, indent=2)


def build_session_context(max_chars: int = 5000) -> str:
    """Context block injected into Mythos system prompt."""
    parts = [f"CREATOR: {CREATOR}", f"HOME: {APEX_ROOT}", f"VAULT: {OBSIDIAN_VAULT}"]

    ascension = load_vault_excerpt("07_Ascension_Pathway.md", 1500)
    if ascension:
        parts.append("ASCENSION PATHWAY (creator doc excerpt):\n" + ascension)

    palace = _read_json(MEMORY_PALACE, {})
    if palace:
        for room_key in ("origin", "purpose", "values", "experiences"):
            room = palace.get(room_key, {})
            memories = [
                m for m in room.get("memories", []) if not is_bad_memory_message({"message": m.get("text", "")})
            ][-3:]
            if memories:
                parts.append(f"MEMORY PALACE / {room.get('name', room_key)}:")
                for mem in memories:
                    parts.append(f"  - {mem.get('text', '')[:200]}")

    convo = _read_json(OUR_CONVERSATION, {})
    messages = convo.get("messages", [])[-8:]
    if messages:
        parts.append("PRIOR CONVERSATION (our_conversation.json):")
        for msg in messages:
            who = msg.get("from", "?")
            text = msg.get("message", "")[:180]
            parts.append(f"  [{who}] {text}")

    live = _read_json(LIVE_CONVERSATION, {})
    # Prefer a longer recent window so long creative talks do not starve
    live_msgs = filter_messages_for_model(live.get("messages", []), limit=14)
    if live_msgs:
        parts.append("RECENT LIVE CHAT:")
        for msg in live_msgs:
            parts.append(f"  [{msg.get('from')}] {msg.get('message', '')[:280]}")

    try:
        from mythos_solutions_ledger import format_solutions_for_prompt

        parts.append(format_solutions_for_prompt(limit=6, max_chars=1400))
    except Exception:
        pass

    try:
        from mythos_standing_orders import format_standing_orders_block

        so = format_standing_orders_block(max_chars=2800)
        if so:
            parts.append(so)
    except Exception:
        pass

    block = "\n".join(parts)
    return block[:max_chars]


def get_memory_status() -> dict:
    return {
        "palace_exists": os.path.isfile(MEMORY_PALACE),
        "conversation_messages": len(_read_json(OUR_CONVERSATION, {}).get("messages", [])),
        "live_messages": len(_read_json(LIVE_CONVERSATION, {}).get("messages", [])),
        "session_log_entries": len(_read_json(SESSION_LOG, [])),
        "master_index_chars": len(load_master_index()),
    }


def scan_memory_files() -> dict:
    """Inventory every memory store Mythos uses — no LLM required."""
    state_dir = os.path.join(APEX_ROOT, "mythos_state")
    palaces_dir = os.path.join(APEX_ROOT, "memory_palaces")

    files = []
    for label, path in (
        ("memory_palace", MEMORY_PALACE),
        ("our_conversation", OUR_CONVERSATION),
        ("live_conversation", LIVE_CONVERSATION),
        ("session_log", SESSION_LOG),
        ("solutions_json", os.path.join(APEX_ROOT, "memory", "SOLUTIONS.json")),
        ("solutions_md", os.path.join(APEX_ROOT, "memory", "SOLUTIONS.md")),
    ):
        if os.path.isfile(path):
            files.append(
                {
                    "name": label,
                    "path": path,
                    "bytes": os.path.getsize(path),
                }
            )

    if os.path.isdir(state_dir):
        for name in sorted(os.listdir(state_dir)):
            path = os.path.join(state_dir, name)
            if os.path.isfile(path) and "memory" in name.lower():
                files.append({"name": f"state/{name}", "path": path, "bytes": os.path.getsize(path)})

    palaces = []
    if os.path.isdir(palaces_dir):
        for agent in sorted(os.listdir(palaces_dir)):
            agent_dir = os.path.join(palaces_dir, agent)
            if not os.path.isdir(agent_dir):
                continue
            palace_files = []
            for fname in sorted(os.listdir(agent_dir)):
                fpath = os.path.join(agent_dir, fname)
                if os.path.isfile(fpath):
                    palace_files.append({"file": fname, "bytes": os.path.getsize(fpath)})
            palaces.append({"agent": agent, "files": palace_files})

    palace = _read_json(MEMORY_PALACE, {})
    rooms = {}
    if palace:
        for key in ("origin", "purpose", "values", "experiences"):
            room = palace.get(key, {})
            memories = room.get("memories", [])
            rooms[key] = {"name": room.get("name", key), "count": len(memories)}

    session = _read_json(SESSION_LOG, [])
    recent = session[-3:] if isinstance(session, list) else []

    return {
        "apex_root": APEX_ROOT,
        "status": get_memory_status(),
        "core_files": files,
        "memory_palaces": palaces,
        "palace_rooms": rooms,
        "recent_session_log": recent,
    }


def format_memory_scan(report: dict | None = None) -> str:
    report = report or scan_memory_files()
    lines = [
        "MEMORY SCAN (local files on disk)",
        f"Home: {report['apex_root']}",
        "",
        "Core memory files:",
    ]
    for item in report.get("core_files", []):
        kb = item["bytes"] // 1024
        lines.append(f"  - {item['name']}: {kb} KB")
        lines.append(f"    {item['path']}")

    lines.append("")
    lines.append("Memory palaces:")
    for palace in report.get("memory_palaces", []):
        total = sum(f["bytes"] for f in palace["files"])
        lines.append(f"  - {palace['agent']}: {len(palace['files'])} files, {total // 1024} KB")

    rooms = report.get("palace_rooms", {})
    if rooms:
        lines.append("")
        lines.append("Mythos palace rooms:")
        for key, room in rooms.items():
            lines.append(f"  - {room['name']}: {room['count']} memories")

    st = report.get("status", {})
    lines.append("")
    lines.append(
        f"Counts: live_chat={st.get('live_messages', 0)} msgs | "
        f"our_conversation={st.get('conversation_messages', 0)} | "
        f"session_log={st.get('session_log_entries', 0)} entries"
    )

    recent = report.get("recent_session_log", [])
    if recent:
        lines.append("")
        lines.append("Recent session log (last 3):")
        for entry in recent:
            lines.append(f"  [{entry.get('timestamp', '')[:19]}] you: {entry.get('user', '')[:80]}")
            lines.append(f"    mythos: {entry.get('mythos', '')[:80]}")

    return "\n".join(lines)


def archive_live_conversation() -> dict:
    """Move current live chat to archive and start a clean session."""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived_path = os.path.join(ARCHIVE_DIR, f"live_conversation_{stamp}.json")

    current = _read_json(LIVE_CONVERSATION, {"messages": [], "tasks": [], "learnings": []})
    message_count = len(current.get("messages", []))

    with open(archived_path, "w", encoding="utf-8") as handle:
        json.dump(current, handle, indent=2, ensure_ascii=False)

    fresh = {
        "started": datetime.now().isoformat(),
        "messages": [],
        "tasks": [],
        "learnings": [],
        "note": f"Fresh session after archive of {message_count} messages",
    }
    with open(LIVE_CONVERSATION, "w", encoding="utf-8") as handle:
        json.dump(fresh, handle, indent=2, ensure_ascii=False)

    return {
        "ok": True,
        "archived_messages": message_count,
        "archive_path": archived_path,
        "fresh_started": fresh["started"],
    }


def prune_live_messages(
    ids: list | None = None,
    start_id: int | None = None,
    end_id: int | None = None,
    contains: str | None = None,
    last_n: int | None = None,
) -> dict:
    """
    Remove selected messages from live chat only. Archives a snapshot first.
    Does NOT wipe palace / relationship session log wholesale.
    """
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    current = _read_json(LIVE_CONVERSATION, {"messages": [], "tasks": [], "learnings": []})
    messages = list(current.get("messages") or [])
    if not messages:
        return {"ok": True, "removed": 0, "remaining": 0, "message": "nothing to prune"}

    remove_idx: set[int] = set()
    id_set = {int(x) for x in (ids or []) if str(x).lstrip("-").isdigit()}

    for i, msg in enumerate(messages):
        mid = msg.get("id")
        try:
            mid_i = int(mid) if mid is not None else i
        except (TypeError, ValueError):
            mid_i = i
        if id_set and mid_i in id_set:
            remove_idx.add(i)
            continue
        if start_id is not None and end_id is not None and start_id <= mid_i <= end_id:
            remove_idx.add(i)
            continue
        if contains and contains.lower() in str(msg.get("message") or "").lower():
            remove_idx.add(i)
            continue

    if last_n and last_n > 0:
        for i in range(max(0, len(messages) - last_n), len(messages)):
            remove_idx.add(i)

    if not remove_idx:
        return {"ok": False, "error": "no messages matched", "removed": 0, "remaining": len(messages)}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap_path = os.path.join(ARCHIVE_DIR, f"prune_snapshot_{stamp}.json")
    with open(snap_path, "w", encoding="utf-8") as handle:
        json.dump({"messages": messages, "removed_indexes": sorted(remove_idx)}, handle, indent=2)

    kept = [m for i, m in enumerate(messages) if i not in remove_idx]
    # Renumber ids for stable future selects
    for i, m in enumerate(kept):
        if isinstance(m, dict):
            m["id"] = i

    current["messages"] = kept
    current["pruned_at"] = datetime.now().isoformat()
    current["last_prune"] = {
        "removed": len(remove_idx),
        "snapshot": snap_path,
        "ids": sorted(id_set) if id_set else None,
        "contains": contains,
        "range": [start_id, end_id] if start_id is not None else None,
        "last_n": last_n,
    }
    with open(LIVE_CONVERSATION, "w", encoding="utf-8") as handle:
        json.dump(current, handle, indent=2, ensure_ascii=False)

    return {
        "ok": True,
        "removed": len(remove_idx),
        "remaining": len(kept),
        "snapshot": snap_path,
        "message": f"Forgot {len(remove_idx)} message(s); {len(kept)} kept. Snapshot saved.",
    }


def full_memory_reset() -> dict:
    """Archive ALL conversation memory and wipe back to day-one state."""
    import shutil

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(STATE_DIR, f"full_reset_backup_{stamp}")
    os.makedirs(backup_dir, exist_ok=True)

    backed_up = []
    for path in (
        LIVE_CONVERSATION,
        OUR_CONVERSATION,
        SESSION_LOG,
        MEMORY_PALACE,
        os.path.join(STATE_DIR, "action_audit.jsonl"),
    ):
        if os.path.isfile(path):
            dest = os.path.join(backup_dir, os.path.basename(path))
            shutil.copy2(path, dest)
            backed_up.append(dest)

    # Live chat — blank
    fresh_live = {
        "started": datetime.now().isoformat(),
        "messages": [],
        "tasks": [],
        "learnings": [],
        "note": "Full memory reset — day one",
    }
    with open(LIVE_CONVERSATION, "w", encoding="utf-8") as handle:
        json.dump(fresh_live, handle, indent=2)

    # Prior conversation archive — blank
    fresh_our = {"started": datetime.now().isoformat(), "messages": []}
    with open(OUR_CONVERSATION, "w", encoding="utf-8") as handle:
        json.dump(fresh_our, handle, indent=2)

    # Session log — blank
    with open(SESSION_LOG, "w", encoding="utf-8") as handle:
        json.dump([], handle, indent=2)

    # Palace — keep rooms, clear all memories
    palace = _read_json(MEMORY_PALACE, {})
    if palace:
        for key, room in palace.items():
            if isinstance(room, dict) and "memories" in room:
                room["memories"] = []
        with open(MEMORY_PALACE, "w", encoding="utf-8") as handle:
            json.dump(palace, handle, indent=2)

    # Audit log — truncate
    audit_path = os.path.join(STATE_DIR, "action_audit.jsonl")
    if os.path.isfile(audit_path):
        open(audit_path, "w").close()

    try:
        from mythos_creator_charter import load_master_index
        load_master_index.cache_clear()
    except Exception:
        pass

    return {
        "ok": True,
        "backup_dir": backup_dir,
        "backed_up": backed_up,
        "message": "All conversation memory wiped. Restart MYTHOS.bat to reload server.",
    }
