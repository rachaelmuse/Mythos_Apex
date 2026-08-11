#!/usr/bin/env python3
"""
Design limb — Motiff-class UI mock generation via local ComfyUI / graphics.

Not Motiff cloud. Generates UI mockup images from prompts for the creator's apps.
"""
from __future__ import annotations

from typing import Any


class DesignLimb:
    def status(self) -> dict[str, Any]:
        gfx_ok = False
        gfx_note = ""
        try:
            from advanced_shards.graphics_limb import GraphicsLimb

            st = GraphicsLimb().status()
            gfx_ok = bool(st.get("ok") or st.get("ready") or st.get("running"))
            gfx_note = str(st)[:400]
        except Exception as exc:
            gfx_note = str(exc)
        return {
            "ok": True,
            "limb": "design",
            "equivalent_to": "Motiff AI (local substitute)",
            "graphics_ready": gfx_ok,
            "graphics": gfx_note,
            "tools": ["design.status", "design.mock", "design.ui"],
            "note": "Uses graphics.generate (ComfyUI). For Figma-connected design, use composio.connect toolkit=figma",
        }

    def mock(
        self,
        prompt: str = "",
        style: str = "clean product UI mockup, desktop app, high detail",
        wait: bool = False,
    ) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "error": "prompt required — describe the screen/UI"}
        full = f"{style}. {prompt}. No watermark, sharp UI, readable labels."
        try:
            from advanced_shards.graphics_limb import GraphicsLimb

            gfx = GraphicsLimb()
            if wait and hasattr(gfx, "generate_wait"):
                return gfx.generate_wait(prompt=full)
            return gfx.generate(prompt=full)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def ui(self, screen: str = "", product: str = "Mythos") -> dict[str, Any]:
        screen = (screen or "settings").strip()
        product = (product or "Mythos").strip()
        return self.mock(
            prompt=f"{product} {screen} screen, dark cyberpunk HUD, neon accents, professional UI",
            wait=False,
        )


def register_design_tools(protocol, quiet: bool = True) -> None:
    limb = DesignLimb()
    protocol.register(
        "design.status",
        limb,
        "status",
        {"description": "Local Motiff-class design limb status (ComfyUI)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "design.mock",
        limb,
        "mock",
        {
            "description": "Generate a UI mockup image from a prompt via ComfyUI",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "style": {"type": "string"},
                    "wait": {"type": "boolean", "default": False},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "design.ui",
        limb,
        "ui",
        {
            "description": "Quick UI screen mock for a product (e.g. screen=dashboard)",
            "parameters": {
                "type": "object",
                "properties": {
                    "screen": {"type": "string"},
                    "product": {"type": "string", "default": "Mythos"},
                },
            },
        },
    )
    if not quiet:
        print("[registry] Design limb seated", flush=True)
