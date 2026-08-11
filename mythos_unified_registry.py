#!/usr/bin/env python3
"""Register every Mythos capability on one ToolProtocol — one brain, all limbs."""
from __future__ import annotations

import os
import sys

APEX_ROOT = os.path.dirname(os.path.abspath(__file__))
if APEX_ROOT not in sys.path:
    sys.path.insert(0, APEX_ROOT)
AGENTS_DIR = os.path.join(APEX_ROOT, "agents")
if AGENTS_DIR not in sys.path:
    sys.path.insert(0, AGENTS_DIR)


def register_all_tools(protocol, quiet: bool = True) -> int:
    """Wire hatchery, evolution, advanced shards, bridge, and service limbs."""
    before = len(getattr(protocol, "tools", {}))

    loaders = [
        _load_hatchery,
        _load_evolution,
        _load_advanced,
        _load_bridge,
        _load_stackforge,
        _load_companion_tools,
        _load_graveyard,
        _load_limbs_tools,
        _load_agents_limb,
        _load_capability,
        _load_service_limbs,
    ]
    for loader in loaders:
        try:
            loader(protocol, quiet=quiet)
        except Exception as exc:
            if not quiet:
                print(f"[registry] {loader.__name__}: {exc}", flush=True)

    return len(protocol.tools) - before


def _load_hatchery(protocol, quiet: bool = True) -> None:
    from hatchery.hatchery_shard import register_hatchery_tools

    register_hatchery_tools(protocol)


def _load_evolution(protocol, quiet: bool = True) -> None:
    from evolution.evolution_shard import register_evolution_tools

    register_evolution_tools(protocol)


def _load_advanced(protocol, quiet: bool = True) -> None:
    from advanced_shards import register_advanced_tools

    register_advanced_tools(protocol, quiet=quiet)


def _load_graveyard(protocol, quiet: bool = True) -> None:
    from advanced_shards.graveyard_limb import register_graveyard_tools

    register_graveyard_tools(protocol, quiet=quiet)


def _load_limbs_tools(protocol, quiet: bool = True) -> None:
    from advanced_shards.limbs_tools_limb import register_limbs_tools

    register_limbs_tools(protocol, quiet=quiet)


def _load_capability(protocol, quiet: bool = True) -> None:
    from advanced_shards.capability_limb import register_capability_tools

    register_capability_tools(protocol, quiet=quiet)


def _load_agents_limb(protocol, quiet: bool = True) -> None:
    from advanced_shards.agents_limb import register_agents_tools

    register_agents_tools(protocol, quiet=quiet)


def _load_stackforge(protocol, quiet: bool = True) -> None:
    from advanced_shards.stackforge_limb import register_stackforge_tools

    register_stackforge_tools(protocol, quiet=quiet)


def _load_companion_tools(protocol, quiet: bool = True) -> None:
    from advanced_shards.openhands_limb import register_openhands_tools
    from advanced_shards.openmontage_limb import register_openmontage_tools
    from advanced_shards.free_cluely_limb import register_free_cluely_tools
    from advanced_shards.deeplivecam_limb import register_deeplivecam_tools
    from advanced_shards.composio_limb import register_composio_tools
    from advanced_shards.screenpipe_limb import register_screenpipe_tools
    from advanced_shards.design_limb import register_design_tools
    from advanced_shards.tts_limb import register_tts_tools

    register_openhands_tools(protocol, quiet=quiet)
    register_openmontage_tools(protocol, quiet=quiet)
    register_free_cluely_tools(protocol, quiet=quiet)
    register_deeplivecam_tools(protocol, quiet=quiet)
    register_composio_tools(protocol, quiet=quiet)
    register_screenpipe_tools(protocol, quiet=quiet)
    register_design_tools(protocol, quiet=quiet)
    register_tts_tools(protocol, quiet=quiet)


def _load_bridge(protocol, quiet: bool = True) -> None:
    from bridge_sovereign import register_bridge_tools

    register_bridge_tools(protocol)


def _load_service_limbs(protocol, quiet: bool = True) -> None:
    from advanced_shards.graphics_limb import GraphicsLimb
    from advanced_shards.gameworld_limb import GameworldLimb
    from advanced_shards.gamecraft_limb import GamecraftLimb
    from advanced_shards.memory_limb import MemoryLimb
    from advanced_shards.matrix_limb import MatrixLimb
    from advanced_shards.studio_limb import StudioLimb, register_studio_tools
    from advanced_shards.protector_limb import ProtectorLimb, register_protector_tools
    from advanced_shards.security_toolkit_limb import register_security_toolkit_tools
    from advanced_shards.nlp_limb import register_nlp_tools
    from advanced_shards.laptop_control_limb import register_laptop_tools
    from advanced_shards.filesorter_limb import register_filesorter_tools
    from mythos_drone_control import DroneControl

    register_laptop_tools(protocol, quiet=quiet)
    register_filesorter_tools(protocol, quiet=quiet)

    gfx = GraphicsLimb()
    gw = GameworldLimb()
    craft = GamecraftLimb()
    mem = MemoryLimb()
    matrix = MatrixLimb()
    drone = DroneControl()
    register_studio_tools(protocol, quiet=quiet)
    register_protector_tools(protocol, quiet=quiet)
    register_security_toolkit_tools(protocol, quiet=quiet)
    register_nlp_tools(protocol, quiet=quiet)

    protocol.register(
        "gamecraft.status",
        craft,
        "status",
        {
            "description": "GameCraft status — scrape + image gen + playable browser games",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "gamecraft.list",
        craft,
        "list",
        {
            "description": "List seated GameCraft browser games",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "gamecraft.scrape",
        craft,
        "scrape",
        {
            "description": (
                "Legacy alias for web research (also used by GameCraft builds). "
                "Prefer research.web for general internet questions on ANY topic."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "topic": {"type": "string"},
                    "limit": {"type": "integer", "default": 20},
                },
            },
        },
    )
    # General internet research — ANY topic (not games-only)
    from advanced_shards.research_limb import ResearchLimb

    research = ResearchLimb()
    protocol.register(
        "research.status",
        research,
        "status",
        {
            "description": "General research limb status — any topic on the internet",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "research.web",
        research,
        "web",
        {
            "description": (
                "Go online and research ANY topic or URL — bridges, history, how-tos, "
                "people, science, schematics, news. Not limited to games. "
                "Use this whenever the creator wants information from the internet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "url": {"type": "string"},
                    "limit": {"type": "integer", "default": 24},
                },
            },
        },
    )
    protocol.register(
        "research.lookup",
        research,
        "lookup",
        {
            "description": "Natural-language internet lookup for ANY query (alias of research.web)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 24},
                },
                "required": ["query"],
            },
        },
    )
    # Agent-Reach — richer channels (YouTube captions, web reader, GH, RSS, …)
    try:
        from advanced_shards.agent_reach_limb import AgentReachLimb

        reach = AgentReachLimb()
        protocol.register(
            "research.reach_status",
            reach,
            "status",
            {
                "description": "Agent-Reach limb status (CLI path, readiness)",
                "parameters": {"type": "object", "properties": {}},
            },
        )
        protocol.register(
            "research.reach_doctor",
            reach,
            "doctor",
            {
                "description": "Check which Agent-Reach internet channels are working",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channels": {
                            "type": "string",
                            "description": "Optional comma-separated channel names",
                        },
                    },
                },
            },
        )
        protocol.register(
            "research.reach_get",
            reach,
            "get",
            {
                "description": (
                    "Read from an Agent-Reach channel (web, youtube, github, rss, …). "
                    "Prefer for YouTube transcripts / richer fetches; use research.web for simple lookups."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                        "max_tokens": {"type": "integer", "default": 4000},
                    },
                    "required": ["target"],
                },
            },
        )
        protocol.register(
            "research.reach_web",
            reach,
            "web",
            {
                "description": (
                    "Fetch a URL or search via Agent-Reach (Jina/Exa when installed). "
                    "Falls back to Jina Reader for bare URLs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "topic": {"type": "string"},
                        "query": {"type": "string"},
                    },
                },
            },
        )
        protocol.register(
            "research.reach_youtube",
            reach,
            "youtube",
            {
                "description": "YouTube metadata, transcript, or search via Agent-Reach / yt-dlp",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                },
            },
        )
        protocol.register(
            "research.reach",
            reach,
            "reach",
            {
                "description": (
                    "One-shot Agent-Reach router: action=doctor|get|web|youtube. "
                    "Use when the creator wants YouTube captions, rich URL read, or channel doctor."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "default": "get"},
                        "target": {"type": "string"},
                        "query": {"type": "string"},
                        "url": {"type": "string"},
                        "topic": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            },
        )
    except Exception:
        pass
    protocol.register(
        "gamecraft.generate_art",
        craft,
        "generate_art",
        {
            "description": "Generate one game art asset via ComfyUI into gamecraft assets",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "role": {"type": "string", "default": "background"},
                    "width": {"type": "integer", "default": 768},
                    "height": {"type": "integer", "default": 512},
                    "wait": {"type": "boolean", "default": True},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "gamecraft.build",
        craft,
        "build",
        {
            "description": (
                "ONE-SHOT: scrape/research + generate art + seat a playable browser game. "
                "Use this when the creator wants a graphical game made for them. Do the work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "url": {"type": "string"},
                    "name": {"type": "string"},
                    "generate_images": {"type": "boolean", "default": True},
                },
            },
        },
    )

    protocol.register(
        "graphics.status",
        gfx,
        "status",
        {
            "description": "Check image/video generation backends (ComfyUI Desktop/portable)",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "graphics.start",
        gfx,
        "start",
        {
            "description": "Launch seated ComfyUI and wait for API on :8188",
            "parameters": {
                "type": "object",
                "properties": {"wait_seconds": {"type": "integer", "default": 45}},
            },
        },
    )
    protocol.register(
        "graphics.generate",
        gfx,
        "generate",
        {
            "description": "Generate an image from a text prompt (uses seated ComfyUI)",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "width": {"type": "integer", "default": 512},
                    "height": {"type": "integer", "default": 512},
                    "ckpt_name": {"type": "string"},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "graphics.generate_wait",
        gfx,
        "generate_wait",
        {
            "description": "Generate an image and wait until the file exists on disk (returns path)",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "width": {"type": "integer", "default": 512},
                    "height": {"type": "integer", "default": 768},
                    "ckpt_name": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "default": 180},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "gameworld.status",
        gw,
        "status",
        {
            "description": "Check if the living gameworld server is running",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "gameworld.start",
        gw,
        "start",
        {
            "description": "Start gameworld server on port 8888 (background)",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "gameworld.project",
        gw,
        "project",
        {
            "description": "List Godot gameworld project scripts/scenes under godot_project/",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "gameworld.write_script",
        gw,
        "write_script",
        {
            "description": "Generate and write a GDScript into godot_project/ (builds gameworld code)",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "What the script should do"},
                    "filename": {"type": "string", "description": "e.g. npc_wander.gd"},
                },
                "required": ["task", "filename"],
            },
        },
    )
    protocol.register(
        "gameworld.upgrade",
        gw,
        "upgrade",
        {
            "description": "Upgrade gameworld on D: — start HTTP+WS servers, assets folder, player script, first world scene",
            "parameters": {"type": "object", "properties": {}},
        },
    )

    from advanced_shards.coding_limb import CodingLimb

    code = CodingLimb()
    protocol.register(
        "coding.status",
        code,
        "status",
        {"description": "Coding limb status — write-enabled paths for Apex/gameworld", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "coding.write_file",
        code,
        "write_file",
        {
            "description": "Write a text/code file under Mythos Apex (safe paths only)",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["filepath", "content"],
            },
        },
    )
    protocol.register(
        "coding.code_to_file",
        code,
        "code_to_file",
        {
            "description": "Generate code with Ollama and write it to a file under Apex/godot_project",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "filepath": {"type": "string"},
                    "language": {"type": "string", "default": ""},
                },
                "required": ["task", "filepath"],
            },
        },
    )
    protocol.register(
        "coding.write_gdscript",
        code,
        "write_gdscript",
        {
            "description": "Generate GDScript and write under godot_project/",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["task", "filename"],
            },
        },
    )
    protocol.register(
        "coding.run_coder",
        code,
        "run_coder",
        {
            "description": "Generate a Python shard into limbs/autonomous_output",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "filename": {"type": "string", "default": ""},
                },
                "required": ["task"],
            },
        },
    )
    protocol.register(
        "coding.apply_patch_replace",
        code,
        "apply_patch_replace",
        {
            "description": "Replace one exact text block in an Apex file",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["filepath", "old", "new"],
            },
        },
    )
    protocol.register(
        "coding.run_python",
        code,
        "run_python",
        {
            "description": "Run a Python file under Apex",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "timeout": {"type": "integer", "default": 60},
                },
                "required": ["filepath"],
            },
        },
    )
    protocol.register(
        "coding.list_project",
        code,
        "list_project",
        {
            "description": "List code files under a project folder (default projects/)",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "default": "projects"}},
            },
        },
    )
    protocol.register(
        "coding.find_local",
        code,
        "find_local",
        {
            "description": "Search Apex/OpenMontage/StackForge/Library for existing code matching a need",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
        },
    )
    protocol.register(
        "coding.find_online",
        code,
        "find_online",
        {
            "description": "Fetch a package/tool from the internet (pip or git/URL)",
            "parameters": {
                "type": "object",
                "properties": {"name_or_url": {"type": "string"}},
                "required": ["name_or_url"],
            },
        },
    )
    protocol.register(
        "coding.solve",
        code,
        "solve",
        {
            "description": "Fully capable coder: find on drives → fetch online → write new code if missing",
            "parameters": {
                "type": "object",
                "properties": {
                    "need": {"type": "string"},
                    "filepath": {"type": "string", "default": ""},
                    "language": {"type": "string", "default": "python"},
                    "package_hint": {"type": "string", "default": ""},
                    "force_write": {"type": "boolean", "default": False},
                },
                "required": ["need"],
            },
        },
    )

    from advanced_shards.agent_loop_limb import AgentLoopLimb

    agent = AgentLoopLimb()
    protocol.register(
        "agent.status",
        agent,
        "status",
        {"description": "Cursor-style agent loop status (plan→edit→run→retry)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "agent.last",
        agent,
        "last",
        {"description": "Show the most recent agent.loop run log", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "agent.loop",
        agent,
        "loop",
        {
            "description": "Autonomous coding loop: plan → write → run → observe → research/heavy → retry until goal or max_steps. Free-flow; no babysitting. When stuck, escalates to Colibri/GGUF heavy brain if allow_heavy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "What to build or fix"},
                    "project_dir": {"type": "string", "default": ""},
                    "max_steps": {"type": "integer", "default": 6},
                    "run_test": {"type": "string", "default": ""},
                    "allow_online": {"type": "boolean", "default": True},
                    "allow_heavy": {
                        "type": "boolean",
                        "default": True,
                        "description": "Auto-call brain.heavy (Colibri/GGUF) when stuck",
                    },
                    "language": {"type": "string", "default": "python"},
                },
                "required": ["goal"],
            },
        },
    )

    from advanced_shards.reality_machine_limb import RealityMachineLimb

    reality = RealityMachineLimb()
    protocol.register(
        "reality.status",
        reality,
        "status",
        {"description": "Reality Machine status — system MoE orchestrator", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "reality.inventory",
        reality,
        "inventory",
        {
            "description": "Federate drives — list top folders across D/E/G (no moves)",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_per_drive": {"type": "integer", "default": 30},
                    "roots": {"type": "string", "description": "Optional extra paths ;-separated"},
                },
            },
        },
    )
    protocol.register(
        "reality.route",
        reality,
        "route",
        {
            "description": "Divide a goal into expert regions (system Mixture-of-Experts plan)",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["goal"],
            },
        },
    )
    protocol.register(
        "reality.find_project",
        reality,
        "find_project",
        {
            "description": "Search D/E/G for a project folder by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "drives": {"type": "string", "default": "D,E,G"},
                    "max_hits": {"type": "integer", "default": 20},
                },
                "required": ["name"],
            },
        },
    )
    protocol.register(
        "reality.census",
        reality,
        "census",
        {
            "description": "Scan drives and queue programs by content (names do not matter)",
            "parameters": {
                "type": "object",
                "properties": {
                    "drives": {"type": "string", "default": "D,E,G"},
                    "max_projects": {"type": "integer", "default": 80},
                    "only_broken": {"type": "boolean", "default": True},
                    "depth": {"type": "integer", "default": 2},
                },
            },
        },
    )
    protocol.register(
        "reality.autonomy",
        reality,
        "autonomy",
        {
            "description": "Heal programs on drives without pointing — internet on, finished programs not reports",
            "parameters": {
                "type": "object",
                "properties": {
                    "drives": {"type": "string", "default": "D,E,G"},
                    "max_projects": {"type": "integer", "default": 3},
                    "max_steps_each": {"type": "integer", "default": 6},
                    "refresh_census": {"type": "boolean", "default": True},
                    "allow_internet": {"type": "boolean", "default": True},
                    "allow_heavy": {"type": "boolean", "default": True},
                    "only_broken": {"type": "boolean", "default": True},
                },
            },
        },
    )
    protocol.register(
        "reality.continue",
        reality,
        "continue_solve",
        {
            "description": "Keep draining heal queue / last Reality Machine goal until programs are finished",
            "parameters": {
                "type": "object",
                "properties": {"max_steps": {"type": "integer", "default": 8}},
            },
        },
    )
    protocol.register(
        "reality.solve",
        reality,
        "solve",
        {
            "description": "Reality Machine: inventory→route→research→code/debug→heavy escalate until done",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "project_dir": {"type": "string"},
                    "max_steps": {"type": "integer", "default": 8},
                    "allow_internet": {"type": "boolean", "default": True},
                    "allow_heavy": {"type": "boolean", "default": True},
                    "allow_colibri_master": {"type": "boolean", "default": True},
                    "language": {"type": "string", "default": ""},
                },
                "required": ["goal"],
            },
        },
    )

    from advanced_shards.colibri_master_limb import ColibriMasterLimb

    coli_master = ColibriMasterLimb()
    protocol.register(
        "colibri.status",
        coli_master,
        "status",
        {"description": "Colibri Master status — MoE bring-up mission", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "colibri.diagnose",
        coli_master,
        "diagnose",
        {"description": "Diagnose Colibri weights/RAM/IO blockers", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "colibri.repair_weights",
        coli_master,
        "repair_weights",
        {
            "description": "Delete corrupt/tiny shards and resume HF download",
            "parameters": {
                "type": "object",
                "properties": {"start_download": {"type": "boolean", "default": True}},
            },
        },
    )
    protocol.register(
        "colibri.free_competitors",
        coli_master,
        "free_competitors",
        {
            "description": "Stop llama/colibri servers hogging RAM on 8010/8088",
            "parameters": {
                "type": "object",
                "properties": {"aggressive": {"type": "boolean", "default": False}},
            },
        },
    )
    protocol.register(
        "colibri.try_serve",
        coli_master,
        "try_serve",
        {
            "description": "Try one low-RAM Colibri serve profile and wait for /v1/models",
            "parameters": {
                "type": "object",
                "properties": {
                    "profile": {"type": "string"},
                    "wait_sec": {"type": "integer", "default": 90},
                    "port": {"type": "integer", "default": 8010},
                },
            },
        },
    )
    protocol.register(
        "colibri.master",
        coli_master,
        "master",
        {
            "description": "Focus until Colibri MoE is up: diagnose→repair→free RAM→try profiles→probe",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_rounds": {"type": "integer", "default": 6},
                    "wait_sec": {"type": "integer", "default": 90},
                    "repair": {"type": "boolean", "default": True},
                    "free_ram": {"type": "boolean", "default": True},
                    "smoke": {"type": "boolean", "default": True},
                },
            },
        },
    )

    from advanced_shards.heavy_brain_limb import HeavyBrainLimb

    heavy = HeavyBrainLimb()
    protocol.register(
        "brain.status",
        heavy,
        "status",
        {
            "description": "Heavy brain (Colibri/GGUF) status — separate from daily Ollama chat",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "brain.ram",
        heavy,
        "ram",
        {
            "description": "Check free/total RAM vs Colibri ~18GB peak need. Report numbers only — no chat fluff.",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "brain.ensure",
        heavy,
        "ensure",
        {
            "description": "Ensure Colibri/GGUF heavy API is up; auto-start coli serve if weights ready",
            "parameters": {
                "type": "object",
                "properties": {
                    "wait_sec": {"type": "integer", "default": 45},
                    "start_if_down": {"type": "boolean", "default": True},
                },
            },
        },
    )
    protocol.register(
        "brain.think",
        heavy,
        "think",
        {
            "description": "Ask the heavy brain (Colibri/GGUF) one deep question. Daily chat stays on Ollama.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "system": {"type": "string", "default": ""},
                    "max_tokens": {"type": "integer", "default": 1200},
                    "ensure_up": {"type": "boolean", "default": True},
                    "temperature": {"type": "number", "default": 0.3},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "brain.heavy",
        heavy,
        "heavy",
        {
            "description": "Alias of brain.think — call Colibri/GGUF for hard jobs without creator babysitting",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "system": {"type": "string", "default": ""},
                    "max_tokens": {"type": "integer", "default": 1200},
                    "ensure_up": {"type": "boolean", "default": True},
                },
                "required": ["prompt"],
            },
        },
    )
    protocol.register(
        "brain.escalate",
        heavy,
        "escalate",
        {
            "description": "Structured escalate: stuck goal + error → heavy brain repair plan",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "error": {"type": "string", "default": ""},
                    "context": {"type": "string", "default": ""},
                    "max_tokens": {"type": "integer", "default": 1400},
                },
                "required": ["goal"],
            },
        },
    )

    from advanced_shards.browser_limb import BrowserLimb
    from advanced_shards.channel_limb import ChannelLimb
    from advanced_shards.video_watch import VideoWatchLimb

    browser = BrowserLimb()
    channels = ChannelLimb()
    vwatch = VideoWatchLimb()
    protocol.register(
        "browser.status",
        browser,
        "status",
        {"description": "Playwright browser limb status", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "browser.open",
        browser,
        "open",
        {
            "description": "Open a URL in Chromium and return page text snapshot",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "wait_ms": {"type": "integer", "default": 1500},
                    "headless": {"type": "boolean", "default": True},
                },
                "required": ["url"],
            },
        },
    )
    protocol.register(
        "browser.snapshot",
        browser,
        "snapshot",
        {
            "description": "Alias of browser.open — page text snapshot",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "wait_ms": {"type": "integer", "default": 1500}},
                "required": ["url"],
            },
        },
    )
    protocol.register(
        "browser.click",
        browser,
        "click",
        {
            "description": "Open URL and click a CSS selector",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "wait_ms": {"type": "integer", "default": 1500},
                },
                "required": ["url", "selector"],
            },
        },
    )
    protocol.register(
        "browser.type",
        browser,
        "type",
        {
            "description": "Open URL and type into a CSS selector (optional Enter)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean", "default": False},
                },
                "required": ["url", "selector", "text"],
            },
        },
    )
    protocol.register(
        "browser.extract",
        browser,
        "extract",
        {
            "description": "Extract text from a CSS selector on a page",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "css": {"type": "string", "default": "body"}},
                "required": ["url"],
            },
        },
    )
    protocol.register(
        "browser.run",
        browser,
        "run",
        {
            "description": "Full browser session with a list of actions (click/fill/press/extract)",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "actions": {"type": "array"},
                    "wait_ms": {"type": "integer", "default": 1500},
                    "headless": {"type": "boolean", "default": True},
                },
                "required": ["url"],
            },
        },
    )
    protocol.register(
        "channel.status",
        channels,
        "status",
        {"description": "Mythos channel webhook / OpenClaw bridge status", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "channel.start",
        channels,
        "start",
        {
            "description": "Start local webhook inbox (POST /hook) for Discord/Telegram bridges",
            "parameters": {"type": "object", "properties": {"port": {"type": "integer"}}},
        },
    )
    protocol.register(
        "channel.stop",
        channels,
        "stop",
        {"description": "Stop channel webhook server", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "channel.inbox",
        channels,
        "inbox",
        {
            "description": "Read recent channel inbox messages",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}},
        },
    )
    protocol.register(
        "channel.send_test",
        channels,
        "send_test",
        {
            "description": "Inject a test channel message into companion/live chat",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "channel": {"type": "string", "default": "local"},
                    "sender": {"type": "string", "default": "creator"},
                },
            },
        },
    )
    protocol.register(
        "channel.configure",
        channels,
        "configure",
        {
            "description": "Configure channel port/token and optional OpenClaw gateway",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {"type": "integer"},
                    "token": {"type": "string"},
                    "post_companion": {"type": "boolean"},
                    "post_live": {"type": "boolean"},
                    "openclaw_enabled": {"type": "boolean"},
                    "openclaw_gateway": {"type": "string"},
                },
            },
        },
    )
    protocol.register(
        "video.see",
        vwatch,
        "see",
        {
            "description": "Describe one image/video frame with local VLM (moondream)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "question": {"type": "string"},
                    "model": {"type": "string", "default": ""},
                },
                "required": ["path"],
            },
        },
    )

    from advanced_shards.optimize_limb import OptimizeLimb
    from advanced_shards.packager_limb import PackagerLimb
    from advanced_shards.re_limb import ReLimb

    opt = OptimizeLimb()
    pkg = PackagerLimb()
    re_limb = ReLimb()
    protocol.register(
        "optimize.status",
        opt,
        "status",
        {"description": "Supervised self-optimize status (no silent core rewrites)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "optimize.bench",
        opt,
        "bench",
        {"description": "Benchmark Mythos hot paths (registry, find, matrix map)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "optimize.propose",
        opt,
        "propose",
        {
            "description": "Propose a supervised speed/quality patch (does not apply)",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string"}},
                "required": ["goal"],
            },
        },
    )
    protocol.register(
        "optimize.apply",
        opt,
        "apply",
        {
            "description": "Apply an approved optimize proposal to a non-protected file",
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "instruction": {"type": "string", "default": ""},
                    "confirm": {"type": "boolean", "default": True},
                },
                "required": ["proposal_id"],
            },
        },
    )
    protocol.register(
        "packager.status",
        pkg,
        "status",
        {"description": "Windows app packaging status (PyInstaller)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "packager.install",
        pkg,
        "install",
        {"description": "Install PyInstaller into Mythos venv", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "packager.package",
        pkg,
        "package",
        {
            "description": "Build a local .exe from a Python entry script",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry": {"type": "string"},
                    "name": {"type": "string", "default": ""},
                    "one_file": {"type": "boolean", "default": True},
                },
                "required": ["entry"],
            },
        },
    )
    protocol.register(
        "re.status",
        re_limb,
        "status",
        {"description": "Reverse-engineering limb status (strings/header/Ghidra)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "re.strings",
        re_limb,
        "strings",
        {
            "description": "Extract printable strings from a binary",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "min_len": {"type": "integer", "default": 4},
                    "limit": {"type": "integer", "default": 200},
                },
                "required": ["filepath"],
            },
        },
    )
    protocol.register(
        "re.header",
        re_limb,
        "header",
        {
            "description": "Quick PE/ELF/magic header for a binary",
            "parameters": {
                "type": "object",
                "properties": {"filepath": {"type": "string"}},
                "required": ["filepath"],
            },
        },
    )
    protocol.register(
        "re.analyze_source",
        re_limb,
        "analyze_source",
        {
            "description": "Summarize a source file for reverse-engineering / porting",
            "parameters": {
                "type": "object",
                "properties": {"filepath": {"type": "string"}},
                "required": ["filepath"],
            },
        },
    )
    protocol.register(
        "re.ghidra",
        re_limb,
        "ghidra",
        {
            "description": "Run Ghidra headless import/analyze if installed",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string"},
                    "project_name": {"type": "string", "default": "mythos_re"},
                },
                "required": ["filepath"],
            },
        },
    )
    protocol.register(
        "memory.status",
        mem,
        "status",
        {
            "description": "Memory system status counts",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "memory.scan",
        mem,
        "scan_report",
        {
            "description": "Scan all Mythos memory files on disk and return a report",
            "parameters": {"type": "object", "properties": {}},
        },
    )
    protocol.register(
        "memory.read_palace",
        mem,
        "read_palace",
        {
            "description": "Read memories from a palace room (origin, purpose, values, experiences)",
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    )
    protocol.register(
        "memory.record_solution",
        mem,
        "record_solution",
        {
            "description": (
                "Save a lasting solution (problem + what fixed it) to memory/SOLUTIONS.json. "
                "Use after you and the creator solve something useful so future sessions remember."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "problem": {"type": "string"},
                    "solution": {"type": "string"},
                    "tags": {"type": "string", "description": "comma-separated tags"},
                    "source": {"type": "string", "default": "live_chat"},
                },
                "required": ["problem", "solution"],
            },
        },
    )
    protocol.register(
        "memory.list_solutions",
        mem,
        "list_solutions",
        {
            "description": "List recent entries from the solutions ledger",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 12},
                    "tag": {"type": "string"},
                },
            },
        },
    )
    protocol.register(
        "drone.list",
        drone,
        "list_all",
        {"description": "List all family drones and hatchery agents with status", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "drone.status",
        drone,
        "status",
        {"description": "Same as drone.list — full drone status", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "drone.stop",
        drone,
        "stop",
        {
            "description": "Stop/cancel a drone (Drone_0) or hatchery agent by id",
            "parameters": {
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        },
    )
    protocol.register(
        "drone.stop_all",
        drone,
        "stop_all",
        {"description": "Clear task queue and stop all hatchery agents", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "drone.clear_queue",
        drone,
        "clear_queue",
        {"description": "Cancel all queued (not in-progress) family tasks", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.map",
        matrix,
        "map_matrix",
        {"description": "Map canonical Mythos home — modules and key directories", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.status",
        matrix,
        "status",
        {"description": "Quick matrix / home status (alias of a light map)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.find_drones",
        matrix,
        "find_drones_spores",
        {
            "description": "Find drone/spore/hatchery artifacts on canonical + Desktop/G/E copies",
            "parameters": {
                "type": "object",
                "properties": {"include_all_roots": {"type": "boolean", "default": True}},
            },
        },
    )
    protocol.register(
        "matrix.scan_roots",
        matrix,
        "scan_roots",
        {"description": "Compare all known Mythos copies (D canonical, Desktop backup, G, E)", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.reconcile",
        matrix,
        "reconcile",
        {"description": "Find misalignments — artifacts only on backup copies vs canonical", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.scan_copies",
        matrix,
        "scan_all_copies",
        {"description": "Scan ALL Mythos_Apex copies (D, Desktop, G, E) for drones/spores/hatchery", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.alignment",
        matrix,
        "alignment_report",
        {"description": "Report misaligned legacy artifacts between copies", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.report",
        matrix,
        "format_matrix_report",
        {"description": "Human-readable full matrix report", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.scan_desktop",
        matrix,
        "scan_desktop",
        {"description": "Scan Desktop Mythos_Apex backup for legacy drones/*.json and agents", "parameters": {"type": "object", "properties": {}}},
    )
    protocol.register(
        "matrix.import_legacy",
        matrix,
        "import_legacy",
        {
            "description": "Import legacy drone JSONs from Desktop or path into canonical hatchery",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "desktop or absolute path"},
                    "dry_run": {"type": "boolean", "description": "Preview only (default true)"},
                },
            },
        },
    )

    if not quiet:
        print("[registry] graphics + gameworld + memory limbs seated", flush=True)
