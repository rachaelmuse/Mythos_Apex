#!/usr/bin/env python3
"""
Agent loop — Cursor-style plan → edit → run → observe → retry.

Free-flow coding without babysitting: Mythos keeps iterating on a goal
until tests pass or max_steps is hit. Uses CodingLimb + optional research.web.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT, detect_chat_model, get_ollama_client

RUNS_DIR = Path(APEX_ROOT) / "mythos_state" / "agent_runs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ollama_json(prompt: str, *, system: str, num_predict: int = 800) -> dict[str, Any] | None:
    model = os.environ.get("MYTHOS_CODER_MODEL") or detect_chat_model()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt[:6000]},
    ]
    try:
        client = get_ollama_client()
        if client is not None and hasattr(client, "chat"):
            resp = client.chat(
                model=model,
                messages=messages,
                options={"temperature": 0.2, "num_predict": num_predict},
            )
            text = ((resp or {}).get("message") or {}).get("content") or ""
        else:
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
                text = ((data.get("message") or {}).get("content") or "")
    except Exception as exc:
        return {"_error": str(exc)}

    text = (text or "").strip()
    # extract JSON object/array
    m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", text)
    if not m:
        return {"_raw": text}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {"_raw": text}


class AgentLoopLimb:
    """Multi-step autonomous coding loop."""

    def status(self) -> dict[str, Any]:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        runs = sorted(RUNS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        latest = None
        if runs:
            try:
                latest = json.loads(runs[0].read_text(encoding="utf-8"))
            except Exception:
                latest = {"path": str(runs[0])}
        return {
            "ok": True,
            "limb": "agent_loop",
            "runs_dir": str(RUNS_DIR),
            "recent_runs": len(runs),
            "latest": {
                "id": (latest or {}).get("id"),
                "goal": ((latest or {}).get("goal") or "")[:120],
                "ok": (latest or {}).get("ok"),
                "steps": len((latest or {}).get("trace") or []),
            }
            if latest
            else None,
            "tools": ["agent.status", "agent.loop", "agent.last"],
            "note": "plan → edit → run → observe → research/heavy → retry until done or max_steps",
            "heavy": "allow_heavy=true escalates to brain.heavy (Colibri/GGUF) when stuck",
        }

    def last(self) -> dict[str, Any]:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        runs = sorted(RUNS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not runs:
            return {"ok": False, "error": "no agent runs yet"}
        try:
            data = json.loads(runs[0].read_text(encoding="utf-8"))
            return {"ok": True, "run": data, "path": str(runs[0])}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "path": str(runs[0])}

    def loop(
        self,
        goal: str = "",
        project_dir: str = "",
        max_steps: int = 6,
        run_test: str = "",
        allow_online: bool = True,
        allow_heavy: bool = True,
        language: str = "python",
    ) -> dict[str, Any]:
        """
        Autonomous coding loop on a project folder.

        goal: what to build/fix
        project_dir: relative under Apex home (default projects/agent_<stamp>/)
        max_steps: plan/edit/run cycles (1–12)
        run_test: optional command file to run each observe (default: main.py)
        allow_online: if True, research.web on repeated failures
        allow_heavy: if True, call Colibri/GGUF heavy brain when stuck (no babysitting)
        """
        goal = (goal or "").strip()
        if not goal:
            return {"ok": False, "error": "goal required"}

        try:
            max_steps = max(1, min(int(max_steps or 6), 12))
        except (TypeError, ValueError):
            max_steps = 6

        # Env override: MYTHOS_ALLOW_HEAVY=0 disables escalate even if allow_heavy=True
        env_heavy = (os.environ.get("MYTHOS_ALLOW_HEAVY") or "1").strip().lower()
        if env_heavy in {"0", "false", "no", "off"}:
            allow_heavy = False

        from advanced_shards.coding_limb import CodingLimb

        coder = CodingLimb()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{stamp}"

        if not (project_dir or "").strip():
            slug = "".join(ch if ch.isalnum() else "_" for ch in goal.lower())[:40].strip("_") or "task"
            project_dir = f"projects/agent_{slug}_{stamp}"
        project_dir = project_dir.strip().replace("\\", "/").lstrip("/")

        main_rel = f"{project_dir}/main.py"
        if (language or "python").lower() in {"gdscript", "gd"}:
            main_rel = f"{project_dir}/main.gd"

        test_rel = (run_test or "").strip() or main_rel

        trace: list[dict[str, Any]] = []
        plan = self._make_plan(goal, project_dir=project_dir, language=language or "python")
        trace.append({"phase": "plan", "at": _now(), "plan": plan})

        # Ensure project exists with an initial write
        init = coder.solve(
            need=f"{goal}\n\nWrite a complete working {language} program for this goal.",
            filepath=main_rel,
            language=language or "python",
            force_write=True,
        )
        trace.append({"phase": "seed", "at": _now(), "result": _trim(init)})

        success = False
        last_obs: dict[str, Any] = {}
        fail_streak = 0
        heavy_hint = ""
        heavy_used = 0

        for step in range(1, max_steps + 1):
            # RUN
            if (language or "python").lower() in {"python", "py", ""}:
                obs = coder.run_python(test_rel, timeout=90)
            else:
                obs = {
                    "ok": True,
                    "note": "non-python: skipped execute; treat write as observe",
                    "path": test_rel,
                }
            last_obs = obs
            trace.append({"phase": "run", "step": step, "at": _now(), "observe": _trim(obs)})

            if obs.get("ok"):
                # Light verify: ask model if goal seems met
                check = self._goal_met(goal, obs)
                trace.append({"phase": "check", "step": step, "at": _now(), "check": check})
                if check.get("done"):
                    success = True
                    break
                # If ran OK but goal not done, continue improving
                fail_streak = 0
            else:
                fail_streak += 1

            # Online research when stuck
            researched: dict[str, Any] | None = None
            if allow_online and fail_streak >= 2:
                topic = self._error_topic(goal, obs)
                researched = self._research(topic)
                trace.append({"phase": "research", "step": step, "at": _now(), "topic": topic, "result": _trim(researched)})

            # Heavy brain (Colibri/GGUF) when still stuck — agents call it without creator
            if allow_heavy and fail_streak >= 2 and heavy_used < 2:
                escalated = self._heavy_escalate(goal, obs, plan=plan, research=researched)
                trace.append({"phase": "heavy", "step": step, "at": _now(), "result": _trim(escalated)})
                if escalated.get("ok") and escalated.get("text"):
                    heavy_hint = str(escalated.get("text") or "")[:4000]
                    heavy_used += 1
                # Reset streak after escalate so we don't hammer Colibri every step
                fail_streak = 0
            elif allow_online and fail_streak >= 2:
                fail_streak = 0

            # EDIT / RETRY — regenerate or patch based on observation
            fix_need = (
                f"GOAL: {goal}\n"
                f"PROJECT: {project_dir}\n"
                f"Last run ok={obs.get('ok')} exit={obs.get('exit_code')}\n"
                f"STDOUT:\n{(obs.get('stdout') or '')[-1500:]}\n"
                f"STDERR:\n{(obs.get('stderr') or obs.get('error') or '')[-1500:]}\n"
                f"Plan so far: {json.dumps(plan)[:800]}\n"
            )
            if heavy_hint:
                fix_need += f"HEAVY BRAIN GUIDANCE (follow this):\n{heavy_hint}\n"
            fix_need += "Rewrite the program so it fully achieves the goal and runs cleanly."
            rewritten = coder.solve(
                need=fix_need,
                filepath=main_rel,
                language=language or "python",
                force_write=True,
            )
            trace.append({"phase": "edit", "step": step, "at": _now(), "result": _trim(rewritten)})
            time.sleep(0.2)

        # Final run
        if (language or "python").lower() in {"python", "py", ""}:
            final = coder.run_python(test_rel, timeout=90)
        else:
            final = last_obs
        trace.append({"phase": "final_run", "at": _now(), "observe": _trim(final)})
        if final.get("ok") and not success:
            # accept run-ok as soft success if model says done or no stderr
            success = bool(self._goal_met(goal, final).get("done")) or not (final.get("stderr") or "").strip()

        record = {
            "id": run_id,
            "at": _now(),
            "goal": goal,
            "project_dir": project_dir,
            "main": main_rel,
            "max_steps": max_steps,
            "allow_heavy": bool(allow_heavy),
            "heavy_used": heavy_used,
            "ok": bool(success and final.get("ok")),
            "final": _trim(final),
            "plan": plan,
            "trace": trace,
            "home": APEX_ROOT,
        }
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        out = RUNS_DIR / f"{run_id}.json"
        out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        # Durable learning
        try:
            from mythos_solutions_ledger import record_solution

            if record["ok"]:
                record_solution(
                    problem=f"agent.loop: {goal[:200]}",
                    solution=f"Completed under {project_dir}; main={main_rel}",
                    tags=["agent_loop", "coding"],
                    source="agent.loop",
                )
        except Exception:
            pass

        return {
            "ok": record["ok"],
            "run_id": run_id,
            "goal": goal,
            "project_dir": project_dir,
            "main": main_rel,
            "steps_used": len([t for t in trace if t.get("phase") == "run"]),
            "final": _trim(final),
            "path": str(out),
            "message": (
                "Goal met — see project + run log."
                if record["ok"]
                else "Stopped without clean success — inspect stderr in run log and retry agent.loop."
            ),
        }

    def _make_plan(self, goal: str, *, project_dir: str, language: str) -> Any:
        data = _ollama_json(
            f"Goal: {goal}\nProject dir: {project_dir}\nLanguage: {language}\n"
            "Return JSON: {\"steps\": [\"...\", \"...\"]} — 3 to 6 concrete coding steps.",
            system="You are Mythos coding planner. Output ONLY JSON.",
            num_predict=400,
        )
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            return data
        return {"steps": ["Write main entry", "Implement core logic", "Handle errors", "Verify run"], "raw": data}

    def _goal_met(self, goal: str, obs: dict[str, Any]) -> dict[str, Any]:
        data = _ollama_json(
            f"Goal: {goal}\nRun ok={obs.get('ok')}\nstdout:\n{(obs.get('stdout') or '')[-1200:]}\n"
            f"stderr:\n{(obs.get('stderr') or '')[-800:]}\n"
            'Return JSON: {"done": true/false, "reason": "..."}. '
            "done=true only if the program clearly achieved the goal and ran without fatal errors.",
            system="Strict judge. Output ONLY JSON.",
            num_predict=200,
        )
        if isinstance(data, dict) and "done" in data:
            return {"done": bool(data.get("done")), "reason": data.get("reason"), "raw": data}
        # Heuristic fallback
        if obs.get("ok") and not (obs.get("stderr") or "").strip():
            return {"done": True, "reason": "ran clean (heuristic)", "raw": data}
        return {"done": False, "reason": "uncertain", "raw": data}

    def _error_topic(self, goal: str, obs: dict[str, Any]) -> str:
        err = (obs.get("stderr") or obs.get("error") or "")[:300]
        return f"python fix: {err or goal}"[:240]

    def _research(self, topic: str) -> dict[str, Any]:
        try:
            from advanced_shards.research_limb import ResearchLimb

            return ResearchLimb().web(topic=topic, limit=16)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _heavy_escalate(
        self,
        goal: str,
        obs: dict[str, Any],
        *,
        plan: Any = None,
        research: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            from advanced_shards.heavy_brain_limb import HeavyBrainLimb

            ctx_parts = [f"plan={json.dumps(plan)[:600]}"]
            if research:
                preview = research.get("answer_preview") or research.get("preview") or ""
                ctx_parts.append(f"research={str(preview)[:800]}")
            return HeavyBrainLimb().escalate(
                goal=goal,
                error=(obs.get("stderr") or obs.get("error") or "")[:3000],
                context="\n".join(ctx_parts),
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def _trim(obj: Any, limit: int = 2500) -> Any:
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) <= limit:
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and len(v) > 800:
                out[k] = v[:800] + "…"
            else:
                out[k] = v
        return out
    return s[:limit] + "…"
