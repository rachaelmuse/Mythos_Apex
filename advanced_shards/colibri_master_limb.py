# -*- coding: utf-8 -*-
"""
Colibri Master — focused bring-up agent for GLM-5.2 MoE on this machine.

Does not give up after one failed serve. Diagnoses, repairs corrupt/incomplete
weights, frees competing ports when asked, tries progressive low-RAM profiles,
probes until /v1/models answers, and writes a ledger of every attempt.
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

COLIBRI_DIR = Path(os.environ.get("COLIBRI_DIR") or r"D:\colibri")
MODEL_DIR = Path(os.environ.get("COLI_MODEL") or r"D:\glm52_i4")
DEFAULT_PORT = int(os.environ.get("COLI_PORT") or 8010)
DOWNLOAD_BAT = COLIBRI_DIR / "_run_download.bat"
SERVE_ERR = COLIBRI_DIR / "serve_8010.err.log"
SERVE_LOG = COLIBRI_DIR / "serve_8010.log"

# Ledger lives next to Mythos state when possible
def _ledger_path() -> Path:
    for root in (
        Path(os.environ.get("MYTHOS_APEX_ROOT") or ""),
        Path(r"G:\Mythos_Codex"),
        Path(r"D:\Mythos_Apex"),
        COLIBRI_DIR,
    ):
        if root and (root / "mythos_state").is_dir() or (root / "mythos_live_brain.py").is_file():
            p = root / "mythos_state"
            p.mkdir(parents=True, exist_ok=True)
            return p / "colibri_master_ledger.json"
    COLIBRI_DIR.mkdir(parents=True, exist_ok=True)
    return COLIBRI_DIR / "colibri_master_ledger.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _py() -> str:
    apex = Path(r"D:\Mythos_Apex\.venv\Scripts\python.exe")
    if apex.is_file():
        return str(apex)
    return os.environ.get("PYTHON") or "python"


def _ram() -> dict[str, Any]:
    total = free = None
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

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        total = round(m.ullTotalPhys / (1024**3), 2)
        free = round(m.ullAvailPhys / (1024**3), 2)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "total_gb": total,
        "free_gb": free,
        "note": "Colibri dense+experts often peak near ~18GB; low-RAM mode streams experts from SSD.",
    }


def _probe(port: int = DEFAULT_PORT, timeout: float = 3.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:1500]
            return {"ok": True, "url": url, "status": getattr(resp, "status", 200), "body": body}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def _is_intentional_empty_shard(path: Path) -> bool:
    """True for empty conversion residues (e.g. out-00136 = 16-byte empty safetensors).

    Hub mirrors ship out-00136 as an empty tensor dict — MTP-only source shard with
    nothing left after the normal conversion pass. Colibri loads it fine; do NOT
    treat as corrupt or force-redownload (HF will just recreate the same 16 bytes).
    See: discuss.huggingface.co/t/out-00136-safetensors-seems-to-be-corrupted...
    """
    try:
        sz = path.stat().st_size
        if sz < 16 or sz > 64:
            return False
        with path.open("rb") as stream:
            raw = stream.read(8)
            if len(raw) != 8:
                return False
            length = int.from_bytes(raw, "little")
            if length < 2 or length > sz - 8:
                return False
            meta = json.loads(stream.read(length))
            return isinstance(meta, dict) and len(meta) == 0
    except Exception:
        return False


def _chat_smoke(port: int = DEFAULT_PORT, timeout: float = 120.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    payload = {
        "model": os.environ.get("MYTHOS_HEAVY_MODEL") or "glm-5.2-colibri",
        "messages": [{"role": "user", "content": "Reply with exactly: COLIBRI_OK"}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        text = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        return {"ok": bool(text), "text": text, "url": url}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "url": url}


def _append_ledger(entry: dict[str, Any]) -> Path:
    path = _ledger_path()
    data: dict[str, Any] = {"runs": [], "updated": _now()}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {"runs": []}
        except Exception:
            data = {"runs": []}
    runs = list(data.get("runs") or [])
    runs.append(entry)
    data["runs"] = runs[-80:]
    data["updated"] = _now()
    data["last"] = entry
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# Progressive profiles — ordered from known low-RAM attempt toward tighter / then slightly more RAM
# Prefer survival-first on ~15GB machines — higher profiles OOM mid-prefill.
PROFILES: list[dict[str, Any]] = [
    {
        "name": "survival_8_128",
        "ram": 8,
        "ctx": 128,
        "kv_slots": 1,
        "cap": 0,
        "ngen": 48,
        "policy": "experimental-fast",
    },
    {
        "name": "tighter_10_128",
        "ram": 10,
        "ctx": 128,
        "kv_slots": 1,
        "cap": 0,
        "ngen": 64,
        "policy": "experimental-fast",
    },
    {
        "name": "lowram_12_256",
        "ram": 12,
        "ctx": 256,
        "kv_slots": 1,
        "cap": 0,
        "ngen": 128,
        "policy": "experimental-fast",
    },
    {
        "name": "balanced_11_192",
        "ram": 11,
        "ctx": 192,
        "kv_slots": 1,
        "cap": 0,
        "ngen": 96,
        "policy": "balanced",
    },
]


class ColibriMasterLimb:
    """Master Colibri until it answers — diagnose → repair → try → probe → repeat."""

    def status(self) -> dict[str, Any]:
        probe = _probe()
        ledger = _ledger_path()
        last = None
        if ledger.is_file():
            try:
                last = (json.loads(ledger.read_text(encoding="utf-8")) or {}).get("last")
            except Exception:
                last = None
        return {
            "ok": True,
            "limb": "colibri_master",
            "model_dir": str(MODEL_DIR),
            "colibri_dir": str(COLIBRI_DIR),
            "port": DEFAULT_PORT,
            "serving": bool(probe.get("ok")),
            "probe": probe,
            "ram": _ram(),
            "ledger": str(ledger),
            "last_run": last,
            "tools": [
                "colibri.diagnose",
                "colibri.repair_weights",
                "colibri.free_competitors",
                "colibri.try_serve",
                "colibri.master",
            ],
            "mission": "Focus until Colibri GLM MoE answers on :8010 — do not park it.",
        }

    def diagnose(self) -> dict[str, Any]:
        """Full read-only diagnosis: shards, incompletes, RAM, last error, doctor."""
        issues: list[str] = []
        tiny: list[dict[str, Any]] = []
        empty_ok: list[dict[str, Any]] = []
        bad_headers: list[dict[str, Any]] = []
        incompletes: list[str] = []

        if not MODEL_DIR.is_dir():
            issues.append(f"model dir missing: {MODEL_DIR}")
        else:
            for p in sorted(MODEL_DIR.glob("out-*.safetensors")):
                sz = p.stat().st_size
                if _is_intentional_empty_shard(p):
                    empty_ok.append({"path": str(p), "bytes": sz, "note": "intentional empty residue"})
                    continue
                if sz < 1_000_000:
                    tiny.append({"path": str(p), "bytes": sz})
                    issues.append(f"corrupt/tiny shard: {p.name} ({sz} bytes)")
                else:
                    # validate safetensors header
                    try:
                        with p.open("rb") as stream:
                            raw = stream.read(8)
                            if len(raw) != 8:
                                raise ValueError("short header")
                            length = int.from_bytes(raw, "little")
                            if length < 2 or length > sz - 8:
                                raise ValueError(f"bad header length {length}")
                            json.loads(stream.read(length))
                    except Exception as exc:
                        bad_headers.append({"path": str(p), "error": str(exc)})
                        issues.append(f"bad safetensors header: {p.name}: {exc}")
            for p in MODEL_DIR.rglob("*.incomplete"):
                incompletes.append(str(p))
            if incompletes:
                issues.append(f"{len(incompletes)} incomplete download file(s)")

        ram = _ram()
        if ram.get("ok") and (ram.get("total_gb") or 0) < 18:
            issues.append(
                f"total RAM {ram.get('total_gb')} GB < ~18 GB peak — must use SSD streaming / low-RAM profiles"
            )
        if ram.get("ok") and (ram.get("free_gb") or 0) < 6:
            issues.append(f"only {ram.get('free_gb')} GB free — close Chrome/WSL/llama before serve")

        err_tail = ""
        if SERVE_ERR.is_file():
            try:
                err_tail = SERVE_ERR.read_text(encoding="utf-8", errors="replace")[-1200:]
            except OSError:
                pass
        if "pread" in err_tail.lower() or "input/output error" in err_tail.lower():
            issues.append("last serve failed with pread/I/O — usually corrupt shard or thrashing")

        doctor = None
        try:
            sys_path_note = ""
            import sys

            if str(COLIBRI_DIR) not in sys.path:
                sys.path.insert(0, str(COLIBRI_DIR))
            from doctor import run_doctor  # type: ignore

            engine = COLIBRI_DIR / "colibri.exe"
            doctor = run_doctor(
                MODEL_DIR,
                ram_gb=float(ram.get("free_gb") or 0),
                context=256,
                gpu_indices=[],
                engine_path=str(engine),
            )
        except Exception as exc:
            doctor = {"ok": False, "error": str(exc), "note": sys_path_note}

        probe = _probe()
        report = {
            "ok": not tiny and not bad_headers and probe.get("ok") is not False or True,
            "ready_to_serve": not tiny and not bad_headers and len(incompletes) == 0,
            "serving_now": bool(probe.get("ok")),
            "issues": issues,
            "tiny_shards": tiny,
            "empty_ok_shards": empty_ok,
            "bad_headers": bad_headers,
            "incomplete_count": len(incompletes),
            "incomplete_sample": incompletes[:8],
            "ram": ram,
            "probe": probe,
            "serve_err_tail": err_tail,
            "doctor": doctor,
            "at": _now(),
        }
        # ok means diagnosis ran; ready_to_serve is the real gate
        report["ok"] = True
        _append_ledger({"phase": "diagnose", "at": _now(), "result": {
            "ready_to_serve": report["ready_to_serve"],
            "serving_now": report["serving_now"],
            "issues": issues[:12],
        }})
        return report

    def repair_weights(self, start_download: bool = True) -> dict[str, Any]:
        """Delete tiny/corrupt shards and FORCE re-download those filenames (HF often skips otherwise)."""
        removed: list[str] = []
        errors: list[str] = []
        need_files: list[str] = []
        if not MODEL_DIR.is_dir():
            return {"ok": False, "error": f"missing model dir {MODEL_DIR}"}

        skipped_empty: list[str] = []
        for p in list(MODEL_DIR.glob("out-*.safetensors")):
            if _is_intentional_empty_shard(p):
                skipped_empty.append(p.name)
                continue
            sz = p.stat().st_size
            bad = sz < 1_000_000
            if not bad:
                try:
                    with p.open("rb") as stream:
                        raw = stream.read(8)
                        length = int.from_bytes(raw, "little")
                        if length < 2 or length > sz - 8:
                            bad = True
                        else:
                            json.loads(stream.read(length))
                except Exception:
                    bad = True
            if bad:
                need_files.append(p.name)
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError as exc:
                    errors.append(f"{p}: {exc}")

        cleared_inc = 0
        for p in list(MODEL_DIR.rglob("*.incomplete")):
            try:
                p.unlink()
                cleared_inc += 1
            except OSError as exc:
                errors.append(f"{p}: {exc}")

        # Also clear HF cache metadata that makes snapshot_download claim DONE while shard is junk
        cache = MODEL_DIR / ".cache"
        cleared_cache = 0
        if cache.is_dir() and need_files:
            for p in cache.rglob("*"):
                try:
                    if any(nf in p.name for nf in need_files) or (
                        p.is_file() and p.suffix in {".incomplete", ".lock"} and "out-" in p.name
                    ):
                        if p.is_file():
                            p.unlink()
                            cleared_cache += 1
                except OSError:
                    pass

        download: dict[str, Any] = {"started": False, "forced_files": need_files}
        if start_download and need_files:
            # Force individual file download — snapshot_download alone recreated 16-byte junk
            forced = []
            try:
                import os as _os
                _os.environ["HF_HUB_DISABLE_XET"] = "1"
                from huggingface_hub import hf_hub_download

                repo = "mateogrgic/GLM-5.2-colibri-int4-with-int8-mtp"
                for name in need_files:
                    path = hf_hub_download(
                        repo_id=repo,
                        filename=name,
                        local_dir=str(MODEL_DIR),
                        force_download=True,
                    )
                    sz = Path(path).stat().st_size if Path(path).is_file() else 0
                    forced.append({"file": name, "path": str(path), "bytes": sz, "ok": sz > 1_000_000})
                download = {
                    "started": True,
                    "mode": "hf_hub_download_force",
                    "forced_files": need_files,
                    "results": forced,
                }
            except Exception as exc:
                download = {"started": False, "error": str(exc), "forced_files": need_files}
                if DOWNLOAD_BAT.is_file():
                    try:
                        subprocess.Popen(
                            ["cmd.exe", "/c", "start", "", str(DOWNLOAD_BAT)],
                            cwd=str(COLIBRI_DIR),
                            close_fds=True,
                        )
                        download["fallback_bat"] = str(DOWNLOAD_BAT)
                    except Exception as exc2:
                        download["fallback_error"] = str(exc2)
        elif start_download and DOWNLOAD_BAT.is_file():
            try:
                subprocess.Popen(
                    ["cmd.exe", "/c", "start", "", str(DOWNLOAD_BAT)],
                    cwd=str(COLIBRI_DIR),
                    close_fds=True,
                )
                download = {"started": True, "bat": str(DOWNLOAD_BAT), "forced_files": need_files}
            except Exception as exc:
                download = {"started": False, "error": str(exc)}

        out = {
            "ok": not errors and (not need_files or any(r.get("ok") for r in (download.get("results") or [{"ok": False}])) or download.get("started")),
            "removed_corrupt": removed,
            "skipped_intentional_empty": skipped_empty,
            "cleared_incomplete": cleared_inc,
            "cleared_cache": cleared_cache,
            "download": download,
            "next": "If forced download ok, run colibri.master / colibri.try_serve",
            "at": _now(),
        }
        # Tighten ok: true if no errors and either nothing to fix or a forced file is healthy
        healthy = False
        for r in download.get("results") or []:
            if r.get("ok"):
                healthy = True
        if not need_files:
            healthy = True
        out["ok"] = (not errors) and (healthy or bool(download.get("fallback_bat")))
        _append_ledger({"phase": "repair_weights", "at": _now(), "result": out})
        return out

    def free_competitors(self, aggressive: bool = False) -> dict[str, Any]:
        """
        Stop known local AI servers that steal RAM (llama-server / old coli on 8010/8088).
        Does NOT kill Chrome/browsers unless aggressive=True (still only named processes).
        """
        # Ports we care about
        ports = [8010, 8088, 8089]
        killed: list[dict[str, Any]] = []
        notes: list[str] = []
        for port in ports:
            try:
                r = subprocess.run(
                    ["cmd.exe", "/c", f"netstat -ano | findstr :{port}"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                lines = (r.stdout or "").strip().splitlines()
                pids = set()
                for line in lines:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        pids.add(parts[-1])
                for pid in pids:
                    # Identify image name
                    info = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    name = (info.stdout or "").lower()
                    allow = any(
                        x in name
                        for x in ("llama", "colibri", "coli", "server.exe")
                    )
                    if aggressive:
                        allow = allow or "python" in name
                    if not allow:
                        notes.append(f"skip pid {pid} ({name.strip()[:80]})")
                        continue
                    subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=15)
                    killed.append({"pid": pid, "port": port, "name": name.strip()[:120]})
            except Exception as exc:
                notes.append(f"port {port}: {exc}")

        ram = _ram()
        out = {"ok": True, "killed": killed, "notes": notes, "ram_after": ram, "at": _now()}
        _append_ledger({"phase": "free_competitors", "at": _now(), "result": {"killed": killed, "ram": ram}})
        return out

    def try_serve(
        self,
        profile: str = "",
        wait_sec: int = 90,
        port: int = DEFAULT_PORT,
    ) -> dict[str, Any]:
        """Start one serve profile and wait for /v1/models."""
        # Already up?
        probe = _probe(port)
        if probe.get("ok"):
            smoke = _chat_smoke(port)
            return {
                "ok": True,
                "already_up": True,
                "probe": probe,
                "smoke": smoke,
                "at": _now(),
            }

        prof = None
        if profile:
            for p in PROFILES:
                if p["name"] == profile:
                    prof = p
                    break
        if prof is None:
            prof = PROFILES[0]

        # Preflight: refuse start if tiny shards present
        diag = self.diagnose()
        if diag.get("tiny_shards") or diag.get("bad_headers"):
            return {
                "ok": False,
                "error": "corrupt weights present — run colibri.repair_weights first",
                "diagnose": {
                    "tiny_shards": diag.get("tiny_shards"),
                    "bad_headers": diag.get("bad_headers"),
                    "issues": diag.get("issues"),
                },
                "at": _now(),
            }

        py = _py()
        coli = COLIBRI_DIR / "coli"
        cmd = [
            py,
            str(coli),
            "serve",
            "--model",
            str(MODEL_DIR),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ram",
            str(prof["ram"]),
            "--ctx",
            str(prof["ctx"]),
            "--cap",
            str(prof["cap"]),
            "--kv-slots",
            str(prof["kv_slots"]),
            "--policy",
            str(prof["policy"]),
            "--auto-tier",
            "--gpu",
            "none",
            "--topp",
            "0.85",
            "--ngen",
            str(prof["ngen"]),
        ]
        env = os.environ.copy()
        env["COLI_MODEL"] = str(MODEL_DIR)
        env["COLI_PORT"] = str(port)
        env["COLI_RAM_OVERCOMMIT"] = "1"
        env["DRAFT"] = "0"  # MTP widens expert union on low hit-rate boxes
        # Windows release is CPU-only — strip CUDA hints that make the engine abort.
        env["COLI_CUDA"] = "0"
        env.pop("COLI_CUDA_PIPE", None)
        env.pop("COLI_CUDA_MTP", None)
        env.pop("COLI_GPU", None)
        env.pop("COLI_GPUS", None)
        env["MYTHOS_HEAVY_MODEL"] = env.get("MYTHOS_HEAVY_MODEL") or "glm-5.2-colibri"

        # Clear previous err log for this attempt
        try:
            SERVE_ERR.write_text("", encoding="utf-8")
        except OSError:
            pass

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(COLIBRI_DIR),
                env=env,
                stdout=open(SERVE_LOG, "a", encoding="utf-8"),
                stderr=open(SERVE_ERR, "a", encoding="utf-8"),
            )
        except Exception as exc:
            return {"ok": False, "error": f"failed to spawn serve: {exc}", "cmd": cmd, "at": _now()}

        wait_sec = max(15, min(int(wait_sec or 90), 600))
        deadline = time.time() + wait_sec
        last = probe
        while time.time() < deadline:
            time.sleep(4)
            if proc.poll() is not None:
                err_tail = ""
                try:
                    err_tail = SERVE_ERR.read_text(encoding="utf-8", errors="replace")[-800:]
                except OSError:
                    pass
                out = {
                    "ok": False,
                    "profile": prof,
                    "error": "serve process exited before ready",
                    "exit_code": proc.returncode,
                    "serve_err_tail": err_tail,
                    "cmd": cmd,
                    "at": _now(),
                }
                _append_ledger({"phase": "try_serve", "at": _now(), "result": out})
                return out
            last = _probe(port, timeout=2.0)
            if last.get("ok"):
                smoke = _chat_smoke(port)
                out = {
                    "ok": True,
                    "profile": prof,
                    "waited_sec": round(wait_sec - (deadline - time.time()), 1),
                    "probe": last,
                    "smoke": smoke,
                    "pid": proc.pid,
                    "cmd": cmd,
                    "at": _now(),
                    "message": "COLIBRI IS UP — Mythos heavy can use :8010",
                }
                _append_ledger({"phase": "try_serve", "at": _now(), "result": {"ok": True, "profile": prof["name"]}})
                return out

        # Timeout — leave process running (may still be loading)
        out = {
            "ok": False,
            "profile": prof,
            "error": f"API not up within {wait_sec}s (model load can take longer — retry probe)",
            "probe": last,
            "pid": proc.pid,
            "still_running": proc.poll() is None,
            "at": _now(),
        }
        _append_ledger({"phase": "try_serve", "at": _now(), "result": out})
        return out

    def master(
        self,
        max_rounds: int = 6,
        wait_sec: int = 90,
        repair: bool = True,
        free_ram: bool = True,
        smoke: bool = True,
    ) -> dict[str, Any]:
        """
        Focused loop until Colibri answers (or budget exhausted).

        Order each round: diagnose → repair if needed → free competitors → try next profile → probe.
        """
        max_rounds = max(1, min(int(max_rounds or 6), 20))
        wait_sec = max(30, min(int(wait_sec or 90), 600))
        trace: list[dict[str, Any]] = []

        # Already up?
        if _probe().get("ok"):
            result = {"ok": True, "already_up": True, "smoke": _chat_smoke() if smoke else None, "at": _now()}
            _append_ledger({"phase": "master", "at": _now(), "result": result})
            return result

        if free_ram:
            fr = self.free_competitors(aggressive=False)
            trace.append({"step": "free_competitors", "result": fr})

        if repair:
            diag0 = self.diagnose()
            trace.append({"step": "diagnose_pre", "issues": diag0.get("issues")})
            if diag0.get("tiny_shards") or diag0.get("bad_headers") or (diag0.get("incomplete_count") or 0) > 0:
                rw = self.repair_weights(start_download=True)
                trace.append({"step": "repair_weights", "result": rw})
                # Give download a head start if we removed a shard
                if rw.get("removed_corrupt"):
                    time.sleep(8)

        for i in range(max_rounds):
            prof = PROFILES[i % len(PROFILES)]
            # Re-diagnose — if still corrupt after repair+wait, repair again once
            diag = self.diagnose()
            if diag.get("serving_now"):
                out = {
                    "ok": True,
                    "round": i + 1,
                    "profile": "already",
                    "smoke": _chat_smoke() if smoke else None,
                    "trace": trace,
                    "at": _now(),
                }
                _append_ledger({"phase": "master", "at": _now(), "result": {"ok": True, "round": i + 1}})
                return out
            if diag.get("tiny_shards") or diag.get("bad_headers"):
                if repair:
                    rw = self.repair_weights(start_download=True)
                    trace.append({"step": f"repair_round_{i+1}", "result": rw})
                    time.sleep(5)
                else:
                    break

            attempt = self.try_serve(profile=prof["name"], wait_sec=wait_sec)
            trace.append({"step": f"try_serve_{prof['name']}", "result": {
                "ok": attempt.get("ok"),
                "error": attempt.get("error"),
                "profile": prof["name"],
            }})
            if attempt.get("ok"):
                out = {
                    "ok": True,
                    "round": i + 1,
                    "profile": prof["name"],
                    "attempt": attempt,
                    "trace": trace,
                    "at": _now(),
                    "message": "COLIBRI MASTER SUCCESS — MoE serve is answering",
                }
                _append_ledger({"phase": "master", "at": _now(), "result": {"ok": True, "profile": prof["name"]}})
                return out

            # If process still loading, wait extra and re-probe once
            if attempt.get("still_running"):
                time.sleep(min(60, wait_sec // 2))
                if _probe().get("ok"):
                    out = {
                        "ok": True,
                        "round": i + 1,
                        "profile": prof["name"],
                        "late_ready": True,
                        "smoke": _chat_smoke() if smoke else None,
                        "trace": trace,
                        "at": _now(),
                    }
                    _append_ledger({"phase": "master", "at": _now(), "result": out})
                    return out

        final_diag = self.diagnose()
        out = {
            "ok": False,
            "error": "budget exhausted — Colibri not answering yet",
            "rounds": max_rounds,
            "trace": trace,
            "final_diagnose": {
                "issues": final_diag.get("issues"),
                "ready_to_serve": final_diag.get("ready_to_serve"),
                "ram": final_diag.get("ram"),
            },
            "next_human": [
                "out-00136 at 16 bytes is NORMAL (empty conversion residue) — do not chase a 2.5GB redownload",
                "Windows Colibri is CPU-only (no CUDA) — VRAM cannot offload dense weights",
                "Chat OOM slab mid-prefill on 15GB: close Chrome/WSL/extra Cursor windows until ≥8–10GB free, then colibri.master",
                "Honest fallback while RAM-tight: SET_HEAVY_GLM.bat (:8088 GGUF) or SET_HEAVY_CLOUD.bat; daily chat stays Ollama",
                "Hardware path: ≥32GB RAM (or Linux CUDA Colibri build) makes this MoE reliable",
            ],
            "ledger": str(_ledger_path()),
            "at": _now(),
        }
        _append_ledger({"phase": "master", "at": _now(), "result": {"ok": False, "issues": final_diag.get("issues")}})
        return out
