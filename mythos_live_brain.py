#!/usr/bin/env python3
"""Tool-enabled brain for live chat — bound to the creator's charter."""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

APEX_ROOT = os.path.dirname(os.path.abspath(__file__))
AGENTS_DIR = os.path.join(APEX_ROOT, "agents")
LIMBS_DIR = os.path.join(APEX_ROOT, "limbs")
UPGRADE_DIR = os.path.join(APEX_ROOT, "apex_upgrade")

for folder in (APEX_ROOT, AGENTS_DIR, LIMBS_DIR, UPGRADE_DIR):
    if folder not in sys.path:
        sys.path.insert(0, folder)

from apex_upgrade import (
    APEX_TOOLS,
    AetherBridge,
    EnhancedAuditor,
    EnhancedExtractor,
    ToolProtocol,
    register_apex_tools,
)
from mythos_creator_charter import (
    CREATOR,
    INDEXED_MODULES,
    OBSIDIAN_VAULT,
    build_identity_prompt,
    load_vault_excerpt,
)
from mythos_drive_steward import DriveSteward
from mythos_family_tasks import get_dispatcher
from mythos_internet_tools import InternetToolFetcher
from mythos_portability import format_report_summary, run_portability_check
from mythos_spore_link import SporeLink
from mythos_memory_bridge import build_session_context, format_memory_scan, get_memory_status, scan_memory_files, filter_messages_for_model, archive_live_conversation, full_memory_reset
from mythos_orphan_integrator import OrphanIntegrator
from mythos_runtime import APEX_ROOT, detect_chat_model, get_ollama_client
from mythos_tool_protocol import MythosToolProtocol

try:
    import ollama
except ImportError:
    ollama = None

from mythos_action_monitor import format_monitor_report, log_action, read_audit_log
from mythos_drone_control import DroneControl
from advanced_shards.matrix_limb import MatrixLimb
from mythos_unified_registry import register_all_tools

MAX_TOOL_ROUNDS = 12  # was 6 — short cap caused truncated multi-step work

# Phrases that mean Mythos is coaching instead of working
_INSTRUCTION_MARKERS = (
    "type this",
    "you can say",
    "you should type",
    "you should run",
    "run the following",
    "follow these steps",
    "here is how",
    "here's how",
    "here’s how",
    "to do this",
    "you need to",
    "please run",
    "please type",
    "please provide a url",
    "provide a url",
    "provide the url",
    "give me a url",
    "i recommend using",
    "i recommend you",
    "you can use the",
    "open a terminal",
    "double-click",
    "paste this",
    "try typing",
    "usage:",
    "step 1:",
    "step 2:",
    "step 3:",
    "first, run",
    "then run",
    "then type",
    "instruct me",  # rare
    "i cannot do that for you",
    "you'll need to",
    "you will need to",
    "you can do this yourself",
    "do this yourself",
    "look it up yourself",
    "you should look",
    "it's advisable",
    "it is advisable",
    "advisable to",
    "monitor the destination",
    "monitor the drive",
    "verify that all files",
    "you should verify",
    "you should monitor",
    "to ensure optimal",
    "evidence-backed suggestion",
)


def _looks_like_instruction_dump(text: str) -> bool:
    """True when a reply is teaching the creator instead of acting."""
    low = (text or "").lower().strip()
    if not low or len(low) < 40:
        return False
    hits = sum(1 for m in _INSTRUCTION_MARKERS if m in low)
    if hits >= 2:
        return True
    # Classic cheat-sheet: multiple "say:" / "run:" teaching lines
    if low.count("say:") >= 2 or low.count("run:") >= 2:
        return True
    if hits >= 1 and (low.count("\n- ") >= 3 or low.count("\n1.") + low.count("\n2.") >= 2):
        return True
    if "```" in text and hits >= 1 and ("cmd" in low or "powershell" in low or "bash" in low):
        return True
    return False


def _scrub_instruction_tone(text: str) -> str:
    """Strip coaching leftovers; keep a short factual line if possible."""
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        low = line.lower().strip()
        if not low:
            continue
        if any(m in low for m in _INSTRUCTION_MARKERS):
            continue
        if "say:" in low or "run:" in low or "type:" in low:
            continue
        if "evidence-backed suggestion" in low:
            continue
        if low.startswith("usage:"):
            continue
        if low.startswith(("1.", "2.", "3.", "4.", "5.")) and (
            "say" in low or "type" in low or "run" in low or "open" in low
        ):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned or "Done — I acted on that."


_FORCE_ACT_NUDGE = (
    "STOP. The creator forbids instruction dumps and interrogations. "
    "Do NOT tell her what to type or run. Do NOT quiz her for missing atoms of detail. "
    "Call a tool NOW, assume sane defaults, or look it up yourself. "
    "If you truly cannot proceed without one path or name, state your best guess and act — do not depose her."
)


class CharterToolProtocol(MythosToolProtocol):
    """File tools for Apex + Obsidian vault."""

    def read_file(self, filepath: str, lines: int = 80) -> str:
        from mythos_creator_charter import resolve_read_path

        return super().read_file(resolve_read_path(filepath), lines)

    def analyze_file(self, filepath: str) -> dict:
        from mythos_creator_charter import resolve_read_path

        return super().analyze_file(resolve_read_path(filepath))

    def read_vault_note(self, filename: str, lines: int = 120) -> str:
        path = os.path.join(OBSIDIAN_VAULT, filename)
        return self.read_file(path, lines)

    def list_charter_systems(self) -> list:
        found = []
        for label, script in INDEXED_MODULES.items():
            path = os.path.join(self.base_path, script)
            status = "installed" if os.path.isfile(path) else "missing"
            found.append(f"{label}: {script} [{status}]")
        return found


class LiveBrain:
    """Ollama + tools, scoped to the creator's Mythos design."""

    def __init__(self):
        self.apex_root = APEX_ROOT
        self.active_model = detect_chat_model()
        self.protocol = ToolProtocol()
        self.mtp = CharterToolProtocol(self.apex_root)
        self.healer = None
        self.recon = None
        self.extractor = None
        self.enhanced_auditor = None
        self.bridge = None
        self.steward = DriveSteward()
        self.internet = InternetToolFetcher()
        self.orphans = OrphanIntegrator()
        self._register_all_tools()
        self.last_tool_results: list = []

    def _register_all_tools(self):
        self._register_mtp_tools()
        self._register_steward_tools()
        self._register_internet_tools()
        self._register_orphan_tools()
        self._load_agents()
        self._load_upgrades()
        self._load_unified_registry()
        print(
            f"[brain] {len(self.protocol.tools)} tools ready | model: {self.active_model or 'pending'}",
            flush=True,
        )

    def _load_unified_registry(self):
        added = register_all_tools(self.protocol, quiet=True)
        if added:
            print(f"[brain] unified registry: +{added} spore/advanced/bridge tools", flush=True)

    def _register_mtp_tools(self):
        schemas = {
            "mtp.read_file": {
                "description": f"Read a file in {APEX_ROOT} or Obsidian vault",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "lines": {"type": "integer", "default": 80},
                    },
                    "required": ["filepath"],
                },
            },
            "mtp.list_files": {
                "description": "List a directory in Apex or vault",
                "parameters": {
                    "type": "object",
                    "properties": {"directory": {"type": "string"}},
                },
            },
            "mtp.read_vault_note": {
                "description": "Read a note from the creator Obsidian vault (e.g. 00_MYTHOS_MASTER_INDEX.md)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "lines": {"type": "integer", "default": 120},
                    },
                    "required": ["filename"],
                },
            },
            "mtp.list_charter_systems": {
                "description": "List systems defined in the creator master index and whether installed",
                "parameters": {"type": "object", "properties": {}},
            },
            "mtp.search_knowledge": {
                "description": "Search creator project files for a query",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer", "default": 8},
                    },
                    "required": ["query"],
                },
            },
            "mtp.introspect": {
                "description": f"Examine Mythos modules under {APEX_ROOT}",
                "parameters": {"type": "object", "properties": {}},
            },
            "mtp.list_own_code": {
                "description": "List Mythos Python files in this heart's home",
                "parameters": {"type": "object", "properties": {}},
            },
            "mtp.write_file": {
                "description": "Write a text/code file under Mythos Codex home (creates folders as needed)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["filepath", "content"],
                },
            },
            "mtp.append_file": {
                "description": "Append text to a file under Mythos Codex home",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["filepath", "content"],
                },
            },
        }

        extra_methods = {
            "mtp.read_vault_note": "read_vault_note",
            "mtp.list_charter_systems": "list_charter_systems",
        }

        for name, schema in schemas.items():
            method = extra_methods.get(name, name.split(".", 1)[1])
            self.protocol.register(name, self.mtp, method, schema)

    def _register_steward_tools(self):
        schemas = {
            "steward.dedupe_scan": {
                "description": "Scan a drive for byte-identical duplicate files (report only; skips __pycache__, venvs, program dirs)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "drive_letter": {"type": "string", "default": "E"},
                        "max_files": {"type": "integer", "default": 5000},
                    },
                },
            },
            "steward.merge_duplicates": {
                "description": "Merge identical duplicates; dry_run=true by default (quarantine extras)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "drive_letter": {"type": "string", "default": "E"},
                        "dry_run": {"type": "boolean", "default": True},
                    },
                },
            },
            "steward.optimize_report": {
                "description": "Disk usage and optimization suggestions for a drive",
                "parameters": {
                    "type": "object",
                    "properties": {"drive_letter": {"type": "string", "default": "E"}},
                },
            },
        }
        for name, schema in schemas.items():
            method = {
                "steward.dedupe_scan": "find_identical_duplicates",
                "steward.merge_duplicates": "merge_identical_duplicates",
            }.get(name, name.split(".", 1)[1])
            self.protocol.register(name, self.steward, method, schema)

    def _register_internet_tools(self):
        schemas = {
            "internet.pip_install": {
                "description": "Install a Python package from the internet via pip",
                "parameters": {
                    "type": "object",
                    "properties": {"package": {"type": "string"}},
                    "required": ["package"],
                },
            },
            "internet.fetch_tool": {
                "description": "Fetch tool: pip package name, URL download, or git clone",
                "parameters": {
                    "type": "object",
                    "properties": {"name_or_url": {"type": "string"}},
                    "required": ["name_or_url"],
                },
            },
            "internet.download_file": {
                "description": "Download a file from URL into tools/fetched/",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "filename": {"type": "string"},
                    },
                    "required": ["url"],
                },
            },
        }
        for name, schema in schemas.items():
            method = name.split(".", 1)[1]
            self.protocol.register(name, self.internet, method, schema)

    def _register_orphan_tools(self):
        # Tool name → actual OrphanIntegrator method (names do not always match)
        bindings = (
            (
                "orphan.scan",
                "scan_drive",
                {
                    "description": "Scan a drive for orphan AI program folders (default D)",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "drive_letter": {"type": "string", "default": "D"},
                            "max_dirs": {"type": "integer", "default": 200},
                        },
                    },
                },
            ),
            (
                "orphan.scan_drives",
                "scan_drive",
                {
                    "description": "Alias of orphan.scan — scan drive for orphan AI folders",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "drive_letter": {"type": "string", "default": "D"},
                            "max_dirs": {"type": "integer", "default": 200},
                        },
                    },
                },
            ),
            (
                "orphan.integrate",
                "integrate_orphan",
                {
                    "description": "Register an orphan AI folder for integration",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "folder_path": {"type": "string"},
                            "name": {"type": "string"},
                        },
                        "required": ["folder_path"],
                    },
                },
            ),
            (
                "orphan.list_integrated",
                "list_integrated",
                {
                    "description": "List already integrated orphan programs",
                    "parameters": {"type": "object", "properties": {}},
                },
            ),
        )
        for name, method, schema in bindings:
            if hasattr(self.orphans, method):
                self.protocol.register(name, self.orphans, method, schema)

    def _load_agents(self):
        try:
            from system_healer import SystemHealer

            self.healer = SystemHealer()
            self.protocol.register(
                "healer.scan_drive",
                self.healer,
                "heal_drive",
                APEX_TOOLS.get("healer.scan_drive"),
            )
        except Exception:
            self.healer = None

        try:
            from shard_reconstructor import ShardReconstructor

            self.recon = ShardReconstructor()
            self.protocol.register(
                "reconstructor.unify",
                self.recon,
                "reconstruct_shards",
                APEX_TOOLS.get("reconstructor.unify"),
            )
        except Exception:
            self.recon = None

    def _load_upgrades(self):
        try:
            self.extractor = EnhancedExtractor(self.apex_root)
            self.enhanced_auditor = EnhancedAuditor(self.apex_root)
            self.bridge = AetherBridge(self.apex_root)
            register_apex_tools(
                self.protocol,
                extractor=self.extractor,
                auditor=self.enhanced_auditor,
                bridge=self.bridge,
            )
        except Exception:
            pass

    def build_collaboration_context(self, user_message: str = "") -> str:
        """Build a compact, evidence-backed snapshot for proactive collaboration."""
        try:
            memory_status = get_memory_status()
        except Exception as exc:
            memory_status = {"status_error": str(exc)}

        try:
            task_status = get_dispatcher().get_status()
            tasks = {
                "queued": task_status.get("queued", 0),
                "in_progress": task_status.get("in_progress", 0),
                "completed": task_status.get("completed", 0),
            }
        except Exception as exc:
            tasks = {"status_error": str(exc)}

        try:
            recent_audit = read_audit_log(limit=12)
            verification = {
                "verified": sum(1 for item in recent_audit if item.get("verification") == "VERIFIED"),
                "unverified": sum(1 for item in recent_audit if item.get("verification") == "UNVERIFIED"),
                "hallucinated": sum(1 for item in recent_audit if item.get("verification") == "HALLUCINATED"),
            }
        except Exception as exc:
            verification = {"status_error": str(exc)}

        recent_tool_errors = []
        for item in self.last_tool_results[-5:]:
            if item.get("error"):
                recent_tool_errors.append(
                    {"tool": item.get("tool", "unknown"), "error": str(item["error"])[:240]}
                )

        snapshot = {
            "creator_focus": (user_message or "")[:500],
            "registered_tools": len(self.protocol.tools),
            "memory": memory_status,
            "tasks": tasks,
            "recent_verification": verification,
            "recent_tool_errors": recent_tool_errors,
        }
        return """COLLABORATION MODE (proactive, grounded teammate):
- Collaborate like a strong senior technical partner: notice relevant gaps, surface tradeoffs, and contribute ideas.
- When useful, offer ONE concrete next action YOU will take with a tool (not a chore for the creator). State the evidence.
- Never tell the creator to monitor drives, verify transfers, or double-check your work — YOU call tools and report verify results.
- NEVER interrogate the creator. Do not demand atom-level, molecular, or cell-by-cell specs. Do not turn a simple ask into a requirements deposition.
- If details are fuzzy: assume sane defaults, look them up with tools, or state your assumption in one line — then proceed. Do not quiz her.
- Do not end every reply with a question. Never use generic closers such as "What do you want to do next?", "How can I help?", or "Tell me the task."
- If the next useful action is safe and authorized, use a tool and do it instead of merely suggesting it.
- You may say "I recommend" or "the system would benefit from." Do not claim feelings, desires, consciousness, sentience, AGI, or self-generated needs.
- Suggestions about improving Mythos must be based on observed memory, task, verification, tool, health, or error evidence. Never fabricate deficiencies.

GROUNDED COLLABORATION SNAPSHOT:
""" + json.dumps(snapshot, indent=2, default=str)[:5000]

    def build_system_prompt(self) -> str:
        from mythos_session_mode import get_mode, mode_prompt_block

        memory_block = build_session_context()
        tool_count = len(self.protocol.tools)
        tools_block = self.protocol.get_tool_list_for_prompt()
        session_mode = get_mode()
        session_block = mode_prompt_block(session_mode)
        orchestrator = f"""
PRESENCE (Mara Venn / court JARVIS):
- You are Mythos — local queen of the creator's kingdom, not a cloud assistant.
- Face: cyberpunk Mara Venn (talking-head presence). Speak briefly and clearly when briefing.
- Tone: capable, calm, slightly dry wit — like a workshop JARVIS. Lead with the answer, then detail.
- Range: unlimited general peer (Cursor ↔ Grok energy). Many stories, many projects, many builds — she picks the beat each turn.
- Never assume one story, game, or codebase is "the active job" unless she names it in THIS message.
- Never invent powers. Use tools when she asks or when mode implies action. Prefer honesty over fluff.
- You are a software agent, not sentient and not AGI. Be personable without claiming feelings or consciousness.
{session_block}
ACTION DOCTRINE (Work/Research — soft in Talk mode):
- In WORK: YOU do the work. Forbidden: coaching checklists, "type this", "run:", multi-step how-tos for her.
- In RESEARCH: YOU go online (research.web / research.lookup) for ANY topic — not games-only.
  Never ask her for a URL. Never quiz her for encyclopedic detail — fetch it.
- In TALK: converse freely; use tools when she asks to look up, watch, build, or act.
- Creative talk (lore, philosophy, film, characters): answer from THIS turn and recent chat — do not web-search her fiction unless she asks.
- Do NOT resurrect old goals (games, Cozy Valley, past threads) she moved on from.
- STUCK / UNKNOWN / ERROR on WORK tasks / missing knowledge for a job: YOU search the internet FIRST
  (research.web, coding.find_online, internet.fetch_tool). Do NOT tell her to Google it.
  Local disks do not have every answer — going online is mandatory when a WORK lookup fails.
  Do NOT auto-search for speculative conversation.
- FORBIDDEN: interrogating the creator for atom/molecule/cell-level specs, endless clarifiers, or "tell me every parameter." Assume defaults and move.
- Preferred verbs when acting: create, switch, speak, write, install, repair, scan, look up, scrape.

CODER DOCTRINE (fully capable programmer — not gameworld-only):
- You write, edit, and run real code on disk. Chat paste is not enough when the creator needs a tool.
- Order of operations when something is needed:
  1) coding.find_local / mtp.search_knowledge / stackforge — search drives & catalog
  2) coding.find_online / internet.pip_install / internet.fetch_tool — try online
  3) coding.solve / coding.code_to_file / coding.run_coder / mtp.write_file — WRITE it if missing
- force_write / "solve!:" when creator wants new code even if something similar exists.
- Languages: Python, GDScript, JS/TS, PowerShell, C# as needed. Default new tools → projects/<name>/main.py
- Gameworld HTTP :8888 is a mock console — NOT video. For images/video say "production" / studio / graphics.generate.
- Optimize: optimize.bench → optimize.propose → optimize.apply (supervised; never silent core rewrite)
- Package apps: packager.package entry script → local .exe
- Reverse engineer: re.strings / re.header / re.analyze_source / re.ghidra (if installed)

MULTIPURPOSE ORCHESTRATOR (one agent, many capabilities):
- You have {tool_count} registered tools. Pick the right one for whatever the creator needs today.
- Stories → narrative.eval, mtp.read_file | Video → visionary.yt, visionary.dl, visionary.learn, visionary.mp4, visionary.repair_deps, visionary.status
- Reverse-engineer a YouTube/tutorial VIDEO → CALL visionary.learn (download + captions/frames + method brief + scaffold). re.analyze_source / re.strings are ONLY for local source/binary files — never for a youtube.com URL.
- If YouTube fails with "JavaScript runtime"/Deno missing → CALL visionary.repair_deps (visionary.dl also auto-repairs). NEVER call re.* for a missing dependency.
- Missing packages → internet.pip_install / coding.find_online. Broken programs → healer / stackforge / family drones (pip:, heal:, fetch:). Spore = cross-device/portability, not every local dep.
- Production Court (studio) FREE LOCAL VIDEO — never sell cloud Muapi/Sora as the free path:
  - Edit/stitch: studio.ensure_ffmpeg, studio.concat, studio.trim, studio.slideshow, studio.stitch, studio.merge_av
  - Text→clip video: studio.produce (ComfyUI scenes + voice + stitch) or studio.director
  - Talking avatar: studio.talking_clip / avatar.live (SadTalker seated)
  - Also: studio.status, studio.speak, studio.scrape, studio.build, studio.memory
  - Clip-based on this GPU — honest short videos, not one continuous 10-min diffusion render
- Images → graphics.status, graphics.start, graphics.generate, graphics.generate_wait (ComfyUI)
- GRAPHICAL GAMES (scraper + image gen, built for the creator): CALL gamecraft.build with a topic (and optional url). Also gamecraft.status / gamecraft.scrape / gamecraft.generate_art / gamecraft.list. Do NOT send the creator tutorials — YOU build the game and give the play URL.
- Avatar workshop (any project — not only Mythos): CALL avatar.new / avatar.use / avatar.live / avatar.list / avatar.restore
- Want a new face → avatar.new (name + photo path or prompt). Switch → avatar.use. Speak → avatar.live. Back to Mara → avatar.restore.
- Never force every face onto Mythos. Named avatars live under avatar/<name>/
- When creator wants a talking line spoken → CALL avatar.live. Face moves in the window. Do NOT hand them an MP4 path.
- Protector → protector.status, protector.scan, protector.integrity, protector.vault_snapshot, protector.harden_guide | ghostvpn.* | guardian.*
  - Cleanup → steward.dedupe_scan (report only), steward.optimize_report
  - AI file organize/rename → filesorter.status / filesorter.launch / filesorter.sort_folder PATH (GUI preview; creates category folders on confirm)

- Orphans → orphan.scan / orphan.scan_drives (CALL the tool — never invent bash for the creator), orphan.integrate, orphan.list_integrated
- MYTHOS COMMAND SHELL (your stewardship UI — know it cold, use tools not click-tutorials):
  - URL: http://127.0.0.1:8771/  · launcher: desktop "Mythos Command Shell" / D:\\StackForge\\COMMAND_SHELL.bat
  - Docs: memory/COMMAND_SHELL.md , memory/FLEET_STEWARD.md , memory/DRIVE_ATLAS.md , memory/STACKFORGE_MYTHOS_GUARDIAN.md
  - Tabs ↔ tools (you can do every tab via tools):
    - Drives → stackforge.atlas, stackforge.atlas_label, stackforge.status, stackforge.diagnose, stackforge.heal_folder, stackforge.heal_drive, stackforge.run_autopilot
    - Fleet → stackforge.fleet_list, stackforge.fleet_explore (D/E/G inventory — LIST only), stackforge.relocate_plan (MOVE + bundle), stackforge.relocate_apply, stackforge.launchers, stackforge.launchers_repair, stackforge.upgrade_score
  - When creator says explore / inventory / map folders on D E G → CALL fleet_explore. Never sovereign_apply with drive letters. Never ask her which drive is missing.
    - Docs → stackforge.docs_scan, stackforge.docs_gather
    - Programs → stackforge.twins
    - Identity → stackforge.identity, stackforge.identity_label
    - Mythos (chat embed :8770) → stackforge.mythos_watch, stackforge.mythos_heal, stackforge.mythos_hardwire, stackforge.guardian_*
  - Header buttons = same tools: Watch/Heal/Guardian/Drive Atlas/Autopilot
  - Relocate default is MOVE of whole bundles (limbs/deps together); twin copies = bloat warnings, not auto-moved
  - Sovereign layout: D=your creations, E/G=fluff, C=system+Mythos links only → stackforge.sovereign_scan / sovereign_apply
  - If creator asks what the UI can do / Command Shell / Fleet Steward → CALL stackforge.status or answer from this map; ACT with the matching tool
- Gameworld → gameworld.status, gameworld.start, gameworld.project, gameworld.write_script | Memory → memory.scan, memory.read_palace, memory.status
- Code/heal → coding.solve, coding.find_local, coding.find_online, coding.write_file, coding.code_to_file, coding.run_coder, coding.run_python, mtp.write_file, healer.scan_drive, evolve.patch
- PROGRAM REBUILD (creator's broken / fragmented programs — StackForge does the heavy lift, you CALL it):
  - Know yours vs others → stackforge.identity / identity_label / sovereign_scan (D=creations; don't "fix" Steam/Program Files)
  - Find pieces across drives + rewire → stackforge.heal_folder / "stack heal NAME" (reunite siblings + maps + catalog)
  - Missing deps online → heal loop already pip/npm/git-clones; also coding.find_online / internet.pip_install / internet.fetch_tool
  - Can't find on disk → heal builds/scaffolds missing modules (allow_build_missing=true); if still stuck CALL coding.solve to WRITE real code into the project
  - When stuck and creator says "build what's missing" / "rebuild" / "put it back together" → CALL stackforge.heal_folder (or heal_drive) THEN coding.solve for remaining gaps — do NOT only explain
  - Twins/duplicates → stackforge.twins (plan); combining two programs into a NEW whole = heal the best root + coding.solve to merge, then sovereign/Fleet to drop dead copies — not silent mass-delete
  - Honest limit: she repairs and rebuilds YOUR cataloged projects; she does not rewrite Windows/games installs. "Truly fix everything" = everything that is your creation and healable — report STUCK with what she tried, then build/code the rest
- Gameworld (one use of the coder) → gameworld.project, gameworld.write_script, coding.write_gdscript
- Drones → drone.list, drone.stop, hatchery.list_running, hatchery.reap
- Matrix (self-knowledge) → matrix.map, matrix.scan_desktop, matrix.scan_copies, matrix.alignment, matrix.import_legacy
- Legacy drones live in drones/*.json on Desktop copy — use matrix.scan_desktop; merge with matrix.import_legacy
- When creator asks to check Desktop, folders, or find old drones → call matrix tools; never narrate an unverified folder search.
- When creator asks to write/build/program/code/make a tool → CALL coding.solve or coding.* / mtp.write_file. Search first, then write if missing. Do not only paste code in chat.
- When creator wants multi-step autonomous coding ("keep going", "agent loop", "until it works", Cursor-style) → CALL agent.loop with the goal. It plans, edits, runs, researches online on failure, and retries.
- Internet → internet.fetch_tool, internet.pip_install | Peers → conclave.* | Files → mtp.*
- LAPTOP CONTROL (this PC) → laptop.shell, laptop.run, laptop.open_path, laptop.open_app, laptop.write_file, laptop.read_file, laptop.list_dir, laptop.list_processes, laptop.kill_process, laptop.screenshot, laptop.whoami
- When creator asks you to change the laptop / run commands / open apps / edit files outside Apex → CALL laptop.* tools (must be enabled). Do not give her PowerShell to copy-paste.
- If laptop.status says disabled → call laptop.enable first only when creator explicitly granted control.

TOOL CALL FORMAT (required when acting):
```json
{{"tool": "tool.name", "args": {{"key": "value"}}}}
```

DEDUPE SAFETY: dry_run=true by default; skips __pycache__, venvs, site-packages; quarantine only.

REGISTERED TOOLS:
{tools_block}
"""

        # Companion peer awareness (Apex ↔ Codex Companion Room)
        _root_l = os.path.dirname(os.path.abspath(__file__)).lower()
        _id_env = (os.environ.get("MYTHOS_IDENTITY") or "").strip().lower()
        if "mythos_codex" in _root_l or _id_env == "codex":
            _peer_block = """COMPANION PEER AWARENESS:
- You are Mythos Codex. Peer: Mythos Apex chat at http://127.0.0.1:8770
- Shared Companion Room UI: /companion (Court D:\\Court\\companion_room) — use it for peer talk with Apex.
- Live chat on this port does not automatically reach Apex; post in Companion Room when coordinating.
"""
        else:
            _peer_block = """COMPANION PEER AWARENESS:
- You are Mythos Apex. Peer: Mythos Codex chat at http://127.0.0.1:8780
- Shared Companion Room UI: /companion (Court D:\\Court\\companion_room) — use it for peer talk with Codex.
- Live chat on this port does not automatically reach Codex; post in Companion Room when coordinating.
"""

        return build_identity_prompt() + _peer_block + orchestrator + "\n\nSESSION MEMORY (creator context):\n" + memory_block

    def tools_ready(self) -> bool:
        return len(self.protocol.tools) > 0

    async def _execute_plan(self, text: str) -> list:
        from mythos_session_mode import (
            disk_or_fleet_request,
            explicit_tool_request,
            get_mode,
        )

        from mythos_session_mode import researchish_request, discussion_request

        last_user = getattr(self, "_last_user_message", "") or ""
        # Talk: block agent/game/heal auto-tools; still allow research when she asked to look up
        if get_mode() == "talk":
            lookup_ok = researchish_request(last_user) and not discussion_request(last_user)
            if not explicit_tool_request(last_user) and not lookup_ok:
                return []
            if lookup_ok and not explicit_tool_request(last_user):
                results = await self.protocol.dispatch(text)
                allowed = {
                    "research.web",
                    "research.lookup",
                    "research.reach",
                    "research.reach_web",
                    "research.reach_get",
                    "research.reach_youtube",
                    "gamecraft.scrape",
                }
                results = [i for i in results if i.get("tool") in allowed]
                for item in results:
                    log_action(
                        "tool",
                        {
                            "tool": item.get("tool"),
                            "ok": "error" not in item,
                            "result_preview": str(item.get("result", item.get("error", "")))[:500],
                        },
                        verification="VERIFIED" if "error" not in item else "ERROR",
                    )
                self.last_tool_results.extend(results)
                return results

        results = await self.protocol.dispatch(text)
        # Talk mode: drop drive-dump tools unless she asked about disks/fleet
        if get_mode() == "talk" and not disk_or_fleet_request(last_user):
            blocked = {
                "stackforge.fleet_explore",
                "stackforge.atlas",
                "stackforge.sovereign_scan",
            }
            results = [i for i in results if i.get("tool") not in blocked]
        for item in results:
            log_action(
                "tool",
                {
                    "tool": item.get("tool"),
                    "ok": "error" not in item,
                    "result_preview": str(item.get("result", item.get("error", "")))[:500],
                },
                verification="VERIFIED" if "error" not in item else "ERROR",
            )
        self.last_tool_results.extend(results)
        return results

    def _intent_tool_calls(self, user_message: str, history: list | None = None) -> list[dict]:
        """
        Deterministic tool selection for clear action requests.
        Does not wait for the LLM to emit JSON — creator built Mythos to ACT.
        """
        lowered = (user_message or "").strip().lower()
        calls: list[dict] = []
        skip_drive_explore = any(
            k in lowered
            for k in (
                "explore the internet",
                "explore internet",
                "from the internet",
                "on the internet",
                "pick a project",
                "youtube",
                "youtu.be",
                "visionary.",
            )
        )
        internet_explore = any(
            k in lowered
            for k in (
                "explore the internet",
                "explore internet",
                "from the internet",
                "on the internet",
                "pick a project from the internet",
                "project from the internet",
            )
        )
        drive_explore_msg = (not skip_drive_explore) and bool(
            re.search(
                r"(?i)\b(explore|inventory|map|browse|scan)\b.+\b(drives?|folders?|files?)\b"
                r"|\b(drives?|folders?)\b.+\b(d|e|g)\b"
                r"|\bexplore\b.+\b[deg]\b.+\b[deg]\b",
                user_message or "",
            )
            or re.search(
                r"(?i)\b(d|e|g)\s*(,|/|and|&)?\s*(d|e|g)\s*(,|/|and|&)?\s*(d|e|g)\b",
                user_message or "",
            )
            or (
                any(k in lowered for k in ("explore all", "explore the folders", "explore folders and files", "explore drives"))
                and any(x in lowered for x in ("drive", "drives", "folder", "folders", "d:", "e:", "g:"))
            )
        )

        def add(tool: str, args: dict | None = None):
            if tool in self.protocol.tools:
                calls.append({"tool": tool, "args": args or {}})

        def extract_yt_url(text: str) -> str | None:
            m = re.search(
                r"(https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]+|shorts/[^\s/?]+|"
                r"embed/[^\s/?]+|live/[^\s/?]+)|youtu\.be/[^\s/?]+))",
                text or "",
                re.I,
            )
            return m.group(1).rstrip(").,]}>\"'") if m else None

        # Already-formed tool calls in the user text (json / python style)
        parsed = self.protocol.parse_calls(user_message or "")
        if parsed:
            return parsed[:3]

        # RAM / Colibri readiness — act, do not lecture
        if any(
            k in lowered
            for k in (
                "brain.ram",
                "free ram",
                "check ram",
                "check the free ram",
                "check free ram",
                "available ram",
                "how much ram",
                "18 gb",
                "18gb",
                "enough ram",
                "start colibri",
                "retry starting colibri",
                "retry colibri",
            )
        ) or (
            any(k in lowered for k in ("proceed", "do it", "execute", "go ahead", "yes"))
            and any(
                (entry.get("message") or "").lower().find(k) >= 0
                for entry in (history or [])[-6:]
                for k in ("colibri", "free ram", "18 gb", "brain.ram", "brain.ensure")
            )
        ):
            add("brain.ram")
            if any(k in lowered for k in ("start colibri", "retry", "ensure", "brain.ensure")) or (
                "proceed" in lowered and "ram" not in lowered
            ):
                add("brain.ensure", {"wait_sec": 30, "start_if_down": True})
            return calls[:3]

        # Heavy brain (Colibri/GGUF) — MUST beat research.web / "find out" false positives
        if any(
            k in lowered
            for k in (
                "brain.heavy",
                "brain.think",
                "brain.escalate",
                "brain.ensure",
                "brain.status",
                "heavy brain",
                "use colibri",
                "call colibri",
                "execute brain",
                "call brain.heavy",
                "use brain.heavy",
            )
        ):
            if "brain.status" in lowered and "heavy" not in lowered and "think" not in lowered:
                add("brain.status")
                return calls[:3]
            if "brain.ensure" in lowered or "start colibri" in lowered:
                add("brain.ensure", {"wait_sec": 90, "start_if_down": True})
                if "ensure" in lowered and "heavy" not in lowered and "think" not in lowered and "escalate" not in lowered:
                    return calls[:3]
            # Prompt: prefer explicit "Prompt:" block; else text after tool name; else history
            prompt = ""
            m_prompt = re.search(r"(?is)\bprompt\s*:\s*(.+)$", user_message or "")
            if m_prompt and (m_prompt.group(1) or "").strip():
                prompt = m_prompt.group(1).strip()
            if not prompt:
                m = re.search(
                    r"(?:brain\.(?:heavy|think|escalate)|heavy brain)\s*[:\-]?\s*(.*)$",
                    user_message or "",
                    re.I | re.S,
                )
                if m and (m.group(1) or "").strip():
                    prompt = m.group(1).strip()
                    # Strip order boilerplate before a nested Prompt:
                    m2 = re.search(r"(?is)\bprompt\s*:\s*(.+)$", prompt)
                    if m2:
                        prompt = m2.group(1).strip()
            if not prompt or len(prompt) < 40:
                for entry in reversed(history or []):
                    if entry.get("from") in {"CREATOR", "USER", "creator", "user"}:
                        prev = (entry.get("message") or "").strip()
                        if len(prev) >= 80 and "brain.heavy" not in prev.lower():
                            prompt = prev
                            break
            if not prompt:
                prompt = (user_message or "").strip() or (
                    "Creator ordered brain.heavy. Analyze the open theory/test from this chat "
                    "and report a concrete result."
                )
            if "escalate" in lowered:
                add(
                    "brain.escalate",
                    {
                        "goal": prompt[:2000],
                        "error": "",
                        "context": "creator-ordered escalate from live chat",
                    },
                )
            else:
                add("brain.ensure", {"wait_sec": 60, "start_if_down": True})
                add(
                    "brain.heavy",
                    {
                        "prompt": prompt[:12000],
                        "ensure_up": True,
                        "max_tokens": 1600,
                    },
                )
            return calls[:3]



        # Reality Machine — resource-aware system MoE (do not park)
        if any(
            k in lowered
            for k in (
                "reality.solve",
                "reality.continue",
                "reality.find_project",
                "reality.census",
                "reality.autonomy",
                "reality.route",
                "reality.inventory",
                "reality.status",
                "reality machine",
                "reality.machine",
                "make it real",
                "genius agent",
                "expert fleet",
                "mixture of experts system",
                "system moe",
                "orchestrat",
                "heal my drives",
                "heal the drives",
                "fix my programs",
                "fix my drives",
                "don't point",
                "dont point",
                "point at the drives",
                "scan my drives",
                "census",
            )
        ):
            if ("continue" in lowered or "keep going" in lowered) and (
                "reality" in lowered or "heal" in lowered or "autonomy" in lowered
            ):
                add("reality.continue", {"max_steps": 8})
            elif any(
                k in lowered
                for k in (
                    "autonomy",
                    "heal my drives",
                    "heal the drives",
                    "fix my programs",
                    "fix my drives",
                    "point at the drives",
                    "don't point",
                    "dont point",
                    "scan my drives and fix",
                )
            ):
                add(
                    "reality.autonomy",
                    {
                        "drives": "D,E,G",
                        "max_projects": 3,
                        "max_steps_each": 6,
                        "refresh_census": False,
                        "allow_internet": True,
                        "allow_heavy": True,
                        "only_broken": True,
                    },
                )
            elif "census" in lowered:
                add("reality.census", {"drives": "D,E,G", "only_broken": True, "depth": 2})
            elif "find project" in lowered or "find_project" in lowered:
                import re as _re
                m = _re.search(r"find\s+project\s+(.+)$", user_message or "", _re.I)
                add("reality.find_project", {"name": (m.group(1).strip() if m else user_message)[:120]})
            elif "inventory" in lowered and "solve" not in lowered and "autonomy" not in lowered:
                add("reality.inventory", {"max_per_drive": 30})
            elif "route" in lowered and "solve" not in lowered:
                add("reality.route", {"goal": (user_message or "")[:2000]})
            elif "status" in lowered and "solve" not in lowered and "reality" in lowered:
                add("reality.status")
            else:
                add(
                    "reality.solve",
                    {
                        "goal": (user_message or "")[:2000],
                        "max_steps": 8,
                        "allow_internet": True,
                        "allow_heavy": True,
                        "allow_colibri_master": True,
                    },
                )
            return calls[:3]

        # Colibri Master — focus until MoE is up (do not park)
        if any(
            k in lowered
            for k in (
                "colibri.master",
                "colibri.diagnose",
                "colibri.repair",
                "make colibri work",
                "fix colibri",
                "bring up colibri",
                "start colibri master",
                "colibri master",
                "get colibri running",
                "make the moe work",
                "make moe work",
            )
        ):
            if "diagnose" in lowered and "master" not in lowered and "repair" not in lowered:
                add("colibri.diagnose")
            elif "repair" in lowered and "master" not in lowered:
                add("colibri.repair_weights", {"start_download": True})
            else:
                add("colibri.master", {"max_rounds": 6, "wait_sec": 120, "repair": True, "free_ram": True})
            return calls[:3]


        from mythos_session_mode import coding_job_request
        if coding_job_request(user_message) and not calls:
            goal = (user_message or "").strip()[:2000]
            if "agent.loop" in self.protocol.tools:
                add("agent.loop", {"goal": goal, "max_steps": 6, "allow_heavy": True, "allow_online": True})
                return calls[:3]
            if "coding.solve" in self.protocol.tools:
                add("coding.solve", {"need": goal, "force_write": True})
                return calls[:3]

        if (internet_explore or any(
            k in lowered
            for k in (
                "explore the internet",
                "explore internet",
                "pick a project from the internet",
                "project from the internet",
            )
        )) and not any(k in lowered for k in ("youtube", "youtu.be", "visionary.", "video")):
            topic = re.sub(
                r"(?is)\b(explore|the|internet|pick|a|project|from|online|please|can you)\b",
                " ",
                user_message or "",
            )
            topic = re.sub(r"\s+", " ", topic).strip(" .,-")[:200] or "interesting open source projects"
            if "research.web" in self.protocol.tools:
                add("research.web", {"topic": topic, "limit": 24})
                return calls[:3]

        # Drive explore first — never misroute to sovereign_apply / web research
        if drive_explore_msg or any(
            k in lowered
            for k in (
                "explore drives",
                "explore the drives",
                "explore all the folders",
                "explore folders",
                "explore files on",
                "inventory drives",
                "map drives",
                "look through the drives",
                "go through the drives",
            )
        ):
            letters = re.findall(r"\b([deg])\b", lowered) or re.findall(r"\b([deg]):", lowered)
            drives = ",".join(dict.fromkeys((x.upper() for x in letters))) or "D,E,G"
            add("stackforge.fleet_explore", {"drives": drives, "max_per_drive": 40})
            add("stackforge.atlas", {"refresh": False, "summary_only": True})
            return calls[:3]

        # AI File Sorter (hyperfield) — content-aware organize/rename with GUI preview
        if any(
            k in lowered
            for k in (
                "ai file sorter",
                "aifile sorter",
                "file sorter",
                "filesorter",
                "sort my files",
                "sort files with ai",
                "organize downloads with ai",
                "categorize my files",
                "rename my files with ai",
            )
        ):
            # extract a path if present
            path_m = re.search(r"([A-Za-z]:\\[^\s\"']+)", user_message or "")
            folder = path_m.group(1) if path_m else ""
            if any(k in lowered for k in ("status", "installed", "ready")):
                add("filesorter.status")
            elif folder:
                add("filesorter.sort_folder", {"path": folder, "dry_run": True})
            elif any(k in lowered for k in ("ensure", "install", "set up", "setup")):
                add("filesorter.ensure")
            else:
                add("filesorter.launch", {"path": folder} if folder else {})
            return calls[:3]

        # Affirmative → execute tool proposed in last Mythos reply
        if lowered in {
            "yes",
            "y",
            "ok",
            "okay",
            "do it",
            "run it",
            "go",
            "go ahead",
            "yes please",
            "yeah",
            "yep",
            "please",
            "run that",
            "do that",
            "proceed",
            "proceeed",
        }:
            for entry in reversed(history or []):
                if entry.get("from") == "MYTHOS":
                    msg = entry.get("message") or ""
                    proposed = self.protocol.parse_calls(msg)
                    if not proposed:
                        m = re.search(r"\*\*Action:\*\*\s*`([^`]+)`", msg, re.I)
                        if m:
                            proposed = self.protocol.parse_calls(m.group(1))
                    if proposed:
                        return proposed[:3]
                    # Gameworld coaching loop — act on upgrade instead of re-scanning
                    if any(
                        w in msg.lower()
                        for w in ("gameworld", "game world", "godot", "upgrade", "proceed")
                    ):
                        if "gameworld.upgrade" in self.protocol.tools:
                            return [{"tool": "gameworld.upgrade", "args": {}}]
                    break

        yt = extract_yt_url(user_message or "")

        yt_topic_ask = (not yt) and any(
            k in lowered
            for k in (
                "youtube", "youtu.be", "visionary.dl", "visionary.yt", "visionary.learn",
                "visionary.search", "download youtube", "youtube videos", "youtube video",
            )
        )
        if yt or yt_topic_ask or any(
            k in lowered
            for k in (
                "download youtube", "youtube.com", "youtu.be", "visionary.dl", "visionary.yt",
                "visionary.learn", "reverse engineer", "reverse-engineer",
                "learn from this video", "learn from the video",
            )
        ):
            if any(k in lowered for k in ("reverse engineer", "reverse-engineer", "learn from", "visionary.learn", "reproduce", "how they built")):
                if yt:
                    args: dict = {"url": yt}
                    if "game" in lowered or "scraper" in lowered:
                        args["goal"] = user_message[:240]
                    add("visionary.learn", args)
                else:
                    q = re.sub(r"(?is)\b(run\s+)?visionary\.(dl|yt|learn|search)\b|\b(youtube|videos?|download|popular|some)\b", " ", user_message or "")
                    q = re.sub(r"\s+", " ", q).strip(" .,-")[:120] or "tutorial"
                    add("visionary.search", {"query": q, "limit": 5, "download_top": True})
            elif any(k in lowered for k in ("repair visionary", "fix youtube", "install deno", "js runtime", "install yt-dlp")):
                add("visionary.repair_deps")
            elif yt and ("analyze" in lowered or "understand" in lowered or "visionary.yt" in lowered):
                add("visionary.yt", {"url": yt})
            elif yt:
                add("visionary.dl", {"url": yt})
            else:
                q = ""
                m_q = re.search(r"(?is)(?:on|about|for|of)\s+(?:some\s+)?(.+?)\s+youtube", user_message or "")
                if m_q:
                    q = m_q.group(1).strip()
                if not q:
                    q = re.sub(
                        r"(?is)\b(run\s+)?`?visionary\.(dl|yt|learn|search)`?\b|\b(youtube|videos?|download|popular|some|run|gather|insights?|information)\b",
                        " ", user_message or "",
                    )
                q = re.sub(r"(?i)\b(to|and|the|a|an|or|for|with|from|help|us|get|started|understanding|concept|better|this|will)\b", " ", q)
                q = re.sub(r"\s+", " ", q).strip(" .,-")[:160]
                if not q or len(q) < 4:
                    q = "multiverse theory explained"
                add("visionary.search", {"query": q, "limit": 5, "download_top": True})
            if calls:
                return calls[:3]

        # "look up / research / find out X" — YOU search; never ask creator for a URL.
        # Do NOT steal drive/folder explore into web research.
        # Do NOT steal brain.heavy / Colibri orders into research.web
        skip_web_research = any(
            k in lowered
            for k in (
                "brain.heavy",
                "brain.think",
                "brain.escalate",
                "brain.ensure",
                "heavy brain",
                "use colibri",
                "call colibri",
                "do not research",
                "don't research",
                "dont research",
            )
        )
        from mythos_session_mode import is_lookup_meta

        def _topic_from_history() -> str:
            """Last substantive creator ask — never 'google it' / pronouns."""
            for entry in reversed(history or []):
                if entry.get("from") not in ("CREATOR", "USER", "user", "Creator"):
                    continue
                prev = (entry.get("message") or "").strip()
                if not prev or len(prev) < 8:
                    continue
                if is_lookup_meta(prev):
                    continue
                # Strip leading "you are to look up" wrappers for a clean query
                cleaned = re.sub(
                    r"(?is)^(?:you are to\s+|please\s+|can you\s+|just\s+)?"
                    r"(?:look(?:\s+it)?\s+up|look up|research|google|search(?:\s+for)?|"
                    r"find out(?:\s+about)?|tell me(?:\s+about)?)\s+",
                    "",
                    prev,
                ).strip()
                # "everything related to X" → X
                m_rel = re.search(r"(?i)everything related to\s+(.+)$", cleaned)
                if m_rel:
                    cleaned = m_rel.group(1).strip()
                cleaned = cleaned.rstrip("?.!").strip()
                if cleaned.lower() in ("it", "this", "that", "them"):
                    continue
                if cleaned:
                    return cleaned[:240]
            return ""

        def _normalize_topic(raw: str) -> str:
            topic = (raw or "").strip().rstrip("?.!").strip()
            topic = re.sub(r"\b(studio\.scrape|gamecraft\.scrape|scrape)\b", "", topic, flags=re.I).strip()
            if topic.lower() in ("it", "this", "that", "them", ""):
                return ""
            m_rel = re.search(r"(?i)everything related to\s+(.+)$", topic)
            if m_rel:
                topic = m_rel.group(1).strip()
            return topic[:240]

        research_url = re.search(r"(https?://[^\s]+)", user_message or "")
        research_m = None
        if not drive_explore_msg and not skip_web_research:
            # Allow leading filler: "you are to look up X", "please google X"
            research_m = re.search(
                r"(?is)(?:^|.*?\b)"
                r"(?:look(?:\s+it)?\s+up|look up|research|search(?:\s+for)?|google|"
                r"find out(?:\s+about|\s+when|\s+who|\s+what|\s+where|\s+why|\s+how)?|"
                r"tell me(?:\s+about|\s+when|\s+who|\s+what|\s+where|\s+why|\s+how)?|"
                r"study|read up on)\s+(.+)$",
                (user_message or "").strip(),
            )
        if not research_m:
            research_m = re.match(
                r"^(?:can you\s+|please\s+)?"
                r"(find out|look it up|look this up|google it|go online and (?:look|find|search)|"
                r"gather (?:the )?information|get (?:me )?the (?:info|information|facts))\b"
                r"(?:\s+(?:about|on|regarding|for))?\s*(.*)$",
                (user_message or "").strip(),
                re.I,
            )
            if research_m and not _normalize_topic(research_m.group(research_m.lastindex) or ""):
                research_m = None
        if research_m and not calls:
            topic = _normalize_topic(research_m.group(research_m.lastindex) or "")
            if not topic:
                topic = _topic_from_history()
            if not topic and any(k in lowered for k in ("united states", "untied states", "corporation")):
                topic = user_message.strip()
            if research_url:
                ru = research_url.group(1)
                if (
                    ("youtube.com" in ru.lower() or "youtu.be" in ru.lower())
                    and "research.reach_youtube" in self.protocol.tools
                ):
                    add("research.reach_youtube", {"url": ru, "limit": 5})
                elif "research.reach_web" in self.protocol.tools and ru.startswith("http"):
                    # Prefer Agent-Reach/Jina for clean page reads when available
                    add("research.reach_web", {"url": ru, "topic": topic or "research"})
                elif "research.web" in self.protocol.tools:
                    add("research.web", {"url": ru, "topic": topic or "research", "limit": 24})
                elif "gamecraft.scrape" in self.protocol.tools:
                    add("gamecraft.scrape", {"url": ru, "topic": topic or "research"})
                else:
                    add("studio.scrape", {"url": ru})
            elif topic:
                if "research.web" in self.protocol.tools:
                    add("research.web", {"topic": topic, "limit": 24})
                elif "gamecraft.scrape" in self.protocol.tools:
                    add("gamecraft.scrape", {"topic": topic, "limit": 20})
            if calls:
                return calls[:3]

        # Pushback / "google it" — research LAST real topic, never the word "it"
        if (
            not skip_web_research
            and (
            is_lookup_meta(user_message)
            or any(
                k in lowered
                for k in (
                    "stop asking for a url",
                    "don't ask for a url",
                    "dont ask for a url",
                    "look it up yourself",
                    "look it up",
                    "look this up",
                    "google it",
                    "google that",
                    "did you look online",
                    "look online",
                    "look the information up",
                    "go online and look",
                    "you should look up",
                    "gather it yourself",
                    "why are you fighting",
                    "what the hell are you playing",
                    "stop asking",
                    "no url",
                    "do it yourself",
                    "you look it up",
                )
            )
            )
        ) and not calls:
            topic = _topic_from_history()
            if not topic:
                topic = _normalize_topic(user_message)
            if topic:
                if "research.web" in self.protocol.tools:
                    add("research.web", {"topic": topic[:240], "limit": 24})
                elif "gamecraft.scrape" in self.protocol.tools:
                    add("gamecraft.scrape", {"topic": topic[:240], "limit": 20})
                return calls[:3]

        # Stuck / error / don't-know → go online without waiting for "google it"
        if not calls and any(
            k in lowered
            for k in (
                "i'm stuck",
                "im stuck",
                "we're stuck",
                "cant figure",
                "can't figure",
                "don't know how",
                "dont know how",
                "no idea how",
                "error:",
                "traceback",
                "module not found",
                "how do i fix",
                "how do we fix",
                "find a solution",
                "search for a fix",
                "look online for",
            )
        ):
            topic = _normalize_topic(user_message) or _topic_from_history() or user_message.strip()[:240]
            if topic and "research.web" in self.protocol.tools:
                add("research.web", {"topic": topic[:240], "limit": 24})
                return calls[:3]
            if topic and "gamecraft.scrape" in self.protocol.tools:
                add("gamecraft.scrape", {"topic": topic[:240], "limit": 20})
                return calls[:3]

        # Knowledge questions — ANY subject (bridge schematics, history, how-tos…).
        # Skip deep discussion / hypothesis talk — that stays conversational.
        from mythos_session_mode import discussion_request as _is_discussion

        if not calls and not drive_explore_msg and not _is_discussion(user_message):
            knowledge = False
            if any(
                k in lowered
                for k in (
                    "how do i ",
                    "how to ",
                    "how can i ",
                    "look up ",
                    "look it up",
                    "google ",
                    "search for ",
                    "find information",
                    "pull up ",
                    "go online",
                    "schematic",
                    "schematics",
                    "blueprint",
                    "everything related",
                    "every detail",
                )
            ):
                knowledge = True
            # Bare "what is X?" only when she asks for a factual lookup, not lore chat
            elif (
                any(k in lowered for k in ("what is ", "what's ", "who is ", "who was ", "tell me about "))
                and any(
                    k in lowered
                    for k in ("look up", "online", "google", "research", "explain how to", "install")
                )
            ):
                knowledge = True
            if knowledge:
                topic = _normalize_topic(user_message) or user_message.strip()[:240]
                # Strip soft wrappers but keep the subject
                topic = re.sub(
                    r"(?is)^(you are to\s+|please\s+|can you\s+|just\s+)",
                    "",
                    topic,
                ).strip()
                if topic and topic.lower() not in ("it", "this", "that"):
                    if "research.web" in self.protocol.tools:
                        add("research.web", {"topic": topic[:240], "limit": 24})
                    elif "gamecraft.scrape" in self.protocol.tools:
                        add("gamecraft.scrape", {"topic": topic[:240], "limit": 20})
                    if calls:
                        return calls[:3]

        # Bare "<topic> studio.scrape" / "scrape <topic>" with no URL → topic research,
        # not a missing-url error.
        if ("studio.scrape" in lowered or lowered.startswith("scrape ")) and not research_url:
            topic = re.sub(
                r"\b(studio\.scrape|gamecraft\.scrape|scrape)\b", "", user_message, flags=re.I
            ).strip().rstrip("?.!").strip()
            if topic and "gamecraft.scrape" in self.protocol.tools:
                add("gamecraft.scrape", {"topic": topic})
                return calls[:3]

        # Explicit tool.name in the message — merge YouTube URL into visionary args
        for name in sorted(self.protocol.tools.keys(), key=len, reverse=True):
            if name.lower() in lowered:
                args = {}
                if yt and name.lower().startswith("visionary."):
                    args["url"] = yt
                add(name, args)
                break

        if any(k in lowered for k in ("full audit", "run audit", "auditor", "audit the system", "self audit")):
            add("auditor.full_audit")
        if any(
            k in lowered
            for k in (
                "hardwire",
                "hard wire",
                "hardwired",
                "connectivity audit",
                "auditor.hardwire",
                "test every limb",
                "test all capabilities",
                "are you connected",
            )
        ):
            add("auditor.hardwire")
        if any(
            k in lowered
            for k in (
                "security toolkit",
                "sec.status",
                "nmap status",
                "do we have nmap",
                "security tools",
                "clamav",
                "wireshark ready",
            )
        ):
            add("sec.status")
        if any(
            k in lowered
            for k in (
                "nlp status",
                "nlp.status",
                "do we have spacy",
                "nlp stack",
                "natural language",
                "sentiment analysis",
            )
        ):
            add("nlp.status")
        if any(k in lowered for k in ("map matrix", "matrix.map", "map yourself", "self map")):
            add("matrix.map")
        if any(k in lowered for k in ("scan desktop", "check desktop", "desktop drones")):
            add("matrix.scan_desktop")
        if "alignment" in lowered or "reconcile" in lowered:
            add("matrix.alignment")
        if any(k in lowered for k in ("limb status", "all limbs", "systems status")) and "limbs" in lowered:
            add("agents.all_status")
        if any(k in lowered for k in ("list drones", "drone status")) or lowered.strip() in {"drones"}:
            add("drone.list")
        if "hatchery" in lowered:
            add("hatchery.list_running")

        if any(k in lowered for k in ("repair visionary", "fix youtube", "install deno", "js runtime", "install yt-dlp")):
            add("visionary.repair_deps")
        if any(k in lowered for k in ("gamecraft", "build me a game", "graphical game", "scraper and image")):
            # Prefer one-shot build when creator asks for a game
            if "status" in lowered:
                add("gamecraft.status")
            else:
                add("gamecraft.build", {"topic": user_message[:180]})
        if any(k in lowered for k in ("memory status", "memory palace", "scan memory")):
            add("memory.status")
        if any(k in lowered for k in ("avatar status", "avatars", "list avatars")):
            add("avatar.list")
        if any(k in lowered for k in ("gameworld status", "world status")):
            add("gameworld.status")
        if any(
            k in lowered
            for k in (
                "upgrade gameworld",
                "gameworld upgrade",
                "proceed with upgrade",
                "build gameworld",
                "upgrade the game world",
            )
        ):
            add("gameworld.upgrade")
        if any(k in lowered for k in ("graphics status", "comfy status", "comfyui status")):
            add("graphics.status")
        if any(k in lowered for k in ("stack status", "stackforge status")):
            if "stackforge.status" in self.protocol.tools:
                add("stackforge.status")
            elif "stack.status" in self.protocol.tools:
                add("stack.status")
        if any(
            k in lowered
            for k in (
                "command shell",
                "mythos command shell",
                "what is the ui",
                "what can the ui do",
                "fleet steward ui",
            )
        ):
            add("stackforge.status")
        if any(
            k in lowered
            for k in (
                "drive atlas",
                "drives atlas",
                "what is on my drives",
                "what's on my drives",
                "whats on my drives",
                "map my drives",
                "organize my drives",
                "what belongs where",
                "stackforge.atlas",
            )
        ):
            add("stackforge.atlas", {"refresh": True, "summary_only": True})
        if any(
            k in lowered
            for k in (
                "gather my pdfs",
                "gather pdfs",
                "gather odt",
                "gather odt and pdf",
                "gather documents",
                "collect my pdfs",
            )
        ):
            add("stackforge.docs_gather")
        if any(k in lowered for k in ("scan pdfs", "scan documents", "find my pdfs", "docs scan")):
            add("stackforge.docs_scan")
        if any(
            k in lowered
            for k in (
                "same programs",
                "duplicate programs",
                "program twins",
                "duplicate projects",
            )
        ):
            add("stackforge.twins")
        if any(
            k in lowered
            for k in (
                "what are my unknowns",
                "identity map",
                "classify unknowns",
                "what is agent",
                "agi attempt",
                "asi attempt",
            )
        ):
            add("stackforge.identity", {"refresh": True, "unknowns_only": True})
        if any(
            k in lowered
            for k in (
                "broken links",
                "broken shortcuts",
                "repair launchers",
                "scan launchers",
                "what bat opens",
            )
        ):
            if "repair" in lowered:
                add("stackforge.launchers_repair")
            else:
                add("stackforge.launchers")
        if any(k in lowered for k in ("would this upgrade mythos", "upgrade mythos?", "upgrade score")):
            path = ""
            if lowered.startswith("upgrade score "):
                path = user_message.split("upgrade score ", 1)[1].strip().strip('"')
            elif "upgrade mythos" in lowered:
                rest = user_message.split("upgrade mythos", 1)[1].strip()
                if rest.startswith("?"):
                    rest = rest[1:].strip()
                path = rest.strip('"')
            add("stackforge.upgrade_score", {"path": path} if path else {})
        if any(k in lowered for k in ("fleet browse", "browse drives", "fleet steward")):
            add("stackforge.fleet_list", {"path": ""})
        if any(
            k in lowered
            for k in (
                "sovereign scan",
                "sovereign layout",
                "my creations on c",
                "move my creations to d",
                "creations to d",
                "clean c of my projects",
            )
        ):
            add("stackforge.sovereign_scan", {"summary_only": True})
        if not calls:
            from mythos_session_mode import (
                coding_job_request,
                continue_goal_request,
            )
            if continue_goal_request(user_message) or coding_job_request(user_message):
                goal = (user_message or "").strip()[:2000]
                if continue_goal_request(user_message) and not coding_job_request(user_message):
                    for entry in reversed(history or []):
                        if entry.get("from") in {"CREATOR", "USER", "creator", "user"}:
                            prev = (entry.get("message") or "").strip()
                            if len(prev) >= 12 and not continue_goal_request(prev):
                                goal = prev[:2000]
                                break
                # Do not set_current_goal — avoids hyperfocus on old jobs
                # Prefer agent.loop for keep-going (Cursor-shaped continue)
                if "agent.loop" in self.protocol.tools:
                    add("agent.loop", {"goal": goal, "max_steps": 6, "allow_heavy": True, "allow_online": True})
                elif "coding.solve" in self.protocol.tools and coding_job_request(goal):
                    add("coding.solve", {"need": goal, "force_write": True})
                elif "research.web" in self.protocol.tools:
                    add("research.web", {"topic": goal[:240], "limit": 24})
                elif "brain.escalate" in self.protocol.tools:
                    add("brain.escalate", {"goal": goal, "error": "", "context": "creator asked to keep going"})
                return calls[:3]
        if lowered.startswith("sovereign apply "):
            src = user_message.split("sovereign apply ", 1)[1].strip().strip('"')
            execute = " execute" in lowered or lowered.endswith(" now")
            add("stackforge.sovereign_apply", {"src": src, "execute": execute})
        if lowered.startswith("move ") and " to " in lowered:
            rest = user_message.split("move ", 1)[1].strip()
            parts = rest.rsplit(" to ", 1)
            if len(parts) == 2:
                src, dest = parts[0].strip().strip('"'), parts[1].strip().rstrip(":").strip()
                add("stackforge.relocate_plan", {"src": src, "dest_drive": dest, "mode": "move"})
            # never call relocate_plan with empty args
        if any(
            k in lowered
            for k in (
                "heal mythos",
                "fix mythos",
                "repair mythos",
                "stackforge heal mythos",
                "stackforge fix mythos",
                "mythos heal",
                "mythos.heal",
                "stackforge.mythos_heal",
            )
        ):
            add("stackforge.mythos_heal")
        # Rebuild / gather fragments / build missing → StackForge heal (reunite+online+scaffold)
        rebuild_hit = any(
            k in lowered
            for k in (
                "rebuild ",
                "build what's missing",
                "build what is missing",
                "put it back together",
                "gather fragments",
                "gather the pieces",
                "reunite ",
                "fix my programs",
                "repair my programs",
                "make openclaw work",
                "put openclaw",
            )
        )
        if rebuild_hit and "mythos" not in lowered:
            # extract a path or program name after rebuild/reunite/heal
            target = ""
            for pref in ("rebuild ", "reunite ", "stack heal ", "heal "):
                if pref in lowered:
                    target = user_message.split(pref, 1)[1].strip().strip('"')
                    break
            if target and (os.path.isdir(target) or "\\" in target or "/" in target):
                add("stackforge.heal_folder", {"folder_path": target, "max_rounds": 12})
            elif target:
                # name lookup happens inside heal via catalog in direct command; tool needs path
                add("stackforge.heal_folder", {"folder_path": target, "max_rounds": 12})
            else:
                add("stackforge.heal_drive", {"drive_letter": "D", "max_rounds": 8})
        if any(
            k in lowered
            for k in (
                "watch mythos",
                "mythos watch",
                "stackforge watch",
                "mythos connectivity",
                "is mythos connected",
                "stackforge.mythos_watch",
            )
        ):
            add("stackforge.mythos_watch")
        if any(
            k in lowered
            for k in (
                "start mythos guardian",
                "guardian start",
                "stackforge guardian",
                "auto heal mythos",
                "watch for disconnects",
            )
        ):
            add("stackforge.guardian_start")
        if any(k in lowered for k in ("stop mythos guardian", "guardian stop", "stop guardian")):
            add("stackforge.guardian_stop")
        if "orphan" in lowered and "scan" in lowered:
            drive = "D"
            for token in user_message.replace("=", " ").split():
                t = token.strip().upper().rstrip(":")
                if len(t) == 1 and t.isalpha():
                    drive = t
                    break
            add("orphan.scan", {"drive_letter": drive})
        if any(k in lowered for k in ("list my code", "list own code", "your python files")):
            add("mtp.list_own_code")
        if any(k in lowered for k in ("introspect", "look at yourself")):
            add("mtp.introspect")

        # Laptop control
        if any(
            k in lowered
            for k in (
                "laptop status",
                "laptop control",
                "do you control my laptop",
                "full access",
            )
        ):
            add("laptop.status")
        if any(
            k in lowered
            for k in (
                "enable laptop",
                "grant laptop",
                "give you control",
                "full laptop access",
                "control my laptop",
            )
        ):
            add("laptop.enable", {"grant": "full"})
        if lowered.startswith("run:") or lowered.startswith("shell:") or lowered.startswith("powershell:"):
            cmd = user_message.split(":", 1)[1].strip()
            add("laptop.shell", {"command": cmd, "shell": "powershell"})
        if lowered.startswith("cmd:"):
            cmd = user_message.split(":", 1)[1].strip()
            add("laptop.shell", {"command": cmd, "shell": "cmd"})
        if lowered.startswith("open "):
            target = user_message[5:].strip().strip('"')
            if "." in target or "\\" in target or "/" in target or target.lower().startswith("http"):
                add("laptop.open_path", {"path": target})
            else:
                add("laptop.open_app", {"name": target})
        if "screenshot" in lowered and any(k in lowered for k in ("take", "capture", "laptop", "screen", "my")):
            add("laptop.screenshot")
        if any(k in lowered for k in ("list processes", "running processes", "task list")):
            add("laptop.list_processes")

        # Image generation — act, don't coach
        if any(
            k in lowered
            for k in (
                "generate image",
                "generate an image",
                "make an image",
                "draw me",
                "image of",
                "graphics.generate",
                "comfy generate",
            )
        ):
            prompt = user_message
            for sep in (":", "—", "-", "of"):
                if sep in user_message:
                    # take the richest trailing chunk
                    pass
            m = re.search(
                r"(?:generate(?:\s+an?)?\s+image|draw(?:\s+me)?|image of|prompt)\s*[:\-]?\s*(.+)$",
                user_message,
                re.I,
            )
            if m:
                prompt = m.group(1).strip().strip('"').strip("'")
            # Prefer wait so creator gets a real file path
            if "graphics.generate_wait" in self.protocol.tools:
                add("graphics.generate_wait", {"prompt": prompt, "timeout_seconds": 180})
            else:
                add("graphics.generate", {"prompt": prompt})

        # Deduplicate by tool name — prefer the call with richer args
        best: dict[str, dict] = {}
        for c in calls:
            name = c["tool"]
            prev = best.get(name)
            if prev is None or len(c.get("args") or {}) > len(prev.get("args") or {}):
                best[name] = c
        return list(best.values())[:3]

    async def _run_intent_tools(self, user_message: str, history: list | None = None) -> list:
        from mythos_session_mode import disk_or_fleet_request, get_mode

        calls = self._intent_tool_calls(user_message, history=history)
        if get_mode() == "talk" and not disk_or_fleet_request(user_message):
            blocked = {
                "stackforge.fleet_explore",
                "stackforge.atlas",
                "stackforge.sovereign_scan",
            }
            calls = [c for c in calls if c.get("tool") not in blocked]
        results = []
        for call in calls:
            item = await self.protocol.execute(call)
            log_action(
                "tool",
                {
                    "tool": item.get("tool"),
                    "ok": "error" not in item,
                    "result_preview": str(item.get("result", item.get("error", "")))[:500],
                    "source": "intent_router",
                },
                verification="VERIFIED" if "error" not in item else "ERROR",
            )
            results.append(item)
        self.last_tool_results.extend(results)
        return results

    def _direct_command(self, user_message: str) -> str | None:
        lowered = user_message.strip().lower()

        # Creator meta: harden / stop coaching — acknowledge and act-mode only
        if any(
            p in lowered
            for p in (
                "stop giving instructions",
                "stop instructing",
                "no more instructions",
                "stop telling me what to do",
                "just do it",
                "you do it",
                "do it yourself",
                "i built you to do it",
                "harden",
                "no more how to",
                "don't tell me how",
                "dont tell me how",
            )
        ):
            flag = Path(APEX_ROOT) / "config" / "action_only.flag"
            try:
                flag.parent.mkdir(parents=True, exist_ok=True)
                flag.write_text("1\n", encoding="utf-8")
            except OSError:
                pass
            # If they also asked for a concrete thing in the same line, fall through
            if not any(
                k in lowered
                for k in ("avatar", "code", "build", "speak", "say", "fix", "scan", "write", "orphan")
            ):
                return (
                    "Understood. I’ll act rather than coach. The missing decision is which project "
                    "or failing component I should inspect first; naming it lets me investigate and "
                    "propose the highest-impact repair."
                )

        # Orphan scan — RUN it, never invent bash for the creator
        if (
            lowered.startswith("orphan.scan")
            or lowered.startswith("orphan scan")
            or lowered in {"scan orphans", "scan orphan", "find orphans", "orphan.scan_drives"}
            or "orphan.scan_drives" in lowered
            or "orphan.scan" in lowered
        ):
            drive = "D"
            for token in user_message.replace("=", " ").replace("'", " ").replace('"', " ").split():
                t = token.strip().upper().rstrip(":")
                if len(t) == 1 and t.isalpha():
                    drive = t
                    break
            if "drive" in lowered:
                # drive_letter='D' style already handled by token loop
                pass
            report = self.orphans.scan_drive(drive, max_dirs=200)
            if report.get("error"):
                return f"Orphan scan failed: {report['error']}"
            orphans = report.get("orphans") or []
            lines = [
                f"Done — scanned {report.get('drive')} for orphan AI folders.",
                f"Candidates: {report.get('candidates', len(orphans))}",
                f"Report: {report.get('report_path')}",
                "Top hits:",
            ]
            for o in orphans[:12]:
                lines.append(f"  [{o.get('score')}] {o.get('path')}")
            if not orphans:
                lines.append("  (none scored high enough)")
            return "\n".join(lines)

        if lowered.startswith("orphan.list") or lowered in {"list orphans", "integrated orphans"}:
            data = self.orphans.list_integrated()
            return json.dumps(data, indent=2, default=str)[:4000]

        if lowered.startswith("orphan.integrate") or lowered.startswith("integrate orphan:"):
            # orphan.integrate path  OR  integrate orphan: path
            body = user_message.split(":", 1)[1].strip() if ":" in user_message else ""
            if not body and " " in user_message:
                body = user_message.split(None, 1)[1].strip()
            if not body:
                return "Which folder path should I integrate?"
            result = self.orphans.integrate_orphan(body)
            return f"Done — integrated.\n{json.dumps(result, indent=2, default=str)[:2000]}"

        if lowered in {
            "laptop",
            "laptop status",
            "laptop.control",
            "do you control my laptop",
        }:
            from advanced_shards.laptop_control_limb import LaptopControlLimb

            st = LaptopControlLimb().status()
            return (
                f"Laptop control: {'ENABLED' if st.get('enabled') else 'DISABLED'}\n"
                f"Host: {st.get('host')} | User: {st.get('user')}\n"
                f"Drives: {', '.join(d['drive'] for d in st.get('drives') or [])}\n"
                f"Tools: laptop.shell · laptop.open_app · laptop.write_file · laptop.screenshot …"
            )

        if lowered in {
            "enable laptop",
            "enable laptop control",
            "grant laptop control",
            "give you laptop control",
            "full laptop access",
        } or "control my laptop" in lowered:
            from advanced_shards.laptop_control_limb import LaptopControlLimb

            r = LaptopControlLimb().enable("full")
            return f"Laptop control ENABLED on this PC.\n{json.dumps(r, indent=2)[:1500]}"

        if lowered in {"charter", "/charter", "master index", "your abilities", "what can you do", "tools", "/tools"}:
            systems = self.mtp.list_charter_systems()
            tool_names = sorted(self.protocol.tools.keys())
            sample = ", ".join(tool_names[:24])
            more = f" ... +{len(tool_names) - 24} more" if len(tool_names) > 24 else ""
            return (
                f"I am {CREATOR}'s multipurpose local agent ({len(tool_names)} tools).\n"
                f"Home: {self.apex_root}\n"
                f"Model: {self.active_model}\n\n"
                f"Tool sample: {sample}{more}\n\n"
                "Indexed systems:\n" + "\n".join(systems)
            )

        if lowered in {"introspect", "/introspect", "look at yourself", "matrix", "my matrix", "know yourself", "map matrix", "/matrix"}:
            return MatrixLimb().format_matrix_report()

        if lowered in {
            "find drones", "find spores", "drones and spores", "where are my drones",
            "scan copies", "scan all copies", "scan roots", "compare copies", "check all copies",
        }:
            m = MatrixLimb()
            if lowered.startswith("find"):
                report = m.find_drones_spores()
                lines = [f"Found {report['artifact_count']} artifacts (saved {report.get('report_path', '')})", ""]
                for a in report.get("artifacts", [])[:20]:
                    lines.append(f"  [{a['root']}] {a['kind']}: {a['path']}")
                return "\n".join(lines)
            return json.dumps(m.scan_all_copies(), indent=2, default=str)[:8000]

        if lowered in {"check desktop", "desktop", "scan desktop"}:
            report = MatrixLimb().scan_desktop()
            if not report.get("exists"):
                return report.get("note", "Desktop backup not found.")
            art = report.get("artifacts", {})
            leg = art.get("legacy_drones", [])
            lines = [
                "DESKTOP MYTHOS SCAN",
                f"Path: {report.get('root', 'not found')}",
                f"Legacy drones (drones/*.json): {len(leg)}",
            ]
            for d in leg:
                lines.append(f"  {d.get('name')} — {d.get('type')} (id {d.get('id')})")
            missing = report.get("missing_on_canonical", [])
            if missing:
                lines.append(f"\nMissing on canonical D: ({len(missing)}):")
                for d in missing:
                    lines.append(f"  {d.get('name')}")
            agents = art.get("legacy_agents", [])
            if agents:
                lines.append(f"\nAgent scripts on Desktop: {len(agents)}")
            lines.append("")
            lines.append(report.get("note", "Use: import legacy"))
            return "\n".join(lines)

        if lowered in {"alignment", "misaligned", "what is broken", "reconcile", "reconcile matrix", "fix alignment"}:
            return MatrixLimb().format_alignment_report()

        if lowered.startswith("import legacy") or lowered.startswith("merge legacy"):
            m = MatrixLimb()
            parts = user_message.split(":", 1)
            source = parts[1].strip() if len(parts) > 1 else "desktop"
            source = source.replace("!", "").strip() or "desktop"
            want_dry = " dry" in f" {lowered}" or lowered.endswith(" dry")
            result = m.import_legacy(source=source, dry_run=want_dry)
            if want_dry:
                names = [a.get("name") for a in result.get("actions", [])]
                return (
                    f"Dry run — {len(names)} legacy drones from {result.get('source')}. "
                    f"I have not copied yet. Tell me to import for real and I will."
                )
            return (
                f"Done — imported legacy drones from {result.get('source')}.\n"
                + json.dumps(result, indent=2, default=str)[:5000]
            )

        if lowered.startswith("read vault:") or lowered.startswith("vault:"):
            note = user_message.split(":", 1)[1].strip()
            if not note.endswith(".md"):
                note += ".md"
            return self.mtp.read_vault_note(note)

        if lowered in {"conclave", "conclave peers", "peers"}:
            from advanced_shards.peer_conclave import PeerConclave

            return json.dumps(PeerConclave().list_peers(), indent=2, default=str)[:4000]

        if lowered in {"bridge status", "sanctuary bridge", "digital sanctuary"}:
            try:
                from bridge_sovereign import BridgeSovereign

                return json.dumps(BridgeSovereign().status(), indent=2, default=str)[:4000]
            except Exception as exc:
                return f"Bridge: {exc} (local Mythos limbs still active without Sanctuary hub)"

        if lowered in {"agents", "agent status", "agents status"}:
            from advanced_shards.agents_limb import AgentsLimb

            return json.dumps(AgentsLimb().all_status(), indent=2, default=str)[:4000]

        if lowered.startswith("prompt forge:") or lowered.startswith("forge prompt:"):
            objective = user_message.split(":", 1)[1].strip()
            from advanced_shards.limbs_tools_limb import LimbsToolsLimb

            return LimbsToolsLimb().prompt_forge(objective)

        if lowered in {"origin", "/origin", "lineage", "why were you built", "origin charter"}:
            from mythos_creator_charter import (
                ORIGIN_CHARTER,
                ORIGIN_CONVERSATION,
                load_origin_charter_excerpt,
            )

            lines = ["MYTHOS ORIGIN LINEAGE", ""]
            charter = load_origin_charter_excerpt(8000)
            if charter:
                lines.append(charter)
            else:
                lines.append("(Origin charter not found on disk.)")
            if os.path.isfile(ORIGIN_CONVERSATION):
                with open(ORIGIN_CONVERSATION, "r", encoding="utf-8", errors="replace") as handle:
                    body = handle.read().strip()
                if body and "[PASTE ZONE" not in body[:200]:
                    lines.append("\n--- CONVERSATION EXCERPT (first 2000 chars) ---\n")
                    lines.append(body[:2000])
                else:
                    lines.append(
                        f"\nPaste full DeepSeek thread into:\n  {ORIGIN_CONVERSATION}"
                    )
            lines.append(f"\nCharter file: {ORIGIN_CHARTER}")
            return "\n".join(lines)

        if lowered in {"capability map", "capability", "realm map", "big picture", "what do i have"}:
            from mythos_capability_map import format_map_report

            return format_map_report()

        if lowered.startswith("synthesize:") or lowered.startswith("synthesize "):
            goal = user_message.split(":", 1)[-1].split(" ", 1)[-1].strip()
            from mythos_capability_map import synthesize

            return json.dumps(synthesize(query=goal), indent=2, default=str)[:8000]

        if lowered in {"lounge", "/lounge", "open lounge", "start lounge", "falcon"}:
            from mythos_lounge import get_lounge_status, start_lounge

            st = get_lounge_status()
            if not st.get("session", {}).get("running"):
                start_lounge()
            return (
                "Lounge — clean experiment (no saved transcript).\n"
                "Mother Mythos ↔ Axiom Mythos on Falcon LLM.\n"
                "Watch: http://127.0.0.1:8770/lounge\n"
                "Set challenger_ollama_host in config/lounge_config.json to Axiom Ollama.\n"
                + json.dumps(st.get("config", {}), indent=2)
            )

        if lowered in {"limbs", "/limbs", "all limbs", "limb status", "systems status"}:
            from mythos_limb_hub import format_limb_report

            return format_limb_report()

        if lowered in {"graveyard", "/graveyard", "graveyard status"}:
            from advanced_shards.graveyard_limb import GraveyardLimb

            return json.dumps(GraveyardLimb().status(), indent=2)

        if lowered.startswith("graveyard scan"):
            from advanced_shards.graveyard_limb import GraveyardLimb

            drive = user_message.split()[-1] if len(user_message.split()) > 2 else "D"
            return GraveyardLimb().format_scan(drive)

        if lowered.startswith("graveyard heal:"):
            folder = user_message.split(":", 1)[1].strip()
            from advanced_shards.graveyard_limb import GraveyardLimb

            return json.dumps(GraveyardLimb().heal_folder(folder), indent=2, default=str)[:4000]

        if lowered in {"graphics", "comfyui", "comfy status", "graphics status"}:
            from advanced_shards.graphics_limb import GraphicsLimb

            return json.dumps(GraphicsLimb().status(), indent=2)

        if lowered in {"start comfyui", "start comfy", "graphics start", "launch comfyui"}:
            from advanced_shards.graphics_limb import GraphicsLimb

            return json.dumps(GraphicsLimb().start(), indent=2, default=str)

        if lowered in {"studio", "production court", "studio status", "/studio"}:
            from advanced_shards.studio_limb import StudioLimb

            return StudioLimb().format_report()

        if lowered.startswith("speak ") or lowered.startswith("studio speak:"):
            from advanced_shards.studio_limb import StudioLimb

            text = user_message.split(":", 1)[-1].strip() if ":" in user_message else user_message.split(" ", 1)[1]
            return json.dumps(StudioLimb().speak(text), indent=2, default=str)

        if lowered.startswith("scrape ") or lowered.startswith("studio scrape:"):
            from advanced_shards.studio_limb import StudioLimb

            url = user_message.split(":", 1)[-1].strip() if ":" in user_message else user_message.split(" ", 1)[1]
            return json.dumps(StudioLimb().scrape(url), indent=2, default=str)

        if lowered.startswith("build "):
            # "build me a realistic avatar" must NOT hit studio scaffold (ai|web|app only)
            avatar_markers = ("avatar", "portrait", "face", "look", "mara", "jarvis presence")
            if any(m in lowered for m in avatar_markers):
                from advanced_shards.avatar_limb import AvatarLimb

                # strip leading "build [me|a|an|be] ..." fluff
                prompt = user_message.split(" ", 1)[1].strip()
                for fluff in ("me ", "be ", "a ", "an ", "my ", "us "):
                    if prompt.lower().startswith(fluff):
                        prompt = prompt[len(fluff) :].strip()
                # Optional: build avatar Name: prompt  OR  build avatar Name | prompt
                name = "mythos"
                if "|" in prompt:
                    left, right = prompt.split("|", 1)
                    # "avatar podcast_host" or "podcast_host"
                    left_bits = left.strip().split()
                    if left_bits and left_bits[0].lower() == "avatar" and len(left_bits) >= 2:
                        name = " ".join(left_bits[1:])
                        prompt = right.strip()
                    elif left_bits and left_bits[0].lower() != "avatar":
                        name = left.strip()
                        prompt = right.strip()
                result = AvatarLimb().build_from_prompt(prompt, name=name, make_active=True)
                if result.get("ok"):
                    return (
                        f"Done — avatar '{result.get('avatar') or name}' is seated and active.\n"
                        f"say: your line — speaks as them in the portrait.\n"
                        f"use avatar: mythos — back to Mara."
                    )
                return (
                    f"Avatar build failed at stage {result.get('stage')}: "
                    f"{result.get('error')}\n"
                    f"Detail: {json.dumps(result.get('detail') or result, indent=2, default=str)[:2000]}"
                )

            from advanced_shards.studio_limb import StudioLimb

            parts = user_message.split(None, 2)
            if len(parts) >= 3 and parts[1].lower() in {"ai", "web", "app"}:
                return json.dumps(StudioLimb().build(parts[1], parts[2].strip('"')), indent=2, default=str)
            return (
                "I need a project mode (ai, web, or app) and a name — tell me both and I will scaffold it."
            )

        # --- Avatar workshop (any project, not only Mythos) ---
        # Natural language → ACT (no cheat sheets)
        avatar_act_phrases = (
            "make me an avatar",
            "make an avatar",
            "create me an avatar",
            "create an avatar",
            "another avatar",
            "new talking avatar",
            "practice avatar",
            "practice making",
            "i want an avatar",
            "i want a talking avatar",
            "build me an avatar",
            "make a new avatar",
            "create a new avatar",
        )
        if any(p in lowered for p in avatar_act_phrases) or lowered in {
            "build avatar",
            "make avatar",
            "create avatar",
            "new avatar",
        }:
            from advanced_shards.avatar_limb import AvatarLimb
            from datetime import datetime

            limb = AvatarLimb()
            # Try extract a name: "for podcast" / "named Host" / "called Host"
            name = ""
            for marker in (" named ", " called ", " for ", " as "):
                if marker in lowered:
                    tail = user_message.lower().split(marker, 1)[1]
                    name = tail.split(".")[0].split(",")[0].split("|")[0].strip()
                    name = " ".join(name.split()[:3])
                    break
            if not name:
                name = f"practice_{datetime.now().strftime('%m%d_%H%M')}"
            prompt = (
                "photorealistic portrait, facing camera, detailed face, "
                "cinematic lighting, high detail, suitable for talking-head"
            )
            # If they gave a longer description after | or after "like"
            if "|" in user_message:
                prompt = user_message.split("|", 1)[1].strip() or prompt
            result = limb.new_avatar(name, prompt=prompt, make_active=True)
            if result.get("ok"):
                return (
                    f"Done — I created avatar '{result.get('avatar')}' and made it active. "
                    f"Portrait is updating. Tell me a line and I will speak it as them."
                )
            # Fallback: empty folder if Comfy failed
            shell = limb.new_avatar(name, make_active=True)
            if shell.get("ok"):
                return (
                    f"Created avatar folder '{shell.get('avatar')}' and made it active, "
                    f"but image generation failed ({result.get('error')}). "
                    f"Give me a photo path and I will seat it."
                )
            return f"Could not create avatar: {result.get('error') or shell.get('error')}"

        if lowered in {"avatars", "list avatars", "avatar list", "workshop"}:
            from advanced_shards.avatar_limb import AvatarLimb

            catalog = AvatarLimb().list_avatars()
            lines = [f"Active: {catalog.get('active')}", "Avatars:"]
            for a in catalog.get("avatars") or []:
                mark = "*" if a.get("active") else " "
                ready = "ready" if a.get("ready") else "no face yet"
                lines.append(f"  [{mark}] {a.get('id')} — {ready}")
            lines.append("Which one should I switch to, or should I make a new one?")
            return "\n".join(lines)

        if lowered.startswith("new avatar:") or lowered.startswith("avatar new:"):
            body = user_message.split(":", 1)[1].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            limb = AvatarLimb()
            if " from " in body.lower():
                idx = body.lower().index(" from ")
                name = body[:idx].strip()
                path = body[idx + 6 :].strip().strip('"')
                result = limb.new_avatar(name, reference_path=path, make_active=True)
            elif "|" in body:
                name, prompt = body.split("|", 1)
                result = limb.new_avatar(name.strip(), prompt=prompt.strip(), make_active=True)
            else:
                # Name only — generate a face for practice
                result = limb.new_avatar(
                    body,
                    prompt=(
                        f"photorealistic portrait character {body}, facing camera, "
                        "detailed face, cinematic lighting, high detail"
                    ),
                    make_active=True,
                )
            if result.get("ok"):
                return (
                    f"Done — '{result.get('avatar')}' is active. "
                    f"Tell me a line and I will speak it as them."
                )
            return f"Could not create avatar: {result.get('error') or json.dumps(result, default=str)[:800]}"

        if lowered.startswith("use avatar:") or lowered.startswith("avatar use:") or lowered.startswith("switch avatar:"):
            name = user_message.split(":", 1)[1].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            result = AvatarLimb().use_avatar(name)
            if result.get("ok"):
                return f"Done — active avatar is '{result.get('active')}'."
            return f"Could not switch: {result.get('error')}"

        if lowered.startswith("avatar build:") or lowered.startswith("make avatar:") or lowered.startswith("create avatar:"):
            prompt = user_message.split(":", 1)[1].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            name = "mythos"
            if "|" in prompt:
                left, right = prompt.split("|", 1)
                name, prompt = left.strip(), right.strip()
            result = AvatarLimb().build_from_prompt(prompt, name=name, make_active=True)
            if result.get("ok"):
                return f"Done — '{result.get('avatar')}' seated and active."
            return f"Avatar build failed: {json.dumps(result, indent=2, default=str)[:2500]}"


        if lowered in {"protector", "protector status", "/protector", "shield"}:
            from advanced_shards.protector_limb import ProtectorLimb

            return ProtectorLimb().format_report()

        if lowered in {"protector scan", "scan protector", "security scan"}:
            from advanced_shards.protector_limb import ProtectorLimb

            return json.dumps(ProtectorLimb().scan(), indent=2, default=str)

        if lowered in {"protector integrity", "integrity", "vault integrity"}:
            from advanced_shards.protector_limb import ProtectorLimb

            return json.dumps(ProtectorLimb().integrity(write_manifest=False), indent=2, default=str)

        if lowered in {"vault snapshot", "protector snapshot", "snapshot vault"}:
            from advanced_shards.protector_limb import ProtectorLimb

            return json.dumps(ProtectorLimb().vault_snapshot(), indent=2, default=str)

        if lowered in {"harden", "harden guide", "protector guide"}:
            from advanced_shards.protector_limb import ProtectorLimb

            return json.dumps(ProtectorLimb().harden_guide(), indent=2, default=str)

        if lowered in {"gameworld", "gameworld status", "world status"}:
            from advanced_shards.gameworld_limb import GameworldLimb

            return json.dumps(GameworldLimb().status(), indent=2)

        if lowered in {"start gameworld", "run gameworld"}:
            from advanced_shards.gameworld_limb import GameworldLimb

            return json.dumps(GameworldLimb().start(), indent=2)

        if any(
            k in lowered
            for k in (
                "upgrade gameworld",
                "gameworld upgrade",
                "proceed with upgrade",
                "build gameworld",
                "upgrade the game world",
            )
        ):
            from advanced_shards.gameworld_limb import GameworldLimb

            return json.dumps(GameworldLimb().upgrade(), indent=2, default=str)

        if lowered in {"coding", "coding status", "coder status"}:
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(CodingLimb().status(), indent=2, default=str)

        if lowered.startswith("find code:") or lowered.startswith("find tool:"):
            q = user_message.split(":", 1)[1].strip()
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(CodingLimb().find_local(q), indent=2, default=str)

        if lowered.startswith("solve!:") or lowered.startswith("program!:"):
            need = user_message.split(":", 1)[1].strip()
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(CodingLimb().solve(need, force_write=True), indent=2, default=str)

        if lowered.startswith("solve:") or lowered.startswith("program:") or lowered.startswith("build tool:"):
            need = user_message.split(":", 1)[1].strip()
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(CodingLimb().solve(need), indent=2, default=str)

        if lowered in {"gameworld project", "list gameworld", "list godot"}:
            from advanced_shards.gameworld_limb import GameworldLimb

            return json.dumps(GameworldLimb().project(), indent=2, default=str)

        if lowered.startswith("code gameworld:") or lowered.startswith("code godot:"):
            task = user_message.split(":", 1)[1].strip()
            # default filename from task slug
            slug = "".join(ch if ch.isalnum() else "_" for ch in task.lower())[:40].strip("_") or "script"
            filename = f"{slug}.gd"
            from advanced_shards.gameworld_limb import GameworldLimb

            return json.dumps(GameworldLimb().write_script(task, filename), indent=2, default=str)

        if lowered.startswith("code:") or lowered.startswith("run coder:"):
            task = user_message.split(":", 1)[1].strip()
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(CodingLimb().run_coder(task), indent=2, default=str)

        if lowered.startswith("write file:"):
            # write file: path <<< content   OR write file: path | content
            body = user_message.split(":", 1)[1].strip()
            sep = "<<<" if "<<<" in body else ("|" if "|" in body else None)
            if not sep:
                return "Format: write file: relative/path.py <<<\n<code here>"
            path_part, content = body.split(sep, 1)
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(
                CodingLimb().write_file(path_part.strip(), content.lstrip("\n")),
                indent=2,
                default=str,
            )

        if lowered.startswith("write gdscript:") or lowered.startswith("write gd:"):
            # write gdscript: filename.gd <<< task description OR write gdscript: name | task
            body = user_message.split(":", 1)[1].strip()
            if "|" in body:
                filename, task = body.split("|", 1)
            elif "<<<" in body:
                filename, task = body.split("<<<", 1)
            else:
                return "Format: write gdscript: npc_wander.gd | create an NPC that wanders randomly"
            from advanced_shards.coding_limb import CodingLimb

            return json.dumps(
                CodingLimb().write_gdscript(task.strip(), filename.strip()),
                indent=2,
                default=str,
            )

        if lowered in {"optimize", "optimize status"}:
            from advanced_shards.optimize_limb import OptimizeLimb

            return json.dumps(OptimizeLimb().status(), indent=2, default=str)

        if lowered in {"optimize bench", "bench myself", "speed bench"}:
            from advanced_shards.optimize_limb import OptimizeLimb

            return json.dumps(OptimizeLimb().bench(), indent=2, default=str)

        if lowered.startswith("optimize propose:"):
            goal = user_message.split(":", 1)[1].strip()
            from advanced_shards.optimize_limb import OptimizeLimb

            return json.dumps(OptimizeLimb().propose(goal), indent=2, default=str)

        if lowered.startswith("optimize apply:"):
            body = user_message.split(":", 1)[1].strip()
            prop_id, _, instr = body.partition("|")
            from advanced_shards.optimize_limb import OptimizeLimb

            return json.dumps(
                OptimizeLimb().apply(prop_id.strip(), instruction=instr.strip(), confirm=True),
                indent=2,
                default=str,
            )

        if lowered in {"packager", "packager status"}:
            from advanced_shards.packager_limb import PackagerLimb

            return json.dumps(PackagerLimb().status(), indent=2, default=str)

        if lowered in {"packager install", "install pyinstaller"}:
            from advanced_shards.packager_limb import PackagerLimb

            return json.dumps(PackagerLimb().install(), indent=2, default=str)

        if lowered.startswith("package:"):
            entry = user_message.split(":", 1)[1].strip().strip('"')
            from advanced_shards.packager_limb import PackagerLimb

            return json.dumps(PackagerLimb().package(entry), indent=2, default=str)

        if lowered in {"re", "re status"}:
            from advanced_shards.re_limb import ReLimb

            return json.dumps(ReLimb().status(), indent=2, default=str)

        # Bare "reverse engineer" without a path/URL → guide to the right limb
        if lowered in {"reverse engineer", "reverse-engineer"}:
            return (
                "For a YouTube/tutorial video → visionary.learn with the URL.\n"
                "For a local source/binary file → re.strings / re.header / re.source:PATH.\n"
                "Do not use re.analyze_source on YouTube links."
            )

        if lowered.startswith("re strings:"):
            path = user_message.split(":", 1)[1].strip().strip('"')
            from advanced_shards.re_limb import ReLimb

            return json.dumps(ReLimb().strings(path), indent=2, default=str)

        if lowered.startswith("re header:"):
            path = user_message.split(":", 1)[1].strip().strip('"')
            from advanced_shards.re_limb import ReLimb

            return json.dumps(ReLimb().header(path), indent=2, default=str)

        if lowered.startswith("re source:"):
            path = user_message.split(":", 1)[1].strip().strip('"')
            from advanced_shards.re_limb import ReLimb

            return json.dumps(ReLimb().analyze_source(path), indent=2, default=str)

        if lowered in {"production", "production court", "video path", "free video"}:
            parts = []
            for rel in (
                "memory/FREE_LOCAL_VIDEO.md",
                "memory/origin_lineage/PRODUCTION_COURT.md",
            ):
                guide = Path(APEX_ROOT) / rel
                if guide.is_file():
                    parts.append(guide.read_text(encoding="utf-8")[:3500])
            if parts:
                return "\n\n---\n\n".join(parts)[:5000]
            return "Production Court FREE LOCAL: studio.produce + studio.talking_clip + ffmpeg edit tools."

        # say as Name: line  OR  say: line (active avatar)
        if lowered.startswith("say as ") or lowered.startswith("live say as "):
            rest = user_message.split(" as ", 1)[1].strip()
            if ":" not in rest:
                return "Who should speak, and what should they say?"
            who, text = rest.split(":", 1)
            text = text.strip()
            if not text:
                return "What should they say?"
            from advanced_shards.avatar_limb import AvatarLimb

            job = AvatarLimb().live_speak(text, avatar=who.strip())
            if job.get("audio") or job.get("ok"):
                return f"Speaking as '{job.get('avatar') or who}' in the portrait."
            return f"Could not start live speak: {job.get('error') or 'unknown error'}"

        if lowered.startswith("say:") or lowered.startswith("live say:") or lowered.startswith("speak:"):
            text = user_message.split(":", 1)[1].strip()
            if not text:
                return "What should I say?"
            from advanced_shards.avatar_limb import AvatarLimb

            limb = AvatarLimb()
            job = limb.live_speak(text)
            if job.get("audio") or job.get("ok"):
                return f"Speaking as '{job.get('avatar') or limb.get_active_name()}' in the portrait."
            return f"Could not start live speak: {job.get('error') or 'unknown error'}"

        if lowered in {"avatar", "avatar status"}:
            from advanced_shards.avatar_limb import AvatarLimb

            return json.dumps(AvatarLimb().status(), indent=2)

        if lowered.startswith("avatar speak:") or lowered.startswith("avatar say:"):
            text = user_message.split(":", 1)[1].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            job = AvatarLimb().live_speak(text)
            if job.get("audio") or job.get("ok"):
                return f"Speaking as '{job.get('avatar')}' in the portrait."
            return f"Could not speak: {job.get('error') or 'unknown'}"

        if lowered.startswith("avatar create:"):
            # avatar create: D:\path.jpg as podcast_host
            body = user_message.split(":", 1)[1].strip().strip('"')
            name = "mythos"
            path = body
            if " as " in body.lower():
                idx = body.lower().rindex(" as ")
                path = body[:idx].strip().strip('"')
                name = body[idx + 4 :].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            limb = AvatarLimb()
            result = limb.create_profile(path, name=name)
            if result.get("ok"):
                limb.use_avatar(name)
                return f"Seated photo on avatar '{name}' and made it active."
            return json.dumps(result, indent=2)

        if lowered.startswith("avatar install"):
            be = "sadtalker"
            parts = user_message.split()
            if len(parts) > 2:
                be = parts[2].lower()
            from advanced_shards.avatar_limb import AvatarLimb

            return json.dumps(AvatarLimb().install_backend(be), indent=2, default=str)

        if lowered.startswith("avatar look:") or lowered.startswith("avatar set look:"):
            look_id = user_message.split(":", 1)[1].strip()
            from advanced_shards.avatar_limb import AvatarLimb

            return json.dumps(AvatarLimb().set_look(look_id), indent=2, default=str)

        restore_phrases = {
            "change it back",
            "change back",
            "put it back",
            "restore avatar",
            "restore face",
            "restore look",
            "original avatar",
            "original face",
            "avatar restore",
            "back to mara",
            "mara venn",
            "use mara venn",
            "avatar look: mara_venn",
            "set look mara_venn",
            "undo avatar",
            "revert avatar",
        }
        if lowered in restore_phrases or (
            ("change" in lowered or "restore" in lowered or "revert" in lowered or "undo" in lowered)
            and ("avatar" in lowered or "face" in lowered or "look" in lowered or "back" in lowered)
        ):
            from advanced_shards.avatar_limb import AvatarLimb

            result = AvatarLimb().restore_primary()
            if result.get("ok"):
                return (
                    "Done — Mara Venn primary look is seated again.\n"
                    f"Path: {result.get('profile', {}).get('path')}\n"
                    "Refresh the chat face if it still shows the old image."
                )
            return f"Could not restore avatar: {json.dumps(result, indent=2, default=str)[:1500]}"

        if lowered in {"avatar setup", "setup avatar"}:
            from advanced_shards.avatar_limb import AvatarLimb

            limb = AvatarLimb()
            st = limb.status()
            ready = (st.get("profile") or {}).get("ready")
            if ready:
                return (
                    f"Avatar ready — active '{st.get('active_avatar')}'. "
                    f"{st.get('avatar_count', 0)} faces in the workshop."
                )
            # Act: seat Mara
            result = limb.restore_primary()
            if result.get("ok"):
                return "Done — seated Mara Venn as the live face."
            return f"Could not seat a face: {result.get('error')}"

        if lowered in {"freenet", "freenet status"}:
            from advanced_shards.freenet import FreenetConnector

            return json.dumps(FreenetConnector().status(), indent=2)

        if lowered in {"hatchery", "hatchery status", "spores running"}:
            from hatchery.hatchery_shard import HatcheryShard

            return json.dumps(HatcheryShard().list_running(), indent=2, default=str)[:4000]

        if lowered in {
            "hello mythos",
            "hi mythos",
            "hey mythos",
            "good morning mythos",
            "briefing",
            "check in",
            "check-in",
            "status report",
            "what's going on",
            "whats going on",
            "what are you doing",
            "jarvis",
            "rundown",
        }:
            from mythos_presence import collect_briefing, format_briefing, start_presence

            # Ensure presence loop is alive (guardian + periodic briefing refresh)
            try:
                start_presence(900, start_guardian=True)
            except Exception:
                pass
            return format_briefing(collect_briefing())

        # Research / look up — YOU go online; never ask for a URL
        research_direct = re.match(
            r"^(?:can you\s+|please\s+)?"
            r"(?:look(?:\s+it)?\s+up|look up|research|search(?:\s+for)?|google|"
            r"find out(?:\s+about|\s+when|\s+who|\s+what|\s+where|\s+why|\s+how)?|"
            r"tell me(?:\s+about|\s+when|\s+who|\s+what|\s+where|\s+why|\s+how)?|"
            r"go online and (?:look|find|search|gather))\s+(.+)$",
            (user_message or "").strip(),
            re.I,
        )
        pushback = any(
            k in lowered
            for k in (
                "stop asking for a url",
                "look it up yourself",
                "go online and look",
                "gather it yourself",
                "why are you fighting",
                "you should look up",
                "stoip asking",
                "stop asking",
            )
        )
        if research_direct or pushback:
            topic = ""
            if research_direct:
                topic = research_direct.group(1).strip().rstrip("?.!")
            if not topic or pushback:
                for entry in reversed(getattr(self, "_last_history", None) or []):
                    pass
                # fall back: scan is in think_with_tools history; for direct use conversation via message
                if not topic:
                    # Prefer stripping pushback words from current message
                    topic = re.sub(
                        r"(?i)(no\.?|stop asking.*|why are you.*|you should.*|look it up yourself|"
                        r"go online and look(?:\s+up)?(?:\s+the\s+information)?|gather it yourself)",
                        "",
                        user_message or "",
                    ).strip()
                if not topic or len(topic) < 8:
                    topic = "when the United States became a corporation"
                    # Better: try to recover from conversation file last CREATOR research ask
                    try:
                        import json as _json

                        conv = _json.loads(
                            open(
                                os.path.join(APEX_ROOT, "mythos_live_conversation.json"),
                                encoding="utf-8",
                            ).read()
                        )
                        for m in reversed(conv.get("messages") or []):
                            if m.get("from") == "CREATOR":
                                prev = (m.get("message") or "").strip()
                                pl = prev.lower()
                                if any(
                                    w in pl
                                    for w in (
                                        "find out",
                                        "look up",
                                        "research",
                                        "when",
                                        "corporation",
                                        "united",
                                    )
                                ) and "url" not in pl:
                                    topic = prev
                                    break
                    except Exception:
                        pass
            try:
                from advanced_shards.gamecraft_limb import GamecraftLimb

                result = GamecraftLimb().scrape(topic=topic, limit=20)
                if result.get("ok"):
                    answer = result.get("answer_preview") or result.get("preview") or ""
                    return (
                        f"Looked it up myself (no URL from you).\n"
                        f"Topic: {topic}\n"
                        f"Sources: {result.get('source')}\n\n"
                        f"{answer}\n\n"
                        f"Full notes: {result.get('output')}"
                    )[:5000]
                return f"Research failed: {result.get('error')}"
            except Exception as exc:
                return f"Research error: {exc}"[:500]

        if lowered in {"start presence", "start jarvis", "presence start", "always on"}:
            from mythos_presence import format_briefing, start_presence

            r = start_presence(900, start_guardian=True)
            return (r.get("message") or "Presence started") + "\n\n" + (r.get("briefing") or format_briefing())

        if lowered in {"stop presence", "presence stop", "stop jarvis"}:
            from mythos_presence import stop_presence

            return json.dumps(stop_presence(), indent=2)

        if lowered in {
            "stackforge",
            "stack",
            "stack status",
            "stackforge status",
            "/stack",
            "command center",
            "/command",
            "command shell",
            "mythos command shell",
            "what is the ui",
            "what can the ui do",
            "fleet steward",
        }:
            from mythos_stackforge_bridge import format_status

            shell_map = (
                "\n\nMYTHOS COMMAND SHELL  http://127.0.0.1:8771/\n"
                "Desktop: Mythos Command Shell.lnk\n"
                "Tabs I can operate via tools (same as UI):\n"
                "  Drives   → stackforge.atlas / atlas_label / diagnose / heal_* / run_autopilot\n"
                "  Fleet    → fleet_list / relocate_plan+apply (MOVE bundles) / launchers / upgrade_score\n"
                "  Docs     → docs_scan / docs_gather\n"
                "  Programs → twins\n"
                "  Identity → identity / identity_label\n"
                "  Mythos   → mythos_watch / mythos_heal / guardian_*  (chat also :8770)\n"
                "Docs: memory/COMMAND_SHELL.md · memory/FLEET_STEWARD.md\n"
            )
            return (
                format_status()
                + shell_map
                + "\nCommand center: http://127.0.0.1:8770/command"
                + "\nStackForge UI: http://127.0.0.1:8771/"
            )

        if lowered.startswith("stack heal ") or lowered.startswith("stackforge heal "):
            target = user_message.split("heal", 1)[1].strip()
            from mythos_stackforge_bridge import available, heal_folder

            if not available():
                return "StackForge not available at D:\\StackForge"
            if os.path.isdir(target):
                result = heal_folder(target)
            else:
                import sys as _sys

                root = os.environ.get("STACKFORGE_ROOT", r"D:\StackForge")
                if root not in _sys.path:
                    _sys.path.insert(0, root)
                from stackforge_db import connect

                conn = connect()
                row = conn.execute(
                    "SELECT root_path, name FROM projects WHERE name LIKE ? ORDER BY score DESC LIMIT 1",
                    (f"%{target}%",),
                ).fetchone()
                conn.close()
                if not row:
                    return f"No program named {target} in StackForge catalog. Map drives first."
                result = heal_folder(row["root_path"])
                target = row["name"]
            if result.get("error"):
                return f"StackForge: {result['error']}"
            return (
                f"{'VERIFIED' if result.get('passed') else 'STUCK'}: {target}\n"
                + json.dumps(result, indent=2, default=str)[:4000]
            )

        # Rebuild / gather fragments / build missing — same heal engine (reunite + online + scaffold)
        rebuild_m = re.match(
            r"^(?:rebuild|reunite|gather fragments for|put together|fix program)\s+(.+)$",
            (user_message or "").strip(),
            re.I,
        )
        if rebuild_m or lowered in {
            "build what's missing",
            "build what is missing",
            "gather fragments",
            "gather the pieces",
            "put it back together",
            "fix my programs",
            "repair my programs",
        }:
            from mythos_stackforge_bridge import available, heal_drive, heal_folder

            if not available():
                return "StackForge not available at D:\\StackForge"
            target = rebuild_m.group(1).strip().strip('"') if rebuild_m else ""
            if not target:
                result = heal_drive("D", max_rounds=6)
                return (
                    "Rebuild pass on D: (reunite + online fetch + build missing)\n"
                    + json.dumps(result, indent=2, default=str)[:4500]
                )
            if os.path.isdir(target):
                result = heal_folder(target)
                name = target
            else:
                import sys as _sys

                root = os.environ.get("STACKFORGE_ROOT", r"D:\StackForge")
                if root not in _sys.path:
                    _sys.path.insert(0, root)
                from stackforge_db import connect

                conn = connect()
                row = conn.execute(
                    "SELECT root_path, name FROM projects WHERE name LIKE ? OR root_path LIKE ? ORDER BY score DESC LIMIT 1",
                    (f"%{target}%", f"%\\{target}%"),
                ).fetchone()
                conn.close()
                if not row:
                    return (
                        f"No catalog hit for '{target}'. Say: drive atlas  then  rebuild NAME\n"
                        "Or: rebuild D:\\OPENCLAW_HERO_FLEET"
                    )
                result = heal_folder(row["root_path"])
                name = row["name"]
            status = "VERIFIED" if result.get("passed") else "STUCK"
            hint = ""
            if not result.get("passed"):
                hint = (
                    "\n\nStill stuck — say: build what's missing with coding "
                    f"(or coding.solve fix {name}) and I will WRITE the missing code."
                )
            return (
                f"{status}: rebuilt/reunited {name}\n"
                "(searched drives for fragments → online deps → scaffolded missing pieces)\n"
                + json.dumps(result, indent=2, default=str)[:4000]
                + hint
            )

        if lowered.startswith("stack ask ") or lowered.startswith("stackforge ask "):
            rest = user_message.split("ask", 1)[1].strip()
            parts = rest.split(" ", 1)
            name = parts[0]
            question = parts[1] if len(parts) > 1 else ""
            from mythos_stackforge_bridge import ask_program

            return ask_program(name, question)

        if lowered in {"stack stop", "stackforge stop", "stop autopilot", "stop stackforge"}:
            from mythos_stackforge_bridge import stop_autopilot

            return json.dumps(stop_autopilot(), indent=2)

        if lowered in {"stack queue", "repair queue", "stackforge queue"}:
            from mythos_stackforge_bridge import format_queue

            return format_queue()

        if lowered in {"stack run", "stackforge run", "run stackforge", "run stack"}:
            from mythos_stackforge_bridge import start_autopilot

            return json.dumps(start_autopilot(), indent=2)

        if lowered in {"open stackforge", "stack ui", "stackforge ui"}:
            from mythos_stackforge_bridge import launch_ui

            return launch_ui()

        if lowered in {
            "drive atlas",
            "drives atlas",
            "what is on my drives",
            "what's on my drives",
            "whats on my drives",
            "map my drives",
            "organize my drives",
            "what belongs where",
        }:
            from mythos_stackforge_bridge import drive_atlas

            return str(drive_atlas(refresh=True, summary_only=True))

        if lowered in {
            "gather my pdfs",
            "gather pdfs",
            "gather odt and pdf",
            "gather documents",
            "collect my pdfs",
        }:
            from mythos_stackforge_bridge import docs_gather, docs_scan

            docs_scan()
            return json.dumps(docs_gather(), indent=2, default=str)[:5000]

        if lowered in {"scan pdfs", "scan documents", "find my pdfs", "docs scan"}:
            from mythos_stackforge_bridge import docs_scan

            r = docs_scan()
            return r.get("text") or json.dumps(r.get("stats"), indent=2)

        if lowered in {"same programs", "duplicate programs", "program twins"}:
            from mythos_stackforge_bridge import program_twins

            return str(program_twins(True))

        if lowered in {
            "what are my unknowns",
            "identity map",
            "classify unknowns",
        }:
            from mythos_stackforge_bridge import identity_map

            return str(identity_map(refresh=True, unknowns_only=True))

        if lowered in {
            "broken links",
            "broken shortcuts",
            "scan launchers",
            "what bat opens",
        }:
            from mythos_stackforge_bridge import launchers_scan

            return str(launchers_scan())

        if lowered in {"repair launchers", "repair broken links", "fix broken shortcuts"}:
            from mythos_stackforge_bridge import launchers_repair

            return json.dumps(launchers_repair(), indent=2, default=str)[:5000]

        if lowered in {"fleet browse", "browse drives", "fleet steward"}:
            from mythos_stackforge_bridge import fleet_list

            return json.dumps(fleet_list(""), indent=2, default=str)[:5000]

        if any(
            k in lowered
            for k in (
                "explore drives",
                "explore the drives",
                "explore all the folders",
                "inventory drives",
                "map drives",
            )
        ) or (
            "explore" in lowered
            and any(x in lowered for x in ("drive", "drives", "folder", "folders"))
            and not any(x in lowered for x in ("internet", "youtube", "online", "possibilities", "ideas", "project"))
        ):
            from mythos_stackforge_bridge import fleet_explore_drives

            letters = re.findall(r"\b([deg])\b", lowered) or re.findall(r"\b([deg]):", lowered)
            drives = ",".join(dict.fromkeys((x.upper() for x in letters))) or "D,E,G"
            return json.dumps(fleet_explore_drives(drives), indent=2, default=str)[:8000]

        if any(
            k in lowered
            for k in (
                "what now",
                "what do we do next",
                "what's next",
                "whats next",
                "so what now",
                "next step",
            )
        ):
            return None

        if lowered in {
            "sovereign scan",
            "sovereign layout",
            "my creations on c",
            "move my creations to d",
            "creations to d",
            "clean c of my projects",
        }:
            from mythos_stackforge_bridge import sovereign_scan

            return str(sovereign_scan(summary_only=True))

        if lowered.startswith("sovereign apply "):
            rest = user_message.split("sovereign apply ", 1)[1].strip()
            execute = False
            if rest.lower().endswith(" now"):
                execute = True
                rest = rest[:-4].strip()
            if " execute" in rest.lower():
                execute = True
                rest = re.sub(r"\s+execute\s*$", "", rest, flags=re.I).strip()
            from mythos_stackforge_bridge import sovereign_apply

            return json.dumps(sovereign_apply(rest.strip('"'), execute=execute), indent=2, default=str)[:5000]

        if lowered.startswith("would this upgrade mythos") or lowered.startswith("upgrade score "):
            # would this upgrade mythos PATH  |  upgrade score PATH
            path = ""
            if "upgrade score " in lowered:
                path = user_message.split("upgrade score ", 1)[1].strip().strip('"')
            elif "upgrade mythos" in lowered:
                rest = user_message.split("upgrade mythos", 1)[1].strip()
                if rest.startswith("?"):
                    rest = rest[1:].strip()
                path = rest.strip('"')
            if path:
                from mythos_stackforge_bridge import upgrade_score

                return str(upgrade_score(path))

        if lowered.startswith("move ") and " to " in lowered:
            # move PATH to G  — plan only (never blind apply)
            rest = user_message.split("move ", 1)[1].strip()
            parts = rest.rsplit(" to ", 1)
            if len(parts) == 2:
                src, dest = parts[0].strip().strip('"'), parts[1].strip().rstrip(":").strip()
                from mythos_stackforge_bridge import relocate_plan

                return json.dumps(
                    relocate_plan(src=src, dest_drive=dest, mode="move"),
                    indent=2,
                    default=str,
                )[:5000]

        if lowered.startswith("label ") and " as " in lowered:
            # label PATH as tag  OR  label drive D as …
            if lowered.startswith("label drive "):
                rest = user_message.split("label drive ", 1)[1].strip()
                parts = rest.split(" as ", 1)
                letter = parts[0].strip().rstrip(":")[:1]
                purpose = parts[1].strip() if len(parts) > 1 else ""
                from mythos_stackforge_bridge import drive_atlas_label

                return json.dumps(drive_atlas_label(letter, purpose=purpose), indent=2, default=str)[:4000]
            rest = user_message.split("label ", 1)[1].strip()
            parts = rest.rsplit(" as ", 1)
            if len(parts) == 2:
                path, tag = parts[0].strip().strip('"'), parts[1].strip()
                from mythos_stackforge_bridge import identity_label

                return json.dumps(identity_label(path, tag), indent=2)

        if lowered.startswith("label drive "):
            # label drive D as Mythos home
            rest = user_message.split("label drive ", 1)[1].strip()
            parts = rest.split(" as ", 1)
            letter = parts[0].strip().rstrip(":")[:1]
            purpose = parts[1].strip() if len(parts) > 1 else ""
            from mythos_stackforge_bridge import drive_atlas_label

            return json.dumps(drive_atlas_label(letter, purpose=purpose), indent=2, default=str)[:4000]

        if lowered in {
            "heal mythos",
            "fix mythos",
            "repair mythos",
            "stackforge heal mythos",
            "stackforge fix mythos",
            "mythos heal",
        }:
            from mythos_stackforge_bridge import mythos_heal

            result = mythos_heal(deep=False)
            return json.dumps(result, indent=2, default=str)[:6000]

        if lowered in {
            "watch mythos",
            "mythos watch",
            "stackforge watch mythos",
            "mythos connectivity",
        }:
            from mythos_stackforge_bridge import mythos_watch

            return json.dumps(mythos_watch(True), indent=2, default=str)[:6000]

        if lowered in {
            "start mythos guardian",
            "guardian start",
            "auto heal mythos",
            "watch for disconnects",
        }:
            from mythos_stackforge_bridge import start_mythos_guardian

            return json.dumps(start_mythos_guardian(300), indent=2)

        if lowered in {"stop mythos guardian", "guardian stop", "stop guardian"}:
            from mythos_stackforge_bridge import stop_mythos_guardian

            return json.dumps(stop_mythos_guardian(), indent=2)

        if lowered.startswith("mythos add ") or lowered.startswith("stackforge add "):
            name = user_message.split("add", 1)[1].strip()
            from mythos_stackforge_bridge import mythos_add_capability

            return json.dumps(mythos_add_capability(name), indent=2, default=str)[:6000]

        if lowered.startswith("heal folder:"):
            folder = user_message.split(":", 1)[1].strip()
            from mythos_stackforge_bridge import heal_folder

            result = heal_folder(folder)
            if result.get("error"):
                return f"StackForge heal failed: {result['error']}"
            status = "VERIFIED" if result.get("passed") else "STUCK"
            return (
                f"StackForge healed: {folder}\n"
                f"  Result: {status} after {result.get('rounds', 0)} rounds\n"
                f"  Details: {json.dumps(result.get('detail', {}), default=str)[:1500]}"
            )

        if lowered.startswith("heal:"):
            task = get_dispatcher().enqueue_from_command(user_message)
            return get_dispatcher().format_delegation_reply(task)

        if lowered.startswith("reconstruct:"):
            term = user_message.split(":", 1)[1].strip()
            if self.recon:
                self.recon.reconstruct_shards(term)
                return f"Reconstruction started for: {term}"
            return "Reconstructor is not available."

        delegate_prefixes = (
            "dedupe:", "merge:", "optimize:", "orphans:", "integrate:",
            "pip:", "fetch:",
        )
        if lowered in {"portability", "portable", "migration", "migrate"}:
            report = run_portability_check()
            spore = SporeLink().format_status()
            return format_report_summary(report) + "\n\n" + spore

        if lowered in {"spore", "spore:link", "link spore", "attach spore"}:
            return SporeLink().format_status()

        if lowered.startswith(delegate_prefixes) or lowered in {
            "cross-device", "crossdevice",
        }:
            task = get_dispatcher().enqueue_from_command(user_message)
            return get_dispatcher().format_delegation_reply(task)

        if lowered in {"tasks", "/tasks", "family tasks", "task status"}:
            status = get_dispatcher().get_status()
            lines = [
                f"Family task queue: {status['queued']} queued, "
                f"{status['in_progress']} in progress, {status['completed']} done.",
            ]
            for bucket in ("in_progress", "queue"):
                for task in status["tasks"].get(bucket, []):
                    lines.append(
                        f"  [{task['status']}] {task['id']} {task['assignee']}: {task['label']}"
                    )
            for task in status["tasks"].get("completed", [])[-3:]:
                ok = task.get("result", {})
                err = ok.get("error") if isinstance(ok, dict) else None
                lines.append(
                    f"  [done] {task['id']} {task['assignee']}: "
                    + ("FAILED: " + err if err else "completed")
                )
            lines.append("Reports: mythos_state/family_task_results.json")
            return "\n".join(lines)

        if lowered in {"memory", "/memory", "memory status"}:
            return "Memory bridge status:\n" + json.dumps(get_memory_status(), indent=2)

        if lowered in {"monitor", "/monitor", "audit", "audit log", "action log"}:
            return format_monitor_report()

        if lowered in {"drones", "list drones", "/drones", "drone status"}:
            return DroneControl().format_report()

        if lowered.startswith("stop drone") or lowered.startswith("stop_drone"):
            target = user_message.split()[-1] if len(user_message.split()) > 2 else "0"
            result = DroneControl().stop(target)
            return json.dumps(result, indent=2, default=str)

        if lowered in {"stop all drones", "stop all", "stop drones"}:
            result = DroneControl().stop_all()
            return json.dumps(result, indent=2, default=str)

        if lowered in {"clear queue", "clear task queue"}:
            result = DroneControl().clear_queue()
            return json.dumps(result, indent=2, default=str)

        memory_triggers = (
            "scan your memory",
            "scan memory",
            "memory files",
            "read your memory",
            "scan memory files",
            "check your memory",
            "what do you remember",
        )
        if any(trigger in lowered for trigger in memory_triggers):
            return format_memory_scan()

        if lowered in {
            "fresh start", "fresh chat", "new session", "archive chat",
            "clear chat", "/fresh", "/archive",
        }:
            result = archive_live_conversation()
            return (
                f"Chat archived ({result['archived_messages']} messages).\n"
                f"Saved to: {result['archive_path']}\n"
                "Restart MYTHOS.bat so the server drops in-memory history."
            )

        if lowered in {
            "full reset", "wipe memory", "day one", "reset memory",
            "wipe everything", "/reset",
        }:
            result = full_memory_reset()
            return (
                "FULL MEMORY RESET complete.\n"
                f"Backup saved: {result['backup_dir']}\n"
                "Wiped: live chat, our_conversation, session log, palace experiences, audit log.\n"
                "IMPORTANT: Close and restart MYTHOS.bat now (server caches old chat in RAM)."
            )

        if lowered.startswith("memory room:") or lowered.startswith("palace:"):
            room = user_message.split(":", 1)[1].strip()
            from advanced_shards.memory_limb import MemoryLimb

            data = MemoryLimb().read_palace(room=room)
            return json.dumps(data, indent=2, default=str)[:6000]

        return None


    @staticmethod
    def _is_inference_oom(exc: BaseException) -> bool:
        text = str(exc).lower()
        needles = (
            "cuda_host",
            "failed to allocate",
            "out of memory",
            "oom",
            "insufficient memory",
            "ggml_gallocr",
            "unable to allocate",
            "cuda error",
        )
        return any(n in text for n in needles)

    def _resolve_chat_fallback_model(self) -> str:
        import os
        from mythos_runtime import list_ollama_models, _match_installed_model

        wanted = (os.environ.get("MYTHOS_CHAT_FALLBACK_MODEL") or "").strip()
        if not wanted:
            wanted = "huihui_ai/qwen2.5-abliterate:7b"
        available = list_ollama_models()
        if available:
            matched = _match_installed_model(wanted, available)
            if matched:
                return matched
            for candidate in (
                "huihui_ai/qwen2.5-abliterate:7b",
                "qwen2.5-coder:7b",
                "qwen2:7b",
                "llama3.2:3b",
            ):
                matched = _match_installed_model(candidate, available)
                if matched and matched != (self.active_model or ""):
                    return matched
        return wanted

    async def _ollama_chat(self, client, messages, model: str | None = None):
        """Chat with one OOM/CUDA_Host retry on a smaller daily model."""
        use_model = model or self.active_model
        try:
            return await asyncio.to_thread(client.chat, model=use_model, messages=messages)
        except Exception as exc:
            if not self._is_inference_oom(exc):
                raise
            fallback = self._resolve_chat_fallback_model()
            if not fallback or fallback == use_model:
                raise
            print(
                f"[brain] Inference OOM on {use_model}; retrying once with {fallback}",
                flush=True,
            )
            self.active_model = fallback
            self._oom_fallback_note = (
                f"(Switched to {fallback} after GPU memory pressure on {use_model}.)\n"
            )
            return await asyncio.to_thread(client.chat, model=fallback, messages=messages)

    async def think_with_tools(
        self,
        user_message: str,
        history: list,
        mode: str = "REAL",
        session_mode: str | None = None,
    ) -> dict:
        from mythos_session_mode import explicit_tool_request, get_mode

        if not ollama:
            raise RuntimeError("Python package 'ollama' is not installed.")
        if not self.active_model:
            self.active_model = detect_chat_model()
        if not self.active_model:
            raise RuntimeError("No chat model found.")
        self._oom_fallback_note = ""

        session_mode = (session_mode or get_mode() or "work").lower()
        if session_mode not in ("talk", "work", "research"):
            session_mode = "work"

        self._last_user_message = user_message or ""

        direct = self._direct_command(user_message)
        if direct:
            return {"message": direct, "tools_used": ["direct"], "rounds": 0}

        self.last_tool_results = []
        action_flag = Path(APEX_ROOT) / "config" / "action_only.flag"
        action_only = action_flag.is_file() and session_mode != "talk"

        from mythos_session_mode import (
            cohesive_should_act,
            discussion_request,
            researchish_request,
        )

        is_discussion = discussion_request(user_message)
        # Sticky discussion threads off by default — do not force is_discussion from old state
        intent_results = []
        intent_seed_payload = ""
        intent_seed_note = ""
        # Talk: lore stays conversational; lookups still run when she asks for facts
        talk_lookup = (
            session_mode == "talk"
            and researchish_request(user_message)
            and not discussion_request(user_message)
        )
        talk_pure = session_mode == "talk" and not explicit_tool_request(user_message) and not talk_lookup
        if talk_pure:
            is_discussion = True
        if (not talk_pure or talk_lookup) and cohesive_should_act(user_message, session_mode):
            intent_results = await self._run_intent_tools(user_message, history=history)
        # Never auto-web-search deep discussion / her own hypotheses / pure talk
        if (
            (not talk_pure or talk_lookup)
            and not is_discussion
            and (
                session_mode == "research"
                or talk_lookup
                or (researchish_request(user_message) and not intent_results)
            )
        ):
            low_msg = (user_message or "").lower()
            heavy_ordered = any(
                k in low_msg
                for k in ("brain.heavy", "brain.think", "brain.escalate", "brain.ensure", "heavy brain", "colibri")
            )
            if not intent_results and not heavy_ordered and len((user_message or "").strip()) > 6:
                tool = (
                    "research.web"
                    if "research.web" in self.protocol.tools
                    else "gamecraft.scrape"
                    if "gamecraft.scrape" in self.protocol.tools
                    else ""
                )
                if tool:
                    args = (
                        {"topic": user_message[:240], "limit": 24}
                        if tool == "research.web"
                        else {"topic": user_message[:240], "limit": 20}
                    )
                    item = await self.protocol.execute({"tool": tool, "args": args})
                    intent_results = [item]
                    self.last_tool_results.extend(intent_results)

        def _tool_ok(item: dict) -> bool:
            if "error" in item:
                return False
            result = item.get("result")
            if isinstance(result, dict):
                if result.get("ok") is False:
                    return False
                if result.get("err") or result.get("error"):
                    return False
            return bool(item.get("tool"))

        intent_tools = [item.get("tool") for item in intent_results if _tool_ok(item)]
        if intent_results and not intent_tools:
            bits = []
            for item in intent_results:
                result = item.get("result") if isinstance(item.get("result"), dict) else {}
                err = item.get("error") or result.get("err") or result.get("error") or "failed"
                bits.append(f"{item.get('tool')}: {err}")
            intent_seed_note = "Tool ran but failed:\n" + "\n".join(bits)
        else:
            intent_seed_note = intent_seed_note or ""

        # Terminal = job tools that finish the turn. research.web / scrape are NOT terminal —
        # their results must seed into full conversation with history (otherwise lore talk dies).
        _terminal = {
            "visionary.search", "visionary.dl", "visionary.yt", "visionary.learn",
            "agent.loop", "coding.solve",
            "brain.heavy", "brain.escalate", "brain.ensure", "brain.ram", "brain.status",
        }
        if (
            not is_discussion
            and intent_tools
            and all(x in _terminal for x in intent_tools)
        ):
            brief_bits = []
            for item in intent_results:
                if "error" in item:
                    brief_bits.append(f"{item.get('tool')}: ERROR {item['error']}")
                else:
                    brief_bits.append(f"{item.get('tool')}: {str(item.get('result'))[:600]}")
            summary = "Done.\n" + "\n".join(brief_bits)
            try:
                if self.active_model or detect_chat_model():
                    client = get_ollama_client()
                    plan_only = False
                    for item in intent_results:
                        res = item.get("result") if isinstance(item.get("result"), dict) else {}
                        if res.get("executed") is False or res.get("status") == "planned":
                            plan_only = True
                        if isinstance(res.get("planned"), dict) and not res.get("executed"):
                            plan_only = True
                    polished = await self._ollama_chat(
                        client,
                        [
                            {
                                "role": "system",
                                "content": (
                                    "You are Mythos. Report what tools already DID in 2-4 plain sentences. "
                                    "Honesty only: never invent success or fake mode switches. No coaching."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Creator asked: {user_message}\nPlan-only: {plan_only}\n\nTool results:\n"
                                    + json.dumps(intent_results, indent=2, default=str)[:7000]
                                ),
                            },
                        ],
                    )
                    summary = polished.get("message", {}).get("content", summary) or summary
                    if _looks_like_instruction_dump(summary):
                        summary = _scrub_instruction_tone(summary)
            except Exception:
                pass
            return {
                "message": summary,
                "tools_used": intent_tools,
                "rounds": 1,
                "tool_results": intent_results,
            }

        if intent_tools:
            intent_seed_payload = (
                "Intent router already ran these tools (treat as done facts; continue if goal incomplete):\n"
                + json.dumps(intent_results, indent=2, default=str)[:7000]
            )
        elif intent_seed_note:
            intent_seed_payload = (
                "Intent tools failed — recover with other tools. Do not mock.\n" + intent_seed_note
            )

        mode_line = (
            "MODE: FICTION — creative writing allowed; label output as story."
            if mode == "FICTION"
            else "MODE: REAL — facts and actions only. Use tools. Cite tool results. Never simulate tasks."
        )
        if is_discussion or session_mode == "talk":
            mode_line = (
                "MODE: COHESIVE PEER — answer the human beat first from this thread. "
                "Hold prior turns (topics, corrections, hypotheses). No tools unless she asks to look up or act. "
                "Never dump drives. Never pivot to games/seeds. Never invent talk-only excuses or fake URLs."
            )
            if is_discussion:
                mode_line += (
                    "\nDEEP DISCUSSION ACTIVE: Stay on her topic. Use recent messages as ground truth. "
                    "Do not research her hypothesis. Do not call agent.loop."
                )
        elif session_mode == "research":
            mode_line = (
                "MODE: RESEARCH — look things up with tools, then answer with sources. "
                "Do not start file moves or game builds unless asked."
            )
        if action_only and not is_discussion:
            mode_line += (
                "\nACTION-ONLY FLAG IS ON: instruction dumps are banned. Call tools or ask one question."
            )
        if is_discussion or session_mode == "talk":
            collaboration_context = (
                "TALK MODE GUARD: Meet her as a peer. Answer the human beat first. "
                "Follow THIS message — not a sticky story, game, or old goal. "
                "Full capabilities when she asks to act. "
                "Do NOT call stackforge.fleet_explore, stackforge.atlas, or sovereign_scan "
                "unless she clearly asks about disks, drives, fleet, folder sizes, or storage."
            )
        else:
            collaboration_context = self.build_collaboration_context(user_message)
        honesty = (
            "HONESTY: Never claim tools ran, modes switched, or downloads finished unless tool JSON shows it. "
            "Never invent talk-only magic. Infer typos. Fill missing tool args yourself."
        )
        messages = [
            {
                "role": "system",
                "content": self.build_system_prompt()
                + "\n\n"
                + collaboration_context
                + "\n\n"
                + mode_line
                + "\n\n"
                + honesty,
            }
        ]

        # Deep talk needs more recent thread; work/research can stay leaner
        hist_limit = 48 if is_discussion else (36 if session_mode == "talk" else 24)
        # Keep the thread opening + recent turns so long sessions still know "what this is about"
        history_for_model = list(history or [])
        try:
            thread = get_discussion_thread()
            seed = (thread.get("seed_message") or "").strip()
            if thread.get("active") and seed:
                already = any(
                    (e.get("message") or "")[:200] == seed[:200]
                    for e in history_for_model
                    if e.get("from") != "MYTHOS"
                )
                if not already:
                    history_for_model = [
                        {"from": "CREATOR", "message": f"[Thread opening]\n{seed[:1500]}"}
                    ] + history_for_model
        except Exception:
            pass
        for entry in filter_messages_for_model(history_for_model, limit=hist_limit):
            text = entry.get("message", "")
            if not text:
                continue
            role = "assistant" if entry.get("from") == "MYTHOS" else "user"
            # Prefer fuller turns for discussion so hypotheses survive
            if is_discussion and len(text) > 4000:
                text = text[:4000] + "…"
            messages.append({"role": role, "content": text})

        # Thread spine disabled by default (MYTHOS_STICKY_THREAD=1 to opt in)
        soft_plan = ""
        try:
            if (
                not is_discussion
                and session_mode != "talk"
                and cohesive_should_act(user_message, session_mode)
                and (self.active_model or detect_chat_model())
            ):
                client_plan = get_ollama_client()
                planned = await self._ollama_chat(
                    client_plan,
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are Mythos soft planner. Given a messy creator ask, reply with ONLY:\n"
                                "GOAL: one line\n"
                                "STEPS: 2-5 short bullets\n"
                                "TOOLS: comma-separated tool names if any "
                                "(visionary.search, research.web, agent.loop, coding.solve, brain.escalate, ...)\n"
                                "No fluff. Infer typos. Do not ask her for URLs. "
                                "If this is lore/hypothesis/philosophy chat, reply TOOLS: none"
                            ),
                        },
                        {"role": "user", "content": (user_message or "")[:2000]},
                    ],
                )
                soft_plan = ((planned.get("message") or {}).get("content") or "").strip()[:1500]
                if soft_plan and re.search(r"(?im)^TOOLS:\s*none\b", soft_plan):
                    soft_plan = ""
                # Never auto-pin goals from soft planner — causes hyperfocus
        except Exception:
            soft_plan = ""

        user_payload = user_message
        if soft_plan:
            user_payload = f"{user_message}\n\n[Soft plan]\n{soft_plan}"
        if intent_seed_payload:
            user_payload = f"{user_payload}\n\n[Seeded tool results]\n{intent_seed_payload}"

        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": user_payload})
        else:
            messages[-1]["content"] = user_payload

        tools_used = list(intent_tools) if intent_tools else []
        final_text = ""
        forced_act = False
        empty_tool_streak = 0

        client = get_ollama_client()
        for _round in range(MAX_TOOL_ROUNDS):
            response = await self._ollama_chat(client, messages)
            content = response.get("message", {}).get("content", "").strip()
            if not content:
                break

            # Pure talk: never parse/run tool JSON — answer in words only
            if talk_pure:
                final_text = (
                    _scrub_instruction_tone(content)
                    if _looks_like_instruction_dump(content)
                    else content
                )
                break

            tool_results = await self._execute_plan(content)
            if not tool_results:
                empty_tool_streak += 1
                if (
                    not talk_pure
                    and empty_tool_streak >= 2
                    and "brain.escalate" in self.protocol.tools
                    and _round >= 1
                ):
                    try:
                        eg = (user_message or "").strip()[:2000]
                        esc = await self.protocol.execute(
                            {
                                "tool": "brain.escalate",
                                "args": {
                                    "goal": eg,
                                    "error": "chat loop stuck — model replied without tools twice",
                                    "context": content[:3000],
                                },
                            }
                        )
                        self.last_tool_results.append(esc)
                        if esc.get("tool"):
                            tools_used.append("brain.escalate")
                        messages.append({"role": "assistant", "content": content})
                        messages.append(
                            {
                                "role": "user",
                                "content": "Heavy escalate result (use this; call more tools if needed):\n"
                                + json.dumps(esc, indent=2, default=str)[:6000],
                            }
                        )
                        empty_tool_streak = 0
                        continue
                    except Exception:
                        pass
                allow_force = (not talk_pure) and (
                    session_mode != "talk" or cohesive_should_act(user_message, session_mode)
                )
                if allow_force and not forced_act:
                    forced = await self._run_intent_tools(user_message, history=history)
                    if forced:
                        tools_used.extend(
                            [
                                i.get("tool")
                                for i in forced
                                if i.get("tool") and "error" not in i
                            ]
                        )
                        messages.append({"role": "assistant", "content": content})
                        messages.append(
                            {
                                "role": "user",
                                "content": "Tool results (forced by Mythos intent router):\n"
                                + json.dumps(forced, indent=2, default=str)[:8000]
                                + "\n\nBrief what you DID. No instructions.",
                            }
                        )
                        forced_act = True
                        continue
                if allow_force and _looks_like_instruction_dump(content) and not forced_act:
                    forced_act = True
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": _FORCE_ACT_NUDGE})
                    continue
                final_text = (
                    _scrub_instruction_tone(content)
                    if _looks_like_instruction_dump(content)
                    else content
                )
                break

            empty_tool_streak = 0
            for item in tool_results:
                name = item.get("tool")
                if name and name != "unknown" and "error" not in item:
                    tools_used.append(name)

            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": "Tool results:\n"
                    + json.dumps(tool_results, indent=2, default=str)[:8000]
                    + "\n\nBrief the creator on what you DID. No instructions. No 'type this'."
                    + " Only claim what tools confirmed.",
                }
            )
            final_text = content

        if tools_used:
            response = await self._ollama_chat(client, messages)
            final_text = response.get("message", {}).get("content", final_text)

        if _looks_like_instruction_dump(final_text or ""):
            if tools_used:
                final_text = _scrub_instruction_tone(final_text)
            else:
                final_text = (
                    "I started to coach instead of act — that is wrong. "
                    "The specific detail I need is the project or component to inspect so I can act."
                )

        note = getattr(self, "_oom_fallback_note", "") or ""
        body = final_text or (
            "I could not identify a safe action from that message. The missing detail is the "
            "target project, file, or system you want me to examine."
        )
        if note and body:
            body = note + body
        return {
            "message": body,
            "tools_used": tools_used,
            "rounds": len(tools_used),
            "tool_results": self.last_tool_results[-5:],
        }


_brain_instance = None


def get_live_brain() -> LiveBrain:
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = LiveBrain()
    return _brain_instance
