#!/usr/bin/env python3
"""
Agent-Reach limb — richer internet eyes for Mythos.

Agent-Reach (Panniantong) is a capability layer: install + doctor + route.
Actual reads use upstream tools (Jina Reader, yt-dlp, gh, feedparser) — not a
`get` RPC. Credentials stay under ~/.agent-reach/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _venv_bin() -> Path | None:
    home = _home()
    for p in (
        home / ".agent-reach-venv" / "Scripts",
        home / ".agent-reach-venv" / "bin",
        Path(sys.executable).resolve().parent,
    ):
        if p.is_dir():
            return p
    return None


def _find_cli() -> Path | None:
    names = ("agent-reach.exe", "agent-reach")
    bin_dir = _venv_bin()
    if bin_dir:
        for name in names:
            p = bin_dir / name
            if p.is_file():
                return p
    which = shutil.which("agent-reach")
    return Path(which) if which else None


def _find_tool(name: str) -> str | None:
    """Prefer Agent-Reach venv Scripts, then PATH."""
    bin_dir = _venv_bin()
    if bin_dir:
        for cand in (f"{name}.exe", name):
            p = bin_dir / cand
            if p.is_file():
                return str(p)
    return shutil.which(name)


def _enriched_env() -> dict[str, str]:
    """Put Agent-Reach venv on PATH so doctor/yt-dlp resolve."""
    env = dict(os.environ)
    bin_dir = _venv_bin()
    if bin_dir:
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env


class AgentReachLimb:
    """Doctor via agent-reach CLI; reads via upstream tools."""

    def __init__(self) -> None:
        self._cli = _find_cli()

    def _cli_path(self) -> Path | None:
        if self._cli and self._cli.is_file():
            return self._cli
        self._cli = _find_cli()
        return self._cli

    def _run_cli(self, args: list[str], *, timeout: int = 180, json_out: bool = False) -> dict[str, Any]:
        cli = self._cli_path()
        if not cli:
            return {
                "ok": False,
                "error": (
                    "agent-reach CLI not found. Expected ~/.agent-reach-venv "
                    "(https://github.com/Panniantong/Agent-Reach)."
                ),
                "limb": "agent_reach",
            }
        cmd = [str(cli), *args]
        if json_out and "--json" not in cmd:
            cmd.append("--json")
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=_enriched_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timed out after {timeout}s", "cmd": cmd, "limb": "agent_reach"}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "cmd": cmd, "limb": "agent_reach"}

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        data: Any = None
        if json_out and stdout:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                data = None
        ok = proc.returncode == 0
        out: dict[str, Any] = {
            "ok": ok,
            "limb": "agent_reach",
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": stdout[:120_000],
            "stderr": stderr[:20_000],
        }
        if data is not None:
            out["data"] = data
        if stdout:
            out["answer_preview"] = stdout[:12_000]
        if not ok:
            out["error"] = stderr or stdout or f"exit {proc.returncode}"
        return out

    def status(self) -> dict[str, Any]:
        cli = self._cli_path()
        return {
            "ok": True,
            "limb": "agent_reach",
            "ready": bool(cli),
            "cli": str(cli) if cli else None,
            "config_dir": str(_home() / ".agent-reach"),
            "yt_dlp": _find_tool("yt-dlp"),
            "gh": _find_tool("gh"),
            "tools": [
                "research.reach",
                "research.reach_doctor",
                "research.reach_get",
                "research.reach_web",
                "research.reach_youtube",
            ],
            "note": (
                "Agent-Reach doctor + upstream reads (Jina/yt-dlp/gh/RSS). "
                "Social channels need creator cookies — ask before configuring."
            ),
        }

    def doctor(self, channels: str = "") -> dict[str, Any]:
        _ = channels  # full CLI uses --json only; channels ignored
        return self._run_cli(["doctor"], timeout=240, json_out=True)

    def web(self, url: str = "", topic: str = "", query: str = "") -> dict[str, Any]:
        url = (url or "").strip()
        topic = (topic or query or "").strip()
        if url:
            return self._jina_read(url)
        if not topic:
            return {
                "ok": False,
                "error": "Provide url or topic/query for research.reach_web.",
                "limb": "agent_reach",
            }
        # DuckDuckGo HTML lite as zero-key search; Exa needs mcporter after install
        ddg = self._ddg_search(topic)
        if ddg.get("ok"):
            return ddg
        return {
            "ok": False,
            "error": "Search failed. Try research.web or run agent-reach install --env=auto for Exa.",
            "limb": "agent_reach",
        }

    def youtube(self, url: str = "", query: str = "", limit: int = 5) -> dict[str, Any]:
        url = (url or "").strip()
        query = (query or "").strip()
        yt = _find_tool("yt-dlp")
        if not yt:
            # module fallback from agent-reach venv
            py = _home() / ".agent-reach-venv" / "Scripts" / "python.exe"
            if not py.is_file():
                py = Path(sys.executable)
            if url:
                return self._yt_module(py, url)
            return {
                "ok": False,
                "error": "yt-dlp not found. Run agent-reach install --env=auto.",
                "limb": "agent_reach",
            }
        if url:
            return self._yt_dlp_url(yt, url)
        if not query:
            return {
                "ok": False,
                "error": "Provide a YouTube url or search query.",
                "limb": "agent_reach",
            }
        return self._yt_dlp_search(yt, query, limit=max(1, min(int(limit or 5), 10)))

    def get(
        self,
        target: str = "",
        query: str = "",
        limit: int = 10,
        max_tokens: int = 4000,
    ) -> dict[str, Any]:
        """Route channel.command → upstream (Agent-Reach has no get RPC)."""
        _ = max_tokens
        target = (target or "").strip().lower()
        query = (query or "").strip()
        if not target:
            return {
                "ok": False,
                "error": "Provide target: web, youtube, github, rss, doctor.",
                "limb": "agent_reach",
            }
        base = target.split(".", 1)[0]
        if base in ("doctor", "status"):
            return self.doctor()
        if base in ("web", "jina", "url"):
            if query.startswith("http"):
                return self.web(url=query)
            return self.web(topic=query)
        if base in ("youtube", "yt"):
            if "youtube.com" in query or "youtu.be" in query or query.startswith("http"):
                return self.youtube(url=query, limit=limit)
            return self.youtube(query=query, limit=limit)
        if base in ("github", "gh"):
            return self._github(query)
        if base == "rss":
            return self._rss(query)
        return {
            "ok": False,
            "error": f"Unknown target '{target}'. Use web|youtube|github|rss|doctor.",
            "limb": "agent_reach",
            "hint": "For Twitter/Reddit/etc. configure Agent-Reach channels first.",
        }

    def reach(
        self,
        action: str = "get",
        target: str = "",
        query: str = "",
        url: str = "",
        topic: str = "",
        limit: int = 10,
    ) -> dict[str, Any]:
        act = (action or "get").strip().lower()
        if act == "doctor":
            return self.doctor()
        if act == "web":
            return self.web(url=url, topic=topic or query)
        if act in ("youtube", "yt"):
            return self.youtube(url=url, query=query or topic, limit=limit)
        return self.get(
            target=target or ("web" if (url or topic or query) else "doctor"),
            query=query or topic or url,
            limit=limit,
        )

    # --- upstream helpers -------------------------------------------------

    def _jina_read(self, url: str) -> dict[str, Any]:
        jina = "https://r.jina.ai/" + url.lstrip()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/plain,text/markdown,*/*",
            "X-Return-Format": "markdown",
        }
        # Prefer curl when available (Agent-Reach docs use curl; urllib often 403'd)
        curl = shutil.which("curl")
        if curl:
            try:
                proc = subprocess.run(
                    [
                        curl,
                        "-sS",
                        "-L",
                        "--max-time",
                        "60",
                        "-A",
                        headers["User-Agent"],
                        "-H",
                        "Accept: text/plain,text/markdown,*/*",
                        jina,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=70,
                    encoding="utf-8",
                    errors="replace",
                    env=_enriched_env(),
                )
                if proc.returncode == 0 and (proc.stdout or "").strip():
                    text = proc.stdout
                    if "Just a moment..." in text or "cf-browser-verification" in text.lower():
                        text = ""
                    else:
                        return {
                            "ok": True,
                            "limb": "agent_reach",
                            "via": "jina_reader_curl",
                            "url": url,
                            "answer_preview": text[:12_000],
                            "stdout": text[:120_000],
                        }
            except (subprocess.TimeoutExpired, OSError):
                pass
        try:
            req = urllib.request.Request(jina, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "limb": "agent_reach",
                "via": "jina_reader",
                "url": url,
                "answer_preview": text[:12_000],
                "stdout": text[:120_000],
            }
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": f"Jina Reader failed: {exc}", "limb": "agent_reach", "url": url}

    def _ddg_search(self, topic: str) -> dict[str, Any]:
        q = urllib.parse.quote_plus(topic)
        api = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
        try:
            req = urllib.request.Request(api, headers={"User-Agent": "Mythos-AgentReachLimb/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc), "limb": "agent_reach"}
        parts: list[str] = []
        if data.get("AbstractText"):
            parts.append(str(data["AbstractText"]))
        if data.get("Heading"):
            parts.insert(0, f"# {data['Heading']}")
        for t in (data.get("RelatedTopics") or [])[:8]:
            if isinstance(t, dict) and t.get("Text"):
                parts.append(f"- {t['Text']}")
        text = "\n".join(parts).strip()
        if not text:
            return {
                "ok": False,
                "error": "No Instant Answer; use research.web for fuller scrape.",
                "limb": "agent_reach",
                "via": "duckduckgo",
            }
        return {
            "ok": True,
            "limb": "agent_reach",
            "via": "duckduckgo",
            "topic": topic,
            "answer_preview": text[:12_000],
            "stdout": text[:120_000],
        }

    def _yt_dlp_url(self, yt: str, url: str) -> dict[str, Any]:
        cmd = [
            yt,
            "--skip-download",
            "--write-auto-sub",
            "--write-sub",
            "--sub-lang",
            "en.*,en",
            "--print",
            "%(title)s\n%(description)s\n%(channel)s\n%(upload_date)s",
            "--print",
            "subtitle:%(requested_subtitles)s",
            url,
        ]
        # Prefer JSON dump + auto subs to files in temp is heavy; keep metadata + --get-comments off
        meta = self._subprocess(
            [yt, "--dump-json", "--skip-download", "--no-warnings", url],
            timeout=120,
        )
        if not meta.get("ok"):
            return meta
        try:
            info = json.loads(meta.get("stdout") or "{}")
        except json.JSONDecodeError:
            return {"ok": False, "error": "yt-dlp JSON parse failed", "limb": "agent_reach"}
        title = info.get("title") or ""
        desc = (info.get("description") or "")[:4000]
        channel = info.get("channel") or info.get("uploader") or ""
        # Subtitles: try --print-to-file or extract from automatic_captions keys
        sub_preview = ""
        subs = info.get("automatic_captions") or info.get("subtitles") or {}
        # Fetch VTT via yt-dlp -o - --write-auto-sub is awkward; use --print after list
        sub_cmd = [
            yt,
            "--skip-download",
            "--write-auto-sub",
            "--sub-lang",
            "en",
            "--sub-format",
            "vtt/best",
            "--convert-subs",
            "srt",
            "-o",
            str(_home() / ".agent-reach" / "cache" / "yt_%(id)s"),
            "--no-warnings",
            url,
        ]
        cache = _home() / ".agent-reach" / "cache"
        cache.mkdir(parents=True, exist_ok=True)
        _ = self._subprocess(sub_cmd, timeout=180)
        vid = info.get("id") or "unknown"
        for p in sorted(cache.glob(f"yt_{vid}*")):
            if p.suffix.lower() in (".srt", ".vtt", ".txt"):
                try:
                    sub_preview = p.read_text(encoding="utf-8", errors="replace")[:8000]
                    break
                except OSError:
                    pass
        if not sub_preview and isinstance(subs, dict):
            sub_preview = f"(captions available for langs: {', '.join(list(subs)[:12])})"
        text = f"Title: {title}\nChannel: {channel}\n\nDescription:\n{desc}\n\nTranscript/captions:\n{sub_preview}"
        return {
            "ok": True,
            "limb": "agent_reach",
            "via": "yt-dlp",
            "url": url,
            "title": title,
            "answer_preview": text[:12_000],
            "stdout": text[:120_000],
        }

    def _yt_dlp_search(self, yt: str, query: str, limit: int = 5) -> dict[str, Any]:
        spec = f"ytsearch{limit}:{query}"
        proc = self._subprocess(
            [yt, "--dump-json", "--flat-playlist", "--skip-download", "--no-warnings", spec],
            timeout=90,
        )
        if not proc.get("ok") and not (proc.get("stdout") or "").strip():
            return proc
        lines = [ln for ln in (proc.get("stdout") or "").splitlines() if ln.strip()]
        rows: list[str] = []
        for ln in lines[:limit]:
            try:
                info = json.loads(ln)
            except json.JSONDecodeError:
                continue
            title = info.get("title") or ""
            vid = info.get("id") or ""
            url = info.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
            rows.append(f"- {title}\n  {url}")
        text = f"YouTube search: {query}\n\n" + ("\n".join(rows) if rows else "(no results)")
        return {
            "ok": bool(rows),
            "limb": "agent_reach",
            "via": "yt-dlp",
            "query": query,
            "answer_preview": text[:12_000],
            "stdout": text[:120_000],
            "error": None if rows else (proc.get("error") or "no results"),
        }

    def _yt_module(self, py: Path, url: str) -> dict[str, Any]:
        code = (
            "import json,yt_dlp;\n"
            f"u={url!r};\n"
            "with yt_dlp.YoutubeDL({'quiet':True,'skip_download':True,'no_warnings':True}) as y:\n"
            " i=y.extract_info(u,download=False);\n"
            " print(json.dumps({k:i.get(k) for k in ('id','title','channel','description','uploader')},ensure_ascii=False))"
        )
        proc = self._subprocess([str(py), "-c", code], timeout=120)
        if not proc.get("ok"):
            return proc
        try:
            info = json.loads(proc.get("stdout") or "{}")
        except json.JSONDecodeError:
            return {"ok": False, "error": "parse failed", "limb": "agent_reach"}
        text = (
            f"Title: {info.get('title')}\nChannel: {info.get('channel') or info.get('uploader')}\n\n"
            f"{(info.get('description') or '')[:4000]}"
        )
        return {
            "ok": True,
            "limb": "agent_reach",
            "via": "yt_dlp_module",
            "url": url,
            "answer_preview": text[:12_000],
            "stdout": text[:120_000],
        }

    def _github(self, query: str) -> dict[str, Any]:
        gh = _find_tool("gh")
        if not gh:
            # public API fallback for owner/repo
            m = re.match(r"(?:https://github\.com/)?([\w.-]+)/([\w.-]+)", query.strip())
            if m:
                api = f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}"
                try:
                    req = urllib.request.Request(api, headers={"User-Agent": "Mythos-AgentReachLimb/1.0", "Accept": "application/vnd.github+json"})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    text = f"{data.get('full_name')}\n{data.get('description')}\n⭐ {data.get('stargazers_count')} · {data.get('html_url')}\n{data.get('homepage') or ''}"
                    return {
                        "ok": True,
                        "limb": "agent_reach",
                        "via": "github_api",
                        "answer_preview": text[:12_000],
                        "stdout": text[:120_000],
                    }
                except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                    return {"ok": False, "error": str(exc), "limb": "agent_reach"}
            return {
                "ok": False,
                "error": "gh CLI not found. Install GitHub CLI or pass owner/repo.",
                "limb": "agent_reach",
            }
        if "/" in query and " " not in query.strip():
            return self._subprocess([gh, "repo", "view", query.strip()], timeout=60)
        return self._subprocess([gh, "search", "repos", query, "--limit", "8"], timeout=60)

    def _rss(self, feed_url: str) -> dict[str, Any]:
        feed_url = (feed_url or "").strip()
        if not feed_url:
            return {"ok": False, "error": "Provide an RSS/Atom URL.", "limb": "agent_reach"}
        try:
            import feedparser  # type: ignore
        except ImportError:
            # try agent-reach venv
            py = _home() / ".agent-reach-venv" / "Scripts" / "python.exe"
            if py.is_file():
                code = (
                    "import feedparser,json,sys;\n"
                    f"d=feedparser.parse({feed_url!r});\n"
                    "items=[{'title':e.get('title'),'link':e.get('link'),'summary':(e.get('summary') or '')[:400]} for e in d.entries[:12]];\n"
                    "print(json.dumps({'title':d.feed.get('title'),'items':items},ensure_ascii=False))"
                )
                return self._subprocess([str(py), "-c", code], timeout=60)
            return {"ok": False, "error": "feedparser not installed", "limb": "agent_reach"}
        d = feedparser.parse(feed_url)
        lines = [f"# {getattr(d.feed, 'title', feed_url)}"]
        for e in list(d.entries)[:12]:
            lines.append(f"- {e.get('title')}\n  {e.get('link')}")
        text = "\n".join(lines)
        return {
            "ok": True,
            "limb": "agent_reach",
            "via": "feedparser",
            "answer_preview": text[:12_000],
            "stdout": text[:120_000],
        }

    def _subprocess(self, cmd: list[str], *, timeout: int = 90) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=_enriched_env(),
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timed out after {timeout}s", "cmd": cmd, "limb": "agent_reach"}
        except OSError as exc:
            return {"ok": False, "error": str(exc), "cmd": cmd, "limb": "agent_reach"}
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        ok = proc.returncode == 0
        out: dict[str, Any] = {
            "ok": ok,
            "limb": "agent_reach",
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": stdout[:120_000],
            "stderr": stderr[:20_000],
        }
        if stdout:
            out["answer_preview"] = stdout[:12_000]
        if not ok:
            out["error"] = stderr or stdout or f"exit {proc.returncode}"
        return out
