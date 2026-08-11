#!/usr/bin/env python3
"""
TTS limb — voice stack for Mythos.

Primary: edge-tts (always seated when package present).
Optional: Dia (Nari Labs) / Coqui / Chatterbox when installed under Mythos_Tools or avatar/deps.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT

OUT = Path(APEX_ROOT) / "media" / "tts"
TOOLS = Path(os.environ.get("MYTHOS_TOOLS") or r"D:\Mythos_Tools")
DIA_DIRS = (
    TOOLS / "Dia",
    TOOLS / "dia-tts",
    TOOLS / "dia",
    Path(APEX_ROOT) / "avatar" / "deps" / "dia",
)


class TtsLimb:
    def status(self) -> dict[str, Any]:
        edge = False
        try:
            import edge_tts  # noqa: F401

            edge = True
        except ImportError:
            pass
        dia_path = next((str(p) for p in DIA_DIRS if p.is_dir()), None)
        return {
            "ok": True,
            "limb": "tts",
            "edge_tts": edge,
            "dia_seated": bool(dia_path),
            "dia_path": dia_path,
            "out": str(OUT),
            "tools": ["tts.status", "tts.seat", "tts.speak", "tts.voices"],
            "primary": "edge-tts",
            "note": (
                "DIA TTS (Nari Labs) optional — seat via tts.seat engine=dia when GPU ready. "
                "Avatar/studio already use edge-tts."
            ),
        }

    def seat(self, engine: str = "edge") -> dict[str, Any]:
        engine = (engine or "edge").strip().lower()
        OUT.mkdir(parents=True, exist_ok=True)
        if engine in ("edge", "edge-tts", "all"):
            try:
                import edge_tts  # noqa: F401

                edge = {"ok": True, "already": True}
            except ImportError:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "edge-tts", "-q"],
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                edge = {"ok": r.returncode == 0, "stderr": (r.stderr or "")[-300:]}
        else:
            edge = {"skipped": True}

        dia_info: dict[str, Any] = {"skipped": True}
        if engine in ("dia", "all"):
            target = TOOLS / "Dia"
            target.mkdir(parents=True, exist_ok=True)
            readme = target / "SEAT_ME.txt"
            readme.write_text(
                "Dia TTS (Nari Labs) — optional open-weights dialogue TTS.\n"
                "https://github.com/nari-labs/dia\n"
                "Clone into this folder and install GPU deps, then tts.speak engine=dia.\n"
                "Until then Mythos uses edge-tts (free, reliable).\n",
                encoding="utf-8",
            )
            # Try shallow clone if git present and empty
            repo = target / "repo"
            if repo.is_dir() and any(repo.iterdir()):
                dia_info = {
                    "ok": True,
                    "path": str(target),
                    "repo": str(repo),
                    "note": "Dia repo already present — still needs torch/CUDA deps for inference",
                }
            elif shutil.which("git"):
                r = subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "https://github.com/nari-labs/dia.git",
                        str(repo),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                dia_info = {
                    "ok": r.returncode == 0,
                    "path": str(target),
                    "stderr": (r.stderr or "")[-400:],
                    "note": "Repo cloned if network OK — still needs torch/CUDA deps",
                }
            else:
                dia_info = {"ok": True, "path": str(target), "note": "Template seated; install Dia weights when ready"}

        return {"ok": True, "edge": edge, "dia": dia_info, "out": str(OUT)}

    def voices(self) -> dict[str, Any]:
        return {
            "ok": True,
            "edge": [
                "en-US-AriaNeural",
                "en-US-AndrewNeural",
                "en-US-BrianNeural",
                "en-GB-RyanNeural",
                "en-GB-SoniaNeural",
            ],
            "apex_default": "en-US-AriaNeural",
            "codex_default": "en-GB-RyanNeural",
        }

    def speak(
        self,
        text: str = "",
        voice: str = "en-US-AriaNeural",
        engine: str = "edge",
        rate: str = "+0%",
    ) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "text required"}
        engine = (engine or "edge").strip().lower()
        if engine == "dia":
            return {
                "ok": False,
                "error": "Dia engine not fully seated — use engine=edge (working) or tts.seat engine=dia first",
                "fallback": "edge-tts",
            }
        OUT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = OUT / f"speak_{stamp}.mp3"
        try:
            import edge_tts

            async def _run():
                comm = edge_tts.Communicate(text, voice, rate=rate)
                await comm.save(str(out))

            asyncio.run(_run())
            return {"ok": out.is_file(), "path": str(out), "voice": voice, "engine": "edge-tts"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def register_tts_tools(protocol, quiet: bool = True) -> None:
    limb = TtsLimb()
    protocol.register(
        "tts.status",
        limb,
        "status",
        {"description": "TTS stack status (edge-tts primary, Dia optional)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "tts.seat",
        limb,
        "seat",
        {
            "description": "Install/seat edge-tts and optional Dia template",
            "parameters": {
                "type": "object",
                "properties": {"engine": {"type": "string", "default": "edge"}},
            },
        },
    )
    protocol.register(
        "tts.voices",
        limb,
        "voices",
        {"description": "List recommended TTS voices", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "tts.speak",
        limb,
        "speak",
        {
            "description": "Speak text to mp3 via edge-tts",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "default": "en-US-AriaNeural"},
                    "engine": {"type": "string", "default": "edge"},
                    "rate": {"type": "string", "default": "+0%"},
                },
                "required": ["text"],
            },
        },
    )
    if not quiet:
        print("[registry] TTS limb seated", flush=True)
