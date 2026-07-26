#!/usr/bin/env python3
"""
Heavy brain limb — Colibri (or GGUF llama-server) on demand.

Daily chat stays on Ollama. When Mythos is stuck or needs deep reasoning,
call brain.heavy / brain.think. Optionally auto-starts coli serve if down.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_COLIBRI_API = "http://127.0.0.1:8010/v1"
DEFAULT_GGUF_API = "http://127.0.0.1:8088/v1"
COLIBRI_SERVE_BAT = Path(r"D:\colibri\START_COLIBRI_SERVE.bat")
COLIBRI_MODEL_DIR = Path(os.environ.get("COLI_MODEL") or r"D:\glm52_i4")
# Rough floor: refuse auto-start until weights look substantially present (~full pack ~370GB)
MIN_MODEL_BYTES = 300 * (1024**3)  # 300 GB — do not start mid-download


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_base() -> str:
    raw = (
        os.environ.get("MYTHOS_HEAVY_API")
        or os.environ.get("OPENAI_BASE_URL")
        or DEFAULT_COLIBRI_API
    ).strip().rstrip("/")
    if raw.endswith("/chat/completions"):
        raw = raw[: -len("/chat/completions")]
    return raw


def _model_name() -> str:
    return (os.environ.get("MYTHOS_HEAVY_MODEL") or "colibri-glm52").strip()


def _probe(base: str, timeout: float = 3.0) -> dict[str, Any]:
    """Probe OpenAI-compatible /models (or root)."""
    base = (base or "").rstrip("/")
    urls = [f"{base}/models", base.replace("/v1", "") + "/health", base]
    last_err = ""
    for url in urls:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")[:2000]
                return {"ok": True, "url": url, "status": getattr(resp, "status", 200), "body": body}
        except Exception as exc:
            last_err = str(exc)
    return {"ok": False, "error": last_err or "unreachable", "base": base}


def _model_dir_bytes(path: Path) -> int:
    """Fast size estimate — top-level + one nested level only (no full tree walk)."""
    if not path.exists():
        return 0
    total = 0
    try:
        for p in path.iterdir():
            try:
                if p.is_file():
                    total += p.stat().st_size
                elif p.is_dir():
                    for c in p.iterdir():
                        try:
                            if c.is_file():
                                total += c.stat().st_size
                        except OSError:
                            pass
            except OSError:
                pass
    except OSError:
        pass
    return total


def _weights_look_ready(path: Path) -> bool:
    """True if Colibri model dir looks substantial enough to try starting serve."""
    if not path.is_dir():
        return False
    # Quick incomplete check — top level + common HF subdirs only (no full tree walk)
    check_dirs = [path, path / ".cache", path / "blobs"]
    for d in check_dirs:
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                name = p.name.lower()
                if name.endswith(".incomplete") or name.endswith(".lock"):
                    return False
        except OSError:
            pass
    return _model_dir_bytes(path) >= MIN_MODEL_BYTES


def _chat_completions(
    *,
    base: str,
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.3,
    max_tokens: int = 1200,
    timeout: float = 600.0,
) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # Colibri tip: topp helps quality on slow MoE
    payload["top_p"] = float(os.environ.get("MYTHOS_HEAVY_TOP_P") or 0.85)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            pass
        return {"ok": False, "error": f"HTTP {exc.code}: {err_body or exc.reason}", "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}

    try:
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = (msg.get("content") or "").strip()
    except Exception:
        text = ""
        choice = {}
    return {
        "ok": bool(text),
        "text": text,
        "model": raw.get("model") or model,
        "usage": raw.get("usage"),
        "raw_choice": {k: choice.get(k) for k in ("finish_reason", "index") if k in choice},
        "url": url,
    }


class HeavyBrainLimb:
    """On-demand frontier brain (Colibri / GGUF) — not daily chat."""

    def status(self) -> dict[str, Any]:
        base = _api_base()
        probe = _probe(base)
        model_bytes = _model_dir_bytes(COLIBRI_MODEL_DIR)
        gguf_probe = _probe(DEFAULT_GGUF_API) if base != DEFAULT_GGUF_API.rstrip("/") else {"ok": False, "skipped": True}
        return {
            "ok": True,
            "limb": "heavy_brain",
            "daily_chat": "ollama (unchanged)",
            "heavy_api": base,
            "heavy_model": _model_name(),
            "heavy_ready": bool(probe.get("ok")),
            "probe": probe,
            "colibri_model_dir": str(COLIBRI_MODEL_DIR),
            "colibri_model_gb": round(model_bytes / (1024**3), 2),
            "colibri_weights_enough_to_start": _weights_look_ready(COLIBRI_MODEL_DIR),
            "serve_bat": str(COLIBRI_SERVE_BAT),
            "serve_bat_exists": COLIBRI_SERVE_BAT.is_file(),
            "gguf_fallback_8088": gguf_probe,
            "tools": ["brain.status", "brain.ensure", "brain.think", "brain.heavy", "brain.escalate"],
            "note": "Agents call brain.heavy when stuck; you do not need to paste into Colibri chat.",
        }

    def ensure(self, wait_sec: int = 45, start_if_down: bool = True) -> dict[str, Any]:
        """
        Make sure the heavy API is answering. If down and start_if_down,
        launch START_COLIBRI_SERVE.bat (when weights look ready).
        """
        base = _api_base()
        probe = _probe(base)
        if probe.get("ok"):
            return {"ok": True, "already_up": True, "api": base, "probe": probe}

        # Prefer Colibri if env points at 8000; else try GGUF 8088 if already up
        if not probe.get("ok"):
            alt = _probe(DEFAULT_GGUF_API)
            if alt.get("ok"):
                os.environ["MYTHOS_HEAVY_API"] = DEFAULT_GGUF_API
                return {
                    "ok": True,
                    "already_up": True,
                    "api": DEFAULT_GGUF_API,
                    "switched_to": "gguf_8088",
                    "probe": alt,
                    "note": "Colibri down; using live GGUF llama-server on :8088",
                }

        if not start_if_down:
            return {"ok": False, "error": "heavy API down", "api": base, "probe": probe}

        if not _weights_look_ready(COLIBRI_MODEL_DIR):
            model_bytes = _model_dir_bytes(COLIBRI_MODEL_DIR)
            return {
                "ok": False,
                "error": "Colibri weights not ready yet — still downloading",
                "colibri_model_gb": round(model_bytes / (1024**3), 2),
                "need_gb_approx": 370,
                "api": base,
                "hint": "Run D:\\colibri\\CHECK_DOWNLOAD.bat; when done, START_COLIBRI_SERVE.bat",
            }

        if not COLIBRI_SERVE_BAT.is_file():
            return {"ok": False, "error": f"missing serve bat: {COLIBRI_SERVE_BAT}"}

        try:
            # Detached so Mythos does not block on the server window
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(COLIBRI_SERVE_BAT)],
                cwd=str(COLIBRI_SERVE_BAT.parent),
                close_fds=True,
            )
        except Exception as exc:
            return {"ok": False, "error": f"failed to start Colibri: {exc}"}

        wait_sec = max(5, min(int(wait_sec or 45), 300))
        deadline = time.time() + wait_sec
        last = probe
        while time.time() < deadline:
            time.sleep(3)
            last = _probe(base, timeout=4.0)
            if last.get("ok"):
                return {
                    "ok": True,
                    "started": True,
                    "api": base,
                    "waited_sec": round(wait_sec - (deadline - time.time()), 1),
                    "probe": last,
                    "at": _now(),
                }

        return {
            "ok": False,
            "started": True,
            "error": f"started serve bat but API not up within {wait_sec}s (model load can take longer)",
            "api": base,
            "probe": last,
            "hint": "Leave the Colibri window open; retry brain.ensure / brain.heavy",
        }

    def think(
        self,
        prompt: str = "",
        system: str = "",
        max_tokens: int = 1200,
        ensure_up: bool = True,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Ask the heavy brain one shot. Auto-ensure server if ensure_up."""
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt required"}

        if ensure_up:
            ready = self.ensure(wait_sec=60, start_if_down=True)
            if not ready.get("ok"):
                return {
                    "ok": False,
                    "error": ready.get("error") or "heavy brain not available",
                    "ensure": ready,
                    "fallback": "daily Ollama still available for normal chat",
                }

        base = _api_base()
        sys_msg = (system or "").strip() or (
            "You are Mythos heavy brain (Colibri/GLM). Be concrete, correct, and actionable. "
            "You assist Apex/Codex on hard jobs — plans, debugging, deep reasoning."
        )
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt[:24000]},
        ]
        t0 = time.time()
        result = _chat_completions(
            base=base,
            messages=messages,
            model=_model_name(),
            temperature=float(temperature or 0.3),
            max_tokens=max(64, min(int(max_tokens or 1200), 8192)),
        )
        result["elapsed_sec"] = round(time.time() - t0, 2)
        result["api"] = base
        result["limb"] = "heavy_brain"
        result["at"] = _now()
        if result.get("ok"):
            result["message"] = "Heavy brain answered — use this for the hard step; daily chat stays on Ollama."
        return result

    def heavy(
        self,
        prompt: str = "",
        system: str = "",
        max_tokens: int = 1200,
        ensure_up: bool = True,
    ) -> dict[str, Any]:
        """Alias of think — tool name brain.heavy."""
        return self.think(
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            ensure_up=ensure_up,
        )

    def escalate(
        self,
        goal: str = "",
        error: str = "",
        context: str = "",
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        """
        Structured escalate for agent.loop / stuck coding:
        returns a repair plan + suggested rewrite guidance from the heavy brain.
        """
        goal = (goal or "").strip() or "unspecified goal"
        error = (error or "").strip()
        context = (context or "").strip()
        prompt = (
            f"Mythos agent is stuck.\n\nGOAL:\n{goal}\n\n"
            f"ERROR / STDERR:\n{error[:4000]}\n\n"
            f"CONTEXT:\n{context[:6000]}\n\n"
            "Respond with:\n"
            "1) Root cause (short)\n"
            "2) Concrete fix steps\n"
            "3) Exact code changes or a full replacement sketch if needed\n"
            "Be specific enough that a 7B coder can implement it."
        )
        out = self.think(
            prompt=prompt,
            system=(
                "You are the heavy escalate brain for Mythos. Diagnose and prescribe fixes. "
                "No fluff. Prefer runnable fixes."
            ),
            max_tokens=max_tokens,
            ensure_up=True,
        )
        out["escalated"] = True
        out["goal"] = goal[:200]
        return out
