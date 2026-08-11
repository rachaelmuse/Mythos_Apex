#!/usr/bin/env python3
"""
Seat / hardwire Mythos tools that were registered but missing binaries.

Installs: Playwright Chromium, edge-tts, composio package.
Seats: screen memory archive, TTS out dir, composio.env template, Dia template.
Reports: which companion tools are connected vs missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

APEX_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(APEX_ROOT))

REPORT = APEX_ROOT / "mythos_state" / "SEAT_REPORT.json"


def _run(cmd: list[str], timeout: int = 600) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(APEX_ROOT))
        return {
            "ok": r.returncode == 0,
            "code": r.returncode,
            "stdout": (r.stdout or "")[-800:],
            "stderr": (r.stderr or "")[-800:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main() -> int:
    py = sys.executable
    steps: dict = {"started": datetime.now(timezone.utc).isoformat(), "python": py}

    # 1) Playwright chromium
    steps["playwright_pip"] = _run([py, "-m", "pip", "install", "playwright", "-q"], timeout=300)
    steps["playwright_chromium"] = _run([py, "-m", "playwright", "install", "chromium"], timeout=900)

    # 2) edge-tts + composio
    steps["edge_tts"] = _run([py, "-m", "pip", "install", "edge-tts", "-q"], timeout=180)
    steps["composio_pip"] = _run([py, "-m", "pip", "install", "composio", "-q"], timeout=300)

    # 3) Limb seat methods
    seat_results = {}
    try:
        from advanced_shards.composio_limb import ComposioLimb

        seat_results["composio"] = ComposioLimb().seat()
    except Exception as exc:
        seat_results["composio"] = {"ok": False, "error": str(exc)}
    try:
        from advanced_shards.screenpipe_limb import ScreenpipeLimb

        seat_results["screenpipe"] = ScreenpipeLimb().seat()
    except Exception as exc:
        seat_results["screenpipe"] = {"ok": False, "error": str(exc)}
    try:
        from advanced_shards.tts_limb import TtsLimb

        seat_results["tts"] = TtsLimb().seat(engine="all")
    except Exception as exc:
        seat_results["tts"] = {"ok": False, "error": str(exc)}
    try:
        from advanced_shards.design_limb import DesignLimb

        seat_results["design"] = DesignLimb().status()
    except Exception as exc:
        seat_results["design"] = {"ok": False, "error": str(exc)}

    # 4) Registry smoke — count tools + new prefixes
    try:
        from apex_upgrade.tool_protocol import ToolProtocol
        from mythos_unified_registry import register_all_tools

        proto = ToolProtocol()
        added = register_all_tools(proto, quiet=True)
        names = sorted(proto.tools.keys())
        prefixes = {}
        for n in names:
            p = n.split(".", 1)[0]
            prefixes[p] = prefixes.get(p, 0) + 1
        needed = ["composio", "screenpipe", "design", "tts", "browser", "research", "avatar", "studio"]
        seat_results["registry"] = {
            "ok": True,
            "added": added,
            "tool_count": len(names),
            "needed": {k: prefixes.get(k, 0) for k in needed},
            "sample": [n for n in names if n.split(".")[0] in needed][:24],
        }
    except Exception as exc:
        seat_results["registry"] = {"ok": False, "error": str(exc)}

    # 5) Chromium launch smoke
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        steps["chromium_launch"] = {"ok": True}
    except Exception as exc:
        steps["chromium_launch"] = {"ok": False, "error": str(exc)[:300]}

    report = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "limbs": seat_results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str)[:4000])
    print(f"\nWrote {REPORT}")
    ok = bool(steps.get("chromium_launch", {}).get("ok")) and bool(
        seat_results.get("registry", {}).get("ok")
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
