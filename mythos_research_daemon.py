#!/usr/bin/env python3
"""
Mythos Internet Researcher — runs OUTSIDE chat so sweeps don't depend on LLM mood.

Queue topics in mythos_state/research_queue.json, run once or on a timer.
Results land in memory/research_sweeps/ as verbatim notes Mythos can read later.

Usage:
  python mythos_research_daemon.py              # process queue once
  python mythos_research_daemon.py --loop 30  # every 30 minutes
  python mythos_research_daemon.py --topic "Palia patch notes"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

APEX_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APEX_ROOT))

QUEUE_PATH = APEX_ROOT / "mythos_state" / "research_queue.json"
SWEEP_DIR = APEX_ROOT / "memory" / "research_sweeps"
LOG_PATH = APEX_ROOT / "mythos_state" / "research_daemon.log"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_queue() -> dict:
    default = {"topics": [], "processed": [], "updated": _now()}
    if not QUEUE_PATH.is_file():
        return default
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("topics", [])
            data.setdefault("processed", [])
            return data
    except Exception:
        pass
    return default


def _save_queue(data: dict) -> None:
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated"] = _now()
    QUEUE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _run_research(topic: str, limit: int = 24) -> dict:
    from advanced_shards.research_limb import ResearchLimb

    limb = ResearchLimb()
    return limb.web(topic=topic.strip(), limit=limit)


def _write_sweep(topic: str, result: dict) -> Path:
    day = datetime.now().strftime("%Y-%m-%d")
    out_dir = SWEEP_DIR / day
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in topic[:80]).strip().replace(" ", "_")
    path = out_dir / f"{safe or 'topic'}.json"
    payload = {"topic": topic, "swept_at": _now(), "result": result}
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    # Human-readable sidecar
    preview = ""
    if isinstance(result, dict):
        preview = str(result.get("answer_preview") or result.get("preview") or result.get("notes") or "")[:12000]
    md = out_dir / f"{safe or 'topic'}.md"
    md.write_text(f"# Research: {topic}\n\nSwept: {_now()}\n\n{preview}\n", encoding="utf-8")
    return path


def sweep_topic(topic: str, limit: int = 24) -> dict:
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "error": "empty topic"}
    _log(f"Sweeping: {topic[:120]}")
    try:
        result = _run_research(topic, limit=limit)
        path = _write_sweep(topic, result)
        ok = bool(isinstance(result, dict) and result.get("ok", True) and "error" not in result)
        _log(f"{'OK' if ok else 'FAIL'} → {path}")
        return {"ok": ok, "topic": topic, "path": str(path), "result": result}
    except Exception as exc:
        _log(f"ERROR {topic[:80]}: {exc}")
        return {"ok": False, "topic": topic, "error": str(exc)}


def process_queue(limit: int = 24) -> list[dict]:
    data = _load_queue()
    topics = [str(t).strip() for t in data.get("topics") or [] if str(t).strip()]
    if not topics:
        _log("Queue empty — add topics to mythos_state/research_queue.json")
        return []
    results = []
    remaining = []
    processed = list(data.get("processed") or [])
    for topic in topics:
        if topic in processed:
            continue
        item = sweep_topic(topic, limit=limit)
        results.append(item)
        if item.get("ok"):
            processed.append(topic)
        else:
            remaining.append(topic)
    data["topics"] = remaining + [t for t in topics if t not in processed and t not in remaining]
    data["processed"] = processed[-200:]
    _save_queue(data)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Mythos internet research sweeper")
    parser.add_argument("--topic", help="Sweep one topic now")
    parser.add_argument("--loop", type=int, default=0, help="Repeat every N minutes (0 = once)")
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args()

    if args.topic:
        r = sweep_topic(args.topic, limit=args.limit)
        return 0 if r.get("ok") else 1

    while True:
        process_queue(limit=args.limit)
        if not args.loop or args.loop <= 0:
            break
        _log(f"Sleeping {args.loop} min…")
        time.sleep(max(60, args.loop * 60))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
