# -*- coding: utf-8 -*-
"""
Reality Machine — resource-aware autonomous problem solver.

System Mixture-of-Experts: a router divides the problem space and dispatches
specialist experts (code / debug / research / engineer / heavy MoE / drives).
Uses every available drive, internet research, and existing Mythos limbs.
Does not stop after one failure — plans → acts → verifies → escalates → retries.
"""
from __future__ import annotations

import json
import os
import re
import string
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT, detect_chat_model, get_ollama_client

STATE_DIR = Path(APEX_ROOT) / "mythos_state" / "reality_machine"
LEDGER = STATE_DIR / "ledger.json"
LAST = STATE_DIR / "last_solve.json"

# Default federation roots (creator sanctuary)
DEFAULT_ROOTS = [
    Path("D:/Mythos_Apex"),
    Path("G:/Mythos_Codex"),
    Path("D:/Sanctuary"),
    Path("D:/[THE_SANCTUARY]"),
    Path("D:/StackForge"),
    Path("D:/colibri"),
    Path("D:/MEMORY_PALACE"),
]

EXPERTS = {
    "router": "Divide goals into homogeneous expert regions",
    "engineer": "Architecture, plans, consolidate or decompose projects",
    "python": "Python coding / repair",
    "javascript": "JS/TS coding / repair",
    "cpp": "C/C++ coding / repair",
    "debug": "Reproduce failures, read logs, verify fixes",
    "research": "Internet + local docs — never ask creator for URLs",
    "heavy": "Colibri MoE / GGUF / cloud when stuck",
    "drives": "Inventory and place work across D/E/G and externals",
    "colibri": "Bring up SSD-backed MoE (tiered RAM/SSD inference)",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _ledger_append(entry: dict[str, Any]) -> None:
    _ensure_state()
    data: dict[str, Any] = {"runs": [], "updated": _now()}
    if LEDGER.is_file():
        try:
            data = json.loads(LEDGER.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {"runs": []}
        except Exception:
            data = {"runs": []}
    runs = list(data.get("runs") or [])
    runs.append(entry)
    data["runs"] = runs[-100:]
    data["updated"] = _now()
    data["last"] = entry
    LEDGER.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _ram() -> dict[str, Any]:
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {
            "ok": True,
            "total_gb": round(m.ullTotalPhys / (1024**3), 2),
            "free_gb": round(m.ullAvailPhys / (1024**3), 2),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _probe(url: str, timeout: float = 2.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def _ollama_text(prompt: str, system: str, num_predict: int = 900) -> str:
    model = os.environ.get("MYTHOS_CODER_MODEL") or detect_chat_model() or "llama3.1:8b"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt[:8000]},
    ]
    try:
        client = get_ollama_client()
        if client is not None and hasattr(client, "chat"):
            resp = client.chat(
                model=model,
                messages=messages,
                options={"temperature": 0.2, "num_predict": num_predict},
            )
            return (((resp or {}).get("message") or {}).get("content") or "").strip()
        body = {
            "model": model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": 0.2, "num_predict": num_predict},
        }
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (((data.get("message") or {}).get("content") or "")).strip()
    except Exception as exc:
        return f"(planner offline: {exc})"


def _ollama_json(prompt: str, system: str) -> dict[str, Any]:
    text = _ollama_text(prompt, system, num_predict=1000)
    m = re.search(r"(\{[\s\S]*\})", text)
    if not m:
        return {"_raw": text}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"_raw": text}


def _drive_letters() -> list[str]:
    found = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            found.append(letter)
    return found


def _folder_size_gb(path: Path, depth: int = 1) -> float | None:
    """Shallow size estimate — top level only for speed."""
    try:
        total = 0
        if not path.is_dir():
            return None
        for i, p in enumerate(path.iterdir()):
            if i > 80:
                break
            try:
                if p.is_file():
                    total += p.stat().st_size
                elif p.is_dir() and depth > 0:
                    for j, c in enumerate(p.iterdir()):
                        if j > 40:
                            break
                        if c.is_file():
                            total += c.stat().st_size
            except OSError:
                pass
        return round(total / (1024**3), 3)
    except OSError:
        return None


class RealityMachineLimb:
    """
    Reality Machine — the creator's resource-aware genius orchestrator.

    Not a clone of Cursor. A Mythos system-MoE that divides problems, uses
    drives + internet + coding loops + Colibri/cloud heavy, and keeps going.
    """

    def status(self) -> dict[str, Any]:
        _ensure_state()
        drives = _drive_letters()
        heavy = {
            "colibri_8010": _probe("http://127.0.0.1:8010/v1/models"),
            "gguf_8088": _probe("http://127.0.0.1:8088/v1/models"),
            "ollama_11434": _probe("http://127.0.0.1:11434/api/tags"),
        }
        last = None
        if LAST.is_file():
            try:
                last = json.loads(LAST.read_text(encoding="utf-8"))
            except Exception:
                last = None
        return {
            "ok": True,
            "limb": "reality_machine",
            "title": "Reality Machine — resource-aware autonomous problem solver",
            "kind": "system_mixture_of_experts",
            "experts": EXPERTS,
            "drives_online": drives,
            "ram": _ram(),
            "heavy_endpoints": heavy,
            "state_dir": str(STATE_DIR),
            "last_solve": last,
            "tools": [
                "reality.status",
                "reality.inventory",
                "reality.find_project",
                "reality.route",
                "reality.solve",
                "reality.continue",
            ],
            "doctrine": (
                "Divide the problem into homogeneous expert regions. "
                "Use RAM/SSD tiering for MoE when Colibri is up. "
                "Federate all drives. Research online when stuck. Never stop after one failure."
            ),
        }

    def inventory(self, max_per_drive: int = 30, roots: str = "") -> dict[str, Any]:
        """Federate visible drives — top folders + known Mythos roots (list only)."""
        max_per_drive = max(5, min(int(max_per_drive or 30), 80))
        drives = {}
        for letter in _drive_letters():
            root = Path(f"{letter}:\\")
            tops = []
            try:
                entries = sorted(root.iterdir(), key=lambda p: p.name.lower())
            except OSError as exc:
                drives[letter] = {"ok": False, "error": str(exc)}
                continue
            for p in entries[:max_per_drive]:
                if not p.is_dir():
                    continue
                # skip obvious junk
                name = p.name
                if name.lower() in {"$recycle.bin", "system volume information", "recovery"}:
                    continue
                tops.append(
                    {
                        "name": name,
                        "path": str(p),
                        "size_gb_shallow": _folder_size_gb(p, depth=0),
                    }
                )
            drives[letter] = {"ok": True, "folder_count": len(tops), "top_folders": tops}

        known = []
        extra = [Path(x.strip()) for x in (roots or "").split(";") if x.strip()]
        for path in list(DEFAULT_ROOTS) + extra:
            known.append(
                {
                    "path": str(path),
                    "exists": path.exists(),
                    "is_dir": path.is_dir() if path.exists() else False,
                }
            )

        out = {
            "ok": True,
            "drives": drives,
            "known_roots": known,
            "ram": _ram(),
            "at": _now(),
            "note": "Inventory is LIST ONLY — no moves. Use reality.solve to act.",
        }
        _ledger_append({"phase": "inventory", "at": _now(), "drives": list(drives.keys())})
        return out

    def route(self, goal: str = "", context: str = "") -> dict[str, Any]:
        """Divide a goal into homogeneous expert regions (system MoE plan)."""
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "error": "goal required"}

        system = (
            "You are the Reality Machine router (system Mixture-of-Experts). "
            "Divide the creator goal into homogeneous expert regions. "
            "Reply with ONLY JSON:\n"
            '{"regions":[{"expert":"python|javascript|cpp|debug|research|engineer|heavy|drives|colibri",'
            '"task":"...","why":"..."}],'
            '"strategy":"one paragraph",'
            '"success":"how we know we are done",'
            '"needs_internet":true/false,'
            '"needs_heavy":true/false,'
            '"language_hint":"python|javascript|cpp|mixed|unknown"}'
        )
        plan = _ollama_json(
            f"GOAL:\n{goal}\n\nCONTEXT:\n{(context or '')[:3000]}\n\nExperts available: {list(EXPERTS)}",
            system,
        )
        if "_raw" in plan or "_error" in plan or "regions" not in plan:
            # Heuristic fallback — never block
            low = goal.lower()
            regions = []
            if any(k in low for k in ("colibri", "glm", "moe", "mixture of expert")):
                regions.append({"expert": "colibri", "task": "Bring up Colibri MoE / repair weights", "why": "MoE engine"})
                regions.append({"expert": "heavy", "task": "Escalate hard reasoning once engine up", "why": "deep work"})
            if any(k in low for k in ("c++", "cpp", ".cpp", ".h", "cmake")):
                regions.append({"expert": "cpp", "task": goal[:240], "why": "C++ signals"})
            if any(k in low for k in ("javascript", "typescript", ".js", ".ts", "node")):
                regions.append({"expert": "javascript", "task": goal[:240], "why": "JS signals"})
            if any(k in low for k in ("python", ".py", "pip", "django", "flask")) or not regions:
                regions.append({"expert": "python", "task": goal[:240], "why": "default coding expert"})
            regions.append({"expert": "debug", "task": "Reproduce and verify after edits", "why": "proof"})
            regions.append({"expert": "research", "task": f"Look up blockers for: {goal[:160]}", "why": "internet when stuck"})
            plan = {
                "regions": regions,
                "strategy": "Heuristic route — LLM planner unavailable or returned non-JSON",
                "success": "Concrete artifact exists and a verify step reports ok",
                "needs_internet": True,
                "needs_heavy": "colibri" in low or "moe" in low or "hard" in low,
                "language_hint": "python",
            }

        out = {"ok": True, "goal": goal[:2000], "plan": plan, "at": _now()}
        _ledger_append({"phase": "route", "at": _now(), "goal": goal[:200], "plan": plan})
        return out

    def solve(
        self,
        goal: str = "",
        project_dir: str = "",
        max_steps: int = 8,
        allow_internet: bool = True,
        allow_heavy: bool = True,
        allow_colibri_master: bool = True,
        language: str = "",
    ) -> dict[str, Any]:
        """
        Main Reality Machine loop:
        inventory snapshot → route → dispatch experts → verify → escalate → retry.
        """
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "error": "goal required — what should the Reality Machine make real?"}

        max_steps = max(1, min(int(max_steps or 8), 16))
        _ensure_state()
        trace: list[dict[str, Any]] = []
        t0 = time.time()

        # Persist goal for keep-going
        try:
            from mythos_session_mode import set_current_goal

            set_current_goal(goal, note="reality.solve")
        except Exception:
            pass

        # 1) Light inventory
        inv = self.inventory(max_per_drive=12)
        trace.append({"step": "inventory", "drives": inv.get("drives") and list(inv["drives"].keys())})

        # 2) Route
        routed = self.route(goal=goal, context=f"drives={inv.get('drives') and list(inv['drives'].keys())}")
        plan = routed.get("plan") or {}
        regions = list(plan.get("regions") or [])
        trace.append({"step": "route", "regions": [r.get("expert") for r in regions]})

        lang = (language or plan.get("language_hint") or "python").lower()
        if lang in {"unknown", "mixed", ""}:
            lang = "python"

        # 3) Colibri / MoE bring-up if region asks
        if allow_colibri_master and any(
            (r.get("expert") or "") == "colibri"
            or "colibri" in goal.lower()
            or "glm" in goal.lower()
            for r in regions
        ):
            try:
                from advanced_shards.colibri_master_limb import ColibriMasterLimb

                cm = ColibriMasterLimb().master(max_rounds=3, wait_sec=60, repair=True, free_ram=True)
                trace.append({"step": "colibri.master", "ok": cm.get("ok"), "error": cm.get("error")})
            except Exception as exc:
                trace.append({"step": "colibri.master", "ok": False, "error": str(exc)})

        # 4) Research if needed / always soft research on first stuck later
        researched = None
        if allow_internet and (plan.get("needs_internet") or any((r.get("expert") == "research") for r in regions)):
            researched = self._research(goal)
            trace.append({"step": "research", "ok": researched.get("ok"), "preview": (researched.get("preview") or "")[:200]})

        # 5) Coding / engineer loop via agent.loop
        coding_goal = goal
        if researched and researched.get("preview"):
            coding_goal = (
                f"{goal}\n\nResearch notes (use if helpful):\n{researched.get('preview')[:2000]}"
            )

        agent_result = None
        try:
            from advanced_shards.agent_loop_limb import AgentLoopLimb

            if lang in {"javascript", "js", "typescript", "ts", "cpp", "c++", "c"}:
                from advanced_shards.coding_limb import CodingLimb

                map_lang = {
                    "js": "javascript",
                    "javascript": "javascript",
                    "ts": "javascript",
                    "typescript": "javascript",
                    "cpp": "cpp",
                    "c++": "cpp",
                    "c": "cpp",
                }
                use_lang = map_lang.get(lang, "python")
                agent_result = CodingLimb().solve(
                    need=coding_goal,
                    force_write=True,
                    language=use_lang,
                )
                trace.append({"step": "coding.solve_lang", "language": use_lang, "ok": agent_result.get("ok")})
            else:
                agent_result = AgentLoopLimb().loop(
                    goal=coding_goal,
                    project_dir=project_dir or "",
                    max_steps=max(3, min(max_steps, 10)),
                    allow_online=allow_internet,
                    allow_heavy=allow_heavy,
                    language=lang if lang in {"python", "gdscript", "gd"} else "python",
                )
            trace.append(
                {
                    "step": "agent.loop",
                    "ok": agent_result.get("ok"),
                    "project_dir": agent_result.get("project_dir"),
                    "error": agent_result.get("error"),
                }
            )
        except Exception as exc:
            trace.append({"step": "agent.loop", "ok": False, "error": str(exc)})
            # Fallback: coding.solve one-shot
            try:
                from advanced_shards.coding_limb import CodingLimb

                agent_result = CodingLimb().solve(need=coding_goal, force_write=True)
                trace.append({"step": "coding.solve", "ok": agent_result.get("ok")})
            except Exception as exc2:
                trace.append({"step": "coding.solve", "ok": False, "error": str(exc2)})

        # 6) If still failing and heavy allowed — escalate
        if allow_heavy and agent_result and not agent_result.get("ok"):
            try:
                from advanced_shards.heavy_brain_limb import HeavyBrainLimb

                esc = HeavyBrainLimb().escalate(
                    goal=goal,
                    error=str(agent_result.get("error") or agent_result)[:4000],
                    context=json.dumps(trace, default=str)[:6000],
                )
                trace.append({"step": "brain.escalate", "ok": esc.get("ok"), "preview": (esc.get("text") or "")[:300]})
                # One more coding pass with heavy guidance
                if esc.get("ok") and esc.get("text"):
                    from advanced_shards.coding_limb import CodingLimb

                    retry = CodingLimb().solve(
                        need=f"{goal}\n\nHeavy escalate guidance:\n{esc.get('text')[:4000]}",
                        force_write=True,
                    )
                    trace.append({"step": "coding.retry_after_heavy", "ok": retry.get("ok")})
                    if retry.get("ok"):
                        agent_result = retry
            except Exception as exc:
                trace.append({"step": "brain.escalate", "ok": False, "error": str(exc)})

        # 7) Engineer summary — what was made real
        summary = _ollama_text(
            f"GOAL: {goal}\n\nTRACE:\n{json.dumps(trace, indent=2, default=str)[:5000]}\n\n"
            f"AGENT_RESULT:\n{json.dumps(agent_result, default=str)[:3000]}\n\n"
            "In 4-6 sentences: what the Reality Machine DID, what exists on disk, what is still blocked, next step.",
            "You are Mythos Reality Machine. Honest report only. No fake success.",
        )

        ok = bool(agent_result and agent_result.get("ok"))
        out = {
            "ok": ok,
            "goal": goal[:2000],
            "plan": plan,
            "trace": trace,
            "result": agent_result,
            "research": researched,
            "summary": summary,
            "elapsed_sec": round(time.time() - t0, 1),
            "drives_seen": list((inv.get("drives") or {}).keys()),
            "at": _now(),
            "message": (
                "Reality Machine completed a solve cycle."
                if ok
                else "Reality Machine ran a cycle but the goal is not fully proven yet — keep going / reality.solve again."
            ),
        }
        LAST.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        _ledger_append({"phase": "solve", "at": _now(), "ok": ok, "goal": goal[:200]})
        return out

    def _research(self, topic: str) -> dict[str, Any]:
        """Prefer research.web, then gamecraft, then local planner notes."""
        topic = (topic or "").strip()[:240]
        try:
            from advanced_shards.research_limb import ResearchLimb

            r = ResearchLimb().web(topic=topic, limit=24)
            if isinstance(r, dict) and (r.get("ok") or r.get("answer_preview") or r.get("preview") or r.get("output")):
                preview = (
                    r.get("answer_preview")
                    or r.get("preview")
                    or str(r.get("notes") or r.get("output") or "")[:1200]
                )
                return {
                    "ok": True,
                    "preview": preview,
                    "source": "research.web",
                    "raw": {k: r.get(k) for k in ("output", "topic", "chunks") if k in r},
                }
        except Exception:
            pass
        try:
            from advanced_shards.gamecraft_limb import GamecraftLimb

            gc = GamecraftLimb()
            if hasattr(gc, "scrape"):
                return gc.scrape(topic=topic, limit=16)
        except Exception:
            pass
        text = _ollama_text(
            f"Topic: {topic}\nList 5 concrete technical approaches and likely failure causes. No fluff.",
            "You are the Reality Machine research expert. Be concrete.",
            num_predict=500,
        )
        return {"ok": bool(text), "preview": text, "source": "local_planner_notes", "topic": topic}

    def find_project(self, name: str = "", drives: str = "D,E,G", max_hits: int = 20) -> dict[str, Any]:
        """Search federated drives for a project folder by name."""
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "name required"}
        max_hits = max(1, min(int(max_hits or 20), 50))
        letters = [x.strip().upper() for x in (drives or "D,E,G").split(",") if x.strip()]
        hits: list[dict[str, Any]] = []
        needle = name.lower()
        for letter in letters:
            root = Path(f"{letter}:/")
            if not root.exists():
                continue
            try:
                for p in root.iterdir():
                    if p.is_dir() and needle in p.name.lower():
                        hits.append({"path": str(p), "name": p.name, "drive": letter})
                        if len(hits) >= max_hits:
                            break
            except OSError:
                continue
            if len(hits) >= max_hits:
                break
            for top in ("Sanctuary", "Mythos_Apex", "Mythos_Codex", "StackForge", "Projects", "Documents"):
                base = root / top
                if not base.is_dir():
                    continue
                try:
                    for p in base.iterdir():
                        if p.is_dir() and needle in p.name.lower():
                            hits.append({"path": str(p), "name": p.name, "drive": letter})
                            if len(hits) >= max_hits:
                                break
                except OSError:
                    pass
                if len(hits) >= max_hits:
                    break
        out = {"ok": True, "query": name, "count": len(hits), "hits": hits, "at": _now()}
        _ledger_append({"phase": "find_project", "at": _now(), "query": name, "count": len(hits)})
        return out

    def continue_solve(self, max_steps: int = 8) -> dict[str, Any]:
        """Resume last Reality Machine goal. Never dump drives."""
        goal = ""
        if LAST.is_file():
            try:
                prev = json.loads(LAST.read_text(encoding="utf-8"))
                goal = (prev.get("goal") or "").strip()
            except Exception:
                goal = ""
        if not goal:
            try:
                from mythos_session_mode import get_current_goal

                goal = get_current_goal()
            except Exception:
                goal = ""
        if not goal:
            return {"ok": False, "error": "no prior reality goal — call reality.solve with a goal first"}
        return self.solve(goal=goal, max_steps=max_steps, allow_internet=True, allow_heavy=True)



# CLI smoke
if __name__ == "__main__":
    m = RealityMachineLimb()
    print(json.dumps(m.status(), indent=2, default=str))
