#!/usr/bin/env python3
"""
Composio.dev limb — 1000+ app connectors for Mythos (Gmail, Slack, Notion, GitHub, …).

Auth: COMPOSIO_API_KEY in env or config/composio.env
Cursor also has plugin-composio-composio MCP (already authenticated in this workspace).

Docs: https://docs.composio.dev
Dashboard: https://dashboard.composio.dev
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mythos_runtime import APEX_ROOT

CONFIG_ENV = Path(APEX_ROOT) / "config" / "composio.env"
STATE_DIR = Path(APEX_ROOT) / "mythos_state" / "composio"


def _load_api_key() -> str:
    key = (os.environ.get("COMPOSIO_API_KEY") or "").strip()
    if key:
        return key
    if CONFIG_ENV.is_file():
        for line in CONFIG_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "COMPOSIO_API_KEY":
                return v.strip().strip('"').strip("'")
    return ""


class ComposioLimb:
    """Bridge Mythos tools → Composio.dev SDK."""

    def status(self) -> dict[str, Any]:
        key = _load_api_key()
        sdk = False
        try:
            import composio  # noqa: F401

            sdk = True
        except ImportError:
            pass
        return {
            "ok": True,
            "limb": "composio",
            "sdk_installed": sdk,
            "api_key_set": bool(key),
            "config_path": str(CONFIG_ENV),
            "dashboard": "https://dashboard.composio.dev",
            "docs": "https://docs.composio.dev",
            "cursor_mcp": "plugin-composio-composio (Cursor MCP — auth separately)",
            "tools": [
                "composio.status",
                "composio.seat",
                "composio.toolkits",
                "composio.search",
                "composio.execute",
                "composio.connect",
            ],
            "install": "pip install composio  # then set COMPOSIO_API_KEY",
            "note": (
                "Runner-H-class app automation via Composio connectors. "
                "Local browser.* stays Playwright; Composio covers Gmail/Slack/Notion/GitHub/etc."
            ),
        }

    def seat(self) -> dict[str, Any]:
        """Ensure package + config template exist."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_ENV.parent.mkdir(parents=True, exist_ok=True)
        if not CONFIG_ENV.is_file():
            CONFIG_ENV.write_text(
                "# Composio.dev — get key at https://dashboard.composio.dev/settings\n"
                "COMPOSIO_API_KEY=\n",
                encoding="utf-8",
            )
        install: dict[str, Any] = {"ok": False}
        try:
            import composio  # noqa: F401

            install = {"ok": True, "already": True}
        except ImportError:
            import subprocess
            import sys

            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "composio", "-q"],
                capture_output=True,
                text=True,
                timeout=300,
            )
            install = {
                "ok": r.returncode == 0,
                "returncode": r.returncode,
                "stderr": (r.stderr or "")[-400:],
            }
        return {
            "ok": bool(install.get("ok")),
            "install": install,
            "config": str(CONFIG_ENV),
            "api_key_set": bool(_load_api_key()),
            "next": (
                "Paste COMPOSIO_API_KEY into config/composio.env (or env), "
                "then composio.toolkits / composio.search"
            ),
        }

    def _client(self):
        key = _load_api_key()
        if not key:
            raise RuntimeError(
                "COMPOSIO_API_KEY missing — set env or config/composio.env "
                "(https://dashboard.composio.dev/settings)"
            )
        from composio import Composio

        return Composio(api_key=key)

    def toolkits(self, limit: int = 40) -> dict[str, Any]:
        """List available Composio toolkits (apps)."""
        try:
            client = self._client()
            # SDK shapes vary by version — try common APIs
            items: list[Any] = []
            if hasattr(client, "toolkits") and hasattr(client.toolkits, "get"):
                raw = client.toolkits.get()
                items = list(raw) if raw is not None else []
            elif hasattr(client, "tools") and hasattr(client.tools, "get"):
                raw = client.tools.get()
                items = list(raw)[:limit] if raw is not None else []
            else:
                return {
                    "ok": True,
                    "note": "SDK seated — use composio.search(use_case=...) or Cursor COMPOSIO_SEARCH_TOOLS",
                    "dashboard": "https://dashboard.composio.dev",
                }
            preview = []
            for it in items[:limit]:
                if isinstance(it, dict):
                    preview.append(it)
                else:
                    preview.append(str(it)[:200])
            return {"ok": True, "count": len(items), "preview": preview}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def search(self, use_case: str = "", query: str = "") -> dict[str, Any]:
        """Find tools for a use case (e.g. 'send gmail', 'create notion page')."""
        q = (use_case or query or "").strip()
        if not q:
            return {"ok": False, "error": "use_case required"}
        try:
            client = self._client()
            user_id = os.environ.get("COMPOSIO_USER_ID") or "mythos_creator"
            session = client.create(user_id=user_id)
            tools = session.tools() if callable(getattr(session, "tools", None)) else getattr(session, "tools", [])
            if callable(tools):
                tools = tools()
            # Filter loosely by query tokens
            tokens = [t for t in q.lower().split() if len(t) > 2]
            matched = []
            for t in tools or []:
                blob = json.dumps(t, default=str).lower() if not isinstance(t, str) else t.lower()
                if not tokens or any(tok in blob for tok in tokens):
                    matched.append(t if isinstance(t, (dict, str)) else str(t)[:300])
                if len(matched) >= 24:
                    break
            return {
                "ok": True,
                "use_case": q,
                "user_id": user_id,
                "matched": matched[:24],
                "total_tools_in_session": len(list(tools or [])),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "hint": "Run composio.seat and set API key"}

    def connect(self, toolkit: str = "") -> dict[str, Any]:
        """Start connecting an app toolkit (returns auth URL when possible)."""
        toolkit = (toolkit or "").strip().lower()
        if not toolkit:
            return {"ok": False, "error": "toolkit required (e.g. gmail, slack, notion, github)"}
        try:
            client = self._client()
            user_id = os.environ.get("COMPOSIO_USER_ID") or "mythos_creator"
            # Prefer connected accounts / auth link APIs when present
            if hasattr(client, "connected_accounts"):
                ca = client.connected_accounts
                if hasattr(ca, "initiate"):
                    link = ca.initiate(user_id=user_id, auth_config_id=toolkit)
                    return {"ok": True, "toolkit": toolkit, "result": str(link)[:2000]}
            return {
                "ok": True,
                "toolkit": toolkit,
                "dashboard": f"https://dashboard.composio.dev",
                "note": (
                    f"Open dashboard → connect '{toolkit}'. "
                    "Or use Cursor MCP: COMPOSIO_MANAGE_CONNECTIONS."
                ),
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def execute(
        self,
        tool: str = "",
        args_json: str = "{}",
        user_id: str = "",
    ) -> dict[str, Any]:
        """Execute a Composio tool by slug with JSON args."""
        tool = (tool or "").strip()
        if not tool:
            return {"ok": False, "error": "tool slug required (e.g. GMAIL_SEND_EMAIL)"}
        try:
            args = json.loads(args_json or "{}")
            if not isinstance(args, dict):
                return {"ok": False, "error": "args_json must be a JSON object"}
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"bad args_json: {exc}"}
        try:
            client = self._client()
            uid = (user_id or os.environ.get("COMPOSIO_USER_ID") or "mythos_creator").strip()
            # Common execute paths across SDK versions
            if hasattr(client, "tools") and hasattr(client.tools, "execute"):
                result = client.tools.execute(slug=tool, arguments=args, user_id=uid)
                return {"ok": True, "tool": tool, "result": result}
            if hasattr(client, "execute"):
                result = client.execute(tool, args, user_id=uid)
                return {"ok": True, "tool": tool, "result": result}
            return {
                "ok": False,
                "error": "SDK execute API not found — upgrade composio or use Cursor MCP COMPOSIO_MULTI_EXECUTE_TOOL",
                "tool": tool,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def register_composio_tools(protocol, quiet: bool = True) -> None:
    limb = ComposioLimb()

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
        ("composio.status", "status", sch("Composio.dev status — SDK, API key, Cursor MCP note")),
        ("composio.seat", "seat", sch("Install composio package + write config/composio.env template")),
        ("composio.toolkits", "toolkits", sch("List Composio app toolkits", {"limit": {"type": "integer", "default": 40}})),
        (
            "composio.search",
            "search",
            sch(
                "Search Composio tools for a use case (gmail, slack, notion, github…)",
                {"use_case": {"type": "string"}, "query": {"type": "string"}},
                ["use_case"],
            ),
        ),
        (
            "composio.connect",
            "connect",
            sch("Connect an app toolkit (opens dashboard / auth link)", {"toolkit": {"type": "string"}}, ["toolkit"]),
        ),
        (
            "composio.execute",
            "execute",
            sch(
                "Execute a Composio tool by slug with JSON args",
                {
                    "tool": {"type": "string"},
                    "args_json": {"type": "string", "default": "{}"},
                    "user_id": {"type": "string"},
                },
                ["tool"],
            ),
        ),
    ]
    for name, method, schema in specs:
        protocol.register(name, limb, method, schema)
    if not quiet:
        print("[registry] Composio limb seated", flush=True)
