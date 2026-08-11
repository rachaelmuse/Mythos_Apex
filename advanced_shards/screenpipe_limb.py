#!/usr/bin/env python3
"""
Screen memory limb — Screenpipe-class local capture for Mythos.

If external Screenpipe CLI is installed, wraps it.
Otherwise runs a local continuous screenshot archive under mythos_state/screen_memory/
that agents can query. Privacy: local disk only — never uploads.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT

MEM_DIR = Path(APEX_ROOT) / "mythos_state" / "screen_memory"
META_PATH = MEM_DIR / "index.jsonl"
PID_PATH = MEM_DIR / "daemon.pid"
TOOLS_HOME = Path(os.environ.get("MYTHOS_TOOLS") or r"D:\Mythos_Tools")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_screenpipe() -> str | None:
    which = shutil.which("screenpipe")
    if which:
        return which
    for p in (
        TOOLS_HOME / "screenpipe" / "screenpipe.exe",
        Path.home() / ".cargo" / "bin" / "screenpipe.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "screenpipe" / "screenpipe.exe",
    ):
        if p.is_file():
            return str(p)
    return None


class ScreenpipeLimb:
    _thread: threading.Thread | None = None
    _stop: threading.Event | None = None

    def status(self) -> dict[str, Any]:
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        external = _find_screenpipe()
        frames = list(MEM_DIR.glob("frame_*.png"))
        running = False
        if PID_PATH.is_file():
            try:
                pid = int(PID_PATH.read_text(encoding="utf-8").strip())
                # Windows: tasklist check is heavy — use thread flag
                running = bool(self._thread and self._thread.is_alive())
            except Exception:
                running = bool(self._thread and self._thread.is_alive())
        else:
            running = bool(self._thread and self._thread.is_alive())
        return {
            "ok": True,
            "limb": "screenpipe",
            "mode": "external" if external else "local_archive",
            "external_cli": external,
            "local_dir": str(MEM_DIR),
            "frame_count": len(frames),
            "daemon_running": running,
            "tools": [
                "screenpipe.status",
                "screenpipe.seat",
                "screenpipe.capture",
                "screenpipe.start",
                "screenpipe.stop",
                "screenpipe.query",
            ],
            "note": (
                "Screenpipe-class screen memory. Local frames only. "
                "free_cluely (Mythos_Tools) is companion activity UI on :5180."
            ),
        }

    def seat(self) -> dict[str, Any]:
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        readme = MEM_DIR / "README.txt"
        if not readme.is_file():
            readme.write_text(
                "Mythos screen memory (local).\n"
                "Frames: frame_YYYYMMDD_HHMMSS.png\n"
                "Index: index.jsonl\n"
                "Optional: install https://github.com/mediar-ai/screenpipe for full OCR+audio.\n",
                encoding="utf-8",
            )
        # Ensure laptop control can screenshot — soft enable note
        return {
            "ok": True,
            "dir": str(MEM_DIR),
            "external": _find_screenpipe(),
            "next": "screenpipe.start interval_sec=30  OR  screenpipe.capture",
        }

    def capture(self, path: str = "") -> dict[str, Any]:
        """One screenshot into screen_memory (uses laptop screenshot path)."""
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = path.strip() if path else str(MEM_DIR / f"frame_{stamp}.png")
        try:
            from advanced_shards.laptop_control_limb import LaptopControlLimb

            limb = LaptopControlLimb()
            # Soft-enable if needed for capture
            st = limb.status()
            if not st.get("enabled"):
                limb.enable(grant="screen_memory")
            r = limb.screenshot(path=out)
            if r.get("ok"):
                with META_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": _now(), "path": out, "kind": "frame"}) + "\n")
            return {"ok": bool(r.get("ok")), "path": out, "detail": r}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _loop(self, interval_sec: int, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.capture()
            except Exception:
                pass
            stop.wait(max(5, int(interval_sec)))

    def start(self, interval_sec: int = 30) -> dict[str, Any]:
        """Start local continuous capture daemon (or note external screenpipe)."""
        external = _find_screenpipe()
        if external:
            try:
                proc = subprocess.Popen(
                    [external],
                    cwd=str(Path(external).parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                PID_PATH.write_text(str(proc.pid), encoding="utf-8")
                return {"ok": True, "mode": "external", "pid": proc.pid, "cli": external}
            except Exception as exc:
                return {"ok": False, "error": str(exc), "cli": external}

        if self._thread and self._thread.is_alive():
            return {"ok": True, "already": True, "mode": "local_archive"}
        self.seat()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            args=(interval_sec, self._stop),
            daemon=True,
            name="mythos-screenpipe",
        )
        self._thread.start()
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
        return {
            "ok": True,
            "mode": "local_archive",
            "interval_sec": interval_sec,
            "dir": str(MEM_DIR),
        }

    def stop(self) -> dict[str, Any]:
        if self._stop:
            self._stop.set()
        self._thread = None
        if PID_PATH.is_file():
            try:
                PID_PATH.unlink()
            except OSError:
                pass
        return {"ok": True, "stopped": True}

    def query(self, limit: int = 12, contains: str = "") -> dict[str, Any]:
        """Return recent frame paths (and index lines). OCR optional later."""
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        frames = sorted(MEM_DIR.glob("frame_*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        frames = frames[: max(1, min(100, int(limit)))]
        rows = [{"path": str(p), "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat()} for p in frames]
        index_hits = []
        if META_PATH.is_file() and contains:
            needle = contains.lower()
            for line in META_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]:
                if needle in line.lower():
                    index_hits.append(line[:400])
        return {
            "ok": True,
            "frames": rows,
            "index_hits": index_hits[:20],
            "dir": str(MEM_DIR),
        }


def register_screenpipe_tools(protocol, quiet: bool = True) -> None:
    limb = ScreenpipeLimb()

    def sch(desc, props=None, required=None):
        return {
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props or {},
                "required": required or [],
            },
        }

    specs = [
        ("screenpipe.status", "status", sch("Screen memory / Screenpipe status")),
        ("screenpipe.seat", "seat", sch("Create local screen_memory archive")),
        ("screenpipe.capture", "capture", sch("Capture one desktop frame", {"path": {"type": "string"}})),
        (
            "screenpipe.start",
            "start",
            sch(
                "Start continuous screen capture (local or external screenpipe)",
                {"interval_sec": {"type": "integer", "default": 30}},
            ),
        ),
        ("screenpipe.stop", "stop", sch("Stop continuous capture")),
        (
            "screenpipe.query",
            "query",
            sch(
                "List recent screen frames for agent memory",
                {"limit": {"type": "integer", "default": 12}, "contains": {"type": "string"}},
            ),
        ),
    ]
    for name, method, schema in specs:
        protocol.register(name, limb, method, schema)
    if not quiet:
        print("[registry] Screenpipe limb seated", flush=True)
