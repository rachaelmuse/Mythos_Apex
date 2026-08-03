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
DEFAULT_OLLAMA_API = "http://127.0.0.1:11434/v1"
COLIBRI_SERVE_BAT = Path(r"D:\colibri\START_COLIBRI_SERVE.bat")
COLIBRI_MODEL_DIR = Path(os.environ.get("COLI_MODEL") or r"D:\glm52_i4")
# Rough floor: refuse auto-start until weights look substantially present (~full pack ~370GB)
MIN_MODEL_BYTES = 300 * (1024**3)  # 300 GB — do not start mid-download
# Windows CPU Colibri OOMs mid-prefill below this free RAM on ~15GB boxes
COLIBRI_MIN_FREE_GB = float(os.environ.get("MYTHOS_COLIBRI_MIN_FREE_GB") or 8.0)
# Prefer larger local Ollama models when Colibri chat cannot finish
OLLAMA_HEAVY_PREFER = [
    "qwen3-coder:30b",
    "qwen3:30b",
    "nemotron-nano-8gb:latest",
    "nemotron-nano:latest",
    "qwen2.5-coder:7b",
    "huihui_ai/qwen2.5-abliterate:7b",
    "llama3.1:8b",
]
# When free RAM is tight, skip 30B-class locals (they OOM like Colibri).
# Under ~6GB free, nemotron-nano-8gb also OOMs (~12GB alloc) — prefer true 7B first.
OLLAMA_HEAVY_LOW_RAM = [
    "qwen2.5-coder:7b",
    "huihui_ai/qwen2.5-abliterate:7b",
    "llama3.1:8b",
    "gemma2:9b",
    "phi3:medium",
    "nemotron-nano-8gb:latest",
    "nemotron-nano:latest",
]
OLLAMA_HEAVY_MID_RAM = [
    "nemotron-nano-8gb:latest",
    "nemotron-nano:latest",
    "qwen2.5-coder:7b",
    "huihui_ai/qwen2.5-abliterate:7b",
    "llama3.1:8b",
    "gemma2:9b",
]


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
    return (os.environ.get("MYTHOS_HEAVY_MODEL") or "glm-5.2-colibri").strip()


def _cloud_allowed() -> bool:
    flag = (os.environ.get("MYTHOS_ALLOW_CLOUD_HEAVY") or "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _cloud_api_base() -> str:
    raw = (os.environ.get("MYTHOS_HEAVY_CLOUD_API") or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith("/chat/completions"):
        raw = raw[: -len("/chat/completions")]
    return raw


def _cloud_model_name() -> str:
    return (os.environ.get("MYTHOS_HEAVY_CLOUD_MODEL") or "gpt-4o-mini").strip()


def _free_ram_gb() -> float | None:
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

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return round(stat.ullAvailPhys / (1024**3), 2)
    except Exception:
        return None
    return None


def _is_colibri_base(base: str) -> bool:
    b = (base or "").lower()
    return ":8010" in b or ":8000" in b or "colibri" in b


def _colibri_chat_viable() -> dict[str, Any]:
    """Models-up is not enough — chat OOM slab if free RAM is too low."""
    free = _free_ram_gb()
    need = COLIBRI_MIN_FREE_GB
    ok = free is not None and free >= need
    return {
        "ok": ok,
        "free_gb": free,
        "need_free_gb": need,
        "reason": None
        if ok
        else (
            f"only {free} GB free - Colibri chat needs >={need} GB free on this Windows CPU build "
            "(otherwise OOM slab mid-prefill while /v1/models still looks healthy)"
        ),
    }


def _first_model_id(probe: dict[str, Any]) -> str:
    body = probe.get("body") or ""
    try:
        data = json.loads(body)
        rows = data.get("data") or []
        if rows and isinstance(rows[0], dict) and rows[0].get("id"):
            return str(rows[0]["id"])
    except Exception:
        pass
    return ""


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
    headers = {"Content-Type": "application/json"}
    api_key = (
        os.environ.get("MYTHOS_HEAVY_CLOUD_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("MYTHOS_HEAVY_API_KEY")
        or ""
    ).strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
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



def _try_cloud_ready() -> dict[str, Any]:
    """Third heavy tier: gated OpenAI-compatible cloud API."""
    if not _cloud_allowed():
        return {"ok": False, "skipped": True, "reason": "MYTHOS_ALLOW_CLOUD_HEAVY not enabled"}
    cloud_base = _cloud_api_base()
    if not cloud_base:
        return {"ok": False, "error": "MYTHOS_HEAVY_CLOUD_API not set"}
    probe = _probe(cloud_base, timeout=8.0)
    # Some cloud hosts reject /models — still allow if key present
    key = (
        os.environ.get("MYTHOS_HEAVY_CLOUD_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    if probe.get("ok") or key:
        os.environ["MYTHOS_HEAVY_API"] = cloud_base
        os.environ["MYTHOS_HEAVY_MODEL"] = _cloud_model_name()
        return {
            "ok": True,
            "already_up": True,
            "api": cloud_base,
            "switched_to": "cloud",
            "cloud_fallback": True,
            "probe": probe,
            "model": _cloud_model_name(),
            "note": "Local heavy down; using gated cloud OpenAI-compatible API",
        }
    return {"ok": False, "error": probe.get("error") or "cloud unreachable", "api": cloud_base, "probe": probe}


def _try_gguf_ready() -> dict[str, Any]:
    probe = _probe(DEFAULT_GGUF_API, timeout=4.0)
    if not probe.get("ok"):
        return {"ok": False, "skipped": True, "reason": "GGUF :8088 not listening", "probe": probe}
    model = _first_model_id(probe) or (os.environ.get("MYTHOS_GGUF_MODEL") or "").strip() or "local-gguf"
    os.environ["MYTHOS_HEAVY_API"] = DEFAULT_GGUF_API
    os.environ["MYTHOS_HEAVY_MODEL"] = model
    return {
        "ok": True,
        "api": DEFAULT_GGUF_API,
        "switched_to": "gguf_8088",
        "model": model,
        "probe": probe,
        "note": "Using GGUF llama-server on :8088",
    }


def _ollama_installed_names() -> list[str]:
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return [str(m.get("name") or "") for m in (data.get("models") or []) if m.get("name")]
    except Exception:
        return []


def _try_ollama_heavy(prefer: list[str] | None = None) -> dict[str, Any]:
    """Local MoE/coding fallback while Colibri chat is physics-blocked."""
    names = _ollama_installed_names()
    if not names:
        return {"ok": False, "skipped": True, "reason": "Ollama not reachable or no models"}
    free = _free_ram_gb()
    prefer_env = (os.environ.get("MYTHOS_OLLAMA_HEAVY_MODEL") or "").strip()
    if prefer is None:
        # 30B-class needs ~12GB+; nemotron-nano also ~12GB alloc — pick by free RAM
        if free is not None and free < 6.0:
            prefer = list(OLLAMA_HEAVY_LOW_RAM)
        elif free is not None and free < 10.0:
            prefer = list(OLLAMA_HEAVY_MID_RAM)
        else:
            prefer = list(OLLAMA_HEAVY_PREFER)
    candidates: list[str] = []
    if prefer_env and prefer_env in names:
        candidates.append(prefer_env)
    lower = {n.lower(): n for n in names}
    for want in prefer:
        if want.lower() in lower:
            n = lower[want.lower()]
            if n not in candidates:
                candidates.append(n)
            continue
        stem = want.lower().split(":")[0]
        for n in names:
            if n.lower().startswith(stem) and n not in candidates:
                candidates.append(n)
                break
    if not candidates:
        for n in names:
            nl = n.lower()
            if "embed" in nl or "moondream" in nl:
                continue
            candidates.append(n)
            break
    if not candidates:
        return {"ok": False, "error": "no suitable Ollama heavy model installed", "available": names[:12]}
    chosen = candidates[0]
    os.environ["MYTHOS_HEAVY_API"] = DEFAULT_OLLAMA_API
    os.environ["MYTHOS_HEAVY_MODEL"] = chosen
    return {
        "ok": True,
        "api": DEFAULT_OLLAMA_API,
        "switched_to": "ollama_heavy",
        "model": chosen,
        "candidates": candidates[:6],
        "free_gb": free,
        "available_sample": names[:8],
        "note": "Colibri chat blocked/OOM - using local Ollama heavy model (daily chat path unchanged)",
    }


def _pick_heavy_fallback(reason: str = "") -> dict[str, Any]:
    """Cascade: GGUF -> Ollama local heavy -> gated cloud."""
    trail: list[dict[str, Any]] = [{"reason": reason}] if reason else []
    gguf = _try_gguf_ready()
    trail.append({"gguf": {k: gguf.get(k) for k in ("ok", "skipped", "reason", "model", "switched_to")}})
    if gguf.get("ok"):
        gguf["fallback_trail"] = trail
        return gguf
    ollama = _try_ollama_heavy()
    trail.append(
        {
            "ollama": {
                k: ollama.get(k)
                for k in ("ok", "skipped", "reason", "model", "switched_to", "error", "candidates", "free_gb")
            }
        }
    )
    if ollama.get("ok"):
        ollama["fallback_trail"] = trail
        return ollama
    cloud = _try_cloud_ready()
    trail.append({"cloud": {k: cloud.get(k) for k in ("ok", "skipped", "reason", "model", "switched_to", "error")}})
    cloud["fallback_trail"] = trail
    return cloud


def _chat_with_ollama_cascade(
    *,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    first: dict[str, Any],
) -> dict[str, Any]:
    """Try chosen Ollama model; on OOM walk remaining candidates then cloud."""
    candidates = list(first.get("candidates") or [first.get("model")])
    errors: list[dict[str, Any]] = []
    last: dict[str, Any] = {"ok": False, "error": "no ollama candidates"}
    for model in candidates:
        if not model:
            continue
        os.environ["MYTHOS_HEAVY_API"] = DEFAULT_OLLAMA_API
        os.environ["MYTHOS_HEAVY_MODEL"] = str(model)
        last = _chat_completions(
            base=DEFAULT_OLLAMA_API,
            messages=messages,
            model=str(model),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=300.0,
        )
        last["api"] = DEFAULT_OLLAMA_API
        last["model_used"] = str(model)
        if last.get("ok"):
            last["switched_to"] = "ollama_heavy"
            last["fallback_errors"] = errors or None
            return last
        err = str(last.get("error") or "")
        errors.append({"model": model, "error": err[:240]})
        # Only continue on OOM / alloc failures; stop on other hard errors after first
        low = err.lower()
        if not any(x in low for x in ("out-of-memory", "oom", "failed to allocate", "unable to allocate")):
            break
    cloud = _try_cloud_ready()
    if cloud.get("ok"):
        last = _chat_completions(
            base=_api_base(),
            messages=messages,
            model=_model_name(),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        last["api"] = _api_base()
        last["model_used"] = _model_name()
        last["switched_to"] = "cloud"
        last["fallback_errors"] = errors
        return last
    last["fallback_errors"] = errors
    last["cloud"] = {k: cloud.get(k) for k in ("ok", "skipped", "reason", "error")}
    return last


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
            "tools": ["brain.status", "brain.ram", "brain.ensure", "brain.think", "brain.heavy", "brain.escalate"],
            "note": (
                "Agents call brain.heavy when stuck. Escalation: "
                "Colibri (if >=8GB free) -> GGUF :8088 -> Ollama heavy (qwen3-coder/nemotron) -> gated cloud."
            ),
            "cloud_allowed": _cloud_allowed(),
            "cloud_api": _cloud_api_base() or None,
            "colibri_chat_viable": _colibri_chat_viable(),
        }

    def ram(self) -> dict[str, Any]:
        """Report physical RAM vs Colibri needs — no chat fluff."""
        total_gb = None
        free_gb = None
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

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_gb = round(stat.ullTotalPhys / (1024**3), 2)
                free_gb = round(stat.ullAvailPhys / (1024**3), 2)
        except Exception as exc:
            return {"ok": False, "error": f"RAM probe failed: {exc}"}

        need_peak = 18.0
        can_ever = bool(total_gb is not None and total_gb >= need_peak)
        enough_now = bool(free_gb is not None and free_gb >= need_peak)
        return {
            "ok": True,
            "total_ram_gb": total_gb,
            "free_ram_gb": free_gb,
            "colibri_peak_need_gb": need_peak,
            "enough_free_now": enough_now,
            "machine_can_ever_hold_peak": can_ever,
            "verdict": (
                "Colibri can try now — free RAM meets peak estimate."
                if enough_now
                else (
                    f"IMPOSSIBLE on this PC: total RAM is {total_gb} GB but Colibri wants ~{need_peak} GB peak. "
                    "Closing apps cannot create free RAM above total installed memory."
                    if not can_ever
                    else f"Not enough free RAM yet ({free_gb} GB free). Close heavy apps, then retry brain.ram / brain.ensure."
                )
            ),
            "ports": {
                "colibri_default": _api_base(),
                "gguf_8088": DEFAULT_GGUF_API,
            },
            "at": _now(),
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

        cloud = _try_cloud_ready()
        if cloud.get("ok"):
            return cloud
        return {
            "ok": False,
            "started": True,
            "error": f"started serve bat but API not up within {wait_sec}s (model load can take longer)",
            "api": base,
            "probe": last,
            "hint": "Leave the Colibri window open; retry brain.ensure / brain.heavy — or set MYTHOS_ALLOW_CLOUD_HEAVY=1",
            "cloud": cloud,
        }

    def think(
        self,
        prompt: str = "",
        system: str = "",
        max_tokens: int = 1200,
        ensure_up: bool = True,
        temperature: float = 0.3,
    ) -> dict[str, Any]:
        """Ask the heavy brain one shot. Auto-ensure + cascade on Colibri OOM/low-RAM."""
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt required"}

        fallback_meta: dict[str, Any] | None = None
        if ensure_up:
            ready = self.ensure(wait_sec=60, start_if_down=True)
            if not ready.get("ok"):
                fb = _pick_heavy_fallback(reason=str(ready.get("error") or "heavy ensure failed"))
                if fb.get("ok"):
                    ready = fb
                    fallback_meta = fb
                else:
                    return {
                        "ok": False,
                        "error": ready.get("error") or "heavy brain not available",
                        "ensure": ready,
                        "fallback": fb,
                        "daily_chat": "Ollama still available for normal chat",
                    }

        base = _api_base()
        # Skip doomed Colibri chat when free RAM is too low (models-up ≠ chat-ready)
        if _is_colibri_base(base):
            viable = _colibri_chat_viable()
            if not viable.get("ok"):
                fb = _pick_heavy_fallback(reason=str(viable.get("reason") or "colibri ram gate"))
                if fb.get("ok"):
                    base = _api_base()
                    fallback_meta = fb
                else:
                    return {
                        "ok": False,
                        "error": viable.get("reason") or "Colibri chat not viable",
                        "colibri_chat_viable": viable,
                        "fallback": fb,
                        "hint": "Free >=8GB RAM for Colibri, start GGUF :8088, or enable cloud heavy",
                        "at": _now(),
                    }

        sys_msg = (system or "").strip() or (
            "You are Mythos heavy brain. Be concrete, correct, and actionable. "
            "You assist Apex/Codex on hard jobs - plans, debugging, deep reasoning."
        )
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": prompt[:24000]},
        ]
        t0 = time.time()
        max_tok = max(64, min(int(max_tokens or 1200), 8192))
        temp = float(temperature or 0.3)

        # Already routed off Colibri (RAM gate / ensure fail) -> talk to that backend
        if fallback_meta and fallback_meta.get("switched_to") == "ollama_heavy":
            result = _chat_with_ollama_cascade(
                messages=messages,
                temperature=temp,
                max_tokens=max_tok,
                first=fallback_meta,
            )
        elif fallback_meta and fallback_meta.get("switched_to") in {"gguf_8088", "cloud"}:
            result = _chat_completions(
                base=_api_base(),
                messages=messages,
                model=_model_name(),
                temperature=temp,
                max_tokens=max_tok,
            )
            result["api"] = _api_base()
            result["model_used"] = _model_name()
            result["switched_to"] = fallback_meta.get("switched_to")
        else:
            result = _chat_completions(
                base=base,
                messages=messages,
                model=_model_name(),
                temperature=temp,
                max_tokens=max_tok,
                timeout=180.0 if _is_colibri_base(base) else 600.0,
            )
            # Colibri often 500/OOM while /models is fine - cascade once
            if not result.get("ok") and _is_colibri_base(base):
                fb = _pick_heavy_fallback(
                    reason=f"Colibri chat failed: {result.get('error') or 'empty response'}"
                )
                if fb.get("ok"):
                    fallback_meta = fb
                    if fb.get("switched_to") == "ollama_heavy":
                        result = _chat_with_ollama_cascade(
                            messages=messages,
                            temperature=temp,
                            max_tokens=max_tok,
                            first=fb,
                        )
                    else:
                        result = _chat_completions(
                            base=_api_base(),
                            messages=messages,
                            model=_model_name(),
                            temperature=temp,
                            max_tokens=max_tok,
                        )
                        result["api"] = _api_base()
                        result["model_used"] = _model_name()
                        result["switched_to"] = fb.get("switched_to")
                    result["colibri_failed"] = True
            # Already on Ollama (prior escalate / env) - walk smaller models on OOM
            elif not result.get("ok") and "11434" in (base or ""):
                fb = _try_ollama_heavy()
                if fb.get("ok"):
                    fallback_meta = fb
                    result = _chat_with_ollama_cascade(
                        messages=messages,
                        temperature=temp,
                        max_tokens=max_tok,
                        first=fb,
                    )

        result["elapsed_sec"] = round(time.time() - t0, 2)
        result["api"] = result.get("api") or _api_base()
        result["model_used"] = result.get("model_used") or _model_name()
        result["limb"] = "heavy_brain"
        result["at"] = _now()
        if fallback_meta:
            result["fallback"] = {
                k: fallback_meta.get(k)
                for k in ("switched_to", "model", "note", "fallback_trail", "candidates")
                if k in fallback_meta
            }
        if result.get("ok"):
            via = (
                result.get("switched_to")
                or (fallback_meta or {}).get("switched_to")
                or ("colibri" if _is_colibri_base(str(result.get("api") or "")) else "heavy")
            )
            result["message"] = (
                f"Heavy brain answered via {via} - use this for the hard step; daily chat stays on Ollama."
            )
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
