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
QUEUE = STATE_DIR / "heal_queue.json"
CENSUS = STATE_DIR / "last_census.json"
HEAL_LAST = STATE_DIR / "last_heal.json"

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

PROJECT_MARKERS = {
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "CMakeLists.txt",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "project.godot",
    "main.py",
    "app.py",
    "index.js",
    "index.ts",
    "Cargo.lock",
    "composer.json",
    "Makefile",
    "meson.build",
}

SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "recovery",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "msocache",
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "site-packages",
    "appdata",
    "intel",
    "amd",
    "nvidia corporation",
}

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
    "census": "Discover programs on drives by content, not folder names",
    "autonomy": "Heal queue without creator pointing — finished programs, not reports",
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


def _skip_dir(name: str) -> bool:
    return (name or "").strip().lower() in SKIP_DIR_NAMES


def _list_names(path: Path, limit: int = 120) -> list[str]:
    names: list[str] = []
    try:
        for i, p in enumerate(path.iterdir()):
            if i >= limit:
                break
            names.append(p.name)
    except OSError:
        pass
    return names


def _detect_language(names_lower: set[str]) -> str:
    if "project.godot" in names_lower:
        return "gdscript"
    if "package.json" in names_lower or "tsconfig.json" in names_lower:
        return "javascript"
    if "cmakelists.txt" in names_lower or any(n.endswith(".cpp") for n in names_lower):
        return "cpp"
    if "cargo.toml" in names_lower:
        return "rust"
    return "python"


def _project_probe(path: Path) -> dict[str, Any] | None:
    """Return project card if path looks like software — name does not matter."""
    if not path.is_dir() or _skip_dir(path.name):
        return None
    names = _list_names(path)
    if not names:
        return None
    names_lower = {n.lower() for n in names}
    markers = sorted(n for n in names_lower if n in {m.lower() for m in PROJECT_MARKERS})
    # Also accept many .py/.js without classic markers
    codeish = sum(1 for n in names_lower if n.endswith((".py", ".js", ".ts", ".tsx", ".cpp", ".h", ".rs", ".gd")))
    if not markers and codeish < 3:
        return None

    broken: list[str] = []
    priority = 40
    if markers:
        priority += 15
    if codeish >= 8:
        priority += 10

    # Incomplete / error residues
    if any(n.endswith(".incomplete") for n in names_lower):
        broken.append("incomplete_download")
        priority += 25
    if any(n.endswith(".err.log") or n.endswith("_err.log") or "error.log" in n for n in names_lower):
        broken.append("error_log_present")
        priority += 20
    if any("agent_status" in n and n.endswith(".json") for n in names_lower):
        # peek quickly
        for n in names:
            if "agent_status" in n.lower() and n.lower().endswith(".json"):
                try:
                    data = json.loads((path / n).read_text(encoding="utf-8", errors="replace")[:4000])
                    st = str(data.get("status") or data.get("state") or "").lower()
                    if st in {"failed", "error", "blocked", "down"}:
                        broken.append(f"agent_status:{st}")
                        priority += 20
                except Exception:
                    pass
                break

    # README distress signals
    for readme in ("README.md", "README.txt", "readme.md", "STATUS.txt", "TODO.md"):
        rp = path / readme
        if not rp.is_file():
            continue
        try:
            text = rp.read_text(encoding="utf-8", errors="replace")[:2500].lower()
        except OSError:
            continue
        if any(k in text for k in ("broken", "doesn't work", "does not work", "todo", "fixme", "wip", "not working", "blocked")):
            broken.append(f"readme_distress:{readme}")
            priority += 15
        break

    # Has project files but no obvious entrypoint
    entries = {"main.py", "app.py", "index.js", "index.ts", "manage.py", "server.py", "cli.py"}
    if markers and not (names_lower & entries) and "package.json" not in names_lower:
        broken.append("no_clear_entrypoint")
        priority += 8

    if not broken:
        # Still queue unfinished-looking trees with many markers but no tests / launch bat
        if markers and not any(n.endswith((".bat", ".ps1", ".cmd", ".sh")) for n in names_lower):
            broken.append("no_launcher")
            priority += 5

    return {
        "path": str(path),
        "name": path.name,
        "markers": markers[:12],
        "code_files_top": codeish,
        "language": _detect_language(names_lower),
        "broken_signals": broken,
        "needs_heal": bool(broken) or priority >= 55,
        "priority": min(priority, 100),
    }


def _infer_heal_goal(card: dict[str, Any]) -> str:
    path = card.get("path") or ""
    name = card.get("name") or Path(path).name
    signals = ", ".join(card.get("broken_signals") or []) or "unknown breakage"
    lang = card.get("language") or "python"
    markers = ", ".join(card.get("markers") or [])
    return (
        f"Heal and finish this program so it actually runs for its intended purpose. "
        f"Project path: {path}. Name (ignore if misleading): {name}. "
        f"Language hint: {lang}. Markers: {markers}. Broken signals: {signals}. "
        f"Use the internet for docs/errors/libraries as needed. "
        f"Do not ask the creator to point at files. "
        f"Success = working code / launcher on disk that fulfills the program's purpose — not a status report."
    )


class RealityMachineLimb:
    """
    Reality Machine — the creator's resource-aware genius orchestrator.

    Not a clone of Cursor. A Mythos system-MoE that divides problems, uses
    drives + internet + coding loops + Colibri/cloud heavy, and keeps going
    until programs are fixed — not until a report is written.
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
        queue = None
        if QUEUE.is_file():
            try:
                queue = json.loads(QUEUE.read_text(encoding="utf-8"))
            except Exception:
                queue = None
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
            "heal_queue": {
                "pending": len((queue or {}).get("pending") or []) if isinstance(queue, dict) else 0,
                "done": len((queue or {}).get("done") or []) if isinstance(queue, dict) else 0,
            },
            "tools": [
                "reality.status",
                "reality.inventory",
                "reality.census",
                "reality.find_project",
                "reality.route",
                "reality.solve",
                "reality.continue",
                "reality.autonomy",
            ],
            "doctrine": (
                "Creator points at drives once. Machine finds programs by content, not names. "
                "Internet is allowed whenever a fix needs it. "
                "Deliverable is a finished working program — not a report. Never stop after one failure."
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

        # 4) Internet ON whenever allowed — do not wait for creator URLs
        researched = None
        if allow_internet:
            researched = self._research(goal)
            trace.append(
                {
                    "step": "research",
                    "ok": researched.get("ok"),
                    "preview": (researched.get("preview") or "")[:200],
                }
            )

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

        # 7) Outcome — finished program on disk (ledger is internal, not a creator report)
        finished_path = ""
        if isinstance(agent_result, dict):
            finished_path = (
                agent_result.get("project_dir")
                or agent_result.get("path")
                or agent_result.get("filepath")
                or project_dir
                or ""
            )
        ok = bool(agent_result and agent_result.get("ok"))
        out = {
            "ok": ok,
            "goal": goal[:2000],
            "plan": plan,
            "trace": trace,
            "result": agent_result,
            "research": researched,
            "finished_path": finished_path,
            "elapsed_sec": round(time.time() - t0, 1),
            "drives_seen": list((inv.get("drives") or {}).keys()),
            "at": _now(),
            "message": (
                f"Program healed/finished at {finished_path or '(see result)'}"
                if ok
                else "Cycle ran; program not yet proven finished — reality.continue / reality.autonomy will keep going."
            ),
        }
        LAST.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        _ledger_append(
            {"phase": "solve", "at": _now(), "ok": ok, "goal": goal[:200], "finished_path": finished_path}
        )
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

    def census(
        self,
        drives: str = "D,E,G",
        max_projects: int = 80,
        only_broken: bool = True,
        depth: int = 2,
    ) -> dict[str, Any]:
        """
        Discover programs across drives by content markers — folder names do not matter.
        Builds/refreshes the heal queue. Does not wait for the creator to point.
        """
        max_projects = max(5, min(int(max_projects or 80), 200))
        depth = max(1, min(int(depth or 2), 3))
        letters = [x.strip().upper() for x in (drives or "D,E,G").split(",") if x.strip()]
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(card: dict[str, Any] | None) -> None:
            if not card:
                return
            key = str(card.get("path") or "").lower()
            if not key or key in seen:
                return
            seen.add(key)
            found.append(card)

        # Always include known sanctuary roots
        for root in DEFAULT_ROOTS:
            if root.is_dir():
                _add(_project_probe(root))

        for letter in letters:
            drive_root = Path(f"{letter}:/")
            if not drive_root.exists():
                continue
            try:
                tops = list(drive_root.iterdir())
            except OSError:
                continue
            for top in tops:
                if len(found) >= max_projects:
                    break
                if not top.is_dir() or _skip_dir(top.name):
                    continue
                card = _project_probe(top)
                _add(card)
                if depth < 2:
                    continue
                # One level deeper inside large buckets (Sanctuary, Projects, etc.)
                try:
                    children = list(top.iterdir())
                except OSError:
                    continue
                for child in children:
                    if len(found) >= max_projects:
                        break
                    if not child.is_dir() or _skip_dir(child.name):
                        continue
                    _add(_project_probe(child))
                    if depth >= 3:
                        try:
                            grands = list(child.iterdir())
                        except OSError:
                            continue
                        for g in grands[:40]:
                            if len(found) >= max_projects:
                                break
                            if g.is_dir() and not _skip_dir(g.name):
                                _add(_project_probe(g))

        found.sort(key=lambda c: int(c.get("priority") or 0), reverse=True)
        healable = [c for c in found if c.get("needs_heal")]
        targets = healable if only_broken else found

        # Preserve prior done/failed — never wipe progress on re-census
        prev_done: list[dict[str, Any]] = []
        prev_failed: list[dict[str, Any]] = []
        if QUEUE.is_file():
            try:
                prev = json.loads(QUEUE.read_text(encoding="utf-8"))
                prev_done = list(prev.get("done") or [])
                prev_failed = list(prev.get("failed") or [])
            except Exception:
                pass
        healed_paths = {
            str(x.get("path") or "").strip().lower()
            for x in prev_done
            if (x.get("path") and str(x.get("status") or "") in {"healed", "done", "skipped_already_healed"})
        }
        # Also honor dedicated healed ledger
        healed_ledger = STATE_DIR / "healed_paths.json"
        if healed_ledger.is_file():
            try:
                hl = json.loads(healed_ledger.read_text(encoding="utf-8"))
                for p in hl.get("paths") or []:
                    if p:
                        healed_paths.add(str(p).strip().lower())
            except Exception:
                pass

        pending_cards = []
        skipped_healed = 0
        for c in targets:
            key = str(c.get("path") or "").strip().lower()
            if key and key in healed_paths:
                skipped_healed += 1
                continue
            pending_cards.append(c)

        queue = {
            "updated": _now(),
            "pending": [
                {
                    "path": c["path"],
                    "name": c["name"],
                    "language": c.get("language"),
                    "priority": c.get("priority"),
                    "broken_signals": c.get("broken_signals"),
                    "goal": _infer_heal_goal(c),
                    "status": "pending",
                    "attempts": 0,
                }
                for c in pending_cards
            ],
            "done": prev_done[-200:],
            "failed": prev_failed[-100:],
        }
        _ensure_state()
        QUEUE.write_text(json.dumps(queue, indent=2), encoding="utf-8")
        out = {
            "ok": True,
            "scanned_drives": letters,
            "projects_found": len(found),
            "heal_queue_size": len(queue["pending"]),
            "skipped_already_healed": skipped_healed,
            "preserved_done": len(prev_done),
            "top": pending_cards[:15],
            "queue_path": str(QUEUE),
            "at": _now(),
            "message": (
                f"Census complete — {len(queue['pending'])} programs queued "
                f"(skipped {skipped_healed} already healed). "
                "Call reality.autonomy to fix them (internet on, no pointing)."
            ),
        }
        CENSUS.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        _ledger_append(
            {
                "phase": "census",
                "at": _now(),
                "found": len(found),
                "queued": len(queue["pending"]),
            }
        )
        return out

    def autonomy(
        self,
        drives: str = "D,E,G",
        max_projects: int = 3,
        max_steps_each: int = 6,
        refresh_census: bool = False,
        allow_internet: bool = True,
        allow_heavy: bool = True,
        only_broken: bool = True,
    ) -> dict[str, Any]:
        """
        Point-at-drives mode: heal next N programs from the persistent queue.

        - Does NOT re-census by default (avoids redoing already-healed programs).
        - Internet ON whenever a fix needs docs/errors/packages.
        - Success = finished working program; healed paths are remembered.
        """
        max_projects = max(1, min(int(max_projects or 3), 12))
        max_steps_each = max(2, min(int(max_steps_each or 6), 12))
        allow_internet = bool(allow_internet)
        t0 = time.time()
        _ensure_state()

        # Only census when forced, or when there is no queue / empty pending
        need_census = bool(refresh_census) or (not QUEUE.is_file())
        if not need_census and QUEUE.is_file():
            try:
                peek = json.loads(QUEUE.read_text(encoding="utf-8"))
                if not (peek.get("pending") or []):
                    need_census = True
            except Exception:
                need_census = True
        if need_census:
            self.census(drives=drives, max_projects=80, only_broken=only_broken, depth=2)

        try:
            queue = json.loads(QUEUE.read_text(encoding="utf-8"))
        except Exception:
            queue = {"pending": [], "done": [], "failed": []}
        pending = list(queue.get("pending") or [])
        done = list(queue.get("done") or [])
        failed = list(queue.get("failed") or [])

        healed_ledger = STATE_DIR / "healed_paths.json"
        healed_paths: set[str] = set()
        if healed_ledger.is_file():
            try:
                healed_paths = {
                    str(p).strip().lower()
                    for p in (json.loads(healed_ledger.read_text(encoding="utf-8")).get("paths") or [])
                    if p
                }
            except Exception:
                healed_paths = set()
        for x in done:
            p = str(x.get("path") or "").strip().lower()
            if p:
                healed_paths.add(p)

        # Drop anything already healed that snuck back into pending
        clean_pending: list[dict[str, Any]] = []
        seen_pending: set[str] = set()
        for item in pending:
            key = str(item.get("path") or "").strip().lower()
            if not key or key in healed_paths or key in seen_pending:
                continue
            seen_pending.add(key)
            clean_pending.append(item)
        pending = clean_pending

        results: list[dict[str, Any]] = []
        healed = 0
        batch = pending[:max_projects]
        remaining = pending[max_projects:]

        for item in batch:
            path = (item.get("path") or "").strip()
            key = path.lower()
            if key in healed_paths:
                results.append(
                    {
                        "path": path,
                        "ok": True,
                        "skipped": True,
                        "message": "already healed — not repeating",
                        "at": _now(),
                    }
                )
                continue
            goal = (item.get("goal") or "").strip() or _infer_heal_goal(item)
            lang = (item.get("language") or "python").strip()
            attempts = int(item.get("attempts") or 0) + 1
            cycle = self.solve(
                goal=goal,
                project_dir=path,
                max_steps=max_steps_each,
                allow_internet=allow_internet,
                allow_heavy=allow_heavy,
                allow_colibri_master=False,
                language=lang,
            )
            entry = {
                "path": path,
                "ok": bool(cycle.get("ok")),
                "finished_path": cycle.get("finished_path") or path,
                "message": cycle.get("message"),
                "attempts": attempts,
                "at": _now(),
            }
            results.append(entry)
            if cycle.get("ok"):
                healed += 1
                healed_paths.add(key)
                done.append(
                    {
                        **item,
                        "status": "healed",
                        "finished_at": _now(),
                        "attempts": attempts,
                    }
                )
            else:
                item = {**item, "attempts": attempts}
                failed.append(
                    {
                        **item,
                        "status": "retry",
                        "last_error": cycle.get("message"),
                        "at": _now(),
                    }
                )
                # At most 2 attempts then park in failed (do not loop forever on same project)
                if attempts < 2:
                    remaining.append(item)

        queue_out = {
            "updated": _now(),
            "pending": remaining,
            "done": done[-300:],
            "failed": failed[-150:],
        }
        QUEUE.write_text(json.dumps(queue_out, indent=2), encoding="utf-8")
        healed_ledger.write_text(
            json.dumps({"updated": _now(), "paths": sorted(healed_paths)}, indent=2),
            encoding="utf-8",
        )

        still = len(remaining)
        out = {
            "ok": healed > 0 or still == 0,
            "healed_this_run": healed,
            "attempted": len(batch),
            "still_pending": still,
            "results": results,
            "internet": allow_internet,
            "refresh_census_used": need_census,
            "elapsed_sec": round(time.time() - t0, 1),
            "at": _now(),
            "message": (
                f"Healed {healed}/{len(batch)} programs this run. "
                + (
                    f"{still} still queued — keep going (will NOT redo already healed)."
                    if still
                    else "Heal queue empty."
                )
            ),
            "deliverable": "fixed programs on disk",
            "not_deliverable": "status reports",
        }
        HEAL_LAST.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        _ledger_append(
            {
                "phase": "autonomy",
                "at": _now(),
                "healed": healed,
                "attempted": len(batch),
                "pending": still,
            }
        )
        return out

    def continue_solve(self, max_steps: int = 8) -> dict[str, Any]:
        """Resume heal queue if present; else last Reality Machine goal."""
        if QUEUE.is_file():
            try:
                q = json.loads(QUEUE.read_text(encoding="utf-8"))
                if q.get("pending"):
                    return self.autonomy(
                        refresh_census=False,
                        max_projects=2,
                        max_steps_each=max(4, min(int(max_steps or 8), 12)),
                        allow_internet=True,
                        allow_heavy=True,
                    )
            except Exception:
                pass
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
            return {
                "ok": False,
                "error": "no heal queue and no prior goal — call reality.autonomy (point at drives) or reality.solve",
            }
        return self.solve(goal=goal, max_steps=max_steps, allow_internet=True, allow_heavy=True)


# CLI smoke
if __name__ == "__main__":
    m = RealityMachineLimb()
    print(json.dumps(m.status(), indent=2, default=str))
